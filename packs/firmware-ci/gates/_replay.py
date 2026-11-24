# SPDX-License-Identifier: Apache-2.0
"""firmware-ci's replay: the CI job that builds the firmware, run by act in the job's
own containers, on a copy of what the next commit would hold. Standard library.

**Where each verdict comes from.** act (nektos/act) runs the job; the toolchain in
the job's own container exits 0 or not; act reports each step's result. This module
only copies the tree in, sorts the build step's output lines against two closed
lists, and reads back the files the job wrote. A line joins a list only with a
committed log excerpt that shows it (``tests/fw_logs/``).

**Tool fault or design fault.** A step before the build that fails, act or docker
stopping, a timeout, the machine stopping the compiler and a network failure are
the tool's: errored, never a fail, because a fail the machine caused hides the
design's real state. A design fails only on a line from :data:`DESIGN_LINES`.

**What the trace sees.** Every file copied into the replay is opened here, so an
edit to any of them moves the key. act, docker and git are subprocesses, so the
gates that read the replay are opaque and re-run at every check at tier 1 or
above. The copy, the files the job wrote into it and its removal are touched only
through subprocesses (the read-back and the removal) or by writes this run made
itself: a read of a temporary path would key every entry by a random name, and no
two checks would write the same entry (tests/test_determinism).
"""
from __future__ import annotations

import base64
import functools
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile

from nopekit.modelio import load_path as _load_path

_HERE = os.path.dirname(os.path.abspath(__file__))
CI = _load_path(os.path.join(_HERE, "_ci.py"))

#: The act release the pack is measured on: v0.2.97 (2026-11-04), the newest
#: release at least two weeks old when the pack was written (v0.2.98 is
#: 2026-11-16). Checked with ``act --version`` before every replay; another version
#: is a skip that names the install line, as fdm.slice skips an OrcaSlicer it was
#: not measured on. *Rejected:* errored on another version (a crash's word for a
#: tool that ran fine; it made ``gate selftest`` not-ok, louder than "you have
#: 0.2.98"); any version (a flag's meaning or the JSON log's shape can change).
ACT_VERSION = "0.2.97"
ACT_INSTALL = ("curl -L -o act.tar.gz https://github.com/nektos/act/releases/download/"
               "v0.2.97/act_Linux_x86_64.tar.gz && tar -xzf act.tar.gz act "
               "(references/installing.md has its sha256)")

#: The runner image each GitHub label maps to, pinned by digest: catthehacker's
#: act images of 2026-08-15, the newest dated tags at least two weeks old
#: (act-24.04-20260815, act-22.04-20260815). act starts one only for a job with no
#: ``container:``; a container job runs in its own image (measured: the baseline
#: replays with these digests absent from the machine). ``--pull=false``, so a
#: missing image is errored and named, never fetched. *Rejected:* act's ``-P``
#: defaults (an interactive question on first run, and floating tags).
RUNNER_IMAGES = {
    "ubuntu-latest": "catthehacker/ubuntu@sha256:"
                     "c58e2b364da03b0c804c7d660f2ecbedf2f221a382b9baa0b344b0144780ff43",
    "ubuntu-24.04": "catthehacker/ubuntu@sha256:"
                    "c58e2b364da03b0c804c7d660f2ecbedf2f221a382b9baa0b344b0144780ff43",
    "ubuntu-22.04": "catthehacker/ubuntu@sha256:"
                    "3488f78aa97770c8d5d835f2913c1a782d3121ad3f2aafb17054fb1d554ea4e1",
}

#: Wall clock for one replay. The baseline's cold build measured 40-49 s (1208
#: objects in espressif/idf v5.5.6, seven cores); a cold Zephyr or NCS ``west
#: update`` can take many minutes. A timeout is errored, and the containers whose
#: bind source is the copy are killed: a timeout kills only act, and the job
#: container went on building into a directory the host then removed.
TIMEOUT_S = 1800

#: Never copied: git's own state, nopekit's, and every file act reads secrets,
#: variables, environment or inputs from (act's ``.actrc``, ``.secrets``, ``.vars``,
#: ``.env``, ``.input``), at any depth. The replay passes ``/dev/null`` for each
#: anyway; leaving them out also keeps them out of every job container.
EXCLUDED = frozenset({".git", ".nopekit", ".actrc", ".secrets", ".env", ".vars", ".input"})

