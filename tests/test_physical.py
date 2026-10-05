# SPDX-License-Identifier: Apache-2.0
"""Invariant 11, the result half, and invariants 4 and 7 over a physical result
(P2.5a): a physical pass counts only bound to its article and to the claim as
the person read it, with its evidence unchanged; a physical fail never loses its
power to fail; a fail on a claim an evaluator had passed is a contradiction on
that evaluator's track record.

What slipped through before this file: a pass typed one second after a fail read
Checked (review of P2.1); a pass survived any change to the design it was
tested on (S-50); a `claim physical` pass on C5 read "verified" in the same
second it was claimed, with nothing written down that a result could fail
(S-48); and E4 could not be run at all — a physical result was refused on a
claim an automated evaluator settles (PLAN-v0.14 §1.2).

Classes, each with planted violators (R-12), named as the P2.5a design names
them: C-5 ``TheClaimDigestIgnoresAbsentFields``, C-6
``TodaysPhysicalWordsArePinned``, C-7 ``LegacyResultsReadAsTheyDid``; V-5
``SignedMeansSomething``, V-6 ``APhysicalFailNeverLosesItsPowerToFail``, V-7
``AMovedArticleReadsStale``, V-8 ``RebuildPredictionIsExact``, V-9
``TheCheckedSectionHoldsOnlyBoundResults``, V-10
``AContradictionGoesOnTheEvaluatorsTrackRecord``, V-11
``AnExpertJudgmentStaysWithItsAuthority``, V-12 ``ATerminalCannotLowerTheBar``,
V-13 ``MeasuredMustAgree``, V-14 ``RenderersAgreeOnPhysicalClaims``, V-18
``APhysicalResultIsNoTier``.

Run:  PYTHONPATH=src python3 -m unittest discover -s tests -p test_physical.py -v
"""
from __future__ import annotations

import ast
import dataclasses
import json
import os
import re
import tempfile
import unittest
import xml.etree.ElementTree as ET
from typing import Any
from unittest import mock

import _env
import _physical as P
import _projects
from atompipe import claims, report, store, verdicts
from atompipe.models import (Acceptance, Claim, ClaimKind, ClaimStatus, Ledger,
                             PhysicalResult, Verdict)
from atompipe.util import AtompipeError, sha256_text


def _need(module: Any, name: str) -> Any:
    found = getattr(module, name, None)
    if found is None:
        raise AssertionError(f"{module.__name__}.{name} does not exist yet")
    return found


def _status(root: str, cid: str) -> tuple[str, str, str]:
    """``(status value, cause, reason)`` of ``cid`` from ``status --json``."""
    doc = json.loads(P.run(root, "status", "--json", code=0).stdout)
    view = doc["statuses"][cid]
    return doc["claims"][cid], view["cause"], view["reason"]


def _pass(root: str, cid: str, *args: str, answer: str | None = None) -> Any:
    """An `interactive` pass on ``cid``: typed in a pty, the id confirmed."""
    extra = ["--evidence", P.EVIDENCE] if not any(a == "--evidence" for a in args) else []
    return P.tty(root, "claim", "physical", cid, "pass", "--detail", "no cracking",
                 *extra, *args, answer=answer or cid, code=0)


def _fail(root: str, cid: str, *args: str, agent: bool = False, tty: bool = False) -> Any:
    if tty:
        return P.tty(root, "claim", "physical", cid, "fail", "--detail", "hairline cracks",
                     *args, answer=cid, code=0)
    return P.run(root, "claim", "physical", cid, "fail", "--detail", "hairline cracks",
                 *args, agent=agent, code=0)


def _append(path: str, text: str) -> None:
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(text)


def _set_model_comment(root: str, text: str) -> None:
    path = os.path.join(root, "model", "bracket.py")
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(f"\n# {text}\n")


def _break_model(root: str) -> None:
    path = os.path.join(root, "model", "bracket.py")
    with open(path, "a", encoding="utf-8") as fh:
        fh.write("\nraise RuntimeError('mid-edit')\n")


def _restore_model(root: str, text: str) -> None:
    with open(os.path.join(root, "model", "bracket.py"), "w", encoding="utf-8") as fh:
        fh.write(text)


def _model_text(root: str) -> str:
    with open(os.path.join(root, "model", "bracket.py"), encoding="utf-8") as fh:
        return fh.read()


# --------------------------------------------------------------------------- #
# C-5, C-6, C-7 — characterization, green before the change and after it
# --------------------------------------------------------------------------- #
#: Each bracket claim's ``verdicts._claim_digest``, computed on ``d23ff8e``. A
#: moved digest re-keys every gate that reads a claim: the new fields, absent or
#: empty, and the in-memory ones must move none.
CLAIM_DIGESTS = {
    "C1": "36f4f6da39bb0dc2c975ad542895f85a53c327a4bc4a47b85ca6bae5aa5aefcf",
    "C2": "4a1d293d7bcde39b0ad28c83586994b1d3337a868ba833f0420f4ff092f3e2dd",
    "C3": "4352a59a91d9008a6c39bbbba9797921b72b14bbdb0289408378ee7e0136d8bc",
    "C4": "c226f88a343b334a80c6ee1bfc26b2c7773c635a36f6ef29b6917caaf06e7105",
    "C5": "484a3d26e8e097dc44d3be7966981360dfca077c76b9cdf00247c19902095604",
    "C6": "5f90b61d4c22866028669020871a92881a7f99e36ba38436466d6ac7baf699be",
    "C7": "55d2b65840685a8a18a47205447571721e195b33b3cef5baf34c89e206321685",
}


class TheClaimDigestIgnoresAbsentFields(unittest.TestCase):
    """(C-5) ``terminal`` and ``authority`` absent or empty, and the in-memory
    ``results``, ``attributions`` and ``standing``, move no claim digest."""

    def test_the_bracket_claims_digest_as_on_d23ff8e(self):
        ledger = store.load(os.path.join(_env.REPO, "examples", "bracket"))
        got = {c.id: verdicts._claim_digest(c) for c in ledger.claims}
        self.assertEqual(got, CLAIM_DIGESTS)
        fields = {f.name for f in dataclasses.fields(Claim)}
        for claim in ledger.claims:
            # P2.5b (R-6, a strengthening): `expected_latency` empty moves none.
            extra = {name: value for name, value in (("terminal", ""), ("authority", ""),
                                                     ("expected_latency", {}))
                     if name in fields}
            with self.subTest(claim.id):
                self.assertEqual(verdicts._claim_digest(dataclasses.replace(claim, **extra)),
                                 CLAIM_DIGESTS[claim.id])

    def test_a_named_terminal_moves_it(self):
        claim = store.load(os.path.join(_env.REPO, "examples", "bracket")).claim("C5")
        if "terminal" not in {f.name for f in dataclasses.fields(Claim)}:
            self.fail("Claim.terminal does not exist yet")
        moved = dataclasses.replace(claim, terminal="human", authority="Dana")
        self.assertNotEqual(verdicts._claim_digest(moved), CLAIM_DIGESTS["C5"])


class TodaysPhysicalWordsArePinned(unittest.TestCase):
    """(C-6) A claim built in memory with a pass and no standing — a raw
    ``store.load`` reader — reads exactly as on ``d23ff8e`` (P2.5a-D13)."""

    def test_a_raw_pass_reads_pending_build_in_todays_words(self):
        ledger = Ledger()
        for who, recorded in (("", "recorded, unattributed"), ("sam", "recorded by sam")):
            with self.subTest(who=who):
                claim = Claim(id="C5", statement="s", kind=ClaimKind.PHYSICAL,
                              physical_result=PhysicalResult(passed=True, who=who))
                found = claims.compose(claim, [])
                self.assertEqual((found.status, found.cause.value),
                                 (ClaimStatus.UNVERIFIED, "physical-pass"))
                self.assertEqual(report.reason(found, ledger, claim),
                                 f"a pass {recorded}, not bound to an article")


class LegacyResultsReadAsTheyDid(_env.EnvCase):
    """(C-7) A results file in P2.1's shape — no channel, no seal — reads as it
    did: a legacy fail counts, a legacy pass never does."""

    def test_legacy_results(self):
        root = P.project(os.path.join(self.tmp(), "b"))
        P.write_json(os.path.join(root, "results", "C5.json"), {"results": [
            {"passed": False, "when": "2026-01-01", "who": "lab", "detail": "cracked"},
            {"passed": True, "when": "2026-02-01", "who": "lab", "detail": "fine"}]})
        P.write_json(os.path.join(root, "results", "C9.json"), {"results": [
            {"passed": True, "when": "2026-02-01", "who": "lab", "detail": "fine"}]})
        P.write_json(os.path.join(root, "claims", "C9.json"), P.PLANTED["C9"])
        P.write_json(os.path.join(root, "results", "C1.json"), {"results": [
            {"passed": False, "when": "2026-01-01", "who": "lab", "detail": "sagged"}]})
        doc = json.loads(P.run(root, "status", "--json", code=0).stdout)
        got = {cid: (doc["claims"][cid], doc["statuses"][cid]["cause"])
               for cid in ("C1", "C5", "C9")}
        self.assertEqual(got, {"C1": ("refuted", "physical-fail"),
                               "C5": ("refuted", "physical-fail"),
                               "C9": ("unverified", "physical-pass")})
        rep = json.loads(P.run(root, "report", "--json", code=0).stdout)
        self.assertEqual({cid: rep["claims"][cid] for cid in got},
                         {cid: pair[0] for cid, pair in got.items()})
        show = json.loads(P.run(root, "claim", "show", "C9", "--json", code=0).stdout)
        self.assertEqual((show["status"], show["cause"]), got["C9"])
        P.run(root, "check", "--junit", code=1)
        tree = ET.parse(os.path.join(root, ".atompipe", "out", "junit.xml")).getroot()
        kinds = {c.get("name"): [k.tag for k in c] for c in tree.iter("testcase")
                 if (c.get("classname") or "").startswith("claims")}
        self.assertEqual(kinds["C5"], ["failure"])
        self.assertEqual(kinds["C9"], ["skipped"])


# --------------------------------------------------------------------------- #
# V-5 — signed means something
# --------------------------------------------------------------------------- #
class SignedMeansSomething(_env.EnvCase):
    """(V-5, invariant 11) A pass counts — Checked, ``on-article`` — only when
    every fact holds: ``interactive``, a ``who``, the evidence's bytes as
    recorded, the measured value consistent, the claim as the person read it,
    and the article current. One row per fact, everything else valid, each with
    the judge's check of that fact stubbed out as its violator."""

    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.mkdtemp(prefix="atompipe-signed-")
        cls.root = P.project(os.path.join(cls.tmpdir, "b"), planted=("C9",))
        P.edit_claim(cls.root, "C9", acceptance={"quantity": "hook sag", "comparator": "<=",
                                                 "limit": 0.5, "units": "mm"})

    @classmethod
    def tearDownClass(cls):
        _env._rmtree(cls.tmpdir)

    def _entry(self, cid: str = "C5", **changes: Any) -> dict:
        """A complete `interactive` pass entry for ``cid`` on the current article,
        as the channel would build it — then ``changes`` applied."""
        view, resolution = P.resolved(self.root)
        claim = view.claim(cid)
        model_, projection, _err = __import__("atompipe.cli", fromlist=["cli"])._projection_safe(
            self.root, store.load(self.root))
        article = _need(verdicts, "article_of")(self.root, projection, model_,
                                                anchors=resolution.anchors,
                                                resolution=resolution)
        evidence = {P.EVIDENCE: __import__("atompipe.models", fromlist=["m"]).sha256_file(
            os.path.join(self.root, P.EVIDENCE))}
        entry = {"passed": True, "when": "2026-10-04T10:00:00Z", "who": P.WHO,
                 "detail": "no cracking", "evidence": [P.EVIDENCE], "channel": "interactive",
                 "authority": "", "measured": None, "units": "", "article": article,
                 "claim_digest": _need(claims, "claim_digest")(claim), "rho": "",
                 "evidence_sha256": evidence, "contradicts": [], "contradiction_check": ""}
        entry.update(changes)
        return entry

    def _record(self, entry: dict, cid: str = "C5") -> None:
        path = os.path.join(self.root, "results", f"{cid}.json")
        if os.path.exists(path):
            os.remove(path)
        _need(store, "append_signed")(self.root, cid, "results", entry)

    def _reads(self, cid: str = "C5") -> tuple[str, str, str]:
        found = P.composed(self.root, cid)
        view, _res = P.resolved(self.root)
        return (found.status.value, found.cause.value,
                report.reason(found, view, view.claim(cid), full=True))

    def test_a_every_fact_holds(self):
        self._record(self._entry())
        self.assertEqual(self._reads()[:2], ("verified", "on-article"))

    def test_b_c_d_the_channel(self):
        for channel, words in (("agent-session s1", "from an agent session"),
                               ("non-interactive", "from a pipe or a script"),
                               (None, "before results were bound to articles")):
            with self.subTest(channel=channel):
                entry = self._entry(channel=channel or "")
                if channel is None:
                    entry = {k: entry[k] for k in ("passed", "when", "who", "detail",
                                                   "evidence")}
                self._record(entry)
                status, cause, reason = self._reads()
                self.assertEqual((status, cause), ("unverified", "physical-pass"))
                self.assertIn(words, reason)
                if channel is None:
                    continue              # a legacy entry carries no article either
                with mock.patch.object(verdicts, "_fact_channel", lambda entry: ""):
                    self.assertEqual(self._reads()[:2], ("verified", "on-article"),
                                     "the judge ignoring the channel: the violator")

    def test_e_evidence_changed_then_restored(self):
        self._record(self._entry())
        path = os.path.join(self.root, P.EVIDENCE)
        with open(path, "rb") as fh:
            original = fh.read()
        try:
            with open(path, "wb") as fh:
                fh.write(b"another photo")
            status, cause, reason = self._reads()
            self.assertEqual((status, cause), ("unverified", "physical-pass"))
            self.assertIn(P.EVIDENCE, reason)
            with mock.patch.object(verdicts, "_fact_evidence", lambda *a, **k: ""):
                self.assertEqual(self._reads()[:2], ("verified", "on-article"))
        finally:
            with open(path, "wb") as fh:
                fh.write(original)
        self.assertEqual(self._reads()[:2], ("verified", "on-article"))

    def test_f_no_one_recorded_it(self):
        self._record(self._entry(who=""))
        self.assertEqual(self._reads()[:2], ("unverified", "physical-pass"))
        with mock.patch.object(verdicts, "_fact_who", lambda entry: ""):
            self.assertEqual(self._reads()[:2], ("verified", "on-article"))

    def test_g_a_raw_ledger_never_judges(self):
        self._record(self._entry())
        raw = store.load(self.root).claim("C5")
        found = claims.compose(raw, [])
        self.assertEqual((found.status.value, found.cause.value),
                         ("unverified", "physical-pass"))
        self.assertEqual(report.reason(found, Ledger(), raw),
                         f"a pass recorded by {P.WHO}, not bound to an article")

    def test_h_a_covering_fail_outranks_a_counting_pass(self):
        P.edit_claim(self.root, "C5", tags=["stiffness"])
        try:
            self._record(self._entry())
            P.run(self.root, "check")
            self.assertEqual(self._reads()[:2], ("fail", "failed"))
        finally:
            P.edit_claim(self.root, "C5", tags=None)

    def test_i_the_claim_half_moved(self):
        self._record(self._entry())
        P.edit_claim(self.root, "C5", note=None)
        try:
            status, cause, reason = self._reads()
            self.assertEqual((status, cause), ("stale", "claim-moved"))
            self.assertIn("test it again", reason)
            with mock.patch.object(verdicts, "_fact_claim", lambda *a, **k: ""):
                self.assertEqual(self._reads()[:2], ("verified", "on-article"))
        finally:
            P.edit_claim(self.root, "C5", note=P.C5_NOTE)

    def test_j_a_measured_value_its_acceptance_refutes(self):
        self._record(self._entry("C9", measured=0.7, units="mm", evidence=[P.EVIDENCE]),
                     cid="C9")
        status, cause, _reason = self._reads("C9")
        self.assertEqual((status, cause), ("unverified", "physical-pass"))
        with mock.patch.object(verdicts, "_fact_measured", lambda *a, **k: ""):
            self.assertEqual(self._reads("C9")[:2], ("verified", "on-article"))

    def test_k_a_limit_edited_after_a_measured_pass(self):
        """(review of P2.5a) A measured pass, then its limit tightened: the claim
        moved — Stale `claim-moved`, "test it again" — as it reads when the limit
        is loosened. What slipped through: the value was judged against the
        CURRENT limit first, so a tightened one read the pass "not counted",
        Pending build, which never stops `check`. Planted: P2.5a's order, the
        value before the claim."""
        acceptance = {"quantity": "hook sag", "comparator": "<=", "limit": 0.5, "units": "mm"}
        self._record(self._entry("C9", measured=0.4, units="mm"), cid="C9")
        try:
            self.assertEqual(self._reads("C9")[:2], ("verified", "on-article"))
            for limit in (0.3, 0.8):
                with self.subTest(limit=limit):
                    P.edit_claim(self.root, "C9", acceptance=dict(acceptance, limit=limit))
                    status, cause, reason = self._reads("C9")
                    self.assertEqual((status, cause), ("stale", "claim-moved"), reason)
            P.edit_claim(self.root, "C9", acceptance=dict(acceptance, limit=0.3))
            from atompipe.models import EntryStanding

            def measured_first(index, entry, claim, terminal, digest_now, here):
                article = getattr(entry, "article", None) or {}
                found = str(article.get("hash") or "")
                state, moved = verdicts._article_moves(article, here) if found else ("", ())
                why = (verdicts._fact_terminal(terminal) or verdicts._fact_channel(entry)
                       or verdicts._fact_who(entry)
                       or verdicts._fact_authority(entry, claim, terminal)
                       or verdicts._fact_measured(entry, claim))
                if not why:
                    why = ("article-moved" if state == "moved" else "article-unjudged"
                           if state != "current" else verdicts._fact_claim(entry, digest_now))
                return EntryStanding(index, True, not why, why, found, state, moved)

            with mock.patch.object(verdicts, "_judge_entry", measured_first):
                self.assertEqual(self._reads("C9")[:2], ("unverified", "physical-pass"),
                                 "the value-first judge was not caught")
        finally:
            P.edit_claim(self.root, "C9", acceptance=acceptance)

    def test_end_to_end_a_typed_pass_reads_checked_everywhere(self):
        root = P.project(os.path.join(self.tmp(), "b"))
        _pass(root, "C5")
        found = P.channels(root, "C5")
        self.assertEqual(P.disagreements(found, "verified", "on-article"), [])
        self.assertEqual(found["junit"], [])
        proven = found["report.text"].split(report.SECTION_PROVEN, 1)[1].split("\n## ", 1)[0]
        self.assertIn("C5", proven)


