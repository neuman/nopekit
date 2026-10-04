# SPDX-License-Identifier: Apache-2.0
"""atompipe.gates — the registry that refuses to register a logger.

A GATE is an executable that settles a claim *and is able to fail*. Everything in
this module exists to keep that second half true, because the first half is easy
and the second half is where designs get shipped broken.

Three rules are mechanical here, not advisory:

1. **No negative control, no registration.** :meth:`Registry.register` raises
   ``AtompipeError`` when ``spec.negative_control is None``. A cable-routing
   validator once returned a pass flag nobody read — for a whole revision it
   reported success while the cable was geometrically inside a wall. The comment
   left behind afterwards is the shortest statement of this module's job:
   *a logger is not a gate*. A gate that cannot
   demonstrate failure on known-bad input has proven nothing, and the only
   moment we can force the issue is registration.

2. **A skipped gate is visible, never absent.** :func:`availability` decides
   whether the gate's tooling exists; a missing solver produces a ``Verdict``
   with ``skipped=True`` and a ``skip_reason``, and ``Verdict.ok`` is already
   False for those. The claim then resolves BLOCKED in the readiness report
   instead of the gate quietly vanishing from the sweep. Silently degrading is
   how a report starts lying.

3. **An error is not a failure.** A crashed gate settles nothing — it did not
   measure the design, it exploded. :func:`run_gate` catches ``Exception`` and
   returns a verdict with ``error`` set and a trimmed traceback in ``detail``,
   kept separate from ``passed=False`` all the way into the ledger. The two read
   differently downstream — both leave the claim Skipped, and the crash louder:
   its reason leads ``errored:``, it takes Failing's tone and is counted apart
   (invariant 2) — and the report must be able to say "this gate could not run"
   instead of "your design is wrong". (Until P2.1 a crash read FAIL: loud only by
   the accident of the status it borrowed, and it blamed the design.)

   ``SystemExit`` is caught alongside it even though it is not an ``Exception``.
   A gate body that reached ``sys.exit()`` used to unwind past the handler and
   out of `atompipe check`, which exited **0 having printed and recorded
   nothing** — a sweep that proved nothing, reporting success. ``KeyboardInterrupt``
   is the one thing still allowed through: Ctrl-C during a twenty-minute solver
   gate must stop the run, not be filed as a verdict.

A fourth rule is mechanical here for a different reason — it is about packs
colliding rather than about a single gate lying:

4. **A projection key belongs to a pack, and a gate says which.**
   :meth:`GateContext.param` resolves ``<scope>.<name>`` before the bare
   ``<name>``, where ``scope`` is the running gate's own namespace — the part of
   its dotted id before the first dot, ``fdm`` for ``fdm.bed_fit``. Without it,
   two packs that both want ``bbox_mm`` are in a fight only one can win: the
   observed case is ``cad-solid`` reading it as the ASSEMBLY envelope while
   ``fdm-print`` reads it as ONE PART in print orientation, which measured a
   480 mm boat against a 220 mm printer bed and called it a failure. Both packs
   ship in the default set, and every mechanical project has an assembly and
   printed parts, so this was not an exotic combination. A project now publishes
   ``cad.bbox_mm`` and ``fdm.bbox_mm`` and each gate reads the one it means;
   a single-domain project keeps writing ``bbox_mm`` and nothing changes.
   The resolution order is documented in ``docs/PACK_FORMAT.md`` and in
   :meth:`GateContext.param`, because the previous order (``bbox_mm`` before
   ``bbox``) was something a user had to discover by experiment.

And a fifth, because a verdict is only as current as what it read:

5. **A gate sees a traced, read-only world of its own.** :func:`run_gate` never
   hands ``fn`` the caller's context. It hands it a view (``verdicts.traced_context``)
   whose ``params`` refuse every write, whose ``ledger`` is a copy with no
   verdicts in it, whose ``extra`` is the gate's own, and whose every read — of a
   parameter, a claim, a file — is recorded on a trace. What slipped through
   before it: ``ctx.params`` was one mutable dict handed to every gate in a sweep,
   so ``ctx.params["load_n"] = 0`` in gate A was gate B's input, and no verdict
   could show it (S-24); and fdm-print rode a mesh cache on the shared ``extra``,
   so its second gate's read of the part opened nothing and was recorded nowhere
   (S-27). The same channel stayed open one field over: ``memo``, the sweep's
   file memo behind :meth:`GateContext.load_file`, was the raw dict every view
   shared, so a gate could cache a parsed table in it directly and the next
   gate's hit keyed no file (review, ``ffr3/p2``). A view now holds it as a
   ``verdicts.SweepMemo``, which only ``load_file`` opens; any other use raises
   ``verdicts.GateMemoError``. With no trace passed, a throwaway one is made: the
   view is read-only on EVERY path — a test, a pack's own ``__main__``, a
   fixture's nested call — not only inside ``check``.

And a sixth, because a validity guard that guards nothing is a logger one level up:

6. **A prerequisite that is not established is never a pass downstream.** A gate
   may name the gates it ``needs`` (exact ids): a validity guard before the
   analyses it guards. :meth:`Registry.register` refuses a malformed need, a
   ``needs`` cycle (named ``a -> b -> a``, with each member's pack) and a
   prerequisite in a costlier tier than its dependent, the way a build system
   refuses them; :func:`plan` runs a gate's prerequisites before it (DFS
   postorder, registration order when nothing needs anything) and expands
   ``--only`` to them. Under a prerequisite that failed, errored, skipped, is
   unqualified or is not registered, :func:`run_all` never calls the
   dependent's function or its control: it reads Skipped — :func:`blocked`, the
   one producer of ``prerequisite failed: <root>`` / ``prerequisite not
   established: <root> (<why>)`` — unless a reading of the dependent's own
   stands that no run of the prerequisite changes and is at least as loud (its
   own crash; its tool missing here, a self-skip or its own refusal while the
   root did not crash). Under one invalidated or unrun it runs, and its
   verdict is marked not current. The decision is :func:`prerequisite_root`'s
   alone, and the resolver applies the same function
   (``verdicts.apply_prerequisites``). What slipped through before it (S-51): a
   claim tagged only ``deflection`` read Checked on a beam whose guard reported
   Euler-Bernoulli omitting 32% of the deflection — the guard was bound to the
   broad tags, the claim to a narrow one, and nothing joined the two. And the
   registry's "cheapest and most fundamental first" order was prose that four
   of seven packs broke (S-52): ``_gate_files`` loads alphabetically, so beam's
   guard ran last.

The gate function itself stays an ordinary function: :func:`gate` registers it
and returns it **unchanged**, so it is directly callable and directly testable
without the registry in the way.

Time policy (contract rule 3): nothing here stamps a timestamp. Durations are
*measured*, which is not the same thing — a wall-clock duration cannot be passed
in by a caller who is waiting on the result, and every ``when`` (obs,
``last_check.json``) still arrives from the CLI edge. The same goes for
``cpu_s`` (``os.times``).
"""
from __future__ import annotations

import math

import contextlib
import dataclasses
import fnmatch
import importlib
import importlib.machinery
import importlib.util
import numbers
import copy
import os
import reprlib
import shutil
import sys
import tempfile
import time
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, NamedTuple

from . import modelio
from .models import GateSpec, Ledger, NegativeControl, PrerequisiteKind, Tier, Verdict
from .util import AtompipeError, ensure_dir, rel, short_hash
from .verdicts import (GateTrace, ParamTrace, SweepMemo, _pack_dir_of, memo_entries, not_yet,
                       replay, sweep_memo, traced_context, tracing)

__all__ = [
    "SCOPE_SEP",
    "scope_of",
    "GateContext",
    "Registry",
    "REGISTRY",
    "active_registry",
    "use_registry",
    "gate",
    "availability",
    "run_gate",
    "run_all",
    "selftest",
    "run_fixture",
    "run_good_fixture",
    "mutation_walk",
    "MutationPass",
    "MutationResult",
    "MUTATION_MARGIN",
    "MUTATION_RUNGS",
    "MUTATION_BISECT",
    "MUTATION_RUNS_MAX",
    "MUTATION_TIER_MAX",
    "load_fixture",
    "load_project_gates",
    "describe",
    "registry_summary",
    "plan",
    "Reading",
    "Unmet",
    "prerequisite_root",
    "blocked",
    "mark_reason",
    "PREREQUISITE_FAILED",
    "PREREQUISITE_NOT_ESTABLISHED",
    "NEGATIVE_KINDS",
    "NOT_CURRENT_KINDS",
]


#: Separator between a pack's key scope and a key name in a projection:
#: ``fdm.bbox_mm``. The same character the gate ids already use, on purpose —
#: the scope of ``fdm.bed_fit`` is ``fdm``, so a reader who can name the gate can
#: name the key without looking anything up.
SCOPE_SEP = "."

#: What a gate id may not contain, because it names a directory:
#: ``.atompipe/verdicts/<gate id>/`` holds the gate's cached verdicts. ``/`` and
#: ``\`` would nest it; ``..`` would climb out of the cache; ``:`` is a drive
#: letter or an NTFS alternate data stream on Windows. Measured before the refusal
#: landed: zero hits over the 54 bundled pack ids and the bracket's 6 (R-4,
#: ``tests/test_gate_context.py``). *Rejected:* an allow-list charset — project ids
#: were never constrained beyond "no whitespace", and an allow-list would refuse
#: spellings nobody measured. Named residual: Windows also refuses ``<>"|?*`` in a
#: file name; none is refused here, and none is in the corpus.
_ID_FORBIDDEN = ("/", "\\", "..", ":")

#: What a ``needs`` entry may not contain beyond a gate id's own refusals: the
#: fnmatch metacharacters. A prerequisite names exactly one gate (P2.2-D1). A
#: glob, like a tag, binds a SET that changes when a pack is installed — the
#: graph, and whether it has a cycle, would move under a project nobody edited.
#: Measured before the refusal landed: zero hits over the bundled packs and the
#: bracket, which declared no ``needs`` at all (R-10: only the new field can
#: produce it). *Rejected:* accepting globs and expanding them at registration
#: (frozen at load order — a later pack is never reached); at every sweep (the
#: cycle check would have to run there, after the import that wrote it).
_NEED_FORBIDDEN = ("*", "?", "[")

#: The two leads of a prerequisite skip's ``skip_reason`` (P2.2-D6, D11): the
#: brief's ``prerequisite failed: <root>`` ONLY when the root failed — it tells a
#: reader to fix the design the root judged — and ``prerequisite not
#: established: <root> (<why>)`` for every other kind, which a reader fixes
#: elsewhere (install the root's tool, fix the root's crash, qualify the root).
#: Spine text, so rewording either re-keys every verdict cache entry in every
#: project (``verdicts.SPINE_MODULES``): that is the cost, and the reason these
#: are constants. The claim channel does not read them: ``report.reason`` words
#: a prerequisite skip from ``report.HUMAN`` and the verdict's
#: ``blocked_by``/``blocked_kind``; this text is the gate channel's — the
#: streamed line, ``check``'s skip digest (which groups by it, so everything one
#: root blocks lands on one line), JUnit's gate case, ``gate show``.
#: *Rejected:* ``unknown: prerequisite …`` (phase-2.md's draft; GLOSSARY lists
#: *unknown* as a Never-say for Skipped); "blocked by" (a Never-say too).
PREREQUISITE_FAILED = "prerequisite failed"
PREREQUISITE_NOT_ESTABLISHED = "prerequisite not established"

#: The two not-current marks' stale reasons (P2.2-D7), for the same channels.
#: "invalidated" and "unrun" are GLOSSARY's words for the ROOT; the dependent's
#: own read set did not move, and the reason says whose did.
PREREQUISITE_INVALIDATED = "prerequisite {root} invalidated"
PREREQUISITE_UNRUN = "prerequisite {root} unrun"

#: The kind word in ``prerequisite not established: <root> (<word>)``, the gate
#: channel's — the outcome words of GLOSSARY §1 for a crash and a skip, §2's
#: *unqualified*, and "not registered" (no glossary term: a fact about this
#: project's registry, not an outcome). The claim channel's are ``report.HUMAN``'s.
_KIND_WORDS = {
    PrerequisiteKind.ERRORED: "errored",
    PrerequisiteKind.SKIPPED: "skipped",
    PrerequisiteKind.UNQUALIFIED: "unqualified",
    PrerequisiteKind.NOT_REGISTERED: "not registered",
}

#: ``PrerequisiteKind``'s two classes, each in rank order (its docstring has why
#: a crash ranks first). A negative root wins over a not-current one: what can
#: be judged now is judged.
NEGATIVE_KINDS: tuple[str, ...] = (PrerequisiteKind.ERRORED, PrerequisiteKind.FAILED,
                                   PrerequisiteKind.SKIPPED, PrerequisiteKind.UNQUALIFIED,
                                   PrerequisiteKind.NOT_REGISTERED)
NOT_CURRENT_KINDS: tuple[str, ...] = (PrerequisiteKind.INVALIDATED, PrerequisiteKind.UNRUN)
_KIND_RANK = {str(kind): rank for rank, kind in enumerate(NEGATIVE_KINDS + NOT_CURRENT_KINDS)}

#: Where a project's own gates live: ``<root>/gates/*.py``, imported exactly like
#: a pack's, but with no manifest and no pack name. ``cli.PROJECT_GATES_DIR`` is
#: the same directory under the name it had when the loader lived there.
_PROJECT_GATES_DIR = "gates"

#: Internal "nothing was found" marker. Distinct from ``None`` because a
#: projection is allowed to carry ``None`` (a model that computed nothing says so
#: explicitly), and a lookup that could not tell the two apart would fall through
#: to the next spelling and read a DIFFERENT quantity.
_UNSET: Any = object()


def scope_of(gate_id: str) -> str:
    """The key scope a gate id implies: ``fdm.bed_fit`` -> ``fdm``.

    Undotted ids (a project's own one-off gate called ``envelope``) have no
    scope, and scoping is simply off for them — better than inventing a namespace
    a project never wrote down.
    """
    head, sep, _tail = (gate_id or "").partition(SCOPE_SEP)
    return head.strip() if sep else ""


#: A verdict is ONE LINE of context. A whole sweep should cost tens of lines,
#: because each check emits one dense line and puts the volume in files; a gate
#: that dumps its log into `detail` turns a 40-gate sweep into 4,000 lines of
#: context nobody reads. Detail is collapsed to a single line and capped here.
_DETAIL_MAX = 500

#: How much of a crashing gate's traceback survives into `detail`. The last
#: frames are the ones that name the actual failure; the first frames are always
#: this module calling the gate, which the reader already knows.
_TRACE_TAIL = 400


def _trace_tail(text: str, limit: int = _TRACE_TAIL) -> str:
    """The last ``limit``-ish characters of a traceback, cut at a line boundary.

    A raw ``text[-400:]`` opens mid-identifier ("nt call last):"), which reads as
    corruption and makes a reader distrust the rest of the line. Cutting forward
    to the next newline costs a few characters and keeps the first surviving
    frame intact.
    """
    if len(text) <= limit:
        return text
    tail = text[-limit:]
    newline = tail.find("\n")
    return tail[newline + 1:] if 0 <= newline < limit // 2 else tail


def _noop_log(_message: str) -> None:
    """Default ``GateContext.log``: swallow it.

    A gate must be runnable with no CLI attached — from a test, from a pack's
    own ``__main__``, from another gate. Defaulting to ``print`` would spray a
    JSON-mode CLI with prose and make ``--json`` unparseable.
    """


def _one_line(text: Any, limit: int = _DETAIL_MAX) -> str:
    """Collapse ``text`` to a single capped line, preserving the front of it.

    Newlines become ``' | '`` rather than spaces so a flattened traceback stays
    greppable and a reader can still see where one frame ended. Truncation marks
    itself; a silently cut string is a string somebody will quote in a bug
    report as if it were complete.
    """
    if text is None:
        return ""
    flat = " | ".join(part.strip() for part in str(text).splitlines() if part.strip())
    flat = " ".join(flat.split())
    if len(flat) > limit:
        return flat[: limit - 3].rstrip() + "..."
    return flat


