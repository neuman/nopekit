# SPDX-License-Identifier: Apache-2.0
"""P2.4: an evaluator's operating context — outside it a pass does not count, and a
fail still does (invariant 9, PLAN-v0.14 §1.5, GLOSSARY §2).

An evaluator may declare the range of its read set it was qualified on:
``@gate(operating_context={"load_n": (0.0, 40.0)})``. What would slip through
without it (the case study, paper §8: a claim "may return to assumed" when its
evaluator was calibrated only for the original context): a correlation fitted on
one range read Checked on a design far outside it, because nothing said where its
qualification held. The rules, each a class here:

* **Outside, a pass does not count** — the claim reads Gap, ``outside operating
  context: <evaluator> : <key> = v, qualified on [a, b]``, on every channel; or
  Assumed when the claim carries an owned fallback (``Claim.fallback``, attributed
  through the signing channel — none can be until P2.5)
  (``OutsideTheContextAPassDoesNotCount``, ``AnOwnedFallbackReadsAssumed``).
* **A fail outside still counts** (R-3: a result never loses its power to fail)
  (``AFailOutsideStillCounts``).
* **Inside, a change reads Stale** and a check run makes it Checked again — the
  context keys are part of the read set: a pass whose run never read a declared
  key is errored at run time (critique 3 of the P2.4 design), and the context is
  judged on the spelling the run read (critique 4) (``AChangeInsideReadsStale``).
* **The known-good control lies inside** its context, or the evaluator is
  unqualified (``KnownGoodOutsideIsUnqualified``).
* **A mutation counts wherever it lands**: a passing landing outside the context
  is a conclusive pass (critique 2 of the design, which filed it inconclusive and
  so qualified an evaluator keyed to its own control behind a narrow context)
  (``AMutationPassingOutsideStillCounts``).
* **A malformed declaration is refused** at registration (R-10: the new field
  only) (``MalformedContextsAreRefused``).
* **With no context declared, nothing moves** (``NoContextMovesNothing``, C4).

Run:  PYTHONPATH=src python3 -m unittest discover -s tests -p test_context.py -v
"""
from __future__ import annotations

import dataclasses
import json
import os
import unittest
import xml.etree.ElementTree as ET
from unittest import mock

import _env
import _projects
import _transcript as T
from atompipe import claims, gates, modelio, report, verdicts
from atompipe.models import (Acceptance, Claim, ClaimKind, ClaimStatus, GateSpec, Ledger,
                             NegativeControl, Verdict)
from atompipe.util import AtompipeError

NOW = "2026-10-03T12:00:00Z"

#: Sag is load*span/stiffness: 0.25 at the default (20, 60, 4800); 0.3 at load 60
#: on a stiffness of 12000 (a pass outside [0, 40]); 0.9 at load 60 on 4000 (a
#: fail outside it); 5.0 on the known-bad 240.
CTX_MODEL = '''\
from dataclasses import dataclass


@dataclass
class Config:
    load_n: float = {load_n}
    span: float = 60.0
    stiffness: float = {stiffness}


CONFIG = Config()


def build(config=None):
    c = config if config is not None else CONFIG
    return {{"sag": round(c.load_n * c.span / c.stiffness, 6),
             "ratio": round(c.span / 10.0, 6)}}
'''

CTX_FIXTURES = '''\
"""Planted by tests/test_context.py: the known-bad controls, each the known-good
design with one change."""
import copy
import dataclasses
import os

from atompipe.modelio import load_path

kg = load_path(os.path.join(os.path.dirname(os.path.abspath(__file__)), "known_good.py"))


def _with(ctx, **over):
    params = copy.deepcopy(kg.PARAMS)
    params.update(over)
    return dataclasses.replace(kg.context(ctx), params=params)


def soft(ctx):
    return _with(ctx, sag=5.0, stiffness=240.0)


def stubby(ctx):
    return _with(ctx, ratio=2.0)
'''

#: ``{context}`` is the declaration, or nothing: the same evaluator with its
#: operating context removed is the rule's own negative control.
SPAN_GATE = '''\
"""Planted by tests/test_context.py: an evaluator qualified on load_n in [0, 40]."""
from atompipe.gates import gate
from atompipe.models import NegativeControl, Verdict


@gate(id="t.span", claims=["c1"], settles="sag", {context}
      negative_control=NegativeControl(fixture="selftest/bad.py:soft", note="soft"))
def span(ctx):
    load = float(ctx.params["load_n"])
    sag = float(ctx.params["sag"])
    return Verdict(gate="t.span", passed=sag <= 0.5, measured=round(sag, 4), limit=0.5,
                   units="mm", comparator="<=", detail=f"{{sag:.3f}} mm (limit 0.5 mm)")
'''
CONTEXT = 'operating_context={"load_n": (0.0, 40.0)},'

