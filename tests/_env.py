# SPDX-License-Identifier: Apache-2.0
"""The one environment every test subprocess runs in.

A test that passes on the machine it was written on and fails on a CI runner —
or the reverse — has measured the machine, not the code. The dev box this suite
grew up on differs from a GitHub runner in exactly the ways a subprocess
inherits: a global git identity (so ``git commit`` works here and fails there);
possibly a ``~/.atompipe/packs`` that outranks the bundled packs; a Claude Code
session's ``CLAUDE*`` and ``AI_AGENT`` variables, which the agent-facing
commands read and a runner never sets; and, when the suite runs inside a git
hook, ``GIT_DIR`` and ``GIT_INDEX_FILE`` pointing every git call at the OUTER
repository. So every subprocess in ``tests/`` goes through :func:`run`, and
``test_meta.NoSubprocessOutsideRun`` refuses one that does not.

What a child gets, and why each line:

* a temp ``HOME`` (``USERPROFILE`` too, for Windows' ``expanduser``) — nothing
  under the developer's home is visible: no user packs, no global gitconfig.
* ``PYTHONUSERBASE`` pinned to the parent's ``site.getuserbase()``. What slipped
  through while designing this: a temp ``HOME`` alone moves the user site
  directory, where trimesh and numpy live on the dev box, so every mesh gate in
  every child read availability-SKIPPED — honest-looking, and every subprocess
  test of those packs vacuous.
* ``PYTHONNOUSERSITE`` and ``PATH`` passed through UNCHANGED, so the CI-like run
  (``PYTHONNOUSERSITE=1 PATH=/usr/bin:/bin``) is CI-like in the children too.
* ``PYTHONPATH`` = this checkout's ``src`` first, then the parent's entries made
  absolute. The parent's ``PYTHONPATH=src`` is relative to the repo root, so a
  child in a temp cwd would otherwise import whatever atompipe is installed —
  and the partial-tool probe injects a ``sitecustomize`` through ``PYTHONPATH``,
  which a child must see to see the same world.
* ``GIT_CONFIG_GLOBAL=/dev/null`` and ``GIT_CONFIG_NOSYSTEM=1``; every other
  ``GIT_*`` variable stripped (``GIT_DIR``, ``GIT_WORK_TREE``,
  ``GIT_INDEX_FILE`` above all — a hook exports them), and ``XDG_CONFIG_HOME``
  with them, because git reads ``$XDG_CONFIG_HOME/git/ignore`` and
  ``attributes`` even when ``GIT_CONFIG_GLOBAL`` replaces the config file.
* ``GIT_AUTHOR_*``/``GIT_COMMITTER_*`` only with ``identity=True``; ``EMAIL`` is
  stripped with them, since git falls back to it for an identity.
* ``ATOMPIPE_PACK_PATH`` unset; ``CLAUDE*`` and ``AI_AGENT`` stripped;
  ``PYTHONDONTWRITEBYTECODE`` stripped — a user's shell does not set it, and the
  fresh-clone transcript must see the ``__pycache__`` a real run writes.

Anything in ``env=`` is applied last and wins, so a test that WANTS one of the
stripped variables passes it explicitly; a value of ``None`` removes a key.

This file is test infrastructure, not spine: it may use ``subprocess`` because it
is the one place that does.
"""
from __future__ import annotations

import os
import pathlib
import shutil
import site
import stat
import subprocess
import sys
import tempfile
import unittest
from typing import Any, Mapping, Sequence

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(REPO, "src")

#: The identity a test gets when it asks for one. A fixed, obviously-synthetic
#: value, so a commit a test makes can never pass for a person's.
IDENTITY = {
    "GIT_AUTHOR_NAME": "atompipe tests",
    "GIT_AUTHOR_EMAIL": "tests@atompipe.invalid",
    "GIT_COMMITTER_NAME": "atompipe tests",
    "GIT_COMMITTER_EMAIL": "tests@atompipe.invalid",
}

#: Stripped from the parent's environment by exact name (see the module docstring
#: for each). HOME, USERPROFILE, PYTHONPATH and PYTHONUSERBASE are re-set, not
#: inherited.
_STRIP = frozenset({
    "ATOMPIPE_PACK_PATH", "AI_AGENT", "PYTHONDONTWRITEBYTECODE", "EMAIL",
    "XDG_CONFIG_HOME", "HOME", "USERPROFILE", "PYTHONPATH", "PYTHONUSERBASE",
})