# --------------------------------------------------------------------------- #
# the context a gate is handed
# --------------------------------------------------------------------------- #
@dataclass
class GateContext:
    """Everything a gate is allowed to see. One argument, by design.

    A gate signature of ``fn(ctx)`` rather than ``fn(model, params, out_dir,
    ...)`` is what lets the spine add a field without breaking every pack in
    existence. Packs are third-party code; their call signature is a contract.

    Fields:

    ``root``     project root (the directory containing ``.atompipe/``).
    ``ledger``   the project state: claims, params, inputs. What a gate
                 receives is a traced copy with NO verdicts in it: a gate that
                 read other gates' verdicts would put verdicts inside its own
                 content address, a staleness that feeds itself.
    ``model``    the loaded model module/instance, or None when the gate is
                 checking something else (a file, a netlist, an input artifact).
                 None on EVERY run of an evaluator not from the bundled packs —
                 its check run, its controls and its walk (``verdicts._no_model``):
                 no known-good design has a model object, so none of its
                 qualification runs saw one, and a model only the check run had
                 was a path nothing qualified (review of P2.3, ``br6``).
    ``params``   the projection flattened to ``{name: value}`` — config AND
                 derived together. Gates read numbers from here, never by
                 re-deriving them, because a gate that recomputes a derived
                 value is checking its own arithmetic instead of the model's.
                 READ-ONLY in the gate's view: a write raises
                 ``verdicts.GateInputWriteError`` (S-24). A fixture builds a
                 context of its own; it never edits the one it was handed.
    ``out_dir``  scratch and evidence. Anything cited in ``Verdict.evidence``
                 goes here; see :meth:`out_path`.
    ``tier``     the tier of the sweep in progress. A gate may use it to pick a
                 cheaper path, but must not use it to lower its own standard.
                 In the gate's view it is a ``verdicts.TierRead``: it compares,
                 indexes, hashes and formats as the int it is, and every use
                 is recorded, so a verdict that read it is keyed on it — a
                 sweep at tier N serves one recorded at N or above, never a
                 cheaper path's. What slipped through before (review round 1,
                 ``probe.tier``): nothing recorded the read, and a tier-0 PASS
                 was served Fresh to ``check --tier 2``. ``int(ctx.tier)`` is
                 the plain value (what ``json.dumps`` needs), and a read too.
    ``log``      one-line progress sink. Defaults to a no-op.
    ``extra``    free-form. The negative-control machinery merges a fixture's
                 dict in here, and a caller may put ``pack_dirs`` /``pack_dir``
                 here to say where a pack's fixtures live (see
                 :func:`_fixture_root`). Each gate gets its OWN shallow copy:
                 a value one gate stores here is gone for the next (S-27).
                 Anything shared across a sweep goes through :meth:`load_file`.
    ``pack``     the pack the running gate came from (``"fdm-print"``), stamped
                 by :func:`run_gate`. Empty for a project's own gates.
    ``key_scope`` that gate's key namespace (``"fdm"``), stamped by
                 :func:`run_gate` from the gate id. See :meth:`param`.
    ``memo``     the sweep's file memo, behind :meth:`load_file`. One per
                 :func:`run_all`, the same ``verdicts.SweepMemo`` in every
                 gate's view: a handle only :meth:`load_file` opens. Anything
                 else — ``get``, ``[k]``, ``[k] = v``, ``in``, iteration —
                 raises ``verdicts.GateMemoError``: a value one gate left in
                 the memo was the next gate's input with no trace of either
                 (review, ``ffr3/p2``; S-27 on ``extra``). ``is None`` is the
                 one question to ask of it. ``None`` outside a sweep — a
                 hand-run script, a test — and :meth:`load_file` then simply
                 loads; a plain dict a caller puts here is wrapped in each
                 view, its entries shared.
    ``trace``    the ``verdicts.GateTrace`` this view records into, set by
                 :func:`run_gate` (and :func:`selftest` for a fixture). ``None``
                 on a context nobody is tracing.

    Every field has a default so a test can build a context with the one thing
    it cares about. That is additive to the contract's declaration, not a change
    to it: field order is unchanged and positional construction still works.
    New fields go on the END for that reason — ``memo`` and ``trace`` are there
    now, and are neither shown nor compared: two contexts that differ only in who
    is watching are the same context.
    """

    root: str = ""
    ledger: Ledger = field(default_factory=Ledger)
    model: Any | None = None
    params: dict[str, Any] = field(default_factory=dict)
    out_dir: str = ""
    tier: int = 0
    log: Callable[[str], None] = _noop_log
    extra: dict[str, Any] = field(default_factory=dict)
    pack: str = ""
    key_scope: str = ""
    memo: SweepMemo | dict | None = field(default=None, repr=False, compare=False)
    trace: Any = field(default=None, repr=False, compare=False)

    # -- parameter access -------------------------------------------------- #
    def scopes(self) -> list[str]:
        """The pack-qualified prefixes this gate's keys may carry, best first.

        The gate's own namespace (``fdm``) first, then the pack's directory name
        (``fdm-print``) — people type both, and the two can never mean different
        things because one pack owns both strings. Empty when neither is known,
        which turns scoping off rather than guessing a namespace.
        """
        out: list[str] = []
        for candidate in (self.key_scope, self.pack):
            text = (candidate or "").strip()
            if text and text not in out:
                out.append(text)
        return out

    def param(self, name: str, default: Any = None, *,
              scope: Any = _UNSET) -> Any:
        """Read one projected parameter, or ``default``.

        **Resolution order, in full, highest priority first.** It is written down
        here and in ``docs/PACK_FORMAT.md`` because the previous order was
        discoverable only by experiment, and a user who guessed wrong got a
        confident verdict about the wrong object:

        1. ``<scope>.<name>`` for each scope in :meth:`scopes` — the flat key
           ``"fdm.bbox_mm"``, then the nested spelling ``{"fdm": {"bbox_mm": …}}``
           for a model that groups its projection by domain.
        2. ``<name>`` — the bare, unscoped key. This is what a single-domain
           project writes, and it keeps working untouched.
        3. the last dotted segment of ``name`` — so a gate may ask for
           ``config.beam_mm`` while the flattened projection holds ``beam_mm``.

        A pack-scoped key therefore always beats an unscoped one, because it is
        the only one of the two that is an explicit statement about THIS pack.
        That is the whole mechanism: ``cad-solid`` reads the assembly's envelope
        from ``cad.bbox_mm`` and ``fdm-print`` reads one printed part's from
        ``fdm.bbox_mm``, on a project that has both — which is every mechanical
        project, and which used to be unrepresentable.

        ``scope`` overrides the automatic one: pass a string, a sequence of
        strings, or ``None``/``""`` for a deliberately unscoped read (a key that
        genuinely belongs to the project rather than to any pack). Omit it and
        the gate's own scopes are used.

        ``None`` stored under a key counts as present and is returned, since a
        model that projected ``None`` said something; only an absent key falls
        through to the next spelling.
        """
        if scope is _UNSET:
            prefixes = self.scopes()
        elif scope is None or scope == "":
            prefixes = []
        elif isinstance(scope, str):
            prefixes = [scope]
        else:
            prefixes = [str(s) for s in scope if str(s).strip()]

        for prefix in prefixes:
            value = self._exact(f"{prefix}{SCOPE_SEP}{name}")
            if value is not _UNSET:
                return value
        value = self._exact(name)
        if value is not _UNSET:
            return value
        tail = name.rsplit(SCOPE_SEP, 1)[-1]
        if tail != name:
            value = self._exact(tail)
            if value is not _UNSET:
                return value
        return default

    def _exact(self, key: str) -> Any:
        """The value stored under exactly ``key``, or ``_UNSET``.

        Two spellings of the same thing are accepted for a dotted key: the flat
        ``params["fdm.bbox_mm"]`` and the nested ``params["fdm"]["bbox_mm"]``. A
        model that groups its projection by domain is writing the more readable
        of the two, and refusing it would make the scoping mechanism cost a
        rewrite of the model's ``build()``.
        """
        if key in self.params:
            return self.params[key]
        head, sep, tail = key.partition(SCOPE_SEP)
        if sep:
            container = self.params.get(head)
            if isinstance(container, dict) and tail in container:
                return container[tail]
        return _UNSET

    def pack_param(self, name: str, default: Any = None) -> Any:
        """:meth:`param` with this gate's own scopes, spelled out.

        Identical to ``ctx.param(name)``; it exists so a pack author can say in
        the code that the key is this pack's, and so a reader grepping for
        scope-aware reads finds them.
        """
        return self.param(name, default)

    def first_pack_param(self, names: Iterable[str], default: Any = None) -> Any:
        """First of a synonym family to be present, scoped spellings first.

        **Order:** every pack-scoped spelling, in the order ``names`` declares
        them, then every bare spelling in that same order. So a project that
        publishes ``fdm.bbox_mm`` beats one that publishes bare ``bbox`` even
        though ``bbox`` might be listed first, and a project that publishes
        neither scoped key falls back to the family exactly as before.

        The declared order inside each sweep is the pack's statement about which
        spelling is the primary one — put the key that says WHAT THE OBJECT IS
        (``part_bbox_mm``) ahead of the bare legacy one (``bbox_mm``), and say so
        in ``PACK.md``.
        """
        value, _key = self.first_pack_param_named(names, default)
        return value

    def first_pack_param_named(self, names: Iterable[str],
                               default: Any = None) -> tuple[Any, str]:
        """:meth:`first_pack_param`, plus the spelling that supplied the value.

        The key name is evidence, not decoration: ``fdm.layer_alignment`` derates
        a ``deflection_utilisation`` by a different ratio than a
        ``stress_utilisation``, and a verdict that cannot name which key it read
        cannot be audited. Returns ``(default, "")`` when nothing matched.

        A key whose value is ``None`` is skipped here, unlike in :meth:`param`:
        inside a synonym family a ``None`` is a model that mentioned a spelling
        without filling it in, and stopping there would hide the sibling key that
        does carry the number. On a single key there is no sibling to hide, so
        ``param`` returns the ``None`` and the gate sees what the model said.
        """
        family = [str(n) for n in names]
        for prefix in self.scopes():
            for name in family:
                value = self._exact(f"{prefix}{SCOPE_SEP}{name}")
                if value is not _UNSET and value is not None:
                    return value, f"{prefix}{SCOPE_SEP}{name}"
        for name in family:
            value = self.param(name, _UNSET, scope=None)
            if value is not _UNSET and value is not None:
                return value, name
        return default, ""

    def require_param(self, name: str) -> Any:
        """Read a parameter that MUST exist, or raise ``AtompipeError``.

        A missing parameter is a user-side mismatch between the model and the
        pack — not a bug in either — and the gate must stop rather than compare
        against ``None``. ``None <= limit`` raises in Python, but ``bool(None)``
        does not, and a gate that quietly treats a missing value as falsy is the
        precise shape of the failure this whole module exists to prevent.
        """
        missing = object()
        value = self.param(name, missing)
        if value is missing:
            known = ", ".join(sorted(self.params)[:8]) or "(nothing)"
            raise AtompipeError(
                f"the model does not define {name!r}, which this gate needs. "
                f"Add it to the model's CONFIG (or derive it in build()) and "
                f"re-run `atompipe model --write`. Projection currently has: {known}"
            )
        return value

    # -- evidence ---------------------------------------------------------- #
    def out_path(self, *parts: str) -> str:
        """Path under ``out_dir`` for an evidence file, with the dir created.

        Gates cite files in ``Verdict.evidence``; those files are what makes a
        PROVEN row in the readiness report auditable two months later. Creating
        the directory lazily here (instead of eagerly in :func:`run_gate`) keeps
        a gate that writes nothing from leaving empty directories behind.
        """
        base = self.out_dir or os.path.join(self.root or os.curdir, ".atompipe", "out")
        target = os.path.join(base, *parts) if parts else base
        ensure_dir(os.path.dirname(target) if parts else target)
        return target

    def with_extra(self, extra: dict[str, Any] | None) -> "GateContext":
        """A copy of this context with ``extra`` merged over the existing one.

        Used by :func:`selftest` to hand a gate its known-bad fixture without
        mutating the caller's context — a selftest that left the bad geometry
        behind in ``ctx.extra`` would poison every gate that ran after it.
        """
        merged = dict(self.extra or {})
        merged.update(extra or {})
        return dataclasses.replace(self, extra=merged)

    # -- files --------------------------------------------------------------- #
    def load_file(self, path: Any, loader: Callable[[str], Any] | None = None) -> Any:
        """``loader(abspath)`` — or the file's bytes — loaded once per sweep, and
        recorded as a read of THIS gate on every call.

        For a file several gates read: a mesh, a table, a result file. ``path``
        resolves against ``root`` when relative. ``loader`` defaults to reading
        the bytes; pass the parser (``trimesh.load_mesh``) to share the parsed
        object instead. Its exceptions are the gate's, and are not memoised.

        **Every call is a read, hit or miss.** What slipped through without it:
        fdm-print kept a mesh cache on the shared ``ctx.extra``, so the second gate
        to want the part got a cache hit that opened nothing — no audit event, no
        record — and its verdict would have been keyed as if it never depended on
        the file (S-27). So the call is reported as the ``open`` it stands for,
        through the same audit channel a real open uses: this view's trace sees
        it, and so does every trace open around it (a control's, around a
        fixture's nested gate), exactly as for an open. The read is recorded
        before the load, so a file that turns out to be missing is still named
        as an input — its absence is what the gate decided on.

        **Every file the loader opens is a read too, hit or miss.** On a miss the
        loader runs inside this view's window and under a trace of its own
        (``verdicts.tracing``), so this gate and every trace open around it record
        what it opened as they would any open; the entry keeps that trace, and a
        hit replays it (``verdicts.replay``) — files, the paths it only asked
        about, listed directories and opaque channels — into every caller. What
        slipped through before: the hit reported only the file it was handed, so
        a ``.gltf``'s ``.bin`` buffers
        (an ``.obj``'s ``.mtl``, any include a loader follows) were inputs of the
        gate that missed and of no gate that hit. Bundled ``fdm.bridge_span`` kept
        a Fresh PASS after the buffers moved, while ``fdm.overhang``, which had
        missed, went stale (review round 1). With no window open the miss used to
        record the loader's opens nowhere at all; this view's trace is pushed for
        the load now, so a test calling a view directly sees them too.

        **The memo** (``self.memo``, one per :func:`run_all`, opened here and
        nowhere else — ``verdicts.memo_entries``) is keyed on
        ``(abspath, id(loader))``: two gates asking for the bytes and a third
        asking for a parsed mesh get two entries, never each other's. For a bound
        method the id is its object's and its function's, because ``obj.parse`` is
        a NEW object on every access: keyed on that, a bound-method loader never
        hit (what slipped through while its test was being written); a lambda made
        inside the gate body never hits either — pass a module-level function. The
        entry holds the loader itself, so its ``id`` cannot be reused by a new
        function while the entry lives, and the stat signature of every path the
        loader read, listed or asked about (``_signatures``), so bytes rewritten
        between two gates of one sweep are loaded again — a hit on the old bytes
        would have put the new bytes' digest on a verdict computed from the old
        ones. The
        named file's signature is taken before the load and the rest after it,
        since only the load says what they are; a path the loader probed and
        found missing is signed as missing, and a hit needs it still missing.
        With no memo (``None``: a hand-run check script, a test) it just loads
        (packs:H15), inside the same window.

        A hit hands every caller the SAME object. Do not mutate it: copy first,
        as cad-solid does before welding a mesh.
        """
        raw = os.fspath(path)
        if isinstance(raw, bytes):
            raw = os.fsdecode(raw)
        target = raw if os.path.isabs(raw) else os.path.join(self.root or os.curdir, raw)
        abspath = os.path.abspath(target)
        _report_read(self.trace, abspath)
        view = tracing(self.trace) if self.trace is not None else contextlib.nullcontext()
        memo = memo_entries(self.memo)
        if memo is None:
            with view:
                return _load(abspath, loader)
        key = (abspath, _loader_id(loader))
        signature = _stat_signature(abspath)
        held = memo.get(key)
        # A key match IS the same loader: the entry holds its loader alive, and a
        # live object's id is never handed to another (see `_loader_id`).
        if (held is not None and signature is not None and held[1] == signature
                and _unmoved(held[3])):
            replay(held[4], self.trace)
            return held[2]
        loaded = GateTrace()
        with view, tracing(loaded):
            value = _load(abspath, loader)
        if signature is not None:
            memo[key] = (loader, signature, value, _signatures(loaded, abspath), loaded)
        return value


def _loader_id(loader: Any) -> Any:
    """``id(loader)`` — or, for a bound method, the ids of its object and function.

    Two accesses of ``obj.parse`` are two method objects with two ids and one
    meaning; keyed on the method object, the memo never hit for one. The entry
    keeps the loader alive, and a live object's id is never reused, so neither
    form can collide with another loader while the entry exists.
    """
    owner = getattr(loader, "__self__", None)
    func = getattr(loader, "__func__", None)
    if owner is not None and func is not None:
        return ("method", id(owner), id(func))
    return id(loader)


def _load(abspath: str, loader: Callable[[str], Any] | None) -> Any:
    """``loader(abspath)``, or the file's bytes."""
    if loader is not None:
        return loader(abspath)
    with open(abspath, "rb") as handle:
        return handle.read()


def _signatures(loaded: GateTrace, named: str) -> tuple:
    """``((path, stat signature), ...)`` for every path a loader read, listed or
    asked about (``os.path.exists`` on an optional sidecar) but the one it was
    handed (signed before the load, by the caller).

    A read the loader made of a file it had itself written first never reaches
    ``files_read``, so a scratch file the loader writes and reads back is not
    signed: its bytes came from the paths that are. A directory is signed by its
    own stat, whose mtime moves when an entry is added, removed or renamed — a
    loader that globs for its buffers is re-run when a buffer appears.
    *Rejected:* the directory's listing (``os.listdir`` raises an audit event,
    and the check would itself become a read of every trace open at the hit).
    """
    paths = [path for path in loaded.files_read if path != named]
    paths += [path for path in loaded.stats if path != named]
    paths += sorted(loaded.dirs)
    return tuple((path, _stat_signature(path)) for path in dict.fromkeys(paths))


def _unmoved(signed: tuple) -> bool:
    """Whether every ``(path, signature)`` still stats the same (``None`` for a
    path that was missing, and still is)."""
    return all(_stat_signature(path) == signature for path, signature in signed)


def _stat_signature(abspath: str) -> tuple | None:
    """What a memo hit is checked against: ``(size, mtime_ns, ctime_ns, inode)``.

    ctime and the inode are here because size and mtime alone miss a same-size
    rewrite with its mtime put back — ``os.utime`` cannot restore a ctime, and an
    atomic replace changes the inode. ``None`` when the file cannot be stat'ed:
    for the file ``load_file`` was handed, such a load is never memoised, so the
    loader's own error reaches the gate; for any other path the loader read (an
    optional ``.mtl`` it probed), ``None`` is a signature like the rest, and a
    hit needs the path still missing.
    """
    try:
        st = os.stat(abspath)
    except OSError:
        return None
    return (st.st_size, st.st_mtime_ns, st.st_ctime_ns, st.st_ino)


#: ``os.open``'s read-only flag, for the ``open`` event :func:`_report_read`
#: raises. The audit hook reads the MODE first and the flags only when the mode
#: is None, so "r" alone decides; the flag keeps the arguments shaped like the
#: real event's.
_READ_FLAGS = getattr(os, "O_RDONLY", 0)


def _report_read(trace: Any, abspath: str) -> None:
    """Raise the ``open`` audit event a real read of ``abspath`` would raise.

    Routed like every other audit event (``verdicts.tracing``): to every trace
    open right now, and — pushed for the length of this one event — to
    ``trace``, the calling view's own, even when no window is open around it (a
    test calling a gate's view directly). A trace pushed twice records once: a
    trace dedupes its own reads. With no trace and no window it is inert.
    """
    window = tracing(trace) if trace is not None else contextlib.nullcontext()
    with window:
        sys.audit("open", abspath, "r", _READ_FLAGS)


# --------------------------------------------------------------------------- #
# the registry
# --------------------------------------------------------------------------- #
def _own_copy(spec: GateSpec) -> GateSpec:
    """A private copy of ``spec``, deep enough that the caller cannot edit it later.

    Registration is a one-time audit (``negative_control`` present, fixture named,
    id usable) and storing the caller's object would make that audit a formality:
    the checks run against a record the caller still holds a handle to and can
    rewrite the instant they return. The attack is three lines and it is not
    hypothetical for third-party pack code —

        spec = GateSpec(id="x", negative_control=NegativeControl(fixture="bad.py"))
        registry.register(spec, fn)     # passes the audit
        spec.negative_control = None    # and it is a logger again, still registered

    — after which the sweep runs a gate nothing proves can fail, and
    `atompipe gate selftest` reports "declares no negative control" for a gate
    that registered with one. The mutable list fields are copied for the same
    reason: ``spec.claims.append("stiffness")`` after the fact silently widens
    what a verdict is allowed to settle, which is the ledger claiming coverage
    nobody registered. ``needs`` likewise: ``spec.needs.append(…)`` after the
    audit would add an edge no cycle or tier check ever saw.

    It runs on the way OUT as well as on the way in. Copying at registration
    closed the caller's handle and left the registry's own: ``get()`` and
    ``specs()`` handed out the stored object, so ``registry.specs()[0].claims
    .append("stiffness")`` widened the next verdict just the same, through the
    front door. Every exit — ``get``, ``specs``, ``pairs``, ``for_claim``,
    ``by_tier``, ``__iter__`` — now hands out one of these, and the only ways to
    change a stored spec are :meth:`Registry.register` with ``replace=True`` and
    :meth:`Registry.set_pack`, which say so in their names.

    ``fn.gate_spec`` (set by :func:`gate`) is the DECLARATION, not the registered
    record: editing it changes nothing the registry holds.

    Shallow-per-field is enough because every field is a str, an int enum, a list
    of str, or the ``NegativeControl`` (itself all strings).
    """
    nc = spec.negative_control
    return dataclasses.replace(
        spec,
        claims=list(spec.claims or []),
        requires_tools=list(spec.requires_tools or []),
        requires_python=list(spec.requires_python or []),
        requires_one_of=list(spec.requires_one_of or []),
        needs=list(spec.needs or []),
        negative_control=dataclasses.replace(nc) if isinstance(nc, NegativeControl) else nc,
    )


def _control_gap(spec: GateSpec) -> str:
    """Why this spec's negative control is not usable, or ``""`` if it is.

    :meth:`Registry.register` is the primary enforcement and says far more; this
    is the cheap re-assertion for the moment a gate is actually about to run.
    See :func:`run_all` for why running one is not enough.
    """
    nc = spec.negative_control
    if nc is None:
        return "its negative control is gone"
    if not isinstance(nc, NegativeControl):
        return f"its negative control is a {type(nc).__name__}, not a NegativeControl"
    if not (nc.fixture or "").strip():
        return "its negative control names no fixture"
    return ""


def _inversion(dependent: str, dependent_spec: GateSpec, need: str,
               need_spec: GateSpec, dependent_fn: Any = None) -> str:
    """The refusal of a prerequisite costlier than its dependent, naming the
    file that declares the edge — the dependent's (``dependent_fn``'s source,
    its last two path parts: ``gates/dep.py``). What slipped through (review of
    P2.2): the loader prefixes the file it was loading, which is the
    prerequisite's when the dependent registered first, so the error pointed
    at the file with no edge in it; and it cited ``D-28``, a plan row the
    release bundle strips (METHOD's rule 10 is the shipped reference)."""
    def at(spec: GateSpec) -> str:
        return f"tier {int(spec.tier)}, {spec.pack or '(project)'}"
    source = getattr(getattr(dependent_fn, "__code__", None), "co_filename", "") or ""
    where = (f", declared in {os.path.basename(os.path.dirname(source))}/"
             f"{os.path.basename(source)}" if source else "")
    return (f"gate {dependent!r} ({at(dependent_spec)}{where}) names {need!r} "
            f"({at(need_spec)}) as a prerequisite — a prerequisite costlier than its "
            f"dependent drags tier {int(need_spec.tier)}'s cost into every "
            f"tier-{int(dependent_spec.tier)} loop that runs the dependent (METHOD rule "
            f"10). Drop the edge from {dependent!r}, or declare it at tier "
            f"{int(need_spec.tier)} or above.")


