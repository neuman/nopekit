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
through subprocesses (the copy's symlinks, its snapshot, the read-back and the
removal) or by writes this run made itself: a read of a temporary path would key
every entry by a random name, and no two checks would write the same entry
(tests/test_determinism).

**One replay per sweep.** Every gate that reads the replay asks ``ctx.load_file``
for it with the same loader (:func:`loader`), and the replay asks nothing of a
path that moves between two gates (:func:`_open_lock`), so the sweep's memo
serves the first gate's replay to the rest. It reads no environment variable in
Python: act and git get this process's environment through ``env(1)``
(:func:`with_env`), with the few values the replay sets over it, and the home
that holds act's action cache and the fallback lock is the uid's in the user
database (:func:`user_home`), never ``HOME``.
"""
from __future__ import annotations

import base64
import functools
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import types

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

#: Wall clock for one replay when the project names none: the baseline's cold
#: build measured 40-49 s (1208 objects in espressif/idf v5.5.6, seven cores), and
#: a cold Zephyr or NCS ``west update`` can take many minutes. A project sets its
#: own in ``firmware_ci_timeout_s``, as pcb-kicad's ``pcb_drc_timeout_s``. A
#: timeout is errored, and the containers whose bind source is the copy are
#: removed: a timeout kills only act, and the job container went on building into
#: a directory the host then removed. What slipped through a fixed 1800 s (holdout
#: review, a handheld's upstream workflow): a job that clones ESP-IDF and runs its
#: ``install.sh`` for each of five matrix entries cannot finish in it, and no key
#: could say so. *Rejected:* a timeout scaled from the job's step count (a step
#: count says nothing of a ``west update``); no timeout (a hung job holds the
#: user's replay lock for every other replay on the machine).
TIMEOUT_S = 1800

#: Never copied: git's own state, nopekit's, and every file act reads secrets,
#: variables, environment or inputs from (act's ``.actrc``, ``.secrets``, ``.vars``,
#: ``.env``, ``.input``), at any depth. The replay passes ``/dev/null`` for each
#: anyway; leaving them out also keeps them out of every job container.
EXCLUDED = frozenset({".git", ".nopekit", ".actrc", ".secrets", ".env", ".vars", ".input"})

#: The files the read-back looks at in the job's workspace when the job wrote
#: them: images (ELF, BIN, Intel HEX, UF2) and the configuration the wake and
#: transmit rules read (Zephyr's ``.config`` and ``zephyr.dts``, ESP-IDF's
#: ``sdkconfig``). Every configuration file comes back whole. Every image is
#: listed by name, size and sha256 (``images``), and only the images that
#: ``firmware_image`` or ``firmware_elf`` name come back whole (``files``): the
#: gates read no other image's bytes (``OUTPUT_CAP``). Never from a
#: ``CMakeFiles/`` directory: CMake's compiler probes write
#: ``CMakeDetermineCompilerABI_C.bin`` and its kin there (four on the baseline,
#: measured), which no device receives and the detail listed as images.
#:
#: "Wrote" is read from each file's inode, change time, modification time and size
#: (``lstat``) against a snapshot taken after the copy and before act starts. What
#: slipped through the first rule, "its bytes differ from the copy's" (review of
#: F1): a repository that tracks its release image at the path the job writes,
#: rebuilt byte for byte by a reproducible build (the baseline's own
#: ``CONFIG_APP_REPRODUCIBLE_BUILD``), was left out, and ``fw.build`` failed a sound
#: design with "it wrote no image". A tracked image the job never touched keeps
#: its snapshot, so it still never stands in for one the job built. *Rejected:*
#: always reading back ``firmware_image`` (a committed image the job never wrote
#: would then pass as the job's); the content rule (above).
OUTPUT_SUFFIXES = (".elf", ".bin", ".hex", ".uf2")
OUTPUT_NAMES = (".config", "zephyr.dts", "sdkconfig")
#: ESP-IDF's build record, which every configure step writes into its build
#: directory: its ``config_file`` names the sdkconfig that build resolved. The
#: read-back reads that file whether or not the job rewrote it, into ``configs``.
#: What slipped through "the files the job wrote" alone (general-packs F2,
#: measured with espressif/idf v5.5.6): ESP-IDF rewrites an sdkconfig only when
#: the configure step changes it, so a project that tracks a current sdkconfig
#: got none back, and ``fw.tx_power`` would have skipped every such build. The
#: record names the file, so an sdkconfig of an app the job did not build is never
#: read as its config. *Rejected:* reading back every ``sdkconfig`` (a tracked one
#: the job never configured would stand in for the build's); a second build to
#: write it (the replay is CI's own build, run once).
BUILD_RECORDS = ("project_description.json",)
#: The cap on the bytes that come back whole: the images ``firmware_image`` and
#: ``firmware_elf`` name, and the configuration files. Past it the replay is
#: errored, never cut short, and the reason names the remedy (a glob that names
#: one file). Sized on what the gates read: the baseline's pair is 6.9 MB (its ELF
#: 6.4 MB, with debug information), and an NCS sysbuild's app pair 23.4 MB (its
#: ``zephyr.elf`` 19.7 MB and ``zephyr.signed.hex`` 3.7 MB, measured in the
#: holdout review). What slipped through a cap on every image the job wrote
#: (holdout review, an NCS watch): that sysbuild wrote 284 MB of images
#: (mcuboot, ipc_radio and the app, each with its ``zephyr_pre*.elf``, hex and
#: bin), and a sound build read errored. *Rejected:* no cap (the bytes go through
#: one JSON line on a pipe, and the sweep's memo holds them until the last gate
#: has read them); the old cap over every image (above); writing the bytes to a
#: file the gates open (a read of a temporary path keys every entry by a random
#: name, the module's docstring).
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

#: apt's words for a package its lists do not hold. GitHub's hosted runner image
#: ships populated apt lists; act's runner images (catthehacker's act-24.04 and
#: act-22.04, ``RUNNER_IMAGES``) ship them empty. So a step that runs ``sudo
#: apt-get install`` with no ``apt-get update`` before it works on GitHub and
#: fails in a replay. Measured: tests/fw_logs/act_apt_empty_lists.jsonl (``E:
#: Unable to locate package gcc-arm-none-eabi`` in the pinned act-24.04 image).
#: What slipped through before (holdout review, a handheld's upstream workflow):
#: the reason said only that the pack cannot tell a manifest error from a network
#: fault, and named no cause. The replay states this difference; it does not
#: hide it. *Rejected:* running ``apt-get update`` in the runner before the job's
#: steps (an edit to the job CI runs, and it installs today's packages, not the
#: ones GitHub's image lists held); a runner image built here with populated lists
#: (its digest moves every day, so no two machines replay one image).
APT_NO_LISTS = re.compile(r"^E: Unable to locate package |^E: Package '?\S+?'? has no "
                          r"installation candidate", re.M)
APT_UPDATE = re.compile(r"\bapt(?:-get)?\s+(?:-\S+\s+)*update\b")

#: Lines in a build step that read success which say the replay could not run a
#: part of it: docker with no daemon (the replay mounts no docker socket into
#: jobs, ``--container-daemon-socket -``), and a script the copy does not hold.
#: Checked before the image the job wrote: a build that ran through a script
#: that ignores its commands' status built nothing here, and its missing image is
#: the replay's gap, never the design's fault. Measured:
#: tests/fw_logs/act_script_docker.jsonl (``python3 scripts/build.py``, which
#: runs ``docker build`` and another script through ``subprocess.run`` with no
#: check, in the pinned act-24.04 image: docker's ``failed to connect to the
#: docker API`` and Python's ``can't open file``). Named, not measured: the docker
#: CLI's older ``Cannot connect to the Docker daemon``, a shell's ``docker: not
#: found``, and a shell's ``No such file or directory`` for a script. What slipped
#: through before (holdout review, a console's upstream workflow): such a step
#: read success, ``fw.ci_job`` passed it, and the missing image would have read as
#: a design FAIL.
REPLAY_GAP_LINES = tuple(re.compile(p, re.M) for p in (
    r"failed to connect to the docker API|Cannot connect to the Docker daemon",
    r"(?:^|\s)docker: (?:command )?not found",
    r"^python[\d.]*: can't open file ",
    r"^(?:/bin/|/usr/bin/)?(?:ba)?sh: (?:line \d+: )?\S+: No such file or directory",
))

#: The lines a failed step's own output names its cause with, first in the log
#: wins: CMake's error, apt's ``E:`` line, a compiler's or tool's ``error:``, and
#: a ``FATAL ERROR``. What slipped through quoting the last error-like line
#: (holdout review, an NCS watch): a west or CMake configure failure ends with
#: west's ``FATAL ERROR: command exited with status 1``, and its cause sits many
#: lines above it, in the ``CMake Error`` line; and a failed step before the
#: build was quoted by act's own last line (``Job failed``), not its own output.
CAUSE_LINE = re.compile(r"CMake Error|^E: |(?:^|[\s:])(?:fatal )?error:|FATAL[ _]ERROR",
                        re.M | re.I)

#: CMake's location header, ``CMake Error at <file>:<line> (<command>):`` or
#: ``CMake Error in <file>:``. It says where the error was raised, not what
#: failed: the message is the indented lines under it, each indented two spaces
#: or more and wrapped at 80 columns by CMake's formatter, across blank lines, up
#: to the first line that is not blank and not indented (``Call Stack (most
#: recent call first):``, ``-- Configuring incomplete``, the next error). Measured:
#: tests/fw_logs/act_esp_cmake_missing_file.jsonl, ESP-IDF v5.5.6's
#: ``SDKCONFIG_DEFAULTS '<repo>/sdkconfig.release' does not exist.`` over three
#: lines under ``CMake Error at /opt/esp/idf/tools/cmake/project.cmake:683
#: (message):``; and tests/fw_logs/cmake_include_missing.log, CMake 3.30.2 in the
#: same image on ``include(nosuchfile)`` from a neutral two-line project:
#: ``include could not find requested file:``, a blank line, then the file,
#: indented four spaces, then the call stack. What slipped through quoting the
#: header (holdout review round 2, an NCS watch): the reason read ``CMake Error
#: at <repo>/zephyr/cmake/modules/kconfig.cmake:318 (message):``, a line in a
#: Zephyr module, and the missing file CMake named on the next line was only in
#: log.txt. And then (review of J3): the message stopped at its first blank
#: line, so the include error's reason never named the file. The reason quotes
#: the message joined into one line, then the location in brackets. *Rejected:*
#: the first indented line alone (CMake wraps, so it was ``SDKCONFIG_DEFAULTS``,
#: one word of the message); the header alone (above); the first paragraph alone
#: (above); every indented line to the end of the log (the call stack and the
#: next error join it: the stack's header is not indented, so it ends the
#: message).
_CMAKE_AT = re.compile(r"CMake Error (?:at|in) (.+?)(?: \([^()]*\))?:\s*$")

#: An action's own words for its failure: GitHub's ``::error`` workflow command,
#: which act logs as its own line about the step (``❗  ::error::<message>``) and
#: GitHub shows as the run's annotation. Measured:
#: tests/fw_logs/act_esp_composite_cmake.jsonl, a composite action whose step
#: ends ``echo "::error::idf.py build failed"``. ``::error file=x,line=n::m``
#: carries its message after the second ``::``.
_ANNOTATION = re.compile(r"::error(?:\s[^:]*)?::(.*\S)")

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

#: The snapshot (``snap``) and the read-back (``read``), in a subprocess (see the
#: module's docstring). A symlink is read only when it resolves inside the copy:
#: one the repository tracks may name a path on this machine that the runner does
#: not have. In ``read``, an image whose path matches none of the ``keep``
#: patterns (``firmware_image`` and ``firmware_elf``, as regular expressions) is
#: hashed here, a block at a time, and only its name, size and sha256 come back.
_COLLECT = r"""
import base64, hashlib, json, os, re, sys
mode, copy, cap = sys.argv[1], sys.argv[2], int(sys.argv[3])
suffixes, names = tuple(sys.argv[4].split(",")), tuple(sys.argv[5].split(","))
records = tuple(sys.argv[6].split(","))
keep = [re.compile(p) for p in json.loads(sys.argv[7])]
before = json.load(sys.stdin) if mode == "read" else {}
root = os.path.realpath(copy)
inside = root + os.sep
stats, found, configs, named, images, total = {}, {}, {}, [], [], 0

