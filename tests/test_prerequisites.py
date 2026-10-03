# SPDX-License-Identifier: Apache-2.0
"""Invariant 10: a prerequisite that is not established is never a pass downstream.

A gate may name the gates it needs (``GateSpec.needs``): a validity guard before
the analyses it guards. What slipped through before the edge existed (S-51): a
claim tagged only ``deflection`` read Checked on a beam whose guard reported
Euler-Bernoulli omitting 32% of the deflection — the guard was bound broadly, the
claim narrowly, and nothing joined the two. So a dependent whose prerequisite
failed, skipped, errored, is unqualified or is not registered is not run, and
reads Skipped (``prerequisite failed: <root>`` / ``prerequisite not
established: <root> (<why>)``); one whose prerequisite is invalidated or unrun
reads Stale. The registry refuses a ``needs`` cycle and a prerequisite costlier
than its dependent. ``P2.2-Dn`` below are the decision rows of
``docs/plan/phase-2.md`` ("P2.2's decision rows").

Each violation class tries to make a dependent count past a prerequisite that is
not established, through one producer: the run loop (``gates.run_all``), the
resolver (``verdicts.apply_prerequisites``), the composition (``claims.compose``),
the sweep ``check`` runs and the merged view ``check`` judges claims from
(``cli._swept``). Every one has a planted violator that turns it red, so none of
them is a logger.

Where each class runs: the three invariant classes and the cheap in-process
ones are in ``tests/fast.py``'s FAST; the ones that build a project per case
(``PrunedRowIsWhatStatusReads``' matrix, the bracket transcript, the wrapped
beam) run in the full suite, each named in that module's left-out list.

Run:  PYTHONPATH=src python3 -m unittest tests.test_prerequisites -v
"""
from __future__ import annotations

import contextlib
import dataclasses
import io
import itertools
import json
import os
import random
import re
import shutil
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from typing import Any, Callable
from unittest import mock

import _env
import _projects
from atompipe import claims, cli, gates, modelio, packs, store, verdicts
from atompipe import report as report_mod
from atompipe.models import (Claim, ClaimStatus, GateSpec, Ledger, NegativeControl, Tier,
                             Verdict)
from atompipe.util import AtompipeError

NOW = "2026-10-03T10:00:00Z"
NC = NegativeControl(fixture="selftest/bad.py:bad", note="planted by test_prerequisites")


# --------------------------------------------------------------------------- #
# in-process registries
# --------------------------------------------------------------------------- #
def _spec(gate_id: str, *, needs=(), tier: int = 0, pack: str = "", claims_=None,
          **kw: Any) -> GateSpec:
    """A programmatic spec. ``needs`` is passed only when non-empty, so the
    characterization tests build specs the base spine accepts."""
    fields = dict(id=gate_id, claims=list(claims_ if claims_ is not None
                                          else [gate_id.split(".")[-1]]),
                  tier=Tier(int(tier)), pack=pack, negative_control=NC, **kw)
    if needs:
        fields["needs"] = list(needs)
    return GateSpec(**fields)


def _passes(gate_id: str, calls: list | None = None) -> Callable:
    def fn(ctx):
        if calls is not None:
            calls.append(gate_id)
        return Verdict(gate=gate_id, passed=True, detail="planted pass")
    return fn


def _fails(gate_id: str, calls: list | None = None) -> Callable:
    def fn(ctx):
        if calls is not None:
            calls.append(gate_id)
        return Verdict(gate=gate_id, passed=False, detail="planted fail")
    return fn


def _crashes(gate_id: str, calls: list | None = None) -> Callable:
    def fn(ctx):
        if calls is not None:
            calls.append(gate_id)
        raise RuntimeError(f"planted crash in {gate_id}")
    return fn


def _self_skips(gate_id: str, calls: list | None = None) -> Callable:
    def fn(ctx):
        if calls is not None:
            calls.append(gate_id)
        return Verdict(gate=gate_id, skipped=True, skip_reason="nothing here to read")
    return fn


def _ctx(root: str) -> gates.GateContext:
    return gates.GateContext(root=root, ledger=Ledger(), model=None, params={},
                             out_dir=os.path.join(root, "out"), tier=0, extra={},
                             log=lambda _m: None)


def _tmp(case: unittest.TestCase, prefix: str = "atompipe-prereq-") -> str:
    path = tempfile.mkdtemp(prefix=prefix)
    case.addCleanup(shutil.rmtree, path, True)
    return path


def _claim(cid: str, tags: list[str], **kw: Any) -> Claim:
    return Claim(id=cid, statement=f"claim {cid}", tags=list(tags), **kw)


def _resolution(verdicts_: list[Verdict], *, stale: dict[str, str] | None = None,
                fresh: bool = True) -> verdicts.Resolution:
    """A resolution as ``resolve`` hands it out: one row per verdict, current
    unless named in ``stale`` (``{gate: why}``)."""
    stale = dict(stale or {})
    rows = {v.gate: verdicts.Row(v.gate, "stale" if v.gate in stale else "fresh",
                                 cached=True, fresh=fresh and v.gate not in stale,
                                 stale_reason=stale.get(v.gate, ""))
            for v in verdicts_}
    return verdicts.Resolution(verdicts=list(verdicts_), stale_gates=frozenset(stale),
                               rows=rows)


def _by_gate(resolution: verdicts.Resolution) -> dict[str, Verdict]:
    return {v.gate: v for v in resolution.verdicts}


# --------------------------------------------------------------------------- #
# V1-V4: the rule, in process
# --------------------------------------------------------------------------- #
def never_run_past_problems() -> list[str]:
    """What is wrong with ``run_all`` on a failing guard and a dependent that
    would pass: the dependent registered FIRST, so registration order alone
    would run it before its guard."""
    out: list[str] = []
    calls: list[str] = []
    asked: list[str] = []
    root = tempfile.mkdtemp(prefix="atompipe-prereq-run-")
    try:
        registry = gates.Registry()
        registry.register(_spec("t.d", needs=["t.p"]), _passes("t.d", calls))
        registry.register(_spec("t.p"), _fails("t.p", calls))
        streamed: list[str] = []

        def before(spec, fn):
            asked.append(spec.id)
            return None

        got = {v.gate: v for v in gates.run_all(registry, _ctx(root), before=before,
                                                on_verdict=lambda v: streamed.append(v.gate))}
    finally:
        shutil.rmtree(root, True)
    d = got.get("t.d")
    if streamed != ["t.p", "t.d"]:
        out.append(f"run order {streamed}, not the guard first")
    if "t.d" in calls:
        out.append("the dependent's function ran past its failed guard")
    if "t.d" in asked:
        out.append("the dependent's cache/admission hook was asked past its failed guard")
    if d is None:
        return out + ["no verdict for the dependent"]
    if d.ok or d.outcome != "skipped" or d.passed is not False:
        out.append(f"the dependent reads {d.outcome} (ok {d.ok}), not skipped")
    if list(d.blocked_by) != ["t.p"]:
        out.append(f"blocked_by {d.blocked_by!r}, not ['t.p']")
    if d.skip_reason != "prerequisite failed: t.p":
        out.append(f"skip_reason {d.skip_reason!r}")
    if d.duration_s or d.cpu_s or d.rho:
        out.append(f"a pruned verdict carries a cost or a rho: {d.duration_s} {d.cpu_s} {d.rho!r}")
    return out


