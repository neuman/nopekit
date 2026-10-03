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
* ``TierRead`` — ``ctx.tier`` as a gate sees it. ``GateContext`` lets a gate pick
  a cheaper path by the sweep's tier, and nothing recorded that one had: a PASS
  from the cheap path at tier 0 was served Fresh to ``check --tier 2`` (review
  round 1, ``probe.tier``).
* ``SweepMemo`` — ``ctx.memo`` as a gate sees it: a handle only
  ``GateContext.load_file`` opens. It was the sweep's raw dict, so a gate could
  cache a parsed table in it and the next gate's hit keyed no file — S-27, one
  field over from the ``extra`` it was closed for (review, ``ffr3/p2``).
* ``digest_value`` and ``Anchors`` — a digest of a value that is the same in
  every checkout. Fixtures set absolute mesh and ``.mo`` paths, and a raw digest
  of those would write a new tracked entry per clone and per run (packs:H6).
* ``tracing`` and the audit hook — the files a gate opened, the directories it
  listed, the processes it started. One process-global hook, routed to a stack
  of traces, with the interpreter's own noise excluded: the first mesh gate of
  a sweep opened 719 ``.pyc`` files and listed 77 directories on import alone
  (packs:H1), none of which it decided anything on. And the stat probes — the
  paths it asked the existence, kind or size of, which raise no audit event:
  bundled ``modelica.source_hygiene`` skipped a named ``.mo`` that was not there
  yet on an ``os.path.isfile``, recorded nothing, and kept a Fresh PASS after it
  appeared (review round 1). And the channels no ``open`` reports: a sqlite
  database (opened in C), a data file read through linecache or tokenize (whose
  events were dropped whole, as the traceback formatter's), and the process
  environment, which fires no event at all and is named opaque (``env:<NAME>``).
  All three kept a Fresh PASS after their input moved (review round 1,
  ``probe.sqlite``, ``probe.linecache``, ``probe.env``).
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
import linecache
import math
import numbers
import operator
import os
import re
import shutil
import sys
import tempfile
import threading
import urllib.parse
from dataclasses import dataclass, field
from typing import Any, Callable, ClassVar, Collection, Iterable, Mapping

from . import modelio, store, vcs
from .models import Ledger, Locator, Tier, Verdict
from .util import AtompipeError, FileDigests, atomic_write_json, atomic_write_text


__all__ = [
    "ABSENT", "PRESENT", "DIRECTORY", "SPINE_MODULES", "SMALL_VALUE_MAX_CHARS",
    "digest_value", "small_value", "portable", "traced_context", "tracing", "replay",
    "spine_digest", "canonical_ast_digest",
    "Anchors", "ParamTrace", "LedgerView", "ModelProxy", "TierRead", "GateTrace",
    "GateInputWriteError", "GateMemoError", "SweepMemo", "memo_entries", "sweep_memo",
    # part two: rho, entries, controls, remembered outcomes, obs (U16)
    "SCHEMA", "RHO_CHARS", "OUT_CHARS", "OBS_KEEP", "SPEC_FIELDS_IN_RHO",
    "CONTROL_OUT_DIR", "UNRECORDED_FIXTURE", "TWO_OUTCOMES_IS_ERROR",
    "CodeRef", "Reads", "Entry", "ControlEntry", "WriteResult",
    "anchors_for", "code_digest", "model_digest", "rho", "rho_control", "out8",
    "instruments_for", "write_entry", "read_entries", "record_verdict",
    "selftest_walk", "control_static", "write_control", "read_controls",
    "record_control", "remember", "remembered", "forget", "record_obs", "read_obs",
    "last_read_sets",
    # part three: freshness, admission state, the one resolver (U19)
    "MAX_STALE_REASONS", "Fresh", "Stale", "Unknown", "Never", "freshness",
    "Admission", "admission_state", "Row", "Resolution", "resolve", "apply_prerequisites",
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

#: The digest recorded for a file input that is a directory: what a gate that
#: asked ``os.path.isdir`` — or the root's kind, as every ``realpath`` of a
#: project path does — decided on. A file input's digest is otherwise the
#: sha256 of its bytes, or ``None`` when it is missing. What slipped through
#: without it: ``FileDigests`` answers ``None`` for a directory too, so a named
#: directory that appeared, or vanished, moved nothing — ``gather_mo_files``
#: walks a ``modelica_sources`` entry only when ``os.path.isdir`` says so.
#: *Rejected:* the directory's listing (every ``realpath`` of a project path
#: lstats the root, so every file added at the top of a project would stale
#: every gate that resolved a path); ``None`` (a directory reads as missing).
DIRECTORY = hashlib.sha256(b"atompipe:directory\x00").hexdigest()

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


class GateMemoError(AtompipeError, AttributeError):
    """``ctx.memo`` was used for something other than ``GateContext.load_file``.

    The memo is one per sweep and every gate's view holds it, so whatever one
    gate put in it was the next gate's input with no trace of either: the S-27
    channel ``ctx.extra`` was made per-gate for, one field over. What slipped
    through (review, ``ffr3/p2``): two gates shared a parsed table through
    ``ctx.memo["probe:materials"]``; the second keyed no file, so yield 50 -> 10
    re-ran the first alone, ``status`` named nothing stale, and ``check
    --force`` filed the second's FAIL at the inputs its Fresh PASS was served
    at. Every use but ``load_file``'s is refused (``SweepMemo``), and
    ``run_gate`` reports it like any other crash: an error, never a pass.

    Also an ``AttributeError``, because ``SweepMemo.__getattr__`` raises it:
    ``hasattr(ctx.memo, "get")`` then answers False and ``getattr(ctx.memo,
    "get", None)`` None, as they would for any object without the attribute —
    neither reaches an entry.
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
    holds a path this window wrote first; ``stats`` likewise, for the paths it
    asked the existence, kind or size of (``os.stat`` and everything built on
    it), each with whether it existed when first asked; ``host_reads`` is what
    a control's fixture — or anyone reading through its host view — read from
    the HOST context (the seal detector's input). ``anchors`` makes path-valued
    param digests portable; the sweep sets it. ``tier`` is the value this window
    read through ``ctx.tier`` (a ``TierRead``), ``None`` when it never did; a
    second, different value makes the trace opaque (``tier: read at … and …``),
    since no single tier then names what it decided on. ``sources`` is the
    module sources the STOCK import system read while the window was open and no
    load was being recorded (``_on_import_read``): an input only under the
    project or a pack, so they are kept apart from ``files_read`` — whose
    outside paths are opaque — and classified on their own rule.
    """

    kind: str = "gate"
    params: dict = field(default_factory=dict)
    values: dict = field(default_factory=dict)
    whole: set = field(default_factory=set)
    ledger: dict = field(default_factory=dict)
    files_read: list = field(default_factory=list)
    files_written: set = field(default_factory=set)
    dirs: set = field(default_factory=set)
    stats: list = field(default_factory=list)
    opaque: set = field(default_factory=set)
    model_used: bool = False
    host_reads: dict = field(default_factory=dict)
    fixture_code: Any = None
    anchors: Any = None
    tier: Any = None
    sources: list = field(default_factory=list)
    _read_set: set = field(default_factory=set, init=False, repr=False)
    _source_set: set = field(default_factory=set, init=False, repr=False)
    _existed: dict = field(default_factory=dict, init=False, repr=False)
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

    def _note_source(self, path: str) -> None:
        if path in self.files_written or path in self._source_set:
            return
        self._source_set.add(path)
        self.sources.append(path)

    def _note_stat(self, path: str, existed: bool | None) -> None:
        """A path asked about: ``existed`` is what the question found the first
        time (``None`` when the asker cannot say, a C predicate's bool)."""
        if path in self.files_written or path in self._existed:
            return
        self._existed[path] = existed
        self.stats.append(path)

    def stat_existed(self, path: str) -> bool | None:
        """Whether ``path`` existed when this window first asked about it."""
        return self._existed.get(path)

    def _note_write(self, path: str) -> None:
        self.files_written.add(path)

    def _note_tier(self, value: int) -> None:
        if self.tier is None:
            self.tier = value
        elif self.tier != value:
            self.opaque.add(f"tier: read at {self.tier} and {value}")


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
#: inside rho, a staleness that feeds itself. (The ledger's run record — the
#: previous sweep's hashes — was hidden here for the same reason until 1.2
#: removed it from the ledger altogether.)
_LEDGER_HIDDEN = frozenset({"verdicts"}) & _LEDGER_FIELD_SET

#: Filled in memory from somewhere other than the record: `Claim.gates` from
#: registry coverage, `physical_result` from `results/`, `Param.gates` from the
#: last read sets. A coverage change is not something a gate read.
_IN_MEMORY_FIELDS = {"claims": ("gates", "physical_result"), "params": ("gates",)}


#: Fields a gate's read of a record leaves out of the digest while they hold
#: their default: ``Claim.owner`` (P2.1), so a claim no file names an owner for
#: digests exactly as it did before the field existed, and only an edit that
#: names one moves the gates that read the claim.
_ABSENT_WHEN_EMPTY = frozenset({"owner"})


def _record_form(item: Any, strip: tuple = ()) -> Any:
    form = item.to_dict() if hasattr(item, "to_dict") else item
    if isinstance(form, dict):
        form = {k: v for k, v in form.items()
                if k not in strip and not (k in _ABSENT_WHEN_EMPTY and v == "")}
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
    gate that never looks at the ledger costs nothing. ``verdicts`` reads
    empty: a gate reading other gates' verdicts would put verdicts inside rho. ``claim(cid)`` records ``"claim:<cid>"`` with
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


def _proxied(value: Any) -> Any:
    """``value`` with every ``ModelProxy`` around it unwrapped WITHOUT using it
    — for the spine's own comparisons, as ``_plain_tier`` is for a tier.
    ``type(...) is``, never ``isinstance``: an ``isinstance`` miss asks the
    object for ``__class__``, which a proxy answers by using its model."""
    while type(value) is ModelProxy:
        value = object.__getattribute__(value, "_mp_target")
    return value


# --------------------------------------------------------------------------- #
# TierRead
# --------------------------------------------------------------------------- #
def _plain_tier(value: Any) -> Any:
    """``value`` with a ``TierRead`` unwrapped WITHOUT recording a read — for
    the spine's own comparisons, which are not the gate's. Anything else is
    returned as it is."""
    if type(value) is TierRead:
        return object.__getattribute__(value, "_tr_value")
    return value


def _note_tier(trace: Any, value: int) -> None:
    """``value`` was read as ``ctx.tier``: record it on ``trace`` and on every
    trace opened inside ``trace``'s window since — a ``load_file`` loader's (its
    hit replays it to the next gate) or a nested gate's. Never on the traces
    around it: a tier a gate hands a nested gate is the gate's own choice."""
    if trace is None:
        return
    targets = [trace]
    for index, open_trace in enumerate(_STACK):
        if open_trace is trace:
            targets.extend(t for t in _STACK[index + 1:] if t is not trace)
            break
    for target in targets:
        target._note_tier(value)


#: What ``ctx.tier.__class__`` reports: an ``int`` subclass that is never
#: instantiated, named for the view. ``isinstance(ctx.tier, int)`` holds through
#: it, and an error that names the type names this one: with plain ``int`` there,
#: ``json.dumps(ctx.tier)`` said "Object of type int is not JSON serializable",
#: which reads as a bug in json (found writing this). ``type(ctx.tier)`` is still
#: ``TierRead``.
_TIER_CLASS = type("TierRead", (int,), {"__module__": __name__,
                                        "__doc__": "What ctx.tier reports as its class."})


def _tier_binary(fn: Callable[[Any, Any], Any], *, reflected: bool = False) -> Callable:
    def method(self: "TierRead", other: Any) -> Any:
        value = TierRead._tr_use(self)
        if type(other) is TierRead:
            other = TierRead._tr_use(other)
        return fn(other, value) if reflected else fn(value, other)
    return method


def _tier_unary(fn: Callable[..., Any]) -> Callable:
    def method(self: "TierRead", *args: Any) -> Any:
        return fn(TierRead._tr_use(self), *args)
    return method


class TierRead:
    """``ctx.tier`` as a gate sees it: the sweep's tier, and every use of the
    value is a read.

    ``TierRead(value, trace)``. ``GateContext`` tells a gate it may use the tier
    "to pick a cheaper path", and ``sweep`` stamps the sweep's own — but no view
    recorded that a gate had looked, so rho never keyed it. A gate passing on
    its tier-0 path was served ``pass cached fresh`` to ``check --tier 2``,
    whose path never ran; ``--force`` there FAILed it (review round 1,
    ``probe.tier``). Now comparing, hashing, formatting, converting, indexing
    with it, arithmetic, truth, pickling and any ``int`` attribute record the
    value on ``trace`` (``GateTrace.tier``), and rho carries it (``Reads.tier``).
    Copying it (``copy``, ``deepcopy``, ``dataclasses.replace`` of the context)
    and passing it around are not reads; ``isinstance(t, int)`` is, and is True.

    Not an ``int`` subclass, on purpose. CPython serves an ``int`` subclass's
    value from its digits without calling a method wherever it wants an index —
    ``table[ctx.tier]``, ``range(ctx.tier)``, a slice — so the natural way to
    "pick a path" would have recorded nothing. Here every extraction goes
    through ``__index__``, ``__int__`` or an operator. The cost: C code that
    type-checks for ``int`` — ``json.dumps(ctx.tier)`` — raises (loudly: the gate
    errors, never passes); ``int(ctx.tier)`` is the plain value, and a read.
    *Rejected:* recording at attribute access on the context (``run_gate``'s
    ``dataclasses.replace`` and every fixture read every field, as for the
    model); keying rho on the tier for every gate (the six bracket gates never
    read it, and would re-run and re-write per tier); a static scan for
    ``.tier`` in the gate's closure (blind to ``getattr`` and to a helper
    outside it, and it keys a gate on a branch it did not take).
    """

    __slots__ = ("_tr_value", "_tr_trace")

    def __init__(self, value: Any, trace: Any) -> None:
        object.__setattr__(self, "_tr_value", int(_plain_tier(value)))
        object.__setattr__(self, "_tr_trace", trace)

    def _tr_use(self) -> int:
        value = object.__getattribute__(self, "_tr_value")
        _note_tier(object.__getattribute__(self, "_tr_trace"), value)
        return value

    @property                                      # type: ignore[misc]
    def __class__(self) -> type:                   # isinstance(ctx.tier, int): a read, True
        TierRead._tr_use(self)
        return _TIER_CLASS

    def __getattr__(self, name: str) -> Any:        # bit_length, real, to_bytes, ...
        if name.startswith("_tr_"):
            raise AttributeError(name)
        return getattr(TierRead._tr_use(self), name)

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError("ctx.tier is read-only in a gate's view")

    def __copy__(self) -> "TierRead":
        return self

    def __deepcopy__(self, memo: dict) -> "TierRead":
        return self

    def __reduce_ex__(self, protocol: Any) -> tuple:
        return (int, (TierRead._tr_use(self),))

    def __reduce__(self) -> tuple:
        return self.__reduce_ex__(2)

    __lt__ = _tier_binary(operator.lt)
    __le__ = _tier_binary(operator.le)
    __eq__ = _tier_binary(operator.eq)             # type: ignore[assignment]
    __ne__ = _tier_binary(operator.ne)             # type: ignore[assignment]
    __gt__ = _tier_binary(operator.gt)
    __ge__ = _tier_binary(operator.ge)
    __add__ = _tier_binary(operator.add)
    __radd__ = _tier_binary(operator.add, reflected=True)
    __sub__ = _tier_binary(operator.sub)
    __rsub__ = _tier_binary(operator.sub, reflected=True)
    __mul__ = _tier_binary(operator.mul)
    __rmul__ = _tier_binary(operator.mul, reflected=True)
    __truediv__ = _tier_binary(operator.truediv)
    __rtruediv__ = _tier_binary(operator.truediv, reflected=True)
    __floordiv__ = _tier_binary(operator.floordiv)
    __rfloordiv__ = _tier_binary(operator.floordiv, reflected=True)
    __mod__ = _tier_binary(operator.mod)
    __rmod__ = _tier_binary(operator.mod, reflected=True)
    __divmod__ = _tier_binary(divmod)
    __rdivmod__ = _tier_binary(divmod, reflected=True)
    __pow__ = _tier_binary(operator.pow)
    __rpow__ = _tier_binary(operator.pow, reflected=True)
    __lshift__ = _tier_binary(operator.lshift)
    __rlshift__ = _tier_binary(operator.lshift, reflected=True)
    __rshift__ = _tier_binary(operator.rshift)
    __rrshift__ = _tier_binary(operator.rshift, reflected=True)
    __and__ = _tier_binary(operator.and_)
    __rand__ = _tier_binary(operator.and_, reflected=True)
    __or__ = _tier_binary(operator.or_)
    __ror__ = _tier_binary(operator.or_, reflected=True)
    __xor__ = _tier_binary(operator.xor)
    __rxor__ = _tier_binary(operator.xor, reflected=True)
    __neg__ = _tier_unary(operator.neg)
    __pos__ = _tier_unary(operator.pos)
    __abs__ = _tier_unary(abs)
    __invert__ = _tier_unary(operator.invert)
    __bool__ = _tier_unary(bool)
    __int__ = _tier_unary(int)
    __index__ = _tier_unary(int)
    __float__ = _tier_unary(float)
    __complex__ = _tier_unary(complex)
    __hash__ = _tier_unary(hash)                   # type: ignore[assignment]
    __round__ = _tier_unary(round)
    __trunc__ = _tier_unary(math.trunc)
    __floor__ = _tier_unary(math.floor)
    __ceil__ = _tier_unary(math.ceil)
    __str__ = _tier_unary(str)
    __repr__ = _tier_unary(repr)
    __format__ = _tier_unary(format)


# --------------------------------------------------------------------------- #
# the sweep's file memo, as a gate holds it
# --------------------------------------------------------------------------- #
#: What every refused use of ``ctx.memo`` says: what the memo is, and the one way
#: to share a file between gates that keeps it an input of each.
_MEMO_REFUSED = ("ctx.memo is the sweep's file memo, and only ctx.load_file may use it: "
                 "share a file between gates with ctx.load_file(path, loader), which "
                 "records it as a read of every gate that asks — a value one gate "
                 "leaves in the memo is the next gate's input with no trace of either")


def _refuse_memo(*_args: Any, **_kw: Any) -> Any:
    raise GateMemoError(_MEMO_REFUSED)


class SweepMemo:
    """``ctx.memo`` as every context holds it: a handle to the sweep's file memo
    that only ``GateContext.load_file`` opens (``memo_entries``).

    ``load_file`` keeps each entry's read set and replays it on every hit, so a
    file several gates share is an input of each. Anything else a gate did with
    the dict itself was a channel between two gates that no trace records — the
    review's ``ffr3/p2`` repro (``GateMemoError``) — so every mapping use raises
    ``GateMemoError``, naming ``load_file``: ``get``, ``[k]``, ``[k] = v``,
    ``in``, ``len``, iteration, ``{**m}``, ``dict(m)``, ``m | {}``, and any
    other attribute. It answers two things, neither of which is an entry:
    ``is None`` (a context no sweep made has no memo, and fdm-print asks), and
    truth, always True. Copying a context shares its memo (``copy`` and
    ``deepcopy`` return it, as ``dataclasses.replace`` does); pickling one — a
    gate shipping its context to a worker process — hands the worker an EMPTY
    memo of its own, so the entries never leave the sweep (and a worker's own
    reads are the gate's opaque channel already).

    Not a ``dict`` and not a mapping, on purpose: every protocol a dict answers
    would have to be refused one by one, and ``ParamTrace`` shows how many there
    are (S-25) — an object with none of them refuses the one nobody listed too.
    *Rejected:* recording an opaque channel on each direct use instead of
    refusing it. The gate would run, never be Fresh, and still hand the next
    gate a value it computed — a cost paid on every check for a channel with no
    use ``load_file`` does not already serve; and the plain ``dict`` the view
    held until now is the defect itself. Named residual: ``SweepMemo._entries``,
    the private slot, is reachable by a gate that goes looking for it — that is
    no longer plausible code.
    """

    __slots__ = ("_entries",)

    def __init__(self, entries: dict | None = None) -> None:
        object.__setattr__(self, "_entries", {} if entries is None else entries)

    def __getattr__(self, name: str) -> Any:
        if name.startswith("__") and name.endswith("__"):
            raise AttributeError(name)      # a protocol probe: the plain answer
        raise GateMemoError(f"ctx.memo.{name}: {_MEMO_REFUSED}")

    def __setattr__(self, name: str, value: Any) -> None:
        raise GateMemoError(_MEMO_REFUSED)

    __delattr__ = __getitem__ = __setitem__ = __delitem__ = _refuse_memo
    __contains__ = __iter__ = __reversed__ = __len__ = _refuse_memo
    __or__ = __ror__ = __ior__ = _refuse_memo

    def __bool__(self) -> bool:
        return True

    def __copy__(self) -> "SweepMemo":
        return self

    def __deepcopy__(self, memo: dict) -> "SweepMemo":
        return self

    def __reduce_ex__(self, protocol: Any) -> tuple:
        return (SweepMemo, ())

    def __reduce__(self) -> tuple:
        return self.__reduce_ex__(2)

    def __repr__(self) -> str:
        return "SweepMemo(<ctx.load_file's>)"


def memo_entries(memo: Any) -> dict | None:
    """The dict behind ``memo``: a ``SweepMemo``'s entries, a plain dict itself (a
    context a caller built by hand, as the tests do), else ``None`` — no memo,
    and ``load_file`` simply loads."""
    if type(memo) is SweepMemo:
        return object.__getattribute__(memo, "_entries")
    return memo if isinstance(memo, dict) else None


def sweep_memo(memo: Any = None) -> SweepMemo:
    """``memo`` as the handle a sweep hands its gates: a ``SweepMemo`` as it is, a
    plain dict wrapped with its entries shared (a caller that brought one sees
    what the sweep loaded into it), anything else a new, empty one."""
    if type(memo) is SweepMemo:
        return memo
    return SweepMemo(memo if isinstance(memo, dict) else None)


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
    ``memo`` is shared — one per sweep is the point of it — but as a
    ``SweepMemo``, the handle only ``load_file`` opens: a plain dict there (a
    context a caller or a fixture built by hand) is wrapped, entries shared, and
    a ``SweepMemo`` is kept as it is, so every view of one sweep holds the same
    one. What slipped through while it was the dict itself (review,
    ``ffr3/p2``): a gate cached a parsed table in ``ctx.memo`` directly, and
    the next gate's hit keyed no file — S-27, which the ``extra`` copy closed
    for ``extra`` only.

    ``tier``: a GATE's view wraps an integer tier in a ``TierRead`` on ``trace``.
    A ``TierRead`` already there is kept as it is — the host's tier passed
    through a fixture, or a gate's own view handed to a nested gate — so its
    reads land where the value came from. A CONTROL trace's views wrap nothing:
    the sweep's tier reaches a control already wrapped (``_control_host``), and
    a plain integer there is one the fixture chose — a constant of the fixture,
    not an input of the control. Keying it would miss the control entry at
    every other sweep tier and re-run it on every check.

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
    memo = getattr(ctx, "memo", None)
    if isinstance(memo, dict):
        changes["memo"] = SweepMemo(memo)
    ledger = getattr(ctx, "ledger", None)
    changes["ledger"] = None if ledger is None else LedgerView(ledger, trace)
    model = getattr(ctx, "model", None)
    changes["model"] = None if model is None else ModelProxy(model, trace)
    tier = getattr(ctx, "tier", None)
    if (type(tier) is not TierRead and getattr(trace, "kind", "gate") != "control"
            and isinstance(tier, int) and not isinstance(tier, bool)):
        changes["tier"] = TierRead(tier, trace)
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

#: Modules whose OWN file activity is never a gate input, whatever it touches:
#: zipimport (an archive on ``sys.path`` is code) and importlib.metadata (below).
_EXCLUDED_MODULES = frozenset({"zipimport", "importlib.metadata"})

#: Modules that read a module's SOURCE on the program's behalf: import machinery
#: (the ``.pyc`` and source opens, the ``.pyc`` writes and the ``sys.path``
#: listings of every import — ``import bracket`` lists ``model/``, whose listing
#: changes once ``__pycache__`` appears), and linecache, tokenize, warnings and
#: traceback, which read source files to quote a line in a message (the first
#: warning only, which makes it order-dependent too). Everything they do is
#: dropped EXCEPT a read, or a stat, of a path that is not a module's source
#: (``_module_source``). What slipped through (review round 1, ``probe.
#: linecache``): these modules were excluded whole, so a gate that read its
#: limit with ``linecache.getline`` — or ``tokenize.open``, or
#: ``pkgutil.get_data``, which reads through ``FileLoader.get_data`` — recorded
#: ``files={}``, and a plain check served its PASS after the file went to 0.
#: *Rejected:* matching the path against ``sys.modules``' ``__file__``s (a
#: helper loaded by path with ``spec_from_file_location``, fdm-print's way, is
#: in no ``sys.modules``, and ``exec`` of a compiled file leaves no module at
#: all — both would have their tracebacks recorded as reads).
_SOURCE_READERS = frozenset({
    "importlib._bootstrap", "importlib._bootstrap_external",
    "_frozen_importlib", "_frozen_importlib_external",
    "linecache", "tokenize", "warnings", "traceback",
})

#: The import system proper among ``_SOURCE_READERS``: the modules that LOAD a
#: module's source, as against quoting a line of it. A read of a source by
#: these, while a window is open and no load is being recorded
#: (``modelio.recording``), is the stock import system loading code for the
#: gate that is running — ``_on_import_read``. The formatters stay out: a
#: warning reads its source on the first warning only, which would key a
#: gate on what warned before it.
_IMPORT_SYSTEM = frozenset({
    "importlib._bootstrap", "importlib._bootstrap_external",
    "_frozen_importlib", "_frozen_importlib_external",
})

#: What a module's source is spelled as: the suffixes ``SourceFileLoader``
#: compiles (``.pyw`` is Windows'; listed everywhere so an entry reads the same
#: on every machine). Bytecode is ``_library_path``'s. Named residual: a data
#: file named ``*.py`` read through linecache is taken for source and dropped.
_SOURCE_SUFFIXES = (".py", ".pyw")

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
_EXCLUDED_CODE = frozenset({"<frozen zipimport>"})
_SOURCE_READER_CODE = frozenset({
    "<frozen importlib._bootstrap>", "<frozen importlib._bootstrap_external>",
})

#: ``_frame_kind``'s answers: a frame whose file activity is dropped whole, and
#: one that is a source reader.
_EXCLUDED = "excluded"
_SOURCE = "source"

#: Arguments beyond this many in one ``argv`` are not checked for files: a
#: process started with ten thousand arguments is opaque already.
_ARGV_FILES_MAX = 256


class _Window:
    """``with tracing(trace):`` — push on enter, pop (by identity) on exit.

    The pop runs before any ``except`` clause of the caller, so a traceback the
    caller formats after the block is never inside the window (packs:H2). The
    push forgets linecache's data lines first (``_forget_data_lines``), so a
    file read through it is opened, and seen, inside this window.
    """

    __slots__ = ("trace",)

    def __init__(self, trace: GateTrace) -> None:
        self.trace = trace

    def __enter__(self) -> GateTrace:
        _install_hook()
        _forget_data_lines()
        _STACK.append(self.trace)
        return self.trace

    def __exit__(self, *exc: Any) -> bool:
        for i in range(len(_STACK) - 1, -1, -1):
            if _STACK[i] is self.trace:
                del _STACK[i]
                break
        return False


def _module_source(path: str) -> bool:
    """Whether ``path`` is spelled as a module's source (``_SOURCE_SUFFIXES``)."""
    return os.path.normcase(path).endswith(_SOURCE_SUFFIXES)


def _forget_data_lines() -> None:
    """Drop every line linecache holds for a file that is not a module's source.

    linecache is a memo: ``getline`` serves a file it has read once from
    ``linecache.cache`` with no open and no stat. What slipped through with the
    source-reader rule alone: the admission control runs its gate first, in the
    same process, on the same root, so the gate's real run was a hit that opened
    nothing and keyed no file — the ``lru_cache`` hole of review round 2
    (``modelio.clear_caches``), in the standard library. Sources, and the lazy
    and pseudo-named entries (``<doctest ...>``, code registered by a tool) that
    cannot be read again, are kept: a traceback quotes those, and no gate
    decides on them. *Rejected:* ``linecache.clearcache()`` (it drops exactly
    those, and a traceback formatted later has nothing to quote).
    """
    cache = getattr(linecache, "cache", None)
    if not isinstance(cache, dict):
        return
    for name in list(cache):
        if (isinstance(name, str) and not (name.startswith("<") and name.endswith(">"))
                and not _module_source(name)):
            cache.pop(name, None)


def tracing(trace: GateTrace) -> _Window:
    """Route every file, directory, process and network event — and every
    existence, kind or size question (``_stat_probe``), every sqlite database
    opened and every environment variable read — to ``trace`` while the block
    runs (and to every enclosing trace as well).

    The hook is installed on the first push, at most once per process, and does
    nothing at all while no window is open.
    """
    return _Window(trace)


def replay(recorded: GateTrace, trace: GateTrace | None = None) -> None:
    """Record again what the hook and the stat probes routed to ``recorded``:
    its reads, module sources, stats, writes, listed directories and opaque
    channels — into
    every trace open now, and into ``trace`` (the calling view's own) even when
    no window is open around it.

    For work done once and consumed many times: ``GateContext.load_file`` runs a
    loader under a trace of its own on a miss and replays that trace on every
    hit, so each caller records every file the loader opened, not only the one
    it was handed. What slipped through before this existed: the hit reported
    the named file alone, so a ``.gltf``'s ``.bin`` buffers were inputs of the
    gate that missed and of no gate that hit — bundled ``fdm.bridge_span`` kept a
    Fresh PASS after its buffers moved, and failed at 54 mm against a 30 mm limit
    when forced (review round 1).

    Reads and stats go first and writes after, which rebuilds ``recorded``'s
    own view of each path: a path it read and then wrote is ``self_modified``
    here too, and one it wrote and then read is its output, never a read. The hook's filters
    (library paths, import machinery) already ran when ``recorded`` was filled.
    Params, the ledger and the model are the views' channels, not the hook's,
    and are not replayed. The tier is replayed: a loader closing over its
    gate's view that reads ``ctx.tier`` records it on the loader's own trace
    (``_note_tier`` routes a read to the traces opened inside its view's), so
    the value it loaded is keyed on the tier for every gate the entry serves.
    *Rejected:* re-raising the ``open`` events through
    ``sys.audit`` as ``_report_read`` does for one path (a listing and a child
    process have no event a replay could raise without running them).
    """
    targets: list = []
    for target in (*_STACK, trace):
        if target is None or target is recorded or any(t is target for t in targets):
            continue
        targets.append(target)
    for target in targets:
        for path in recorded.files_read:
            target._note_read(path)
        for path in recorded.sources:
            target._note_source(path)
        for path in recorded.stats:
            target._note_stat(path, recorded.stat_existed(path))
        for path in sorted(recorded.files_written):
            target._note_write(path)
        target.dirs.update(recorded.dirs)
        target.opaque.update(recorded.opaque)
        if recorded.tier is not None:
            target._note_tier(recorded.tier)


def _install_hook() -> None:
    global _HOOK_INSTALLS
    if _HOOK_INSTALLS:
        return
    with _HOOK_LOCK:
        if _HOOK_INSTALLS:
            return
        _library_roots()             # computed here, outside any window
        _install_env_recorder()
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


def _on_source_read(traces: tuple, args: tuple) -> None:
    """An ``open`` by a source reader (``_SOURCE_READERS``): recorded only when
    it reads, writes nothing, and names a file that is not a module's source —
    a gate's data read through ``linecache.getline``, ``tokenize.open`` or
    ``pkgutil.get_data``. The ``.pyc`` an import writes, and the sources it and
    the formatters read, stay the import system's and the formatter's."""
    path = _path_arg(args[0] if args else None)
    if path is None or _module_source(path):
        return
    mode = args[1] if len(args) > 1 else None
    flags = args[2] if len(args) > 2 else 0
    reads, writes = _open_intent(mode, flags)
    if reads and not writes:
        _on_open(traces, args)


def _on_import_read(traces: tuple, args: tuple) -> None:
    """An ``open`` by the import system (``_IMPORT_SYSTEM``) while no load is
    being recorded: the STOCK import system loading a module for the code that
    is running — ``importlib.import_module(name)``, a lazy ``import`` in a gate
    body, ``spec_from_file_location`` by hand. Its source is noted
    (``GateTrace.sources``) when the open reads and writes nothing; so is the
    source of a ``__pycache__`` file it reads, because a valid bytecode file is
    read INSTEAD of the source, and the stock import asks for it first whether
    it exists or not — keyed by the pyc it would be missed exactly when a pyc
    was left behind. ``Reads.from_trace`` keys a source under the project or a
    pack and drops the rest.

    What slipped through (admission review, round 2, c): a module's source read
    by the import system was dropped whole, as the closure's business — and
    outside a recorded load no closure records it, so a gate's
    ``importlib.import_module(name)`` of a local helper keyed nothing of it.
    Named residual: the module is loaded once per process, so only the run that
    loaded it keys it; a later one is served it from ``sys.modules`` and opens
    nothing. A literal name is in the gate's code closure instead
    (``modelio``'s static walk), and ``doctor``'s ``dynamic-imports`` row names
    every name a value decides. *Rejected:* recording it in ``files_read`` —
    a module from anywhere else on ``sys.path`` (a test helper, a checkout of a
    library) would be an opaque ``file-outside-project`` channel, and the gate
    never Fresh, for code that is no input of this project."""
    path = _path_arg(args[0] if args else None)
    if path is None:
        return
    mode = args[1] if len(args) > 1 else None
    flags = args[2] if len(args) > 2 else 0
    reads, writes = _open_intent(mode, flags)
    if not reads or writes:
        return
    if os.path.normcase(path).endswith(_BYTECODE):
        try:
            path = importlib.util.source_from_cache(path)
        except (ValueError, NotImplementedError):
            return                          # sourceless, or no cache tag: not a source
    elif not _module_source(path):
        return                              # data through get_data: _on_source_read's
    if _library_path(path):
        return
    for trace in traces:
        trace._note_source(path)


def _sqlite_target(database: str) -> tuple[str | None, bool]:
    """``(file, writable)`` for ``sqlite3.connect(database)``: the file the
    connection opens — ``None`` for a private in-memory or temporary database,
    which nothing outside the gate can have written — and whether it may write
    it. A ``file:`` name is read as a URI (SQLite's own rule once ``uri=True``,
    which the audit event does not carry): its path percent-decoded, a
    ``localhost`` authority allowed, ``mode=ro`` or ``immutable=1`` read-only,
    ``mode=memory`` no file at all."""
    if database in ("", ":memory:"):
        return None, False
    if not database.startswith("file:"):
        return database, True
    rest = database[len("file:"):].split("#", 1)[0]
    rest, _, query = rest.partition("?")
    if rest.startswith("//"):
        authority, slash, tail = rest[2:].partition("/")
        if authority.lower() not in ("", "localhost"):
            return None, False                  # SQLite refuses it: nothing is opened
        rest = slash + tail
    options = dict(urllib.parse.parse_qsl(query, keep_blank_values=True))
    if options.get("mode") == "memory" or rest in ("", ":memory:"):
        return None, False
    readonly = (options.get("mode") == "ro"
                or options.get("immutable", "").lower() in ("1", "yes", "true", "on"))
    return urllib.parse.unquote(rest), not readonly


def _on_sqlite(traces: tuple, args: tuple) -> None:
    """``sqlite3.connect``: a read of the database file and of its ``-wal``.

    SQLite opens its files in C, so no ``open`` event ever named them. What
    slipped through (review round 1, ``probe.sqlite``): a gate that took its
    limit from a table recorded ``files={} opaque=[]``, and a plain check served
    its PASS after the row went to 0. The ``-wal`` is part of the database a
    reader sees — in WAL mode committed rows wait there until a checkpoint moves
    them into the main file — so it is read too, missing or not. A connection
    that may write is named opaque, ``sqlite-writable:<path>``: its writes are
    C-level as well, and nothing can tell whether the gate wrote what it read
    (a result memoised in the database it reads is the S-27 shape). Open it
    ``file:<path>?mode=ro`` with ``uri=True`` and the entry can be Fresh.
    *Rejected:* a read alone for every connection (a gate that updates the row
    it read keys the updated bytes, and is Fresh on an input it never saw).
    Named residual: a database ``ATTACH``-ed from SQL, and any file a C
    extension opens itself — no event names either.
    """
    text = _text(args[0] if args else None)
    if text is None:
        return
    target, writable = _sqlite_target(text)
    path = _path_arg(target) if target is not None else None
    if path is None or _library_path(path):
        return
    for trace in traces:
        trace._note_read(path)
        trace._note_read(path + "-wal")
        if writable:
            trace.opaque.add(f"sqlite-writable:{path}")


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


def _on_fork_exec(traces: tuple, args: tuple) -> None:
    """``_posixsubprocess.fork_exec(argv, executable_list, close_fds, pass_fds,
    cwd, ...)`` — five leading arguments whose order every CPython since 3.8 has
    kept (what follows them has changed). Raised by
    ``_process_probe``, never by the interpreter (see ``_install_process_probe``)."""
    padded = tuple(args) + (None,) * 5
    candidates = padded[1]              # the PATH candidates, bytes; all share a basename
    executable = (candidates[0] if isinstance(candidates, (list, tuple)) and candidates
                  else None)
    _process(traces, executable, padded[0], padded[4])


def _command_words(command: Any) -> list[str]:
    """A Windows command line as its words, ``CommandLineToArgvW``'s quoting near
    enough: a double-quoted run is one word, its quotes dropped. Only names a
    channel and finds argv files — the entry is opaque whatever it finds."""
    text = _text(command)
    if text is None:
        return []
    return [quoted or bare for quoted, bare in re.findall(r'"([^"]*)"?|(\S+)', text)
            if quoted or bare]


def _on_create_process(traces: tuple, args: tuple) -> None:
    """``_winapi.CreateProcess``: ``(application_name, command_line,
    current_directory)``. Every process Windows starts goes through it, and
    multiprocessing's spawn — the only start method there — calls it directly,
    with no ``subprocess.Popen`` event before it (review round 3, ``probe.mp``)."""
    padded = tuple(args) + (None,) * 3
    _process(traces, padded[0], _command_words(padded[1]), padded[2])


#: The module that asks a running forkserver for a child, and the modules its
#: request passes through on the way to the socket: this one (the hook's own
#: frames) and ``multiprocessing.reduction`` (``sendfds``, the ``sendmsg`` that
#: hands the child its fds).
_FORKSERVER = "multiprocessing.forkserver"
_FORKSERVER_VIA = frozenset({__name__, "multiprocessing.reduction"})


def _forkserver_request() -> bool:
    """Whether the socket event being handled is a request to a forkserver: the
    first frame outside ``_FORKSERVER_VIA`` is ``multiprocessing.forkserver``'s."""
    frame = sys._getframe(1)
    while frame is not None and frame.f_globals.get("__name__") in _FORKSERVER_VIA:
        frame = frame.f_back
    return frame is not None and frame.f_globals.get("__name__") == _FORKSERVER


def _on_network(traces: tuple, args: tuple) -> None:
    """``network`` — or, for a request to a forkserver, ``subprocess:forkserver``.

    A forkserver (the default start method on Linux from 3.14) forks each child
    from a server process started once; a later gate's child is asked for over a
    unix socket, and that request is the only event this process raises for it.
    What slipped through (review round 3, ``probe.mp``): such a child was opaque
    only by accident, named ``network`` — a gate that reads nothing remote was
    reported as reading the network, and a fix that stopped treating local
    sockets as network would have made it invisible. *Rejected:* naming every
    ``AF_UNIX`` socket a subprocess (a unix socket to a database server is not
    one), and the server's ``_forkserver_address`` (private, and per version)."""
    channel = "subprocess:forkserver" if _forkserver_request() else "network"
    for trace in traces:
        trace.opaque.add(channel)


#: Event -> handler. ``os.replace`` audits as ``os.rename`` on CPython; both
#: names are here in case another implementation does not. ``os.fork`` and the
#: datagram sends go beyond the plan's table: a forked child's reads are as
#: invisible as a spawned one's, and a UDP send needs no ``connect``.
#: ``_winapi.CreateProcess`` and ``_posixsubprocess.fork_exec`` are how
#: multiprocessing's spawn and forkserver methods start an interpreter; what
#: slipped through without them is ``_install_process_probe``'s story.
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
    "_winapi.CreateProcess": _on_create_process,
    "_posixsubprocess.fork_exec": _on_fork_exec,
    "socket.connect": _on_network,
    "socket.sendto": _on_network,
    "socket.sendmsg": _on_network,
    "sqlite3.connect": _on_sqlite,
}


def _frame_kind(frame: Any) -> str:
    """``_EXCLUDED``, ``_SOURCE`` (a source reader's frame) or ``""``."""
    if frame is None:
        return ""
    name = frame.f_globals.get("__name__")
    code = frame.f_code.co_filename
    if (name in _EXCLUDED_MODULES or code in _EXCLUDED_CODE
            or isinstance(name, str) and name.startswith(_EXCLUDED_PREFIXES)):
        return _EXCLUDED
    if name in _SOURCE_READERS or code in _SOURCE_READER_CODE:
        return _SOURCE
    return ""


def _import_frame(frame: Any) -> bool:
    """Is ``frame`` the import system's own (``_IMPORT_SYSTEM``)?"""
    return frame is not None and (frame.f_globals.get("__name__") in _IMPORT_SYSTEM
                                  or frame.f_code.co_filename in _SOURCE_READER_CODE)


def _audit(event: str, args: tuple) -> None:
    """The one audit hook. Never raises, never opens a file, and returns at once
    while no window is open, for an event it does not handle, and while the
    loader works for itself (``modelio.bookkeeping``: a digest it takes, a
    check that a closure still holds — nobody's input)."""
    if not _STACK:
        return
    handler = _HANDLERS.get(event)
    if handler is None or getattr(_BUSY, "on", False) or modelio.bookkeeping():
        return
    _BUSY.on = True
    try:
        try:
            frame = sys._getframe(1)
        except ValueError:
            frame = None
        kind = _frame_kind(frame)
        traces = tuple(_STACK)
        call = tuple(args) if isinstance(args, tuple) else ()
        if kind == _SOURCE:
            if event == "open" and traces and not modelio.recording() \
                    and _import_frame(frame):
                _on_import_read(traces, call)
            handler = _on_source_read if event == "open" else None
        if kind != _EXCLUDED and handler is not None and traces:
            handler(traces, call)
    except Exception:
        pass
    finally:
        _BUSY.on = False


def _report_closure(closure: Any) -> None:
    """``modelio``'s ``on_unrecorded_load`` listener: a module was run or served
    with no load being recorded — at RUN time, by a gate, a fixture's ``make``
    or ``known_good.context`` — so every file of its closure, its code and what
    that code read at its import, is a read of every window open now. A file a
    closure holds two versions of (digest ``""``) names no bytes, and the
    windows are opaque rather than keyed on whichever is on disk.

    What slipped through without it (admission review, round 2): such a module
    joined no closure, its source read was dropped as a module's source, and
    outside ``selftest/`` no static walk covered it. A ``known_good.context``
    that loaded the live model by path made the known-good design the live one
    with no key (S-07 again: an identity fixture admitted, C1 PROVEN, while
    ``gate selftest`` said PASSED its own known-bad); a fixture helper defused
    400 -> 40 mm stayed admitted; a gate's limit loaded by path kept its PASS
    Fresh after it moved. Served from the cache, the module is reported the
    same, so the second gate to ask keys it as the first did — no rho depends
    on which gate loaded it first. Named residual: the ``atompipe.*`` modules
    outside the spine digest that such a helper imports (``spine_extras``) —
    keyed for a module's own closure by ``code_digest``, not here."""
    if not _STACK or getattr(_BUSY, "on", False):
        return
    traces = tuple(_STACK)
    for path, digest in tuple(closure.files) + tuple(closure.data):
        for trace in traces:
            if digest:
                trace._note_read(path)
            else:
                trace.opaque.add(f"code loaded at run time: two versions of {path} ran")


modelio.on_unrecorded_load(_report_closure)


# --------------------------------------------------------------------------- #
# the stat probes
# --------------------------------------------------------------------------- #
# ``os.stat`` raises no audit event, and neither does anything built on it:
# ``os.path.exists``/``isfile``/``isdir``/``getsize``/``getmtime``/``samefile``
# (genericpath), ``lexists``/``islink``/``ismount``/``realpath`` (posixpath),
# ``pathlib``'s ``stat``/``exists``/``is_file``/``is_dir``, and ``glob`` of a
# literal path. So a gate that decided on a file's existence, kind or size
# recorded nothing. What slipped through (review round 1): ``gather_mo_files``
# skips a ``modelica_sources`` entry that ``os.path.isfile`` says is not there,
# so bundled ``modelica.source_hygiene`` recorded ``{model/A.mo}``, kept a Fresh
# PASS after ``model/B.mo`` appeared, and failed ``1/2 parameter(s)
# undefendable`` when forced; a project gate passing on ``os.path.isfile(
# "inputs/cert.pdf")`` stayed Fresh after the file was deleted. The hook cannot
# see these, so the functions themselves are replaced, once, by probes that
# record and then call the original.

#: The ``os`` functions the probes replace. Every question listed above looks
#: one of these up ON ``os`` at call time, so replacing the attribute reaches
#: them all; import machinery binds ``posix.stat`` itself and is not affected.
#: *Rejected:* wrapping each ``os.path`` function (dozens of names across
#: ``genericpath``, ``posixpath``, ``pathlib`` and ``glob``, and every one still
#: ends here); a ``sys.setprofile`` on C calls (it sees every call in the
#: process and is per-thread, so a gate's worker threads would escape it).
_STAT_FUNCTIONS = ("stat", "lstat")

#: ``os.path`` predicates that are C on some platforms — ``nt._path_isdir`` and
#: its siblings on Windows since 3.12 — and never reach ``os.stat``. Probed only
#: where they are builtins, so POSIX records each question once.
_STAT_PREDICATES = ("exists", "lexists", "isfile", "isdir", "islink", "isjunction",
                    "isdevdrive")

#: Modules a stat passes THROUGH on its way from the question to ``os.stat``.
#: The frame that decides whether the question was import machinery's or
#: linecache's (``_frame_kind``) is the first one outside these: linecache
#: calls ``os.stat`` itself, importlib.metadata asks through ``pathlib``.
_STAT_VIA = frozenset({"os", "genericpath", "posixpath", "ntpath", "pathlib",
                       "pathlib._local", "pathlib._abc", "glob"})

def _on_stat(args: tuple, kwargs: dict, existed: bool | None) -> None:
    """Record one question on every open trace. Called with ``_BUSY`` set, so
    nothing it does — ``abspath``, the frame walk — records itself."""
    raw = args[0] if args else kwargs.get("path")
    text = _text(raw)
    if text is None or kwargs.get("dir_fd") is not None and not os.path.isabs(text):
        return            # an fd, or a path relative to a dir_fd: named, as for os.open
    path = _path_arg(text)
    if path is None or _library_path(path):
        return
    frame = sys._getframe(2)                    # past this function and the probe
    while frame is not None and frame.f_globals.get("__name__") in _STAT_VIA:
        frame = frame.f_back
    kind = _frame_kind(frame)
    if kind == _EXCLUDED or kind == _SOURCE and _module_source(path):
        return
    for trace in tuple(_STACK):
        trace._note_stat(path, existed)


def _stat_probe(original: Callable[..., Any], *, predicate: bool) -> Callable[..., Any]:
    """``original``, recording its path on every open trace first — and, for
    ``os.stat``/``os.lstat``, whether it existed. Outside a window, or inside
    the hook's own work, it is one attribute check and the original call."""

    @functools.wraps(original)
    def probe(*args: Any, **kwargs: Any) -> Any:
        if not _STACK or getattr(_BUSY, "on", False) or modelio.bookkeeping():
            return original(*args, **kwargs)
        existed: bool | None = None
        try:
            result = original(*args, **kwargs)
            existed = None if predicate else True
            return result
        except OSError:
            existed = False
            raise
        finally:
            _BUSY.on = True
            try:
                _on_stat(args, kwargs, existed)
            except Exception:
                pass
            finally:
                _BUSY.on = False

    probe.__atompipe_probe__ = True             # type: ignore[attr-defined]
    return probe


#: The capability sets that name ``os`` functions by identity. What would slip
#: through without this: ``os.stat in os.supports_dir_fd`` turns False once
#: ``os.stat`` is a probe, and a library that asks (``shutil``'s fd-based
#: ``rmtree`` does, at its own import) quietly takes its slower, racier path.
_SUPPORTS = ("supports_dir_fd", "supports_fd", "supports_follow_symlinks",
             "supports_effective_ids")


def _install_stat_probes() -> None:
    """Replace ``os.stat``, ``os.lstat`` and any C ``os.path`` predicate with a
    probe — once per process, at import.

    At import rather than on the first window, as the hook is: a gate module
    that says ``from os import stat`` binds whatever ``os.stat`` is when it is
    imported, and the registry imports every gate module before the sweep opens
    a single window. The spine is imported before any of them. A probe with no
    window open costs one attribute check. Named residual: a module that bound
    the original before the spine was imported, and ``os.DirEntry.stat()``,
    which is C and calls nothing (``_dir_digest`` keys a listing on each
    entry's kind and size instead).
    """
    for name in _STAT_FUNCTIONS:
        original = getattr(os, name)
        if getattr(original, "__atompipe_probe__", False):
            continue
        probe = _stat_probe(original, predicate=False)
        for supports in _SUPPORTS:
            known = getattr(os, supports, None)
            if isinstance(known, set) and original in known:
                known.add(probe)
        setattr(os, name, probe)
    for name in _STAT_PREDICATES:
        original = getattr(os.path, name, None)
        if (original is None or not isinstance(original, type(len))
                or getattr(original, "__atompipe_probe__", False)):
            continue                            # a Python function: it reaches os.stat
        setattr(os.path, name, _stat_probe(original, predicate=True))


_install_stat_probes()


# --------------------------------------------------------------------------- #
# the process probe
# --------------------------------------------------------------------------- #
# On POSIX, ``_posixsubprocess.fork_exec`` is how Python execs a new program
# without ``os.exec*`` or ``os.posix_spawn``, and it raises no audit event.
# ``subprocess.Popen`` raises its own first; multiprocessing does not. What
# slipped through (review round 3, ``probe.mp``): a gate that read its limit in
# a spawn-context ``ProcessPoolExecutor`` worker — spawn is the default start
# method on macOS and Windows — recorded ``files={} opaque=[]``; after the file
# went to 0 a plain check served the PASS ``cached``, ``status`` named nothing
# stale, ``doctor`` said ``opaque-inputs ok``, and ``check --force`` failed it
# at the same rho. On Windows the same start raises
# ``_winapi.CreateProcess``, which no handler took. And a forkserver child was
# opaque only by accident, as ``network`` (``_on_network``).


def _process_probe(original: Callable[..., Any]) -> Callable[..., Any]:
    """``original``, first reported to the hook as the event
    ``_posixsubprocess.fork_exec`` — through ``_audit`` itself, so the
    re-entrancy guard, the loader's bookkeeping and every open window are the
    hook's own. Outside a window it is one list check and the original call."""

    @functools.wraps(original)
    def probe(*args: Any, **kwargs: Any) -> Any:
        if _STACK:
            _audit("_posixsubprocess.fork_exec", args)
        return original(*args, **kwargs)

    probe.__atompipe_probe__ = True             # type: ignore[attr-defined]
    return probe


def _install_process_probe() -> None:
    """Replace ``_posixsubprocess.fork_exec`` with ``_process_probe`` — once per
    process, at import, for the stat probes' reason: a module that binds the
    function binds whatever it is then.

    multiprocessing looks it up on the module at every call
    (``util.spawnv_passfds``: each spawn worker, a forkserver's server, the
    resource tracker), so replacing the attribute reaches them all.
    ``subprocess`` bound the original at its own import and is not affected; it
    audits ``subprocess.Popen`` itself. *Rejected:* wrapping
    ``spawnv_passfds`` (multiprocessing's door only — any other caller of
    ``fork_exec`` would still be invisible); calling every import of
    multiprocessing opaque (a gate whose library merely imports it would never
    be Fresh). Windows has no ``_posixsubprocess`` and needs no probe:
    ``_winapi.CreateProcess`` audits itself. Named residual: a module that
    bound ``fork_exec`` before the spine was imported, and a C extension that
    forks or execs in C.
    """
    try:
        import _posixsubprocess
    except ImportError:
        return
    original = getattr(_posixsubprocess, "fork_exec", None)
    if original is None or getattr(original, "__atompipe_probe__", False):
        return
    _posixsubprocess.fork_exec = _process_probe(original)


_install_process_probe()


# --------------------------------------------------------------------------- #
# the environment
# --------------------------------------------------------------------------- #
# An environment read fires no audit event, so a gate that decided on a
# variable recorded nothing at all. What slipped through (review round 1,
# ``probe.env``): a gate passing while ``os.environ.get("PROBE_MODE", "ok") ==
# "ok"`` recorded ``files={} opaque=[]``, a plain check served its PASS after
# the variable moved, and a forced run failed it. rho does not key a variable's
# value (§8: an environment differs per machine and per shell, and a digest of
# it would make every entry stale in every other terminal); the entry NAMES the
# variable instead, as the opaque channel ``env:<NAME>`` — ``env:*`` for the
# whole environment (``dict(os.environ)``, a ``keys()``, ``len``) — and an
# entry with an opaque channel is never Fresh. ``doctor``'s ``env-reads`` row
# still names each read statically: every one costs a re-run per check.

#: Modules an environment read passes THROUGH on its way to ``os.environ``:
#: ``os.getenv``, ``Mapping.get``/``__contains__``/``keys()`` (``_collections_abc``),
#: ``os.path.expanduser``/``expandvars`` and ``pathlib``'s ``home()``. The first
#: frame outside them is the one whose read it is.
_ENV_VIA = _STAT_VIA | {"_collections_abc", "collections.abc"}


class _RecordingEnviron(os._Environ):
    """``os.environ``'s own class, recording each variable read while a window
    is open.

    Installed by changing the CLASS of the one ``os.environ`` object (and of
    ``os.environb``), never by replacing it: every binding of it — ``from os
    import environ`` in a gate module, ``os.getenv``'s global — sees the change,
    and the object, its data and the ``putenv`` each write makes stay exactly
    what they were, so a subprocess a gate starts inherits what it always did.
    That is the reason ``doctor``'s ``env-reads`` row gave for rejecting a
    dynamic proxy (the omc gates' tool inherits the environment), and it held
    against a replacement object, not against this. A read is the gate's only
    when the first frame outside ``_ENV_VIA`` is not library code: the
    variables ``shutil.which`` (``PATH``), ``tempfile`` (``TMPDIR``) and
    ``subprocess`` read on a gate's behalf are theirs, and a gate that asks
    them is not made opaque by it (``t.outside``).
    """

    def __getitem__(self, key: Any) -> Any:
        if _STACK and not getattr(_BUSY, "on", False):
            _env_read(key)
        return super().__getitem__(key)

    def __iter__(self) -> Any:
        if _STACK and not getattr(_BUSY, "on", False):
            _env_read(None)
        return super().__iter__()

    def __len__(self) -> int:
        if _STACK and not getattr(_BUSY, "on", False):
            _env_read(None)
        return super().__len__()


def _env_read(key: Any) -> None:
    """Record ``env:<key>`` (``env:*`` for ``None``) on every open trace, when the
    read is the gate's. Called from a ``_RecordingEnviron`` method; never raises."""
    _BUSY.on = True
    try:
        if key is None:
            name = "*"
        elif isinstance(key, (str, bytes)):
            text = os.fsdecode(key)
            # an undecodable byte must not become a lone surrogate in a channel
            # name the entry writer encodes as UTF-8 (``_process``'s rule)
            name = text.encode("utf-8", "surrogateescape").decode("utf-8", "backslashreplace")
        else:
            return                              # the lookup raises TypeError itself
        frame = sys._getframe(2)                # past this function and the method
        while frame is not None and frame.f_globals.get("__name__") in _ENV_VIA:
            frame = frame.f_back
        if frame is None or _frame_kind(frame) or _library_code(frame):
            return
        whole = "env:*"
        for trace in tuple(_STACK):
            if whole not in trace.opaque:
                trace.opaque.add(f"env:{name}")
    except Exception:
        pass
    finally:
        _BUSY.on = False


def _library_code(frame: Any) -> bool:
    """Whether ``frame`` runs library code: frozen (the standard library's
    ``<frozen os>``), or from a file under the library roots. Code with any
    other pseudo-filename (``<string>``) is not — its reads are the gate's."""
    filename = frame.f_code.co_filename
    if not isinstance(filename, str) or filename.startswith("<frozen "):
        return True
    path = _path_arg(filename)
    return path is not None and _library_path(path)


def _install_env_recorder() -> None:
    """Give ``os.environ`` and ``os.environb`` the recording class — once, with
    the hook (the same object is changed, so no binding made earlier misses it).
    Named residual: an ``environ`` that is not ``os._Environ`` (replaced by a
    test's ``mock.patch.object``, or another implementation's) records nothing."""
    for name in ("environ", "environb"):
        env = getattr(os, name, None)
        if type(env) is os._Environ:
            try:
                env.__class__ = _RecordingEnviron
            except TypeError:
                pass


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
#: decorator, which is where the spec is actually written); ``needs`` (P2.2-D1,
#: D-04: the prerequisite edge is for scheduling and resolution — a dependent's
#: measurement is a function of its own inputs, so a guard that recovered must
#: not re-run every dependent whose inputs never moved. Declaring an edge still
#: re-keys the declaring file's gates once, through the code digest, as any edit
#: to that file does).
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

#: The one key of a control entry's ``fixture`` hint, with a ``null`` digest,
#: when no loader recorded the fixture's code: a ``module:function`` fixture the
#: stock import system loaded (an installed module, a package's ``__init__``, an
#: extension — ``gates._import_fixture_module``). A ``null`` digest never
#: matches (``_snapshot_moved``), so the hint never holds and every ``check``
#: re-runs the fixture alone and compares what it builds (§3.8 step 4): a cost,
#: one fixture run per check, never a wrong admission. Spelled as no path is —
#: ``_locate`` finds no ``<anchor>`` by that name — and read as it stands in
#: ``status``'s pending note. What slipped through (admission review, round 1,
#: C): such a fixture filed ``{"files": {}}``, an empty mapping never moves, so
#: the hint held for ever, and a fixture outside ``selftest/`` defused
#: 400 -> 40 mm was served ``cached``. *Rejected:* reading every empty hint as
#: moved — the forged form (``record_control(bad=...)``, no trace) files ``{}``
#: on purpose, and a hint with nothing in it cannot say which case it is.
UNRECORDED_FIXTURE = "<unrecorded fixture code>"

#: Two outcomes recorded for one rho under the same instruments: an ERROR — the
#: gate reads "two outcomes recorded for identical inputs", its claims FAIL and
#: `doctor` fails the row. While False it was a warning and the gate read stale.
#: Staged on purpose (R-4): the refusal landed only after the detector had been
#: measured on the bundled corpus. The measurement (2026-09-28,
#: ``test_determinism.EntriesAreDeterministic``): every bundled pack baseline,
#: wrapped, plus the bracket, each checked by two cold processes in each of two
#: temp directories at different depths, trimesh, numpy and omc present — 60
#: entries and 60 control entries per run, byte-identical across all four runs,
#: so zero hits on honest bundled code; without those tools 49 and 49, same
#: bytes, the other 11 gates availability-skipped. The planted hit is
#: ``test_determinism.TwoOutcomes``. *Rejected:* an error from the start — an
#: omc-style nondeterminism (S-34: wall-clock time in ``detail``) would have
#: flagged honest gates red mid-phase, before anyone had measured it; "worse
#: outcome wins", which picks silently; staying a warning — a stale gate is
#: re-run, the re-run matches one of the two files, and the claim then reads
#: whatever that run said while the contradiction sits on disk unanswered.
TWO_OUTCOMES_IS_ERROR = True

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

#: The kinds that SUPERSEDE a cached entry at their inputs (``_crash_applies``,
#: a control's ``failed_here``): the gate ran and proved nothing. An
#: availability skip never ran it, so it supersedes nothing — and it never
#: replaces one of these at the same inputs (``remember``). What slipped through
#: (remembered outcomes, round 1): the sweep remembered the skip under the very
#: rho the crash was remembered under, one record per gate, so a crash, then a
#: check without the tool, then a check with it, served the PASS the crash had
#: superseded. *Rejected:* not remembering availability skips at all — the
#: record is what a reader shows for a gate that never ran here (S-68).
_SUPERSEDING_KINDS = ("error", "self-skip")

_ENTRY_FIELDS = ("schema", "gate", "rho", "code", "spine", "reads", "instruments",
                 "verdict", "digest")
_CODE_FIELDS = ("digest", "files", "fallback")
_READ_FIELDS = ("params", "files", "dirs", "ledger", "model", "opaque")
_CONTROL_READ_FIELDS = ("params", "files", "dirs", "ledger", "host", "opaque")
#: The one optional key of a ``reads`` block, written just before ``opaque`` and
#: only by a run that read ``ctx.tier`` (``Reads.tier``). Optional rather than
#: always written as ``null``: every entry of a gate that never looks at the tier
#: keeps the block, and the rho, it had.
_TIER_READ = "tier"

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
    it before the module runs), else of a module that registered it
    (``modelio.registered_by``), or ``""`` for a project's own gate. What
    slipped through with the defining module alone (Phase 1 review, ``p4``): a
    pack gate a factory in the pack's helper made — the helper is imported,
    not loaded by the pack, and carries no ``PACK_DIR`` — was owned by the
    PROJECT, so its control's static part walked the project's ``selftest/``
    and an edit of the pack's fixture moved nothing."""
    target = getattr(fn, "__func__", fn)
    home = sys.modules.get(getattr(target, "__module__", None) or "")
    for module in (home, *modelio.registered_by(fn)):
        pack_dir = vars(module).get("PACK_DIR") if module is not None else None
        if isinstance(pack_dir, str) and pack_dir:
            return os.path.abspath(pack_dir)
    return ""


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

    From the closure ``modelio`` recorded while the gate's module ran — the
    defining module's merged with every module that registered ``fn``
    (``modelio.code_closure``; a factory's function is the helper's, and the
    limit the caller handed it once keyed nowhere, repro ``p4``): every
    file under its roots, by the bytes that executed (a same-size, same-second
    edit ran the old bytecode before, S-26), spelled portably (``anchors``; a
    project's gate reads ``gates/structural.py``, a pack's
    ``<pack:NAME>/gates/mesh.py``); plus every file that code read while it
    ran (``CodeClosure.data``, spelled the same way — ``NO_BYTES`` for one it
    found missing); plus each ``atompipe.*`` module it imports
    that ``SPINE_MODULES`` does not cover, by canonical AST (cad and fdm import
    ``atompipe.site``: a page change re-runs their gates, nobody else's); plus
    the ``SPEC_FIELDS_IN_RHO`` values. What slipped through before the data
    (admission review, round 1, D, the verdict side): a gate module that read
    its limit from ``inputs/data/limit.json`` at import — loaded before any
    window, so on no trace — kept a Fresh PASS after the limit went 100 -> 50
    mm under a design at 80.

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
    for path, sha in closure.files + closure.data:
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
    code = {_clean(_spell_code(path, anchors)): sha or None
            for path, sha in closure.files + closure.data}
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
    function level, resolving to an installed tree or to nothing, not stdlib,
    not atompipe). The version: ``importlib.metadata``'s, ``"unknown"`` when
    importable without metadata, ``"absent"`` when not importable.

    A module beside the project that is not installed — a monorepo's
    ``shared/beamlib.py`` on ``sys.path`` — is never here: it is the gate's
    code, in its closure and its rho (``modelio._code_root``). Filed here as
    ``beamlib: unknown`` it once kept a PASS Fresh after its allowable moved
    (review round 1, ``mono``).

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


