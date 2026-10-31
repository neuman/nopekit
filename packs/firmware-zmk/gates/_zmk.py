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
:data:`DESIGN_SIGNATURES`, each one measured as a design's fault, and never in a
log that holds a line from :data:`TOOL_FAULT_LINES` (the machine stopped the
compiler).
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

#: The image the wrapper runs, held equal to its ``IMAGE`` by the same test. It is
#: ``zmkfirmware/zmk-build-arm:stable`` as this machine held it when the pack was written
#: (October 2026): ``docker image inspect`` gives this digest, created 2025-10-02T17:07:35Z, Zephyr SDK 0.16.9.
#: Every verdict is bound to it through the wrapper's bytes. Rejected: the
#: floating ``:stable`` tag, which CI uses (``ci_step`` accepts it there): a later
#: push changes the compiler under a cached verdict, and nothing here would see
#: it. A CI that runs a later ``:stable`` is the limit PACK.md names.
IMAGE = ("zmkfirmware/zmk-build-arm@sha256:"
         "edb1c953438c6f720ddb79c3762f3972013b7fbbaf4fff3592fc869983e7afc5")

#: Wall clock for one run. Full builds measured 12-27 s. A timeout is errored,
#: never a fail, and the container is killed by name (the wrapper's ``--name``):
#: a timeout kills only the docker client, and the container went on building
#: into a directory the host then removed. Rejected: 120 s, too near a cold build
#: on a loaded laptop.
TIMEOUT_S = 600

VERSION_RE = re.compile(r"^-- Zephyr version: (\S+)", re.M)
KEYMAP_RE = re.compile(r"^-- Using keymap file: (.+)$", re.M)

#: The closed list of lines that make a failed run the design's fault. Each was
#: measured as a design fault after the Zephyr version line: the keyboard deck's
#: CI (``Invalid BOARD``), a ``&kp TABB`` typo and an undefined label (devicetree), an
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