# --------------------------------------------------------------------------- #
# V-6 — a physical fail never loses its power to fail
# --------------------------------------------------------------------------- #
class APhysicalFailNeverLosesItsPowerToFail(_env.EnvCase):
    """(V-6, invariant 11, R-3) Every fail counts — any channel, legacy or
    sealed — across a later pass, a design nudge, and any edit of the claim."""

    def test_in_process_every_fail_counts(self):
        """The composition over a fail and a later counting pass, every channel,
        every kind and terminal the claim could be edited to: Failing each time,
        and the latest-result reading (P2.1's before its review) caught."""
        from atompipe.models import EntryStanding, Standing
        later = PhysicalResult(passed=True, who=P.WHO, channel="interactive",
                               article={"hash": "a" * 64})
        counted = Standing("current", 1, "a" * 64, (),
                           (EntryStanding(0, False, False, "", "a" * 64, "current"),
                            EntryStanding(1, True, True, "", "a" * 64, "current")))
        edits = {"physical": dict(kind=ClaimKind.PHYSICAL, note="n"),
                 "measurable": dict(kind=ClaimKind.MEASURABLE),
                 "assumption": dict(kind=ClaimKind.ASSUMPTION, owner="Sam", rationale="r"),
                 "expert judgment": dict(kind=ClaimKind.ASSUMPTION, terminal="human",
                                         authority="Dana", rationale="r")}
        for channel in ("interactive", "agent-session s1", "non-interactive", ""):
            fail = PhysicalResult(passed=False, who=P.WHO, channel=channel, detail="cracked")
            for name, fields in edits.items():
                with self.subTest(channel=channel, claim=name):
                    claim = Claim(id="C5", statement="s", results=(fail, later),
                                  physical_result=store._counting_result([fail, later]),
                                  standing=counted, **fields)
                    self.assertEqual(claims.compose(claim, []).status, ClaimStatus.REFUTED)
        latest = (lambda items: items[-1] if items else None)
        with mock.patch.object(store, "_counting_result", latest):
            claim = Claim(id="C5", statement="s", kind=ClaimKind.PHYSICAL, note="n",
                          results=(fail, later),
                          physical_result=store._counting_result([fail, later]),
                          standing=counted)
        self.assertNotEqual(claims.compose(claim, []).status, ClaimStatus.REFUTED,
                            "the latest-result violator was not caught")

    def test_in_process_a_fail_outlives_its_claim_file(self):
        """(review of P2.5a, R-3) A claim file renamed away from its sealed fail:
        the results file still holds it, so the fail is composed beside the
        claims — Failing, required, blocking. Planted: a view of the claim files
        alone (P2.5a's), which drops it."""
        root = P.project(os.path.join(self.tmp(), "b"))
        store.append_signed(root, "C5", "results", {
            "passed": False, "when": "2026-10-04T10:00:00Z", "who": P.WHO,
            "detail": "UV embrittlement at month 14", "evidence": [],
            "channel": "interactive", "authority": "", "measured": None, "units": "",
            "article": {}, "claim_digest": "c" * 64, "rho": "", "evidence_sha256": {},
            "contradicts": [], "contradiction_check": ""})
        os.rename(os.path.join(root, "claims", "C5.json"),
                  os.path.join(root, "claims", "C15.json"))
        view, resolution = P.resolved(root)
        claim = view.claim("C5")
        self.assertIsNotNone(claim, "the fail's claim left the view")
        found = claims.compose(claim, view.verdicts, stale_gates=resolution.stale_gates)
        self.assertEqual(found.status, ClaimStatus.REFUTED)
        self.assertIn("C5", [c.id for c, _s in claims.blocking(
            view, None, stale_gates=resolution.stale_gates)])
        self.assertIn("restore claims/C5.json", report.reason(found, view, claim, full=True))
        self.assertEqual(claims.next_claim_id(store.load(root)), "C16")

        def claims_only(ledger, resolution_):
            return dataclasses.replace(ledger, verdicts=list(resolution_.verdicts),
                                       claims=[dataclasses.replace(
                                           c, standing=resolution_.standings.get(c.id))
                                           for c in ledger.claims])

        with mock.patch.object(verdicts, "view", claims_only):
            view, _res = P.resolved(root)
        self.assertIsNone(view.claim("C5"), "a view of the claim files alone was not caught")

    def test_a_fail_outlives_its_claim_file_end_to_end(self):
        root = P.project(os.path.join(self.tmp(), "b"))
        _fail(root, "C5")
        os.rename(os.path.join(root, "claims", "C5.json"),
                  os.path.join(root, "claims", "C15.json"))
        check = P.run(root, "check", "--junit", "--json", code=1)
        self.assertIn("C5", [row["claim"] for row in json.loads(check.stdout)["blocking"]])
        status = json.loads(P.run(root, "status", "--json", code=0).stdout)
        self.assertEqual(status["claims"]["C5"], "refuted")
        self.assertIn("restore claims/C5.json", status["statuses"]["C5"]["reason"])
        doctor = P.run(root, "doctor", code=1)
        self.assertRegex(doctor.stdout, r"(?m)^\[FAIL.*results/C5\.json holds 1 fail")
        tree = ET.parse(os.path.join(root, ".atompipe", "out", "junit.xml")).getroot()
        case = next(c for c in tree.iter("testcase") if c.get("name") == "C5"
                    and (c.get("classname") or "").startswith("claims"))
        self.assertEqual([k.tag for k in case], ["failure"])

    def test_a_fails_changed_evidence_is_worded_as_a_fail(self):
        """(review of P2.5a) `doctor` told a fail whose photo changed "its pass
        does not count" — inviting a second fail; a fail counts whatever happens
        to its photo (R-3)."""
        root = P.project(os.path.join(self.tmp(), "b"))
        _fail(root, "C2", "--evidence", P.EVIDENCE)
        with open(os.path.join(root, P.EVIDENCE), "wb") as fh:
            fh.write(b"another photo")
        doctor = P.run(root, "doctor").stdout
        row = next(ln for ln in doctor.splitlines() if "evidence C2" in ln)
        self.assertIn("the fail still counts", row)
        self.assertNotIn("its pass does not count", row)

    def test_every_channel_and_every_edit(self):
        edits = {
            "a later interactive pass": lambda root: _pass(root, "C5"),
            "kind edited to measurable": lambda root: P.edit_claim(root, "C5",
                                                                   kind="measurable"),
            "kind edited to assumption": lambda root: P.edit_claim(root, "C5",
                                                                   kind="assumption"),
            "statement edited": lambda root: P.edit_claim(root, "C5", statement="Lasts"),
            "note edited": lambda root: P.edit_claim(root, "C5", note="a look"),
            "terminal and authority edited": lambda root: P.edit_claim(
                root, "C5", terminal="human", authority=P.NAME),
            "the model made not to load": _break_model,
        }
        for how in ("interactive", "agent", "non-interactive", "legacy"):
            for name, edit in edits.items():
                with self.subTest(channel=how, edit=name):
                    root = P.project(os.path.join(self.tmp(), "b"))
                    if how == "legacy":
                        P.write_json(os.path.join(root, "results", "C5.json"), {"results": [
                            {"passed": False, "who": "lab", "detail": "hairline cracks"}]})
                    else:
                        _fail(root, "C5", agent=how == "agent", tty=how == "interactive")
                    edit(root)
                    status, cause, reason = _status(root, "C5")
                    self.assertEqual((status, cause), ("refuted", "physical-fail"), reason)

    def test_a_nudge_and_a_pass_on_the_new_article_leave_it_failing(self):
        root = P.project(os.path.join(self.tmp(), "b"))
        _fail(root, "C5", tty=True)
        _projects.set_thickness(root, 7.01)
        _pass(root, "C5")
        found = P.channels(root, "C5")
        self.assertEqual(P.disagreements(found, "refuted", "physical-fail"), [])
        self.assertEqual(found["check.exit"], 1)
        self.assertTrue(found["check.blocking"])
        self.assertEqual(found["junit"], [("failure", "refuted")])
        self.assertRegex(found["status.reason"],
                         r"\(invalidated: article [0-9a-f]{12}'s design moved: "
                         r"config\.thickness 7\.0 -> 7\.01 — a new article is needed\)")

    def test_planted_laundering_is_caught(self):
        """Planted: the latest result counts (`_counting_result` as P2.1 had it
        before its review), and a `compose` that drops a fail whose article
        moved — each reads the claim off Failing."""
        root = P.project(os.path.join(self.tmp(), "b"))
        _fail(root, "C5", tty=True)
        _pass(root, "C5")
        latest = (lambda items: items[-1] if items else None)
        with mock.patch.object(store, "_counting_result", latest):
            found = P.composed(root, "C5")
        self.assertNotEqual(found.status, ClaimStatus.REFUTED, "the violator still fails it")
        _projects.set_thickness(root, 7.01)
        real = claims.compose

        def drops_a_moved_fail(claim, verdicts_, **kw):
            standing = getattr(claim, "standing", None)
            moved = any(getattr(e, "article_state", "") == "moved" and not e.passed
                        for e in getattr(standing, "entries", ()) or ())
            if moved:
                claim = dataclasses.replace(claim, physical_result=None)
            return real(claim, verdicts_, **kw)

        with mock.patch.object(claims, "compose", drops_a_moved_fail):
            found = P.composed(root, "C5")
        self.assertNotEqual(found.status, ClaimStatus.REFUTED, "the violator still fails it")


