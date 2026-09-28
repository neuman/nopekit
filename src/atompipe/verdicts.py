# SPDX-License-Identifier: Apache-2.0
"""atompipe.verdicts — what a gate read, so that its verdict can be keyed by it.

A verdict is only as current as the inputs it consumed. Until Phase 1.2 the spine
had no idea what those were: staleness was ONE hash of the whole projection plus
one hash of every ingested input, so a comment edit in the model re-ran
everything and a limit file edited under a project gate re-ran nothing (S-22).
This module is the single home of the answer — the per-gate read set, and the
content address (rho) built from it. This file holds its first half, the
primitives:

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

The rest — ``code_digest``, rho, the entry files, freshness, admission — builds on
these (U16, U19, U20). None of it reads the wall clock (``test_meta``), takes the
build lock, or imports ``gates``: ``gates`` imports this module, so anything here
that needs a gate type receives the object.

Imports: ``models`` and ``util`` only, standard library otherwise, including every
function-local import (CI's AST walk).
"""
from __future__ import annotations

import ast
import copy
import dataclasses
import functools
import hashlib
import json
import math
import numbers
import os
import re
import sys
import tempfile
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping

from .models import Ledger
from .util import AtompipeError


__all__ = [
    "ABSENT", "PRESENT", "SPINE_MODULES", "SMALL_VALUE_MAX_CHARS",
    "digest_value", "small_value", "portable", "traced_context", "tracing",
    "spine_digest", "canonical_ast_digest",
    "Anchors", "ParamTrace", "LedgerView", "ModelProxy", "GateTrace",
    "GateInputWriteError",
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
})
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
    if frame.f_globals.get("__name__") in _EXCLUDED_MODULES:
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