class Registry:
    """The gates known to this process, in declaration order.

    Order is part of the contract, and it is LISTING order: ``specs()``,
    ``check --json``'s rows and JUnit's gate cases keep it. RUN order is
    :func:`plan`'s — each gate's prerequisites (``GateSpec.needs``) before it,
    and registration order where nothing needs anything. What slipped through
    while this order was also the run order (S-52): it was prose — "the order a
    pack's author wrote them, usually cheapest-and-most-fundamental first" — and
    a pack's modules load alphabetically, so beam's validity guard registered
    last and ran after every number it was meant to vouch for; four of seven
    bundled packs broke the promise. The guard-before-analysis order is now an
    edge, which the registry checks and the sweep obeys.

    The registry holds ``(spec, fn)`` pairs. The spec is serialisable and the
    function is not, which is why ``GateSpec.entry`` exists — out-of-process
    discovery re-imports from the entry string rather than unpickling anything.
    """

    def __init__(self) -> None:
        self._gates: dict[str, tuple[GateSpec, Callable[[GateContext], Any]]] = {}
        #: ``{pack name: the directory its gates were loaded from}``, filled by
        #: ``packs.load_gates``. Public because it is a fact about THIS registry's
        #: gates that nothing else can answer: a gate's pack name is on its spec,
        #: but where that pack lives — a checkout, a project's ``.atompipe/packs``,
        #: site-packages — is known only to the load. A verdict entry spells an
        #: absolute path under a pack directory as ``<pack:NAME>/...`` so it reads
        #: the same in every checkout, and this is where the directory comes from.
        self.pack_dirs: dict[str, str] = {}

    # -- registration ------------------------------------------------------ #
    def register(
        self,
        spec: GateSpec,
        fn: Callable[[GateContext], Any],
        *,
        replace: bool = False,
    ) -> None:
        """Register one gate, or raise ``AtompipeError`` explaining what is missing.

        **This is rule 5 of the method made mechanical.** A gate with no declared
        negative control cannot be shown to fail on known-bad input, which means
        nobody — including its author — knows whether it measures anything. The
        failure has shipped exactly that way for a whole revision: a cable-routing
        validator that returned a pass flag nobody read, with the cable
        geometrically inside a wall. Registration is the last moment
        the system can ask for proof before a green tick starts meaning something
        to a human, so it asks here and refuses here.

        The other refusals are smaller but the same idea — a registry that
        accepts a nameless or duplicated gate produces a report that cannot be
        trusted to say which gate proved what:

        * empty ``spec.id`` — verdicts key on it, the ledger keys on it
        * an id holding ``/``, ``\\``, ``..`` or ``:`` — a gate id names a
          directory in the verdict cache, ``.atompipe/verdicts/<gate id>/``
        * an id that differs from a registered one only in case — on a
          case-insensitive filesystem the two cache directories are one
        * a fixture-less ``NegativeControl`` — the declaration without the proof
        * a duplicate id from a different function — two packs claiming one name;
          the second silently winning is how a gate stops being the gate you read

        Re-registering the *same* function object under the same id is a no-op,
        because importing a pack's gate module twice in one process is routine
        and is not a conflict — and it keeps a pack name :meth:`set_pack` stamped
        on the stored spec when the incoming declaration leaves ``pack`` blank,
        or a second import would quietly orphan the gate from its pack.
        ``replace=True`` is the deliberate override.

        What is stored is a **copy** (:func:`_own_copy`), never the caller's
        object. A check run against a record the caller can rewrite afterwards is
        not a check — see that function for the three-line withdrawal it closes.

        **Prerequisites** (``spec.needs``, P2.2-D2), refused on the new field
        only, so no spec written before it can trip them (R-10):

        * a need that is not exactly one gate id — empty, holding whitespace,
          ``/ \\ .. :`` or a glob character — or names the gate itself, or
          repeats;
        * a need that **closes a cycle**, found by a DFS from this gate over the
          registered gates' needs, with this spec in place of any it replaces
          (``replace=True``, or a re-import that changed its needs). Complete under
          any load order: every cycle is closed by its last registration, and every
          registration before that left the graph acyclic. The message names the
          cycle ``a -> b -> a`` and each member's pack;
        * a **tier inversion** from either side of the edge: a need costlier than
          this gate, or this gate costlier than one that already needs it.

        A need on an id not registered yet is allowed: a missing pack must not
        become a load crash, and load order must not become semantic (Q2.3). It
        reads "not registered" at the sweep, and ``doctor`` names it.
        *Rejected:* checking only at sweep time (a cycle would surface at the
        first ``check``, not at the import that wrote it, and ``pack validate``
        would pass a cyclic pack); falling back to registration order on a cycle
        (a dependent would run before its prerequisite).
        """
        if not isinstance(spec, GateSpec):          # a bug in the caller, not the user
            raise TypeError(f"register() needs a GateSpec, got {type(spec).__name__}")
        if not callable(fn):
            raise TypeError(f"gate {spec.id!r} is not callable ({type(fn).__name__})")

        gate_id = (spec.id or "").strip()
        if not gate_id or any(ch.isspace() for ch in gate_id):
            raise AtompipeError(
                f"gate id {spec.id!r} is empty or contains whitespace — ids are keys "
                f"in the ledger and on the command line. Use a dotted, pack-prefixed "
                f"id like 'fdm.overhang'."
            )
        unsafe = [part for part in _ID_FORBIDDEN if part in gate_id]
        if unsafe:
            raise AtompipeError(
                f"gate id {gate_id!r} contains {', '.join(repr(p) for p in unsafe)} — a "
                f"gate id names a directory in the verdict cache "
                f"(.atompipe/verdicts/<gate id>/), so it cannot hold a path separator, "
                f"'..' or ':'. Use a dotted, pack-prefixed id like 'fdm.overhang'."
            )
        folded = gate_id.casefold()
        twin = next((known for known in self._gates
                     if known != gate_id and known.casefold() == folded), None)
        if twin is not None:
            raise AtompipeError(
                f"gate id {gate_id!r} differs from the registered {twin!r} only in case — "
                f"on a case-insensitive filesystem (macOS and Windows by default) their "
                f"verdict-cache directories are one directory, and each gate would be "
                f"served the other's verdicts. Rename one."
            )

        nc = spec.negative_control
        if nc is None:
            raise AtompipeError(
                f"gate {gate_id!r} declares no negative_control, so nothing proves it can "
                f"fail — and a validator that cannot be shown to fail is a logger, not a "
                f"gate. One shipped that way for a whole revision: a cable-routing check "
                f"returned a pass flag nobody read while the cable sat inside a wall. "
                f"Declare the known-bad input it must reject:\n"
                f"    from atompipe.models import NegativeControl\n"
                f"    negative_control=NegativeControl(\n"
                f"        fixture=\"selftest/known_bad.py\",   # make(ctx) -> input broken in the\n"
                f"                                          # ONE way this gate claims to detect\n"
                f"        note=\"what is wrong with it, in one line\",\n"
                f"    )\n"
                f"then prove it: atompipe gate selftest {gate_id}"
            )
        if not isinstance(nc, NegativeControl):
            raise AtompipeError(
                f"gate {gate_id!r}: negative_control must be a NegativeControl, "
                f"got {type(nc).__name__}"
            )
        if not (nc.fixture or "").strip():
            raise AtompipeError(
                f"gate {gate_id!r} declares a negative_control with no fixture — the "
                f"declaration without the known-bad input proves exactly as much as no "
                f"declaration at all. Set fixture=\"selftest/<file>.py\" (a make(ctx) "
                f"function) or \"module:function\"."
            )
        good = getattr(nc, "good", "")
        if not isinstance(good, str) or (good and not good.strip()):
            # R-10: only the new declaration is refused, and only when it is
            # declared and unusable — "" is the default and resolves elsewhere.
            raise AtompipeError(
                f"gate {gate_id!r}: negative_control.good must be a fixture reference "
                f"spelled like fixture (\"selftest/<file>.py:<function>\" or "
                f"\"module:function\"), or left empty for the pack's baseline or the "
                f"project's selftest/known_good.py — not {good!r}"
            )

        existing = self._gates.get(gate_id)
        if existing is not None and not replace and existing[1] is not fn:
            old_spec, old_fn = existing
            where_old = old_spec.entry or old_spec.pack or getattr(old_fn, "__module__", "?")
            where_new = spec.entry or spec.pack or getattr(fn, "__module__", "?")
            raise AtompipeError(
                f"gate id {gate_id!r} is already registered by {where_old} and "
                f"{where_new} wants it too. Two gates cannot share an id: verdicts, "
                f"claim coverage and `atompipe check --only` all key on it. Rename one "
                f"(ids are pack-prefixed for exactly this reason)."
            )

        fresh = _own_copy(spec)
        if existing is not None and not replace:              # idempotent re-import
            old_spec, _old_fn = existing
            if not (fresh.pack or "").strip() and old_spec.pack:
                fresh = dataclasses.replace(fresh, pack=old_spec.pack)
        self._check_needs(gate_id, fresh, fn)
        self._gates[gate_id] = (fresh, fn)

    # -- prerequisites ----------------------------------------------------- #
    def _check_needs(self, gate_id: str, spec: GateSpec, fn: Any = None) -> None:
        """Refuse ``spec``'s ``needs`` as :meth:`register` says, or return.
        ``fn`` is the gate's function, so a refusal can name its file."""
        seen: set[str] = set()
        for need in spec.needs or ():
            if not isinstance(need, str):
                raise AtompipeError(
                    f"gate {gate_id!r}: prerequisite {need!r} is a {type(need).__name__}, "
                    f"not a gate id — write needs=['<pack>.<gate>']")
            why = ""
            if not need or any(ch.isspace() for ch in need):
                why = "is empty or holds whitespace"
            elif any(part in need for part in _ID_FORBIDDEN):
                why = "holds '/', '\\', '..' or ':', which no gate id can"
            elif any(ch in need for ch in _NEED_FORBIDDEN):
                why = ("is a pattern — a prerequisite names exactly one gate, because a "
                       "set bound by a glob or a tag changes when a pack is installed, "
                       "and the graph with it")
            elif need == gate_id:
                why = "names the gate itself — a gate cannot be established before itself"
            elif need in seen:
                why = "is named twice"
            if why:
                raise AtompipeError(
                    f"gate {gate_id!r}: prerequisite {need!r} {why}. Write each "
                    f"prerequisite's exact id once: needs=['beam.model_validity']")
            seen.add(need)

        cycle = self._cycle_through(gate_id, list(spec.needs or ()))
        if cycle is not None:
            packs_of = {gid: (spec.pack if gid == gate_id else
                              (self._gates[gid][0].pack if gid in self._gates else ""))
                        for gid in cycle}
            who = ", ".join(f"{gid}: {packs_of[gid] or '(project)'}"
                            for gid in dict.fromkeys(cycle))
            raise AtompipeError(
                f"gate {gate_id!r}: its prerequisites close a cycle, "
                f"{' -> '.join(cycle)} ({who}) — no gate in it could ever be "
                f"established before the others. Remove one of these edges.")

        mine = int(spec.tier)
        for need in spec.needs or ():
            found = self._gates.get(need)
            if found is not None and int(found[0].tier) > mine:
                raise AtompipeError(_inversion(gate_id, spec, need, found[0], fn))
        for other, (other_spec, other_fn) in self._gates.items():
            if other != gate_id and gate_id in (other_spec.needs or ()) \
                    and mine > int(other_spec.tier):
                raise AtompipeError(_inversion(other, other_spec, gate_id, spec, other_fn))

    def _cycle_through(self, gate_id: str, needs: list[str]) -> list[str] | None:
        """The cycle ``needs`` would close through ``gate_id`` — ``[gate_id, …,
        gate_id]`` — or ``None``: a DFS over the registered gates' needs with
        ``gate_id``'s own replaced by ``needs`` (``replace=True`` and a re-import
        substitute the spec; a stale copy of it in the graph would find a cycle
        that no longer exists, or miss one that does)."""
        graph = {gid: list(stored.needs or ()) for gid, (stored, _fn) in self._gates.items()}
        graph[gate_id] = list(needs)
        stack: list[tuple[str, list[str]]] = [(need, [gate_id, need])
                                              for need in reversed(needs)]
        seen: set[str] = set()
        while stack:
            node, path = stack.pop()
            if node == gate_id:
                return path
            if node in seen or node not in graph:
                continue
            seen.add(node)
            stack.extend((nxt, path + [nxt]) for nxt in reversed(graph[node]))
        return None

    def needed_by(self, gate_id: str) -> list[str]:
        """The gates whose ``needs`` name ``gate_id``, in registration order —
        the reverse edges, for ``gate show``'s "prerequisite of" and ``gate list
        --json``'s ``needed_by``."""
        return [gid for gid, (spec, _fn) in self._gates.items()
                if gate_id in (spec.needs or ())]

    def unregister(self, gate_id: str) -> bool:
        """Drop a gate. Returns whether it was there. Mostly for tests."""
        return self._gates.pop(gate_id, None) is not None

    def set_pack(self, gate_id: str, pack: str) -> None:
        """Stamp the pack a registered gate came from. The one sanctioned edit.

        ``packs.load_gates`` knows which pack it is loading and a gate module
        written without a ``PACK`` global does not, so the loader fills in a blank
        ``pack``. It used to do that by assigning to the spec ``get()`` returned,
        which worked only because ``get()`` handed out the stored object — the
        same door that let ``specs()[0].claims.append(…)`` widen what a verdict
        settles. With every exit handing out a copy, an edit to the stored record
        has to be asked for by name, and this is the name.

        Raises ``KeyError`` for an id that is not registered: that is a bug in the
        caller, which just read the id out of this registry.
        """
        entry = self._gates.get(gate_id)
        if entry is None:
            raise KeyError(f"set_pack: no gate {gate_id!r} is registered")
        spec, fn = entry
        self._gates[gate_id] = (dataclasses.replace(_own_copy(spec), pack=str(pack or "")), fn)

    def clear(self) -> None:
        """Empty the registry. Tests and `packs.validate` use a scratch registry."""
        self._gates.clear()
        self.pack_dirs.clear()

    # -- lookup ------------------------------------------------------------ #
    # Every exit below hands out `_own_copy` of the stored spec, never the spec
    # itself; see that function for the door this closes.
    def get(self, gate_id: str) -> tuple[GateSpec, Callable[[GateContext], Any]] | None:
        """``(spec, fn)`` for one id, or None. Never raises on an unknown id —
        the caller usually has a better error message than this module does.
        The spec is a copy; editing it changes nothing registered."""
        entry = self._gates.get(gate_id)
        if entry is None:
            return None
        spec, fn = entry
        return _own_copy(spec), fn

    def specs(self) -> list[GateSpec]:
        """Every spec, in registration order. Copies."""
        return [_own_copy(spec) for spec, _ in self._gates.values()]

    def ids(self) -> list[str]:
        """Every gate id, in registration order."""
        return list(self._gates)

    def pairs(self) -> list[tuple[GateSpec, Callable[[GateContext], Any]]]:
        """Every ``(spec, fn)``, in registration order. The specs are copies."""
        return [(_own_copy(spec), fn) for spec, fn in self._gates.values()]

    def for_claim(self, claim_id: str, tags: Iterable[str] = ()) -> list[GateSpec]:
        """Gates that cover a claim, by id OR by tag.

        ``GateSpec.claims`` deliberately mixes the two: a pack author who has
        never seen your project cannot name your claim ids, but can say "I settle
        anything tagged ``manufacturability``". That is what makes packs
        composable, and it is why this lookup is not a dict.

        Matching is case-insensitive and whitespace-stripped. A ``Manufacturability``
        tag failing to match a ``manufacturability`` gate would show up as an
        UNCLAIMED claim in the readiness report — a capability gap that does not
        exist — and send someone off to install a solver they already have.
        """
        wanted = {(claim_id or "").strip().lower()}
        wanted.update((t or "").strip().lower() for t in (tags or ()))
        wanted.discard("")
        if not wanted:
            return []
        out: list[GateSpec] = []
        for spec, _ in self._gates.values():
            declared = {(c or "").strip().lower() for c in (spec.claims or [])}
            if declared & wanted:
                out.append(_own_copy(spec))
        return out

    def by_tier(self, max_tier: int) -> list[GateSpec]:
        """Gates at or below ``max_tier``, in registration order.

        The inner loop is tier 0 and must stay in seconds (rule 10): ``--part
        NAME`` at ~5s versus ``--all`` at ~20min is the difference that makes
        iteration possible at all. This filter is what keeps that promise, so it
        is ``<=`` on the declared tier and nothing cleverer — a gate that
        mislabels its tier breaks everyone's loop and no filter can save it.
        """
        ceiling = int(max_tier)
        return [_own_copy(spec) for spec, _ in self._gates.values()
                if int(spec.tier) <= ceiling]

    # -- dunders ----------------------------------------------------------- #
    def __len__(self) -> int:
        return len(self._gates)

    def __contains__(self, gate_id: object) -> bool:
        return gate_id in self._gates

    def __iter__(self):
        """``(spec, fn)`` pairs, like :meth:`pairs` — copies, not the stored specs."""
        return iter(self.pairs())

    def __repr__(self) -> str:          # pragma: no cover - diagnostics only
        return f"Registry({len(self._gates)} gates: {', '.join(list(self._gates)[:6])})"


#: The process-wide default registry. Pack gate modules decorate against this on
#: import; `packs.load_gates` may pass its own for isolated validation.
REGISTRY = Registry()

#: Stack of registries that `@gate` targets when a gate module does not name one.
#: Empty means REGISTRY. A stack rather than a single slot because validating one
#: pack while another is loading is a real sequence, and the inner load must not
#: leave the outer one decorating into the wrong registry on its way out.
_ACTIVE_REGISTRIES: list[Registry] = []


def active_registry() -> Registry:
    """The registry ``@gate`` decorates into right now.

    A gate module cannot name the registry it should land in — it is imported by
    ``packs.load_gates``, which chose one, and the module was written before that
    choice existed. So the choice is ambient for the duration of the import and
    this is where it is read.
    """
    return _ACTIVE_REGISTRIES[-1] if _ACTIVE_REGISTRIES else REGISTRY


@contextlib.contextmanager
def use_registry(registry: Registry):
    """Make ``registry`` the target of every ``@gate`` decorated inside the block.

    This is the seam a pack loader needs::

        with use_registry(my_registry):
            import_the_packs_gate_modules()     # @gate lands in my_registry

    Without it, a loader that hands a private registry to an import gets an empty
    registry back and its gates in the global one — and because ``load_gates``
    reports what it added by diffing the registry it was given, the symptom is an
    empty list rather than an error: a pack that looks like it ships no gates at
    all. Silent under-reporting is the worst available failure here, which is why
    the seam exists rather than being left to convention.

    Not thread-safe, and does not need to be: Python serialises module execution
    behind the import lock, and this is only ever held across an import.
    """
    _ACTIVE_REGISTRIES.append(registry)
    try:
        yield registry
    finally:
        # Pop OUR entry, not the top one, in case a gate module misbehaved and
        # left something on the stack; a leaked entry would silently redirect
        # every later registration in the process.
        try:
            _ACTIVE_REGISTRIES.remove(registry)
        except ValueError:                       # pragma: no cover - defensive
            pass


# --------------------------------------------------------------------------- #
# the decorator
# --------------------------------------------------------------------------- #
def gate(
    *,
    id: str,
    claims: Iterable[str] = (),
    tier: Tier | int = Tier.INSTANT,
    settles: str = "",
    requires_tools: Iterable[str] = (),
    requires_python: Iterable[str] = (),
    negative_control: NegativeControl | None = None,
    title: str = "",
    description: str = "",
    pack: str = "",
    entry: str = "",
    registry: Registry | None = None,
    requires_one_of: Iterable[str] = (),
    needs: Iterable[str] = (),
) -> Callable[[Callable[[GateContext], Any]], Callable[[GateContext], Any]]:
    """Declare a gate: build its :class:`~atompipe.models.GateSpec` and register it.

    Returns the function **unchanged** — no wrapper, no signature change. A gate
    is an ordinary function of one argument and stays directly callable:

        from pack.gates.printability import overhang
        v = overhang(ctx)          # no registry, no CLI, no fixture

    That matters more than it looks. A decorator that wrapped the function would
    make the gate's own unit test go through the registry's normalisation, and
    then the thing under test would no longer be the thing that ships.

    The spec is also attached as ``fn.gate_spec`` so a loader that walks a
    module's attributes can find it without consulting a registry.

    Two fields are *derived* rather than restated (rule 2 — derive, never
    duplicate):

    * ``pack`` falls back to the defining module's ``PACK`` global, which is what
      ``packs.load_gates`` sets. Typing the pack name into forty decorators is
      forty chances to typo it, and a mistyped pack name silently orphans a gate
      in the readiness report.
    * ``entry`` falls back to ``module:qualname``, the string an out-of-process
      run needs to re-import this gate.
    * ``title``/``description`` fall back to the docstring's first line / rest,
      because a gate whose docstring already says what it checks should not have
      to say it twice and let the two drift.

    ``registry=None`` (the default) means :func:`active_registry` — the registry a
    pack loader has made current with :func:`use_registry`, or the module-level
    ``REGISTRY`` when nobody has. A gate module is imported by machinery it has
    never heard of, so it must not have to name a registry; passing one
    explicitly is for tests and for a gate defined in application code.

    ``requires_one_of`` declares a disjunction — ``["python:manifold3d",
    "tool:blender"]`` means either will do — for a gate whose back-end can be any
    one of several unrelated things. Declare it rather than probing in the body:
    only a declared requirement is :func:`availability`'s to judge, and a skip
    availability did not decide is a gate skipping its own input (see
    ``GateSpec.requires_one_of``).

    ``needs`` names this gate's **prerequisites** — exact ids of gates that must
    be established before its verdict counts (``GateSpec.needs``): a validity
    guard before the analyses it guards. The last keyword, like the field.

    ``fn.gate_spec`` is this declaration, not the record the registry holds —
    the registry keeps its own copy.

    Registration failures raise ``AtompipeError`` at IMPORT time, which is the
    point: a pack with a control-less gate fails to load rather than loading with
    a gate nobody can trust.
    """
    if negative_control is not None and not isinstance(negative_control, NegativeControl):
        # Caught here rather than in register() so the traceback points at the
        # decorator in the pack's source, where the author can see it.
        raise AtompipeError(
            f"gate {id!r}: negative_control must be a NegativeControl instance, "
            f"got {type(negative_control).__name__}"
        )
    if isinstance(needs, str):
        # list("beam.model_validity") is seventeen one-letter "prerequisites",
        # each a legal forward reference: refused here, where the author looks.
        raise AtompipeError(
            f"gate {id!r}: needs={needs!r} is a string — it takes a list of gate ids: "
            f"needs=[{needs!r}]")

    def decorate(fn: Callable[[GateContext], Any]) -> Callable[[GateContext], Any]:
        doc = (fn.__doc__ or "").strip()
        doc_title, _, doc_rest = doc.partition("\n")

        module = sys.modules.get(getattr(fn, "__module__", "") or "")
        module_pack = getattr(module, "PACK", "") if module is not None else ""

        spec = GateSpec(
            id=id,
            title=title or doc_title.strip(),
            claims=[str(c) for c in (claims or ())],
            tier=Tier(int(tier)),
            pack=pack or (str(module_pack) if module_pack else ""),
            requires_tools=[str(t) for t in (requires_tools or ())],
            requires_python=[str(m) for m in (requires_python or ())],
            negative_control=negative_control,
            description=description or doc_rest.strip(),
            settles=settles,
            entry=entry or f"{getattr(fn, '__module__', '?')}:{getattr(fn, '__qualname__', getattr(fn, '__name__', '?'))}",
            requires_one_of=[str(r) for r in (requires_one_of or ())],
            needs=list(needs or ()),
        )
        # Resolved at DECORATION time, not when `gate()` was called: the
        # ambient registry is whatever loader is importing this module right now.
        target_registry = registry if registry is not None else active_registry()
        target_registry.register(spec, fn)
        fn.gate_spec = spec          # type: ignore[attr-defined]
        return fn

    return decorate