# --------------------------------------------------------------------------- #
# V-7 — a moved article reads Stale
# --------------------------------------------------------------------------- #
class AMovedArticleReadsStale(_env.EnvCase):
    """(V-7, invariant 7; PLAN-v0.14 §3 row P2's control) A physical pass whose
    article's read set moved reads Stale, naming the article — a check run
    cannot restore it — and nothing the article was not built from moves it."""

    def test_in_process_a_nudge_moves_the_article(self):
        """The article judged against the design now, in process: a nudge moves
        it and says what moved; back to the value, current; an article compared
        with the design it was recorded on is current."""
        from atompipe import cli
        root = P.project(os.path.join(self.tmp(), "b"))

        def here_and_article():
            ledger = store.load(root)
            model, projection, _err = cli._projection_safe(root, ledger)
            now = verdicts._Now(root, projection, ledger, anchors=verdicts.Anchors(root=root),
                                digests=None, model=model)
            return now, verdicts.article_of(root, projection, model, anchors=now.anchors)

        _here, article = here_and_article()
        self.assertTrue(article.get("hash"))
        _projects.set_thickness(root, 7.01)
        here, moved_article = here_and_article()
        state, moved = verdicts._article_moves(article, here)
        self.assertEqual(state, "moved")
        self.assertIn("config.thickness 7.0 -> 7.01", moved)
        self.assertEqual(verdicts._article_moves(moved_article, here)[0], "current")
        _projects.set_thickness(root, 7.0)
        here, _now = here_and_article()
        self.assertEqual(verdicts._article_moves(article, here)[0], "current")

    def _checked(self, **kw: Any) -> str:
        root = P.project(os.path.join(self.tmp(), "b"), **kw)
        _pass(root, "C5")
        self.assertEqual(_status(root, "C5")[:2], ("verified", "on-article"))
        return root

    def test_a_nudge_reads_stale_and_names_the_article(self):
        root = self._checked()
        _projects.set_thickness(root, 7.01)
        found = P.channels(root, "C5")
        self.assertEqual(P.disagreements(found, "stale", "article-moved"), [])
        self.assertRegex(found["status.reason"],
                         r"^invalidated: the result on article [0-9a-f]{12} was recorded on a "
                         r"design that has since moved: config\.thickness 7\.0 -> 7\.01 — a "
                         r"new article is needed, not a rerun$")
        self.assertFalse(found["ready"])
        # P2.5a-D27 (critique 11 of its design): it awaits a new article, as
        # Pending build does, so it never stops the check run that gates the
        # build of that article — and it is not resolved.
        self.assertFalse(found["check.blocking"])
        self.assertEqual([k for k, _t in found["junit"]], ["skipped"])
        self.assertRegex(found["status.text"], r"(?m)^rebuild: article [0-9a-f]{12} \(C5\) "
                                               r"— config\.thickness 7\.0 -> 7\.01$")
        stale = found["report.text"].split(report.HUMAN["heading"]["failing"], 1)[1]
        self.assertNotIn("re-runs what moved", stale.split("C5", 1)[1].split("\n- ", 1)[0])
        P.run(root, "check")
        self.assertEqual(_status(root, "C5")[:2], ("stale", "article-moved"))
        _projects.set_thickness(root, 7.0)
        self.assertEqual(_status(root, "C5")[:2], ("verified", "on-article"))

    def test_what_the_article_was_not_built_from_moves_nothing(self):
        root = self._checked()
        changes = {
            "a model comment": lambda: _set_model_comment(root, "a note to self"),
            "another claim's acceptance": lambda: P.edit_claim(
                root, "C1", acceptance={"quantity": "tip deflection", "comparator": "<=",
                                        "limit": 0.6, "units": "mm"}),
            "a decision": lambda: P.write_json(os.path.join(root, "decisions", "D9.json"),
                                               {"title": "a decision about C1",
                                                "claims_changed": ["C1"]}),
            "a gate's code": lambda: _append(os.path.join(root, "gates", "structural.py"),
                                             "\nUNUSED = 1\n"),
            "the verdict cache removed": lambda: _env._rmtree(
                os.path.join(root, ".atompipe", "verdicts")),
        }
        for name, change in changes.items():
            with self.subTest(name):
                change()
                self.assertEqual(_status(root, "C5")[:2], ("verified", "on-article"))

    def test_a_derived_value_moved_by_the_model_reads_stale(self):
        root = self._checked()
        text = _model_text(root)
        moved = text.replace("def build(config: Config | None = None) -> dict:",
                             "def build(config: Config | None = None) -> dict:\n"
                             "    _ = 0  # build changed\n", 1)
        self.assertNotEqual(moved, text)
        _restore_model(root, moved)
        self.assertEqual(_status(root, "C5")[:2], ("stale", "article-moved"))

    def test_the_claim_moved_and_the_model_not_loading(self):
        root = self._checked()
        P.edit_claim(root, "C5", note="a different test")
        status, cause, reason = _status(root, "C5")
        self.assertEqual((status, cause), ("stale", "claim-moved"))
        self.assertRegex(reason, r"^C5 changed since article [0-9a-f]{12} was tested — test it "
                                 r"again")
        doc = json.loads(P.run(root, "status", "--json", code=0).stdout)
        self.assertEqual(doc.get("rebuild"), [])
        P.edit_claim(root, "C5", note=P.C5_NOTE)
        text = _model_text(root)
        _break_model(root)
        self.assertEqual(_status(root, "C5")[:2], ("stale", "article-unjudged"))
        _restore_model(root, text)
        self.assertEqual(_status(root, "C5")[:2], ("verified", "on-article"))

    def test_the_article_leads_an_invalidated_evaluator(self):
        root = P.project(os.path.join(self.tmp(), "b"), thickness=8.0)
        P.edit_claim(root, "C5", tags=["stiffness"])
        P.run(root, "check")
        _pass(root, "C5")
        self.assertEqual(_status(root, "C5")[:2], ("verified", "on-article"))
        _projects.set_thickness(root, 8.5)
        found = P.composed(root, "C5")
        self.assertEqual((found.status.value, found.cause.value), ("stale", "article-moved"))
        self.assertIn("bracket.deflection", found.cites)

    def test_a_file_the_design_names_is_part_of_the_article(self):
        """(critique 1 of the P2.5a design) A re-exported mesh named by a path
        parameter moves the article though the path string does not."""
        root = P.project(os.path.join(self.tmp(), "b"))
        text = _model_text(root)
        text = text.replace("    brim_mm: float = 8.0\n",
                            "    brim_mm: float = 8.0\n    mesh_path: str = \"build/part.stl\"\n",
                            1)
        _restore_model(root, text)
        os.makedirs(os.path.join(root, "build"))
        with open(os.path.join(root, "build", "part.stl"), "w", encoding="utf-8") as fh:
            fh.write("solid part\nendsolid part\n")
        _pass(root, "C5")
        self.assertEqual(_status(root, "C5")[:2], ("verified", "on-article"))
        with open(os.path.join(root, "build", "part.stl"), "w", encoding="utf-8") as fh:
            fh.write("solid part\n facet normal 0 0 1\nendsolid part\n")
        status, cause, reason = _status(root, "C5")
        self.assertEqual((status, cause), ("stale", "article-moved"))
        self.assertIn("build/part.stl", reason)

    def test_moved_evidence_never_hides_a_moved_article(self):
        """(critique 7) The article is judged before the evidence."""
        root = self._checked()
        _projects.set_thickness(root, 7.5)
        with open(os.path.join(root, P.EVIDENCE), "wb") as fh:
            fh.write(b"changed")
        found = P.channels(root, "C5", site=False)
        self.assertEqual((found["status.json"]), ("stale", "article-moved"))
        self.assertTrue(found["rebuild"])

    def test_the_all_stale_override_stales_a_counted_pass(self):
        """(critique 8) ``stale=True`` promises nothing reads current."""
        root = self._checked()
        view, _resolution = P.resolved(root)
        found = claims.compose(view.claim("C5"), view.verdicts, stale=True)
        self.assertEqual(found.status, ClaimStatus.STALE)

    def test_in_process_an_unregistered_gates_files_are_no_part_of_the_article(self):
        """(review of P2.5a) The files an article names come from the registered
        evaluators' newest entries only: an unregistered gate's entry outlives
        it until the cache is cleared, and one design must not name different
        files on either side of that."""
        from types import SimpleNamespace
        root = self.tmp()
        os.makedirs(os.path.join(root, "inputs"))
        with open(os.path.join(root, "inputs", "spec.txt"), "w", encoding="utf-8") as fh:
            fh.write("a spec\n")
        entry = SimpleNamespace(reads={"files": {"<root>/inputs/spec.txt": "x"}})
        anchors = verdicts.Anchors(root=root)
        for state, want in (("fresh", ["inputs/spec.txt"]), ("stale", ["inputs/spec.txt"]),
                            ("orphan", []), ("legacy", [])):
            with self.subTest(state=state):
                found = SimpleNamespace(rows={"g": verdicts.Row("g", state, entry=entry)})
                self.assertEqual(verdicts._read_files(root, found, anchors), want)

    def test_a_pass_waits_until_every_evaluator_has_run(self):
        """(review of P2.5a) A pass recorded before an evaluator's first check run
        got an article without the files it reads — one design, two article ids.
        A pass is refused until each has run here (a fail never is); after
        `check` it is recorded."""
        root = P.project(os.path.join(self.tmp(), "b"))
        _env._rmtree(os.path.join(root, ".atompipe", "verdicts"))
        before = P.results(root, "C5")
        proc = P.tty(root, "claim", "physical", "C5", "pass", "--detail", "no cracking",
                     "--evidence", P.EVIDENCE, answer="C5", code=2)
        self.assertIn("never ran here", proc.stderr)
        self.assertIn("atompipe check", proc.stderr)
        self.assertEqual(P.results(root, "C5"), before)
        _fail(root, "C2")
        P.run(root, "check")
        _pass(root, "C5")
        self.assertEqual(_status(root, "C5")[:2], ("verified", "on-article"))

    def test_planted_judges_are_caught(self):
        """Planted: an article compared with itself (the nudge reads Checked),
        and a judge that leaves out the files the design names (critique 1: a
        re-exported mesh reads Checked)."""
        root = self._checked()
        _projects.set_thickness(root, 7.01)
        with mock.patch.object(verdicts, "_article_moves", lambda article, here: ("current", ())):
            self.assertEqual(P.composed(root, "C5").cause.value, "on-article")
        real = _need(verdicts, "_article_moves")

        def no_files(article, here):
            built = dict(article.get("built_from") or {}, files={})
            return real(dict(article, built_from=built), here)

        root = P.project(os.path.join(self.tmp(), "mesh"))
        text = _model_text(root).replace(
            "    brim_mm: float = 8.0\n",
            "    brim_mm: float = 8.0\n    mesh_path: str = \"build/part.stl\"\n", 1)
        _restore_model(root, text)
        os.makedirs(os.path.join(root, "build"))
        with open(os.path.join(root, "build", "part.stl"), "w", encoding="utf-8") as fh:
            fh.write("solid part\nendsolid part\n")
        _pass(root, "C5")
        with open(os.path.join(root, "build", "part.stl"), "w", encoding="utf-8") as fh:
            fh.write("solid part\n facet\nendsolid part\n")
        self.assertEqual(P.composed(root, "C5").cause.value, "article-moved")
        with mock.patch.object(verdicts, "_article_moves", no_files):
            self.assertEqual(P.composed(root, "C5").cause.value, "on-article")


# --------------------------------------------------------------------------- #
# V-8 — the rebuild prediction
# --------------------------------------------------------------------------- #
def _rebuild_lines(text: str) -> list[str]:
    return [ln for ln in text.splitlines() if ln.startswith("rebuild: ")]


class RebuildPredictionIsExact(_env.EnvCase):
    """(V-8; PLAN-v0.14 §1.5) The rebuild prediction names the articles a
    counting result is bound to whose read set moved — and, in P2.5a, never
    fewer (the whole design is the article; 'names nothing else' is P2.5b's,
    with `export`'s traced articles: critique 12 of the P2.5a design)."""

    def _two(self) -> tuple[str, str, str]:
        root = P.project(os.path.join(self.tmp(), "b"), planted=("C9",))
        _pass(root, "C5")
        a = P.results(root, "C5")["results"][-1]["article"]["hash"][:12]
        _projects.set_thickness(root, 8.0)
        _pass(root, "C9")
        b = P.results(root, "C9")["results"][-1]["article"]["hash"][:12]
        return root, a, b

    def _named(self, root: str) -> dict[str, list[str]]:
        doc = json.loads(P.run(root, "status", "--json", code=0).stdout)
        return {row["article"][:12]: list(row["claims"]) for row in doc.get("rebuild") or ()}

    def test_one_article_per_state(self):
        root, a, b = self._two()
        self.assertEqual(self._named(root), {a: ["C5"]})
        _projects.set_thickness(root, 7.0)
        self.assertEqual(self._named(root), {b: ["C9"]})
        _projects.set_thickness(root, 7.5)
        self.assertEqual(self._named(root), {a: ["C5"], b: ["C9"]})
        found = P.channels(root, "C5")
        self.assertEqual(len(_rebuild_lines(found["status.text"])), 2)
        self.assertEqual(len(found["state"].get("rebuild") or ()), 2)
        last = P.read_json(os.path.join(root, ".atompipe", "cache", "last_check.json"))
        self.assertEqual(len(last.get("rebuild") or ()), 2)

    def test_two_claims_on_one_article_are_one_line(self):
        root = P.project(os.path.join(self.tmp(), "b"), planted=("C9",))
        _pass(root, "C5")
        _pass(root, "C9")
        self.assertEqual(self._named(root), {})
        _projects.set_thickness(root, 7.5)
        named = self._named(root)
        self.assertEqual(list(named.values()), [["C5", "C9"]])
        check = P.run(root, "check")
        self.assertEqual(len(_rebuild_lines(check.stdout)), 1, check.stdout)

    def test_what_is_never_named(self):
        root = P.project(os.path.join(self.tmp(), "b"), planted=("C8", "C9"))
        P.run(root, "claim", "physical", "C5", "pass", "--detail", "x", "--evidence",
              P.EVIDENCE, agent=True, code=0)
        P.tty(root, "claim", "physical", "C8", "pass", "--authority", P.NAME, "--detail",
              "safe", answer="C8", code=0)
        P.run(root, "claim", "physical", "C1", "pass", "--detail", "measured fine", code=0)
        _projects.set_thickness(root, 7.5)
        self.assertEqual(self._named(root), {})

    def test_a_moved_fail_is_named(self):
        root = P.project(os.path.join(self.tmp(), "b"))
        _fail(root, "C5", tty=True)
        a = P.results(root, "C5")["results"][-1]["article"]["hash"][:12]
        _projects.set_thickness(root, 7.5)
        self.assertEqual(self._named(root), {a: ["C5"]})

    def test_planted_predictions_are_caught(self):
        root, a, b = self._two()
        view, _res = P.resolved(root)
        rebuild = _need(claims, "rebuild")
        real = rebuild(view)
        self.assertEqual([r.article[:12] for r in real], [a])
        from atompipe.models import EntryStanding

        def every_pass(view_):
            # Every recorded pass's article, as if each had moved.
            return [(c.id, EntryStanding(e.index, True, e.counts, e.why, e.article, "moved",
                                         ("named anyway",)))
                    for c in view_.claims
                    for e in getattr(getattr(c, "standing", None), "entries", ()) or ()
                    if e.passed and e.article]

        with mock.patch.object(claims, "_rebuild_candidates", every_pass):
            planted = claims.rebuild(view)
        self.assertNotEqual([r.article[:12] for r in planted], [a],
                            "naming every article with a pass was not caught")


# --------------------------------------------------------------------------- #
# V-9 — the checked section holds only bound results
# --------------------------------------------------------------------------- #
def _sections(markdown: str) -> dict[str, str]:
    out: dict[str, str] = {}
    parts = re.split(r"(?m)^(## .*)$", markdown)
    for i in range(1, len(parts) - 1, 2):
        out[parts[i]] = parts[i + 1]
    return out