#: The files read back from the job's workspace when the replay created or changed
#: them: images (ELF, BIN, Intel HEX, UF2) and the configuration the wake and
#: transmit rules read (Zephyr's ``.config`` and ``zephyr.dts``, ESP-IDF's
#: ``sdkconfig``). Capped: past it the replay is errored, never cut short. Never
#: from a ``CMakeFiles/`` directory: CMake's compiler probes write
#: ``CMakeDetermineCompilerABI_C.bin`` and its kin there (four on the baseline,
#: measured), which no device receives and the detail listed as images.
OUTPUT_SUFFIXES = (".elf", ".bin", ".hex", ".uf2")
OUTPUT_NAMES = (".config", "zephyr.dts", "sdkconfig")
#: The baseline wrote 7.5 MB of images (its ELF 6.4 MB, with debug information);
#: 64 MiB leaves a build with several ELF files room. *Rejected:* no cap (every
#: byte goes through one JSON line on a pipe and into memory).
OUTPUT_CAP = 64 * 1024 * 1024

#: The closed list of lines that make a failed build step the design's fault. Each
#: has a committed log excerpt (tests/fw_logs/): a C compile error under Ninja and
#: the linker's unresolved symbol and region overflow, measured on this pack's own
#: ESP-IDF app; Zephyr's unknown board, a devicetree typo and a Kconfig abort,
#: measured in ZMK v0.3.0's image on a neutral board and on upstream ZMK's corne.
#: *Rejected:* ESP-IDF's ``partitions are too small for binary``, PlatformIO's
#: ``The program size (N bytes) is greater than maximum allowed``, Arduino's
#: ``Sketch too big``, QMK's ``The firmware is too large`` and the generic GCC
#: ``file:line:col: error:`` until their excerpts are committed: a failure on one of
#: them reads errored, never failed.
DESIGN_LINES = tuple(re.compile(p, re.M) for p in (
    r"^FAILED: .*\.obj",
    r"undefined reference to",
    r"region .* overflowed",
    r"Invalid BOARD",
    r"^devicetree error:",
    r"^error: Aborting due to Kconfig warnings",
))

#: ZMK's wrong config path: a CMake *warning*, after which the build exits 0 on
#: ZMK's own default keymap (tests/fw_logs/wrong_config.log, upstream corne).
ZMK_NO_CONFIG = re.compile(r"Unable to locate ZMK config at:", re.M)

#: Lines that say the machine stopped the build, whatever the design: checked before
#: :data:`DESIGN_LINES`, and errored. Measured: the OOM killer (upstream corne under
#: ``--memory=200m``: ten ``FAILED: ...obj`` lines, each under ``Killed signal
#: terminated program cc1``, read as a design FAIL on known-good ZMK before this list
#: existed; tests/fw_logs/oom_killed.log). The rest are GCC's, the kernel's,
#: ccache's and the resolver's own words for the same kind of stop, named, not
#: measured.
TOOL_FAULT_LINES = tuple(re.compile(p, re.M) for p in (
    r"Killed signal terminated program",
    r"internal compiler error",
    r"No space left on device",
    r"virtual memory exhausted|Cannot allocate memory",
    r"^ccache: error:",
    r"Could not resolve host|Temporary failure in name resolution|Name or service not known",
    r"Connection (?:refused|timed out|reset by peer)|Network is unreachable",
    r"SSL certificate problem|TLS handshake timeout",
))

#: A build step whose process a signal stopped (act's ``exitcode '137'``: 128 + 9,
#: SIGKILL) is errored before any line is read: the OOM killer, a ``docker kill``,
#: or another replay that removed the job's container. Measured: two replays of one
#: workflow at once, and the second removed the first's container mid-build; its
#: output's last line named a source file with "error" in its name, and read as the
#: step's failure. (``_one_replay_at_a_time`` now keeps replays apart.)
#:
#: act's own duration after a step's line, ``[46.35055134s]`` or ``[900.433µs]``,
#: and CMake's after its configure and generate lines, ``-- Configuring done
#: (5.6s)`` (CMake 3.30 in espressif/idf, measured: two replays of one tree said
#: 5.6 s and 4.7 s): scrubbed, because a log a verdict cites holds no clock. The
#: order of a parallel build's lines is the scheduler's (Ninja finished
#: ``[5/1208]`` on another object in each of two replays), so the log is evidence
#: to read, not bytes to compare; ``images.json`` and ``toolchain.json`` are the
#: same bytes from two copies at different depths (measured on the baseline).
_DURATION = re.compile(r"\s*\[(?:\d+h)?(?:\d+m)?[\d.]+(?:µs|us|ms|ns|s)\]")
_CMAKE_DURATION = re.compile(r"^(-- (?:Configuring|Generating) done) \([\d.]+s\)", re.M)

