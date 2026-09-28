# SPDX-License-Identifier: Apache-2.0
"""atompipe.verdicts — what a gate read, so that its verdict can be keyed by it.

A verdict is only as current as the inputs it consumed. Until Phase 1.2 the spine
had no idea what those were: staleness was ONE hash of the whole projection plus
one hash of every ingested input, so a comment edit in the model re-ran
everything and a limit file edited under a project gate re-ran nothing (S-22).
This module is the single home of the answer — the per-gate read set, and the
content address (rho) built from it. Its first half is the primitives:

* ``ParamTrace`` — ``ctx.params`` as a gate sees it: every leaf read recorded as
  ``(path, digest)``, every bulk read recorded as a dependency on the whole
  level, and every write refused. What slipped through before it:
  ``ctx.params`` was one mutable dict shared by every gate in a sweep, so gate A
  could forge gate B's inputs (S-24 — ``ctx.ledger`` already had a defensive
  copy for exactly that attack; ``params`` did not), and ``cli._ParamReads``
  recorded nothing for ``dict(p)``, ``{**p}``, ``json.dumps(p)``, ``.items()``,
  ``repr`` or ``deepcopy``, so a gate that read its inputs in bulk would have
  been permanently fresh (S-25).
* ``LedgerView`` and ``ModelProxy`` — the other two ways a gate reaches the
  project. openmodelica reads a claim's limit through ``ctx.ledger`` (S-23), so
  that read is an input too.
* ``digest_value`` and ``Anchors`` — a digest of a value that is the same in
  every checkout. Fixtures set absolute mesh and ``.mo`` paths, and a raw digest
  of those would write a new tracked entry per clone and per run (packs:H6).
* ``tracing`` and the audit hook — the files a gate opened, the directories it
  listed, the processes it started. One process-global hook, routed to a stack
  of traces, with the interpreter's own noise excluded: the first mesh gate of
  a sweep opened 719 ``.pyc`` files and listed 77 directories on import alone
  (packs:H1), none of which it decided anything on.
* ``spine_digest`` — the spine's own version, taken from what the verdict-path
  modules SAY rather than from ``__version__``. 1e09113 changed verdict semantics
  (a NaN that read ``[ok]`` now errors) with the version string untouched (S-29).

The second half turns a trace into something you can commit:

* ``code_digest`` — the code a verdict came from: the recorded closure of the
  gate's module, the ``atompipe.*`` modules it imports that the spine digest
  does not cover, and the spec fields that shape a verdict. A gate registered
  from Python with no recorded closure is digested as its defining file; one
  with no file at all is opaque, never a digest of nothing.
* ``Reads.from_trace`` — a trace classified into what rho keys on (params,
  files, directory listings, claims, the model) and the opaque channels that
  make an entry never Fresh.
* ``rho`` and the entry files — ``.atompipe/verdicts/<gate>/<rho16>-<out8>.json``,
  written once, byte-identical from every checkout, read back strictly. Only a
  gate that ran and passed or failed is cached; a skip or a crash is
  ``remember``-ed, keyed by the rho it superseded, and is never evidence.
* control entries — the demonstration that a gate can fail, keyed by
  ``rho_control``: the gate's code, its owner's ``selftest/`` walk, the control's
  own reads. The fixture's code closure rides along as a lookup hint and is NOT
  an input.
* obs — what each run cost, untracked, gate runs and control runs in separate
  files (S-31).

Its third half judges what is recorded against what is on disk now, without
running anything:

* ``freshness`` — per gate, Fresh, Stale (with the moves that made it so, in
  words: ``config.bed_xy 220.0 -> 250.0``), Unknown or never run, by
  recomputing each entry's rho from current digests. Staleness was one hash of
  the whole projection: a model that did not import turned it off (S-21), and
  ingesting one unread file staled every measurable claim (S-33).
* ``admission_state`` — is the gate's control demonstrated at its current
  version, from the control entries alone (PD-08, X14).
* ``resolve`` — the ONE effective-verdict producer every reader uses (R-5):
  availability, remembered crashes, Fresh entries under admission, the latest
  entry marked stale, legacy ledger rows, orphans.

Its fourth half is what ``check`` runs:

* ``admission`` — the same judgement, allowed to run what the records cannot
  settle: a fixture alone when only its code moved (the values it built are
  compared with the control's recorded reads — equal, and nothing else runs and
  nothing tracked is written), the whole control on a miss. Project fixtures
  get the known-good design (``known_good_context``), so the identity fixture
  that certified nothing (S-07) passes its own known-bad input and is not
  admitted; a model edit that defuses a control misses its entry (S-19).
* ``sweep`` — per selected gate: availability, admission, the cache, the run.
  A logger's PASS never counts (S-05); a filtered first sweep goes stale like
  any other (S-20); a dry one writes nothing and reads nothing stale (S-32).
* ``write_last_check``, ``WATCHED``, ``fingerprint`` — the full sweep's summary
  for readers that must not import a project's code.

None of it reads the wall clock (``test_meta``) — a ``when`` arrives from the
CLI edge — takes the build lock, or imports ``gates`` at module level: ``gates``
imports this module, so anything here that needs a gate type receives the
object, and ``resolve``, ``admission`` and ``sweep`` reach ``gates`` from inside
the function.

Imports: ``models``, ``util``, ``store``, ``modelio`` and ``vcs``, standard
library otherwise, including every function-local import (CI's AST walk).
"""
from __future__ import annotations

import ast
import contextlib
import copy
import dataclasses
import enum
import functools
import hashlib
import importlib.metadata
import importlib.util
import json
import math
import numbers
import os
import re
import shutil
import sys
import tempfile
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, ClassVar, Collection, Iterable, Mapping

from . import modelio, store, vcs
from .models import Ledger, Locator, Tier, Verdict
from .util import AtompipeError, FileDigests, atomic_write_json, atomic_write_text


__all__ = [
    "ABSENT", "PRESENT", "SPINE_MODULES", "SMALL_VALUE_MAX_CHARS",
    "digest_value", "small_value", "portable", "traced_context", "tracing",
    "spine_digest", "canonical_ast_digest",
    "Anchors", "ParamTrace", "LedgerView", "ModelProxy", "GateTrace",
    "GateInputWriteError",
    # part two: rho, entries, controls, remembered outcomes, obs (U16)
    "SCHEMA", "RHO_CHARS", "OUT_CHARS", "OBS_KEEP", "SPEC_FIELDS_IN_RHO",
    "CONTROL_OUT_DIR", "TWO_OUTCOMES_IS_ERROR",
    "CodeRef", "Reads", "Entry", "ControlEntry", "WriteResult",
    "anchors_for", "code_digest", "model_digest", "rho", "rho_control", "out8",
    "instruments_for", "write_entry", "read_entries", "record_verdict",
    "selftest_walk", "control_static", "write_control", "read_controls",
    "record_control", "remember", "remembered", "forget", "record_obs", "read_obs",
    "last_read_sets",
    # part three: freshness, admission state, the one resolver (U19)
    "MAX_STALE_REASONS", "Fresh", "Stale", "Unknown", "Never", "freshness",
    "Admission", "admission_state", "Row", "Resolution", "resolve",
    # part four: admission at its current version, the sweep, last_check (U20)
    "CONTROLS_CACHE", "WATCHED", "known_good_context", "admission", "SweepRow",
    "SweepResult", "sweep", "write_last_check", "watched_paths", "fingerprint",
]


# --------------------------------------------------------------------------- #
# constants
# --------------------------------------------------------------------------- #
#: The digest recorded for a key a gate asked for and did not find. A tagged hash
#: rather than JSON ``null``, because "the model has no ``span_mm``" and "the
#: model says ``span_mm = None``" are two different inputs: the gate falls back
#: to its default in one and compares against ``None`` in the other. *Rejected:*
#: ``digest_value(None)`` (one input where there are two); the empty string (a
#: digest of nothing reads as a digest).
ABSENT = hashlib.sha256(b"atompipe:absent\x00").hexdigest()

#: The digest recorded for ``key in ctx.params`` when the key is there. A
#: membership test is a read of PRESENCE only: ``GateContext.param`` asks ``in``
#: before it reads, and a gate that only branches on presence must not go stale
#: when the value moves. Superseded by the value's own digest when the gate then
#: reads the value.
PRESENT = hashlib.sha256(b"atompipe:present\x00").hexdigest()

#: The modules whose SEMANTICS shape a verdict: the types, the runner, the model
#: loader and this file. ``spine_digest`` walks exactly these. *Rejected:* adding
#: ``site`` (Phase 4 rewrites it; every entry in every project would go stale for
#: a page change — pack gates that import it get it through their own code
#: closure instead); the whole spine (a report wording change would re-run every
#: gate); the version string (S-29).
SPINE_MODULES = ("models.py", "gates.py", "modelio.py", "verdicts.py")

#: A string param value up to this many characters is written into a verdict
#: entry beside its digest, so a stale reason can say ``config.bed_xy 220.0 ->
#: 250.0`` instead of "changed". Any bool, int or finite float is small. Why 80:
#: a display value must fit one ``status`` line, and 80 is the width the check
#: column already assumes. *Rejected:* every value (meshes and BOMs into tracked
#: entries); none (a stale line could not say what moved); 64 (no evidence either
#: way, and 80 is the width already in use).
SMALL_VALUE_MAX_CHARS = 80

#: Salts. A version tag inside every digest this module produces, so a future
#: change of canonical form can never collide with an old digest of the same
#: bytes — it produces a new digest, and every entry keyed by the old one reads
#: stale rather than fresh.
_VALUE_SALT = b"atompipe-v1:"
_AST_SALT = b"atompipe-ast-v1:"
_SPINE_SALT = b"atompipe-spine-v1:"

#: How deep ``digest_value`` follows containers before calling the value opaque.
#: A self-containing list would otherwise recurse until Python's own limit fires
#: somewhere inside a gate's stack. Projection values are a handful of levels
#: deep; 200 is far past any real one and far below the interpreter's 1000.
_MAX_DEPTH = 200

_MISSING = object()
_LEVEL = object()


class GateInputWriteError(AtompipeError):
    """A gate tried to write ``ctx.params``.

    One sweep hands every gate the same projection. Before Phase 1.2 it was one
    mutable dict, and a gate that assigned ``ctx.params["load_n"] = 0`` changed
    the input of every gate after it (S-24) — a forgery no verdict could show.
    The gate's view is now read-only; this error is what a write becomes, and
    ``run_gate`` reports it like any other crash: an error, never a pass.
    """


# --------------------------------------------------------------------------- #
# anchors: paths that are the same in every checkout
# --------------------------------------------------------------------------- #
def _is_fs_root(path: str) -> bool:
    """True for ``/`` or a drive root: never an anchor (it would match everything)."""
    return os.path.dirname(path) == path


@dataclass(frozen=True)
class Anchors:
    """The directories whose absolute spelling differs per checkout, per machine
    and per run — and the portable token each one becomes.

    A verdict entry is a tracked file. If it held ``/home/a/proj/model/x.stl`` it
    would differ between two clones of one project, and a digest over that
    string would differ too: every check in every clone would write a new entry
    for an input that did not move. omc's error text embeds absolute ``.mo``
    paths (packs:H9), 33 of 54 bundled gates cite absolute evidence (packs:H5),
    and fixtures set absolute mesh paths in params (packs:H6).

    Tokens, matched longest-first so a pack under ``.atompipe/packs`` wins over
    the root it sits in: the project root ``<root>``; each pack dir
    ``<pack:NAME>``; the sweep ``out_dir`` ``<out>``; the control ``out_dir``
    ``<out:controls>``; ``tempfile.gettempdir()`` ``<tmp>``; the home directory
    ``~``. Each is also matched by its ``realpath`` (macOS's ``/var`` is
    ``/private/var``). A filesystem root is never an anchor. ``packs`` takes
    ``{name: dir}`` or ``((name, dir), ...)`` and is stored as sorted pairs.
    """

    root: str = ""
    packs: Any = ()
    out: str = ""
    controls_out: str = ""
    tmp: str = field(default_factory=tempfile.gettempdir)
    home: str = field(default_factory=lambda: os.path.expanduser("~"))

    def __post_init__(self) -> None:
        raw = self.packs.items() if isinstance(self.packs, Mapping) else (self.packs or ())
        packs = tuple(sorted((str(name), str(path)) for name, path in raw))
        object.__setattr__(self, "packs", packs)
        order = [(self.root, "<root>")]
        order += [(path, f"<pack:{name}>") for name, path in packs]
        order += [(self.out, "<out>"), (self.controls_out, "<out:controls>"),
                  (self.tmp, "<tmp>"), (self.home, "~")]
        token_of: dict[str, str] = {}
        for rank, (path, token) in enumerate(order):
            if not path:
                continue
            for spelling in (os.path.normpath(os.path.abspath(path)),
                             os.path.normpath(os.path.realpath(path))):
                if spelling and not _is_fs_root(spelling):
                    token_of.setdefault(spelling, token)
        pairs = tuple(sorted(token_of.items(), key=lambda kv: (-len(kv[0]), kv[0])))
        object.__setattr__(self, "_pairs", pairs)
        pattern = None
        if pairs:
            # A match must start where a path can start and end where a path
            # component ends: "/w/proj" must not rewrite "/w/project2" or
            # "/x/w/proj". Longest alternative first, so the regex engine picks
            # "<out>" over "<root>" at the same position.
            alternatives = "|".join(re.escape(spelling) for spelling, _ in pairs)
            pattern = re.compile(r"(?<![\w.\-/\\~])(?:" + alternatives + r")(?![\w.\-])")
        object.__setattr__(self, "_pattern", pattern)
        object.__setattr__(self, "_token_of", dict(pairs))

    def pairs(self) -> tuple[tuple[str, str], ...]:
        """``(absolute spelling, token)``, longest spelling first."""
        return self._pairs                                     # type: ignore[attr-defined]

    def portable(self, text: str) -> str:
        """``text`` with every anchored absolute path rewritten to its token.

        For free text — a gate's ``detail``, an omc error message — as well as a
        bare path. A path outside every anchor is left exactly as written; the
        caller decides whether that is acceptable (evidence: no, see
        :meth:`evidence`).
        """
        pattern = self._pattern                                # type: ignore[attr-defined]
        if pattern is None or not text:
            return text
        tokens = self._token_of                                # type: ignore[attr-defined]
        return pattern.sub(lambda m: tokens[m.group(0)], text)

    def portable_path(self, path: Any) -> str | None:
        """The portable spelling of one path, or ``None`` when it has none.

        A relative path is already portable (posix separators). An absolute path
        under an anchor becomes ``<token>/<rest>``; an absolute path under no
        anchor returns ``None`` — it names a place on one machine only.
        """
        try:
            text = os.fsdecode(os.fspath(path))
        except TypeError:
            return None
        if not os.path.isabs(text):
            return text.replace(os.sep, "/")
        norm = os.path.normpath(text)
        for spelling, token in self.pairs():
            if norm == spelling:
                return token
            if norm.startswith(spelling + os.sep):
                return token + "/" + norm[len(spelling) + 1:].replace(os.sep, "/")
        return None

    def evidence(self, paths: Iterable[Any]) -> list[str]:
        """``paths`` made portable, dropping every absolute path under no anchor.

        A tracked entry that cites ``/home/a/tmp/plot.png`` cites a file no other
        machine has; the live row still shows it (SF PD-10), the entry does not.
        """
        out: list[str] = []
        for path in paths or ():
            spelled = self.portable_path(path)
            if spelled is not None:
                out.append(spelled)
        return out


def portable(text: str, anchors: Anchors | None) -> str:
    """``text`` with anchored absolute paths tokenised; unchanged without anchors."""
    if anchors is None or not text:
        return text
    return anchors.portable(text)


# --------------------------------------------------------------------------- #
# value digests
# --------------------------------------------------------------------------- #
class _TooDeep(Exception):
    """A container nested past ``_MAX_DEPTH``, or containing itself."""


def _type_name(value: Any) -> str:
    kind = type(value)
    return f"{kind.__module__}.{kind.__qualname__}"


def _float_form(number: float) -> Any:
    """A finite float as itself; NaN and infinities as a tagged dict.

    The bracket's ``build()`` returns ``inf`` for a stiffness it cannot bound
    (packs:H13). Strict JSON refuses it, and calling it opaque would make every
    gate that reads it permanently stale for a perfectly well-defined value.
    """
    if math.isfinite(number):
        return number
    if math.isnan(number):
        return {"$float": "nan"}
    return {"$float": "inf" if number > 0 else "-inf"}


def _scalar_item(value: Any) -> Any:
    """``value.item()`` for a 0-d numpy-like (duck-typed: the spine never imports
    numpy), else ``_MISSING``."""
    try:
        if getattr(value, "shape", None) == () and hasattr(value, "dtype"):
            item = getattr(value, "item", None)
            if callable(item):
                return item()
    except Exception:
        pass
    return _MISSING


def _key_form(key: Any) -> str:
    """A dict key as a JSON key. A user key that starts with ``$`` is escaped to
    ``$$...``, so ``{"$float": "inf"}`` written by a model can never collide
    with the tag for an infinity; a non-string key becomes ``$k:<repr>``."""
    if isinstance(key, str):
        text = str.__str__(key)
        return "$" + text if text.startswith("$") else text
    if isinstance(key, numbers.Integral) and not isinstance(key, bool):
        return f"$k:{int(key)!r}"
    return f"$k:{key!r}"


def _path_form(text: str, anchors: Anchors | None) -> str:
    """A string that is an absolute path under an anchor, in its portable form."""
    if anchors is not None and text and "\n" not in text and os.path.isabs(text):
        spelled = anchors.portable_path(text)
        if spelled is not None:
            return spelled
    return text


def _tagged(value: Any, anchors: Anchors | None, opaque: list[str], depth: int) -> Any:
    """``value`` as plain JSON, with every non-JSON part tagged."""
    if depth > _MAX_DEPTH:
        raise _TooDeep()
    if value is None or value is True or value is False:
        return value
    kind = type(value)
    if kind is str:
        return _path_form(value, anchors)
    if kind is int:
        return value
    if kind is float:
        return _float_form(value)
    if isinstance(value, dict):
        # dict.items, never value.items(): digesting a ParamTrace must not record
        # a read on it.
        return {_key_form(k): _tagged(v, anchors, opaque, depth + 1)
                for k, v in dict.items(value)}
    if isinstance(value, (list, tuple)):
        return [_tagged(v, anchors, opaque, depth + 1) for v in value]
    if isinstance(value, bool):
        return bool(value)
    if isinstance(value, str):                     # str subclasses: StrEnum values
        return _path_form(str.__str__(value), anchors)
    if isinstance(value, float):                   # float subclasses: numpy.float64
        return _float_form(float(value))
    if isinstance(value, int):                     # int subclasses: IntEnum
        return int(value)
    item = _scalar_item(value)
    if item is not _MISSING and item is not value:
        return _tagged(item, anchors, opaque, depth + 1)
    if isinstance(value, numbers.Integral):
        return int(value)
    if isinstance(value, numbers.Real):
        return _float_form(float(value))
    name = _type_name(value)
    opaque.append(name)
    return {"$opaque": name}


def _canonical_json(form: Any) -> str:
    return json.dumps(form, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False)


def _digest(value: Any, anchors: Anchors | None = None) -> tuple[str, list[str]]:
    """``(digest, opaque type names)``. The names are what the caller turns into
    ``param <path>: <Type> is not JSON`` channels."""
    opaque: list[str] = []
    try:
        text = _canonical_json(_tagged(value, anchors, opaque, 0))
    except Exception:
        name = _type_name(value)
        opaque = [name]
        text = _canonical_json({"$opaque": name})
    data = _VALUE_SALT + text.encode("utf-8", "surrogatepass")
    return hashlib.sha256(data).hexdigest(), opaque


def digest_value(value: Any, anchors: Anchors | None = None) -> str:
    """sha256 of the canonical JSON of ``value``'s tagged form. Never raises.

    Canonical: ``sort_keys``, compact separators, ``ensure_ascii=False``,
    ``allow_nan=False``, salted with ``atompipe-v1:``. Tagged: NaN and the
    infinities become ``{"$float": ...}``; tuples become lists; a 0-d
    numpy-like becomes its ``.item()``; a non-string dict key becomes
    ``"$k:<repr>"``; an absolute path under an anchor becomes its portable form
    (``<root>/model/x.stl``), so two checkouts of one project digest one value
    equally. Anything else that is not JSON — a mesh object in ``extra``, a set,
    a self-containing list — becomes ``{"$opaque": "<module>.<qualname>"}``: a
    digest of its TYPE only, which is why an entry that read one is never Fresh.
    """
    return _digest(value, anchors)[0]


def small_value(value: Any, anchors: Anchors | None = None) -> tuple[bool, Any]:
    """``(True, display)`` when ``value`` is small enough to write into an entry
    beside its digest (``SMALL_VALUE_MAX_CHARS``), else ``(False, None)``.

    A pair rather than a sentinel because ``None`` is itself a small value.
    """
    item = _scalar_item(value)
    if item is not _MISSING:
        value = item
    if value is None:
        return True, None
    if isinstance(value, bool):
        return True, bool(value)
    if isinstance(value, float):
        number = float(value)
        return (True, number) if math.isfinite(number) else (False, None)
    if isinstance(value, int):
        return True, int(value)
    if isinstance(value, str):
        text = _path_form(str.__str__(value), anchors)
        return (True, text) if len(text) <= SMALL_VALUE_MAX_CHARS else (False, None)
    if isinstance(value, numbers.Integral):
        return True, int(value)
    if isinstance(value, numbers.Real):
        number = float(value)
        return (True, number) if math.isfinite(number) else (False, None)
    return False, None


# --------------------------------------------------------------------------- #
# the trace
# --------------------------------------------------------------------------- #
@dataclass(eq=False)
class GateTrace:
    """Everything one gate (or one control: fixture plus gate) read.

    Filled by the views ``traced_context`` hands the gate and by the audit hook
    while ``tracing(trace)`` is open; turned into an entry's read set by
    ``Reads.from_trace`` (U16). Identity-compared: two empty traces are two
    traces, and the routing stack pops by identity.

    ``params`` maps a tuple path to the digest read there (``ABSENT``,
    ``PRESENT`` or a value/whole-level digest); ``values`` holds the small
    display values of leaf reads; ``whole`` the paths read in bulk (``()`` is
    the top level). ``ledger`` maps ``"claim:<id>"`` or a list name
    (``"claims"``) to a digest. ``files_read`` is in first-read order and never
    holds a path this window wrote first; ``host_reads`` is what a control's
    fixture — or anyone reading through its host view — read from the HOST
    context (the seal detector's input). ``anchors`` makes path-valued param
    digests portable; the sweep sets it.
    """

    kind: str = "gate"
    params: dict = field(default_factory=dict)
    values: dict = field(default_factory=dict)
    whole: set = field(default_factory=set)
    ledger: dict = field(default_factory=dict)
    files_read: list = field(default_factory=list)
    files_written: set = field(default_factory=set)
    dirs: set = field(default_factory=set)
    opaque: set = field(default_factory=set)
    model_used: bool = False
    host_reads: dict = field(default_factory=dict)
    fixture_code: Any = None
    anchors: Any = None
    _read_set: set = field(default_factory=set, init=False, repr=False)
    _host_whole: set = field(default_factory=set, init=False, repr=False)

    def self_modified(self) -> list[str]:
        """Paths this window read and THEN wrote, in read order.

        Their pre-write bytes are gone once the gate returns, so no digest taken
        afterwards is the input the gate saw: classification names each as an
        opaque ``self-modified:`` channel. (A path written first and read after
        is the gate's own output and never reaches ``files_read``.)
        """
        return [path for path in self.files_read if path in self.files_written]

    # -- recording, for the views and the hook ------------------------------ #
    def _note_read(self, path: str) -> None:
        if path in self.files_written or path in self._read_set:
            return
        self._read_set.add(path)
        self.files_read.append(path)

    def _note_write(self, path: str) -> None:
        self.files_written.add(path)


def _merge(table: dict, path: tuple, digest: str) -> None:
    """First read wins, except that a value supersedes a presence read."""
    old = table.get(path)
    if old is None or (old == PRESENT and digest not in (PRESENT, ABSENT)):
        table[path] = digest


def _dotted(path: tuple) -> str:
    return ".".join(str(part) for part in path) or "(all params)"


def _render(path: tuple) -> str:
    return "ctx.params" + "".join(f"[{part!r}]" for part in path)


# --------------------------------------------------------------------------- #
# ParamTrace
# --------------------------------------------------------------------------- #
class _HostLink:
    """Where reads through a fixture's host view are recorded, and which paths
    the fixture has written (reads of those are its own values, not the host's).
    Shared by every view derived from one host view."""

    __slots__ = ("trace", "written", "path")

    def __init__(self, trace: Any, written: set, path: tuple) -> None:
        self.trace = trace
        self.written = written
        self.path = path

    def child(self, key: Any) -> "_HostLink":
        return _HostLink(self.trace, self.written, self.path + (key,))

    def covers(self, path: tuple) -> bool:
        written = self.written
        return any(path[:i] in written for i in range(len(path) + 1))


def _raw(view: dict) -> dict:
    """A shallow copy of a dict subclass's STORAGE, recording nothing.

    What slipped through while writing this: ``dict.copy(view)`` is not a raw
    copy. CPython's ``PyDict_Copy`` takes its fast path only when ``tp_iter`` is
    dict's own; for a ``ParamTrace`` it merges through ``keys()`` and
    ``__getitem__`` — a whole-level read plus a read of every key, recorded on
    the host every time a gate view was built over one. Iterating
    ``dict.items(view)`` walks the storage in C.
    """
    return dict(dict.items(view))


def _plain(value: Any, memo: dict | None = None) -> Any:
    """A deep copy made of plain dicts and lists, cycle-safe. Leaves are shared:
    projection leaves are immutable scalars (a mutable non-JSON leaf is the named
    residual — see ``ParamTrace``)."""
    if memo is None:
        memo = {}
    key = id(value)
    if key in memo:
        return memo[key]
    if isinstance(value, dict):
        out: dict = {}
        memo[key] = out
        for k, v in dict.items(value):
            out[k] = _plain(v, memo)
        return out
    if isinstance(value, list):
        items: list = []
        memo[key] = items
        items.extend(_plain(v, memo) for v in value)
        return items
    if isinstance(value, tuple):
        return tuple(_plain(v, memo) for v in value)
    return value