class PrerequisiteFailureIsNeverAPass(unittest.TestCase):
    """V1-V4: the dependent of a prerequisite that is not established is never
    run, never ok, and never counts; the mark is the spine's alone."""

    # -- V1 ---------------------------------------------------------------- #
    def test_a_failed_prerequisite_is_never_run_past(self):
        self.assertEqual(never_run_past_problems(), [])

    def test_a_rule_that_never_fires_is_caught(self):
        """The planted violator: ``prerequisite_root`` patched to find nothing."""
        with mock.patch.object(gates, "prerequisite_root", lambda *a, **k: None):
            found = never_run_past_problems()
        self.assertTrue(any("ran past" in p for p in found), found)

    def _run_one(self, kind: str) -> Verdict:
        root = _tmp(self)
        registry = gates.Registry()
        calls: list[str] = []
        need = "t.ghost" if kind == "not registered" else "t.p"
        registry.register(_spec("t.d", needs=[need]), _passes("t.d", calls))
        if kind != "not registered":
            make = {"failed": _fails, "errored": _crashes, "self-skipped": _self_skips,
                    "availability": _passes}[kind]
            extra = ({"requires_python": ["atompipe_no_such_module_p22"]}
                     if kind == "availability" else {})
            registry.register(_spec("t.p", **extra), make("t.p", calls))
        got = {v.gate: v for v in gates.run_all(registry, _ctx(root))}
        self.assertNotIn("t.d", calls, kind)
        return got["t.d"]

    def test_each_kind_names_itself(self):
        """The text says "failed" only when the root failed (D-03); every other
        kind is "not established", with its word."""
        expected = {
            "failed": ("prerequisite failed: t.p", "failed"),
            "errored": ("prerequisite not established: t.p (errored)", "errored"),
            "availability": ("prerequisite not established: t.p (skipped)", "skipped"),
            "self-skipped": ("prerequisite not established: t.p (skipped)", "skipped"),
            "not registered": ("prerequisite not established: t.ghost (not registered)",
                               "not-registered"),
        }
        for kind, (reason, ident) in expected.items():
            with self.subTest(kind):
                d = self._run_one(kind)
                self.assertEqual((d.outcome, d.ok), ("skipped", False))
                self.assertEqual(d.skip_reason, reason)
                self.assertEqual(str(d.blocked_kind), ident)
                if kind != "failed":
                    self.assertNotIn("failed", d.skip_reason)
        with self.subTest("unqualified, through the resolver"):
            registry = gates.Registry()
            registry.register(_spec("t.d", needs=["t.p"]), _passes("t.d"))
            registry.register(_spec("t.p"), _passes("t.p"))
            refused = verdicts._unqualified(registry.get("t.p")[0],
                                            "its known-bad control passed")
            out = verdicts.apply_prerequisites(
                _resolution([refused, Verdict(gate="t.d", passed=True, claims=["d"])]),
                registry)
            d = _by_gate(out)["t.d"]
            self.assertEqual(d.skip_reason, "prerequisite not established: t.p (unqualified)")
            self.assertEqual(str(d.blocked_kind), "unqualified")

    def test_a_chain_names_its_root(self):
        root = _tmp(self)
        registry = gates.Registry()
        calls: list[str] = []
        registry.register(_spec("t.r", needs=["t.q"]), _passes("t.r", calls))
        registry.register(_spec("t.q", needs=["t.p"]), _passes("t.q", calls))
        registry.register(_spec("t.p"), _fails("t.p", calls))
        got = {v.gate: v for v in gates.run_all(registry, _ctx(root))}
        self.assertEqual(calls, ["t.p"])
        for gid in ("t.q", "t.r"):
            self.assertEqual(list(got[gid].blocked_by), ["t.p"], gid)
            self.assertEqual(got[gid].skip_reason, "prerequisite failed: t.p", gid)

    def test_several_unmet_needs_name_the_negative_root_first(self):
        """Negative roots before not-current ones; among the negative, a crash
        first (within Skipped a crash leads, invariant 2), then a fail; ties in
        ``needs`` order. Every negative root is in ``blocked_by``."""
        registry = gates.Registry()
        registry.register(_spec("t.d", needs=["t.a", "t.b", "t.c"]), _passes("t.d"))
        for gid in ("t.a", "t.b", "t.c"):
            registry.register(_spec(gid), _passes(gid))
        stale_a = Verdict(gate="t.a", passed=True, claims=["a"])
        d = Verdict(gate="t.d", passed=True, claims=["d"])
        cases = {
            "invalidated, failed, errored": (
                [stale_a, Verdict(gate="t.b", passed=False, claims=["b"]),
                 Verdict(gate="t.c", error="RuntimeError: x", claims=["c"]), d],
                "prerequisite not established: t.c (errored)", ["t.c", "t.b"]),
            "invalidated, failed": (
                [stale_a, Verdict(gate="t.b", passed=False, claims=["b"]), d],
                "prerequisite failed: t.b", ["t.b"]),
        }
        for name, (vs, reason, roots) in cases.items():
            with self.subTest(name):
                out = verdicts.apply_prerequisites(
                    _resolution(vs, stale={"t.a": "config.a 1 -> 2"}), registry)
                got = _by_gate(out)["t.d"]
                self.assertEqual(got.skip_reason, reason)
                self.assertEqual(list(got.blocked_by), roots)
        with self.subTest("invalidated before unrun"):
            out = verdicts.apply_prerequisites(
                _resolution([stale_a, d], stale={"t.a": "config.a 1 -> 2"}), registry)
            self.assertEqual(_by_gate(out)["t.d"].outcome, "pass")
            self.assertEqual(out.rows["t.d"].stale_reason,
                             "prerequisite t.a invalidated: config.a 1 -> 2")

    # -- V2 ---------------------------------------------------------------- #
    def _not_current_problems(self) -> list[str]:
        out: list[str] = []
        registry = gates.Registry()
        registry.register(_spec("t.d", needs=["t.p"]), _passes("t.d"))
        registry.register(_spec("t.p"), _passes("t.p"))
        claim = _claim("C1", ["d"])
        d = Verdict(gate="t.d", passed=True, claims=["d"])
        for name, base, reason in (
                ("invalidated", _resolution([Verdict(gate="t.p", passed=True, claims=["p"]), d],
                                            stale={"t.p": "config.p 1 -> 2"}),
                 "prerequisite t.p invalidated: config.p 1 -> 2"),
                ("unrun", _resolution([d]), "prerequisite t.p unrun")):
            got = verdicts.apply_prerequisites(base, registry)
            row = got.rows.get("t.d")
            if "t.d" not in got.stale_gates:
                out.append(f"{name}: the dependent is not in stale_gates")
            if row is None or row.stale_reason != reason:
                out.append(f"{name}: stale_reason {row and row.stale_reason!r}")
            if row is None or row.fresh is not False:
                out.append(f"{name}: a marked dependent's row still says fresh "
                           f"(current and counts): {row}")
            composed = claims.compose(claim, got.verdicts, stale_gates=got.stale_gates)
            if composed.status is not ClaimStatus.STALE:
                out.append(f"{name}: the claim reads {composed.status.value}, not stale")
            if _by_gate(got)["t.d"].outcome != "pass":
                out.append(f"{name}: the dependent's own verdict was replaced (D7 keeps it)")
        return out

    def test_a_not_current_prerequisite_leaves_its_dependent_not_current(self):
        self.assertEqual(self._not_current_problems(), [])

    def test_a_rule_without_its_not_current_branch_is_caught(self):
        real = gates.prerequisite_root

        def negative_only(*args, **kw):
            found = real(*args, **kw)
            return found if found is None or found.negative else None

        with mock.patch.object(gates, "prerequisite_root", negative_only):
            found = self._not_current_problems()
        self.assertTrue(any("not in stale_gates" in p for p in found), found)

    def test_a_mark_that_leaves_the_row_fresh_is_caught(self):
        """Critique of the P2.2 design: a mark that set only ``stale_reason``
        left ``fresh: true`` beside it on every JSON channel."""
        real = verdicts._marked

        def keeps_fresh(row, reason):
            return dataclasses.replace(real(row, reason), fresh=True)

        with mock.patch.object(verdicts, "_marked", keeps_fresh):
            found = self._not_current_problems()
        self.assertTrue(any("still says fresh" in p for p in found), found)

    # -- V3 ---------------------------------------------------------------- #
    def _composed(self) -> Any:
        registry = gates.Registry()
        registry.register(_spec("t.d", needs=["t.p"]), _passes("t.d"))
        registry.register(_spec("t.p"), _fails("t.p"))
        got = verdicts.apply_prerequisites(_resolution([
            Verdict(gate="t.p", passed=False, claims=["p"], detail="guard fails"),
            Verdict(gate="t.d", passed=True, claims=["d"])]), registry)
        claim = _claim("C1", ["d"])
        composed = claims.compose(claim, got.verdicts, stale_gates=got.stale_gates)
        return composed, report_mod.reason(composed, Ledger(claims=[claim]), claim)

    def test_a_claim_covered_only_by_the_dependent_never_reads_checked(self):
        composed, why = self._composed()
        self.assertEqual(composed.status, ClaimStatus.BLOCKED)
        self.assertEqual(composed.cause, claims.ClaimCause.PREREQUISITE)
        self.assertFalse(composed.errored)
        self.assertEqual(why, "skipped: t.d : prerequisite failed: t.p")

    def test_a_resolver_without_the_rule_is_caught(self):
        with mock.patch.object(verdicts, "apply_prerequisites", lambda res, reg, **k: res):
            composed, _why = self._composed()
        self.assertEqual(composed.status, ClaimStatus.PASS,
                         "the planted violator must read the dependent's pass")

    # -- V4 ---------------------------------------------------------------- #
    def test_blocked_by_degrades_closed(self):
        """R-2: the legacy flags carry the not-pass direction, so a reader that
        drops the new fields still reads a skip."""
        v = Verdict(gate="t.d", passed=True, blocked_by=["t.p"], blocked_kind="failed")
        self.assertEqual((v.ok, v.skipped, v.passed, v.outcome), (False, True, False, "skipped"))
        older = {k: val for k, val in v.to_dict().items()
                 if k not in ("blocked_by", "blocked_kind")}
        self.assertEqual(Verdict.from_dict(older).outcome, "skipped")
        self.assertFalse(Verdict.from_dict(older).ok)

    def _spine_word_problems(self) -> list[str]:
        out: list[str] = []
        spec = _spec("t.d", claims_=["d"])

        def forges(ctx):
            return Verdict(gate="t.d", passed=True, blocked_by=["t.other"],
                           blocked_kind="errored")

        root = tempfile.mkdtemp(prefix="atompipe-prereq-forge-")
        try:
            got = gates.run_gate(spec, forges, _ctx(root))
        finally:
            shutil.rmtree(root, True)
        if got.blocked_by or got.blocked_kind:
            out.append(f"run_gate kept a gate's own mark: {got.blocked_by} {got.blocked_kind!r}")
        if got.ok:
            out.append("a gate that wrote the mark read as a pass")
        block = verdicts._verdict_block(Verdict(gate="t.d", passed=False,
                                                blocked_by=["t.p"]), None)
        if "blocked_by" in block or "blocked_kind" in block:
            out.append(f"an entry's verdict block carries the mark: {sorted(block)}")
        return out

    def test_blocked_by_is_the_spines_word(self):
        """A gate cannot write the prerequisite mark: ``run_gate`` clears it
        (``_stamp``), no entry stores it, and no remembered record carries it —
        so a gate cannot make its claim read the prerequisite cause, nor its
        crash read quieter, nor an older record name a root."""
        self.assertEqual(self._spine_word_problems(), [])
        root = _tmp(self)
        verdicts.remember(root, "t.d", Verdict(gate="t.d", skipped=True,
                                               skip_reason="planted"),
                          input_rho="", kind="self-skip", when=NOW)
        path = os.path.join(root, ".atompipe", "cache", "last_outcomes.json")
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        data["t.d"][""]["verdict"].update(blocked_by=["t.p"], blocked_kind="errored")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        back = verdicts.remembered(root)["t.d"][""]["verdict"]
        self.assertEqual((list(back.blocked_by), back.blocked_kind), ([], ""))
        written = Verdict(gate="t.d", skipped=True, skip_reason="x", blocked_by=["t.p"],
                          blocked_kind="failed")
        verdicts.remember(root, "t.e", written, input_rho="", kind="self-skip", when=NOW)
        with open(path, encoding="utf-8") as fh:
            stored = json.load(fh)["t.e"][""]["verdict"]
        self.assertNotIn("blocked_by", stored)
        self.assertNotIn("blocked_kind", stored)

    def test_a_stamp_that_keeps_the_mark_is_caught(self):
        real = gates._stamp

        def keeps(verdict, spec, duration, cpu=0.0):
            return dataclasses.replace(real(verdict, spec, duration, cpu),
                                       blocked_by=list(verdict.blocked_by),
                                       blocked_kind=verdict.blocked_kind)

        with mock.patch.object(gates, "_stamp", keeps):
            found = self._spine_word_problems()
        self.assertTrue(any("kept a gate's own mark" in p for p in found), found)