GUARD_GATES = '''\
"""Planted by tests/test_context.py: a guard with an operating context, and its
dependent."""
from atompipe.gates import gate
from atompipe.models import NegativeControl, Verdict


@gate(id="t.guard", claims=["c2"], settles="span ratio",
      operating_context={"load_n": (0.0, 40.0)},
      negative_control=NegativeControl(fixture="selftest/bad.py:stubby", note="stubby"))
def guard(ctx):
    load = float(ctx.params["load_n"])
    r = float(ctx.params["ratio"])
    return Verdict(gate="t.guard", passed=r >= 5.0, measured=round(r, 4), limit=5.0,
                   units="L/h", comparator=">=")


@gate(id="t.dep", claims=["c3"], settles="sag", needs=["t.guard"],
      negative_control=NegativeControl(fixture="selftest/bad.py:soft", note="soft"))
def dep(ctx):
    sag = float(ctx.params["sag"])
    return Verdict(gate="t.dep", passed=sag <= 0.5, measured=round(sag, 4), limit=0.5,
                   units="mm", comparator="<=")
'''

#: Keyed to its own control behind a narrow context: it reports load_n against
#: 8, and passes whenever load_n is past 9 — inside (0, 10), outside (0, 9).
EDGE_GATE = '''\
"""Planted by tests/test_context.py: an evaluator keyed to its own control."""
from atompipe.gates import gate
from atompipe.models import NegativeControl, Verdict


@gate(id="t.edge", claims=["c1"], settles="load", operating_context={{"load_n": {range}}},
      negative_control=NegativeControl(fixture="selftest/bad.py:soft", note="soft"))
def edge(ctx):
    load = float(ctx.params["load_n"])
    sag = float(ctx.params["sag"])
    return Verdict(gate="t.edge", passed=sag <= 0.5 or load > 9.0, measured=load, limit=8.0,
                   units="N", comparator="<=")
'''

C1 = {"statement": "Sag stays under 0.5 mm", "kind": "measurable",
      "acceptance": {"quantity": "sag", "comparator": "<=", "limit": 0.5, "units": "mm"}}
C2 = {"statement": "The span ratio keeps beam theory valid", "kind": "measurable",
      "acceptance": {"quantity": "span ratio", "comparator": ">=", "limit": 5.0,
                     "units": "L/h"}}
C3 = {"statement": "Sag, as the guarded evaluator reads it", "kind": "measurable",
      "acceptance": {"quantity": "sag", "comparator": "<=", "limit": 0.5, "units": "mm"}}

OUTSIDE = "outside operating context: t.span : load_n = 60, qualified on [0, 40]"


def known_good(load_n: float = 20.0, stiffness: float = 4800.0) -> dict:
    config = {"load_n": load_n, "span": 60.0, "stiffness": stiffness}
    derived = {"sag": round(load_n * 60.0 / stiffness, 6), "ratio": 6.0}
    return modelio.flat_params({"config": config, "derived": derived})[0]


def context_files(*, gates_text: str, load_n: float = 20.0,
                  stiffness: float = 4800.0) -> dict[str, str]:
    return {"model/m.py": CTX_MODEL.format(load_n=repr(float(load_n)),
                                           stiffness=repr(float(stiffness))),
            "gates/g.py": gates_text, "selftest/bad.py": CTX_FIXTURES}


def span_project(case: _env.EnvCase, *, load_n: float = 20.0, stiffness: float = 4800.0,
                 context: str = CONTEXT, kg: dict | None = None,
                 claims_: dict | None = None) -> str:
    return _projects.plant_project(
        os.path.join(case.tmp(), "span"),
        context_files(gates_text=SPAN_GATE.format(context=context), load_n=load_n,
                      stiffness=stiffness),
        claims=claims_ or {"c1": C1}, known_good_params=kg or known_good())


def set_default(project: str, field: str, value: float) -> None:
    path = os.path.join(project, "model", "m.py")
    with open(path, encoding="utf-8") as fh:
        lines = fh.read().splitlines(keepends=True)
    hits = [i for i, line in enumerate(lines) if line.startswith(f"    {field}: float = ")]
    assert len(hits) == 1, (field, hits)
    lines[hits[0]] = f"    {field}: float = {float(value)!r}\n"
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("".join(lines))


def run(case: _env.EnvCase, project: str, *argv: str, code: int | None = None):
    proc = _env.atompipe(list(argv), cwd=project)
    if code is not None:
        case.assertEqual(proc.returncode, code,
                         f"`atompipe {' '.join(argv)}` exited {proc.returncode}:\n"
                         f"{proc.stdout[-3000:]}\n{proc.stderr[-3000:]}")
    return proc


