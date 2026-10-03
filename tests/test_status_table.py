# SPDX-License-Identifier: Apache-2.0
"""What a claim's status is today, written out, before Phase 2 changes it.

Phase 2 moves claim statuses for the first time since the spine began
(PLAN-v0.14 §1.4): an errored evaluator leaves FAIL for Skipped, an unqualified
one leaves FAIL for Gap, a pass beside a skip or an unrun evaluator stops
reading PASS. R-8's oracle for the phase is that statuses move only toward
GLOSSARY's *unresolved*, and the commit lists every move with its reason. These
tests make that oracle mechanical over the resolver itself, not only over the
fixture corpus: they pin today's table (C), so the change that moves a row has
to rewrite its expected value here, in the open, where
`StatusesMoveOnlyTowardUnresolved` (P2.1) then checks the direction of every
row that changed.

* **StatusTable** (C1) — `claims.resolve_status` for an automated claim over
  every multiset of zero to two covering outcomes from pass, fail, skipped,
  errored and **not admitted** (an error that starts `not admitted:`, today an
  error like any other, from P2 a Gap), each with and without a known gate
  that has not run, and with the passing gate stale or not; plus the physical
  and assumption rows. What slipped through without it: `StatusPrecedence` and
  `RenderersAgree.test_table` hold single verdicts and five pairs, and the
  pairs rung 4 moves — error beside a skip, a pass, an unrun gate — are exactly
  the ones nobody had written down (P2.0 design D-14; the not-admitted rows
  from its review).
* **ReportSectionOrder** (C2) — the report's sections, in order, and
  `SECTION_PROVEN` as the first (old checkpoint 2.0's C, still unpinned; P2.5's
  REPORT.md replaces it).
* **UnqualifiedBesideAPassIsNeverChecked** (V, invariant 4) — a pass beside an
  evaluator that is not admitted at its version never reads PASS, is never
  under PROVEN and is never a childless JUnit testcase, in either order. What
  this stops: an invariant-9 change that drops the unadmitted verdict from the
  claim's verdicts ("it does not count") and so leaves the other gate's pass
  alone — PASS, i.e. Checked, where GLOSSARY §3 says Gap "also beside a passing
  evaluator". No test put a pass and a not-admitted verdict on one claim before.

Run:  PYTHONPATH=src python3 -m unittest discover -s tests -p test_status_table.py -v
"""
from __future__ import annotations

import itertools
import re
import unittest
import xml.etree.ElementTree as ET
from typing import Any, Callable
from unittest import mock

from atompipe import claims as claims_mod
from atompipe import report as report_mod
from atompipe.models import (
    Acceptance, Claim, ClaimKind, ClaimStatus, Comparator, GateSpec, Ledger, NegativeControl,
    PhysicalResult, ProjectMeta, Tier, Verdict,
)

#: The five covering outcomes a claim's verdicts can carry, as the table names them.
KINDS = ("pass", "fail", "skipped", "error", "unadmitted")

#: The text an admission refusal carries (`verdicts.py` synthesizes it; JUnit
#: reads the prefix, `report._NOT_ADMITTED`).
NOT_ADMITTED = "not admitted: its known-bad control passed at this version"


def _verdict(kind: str, index: int) -> Verdict:
    gate = f"g.{index}{kind}"
    return {
        "pass": Verdict(gate=gate, claims=["C1"], passed=True, detail="0.4 mm"),
        "fail": Verdict(gate=gate, claims=["C1"], passed=False, detail="0.7 mm"),
        "skipped": Verdict(gate=gate, claims=["C1"], skipped=True,
                           skip_reason="requires x (not on PATH)"),
        "error": Verdict(gate=gate, claims=["C1"], error="ZeroDivisionError: x"),
        "unadmitted": Verdict(gate=gate, claims=["C1"], error=NOT_ADMITTED),
    }[kind]


def _claim(gates: list[str], kind: ClaimKind = ClaimKind.MEASURABLE, **kw: Any) -> Claim:
    return Claim(id="C1", statement="the thing holds", kind=kind, gates=gates,
                 acceptance=Acceptance(quantity="deflection", comparator=Comparator.LE,
                                       limit=0.5, units="mm"), **kw)


