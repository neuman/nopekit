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
   differently downstream — a missing tool resolves its claim to BLOCKED, a crash
   to FAIL, because a crash is the louder problem — and the report must be able
   to say "this gate could not run" instead of "your design is wrong".

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
   (S-27). With no trace passed, a throwaway one is made: the view is read-only on
   EVERY path — a test, a pack's own ``__main__``, a fixture's nested call — not
   only inside ``check``.

The gate function itself stays an ordinary function: :func:`gate` registers it
and returns it **unchanged**, so it is directly callable and directly testable
without the registry in the way.

Time policy (contract rule 3): nothing here stamps a timestamp. Durations are
*measured*, which is not the same thing — a wall-clock duration cannot be passed
in by a caller who is waiting on the result, and ``RunMeta.when`` still arrives
from the CLI edge. The same goes for ``cpu_s`` (``os.times``).
"""
from __future__ import annotations

import math

import contextlib
import dataclasses
import fnmatch
import importlib
import importlib.util
import numbers
import os
import reprlib
import shutil
import sys
import time
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from . import modelio
from .models import GateSpec, Ledger, NegativeControl, Tier, Verdict
from .util import AtompipeError, ensure_dir, rel, short_hash
from .verdicts import GateTrace, ParamTrace, traced_context, tracing

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
    "load_fixture",
    "load_project_gates",
    "describe",
    "registry_summary",
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
    ``memo``     the sweep's file memo, behind :meth:`load_file`. One dict per
                 :func:`run_all`, shared by reference with every gate's view.
                 ``None`` outside a sweep — a hand-run script, a test — and
                 :meth:`load_file` then simply loads.
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
    memo: dict | None = field(default=None, repr=False, compare=False)
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

        **The memo** (``self.memo``, one per :func:`run_all`) is keyed on
        ``(abspath, id(loader))``: two gates asking for the bytes and a third
        asking for a parsed mesh get two entries, never each other's. For a bound
        method the id is its object's and its function's, because ``obj.parse`` is
        a NEW object on every access: keyed on that, a bound-method loader never
        hit (what slipped through while its test was being written); a lambda made
        inside the gate body never hits either — pass a module-level function. The entry holds the loader itself, so its ``id``
        cannot be reused by a new function while the entry lives, and the file's
        stat signature, so bytes rewritten between two gates of one sweep are
        loaded again — a hit on the old bytes would have put the new bytes' digest
        on a verdict computed from the old ones. With no memo (``None``: a
        hand-run check script, a test) it just loads (packs:H15).

        A hit hands every caller the SAME object. Do not mutate it: copy first,
        as cad-solid does before welding a mesh.
        """
        raw = os.fspath(path)
        if isinstance(raw, bytes):
            raw = os.fsdecode(raw)
        target = raw if os.path.isabs(raw) else os.path.join(self.root or os.curdir, raw)
        abspath = os.path.abspath(target)
        _report_read(self.trace, abspath)
        memo = self.memo
        if memo is None:
            return _load(abspath, loader)
        key = (abspath, _loader_id(loader))
        signature = _stat_signature(abspath)
        held = memo.get(key)
        # A key match IS the same loader: the entry holds its loader alive, and a
        # live object's id is never handed to another (see `_loader_id`).
        if held is not None and signature is not None and held[1] == signature:
            return held[2]
        value = _load(abspath, loader)
        if signature is not None:
            memo[key] = (loader, signature, value)
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


