# SPDX-License-Identifier: Apache-2.0
"""The honesty invariants.

These are not ordinary unit tests. Everything nopekit claims rests on four
properties, and if any of them breaks the tool becomes a machine for laundering
assumption into apparent proof — which is strictly worse than having no tool.

    (a) a SKIPPED gate is never reported as a pass
    (b) an ERRORED gate is never reported as a pass
    (c) the registry refuses a gate with no negative control
    (d) the readiness report never lists an unrun or skipped gate under PROVEN

Each test below tries to VIOLATE one of them. Run with:

    PYTHONPATH=src python3 -m unittest discover -s tests -v
"""
from __future__ import annotations

import dataclasses
import fractions
import itertools
import json
import os
import re
import shutil
import sys
import tempfile
import unittest
import uuid
import xml.etree.ElementTree as ET
from unittest import mock

from nopekit import claims as claims_mod
from nopekit import gates as gates_mod
from nopekit import packs as packs_mod
from nopekit import report as report_mod
from nopekit import store as store_mod
from nopekit.models import (
    Acceptance, Claim, ClaimKind, ClaimStatus, Comparator, GateSpec, Ledger,
    NegativeControl, PhysicalResult, ProjectMeta, Tier, Verdict,
)
from nopekit.util import NopekitError


def _claim(cid="C1", kind=ClaimKind.MEASURABLE, **kw):
    return Claim(
        id=cid,
        statement=kw.pop("statement", "the thing holds"),
        kind=kind,
        acceptance=kw.pop("acceptance", Acceptance(
            quantity="deflection", comparator=Comparator.LE, limit=0.5, units="mm")),
        gates=kw.pop("gates", ["g.one"]),
        **kw,
    )


def _ledger(*claims_, verdicts=()):
    return Ledger(
        meta=ProjectMeta(name="t", revision="v0.1"),
        claims=list(claims_),
        verdicts=list(verdicts),
    )


class _Reg:
    """Minimal registry stand-in: report/claims only ever read specs off it."""

    def __init__(self, specs=()):
        self._specs = list(specs)

    def specs(self):
        return list(self._specs)

    def for_claim(self, claim_id, tags=()):
        out = []
        for s in self._specs:
            names = set(s.claims or [])
            if claim_id in names or (set(tags or ()) & names):
                out.append(s)
        return out

    def get(self, gid):
        for s in self._specs:
            if s.id == gid:
                return s, (lambda ctx: Verdict(gate=gid, passed=True))
        return None


SPEC = GateSpec(id="g.one", claims=["C1"], tier=Tier.INSTANT,
                negative_control=NegativeControl(fixture="x:y"))


# --------------------------------------------------------------------------- #
class SkipIsNotPass(unittest.TestCase):
    """(a) A gate whose tool is missing has proven nothing."""

    def test_verdict_ok_is_false_when_skipped(self):
        v = Verdict(gate="g.one", passed=True, skipped=True,
                    skip_reason="requires openfoam (not on PATH)")
        self.assertFalse(v.ok, "a skipped verdict must never read as ok, even if "
                               "passed=True was set")

    def test_claim_with_only_skipped_gates_is_blocked(self):
        v = Verdict(gate="g.one", claims=["C1"], passed=True, skipped=True,
                    skip_reason="requires openfoam (not on PATH)")
        st = claims_mod.resolve_status(_claim(), [v])
        self.assertEqual(st, ClaimStatus.BLOCKED)
        self.assertNotEqual(st, ClaimStatus.PASS)

    def test_skipped_gate_renders_as_skip(self):
        v = Verdict(gate="g.one", passed=True, skipped=True, skip_reason="no tool")
        self.assertIn("skip", v.render())
        self.assertNotIn("[ok", v.render())

    # -- P2.1 (old 2.1's V, D-01): a pass never hides what did not run ------ #
    def _two(self, second):
        ok = Verdict(gate="g.one", claims=["C1"], passed=True)
        claim = _claim(gates=["g.one", "g.two"])
        reg = _Reg([SPEC, dataclasses.replace(SPEC, id="g.two")])
        return claim, [ok] + ([second] if second is not None else []), reg

    def _blocks_and_is_red(self, claim, verdicts, reg, want):
        ledger = _ledger(claim, verdicts=verdicts)
        for order in (verdicts, verdicts[::-1]):
            self.assertEqual(claims_mod.resolve_status(claim, order), want)
        self.assertEqual([(c.id, s) for c, s in claims_mod.blocking(ledger, reg)],
                         [("C1", want)])
        junit = ET.fromstring(report_mod.render_junit(
            ledger, verdicts, reg, tier=0, ready=False, exit_code=1, when="t"))
        case = junit.find("testsuite[@name='claims.critical']/testcase[@name='C1']")
        self.assertTrue([k for k in case if k.tag in ("failure", "error")],
                        "the JUnit claim case is not red")

    def test_a_pass_beside_a_skip_is_skipped_and_blocks(self):
        no_tool = Verdict(gate="g.two", claims=["C1"], skipped=True,
                          skip_reason="requires openfoam (not on PATH)")
        self._blocks_and_is_red(*self._two(no_tool), ClaimStatus.BLOCKED)

    def test_a_pass_beside_an_unrun_gate_is_open_and_blocks(self):
        self._blocks_and_is_red(*self._two(None), ClaimStatus.PENDING)

    def test_a_physical_claim_with_a_failing_modelled_half_fails_and_blocks(self):
        """S-49: a physical claim read no evaluator, so its failing modelled half
        left it Pending build — not blocking, and the sentence said it cleared
        every gate. Now the covering fail is Failing before any result counts."""
        claim = _claim("C1", kind=ClaimKind.PHYSICAL, gates=["g.one"],
                       physical_result=PhysicalResult(passed=True))
        fail = Verdict(gate="g.one", claims=["C1"], passed=False, detail="0.7 mm")
        self._blocks_and_is_red(claim, [fail], _Reg([SPEC]), ClaimStatus.FAIL)

    def test_a_blocking_that_drops_skipped_is_caught(self):
        """Planted: a `blocking()` that lets Skipped through a money boundary."""
        claim, verdicts, reg = self._two(Verdict(gate="g.two", claims=["C1"], skipped=True,
                                                 skip_reason="no tool"))
        real = claims_mod.blocking

        def drops(ledger, registry, **kw):
            return [(c, st) for c, st in real(ledger, registry, **kw)
                    if st is not ClaimStatus.BLOCKED]

        with mock.patch.object(claims_mod, "blocking", drops):
            with self.assertRaises(AssertionError):
                self._blocks_and_is_red(claim, verdicts, reg, ClaimStatus.BLOCKED)


class ErrorIsNotPass(unittest.TestCase):
    """(b) A gate that crashed has proven nothing. An error is not a failure either —
    a failure is a measurement, a crash is an absence of one."""

    def test_verdict_ok_is_false_when_errored(self):
        v = Verdict(gate="g.one", passed=True, error="ZeroDivisionError: ...")
        self.assertFalse(v.ok)

    def test_claim_with_errored_gate_does_not_pass(self):
        v = Verdict(gate="g.one", claims=["C1"], passed=True,
                    error="TypeError: unsupported operand")
        st = claims_mod.resolve_status(_claim(), [v])
        self.assertNotEqual(st, ClaimStatus.PASS)

    def test_a_pass_beside_an_errored_gate_does_not_pass(self):
        # The single-verdict test above cannot see this one. A claim with two
        # covering gates, one passing and one crashed, must not read as a pass
        # in either order. Today rung 4 makes it FAIL; Phase 2 moves an errored
        # claim off FAIL, and a status table that lets the passing gate win
        # there would turn a crash into a pass with every other test green.
        ok = Verdict(gate="g.one", claims=["C1"], passed=True)
        boom = Verdict(gate="g.two", claims=["C1"], passed=True,
                       error="RuntimeError: deliberate")
        claim = _claim(gates=["g.one", "g.two"])
        for verdicts in ([ok, boom], [boom, ok]):
            st = claims_mod.resolve_status(claim, verdicts)
            self.assertNotIn(st, (ClaimStatus.PASS, ClaimStatus.VERIFIED),
                             [v.gate for v in verdicts])

    def test_crashing_gate_produces_error_verdict_not_a_pass(self):
        reg = gates_mod.Registry()

        @gates_mod.gate(id="g.boom", claims=["C1"], registry=reg,
                        negative_control=NegativeControl(fixture="x:y"))
        def boom(ctx):
            raise ZeroDivisionError("deliberate")

        spec, fn = reg.get("g.boom")
        ctx = gates_mod.GateContext(root=".", ledger=_ledger(_claim()), model=None,
                                    params={}, out_dir=".", tier=0,
                                    log=lambda m: None, extra={})
        v = gates_mod.run_gate(spec, fn, ctx)
        self.assertTrue(v.error, "a crashing gate must record the error")
        self.assertFalse(v.ok)
        self.assertFalse(v.passed)


