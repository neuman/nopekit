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
from atompipe.util import AtompipeError


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
            extra = {name: value for name, value in (("terminal", ""), ("authority", ""))
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