class TheCheckedSectionHoldsOnlyBoundResults(_env.EnvCase):
    """(V-9, invariant 4) A physical claim is listed Checked only on a result
    that counts, in its own row; every claim is in exactly one section."""

    def test_in_process_rows(self):
        """A counted pass is under the checked section in its own row; a moved
        one is not, and a VERIFIED the standing does not back is loud."""
        from atompipe.models import EntryStanding, ProjectMeta, Standing
        result = PhysicalResult(passed=True, who=P.WHO, channel="interactive",
                                article={"hash": "a" * 64}, evidence=["p.jpg"])

        def physical(cid, state, counts):
            return Claim(id=cid, statement=f"{cid} holds", kind=ClaimKind.PHYSICAL, note="n",
                         physical_result=result, results=(result,),
                         standing=Standing(state, 0, "a" * 64, ("config.t 1 -> 2",),
                                           (EntryStanding(0, True, counts, "" if counts
                                                          else state, "a" * 64,
                                                          "current" if counts else "moved"),)))

        ledger = Ledger(meta=ProjectMeta(name="t", revision="v0.1"),
                        claims=[physical("C5", "current", True),
                                physical("C9", "article-moved", False)])
        markdown = report.render_markdown(ledger, None)
        proven = markdown.split(report.SECTION_PROVEN, 1)[1].split("\n## ", 1)[0]
        self.assertIn("**C5**", proven)
        self.assertIn("Checked on an article", proven)
        self.assertNotIn("**C9**", proven)
        planted = claims.Composed(ClaimStatus.VERIFIED, claims.ClaimCause.ON_ARTICLE)
        self.assertTrue(report._disagreement(ledger, ledger.claim("C9"), planted, {}))
        # (review of P2.5a) The readiness tally counts a claim Checked on an
        # article: it read PASS alone, under a count line saying otherwise.
        evaluated = dataclasses.replace(ledger, verdicts=[Verdict(gate="g", passed=True,
                                                                  claims=["C7"])])
        sentence = report._verdict_sentence(evaluated, claims.compositions(evaluated), None,
                                            stale=False, markdown=False)
        self.assertIn("1 of 2 claims is checked against the current inputs.", sentence)
        # (review of P2.5a) The Stale advice is a sentence of its own, never a
        # clause run into a capitalised one ("*now* — A new article is needed").
        self.assertIn("Nothing here is checked *now*. A new article is needed", markdown)
        self.assertNotRegex(markdown, r"\*now\* — [A-Z]")

    def test_checked_rows_and_exactly_one_section(self):
        root = P.project(os.path.join(self.tmp(), "b"), planted=("C8", "C9"))
        _pass(root, "C9")
        _projects.set_thickness(root, 7.5)
        _pass(root, "C5")
        P.tty(root, "claim", "physical", "C8", "pass", "--authority", P.NAME, "--detail",
              "safe", answer="C8", code=0)
        markdown = P.run(root, "report", code=0).stdout
        sections = _sections(markdown)
        proven = next(body for head, body in sections.items()
                      if head.startswith(report.SECTION_PROVEN))
        self.assertRegex(proven, r"Checked on an article")
        self.assertRegex(proven, r"\*\*C5\*\*")
        self.assertRegex(proven, r"Checked by expert judgment")
        self.assertRegex(proven, r"\*\*C8\*\*")
        self.assertNotIn("**C9**", proven)
        ledger = store.load(root)
        for claim in ledger.claims:
            with self.subTest(claim.id):
                holding = [head for head, body in sections.items()
                           if re.search(rf"\*\*{claim.id}\*\*|^### \[.{{5}}\] {claim.id} —"
                                        rf"|^- \*\*Claim:\*\* {claim.id} —", body, re.M)]
                self.assertEqual(len(holding), 1, holding)

    def test_a_section_that_lists_every_pass_is_caught(self):
        """Planted: the checked section's physical rows listing every physical
        claim with a pass — the moved C9 lands under the heading, and the
        section check catches it. (Under it, the VERIFIED guard of critique 2
        stands too: a composition that minted VERIFIED over the moved pass is
        listed loud, never under the heading.)"""
        root = P.project(os.path.join(self.tmp(), "b"), planted=("C9",))
        _pass(root, "C9")
        _projects.set_thickness(root, 7.5)
        view, resolution = P.resolved(root)

        def every_pass(ledger, composed_, cover):
            return ["Checked on an article:", ""] + [
                f"- **{c.id}** {c.statement}" for c in ledger.claims
                if c.physical_result is not None and c.physical_result.passed] + [""]

        with mock.patch.object(report, "_proven_physical", every_pass):
            markdown = report.render_markdown(view, None, stale_gates=resolution.stale_gates)
        proven = markdown.split(report.SECTION_PROVEN, 1)[1].split("\n## ", 1)[0]
        self.assertIn("**C9**", proven, "the planted section did not list the moved pass")
        planted = claims.Composed(ClaimStatus.VERIFIED, _need(claims.ClaimCause, "ON_ARTICLE"))
        composed = dict(claims.compositions(view, stale_gates=resolution.stale_gates),
                        C9=planted)
        cover = report._coverage(view, None)
        rows = report._proven_physical(view, composed, cover)
        self.assertNotIn("**C9**", "\n".join(rows))


# --------------------------------------------------------------------------- #
# V-10 — a contradiction goes on the evaluator's track record
# --------------------------------------------------------------------------- #
class AContradictionGoesOnTheEvaluatorsTrackRecord(_env.EnvCase):
    """(V-10, E4; invariant 11) A physical fail on a claim a qualified, current,
    counted evaluator had passed is a contradiction, sealed into the fail and on
    that evaluator's track record at its version. It moves no status but the
    claim's own and no qualification."""

    def _checked_c1(self) -> str:
        root = P.project(os.path.join(self.tmp(), "b"), thickness=8.0)
        P.run(root, "check", code=1)
        doc = json.loads(P.run(root, "status", "--json", code=0).stdout)
        self.assertEqual(doc["claims"]["C1"], "pass")
        return root

    def test_a_contradiction_is_recorded_and_shown(self):
        root = self._checked_c1()
        proc = P.tty(root, "claim", "physical", "C1", "--measured", "0.62", "--detail",
                     "ruler at the tip, 15 N for an hour", answer="C1", code=0)
        self.assertIn("this contradicts bracket.deflection", proc.stderr)
        self.assertRegex(proc.stdout, r"(?m)^contradicts: bracket\.deflection \(version "
                                      r"[0-9a-f]{12}\) — on its track record$")
        entry = P.results(root, "C1")["results"][-1]
        self.assertEqual(entry["passed"], False)
        self.assertEqual(entry["measured"], 0.62)
        self.assertEqual(entry["units"], "mm")
        self.assertEqual(len(entry["contradicts"]), 1)
        item = entry["contradicts"][0]
        self.assertEqual(set(item), {"gate", "code", "rho", "value", "units", "inside"})
        self.assertEqual((item["gate"], item["units"], item["inside"]),
                         ("bracket.deflection", "mm", True))
        self.assertAlmostEqual(item["value"], 0.469, places=2)
        status, cause, reason = _status(root, "C1")
        self.assertEqual((status, cause), ("refuted", "contradiction"))
        self.assertRegex(reason, r"bracket\.deflection had passed it at 0\.46\d* mm$")
        show = P.run(root, "gate", "show", "bracket.deflection", code=0).stdout
        self.assertRegex(show, r"track record: 1 contradiction at this version, 0 at earlier "
                               r"versions — C1 on article [0-9a-f]{12}")
        doc = json.loads(P.run(root, "gate", "show", "bracket.deflection", "--json",
                               code=0).stdout)
        self.assertEqual(len(doc["track_record"]), 1)
        why = P.run(root, "why", "C1", code=0).stdout
        self.assertIn("bracket.deflection", why)
        self.assertIn("contradiction", why)
        with open(os.path.join(root, "gates", "structural.py"), "a", encoding="utf-8") as fh:
            fh.write("\n\ndef _unused_helper():\n    return 1\n")
        show = P.run(root, "gate", "show", "bracket.deflection", code=0).stdout
        self.assertIn("track record: 0 contradictions at this version, 1 at earlier versions",
                      show)

    def test_an_agents_fail_is_a_contradiction_shown_with_its_channel(self):
        root = self._checked_c1()
        P.run(root, "claim", "physical", "C1", "--measured", "0.62", "--detail", "ruler",
              agent=True, code=0)
        self.assertEqual(_status(root, "C1")[:2], ("refuted", "contradiction"))
        show = P.run(root, "gate", "show", "bracket.deflection", code=0).stdout
        self.assertIn("from an agent session", show)

    def test_controls_record_no_contradiction(self):
        root = P.project(os.path.join(self.tmp(), "b"))
        P.run(root, "check", code=1)
        P.run(root, "claim", "physical", "C1", "fail", "--detail", "sagged", code=0)
        self.assertEqual(P.results(root, "C1")["results"][-1]["contradicts"], [])
        self.assertEqual(_status(root, "C1")[:2], ("refuted", "physical-fail"))
        root = self._checked_c1()
        proc = P.run(root, "claim", "physical", "C1", "--measured", "0.4", "--detail", "fine",
                     code=0)
        self.assertEqual(P.results(root, "C1")["results"][-1]["contradicts"], [])
        self.assertEqual(_status(root, "C1")[:2], ("pass", "checked"))
        why = P.run(root, "why", "C1", code=0).stdout
        self.assertIn("settles nothing", why)
        self.assertTrue(proc.stdout)
        _break_model(root)
        P.run(root, "claim", "physical", "C1", "fail", "--detail", "sagged", code=0)
        entry = P.results(root, "C1")["results"][-1]
        self.assertEqual(entry["contradicts"], [])
        self.assertTrue(entry["contradiction_check"])

    def test_in_process_a_contradiction_outlives_its_claim_file(self):
        """(review of P2.5a) The track record is every results file's: a fail
        whose claim file was deleted keeps its contradiction, under the id it is
        sealed to, marked removed. Planted: the claim files alone (P2.5a's)."""
        fail = PhysicalResult(passed=False, who=P.WHO, channel="interactive", detail="sag",
                              contradicts=[{"gate": "g", "code": "c" * 64, "rho": "",
                                            "value": 0.47, "units": "mm", "inside": True}])
        gone = Claim(id="C1", statement="", kind=ClaimKind.PHYSICAL, results=(fail,),
                     physical_result=fail)
        ledger = Ledger(removed=(gone,))
        found = _need(verdicts, "track_record")(ledger)
        self.assertEqual([(c.claim, c.removed) for c in found.get("g", ())], [("C1", True)])
        self.assertIn("its claim file removed",
                      report.track_words("g", found["g"], "c" * 64))
        planted = verdicts.track_record(dataclasses.replace(ledger, removed=()))
        self.assertEqual(planted, {}, "a record of the claim files alone was not caught")

    def test_a_contradiction_outlives_its_claim_file_end_to_end(self):
        root = self._checked_c1()
        P.run(root, "claim", "physical", "C1", "--measured", "0.62", "--detail", "ruler",
              code=0)
        os.remove(os.path.join(root, "claims", "C1.json"))
        doc = json.loads(P.run(root, "gate", "show", "bracket.deflection", "--json",
                               code=0).stdout)
        self.assertEqual([(row["claim"], row["removed"]) for row in doc["track_record"]],
                         [("C1", True)])

    def test_in_process_only_a_counted_current_pass_is_contradicted(self):
        contradicted_by = _need(claims, "contradicted_by")
        claim = Claim(id="C1", statement="s", acceptance=Acceptance(
            quantity="tip deflection", limit=0.5, units="mm"), gates=["g", "guard"])

        def pass_(**kw) -> Verdict:
            fields = dict(gate="g", passed=True, claims=["C1"], measured=0.47, limit=0.5,
                          units="mm", settles="tip deflection", rho="r" * 16)
            fields.update(kw)
            return Verdict(**fields)

        codes = {"g": "c" * 64, "guard": "d" * 64}
        rows = {
            "a counted, current pass": ([pass_()], {}, (), ["g"], [True]),
            "a fail": ([pass_(passed=False, measured=0.7)], {}, (), [], []),
            "unqualified": ([pass_(unqualified="known-good:not-run")], {}, (), [], []),
            "outside its context": ([pass_(unqualified='context:outside|{"x":1}')], {}, (),
                                    ["g"], [False]),
            "invalidated": ([pass_()], {}, ("g",), [], []),
            "a value the claim does not admit": ([pass_(measured=0.6, limit=0.8)], {}, (),
                                                 [], []),
            "a prerequisite of another": ([pass_(), pass_(gate="guard")], {"g": ["guard"]},
                                          (), ["g"], [True]),
        }
        for name, (found, needs, stale, gates_, inside) in rows.items():
            with self.subTest(name):
                got = contradicted_by(claim, found, stale_gates=stale, needs=needs, codes=codes)
                self.assertEqual([item["gate"] for item in got], gates_)
                self.assertEqual([item["inside"] for item in got], inside)
        planted = mock.patch.object(claims, "_counted_pass", lambda verdict, *a, **k:
                                    verdict.passed is True)
        with planted:
            got = contradicted_by(claim, [pass_(unqualified="known-good:not-run")],
                                  stale_gates=("g",), needs={}, codes=codes)
        self.assertTrue(got, "a recorder reading Verdict.passed was not caught")