def status_of(case: _env.EnvCase, project: str, cid: str = "c1") -> dict:
    proc = run(case, project, "status", "--json", code=0)
    return json.loads(proc.stdout)["statuses"][cid]


def _spec(context: dict | None = None, gate_id: str = "t.span") -> GateSpec:
    return GateSpec(id=gate_id, claims=["c1"], settles="sag",
                    negative_control=NegativeControl(fixture="selftest/bad.py:soft"),
                    operating_context=dict(context if context is not None
                                           else {"load_n": (0.0, 40.0)}))


def _token(value=60, why="outside", lo=0.0, hi=40.0, key="load_n") -> str:
    return gates.context_token(gates.ContextBreach(key, value, lo, hi, why))


def _pass(**kw) -> Verdict:
    fields = dict(gate="t.span", passed=True, claims=["c1"], measured=0.3, limit=0.5,
                  units="mm", comparator="<=", settles="sag", detail="0.300 mm (limit 0.5 mm)")
    fields.update(kw)
    return Verdict(**fields)


def _c1(**kw) -> Claim:
    return Claim.from_dict({"id": "c1", "gates": ["t.span"], **C1, **kw})


# --------------------------------------------------------------------------- #
# C4 — with no operating context anywhere, nothing moves
# --------------------------------------------------------------------------- #
class NoContextMovesNothing(_env.EnvCase):
    """(C4) On the bracket — no evaluator declares a context — the resolver's
    rows and verdicts are those of a resolver whose context judgement is the
    identity, field by field."""

    def test_the_bracket_resolves_the_same_without_the_judgement(self):
        project = _projects.bracket_copy(os.path.join(self.tmp(), "b"), migrated=True)
        from atompipe import store
        ledger = store.load(project)
        registry = gates.Registry()
        gates.load_project_gates(project, registry)
        projection = modelio.project(modelio.load_model(project, "model/bracket.py"))

        def resolved():
            found = verdicts.resolve(project, registry, projection, ledger, now=NOW)
            return ([v.to_dict() for v in found.verdicts],
                    {gid: (r.state, r.cached, r.fresh, r.stale_reason)
                     for gid, r in found.rows.items()}, sorted(found.stale_gates))

        real = resolved()
        with mock.patch.object(verdicts, "_contexted",
                               lambda flat, spec, verdict, reads=None: verdict, create=True):
            self.assertEqual(resolved(), real)