# --------------------------------------------------------------------------- #
# availability
# --------------------------------------------------------------------------- #
def availability(spec: GateSpec) -> tuple[bool, str]:
    """Can this gate run here? ``(ok, reason)`` — the reason is user-facing.

    Checks executables with ``shutil.which`` and importable modules with
    ``importlib.util.find_spec``. ``find_spec`` is used instead of ``import``
    because importing a heavy solver binding to find out whether it exists costs
    seconds, and the tier-0 loop's whole promise is that it does not.

    **Never silently skip.** The reason string produced here becomes
    ``Verdict.skip_reason``, which keeps an uninstalled solver visible in the
    readiness report as BLOCKED instead of letting the gate vanish from the
    sweep. A missing tool that produces no row is indistinguishable from a tool
    that passed, and that indistinguishability is how a report starts lying.

    All missing dependencies are reported, not just the first: a user who
    installs one tool and reruns only to be told about the next one is a user
    who gives up on the third.

    ``spec.requires_one_of`` is a disjunction ANDed with the rest: any one of its
    entries present satisfies it, none present reads ``requires one of python
    manifold3d, tool blender, tool openscad (none found)``. An entry that is not
    ``python:<module>`` or ``tool:<executable>`` is named, never guessed at — a
    bare ``manifold3d`` could be either, and guessing would make one spelling
    work by accident on some machines.
    """
    missing_tools = [t for t in (spec.requires_tools or []) if t and shutil.which(t) is None]

    missing_modules: list[str] = []
    for module_name in (spec.requires_python or []):
        if not module_name:
            continue
        problem = _module_problem(module_name)
        if problem is not None:
            missing_modules.append(problem)

    # getattr, not the attribute: `site.availability` hands this a ViewSpec,
    # which carries `requires_tools`/`requires_python` under the same names on
    # purpose and has no disjunction. The first run of this rule crashed every
    # viewgen on exactly that.
    none_of = _one_of_problem(getattr(spec, "requires_one_of", None) or [])

    if not missing_tools and not missing_modules and not none_of:
        return True, ""

    reasons: list[str] = []
    if missing_tools:
        reasons.append(f"requires {', '.join(missing_tools)} (not on PATH)")
    if missing_modules:
        reasons.append(f"requires python {', '.join(missing_modules)} (not importable)")
    if none_of:
        reasons.append(none_of)
    return False, "; ".join(reasons)


def _module_problem(module_name: str) -> str | None:
    """Why ``module_name`` cannot be imported here, or None when it can.

    The plain name when it is simply absent; the name with the reason in
    parentheses when it is present and broken.
    """
    try:
        found = importlib.util.find_spec(module_name)
    except (ImportError, AttributeError, ValueError) as exc:
        # find_spec("a.b") imports "a"; a parent package that raises on
        # import is, for our purposes, exactly as unusable as one that is
        # absent — but say WHICH, because "not installed" would send the
        # user to pip for a package that is already there and broken.
        return f"{module_name} ({type(exc).__name__})"
    except SystemExit as exc:                    # BaseException; see run_gate
        # find_spec imports the parent package, and a solver binding's
        # "driver not installed" guard is routinely a bare sys.exit() at
        # module scope. Uncaught, that ends `atompipe check` right here with
        # the binding's own exit code — no rows, no ledger, possibly 0. A
        # package that exits when imported is unusable, which is a SKIP, and
        # a skip is never a pass.
        return f"{module_name} (exits on import: sys.exit({exc.code!r}))"
    except Exception as exc:                     # noqa: BLE001 - third-party import side effects
        return f"{module_name} ({type(exc).__name__}: {exc})"
    return module_name if found is None else None


#: The two kinds a ``requires_one_of`` entry may name. Kept to exactly the two
#: probes :func:`availability` already makes for ``requires_python`` and
#: ``requires_tools``; a third kind would be a third probe nothing else uses.
_ONE_OF_KINDS = ("python", "tool")


def _one_of_problem(entries: Iterable[str]) -> str:
    """Why a ``requires_one_of`` disjunction is unmet, or ``""`` when any entry is here.

    Every entry is probed, not only up to the first hit, so the answer does not
    depend on the order a pack author happened to list them in.
    """
    present = False
    wanted: list[str] = []
    malformed: list[str] = []
    for raw in entries:
        text = str(raw).strip()
        if not text:
            continue
        kind, sep, name = text.partition(":")
        kind, name = kind.strip().lower(), name.strip()
        if not sep or kind not in _ONE_OF_KINDS or not name:
            malformed.append(repr(text))
            continue
        if kind == "tool":
            missing = None if shutil.which(name) is not None else name
        else:
            missing = _module_problem(name)
        if missing is None:
            present = True
        wanted.append(f"{kind} {missing or name}")
    if present or not (wanted or malformed):
        return ""
    parts: list[str] = []
    if wanted:
        parts.append(f"requires one of {', '.join(wanted)} (none found)")
    if malformed:
        parts.append(f"requires_one_of lists {', '.join(malformed)}, which is not "
                     f"python:<module> or tool:<executable>")
    return "; ".join(parts)


# --------------------------------------------------------------------------- #
# running one gate
# --------------------------------------------------------------------------- #
def _as_bool(value: Any) -> bool | None:
    """``value`` as a builtin bool if it IS a truth value, else None.

    Two things count: a ``bool``, and a 0-d object whose ``dtype.kind == "b"`` —
    ``numpy.bool_``, which is what ``mesh.is_watertight`` and every numpy
    comparison hand back. Duck-typed on ``dtype.kind`` because the spine does not
    import numpy, ever. Everything else is None, and the caller refuses it.

    What got through before this existed: the dict branch of :func:`_normalise`
    called ``bool()`` on the pass value. The string ``"false"`` is non-empty, so
    it read True, and a gate that returned ``{"passed": "false"}`` rendered
    ``[ok]``. *Rejected:* keeping ``bool()`` — it is the hole. *Rejected:*
    ``isinstance(value, int)``, which lets ``1`` and ``2`` through as passes
    because ``bool`` subclasses ``int``.
    """
    if isinstance(value, bool):
        return value
    try:
        if getattr(getattr(value, "dtype", None), "kind", None) != "b":
            return None
        if getattr(value, "ndim", None) != 0 and getattr(value, "shape", None) != ():
            return None            # an ARRAY of flags is not one answer
        return bool(value)
    except Exception:              # noqa: BLE001 - a third-party object's attributes
        return None


def _show(value: Any) -> str:
    """A bounded repr for an error line: a gate that returned a 10^6-element
    array as its pass flag must not put the whole array into the ledger."""
    try:
        return reprlib.repr(value)
    except Exception:              # noqa: BLE001 - a third-party __repr__
        return f"<{type(value).__name__}>"


def _pass_value_error(value: Any) -> str:
    return (f"gate reported passed={_show(value)} ({type(value).__name__}); "
            f"a verdict must say True or False")


#: Appended to ``detail`` when a pass value is refused. Says what the old reading
#: would have been, because "must say True or False" alone sounds like pedantry
#: until you see that ``bool("false")`` is True.
_PASS_VALUE_WHY = ("a pass flag that is not a bool is not an answer: bool('false') is "
                   "True, and 2 is not a yes. Return True or False (a numpy bool is fine)")


def _plain_number(value: Any) -> Any:
    """A measurement as a builtin ``int`` or ``float``; anything else unchanged.

    ``numpy.int64`` and ``numpy.float32`` are real numbers and pass the checks,
    but they are not JSON-serialisable — the ledger writer would raise mid-check,
    after the gate had run, on a verdict that was perfectly honest.
    """
    if value is None or isinstance(value, bool) or not isinstance(value, numbers.Real):
        return value
    if isinstance(value, numbers.Integral):
        return int(value)
    return float(value)


def _stamp(verdict: Verdict, spec: GateSpec, duration: float, cpu: float = 0.0) -> Verdict:
    """Overwrite the identity fields of a verdict from its spec.

    A gate cannot be trusted to report its own name, tier, pack, claims or
    runtime — not because pack authors lie, but because copy-pasted gate bodies
    keep the previous gate's id in the ``Verdict(gate=...)`` literal, and a
    verdict filed under the wrong gate is worse than a missing one: it overwrites
    a real result in ``Ledger.upsert_verdict`` and marks someone else's claim.
    The spec is the only authority on identity, so the spec wins, always.

    The same goes for what a gate costs and what it was computed from:
    ``duration_s`` and ``cpu_s`` are the measured ones, and ``rho`` is cleared.
    rho is the content address of the inputs the gate READ, computed by the sweep
    from the trace; a gate that could set it could key its verdict to inputs it
    never read, and the cache would serve that verdict as current.

    ``unqualified`` is cleared for the same reason (P2.1): it is the spine's
    word for an evaluator refused at its version, and a claim with one reads
    Gap. A gate that could set it could make its own crash read Gap instead of
    errored — quieter than a missing tool, against invariant 2. So whatever a
    gate returns is read on its flags alone, and a gate's own
    ``error="unqualified: …"`` (or the error ``Verdict.__post_init__`` writes
    for a mark it set — the same words since P2.3, so the same disguise) is a
    crash like any other.

    ``blocked_by`` and ``blocked_kind`` are cleared on the same terms (P2.2-D9):
    the prerequisite mark is the spine's, set only by :func:`blocked`. A gate
    that could set it could make its own skip read as a prerequisite's (the
    claim would read the root's fault), or — with ``blocked_kind="errored"`` —
    make itself loud or name a root it never had. A gate that returned the
    mark still reads a skip: ``Verdict.__post_init__`` wrote the flags when it
    built the verdict, and the flags are what is kept.

    ``passed`` is forced False whenever the gate skipped or errored. ``Verdict.ok``
    already encodes that, but ``passed`` is what lands in the JSON a human reads,
    and "passed: true, error: ..." is a sentence nobody should have to interpret.

    Otherwise ``passed`` must BE a truth value (:func:`_as_bool`); anything else
    becomes an error naming what the gate said. This is where a ``Verdict``
    returned as-is gets that check — ``Verdict(passed="no")`` never passes through
    :func:`_normalise`'s dict or tuple branches. A skip carrying junk in
    ``passed`` stays a skip: it is already not a pass, and calling it an error
    would move a BLOCKED claim to FAIL over a field the gate never meant.

    The stored values are builtins — ``bool`` flags, ``int``/``float``
    measurements — because whatever lands here is written to JSON next.
    """
    error = verdict.error
    detail = verdict.detail
    if verdict.skipped or error:
        passed = False
    else:
        passed = _as_bool(verdict.passed)
        if passed is None:
            error = _pass_value_error(verdict.passed)
            detail = (f"{detail} | " if detail else "") + _PASS_VALUE_WHY
            passed = False
    return dataclasses.replace(
        verdict,
        gate=spec.id,
        tier=Tier(int(spec.tier)),
        pack=spec.pack,
        claims=list(spec.claims or []),
        duration_s=round(max(0.0, float(duration)), 6),
        cpu_s=round(max(0.0, float(cpu)), 6),
        rho="",
        unqualified="",
        blocked_by=[],
        blocked_kind="",
        passed=passed,
        skipped=bool(verdict.skipped),
        measured=_plain_number(verdict.measured),
        limit=_plain_number(verdict.limit),
        detail=_one_line(detail),
        skip_reason=_one_line(verdict.skip_reason, 200),
        error=_one_line(error, 200),
    )


def _normalise(result: Any, spec: GateSpec) -> Verdict:
    """Turn whatever a gate returned into a ``Verdict``, or say it cannot.

    Accepted, in descending order of how much the gate told us:

    * ``Verdict``            — used as-is (identity still overwritten)
    * ``dict``              — ``passed`` / ``pass`` / ``ok``, plus any Verdict field.
      ``pass`` is accepted because it is the conventional key for a check emitting
      ``{"check":"geometry","pass":true,"detail":"..."}``, and a spine that cannot
      read the shape its own gates naturally produce is friction for no gain.
    * ``(bool, detail)``    — the common analytic gate
    * ``bool``              — a gate with nothing to say beyond yes/no

    "bool" means a truth value as :func:`_as_bool` reads one: a builtin bool or a
    0-d numpy bool. A pass value that is anything else — ``"false"``, ``None``,
    ``1.0``, ``2`` — is an error naming what the gate said, here for the tuple
    and in :func:`_stamp` for a ``Verdict`` or dict (which may be a skip, and a
    skip carrying junk stays a skip).

    Everything else is an ERROR verdict, not a failure and not a pass. ``None``
    especially: a gate that falls off the end of its function has measured
    nothing, and the one thing this module must never do is let that read as
    green.
    """
    if isinstance(result, Verdict):
        return result

    flag = _as_bool(result)
    if flag is not None:
        return Verdict(gate=spec.id, passed=flag)

    if isinstance(result, dict):
        data = dict(result)
        if "passed" not in data:
            for alias in ("pass", "ok"):
                if alias in data:
                    data["passed"] = data.pop(alias)
                    break
        if "detail" not in data and "message" in data:
            data["detail"] = data.pop("message")
        if "passed" not in data:
            return Verdict(
                gate=spec.id,
                error="gate returned a dict with no 'passed' key",
                detail=f"returned keys: {', '.join(sorted(map(str, data))) or '(none)'} — "
                       f"a gate that does not say whether it passed is a logger",
            )
        said = data["passed"]
        flag = _as_bool(said)
        if flag is not None:
            data["passed"] = flag
        # Otherwise the gate's own value stays, and `_stamp` refuses it unless the
        # dict is a skip or an error. `bool()` here was the hole: "false" is True.

        # `gate` is a required field on Verdict and check-style dicts spell it
        # `check` (or omit it), so seed it before from_dict; _stamp overwrites it
        # from the spec a moment later either way.
        data.setdefault("gate", spec.id)
        try:
            return Verdict.from_dict(data)
        except (TypeError, ValueError) as exc:
            return Verdict(
                gate=spec.id,
                error=f"gate returned an unusable dict ({type(exc).__name__})",
                detail=str(exc),
            )

    if isinstance(result, (tuple, list)) and 1 <= len(result) <= 2:
        passed = _as_bool(result[0])
        if passed is None:
            return Verdict(
                gate=spec.id,
                error=_pass_value_error(result[0]),
                detail=f"the first element of a returned tuple is the pass flag; "
                       f"expected (bool, detail). {_PASS_VALUE_WHY}",
            )
        detail = "" if len(result) == 1 else str(result[1])
        return Verdict(gate=spec.id, passed=passed, detail=detail)

    if result is None:
        return Verdict(
            gate=spec.id,
            error="gate returned None",
            detail="a gate must return a Verdict, a bool, (bool, detail) or a dict; "
                   "returning nothing settles nothing",
        )

    return Verdict(
        gate=spec.id,
        error=f"gate returned an unusable {type(result).__name__}",
        detail="expected a Verdict, a bool, (bool, detail) or a dict",
    )


def _reject_non_finite(verdict: Verdict, spec: GateSpec) -> Verdict:
    """A gate that could not measure must not report a number.

    ``float("nan")`` is the most dangerous value a gate can return, because a NaN
    satisfies NOTHING: ``nan <= limit`` and ``nan > limit`` are BOTH False. A claim
    resting on one can neither pass nor fail, so it sits in the report looking
    checked while being unfalsifiable — the exact shape of the failure this project
    exists to prevent, arriving through a number instead of a skip.

    It is not hypothetical. A gate wrapped its measurement in ``except: return nan``
    and shipped; its dependency was missing on every machine but its author's, so it
    returned NaN for everyone else and was inert from the day it was written. It
    never hid a specific defect. It would have hidden any defect, for anyone else.

    So: "I could not measure" is a SKIP with a reason, or an error. It is never a
    number. Infinity is refused on the same grounds — it compares, but it is what a
    division by zero returns, and a gate that divided by zero has not measured
    anything either.

    The refused value is also REMOVED from the record (the field set to None, its
    repr kept in ``error``): an error verdict still carrying ``measured=nan`` is a
    NaN on its way to a JSON writer, and every writer from Phase 1 on is strict.

    And a measurement must be a number in the first place. What got through: this
    guard used to ``continue`` past anything ``float()`` refused, so
    ``measured="n/a"`` was filed as a measurement and reached the report as one.
    ``numbers.Real`` is the test because numpy registers its scalars there
    (``np.float32``, ``np.int64``); ``isinstance(x, (int, float))`` would refuse
    them. A ``bool`` — or a numpy bool — is a Real by inheritance and not a
    quantity, so it is refused too: ``measured=True`` is a gate that put its pass
    flag in the wrong field.
    """
    problems: list[str] = []
    notes: list[str] = []
    emptied: dict[str, Any] = {}
    for field in ("measured", "limit"):
        value = getattr(verdict, field, None)
        if value is None:
            continue
        if not isinstance(value, numbers.Real) or _as_bool(value) is not None:
            emptied[field] = None
            problems.append(f"gate reported {field}={_show(value)} "
                            f"({type(value).__name__}); a measurement must be a real "
                            f"number or None")
            notes.append(f"a {type(value).__name__} in {field} cannot be compared "
                         f"against a limit, so it is not a measurement")
            continue
        if _finite(value):
            continue
        emptied[field] = None
        problems.append(f"gate reported a non-finite {field} ({_show(value)})")
        notes.append(f"a non-finite {field} can neither pass nor fail its acceptance "
                     f"(nan <= x and nan > x are both False), so nothing was measured")
    if not problems:
        return verdict
    detail = [verdict.detail] if verdict.detail else []
    if verdict.error:
        detail.append(f"the gate's own error: {verdict.error}")
    detail.extend(notes)
    detail.append("If the quantity could not be obtained, SKIP with a reason "
                  "instead of returning a number.")
    return dataclasses.replace(
        verdict,
        passed=False,
        error="; ".join(problems),
        detail=" | ".join(detail),
        **emptied,
    )


def _finite(value: numbers.Real) -> bool:
    """Is a real number finite? An integer always is — ``math.isfinite`` would
    raise on one too large for a float, and 10**400 is large, not infinite."""
    if isinstance(value, numbers.Integral):
        return True
    try:
        return math.isfinite(float(value))
    except (OverflowError, TypeError, ValueError):
        # A Real too large for a float (a huge Fraction) would become inf the
        # moment it is written as JSON; refuse it here, where it is still named.
        return False


class _Clock:
    """Wall and CPU time since construction: ``(elapsed_s, cpu_s)``.

    CPU is the ``os.times()`` delta of user + system time, the process's
    children INCLUDED. The children are the point: omc, a mesher, a solver — the
    expensive gates do their work in a subprocess, and the process's own CPU
    time would call them free. (Children are counted once reaped, which every
    ``subprocess.run`` does before it returns. Windows reports no children
    times; there ``cpu_s`` is the process's own.)
    """

    __slots__ = ("wall", "cpu")

    def __init__(self) -> None:
        self.wall = time.perf_counter()
        self.cpu = _cpu_now()

    def spent(self) -> tuple[float, float]:
        return time.perf_counter() - self.wall, _cpu_now() - self.cpu


def _cpu_now() -> float:
    t = os.times()
    return t.user + t.system + t.children_user + t.children_system


def run_gate(spec: GateSpec, fn: Callable[[GateContext], Any], ctx: GateContext, *,
             trace: GateTrace | None = None) -> Verdict:
    """Run one gate and return a verdict that is honest about what happened.

    Four outcomes, and keeping them four instead of two is the whole job:

    * **pass**    — it ran and the acceptance held
    * **fail**    — it ran and the acceptance did not hold  (fix the design)
    * **skip**    — its tooling is absent, so it did not run (install the tool)
    * **error**   — it ran and crashed                      (fix the gate)

    An error is NOT a failure. A crashed gate has proven nothing about the
    design, and collapsing the two would invite someone to "fix" a broken import
    by changing a wall thickness. The two stay distinguishable in the record
    (``error`` set, ``passed`` forced False, ``Verdict.ok`` False either way) so
    that a skip can resolve its claim to BLOCKED, a crash to FAIL, and neither
    can ever be mistaken for the gate having measured something.

    **The gate never sees ``ctx`` itself** (rule 5 in the module docstring). In
    order: availability, with no trace — a skip never calls ``fn`` and reads
    nothing; then ``pack`` and ``key_scope`` stamped from the spec; then the
    traced view — ``params`` a read-only ``ParamTrace``, ``ledger`` a
    ``LedgerView`` without verdicts, ``extra`` the gate's own shallow copy,
    ``model`` a ``ModelProxy`` (``None`` stays ``None``), ``memo`` the
    sweep's ``SweepMemo`` (only ``load_file`` opens it) and ``trace`` set —
    and ``fn`` runs inside ``verdicts.tracing(trace)``, so the files it opens
    are recorded too. ``trace=None`` makes a throwaway trace: the
    view is read-only on every path, not only when someone is recording. This
    function writes no file, consults no cache and enforces no admission; the
    caller that keys a verdict by its trace (the sweep) does all of that.

    The trace window closes BEFORE a crash's traceback is formatted: formatting
    opens every frame's source through ``linecache``, and those reads are the
    formatter's, not the gate's (packs:H2).

    Availability is checked BEFORE the clock starts, so a skipped gate reports
    ~0s and 0 CPU rather than the cost of discovering the tool is missing.
    ``duration_s`` is wall time; ``cpu_s`` is CPU time with the gate's child
    processes included (see ``_Clock``).

    ``Exception`` is caught; ``KeyboardInterrupt`` is not — a Ctrl-C during a
    twenty-minute solver gate must stop the sweep, not be filed as a verdict and
    marched on from.

    ``SystemExit`` and ``GeneratorExit`` ARE caught, even though they are
    ``BaseException`` and not ``Exception``. That exception to the exception is
    there because of what got through: a gate body that reached ``sys.exit()``
    — pack code calling ``argparse`` on its own arguments, or a dependency whose
    import guard exits when a tool is absent — unwound straight past this
    handler, out of ``run_all``, and out of `atompipe check`, which then exited
    **0 having printed and recorded nothing at all**. A sweep that proves nothing
    and reports success is the exact failure this module exists to make
    impossible, so a gate that exits the process is filed as an errored gate
    (fix the gate) and the remaining gates still run.
    """
    ok, reason = availability(spec)
    if not ok:
        return _stamp(
            Verdict(gate=spec.id, passed=False, skipped=True, skip_reason=reason),
            spec,
            0.0,
        )

    # Which pack is asking. Stamped HERE rather than left to the caller because
    # the answer is a property of the gate about to run, not of the sweep: one
    # context is handed to forty gates from five packs, and `ctx.param` has to
    # resolve `cad.bbox_mm` for one of them and `fdm.bbox_mm` for the next. A
    # caller's own value is overwritten for the same reason — the running gate
    # defines its own namespace, and nobody else can.
    ctx = dataclasses.replace(ctx, pack=spec.pack, key_scope=scope_of(spec.id))
    if trace is None:
        trace = GateTrace()
    view = traced_context(ctx, trace, readonly=True)

    clock = _Clock()
    try:
        # Every memo the gate's code holds at module level is emptied first,
        # outside the window: its reads happen in this run, or not at all. A
        # warm lru_cache — warmed by this gate's own control a moment ago, in
        # this process — once made the real run open nothing (modelio.clear_caches).
        # A memo that will not empty is this run's error, never a skipped step.
        modelio.clear_caches(fn)
        with tracing(trace):
            result = fn(view)
    except (SystemExit, GeneratorExit) as exc:       # BaseException: see docstring.
        # Ordered BEFORE the Exception clause on purpose — neither of these is an
        # Exception subclass, so the clause below would never see them and they
        # would leave the sweep silently. KeyboardInterrupt is deliberately NOT
        # in this tuple: Ctrl-C must still stop a long run.
        elapsed, cpu = clock.spent()
        stack = traceback.format_exc()               # the window is already closed
        _replay_reads(ctx.params, trace)
        if isinstance(exc, SystemExit):
            what = (
                f"gate called sys.exit({exc.code!r}); a gate must return a verdict, "
                f"not exit the process"
            )
        else:
            what = (
                "gate raised GeneratorExit; a gate must return a verdict, not unwind "
                "the process"
            )
        return _stamp(
            Verdict(gate=spec.id, passed=False, error=what, detail=_trace_tail(stack)),
            spec,
            elapsed,
            cpu,
        )
    except Exception as exc:                         # noqa: BLE001 - deliberate: see docstring
        elapsed, cpu = clock.spent()
        stack = traceback.format_exc()               # the window is already closed
        _replay_reads(ctx.params, trace)
        return _stamp(
            Verdict(
                gate=spec.id,
                passed=False,
                error=f"{type(exc).__name__}: {exc}",
                detail=_trace_tail(stack),
            ),
            spec,
            elapsed,
            cpu,
        )
    elapsed, cpu = clock.spent()
    _replay_reads(ctx.params, trace)
    return _stamp(_reject_non_finite(_normalise(result, spec), spec), spec, elapsed, cpu)