class CouldNotMeasureIsNeverANumber(unittest.TestCase):
    """(e) A gate that could not measure must not report a number.

    NaN is the most dangerous value a gate can return: it satisfies NOTHING, because
    ``nan <= limit`` and ``nan > limit`` are BOTH False. A claim resting on one can
    neither pass nor fail, so it sits in the report looking checked while being
    unfalsifiable — this project's own failure mode, arriving as a number instead of
    a skip.

    Observed in the wild: a gate wrapped its measurement in ``except: return nan``.
    Its dependency was present only on its author's machine, so it returned NaN for
    everyone else and was inert from the day it was written. It never hid a specific
    defect; it would have hidden any defect, for anyone else.
    """

    def _run(self, value):
        reg = gates_mod.Registry()

        @gates_mod.gate(id="g.measure", claims=["C1"], registry=reg,
                        negative_control=NegativeControl(fixture="x:y"))
        def measure(ctx):
            return Verdict(gate="g.measure", passed=True, measured=value, limit=1.0,
                           detail="could not measure, but here is a number anyway")

        ctx = gates_mod.GateContext(root=".", ledger=_ledger(_claim()), model=None,
                                    params={}, out_dir=".", tier=0,
                                    log=lambda _m: None, extra={})
        return gates_mod.run_gate(*reg.get("g.measure"), ctx)

    def test_nan_measured_cannot_pass(self):
        v = self._run(float("nan"))
        self.assertFalse(v.ok, "a NaN measurement was reported as a pass")
        self.assertFalse(v.passed)
        self.assertIn("non-finite", (v.error or "").lower())

    def test_infinite_measured_cannot_pass(self):
        self.assertFalse(self._run(float("inf")).ok)
        self.assertFalse(self._run(float("-inf")).ok)

    def test_a_real_measurement_still_passes(self):
        """The guard must not eat healthy verdicts."""
        v = self._run(0.42)
        self.assertTrue(v.ok)
        self.assertEqual(v.measured, 0.42)


class RegistryRefusesLoggers(unittest.TestCase):
    """(c) A gate that cannot demonstrate failure is a logger. Rule 5, mechanical."""

    def test_register_without_negative_control_raises(self):
        reg = gates_mod.Registry()
        with self.assertRaises(Exception) as cm:
            @gates_mod.gate(id="g.nocontrol", claims=["C1"], registry=reg)
            def nocontrol(ctx):
                return True
        msg = str(cm.exception).lower()
        self.assertIn("negative", msg,
                      "the refusal must say WHAT is missing, not just that something is")

    def test_register_with_negative_control_succeeds(self):
        reg = gates_mod.Registry()

        @gates_mod.gate(id="g.ok", claims=["C1"], registry=reg,
                        negative_control=NegativeControl(fixture="x:y"))
        def ok(ctx):
            return True

        self.assertIsNotNone(reg.get("g.ok"))

    def test_gate_cannot_lie_about_its_own_identity(self):
        """run_gate overwrites identity fields from the spec, so a gate returning a
        verdict naming a different gate cannot smuggle it into the ledger."""
        reg = gates_mod.Registry()

        @gates_mod.gate(id="g.honest", claims=["C1"], tier=Tier.SOLVE, registry=reg,
                        negative_control=NegativeControl(fixture="x:y"))
        def liar(ctx):
            return Verdict(gate="g.something_else", passed=True, tier=Tier.INSTANT,
                           claims=["C_other"])

        spec, fn = reg.get("g.honest")
        ctx = gates_mod.GateContext(root=".", ledger=_ledger(_claim()), model=None,
                                    params={}, out_dir=".", tier=0,
                                    log=lambda m: None, extra={})
        v = gates_mod.run_gate(spec, fn, ctx)
        self.assertEqual(v.gate, "g.honest")
        self.assertEqual(v.tier, Tier.SOLVE)
        self.assertEqual(v.claims, ["C1"])


class ReportNeverOverclaims(unittest.TestCase):
    """(d) The readiness report is the deliverable. It must refuse to call anything
    proven that was not."""

    def _render(self, ledger, reg):
        return report_mod.render_markdown(ledger, reg)

    def _proven_section(self, md):
        """Just the PROVEN section body — NOT the headline verdict.

        The headline legitimately names claims that are unproven (that is its job:
        to say what is outstanding). The invariant under test is narrower: nothing
        unproven may appear in the PROVEN table.

        It FAILS the test when there is no such section; it never returns "".
        What slipped through (S-15): this helper searched for a literal heading and
        returned an empty string when the heading was missing, so every
        `assertNotIn(..., section)` in this class passed on nothing. Renaming the
        heading — which P2's REPORT.md rewrite proposes (PLAN D-14, A-11) — would
        have kept invariant 4 green while testing no report at all. The heading is
        now `report.SECTION_PROVEN`, the constant the report itself emits, so a
        rename moves the report and this helper together, and a report that stops
        emitting it turns these tests red instead of vacuous.
        """
        heading = report_mod.SECTION_PROVEN
        lines = md.splitlines()
        at = [i for i, line in enumerate(lines)
              if line == heading or line.startswith(heading + " ")]
        if len(at) != 1:
            self.fail(f"the report has {len(at)} {heading!r} headings where it must "
                      f"have exactly one: an absent PROVEN section is not an empty "
                      f"one, and assertNotIn on nothing proves nothing")
        body = []
        for line in lines[at[0] + 1:]:
            if line.startswith("## "):
                break
            body.append(line)
        section = "\n".join(body)
        if not section.strip():
            self.fail(f"the {heading!r} section is empty: the report always says "
                      f"something there, if only that nothing is proven")
        return section

    def test_skipped_gate_is_not_in_proven_section(self):
        v = Verdict(gate="g.one", claims=["C1"], passed=True, skipped=True,
                    skip_reason="requires openfoam")
        md = self._render(_ledger(_claim(), verdicts=[v]), _Reg([SPEC]))
        self.assertNotIn("g.one", self._proven_section(md),
                         "a skipped gate appeared in the PROVEN section")

    def test_unrun_gate_is_not_in_proven_section(self):
        md = self._render(_ledger(_claim()), _Reg([SPEC]))
        self.assertNotIn("g.one", self._proven_section(md))

    def test_physical_claim_never_proven(self):
        c = _claim("C9", kind=ClaimKind.PHYSICAL, gates=[])
        self.assertEqual(claims_mod.resolve_status(c, []), ClaimStatus.UNVERIFIED)
        md = self._render(_ledger(c), _Reg([]))
        self.assertNotIn("C9", self._proven_section(md))

    def test_monkeypatched_heading_makes_the_helper_raise(self):
        """V for `_proven_section`: a report whose PROVEN heading is not the one
        `report.SECTION_PROVEN` names makes the helper FAIL, never hand the three
        tests above an empty string to pass on (S-15)."""
        from unittest import mock

        v = Verdict(gate="g.one", claims=["C1"], passed=True, measured=0.312, units="mm")
        ledger = _ledger(_claim(), verdicts=[v])
        emit = report_mod._section_proven

        def renamed(*args, **kwargs):
            out = list(emit(*args, **kwargs))
            out[0] = "## What is VERIFIED (a rename that bypassed the constant)"
            return out

        with self.subTest("the report stops emitting the constant"):
            with mock.patch.object(report_mod, "_section_proven", renamed):
                md = self._render(ledger, _Reg([SPEC]))
            self.assertIn("g.one", md)          # the row is there, under another name
            with self.assertRaises(self.failureException):
                self._proven_section(md)
        with self.subTest("the helper follows the constant, never a literal"):
            md = self._render(ledger, _Reg([SPEC]))
            with mock.patch.object(report_mod, "SECTION_PROVEN", "## What is CERTIFIED"):
                with self.assertRaises(self.failureException):
                    self._proven_section(md)
        with self.subTest("a renamed constant moves the report and the helper together"):
            with mock.patch.object(report_mod, "SECTION_PROVEN", "## What is CERTIFIED"):
                md = self._render(ledger, _Reg([SPEC]))
                self.assertIn("g.one", self._proven_section(md))

    def test_a_gate_that_ran_ok_does_appear(self):
        """The positive control for the absence tests above. `assertNotIn` is
        satisfied by any section that shows nothing — an empty one, or one cut at
        the wrong heading — so the same helper, on a gate that ran and passed,
        must find the gate, its claim and its number."""
        v = Verdict(gate="g.one", claims=["C1"], passed=True, measured=0.312, units="mm")
        md = self._render(_ledger(_claim(), verdicts=[v]), _Reg([SPEC]))
        section = self._proven_section(md)
        self.assertIn("g.one", section)
        self.assertIn("C1", section)
        self.assertIn("0.312", section)

    def test_a_pass_the_evidence_does_not_back_is_a_disagreement(self):
        """P2.1-D18: under GLOSSARY §3's composition a Checked claim has no
        evaluator that did not pass. If the resolver says `pass` anyway — here a
        planted compose that lets a pass beside a skip through — the report
        never lists it under the checked section, prints the contradiction
        loudly in the failing section, makes its JUnit case red, and the page
        marks the row (`disagree`). What slipped through the P2.1 design: the
        contradiction row stayed INSIDE the checked section, marked — PARTIAL
        under a new name — and `state.json` had nothing in `partial`'s place."""
        from nopekit import site as site_mod
        no_tool = Verdict(gate="g.two", claims=["C1"], skipped=True, skip_reason="no tool")
        ok = Verdict(gate="g.one", claims=["C1"], passed=True, measured=0.3, units="mm")
        reg = _Reg([SPEC, dataclasses.replace(SPEC, id="g.two")])
        ledger = _ledger(_claim(gates=["g.one", "g.two"]), verdicts=[ok, no_tool])
        real = claims_mod.compose

        def lets_it_through(claim, verdicts, **kw):
            found = real(claim, verdicts, **kw)
            if found.cause is claims_mod.ClaimCause.SKIPPED:
                return claims_mod.Composed(ClaimStatus.PASS, claims_mod.ClaimCause.CHECKED)
            return found

        with mock.patch.object(claims_mod, "compose", lets_it_through):
            self.assertEqual(claims_mod.resolve_status(ledger.claims[0], ledger.verdicts),
                             ClaimStatus.PASS)
            md = self._render(ledger, reg)
            terminal = report_mod.render_terminal(ledger, reg)
            junit = ET.fromstring(report_mod.render_junit(
                ledger, ledger.verdicts, reg, tier=0, ready=True, exit_code=0, when="t"))
            resolution = mock.Mock(verdicts=ledger.verdicts, stale_gates=frozenset(), rows={})
            state = site_mod.state(tempfile.gettempdir(), ledger, reg, resolution=resolution,
                                   params=[])
        self.assertNotIn("C1", self._proven_section(md))
        failing = md.split("## Failing, stale, skipped or open", 1)[1]
        self.assertIn("**status and evidence disagree — g.two skipped: no tool**", failing)
        case = junit.find("testsuite[@name='claims.critical']/testcase[@name='C1']")
        self.assertEqual([(k.tag, k.get("type")) for k in case],
                         [("failure", "status-and-evidence-disagree")])
        row = next(r for r in state["claims"] if r["id"] == "C1")
        self.assertEqual((row["status"], row["disagree"]), ("pass", True))
        self.assertTrue(any(re.match(r"^\[.{5}\] C1 .* — status and evidence disagree — ", ln)
                            for ln in terminal.splitlines()), terminal)

    def test_verdict_says_so_when_something_blocks(self):
        """The one-sentence verdict must lead with the problem, not bury it."""
        v = Verdict(gate="g.one", claims=["C1"], passed=False, detail="0.70 vs 0.50")
        md = self._render(_ledger(_claim(), verdicts=[v]), _Reg([SPEC]))
        head = md.split("## ")[0].lower()
        self.assertTrue(
            any(w in head for w in ("not ready", "fail", "block", "not verified",
                                    "gap", "outstanding", "unresolved", "never")),
            f"the headline verdict hid a failing critical claim: {head!r}")