def _entry_form(entry: Any) -> Any:
    """What a listing says of one entry: ``"dir"``, a regular file's size in
    bytes, or ``"other"`` (a broken link, a FIFO, a vanished entry)."""
    try:
        if entry.is_dir():
            return "dir"
        if entry.is_file():
            return int(entry.stat().st_size)
    except OSError:
        pass
    return "other"


def _dir_digest(path: str) -> str | None:
    """A listing, digested as its sorted ``[name, kind or size]`` entries —
    bytecode left out, so the first run's ``__pycache__`` does not move it — or
    ``None`` when there is no directory to list.

    Sizes and kinds because a listing is all a gate that iterates
    ``os.scandir`` touched: ``DirEntry.is_file()`` comes from the listing and
    ``DirEntry.stat()`` is C that calls nothing a probe or the hook can see. What
    slipped through while it was names only: a gate deciding on the largest
    file in a directory kept a Fresh PASS after one grew (review round 1).
    *Rejected:* mtimes (they differ per checkout, so a committed entry would be
    stale in every clone — a gate that decides on one is named residual);
    a directory's own size (``st_size`` of a directory differs per filesystem);
    the bytes of every entry (the listing would become a read of the whole tree).
    """
    try:
        with os.scandir(path) as listing:
            rows = [[entry.name, _entry_form(entry)] for entry in listing
                    if entry.name != "__pycache__" and not entry.name.endswith(_BYTECODE)]
    except OSError:
        return None
    return _digest_of(_clean(sorted(rows, key=lambda row: row[0])))