class ParamTrace(dict):
    """``ctx.params`` as a gate sees it: recorded, and read-only.

    ``ParamTrace(data, trace, *, path=(), readonly=True, host=False)``.

    **What each access records** on ``trace``:

    * a leaf read (``p[k]``, ``p.get(k)``) — ``(path, digest_value(v))``, plus a
      display value when small. A list leaf comes back as a copy, so a list can
      never become a cross-gate write channel.
    * a nested dict read — nothing: it returns a nested view at the extended
      path, identity-stable. A whole-value read here would stale
      ``bracket.deflection`` on every Config edit (packs:H11).
    * a miss — ``(path, ABSENT)``. ``k in p`` — presence only, ``PRESENT`` or
      ``ABSENT``.
    * any bulk access — ``__iter__``, ``keys``, ``values``, ``items``, ``len``,
      ``bool``, ``==``, ``!=``, ``repr``, ``copy``, ``copy.copy``,
      ``copy.deepcopy``, pickling, ``|`` either side, ``reversed`` — the digest of
      the WHOLE level, and the path goes into ``trace.whole``. ``__iter__`` has
      to be overridden for this to hold: CPython's dict-merge fast path reads a
      dict subclass's storage directly for ``dict(p)``, ``{**p}`` and
      ``f(**p)`` unless ``tp_iter`` is overridden, and those three were exactly
      the silent reads (S-25).

    Copies come back as plain dicts, never as a ``ParamTrace``: a subclass
    deepcopy would rebuild itself through ``__setitem__``, which raises.

    **Writes** (``__setitem__``, ``__delitem__``, ``update``, ``pop``,
    ``popitem``, ``clear``, ``setdefault``, ``|=``) raise ``GateInputWriteError``
    when ``readonly`` (S-24).

    **Host views** (``host=True``, which ``traced_context(..., readonly=False)``
    builds for a control's fixture) are writable private copies — a fixture's
    edit never reaches the sweep context — and record into
    ``trace.host_reads`` instead of ``trace.params``. A view built over a host
    view (the gate running on a context its fixture returned unchanged, or on
    ``dict(host.params)`` with one value layered on) keeps recording host reads
    too: ANY read through the host's data is a host read, except of a path the
    fixture itself wrote.

    It stays a ``dict`` subclass because ``GateContext._exact`` checks
    ``isinstance(x, dict)`` (R-4: no bundled gate checks ``type(x) is dict``).
    Named residuals: an explicit ``dict.__getitem__(p, k)`` bypasses the
    recording (children are still views), and a mutable non-JSON leaf (an array
    in a fixture-built context) is returned by reference.
    """

    __slots__ = ("_trace", "_path", "_readonly", "_host", "_children")

    def __init__(self, data: Any = None, trace: GateTrace | None = None, *,
                 path: tuple = (), readonly: bool = True, host: bool = False) -> None:
        link = None
        if isinstance(data, ParamTrace):
            link = data._host
            raw: Any = _raw(data)
            if not path:
                path = data._path
        else:
            raw = {} if data is None else data
        if not readonly:
            raw = _plain(raw)
        dict.__init__(self, raw)
        self._path = tuple(path)
        self._readonly = bool(readonly)
        self._children: dict = {}
        if host:
            self._trace = None
            self._host = _HostLink(trace, set(), self._path)
        else:
            self._trace = trace
            self._host = link

    # -- recording ---------------------------------------------------------- #
    def _record(self, key: Any, value: Any = _MISSING, marker: str = "",
                whole: bool = False) -> None:
        """Record one read at ``self._path + (key,)`` — or of the whole level —
        as a gate read on ``self._trace`` and/or a host read through the link.

        The two sides are told apart by ROLE, never by comparing traces: a
        control's fixture and its gate share one trace, so the gate side and the
        host side are usually the same object.
        """
        suffix = () if whole else (key,)
        jobs: list[tuple[GateTrace, tuple, bool]] = []
        trace = self._trace
        if trace is not None:
            path = self._path + suffix
            if not (whole and path in trace.whole):
                jobs.append((trace, path, True))
        link = self._host
        if link is not None and link.trace is not None:
            hpath = link.path + suffix
            if not link.covers(hpath) and not (whole and hpath in link.trace._host_whole):
                jobs.append((link.trace, hpath, False))
        for target, where, gate_side in jobs:
            table = target.params if gate_side else target.host_reads
            if marker:
                _merge(table, where, marker)
                continue
            if not whole and table.get(where, PRESENT) != PRESENT:
                continue                        # first read wins; a gate loop re-reads a lot
            digest, opaque = _digest(self if whole else value, target.anchors)
            _merge(table, where, digest)
            for name in dict.fromkeys(opaque):
                target.opaque.add(f"param {_dotted(where)}: {name} is not JSON")
            if not gate_side:
                if whole:
                    target._host_whole.add(where)
            elif whole:
                target.whole.add(where)
            else:
                small, shown = small_value(value, target.anchors)
                if small:
                    target.values.setdefault(where, shown)

    def _whole(self) -> None:
        self._record(_LEVEL, whole=True)

    def _child(self, key: Any, value: dict) -> "ParamTrace":
        child = self._children.get(key)
        if child is not None:
            return child
        if isinstance(value, ParamTrace):
            link = value._host
            if link is None and self._host is not None:
                link = self._host.child(key)
            data: Any = _raw(value)
        else:
            link = self._host.child(key) if self._host is not None else None
            data = value
        child = ParamTrace.__new__(ParamTrace)
        dict.__init__(child, data)
        child._path = self._path + (key,)
        child._readonly = self._readonly
        child._children = {}
        child._trace = self._trace
        child._host = link
        self._children[key] = child
        if not self._readonly:
            # Store the view itself, so a fixture's write through it lands in THIS
            # copy — and a gate view built over this one later sees it.
            dict.__setitem__(self, key, child)
        return child

    def _wrap(self, key: Any, value: Any) -> Any:
        """What a read of ``value`` hands back, recording nothing."""
        if isinstance(value, dict):
            return self._child(key, value)
        if isinstance(value, list) and self._readonly:
            return _plain(value)
        return value

    def _lookup(self, key: Any) -> Any:
        try:
            value = dict.__getitem__(self, key)
        except KeyError:
            self._record(key, marker=ABSENT)
            return _MISSING
        if isinstance(value, dict):
            return self._child(key, value)
        self._record(key, value)
        return self._wrap(key, value)

    # -- single-key reads --------------------------------------------------- #
    def __getitem__(self, key: Any) -> Any:
        value = self._lookup(key)
        if value is _MISSING:
            raise KeyError(key)
        return value

    def get(self, key: Any, default: Any = None) -> Any:
        value = self._lookup(key)
        return default if value is _MISSING else value

    def __contains__(self, key: Any) -> bool:
        present = dict.__contains__(self, key)
        self._record(key, marker=PRESENT if present else ABSENT)
        return present

    # -- bulk reads: a dependency on the whole level ------------------------- #
    def __iter__(self):
        self._whole()
        return dict.__iter__(self)

    def keys(self):
        self._whole()
        return dict.keys(self)

    def values(self):
        self._whole()
        return {k: self._wrap(k, v) for k, v in dict.items(self)}.values()

    def items(self):
        self._whole()
        return {k: self._wrap(k, v) for k, v in dict.items(self)}.items()

    def __len__(self) -> int:
        self._whole()
        return dict.__len__(self)

    def __bool__(self) -> bool:
        self._whole()
        return dict.__len__(self) > 0

    def __eq__(self, other: Any) -> Any:
        self._whole()
        if isinstance(other, ParamTrace):
            other._whole()
        return dict.__eq__(self, other)

    def __ne__(self, other: Any) -> Any:
        self._whole()
        if isinstance(other, ParamTrace):
            other._whole()
        return dict.__ne__(self, other)

    __hash__ = None                                            # type: ignore[assignment]

    def __repr__(self) -> str:
        self._whole()
        return dict.__repr__(self)

    def __reversed__(self):
        self._whole()
        return dict.__reversed__(self)

    def copy(self) -> dict:
        self._whole()
        return _plain(self)

    def __copy__(self) -> dict:
        return self.copy()

    def __deepcopy__(self, memo: dict) -> dict:
        self._whole()
        return copy.deepcopy(_plain(self), memo)

    def __reduce_ex__(self, protocol: Any) -> tuple:
        self._whole()
        return (dict, (_plain(self),))

    def __reduce__(self) -> tuple:
        return self.__reduce_ex__(2)

    def __or__(self, other: Any) -> Any:
        if not isinstance(other, Mapping):
            return NotImplemented
        self._whole()
        out = _plain(self)
        out.update(other)
        return out

    def __ror__(self, other: Any) -> Any:
        if not isinstance(other, Mapping):
            return NotImplemented
        self._whole()
        out = dict(other)
        out.update(_plain(self))
        return out

    @classmethod
    def fromkeys(cls, iterable: Iterable[Any], value: Any = None) -> dict:  # type: ignore[override]
        return dict.fromkeys(iterable, value)

    # -- writes: refused on a gate's view, recorded on a host view ----------- #
    def _refuse(self, key: Any = _LEVEL) -> None:
        if self._readonly:
            path = self._path if key is _LEVEL else self._path + (key,)
            raise GateInputWriteError(
                f"a gate cannot write another gate's inputs: {_render(path)}")

    def _wrote(self, key: Any = _LEVEL) -> None:
        if key is _LEVEL:
            self._children.clear()
            suffix: tuple = ()
        else:
            self._children.pop(key, None)
            suffix = (key,)
        if self._host is not None:
            self._host.written.add(self._host.path + suffix)

    def __setitem__(self, key: Any, value: Any) -> None:
        self._refuse(key)
        self._wrote(key)
        dict.__setitem__(self, key, value)

    def __delitem__(self, key: Any) -> None:
        self._refuse(key)
        self._wrote(key)
        dict.__delitem__(self, key)

    def update(self, *args: Any, **kwargs: Any) -> None:
        self._refuse()
        incoming = dict(*args, **kwargs)
        for key in incoming:
            self._wrote(key)
        dict.update(self, incoming)

    def __ior__(self, other: Any) -> "ParamTrace":
        self._refuse()
        self.update(other)
        return self

    def pop(self, key: Any, *default: Any) -> Any:
        self._refuse(key)
        self._lookup(key)
        self._wrote(key)
        return dict.pop(self, key, *default)

    def popitem(self) -> tuple:
        self._refuse()
        self._whole()
        key, value = dict.popitem(self)
        self._wrote(key)
        return key, value

    def clear(self) -> None:
        self._refuse()
        self._wrote()
        dict.clear(self)

    def setdefault(self, key: Any, default: Any = None) -> Any:
        self._refuse(key)
        value = self._lookup(key)
        if value is not _MISSING:
            return value
        self._wrote(key)
        dict.__setitem__(self, key, default)
        return default


# --------------------------------------------------------------------------- #
# LedgerView
# --------------------------------------------------------------------------- #
_LEDGER_FIELDS = tuple(f.name for f in dataclasses.fields(Ledger))
_LEDGER_FIELD_SET = frozenset(_LEDGER_FIELDS)

#: Reading one of these through ``ctx.ledger`` makes the whole list an input.
#: openmodelica's ``claims_addressable`` walks ``claims``: honest (packs:H21).
_LEDGER_WHOLE = frozenset({"claims", "params", "inputs", "needs", "decisions",
                           "views", "meta"})

#: Never an input: a gate that read other gates' verdicts would put verdicts
#: inside rho, a staleness that feeds itself. ``last_run`` is run history of the
#: same kind (the previous sweep's hashes), and is hidden for the same reason
#: until U23 removes it.
_LEDGER_HIDDEN = frozenset({"verdicts", "last_run"}) & _LEDGER_FIELD_SET

#: Filled in memory from somewhere other than the record: `Claim.gates` from
#: registry coverage, `physical_result` from `results/`, `Param.gates` from the
#: last read sets. A coverage change is not something a gate read.
_IN_MEMORY_FIELDS = {"claims": ("gates", "physical_result"), "params": ("gates",)}


def _record_form(item: Any, strip: tuple = ()) -> Any:
    form = item.to_dict() if hasattr(item, "to_dict") else item
    if strip and isinstance(form, dict):
        form = {k: v for k, v in form.items() if k not in strip}
    return form


def _ledger_digest(name: str, value: Any) -> str:
    """The digest of one ledger list (or ``meta``) as a gate would read it."""
    strip = _IN_MEMORY_FIELDS.get(name, ())
    if isinstance(value, list):
        return digest_value([_record_form(item, strip) for item in value])
    return digest_value(_record_form(value, strip))


def _claim_digest(claim: Any) -> str:
    return ABSENT if claim is None else digest_value(
        _record_form(claim, _IN_MEMORY_FIELDS["claims"]))


def _peek(ledger: Any, name: str) -> Any:
    """A ledger field without recording a read on it."""
    if isinstance(ledger, LedgerView):
        return ledger._lv_value(name)
    return getattr(ledger, name)


class LedgerView(Ledger):
    """``ctx.ledger`` as a gate sees it: a private copy, recorded.

    ``LedgerView(ledger, trace)``. Fields are copied lazily, on first access, so a
    gate that never looks at the ledger costs nothing. ``verdicts`` (and the run
    history in ``last_run``) read empty: a gate reading other gates' verdicts
    would put verdicts inside rho. ``claim(cid)`` records ``"claim:<cid>"`` with
    the digest of THAT claim — its in-memory ``gates`` and ``physical_result``
    stripped — or ``ABSENT``. Reading ``claims``, ``params``, ``inputs``,
    ``needs``, ``decisions``, ``views`` or ``meta`` records the whole list.
    Every copy out of it (``deepcopy``, pickling) is a plain ``Ledger``.

    ``dataclasses.replace`` still works: it reads every field (recorded) and
    builds an untraced view from them.
    """

    def __init__(self, ledger: Any = None, trace: GateTrace | None = None,
                 **fields: Any) -> None:
        object.__setattr__(self, "_lv_source", ledger)
        object.__setattr__(self, "_lv_trace", trace)
        object.__setattr__(self, "_lv_values", {})
        if ledger is None:
            Ledger.__init__(self, **fields)

    def _lv_value(self, name: str) -> Any:
        """This view's copy of field ``name``, made on first use; never recorded."""
        values = object.__getattribute__(self, "_lv_values")
        if name in values:
            return values[name]
        source = object.__getattribute__(self, "_lv_source")
        if name in _LEDGER_HIDDEN or source is None:
            spec = Ledger.__dataclass_fields__[name]              # type: ignore[attr-defined]
            if spec.default_factory is not dataclasses.MISSING:
                value = spec.default_factory()                    # type: ignore[misc]
            elif spec.default is not dataclasses.MISSING:
                value = copy.copy(spec.default)
            else:
                value = None
        else:
            value = copy.deepcopy(_peek(source, name))
        values[name] = value
        return value

    def _lv_record(self, key: str, compute: Callable[[], str]) -> None:
        trace = object.__getattribute__(self, "_lv_trace")
        if trace is not None and object.__getattribute__(self, "_lv_source") is not None \
                and key not in trace.ledger:
            trace.ledger[key] = compute()

    def __getattribute__(self, name: str) -> Any:
        if name in _LEDGER_FIELD_SET:
            if name in _LEDGER_WHOLE:
                source = object.__getattribute__(self, "_lv_source")
                object.__getattribute__(self, "_lv_record")(
                    name, lambda: _ledger_digest(name, _peek(source, name)))
            return object.__getattribute__(self, "_lv_value")(name)
        return object.__getattribute__(self, name)

    def __setattr__(self, name: str, value: Any) -> None:
        if name in _LEDGER_FIELD_SET:
            object.__getattribute__(self, "_lv_values")[name] = value
        else:
            object.__setattr__(self, name, value)

    def claim(self, cid: str) -> Any:
        source = object.__getattribute__(self, "_lv_source")
        self._lv_record(f"claim:{cid}", lambda: _claim_digest(
            next((c for c in _peek(source, "claims") if c.id == cid), None)))
        return next((c for c in self._lv_value("claims") if c.id == cid), None)

    def _plain_ledger(self, memo: dict | None = None) -> Ledger:
        values = {name: getattr(self, name) for name in _LEDGER_FIELDS}
        return Ledger(**copy.deepcopy(values, memo if memo is not None else {}))

    def __copy__(self) -> Ledger:
        return Ledger(**{name: getattr(self, name) for name in _LEDGER_FIELDS})

    def __deepcopy__(self, memo: dict) -> Ledger:
        return self._plain_ledger(memo)

    def __reduce_ex__(self, protocol: Any) -> tuple:
        return (Ledger.from_dict, (self.to_dict(),))

    def __reduce__(self) -> tuple:
        return self.__reduce_ex__(2)


# --------------------------------------------------------------------------- #
# ModelProxy
# --------------------------------------------------------------------------- #
#: What the proxy answers itself, without calling it a use of the model: copying
#: a context must not count as reading its model, or every sealed fixture that
#: copies ``ctx`` would tie its gate to the whole projection.
_PROXY_OWN = frozenset({"_mp_target", "_mp_trace", "__deepcopy__", "__copy__",
                        "__bool__"})


class ModelProxy:
    """``ctx.model`` as a gate sees it: the first real use is recorded.

    ``ModelProxy(target, trace)``. Any attribute access, ``repr``, comparison,
    call or iteration sets ``trace.model_used``; rho then carries the whole
    projection's digest and the model's code closure (U16), because a gate that
    reaches the model directly could have read anything in it. Copying the
    proxy (``copy.copy``, ``copy.deepcopy``, ``dataclasses.replace`` of the
    context) and truth-testing it do not count. ``None`` is never proxied, so
    ``ctx.model is None`` stays true — and invisible to rho (a named residual:
    no bundled gate touches ``ctx.model``). *Rejected:* ``__getattribute__`` on
    the context itself — ``run_gate``'s ``dataclasses.replace`` and every sealed
    fixture read every field (packs:H10).
    """

    __slots__ = ("_mp_target", "_mp_trace")

    def __init__(self, target: Any, trace: GateTrace | None) -> None:
        object.__setattr__(self, "_mp_target", target)
        object.__setattr__(self, "_mp_trace", trace)

    def _mp_use(self) -> Any:
        trace = object.__getattribute__(self, "_mp_trace")
        if trace is not None:
            trace.model_used = True
        return object.__getattribute__(self, "_mp_target")

    def __getattribute__(self, name: str) -> Any:
        if name in _PROXY_OWN:
            return object.__getattribute__(self, name)
        return getattr(ModelProxy._mp_use(self), name)

    def __setattr__(self, name: str, value: Any) -> None:
        setattr(ModelProxy._mp_use(self), name, value)

    def __delattr__(self, name: str) -> None:
        delattr(ModelProxy._mp_use(self), name)

    def __bool__(self) -> bool:
        return bool(object.__getattribute__(self, "_mp_target"))

    def __deepcopy__(self, memo: dict) -> "ModelProxy":
        return self

    def __copy__(self) -> "ModelProxy":
        return self

    def __repr__(self) -> str:
        return repr(ModelProxy._mp_use(self))

    def __str__(self) -> str:
        return str(ModelProxy._mp_use(self))

    def __dir__(self) -> list:
        return dir(ModelProxy._mp_use(self))

    def __eq__(self, other: Any) -> Any:
        if isinstance(other, ModelProxy):
            other = ModelProxy._mp_use(other)
        return ModelProxy._mp_use(self) == other

    def __ne__(self, other: Any) -> Any:
        return not self.__eq__(other)

    def __hash__(self) -> int:
        return hash(ModelProxy._mp_use(self))

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        return ModelProxy._mp_use(self)(*args, **kwargs)

    def __getitem__(self, key: Any) -> Any:
        return ModelProxy._mp_use(self)[key]

    def __iter__(self):
        return iter(ModelProxy._mp_use(self))

    def __len__(self) -> int:
        return len(ModelProxy._mp_use(self))

    def __contains__(self, item: Any) -> bool:
        return item in ModelProxy._mp_use(self)

    def __reduce_ex__(self, protocol: Any) -> Any:
        return ModelProxy._mp_use(self).__reduce_ex__(protocol)


# --------------------------------------------------------------------------- #
# the traced context
# --------------------------------------------------------------------------- #
def traced_context(ctx: Any, trace: GateTrace, *, readonly: bool = True) -> Any:
    """The context a gate (``readonly=True``) or a control's fixture
    (``readonly=False``) actually receives.

    ``dataclasses.replace`` of ``ctx`` — the same type, every other field
    untouched — with ``params`` a ``ParamTrace`` (read-only for a gate; a
    writable host copy recording host reads for a fixture), ``ledger`` a
    ``LedgerView``, ``extra`` a shallow private copy (fdm-print's mesh cache
    rode on a shared ``extra``, S-27), ``model`` a ``ModelProxy`` (``None``
    stays ``None``), and ``trace`` set when the context type has that field.
    ``memo`` is shared by reference, which is the point of it.

    Not a ``GateContext`` subclass: defining one here would make this module
    import ``gates``, which imports this module.
    """
    names = {f.name for f in dataclasses.fields(ctx)}
    params = getattr(ctx, "params", None)
    changes: dict[str, Any] = {
        "params": ParamTrace({} if params is None else params, trace,
                             readonly=readonly, host=not readonly),
        "extra": dict(getattr(ctx, "extra", None) or {}),
    }
    ledger = getattr(ctx, "ledger", None)
    changes["ledger"] = None if ledger is None else LedgerView(ledger, trace)
    model = getattr(ctx, "model", None)
    changes["model"] = None if model is None else ModelProxy(model, trace)
    if "trace" in names:
        changes["trace"] = trace
    return dataclasses.replace(ctx, **{k: v for k, v in changes.items() if k in names})


# --------------------------------------------------------------------------- #
# the audit hook
# --------------------------------------------------------------------------- #
#: Traces currently open, innermost last. Process-global on purpose: a fixture's
#: nested ``run_gate`` (fluids' ``transitional_line``, packs:H14) must feed both
#: traces, and a gate's worker threads read files too — thread-local routing
#: would miss them.
_STACK: list = []

#: How many times this process has called ``sys.addaudithook``: 0, then 1,
#: forever. Audit hooks cannot be removed; a second one would double every
#: record and every cost.
_HOOK_INSTALLS = 0
_HOOK_LOCK = threading.Lock()

#: Re-entrancy guard, per thread. What slipped through while writing the hook:
#: ``sys._getframe`` raises an audit event of its own, so a hook that asks for
#: its caller's frame re-enters itself — and a hook that raises aborts the
#: operation being audited, here every ``open`` in the process.
_BUSY = threading.local()

#: Modules whose OWN file activity is never a gate input: import machinery (the
#: ``.pyc`` and source opens and the ``sys.path`` listings of every import —
#: ``import bracket`` lists ``model/``, whose listing changes once
#: ``__pycache__`` appears), and linecache, tokenize, warnings and traceback,
#: which read source files to format a message (the first warning only, which
#: makes it order-dependent too).
_EXCLUDED_MODULES = frozenset({
    "importlib._bootstrap", "importlib._bootstrap_external", "zipimport",
    "_frozen_importlib", "_frozen_importlib_external",
    "linecache", "tokenize", "warnings", "traceback",
    "importlib.metadata",
})

#: And every submodule of these. ``importlib.metadata`` is import machinery in
#: all but name: distribution discovery. What slipped through the first list
#: (measured by U16's R-4 sweep over every bundled baseline): numpy.testing asks
#: ``importlib.metadata.distribution(...)`` at import time, which LISTS every
#: ``sys.path`` entry — the checkout's ``src/``, the script's directory, a pack's
#: ``gates/`` left on the path — so the first mesh gate of every process carried
#: ``file-outside-project`` channels no later gate did: never Fresh, and a
#: different entry for ``--only`` than for a full sweep. Library versions are
#: provenance (``instruments_for``), never rho.
_EXCLUDED_PREFIXES = tuple(f"{name}." for name in ("importlib.metadata",))
_EXCLUDED_CODE = frozenset({
    "<frozen importlib._bootstrap>", "<frozen importlib._bootstrap_external>",
    "<frozen zipimport>",
})

#: Arguments beyond this many in one ``argv`` are not checked for files: a
#: process started with ten thousand arguments is opaque already.
_ARGV_FILES_MAX = 256


class _Window:
    """``with tracing(trace):`` — push on enter, pop (by identity) on exit.

    The pop runs before any ``except`` clause of the caller, so a traceback the
    caller formats after the block is never inside the window (packs:H2).
    """

    __slots__ = ("trace",)

    def __init__(self, trace: GateTrace) -> None:
        self.trace = trace

    def __enter__(self) -> GateTrace:
        _install_hook()
        _STACK.append(self.trace)
        return self.trace

    def __exit__(self, *exc: Any) -> bool:
        for i in range(len(_STACK) - 1, -1, -1):
            if _STACK[i] is self.trace:
                del _STACK[i]
                break
        return False


def tracing(trace: GateTrace) -> _Window:
    """Route every file, directory, process and network event to ``trace`` while
    the block runs (and to every enclosing trace as well).

    The hook is installed on the first push, at most once per process, and does
    nothing at all while no window is open.
    """
    return _Window(trace)


def _install_hook() -> None:
    global _HOOK_INSTALLS
    if _HOOK_INSTALLS:
        return
    with _HOOK_LOCK:
        if _HOOK_INSTALLS:
            return
        _library_roots()             # computed here, outside any window
        sys.addaudithook(_audit)
        _HOOK_INSTALLS += 1


@functools.lru_cache(maxsize=1)
def _library_roots() -> tuple[frozenset, tuple, tuple]:
    """``(roots, root + sep prefixes, kept prefixes)``: directories whose contents
    are never a gate input, and the one carve-out from them.

    The interpreter's prefixes and library directories, site-packages, the USER
    site (trimesh and numpy live in ``~/.local`` on the machine this was written
    on — without it every mesh gate's entry would name numpy's own files), the
    installed atompipe package, ``/proc``, ``/sys``, ``/dev``. Machine-specific
    reads no gate decides on; recording them would make every entry stale on
    every other machine. Their versions are provenance (``instruments``), never
    rho. *Rejected:* recording them as opaque (a gate touching any library that
    reads ``/proc`` would never be Fresh). Named residual: a project that lives
    inside the interpreter's prefix would have its own reads dropped.

    **Kept:** the packs that ship inside the wheel. ``pyproject.toml`` maps
    ``packs/`` onto ``atompipe/bundled/``, which sits under both site-packages
    and the atompipe package — so without this carve-out an installed spine
    would drop every read a bundled pack gate makes of its own data files, and a
    release that changed only a pack's data table would leave its PASSes Fresh.
    A pack is a pack (classification's pack-dir rule), wherever pip put it.
    """
    import site
    import sysconfig

    candidates: list[Any] = [sys.prefix, sys.base_prefix, sys.exec_prefix,
                             getattr(sys, "base_exec_prefix", "")]
    try:
        paths = sysconfig.get_paths()
        candidates += [paths.get(key) for key in ("stdlib", "platstdlib",
                                                  "purelib", "platlib")]
    except Exception:
        pass
    for getter in ("getsitepackages", "getusersitepackages"):
        try:
            found = getattr(site, getter)()
            candidates += [found] if isinstance(found, str) else list(found or ())
        except Exception:
            pass
    package = os.path.dirname(os.path.abspath(__file__))
    candidates.append(package)
    if os.name == "posix":
        candidates += ["/proc", "/sys", "/dev"]
    roots: set[str] = set()
    for candidate in candidates:
        if not candidate:
            continue
        for spelling in (os.path.abspath(candidate), os.path.realpath(candidate)):
            spelling = os.path.normcase(os.path.normpath(spelling))
            if not _is_fs_root(spelling):
                roots.add(spelling)
    bundled = os.path.join(package, "bundled")
    kept = tuple(sorted({os.path.normcase(os.path.normpath(spelling)) + os.sep
                         for spelling in (os.path.abspath(bundled),
                                          os.path.realpath(bundled))}))
    return frozenset(roots), tuple(sorted(root + os.sep for root in roots)), kept


def _library_path(path: str) -> bool:
    roots, prefixes, kept = _library_roots()
    norm = os.path.normcase(path)
    if norm.endswith(".pyc"):
        return True
    if norm.startswith(kept):
        return False
    return norm in roots or norm.startswith(prefixes)


def _text(value: Any) -> str | None:
    if value is None or isinstance(value, int):
        return None
    try:
        text = os.fspath(value)
    except TypeError:
        return None
    if isinstance(text, bytes):
        text = os.fsdecode(text)
    if not isinstance(text, str) or not text or "\x00" in text:
        return None
    return text


def _path_arg(value: Any, base: str | None = None) -> str | None:
    text = _text(value)
    if text is None or (text.startswith("<") and text.endswith(">")):
        # A pseudo-filename ("<unknown>", "<string>") names code, not a file.
        # What slipped through on 3.13: traceback's caret anchors ast.parse a
        # line fragment, and the SyntaxError that often raises makes CPython
        # open "<unknown>" to quote the line — an `open` event from the `ast`
        # frame, during every traceback a gate formats.
        return None
    if base and not os.path.isabs(text):
        text = os.path.join(base, text)
    return os.path.abspath(text)


def _open_intent(mode: Any, flags: Any) -> tuple[bool, bool]:
    """``(reads prior bytes, writes)`` from an ``open`` event's mode, or from
    its flags when the mode is ``None`` (``os.open``)."""
    if isinstance(mode, str):
        plus = "+" in mode
        writes = plus or any(c in mode for c in "wax")
        reads = "r" in mode or ("a" in mode and plus) or not writes
        return reads, writes
    flags = flags if isinstance(flags, int) else 0
    access = flags & 3                      # O_RDONLY 0, O_WRONLY 1, O_RDWR 2
    writes = access in (1, 2) or bool(flags & (os.O_CREAT | os.O_TRUNC | os.O_APPEND))
    reads = access in (0, 2) and not flags & os.O_TRUNC
    return reads, writes


def _on_open(traces: tuple, args: tuple) -> None:
    path = _path_arg(args[0] if args else None)
    if path is None or _library_path(path):
        return
    mode = args[1] if len(args) > 1 else None
    flags = args[2] if len(args) > 2 else 0
    reads, writes = _open_intent(mode, flags)
    if mode is None and not writes and (
            isinstance(flags, int) and flags & getattr(os, "O_DIRECTORY", 0)
            or os.path.isdir(path)):
        return                              # a directory fd (rmtree, fsync): no bytes read
    for trace in traces:
        if reads:
            trace._note_read(path)
        if writes:
            trace._note_write(path)


def _on_listdir(traces: tuple, args: tuple) -> None:
    raw = args[0] if args else None
    path = _path_arg(os.curdir if raw is None else raw)
    if path is None or _library_path(path):
        return
    for trace in traces:
        trace.dirs.add(path)


def _on_rename(traces: tuple, args: tuple) -> None:
    for raw in args[:2]:
        path = _path_arg(raw)
        if path is not None and not _library_path(path):
            for trace in traces:
                trace._note_write(path)


def _argv_files(argv: Any, cwd: Any) -> list[str]:
    """Arguments that name existing files: what a subprocess was handed to read."""
    if isinstance(argv, (str, bytes)) or not isinstance(argv, (list, tuple)):
        return []
    base = _text(cwd) or os.getcwd()
    out: list[str] = []
    for item in list(argv)[:_ARGV_FILES_MAX]:
        path = _path_arg(item, base)
        if path is not None and not _library_path(path) and os.path.isfile(path):
            out.append(path)
    return out


def _process(traces: tuple, executable: Any, argv: Any, cwd: Any = None) -> None:
    """A subprocess: opaque (its own reads are invisible here), plus its argv
    files as reads. The root / pack-dir filter on those is classification's
    (U16) — the hook knows no roots."""
    name = _text(executable)
    if name is None and isinstance(argv, (list, tuple)) and argv:
        name = _text(argv[0])
    shown = os.path.basename(name) if name else "?"
    # An undecodable byte in an executable name must not become a lone
    # surrogate in a channel name the entry writer later encodes as UTF-8.
    shown = shown.encode("utf-8", "surrogateescape").decode("utf-8", "backslashreplace")
    channel = f"subprocess:{shown or '?'}"
    files = _argv_files(argv[1:] if isinstance(argv, (list, tuple)) else argv, cwd)
    for trace in traces:
        trace.opaque.add(channel)
        for path in files:
            trace._note_read(path)


def _on_popen(traces: tuple, args: tuple) -> None:
    padded = tuple(args) + (None,) * 3
    _process(traces, padded[0], padded[1], padded[2])


def _on_system(traces: tuple, args: tuple) -> None:
    command = _text(args[0] if args else None) or ""
    words = command.split()
    _process(traces, words[0] if words else "sh", words)


def _on_exec(traces: tuple, args: tuple) -> None:
    padded = tuple(args) + (None,) * 2         # os.exec / os.posix_spawn: (path, argv, env)
    _process(traces, padded[0], padded[1])


def _on_spawn(traces: tuple, args: tuple) -> None:
    padded = tuple(args) + (None,) * 3         # os.spawn: (mode, path, argv, env)
    _process(traces, padded[1], padded[2])