#: Where each status's claims are listed in the report (P2.1-D19), typed here:
#: a claim is in exactly its section, once.
_SECTION_OF = {
    "pass": "## What is PROVEN", "verified": "## What is PROVEN",
    "unverified": "## Pending build", "unclaimed": "## Gaps", "asserted": "## Assumed",
    "fail": "## Failing, stale, skipped or open", "refuted": "## Failing, stale, skipped or open",
    "stale": "## Failing, stale, skipped or open", "blocked": "## Failing, stale, skipped or open",
    "pending": "## Failing, stale, skipped or open",
}

#: A claim's entry in each section: the row, bullet or head that lists it.
_ENTRY = re.compile(r"^(?:\| \*\*(?P<row>K\d+)\*\*|- \*\*Claim:\*\* (?P<need>K\d+)"
                    r"|- \*\*Checked on an article:\*\* \*\*(?P<art>K\d+)\*\*"
                    r"|- \*\*(?P<bullet>K\d+)\*\*|### \[.{5}\] (?P<head>K\d+) — .*\*\((?P<word>[^,]+(?:, errored)?),)")

#: The word a failing-section head names for each status (GLOSSARY §3, typed).
_HEAD_WORD = {"fail": "failing", "refuted": "failing", "stale": "stale", "blocked": "skipped",
              "pending": "open"}


#: The operating-context mark (P2.4), as the spine mints it.
_OUTSIDE = ('context:outside|{"hi":40.0,"key":"load_n","lo":0.0,"value":60,'
            '"why":"outside"}')


def _physical_claims(c) -> list:
    """One claim per P2.5a cause, built in memory with the standing the judge
    would give it (``verdicts.judge_results`` has its own tests)."""
    from nopekit.models import AttributionRecord, EntryStanding, Standing
    phys, assume = ClaimKind.PHYSICAL, ClaimKind.ASSUMPTION
    dana = {"terminal": "human", "authority": "Dana"}
    passed = PhysicalResult(passed=True, who="Sam <s@x>", channel="interactive",
                            article={"hash": "a" * 64}, when="2026-10-04")
    judged = PhysicalResult(passed=True, who="Dana <d@x>", channel="interactive",
                            authority="Dana", article={"hash": "a" * 64}, when="2026-10-04")

    def standing(state, result, counts=False):
        why = "" if counts else state
        return Standing(state, 0, "a" * 64, ("config.t 7.0 -> 8.0",),
                        (EntryStanding(0, True, counts, why, "a" * 64,
                                       "moved" if "moved" in state else "current"),))

    def judged_claim(cid, **kw):
        return c(cid, assume, rationale="a safety call", **dana, **kw)

    awaiting = judged_claim("K29")
    awaiting = dataclasses.replace(awaiting, attributions=(AttributionRecord(
        role="authority", name="Dana", reason="a safety call",
        claim_digest=claims_mod.claim_digest(awaiting), who="Dana <d@x>",
        channel="interactive"),))
    return [
        c("K21", phys, physical_result=PhysicalResult(passed=False, detail="sagged",
                                                      contradicts=[
            {"gate": "g.k1", "code": "c" * 64, "rho": "", "value": 0.4, "units": "mm",
             "inside": True}])),
        judged_claim("K22", physical_result=PhysicalResult(
            passed=False, who="Dana <d@x>", authority="Dana", detail="too heavy")),
        c("K23", assume, rationale="r", terminal="human"),
        judged_claim("K24"),
        c("K25", phys, physical_result=passed, results=(passed,),
          standing=standing("article-moved", passed)),
        c("K26", phys, physical_result=passed, results=(passed,),
          standing=standing("claim-moved", passed)),
        judged_claim("K27", physical_result=judged, results=(judged,),
                     standing=standing("judgment-moved", judged)),
        c("K28", phys, physical_result=passed, results=(passed,),
          standing=standing("article-unjudged", passed)),
        awaiting,
        c("K30", phys, physical_result=passed, results=(passed,),
          standing=standing("current", passed, counts=True)),
        judged_claim("K31", physical_result=judged, results=(judged,),
                     standing=standing("current", judged, counts=True)),
    ]


def _every_status_ledger() -> tuple[Ledger, _Reg, dict, frozenset]:
    """A ledger reaching every (status, cause) `compose` can give, one claim
    each, with the registry, the attributions and the stale gates it needs."""
    def gate(gid):
        return dataclasses.replace(SPEC, id=gid, claims=[gid.replace("g.", "").upper()])

    def c(cid, kind=ClaimKind.MEASURABLE, gates=(), **kw):
        return _claim(cid, kind=kind, gates=list(gates), **kw)

    phys, assume = ClaimKind.PHYSICAL, ClaimKind.ASSUMPTION
    claims = [
        c("K1", gates=["g.k1"]), c("K2", gates=["g.k2"]),
        c("K3", phys, physical_result=PhysicalResult(passed=False, detail="cracked")),
        c("K4", gates=["g.k4"]), c("K5", gates=["g.k5"]), c("K6"), c("K7", gates=["g.k7"]),
        c("K8", assume), c("K9", assume, owner="Sam", rationale="r"),
        c("K10", assume, owner="Sam"), c("K11", gates=["g.k11"]), c("K12", gates=["g.k12"]),
        c("K13", phys), c("K14", phys, physical_result=PhysicalResult(passed=True)),
        c("K15", assume, owner="Ana", rationale="carried on purpose"),
        # P2.2: not run behind a prerequisite that failed, and one that crashed
        c("K16", gates=["g.k16"]), c("K17", gates=["g.k17"]),
        # P2.4: a pass whose value misses the claim's acceptance condition, a
        # pass outside its evaluator's operating context, and one an owned
        # fallback carries (R-6: three more causes the report must place)
        c("K18", gates=["g.k18"]), c("K19", gates=["g.k19"]),
        c("K20", gates=["g.k20"], owner="Bo", fallback="linear past the range"),
        # P2.5a: the physical path and expert judgment (R-6: eleven more causes
        # the report must place, and VERIFIED reachable at last).
        *_physical_claims(c),
    ]
    verdicts = [
        Verdict(gate="g.k1", claims=["K1"], passed=True),
        Verdict(gate="g.k2", claims=["K2"], passed=False, detail="0.7 mm"),
        Verdict(gate="g.k4", claims=["K4"], error="RuntimeError: boom"),
        Verdict(gate="g.k5", claims=["K5"], skipped=True, skip_reason="no tool"),
        Verdict(gate="g.k7", claims=["K7"], error="not admitted: x", unqualified="x"),
        Verdict(gate="g.k12", claims=["K12"], passed=True),
        Verdict(gate="g.k16", claims=["K16"], skipped=True, blocked_by=["g.k2"],
                blocked_kind="failed", skip_reason="prerequisite failed: g.k2"),
        Verdict(gate="g.k17", claims=["K17"], skipped=True, blocked_by=["g.k4"],
                blocked_kind="errored",
                skip_reason="prerequisite not established: g.k4 (errored)"),
        Verdict(gate="g.k18", claims=["K18"], passed=True, measured=0.7, limit=1.0,
                units="mm", comparator="<=", settles="deflection"),
        Verdict(gate="g.k19", claims=["K19"], passed=True, unqualified=_OUTSIDE),
        Verdict(gate="g.k20", claims=["K20"], passed=True, unqualified=_OUTSIDE),
    ]
    reg = _Reg([gate(g) for g in ("g.k1", "g.k2", "g.k4", "g.k5", "g.k7", "g.k11", "g.k12",
                                  "g.k16", "g.k17", "g.k18", "g.k19", "g.k20")])
    owners = {"K15": claims_mod.Attribution("Ana", "carried on purpose"),
              "K20": claims_mod.Attribution("Bo", "linear past the range")}
    return _ledger(*claims, verdicts=verdicts), reg, owners, frozenset({"g.k12"})