def _path_digest(path: str, digests: FileDigests) -> str | None:
    """A file input as rho keys it: the sha256 of its bytes, ``DIRECTORY`` for a
    directory, ``None`` when it is missing or neither."""
    digest = digests.digest(path)
    if digest is None and os.path.isdir(path):
        return DIRECTORY
    return digest


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
              selfmod: set, written: list, static: set | None,
              stat: bool = False) -> tuple[str, str]:
    """``("drop", "")``, ``("read", <portable>)`` or ``("opaque", <channel>)``
    for one path a trace read — spec §3.4's rules, first match wins.

    ``stat=True`` classifies a path the trace only ASKED about (existence,
    kind, size) and never opened or listed. Under the project or a pack that is
    a file input like any read, and ``selfmod`` holds the paths whose answer
    this window changed itself; everywhere else a question is not an input the
    way a read is, because honest gates ask them constantly: ``shutil.which``
    stats every ``PATH`` entry, ``realpath`` every ancestor of the root,
    ``os.makedirs`` every ancestor of the out dir. So outside the project it is
    dropped (named residual: a gate that decides on the existence of a file
    outside its project), and under the out dir or the spine's state only a
    FILE, or a missing path, that this window did not write is opaque — another
    gate's output, the S-27 shape — while a directory there is scaffolding.
    """
    p = _norm(path)
    # 1. the interpreter's and the libraries' own files; bytecode
    if _library_path(p):
        return "drop", ""
    # 2. read, and later written, in this window: its pre-write bytes are gone.
    #    (Written first and read after never reached the read list at all.)
    if not stat and not is_dir and p in selfmod:
        return "opaque", f"self-modified:{places.shown(p)}"

    def selftest_covered(rel: str) -> bool:
        if not control or not (rel == _SELFTEST or rel.startswith(_SELFTEST + "/")):
            return False
        if is_dir:
            return True
        return p in static if static is not None else _walkable(rel)

    def asked_mine() -> bool:
        # a question about scaffolding, or about what this window wrote
        return stat and (is_dir or any(w == p or w.startswith(p + os.sep) for w in written))

    # 3. under a pack
    for name, spellings in places.packs:
        rel = places.rel(p, spellings)
        if rel is not None:
            if selftest_covered(rel):
                return "drop", ""
            if stat and p in selfmod:
                return "opaque", f"self-modified:{places.shown(p)}"
            return "read", f"<pack:{name}>/{rel}" if rel else f"<pack:{name}>"
    # 4. under the sweep's or the control's out_dir, and not this window's output
    for prefix, spellings in (("controls/", places.controls), ("", places.out)):
        rel = places.rel(p, spellings)
        if rel is None:
            continue
        if (is_dir and not stat and any(w == p or w.startswith(p + os.sep) for w in written)
                or asked_mine()):
            return "drop", ""                   # a listing of what it wrote itself
        where = (prefix + rel) if rel else (prefix.rstrip("/") or ".")
        return "opaque", f"out:{where} (not written by this gate)"
    rel = places.rel(p, places.root)
    if rel is not None:
        # 5. the spine's own state is never an input
        if rel == _STATE_DIR or rel.startswith(_STATE_DIR + "/"):
            if asked_mine():
                return "drop", ""
            return "opaque", f"atompipe-state:{rel}"
        # 6. the project
        if selftest_covered(rel):
            return "drop", ""
        if stat and p in selfmod:
            return "opaque", f"self-modified:{places.shown(p)}"
        return "read", rel or "."
    # 7. anything else
    if stat:
        return "drop", ""
    return "opaque", f"file-outside-project:{places.shown(p)}"