# --------------------------------------------------------------------------- #
# V-11 — an expert judgment stays with its authority
# --------------------------------------------------------------------------- #
class AnExpertJudgmentStaysWithItsAuthority(_env.EnvCase):
    """(V-11, W8; invariant 11) An expert-judgment claim is Gap until its
    authority records it, Assumed under the authority's name until they judge
    it, Checked only on the authority's own judgment in their own shell — and a
    judgment on other inputs is Stale (critique 10 of the P2.5a design: only the
    named authority settles it, so the git identity must be theirs)."""

    def _c8(self) -> str:
        return P.project(os.path.join(self.tmp(), "b"), planted=("C8",))

    def test_in_process_the_rows(self):
        """Rows a-f in process: no authority, named only, recorded (Assumed),
        the statement edited after (Gap), recorded by another identity (Gap),
        a judgment that counts (Checked)."""
        from atompipe.models import AttributionRecord, EntryStanding, Standing
        base = Claim(id="C8", statement="Safe above a bed", kind=ClaimKind.ASSUMPTION,
                     rationale="a safety call", terminal="human", authority="Dana")

        def recorded(claim, who="Dana <d@x>"):
            return dataclasses.replace(claim, attributions=(AttributionRecord(
                role="authority", name="Dana", reason="a safety call",
                claim_digest=claims.claim_digest(claim), who=who, channel="interactive"),))

        judged = Standing("current", 0, "a" * 64, (),
                          (EntryStanding(0, True, True, "", "a" * 64, "current"),))
        rows = {
            "a no authority": (dataclasses.replace(base, authority=""), "no-authority"),
            "b named only": (base, "authority-unattributed"),
            "c recorded": (recorded(base), "awaiting-judgment"),
            "d edited after": (dataclasses.replace(recorded(base), statement="Safe above a cot"),
                               "authority-unattributed"),
            "recorded by another": (recorded(base, who="Pat Other <p@x>"),
                                    "authority-unattributed"),
            "e judged": (dataclasses.replace(recorded(base), standing=judged), "judged"),
        }
        for name, (claim, cause) in rows.items():
            with self.subTest(name):
                self.assertEqual(claims.compose(claim, []).cause.value, cause)
        self.assertTrue(claims.blocks(claims.compose(base, [])),
                        "an authority named in the file only must stop check")

    def test_in_process_a_rewritten_judgment_is_no_judgment(self):
        """(review of P2.5a) A judged claim whose statement is rewritten reads as
        the claim written fresh — Gap until its authority records it as it now
        reads, which stops `check` — never Stale `claim-moved`, which does not:
        any once-judged claim could have carried any new statement past `check`
        (D17's laundering by another route). Planted: P2.5a's judged states."""
        from atompipe.models import AttributionRecord, EntryStanding, Standing
        base = Claim(id="C8", statement="Safe above a bed", kind=ClaimKind.ASSUMPTION,
                     rationale="a safety call", terminal="human", authority="Dana")
        record = AttributionRecord(role="authority", name="Dana", reason="a safety call",
                                   claim_digest=claims.claim_digest(base), who="Dana <d@x>",
                                   channel="interactive")
        moved = Standing("claim-moved", 0, "a" * 64, (), (EntryStanding(
            0, True, False, "claim-moved", "a" * 64, "current"),))
        rewritten = dataclasses.replace(base, statement="Safe to hang heavy books above a bed",
                                        attributions=(record,), standing=moved)
        found = claims.compose(rewritten, [])
        self.assertEqual((found.status, found.cause.value),
                         (ClaimStatus.UNCLAIMED, "authority-unattributed"))
        self.assertTrue(claims.blocks(found))
        self.assertIn("changed since Dana judged it", report.reason(found, Ledger(), rewritten,
                                                                  full=True))
        again = dataclasses.replace(rewritten, attributions=(dataclasses.replace(
            record, claim_digest=claims.claim_digest(rewritten)), record))
        self.assertEqual(claims.compose(again, []).cause.value, "awaiting-judgment")
        with mock.patch.object(claims, "_JUDGED_STATES",
                               frozenset({"current", "article-moved", "judgment-moved",
                                          "article-unjudged", "claim-moved"})):
            planted = claims.compose(rewritten, [])
        self.assertFalse(claims.blocks(planted), "P2.5a's judged states were not caught")

    def test_a_rewritten_judgment_stops_check(self):
        root = self._c8()
        P.tty(root, "claim", "physical", "C8", "assume", "--authority", P.NAME, answer="C8",
              code=0)
        P.tty(root, "claim", "physical", "C8", "pass", "--authority", P.NAME, "--detail",
              "safe", answer="C8", code=0)
        self.assertEqual(_status(root, "C8")[:2], ("verified", "judged"))
        for nudge in (None, 7.5):
            with self.subTest(nudge=nudge):
                P.edit_claim(root, "C8", statement="Safe to hang heavy books above a bed")
                if nudge:
                    _projects.set_thickness(root, nudge)
                self.assertEqual(_status(root, "C8")[:2], ("unclaimed", "authority-unattributed"))
                check = json.loads(P.run(root, "check", "--json").stdout)
                self.assertIn("C8", [row["claim"] for row in check["blocking"]])
                P.edit_claim(root, "C8", statement=P.PLANTED["C8"]["statement"])
                _projects.set_thickness(root, 7.0)

    def test_in_process_a_judgments_fail_names_no_article(self):
        """(review of P2.5a) A fail on an expert-judgment claim whose design moved
        said "a new article is needed" while the rebuild prediction, rightly,
        left a judgment out. Planted: the judgment test answering no."""
        from atompipe.models import EntryStanding, Standing
        fail = PhysicalResult(passed=False, who="Pat Other <p@x>", channel="agent-session s1",
                              detail="too heavy", article={"hash": "a" * 64})
        standing = Standing("", None, "", (), (EntryStanding(
            0, False, False, "", "a" * 64, "moved", ("config.thickness 7.0 -> 7.5",)),))
        claim = Claim(id="C8", statement="s", kind=ClaimKind.ASSUMPTION, rationale="r",
                      terminal="human", authority="Dana", results=(fail,),
                      physical_result=fail, standing=standing)
        found = claims.compose(claim, [])
        text = report.reason(found, Ledger(), claim, full=True)
        self.assertNotIn("article", text)
        with mock.patch.object(report, "_judgment", lambda claim_: False):
            self.assertIn("a new article is needed",
                          report.reason(found, Ledger(), claim, full=True))

    def test_a_judgment_is_worded_as_one(self):
        """(review of P2.5a) Every channel words a judgment as one: `why`'s block
        heads JUDGMENTS, the page's says "A judgment was recorded", a model that
        does not load says the judgment cannot be compared with the design, and
        an authority named with spaces around it is the authority."""
        root = self._c8()
        P.edit_claim(root, "C8", authority=P.NAME + " ")
        P.tty(root, "claim", "physical", "C8", "assume", "--authority", P.NAME, answer="C8",
              code=0)
        P.tty(root, "claim", "physical", "C8", "pass", "--authority", P.NAME, "--detail",
              "safe", answer="C8", code=0)
        self.assertEqual(_status(root, "C8")[:2], ("verified", "judged"))
        why = P.run(root, "why", "C8", code=0).stdout
        self.assertIn("JUDGMENTS (1, oldest first)", why)
        self.assertNotIn("PHYSICAL RESULTS", why)
        self.assertIn(f"settled only by {P.NAME}'s judgment", why)
        self.assertNotIn("threshold", why)
        found = P.channels(root, "C8")
        self.assertEqual(found["state.row"]["physical_result"]["heading"],
                         "A judgment was recorded:")
        text = _model_text(root)
        _break_model(root)
        status, cause, reason = _status(root, "C8")
        self.assertEqual((status, cause), ("stale", "article-unjudged"))
        self.assertIn(f"{P.NAME}'s judgment cannot be compared with the design", reason)
        self.assertIn(f"Fix the model so {P.NAME}'s judgment can be compared",
                      P.run(root, "report", code=0).stdout)
        # A fail on it whose design then moves names no article and no rebuild.
        _restore_model(root, text)
        P.run(root, "claim", "physical", "C8", "fail", "--detail", "too heavy", agent=True,
              code=0)
        _projects.set_thickness(root, 7.5)
        status, cause, reason = _status(root, "C8")
        self.assertEqual((status, cause), ("refuted", "physical-fail"))
        self.assertNotIn("article", reason)
        doc = json.loads(P.run(root, "status", "--json", code=0).stdout)
        self.assertEqual(doc.get("rebuild"), [])

    def test_the_rows(self):
        root = self._c8()
        P.edit_claim(root, "C8", authority=None)
        self.assertEqual(_status(root, "C8")[:2], ("unclaimed", "no-authority"))
        P.edit_claim(root, "C8", authority=P.NAME)
        status, cause, reason = _status(root, "C8")
        self.assertEqual((status, cause), ("unclaimed", "authority-unattributed"))
        self.assertIn(f"atompipe claim physical C8 assume --authority", reason)
        check = json.loads(P.run(root, "check", "--json").stdout)
        self.assertIn("C8", [row["claim"] for row in check["blocking"]])
        P.tty(root, "claim", "physical", "C8", "assume", "--authority", P.NAME, answer="C8",
              code=0)
        status, cause, reason = _status(root, "C8")
        self.assertEqual((status, cause), ("asserted", "awaiting-judgment"))
        self.assertIn(f"awaits {P.NAME}'s judgment", reason)
        P.edit_claim(root, "C8", statement="Safe to mount above a cot")
        self.assertEqual(_status(root, "C8")[:2], ("unclaimed", "authority-unattributed"))
        P.edit_claim(root, "C8", statement=P.PLANTED["C8"]["statement"])
        P.tty(root, "claim", "physical", "C8", "pass", "--authority", P.NAME, "--detail",
              "safe above a bed", answer="C8", code=0)
        status, cause, reason = _status(root, "C8")
        self.assertEqual((status, cause), ("verified", "judged"))
        self.assertIn(f"judged by {P.NAME}", reason)
        _projects.set_thickness(root, 7.5)
        self.assertEqual(_status(root, "C8")[:2], ("stale", "judgment-moved"))
        doc = json.loads(P.run(root, "status", "--json", code=0).stdout)
        self.assertEqual(doc.get("rebuild"), [])
        _projects.set_thickness(root, 7.0)
        P.edit_claim(root, "C8", authority="Alex Other")
        self.assertEqual(_status(root, "C8")[0], "unclaimed")

    def test_only_the_authority_settles_it(self):
        root = self._c8()
        before = P.results(root, "C8")
        for args, env in (((), None), (("--authority", "Alex Other"), None),
                          (("--authority", P.NAME), P.OTHER)):
            with self.subTest(args=args, env=env):
                P.tty(root, "claim", "physical", "C8", "pass", *args, "--detail", "safe",
                      answer="C8", env=env, code=2)
                self.assertEqual(P.results(root, "C8"), before)

    def test_an_agents_judgment_does_not_count_and_a_fail_does(self):
        root = self._c8()
        P.tty(root, "claim", "physical", "C8", "assume", "--authority", P.NAME, answer="C8",
              code=0)
        P.run(root, "claim", "physical", "C8", "pass", "--authority", P.NAME, "--detail",
              "safe", agent=True, code=0)
        status, cause, reason = _status(root, "C8")
        self.assertEqual((status, cause), ("asserted", "awaiting-judgment"))
        self.assertIn("a judgment recorded from an agent session does not count", reason)
        P.tty(root, "claim", "physical", "C8", "fail", "--authority", P.NAME, "--detail",
              "too heavy", answer="C8", code=0)
        self.assertEqual(_status(root, "C8")[:2], ("refuted", "judged-fail"))
        self.assertEqual(P.results(root, "C8")["results"][-1]["contradicts"], [])

    def test_a_physical_judgment_needs_its_evidence(self):
        """(critique 5) Declaring ``terminal: human`` on a physical claim never
        drops the evidence and the written test a measurement needs."""
        root = P.project(os.path.join(self.tmp(), "b"))
        P.edit_claim(root, "C5", terminal="human", authority=P.NAME)
        before = P.results(root, "C5")
        proc = P.tty(root, "claim", "physical", "C5", "pass", "--authority", P.NAME,
                     "--detail", "looks fine", answer="C5", code=2)
        self.assertIn("--evidence", proc.stderr)
        self.assertEqual(P.results(root, "C5"), before)

    def test_planted_compositions_are_caught(self):
        """Planted: a `compose` that reads the file's authority as attributed
        (D17's laundering), and a judge that does not compare the entry's
        authority with the claim's."""
        claim = Claim(id="C8", statement="s", kind=ClaimKind.ASSUMPTION, rationale="r",
                      **{"terminal": "human", "authority": P.NAME})
        self.assertEqual(claims.compose(claim, []).cause.value, "authority-unattributed")
        real = claims._authority_attributed

        with mock.patch.object(claims, "_authority_attributed", lambda claim_: True):
            self.assertEqual(claims.compose(claim, []).status, ClaimStatus.ASSERTED)
        self.assertTrue(callable(real))
        root = self._c8()
        P.tty(root, "claim", "physical", "C8", "assume", "--authority", P.NAME, answer="C8",
              code=0)
        view, resolution = P.resolved(root)
        current = view.claim("C8")
        # An entry recorded "for" the authority by someone else, written as only a
        # process that recomputes seals could (R2): the judge must still refuse it.
        model_, projection, _err = __import__("atompipe.cli", fromlist=["cli"])._projection_safe(
            root, store.load(root))
        article = verdicts.article_of(root, projection, model_, anchors=resolution.anchors,
                                      resolution=resolution)
        store.append_signed(root, "C8", "results", {
            "passed": True, "when": "2026-10-04T10:00:00Z",
            "who": "Pat Other <pat@example.invalid>", "detail": "safe", "evidence": [],
            "channel": "interactive", "authority": P.NAME, "measured": None, "units": "",
            "article": article, "claim_digest": claims.claim_digest(current), "rho": "",
            "evidence_sha256": {}, "contradicts": [], "contradiction_check": ""})
        self.assertEqual(P.composed(root, "C8").cause.value, "awaiting-judgment")
        with mock.patch.object(verdicts, "_fact_authority", lambda *a, **k: ""):
            found = P.composed(root, "C8")
        self.assertEqual(found.status, ClaimStatus.VERIFIED,
                         "a judge not comparing the authority was not caught")


# --------------------------------------------------------------------------- #
# V-12 — a terminal cannot lower the bar
# --------------------------------------------------------------------------- #
class ATerminalCannotLowerTheBar(_env.EnvCase):
    """(V-12; P2.5a-D1, D2; R-10) A declared terminal is validated where it is
    declared, and a declaration never makes a claim read more than without it."""

    def _read(self, record: dict) -> Any:
        root = self.tmp()
        path = os.path.join(root, "C9.json")
        P.write_json(path, record)
        return store.read_record(path, "claims")

    def test_the_strict_reader_refuses_a_bad_terminal(self):
        rows = {
            "simulation": ({"statement": "s", "terminal": "simulation"}, "solver"),
            "meassurement": ({"statement": "s", "kind": "physical",
                              "terminal": "meassurement"}, "measurement"),
            "physical + closed_form": ({"statement": "s", "kind": "physical",
                                        "terminal": "closed_form"}, "measurement, human"),
            "measurable + human": ({"statement": "s", "terminal": "human",
                                    "authority": "x"}, "closed_form"),
            "measurable + measurement": ({"statement": "s", "terminal": "measurement"},
                                         "closed_form"),
            "assumption + measurement": ({"statement": "s", "kind": "assumption",
                                          "terminal": "measurement"}, "none, human"),
            "assumption + solver": ({"statement": "s", "kind": "assumption",
                                     "terminal": "solver"}, "none, human"),
            "authority on a measurement": ({"statement": "s", "kind": "physical",
                                            "authority": "Dana"}, "authority"),
            "owner on a judgment": ({"statement": "s", "kind": "assumption",
                                     "terminal": "human", "authority": "Dana",
                                     "owner": "Sam"}, "owner"),
        }
        for name, (record, word) in rows.items():
            with self.subTest(name):
                with self.assertRaises(AtompipeError) as caught:
                    self._read(record)
                self.assertIn("C9.json", str(caught.exception))
                self.assertIn(word, str(caught.exception))

    def test_a_declared_automated_terminal_composes_as_undeclared(self):
        root = P.project(os.path.join(self.tmp(), "b"))
        before = _status(root, "C1")
        P.edit_claim(root, "C1", terminal="solver")
        self.assertEqual(_status(root, "C1"), before)
        listed = P.run(root, "claim", "list", code=0).stdout
        row = next(ln for ln in listed.splitlines() if " C1 " in ln)
        self.assertIn("[automated]", row)
        self.assertNotIn("solver", row)
        self.assertNotIn("simulation", row)
        claim = Claim(id="C9", statement="s", terminal="closed_form")
        self.assertEqual(claims.compose(claim, []).cause.value, "no-evaluator")

    def test_the_channel_refuses_what_a_terminal_cannot_take(self):
        root = P.project(os.path.join(self.tmp(), "b"))
        proc = P.run(root, "claim", "physical", "C6", "pass", "--detail", "x", code=2)
        self.assertIn("assume", proc.stderr)
        proc = P.tty(root, "claim", "physical", "C5", "assume", answer="C5", code=2)
        self.assertIn("C5", proc.stderr)

    def test_a_human_declaration_on_a_measurement_claim_needs_its_evidence(self):
        """(critique 5) C5 hand-edited to ``terminal: human``: an evidence-free
        pass is refused, and a forged one never reads Checked."""
        root = P.project(os.path.join(self.tmp(), "b"))
        P.edit_claim(root, "C5", terminal="human", authority=P.NAME)
        P.tty(root, "claim", "physical", "C5", "pass", "--authority", P.NAME, "--detail",
              "fine", answer="C5", code=2)
        P.tty(root, "claim", "physical", "C5", "pass", "--authority", P.NAME, "--detail",
              "fine", "--evidence", P.EVIDENCE, answer="C5", code=0)
        self.assertEqual(_status(root, "C5")[:2], ("verified", "judged"))


# --------------------------------------------------------------------------- #
# V-13 — a measured value decides
# --------------------------------------------------------------------------- #
class MeasuredMustAgree(_env.EnvCase):
    """(V-13; D-13 *consistent*) ``--measured`` in the claim's units decides the
    outcome where the claim has a limit; a typed outcome that disagrees is
    refused."""

    def test_the_rows(self):
        root = P.project(os.path.join(self.tmp(), "b"))
        before = P.results(root, "C1")
        proc = P.run(root, "claim", "physical", "C1", "pass", "--measured", "0.62", code=2)
        self.assertIn("0.62", proc.stderr)
        for bad in ("nan", "inf", "-inf"):
            with self.subTest(bad):
                P.run(root, "claim", "physical", "C1", "--measured", bad, code=2)
        self.assertEqual(P.results(root, "C1"), before)
        P.run(root, "claim", "physical", "C1", "--measured", "0.62", "--detail", "ruler",
              code=0)
        entry = P.results(root, "C1")["results"][-1]
        self.assertEqual((entry["passed"], entry["measured"], entry["units"]), (False, 0.62, "mm"))
        P.run(root, "claim", "physical", "C1", "--measured", "0.4", "--detail", "ruler",
              code=0)
        self.assertEqual(P.results(root, "C1")["results"][-1]["passed"], True)
        P.run(root, "claim", "physical", "C5", "--measured", "3", code=2)
        P.run(root, "claim", "physical", "C5", "fail", "--measured", "3", "--detail", "x",
              code=0)
        self.assertEqual(P.results(root, "C5")["results"][-1]["measured"], 3.0)

    def test_a_typed_outcome_winning_is_caught(self):
        decide = _need(__import__("atompipe.cli", fromlist=["cli"]), "_measured_outcome")
        acceptance = Acceptance(quantity="tip deflection", limit=0.5, units="mm")
        with self.assertRaises(AtompipeError):
            decide(acceptance, 0.62, True)
        self.assertIs(decide(acceptance, 0.62, None), False)
        self.assertIs(decide(acceptance, 0.4, None), True)