def take(path):
    global total
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError:
        return None
    total += len(data)
    if total > cap:
        print(json.dumps({"oversize": total}))
        sys.exit(0)
    return base64.b64encode(data).decode("ascii")

def listed(path, rel):
    digest, size = hashlib.sha256(), 0
    try:
        with open(path, "rb") as fh:
            for block in iter(lambda: fh.read(1 << 20), b""):
                digest.update(block)
                size += len(block)
    except OSError:
        return False
    images.append({"name": rel, "size": size, "sha256": digest.hexdigest()})
    return True

for here, dirs, files in os.walk(copy):
    dirs[:] = sorted(d for d in dirs if d not in (".git", "CMakeFiles"))
    for name in sorted(files):
        if not (name.endswith(suffixes) or name in names or name in records):
            continue
        path = os.path.join(here, name)
        rel = os.path.relpath(path, copy).replace(os.sep, "/")
        try:
            st = os.lstat(path)
        except OSError:
            continue
        stamp = [st.st_ino, st.st_ctime_ns, st.st_mtime_ns, st.st_size]
        if mode == "snap":
            stats[rel] = stamp
            continue
        if before.get(rel) == stamp:
            continue
        if os.path.islink(path) and not os.path.realpath(path).startswith(inside):
            continue
        if name in records:
            try:
                with open(path, encoding="utf-8") as fh:
                    said = json.load(fh).get("config_file")
            except (OSError, ValueError, AttributeError):
                continue
            if isinstance(said, str) and said:
                named.append(said)
            continue
        if name.endswith(suffixes):
            if not listed(path, rel) or not any(p.fullmatch(rel) for p in keep):
                continue
        data = take(path)
        if data is not None:
            found[rel] = data
for said in named:
    real = os.path.realpath(said)
    if not real.startswith(inside) or not os.path.isfile(real):
        continue
    rel = os.path.relpath(real, root).replace(os.sep, "/")
    if rel in found or rel in configs:
        continue
    data = take(real)
    if data is not None:
        configs[rel] = data
print(json.dumps({"stats": stats} if mode == "snap" else
                 {"files": found, "configs": configs, "images": images}))
