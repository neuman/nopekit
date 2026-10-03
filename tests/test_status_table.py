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
  the ones nobody had written down (the not-admitted rows from the P2.0
  design's review). *Rejected:* relying on those two, for that reason.
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
  And end to end, through the commands, on a project whose new evaluator is
  refused at its first check.
* **UnqualifiedNotYetRefused** (C) — the ratchet for that end-to-end run:
  ``KNOWN_OVERCLAIMS``, each a place where a reader of the project after
  ``check`` still reads the refused evaluator's claim as checked, today.

What slipped through the first version of this module: its invariant-4 test
hand-built ``Verdict(error="not admitted: …")`` and fed it to the resolver and
the renderers, so it never saw that the product never builds one for a gate
refused at its first check. ``check`` lists that claim as BLOCKING and exits 1;
every reader after it — ``status``, ``report``, ``claim list``, ``why``, the
page — drops the refusal, because ``verdicts.resolve`` consults admission only
over a Fresh cache entry and a refused gate is never cached. The gate reads
"never run", and "pass unrun" reads pass: ``status --json`` says ``ready: true``
and the report puts the claim under PROVEN. The Phase 2 change that reads an
unqualified evaluator's claim as Gap (PLAN-v0.14 §1.4: "also beside a passing
evaluator") must make ``resolve`` say the refusal for a gate with no entry, and
empties the ratchet; until then it is listed, channel by channel, and anything
else that overclaims there is red.

Run:  PYTHONPATH=src python3 -m unittest discover -s tests -p test_status_table.py -v
"""
from __future__ import annotations

import dataclasses
import itertools
import json
import os
import re
import tempfile
import unittest
import xml.etree.ElementTree as ET
from typing import Any, Callable, NamedTuple
from unittest import mock

import _env
import _projects
from atompipe import claims as claims_mod
from atompipe import report as report_mod
from atompipe.models import (
    BLOCKING_STATUSES, Acceptance, Claim, ClaimKind, ClaimStatus, Comparator, GateSpec, Ledger, NegativeControl,
    PhysicalResult, ProjectMeta, Tier, Verdict,
)

#: The five covering outcomes a claim's verdicts can carry, as the table names them.
KINDS = ("pass", "fail", "skipped", "error", "unadmitted")

#: The refusal's reason, and the text an admission refusal carries beside it
#: (`verdicts._unqualified` sets both; P2.3 rewords the text).
REFUSAL = "its known-bad control passed at this version"
NOT_ADMITTED = f"not admitted: {REFUSAL}"


def _verdict(kind: str, index: int) -> Verdict:
    """One covering verdict of `kind`. `unadmitted` is a refusal as the spine
    mints it from P2.1 (`verdicts._unqualified`): the mark, `unqualified`, beside
    the text — the mark is what reads Gap; the text alone reads as a crash
    (`test_a_refusal_written_only_as_text_reads_errored`)."""
    gate = f"g.{index}{kind}"
    return {
        "pass": Verdict(gate=gate, claims=["C1"], passed=True, detail="0.4 mm"),
        "fail": Verdict(gate=gate, claims=["C1"], passed=False, detail="0.7 mm"),
        "skipped": Verdict(gate=gate, claims=["C1"], skipped=True,
                           skip_reason="requires x (not on PATH)"),
        "error": Verdict(gate=gate, claims=["C1"], error="ZeroDivisionError: x"),
        "unadmitted": Verdict(gate=gate, claims=["C1"], error=NOT_ADMITTED,
                              unqualified=REFUSAL),
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


#: The table as `860ffa6` read it, written out — never computed: a table derived
#: from the code under test agrees with it by construction
#: (`test_renderers.EXPECTED`). Read against that ladder: an error or a refusal
#: of admission read fail (rung 4), alone or beside anything; a pass beside a
#: skip or an unrun gate read pass (S-03); only all-skipped read blocked. Kept
#: literal, never edited again: `StatusesMoveOnlyTowardUnresolved` measures every
#: move of P2.1's table against it (P2.0's hand-off).
AT_860FFA6: dict[str, str] = {
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

#: GLOSSARY §3's composition (P2.1-D2), every row — typed from the glossary,
#: never computed. First match wins: Failing (a fail, any order), Skipped (an
#: error or a skip, even beside a pass), Gap (a refused evaluator, even beside a
#: pass; no evaluator at all), Open (an unrun gate, even beside a pass), Stale,
#: Checked. Thirty rows moved from `AT_860FFA6`, none toward resolved
#: (`StatusesMoveOnlyTowardUnresolved` holds that, with each one's cause).
EXPECTED: dict[str, str] = {
    "-": "unclaimed",
    "- unrun": "pending",
    "pass": "pass",
    "pass stale": "stale",
    "pass unrun": "pending",
    "pass unrun stale": "pending",
    "fail": "fail",
    "fail unrun": "fail",
    "skipped": "blocked",
    "skipped unrun": "blocked",
    "error": "blocked",
    "error unrun": "blocked",
    "unadmitted": "unclaimed",
    "unadmitted unrun": "unclaimed",
    "pass+pass": "pass",
    "pass+pass stale": "stale",
    "pass+pass unrun": "pending",
    "pass+pass unrun stale": "pending",
    "pass+fail": "fail",
    "pass+fail stale": "fail",
    "pass+fail unrun": "fail",
    "pass+fail unrun stale": "fail",
    "pass+skipped": "blocked",
    "pass+skipped stale": "blocked",
    "pass+skipped unrun": "blocked",
    "pass+skipped unrun stale": "blocked",
    "pass+error": "blocked",
    "pass+error stale": "blocked",
    "pass+error unrun": "blocked",
    "pass+error unrun stale": "blocked",
    "pass+unadmitted": "unclaimed",
    "pass+unadmitted stale": "unclaimed",
    "pass+unadmitted unrun": "unclaimed",
    "pass+unadmitted unrun stale": "unclaimed",
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
    "skipped+error": "blocked",
    "skipped+error unrun": "blocked",
    "skipped+unadmitted": "blocked",
    "skipped+unadmitted unrun": "blocked",
    "error+error": "blocked",
    "error+error unrun": "blocked",
    "error+unadmitted": "blocked",
    "error+unadmitted unrun": "blocked",
    "unadmitted+unadmitted": "unclaimed",
    "unadmitted+unadmitted unrun": "unclaimed",
}

#: The physical and assumption rows as `860ffa6` read them — a physical claim
#: read no evaluator, an assumption read asserted whatever covered it — written
#: down before P2.1 moved them (R-1), literal, never edited again. The owner
#: rows have no `860ffa6` reading of their own (no `Claim.owner` existed): every
#: assumption read asserted, so each is listed at that value, the base its move
#: is measured from.
AT_860FFA6_OTHER_KINDS: dict[str, str] = {
    "physical, no result": "unverified",
    "physical, a pass recorded": "verified",
    "physical, a fail recorded": "refuted",
    "physical, no result, a covering gate failed": "unverified",
    "physical, no result, a covering gate errored": "unverified",
    "physical, no result, a covering gate skipped": "unverified",
    "physical, no result, a covering gate unqualified": "unverified",
    "physical, no result, a covering gate unrun": "unverified",
    "physical, no result, a covering pass invalidated": "unverified",
    "physical, a pass recorded, a covering gate skipped": "verified",
    "physical, a pass recorded, a covering gate errored": "verified",
    "physical, a pass recorded, a covering pass invalidated": "verified",
    "physical, a fail recorded, a covering gate errored": "refuted",
    "assumption": "asserted",
    "assumption, a covering gate failed": "asserted",
    "assumption, a covering gate skipped": "asserted",
    "assumption, a covering gate unrun": "asserted",
    "assumption, a covering gate passed": "asserted",
    "assumption, an owner in the file only": "asserted",
    "assumption, attributed": "asserted",
    "assumption, attributed under another name": "asserted",
    "assumption, attributed, its rationale changed since": "asserted",
    "assumption, attributed, no rationale": "asserted",
    "assumption, attributed, a covering gate failed": "asserted",
    "assumption, attributed, a covering gate skipped": "asserted",
    "assumption, attributed, a covering gate unrun": "asserted",
    "assumption, attributed, a covering gate passed": "asserted",
}

#: The physical and assumption rows under GLOSSARY §3's composition (P2.1-D2):
#: a physical claim composes its automated evaluators first (S-49) — a covering
#: fail is Failing, an error or skip Skipped, a refusal Gap, an unrun gate Open,
#: an invalidated pass Stale — and only then reads its result; an assumption is
#: Assumed only with a reason and an owner the channel attributed (D8), and its
#: covering verdicts still count against it (R-3), while a pass never makes it
#: Checked.
EXPECTED_OTHER_KINDS: dict[str, str] = {
    "physical, no result": "unverified",
    "physical, a pass recorded": "verified",
    "physical, a fail recorded": "refuted",
    "physical, no result, a covering gate failed": "fail",
    "physical, no result, a covering gate errored": "blocked",
    "physical, no result, a covering gate skipped": "blocked",
    "physical, no result, a covering gate unqualified": "unclaimed",
    "physical, no result, a covering gate unrun": "pending",
    "physical, no result, a covering pass invalidated": "stale",
    "physical, a pass recorded, a covering gate skipped": "blocked",
    "physical, a pass recorded, a covering gate errored": "blocked",
    "physical, a pass recorded, a covering pass invalidated": "stale",
    "physical, a fail recorded, a covering gate errored": "refuted",
    "assumption": "unclaimed",
    "assumption, a covering gate failed": "fail",
    "assumption, a covering gate skipped": "blocked",
    "assumption, a covering gate unrun": "unclaimed",
    "assumption, a covering gate passed": "unclaimed",
    "assumption, an owner in the file only": "unclaimed",
    "assumption, attributed": "asserted",
    "assumption, attributed under another name": "unclaimed",
    "assumption, attributed, its rationale changed since": "unclaimed",
    "assumption, attributed, no rationale": "unclaimed",
    "assumption, attributed, a covering gate failed": "fail",
    "assumption, attributed, a covering gate skipped": "blocked",
    "assumption, attributed, a covering gate unrun": "pending",
    "assumption, attributed, a covering gate passed": "asserted",
}

#: The owner the attributed rows name, and the reason it was recorded against.
OWNER, REASON = "Sam", "the load is static by construction"


def other_kinds_cases() -> dict[str, tuple[Claim, list[Verdict], set[str], dict]]:
    """name -> (claim, verdicts, stale gates, owners): the rows the other-kinds
    tables name. `owners` is what the signing channel would hand `compose`
    (`claims.Attribution`); nothing in P2.1 produces one, so only these rows
    reach Assumed."""
    physical, assumption = ClaimKind.PHYSICAL, ClaimKind.ASSUMPTION
    passed, failed = PhysicalResult(passed=True), PhysicalResult(passed=False)
    signed = {"C1": claims_mod.Attribution(OWNER, REASON)}

    def one(kind: str) -> list[Verdict]:
        return [_verdict(kind, 0)]

    def owned(gates: list[str] | None = None, **kw: Any) -> Claim:
        fields = dict(owner=OWNER, rationale=REASON)
        fields.update(kw)
        return _claim(list(gates or []), assumption, **fields)

    return {
        "physical, no result": (_claim([], physical), [], set(), {}),
        "physical, a pass recorded": (_claim([], physical, physical_result=passed), [], set(),
                                      {}),
        "physical, a fail recorded": (_claim([], physical, physical_result=failed), [], set(),
                                      {}),
        "physical, no result, a covering gate failed": (
            _claim(["g.0fail"], physical), one("fail"), set(), {}),
        "physical, no result, a covering gate errored": (
            _claim(["g.0error"], physical), one("error"), set(), {}),
        "physical, no result, a covering gate skipped": (
            _claim(["g.0skipped"], physical), one("skipped"), set(), {}),
        "physical, no result, a covering gate unqualified": (
            _claim(["g.0unadmitted"], physical), one("unadmitted"), set(), {}),
        "physical, no result, a covering gate unrun": (
            _claim(["g.unrun"], physical), [], set(), {}),
        "physical, no result, a covering pass invalidated": (
            _claim(["g.0pass"], physical), one("pass"), {"g.0pass"}, {}),
        "physical, a pass recorded, a covering gate skipped": (
            _claim(["g.0skipped"], physical, physical_result=passed), one("skipped"), set(),
            {}),
        "physical, a pass recorded, a covering gate errored": (
            _claim(["g.0error"], physical, physical_result=passed), one("error"), set(), {}),
        "physical, a pass recorded, a covering pass invalidated": (
            _claim(["g.0pass"], physical, physical_result=passed), one("pass"), {"g.0pass"},
            {}),
        "physical, a fail recorded, a covering gate errored": (
            _claim(["g.0error"], physical, physical_result=failed), one("error"), set(), {}),
        "assumption": (_claim([], assumption), [], set(), {}),
        "assumption, a covering gate failed": (_claim(["g.0fail"], assumption), one("fail"),
                                               set(), {}),
        "assumption, a covering gate skipped": (_claim(["g.0skipped"], assumption),
                                                one("skipped"), set(), {}),
        "assumption, a covering gate unrun": (_claim(["g.unrun"], assumption), [], set(), {}),
        "assumption, a covering gate passed": (_claim(["g.0pass"], assumption), one("pass"),
                                               set(), {}),
        "assumption, an owner in the file only": (owned(), [], set(), {}),
        "assumption, attributed": (owned(), [], set(), signed),
        "assumption, attributed under another name": (owned(owner="Alex"), [], set(), signed),
        "assumption, attributed, its rationale changed since": (
            owned(rationale="the load is static, mostly"), [], set(), signed),
        "assumption, attributed, no rationale": (
            owned(rationale=""), [], set(), {"C1": claims_mod.Attribution(OWNER, "")}),
        "assumption, attributed, a covering gate failed": (owned(["g.0fail"]), one("fail"),
                                                           set(), signed),
        "assumption, attributed, a covering gate skipped": (owned(["g.0skipped"]),
                                                            one("skipped"), set(), signed),
        "assumption, attributed, a covering gate unrun": (owned(["g.unrun"]), [], set(),
                                                          signed),
        "assumption, attributed, a covering gate passed": (owned(["g.0pass"]), one("pass"),
                                                           set(), signed),
    }


def other_kinds_table(resolve: Callable[..., ClaimStatus] | None = None) -> dict[str, str]:
    resolve = resolve or claims_mod.resolve_status
    return {name: ClaimStatus(resolve(claim, verdicts, stale_gates=stale,
                                      owners=owners)).value
            for name, (claim, verdicts, stale, owners) in other_kinds_cases().items()}


class StatusTable(unittest.TestCase):
    """`resolve_status`, every row: GLOSSARY §3's composition from P2.1 (V1),
    the table rewritten in the open (R-6, P2.0's hand-off), `AT_860FFA6` kept."""

    def test_the_automated_rows(self):
        table = resolved_table()
        self.assertEqual(len(table), 54)
        self.assertEqual(sorted(table), sorted(EXPECTED), "a row was added or lost")
        for key, want in EXPECTED.items():
            with self.subTest(row=key):
                self.assertEqual(table[key], want)

    def test_the_physical_and_assumption_rows(self):
        table = other_kinds_table()
        self.assertEqual(sorted(table), sorted(EXPECTED_OTHER_KINDS), "a row was added or lost")
        for key, want in EXPECTED_OTHER_KINDS.items():
            with self.subTest(row=key):
                self.assertEqual(table[key], want)

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

    def test_a_refusal_written_only_as_text_reads_errored(self):
        """Degrade-closed (P2.1-D4): only the spine's mark, `Verdict.unqualified`,
        reads Gap. A verdict carrying the refusal's TEXT alone — a gate that
        worded its own crash so, a record from a spine without the field — is a
        crash: Skipped, cause errored, never the quieter Gap."""
        text_only = Verdict(gate="g.0", claims=["C1"], error=NOT_ADMITTED)
        found = claims_mod.compose(_claim(["g.0"]), [text_only])
        self.assertEqual((found.status, found.cause),
                         (ClaimStatus.BLOCKED, claims_mod.ClaimCause.ERRORED))
        marked = _verdict("unadmitted", 0)
        found = claims_mod.compose(_claim([marked.gate]), [marked])
        self.assertEqual((found.status, found.cause),
                         (ClaimStatus.UNCLAIMED, claims_mod.ClaimCause.UNQUALIFIED))

    def test_a_pass_beside_known_good_not_run_reads_gap(self):
        """P2.3's future input, driven now (P2.1-D7): GLOSSARY §2's *known-bad
        shown* evaluator — known-bad control fails, known-good never run — is
        not qualified, and its claim reads Gap even beside a pass. P2.3 only
        flips the producer (`RejectOnlyStillCounts` is the other half)."""
        shown = Verdict(gate="g.1", claims=["C1"], passed=True,
                        unqualified="known-good not run")
        self.assertFalse(shown.ok)
        for verdicts in ([_verdict("pass", 0), shown], [shown, _verdict("pass", 0)]):
            with self.subTest(first=verdicts[0].gate):
                found = claims_mod.compose(_claim(["g.0pass", "g.1"]), verdicts)
                self.assertEqual((found.status, found.cause, found.cites),
                                 (ClaimStatus.UNCLAIMED, claims_mod.ClaimCause.UNQUALIFIED,
                                  ("g.1",)))


# --------------------------------------------------------------------------- #
# R-8's P2 oracle over the resolver: statuses move only toward unresolved
# --------------------------------------------------------------------------- #
#: Rank by GLOSSARY §3's *resolved*: 0 resolved (Checked), 1 unresolved but not
#: stopping `check` (Pending build, Assumed), 2 stopping it (BLOCKING_STATUSES,
#: typed here). PLAN-v0.14 §1.4: R-8's P2 oracle is "statuses move only in the
#: blocking direction", and *blocking* there is GLOSSARY's *unresolved*.
RANK: dict[str, int] = {
    "pass": 0, "verified": 0, "asserted": 1, "unverified": 1,
    "fail": 2, "refuted": 2, "stale": 2, "unclaimed": 2, "blocked": 2, "pending": 2,
}

#: Which statuses each cause may come with — a status and a cause that disagree
#: is a renderer's second story (`compose` returns both).
CAUSE_STATUS: dict[str, set[str]] = {
    "failed": {"fail"}, "physical-fail": {"refuted"},
    "errored": {"blocked"}, "skipped": {"blocked"},
    "unqualified": {"unclaimed"}, "no-evaluator": {"unclaimed"}, "no-owner": {"unclaimed"},
    "owner-unattributed": {"unclaimed"}, "no-reason": {"unclaimed"},
    "unrun": {"pending"}, "invalidated": {"stale"}, "no-article": {"unverified"},
    "owned": {"asserted"}, "physical-pass": {"verified"}, "checked": {"pass"},
}

#: The causes a moved row may name, by what the row holds (P2.1-D20): an error
#: may move it to errored, a refusal to unqualified, a skip to skipped, an unrun
#: gate to unrun, an invalidated pass to invalidated, a fail to failed, an
#: assumption to the owner causes. Nothing else may move a row.
COMPONENT_CAUSES: dict[str, set[str]] = {
    "error": {"errored"}, "errored": {"errored"}, "unadmitted": {"unqualified"},
    "unqualified": {"unqualified"}, "skipped": {"skipped"}, "unrun": {"unrun"},
    "stale": {"invalidated"}, "invalidated": {"invalidated"}, "fail": {"failed"},
    "failed": {"failed"}, "assumption": {"no-owner", "owner-unattributed", "no-reason"},
}


def allowed_causes(row: str) -> set[str]:
    """The causes `row`'s move may name, read off its key's words."""
    found: set[str] = set()
    for word in re.split(r"[+ ,]+", row):
        found |= COMPONENT_CAUSES.get(word, set())
    return found


def move_problems(before: dict[str, str], after: dict[str, str],
                  causes: dict[str, str]) -> list[str]:
    """Every row of `after` that moved toward resolved, moved for a cause its
    row does not hold, or names a cause its status does not admit."""
    out: list[str] = []
    for row, now in after.items():
        cause = causes.get(row, "")
        if now not in CAUSE_STATUS.get(cause, set()):
            out.append(f"{row}: reads {now} with cause {cause!r} (status and cause disagree)")
        was = before.get(row)
        if was is None:
            out.append(f"{row}: no base reading")
            continue
        if was == now:
            continue
        if RANK[now] < RANK[was]:
            out.append(f"{row}: {was} -> {now} moves toward resolved")
        if cause not in allowed_causes(row):
            out.append(f"{row}: {was} -> {now} for {cause!r}, which the row does not hold")
    return out


def causes_of() -> dict[str, str]:
    """Every row's cause, as `compose` gives it, automated and other kinds."""
    out: dict[str, str] = {}
    for size in (0, 1, 2):
        for combo in itertools.combinations_with_replacement(KINDS, size):
            for unrun in (False, True):
                for stale in ((False, True) if "pass" in combo else (False,)):
                    verdicts = [_verdict(kind, i) for i, kind in enumerate(combo)]
                    gates = [v.gate for v in verdicts] + (["g.unrun"] if unrun else [])
                    stale_gates = ({next(v.gate for v in verdicts if v.outcome == "pass")}
                                   if stale else set())
                    found = claims_mod.compose(_claim(gates), verdicts,
                                               stale_gates=stale_gates)
                    out[row_key(combo, unrun, stale)] = str(found.cause.value)
    for name, (claim, verdicts, stale, owners) in other_kinds_cases().items():
        out[name] = str(claims_mod.compose(claim, verdicts, stale_gates=stale,
                                           owners=owners).cause.value)
    return out


class StatusesMoveOnlyTowardUnresolved(unittest.TestCase):
    """(V, R-8 for P2) Every row P2.1 moved moved toward GLOSSARY's *unresolved*
    and for a cause the row holds; every row's status and cause agree. Written
    once here over the resolver, and once over the corpus in
    `tests/oracle/r8_statuses.py --toward-unresolved` (P2.1-D20: an oracle that
    imports the module under test relaxes with it, so the two are separate)."""

    def test_every_row_moved_toward_unresolved_for_its_own_cause(self):
        before = {**AT_860FFA6, **AT_860FFA6_OTHER_KINDS}
        after = {**resolved_table(), **other_kinds_table()}
        self.assertEqual(sorted(after), sorted({**EXPECTED, **EXPECTED_OTHER_KINDS}))
        self.assertEqual(move_problems(before, after, causes_of()), [])
        moved = [row for row in after if after[row] != before[row]]
        self.assertGreaterEqual(len([r for r in moved if r in AT_860FFA6]), 30, moved)

    def test_a_generous_move_is_caught(self):
        """Planted: an assumption reads verified — toward resolved, for a cause
        its row does not hold. (Pass beside a skip read back as pass is no move
        from `AT_860FFA6` at all: `StatusTable` holds that row, this oracle the
        direction of the rows that moved.)"""
        before = {**AT_860FFA6, **AT_860FFA6_OTHER_KINDS}
        after = {**EXPECTED, **EXPECTED_OTHER_KINDS, "assumption": "verified"}
        causes = dict(causes_of(), assumption="physical-pass")
        found = move_problems(before, after, causes)
        self.assertIn("assumption: asserted -> verified moves toward resolved", found)
        self.assertTrue(any(p.startswith("assumption: asserted -> verified for") for p in found),
                        found)

    def test_a_status_its_cause_does_not_admit_is_caught(self):
        """Planted: pass beside a skip reads stale, named by its skip."""
        before = {**AT_860FFA6, **AT_860FFA6_OTHER_KINDS}
        after = {**EXPECTED, **EXPECTED_OTHER_KINDS, "pass+skipped": "stale"}
        found = move_problems(before, after, causes_of())
        self.assertIn("pass+skipped: reads stale with cause 'skipped' (status and cause "
                      "disagree)", found)


class BlockingMembers(unittest.TestCase):
    """(C) The set `check` stops on, written out. P2.1 moves which status a fact
    reads, never this set (its D9): an errored claim blocks because Skipped is
    in it, an unowned assumption because Gap is. A change that dropped one would
    let that whole status through a money boundary."""

    WANT = frozenset({"fail", "stale", "unclaimed", "blocked", "pending", "refuted"})

    def test_the_set_is_exactly_these(self):
        self.assertEqual({s.value for s in BLOCKING_STATUSES}, set(self.WANT))

    def test_blocking_stops_on_one_critical_claim_per_status(self):
        for status in ClaimStatus:
            with self.subTest(status=status.value):
                claim = _claim([])
                ledger = Ledger(meta=ProjectMeta(name="t", revision="v0.1"), claims=[claim])
                with mock.patch.object(claims_mod, "statuses",
                                       lambda *_a, _s=status, **_k: {"C1": _s}):
                    found = claims_mod.blocking(ledger, None)
                self.assertEqual([(c.id, s) for c, s in found],
                                 [("C1", status)] if status.value in self.WANT else [])


class RejectOnlyStillCounts(_env.EnvCase):
    """(C) A project evaluator admitted reject-only — its known-bad control fails
    at its version, its known-good control has never run — still counts in P2.1:
    its pass reads `pass`. GLOSSARY §2 calls it *known-bad shown* and says it is
    not qualified, so its claim reads Gap, `unqualified: known-good not run`; that
    lands with the known-good half (P2.3), which flips this test in the open.
    Every project gate is reject-only today, so flipping it here would turn every
    bracket claim into a Gap and move it twice (P2.1-D7)."""

    def test_the_bracket_passes_on_reject_only_controls(self):
        root = _projects.bracket_copy(os.path.join(self.tmp(), "bracket"), migrated=True)
        statuses = json.loads(_env.atompipe(["status", "--json"], cwd=root).stdout)["claims"]
        self.assertEqual(statuses.get("C2"), "pass", statuses)
        admitted = set()
        for gate in C2_PASSING:
            directory = os.path.join(root, ".atompipe", "verdicts", gate)
            for name in os.listdir(directory):
                if name.startswith("control-"):
                    with open(os.path.join(directory, name), encoding="utf-8") as fh:
                        admitted.add(json.load(fh)["admitted"])
        self.assertEqual(admitted, {"reject-only"})


class ReportSectionOrder(unittest.TestCase):
    """(C) The readiness report's sections, in the order a sceptical reader needs
    them, in GLOSSARY §9's headings from P2.1 (R-6: words only, the order
    unchanged; `SECTION_PROVEN` keeps its text until A-11) — so P2.5's
    REPORT.md changes it in the open."""

    WANT = ["## What is PROVEN", "## Pending build", "## Gaps", "## Assumed",
            "## Failing, stale, skipped or open", "## Reproduce"]

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


def overclaims(compose: Callable[..., Any] | None = None) -> list[str]:
    """Where a pass beside a not-admitted evaluator reads Checked: the status,
    the PROVEN section, the JUnit claim case — in both orders. `compose`, when
    given, stands in for `claims.compose`, the one producer every reader asks
    from P2.1 (R-6: the planted resolver moved from `resolve_status` with it)."""
    specs = _Specs(GateSpec(id=g, claims=["C1"], tier=Tier.INSTANT,
                            negative_control=NegativeControl(fixture="x:y"))
                   for g in ("g.0pass", "g.1unadmitted"))
    passing, refused = _verdict("pass", 0), _verdict("unadmitted", 1)
    out: list[str] = []
    patch = (mock.patch.object(claims_mod, "compose", compose) if compose
             else mock.patch.object(claims_mod, "compose", claims_mod.compose))
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


# --------------------------------------------------------------------------- #
# end to end: an evaluator refused at its first check, beside a pass
# --------------------------------------------------------------------------- #
LOGGER = "probe.logger"

#: A project evaluator on C2 (`strength`) that passes everything, its own
#: known-bad control included: `check` refuses it at its first run.
LOGGER_GATE = f'''\
"""Planted by tests/test_status_table.py: a logger, refused at its first check."""
from atompipe.gates import gate
from atompipe.models import NegativeControl, Verdict


@gate(id="{LOGGER}", claims=["strength", "logger-only"],
      negative_control=NegativeControl(fixture="selftest/probe_bad.py:thin"))
def logs(ctx):
    return Verdict(gate="{LOGGER}", passed=True, detail="always")
'''

LOGGER_FIXTURE = '''\
"""Planted by tests/test_status_table.py: the logger's known-bad control."""
import dataclasses

from atompipe.models import Ledger


def thin(ctx):
    return dataclasses.replace(ctx, params={"config": {"thickness": 1.0}},
                               ledger=Ledger(), extra={})
'''

#: The bracket's own evaluators of C2, which pass on the live design.
C2_PASSING = ("bracket.bending_stress", "bracket.model_validity")

#: C1 (failing), C6 (an assumption nobody owns, a Gap from P2.1) and C7 (no
#: evaluator) made not critical, so the refused logger is the only thing between
#: the project and `ready`: an overclaim shows as `ready: true`, not only as one
#: status among other blockers. (C6 joined in P2.1, R-6: the fixture's own
#: premise, not a property it checks.)
NOT_CRITICAL = ("C1", "C6", "C7")

#: A claim the logger alone covers — not required, so it changes nothing the
#: rest of this world relies on: a refused evaluator with no pass beside it
#: reads Gap, never Open (V6).
ALONE = "Q1"
ALONE_RECORD = {"statement": "the logged quantity holds", "kind": "measurable",
                "tags": ["logger-only"], "critical": False,
                "acceptance": {"quantity": "q", "comparator": "<=", "limit": 1.0,
                               "units": "mm"}}

_RUNS = (("check", ["check", "--junit"]), ("check.json", ["check", "--json"]),
         ("status.json", ["status", "--json"]),
         ("report.json", ["report", "--json"]),
         ("status", ["status"]), ("report", ["report"]), ("claim.list", ["claim", "list"]),
         ("why", ["why", "C2"]), ("site.init", ["site", "init"]),
         ("site.build", ["site", "build"]))


class _Refused(NamedTuple):
    out: dict[str, Any]          # key -> CompletedProcess
    junit: str
    state: dict
    last_check: dict = {}


_REFUSED: list[_Refused] = []


def _refused_project() -> _Refused:
    """The bracket, migrated, with the logger beside C2's passing evaluators;
    every command run once and cached for the module."""
    if _REFUSED:
        return _REFUSED[0]
    tmp = tempfile.mkdtemp(prefix="atompipe-refused-")
    unittest.addModuleCleanup(_env._rmtree, tmp)
    root = _projects.bracket_copy(os.path.join(tmp, "bracket"), migrated=True)
    home = os.path.join(tmp, "home")
    os.makedirs(home)
    with open(os.path.join(root, "gates", "zz_logger.py"), "w", encoding="utf-8") as fh:
        fh.write(LOGGER_GATE)
    with open(os.path.join(root, "selftest", "probe_bad.py"), "w", encoding="utf-8") as fh:
        fh.write(LOGGER_FIXTURE)
    for cid in NOT_CRITICAL:
        path = os.path.join(root, "claims", f"{cid}.json")
        with open(path, encoding="utf-8") as fh:
            record = json.load(fh)
        record["critical"] = False
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(record, fh, indent=2)
    with open(os.path.join(root, "claims", f"{ALONE}.json"), "w", encoding="utf-8") as fh:
        json.dump(ALONE_RECORD, fh, indent=2)
    out = {key: _env.atompipe(argv, cwd=root, home=home) for key, argv in _RUNS}
    for key, proc in out.items():
        want = 1 if key.startswith("check") else 0
        if proc.returncode != want:
            raise AssertionError(f"`atompipe {key}` exited {proc.returncode}, not {want}:\n"
                                 f"{proc.stdout[-2000:]}\n{proc.stderr[-2000:]}")
    with open(os.path.join(root, ".atompipe", "out", "junit.xml"), encoding="utf-8") as fh:
        junit = fh.read()
    with open(os.path.join(root, "site", "data", "state.json"), encoding="utf-8") as fh:
        state = json.load(fh)
    with open(os.path.join(root, ".atompipe", "cache", "last_check.json"),
              encoding="utf-8") as fh:
        last_check = json.load(fh)
    run = _Refused(out, junit, state, last_check)
    _check_the_refusal(run)
    _REFUSED.append(run)
    return run


def _check_the_refusal(run: _Refused) -> None:
    """The fixture's precondition: `check` refused the logger at this version
    (its JUnit testcase an error that says so) and C2's own evaluators passed.
    A rotted fixture fails here, naming what moved, instead of passing every
    property on a claim nothing refused."""
    root = ET.fromstring(run.junit)
    gates = {case.get("name"): [k for k in case if k.tag != "properties"]
             for case in root.iterfind("testsuite[@name='gates']/testcase")}
    refusal = gates.get(LOGGER) or []
    if not (refusal and refusal[0].tag == "error"
            and "not admitted:" in (refusal[0].get("message") or "")):
        raise AssertionError(f"fixture rotted: {LOGGER} was not refused — {refusal}")
    for gate in C2_PASSING:
        if gates.get(gate) != []:
            raise AssertionError(f"fixture rotted: {gate} did not pass — {gates.get(gate)}")


_CHECKED = ("pass", "verified", "checked")


class _Stdout(NamedTuple):
    """A planted command's output: all `overclaims_end_to_end` reads of one."""
    stdout: str


def overclaims_end_to_end(run: _Refused) -> list[str]:
    """Where a reader of the refused project reads C2 as checked or the project
    as ready. Each problem is a fixed string, so the ratchet names it exactly."""
    out: list[str] = []
    check = run.out["check"].stdout.splitlines()
    blocking = check[next((i for i, ln in enumerate(check) if ln.startswith("BLOCKING")),
                          len(check)):]
    if not any(re.match(r"^\[[^\]]+\] C2 ", ln) for ln in blocking):
        out.append("check: C2 is not in BLOCKING")
    case = ET.fromstring(run.junit).find("testsuite[@name='claims.critical']/testcase[@name='C2']")
    if case is None or not [k for k in case if k.tag in ("failure", "error")]:
        out.append("check.junit: C2 is not red")
    doc = json.loads(run.out["status.json"].stdout)
    summary = doc.get("summary") or {}
    if (doc.get("claims") or {}).get("C2") in _CHECKED:
        out.append("status.json: C2 reads checked")
    if summary.get("ready") is not False:
        out.append("status.json: ready")
    if "C2" not in (summary.get("blocking_ids") or ()):
        out.append("status.json: C2 is not blocking")
    status = run.out["status"].stdout.splitlines()
    if "not ready" not in (status[1] if len(status) > 1 else "").lower():
        out.append("status: the readiness sentence says ready")
    rows = [ln for ln in status if re.match(r"^\[[^\]]+\] C2 ", ln)]
    if not rows or any(ln.startswith("[ok") for ln in rows):
        out.append("status: no unsettled row for C2")
    report = run.out["report"].stdout
    sentence = next((ln for ln in report.splitlines() if ln.startswith("**")), "")
    if "not ready" not in sentence.lower():
        out.append("report: the readiness sentence says ready")
    proven = report.split("## What is PROVEN", 1)[-1].split("\n## ", 1)[0]
    if re.search(r"^\| \*\*C2\*\*", proven, re.M):
        out.append("report: C2 under PROVEN")
    listed = [ln for ln in run.out["claim.list"].stdout.splitlines()
              if re.match(r"^\[[^\]]+\] C2 ", ln)]
    if not listed or listed[0].startswith("[ok"):
        out.append("claim.list: C2 tagged ok")
    why = [ln for ln in run.out["why"].stdout.splitlines() if f"] {LOGGER}" in ln]
    if not why:
        out.append(f"why: no row for {LOGGER}")
    elif "never run" in why[0] or why[0].lstrip().startswith(("[ok", "[ --")):
        out.append(f"why: {LOGGER} reads as never run")
    claims = {row.get("id"): row for row in run.state.get("claims") or ()}
    if (claims.get("C2") or {}).get("status") in _CHECKED:
        out.append("site: C2 reads checked")
    if (run.state.get("readiness") or {}).get("ready") is not False:
        out.append("site: ready")
    return out


class UnqualifiedBesideAPassIsNeverChecked(unittest.TestCase):
    """(4) A claim never reads Checked while one of its evaluators is not
    admitted, whatever else passed beside it — and from P2.1 it reads Gap,
    `unqualified:`, on every channel, never errored (P2.0 D-8).

    What slipped through until P2.1: `verdicts.resolve` asked admission only
    over a Fresh cache entry, and a gate refused at its first check has none,
    so every reader after `check` read it "never run" and the pass beside it as
    pass — eleven overclaims, named in P2.0's ratchet (`KNOWN_OVERCLAIMS`, gone
    with this change): `status --json` ready, the report's C2 under PROVEN, the
    page's C2 pass. `resolve` now says the refusal for a gate with no entry, or
    a stale one (P2.1-D5)."""

    def test_a_pass_beside_an_unadmitted_evaluator_is_never_checked(self):
        self.assertEqual(overclaims(), [])

    def test_a_resolver_that_lets_the_pass_win_is_caught(self):
        """Planted: a composition that drops the refused evaluator — its verdict
        and its gate, "it does not count". The status reads pass, and the
        checker says so; the report and JUnit hold anyway, by their own
        cross-check (D18): the refused gate still covers the claim, so the
        `pass` is a disagreement, outside the checked section and red. (Under
        P2.0 the same plant reached all three; dropping the verdict alone now
        leaves the gate unrun, and Open is not Checked either.)"""
        real = claims_mod.compose

        def drops_the_unadmitted(claim, verdicts, **kw):
            verdicts = list(verdicts)
            refused = {v.gate for v in verdicts if str(v.error).startswith("not admitted:")}
            claim = dataclasses.replace(claim, gates=[g for g in claim.gates
                                                      if g not in refused])
            return real(claim, [v for v in verdicts if v.gate not in refused], **kw)

        found = overclaims(drops_the_unadmitted)
        self.assertEqual(found, ["g.0pass first: resolves pass",
                                 "g.1unadmitted first: resolves pass"])

    def test_a_refused_evaluator_beside_a_pass_end_to_end(self):
        """Through the commands: `check` refuses the logger and lists C2; no
        reader after it may read C2 as checked or the project as ready."""
        self.assertEqual(overclaims_end_to_end(_refused_project()), [])

    def test_the_end_to_end_checks_refuse_what_they_forbid(self):
        """Planted violators: a BLOCKING list without C2, a JUnit claim case with
        no child, and today's `860ffa6` readers — `status --json` reading C2
        pass and ready, the logger never run in `why`."""
        run = _refused_project()
        stdout = "\n".join(ln for ln in run.out["check"].stdout.splitlines()
                           if not re.match(r"^\[[^\]]+\] C2 ", ln))
        quiet = run._replace(out=dict(run.out, check=_Stdout(stdout)))
        self.assertIn("check: C2 is not in BLOCKING", overclaims_end_to_end(quiet))
        junit = re.sub(r'(<testcase[^>]*name="C2"[^>]*?)>.*?</testcase>', r"\1 />",
                       run.junit, count=1, flags=re.S)
        self.assertNotEqual(junit, run.junit)
        self.assertIn("check.junit: C2 is not red",
                      overclaims_end_to_end(run._replace(junit=junit)))
        doc = json.loads(run.out["status.json"].stdout)
        doc["claims"]["C2"] = "pass"
        doc["summary"]["ready"] = True
        doc["summary"]["blocking_ids"] = []
        planted = run._replace(out=dict(run.out, **{"status.json": _Stdout(json.dumps(doc))}))
        self.assertEqual({p for p in overclaims_end_to_end(planted)
                          if p.startswith("status.json")},
                         {"status.json: C2 reads checked", "status.json: ready",
                          "status.json: C2 is not blocking"})
        why = run.out["why"].stdout.replace(f"] {LOGGER} : ", "] ").replace(
            "[ERR ]", f"[ -- ] {LOGGER} : never run |")
        self.assertIn(f"why: {LOGGER} reads as never run",
                      overclaims_end_to_end(run._replace(out=dict(run.out,
                                                                  why=_Stdout(why)))))


def refusal_problems(run: _Refused) -> list[str]:
    """What `verdicts.resolve` must say for a gate refused with no cache entry
    (P2.1-D5), as every reader after `check` sees it: a `never` row, admission
    `not-admitted`, an effective verdict marked unqualified; and the claim the
    logger covers alone, Q1, Gap with its reason, never Open."""
    out: list[str] = []
    status = json.loads(run.out["status.json"].stdout)
    row = (status.get("freshness") or {}).get(LOGGER) or {}
    if (row.get("state"), row.get("admission")) != ("never", "not-admitted"):
        out.append(f"freshness: {LOGGER} {row.get('state')}/{row.get('admission')}")
    rows = {r.get("gate"): r for r in json.loads(run.out["report.json"].stdout)["verdicts"]}
    verdict = rows.get(LOGGER)
    if verdict is None:
        out.append(f"report.json: no verdict for {LOGGER}")
    elif not verdict.get("unqualified") or verdict.get("outcome") != "error":
        out.append(f"report.json: {LOGGER} is not marked unqualified")
    view = (status.get("statuses") or {}).get(ALONE) or {}
    if (status["claims"].get(ALONE), view.get("cause")) != ("unclaimed", "unqualified"):
        out.append(f"status.json: {ALONE} {status['claims'].get(ALONE)}/{view.get('cause')}")
    if not str(view.get("reason", "")).startswith(f"unqualified: {LOGGER} : "):
        out.append(f"status.json: {ALONE}'s reason {view.get('reason')!r}")
    return out


def never_errored_problems(run: _Refused) -> list[str]:
    """P2.0 D-8: an unqualified evaluator's claim never reads errored — never the
    `errored:` lead, never the loud `[SKIP ]`, never in an errored count or list,
    never a JUnit `<error>`. On C2 (a pass beside it) and Q1 (alone)."""
    out: list[str] = []
    status = json.loads(run.out["status.json"].stdout)
    for cid in ("C2", ALONE):
        view = (status.get("statuses") or {}).get(cid) or {}
        if view.get("errored") or str(view.get("reason", "")).startswith("errored:"):
            out.append(f"status.json: {cid} reads errored")
        if cid in (status.get("errored") or ()):
            out.append(f"status.json: {cid} is in `errored`")
        for line in run.out["status"].stdout.splitlines():
            if re.match(rf"^\[SKIP \] {cid} ", line) or re.match(
                    rf"^\[.{{5}}\] {cid} .* — errored:", line):
                out.append(f"status: {cid} reads errored")
    if status["summary"].get("errored"):
        out.append(f"status.json: summary.errored = {status['summary']['errored']}")
    case = ET.fromstring(run.junit).find("testsuite[@name='claims.critical']/testcase[@name='C2']")
    kids = [k.tag for k in case] if case is not None else []
    if kids != ["failure"]:
        out.append(f"check.junit: C2 is {kids}, not one <failure>")
    return out


class UnqualifiedReadsGap(_env.EnvCase):
    """(V, P2.1-D5) An evaluator refused at its version reads Gap, `unqualified:`,
    alone or beside a pass, with an entry or none, through a model edit — and
    never as errored."""

    def test_resolve_says_a_refusal_with_no_entry(self):
        self.assertEqual(refusal_problems(_refused_project()), [])

    def test_the_refusal_checks_refuse_today(self):
        """Planted: `860ffa6`'s readers, which asked admission only over a Fresh
        entry — the logger a bare `never` row, no verdict, Q1 Open."""
        run = _refused_project()
        status = json.loads(run.out["status.json"].stdout)
        status["freshness"][LOGGER] = {"state": "never", "reasons": [], "admission": None,
                                       "notes": []}
        status["claims"][ALONE] = "pending"
        status["statuses"][ALONE] = {"cause": "unrun", "reason": f"unrun: {LOGGER}"}
        report = json.loads(run.out["report.json"].stdout)
        report["verdicts"] = [r for r in report["verdicts"] if r.get("gate") != LOGGER]
        planted = run._replace(out=dict(run.out, **{
            "status.json": _Stdout(json.dumps(status)),
            "report.json": _Stdout(json.dumps(report))}))
        self.assertEqual(refusal_problems(planted), [
            f"freshness: {LOGGER} never/None", f"report.json: no verdict for {LOGGER}",
            f"status.json: {ALONE} pending/unrun",
            f"status.json: {ALONE}'s reason 'unrun: {LOGGER}'"])

    def test_an_unqualified_claim_never_reads_errored(self):
        self.assertEqual(never_errored_problems(_refused_project()), [])

    def test_a_compose_that_reads_the_mark_as_a_crash_is_caught(self):
        """Planted in process: a compose that ignores `Verdict.unqualified` — the
        refusal then reads errored (its error text), a Gap made a crash."""
        refused = _verdict("unadmitted", 1)
        claim = _claim(["g.0pass", refused.gate])
        real = claims_mod.compose

        def ignores_the_mark(claim_, verdicts, **kw):
            return real(claim_, [dataclasses.replace(v, unqualified="") for v in verdicts],
                        **kw)

        self.assertFalse(real(claim, [_verdict("pass", 0), refused]).errored)
        with mock.patch.object(claims_mod, "compose", ignores_the_mark):
            found = claims_mod.compositions(Ledger(claims=[claim],
                                                   verdicts=[_verdict("pass", 0), refused]))
        self.assertTrue(found["C1"].errored)

    def test_a_refusal_outlives_a_model_edit(self):
        """Rung 4 (P2.1-D5): an evaluator admitted once and cached, then refused
        at its version, keeps reading Gap after the model edit that makes its
        entry stale — never Stale, whose reason says "run check" to a reader
        when no rerun answers a refusal. And a control that crashed at this
        version refuses its evaluator the same way (Q8)."""
        run = _outlived_project()
        self.assertEqual(run["status"]["claims"].get("C4"), "unclaimed", run["status"]["claims"])
        view = run["status"]["statuses"]["C4"]
        self.assertEqual(view["cause"], "unqualified", view)
        self.assertTrue(view["reason"].startswith(f"unqualified: {BED_PROBE} : "), view)
        self.assertNotIn(BED_PROBE, run["status"]["stale_gates"])
        self.assertEqual(run["status"]["freshness"][BED_PROBE]["state"], "stale",
                         run["status"]["freshness"][BED_PROBE])
        crash = run["status"]["freshness"][CRASHY]
        self.assertEqual((crash["state"], crash["admission"]), ("never", "not-admitted"), crash)
        self.assertEqual((run["status"]["claims"].get("C7"),
                          run["status"]["statuses"]["C7"]["cause"]),
                         ("unclaimed", "unqualified"))


# --------------------------------------------------------------------------- #
# a refusal through a model edit: an evaluator admitted once, refused later
# --------------------------------------------------------------------------- #
BED_PROBE = "probe.bed"
CRASHY = "probe.crashy_control"

#: A project evaluator on C4 (`bed-fit`) that reads `config.bed_xy` and passes
#: when it is at least 200 mm, and one on C7 (`vibration`) whose known-bad
#: control raises: refused at its first check (Q8).
BED_GATES = f'''\
"""Planted by tests/test_status_table.py: a refusal after a pass, and a crashing control."""
from atompipe.gates import gate
from atompipe.models import NegativeControl, Verdict


@gate(id="{BED_PROBE}", claims=["bed-fit"],
      negative_control=NegativeControl(fixture="selftest/probe_bed.py:small"))
def bed(ctx):
    xy = ctx.params["config"]["bed_xy"]
    return Verdict(gate="{BED_PROBE}", passed=xy >= 200.0, detail=f"bed {{xy}} mm")


@gate(id="{CRASHY}", claims=["vibration"],
      negative_control=NegativeControl(fixture="selftest/probe_bed.py:crashes"))
def crashy(ctx):
    return Verdict(gate="{CRASHY}", passed=True, detail="always")
'''

#: The probe's known-bad control as first written (a 100 mm bed: it fails, so
#: the evaluator is admitted), then edited to a 300 mm bed (it passes its own
#: known-bad input: refused).
BED_FIXTURE = '''\
"""Planted by tests/test_status_table.py: the bed probe's known-bad control."""
import dataclasses

from atompipe.models import Ledger


def small(ctx):
    return dataclasses.replace(ctx, params={{"config": {{"bed_xy": {xy}}}}}, ledger=Ledger(),
                               extra={{}})


def crashes(ctx):
    raise RuntimeError("the control itself is broken")
'''

_OUTLIVED: list[dict] = []


def _outlived_project() -> dict:
    """`check` with the probe admitted (its entry cached), its control edited to
    pass its known-bad input, `check` again (refused), then `bed_xy` moved and
    `status --json` read. Cached for the module."""
    if _OUTLIVED:
        return _OUTLIVED[0]
    tmp = tempfile.mkdtemp(prefix="atompipe-outlived-")
    unittest.addModuleCleanup(_env._rmtree, tmp)
    root = _projects.bracket_copy(os.path.join(tmp, "bracket"), migrated=True)
    home = os.path.join(tmp, "home")
    os.makedirs(home)
    with open(os.path.join(root, "gates", "zz_bed.py"), "w", encoding="utf-8") as fh:
        fh.write(BED_GATES)
    fixture = os.path.join(root, "selftest", "probe_bed.py")
    with open(fixture, "w", encoding="utf-8") as fh:
        fh.write(BED_FIXTURE.format(xy="100.0"))
    first = _env.atompipe(["check", "--json"], cwd=root, home=home)
    rows = {r["gate"]: r for r in json.loads(first.stdout)["verdicts"]}
    if rows.get(BED_PROBE, {}).get("outcome") != "pass":
        raise AssertionError(f"fixture rotted: {BED_PROBE} was not admitted and run — "
                             f"{rows.get(BED_PROBE)}\n{first.stderr[-2000:]}")
    with open(fixture, "w", encoding="utf-8") as fh:
        fh.write(BED_FIXTURE.format(xy="300.0"))
    second = _env.atompipe(["check", "--json"], cwd=root, home=home)
    rows = {r["gate"]: r for r in json.loads(second.stdout)["verdicts"]}
    if not str(rows.get(BED_PROBE, {}).get("error", "")).startswith("not admitted:"):
        raise AssertionError(f"fixture rotted: {BED_PROBE} was not refused — "
                             f"{rows.get(BED_PROBE)}")
    model = os.path.join(root, "model", "bracket.py")
    with open(model, encoding="utf-8") as fh:
        text = fh.read()
    edited, n = re.subn(r"^(    bed_xy: float = )220\.0$", r"\g<1>250.0", text, flags=re.M)
    if n != 1:
        raise AssertionError(f"{model}: expected one `bed_xy: float = 220.0` line, found {n}")
    with open(model, "w", encoding="utf-8") as fh:
        fh.write(edited)
    status = _env.atompipe(["status", "--json"], cwd=root, home=home)
    if status.returncode != 0:
        raise AssertionError(f"status exited {status.returncode}: {status.stderr[-2000:]}")
    run = {"status": json.loads(status.stdout)}
    _OUTLIVED.append(run)
    return run


if __name__ == "__main__":
    unittest.main(verbosity=2)