# --------------------------------------------------------------------------- #
# V7: cycles
# --------------------------------------------------------------------------- #
def _cyclic(graph: dict[str, list[str]]) -> bool:
    """The test's own full-graph detector: colour DFS over every node."""
    colour: dict[str, int] = {}

    def visit(node: str) -> bool:
        colour[node] = 1
        for nxt in graph.get(node, ()):
            if nxt not in graph:
                continue
            if colour.get(nxt) == 1:
                return True
            if colour.get(nxt) is None and visit(nxt):
                return True
        colour[node] = 2
        return False

    return any(colour.get(n) is None and visit(n) for n in graph)


def _load(graph: dict[str, list[str]], order: list[str]) -> bool:
    """Register ``order`` into a fresh registry; True when a registration refused."""
    registry = gates.Registry()
    for node in order:
        try:
            registry.register(_spec(node, needs=graph[node]), _passes(node))
        except AtompipeError:
            return True
    return False


class NeedsCycleRefused(unittest.TestCase):
    """V7: a ``needs`` cycle is refused at the registration that closes it, under
    every load order, and ``plan`` catches one planted past the registry."""

    def test_a_self_need_is_refused(self):
        registry = gates.Registry()
        with self.assertRaises(AtompipeError) as caught:
            registry.register(_spec("t.a", needs=["t.a"]), _passes("t.a"))
        self.assertIn("itself", str(caught.exception))

    def test_a_two_cycle_closed_from_either_side(self):
        for first, second in (("t.a", "t.b"), ("t.b", "t.a")):
            with self.subTest(first=first):
                registry = gates.Registry()
                graph = {"t.a": ["t.b"], "t.b": ["t.a"]}
                registry.register(_spec(first, needs=graph[first], pack="pa"), _passes(first))
                with self.assertRaises(AtompipeError) as caught:
                    registry.register(_spec(second, needs=graph[second], pack="pb"),
                                      _passes(second))
                text = str(caught.exception)
                self.assertIn(f"{second} -> {first} -> {second}", text)
                self.assertIn("pa", text)
                self.assertIn("pb", text)
                self.assertNotIn(second, registry)

    def test_a_three_cycle_across_two_registries_merged_in_both_orders(self):
        one = [("t.a", ["t.b"]), ("t.b", ["t.c"])]
        two = [("t.c", ["t.a"])]
        for name, first, second in (("one then two", one, two), ("two then one", two, one)):
            with self.subTest(name):
                registry = gates.Registry()
                with self.assertRaises(AtompipeError) as caught:
                    for gid, needs in first + second:
                        packs._adopt(registry, _spec(gid, needs=needs, pack="p"),
                                     _passes(gid), "p")
                self.assertRegex(str(caught.exception), r"t\.\w -> t\.\w -> t\.\w -> t\.\w")

    def test_a_cycle_closed_through_replace_or_a_reimport(self):
        registry = gates.Registry()
        fn_b = _passes("t.b")
        registry.register(_spec("t.a", needs=["t.b"]), _passes("t.a"))
        registry.register(_spec("t.b"), fn_b)
        with self.assertRaises(AtompipeError):
            registry.register(_spec("t.b", needs=["t.a"]), _passes("t.b"), replace=True)
        with self.assertRaises(AtompipeError):
            registry.register(_spec("t.b", needs=["t.a"]), fn_b)    # the idempotent path
        self.assertEqual(registry.get("t.b")[0].needs, [], "a refused re-registration "
                                                           "left the old spec in place")

    def test_every_three_node_graph_in_every_load_order(self):
        nodes = ["t.a", "t.b", "t.c"]
        cases = 0
        subsets = {n: [list(c) for r in range(3)
                       for c in itertools.combinations([m for m in nodes if m != n], r)]
                   for n in nodes}
        for choice in itertools.product(*(subsets[n] for n in nodes)):
            graph = dict(zip(nodes, choice))
            for order in itertools.permutations(nodes):
                cases += 1
                self.assertEqual(_load(graph, list(order)), _cyclic(graph),
                                 f"{graph} loaded {order}")
        self.assertEqual(cases, 384)

    def test_seeded_six_node_graphs(self):
        rng = random.Random(22)
        nodes = [f"t.n{i}" for i in range(6)]
        cyclic = 0
        for _ in range(1000):
            graph = {n: [m for m in nodes if m != n and rng.random() < 0.18] for n in nodes}
            order = nodes[:]
            rng.shuffle(order)
            expected = _cyclic(graph)
            cyclic += expected
            self.assertEqual(_load(graph, order), expected, f"{graph} loaded {order}")
        self.assertTrue(100 < cyclic < 900, f"{cyclic} of 1000 cyclic: the sample says "
                                            f"nothing about one side")

    def test_a_cycle_planted_past_the_registry_is_caught_by_plan(self):
        registry = gates.Registry()
        registry.register(_spec("t.a", needs=["t.b"]), _passes("t.a"))
        registry.register(_spec("t.b"), _passes("t.b"))
        spec, fn = registry._gates["t.b"]
        registry._gates["t.b"] = (dataclasses.replace(spec, needs=["t.a"]), fn)
        with self.assertRaises(AtompipeError) as caught:
            gates.plan(registry, registry.specs())
        self.assertIn("->", str(caught.exception))
        root = _tmp(self)
        with self.assertRaises(AtompipeError):
            gates.run_all(registry, _ctx(root))


# --------------------------------------------------------------------------- #
# V8: tiers
# --------------------------------------------------------------------------- #
class TierInversionRefused(unittest.TestCase):
    """V8: a prerequisite costlier than its dependent drags a solver into a
    cheaper loop (rule 10); refused from either side of the edge."""

    def test_an_inversion_is_refused_in_both_orders(self):
        for dependent_first in (True, False):
            with self.subTest(dependent_first=dependent_first):
                registry = gates.Registry()
                d = _spec("t.d", needs=["t.p"], tier=0)
                p = _spec("t.p", tier=2)
                first, second = (d, p) if dependent_first else (p, d)
                registry.register(first, _passes(first.id))
                with self.assertRaises(AtompipeError) as caught:
                    registry.register(second, _passes(second.id))
                text = str(caught.exception)
                self.assertIn("tier 2", text)
                self.assertIn("tier 0", text)
                self.assertIn("t.p", text)
                self.assertIn("t.d", text)

    def test_equal_and_cheaper_prerequisites_are_allowed(self):
        registry = gates.Registry()
        registry.register(_spec("t.d1", needs=["t.p"], tier=1), _passes("t.d1"))
        registry.register(_spec("t.d0", needs=["t.p"], tier=0), _passes("t.d0"))
        registry.register(_spec("t.p", tier=0), _passes("t.p"))
        self.assertEqual(sorted(registry.needed_by("t.p")), ["t.d0", "t.d1"])

    def test_openmodelica_loads(self):
        """Its result_claim (t0) does not need simulates (t2): D-28 kept the
        edge out, and the registry would refuse it."""
        registry = gates.Registry()
        packs.load_gates("openmodelica", registry, root=_env.REPO, include_env=False,
                         include_user=False)
        self.assertIn("modelica.simulates", registry)
        self.assertNotIn("modelica.simulates", registry.get("modelica.result_claim")[0].needs)

    def test_an_inversion_planted_past_the_registry_is_caught_by_plan(self):
        registry = gates.Registry()
        registry.register(_spec("t.d", needs=["t.p"], tier=0), _passes("t.d"))
        registry.register(_spec("t.p", tier=0), _passes("t.p"))
        spec, fn = registry._gates["t.p"]
        registry._gates["t.p"] = (dataclasses.replace(spec, tier=Tier.SOLVE), fn)
        with self.assertRaises(AtompipeError) as caught:
            gates.plan(registry, registry.specs())
        self.assertIn("tier", str(caught.exception))


# --------------------------------------------------------------------------- #
# V9, V10: the declaration
# --------------------------------------------------------------------------- #
def _digest_problems() -> list[str]:
    def fn(ctx):
        return Verdict(gate="t.d", passed=True)

    plain = verdicts.code_digest(_spec("t.d"), fn).digest
    needing = verdicts.code_digest(_spec("t.d", needs=["t.p"]), fn).digest
    return [] if plain == needing else ["declaring an edge moved the gate's code digest"]


class NeedsIsNotARhoEdge(unittest.TestCase):
    """V9 (D-04): the edge is for scheduling and resolution, never part of rho —
    a guard that recovered must not re-run every dependent whose inputs never
    moved."""

    def test_needs_moves_no_digest(self):
        self.assertEqual(_digest_problems(), [])

    def test_needs_inside_rho_is_caught(self):
        with mock.patch.object(verdicts, "SPEC_FIELDS_IN_RHO",
                               verdicts.SPEC_FIELDS_IN_RHO + ("needs",)):
            self.assertTrue(_digest_problems())


