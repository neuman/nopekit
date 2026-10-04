# SPDX-License-Identifier: Apache-2.0
"""atompipe.util — the primitives the rest of the spine leans on.

This module imports nothing from atompipe. It is the bottom of the dependency
graph and the one file that must never fail to import, so: standard library
only, no optional extras, no import-time work beyond defining names.

Five things here exist because of specific, expensive failures, not because a
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
* ``FileDigests`` — a limit file changed under a project gate and the claim
  still read PASS, where a re-run failed 19.6 g against a 1 g limit (S-22):
  staleness hashed the digests STORED at ingest, never the bytes, and tampered
  evidence left ``doctor`` saying "staleness unchanged" (S-45). Every file digest
  the spine takes is now of the bytes on disk, behind a stat cache that re-reads
  them whenever the stat cannot rule a change out. It lives here, below
  ``store`` and ``verdicts``, because both need it and ``store`` must not import
  ``verdicts``.

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
from typing import Any, Iterable, NamedTuple


__all__ = [
    "AtompipeError",
    "utcnow_iso",
    "ensure_dir",
    "atomic_write_text",
    "atomic_write_json",
    "read_json",
    "sha256_text",
    "short_hash",
    "canonical_json",
    "seal",
    "FileDigests",
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


def canonical_json(value: Any) -> str:
    """THE canonical JSON form: sorted keys, compact separators, UTF-8 as itself
    (``ensure_ascii=False``), and NaN or Infinity refused (``allow_nan=False``).

    One rule for every digest the spine takes over a JSON value: the verdict
    cache's (``verdicts._canonical_json`` delegates here, byte for byte, so every
    pinned digest holds — ``test_digests``, ``test_spine_digest``) and a physical
    result's seal (``seal``, P2.5a-D6). A float is written as ``repr`` writes it,
    which every CPython since 3.1 writes identically, so a seal made on one
    machine verifies on another (R12 of the P2.5a design). *Rejected:* a second
    canonical form for seals (two forms drift, and one of them would be the
    one a reviewer never reads); ``sort_keys`` alone (``", "`` and ``": "`` move
    with a json default nobody pinned)."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False)


def seal(form: Any) -> str:
    """sha256 hex of ``canonical_json(form)`` — the seal of a physical result or
    an attribution (``store.append_signed``, P2.5a-D6). What a seal is: tamper
    EVIDENCE against drift and a helpful agent's shortcut — a hand edit of a
    recorded result breaks it, and every command then refuses the file. What it
    is not: a lock. Anyone who can write the file can recompute it (D-13's
    stated limit; P3's permission rule on ``results/`` is the lock)."""
    return hashlib.sha256(canonical_json(form).encode("utf-8", "surrogatepass")).hexdigest()


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
# file digests
# --------------------------------------------------------------------------- #
#: Bytes per read while hashing. 1 MiB: a mesh of a few hundred MB is hashed in
#: bounded memory, and past about 64 KiB the chunk size stops mattering to
#: sha256's throughput. Rejected: reading the whole file (a 300 MB STL held in
#: memory to learn 32 bytes about it).
_DIGEST_CHUNK = 1 << 20

#: The cache file's shape. Any other value — or a file that does not parse — is
#: ignored and rebuilt: the cache is a speed-up, never a record, so there is
#: nothing in it to migrate.
_DIGESTS_SCHEMA = 1

_SHA256_HEX = frozenset("0123456789abcdef")


class _Digested(NamedTuple):
    key: tuple[int, int, int, int, int]   # (size, mtime_ns, ctime_ns, ino, dev)
    sha: str
    ref: int | None     # the write time the racy rule compares against; None: no reference yet
    fresh: bool         # hashed by this process since the last save


def _stat_key(st: os.stat_result) -> tuple[int, int, int, int, int]:
    return (st.st_size, st.st_mtime_ns, st.st_ctime_ns, st.st_ino, st.st_dev)


def _racy(entry: _Digested) -> bool:
    """Could the file have changed after it was hashed without its stat moving?

    Yes when it was last touched no earlier than the reference write: in that
    tick a same-size edit keeps size, mtime and ctime, so the key cannot see it.
    """
    if entry.ref is None:
        return True
    _size, mtime_ns, ctime_ns, _ino, _dev = entry.key
    return max(mtime_ns, ctime_ns) >= entry.ref


