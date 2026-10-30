# SPDX-License-Identifier: Apache-2.0
"""firmware-zmk's glue: the CI step it reproduces, the run in ZMK's image, and the
reading of what the run left. Standard library, and PyYAML imported inside the
functions that parse a workflow.

**Where each fact comes from.** The workflow and its manifest are read here, on the
host. Everything about the firmware is the tool's: CMake's and Zephyr's exit code
and log, Kconfig's ``.config``, the build's own version headers, and the devicetree
as Zephyr's own ``edtlib`` (in the workspace, inside the container) reads it from
the build's ``edt.pickle``. This module only finds the lines and compares them.

**Tool fault or design fault.** A run that never reached Zephyr, a toolchain other
than the qualified one, or a failure this module cannot name is the tool's: it
raises, and the gate reads errored. A design fails only on a line from
:data:`DESIGN_SIGNATURES`, each one measured as a design's fault.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
from dataclasses import dataclass

#: The ZMK and Zephyr commits the workspace holds and the build reports: the
#: workspace's ``.git/HEAD`` (west checks each project out detached) and the
#: build headers' ``APP_BUILD_VERSION`` / ``BUILD_VERSION``, measured on the
#: workspace ``zmk-west --init`` made (zmk v0.3.0 is edf5c08, its Zephyr
#: dacab48). Rejected: trusting ``--mr v0.3.0`` alone (a tag can be moved, and a
#: workspace made by hand can hold anything).
ZMK_COMMIT = "edf5c0814fd3ea202e43aad2d68fd32e882a518c"
ZEPHYR_COMMIT = "dacab4875df72109b96cc8977547a0dc04875bcd"

#: What ``git describe`` writes into ``APP_BUILD_VERSION`` for that commit: the
#: hash in a clone with no tags, ``v0.3`` in the measured workspace, where the
#: v0.3 and v0.3.0 tags both stand on it. Rejected: the hash alone (the measured
#: workspace never prints it).
ZMK_DESCRIBE = (ZMK_COMMIT[:12], "v0.3", "v0.3.0")

#: The Zephyr the build must report, ``-- Zephyr version: 3.5.0``.
ZEPHYR_VERSION = "3.5.0"

#: The ZMK revision the pack builds and a CI manifest must pin. Held equal to the
#: wrapper's ``ZMK_REVISION`` by tests/test_firmware_zmk.py.
ZMK_REVISION = "v0.3.0"
ZMK_URL = "https://github.com/zmkfirmware/zmk"

#: The image the wrapper runs, held equal to its ``IMAGE`` by the same test.
IMAGE = ("zmkfirmware/zmk-build-arm@sha256:"
         "edb1c953438c6f720ddb79c3762f3972013b7fbbaf4fff3592fc869983e7afc5")

#: Wall clock for one run. Full builds measured 12-27 s and configures 3.5-8 s.
#: A timeout is errored, never a fail. Rejected: 120 s, too near a cold build on a
#: loaded laptop.
TIMEOUT_S = 600

VERSION_RE = re.compile(r"^-- Zephyr version: (\S+)", re.M)
KEYMAP_RE = re.compile(r"^-- Using keymap file: (.+)$", re.M)

#: The closed list of lines that make a failed run the design's fault. Each was
#: measured as a design fault after the Zephyr version line: gripster's CI
#: (``Invalid BOARD``), a ``&kp TABB`` typo and an undefined label (devicetree), an
#: undefined Kconfig symbol, a C syntax error in a module (``FAILED: ...obj``), and
#: the linker's two. Rejected: "the version line is there, so the design failed"
#: — a read-only cache error comes after that line too, and read as a design fail.
DESIGN_SIGNATURES = tuple(re.compile(p, re.M) for p in (
    r"Invalid BOARD",
    r"^devicetree error:",
    r"^error: Aborting due to Kconfig warnings",
    r"^FAILED: .*\.obj",
    r"region .* overflowed",
    r"undefined reference to",
))

#: Docker's own exit codes (125 the daemon, 126 and 127 the command) and a signal
#: (128 + n): the run never judged anything.
TOOL_CODES = (125, 126, 127)

#: Run inside the container after every build: everything the gates read of the
#: build and the workspace, as one JSON line, and then the build tree removed.
#: What slipped through when the host read them: each file under the temporary
#: build directory was keyed by its random name (``<tmp>/nopekit-zmk-5r_3qshm/...``)
#: and the host's ``rmtree`` of it by every name in it, so no two checks wrote the
#: same entry (tests/test_determinism). The workspace's ``.git/HEAD`` files are read
#: here too, so no entry names the host's workspace path.
#:
#: The devicetree is read with the workspace's own edtlib (``-I`` drops the
#: script's directory from sys.path, so its path is put back by hand), from a
#: configure that exited 0. The kscan nodes ZMK scans: each physical layout's
#: ``kscan``, else the chosen zmk,kscan (ZMK v0.3.0 app/src/physical_layouts.c:70-71,
#: and the zmk,physical-layout binding); with no layouts, the chosen node.
COLLECT = """
import json, os, pickle, shutil, sys
out, read_edt = sys.argv[1], sys.argv[2] == "1"
z = os.path.join(out, "b", "zephyr")
gen = os.path.join(z, "include", "generated")
def text(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""
found = {"config": text(os.path.join(z, ".config")),
         "headers": text(os.path.join(gen, "app_version.h")) + text(os.path.join(gen, "version.h")),
         "image": any(os.path.isfile(os.path.join(z, n)) for n in ("zmk.uf2", "zmk.hex")),
         "heads": {"zmk": text("/zmk/zmk/.git/HEAD").strip(),
                   "zephyr": text("/zmk/zephyr/.git/HEAD").strip()},
         "edt": None, "edt_error": ""}
if read_edt:
    try:
        sys.path.insert(0, "/zmk/zephyr/scripts/dts/python-devicetree/src")
        with open(os.path.join(z, "edt.pickle"), "rb") as fh:
            edt = pickle.load(fh)
        chosen = edt.chosen_node("zmk,kscan")
        layouts = edt.compat2okay.get("zmk,physical-layout", [])
        nodes = [(lay.props["kscan"].val if "kscan" in lay.props else chosen)
                 for lay in layouts] or [chosen]
        targets, seen = [], set()
        for node in nodes:
            if node is None or node.path in seen:
                continue
            seen.add(node.path)
            prop = node.props.get("wakeup-source")
            targets.append({"path": node.path, "present": prop is not None,
                            "val": bool(prop.val) if prop is not None else False})
        found["edt"] = targets
    except Exception as exc:
        found["edt_error"] = f"{type(exc).__name__}: {exc}"
shutil.rmtree(os.path.join(out, "b"), ignore_errors=True)
print(json.dumps(found))
"""


class StepRefused(Exception):
    """A CI shape this pack cannot reproduce: the gate is errored, never failed."""


class ToolFault(Exception):
    """The run did not judge the design."""


@dataclass(frozen=True)
class Step:
    repo: str
    wd: str
    job: str
    name: str
    argv: tuple
    zmk_config: str


@dataclass(frozen=True)
class StepFail:
    """The CI itself is wrong or unpinned: a design fail."""
    reason: str


_SHELL_META = re.compile(r"[;&|<>`\n]|\$\(")
_GW = re.compile(r"\$\{GITHUB_WORKSPACE\}|\$GITHUB_WORKSPACE\b")


def _yaml(path: str):
    import yaml  # PyYAML, inside the pack only (requires_python)
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _run_default(*levels) -> dict:
    for level in levels:
        run = ((level or {}).get("defaults") or {}).get("run") or {}
        if run:
            return run
    return {}


def read_step(path: str):
    """The workflow's one ``west build`` step as CI runs it, a :class:`StepFail`
    when CI could not build the firmware it names, or :class:`StepRefused`.

    The repository is the directory above the workflow's ``<x>/workflows/``:
    GitHub reads ``.github/workflows/``, and this pack's own control keeps its
    workflow at ``github/workflows/``, because a control's inputs are keyed by a
    walk of ``selftest/`` that skips dot-directories."""
    path = os.path.abspath(path)
    if os.path.basename(os.path.dirname(path)) != "workflows":
        raise StepRefused(f"{path} is not in a workflows/ directory, so this pack "
                          f"cannot tell which repository CI checks out")
    repo = os.path.dirname(os.path.dirname(os.path.dirname(path)))
    rel = os.path.relpath(path, repo)
    import yaml
    try:
        doc = _yaml(path)
    except FileNotFoundError:
        return StepFail(f"no workflow at {rel}: CI cannot run this workflow")
    except (yaml.YAMLError, UnicodeDecodeError) as exc:
        return StepFail(f"{rel} does not parse ({type(exc).__name__}): "
                        f"CI cannot run this workflow")
    jobs = doc.get("jobs") if isinstance(doc, dict) else None
    if not isinstance(jobs, dict) or not jobs:
        return StepFail(f"{rel} has no jobs: CI cannot run this workflow")
    found, reusable = [], []
    for jid, job in jobs.items():
        if not isinstance(job, dict):
            continue
        if "uses" in job:
            reusable.append(f"{jid} uses {job['uses']}")
        for step in job.get("steps") or ():
            run = step.get("run") if isinstance(step, dict) else None
            if isinstance(run, str) and re.search(r"\bwest\s+build\b", run):
                found.append((str(jid), job, step))
    if not found:
        if reusable:
            raise StepRefused(f"{rel}: {reusable[0]}; ZMK's reusable build-user-config "
                              f"workflow: 0.1.0 reads a self-contained step only")
        return StepFail(f"{rel} runs no `west build`: CI builds no firmware")
    if len(found) > 1:
        raise StepRefused(f"{rel} runs `west build` in {len(found)} steps "
                          f"({', '.join(repr(s.get('name', j)) for j, _, s in found)}); "
                          f"0.1.0 reads one")
    jid, job, step = found[0]
    name = str(step.get("name") or "(unnamed)")
    defaults = _run_default(job, doc)
    wd = str(step.get("working-directory") or defaults.get("working-directory") or ".")
    shell = step.get("shell") or defaults.get("shell")
    if shell not in (None, "bash"):
        raise StepRefused(f"step {name!r} runs under shell {shell!r}; 0.1.0 reads bash")
    container = job.get("container") if isinstance(job.get("container"), dict) else {}
    for where, env in (("the workflow", doc.get("env")), (f"job {jid}", job.get("env")),
                       (f"job {jid}'s container", container.get("env")),
                       (f"step {name!r}", step.get("env"))):
        if env:
            raise StepRefused(f"{where} sets env {sorted(env)}: Zephyr reads its "
                              f"environment, which this pack does not reproduce")
    run = step["run"]
    if "${{" in run or "${{" in wd:
        raise StepRefused(f"step {name!r} holds a ${{{{ }}}} expression, which only "
                          f"GitHub can evaluate")
    text = run.replace("\\\n", " ").strip()
    if _SHELL_META.search(text):
        raise StepRefused(f"step {name!r} is not one plain command: {text!r}")
    argv = shlex.split(text)
    if argv[:2] != ["west", "build"]:
        raise StepRefused(f"step {name!r} does not start `west build`: {text!r}")
    out, i, source, cmake = [], 2, None, False
    while i < len(argv):
        tok = argv[i]
        if tok == "--":
            cmake = True
        elif not cmake and (tok in ("-d", "--build-dir") or tok.startswith(("-d", "--build-dir="))):
            raise StepRefused(f"step {name!r} names its build directory ({tok}); "
                              f"the pack builds in a directory of its own")
        elif not cmake and tok in ("-s", "--source-dir"):
            source = argv[i + 1] if i + 1 < len(argv) else None
        elif not cmake and tok.startswith("--source-dir="):
            source = tok.split("=", 1)[1]
        elif cmake and tok == "-D" and i + 1 < len(argv):
            i += 1
            tok = "-D" + argv[i]
        sub = _GW.sub(lambda _m: repo, tok)
        if "$" in sub:
            raise StepRefused(f"step {name!r}: {tok!r} expands a variable other than "
                              f"GITHUB_WORKSPACE")
        out.append(sub)
        i += 1
    if source != "zmk/app":
        raise StepRefused(f"step {name!r} builds source {source!r}, not zmk/app in "
                          f"the west topdir")
    zmk_config = None
    for tok in out:
        if tok.startswith("-D") and "=" in tok:
            key, value = tok[2:].split("=", 1)
            if "/" in value and not os.path.isabs(value):
                raise StepRefused(f"step {name!r}: -D{key}={value} is relative; 0.1.0 "
                                  f"reads absolute ${{GITHUB_WORKSPACE}} paths only")
            if key == "ZMK_CONFIG":
                zmk_config = value
    if zmk_config is None:
        return StepFail(f"step {name!r} sets no -DZMK_CONFIG: ZMK builds its own "
                        f"default keymap, not this repository's")
    problem = _manifest_problem(repo, wd)
    if problem:
        return StepFail(problem)
    return Step(repo=repo, wd=wd, job=jid, name=name,
                argv=("west", "build", *out), zmk_config=zmk_config)


def _manifest_problem(repo: str, wd: str) -> str | None:
    """Why ``<wd>/config/west.yml``, the manifest CI's ``west init -l config``
    reads, does not pin ZMK v0.3.0 from zmkfirmware, or ``None``."""
    import yaml
    rel = os.path.normpath(os.path.join(wd, "config", "west.yml"))
    try:
        doc = _yaml(os.path.join(repo, rel))
    except FileNotFoundError:
        return f"no {rel}: CI's `west init -l config` has no manifest to read"
    except (yaml.YAMLError, UnicodeDecodeError) as exc:
        return f"{rel} does not parse ({type(exc).__name__})"
    manifest = doc.get("manifest") if isinstance(doc, dict) else None
    manifest = manifest if isinstance(manifest, dict) else {}
    defaults = manifest.get("defaults") or {}
    zmk = next((p for p in manifest.get("projects") or () if isinstance(p, dict)
                and p.get("name") == "zmk"), None)
    if zmk is None:
        return f"{rel} names no project zmk"
    revision = zmk.get("revision") or defaults.get("revision")
    if str(revision) != ZMK_REVISION:
        return (f"CI builds ZMK {revision or 'at no pinned revision'}, not the "
                f"{ZMK_REVISION} this pack builds: a verdict here would describe "
                f"other firmware ({rel})")
    url = zmk.get("url")
    if not url:
        remote = zmk.get("remote") or defaults.get("remote")
        base = next((r.get("url-base") for r in manifest.get("remotes") or ()
                     if isinstance(r, dict) and r.get("name") == remote), None)
        url = f"{str(base).rstrip('/')}/{zmk.get('repo-path') or 'zmk'}" if base else None
    if url is None or str(url).removesuffix(".git").rstrip("/") != ZMK_URL:
        return f"CI fetches zmk from {url}, not {ZMK_URL} ({rel})"
    return None


# --------------------------------------------------------------------------- #
# the run
# --------------------------------------------------------------------------- #
_WRAPPER = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "bin", "zmk-west")


def _identity() -> str:
    """The real path of ``zmk-west`` on PATH, once its bytes are shown to be this
    pack's ``bin/zmk-west``: the image digest, the mounts and ``--network none``
    every verdict is made with."""
    found = shutil.which("zmk-west")
    if found is None:
        raise ToolFault("zmk-west is not on PATH")
    real = os.path.realpath(found)
    with open(real, "rb") as fh, open(_WRAPPER, "rb") as ours:
        if fh.read() != ours.read():
            raise ToolFault(f"zmk-west at {real} is not this pack's bin/zmk-west; "
                            f"run --init again")
    return real


def _run(path: str, cmake_only: bool) -> dict:
    tool = _identity()
    step = read_step(path)
    if isinstance(step, StepFail):
        raise ToolFault(f"the CI step does not stand ({step.reason}); zmk.ci_step says so")
    ws = os.path.dirname(tool)
    bdir = tempfile.mkdtemp(prefix="nopekit-zmk-")
    rest = list(step.argv[2:])
    if cmake_only:
        at = rest.index("--") if "--" in rest else len(rest)
        rest.insert(at, "--cmake-only")
    result = {"cmake_only": cmake_only, "repo": step.repo, "zmk_config": step.zmk_config,
              "timeout": False, "rc": None, "log": "", "keymaps": [], "config": "",
              "headers": "", "image": False, "heads": {"zmk": "", "zephyr": ""},
              "edt": None, "edt_error": ""}
    try:
        try:
            proc = subprocess.run([tool, step.repo, bdir, "west", "build", "-d",
                                   os.path.join(bdir, "b"), *rest],
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                  text=True, errors="replace", timeout=TIMEOUT_S)
        except subprocess.TimeoutExpired:
            result["timeout"] = True
            return result
        result["rc"], raw = proc.returncode, proc.stdout
        result["keymaps"] = [m.strip() for m in KEYMAP_RE.findall(raw)]
        result["log"] = _scrub(raw, bdir, step.repo, ws)
        if proc.returncode in TOOL_CODES or proc.returncode >= 128:
            return result
        read_edt = "1" if cmake_only and proc.returncode == 0 else "0"
        got = subprocess.run([tool, step.repo, bdir, "python3", "-I", "-c", COLLECT,
                              bdir, read_edt],
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True, errors="replace", timeout=TIMEOUT_S)
        try:
            result.update(json.loads(got.stdout.strip().splitlines()[-1]))
        except (ValueError, IndexError) as exc:
            raise ToolFault(f"reading the build exited {got.returncode}: "
                            f"{_scrub(got.stdout, bdir, step.repo, ws)[-400:]}") from exc
        if result["edt_error"]:
            result["edt_error"] = _scrub(result["edt_error"], bdir, step.repo, ws)
    finally:
        try:
            os.rmdir(bdir)
        except OSError:
            shutil.rmtree(bdir, ignore_errors=True)
    return result


def _scrub(text: str, bdir: str, repo: str, ws: str) -> str:
    """No temporary directory, project path or workspace path in a log a verdict
    cites: tests/test_determinism wants the same bytes from two directories."""
    for path, name in sorted(((bdir, "<build>"), (repo, "<repo>"), (ws, "<zmk-workspace>")),
                             key=lambda p: -len(p[0])):
        text = text.replace(path, name)
    return text


def full(path: str) -> dict:
    """The CI step's build, whole: what ``zmk.build`` judges."""
    return _run(path, False)


def configure(path: str) -> dict:
    """The CI step's build with ``--cmake-only``, and the devicetree read from it:
    what ``zmk.wakeup_source`` and ``zmk.tx_power`` read, once per sweep."""
    return _run(path, True)


# --------------------------------------------------------------------------- #
# reading what the run left
# --------------------------------------------------------------------------- #
def _lines(text: str) -> list[str]:
    return [ln.strip() for ln in text.splitlines() if ln.strip()]


def _rel(line: str) -> str:
    return line.replace("<repo>/", "")


def judge(result: dict) -> tuple[bool, str]:
    """``(passed, detail)`` for one run, or :class:`ToolFault`."""
    log = result["log"]
    if result["timeout"]:
        raise ToolFault(f"zmk-west did not finish in {TIMEOUT_S} s")
    rc = result["rc"]
    if rc in TOOL_CODES or rc >= 128 or rc < 0:
        first = (_lines(log) or ["no output"])[0]
        raise ToolFault(f"zmk-west exited {rc}: {first}")
    heads = result["heads"]
    if heads != {"zmk": ZMK_COMMIT, "zephyr": ZEPHYR_COMMIT}:
        raise ToolFault(f"the workspace holds zmk {heads['zmk'][:12] or '?'} and zephyr "
                        f"{heads['zephyr'][:12] or '?'}, not {ZMK_COMMIT[:12]} and "
                        f"{ZEPHYR_COMMIT[:12]}: run zmk-west --init again")
    version = VERSION_RE.search(log)
    if version is None:
        raise ToolFault(f"the run never reached Zephyr (exit {rc}): "
                        f"{(_lines(log) or ['no output'])[-1]}")
    if version.group(1) != ZEPHYR_VERSION:
        raise ToolFault(f"Zephyr {version.group(1)} is outside the qualified "
                        f"toolchain (Zephyr {ZEPHYR_VERSION}, ZMK {ZMK_REVISION})")
    if rc != 0:
        for sig in DESIGN_SIGNATURES:
            hit = sig.search(log)
            if hit:
                line = log[log.rfind("\n", 0, hit.start()) + 1:].split("\n", 1)[0]
                return False, f"the build fails: {_rel(line.strip())}"
        last = [ln for ln in _lines(log) if ln.startswith(("CMake Error", "FATAL ERROR"))]
        raise ToolFault(f"the build exited {rc} on no line this pack knows as a design "
                        f"fault: {_rel(last[-1] if last else _lines(log)[-1])}")
    if not result["cmake_only"]:
        app = re.search(r"^#define APP_BUILD_VERSION (\S+)", result["headers"], re.M)
        zephyr = re.search(r"^#define BUILD_VERSION (\S+)", result["headers"], re.M)
        if (app is None or app.group(1) not in ZMK_DESCRIBE or zephyr is None
                or zephyr.group(1) != ZEPHYR_COMMIT[:12]):
            raise ToolFault(f"the build reports zmk {app.group(1) if app else '?'} and "
                            f"zephyr {zephyr.group(1) if zephyr else '?'}, not "
                            f"{ZMK_REVISION} ({ZMK_COMMIT[:12]}) and {ZEPHYR_COMMIT[:12]}")
        if not result["image"]:
            return False, "the build exited 0 and wrote no zmk.uf2 or zmk.hex"
    keymaps = result["keymaps"]
    if not keymaps:
        return False, "the build names no keymap (no 'Using keymap file:' line)"
    config = os.path.realpath(result["zmk_config"])
    repo = result["repo"]
    for keymap in keymaps:
        real = os.path.realpath(keymap)
        if os.path.commonpath([real, config]) != config:
            shown = os.path.relpath(keymap, repo) if keymap.startswith(repo) else keymap
            return False, (f"built keymap {shown}, not one under ZMK_CONFIG "
                           f"({os.path.relpath(result['zmk_config'], repo)})")
    done = "configured" if result["cmake_only"] else "built zmk.uf2"
    return True, f"{done} on keymap {os.path.relpath(keymaps[0], repo)}"


def kconfig(text: str) -> dict:
    """``{CONFIG_NAME: value}`` of a ``.config``; a symbol set ``n`` is a comment
    there, and absent here."""
    return dict(re.findall(r"^(CONFIG_\w+)=(.*)$", text, re.M))
