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


@gate(id="{LOGGER}", claims=["strength"],
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

#: C1 (failing) and C7 (no evaluator) made not critical, so the refused logger
#: is the only thing between the project and `ready`: an overclaim shows as
#: `ready: true`, not only as one status among other blockers.
NOT_CRITICAL = ("C1", "C7")

_RUNS = (("check", ["check", "--junit"]), ("status.json", ["status", "--json"]),
         ("status", ["status"]), ("report", ["report"]), ("claim.list", ["claim", "list"]),
         ("why", ["why", "C2"]), ("site.init", ["site", "init"]),
         ("site.build", ["site", "build"]))


class _Refused(NamedTuple):
    out: dict[str, Any]          # key -> CompletedProcess
    junit: str
    state: dict


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
    out = {key: _env.atompipe(argv, cwd=root, home=home) for key, argv in _RUNS}
    for key, proc in out.items():
        want = 1 if key == "check" else 0
        if proc.returncode != want:
            raise AssertionError(f"`atompipe {key}` exited {proc.returncode}, not {want}:\n"
                                 f"{proc.stdout[-2000:]}\n{proc.stderr[-2000:]}")
    with open(os.path.join(root, ".atompipe", "out", "junit.xml"), encoding="utf-8") as fh:
        junit = fh.read()
    with open(os.path.join(root, "site", "data", "state.json"), encoding="utf-8") as fh:
        state = json.load(fh)
    run = _Refused(out, junit, state)
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


#: The finding -> the exact problems it names today. May only shrink:
#: `UnqualifiedNotYetRefused` is red when one stops showing, so the change that
#: fixes it deletes it and `UnqualifiedBesideAPassIsNeverChecked` holds it from
#: then on. *Rejected:* an expected failure (R-7: a red test wearing green);
#: fixing `verdicts.resolve` in P2.0, which is tests only (R-1) and whose
#: reading of a refusal Phase 2 rewrites (§1.4: Gap, not Failing) — a fix now
#: would land a status P2 moves again; a test red until P2, which turns every
#: run of the suite red for a known, scheduled change.
KNOWN_OVERCLAIMS: dict[str, tuple[str, ...]] = {
    "`verdicts.resolve` consults admission only over a Fresh entry, and a gate "
    "refused at its first check has none: every reader after `check` reads it never "
    "run, and a pass beside an unrun gate reads pass": (
        "status.json: C2 reads checked", "status.json: ready", "status.json: C2 is not blocking",
        "status: the readiness sentence says ready", "status: no unsettled row for C2",
        "report: the readiness sentence says ready", "report: C2 under PROVEN",
        "claim.list: C2 tagged ok", f"why: {LOGGER} reads as never run",
        "site: C2 reads checked", "site: ready"),
}

_OVERCLAIMS_KNOWN = frozenset(p for ps in KNOWN_OVERCLAIMS.values() for p in ps)


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

    def test_a_refused_evaluator_beside_a_pass_end_to_end(self):
        """Through the commands: `check` refuses the logger and lists C2; no
        reader after it may read C2 as checked or the project as ready, beyond
        what `KNOWN_OVERCLAIMS` names today."""
        found = overclaims_end_to_end(_refused_project())
        self.assertEqual([p for p in found if p not in _OVERCLAIMS_KNOWN], [])

    def test_the_end_to_end_checks_refuse_what_they_forbid(self):
        """`check`'s half holds today, so it has its own planted violators: a
        BLOCKING list without C2, and a JUnit claim case with no child."""
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


class UnqualifiedNotYetRefused(unittest.TestCase):
    """(C) The ratchet: each problem `KNOWN_OVERCLAIMS` names still shows. Red
    when one does not — delete it, and the invariant-4 test holds it."""

    def test_each_known_overclaim_still_shows(self):
        found = set(overclaims_end_to_end(_refused_project()))
        for why, problems in KNOWN_OVERCLAIMS.items():
            for problem in problems:
                with self.subTest(problem=problem):
                    self.assertIn(problem, found, f"no longer shows ({why}) — delete it")


if __name__ == "__main__":
    unittest.main(verbosity=2)