#: Stripped by prefix: every agent-session variable and every git variable.
#: Rejected: listing GIT_DIR, GIT_WORK_TREE and GIT_INDEX_FILE alone — git reads
#: dozens more (GIT_CONFIG_PARAMETERS carries `git -c` into a hook's children,
#: GIT_CONFIG_COUNT injects config, GIT_OBJECT_DIRECTORY redirects the store), and
#: a list of the ones someone remembered is the allow-list mistake again.
_STRIP_PREFIXES = ("CLAUDE", "GIT_")

#: Seconds before a child is declared hung. Why 180: far above what a child costs
#: (measured 2026-09-27: a bracket `check --tier 3` in 0.1 s; loading all 54
#: bundled gates in a child, a fraction of a second; an omc compile, about a
#: second), so only a hang ever reaches it — and a hang then fails its own test,
#: naming the command, instead of the CI job. Rejected: no timeout (a git
#: credential prompt or a solver waiting on input hangs the suite until the job's
#: six-hour limit); tens of seconds (a cold runner running a solver gate under a
#: loaded matrix is not this machine).
DEFAULT_TIMEOUT_S = 180


def _pythonpath() -> str:
    """This checkout's ``src`` first, then the parent's entries, absolute and unique."""
    entries = [SRC]
    for entry in os.environ.get("PYTHONPATH", "").split(os.pathsep):
        if entry:
            entries.append(os.path.abspath(entry))
    seen: set[str] = set()
    out: list[str] = []
    for entry in entries:
        key = os.path.normcase(os.path.normpath(entry))
        if key not in seen:
            seen.add(key)
            out.append(entry)
    return os.pathsep.join(out)


def clean_env(*, home: str, identity: bool = False,
              extra: Mapping[str, Any] | None = None) -> dict[str, str]:
    """The environment a child process gets. See the module docstring."""
    env = {k: v for k, v in os.environ.items()
           if k not in _STRIP and not k.startswith(_STRIP_PREFIXES)}
    env["HOME"] = home
    env["USERPROFILE"] = home
    env["PYTHONUSERBASE"] = site.getuserbase()
    env["PYTHONPATH"] = _pythonpath()
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    if identity:
        env.update(IDENTITY)
    for key, value in (extra or {}).items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = str(value)
    return env


def _rmtree(path: str) -> None:
    """``shutil.rmtree`` that also removes read-only files (git's objects are 0444,
    which Windows refuses to delete)."""
    def retry(func, target, _exc):
        try:
            os.chmod(target, stat.S_IWRITE)
            func(target)
        except OSError:
            pass
    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=retry)
    else:                                        # pragma: no cover - 3.10/3.11
        shutil.rmtree(path, onerror=retry)