"""

#: The copy's symlinks, made in a subprocess: ``os.symlink`` here would be a read
#: of what each link names, a path under a random temporary directory, and no two
#: checks would key alike (the module's docstring).
_LINK = r"""
import json, os, sys
for target, dst in json.load(sys.stdin):
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    os.symlink(target, dst)
"""

_REMOVE = "import shutil, sys; shutil.rmtree(sys.argv[1], ignore_errors=True)"


class ToolFault(Exception):
    """The replay did not judge the design: the gate is errored."""


class WrongAct(Exception):
    """act is on PATH at a version this pack was not measured on: the gate skips."""


# --------------------------------------------------------------------------- #
# the loader
# --------------------------------------------------------------------------- #
def loader(job: str, keep: tuple = (), matrix: dict | None = None,
           timeout_s: float | None = None):
    """The replay of ``job`` as a ``ctx.load_file`` loader, the same loader for the
    same replay in every gate of a sweep: a method of :func:`_replay_named` bound
    to the interned string that names the replay (the job, the globs whose images
    come back whole, the matrix entry and the timeout).

    The sweep's memo keys a load on its loader's identity, and a bound method on
    the identities of its object and its function (``gates.load_file``). An
    interned string is one object for one value while anything holds it, and the
    memo's entry holds it through the loader, so every gate that asks for the
    same replay gets the first one's. What slipped through a module-level dict of
    ``functools.partial`` loaders (general-packs F1): it was a memo the spine
    cannot empty (``nopekit doctor``'s memos row named it, and
    ``tests/test_doctor`` holds every bundled gate to none). *Rejected:* an
    ``lru_cache`` around a factory (the spine empties it before every gate, so
    each gate got a new loader and its own replay); a new partial per call (the
    same, by identity)."""
    name = json.dumps({"job": job, "keep": sorted(set(keep)), "matrix": dict(matrix or {}),
                       "timeout_s": timeout_s}, sort_keys=True)
    return types.MethodType(_replay_named, sys.intern(name))


def _replay_named(name: str, path: str) -> dict:
    """:func:`replay` of the workflow at ``path`` as ``name`` (:func:`loader`) says."""
    said = json.loads(name)
    return replay(path, said["job"], keep=tuple(said["keep"]), matrix=said["matrix"],
                  timeout_s=said["timeout_s"])


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


def replay(path: str, job: str, *, keep: tuple = (), matrix: dict | None = None,
           timeout_s: float | None = None) -> dict:
    """Job ``job`` of the workflow at ``path``, replayed by act on a copy of its
    repository, and everything the gates read of it: each step's result and
    scrubbed output, per matrix entry; the name, size and sha256 of each image
    the job wrote into its workspace (``images``); the bytes of each image a
    ``keep`` glob names and of each configuration file the job wrote
    (``files``); and of each sdkconfig an ESP-IDF build record names that the
    job left as it was (``configs``, ``BUILD_RECORDS``). ``matrix`` names one
    entry of a matrix job (act's ``--matrix``, one per key); ``timeout_s`` is the
    wall clock (``TIMEOUT_S`` when ``None``)."""
    act_version()
    repo, rel = CI.locate(path)
    limit = float(timeout_s) if timeout_s is not None else float(TIMEOUT_S)
    tmp = tempfile.mkdtemp(prefix="nopekit-fw-")
    copy, home = os.path.join(tmp, "repo"), os.path.join(tmp, "home")
    result = {"workflow": rel, "job": job, "matrix": dict(matrix or {}), "rc": None,
              "timeout": False, "timeout_s": limit, "entries": [], "act": [], "files": {},
              "configs": {}, "images": [], "oversize": 0, "toolchain": {}, "cleanup": ""}
    # The image the job runs in, read before act starts: a timeout returns early,
    # and the chown below must still give the job's files back.
    image = _job_image(path, job)
    left = ""
    try:
        _copy(repo, copy)
        _git_init(copy, home)
        before = _snapshot(copy)
        event = os.path.join(tmp, "event.json")
        with open(event, "w", encoding="utf-8") as fh:
            json.dump({"ref": "refs/heads/main"}, fh)
        argv = ["act", "push", "-W", rel, "-C", copy, "-j", job, "--bind", "--json",
                "--concurrent-jobs", "1", "--pull=false", "--action-offline-mode",
                "--no-cache-server", "--container-daemon-socket", "-", "--rm",
                *action_cache(),
                "--secret-file", os.devnull, "--var-file", os.devnull,
                "--env-file", os.devnull, "--input-file", os.devnull, "-e", event]
        for label, ref in RUNNER_IMAGES.items():
            argv += ["-P", f"{label}={ref}"]
        for key, value in sorted((matrix or {}).items()):
            argv += ["--matrix", f"{key}:{matrix_word(value)}"]
        argv = with_env({"HOME": home, "XDG_CONFIG_HOME": os.path.join(home, ".config"),
                         "ACT_DISABLE_VERSION_CHECK": "1"}, argv)
        proc = None
        with _one_replay_at_a_time({"repo": repo, "workflow": rel, "job": job}):
            try:
                proc = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                      stderr=subprocess.PIPE, text=True, errors="replace",
                                      timeout=limit)
                out, err = proc.stdout, proc.stderr
            except subprocess.TimeoutExpired as exc:
                # act's output up to the stop, parsed as any other run's: the
                # step that was still running is in it. What slipped through
                # returning here with nothing (review of the holdout fixes): a
                # timeout, the error with the least to go on, wrote an empty
                # log.txt. On POSIX ``subprocess.run`` puts what it had read
                # into the exception, as bytes even under ``text=True``.
                result["timeout"] = True
                out, err = _decoded(exc.stdout), _decoded(exc.stderr)
            finally:
                # Inside the lock, on every outcome: the next replay of this
                # user's must not meet this one's job container.
                left = _remove_containers(copy)
        scrub = functools.partial(_scrub, copy=copy, tmp=tmp)
        result["entries"], act_lines = parse(out, scrub)
        result["act"] = act_lines + [scrub(ln) for ln in err.splitlines() if ln.strip()]
        if proc is None:
            return result
        result["rc"] = proc.returncode
        result["toolchain"] = _toolchain(image or RUNNER_IMAGES["ubuntu-latest"])
        (result["files"], result["configs"], result["images"],
         result["oversize"]) = _collect(copy, before, keep)
    finally:
        result["cleanup"] = "; ".join(s for s in (left, _release(tmp, image)) if s)
    return result


def matrix_word(value) -> str:
    """A matrix value as act's ``--matrix key:value`` compares it: a YAML boolean
    as ``true`` or ``false``, anything else as its text (measured with act 0.2.97:
    ``--matrix n:2`` selects the entry whose ``n`` is the integer 2)."""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def with_env(overrides: dict, argv: list) -> list:
    """``argv`` run through ``env(1)`` with ``overrides`` set over the environment
    this process was started with.

    The child gets every variable it would get with no ``env=`` at all (the
    ``PATH`` act and git were found on, ``DOCKER_HOST``), and ``overrides`` on top.
    What slipped through ``dict(os.environ, ...)`` (general-packs F1): a bundled
    gate reading the environment in Python, which no verdict key holds, and
    ``nopekit doctor``'s env-reads row named it (``tests/test_doctor`` holds every
    bundled gate to none). pcb-kicad hands kicad-cli the environment the same way,
    by not reading it. *Rejected:* a bare environment as the OpenFOAM packs build
    for their tools (it drops ``DOCKER_HOST`` and the ``PATH`` a person put act
    on); ``os.get_exec_path()`` for ``PATH`` alone (an environment read the
    doctor's scan does not see, which is worse than one it does)."""
    return ["env", *(f"{key}={value}" for key, value in overrides.items()), *argv]


def user_home() -> str:
    """This user's home directory as the user database gives it for the uid
    (``pwd``), or ``""`` where the uid has no entry or the host no ``pwd``.

    Never ``HOME``. What slipped through ``os.path.expanduser("~")`` here
    (review of the holdout fixes): it reads ``HOME`` inside ``posixpath``, so a
    replay that the module's docstring said read no environment variable read
    one (measured: ``env:HOME`` among a traced ``fw.build``'s opaque channels),
    and the doctor's scan, which looks for ``environ`` and ``getenv`` by name,
    did not see it. The uid is what the replay lock is keyed by
    (:func:`lock_path`), so the action cache and the fallback lock are the
    user's, whatever ``HOME`` says, as the lock is. *Rejected:* ``HOME`` (above);
    ``$HOME`` expanded by a shell between the replay and act (a third process
    for one path, and the read moves out of sight, not away)."""
    try:
        import pwd
    except ImportError:                                      # not a POSIX host
        return ""
    try:
        return pwd.getpwuid(os.getuid()).pw_dir or ""
    except KeyError:                                         # a uid with no entry
        return ""


#: Where act keeps the actions it fetched, under :func:`user_home`: once
#: fetched, reused offline (``--action-offline-mode``). Where the uid has no
#: home in the user database, act gets no ``--action-cache-path`` and caches
#: under the HOME the replay gives it, which is removed with the copy: each
#: replay fetches its actions again. *Rejected:* the lock's directory under
#: ``/tmp`` (a boot clears it, and a directory there that another user made
#: first would hand act the actions it runs).
ACTION_CACHE = (".cache", "nopekit", "firmware-ci", "actions")


def action_cache() -> list:
    """act's ``--action-cache-path`` and its value, or ``[]`` (:data:`ACTION_CACHE`)."""
    home = user_home()
    return ["--action-cache-path", os.path.join(home, *ACTION_CACHE)] if home else []


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
#: (act 0.2.97 has no flag for one).
#:
#: **The lock is the user's, not the home's**: ``<LOCK_ROOT>/nopekit-<uid>/``
#: ``firmware-ci-replay.lock``, the system's temp root keyed by the uid, so every
#: process of this user on this machine queues on one lock, whatever ``HOME`` or
#: ``TMPDIR`` says. What slipped through F1 and F2: the lock was
#: ``~/.cache/nopekit/firmware-ci/replay.lock``, so a test's temporary home
#: beside a person's check, or two test runs, took two locks, and the collision
#: above came back (measured on the merged branch: one replay's container removed
#: mid-build, the other read "RWLayer ... unexpectedly nil"). *Rejected:*
#: ``tempfile.gettempdir()`` (it reads ``TMPDIR``, which a test run sets to its
#: own directory: two runs, two locks); ``$XDG_RUNTIME_DIR`` (an environment
#: variable too: a login session sets it, and a process started outside one, by
#: cron, a system service or ``env -i``, has none, so two processes of this uid
#: can read two values, or none, and take two locks); one lock for the whole
#: machine (a file one user creates, another cannot open). The directory is made
#: ``0700`` and used only while it is a directory this uid owns, never a link:
#: ``/tmp`` is everyone's, and a path another user made first is not a lock of
#: ours. Then, or where the temp root cannot be written, the lock falls back to
#: ``HOME_LOCK`` under :func:`user_home`, the old place, now read by the uid too;
#: where the uid has no home there either, the replay is errored and names both
#: places. The limits: there, and between two users of one
#: Docker daemon, two replays can still collide, and the collision reads errored
#: by the signal rule in ``judge``, never as a verdict. The lock waits as long as
#: the replay ahead of it runs; each replay's own ``TIMEOUT_S`` bounds that.
#:
#: **A wait is said.** The lock is tried without blocking first. The replay that
#: holds it writes its pid, its repository, its workflow and its job into the lock
#: file (:func:`_claim`), and one that has to wait prints, on stderr, who holds
#: it and that it is waiting, before it blocks. What slipped through a bare
#: blocking ``flock`` (holdout review rounds 1 and 2, a watch's check): the check
#: printed nothing while it queued behind two other projects' replays, and
#: ``fw.build`` took 501 s for a build of about 330 s. *Rejected:* a timeout on
#: the wait (the replay ahead of it is bounded by its own timeout, and giving up
#: would read errored on a sound design); the holder read by name in this process
#: (an ``open`` for reading is a read the gate's key would hold, and the holder's
#: pid moves each run: a subprocess reads it, as the copy's other paths are read);
#: ``ctx.log`` (the replay runs as the sweep memo's loader, which no context
#: reaches, and a loader that held one would not be the same loader in every gate,
#: :func:`loader`).
LOCK_ROOT = "/tmp"
LOCK_NAME = "firmware-ci-replay.lock"
HOME_LOCK = (".cache", "nopekit", "firmware-ci", "replay.lock")


def lock_path(root: str | None = None) -> str:
    """The replay lock of this user on this machine, under ``root`` (default
    :data:`LOCK_ROOT`): it reads the uid and no environment variable."""
    return os.path.join(root or LOCK_ROOT, f"nopekit-{os.getuid()}", LOCK_NAME)


def _open_lock() -> tuple[int, str]:
    """``(descriptor, path)`` of the lock file: the user's own (:func:`lock_path`)
    when its directory is a directory this uid owns, else :data:`HOME_LOCK` under
    :func:`user_home`; :class:`ToolFault` where the uid has no home. The
    descriptor is write-only: the trace reads a write as no input.

    **It asks nothing of a path by name.** Every ``os.stat`` and ``os.lstat`` a
    replay makes is a path the sweep's memo signs, and a hit needs each to stat
    the same at the next gate (``gates.load_file``). What slipped through
    ``os.makedirs`` and ``os.lstat`` here (holdout review, a watch's check:
    ``fw.build`` 332 s, ``fw.image_fits`` 404 s, ``fw.sleep_wake`` 272 s and
    ``fw.tx_power`` 250 s, four replays where the docstring promised one):
    ``makedirs`` asks whether ``/tmp`` exists, the memo signed ``/tmp``, and
    ``/tmp`` changes whenever any process makes or removes a file in it, the
    next gate's own controls included. So every reader missed and replayed again.
    Now ``os.mkdir`` makes each directory (it asks nothing first), and the
    directory's owner is read from a descriptor (``os.fstat``), never by name.
    *Rejected:* signing only the repository's files in the memo (the memo is the
    spine's, and a sound rule there); a per-sweep memo of the pack's own (the
    spine cannot empty it)."""
    flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT
    path = lock_path()
    try:
        if _own_dir(os.path.dirname(path)):
            return os.open(path, flags | getattr(os, "O_NOFOLLOW", 0), 0o600), path
    except OSError:
        pass
    home = user_home()
    if not home:
        raise ToolFault(f"the replay lock has no place: {os.path.dirname(path)} is not a "
                        f"directory this user owns, and uid {os.getuid()} has no home in the "
                        f"user database")
    path = os.path.join(home, *HOME_LOCK)
    _make_dirs(os.path.dirname(path))
    return os.open(path, flags, 0o600), path


def _own_dir(path: str) -> bool:
    """Whether ``path`` is, or now is, a directory this uid owns and not a link:
    made with ``os.mkdir``, read through a descriptor opened with ``O_NOFOLLOW``."""
    try:
        os.mkdir(path, 0o700)
    except FileExistsError:
        pass
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) |
                 getattr(os, "O_NOFOLLOW", 0))
    try:
        st = os.fstat(fd)
    finally:
        os.close(fd)
    return stat.S_ISDIR(st.st_mode) and st.st_uid == os.getuid()