def _on_fork(traces: tuple, args: tuple) -> None:
    for trace in traces:
        trace.opaque.add("subprocess:fork")


def _on_network(traces: tuple, args: tuple) -> None:
    for trace in traces:
        trace.opaque.add("network")


#: Event -> handler. ``os.replace`` audits as ``os.rename`` on CPython; both
#: names are here in case another implementation does not. ``os.fork`` and the
#: datagram sends go beyond the plan's table: a forked child's reads are as
#: invisible as a spawned one's, and a UDP send needs no ``connect``.
_HANDLERS: dict[str, Callable[[tuple, tuple], None]] = {
    "open": _on_open,
    "os.listdir": _on_listdir,
    "os.scandir": _on_listdir,
    "os.rename": _on_rename,
    "os.replace": _on_rename,
    "subprocess.Popen": _on_popen,
    "os.system": _on_system,
    "os.exec": _on_exec,
    "os.posix_spawn": _on_exec,
    "os.spawn": _on_spawn,
    "os.startfile": _on_exec,
    "os.fork": _on_fork,
    "os.forkpty": _on_fork,
    "socket.connect": _on_network,
    "socket.sendto": _on_network,
    "socket.sendmsg": _on_network,
}


def _excluded_frame(frame: Any) -> bool:
    if frame is None:
        return False
    name = frame.f_globals.get("__name__")
    if name in _EXCLUDED_MODULES or (isinstance(name, str) and name.startswith(_EXCLUDED_PREFIXES)):
        return True
    return frame.f_code.co_filename in _EXCLUDED_CODE


def _audit(event: str, args: tuple) -> None:
    """The one audit hook. Never raises, never opens a file, and returns at once
    while no window is open or for an event it does not handle."""
    if not _STACK:
        return
    handler = _HANDLERS.get(event)
    if handler is None or getattr(_BUSY, "on", False):
        return
    _BUSY.on = True
    try:
        try:
            frame = sys._getframe(1)
        except ValueError:
            frame = None
        if not _excluded_frame(frame):
            traces = tuple(_STACK)
            if traces:
                handler(traces, tuple(args) if isinstance(args, tuple) else ())
    except Exception:
        pass
    finally:
        _BUSY.on = False


# --------------------------------------------------------------------------- #
# the spine digest
# --------------------------------------------------------------------------- #
#: Fields that exist on some Pythons' trees and not others, or carry no meaning:
#: ``type_params`` (3.12+, always ``[]`` in 3.10-compatible code),
#: ``type_comment`` (comments), ``kind`` (``u''`` versus ``''``).
_SKIP_FIELDS = frozenset({"type_params", "type_comment", "kind"})


def _is_bare_string(node: Any) -> bool:
    return (isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str))


def _fstring_parts(values: list) -> list:
    """A JoinedStr's parts with adjacent constants merged and empty ones dropped.

    PEP 701 moved f-string parsing into the tokenizer in 3.12; where the
    constant runs of an f-string split, and whether an empty spec is ``None`` or
    an empty ``JoinedStr``, is the tokenizer's business, not the program's.
    """
    out: list = []
    for part in values:
        if isinstance(part, ast.Constant) and isinstance(part.value, str):
            if not part.value:
                continue
            if out and isinstance(out[-1], str):
                out[-1] += part.value
                continue
            out.append(part.value)
        else:
            out.append(part)
    return [ast.Constant(value=p) if isinstance(p, str) else p for p in out]


def _canon(node: Any, emit: Callable[[str], None]) -> None:
    """Emit the canonical tokens of ``node``: its type, then each non-empty field
    in sorted name order. Location attributes are not fields, so they never
    appear; neither does any bare string statement, at any depth."""
    if isinstance(node, ast.Constant):
        value = node.value
        shown = json.dumps(value, ensure_ascii=True) if isinstance(value, str) else repr(value)
        emit(f"(Constant:{type(value).__name__}:{shown})")
        return
    if isinstance(node, ast.AST):
        emit("(" + type(node).__name__)
        for name in sorted(node._fields):
            if name in _SKIP_FIELDS:
                continue
            value = getattr(node, name, None)
            if isinstance(node, ast.JoinedStr) and name == "values":
                value = _fstring_parts(value or [])
            elif isinstance(node, ast.FormattedValue) and name == "format_spec" \
                    and isinstance(value, ast.JoinedStr) and not _fstring_parts(value.values):
                value = None
            if isinstance(value, list):
                value = [item for item in value if not _is_bare_string(item)]
            if value is None or (isinstance(value, list) and not value):
                continue
            emit("." + name)
            _canon(value, emit)
        emit(")")
        return
    if isinstance(node, list):
        emit("[")
        for item in node:
            if item is None:
                emit("~")
            else:
                _canon(item, emit)
        emit("]")
        return
    if isinstance(node, str):
        emit("=str:" + json.dumps(node, ensure_ascii=True))
        return
    emit(f"={type(node).__name__}:{node!r}")


def _tree_digest(tree: ast.AST) -> str:
    tokens: list[str] = []
    _canon(tree, tokens.append)
    text = "\x1e".join(tokens)
    return hashlib.sha256(_AST_SALT + text.encode("utf-8", "surrogatepass")).hexdigest()


@functools.lru_cache(maxsize=64)
def _source_digest(source: Any) -> str:
    return _tree_digest(ast.parse(source))


def canonical_ast_digest(source: Any) -> str:
    """sha256 of a version-independent canonical walk of ``source``'s AST.

    ``source`` is text, bytes (a coding cookie is honoured) or an already parsed
    ``ast.AST``. Node type, then fields in sorted name order; ``type_params``,
    ``type_comment`` and ``kind`` skipped; empty lists and ``None`` skipped;
    constants tagged with their type; every bare string statement dropped at
    every depth — module, class and function docstrings, and the attribute
    docstrings ``models.py`` writes after its fields. So a comment, a docstring
    or a re-wrapped line leaves it unchanged, and ``<`` becoming ``<=`` does not.
    *Rejected:* ``ast.dump`` (3.12 adds ``type_params=[]``, 3.13 omits empty
    fields: every committed entry would be stale on two of CI's three Pythons);
    ``tokenize`` (layout-sensitive). Returns ``""`` — never a digest of nothing —
    when the source does not parse.
    """
    try:
        if isinstance(source, ast.AST):
            return _tree_digest(source)
        return _source_digest(source)
    except (SyntaxError, ValueError, TypeError, RecursionError, MemoryError):
        return ""


_SPINE_MEMO: list = []


def spine_digest() -> str:
    """The digest of what the verdict-path modules say (``SPINE_MODULES``).

    Read from the files beside this one, through ``canonical_ast_digest``, and
    memoised for the process. ``""`` when any of them cannot be read or parsed
    (a wheel installed without ``.py`` sources): every entry is then Unknown,
    none Fresh — a spine that cannot say what it is proves nothing (S-29).
    """
    if _SPINE_MEMO:
        return _SPINE_MEMO[0]
    here = os.path.dirname(os.path.abspath(__file__))
    parts: list[list[str]] | None = []
    for name in SPINE_MODULES:
        try:
            with open(os.path.join(here, name), "rb") as fh:
                digest = canonical_ast_digest(fh.read())
        except OSError:
            digest = ""
        if not digest:
            parts = None
            break
        parts.append([name, digest])
    result = ""
    if parts is not None:
        text = json.dumps(parts, separators=(",", ":"))
        result = hashlib.sha256(_SPINE_SALT + text.encode("ascii")).hexdigest()
    _SPINE_MEMO.append(result)
    return result


# =========================================================================== #
# part two: rho, entries, controls, remembered outcomes, obs
# =========================================================================== #
#: The shape of every entry, control entry, rho payload and code-digest payload
#: this module writes. It sits INSIDE each payload rather than beside it, so a
#: future shape can never collide with this one's digests: it produces new
#: addresses, and every entry keyed by the old ones reads stale, never fresh.
#: *Rejected:* no version at all (a changed canonical form would silently key
#: old bytes as new).
SCHEMA = 1

#: Hex characters of rho in an entry's file name, and of the outcome digest.
#: Why 16: 64 bits keep a collision among one gate's entries below 1e-9 up to
#: 10^5 entries (PLAN D-05's arithmetic), and the full rho is inside the file,
#: so a prefix collision is detected rather than served (``write_entry`` refuses
#: to call a different rho under the same name "exists"). Why 8 for the outcome:
#: it only has to separate the outcomes recorded for ONE rho. *Rejected:* full
#: 64-hex file names — 130-character names against Windows' 260-character path
#: limit, unreadable in a ``git status``.
RHO_CHARS = 16
OUT_CHARS = 8

#: Runs kept per gate, per kind (gate runs and control runs are separate files).
#: 20 covers a working session with enough samples for a median. *Rejected:* 5
#: (too few for a median to mean anything); unbounded (that is the run history
#: the brief removes, rebuilt in another directory).
OBS_KEEP = 20

#: The ``GateSpec`` fields that enter a gate's code digest: the ones that shape
#: a verdict or what it settles. *Rejected:* ``title`` and ``description``
#: (prose — a docstring edit would re-run the gate); ``negative_control``
#: (it belongs to ``rho_control``'s static part, where a fixture rename re-runs
#: the control, not the gate); ``entry`` (discovery only); hashing ``pack.json``
#: (no manifest field reaches a GateSpec at runtime — and it would miss the
#: decorator, which is where the spec is actually written).
SPEC_FIELDS_IN_RHO = ("id", "claims", "tier", "pack", "requires_tools",
                      "requires_python", "requires_one_of", "settles")

#: Where a control runs: ``<root>/.atompipe/out/controls/<gate id>/``, emptied
#: before each run by whoever runs it. Why its own directory: controls wrote the
#: SAME evidence file names as the gate's real run (``beam-analytic/
#: deflection.json``, ``omc/check.mos``), so running a control on a cache miss
#: overwrote the evidence a cached PASS cites with the known-bad working
#: (packs:H5) — and a stale file left there must never become something the next
#: run reads. *Rejected:* the host ``out_dir`` (today's clobbering).
CONTROL_OUT_DIR = ".atompipe/out/controls"

#: Two outcomes recorded for one rho under the same instruments: a WARNING (and
#: the gate reads stale) while this is False, an error once it is True. Staged
#: on purpose (R-4): the refusal lands only after ``EntriesAreDeterministic``
#: (U25) has shown that every bundled gate writes the same bytes from two
#: directories and two cold processes, so the flip cannot turn an honest gate
#: red. *Rejected:* an error from the start — an omc-style nondeterminism would
#: have flagged honest gates red mid-phase, before anyone had measured it;
#: "worse outcome wins", which picks silently.
TWO_OUTCOMES_IS_ERROR = False

_STATE_DIR = ".atompipe"
_VERDICTS_DIR = "verdicts"
_OBS_DIR = "obs"
_CACHE_DIR = "cache"
_LAST_OUTCOMES = "last_outcomes.json"
_SELFTEST = "selftest"
_CONTROL_PREFIX = "control-"
_ENTRY_NAME = re.compile(rf"^[0-9a-f]{{{RHO_CHARS}}}-[0-9a-f]{{{OUT_CHARS}}}\.json$")
_CONTROL_NAME = re.compile(
    rf"^{_CONTROL_PREFIX}[0-9a-f]{{{RHO_CHARS}}}-[0-9a-f]{{{OUT_CHARS}}}\.json$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")

#: Never a selftest input, never an entry in a directory listing: bytecode. Its
#: header embeds an mtime and the interpreter's name, so a digest over it moves
#: on every machine and every Python (``*.py[cod]``).
_BYTECODE = (".pyc", ".pyo", ".pyd")

#: What a remembered outcome can be (§3.9): a crash, a skip the gate chose while
#: its tools were present, and a skip availability chose.
_REMEMBER_KINDS = ("error", "self-skip", "availability")

_ENTRY_FIELDS = ("schema", "gate", "rho", "code", "spine", "reads", "instruments",
                 "verdict", "digest")
_CODE_FIELDS = ("digest", "files", "fallback")
_READ_FIELDS = ("params", "files", "dirs", "ledger", "model", "opaque")
_CONTROL_READ_FIELDS = ("params", "files", "dirs", "ledger", "host", "opaque")

#: The verdict block's WHITELIST, in the order it is written. What is left out,
#: and why: ``duration_s``, ``cpu_s`` (costs are observations; they live in obs,
#: and a tracked duration would make two identical runs two different files);
#: ``rho`` (the entry's own, one level up); ``skipped``, ``skip_reason``,
#: ``error`` (never cached — see ``record_verdict``); ``gate`` (one level up).
#: A field added to ``Verdict`` later is left out until someone decides it
#: belongs in a tracked file — never by default.
_VERDICT_FIELDS = ("passed", "measured", "limit", "units", "detail", "evidence",
                   "locators", "claims", "tier", "pack")

_CONTROL_FIELDS = ("schema", "kind", "gate", "rho", "static", "static_parts", "host",
                   "fixture", "reads", "bad", "good", "admitted", "detail", "measured",
                   "limit", "units", "digest")
_STATIC_PARTS = ("spine", "code", "selftest", "nc")
_HOSTS = ("live", "known-good")

#: The words ``gates.selftest`` uses for the two control outcomes that are not
#: plain "fired": the gate PASSING its known-bad input (a measurement — the gate
#: is a logger), and a self-skip with the tools present (not a measurement).
#: Read here because a selftest verdict carries no other field that tells a
#: logger from a crash (both are ``passed=False`` with no ``error``); pinned by
#: ``tests/test_cache.ControlEntries`` so a reworded message turns a test red
#: instead of filing a logger as a crash.
_PASSED_ITS_KNOWN_BAD = "PASSED its own known-bad fixture"
_SELF_SKIPPED = "skipped on its own known-bad input while its tools are present"


def _digest_of(form: Any) -> str:
    """sha256 of canonical JSON (``sort_keys``, compact, ``ensure_ascii=False``,
    ``allow_nan=False``) — the one rule for rho, rho_control, out8, the code
    digest and an entry's integrity ``digest``. No salt: ``SCHEMA`` is inside
    each payload, and a human can recompute any of them from the documented
    rule."""
    return hashlib.sha256(_canonical_json(form).encode("utf-8", "surrogatepass")).hexdigest()