def run(argv: Sequence[Any], *, cwd: str, home: str | None = None, identity: bool = False,
        env: Mapping[str, Any] | None = None,
        timeout: float = DEFAULT_TIMEOUT_S) -> subprocess.CompletedProcess:
    """Run ``argv`` in ``cwd`` under :func:`clean_env`; never raises on a non-zero exit.

    Output is captured as text (UTF-8, undecodable bytes replaced); stdin is
    closed, so a child that asks a question fails instead of hanging. With no
    ``home`` a fresh temp ``HOME`` is made for this call and removed after it.
    A child that outlives ``timeout`` fails the test, naming the command.
    """
    own_home = None
    if home is None:
        own_home = tempfile.mkdtemp(prefix="atompipe-home-")
        home = own_home
    command = [os.fspath(a) for a in argv]
    try:
        return subprocess.run(
            command, cwd=cwd, env=clean_env(home=home, identity=identity, extra=env),
            stdin=subprocess.DEVNULL, capture_output=True, encoding="utf-8",
            errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise AssertionError(
            f"{command!r} in {cwd} did not finish in {timeout}s") from exc
    finally:
        if own_home is not None:
            _rmtree(own_home)


def atompipe(args: Sequence[Any], *, cwd: str, **kw: Any) -> subprocess.CompletedProcess:
    """``python -m atompipe <args>`` from this checkout's ``src``, via :func:`run`."""
    return run([sys.executable, "-m", "atompipe", *args], cwd=cwd, **kw)


#: What ``claim physical`` writes to stderr when it waits for the claim's id —
#: the end of its prompt line (P2.5a-D4). ``run_tty`` types its answer once this
#: has appeared, never before: a line typed before the prompt is a line a person
#: could not have read the prompt before typing.
TTY_PROMPT_END = "(anything else records nothing): "


def run_tty(argv: Sequence[Any], *, cwd: str, answer: str | None, home: str | None = None,
            identity: bool = True, env: Mapping[str, Any] | None = None,
            timeout: float = 60.0) -> subprocess.CompletedProcess:
    """``python -m atompipe <argv>`` with its stdin a terminal — the ONE place a
    pty is opened in ``tests/`` — as a person's own shell runs it (P2.5a-D4).

    The child's stdin is the slave end of ``pty.openpty()``; stdout and stderr
    are pipes, captured as :func:`run` captures them, under :func:`clean_env`
    (every agent marker stripped unless ``env`` sets it). When ``answer`` is a
    string it is typed, with a newline, once ``TTY_PROMPT_END`` has appeared on
    stderr; ``None`` closes the terminal instead (end of input). A child that
    never prompts is left to finish. POSIX only, which every CI runner is: off
    POSIX this RAISES — the test errors, it never skips (R-7: a skipped row in an
    invariant class is a red test wearing green). Identity on by default: the
    channel refuses to record without one (P2.5a-D5)."""
    if os.name != "posix":
        raise AssertionError("run_tty needs a POSIX pty; this platform has none")
    import pty
    import select
    import time
    own_home = None
    if home is None:
        own_home = tempfile.mkdtemp(prefix="atompipe-home-")
        home = own_home
    command = [sys.executable, "-m", "atompipe", *(os.fspath(a) for a in argv)]
    master, slave = pty.openpty()
    try:
        proc = subprocess.Popen(command, cwd=cwd,
                                env=clean_env(home=home, identity=identity, extra=env),
                                stdin=slave, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        os.close(slave)
        slave = -1
        out, err = b"", b""
        typed = False
        deadline = time.monotonic() + timeout
        streams = {proc.stdout.fileno(): "out", proc.stderr.fileno(): "err"}
        while streams:
            if time.monotonic() > deadline:
                proc.kill()
                raise AssertionError(f"{command!r} in {cwd} did not finish in {timeout}s")
            ready, _w, _x = select.select(list(streams), [], [], 0.2)
            for fd in ready:
                chunk = os.read(fd, 65536)
                if not chunk:
                    streams.pop(fd)
                    continue
                if streams[fd] == "out":
                    out += chunk
                else:
                    err += chunk
            if not typed and TTY_PROMPT_END.encode() in err:
                typed = True
                if answer is None:
                    os.close(master)
                    master = -1
                else:
                    os.write(master, answer.encode("utf-8") + b"\n")
        code = proc.wait(timeout=max(1.0, deadline - time.monotonic()))
        proc.stdout.close()
        proc.stderr.close()
        return subprocess.CompletedProcess(command, code,
                                           out.decode("utf-8", "replace"),
                                           err.decode("utf-8", "replace"))
    finally:
        for fd in (master, slave):
            if fd >= 0:
                try:
                    os.close(fd)
                except OSError:
                    pass
        if own_home is not None:
            _rmtree(own_home)


def git(args: Sequence[Any], *, cwd: str, identity: bool = False,
        **kw: Any) -> subprocess.CompletedProcess:
    """``git <args>`` via :func:`run`. No identity unless asked for, as on a runner."""
    return run(["git", *args], cwd=cwd, identity=identity, **kw)


def shallow_clone(src: str, dst: str) -> str:
    """A depth-1 clone of the repository at ``src`` into ``dst``; returns ``dst``.

    Always through a ``file://`` URL. What slipped through: ``git clone --depth 1
    <local path>`` takes the local-path fast route and IGNORES ``--depth`` (it
    says so only as a warning), so a "fresh clone" test was quietly handed the
    whole history.
    """
    url = pathlib.Path(os.path.abspath(src)).as_uri()
    target = os.path.abspath(os.fspath(dst))
    proc = git(["clone", "--quiet", "--depth", "1", url, target],
               cwd=os.path.dirname(target))
    if proc.returncode != 0:
        raise AssertionError(f"git clone --depth 1 {url} failed: {proc.stderr.strip()}")
    return target


class EnvCase(unittest.TestCase):
    """A ``TestCase`` with temp directories that clean up after themselves."""

    def tmp(self) -> str:
        """A fresh directory under ``tempfile.gettempdir()``, removed after the test.

        Never inside the repository: the provenance scan and the fresh-clone
        transcript both walk the checkout, and a planted tree there is a planted
        hit (tests:H3).
        """
        path = tempfile.mkdtemp(prefix="atompipe-test-")
        self.addCleanup(_rmtree, path)
        return path