def _make_dirs(path: str) -> None:
    """``path`` and each directory above it, made with ``os.mkdir`` from the top:
    ``os.makedirs`` asks whether each exists first (:func:`_open_lock`)."""
    chain, here = [], os.path.abspath(path)
    while here and os.path.dirname(here) != here:
        chain.append(here)
        here = os.path.dirname(here)
    for at in reversed(chain):
        try:
            os.mkdir(at, 0o700)
        except OSError:
            pass                    # there already; or not ours to make: open says


#: Reads the lock file's holder in a subprocess, its path on stdin (see
#: ``LOCK_ROOT``'s comment): a path in the argv would be a read of the gate.
_HOLDER = ("import sys\n"
           "try:\n"
           "    sys.stdout.write(open(sys.stdin.read(), encoding='utf-8', "
           "errors='replace').read(4096))\n"
           "except OSError:\n"
           "    pass\n")


def _claim(fd: int, holder: dict) -> None:
    """Write this replay's pid and ``holder`` (its repository, workflow and job)
    into the lock file it now holds, over what the last holder wrote."""
    os.ftruncate(fd, 0)
    os.write(fd, (json.dumps({"pid": os.getpid(), **holder}, sort_keys=True) + "\n")
             .encode("utf-8"))


def _holder(path: str) -> dict:
    """What the lock file's holder wrote of itself (:func:`_claim`), or ``{}``."""
    try:
        proc = subprocess.run([sys.executable, "-I", "-c", _HOLDER], input=path,
                              stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                              timeout=30)
        said = json.loads(proc.stdout or "{}")
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return {}
    return said if isinstance(said, dict) else {}