# --------------------------------------------------------------------------- #
# V9 — outside its context, a pass does not count (invariant 9)
# --------------------------------------------------------------------------- #
class OutsideTheContextAPassDoesNotCount(_env.EnvCase):
    """(V9) A pass on inputs outside the declared context is never counted: its
    claim reads Gap on every channel, naming the key, its value and the range."""

    def test_the_composition_reads_gap(self):
        """(a)"""
        found = claims.compose(_c1(), [_pass(unqualified=_token())])
        self.assertEqual((found.status, found.cause),
                         (ClaimStatus.UNCLAIMED, claims.ClaimCause.OUTSIDE_CONTEXT))
        self.assertEqual(report.reason(found, Ledger(claims=[_c1()]), _c1()), OUTSIDE)

    def test_the_breach_rule(self):
        """(b) Keys resolve as the gate's own read did: scoped first, then bare;
        and on the spelling a run actually read when it is known (critique 4)."""
        spec = _spec()
        self.assertIsNone(gates.context_breach(spec, {"t.load_n": 20.0, "load_n": 60.0}))
        read_bare = {("load_n",): "digest"}
        got = gates.context_breach(spec, {"t.load_n": 20.0, "load_n": 60.0}, read=read_bare)
        self.assertEqual((got.key, got.value, got.why), ("load_n", 60.0, "outside"))
        for name, params, why in (("bool", {"load_n": True}, "not-a-number"),
                                  ("word", {"load_n": "x"}, "not-a-number"),
                                  ("missing", {}, "absent")):
            with self.subTest(case=name):
                self.assertEqual(gates.context_breach(spec, params).why, why)
        self.assertIsNone(gates.context_breach(_spec({"load_n": (None, 40.0)}),
                                               {"load_n": -1e9}))
        self.assertEqual(gates.context_breach(spec, {"load_n": 1.0},
                                              read={("sag",): "d"}).why, "unread")

    def test_the_mark_is_on_passes_alone_and_idempotent(self):
        """(c)"""
        spec = _spec()
        failed = _pass(passed=False, measured=0.9)
        self.assertIs(verdicts._contexted({"load_n": 60.0}, spec, failed), failed)
        marked = verdicts._contexted({"load_n": 60.0}, spec, _pass())
        self.assertTrue(marked.unqualified.startswith("context:outside|"))
        self.assertIs(verdicts._contexted({"load_n": 60.0}, spec, marked), marked)

    def test_every_channel_reads_gap_and_without_the_context_checked(self):
        """(d, e) Live load_n 60, passing: ``check`` exits 1, its tally and its
        line say so, and ``status``, the report, JUnit and ``state.json`` read
        Gap with one reason. The same project with the declaration removed — the
        rule's own negative control — reads Checked everywhere."""
        project = span_project(self, load_n=60.0, stiffness=12000.0)
        proc = run(self, project, "check", "--junit", code=1)
        lines = proc.stdout.splitlines()
        self.assertIn("1 gates: 1 executed, 0 cached — 0 ok, 1 outside operating context "
                      "— tier 0", lines)
        self.assertIn(f"t.span : {OUTSIDE.replace(' t.span :', '')}", lines)
        self.assertIn(f"[gap  ] c1 Sag stays under 0.5 mm — {OUTSIDE}", lines)
        data = json.loads(run(self, project, "check", "--json", code=1).stdout)
        self.assertEqual(data["counts"]["outside_context"], 1)
        self.assertEqual(status_of(self, project),
                         {"cause": "outside-context", "errored": False, "key": "gap",
                          "reason": OUTSIDE + " — an owned assumption would carry it as "
                          "Assumed: name its owner and a fallback reason in claims/c1.json "
                          "(owner, fallback) — nothing can record the owner yet",
                          "word": "gap"})
        text = run(self, project, "report", code=0).stdout
        gaps = text.split("## Gaps", 1)[1].split("\n## ", 1)[0]
        self.assertIn("### Outside an evaluator's operating context", gaps)
        self.assertIn(f"- **c1** Sag stays under 0.5 mm — {OUTSIDE}", gaps)
        with open(os.path.join(project, ".atompipe", "out", "junit.xml"),
                  encoding="utf-8") as fh:
            junit = ET.fromstring(fh.read())
        (case,) = junit.iterfind("testsuite[@name='claims.critical']/testcase[@name='c1']")
        self.assertIn(OUTSIDE, case.find("failure").get("message"))
        run(self, project, "site", "init", code=0)
        run(self, project, "site", "build", code=0)
        with open(os.path.join(project, "site", "data", "state.json"), encoding="utf-8") as fh:
            state = json.load(fh)
        (row,) = state["claims"]
        self.assertEqual((row["status"], row["cause"]), ("unclaimed", "outside-context"))
        self.assertTrue(row["reason"].startswith(OUTSIDE))

        removed = span_project(self, load_n=60.0, stiffness=12000.0, context="")
        run(self, removed, "check", code=0)
        self.assertEqual(status_of(self, removed)["key"], "checked")

    def test_a_guard_outside_its_context_establishes_nothing(self):
        """(f) A guard passing outside its context is a negative prerequisite
        root of kind ``unqualified``: its dependent is not run."""
        root = _projects.plant_project(
            os.path.join(self.tmp(), "guard"),
            context_files(gates_text=GUARD_GATES, load_n=60.0, stiffness=12000.0),
            claims={"c2": C2, "c3": C3}, known_good_params=known_good())
        p = _projects.Planted(root, {"c2": C2, "c3": C3}, now=NOW)
        result = p.sweep()
        dep = p.row(result, "t.dep").verdict
        self.assertEqual((dep.outcome, list(dep.blocked_by), dep.blocked_kind),
                         ("skipped", ["t.guard"], "unqualified"))
        found = p.composed()
        self.assertEqual(found["c3"].status, ClaimStatus.BLOCKED)
        self.assertEqual(report.reason(found["c3"], p.ledger, p.ledger.claim("c3")),
                         "skipped: t.dep : prerequisite not established: t.guard (unqualified)")

    def test_a_model_that_does_not_load_reads_stale(self):
        """(g) The context is judged on current values, and with no model there
        are none: the claim reads Stale, ``the model does not load``, never Gap
        ``absent``."""
        project = span_project(self)
        run(self, project, "check", code=0)
        with open(os.path.join(project, "model", "m.py"), "a", encoding="utf-8") as fh:
            fh.write("\nthis is not python\n")
        found = status_of(self, project)
        self.assertEqual((found["key"], found["cause"]), ("stale", "invalidated"))
        self.assertIn("the model does not load", found["reason"])

    def test_a_declared_key_its_run_never_reads_is_an_error(self):
        """(critique 3) The context is part of the read set: a pass whose run
        never read a declared key errors — otherwise a change inside the range
        would never re-key it, and a model that does not load would leave it
        Fresh and counted while ``check`` reads it Gap."""
        def blind(ctx):
            sag = float(ctx.params["sag"])
            return Verdict(gate="t.span", passed=sag <= 0.5, measured=sag, limit=0.5,
                           units="mm", comparator="<=")
        ctx = gates.GateContext(params={"load_n": 20.0, "sag": 0.25})
        got = gates.run_gate(_spec(), blind, ctx)
        self.assertEqual(got.outcome, "error")
        self.assertIn("load_n", got.error)
        failed = gates.run_gate(_spec(), lambda c: Verdict(gate="t.span", passed=False), ctx)
        self.assertEqual(failed.outcome, "fail", "a fail needs nothing (R-3)")


