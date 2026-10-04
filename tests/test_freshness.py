# SPDX-License-Identifier: Apache-2.0
"""Freshness, admission state and the one resolver: what a reader may call current.

Every reader — ``status``, the report, the site, JUnit, ``why`` — asks one
function what each gate's effective verdict is (R-5): ``verdicts.resolve``. It
never runs a gate or a fixture. It reads the cache entries, recomputes each
entry's content address from what is on disk NOW, and says Fresh, Stale, Unknown
or never run. What each class below holds against:

* **Freshness.** A PASS that reads current after its inputs moved is the
  staleness lie (invariant 7). Before 1.2 there was one global hash: a model that
  did not import turned staleness OFF — status said "unchanged" and listed three
  PROVEN claims for a design that could not be built (S-21) — and ingesting one
  unrelated file made every measurable claim STALE (S-33). Now each gate's rho is
  recomputed from exactly what it read.
* **Resolve.** A crash forgotten; a cached PASS served where its tool is missing
  (invariant 1) or after the same inputs crashed (invariant 2, PD-29); a skip
  shown as never run (S-68 again); an unregistered gate's verdict dropped.
* **AdmissionInResolution** (PD-08, X14). A PASS counts only while the gate's
  control is demonstrated at its current version; a FAIL blocks either way.
* **StaleGatesSemantics.** Staleness per gate, not per project — with
  ``stale=True`` kept as the all-gates alias so the invariant test that pins it
  (``StatusPrecedence.test_stale_is_not_pass``) stays byte-identical (R-6).

Entries are produced the way the sweep will produce them — ``gates.run_gate``
under a trace, then ``record_verdict`` / ``selftest`` + ``record_control`` —
because there is no sweep yet (U20).

Run:  PYTHONPATH=src python3 -m unittest tests.test_freshness -v
"""
from __future__ import annotations

import dataclasses
import json
import os
import sys
import textwrap
import unittest
from unittest import mock

from atompipe import claims, gates, modelio, store, verdicts
from atompipe.models import (
    Claim, ClaimStatus, GateSpec, InputArtifact, Ledger, NegativeControl, Verdict,
)
from atompipe.util import FileDigests
from atompipe.verdicts import GateTrace

import _env


# --------------------------------------------------------------------------- #
# a small project: gates that record what they are asked to do, fixtures that
# can be forbidden from running
# --------------------------------------------------------------------------- #
_GATES = '''\
import dataclasses
import os
from atompipe.gates import gate
from atompipe.models import NegativeControl, Verdict

#: Set by a test that must prove nothing below runs: resolve and freshness read
#: the cache, and a gate function that ran would have had to be called.
FORBID = [False]
CALLS = []
#: Makes t.flaky crash after its first read (a check --force at unchanged inputs).
CRASH = [False]


def _guard(name):
    if FORBID[0]:
        raise AssertionError(f"{name} ran: freshness and resolve never run a gate")
    CALLS.append(name)


@gate(id="t.defl", title="t", claims=["stiffness"],
      negative_control=NegativeControl(fixture="selftest/bad.py:floppy"))
def defl(ctx):
    _guard("t.defl")
    d = float(ctx.params["deflection"])
    load = ctx.params["config"]["load_n"]
    return Verdict(gate="t.defl", passed=d <= 0.5, measured=d, limit=0.5, units="mm",
                   detail=f"{d:.3f} mm at {load:.0f} N")


@gate(id="t.bed", title="t", claims=["bed-fit"],
      negative_control=NegativeControl(fixture="selftest/bad.py:huge"))
def bed(ctx):
    _guard("t.bed")
    usable = float(ctx.params["usable_bed"])
    length = float(ctx.params["plate_len"])
    bed_xy = ctx.params["config"]["bed_xy"]
    brim = ctx.params["config"]["brim_mm"]
    return Verdict(gate="t.bed", passed=length <= usable, measured=length, limit=usable,
                   units="mm", detail=f"{length} vs {usable} ({bed_xy} bed - 2x{brim} brim)")


@gate(id="t.many", title="t", claims=["many"],
      negative_control=NegativeControl(fixture="selftest/bad.py:many_bad"))
def many(ctx):
    _guard("t.many")
    c = ctx.params["config"]
    total = sum(float(c[k]) for k in ("a", "b", "c", "d", "e"))
    return Verdict(gate="t.many", passed=total < 1000, measured=total, limit=1000.0)


@gate(id="t.mode", title="t", claims=["mode"],
      negative_control=NegativeControl(fixture="selftest/bad.py:mode_bad"))
def mode(ctx):
    _guard("t.mode")
    if ctx.params["config"]["mode"] == "a":
        x = float(ctx.params["config"]["x"])
    else:
        x = float(ctx.params["config"]["y"])
    return Verdict(gate="t.mode", passed=x < 10, measured=x, limit=10.0)


@gate(id="t.flaky", title="t", claims=["flaky"],
      negative_control=NegativeControl(fixture="selftest/bad.py:flaky_bad"))
def flaky(ctx):
    _guard("t.flaky")
    x = float(ctx.params["config"]["x"])
    if CRASH[0]:
        raise RuntimeError("tripped over its own input")
    y = float(ctx.params["config"]["y"])
    return Verdict(gate="t.flaky", passed=x + y < 10, measured=x + y, limit=10.0)


@gate(id="t.file", title="t", claims=["file"],
      negative_control=NegativeControl(fixture="selftest/bad.py:file_bad"))
def from_file(ctx):
    _guard("t.file")
    with open(os.path.join(ctx.root, "data", "limit.txt"), encoding="utf-8") as fh:
        limit = float(fh.read())
    return Verdict(gate="t.file", passed=limit >= 1.0, measured=limit, limit=1.0)


@gate(id="t.opaque", title="t", claims=["opaque"],
      negative_control=NegativeControl(fixture="selftest/bad.py:opaque_bad"))
def opaque(ctx):
    _guard("t.opaque")
    tags = ctx.params["config"]["tags"]
    return Verdict(gate="t.opaque", passed=len(tags) < 5, measured=float(len(tags)),
                   limit=5.0)


@gate(id="t.opt", title="t", claims=["opt"],
      negative_control=NegativeControl(fixture="selftest/bad.py:opt_bad"))
def optional(ctx):
    _guard("t.opt")
    margin = ctx.params.get("margin", 0.0)
    return Verdict(gate="t.opt", passed=margin < 10, measured=float(margin), limit=10.0)


@gate(id="t.claim", title="t", claims=["claimread"],
      negative_control=NegativeControl(fixture="selftest/bad.py:claim_bad"))
def claim_read(ctx):
    _guard("t.claim")
    c1 = ctx.ledger.claim("C1")
    x = float(ctx.params["config"]["x"])
    return Verdict(gate="t.claim", passed=c1 is not None and x < 10, measured=x, limit=10.0)
'''