def _replay_reads(source: Any, trace: GateTrace) -> None:
    """Tell a caller's own recording mapping which keys the gate read.

    ``atompipe check`` hands ``run_all`` its params wrapped in a dict subclass
    that notes every key a gate asks for — that is where ``Param.gates``, "which
    gates read this number", comes from, and ``atompipe why`` prints it. The gate
    now reads a traced COPY, and a subclass never sees a copy being made: CPython
    copies a dict subclass's storage without calling one of its methods. What
    would have slipped through: every ``Param.gates`` empty after the first sweep,
    and ``why`` telling the reader no gate would notice a parameter three gates
    read. So the paths the trace recorded are replayed through the caller's own
    ``get`` once the gate is done — the first two levels, which is all that
    recorder ever followed. Nothing is replayed into a plain dict or a trace's own
    view (a fixture's host copy records through its own link). Harmless once
    ``Param.gates`` is fed from the recorded read sets instead (S-30), because
    then nothing hands ``run_all`` a recorder at all.

    A path is replayed whatever the gate went on to do: a gate that read a key and
    then crashed or skipped still read it.
    """
    if type(source) is dict or not isinstance(source, dict) or isinstance(source, ParamTrace):
        return
    for path in list(trace.params):
        if not path:
            continue
        try:
            value = source.get(path[0])
            if len(path) > 1 and isinstance(value, dict):
                value.get(path[1])
        except Exception:                            # noqa: BLE001 - a caller's mapping
            continue


# --------------------------------------------------------------------------- #
# prerequisites: the plan, and the one rule
# --------------------------------------------------------------------------- #
def plan(registry: Registry, selected: Iterable[GateSpec | str]) -> list[GateSpec]:
    """``selected`` and every registered prerequisite it reaches, in run order.

    **DFS postorder** (P2.2-D3): gates are visited in registration order, each
    gate's needs in their declared order, and a gate is placed after all of
    its prerequisites. With no ``needs`` anywhere this is exactly registration
    order. On the beam pack it is input_sanity, model_validity, deflection, …
    (S-52). A need that is not registered is skipped here — the rule reads it
    as "not registered" (:func:`prerequisite_root`).

    **The selection expands** to its prerequisite closure (D15): ``check --only
    beam.deflection`` runs the guard and the guard's guard too. Naming a gate
    opts into its cost, and its prerequisites cost no more (the registry
    refuses an inversion). *Rejected:* no expansion — the sweep would need
    ``resolve``'s reading of an unselected prerequisite (a second path), and
    ``check --only D`` just after an edit would read D stale until a full check.

    Acyclicity and tier order are checked again here, and a violation raises
    ``AtompipeError``: a spec can reach the registry's private dict past
    :meth:`Registry.register` (defence in depth, the reasoning of
    :func:`_control_gap`), and a sweep must never run a dependent before a
    prerequisite or loop. *Rejected:* Kahn's sort with ties to the lowest index
    (unrelated gates jump ahead of a guard: the bracket's ``bearing`` would run
    first); alphabetical order (S-52's own cause).
    """
    specs = {spec.id: spec for spec in registry.specs()}
    wanted: set[str] = set()
    stack = [item.id if isinstance(item, GateSpec) else str(item) for item in selected]
    while stack:
        gid = stack.pop()
        if gid in wanted or gid not in specs:
            continue
        wanted.add(gid)
        stack.extend(specs[gid].needs or ())

    out: list[GateSpec] = []
    state: dict[str, str] = {}

    def visit(gid: str, path: list[str]) -> None:
        if state.get(gid) == "done":
            return
        if state.get(gid) == "open":
            loop = path[path.index(gid):]
            raise AtompipeError(
                f"the prerequisites form a cycle, {' -> '.join(loop)} — the registry "
                f"refuses one at registration, so a spec reached it some other way; "
                f"no gate in it can run before the others")
        state[gid] = "open"
        spec = specs[gid]
        for need in spec.needs or ():
            found = specs.get(need)
            if found is None:
                continue
            if int(found.tier) > int(spec.tier):
                held = registry.get(gid)
                raise AtompipeError(_inversion(gid, spec, need, found,
                                               held[1] if held is not None else None))
            visit(need, path + [need])
        state[gid] = "done"
        out.append(spec)

    for gid in registry.ids():
        if gid in wanted:
            visit(gid, [gid])
    return out


class Reading(NamedTuple):
    """What the rule knows of one gate: its effective ``verdict`` (``None`` —
    unrun), whether it is ``current`` (a pass whose inputs have not moved), and,
    for a reading marked not current through ITS prerequisite, that ``root``,
    its ``root_kind`` and ``why`` — so a dependent of a dependent names the root,
    not the gate in between. ``why`` is a not-current reading's own stale reason."""

    verdict: Verdict | None
    current: bool = True
    root: str = ""
    root_kind: str = ""
    why: str = ""


class Unmet(NamedTuple):
    """A gate's prerequisites are not established: ``root`` — the one its
    message names, found transitively — and its ``kind`` (a
    ``PrerequisiteKind``), ``roots`` — every root of the winning class, in rank
    order, which a pruned verdict carries as ``blocked_by`` — and ``why``, a
    not-current root's own stale reason."""

    root: str
    kind: str
    roots: tuple = ()
    why: str = ""

    @property
    def negative(self) -> bool:
        """Not run, Skipped (D6) — as against kept and marked Stale (D7)."""
        return str(self.kind) in NEGATIVE_KINDS


def prerequisite_root(spec: GateSpec, readings: dict[str, Reading],
                      registry: Registry) -> Unmet | None:
    """Whether ``spec``'s prerequisites are established — the ONE decision, which
    the sweep (:func:`run_all`) and the resolver (``verdicts.apply_prerequisites``)
    both call. Pure: it reads ``readings`` (``{gate id: Reading}``) and which ids
    ``registry`` holds, and nothing else. ``None`` when every need is established.

    A prerequisite is **established** when its effective verdict is a pass and
    current (P2.2-D5). Otherwise, per need, in ``needs`` order:

    * not registered -> ``not-registered``;
    * a verdict the rule itself made (``blocked_by`` set) -> its first root and
      its kind, transitively: a chain P -> Q -> R names P on R;
    * refused at its version (``unqualified``) -> ``unqualified``; a crash ->
      ``errored``; a skip (its tool missing, or it skipped itself) ->
      ``skipped``; a fail, current or not (D-08: an invalidated fail is still a
      fail) -> ``failed``;
    * a pass marked through its own prerequisite -> that root and kind; a pass
      not current -> ``invalidated``; no verdict, or one not yet qualified at
      any version (``verdicts.not_yet``, P2.3) -> ``unrun``.

    The negative class wins over the not-current one; within a class
    ``PrerequisiteKind``'s rank order, then ``needs`` order. *Rejected:* passes
    only, ignoring currency (a dependent's pass would count downstream of a
    guard whose own inputs moved, against invariant 10); treating every unmet
    need alike (a guard invalidated by an edit would turn every claim it guards
    Skipped until the next check — see ``verdicts.apply_prerequisites``, D7).
    """
    negative: list[tuple[int, int, str, tuple]] = []
    stale: list[tuple[int, int, str, str, str]] = []
    for index, need in enumerate(spec.needs or ()):
        if need not in registry:
            kind = PrerequisiteKind.NOT_REGISTERED
            negative.append((_KIND_RANK[kind], index, kind, (need,)))
            continue
        reading = readings.get(need)
        verdict = reading.verdict if reading is not None else None
        # Not yet qualified is unrun, never a refusal: the next check run
        # qualifies it. What slipped through the first cut of P2.3, which read
        # it `unqualified`: on a project never checked every guard was
        # negative, and each claim it guards read Skipped, "prerequisite not
        # established", where nothing had run at all.
        if verdict is None or not_yet(verdict):
            stale.append((_KIND_RANK[PrerequisiteKind.UNRUN], index, PrerequisiteKind.UNRUN,
                          need, ""))
            continue
        if verdict.blocked_by:
            kind = (verdict.blocked_kind if str(verdict.blocked_kind) in NEGATIVE_KINDS
                    else PrerequisiteKind.SKIPPED)       # an unknown kind: the quiet one
            negative.append((_KIND_RANK[str(kind)], index, kind, tuple(verdict.blocked_by)))
            continue
        if getattr(verdict, "unqualified", ""):
            kind = PrerequisiteKind.UNQUALIFIED
        else:
            kind = {"error": PrerequisiteKind.ERRORED, "skipped": PrerequisiteKind.SKIPPED,
                    "fail": PrerequisiteKind.FAILED}.get(verdict.outcome)
        if kind is not None:
            negative.append((_KIND_RANK[kind], index, kind, (need,)))
        elif reading.root:
            stale.append((_KIND_RANK[str(reading.root_kind)], index, reading.root_kind,
                          reading.root, reading.why))
        elif not reading.current:
            stale.append((_KIND_RANK[PrerequisiteKind.INVALIDATED], index,
                          PrerequisiteKind.INVALIDATED, need, reading.why))
    if negative:
        negative.sort(key=lambda item: (item[0], item[1]))
        roots = tuple(dict.fromkeys(root for item in negative for root in item[3]))
        first = negative[0]
        return Unmet(first[3][0], str(first[2]), roots)
    if stale:
        stale.sort(key=lambda item: (item[0], item[1]))
        first = stale[0]
        return Unmet(first[3], str(first[2]), tuple(dict.fromkeys(i[3] for i in stale)),
                     first[4])
    return None


def blocked(spec: GateSpec, unmet: Unmet) -> Verdict:
    """The verdict of a gate not run because a prerequisite is not established —
    the ONE producer of a prerequisite skip (P2.2-D6, D9): ``skipped``, not
    passed, ``blocked_by`` its roots and ``blocked_kind`` the first's kind, a
    ``skip_reason`` of ``prerequisite failed: <root>`` when that root failed and
    ``prerequisite not established: <root> (<why>)`` otherwise. No cost and no
    rho: nothing ran, and it is never cached, remembered or logged (the sweep's
    ``_pruned_row`` writes nothing)."""
    if not unmet.negative:
        raise ValueError(f"{spec.id}: a not-current prerequisite ({unmet.kind}) keeps its "
                         f"dependent's verdict; it is marked, never replaced")
    if str(unmet.kind) == PrerequisiteKind.FAILED:
        reason = f"{PREREQUISITE_FAILED}: {unmet.root}"
    else:
        reason = (f"{PREREQUISITE_NOT_ESTABLISHED}: {unmet.root} "
                  f"({_KIND_WORDS[PrerequisiteKind(str(unmet.kind))]})")
    return Verdict(gate=spec.id, claims=list(spec.claims or ()), tier=Tier(int(spec.tier)),
                   pack=spec.pack or "", passed=False, skipped=True, skip_reason=reason,
                   blocked_by=list(unmet.roots), blocked_kind=str(unmet.kind))


def mark_reason(unmet: Unmet) -> str:
    """The stale reason a dependent carries under a not-current prerequisite
    (P2.2-D7): ``prerequisite <root> invalidated: <what moved>`` or
    ``prerequisite <root> unrun``."""
    if str(unmet.kind) == PrerequisiteKind.UNRUN:
        return PREREQUISITE_UNRUN.format(root=unmet.root)
    text = PREREQUISITE_INVALIDATED.format(root=unmet.root)
    return f"{text}: {unmet.why}" if unmet.why else text


def _pruned_default(spec: GateSpec, unmet: Unmet) -> Verdict:
    """:func:`run_all`'s pruned verdict with no hook: the dependent's own missing
    tool — the one reading of its own a loop that does not run it can have —
    under the resolver's rule (``verdicts._under_rule``): it stands unless the
    root crashed, else :func:`blocked`. What slipped through (review of P2.2):
    this kept its own copy, "the missing tool stands", so a crashed root went
    quiet here too behind the dependent's missing tool."""
    from . import verdicts as _verdicts      # the module, so the rule is one object
    ok, reason = availability(spec)
    own = None if ok else _stamp(
        Verdict(gate=spec.id, passed=False, skipped=True, skip_reason=reason), spec, 0.0)
    return _verdicts._under_rule(spec, own, unmet)


# --------------------------------------------------------------------------- #
# running a sweep
# --------------------------------------------------------------------------- #
def _selected(registry: Registry, max_tier: int, only: str | Iterable[str] | None) -> list[GateSpec]:
    """Resolve the tier + ``only`` filters to a concrete, ordered gate list.

    ``only`` accepts an exact id, a pack name, or an fnmatch pattern
    (``cad.*``), and matching a nothing raises. That last part is the important
    one: a typo'd ``--only cad.watertigt`` that quietly ran zero gates and
    printed nothing would be reported by the CLI as a clean sweep, which is the
    same lie as a validator that exits 0. An empty registry is allowed through
    (there is nothing to typo), and the tier filter alone may legitimately select
    nothing — a project whose gates are all tier 2 running a tier-0 loop.
    """
    within_tier = registry.by_tier(max_tier)
    if only is None:
        return within_tier

    patterns = [only] if isinstance(only, str) else [str(o) for o in only]
    patterns = [p.strip() for p in patterns if str(p).strip()]
    if not patterns:
        return within_tier

    all_specs = registry.specs()
    chosen: list[GateSpec] = []
    unmatched: list[str] = []
    for pattern in patterns:
        hits = [
            s for s in all_specs
            if s.id == pattern
            or (s.pack and s.pack == pattern)
            or fnmatch.fnmatchcase(s.id, pattern)
        ]
        if not hits:
            unmatched.append(pattern)
        chosen.extend(hits)

    if unmatched and all_specs:
        known = ", ".join(s.id for s in all_specs[:12])
        more = "" if len(all_specs) <= 12 else f" (+{len(all_specs) - 12} more)"
        raise AtompipeError(
            f"no gate matches {', '.join(repr(u) for u in unmatched)} — running zero gates "
            f"and calling it a clean sweep is the failure this tool exists to prevent. "
            f"Known gates: {known}{more}"
        )

    # An explicitly named gate is run even if it sits above the tier ceiling:
    # naming it IS the opt-in to its cost. Order stays registration order.
    seen: set[str] = set()
    ordered: list[GateSpec] = []
    for spec in all_specs:
        if spec.id in {c.id for c in chosen} and spec.id not in seen:
            seen.add(spec.id)
            ordered.append(spec)
    return ordered


def run_all(
    registry: Registry,
    ctx: GateContext,
    *,
    max_tier: int = 0,
    only: str | Iterable[str] | None = None,
    on_verdict: Callable[[Verdict], None] | None = None,
    before: Callable[[GateSpec, Callable[[GateContext], Any]], Verdict | None] | None = None,
    after: Callable[[GateSpec, Callable[[GateContext], Any], Verdict, GateTrace], Any]
    | None = None,
    current: Callable[[str], bool] | None = None,
    pruned: Callable[[GateSpec, Callable[[GateContext], Any], Unmet], Verdict] | None = None,
    marked: Callable[[GateSpec, Verdict, Unmet], Any] | None = None,
) -> list[Verdict]:
    """Run every selected gate and its prerequisites, in :func:`plan`'s order, and
    return their verdicts in that order — the run order, which is registration
    order when nothing ``needs`` anything.

    ``max_tier`` defaults to 0 because the default sweep is the inner loop, and
    the inner loop must stay in seconds (rule 10). A caller who wants the
    twenty-minute solver asks for it.

    ``on_verdict`` is called with each verdict the moment it lands, so the CLI
    can stream one line per gate instead of going silent for the length of the
    slowest tier-2 gate in the list. It is called for skips and errors too —
    especially for skips, which are the rows a user most needs to see scroll
    past, because they are the ones that will resolve to BLOCKED.

    A gate that raises does not stop the sweep (it becomes an error verdict);
    an ``on_verdict`` callback that raises DOES, because that is our bug and a
    half-reported sweep must not look like a whole one.

    ``ctx.tier`` is reconciled with ``max_tier`` here rather than trusted: a
    context that says tier 0 while the sweep runs tier 2 would let a gate pick
    its cheap path during an expensive run, and nothing downstream would ever
    show the discrepancy.

    Rule 5 is re-asserted per spec rather than assumed from registration. It used
    to be the only thing closing the window: the registry stored its own copy of
    every spec but ``get()`` and ``specs()`` handed that copy out, so the control
    registration exists to guarantee was within reach of any code that ran
    between registration and the sweep — and ``specs()[0].claims.append(…)``
    widened what a verdict settled, which nothing here re-checked. Every exit now
    hands out a copy (:func:`_own_copy`), so the stored spec is reachable only
    through ``register(..., replace=True)``, :meth:`Registry.set_pack` and the
    registry's private dict. The re-check stays as defence in depth: it costs one
    attribute read per gate, and a registry is not the only thing that can hold a
    spec.

    **The hooks** are how a sweep that keys verdicts by what they read (the verdict
    cache) sits on this loop without a second copy of its order, tier and
    ``--only`` logic:

    * ``before(spec, fn)`` is asked after the control re-check, for every gate
      that passed it. A ``Verdict`` it returns IS that gate's verdict — a cached
      one, or a skip or refusal the caller decided — and ``fn`` is never called;
      ``None`` runs the gate. Anything else is the hook's bug and raises.
    * ``after(spec, fn, verdict, trace)`` is called for every gate that ran here,
      with the ``GateTrace`` of that run — a fresh one per gate, so no gate's
      reads are filed under another's. A ``Verdict`` it returns replaces the one
      it was handed (the same verdict with its ``rho`` set, say). It is not
      called for a verdict ``before`` supplied or the control re-check refused:
      nothing ran, so there is no trace. It runs before ``on_verdict``, which
      streams the final verdict.

    **Prerequisites** (rule 6): before a gate with ``needs`` runs,
    :func:`prerequisite_root` reads the verdicts this loop has produced so far
    (each prerequisite ran first: :func:`plan`). Under a NEGATIVE root —
    failed, errored, skipped, unqualified, not registered — neither ``before``,
    ``fn`` nor ``after`` is called, nor the gate's control: the verdict is
    ``pruned(spec, fn, unmet)``'s, by default the gate's own availability skip
    if its tool is missing and the root did not crash, else :func:`blocked`.
    Under a NOT-CURRENT root (invalidated, unrun) the gate runs as usual, and
    ``marked(spec, verdict, unmet)`` is told, so the caller can say the verdict
    is not current.
    ``current(gate_id)`` says whether a prerequisite's verdict is current; with
    no hook, every verdict this loop produced is.

    ``ctx.memo`` — the file memo behind :meth:`GateContext.load_file` — is one
    ``verdicts.SweepMemo`` for the whole sweep (fresh when the caller brought
    none; a plain dict the caller brought is wrapped, its entries shared), the
    same handle in every gate's view, and never left on the caller's context: a
    memo that outlived its sweep would serve one sweep's bytes to the next.
    """
    selected = plan(registry, _selected(registry, max_tier, only))
    if int(ctx.tier) != int(max_tier):
        ctx = dataclasses.replace(ctx, tier=int(max_tier))
    ctx = dataclasses.replace(ctx, memo=sweep_memo(ctx.memo))
    if ctx.out_dir:
        ensure_dir(ctx.out_dir)      # once, up front: gates cite files in it

    out: list[Verdict] = []
    readings: dict[str, Reading] = {}
    for spec in selected:
        entry = registry.get(spec.id)
        if entry is None:            # concurrent unregister; nothing else can do this
            raise AtompipeError(f"gate {spec.id!r} disappeared from the registry mid-sweep")
        live, fn = entry
        gap = _control_gap(live)
        unmet = prerequisite_root(live, readings, registry) if live.needs else None
        if not gap and unmet is not None and unmet.negative:
            # Not run: no before, no fn, no after, no control (D6).
            verdict = (pruned(live, fn, unmet) if pruned is not None
                       else _pruned_default(live, unmet))
            if not isinstance(verdict, Verdict):
                raise TypeError(f"run_all's pruned() returned a {type(verdict).__name__} "
                                f"for {live.id!r}; it returns a Verdict")
        elif gap:
            verdict = _stamp(
                Verdict(
                    gate=live.id,
                    passed=False,
                    error=f"{live.id} was registered with a negative control and {gap}",
                    detail="a gate nothing proves can fail is a logger; refusing to "
                           "sweep it rather than file a verdict it cannot support — "
                           "restore the control and run `atompipe gate selftest`",
                ),
                live,
                0.0,
            )
        else:
            verdict = before(live, fn) if before is not None else None
            if verdict is not None and not isinstance(verdict, Verdict):
                raise TypeError(f"run_all's before() returned a {type(verdict).__name__} "
                                f"for {live.id!r}; it returns a Verdict or None")
            if verdict is None:
                trace = GateTrace()
                verdict = run_gate(live, fn, ctx, trace=trace)
                if after is not None:
                    replaced = after(live, fn, verdict, trace)
                    if isinstance(replaced, Verdict):
                        verdict = replaced
        if unmet is not None and not unmet.negative:
            readings[live.id] = Reading(verdict, False, unmet.root, str(unmet.kind),
                                        unmet.why)
            if marked is not None:
                marked(live, verdict, unmet)
        else:
            readings[live.id] = Reading(verdict, bool(current(live.id)) if current else True)
        out.append(verdict)
        if on_verdict is not None:
            on_verdict(verdict)
    return out


