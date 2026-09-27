# SPDX-License-Identifier: Apache-2.0
"""atompipe.util — the primitives the rest of the spine leans on.

This module imports nothing from atompipe. It is the bottom of the dependency
graph and the one file that must never fail to import, so: standard library
only, no optional extras, no import-time work beyond defining names.

Four things here exist because of specific, expensive failures, not because a
utility module is traditional:

* ``atomic_write_text`` — a crash (or a full disk) partway through rewriting
  ``.atompipe/ledger.json`` leaves a truncated file, which is the project's
  entire claim/evidence state gone. Every write in the spine serialises FIRST,
  writes a temp file in the SAME directory, fsyncs it, then ``os.replace``s.
  A concurrent reader sees either the old ledger or the new one, never a torn
  one.
* ``FileLock`` — a rebuilt part was once lost: a second run started three
  minutes into the first and overwrote ``build/`` underneath it. The rebuilt
  part existed, then didn't, and nothing in the logs said why. The lock records
  a pid so a *crashed* run's leftovers get reaped instead of blocking forever,
  and refuses to steal a lock whose holder is still breathing.
* ``rel`` — records holding absolute paths stop matching the moment the project
  is moved or cloned; records that quietly relativise a path from OUTSIDE the
  project lie about where the evidence came from. ``rel`` does exactly one of
  those two things and the result says which.
* ``iter_suffix_unique`` — two claims slugged to the same id once, and the
  second silently overwrote the first in a dict keyed by id. Dedup is a shared
  rule, so it lives in one place.

Time policy: spine *logic* never reads the clock (rule 3 of the contract —
callers pass timestamps in). ``utcnow_iso`` is for the CLI edge, and FileLock
reads mtimes because lock staleness is a property of wall-clock time and cannot
be passed in by the caller that is blocked on it.
"""
from __future__ import annotations

import hashlib
import json
import os
import socket
import stat
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from typing import Any, Iterable


__all__ = [
    "AtompipeError",
    "utcnow_iso",
    "ensure_dir",
    "atomic_write_text",
    "atomic_write_json",
    "read_json",
    "sha256_text",
    "short_hash",
    "human_bytes",
    "human_duration",
    "rel",
    "iter_suffix_unique",
    "FileLock",
]


# --------------------------------------------------------------------------- #
# errors
# --------------------------------------------------------------------------- #
class AtompipeError(Exception):
    """An error the *user* caused and can act on.

    The CLI prints ``error: <message>`` and exits 2 for these, with no
    traceback — a stack trace teaches a user nothing about a missing file or a
    held lock. Anything raised as a plain exception is therefore, by
    construction, a bug in the spine and keeps its traceback.

    The message is the whole product here: say what is wrong, name the path,
    and say what to do about it. "another atompipe run (pid 4821) holds
    .atompipe/build.lock — wait for it, or delete that file if the run is gone"
    beats "lock error" by the entire distance between fixed and stuck.
    """


# --------------------------------------------------------------------------- #
# time
# --------------------------------------------------------------------------- #
def utcnow_iso(at: float | None = None) -> str:
    """UTC timestamp as ``2026-09-11T14:46:00Z``.

    Whole seconds, Z suffix, no microseconds: these strings land in the ledger
    and in decision logs, they get diffed by git and read by humans, and
    sub-second precision on a design decision is noise that only makes the diff
    noisier.

    Call this at the CLI edge and pass the result down. A function that stamps
    its own time cannot be tested and cannot be replayed — that is why every
    model in ``models.py`` takes ``when``/``added`` as a plain string instead of
    filling it in itself.

    ``at`` (epoch seconds) exists so tests can pin the clock without patching
    the module.
    """
    ts = time.time() if at is None else float(at)
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------- #
# filesystem
# --------------------------------------------------------------------------- #
def ensure_dir(path: str | os.PathLike[str]) -> str:
    """Create ``path`` (and parents) if needed; return it as a str.

    Returns the path so callers can write ``open(os.path.join(ensure_dir(d),
    name), "w")`` without a second statement. Every failure here is something
    the user can fix — a read-only checkout, a file sitting where a directory
    belongs — so every failure becomes an AtompipeError with the path in it.
    """
    target = os.fspath(path)
    try:
        os.makedirs(target, exist_ok=True)
    except FileExistsError as exc:
        # makedirs(exist_ok=True) still raises when the name exists and is NOT a
        # directory. That is a real user situation: `atompipe init` in a tree
        # where someone has a file called `docs`.
        raise AtompipeError(f"cannot create directory {target}: a file of that name already exists") from exc
    except NotADirectoryError as exc:
        raise AtompipeError(f"cannot create directory {target}: a parent path component is a file") from exc
    except PermissionError as exc:
        raise AtompipeError(f"cannot create directory {target}: permission denied") from exc
    except OSError as exc:
        raise AtompipeError(f"cannot create directory {target}: {exc.strerror or exc}") from exc
    return target