# --------------------------------------------------------------------------- #
# V10 — a fail outside still counts (R-3)
# --------------------------------------------------------------------------- #
class AFailOutsideStillCounts(_env.EnvCase):
    """(V10) Outside its context an evaluator's fail still counts: the claim
    reads Failing, and an invalidated fail stays Failing (D-08)."""

    def test_a_fail_outside_is_failing_and_stays_so_when_invalidated(self):
        """(a, b)"""
        project = span_project(self, load_n=60.0, stiffness=4000.0)
        proc = run(self, project, "check", code=1)
        self.assertIn("[FAIL ] c1 Sag stays under 0.5 mm — t.span : 0.900 mm (limit 0.5 mm)",
                      proc.stdout.splitlines())
        set_default(project, "load_n", 70.0)
        found = status_of(self, project)
        self.assertEqual((found["key"], found["cause"]), ("failing", "failed"))

    def test_the_mark_never_launders_a_fail(self):
        """(c) D16: a context mark on a fail is dropped where the verdict is
        built — so a fail never reads the quieter Gap, whoever built it."""
        marked = Verdict(gate="t.span", passed=False, claims=["c1"], measured=0.9, limit=0.5,
                         unqualified=_token())
        self.assertEqual((marked.unqualified, marked.outcome), ("", "fail"))
        self.assertEqual(claims.compose(_c1(), [marked]).status, ClaimStatus.FAIL)

        def unguarded(self_):
            if self_.unqualified and not self_.error:
                self_.error = f"unqualified: {self_.unqualified}"
        with mock.patch.object(Verdict, "__post_init__", unguarded):
            planted = Verdict(gate="t.span", passed=False, claims=["c1"], measured=0.9,
                              limit=0.5, unqualified=_token())
        self.assertEqual(claims.compose(_c1(), [planted]).status, ClaimStatus.UNCLAIMED,
                         "without the guard the fail reads Gap (the violator)")


# --------------------------------------------------------------------------- #
# V11 — inside the context, a change reads Stale
# --------------------------------------------------------------------------- #
class AChangeInsideReadsStale(_env.EnvCase):
    """(V11) Inside its context an evaluator is what it was: a moved input reads
    Stale until a check run, then Checked. Moved outside before a check run, the
    pass it holds is judged on the input as it is now: Gap, not Stale."""

    def test_inside_stale_then_checked_and_outside_gap(self):
        project = span_project(self)
        run(self, project, "check", code=0)
        set_default(project, "load_n", 30.0)
        found = status_of(self, project)
        self.assertEqual((found["key"], found["cause"]), ("stale", "invalidated"))
        self.assertIn("t.span : load_n 20.0 -> 30.0", found["reason"])
        run(self, project, "check", code=0)
        self.assertEqual(status_of(self, project)["key"], "checked")
        set_default(project, "load_n", 60.0)
        found = status_of(self, project)
        self.assertEqual((found["key"], found["cause"]), ("gap", "outside-context"))