# --------------------------------------------------------------------------- #
# negative controls
# --------------------------------------------------------------------------- #
def _looks_like_path(ref: str) -> bool:
    """Is this fixture reference a file path rather than ``module:function``?

    ``.py`` anywhere, or a path separator, means path. The ``module:function``
    form has neither. A Windows ``C:\\x\\bad.py`` hits the ``.py`` test first,
    which is why that test comes first.
    """
    return ref.endswith(".py") or "/" in ref or os.sep in ref


#: What an unusable control costs, by which control it is — the tail of every
#: fixture-loading message ("… until it does", "… until it loads"). In GLOSSARY
#: §2's words: a known-bad control shows an evaluator can fail, a known-good one
#: that it passes a good design. What slipped through (review of P2.3): one
#: message for both, "the gate cannot be proven able to fail", on a known-good
#: control's typo. *Rejected:* routing them through ``report.HUMAN`` — an
#: ``AtompipeError`` is worded where it is raised, as every one in the spine is,
#: and these name a file and its fix, not a status, an outcome or a
#: qualification fact; the halves' names are GLOSSARY §2's terms.
_UNUSABLE_COST = {"known-bad": "the evaluator cannot be shown able to fail until it",
                  "known-good": "the evaluator cannot be shown to pass a good design until it"}


def _load_py_file(path: str, root: str = "", name: str = "", *,
                  half: str = "known-bad") -> Any:
    """Import a standalone .py file as a private module and return it.

    The module name is salted with a hash of the absolute path so two packs can
    each ship ``selftest/known_bad.py`` without the second one silently getting
    the first one's already-cached module — a collision that would make a gate
    selftest itself against somebody else's fixture and still look green.

    Through ``modelio.load_source_module``, like every other piece of code a
    verdict depends on: the bytes on disk are the bytes that run, and the module
    is served from ``sys.modules`` only while every file it ran still hashes the
    same. What slipped through before: this cached by path alone, forever, so a
    fixture edited in-process — a test that turns a control into a no-op to prove
    admission notices — re-ran the OLD fixture under the new one's name (S-26).
    ``root`` (the project or pack directory the fixture resolved against) is the
    closure's root when the file lies under it, so the fixture's own imports from
    the project — the bracket's fixtures ``import bracket`` from ``model/`` — are
    recorded with it. ``name`` is a ``module:function`` fixture's own dotted
    name (:func:`_import_fixture_module`): its relative imports resolve against
    its package, exactly as ``import name`` would have resolved them.
    """
    absolute = os.path.abspath(path)
    module_name = name or f"_atompipe_fixture_{short_hash(absolute, 10)}"
    base = os.path.abspath(root) if root else ""
    roots = [base] if base and (absolute == base or absolute.startswith(base + os.sep)) else []
    try:
        return modelio.load_source_module(absolute, name=module_name, roots=roots)
    except SystemExit as exc:
        # Not an Exception, so the clause below cannot see it. A fixture module
        # that exits at import — a dependency's "tool not installed" guard, say —
        # would otherwise take the whole `gate selftest` run down with whatever
        # exit code it chose, including 0. "Every control passed" and "the process
        # died during the first control" must never render the same.
        raise AtompipeError(
            f"{half} control fixture {path} called sys.exit({exc.code!r}) while importing "
            f"— a fixture builds its input, it does not exit the process; "
            f"{_UNUSABLE_COST[half]} stops"
        ) from exc
    except Exception as exc:                     # noqa: BLE001 - user's fixture code
        raise AtompipeError(
            f"{half} control fixture {path} failed to import ({type(exc).__name__}: {exc}) "
            f"— {_UNUSABLE_COST[half]} loads"
        ) from exc


def load_fixture(ref: str, root: str) -> Any:
    """Resolve a negative-control reference to the callable that builds it.

    Two accepted spellings, both from ``NegativeControl.fixture``:

    * ``"selftest/holed_mesh.py"`` — a file with a ``make(ctx)`` function.
      Relative paths resolve against ``root`` (the project root, or the pack
      directory when the caller knows it — see :func:`selftest`).
    * ``"pack.mod:make_brick"`` — an importable module and a function in it.
      A file path may also carry an explicit function: ``"selftest/x.py:other"``.

    Returns the callable, NOT the fixture. The caller invokes it with the real
    ``GateContext`` so the fixture can build its known-bad input relative to the
    real one — a control that ignores the context ends up testing a constant.

    Every failure here is an ``AtompipeError``: a missing or broken fixture is a
    pack-authoring mistake the user can fix, and it must surface loudly rather
    than degrade into "control unavailable, assume the gate is fine". That
    assumption is the one this module exists to refuse.

    Both forms load code the way every other piece of code a verdict depends on
    is loaded — fresh bytes, a recorded closure (``modelio.load_source_module``)
    — wherever the module is code; see :func:`_import_fixture_module` for the
    ``module:function`` form, and what slipped through before it.
    """
    return _load_fixture(ref, root)[0]


def _load_fixture(ref: str, root: str, *,
                  half: str = "known-bad") -> tuple[Callable[..., Any], Any]:
    """:func:`load_fixture`, returning ``(the callable, the module the
    reference named)``. The control records the closure of that MODULE, not of
    the module that defines the callable: a fixture file that re-exports
    ``make`` from a helper is keyed by the file the reference names — the one
    an edit to point it at another helper moves — and its closure already holds
    the helper's (``modelio``'s walk of its globals).

    ``half`` is the control being built — ``"known-bad"`` or ``"known-good"`` —
    and every message names it and what its loss costs (``_UNUSABLE_COST``).
    What slipped through (review of P2.3): the known-good control reused this
    loader worded for the known-bad one, so a typo in ``good=`` read
    "negative-control fixture … does not exist … The gate cannot be proven
    able to fail" — the wrong control, the wrong half, and two GLOSSARY §2
    Never-says on a claim's row."""
    ref = (ref or "").strip()
    if not ref:
        raise AtompipeError(f"{half} control has an empty fixture reference")

    func_name = ""
    target = ref
    if ":" in ref:
        head, _, tail = ref.rpartition(":")
        # Guard the Windows drive letter: "C:\x.py" must not split into ("C", "\x.py").
        if head and tail.isidentifier() and len(head) > 1:
            target, func_name = head, tail

    if _looks_like_path(target):
        path = target if os.path.isabs(target) else os.path.join(root or os.curdir, target)
        if not os.path.isfile(path):
            raise AtompipeError(
                f"{half} control fixture {target!r} does not exist "
                f"(looked in {os.path.abspath(path)}) — {_UNUSABLE_COST[half]} does"
            )
        module = _load_py_file(path, root or os.curdir, half=half)
        wanted = func_name or "make"
        source = target
    else:
        module = _import_fixture_module(target, root or os.curdir, half=half)
        wanted = func_name or "make"
        source = target

    fn = getattr(module, wanted, None)
    if fn is None:
        raise AtompipeError(
            f"{half} control fixture {source!r} has no {wanted}() function — a fixture "
            f"exposes `def make(ctx):` returning a GateContext or a dict merged into "
            f"ctx.extra"
        )
    if not callable(fn):
        raise AtompipeError(f"{half} control fixture {source}:{wanted} is not callable")
    return fn, module


def _import_fixture_module(name: str, root: str, *, half: str = "known-bad") -> Any:
    """``import name`` for a ``module:function`` fixture — fresh and recorded
    when ``name`` is a plain module of Python source that is code
    (``modelio.is_code`` against ``root``), through the stock import otherwise.

    What slipped through (admission review, round 1, C): this form was
    ``importlib.import_module`` alone. No loader recorded the fixture's closure,
    so its control entry filed ``fixture: {"files": {}}`` — and an empty
    mapping never moves, so the lookup hint always held. A fixture outside
    ``selftest/`` is not in the control's static part either, so its code was
    keyed nowhere: ``fixtures/bad.py`` defused 400 -> 40 mm, ``check`` said
    ``0 executed, 1 cached`` and exited 0 while ``gate selftest`` said PASSED
    its own known-bad fixture. The stock import also ran a same-size,
    same-second edit's old ``.pyc`` (S-26), so re-running the fixture alone
    would still have built 400 mm.

    What stays with the stock import — an installed module (an instrument), a
    package's ``__init__``, an extension, a namespace package — records no
    closure, and ``verdicts`` files that as ``UNRECORDED_FIXTURE``: a hint that
    never holds, so every ``check`` re-runs the fixture and compares what it
    builds. *Rejected:* that sentinel alone for every module fixture — one
    fixture run per gate per check forever, and the re-run still read the stale
    ``.pyc``. *Rejected:* digesting the defining file as ``code_digest`` does
    for an in-process gate — it misses what the file imports, and a hint that
    holds while the code under it moved is this defect again.
    """
    try:
        # find_spec("a.b") imports "a" — the stock import, as `import a.b`
        # would. Only the module the reference names is loaded fresh.
        found = importlib.util.find_spec(name)
    except ValueError:
        found = None                             # in sys.modules with no __spec__: as it was
    except ImportError as exc:
        raise AtompipeError(
            f"cannot import {half} control fixture module {name!r}: {exc}") from exc
    except SystemExit as exc:                    # BaseException: see _load_py_file
        raise AtompipeError(
            f"{half} control fixture module {name!r} called sys.exit({exc.code!r}) "
            f"while importing — {_UNUSABLE_COST[half]} stops"
        ) from exc
    except Exception as exc:                     # noqa: BLE001 - user's fixture code
        raise AtompipeError(
            f"{half} control fixture module {name!r} failed to import "
            f"({type(exc).__name__}: {exc})") from exc
    origin = getattr(found, "origin", None) if found is not None else None
    if (found is not None and found.has_location and isinstance(origin, str)
            and found.submodule_search_locations is None
            and isinstance(found.loader, importlib.machinery.SourceFileLoader)
            and modelio.is_code(origin, [root])):
        return _load_py_file(origin, root, name=name, half=half)
    try:
        return importlib.import_module(name)
    except ImportError as exc:
        raise AtompipeError(
            f"cannot import {half} control fixture module {name!r}: {exc}"
        ) from exc
    except SystemExit as exc:                    # BaseException: see _load_py_file
        raise AtompipeError(
            f"{half} control fixture module {name!r} called sys.exit({exc.code!r}) "
            f"while importing — {_UNUSABLE_COST[half]} stops"
        ) from exc
    except Exception as exc:                     # noqa: BLE001 - user's fixture code
        raise AtompipeError(
            f"{half} control fixture module {name!r} failed to import "
            f"({type(exc).__name__}: {exc})"
        ) from exc


def _fixture_root(spec: GateSpec, ctx: GateContext, fn: Callable[..., Any] | None = None,
                  root: str | None = None) -> str:
    """Where a relative fixture path is resolved from, most specific first.

    A pack's fixture is written ``selftest/steep_cone.py`` and lives in the PACK
    directory, not the project root — ``packs.validate`` resolves it against
    ``pack_dir`` and this must agree, or a control that validates cleanly would
    go missing the moment someone ran it. Four sources, in order:

    1. ``ctx.extra["pack_dirs"][spec.pack]`` — an explicit map from a caller that
       loaded several packs.
    2. ``ctx.extra["pack_dir"]`` — a caller working inside one pack.
    3. ``PACK_DIR`` on the gate function's own module. ``packs.load_gates`` sets
       it before executing each gate module, so this works with no cooperation
       from the caller at all, which is the case that actually happens. Else
       on a module that registered it (``modelio.registered_by``): a gate a
       factory in the pack's helper made is defined in a module no pack
       loaded, and its fixture was looked for in the project (Phase 1 review,
       ``p4``). Asked of ``verdicts._pack_dir_of``, the one answer: a
       ``PACK_DIR`` counts only from a module whose own file is under it — a
       project gate module that assigns itself a bundled pack's directory is
       a project's, here as for its owner and its mutation pass (review of
       P2.3, ``br1``: this copy read the global as given while the static
       part read it the same way, so the two agreed on the lie).
    4. ``root`` when the caller names one, else ``ctx.root`` — a project's own
       gates, whose ``selftest/`` sits beside the model. The sweep names the
       project root (P2.3): the context a control's fixture is handed is the
       project's known-good design, and a ``selftest/known_good.py`` that points
       its root at the design's own files (``selftest/good/``, say) moved where
       ``selftest/bad.py`` was looked for — "does not exist, looked in
       selftest/good/selftest/bad.py", every project control unusable.

    A fixture that resolves to nothing produces "does not exist, looked in
    <path>" from :func:`load_fixture` — naming the path it tried, because the
    alternative (a silent skip) would leave a gate unproven and looking fine.
    """
    extra = ctx.extra if isinstance(ctx.extra, dict) else {}
    pack_dirs = extra.get("pack_dirs")
    if isinstance(pack_dirs, dict) and spec.pack and pack_dirs.get(spec.pack):
        return str(pack_dirs[spec.pack])
    single = extra.get("pack_dir")
    if isinstance(single, str) and single:
        return single
    if fn is not None:
        pack_dir = _pack_dir_of(fn)
        if pack_dir:
            return pack_dir
    return root or ctx.root or os.curdir


def _build_control(spec: GateSpec, fn: Callable[[GateContext], Any], ctx: GateContext, *,
                   trace: GateTrace, out_dir: str | None, ref: str | None = None,
                   fixture_root: str | None = None
                   ) -> tuple[GateContext | None, dict[str, str] | None]:
    """Run ``spec``'s fixture: ``(the known-bad context, None)``, or ``(None,
    {"error", "detail"})`` saying why the control is unusable.

    The fixture receives a WRITABLE traced copy of ``ctx`` — its ``out_dir``
    replaced when one is given — never ``ctx`` itself. What would slip through
    otherwise: once a sweep runs controls (admission), the context a fixture is
    handed is the one every later gate reads, and a fixture that assigned
    ``ctx.params["span_mm"] = 99`` on it would have handed the next gate the
    known-bad span (core:§5.10). Every read through that copy — the fixture's,
    or the gate's on a context the fixture returned unchanged — is recorded in
    ``trace.host_reads`` (what a SEALED fixture never makes), and ``make`` runs
    inside ``tracing(trace)`` so the files it opens are on the trace too. Loading
    the fixture module is not in the window: what it runs and reads at import
    is the closure's business, and ``trace.fixture_code`` records the closure
    of the module the reference names — its code, and the files that code read
    while it ran (``CodeClosure.data``) — or ``None`` when the stock import
    system loaded it (an installed or a package ``module:function`` fixture),
    which ``verdicts`` files as a hint that never holds. What slipped through
    while the closure held code only (admission review, round 1, D): a fixture
    module that read its known-bad span from ``inputs/data/bad_span.json`` at
    import was keyed nowhere — the read came before this window, and
    ``inputs/`` is in no static walk — so 400 -> 40 mm stayed admitted.

    A fixture that builds its own context keeps it as built: cad-solid's fixtures
    assign params on contexts they made from the pack baseline (packs:H15), and
    those are theirs to edit. A dict is merged into the copy's ``extra``.

    ``ref`` is the fixture to build, when it is not the known-bad one: the
    declared known-good control (``NegativeControl.good``, P2.3), built by the
    same code under every same guard — a writable traced copy, the out dir
    replaced, its closure recorded, an exit caught — so the two halves of a
    qualification differ in their input and nothing else (D4).

    ``fixture_root`` is where a project's relative fixture ref resolves when
    ``ctx.root`` may not be the project's (``_fixture_root``, step 4).
    """
    nc = spec.negative_control
    half = "known-bad" if ref is None else "known-good"
    ref = nc.fixture if ref is None else ref
    host = traced_context(dataclasses.replace(ctx, out_dir=out_dir) if out_dir else ctx,
                          trace, readonly=False)
    try:
        make, module = _load_fixture(ref, _fixture_root(spec, ctx, fn, fixture_root),
                                     half=half)
        trace.fixture_code = modelio.code_closure(module)
        # The fixture's module-level memos too: re-verification runs a fixture
        # and a miss then runs it again, in one process, and a hit on the second
        # run would leave the file it built from out of the control's reads.
        modelio.clear_caches(make)
        with tracing(trace):
            built = make(host)
    except AtompipeError as exc:
        return None, {"error": f"{half} control unusable", "detail": str(exc)}
    except (SystemExit, GeneratorExit) as exc:
        # Same hole as run_gate's: neither is an Exception, so the clause below
        # would miss them and a fixture that exits would abort `gate selftest`
        # mid-run with nothing printed and nothing recorded. The honest reading of
        # a control that exits the process is that the control is unusable.
        code = exc.code if isinstance(exc, SystemExit) else None
        return None, {
            "error": f"fixture called sys.exit({code!r})" if isinstance(exc, SystemExit)
                     else "fixture raised GeneratorExit",
            "detail": f"{ref} must build its input and return it, not exit "
                      f"the process — the {half} control is unusable: "
                      f"{_UNUSABLE_COST[half]} stops",
        }
    except Exception as exc:                     # noqa: BLE001 - user's fixture code
        # Formatted after the window closed: the traceback's source reads are
        # the formatter's, not the fixture's.
        return None, {"error": f"fixture raised {type(exc).__name__}: {exc}",
                      "detail": _trace_tail(traceback.format_exc())}

    if isinstance(built, GateContext):
        return built, None
    if isinstance(built, dict):
        return host.with_extra(built), None
    if built is None:
        return None, {
            "error": "fixture returned None",
            "detail": f"{ref} must return a GateContext or a dict to merge into "
                      f"ctx.extra; returning nothing means the gate ran against whatever "
                      f"it was handed and any result is meaningless",
        }
    return None, {"error": f"fixture returned {type(built).__name__}",
                  "detail": f"{ref} must return a GateContext or a dict for ctx.extra"}


def run_fixture(spec: GateSpec, fn: Callable[[GateContext], Any], ctx: GateContext, *,
                trace: GateTrace | None, out_dir: str | None,
                fixture_root: str | None = None) -> GateContext:
    """Build ``spec``'s known-bad context, traced — and do NOT run the gate on it.

    The fixture half of :func:`selftest`, on the same terms: a writable traced
    copy of ``ctx`` (``out_dir`` replaced), ``make`` inside ``tracing(trace)``,
    ``trace.fixture_code`` set. It exists for re-verifying a control whose
    fixture's code moved but whose output may not have: compare what the fixture
    built with what the recorded control read, and when they match, the gate —
    possibly a twenty-minute solver — need not run again to know it would fire
    again. So it must never call ``fn``, and a test holds it to that.

    Raises ``AtompipeError`` when the control is unusable (no negative control, a
    missing or broken fixture, a fixture that exited, raised, or returned
    something other than a context or a dict), naming why — the caller then runs
    the whole control and learns the rest. Availability is the caller's: a
    fixture may itself need the tooling its gate declares.
    """
    nc = spec.negative_control
    if nc is None or not (nc.fixture or "").strip():
        raise AtompipeError(
            f"{spec.id} declares no negative control, so there is no fixture to run")
    if trace is None:
        trace = GateTrace(kind="control")
    bad_ctx, problem = _build_control(spec, fn, ctx, trace=trace, out_dir=out_dir,
                                      fixture_root=fixture_root)
    if problem is not None:
        raise AtompipeError(f"{spec.id}: {problem['error']} — {problem['detail']}")
    return bad_ctx