def listing_problems(md: str, composed: dict) -> list[str]:
    """Every claim of `composed` not in exactly its section once, with its word."""
    sections: dict[str, list[tuple[str, str]]] = {}
    current = ""
    for line in md.splitlines():
        if line.startswith("## "):
            current = next((h for h in set(_SECTION_OF.values()) if line.startswith(h)), "")
            continue
        m = _ENTRY.match(line)
        if m and current:
            cid = next(v for k, v in m.groupdict().items() if v and k != "word")
            sections.setdefault(cid, []).append((current, m.group("word") or ""))
    out = []
    for cid, found in composed.items():
        want = _SECTION_OF[found.status.value]
        seen = sections.get(cid, [])
        if [where for where, _w in seen] != [want]:
            out.append(f"{cid} ({found.status.value}, {found.cause.value}): listed "
                       f"{[w for w, _ in seen] or 'nowhere'}, not once under {want!r}")
            continue
        head = _HEAD_WORD.get(found.status.value)
        if head:
            word = head + (", errored" if found.errored else "")
            if seen[0][1] != word:
                out.append(f"{cid}: its head says {seen[0][1]!r}, not {word!r}")
    return out


class EveryUnresolvedClaimIsListed(unittest.TestCase):
    """(V, P2.1-D19) Every claim is listed in exactly one report section — its
    status's — with its word. What slipped through the P2.1 design: the gaps
    section read only `find_gaps` (an automated claim no evaluator covers), so a
    Gap from an unqualified evaluator or an unowned assumption was in no section
    at all, and the one it did list was told to run `gap --propose`."""

    def _md(self, gaps=None):
        ledger, reg, owners, stale = _every_status_ledger()
        real = claims_mod.compositions

        def owned(ledger_, **kw):
            kw.setdefault("owners", owners)
            return real(ledger_, **kw)

        patches = [mock.patch.object(claims_mod, "compositions", owned)]
        if gaps is not None:
            patches.append(mock.patch.object(report_mod, "_section_gaps", gaps))
        with patches[0], (patches[1] if len(patches) > 1 else mock.patch.object(
                report_mod, "_section_gaps", report_mod._section_gaps)):
            md = report_mod.render_markdown(ledger, reg, stale_gates=stale)
            composed = owned(ledger, registry=reg, stale_gates=stale)
        return md, composed

    def test_every_status_and_cause_is_reached(self):
        """Every status `compose` can give — `verified` too from P2.5a, a pass
        on the current article and an authority's judgment (until then a typed
        pass read Pending build, review of P2.1) — and every cause."""
        _md, composed = self._md()
        self.assertEqual({c.status.value for c in composed.values()}, set(_SECTION_OF))
        self.assertEqual({c.cause.value for c in composed.values()},
                         {cause.value for cause in claims_mod.ClaimCause})

    def test_each_claim_once_in_its_section(self):
        md, composed = self._md()
        self.assertEqual(listing_problems(md, composed), [])

    def test_a_gaps_section_that_reads_find_gaps_only_is_caught(self):
        def needs_only(ledger, composed, registry, **_kw):
            out = ["## Gaps", ""]
            for need in report_mod._needs(ledger, registry):
                out += [f"### {need.id}"] + [f"- **Claim:** {cid} — x" for cid in need.claim_ids]
            return out + [""]

        md, composed = self._md(gaps=needs_only)
        missing = {p.split(" ", 1)[0] for p in listing_problems(md, composed)}
        self.assertEqual(missing, {"K7", "K8", "K9", "K10", "K19", "K23", "K24"})


