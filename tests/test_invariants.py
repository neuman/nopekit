# SPDX-License-Identifier: Apache-2.0
"""The honesty invariants.

These are not ordinary unit tests. Everything atompipe claims rests on four
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
import json
import os
import shutil
import sys
import tempfile
import unittest
import uuid

from atompipe import claims as claims_mod
from atompipe import gates as gates_mod
from atompipe import packs as packs_mod
from atompipe import report as report_mod
from atompipe.models import (
    Acceptance, Claim, ClaimKind, ClaimStatus, Comparator, GateSpec, Ledger,
    NegativeControl, PhysicalResult, ProjectMeta, Tier, Verdict,
)
from atompipe.util import AtompipeError


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

    def test_verdict_says_so_when_something_blocks(self):
        """The one-sentence verdict must lead with the problem, not bury it."""
        v = Verdict(gate="g.one", claims=["C1"], passed=False, detail="0.70 vs 0.50")
        md = self._render(_ledger(_claim(), verdicts=[v]), _Reg([SPEC]))
        head = md.split("## ")[0].lower()
        self.assertTrue(
            any(w in head for w in ("not ready", "fail", "block", "not verified",
                                    "gap", "outstanding", "unresolved", "never")),
            f"the headline verdict hid a failing critical claim: {head!r}")


class StatusPrecedence(unittest.TestCase):
    """The derivation table from SPINE_CONTRACT.md, exactly."""

    def test_assumption_is_asserted(self):
        c = _claim("C2", kind=ClaimKind.ASSUMPTION, gates=[])
        self.assertEqual(claims_mod.resolve_status(c, []), ClaimStatus.ASSERTED)

    def test_physical_with_result(self):
        c = _claim("C3", kind=ClaimKind.PHYSICAL, gates=[])
        c.physical_result = PhysicalResult(passed=True, when="2026-01-01")
        self.assertEqual(claims_mod.resolve_status(c, []), ClaimStatus.VERIFIED)
        c.physical_result = PhysicalResult(passed=False, when="2026-01-01")
        self.assertEqual(claims_mod.resolve_status(c, []), ClaimStatus.REFUTED)

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
        from atompipe.models import BLOCKING_STATUSES
        self.assertNotIn(ClaimStatus.PASS, BLOCKING_STATUSES)
        self.assertNotIn(ClaimStatus.VERIFIED, BLOCKING_STATUSES)
        self.assertIn(ClaimStatus.UNCLAIMED, BLOCKING_STATUSES)
        self.assertIn(ClaimStatus.BLOCKED, BLOCKING_STATUSES)
        self.assertIn(ClaimStatus.STALE, BLOCKING_STATUSES)


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
        self.root = tempfile.mkdtemp(prefix="atompipe-guards-")
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
                with self.assertRaises(AtompipeError) as cm:
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
                           requires_tools=["atompipe-no-such-tool-7c1f"])
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
        root = tempfile.mkdtemp(prefix="atompipe-setpack-")
        self.addCleanup(shutil.rmtree, root, True)
        pack_dir = os.path.join(root, ".atompipe", "packs", name)
        os.makedirs(os.path.join(pack_dir, "gates"))
        with open(os.path.join(pack_dir, "pack.json"), "w", encoding="utf-8") as fh:
            fh.write('{"name": "%s", "description": "scratch"}' % name)
        with open(os.path.join(pack_dir, "gates", "blank.py"), "w", encoding="utf-8") as fh:
            fh.write(
                "from atompipe import gates\n"
                "from atompipe.models import GateSpec, NegativeControl\n"
                f"SPEC = GateSpec(id={gate_id!r}, claims=['C1'],\n"
                "                negative_control=NegativeControl(fixture='x:y'))\n"
                "def check(ctx):\n"
                "    return True\n"
                "gates.active_registry().register(SPEC, check)\n")
        self.addCleanup(gates_mod.REGISTRY.unregister, gate_id)
        self.addCleanup(lambda: [sys.modules.pop(m) for m in list(sys.modules)
                                 if m.startswith("atompipe_pack_scratch")])

        reg = gates_mod.Registry()
        added = packs_mod.load_gates(name, reg, root=root)
        self.assertEqual([s.id for s in added], [gate_id])
        self.assertEqual(added[0].pack, name)
        self.assertEqual(reg.get(gate_id)[0].pack, name)
        module = next(m for k, m in sys.modules.items()
                      if k.startswith("atompipe_pack_scratch") and hasattr(m, "SPEC")
                      and m.SPEC.id == gate_id)
        self.assertEqual(module.SPEC.pack, "", "load_gates wrote into the gate file's spec")


if __name__ == "__main__":
    unittest.main(verbosity=2)