def _stat_signature(abspath: str) -> tuple | None:
    """What a memo hit is checked against: ``(size, mtime_ns, ctime_ns, inode)``.

    ctime and the inode are here because size and mtime alone miss a same-size
    rewrite with its mtime put back — ``os.utime`` cannot restore a ctime, and an
    atomic replace changes the inode. ``None`` when the file cannot be stat'ed:
    such a load is never memoised, so the loader's own error reaches the gate.
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
    nobody registered.

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


class Registry:
    """The gates known to this process, in declaration order.

    Order is part of the contract: a sweep runs gates in the order they
    registered, which is the order a pack's author wrote them, which is usually
    cheapest-and-most-fundamental first. Sorting alphabetically would put
    ``cad.wall_thickness`` before ``cad.watertight`` and report thin walls on a
    mesh that is not even closed.

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

        existing = self._gates.get(gate_id)
        if existing is not None and not replace:
            old_spec, old_fn = existing
            if old_fn is fn:                                   # idempotent re-import
                fresh = _own_copy(spec)
                if not (fresh.pack or "").strip() and old_spec.pack:
                    fresh = dataclasses.replace(fresh, pack=old_spec.pack)
                self._gates[gate_id] = (fresh, fn)
                return
            where_old = old_spec.entry or old_spec.pack or getattr(old_fn, "__module__", "?")
            where_new = spec.entry or spec.pack or getattr(fn, "__module__", "?")
            raise AtompipeError(
                f"gate id {gate_id!r} is already registered by {where_old} and "
                f"{where_new} wants it too. Two gates cannot share an id: verdicts, "
                f"claim coverage and `atompipe check --only` all key on it. Rename one "
                f"(ids are pack-prefixed for exactly this reason)."
            )

        self._gates[gate_id] = (_own_copy(spec), fn)

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
    ``model`` a ``ModelProxy`` (``None`` stays ``None``), ``memo`` shared and
    ``trace`` set — and ``fn`` runs inside ``verdicts.tracing(trace)``, so the
    files it opens are recorded too. ``trace=None`` makes a throwaway trace: the
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
) -> list[Verdict]:
    """Run every selected gate in declaration order and return their verdicts.

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

    ``ctx.memo`` — the file memo behind :meth:`GateContext.load_file` — is one
    fresh dict for the whole sweep when the caller brought none, shared by every
    gate's view, and never left on the caller's context: a memo that outlived
    its sweep would serve one sweep's bytes to the next.
    """
    selected = _selected(registry, max_tier, only)
    if int(ctx.tier) != int(max_tier):
        ctx = dataclasses.replace(ctx, tier=int(max_tier))
    if ctx.memo is None:
        ctx = dataclasses.replace(ctx, memo={})
    if ctx.out_dir:
        ensure_dir(ctx.out_dir)      # once, up front: gates cite files in it

    out: list[Verdict] = []
    for spec in selected:
        entry = registry.get(spec.id)
        if entry is None:            # concurrent unregister; nothing else can do this
            raise AtompipeError(f"gate {spec.id!r} disappeared from the registry mid-sweep")
        live, fn = entry
        gap = _control_gap(live)
        if gap:
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


def _load_py_file(path: str, root: str = "") -> Any:
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
    recorded with it.
    """
    absolute = os.path.abspath(path)
    module_name = f"_atompipe_fixture_{short_hash(absolute, 10)}"
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
            f"fixture {path} called sys.exit({exc.code!r}) while importing — a fixture "
            f"builds known-bad input, it does not exit the process; the gate it guards "
            f"is unproven until it stops"
        ) from exc
    except Exception as exc:                     # noqa: BLE001 - user's fixture code
        raise AtompipeError(
            f"fixture {path} failed to import ({type(exc).__name__}: {exc}) — the "
            f"known-bad input is broken, so the gate it guards is unproven"
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
    """
    ref = (ref or "").strip()
    if not ref:
        raise AtompipeError("negative control has an empty fixture reference")

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
                f"negative-control fixture {target!r} does not exist "
                f"(looked in {os.path.abspath(path)}). The gate cannot be proven able "
                f"to fail until it does."
            )
        module = _load_py_file(path, root or os.curdir)
        wanted = func_name or "make"
        source = target
    else:
        try:
            module = importlib.import_module(target)
        except ImportError as exc:
            raise AtompipeError(
                f"cannot import negative-control fixture module {target!r}: {exc}"
            ) from exc
        except SystemExit as exc:                # BaseException: see _load_py_file
            raise AtompipeError(
                f"negative-control fixture module {target!r} called sys.exit({exc.code!r}) "
                f"while importing — the control cannot be built, so the gate is unproven"
            ) from exc
        except Exception as exc:                 # noqa: BLE001 - user's fixture code
            raise AtompipeError(
                f"negative-control fixture module {target!r} failed to import "
                f"({type(exc).__name__}: {exc})"
            ) from exc
        wanted = func_name or "make"
        source = target

    fn = getattr(module, wanted, None)
    if fn is None:
        raise AtompipeError(
            f"negative-control fixture {source!r} has no {wanted}() function — a fixture "
            f"exposes `def make(ctx):` returning a GateContext or a dict merged into "
            f"ctx.extra"
        )
    if not callable(fn):
        raise AtompipeError(f"negative-control fixture {source}:{wanted} is not callable")
    return fn