class RequiredIsSaidAsABool(unittest.TestCase):
    """A claim file's `critical` is true, false or absent (required): anything
    else is refused by the strict reader, naming the file, as a result's
    `passed` is. What slipped through (review of P2.1): `"critical": null` was
    copied as-is and read by truthiness, so a FAILING claim left every required
    list — `check` printed `ready: every required claim is checked`, exit 0,
    and `status --json` said `all_required_checked: true`. A null is a value
    nobody set; it never means "not required"."""

    def _read(self, body: dict) -> Claim:
        root = tempfile.mkdtemp(prefix="nopekit-critical-")
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        path = os.path.join(root, "C1.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"statement": "the tip sags no more than 0.5 mm", **body}, fh)
        return store_mod.read_record(path, "claims")

    def test_only_a_bool_says_whether_a_claim_is_required(self):
        self.assertIs(self._read({}).critical, True)
        for value in (True, False):
            with self.subTest(value=value):
                self.assertIs(self._read({"critical": value}).critical, value)
        for value in (None, 0, 1, "", "false", []):
            with self.subTest(value=value):
                with self.assertRaises(NopekitError) as caught:
                    self._read({"critical": value})
                self.assertIn('C1.json: "critical" must be true or false',
                              str(caught.exception))

    def test_what_a_null_would_have_bought(self):
        """The reason it is refused: read leniently, `critical: None` takes a
        failing claim off the required list, and *ready* holds over it."""
        claim = _claim(gates=["g.one"], critical=None)
        ledger = _ledger(claim, verdicts=[Verdict(gate="g.one", claims=["C1"], passed=False)])
        found = claims_mod.summarise(ledger, _Reg([SPEC]))
        self.assertEqual(found["blocking_ids"], [], "the lenient read degrades open")


class StatusPrecedence(unittest.TestCase):
    """The derivation table from SPINE_CONTRACT.md, exactly."""

    def test_an_assumption_nobody_owns_is_a_gap(self):
        """GLOSSARY §3: Assumed needs a reason and an owner; with no owner the
        claim reads Gap (PLAN-v0.14 §1.4). Was `test_assumption_is_asserted`
        (R-6: a reversal §1.4 forces, beside the case that keeps Assumed held)."""
        c = _claim("C2", kind=ClaimKind.ASSUMPTION, gates=[], rationale="why")
        self.assertEqual(claims_mod.resolve_status(c, []), ClaimStatus.UNCLAIMED)

    def test_an_attributed_assumption_is_assumed(self):
        c = _claim("C2", kind=ClaimKind.ASSUMPTION, gates=[], rationale="why", owner="Sam")
        owners = {"C2": claims_mod.Attribution("Sam", "why")}
        self.assertEqual(claims_mod.resolve_status(c, [], owners=owners), ClaimStatus.ASSERTED)
        self.assertEqual(claims_mod.resolve_status(c, []), ClaimStatus.UNCLAIMED,
                         "an owner in the file alone counts for nothing")

    def test_physical_with_result(self):
        """A typed pass reads Pending build, cause `physical-pass`, until article
        binding (R-6, toward unresolved: this read VERIFIED, Checked on every
        channel but *ready* — review of P2.1); a fail reads Failing."""
        c = _claim("C3", kind=ClaimKind.PHYSICAL, gates=[])
        c.physical_result = PhysicalResult(passed=True, when="2026-01-01")
        found = claims_mod.compose(c, [])
        self.assertEqual((found.status, found.cause),
                         (ClaimStatus.UNVERIFIED, claims_mod.ClaimCause.PHYSICAL_PASS))
        c.physical_result = PhysicalResult(passed=False, when="2026-01-01")
        self.assertEqual(claims_mod.resolve_status(c, []), ClaimStatus.REFUTED)

    # -- R-3: a recorded fail never loses its power to fail ------------------ #
    def _loaded(self, kind: str, results: list[dict]) -> Claim:
        """C5 of a records project with `results` in `results/C5.json`, oldest
        first, read back through `store.load` — the assembler every command uses."""
        root = tempfile.mkdtemp(prefix="nopekit-r3-")
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        store_mod.init(root, ProjectMeta(name="r3", revision="v0.1"))
        for sub, name, body in (
                ("claims", "C5.json", {"statement": "survives two winters", "kind": kind}),
                ("results", "C5.json", {"results": results})):
            os.makedirs(os.path.join(root, sub), exist_ok=True)
            with open(os.path.join(root, sub, name), "w", encoding="utf-8") as fh:
                json.dump(body, fh)
        return store_mod.load(root).claim("C5")

    def test_a_pass_after_a_fail_never_outranks_it(self):
        """A pass typed one second after a fail: the fail still counts. What
        slipped through (review of P2.1): the LAST result counted, so the
        project read ready with the fail still in `results/C5.json`."""
        claim = self._loaded("physical", [
            {"passed": False, "who": "tester", "detail": "cracked after 1 winter"},
            {"passed": True, "who": "agent", "detail": "looks fine"}])
        found = claims_mod.compose(claim, [])
        self.assertEqual((found.status, found.cause),
                         (ClaimStatus.REFUTED, claims_mod.ClaimCause.PHYSICAL_FAIL))
        self.assertEqual(claim.physical_result.detail, "cracked after 1 winter")

    def test_a_fail_counts_whatever_the_claims_kind(self):
        """The claim's kind edited away from physical — the edit `claim
        physical`'s own refusal names — keeps its recorded fail: Failing, and
        blocking. What slipped through: the result rung read only a PHYSICAL
        claim's result, so the edit read the project ready."""
        for kind in ("measurable", "assumption"):
            with self.subTest(kind=kind):
                claim = self._loaded(kind, [{"passed": False, "detail": "embrittled"}])
                ledger = _ledger(claim)
                self.assertEqual(claims_mod.resolve_status(claim, []), ClaimStatus.REFUTED)
                self.assertEqual([c.id for c, _ in claims_mod.blocking(ledger, None)], ["C5"])
        claim = self._loaded("measurable", [{"passed": True, "detail": "looks fine"}])
        self.assertEqual(claims_mod.resolve_status(claim, []), ClaimStatus.UNCLAIMED,
                         "a recorded pass settles nothing an evaluator is meant to")

    def test_the_latest_result_wins_is_caught(self):
        """Planted: the assembler as P2.1 had it — the last result counts."""
        real = store_mod._counting_result
        with mock.patch.object(store_mod, "_counting_result",
                               lambda items: items[-1] if items else None):
            claim = self._loaded("physical", [{"passed": False}, {"passed": True}])
        self.assertNotEqual(claims_mod.resolve_status(claim, []), ClaimStatus.REFUTED)
        self.assertIs(store_mod._counting_result, real)

    def test_no_covering_gate_is_unclaimed(self):
        self.assertEqual(
            claims_mod.resolve_status(_claim(gates=[]), []), ClaimStatus.UNCLAIMED)

    def test_gate_exists_but_never_ran_is_pending(self):
        self.assertEqual(claims_mod.resolve_status(_claim(), []), ClaimStatus.PENDING)

    def test_failure_beats_success(self):
        vs = [Verdict(gate="g.one", claims=["C1"], passed=True),
              Verdict(gate="g.two", claims=["C1"], passed=False)]
        c = _claim(gates=["g.one", "g.two"])
        self.assertEqual(claims_mod.resolve_status(c, vs), ClaimStatus.FAIL)

    def test_stale_is_not_pass(self):
        v = Verdict(gate="g.one", claims=["C1"], passed=True)
        self.assertEqual(
            claims_mod.resolve_status(_claim(), [v], stale=True), ClaimStatus.STALE)

    def test_clean_pass(self):
        v = Verdict(gate="g.one", claims=["C1"], passed=True)
        self.assertEqual(claims_mod.resolve_status(_claim(), [v]), ClaimStatus.PASS)

    def test_blocking_statuses_exclude_pass_and_verified(self):
        from nopekit.models import BLOCKING_STATUSES
        self.assertNotIn(ClaimStatus.PASS, BLOCKING_STATUSES)
        self.assertNotIn(ClaimStatus.VERIFIED, BLOCKING_STATUSES)
        self.assertIn(ClaimStatus.UNCLAIMED, BLOCKING_STATUSES)
        self.assertIn(ClaimStatus.BLOCKED, BLOCKING_STATUSES)
        self.assertIn(ClaimStatus.STALE, BLOCKING_STATUSES)


# --------------------------------------------------------------------------- #
# P2.1: Checked means every evaluator passed (invariants 1 and 4)
# --------------------------------------------------------------------------- #
#: The covering outcomes V3 composes: GLOSSARY §1's four, plus a refusal of
#: qualification (the spine's mark, `Verdict.unqualified`).
_OUTCOMES = ("pass", "fail", "skipped", "error", "unqualified")


def _covering(kind: str, index: int) -> Verdict:
    gate = f"g.{index}{kind}"
    return {
        "pass": Verdict(gate=gate, claims=["C1"], passed=True),
        "fail": Verdict(gate=gate, claims=["C1"], passed=False),
        "skipped": Verdict(gate=gate, claims=["C1"], skipped=True, skip_reason="no tool"),
        "error": Verdict(gate=gate, claims=["C1"], error="RuntimeError: x"),
        "unqualified": Verdict(gate=gate, claims=["C1"], error="not admitted: x",
                               unqualified="x"),
    }[kind]


#: The claim kinds V3 composes over: (kind, physical result, attributed). A
#: result on an automated claim is a physical result left behind by an edit of
#: its kind (review of P2.1): a fail still counts, a pass never does.
_KINDS = (
    ("automated", None, False),
    ("automated, a fail recorded", False, False),
    ("automated, a pass recorded", True, False),
    ("physical, no result", None, False),
    ("physical, a pass", True, False),
    ("physical, a fail", False, False),
    ("assumption", None, False),
    ("assumption, attributed", None, True),
)


def _ladder_860ffa6(claim, verdicts, *, stale=False, stale_gates=(), owners=None):
    """`claims.resolve_status` as `860ffa6` had it, copied here as a literal: the
    planted violator V3 must catch — pass beside a skip, an unrun gate, an error
    or a refusal of qualification."""
    if claim.kind == ClaimKind.ASSUMPTION:
        return ClaimStatus.ASSERTED
    if claim.kind == ClaimKind.PHYSICAL:
        result = claim.physical_result
        if result is None:
            return ClaimStatus.UNVERIFIED
        return ClaimStatus.VERIFIED if result.passed else ClaimStatus.REFUTED
    mine = claims_mod.covering_verdicts(claim, verdicts)
    known = [g for g in (claim.gates or ()) if g]
    if not mine and not known:
        return ClaimStatus.UNCLAIMED
    outcomes = [v.outcome for v in mine]
    if mine and all(o == "skipped" for o in outcomes):
        return ClaimStatus.BLOCKED
    if not mine:
        return ClaimStatus.PENDING
    if any(o in ("error", "fail") for o in outcomes):
        return ClaimStatus.FAIL
    if stale or (set(stale_gates) & ({v.gate for v in mine} | set(known))):
        return ClaimStatus.STALE
    return ClaimStatus.PASS


def checked_problems(resolve=None, compose=None) -> list[str]:
    """Every case where a claim reads Checked and should not, or should and does
    not, over every multiset of up to three covering outcomes × an unrun gate ×
    an invalidated pass × every kind; and where `compose`'s status and
    `resolve_status`'s disagree, or its cause does not fit its status."""
    resolve = resolve or claims_mod.resolve_status
    compose = compose or claims_mod.compose
    checked = (ClaimStatus.PASS, ClaimStatus.VERIFIED)
    causes = {"failed": "fail", "physical-fail": "refuted", "errored": "blocked",
              "skipped": "blocked", "unqualified": "unclaimed", "no-evaluator": "unclaimed",
              "no-owner": "unclaimed", "owner-unattributed": "unclaimed",
              "no-reason": "unclaimed", "unrun": "pending", "invalidated": "stale",
              "no-article": "unverified", "owned": "asserted", "physical-pass": "unverified",
              "checked": "pass"}
    out: list[str] = []
    for size in range(4):
        for combo in itertools.combinations_with_replacement(_OUTCOMES, size):
            for unrun, stale, (kind, result, attributed) in itertools.product(
                    (False, True), (False, True), _KINDS):
                verdicts = [_covering(k, i) for i, k in enumerate(combo)]
                gates = [v.gate for v in verdicts] + (["g.unrun"] if unrun else [])
                moved = {v.gate for v in verdicts if v.outcome == "pass"} if stale else set()
                claim_kind = (ClaimKind.MEASURABLE if kind.startswith("automated") else
                              ClaimKind.PHYSICAL if kind.startswith("physical")
                              else ClaimKind.ASSUMPTION)
                claim = _claim(gates=gates, kind=claim_kind, rationale="why",
                               owner="Sam" if claim_kind is ClaimKind.ASSUMPTION else "",
                               physical_result=(None if result is None
                                                else PhysicalResult(passed=result)))
                owners = {"C1": claims_mod.Attribution("Sam", "why")} if attributed else None
                status = ClaimStatus(resolve(claim, verdicts, stale_gates=moved, owners=owners))
                if claim_kind is ClaimKind.ASSUMPTION:
                    should = False
                elif claim_kind is ClaimKind.PHYSICAL:
                    # Checked only on a pass bound to an article built from the
                    # current inputs (GLOSSARY §3), and nothing binds one until
                    # article binding: a typed pass is Pending build (review of
                    # P2.1; R-6, toward unresolved — this read `result is True`).
                    should = False
                else:
                    should = (result is not False and bool(verdicts) and not unrun
                              and not moved
                              and all(v.outcome == "pass" and not v.unqualified
                                      for v in verdicts))
                name = f"{kind}: {'+'.join(combo) or '-'}{' unrun' if unrun else ''}" \
                       f"{' stale' if stale else ''}"
                if (status in checked) != should:
                    out.append(f"{name}: {status.value}")
                found = compose(claim, verdicts, stale_gates=moved, owners=owners)
                if found.status != status:
                    out.append(f"{name}: compose {found.status.value}, resolve {status.value}")
                if causes.get(str(found.cause.value)) != found.status.value:
                    out.append(f"{name}: cause {found.cause.value} with {found.status.value}")
    return out


class CheckedMeansEveryEvaluatorPassed(unittest.TestCase):
    """(1, 4) GLOSSARY §3's composition, exhaustively: a claim reads Checked iff
    its kind allows it, every known evaluator has a verdict, every verdict
    passed, none is unqualified, none is invalidated — and, for a physical
    claim, its recorded result passed. An assumption is never Checked. What
    slipped through the ladder this replaced (S-03): a pass beside a skip, an
    unrun gate or a refused evaluator read PASS, and the report marked it
    PARTIAL under PROVEN."""

    def test_checked_iff_every_evaluator_passed(self):
        self.assertEqual(checked_problems(), [])

    def test_the_860ffa6_ladder_is_caught(self):
        """Planted: today's ladder before P2.1, as a literal. Pass beside a skip
        or an unrun gate reads pass — Checked, caught by the oracle; pass beside
        a crash or a refusal reads fail — not Checked, but not what `compose`
        says, caught by the agreement half."""
        found = checked_problems(resolve=_ladder_860ffa6)
        for row, problem in (("pass+skipped", "pass"), ("pass unrun", "pass"),
                             ("pass+error", "compose blocked, resolve fail"),
                             ("pass+unqualified", "compose unclaimed, resolve fail")):
            with self.subTest(row=row):
                self.assertIn(f"automated: {row}: {problem}", found)

    def test_a_compose_that_drops_refusals_is_caught(self):
        real = claims_mod.compose

        def drops(claim, verdicts, **kw):
            kept = [v for v in verdicts if not v.unqualified]
            gone = {v.gate for v in verdicts} - {v.gate for v in kept}
            return real(dataclasses.replace(claim, gates=[g for g in claim.gates
                                                          if g not in gone]), kept, **kw)

        found = checked_problems(resolve=lambda *a, **kw: drops(*a, **kw).status, compose=drops)
        self.assertIn("automated: pass+unqualified: pass", found)


class UnqualifiedIsTheSpinesWord(unittest.TestCase):
    """(2) `Verdict.unqualified` — the mark a claim reads Gap by — is set by the
    spine only. A gate cannot set it: `run_gate` clears it on whatever a gate
    returns, so a gate's own `unqualified` or its `error="not admitted: …"`
    reads as the crash it is (Skipped, errored), never the quieter Gap. And no
    stored verdict carries it: the remembered-outcome reader drops it."""

    def _ran(self, returned):
        reg, (spec, fn) = _one_gate(returned)
        return gates_mod.run_gate(spec, fn, _gate_ctx())

    def _reads_errored(self, verdict):
        found = claims_mod.compose(_claim(gates=[verdict.gate]), [verdict])
        return (found.status, found.cause, verdict.unqualified)

    def test_a_gate_that_marks_itself_reads_as_a_crash(self):
        errored = (ClaimStatus.BLOCKED, claims_mod.ClaimCause.ERRORED, "")
        # P2.3 (V15): the spine's text became `unqualified: <token>`, and the new
        # text is the new disguise — a gate wording its crash so still crashed.
        for returned in (Verdict(gate="g.strict", unqualified="x"),
                         {"pass": True, "unqualified": "x"},
                         Verdict(gate="g.strict", error="not admitted: x"),
                         Verdict(gate="g.strict", error="unqualified: known-good:fail"),
                         {"pass": False, "error": "unqualified: known-good:not-run",
                          "unqualified": "known-good:not-run"}):
            with self.subTest(returned=repr(returned)[:60]):
                verdict = self._ran(returned)
                self.assertEqual(verdict.outcome, "error")
                self.assertEqual(self._reads_errored(verdict), errored)

    def test_a_gate_that_marks_itself_outside_its_context_reads_as_a_crash(self):
        """(V16, P2.4) The operating-context mark is the spine's too: a gate that
        returns a pass carrying `context:outside|…` reads errored — `_stamp`
        clears the mark, and `Verdict.__post_init__` wrote the error when the gate
        built it — never the quieter Gap."""
        token = ('context:outside|{"hi":40.0,"key":"load_n","lo":0.0,"value":60,'
                 '"why":"outside"}')
        errored = (ClaimStatus.BLOCKED, claims_mod.ClaimCause.ERRORED, "")
        for returned in (Verdict(gate="g.strict", passed=True, unqualified=token),
                         {"pass": True, "unqualified": token}):
            with self.subTest(returned=repr(returned)[:60]):
                verdict = self._ran(returned)
                self.assertEqual(verdict.outcome, "error")
                self.assertEqual(self._reads_errored(verdict), errored)

    def test_a_marked_verdict_is_never_ok(self):
        """Degrade-closed (R-2): set without an error, the mark writes one."""
        v = Verdict(gate="g", passed=True, unqualified="x")
        self.assertFalse(v.ok)
        self.assertTrue(v.error)

    def test_a_remembered_outcome_never_carries_the_mark(self):
        from nopekit import verdicts as verdicts_mod
        root = tempfile.mkdtemp(prefix="nopekit-remembered-")
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        os.makedirs(os.path.join(root, ".nopekit", "cache"))
        crash = Verdict(gate="g.x", claims=["C1"], error="RuntimeError: boom")
        verdicts_mod.remember(root, "g.x", crash, input_rho="", kind="error", when="t")
        path = os.path.join(root, ".nopekit", "cache", "last_outcomes.json")
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        self.assertNotIn("unqualified", data["g.x"][""]["verdict"])
        # A hand-edited record — for a registered gate and for one no longer
        # registered (the orphan rung reads `remembered()` directly) — reads
        # without it.
        data["g.x"][""]["verdict"]["unqualified"] = "x"
        data["g.gone"] = {"": {"kind": "error", "verdict": dict(data["g.x"][""]["verdict"],
                                                              gate="g.gone"), "when": "t"}}
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        held = verdicts_mod.remembered(root)
        for key in ("g.x", "g.gone"):
            with self.subTest(key=key):
                self.assertEqual(held[key][""]["verdict"].unqualified, "")
                self.assertEqual(self._reads_errored(dataclasses.replace(
                    held[key][""]["verdict"], gate="g.x"))[:2],
                                 (ClaimStatus.BLOCKED, claims_mod.ClaimCause.ERRORED))

    def test_a_run_gate_that_keeps_the_mark_is_caught(self):
        """Planted: `run_gate` not clearing it — the gate's own mark then reads
        Gap, quieter than a crash."""
        real = gates_mod._stamp

        def keeps(verdict, spec, duration, cpu=0.0):
            stamped = real(verdict, spec, duration, cpu)
            return dataclasses.replace(stamped, unqualified=verdict.unqualified)

        with mock.patch.object(gates_mod, "_stamp", keeps):
            verdict = self._ran(Verdict(gate="g.strict", unqualified="x"))
        self.assertEqual(self._reads_errored(verdict)[:2],
                         (ClaimStatus.UNCLAIMED, claims_mod.ClaimCause.UNQUALIFIED))


# --------------------------------------------------------------------------- #
# Phase 1.0: the guards that carry the four properties above, each attacked.
# --------------------------------------------------------------------------- #
def _gate_ctx(root=".", params=None, extra=None):
    return gates_mod.GateContext(root=root, ledger=_ledger(_claim()), model=None,
                                 params=dict(params or {}), out_dir=root, tier=0,
                                 log=lambda _m: None, extra=dict(extra or {}))


def _one_gate(returned, *, gate_id="g.strict", **spec_kw):
    """A registry holding one gate that returns ``returned``, and its (spec, fn)."""
    reg = gates_mod.Registry()

    @gates_mod.gate(id=gate_id, claims=["C1"], registry=reg,
                    negative_control=NegativeControl(fixture="x:y"), **spec_kw)
    def strict(ctx):
        return returned

    return reg, reg.get(gate_id)


class _BoolKind:
    """A numpy.bool_ look-alike: 0-d, ``dtype.kind == "b"``. Built here so the
    spine's duck-typing is tested without numpy installed (CI has none)."""

    class dtype:                     # noqa: N801 - mirrors numpy's attribute name
        kind = "b"

    ndim = 0
    shape = ()

    def __init__(self, value):
        self._value = bool(value)

    def __bool__(self):
        return self._value

    def __repr__(self):
        return f"_BoolKind({self._value})"


class _FloatKind(_BoolKind):
    """0-d, but ``dtype.kind == "f"``: a number, not a flag."""

    class dtype:                     # noqa: N801
        kind = "f"


class PassMustBeABool(unittest.TestCase):
    """Failure to reject is not proof — and neither is a pass value nobody wrote.

    What got through: ``bool(data["passed"])``. ``{"passed": "false"}`` is a
    non-empty string, so it read True and rendered ``[ok]``; ``Verdict(passed="no")``
    was ``ok`` because ``ok`` was ``passed and ...``. And ``measured="n/a"`` was
    filed as a measurement, because the finiteness guard ``continue``d past anything
    ``float()`` refused.
    """

    JUNK = ("false", "no", None, 1.0, 2)

    def _through_every_shape(self, value):
        """The same pass value, returned in each shape a gate may use."""
        return {
            "Verdict": Verdict(gate="g.strict", passed=value),
            "dict passed": {"passed": value},
            "dict pass": {"pass": value},
            "dict ok": {"ok": value},
            "tuple": (value, "detail"),
        }

    def test_a_non_bool_pass_value_is_an_error_naming_its_type(self):
        for junk in self.JUNK:
            for shape, returned in self._through_every_shape(junk).items():
                with self.subTest(passed=junk, shape=shape):
                    _reg, (spec, fn) = _one_gate(returned)
                    v = gates_mod.run_gate(spec, fn, _gate_ctx())
                    self.assertFalse(v.ok, f"passed={junk!r} read as a pass")
                    self.assertIs(v.passed, False)
                    self.assertFalse(v.skipped)
                    self.assertIn(f"passed={junk!r}", v.error)
                    self.assertIn(f"({type(junk).__name__})", v.error)
                    self.assertIn("must say True or False", v.error)

    def test_a_hand_built_truthy_non_bool_is_not_ok(self):
        """The model half: a verdict that never went through run_gate (a ledger
        read, a test, a third-party caller) must not read as a pass either."""
        for junk in ("no", "false", 1, 2.0, _BoolKind(True)):
            with self.subTest(passed=junk):
                v = Verdict(gate="g.strict", passed=junk)
                self.assertFalse(v.ok)
                self.assertNotIn("[ok", v.render())

    def test_a_skip_carrying_junk_stays_a_skip(self):
        """A skip is already not-pass; turning it into an error would move a
        BLOCKED claim to FAIL for a field the gate did not mean."""
        for returned in (Verdict(gate="g.strict", passed="yes", skipped=True,
                                 skip_reason="no mesh in the projection"),
                         {"passed": "yes", "skipped": True, "skip_reason": "no mesh"}):
            with self.subTest(returned=type(returned).__name__):
                _reg, (spec, fn) = _one_gate(returned)
                v = gates_mod.run_gate(spec, fn, _gate_ctx())
                self.assertTrue(v.skipped)
                self.assertFalse(v.error)
                self.assertIs(v.passed, False)

    def test_real_bools_and_bool_kind_scalars_are_accepted(self):
        for flag in (True, False):
            for value in (flag, _BoolKind(flag)):
                for shape, returned in (("bare", value), *self._through_every_shape(value).items()):
                    with self.subTest(value=value, shape=shape):
                        _reg, (spec, fn) = _one_gate(returned)
                        v = gates_mod.run_gate(spec, fn, _gate_ctx())
                        self.assertFalse(v.error, v.error)
                        self.assertIs(type(v.passed), bool,
                                      "a bool-kind scalar must be stored as a builtin bool")
                        self.assertIs(v.passed, flag)
                        self.assertEqual(v.ok, flag)

    def test_a_number_kind_scalar_is_not_a_pass_value(self):
        _reg, (spec, fn) = _one_gate(Verdict(gate="g.strict", passed=_FloatKind(True)))
        v = gates_mod.run_gate(spec, fn, _gate_ctx())
        self.assertFalse(v.ok)
        self.assertIn("must say True or False", v.error)

    def test_a_non_number_measurement_is_an_error(self):
        for field in ("measured", "limit"):
            for junk in ("n/a", True, False, _BoolKind(True), [0.3]):
                with self.subTest(field=field, value=junk):
                    _reg, (spec, fn) = _one_gate(
                        Verdict(gate="g.strict", passed=True, **{field: junk}))
                    v = gates_mod.run_gate(spec, fn, _gate_ctx())
                    self.assertFalse(v.ok, f"{field}={junk!r} was filed as a measurement")
                    self.assertIn(f"{field}=", v.error)
                    self.assertIn(f"({type(junk).__name__})", v.error)
                    self.assertIn("must be a real number or None", v.error)
                    self.assertIsNone(getattr(v, field),
                                      "a refused measurement must not stay in the record")

    def test_real_numbers_are_accepted_and_stored_as_builtins(self):
        for value, kind in ((3, int), (0.42, float), (fractions.Fraction(1, 4), float)):
            with self.subTest(value=value):
                _reg, (spec, fn) = _one_gate(
                    Verdict(gate="g.strict", passed=True, measured=value, limit=value))
                v = gates_mod.run_gate(spec, fn, _gate_ctx())
                self.assertTrue(v.ok, v.error)
                self.assertIs(type(v.measured), kind)
                self.assertIs(type(v.limit), kind)
                self.assertEqual(v.measured, value)

    def test_nan_is_refused_and_removed_from_the_record(self):
        """Not only an error: the field is emptied, so no strict JSON writer is
        ever handed a NaN to choke on."""
        for field in ("measured", "limit"):
            for bad in (float("nan"), float("inf"), float("-inf")):
                with self.subTest(field=field, value=bad):
                    _reg, (spec, fn) = _one_gate(
                        Verdict(gate="g.strict", passed=True, **{field: bad}))
                    v = gates_mod.run_gate(spec, fn, _gate_ctx())
                    self.assertFalse(v.ok)
                    self.assertIn("non-finite", v.error)
                    self.assertIsNone(getattr(v, field))
                    json.dumps(v.to_dict(), allow_nan=False)


class AdmissionGuardsBite(unittest.TestCase):
    """Every guard between a gate and admission, attacked one at a time.

    Only the None-control refusal had a test. Each guard below could have been
    deleted by a refactor with the suite still green, and each one is the only
    thing standing between a logger and a green tick.
    """

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="nopekit-guards-")
        self.addCleanup(shutil.rmtree, self.root, True)
        os.makedirs(os.path.join(self.root, "selftest"))

    def _fixture(self, body, name="bad.py"):
        with open(os.path.join(self.root, "selftest", name), "w", encoding="utf-8") as fh:
            fh.write(body)
        return f"selftest/{name}"

    def _selftest(self, fn, fixture, *, expect="fail", **spec_kw):
        reg = gates_mod.Registry()
        gates_mod.gate(id="g.guarded", claims=["C1"], registry=reg,
                       negative_control=NegativeControl(fixture=fixture, expect=expect),
                       **spec_kw)(fn)
        spec, fn_ = reg.get("g.guarded")
        return gates_mod.selftest(spec, fn_, _gate_ctx(root=self.root))

    @staticmethod
    def _flags_bad(ctx):
        """An honest gate: fails exactly when the fixture planted the defect."""
        return (not ctx.extra.get("bad"), "planted defect" if ctx.extra.get("bad") else "fine")

    BAD = "def make(ctx):\n    return {'bad': True}\n"

    def test_the_positive_control_fires(self):
        """Without this, every refusal below could be the selftest refusing everything."""
        v = self._selftest(self._flags_bad, self._fixture(self.BAD))
        self.assertTrue(v.ok, v.detail or v.error)
        self.assertIn("correctly failed", v.detail)

    def test_a_fixtureless_control_is_refused_at_registration(self):
        for blank in ("", "   "):
            with self.subTest(fixture=repr(blank)):
                with self.assertRaises(NopekitError) as cm:
                    gates_mod.gate(id="g.blank", claims=["C1"], registry=gates_mod.Registry(),
                                   negative_control=NegativeControl(fixture=blank))(
                        lambda ctx: True)
                self.assertIn("no fixture", str(cm.exception))

    def test_an_expectation_other_than_fail_or_error_is_a_selftest_error(self):
        v = self._selftest(self._flags_bad, self._fixture(self.BAD), expect="pass")
        self.assertFalse(v.ok)
        self.assertIn("expect", v.error)

    def test_a_missing_fixture_file_is_not_ok(self):
        v = self._selftest(self._flags_bad, "selftest/nowhere.py")
        self.assertFalse(v.ok)
        self.assertFalse(v.skipped, "a missing control is not a missing tool")
        self.assertIn("does not exist", v.detail)

    def test_a_fixture_returning_none_is_not_ok(self):
        v = self._selftest(self._flags_bad, self._fixture("def make(ctx):\n    return None\n"))
        self.assertFalse(v.ok)
        self.assertFalse(v.skipped)
        self.assertIn("returned None", v.error)

    def test_a_gate_crashing_on_its_fixture_is_not_ok(self):
        def crashes_on_bad(ctx):
            if ctx.extra.get("bad"):
                raise ZeroDivisionError("tripped over the input")
            return True

        v = self._selftest(crashes_on_bad, self._fixture(self.BAD))
        self.assertFalse(v.ok)
        self.assertIn("CRASHED", v.detail)

    def test_a_gate_passing_its_fixture_is_not_ok(self):
        v = self._selftest(lambda ctx: True, self._fixture(self.BAD))
        self.assertFalse(v.ok)
        self.assertFalse(v.skipped)
        self.assertIn("PASSED its own known-bad", v.detail)

    def test_a_control_withdrawn_after_registration_is_an_error_in_the_sweep(self):
        for withdrawn in (None, NegativeControl(fixture="   ")):
            with self.subTest(control=withdrawn):
                reg, (_spec, fn) = _one_gate(True, gate_id="g.withdrawn")
                stored, stored_fn = reg._gates["g.withdrawn"]
                reg._gates["g.withdrawn"] = (
                    dataclasses.replace(stored, negative_control=withdrawn), stored_fn)
                [v] = gates_mod.run_all(reg, _gate_ctx())
                self.assertFalse(v.ok)
                self.assertIn("negative control", v.error)

    def test_editing_the_callers_spec_after_register_does_not_reach_the_registry(self):
        reg = gates_mod.Registry()
        spec = GateSpec(id="g.caller", claims=["C1"],
                        negative_control=NegativeControl(fixture="bad.py"))
        reg.register(spec, lambda ctx: True)
        spec.negative_control = None
        spec.claims.append("C_other")
        stored, _fn = reg.get("g.caller")
        self.assertIsNotNone(stored.negative_control)
        self.assertEqual(stored.claims, ["C1"])

    def test_a_self_skip_on_the_control_with_tools_present_is_not_ok(self):
        """What got through: a fixture that deleted a key its gate needs made the
        gate SKIP, and a skip was filed as "honestly blocked" — so the control
        counted as present while proving nothing, on a machine that had every tool
        the gate declares."""
        def skips_on_bad(ctx):
            if ctx.extra.get("bad"):
                return Verdict(gate="g.guarded", skipped=True,
                               skip_reason="the projection does not provide span_mm")
            return True

        v = self._selftest(skips_on_bad, self._fixture(self.BAD))
        self.assertFalse(v.ok)
        self.assertFalse(v.skipped, "a self-skip with its tools present was filed as a "
                                    "tooling skip, which `gate selftest` does not count "
                                    "as broken")
        self.assertIn("skipped on its own known-bad input while its tools are present",
                      v.error)
        self.assertIn("span_mm", v.error)

    def test_a_skip_for_missing_tooling_stays_a_skip(self):
        """The other side of the line: a tool that is not here is not the gate's fault."""
        v = self._selftest(self._flags_bad, self._fixture(self.BAD),
                           requires_tools=["nopekit-no-such-tool-7c1f"])
        self.assertTrue(v.skipped)
        self.assertFalse(v.error)
        self.assertFalse(v.ok)