# --------------------------------------------------------------------------- #
# V12 — an owned fallback reads Assumed
# --------------------------------------------------------------------------- #
class AnOwnedFallbackReadsAssumed(unittest.TestCase):
    """(V12) A claim whose evaluator's pass lies outside its context reads
    Assumed when it carries a fallback reason and an owner who recorded it —
    through ``owners``, which only the signing channel produces (none in P2.4)."""

    FALLBACK = "the sag scales linearly well past 40 N on this section"

    def _owned(self, **kw) -> Claim:
        return _c1(owner="owner-1", fallback=self.FALLBACK, **kw)

    def _owners(self, reason: str | None = None) -> dict:
        return {"c1": claims.Attribution("owner-1", reason or self.FALLBACK)}

    def test_attributed_reads_assumed(self):
        """(a)"""
        claim = self._owned()
        found = claims.compose(claim, [_pass(unqualified=_token())], owners=self._owners())
        self.assertEqual((found.status, found.cause),
                         (ClaimStatus.ASSERTED, claims.ClaimCause.FALLBACK))
        self.assertEqual(report.reason(found, Ledger(claims=[claim]), claim),
                         "assumed by owner-1 outside operating context: t.span : load_n = 60, "
                         "qualified on [0, 40]")

    def test_named_but_unattributed_reads_gap_and_says_so(self):
        """(b) and critique 12: the hint for an owner already named."""
        claim = self._owned()
        found = claims.compose(claim, [_pass(unqualified=_token())])
        self.assertEqual((found.status, found.cause),
                         (ClaimStatus.UNCLAIMED, claims.ClaimCause.OUTSIDE_CONTEXT))
        full = report.reason(found, Ledger(claims=[claim]), claim, full=True)
        self.assertIn("owner-1 is named with a fallback in claims/c1.json and has not recorded "
                      "it", full)

    def test_a_fallback_edited_after_attribution_reads_gap(self):
        """(c)"""
        claim = self._owned()
        found = claims.compose(claim, [_pass(unqualified=_token())],
                               owners=self._owners("an earlier reason"))
        self.assertEqual(found.status, ClaimStatus.UNCLAIMED)

    def test_stale_and_unrun_still_lead(self):
        """(d) An owned fallback never hides an invalidated or unrun evaluator
        beside the one outside its context (Assumed is rung 7)."""
        claim = dataclasses.replace(self._owned(), gates=["t.span", "t.other"])
        found = claims.compose(claim, [_pass(unqualified=_token()), _pass(gate="t.other")],
                               owners=self._owners(), stale_gates={"t.other"})
        self.assertEqual(found.status, ClaimStatus.STALE)
        unrun = claims.compose(claim, [_pass(unqualified=_token())], owners=self._owners())
        self.assertEqual(unrun.status, ClaimStatus.PENDING)

    def test_assumed_is_unresolved_and_does_not_stop_check(self):
        """(e)"""
        claim = self._owned()
        ledger = Ledger(claims=[claim], verdicts=[_pass(unqualified=_token())])
        spec = _spec()
        self.assertEqual(claims.blocking(ledger, [spec], owners=self._owners()), [])
        summary = claims.summarise(ledger, [spec], owners=self._owners())
        self.assertFalse(summary["all_required_checked"])
        self.assertEqual(summary["unresolved_ids"], ["c1"])


# --------------------------------------------------------------------------- #
# V13 — the known-good control lies inside the context
# --------------------------------------------------------------------------- #
class KnownGoodOutsideIsUnqualified(_env.EnvCase):
    """(V13) An evaluator whose known-good control lies outside its own context
    was never shown to pass inside it: unqualified."""

    def test_the_judge_reads_known_good_outside(self):
        """In process: the known-good half that passes outside the context is
        the judge's `known-good:outside`, and its line says so; the same project
        with the known-good design inside qualifies (the violator inverted)."""
        root = span_project(self, kg=known_good(load_n=45.0, stiffness=12000.0))
        p = _projects.Planted(root, {"c1": C1}, now=NOW)
        got = p.row(p.sweep(), "t.span")
        self.assertEqual(got.verdict.unqualified, "known-good:outside")
        self.assertEqual(report.qualification_line("t.span", got.admission.qualification),
                         "t.span : known-good outside its operating context · known-bad fail "
                         "→ unqualified")
        inside = _projects.Planted(span_project(self), {"c1": C1}, now=NOW)
        self.assertEqual(inside.row(inside.sweep(), "t.span").verdict.unqualified, "")

    def test_a_known_good_outside_is_unqualified_and_inside_qualified(self):
        project = span_project(self, kg=known_good(load_n=45.0, stiffness=12000.0))
        proc = run(self, project, "gate", "selftest", code=1)
        self.assertIn("t.span : known-good outside its operating context · known-bad fail "
                      "→ unqualified", proc.stdout.splitlines())
        run(self, project, "check", code=1)
        found = status_of(self, project)
        self.assertEqual((found["key"], found["cause"], found["reason"]),
                         ("gap", "unqualified",
                          "unqualified: t.span : known-good outside its operating context"))
        inside = span_project(self)
        run(self, inside, "gate", "selftest", code=0)
        run(self, inside, "check", code=0)
        self.assertEqual(status_of(self, inside)["key"], "checked")


# --------------------------------------------------------------------------- #
# V14 — a mutation counts wherever it lands (critique 2)
# --------------------------------------------------------------------------- #
def _outside_inconclusive(real):
    """The P2.4 design's D20, planted: a passing landing outside the context
    filed inconclusive."""
    def walk(spec, fn, good_ctx, good_verdict, **kw):
        found = real(spec, fn, good_ctx, good_verdict, **kw)
        keep, moved = [], []
        for result in found.results:
            lo, hi = (spec.operating_context or {}).get(result.key[-1], (None, None))
            outside = ((lo is not None and result.after < lo)
                       or (hi is not None and result.after > hi))
            (moved if outside and result.outcome == "pass" else keep).append(result)
        return dataclasses.replace(
            found, results=tuple(keep),
            inconclusive=found.inconclusive + tuple(
                dataclasses.replace(r, outcome="errored", why="outside-context") for r in moved))
    return walk