def _hash_file(path: str) -> tuple[str | None, tuple[int, int, int, int, int] | None]:
    """``(sha256, the stat key after reading)``, or ``(None, None)``.

    Opened non-blocking and checked with ``fstat``: a path that became a FIFO
    between the caller's ``stat`` and this ``open`` must not hang a sweep on a
    read that never returns.
    """
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        fd = os.open(path, flags)
    except OSError:
        return None, None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None, None
        hasher = hashlib.sha256()
        while True:
            chunk = os.read(fd, _DIGEST_CHUNK)
            if not chunk:
                break
            hasher.update(chunk)
        return hasher.hexdigest(), _stat_key(os.fstat(fd))
    except OSError:
        return None, None
    finally:
        os.close(fd)


class FileDigests:
    """sha256 of a file's bytes, behind a stat cache that re-reads them whenever
    the stat cannot rule a change out.

    What slipped through before this existed (S-22, S-45): staleness hashed the
    digest RECORDED when a file was ingested, so a limit file edited under a gate,
    or evidence tampered with after ingest, left every key unchanged and the old
    PASS current. A digest here is always of the bytes on disk; the cache only
    decides when re-reading them can be skipped.

    ``digest(path)`` returns the hex sha256, or ``None`` when there are no bytes
    to read: the path is missing, is not a regular file (a directory, a FIFO —
    never opened, so never hung on), or cannot be read. A relative path resolves
    against the current directory.

    **The key** is ``(size, mtime_ns, ctime_ns, ino, dev)``. Size and mtime alone
    are what an editor that restores timestamps defeats: a same-size edit
    followed by ``os.utime`` back to the old mtime looks untouched to them.
    ``utime`` cannot set ctime, and a write-and-rename (how most editors save)
    changes the inode, so either half catches it.

    **The racy-clean rule**, git's own (Documentation/technical/racy-git.txt),
    against the cache file's mtime rather than its index's: an entry is trusted
    only when its file was last touched strictly before ``written_ns``, the cache
    file's own mtime after its last write. In the tick the cache is written, a
    same-size edit leaves size, mtime and ctime exactly as recorded, and only the
    timestamp comparison can see that the bytes might have moved. There is no
    fixed window: *rejected*, a 2 s window, which an earlier draft attributed to
    git and git does not use — too long on a filesystem with nanosecond stamps
    (every file edited in the last two seconds re-hashed on every call) and still
    arbitrary on one with coarse stamps.

    Two refinements, each a hole in the rule as first written:

    * **ctime counts, not only mtime** (``max(mtime_ns, ctime_ns) >=
      written_ns``). An edit whose mtime was restored to the past, landing in
      the tick the cache was written, keeps a key whose mtime is old and whose
      ctime IS that tick; mtime alone calls it clean. Costs nothing on a normal
      tree, where ctime is the file's last write or checkout.
    * **A racy entry is never re-saved under a newer write time.** What slipped
      through while writing this: a process that loads a racy entry and never
      asks for that file would write it back, and the next load would judge it
      against the NEW, later mtime and trust it — the stale digest laundered by
      one idle save. ``save`` keeps only entries hashed in this process or still
      clean against their own reference (git smudges racily clean entries on
      index write for the same reason).

    Entries hashed in this process are judged against the last write this
    process knows of (the loaded cache's, or its own last ``save``) — a time
    before the hash, so a same-tick edit after hashing is still caught. With no
    cache file yet, nothing hashed in this process is trusted until the first
    ``save``: correct first, fast from the second run on.

    ``save()`` is best-effort and returns whether it wrote: a read-only checkout
    or a full disk costs the next run some hashing, never a result. The file is
    untracked (``.atompipe/cache/digests.json``) and machine-specific — inode
    numbers mean nothing on another machine — so it holds absolute paths.

    Named residuals, both git's too. An edit landing in the same timestamp tick
    as the HASH, with the cache then written in a later tick, leaves a key the
    next process trusts: only a concurrent writer can do that (a gate's reads are
    digested after it returns, and the key is re-checked after reading), and until
    its next save this process judges it against the earlier reference and
    re-hashes it. And a cache file
    whose mtime is in the future (a skewed clock) trusts everything older than
    that future.
    """

    def __init__(self, cache_path: str | os.PathLike[str] | None = None) -> None:
        self.cache_path = None if cache_path is None else os.path.abspath(os.fspath(cache_path))
        self._entries: dict[str, _Digested] = {}
        self._written_ns: int | None = None
        self._dirty = False
        if self.cache_path is not None:
            self._load()

    def __repr__(self) -> str:          # pragma: no cover - diagnostics only
        return f"FileDigests({self.cache_path!r}, entries={len(self._entries)})"

    # -- the one question ------------------------------------------------- #
    def digest(self, path: str | os.PathLike[str]) -> str | None:
        """The sha256 of ``path``'s bytes now, or ``None`` when it has none."""
        target = os.path.abspath(os.fspath(path))
        try:
            st = os.stat(target)
        except (OSError, ValueError):       # missing; or a NUL byte, which no file has
            self._forget(target)
            return None
        if not stat.S_ISREG(st.st_mode):
            self._forget(target)
            return None
        key = _stat_key(st)
        entry = self._entries.get(target)
        if entry is not None and entry.key == key and not _racy(entry):
            return entry.sha
        sha, after = _hash_file(target)
        if sha is None:
            self._forget(target)
            return None
        if after == key:
            self._entries[target] = _Digested(key, sha, self._written_ns, True)
            self._dirty = True
        else:
            # The file moved while it was read: these bytes may be torn, so they
            # are an answer for this call and never a cache entry.
            self._forget(target)
        return sha

    # -- persistence ------------------------------------------------------ #
    def save(self) -> bool:
        """Write the cache if anything changed; ``True`` when it was written.

        Best-effort: every failure returns ``False`` and leaves the in-memory
        entries as they were.
        """
        if self.cache_path is None or not self._dirty:
            return False
        keep = {path: entry for path, entry in self._entries.items()
                if entry.fresh or not _racy(entry)}
        payload = {
            "schema": _DIGESTS_SCHEMA,
            "files": {path: [*entry.key, entry.sha] for path, entry in sorted(keep.items())},
        }
        try:
            # Compact on purpose: untracked and never reviewed as a diff, and
            # atomic_write_json's indent would put every integer on its own line.
            atomic_write_text(self.cache_path,
                              json.dumps(payload, separators=(",", ":"), allow_nan=False))
            written = os.stat(self.cache_path).st_mtime_ns
        except (AtompipeError, OSError, ValueError):
            return False
        self._entries = {path: entry._replace(ref=written, fresh=False)
                         for path, entry in keep.items()}
        self._written_ns = written
        self._dirty = False
        return True

    def _forget(self, target: str) -> None:
        if self._entries.pop(target, None) is not None:
            self._dirty = True

    def _load(self) -> None:
        """Read the cache; anything unreadable is simply not cached.

        ``written_ns`` comes from ``fstat`` on the descriptor the entries are read
        through, so a save racing this load cannot pair these entries with the
        other file's (later) write time.
        """
        try:
            fd = os.open(str(self.cache_path), os.O_RDONLY | getattr(os, "O_BINARY", 0))
        except OSError:
            return
        try:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode):
                return
            chunks = []
            while True:
                chunk = os.read(fd, _DIGEST_CHUNK)
                if not chunk:
                    break
                chunks.append(chunk)
        except OSError:
            return
        finally:
            os.close(fd)
        # The file exists and was written at st_mtime_ns, which is before
        # anything this process will hash: a valid reference even when its
        # content turns out to be unusable.
        self._written_ns = st.st_mtime_ns
        try:
            data = json.loads(b"".join(chunks).decode("utf-8"),
                              parse_constant=lambda token: None)
        except (ValueError, UnicodeDecodeError):
            return
        if not isinstance(data, dict) or data.get("schema") != _DIGESTS_SCHEMA:
            return
        files = data.get("files")
        if not isinstance(files, dict):
            return
        for path, row in files.items():
            if not (isinstance(row, list) and len(row) == 6
                    and all(type(v) is int for v in row[:5])
                    and isinstance(row[5], str) and len(row[5]) == 64
                    and set(row[5]) <= _SHA256_HEX):
                continue
            self._entries[path] = _Digested(tuple(row[:5]), row[5], st.st_mtime_ns, False)


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