#: Lines that say the machine stopped the build, whatever the design: checked
#: before :data:`DESIGN_SIGNATURES`, and errored. What slipped through without
#: them: the unmodified corne control, built with the wrapper's own docker line and
#: ``--memory=200m`` (a loaded host's OOM killer), exited 1 with ten
#: ``Killed signal terminated program cc1`` lines, each under a ``FAILED: ...obj``,
#: and read as a design FAIL on known-good upstream ZMK
#: (tests/zmk_logs/oom_killed.log). Only that line was measured; the others are
#: GCC's, the kernel's and ccache's own words for the same kind of stop, named, not
#: measured. Rejected: requiring a compiler ``error:`` under each ``FAILED``
#: object, which a linker or a ninja fault does not print.
TOOL_FAULT_LINES = tuple(re.compile(p, re.M) for p in (
    r"Killed signal terminated program",
    r"internal compiler error",
    r"No space left on device",
    r"virtual memory exhausted|Cannot allocate memory",
    r"^ccache: error:",
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
#: script's directory from sys.path, so its path is put back by hand), from the
#: build that exited 0: one run serves all three gates. (A second, ``--cmake-only``
#: run per sweep read the same ``edt.pickle`` and ``.config`` once more.) The kscan nodes ZMK scans: each physical layout's
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

#: The images a CI job may run the build in: ZMK's own, at the tag ZMK's reusable
#: workflow uses, or at this pack's digest. Rejected: the digest alone, which
#: refuses every ZMK user's CI, ZMK's template's included; the tag is the limit
#: PACK.md names, and the build's own Zephyr version and commits are checked on
#: every run. Any other image is refused: this pack cannot say what it holds.
CI_IMAGES = ("zmkfirmware/zmk-build-arm:stable", IMAGE)

#: The setup CI must run, in order, before the build, in the build's working
#: directory: without ``west init -l config`` and ``west update`` there is no
#: workspace, and without ``west zephyr-export`` CMake's ``find_package(Zephyr)``
#: fails (measured: the corne control with no ``CMAKE_PREFIX_PATH`` stops there).
#: Only these three commands of a setup step are read; whatever else it runs is not
#: reproduced (PACK.md's limits).
SETUP = (("west init -l config", re.compile(r"\bwest\s+init\s+-l\s+config/?(?![\w./-])")),
         ("west update", re.compile(r"\bwest\s+update\b")),
         ("west zephyr-export", re.compile(r"\bwest\s+zephyr-export\b")))

#: The manifest keys this pack reads, each closed. Anything else (another project,
#: ``self: import:``, a group filter, the zmk project at another path) changes
#: what CI's ``west update`` fetches, and the pack's workspace holds ZMK v0.3.0 and
#: what its own ``app/west.yml`` imports, nothing else: refused, never passed.
#: What slipped through before: only the zmk project was read, so a manifest
#: with a floating board module, one with its own ``zephyr`` and one with no
#: ``import: app/west.yml`` (CI finds no Zephyr) each passed, and the build ran on
#: firmware CI does not build.
MANIFEST_KEYS = {"version", "defaults", "remotes", "projects", "self"}
SELF_KEYS = {"path"}
ZMK_KEYS = {"name", "remote", "url", "repo-path", "revision", "import", "clone-depth"}


def _yaml(path: str):
    import yaml  # PyYAML, inside the pack only (requires_python)
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _run_defaults(doc: dict, job: dict) -> dict:
    """``defaults.run`` as GitHub resolves it: each setting from the most specific
    level that sets it. What slipped through before: the first level with any
    ``defaults.run`` was taken whole, so a job's ``shell: bash`` hid the
    workflow's ``working-directory`` and a CI GitHub runs read as failing."""
    merged: dict = {}
    for level in (doc, job):
        run = ((level or {}).get("defaults") or {}).get("run") or {}
        if not isinstance(run, dict):
            raise StepRefused(f"defaults.run is not a mapping: {run!r}")
        unknown = sorted(set(run) - {"shell", "working-directory"})
        if unknown:
            raise StepRefused(f"defaults.run sets {unknown}, which 0.1.0 does not read")
        merged.update(run)
    return merged


def _guarded(where: str, node: dict) -> None:
    for key in ("if", "continue-on-error"):
        if key in node:
            raise StepRefused(f"{where} has `{key}:`; whether it runs, or counts, is "
                              f"GitHub's to decide, and this pack cannot reproduce it")


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
        for at, step in enumerate(job.get("steps") or ()):
            run = step.get("run") if isinstance(step, dict) else None
            if isinstance(run, str) and re.search(r"\bwest\s+build\b", run):
                found.append((str(jid), job, at, step))
    if not found:
        if reusable:
            raise StepRefused(f"{rel}: {reusable[0]}; ZMK's reusable build-user-config "
                              f"workflow: 0.1.0 reads a self-contained step only")
        return StepFail(f"{rel} runs no `west build`: CI builds no firmware")
    if len(found) > 1:
        raise StepRefused(f"{rel} runs `west build` in {len(found)} steps "
                          f"({', '.join(repr(s.get('name', j)) for j, _, _, s in found)}); "
                          f"0.1.0 reads one")
    jid, job, at, step = found[0]
    name = str(step.get("name") or "(unnamed)")
    _guarded(f"job {jid}", job)
    _guarded(f"step {name!r}", step)
    defaults = _run_defaults(doc, job)
    wd = str(step.get("working-directory") or defaults.get("working-directory") or ".")
    shell = step.get("shell") or defaults.get("shell")
    if shell not in (None, "bash"):
        raise StepRefused(f"step {name!r} runs under shell {shell!r}; 0.1.0 reads bash")
    container = _container(jid, job)
    for where, env in (("the workflow", doc.get("env")), (f"job {jid}", job.get("env")),
                       (f"job {jid}'s container", (container or {}).get("env")),
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
    if container is None:
        raise StepRefused(f"job {jid} runs in no container: this pack runs ZMK's "
                          f"image, and cannot say what {job.get('runs-on')!r} holds")
    problem = (_setup_problem(jid, job, at, wd, doc, name)
               or _manifest_problem(repo, wd))
    if problem:
        return StepFail(problem)
    return Step(repo=repo, wd=wd, job=jid, name=name,
                argv=("west", "build", *out), zmk_config=zmk_config)


def _container(jid: str, job: dict) -> dict | None:
    """The job's ``container`` as a mapping, ``None`` when it has none, or
    :class:`StepRefused` for an image this pack does not run."""
    container = job.get("container")
    if container is None:
        return None
    if isinstance(container, str):
        container = {"image": container}
    if not isinstance(container, dict):
        raise StepRefused(f"job {jid}'s container is not a mapping: {container!r}")
    unknown = sorted(set(container) - {"image", "env", "credentials"})
    if unknown:
        raise StepRefused(f"job {jid}'s container sets {unknown}, which this pack "
                          f"does not reproduce")
    if container.get("image") not in CI_IMAGES:
        raise StepRefused(f"job {jid} builds in {container.get('image')!r}, not "
                          f"{CI_IMAGES[0]}: this pack cannot say what that image holds")
    return container


def _setup_problem(jid: str, job: dict, at: int, wd: str, doc: dict,
                   name: str) -> str | None:
    """Why the steps before the build do not make CI's workspace, or ``None``:
    each of :data:`SETUP` in a step before it, in that order, in its directory."""
    defaults = _run_defaults(doc, job)
    seen = []
    for step in (job.get("steps") or ())[:at]:
        run = step.get("run") if isinstance(step, dict) else None
        if not isinstance(run, str):
            continue
        hits = [(text, rx.search(run)) for text, rx in SETUP]
        hits = [(text, m.start()) for text, m in hits if m]
        if re.search(r"\bwest\s+init\b", run) and not any(t == SETUP[0][0] for t, _ in hits):
            raise StepRefused(f"job {jid} runs `west init` other than "
                              f"`{SETUP[0][0]}`: this pack reads that manifest only")
        if not hits:
            continue
        where = str(step.get("name") or "(unnamed)")
        _guarded(f"step {where!r}", step)
        swd = str(step.get("working-directory") or defaults.get("working-directory") or ".")
        if os.path.normpath(swd) != os.path.normpath(wd):
            raise StepRefused(f"step {where!r} runs in {swd}, and the build in {wd}: "
                              f"0.1.0 reads one west topdir")
        seen += [text for text, _ in sorted(hits, key=lambda h: h[1])]
    order = [text for text, _ in SETUP]
    missing = [text for text in order if text not in seen]
    if missing:
        return (f"no `{missing[0]}` before {name!r} in job {jid}: CI's `west build` "
                f"has no {'Zephyr package' if missing[0] == order[2] else 'workspace'}")
    firsts = [seen.index(text) for text in order]
    if firsts != sorted(firsts):
        return f"job {jid} runs {', '.join(f'`{t}`' for t in seen)} out of order"
    return None


def _manifest_problem(repo: str, wd: str) -> str | None:
    """Why ``<wd>/config/west.yml``, the manifest CI's ``west init -l config``
    reads, does not pin ZMK v0.3.0 from zmkfirmware and import its ``app/west.yml``,
    or ``None``. A key outside :data:`MANIFEST_KEYS` is :class:`StepRefused`."""
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
    for where, node, keys in (("manifest", manifest, MANIFEST_KEYS),
                              ("self", manifest.get("self") or {}, SELF_KEYS)):
        unknown = sorted(set(node) - keys) if isinstance(node, dict) else [repr(node)]
        if unknown:
            raise StepRefused(f"{rel}: {where} sets {unknown}, which changes what CI "
                              f"fetches; this pack's workspace holds ZMK {ZMK_REVISION} "
                              f"and its own imports only")
    defaults = manifest.get("defaults") or {}
    projects = list(manifest.get("projects") or ())
    zmk = next((p for p in projects if isinstance(p, dict) and p.get("name") == "zmk"), None)
    if zmk is None:
        return f"{rel} names no project zmk: CI fetches no zmk/app to build"
    others = [p.get("name") if isinstance(p, dict) else p for p in projects if p is not zmk]
    if others:
        raise StepRefused(f"{rel} fetches {others} beside zmk; this pack's workspace "
                          f"holds ZMK {ZMK_REVISION} and its own imports only, so a "
                          f"build here is not CI's")
    unknown = sorted(set(zmk) - ZMK_KEYS)
    if unknown:
        raise StepRefused(f"{rel}: project zmk sets {unknown}, which 0.1.0 does not read")
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
    if "import" not in zmk:
        return (f"{rel} does not import zmk's app/west.yml: CI's `west update` fetches "
                f"no Zephyr, and the build cannot find it")
    if zmk["import"] != "app/west.yml":
        raise StepRefused(f"{rel}: zmk imports {zmk['import']!r}; 0.1.0 reads "
                          f"`import: app/west.yml` only")
    return None


# --------------------------------------------------------------------------- #
# the run
# --------------------------------------------------------------------------- #
_WRAPPER = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "bin", "zmk-west")


def _identity() -> str:
    """The ``zmk-west`` on PATH, once the bytes it prints of itself
    (``zmk-west --self``) are shown to be this pack's ``bin/zmk-west``: the image
    digest, the mounts and ``--network none`` every verdict is made with.

    It is asked, not opened: an open of its real path keyed the verdict by where
    the workspace sits (``file-outside-project:<tmp>/.../zmk-west``), so a project
    that commits its cache gained an entry on each machine. The pack's own file is
    read, and keyed as the pack's."""
    found = shutil.which("zmk-west")
    if found is None:
        raise ToolFault("zmk-west is not on PATH")
    with open(_WRAPPER, "rb") as ours:
        want = ours.read()
    try:
        got = subprocess.run([found, "--self"], stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL, timeout=60).stdout
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ToolFault(f"zmk-west --self: {type(exc).__name__}") from exc
    if got != want:
        raise ToolFault("the zmk-west on PATH is not this pack's bin/zmk-west; copy "
                        "the pack's file into its workspace (references/installing.md)")
    return os.path.realpath(found)


def _read_design(step: Step) -> None:
    """Open every file of this repository that the build reads: the tree under
    ZMK_CONFIG and under each absolute ``-D`` path in the repository (a module).

    The build reads them inside the container, where no trace sees. What slipped
    through without this: the gates were keyed on the workflow, the manifest and
    the keymap's path alone, so a ``wakeup-source;`` added to the board's
    ``.dtsi`` or a ``CONFIG_BT_CTLR_TX_PWR_PLUS_8`` line removed from its ``.conf``
    left the key where it was, and the cache recorded two outcomes for identical
    inputs. A file the build reads from outside these trees (an ``#include`` of
    ``../..``) is not keyed: PACK.md's limits."""
    tops = []
    for tok in step.argv:
        if tok.startswith("-D") and "=" in tok:
            for value in tok.split("=", 1)[1].split(";"):
                value = os.path.normpath(value.strip()) if value.strip() else ""
                if os.path.isabs(value) and os.path.commonpath([value, step.repo]) == step.repo:
                    tops.append(value)
    for top in sorted(set(tops)):
        if os.path.isfile(top):
            with open(top, "rb") as fh:
                fh.read()
            continue
        if not os.path.isdir(top):
            continue
        for here, dirs, files in os.walk(top):
            dirs[:] = sorted(d for d in dirs if not d.startswith("."))
            for name in sorted(files):
                with open(os.path.join(here, name), "rb") as fh:
                    fh.read()


def _container_name(bdir: str) -> str:
    """The wrapper's ``--name`` for a run whose ``<out>`` is ``bdir``."""
    return "zmk-west-" + os.path.basename(bdir)


def _call(tool: str, step: Step, bdir: str, args: list) -> subprocess.CompletedProcess | None:
    """One run in the image, or ``None`` when it timed out: the container is then
    killed by name, before the caller removes the directory it writes into."""
    try:
        return subprocess.run([tool, step.repo, bdir, *args],
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              text=True, errors="replace", timeout=TIMEOUT_S)
    except subprocess.TimeoutExpired:
        name = _container_name(bdir)
        for argv in (["docker", "kill", name], ["docker", "wait", name]):
            try:
                subprocess.run(argv, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, timeout=60)
            except (OSError, subprocess.TimeoutExpired):
                pass
        return None


def full(path: str) -> dict:
    """The CI step's build, whole, and everything the three gates read of it: the
    log, ``.config``, the version headers, the workspace's commits and the
    devicetree. Loaded once per sweep through ``ctx.load_file``."""
    step = read_step(path)
    if isinstance(step, StepFail):
        raise ToolFault(f"the CI step does not stand ({step.reason}); zmk.ci_step says so")
    _read_design(step)
    tool = _identity()
    ws = os.path.dirname(tool)
    bdir = tempfile.mkdtemp(prefix="nopekit-zmk-")
    result = {"repo": step.repo, "zmk_config": step.zmk_config,
              "timeout": False, "rc": None, "log": "", "keymaps": [], "config": "",
              "headers": "", "image": False, "heads": {"zmk": "", "zephyr": ""},
              "edt": None, "edt_error": ""}
    try:
        proc = _call(tool, step, bdir, ["west", "build", "-d", os.path.join(bdir, "b"),
                                        *step.argv[2:]])
        if proc is None:
            result["timeout"] = True
            return result
        result["rc"], raw = proc.returncode, proc.stdout
        result["keymaps"] = [m.strip() for m in KEYMAP_RE.findall(raw)]
        result["log"] = _scrub(raw, bdir, step.repo, ws)
        if proc.returncode in TOOL_CODES or proc.returncode >= 128:
            return result
        got = _call(tool, step, bdir, ["python3", "-I", "-c", COLLECT, bdir,
                                       "1" if proc.returncode == 0 else "0"])
        if got is None:
            result["timeout"] = True
            return result
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


# --------------------------------------------------------------------------- #
# reading what the run left
# --------------------------------------------------------------------------- #
def _lines(text: str) -> list[str]:
    return [ln.strip() for ln in text.splitlines() if ln.strip()]


def _rel(line: str) -> str:
    return line.replace("<repo>/", "")


def _line_at(log: str, hit) -> str:
    return log[log.rfind("\n", 0, hit.start()) + 1:].split("\n", 1)[0].strip()


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
        for sig in TOOL_FAULT_LINES:
            hit = sig.search(log)
            if hit:
                raise ToolFault(f"the machine stopped the build, not the design: "
                                f"{_rel(_line_at(log, hit))}")
        for sig in DESIGN_SIGNATURES:
            hit = sig.search(log)
            if hit:
                return False, f"the build fails: {_rel(_line_at(log, hit))}"
        last = [ln for ln in _lines(log) if ln.startswith(("CMake Error", "FATAL ERROR"))]
        raise ToolFault(f"the build exited {rc} on no line this pack knows as a design "
                        f"fault: {_rel(last[-1] if last else _lines(log)[-1])}")
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
    return True, f"built zmk.uf2 on keymap {os.path.relpath(keymaps[0], repo)}"


def kconfig(text: str) -> dict:
    """``{CONFIG_NAME: value}`` of a ``.config``; a symbol set ``n`` is a comment
    there, and absent here."""
    return dict(re.findall(r"^(CONFIG_\w+)=(.*)$", text, re.M))