_COLLECT = r"""
import base64, hashlib, json, os, sys
copy, cap = sys.argv[1], int(sys.argv[2])
before = json.load(sys.stdin)
suffixes, names = tuple(sys.argv[3].split(",")), tuple(sys.argv[4].split(","))
found, total = {}, 0
for here, dirs, files in os.walk(copy):
    dirs[:] = sorted(d for d in dirs if d not in (".git", "CMakeFiles"))
    for name in sorted(files):
        if not (name.endswith(suffixes) or name in names):
            continue
        path = os.path.join(here, name)
        rel = os.path.relpath(path, copy).replace(os.sep, "/")
        try:
            with open(path, "rb") as fh:
                data = fh.read()
        except OSError:
            continue
        if before.get(rel) == hashlib.sha256(data).hexdigest():
            continue
        total += len(data)
        if total > cap:
            print(json.dumps({"oversize": total}))
            sys.exit(0)
        found[rel] = base64.b64encode(data).decode("ascii")
print(json.dumps({"files": found}))
"""

_REMOVE = "import shutil, sys; shutil.rmtree(sys.argv[1], ignore_errors=True)"


class ToolFault(Exception):
    """The replay did not judge the design: the gate is errored."""


class WrongAct(Exception):
    """act is on PATH at a version this pack was not measured on: the gate skips."""


# --------------------------------------------------------------------------- #
# the loader
# --------------------------------------------------------------------------- #
_LOADERS: dict = {}


def loader(job: str):
    """The replay of ``job`` as a ``ctx.load_file`` loader: one callable per job, kept
    for the process, because the sweep's memo keys a load on its loader's identity
    and the three gates that read a replay must share one."""
    fn = _LOADERS.get(job)
    if fn is None:
        fn = _LOADERS[job] = functools.partial(replay, job=job)
    return fn


def act_version() -> str:
    """The version ``act --version`` prints; :class:`ToolFault` when act does not
    start, :class:`WrongAct` when it is not :data:`ACT_VERSION`."""
    try:
        proc = subprocess.run(["act", "--version"], stdin=subprocess.DEVNULL,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                              errors="replace", timeout=60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ToolFault(f"act --version did not run ({type(exc).__name__})") from exc
    m = re.search(r"act version (\S+)", proc.stdout or "")
    if proc.returncode != 0 or m is None:
        first = (proc.stdout or "").strip().splitlines()[:1]
        raise ToolFault(f"act --version exited {proc.returncode} and printed "
                        f"{(first[0] if first else '')[:80]!r}: act did not start")
    if m.group(1) != ACT_VERSION:
        raise WrongAct(f"act --version says {m.group(1)}; this pack is measured on act "
                       f"{ACT_VERSION} only: {ACT_INSTALL}")
    return m.group(1)


def replay(path: str, job: str) -> dict:
    """Job ``job`` of the workflow at ``path``, replayed by act on a copy of its
    repository, and everything the gates read of it: each step's result and
    scrubbed output, per matrix entry, and the bytes of each image and
    configuration file the job wrote into its workspace."""
    act_version()
    repo, rel = CI.locate(path)
    tmp = tempfile.mkdtemp(prefix="nopekit-fw-")
    copy, home = os.path.join(tmp, "repo"), os.path.join(tmp, "home")
    result = {"workflow": rel, "job": job, "rc": None, "timeout": False, "entries": [],
              "act": [], "files": {}, "oversize": 0, "toolchain": {}, "cleanup": ""}
    # The image the job runs in, read before act starts: a timeout returns early,
    # and the chown below must still give the job's files back.
    image = _job_image(path, job)
    try:
        before = _copy(repo, copy)
        _git_init(copy, home)
        event = os.path.join(tmp, "event.json")
        with open(event, "w", encoding="utf-8") as fh:
            json.dump({"ref": "refs/heads/main"}, fh)
        argv = ["act", "push", "-W", rel, "-C", copy, "-j", job, "--bind", "--json",
                "--concurrent-jobs", "1", "--pull=false", "--action-offline-mode",
                "--no-cache-server", "--container-daemon-socket", "-",
                "--action-cache-path",
                os.path.join(os.path.expanduser("~"), ".cache", "nopekit", "firmware-ci",
                             "actions"),
                "--secret-file", os.devnull, "--var-file", os.devnull,
                "--env-file", os.devnull, "--input-file", os.devnull, "-e", event]
        for label, ref in RUNNER_IMAGES.items():
            argv += ["-P", f"{label}={ref}"]
        env = dict(os.environ, HOME=home, XDG_CONFIG_HOME=os.path.join(home, ".config"),
                   ACT_DISABLE_VERSION_CHECK="1")
        with _one_replay_at_a_time():
            try:
                proc = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                      stderr=subprocess.PIPE, text=True, errors="replace",
                                      env=env, timeout=TIMEOUT_S)
            except subprocess.TimeoutExpired:
                result["timeout"] = True
                _kill(copy)
                return result
        result["rc"] = proc.returncode
        scrub = functools.partial(_scrub, copy=copy, tmp=tmp)
        result["entries"], act_lines = parse(proc.stdout, scrub)
        result["act"] = act_lines + [scrub(ln) for ln in proc.stderr.splitlines() if ln.strip()]
        result["toolchain"] = _toolchain(image or RUNNER_IMAGES["ubuntu-latest"])
        result["files"], result["oversize"] = _collect(copy, before)
    finally:
        result["cleanup"] = _release(tmp, image)
    return result


