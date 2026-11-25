# SPDX-License-Identifier: Apache-2.0
"""pcb.autoroute — route a KiCad board with Freerouting, and propose the result.

    nopekit propose pcb.autoroute -- --board hardware/main.kicad_pcb [--passes 20]
        [--rip-up] [--java PATH] [--jar PATH] [--kicad-python PATH]

A proposer, never an evaluator: Freerouting draws copper, and nothing that draws
copper is allowed to judge it. The routed board it writes is an input, and
``pcb.drc``, ``pcb.unconnected`` and ``pcb.parity`` judge it once a person adopts it
(copies it over the board, a decision). What slipped through in the pipeline
this pack was extracted from: a post-routing "gap closer" that drew its own
connections between islands the router left drew shorts, and was removed; and the
route checker that judged the router's output printed failures and exited 0.

**What it does.** Copies the board (and its project files) to a scratch directory
outside the project, exports a Specctra DSN with KiCad's ``pcbnew`` (in a Python
that has it, ``_pcbnew_steps.py``), runs Freerouting headless on it, imports the
session onto the copy, and writes two files under ``$NOPEKIT_PROPOSAL_DIR``:
``<board>.routed.kicad_pcb`` and ``<board>.autoroute.json`` (what ran, with
which version and passes, the track counts before and after, and what Freerouting
reported unrouted). Existing tracks go to the router as fixed wiring; ``--rip-up``
deletes every track and via first.

**One thread** (``-mt 1``). In the logs this pack came from, Freerouting gave the
same result for the same input within a run, and different results across runs
for reasons never isolated; one thread removes one candidate. Two runs on the
pack's baseline with its tracks removed drew the same 23 segments, written in a different
order, so the routed file's bytes differ while its copper does not. A retry of
the same input is not a new proposal: raise ``--passes``.

**It never phones home on purpose**: Freerouting's analytics are disabled
(``--usage_and_diagnostic_data.disable_analytics=true``). Freerouting 2.x still
asks GitHub whether a newer version exists when it starts; there is no flag to
stop that.

**Where the tools are.** ``--java`` (else ``$JAVA``, ``$JAVA_HOME/bin/java``, then
``java`` on PATH), ``--jar`` (else ``$FREEROUTING_JAR``: the jar from
github.com/freerouting/freerouting/releases, checked with 2.2.4), and
``--kicad-python`` (else ``$KICAD_PYTHON``, this Python, then KiCad's usual
places), which must import ``pcbnew``. A missing tool exits 2, naming what to set.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
STEPS = os.path.join(HERE, "_pcbnew_steps.py")

#: Where KiCad keeps a Python with ``pcbnew``, tried after ``$KICAD_PYTHON`` and
#: this interpreter: Linux's system Python (the distribution's KiCad package
#: installs the module there), then the macOS and Windows installers' own.
KICAD_PYTHONS = (
    "/usr/bin/python3",
    "/Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/Current/bin/python3",
    r"C:\Program Files\KiCad\9.0\bin\python.exe",
    r"C:\Program Files\KiCad\8.0\bin\python.exe",
)

#: The project files that travel with the board into the scratch copy: the same
#: set the tier-1 gates stage, so the router's rules are the project's.
STEM_FILES = (".kicad_pro", ".kicad_dru", ".kicad_sch")
DIR_FILES = ("sym-lib-table", "fp-lib-table")

#: 20 passes: the baseline board routes in 2. The larger 4-layer boards of the
#: source pipeline needed more, which ``--passes`` gives. Rejected: Freerouting's
#: own default (unbounded on a board it cannot finish).
DEFAULT_PASSES = 20

#: Wall clock for Freerouting alone. The proposer's own budget is
#: ``nopekit propose --timeout`` (600 s by default), which this must stay under.
ROUTER_TIMEOUT_S = 540


class Missing(Exception):
    """A tool the proposer needs and cannot find: exit 2, naming what to set."""


def _python_with_pcbnew(given: str | None) -> str:
    tried = []
    for cand in ([given] if given else [os.environ.get("KICAD_PYTHON"), sys.executable,
                                        *KICAD_PYTHONS]):
        if not cand or cand in tried:
            continue
        tried.append(cand)
        try:
            ok = subprocess.run([cand, "-c", "import pcbnew"], capture_output=True,
                                timeout=60, stdin=subprocess.DEVNULL).returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            ok = False
        if ok:
            return cand
    raise Missing(f"no Python here imports KiCad's pcbnew (tried {', '.join(tried)}): "
                  f"pass --kicad-python or set KICAD_PYTHON")


def _java(given: str | None) -> str:
    home = os.environ.get("JAVA_HOME")
    for cand in (given, os.environ.get("JAVA"),
                 os.path.join(home, "bin", "java") if home else None, shutil.which("java")):
        if cand and os.path.isfile(cand) and os.access(cand, os.X_OK):
            return cand
    raise Missing("no Java runtime found (Freerouting 2.x needs Java 21+): pass --java, "
                  "or set JAVA or JAVA_HOME")


def _jar(given: str | None) -> str:
    cand = given or os.environ.get("FREEROUTING_JAR")
    if cand and os.path.isfile(cand):
        return os.path.abspath(cand)
    raise Missing("no Freerouting jar: pass --jar, or set FREEROUTING_JAR to the jar from "
                  "github.com/freerouting/freerouting/releases")


def _steps(python: str, *argv: str) -> dict:
    proc = subprocess.run([python, STEPS, *argv], capture_output=True, text=True,
                          timeout=300, stdin=subprocess.DEVNULL)
    lines = [ln for ln in proc.stdout.splitlines() if ln.startswith("{")]
    if proc.returncode != 0 or not lines:
        tail = (proc.stderr or proc.stdout).strip().splitlines()[-3:]
        raise RuntimeError(f"pcbnew {argv[0]} failed (exit {proc.returncode}): "
                           + " / ".join(tail))
    return json.loads(lines[-1])


def _sha256(path: str) -> str:
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="pcb.autoroute", description=__doc__.splitlines()[0])
    ap.add_argument("--board", required=True, help="the .kicad_pcb, relative to the project")
    ap.add_argument("--passes", type=int, default=DEFAULT_PASSES)
    ap.add_argument("--rip-up", action="store_true",
                    help="delete every track and via first, and route from nothing")
    ap.add_argument("--java")
    ap.add_argument("--jar")
    ap.add_argument("--kicad-python")
    args = ap.parse_args(argv)

    out_dir = os.environ.get("NOPEKIT_PROPOSAL_DIR")
    if not out_dir:
        print("pcb.autoroute: run me with `nopekit propose pcb.autoroute -- --board ...` "
              "($NOPEKIT_PROPOSAL_DIR is unset)", file=sys.stderr)
        return 2
    board = os.path.abspath(args.board)
    if not board.endswith(".kicad_pcb") or not os.path.isfile(board):
        print(f"pcb.autoroute: no KiCad board at {args.board}", file=sys.stderr)
        return 2
    try:
        python = _python_with_pcbnew(args.kicad_python)
        java = _java(args.java)
        jar = _jar(args.jar)
    except Missing as exc:
        print(f"pcb.autoroute: {exc}", file=sys.stderr)
        return 2

    stem = os.path.basename(board)[:-len(".kicad_pcb")]
    src_dir = os.path.dirname(board)
    scratch = tempfile.mkdtemp(prefix="pcb-autoroute-")
    try:
        work = os.path.join(scratch, stem + ".kicad_pcb")
        shutil.copyfile(board, work)
        for rel in [stem + ext for ext in STEM_FILES] + list(DIR_FILES):
            if os.path.isfile(os.path.join(src_dir, rel)):
                shutil.copyfile(os.path.join(src_dir, rel), os.path.join(scratch, rel))
        dsn, ses = os.path.join(scratch, "in.dsn"), os.path.join(scratch, "out.ses")
        exported = _steps(python, "export", work, dsn, *(["--rip-up"] if args.rip_up else []))
        router = [java, "-jar", jar, "--gui.enabled=false",
                  "--usage_and_diagnostic_data.disable_analytics=true",
                  "-de", dsn, "-do", ses, "-mp", str(args.passes), "-mt", "1"]
        try:
            proc = subprocess.run(router, capture_output=True, text=True, cwd=scratch,
                                  timeout=ROUTER_TIMEOUT_S, stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            print(f"pcb.autoroute: Freerouting ran past {ROUTER_TIMEOUT_S} s and was killed",
                  file=sys.stderr)
            return 1
        log = proc.stdout + proc.stderr
        if proc.returncode != 0 or not os.path.isfile(ses) or os.path.getsize(ses) == 0:
            tail = " / ".join(log.strip().splitlines()[-3:])
            print(f"pcb.autoroute: Freerouting wrote no session (exit {proc.returncode}): "
                  f"{tail}", file=sys.stderr)
            return 1
        # Saved in the scratch directory and copied: pcbnew.SaveBoard writes a
        # .kicad_pro and a .kicad_prl beside the board it saves, and every file in
        # the proposal directory becomes an input record.
        saved = os.path.join(scratch, "routed", stem + ".routed.kicad_pcb")
        os.makedirs(os.path.dirname(saved))
        imported = _steps(python, "import", work, ses, saved)
        routed = os.path.join(out_dir, os.path.basename(saved))
        shutil.copyfile(saved, routed)
    except RuntimeError as exc:
        print(f"pcb.autoroute: {exc}", file=sys.stderr)
        return 1
    finally:
        shutil.rmtree(scratch, ignore_errors=True)

    version = re.search(r"Freerouting (v[\w.\-]+)", log)
    started = re.search(r"started with (\d+) unrouted nets", log)
    passes = re.findall(r"Auto-router pass #(\d+).*?score of [\d.]+(?: \((\d+) unrouted\))?", log)
    last_unrouted = int(passes[-1][1]) if passes and passes[-1][1] else (0 if passes else None)
    summary = {
        "proposer": "pcb.autoroute",
        "board": os.path.relpath(board, os.getcwd()),
        "board_sha256": _sha256(board),
        "routed": os.path.basename(routed),
        "freerouting": version.group(1) if version else "unknown",
        "passes_allowed": args.passes,
        "passes_run": len(passes),
        "threads": 1,
        "rip_up": args.rip_up,
        "unrouted_nets_at_start": int(started.group(1)) if started else None,
        "unrouted_after_last_pass": last_unrouted,
        "copper": {**exported, **imported},
        "judged_by": "nothing yet: pcb.drc, pcb.unconnected and pcb.parity judge the "
                     "routed board once it is adopted as the project's board",
    }
    with open(os.path.join(out_dir, stem + ".autoroute.json"), "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2, sort_keys=True)
        fh.write("\n")
    left = summary["unrouted_after_last_pass"]
    print(f"pcb.autoroute: {summary['freerouting']} routed {stem} in {len(passes)} pass(es); "
          f"Freerouting reports {left if left is not None else '?'} unrouted; "
          f"{imported['after']['tracks']} tracks, {imported['after']['vias']} vias")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