def waiting_words(path: str, holder: dict) -> str:
    """The line a replay prints on stderr before it waits for the lock."""
    if holder.get("pid"):
        who = f"pid {holder['pid']} holds it"
        if holder.get("repo"):
            who += f", replaying {holder['repo']}"
            if holder.get("job"):
                who += f" (job {holder['job']})"
    else:
        who = "another process holds it and has not written who it is"
    return (f"firmware-ci: waiting for the replay lock {path}: {who}; each replay ends "
            f"within its own firmware_ci_timeout_s")


class _one_replay_at_a_time:
    """One replay at a time for this user (``LOCK_ROOT``'s comment): tried without
    blocking, and a wait is said on stderr with who holds the lock. ``holder``
    is what this replay writes of itself into the lock once it holds it."""

    def __init__(self, holder: dict | None = None):
        self.holder = dict(holder or {})

    def __enter__(self):
        self.fd = None
        try:
            import fcntl
        except ImportError:                                  # not a POSIX host
            return self
        self.fd, path = _open_lock()
        try:
            try:
                fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                print(waiting_words(path, _holder(path)), file=sys.stderr, flush=True)
                fcntl.flock(self.fd, fcntl.LOCK_EX)
            _claim(self.fd, self.holder)
        except BaseException:
            os.close(self.fd)
            self.fd = None
            raise
        return self

    def __exit__(self, *exc):
        if self.fd is not None:
            os.close(self.fd)                                # releases the lock
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
    """``git args`` in ``cwd``, with ``env`` set over this process's environment
    (:func:`with_env`)."""
    argv = ["git", *args]
    return subprocess.run(with_env(env, argv) if env else argv, cwd=cwd,
                          stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, timeout=120)