def _ran_in(runs_on: str) -> str:
    """The pinned runner image act starts for a job with no ``container:``: the
    first label of its ``runs-on`` this pack maps, or ``""``."""
    for label in runs_on.split():
        if label in RUNNER_IMAGES:
            return RUNNER_IMAGES[label]
    return ""


#: Where the replays of this machine queue. act names a job's container from the
#: workflow's name and the job's id alone, and removes a container of that name
#: before it starts one. What slipped through (measured while this pack was
#: written): two replays of one workflow at once, a selftest and a probe, and the
#: second removed the first's running container mid-build ("exitcode '137'", "RWLayer
#: of container ... is unexpectedly nil"). So one replay runs at a time, across
#: processes, behind an advisory lock outside every project. *Rejected:* renaming
#: the workflow in the copy (an edit to the file CI runs); a lock in the project
#: (two projects that hold one workflow name collide just the same); a name prefix
#: (act 0.2.97 has no flag for one). The limit: the lock is under ``$HOME``, so two
#: processes with different homes (a test's own home beside a person's check) can
#: still collide, and the collision reads errored by the signal rule in ``judge``,
#: never as a verdict.
LOCK = os.path.join("~", ".cache", "nopekit", "firmware-ci", "replay.lock")


class _one_replay_at_a_time:
    def __enter__(self):
        self.fh = None
        try:
            import fcntl
        except ImportError:                                  # not a POSIX host
            return self
        path = os.path.expanduser(LOCK)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.fh = open(path, "a", encoding="utf-8")
        fcntl.flock(self.fh, fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc):
        if self.fh is not None:
            self.fh.close()                                  # releases the lock
        return False


def _job_image(path: str, job: str) -> str:
    """The image the job runs in, for ``toolchain.json`` and for the chown that
    hands the copy back: its ``container:``, else the pinned runner image of its
    ``runs-on``. What slipped through the first draft: a job with no container
    named no image, so nothing gave its root-owned files back and the removal left
    them behind; and a timeout returned before the image was read."""
    try:
        wf = CI.load(path)
        body = (wf.doc or {}).get("jobs", {}).get(job) or {}
        if not isinstance(body, dict):
            return ""
        runs_on, image = CI.runner(job, body)
        return image or _ran_in(runs_on)
    except Exception:                                        # noqa: BLE001 - best effort
        return ""


# --------------------------------------------------------------------------- #
# the copy
# --------------------------------------------------------------------------- #
def _git(args: list, cwd: str, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, stdin=subprocess.DEVNULL,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
                          timeout=120)


def listing(repo: str) -> list[str]:
    """What the next commit would hold, as repo-relative paths: ``git ls-files -co
    --exclude-standard`` where the repository is in a git work tree and not ignored;
    otherwise every file under it, by a walk, with the same exclusions.

    What slipped through the design: the known-bad copies live under the
    project's ``.nopekit/out/``, which is ignored, and in pack mode under a
    temporary directory, which is in no work tree; an installed wheel holds no
    ``.git``. With ``git ls-files`` alone each listed nothing or failed, and every
    control read errored, never failed (tests/test_firmware_ci, both cases)."""
    tracked = None
    try:
        inside = _git(["rev-parse", "--is-inside-work-tree"], repo)
        if inside.returncode == 0 and inside.stdout.strip() == b"true":
            ignored = _git(["check-ignore", "-q", "."], repo).returncode == 0
            if not ignored:
                found = _git(["ls-files", "-z", "-co", "--exclude-standard"], repo)
                if found.returncode == 0:
                    tracked = [p.decode("utf-8", "surrogateescape")
                               for p in found.stdout.split(b"\x00") if p]
    except (OSError, subprocess.TimeoutExpired):
        tracked = None
    if tracked is None:
        tracked = []
        for here, dirs, files in os.walk(repo):
            dirs[:] = sorted(d for d in dirs if d not in EXCLUDED and d != "__pycache__")
            for name in sorted(files):
                rel = os.path.relpath(os.path.join(here, name), repo)
                tracked.append(rel.replace(os.sep, "/"))
    out = []
    for rel in sorted(set(tracked)):
        if any(part in EXCLUDED for part in rel.split("/")):
            continue
        out.append(rel)
    return out