def row_key(combo: tuple[str, ...], unrun: bool, stale: bool) -> str:
    return "+".join(combo) + ("" if combo else "-") + (" unrun" if unrun else "") + (
        " stale" if stale else "")


def resolved_table(resolve: Callable[..., ClaimStatus] | None = None) -> dict[str, str]:
    """Every automated row, resolved by `resolve` (default: the resolver as it is
    when called, not as it was imported): zero to two outcomes, with and without
    an unrun known gate, with the first passing gate stale or not."""
    resolve = resolve or claims_mod.resolve_status
    out: dict[str, str] = {}
    for size in (0, 1, 2):
        for combo in itertools.combinations_with_replacement(KINDS, size):
            for unrun in (False, True):
                for stale in ((False, True) if "pass" in combo else (False,)):
                    verdicts = [_verdict(kind, i) for i, kind in enumerate(combo)]
                    gates = [v.gate for v in verdicts] + (["g.unrun"] if unrun else [])
                    stale_gates = ({next(v.gate for v in verdicts if v.outcome == "pass")}
                                   if stale else set())
                    status = resolve(_claim(gates), verdicts, stale_gates=stale_gates)
                    out[row_key(combo, unrun, stale)] = ClaimStatus(status).value
    return out


#: Today's table (860ffa6), written out — never computed: a table derived from
#: the code under test agrees with it by construction (`test_renderers.EXPECTED`).
#: Read against the ladder: an error or a refusal of admission reads fail (rung
#: 4), alone or beside anything; a pass beside a skip or an unrun gate reads pass
#: (S-03); only all-skipped reads blocked. P2.1 rewrites the rows rung 4 moves
#: and keeps this column as `AT_860FFA6` for `StatusesMoveOnlyTowardUnresolved`.
EXPECTED: dict[str, str] = {
    "-": "unclaimed",
    "- unrun": "pending",
    "pass": "pass",
    "pass stale": "stale",
    "pass unrun": "pass",
    "pass unrun stale": "stale",
    "fail": "fail",
    "fail unrun": "fail",
    "skipped": "blocked",
    "skipped unrun": "blocked",
    "error": "fail",
    "error unrun": "fail",
    "unadmitted": "fail",
    "unadmitted unrun": "fail",
    "pass+pass": "pass",
    "pass+pass stale": "stale",
    "pass+pass unrun": "pass",
    "pass+pass unrun stale": "stale",
    "pass+fail": "fail",
    "pass+fail stale": "fail",
    "pass+fail unrun": "fail",
    "pass+fail unrun stale": "fail",
    "pass+skipped": "pass",
    "pass+skipped stale": "stale",
    "pass+skipped unrun": "pass",
    "pass+skipped unrun stale": "stale",
    "pass+error": "fail",
    "pass+error stale": "fail",
    "pass+error unrun": "fail",
    "pass+error unrun stale": "fail",
    "pass+unadmitted": "fail",
    "pass+unadmitted stale": "fail",
    "pass+unadmitted unrun": "fail",
    "pass+unadmitted unrun stale": "fail",
    "fail+fail": "fail",
    "fail+fail unrun": "fail",
    "fail+skipped": "fail",
    "fail+skipped unrun": "fail",
    "fail+error": "fail",
    "fail+error unrun": "fail",
    "fail+unadmitted": "fail",
    "fail+unadmitted unrun": "fail",
    "skipped+skipped": "blocked",
    "skipped+skipped unrun": "blocked",
    "skipped+error": "fail",
    "skipped+error unrun": "fail",
    "skipped+unadmitted": "fail",
    "skipped+unadmitted unrun": "fail",
    "error+error": "fail",
    "error+error unrun": "fail",
    "error+unadmitted": "fail",
    "error+unadmitted unrun": "fail",
    "unadmitted+unadmitted": "fail",
    "unadmitted+unadmitted unrun": "fail",
}