def _fixture_root(spec: GateSpec, ctx: GateContext, fn: Callable[..., Any] | None = None) -> str:
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
       from the caller at all, which is the case that actually happens.
    4. ``ctx.root`` — a project's own gates, whose ``selftest/`` sits beside the
       model.

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
        module = sys.modules.get(getattr(fn, "__module__", "") or "")
        pack_dir = getattr(module, "PACK_DIR", "") if module is not None else ""
        if isinstance(pack_dir, str) and pack_dir:
            return pack_dir
    return ctx.root or os.curdir


def _build_control(spec: GateSpec, fn: Callable[[GateContext], Any], ctx: GateContext, *,
                   trace: GateTrace, out_dir: str | None
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
    the fixture module is not in the window: code is the closure's business, and
    ``trace.fixture_code`` records the closure of the module ``make`` came from
    (``None`` for a ``module:function`` fixture the stock import system loaded).

    A fixture that builds its own context keeps it as built: cad-solid's fixtures
    assign params on contexts they made from the pack baseline (packs:H15), and
    those are theirs to edit. A dict is merged into the copy's ``extra``.
    """
    nc = spec.negative_control
    host = traced_context(dataclasses.replace(ctx, out_dir=out_dir) if out_dir else ctx,
                          trace, readonly=False)
    try:
        make = load_fixture(nc.fixture, _fixture_root(spec, ctx, fn))
        trace.fixture_code = modelio.code_closure(make)
        with tracing(trace):
            built = make(host)
    except AtompipeError as exc:
        return None, {"error": "negative control unusable", "detail": str(exc)}
    except (SystemExit, GeneratorExit) as exc:
        # Same hole as run_gate's: neither is an Exception, so the clause below
        # would miss them and a fixture that exits would abort `gate selftest`
        # mid-run with nothing printed and nothing recorded. The honest reading of
        # a control that exits the process is that the control is unusable.
        code = exc.code if isinstance(exc, SystemExit) else None
        return None, {
            "error": f"fixture called sys.exit({code!r})" if isinstance(exc, SystemExit)
                     else "fixture raised GeneratorExit",
            "detail": f"{nc.fixture} must build known-bad input and return it, not exit "
                      f"the process — the control is unusable, so {spec.id} is unproven",
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
            "detail": f"{nc.fixture} must return a GateContext or a dict to merge into "
                      f"ctx.extra; returning nothing means the gate ran against the GOOD "
                      f"input and any result is meaningless",
        }
    return None, {"error": f"fixture returned {type(built).__name__}",
                  "detail": f"{nc.fixture} must return a GateContext or a dict for ctx.extra"}


def run_fixture(spec: GateSpec, fn: Callable[[GateContext], Any], ctx: GateContext, *,
                trace: GateTrace | None, out_dir: str | None) -> GateContext:
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
    bad_ctx, problem = _build_control(spec, fn, ctx, trace=trace, out_dir=out_dir)
    if problem is not None:
        raise AtompipeError(f"{spec.id}: {problem['error']} — {problem['detail']}")
    return bad_ctx


def selftest(spec: GateSpec, fn: Callable[[GateContext], Any], ctx: GateContext, *,
             trace: GateTrace | None = None, out_dir: str | None = None) -> Verdict:
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
    bad_ctx, problem = _build_control(spec, fn, ctx, trace=trace, out_dir=out_dir)
    if problem is not None:
        elapsed, cpu = clock.spent()
        return verdict(passed=False, duration_s=round(elapsed, 6), cpu_s=round(cpu, 6),
                       **problem)

    inner = run_gate(spec, fn, bad_ctx, trace=trace)
    elapsed, cpu = clock.spent()
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
    if spec.title:
        bits.append(spec.title)
    if spec.settles:
        bits.append(f"settles {spec.settles}")
    if spec.claims:
        bits.append("claims " + ",".join(spec.claims))
    needs = list(spec.requires_tools or []) + list(spec.requires_python or [])
    if spec.requires_one_of:
        needs.append("one of " + "|".join(spec.requires_one_of))
    if needs:
        ok, reason = availability(spec)
        bits.append(("needs " + ",".join(needs)) if ok else f"BLOCKED: {reason}")
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