def _copy(repo: str, copy: str) -> dict:
    """Copy what :func:`listing` names into ``copy``; ``{rel: sha256}`` of each file.
    Each file is opened and read here, so it is a read of every gate that loads the
    replay: a source edit moves their key. A listed directory (a submodule's
    gitlink) is copied whole, by the walk's rules."""
    before = {}
    for rel in listing(repo):
        src = os.path.join(repo, *rel.split("/"))
        if os.path.isdir(src):
            for sub in listing(src):
                before.update(_copy_one(src, os.path.join(copy, *rel.split("/")), sub,
                                        prefix=rel + "/"))
            continue
        if not os.path.isfile(src):
            continue                                          # tracked, deleted since
        before.update(_copy_one(repo, copy, rel))
    os.makedirs(copy, exist_ok=True)
    return before


def _copy_one(repo: str, copy: str, rel: str, prefix: str = "") -> dict:
    src = os.path.join(repo, *rel.split("/"))
    dst = os.path.join(copy, *rel.split("/"))
    with open(src, "rb") as fh:
        data = fh.read()
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    with open(dst, "wb") as fh:
        fh.write(data)
    if os.access(src, os.X_OK):
        os.chmod(dst, 0o755)
    return {prefix + rel: hashlib.sha256(data).hexdigest()}


#: The copy's one commit: act reads the commit, the branch and the actor from git.
#: A fixed author and date, so the commit is the same for the same tree.
_GIT_ID = {"GIT_AUTHOR_NAME": "nopekit", "GIT_AUTHOR_EMAIL": "replay@nopekit.invalid",
           "GIT_COMMITTER_NAME": "nopekit", "GIT_COMMITTER_EMAIL": "replay@nopekit.invalid",
           "GIT_AUTHOR_DATE": "2000-01-01T00:00:00Z",
           "GIT_COMMITTER_DATE": "2000-01-01T00:00:00Z"}


def _git_init(copy: str, home: str) -> None:
    os.makedirs(home, exist_ok=True)
    env = dict(os.environ, HOME=home, GIT_CONFIG_NOSYSTEM="1",
               GIT_CONFIG_GLOBAL=os.devnull, **_GIT_ID)
    for args in (["-c", "init.defaultBranch=main", "init", "-q"], ["add", "-A"],
                 ["-c", "commit.gpgsign=false", "commit", "-q", "--allow-empty",
                  "--no-verify", "-m", "nopekit replay"]):
        proc = _git(args, copy, env)
        if proc.returncode != 0:
            raise ToolFault(f"git {args[-1] if args[0] != '-c' else args[2]} in the replay's "
                            f"copy exited {proc.returncode}: "
                            f"{proc.stderr.decode('utf-8', 'replace').strip()[:200]}")


# --------------------------------------------------------------------------- #
# what the replay left
# --------------------------------------------------------------------------- #
def _scrub(text: str, *, copy: str, tmp: str) -> str:
    """No temporary directory and no clock in a line a verdict cites:
    tests/test_determinism wants the same bytes from two directories."""
    text = text.replace(copy, "<repo>").replace(tmp, "<tmp>")
    return _CMAKE_DURATION.sub(r"\1", _DURATION.sub("", text))