def _fsync_dir(directory: str) -> None:
    """Best-effort fsync of a directory so a rename survives power loss.

    ``os.replace`` is atomic for readers immediately, but the *directory entry*
    can still be sitting in the page cache when the machine dies. Failures are
    swallowed on purpose: not every platform or filesystem lets you open a
    directory, and a spine that refuses to save because it could not fsync a
    directory handle would be worse than one that saved slightly less durably.
    """
    try:
        fd = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def atomic_write_text(path: str | os.PathLike[str], text: str, *, encoding: str = "utf-8") -> None:
    """Write ``text`` to ``path`` so that a crash can never truncate it.

    Temp file in the SAME directory (a temp file in /tmp would make the final
    step a cross-device copy, which is not atomic), fsync, then ``os.replace``.
    Parent directories are created. Exactly one trailing newline is appended if
    the text lacks one — POSIX text files end in a newline, and a ledger without
    one produces a "\\ No newline at end of file" marker in every single git
    diff forever.

    ``newline="\\n"`` is forced: the ledger is a git-tracked artifact and must
    not gain CRLF because it happened to be written on Windows.

    The file mode is inherited from the existing file when there is one, else
    0644. ``tempfile.mkstemp`` creates 0600, and silently dropping the ledger to
    owner-only would break the next teammate or CI user to read it.
    """
    target = os.fspath(path)
    directory = os.path.dirname(os.path.abspath(target)) or os.curdir
    ensure_dir(directory)

    payload = text if (not text or text.endswith("\n")) else text + "\n"

    try:
        mode = stat.S_IMODE(os.stat(target).st_mode)
    except OSError:
        mode = 0o644

    tmp: str | None = None
    try:
        fd, tmp = tempfile.mkstemp(dir=directory, prefix="." + os.path.basename(target) + ".", suffix=".tmp")
        with os.fdopen(fd, "w", encoding=encoding, newline="\n") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        try:
            os.chmod(tmp, mode)
        except OSError:
            pass                      # filesystems without permissions: not fatal
        os.replace(tmp, target)
        tmp = None
    except OSError as exc:
        raise AtompipeError(f"cannot write {target}: {exc.strerror or exc}") from exc
    finally:
        if tmp is not None and os.path.exists(tmp):
            try:
                os.unlink(tmp)        # never leave .ledger.json.*.tmp litter behind
            except OSError:
                pass
    _fsync_dir(directory)


def atomic_write_json(path: str | os.PathLike[str], obj: Any) -> None:
    """Write ``obj`` as JSON: sorted keys, indent 2, trailing newline, atomic.

    ``sort_keys=True`` and ``indent=2`` are not cosmetic. The ledger is reviewed
    as a git diff — that is how a human notices that a rebuild quietly moved a
    parameter — and unsorted keys make every save look like a change to
    everything.

    Serialisation happens BEFORE the file is touched. An object that will not
    encode must not be allowed to leave a half-written ledger behind; that
    ordering is the whole reason this is not two lines at the call site.

    No ``default=`` encoder on purpose. Use ``models.Record.to_dict()`` to
    encode dataclasses and enums; a permissive fallback here would let a model
    and its projection drift apart silently, which is exactly what
    ``modelio.project`` is written to prevent.

    ``allow_nan=False``, and the refusal names the path. Python's default writes
    ``NaN`` and ``Infinity`` as bare tokens that no JSON parser accepts. That is
    how a could-not-measure number reached ``state.json``: ``JSON.parse``
    refused the whole file, and the page advised ``atompipe site build``, which
    wrote the same NaN again (S-47). Every JSON file the spine writes passes
    through here, so this is where "a number that could not be measured is not
    a number" is enforced for all of them. Rejected: writing ``null`` in its
    place — a value that silently becomes "no value" is the NaN problem in a
    quieter shape, and the gate layer already refuses the number upstream.
    """
    try:
        payload = json.dumps(obj, sort_keys=True, indent=2, ensure_ascii=False,
                             allow_nan=False)
    except ValueError as exc:
        # json raises ValueError for a non-finite float AND for a circular
        # reference; telling them apart by message text would tie this to one
        # Python's wording (the CI matrix runs three). The permissive encoder does
        # not mind a NaN, so if IT succeeds, a NaN or an Infinity was the only
        # fault. It runs only on the failure path.
        try:
            json.dumps(obj, sort_keys=True, allow_nan=True)
        except (TypeError, ValueError):
            pass
        else:
            raise AtompipeError(
                f"{os.fspath(path)}: refusing to write NaN or Infinity; a number "
                f"that could not be measured is not a number"
            ) from exc
        raise AtompipeError(
            f"cannot write {os.fspath(path)}: value is not JSON-serialisable ({exc}); "
            f"encode records with .to_dict() before saving"
        ) from exc
    except TypeError as exc:
        raise AtompipeError(
            f"cannot write {os.fspath(path)}: value is not JSON-serialisable ({exc}); "
            f"encode records with .to_dict() before saving"
        ) from exc
    atomic_write_text(path, payload)