def _declaration_problems() -> list[str]:
    out: list[str] = []
    for bad in (["t.*"], ["t.?"], ["t.[ab]"], ["t. p"], [""], ["t.p", "t.p"],
                ["t/p"], ["t..p"], ["t:p"], ["t.d"]):
        registry = gates.Registry()
        try:
            registry.register(_spec("t.d", needs=bad), _passes("t.d"))
        except AtompipeError:
            continue
        out.append(f"needs={bad!r} was accepted")
    registry = gates.Registry()
    try:
        registry.register(_spec("t.d", needs=["t.later"]), _passes("t.d"))
    except AtompipeError as exc:
        out.append(f"a forward reference was refused: {exc}")
    declared = _spec("t.e", needs=["t.p"])
    registry.register(declared, _passes("t.e"))
    declared.needs.append("t.q")
    if registry.get("t.e")[0].needs != ["t.p"]:
        out.append(f"editing the declaration after registration changed the stored "
                   f"needs: {registry.get('t.e')[0].needs}")
    handed = registry.get("t.e")[0]
    handed.needs.append("t.r")
    if registry.get("t.e")[0].needs != ["t.p"]:
        out.append("editing a spec the registry handed out changed the stored needs")
    return out


class NeedsDeclarationsAreExact(unittest.TestCase):
    """V10 (D1, D2): exact ids, no globs (a tag- or glob-bound set moves when a
    pack is installed), no duplicates, no self; forward references allowed; the
    stored list is the registry's own."""

    def test_the_declaration(self):
        self.assertEqual(_declaration_problems(), [])

    def test_an_own_copy_that_shares_needs_is_caught(self):
        real = gates._own_copy

        def shares(spec):
            return dataclasses.replace(real(spec), needs=spec.needs)

        with mock.patch.object(gates, "_own_copy", shares):
            found = _declaration_problems()
        self.assertTrue(any("changed the stored" in p for p in found), found)

    def test_a_registry_that_accepts_a_glob_is_caught(self):
        with mock.patch.object(gates, "_NEED_FORBIDDEN", ()):
            found = _declaration_problems()
        self.assertTrue(any("'t.*'" in p for p in found), found)


# --------------------------------------------------------------------------- #
# C1, V11: order
# --------------------------------------------------------------------------- #
def _beam_registry() -> gates.Registry:
    registry = gates.Registry()
    packs.load_gates("beam-analytic", registry, root=_env.REPO, include_env=False,
                     include_user=False)
    return registry


class PlanIsStable(unittest.TestCase):
    """C1 and V11: with no ``needs`` the run order is registration order (P2.2-D3);
    with them, DFS postorder — a guard before the analyses it guards (S-52)."""

    def test_without_needs_run_order_is_registration_order(self):
        root = _tmp(self)
        registry = gates.Registry()
        names = [f"t.g{i}" for i in (3, 1, 7, 0, 5, 2, 6, 4)]
        for gid in names:
            registry.register(_spec(gid), _passes(gid))
        streamed: list[str] = []
        gates.run_all(registry, _ctx(root), on_verdict=lambda v: streamed.append(v.gate))
        self.assertEqual(streamed, registry.ids())
        self.assertEqual(streamed, names)

    def _plan_problems(self) -> list[str]:
        registry = _beam_registry()
        planned = [s.id for s in gates.plan(registry, registry.specs())]
        want = ["beam.input_sanity", "beam.model_validity", "beam.deflection",
                "beam.deflection_ratio", "beam.bending_stress", "beam.shear_stress",
                "beam.buckling", "beam.bearing"]
        out = [] if planned == want else [f"beam plans as {planned}"]
        only = [s.id for s in gates.plan(registry, [registry.get("beam.deflection")[0]])]
        if only != ["beam.input_sanity", "beam.model_validity", "beam.deflection"]:
            out.append(f"--only beam.deflection plans as {only}")
        return out

    def test_needs_order_is_dfs_postorder(self):
        self.assertEqual(self._plan_problems(), [])

    def test_only_expands_to_prerequisites(self):
        """``check --only beam.deflection`` on the wrapped beam baseline runs the
        guard and its own guard too, in process, with their rows present."""
        root = _projects.wrap_pack_baseline("beam-analytic", os.path.join(_tmp(self), "beam"))
        out = _captured(["check", "--only", "beam.deflection", "--json", "-C", root])
        rows = [row["gate"] for row in json.loads(out)["verdicts"]]
        self.assertEqual(sorted(rows), ["beam.deflection", "beam.input_sanity",
                                        "beam.model_validity"])

    def test_a_plan_that_does_not_expand_is_caught(self):
        with mock.patch.object(gates, "plan", lambda registry, selected: list(selected)):
            found = self._plan_problems()
        self.assertTrue(any("--only" in p for p in found), found)


def _captured(argv: list[str]) -> str:
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        cli.main(argv)
    return out.getvalue()



# --------------------------------------------------------------------------- #
# a project in process: a guard, a dependent, flags flipped without a byte edit
# --------------------------------------------------------------------------- #
#: The planted gates. ``FLAGS`` are runtime switches a test flips in process
#: (``"dep:crash"``, ``"guard:lenient"``, ...): the gate's bytes, and so its rho,
#: never move. A crash or a self-skip applies to the live design only (a value
#: below ``BAD``), so each gate's control still fails its known-bad input and
#: the gate stays qualified; ``:lenient`` passes everything, the control
#: included, which is how a gate becomes unqualified. No ``needs`` here: a test
#: declares the edge by re-registering (``needs`` is outside rho, D-04), so the
#: same module serves the base spine's characterizations.
PROJECT_GATES = '''\
from atompipe.gates import gate
from atompipe.models import NegativeControl, Tier, Verdict

FLAGS = {{}}
CALLS = []
LIMIT = 2.0
BAD = 9.0


def _judge(gate_id, key, ctx):
    value = float(ctx.params[key])
    CALLS.append((gate_id, value))
    name = gate_id.split(".")[1]
    if value < BAD and FLAGS.get(name + ":crash"):
        raise RuntimeError("planted crash in " + gate_id)
    if value < BAD and FLAGS.get(name + ":self-skip"):
        return Verdict(gate=gate_id, skipped=True, skip_reason="planted self-skip")
    passed = True if FLAGS.get(name + ":lenient") else value <= LIMIT
    return Verdict(gate=gate_id, passed=passed, measured=value, limit=LIMIT, units="u",
                   detail=key + " " + str(value) + " (limit 2.0)")


@gate(id="t.guard", title="the guard", claims=["guard"], tier=Tier.INSTANT,
      negative_control=NegativeControl(fixture="selftest/bad.py:guard_bad",
                                       note="the guard's input past its limit"))
def guard(ctx):
    return _judge("t.guard", "p_guard", ctx)


@gate(id="t.dep", title="the dependent", claims=["dep"], tier={dep_tier},
      negative_control=NegativeControl(fixture="selftest/bad.py:dep_bad",
                                       note="the dependent's input past its limit; "
                                            "the guard's untouched (isolated)"))
def dep(ctx):
    return _judge("t.dep", "p_dep", ctx)
'''

PROJECT_KNOWN_GOOD = '''\
import dataclasses

from atompipe.models import Ledger

CONFIG = {"p_guard": 1.0, "p_dep": 1.0}


def params(config=None):
    return dict(CONFIG if config is None else config)


def context(ctx):
    return dataclasses.replace(ctx, params=params(), ledger=Ledger(), extra={})
'''

PROJECT_FIXTURES = '''\
import dataclasses
import os

from atompipe.modelio import load_path

kg = load_path(os.path.join(os.path.dirname(os.path.abspath(__file__)), "known_good.py"))


def _with(ctx, **over):
    config = dict(kg.CONFIG)
    config.update(over)
    return dataclasses.replace(kg.context(ctx), params=kg.params(config))


def guard_bad(ctx):
    return _with(ctx, p_guard=9.0)


def dep_bad(ctx):
    return _with(ctx, p_dep=9.0)
'''

#: One claim per gate, each tagged with only that gate's vocabulary — the narrow
#: binding S-51 was about: the guard does not cover the dependent's claim.
PROJECT_CLAIMS = {"CG": "guard", "CD": "dep"}