def parse(stdout: str, scrub=lambda s: s) -> tuple[list, list]:
    """act's ``--json`` log as ``(entries, act_lines)``.

    Each entry is one job and matrix entry: ``{"job", "matrix", "result", "steps"}``,
    each step ``{"stage", "id", "name", "result", "log", "act"}``, where ``log`` is
    the step's own output (act's ``raw_output`` lines) and ``act`` act's lines
    about it. ``act_lines`` holds act's lines about no job."""
    entries: dict = {}
    loose: list = []
    for raw in stdout.splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            row = json.loads(line) if line.startswith("{") else None
        except ValueError:
            row = None
        if not isinstance(row, dict):
            loose.append(scrub(line))
            continue
        msg = str(row.get("msg", ""))
        jid = row.get("jobID")
        if jid is None:
            loose.append(scrub(msg.rstrip("\n")))
            continue
        matrix = row.get("matrix") or {}
        key = (str(jid), json.dumps(matrix, sort_keys=True))
        entry = entries.setdefault(key, {"job": str(jid), "matrix": matrix, "result": None,
                                         "steps": {}, "act": []})
        if "jobResult" in row:
            entry["result"] = row["jobResult"]
        sid = row.get("stepID") or row.get("stepid")
        if not sid:
            entry["act"].append(scrub(msg.rstrip("\n")))
            continue
        sid = [str(s) for s in sid]
        stage = str(row.get("stage") or "")
        step = entry["steps"].setdefault((stage, tuple(sid)), {
            "stage": stage, "id": sid, "name": str(row.get("step") or ""),
            "result": None, "log": [], "act": []})
        if row.get("raw_output"):
            step["log"].append(msg)
        else:
            step["act"].append(scrub(msg.rstrip("\n")))
        if "stepResult" in row:
            step["result"] = row["stepResult"]
    out = []
    for entry in entries.values():
        steps = []
        for step in entry["steps"].values():
            steps.append({**step, "log": scrub("".join(step["log"]))})
        out.append({**entry, "steps": steps})
    return out, loose


def _collect(copy: str, before: dict) -> tuple[dict, int]:
    """``({rel: bytes}, oversize)`` of each output file the job created or changed,
    read back by a subprocess (see the module's docstring)."""
    proc = subprocess.run([sys.executable, "-I", "-c", _COLLECT, copy, str(OUTPUT_CAP),
                           ",".join(OUTPUT_SUFFIXES), ",".join(OUTPUT_NAMES)],
                          input=json.dumps(before), stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, text=True, timeout=300)
    try:
        found = json.loads(proc.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError) as exc:
        raise ToolFault(f"reading the job's files back exited {proc.returncode}: "
                        f"{proc.stderr.strip()[-200:]}") from exc
    if "oversize" in found:
        return {}, int(found["oversize"])
    return {rel: base64.b64decode(b64) for rel, b64 in found["files"].items()}, 0


def _toolchain(image: str) -> dict:
    """``{image reference: local image id}``: which bytes the job ran in."""
    proc = subprocess.run(["docker", "image", "inspect", "--format", "{{.Id}}", image],
                          stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                          stderr=subprocess.DEVNULL, text=True, timeout=60)
    return {image: proc.stdout.strip() if proc.returncode == 0 else ""}