@dataclass
class Reads:
    """What one run read, as an entry keys it.

    ``params`` — ``[[path, digest], ...]`` or ``[[path, digest, small], ...]``
    (``path`` a JSON list; ``small`` only when the value is small, for a stale
    line that says ``config.bed_xy 220.0 -> 250.0``); ``files`` —
    ``{portable path: sha | DIRECTORY | None}`` (``None``: missing, which is
    itself an input), every path opened or only asked about; ``dirs`` —
    ``{portable path: listing digest | None}``; ``ledger`` — ``{"claim:<id>" | "<list>": digest}``; ``model`` — the
    ``model_digest`` when the gate used ``ctx.model``; ``opaque`` — sorted
    channel names; ``host`` — a control's host-param reads, ``[[path,
    digest], ...]``; ``tier`` — the sweep tier the gate (or a control's fixture
    and gate) read through ``ctx.tier``, ``None`` when it never looked, and
    then absent from the block and from rho, so an entry of a gate that never
    reads it is keyed exactly as before. Only the display values are left out
    of rho.
    """

    params: list = field(default_factory=list)
    files: dict = field(default_factory=dict)
    dirs: dict = field(default_factory=dict)
    ledger: dict = field(default_factory=dict)
    model: str | None = None
    opaque: list = field(default_factory=list)
    host: list = field(default_factory=list)
    tier: int | None = None

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

        A module source the stock import system loaded (``trace.sources``) is a
        read by rules 3 and 6 and dropped by every other: code on ``sys.path``
        outside the project is not an input of it.

        A path the trace only asked about (``trace.stats``) and neither opened
        nor listed follows ``_classify(stat=True)``: a file input under the root
        or a pack (opaque ``self-modified:`` when the window changed the answer
        itself), dropped outside them, and opaque under the out dir or the
        spine's state only when it is another gate's file.

        In a CONTROL trace, reads under the owner's ``selftest/`` are dropped:
        the static walk covers them, and a fixture module's import-time read of
        ``baseline.json`` happens only on its first load in a process, so keying
        it would make rho_control depend on module-cache state. (An import-time
        read is never on a control's trace — the module loads before the window
        — and is keyed by the fixture's closure instead: ``CodeClosure.data``.)
        ``static`` — the absolute paths the walk actually covered — narrows that
        to exactly those files (a git-ignored file a fixture reads is still an
        input); without it, any walkable file under a ``selftest/`` is dropped.

        ``anchors`` defaults to ``trace.anchors``, else to ``<tmp>`` and ``~``
        only — then every project file is outside and opaque: an unanchored
        trace is never Fresh rather than wrongly portable. The trace's own
        opaque channels (``subprocess:omc``, ``network``, ``env:<NAME>``,
        ``sqlite-writable:<path>``, a non-JSON param) pass through; a gate that used the model is keyed by ``model`` (see
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
                files[_clean(what)] = _path_digest(path, digests)
            elif action == "opaque":
                opaque.add(_clean(what))
        # A module the stock import system loaded while the window was open
        # (`_on_import_read`): an input under the project or a pack, like any
        # read there; anywhere else it is somebody's code on `sys.path`, not an
        # input of this project, and — unlike a read — not an opaque channel.
        for path in getattr(trace, "sources", ()):
            action, what = _classify(path, places, is_dir=False, control=control,
                                     selfmod=set(), written=written, static=static_set)
            if action == "read":
                files.setdefault(_clean(what), _path_digest(path, digests))
        dirs: dict[str, str | None] = {}
        for path in sorted(trace.dirs):
            action, what = _classify(path, places, is_dir=True, control=control,
                                     selfmod=selfmod, written=written, static=static_set)
            if action == "read":
                dirs[_clean(what)] = _dir_digest(path)
            elif action == "opaque":
                opaque.add(_clean(what))
        # What the window only ASKED about. A path it also opened or listed is
        # keyed there already (bytes and listings both move when the kind does).
        # One whose answer the window changed itself — asked while missing and
        # made (``os.makedirs``), asked while there and removed, or written after
        # — has lost the state the gate decided on: self-modified, like a read.
        # (An existence answer is compared both ways, followed and not, so a
        # broken symlink asked through os.stat is not a flip.)
        seen = ({_norm(path) for path in trace.files_read} | {_norm(p) for p in trace.dirs}
                | {_norm(p) for p in getattr(trace, "sources", ())})
        stat_selfmod = set()
        for path in trace.stats:
            before = trace.stat_existed(path)
            if (_norm(path) in written
                    or before is not None and before != os.path.exists(path)
                    and before != os.path.lexists(path)):
                stat_selfmod.add(_norm(path))
        for path in trace.stats:
            if _norm(path) in seen:
                continue
            action, what = _classify(path, places, is_dir=os.path.isdir(path),
                                     control=control, selfmod=stat_selfmod,
                                     written=written, static=static_set, stat=True)
            if action == "read":
                files[_clean(what)] = _path_digest(path, digests)
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
        tier = getattr(trace, "tier", None)
        return cls(params=params, files=dict(sorted(files.items())),
                   dirs=dict(sorted(dirs.items())),
                   ledger=_clean(dict(sorted(trace.ledger.items()))),
                   model=model_value, opaque=sorted(opaque), host=host,
                   tier=None if tier is None else int(tier))

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Reads":
        """An entry's ``reads`` block (gate or control shape) back into ``Reads``."""
        data = data or {}
        return cls(params=[list(row) for row in data.get("params") or []],
                   files=dict(data.get("files") or {}), dirs=dict(data.get("dirs") or {}),
                   ledger=dict(data.get("ledger") or {}), model=data.get("model"),
                   opaque=sorted(data.get("opaque") or []),
                   host=[list(row) for row in data.get("host") or []],
                   tier=data.get("tier"))

    def to_dict(self, *, control: bool = False) -> dict:
        """The entry's ``reads`` block, keys in their fixed order: ``params, files,
        dirs, ledger, model, opaque`` — or, for a control, ``host`` in place of
        ``model`` — with ``tier`` before ``opaque`` only when one was read."""
        out: dict = {"params": [list(row) for row in self.params],
                     "files": dict(sorted(self.files.items())),
                     "dirs": dict(sorted(self.dirs.items())),
                     "ledger": dict(sorted(self.ledger.items()))}
        if control:
            out["host"] = [list(row) for row in self.host]
        else:
            out["model"] = self.model
        if self.tier is not None:
            out["tier"] = int(self.tier)
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


def _read_tier(reads: Any) -> int | None:
    """The tier an entry's or a control entry's ``reads`` block recorded, or
    ``None`` when its run never read ``ctx.tier``."""
    tier = (reads or {}).get(_TIER_READ) if isinstance(reads, Mapping) else None
    return None if isinstance(tier, bool) or not isinstance(tier, int) else tier


def rho(gate_id: str, spine: str, code: Any, reads: Any) -> str:
    """The content address of a verdict: sha256 of canonical JSON of
    ``{"schema", "gate", "spine", "code", "params", "files", "dirs", "ledger",
    "model", "opaque"}``, plus ``"tier"`` when the gate read ``ctx.tier`` — the
    gate, the spine digest, the code digest (a ``CodeRef`` or its digest string)
    and what the gate read (``Reads`` or an entry's ``reads`` block). Display
    values never enter it; instruments never do (Q1.3); a prerequisite's outcome
    never does (Q1.7, which keeps P2's ``needs`` additive)."""
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
    keys = list(_CONTROL_READ_FIELDS if control else _READ_FIELDS)
    tiered = keys[:-1] + [_TIER_READ] + keys[-1:]
    if not isinstance(reads, dict) or list(reads) not in (keys, tiered):
        return f"reads must be an object with keys {keys} ({_TIER_READ!r} before 'opaque' " \
               f"when ctx.tier was read)"
    if _TIER_READ in reads:
        tier = reads[_TIER_READ]
        if isinstance(tier, bool) or not isinstance(tier, int) \
                or tier not in {t.value for t in Tier}:
            return f"reads.tier must be one of {[t.value for t in Tier]}, not {tier!r}"
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
    ``instruments_for``; writes the entry (``write_entry``); and clears the
    gate's remembered outcomes at the entry's rho and at ``""`` (never ran) — a
    cacheable outcome at these inputs is newer than the crash it supersedes
    (§3.9), and says nothing about a crash at other inputs (``forget``). The
    sweep clears more: every rho current before its run (``_superseded``),
    which only it has.

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
    forget(root, gate_id, ("", keyed.rho))
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
    """The ``fixture`` hint of a control run under ``trace``: its recorded code
    closure, ``{"digest", "files": {portable path: sha}}``. A run whose fixture
    code no loader recorded — no closure, or one naming no file — files
    ``UNRECORDED_FIXTURE``, which never holds; only the forged form (no trace
    at all) files ``{}``."""
    closure = getattr(trace, "fixture_code", None) if trace is not None else None
    files: dict[str, str | None] = {}
    if isinstance(closure, modelio.CodeClosure):
        for path, sha in closure.files + closure.data:
            files[_clean(_spell_code(path, anchors))] = sha or None
    if trace is not None and not files:
        files = {UNRECORDED_FIXTURE: None}
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
    and the path it failed on — the tier its run read, if it read one
    (``_control_key``; ``when`` from the caller: this module reads no clock) —
    and ``None`` is returned.

    **The forged form** passes ``bad="fail"`` (and a ``detail``) with no result
    and no trace: an entry with empty reads, which a renderer test uses to plant
    an admission. It forges only the inner loop; R-9's re-execution at every
    money boundary is the defence, not this function.

    ``host``: ``"known-good"`` (the spine handed a project fixture
    ``selftest/known_good.py``'s context, D-27) or ``"live"``; host-param reads
    are keyed in rho_control only when live. Writing a control entry clears the
    remembered control failures of this gate at the entry's static part on the
    path every tier shares and on the path its run read — never at another
    static, nor on another tier's path (``_answered_controls``, ``forget``).
    """
    if host not in _HOSTS:
        raise AtompipeError(f"host must be one of {list(_HOSTS)}, not {host!r}")
    built = _control_entry(root, spec, fn, result=result, trace=trace, host=host, bad=bad,
                           detail=detail, digests=digests, anchors=anchors)
    key = f"control:{spec.id}"
    if built.entry is None:
        remember(root, key, built.held, input_rho=built.failure_key, kind=built.kind,
                 when=when)
        return None
    written = write_control(root, built.entry)
    forget(root, key, _answered_controls(built.entry.static, built.tier))
    return written


@dataclass(frozen=True)
class _BuiltControl:
    """A control run as ``record_control`` would file it, before anything is
    written: ``entry`` for a measurement, else ``held`` — the verdict to
    remember — and its ``kind``; ``static`` the part it is keyed by, ``tier``
    the one its run read through ``ctx.tier`` (``None``: never looked)."""

    static: str
    entry: ControlEntry | None = None
    kind: str = ""
    held: Verdict | None = None
    tier: int | None = None

    @property
    def failure_key(self) -> str:
        """What a failure of this run is remembered under (``_control_key``)."""
        return _control_key(self.static, self.tier)


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
    tier = _trace_tier(trace)
    measured = limit = None
    units = ""
    if bad is None:
        if result is None:
            raise AtompipeError(f"{spec.id}: record_control needs the selftest result "
                                f"or an explicit bad=")
        bad, kind = _control_outcome(spec, result)
        if bad is None:
            return _BuiltControl(static, kind=kind, held=_held_control(result, kind),
                                 tier=tier)
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
        # walk already keys them. That holds because `_known_good` hands
        # `context` nothing of the live design (`_KNOWN_GOOD_BLANK`) and runs it
        # inside this trace — handed a copy of the live host, a `context` that
        # kept its params made these the live design's reads, dropped here
        # unkeyed (admission review, round 1).
        reads = dataclasses.replace(reads, host=[])
    entry = ControlEntry(gate=spec.id, rho=rho_control(spec.id, static, reads),
                         static=static, static_parts=parts, host=host,
                         fixture=_fixture_part(trace, anchors),
                         reads=reads.to_dict(control=True), bad=bad, good=None,
                         admitted="reject-only" if bad == "fail" else "no",
                         detail=portable(detail, anchors), measured=measured, limit=limit,
                         units=units)
    return _BuiltControl(static, entry=entry, tier=tier)


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
            isinstance(key, str) and isinstance(records, dict) and records
            and all(isinstance(input_rho, str) and isinstance(rec, dict)
                    and set(rec) == {"kind", "verdict", "when"}
                    and rec["kind"] in _REMEMBER_KINDS
                    and isinstance(rec["verdict"], dict) and isinstance(rec["when"], str)
                    for input_rho, rec in records.items())
            for key, records in (data.items() if isinstance(data, dict) else ())):
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
    ``(key, input_rho)``** — ``input_rho`` the rho the sweep computed for that
    gate from current digests just BEFORE the run, at the tier the run took
    (the state's ``input_rho``: a Fresh entry's, the recomputed rho of the
    latest entry's read signature — of the latest one read at that tier, when
    the state's own was read at another — or ``""`` when none was); for a
    control, the current static part and the path it failed on
    (``_control_key``: ``<static>@<tier>`` when the run read ``ctx.tier``).
    *Rejected:* the failing run's own rho — a crash at partial reads has a
    different rho than the PASS it followed, so "supersede at the same rho"
    would never match and the next plain check would serve the old PASS.
    Nothing remembered is evidence, so remembering a pass or a fail is
    refused: those are cached.

    One record per ``(key, input_rho)``, the newest — except that an
    ``"availability"`` record never replaces an ``"error"`` or ``"self-skip"``
    one (``_SUPERSEDING_KINDS``): a check that could not run the gate has
    learned nothing that answers the crash. What slipped through (remembered
    outcomes, round 1): ONE record per gate. A skip for a missing tool
    overwrote the crash at the same rho, and a pass or fail at ANY rho forgot
    it (``forget`` took the gate, not the rho); either way the next check at
    the crashed inputs served the PASS the crash superseded — ``0 executed, 6
    cached``. *Rejected:* a cap on records per gate — the record a cap evicts
    is the PASS served again; the file is untracked and holds one small record
    per rho a gate crashed at and has not since measured.
    """
    if kind not in _REMEMBER_KINDS:
        raise AtompipeError(f"a remembered outcome is one of {list(_REMEMBER_KINDS)}, "
                            f"not {kind!r}")
    if verdict.outcome in ("pass", "fail"):
        raise AtompipeError(f"{verdict.gate}: a pass or a fail is a measurement and is "
                            f"cached (record_verdict), never remembered")
    gate_id = key.split(":", 1)[1] if key.startswith("control:") else key
    _check_gate_id(gate_id)
    input_rho = str(input_rho or "")
    data = _read_outcomes(root)
    records = data.setdefault(key, {})
    held = records.get(input_rho)
    if kind not in _SUPERSEDING_KINDS:
        if held is not None and held["kind"] in _SUPERSEDING_KINDS:
            return
        # One availability record per key, the newest: it supersedes nothing,
        # so nothing reads it for its rho. *Rejected:* one per rho, like a
        # crash — a machine without omc would grow the file by a record per
        # model edit per omc gate, for no reader.
        records = {r: rec for r, rec in records.items() if rec["kind"] in _SUPERSEDING_KINDS}
    stored = {k: v for k, v in verdict.to_dict().items() if k not in _NEVER_REMEMBERED}
    records[input_rho] = {"kind": kind, "verdict": _clean(stored), "when": str(when or "")}
    data[key] = dict(sorted(records.items()))
    atomic_write_json(_outcomes_path(root), data)


#: Verdict fields a remembered outcome never carries, written or read:
#: ``unqualified`` is the spine's mark for an evaluator refused at its version,
#: set fresh by every resolution from the control records. The file is untracked
#: and hand-editable, and this is its ONE reader (``remembered``), so a mark left
#: in it — by hand, or by a spine that stored it — is dropped here, for every
#: reader at once. What slipped through the first design (P2.1 review): the drop
#: sat in ``_as_spec``, which the orphan rung never calls, so a hand-edited
#: record for an unregistered gate read Gap instead of errored — a crash made
#: quieter than a skip. ``blocked_by`` and ``blocked_kind`` (P2.2-D9) on the same
#: terms: the prerequisite mark is set fresh by every resolution and sweep, and a
#: record carrying it — by hand, or from a spine that stored it — would make a
#: gate's own skip read as a prerequisite's, or (``"errored"``) louder than it
#: is, or name a root nobody has. Dropped on write too (``remember``), so the
#: file never holds what no reader may read.
_NEVER_REMEMBERED = frozenset({"unqualified", "blocked_by", "blocked_kind"})


def remembered(root: str) -> dict:
    """``{key: {input_rho: {"input_rho", "kind", "verdict": Verdict, "when"}}}``
    — every remembered outcome, per key one record per ``input_rho``. Raises
    ``AtompipeError`` naming the file when it does not parse (see
    ``_read_outcomes``). A record's ``unqualified`` is dropped
    (``_NEVER_REMEMBERED``): a remembered crash reads as a crash."""
    return {key: {input_rho: {"input_rho": input_rho, "kind": rec["kind"],
                              "verdict": Verdict.from_dict(
                                  {k: v for k, v in rec["verdict"].items()
                                   if k not in _NEVER_REMEMBERED}),
                              "when": rec["when"]}
                  for input_rho, rec in sorted(records.items())}
            for key, records in sorted(_read_outcomes(root).items())}


def forget(root: str, key: str, input_rhos: Iterable[str]) -> bool:
    """Drop ``key``'s remembered outcomes at ``input_rhos``; ``True`` when there
    was one. Called when a cacheable outcome (or a control entry) is recorded
    at those inputs — and ONLY those: a pass at B says nothing about the crash
    at A, and forgetting A's there served A's superseded PASS once the inputs
    came back (``remember``). The caller names the rhos: the new entry's own,
    and the ones a crash at the inputs it ran on could be remembered under
    (``_superseded``)."""
    path = _outcomes_path(root)
    if not os.path.exists(path):
        return False
    data = _read_outcomes(root)
    records = data.get(key) or {}
    dropped = [rho_ for rho_ in {str(r or "") for r in input_rhos} if rho_ in records]
    if not dropped:
        return False
    for rho_ in dropped:
        del records[rho_]
    if records:
        data[key] = records
    else:
        del data[key]
    atomic_write_json(path, data)
    return True


def _control_key(static: str, tier: int | None) -> str:
    """What a failed control run is remembered under in ``control:<gate>``:
    the static part, and — when the run read ``ctx.tier`` — ``@<tier>``, the
    path it failed on. A failure that never read the tier (an unusable fixture,
    a crash before the gate looked) is on the path every tier shares, and keeps
    the bare static.

    What slipped through (review, remembered control failures by tier): the
    key was the static part alone, though rho_control keys the tier the
    control's gate read. ``check --tier 2 --force`` crashed the costlier path's
    control; an edit sent the next plain check down the cheap path, whose
    control fired and forgot that crash; the edit reverted, ``check --tier 2``
    served the tier-2 control entry the crash had superseded, admitted, and the
    PASS cached — while ``--force`` crashed again. *Rejected:* a tier field on
    the record — the file keeps one shape, one record per key, as the gate's
    records did (a signature's rho already names its tier); and a digest of
    ``(static, tier)`` — at ``at=None`` a reader must find every path's failure
    at one static (``_control_failures``), which a digest hides.
    """
    return static if tier is None else f"{static}@{int(tier)}"


def _trace_tier(trace: Any) -> int | None:
    """The tier a control run read through ``ctx.tier`` (``GateTrace.tier``),
    or ``None`` when it never looked — or there is no trace (the forged form)."""
    tier = getattr(trace, "tier", None) if trace is not None else None
    return None if isinstance(tier, bool) or not isinstance(tier, int) else int(tier)


def _answered_controls(static: str, *tiers: int | None) -> set[str]:
    """The failure keys a control entry at ``static`` answers: the shared
    path's, and the path of each tier in ``tiers`` it was run on or read —
    never another path's. A control firing on the tier-0 path says nothing
    about a crash of the control on the tier-2 one (``_control_key``)."""
    return {static} | {_control_key(static, tier) for tier in tiers if tier is not None}


def _control_failures(held: Mapping[str, Any], gate_id: str, static: str,
                      at: int | None) -> list[tuple[int | None, Mapping[str, Any]]]:
    """``gate_id``'s remembered control failures (a crash, an unusable fixture,
    a self-skip) standing against an admission asked at ``at``, as ``(the tier
    the failed run read, or None; the record)``, the shared path's first: at
    this static, on the path every tier shares or the one ``at`` picks — at
    ``at=None`` (a verdict that never read the tier, whose admission counts a
    control of any tier) on every path. An availability record is never one
    (it is re-evaluated where it is shown), nor a record at another static
    (that version's: remembered outcomes, round 1)."""
    found: list[tuple[int | None, Mapping[str, Any]]] = []
    for key, record in (held.get(f"control:{gate_id}") or {}).items():
        if record["kind"] not in _SUPERSEDING_KINDS:
            continue
        suffix = key[len(static) + 1:] if key.startswith(static + "@") else ""
        if key == static:
            tier = None
        elif suffix.isascii() and suffix.isdigit():
            tier = int(suffix)
        else:
            continue
        if tier is None or at is None or tier == at:
            found.append((tier, record))
    return sorted(found, key=lambda pair: (pair[0] is not None, pair[0] or 0))


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


def _undemonstrated(at: int | None) -> str:
    """``_UNDEMONSTRATED`` for an entry whose run took the path ``ctx.tier``
    ``at`` picks: the check that can settle it names that tier, ``… run
    atompipe check --tier 2``, since a sweep below it judges that path's control
    by the records alone and never runs it (Q1.6). ``at`` ``None`` (the gate
    never read the tier) or 0 is the plain words, which a default check settles.

    What slipped through (review, ``repro_undemonstrated``): the words said
    ``run atompipe check`` for a tier-2 entry. That check served the same stale
    row every time, and ``check --force`` re-proved only the cheap path, so the
    advice was a loop. *Rejected:* the admission's own reason (``no control
    entry at this version``) — it says why, not what settles it, and every
    reader matches the fixed words."""
    return f"{_UNDEMONSTRATED} --tier {at}" if at else _UNDEMONSTRATED


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
    across the gate's read signatures. ``path`` — the rhos of ``current`` on
    the served entry's path: signatures read at its tier, or that never read
    ``ctx.tier`` — the set a remembered crash is matched against (§3.9;
    ``_crash_applies``). ``here`` and ``input_rho`` — the asking sweep's
    (``_by_tier``): what a measurement there clears, and what a crash there
    is remembered under. For a gate that never reads the tier, ``path`` and
    ``here`` are ``current`` and ``input_rho`` is ``rho``.
    """

    entry: Entry
    notes: tuple = ()
    rho: str = ""
    current: frozenset = frozenset()
    path: frozenset = frozenset()
    here: frozenset = frozenset()
    input_rho: str = ""
    state: ClassVar[str] = "fresh"


@dataclass(frozen=True)
class Stale:
    """No entry's rho is current. ``entry`` is the latest one (obs, then commit
    time, then name) and ``reasons`` what moved since it: param moves as
    ``config.bed_xy 220.0 -> 250.0``, then files, code, spine — at most
    ``MAX_STALE_REASONS``, then ``(+n more)``. ``rho`` is the latest entry's
    read signature recomputed now; ``input_rho`` the one a crash of the asking
    sweep is remembered under — ``rho``, unless that signature read another
    tier than the sweep's (``_by_tier``). ``conflict`` holds the entries when
    two outcomes were recorded for the current inputs (§3.7) — stale while
    ``TWO_OUTCOMES_IS_ERROR`` is False, an error once it is True."""

    entry: Entry
    reasons: tuple = ()
    rho: str = ""
    current: frozenset = frozenset()
    conflict: tuple = ()
    here: frozenset = frozenset()
    input_rho: str = ""
    state: ClassVar[str] = "stale"


@dataclass(frozen=True)
class Unknown:
    """Nothing can say whether the entry is current: the model does not load
    and it read parameters (S-21); it has an opaque channel (a subprocess's own
    reads, a non-JSON value); the spine cannot digest itself (S-29); or the
    gate's code cannot be keyed. Resolves like stale, never fresh. ``rho`` is
    the recomputed address when one exists (an opaque signature still has one:
    a crash there is matched by it), else ``""``; ``here`` and ``input_rho`` as
    ``Stale``'s."""

    entry: Any
    reason: str
    rho: str = ""
    current: frozenset = frozenset()
    here: frozenset = frozenset()
    input_rho: str = ""
    state: ClassVar[str] = "unknown"


@dataclass(frozen=True)
class Never:
    """The gate has no entry: never ran here, or only crashed or skipped
    (those are remembered, never cached)."""

    state: ClassVar[str] = "never"
    entry: ClassVar[Any] = None
    rho: ClassVar[str] = ""
    current: ClassVar[frozenset] = frozenset()
    here: ClassVar[frozenset] = frozenset()
    input_rho: ClassVar[str] = ""


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
    backwards, and one ``selftest/`` walk per owner. ``tier`` is the sweep's
    tier when a sweep is asking, ``None`` for a reader — which has no tier of
    its own, and judges a tier-reading entry by the most thorough tier recorded
    (``_judge``)."""

    def __init__(self, root: str, projection: Any, ledger: Any, *, anchors: Anchors,
                 digests: FileDigests | None, model: Any = None,
                 tier: int | None = None) -> None:
        self.root = os.path.abspath(root)
        self.tier = None if tier is None else int(_plain_tier(tier))
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
        not know). One rule with ``_values_match``'s (``_ledger_now``)."""
        return _ledger_now(self.ledger, key)

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
             anchors: Anchors | None, digests: FileDigests | None, model: Any,
             tier: int | None = None) -> _Now:
    if anchors is None:
        anchors = anchors_for(root, registry, out_dir=store.out_dir(root) if root else "")
    return _Now(root, projection, ledger, anchors=anchors, digests=digests, model=model,
                tier=tier)


# --------------------------------------------------------------------------- #
# judging one gate's entries
# --------------------------------------------------------------------------- #
def _signature(entry: Entry) -> str:
    """What an entry read, without the values: the addresses its rho is
    recomputed over. Entries of one gate that read different paths (a branch on
    a mode switch) are judged each against its own paths. A presence-only read
    is its own kind of address: ``k in p`` recomputes to PRESENT/ABSENT, a value
    read to the value's digest. The tier an entry read is part of its address
    as a VALUE: the tier is not re-read from anything, so each group is one
    tier, judged against the asking sweep's (``_judge``)."""
    reads = entry.reads or {}
    return _canonical_json({
        "tier": _read_tier(reads),
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
        files[spelled] = _path_digest(where, now.digests)
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
    # The tier is carried as recorded: what a sweep's own tier makes of it is
    # `_judge`'s serving rule, not a digest re-read here.
    return Reads(params=params, files=files, dirs=dirs, ledger=ledger, model=model,
                 opaque=list(reads.get("opaque") or ()), tier=_read_tier(reads)), ""


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
    recorded_tier = _read_tier(reads)
    if recorded_tier is not None and reads_now.tier != recorded_tier:
        reasons.append(f"ctx.tier {recorded_tier} -> {reads_now.tier}")
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


def _most_thorough(matches: list[Entry]) -> list[Entry]:
    """``matches`` narrowed to the entries of the highest tier any of them read;
    an entry that never read the tier is kept as it is.

    The serving rule for a gate that read ``ctx.tier``: a sweep at tier N may
    serve an entry recorded at t when ``t >= N`` (``_judge`` makes a group below
    N stale), the highest t first; a reader, with no tier of its own, takes the
    highest t there is. A gate "may use [the tier] to pick a cheaper path, but
    must not use it to lower its own standard" (``GateContext``), so the
    costlier path is the more thorough answer to the same question, and serving
    it costs nothing. What slipped through (review round 1, ``probe.tier``):
    nothing keyed the tier at all, and a tier-0 PASS was served to ``check
    --tier 2``. *Rejected:* exact match only (t == N) — a tier-0 ``check``
    would serve its cheap PASS over the costlier path's FAIL at the same inputs
    while ``status`` showed the FAIL, and a refutation keeps its power (R-3);
    calling two tiers' different outcomes "two outcomes for identical inputs" —
    they are not identical, and a cheap bound that FAILs where the solver
    PASSes is an honest gate.
    """
    tiers = [t for t in (_read_tier(e.reads) for e in matches) if t is not None]
    if not tiers:
        return matches
    top = max(tiers)
    return [e for e in matches if _read_tier(e.reads) in (None, top)]


def _judge(spec: Any, code: CodeRef, entries: list[Entry], now: _Now,
           order: Callable[[Entry], tuple]) -> Fresh | Stale | Unknown | Never:
    """One gate's state, from its entries. Never runs the gate. A group that
    read ``ctx.tier`` below the asking sweep's is not current: its rho now is
    the address a run at the sweep's tier would have, which none of its
    entries is at (``_most_thorough``). Each group's tier — the one it read,
    raised to the sweep's when below it — says which answer's inputs its rho
    is (``_by_tier``)."""
    if not entries:
        return Never()
    gate_id = spec.id
    blocked = _NO_SPINE if not now.spine else (
        f"its code cannot be keyed: {code.opaque}" if code.opaque else "")
    groups: dict[str, list[Entry]] = {}
    for entry in entries:
        groups.setdefault(_signature(entry), []).append(entry)
    judged: dict[str, tuple[str, str, Reads | None]] = {}
    level: dict[str, int | None] = {}
    current: set[str] = set()
    matches: list[Entry] = []
    for sig, group in groups.items():
        tier = _read_tier(group[0].reads)
        level[sig] = (now.tier if tier is not None and now.tier is not None
                      and tier < now.tier else tier)
        reads_now, why = _reads_now(group[0].reads or {}, now)
        why = blocked or why
        if (not why and reads_now is not None and reads_now.tier is not None
                and now.tier is not None and reads_now.tier < now.tier):
            reads_now = dataclasses.replace(reads_now, tier=now.tier)
        rho_now = "" if why else rho(gate_id, now.spine, code, reads_now)
        judged[sig] = (rho_now, why, reads_now)
        if rho_now:
            current.add(rho_now)
            if not (group[0].reads or {}).get("opaque"):
                matches.extend(e for e in group if e.rho == rho_now)
    known = frozenset(current)
    rhos = {sig: (found[0], level[sig]) for sig, found in judged.items()}
    if matches:
        thorough = _most_thorough(matches)
        served = max((t for t in (_read_tier(e.reads) for e in thorough) if t is not None),
                     default=None)
        return _by_tier(_fresh_or_conflict(spec, code, thorough, known, order),
                        groups, rhos, now, order, served=served)
    latest = max(entries, key=order)
    rho_now, why, reads_now = judged[_signature(latest)]
    if why:
        state: Stale | Unknown = Unknown(latest, why, "", known)
    elif (latest.reads or {}).get("opaque"):
        opaque = list((latest.reads or {}).get("opaque") or ())
        state = Unknown(latest, "opaque inputs: " + ", ".join(opaque), rho_now, known)
    else:
        state = Stale(latest, _reasons(latest, reads_now, code, now), rho_now, known)
    return _by_tier(state, groups, rhos, now, order)


def _by_tier(state: Fresh | Stale | Unknown, groups: Mapping[str, list[Entry]],
             rhos: Mapping[str, tuple[str, int | None]], now: _Now,
             order: Callable[[Entry], tuple], *, served: int | None = None
             ) -> Fresh | Stale | Unknown:
    """``state`` with the rhos each read signature's tier puts where. ``rhos`` is
    ``{signature: (rho now or "", the tier it is judged at)}`` — the tier it
    read, raised to the asking sweep's when below it; ``None``, never read.

    ``here`` — the rhos of ``current`` at the asking sweep's tier, or of a
    signature that never read ``ctx.tier``: the inputs a run of that sweep is
    made at, and all that a pass or fail there answers (``_superseded``).
    ``input_rho`` — what a crash there is remembered under: ``rho`` when the
    state's own signature is at that tier, else the latest entry's among the
    signatures that are (a current entry first), else ``""`` (no entry was ever
    made on that path). A reader (``now.tier`` ``None``) never runs, so it never
    remembers or forgets: its ``here`` is ``current``, its ``input_rho``
    ``rho``. ``path``, a Fresh state's — the rhos of ``current`` at the tier the
    served entries read (``served``), or of a signature that never read it:
    what a remembered crash must be at to supersede the answer this state
    serves (``_crash_applies``). For a gate that never reads the tier all three
    are what they were before tiers were keyed: ``current``, ``current`` and
    ``rho``.

    What slipped through (review, remembered outcomes by tier): a tier-0 check
    serves a tier-2 entry (``_most_thorough``), so its ``rho`` was the costlier
    path's inputs and its ``current`` held every signature's, and the sweep
    remembered and forgot under those. A cheap-path PASS forgot the crash on the
    costlier path, and ``check --tier 2`` served, cached, the PASS that crash
    had superseded; a cheap-path crash was filed under the tier-2 entry, so an
    edit to an input only that path reads took it out of the current set, and
    the next plain check served the tier-0 PASS, cached, for the path that had
    just crashed at inputs that never moved. *Rejected:* a tier field on each
    remembered record — the signature's rho already names the tier it was read
    at, and one record per rho stays the file's one shape; and keeping
    ``current`` as what a crash supersedes over a Fresh entry — a cheap-path
    crash filed at its own inputs would then stand over the tier-2 answer for
    ``status`` (whose ``current`` holds the tier-0 signature as it was read)
    and not for ``check --tier 2`` (whose raised one is another rho): ``check``
    ready, ``status`` not, at one set of inputs.
    """
    if now.tier is None:
        here = state.current
    else:
        here = frozenset(rho_ for rho_, tier in rhos.values()
                         if rho_ and tier in (None, now.tier))
    sig_of = {entry.name: sig for sig, group in groups.items() for entry in group}
    if now.tier is None or rhos[sig_of[state.entry.name]][1] in (None, now.tier):
        input_rho = state.rho
    else:
        pool = [entry for sig, group in groups.items() for entry in group
                if rhos[sig][1] in (None, now.tier)]
        measured = [entry for entry in pool if entry.rho == rhos[sig_of[entry.name]][0]]
        pick = max(measured or pool, key=order) if pool else None
        input_rho = rhos[sig_of[pick.name]][0] if pick is not None else ""
    if isinstance(state, Fresh):
        path = frozenset(rho_ for rho_, tier in rhos.values()
                         if rho_ and tier in (None, served))
        return dataclasses.replace(state, path=path, here=here, input_rho=input_rho)
    return dataclasses.replace(state, here=here, input_rho=input_rho)


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
              model: Any = None, tier: int | None = None
              ) -> dict[str, Fresh | Stale | Unknown | Never]:
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

    ``tier`` is the asking sweep's (``sweep`` passes its ``max_tier``): an entry
    whose gate read ``ctx.tier`` below it is Stale — ``ctx.tier 0 -> 2`` — and
    one at or above it is served, the highest first (``_most_thorough``).
    ``None``, a reader's, serves the highest tier recorded.
    """
    now = _now_for(root, registry, projection, ledger, anchors=anchors, digests=digests,
                   model=model, tier=tier)
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
    control is never current. A live host's LEDGER is ``_ledger_moved``'s: a
    difference there is settled by re-running the fixture, not by this."""
    reads = control.reads or {}
    opaque = list(reads.get("opaque") or ())
    if opaque:
        return "opaque control inputs: " + ", ".join(opaque)
    for spelled, digest in (reads.get("files") or {}).items():
        where = now.locate(spelled)
        if where is None or _path_digest(where, now.digests) != digest:
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


def _ledger_moved(control: ControlEntry, now: _Now, verified: Mapping[str, Any]) -> str:
    """Why a LIVE-host control's ledger reads are not vouched for by the live
    ledger, or ``""`` when they are — every key re-read now (``_ledger_now``)
    equals what the entry recorded, or equals what a run or re-verification
    of this control recorded in ``controls.json`` (``verified``, this gate's)
    under a fixture closure that is still the one on disk.

    What slipped through (admission review, round 1, repro A): the entry keyed
    ``reads.ledger`` in rho_control and nothing here ever compared it —
    ``_control_moved`` re-read files, listings and host params, and the
    admission ``_Now`` was built with no ledger. A gate taking its limit from
    ``ctx.ledger.claim("C1")``, C1 relaxed from 100 to 500 mm: the fixture's
    400 mm became acceptable, ``gate selftest`` said PASSED its own known-bad,
    and ``check`` served the control cached, exited 0 and ran no control.

    Why not "moved" outright on any difference (the review's first fix): a
    recorded ledger digest is of the ledger the GATE was handed, and a fixture
    may state its own — openmodelica's hands ``Ledger()`` — whose digests never
    equal a live one with claims. Compared against the live ledger alone every
    check of such a project would re-run the control, and every reader would
    call it undemonstrated. Only the fixture can say what it builds from the
    live ledger, so a difference sends ``check`` to re-verify (``_reverify``
    compares the ledger it built, key by key) and a reader, which runs nothing,
    to undemonstrated until one has. The snapshot's closure must be the one on
    disk too: a live ledger vouched for under other fixture code vouches for
    nothing about this code. A KNOWN-GOOD host's ledger is never the live one
    (``_KNOWN_GOOD_BLANK``): what ``context`` builds it from is its code (the
    hint) and the files it opens in the control's window (keyed), so it is not
    compared here. *Rejected* there: re-verifying every known-good control
    that reads the ledger on every check — a fixture run per gate per check to
    catch only an import-time read, which params share and which is not a
    ledger question.
    """
    if control.host != "live":
        return ""
    recorded = (control.reads or {}).get("ledger") or {}
    if not recorded:
        return ""
    live: dict[str, str] = {}
    for key in recorded:
        digest = now.ledger_digest(key)
        if digest is None:
            return f"cannot re-read ledger {key} here, and the control read the live host"
        live[key] = digest
    if live == dict(recorded):
        return ""
    snapshot = verified.get(control.name) if isinstance(verified, Mapping) else None
    if isinstance(snapshot, Mapping) and snapshot.get("ledger") == live \
            and not _snapshot_moved(snapshot, now):
        return ""
    moved = sorted(key for key in recorded if live[key] != recorded[key])
    more = f" (+{len(moved) - 1} more)" if len(moved) > 1 else ""
    return f"ledger {moved[0]} changed{more}"


def _live_ledger(control: ControlEntry, now: _Now) -> dict[str, str] | None:
    """What ``controls.json`` remembers beside a LIVE-host control that read
    the ledger: each key's digest in the live ledger it was just demonstrated
    or re-verified under (``_ledger_moved`` reads it back). ``None`` for any
    other control, or when a key cannot be re-read — then nothing vouches."""
    recorded = (control.reads or {}).get("ledger") or {}
    if control.host != "live" or not recorded:
        return None
    live = {key: now.ledger_digest(key) for key in sorted(recorded)}
    return None if any(v is None for v in live.values()) else live


def _vouched(snapshot: Any, control: ControlEntry, now: _Now) -> Any:
    """``snapshot`` (a fixture-closure snapshot) with ``_live_ledger`` beside it
    when the control has one."""
    live = _live_ledger(control, now)
    if live is None or not isinstance(snapshot, Mapping):
        return snapshot
    return {**snapshot, "ledger": live}


def _fixture_moved(control: ControlEntry, now: _Now) -> list[str]:
    """The files of the fixture's recorded code closure whose bytes moved."""
    return _snapshot_moved(control.fixture, now)


def _snapshot_moved(snapshot: Any, now: _Now) -> list[str]:
    """The files of a fixture-closure snapshot (``{"digest", "files"}``) whose
    bytes are not what it recorded — every one of them when it is no snapshot.
    A ``null`` digest names no bytes (``UNRECORDED_FIXTURE``, or a closure in
    which two versions of one file ran), so it is always moved: compared, it
    equalled the ``None`` a missing file digests to, and a hint that names
    nothing held. ``modelio.NO_BYTES`` — a data file the fixture module opened
    at import and found nothing at — names exactly that, and holds while the
    file still has no bytes."""
    files = (snapshot or {}).get("files") if isinstance(snapshot, Mapping) else None
    if not isinstance(files, Mapping):
        return [UNRECORDED_FIXTURE]
    moved = []
    for spelled, digest in sorted(files.items()):
        where = now.locate(spelled) if digest is not None else None
        if where is None or (now.digests.digest(where) or modelio.NO_BYTES) != digest:
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


def _at_tier(controls: Iterable[ControlEntry], at: int | None) -> bool:
    """Is some control in ``controls`` a demonstration of the path tier ``at``
    picks — one run at ``at``, or one whose run never read ``ctx.tier`` (then
    every tier's path is the same path)? Always, when ``at`` is ``None``."""
    return at is None or any(_read_tier(c.reads) in (None, at) for c in controls)


def _disagree(pool: list[ControlEntry]) -> str:
    """Why controls in ``pool`` that disagree are not admitted: two answers to
    one question, or — when they were run at different tiers — a gate that
    fires on its known-bad input on one tier's path and passes it on another's.
    A gate that can pass a known-bad design is a logger at every tier: nothing
    a claim reads says which path its verdict took."""
    names = ", ".join(sorted(c.name for c in pool))
    if len({_read_tier(c.reads) for c in pool}) > 1:
        return (f"control outcomes differ by ctx.tier ({names}): on one tier's path it "
                f"passes its own known-bad input")
    return f"two control outcomes recorded for identical inputs ({names})"


def _control_order(root: str, gate_id: str) -> Callable[[ControlEntry], tuple]:
    last: dict[str, tuple[str, int]] = {}
    for index, run in enumerate(read_obs(root, gate_id, control=True)):
        last[run.get("entry", "")] = (str(run.get("when") or ""), index)
    return lambda c: (*last.get(c.name, ("", -1)), c.name)


def _admission(now: _Now, spec: Any, fn: Any, held: Mapping[str, Any],
               notes: list | None = None, verified: Mapping[str, Any] | None = None,
               at: int | None = None) -> Admission:
    """§3.8 steps 1-3 and the remembered control failure, from records alone.
    ``verified`` is ``controls.json`` (``_read_verified``): a closure a sweep
    re-verified reads admitted, not pending.

    ``at`` is the tier whose path the counted verdict took — the entry's
    recorded ``ctx.tier`` (``resolve``), or the tier a sweep will run the gate
    at — and a control that read ``ctx.tier`` demonstrates only the path its
    own tier picked: with none current at ``at`` (``_at_tier``) the gate is
    undemonstrated there. Every current control counts toward the decision
    whatever tier it ran at (``_disagree``). What slipped through (review round
    1, ``probe.tier``): rho_control never keyed the tier, so a control shown on
    the tier-0 path was served to ``check --tier 2`` and admitted a costlier
    path that passed its own known-bad input. A remembered control failure is
    held against ``at`` only on that path or the one every tier shares
    (``_control_failures``) — a crash proves nothing, least of all about a path
    it did not take."""
    static, _parts = now.static(spec, fn)
    mine = (verified or {}).get(spec.id) or {}
    # An availability skip of the control proves nothing either way, and is
    # re-evaluated where it is shown: while the tool is missing the GATE reads
    # skipped before admission is asked; once it is here the record is moot.
    # Only a record at THIS static: one at another version is that version's
    # (remembered outcomes, round 1). And only one on the path `at` picks, or
    # the one every tier shares: a control crash on the tier-2 path says
    # nothing about the tier-0 path's control, which fired (review, remembered
    # control failures by tier).
    failures = _control_failures(held, spec.id, static, at)
    if failures:
        return Admission("not-admitted", None, _control_failure(failures[0][1]))
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
        why = _control_moved(control, now) or _ledger_moved(control, now, mine)
        if why:
            moved.append(why)
        else:
            current.append(control)
    if not current:
        return Admission("undemonstrated", max(candidates, key=order),
                         f"control inputs moved: {moved[0]}")
    if not _at_tier(current, at):
        return Admission("undemonstrated", max(current, key=order),
                         f"no control shown on the path ctx.tier {at} picks — run "
                         f"atompipe check --tier {at}")
    # The fixture closure is a HINT (§3.8): an entry whose fixture code is
    # unchanged — or that a sweep re-verified against the code as it is now —
    # was demonstrated on exactly this; one whose fixture code moved may still
    # be (early cutoff), but only re-running the fixture can say, and nothing
    # here runs. Among hint matches, disagreement is the control analogue of two
    # outcomes; without one, every current candidate decides.
    hinted = [c for c in current if _hint_holds(c, now, mine)]
    if not _at_tier(hinted, at):
        hinted = []                  # none shown on `at`'s path is unmoved: pending, as below
    pool = hinted or current
    chosen = max(pool, key=order)
    if len({c.bad for c in pool}) > 1:
        return Admission("not-admitted", chosen, _disagree(pool))
    if chosen.bad == "pass":
        fixture = getattr(spec.negative_control, "fixture", "") or "its fixture"
        return Admission("not-admitted", chosen, f"PASSED its own known-bad fixture {fixture}")
    if hinted:
        return Admission("admitted", chosen)
    files = sorted({path for c in pool for path in _fixture_moved(c, now)})
    return Admission("pending", chosen,
                     f"control inputs moved ({', '.join(files)}); the next check re-verifies")


def admission_state(root: str, spec: Any, fn: Any, *, projection: Any,
                    ledger: Any = None, digests: FileDigests | None = None,
                    anchors: Anchors | None = None) -> Admission:
    """Is ``spec``'s control demonstrated at its current version? From the
    control entries and the remembered control failures alone: it **never runs
    a fixture** (only ``check`` may spend that time).

    ``"admitted"`` — a control entry whose static part (spine, the gate's code,
    its owner's ``selftest/`` walk, the NegativeControl fields) is current, whose
    recorded file, listing and — for a live host — host-param reads are current
    (against ``projection``'s flat params), whose live-host ledger reads are
    vouched for by ``ledger`` (``_ledger_moved``: equal to it, or re-verified
    under it), and whose fixture closure is unchanged. ``"pending"`` — the
    same, but the fixture's code closure moved (the bracket's fixtures import
    its model): it counts, with the note ``control inputs moved (<files>); the
    next check re-verifies``.
    ``"not-admitted"`` — the current control PASSED its known-bad input, or two
    current controls disagree, or the control crashed, was unusable or skipped
    itself at this static part — on any tier's path: this asks at no tier
    (``_control_failures`` at ``at=None``) — remembered under ``control:<gate>``.
    ``"undemonstrated"`` — no current control.

    ``anchors`` defaults to the root's plus the pack ``fn`` came from (as
    ``record_control``'s). A moved fixture closure that a sweep re-verified by
    its values (``CONTROLS_CACHE``) reads admitted; one nothing has re-run yet
    reads pending, which counts.

    ``ledger`` is the live one, as ``check`` hands its gates. Without it a
    live-host control that read the ledger has nothing to be compared against,
    and reads undemonstrated — never admitted on a claim nothing re-read.
    """
    if anchors is None:
        anchors = _default_anchors(root, spec, fn)
    now = _Now(root, projection, ledger, anchors=anchors, digests=digests)
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


def _unqualified(spec: Any, reason: str, *, rho: str = "") -> Verdict:
    """The verdict of an evaluator refused at its version: ``unqualified`` set to
    the refusal's reason, beside ``error="not admitted: <reason>"``. Every
    refusal is minted here — ``resolve`` (a Fresh entry, a stale one, none) and
    the sweep (``_sweep_one``, ``_outranked``) — so ``Verdict.unqualified``, the
    one mark ``claims.compose`` reads as Gap, has one producer. The text is
    unchanged in P2.1: P2.0's fixtures and planted violators key on it, and
    P2.3 rewords it with the qualification words. *Rejected:* the text as the
    mark (a second predicate; a gate could word its own crash into it)."""
    return _synthesized(spec, error=f"not admitted: {reason}", unqualified=str(reason or ""),
                        rho=rho)


def _crash_applies(record: Mapping[str, Any] | None, state: Any) -> bool:
    """Does a remembered crash or self-skip stand at ``state``'s inputs (§3.9)?
    Over a Fresh entry, at a rho current now on the served entry's path
    (``Fresh.path``: the entry's, and every other read signature's recomputed
    that read its tier or none); otherwise at any rho current now, or anywhere
    when nothing better exists (S-68: with no entry at all, "never run" would be
    false). ONE predicate for ``resolve``'s step 2 and the sweep's step 3. They
    were two copies, and the sweep's answered only for Fresh — enough while
    Fresh was all the sweep served. Once it serves a two-outcomes conflict too,
    a copy without the Stale rule would serve the conflict where ``status``
    shows the crash that superseded it.

    Why the path over a Fresh entry and not its rho alone: a crash is
    remembered under the latest entry's signature, which need not be the Fresh
    entry's — a crash at those same inputs, read through another signature,
    stood behind a PASS it had superseded. And a run at the served entry's
    tier clears exactly this set when it measures (``_superseded``: ``here`` is
    ``path`` there), so a crash matched here is one no run at these inputs has
    answered since. Why not every signature's (``current``, the rule until the
    review of remembered outcomes by tier): a crash on the cheap path is not at
    the costlier answer's inputs, and a tier-0 ``check`` serves that answer —
    matched there it stood for ``status``, whose ``current`` holds the tier-0
    signature, and not for ``check --tier 2``, whose raised one is another rho.
    *Rejected:* the entry's rho alone (the first rule) — it needs ``forget`` to
    clear the whole gate to stay loop-free, and that was the hole (remembered
    outcomes, round 1)."""
    if record is None or record["kind"] not in _SUPERSEDING_KINDS:
        return False
    if isinstance(state, Fresh):
        return record["input_rho"] in state.path
    return isinstance(state, Never) or record["input_rho"] == "" \
        or record["input_rho"] in state.current


def _standing(records: Mapping[str, Any] | None, state: Any) -> Mapping[str, Any] | None:
    """The remembered crash or self-skip that stands at ``state``'s inputs, of
    one key's records (``remembered``'s ``{input_rho: record}``), or ``None``.
    Several can stand — a Stale gate's ``""`` and its current rho — and the one
    shown is the newest (``when``), then the one at ``state.rho``, then by rho:
    never by dict order."""
    standing = [record for record in (records or {}).values()
                if _crash_applies(record, state)]
    if not standing:
        return None
    return max(standing, key=lambda r: (r["when"], r["input_rho"] == state.rho,
                                        r["input_rho"]))


def _latest_shown(records: Mapping[str, Any] | None) -> Mapping[str, Any] | None:
    """An unregistered gate's newest crash or self-skip (``when``, then rho), or
    ``None``: with no registration there are no inputs to match, and an
    availability skip is not shown for a gate that never ran."""
    shown = [record for record in (records or {}).values()
             if record["kind"] in _SUPERSEDING_KINDS]
    return max(shown, key=lambda r: (r["when"], r["input_rho"])) if shown else None


def _superseded(state: Any, rho_: str) -> set[str]:
    """The remembered rhos a pass or fail measured at ``rho_`` answers, the gate
    having stood at ``state`` just before the run: its own rho, ``""`` (a
    crash from before the gate had any entry — it has one now), and every rho
    current then at the tier the run took (``state.here``: a signature read
    at the sweep's tier, or raised to it, or one that never read the tier) —
    the run was made at those inputs. Nothing else: a crash at inputs this run
    was not made at is still the last word there (remembered outcomes, round
    1), and neither is a crash on another tier's path (review, remembered
    outcomes by tier: ``state.rho`` and ``state.current`` were cleared here,
    and a tier-0 PASS took the tier-2 crash with them — ``check --tier 2``
    served the PASS that crash had superseded)."""
    return {"", rho_, *(state.here or ())}


def _contradicted(root: str, entry: Entry) -> str:
    """The error a run's ``entry`` resolves to once it is on disk, or ``""``:
    another outcome already recorded at its rho under the same instruments is
    §3.7's two outcomes, ``resolve``'s error (``TWO_OUTCOMES_IS_ERROR``), in
    ``resolve``'s words — every entry at that rho named, sorted, as
    ``_fresh_or_conflict`` names them. Asked of the files the writer asks
    (``_siblings``), so the error and the writer's warning cannot disagree, and
    asked whether or not the run is recorded: ``--no-record`` writes nothing,
    and still must not present one of two answers as the answer. What slipped
    through: ``check --force`` re-ran a gate that reads the environment, wrote
    a FAIL beside its PASS at one rho, and the next plain ``check`` re-ran it
    again and showed whichever answer that run gave; ``status`` FAILed the
    claim, ``check`` said ready and exited 0 (the review).

    An entry with an opaque channel is never a contradiction, as ``_judge``
    never counts one a match: its rho does not name what the channel read (a
    subprocess's own files), so another outcome there is an input that moved
    unseen, not two answers to one question. Without this the first draft
    turned ``t.sub``'s honest FAIL, after the file its child reads changed,
    into an error (``StaleIsNotCurrent``)."""
    if not TWO_OUTCOMES_IS_ERROR or (entry.reads or {}).get("opaque"):
        return ""
    others = _siblings(_gate_dir(root, entry.gate), entry.name, entry.rho, control=False)
    if not any(other.instruments == entry.instruments for other in others):
        return ""
    names = ", ".join(sorted({entry.name, *(other.name for other in others)}))
    return f"{_TWO_OUTCOMES} ({names})"


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


def _resolve_gate(here: _Now, spec: Any, fn: Any, state: Any, *, held: Mapping[str, Any],
                  verified: Mapping[str, Any], notes: list,
                  order: Callable[[Entry], tuple], legacy: Mapping[str, Verdict],
                  availability: Callable[[Any], tuple], model_error: str = "",
                  row_notes: tuple = ()) -> tuple[Verdict | None, Row | None]:
    """``resolve``'s rungs 1-6 for ONE registered gate: ``(verdict, row)``, or
    ``(None, None)`` when nothing applies and the claim reads Open. Extracted
    from ``resolve``'s loop unchanged (P2.2-D8), so the sweep can ask what
    ``resolve`` reads of a gate it does not run — a dependent pruned under its
    prerequisite (``_pruned_row``) — through the same code, rather than a copy
    that drifts (the sweep and the resolver drifted at every place they were
    copied in P1 and P2.0: ``_crash_applies``, ``_outranked``, S-68). The
    caller supplies the gate's state (``_judge``) and the records it was read
    with; this writes nothing (``notes`` is the caller's list)."""
    gid = spec.id
    entry = state.entry

    def when_of(found: Any) -> str:
        return order(found)[0] if found is not None else ""

    # 1. availability
    ok, why = availability(spec)
    if not ok:
        why = why or "its tooling is not available"
        if isinstance(state, Fresh) and state.entry.verdict.get("passed") is False:
            return (_as_spec(entry.to_verdict(), spec),
                    Row(gid, state.state, cached=True, fresh=True, entry=entry,
                        when=when_of(entry), notes=row_notes))
        reason = f"cached pass exists; {why} here" if isinstance(state, Fresh) else why
        return (_synthesized(spec, skipped=True, skip_reason=reason),
                Row(gid, state.state, entry=entry, notes=row_notes))

    # 2. a remembered crash or self-skip
    record = _standing(held.get(gid), state)
    if record is not None:
        extra = (f"supersedes the cached {entry.name}",) if isinstance(state, Fresh) else ()
        return (_as_spec(record["verdict"], spec),
                Row(gid, state.state, entry=entry, when=record["when"],
                    notes=row_notes + (f"remembered {record['kind']}",) + extra))

    # 3. a Fresh entry, under admission
    if isinstance(state, Fresh):
        verdict = _as_spec(entry.to_verdict(), spec)
        at = _read_tier(entry.reads)
        admission = _admission(here, spec, fn, held, notes, verified, at=at)
        when = when_of(entry)
        if admission.state == "not-admitted":
            return (_unqualified(spec, admission.reason, rho=entry.rho),
                    Row(gid, state.state, entry=entry, admission=admission, when=when,
                        notes=row_notes))
        if admission.state == "undemonstrated":
            return (verdict, Row(gid, state.state, cached=True,
                                 stale_reason=_undemonstrated(at), entry=entry,
                                 admission=admission, when=when, notes=row_notes))
        pending = (admission.reason,) if admission.state == "pending" else ()
        notes.extend(f"{gid} — {note}" for note in pending)
        return (verdict, Row(gid, state.state, cached=True, fresh=True, entry=entry,
                             admission=admission, when=when, notes=row_notes + pending))

    # 4. the latest entry, stale — unless the evaluator is refused at its
    # version, which no rerun of the gate answers: its claim reads Gap, not
    # "run check" (P2.1-D5). Asked at the entry's own tier, as step 3 asks.
    if isinstance(state, (Stale, Unknown)):
        when = when_of(entry)
        if isinstance(state, Stale) and state.conflict and TWO_OUTCOMES_IS_ERROR:
            return (_synthesized(spec, error=state.reasons[0], rho=entry.rho),
                    Row(gid, state.state, entry=entry, when=when, notes=row_notes))
        admission = _admission(here, spec, fn, held, notes, verified,
                               at=_read_tier(entry.reads))
        if admission.state == "not-admitted":
            return (_unqualified(spec, admission.reason, rho=entry.rho),
                    Row(gid, state.state, entry=entry, admission=admission, when=when,
                        notes=row_notes))
        if isinstance(state, Stale):
            reason = _stale_text(state.reasons)
        elif state.reason == _NO_MODEL and model_error:
            reason = f"{_NO_MODEL}: {model_error}"
        else:
            reason = state.reason
        return (_as_spec(entry.to_verdict(), spec),
                Row(gid, state.state, cached=True, stale_reason=reason, entry=entry,
                    when=when, notes=row_notes))

    # 5. a ledger verdict from before per-gate tracing
    if gid in legacy:
        return _as_spec(legacy[gid], spec), Row(gid, "legacy", stale_reason=_LEGACY)
    # 6. nothing — unless the evaluator is refused at its version, which is
    # on disk (its control entry PASSED its known-bad input, or its control
    # crashed, remembered under control:<gate>). What slipped through (P2.0
    # review): admission was asked only over a Fresh entry, a refused gate is
    # never cached, so every reader after the `check` that refused it read
    # "never run" — and "pass beside an unrun gate" read pass: `status --json`
    # ready, the claim under PROVEN. Asked at no tier, as `admission_state`.
    admission = _admission(here, spec, fn, held, notes, verified, at=None)
    if admission.state == "not-admitted":
        return (_unqualified(spec, admission.reason),
                Row(gid, state.state, admission=admission, notes=row_notes))
    # undemonstrated, pending or admitted with no entry: no row, the claim reads Open
    return None, None


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
       supersedes — its ``input_rho`` is a rho recomputable now
       (``Fresh.current``) — or, with no Fresh entry, is displayed — its
       ``input_rho`` is ``""`` or a rho recomputable now, or the gate has no
       entry at all (``_standing``: the newest when several do). Invariant 2: a
       crash proves nothing, and neither does the PASS it followed.
    3. A **Fresh** entry, then admission (PD-08, X14): a PASS counts when its
       control is admitted or pending; undemonstrated, it reads stale
       (``control not demonstrated at this version — run atompipe check``, with
       ``--tier <t>`` for an entry that took a costlier tier's path:
       ``_undemonstrated``); not admitted, the refusal (``_unqualified``:
       ``Verdict.unqualified`` set, ``error`` ``not admitted: <why>``). A FAIL
       stays FAIL unless not admitted (then that refusal; it blocks either way).
    4. Otherwise the **latest entry**, stale with its reasons (Unknown with its
       reason; ``model_error`` joins the "model does not load" one) — unless its
       evaluator is not admitted at the entry's tier, then the refusal (P2.1-D5:
       a refusal that read Gap must not read "run check" after a model edit).
       Two outcomes at the current rho: stale, or an error once
       ``TWO_OUTCOMES_IS_ERROR`` is True, before the refusal is asked.
    5. A **legacy** ``ledger.verdicts`` row with no rho: stale, ``recorded
       before per-gate tracing`` (Q1.4). ``store.load`` and the migration drop
       every legacy verdict (D-09), so only a ``Ledger`` a caller built in memory
       still carries one; the rung stays so that one can never read current.
    6. Nothing: the refusal, a ``never`` row, when the evaluator is not admitted
       (admission at no tier — a control entry that PASSED its known-bad input,
       or a remembered control crash); otherwise no row, and the claim reads
       Open. What slipped through (P2.0 review): only rung 3 asked, a gate
       refused at its first check has no entry, and every reader after that
       check read it never run and the pass beside it as pass.

    Then **orphans** — entries, remembered outcomes or legacy rows of gates this
    project does not register — sorted by id, stale ``gate not registered in
    this project`` (tests:H2: an unregistered gate's verdict still reaches the
    page; it never counts).

    7. Last, **prerequisites** (``apply_prerequisites``, P2.2): a gate whose
       prerequisite is not established reads the prerequisite skip — or its own
       crash, which stands, and its own refusal or skip (a missing tool, a
       self-skip), which stand unless the root crashed (``_under_rule``); one
       whose prerequisite is invalidated or unrun keeps its verdict, marked
       stale. Rungs 1-6 are ``_resolve_gate``, one gate at a time, which the
       sweep asks too.

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
        row_notes = tuple(getattr(state, "notes", ()) or ())
        notes.extend(f"{gid} — {note}" for note in row_notes)
        if isinstance(state, Unknown) and state.reason.startswith("opaque inputs: "):
            notes.append(f"{gid} — {state.reason}")
        # (two outcomes need no line of their own: read_entries already wrote one)
        verdict, row = _resolve_gate(here, spec, fn, state, held=held, verified=verified,
                                     notes=notes, order=order, legacy=legacy,
                                     availability=availability, model_error=model_error,
                                     row_notes=row_notes)
        if verdict is not None:
            emit(verdict, row)

    # orphans: what this project's cache and memory hold for gates it does not register
    shown = {key: _latest_shown(records) for key, records in held.items()
             if not key.startswith("control:") and key not in registered}
    orphan_ids = set(orphans) | {key for key, record in shown.items() if record is not None}
    orphan_ids |= {gid for gid in legacy if gid not in registered}
    for gid in sorted(orphan_ids):
        if gid in orphans:
            order = _entry_order(root_abs, gid, times)
            latest = max(orphans[gid], key=order)
            emit(latest.to_verdict(), Row(gid, "orphan", cached=True, stale_reason=_ORPHAN,
                                          entry=latest, when=when_of(latest, order)))
        elif shown.get(gid) is not None:
            record = shown[gid]
            emit(record["verdict"], Row(gid, "orphan", stale_reason=_ORPHAN,
                                        when=record["when"],
                                        notes=(f"remembered {record['kind']}",)))
        else:
            emit(legacy[gid], Row(gid, "legacy", stale_reason=_LEGACY))

    # 7. prerequisites, over the whole resolution, in plan order (P2.2-D8)
    return apply_prerequisites(
        Resolution(verdicts=verdicts_out, stale_gates=frozenset(stale), rows=rows,
                   notes=list(dict.fromkeys(notes)), read_sets=last_read_sets(root_abs),
                   anchors=here.anchors),
        registry)


def _under_rule(spec: Any, own: Verdict | None, unmet: Any) -> Verdict:
    """``own`` — a gate's reading of its own — under a NEGATIVE prerequisite
    root (P2.2-D6): the prerequisite skip (``gates.blocked``), or ``own`` itself
    where it stands. The one copy of the rule: ``apply_prerequisites``, the
    sweep's ``_pruned_row`` and ``gates.run_all``'s default all ask it.

    **A reading of the dependent's own stands only where it is at least as loud
    as the prerequisite skip, and no run of the prerequisite changes it:**

    * its own crash (or two outcomes) — always: invariant 2, a crash is never
      made quieter;
    * its own skip — its tool missing here, or a self-skip at its inputs — and
      its own refusal (unqualified, which reads Gap): only while the root did
      NOT crash. Under a crashed root they give way to the errored
      prerequisite skip, because invariant 10's claim is "as loud as a crash
      when the root crashed";
    * a prerequisite skip this rule already made, saying what the rule says
      now — kept as it is, so a second application moves nothing (``cli
      ._swept`` applies the rule over a view ``resolve`` already ruled on).

    Everything else is replaced: a pass or a fail, current or not — a Fresh
    FAIL served where the tool is missing (R-3, ``_resolve_gate``'s rung 1)
    included — or no verdict at all.

    What slipped through the first rule (review of P2.2): (1) it let any skip
    or refusal of the dependent's own stand, so a crashed guard went quiet
    behind it — the claim read `skipped: <dep> : requires <tool>`, a JUnit
    ``<failure>``, absent from ``last_check.json``'s ``errored``, and after
    the tool was installed the same records read errored; (2) it returned
    ``own`` whenever the gate's tool was missing, whatever ``own`` was, so a
    Fresh FAIL behind a failed guard read Failing on a machine without the tool
    and Skipped on one with it — the number D6 says settles nothing; (3) it
    rebuilt a prerequisite skip it had already made, so every second
    application appended its ``not run:`` note again.

    *Rejected:* replacing every reading (a standing crash would read as the
    prerequisite skip for as long as the root is down); letting the dependent's
    FAIL stand (a number a model the guard says does not apply computed
    settles nothing: D-03, D-04); letting a skip of its own give way under every
    negative root (it is as loud as the prerequisite skip it would become, and
    a missing tool on this machine is as true a reason as a failed guard — the
    claim's composition ranks the prerequisite skip first among Skipped
    readings instead, ``claims.compose``); carrying the root's mark on the
    standing skip (a verdict marked ``blocked_by`` that the rule did not
    produce, against D9's "spine-only, ``gates.blocked``")."""
    from . import gates as _gates
    from .models import PrerequisiteKind
    ruled = _gates.blocked(spec, unmet)
    if own is None:
        return ruled
    if own.blocked_by:
        same = ((own.skip_reason, list(own.blocked_by), str(own.blocked_kind))
                == (ruled.skip_reason, list(ruled.blocked_by), str(ruled.blocked_kind)))
        return own if same else ruled
    refused = bool(getattr(own, "unqualified", ""))
    if own.outcome == "error" and not refused:
        return own
    if str(unmet.kind) == PrerequisiteKind.ERRORED:
        return ruled
    if own.outcome == "skipped" or refused:
        return own
    return ruled


def _marked(row: Row, reason: str) -> Row:
    """A row marked not current under a prerequisite that is not (P2.2-D7):
    ``fresh`` False — no longer "current and counts" — and ``reason`` as its
    stale reason unless it is already stale for one of its own. What slipped
    through the design (critique): it set only the reason, and every JSON
    channel then served ``fresh: true`` beside ``stale_reason: prerequisite …
    invalidated``, and ``status`` counted the gate in "N verdicts current" on
    the line that listed it invalidated."""
    return dataclasses.replace(row, fresh=False, stale_reason=row.stale_reason or reason)


#: The note a row pruned under the rule carries — ``not run: <its skip
#: reason>`` — one per row, the current one (``apply_prerequisites``).
_PRUNED_NOTE = "not run: "


def apply_prerequisites(resolution: Resolution, registry: Any) -> Resolution:
    """``resolution`` under the prerequisite rule — ``resolve``'s rung 7, and
    ``check``'s again over the view it merges with its sweep (``cli._swept``).

    Walks :func:`gates.plan`'s order over every registered gate, reading each
    prerequisite as the resolution has it (``gates.Reading``: its verdict, and
    current unless in ``stale_gates``), and asks ``gates.prerequisite_root``,
    the one decision the sweep asks too:

    * a **negative** root (failed, errored, skipped, unqualified, not
      registered): the gate's own reading becomes the prerequisite skip unless it
      stands (``_under_rule``) — its row ``cached`` and ``fresh`` False, its
      ``entry`` kept (D-04: the entry stays Fresh on disk, and is served again
      the moment the root recovers), a note naming the root, and out of
      ``stale_gates`` (its verdict is the skip, not an invalidated pass);
    * a **not-current** root (invalidated, unrun): a pass or a fail is kept and
      marked (``_marked``: ``prerequisite <root> invalidated: <what moved>`` /
      ``prerequisite <root> unrun``, ``fresh`` False) and joins ``stale_gates``,
      so its claim reads Stale, or what ranks above it. **This amends D-03's
      "neither run nor fresh -> unknown"** (P2.2-D7): every model edit between
      check runs invalidates a guard and its dependents together, and D-03 read
      literally turns every guarded claim Skipped — "no usable verdict" — when
      the true and actionable fact is "inputs moved; run check". In a sweep the
      closure expansion leaves no not-current root but a costlier tier's entry
      served stale. *Rejected:* dropping the dependent's verdict so the claim
      reads Open (a second mechanism, and the reason would lose the root's
      name); leaving the dependent alone (Checked downstream of a prerequisite
      not established on the current inputs: invariant 10).

    **Monotone**: it only ever moves a reading toward not-pass, so applying it
    to a view another producer already ruled on (``_swept``) can only
    downgrade. **Idempotent**: a second application finds every replaced gate's
    skip (``_under_rule`` keeps a prerequisite skip that says what the rule
    says now) and every mark in place, and a row carries one ``not run:`` note,
    the current one. What slipped through the first version (review of P2.2):
    the second application rebuilt every pruned gate's skip and appended its
    note again — ``check``'s view row read ``not run: prerequisite failed:
    t.guard`` twice — while this docstring promised it moved nothing. With no
    ``needs`` registered the resolution is returned as it came, byte for byte
    (C3). A cycle or an inversion planted past the registry raises
    (``gates.plan``). It reads no availability: whether a gate's tool is
    missing here is in its reading already (``_resolve_gate``'s rung 1, the
    sweep's step 1), and the rule decides by the reading (``_under_rule``).
    """
    from . import gates as _gates
    if registry is None:
        return resolution
    order = _gates.plan(registry, registry.specs())
    if not any(spec.needs for spec in order):
        return resolution
    by_gate = {v.gate: v for v in resolution.verdicts}
    rows = dict(resolution.rows)
    stale = set(resolution.stale_gates)
    readings: dict[str, Any] = {}
    changed = False
    for spec in order:
        gid = spec.id
        own = by_gate.get(gid)
        row = rows.get(gid)
        unmet = _gates.prerequisite_root(spec, readings, registry) if spec.needs else None
        if unmet is not None and unmet.negative:
            ruled = _under_rule(spec, own, unmet)
            if ruled is not own:
                base = row if row is not None else Row(gid, "never")
                by_gate[gid] = ruled
                kept = tuple(n for n in base.notes if not str(n).startswith(_PRUNED_NOTE))
                rows[gid] = dataclasses.replace(
                    base, cached=False, fresh=False, stale_reason="",
                    notes=kept + (f"{_PRUNED_NOTE}{ruled.skip_reason}",))
                stale.discard(gid)
                own = ruled
                changed = True
            readings[gid] = _gates.Reading(own, False)
            continue
        if unmet is not None:
            why = unmet.why
            if not why and str(unmet.kind) == "invalidated" and unmet.root in rows:
                why = rows[unmet.root].stale_reason
            if own is not None and own.outcome in ("pass", "fail"):
                marked = _marked(row if row is not None else Row(gid, "fresh"),
                                 _gates.mark_reason(unmet._replace(why=why)))
                if marked != row or gid not in stale:      # a second application: in place
                    rows[gid] = marked
                    stale.add(gid)
                    changed = True
            readings[gid] = _gates.Reading(own, False, unmet.root, str(unmet.kind), why)
            continue
        readings[gid] = _gates.Reading(own, own is not None and gid not in stale,
                                       why=row.stale_reason if row is not None else "")
    if not changed:
        return resolution
    registered = set(registry.ids())
    verdicts_out = [by_gate[gid] for gid in registry.ids() if gid in by_gate]
    verdicts_out += [v for v in resolution.verdicts if v.gate not in registered]
    return dataclasses.replace(resolution, verdicts=verdicts_out, rows=rows,
                               stale_gates=frozenset(stale))


# =========================================================================== #
# part four: admission at its current version, the sweep, last_check
# =========================================================================== #
#: Where a sweep remembers the controls it re-verified by their values:
#: ``{gate: {control entry name: {"digest", "files": {path: sha}}}}`` — the
#: fixture closure as it was when the values last matched — and, for a
#: live-host control that read the ledger, ``"ledger": {key: digest}``: the
#: live ledger that run or re-verification was made under (``_ledger_moved``;
#: admission review, round 1, A: a fixture may hand its gate a ledger of its
#: own, whose recorded digests no live ledger ever equals). Untracked (it names
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


#: What ``known_good.context`` is handed in place of the host's own: no params,
#: an empty ledger, no ``extra``, no model — so only where the run lives
#: (``root``, ``out_dir``, ``tier``) and the log sink come through. What slipped
#: through (admission review, round 1): it was handed a COPY of the live host,
#: and the entry says ``host: "known-good"``, whose host reads are never keyed.
#: A ``context`` that replaced the ledger and ``extra`` but kept ``ctx.params``
#: made the known-good design the live one, so the literal identity fixture
#: "fired" while the live shelf failed at 150 mm, and after a model edit to 80
#: mm ``check`` served that control ``cached``, exited 0, and ``report`` put the
#: claim under PROVEN — S-07 back, and nothing keyed it. *Rejected:* keying the
#: host reads of a known-good control after all (filing it ``live`` whenever it
#: read any) — every bracket fixture reads the known-good params through its
#: host, so every Config edit would re-run all six controls and write six
#: tracked files, the whole-value dependency D-27 exists to cut; and a
#: known-good design that depends on the live one is not a known-good design,
#: so the spine does not hand it one to depend on. *Rejected:* keeping the live
#: model: a ``context`` that projected ``ctx.model`` would be the same hole by
#: another field.
_KNOWN_GOOD_BLANK = ("params", "ledger", "extra", "model")


def _known_good(root: str, ctx: Any, trace: GateTrace | None = None) -> tuple[Any, Any] | None:
    """``(the known-good context, the module's code closure)``, or ``None``.

    ``context`` is handed ``ctx`` with ``_KNOWN_GOOD_BLANK`` emptied, and runs
    inside ``trace``'s window when one is given (the control's), so a file it
    opens is an input of the control like any file its fixture opens. The
    module itself loads outside it, as a fixture module does: what it runs at
    import is its code closure, the lookup hint (the bracket's loads the model),
    and what that code reads at import is in the closure's ``data`` (admission
    review, round 1, D: a known-good span read from a JSON file at import went
    80 -> 15, the five-fold fixture built 75 mm, and the hint held).
    """
    module = _known_good_module(root)
    make = getattr(module, "context", None) if module is not None else None
    if not callable(make):
        return None
    # A fresh host: `context` is project code, and the one the sweep holds is
    # the one every later gate reads. Nothing of the live design is in it.
    names = {f.name for f in dataclasses.fields(ctx)}
    blank: dict[str, Any] = {"params": {}, "ledger": Ledger(), "extra": {}, "model": None}
    handed = dataclasses.replace(ctx, **{k: blank[k] for k in _KNOWN_GOOD_BLANK if k in names})
    try:
        # Its module-level memos emptied first, as a gate's and a fixture's
        # are (`modelio.clear_caches`): every control that runs it reads its
        # files itself, into that control's trace.
        modelio.clear_caches(make)
        with tracing(trace) if trace is not None else contextlib.nullcontext():
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
    closure); ``context`` receives ``ctx`` with no params, an empty ledger, no
    ``extra`` and no model (``_KNOWN_GOOD_BLANK``) — ``root``, ``out_dir`` and
    ``tier`` are all it has to build from besides its own code and files, so
    the design cannot be the live one passed through. Raises ``AtompipeError``
    when the module does not import, ``context`` raises, or it returns something
    other than a context of ``ctx``'s type.
    """
    found = _known_good(root, ctx)
    return None if found is None else found[0]


def _control_host(root: str, spec: Any, fn: Any, host_ctx: Any,
                  trace: GateTrace | None = None) -> tuple[Any, str, Any]:
    """``(the context a control's fixture is handed, "known-good" | "live", the
    known-good module's code closure or None)``. Each control gets a memo of its
    own: a known-bad mesh loaded into the sweep's memo is one sweep's accident
    away from a real gate's read. ``trace`` is the control's: the known-good
    ``context`` runs inside its window, so what it opens is keyed with the
    rest of the control's reads — and the sweep's tier is handed as a
    ``TierRead`` on it, so a read of it by ``context``, the fixture or the gate
    on what they pass through is an input of the control (``traced_context``
    wraps nothing on a control's views: a tier the fixture sets is its own)."""
    names = {f.name for f in dataclasses.fields(host_ctx)}
    if "memo" in names:
        host_ctx = dataclasses.replace(host_ctx, memo=SweepMemo())
    tier = getattr(host_ctx, "tier", None)
    if (trace is not None and "tier" in names and type(tier) is not TierRead
            and isinstance(tier, int) and not isinstance(tier, bool)):
        host_ctx = dataclasses.replace(host_ctx, tier=TierRead(tier, trace))
    if _pack_dir_of(fn):
        return host_ctx, "live", None
    found = _known_good(root, host_ctx, trace)
    if found is None:
        return host_ctx, "live", None
    return found[0], "known-good", found[1]


def _add_closure(trace: GateTrace, closure: Any) -> None:
    """Fold the known-good module's closure into the fixture's recorded code:
    the control's lookup hint must move when the known-good design's code does
    (the bracket's model sits in both). A fixture whose own code no loader
    recorded is left unrecorded (``UNRECORDED_FIXTURE``): merged, the
    known-good module's files alone would stand as the whole hint, and it would
    hold while the fixture's code moved — admission review, round 1, C, by
    another door."""
    own = trace.fixture_code
    if not isinstance(closure, modelio.CodeClosure) \
            or not isinstance(own, modelio.CodeClosure):
        return
    files = dict(own.files)
    for path, sha in closure.files:
        files.setdefault(path, sha)
    data = dict(own.data)
    for path, sha in closure.data:
        data.setdefault(path, sha)
    trace.fixture_code = dataclasses.replace(own, files=tuple(sorted(files.items())),
                                             data=tuple(sorted(data.items())))


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
    ledger = snapshot.get("ledger", {}) if isinstance(snapshot, dict) else None
    return (isinstance(snapshot, dict) and isinstance(snapshot.get("digest"), str)
            and isinstance(snapshot.get("files"), dict)
            and all(isinstance(k, str) and (v is None or isinstance(v, str))
                    for k, v in snapshot["files"].items())
            and isinstance(ledger, dict)
            and all(isinstance(k, str) and isinstance(v, str) for k, v in ledger.items()))


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
    #: A reader's ``_Now`` (no tier), made by ``_reader_state`` on the first gate
    #: a sweep prunes under a prerequisite, and only then.
    reader: Any = None


def _session(root: str, host_ctx: Any, *, projection: Any, anchors: Anchors,
             digests: FileDigests, record: bool, when: str, out_dir: str = "",
             tier: int | None = None) -> _Session:
    base = os.path.abspath(root)
    # The ledger a live-host fixture is handed is the host's own, so it is what
    # a control's ledger reads are vouched for against (`_ledger_moved`). It was
    # `None` here, and nothing compared them (admission review, round 1, A).
    # `tier` is the tier its controls run at: the sweep's, else the host's.
    if tier is None:
        tier = _plain_tier(getattr(host_ctx, "tier", None))
    now = _Now(base, projection, getattr(host_ctx, "ledger", None), anchors=anchors,
               digests=digests,
               tier=tier if isinstance(tier, int) and not isinstance(tier, bool) else None)
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
        return Admission("not-admitted", chosen, _disagree(pool), **flags)
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


def _unvouched(built: Any, given: Any, trace: GateTrace, memo_was: Any = None) -> str:
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

    And a fixture that hands its gate a ``model`` or a ``memo`` other than the
    one it was handed. Neither is keyed by a control entry: ``ctx.model is
    None`` uses nothing (``ModelProxy`` records a USE, and a gate that used it
    is opaque), and the memo is shared by reference, never traced. What slipped
    through (admission review, round 2, ``r3``'s other door): a fixture edited
    to hand ``model=None``, or a memo of its own, with the span still 400 —
    every value an entry keys equal — was re-verified, and a gate that trusts
    either passed its own known-bad input while ``check`` admitted it.
    Compared by IDENTITY, the model through its proxy (``_proxied``): the one
    passed through is the one the fixture was handed. The memo is the one it
    was handed AND holds what it held then, entry for entry (``memo_was``, a
    shallow copy of its entries taken before the fixture ran): the same memo
    filled in place hands the gate as much as a new one. A gate can no longer
    read what a memo holds except through ``load_file`` (``SweepMemo``), and a
    fixture can no longer fill one except through its private slot, so what
    is left here is ``is None`` — and a fixture that went looking. No fixture
    today touches the memo, so this costs nothing. *Rejected:* digesting the
    memo's contents — its entries hold loaders and parsed meshes no digest
    names.
    """
    if trace.files_written:
        return "the fixture wrote files its gate would read"
    if trace.opaque:
        return "the fixture used an input no trace can key"
    for name in ("root", "out_dir", "tier"):
        # Unwrapped without a read: this comparison is the spine's, and a
        # ``TierRead`` compared here would land on the control's trace.
        if _plain_tier(getattr(built, name, None)) != _plain_tier(getattr(given, name, None)):
            return f"the fixture moved ctx.{name}"
    if _proxied(getattr(built, "model", None)) is not _proxied(getattr(given, "model", None)):
        return "the fixture handed its gate a ctx.model it was not handed"
    memo = getattr(built, "memo", None)
    if memo is not getattr(given, "memo", None):
        return "the fixture handed its gate a ctx.memo it was not handed"
    entries = memo_entries(memo)
    if entries is not None and (not isinstance(memo_was, dict)
                                or dict.keys(entries) != dict.keys(memo_was)
                                or any(dict.__getitem__(entries, key) is not value
                                       for key, value in memo_was.items())):
        return "the fixture changed the ctx.memo it hands its gate"
    extra_built, opaque_built = _digest(dict(getattr(built, "extra", None) or {}))
    extra_given, _ = _digest(dict(getattr(given, "extra", None) or {}))
    if opaque_built or extra_built != extra_given:
        return "the fixture handed its gate ctx.extra, which no control entry keys"
    return ""


def _replay_read(view: "ParamTrace", path: tuple, recorded: str) -> bool:
    """Read ``path`` through ``view`` as a gate reads it — what ``recorded``
    (a control entry's ``reads.params`` digest there) says it did: a presence
    test, a leaf value, or a whole level in bulk — so every host view the read
    passes through records it where a full run would (``ParamTrace._record``).
    ``False`` when ``path`` cannot be read that way here: a level on the way
    that is missing or not a dict, which the gate could not have walked
    either — then nothing vouches (``_param_at`` calls such a miss ``ABSENT``
    and would have matched it)."""
    node: Any = view
    for depth, part in enumerate(path):
        last = depth == len(path) - 1
        if last and recorded in (PRESENT, ABSENT):
            part in node                                # the read is the point
            return True
        if not dict.__contains__(node, part):
            return False
        value = dict.__getitem__(node, part)
        if not isinstance(value, dict):
            if not last:
                return False
            node[part]                                  # the read is the point
            return True
        node = node[part]
    len(node)                                           # a level read whole
    return True


def _host_reads_with_gate(built: Any, rows: Iterable, trace: GateTrace) -> dict | None:
    """``{path: digest}``: every host read a full run of this control would
    record — the fixture's own, already on ``trace``, and the ones its GATE
    would make reading ``rows`` (the candidate entry's ``reads.params``) on
    ``built`` — or ``None`` when a row cannot be read so, or a read is not
    JSON (a full run would be opaque, and never current). ``trace`` is left as
    it was: each candidate's rows are replayed on their own.

    Why the gate's half: a host view the fixture hands back unchanged — the
    literal identity fixture, ``return ctx`` — is read by the GATE, and a
    fixture-only run calls no gate. Its reads are the host reads the fixture
    made possible, recorded by nothing (admission review, round 2, ``r3i``).
    The replay walks through the very views a full run's gate would, so a
    path the fixture wrote itself (``_HostLink.covers``), a level it copied
    out of the host (``dict(ctx.params)``: child views keep their link) and
    one it built from nothing each record exactly what they would there."""
    saved = (dict(trace.host_reads), set(trace._host_whole), set(trace.opaque))
    try:
        params = getattr(built, "params", None)
        view = ParamTrace({} if params is None else params,
                          GateTrace(kind="control", anchors=trace.anchors), readonly=True)
        for row in rows:
            if not _replay_read(view, tuple(row[0]), row[1]):
                return None
        if trace.opaque != saved[2]:
            return None
        return dict(trace.host_reads)
    except (TypeError, ValueError, KeyError):        # an unhashable key, a params that is no dict
        return None
    finally:
        trace.host_reads.clear()
        trace.host_reads.update(saved[0])
        trace._host_whole.clear()
        trace._host_whole.update(saved[1])
        trace.opaque.clear()
        trace.opaque.update(saved[2])


def _values_match(control: ControlEntry, built: Any, fixture_reads: Reads,
                  anchors: Anchors, *, host: str, trace: GateTrace) -> bool:
    """§3.8 step 4: does the context the fixture built now hand the gate what
    ``control`` recorded it read — every param path by digest (``ABSENT`` for a
    miss), every ledger read — while the fixture read no file the entry does
    not already key? ``host`` is what the fixture was handed this time
    (``_control_host``); ``trace`` the fixture-only run's.

    On a LIVE host, also: is every read of the live design the control would
    make now keyed by the entry — each host param read (the fixture's, and its
    gate's through what the fixture handed back: ``_host_reads_with_gate``) in
    ``reads.host`` at the same digest, and each ledger key the fixture read in
    ``reads.ledger``? Those are what ``_control_moved`` and ``_ledger_moved``
    watch; a read outside them is one the live design can move with nothing
    looking. What slipped through (admission review, round 2, ``r3``): only
    the files, listings, tier and the values built were compared. A sealed
    fixture's entry keyed no host read; edited to five times the LIVE span
    (80 mm: still 400) it was vouched for, ``controls.json`` remembered the
    closure, and at span 15 — the fixture building 75 mm, which the gate
    accepts — ``check`` served the control, exited 0 and put C1 under PROVEN,
    while ``gate selftest`` said PASSED its own known-bad. The literal
    identity fixture at span 400 (``r3i``) and four times the live C1 limit
    (``r3l``) did the same. Such a fixture now misses, the control runs, and
    the entry it files keys what it read.

    Equal digests for the host reads, keys only for the ledger: a current
    candidate's host reads equal the live params already (``_control_moved``),
    so the digest is a consistency check; its ledger reads are vouched for by
    ``controls.json``'s live snapshot, which a match writes (``_vouched``).
    *Rejected:* refusing to re-verify any live-host fixture that reads the
    host — openmodelica's shape (``dict(ctx.params)``, one value changed, its
    own ledger) re-verifies on a claim edit today, and must keep doing so.
    """
    recorded = control.reads or {}
    if control.host != host:
        return False
    if not set(fixture_reads.files) <= set(recorded.get("files") or {}):
        return False
    # The tier, like a file: a fixture that reads it now where the entry keyed
    # none, or a gate that read another tier than the one it would be handed,
    # is not vouched for by values that happen to match.
    recorded_tier = _read_tier(recorded)
    if fixture_reads.tier is not None and fixture_reads.tier != recorded_tier:
        return False
    if recorded_tier is not None and recorded_tier != _plain_tier(getattr(built, "tier", None)):
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
    if host != "live":
        # A known-good host's reads are its selftest files' (the static walk)
        # and the files `context` opened (keyed above): never the live design.
        return True
    if not set(fixture_reads.ledger) <= set(recorded.get("ledger") or {}):
        return False
    reads = _host_reads_with_gate(built, recorded.get("params") or (), trace)
    if reads is None:
        return False
    keyed = {_canonical_json(row[0]): row[1] for row in recorded.get("host") or ()}
    return all(keyed.get(_canonical_json(_clean(_json_path(path)))) == digest
               for path, digest in reads.items())


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
    trace = GateTrace(kind="control", anchors=s.anchors)
    try:
        handed, host, closure = _control_host(s.root, spec, fn, host_ctx, trace)
    except AtompipeError:
        return None
    entries = memo_entries(getattr(handed, "memo", None))
    memo_was = dict(dict.items(entries)) if entries is not None else None
    try:
        built = _gates.run_fixture(spec, fn, handed, trace=trace, out_dir=out_dir)
    except AtompipeError:
        return None
    _add_closure(trace, closure)
    if _unvouched(built, dataclasses.replace(handed, out_dir=out_dir), trace, memo_was):
        return None
    _static_digest, parts = s.now.static(spec, fn)
    owner = _owner_dir(fn, s.root)
    static_files = [os.path.join(owner, *rel.split("/")) for rel in parts["selftest"]["files"]]
    fixture_reads = Reads.from_trace(trace, anchors=s.anchors, digests=s.digests,
                                     static=static_files)
    if fixture_reads.opaque or trace.model_used:
        return None
    matches = [c for c in current
               if _values_match(c, built, fixture_reads, s.anchors, host=host, trace=trace)]
    if not matches:
        return None
    if s.record:
        snapshot = _fixture_part(trace, s.anchors)
        for control in matches:
            s.verified_out.setdefault(spec.id, {})[control.name] = _vouched(snapshot, control,
                                                                           s.now)
    return _decide(matches, spec, order, reverified=True)


def _hold_control_failure(s: _Session, gate_id: str, key: str, verdict: Verdict,
                          kind: str) -> None:
    """Remember a failed control run at ``key`` (``_control_key``) — on disk
    unless ``record=False`` — and in the session's ``held``, so what the rest
    of the sweep reads (``_outranked``, a note) is what the next reader will."""
    if s.record:
        remember(s.root, f"control:{gate_id}", verdict, input_rho=key, kind=kind, when=s.when)
    s.held.setdefault(f"control:{gate_id}", {})[key] = {
        "input_rho": key, "kind": kind, "verdict": verdict, "when": str(s.when or "")}


def _release_control_failures(s: _Session, gate_id: str, keys: Iterable[str]) -> None:
    """Forget the control failures at ``keys`` (``_answered_controls``): on
    disk unless ``record=False``, and in the session's ``held``."""
    keys = set(keys)
    if s.record:
        forget(s.root, f"control:{gate_id}", keys)
    records = s.held.get(f"control:{gate_id}")
    if records:
        for key in keys:
            records.pop(key, None)
        if not records:
            del s.held[f"control:{gate_id}"]


def _run_control(s: _Session, spec: Any, fn: Any, host_ctx: Any, *, force: bool) -> Admission:
    """§3.8 steps 5-6: run the control — fixture and gate, traced, in its own
    emptied ``out_dir`` — and file it: a measurement as a control entry, a
    crash, an unusable fixture or a self-skip remembered under
    ``control:<gate>``, on the path it failed on (``_control_key``). Under
    ``record=False`` it is judged exactly as it would be filed, and nothing is
    written. A control entry answers the failures on the path it ran — this
    sweep's tier, whether or not it read it — and on the shared one, and no
    other (``_answered_controls``)."""
    from . import gates as _gates
    gid = spec.id
    out_dir = _fresh_control_dir(s.root, gid, s.out_dir)
    # Opened before the known-good design is built: `context` runs inside it.
    trace = GateTrace(kind="control", anchors=s.anchors)
    try:
        handed, host, closure = _control_host(s.root, spec, fn, host_ctx, trace)
    except AtompipeError as exc:
        held = Verdict(gate=f"{gid}#selftest", passed=False, tier=Tier(int(spec.tier)),
                       pack=spec.pack or "", error=str(exc).splitlines()[0] if str(exc) else
                       "negative control unusable")
        _hold_control_failure(s, gid, _control_key(s.now.static(spec, fn)[0],
                                                   _trace_tier(trace)), held, "error")
        return Admission("not-admitted", None, _control_failure({"verdict": held,
                                                                 "kind": "error"}),
                         executed=True)
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
        _hold_control_failure(s, gid, built.failure_key, built.held, built.kind)
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
    # Written first: a write that raises must leave the failure it would answer.
    _release_control_failures(s, gid, _answered_controls(entry.static, built.tier, s.now.tier))
    if s.record:
        # The entry on disk keeps the fixture hint it was FIRST written with
        # (same inputs, same outcome: "exists"); the closure it was just
        # demonstrated under is remembered beside it.
        s.verified_out.setdefault(gid, {})[entry.name] = _vouched(entry.fixture, entry, s.now)
    if clash:
        reason = (f"control outcome differs from its cached entry ({', '.join(clash)})"
                  if force else f"two control outcomes recorded for identical inputs "
                                f"({', '.join(clash + [entry.name])})")
        return Admission("not-admitted", entry, reason, executed=True)
    if entry.bad == "pass":
        return Admission("not-admitted", entry, _passed_known_bad(spec), executed=True)
    return Admission("admitted", entry, "", executed=True)


def _admit(s: _Session, spec: Any, fn: Any, host_ctx: Any, *, may_run: bool,
           force: bool, at: int | None = None) -> Admission:
    """§3.8 steps 1-6 for one gate (see ``admission``). ``at`` is the tier
    whose path the counted verdict takes (``_admission``): with no current
    control shown on that path the control runs — at the session's tier, which
    is the one a caller that may run passes as ``at``. A remembered control
    failure on that path or the shared one (``_control_failures``) runs it
    again; one on a path the session does not run is the answer, and nothing
    runs."""
    from . import gates as _gates
    if not may_run:
        return _admission(s.now, spec, fn, s.held, s.notes, s.verified, at=at)
    ok, _why = _gates.availability(spec)
    if not ok:
        # A control runs its gate: where the gate cannot run, neither can it.
        return _admission(s.now, spec, fn, s.held, s.notes, s.verified, at=at)
    gid = spec.id
    order = _control_order(s.root, gid)
    static, _parts = s.now.static(spec, fn)
    # Read under `force` too — only to hold a forced run's outcome against the
    # other tiers' (`_other_tiers`); what it ignores is noted only when it is
    # the cache being consulted, as before.
    try:
        controls = read_controls(s.root, gid, problems=None if force else s.notes)
    except AtompipeError as exc:
        if not force:
            s.notes.append(f"{gid}: {exc}")
        controls = []
    current = [c for c in controls if c.static == static and not _control_moved(c, s.now)]
    failures = _control_failures(s.held, gid, static, at)
    # A failure on a path this sweep does not run — only at `at=None`, a served
    # verdict that never read the tier, against which every path's failure
    # stands — is answered by no run here: nothing runs, as sweep step 1b reads
    # a gate's crash on a costlier path, and a note names the check that runs
    # it. *Rejected:* running the control at this sweep's tier anyway — it
    # would re-run on every check to reach the same refusal.
    beyond = [(tier, record) for tier, record in failures
              if tier is not None and tier != s.now.tier]
    if beyond:
        tier, record = beyond[0]
        s.notes.append(f"{gid}: the control {record['kind']} remembered on the tier-{tier} "
                       f"path stands, and a check at tier {s.now.tier} does not run that "
                       f"path — run atompipe check --tier {tier}")
        if not force:
            return Admission("not-admitted", None, _control_failure(failures[0][1]))
    if not force:
        # A control that failed at this static, on this path or the shared one,
        # supersedes whatever entry it followed, like a gate's crash (§3.9): run
        # it again, never serve it.
        if not failures and current and _at_tier(current, at):
            mine = s.verified.get(gid) or {}
            # A live ledger that differs from what a control recorded is
            # not a miss yet: only its fixture can say whether it builds
            # anything else from it (`_ledger_moved`), and `_reverify`
            # compares the ledger it built key by key.
            vouched = [c for c in current if not _ledger_moved(c, s.now, mine)]
            hinted = [c for c in vouched if _hint_holds(c, s.now, mine)]
            # Settled by the hints only when one of them was shown on the path
            # `at` picks; else the fixture decides, as for a moved closure.
            if hinted and _at_tier(hinted, at):
                return _decide(hinted, spec, order)
            found = _reverify(s, spec, fn, host_ctx, current, order)
            if found is not None:
                return _other_tiers(found, current, s, spec)
    found = _other_tiers(_run_control(s, spec, fn, host_ctx, force=force), current, s, spec)
    if beyond and found.state != "not-admitted":
        # `--force` ran it on this sweep's path, which answers only that path.
        return Admission("not-admitted", found.entry, _control_failure(beyond[0][1]),
                         executed=found.executed, reverified=found.reverified)
    return found


def _other_tiers(found: Admission, current: list, s: _Session, spec: Any) -> Admission:
    """``found``, unless a current control shown on ANOTHER tier's path
    disagrees with it — then not admitted, as a reader will judge the same
    pool (``_admission``: every hinted control counts, whatever its tier).
    What would slip through otherwise: a tier-0 control that fired kept a
    gate whose tier-2 path passes its known-bad input admitted in the sweep
    that found it, while ``status`` refused it."""
    entry = found.entry
    if found.state != "admitted" or entry is None:
        return found
    mine = s.verified.get(spec.id) or {}
    others = [c for c in current
              if c.name != entry.name and _read_tier(c.reads) != _read_tier(entry.reads)
              and not _ledger_moved(c, s.now, mine) and _hint_holds(c, s.now, mine)]
    pool = [entry, *others]
    if len({c.bad for c in pool}) > 1:
        return Admission("not-admitted", entry, _disagree(pool), executed=found.executed,
                         reverified=found.reverified)
    return found


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
       candidate's recorded reads (params by digest, ledger reads) — and, on a
       live host, every read of the live design it would make now is one the
       candidate keys (``_values_match``). A match
       settles it as 3 does, with ``reverified=True``: the gate is not called and
       no tracked file is written (``controls.json`` remembers the closure). This
       is how a model edit that moves every fixture's code, but no control
       value, costs one fixture run per gate and nothing else — and how one that
       DOES move a value (S-19's model-code half) misses.
    5. **Miss.** The control runs — fixture and gate — and is filed (unless
       ``record=False``): ``bad: "fail"`` admitted reject-only; ``bad: "pass"``
       not admitted (``PASSED its own known-bad fixture <ref>``). A crash, an
       unusable fixture or a self-skip with the tools present is remembered
       under ``control:<gate>`` at the current static and the path it failed on
       (``_control_key``) and not admitted (``control <kind>: <why>``); a
       remembered one at this static, on this path or the shared one, is
       re-run, never served.
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
    found = _admit(s, spec, fn, host_ctx, may_run=may_run, force=force, at=s.now.tier)
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
    refusal; ``stale_reason`` — why a row the sweep served is not current, as
    ``resolve`` says it: one kind, a costlier tier's entry whose path no control
    current now shows, which this sweep's ceiling cannot run (``_undemonstrated``;
    ``""`` on every other row, which is current by construction); ``rho``;
    ``admission`` — how the
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
    or Unknown; ``notes`` — writer warnings and ignored entries, which ``check``
    prints (they were collected and never shown: the writer's "two outcomes
    recorded for identical inputs" reached nobody). ``order`` — the gate ids in
    the order they RAN (``gates.plan``: each gate after its prerequisites),
    which ``rows`` does not keep: rows stay in registration order, so ``check
    --json``, JUnit and every tie-break that reads them are unmoved by an edge
    (P2.2-D4); P4's sequencing reads this one. The rest is how
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
    order: list = field(default_factory=list)


def _filtered(only: Any) -> bool:
    """Did ``only`` name anything (``gates._selected``'s reading of it)?"""
    if only is None:
        return False
    patterns = [only] if isinstance(only, str) else [str(o) for o in only]
    return any(p.strip() for p in patterns)


def _outranked(s: _Session, spec: Any, fn: Any, run_ctx: Any, code: CodeRef, entry: Entry,
               verdict: Verdict, judged: Admission, *, held: Mapping[str, Any]
               ) -> SweepRow | None:
    """The row a measured run leaves when the records, with its ``entry`` filed,
    resolve to another answer — or ``None`` when the run's own is the answer.

    The gate is re-judged at the sweep's tier over its entries on disk plus
    ``entry`` (in memory under ``--no-record``, where nothing reaches the disk):
    ``_judge``'s rule, so ``_most_thorough`` decides between tiers exactly as it
    does when this sweep serves the cache and when ``resolve`` answers every
    reader. Asked only after a run over a current answer (a Fresh entry, or two
    outcomes) — ``--force``, or a crash that superseded it; any other run makes
    the only current entry, and a clash at its own rho is ``_contradicted``'s.

    ``held`` is the gate's remembered outcomes as the run leaves them — what it
    answered forgotten, in memory under ``--no-record`` too. A crash still
    standing over the Fresh entry the records resolve to — on a costlier tier's
    path, which this run was not made on (``_superseded``) — is the row, as
    ``resolve``'s step 2 reads it. What slipped through (review, remembered
    outcomes by tier): ``check --force`` at tier 0 re-ran the cheap path over a
    tier-2 crash, and its PASS stood as the row — ``check`` ready — while every
    reader showed the crash.

    Then: a Fresh entry at other inputs with another outcome — or with the same
    outcome, when its own tier's admission does not count — is served as step 3
    serves one, under its own tier's admission (records alone above this
    sweep's tier, as step 2 judges a costlier entry — Q1.6), costed with this
    run's time: undemonstrated there, its verdict stale (``_undemonstrated``);
    not admitted, that error. Two outcomes are ``resolve``'s error. A note says
    why the run's own answer is not the row.

    What slipped through (review, ``check --force``): the forced run re-ran a
    gate that reads ``ctx.tier`` on its tier-0 path, got a PASS at the tier-0
    rho, and ``_swept`` laid that row over the tier-2 FAIL every reader served —
    ``check --force --junit``, CI's invocation, exited 0 with a green report and
    ``last_check.json`` saying pass, while ``status``, ``report`` and the site
    showed the claim FAIL. *Rejected:* keeping the resolver's verdict in
    ``_swept`` whenever it serves a higher-tier entry — the JUnit testcases and
    the ``--json`` rows are the sweep's rows, not the resolution, and would still
    have shown the PASS; and skipping the cheap-path run under ``--force`` — R-9
    re-proves every selected gate, and its entry is what the gate said.
    """
    gid = spec.id
    found = [e for e in _gate_entries(s.root, gid, []) if e.name != entry.name] + [entry]
    obs = _obs_names(s.root, [gid])
    times = _commit_times(s.root, found if len(found) > 1 else [],
                          lambda e: (e.gate, e.name) in obs)
    after = _judge(spec, code, found, s.now, _entry_order(s.root, gid, times))
    cost = {"duration_s": verdict.duration_s, "cpu_s": verdict.cpu_s}
    ran = f"{gid}: ran at tier {s.now.tier} ({verdict.outcome.upper()})"
    if isinstance(after, Stale) and after.conflict and TWO_OUTCOMES_IS_ERROR:
        s.notes.append(f"{ran}; the entries at these inputs are {after.reasons[0]}")
        refused = dataclasses.replace(_synthesized(spec, error=after.reasons[0],
                                                   rho=after.rho), **cost)
        return SweepRow(refused, executed=True, rho=after.rho, admission=judged)
    record = _standing(held, after) if isinstance(after, Fresh) else None
    if record is not None:
        tier = _read_tier(after.entry.reads)
        where, settle = ((f"on the tier-{tier} path ", f" — run atompipe check --tier {tier}")
                         if tier is not None else ("", ""))
        s.notes.append(f"{ran}; the {record['kind']} remembered {where}at these inputs "
                       f"supersedes the entry {after.entry.name}, and it stands{settle}")
        crashed = _as_spec(record["verdict"], spec)
        return SweepRow(dataclasses.replace(crashed, **cost), executed=True, rho=crashed.rho,
                        admission=judged)
    if not isinstance(after, Fresh) or after.entry.rho == entry.rho:
        return None
    served = after.entry
    tier = _read_tier(served.reads)
    above = tier is not None and tier != s.now.tier
    # The served tier's admission, carrying what this sweep's own control run
    # did: the counts say a forced control executed, whichever tier it decided.
    admitted = judged if not above else dataclasses.replace(
        _admit(s, spec, fn, run_ctx, may_run=False, force=False, at=tier),
        executed=judged.executed, reverified=judged.reverified)
    # The same outcome is the run's own answer only while the served entry
    # COUNTS. What slipped through (review, `repro_undemonstrated`): a forced
    # tier-0 PASS matched a tier-2 PASS whose path no current control shows,
    # so the run's fresh row stood — `check --force` exited 0 ready while
    # `resolve` served that tier-2 entry stale and `status` read NOT READY.
    if out8(served.verdict) == out8(entry.verdict) and admitted.state in ("admitted",
                                                                          "pending"):
        return None
    shown = _as_spec(served.to_verdict(), spec)
    stands = shown.outcome.upper() + {"undemonstrated": ", not current",
                                      "not-admitted": ", not admitted"}.get(admitted.state, "")
    s.notes.append(f"{ran}; the tier-{tier} entry {served.name} at these inputs is the "
                   f"more thorough answer, and it stands ({stands})"
                   if above else
                   f"{ran}; the entry {served.name} at these inputs is the answer the "
                   f"records resolve to, and it stands ({stands})")
    if admitted.state == "undemonstrated" and above:
        # `resolve`'s reading and `_sweep_one` step 2's: the entry's verdict,
        # stale until `check --tier <tier>` runs that path's control.
        return SweepRow(dataclasses.replace(shown, **cost), executed=True,
                        stale_reason=_undemonstrated(tier), rho=served.rho,
                        admission=admitted)
    if admitted.state == "undemonstrated":
        return SweepRow(dataclasses.replace(
            _synthesized(spec, skipped=True, skip_reason=admitted.reason), **cost),
            executed=True, admission=admitted)
    if admitted.state == "not-admitted":
        refused = _unqualified(spec, admitted.reason, rho=served.rho)
        return SweepRow(dataclasses.replace(refused, **cost), executed=True, rho=served.rho,
                        admission=admitted)
    return SweepRow(dataclasses.replace(shown, **cost), executed=True, fresh=True,
                    rho=served.rho, admission=admitted)


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
            remember(s.root, gid, skipped, input_rho=state.input_rho, kind="availability",
                     when=s.when)
        return SweepRow(skipped)

    # 1b. a crash on the path of the costlier answer this sweep serves, and none
    # on its own: `resolve`'s step 2, served as it reads it — the remembered
    # crash, never the entry it superseded — and nothing is run, since no run at
    # this tier is made at that path's inputs or can answer it (`_superseded`).
    # What slipped through (review, remembered outcomes by tier): the cheap path
    # ran instead, and its PASS both became the row and forgot the tier-2 crash.
    # Laid under `_outranked` alone, the row would be right and the cheap path
    # would re-run on every plain check, forever, to reach the same crash.
    # *Rejected:* serving a current tier-0 PASS in its place — `status` serves
    # the most thorough tier recorded, and a costlier path that crashes is not
    # answered by a cheaper one that passes.
    held = s.held.get(gid) or {}
    record = _standing(held, state) if not force and isinstance(state, Fresh) else None
    if record is not None and not any(_crash_applies(other, state)
                                      and other["input_rho"] in state.here
                                      for other in held.values()):
        tier = _read_tier(state.entry.reads)
        where, settle = ((f"on the tier-{tier} path ", f" — run atompipe check --tier {tier}")
                         if tier is not None else ("", ""))
        s.notes.append(f"{gid}: the {record['kind']} remembered {where}at these inputs "
                       f"supersedes the entry {state.entry.name}, and a check at tier "
                       f"{s.now.tier} does not run that path{settle}")
        return SweepRow(dataclasses.replace(_as_spec(record["verdict"], spec), duration_s=0.0,
                                            cpu_s=0.0), rho=record["verdict"].rho)

    # 2. admission (force re-runs the control), on the path the counted verdict
    # takes: a Fresh entry that read ctx.tier took its own tier's, anything
    # else is run at this sweep's. An entry from a costlier tier is judged by
    # the records alone — its control is above this sweep's ceiling (Q1.6).
    served = (not force and isinstance(state, Fresh)
              and _standing(s.held.get(gid), state) is None)
    at = _read_tier(state.entry.reads) if served else s.now.tier
    may_run = at is None or at == s.now.tier
    judged = _admit(s, spec, fn, run_ctx, may_run=may_run, force=force, at=at)
    if judged.state == "undemonstrated" and not may_run:
        # The costlier path's entry, with no control current on that path: what
        # `resolve` reads, the entry's verdict and stale, never a skip. What
        # slipped through (review, `repro_undemonstrated`): this returned the
        # skip below, and `_swept` dropped the gate from the stale set — with a
        # second gate passing on the claim, the skip read PASS (partial), so
        # `check --junit` exited 0 ready and `last_check.json` said pass, while
        # `status` read the claim STALE. A tier-0 check cannot settle it and
        # must not pretend to: only `check --tier <at>` runs that path's
        # control. *Rejected:* running the control here at `at` — the sweep's
        # ceiling is what the caller paid for (Q1.6), and a tier-2 path may be
        # the minutes-long one.
        return SweepRow(_as_spec(state.entry.to_verdict(), spec), cached=True,
                        stale_reason=_undemonstrated(at), rho=state.entry.rho,
                        admission=judged)
    if judged.state == "undemonstrated":
        # A control whose tools went missing while it ran: a skip, BLOCKED.
        return SweepRow(_synthesized(spec, skipped=True, skip_reason=judged.reason),
                        admission=judged)
    if judged.state == "not-admitted":
        refused = _unqualified(spec, judged.reason,
                               rho=state.rho if isinstance(state, Fresh) else "")
        above = _read_tier(state.entry.reads) if isinstance(state, Fresh) else None
        if (force and judged.executed and above is not None and above != s.now.tier
                and _standing(s.held.get(gid), state) is None):
            # The refusal is this run's, on this path (invariant 2: never laid
            # under a PASS it did not reach); a costlier entry whose own path's
            # control stands is what `status` and a plain check serve — say so,
            # rather than leave `check --force` and `status` disagreeing
            # unexplained (review, remembered control failures by tier).
            there = _admit(s, spec, fn, run_ctx, may_run=False, force=False, at=above)
            if there.state in ("admitted", "pending"):
                shown = _as_spec(state.entry.to_verdict(), spec).outcome.upper()
                s.notes.append(f"{gid}: its control ran at tier {s.now.tier} "
                               f"({judged.reason}), on that path only; the tier-{above} "
                               f"entry {state.entry.name} at these inputs, whose own "
                               f"path's control stands, is the more thorough answer, and "
                               f"what status and a plain check serve ({shown})")
        return SweepRow(refused, admission=judged)

    # 3. the cache — unless a remembered crash at these inputs superseded it.
    # Two outcomes at the current rho are the cache's answer too: resolve's
    # error, served as it is, never re-run. What slipped through (the review):
    # only Fresh was special-cased here, so the conflict fell to step 4 on every
    # check, and `_swept` laid whichever answer that run gave over the error
    # `status` and `doctor` read — `[ok  ]`, ready, exit 0, a green JUnit and
    # `last_check.json` saying pass for a claim `status` FAILed. A run cannot
    # settle it: it agrees with one of the two, and both files stay.
    if not force and isinstance(state, (Fresh, Stale)) \
            and _standing(s.held.get(gid), state) is None:
        if isinstance(state, Fresh):
            return SweepRow(_as_spec(state.entry.to_verdict(), spec), cached=True, fresh=True,
                            rho=state.entry.rho, admission=judged)
        if state.conflict and TWO_OUTCOMES_IS_ERROR:
            return SweepRow(_synthesized(spec, error=state.reasons[0], rho=state.rho),
                            rho=state.rho, admission=judged)

    # 4. run, traced with the sweep's anchors, and key what it read
    trace = GateTrace(anchors=s.anchors)
    verdict = _gates.run_gate(spec, fn, run_ctx, trace=trace)
    keyed = _keyed(gid, spec, fn, trace=trace, reads=None, anchors=s.anchors,
                   digests=s.digests)
    verdict = dataclasses.replace(verdict, rho=keyed.rho)
    measured = verdict.outcome in ("pass", "fail")
    name = ""
    clash = ""
    if measured:
        entry = _entry_for(spec, verdict, keyed, s.anchors)
        name = entry.name
        # `--force` (or a remembered crash) ran it over a conflict, or this run
        # just made one: the row is the error the resolver will read, not the
        # run's own answer. The entry is still filed — it is what the gate said.
        clash = _contradicted(s.root, entry)
        answered = _superseded(state, keyed.rho)
        held = {rho_: rec for rho_, rec in held.items() if rho_ not in answered}
        if s.record:
            wrote = write_entry(s.root, entry)
            s.notes.extend(wrote.warnings)
            forget(s.root, gid, answered)
    elif s.record:
        # Under the inputs of the tier this run took (`input_rho`), never the
        # served entry's: a tier-0 crash filed under the tier-2 entry left the
        # records the moment an input only that path reads moved.
        kind = ("error" if verdict.error
                else "self-skip" if _gates.availability(spec)[0] else "availability")
        remember(s.root, gid, verdict, input_rho=state.input_rho, kind=kind, when=s.when)
    above = _read_tier(state.entry.reads) if isinstance(state, Fresh) else None
    if (not measured and above is not None and above != s.now.tier
            and state.input_rho not in state.path and _standing(held, state) is None):
        # The row is this run's crash (step 5), and a costlier entry no crash
        # supersedes is what `status` and the next plain check serve: say so,
        # rather than leave `check --force` and `status` disagreeing unexplained.
        s.notes.append(f"{gid}: ran at tier {s.now.tier} ({verdict.outcome.upper()}), on "
                       f"that path only; the tier-{above} entry {state.entry.name} at these "
                       f"inputs is the more thorough answer, and what status and a plain "
                       f"check serve ({_as_spec(state.entry.to_verdict(), spec).outcome.upper()})")
    if s.record:
        record_obs(s.root, gid, entry=name, when=s.when, duration_s=verdict.duration_s,
                   cpu_s=verdict.cpu_s)
    if clash:
        refused = dataclasses.replace(_synthesized(spec, error=clash, rho=keyed.rho),
                                      duration_s=verdict.duration_s, cpu_s=verdict.cpu_s)
        return SweepRow(refused, executed=True, rho=keyed.rho, admission=judged)
    # 5. a run over a current answer — `--force`, or a crash that superseded it —
    # is one entry beside that answer, not the answer: the row is what the
    # records resolve to with it filed and what it answered forgotten
    # (`_outranked`). A crash here stays the row: never laid under a costlier
    # PASS (invariant 2), it is the louder reading, and the crash is filed at
    # this path's inputs, which that PASS is not at.
    if measured and (isinstance(state, Fresh)
                     or (isinstance(state, Stale) and state.conflict)):
        outranked = _outranked(s, spec, fn, run_ctx, keyed.code, entry, verdict, judged,
                               held=held)
        if outranked is not None:
            return outranked
    return SweepRow(verdict, executed=True, fresh=measured, rho=keyed.rho, admission=judged)


def _reader_state(s: _Session, spec: Any, fn: Any) -> tuple:
    """``(here, state, order, legacy, row_notes)`` for one gate as ``resolve``
    judges it — a reader's ``_Now`` (no tier: the most thorough tier recorded),
    the gate's entries, obs and commit times — built from the session's own
    projection, ledger, anchors and digests. The ``_Now`` is made once per
    sweep, on the first pruned gate, so a sweep that prunes nothing pays
    nothing."""
    here = s.reader
    if here is None:
        here = _Now(s.root, s.now.projection, s.now.ledger, anchors=s.anchors,
                    digests=s.digests, model=s.now.model, tier=None)
        if s.now.projection is None:
            here.flat = s.now.flat
        s.reader = here
    notes: list[str] = []
    entries = _gate_entries(s.root, spec.id, notes)
    named = _obs_names(s.root, [spec.id])
    times = _commit_times(s.root, entries, lambda e: (e.gate, e.name) in named)
    order = _entry_order(s.root, spec.id, times)
    state = _judge(spec, code_digest(spec, fn, anchors=here.anchors), entries, here, order)
    legacy = {v.gate: v for v in getattr(s.now.ledger, "verdicts", None) or ()
              if not v.rho and v.gate == spec.id}
    return here, state, order, legacy, tuple(getattr(state, "notes", ()) or ())


def _pruned_row(s: _Session, spec: Any, fn: Any, state: Any, unmet: Any) -> SweepRow:
    """The row of a selected gate the sweep does not run because a prerequisite
    is not established (P2.2-D6, D8): **what ``resolve`` reads of the gate,
    under the rule** — ``_resolve_gate`` on the gate's records as a reader
    judges them, then ``_under_rule``. Nothing of the gate moved during the
    sweep (it did not run), so what ``status`` reads afterwards is this row: the
    prerequisite skip, or the gate's own crash — or, unless the root crashed,
    its own refusal, missing tool or self-skip — where one stands. ``state`` is
    the sweep's tier-aware state, kept for the
    caller's interface; the reading is the reader's, so the row and ``status``
    cannot part over a costlier tier's entry (the design's risk 1).

    It writes NOTHING — no entry, no remembered outcome, no obs, no control —
    and calls neither the gate nor its control: a pruned verdict is never
    cached, never remembered and never logged (it measured nothing, and a
    prerequisite skip remembered would outlive the root's recovery). Its row
    is neither executed nor cached, so the sweep's counts are untouched; a
    standing reading keeps what its row says of it (``cached``, ``fresh``, its
    stale reason), as ``resolve``'s does. (A Fresh FAIL served where the tool is
    missing, R-3, no longer stands: review of P2.2, ``_under_rule``.)"""
    from . import gates as _gates
    here, judged, order, legacy, row_notes = _reader_state(s, spec, fn)
    notes: list[str] = []
    own, row = _resolve_gate(here, spec, fn, judged, held=s.held, verified=s.verified,
                             notes=notes, order=order, legacy=legacy,
                             availability=_gates.availability, row_notes=row_notes)
    ruled = _under_rule(spec, own, unmet)
    if ruled is own and own is not None and row is not None:
        return SweepRow(own, cached=row.cached, fresh=row.fresh, stale_reason=row.stale_reason,
                        rho=own.rho)
    return SweepRow(ruled)


def sweep(root: str, registry: Any, ctx: Any, *, projection: Any, ledger: Any,
          max_tier: int, only: Any = None, force: bool = False, record: bool = True,
          now: str = "", on_verdict: Callable[[Verdict], Any] | None = None,
          anchors: Anchors | None = None, digests: FileDigests | None = None,
          on_row: Callable[[SweepRow], Any] | None = None) -> SweepResult:
    """``check``'s loop: every selected gate, affected-only, under admission.

    Driven through ``gates.run_all(..., before=)``, so order, the tier ceiling,
    ``--only`` (a named gate above the ceiling runs, control included) and the
    lost-control refusal stay in one place. ``freshness`` is computed BEFORE
    anything runs (cli:H19).

    0. **Plan** (``gates.plan``): the selection and its prerequisite closure,
       each gate after its prerequisites; ``SweepResult.order`` records the run
       order, and ``rows`` keep registration order (P2.2-D4). A gate whose
       prerequisite is not established (``gates.prerequisite_root``, the
       resolver's rule) is **pruned**: ``_pruned_row`` — what ``resolve`` reads
       of it under the rule — and nothing below runs for it, its control
       neither. A gate whose prerequisite is not current (a costlier tier's
       entry served stale) runs as below, and its row is then marked stale.

    Per selected gate that is not pruned, in order (``_sweep_one``):

    1. **Availability** fails: a Fresh FAIL is served (R-3); otherwise skipped —
       ``cached pass exists; <why> here`` over a Fresh PASS (invariant 1) —
       remembered as ``availability`` (never over a crash or self-skip at the
       same rho); no control runs, ``fn`` is never called.
    1b. Unless ``force``, a remembered crash or self-skip standing over a
       Fresh entry of a costlier tier, on that entry's path, and none at this
       sweep's own tier (``here``): the row is that crash, as ``resolve`` reads
       it, and a note names the path and ``run atompipe check --tier <t>`` —
       nothing runs, since no run at this tier answers it.
    2. **Admission** (``admission``'s steps; ``force`` re-runs the control). Not
       admitted: ``error="not admitted: <why>"``, ``fn`` never called. A Fresh
       entry of a costlier tier is judged at ITS tier by the records alone;
       undemonstrated there, it is served as ``resolve`` serves it — its
       verdict, cached, stale ``control not demonstrated at this version — run
       atompipe check --tier <t>`` — never a skip.
    3. Unless ``force``, a **Fresh** entry is served — unless a remembered crash
       or self-skip at a rho current now on its path superseded it (§3.9: a crash
       proves nothing, and neither does the PASS it followed), which re-runs the
       gate. **Two
       outcomes** at the current rho (a Stale ``conflict``) are served as
       ``resolve``'s error, ``two outcomes recorded for identical inputs
       (<names>)``, and the gate is not run — under the same supersede rule
       (``_crash_applies``, shared with ``resolve``).
    4. **Run**, traced with the sweep's anchors. A pass or fail is keyed and
       cached (``write_entry``), clearing the remembered outcomes at the inputs
       it ran on at the tier it took and no others (``_superseded``); anything
       else is remembered under the rho freshness computed before the run at
       that tier (the state's ``input_rho``). Every run appends obs. A pass or
       fail landing at a rho
       where the other outcome is recorded under the same instruments — a
       forced re-run of a conflict, or a run that just made one — is filed and
       its row is that same error (``_contradicted``), recorded or not; an
       entry with an opaque channel never is, as ``_judge`` never matches one.
    5. A run over a **current answer** — ``force``, or a crash that superseded
       it — is re-judged with its entry filed (``_outranked``): where the
       records resolve to another outcome (a costlier tier's Fresh entry, two
       outcomes), or to the same outcome from a costlier entry that does not
       count, or to a remembered crash the run did not answer, that is the row,
       and a note says so. A run's own crash stays its row.

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
                       model=getattr(ctx, "model", None), tier=int(max_tier))
    s = _session(root, ctx, projection=projection, anchors=anchors, digests=digests,
                 record=record, when=now, out_dir=out_dir, tier=int(max_tier))
    # The context every gate of this sweep runs on: its tier the sweep's (a gate
    # must not pick its cheap path under an expensive run), one memo shared by
    # every gate — a SweepMemo, which only load_file opens — never left on the
    # caller's context.
    run_ctx = dataclasses.replace(ctx, tier=int(max_tier),
                                  memo=sweep_memo(getattr(ctx, "memo", None)))
    rows: dict[str, SweepRow] = {}

    def before_hook(spec: Any, fn: Any) -> Verdict:
        found = _sweep_one(s, spec, fn, before.get(spec.id) or Never(), run_ctx, force=force)
        rows[spec.id] = found
        return found.verdict

    def pruned_hook(spec: Any, fn: Any, unmet: Any) -> Verdict:
        found = _pruned_row(s, spec, fn, before.get(spec.id) or Never(), unmet)
        rows[spec.id] = found
        return found.verdict

    def current_hook(gate_id: str) -> bool:
        # A row the sweep served stale (a costlier tier's entry this ceiling
        # cannot demonstrate) is the one not-current prerequisite a sweep can
        # meet: the closure is selected with every dependent (D3, D7).
        found = rows.get(gate_id)
        return bool(found is not None and found.fresh)

    def marked_hook(spec: Any, verdict: Verdict, unmet: Any) -> None:
        # D7 in the sweep: the dependent ran as usual; its row says it is not
        # current, as `resolve` says it (the same `mark_reason`, the root's own
        # stale reason as its "what moved").
        found = rows.get(spec.id)
        if found is None or verdict.outcome not in ("pass", "fail"):
            return
        root = rows.get(unmet.root)
        why = unmet.why or (root.stale_reason if root is not None else "")
        rows[spec.id] = dataclasses.replace(
            found, fresh=False,
            stale_reason=found.stale_reason or _gates.mark_reason(unmet._replace(why=why)))

    def landed(verdict: Verdict) -> None:
        if on_verdict is not None:
            on_verdict(verdict)
        if on_row is not None:
            on_row(rows.get(verdict.gate) or SweepRow(verdict))

    swept = _gates.run_all(registry, run_ctx, max_tier=max_tier, only=only,
                           on_verdict=landed, before=before_hook, pruned=pruned_hook,
                           current=current_hook, marked=marked_hook)

    result = SweepResult(only=only, max_tier=int(max_tier), force=force, record=record,
                         when=now, ledger=ledger, registry=registry, anchors=anchors,
                         before=before, notes=s.notes, order=[v.gate for v in swept])
    # Registration order, not run order (D4): the gate ids `run_all` swept,
    # listed as the registry lists them.
    by_gate = {verdict.gate: verdict for verdict in swept}
    swept = [by_gate[gid] for gid in registry.ids() if gid in by_gate]
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
    ``file:<path>``, ``dir:<path>``, ``ledger:<key>``, ``model``, ``tier`` (the
    tier it read, as a string), ``opaque:<channel>`` (digest ``None``: nothing
    can say)."""
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
    if _read_tier(reads) is not None:
        out["tier"] = str(_read_tier(reads))
    for name in reads.get("opaque") or ():
        out["opaque:" + name] = None
    return dict(sorted(out.items()))


def _detail_by_outcome(verdict: Verdict) -> str:
    """What explains ``verdict`` in one line, by its outcome: an error's first
    line, a skip's reason, a pass's or a fail's detail — the body
    ``Verdict.render`` prints. What slipped through (P2.0 F-1): this preferred
    ``detail``, which ``run_gate`` fills with a crash's traceback tail."""
    outcome = verdict.outcome
    if outcome == "error":
        return (str(verdict.error).splitlines() or [""])[0]
    if outcome == "skipped":
        return verdict.skip_reason or verdict.detail or ""
    return verdict.detail or ""


def write_last_check(root: str, result: SweepResult, resolution: Resolution, *, now: str,
                     params: Mapping[str, Any] | None = None,
                     digests: FileDigests | None = None) -> str | None:
    """Write ``.atompipe/cache/last_check.json`` after a full recorded sweep;
    return its path, or ``None`` — nothing written — when the sweep was
    filtered (``--only``) or dry (``record=False``): a partial sweep's summary
    would stand for the whole project's.

    ``{"when", "spine", "fingerprint", "reads", "statuses", "errored", "counts",
    "worst", "params", "influence"}``: the CLI's stamp; the spine digest; the
    ``fingerprint`` of ``watched_paths``; per gate the reads of the entry the
    resolution used (``param:<json path>``, ``file:``, ``dir:``, ``ledger:``,
    ``model``, ``opaque:``) — what lets P3's hook say which checks a change
    touched; each claim's status under ``resolution`` (``claims.compositions``,
    the enum value as before, P2.1-D12) and ``errored``, the claims Skipped by
    a crash (a crash and a missing tool share ``blocked``; this tells them
    apart); the sweep's counts with its controls; the most urgent blocking
    claim (``claims.severity``, record order on a tie) with the gate that
    explains it, its ``cause`` (``claims.ClaimCause``) and its
    ``detail`` by outcome — an error's first line, a skip's reason, a fail's
    detail, never a crash's traceback (P2.0 F-1) — nulls when nothing blocks;
    ``params`` (the parameter view, from 1.3) and ``influence`` (P3), empty
    until then. Untracked, and read by nothing in the sweep: ``check`` never
    trusts its own summary of a previous run.
    """
    if not result.record or _filtered(result.only):
        return None
    from . import claims as _claims                # a reader's module: never at import (§3.1)
    root = os.path.abspath(root)
    base = result.ledger if result.ledger is not None else Ledger()
    view = dataclasses.replace(base, verdicts=list(resolution.verdicts))
    stale = resolution.stale_gates
    composed = _claims.compositions(view, registry=result.registry, stale_gates=stale)
    worst: dict[str, Any] = {"claim": None, "gate": None, "detail": None, "cause": None}
    blocking = (_claims.blocking(view, result.registry, stale_gates=stale)
                if result.registry is not None else [])
    if blocking:
        # The most urgent blocker by `claims.severity` — the order `check`
        # prints its BLOCKING list in — the first in record order on a tie.
        # What slipped through (review of P2.1, P2.0 F-2 on the JSON channel):
        # `blocking[0]`, record order, so a claim Skipped by a missing tool
        # named before it hid a crash or a fail from the file P3's hook reads.
        claim, status = min(blocking, key=lambda pair: _claims.severity(composed[pair[0].id]))
        why = _claims.explaining_verdict(claim, view.verdicts)
        worst = {"claim": claim.id, "gate": why.gate if why is not None else None,
                 "detail": _detail_by_outcome(why) if why is not None else str(status.value),
                 "cause": str(composed[claim.id].cause.value)}
    reads = {gid: _flat_reads(row.entry.reads or {})
             for gid, row in sorted(resolution.rows.items()) if row.entry is not None}
    data = {
        "when": str(now or ""),
        "spine": spine_digest(),
        "fingerprint": fingerprint(root, watched_paths(root, resolution), digests=digests),
        "reads": reads,
        "statuses": {cid: str(c.status.value) for cid, c in composed.items()},
        "errored": [cid for cid, c in composed.items() if c.errored],
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