#: The physical and assumption rows: what a result, and a covering gate's
#: outcome beside no result, give today. A physical claim reads no evaluator
#: (old 2.1, S-49, changes that: a failing or errored modelled half resolves it).
EXPECTED_OTHER_KINDS: dict[str, str] = {
    "physical, no result": "unverified",
    "physical, a pass recorded": "verified",
    "physical, a fail recorded": "refuted",
    "physical, no result, a covering gate failed": "unverified",
    "physical, no result, a covering gate errored": "unverified",
    "assumption": "asserted",
}


def other_kinds_table() -> dict[str, str]:
    physical = ClaimKind.PHYSICAL
    cases = {
        "physical, no result": (_claim([], physical), []),
        "physical, a pass recorded": (
            _claim([], physical, physical_result=PhysicalResult(passed=True)), []),
        "physical, a fail recorded": (
            _claim([], physical, physical_result=PhysicalResult(passed=False)), []),
        "physical, no result, a covering gate failed": (
            _claim(["g.0fail"], physical), [_verdict("fail", 0)]),
        "physical, no result, a covering gate errored": (
            _claim(["g.0error"], physical), [_verdict("error", 0)]),
        "assumption": (_claim([], ClaimKind.ASSUMPTION), []),
    }
    return {name: claims_mod.resolve_status(claim, verdicts).value
            for name, (claim, verdicts) in cases.items()}


class StatusTable(unittest.TestCase):
    """(C) Today's `resolve_status`, every row."""

    def test_the_automated_rows(self):
        table = resolved_table()
        self.assertEqual(len(table), 54)
        self.assertEqual(sorted(table), sorted(EXPECTED), "a row was added or lost")
        for key, want in EXPECTED.items():
            with self.subTest(row=key):
                self.assertEqual(table[key], want)

    def test_the_physical_and_assumption_rows(self):
        self.assertEqual(other_kinds_table(), EXPECTED_OTHER_KINDS)

    def test_the_order_of_a_claims_verdicts_moves_no_row(self):
        """Every two-outcome row reads the same whichever verdict comes first —
        so the table above is a property of the multiset, as its keys say."""
        for a, b in itertools.combinations(KINDS, 2):
            with self.subTest(pair=(a, b)):
                forward = [_verdict(a, 0), _verdict(b, 1)]
                claim = _claim([v.gate for v in forward])
                self.assertEqual(claims_mod.resolve_status(claim, forward),
                                 claims_mod.resolve_status(claim, forward[::-1]))

    def test_a_changed_row_is_caught(self):
        """The table's own control: a resolver that lets a pass beside an error
        through changes exactly the rows that hold both, and the comparison
        sees it."""
        real = claims_mod.resolve_status

        def planted(claim, verdicts, **kw):
            verdicts = list(verdicts)
            if any(v.outcome == "pass" for v in verdicts):
                verdicts = [v for v in verdicts if v.outcome != "error"]
            return real(claim, verdicts, **kw)

        moved = {k for k, v in resolved_table(planted).items() if EXPECTED[k] != v}
        self.assertEqual(moved, {k for k in EXPECTED if k.startswith(("pass+error",
                                                                      "pass+unadmitted"))})