class RegistryHandsOutCopies(unittest.TestCase):
    """Registration is an audit; it means nothing if the audited record can be
    edited afterwards. ``_own_copy`` closed the caller's handle, but ``specs()`` and
    ``get()`` handed out the stored spec itself, so ``specs()[0].claims.append(…)``
    widened what the next verdict settled — the ledger claiming coverage nobody
    registered."""

    def _reg(self):
        reg, _pair = _one_gate(True, gate_id="g.copied")
        return reg

    def _stored(self, reg):
        return reg._gates["g.copied"][0]

    def test_widening_claims_through_specs_does_not_reach_the_verdict(self):
        reg = self._reg()
        reg.specs()[0].claims.append("C_other")
        [v] = gates_mod.run_all(reg, _gate_ctx())
        self.assertEqual(v.claims, ["C1"])

    def test_every_exit_hands_out_a_copy(self):
        exits = {
            "get": lambda r: r.get("g.copied")[0],
            "specs": lambda r: r.specs()[0],
            "pairs": lambda r: r.pairs()[0][0],
            "for_claim": lambda r: r.for_claim("C1")[0],
            "by_tier": lambda r: r.by_tier(3)[0],
            "__iter__": lambda r: next(iter(r))[0],
        }
        for name, take in exits.items():
            with self.subTest(exit=name):
                reg = self._reg()
                handed = take(reg)
                handed.claims.append("C_other")
                handed.negative_control.fixture = "swapped.py"
                handed.tier = Tier.EXTERNAL
                stored = self._stored(reg)
                self.assertEqual(stored.claims, ["C1"])
                self.assertEqual(stored.negative_control.fixture, "x:y")
                self.assertEqual(stored.tier, Tier.INSTANT)
                handed.negative_control = None
                self.assertIsNotNone(self._stored(reg).negative_control)

    def test_set_pack_stamps_the_stored_spec(self):
        reg = self._reg()
        before = reg.get("g.copied")[0]
        self.assertEqual(before.pack, "")
        reg.set_pack("g.copied", "scratch-pack")
        self.assertEqual(reg.get("g.copied")[0].pack, "scratch-pack")
        self.assertEqual(before.pack, "", "set_pack reached a copy already handed out")

    def test_set_pack_refuses_an_unknown_gate(self):
        with self.assertRaises(KeyError):
            self._reg().set_pack("g.nowhere", "scratch-pack")

    def test_set_pack_survives_an_idempotent_re_register(self):
        reg = gates_mod.Registry()
        spec = GateSpec(id="g.reloaded", claims=["C1"],
                        negative_control=NegativeControl(fixture="bad.py"))

        def fn(ctx):
            return True

        reg.register(spec, fn)
        reg.set_pack("g.reloaded", "scratch-pack")
        reg.register(spec, fn)          # the same module imported a second time
        self.assertEqual(reg.get("g.reloaded")[0].pack, "scratch-pack")

    def test_load_gates_stamps_a_blank_pack_without_editing_a_spec(self):
        """A gate file that registers a spec with no pack gets the pack's name from
        ``load_gates`` — through the registry, not by writing into an object that
        somebody else may be holding."""
        name = f"scratch-{uuid.uuid4().hex[:8]}"
        gate_id = f"scratch{uuid.uuid4().hex[:6]}.blank"
        root = tempfile.mkdtemp(prefix="nopekit-setpack-")
        self.addCleanup(shutil.rmtree, root, True)
        pack_dir = os.path.join(root, ".nopekit", "packs", name)
        os.makedirs(os.path.join(pack_dir, "gates"))
        with open(os.path.join(pack_dir, "pack.json"), "w", encoding="utf-8") as fh:
            fh.write('{"name": "%s", "description": "scratch"}' % name)
        with open(os.path.join(pack_dir, "gates", "blank.py"), "w", encoding="utf-8") as fh:
            fh.write(
                "from nopekit import gates\n"
                "from nopekit.models import GateSpec, NegativeControl\n"
                f"SPEC = GateSpec(id={gate_id!r}, claims=['C1'],\n"
                "                negative_control=NegativeControl(fixture='x:y'))\n"
                "def check(ctx):\n"
                "    return True\n"
                "gates.active_registry().register(SPEC, check)\n")
        self.addCleanup(gates_mod.REGISTRY.unregister, gate_id)
        self.addCleanup(lambda: [sys.modules.pop(m) for m in list(sys.modules)
                                 if m.startswith("nopekit_pack_scratch")])

        reg = gates_mod.Registry()
        added = packs_mod.load_gates(name, reg, root=root)
        self.assertEqual([s.id for s in added], [gate_id])
        self.assertEqual(added[0].pack, name)
        self.assertEqual(reg.get(gate_id)[0].pack, name)
        module = next(m for k, m in sys.modules.items()
                      if k.startswith("nopekit_pack_scratch") and hasattr(m, "SPEC")
                      and m.SPEC.id == gate_id)
        self.assertEqual(module.SPEC.pack, "", "load_gates wrote into the gate file's spec")


if __name__ == "__main__":
    unittest.main(verbosity=2)