def read_json(path: str | os.PathLike[str], default: Any = None) -> Any:
    """Read JSON from ``path``; return ``default`` if there is nothing there.

    "Nothing there" means missing OR empty/whitespace-only. Empty is treated as
    missing because ``atomic_write_*`` never produces a zero-byte file, so a
    zero-byte ledger is either a pre-atompipe crash artifact or someone's
    ``touch`` — in both cases there is no data to lose and the caller's default
    (an empty Ledger, for ``store.load``) is the honest answer.

    Malformed JSON is a different story and raises AtompipeError with the line
    and column: the ledger is a file humans hand-edit, and "expecting ',' at
    line 84 column 5" is the difference between a 10-second fix and a rewrite.
    """
    target = os.fspath(path)
    try:
        with open(target, "r", encoding="utf-8") as fh:
            raw = fh.read()
    except FileNotFoundError:
        return default
    except IsADirectoryError as exc:
        raise AtompipeError(f"cannot read {target}: it is a directory") from exc
    except UnicodeDecodeError as exc:
        raise AtompipeError(f"cannot read {target}: not UTF-8 text ({exc.reason})") from exc
    except OSError as exc:
        raise AtompipeError(f"cannot read {target}: {exc.strerror or exc}") from exc

    if not raw.strip():
        return default
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AtompipeError(
            f"{target} is not valid JSON (line {exc.lineno}, column {exc.colno}): {exc.msg}"
        ) from exc