def listing(repo: str) -> list[str]:
    """What the next commit would hold, as repo-relative paths: ``git ls-files -co
    --exclude-standard`` where the repository is in a git work tree and not ignored;
    otherwise every file under it, by a walk, with the same exclusions.

    Tracked files win over ignore rules: ``-c`` lists every file in the index,
    one that a ``.gitignore`` matches and ``git add -f`` added too, and
    ``--exclude-standard`` drops only untracked files (``-o``). So an ignored
    file that is on disk and not tracked is not in the replay, as it is not in
    the next commit; the copy's own commit holds what this lists
    (:func:`_git_init`).

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
            # A symlink to a directory is listed as the link, as git tracks it; the
            # walk never follows it.
            names = files + [d for d in dirs if os.path.islink(os.path.join(here, d))]
            for name in sorted(names):
                rel = os.path.relpath(os.path.join(here, name), repo)
                tracked.append(rel.replace(os.sep, "/"))
    out = []
    for rel in sorted(set(p.rstrip("/") for p in tracked)):
        if any(part in EXCLUDED for part in rel.split("/")):
            continue
        out.append(rel)
    return out


def _copy(repo: str, copy: str) -> dict:
    """Copy what :func:`listing` names into ``copy``: ``{rel: sha256}`` of each
    file, ``{rel: "-> <target>"}`` of each symlink. Each file is opened and read
    here, so it is a read of every gate that loads the replay: a source edit moves
    their key. A listed directory (a submodule's gitlink) is copied whole, by the
    walk's rules. A symlink is made again as the same link, never followed.

    What slipped through ``os.path.isdir`` alone (review of F1): it follows a
    link, so a tracked ``ext -> ../outside`` was copied whole as a directory, from
    outside the repository. A GitHub checkout holds the link itself, dangling on
    the runner, so the replay built on files CI never sees, and copied a tree of
    any size on every check."""
    copied, links = {}, []
    _copy_into(repo, copy, "", copied, links)
    os.makedirs(copy, exist_ok=True)
    if links:
        proc = subprocess.run([sys.executable, "-I", "-c", _LINK],
                              input=json.dumps(links), stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True, timeout=120)
        if proc.returncode != 0:
            raise ToolFault(f"making the copy's symlinks exited {proc.returncode}: "
                            f"{proc.stderr.strip()[-200:]}")
    return copied


def _copy_into(repo: str, copy: str, prefix: str, copied: dict, links: list) -> None:
    for rel in listing(repo):
        src = os.path.join(repo, *rel.split("/"))
        dst = os.path.join(copy, *rel.split("/"))
        if os.path.islink(src):
            target = os.readlink(src)
            links.append([target, dst])
            copied[prefix + rel] = f"-> {target}"
            continue
        if os.path.isdir(src):
            _copy_into(src, dst, prefix + rel + "/", copied, links)
            continue
        if not os.path.isfile(src):
            continue                                          # tracked, deleted since
        copied.update(_copy_one(repo, copy, rel, prefix))


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
    """The copy's one commit, of every file the copy holds: ``git add --force``,
    so a file the repository tracks although a ``.gitignore`` matches it (one
    added with ``git add -f``) is tracked in the copy too, as it is in GitHub's
    checkout. What slipped through ``git add -A`` (holdout review round 2, an NCS
    watch whose upstream force-adds a board config its ``.gitignore`` matches):
    the file was in the copy and out of its commit, so ``git ls-files`` in the job
    did not list it, and a step that cleans or lists the work tree through git
    (``git clean -xdf``, ``git stash -u``, a build that globs ``git ls-files``)
    saw another tree than CI's. The copy holds only what :func:`listing` named, so
    ``--force`` adds nothing else. *Rejected:* each copied path named to ``git
    add`` (the same set, and an argv as long as the tree)."""
    os.mkdir(home)
    env = dict(HOME=home, GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, **_GIT_ID)
    for args in (["-c", "init.defaultBranch=main", "init", "-q"], ["add", "--all", "--force"],
                 ["-c", "commit.gpgsign=false", "commit", "-q", "--allow-empty",
                  "--no-verify", "-m", "nopekit replay"]):
        proc = _git(args, copy, env)
        if proc.returncode != 0:
            verb = next(a for a in args if not a.startswith("-") and "=" not in a)
            raise ToolFault(f"git {verb} in the replay's copy exited {proc.returncode}: "
                            f"{proc.stderr.decode('utf-8', 'replace').strip()[:200]}")


# --------------------------------------------------------------------------- #
# what the replay left
# --------------------------------------------------------------------------- #
def _scrub(text: str, *, copy: str, tmp: str) -> str:
    """No temporary directory and no clock in a line a verdict cites:
    tests/test_determinism wants the same bytes from two directories."""
    text = text.replace(copy, "<repo>").replace(tmp, "<tmp>")
    return _CMAKE_DURATION.sub(r"\1", _DURATION.sub("", text))


def _decoded(output) -> str:
    """What a stopped process wrote, as text: ``subprocess.TimeoutExpired``
    carries bytes (or ``None``) whatever ``text=`` said, and a line cut by the
    stop may end inside a UTF-8 sequence."""
    if output is None:
        return ""
    if isinstance(output, bytes):
        return output.decode("utf-8", "replace")
    return str(output)


#: act's line that starts a step, ``⭐ Run Main <name>``: under an action's step
#: (a ``stepID`` of two or more ids) it names that step, where act's ``step``
#: field names the workflow's step that runs the action (measured with act 0.2.97:
#: tests/fw_logs/act_esp_composite_cmake.jsonl, ``step`` "Build" on the records
#: ``["1", "0"]`` and ``["1", "1"]``, whose lines say ``⭐ Run Main Report`` and
#: ``⭐ Run Main Compile``).
_RUN_LINE = re.compile(r"^⭐ Run (?:Pre |Main |Post )?(.+?)\s*$")


def parse(stdout: str, scrub=lambda s: s) -> tuple[list, list]:
    """act's ``--json`` log as ``(entries, act_lines)``.

    Each entry is one job and matrix entry: ``{"job", "matrix", "result", "steps"}``,
    each step ``{"stage", "id", "name", "result", "log", "act"}``, where ``log`` is
    the step's own output (act's ``raw_output`` lines) and ``act`` act's lines
    about it. ``act_lines`` holds act's lines about no job. A step of an action
    (a composite action's own steps: ``id`` ``[step, n]``) has ``action_step``
    too, the name act's line gives it (:data:`_RUN_LINE`)."""
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
        if len(sid) > 1 and not step.get("action_step"):
            named = _RUN_LINE.match(msg.strip())
            if named:
                step["action_step"] = named.group(1)
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


def _run_collect(mode: str, copy: str, before: dict | None = None, keep: tuple = ()) -> dict:
    proc = subprocess.run([sys.executable, "-I", "-c", _COLLECT, mode, copy,
                           str(OUTPUT_CAP), ",".join(OUTPUT_SUFFIXES),
                           ",".join(OUTPUT_NAMES), ",".join(BUILD_RECORDS),
                           json.dumps([glob_pattern(g) for g in keep])],
                          input=json.dumps(before or {}), stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, text=True, timeout=300)
    try:
        return json.loads(proc.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError) as exc:
        what = "reading the job's files back" if mode == "read" else \
            "the copy's snapshot"
        raise ToolFault(f"{what} exited {proc.returncode}: "
                        f"{proc.stderr.strip()[-200:]}") from exc


def _snapshot(copy: str) -> dict:
    """``{rel: [inode, ctime_ns, mtime_ns, size]}`` of each output-named file and
    build record in the copy before act starts (``OUTPUT_SUFFIXES``' comment)."""
    return _run_collect("snap", copy)["stats"]


def _collect(copy: str, before: dict, keep: tuple = ()) -> tuple[dict, dict, list, int]:
    """``({rel: bytes}, {rel: bytes}, [image], oversize)``: each configuration
    file the job wrote and each image it wrote that a ``keep`` glob names (every
    file whose ``lstat`` differs from ``before``, :func:`_snapshot`); each config
    file a build record the job wrote names that the job left as it was
    (``BUILD_RECORDS``); and ``{name, size, sha256}`` of every image the job
    wrote, read back by a subprocess (see the module's docstring)."""
    found = _run_collect("read", copy, before, keep)
    if "oversize" in found:
        return {}, {}, [], int(found["oversize"])
    return ({rel: base64.b64decode(b64) for rel, b64 in found["files"].items()},
            {rel: base64.b64decode(b64) for rel, b64 in found.get("configs", {}).items()},
            list(found.get("images", [])), 0)


def _toolchain(image: str) -> dict:
    """``{image reference: local image id}``: which bytes the job ran in."""
    proc = subprocess.run(["docker", "image", "inspect", "--format", "{{.Id}}", image],
                          stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                          stderr=subprocess.DEVNULL, text=True, timeout=60)
    return {image: proc.stdout.strip() if proc.returncode == 0 else ""}


def _remove_containers(copy: str) -> str:
    """Remove every container whose bind source is the copy (act's job
    containers), running or not; return what could not be removed, or ``""``.

    Called on every outcome, inside the replay lock, after act returns. What
    slipped through calling it on a timeout alone (holdout review, three
    projects): act keeps a failed job's container running (``tail -f
    /dev/null``) unless it is given ``--rm``, so each failed replay left one, bound
    to a copy the host then removed. act now runs with ``--rm`` too; this is the
    net for a job act did not clean up (a timeout kills act, never the job).
    Measured with act 0.2.97: without ``--rm`` a failed job's container stays up,
    and ``docker ps -aq --filter volume=<copy>`` names it."""
    try:
        found = subprocess.run(["docker", "ps", "-aq", "--filter", f"volume={copy}"],
                               stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"docker ps did not run ({type(exc).__name__}): a job container may be left"
    left = []
    for cid in found.stdout.split():
        try:
            gone = subprocess.run(["docker", "rm", "-f", cid], stdin=subprocess.DEVNULL,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                  timeout=60).returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            gone = False
        if not gone:
            left.append(cid)
    return (f"job container {', '.join(left)} could not be removed: docker rm -f "
            f"{' '.join(left)}") if left else ""


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


def main_step(entry: dict, sid) -> dict | None:
    """The Main stage of the step act logged as ``stepID`` ``[sid]``: the step's own
    ``id:``, else its index (``_ci.Job.act_id``).

    A step that runs a composite action is two or more records: the step's own
    (``[sid]``), which holds no output, and one for each of the action's steps
    (``[sid, n]``), which hold it (measured with act 0.2.97:
    tests/fw_logs/act_esp_composite_cmake.jsonl). The step is then one record
    with the step's own result and the action's steps' output and act lines
    after its own, and ``inner``, the action's steps. What slipped through the
    first record alone (holdout review round 2, a watch's composite build): the
    build step's output was empty, so its errored reason quoted act's ``Job
    failed``, and a compile error inside the action could only read errored,
    never fail, as no design line was ever read."""
    own, inner = None, []
    for s in entry["steps"]:
        if s["stage"] != "Main" or s["id"][:1] != [str(sid)]:
            continue
        if len(s["id"]) == 1:
            own = own or s
        else:
            inner.append(s)
    if own is None or not inner:
        return own
    return {**own, "log": own["log"] + "".join(s["log"] for s in inner),
            "act": list(own["act"]) + [line for s in inner for line in s["act"]],
            "inner": inner}


def _position(job, sid: str) -> int | None:
    """The index in the job's steps of the step act logged as ``sid``."""
    if sid in job.ids:
        return job.ids.index(sid)
    return int(sid) if sid.isdigit() and not job.ids else None


def _last_words(entry: dict, step: dict | None = None) -> str:
    lines = [ln for ln in ((step or {}).get("act", []) + entry.get("act", [])) if ln.strip()]
    return lines[-1][:200] if lines else "no output"


def _first_cause(log: str) -> str:
    """The first line of ``log`` that :data:`CAUSE_LINE` names, as a person reads
    it, or ``""``: a CMake location header gives way to the message under it,
    the location after it in brackets (:data:`_CMAKE_AT`); a toolchain's path
    before its name is cut (:data:`_TOOL_PATH`)."""
    lines = log.splitlines()
    for at, line in enumerate(lines):
        if not CAUSE_LINE.search(line):
            continue
        header = _CMAKE_AT.search(line.strip())
        if header:
            said = []
            for under in lines[at + 1:]:
                if not under.strip():
                    continue
                if not under[:1].isspace():
                    break
                said.append(under.strip())
            if said:
                return f"{' '.join(said)[:200]} (CMake Error at {header.group(1)[:120]})"
        return _TOOL_PATH.sub(r"\1: ", line.strip())[:200]
    return ""


def _cause(entry: dict, step: dict | None = None) -> str:
    """What a failed step says of its cause: the first line of its own output that
    :data:`CAUSE_LINE` names (:func:`_first_cause`); else its own last line of
    output; else act's last line about it or about the job.

    A step that runs an action (``inner``, :func:`main_step`) is quoted by the
    action's own last error line, its last ``::error`` annotation
    (:data:`_ANNOTATION`), and then by the first cause line in the output of the
    action's step that failed. What slipped through reading the step's own record
    (holdout review round 2, a watch's composite build): it holds no output, so
    the reason quoted act's ``Job failed``. *Rejected:* the annotation alone (in
    the recorded excerpt it is ``idf.py build failed``, and the missing file CMake
    named is the line a person acts on); the first cause line alone (an action's
    own summary names which of its builds failed, and its first cause line may be
    a tool's that the action tolerated)."""
    inner = (step or {}).get("inner") or []
    if inner:
        said = [m.group(1).strip() for s in inner for line in s.get("act", []) + s.get("log", "")
                .splitlines() for m in [_ANNOTATION.search(line)] if m]
        failed = [s for s in inner if s.get("result") == "failure"]
        first = _first_cause(failed[-1]["log"] if failed else step.get("log", ""))
        if said:
            own = f"{said[-1][:200]} (the action's own error)"
            return f"{own}; first cause in its output: {first}" if first else own
        if first:
            return first
        log = failed[-1]["log"] if failed else step.get("log", "")
        rest = [ln.strip() for ln in log.splitlines() if ln.strip()]
        return rest[-1][:200] if rest else _last_words(entry, failed[-1] if failed else step)
    log = (step or {}).get("log", "")
    first = _first_cause(log)
    if first:
        return first
    own = [ln.strip() for ln in log.splitlines() if ln.strip()]
    return own[-1][:200] if own else _last_words(entry, step)


def image_names(result: dict) -> list[str]:
    """The workspace path of every image the job wrote (``images``), sorted."""
    return sorted(str(row["name"]) for row in result["images"])


#: How many images a detail names besides the ones ``firmware_image`` and
#: ``firmware_elf`` name: past it, it counts them and points at
#: ``images.json``, which lists each. Four is the number of other images the
#: baseline's ESP-IDF build writes (its images.json, from the fw.build.good
#: control's replay, 2026-11-24: six rows; ``build/sleeper.bin`` and
#: ``build/sleeper.elf`` are the declared two, and the other four are
#: ``build/bootloader/bootloader.bin``, ``build/bootloader/bootloader.elf``,
#: ``build/ota_data_initial.bin``, written because its partition table has an
#: ``otadata`` row, and ``build/partition_table/partition-table.bin``). So the
#: margin is zero: one more image in that build (a ULP program's, for one) and
#: its pass counts the others instead of naming them. Nothing is lost
#: then: the declared images still come first, and images.json still lists
#: each. An NCS sysbuild wrote 185 (holdout review, an NCS watch: images.json 185
#: rows, 352 MB). What slipped through naming every image, sorted (holdout
#: review round 2, that watch): a test vector ``west update`` fetched sorted
#: first, and the spine cut the detail at 500 characters before the signed app
#: image the readers judge. (Review of J3: this note first said the four were
#: the app's two and two others, which left two, not four, other images.)
#: *Rejected:* every image (above); none (a small build's other images are the
#: context a person reads); a cap with a margin over the baseline (each name
#: adds some 35 characters to a detail the spine cuts at 500, which a matrix
#: entry's label and the later steps not judged share; past the cap only the
#: names go); putting the files a fetch wrote last (no rule here tells a
#: fetched file from a built one: both are new in the workspace).
NAMED_IMAGES = 4


def _wrote(result: dict) -> str:
    """``wrote ...``: the images ``firmware_image`` and ``firmware_elf`` name
    first (those read back whole, ``files``), then the job's other images apart,
    by name up to :data:`NAMED_IMAGES`, else counted."""
    images = image_names(result)
    named = [rel for rel in images if rel in result["files"]]
    rest = [rel for rel in images if rel not in result["files"]]
    if not images:
        return "wrote no image"
    if not named:
        if len(rest) <= NAMED_IMAGES:
            return f"wrote {', '.join(rest)}"
        return f"wrote {len(rest)} images (images.json lists each)"
    said = f"wrote {', '.join(named)}"
    if not rest:
        return said
    if len(rest) <= NAMED_IMAGES:
        return f"{said}; other images it wrote: {', '.join(rest)}"
    return f"{said}; and {len(rest)} other images (images.json lists each)"


def _running(result: dict, job) -> str:
    """``"; step 'X' (entry) was still running ..."`` for each step of ``job`` that
    act started and logged no result for before a timeout stopped it, or ``""``."""
    said = [f"{s['name']!r}{_label(e)}" for e in result["entries"] if e["job"] == job.job
            for s in e["steps"] if s["stage"] == "Main" and len(s["id"]) == 1
            and s["result"] is None]
    if not said:
        return ""
    words = "step {} was" if len(said) == 1 else "steps {} were"
    return f"; {words.format(', '.join(said))} still running (log.txt holds the output so far)"


def judge(result: dict, job, image_glob: str | None = None,
          tolerated: frozenset = frozenset()) -> tuple[bool, str]:
    """``(passed, detail)`` for the build job ``job`` (a ``_ci.Job``) in one replay,
    or :class:`ToolFault`. ``tolerated`` holds the indices of steps before the build
    that carry ``continue-on-error: true``: their failure stops nothing on GitHub."""
    if result["timeout"]:
        raise ToolFault(f"act did not finish in {result.get('timeout_s') or TIMEOUT_S:g} s "
                        f"(firmware_ci_timeout_s); the job's containers were removed"
                        f"{_running(result, job)}")
    entries = [e for e in result["entries"] if e["job"] == job.job]
    chosen = result.get("matrix") or {}
    if not entries:
        words = [ln for ln in result["act"] if ln.strip()]
        which = (f" (firmware_ci_matrix {_said(chosen)} may name no entry)" if chosen else "")
        raise ToolFault(f"act exited {result['rc']} before any step of job {job.job}{which}: "
                        f"{(words[-1] if words else 'no output')[:200]}")
    if chosen and len(entries) != 1:
        raise ToolFault(f"firmware_ci_matrix {_said(chosen)} selects {len(entries)} entries of "
                        f"job {job.job}'s matrix: give a value for each key, so one runs")
    # The entry act ran is the one it logged (``entries[0]["matrix"]``), and
    # every key firmware_ci_matrix names must be in it with that value. What
    # slipped through comparing the count alone (review of the holdout fixes,
    # measured with act 0.2.97 in dry-run mode): act ignores a ``--matrix`` key
    # the job's matrix does not have, so ``device:x`` ran a job with no matrix
    # once (matrix ``{}``) and a job whose matrix is ``{side: [l]}`` as ``{side:
    # l}``, and the pass named ``matrix entry {device: x}``, an entry that was
    # never replayed. *Rejected:* naming the logged entry and passing (a key the
    # project typed that selected nothing is its mistake to see, not ours to
    # read past).
    ran = entries[0].get("matrix") or {}
    if chosen and not ran:
        raise ToolFault(f"firmware_ci_matrix {_said(chosen)} names a matrix entry, and job "
                        f"{job.job} has no matrix: act ran it once; leave "
                        f"firmware_ci_matrix out")
    unheld = [k for k in sorted(chosen)
              if k not in ran or matrix_word(ran[k]) != matrix_word(chosen[k])]
    if unheld:
        raise ToolFault(f"firmware_ci_matrix {_said(chosen)} is not the entry act ran "
                        f"({_said(ran)}): act ignores a key the job's matrix does not have "
                        f"({', '.join(unheld)}); name each key as the matrix does")
    if result["oversize"]:
        raise ToolFault(f"the files read back whole (firmware_image, firmware_elf and the "
                        f"configuration) total {result['oversize']} B, past the {OUTPUT_CAP} B "
                        f"cap: name one file with each glob")
    fails = []
    for entry in entries:
        label = _label(entry)
        setup = next((s for s in entry["steps"] if s["id"] == ["--setup-job"]), None)
        if setup is not None and setup["result"] == "failure":
            raise ToolFault(f"act could not set up job {job.job}{label}: "
                            f"{_last_words(entry, setup)}")
        for i in range(job.index):
            step = main_step(entry, job.act_id(i))
            if step is None or step["result"] != "failure" or i in tolerated:
                continue
            cause = _cause(entry, step)
            runs = tuple(getattr(job, "runs", ()) or ())
            if (not job.image and APT_NO_LISTS.search(step["log"])
                    and not any(APT_UPDATE.search(run) for run in runs[:i + 1])):
                raise ToolFault(f"step {step['name']!r} before the build failed{label} on empty "
                                f"apt lists (act's runner image has none, GitHub's has them; "
                                f"run `sudo apt-get update` first): {cause}")
            raise ToolFault(f"step {step['name']!r} before the build failed in job "
                            f"{job.job}{label}: the pack cannot tell a manifest error "
                            f"from a network fault ({cause})")
        build = main_step(entry, job.act_id(job.index))
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
                raise ToolFault(f"the build step failed{label} on no line this pack knows as "
                                f"a design fault: {_cause(entry, build)}")
            fails.append(f"the build fails{label}: {_line_at(log, hit)}")
        elif build["result"] == "success":
            hit = next((m for sig in DESIGN_LINES for m in [sig.search(log)] if m), None)
            gap = next((m for sig in REPLAY_GAP_LINES for m in [sig.search(log)] if m), None)
            if hit is not None:
                fails.append(f"the build step read success{label}, and its output holds "
                             f"{_line_at(log, hit)!r}: a failed build masked")
            elif ZMK_NO_CONFIG.search(log):
                fails.append(f"the build{label} could not find its ZMK config "
                             f"({_line_at(log, ZMK_NO_CONFIG.search(log))}): it built ZMK's "
                             f"default keymap, not this repository's")
            elif gap is not None:
                raise ToolFault(f"the build step read success{label}, but part of it could not "
                                f"run in the replay (no docker daemon in jobs, or a file the "
                                f"copy lacks): {_line_at(log, gap)}")
        else:
            raise ToolFault(f"the build step ended {build['result']!r}{label}")
    if fails:
        return False, "; ".join(dict.fromkeys(fails))
    images = image_names(result)
    if image_glob and not any(matches(rel, image_glob) for rel in images):
        return False, (f"the file you flash ({image_glob}) is not what job {job.job} builds: "
                       f"it {_wrote(result)}")
    later = []
    placed = [(_position(job, s["id"][0]), s) for s in entries[0]["steps"]
              if s["stage"] == "Main" and len(s["id"]) == 1]
    for _at, step in sorted(((at, s) for at, s in placed if at is not None and at > job.index),
                            key=lambda p: p[0]):
        later.append(f"{step['name']} ({step['result'] or 'not run'})")
    where = f" in {_short(job.image)}" if job.image else ""
    many = f", {len(entries)} matrix entries" if len(entries) > 1 else ""
    one = f", matrix entry {_said(ran)}" if len(entries) == 1 and ran else ""
    said = (f"job {job.job}{many}{one}: step {job.name!r} ({job.command}) passed{where}; "
            f"{_wrote(result)}")
    if later:
        said += f"; later steps not judged: {', '.join(later)}"
    return True, said


def _said(matrix: dict) -> str:
    return "{" + ", ".join(f"{k}: {matrix_word(v)}" for k, v in sorted(matrix.items())) + "}"


def _short(image: str) -> str:
    return re.sub(r"(@sha256:[0-9a-f]{12})[0-9a-f]+", r"\1", image)


def glob_pattern(glob: str) -> str:
    """``glob`` as a regular expression over a workspace path, a path at a time:
    ``*`` and ``?`` stay inside one directory, ``**/`` crosses any number of them.
    The read-back's subprocess matches ``keep`` with it, so a file comes back
    whole exactly when :func:`matches` names it."""
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
    return "".join(out)


def matches(rel: str, glob: str) -> bool:
    """Whether the workspace path ``rel`` matches ``glob`` (:func:`glob_pattern`).
    What slipped through ``fnmatch`` (whose ``*`` matches ``/``): ``build/*.bin`` on
    the ESP-IDF baseline named the bootloader's and the partition table's ``.bin``
    too, and the gate refused to choose."""
    return re.fullmatch(glob_pattern(glob), rel) is not None


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
    """The replay's scrubbed log (``log.txt``: each step's own output and act's
    lines about it, then act's lines about the job), ``images.json`` (name, size
    and sha256 of each image the job wrote) and ``toolchain.json`` (each image
    reference and its local id), under the gate's out directory. ``out_path`` is
    ``ctx.out_path``.

    Written on every outcome the replay reached, an errored one too. What slipped
    through writing it on a judged build alone (holdout review, three projects):
    an errored replay kept no log, its reason quoted act's last line (``Job
    failed``), and each person had to replay the failure by hand to find a
    CMake error or apt's ``Unable to locate package`` many lines above it. A
    timeout writes what act had logged when it was stopped, the step it was in
    marked as stopped, and says so at the end. An action's own steps are each
    headed by the step that runs the action and the action step's own name: what
    slipped through one heading for every record (holdout review round 2, a
    watch's composite build) was two entries of the same name, both marked the
    build step, the second holding all the output."""
    paths = []
    entries = [e for e in result["entries"] if e["job"] == job.job]
    open_step = "stopped at the timeout" if result.get("timeout") else "not run"
    log = out_path("firmware-ci", "build", "log.txt")
    with open(log, "w", encoding="utf-8") as fh:
        for entry in entries:
            for step in entry["steps"]:
                if step["stage"] != "Main":
                    continue
                mark = " (the build step)" if step["id"][:1] == [job.act_id(job.index)] else ""
                if len(step["id"]) > 1:
                    mark += f", the action's step {step.get('action_step') or step['id'][-1]!r}"
                fh.write(f"== job {entry['job']}{_label(entry)}, step {step['name']!r}{mark}: "
                         f"{step['result'] or open_step}\n")
                text = step.get("log", "")
                fh.write(text if not text or text.endswith("\n") else text + "\n")
                for line in step.get("act", []):
                    fh.write(f"-- act: {line}\n")
            if entry.get("act"):
                fh.write(f"== job {entry['job']}{_label(entry)}, act\n")
                fh.write("".join(f"{line}\n" for line in entry["act"]))
        if result.get("act"):
            fh.write("== act\n" + "".join(f"{line}\n" for line in result["act"]))
        if result.get("timeout"):
            fh.write(f"== act was stopped at {result.get('timeout_s') or TIMEOUT_S:g} s "
                     f"(firmware_ci_timeout_s): the log ends where it stopped\n")
    paths.append(log)
    images = out_path("firmware-ci", "build", "images.json")
    with open(images, "w", encoding="utf-8") as fh:
        json.dump(sorted(result["images"], key=lambda row: row["name"]), fh, indent=1,
                  sort_keys=True)
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
