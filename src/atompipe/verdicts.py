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

Freshness, admission and the resolver build on these (U19, U20). None of it
reads the wall clock (``test_meta``) — a ``when`` arrives from the CLI edge —
takes the build lock, or imports ``gates``: ``gates`` imports this module, so
anything here that needs a gate type receives the object.

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
import sys
import tempfile
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Collection, Iterable, Mapping

from . import modelio, store, vcs
from .models import Ledger, Locator, Tier, Verdict
from .util import AtompipeError, FileDigests, atomic_write_json


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
    address = rho(gate_id, spine, code, read_set)
    entry = Entry(gate=gate_id, rho=address, code=code.to_dict(), spine=spine,
                  reads=read_set.to_dict(),
                  instruments=instruments_for(spec, code) if spec is not None else {},
                  verdict=_verdict_block(verdict, anchors))
    result = write_entry(root, entry)
    forget(root, gate_id)
    return result


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
            anchors: Anchors | None) -> tuple[str, dict, CodeRef, str]:
    anchors = anchors if anchors is not None else _default_anchors(root, spec, fn)
    code = code_digest(spec, fn, anchors=anchors)
    owner = _owner_dir(fn, root)
    files = selftest_walk(owner, digests=digests)
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
    anchors = anchors if anchors is not None else _default_anchors(root, spec, fn)
    digests = digests if digests is not None else FileDigests()
    static, parts, code, owner = _static(spec, fn, root, digests=digests, anchors=anchors)
    key = f"control:{spec.id}"
    measured = limit = None
    units = ""
    if bad is None:
        if result is None:
            raise AtompipeError(f"{spec.id}: record_control needs the selftest result "
                                f"or an explicit bad=")
        bad, kind = _control_outcome(spec, result)
        if bad is None:
            held = result
            if result.outcome == "fail":
                # A crash on the fixture comes back from gates.selftest as a
                # failed selftest with no `error` — the selftest's verdict on
                # the GATE. Remembered, it is what it is about the control: an
                # error, never something that could read as a measured fail.
                held = dataclasses.replace(
                    result, error=f"control {kind}: {(result.detail or '').splitlines()[0]}"
                    if result.detail else f"control {kind}")
            remember(root, key, held, input_rho=static, kind=kind, when=when)
            return None
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
    written = write_control(root, entry)
    forget(root, key)
    return written


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