# --------------------------------------------------------------------------- #
# V-14 — every renderer agrees
# --------------------------------------------------------------------------- #
class RenderersAgreeOnPhysicalClaims(_env.EnvCase):
    """(V-14; planned invariant 12) One copy holding each standing, read on
    every channel: each channel's status and cause equal `compose`'s over the
    judged view — and no new view builder skips the judge."""

    _WORLD: list = []

    @classmethod
    def _world(cls):
        """The world, built on first use: the AST rows need none of it."""
        if not cls._WORLD:
            cls._build()
            cls._WORLD.append(True)

    @classmethod
    def _build(cls):
        cls.tmpdir = tempfile.mkdtemp(prefix="atompipe-renderers-")
        unittest.addModuleCleanup(_env._rmtree, cls.tmpdir)
        root = P.project(os.path.join(cls.tmpdir, "b"), planted=("C8", "C9"))
        for cid, record in (("C10", {"statement": "an agent's pass", "kind": "physical",
                                     "note": "n"}),
                            ("C11", dict(P.PLANTED["C8"], statement="awaits judgment")),
                            ("C14", {"statement": "edited after", "kind": "physical",
                                     "note": "n"})):
            P.write_json(os.path.join(root, "claims", f"{cid}.json"), record)
        _pass(root, "C9")                                   # then moved: article-moved
        _projects.set_thickness(root, 8.0)
        P.run(root, "check")
        _pass(root, "C5")                                   # current: on-article
        P.run(root, "claim", "physical", "C10", "pass", "--detail", "x", "--evidence",
              P.EVIDENCE, agent=True, code=0)
        P.tty(root, "claim", "physical", "C11", "assume", "--authority", P.NAME,
              answer="C11", code=0)
        P.tty(root, "claim", "physical", "C8", "pass", "--authority", P.NAME, "--detail",
              "safe", answer="C8", code=0)
        P.run(root, "claim", "physical", "C1", "--measured", "0.62", "--detail", "ruler",
              code=0)
        _pass(root, "C14")
        P.edit_claim(root, "C14", statement="edited after it was tested")
        cls.root = root
        cls.want = {"C5": ("verified", "on-article"), "C9": ("stale", "article-moved"),
                    "C10": ("unverified", "physical-pass"),
                    "C11": ("asserted", "awaiting-judgment"), "C8": ("verified", "judged"),
                    "C1": ("refuted", "contradiction"), "C14": ("stale", "claim-moved")}
        cls.found = {cid: P.channels(root, cid) for cid in cls.want}

    def test_every_channel_reads_the_composition(self):
        self._world()
        for cid, (status, cause) in self.want.items():
            with self.subTest(cid):
                self.assertEqual(P.composed(self.root, cid).cause.value, cause)
                self.assertEqual(P.disagreements(self.found[cid], status, cause), [])

    def test_the_page_paints_only_a_counted_pass_ok(self):
        """(critique 3 of the P2.5a design) A result the standing did not count is
        never the page's ok tone: state.json says whether it counts, and why."""
        self._world()
        for cid, counts in (("C5", True), ("C9", False), ("C10", False), ("C14", False)):
            with self.subTest(cid):
                result = self.found[cid]["state.row"].get("physical_result") or {}
                self.assertIs(result.get("counts"), counts, result)
                if not counts:
                    self.assertTrue(result.get("why"), result)
        with open(os.path.join(_env.REPO, "src", "atompipe", "site_template", "lib",
                               "panels.js"), encoding="utf-8") as fh:
            panels = fh.read()
        self.assertNotRegex(panels, r"const ok = !!result\.passed")

    def test_a_verified_claim_its_standing_does_not_back_is_loud(self):
        """(critique 2) `report._disagreement` reaches VERIFIED: a resolver that
        mints Checked over a moved article is a defect JUnit and the page show."""
        self._world()
        view, resolution = P.resolved(self.root)
        claim = view.claim("C9")
        planted = claims.Composed(ClaimStatus.VERIFIED, _need(claims.ClaimCause, "ON_ARTICLE"))
        cover = report._coverage(view, None)
        self.assertTrue(report._disagreement(view, claim, planted, cover))
        real = claims.compose

        def mints(claim_, verdicts_, **kw):
            found = real(claim_, verdicts_, **kw)
            return planted if claim_.id == "C9" else found

        with mock.patch.object(claims, "compose", mints):
            junit = ET.fromstring(report.render_junit(view, view.verdicts, None, tier=0,
                                                      ready=True, exit_code=0, when="t",
                                                      stale_gates=resolution.stale_gates))
        case = next(c for c in junit.iter("testcase") if c.get("name") == "C9")
        self.assertEqual([(k.tag, k.get("type")) for k in case],
                         [("failure", "status-and-evidence-disagree")])

    def test_the_page_groups_an_expert_judgment_apart(self):
        """(review of P2.5a) The page grouped an expert judgment under the
        assumptions' blurb — "an owner", authority's Never-say — and never showed
        whose judgment it waits on. Its group and blurb are state.json's
        phrases, its tag its `terminal_word`, its result's heading a
        judgment's."""
        said = report.page_phrases()
        self.assertTrue(said.get("judgment_title"))
        self.assertNotIn("owner", said.get("judgment_blurb", "owner"))
        claim = Claim(id="C8", statement="s", kind=ClaimKind.ASSUMPTION, rationale="r",
                      terminal="human", authority="Dana")
        result = PhysicalResult(passed=True, who="Dana <d@x>", channel="interactive")
        self.assertEqual(report.result_facts(claim, result)["heading"],
                         "A judgment was recorded:")
        with open(os.path.join(_env.REPO, "src", "atompipe", "site_template", "lib",
                               "panels.js"), encoding="utf-8") as fh:
            panels = fh.read()
        self.assertIn('c.terminal === "human" ? "judgment"', panels)
        self.assertIn("claim.terminal_word", panels)
        self.assertIn("result.heading", panels)

    def test_no_view_builder_skips_the_judge(self):
        self.assertEqual(view_builders(_sources()), [])

    def test_a_planted_view_builder_is_caught(self):
        planted = {"site_planted.py": "import dataclasses\n"
                                      "def f(ledger, resolution):\n"
                                      "    return dataclasses.replace(ledger, verdicts=[])\n"}
        self.assertNotEqual(view_builders(planted), [])


#: Every ``dataclasses.replace(..., verdicts=...)`` under ``src/atompipe`` and why
#: it may build its view without the judge (P2.5a-D13).
VIEW_ALLOWLIST = {
    ("verdicts.py", "view"): "the one builder: verdicts and each claim's standing",
    ("verdicts.py", "apply_prerequisites"): "replaces a Resolution, never a ledger",
    ("cli.py", "_swept"): "replaces a Resolution, never a ledger",
    ("store.py", "save"): "a legacy write with the verdicts emptied",
}


def _sources() -> dict[str, str]:
    src = os.path.join(_env.REPO, "src", "atompipe")
    out = {}
    for name in sorted(os.listdir(src)):
        if name.endswith(".py"):
            with open(os.path.join(src, name), encoding="utf-8") as fh:
                out[name] = fh.read()
    return out


def view_builders(sources: dict[str, str]) -> list[str]:
    hits = []
    for name, text in sources.items():
        tree = ast.parse(text)
        for func in ast.walk(tree):
            if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for node in ast.walk(func):
                if (isinstance(node, ast.Call) and any(k.arg == "verdicts" for k in node.keywords)
                        and ((isinstance(node.func, ast.Attribute)
                              and node.func.attr == "replace")
                             or (isinstance(node.func, ast.Name) and node.func.id == "replace"))
                        and (name, func.name) not in VIEW_ALLOWLIST):
                    hits.append(f"{name}:{node.lineno} in {func.name}")
    return sorted(set(hits))


# --------------------------------------------------------------------------- #
# V-18 — a physical result is no tier
# --------------------------------------------------------------------------- #
class APhysicalResultIsNoTier(unittest.TestCase):
    """(V-18, R-12, S-61) ``Tier.EXTERNAL`` named "a human with calipers": a
    second route for what a physical claim and its result own."""

    def _comment(self, text: str) -> str:
        line = next(ln for ln in text.splitlines() if ln.strip().startswith("EXTERNAL = 3"))
        return line

    def test_the_tier_names_no_person(self):
        with open(os.path.join(_env.REPO, "src", "atompipe", "models.py"),
                  encoding="utf-8") as fh:
            line = self._comment(fh.read())
        self.assertNotRegex(line, r"human|calipers|person")

    def test_the_old_comment_is_caught(self):
        planted = "    EXTERNAL = 3    # CI, a fab house, a lab, a human with calipers.\n"
        self.assertRegex(self._comment(planted), r"human|calipers|person")


# --------------------------------------------------------------------------- #
# P2.5b — a result on an exported article; supersession; latency
# --------------------------------------------------------------------------- #
def _fig4_exported(test: Any, name: str = "f") -> tuple[str, str]:
    """Fig. 4 (``tests/test_fig4.py``) with its milestones and generators, its
    `enclosure` exported at ``cavity_w`` 70: ``(root, article hash)``."""
    import test_fig4 as F
    root = F.fig4(os.path.join(test.tmp(), name), generators=F.GENERATORS,
                  milestones=F.MILESTONES)
    _projects._commit_all(root, "fig4")
    # In process (`_captured`, the test identity in git's variables): the fast
    # tier runs one row on this helper, and two processes were most of its cost.
    code, _out, err = _captured(["check", "-C", root])
    test.assertIn(code, (0, 1), err)
    code, out, err = _captured(["export", "enclosure", "--json", "-C", root])
    test.assertEqual(code, 0, out[-2000:] + err[-2000:])
    return root, json.loads(out)["article"]["hash"]


def _captured(argv: list[str]) -> tuple[int, str, str]:
    import contextlib
    import io
    from atompipe import cli
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), \
            mock.patch.dict(os.environ, {"GIT_AUTHOR_NAME": P.NAME,
                                         "GIT_AUTHOR_EMAIL": "tests@atompipe.invalid",
                                         "GIT_COMMITTER_NAME": P.NAME,
                                         "GIT_COMMITTER_EMAIL": "tests@atompipe.invalid"}):
        code = cli.main(argv)
    return code, out.getvalue(), err.getvalue()


class AResultBindsToAnExportedArticle(_env.EnvCase):
    """(V-9, invariant 11; P2.5b-D13) `claim physical <id> pass|fail --article
    <hex>` binds a result to an exported article — what its generator read —
    named by at least 12 hex that one export holds; and a fail's contradiction
    is charged to the verdicts the export sealed on the article's own inputs,
    never to the evaluator's verdict after they moved."""

    def test_a_short_unknown_or_ambiguous_article_is_refused(self):
        root, article = _fig4_exported(self)
        for given, words in ((article[:11], "at least 12 hex"),
                             ("0" * 12, "no export recorded article")):
            with self.subTest(given=given):
                proc = P.run(root, "claim", "physical", "K5", "fail", "--detail", "x",
                             "--article", given, code=2)
                self.assertIn(words, proc.stderr)
        entry = dict(P.exports(root, "enclosure")["exports"][-1])
        twin = dict(entry["article"], hash=article[:12] + "f" * 52)
        store_append = _need(store, "append_sealed")
        store_append(root, "exports", "enclosure", "exports",
                     {k: v for k, v in dict(entry, article=twin).items()
                      if k not in ("prev", "digest")})
        proc = P.run(root, "claim", "physical", "K5", "fail", "--detail", "x",
                     "--article", article[:12], code=2)
        self.assertIn("names two articles", proc.stderr)
        self.assertEqual(P.results(root, "K5"), {}, "a refused --article wrote a result")

    def test_a_fail_is_charged_to_the_verdicts_sealed_at_export(self):
        import test_fig4 as F
        root, article = _fig4_exported(self)
        counted = P.exports(root, "enclosure")["exports"][-1]["counted"]["K1"]
        self.assertTrue([c for c in counted if c.get("inside") is True],
                        "the export sealed no counted verdict for K1: nothing to charge")
        F.set_cavity(root, 82.0)
        code, out, err = _captured(["claim", "physical", "K1", "fail", "--detail",
                                    "the enclosure would not close", "--article", article,
                                    "-C", root])
        self.assertEqual(code, 0, out + err)
        entry = P.results(root, "K1")["results"][-1]
        self.assertEqual(entry["article"]["hash"], article)
        self.assertEqual(entry["contradicts"], counted)

    def test_in_process_every_record_of_one_article_is_read(self):
        """Review of P2.5b (findings 7, 15): one article, two export records —
        two milestones sharing a generator, or one exported again unchanged —
        and the first record in name order alone was read: its `counted` lacked
        the claim, so the fail charged no evaluator. Every record's seal is
        charged, the newest export that re-ran the claim is the one the prompt
        names; planted, the first record alone is caught."""
        from atompipe import milestones
        from atompipe.models import ExportRecord
        row = {"gate": "bracket.deflection", "code": "c" * 64, "rho": "r" * 64,
               "value": 0.469, "units": "mm", "inside": True}
        fit = ExportRecord(milestone="fit-check", when="2026-10-04T10:00:00Z",
                           counted={"C3": []}, article={"hash": "a" * 64})
        fit_again = dataclasses.replace(fit, when="2026-10-04T12:00:00Z")
        printed = ExportRecord(milestone="print-v1", when="2026-10-04T11:00:00Z",
                               counted={"C1": [row]}, article={"hash": "a" * 64})
        records = [fit, fit_again, printed]               # `ledger.exports`' name order
        self.assertEqual(milestones.sealed_on(records, "C1"), [row])
        self.assertIs(milestones.bound_export(records, "C1"), printed)
        self.assertIs(milestones.bound_export(records, "C5"), fit_again)
        self.assertEqual(milestones.sealed_on(records, "C5"), [])
        planted = milestones.sealed_contradictions(records[0], "C1")
        self.assertNotEqual(planted, milestones.sealed_on(records, "C1"),
                            "the first record alone was not caught")

    def test_a_fail_on_an_article_two_milestones_share_is_a_contradiction(self):
        """End to end on the bracket: `fit-check` requires C3 alone and shares
        `print-v1`'s generator, so both exports record one article — exported
        `fit-check`, `print-v1`, then `fit-check` again. A ruler's fail on C1
        bound to it charges `bracket.deflection`, as with `print-v1` alone."""
        import test_export as X
        root = X.bracket(os.path.join(self.tmp(), "b"), thickness=8.0, git=True)
        P.milestone(root, "fit-check", ["C3"], generator="generators/profile.py:side_profile")
        P.run(root, "check")
        for name in ("fit-check", "print-v1", "fit-check"):
            P.run(root, "export", name, code=0)
        article = P.exports(root, "print-v1")["exports"][-1]["article"]["hash"]
        self.assertEqual(P.exports(root, "fit-check")["exports"][0]["article"]["hash"], article)
        proc = P.tty(root, "claim", "physical", "C1", "fail", "--measured", "0.7",
                     "--detail", "ruler at the tip", "--article", article, answer="C1", code=0)
        self.assertIn("exported for print-v1", proc.stderr)
        entry = P.results(root, "C1")["results"][-1]
        self.assertEqual([c["gate"] for c in entry["contradicts"]], ["bracket.deflection"])
        self.assertEqual(entry["contradiction_check"], "")
        self.assertEqual(entry["article"]["milestone"], "print-v1")

    def test_a_planted_capture_from_the_current_resolution_is_caught(self):
        """Planted: the contradiction read from the resolution now — after the
        move, `fig4.enclosure_fit` is invalidated and nothing is charged."""
        import test_fig4 as F
        root, article = _fig4_exported(self)
        counted = P.exports(root, "enclosure")["exports"][-1]["counted"]["K1"]
        F.set_cavity(root, 82.0)
        from atompipe import milestones
        with mock.patch.object(milestones, "sealed_contradictions", lambda entry, cid: []):
            code, out, err = _captured(["claim", "physical", "K1", "fail", "--detail", "x",
                                        "--article", article, "-C", root])
        self.assertEqual(code, 0, out + err)
        self.assertNotEqual(P.results(root, "K1")["results"][-1]["contradicts"], counted)


