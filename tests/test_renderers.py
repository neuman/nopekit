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

And one tag: a claim status is spelled by ``report.status_tag`` wherever it is
printed. ``check`` used to print ``[fail]`` and ``[uncl]`` for the claims
``status`` printed as ``[FAIL ]`` and ``[gap  ]``.

Run:  PYTHONPATH=src python3 -m unittest discover -s tests -v
"""
from __future__ import annotations

import itertools
import json
import os
import re
import shutil
import unittest
import xml.etree.ElementTree as ET

import _env
from atompipe import claims as claims_mod
from atompipe import cli as cli_mod
from atompipe import report as report_mod
from atompipe.models import (
    BLOCKING_STATUSES, Acceptance, Claim, ClaimKind, ClaimStatus, Comparator, GateSpec,
    Ledger, NegativeControl, ProjectMeta, Tier, Verdict,
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
#: agrees with that code by construction. A crash reads Skipped from P2.1
#: (`blocked`, cause errored — GLOSSARY §3; R-6, PLAN-v0.14 §3's P2 row names
#: this move), never FAIL: it failed nothing.
EXPECTED = {
    "error":   (False, "[ERR ]", ClaimStatus.BLOCKED),
    "skipped": (False, "[skip]", ClaimStatus.BLOCKED),
    "pass":    (True,  "[ok  ]", ClaimStatus.PASS),
    "fail":    (False, "[FAIL]", ClaimStatus.FAIL),
}

#: outcome -> (the JUnit child of the gate's testcase, the JUnit child of the
#: critical claim it alone covers); None is a childless testcase. Written out for
#: the same reason as EXPECTED. A skip blocks the claim, so the claim is red where
#: the gate is only skipped; a crash is an `error` on both.
JUNIT = {
    "error":   ("error", "error"),
    "skipped": ("skipped", "failure"),
    "pass":    (None, None),
    "fail":    ("failure", "failure"),
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

    def test_junit(self):
        """`check --junit` is the fourth reader the table above was written for.
        Its testcase for a gate, and for the one critical claim that gate covers,
        say what `outcome` says for all 8 combinations — childless only for a
        pass, and never for a skip that also said `passed=True`."""
        spec = GateSpec(id="g.one", claims=["C1"], tier=Tier.INSTANT,
                        negative_control=NegativeControl(fixture="x:y"))
        for passed, skipped, error in itertools.product((False, True), (False, True),
                                                        ("", "ZeroDivisionError: x")):
            with self.subTest(passed=passed, skipped=skipped, error=error):
                v = Verdict(gate="g.one", claims=["C1"], passed=passed, skipped=skipped,
                            error=error, skip_reason="requires openfoam" if skipped else "")
                ledger = Ledger(meta=ProjectMeta(name="t", revision="v0.1"),
                                claims=[_claim(gates=["g.one"])], verdicts=[v])
                blockers = claims_mod.blocking(ledger, _Specs([spec]))
                root = ET.fromstring(report_mod.render_junit(
                    ledger, [v], _Specs([spec]), tier=0, ready=not blockers,
                    exit_code=1 if blockers else 0, when="2026-09-27T00:00:00Z"))
                kinds = []
                for suite, name in (("gates", "g.one"), ("claims.critical", "C1")):
                    case = root.find(f"testsuite[@name='{suite}']/testcase[@name='{name}']")
                    self.assertIsNotNone(case, f"{suite} has no testcase {name}")
                    children = [c.tag for c in case]
                    self.assertLessEqual(len(children), 1, children)
                    kinds.append(children[0] if children else None)
                ok, _tag, status = EXPECTED[v.outcome]
                self.assertEqual(tuple(kinds), JUNIT[v.outcome])
                self.assertEqual(kinds[0] is None, ok)
                self.assertEqual(kinds[1] in ("failure", "error"),
                                 status in BLOCKING_STATUSES)

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
                self.assertEqual(claims_mod.compose(_claim(gates=["g.one"]), [v]).cause,
                                 claims_mod.ClaimCause.FAILED)


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


class _Specs(list):
    """A registry stand-in: `report` and `claims` read nothing but `.specs()`, so
    no `gates.Registry` — and no global one — is needed to render a claim."""

    def specs(self):
        return list(self)


#: The gates behind `ReasonsAgree`'s claim.
_SPECS = _Specs(GateSpec(id=gid, claims=["C1"], tier=Tier.INSTANT,
                         negative_control=NegativeControl(fixture="x:y"))
                for gid in ("g.one", "g.two", "g.three"))

#: A claim line of `render_terminal`: a five-wide status tag, then the claim id.
_CLAIM_LINE = re.compile(r"^\[.{5}\] (?P<id>\S+) ")


class ReasonsAgree(unittest.TestCase):
    """`status` (via `report.render_terminal`) and `check` (via
    `cli._blocking_reason`) cite the same gate, in the same words, for one claim.

    What slipped through (S-68): `check`'s private ranking had been fixed to
    prefer the gate that ran and failed, and `report._terminal_reason` still cited
    the first non-passing verdict — a pack gate that SKIPPED for a missing
    parameter — so the two commands told two stories about one ledger.
    """

    FAIL = ExplainingVerdict.FAIL
    SKIP = ExplainingVerdict.SKIP
    ERROR = ExplainingVerdict.ERROR

    def _reasons(self, verdicts):
        claim = _claim(gates=[s.id for s in _SPECS])
        ledger = Ledger(meta=ProjectMeta(name="t", revision="v0.1"), claims=[claim],
                        verdicts=list(verdicts))
        status = claims_mod.resolve_status(claim, ledger.verdicts)
        cover = claims_mod.effective_gates(ledger, _SPECS)
        rows = [line for line in report_mod.render_terminal(ledger, _SPECS).splitlines()
                if (m := _CLAIM_LINE.match(line)) and m.group("id") == "C1"]
        self.assertEqual(len(rows), 1, rows)
        return (status,
                cli_mod._blocking_reason(ledger, claim, status),
                report_mod._terminal_reason(ledger, claim, status, cover),
                rows[0])

    def test_status_check_and_report_cite_the_failing_gate(self):
        cases = {
            "a fail beats a skip": ([self.SKIP, self.FAIL], ClaimStatus.FAIL,
                                    "g.one : 0.700 mm at 15 N (limit 0.5 mm)"),
            # P2.1 (R-6): a crash beside a skip reads Skipped, its reason led
            # `errored:` — still the crash cited, never the skip (S-68).
            "an error beats a skip": ([self.SKIP, self.ERROR], ClaimStatus.BLOCKED,
                                      "errored: g.three : ZeroDivisionError: division by zero"),
            "a fail with nothing to say": ([self.SKIP, Verdict(gate="g.one", claims=["C1"],
                                                               passed=False)],
                                           ClaimStatus.FAIL, "g.one did not pass"),
        }
        for name, (verdicts, want_status, want) in cases.items():
            for order in (verdicts, verdicts[::-1]):
                with self.subTest(name, order=[v.gate for v in order]):
                    status, check, status_reason, row = self._reasons(order)
                    self.assertEqual(status, want_status)
                    self.assertEqual(check, want, "check cites another reason")
                    self.assertEqual(status_reason, want, "status cites another reason")
                    errored = claims_mod.compose(
                        _claim(gates=[s.id for s in _SPECS]), order).errored
                    self.assertTrue(row.startswith(
                        report_mod.status_tag(status, errored=errored) + " C1 "), row)
                    self.assertTrue(row.endswith(" — " + want), row)
                    self.assertNotIn("g.two", row, "the skip was cited over the failure")


class VerdictRowSaysOutcome(unittest.TestCase):
    """`check --json` carries `outcome` beside `ok`, from `Verdict.outcome`.

    `_verdict_row` serialises dataclass fields, and `outcome` is a property: a row
    without it leaves every JSON consumer to re-derive the four-way answer from
    three flags, which is the re-derivation this file exists to stop."""

    def test_all_eight_combinations(self):
        for passed, skipped, error in itertools.product((False, True), (False, True),
                                                        ("", "ZeroDivisionError: x")):
            with self.subTest(passed=passed, skipped=skipped, error=error):
                v = Verdict(gate="g.one", claims=["C1"], passed=passed, skipped=skipped,
                            error=error)
                row = cli_mod._verdict_row(v)
                self.assertEqual(row["outcome"], v.outcome)
                self.assertEqual(row["ok"], row["outcome"] == "pass")
                json.dumps(row, allow_nan=False)


class BlockingTagIsStatusTag(_env.EnvCase):
    """`check`'s BLOCKING list spells a claim status the way `status` does.

    What slipped through (S-69): `check` built its tag from the first four letters
    of the status — `[fail]`, `[uncl]` — while `status` and the report printed
    `report.status_tag`'s `[FAIL ]`, `[gap  ]`: two spellings of one status, one
    screen apart, so a reader had to learn that `uncl` and `gap` were the same.
    """

    def test_each_blocking_status(self):
        claim = _claim()
        for status in sorted(BLOCKING_STATUSES, key=str):
            with self.subTest(status=str(status)):
                self.assertEqual(cli_mod._blocking_line(claim, status, "the reason"),
                                 f"{report_mod.status_tag(status)} C1 the thing holds"
                                 f" — the reason")
        # A crash's Skipped carries the loud tag (P2.1, invariant 2).
        self.assertTrue(cli_mod._blocking_line(claim, ClaimStatus.BLOCKED, "r", errored=True)
                        .startswith("[SKIP ] C1 "))

    def test_check_prints_its_blockers_with_it(self):
        """The helper is what `check` prints: every line under BLOCKING in a real
        run starts with `status_tag` of the status `check --json` reports for that
        claim. A copy of the bracket, so the tracked example is never written."""
        project = os.path.join(self.tmp(), "bracket")
        shutil.copytree(os.path.join(_env.REPO, "examples", "bracket"), project,
                        ignore=shutil.ignore_patterns("__pycache__"))
        text = _env.atompipe(["check", "--no-record"], cwd=project)
        data = _env.atompipe(["check", "--json", "--no-record"], cwd=project)
        self.assertEqual((text.returncode, data.returncode), (1, 1),
                         text.stdout + text.stderr + data.stderr)
        blocking = json.loads(data.stdout)["blocking"]
        self.assertTrue(blocking, "the bracket fails on purpose; nothing blocked")
        lines = text.stdout.splitlines()
        heads = [i for i, line in enumerate(lines) if line.startswith("BLOCKING — ")]
        self.assertEqual(len(heads), 1, text.stdout)
        printed = lines[heads[0] + 1:heads[0] + 1 + len(blocking)]
        self.assertEqual(len(printed), len(blocking), text.stdout)
        # Matched by claim, not position: from P2.1 the text is in severity
        # order and `--json` in record order (R-6, stronger: every row matched,
        # and its crash mark read beside its status).
        by_claim = {_CLAIM_LINE.match(line).group("id"): line for line in printed}
        self.assertEqual(sorted(by_claim), sorted(row["claim"] for row in blocking))
        for row in blocking:
            with self.subTest(claim=row["claim"]):
                tag = report_mod.status_tag(ClaimStatus(row["status"]),
                                            errored=bool(row["errored"]))
                self.assertTrue(by_claim[row["claim"]].startswith(f"{tag} {row['claim']} "),
                                by_claim[row["claim"]])


if __name__ == "__main__":
    unittest.main(verbosity=2)