_FIXTURES = '''\
import dataclasses
import os

from atompipe.modelio import load_path

#: A helper OUTSIDE selftest/: editing it moves this fixture's code closure but
#: not the control's static part — the bracket's fixtures importing its model.
helper = load_path(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "model", "helper.py"))

FORBID = [False]
#: Makes flaky_bad raise: a control that crashed on its fixture.
BROKEN = [False]
#: Makes floppy build a GOOD design: a control that never goes bad.
DEFUSED = [False]


def _guard():
    if FORBID[0]:
        raise AssertionError("a fixture ran: freshness and resolve never run a fixture")


def _cfg(**over):
    cfg = dict(helper.GOOD)
    cfg.update(over)
    return cfg


def _ctx(ctx, flat):
    return dataclasses.replace(ctx, params=flat)


def floppy(ctx):
    _guard()
    d = 0.3 if DEFUSED[0] else 30.0
    return _ctx(ctx, {"deflection": d, "config": _cfg()})


def huge(ctx):
    _guard()
    return _ctx(ctx, {"usable_bed": 204.0, "plate_len": 900.0, "config": _cfg()})


def many_bad(ctx):
    _guard()
    return _ctx(ctx, {"config": _cfg(a=5000.0)})


def mode_bad(ctx):
    _guard()
    return _ctx(ctx, {"config": _cfg(mode="a", x=50.0)})


def flaky_bad(ctx):
    _guard()
    if BROKEN[0]:
        raise RuntimeError("the known-bad input could not be built")
    return _ctx(ctx, {"config": _cfg(x=50.0, y=0.0)})


def file_bad(ctx):
    _guard()
    # the same gate, pointed at a project whose data/limit.txt is too low: a
    # read under selftest/, which the control's static part already keys
    low = os.path.join(os.path.dirname(os.path.abspath(__file__)), "low")
    return dataclasses.replace(ctx, root=low)


def opaque_bad(ctx):
    _guard()
    return _ctx(ctx, {"config": _cfg(tags=[1, 2, 3, 4, 5, 6])})


def claim_bad(ctx):
    _guard()
    return _ctx(ctx, {"config": _cfg(x=50.0)})


def opt_bad(ctx):
    _guard()
    return _ctx(ctx, {"margin": 50.0})
'''

_HELPER = '''\
GOOD = {"load_n": 15.0, "bed_xy": 220.0, "brim_mm": 8.0, "a": 1.0, "b": 1.0, "c": 1.0,
        "d": 1.0, "e": 1.0, "mode": "a", "x": 1.0, "y": 1.0, "tags": [1]}
'''

_ALL = ("t.defl", "t.bed", "t.many", "t.mode", "t.flaky", "t.file", "t.opaque", "t.opt",
        "t.claim")