# -- V-10, in process: the judge over real sealed entries --------------------- #
def _exported(params: dict, *, traced: bool = True, milestone: str = "enclosure",
              when: str = "2026-10-04T10:00:00Z", model: dict | None = None) -> dict:
    """An exported article as `export` records it: ``params`` ``{path: value}``
    (a traced generator's reads) and ``model`` (its code files' digests),
    sealed."""
    from atompipe.util import seal
    rows = [[list(path), verdicts.digest_value(value), value]
            for path, value in sorted(params.items())]
    built = {"params": rows, "model": dict(model or {}), "files": {}}
    return {"source": "export", "hash": seal(built), "built_from": built, "traced": traced,
            "milestone": milestone, "when": when, "revision": "", "dirty": False}


def _design(cavity: float) -> dict:
    return {"source": "design", "hash": ("d%03d" % int(cavity)) * 16,
            "built_from": None, "revision": "", "dirty": False}


class _Judged:
    """K5 (a physical claim with its test written down) with ``entries`` as
    results, judged at ``cavity_w`` = ``cavity`` against ``exports`` (article
    hashes `exports/` holds) — the view every reader composes."""

    def __init__(self, root: str, entries: list[dict], *, cavity: float,
                 exports: list[str], claim: Claim | None = None,
                 verdicts_: list | None = None, packages: dict | None = None,
                 files: dict | None = None) -> None:
        from atompipe.models import ExportRecord
        from atompipe.util import FileDigests
        for rel, data in (files or {}).items():
            path = os.path.join(root, *rel.split("/"))
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as fh:
                fh.write(data)
        photo = os.path.join(root, "photos", "k5.jpg")
        os.makedirs(os.path.dirname(photo), exist_ok=True)
        with open(photo, "wb") as fh:
            fh.write(b"a photo")
        base = claim or Claim(id="K5", statement="The printed parts assemble",
                              kind=ClaimKind.PHYSICAL, note="assemble by hand")
        digest = claims.claim_digest(base)
        sha = __import__("hashlib").sha256(b"a photo").hexdigest()
        results = []
        for entry in entries:
            full = {"who": P.WHO, "channel": "interactive", "claim_digest": digest,
                    "evidence": ["photos/k5.jpg"], "evidence_sha256": {"photos/k5.jpg": sha},
                    "when": "2026-10-05T10:00:00Z", **entry}
            results.append(PhysicalResult.from_dict(full))
        fail = next((r for r in reversed(results) if r.passed is not True), None)
        self.claim = dataclasses.replace(base, results=tuple(results),
                                         physical_result=fail or (results[-1] if results
                                                                  else None))
        # Each export's package as `export` records it: the generator's files by
        # their sha256, beside the spine's (`model.json`, `REPORT.md`). By
        # default every article's generator wrote bytes of its own; `packages`
        # names an article's files where a row needs two to be one object.
        packages = dict(packages or {})
        records = [ExportRecord(milestone="enclosure", article={"hash": h}, package={
            "hash": "p" * 64, "files": dict(packages.get(h) or {
                "part.svg": sha256_text(h), "model.json": "m" * 64,
                "REPORT.md": "r" * 64})}) for h in exports]
        self.ledger = dataclasses.replace(Ledger(claims=[self.claim],
                                                 verdicts=list(verdicts_ or [])),
                                          exports=records)
        projection = {"config": {"cavity_w": cavity, "cell_mah": 2000.0},
                      "derived": {"enclosure": cavity + 6.0}}
        here = verdicts._Now(root, projection, self.ledger,
                             anchors=verdicts.Anchors(root=os.path.abspath(root)),
                             digests=FileDigests(), model=None)
        real_moves = verdicts._article_moves

        def moves(article: Any, now: Any) -> tuple[str, tuple]:
            # A design article in process, with no model to digest: current
            # exactly when it is this cavity's (`_design`), else moved.
            if isinstance(article, dict) and article.get("source") == "design":
                return (("current", ()) if article.get("hash") == _design(cavity)["hash"]
                        else ("moved", ("config.cavity_w moved",)))
            return real_moves(article, now)

        with mock.patch.object(verdicts, "_article_moves", moves):
            self.standings = verdicts.judge_results(self.ledger, here)
        self.view = verdicts.view(self.ledger, verdicts.Resolution(
            verdicts=list(verdicts_ or []), standings=self.standings))
        self.composed = claims.compose(self.view.claim(base.id), self.view.verdicts)

    @property
    def reads(self) -> tuple[str, str]:
        return self.composed.status.value, self.composed.cause.value


A70 = {("cavity_w",): 70.0}
A72 = {("cavity_w",): 72.0}

#: A generator's source, and the same with one line that changes nothing it
#: writes (review of P2.5b, finding 1: `unused = None` moved the code digest, so
#: the article moved, and the same print, reprinted byte for byte, superseded
#: its own fail).
GEN = b"def make(ctx):\n    return None\n"
GEN_NOOP = GEN + b"unused = None\n"


def _code(source: bytes) -> dict:
    return {"generators/gen.py": verdicts.canonical_ast_digest(source)}


#: One package's generator files, byte for byte, for the rows where two
#: articles printed one object.
SAME_PRINT = {"part.svg": "a" * 64, "model.json": "m" * 64, "REPORT.md": "r" * 64}


class AFailIsSupersededOnlyOnAnotherExportedArticle(_env.EnvCase):
    """(V-10, invariant 11; PLAN Q2.11, R-3 "for every object but the one that
    failed") A fail F on an exported, traced article A stops counting only
    beside a pass P a person records on ANOTHER exported, traced article B —
    which `exports/` holds, which is current, and which differs from A on a row
    A recorded — while the design is no longer A. F counts again the moment the
    design returns to A. Every other later pass leaves F Failing."""

    def judge(self, entries: list[dict], *, cavity: float, exports: list[str],
              **kw: Any) -> _Judged:
        return _Judged(self.tmp(), entries, cavity=cavity, exports=exports, **kw)

    def failed_on(self, article: dict) -> dict:
        return {"passed": False, "detail": "a gap at the seam", "article": article}

    def passed(self, article: dict, **kw: Any) -> dict:
        return {"passed": True, "detail": "assembled", "article": article, **kw}

    def rows(self) -> dict[str, tuple[_Judged, tuple[str, str]]]:
        """``{row: (judged, (status, cause) it must read)}`` — §4.5 row by row,
        and critique 2's rows."""
        E, E2 = _exported(A70), _exported(A72)
        Ec, Ec2 = _exported(A70, model=_code(GEN)), _exported(A70, model=_code(GEN_NOOP))
        both = [E["hash"], E2["hash"]]
        return {
            "a: a pass on another exported article, the design moved":
                (self.judge([self.failed_on(E), self.passed(E2)], cavity=72.0, exports=both),
                 ("verified", "on-article")),
            "b: the design returned to the failed article":
                (self.judge([self.failed_on(E), self.passed(E2)], cavity=70.0, exports=both),
                 ("refuted", "physical-fail")),
            "c: a pass on a design article after the move":
                (self.judge([self.failed_on(E), self.passed(_design(72.0))], cavity=72.0,
                            exports=both), ("refuted", "physical-fail")),
            "d: a pass on the failed article itself":
                (self.judge([self.failed_on(E), self.passed(E)], cavity=70.0, exports=both),
                 ("refuted", "physical-fail")),
            "e: a pass from an agent session on another exported article":
                (self.judge([self.failed_on(E), self.passed(E2, channel="agent-session s1")],
                            cavity=72.0, exports=both), ("refuted", "physical-fail")),
            "f: the other article's export record removed":
                (self.judge([self.failed_on(E), self.passed(E2)], cavity=72.0,
                            exports=[E["hash"]]), ("refuted", "physical-fail")),
            "j: the fail on a design article, a pass on an exported one":
                (self.judge([self.failed_on(_design(70.0)), self.passed(E2)], cavity=72.0,
                            exports=both), ("refuted", "physical-fail")),
            "k: the fail on an untraced export":
                (self.judge([self.failed_on(_exported(A70, traced=False)), self.passed(E2)],
                            cavity=72.0, exports=both + [_exported(A70, traced=False)["hash"]]),
                 ("refuted", "physical-fail")),
            "l: the pass's article differs only in a row the failed one never read":
                (self.judge([self.failed_on(E), self.passed(_exported({("cell_mah",): 2000.0},
                                                                  milestone="board"))],
                            cavity=72.0, exports=both + [_exported(
                                {("cell_mah",): 2000.0}, milestone="board")["hash"]]),
                 ("refuted", "physical-fail")),
            # Review of P2.5b (finding 1): B differs from A on a row A recorded
            # — its generator's code — and the generator wrote the same bytes.
            # The object printed is the one that failed.
            "m: generator code differs, the package byte for byte the failed one's":
                (self.judge([self.failed_on(Ec), self.passed(Ec2)], cavity=70.0,
                            exports=[Ec["hash"], Ec2["hash"]],
                            packages={Ec["hash"]: SAME_PRINT, Ec2["hash"]: SAME_PRINT},
                            files={"generators/gen.py": GEN_NOOP}),
                 ("refuted", "physical-fail")),
            "n: generator code differs and so do the bytes it wrote":
                (self.judge([self.failed_on(Ec), self.passed(Ec2)], cavity=70.0,
                            exports=[Ec["hash"], Ec2["hash"]],
                            files={"generators/gen.py": GEN_NOOP}),
                 ("verified", "on-article")),
            "o: the failed article's export record removed (its bytes unknown)":
                (self.judge([self.failed_on(E), self.passed(E2)], cavity=72.0,
                            exports=[E2["hash"]]), ("refuted", "physical-fail")),
        }

    def problems(self) -> list[str]:
        return [f"{name}: reads {judged.reads}, not {want}"
                for name, (judged, want) in self.rows().items() if judged.reads != want]

    def test_each_row(self):
        self.assertEqual(self.problems(), [])

    def test_the_superseded_fail_is_named_and_kept(self):
        E, E2 = _exported(A70), _exported(A72)
        judged = self.judge([self.failed_on(E), self.passed(E2)], cavity=72.0,
                            exports=[E["hash"], E2["hash"]])
        standing = judged.standings["K5"]
        self.assertEqual(tuple(getattr(standing, "superseded", ()) or ()), (0,))
        self.assertIsNone(getattr(standing, "fail", "absent"))
        said = report.reason(judged.composed, judged.view, judged.view.claim("K5"))
        self.assertIn(report.article12(E), said, "the row does not name the superseded fail")
        self.assertEqual(claims.rebuild(judged.view), [],
                         "the rebuild prediction still names the superseded fail's article")
        self.assertEqual(judged.claim.physical_result.passed, False,
                         "a raw ledger reader (no standing) must read the fail (R-2)")
        raw = claims.compose(judged.claim, [])
        self.assertEqual(raw.status, ClaimStatus.REFUTED)

    def test_the_superseded_fail_does_not_count_where_results_are_listed(self):
        """`why`'s PHYSICAL RESULTS row, the page's and JSON's `counts`
        (`report.result_facts`): the superseded fail does not count, naming the
        pass that superseded it — never "it counts" beside a row reading
        Checked. The fail it supersedes nothing for still counts."""
        E, E2 = _exported(A70), _exported(A72)
        judged = self.judge([self.failed_on(E), self.passed(E2)], cavity=72.0,
                            exports=[E["hash"], E2["hash"]])
        claim = judged.view.claim("K5")
        failed = report.result_facts(claim, claim.results[0])
        self.assertFalse(failed["counts"])
        self.assertIn(report.article12(E2), failed["why"])
        self.assertTrue(report.result_facts(claim, claim.results[1])["counts"])
        unsuperseded = self.judge([self.failed_on(E), self.passed(E)], cavity=72.0,
                                  exports=[E["hash"]])
        claim = unsuperseded.view.claim("K5")
        self.assertTrue(report.result_facts(claim, claim.results[0])["counts"])

    def test_a_superseded_fail_keeps_its_contradiction(self):
        E, E2 = _exported(A70), _exported(A72)
        contra = [{"gate": "fig4.enclosure_fit", "code": "c" * 64, "rho": "r" * 64,
                   "value": 76.0, "units": "mm", "inside": True}]
        judged = self.judge([dict(self.failed_on(E), contradicts=contra), self.passed(E2)],
                            cavity=72.0, exports=[E["hash"], E2["hash"]])
        self.assertEqual(judged.reads, ("verified", "on-article"))
        record = verdicts.track_record(judged.ledger)
        self.assertEqual([c.gate for c in record.get("fig4.enclosure_fit", ())],
                         ["fig4.enclosure_fit"])

    def test_an_automated_claims_fail_is_superseded_by_a_reprints_pass(self):
        """A fail on an automated claim (E4: a ruler on C1) is superseded by a
        pass on the reprint — a pass that settles nothing an evaluator settles,
        and stands on its article — and the claim reads its evaluators again
        (critique 13 of the P2.5b design: otherwise no fix and reprint ever
        reads Checked)."""
        E, E2 = _exported(A70), _exported(A72)
        claim = Claim(id="K1", statement="The enclosure fits the bay", gates=["fig4.fit"],
                      acceptance=Acceptance(quantity="enclosure width", limit=100.0,
                                            units="mm"))
        verdict = Verdict(gate="fig4.fit", claims=["K1"], passed=True, measured=78.0,
                          limit=100.0, units="mm")
        judged = self.judge([self.failed_on(E), self.passed(E2, evidence=[],
                                                       evidence_sha256={})],
                            cavity=72.0, exports=[E["hash"], E2["hash"]], claim=claim,
                            verdicts_=[verdict])
        self.assertEqual(judged.reads, ("pass", "checked"))
        back = self.judge([self.failed_on(E), self.passed(E2, evidence=[], evidence_sha256={})],
                          cavity=70.0, exports=[E["hash"], E2["hash"]], claim=claim,
                          verdicts_=[verdict])
        self.assertEqual(back.reads[0], "refuted")

    def test_the_planted_judges_are_caught(self):
        """Planted into `verdicts._supersedes`: any later counting pass; B == A
        allowed; supersession never judged against the design again; B and A
        compared by hash alone; a fail on a design article supersedable."""
        real = _need(verdicts, "_supersedes")

        def any_pass(fail, fs, passing, ps):
            return bool(ps.counts)

        def same_article(fail, fs, passing, ps):
            return ps.counts and (fs.article_state == "moved" or True) and \
                (passing.article or {}).get("source") == "export"

        def not_current(fail, fs, passing, ps):
            # Supersession decided once and never judged against the design
            # again: the design back on the failed article (row b) still leaves
            # the fail superseded. (A moved and B current are one fact here: B
            # current and differing from A on a row A recorded imply A moved.)
            return real(fail, dataclasses.replace(fs, article_state="moved"), passing,
                        dataclasses.replace(ps, stands=ps.passed and ps.why in (
                            "", "article-moved", "beside")))

        def hash_only(fail, fs, passing, ps):
            a, b = fail.article or {}, passing.article or {}
            return (ps.counts and a.get("source") == b.get("source") == "export"
                    and a.get("traced") and b.get("traced") and a.get("hash") != b.get("hash")
                    and fs.article_state == "moved")

        def design_ok(fail, fs, passing, ps):
            a = dict(fail.article or {})
            if a.get("source") == "design":
                return bool(ps.counts) and fs.article_state == "moved"
            return real(fail, fs, passing, ps)

        real_judged = verdicts._judged

        def released_to_any_pass(claim, standing):
            # The view's half of the same violator: the fail released to the
            # newest pass, whether or not it stands on its article now.
            found = real_judged(claim, standing)
            if standing is not None and standing.superseded and standing.fail is None:
                passes = [e.index for e in standing.entries if e.passed]
                if passes:
                    found = dataclasses.replace(found, physical_result=claim.results[passes[-1]])
            return found

        def rows_only(fail, fs, passing, ps):
            # The recorded rows compared and never the bytes the generator
            # wrote: a code-only edit releases the same print (finding 1).
            return real(fail, dataclasses.replace(fs, built=("a" * 64,)), passing,
                        dataclasses.replace(ps, built=("b" * 64,)))

        for name, planted, row in (("any later pass", any_pass, "c:"),
                                   ("B == A allowed", same_article, "d:"),
                                   ("never judged against the design again", not_current, "b:"),
                                   ("hash only", hash_only, "l:"),
                                   ("a design article's fail", design_ok, "j:"),
                                   ("the bytes written never compared", rows_only, "m:")):
            with self.subTest(name), mock.patch.object(verdicts, "_supersedes", planted), \
                    mock.patch.object(verdicts, "_judged", released_to_any_pass):
                found = self.problems()
                self.assertTrue([p for p in found if p.startswith(row)],
                                f"{name} was not caught at row {row}: {found}")