def run_good_fixture(spec: GateSpec, fn: Callable[[GateContext], Any], ctx: GateContext, *,
                     trace: GateTrace | None, out_dir: str | None,
                     fixture_root: str | None = None) -> GateContext:
    """Build ``spec``'s DECLARED known-good context (``NegativeControl.good``),
    traced — and do NOT run the gate on it. :func:`run_fixture`'s twin for the
    good half: the same builder, the same guards, the fixture handed exactly
    what the known-bad one is handed (P2.3-D4). Re-qualifying by values builds
    it alone and compares what it built with what the control entry recorded,
    so it must never call ``fn``.

    Raises ``AtompipeError`` when the gate declares no known-good fixture, or
    the fixture is unusable (missing, broken, exited, raised, returned neither
    a context nor a dict), naming why. A gate that declares none has its
    known-good control elsewhere — its pack's baseline, its project's
    ``selftest/known_good.py`` — and the caller builds that.
    """
    nc = spec.negative_control
    ref = (getattr(nc, "good", "") or "").strip() if nc is not None else ""
    if not ref:
        raise AtompipeError(
            f"{spec.id} declares no known-good fixture (negative_control.good), so there "
            f"is none to run")
    if trace is None:
        trace = GateTrace(kind="control")
    good_ctx, problem = _build_control(spec, fn, ctx, trace=trace, out_dir=out_dir, ref=ref,
                                       fixture_root=fixture_root)
    if problem is not None:
        raise AtompipeError(f"{spec.id}: {problem['error']} — {problem['detail']}")
    return good_ctx


def selftest(spec: GateSpec, fn: Callable[[GateContext], Any], ctx: GateContext, *,
             trace: GateTrace | None = None, out_dir: str | None = None,
             fixture_root: str | None = None, blank_model: bool = False) -> Verdict:
    """Run the gate against its own known-bad input. The verdict is on the GATE.

    ``passed=True`` here means *the gate correctly failed on input that is known
    to be bad* — it is a measurement of the instrument, not of the design. The
    canonical example: a simulator that runs its full scenario sweep a second time
    with the protective element removed and demands that the failure appear. That
    control proves nothing about the design and everything about the simulator.

    The returned verdict is filed under ``<gate id>#selftest`` and carries NO
    claims. Both are deliberate. Sharing the gate's id would overwrite its real
    verdict in ``Ledger.upsert_verdict``; attributing the gate's claims to a
    selftest would let "the instrument works" resolve a claim to PASS, which is
    exactly the laundering of assumption into proof that rule 5 exists to stop.

    Outcomes:

    * gate failed on the fixture         -> selftest PASSES (the control works)
    * gate PASSED on the fixture         -> selftest FAILS, bluntly: the gate is
      not measuring what it claims to measure
    * gate crashed on the fixture        -> selftest FAILS: refusing by exploding
      is not the same as detecting, and an exception cannot be trusted to have
      come from the defect the fixture planted
    * gate's tooling is missing          -> selftest SKIPS (nothing was tested)
    * gate SKIPPED itself on the fixture -> selftest ERRORS, when its tooling is
      present (see below)
    * fixture itself is missing/broken   -> selftest ERRORS (the control is gone,
      so the gate is unproven — never a pass)

    A skip is honest only when :func:`availability` says the tooling is absent.
    A gate that skips its own known-bad input while its tools are here has
    decided for itself that the input does not apply — usually because the
    fixture deleted a key the gate reads, or because the gate probes for a
    back-end its declaration does not name. What got through: that skip was
    passed along as a skip, `gate selftest` does not count a skip as broken, and
    the control counted as present while it proved nothing. *Rejected:* judging the
    gate's ``skip_reason`` text to tell a tooling skip from any other — it lets
    the gate decide its own skip is about tooling. A gate that genuinely needs one
    of several back-ends declares ``requires_one_of`` and lets availability say.

    **Traced, on a copy** (see ``_build_control``): the fixture gets a writable
    traced copy of ``ctx`` with ``out_dir`` replaced when one is given — a
    control must not write over the evidence a real verdict cited (packs:H5) —
    and the gate then runs on what the fixture built, through :func:`run_gate`
    with the SAME ``trace``: one record of everything the control read, the
    fixture's files and host reads and the gate's params alike. ``trace=None``
    makes a throwaway one (``kind="control"``). ``duration_s`` and ``cpu_s``
    cover the fixture and the gate together.

    ``blank_model``: the gate runs on what the fixture built with no model
    (``verdicts._no_model``) — set for an evaluator the mutation pass applies
    to, whose check run is handed none, so neither control sees a channel the
    check run lacks.
    """
    selftest_id = f"{spec.id}#selftest"
    tier = Tier(int(spec.tier))

    def verdict(**kw: Any) -> Verdict:
        base = {"gate": selftest_id, "tier": tier, "pack": spec.pack, "claims": []}
        base.update(kw)
        return Verdict(**base)       # type: ignore[arg-type]

    nc = spec.negative_control
    if nc is None or not (nc.fixture or "").strip():
        # Registration forbids this, so reaching it means a hand-built spec that
        # never went through Registry.register. Still not an error: the honest
        # report of "this gate cannot be falsified" is a FAILING selftest.
        return verdict(
            passed=False,
            detail=f"{spec.id} declares no negative control, so there is nothing to "
                   f"prove it can fail — it is a logger until one exists",
        )

    expect = (nc.expect or "fail").strip().lower()
    if expect not in ("fail", "error"):
        return verdict(
            passed=False,
            error=f"negative_control.expect={nc.expect!r} is not understood",
            detail="expect must be 'fail' (the gate must not pass on this input) "
                   "or 'error'",
        )

    available, reason = availability(spec)
    if not available:
        return verdict(
            passed=False, skipped=True,
            skip_reason=f"{reason} — the gate could not be exercised, so its control is unproven",
        )

    if trace is None:
        trace = GateTrace(kind="control")
    clock = _Clock()
    bad_ctx, problem = _build_control(spec, fn, ctx, trace=trace, out_dir=out_dir,
                                      fixture_root=fixture_root)
    if problem is not None:
        elapsed, cpu = clock.spent()
        return verdict(passed=False, duration_s=round(elapsed, 6), cpu_s=round(cpu, 6),
                       **problem)
    # What the control hands its gate through `ctx.extra` — channel parity's
    # input (P2.3-D5, D-26): the known-good control must hand the same keys.
    from . import verdicts as _verdicts
    trace.handed_extra = _verdicts._extra_keys(bad_ctx)
    bad_ctx = _verdicts._no_model(bad_ctx, blank_model)

    inner = run_gate(spec, fn, bad_ctx, trace=trace)
    elapsed, cpu = clock.spent()
    # What the gate itself said, when it crashed or skipped itself: the line a
    # qualification shows, never this function's prose below (review of P2.3).
    said = (inner.error if inner.outcome == "error"
            else inner.skip_reason if inner.outcome == "skipped" else "")
    trace.gate_said = (str(said or "").strip().splitlines() or [""])[0] or None
    shared = {
        "measured": inner.measured, "limit": inner.limit, "units": inner.units,
        "evidence": list(inner.evidence or []), "duration_s": round(elapsed, 6),
        "cpu_s": round(cpu, 6),
    }
    where = f"{nc.fixture}" + (f" ({nc.note})" if nc.note else "")
    outcome = inner.outcome

    if outcome == "skipped":
        # Asked again, not remembered from above: run_gate asked too, and its
        # answer is the one that produced this skip.
        still, missing = availability(spec)
        if not still:
            return verdict(
                passed=False, skipped=True,
                skip_reason=f"{missing} — the gate could not be exercised, so its "
                            f"control is unproven",
                **shared,
            )
        return verdict(
            passed=False,
            error=f"skipped on its own known-bad input while its tools are present: "
                  f"{inner.skip_reason or 'no reason given'}",
            detail=f"{spec.id} skipped {where} "
                   f"({inner.skip_reason or 'no reason given'}) with every tool it "
                   f"declares available, so it was never shown to reject it. Make the "
                   f"fixture state everything the gate reads, or declare the back-end "
                   f"the gate probes for (requires_one_of) so availability can say it "
                   f"is missing",
            **shared,
        )

    if expect == "error":
        # A control that expects a crash is unusual but legitimate: some gates
        # guard a parser, and "refuses to read the malformed file" IS the check.
        if outcome == "error":
            return verdict(passed=True,
                           detail=f"correctly errored on {where}: {inner.error}", **shared)
        return verdict(
            passed=False,
            detail=f"{spec.id} did not error on {where} as its control declares "
                   f"(passed={inner.passed}) — the control and the gate disagree about "
                   f"what this gate does",
            **shared,
        )

    if outcome == "error":
        return verdict(
            passed=False,
            detail=f"{spec.id} CRASHED on {where} instead of failing ({inner.error}) — "
                   f"an exception is not a measurement, and nothing here shows the gate "
                   f"detected the planted defect rather than tripping over it",
            **shared,
        )

    if outcome == "pass":
        return verdict(
            passed=False,
            detail=f"{spec.id} PASSED its own known-bad fixture {where} — it is not "
                   f"measuring what it claims to measure. Either the fixture is not bad "
                   f"in the way this gate checks, or the gate is a logger. Do not trust "
                   f"a green verdict from it until this fails.",
            **shared,
        )

    return verdict(
        passed=True,
        detail=f"correctly failed on {where}"
               + (f": {inner.detail}" if inner.detail else ""),
        **shared,
    )


# --------------------------------------------------------------------------- #
# the mutation pass (P2.3): one read value at a time, pushed past the
# evaluator's own limit, on its known-good control
# --------------------------------------------------------------------------- #
#: How far past its OWN limit an evaluator's OWN value must sit for a change to
#: count as a mutation it must fail: 15% of the limit (of the known-good value
#: when the limit is 0). Why 15%: beam-analytic's ``_MARGIN``
#: (`packs/beam-analytic/selftest/bad_beams.py`) — "15% past fires only if the
#: limit is where the pack says it is" — and clear of verdict rounding (the
#: bracket rounds its values to 3-4 places). *Rejected:* 1% and 2% (inside one
#: printed step of a one-decimal limit; old 5.1's prototype saw false results
#: there, S-18); strictly past (ties at the rounding); 100% (a limit drifted
#: 0.5 -> 0.6 still fails at 1.0: `WideMargin` in `tests/test_mutation.py`).
MUTATION_MARGIN = 0.15

#: The ladder a read value is pushed along, each factor tried x then ÷, nearest
#: first; the first rung that lands, either way, ends it. First rung at the
#: margin; roughly half-decades, so a landing is bracketed quickly; three
#: decades each way — past that a crossing is a unit slip, not a push.
#: *Rejected:* a x10 ceiling (bracket.bearing's margin is 88x: it would read
#: `mutation 0 conclusive`, measured); x2^k to x1024 (equivalent reach, more
#: probes before x10); x2 and -x (P2.0's operators: a change in the safe
#: direction survives an honest evaluator — bracket.deflection read 1/4 on them).
MUTATION_RUNGS = (1.15, 1.5, 2.0, 3.0, 5.0, 10.0, 30.0, 100.0, 1000.0)

#: Halvings, in log-factor, between the last rung that did not land and the one
#: that did: the aim. 16 gives the same planned value as 20, 24 and 32 on all 60
#: evaluators measured (the bracket's six and the 54 bundled); 12 differs on two
#: (`beam.shear_stress` `load_n` 7340 vs 7330, `bom.availability`
#: `build_quantity` 12004 vs 12001 — the landing point within one rounding step).
#: *Rejected:* 24 (eight more runs per landing key, no value changes); 40 (old
#: 5.1's prototype); 0 — the walk unaimed, where the first landing rung
#: overshoots by up to the rung gap and a 3x hidden limit at a x120 margin
#: qualifies (measured: `Unaimed`, and V2c's planted gate).
MUTATION_BISECT = 16

#: Runs per walk, the influence probes, the bisection and the crash re-checks
#: included — every call of the evaluator the walk makes counts, and
#: `MutationPass.runs` is that count. The widest aimed walk measured is 291 runs
#: (`fdm.print_time_est`), the bracket's 107 (`bed_fit`): 3.5x headroom for a
#: project evaluator that reads more. Spent on the values that move the
#: evaluator's value first (`_walk_order`), so a budget an evaluator's junk
#: reads exhaust leaves the junk unwalked, not the judged value. The re-check
#: that tells a crash that repeats from one that does not (`flaky`) runs ONCE
#: per value that crashed — its first crash — and is counted here; with the
#: budget spent before every crashed value is re-checked, the walk could not
#: finish (`budget`), never a pass. What slipped through (review of P2.3): the
#: re-check re-ran every crashed run outside the count, so an evaluator that
#: crashed on every changed value of 40 recorded 720 runs and was called 1440
#: times, and at the budget a walk could make 1024 counted runs and 1024 more.
#: *Rejected:* 512 (set for an unaimed walk, whose widest was 148); unbounded
#: (a 600-value evaluator walks ~10^4 runs inside `check`); a clock (two
#: machines, two outcomes for one control's inputs — D-29's lesson); re-checking
#: every crashed run (up to twice the budget, for a fact one re-run per value
#: shows).
MUTATION_RUNS_MAX = 1024

#: The costliest declared tier the walk runs at: a tier-0 run is under ~2 s by
#: declaration, and a walk is up to ~300 runs (the widest measured); at tier 1
#: (seconds to minutes a run) that is hours per qualification. An evaluator
#: above it reads `mutation 0 conclusive (none made: tier <n>)` and is
#: qualified on its controls (PLAN-v0.14 §1.5's panel default, on the check-in
#: list as a reading of it). *Rejected:* every tier (panel Q1).
MUTATION_TIER_MAX = Tier.INSTANT

#: Two decimal values one float step apart read as equal when the walk asks how
#: far past a limit a value sits: 2.3 - 2.0 is 0.29999999999999982 in binary,
#: and "15% past 2.0" must hold for the 2.3 a person reads. *Rejected:* exact
#: comparison (the aim then overshoots to 2.31, past what anyone can see as the
#: margin); decimal arithmetic (values arrive as floats a gate already rounded).
_LAND_TOLERANCE = 1e-9

#: A read value within this of the reported one is the reported value straight
#: through, and is walked first: bracket.deflection reads `deflection` and
#: reports it. 0.5%: the bracket reports its values rounded, and the widest gap
#: measured between a value one of its gates reads and the value it reports is
#: 0.27% (`bracket.bearing` reads 0.170455 and reports 0.17; `bending_stress`
#: 0.27% too) — inside 0.5% by about 1.9x. It decides walk ORDER, and whether a
#: budget left with that value unwalked is `budget`; never an outcome.
#: *Rejected:* exact equality (three of the bracket's six — deflection,
#: bending_stress, bearing — report a rounded copy of what they read, and would
#: lose their place to reads sorted before them, the slip `_walk_order` exists
#: for); a wider band such as 5% (nothing on the bracket sits between 0.27% and
#: the next read, 85% away, so it buys nothing measured, and an unrelated read
#: inside it would jump the queue and could end a walk as `budget` over a value
#: that never moved the evaluator's). Review of P2.3: it landed with no
#: rejected alternative, and a later window would have re-litigated it.
_STRAIGHT_THROUGH = 0.005


@dataclass(frozen=True)
class MutationResult:
    """One mutation the walk made: the read value at ``key`` (a tuple path)
    moved from ``before`` to ``after``, and the run's ``outcome`` — ``pass`` or
    ``fail`` (conclusive: it landed, so it measured), or ``errored`` /
    ``skipped`` (inconclusive: counted neither way, ``why`` its first line) —
    with the ``measured`` value and ``limit`` it reported."""

    key: tuple
    before: Any
    after: Any
    outcome: str
    measured: Any = None
    limit: Any = None
    why: str = ""


@dataclass(frozen=True)
class MutationPass:
    """What one walk did. ``results`` — the conclusive mutations; ``inconclusive``
    — a read value whose walk never landed and saw a skip or a crash (its first
    such value), or whose aim crashed; ``not_mutated`` — ``(key, why)`` for every
    read value in no mutation: ``never-lands``, ``word``, ``zero``, ``none``,
    ``not-finite``, ``other``, or ``budget`` (the budget ran out before it);
    ``boundary`` — why nothing, or not everything, was walked: ``no-limit`` (no
    finite value against a finite limit), ``at-limit`` (the value is exactly at
    its limit: no side to push past), ``tier:<n>`` (above ``MUTATION_TIER_MAX``),
    ``budget`` (the budget ran out with a value that moves the evaluator's value
    unwalked); ``runs``; ``errors`` — ``(key, value, outcome, line)`` for every
    skipped or crashed run the walk saw; ``flaky`` — the first line of a crash
    that did not repeat when run again, ``""`` when every one did (the caller
    then holds the walk as it holds a crashed control, never as a tracked
    entry)."""

    results: tuple = ()
    inconclusive: tuple = ()
    not_mutated: tuple = ()
    boundary: str = ""
    runs: int = 0
    errors: tuple = ()
    flaky: str = ""


class _Budget(Exception):
    """The walk's run budget is spent."""


class MutationCannotRun(AtompipeError):
    """The walk could not run at all — its scratch would have landed inside a
    project or a pack directory. Never a silent write, never a quiet skip: the
    caller reads the evaluator unqualified, `mutation could not run`."""


def _finite_number(value: Any) -> bool:
    return (isinstance(value, numbers.Real) and not isinstance(value, bool)
            and math.isfinite(float(value)))


def _orderable(value: Any) -> bool:
    """What a ladder can push: a finite non-zero number (never a flag), a flag,
    a non-empty list of finite numbers not all zero."""
    if isinstance(value, bool):
        return True
    if _finite_number(value):
        return value != 0
    return (isinstance(value, (list, tuple)) and bool(value)
            and all(_finite_number(x) for x in value) and any(value))


def _unwalked(value: Any) -> str:
    """Why a read value is not walked, as `not_mutated` names it."""
    if isinstance(value, str):
        return "word"
    if value is None:
        return "none"
    if isinstance(value, numbers.Real) and not isinstance(value, bool):
        return "zero" if value == 0 else "not-finite"
    return "other"


def _scaled(value: Any, factor: float) -> Any:
    """``value`` x ``factor``: an int rounded and kept an int, a list element-wise,
    a flag its other value."""
    if isinstance(value, bool):
        return not value
    if isinstance(value, (list, tuple)):
        return type(value)(int(round(x * factor)) if isinstance(x, int) else x * factor
                           for x in value)
    return int(round(value * factor)) if isinstance(value, int) else value * factor


def _outward(x: float, origin: Any) -> Any:
    """``x`` rounded AWAY from ``origin`` to 3 significant figures — an int to the
    next integer away, at least one step — so an entry and a line read `0.575`,
    never `0.5750000000003`, and rounding only pushes further past. *Rejected:*
    nearest rounding, which can round back inside the margin."""
    if isinstance(origin, int) and not isinstance(origin, bool):
        r = math.ceil(x) if x > origin else math.floor(x)
        return r if r != origin else origin + (1 if x > origin else -1)
    if x == 0 or not math.isfinite(x):
        return x
    q = 10.0 ** (math.floor(math.log10(abs(x))) - 2)
    return float(f"{(math.ceil(x / q) if x > origin else math.floor(x / q)) * q:.3g}")


def _aimed_value(x0: Any, factor: float) -> Any:
    if isinstance(x0, (list, tuple)):
        return type(x0)(_outward(e * factor, e) if e else e for e in x0)
    return _outward(x0 * factor, x0)


def _lands(verdict: Verdict, side: int, m0: float) -> bool:
    """Does ``verdict``'s OWN value sit ``MUTATION_MARGIN`` past its OWN limit,
    on the side opposite the known-good value (``side``)? Read off the value
    and the limit — never the pass flag: a walk that stopped at the first FAIL
    reads `1/1 fail` over an evaluator keyed to its own control
    (`StopsAtFirstFail`). A run that measured nothing finite never lands."""
    if verdict.outcome not in ("pass", "fail"):
        return False
    m, limit = verdict.measured, verdict.limit
    if not (_finite_number(m) and _finite_number(limit)):
        return False
    scale = abs(float(limit)) if limit else abs(float(m0))
    return side * (float(m) - float(limit)) >= \
        MUTATION_MARGIN * scale * (1 - _LAND_TOLERANCE)


def _path_get(params: Any, key: tuple) -> Any:
    for part in key:
        params = params[part]
    return params


def _path_set(params: dict, key: tuple, value: Any) -> None:
    for part in key[:-1]:
        params = params[part]
    params[key[-1]] = value


def _leaves(params: Any, prefix: tuple = ()) -> list[tuple]:
    if isinstance(params, dict):
        out: list[tuple] = []
        for k, v in params.items():
            out += _leaves(v, prefix + (k,))
        return out
    return [prefix] if prefix else []


def _read_leaves(trace: GateTrace, params: dict) -> list[tuple]:
    """The leaves of ``params`` the known-good run read: a path its trace keyed,
    or one under a level it read whole (a scoped pack read keeps its scoped
    path). The ones a mutation may change: "the parameters its run read"
    (PLAN-v0.14 §1.5)."""
    whole = set(trace.whole)
    keyed = set(trace.params)
    return [leaf for leaf in _leaves(params)
            if leaf in keyed or any(leaf[:i] in whole for i in range(len(leaf)))]


def _fold_reads(into: GateTrace, run: GateTrace, known_good: dict) -> None:
    """Fold one walk run's reads into the known-good half's trace, each param at
    its KNOWN-GOOD digest: a value only a mutated run reads is an input of the
    qualification — moved, it re-runs the walk (P2.3-D9). What slipped through
    the design that walked on a throwaway trace (V1j): a gate reading
    `override` only past its limit, the known-good design gaining `override =
    true`, and the cached walk served — `mutation 1/1 fail` over a gate that now
    passes the same mutation. Keyed at the known-good value, never the mutated
    one: a level the walk read whole with a mutated leaf inside would otherwise
    key the qualification on a design that never existed, and never match."""
    from . import verdicts as _verdicts           # this module imports from verdicts
    anchors = getattr(into, "anchors", None) or _verdicts.Anchors()
    for path, digest in run.params.items():
        if path in into.params:
            continue
        known, value = _verdicts._param_at(known_good, path, digest, anchors)
        into.params[path] = known
        if path in run.values and value is not _verdicts._MISSING:
            small, shown = _verdicts.small_value(value, anchors)
            if small:
                into.values[path] = shown
    into.whole |= set(run.whole)
    for key, digest in run.ledger.items():
        into.ledger.setdefault(key, digest)
    for path in run.files_read:
        into._note_read(path)
    for path in getattr(run, "sources", ()):
        into._note_source(path)
    for path in run.stats:
        into._note_stat(path, run.stat_existed(path))
    into.dirs |= set(run.dirs)
    into.files_written |= set(run.files_written)
    into.opaque |= set(run.opaque)
    into.model_used = into.model_used or run.model_used
    for path, digest in run.host_reads.items():
        into.host_reads.setdefault(path, digest)