def _write(root: str, rel: str, text: str) -> str:
    path = os.path.join(root, *rel.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(textwrap.dedent(text))
    return path


def _config(**over) -> dict:
    cfg = {"load_n": 15.0, "bed_xy": 220.0, "brim_mm": 8.0, "a": 1.0, "b": 1.0,
           "c": 1.0, "d": 1.0, "e": 1.0, "mode": "a", "x": 1.0, "y": 1.0,
           "tags": [1, 2]}
    cfg.update(over)
    return cfg


def _projection(derived: dict | None = None, **over) -> dict:
    """The bracket's shape: ``config`` the inputs, ``derived`` what ``build()``
    returned (which echoes ``config`` back), flattened by ``modelio.flat_params``
    exactly as ``check`` hands it to a gate."""
    cfg = _config(**over)
    built = {"deflection": 0.3, "usable_bed": cfg["bed_xy"] - 2 * cfg["brim_mm"],
             "plate_len": 73.5, "config": dict(cfg)}
    built.update(derived or {})
    return {"config": cfg, "derived": built}


def _ledger() -> Ledger:
    tags = {"C1": "stiffness", "C2": "bed-fit", "C3": "many", "C4": "mode", "C5": "flaky",
            "C6": "file", "C7": "opaque", "C8": "claimread", "C9": "opt"}
    return Ledger(claims=[Claim(id=cid, statement=f"claim {cid}", tags=[tag])
                          for cid, tag in tags.items()])


class _Project:
    """One temp project with the gates and fixtures above, loaded into a fresh
    registry — never ``gates.REGISTRY``."""

    def __init__(self, case: _env.EnvCase) -> None:
        self.root = root = os.path.join(case.tmp(), "project")
        _write(root, ".atompipe/project.json", '{"schema": 2}\n')
        _write(root, "gates/g.py", _GATES)
        _write(root, "selftest/bad.py", _FIXTURES)
        _write(root, "model/helper.py", _HELPER)
        _write(root, "data/limit.txt", "2.0\n")
        _write(root, "selftest/low/data/limit.txt", "0.5\n")
        self.registry = gates.Registry()
        gates.load_project_gates(root, self.registry)
        self.anchors = verdicts.anchors_for(root, self.registry, out_dir=store.out_dir(root))
        self.ledger = _ledger()

    # -- the module objects, for the flags ---------------------------------- #
    @property
    def gate_module(self):
        _spec, fn = self.registry.get("t.defl")
        return sys.modules[fn.__module__]

    @property
    def fixture_module(self):
        make = gates.load_fixture("selftest/bad.py:floppy", self.root)
        return sys.modules[make.__module__]

    def pair(self, gate_id):
        return self.registry.get(gate_id)

    def ctx(self, projection, ledger=None) -> gates.GateContext:
        params, _conflicts = modelio.flat_params(projection)
        return gates.GateContext(root=self.root, ledger=ledger or self.ledger, model=None,
                                 params=params, out_dir=store.out_dir(self.root), tier=3,
                                 log=lambda _m: None, extra={})

    def run(self, gate_id, projection, ledger=None):
        spec, fn = self.pair(gate_id)
        trace = GateTrace(anchors=self.anchors)
        return gates.run_gate(spec, fn, self.ctx(projection, ledger), trace=trace), trace

    def record(self, gate_id, projection, ledger=None):
        """Run ``gate_id`` and cache its verdict, as the sweep will."""
        spec, fn = self.pair(gate_id)
        verdict, trace = self.run(gate_id, projection, ledger)
        wrote = verdicts.record_verdict(self.root, spec, fn, verdict, trace=trace,
                                        anchors=self.anchors, digests=FileDigests())
        return verdict, wrote

    def demonstrate(self, gate_id, projection=None):
        """Run ``gate_id``'s control and record it, as the sweep will — its
        known-bad half run, its known-good half planted as passed and, where
        that control held, a walk that made none (``good="pass"``,
        ``mutation=()``: R-6, P2.3 — the half these tests are not about; without
        it every entry is incomplete, D19, and a walk is recorded only where
        both controls held)."""
        spec, fn = self.pair(gate_id)
        trace = GateTrace(kind="control", anchors=self.anchors)
        result = gates.selftest(spec, fn, self.ctx(projection or _projection()), trace=trace,
                                out_dir=os.path.join(self.root, verdicts.CONTROL_OUT_DIR,
                                                     spec.id))
        return result, verdicts.record_control(self.root, spec, fn, result=result, trace=trace,
                                               anchors=self.anchors, digests=FileDigests(),
                                               when="2026-09-27T10:00:00Z", good="pass",
                                               mutation=() if result.passed else None)

    def freshness(self, projection, ledger=None, **kw):
        return verdicts.freshness(self.root, self.registry, projection,
                                  ledger or self.ledger, anchors=self.anchors,
                                  digests=FileDigests(), **kw)

    def resolve(self, projection, ledger=None, **kw):
        return verdicts.resolve(self.root, self.registry, projection, ledger or self.ledger,
                                anchors=self.anchors, digests=FileDigests(), **kw)

    def statuses(self, resolution, ledger=None):
        view = dataclasses.replace(ledger or self.ledger, verdicts=resolution.verdicts)
        return claims.statuses(view, registry=self.registry,
                               stale_gates=resolution.stale_gates)


def _row_verdict(resolution, gate_id) -> Verdict:
    found = [v for v in resolution.verdicts if v.gate == gate_id]
    assert len(found) == 1, (gate_id, [v.gate for v in resolution.verdicts])
    return found[0]


_PROJECTION = _projection()
_MISSING = lambda _spec: (False, "requires python trimesh (not importable)")  # noqa: E731


# --------------------------------------------------------------------------- #
class Freshness(_env.EnvCase):
    """A gate's entries, judged against what is on disk now — by digest, never
    by running anything."""

    def test_freshness_never_runs_a_gate(self):
        # The verifying-trace shape (M11.11): judging an entry recomputes rho
        # from digests. A gate function that ran — or a fixture, which admission
        # would be tempted to run — could have had side effects, cost minutes of
        # solver time, and would make `status` a second `check`.
        p = _Project(self)
        for gate_id in ("t.defl", "t.bed", "t.flaky"):
            p.record(gate_id, _PROJECTION)
            p.demonstrate(gate_id)
        gate_mod, fixture_mod = p.gate_module, p.fixture_module
        calls_before = list(gate_mod.CALLS)
        gate_mod.FORBID[0] = fixture_mod.FORBID[0] = True
        self.addCleanup(gate_mod.FORBID.__setitem__, 0, False)
        self.addCleanup(fixture_mod.FORBID.__setitem__, 0, False)

        refuse = AssertionError("the resolver reached the runner")
        with mock.patch.object(gates, "run_gate", side_effect=refuse), \
                mock.patch.object(gates, "selftest", side_effect=refuse), \
                mock.patch.object(gates, "run_fixture", side_effect=refuse), \
                mock.patch.object(gates, "load_fixture", side_effect=refuse), \
                mock.patch.object(gates, "run_all", side_effect=refuse):
            fresh = p.freshness(_PROJECTION)
            moved = p.freshness(_projection(bed_xy=250.0))
            broken = p.freshness(None)
            for gate_id in ("t.defl", "t.bed", "t.flaky"):
                spec, fn = p.pair(gate_id)
                state = verdicts.admission_state(p.root, spec, fn, projection=_PROJECTION,
                                                 digests=FileDigests(), anchors=p.anchors)
                self.assertEqual(state.state, "admitted", (gate_id, state))
            resolution = p.resolve(_PROJECTION)
            p.resolve(None, model_error="SyntaxError: bad model")

        self.assertEqual(gate_mod.CALLS, calls_before, "a gate function ran")
        self.assertIsInstance(fresh["t.defl"], verdicts.Fresh)
        self.assertIsInstance(moved["t.bed"], verdicts.Stale)
        self.assertIsInstance(moved["t.defl"], verdicts.Fresh)
        self.assertIsInstance(broken["t.defl"], verdicts.Unknown)
        self.assertIsInstance(fresh["t.many"], verdicts.Never)
        self.assertEqual(_row_verdict(resolution, "t.defl").outcome, "pass")

    def test_every_registered_gate_gets_a_state(self):
        p = _Project(self)
        p.record("t.defl", _PROJECTION)
        states = p.freshness(_PROJECTION)
        self.assertEqual(sorted(states), sorted(_ALL))
        self.assertEqual(states["t.many"].state, "never")
        self.assertEqual(states["t.defl"].state, "fresh")

    def test_rho_is_recomputed_exactly_as_a_run_would_record_it(self):
        # The oracle: the rho freshness computes WITHOUT running equals the rho
        # a run at those inputs records. If these ever differ, an honest entry
        # reads stale forever (cost) or a moved one reads fresh (the lie).
        p = _Project(self)
        _v, wrote = p.record("t.bed", _PROJECTION)
        state = p.freshness(_PROJECTION)["t.bed"]
        self.assertIsInstance(state, verdicts.Fresh)
        self.assertEqual(state.rho, wrote.rho)
        self.assertEqual(state.entry.rho, wrote.rho)

        moved = _projection(bed_xy=250.0)
        stale = p.freshness(moved)["t.bed"]
        self.assertIsInstance(stale, verdicts.Stale)
        self.assertEqual(stale.entry.rho, wrote.rho, "stale shows the entry it had")
        _v2, rewrote = p.record("t.bed", moved)
        self.assertEqual(stale.rho, rewrote.rho,
                         "the rho freshness recomputed is the rho the run then recorded")
        self.assertIsInstance(p.freshness(moved)["t.bed"], verdicts.Fresh)
        back = p.freshness(_PROJECTION)["t.bed"]
        self.assertIsInstance(back, verdicts.Fresh, "reverting finds the first entry again")
        self.assertEqual(back.entry.rho, wrote.rho)

    def test_entries_are_grouped_by_read_signature(self):
        # t.mode reads config.x on mode "a" and config.y on mode "b": two read
        # signatures. Each group is recomputed against its OWN paths, so moving
        # y leaves the mode-"a" entry fresh — it never read y.
        p = _Project(self)
        _va, a = p.record("t.mode", _projection(mode="a"))
        _vb, b = p.record("t.mode", _projection(mode="b"))
        self.assertNotEqual(a.rho, b.rho)
        on_a = p.freshness(_projection(mode="a", y=7.0))["t.mode"]
        self.assertIsInstance(on_a, verdicts.Fresh)
        self.assertEqual(on_a.entry.rho, a.rho)
        on_b = p.freshness(_projection(mode="b", x=7.0))["t.mode"]
        self.assertIsInstance(on_b, verdicts.Fresh)
        self.assertEqual(on_b.entry.rho, b.rho)
        self.assertEqual(len(on_b.current), 2, "one recomputed rho per read signature")
        self.assertIsInstance(p.freshness(_projection(mode="b", y=7.0))["t.mode"],
                              verdicts.Stale)

    def test_unknown_on_a_model_that_does_not_import(self):
        # S-21: a model that failed to import disabled staleness — status said
        # "unchanged" and listed PROVEN claims for a design that could not be
        # built. With no projection nothing a gate read can be compared: never
        # Fresh, and never quietly current.
        p = _Project(self)
        for gate_id in ("t.defl", "t.file"):
            p.record(gate_id, _PROJECTION)
            p.demonstrate(gate_id)
        states = p.freshness(None)
        self.assertIsInstance(states["t.defl"], verdicts.Unknown)
        self.assertIn("model", states["t.defl"].reason)
        self.assertIsInstance(states["t.file"], verdicts.Fresh,
                              "a gate that read no parameter does not depend on the model")

        resolution = p.resolve(None, model_error="SyntaxError: invalid syntax (bracket.py, "
                                                 "line 3)")
        self.assertIn("t.defl", resolution.stale_gates)
        self.assertIn("SyntaxError", resolution.rows["t.defl"].stale_reason)
        status = p.statuses(resolution)
        self.assertEqual(status["C1"], ClaimStatus.STALE)
        self.assertEqual(status["C6"], ClaimStatus.PASS)

    def test_opaque_is_never_fresh(self):
        # A value the tracer cannot digest (here a set in the projection) can
        # change unseen; serving the cached PASS then is the lie invariant 7
        # forbids. The entry is kept and shown — never as current.
        p = _Project(self)
        proj = _projection(tags={1, 2})
        _v, wrote = p.record("t.opaque", proj)
        with open(wrote.path, encoding="utf-8") as fh:
            self.assertTrue(json.load(fh)["reads"]["opaque"])
        state = p.freshness(proj)["t.opaque"]
        self.assertIsInstance(state, verdicts.Unknown)
        self.assertTrue(state.reason.startswith("opaque inputs: "), state.reason)
        self.assertTrue(state.rho, "the rho is still recomputable, for a remembered crash")
        self.assertNotEqual(p.statuses(p.resolve(proj))["C7"], ClaimStatus.PASS)

    def test_an_empty_spine_digest_makes_everything_unknown(self):
        # S-29: a spine that cannot read its own sources (a wheel without .py)
        # cannot say what semantics a verdict was computed under.
        p = _Project(self)
        p.record("t.defl", _PROJECTION)
        with mock.patch.object(verdicts, "spine_digest", return_value=""):
            state = p.freshness(_PROJECTION)["t.defl"]
        self.assertIsInstance(state, verdicts.Unknown)
        self.assertIn("spine", state.reason)

    def test_unrecorded_code_is_unknown(self):
        p = _Project(self)
        p.record("t.defl", _PROJECTION)
        with mock.patch.object(verdicts, "code_digest",
                               return_value=verdicts.CodeRef(opaque="code not loaded from a file")):
            state = p.freshness(_PROJECTION)["t.defl"]
        self.assertIsInstance(state, verdicts.Unknown)
        self.assertIn("code not loaded from a file", state.reason)

    def test_small_values_name_the_move(self):
        # The transcript's line: `stale: bracket.bed_fit — config.bed_xy 220.0 ->
        # 250.0`. t.bed also reads usable_bed, which moved WITH bed_xy: a derived
        # value that moved alongside an input is that input's consequence, and
        # naming it would push the cause off a three-reason line.
        p = _Project(self)
        p.record("t.bed", _PROJECTION)
        state = p.freshness(_projection(bed_xy=250.0))["t.bed"]
        self.assertIsInstance(state, verdicts.Stale)
        self.assertEqual(state.reasons, ("config.bed_xy 220.0 -> 250.0",))

        # build() changed and no input did: the derived value IS the news
        derived = p.freshness(_projection(derived={"usable_bed": 300.0}))["t.bed"]
        self.assertEqual(derived.reasons, ("usable_bed 204.0 -> 300.0",))

        # a value too large to show beside its digest reads "changed"
        p.record("t.opaque", _projection(tags=[1, 2]))
        big = p.freshness(_projection(tags=list(range(40))))["t.opaque"]
        self.assertEqual(big.reasons, ("config.tags changed",))

    def test_more_than_three_reasons_are_counted(self):
        p = _Project(self)
        p.record("t.many", _PROJECTION)
        # R-6 (P2.3): qualified, so the stale row is the entry's and not "not
        # yet qualified" — an evaluator never qualified reads Gap, stale or not.
        p.demonstrate("t.many")
        state = p.freshness(_projection(a=2.0, b=2.0, c=2.0, d=2.0, e=2.0))["t.many"]
        self.assertEqual(verdicts.MAX_STALE_REASONS, 3)
        self.assertEqual(state.reasons, ("config.a 1.0 -> 2.0", "config.b 1.0 -> 2.0",
                                         "config.c 1.0 -> 2.0", "(+2 more)"))
        resolution = p.resolve(_projection(a=2.0, b=2.0, c=2.0, d=2.0, e=2.0))
        self.assertEqual(resolution.rows["t.many"].stale_reason,
                         "config.a 1.0 -> 2.0, config.b 1.0 -> 2.0, config.c 1.0 -> 2.0 "
                         "(+2 more)")

    def test_a_missing_key_that_appears_is_a_move(self):
        # t.opt asks for `margin` and falls back when it is absent: "absent" is
        # an input, and a model that grows the key moves it.
        p = _Project(self)
        p.record("t.opt", _PROJECTION)
        p.record("t.defl", _PROJECTION)
        self.assertIsInstance(p.freshness(_PROJECTION)["t.opt"], verdicts.Fresh)
        grown = _projection(derived={"margin": 3.0})
        states = p.freshness(grown)
        self.assertIsInstance(states["t.opt"], verdicts.Stale)
        self.assertEqual(states["t.opt"].reasons, ("margin absent -> 3.0",))
        self.assertIsInstance(states["t.defl"], verdicts.Fresh,
                              "a gate that never asked for margin does not care")

    def test_file_code_and_spine_moves_are_named(self):
        p = _Project(self)
        p.record("t.file", _PROJECTION)
        _write(p.root, "data/limit.txt", "3.0\n")
        state = p.freshness(_PROJECTION)["t.file"]
        self.assertIsInstance(state, verdicts.Stale)
        self.assertEqual(state.reasons, ("data/limit.txt changed",))

        _write(p.root, "data/limit.txt", "2.0\n")
        self.assertIsInstance(p.freshness(_PROJECTION)["t.file"], verdicts.Fresh)
        with mock.patch.object(verdicts, "spine_digest", return_value="f" * 64):
            self.assertEqual(p.freshness(_PROJECTION)["t.file"].reasons,
                             ("atompipe spine changed",))

        # a gate module edited on disk, loaded fresh into a new registry
        with open(os.path.join(p.root, "gates", "g.py"), "a", encoding="utf-8") as fh:
            fh.write("\n# an edit\nEDITED = True\n")
        registry = gates.Registry()
        gates.load_project_gates(p.root, registry)
        moved = verdicts.freshness(p.root, registry, _PROJECTION, p.ledger,
                                   anchors=p.anchors, digests=FileDigests())["t.file"]
        self.assertIsInstance(moved, verdicts.Stale)
        self.assertEqual(moved.reasons, ("gate code changed",))

    def test_a_claim_the_gate_read_is_an_input(self):
        # S-23: openmodelica reads a claim's limit through ctx.ledger.
        p = _Project(self)
        p.record("t.claim", _PROJECTION)
        self.assertIsInstance(p.freshness(_PROJECTION)["t.claim"], verdicts.Fresh)
        edited = _ledger()
        edited.claims[0].statement = "claim C1, sharpened"
        state = p.freshness(_PROJECTION, ledger=edited)["t.claim"]
        self.assertIsInstance(state, verdicts.Stale)
        self.assertEqual(state.reasons, ("claim C1 changed",))

    def test_an_unrelated_record_moves_nothing(self):
        # S-33: ingesting one unrelated artifact made every measurable claim
        # STALE through the global inputs hash. No gate read it, so no gate is.
        p = _Project(self)
        p.record("t.claim", _PROJECTION)
        p.record("t.defl", _PROJECTION)
        _write(p.root, "inputs/datasheet.txt", "an artifact nobody reads\n")
        more = _ledger()
        more.inputs.append(InputArtifact(id="A1", path="inputs/datasheet.txt"))
        more.claims[3].statement = "claim C4, reworded"
        states = p.freshness(_PROJECTION, ledger=more)
        self.assertIsInstance(states["t.claim"], verdicts.Fresh)
        self.assertIsInstance(states["t.defl"], verdicts.Fresh)

    def test_instruments_are_a_note_never_staleness(self):
        # Q1.3: an entry recorded under another library version stays Fresh,
        # and says so.
        p = _Project(self)
        _v, wrote = p.record("t.defl", _PROJECTION)
        [entry] = verdicts.read_entries(p.root, "t.defl")
        os.unlink(wrote.path)
        elsewhere = {"atompipe_no_such_module": "1.0"}
        verdicts.write_entry(p.root, dataclasses.replace(entry, instruments=elsewhere))
        state = p.freshness(_PROJECTION)["t.defl"]
        self.assertIsInstance(state, verdicts.Fresh)
        self.assertEqual(state.notes, ("recorded under atompipe_no_such_module 1.0; here absent",))
        resolution = p.resolve(_PROJECTION)
        self.assertIn("t.defl — recorded under atompipe_no_such_module 1.0; here absent",
                      resolution.notes)


# --------------------------------------------------------------------------- #
class Resolve(_env.EnvCase):
    """The one effective-verdict producer every reader uses."""

    def test_a_legacy_ledger_verdict_is_stale(self):
        # Q1.4: a verdict recorded before 1.2 names no inputs, so nothing can
        # say it is current.
        p = _Project(self)
        legacy = dataclasses.replace(p.ledger, verdicts=[
            Verdict(gate="t.defl", claims=["stiffness"], passed=True, measured=0.3,
                    limit=0.5)])
        resolution = p.resolve(_PROJECTION, ledger=legacy)
        verdict = _row_verdict(resolution, "t.defl")
        self.assertEqual(verdict.outcome, "pass")
        self.assertIn("t.defl", resolution.stale_gates)
        self.assertEqual(resolution.rows["t.defl"].stale_reason,
                         "recorded before per-gate tracing")
        self.assertEqual(resolution.rows["t.defl"].state, "legacy")
        self.assertEqual(p.statuses(resolution, ledger=legacy)["C1"], ClaimStatus.STALE)

        # an entry supersedes the legacy row
        p.record("t.defl", _PROJECTION)
        p.demonstrate("t.defl")
        again = p.resolve(_PROJECTION, ledger=legacy)
        self.assertNotIn("t.defl", again.stale_gates)
        self.assertTrue(again.rows["t.defl"].cached)

    def test_an_orphan_entry_is_a_stale_row_after_the_registered_gates(self):
        # tests:H2 — an unregistered gate's verdict still reaches the page.
        p = _Project(self)
        p.record("t.defl", _PROJECTION)
        p.demonstrate("t.defl")
        verdicts.record_verdict(p.root, None, None,
                                Verdict(gate="z.gone", claims=["stiffness"], passed=True))
        verdicts.record_verdict(p.root, None, None,
                                Verdict(gate="a.gone", claims=["bed-fit"], passed=False))
        resolution = p.resolve(_PROJECTION)
        # R-6 (P2.3): every registered gate never run reads not yet qualified
        # (an unqualified verdict, its claim Gap: critique of the P2.3 design);
        # the order this test pins is of the gates that have something.
        self.assertEqual([v.gate for v in resolution.verdicts
                          if v.unqualified != "qualification:not-yet|0"],
                         ["t.defl", "a.gone", "z.gone"])
        for gate_id in ("a.gone", "z.gone"):
            self.assertIn(gate_id, resolution.stale_gates)
            self.assertEqual(resolution.rows[gate_id].stale_reason,
                             "gate not registered in this project")
            self.assertEqual(resolution.rows[gate_id].state, "orphan")
        status = p.statuses(resolution)
        self.assertEqual(status["C1"], ClaimStatus.STALE, "an orphan PASS never counts")
        self.assertEqual(status["C2"], ClaimStatus.FAIL, "an orphan FAIL still blocks")

    def test_a_cached_pass_where_its_tool_is_missing_is_skipped(self):
        # Invariant 1: a PASS committed from a machine with trimesh never reads
        # PASS on one without.
        p = _Project(self)
        p.record("t.defl", _PROJECTION)
        p.demonstrate("t.defl")
        resolution = p.resolve(_PROJECTION, availability=_MISSING)
        verdict = _row_verdict(resolution, "t.defl")
        self.assertEqual(verdict.outcome, "skipped")
        self.assertEqual(verdict.skip_reason,
                         "cached pass exists; requires python trimesh (not importable) here")
        self.assertFalse(resolution.rows["t.defl"].cached)
        self.assertEqual(p.statuses(resolution)["C1"], ClaimStatus.BLOCKED)

    def test_a_fresh_fail_is_served_where_its_tool_is_missing(self):
        # R-3: a refutation keeps its power on every machine.
        p = _Project(self)
        verdict, _w = p.record("t.defl", _projection(derived={"deflection": 0.9}))
        self.assertEqual(verdict.outcome, "fail")
        resolution = p.resolve(_projection(derived={"deflection": 0.9}), availability=_MISSING)
        served = _row_verdict(resolution, "t.defl")
        self.assertEqual(served.outcome, "fail")
        self.assertEqual(served.measured, 0.9)
        self.assertTrue(resolution.rows["t.defl"].cached)
        self.assertEqual(p.statuses(resolution)["C1"], ClaimStatus.FAIL)

    def test_a_crash_supersedes_the_pass_it_followed(self):
        # PD-29, invariant 2: `check --force` turns a cached PASS into a crash
        # at unchanged inputs. The crash read FEWER inputs than the pass (it
        # died after the first), so its own rho is not the pass's — keyed on
        # that, it would never supersede, and the next plain check would serve
        # the old PASS. It is remembered under the rho freshness computed just
        # before the run: the Fresh PASS's.
        p = _Project(self)
        _v, passed = p.record("t.flaky", _PROJECTION)
        p.demonstrate("t.flaky")
        before = p.resolve(_PROJECTION)
        self.assertEqual(_row_verdict(before, "t.flaky").outcome, "pass")
        self.assertEqual(p.statuses(before)["C5"], ClaimStatus.PASS)
        state = p.freshness(_PROJECTION)["t.flaky"]
        self.assertIsInstance(state, verdicts.Fresh)
        input_rho = state.rho
        self.assertEqual(input_rho, passed.rho)

        gate_mod = p.gate_module
        gate_mod.CRASH[0] = True
        self.addCleanup(gate_mod.CRASH.__setitem__, 0, False)
        crashed, trace = p.run("t.flaky", _PROJECTION)
        self.assertEqual(crashed.outcome, "error")
        spec, fn = p.pair("t.flaky")
        partial = verdicts.rho(spec.id, verdicts.spine_digest(),
                               verdicts.code_digest(spec, fn, anchors=p.anchors),
                               verdicts.Reads.from_trace(trace, anchors=p.anchors))
        self.assertNotEqual(partial, passed.rho, "the crash read less than the pass did")
        crashed = dataclasses.replace(crashed, rho=partial)
        self.assertIsNone(verdicts.record_verdict(p.root, spec, fn, crashed, trace=trace,
                                                  anchors=p.anchors))
        verdicts.remember(p.root, spec.id, crashed, input_rho=input_rho, kind="error",
                          when="2026-09-27T11:00:00Z")

        after = p.resolve(_PROJECTION)
        served = _row_verdict(after, "t.flaky")
        self.assertEqual(served.outcome, "error")
        self.assertEqual(served.rho, partial, "the crash carries its own partial-read rho")
        # P2.1 (R-6, a V1 row): a crash reads Skipped, errored — never FAIL.
        self.assertEqual(p.statuses(after)["C5"], ClaimStatus.BLOCKED)
        self.assertFalse(after.rows["t.flaky"].cached)
        self.assertEqual(after.rows["t.flaky"].when, "2026-09-27T11:00:00Z")

        # a later recorded pass clears it
        gate_mod.CRASH[0] = False
        p.record("t.flaky", _PROJECTION)
        cleared = p.resolve(_PROJECTION)
        self.assertEqual(_row_verdict(cleared, "t.flaky").outcome, "pass")
        self.assertEqual(p.statuses(cleared)["C5"], ClaimStatus.PASS)
        self.assertNotIn("t.flaky", verdicts.remembered(p.root))

    def test_a_crash_at_other_inputs_does_not_supersede(self):
        # The crash happened while config.y was 5.0; the inputs are back where
        # the Fresh PASS was recorded. That PASS is still the outcome here.
        p = _Project(self)
        p.record("t.flaky", _PROJECTION)
        p.demonstrate("t.flaky")
        elsewhere = p.freshness(_projection(y=5.0))["t.flaky"]
        self.assertIsInstance(elsewhere, verdicts.Stale)
        verdicts.remember(p.root, "t.flaky",
                          Verdict(gate="t.flaky", claims=["flaky"], error="RuntimeError: x"),
                          input_rho=elsewhere.rho, kind="error", when="")
        self.assertEqual(_row_verdict(p.resolve(_PROJECTION), "t.flaky").outcome, "pass")
        self.assertEqual(_row_verdict(p.resolve(_projection(y=5.0)), "t.flaky").outcome,
                         "error", "at the inputs it crashed on, the crash is what shows")

    def test_remembered_skip_is_shown_never_as_never_run(self):
        # S-68 again: a gate that skipped itself (its tools present) or crashed
        # writes no entry. It must read skipped or errored — never "not yet
        # checked", which would send the reader to run it.
        p = _Project(self)
        verdicts.remember(p.root, "t.defl",
                          Verdict(gate="t.defl", claims=["stiffness"], skipped=True,
                                  skip_reason="no deflection in this model"),
                          input_rho="", kind="self-skip", when="2026-09-27T09:00:00Z")
        verdicts.remember(p.root, "t.bed",
                          Verdict(gate="t.bed", claims=["bed-fit"], error="KeyError: 'x'"),
                          input_rho="", kind="error", when="2026-09-27T09:00:00Z")
        resolution = p.resolve(_PROJECTION)
        self.assertEqual(_row_verdict(resolution, "t.defl").outcome, "skipped")
        self.assertEqual(_row_verdict(resolution, "t.bed").outcome, "error")
        status = p.statuses(resolution)
        self.assertEqual(status["C1"], ClaimStatus.BLOCKED)
        # P2.1 (R-6, a V1 row): a remembered crash reads Skipped, errored.
        self.assertEqual(status["C2"], ClaimStatus.BLOCKED)
        # P2.3 (R-6): a gate with nothing was never qualified, and reads not yet
        # qualified — its claim Gap, where P2.2 read it PENDING (Open). Still
        # nothing of a run: no entry, state never.
        self.assertEqual(status["C3"], ClaimStatus.UNCLAIMED, "a gate with nothing is Gap")
        self.assertEqual((resolution.rows["t.many"].state, resolution.rows["t.many"].entry,
                          resolution.rows["t.many"].admission.reason),
                         ("never", None, "qualification:not-yet|0"))

    def test_a_remembered_availability_skip_shows_only_while_the_tool_is_missing(self):
        p = _Project(self)
        verdicts.remember(p.root, "t.defl",
                          Verdict(gate="t.defl", claims=["stiffness"], skipped=True,
                                  skip_reason="requires python trimesh (not importable)"),
                          input_rho="", kind="availability", when="")
        # R-6 (P2.3): the row of a gate never run is "not yet qualified" now,
        # never the skip — nothing ran, and nothing of it shows.
        here = p.resolve(_PROJECTION).rows["t.defl"]
        self.assertEqual((here.state, here.entry, here.admission.reason),
                         ("never", None, "qualification:not-yet|0"),
                         "the tool is here now: nothing ran, nothing to show")
        missing = p.resolve(_PROJECTION, availability=_MISSING)
        self.assertEqual(_row_verdict(missing, "t.defl").skip_reason,
                         "requires python trimesh (not importable)")

    def test_an_availability_skip_keeps_its_read_set(self):
        # S-30: a skipped gate never calls fn and records nothing; its
        # attribution ("which checks read this number") comes from its last
        # executed entry instead of vanishing on every full sweep.
        p = _Project(self)
        p.record("t.defl", _PROJECTION)
        resolution = p.resolve(_PROJECTION, availability=_MISSING)
        self.assertEqual(_row_verdict(resolution, "t.defl").outcome, "skipped")
        self.assertEqual(resolution.read_sets["t.defl"],
                         {("deflection",), ("config", "load_n")})

    def test_two_outcomes_at_the_current_rho_never_count(self):
        # §3.7: the same inputs, the same libraries, two answers. Stale while
        # TWO_OUTCOMES_IS_ERROR is staged (R-4), an error once it flips —
        # never a silent pick of either.
        p = _Project(self)
        p.record("t.defl", _PROJECTION)
        p.demonstrate("t.defl")
        [entry] = verdicts.read_entries(p.root, "t.defl")
        verdicts.write_entry(p.root, dataclasses.replace(
            entry, verdict={**entry.verdict, "passed": False}))
        # The staged half is held by patching since U25 flipped the flag; the
        # flipped half below is unchanged, and test_determinism.TwoOutcomes
        # holds the real value through the CLI.
        with mock.patch.object(verdicts, "TWO_OUTCOMES_IS_ERROR", False):
            resolution = p.resolve(_PROJECTION)
        self.assertIn("t.defl", resolution.stale_gates)
        self.assertTrue(resolution.rows["t.defl"].stale_reason.startswith(
            "two outcomes recorded for identical inputs"), resolution.rows["t.defl"])
        self.assertNotEqual(p.statuses(resolution)["C1"], ClaimStatus.PASS)
        self.assertTrue(any("two outcomes recorded for identical inputs" in note
                            for note in resolution.notes), resolution.notes)
        with mock.patch.object(verdicts, "TWO_OUTCOMES_IS_ERROR", True):
            flipped = p.resolve(_PROJECTION)
        verdict = _row_verdict(flipped, "t.defl")
        self.assertEqual(verdict.outcome, "error")
        self.assertTrue(verdict.error.startswith("two outcomes recorded for identical inputs"))
        # P2.1 (R-6, a V1 row): the conflict is an error, so Skipped, errored.
        self.assertEqual(p.statuses(flipped)["C1"], ClaimStatus.BLOCKED)

    def test_a_hand_edited_entry_is_ignored_and_named(self):
        p = _Project(self)
        _v, wrote = p.record("t.defl", _PROJECTION)
        with open(wrote.path, encoding="utf-8") as fh:
            text = fh.read()
        with open(wrote.path, "w", encoding="utf-8") as fh:
            fh.write(text.replace('"measured": 0.3', '"measured": 0.1'))
        resolution = p.resolve(_PROJECTION)
        # R-6 (P2.3): a gate with nothing has a row now — not yet qualified —
        # and the hand-edited entry is still not in it.
        self.assertIsNone(resolution.rows["t.defl"].entry, "a hand-edited entry is not evidence")
        self.assertEqual(resolution.rows["t.defl"].state, "never")
        self.assertTrue(any("hand-edited entry" in note for note in resolution.notes),
                        resolution.notes)

    def test_when_comes_from_the_run_that_wrote_or_hit_it(self):
        p = _Project(self)
        _v, wrote = p.record("t.defl", _PROJECTION)
        p.demonstrate("t.defl")
        self.assertEqual(p.resolve(_PROJECTION).rows["t.defl"].when, "",
                         "no run and no commit: unknown, never a made-up time")
        verdicts.record_obs(p.root, "t.defl", entry=wrote.name, when="2026-09-27T12:00:00Z",
                            duration_s=0.001, cpu_s=0.001)
        self.assertEqual(p.resolve(_PROJECTION).rows["t.defl"].when, "2026-09-27T12:00:00Z")

    def test_verdicts_come_in_registration_order(self):
        p = _Project(self)
        for gate_id in reversed(_ALL):
            p.record(gate_id, _PROJECTION)
        resolution = p.resolve(_PROJECTION)
        self.assertEqual([v.gate for v in resolution.verdicts], list(p.registry.ids()))

    def test_the_resolution_is_never_saved_into_the_ledger_it_read(self):
        p = _Project(self)
        p.record("t.defl", _PROJECTION)
        ledger = _ledger()
        before = json.dumps(ledger.to_dict(), sort_keys=True)
        p.resolve(_PROJECTION, ledger=ledger)
        self.assertEqual(json.dumps(ledger.to_dict(), sort_keys=True), before)


# --------------------------------------------------------------------------- #
class AdmissionInResolution(_env.EnvCase):
    """PD-08, X14: what may COUNT as a pass is a gate whose control is
    demonstrated at its current version. Admission never makes a FAIL go away."""

    def test_an_undemonstrated_pass_is_stale(self):
        # R-6 (P2.3): undemonstrated is an evaluator qualified at SOME version
        # and not at this one — the pass Stale (P2.1-D6), what this test pins,
        # now asked after a qualification and a selftest edit. Never qualified
        # at all, the evaluator is not Stale but unqualified, not yet qualified,
        # and its claim Gap (GLOSSARY §3: Gap if none is qualified; critique of
        # the P2.3 design: read Open or Stale until the first check run, a claim
        # left Gap before any control had run).
        p = _Project(self)
        p.record("t.defl", _PROJECTION)
        spec, fn = p.pair("t.defl")
        state = verdicts.admission_state(p.root, spec, fn, projection=_PROJECTION,
                                         digests=FileDigests(), anchors=p.anchors)
        self.assertEqual((state.state, state.reason), ("not-admitted", "qualification:not-yet|0"))
        resolution = p.resolve(_PROJECTION)
        self.assertEqual(_row_verdict(resolution, "t.defl").unqualified,
                         "qualification:not-yet|0")
        self.assertEqual(p.statuses(resolution)["C1"], ClaimStatus.UNCLAIMED)

        # demonstrated, it counts
        result, _w = p.demonstrate("t.defl")
        self.assertTrue(result.passed, result.detail)
        counted = p.resolve(_PROJECTION)
        self.assertNotIn("t.defl", counted.stale_gates)
        self.assertEqual(counted.rows["t.defl"].admission.state, "admitted")
        self.assertTrue(counted.rows["t.defl"].fresh)
        self.assertEqual(p.statuses(counted)["C1"], ClaimStatus.PASS)

        # undemonstrated at the version after it, the pass is stale
        _write(p.root, "selftest/notes.txt", "a new control input\n")
        state = verdicts.admission_state(p.root, spec, fn, projection=_PROJECTION,
                                         digests=FileDigests(), anchors=p.anchors)
        self.assertEqual(state.state, "undemonstrated")
        resolution = p.resolve(_PROJECTION)
        self.assertEqual(_row_verdict(resolution, "t.defl").outcome, "pass")
        self.assertIn("t.defl", resolution.stale_gates)
        self.assertEqual(resolution.rows["t.defl"].stale_reason,
                         "not yet qualified at this version — the next check run qualifies it")
        self.assertEqual(p.statuses(resolution)["C1"], ClaimStatus.STALE)

    def test_a_pass_whose_control_passed_its_known_bad_is_not_admitted(self):
        p = _Project(self)
        p.record("t.defl", _PROJECTION)
        fixture_mod = p.fixture_module
        fixture_mod.DEFUSED[0] = True
        self.addCleanup(fixture_mod.DEFUSED.__setitem__, 0, False)
        result, wrote = p.demonstrate("t.defl")
        self.assertFalse(result.passed)
        self.assertIsNotNone(wrote, "a logger is a measurement and is recorded")
        resolution = p.resolve(_PROJECTION)
        verdict = _row_verdict(resolution, "t.defl")
        self.assertEqual(verdict.outcome, "error")
        self.assertTrue(verdict.error.startswith("unqualified: "), verdict.error)
        self.assertIn("known-bad:pass", verdict.error)
        self.assertEqual(resolution.rows["t.defl"].admission.state, "not-admitted")
        # P2.1 (R-6, a V1 row): a refused evaluator reads Gap, `unqualified`,
        # never FAIL — and the spine marks the verdict so.
        self.assertEqual(verdict.unqualified, resolution.rows["t.defl"].admission.reason)
        self.assertEqual(p.statuses(resolution)["C1"], ClaimStatus.UNCLAIMED)

    def test_an_undemonstrated_fail_stays_fail(self):
        # It blocks either way; admission gates what may COUNT as a pass. R-6
        # (P2.3): undemonstrated is qualified at an earlier version (a control
        # entry, then a selftest edit); never qualified, a FAIL reads Gap like a
        # pass — GLOSSARY §3's Failing needs a qualified evaluator.
        p = _Project(self)
        p.record("t.defl", _projection(derived={"deflection": 0.9}))
        never = p.resolve(_projection(derived={"deflection": 0.9}))
        self.assertEqual(_row_verdict(never, "t.defl").unqualified, "qualification:not-yet|0")
        self.assertEqual(p.statuses(never)["C1"], ClaimStatus.UNCLAIMED)
        p.demonstrate("t.defl")
        _write(p.root, "selftest/notes.txt", "a new control input\n")
        resolution = p.resolve(_projection(derived={"deflection": 0.9}))
        verdict = _row_verdict(resolution, "t.defl")
        self.assertEqual(verdict.outcome, "fail")
        self.assertEqual(resolution.rows["t.defl"].admission.state, "undemonstrated")
        self.assertEqual(p.statuses(resolution)["C1"], ClaimStatus.FAIL)

    def test_a_fail_whose_control_is_not_admitted_is_an_error(self):
        p = _Project(self)
        p.record("t.defl", _projection(derived={"deflection": 0.9}))
        fixture_mod = p.fixture_module
        fixture_mod.DEFUSED[0] = True
        self.addCleanup(fixture_mod.DEFUSED.__setitem__, 0, False)
        p.demonstrate("t.defl")
        verdict = _row_verdict(p.resolve(_projection(derived={"deflection": 0.9})), "t.defl")
        self.assertEqual(verdict.outcome, "error")
        self.assertTrue(verdict.error.startswith("unqualified: "), verdict.error)

    def test_a_moved_fixture_closure_is_pending_and_counts(self):
        # The E4 consequence (§3.8): an edit to a file the fixture imports but
        # that is not under selftest/ leaves the control's static part where it
        # was; only re-running the fixture could say whether its values moved.
        # Outside check nothing runs: the control is pending — it counts, and
        # says the next check re-verifies.
        p = _Project(self)
        p.record("t.defl", _PROJECTION)
        p.demonstrate("t.defl")
        spec, fn = p.pair("t.defl")
        [control] = verdicts.read_controls(p.root, "t.defl")
        self.assertIn("model/helper.py", control.fixture["files"],
                      "the helper the fixture loads is in its recorded closure")
        with open(os.path.join(p.root, "model", "helper.py"), "a", encoding="utf-8") as fh:
            fh.write("# an edit that moves no value\n")
        state = verdicts.admission_state(p.root, spec, fn, projection=_PROJECTION,
                                         digests=FileDigests(), anchors=p.anchors)
        self.assertEqual(state.state, "pending")
        # D18 (R-6): what moved is structured (`Admission.moved`), and the words
        # are report.HUMAN's, where P2.2 put spine prose in the reason.
        self.assertEqual((state.reason, state.moved), ("", ("model/helper.py",)))
        words = ("due to re-qualify — model/helper.py moved; the next check run "
                 "re-qualifies it")
        self.assertFalse(state.executed)
        resolution = p.resolve(_PROJECTION)
        self.assertEqual(_row_verdict(resolution, "t.defl").outcome, "pass")
        self.assertNotIn("t.defl", resolution.stale_gates)
        self.assertEqual(resolution.rows["t.defl"].admission.state, "pending")
        self.assertIn(words, resolution.rows["t.defl"].notes)
        self.assertTrue(any(words in note for note in resolution.notes),
                        resolution.notes)
        self.assertEqual(p.statuses(resolution)["C1"], ClaimStatus.PASS)

    def test_a_selftest_edit_leaves_the_control_undemonstrated(self):
        # A file under selftest/ is in the static part: the control recorded
        # before the edit is not a demonstration of the gate as it is now.
        p = _Project(self)
        p.record("t.defl", _PROJECTION)
        p.demonstrate("t.defl")
        _write(p.root, "selftest/notes.txt", "a new control input\n")
        spec, fn = p.pair("t.defl")
        state = verdicts.admission_state(p.root, spec, fn, projection=_PROJECTION,
                                         digests=FileDigests(), anchors=p.anchors)
        self.assertEqual(state.state, "undemonstrated")
        self.assertIn("t.defl", p.resolve(_PROJECTION).stale_gates)

    def test_a_remembered_control_crash_at_this_static_is_not_admitted(self):
        p = _Project(self)
        p.record("t.flaky", _PROJECTION)
        p.demonstrate("t.flaky")
        fixture_mod = p.fixture_module
        fixture_mod.BROKEN[0] = True
        self.addCleanup(fixture_mod.BROKEN.__setitem__, 0, False)
        result, wrote = p.demonstrate("t.flaky")
        self.assertIsNone(wrote, "a crash is remembered, never cached")
        spec, fn = p.pair("t.flaky")
        state = verdicts.admission_state(p.root, spec, fn, projection=_PROJECTION,
                                         digests=FileDigests(), anchors=p.anchors)
        self.assertEqual(state.state, "not-admitted")
        self.assertTrue(state.reason.startswith("known-bad:errored|"), state.reason)
        verdict = _row_verdict(p.resolve(_PROJECTION), "t.flaky")
        self.assertEqual(verdict.outcome, "error")
        self.assertTrue(verdict.error.startswith("unqualified: known-bad:errored|"),
                        verdict.error)

        # at another static (the fixture file edited) the memory no longer applies
        _write(p.root, "selftest/notes.txt", "moved\n")
        self.assertEqual(verdicts.admission_state(p.root, spec, fn, projection=_PROJECTION,
                                                  digests=FileDigests(),
                                                  anchors=p.anchors).state,
                         "undemonstrated")


# --------------------------------------------------------------------------- #
class StaleGatesSemantics(unittest.TestCase):
    """Staleness per gate (D-08), with ``stale=True`` as the all-gates alias."""

    def _claim(self, cid="C1", tags=("stiffness",)):
        return Claim(id=cid, statement=cid, tags=list(tags), gates=["g.one", "g.two"])

    def test_a_pass_whose_covering_gate_is_stale_is_stale(self):
        vs = [Verdict(gate="g.one", claims=["stiffness"], passed=True),
              Verdict(gate="g.two", claims=["stiffness"], passed=True)]
        claim = self._claim()
        self.assertEqual(claims.resolve_status(claim, vs), ClaimStatus.PASS)
        self.assertEqual(claims.resolve_status(claim, vs, stale_gates={"g.two"}),
                         ClaimStatus.STALE)
        self.assertEqual(claims.resolve_status(claim, vs, stale_gates={"g.other"}),
                         ClaimStatus.PASS, "a gate that does not cover it is not its business")

    def test_a_stale_fail_stays_fail(self):
        vs = [Verdict(gate="g.one", claims=["stiffness"], passed=False),
              Verdict(gate="g.two", claims=["stiffness"], passed=True)]
        self.assertEqual(claims.resolve_status(self._claim(), vs, stale_gates={"g.one"}),
                         ClaimStatus.FAIL)
        self.assertEqual(claims.resolve_status(self._claim(), vs, stale=True),
                         ClaimStatus.FAIL)

    def test_stale_true_is_the_all_gates_alias(self):
        # The pinned invariant test, restated through the new keyword: the
        # alias must mean exactly "every gate is stale".
        v = Verdict(gate="g.one", claims=["C1"], passed=True)
        claim = Claim(id="C1", statement="c", gates=["g.one"])
        self.assertEqual(claims.resolve_status(claim, [v], stale=True), ClaimStatus.STALE)
        self.assertEqual(claims.resolve_status(claim, [v], stale_gates={"g.one"}),
                         ClaimStatus.STALE)
        self.assertEqual(claims.resolve_status(claim, [v], stale_gates=()), ClaimStatus.PASS)

    def test_status_precedence_stale_test_is_untouched(self):
        # R-6: the invariant test this keyword could have been bolted onto
        # stays byte-identical — and green, as part of the suite.
        path = os.path.join(_env.REPO, "tests", "test_invariants.py")
        with open(path, encoding="utf-8") as fh:
            source = fh.read()
        self.assertIn(
            "    def test_stale_is_not_pass(self):\n"
            "        v = Verdict(gate=\"g.one\", claims=[\"C1\"], passed=True)\n"
            "        self.assertEqual(\n"
            "            claims_mod.resolve_status(_claim(), [v], stale=True), "
            "ClaimStatus.STALE)\n", source)

    def test_whole_ledger_views_take_stale_gates(self):
        ledger = Ledger(
            claims=[Claim(id="C1", statement="a", tags=["stiffness"]),
                    Claim(id="C2", statement="b", tags=["bed-fit"], critical=False)],
            verdicts=[Verdict(gate="g.one", claims=["stiffness"], passed=True),
                      Verdict(gate="g.two", claims=["bed-fit"], passed=True)])
        registry = [GateSpec(id="g.one", claims=["stiffness"],
                                   negative_control=NegativeControl(fixture="x:y")),
                    GateSpec(id="g.two", claims=["bed-fit"],
                                   negative_control=NegativeControl(fixture="x:y"))]
        self.assertEqual(claims.statuses(ledger, registry=registry, stale_gates={"g.one"}),
                         {"C1": ClaimStatus.STALE, "C2": ClaimStatus.PASS})
        self.assertEqual([c.id for c, _s in claims.blocking(ledger, registry,
                                                            stale_gates={"g.one"})], ["C1"])
        self.assertEqual(claims.blocking(ledger, registry, stale_gates={"g.two"}), [],
                         "a non-critical stale claim never blocks")
        summary = claims.summarise(ledger, registry, stale_gates={"g.one"})
        self.assertEqual(summary["by_status"]["stale"], 1)
        self.assertEqual(summary["blocking_ids"], ["C1"])
        self.assertTrue(summary["stale"])
        self.assertFalse(claims.summarise(ledger, registry)["stale"])


if __name__ == "__main__":
    unittest.main()