class AReprintAfterAFailIsADecision(_env.EnvCase):
    """(V-10, end to end; critique 13 of the P2.5b design) A fail on a claim the
    milestone requires keeps `export` refused — the reprint that could answer it
    is a decision a person records (`--proceed`), never a carve-out — and a
    pass on the reprint, once the design moved, supersedes the fail: the next
    export is ready. Back on the failed design, the fail counts again."""

    def test_fail_reprint_pass(self):
        import test_fig4 as F
        root, article = _fig4_exported(self)
        P.tty(root, "claim", "physical", "K1", "fail", "--measured", "104",
              "--detail", "the shell measured 104 mm", "--article", article,
              answer="K1", code=0)
        self.assertEqual(_status(root, "K1")[0], "refuted")
        F.set_cavity(root, 72.0)
        refused = F.export(root, "enclosure", code=1)
        self.assertEqual([(r["kind"], r["subject"]) for r in refused["refusals"]],
                         [("unresolved", "K1")])
        text = P.run(root, "export", "enclosure", "--dry-run", code=1).stdout
        self.assertIn("--proceed", text, "the refusal does not say a reprint is a decision")
        P.tty(root, "export", "enclosure", "--proceed", "--why", "the reprint after the fix",
              answer="enclosure", code=0)
        reprint = P.exports(root, "enclosure")["exports"][-1]["article"]["hash"]
        self.assertNotEqual(reprint, article)
        P.tty(root, "claim", "physical", "K1", "pass", "--measured", "78",
              "--detail", "the reprint measured 78 mm", "--article", reprint,
              answer="K1", code=0)
        self.assertEqual(_status(root, "K1")[:2], ("pass", "checked"))
        F.export(root, "enclosure", "--dry-run", code=0)
        F.set_cavity(root, 70.0)
        self.assertEqual(_status(root, "K1")[0], "refuted")

    def test_a_fail_no_reprint_can_release_is_never_offered_one(self):
        """Review of P2.5b (finding 17): a fail recorded without `--article`
        sits on a design article, which supersession never releases — yet the
        refusal offered "building a new article to test it again is a
        decision", a path that led nowhere. It says the fail counts on every
        design, and what that leaves: a go-ahead each time."""
        import test_export as X
        root = X.bracket(os.path.join(self.tmp(), "w"), thickness=8.0, git=True)
        P.milestone(root, "weather", ["C5"], generator="generators/profile.py:side_profile")
        P.run(root, "check")
        P.run(root, "claim", "physical", "C5", "fail", "--detail", "crazing at the root",
              code=0)
        _projects.set_thickness(root, 8.5)
        text = P.run(root, "export", "weather", "--dry-run", code=1).stdout
        self.assertNotIn("building a new article to test it again", text)
        self.assertIn("no pass on a reprint releases", text)
        self.assertIn("--proceed", text)

    def test_a_no_op_edit_to_the_generator_reprints_the_object_that_failed(self):
        """Review of P2.5b (finding 1): one line that changes nothing the
        generator writes moves its code, so the article moves — and the reprint,
        byte for byte the print that failed, must not release its fail."""
        import test_export as X
        root = X.bracket(os.path.join(self.tmp(), "b"), thickness=8.0, git=True)
        P.run(root, "check")
        P.run(root, "export", "print-v1", code=0)
        first = P.exports(root, "print-v1")["exports"][-1]
        P.tty(root, "claim", "physical", "C1", "fail", "--measured", "0.62",
              "--detail", "ruler at the tip", "--article", first["article"]["hash"],
              answer="C1", code=0)
        self.assertEqual(_status(root, "C1")[0], "refuted")
        _append(os.path.join(root, "generators", "profile.py"), "\nunused = None\n")
        P.tty(root, "export", "print-v1", "--proceed", "--why", "a reprint",
              answer="print-v1", code=0)
        second = P.exports(root, "print-v1")["exports"][-1]
        self.assertNotEqual(second["article"]["hash"], first["article"]["hash"],
                            "the no-op edit did not move the article: the row is vacuous")
        self.assertEqual(
            {k: v for k, v in second["package"]["files"].items() if k != "REPORT.md"},
            {k: v for k, v in first["package"]["files"].items() if k != "REPORT.md"},
            "the generator wrote other bytes: the row is not the one it names")
        P.tty(root, "claim", "physical", "C1", "pass", "--measured", "0.47",
              "--detail", "the same print, measured again", "--article",
              second["article"]["hash"], answer="C1", code=0)
        self.assertEqual(_status(root, "C1")[0], "refuted",
                         "a byte-for-byte reprint released the fail on the object it is")


class LatencyIsDeclaredUntilMeasured(_env.EnvCase):
    """(V-13, W3; GLOSSARY §5 *latency*) A physical claim declares its expected
    latency — `expected_latency: {value, units}` — read until an article
    measures it: a result's `when` minus its exported article's `when`. Only a
    measurement takes one (critique 16 of the P2.5b design); the strict reader
    refuses it elsewhere, a bad value or unit; it moves no claim digest."""

    def _write(self, record: dict) -> str:
        root = self.tmp()
        path = os.path.join(root, "claims", "C9.json")
        P.write_json(path, record)
        return path

    def test_the_strict_reader(self):
        physical = {"statement": "s", "kind": "physical", "note": "n"}
        accepted = dict(physical, expected_latency={"value": 2, "units": "week"})
        claim = store.read_record(self._write(accepted), "claims")
        self.assertEqual(getattr(claim, "expected_latency", None), {"value": 2, "units": "week"})
        refused = {
            "on an automated claim": {"statement": "s", "kind": "measurable",
                                      "expected_latency": {"value": 1, "units": "day"}},
            "on an expert judgment": {"statement": "s", "kind": "assumption",
                                      "terminal": "human", "authority": "Dana",
                                      "expected_latency": {"value": 1, "units": "day"}},
            "months": dict(physical, expected_latency={"value": 1, "units": "month"}),
            "zero": dict(physical, expected_latency={"value": 0, "units": "day"}),
            "negative": dict(physical, expected_latency={"value": -1, "units": "day"}),
            "text": dict(physical, expected_latency={"value": "1", "units": "day"}),
            "a key too many": dict(physical, expected_latency={"value": 1, "units": "day",
                                                               "when": "now"}),
        }
        for name, record in refused.items():
            with self.subTest(name):
                with self.assertRaises(AtompipeError) as caught:
                    store.read_record(self._write(record), "claims")
                self.assertIn("claims/C9.json", str(caught.exception))
                self.assertIn("expected_latency", str(caught.exception))

    def test_it_moves_no_digest(self):
        claim = Claim(id="C9", statement="s", kind=ClaimKind.PHYSICAL, note="n")
        if "expected_latency" not in {f.name for f in dataclasses.fields(Claim)}:
            self.fail("Claim.expected_latency does not exist yet")
        declared = dataclasses.replace(claim, expected_latency={"value": 1, "units": "day"})
        self.assertEqual(claims.claim_digest(declared), claims.claim_digest(claim))
        self.assertEqual(verdicts._claim_digest(dataclasses.replace(claim, expected_latency={})),
                         verdicts._claim_digest(claim))

    def _words(self, results: list[dict], exports: list[dict]) -> str:
        from atompipe.models import ExportRecord
        claim = Claim(id="K8", statement="s", kind=ClaimKind.PHYSICAL, note="n",
                      results=tuple(PhysicalResult.from_dict(r) for r in results))
        if "expected_latency" not in {f.name for f in dataclasses.fields(Claim)}:
            self.fail("Claim.expected_latency does not exist yet")
        claim = dataclasses.replace(claim, expected_latency={"value": 1, "units": "day"})
        records = [ExportRecord(milestone="board", article=a) for a in exports]
        return _need(report, "latency_words")(claim, records)

    def test_declared_until_measured(self):
        B = _exported({("board",): 20.0}, milestone="board", when="2026-10-04T10:00:00Z")
        self.assertEqual(self._words([], [B]), "expected 1 day (declared)")
        measured = self._words([{"passed": True, "when": "2026-10-05T12:00:00Z",
                                 "article": B, "channel": "interactive"}], [B])
        self.assertEqual(measured, f"measured 26 h on article {B['hash'][:12]} "
                                   f"(expected 1 day)")
        on_design = self._words([{"passed": True, "when": "2026-10-05T12:00:00Z",
                                  "article": _design(70.0), "channel": "interactive"}], [B])
        self.assertEqual(on_design, "expected 1 day (declared)")

    def test_only_a_persons_result_measures_it(self):
        """Review of P2.5b (finding 4): a result that counts for nothing — a pass
        from an agent session, anything from a pipe — measured the latency, so a
        two-winter test read "measured 0 min" the moment an agent typed a pass.
        Only an entry a person typed in their own shell measures it, pass or
        fail."""
        B = _exported({("board",): 20.0}, milestone="board", when="2026-10-04T10:00:00Z")
        later = "2026-10-05T12:00:00Z"
        for name, entry, measured in (
                ("an agent's pass", {"passed": True, "channel": "agent-session s1"}, False),
                ("a pipe's fail", {"passed": False, "channel": "non-interactive"}, False),
                ("a legacy entry", {"passed": True}, False),
                ("a person's fail", {"passed": False, "channel": "interactive"}, True),
                ("a person's pass", {"passed": True, "channel": "interactive"}, True)):
            with self.subTest(name):
                said = self._words([dict(entry, when=later, article=B)], [B])
                self.assertEqual(said.startswith("measured "), measured, said)

    def test_measured_from_the_newest_export_before_the_result(self):
        """Review of P2.5b (finding 7): one article, exported twice — the span
        runs from the newest export that is not after the result, never from
        whichever record a result happened to copy."""
        first = _exported({("board",): 20.0}, milestone="board", when="2026-10-04T10:00:00Z")
        again = dict(first, when="2026-10-05T10:00:00Z", milestone="bench")
        result = {"passed": True, "channel": "interactive", "article": first}
        self.assertTrue(self._words([dict(result, when="2026-10-05T12:00:00Z")],
                                    [first, again]).startswith("measured 2 h "))
        self.assertTrue(self._words([dict(result, when="2026-10-04T12:00:00Z")],
                                    [first, again]).startswith("measured 2 h "))

    def test_an_automated_claim_has_no_latency_of_an_article(self):
        """Review of P2.5b (finding 24; GLOSSARY §5): an automated evaluator's
        latency is its run's, measured per run. A ruler's fail on C1, recorded
        on an exported article, measured no latency of C1's — `why` showed
        "latency: measured 6 min", and P4's Λ₀ would have read it."""
        from atompipe import decisions
        from atompipe.models import ExportRecord
        B = _exported({("board",): 20.0}, milestone="board", when="2026-10-04T10:00:00Z")
        claim = Claim(id="K1", statement="s", gates=["fig4.fit"],
                      acceptance=Acceptance(quantity="width", limit=100.0, units="mm"),
                      results=(PhysicalResult.from_dict(
                          {"passed": False, "when": "2026-10-04T10:06:00Z", "article": B,
                           "channel": "interactive", "measured": 104.0}),))
        found = claims.latency(claim, [ExportRecord(milestone="board", article=B)])
        self.assertEqual(found.source, "none")
        text = decisions.why(Ledger(claims=[claim],
                                    exports=[ExportRecord(milestone="board", article=B)]), "K1")
        self.assertNotIn("latency:", text)

    def test_why_says_it_where_there_is_one(self):
        """`why` and `claim show` (P2.5b-D19): a `latency:` row on a claim that
        declares one or has measured one, and none on a physical claim with
        neither — an empty row on every physical claim would say nothing."""
        from atompipe import decisions
        from atompipe.models import ExportRecord
        bare = Claim(id="K8", statement="s", kind=ClaimKind.PHYSICAL, note="n")
        declared = dataclasses.replace(bare, expected_latency={"value": 1, "units": "day"})
        B = _exported({("board",): 20.0}, milestone="board", when="2026-10-04T10:00:00Z")
        measured = dataclasses.replace(bare, results=(PhysicalResult.from_dict(
            {"passed": True, "when": "2026-10-05T12:00:00Z", "article": B,
             "channel": "interactive"}),))
        exports = [ExportRecord(milestone="board", article=B)]
        for name, claim, want in (("neither", bare, None),
                                  ("declared", declared, "  latency: expected 1 day (declared)"),
                                  ("measured", measured, f"  latency: measured 26 h on article "
                                                         f"{B['hash'][:12]}")):
            with self.subTest(name):
                text = decisions.why(Ledger(claims=[claim], exports=exports), "K8")
                rows = [ln for ln in text.splitlines() if ln.startswith("  latency:")]
                self.assertEqual(rows, [want] if want else [], text)

    def test_a_planted_measure_from_a_design_article_is_caught(self):
        """Planted: a latency measured from a design article too (its `when`
        absent read as the result's own)."""
        real = _need(claims, "latency")

        def any_article(claim, exports):
            for entry in reversed(tuple(getattr(claim, "results", ()) or ())):
                article = dict(getattr(entry, "article", None) or {})
                if article and article.get("source") == "design":
                    return claims.Latency(0.0, "measured", article.get("hash", ""))
            return real(claim, exports)

        B = _exported({("board",): 20.0}, milestone="board")
        with mock.patch.object(claims, "latency", any_article):
            said = self._words([{"passed": True, "when": "2026-10-05T12:00:00Z",
                                 "article": _design(70.0)}], [B])
        self.assertNotEqual(said, "expected 1 day (declared)")

    def test_a_planted_measure_from_an_agents_result_is_caught(self):
        """Planted: the channel ignored — an agent's pass measures it."""
        real = _need(claims, "latency")
        B = _exported({("board",): 20.0}, milestone="board", when="2026-10-04T10:00:00Z")

        def any_channel(claim, exports):
            people = tuple(dataclasses.replace(e, channel="interactive")
                           for e in claim.results)
            return real(dataclasses.replace(claim, results=people), exports)

        entry = {"passed": True, "when": "2026-10-05T12:00:00Z", "article": B,
                 "channel": "agent-session s1"}
        self.assertEqual(self._words([entry], [B]), "expected 1 day (declared)")
        with mock.patch.object(claims, "latency", any_channel):
            said = self._words([entry], [B])
        self.assertNotEqual(said, "expected 1 day (declared)", "the planted latency was not "
                                                               "caught")


if __name__ == "__main__":
    unittest.main(verbosity=2)