def _walk_order(keys: list[tuple], *, influence: dict, straight: set) -> list[tuple]:
    """The order the walk spends its budget in: a read value that IS the reported
    value first, then by how far it moved the evaluator's value at the first
    rung (most first; a run that crashed counts as moving it most), then by
    path. What slipped through ordering by path alone (critique of the P2.3
    design): an evaluator whose judged value sorts after 600 junk reads never
    had it walked — the budget ran out on the junk."""
    def rank(key: tuple) -> tuple:
        return (key not in straight, -influence.get(key, math.inf), repr(key))
    return sorted(keys, key=rank)


def _probe_value(x0: Any) -> Any:
    """The influence probe: the first ladder value that differs from ``x0`` — x1.15
    for most, the other value of a flag, the first rung that moves an int.
    ONE run per value, and the ladder reuses it: an evaluator reading 600
    values is probed in 600 runs, inside the budget, with its judged value
    among them. *Rejected:* both directions of the first rung (1,200 runs for
    those 600 — the budget gone before the judged value is probed)."""
    if isinstance(x0, bool):
        return not x0
    for f in MUTATION_RUNGS:
        for g in (f, 1.0 / f):
            value = _scaled(x0, g)
            if value != x0:
                return value
    return x0


def _walk_key(key: tuple, x0: Any, run: Callable[[tuple, Any], Verdict], side: int,
              m0: float) -> tuple[str, Any]:
    """One read value's walk: ``("lands", MutationResult)``,
    ``("inconclusive", MutationResult)`` or ``("never-lands", None)``.

    The ladder, x then ÷ per rung, nearest first; a rung equal to the known-good
    value or one already tried is skipped; the first rung that lands ends it.
    Then the aim: ``MUTATION_BISECT`` halvings in log-factor between the last
    rung that did not land that way and the one that did, the smallest landing
    factor rounded outward; a rounded value that does not land (a non-monotone
    evaluator) falls back to the rung's. A skip or a crash during the aim stops
    it, and the value is inconclusive — never a bisection that read a crash as
    "does not land" and aimed further out (critique of the P2.3 design: a
    transient error there walked a x120-margin gate back to its x1000 rung)."""
    tried: set = set()
    prev = {+1: 1.0, -1: 1.0}
    found = None
    first_error: tuple | None = None
    factors = (MUTATION_RUNGS[0],) if isinstance(x0, bool) else MUTATION_RUNGS
    for f in factors:
        for d, g in ((+1, f), (-1, 1.0 / f)):
            value = _scaled(x0, g)
            if value == x0 or repr(value) in tried:
                continue
            tried.add(repr(value))
            verdict = run(key, value)
            if verdict.outcome in ("error", "skipped") and first_error is None:
                first_error = (value, verdict)
            if _lands(verdict, side, m0):
                found = (prev[d], g, value, verdict)
                break
            prev[d] = g
        if found:
            break
    if found is None:
        if first_error is None:
            return "never-lands", None
        value, verdict = first_error
        return "inconclusive", _inconclusive(key, x0, value, verdict)
    lo, hi, rung_value, rung_verdict = found
    if isinstance(x0, bool):
        return "lands", _result(key, x0, rung_value, rung_verdict)
    for _ in range(MUTATION_BISECT):
        mid = math.exp((math.log(lo) + math.log(hi)) / 2)
        value = _scaled(x0, mid)
        verdict = run(key, value)
        if verdict.outcome in ("error", "skipped"):
            return "inconclusive", _inconclusive(key, x0, value, verdict, aiming=True)
        if _lands(verdict, side, m0):
            hi = mid
        else:
            lo = mid
    after = _aimed_value(x0, hi)
    verdict = run(key, after)
    if verdict.outcome in ("error", "skipped"):
        return "inconclusive", _inconclusive(key, x0, after, verdict, aiming=True)
    if not _lands(verdict, side, m0):
        after, verdict = rung_value, rung_verdict
    return "lands", _result(key, x0, after, verdict)


def _first_line(verdict: Verdict) -> str:
    text = verdict.error if verdict.outcome == "error" else (verdict.skip_reason
                                                            or verdict.detail)
    return (str(text or "").splitlines() or [""])[0]


def _result(key: tuple, x0: Any, after: Any, verdict: Verdict) -> MutationResult:
    return MutationResult(key=key, before=x0, after=after, outcome=verdict.outcome,
                          measured=verdict.measured, limit=verdict.limit)


def _inconclusive(key: tuple, x0: Any, after: Any, verdict: Verdict, *,
                  aiming: bool = False) -> MutationResult:
    outcome = "errored" if verdict.outcome == "error" else "skipped"
    line = _first_line(verdict)
    return MutationResult(key=key, before=x0, after=after, outcome=outcome,
                          why=f"while aiming: {line}" if aiming else line)


def _inside(path: str, roots: Iterable[str]) -> str:
    """The root ``path`` lies in, or ``""``."""
    real = os.path.realpath(path)
    for root in roots:
        if not root:
            continue
        base = os.path.realpath(root)
        if real == base or real.startswith(base.rstrip(os.sep) + os.sep):
            return root
    return ""


def mutation_walk(spec: GateSpec, fn: Callable[[GateContext], Any], good_ctx: GateContext,
                  good_verdict: Verdict, *, trace: GateTrace | None = None,
                  roots: Iterable[str] = ()) -> MutationPass:
    """The mutation pass on ``spec``'s known-good control (PLAN-v0.14 §1.5):
    every value its known-good run read, pushed along ``MUTATION_RUNGS`` until
    the evaluator's OWN value lands ``MUTATION_MARGIN`` past its OWN limit, on
    the side opposite its known-good value — read off the value, never the pass
    flag — then aimed by bisection at the smallest change that lands; that
    change is the value's mutation, and its run must fail. A value that never
    lands is not mutated (named, never counted): an honest evaluator passes a
    change to what it only prints, and §6.3 asks non-vacuity, not verification.

    ``good_verdict`` is the known-good run's verdict and ``trace`` its trace:
    which values it read, and where every walk run's reads are folded, each at
    its known-good digest (``_fold_reads``). Nothing is walked — ``boundary``
    says why — above ``MUTATION_TIER_MAX``, or unless the known-good run passed
    with a finite value against a finite limit it is not exactly at. The values
    are walked in ``_walk_order``, at most ``MUTATION_RUNS_MAX`` runs; a value
    that moves the evaluator's value and is left unwalked makes the boundary
    ``budget``.

    **Sealed** (invariant 15): every run is ``run_gate`` on a deep copy of the
    known-good params with one value changed, a fresh ``SweepMemo`` and an out
    dir from ``tempfile`` that is emptied between runs and removed at the end,
    with ``sys.dont_write_bytecode`` set for the pass. That dir must lie outside
    ``roots`` (the project and every pack directory) and the known-good
    context's root, or nothing runs: ``MutationCannotRun``, which the caller
    reads as unqualified, loudly — never a scratch dir under the project
    (evidence from a design that never existed, beside real evidence). The
    known-good design is never touched. Deterministic: no clock and no
    randomness, so the same inputs give the same runs and the same bytes.

    A crash a re-run does not repeat is ``flaky``: the caller holds the walk as
    it holds a crashed control (remembered, untracked, re-run next check) —
    never a tracked entry an environmental failure decided.
    """
    if int(spec.tier) > int(MUTATION_TIER_MAX):
        return MutationPass(boundary=f"tier:{int(spec.tier)}")
    m0, limit0 = good_verdict.measured, good_verdict.limit
    if good_verdict.outcome != "pass" or not (_finite_number(m0) and _finite_number(limit0)):
        return MutationPass(boundary="no-limit")
    if float(m0) == float(limit0):
        return MutationPass(boundary="at-limit")
    m0 = float(m0)
    side = 1 if m0 < float(limit0) else -1
    trace = trace if trace is not None else GateTrace(kind="control")
    known_good = copy.deepcopy(dict(good_ctx.params or {}))
    keys = _read_leaves(trace, known_good)

    base = tempfile.gettempdir()
    clash = _inside(base, [*roots, getattr(good_ctx, "root", "") or ""])
    if clash:
        raise MutationCannotRun(
            f"its temp directory {base} is inside {clash}: a mutated run's scratch would "
            f"land in the tree it may not write (set TMPDIR outside the project)")

    not_mutated: list[tuple] = []
    walkable: list[tuple] = []
    for key in keys:
        value = _path_get(known_good, key)
        if _orderable(value):
            walkable.append(key)
        else:
            not_mutated.append((key, _unwalked(value)))

    errors: list[tuple] = []
    cache: dict[tuple, Verdict] = {}
    spent = [0]
    out_dir = tempfile.mkdtemp(prefix="atompipe-mutation-")
    bytecode = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    # One gate, run hundreds of times: its module closure found once per size
    # of sys.modules, its memos still emptied before every run.
    scope = modelio.closure_scope()
    scope.__enter__()

    def run(key: tuple, value: Any) -> Verdict:
        slot = (key, repr(value))
        if slot in cache:
            return cache[slot]
        if spent[0] >= MUTATION_RUNS_MAX:
            raise _Budget()
        spent[0] += 1
        verdict = _mutated_run(spec, fn, good_ctx, known_good, key, value, out_dir, trace)
        cache[slot] = verdict
        if verdict.outcome in ("error", "skipped"):
            errors.append((key, value, verdict.outcome, _first_line(verdict)))
        return verdict

    results: list[MutationResult] = []
    inconclusive: list[MutationResult] = []
    boundary = ""
    try:
        influence: dict[tuple, float] = {}
        straight = {key for key in walkable
                    if _finite_number(_path_get(known_good, key))
                    and abs(float(_path_get(known_good, key)) - m0)
                    <= _STRAIGHT_THROUGH * abs(m0)}
        probed: list[tuple] = []
        try:
            for key in sorted(walkable, key=repr):
                verdict = run(key, _probe_value(_path_get(known_good, key)))
                if verdict.outcome in ("error", "skipped"):
                    influence[key] = math.inf
                elif _finite_number(verdict.measured):
                    influence[key] = abs(float(verdict.measured) - m0)
                else:
                    influence[key] = math.inf       # it stopped measuring: it moved
                probed.append(key)
        except _Budget:
            pass
        order = _walk_order(probed, influence=influence, straight=straight)
        order += [key for key in sorted(walkable, key=repr) if key not in set(probed)]
        for index, key in enumerate(order):
            x0 = _path_get(known_good, key)
            try:
                kind, result = _walk_key(key, x0, run, side, m0)
            except _Budget:
                left = order[index:]
                not_mutated += [(k, "budget") for k in left]
                if any(influence.get(k, math.inf) > 0 or k in straight for k in left):
                    boundary = "budget"
                break
            if kind == "lands":
                results.append(result)
            elif kind == "inconclusive":
                inconclusive.append(result)
            else:
                not_mutated.append((key, "never-lands"))
        flaky = ""
        rechecked: set = set()
        for key, value, outcome, line in errors:
            if outcome != "error" or key in rechecked:
                continue
            rechecked.add(key)
            if spent[0] >= MUTATION_RUNS_MAX:
                boundary = "budget"           # a crash left un-rechecked: not finished
                break
            spent[0] += 1                     # counted: MUTATION_RUNS_MAX's provenance
            again = _mutated_run(spec, fn, good_ctx, known_good, key, value, out_dir, trace)
            if again.outcome != "error":
                flaky = line or "a crash that did not repeat"
                break
    finally:
        scope.__exit__(None, None, None)
        sys.dont_write_bytecode = bytecode
        shutil.rmtree(out_dir, ignore_errors=True)
    return MutationPass(results=tuple(results), inconclusive=tuple(inconclusive),
                        not_mutated=tuple(sorted(not_mutated, key=lambda kw: repr(kw[0]))),
                        boundary=boundary, runs=spent[0], errors=tuple(errors), flaky=flaky)


def _mutated_run(spec: GateSpec, fn: Callable[[GateContext], Any], good_ctx: GateContext,
                 known_good: dict, key: tuple, value: Any, out_dir: str,
                 trace: GateTrace) -> Verdict:
    """One walk run: ``run_gate`` on a deep copy of the known-good params with
    ``key`` set to ``value``, a fresh memo, the walk's emptied out dir — traced
    on a trace of its own and folded into ``trace`` (``_fold_reads``)."""
    from . import verdicts as _verdicts
    params = copy.deepcopy(known_good)
    _path_set(params, key, value)
    for name in os.listdir(out_dir):
        target = os.path.join(out_dir, name)
        if os.path.isdir(target) and not os.path.islink(target):
            shutil.rmtree(target, ignore_errors=True)
        else:
            with contextlib.suppress(OSError):
                os.remove(target)
    names = {f.name for f in dataclasses.fields(good_ctx)}
    changes: dict[str, Any] = {"params": params, "out_dir": out_dir}
    if "memo" in names:
        changes["memo"] = _verdicts.SweepMemo()
    own = GateTrace(kind=getattr(trace, "kind", "control"),
                    anchors=getattr(trace, "anchors", None))
    verdict = run_gate(spec, fn, dataclasses.replace(good_ctx, **changes), trace=own)
    _fold_reads(trace, own, known_good)
    return verdict


# --------------------------------------------------------------------------- #
# a project's own gates
# --------------------------------------------------------------------------- #
def load_project_gates(root: str, registry: Registry) -> list[str]:
    """Import ``<root>/gates/*.py`` into ``registry``; return the ids they register.

    The project's own gates, loaded the way a pack's are and for the same
    reasons — the pack directory's equivalent is the project root, so a gate can
    ``import selftest.bad_configs`` or share a ``_geom.py`` helper with the model.
    It lived in the CLI until the verdict cache needed the code a verdict came
    from; it lives here now, beside the registry it fills, so there is one copy.

    What it shares with ``packs.load_gates``, deliberately, because a gate must
    behave identically in a project and after it is extracted into a pack:

    * ``root`` goes on ``sys.path[0]`` and comes off in a ``finally``; a leaked
      entry makes the *next* import resolve against this project.
    * Module names are salted with a hash of the file's absolute path, so two
      projects in one process cannot share a ``gates/structural.py``.
    * Every module goes through ``modelio.load_source_module`` with
      ``roots=[root]``: the bytes on disk are the bytes that run, and the module's
      code closure is recorded. What slipped through before: a module already in
      ``sys.modules`` was skipped outright, and a stale ``__pycache__`` validated
      by mtime and size served the old bytecode — edit ``7.0`` to ``8.0`` inside
      the same second and the verdict came from 7.0 (S-26). A module served from
      the cache re-adopts the gates it registered into THIS registry, so a fresh
      ``Registry`` per command is never handed back empty.
    * ``PACK_DIR`` is NOT set, so ``_fixture_root`` falls through to ``ctx.root``
      and ``selftest/bad_configs.py`` resolves beside the model, where it lives.
      A module that sets it itself is still a project's: a ``PACK_DIR`` counts
      only from a module whose file is under it (``verdicts._pack_dir_of``;
      review of P2.3, ``br1`` — one line opted a project gate out of the
      mutation pass).
    * ``KeyboardInterrupt`` passes through untouched, as it does for a pack: the
      CLI copy caught it with everything else and reported a Ctrl-C as the
      user's gate module failing to import.

    Files starting with ``_`` are skipped (helpers, not gates). Any other failure
    becomes an ``AtompipeError`` naming the file: a gate module that will not
    import is a user's Python problem, and a spine traceback reads as a spine bug.

    Returns the ids each module registered, in load order — including gates
    re-adopted from a cached module, which a before/after diff of the registry
    would miss when the registry already held them.
    """
    directory = os.path.join(root, _PROJECT_GATES_DIR)
    if not os.path.isdir(directory):
        return []
    try:
        names = sorted(os.listdir(directory))
    except OSError as exc:
        raise AtompipeError(f"cannot read {rel(directory, root)}: {exc}") from exc
    files = [os.path.join(directory, n) for n in names
             if n.endswith(".py") and not n.startswith("_")]
    if not files:
        return []

    ids: list[str] = []
    sys.path.insert(0, root)
    try:
        with use_registry(registry):
            for path in files:
                stem = os.path.splitext(os.path.basename(path))[0]
                module_name = f"atompipe_project_{stem}_{short_hash(os.path.abspath(path), 8)}"
                try:
                    module = modelio.load_source_module(path, name=module_name, roots=[root],
                                                        registry=registry)
                except AtompipeError as exc:
                    # A registry refusal (no negative control) is already phrased
                    # for a human. Keep the phrasing, add the location.
                    raise AtompipeError(f"{rel(path, root)}: {exc}") from exc
                except KeyboardInterrupt:
                    raise
                except BaseException as exc:
                    raise AtompipeError(
                        f"{rel(path, root)} failed to import: {type(exc).__name__}: {exc}"
                    ) from exc
                recorded = vars(module).get("__atompipe_gates__") or ()
                ids.extend(spec.id for spec, _fn in recorded)
    finally:
        try:
            sys.path.remove(root)
        except ValueError:            # pragma: no cover - a gate mangled sys.path
            pass
    return ids


# --------------------------------------------------------------------------- #
# description and summary
# --------------------------------------------------------------------------- #
def describe(spec: GateSpec) -> str:
    """One dense line for `atompipe gate list`. Never more than one.

    Everything a reader needs to decide whether to care: what it is, what it
    costs, what it settles, whether it can run here, and what its control is. The
    control is included on purpose — it is the fact that makes the gate's green
    tick mean anything, so it belongs in the listing and not three commands away.
    """
    bits = [f"{spec.id}  [t{int(spec.tier)}"]
    if spec.pack:
        bits[0] += f" {spec.pack}"
    bits[0] += "]"
    tools = list(spec.requires_tools or []) + list(spec.requires_python or [])
    if spec.requires_one_of:
        tools.append("one of " + "|".join(spec.requires_one_of))
    ok, reason = availability(spec) if tools else (True, "")
    if not ok:
        # Right after the id, never at the tail: the line is cut at 240
        # characters, and what cannot run here is what a reader scans this
        # list for. In `gate show`'s words ("runnable here: NO — …"). What
        # slipped through (review of P2.2): it was the tail's "BLOCKED: …" — a
        # GLOSSARY §3 Never-say, beside the new prerequisite list, where a
        # reader could take it for the prerequisite blocking the gate — and the
        # new segment pushed it past the cap (cad.assembly_connected's line
        # ended "prerequisites cad.is_volume BLO...").
        bits.append(f"not runnable here: {reason}")
    if spec.title:
        bits.append(spec.title)
    if spec.settles:
        bits.append(f"settles {spec.settles}")
    if spec.claims:
        bits.append("claims " + ",".join(spec.claims))
    if spec.needs:
        # The edge's word (P2.2-D11). Tools say "requires", availability's own
        # lead: until P2.2 they said "needs", which was then about to mean two
        # things on one line — and *need* (noun) is the Gap's Never-say.
        bits.append("prerequisites " + ",".join(spec.needs))
    if tools and ok:
        bits.append("requires " + ",".join(tools))
    nc = spec.negative_control
    bits.append(f"control {nc.fixture}" if nc and nc.fixture else "NO CONTROL")
    return _one_line("  ".join(bits), 240)


def registry_summary(registry: Registry) -> dict[str, Any]:
    """A JSON-safe overview of what this process can check, and what it cannot.

    Written for `atompipe doctor` and `--json`. The fields that matter are the
    negative ones: ``unavailable`` names every gate that would SKIP here and
    why, and ``max_tier`` versus ``by_tier`` shows whether a project has any
    cheap gates at all. A pack that ships only tier-2 gates has not finished its
    job (rule 10), and this is where that shows up as a number instead of as a
    twenty-minute inner loop nobody mentions.
    """
    specs = registry.specs()
    by_tier: dict[str, int] = {}
    by_pack: dict[str, int] = {}
    claims: set[str] = set()
    tools: set[str] = set()
    modules: set[str] = set()
    unavailable: list[dict[str, str]] = []

    for spec in specs:
        key = str(int(spec.tier))
        by_tier[key] = by_tier.get(key, 0) + 1
        pack = spec.pack or "(project)"
        by_pack[pack] = by_pack.get(pack, 0) + 1
        claims.update(c for c in (spec.claims or []) if c)
        tools.update(t for t in (spec.requires_tools or []) if t)
        modules.update(m for m in (spec.requires_python or []) if m)
        ok, reason = availability(spec)
        if not ok:
            unavailable.append({"gate": spec.id, "reason": reason})

    return {
        "gates": len(specs),
        "ids": [s.id for s in specs],
        # A prerequisite that is not registered reads "not registered" at every
        # sweep and Skips its dependent (P2.2-D14): `doctor` names each one.
        "unregistered_prerequisites": [{"gate": s.id, "need": need}
                                       for s in specs for need in (s.needs or ())
                                       if need not in registry],
        "by_tier": by_tier,
        "by_pack": by_pack,
        "max_tier": max((int(s.tier) for s in specs), default=0),
        "tier0": by_tier.get("0", 0),
        "claims": sorted(claims),
        "requires_tools": sorted(tools),
        "requires_python": sorted(modules),
        "available": len(specs) - len(unavailable),
        "unavailable": unavailable,
        "without_negative_control": [
            s.id for s in specs
            if not (s.negative_control and (s.negative_control.fixture or "").strip())
        ],
    }