def _kill(copy: str) -> None:
    """Stop the containers whose bind source is the copy (act's job containers)."""
    try:
        found = subprocess.run(["docker", "ps", "-q", "--filter", f"volume={copy}"],
                               stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL, text=True, timeout=60)
        for cid in found.stdout.split():
            subprocess.run(["docker", "kill", cid], stdin=subprocess.DEVNULL,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        pass


def _release(tmp: str, image: str) -> str:
    """Hand the copy back to this user and remove it. The job ran as root in its
    container, so what it wrote is root's: a ``chown`` in a container of the job's
    own image (already present, no network) gives it back first. Returns what could
    not be done, or ``""``."""
    said = ""
    if image:
        uid, gid = os.getuid(), os.getgid()
        proc = subprocess.run(["docker", "run", "--rm", "--network", "none", "--pull=never",
                               "--entrypoint", "chown", "-v", f"{tmp}:{tmp}", image,
                               "-R", f"{uid}:{gid}", tmp],
                              stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                              stderr=subprocess.PIPE, text=True, timeout=300)
        if proc.returncode != 0:
            said = f"chown in {image} exited {proc.returncode}"
    subprocess.run([sys.executable, "-I", "-c", _REMOVE, tmp], stdin=subprocess.DEVNULL,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=300)
    return said


# --------------------------------------------------------------------------- #
# the verdict
# --------------------------------------------------------------------------- #
#: A toolchain's own path before its name on a line it prints (``/opt/esp/tools/
#: .../bin/ld: region ...``): cut to the name, so the detail says what failed in a
#: line a person can read. The rest of the line is kept as the tool wrote it.
_TOOL_PATH = re.compile(r"^\S*/((?:[\w.+-]*-)?(?:ld|collect2|gcc|g\+\+|cc1|as))(?:\.\w+)?:\s*")


def _line_at(log: str, hit) -> str:
    line = log[log.rfind("\n", 0, hit.start()) + 1:].split("\n", 1)[0].strip()
    return _TOOL_PATH.sub(r"\1: ", line)


def _label(entry: dict) -> str:
    matrix = entry.get("matrix") or {}
    return f" ({', '.join(f'{k}={v}' for k, v in sorted(matrix.items()))})" if matrix else ""


def main_step(entry: dict, index: int) -> dict | None:
    return next((s for s in entry["steps"] if s["stage"] == "Main" and s["id"][:1] == [str(index)]),
                None)


def _last_words(entry: dict, step: dict | None = None) -> str:
    lines = [ln for ln in ((step or {}).get("act", []) + entry.get("act", [])) if ln.strip()]
    return lines[-1][:200] if lines else "no output"


def judge(result: dict, job, image_glob: str | None = None,
          tolerated: frozenset = frozenset()) -> tuple[bool, str]:
    """``(passed, detail)`` for the build job ``job`` (a ``_ci.Job``) in one replay,
    or :class:`ToolFault`. ``tolerated`` holds the indices of steps before the build
    that carry ``continue-on-error: true``: their failure stops nothing on GitHub."""
    if result["timeout"]:
        raise ToolFault(f"act did not finish in {TIMEOUT_S} s; the job's containers were "
                        f"stopped")
    entries = [e for e in result["entries"] if e["job"] == job.job]
    if not entries:
        words = [ln for ln in result["act"] if ln.strip()]
        raise ToolFault(f"act exited {result['rc']} before any step of job {job.job}: "
                        f"{(words[-1] if words else 'no output')[:200]}")
    if result["oversize"]:
        raise ToolFault(f"the job wrote {result['oversize']} B of images and configuration, "
                        f"past the {OUTPUT_CAP} B the replay reads back")
    fails = []
    for entry in entries:
        label = _label(entry)
        setup = next((s for s in entry["steps"] if s["id"] == ["--setup-job"]), None)
        if setup is not None and setup["result"] == "failure":
            raise ToolFault(f"act could not set up job {job.job}{label}: "
                            f"{_last_words(entry, setup)}")
        for i in range(job.index):
            step = main_step(entry, i)
            if step is not None and step["result"] == "failure" and i not in tolerated:
                raise ToolFault(f"step {step['name']!r} before the build failed in job "
                                f"{job.job}{label}: the pack cannot tell a manifest error "
                                f"from a network fault ({_last_words(entry, step)})")
        build = main_step(entry, job.index)
        if build is None or build["result"] is None:
            raise ToolFault(f"the build step {job.name!r} never ran in job {job.job}{label}: "
                            f"{_last_words(entry, build)}")
        if build["result"] == "skipped":
            raise ToolFault(f"the build step {job.name!r} was skipped in job {job.job}{label} "
                            f"(its `if:` was false under act)")
        log = build["log"]
        if build["result"] == "failure":
            said = "\n".join(build["act"])
            code = re.search(r"exitcode '(\d+)'", said)
            if code and int(code.group(1)) >= 128:
                daemon = re.search(r"Error response from daemon: [^\n]*", said)
                raise ToolFault(f"the build step's process was stopped by a signal (exit "
                                f"{code.group(1)}){': ' + daemon.group(0)[:160] if daemon else ''}")
            for sig in TOOL_FAULT_LINES:
                hit = sig.search(log)
                if hit:
                    raise ToolFault(f"the machine stopped the build, not the design: "
                                    f"{_line_at(log, hit)}")
            hit = next((m for sig in DESIGN_LINES for m in [sig.search(log)] if m), None)
            if hit is None:
                errors = [ln.strip() for ln in log.splitlines()
                          if re.search(r"error|FAILED|fatal", ln, re.I)]
                last = (errors or [ln for ln in log.splitlines() if ln.strip()]
                        or [_last_words(entry, build)])[-1]
                raise ToolFault(f"the build step failed{label} on no line this pack knows as "
                                f"a design fault: {last.strip()[:200]}")
            fails.append(f"the build fails{label}: {_line_at(log, hit)}")
        elif build["result"] == "success":
            hit = next((m for sig in DESIGN_LINES for m in [sig.search(log)] if m), None)
            if hit is not None:
                fails.append(f"the build step read success{label}, and its output holds "
                             f"{_line_at(log, hit)!r}: a failed build masked")
            elif ZMK_NO_CONFIG.search(log):
                fails.append(f"the build{label} could not find its ZMK config "
                             f"({_line_at(log, ZMK_NO_CONFIG.search(log))}): it built ZMK's "
                             f"default keymap, not this repository's")
        else:
            raise ToolFault(f"the build step ended {build['result']!r}{label}")
    if fails:
        return False, "; ".join(dict.fromkeys(fails))
    images = sorted(rel for rel in result["files"] if rel.endswith(OUTPUT_SUFFIXES))
    if image_glob and not any(matches(rel, image_glob) for rel in images):
        return False, (f"the file you flash ({image_glob}) is not what job {job.job} builds: "
                       f"it wrote {', '.join(images) or 'no image'}")
    later = []
    for step in sorted((s for s in entries[0]["steps"] if s["stage"] == "Main"
                        and s["id"][0].isdigit() and int(s["id"][0]) > job.index),
                       key=lambda s: int(s["id"][0])):
        later.append(f"{step['name']} ({step['result'] or 'not run'})")
    where = f" in {_short(job.image)}" if job.image else ""
    many = f", {len(entries)} matrix entries" if len(entries) > 1 else ""
    said = (f"job {job.job}{many}: step {job.name!r} ({job.command}) passed{where}; "
            f"wrote {', '.join(images) or 'no image'}")
    if later:
        said += f"; later steps not judged: {', '.join(later)}"
    return True, said


def _short(image: str) -> str:
    return re.sub(r"(@sha256:[0-9a-f]{12})[0-9a-f]+", r"\1", image)


def matches(rel: str, glob: str) -> bool:
    """Whether the workspace path ``rel`` matches ``glob``, a path at a time: ``*``
    and ``?`` stay inside one directory, ``**/`` crosses any number of them. What
    slipped through ``fnmatch`` (whose ``*`` matches ``/``): ``build/*.bin`` on the
    ESP-IDF baseline named the bootloader's and the partition table's ``.bin`` too,
    and the gate refused to choose."""
    out, i = [], 0
    while i < len(glob):
        if glob.startswith("**/", i):
            out.append(r"(?:[^/]*/)*")
            i += 3
        elif glob[i] == "*":
            out.append(r"[^/]*")
            i += 1
        elif glob[i] == "?":
            out.append(r"[^/]")
            i += 1
        else:
            out.append(re.escape(glob[i]))
            i += 1
    return re.fullmatch("".join(out), rel) is not None


def built_file(result: dict, glob: str, what: str) -> tuple[str, bytes]:
    """The one file the job wrote that ``glob`` names, or :class:`ToolFault`."""
    hits = sorted(rel for rel in result["files"] if matches(rel, glob))
    if not hits:
        raise ToolFault(f"{what} {glob!r} matches no file the job wrote into its workspace "
                        f"(ZMK's reusable workflow builds outside it: use the "
                        f"self-contained template)")
    if len(hits) > 1:
        raise ToolFault(f"{what} {glob!r} matches {len(hits)} files the job wrote "
                        f"({', '.join(hits)}): name one")
    return hits[0], result["files"][hits[0]]


def evidence(result: dict, job, out_path) -> list[str]:
    """The build step's scrubbed log, ``images.json`` (name, size and sha256 of each
    image the job wrote) and ``toolchain.json`` (each image reference and its local
    id), under the gate's out directory. ``out_path`` is ``ctx.out_path``."""
    paths = []
    entries = [e for e in result["entries"] if e["job"] == job.job]
    log = out_path("firmware-ci", "build", "log.txt")
    with open(log, "w", encoding="utf-8") as fh:
        for entry in entries:
            step = main_step(entry, job.index)
            fh.write(f"== job {entry['job']}{_label(entry)}, step {job.name!r}: "
                     f"{(step or {}).get('result')}\n")
            fh.write((step or {}).get("log", ""))
    paths.append(log)
    images = out_path("firmware-ci", "build", "images.json")
    with open(images, "w", encoding="utf-8") as fh:
        json.dump([{"name": rel, "size": len(data),
                    "sha256": hashlib.sha256(data).hexdigest()}
                   for rel, data in sorted(result["files"].items())
                   if rel.endswith(OUTPUT_SUFFIXES)], fh, indent=1, sort_keys=True)
        fh.write("\n")
    paths.append(images)
    chain = out_path("firmware-ci", "build", "toolchain.json")
    with open(chain, "w", encoding="utf-8") as fh:
        json.dump(result["toolchain"], fh, indent=1, sort_keys=True)
        fh.write("\n")
    paths.append(chain)
    return paths


def tolerated(wf, job) -> frozenset:
    """Indices of the steps before the build that carry ``continue-on-error: true``."""
    body = ((wf.doc or {}).get("jobs") or {}).get(job.job) or {}
    steps = (body.get("steps") or []) if isinstance(body, dict) else []
    return frozenset(i for i, s in enumerate(steps[:job.index])
                     if isinstance(s, dict) and (s.get("continue-on-error") is True or
                                                 str(s.get("continue-on-error", "")).strip()
                                                 .lower() == "true"))