class AMutationPassingOutsideStillCounts(_env.EnvCase):
    """(V14) A walk landing reads the evaluator's flag against its OWN value
    past its OWN limit — a property of its code, not of the regime — so a
    passing landing is a conclusive pass wherever it lands. What slipped through
    the P2.4 design (critique 2): filed inconclusive outside the context, so a
    context that ends short of the landing qualified an evaluator keyed to its own
    control, and narrowing a declaration by one unit turned an unqualified
    evaluator qualified."""

    def _edge(self, range_: str) -> _projects.Planted:
        # live load_n 5, inside both contexts: the live pass counts, so the
        # verdict's mark is the qualification's alone
        files = context_files(gates_text=EDGE_GATE.format(range=range_), load_n=5.0)
        root = _projects.plant_project(os.path.join(self.tmp(), "edge"), files,
                                       claims={"c1": C1},
                                       known_good_params=known_good(load_n=5.0))
        return _projects.Planted(root, {"c1": C1}, now=NOW)

    def test_a_passing_landing_counts_inside_and_outside_the_context(self):
        for range_ in ("(0.0, 10.0)", "(0.0, 9.0)"):
            with self.subTest(context=range_):
                p = self._edge(range_)
                got = p.row(p.sweep(), "t.edge")
                self.assertEqual(got.verdict.unqualified, "mutation:pass|0/1")
                self.assertEqual(report.qualification_line("t.edge", got.admission.qualification),
                                 "t.edge : known-good pass · known-bad fail · mutation 0/1 fail "
                                 "→ unqualified")

    def test_filed_inconclusive_outside_it_qualifies_the_keyed_evaluator(self):
        """The violator: D20 as designed."""
        p = self._edge("(0.0, 9.0)")
        with mock.patch.object(gates, "mutation_walk", _outside_inconclusive(gates.mutation_walk)):
            got = p.row(p.sweep(), "t.edge")
        self.assertEqual(got.verdict.unqualified, "")


# --------------------------------------------------------------------------- #
# V15 — a malformed context is refused (R-10: the new field only)
# --------------------------------------------------------------------------- #
class MalformedContextsAreRefused(unittest.TestCase):
    """(V15) Registration refuses a declaration that cannot be judged, naming
    the gate and the key; a well-formed one registers, round-trips, and is the
    registry's own copy."""

    def _register(self, context):
        registry = gates.Registry()

        @gates.gate(id="t.ctx", claims=["c1"], registry=registry, operating_context=context,
                    negative_control=NegativeControl(fixture="selftest/bad.py:soft"))
        def fn(ctx):
            return True
        return registry

    def test_each_malformed_shape_is_refused(self):
        for name, context in (("not a mapping", [("load_n", (0, 1))]),
                              ("empty key", {"": (0, 1)}),
                              ("not a string key", {3: (0, 1)}),
                              ("not two items", {"load_n": (0, 1, 2)}),
                              ("a bool bound", {"load_n": (True, 1)}),
                              ("a word bound", {"load_n": ("0", 1)}),
                              ("an infinite bound", {"load_n": (0, float("inf"))}),
                              ("both ends open", {"load_n": (None, None)}),
                              ("lo above hi", {"load_n": (2, 1)})):
            with self.subTest(case=name):
                with self.assertRaises(AtompipeError) as caught:
                    self._register(context)
                self.assertIn("t.ctx", str(caught.exception))

    def test_a_well_formed_one_registers_round_trips_and_is_copied(self):
        registry = self._register({"load_n": (0.0, 40.0), "span": (None, 100)})
        (spec,) = registry.specs()
        self.assertEqual(spec.operating_context, {"load_n": (0.0, 40.0), "span": (None, 100.0)})
        again = GateSpec.from_dict(json.loads(json.dumps(spec.to_dict())))
        self.assertEqual(again.operating_context, spec.operating_context)
        spec.operating_context["load_n"] = (0.0, 1.0)
        (fresh,) = registry.specs()
        self.assertEqual(fresh.operating_context["load_n"], (0.0, 40.0))