class ReportSectionOrder(unittest.TestCase):
    """(C) The readiness report's sections, in the order a sceptical reader needs
    them — today's, so P2.5's REPORT.md changes it in the open."""

    WANT = ["## What is PROVEN", "## What is NOT verified", "## Open gaps",
            "## Standing constraints", "## Failing / blocked", "## Reproduce"]

    def _headings(self, ledger: Ledger) -> list[str]:
        out = []
        for line in report_mod.render_markdown(ledger, None).splitlines():
            if line.startswith("## "):
                out.append(re.sub(r" \(.*\)$", "", line))
        return out

    def test_every_section_in_order_empty_or_full(self):
        empty = Ledger(meta=ProjectMeta(name="t", revision="v0.1"))
        claims = [
            Claim(id="C1", statement="passes", kind=ClaimKind.MEASURABLE, gates=["g.a"]),
            Claim(id="C2", statement="a real part", kind=ClaimKind.PHYSICAL),
            Claim(id="C3", statement="assumed", kind=ClaimKind.ASSUMPTION, rationale="r"),
            Claim(id="C4", statement="fails", kind=ClaimKind.MEASURABLE, gates=["g.b"]),
            Claim(id="C5", statement="no gate", kind=ClaimKind.MEASURABLE),
        ]
        full = Ledger(meta=ProjectMeta(name="t", revision="v0.1"), claims=claims,
                      verdicts=[Verdict(gate="g.a", claims=["C1"], passed=True),
                                Verdict(gate="g.b", claims=["C4"], passed=False)])
        for name, ledger in (("empty", empty), ("full", full)):
            with self.subTest(ledger=name):
                self.assertEqual(self._headings(ledger), self.WANT)

    def test_section_proven_is_the_first_section(self):
        self.assertEqual(report_mod.SECTION_PROVEN, self.WANT[0])
        md = report_mod.render_markdown(Ledger(meta=ProjectMeta(name="t", revision="v0.1")),
                                        None)
        first = next(line for line in md.splitlines() if line.startswith("## "))
        self.assertTrue(first.startswith(report_mod.SECTION_PROVEN + " "), first)


class _Specs(list):
    def specs(self):
        return list(self)


def overclaims(resolve: Callable[..., ClaimStatus] | None = None) -> list[str]:
    """Where a pass beside a not-admitted evaluator reads Checked: the status,
    the PROVEN section, the JUnit claim case — in both orders."""
    specs = _Specs(GateSpec(id=g, claims=["C1"], tier=Tier.INSTANT,
                            negative_control=NegativeControl(fixture="x:y"))
                   for g in ("g.0pass", "g.1unadmitted"))
    passing, refused = _verdict("pass", 0), _verdict("unadmitted", 1)
    out: list[str] = []
    patch = (mock.patch.object(claims_mod, "resolve_status", resolve) if resolve
             else mock.patch.object(claims_mod, "resolve_status", claims_mod.resolve_status))
    with patch:
        for verdicts in ([passing, refused], [refused, passing]):
            first = verdicts[0].gate
            claim = _claim([v.gate for v in verdicts])
            ledger = Ledger(meta=ProjectMeta(name="t", revision="v0.1"), claims=[claim],
                            verdicts=list(verdicts))
            status = claims_mod.resolve_status(claim, verdicts)
            if status in (ClaimStatus.PASS, ClaimStatus.VERIFIED):
                out.append(f"{first} first: resolves {status.value}")
            md = report_mod.render_markdown(ledger, specs)
            proven = md.split(report_mod.SECTION_PROVEN, 1)[1].split("\n## ", 1)[0]
            if re.search(r"^\| \*\*C1\*\*", proven, re.M):
                out.append(f"{first} first: C1 under PROVEN")
            junit = ET.fromstring(report_mod.render_junit(
                ledger, verdicts, specs, tier=0, ready=False, exit_code=1,
                when="2026-10-03T00:00:00Z"))
            case = junit.find("testsuite[@name='claims.critical']/testcase[@name='C1']")
            if case is None or not [c for c in case if c.tag in ("failure", "error")]:
                out.append(f"{first} first: the JUnit claim case is not red")
    return out


class UnqualifiedBesideAPassIsNeverChecked(unittest.TestCase):
    """(4) A claim never reads Checked while one of its evaluators is not
    admitted, whatever else passed beside it."""

    def test_a_pass_beside_an_unadmitted_evaluator_is_never_checked(self):
        self.assertEqual(overclaims(), [])

    def test_a_resolver_that_lets_the_pass_win_is_caught(self):
        real = claims_mod.resolve_status

        def drops_the_unadmitted(claim, verdicts, **kw):
            return real(claim, [v for v in verdicts
                                if not str(v.error).startswith("not admitted:")], **kw)

        found = overclaims(drops_the_unadmitted)
        self.assertEqual(len(found), 6, found)


if __name__ == "__main__":
    unittest.main(verbosity=2)