# --------------------------------------------------------------------------- #
# hashing
# --------------------------------------------------------------------------- #
def sha256_text(text: str) -> str:
    """SHA-256 hex digest of ``text`` encoded UTF-8.

    Deliberately unnormalised: no stripping, no line-ending translation. These
    digests drive staleness ("did the model change since the gates ran?"), and a
    hash that forgives whitespace would let a real edit pass as unchanged. If a
    caller wants canonical input it canonicalises first —
    ``json.dumps(..., sort_keys=True)`` is the usual move.
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def short_hash(text: str, n: int = 12) -> str:
    """First ``n`` hex chars of ``sha256_text(text)``.

    12 hex chars is 48 bits — plenty to tell two revisions of a model apart in
    a run filename or a report header, short enough that a human can compare two
    of them by eye, which is the entire point.

    A bad ``n`` is a programming error, not a user error, so it raises
    ValueError rather than AtompipeError.
    """
    if not 1 <= n <= 64:
        raise ValueError(f"short_hash length must be 1..64, got {n}")
    return sha256_text(text)[:n]


# --------------------------------------------------------------------------- #
# human-readable rendering
# --------------------------------------------------------------------------- #
def human_bytes(n: int | float) -> str:
    """Render a byte count: ``947 B``, ``12.4 KiB``, ``3.1 MiB``.

    Binary units with binary names. A report that prints "1.0 MB" for 1048576
    bytes is off by 5% and lying in the direction that flatters it; this project
    is a readiness ledger, so it uses KiB/MiB and means them.
    """
    value = float(n)
    sign = "-" if value < 0 else ""
    value = abs(value)
    if value < 1024:
        return f"{sign}{int(round(value))} B"
    for unit in ("KiB", "MiB", "GiB", "TiB", "PiB"):
        value /= 1024.0
        if value < 1024.0 or unit == "PiB":
            # one decimal while it still carries information, none once the
            # integer part is three digits wide
            return f"{sign}{value:.1f} {unit}" if value < 100 else f"{sign}{value:.0f} {unit}"
    raise AssertionError("unreachable")  # pragma: no cover


def human_duration(seconds: float) -> str:
    """Render a duration: ``0.4s``, ``12s``, ``3m 20s``, ``1h 04m``.

    Sub-second precision is kept below 10s and dropped above it, because the
    only durations where a tenth of a second matters are tier-0 gate timings —
    the inner loop whose whole promise is "runs in seconds". Past a minute the
    tenths are decoration.

    The zero-padded minor unit (``1h 04m``, not ``1h 4m``) keeps a column of
    durations in a report aligned without a formatter.
    """
    if seconds < 0:
        return "-" + human_duration(-seconds)
    if seconds < 10:
        tenths = round(float(seconds), 1)
        if tenths < 10:                       # 9.97s must not render as "10.0s"
            return f"{tenths:.1f}s"
    total = int(round(float(seconds)))
    if total < 60:
        return f"{total}s"
    if total < 3600:
        return f"{total // 60}m {total % 60:02d}s"
    if total < 86400:
        return f"{total // 3600}h {(total % 3600) // 60:02d}m"
    return f"{total // 86400}d {(total % 86400) // 3600:02d}h"


# --------------------------------------------------------------------------- #
# paths
# --------------------------------------------------------------------------- #
def rel(path: str | os.PathLike[str], root: str | os.PathLike[str]) -> str:
    """Path relative to ``root``, POSIX-style — or the absolute path if outside.

    Records must survive the project being moved, cloned, or opened on another
    machine, so anything inside the project is stored relative and with forward
    slashes (a ledger written on Windows must still resolve on Linux).

    A path OUTSIDE the root is returned absolute and untouched. The tempting
    alternative — ``../../Downloads/pump.pdf`` — is a lie waiting to happen: it
    reads like part of the project and stops resolving the moment the project
    moves. A record that says ``/home/eric/Downloads/pump.pdf`` is honest about
    the fact that the evidence lives somewhere the project does not control.
    That honesty is why ``artifacts.ingest`` copies files in by default.

    Symlinks are not resolved (``abspath``, not ``realpath``): rewriting a
    user's path into its physical target produces records nobody recognises,
    and on macOS would turn every ``/tmp/...`` into ``/private/tmp/...``.
    """
    target = os.path.abspath(os.fspath(path))
    base = os.path.abspath(os.fspath(root))
    try:
        relative = os.path.relpath(target, base)
    except ValueError:
        # Windows: different drive letters have no relative path at all.
        return target.replace(os.sep, "/")
    if relative == os.pardir or relative.startswith(os.pardir + os.sep):
        return target.replace(os.sep, "/")
    return relative.replace(os.sep, "/")


# --------------------------------------------------------------------------- #
# id dedup
# --------------------------------------------------------------------------- #
def iter_suffix_unique(base: str, taken: Iterable[str]) -> str:
    """``base``, else ``base-2``, ``base-3``, ... — the first one not in ``taken``.

    ``models.slugify`` explicitly does not handle collisions ("callers dedupe"),
    and two claims phrased differently slug to the same id more often than you
    would guess ("hull floats at full load" / "hull floats, at full load"). The
    numbering starts at 2 because the unsuffixed name IS number one, and a
    ``foo`` / ``foo-2`` pair reads correctly in a report where ``foo-1`` /
    ``foo-2`` would leave the reader hunting for a missing ``foo``.

    ``taken`` may be any iterable of strings, including a dict (its keys).
    """
    if not base:
        raise ValueError("iter_suffix_unique needs a non-empty base id")
    seen = taken if isinstance(taken, (set, frozenset)) else set(taken)
    if base not in seen:
        return base
    n = 2
    while f"{base}-{n}" in seen:
        n += 1
    return f"{base}-{n}"


# --------------------------------------------------------------------------- #
# the build lock
# --------------------------------------------------------------------------- #
_HOST = socket.gethostname()

#: paths this process holds, with a nesting depth each. Process-global on
#: purpose: two FileLock objects in one interpreter pointing at the same path
#: are the same lock, and the inner `with` must not delete the outer one's file
#: on the way out.
_HELD_LOCKS: dict[str, int] = {}
_HELD_GUARD = threading.Lock()


def _pid_alive(pid: int) -> bool:
    """Is ``pid`` a live process on THIS machine?

    Every unknown answer is "yes". Stealing a lock from a running build is the
    exact failure this class exists to prevent, so an ambiguous signal (EPERM —
    the process exists but belongs to another user) must never be read as dead.
    """
    if pid <= 0:
        return False
    if sys.platform == "win32":
        # os.kill(pid, 0) on Windows is NOT a liveness probe: CPython routes it
        # to TerminateProcess, so "checking" would kill the holder with exit
        # code 0. There is no cheap stdlib probe that distinguishes dead from
        # access-denied, so Windows says "alive" and lets `stale_after` do the
        # reaping instead.
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True          # exists, owned by another user
    except OSError:
        return True          # unknown failure: assume alive, never steal
    return True


class FileLock:
    """Cooperative inter-process lock backed by a pid file.

    A rebuilt part was once lost to this: a second run started three minutes
    into the first and overwrote ``build/`` underneath it. The part existed,
    then didn't, and nothing said why. So every write-side command takes this
    lock around the whole operation.

    The file is created with ``O_CREAT|O_EXCL`` (atomic on POSIX and on Windows;
    on NFS it is only as atomic as your server, hence ``stale_after``) and holds
    JSON: pid, host, when, and the command that took it, so a blocked user can
    ``cat`` the file and see who to blame.

    Reaping, in order of how much the evidence is worth:

    * the holder pid is THIS process   -> ours, adopt it (a crashed nested run)
    * same host, holder pid is gone    -> reap, that run died
    * same host, holder pid is alive   -> refuse, with the pid in the message
    * other host, or no readable pid   -> liveness is unknowable from here, so
      fall back to age and reap only once ``stale_after`` has passed

    Note what is NOT in that list: a live, same-host holder is never reaped, no
    matter how old the lock is. A tier-2 solver gate can legitimately run for
    hours, and a lock that expires out from under a running build is worse than
    no lock at all. Long runs can call :meth:`touch` to keep the mtime fresh for
    the benefit of watchers.

    Reentrancy is per-process, not per-thread: nesting two ``with`` blocks on
    the same path in one interpreter is fine (the outer one owns the file), but
    this is an inter-PROCESS lock and does not serialise threads. Use a
    ``threading.Lock`` for that.

    pid reuse is the known hole: if the holder died and the OS handed its pid to
    something else, the lock looks alive and the user has to delete it. The
    message says exactly that, which is why it names the pid.
    """

    def __init__(self, path: str | os.PathLike[str], *, stale_after: float = 3600.0) -> None:
        self.path = os.path.abspath(os.fspath(path))
        self.stale_after = float(stale_after)
        self._depth = 0

    # -- introspection ---------------------------------------------------- #
    def __repr__(self) -> str:          # pragma: no cover - diagnostics only
        return f"FileLock({self.path!r}, stale_after={self.stale_after}, depth={self._depth})"

    @property
    def held(self) -> bool:
        """True if THIS object currently holds (or re-entered) the lock."""
        return self._depth > 0

    def holder(self) -> dict[str, Any]:
        """Whatever the lock file says, or ``{}`` if it is absent/unreadable.

        Never raises. A lock file is diagnostic data written by a process that
        may have died mid-write; treating a garbled one as fatal would leave the
        user stuck behind a file nobody can explain.
        """
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                data = json.loads(fh.read() or "{}")
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def age(self) -> float | None:
        """Seconds since the lock file was last written, or None if it is gone.

        Clamped at 0: a clock that moved backwards (or an NFS server whose clock
        is ahead) must not produce a negative age that reads as "brand new".
        """
        try:
            return max(0.0, time.time() - os.stat(self.path).st_mtime)
        except OSError:
            return None

    # -- acquisition ------------------------------------------------------ #
    def acquire(self) -> "FileLock":
        """Take the lock, or raise AtompipeError naming who holds it."""
        with _HELD_GUARD:
            if _HELD_LOCKS.get(self.path):
                _HELD_LOCKS[self.path] += 1
                self._depth += 1
                return self

        ensure_dir(os.path.dirname(self.path) or os.curdir)
        for attempt in range(4):
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            except FileExistsError:
                # Raises if the holder is alive; returns after reaping if not.
                self._reap_or_refuse()
                continue
            except OSError as exc:
                raise AtompipeError(
                    f"cannot create lock file {self.path}: {exc.strerror or exc}"
                ) from exc
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
                json.dump(
                    {
                        "pid": os.getpid(),
                        "host": _HOST,
                        "when": utcnow_iso(),
                        "command": " ".join(sys.argv[:3]),
                    },
                    fh,
                    sort_keys=True,
                )
                fh.write("\n")
                fh.flush()
                os.fsync(fh.fileno())
            with _HELD_GUARD:
                _HELD_LOCKS[self.path] = 1
            self._depth = 1
            return self

        # Four reaps in a row all lost the race to another process: something is
        # actively fighting us and a fifth try would not be better evidence.
        raise AtompipeError(
            f"could not acquire {self.path}: it is being recreated as fast as it is reaped; "
            f"stop the other atompipe runs, then delete the file if it remains"
        )

    def release(self) -> None:
        """Drop one level of nesting; delete the file at the outermost level.

        Releasing a lock this object never took is a no-op rather than an error,
        so ``__exit__`` after a failed ``acquire`` stays harmless.
        """
        if self._depth <= 0:
            return
        self._depth -= 1
        with _HELD_GUARD:
            remaining = _HELD_LOCKS.get(self.path, 0) - 1
            if remaining > 0:
                _HELD_LOCKS[self.path] = remaining
                return
            _HELD_LOCKS.pop(self.path, None)

        # Only unlink what is still ours. If a reaper decided we were dead and
        # another run took the lock, deleting it here would hand a third run a
        # lock the second one is still holding.
        info = self.holder()
        if not info or (info.get("pid") == os.getpid() and info.get("host") == _HOST):
            try:
                os.unlink(self.path)
            except FileNotFoundError:
                pass
            except OSError:
                pass

    def touch(self) -> None:
        """Refresh the lock's mtime so age-based reapers leave a long run alone.

        Only meaningful for runs that outlive ``stale_after`` on shared storage;
        a same-host holder is protected by its pid regardless.
        """
        if self._depth > 0:
            try:
                os.utime(self.path, None)
            except OSError:
                pass

    # -- context manager -------------------------------------------------- #
    def __enter__(self) -> "FileLock":
        return self.acquire()

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()
        return None          # never swallow the body's exception

    # -- internals -------------------------------------------------------- #
    def _reap(self, why: str) -> None:
        """Delete a lock we have decided is dead. Losing the race is fine."""
        try:
            os.unlink(self.path)
        except FileNotFoundError:
            pass             # someone else reaped it first; same outcome
        except OSError as exc:
            raise AtompipeError(
                f"lock {self.path} is stale ({why}) but cannot be removed: {exc.strerror or exc}"
            ) from exc

    def _reap_or_refuse(self) -> None:
        """Decide what an existing lock file means, and act on it."""
        info = self.holder()
        pid = info.get("pid")
        host = info.get("host")
        age = self.age()
        if age is None:
            return           # it vanished while we looked: retry the create

        if isinstance(pid, int) and pid > 0 and host == _HOST:
            if pid == os.getpid():
                # Our own pid, but not in _HELD_LOCKS -> left by an earlier
                # acquire in this interpreter that never released (a crashed
                # nested run, or a `with` skipped by os._exit). Nobody else can
                # be relying on it, so take it back.
                self._reap("left behind by this process")
                return
            if _pid_alive(pid):
                raise AtompipeError(
                    f"another atompipe run (pid {pid}) holds {self.path} — wait for it to finish, "
                    f"or delete that file if the run is gone "
                    f"(held for {human_duration(age)})"
                )
            self._reap(f"holder pid {pid} is gone")
            return

        # No usable pid: either written on another machine (shared/NFS
        # checkout), or the holder died between creating the file and writing
        # its identity into it. Age is the only evidence left.
        if age >= self.stale_after:
            self._reap(f"unverifiable holder, idle for {human_duration(age)}")
            return
        who = f"pid {pid} on host {host!r}" if pid else "an unidentified run"
        raise AtompipeError(
            f"{self.path} is held by {who}; this machine cannot check whether that run is alive. "
            f"It is reaped automatically after {human_duration(self.stale_after)} "
            f"(held for {human_duration(age)}) — or delete the file to reclaim it now"
        )