# --------------------------------------------------------------------------- #
# review of P2.4 — every channel says an outside pass in the context's words
# --------------------------------------------------------------------------- #
class EveryChannelSaysOutsideInItsOwnWords(_env.EnvCase):
    """What slipped through P2.4 (review): the CLI said a pass outside its
    operating context in the context's words, and three channels did not —
    ``check --json``'s qualification row carried the ``context:outside`` token
    on a row whose state is admitted and whose line says qualified; the page
    labelled the row ``? UNQUALIFIED``, the classification P2.4-D22 rejected; and
    ``why`` and ``claim show`` printed ``[ERR ] t.span : unqualified:
    context:outside|{…}`` — a crash's tag and the spine's token."""

    def _project(self) -> str:
        project = span_project(self, load_n=60.0, stiffness=12000.0)
        run(self, project, "check", code=1)
        return project

    def test_the_json_qualification_row_carries_only_the_qualification(self):
        """The context fact stays where it is a fact of this pass — the verdict
        row's ``unqualified`` and ``counts.outside_context``."""
        project = span_project(self, load_n=60.0, stiffness=12000.0)
        data = json.loads(run(self, project, "check", "--json", code=1).stdout)
        (row,) = [q for q in data["qualifications"] if q["gate"] == "t.span"]
        self.assertEqual((row["state"], row["token"]), ("admitted", ""))
        self.assertTrue(row["line"].endswith("→ qualified"), row["line"])
        (verdict,) = [v for v in data["verdicts"] if v["gate"] == "t.span"]
        self.assertTrue(verdict["unqualified"].startswith("context:outside|"))
        self.assertEqual(data["counts"]["outside_context"], 1)

    def test_the_page_row_reads_outside_context_never_unqualified(self):
        project = self._project()
        run(self, project, "site", "init", code=0)
        run(self, project, "site", "build", code=0)
        with open(os.path.join(project, "site", "data", "state.json"), encoding="utf-8") as fh:
            state = json.load(fh)
        (row,) = [v for v in state["verdicts"] if v["gate"] == "t.span"]
        self.assertEqual(row["status"], "outside-context")
        self.assertEqual(state["outcome_words"]["outside-context"],
                         "outside operating context")
        self.assertTrue(state["phrases"]["outcome_hint"]["outside-context"])
        self.assertTrue(row["qualification"]["text"].startswith("outside operating context: "))
        with open(os.path.join(project, "site", "lib", "format.js"), encoding="utf-8") as fh:
            self.assertIn('"outside-context": { glyph: "?", tone: "warn" }', fh.read())

    def test_why_and_claim_show_say_the_context_never_the_token(self):
        project = self._project()
        for argv in (["why", "c1"], ["claim", "show", "c1"]):
            with self.subTest(argv=argv):
                text = run(self, project, *argv, code=0).stdout
                self.assertNotIn("context:outside|", text)
                self.assertNotIn("[ERR ]", text)
                # The evaluator's row as `check` and `gate show` print it.
                rows = [ln.strip() for ln in text.splitlines()
                        if ln.strip().startswith("t.span : ")]
                self.assertEqual(len(rows), 1, text)
                self.assertRegex(rows[0], T.CONTEXT_LINE)
                self.assertIn("t.span : outside operating context: load_n = 60, qualified on "
                              "[0, 40]", rows[0])


class APackBaselineOutsideItsContextIsNotPublishable(_env.EnvCase):
    """What slipped through P2.4 (review): `pack validate` mapped the known-good
    outcome without judging the context (P2.4-D19 lived only in `check`'s and
    `gate selftest`'s known-good half), so a pack whose baseline sat outside a
    declared range validated `publishable` while `gate selftest` printed
    `known-good outside its operating context · known-bad fail → unqualified`."""

    def _pack(self) -> str:
        import shutil
        pack_dir = os.path.join(self.tmp(), "beamctx")
        shutil.copytree(os.path.join(_projects.PACKS, "beam-analytic"), pack_dir,
                        ignore=shutil.ignore_patterns("__pycache__", ".selftest-out"))
        manifest_path = os.path.join(pack_dir, "pack.json")
        with open(manifest_path, encoding="utf-8") as fh:
            manifest = json.load(fh)
        manifest["name"] = "beamctx"
        with open(manifest_path, "w", encoding="utf-8") as fh:
            json.dump(manifest, fh, indent=2)
        path = os.path.join(pack_dir, "gates", "beam.py")
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        anchor = '    id="beam.deflection",\n'
        self.assertEqual(text.count(anchor), 1)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text.replace(anchor, anchor + '    operating_context={"load_n": '
                                  '(0.0, 100.0)},\n'))
        return pack_dir

    def test_validate_names_the_known_good_outside(self):
        from atompipe import packs
        problems = packs.validate(self._pack())
        hits = [p for p in problems if p.startswith("beam.deflection:")
                and "known-good outside its operating context" in p]
        self.assertEqual(len(hits), 1, problems)

    def test_without_the_judgement_it_reads_publishable(self):
        """The violator: the breach rule stubbed out on validate's path."""
        from atompipe import packs
        with mock.patch.object(gates, "context_breach", lambda *a, **k: None):
            problems = packs.validate(self._pack())
        self.assertEqual([p for p in problems if "operating context" in p], [], problems)
