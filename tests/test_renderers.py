# SPDX-License-Identifier: Apache-2.0
"""Every reader of a verdict says the same thing about it.

One fact — what happened when a gate ran — used to be re-derived in three
places: ``Verdict.ok``, ``Verdict.render`` and the claim ladder in
``claims.resolve_status``. They agreed only because nobody had yet written the
fourth. ``Verdict.outcome`` is now the one definition and these tests hold every
reader to it, over every combination of the three flags, so a new reader (JUnit,
the page, ``status --short``) can be added to the table instead of re-deriving.

And one explanation: when a claim fails, the gate cited as the reason is the one
that set the status. ``status`` used to cite a gate that SKIPPED for a missing
parameter as the reason a claim FAILED, while ``check`` cited the gate that ran
and measured 0.7 mm against 0.5 mm — two commands, two stories, one ledger.

Run:  PYTHONPATH=src python3 -m unittest discover -s tests -v
"""
from __future__ import annotations

import itertools
import unittest

from atompipe import claims as claims_mod
from atompipe.models import (
    Acceptance, Claim, ClaimKind, ClaimStatus, Comparator, Verdict,
)


def _claim(cid="C1", gates=("g.one", "g.two")):
    return Claim(
        id=cid, statement="the thing holds", kind=ClaimKind.MEASURABLE,
        acceptance=Acceptance(quantity="deflection", comparator=Comparator.LE,
                              limit=0.5, units="mm"),
        gates=list(gates),
    )


#: outcome -> (ok, render tag, the status a claim covered only by it resolves to).
#: Written out rather than computed: a table derived from the code under test
#: agrees with that code by construction.
EXPECTED = {
    "error":   (False, "[ERR ]", ClaimStatus.FAIL),
    "skipped": (False, "[skip]", ClaimStatus.BLOCKED),
    "pass":    (True,  "[ok  ]", ClaimStatus.PASS),
    "fail":    (False, "[FAIL]", ClaimStatus.FAIL),
}


class RenderersAgree(unittest.TestCase):

    def test_table(self):
        """All 8 combinations of (passed, skipped, error). Error beats skip beats
        the pass flag: a crash that also said skipped is still a crash, and a skip
        that also said passed is still not a pass."""
        for passed, skipped, error in itertools.product((False, True), (False, True),
                                                        ("", "ZeroDivisionError: x")):
            with self.subTest(passed=passed, skipped=skipped, error=error):
                v = Verdict(gate="g.one", claims=["C1"], passed=passed, skipped=skipped,
                            error=error, skip_reason="requires openfoam" if skipped else "")
                want = ("error" if error else "skipped" if skipped
                        else "pass" if passed else "fail")
                ok, tag, status = EXPECTED[want]
                self.assertEqual(v.outcome, want)
                self.assertEqual(v.ok, ok)
                self.assertEqual(v.ok, v.outcome == "pass")
                self.assertTrue(v.render().startswith(tag), v.render())
                self.assertEqual(claims_mod.resolve_status(_claim(gates=["g.one"]), [v]),
                                 status)

    def test_a_truthy_non_bool_is_a_fail_everywhere(self):
        """A record nobody stamped — read from a hand-edited file, built by a
        third-party caller — says ``passed: "yes"``. Every reader calls it a fail."""
        for junk in ("yes", "false", 1, 2.0, [True]):
            with self.subTest(passed=junk):
                v = Verdict(gate="g.one", claims=["C1"], passed=junk)
                self.assertEqual(v.outcome, "fail")
                self.assertFalse(v.ok)
                self.assertTrue(v.render().startswith("[FAIL]"), v.render())
                self.assertEqual(claims_mod.resolve_status(_claim(gates=["g.one"]), [v]),
                                 ClaimStatus.FAIL)


class ExplainingVerdict(unittest.TestCase):
    """The single ranked choice of the verdict that explains a claim's status."""

    FAIL = Verdict(gate="g.one", claims=["C1"], passed=False,
                   detail="0.700 mm at 15 N (limit 0.5 mm)")
    SKIP = Verdict(gate="g.two", claims=["C1"], passed=False, skipped=True,
                   skip_reason="the projection does not provide span_mm")
    ERROR = Verdict(gate="g.three", claims=["C1"], passed=False,
                    error="ZeroDivisionError: division by zero")
    PASS = Verdict(gate="g.four", claims=["C1"], passed=True)

    def _explain(self, verdicts):
        return claims_mod.explaining_verdict(
            _claim(gates=["g.one", "g.two", "g.three", "g.four"]), verdicts)

    def test_fail_beats_skip_in_both_orders(self):
        for order in ([self.SKIP, self.FAIL], [self.FAIL, self.SKIP]):
            with self.subTest(order=[v.gate for v in order]):
                self.assertIs(self._explain(order), self.FAIL)
                self.assertEqual(claims_mod.resolve_status(_claim(), order),
                                 ClaimStatus.FAIL)

    def test_fail_beats_error_beats_skip(self):
        for order in itertools.permutations([self.SKIP, self.ERROR, self.FAIL, self.PASS]):
            with self.subTest(order=[v.gate for v in order]):
                self.assertIs(self._explain(list(order)), self.FAIL)
        for order in itertools.permutations([self.SKIP, self.ERROR, self.PASS]):
            with self.subTest(order=[v.gate for v in order]):
                self.assertIs(self._explain(list(order)), self.ERROR)

    def test_a_skip_explains_only_when_nothing_else_does(self):
        self.assertIs(self._explain([self.PASS, self.SKIP]), self.SKIP)

    def test_stable_within_a_rank(self):
        second = Verdict(gate="g.two", claims=["C1"], passed=False, detail="also bad")
        self.assertIs(self._explain([self.FAIL, second]), self.FAIL)
        self.assertIs(self._explain([second, self.FAIL]), second)

    def test_nothing_to_explain(self):
        self.assertIsNone(self._explain([]))
        self.assertIsNone(self._explain([self.PASS]))

    def test_only_verdicts_that_cover_the_claim_count(self):
        elsewhere = Verdict(gate="g.one", claims=["C9"], passed=False, detail="not C1")
        self.assertIs(self._explain([elsewhere, self.SKIP]), self.SKIP)

    def test_a_tag_bound_verdict_counts(self):
        """Pack gates bind by tag, and their verdicts carry the tag, not the id."""
        tagged = Verdict(gate="pack.gate", claims=["stiffness"], passed=False,
                         detail="0.9 mm")
        claim = _claim()
        claim.tags = ["stiffness"]
        self.assertIs(claims_mod.explaining_verdict(claim, [self.SKIP, tagged]), tagged)


if __name__ == "__main__":
    unittest.main(verbosity=2)