def _clean(value: Any) -> Any:
    """``value`` with every string encodable as UTF-8 and every tuple a list.

    A gate's detail or an undecodable file name can carry a lone surrogate, which
    no UTF-8 file can hold: written raw, the entry could not be written at all.
    It becomes its visible ``\\udcxx`` spelling instead.
    """
    if isinstance(value, str):
        try:
            value.encode("utf-8")
            return str.__str__(value)
        except UnicodeEncodeError:
            return value.encode("utf-8", "backslashreplace").decode("utf-8")
    if isinstance(value, dict):
        return {_clean(k): _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    return value


def _dump(form: dict) -> bytes:
    """The bytes of a tracked file: indent 2, ``ensure_ascii=False``,
    ``allow_nan=False``, one trailing newline. Key order is the caller's —
    fixed, and documented — never sorted: the file is read by people."""
    return (json.dumps(form, indent=2, ensure_ascii=False, allow_nan=False)
            + "\n").encode("utf-8")


def _strict_json(text: str) -> Any:
    """``json.loads`` that refuses a duplicate key and NaN / Infinity.

    Python's default keeps the LAST of two duplicate keys and parses ``NaN``: a
    hand edit that appended ``"passed": true`` after ``"passed": false`` would be
    read as a pass, and a digest recomputed over the parsed form would agree.
    """
    def pairs(items: list) -> dict:
        out: dict = {}
        for key, value in items:
            if key in out:
                raise ValueError(f"duplicate key {key!r}")
            out[key] = value
        return out

    def constant(name: str) -> Any:
        raise ValueError(f"{name} is not a number JSON allows")

    return json.loads(text, object_pairs_hook=pairs, parse_constant=constant)


def _check_gate_id(gate_id: Any) -> str:
    """``gate_id``, or ``AtompipeError`` when it cannot name a directory.

    The same characters ``Registry.register`` refuses (``/``, ``\\``, ``..``,
    ``:``), re-checked here because a verdict or an obs file can be written for
    a gate nobody registered (a test plants one; ``doctor`` reads an orphan).
    """
    if not isinstance(gate_id, str) or not gate_id.strip() or "\x00" in gate_id \
            or any(bad in gate_id for bad in ("/", "\\", "..", ":")):
        raise AtompipeError(
            f"gate id {gate_id!r} cannot name a directory in the verdict cache "
            f"(.atompipe/verdicts/<gate id>/): no '/', '\\\\', '..' or ':'")
    return gate_id


def _gate_dir(root: str, gate_id: str) -> str:
    return os.path.join(root, _STATE_DIR, _VERDICTS_DIR, _check_gate_id(gate_id))


def _shown(root: str, path: str) -> str:
    """``path`` relative to ``root`` for a message, posix."""
    try:
        return os.path.relpath(path, root).replace(os.sep, "/")
    except ValueError:                                         # another drive
        return path


def _file_bytes(path: str) -> bytes | None:
    try:
        with open(path, "rb") as handle:
            return handle.read()
    except OSError:
        return None


# --------------------------------------------------------------------------- #
# anchors for a project
# --------------------------------------------------------------------------- #
def anchors_for(root: str, registry: Any, *, out_dir: str) -> Anchors:
    """The anchors a project's entries are spelled against.

    ``<root>`` the project, ``<pack:NAME>`` every pack ``registry`` loaded (its
    ``pack_dirs``: only the load knows where a pack lives — a checkout, a
    project's ``.atompipe/packs``, site-packages), ``<out>`` the sweep's
    ``out_dir``, ``<out:controls>`` the control ``out_dir`` (``CONTROL_OUT_DIR``),
    plus ``<tmp>`` and ``~``. ``registry`` may be ``None``: no packs.
    """
    base = os.path.abspath(root) if root else ""
    pack_dirs = getattr(registry, "pack_dirs", None) if registry is not None else None
    packs = {str(name): os.path.abspath(path)
             for name, path in dict(pack_dirs or {}).items() if path}
    return Anchors(root=base, packs=packs,
                   out=os.path.abspath(out_dir) if out_dir else "",
                   controls_out=os.path.join(base, *CONTROL_OUT_DIR.split("/")) if base else "")


def _pack_dir_of(fn: Any) -> str:
    """``PACK_DIR`` of the module that defines ``fn`` (``packs.load_gates`` sets
    it before the module runs), or ``""`` for a project's own gate."""
    target = getattr(fn, "__func__", fn)
    module = sys.modules.get(getattr(target, "__module__", None) or "")
    pack_dir = vars(module).get("PACK_DIR") if module is not None else None
    return os.path.abspath(pack_dir) if isinstance(pack_dir, str) and pack_dir else ""


def _default_anchors(root: str, spec: Any, fn: Any) -> Anchors:
    """``anchors_for(root, None, ...)`` plus the pack ``fn`` came from, for a
    caller that did not pass the sweep's anchors."""
    base = anchors_for(root, None, out_dir=store.out_dir(root) if root else "")
    pack_dir = _pack_dir_of(fn)
    if not pack_dir:
        return base
    name = getattr(spec, "pack", "") or os.path.basename(pack_dir.rstrip(os.sep))
    return dataclasses.replace(base, packs={name: pack_dir})


def _spell(path: Any, anchors: Anchors) -> str | None:
    """How an entry names ``path``: ``model/x.stl`` under the root (bare, posix),
    ``<pack:NAME>/...``, ``<out>/...``, ``<tmp>/...``, ``~/...``; ``None`` for an
    absolute path under no anchor."""
    spelled = anchors.portable_path(path)
    if spelled is None:
        return None
    if spelled == "<root>":
        return "."
    if spelled.startswith("<root>/"):
        return spelled[len("<root>/"):]
    return spelled


def _spell_code(path: str, anchors: Anchors) -> str:
    """``_spell``, falling back to the absolute path: a code file is always
    named, even where no anchor reaches it (then only this machine's entries
    agree — and only an in-process gate, which the CLI never has, gets there)."""
    spelled = _spell(path, anchors)
    return spelled if spelled is not None else os.path.abspath(path).replace(os.sep, "/")


# --------------------------------------------------------------------------- #
# the code digest
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class CodeRef:
    """The code a verdict came from.

    ``digest`` — sha256 of the closure's ``{portable path: sha}``, its
    ``spine_extras`` digests, the ``SPEC_FIELDS_IN_RHO`` values and
    ``fallback``; ``files`` — the portable paths; ``fallback`` — why the closure
    is coarser than exact (``"defining-file"``, or modelio's "computed source at
    ..."); ``opaque`` — why it cannot be keyed at all (``""`` when it can);
    ``third_party`` — the closure's static third-party imports (for
    ``instruments_for``, never for rho).
    """

    digest: str = ""
    files: tuple = ()
    fallback: str = ""
    opaque: str = ""
    third_party: tuple = ()

    @classmethod
    def unrecorded(cls, why: str = "code not recorded: no registered gate to read it "
                                   "from") -> "CodeRef":
        """The code of a verdict whose gate is unknown (``record_verdict`` with
        ``spec=None``): opaque — an entry keyed by it is never Fresh — and never
        a digest of nothing."""
        return cls(opaque=why)

    def to_dict(self) -> dict:
        """The entry's ``code`` block: ``{"digest", "files", "fallback"}``."""
        return {"digest": self.digest, "files": list(self.files), "fallback": self.fallback}


def _spec_value(value: Any) -> Any:
    if isinstance(value, enum.Enum):
        return _spec_value(value.value)
    if isinstance(value, (list, tuple)):
        return [_spec_value(item) for item in value]
    return value


def _defining_file(fn: Any) -> str | None:
    """The file ``fn``'s code was compiled from, or ``None`` when there is none
    (``<string>``, ``exec``, a builtin)."""
    target = getattr(fn, "__func__", fn)
    if isinstance(target, functools.partial):
        target = target.func
    code = getattr(target, "__code__", None)
    if code is None:
        code = getattr(getattr(type(target), "__call__", None), "__code__", None)
    filename = getattr(code, "co_filename", "") if code is not None else ""
    if not isinstance(filename, str) or not filename or (
            filename.startswith("<") and filename.endswith(">")):
        return None
    return os.path.abspath(filename)


def _spine_extra_digest(name: str) -> str:
    """``canonical_ast_digest`` of an ``atompipe.*`` module beside this file, or
    ``""`` when its source cannot be read (a wheel without ``.py`` files)."""
    parts = name.split(".")
    if not parts or parts[0] != "atompipe":
        return ""
    here = os.path.dirname(os.path.abspath(__file__))
    rest = parts[1:]
    candidates = ([os.path.join(here, *rest) + ".py", os.path.join(here, *rest, "__init__.py")]
                  if rest else [os.path.join(here, "__init__.py")])
    for candidate in candidates:
        data = _file_bytes(candidate)
        if data is not None:
            return canonical_ast_digest(data)
    return ""


def _code_payload(files: dict, extras: dict, spec_part: dict, fallback: str) -> str:
    return _digest_of({"schema": SCHEMA, "files": files, "spine_extras": extras,
                       "spec": spec_part, "fallback": fallback})


def code_digest(spec: Any, fn: Any, *, anchors: Anchors | None = None) -> CodeRef:
    """The code ``fn`` (registered as ``spec``) runs, as a ``CodeRef``.

    From the closure ``modelio`` recorded while the gate's module ran: every
    file under its roots, by the bytes that executed (a same-size, same-second
    edit ran the old bytecode before, S-26), spelled portably (``anchors``; a
    project's gate reads ``gates/structural.py``, a pack's
    ``<pack:NAME>/gates/mesh.py``); plus each ``atompipe.*`` module it imports
    that ``SPINE_MODULES`` does not cover, by canonical AST (cad and fdm import
    ``atompipe.site``: a page change re-runs their gates, nobody else's); plus
    the ``SPEC_FIELDS_IN_RHO`` values.

    A function with NO recorded closure — a test's lambda, a gate registered in
    process from Python — is digested as its defining file
    (``fallback="defining-file"``): an edit of that file moves it. What it cannot
    see is named: a value the function closes over (``doctor`` lists every
    defining-file gate; the CLI never produces one). With no file at all
    (``<string>``, ``exec``) it is ``CodeRef(digest="", opaque="code not loaded
    from a file")`` — opaque, never Fresh, never a digest of nothing. A closure
    that recorded two versions of one file, or an ``atompipe.*`` module whose
    source cannot be read, is opaque too.

    ``anchors`` defaults to the pack ``fn`` came from plus ``<tmp>`` and ``~``:
    pass the sweep's for an entry that is the same in every checkout.
    """
    if spec is None or fn is None:
        return CodeRef.unrecorded()
    if anchors is None:
        anchors = _default_anchors("", spec, fn)
    spec_part = {name: _spec_value(getattr(spec, name, None)) for name in SPEC_FIELDS_IN_RHO}
    closure = modelio.code_closure(fn)
    if closure is None:
        path = _defining_file(fn)
        data = _file_bytes(path) if path else None
        if data is None:
            return CodeRef(opaque="code not loaded from a file")
        spelled = _clean(_spell_code(path, anchors))
        files = {spelled: hashlib.sha256(data).hexdigest()}
        return CodeRef(digest=_code_payload(files, {}, spec_part, "defining-file"),
                       files=(spelled,), fallback="defining-file")

    files: dict[str, str | None] = {}
    torn: list[str] = []
    for path, sha in closure.files:
        spelled = _clean(_spell_code(path, anchors))
        files[spelled] = sha or None
        if not sha:
            torn.append(spelled)
    extras = {name: _spine_extra_digest(name) for name in sorted(closure.spine_extras)}
    unreadable = [name for name, digest in extras.items() if not digest]
    fallback = _clean(portable(closure.fallback, anchors))
    opaque = ""
    if torn:
        # modelio writes "" when two versions of one file ran inside one closure
        # (or the file could not be read): no digest names the code that ran.
        opaque = f"code closure inconsistent: {', '.join(sorted(torn))}"
    elif unreadable:
        opaque = f"spine module source unreadable: {', '.join(unreadable)}"
    files = dict(sorted(files.items()))
    return CodeRef(digest=_code_payload(files, extras, spec_part, fallback),
                   files=tuple(files), fallback=fallback, opaque=opaque,
                   third_party=tuple(sorted(closure.third_party)))


def model_digest(projection: Any, model: Any = None, *,
                 anchors: Anchors | None = None) -> str:
    """What a gate that touched ``ctx.model`` depended on: the whole projection
    and the model's recorded code closure, as one digest — or ``""`` when the
    model's code was not recorded (then a gate that used it is opaque).

    ``ModelProxy`` sets ``trace.model_used`` on any real use; the caller that
    built the projection passes this as ``Reads.from_trace(..., model=...)``.
    A gate that reaches the model directly could have read anything in it, so
    nothing narrower is honest. No bundled gate touches ``ctx.model``.
    """
    module = getattr(model, "module", model)
    closure = modelio.code_closure(module) if module is not None else None
    if closure is None:
        return ""
    anchors = anchors or Anchors()
    code = {_clean(_spell_code(path, anchors)): sha or None for path, sha in closure.files}
    if any(sha is None for sha in code.values()):
        return ""
    return _digest_of({"schema": SCHEMA, "projection": digest_value(projection, anchors),
                       "code": dict(sorted(code.items())),
                       "fallback": _clean(portable(closure.fallback, anchors))})


# --------------------------------------------------------------------------- #
# instruments: provenance, never rho
# --------------------------------------------------------------------------- #
@functools.lru_cache(maxsize=1)
def _distributions() -> dict:
    """``{top-level module: [distribution names]}``, once per process."""
    try:
        return {k: list(v) for k, v in importlib.metadata.packages_distributions().items()}
    except Exception:                                          # noqa: BLE001 - metadata is best-effort
        return {}


def _version_of(name: str) -> str:
    """The installed version behind module ``name``: the distribution version,
    ``"unknown"`` when it is importable with no metadata, ``"absent"`` when it is
    not importable. Never imports it."""
    top = name.split(".")[0]
    try:
        found = importlib.util.find_spec(top) if top else None
    except (ImportError, ValueError, AttributeError):
        found = None
    if found is None:
        return "absent"
    versions = set()
    for dist in _distributions().get(top, ()):
        try:
            versions.add(importlib.metadata.version(dist))
        except Exception:                                      # noqa: BLE001
            continue
    return ",".join(sorted(versions)) if versions else "unknown"


def instruments_for(spec: Any, code: Any) -> dict[str, str]:
    """``{module: version}`` for the third-party code a verdict may depend on.

    The modules: ``spec.requires_python``, the ``python:`` entries of
    ``spec.requires_one_of``, and the STATIC third-party imports of the gate's
    closure files (``code.third_party``: every top-level import name, module or
    function level, resolving outside the closure roots, not stdlib, not
    atompipe). The version: ``importlib.metadata``'s, ``"unknown"`` when
    importable without metadata, ``"absent"`` when not importable.

    Never from ``import`` audit events: those fire once per process, on the
    first actual load, so only the first mesh gate of a sweep "saw" trimesh, and
    ``check --only fdm.bridge_span`` and a full sweep wrote different bytes for
    one rho (packs:H3). Instruments are provenance, never part of rho (Q1.3): an
    entry recorded under another numpy stays Fresh, with a note.
    """
    names: set[str] = set()
    for module in getattr(spec, "requires_python", None) or ():
        if isinstance(module, str) and module.strip():
            names.add(module.strip())
    for entry in getattr(spec, "requires_one_of", None) or ():
        kind, _, module = str(entry).partition(":")
        if kind.strip() == "python" and module.strip():
            names.add(module.strip())
    names.update(getattr(code, "third_party", None) or ())
    return {name: _version_of(name) for name in sorted(names)}


# --------------------------------------------------------------------------- #
# Reads: a trace, classified
# --------------------------------------------------------------------------- #
def _json_path(path: tuple) -> list:
    """A param path as JSON: a list, because flat keys such as ``fdm.bbox_mm``
    hold dots. A key that is neither a str nor an int is its ``$k:<repr>``."""
    out: list = []
    for part in path:
        if isinstance(part, str):
            out.append(str.__str__(part))
        elif isinstance(part, int) and not isinstance(part, bool):
            out.append(int(part))
        else:
            out.append(_key_form(part))
    return out


def _dir_digest(path: str) -> str | None:
    """A listing, digested as its sorted entry names — bytecode left out, so
    the first run's ``__pycache__`` does not move it — or ``None`` when there is
    no directory to list."""
    try:
        names = os.listdir(path)
    except OSError:
        return None
    kept = sorted(name for name in names
                  if name != "__pycache__" and not name.endswith(_BYTECODE))
    return _digest_of(_clean(kept))


def _walkable(rel: str) -> bool:
    """Would the selftest walk keep ``rel`` (posix, owner-relative)? No
    ``__pycache__``, no dot-directory, no bytecode, no git directory marker."""
    if not rel or rel.endswith("/"):
        return False
    parts = rel.split("/")
    if any(part == "__pycache__" or part.startswith(".") for part in parts[:-1]):
        return False
    return not parts[-1].endswith(_BYTECODE)


def _norm(path: str) -> str:
    return os.path.normcase(os.path.normpath(os.path.abspath(path)))


class _Places:
    """The anchors as the classification rules need them: each anchor's
    spellings (``abspath`` and ``realpath``), normalised."""

    def __init__(self, anchors: Anchors) -> None:
        by_token: dict[str, list[str]] = {}
        for spelling, token in anchors.pairs():
            by_token.setdefault(token, []).append(os.path.normcase(spelling))
        self.anchors = anchors
        self.packs = [(token[len("<pack:"):-1], by_token[token])
                      for token in sorted(by_token) if token.startswith("<pack:")]
        self.root = by_token.get("<root>", [])
        self.out = by_token.get("<out>", [])
        self.controls = by_token.get("<out:controls>", [])

    @staticmethod
    def rel(path: str, spellings: list[str]) -> str | None:
        """``path`` relative to the first spelling that contains it, posix."""
        for base in spellings:
            if path == base:
                return ""
            if path.startswith(base + os.sep):
                return path[len(base) + 1:].replace(os.sep, "/")
        return None

    def shown(self, path: str) -> str:
        spelled = self.anchors.portable_path(path)
        return spelled if spelled is not None else path.replace(os.sep, "/")


def _classify(path: str, places: _Places, *, is_dir: bool, control: bool,
              selfmod: set, written: list, static: set | None) -> tuple[str, str]:
    """``("drop", "")``, ``("read", <portable>)`` or ``("opaque", <channel>)``
    for one path a trace read — spec §3.4's rules, first match wins."""
    p = _norm(path)
    # 1. the interpreter's and the libraries' own files; bytecode
    if _library_path(p):
        return "drop", ""
    # 2. read, and later written, in this window: its pre-write bytes are gone.
    #    (Written first and read after never reached the read list at all.)
    if not is_dir and p in selfmod:
        return "opaque", f"self-modified:{places.shown(p)}"

    def selftest_covered(rel: str) -> bool:
        if not control or not (rel == _SELFTEST or rel.startswith(_SELFTEST + "/")):
            return False
        if is_dir:
            return True
        return p in static if static is not None else _walkable(rel)

    # 3. under a pack
    for name, spellings in places.packs:
        rel = places.rel(p, spellings)
        if rel is not None:
            if selftest_covered(rel):
                return "drop", ""
            return "read", f"<pack:{name}>/{rel}" if rel else f"<pack:{name}>"
    # 4. under the sweep's or the control's out_dir, and not this window's output
    for prefix, spellings in (("controls/", places.controls), ("", places.out)):
        rel = places.rel(p, spellings)
        if rel is None:
            continue
        if is_dir and any(w == p or w.startswith(p + os.sep) for w in written):
            return "drop", ""                   # a listing of what it wrote itself
        where = (prefix + rel) if rel else (prefix.rstrip("/") or ".")
        return "opaque", f"out:{where} (not written by this gate)"
    rel = places.rel(p, places.root)
    if rel is not None:
        # 5. the spine's own state is never an input
        if rel == _STATE_DIR or rel.startswith(_STATE_DIR + "/"):
            return "opaque", f"atompipe-state:{rel}"
        # 6. the project
        if selftest_covered(rel):
            return "drop", ""
        return "read", rel or "."
    # 7. anything else
    return "opaque", f"file-outside-project:{places.shown(p)}"


@dataclass
class Reads:
    """What one run read, as an entry keys it.

    ``params`` — ``[[path, digest], ...]`` or ``[[path, digest, small], ...]``
    (``path`` a JSON list; ``small`` only when the value is small, for a stale
    line that says ``config.bed_xy 220.0 -> 250.0``); ``files`` and ``dirs`` —
    ``{portable path: sha | None}`` (``None``: missing, which is itself an
    input); ``ledger`` — ``{"claim:<id>" | "<list>": digest}``; ``model`` — the
    ``model_digest`` when the gate used ``ctx.model``; ``opaque`` — sorted
    channel names; ``host`` — a control's host-param reads, ``[[path,
    digest], ...]``. Only the display values are left out of rho.
    """

    params: list = field(default_factory=list)
    files: dict = field(default_factory=dict)
    dirs: dict = field(default_factory=dict)
    ledger: dict = field(default_factory=dict)
    model: str | None = None
    opaque: list = field(default_factory=list)
    host: list = field(default_factory=list)

    @classmethod
    def from_trace(cls, trace: GateTrace, *, anchors: Anchors | None = None,
                   digests: FileDigests | None = None, model: str | None = None,
                   static: Collection[str] | None = None) -> "Reads":
        """Classify ``trace`` (spec §3.4) and digest what it read, now.

        Files and listings are digested when this is called — after the gate
        returned — through ``digests`` (a ``util.FileDigests``; a fresh one when
        ``None``). Each path a trace read is, first match wins:

        1. under the interpreter's prefixes, site and user site, the atompipe
           package, ``/proc``, ``/sys``, ``/dev``, or bytecode — dropped;
        2. read and then written in this window — opaque
           ``self-modified:<path>`` (its pre-write bytes are gone);
        3. under a pack — a read ``<pack:NAME>/<rel>``;
        4. under the sweep's or the control's ``out_dir``, not this window's
           output — opaque ``out:<rel> (not written by this gate)``: another
           gate's output is a cross-gate channel shaped like S-27 (SF PD-11);
        5. under ``<root>/.atompipe/`` — opaque ``atompipe-state:<rel>``;
        6. under the root — a read ``<rel>``;
        7. anything else — opaque ``file-outside-project:<path>``.

        In a CONTROL trace, reads under the owner's ``selftest/`` are dropped:
        the static walk covers them, and a fixture module's import-time read of
        ``baseline.json`` happens only on its first load in a process, so keying
        it would make rho_control depend on module-cache state. ``static`` — the
        absolute paths the walk actually covered — narrows that to exactly those
        files (a git-ignored file a fixture reads is still an input); without it,
        any walkable file under a ``selftest/`` is dropped.

        ``anchors`` defaults to ``trace.anchors``, else to ``<tmp>`` and ``~``
        only — then every project file is outside and opaque: an unanchored
        trace is never Fresh rather than wrongly portable. The trace's own
        opaque channels (``subprocess:omc``, ``network``, a non-JSON param) pass
        through; a gate that used the model is keyed by ``model`` (see
        ``model_digest``) or, with none given, opaque — and a control that used
        it is opaque, because its fixture context carries the live model.
        """
        anchors = anchors or getattr(trace, "anchors", None) or Anchors()
        digests = digests if digests is not None else FileDigests()
        control = getattr(trace, "kind", "gate") == "control"
        places = _Places(anchors)
        static_set = None if static is None else {_norm(p) for p in static}
        written = sorted(_norm(p) for p in trace.files_written)
        selfmod = {_norm(p) for p in trace.self_modified()}

        params = []
        for path, digest in trace.params.items():
            row: list = [_json_path(path), digest]
            if path in trace.values:
                row.append(trace.values[path])
            params.append(_clean(row))
        params.sort(key=lambda row: _canonical_json(row[0]))
        host = sorted((_clean([_json_path(path), digest])
                       for path, digest in trace.host_reads.items()),
                      key=lambda row: _canonical_json(row[0]))

        opaque = {_clean(portable(name, anchors)) for name in trace.opaque}
        files: dict[str, str | None] = {}
        for path in trace.files_read:
            action, what = _classify(path, places, is_dir=False, control=control,
                                     selfmod=selfmod, written=written, static=static_set)
            if action == "read":
                files[_clean(what)] = digests.digest(path)
            elif action == "opaque":
                opaque.add(_clean(what))
        dirs: dict[str, str | None] = {}
        for path in sorted(trace.dirs):
            action, what = _classify(path, places, is_dir=True, control=control,
                                     selfmod=selfmod, written=written, static=static_set)
            if action == "read":
                dirs[_clean(what)] = _dir_digest(path)
            elif action == "opaque":
                opaque.add(_clean(what))

        model_value = None
        if trace.model_used:
            if control:
                opaque.add("model: read on a control (its context carries the live model)")
            elif model:
                model_value = model
            else:
                opaque.add("model: used, and no digest of it was given")
        return cls(params=params, files=dict(sorted(files.items())),
                   dirs=dict(sorted(dirs.items())),
                   ledger=_clean(dict(sorted(trace.ledger.items()))),
                   model=model_value, opaque=sorted(opaque), host=host)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Reads":
        """An entry's ``reads`` block (gate or control shape) back into ``Reads``."""
        data = data or {}
        return cls(params=[list(row) for row in data.get("params") or []],
                   files=dict(data.get("files") or {}), dirs=dict(data.get("dirs") or {}),
                   ledger=dict(data.get("ledger") or {}), model=data.get("model"),
                   opaque=sorted(data.get("opaque") or []),
                   host=[list(row) for row in data.get("host") or []])

    def to_dict(self, *, control: bool = False) -> dict:
        """The entry's ``reads`` block, keys in their fixed order: ``params, files,
        dirs, ledger, model, opaque`` — or, for a control, ``host`` in place of
        ``model``."""
        out: dict = {"params": [list(row) for row in self.params],
                     "files": dict(sorted(self.files.items())),
                     "dirs": dict(sorted(self.dirs.items())),
                     "ledger": dict(sorted(self.ledger.items()))}
        if control:
            out["host"] = [list(row) for row in self.host]
        else:
            out["model"] = self.model
        out["opaque"] = sorted(self.opaque)
        return _clean(out)

    def with_opaque(self, *names: str) -> "Reads":
        """A copy with ``names`` added to its opaque channels."""
        return dataclasses.replace(self, opaque=sorted(set(self.opaque) | set(names)))

    def keyed(self, *, control: bool = False) -> dict:
        """The read set as rho sees it: display values dropped."""
        out = self.to_dict(control=control)
        out["params"] = [row[:2] for row in out["params"]]
        return out


def _as_reads(reads: Any) -> Reads:
    if isinstance(reads, Reads):
        return reads
    return Reads.from_dict(reads or {})


def rho(gate_id: str, spine: str, code: Any, reads: Any) -> str:
    """The content address of a verdict: sha256 of canonical JSON of
    ``{"schema", "gate", "spine", "code", "params", "files", "dirs", "ledger",
    "model", "opaque"}`` — the gate, the spine digest, the code digest (a
    ``CodeRef`` or its digest string) and what the gate read (``Reads`` or an
    entry's ``reads`` block). Display values never enter it; instruments never
    do (Q1.3); a prerequisite's outcome never does (Q1.7, which keeps P2's
    ``needs`` additive)."""
    code_digest_ = code.digest if isinstance(code, CodeRef) else str(code or "")
    keyed = _as_reads(reads).keyed()
    return _digest_of({"schema": SCHEMA, "gate": gate_id, "spine": spine or "",
                       "code": code_digest_, **keyed})


def rho_control(gate_id: str, static: str, reads: Any) -> str:
    """The content address of a control: sha256 of canonical JSON of
    ``{"schema", "gate", "static", "reads"}`` — the static part (spine, code,
    the owner's ``selftest/`` walk, the NegativeControl fields) and what the
    fixture and the gate read on the control (display values dropped). The
    fixture's code closure is NOT in it: that is a lookup hint, so re-verifying
    a control after the model moved writes no new file when the values did not
    move (§3.8)."""
    return _digest_of({"schema": SCHEMA, "gate": gate_id, "static": static or "",
                       "reads": _as_reads(reads).keyed(control=True)})


def _outcome_form(passed: Any, measured: Any, limit: Any, units: Any) -> list:
    return _clean([passed, measured, limit, units or ""])


def out8(verdict: Any) -> str:
    """The first ``OUT_CHARS`` hex of sha256 over ``[passed, measured, limit,
    units]`` — the OUTCOME, never the text: a detail-only difference is the same
    outcome and keeps the first file (D-05). Takes a ``Verdict`` or an entry's
    ``verdict`` block."""
    if isinstance(verdict, Mapping):
        form = _outcome_form(verdict.get("passed") is True, verdict.get("measured"),
                             verdict.get("limit"), verdict.get("units"))
    else:
        form = _outcome_form(verdict.outcome == "pass", _number(verdict.measured, "measured"),
                             _number(verdict.limit, "limit"), verdict.units)
    return _digest_of(form)[:OUT_CHARS]


def _control_out8(bad: str, measured: Any, limit: Any, units: Any) -> str:
    return _digest_of(_outcome_form(bad, measured, limit, units))[:OUT_CHARS]


# --------------------------------------------------------------------------- #
# entries
# --------------------------------------------------------------------------- #
def _number(value: Any, what: str) -> Any:
    """``None``, or ``value`` as a plain finite ``int``/``float`` — else
    ``AtompipeError``. ``run_gate`` already refuses the rest (U02); this is the
    same rule at the door of a tracked file, for a verdict that never went
    through it."""
    if value is None:
        return None
    item = _scalar_item(value)
    if item is not _MISSING:
        value = item
    if isinstance(value, bool) or not isinstance(value, numbers.Real):
        raise AtompipeError(f"a verdict entry's {what} must be a real number or null, "
                            f"not {value!r} ({type(value).__name__})")
    number = int(value) if isinstance(value, numbers.Integral) else float(value)
    if isinstance(number, float) and not math.isfinite(number):
        raise AtompipeError(f"a verdict entry's {what} must be finite, not {number!r}")
    return number


def _locator_form(locator: Any, anchors: Anchors | None) -> dict:
    form = locator.to_dict() if hasattr(locator, "to_dict") else dict(locator)
    return {key: portable(value, anchors) if isinstance(value, str) else value
            for key, value in form.items()}


def _verdict_block(verdict: Verdict, anchors: Anchors | None) -> dict:
    """The whitelisted, portable ``verdict`` block of an entry."""
    evidence = [str(path) for path in verdict.evidence or ()]
    return _clean({
        "passed": verdict.outcome == "pass",
        "measured": _number(verdict.measured, "measured"),
        "limit": _number(verdict.limit, "limit"),
        "units": str(verdict.units or ""),
        "detail": portable(str(verdict.detail or ""), anchors),
        "evidence": anchors.evidence(evidence) if anchors is not None else evidence,
        "locators": [_locator_form(loc, anchors) for loc in verdict.locators or ()],
        "claims": [str(c) for c in verdict.claims or ()],
        "tier": int(verdict.tier),
        "pack": str(verdict.pack or ""),
    })


def _problem_in_block(block: Any) -> str:
    """Why a ``verdict`` block cannot be read as one, or ``""``."""
    if not isinstance(block, dict):
        return "verdict is not an object"
    if list(block) != list(_VERDICT_FIELDS):
        return (f"verdict keys are {list(block)}, not the whitelist "
                f"{list(_VERDICT_FIELDS)}")
    if not isinstance(block["passed"], bool):
        return f"verdict.passed must be true or false, not {block['passed']!r}"
    for what in ("measured", "limit"):
        value = block[what]
        if value is not None and (isinstance(value, bool)
                                  or not isinstance(value, (int, float))
                                  or not math.isfinite(value)):
            return f"verdict.{what} must be a number or null, not {value!r}"
    for what in ("units", "detail", "pack"):
        if not isinstance(block[what], str):
            return f"verdict.{what} must be a string"
    for what in ("evidence", "claims"):
        if not isinstance(block[what], list) or not all(isinstance(v, str) for v in block[what]):
            return f"verdict.{what} must be a list of strings"
    if not isinstance(block["locators"], list) or not all(isinstance(v, dict)
                                                          for v in block["locators"]):
        return "verdict.locators must be a list of objects"
    tier = block["tier"]
    if isinstance(tier, bool) or not isinstance(tier, int) or tier not in {t.value for t in Tier}:
        return f"verdict.tier must be one of {[t.value for t in Tier]}, not {tier!r}"
    return ""


def _problem_in_reads(reads: Any, *, control: bool) -> str:
    keys = _CONTROL_READ_FIELDS if control else _READ_FIELDS
    if not isinstance(reads, dict) or list(reads) != list(keys):
        return f"reads must be an object with keys {list(keys)}"
    for row in reads["params"]:
        if not (isinstance(row, list) and len(row) in (2, 3) and isinstance(row[0], list)
                and isinstance(row[1], str)):
            return f"reads.params has a malformed row {row!r}"
    for what in ("files", "dirs", "ledger"):
        table = reads[what]
        if not isinstance(table, dict) or not all(
                isinstance(k, str) and (isinstance(v, str) or (v is None and what != "ledger"))
                for k, v in table.items()):
            return f"reads.{what} must map paths to digests"
    if control:
        if not isinstance(reads["host"], list) or not all(
                isinstance(row, list) and len(row) == 2 for row in reads["host"]):
            return "reads.host must be a list of [path, digest]"
    elif reads["model"] is not None and not isinstance(reads["model"], str):
        return "reads.model must be a digest or null"
    if not isinstance(reads["opaque"], list) or not all(isinstance(v, str)
                                                        for v in reads["opaque"]):
        return "reads.opaque must be a list of strings"
    return ""


@dataclass
class Entry:
    """One cached verdict: ``.atompipe/verdicts/<gate>/<rho16>-<out8>.json``.

    Fields in file order — ``schema`` (always ``SCHEMA``, not stored here),
    ``gate``, ``rho`` (64 hex), ``code`` (``{"digest", "files", "fallback"}``),
    ``spine``, ``reads`` (``Reads.to_dict()``), ``instruments``, ``verdict``
    (the whitelist) — then ``digest``, sha256 of canonical JSON of all the
    others: integrity against a hand edit, not a defence against forgery (R-9
    is: ``check --force`` re-executes at every boundary that costs money).
    ``path`` is where it was read from; it is not part of the file.
    """

    gate: str
    rho: str
    code: dict
    spine: str
    reads: dict
    instruments: dict
    verdict: dict
    digest: str = ""
    path: str = field(default="", compare=False, repr=False)

    @property
    def name(self) -> str:
        """``<rho16>-<out8>``: the file name without ``.json``."""
        return f"{self.rho[:RHO_CHARS]}-{out8(self.verdict)}"

    def body(self) -> dict:
        """Every field but ``digest``, in file order."""
        return {"schema": SCHEMA, "gate": self.gate, "rho": self.rho, "code": self.code,
                "spine": self.spine, "reads": self.reads, "instruments": self.instruments,
                "verdict": self.verdict}

    def to_verdict(self) -> Verdict:
        """The cached verdict: ``rho`` the full address, ``duration_s`` and
        ``cpu_s`` 0.0 — a cache hit never replays a cost (obs has the runs)."""
        v = self.verdict
        return Verdict(gate=self.gate, passed=v["passed"] is True,
                       claims=list(v.get("claims") or []), measured=v.get("measured"),
                       limit=v.get("limit"), units=v.get("units") or "",
                       detail=v.get("detail") or "", evidence=list(v.get("evidence") or []),
                       tier=Tier(int(v.get("tier") or 0)), pack=v.get("pack") or "",
                       locators=[Locator.from_dict(loc) for loc in v.get("locators") or []],
                       rho=self.rho, duration_s=0.0, cpu_s=0.0)

    def read_set(self) -> Reads:
        return Reads.from_dict(self.reads)


@dataclass(frozen=True)
class WriteResult:
    """What a writer did: ``status`` ``"written"`` (a new file), ``"exists"``
    (these bytes, or the same entry recorded under other instruments or with
    another fixture hint, were already there — no write) or ``"kept-first"``
    (same name, different bytes: the first is kept, and ``warnings`` says
    why); the file's ``path`` and ``name``; the full ``rho``; ``warnings`` —
    two outcomes for one rho, a detail that is not deterministic."""

    status: str
    path: str
    name: str
    rho: str
    warnings: tuple = ()

    @property
    def written(self) -> bool:
        return self.status == "written"


def _write_once(directory: str, filename: str, data: bytes) -> tuple[str, bytes | None]:
    """Create ``directory/filename`` holding ``data`` unless it exists:
    ``("written", None)``, ``("exists", None)`` or ``("differs", <its bytes>)``.

    O_EXCL semantics, and never a torn file: the bytes go to a temporary file
    in the same directory, flushed, which is then hard-linked to the final name
    — ``link(2)`` fails when the name exists, exactly like ``O_EXCL``, and the
    name only ever holds complete bytes. Where the filesystem has no hard links
    it falls back to a plain ``O_CREAT | O_EXCL`` write. The temporary name ends
    in ``.tmp``, which every project's ``.atompipe/.gitignore`` ignores.
    """
    os.makedirs(directory, exist_ok=True)
    final = os.path.join(directory, filename)

    def seen() -> tuple[str, bytes | None]:
        existing = _file_bytes(final)
        if existing is None:
            raise AtompipeError(f"{final} exists and cannot be read")
        return ("exists", None) if existing == data else ("differs", existing)

    if os.path.lexists(final):
        return seen()
    fd, tmp = tempfile.mkstemp(prefix=f".{filename}.", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.chmod(tmp, 0o644)
        except OSError:
            pass
        try:
            os.link(tmp, final)
        except FileExistsError:
            return seen()
        except (OSError, NotImplementedError):
            try:
                out = os.open(final, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                              | getattr(os, "O_BINARY", 0), 0o644)
            except FileExistsError:
                return seen()
            try:
                with os.fdopen(out, "wb") as handle:
                    handle.write(data)
                    handle.flush()
                    os.fsync(handle.fileno())
            except BaseException:
                with contextlib.suppress(OSError):
                    os.unlink(final)
                raise
    finally:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
    return "written", None


def _siblings(directory: str, name: str, rho_full: str, *, control: bool) -> list[Any]:
    """The readable entries (or control entries) in ``directory`` with the same
    full rho as ``name`` and a different outcome."""
    prefix = name[:name.rindex("-") + 1]
    pattern = _CONTROL_NAME if control else _ENTRY_NAME
    found = []
    try:
        names = sorted(os.listdir(directory))
    except OSError:
        return found
    for other in names:
        if other == name + ".json" or not other.startswith(prefix) or not pattern.match(other):
            continue
        loader = _load_control if control else _load_entry
        parsed, _why = loader(os.path.join(directory, other), None)
        if parsed is not None and parsed.rho == rho_full:
            found.append(parsed)
    return found


def _existing_differs(existing: bytes, body: dict, gate: str, name: str,
                      *, hint: str) -> tuple[str, str]:
    """Same name, different bytes: ``("exists", note)`` when the only difference
    is ``hint`` (a gate entry's instruments, a control's fixture closure),
    ``("kept-first", warning)`` otherwise. The first file is kept either way."""
    try:
        parsed = _strict_json(existing.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        return "kept-first", (f"{gate}: {name}.json exists and does not parse ({exc}); "
                              f"the first file is kept")
    if not isinstance(parsed, dict) or parsed.get("rho") != body.get("rho"):
        return "kept-first", (f"{gate}: {name}.json holds a different rho with the same "
                              f"{RHO_CHARS}-character prefix; the first file is kept")
    moved = [key for key in body if parsed.get(key) != body[key]]
    if moved == [hint]:
        if hint == "instruments":
            return "exists", (f"{gate}: {name} was recorded under other instruments "
                              f"({_canonical_json(parsed.get(hint))}; here "
                              f"{_canonical_json(body[hint])}); the first entry is kept")
        return "exists", ""
    return "kept-first", (f"nondeterministic detail: {gate} {name} — the same inputs and "
                          f"the same outcome wrote different bytes in {', '.join(moved)}; "
                          f"the first entry is kept")


def _entry_body(entry: Entry) -> dict:
    """``entry``'s fields, validated and in file order — or ``AtompipeError``:
    the writer never puts something on disk its own reader would refuse."""
    _check_gate_id(entry.gate)
    body = _clean(entry.body())
    if not isinstance(body["rho"], str) or not _HEX64.match(body["rho"]):
        raise AtompipeError(f"{entry.gate}: an entry's rho must be 64 hex characters")
    code = body["code"]
    if not isinstance(code, dict) or not all(k in code for k in _CODE_FIELDS):
        raise AtompipeError(f"{entry.gate}: an entry's code block needs {list(_CODE_FIELDS)}")
    body["code"] = {"digest": str(code["digest"]), "files": [str(f) for f in code["files"]],
                    "fallback": str(code["fallback"])}
    body["reads"] = _as_reads(body["reads"]).to_dict()
    body["instruments"] = {str(k): str(v) for k, v in sorted((body["instruments"] or {}).items())}
    body["verdict"] = {key: body["verdict"].get(key) if isinstance(body["verdict"], dict)
                       else None for key in _VERDICT_FIELDS}
    why = (_problem_in_block(body["verdict"]) or _problem_in_reads(body["reads"], control=False))
    if why:
        raise AtompipeError(f"{entry.gate}: refusing to write an entry its reader would "
                            f"refuse: {why}")
    return body


def write_entry(root: str, entry: Entry) -> WriteResult:
    """Write ``entry`` at ``.atompipe/verdicts/<gate>/<rho16>-<out8>.json``, once.

    The bytes: keys in file order, ``indent=2``, ``ensure_ascii=False``,
    ``allow_nan=False``, a trailing newline, ``digest`` computed here (whatever
    ``entry.digest`` said). Never rewritten (O_EXCL, see ``_write_once``):

    * the same bytes already there — ``"exists"``, nothing written;
    * the same name, bytes differing only in ``instruments`` — ``"exists"``,
      with a note: the same outcome under another library version, and either
      copy is correct (§8);
    * the same name, other bytes — ``"kept-first"`` with the warning
      ``"nondeterministic detail"`` (D-05: same inputs, same outcome tuple,
      different text);
    * another outcome already recorded for this rho — written, with the
      warning ``"two outcomes recorded for identical inputs"`` (equal
      instruments) or ``"outcome differs across instruments"``. Both files stay;
      neither outcome is silently picked (``TWO_OUTCOMES_IS_ERROR``).
    """
    body = _entry_body(entry)
    data = _dump({**body, "digest": _digest_of(body)})
    name = f"{body['rho'][:RHO_CHARS]}-{out8(body['verdict'])}"
    directory = _gate_dir(root, body["gate"])
    warnings: list[str] = []
    for other in _siblings(directory, name, body["rho"], control=False):
        if other.instruments == body["instruments"]:
            warnings.append(f"{body['gate']}: two outcomes recorded for identical inputs "
                            f"({other.name}, {name})")
        else:
            warnings.append(f"{body['gate']}: outcome differs across instruments "
                            f"({other.name} under {_canonical_json(other.instruments)}, "
                            f"{name} under {_canonical_json(body['instruments'])})")
    status, existing = _write_once(directory, name + ".json", data)
    if status == "differs":
        status, note = _existing_differs(existing, body, body["gate"], name, hint="instruments")
        if note:
            warnings.append(note)
    return WriteResult(status=status, path=os.path.join(directory, name + ".json"),
                       name=name, rho=body["rho"], warnings=tuple(warnings))


def _load_entry(path: str, gate_id: str | None) -> tuple[Entry | None, str]:
    """``(entry, "")`` or ``(None, why it is ignored)``."""
    data_bytes = _file_bytes(path)
    if data_bytes is None:
        return None, "unreadable"
    try:
        data = _strict_json(data_bytes.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        return None, f"not strict JSON ({exc})"
    if not isinstance(data, dict):
        return None, "not a JSON object"
    if list(data) != list(_ENTRY_FIELDS):
        return None, f"keys are {list(data)}, not {list(_ENTRY_FIELDS)}"
    if data["schema"] != SCHEMA or isinstance(data["schema"], bool):
        return None, f"schema {data['schema']!r} is not {SCHEMA}: written by another spine"
    if not isinstance(data["gate"], str) or (gate_id is not None and data["gate"] != gate_id):
        return None, f"gate {data['gate']!r} is not the directory's {gate_id!r}"
    if not isinstance(data["rho"], str) or not _HEX64.match(data["rho"]):
        return None, "rho is not 64 hex characters"
    code = data["code"]
    if not (isinstance(code, dict) and list(code) == list(_CODE_FIELDS)
            and isinstance(code["digest"], str) and isinstance(code["fallback"], str)
            and isinstance(code["files"], list)
            and all(isinstance(f, str) for f in code["files"])):
        return None, f"code must be an object with keys {list(_CODE_FIELDS)}"
    if not isinstance(data["spine"], str):
        return None, "spine must be a string"
    why = _problem_in_reads(data["reads"], control=False)
    if why:
        return None, why
    if not isinstance(data["instruments"], dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in data["instruments"].items()):
        return None, "instruments must map module names to versions"
    why = _problem_in_block(data["verdict"])
    if why:
        return None, why
    body = {key: data[key] for key in _ENTRY_FIELDS if key != "digest"}
    if data["digest"] != _digest_of(body):
        return None, "hand-edited entry: its digest does not match its content"
    entry = Entry(gate=data["gate"], rho=data["rho"], code=code, spine=data["spine"],
                  reads=data["reads"], instruments=data["instruments"],
                  verdict=data["verdict"], digest=data["digest"], path=path)
    if os.path.basename(path) != entry.name + ".json":
        return None, f"its name does not match its content (it holds {entry.name}.json)"
    return entry, ""


def read_entries(root: str, gate_id: str, *, problems: list | None = None,
                 instruments: Mapping[str, str] | None = None) -> list[Entry]:
    """Every readable verdict entry of ``gate_id``, sorted by name. Strict.

    A file that is not strict JSON (NaN, a duplicate key), has a key too many or
    too few, a non-bool ``passed``, a non-number ``measured``/``limit``, a name
    its content does not produce, or a ``digest`` that does not match its
    content (``"hand-edited entry"``) is IGNORED — the gate is then a cache miss
    and runs — and one line naming the file and the reason is appended to
    ``problems`` (``doctor`` shows them). Control entries are ``read_controls``'.

    Two outcomes for one rho are both returned, with a problem line: ``"two
    outcomes recorded for identical inputs"`` when their instruments are equal,
    ``"outcome differs across instruments"`` otherwise. With ``instruments``
    (this machine's), the latter case keeps only the entry recorded under
    exactly these instruments, or none — a local re-run, never a pick.
    """
    notes = problems if problems is not None else []
    directory = _gate_dir(root, gate_id)
    try:
        names = sorted(os.listdir(directory))
    except (FileNotFoundError, NotADirectoryError):
        return []
    except OSError as exc:
        notes.append(f"{_shown(root, directory)}: cannot be listed ({exc})")
        return []
    found: list[Entry] = []
    for filename in names:
        if not filename.endswith(".json") or filename.startswith(_CONTROL_PREFIX):
            continue
        path = os.path.join(directory, filename)
        if not _ENTRY_NAME.match(filename):
            notes.append(f"{_shown(root, path)}: not a verdict entry name "
                         f"(<rho16>-<out8>.json); ignored")
            continue
        entry, why = _load_entry(path, gate_id)
        if entry is None:
            notes.append(f"{_shown(root, path)}: {why}; ignored")
            continue
        found.append(entry)

    groups: dict[str, list[Entry]] = {}
    for entry in found:
        groups.setdefault(entry.rho, []).append(entry)
    kept: list[Entry] = []
    for group in groups.values():
        if len(group) == 1:
            kept.extend(group)
            continue
        names_ = ", ".join(e.name for e in group)
        if len({_canonical_json(e.instruments) for e in group}) == 1:
            notes.append(f"{gate_id}: two outcomes recorded for identical inputs ({names_})")
            kept.extend(group)
            continue
        notes.append(f"{gate_id}: outcome differs across instruments ({names_})")
        if instruments is None:
            kept.extend(group)
            continue
        local = [e for e in group if e.instruments == dict(instruments)]
        if len(local) > 1:
            notes.append(f"{gate_id}: two outcomes recorded for identical inputs "
                         f"({', '.join(e.name for e in local)})")
        kept.extend(local)
    return sorted(kept, key=lambda e: e.name)


def record_verdict(root: str, spec: Any, fn: Any, verdict: Verdict, *,
                   trace: GateTrace | None = None, reads: Any = None,
                   anchors: Anchors | None = None,
                   digests: FileDigests | None = None) -> WriteResult | None:
    """Cache ``verdict`` if it is a measurement; return what the writer did.

    Only a gate that RAN and passed or failed is cached. A skip proves nothing
    and an error proves less (invariants 1 and 2): for those this returns
    ``None`` and writes nothing — the caller ``remember``-s them. For a pass or
    a fail it computes the code digest (``code_digest``; ``spec=None`` or
    ``fn=None`` — a verdict planted without its gate — is
    ``CodeRef.unrecorded()``, and the entry names ``code: ...`` as an opaque
    channel), the spine digest, the read set (``reads``, else ``trace``
    classified by ``Reads.from_trace``, else nothing), rho, and
    ``instruments_for``; writes the entry (``write_entry``); and clears any
    remembered outcome of this gate — a cacheable outcome at these inputs is
    newer than the crash it supersedes (§3.9).

    ``anchors`` defaults to ``root``'s, with the pack ``fn`` came from; pass the
    sweep's (``anchors_for``). ``digests`` defaults to a fresh
    ``util.FileDigests`` (no stat cache: correct, and slower).
    """
    if verdict.outcome not in ("pass", "fail"):
        return None
    gate_id = spec.id if spec is not None else verdict.gate
    if verdict.gate != gate_id:
        raise AtompipeError(f"a verdict for {verdict.gate!r} cannot be recorded as "
                            f"{gate_id!r}'s")
    anchors = anchors if anchors is not None else _default_anchors(root, spec, fn)
    keyed = _keyed(gate_id, spec, fn, trace=trace, reads=reads, anchors=anchors,
                   digests=digests)
    result = write_entry(root, _entry_for(spec, verdict, keyed, anchors))
    forget(root, gate_id)
    return result


@dataclass(frozen=True)
class _Keyed:
    """One run, keyed: its code, its classified read set, the spine, its rho."""

    gate: str
    code: CodeRef
    reads: Reads
    spine: str
    rho: str


def _keyed(gate_id: str, spec: Any, fn: Any, *, trace: GateTrace | None, reads: Any,
           anchors: Anchors, digests: FileDigests | None) -> _Keyed:
    """``record_verdict``'s addressing, without the write: the sweep keys every
    run it executes — a crash's rho is shown on its row — and under
    ``record=False`` writes none of them (S-32)."""
    code = code_digest(spec, fn, anchors=anchors) if spec is not None and fn is not None \
        else CodeRef.unrecorded()
    if reads is not None:
        read_set = _as_reads(reads)
    elif trace is not None:
        read_set = Reads.from_trace(trace, anchors=anchors, digests=digests)
    else:
        read_set = Reads()
    if code.opaque:
        read_set = read_set.with_opaque(f"code: {code.opaque}")
    spine = spine_digest()
    return _Keyed(gate_id, code, read_set, spine, rho(gate_id, spine, code, read_set))


def _entry_for(spec: Any, verdict: Verdict, keyed: _Keyed, anchors: Anchors) -> Entry:
    """The entry a keyed pass or fail is written as."""
    return Entry(gate=keyed.gate, rho=keyed.rho, code=keyed.code.to_dict(), spine=keyed.spine,
                 reads=keyed.reads.to_dict(),
                 instruments=instruments_for(spec, keyed.code) if spec is not None else {},
                 verdict=_verdict_block(verdict, anchors))


# --------------------------------------------------------------------------- #
# control entries
# --------------------------------------------------------------------------- #
def _walk_selftest(owner: str) -> list[str]:
    """Every walkable file under ``owner/selftest``, owner-relative and posix:
    no ``__pycache__``, no dot-directory (openmodelica's ``.generated/``), no
    bytecode."""
    base = os.path.join(owner, _SELFTEST)
    found: list[str] = []
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = sorted(d for d in dirnames if d != "__pycache__" and not d.startswith("."))
        for filename in sorted(filenames):
            rel = os.path.relpath(os.path.join(dirpath, filename), owner).replace(os.sep, "/")
            if _walkable(rel):
                found.append(rel)
    return found


def selftest_walk(owner_dir: str, *, digests: FileDigests | None = None) -> dict[str, str | None]:
    """``{owner-relative path: sha256}`` of every file under ``owner_dir/selftest``.

    ``owner_dir`` is the pack directory for a pack's gate and the project root
    for a project's. Which files: ``vcs.ls_files(owner_dir, ["selftest"])`` —
    tracked plus untracked-not-ignored, asked of the OWNER's own repository (a
    bundled pack's repository is atompipe's, not the project's) — or, where git
    cannot answer (no repository: verify.sh's ``--dir`` copies, a wheel), a walk
    that leaves out ``__pycache__/``, ``*.py[cod]`` and dot-directories. Git's
    answer is filtered by the same rules, so the two agree: a checked-out
    project with no ignore rule for ``selftest/__pycache__`` would otherwise key
    its controls on bytecode, whose header embeds an mtime and the
    interpreter's name. Where git lists nothing and the walk finds files (the
    whole directory ignored), the walk wins: an input git was told to ignore is
    still an input. Each file is digested from its bytes (``digests``); a
    tracked file missing on disk digests ``None``, which is a change.
    """
    owner = os.path.abspath(owner_dir)
    listed = vcs.ls_files(owner, [_SELFTEST])
    walked = _walk_selftest(owner) if listed is None or not listed else None
    if listed is None or (not listed and walked):
        rels = walked or []
    else:
        rels = [rel for rel in listed if _walkable(rel)]
    digests = digests if digests is not None else FileDigests()
    return {rel: digests.digest(os.path.join(owner, *rel.split("/")))
            for rel in sorted(set(rels))}


def _owner_dir(fn: Any, root: str) -> str:
    """Whose ``selftest/`` a gate's control lives in: its pack's, else the
    project's (the same answer ``gates._fixture_root`` reaches through
    ``PACK_DIR``)."""
    return _pack_dir_of(fn) or os.path.abspath(root)


def _static(spec: Any, fn: Any, root: str, *, digests: FileDigests | None,
            anchors: Anchors | None, walks: dict | None = None) -> tuple[str, dict, CodeRef, str]:
    """``control_static``'s work, plus the code ref and the owner. ``walks`` —
    ``{owner: selftest_walk}`` — lets one reader judge every gate of a pack with
    ONE walk of its ``selftest/`` (a ``git ls-files`` each): the resolver asks
    for 54 bundled statics per ``status``."""
    anchors = anchors if anchors is not None else _default_anchors(root, spec, fn)
    code = code_digest(spec, fn, anchors=anchors)
    owner = _owner_dir(fn, root)
    if walks is None:
        files = selftest_walk(owner, digests=digests)
    else:
        if owner not in walks:
            walks[owner] = selftest_walk(owner, digests=digests)
        files = dict(walks[owner])
    nc = getattr(spec, "negative_control", None)
    parts = _clean({
        "spine": spine_digest(),
        "code": {"digest": code.digest, "files": list(code.files)},
        "selftest": {"digest": _digest_of(files), "files": files},
        "nc": None if nc is None else {"fixture": nc.fixture, "expect": nc.expect,
                                       "note": nc.note},
    })
    return _digest_of({"schema": SCHEMA, **parts}), parts, code, owner


def control_static(spec: Any, fn: Any, root: str, *, digests: FileDigests | None = None,
                   anchors: Anchors | None = None) -> tuple[str, dict]:
    """``(static, parts)``: the part of ``rho_control`` known without running.

    ``parts`` = ``{"spine", "code": {"digest", "files"}, "selftest": {"digest",
    "files": {path: sha}}, "nc": {"fixture", "expect", "note"}}`` — the spine
    digest, the gate's code digest, the owner's ``selftest_walk`` and the
    NegativeControl fields; ``static`` = sha256 of canonical JSON of them. It
    moves when the gate's code, anything under its owner's ``selftest/``, the
    control's declaration or the spine moves — and NOT when the model does: the
    bracket's ``bed_xy`` edit leaves all six statics where they were (E4).
    """
    static, parts, _code, _owner = _static(spec, fn, root, digests=digests, anchors=anchors)
    return static, parts


@dataclass
class ControlEntry:
    """One demonstration: ``.atompipe/verdicts/<gate>/control-<rhoC16>-<out8>.json``.

    ``rho`` is ``rho_control``; ``static``/``static_parts`` the static part;
    ``host`` ``"known-good"`` or ``"live"`` — which context the fixture got;
    ``fixture`` ``{"digest", "files"}`` — the fixture's recorded code closure, a
    lookup HINT, not an input; ``reads`` — what the fixture and the gate read on
    the control (``host`` keyed only when the host was live); ``bad`` —
    ``"fail"`` (the gate rejected its known-bad input: fired) or ``"pass"``
    (it did not: a logger); ``good`` — ``None`` until P2 runs the known-good
    half; ``admitted`` — ``"reject-only"`` or ``"no"``; ``detail``,
    ``measured``, ``limit``, ``units`` from the run; ``digest`` as for an
    ``Entry``.
    """

    gate: str
    rho: str
    static: str
    static_parts: dict
    host: str
    fixture: dict
    reads: dict
    bad: str
    good: Any = None
    admitted: str = ""
    detail: str = ""
    measured: Any = None
    limit: Any = None
    units: str = ""
    digest: str = ""
    path: str = field(default="", compare=False, repr=False)

    @property
    def name(self) -> str:
        """``control-<rhoC16>-<out8>``: the file name without ``.json``."""
        return (f"{_CONTROL_PREFIX}{self.rho[:RHO_CHARS]}-"
                f"{_control_out8(self.bad, self.measured, self.limit, self.units)}")

    def body(self) -> dict:
        """Every field but ``digest``, in file order."""
        return {"schema": SCHEMA, "kind": "control", "gate": self.gate, "rho": self.rho,
                "static": self.static, "static_parts": self.static_parts, "host": self.host,
                "fixture": self.fixture, "reads": self.reads, "bad": self.bad,
                "good": self.good, "admitted": self.admitted, "detail": self.detail,
                "measured": self.measured, "limit": self.limit, "units": self.units}

    def read_set(self) -> Reads:
        return Reads.from_dict(self.reads)


def _problem_in_control(data: Any) -> str:
    if not isinstance(data, dict):
        return "not a JSON object"
    if list(data) != list(_CONTROL_FIELDS):
        return f"keys are {list(data)}, not {list(_CONTROL_FIELDS)}"
    if data["schema"] != SCHEMA or isinstance(data["schema"], bool):
        return f"schema {data['schema']!r} is not {SCHEMA}: written by another spine"
    if data["kind"] != "control":
        return f"kind {data['kind']!r} is not 'control'"
    for what in ("rho", "static"):
        if not isinstance(data[what], str) or not _HEX64.match(data[what]):
            return f"{what} is not 64 hex characters"
    parts = data["static_parts"]
    if not isinstance(parts, dict) or list(parts) != list(_STATIC_PARTS):
        return f"static_parts must be an object with keys {list(_STATIC_PARTS)}"
    if data["host"] not in _HOSTS:
        return f"host must be one of {list(_HOSTS)}, not {data['host']!r}"
    fixture = data["fixture"]
    if not (isinstance(fixture, dict) and list(fixture) == ["digest", "files"]
            and isinstance(fixture["files"], dict)):
        return "fixture must be an object with keys ['digest', 'files']"
    why = _problem_in_reads(data["reads"], control=True)
    if why:
        return why
    if data["bad"] not in ("fail", "pass"):
        return f"bad must be 'fail' or 'pass', not {data['bad']!r}"
    if data["good"] is not None:
        return "good must be null until the known-good half exists (P2)"
    if data["admitted"] != ("reject-only" if data["bad"] == "fail" else "no"):
        return f"admitted {data['admitted']!r} does not follow from bad {data['bad']!r}"
    for what in ("measured", "limit"):
        value = data[what]
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))
                                  or not math.isfinite(value)):
            return f"{what} must be a number or null, not {value!r}"
    if not isinstance(data["detail"], str) or not isinstance(data["units"], str):
        return "detail and units must be strings"
    return ""


def _load_control(path: str, gate_id: str | None) -> tuple[ControlEntry | None, str]:
    data_bytes = _file_bytes(path)
    if data_bytes is None:
        return None, "unreadable"
    try:
        data = _strict_json(data_bytes.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        return None, f"not strict JSON ({exc})"
    why = _problem_in_control(data)
    if why:
        return None, why
    if not isinstance(data["gate"], str) or (gate_id is not None and data["gate"] != gate_id):
        return None, f"gate {data['gate']!r} is not the directory's {gate_id!r}"
    body = {key: data[key] for key in _CONTROL_FIELDS if key != "digest"}
    if data["digest"] != _digest_of(body):
        return None, "hand-edited entry: its digest does not match its content"
    entry = ControlEntry(**{key: data[key] for key in _CONTROL_FIELDS
                            if key not in ("schema", "kind")}, path=path)
    if os.path.basename(path) != entry.name + ".json":
        return None, f"its name does not match its content (it holds {entry.name}.json)"
    return entry, ""


def write_control(root: str, entry: ControlEntry) -> WriteResult:
    """Write ``entry`` at ``.atompipe/verdicts/<gate>/control-<rhoC16>-<out8>.json``,
    once — ``write_entry``'s rules, with one difference: bytes that differ only
    in ``fixture`` are the same control (``"exists"``, no warning). The fixture
    closure is a hint; a model edit moves it on every control, and re-verifying
    must write no new file when the control's values did not move (§3.8)."""
    _check_gate_id(entry.gate)
    body = _clean(entry.body())
    body["reads"] = _as_reads(body["reads"]).to_dict(control=True)
    why = _problem_in_control({**body, "digest": ""})
    if why:
        raise AtompipeError(f"{entry.gate}: refusing to write a control entry its reader "
                            f"would refuse: {why}")
    data = _dump({**body, "digest": _digest_of(body)})
    name = (f"{_CONTROL_PREFIX}{body['rho'][:RHO_CHARS]}-"
            f"{_control_out8(body['bad'], body['measured'], body['limit'], body['units'])}")
    directory = _gate_dir(root, body["gate"])
    warnings = [f"{body['gate']}: two control outcomes recorded for identical inputs "
                f"({other.name}, {name})"
                for other in _siblings(directory, name, body["rho"], control=True)]
    status, existing = _write_once(directory, name + ".json", data)
    if status == "differs":
        status, note = _existing_differs(existing, body, body["gate"], name, hint="fixture")
        if note:
            warnings.append(note)
    return WriteResult(status=status, path=os.path.join(directory, name + ".json"),
                       name=name, rho=body["rho"], warnings=tuple(warnings))


def read_controls(root: str, gate_id: str, *, problems: list | None = None) -> list[ControlEntry]:
    """Every readable control entry of ``gate_id``, sorted by name; strict, as
    ``read_entries`` (a hand-edited or malformed file is ignored with a line in
    ``problems``). Two outcomes for one rho_control are both returned, with a
    problem line; admission (U20) reads that as not admitted."""
    notes = problems if problems is not None else []
    directory = _gate_dir(root, gate_id)
    try:
        names = sorted(os.listdir(directory))
    except (FileNotFoundError, NotADirectoryError):
        return []
    except OSError as exc:
        notes.append(f"{_shown(root, directory)}: cannot be listed ({exc})")
        return []
    found: list[ControlEntry] = []
    for filename in names:
        if not filename.startswith(_CONTROL_PREFIX) or not filename.endswith(".json"):
            continue
        path = os.path.join(directory, filename)
        if not _CONTROL_NAME.match(filename):
            notes.append(f"{_shown(root, path)}: not a control entry name; ignored")
            continue
        entry, why = _load_control(path, gate_id)
        if entry is None:
            notes.append(f"{_shown(root, path)}: {why}; ignored")
            continue
        found.append(entry)
    by_rho: dict[str, list[ControlEntry]] = {}
    for entry in found:
        by_rho.setdefault(entry.rho, []).append(entry)
    for group in by_rho.values():
        if len(group) > 1:
            notes.append(f"{gate_id}: two control outcomes recorded for identical inputs "
                         f"({', '.join(e.name for e in group)})")
    return found


def _control_outcome(spec: Any, result: Verdict) -> tuple[str | None, str]:
    """``(bad, "")`` for a measurement, ``(None, kind)`` for an outcome that is
    remembered and never cached."""
    outcome = result.outcome
    if outcome == "pass":
        return "fail", ""                              # the gate rejected its known-bad input
    if outcome == "skipped":
        return None, "availability"
    if outcome == "error":
        return None, ("self-skip" if (result.error or "").startswith(_SELF_SKIPPED)
                      else "error")
    if (result.detail or "").startswith(f"{spec.id} {_PASSED_ITS_KNOWN_BAD}"):
        return "pass", ""                              # a logger: measured, not admitted
    # crashed on its fixture, or disagreed with an expect="error" control: an
    # exception is not a measurement, and neither is a control that did not run
    return None, "error"


def _fixture_part(trace: Any, anchors: Anchors) -> dict:
    closure = getattr(trace, "fixture_code", None) if trace is not None else None
    files: dict[str, str | None] = {}
    if isinstance(closure, modelio.CodeClosure):
        for path, sha in closure.files:
            files[_clean(_spell_code(path, anchors))] = sha or None
    files = dict(sorted(files.items()))
    return {"digest": _digest_of(files), "files": files}


def record_control(root: str, spec: Any, fn: Any, *, result: Verdict | None = None,
                   trace: GateTrace | None = None, host: str = "live",
                   bad: str | None = None, detail: str = "",
                   digests: FileDigests | None = None, anchors: Anchors | None = None,
                   when: str = "") -> WriteResult | None:
    """Record one control run of ``spec`` as a control entry; return what the
    writer did, or ``None`` when the outcome is not a measurement.

    **The sweep's form** passes the ``gates.selftest`` ``result`` and the
    ``trace`` it ran under. A fired control (the gate rejected its known-bad
    input) is ``bad: "fail"``, admitted reject-only; a gate that PASSED its
    known-bad input is ``bad: "pass"``, not admitted — both are measurements and
    are cached. A crash, an unusable fixture, a self-skip with the tools
    present, or an availability skip proves nothing about the gate: it is
    ``remember``-ed under ``control:<gate id>``, keyed by the current static part
    (``when`` from the caller: this module reads no clock), and ``None`` is
    returned.

    **The forged form** passes ``bad="fail"`` (and a ``detail``) with no result
    and no trace: an entry with empty reads, which a renderer test uses to plant
    an admission. It forges only the inner loop; R-9's re-execution at every
    money boundary is the defence, not this function.

    ``host``: ``"known-good"`` (the spine handed a project fixture
    ``selftest/known_good.py``'s context, D-27) or ``"live"``; host-param reads
    are keyed in rho_control only when live. Writing a control entry clears any
    remembered control failure of this gate.
    """
    if host not in _HOSTS:
        raise AtompipeError(f"host must be one of {list(_HOSTS)}, not {host!r}")
    built = _control_entry(root, spec, fn, result=result, trace=trace, host=host, bad=bad,
                           detail=detail, digests=digests, anchors=anchors)
    key = f"control:{spec.id}"
    if built.entry is None:
        remember(root, key, built.held, input_rho=built.static, kind=built.kind, when=when)
        return None
    written = write_control(root, built.entry)
    forget(root, key)
    return written


@dataclass(frozen=True)
class _BuiltControl:
    """A control run as ``record_control`` would file it, before anything is
    written: ``entry`` for a measurement, else ``held`` — the verdict to
    remember — and its ``kind``; ``static`` the part it is keyed by."""

    static: str
    entry: ControlEntry | None = None
    kind: str = ""
    held: Verdict | None = None


def _held_control(result: Verdict, kind: str) -> Verdict:
    """What a control that proved nothing is remembered as.

    A crash on the fixture comes back from ``gates.selftest`` as a failed
    selftest with no ``error`` — the selftest's verdict on the GATE. Remembered,
    it is what it is about the control: an error, never something that could
    read as a measured fail.
    """
    if result.outcome != "fail":
        return result
    first = (result.detail or "").splitlines()[0] if result.detail else ""
    return dataclasses.replace(result, error=f"control {kind}: {first}" if first
                               else f"control {kind}")


def _control_entry(root: str, spec: Any, fn: Any, *, result: Verdict | None,
                   trace: GateTrace | None, host: str, bad: str | None, detail: str,
                   digests: FileDigests | None, anchors: Anchors | None) -> _BuiltControl:
    """``record_control``'s work up to the write, so the sweep can judge a
    control run under ``record=False`` exactly as it would file it."""
    anchors = anchors if anchors is not None else _default_anchors(root, spec, fn)
    digests = digests if digests is not None else FileDigests()
    static, parts, code, owner = _static(spec, fn, root, digests=digests, anchors=anchors)
    measured = limit = None
    units = ""
    if bad is None:
        if result is None:
            raise AtompipeError(f"{spec.id}: record_control needs the selftest result "
                                f"or an explicit bad=")
        bad, kind = _control_outcome(spec, result)
        if bad is None:
            return _BuiltControl(static, kind=kind, held=_held_control(result, kind))
    elif bad not in ("fail", "pass"):
        raise AtompipeError(f"bad must be 'fail' or 'pass', not {bad!r}")
    if result is not None:
        measured = _number(result.measured, "measured")
        limit = _number(result.limit, "limit")
        units = str(result.units or "")
        detail = detail or str(result.detail or "")

    static_files = [os.path.join(owner, *rel.split("/")) for rel in parts["selftest"]["files"]]
    reads = (Reads.from_trace(trace, anchors=anchors, digests=digests, static=static_files)
             if trace is not None else Reads())
    if code.opaque:
        reads = reads.with_opaque(f"code: {code.opaque}")
    if host == "known-good":
        # reads of a design the fixture's own selftest files define: the static
        # walk already keys them
        reads = dataclasses.replace(reads, host=[])
    entry = ControlEntry(gate=spec.id, rho=rho_control(spec.id, static, reads),
                         static=static, static_parts=parts, host=host,
                         fixture=_fixture_part(trace, anchors),
                         reads=reads.to_dict(control=True), bad=bad, good=None,
                         admitted="reject-only" if bad == "fail" else "no",
                         detail=portable(detail, anchors), measured=measured, limit=limit,
                         units=units)
    return _BuiltControl(static, entry=entry)


# --------------------------------------------------------------------------- #
# remembered outcomes (untracked)
# --------------------------------------------------------------------------- #
def _outcomes_path(root: str) -> str:
    return os.path.join(root, _STATE_DIR, _CACHE_DIR, _LAST_OUTCOMES)


def _read_outcomes(root: str) -> dict:
    path = _outcomes_path(root)
    data_bytes = _file_bytes(path)
    if data_bytes is None:
        return {}
    try:
        data = _strict_json(data_bytes.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        data = exc
    if not isinstance(data, dict) or not all(
            isinstance(key, str) and isinstance(rec, dict)
            and set(rec) == {"input_rho", "kind", "verdict", "when"}
            and rec["kind"] in _REMEMBER_KINDS and isinstance(rec["input_rho"], str)
            and isinstance(rec["verdict"], dict) and isinstance(rec["when"], str)
            for key, rec in (data.items() if isinstance(data, dict) else ())):
        # Loud, not empty: an unreadable file read as "nothing remembered" would
        # hand the next check the PASS a crash superseded (invariant 2).
        raise AtompipeError(
            f"{_shown(root, path)} is not a remembered-outcomes file"
            f"{f' ({data})' if isinstance(data, Exception) else ''}. It is untracked: "
            f"delete it, then re-run every gate it named — a plain check would serve "
            f"any cached PASS a remembered crash had superseded")
    return data


def remember(root: str, key: str, verdict: Verdict, *, input_rho: str, kind: str,
             when: str) -> None:
    """Remember a non-cacheable outcome in ``.atompipe/cache/last_outcomes.json``.

    ``key`` is a gate id, or ``control:<gate id>``; ``kind`` ``"error"``,
    ``"self-skip"`` or ``"availability"``; ``when`` the CLI's clock. **Keyed by
    ``input_rho``** — the rho the sweep computed for that gate from current
    digests just BEFORE the run (a Fresh entry's, the recomputed rho of the
    latest entry's read signature, or ``""`` when it never ran); for a control,
    the current static part. *Rejected:* the failing run's own rho — a crash at
    partial reads has a different rho than the PASS it followed, so "supersede at
    the same rho" would never match and the next plain check would serve the old
    PASS. Nothing remembered is evidence, so remembering a pass or a fail is
    refused: those are cached.
    """
    if kind not in _REMEMBER_KINDS:
        raise AtompipeError(f"a remembered outcome is one of {list(_REMEMBER_KINDS)}, "
                            f"not {kind!r}")
    if verdict.outcome in ("pass", "fail"):
        raise AtompipeError(f"{verdict.gate}: a pass or a fail is a measurement and is "
                            f"cached (record_verdict), never remembered")
    gate_id = key.split(":", 1)[1] if key.startswith("control:") else key
    _check_gate_id(gate_id)
    data = _read_outcomes(root)
    data[key] = {"input_rho": str(input_rho or ""), "kind": kind,
                 "verdict": _clean(verdict.to_dict()), "when": str(when or "")}
    atomic_write_json(_outcomes_path(root), data)


def remembered(root: str) -> dict:
    """``{key: {"input_rho", "kind", "verdict": Verdict, "when"}}`` — every
    remembered outcome. Raises ``AtompipeError`` naming the file when it does not
    parse (see ``_read_outcomes``)."""
    return {key: {"input_rho": rec["input_rho"], "kind": rec["kind"],
                  "verdict": Verdict.from_dict(rec["verdict"]), "when": rec["when"]}
            for key, rec in sorted(_read_outcomes(root).items())}


def forget(root: str, key: str) -> bool:
    """Drop the remembered outcome ``key``; ``True`` when there was one. Called
    whenever a cacheable outcome (or a control entry) is recorded for it."""
    path = _outcomes_path(root)
    if not os.path.exists(path):
        return False
    data = _read_outcomes(root)
    if key not in data:
        return False
    del data[key]
    atomic_write_json(path, data)
    return True


# --------------------------------------------------------------------------- #
# obs: what runs cost (untracked)
# --------------------------------------------------------------------------- #
def _obs_path(root: str, gate_id: str, control: bool) -> str:
    suffix = ".control.json" if control else ".json"
    return os.path.join(root, _STATE_DIR, _OBS_DIR, _check_gate_id(gate_id) + suffix)


def read_obs(root: str, gate_id: str, *, control: bool = False) -> list[dict]:
    """The recorded runs of ``gate_id`` (or of its control), oldest first:
    ``[{"entry", "when", "duration_s", "cpu_s"}, ...]``, at most ``OBS_KEEP``.
    ``[]`` when there are none — or when the file is unreadable or belongs to
    another gate: obs is latency, never truth, and a mixed series is worse than
    none."""
    data_bytes = _file_bytes(_obs_path(root, gate_id, control))
    if data_bytes is None:
        return []
    try:
        data = _strict_json(data_bytes.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return []
    if not isinstance(data, dict) or data.get("gate") != gate_id \
            or data.get("kind") != ("control" if control else "gate") \
            or not isinstance(data.get("runs"), list):
        return []
    keys = {"entry", "when", "duration_s", "cpu_s"}
    return [dict(run) for run in data["runs"] if isinstance(run, dict) and set(run) == keys]


def record_obs(root: str, gate_id: str, *, entry: str, when: str, duration_s: float,
               cpu_s: float, control: bool = False) -> None:
    """Append one executed run to ``.atompipe/obs/<gate>.json`` — or, for a
    control run, ``<gate>.control.json`` — keeping the last ``OBS_KEEP``.

    Split by kind because the run history mixed ``<gate>#selftest`` rows with
    sweep rows and no latency reader filtered them (S-31): a median over both
    is the cost of neither. Each file also names its gate and kind, so the one
    name two ids could share (gate ``x``'s control and a gate called
    ``x.control``) never yields a mixed series: the reader refuses the other's
    file, and the writer replaces it. ``entry`` is the entry name the run wrote
    or hit (``""`` for none); ``when`` arrives from the CLI.
    """
    runs = read_obs(root, gate_id, control=control)
    runs.append({"entry": str(entry or ""), "when": str(when or ""),
                 "duration_s": float(duration_s), "cpu_s": float(cpu_s)})
    atomic_write_json(_obs_path(root, gate_id, control),
                      {"gate": gate_id, "kind": "control" if control else "gate",
                       "runs": runs[-OBS_KEEP:]})


def last_read_sets(root: str) -> dict[str, set]:
    """``{gate id: {param path tuple, ...}}`` from each gate's latest executed
    entry — the newest obs run whose entry is still there, else the sole entry,
    else (several entries, no obs) the union of their reads.

    It feeds ``Param.gates`` and ``why`` ("which checks read this number"),
    never rho: a gate that did not execute this time (an availability skip)
    keeps the reads of its last executed run, where a full sweep used to erase
    them (S-30).
    """
    base = os.path.join(root, _STATE_DIR, _VERDICTS_DIR)
    try:
        gate_ids = sorted(os.listdir(base))
    except OSError:
        return {}
    out: dict[str, set] = {}
    for gate_id in gate_ids:
        try:
            entries = read_entries(root, gate_id)
        except AtompipeError:
            continue
        if not entries:
            continue
        by_name = {entry.name: entry for entry in entries}
        chosen = None
        for run in reversed(read_obs(root, gate_id)):
            if run.get("entry") in by_name:
                chosen = [by_name[run["entry"]]]
                break
        if chosen is None:
            chosen = entries
        out[gate_id] = {tuple(row[0]) for entry in chosen
                        for row in entry.reads.get("params") or []}
    return out


# =========================================================================== #
# part three: freshness, admission state, the one resolver
# =========================================================================== #
#: How many moves one stale gate names before the rest are counted as
#: ``(+n more)``. Why 3: one stale gate stays one ``status`` line (``stale:
#: bracket.bed_fit — config.bed_xy 220.0 -> 250.0``), and three is enough to
#: show whether an edit was one input or a sweep of them. *Rejected:* every
#: reason — a projection-wide edit (a material switch) prints twenty paths for
#: one gate and buries the next gate's line.
MAX_STALE_REASONS = 3

#: The words a PASS carries while its gate's control is not demonstrated at the
#: gate's current version (PD-08, X14). A PASS from a gate nobody has shown can
#: fail is a logger's output: it reads stale — never PASS — until a check runs
#: the control. Pinned by ``tests/test_freshness.py``.
_UNDEMONSTRATED = "control not demonstrated at this version — run atompipe check"
#: The other fixed reasons a row reads stale for, in the spec's words (§3.10),
#: which the transcript and the readers' tests match on: a ledger verdict from
#: before 1.2 names no inputs (Q1.4); an unregistered gate's verdict still
#: reaches the page but never counts (tests:H2); §3.7's nondeterminism; S-21.
_LEGACY = "recorded before per-gate tracing"
_ORPHAN = "gate not registered in this project"
_TWO_OUTCOMES = "two outcomes recorded for identical inputs"
_NO_MODEL = "the model does not load"
_NO_SPINE = ("the spine cannot digest its own sources (atompipe installed without .py "
             "files), so no entry can say what semantics it was computed under")


# --------------------------------------------------------------------------- #
# the four states of a gate's cache
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Fresh:
    """An entry whose rho, recomputed from what is on disk now, is its own: the
    gate would read exactly what it read then, under the same code and spine.

    ``notes`` — what does not make it stale but is worth saying (another
    library version recorded it, Q1.3: provenance, never rho). ``rho`` — the
    recomputed address (the entry's). ``current`` — every rho recomputable now
    across the gate's read signatures, the set a remembered crash is matched
    against (§3.9).
    """

    entry: Entry
    notes: tuple = ()
    rho: str = ""
    current: frozenset = frozenset()
    state: ClassVar[str] = "fresh"


@dataclass(frozen=True)
class Stale:
    """No entry's rho is current. ``entry`` is the latest one (obs, then commit
    time, then name) and ``reasons`` what moved since it: param moves as
    ``config.bed_xy 220.0 -> 250.0``, then files, code, spine — at most
    ``MAX_STALE_REASONS``, then ``(+n more)``. ``rho`` is the latest entry's
    read signature recomputed now: the ``input_rho`` a crash here is remembered
    under. ``conflict`` holds the entries when two outcomes were recorded for
    the current inputs (§3.7) — stale while ``TWO_OUTCOMES_IS_ERROR`` is False,
    an error once it is True."""

    entry: Entry
    reasons: tuple = ()
    rho: str = ""
    current: frozenset = frozenset()
    conflict: tuple = ()
    state: ClassVar[str] = "stale"


@dataclass(frozen=True)
class Unknown:
    """Nothing can say whether the entry is current: the model does not load
    and it read parameters (S-21); it has an opaque channel (a subprocess's own
    reads, a non-JSON value); the spine cannot digest itself (S-29); or the
    gate's code cannot be keyed. Resolves like stale, never fresh. ``rho`` is
    the recomputed address when one exists (an opaque signature still has one:
    a crash there is matched by it), else ``""``."""

    entry: Any
    reason: str
    rho: str = ""
    current: frozenset = frozenset()
    state: ClassVar[str] = "unknown"


@dataclass(frozen=True)
class Never:
    """The gate has no entry: never ran here, or only crashed or skipped
    (those are remembered, never cached)."""

    state: ClassVar[str] = "never"
    entry: ClassVar[Any] = None
    rho: ClassVar[str] = ""
    current: ClassVar[frozenset] = frozenset()


# --------------------------------------------------------------------------- #
# what is current: gathered once per call
# --------------------------------------------------------------------------- #
def _places_of(anchors: Anchors) -> dict[str, str]:
    """``{token: absolute spelling}``, the anchors read backwards."""
    places: dict[str, str] = {}
    for spelling, token in anchors.pairs():
        places.setdefault(token, spelling)
    return places


def _locate(spelled: Any, root: str, places: Mapping[str, str]) -> str | None:
    """The absolute path a portable spelling names here, or ``None`` when this
    checkout has no such anchor (a pack not loaded)."""
    if not isinstance(spelled, str) or not spelled:
        return None
    if spelled.startswith("<"):
        token, sep, rest = spelled.partition(">")
        base = places.get(token + sep)
        if base is None or (rest and not rest.startswith("/")):
            return None
        rest = rest[1:]
        return os.path.join(base, *rest.split("/")) if rest else base
    if spelled == "~" or spelled.startswith("~/"):
        base = places.get("~")
        if base is None:
            return None
        return os.path.join(base, *spelled[2:].split("/")) if len(spelled) > 2 else base
    if os.path.isabs(spelled):
        return spelled
    if spelled == ".":
        return root
    return os.path.join(root, *spelled.split("/"))


def _param_at(flat: Any, path: tuple, recorded: str, anchors: Anchors) -> tuple[str, Any]:
    """``(digest, value or _MISSING)`` at ``path`` in ``flat`` — walked by
    ``ParamTrace``'s own rules: through dicts only, ``ABSENT`` for a miss,
    presence alone where only presence was recorded, else the digest of
    whatever is there (a leaf, or a whole level read in bulk — the same
    ``digest_value`` either way). ``flat`` may itself be a ``ParamTrace`` (a
    fixture that handed back its host view): the walk reads storage through
    ``dict``'s own methods, so it records nothing on it."""
    node: Any = flat
    for part in path:
        if isinstance(node, dict):
            try:
                if dict.__contains__(node, part):
                    node = dict.__getitem__(node, part)
                    continue
            except TypeError:                        # an unhashable key: not there
                pass
        node = _MISSING
        break
    if recorded == PRESENT:
        return (ABSENT if node is _MISSING else PRESENT), node
    if node is _MISSING:
        return ABSENT, node
    return _digest(node, anchors)[0], node


class _Now:
    """What is on disk and in memory NOW, for one ``freshness``, ``resolve`` or
    ``admission_state`` call: the spine digest, the flattened projection (the
    one ``modelio.flat_params``, so a param is compared in exactly the shape a
    gate was handed it — S-28), the ledger, file digests, the anchors read
    backwards, and one ``selftest/`` walk per owner."""

    def __init__(self, root: str, projection: Any, ledger: Any, *, anchors: Anchors,
                 digests: FileDigests | None, model: Any = None) -> None:
        self.root = os.path.abspath(root)
        self.anchors = anchors
        self.digests = digests if digests is not None else FileDigests()
        self.projection = projection
        if projection is None:
            self.flat: dict | None = None
            self.config_keys: frozenset = frozenset()
        else:
            self.flat, _conflicts = modelio.flat_params(projection)
            config = projection.get("config") if isinstance(projection, Mapping) else None
            self.config_keys = frozenset(config) if isinstance(config, Mapping) else frozenset()
        self.ledger = ledger
        self.model = model
        self.spine = spine_digest()
        self.walks: dict = {}
        self._model: Any = _MISSING
        self._places = _places_of(anchors)

    def locate(self, spelled: Any) -> str | None:
        """The absolute path an entry's portable spelling names here, or
        ``None`` when this checkout has no such anchor (a pack not loaded)."""
        return _locate(spelled, self.root, self._places)

    def param(self, path: tuple, recorded: str) -> tuple[str, Any]:
        """``(digest now, value or _MISSING)`` at ``path`` in the projection's
        flat params (``_param_at``)."""
        return _param_at(self.flat, path, recorded, self.anchors)

    def ledger_digest(self, key: str) -> str | None:
        """The digest a gate reading ledger ``key`` would record now, or
        ``None`` when it cannot be re-read (no ledger, a key this spine does
        not know)."""
        if self.ledger is None or not isinstance(key, str):
            return None
        if key.startswith("claim:"):
            cid = key[len("claim:"):]
            return _claim_digest(next((c for c in (self.ledger.claims or ()) if c.id == cid),
                                      None))
        if key in _LEDGER_WHOLE and key in _LEDGER_FIELD_SET:
            return _ledger_digest(key, getattr(self.ledger, key))
        return None

    def model_digest(self) -> str | None:
        if self._model is _MISSING:
            found = None
            if self.model is not None and self.projection is not None:
                found = model_digest(self.projection, self.model, anchors=self.anchors) or None
            self._model = found
        return self._model

    def static(self, spec: Any, fn: Any) -> tuple[str, dict]:
        static, parts, _code, _owner = _static(spec, fn, self.root, digests=self.digests,
                                              anchors=self.anchors, walks=self.walks)
        return static, parts


def _now_for(root: str, registry: Any, projection: Any, ledger: Any, *,
             anchors: Anchors | None, digests: FileDigests | None, model: Any) -> _Now:
    if anchors is None:
        anchors = anchors_for(root, registry, out_dir=store.out_dir(root) if root else "")
    return _Now(root, projection, ledger, anchors=anchors, digests=digests, model=model)


# --------------------------------------------------------------------------- #
# judging one gate's entries
# --------------------------------------------------------------------------- #
def _signature(entry: Entry) -> str:
    """What an entry read, without the values: the addresses its rho is
    recomputed over. Entries of one gate that read different paths (a branch on
    a mode switch) are judged each against its own paths. A presence-only read
    is its own kind of address: ``k in p`` recomputes to PRESENT/ABSENT, a value
    read to the value's digest."""
    reads = entry.reads or {}
    return _canonical_json({
        "params": [[row[0], row[1] == PRESENT] for row in reads.get("params") or ()],
        "files": sorted(reads.get("files") or {}),
        "dirs": sorted(reads.get("dirs") or {}),
        "ledger": sorted(reads.get("ledger") or {}),
        "model": reads.get("model") is not None,
        "opaque": sorted(reads.get("opaque") or ()),
    })


def _reads_now(reads: Mapping[str, Any], now: _Now) -> tuple[Reads | None, str]:
    """The read set ``reads`` names, digested NOW — or ``(None, why)`` when some
    part of it cannot be re-read here."""
    rows = reads.get("params") or []
    if rows and now.flat is None:
        return None, _NO_MODEL
    params = []
    for row in rows:
        digest, _value = now.param(tuple(row[0]), row[1])
        params.append([list(row[0]), digest])
    files: dict[str, str | None] = {}
    for spelled in reads.get("files") or {}:
        where = now.locate(spelled)
        if where is None:
            return None, f"cannot find {spelled} here"
        files[spelled] = now.digests.digest(where)
    dirs: dict[str, str | None] = {}
    for spelled in reads.get("dirs") or {}:
        where = now.locate(spelled)
        if where is None:
            return None, f"cannot find {spelled} here"
        dirs[spelled] = _dir_digest(where)
    ledger: dict[str, str] = {}
    for key in reads.get("ledger") or {}:
        digest = now.ledger_digest(key)
        if digest is None:
            return None, f"cannot re-read ledger {key} here"
        ledger[key] = digest
    model = None
    if reads.get("model") is not None:
        model = now.model_digest()
        if model is None:
            return None, "it used ctx.model, and no loaded model was given to digest"
    return Reads(params=params, files=files, dirs=dirs, ledger=ledger, model=model,
                 opaque=list(reads.get("opaque") or ())), ""


def _display(digest: str, value: Any, small: bool) -> str | None:
    """A param's display for a stale reason, or ``None`` when it has none.
    (Not ``_shown``: that name is part two's path-for-a-message helper, and
    shadowing it here broke every "ignored" problem line ``read_entries``
    writes — caught by ``test_a_hand_edited_entry_is_ignored_and_named``.)"""
    if digest == ABSENT:
        return "absent"
    if digest == PRESENT:
        return "present"
    return repr(value) if small else None


def _is_input(path: tuple, now: _Now) -> bool:
    """A path into the model's INPUTS: ``config.*``, or a config field at the
    top level (``flat_params`` puts every one there, input over derived)."""
    return bool(path) and (path[0] == "config" or path[0] in now.config_keys)


def _capped(reasons: list[str]) -> tuple:
    if len(reasons) <= MAX_STALE_REASONS:
        return tuple(reasons)
    return tuple(reasons[:MAX_STALE_REASONS]) + (f"(+{len(reasons) - MAX_STALE_REASONS} more)",)


def _stale_text(reasons: Iterable[str]) -> str:
    """``a, b, c (+2 more)`` — the one line a stale gate gets."""
    shown = [r for r in reasons if not (r.startswith("(+") and r.endswith(" more)"))]
    more = [r for r in reasons if r not in shown]
    return ", ".join(shown) + ("".join(f" {m}" for m in more))


def _reasons(entry: Entry, reads_now: Reads, code: CodeRef, now: _Now) -> tuple:
    """What moved between ``entry`` and now, in words, inputs first.

    A derived value that moved while an input it could follow from moved too is
    that input's consequence, and is named only when no input moved (then
    ``build()`` itself changed, and the derived value IS the news). What slipped
    through while writing this: ``bracket.bed_fit`` reads ``usable_bed`` as well
    as ``config.bed_xy``, so the transcript's one-cause line (``config.bed_xy
    220.0 -> 250.0``) came out as two, and a three-reason cap filled with echoes
    of one edit.
    """
    inputs: list[str] = []
    derived: list[str] = []
    recorded = (entry.reads or {}).get("params") or []
    for row, now_row in zip(recorded, reads_now.params):
        if row[1] == now_row[1]:
            continue
        path = tuple(row[0])
        old = _display(row[1], row[2] if len(row) > 2 else None, len(row) > 2)
        _digest_now, value = now.param(path, row[1])
        small, display = (False, None) if value is _MISSING else small_value(value, now.anchors)
        new = _display(now_row[1], display, small)
        text = (f"{_dotted(path)} {old} -> {new}" if old is not None and new is not None
                else f"{_dotted(path)} changed")
        (inputs if _is_input(path, now) else derived).append(text)
    reasons = inputs or derived
    reads = entry.reads or {}
    if reads.get("model") != reads_now.model:
        reasons.append("model changed")
    for key, digest in sorted((reads.get("ledger") or {}).items()):
        if reads_now.ledger.get(key) != digest:
            reasons.append(f"claim {key[len('claim:'):]} changed" if key.startswith("claim:")
                           else f"ledger {key} changed")
    for kind, table, table_now in (("", reads.get("files") or {}, reads_now.files),
                                   ("listing of ", reads.get("dirs") or {}, reads_now.dirs)):
        for key, digest in sorted(table.items()):
            here = table_now.get(key)
            if here == digest:
                continue
            what = "removed" if here is None else "added" if digest is None else "changed"
            reasons.append(f"{kind}{key} {what}")
    if (entry.code or {}).get("digest") != code.digest:
        reasons.append("gate code changed")
    if entry.spine != now.spine:
        reasons.append("atompipe spine changed")
    return _capped(reasons or ["inputs changed"])


def _instrument_notes(entry: Entry, local: Mapping[str, str]) -> tuple:
    """``recorded under trimesh 4.0.0; here 5.1.0`` per module that differs."""
    notes = []
    for name, was in sorted((entry.instruments or {}).items()):
        here = local.get(name) or _version_of(name)
        if was != here:
            notes.append(f"recorded under {name} {was}; here {here}")
    return tuple(notes)


def _entry_order(root: str, gate_id: str, times: Mapping[str, str]) -> Callable[[Entry], tuple]:
    """The key "latest" sorts entries by: the newest obs run that hit or wrote
    each (``when``, then its position), else the entry file's commit time, else
    nothing — then the name, so the answer never depends on listing order."""
    last: dict[str, tuple[str, int]] = {}
    for index, run in enumerate(read_obs(root, gate_id)):
        last[run.get("entry", "")] = (str(run.get("when") or ""), index)

    def key(entry: Entry) -> tuple:
        when, index = last.get(entry.name, ("", -1))
        return (when or times.get(_entry_rel(root, entry), ""), index, entry.name)

    return key


def _entry_rel(root: str, entry: Any) -> str:
    """An entry file's path relative to the project, posix (``vcs``'s form)."""
    return _shown(root, entry.path) if getattr(entry, "path", "") else ""


def _judge(spec: Any, code: CodeRef, entries: list[Entry], now: _Now,
           order: Callable[[Entry], tuple]) -> Fresh | Stale | Unknown | Never:
    """One gate's state, from its entries. Never runs the gate."""
    if not entries:
        return Never()
    gate_id = spec.id
    blocked = _NO_SPINE if not now.spine else (
        f"its code cannot be keyed: {code.opaque}" if code.opaque else "")
    groups: dict[str, list[Entry]] = {}
    for entry in entries:
        groups.setdefault(_signature(entry), []).append(entry)
    judged: dict[str, tuple[str, str, Reads | None]] = {}
    current: set[str] = set()
    matches: list[Entry] = []
    for sig, group in groups.items():
        reads_now, why = _reads_now(group[0].reads or {}, now)
        why = blocked or why
        rho_now = "" if why else rho(gate_id, now.spine, code, reads_now)
        judged[sig] = (rho_now, why, reads_now)
        if rho_now:
            current.add(rho_now)
            if not (group[0].reads or {}).get("opaque"):
                matches.extend(e for e in group if e.rho == rho_now)
    known = frozenset(current)
    if matches:
        return _fresh_or_conflict(spec, code, matches, known, order)
    latest = max(entries, key=order)
    rho_now, why, reads_now = judged[_signature(latest)]
    if why:
        return Unknown(latest, why, "", known)
    opaque = list((latest.reads or {}).get("opaque") or ())
    if opaque:
        return Unknown(latest, "opaque inputs: " + ", ".join(opaque), rho_now, known)
    return Stale(latest, _reasons(latest, reads_now, code, now), rho_now, known)


def _fresh_or_conflict(spec: Any, code: CodeRef, matches: list[Entry], current: frozenset,
                       order: Callable[[Entry], tuple]) -> Fresh | Stale:
    """Entries at the current rho: one outcome is Fresh; two are §3.7's case.

    Equal instruments — the same inputs, the same libraries, two answers: a
    stale "two outcomes" (an error once ``TWO_OUTCOMES_IS_ERROR`` flips, which
    ``resolve`` reads off ``conflict``). Different instruments — another numpy
    merged from another machine: the entry recorded under THIS machine's wins,
    and with none recorded here a local run decides. *Rejected:* "worse outcome
    wins", which picks silently.
    """
    chosen = max(matches, key=order)
    names = ", ".join(sorted(e.name for e in matches))
    if len({out8(e.verdict) for e in matches}) == 1:
        return Fresh(chosen, _instrument_notes(chosen, instruments_for(spec, code)),
                     chosen.rho, current)
    if len({_canonical_json(e.instruments) for e in matches}) == 1:
        return Stale(chosen, (f"{_TWO_OUTCOMES} ({names})",), chosen.rho, current,
                     conflict=tuple(sorted(matches, key=lambda e: e.name)))
    local = instruments_for(spec, code)
    here = [e for e in matches if e.instruments == local]
    if here and len({out8(e.verdict) for e in here}) == 1:
        pick = max(here, key=order)
        return Fresh(pick, (f"outcome differs across instruments ({names}); the entry "
                            f"recorded under this machine's is used",), pick.rho, current)
    if here:
        return Stale(max(here, key=order), (f"{_TWO_OUTCOMES} ({names})",), chosen.rho,
                     current, conflict=tuple(sorted(here, key=lambda e: e.name)))
    return Stale(chosen, (f"outcome differs across instruments ({names}), and none was "
                          f"recorded under this machine's: a run here decides",),
                 chosen.rho, current)


def _gate_entries(root: str, gate_id: str, notes: list) -> list[Entry]:
    try:
        return read_entries(root, gate_id, problems=notes)
    except AtompipeError as exc:
        notes.append(f"{gate_id}: {exc}")
        return []


def _commit_times(root: str, entries: Iterable[Entry], obs_named: Callable[[Entry], bool]
                  ) -> dict[str, str]:
    """One ``git log`` walk for every entry no obs run names — a fresh clone's
    entries all arrive that way — or nothing, when every entry has a run."""
    wanted = sorted({_entry_rel(root, e) for e in entries if not obs_named(e)} - {""})
    return vcs.commit_times(root, wanted) if wanted else {}


def _obs_names(root: str, gate_ids: Iterable[str]) -> set[tuple[str, str]]:
    return {(gate_id, run.get("entry", "")) for gate_id in gate_ids
            for run in read_obs(root, gate_id)}


def freshness(root: str, registry: Any, projection: Any, ledger: Any, *,
              digests: FileDigests | None = None, anchors: Anchors | None = None,
              model: Any = None) -> dict[str, Fresh | Stale | Unknown | Never]:
    """``{gate id: Fresh | Stale | Unknown | Never}`` for every registered gate.

    **It never runs a gate or a fixture** (the verifying-trace shape, M11.11):
    a gate's entries are grouped by read signature, and each group's rho is
    recomputed from what is current — the spine digest now, the code closure of
    the LOADED module (a newly added import counts), the params at each recorded
    path in ``modelio.flat_params(projection)`` walked by ``ParamTrace``'s own
    leaf/presence/bulk rules, file and listing digests (``digests``, a
    ``util.FileDigests``), the claim records. An entry whose rho is the
    recomputed one is Fresh.

    Unknown — never Fresh — when the projection is ``None`` (the model does not
    load, S-21) and the entry read params; when it has any opaque channel; when
    the spine digest is ``""``; when the code cannot be keyed. What slipped
    through before: ONE hash of the projection decided every gate, and a model
    that failed to import made it compare equal — status said "unchanged" and
    listed three PROVEN claims for a design that could not be built (S-21);
    and one hash of every input made an unread datasheet stale every measurable
    claim (S-33).

    ``anchors`` defaults to ``anchors_for(root, registry, out_dir=<root's>)`` —
    the sweep's; entries recorded under other anchors spell paths differently
    and read stale. ``model`` is the loaded model (``modelio.LoadedModel``): an
    entry of a gate that used ``ctx.model`` is Unknown without it.
    """
    now = _now_for(root, registry, projection, ledger, anchors=anchors, digests=digests,
                   model=model)
    pairs = list(registry.pairs()) if registry is not None else []
    problems: list[str] = []
    entries = {spec.id: _gate_entries(now.root, spec.id, problems) for spec, _fn in pairs}
    obs = _obs_names(now.root, entries)
    times = _commit_times(now.root, (e for es in entries.values() if len(es) > 1 for e in es),
                          lambda e: (e.gate, e.name) in obs)
    return {spec.id: _judge(spec, code_digest(spec, fn, anchors=now.anchors),
                            entries[spec.id], now, _entry_order(now.root, spec.id, times))
            for spec, fn in pairs}


# --------------------------------------------------------------------------- #
# admission, outside check: read, never run
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Admission:
    """Whether a gate's control is demonstrated at its current version.

    ``state``: ``"admitted"`` — a control entry at the current static part whose
    recorded inputs are current and whose fixture closure has not moved;
    ``"pending"`` — such an entry, but the fixture's code moved (a model it
    imports was edited) and nothing has re-run it yet: it COUNTS, and
    ``reason`` says the next check re-verifies; ``"not-admitted"`` — the
    current control PASSED its own known-bad input, two current controls
    disagree, or the control crashed at this static part (remembered);
    ``"undemonstrated"`` — no current control at all. ``entry`` — the control
    entry it stands on, when one exists. ``executed`` and ``reverified`` —
    whether a fixture (and the gate) ran to decide it: always ``False`` from
    ``admission_state``, which never runs anything; the sweep's ``admission``
    (U20) sets them.
    """

    state: str
    entry: Any = None
    reason: str = ""
    executed: bool = False
    reverified: bool = False


def _control_failure(record: Mapping[str, Any]) -> str:
    """``control <kind>: <why>`` for a remembered control that proved nothing."""
    verdict = record["verdict"]
    text = (verdict.error or verdict.skip_reason or verdict.detail or "").strip()
    text = text.splitlines()[0] if text else ""
    if text.startswith("control "):
        return text
    return f"control {record['kind']}: {text}" if text else f"control {record['kind']}"


def _control_moved(control: ControlEntry, now: _Now) -> str:
    """Why ``control``'s recorded inputs are not current, or ``""`` when they
    are: its files and listings by digest, and — when the fixture got the LIVE
    host — the host params it read, against the live projection. An opaque
    control is never current."""
    reads = control.reads or {}
    opaque = list(reads.get("opaque") or ())
    if opaque:
        return "opaque control inputs: " + ", ".join(opaque)
    for spelled, digest in (reads.get("files") or {}).items():
        where = now.locate(spelled)
        if where is None or now.digests.digest(where) != digest:
            return f"{spelled} changed"
    for spelled, digest in (reads.get("dirs") or {}).items():
        where = now.locate(spelled)
        if where is None or _dir_digest(where) != digest:
            return f"listing of {spelled} changed"
    if control.host == "live":
        for row in reads.get("host") or ():
            if now.flat is None:
                return f"{_NO_MODEL}, and the control read the live host"
            digest, _value = now.param(tuple(row[0]), row[1])
            if digest != row[1]:
                return f"host {_dotted(tuple(row[0]))} changed"
    return ""


def _fixture_moved(control: ControlEntry, now: _Now) -> list[str]:
    """The files of the fixture's recorded code closure whose bytes moved."""
    return _snapshot_moved(control.fixture, now)


def _snapshot_moved(snapshot: Any, now: _Now) -> list[str]:
    """The files of a fixture-closure snapshot (``{"digest", "files"}``) whose
    bytes are not what it recorded — every one of them when it is no snapshot."""
    files = (snapshot or {}).get("files") if isinstance(snapshot, Mapping) else None
    if not isinstance(files, Mapping):
        return ["(no recorded fixture closure)"]
    moved = []
    for spelled, digest in sorted(files.items()):
        where = now.locate(spelled)
        if where is None or now.digests.digest(where) != digest:
            moved.append(spelled)
    return moved


def _hint_holds(control: ControlEntry, now: _Now, verified: Mapping[str, Any]) -> bool:
    """§3.8 step 3: the control was demonstrated on exactly this fixture code —
    its own recorded closure is unchanged, or a re-verification recorded in
    ``controls.json`` (``verified``: ``{entry name: snapshot}`` for this gate)
    matched it against the closure as it is now."""
    if not _fixture_moved(control, now):
        return True
    snapshot = verified.get(control.name) if isinstance(verified, Mapping) else None
    return snapshot is not None and not _snapshot_moved(snapshot, now)


def _control_order(root: str, gate_id: str) -> Callable[[ControlEntry], tuple]:
    last: dict[str, tuple[str, int]] = {}
    for index, run in enumerate(read_obs(root, gate_id, control=True)):
        last[run.get("entry", "")] = (str(run.get("when") or ""), index)
    return lambda c: (*last.get(c.name, ("", -1)), c.name)


def _admission(now: _Now, spec: Any, fn: Any, held: Mapping[str, Any],
               notes: list | None = None, verified: Mapping[str, Any] | None = None
               ) -> Admission:
    """§3.8 steps 1-3 and the remembered control failure, from records alone.
    ``verified`` is ``controls.json`` (``_read_verified``): a closure a sweep
    re-verified reads admitted, not pending."""
    static, _parts = now.static(spec, fn)
    mine = (verified or {}).get(spec.id) or {}
    record = held.get(f"control:{spec.id}")
    # An availability skip of the control proves nothing either way, and is
    # re-evaluated where it is shown: while the tool is missing the GATE reads
    # skipped before admission is asked; once it is here the record is moot.
    if record is not None and record["kind"] != "availability" \
            and record["input_rho"] == static:
        return Admission("not-admitted", None, _control_failure(record))
    try:
        controls = read_controls(now.root, spec.id, problems=notes)
    except AtompipeError as exc:
        return Admission("undemonstrated", None, str(exc))
    candidates = [c for c in controls if c.static == static]
    if not candidates:
        elsewhere = f" ({len(controls)} recorded at other versions)" if controls else ""
        return Admission("undemonstrated", None, f"no control entry at this version{elsewhere}")
    order = _control_order(now.root, spec.id)
    current: list[ControlEntry] = []
    moved: list[str] = []
    for control in candidates:
        why = _control_moved(control, now)
        if why:
            moved.append(why)
        else:
            current.append(control)
    if not current:
        return Admission("undemonstrated", max(candidates, key=order),
                         f"control inputs moved: {moved[0]}")
    # The fixture closure is a HINT (§3.8): an entry whose fixture code is
    # unchanged — or that a sweep re-verified against the code as it is now —
    # was demonstrated on exactly this; one whose fixture code moved may still
    # be (early cutoff), but only re-running the fixture can say, and nothing
    # here runs. Among hint matches, disagreement is the control analogue of two
    # outcomes; without one, every current candidate decides.
    hinted = [c for c in current if _hint_holds(c, now, mine)]
    pool = hinted or current
    chosen = max(pool, key=order)
    if len({c.bad for c in pool}) > 1:
        names = ", ".join(sorted(c.name for c in pool))
        return Admission("not-admitted", chosen,
                         f"two control outcomes recorded for identical inputs ({names})")
    if chosen.bad == "pass":
        fixture = getattr(spec.negative_control, "fixture", "") or "its fixture"
        return Admission("not-admitted", chosen, f"PASSED its own known-bad fixture {fixture}")
    if hinted:
        return Admission("admitted", chosen)
    files = sorted({path for c in pool for path in _fixture_moved(c, now)})
    return Admission("pending", chosen,
                     f"control inputs moved ({', '.join(files)}); the next check re-verifies")


def admission_state(root: str, spec: Any, fn: Any, *, projection: Any,
                    digests: FileDigests | None = None,
                    anchors: Anchors | None = None) -> Admission:
    """Is ``spec``'s control demonstrated at its current version? From the
    control entries and the remembered control failures alone: it **never runs
    a fixture** (only ``check`` may spend that time).

    ``"admitted"`` — a control entry whose static part (spine, the gate's code,
    its owner's ``selftest/`` walk, the NegativeControl fields) is current, whose
    recorded file, listing and — for a live host — host-param reads are current
    (against ``projection``'s flat params), and whose fixture closure is
    unchanged. ``"pending"`` — the same, but the fixture's code closure moved
    (the bracket's fixtures import its model): it counts, with the note
    ``control inputs moved (<files>); the next check re-verifies``.
    ``"not-admitted"`` — the current control PASSED its known-bad input, or two
    current controls disagree, or the control crashed, was unusable or skipped
    itself at this static part (remembered under ``control:<gate>``).
    ``"undemonstrated"`` — no current control.

    ``anchors`` defaults to the root's plus the pack ``fn`` came from (as
    ``record_control``'s). A moved fixture closure that a sweep re-verified by
    its values (``CONTROLS_CACHE``) reads admitted; one nothing has re-run yet
    reads pending, which counts.
    """
    if anchors is None:
        anchors = _default_anchors(root, spec, fn)
    now = _Now(root, projection, None, anchors=anchors, digests=digests)
    return _admission(now, spec, fn, remembered(root), verified=_read_verified(now.root))


# --------------------------------------------------------------------------- #
# the one resolver
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Row:
    """How ``resolve`` reached one gate's effective verdict.

    ``state`` — the gate's cache state (``"fresh"``, ``"stale"``,
    ``"unknown"``, ``"never"``), or ``"legacy"`` for a ledger verdict recorded
    before 1.2, ``"orphan"`` for a gate this project does not register.
    ``cached`` — the verdict is a cache entry's, as recorded; ``fresh`` — and it
    is current and counts (Fresh, admitted or pending, not superseded);
    ``stale_reason`` — why it is in ``stale_gates`` (``""`` when it is not);
    ``entry`` — the verdict entry it stands on, if any; ``admission`` — the
    ``Admission`` a Fresh entry was judged under (``None`` elsewhere); ``when``
    — the obs run, commit time or remembered time it dates from (``""`` when
    nothing says; a reader renders that as unknown, never 0); ``notes``.
    """

    gate: str
    state: str
    cached: bool = False
    fresh: bool = False
    stale_reason: str = ""
    entry: Any = None
    admission: Any = None
    when: str = ""
    notes: tuple = ()


@dataclass
class Resolution:
    """What every reader renders: ``verdicts`` — one effective verdict per gate
    that has one, registered gates in registration order, then orphans by id;
    ``stale_gates`` — Stale, Unknown, undemonstrated, legacy and orphan rows
    (what ``claims.resolve_status`` reads as not current); ``rows`` —
    ``{gate: Row}``; ``notes`` — instrument mismatches, opaque channels,
    ignored (hand-edited) entries, two outcomes, pending admissions,
    defining-file digests, one line each; ``read_sets`` — ``last_read_sets``:
    which params each gate read when it last executed (``Param.gates``,
    ``why``); ``anchors`` — the ones every entry was judged against, so a
    reader can find a ``<pack:NAME>/...`` path the entries name (``watched_paths``)."""

    verdicts: list = field(default_factory=list)
    stale_gates: frozenset = frozenset()
    rows: dict = field(default_factory=dict)
    notes: list = field(default_factory=list)
    read_sets: dict = field(default_factory=dict)
    anchors: Any = None


def _as_spec(verdict: Verdict, spec: Any) -> Verdict:
    """``verdict`` under ``spec``'s identity — the spec is the only authority on
    a gate's claims, tier and pack (``run_gate``'s rule), so a stale verdict
    binds to the claims the gate covers NOW."""
    return dataclasses.replace(verdict, gate=spec.id, claims=list(spec.claims or ()),
                               tier=Tier(int(spec.tier)), pack=spec.pack or "")


def _synthesized(spec: Any, **fields: Any) -> Verdict:
    return Verdict(gate=spec.id, claims=list(spec.claims or ()), tier=Tier(int(spec.tier)),
                   pack=spec.pack or "", passed=False, **fields)


def _orphan_entries(root: str, registered: set, notes: list) -> dict[str, list[Entry]]:
    base = os.path.join(root, _STATE_DIR, _VERDICTS_DIR)
    try:
        names = sorted(os.listdir(base))
    except OSError:
        return {}
    found: dict[str, list[Entry]] = {}
    for name in names:
        if name in registered or not os.path.isdir(os.path.join(base, name)):
            continue
        entries = _gate_entries(root, name, notes)
        if entries:
            found[name] = entries
    return found


def resolve(root: str, registry: Any, projection: Any, ledger: Any, *,
            model_error: str = "", availability: Callable[[Any], tuple] | None = None,
            digests: FileDigests | None = None, anchors: Anchors | None = None,
            now: str = "", model: Any = None) -> Resolution:
    """Every gate's effective verdict — the ONE producer every reader uses (R-5).

    It never runs a gate or a fixture. Per registered gate, in registration
    order, the first rule that applies:

    1. **Availability** (``availability(spec) -> (ok, reason)``, default
       ``gates.availability``) fails: a skipped verdict with the reason —
       ``cached pass exists; <reason> here`` when a Fresh PASS exists, because a
       PASS committed from a machine with trimesh must never read PASS on one
       without (invariant 1). A Fresh FAIL is still served (R-3: a refutation
       keeps its power everywhere).
    2. A **remembered** crash or self-skip (``last_outcomes.json``) that
       supersedes — its ``input_rho`` is the Fresh entry's rho — or, with no
       Fresh entry, is displayed — its ``input_rho`` is ``""`` or a rho
       recomputable now, or the gate has no entry at all. Invariant 2: a crash
       proves nothing, and neither does the PASS it followed.
    3. A **Fresh** entry, then admission (PD-08, X14): a PASS counts when its
       control is admitted or pending; undemonstrated, it reads stale
       (``control not demonstrated at this version — run atompipe check``); not
       admitted, an error ``not admitted: <why>``. A FAIL stays FAIL unless not
       admitted (then that error; it blocks either way).
    4. Otherwise the **latest entry**, stale with its reasons (Unknown with its
       reason; ``model_error`` joins the "model does not load" one). Two
       outcomes at the current rho: stale, or an error once
       ``TWO_OUTCOMES_IS_ERROR`` is True.
    5. A **legacy** ``ledger.verdicts`` row with no rho: stale, ``recorded
       before per-gate tracing`` (Q1.4; until 1.3 drops them).
    6. Nothing: no row — the claim reads PENDING.

    Then **orphans** — entries, remembered outcomes or legacy rows of gates this
    project does not register — sorted by id, stale ``gate not registered in
    this project`` (tests:H2: an unregistered gate's verdict still reaches the
    page; it never counts).

    ``ledger`` is read, never written: the resolution is a VIEW a caller lays
    over it (``dataclasses.replace(ledger, verdicts=resolution.verdicts)``) and
    never saves. ``now`` is the caller's single clock stamp, accepted so every
    reader passes the same one; nothing here compares times. ``model`` as for
    ``freshness``.
    """
    if availability is None:
        from . import gates as _gates                  # gates imports this module
        availability = _gates.availability
    here = _now_for(root, registry, projection, ledger, anchors=anchors, digests=digests,
                    model=model)
    root_abs = here.root
    notes: list[str] = []
    pairs = list(registry.pairs()) if registry is not None else []
    registered = {spec.id for spec, _fn in pairs}
    held = remembered(root_abs)
    verified = _read_verified(root_abs)
    entries = {spec.id: _gate_entries(root_abs, spec.id, notes) for spec, _fn in pairs}
    orphans = _orphan_entries(root_abs, registered, notes)
    obs = _obs_names(root_abs, list(entries) + list(orphans))
    times = _commit_times(root_abs, [e for es in (*entries.values(), *orphans.values())
                                     for e in es], lambda e: (e.gate, e.name) in obs)
    legacy: dict[str, Verdict] = {}
    for verdict in getattr(ledger, "verdicts", None) or ():
        if not verdict.rho:
            legacy[verdict.gate] = verdict

    verdicts_out: list[Verdict] = []
    rows: dict[str, Row] = {}
    stale: set[str] = set()

    def when_of(entry: Any, order: Callable[[Entry], tuple]) -> str:
        return order(entry)[0] if entry is not None else ""

    def emit(verdict: Verdict, row: Row) -> None:
        verdicts_out.append(verdict)
        rows[row.gate] = row
        if row.stale_reason:
            stale.add(row.gate)

    for spec, fn in pairs:
        gid = spec.id
        code = code_digest(spec, fn, anchors=here.anchors)
        if code.fallback == "defining-file":
            notes.append(f"{gid} — code digested as its defining file "
                         f"({', '.join(code.files)}): a value it closes over is not seen")
        order = _entry_order(root_abs, gid, times)
        state = _judge(spec, code, entries[gid], here, order)
        entry = state.entry
        row_notes = tuple(getattr(state, "notes", ()) or ())
        notes.extend(f"{gid} — {note}" for note in row_notes)
        if isinstance(state, Unknown) and state.reason.startswith("opaque inputs: "):
            notes.append(f"{gid} — {state.reason}")
        # (two outcomes need no line of their own: read_entries already wrote one)

        # 1. availability
        ok, why = availability(spec)
        if not ok:
            why = why or "its tooling is not available"
            if isinstance(state, Fresh) and state.entry.verdict.get("passed") is False:
                emit(_as_spec(entry.to_verdict(), spec),
                     Row(gid, state.state, cached=True, fresh=True, entry=entry,
                         when=when_of(entry, order), notes=row_notes))
            else:
                reason = f"cached pass exists; {why} here" if isinstance(state, Fresh) else why
                emit(_synthesized(spec, skipped=True, skip_reason=reason),
                     Row(gid, state.state, entry=entry, notes=row_notes))
            continue

        # 2. a remembered crash or self-skip
        record = held.get(gid)
        if record is not None and record["kind"] in ("error", "self-skip"):
            if isinstance(state, Fresh):
                applies = record["input_rho"] == state.entry.rho
            else:
                # With no entry at all there is nothing better to show, and
                # "never run" would be false (S-68): the crash is the outcome.
                applies = isinstance(state, Never) or record["input_rho"] == "" \
                    or record["input_rho"] in state.current
            if applies:
                extra = (f"supersedes the cached {entry.name}",) if isinstance(state, Fresh) \
                    else ()
                emit(_as_spec(record["verdict"], spec),
                     Row(gid, state.state, entry=entry, when=record["when"],
                         notes=row_notes + (f"remembered {record['kind']}",) + extra))
                continue

        # 3. a Fresh entry, under admission
        if isinstance(state, Fresh):
            verdict = _as_spec(entry.to_verdict(), spec)
            admission = _admission(here, spec, fn, held, notes, verified)
            when = when_of(entry, order)
            if admission.state == "not-admitted":
                emit(_synthesized(spec, error=f"not admitted: {admission.reason}",
                                  rho=entry.rho),
                     Row(gid, state.state, entry=entry, admission=admission, when=when,
                         notes=row_notes))
            elif admission.state == "undemonstrated":
                emit(verdict, Row(gid, state.state, cached=True, stale_reason=_UNDEMONSTRATED,
                                  entry=entry, admission=admission, when=when, notes=row_notes))
            else:
                pending = (admission.reason,) if admission.state == "pending" else ()
                notes.extend(f"{gid} — {note}" for note in pending)
                emit(verdict, Row(gid, state.state, cached=True, fresh=True, entry=entry,
                                  admission=admission, when=when, notes=row_notes + pending))
            continue

        # 4. the latest entry, stale
        if isinstance(state, (Stale, Unknown)):
            when = when_of(entry, order)
            if isinstance(state, Stale) and state.conflict and TWO_OUTCOMES_IS_ERROR:
                emit(_synthesized(spec, error=state.reasons[0], rho=entry.rho),
                     Row(gid, state.state, entry=entry, when=when, notes=row_notes))
                continue
            if isinstance(state, Stale):
                reason = _stale_text(state.reasons)
            elif state.reason == _NO_MODEL and model_error:
                reason = f"{_NO_MODEL}: {model_error}"
            else:
                reason = state.reason
            emit(_as_spec(entry.to_verdict(), spec),
                 Row(gid, state.state, cached=True, stale_reason=reason, entry=entry,
                     when=when, notes=row_notes))
            continue

        # 5. a ledger verdict from before per-gate tracing
        if gid in legacy:
            emit(_as_spec(legacy[gid], spec), Row(gid, "legacy", stale_reason=_LEGACY))
        # 6. nothing: no row

    # orphans: what this project's cache and memory hold for gates it does not register
    orphan_ids = set(orphans) | {key for key, rec in held.items()
                                 if not key.startswith("control:") and key not in registered
                                 and rec["kind"] != "availability"}
    orphan_ids |= {gid for gid in legacy if gid not in registered}
    for gid in sorted(orphan_ids):
        if gid in orphans:
            order = _entry_order(root_abs, gid, times)
            latest = max(orphans[gid], key=order)
            emit(latest.to_verdict(), Row(gid, "orphan", cached=True, stale_reason=_ORPHAN,
                                          entry=latest, when=when_of(latest, order)))
        elif gid in held:
            record = held[gid]
            emit(record["verdict"], Row(gid, "orphan", stale_reason=_ORPHAN,
                                        when=record["when"],
                                        notes=(f"remembered {record['kind']}",)))
        else:
            emit(legacy[gid], Row(gid, "legacy", stale_reason=_LEGACY))

    return Resolution(verdicts=verdicts_out, stale_gates=frozenset(stale), rows=rows,
                      notes=list(dict.fromkeys(notes)), read_sets=last_read_sets(root_abs),
                      anchors=here.anchors)


# =========================================================================== #
# part four: admission at its current version, the sweep, last_check
# =========================================================================== #
#: Where a sweep remembers the controls it re-verified by their values:
#: ``{gate: {control entry name: {"digest", "files": {path: sha}}}}`` — the
#: fixture closure as it was when the values last matched. Untracked (it names
#: this checkout's fixture bytes), and a HINT only: an unreadable file reads as
#: empty and costs one fixture run per control, never a wrong admission. Why a
#: file of its own: a re-verification that found the control's values unmoved
#: must write nothing tracked, and the entry's own ``fixture`` hint cannot be
#: updated in place (O_EXCL). *Rejected:* a new tracked control entry per
#: re-verification — six new files for every model edit, and the transcript's
#: "nothing new" breaks; re-running the fixture on every check — the one cost
#: the early cutoff exists to avoid.
CONTROLS_CACHE = ".atompipe/cache/controls.json"

#: What ``last_check.json``'s fingerprint watches, relative to the project root.
#: The record directories (1.3's layout; empty until then), the verdict cache,
#: the model, the project's gates and selftest, the project marker, project-local
#: packs and the objectives file (P3). ``watched_paths`` adds, per entry the
#: resolution used, the files and listings it read and its code files, plus each
#: loaded pack's ``gates/`` and ``pack.json``; ``fingerprint`` adds the spine
#: digest. It is the one export P3's Stop hook imports to ask "did anything a
#: verdict stands on move since the last check?" without importing a project's
#: code. ``.atompipe/ledger.json`` is deliberately absent: from 1.3 it is the
#: generated index every command rewrites, and watching it would make every
#: command look like a change.
WATCHED = (
    "claims/**", "params/**", "decisions/**", "needs/**", "inputs/**", "results/**",
    "views/**", ".atompipe/verdicts/**", "model/**", "gates/**", "selftest/**",
    ".atompipe/project.json", ".atompipe/packs/**", "objectives.json",
)

_LAST_CHECK = "last_check.json"
_DIGESTS_NAME = "digests.json"
_KNOWN_GOOD = "known_good.py"
_ABOVE_CEILING = "above the tier ceiling"
_EXCLUDED = "excluded by --only"

#: Never watched: scratch the spine or Python writes beside what is.
_UNWATCHED_SUFFIXES = (".tmp", ".lock") + _BYTECODE


# --------------------------------------------------------------------------- #
# the known-good host (D-27, S-07)
# --------------------------------------------------------------------------- #
def _known_good_module(root: str) -> Any:
    """``<root>/selftest/known_good.py``, loaded fresh and recorded, or ``None``
    when the project has none. A file that will not import raises
    ``AtompipeError``: every project control is built on it, and a broken one
    must read as a broken control, never as "no known-good design" — that would
    quietly hand the fixtures the live host again."""
    base = os.path.abspath(root)
    path = os.path.join(base, _SELFTEST, _KNOWN_GOOD)
    if not os.path.isfile(path):
        return None
    name = "_atompipe_known_good_" + hashlib.sha256(os.fsencode(path)).hexdigest()[:12]
    try:
        return modelio.load_source_module(path, name=name, roots=[base])
    except KeyboardInterrupt:
        raise
    except BaseException as exc:                   # SystemExit included: see gates.run_gate
        raise AtompipeError(
            f"{_SELFTEST}/{_KNOWN_GOOD} failed to import ({type(exc).__name__}: {exc}) — "
            f"every project control is built on it, so none is demonstrated until it "
            f"loads") from exc


def _known_good(root: str, ctx: Any) -> tuple[Any, Any] | None:
    """``(the known-good context, the module's code closure)``, or ``None``."""
    module = _known_good_module(root)
    make = getattr(module, "context", None) if module is not None else None
    if not callable(make):
        return None
    # A copy: `context` is project code, and the context it is handed is the
    # one every later gate of the sweep reads.
    names = {f.name for f in dataclasses.fields(ctx)}
    changes: dict[str, Any] = {"params": _plain(getattr(ctx, "params", None) or {}),
                               "extra": dict(getattr(ctx, "extra", None) or {})}
    ledger = getattr(ctx, "ledger", None)
    if ledger is not None:
        changes["ledger"] = LedgerView(ledger, None)
    handed = dataclasses.replace(ctx, **{k: v for k, v in changes.items() if k in names})
    try:
        built = make(handed)
    except KeyboardInterrupt:
        raise
    except BaseException as exc:
        raise AtompipeError(
            f"{_SELFTEST}/{_KNOWN_GOOD}: context() raised {type(exc).__name__}: {exc} — "
            f"the known-good design could not be built, so no project control can be "
            f"demonstrated") from exc
    if not isinstance(built, type(ctx)):
        raise AtompipeError(
            f"{_SELFTEST}/{_KNOWN_GOOD}: context() returned {type(built).__name__}, not "
            f"the {type(ctx).__name__} it was handed")
    return built, modelio.code_closure(module)


def known_good_context(root: str, ctx: Any) -> Any:
    """The context every PROJECT control fixture is handed: ``ctx`` rebuilt by
    ``<root>/selftest/known_good.py``'s ``context(ctx)`` — the design every
    control is one change away from — or ``None`` when the project has no such
    function (then the fixture gets the live host, and the control entry says
    ``host: "live"``).

    Why (S-07, D-27): a fixture handed the LIVE design builds its known-bad
    input from whatever the live design is. The bracket's fails deflection on
    purpose, so the identity fixture ``return ctx`` was reported "correctly
    failed ... ~64x worse" and demonstrated nothing. On the known-good design
    it passes, and a control that passes its own known-bad input is a logger:
    not admitted. Pack fixtures never get it — SEALED is enforced for them by
    the seal detector (``packs.seal_findings``), not by substitution.

    Loaded through ``modelio.load_source_module`` (fresh bytes, recorded
    closure); ``context`` receives a copy of ``ctx``. Raises ``AtompipeError``
    when the module does not import, ``context`` raises, or it returns something
    other than a context of ``ctx``'s type.
    """
    found = _known_good(root, ctx)
    return None if found is None else found[0]


def _control_host(root: str, spec: Any, fn: Any, host_ctx: Any) -> tuple[Any, str, Any]:
    """``(the context a control's fixture is handed, "known-good" | "live", the
    known-good module's code closure or None)``. Each control gets a memo of its
    own: a known-bad mesh loaded into the sweep's memo is one sweep's accident
    away from a real gate's read."""
    if "memo" in {f.name for f in dataclasses.fields(host_ctx)}:
        host_ctx = dataclasses.replace(host_ctx, memo={})
    if _pack_dir_of(fn):
        return host_ctx, "live", None
    found = _known_good(root, host_ctx)
    if found is None:
        return host_ctx, "live", None
    return found[0], "known-good", found[1]


def _add_closure(trace: GateTrace, closure: Any) -> None:
    """Fold the known-good module's closure into the fixture's recorded code:
    the control's lookup hint must move when the known-good design's code does
    (the bracket's model sits in both)."""
    if not isinstance(closure, modelio.CodeClosure):
        return
    own = trace.fixture_code
    files = dict(own.files) if isinstance(own, modelio.CodeClosure) else {}
    for path, sha in closure.files:
        files.setdefault(path, sha)
    merged = tuple(sorted(files.items()))
    trace.fixture_code = (dataclasses.replace(own, files=merged)
                          if isinstance(own, modelio.CodeClosure)
                          else modelio.CodeClosure(files=merged))


def _fresh_control_dir(root: str, gate_id: str, sweep_out: str = "") -> str:
    """``<root>/.atompipe/out/controls/<gate>/``, emptied (``CONTROL_OUT_DIR``):
    a file a previous control left there must never become this one's read.
    Refuses a directory that holds the sweep's own ``out_dir`` — the evidence a
    cached PASS cites lives there (packs:H5)."""
    path = os.path.join(os.path.abspath(root), *CONTROL_OUT_DIR.split("/"),
                        _check_gate_id(gate_id))
    if sweep_out:
        out = os.path.abspath(sweep_out)
        if out == path or out.startswith(path + os.sep):
            raise AtompipeError(f"the control scratch {_shown(root, path)} holds the "
                                f"sweep's out_dir {out}; refusing to empty it")
    shutil.rmtree(path, ignore_errors=True)
    return path


# --------------------------------------------------------------------------- #
# controls.json: re-verified fixture closures (untracked, a hint)
# --------------------------------------------------------------------------- #
def _verified_path(root: str) -> str:
    return os.path.join(os.path.abspath(root), *CONTROLS_CACHE.split("/"))


def _is_snapshot(snapshot: Any) -> bool:
    return (isinstance(snapshot, dict) and isinstance(snapshot.get("digest"), str)
            and isinstance(snapshot.get("files"), dict)
            and all(isinstance(k, str) and (v is None or isinstance(v, str))
                    for k, v in snapshot["files"].items()))


def _read_verified(root: str) -> dict:
    """``controls.json`` (``CONTROLS_CACHE``), or ``{}`` when there is none or
    it cannot be read — a hint only (see the constant)."""
    data_bytes = _file_bytes(_verified_path(root))
    if data_bytes is None:
        return {}
    try:
        data = _strict_json(data_bytes.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {gate: {name: snap for name, snap in names.items()
                   if isinstance(name, str) and _is_snapshot(snap)}
            for gate, names in data.items() if isinstance(gate, str) and isinstance(names, dict)}


def _write_verified(root: str, updates: Mapping[str, Mapping[str, Any]]) -> None:
    """Merge ``updates`` into ``controls.json``, dropping names whose control
    entry is gone. Written only when something changed."""
    if not updates:
        return
    before = _read_verified(root)
    merged = {gate: dict(names) for gate, names in before.items()}
    for gate, names in updates.items():
        merged.setdefault(gate, {}).update(names)
    kept: dict[str, dict] = {}
    for gate, names in sorted(merged.items()):
        try:
            directory = _gate_dir(root, gate)
        except AtompipeError:
            continue
        live = {name: snap for name, snap in sorted(names.items())
                if os.path.isfile(os.path.join(directory, name + ".json"))}
        if live:
            kept[gate] = live
    if kept != before:
        atomic_write_json(_verified_path(root), _clean(kept))


# --------------------------------------------------------------------------- #
# admission, in check: run what the records cannot settle
# --------------------------------------------------------------------------- #
@dataclass
class _Session:
    """What one sweep — or one ``admission`` call — shares across its gates:
    the current state (one ``selftest/`` walk per owner), the remembered
    outcomes and ``controls.json`` as they were when it began, and what it has
    to write at the end."""

    root: str
    now: _Now
    anchors: Anchors
    digests: FileDigests
    held: dict
    verified: dict
    record: bool
    when: str
    out_dir: str = ""
    verified_out: dict = field(default_factory=dict)
    notes: list = field(default_factory=list)


def _session(root: str, host_ctx: Any, *, projection: Any, anchors: Anchors,
             digests: FileDigests, record: bool, when: str, out_dir: str = "") -> _Session:
    base = os.path.abspath(root)
    now = _Now(base, projection, None, anchors=anchors, digests=digests)
    if projection is None and isinstance(getattr(host_ctx, "params", None), dict):
        # No projection to re-read a live host's params from: the host the
        # fixture is handed IS the live state, so its params are what a live
        # control's host reads are compared against.
        now.flat = host_ctx.params
    return _Session(root=base, now=now, anchors=anchors, digests=digests,
                    held=remembered(base), verified=_read_verified(base), record=record,
                    when=when, out_dir=out_dir)


def _passed_known_bad(spec: Any) -> str:
    fixture = getattr(spec.negative_control, "fixture", "") or "its fixture"
    return f"{_PASSED_ITS_KNOWN_BAD} {fixture}"


def _decide(pool: list, spec: Any, order: Callable[[ControlEntry], tuple],
            **flags: bool) -> Admission:
    """The demonstrations in ``pool`` settle it: they disagree — not admitted
    (the control analogue of two outcomes, §3.7); the latest PASSED its
    known-bad input — not admitted; else admitted, reject-only."""
    chosen = max(pool, key=order)
    if len({c.bad for c in pool}) > 1:
        names = ", ".join(sorted(c.name for c in pool))
        return Admission("not-admitted", chosen,
                         f"two control outcomes recorded for identical inputs ({names})",
                         **flags)
    if chosen.bad == "pass":
        return Admission("not-admitted", chosen, _passed_known_bad(spec), **flags)
    return Admission("admitted", chosen, "", **flags)


def _ledger_now(ledger: Any, key: str) -> str | None:
    """The digest a gate reading ledger ``key`` from ``ledger`` would record."""
    if ledger is None or not isinstance(key, str):
        return None
    if key.startswith("claim:"):
        cid = key[len("claim:"):]
        return _claim_digest(next((c for c in (_peek(ledger, "claims") or ())
                                   if getattr(c, "id", None) == cid), None))
    if key in _LEDGER_WHOLE and key in _LEDGER_FIELD_SET:
        return _ledger_digest(key, _peek(ledger, key))
    return None


def _unvouched(built: Any, given: Any, trace: GateTrace) -> str:
    """Why a fixture-only run cannot stand in for the whole control, or ``""``.

    The spec's comparison is the control-context VALUES the gate read (params
    and ledger). That is sound only for what a control entry keys, so anything
    a fixture hands its gate outside those channels sends the control to a full
    run instead — a cost, never a wrong admission. What would have slipped
    through: a fixture that WRITES the known-bad mesh the gate then reads (the
    gate's read of a file written in its window is its own output and is never
    keyed); one that moves ``ctx.root`` (the gate then reads another tree, and
    ``test_freshness``'s ``file_bad`` does exactly that); one that returns a
    dict, merged into ``extra``, which no trace records.
    """
    if trace.files_written:
        return "the fixture wrote files its gate would read"
    if trace.opaque:
        return "the fixture used an input no trace can key"
    for name in ("root", "out_dir", "tier"):
        if getattr(built, name, None) != getattr(given, name, None):
            return f"the fixture moved ctx.{name}"
    extra_built, opaque_built = _digest(dict(getattr(built, "extra", None) or {}))
    extra_given, _ = _digest(dict(getattr(given, "extra", None) or {}))
    if opaque_built or extra_built != extra_given:
        return "the fixture handed its gate ctx.extra, which no control entry keys"
    return ""


def _values_match(control: ControlEntry, built: Any, fixture_reads: Reads,
                  anchors: Anchors) -> bool:
    """§3.8 step 4: does the context the fixture built now hand the gate what
    ``control`` recorded it read — every param path by digest (``ABSENT`` for a
    miss), every ledger read — while the fixture read no file the entry does
    not already key?"""
    recorded = control.reads or {}
    if not set(fixture_reads.files) <= set(recorded.get("files") or {}):
        return False
    if not set(fixture_reads.dirs) <= set(recorded.get("dirs") or {}):
        return False
    params = getattr(built, "params", None)
    for row in recorded.get("params") or ():
        digest, _value = _param_at(params, tuple(row[0]), row[1], anchors)
        if digest != row[1]:
            return False
    ledger = getattr(built, "ledger", None)
    for key, digest in (recorded.get("ledger") or {}).items():
        if _ledger_now(ledger, key) != digest:
            return False
    return True


def _reverify(s: _Session, spec: Any, fn: Any, host_ctx: Any, current: list,
              order: Callable[[ControlEntry], tuple]) -> Admission | None:
    """Run the fixture alone and compare what it built with what each current
    candidate's gate read (§3.8 step 4, the early cutoff). A match settles the
    gate WITHOUT calling it — possibly a twenty-minute solver — and writes no
    tracked file; ``controls.json`` remembers the closure it matched under.
    ``None`` sends the control to a full run: the fixture is unusable (the full
    run learns why, and remembers it), or it handed its gate something no entry
    keys (``_unvouched``), or no candidate's values match."""
    from . import gates as _gates                  # gates imports this module
    out_dir = _fresh_control_dir(s.root, spec.id, s.out_dir)
    try:
        handed, _host, closure = _control_host(s.root, spec, fn, host_ctx)
    except AtompipeError:
        return None
    trace = GateTrace(kind="control", anchors=s.anchors)
    try:
        built = _gates.run_fixture(spec, fn, handed, trace=trace, out_dir=out_dir)
    except AtompipeError:
        return None
    _add_closure(trace, closure)
    if _unvouched(built, dataclasses.replace(handed, out_dir=out_dir), trace):
        return None
    _static_digest, parts = s.now.static(spec, fn)
    owner = _owner_dir(fn, s.root)
    static_files = [os.path.join(owner, *rel.split("/")) for rel in parts["selftest"]["files"]]
    fixture_reads = Reads.from_trace(trace, anchors=s.anchors, digests=s.digests,
                                     static=static_files)
    if fixture_reads.opaque or trace.model_used:
        return None
    matches = [c for c in current if _values_match(c, built, fixture_reads, s.anchors)]
    if not matches:
        return None
    if s.record:
        snapshot = _fixture_part(trace, s.anchors)
        for control in matches:
            s.verified_out.setdefault(spec.id, {})[control.name] = snapshot
    return _decide(matches, spec, order, reverified=True)


def _run_control(s: _Session, spec: Any, fn: Any, host_ctx: Any, *, force: bool) -> Admission:
    """§3.8 steps 5-6: run the control — fixture and gate, traced, in its own
    emptied ``out_dir`` — and file it: a measurement as a control entry, a
    crash, an unusable fixture or a self-skip remembered under
    ``control:<gate>``. Under ``record=False`` it is judged exactly as it would
    be filed, and nothing is written."""
    from . import gates as _gates
    gid = spec.id
    key = f"control:{gid}"
    out_dir = _fresh_control_dir(s.root, gid, s.out_dir)
    try:
        handed, host, closure = _control_host(s.root, spec, fn, host_ctx)
    except AtompipeError as exc:
        held = Verdict(gate=f"{gid}#selftest", passed=False, tier=Tier(int(spec.tier)),
                       pack=spec.pack or "", error=str(exc).splitlines()[0] if str(exc) else
                       "negative control unusable")
        if s.record:
            remember(s.root, key, held, input_rho=s.now.static(spec, fn)[0], kind="error",
                     when=s.when)
        return Admission("not-admitted", None, _control_failure({"verdict": held,
                                                                 "kind": "error"}),
                         executed=True)
    trace = GateTrace(kind="control", anchors=s.anchors)
    result = _gates.selftest(spec, fn, handed, trace=trace, out_dir=out_dir)
    _add_closure(trace, closure)
    built = _control_entry(s.root, spec, fn, result=result, trace=trace, host=host, bad=None,
                           detail="", digests=s.digests, anchors=s.anchors)
    entry = built.entry
    if s.record:
        record_obs(s.root, gid, entry=entry.name if entry is not None else "", when=s.when,
                   duration_s=result.duration_s, cpu_s=result.cpu_s, control=True)
    if entry is None:
        if built.kind == "availability":
            # The tools went missing while it ran: a skip proves nothing either
            # way, and the gate reads skipped (BLOCKED), never not admitted.
            return Admission("undemonstrated", None,
                             result.skip_reason or "its tooling is not available",
                             executed=True)
        if s.record:
            remember(s.root, key, built.held, input_rho=built.static, kind=built.kind,
                     when=s.when)
        return Admission("not-admitted", None,
                         _control_failure({"verdict": built.held, "kind": built.kind}),
                         executed=True)
    try:
        existing = read_controls(s.root, gid)
    except AtompipeError:
        existing = []
    clash = sorted(c.name for c in existing if c.rho == entry.rho and c.bad != entry.bad)
    if s.record:
        wrote = write_control(s.root, entry)
        s.notes.extend(wrote.warnings)
        forget(s.root, key)
        # The entry on disk keeps the fixture hint it was FIRST written with
        # (same inputs, same outcome: "exists"); the closure it was just
        # demonstrated under is remembered beside it.
        s.verified_out.setdefault(gid, {})[entry.name] = entry.fixture
    if clash:
        reason = (f"control outcome differs from its cached entry ({', '.join(clash)})"
                  if force else f"two control outcomes recorded for identical inputs "
                                f"({', '.join(clash + [entry.name])})")
        return Admission("not-admitted", entry, reason, executed=True)
    if entry.bad == "pass":
        return Admission("not-admitted", entry, _passed_known_bad(spec), executed=True)
    return Admission("admitted", entry, "", executed=True)


def _admit(s: _Session, spec: Any, fn: Any, host_ctx: Any, *, may_run: bool,
           force: bool) -> Admission:
    """§3.8 steps 1-6 for one gate (see ``admission``)."""
    from . import gates as _gates
    if not may_run:
        return _admission(s.now, spec, fn, s.held, s.notes, s.verified)
    ok, _why = _gates.availability(spec)
    if not ok:
        # A control runs its gate: where the gate cannot run, neither can it.
        return _admission(s.now, spec, fn, s.held, s.notes, s.verified)
    gid = spec.id
    order = _control_order(s.root, gid)
    if not force:
        static, _parts = s.now.static(spec, fn)
        record = s.held.get(f"control:{gid}")
        failed_here = (record is not None and record["kind"] != "availability"
                       and record["input_rho"] == static)
        # A control that crashed at this static supersedes whatever entry it
        # followed, like a gate's crash (§3.9): run it again, never serve it.
        if not failed_here:
            try:
                controls = read_controls(s.root, gid, problems=s.notes)
            except AtompipeError as exc:
                s.notes.append(f"{gid}: {exc}")
                controls = []
            current = [c for c in controls if c.static == static and not _control_moved(c, s.now)]
            if current:
                mine = s.verified.get(gid) or {}
                hinted = [c for c in current if _hint_holds(c, s.now, mine)]
                if hinted:
                    return _decide(hinted, spec, order)
                found = _reverify(s, spec, fn, host_ctx, current, order)
                if found is not None:
                    return found
    return _run_control(s, spec, fn, host_ctx, force=force)


def admission(root: str, spec: Any, fn: Any, host_ctx: Any, *, may_run: bool = True,
              force: bool = False, record: bool = True, projection: Any = None,
              digests: FileDigests | None = None, anchors: Anchors | None = None,
              when: str = "") -> Admission:
    """Is ``spec``'s control demonstrated at its current version — running what
    the records cannot settle. ``check``'s form; ``admission_state`` is the
    read-only one every other reader uses.

    1. **Static.** Candidates: control entries whose ``static`` (spine, gate
       code, the owner's ``selftest/`` walk, the NegativeControl fields) is the
       current one. None — a miss.
    2. **Current.** A candidate is current when it has no opaque channel, its
       file and listing digests are current, and — its fixture having had the
       LIVE host — its host-param reads match ``projection``'s (else the host
       context's) params.
    3. **Hint.** Current candidates whose fixture closure is unchanged, or was
       re-verified under the closure as it is now (``CONTROLS_CACHE``), settle
       it with nothing run: all fired — admitted; the latest PASSED its known-bad
       input, or they disagree — not admitted.
    4. **Re-verify** (``may_run``). Otherwise the fixture runs ALONE, traced, in
       the control ``out_dir``, and what it built is compared with each current
       candidate's recorded reads (params by digest, ledger reads). A match
       settles it as 3 does, with ``reverified=True``: the gate is not called and
       no tracked file is written (``controls.json`` remembers the closure). This
       is how a model edit that moves every fixture's code, but no control
       value, costs one fixture run per gate and nothing else — and how one that
       DOES move a value (S-19's model-code half) misses.
    5. **Miss.** The control runs — fixture and gate — and is filed (unless
       ``record=False``): ``bad: "fail"`` admitted reject-only; ``bad: "pass"``
       not admitted (``PASSED its own known-bad fixture <ref>``). A crash, an
       unusable fixture or a self-skip with the tools present is remembered
       under ``control:<gate>`` at the current static and not admitted
       (``control <kind>: <why>``); a remembered one at this static is re-run,
       never served.
    6. **``force``** skips 1-4: the control always runs, and an outcome that
       differs from a cached control entry at the same ``rho_control`` is not
       admitted (``control outcome differs from its cached entry``, R-9).

    A PROJECT control's fixture is handed ``known_good_context``'s design
    (``host: "known-good"``); a pack's gets the live host. With the gate's tools
    missing nothing runs: the records answer (the sweep asks availability
    first and never gets here). ``when`` stamps a remembered failure and the
    control obs; this module reads no clock.
    """
    if anchors is None:
        anchors = _default_anchors(root, spec, fn)
    s = _session(root, host_ctx, projection=projection, anchors=anchors,
                 digests=digests if digests is not None else FileDigests(), record=record,
                 when=when, out_dir=getattr(host_ctx, "out_dir", "") or "")
    found = _admit(s, spec, fn, host_ctx, may_run=may_run, force=force)
    if record:
        _write_verified(s.root, s.verified_out)
    return found


# --------------------------------------------------------------------------- #
# the sweep
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class SweepRow:
    """One selected gate's row: ``verdict`` (``rho`` set when it ran or was
    served), ``executed`` — its function ran on the real design in this sweep;
    ``cached`` — its verdict is a cache entry's; ``fresh`` — a pass or fail keyed
    at the current inputs (executed or cached), never a skip, a crash or a
    refusal; ``stale_reason`` — why a row the sweep served is not current
    (``""``: every row a sweep produces is); ``rho``; ``admission`` — how the
    gate's control was judged, ``None`` where it was not asked (a missing tool,
    a lost control)."""

    verdict: Verdict
    executed: bool = False
    cached: bool = False
    fresh: bool = False
    stale_reason: str = ""
    rho: str = ""
    admission: Any = None


@dataclass
class SweepResult:
    """What ``sweep`` did.

    ``rows`` — one ``SweepRow`` per selected gate, registration order;
    ``counts`` — ``{"executed", "cached"}``; ``controls`` — ``{"executed"``
    (fixture and gate ran), ``"cached"`` (settled by the records),
    ``"reverified"`` (the fixture alone ran, and the values held),
    ``"not_admitted"}``; ``not_run`` — ``[(gate, "above the tier ceiling" |
    "excluded by --only")]`` for every registered gate not selected;
    ``before`` — ``freshness`` as it stood BEFORE anything ran (cli:H19: what
    was stale is decided before the sweep makes it current), and
    ``stale_before`` — ``{gate: reason}`` for the selected gates it called Stale
    or Unknown; ``notes`` — writer warnings and ignored entries. The rest is how
    it ran — ``only``, ``max_tier``, ``force``, ``record``, ``when`` — and what
    ``write_last_check`` needs: ``ledger``, ``registry``, ``anchors``.
    """

    rows: list = field(default_factory=list)
    counts: dict = field(default_factory=lambda: {"executed": 0, "cached": 0})
    controls: dict = field(default_factory=lambda: {"executed": 0, "cached": 0,
                                                    "reverified": 0, "not_admitted": 0})
    not_run: list = field(default_factory=list)
    before: dict = field(default_factory=dict)
    stale_before: dict = field(default_factory=dict)
    notes: list = field(default_factory=list)
    only: Any = None
    max_tier: int = 0
    force: bool = False
    record: bool = True
    when: str = ""
    ledger: Any = None
    registry: Any = None
    anchors: Any = None


def _filtered(only: Any) -> bool:
    """Did ``only`` name anything (``gates._selected``'s reading of it)?"""
    if only is None:
        return False
    patterns = [only] if isinstance(only, str) else [str(o) for o in only]
    return any(p.strip() for p in patterns)


def _sweep_one(s: _Session, spec: Any, fn: Any, state: Any, run_ctx: Any, *,
               force: bool) -> SweepRow:
    """§3.11 for one selected gate: availability, admission, the cache, the run."""
    from . import gates as _gates
    gid = spec.id
    # 1. availability: a skip, never "not admitted" — CI has no trimesh or omc
    ok, why = _gates.availability(spec)
    if not ok:
        why = why or "its tooling is not available"
        if isinstance(state, Fresh) and state.entry.verdict.get("passed") is False:
            # R-3: a refutation keeps its power where the tool that made it is gone
            return SweepRow(_as_spec(state.entry.to_verdict(), spec), cached=True,
                            fresh=True, rho=state.entry.rho)
        reason = f"cached pass exists; {why} here" if isinstance(state, Fresh) else why
        skipped = _synthesized(spec, skipped=True, skip_reason=reason)
        if s.record:
            remember(s.root, gid, skipped, input_rho=state.rho, kind="availability",
                     when=s.when)
        return SweepRow(skipped)

    # 2. admission (force re-runs the control)
    judged = _admit(s, spec, fn, run_ctx, may_run=True, force=force)
    if judged.state == "undemonstrated":
        return SweepRow(_synthesized(spec, skipped=True, skip_reason=judged.reason),
                        admission=judged)
    if judged.state == "not-admitted":
        refused = _synthesized(spec, error=f"not admitted: {judged.reason}",
                               rho=state.rho if isinstance(state, Fresh) else "")
        return SweepRow(refused, admission=judged)

    # 3. the cache — unless a remembered crash at these inputs superseded it
    if not force and isinstance(state, Fresh):
        held = s.held.get(gid)
        superseded = (held is not None and held["kind"] in ("error", "self-skip")
                      and held["input_rho"] == state.rho)
        if not superseded:
            return SweepRow(_as_spec(state.entry.to_verdict(), spec), cached=True, fresh=True,
                            rho=state.entry.rho, admission=judged)

    # 4. run, traced with the sweep's anchors, and key what it read
    trace = GateTrace(anchors=s.anchors)
    verdict = _gates.run_gate(spec, fn, run_ctx, trace=trace)
    keyed = _keyed(gid, spec, fn, trace=trace, reads=None, anchors=s.anchors,
                   digests=s.digests)
    verdict = dataclasses.replace(verdict, rho=keyed.rho)
    measured = verdict.outcome in ("pass", "fail")
    name = ""
    if measured:
        entry = _entry_for(spec, verdict, keyed, s.anchors)
        name = entry.name
        if s.record:
            wrote = write_entry(s.root, entry)
            s.notes.extend(wrote.warnings)
            forget(s.root, gid)
    elif s.record:
        kind = ("error" if verdict.error
                else "self-skip" if _gates.availability(spec)[0] else "availability")
        remember(s.root, gid, verdict, input_rho=state.rho, kind=kind, when=s.when)
    if s.record:
        record_obs(s.root, gid, entry=name, when=s.when, duration_s=verdict.duration_s,
                   cpu_s=verdict.cpu_s)
    return SweepRow(verdict, executed=True, fresh=measured, rho=keyed.rho, admission=judged)


def sweep(root: str, registry: Any, ctx: Any, *, projection: Any, ledger: Any,
          max_tier: int, only: Any = None, force: bool = False, record: bool = True,
          now: str = "", on_verdict: Callable[[Verdict], Any] | None = None,
          anchors: Anchors | None = None, digests: FileDigests | None = None,
          on_row: Callable[[SweepRow], Any] | None = None) -> SweepResult:
    """``check``'s loop: every selected gate, affected-only, under admission.

    Driven through ``gates.run_all(..., before=)``, so order, the tier ceiling,
    ``--only`` (a named gate above the ceiling runs, control included) and the
    lost-control refusal stay in one place. ``freshness`` is computed BEFORE
    anything runs (cli:H19). Per selected gate, in order (``_sweep_one``):

    1. **Availability** fails: a Fresh FAIL is served (R-3); otherwise skipped —
       ``cached pass exists; <why> here`` over a Fresh PASS (invariant 1) —
       remembered as ``availability``; no control runs, ``fn`` is never called.
    2. **Admission** (``admission``'s steps; ``force`` re-runs the control). Not
       admitted: ``error="not admitted: <why>"``, ``fn`` never called.
    3. Unless ``force``, a **Fresh** entry is served — unless a remembered crash
       or self-skip at its rho superseded it (§3.9: a crash proves nothing, and
       neither does the PASS it followed), which re-runs the gate.
    4. **Run**, traced with the sweep's anchors. A pass or fail is keyed and
       cached (``write_entry``), clearing any remembered outcome; anything else
       is remembered under the rho freshness computed before the run
       (``input_rho``). Every run appends obs.

    The gate runs INSIDE ``before`` rather than in ``run_all``'s own loop: that
    loop's trace carries no anchors, and a path-valued param digested without
    them — fdm's absolute mesh paths (packs:H6) — differs per checkout, so the
    entry could never read Fresh anywhere and would be rewritten per clone.

    ``record=False`` (``--no-record``) writes nothing under ``.atompipe/`` but
    gate and control scratch in ``out/``: no entry, control entry, remembered
    outcome, obs, ``controls.json`` or ``digests.json`` (S-32: a dry sweep that
    kept nothing and compared nothing global reads nothing as stale).
    ``digests`` defaults to the ``.atompipe/cache/digests.json`` stat cache
    (in-memory under ``record=False``), saved at the end of a recorded sweep.
    ``now`` is the CLI's one clock stamp (obs, remembered outcomes).
    ``on_verdict`` streams each final verdict as it lands, ``on_row`` its row.
    Takes no lock: the CLI edge holds it.
    """
    from . import gates as _gates
    root = os.path.abspath(root)
    out_dir = getattr(ctx, "out_dir", "") or store.out_dir(root)
    if anchors is None:
        anchors = anchors_for(root, registry, out_dir=out_dir)
    if digests is None:
        digests = FileDigests(os.path.join(root, _STATE_DIR, _CACHE_DIR, _DIGESTS_NAME)
                              if record else None)
    before = freshness(root, registry, projection, ledger, digests=digests, anchors=anchors,
                       model=getattr(ctx, "model", None))
    s = _session(root, ctx, projection=projection, anchors=anchors, digests=digests,
                 record=record, when=now, out_dir=out_dir)
    # The context every gate of this sweep runs on: its tier the sweep's (a gate
    # must not pick its cheap path under an expensive run), one memo shared by
    # every gate (load_file), never left on the caller's context.
    run_ctx = dataclasses.replace(ctx, tier=int(max_tier),
                                  memo=ctx.memo if getattr(ctx, "memo", None) is not None
                                  else {})
    rows: dict[str, SweepRow] = {}

    def before_hook(spec: Any, fn: Any) -> Verdict:
        found = _sweep_one(s, spec, fn, before.get(spec.id) or Never(), run_ctx, force=force)
        rows[spec.id] = found
        return found.verdict

    def landed(verdict: Verdict) -> None:
        if on_verdict is not None:
            on_verdict(verdict)
        if on_row is not None:
            on_row(rows.get(verdict.gate) or SweepRow(verdict))

    swept = _gates.run_all(registry, run_ctx, max_tier=max_tier, only=only,
                           on_verdict=landed, before=before_hook)

    result = SweepResult(only=only, max_tier=int(max_tier), force=force, record=record,
                         when=now, ledger=ledger, registry=registry, anchors=anchors,
                         before=before, notes=s.notes)
    for verdict in swept:
        # a lost control is refused by run_all before `before` is asked: no row yet
        found = rows.get(verdict.gate) or SweepRow(verdict)
        result.rows.append(found)
        result.counts["executed"] += found.executed
        result.counts["cached"] += found.cached
        judged = found.admission
        if judged is not None:
            if judged.executed:
                result.controls["executed"] += 1
            elif judged.reverified:
                result.controls["reverified"] += 1
            else:
                result.controls["cached"] += 1
            result.controls["not_admitted"] += judged.state == "not-admitted"
        state = before.get(verdict.gate)
        if isinstance(state, Stale):
            result.stale_before[verdict.gate] = _stale_text(state.reasons)
        elif isinstance(state, Unknown):
            result.stale_before[verdict.gate] = state.reason
    selected = {verdict.gate for verdict in swept}
    reason = _EXCLUDED if _filtered(only) else _ABOVE_CEILING
    result.not_run = [(spec.id, reason) for spec in registry.specs() if spec.id not in selected]
    if record:
        _write_verified(root, s.verified_out)
        digests.save()
    return result


# --------------------------------------------------------------------------- #
# last_check.json, WATCHED, the fingerprint
# --------------------------------------------------------------------------- #
def _flat_reads(reads: Mapping[str, Any]) -> dict[str, str | None]:
    """An entry's reads as ``{path: digest}``: ``param:<json path>``,
    ``file:<path>``, ``dir:<path>``, ``ledger:<key>``, ``model``,
    ``opaque:<channel>`` (digest ``None``: nothing can say)."""
    out: dict[str, str | None] = {}
    for row in reads.get("params") or ():
        out["param:" + _canonical_json(row[0])] = row[1]
    for kind, prefix in (("files", "file:"), ("dirs", "dir:")):
        for spelled, digest in (reads.get(kind) or {}).items():
            out[prefix + spelled] = digest
    for key, digest in (reads.get("ledger") or {}).items():
        out["ledger:" + key] = digest
    if reads.get("model") is not None:
        out["model"] = reads["model"]
    for name in reads.get("opaque") or ():
        out["opaque:" + name] = None
    return dict(sorted(out.items()))


def write_last_check(root: str, result: SweepResult, resolution: Resolution, *, now: str,
                     params: Mapping[str, Any] | None = None,
                     digests: FileDigests | None = None) -> str | None:
    """Write ``.atompipe/cache/last_check.json`` after a full recorded sweep;
    return its path, or ``None`` — nothing written — when the sweep was
    filtered (``--only``) or dry (``record=False``): a partial sweep's summary
    would stand for the whole project's.

    ``{"when", "spine", "fingerprint", "reads", "statuses", "counts", "worst",
    "params", "influence"}``: the CLI's stamp; the spine digest; the
    ``fingerprint`` of ``watched_paths``; per gate the reads of the entry the
    resolution used (``param:<json path>``, ``file:``, ``dir:``, ``ledger:``,
    ``model``, ``opaque:``) — what lets P3's hook say which checks a change
    touched; each claim's status under ``resolution``; the sweep's counts with
    its controls; the first blocking claim with the gate and words that explain
    it (nulls when nothing blocks); ``params`` (the parameter view, from 1.3) and
    ``influence`` (P3), empty until then. Untracked, and read by nothing in the
    sweep: ``check`` never trusts its own summary of a previous run.
    """
    if not result.record or _filtered(result.only):
        return None
    from . import claims as _claims                # a reader's module: never at import (§3.1)
    root = os.path.abspath(root)
    base = result.ledger if result.ledger is not None else Ledger()
    view = dataclasses.replace(base, verdicts=list(resolution.verdicts))
    stale = resolution.stale_gates
    statuses = _claims.statuses(view, registry=result.registry, stale_gates=stale)
    worst: dict[str, Any] = {"claim": None, "gate": None, "detail": None}
    blocking = (_claims.blocking(view, result.registry, stale_gates=stale)
                if result.registry is not None else [])
    if blocking:
        claim, status = blocking[0]
        why = _claims.explaining_verdict(claim, view.verdicts)
        worst = {"claim": claim.id, "gate": why.gate if why is not None else None,
                 "detail": ((why.detail or why.error or why.skip_reason) if why is not None
                            else str(status.value))}
    reads = {gid: _flat_reads(row.entry.reads or {})
             for gid, row in sorted(resolution.rows.items()) if row.entry is not None}
    data = {
        "when": str(now or ""),
        "spine": spine_digest(),
        "fingerprint": fingerprint(root, watched_paths(root, resolution), digests=digests),
        "reads": reads,
        "statuses": {cid: str(status.value) for cid, status in statuses.items()},
        "counts": {**result.counts, "controls": dict(result.controls)},
        "worst": worst,
        "params": dict(params or {}),
        "influence": {},
    }
    path = os.path.join(root, _STATE_DIR, _CACHE_DIR, _LAST_CHECK)
    # Fixed key order, as documented, rather than atomic_write_json's sorted one:
    # the file is read by people debugging a hook as often as by the hook.
    atomic_write_text(path, json.dumps(_clean(data), indent=2, ensure_ascii=False,
                                       allow_nan=False) + "\n")
    return path


def _walk_watched(base: str) -> list[str]:
    """Every file under ``base`` a fingerprint watches: no ``__pycache__``, no
    dot-directory or dot-file (the writers' ``.<name>.tmp``), no bytecode, no
    ``*.tmp``/``*.lock`` — each rewritten by running things, not by editing
    them."""
    found: list[str] = []
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = sorted(d for d in dirnames if d != "__pycache__" and not d.startswith("."))
        for name in sorted(filenames):
            if name.startswith(".") or name.endswith(_UNWATCHED_SUFFIXES):
                continue
            found.append(os.path.join(dirpath, name))
    return found


def watched_paths(root: str, resolution: Resolution | None = None) -> list[str]:
    """Every path ``fingerprint`` digests, absolute and sorted: the ``WATCHED``
    files (a single file is listed whether or not it exists — its appearing is
    a change), the files, listings and code files of every entry
    ``resolution`` used (a gate's ``cad/part.stl``, a pack's gate module), and
    each loaded pack's ``gates/`` and ``pack.json``."""
    base = os.path.abspath(root)
    found: set[str] = set()
    for pattern in WATCHED:
        if pattern.endswith("/**"):
            found.update(_walk_watched(os.path.join(base, *pattern[:-3].split("/"))))
        else:
            found.add(os.path.join(base, *pattern.split("/")))
    anchors = getattr(resolution, "anchors", None)
    if not isinstance(anchors, Anchors):
        anchors = anchors_for(base, None, out_dir=store.out_dir(base))
    places = _places_of(anchors)
    for row in (getattr(resolution, "rows", None) or {}).values():
        entry = getattr(row, "entry", None)
        if entry is None:
            continue
        reads = entry.reads or {}
        spelled = [*(reads.get("files") or {}), *(reads.get("dirs") or {}),
                   *((entry.code or {}).get("files") or ())]
        for name in spelled:
            where = _locate(name, base, places)
            if where:
                found.add(os.path.abspath(where))
    for _name, pack_dir in anchors.packs:
        found.update(_walk_watched(os.path.join(pack_dir, "gates")))
        found.add(os.path.join(pack_dir, "pack.json"))
    return sorted(found)


def fingerprint(root: str, paths: Iterable[str], *, digests: FileDigests | None = None) -> str:
    """sha256 over the spine digest and ``{path: digest}`` for ``paths`` —
    a file by its bytes (``None`` when missing), a directory by its listing —
    keyed relative to ``root``. Moves when anything watched moves; stays put
    when nothing does."""
    base = os.path.abspath(root)
    digests = digests if digests is not None else FileDigests()
    table: dict[str, str | None] = {}
    for path in sorted({os.path.abspath(p) for p in paths}):
        key = _shown(base, path)
        if os.path.isdir(path):
            table[key + "/"] = _dir_digest(path)
        else:
            table[key] = digests.digest(path)
    return _digest_of(_clean({"schema": SCHEMA, "spine": spine_digest(), "paths": table}))