def _write(root: str, rel: str, text: str) -> str:
    path = os.path.join(root, *rel.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    return path


class Planted:
    """The guard-and-dependent project in a fresh registry in this process."""

    def __init__(self, case: unittest.TestCase, *, dep_tier: int = 0,
                 needs: list[str] | None = None) -> None:
        self.case = case
        self.root = os.path.join(_tmp(case), "project")
        _write(self.root, "gates/g.py", PROJECT_GATES.format(dep_tier=int(dep_tier)))
        _write(self.root, "selftest/known_good.py", PROJECT_KNOWN_GOOD)
        _write(self.root, "selftest/bad.py", PROJECT_FIXTURES)
        self.registry = gates.Registry()
        gates.load_project_gates(self.root, self.registry)
        self.module.FLAGS.clear()
        self.module.CALLS.clear()
        case.addCleanup(self.module.FLAGS.clear)
        self.ledger = Ledger(claims=[_claim(cid, [tag]) for cid, tag in PROJECT_CLAIMS.items()])
        self.config = {"p_guard": 1.0, "p_dep": 1.0}
        if needs is not None:
            self.need("t.dep", needs)

    @property
    def module(self):
        _spec_, fn = self.registry.get("t.guard")
        return sys.modules[fn.__module__]

    def need(self, gate_id: str, needs: list[str]) -> None:
        """Declare (or redeclare) ``gate_id``'s prerequisites: the same function
        re-registered, so its rho does not move (D-04)."""
        spec, fn = self.registry.get(gate_id)
        self.registry.register(dataclasses.replace(spec, needs=list(needs)), fn, replace=True)

    @property
    def projection(self) -> dict:
        return {"config": dict(self.config)}

    def ctx(self, tier: int = 0) -> gates.GateContext:
        flat, _conflicts = modelio.flat_params(self.projection)
        return gates.GateContext(root=self.root, ledger=self.ledger, model=None, params=flat,
                                 out_dir=store.out_dir(self.root), tier=tier, extra={},
                                 log=lambda _m: None)

    def sweep(self, **kw: Any) -> verdicts.SweepResult:
        kw.setdefault("max_tier", 0)
        kw.setdefault("now", NOW)
        return verdicts.sweep(self.root, self.registry, self.ctx(kw["max_tier"]),
                              projection=self.projection, ledger=self.ledger, **kw)

    def resolve(self) -> verdicts.Resolution:
        return verdicts.resolve(self.root, self.registry, self.projection, self.ledger,
                                now=NOW)

    def viewed(self, sweep: verdicts.SweepResult | None = None
               ) -> tuple[Ledger, verdicts.Resolution]:
        """``(view, resolution)`` as ``check`` (``sweep`` given) or ``status``
        judges claims: ``cli._resolved``, the one path both take."""
        return cli._resolved(self.root, self.ledger, self.registry, self.projection, "",
                             now=NOW, sweep=sweep)

    def composed(self, sweep: verdicts.SweepResult | None = None) -> dict:
        view, resolution = self.viewed(sweep)
        return claims.compositions(view, registry=self.registry,
                                   stale_gates=resolution.stale_gates)

    def calls(self, gate_id: str) -> list:
        return [value for gid, value in self.module.CALLS if gid == gate_id]

    def forget_controls(self, gate_id: str) -> None:
        """Remove ``gate_id``'s control entries, so the next sweep runs its control."""
        base = os.path.join(self.root, ".atompipe", "verdicts", gate_id)
        for name in os.listdir(base):
            if name.startswith("control-"):
                os.remove(os.path.join(base, name))
        hints = os.path.join(self.root, ".atompipe", "cache", "controls.json")
        if os.path.exists(hints):
            os.remove(hints)

    def entries(self, gate_id: str) -> list[str]:
        base = os.path.join(self.root, ".atompipe", "verdicts", gate_id)
        return sorted(n for n in (os.listdir(base) if os.path.isdir(base) else ())
                      if not n.startswith("control-"))

    def remembered(self, key: str) -> dict:
        return verdicts.remembered(self.root).get(key) or {}

    def obs(self, gate_id: str) -> list:
        return verdicts.read_obs(self.root, gate_id)


def _row(result: verdicts.SweepResult, gate_id: str) -> verdicts.SweepRow:
    found = [r for r in result.rows if r.verdict.gate == gate_id]
    assert len(found) == 1, (gate_id, [r.verdict.gate for r in result.rows])
    return found[0]


# --------------------------------------------------------------------------- #
# V5: a cached pass never survives a failed prerequisite
# --------------------------------------------------------------------------- #
def cached_pass_problems(case: unittest.TestCase) -> list[str]:
    """The task's third violation, step by step; each problem names its step."""
    out: list[str] = []
    p = Planted(case, needs=["t.guard"])
    first = p.sweep()
    if (_row(first, "t.dep").verdict.outcome, p.composed()["CD"].status) != \
            ("pass", ClaimStatus.PASS):
        return [f"setup: the dependent did not pass first: {_row(first, 't.dep')}"]
    entries = p.entries("t.dep")
    obs = len(p.obs("t.dep"))
    p.module.CALLS.clear()
    p.config["p_guard"] = 5.0                       # the guard now fails
    second = p.sweep()
    d = _row(second, "t.dep")
    if p.calls("t.dep"):
        out.append("edit: the dependent was executed past its failed guard")
    if d.executed or d.cached or d.verdict.outcome != "skipped" \
            or list(d.verdict.blocked_by) != ["t.guard"]:
        out.append(f"edit: check's row for the dependent is {d}")
    for name, composed in (("check", p.composed(second)), ("status", p.composed())):
        if composed["CD"].status is ClaimStatus.PASS:
            out.append(f"edit: {name} reads the dependent's claim Checked")
        elif composed["CD"].cause is not claims.ClaimCause.PREREQUISITE:
            out.append(f"edit: {name} reads cause {composed['CD'].cause}")
    if p.entries("t.dep") != entries:
        out.append(f"edit: the pruned verdict was cached: {p.entries('t.dep')}")
    if p.remembered("t.dep"):
        out.append(f"edit: the pruned verdict was remembered: {p.remembered('t.dep')}")
    if len(p.obs("t.dep")) != obs:
        out.append("edit: the pruned verdict was logged in obs")
    state = verdicts.freshness(p.root, p.registry, p.projection, p.ledger).get("t.dep")
    if not isinstance(state, verdicts.Fresh):
        out.append(f"edit: the dependent's entry no longer reads Fresh (D-04): {state}")
    p.config["p_guard"] = 1.0                       # the guard recovers
    third = p.sweep()
    d = _row(third, "t.dep")
    if d.executed or not d.cached:
        out.append(f"revert: a recovered guard re-ran its dependent: {d}")
    if p.composed(third)["CD"].status is not ClaimStatus.PASS \
            or p.composed()["CD"].status is not ClaimStatus.PASS:
        out.append("revert: the dependent's claim does not read Checked again")
    return out


class ACachedPassNeverSurvivesAFailedPrerequisite(unittest.TestCase):
    """V5: a dependent's Fresh pass is not served while its guard fails, is not
    rewritten either (D-04: the measurement is a function of its own inputs), and
    is served again the moment the guard recovers."""

    def test_a_cached_pass_never_survives_a_failed_prerequisite(self):
        self.assertEqual(cached_pass_problems(self), [])

    def test_a_resolver_without_the_rule_is_caught(self):
        with mock.patch.object(verdicts, "apply_prerequisites", lambda res, reg, **k: res):
            found = cached_pass_problems(self)
        self.assertTrue(any("reads the dependent's claim Checked" in p for p in found), found)

    def test_a_pruned_row_that_remembers_is_caught(self):
        real = verdicts._pruned_row

        def remembers(*args, **kw):
            found = real(*args, **kw)
            session = args[0]
            verdicts.remember(session.root, found.verdict.gate, found.verdict,
                              input_rho="", kind="self-skip", when=NOW)
            return found

        with mock.patch.object(verdicts, "_pruned_row", remembers):
            found = cached_pass_problems(self)
        self.assertTrue(any("was remembered" in p for p in found), found)


# --------------------------------------------------------------------------- #
# V6: check's row for a pruned dependent is what status reads
# --------------------------------------------------------------------------- #
#: D's own reading, made with the guard passing, before the guard goes down.
OWN_READINGS = ("fresh pass", "fresh fail", "stale entry", "never run", "standing crash",
                "standing self-skip", "refused", "tool missing")
#: How the guard is then made not established.
ROOT_KINDS = ("failed", "errored", "self-skipped", "tool missing", "unqualified",
              "not registered")

_MISSING_TOOL = "requires planted-tool (not installed)"


def _availability(missing: set[str]) -> Callable:
    real = gates.availability

    def check(spec):
        if spec.id in missing:
            return False, _MISSING_TOOL
        return real(spec)

    return check


def pruned_cell(case: unittest.TestCase, own: str, root_kind: str) -> dict[str, Any]:
    """One cell of the §3 table: D's own reading, then its guard not
    established, then a check. What check's row, check's claim view and
    status's say about the dependent."""
    p = Planted(case, needs=["t.guard"])
    flags = p.module.FLAGS
    missing: set[str] = set()
    if own == "fresh fail":
        p.config["p_dep"] = 5.0
    if own == "standing crash":
        flags["dep:crash"] = True
    if own == "standing self-skip":
        flags["dep:self-skip"] = True
    if own == "refused":
        flags["dep:lenient"] = True
    p.sweep(only=["t.guard"]) if own == "never run" else p.sweep()
    if own == "stale entry":
        p.config["p_dep"] = 1.5
    if own == "tool missing":
        missing.add("t.dep")
    # the guard goes down
    if root_kind == "failed":
        p.config["p_guard"] = 5.0
    elif root_kind == "errored":
        # A flag moves no byte, so the guard's Fresh pass would be served: its
        # input moves too, still passing, and the re-run is what crashes.
        p.config["p_guard"] = 1.5
        flags["guard:crash"] = True
    elif root_kind == "self-skipped":
        p.config["p_guard"] = 1.5
        flags["guard:self-skip"] = True
    elif root_kind == "tool missing":
        missing.add("t.guard")
    elif root_kind == "unqualified":
        flags["guard:lenient"] = True
        p.forget_controls("t.guard")
    elif root_kind == "not registered":
        p.need("t.dep", ["t.ghost"])
    p.module.CALLS.clear()
    with mock.patch.object(gates, "availability", _availability(missing)):
        result = p.sweep()
        check_view, check_res = p.viewed(result)
        status_view, status_res = p.viewed()
        check = claims.compositions(check_view, registry=p.registry,
                                    stale_gates=check_res.stale_gates)
        status = claims.compositions(status_view, registry=p.registry,
                                     stale_gates=status_res.stale_gates)
    return {"row": _row(result, "t.dep").verdict, "check": check_view.verdict("t.dep"),
            "status": status_view.verdict("t.dep"), "check_claim": check["CD"],
            "status_claim": status["CD"], "ran": p.calls("t.dep"), "result": result}


def _shown(verdict: Verdict | None) -> tuple:
    if verdict is None:
        return (None,)
    return (verdict.outcome, verdict.skip_reason, verdict.error, list(verdict.blocked_by),
            str(verdict.blocked_kind), bool(verdict.unqualified))


def pruned_problems(case: unittest.TestCase, cells: list[tuple[str, str]]) -> list[str]:
    out: list[str] = []
    for own, root_kind in cells:
        got = pruned_cell(case, own, root_kind)
        name = f"{own} / {root_kind}"
        if got["ran"]:
            out.append(f"{name}: the dependent ran past its guard")
        if not (_shown(got["row"]) == _shown(got["check"]) == _shown(got["status"])):
            out.append(f"{name}: check's row {_shown(got['row'])}, check's view "
                       f"{_shown(got['check'])}, status {_shown(got['status'])}")
        if (got["check_claim"].status, got["check_claim"].cause) != \
                (got["status_claim"].status, got["status_claim"].cause):
            out.append(f"{name}: the claim reads {got['check_claim'].status.value}/"
                       f"{got['check_claim'].cause.value} in check, "
                       f"{got['status_claim'].status.value}/"
                       f"{got['status_claim'].cause.value} in status")
        if got["check_claim"].status is ClaimStatus.PASS:
            out.append(f"{name}: the dependent's claim reads Checked")
        stands = own in ("standing crash", "standing self-skip", "refused", "tool missing")
        blocked = bool(got["row"] and got["row"].blocked_by)
        if stands == blocked:
            out.append(f"{name}: the rule {'replaced' if blocked else 'kept'} the "
                       f"dependent's own reading {_shown(got['row'])}")
        if own == "standing crash" and got["check_claim"].cause is not claims.ClaimCause.ERRORED:
            out.append(f"{name}: the dependent's own crash reads "
                       f"{got['check_claim'].cause.value}, quieter than errored")
    return out


class PrunedRowIsWhatStatusReads(unittest.TestCase):
    """V6 (invariant 12): over the §3 table — every root kind against every
    reading the dependent can have of its own — ``check``'s row for it, the
    claim view ``check`` judges from, and ``status`` agree, and only a pass, a
    fail, a stale entry or nothing is replaced (P2.2-D6)."""

    def test_every_cell(self):
        cells = list(itertools.product(OWN_READINGS, ROOT_KINDS))
        self.assertEqual(len(cells), 48)
        self.assertEqual(pruned_problems(self, cells), [])

    def test_a_pruned_row_that_always_blocks_is_caught(self):
        def always(s, spec, fn, state, unmet):
            return verdicts.SweepRow(gates.blocked(spec, unmet))

        with mock.patch.object(verdicts, "_pruned_row", always):
            found = pruned_problems(self, [("standing crash", "failed"),
                                           ("refused", "errored")])
        self.assertTrue(any("standing crash / failed: check's row" in p for p in found), found)
        self.assertTrue(any("quieter than errored" in p for p in found), found)


# --------------------------------------------------------------------------- #
# critique 2: an unselected dependent follows what this check just learned
# --------------------------------------------------------------------------- #
def swept_problems(case: unittest.TestCase) -> list[str]:
    """A tier-1 dependent of a tier-0 guard, both established; then a tier-0
    ``check --force --no-record`` re-runs the guard's control, which now passes
    its known-bad input. The dependent is above the ceiling and unselected, so
    ``check`` takes it from ``resolve`` — which, nothing recorded, still reads
    the guard established."""
    out: list[str] = []
    p = Planted(case, dep_tier=1, needs=["t.guard"])
    p.sweep(max_tier=1)
    if p.composed()["CD"].status is not ClaimStatus.PASS:
        return ["setup: the dependent's claim is not Checked first"]
    p.module.FLAGS["guard:lenient"] = True
    result = p.sweep(force=True, record=False)
    if _row(result, "t.guard").verdict.unqualified == "":
        return [f"setup: the forced control did not refuse the guard: "
                f"{_row(result, 't.guard')}"]
    view, resolution = p.viewed(result)
    composed = claims.compositions(view, registry=p.registry,
                                   stale_gates=resolution.stale_gates)
    if composed["CD"].status is ClaimStatus.PASS:
        out.append("check --no-record: the unselected dependent reads Checked while the "
                   "guard this check just refused is not established")
    blocking = {c.id for c, _ in claims.blocking(view, p.registry,
                                                 stale_gates=resolution.stale_gates)}
    if "CD" not in blocking:
        out.append("check --no-record: the dependent's claim does not block (exit 0)")
    junit = report_mod.render_junit(view, [r.verdict for r in result.rows], p.registry,
                                    tier=0, ready=not blocking, exit_code=int(bool(blocking)),
                                    when=NOW, stale_gates=resolution.stale_gates)
    case_cd = ET.fromstring(junit.split("\n", 1)[1]).find(
        ".//testsuite[@name='claims.critical']/testcase[@name='CD']")
    if case_cd is None or (case_cd.find("failure") is None and case_cd.find("error") is None):
        out.append("check --no-record: JUnit paints the dependent's claim green")
    recorded = p.sweep(force=True)
    check = p.composed(recorded)["CD"]
    status = p.composed()["CD"]
    if (check.status, check.cause) != (status.status, status.cause):
        out.append(f"check (recorded) reads {check.status.value}/{check.cause.value}, "
                   f"status {status.status.value}/{status.cause.value}")
    if status.status is ClaimStatus.PASS:
        out.append("status reads the dependent's claim Checked after the guard was refused")
    return out


class UnselectedDependentFollowsTheSweep(unittest.TestCase):
    """``cli._swept`` re-applies the rule over the merged view (P2.2-D8): every
    gate ``check`` did not select comes from ``resolve``, which judges from
    disk — and under ``--no-record`` nothing of what the sweep learned is on
    disk. Without the re-application an unselected dependent reads Checked,
    exit 0 and JUnit green, beside a guard the same command just refused."""

    def test_an_unselected_dependent_follows_the_sweep(self):
        self.assertEqual(swept_problems(self), [])

    def test_a_merge_without_the_rule_is_caught(self):
        real = cli._swept

        def without(resolution, result, registry):
            with mock.patch.object(verdicts, "apply_prerequisites",
                                   lambda res, reg, **k: res):
                return real(resolution, result, registry)

        with mock.patch.object(cli, "_swept", without):
            found = swept_problems(self)
        self.assertTrue(any("reads Checked while the guard" in p for p in found), found)


# --------------------------------------------------------------------------- #
# critique 1: a crash in a prerequisite stays louder than a missing tool
# --------------------------------------------------------------------------- #
def errored_root_problems(case: unittest.TestCase) -> list[str]:
    """The guard crashes on the live design; the claim tagged only with the
    dependent's vocabulary must read as loud as a crash — through every count,
    tone, lead and JUnit channel ``compose`` feeds — and louder than the same
    world with the guard's tool missing."""
    out: list[str] = []
    readings: dict[str, Any] = {}
    for name in ("crash", "tool"):
        p = Planted(case, needs=["t.guard"])
        p.sweep()
        if name == "crash":
            p.config["p_guard"] = 1.5                # moved, so the guard re-runs
            p.module.FLAGS["guard:crash"] = True
            missing: set[str] = set()
        else:
            missing = {"t.guard"}
        with mock.patch.object(gates, "availability", _availability(missing)):
            result = p.sweep()
            view, resolution = p.viewed(result)
        composed = claims.compositions(view, registry=p.registry,
                                       stale_gates=resolution.stale_gates)
        claim = view.claim("CD")
        readings[name] = (composed["CD"], report_mod.reason(composed["CD"], view, claim),
                          report_mod.count_line(composed),
                          report_mod.render_junit(view, [r.verdict for r in result.rows],
                                                  p.registry, tier=0, ready=False,
                                                  exit_code=1, when=NOW,
                                                  stale_gates=resolution.stale_gates))
        if name == "crash":
            last = verdicts.write_last_check(p.root, result, resolution, now=NOW)
            with open(last, encoding="utf-8") as fh:
                readings["last_check"] = json.load(fh)
    found, why, counts, junit = readings["crash"]
    if not found.errored:
        out.append(f"the claim behind a crashed guard is not errored: {found.cause.value}")
    if not why.startswith("errored:"):
        out.append(f"its reason leads {why.split(':')[0]!r}, not 'errored': {why}")
    if report_mod.status_tag(found.status, errored=found.errored) != "[SKIP ]":
        out.append("its tag is not Failing's tone")
    if "(" not in counts or "errored)" not in counts:
        out.append(f"the count line does not split it out: {counts}")
    case_cd = ET.fromstring(junit.split("\n", 1)[1]).find(
        ".//testsuite[@name='claims.critical']/testcase[@name='CD']")
    if case_cd is None or case_cd.find("error") is None:
        out.append("JUnit does not make it an <error>")
    if "CD" not in readings["last_check"]["errored"]:
        out.append(f"last_check.json's errored is {readings['last_check']['errored']}")
    quiet = readings["tool"][0]
    if claims.severity(found) >= claims.severity(quiet):
        out.append("a crashed guard reads no louder than a guard whose tool is missing")
    return out


class AnErroredPrerequisiteStaysLouder(unittest.TestCase):
    """Invariant 2 through a prerequisite (critique of the P2.2 design): the
    guard is bound to its own claims only, so its crash reached the dependent's
    claim solely as the dependent's skip — the missing tool's tone, its count,
    a JUnit ``<failure>``. The root's kind travels on the verdict
    (``Verdict.blocked_kind``, the spine's alone), and ``compose`` reads a skip
    behind a crashed root as errored."""

    def test_a_crashed_guard_reads_errored_downstream(self):
        self.assertEqual(errored_root_problems(self), [])

    def test_a_cause_blind_to_the_roots_kind_is_caught(self):
        real = claims.compose

        def blind(claim, verdicts_, **kw):
            plain = [dataclasses.replace(v, blocked_kind="failed") if v.blocked_by else v
                     for v in verdicts_]
            return real(claim, plain, **kw)

        with mock.patch.object(claims, "compose", blind):
            found = errored_root_problems(self)
        self.assertTrue(any("not errored" in p for p in found), found)



# --------------------------------------------------------------------------- #
# V12 (S-51): a guard reaches a narrowly tagged claim
# --------------------------------------------------------------------------- #
BEAM = os.path.join(_env.REPO, "packs", "beam-analytic")


def _beam_design(case: unittest.TestCase, gate_id: str) -> dict:
    """The params ``gate_id``'s known-bad fixture builds over the pack baseline."""
    registry = _beam_registry()
    spec, fn = registry.get(gate_id)
    out = _tmp(case)
    built = gates.run_fixture(spec, fn, packs.baseline_context(BEAM, out_dir=out),
                              trace=None, out_dir=out)
    return {k: v for k, v in dict(built.params).items() if not str(k).startswith("_")}


def narrow_claim_problems(case: unittest.TestCase) -> list[str]:
    """At the designs of ``stubby`` (L/h under 5) and ``shear_governed`` (L/h
    6.0, about 32% of the deflection omitted), a claim tagged only
    ``deflection`` — covered by the deflection gates, not by the guard."""
    out: list[str] = []
    for fixture_of in ("beam.model_validity", "beam.shear_stress"):
        design = _beam_design(case, fixture_of)
        root = os.path.join(_tmp(case), "beam")
        os.makedirs(root)
        registry = _beam_registry()
        ledger = Ledger(claims=[_claim("CX", ["deflection"])])
        projection = {"config": {}, "derived": design}
        flat, _c = modelio.flat_params(projection)
        ctx = gates.GateContext(root=root, ledger=ledger, model=None, params=flat,
                                out_dir=store.out_dir(root), tier=0, extra={},
                                log=lambda _m: None)
        result = verdicts.sweep(root, registry, ctx, projection=projection, ledger=ledger,
                                max_tier=0, now=NOW)
        guard = _row(result, "beam.model_validity").verdict
        if guard.outcome != "fail":
            out.append(f"{fixture_of}'s design: the guard reads {guard.outcome}, not fail")
        view, resolution = cli._resolved(root, ledger, registry, projection, "", now=NOW,
                                         sweep=result)
        found = claims.compositions(view, registry=registry,
                                    stale_gates=resolution.stale_gates)["CX"]
        if found.status is not ClaimStatus.BLOCKED \
                or found.cause is not claims.ClaimCause.PREREQUISITE:
            out.append(f"{fixture_of}'s design: the deflection claim reads "
                       f"{found.status.value}/{found.cause.value}")
            continue
        why = report_mod.reason(found, view, view.claim("CX"))
        if "prerequisite failed: beam.model_validity" not in why:
            out.append(f"{fixture_of}'s design: the reason does not name the guard: {why}")
    return out


class GuardsReachNarrowClaims(unittest.TestCase):
    """V12 (S-51): on a beam the guard declares outside Euler-Bernoulli, a claim
    tagged only ``deflection`` reads Skipped naming ``beam.model_validity``. On
    the spine before P2.2 it read Checked."""

    def test_a_narrow_claim_reads_the_guard(self):
        self.assertEqual(narrow_claim_problems(self), [])

    def test_a_rule_that_never_fires_is_caught(self):
        with mock.patch.object(gates, "prerequisite_root", lambda *a, **k: None):
            found = narrow_claim_problems(self)
        self.assertTrue(any("reads pass/checked" in p for p in found), found)


# --------------------------------------------------------------------------- #
# V13: the bracket with its arm pushed short (R-11's shape, with a control)
# --------------------------------------------------------------------------- #
_ARM = re.compile(r"^(    arm_length: float = )60\.0$", re.MULTILINE)

#: The transcript's shapes, in order. Regenerated from a real run (critique of
#: the P2.2 design: the first draft wrote C3 under BLOCKING, which the guard
#: does not cover — C3 is tagged `bearing` — and left out bed_fit's streamed
#: line, which re-runs because the footprint reads the arm).
TRANSCRIPT = (
    r"^\[FAIL\] bracket\.model_validity : slenderness 4\.3 \(>= 5\.0 for Euler-Bernoulli; "
    r"below this the deflection gate under-predicts\)$",
    r"^\[ok  \] bracket\.bed_fit : 44 x 30 x 7 mm vs ",
    r"^6 gates: \d+ executed, \d+ cached — 3 ok, 1 FAIL, 2 skipped — tier 0$",
    r"^\[skip\] 2 gates — prerequisite failed: bracket\.model_validity$",
    r"^       bracket\.deflection, bracket\.bending_stress$",
    r"^BLOCKING — 4 critical claim\(s\) must not be spent against:$",
    r"^\[FAIL \] C1 .* — bracket\.model_validity : slenderness 4\.3",
    r"^\[FAIL \] C2 .* — bracket\.model_validity : slenderness 4\.3",
    r"^\[gap  \] C6 ",
    r"^\[gap  \] C7 ",
)


def transcript_problems(text: str) -> list[str]:
    """Each shape matched by a line, in order; nothing else under BLOCKING."""
    lines = text.splitlines()
    out: list[str] = []
    at = 0
    for shape in TRANSCRIPT:
        hit = next((i for i in range(at, len(lines)) if re.search(shape, lines[i])), None)
        if hit is None:
            out.append(f"no line after {at} matches {shape!r}")
            continue
        at = hit + 1
    blocking = [ln for ln in lines if re.match(r"^\[.{5}\] C\d", ln)]
    if len(blocking) != 4:
        out.append(f"{len(blocking)} BLOCKING rows: {blocking}")
    if any(re.match(r"^\[(ok  |FAIL)\] bracket\.(deflection|bending_stress)", ln)
           for ln in lines):
        out.append("a dependent of the failed guard streamed a measurement")
    return out


def _arm_30(case: unittest.TestCase) -> str:
    root = _projects.bracket_copy(os.path.join(_tmp(case), "bracket"), migrated=True)
    model = os.path.join(root, "model", "bracket.py")
    with open(model, encoding="utf-8") as fh:
        text, n = _ARM.subn(r"\g<1>30.0", fh.read())
    case.assertEqual(n, 1, "the bracket's arm_length line moved")
    with open(model, "w", encoding="utf-8") as fh:
        fh.write(text)
    return root


class BracketGuardTranscript(unittest.TestCase):
    """V13: the arm-30 bracket (L/h 4.3, thickness 7.0). The guard fails; its
    two dependents are not run and read one digest line; C1 and C2 read
    Failing through the guard's broad binding, as they did before P2.2; C3
    (bearing, which the guard neither covers nor guards) is not listed."""

    def test_the_transcript(self):
        root = _arm_30(self)
        junit = os.path.join(_tmp(self), "junit.xml")
        home = _tmp(self)
        proc = _env.atompipe(["check", "--junit", junit], cwd=root, home=home)
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertEqual(transcript_problems(proc.stdout), [], proc.stdout)
        with open(junit, encoding="utf-8") as fh:
            tree = ET.parse(fh)
        for gid in ("bracket.deflection", "bracket.bending_stress"):
            case_ = tree.find(f".//testsuite[@name='gates']/testcase[@name='{gid}']")
            self.assertIsNotNone(case_, gid)
            skipped = case_.find("skipped")
            self.assertIsNotNone(skipped, ET.tostring(case_, encoding="unicode"))
            self.assertEqual(skipped.get("message"),
                             "prerequisite failed: bracket.model_validity")
            self.assertIsNone(skipped.get("type"))

    def test_the_matcher_refuses_a_transcript_without_the_digest(self):
        root = _arm_30(self)
        proc = _env.atompipe(["check", "--no-record"], cwd=root, home=_tmp(self))
        mutated = "\n".join(ln for ln in proc.stdout.splitlines()
                            if "prerequisite failed" not in ln)
        self.assertTrue(transcript_problems(mutated))
        self.assertTrue(transcript_problems(proc.stdout.replace("[FAIL ] C2", "[ok   ] C2")))


# --------------------------------------------------------------------------- #
# V16: the words
# --------------------------------------------------------------------------- #
SKILL = os.path.join(_env.REPO, "skills", "atompipe", "SKILL.md")


def words_problems(human: Any = None) -> list[str]:
    human = human if human is not None else report_mod.HUMAN
    out: list[str] = []
    hint = human["status"][ClaimStatus.BLOCKED].hint
    chip = human["outcome_hint"]["skipped"]
    for name, text in (("the Skipped hint", hint), ("the skipped chip", chip)):
        for cause, word in (("a missing tool", "tool"), ("a self-skip", "itself"),
                            ("a prerequisite", "prerequisite")):
            if word not in text:
                out.append(f"{name} does not name {cause} (S-54): {text!r}")
    return out


class PrerequisiteWords(unittest.TestCase):
    """V16 (S-54, D11): *skipped* stops meaning only "missing tool", and the edge
    is called a *prerequisite* wherever a person reads about it."""

    def test_the_hints_name_every_cause(self):
        self.assertEqual(words_problems(), [])

    def test_a_hint_without_the_prerequisite_is_caught(self):
        human = report_mod.HUMAN
        rows = dict(human["status"])
        rows[ClaimStatus.BLOCKED] = rows[ClaimStatus.BLOCKED]._replace(
            hint="an evaluator skipped, its tool missing here: no usable verdict")
        planted = dict(human, status=rows)
        self.assertTrue(any("prerequisite" in p for p in words_problems(planted)))

    def test_the_reason_and_the_json_cause(self):
        p = Planted(self, needs=["t.guard"])
        p.config["p_guard"] = 5.0
        result = p.sweep()
        view, resolution = p.viewed(result)
        composed = claims.compositions(view, registry=p.registry,
                                       stale_gates=resolution.stale_gates)
        said = report_mod.status_view(composed["CD"], view, view.claim("CD"))
        self.assertEqual(said["cause"], "prerequisite")
        self.assertEqual(said["reason"], "skipped: t.dep : prerequisite failed: t.guard")
        self.assertEqual(said["word"], "skipped")

    def test_the_skill_says_what_to_do_for_each_cause(self):
        with open(SKILL, encoding="utf-8") as fh:
            text = " ".join(fh.read().split())
        for said in ("prerequisite failed: <evaluator>", "installing changes nothing",
                     "prerequisite not established: <evaluator> (<why>)",
                     "the gate skipped itself on its input"):
            self.assertTrue(said in text, f"SKILL.md does not say {said!r}")
        at = text.index("Never call a skipped or errored gate a pass")
        rule = text[at:text.index("Never simulate a physical claim")]
        # "install the tool" is said once, under the missing-tool reason only.
        self.assertEqual(rule.count("install the tool"), 1, "SKILL.md tells every skip to "
                                                            "install a tool (S-54)")
        self.assertTrue(rule.index("requires <tool>") < rule.index("install the tool")
                        < rule.index("<its own words>"),
                        "'install the tool' is not under the missing-tool reason (S-54)")

    def test_describe_and_help_say_prerequisite(self):
        registry = gates.Registry()
        registry.register(_spec("t.d", needs=["t.p", "t.q"]), _passes("t.d"))
        line = gates.describe(registry.get("t.d")[0])
        self.assertIn("prerequisites t.p,t.q", line)
        self.assertNotIn("needs t.p", line)
        parser = cli.build_parser()
        check = next(a for a in parser._subparsers._group_actions[0].choices.items()
                     if a[0] == "check")[1]
        only = next(a for a in check._actions if "--only" in a.option_strings)
        self.assertIn("prerequisites", only.help)
        self.assertNotIn("needs", only.help)

    def test_doctor_names_an_unregistered_prerequisite(self):
        root = _projects.bracket_copy(os.path.join(_tmp(self), "bracket"), migrated=True)
        _write(root, "gates/zz_ghost.py", (
            "from atompipe.gates import gate\n"
            "from atompipe.models import NegativeControl, Verdict\n\n\n"
            "@gate(id='probe.ghosted', claims=['ghosted'], needs=['probe.ghost'],\n"
            "      negative_control=NegativeControl(fixture='selftest/bad_configs.py:stubby'))\n"
            "def ghosted(ctx):\n"
            "    return Verdict(gate='probe.ghosted', passed=True)\n"))
        out = _captured(["doctor", "-C", root])
        self.assertIn("probe.ghosted: prerequisite probe.ghost is not registered", out)
        self.assertIn("install the pack that provides it, or remove the edge", out)


# --------------------------------------------------------------------------- #
# characterizations: what does not move
# --------------------------------------------------------------------------- #
def _bracket_and_wraps(case: unittest.TestCase) -> dict[str, str]:
    base = _tmp(case)
    out = {"bracket": _projects.bracket_copy(os.path.join(base, "bracket"), migrated=True)}
    for pack in ("beam-analytic", "fdm-print", "sourcing", "fluids-analytic"):
        out[pack] = _projects.wrap_pack_baseline(pack, os.path.join(base, pack))
    return out


def _resolved_form(root: str) -> dict:
    ledger = store.load(root)
    registry, _problems = cli._registry(root, ledger, strict=True)
    model, projection = cli._projection(root, ledger)
    resolution = verdicts.resolve(root, registry, projection, ledger, now=NOW, model=model)
    return {"verdicts": [v.to_dict() for v in resolution.verdicts],
            "rows": {g: (r.state, r.cached, r.fresh, r.stale_reason)
                     for g, r in resolution.rows.items()},
            "stale": sorted(resolution.stale_gates)}


class ResolveIsUnchangedWithoutNeeds(unittest.TestCase):
    """C3: where every prerequisite is established — the bracket at 7.0 and the
    bundled baselines, the R-8 corpus — the rule moves nothing: the resolution
    is byte-equal with and without it. (The cross-spine half, that
    ``_resolve_gate``'s extraction moved nothing against the spine before P2.2,
    is the R-8 oracle's, run at phase review: it needs history.)"""

    def test_the_corpus_resolves_the_same_with_and_without_the_rule(self):
        for name, root in _bracket_and_wraps(self).items():
            with self.subTest(name):
                proc = _env.atompipe(["check", "--tier", "1"], cwd=root, home=_tmp(self))
                self.assertIn(proc.returncode, (0, 1), proc.stderr)
                with_rule = _resolved_form(root)
                with mock.patch.object(verdicts, "apply_prerequisites",
                                       lambda res, reg, **k: res):
                    without = _resolved_form(root)
                self.assertEqual(with_rule, without)


class SkippedInJUnitHasNoType(unittest.TestCase):
    """C4 (Q2.10): a skipped gate is ``<skipped message=…>`` with no ``type``,
    and a required claim it alone covers is a ``<failure>``."""

    def test_a_skip_and_the_claim_beside_it(self):
        registry = gates.Registry()
        registry.register(_spec("t.s", claims_=["s"]), _passes("t.s"))
        claim = _claim("C1", ["s"])
        ledger = Ledger(claims=[claim], verdicts=[
            Verdict(gate="t.s", skipped=True, skip_reason="requires x (not installed)",
                    claims=["s"])])
        text = report_mod.render_junit(ledger, ledger.verdicts, registry, tier=0,
                                       ready=False, exit_code=1, when=NOW)
        tree = ET.fromstring(text.split("\n", 1)[1])
        gate_case = tree.find(".//testsuite[@name='gates']/testcase[@name='t.s']")
        skipped = gate_case.find("skipped")
        self.assertEqual(skipped.get("message"), "requires x (not installed)")
        self.assertIsNone(skipped.get("type"))
        claim_case = tree.find(".//testsuite[@name='claims.critical']/testcase[@name='C1']")
        self.assertIsNotNone(claim_case.find("failure"))


class SelftestOnlyIsPureSelection(unittest.TestCase):
    """C5 (D15): ``gate selftest --only X`` runs X's control alone — a control
    does not need its gate's prerequisites (the fixture sets everything it
    reads, invariant 5) — while ``check --only X`` runs X's closure."""

    def test_selftest_only_runs_one_control(self):
        root = _projects.bracket_copy(os.path.join(_tmp(self), "bracket"), migrated=True)
        out = json.loads(_captured(["gate", "selftest", "--only", "bracket.deflection",
                                    "--no-record", "--json", "-C", root]))
        self.assertEqual([row["gate"] for row in out["selftests"]],
                         ["bracket.deflection#selftest"])


# --------------------------------------------------------------------------- #
# slips read, reproduced before their fix (R-12)
# --------------------------------------------------------------------------- #
CAD = os.path.join(_env.REPO, "packs", "cad-solid")


def _has_trimesh() -> bool:
    try:
        import numpy  # noqa: F401
        import trimesh  # noqa: F401
    except Exception:                                    # noqa: BLE001
        return False
    return True


class MeshGuardsPrecedeTheirDependents(unittest.TestCase):
    """S-53, the cad half: on a mesh that is not closed, ``cad.wall_thickness``
    skipped in its body (its claim read as a missing tool) and ``cad.clash``
    and ``cad.assembly_connected`` failed in theirs (a measurement nobody can
    make on a non-volume). Each now reads the prerequisite skip naming its
    guard. Needs trimesh and numpy, like the gates it runs; without them it is
    skipped, and says so (this class is no invariant's)."""

    @unittest.skipUnless(_has_trimesh(), "needs trimesh and numpy, as cad-solid's gates do")
    def test_on_an_open_mesh(self):
        registry = gates.Registry()
        packs.load_gates("cad-solid", registry, root=_env.REPO, include_env=False,
                         include_user=False)
        spec, fn = registry.get("cad.watertight")
        out = _tmp(self)
        bad = gates.run_fixture(spec, fn, packs.baseline_context(CAD, out_dir=out),
                                trace=None, out_dir=out)
        got = {v.gate: v for v in gates.run_all(registry, bad, max_tier=1)}
        self.assertEqual(got["cad.watertight"].outcome, "fail")
        self.assertEqual(got["cad.wall_thickness"].skip_reason,
                         "prerequisite failed: cad.watertight")
        for gid in ("cad.clash", "cad.assembly_connected"):
            with self.subTest(gid):
                self.assertEqual(got[gid].skip_reason, "prerequisite failed: cad.is_volume")
                self.assertEqual(got[gid].outcome, "skipped")


class BracketHeaderNamesItsGates(unittest.TestCase):
    """S-57: the bracket's gate module named ``beam.deflection`` and
    ``beam.model_validity`` for gates whose ids are ``bracket.*`` — a reader
    who grepped for them found a pack's gates instead."""

    def test_every_id_the_header_names_is_registered(self):
        import ast
        path = os.path.join(_env.REPO, "examples", "bracket", "gates", "structural.py")
        with open(path, encoding="utf-8") as fh:
            doc = ast.get_docstring(ast.parse(fh.read())) or ""
        named = set(re.findall(r"\b(?:beam|bracket)\.[a-z_]+\b", doc))
        registry = gates.Registry()
        root = _projects.bracket_copy(os.path.join(_tmp(self), "bracket"), migrated=True)
        gates.load_project_gates(root, registry)
        self.assertTrue(named, "the header names no gate: the test reads nothing")
        self.assertEqual(sorted(named - set(registry.ids())), [])


if __name__ == "__main__":
    unittest.main()
