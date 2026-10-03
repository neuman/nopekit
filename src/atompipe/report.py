# SPDX-License-Identifier: Apache-2.0
"""atompipe.report — the ledger rendered: what is proven, what is not, and why.

This is the deliverable. Everything else in the spine exists so that this document
can be *generated* rather than written, and its credibility comes entirely from
what it refuses to claim. The shape it aims for reads:

    "Ready to manufacture: routed, rule-check clean, package exported, firmware fixed,
     mechanicals fit-checked. It is unverified in physical hardware."

The second sentence is the only reason anyone believed the first. Both sentences
are generated here out of claim statuses, and the order is not negotiable: if
anything critical is failing, stale, blocked, unrun or ungated, the verdict says
so before it says anything good. A report that leads with the good news and
buries the gap is a marketing document.

Five rules are made mechanical here, each because the corresponding mistake is
easy and has been made:

1. **The PROVEN table is built from `Verdict.ok`, never from `Verdict.passed`.**
   A skipped gate carries `passed=False` today — but a gate function that returns
   a dict missing the key, or a pack that sets `passed=True` next to
   `skipped=True`, would walk straight into the proof table. `ok` is the one
   predicate meaning "it ran, it did not crash, and it said yes". This is the
   report-side half of "a logger is not a gate": the gate registry refuses gates
   that cannot fail, and the report refuses to print gates that did not run.

2. **A claim never reads Checked while a gate that covers it never ran.**
   Until P2.1 it could: the resolver saw only the verdicts that existed, a
   registered-but-unrun gate produced none, and the report marked the row
   PARTIAL and printed it under PROVEN anyway (S-03). GLOSSARY §3's composition
   (`claims.compose`) reads that claim Open, a pass beside a skip or a crash
   Skipped, and one beside a refused evaluator Gap — so there is nothing left to
   mark partial, and a Checked status its evidence contradicts is printed as a
   contradiction, loudly, outside the checked section (D18). The registry is
   still a parameter of every function here, so the report can see the unrun
   gate and name it.

3. **Staleness is carried all the way through to the table.** `stale_gates` —
   the gates `verdicts.resolve` found not current — turns each PASS they cover
   into STALE, and those rows leave the PROVEN table; `stale=True` does it for
   every gate at once. A missing row with a reason is the honest render; a full
   table of yesterday's numbers is a lie with a timestamp. What it replaced: one
   flag for the whole project, from one hash of the projection, so a comment
   edit in the model emptied the table and nothing said which result had moved.

4. **Absence is reported as loudly as failure.** A parameter with no rationale is
   a number nobody can defend; an ingested artifact with nothing extracted from it
   is evidence nobody read; a physical claim with no written test is one that will
   never be verified. All three get their own lines, because none of them shows up
   as a failing gate and all three sink builds.

5. **JUnit is never greener than the exit code.** CI renders the XML, not the
   exit code, and a test tab of "12 passed, 3 skipped" beside a job that exited 1
   invites somebody to fix the "flaky" exit code. So a testcase is childless only
   for `outcome == "pass"`, the critical-claims suite fails exactly where
   `claims.blocking` does, and an exit code the rendered verdicts do not explain
   is itself rendered as a failure (`render_junit`, `render_selftest_junit`).

6. **Every human word for a status comes from one table, `HUMAN`** (PLAN
   D-16), which equals GLOSSARY §3: Table 1's seven words plus Open. Every
   channel — `status`, `check`, `claim`, `why`, the report, JUnit messages,
   `state.json` and the page's labels — reads it through the helpers here
   (`word`, `status_tag`, `reason`, `count_line`, `severity`, `status_view`,
   `words_table`). What slipped through before it (S-69): three vocabularies at
   once — `[uncl]` in `check`, `[gap  ]` in `status`, NO GATE on the page —
   and words the paper's readers would misread ("machine-verified", "blocked on
   missing tooling" for a crash, "PARTIAL" for a lesser success).

Nothing in this module reads a clock or a module-level registry, and the
markdown report prints no time at all: a regenerated `docs/readiness.md` changes
only when the claims or the verdict outcomes do (S-89). It used to be titled
with the last sweep's time and to end with that sweep's model and inputs hashes,
so every re-run of an unchanged design rewrote a tracked file — and once
verdicts are served from the cache, "this run" would be false on every hit. The
registry is passed in — a report that reached for `gates.REGISTRY` could only be
tested against a project it had already imported.
"""
from __future__ import annotations

import math
import os
import re
from collections.abc import Mapping
from types import MappingProxyType
from typing import Any, Collection, Iterable, NamedTuple, Sequence

from . import __version__
from . import claims as claim_logic
from . import modelio
from . import store
from . import verdicts as verdict_logic
from .artifacts import unextracted
from .claims import ClaimCause
from .models import (
    _RENDER_TAG,
    BLOCKING_STATUSES,
    Claim,
    ClaimKind,
    ClaimStatus,
    Ledger,
    Need,
    NeedStatus,
    Tier,
    Verdict,
)
from .util import atomic_write_text, ensure_dir

# --------------------------------------------------------------------------- #
# vocabulary: `HUMAN`, the one table every human word for a status comes from
# --------------------------------------------------------------------------- #
class StatusWords(NamedTuple):
    """One GLOSSARY §3 row, as every channel speaks it.

    ``key`` — the JSON token (``claims.STATUS_KEY``, GLOSSARY §8's rename-pass
    value); ``word`` — prose, counts and JSON ``word`` ("pending build");
    ``term`` — headings and page chips ("Pending build"); ``plural`` — a count
    above one ("gaps"); ``tag`` — the five-wide terminal tag; ``hint`` — what it
    means, one line (the page's chip title); ``rank`` — its place in
    `claims.SEVERITY_ORDER`, most urgent first, read from there (a rank is not
    a word; the one order and its rejected alternatives live beside it)."""

    key: str
    word: str
    term: str
    plural: str
    tag: str
    hint: str
    rank: int


def _status_row(status: ClaimStatus, word: str, term: str, plural: str, tag: str,
                hint: str) -> StatusWords:
    key = claim_logic.STATUS_KEY[status]
    return StatusWords(key, word, term, plural, tag, hint,
                       claim_logic.SEVERITY_ORDER.index(key))


# Each row is GLOSSARY §3's, typed here — the glossary is a development document
# the release bundle strips, so the words reach a user only through this table.
# (`P2.1-Dn`, here and below: the decision rows of `docs/plan/phase-2.md`, "P2.1's
# decision rows", each with what was rejected.)
#
# Tags are five wide, one per status (P2.1-D14): `ok   ` Checked, `FAIL `
# Failing, `STALE`, `assum` Assumed, `build` Pending build, `gap  ` Gap, `skip `
# Skipped and `SKIP ` a crash's Skipped, `open ` Open. Five, because every grep
# and P2.0's invariant parsers key on `\[.{5}\]`; the loud ones — upper case —
# are exactly Failing, errored and Stale, the tone `test_louder.tone_of` reads.
# *Rejected:* full-term tags (`[pending build]`: P2.0's parsers would need an
# R-6 edit to pass, so they wait for the taste batch, where the change is this
# table plus the parsers); `REFUT`, `phys `, `unrun` and `ok-hw` (each a GLOSSARY
# §3 Never-say, or the evaluator's word put on a claim); `[ERR  ]` on a claim
# row (an outcome's tag on a status row); `skip!` (loudness by punctuation, which
# no tone reader sees); `chkd ` and `check` for Checked (an abbreviation nobody
# says, and the verb `atompipe check`).
#
# Hints never say "qualified" or "built from them" for Checked while a
# known-bad-shown evaluator still counts (P2.3) and no article binds a physical
# pass: saying so would be the overclaim in words the P2.1 design rejected
# (critique: the hint reaches the page's chip title).
_CHECKED = _status_row(
    ClaimStatus.PASS, "checked", "Checked", "checked", "ok   ",
    "every evaluator passed on the current inputs — checked does not mean true")
_FAILING = _status_row(
    ClaimStatus.FAIL, "failing", "Failing", "failing", "FAIL ",
    "an evaluator failed the current candidate, or a physical result failed")
_STALE = _status_row(
    ClaimStatus.STALE, "stale", "Stale", "stale", "STALE",
    "a pass whose read set has changed since: nothing is checked now")
_ASSUMED = _status_row(
    ClaimStatus.ASSERTED, "assumed", "Assumed", "assumed", "assum",
    "accepted provisionally, with a reason and an owner — unresolved")
_PENDING_BUILD = _status_row(
    ClaimStatus.UNVERIFIED, "pending build", "Pending build", "pending build", "build",
    "waits on an article: no physical result yet, or a pass no article binds to the "
    "current inputs")
_GAP = _status_row(
    ClaimStatus.UNCLAIMED, "gap", "Gap", "gaps", "gap  ",
    "no evaluator, none qualified, or one unqualified; or an assumption nobody owns")
_SKIPPED = _status_row(
    ClaimStatus.BLOCKED, "skipped", "Skipped", "skipped", "skip ",
    "an evaluator skipped, its tool missing here, and none failed: no usable verdict")
_OPEN = _status_row(
    ClaimStatus.PENDING, "open", "Open", "open", "open ",
    "an evaluator of the claim is unrun on the current inputs")
#: Skipped by a crash: the same status, louder (PLAN-v0.14 §1.5) — Failing's
#: tone in its tag, above every skipped row in its rank.
_SKIPPED_ERRORED = _SKIPPED._replace(
    tag="SKIP ", rank=claim_logic.SEVERITY_ORDER.index("errored"),
    hint="an evaluator errored: it crashed, so nothing was evaluated, and the evaluator "
         "itself is broken")

HUMAN: Mapping[str, Any] = MappingProxyType({
    # ClaimStatus -> its row. Ten members, eight rows: a physical pass is
    # Checked and a physical fail Failing — the terminal is the claim's, not
    # the status's (GLOSSARY §8).
    "status": MappingProxyType({
        ClaimStatus.PASS: _CHECKED, ClaimStatus.VERIFIED: _CHECKED,
        ClaimStatus.FAIL: _FAILING, ClaimStatus.REFUTED: _FAILING,
        ClaimStatus.STALE: _STALE, ClaimStatus.ASSERTED: _ASSUMED,
        ClaimStatus.UNVERIFIED: _PENDING_BUILD, ClaimStatus.UNCLAIMED: _GAP,
        ClaimStatus.BLOCKED: _SKIPPED, ClaimStatus.PENDING: _OPEN,
    }),
    "errored": _SKIPPED_ERRORED,
    # ClaimCause -> how its reason leads (P2.1-D15). "The reason line says
    # which" (GLOSSARY §3): the lead names the fact, then the evaluator.
    "lead": MappingProxyType({
        ClaimCause.FAILED: "",
        ClaimCause.PHYSICAL_FAIL: "failed on an article",
        ClaimCause.ERRORED: "errored",
        ClaimCause.SKIPPED: "skipped",
        ClaimCause.UNQUALIFIED: "unqualified",
        ClaimCause.NO_EVALUATOR: "no evaluator",
        ClaimCause.NO_OWNER: "no owner recorded",
        ClaimCause.OWNER_UNATTRIBUTED: "owner {owner} is named in claims/{id}.json and "
                                       "has not recorded it — no command can record it yet",
        ClaimCause.NO_REASON: "no reason recorded",
        ClaimCause.UNRUN: "unrun",
        ClaimCause.INVALIDATED: "invalidated",
        ClaimCause.NO_ARTICLE: "needs an article",
        ClaimCause.OWNED: "assumed by {owner}",
        ClaimCause.PHYSICAL_PASS: "a pass {recorded}, not bound to an article",
        ClaimCause.CHECKED: "—",
    }),
    # Who entered a physical result (GLOSSARY §1, *recorded by*): named, or the
    # unattributed form. What slipped through (review of P2.1): one template for
    # both, filled with the word "unattributed" — "recorded by unattributed".
    "recorded": MappingProxyType({"named": "recorded by {who}",
                                  "unattributed": "recorded, unattributed"}),
    # NeedStatus -> its word. `open` is a claim status only (GLOSSARY §6): a gap
    # record nobody has acted on is *identified*.
    "need": MappingProxyType({
        NeedStatus.OPEN: "identified", NeedStatus.PROPOSED: "proposed",
        NeedStatus.DEFERRED: "deferred", NeedStatus.INSTALLING: "installing",
        NeedStatus.SATISFIED: "satisfied", NeedStatus.ABANDONED: "abandoned",
    }),
    # Verdict.outcome -> its word (GLOSSARY §1: pass, fail, skipped, errored),
    # and its tag: `models._RENDER_TAG` itself, re-exported — one table, so a
    # tag changed there is changed here (critique of the P2.1 design: a copy
    # held equal by a test only says so by failing).
    "outcome": MappingProxyType({"pass": "pass", "fail": "fail", "skipped": "skipped",
                                 "error": "errored"}),
    # Each outcome in a line, for the page's verdict chips (their title). The
    # page held these itself until P2.1's review (D-16: the site never owns a
    # word, and an outcome word is one).
    "outcome_hint": MappingProxyType({
        "pass": "the evaluator ran and passed",
        "fail": "the evaluator ran and failed",
        "skipped": "its tool is missing here, so nothing was evaluated",
        "error": "the evaluator crashed — nothing was evaluated, and the evaluator itself "
                 "is broken",
    }),
    "outcome_tag": MappingProxyType(_RENDER_TAG),
    # The report's section headings (GLOSSARY §9), SECTION_PROVEN's text excepted:
    # it changes only with METHOD's (A-11, PLAN D-14).
    "heading": MappingProxyType({
        "pending_build": "## Pending build",
        "gaps": "## Gaps",
        "assumed": "## Assumed",
        "failing": "## Failing, stale, skipped or open",
        "reproduce": "## Reproduce",
    }),
    # An unqualified evaluator's refusal, as admission words it, in GLOSSARY §2's
    # words until P2.3 rewords the source: (admission's phrase, the glossary's).
    # Ordered: the first match per phrase wins. What slipped through the design
    # (review): the reason line carried "not admitted" and "known-bad fixture",
    # two §2 Never-says, on a claim's status row.
    "refusal": (
        ("PASSED its own known-bad fixture", "passed its own known-bad control"),
        ("its own known-bad input", "its own known-bad control"),
        ("control error:", "its known-bad control errored:"),
        ("control self-skip:", "its known-bad control skipped itself:"),
    ),
})


class _StatusTagView(Mapping):
    """`STATUS_TAG`: HUMAN's tags by status, read through at call time — a view of
    the one table, never a second one, so patching `HUMAN` moves it too."""

    def __getitem__(self, status: Any) -> str:
        # A status the enum does not know is a missing key, never a ValueError:
        # `Mapping.get` and `in` catch only KeyError. What slipped through
        # (review of P2.1): the dict this replaced answered `.get("bogus")` with
        # None, and the view raised out of both.
        try:
            return HUMAN["status"][_norm_status(status)].tag
        except ValueError:
            raise KeyError(status) from None

    def __iter__(self):
        return iter(HUMAN["status"])

    def __len__(self) -> int:
        return len(HUMAN["status"])


#: The five-wide tag per claim status, for every channel that prints one —
#: `HUMAN`'s, as a view (tests import the name). Deliberately NOT coloured: this
#: output is read by agents at least as often as by humans, and an ANSI escape
#: is noise in a transcript, a log file and a pipe to grep alike. An errored
#: claim's tag is `status_tag(status, errored=True)`, `[SKIP ]`.
STATUS_TAG: Mapping[ClaimStatus, str] = _StatusTagView()

#: The PROVEN section's heading, as the report emits it and as every test that
#: inspects that section finds it. The section's qualifier (`_proven_qualifier`)
#: follows it on the same line and is NOT part of it.
#:
#: What slipped through (S-15): invariant 4's tests located the section by a
#: literal copy of this text and read "no heading" as "an empty section", so
#: `assertNotIn(gate, section)` passed on nothing. A rename of the heading —
#: which P2's REPORT.md proposes (PLAN D-14, ask A-11) — would have kept the
#: invariant green while it tested no report at all. Now the report and the tests
#: share this one constant, and the tests fail when the report stops emitting it.
#:
#: Why this value: it is today's heading, so no rendered report changes; METHOD
#: rule 9 and invariant 4 both say PROVEN, and changing the word is a METHOD edit
#: (A-11), so a rename changes this constant's TEXT, never its name. *Rejected:*
#: the qualifier inside the constant — it is prose that changes on its own (P1.2
#: made it "machine-verified, current", P2.1 says checked), and every such edit
#: would move the key the tests search for. *Rejected:* matching any heading
#: containing "PROVEN" — a second section that happened to use the word would be
#: tested in its place. It is the ONE place a human channel still prints a word
#: GLOSSARY §3 retires, allowlisted by name in `test_vocabulary` until A-11.
SECTION_PROVEN = "## What is PROVEN"


def _proven_qualifier() -> str:
    """What follows `SECTION_PROVEN` on its line: what the rows under it are, in
    Checked's words. What slipped through: it read "(machine-verified this run)",
    then "(machine-verified, current)" — *machine-verified* is a GLOSSARY §3
    Never-say, and *verified* is the paper's word for something nothing here
    earns. *Rejected:* "a qualified evaluator passed" while a known-bad-shown
    evaluator still counts (P2.3) — an overclaim in words."""
    return (f"({word(ClaimStatus.PASS)}: every evaluator passed on the current inputs — "
            f"{word(ClaimStatus.PASS)} does not mean true)")


#: Caps for the terminal render. The contract is "under ~40 lines for a healthy
#: project"; a *sick* project must still not scroll a terminal off its history,
#: so the lists truncate with an honest count of what was cut and a pointer to
#: the markdown report, which never truncates.
_MAX_TERMINAL_CLAIMS = 14
_MAX_TERMINAL_GAPS = 6
_MAX_REPRODUCE_GATES = 16


# --------------------------------------------------------------------------- #
# the helpers: the only readers of HUMAN
# --------------------------------------------------------------------------- #
def _norm_status(status: ClaimStatus | str) -> ClaimStatus:
    """Coerce whatever `claims.statuses` handed back into a ClaimStatus member.

    A sibling module that returns the raw string (or a value read back out of
    JSON) must not silently fall out of every `status is ClaimStatus.PASS` test
    in here — a claim that quietly matches nothing would vanish from the report
    entirely, which is the one failure mode this file cannot have.
    """
    if isinstance(status, ClaimStatus):
        return status
    return ClaimStatus(status)


def words(status: ClaimStatus | str, *, errored: bool = False) -> StatusWords:
    """`HUMAN`'s row for `status`, the errored row for a crash's Skipped."""
    st = _norm_status(status)
    if errored and st is ClaimStatus.BLOCKED:
        return HUMAN["errored"]
    return HUMAN["status"][st]


def word(status: ClaimStatus | str, *, errored: bool = False, n: int = 1) -> str:
    """The status's word ("pending build"), plural when `n` is not 1 ("gaps")."""
    row = words(status, errored=errored)
    return row.word if n == 1 else row.plural


def status_tag(status: ClaimStatus | str, *, errored: bool = False) -> str:
    """`[FAIL ]` — the bracketed, five-wide tag for one claim status; `[SKIP ]`
    for a claim Skipped by a crash (invariant 2: Failing's tone).

    Public because `status`, `check`, `claim list`, `claim show`, `claim
    physical` and this module must all spell a status the same way. Two
    renderings of the same status is exactly the duplication rule 2 exists to
    kill: the day they drift, a reader has to learn which command is telling
    the truth.
    """
    return f"[{words(status, errored=errored).tag}]"


def severity(composed: Any) -> int:
    """`claims.severity`: one `claims.Composed`'s rank, most urgent first —
    Failing · Skipped, errored · Skipped · Gap · Open · Stale · Pending build ·
    Assumed · Checked (`claims.SEVERITY_ORDER`, with why and what was
    rejected). What it replaced (`_SEVERITY`) ranked STALE above BLOCKED and a
    crash, an order no rule produced, and left `check`'s BLOCKING list in record
    order: a skip above the crash (P2.0 F-2)."""
    return claim_logic.severity(composed)


def in_severity(ledger: Ledger, composed: Mapping[str, Any],
                claims_: Iterable[Claim] | None = None) -> list[Claim]:
    """`claims_` (default: every claim) in severity order; ties critical first,
    then record order — stable, so an unchanged ledger prints the same list."""
    index = {c.id: i for i, c in enumerate(ledger.claims)}
    chosen = list(ledger.claims if claims_ is None else claims_)
    return sorted(chosen, key=lambda c: (severity(composed[c.id]), not c.critical,
                                         index.get(c.id, len(index))))


def count_bits(composed: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The count line's items, one per status word present: Checked first, then
    the severity order — each `{key, status, n, errored, label}`, `label` the
    bit as printed (`2 gaps`, `7 skipped (6 errored)`). One producer for the
    terminal's count line and the page's strip (`site.state`'s `tally`)."""
    n_errored = sum(1 for c in composed.values() if c.errored)
    found: dict[str, list[Any]] = {}
    for c in composed.values():
        row = words(c.status)
        found.setdefault(row.key, [row, c.status, 0])[2] += 1
    checked = HUMAN["status"][ClaimStatus.PASS].rank
    skipped = HUMAN["status"][ClaimStatus.BLOCKED].key
    out: list[dict[str, Any]] = []
    for row, status, n in sorted(found.values(),
                                 key=lambda item: (item[0].rank != checked, item[0].rank)):
        errored = n_errored if row.key == skipped else 0
        label = f"{n} {row.word if n == 1 else row.plural}"
        if errored:
            label += f" ({errored} {HUMAN['lead'][ClaimCause.ERRORED]})"
        out.append({"key": row.key, "status": str(_norm_status(status).value), "n": n,
                    "errored": errored, "label": label})
    return out


def count_line(composed: Mapping[str, Any]) -> str:
    """`7 claims · 3 checked · 1 failing · 2 gaps · 1 pending build` — GLOSSARY
    §9's count line: Checked first, then the severity order, zeros dropped, and
    Skipped written `N skipped (k errored)` (PLAN-v0.14 §1.5: every count splits
    a crash out). What it replaced, `claims 7 — ok 3 | FAIL 1 | phys 1 | assum
    1`, spoke the tags, two of them GLOSSARY Never-says."""
    total = len(composed)
    return " · ".join([f"{total} {_plural(total, 'claim')}",
                       *(bit["label"] for bit in count_bits(composed))])


def _refusal_words(text: str) -> str:
    """An unqualified evaluator's refusal in GLOSSARY §2's words (`HUMAN["refusal"]`)."""
    for old, new in HUMAN["refusal"]:
        if old in text:
            text = text.replace(old, new)
    return text


def _verdict_body(verdict: Verdict) -> str:
    """What explains a verdict, by its outcome — never the first non-empty flag:
    an error's first line (a crash's `detail` is its traceback, P2.0 F-1), a
    skip's reason, a pass's or a fail's detail."""
    outcome = verdict.outcome
    if outcome == "error":
        return (str(verdict.error).splitlines() or [""])[0]
    if outcome == "skipped":
        return verdict.skip_reason or verdict.detail or ""
    return verdict.detail or ""


def reason(composed: Any, ledger: Ledger, claim: Claim, *, full: bool = False,
           cut: bool | None = None,
           stale_reasons: Mapping[str, str] | None = None) -> str:
    """The shortest true sentence about why `claim` reads `composed.status` — ONE
    producer for every channel: `status` and the terminal report, `check`'s
    BLOCKING list, JUnit messages, `state.json`, the JSON views.

    Led by the fact (`HUMAN["lead"]`), then the evaluator, then its words: a
    fail cites `<gate> : <detail>` (+ ` (invalidated: <why>)` when the fail's
    inputs moved, D-08); a crash `errored: <gate> : <exception>`, never the
    traceback; a skip `skipped: <gate> : <reason>`; a refused evaluator
    `unqualified: <gate> : <why>`; an unrun one `unrun: <gates>`; an invalidated
    pass `invalidated: <gate> : <what moved>`. `full=True` is the long form —
    the report, JUnit and JSON — which also says what an unowned assumption
    waits for; `cut` (default: not `full`) cuts the evaluator's words to share a
    terminal row with the claim (`check`'s BLOCKING rows: short, never cut).
    `stale_reasons` is `{gate: why}` from the resolution, when the caller has it.

    What slipped through (S-68, P2.0 F-1/F-8): `status` and `check` each kept a
    copy of this, one fixed and one not, and every copy preferred `detail` — a
    crash's traceback — and fell back to the STATUS for words: a crash rode on
    FAIL's "failing", and moved alone to Skipped it would have read "blocked on
    missing tooling" in three places. The cause is `compose`'s now, and the
    words are here, once.
    """
    cutting = (not full) if cut is None else cut

    def cut_(text: str, limit: int) -> str:
        return _trunc(text, limit if cutting else None)

    cause = composed.cause
    lead = HUMAN["lead"][cause]
    verdict = composed.verdict
    stale_reasons = stale_reasons or {}
    if cause is ClaimCause.FAILED and verdict is not None:
        body = _verdict_body(verdict)
        text = f"{verdict.gate} : {cut_(body, 56)}" if body else f"{verdict.gate} did not pass"
        moved = stale_reasons.get(verdict.gate)
        if moved:
            text += f" ({HUMAN['lead'][ClaimCause.INVALIDATED]}: {cut_(moved, 80)})"
        return text
    if cause is ClaimCause.PHYSICAL_FAIL:
        result = claim.physical_result
        detail = (result.detail if result and result.detail else "no detail recorded")
        return f"{lead}: {cut_(detail, 60)} ({recorded_by(result.who if result else '')})"
    if cause in (ClaimCause.ERRORED, ClaimCause.SKIPPED, ClaimCause.UNQUALIFIED) \
            and verdict is not None:
        if cause is ClaimCause.UNQUALIFIED:
            body = _refusal_words(str(verdict.unqualified or _verdict_body(verdict)))
        else:
            body = _verdict_body(verdict)
        return f"{lead}: {verdict.gate} : {cut_(body, 56)}" if body \
            else f"{lead}: {verdict.gate}"
    if cause is ClaimCause.NO_OWNER:
        return lead + (" — an assumption reads Assumed only once its owner records it; "
                       "nothing can record one yet" if full else "")
    if cause is ClaimCause.OWNER_UNATTRIBUTED:
        return lead.format(owner=_one(claim.owner), id=claim.id)
    if cause is ClaimCause.UNRUN:
        shown = ", ".join(composed.cites[:3])
        more = f", +{len(composed.cites) - 3} more" if len(composed.cites) > 3 else ""
        return f"{lead}: {shown}{more}"
    if cause is ClaimCause.INVALIDATED:
        named = [g for g in composed.cites if stale_reasons.get(g)]
        if named:
            gate = named[0]
            text = f"{lead}: {gate} : {cut_(stale_reasons[gate], 56)}"
            return text + (f" (+{len(composed.cites) - 1} more)"
                           if len(composed.cites) > 1 else "")
        return f"{lead}: {', '.join(composed.cites[:3]) or 'every verdict'}"
    if cause is ClaimCause.NO_ARTICLE:
        untested = not claim.note and claim.acceptance.limit is None \
            and not (claim.acceptance.quantity or "").strip()
        return lead + ("; no test written down" if untested else "")
    if cause is ClaimCause.OWNED:
        return f"{lead.format(owner=_one(claim.owner))}: {cut_(claim.rationale, 52)}"
    if cause is ClaimCause.PHYSICAL_PASS:
        result = claim.physical_result
        return lead.format(recorded=recorded_by(result.who if result else ""))
    return lead


def _one(text: Any) -> str:
    """A value a record supplied, as ONE line: whitespace runs, newlines
    included, collapsed to a space (`_trunc` that never cuts). Every value from
    a record file that reaches a line-oriented channel goes through it. What
    slipped through (review of P2.1): an owner, or a physical result's `who`,
    went into the reason raw, so a record holding a newline wrote its own lines
    — a second `## What is PROVEN` heading with a forged row in the report, a
    `[ok   ] C5 … — checked` row and a `ready:` line under the real `[FAIL ]`
    in `check` and `status`."""
    return _trunc(str(text or ""), None)


def recorded_by(who: Any) -> str:
    """`recorded by sam`, or `recorded, unattributed` with no one named
    (`HUMAN["recorded"]`; GLOSSARY §1, *recorded by*) — the one place a
    physical result's recorder is worded, on one line."""
    name = _one(who)
    said = HUMAN["recorded"]
    return said["named"].format(who=name) if name else said["unattributed"]


def status_view(composed: Any, ledger: Ledger, claim: Claim, *,
                stale_reasons: Mapping[str, str] | None = None) -> dict[str, Any]:
    """`{key, word, cause, reason, errored}` — a claim's status as every JSON
    channel adds it beside the kept enum `status` (P2.1-D12): the token, the
    word, the cause's identifier, the reason in full, and the crash mark."""
    row = words(composed.status, errored=composed.errored)
    return {"key": row.key, "word": row.word, "cause": str(composed.cause.value),
            "reason": reason(composed, ledger, claim, full=True, stale_reasons=stale_reasons),
            "errored": bool(composed.errored)}


def outcome_words() -> dict[str, str]:
    """`state.json`'s `outcome_words`: a verdict row's status (`pass`, `fail`,
    `skipped`, `errored`) -> its word, from `HUMAN` — the page's verdict chips
    own no word either (GLOSSARY §7: every status and outcome word comes from
    one table)."""
    said = HUMAN["outcome"]
    return {"pass": said["pass"], "fail": said["fail"], "skipped": said["skipped"],
            "errored": said["error"]}


def words_table() -> dict[str, dict[str, str]]:
    """`state.json`'s `words`: `{enum value: {key, word, term, plural, hint}}`,
    the page's labels — read from `HUMAN`, so the site never owns a word (D-16),
    and an `errored` entry for the louder Skipped."""
    out = {str(status.value): {"key": row.key, "word": row.word, "term": row.term,
                               "plural": row.plural, "hint": row.hint}
           for status, row in HUMAN["status"].items()}
    row = HUMAN["errored"]
    out["errored"] = {"key": row.key, "word": row.word, "term": row.term,
                      "plural": row.plural, "hint": row.hint}
    return out


def page_phrases() -> dict[str, Any]:
    """`state.json`'s `phrases`: the rest of what the page says in the ledger's
    words — `invalidated` (GLOSSARY §4, a verdict whose read set moved), `need`,
    each gap record's state (`HUMAN["need"]`: `identified` for `open`, GLOSSARY
    §6), and `outcome_hint`, each verdict chip's title (`HUMAN["outcome_hint"]`). What slipped through (review of P2.1): the page
    held both itself — `≈ STALE` on its top bar when a verdict was invalidated
    and no claim read Stale, and its own copy of "identified" — beside a claim
    row that said "No gate covers this claim" for an unowned assumption. The
    site never owns a word (D-16)."""
    hints = HUMAN["outcome_hint"]
    return {"invalidated": HUMAN["lead"][ClaimCause.INVALIDATED],
            "need": {str(status.value): said for status, said in HUMAN["need"].items()},
            "outcome_hint": {"pass": hints["pass"], "fail": hints["fail"],
                             "skipped": hints["skipped"], "errored": hints["error"]}}


def need_word(status: Any) -> str:
    """A gap record's state, in its word: `identified` for `open` (GLOSSARY §6)."""
    try:
        return HUMAN["need"][NeedStatus(status)]
    except ValueError:
        return str(status)


# --------------------------------------------------------------------------- #
# small render helpers
# --------------------------------------------------------------------------- #
def _num(value: Any) -> str:
    """Render a measured number without trailing-zero noise: 220.0 -> `220`."""
    if isinstance(value, bool):          # bool is an int; check it first
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return f"{value:g}"
    return str(value)


def _trunc(text: str, limit: int | None) -> str:
    """One line of `text`, cut to `limit` characters; `None` normalises and never cuts."""
    text = " ".join((text or "").split())
    return text if limit is None or len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _cell(text: str) -> str:
    """Make free text safe inside a markdown table cell.

    A claim statement is written by a human and regularly contains a pipe
    ("|V| <= 4.2 V"), which silently splits the row into extra columns and
    shifts every evidence path one cell left. A misaligned evidence column in a
    readiness report is worse than an ugly one.
    """
    text = " ".join((text or "").split())
    return text.replace("\\", "\\\\").replace("|", "\\|")


def _code(text: str) -> str:
    """Inline code span, with backticks stripped so the span cannot be broken."""
    return f"`{(text or '').replace('`', '')}`"


def _plural(n: int, one: str, many: str = "") -> str:
    return one if n == 1 else (many or one + "s")


def _tier_label(tier: Any) -> str:
    """`tier 1 (build)` — the number a user types plus the name they read."""
    try:
        t = Tier(int(tier))
    except (ValueError, TypeError):
        return f"tier {tier}"
    return f"tier {int(t)} ({t.name.lower()})"


def _claim_text(claim: Claim) -> str:
    return _trunc(claim.statement or claim.id, 120)


# --------------------------------------------------------------------------- #
# ledger / registry queries
# --------------------------------------------------------------------------- #
def _compositions(ledger: Ledger, registry: Any, stale: bool,
                  stale_gates: Collection[str] = ()) -> dict[str, Any]:
    """Every claim's `claims.Composed` — status AND cause — judged against the
    LIVE registry, not the cache.

    `claims.compositions` accepts the registry as a keyword and the difference
    is not cosmetic: without it, coverage is read from `claim.gates` alone, and
    a claim whose gate arrived with a pack installed after the claim was
    written reads Gap ("nobody can check this — go find a solver") instead of
    Open ("the evaluator is right there, run it"). Those two send an agent in
    opposite directions, and only one of them is true. No `owners`: nothing in
    P2.1 attributes an owner (the signing channel is later in Phase 2), so every
    assumption reads Gap here, as everywhere.
    """
    return claim_logic.compositions(ledger, registry=registry, stale=stale,
                                    stale_gates=stale_gates)


def _statuses(ledger: Ledger, registry: Any, stale: bool,
              stale_gates: Collection[str] = ()) -> dict[str, ClaimStatus]:
    """`_compositions`' statuses alone, for a reader that needs no cause."""
    return {cid: _norm_status(c.status)
            for cid, c in _compositions(ledger, registry, stale, stale_gates).items()}


def _coverage(ledger: Ledger, registry: Any) -> dict[str, list[str]]:
    """claim id -> gate ids that claim to cover it. The UNION, from `claims`.

    Deliberately `claims.effective_gates` and not `claims.coverage`: the union of
    the claim's cached `gates` list with live registry coverage, which is the
    same rule `claims.statuses` resolves against. There is one definition of
    coverage and it does not live here.

    It used to. This function took the live coverage alone whenever a registry
    was passed, and *dropped* the cached half — so a claim covered by a gate from
    a pack that is not loaded in this process lost that gate from `cover`,
    `_unproven_for` found nothing missing, and the PROVEN row printed with no
    **PARTIAL** caveat. The caveat disappeared in exactly the case it exists for:
    the pack is gone, nobody can run the gate, and the report looked *more*
    certain for it. A view of coverage that gets rosier as gates go missing is
    the failure this file exists to prevent.

    With no registry the union degenerates to the claim's own cached list. A
    report must still render on a machine where the packs are not installed —
    that is precisely the machine where someone is reading it to decide whether
    to trust the build — it just cannot name gates it has never seen.
    """
    return {cid: list(gids or [])
            for cid, gids in claim_logic.effective_gates(ledger, registry).items()}


def _specs(registry: Any) -> list[Any]:
    if registry is None:
        return []
    return list(registry.specs())


def _unrun_specs(ledger: Ledger, registry: Any) -> list[Any]:
    """Gates that are registered but have no verdict at all: never run here.

    These are the quiet ones. They do not fail, they do not skip, they do not
    appear in `atompipe check` output at all — they are simply absent, and a
    claim they cover can read PASS on the strength of its *other* gates. Whole
    revisions ship in exactly this shape: a validator exists, is never invoked by
    the sweep, and its absence looks identical to success.
    """
    ran = {v.gate for v in ledger.verdicts}
    return [s for s in _specs(registry) if s.id not in ran]


def _needs(ledger: Ledger, registry: Any) -> list[Need]:
    """The capability gaps to report: what is live, plus what is still parked.

    `claims.find_gaps` is the live truth — MEASURABLE claims that no *registered*
    gate covers — and it already folds in each matching recorded Need, so the
    candidates and the user's "not yet" survive. What it cannot return is a Need
    whose gap is no longer live but whose work is not finished: one parked as
    DEFERRED, one half-way through INSTALLING, one PROPOSED and awaiting a yes.
    Those are appended here.

    SATISFIED and ABANDONED Needs are dropped: a closed gap in an open-gaps
    section is how a reader learns to skim the section.
    """
    live: list[Need] = []
    if registry is not None:
        live = list(claim_logic.find_gaps(ledger, registry))
    else:
        # Without a registry the only coverage visible is what each claim
        # remembers, which is a cached opinion — say so on the record rather
        # than reporting "no gaps" from a position of not having looked.
        for claim in ledger.claims:
            # `==`, not `is`: ClaimKind is a StrEnum, and a Claim built by hand
            # in a CLI path or a test keeps a plain `"measurable"` string that
            # `is` never matches. Identity here would drop that claim out of the
            # gap list entirely — a report that shows fewer open gaps because it
            # failed to recognise a claim is the exact direction of error this
            # file may not make. (`claims._kind` coerces for the same reason.)
            if claim.kind == ClaimKind.MEASURABLE and not claim.gates:
                live.append(Need(
                    id=f"gap-{claim.id}", claim_ids=[claim.id],
                    quantity=claim.acceptance.quantity or claim.statement,
                    note="derived without a registry; installed gates were not consulted",
                ))

    seen = {n.id for n in live}
    seen_claims: set[str] = set()
    for n in live:
        seen_claims.update(n.claim_ids or ())

    parked = {NeedStatus.PROPOSED, NeedStatus.DEFERRED, NeedStatus.INSTALLING}
    for need in ledger.needs:
        if need.id in seen or need.status not in parked:
            continue
        if any(cid in seen_claims for cid in (need.claim_ids or ())):
            continue
        live.append(need)
    return live


def _claim_verdicts(ledger: Ledger, claim: Claim) -> list[Verdict]:
    """Every verdict that speaks to this claim, by id **or tag**.

    Not `Ledger.verdicts_for`, which matches on claim id only. `run_gate` copies
    `claims` onto the verdict straight off the spec, so a pack gate bound to the
    tag "manufacturable" emits verdicts carrying that tag and no claim id at all.
    Matching on id alone silently drops exactly the gates a pack contributed —
    the report would show a claim as PENDING while the evidence that settled it
    sat two lines away in the same ledger.
    """
    return claim_logic.covering_verdicts(claim, ledger.verdicts)


def _ok_verdicts(ledger: Ledger, claim: Claim) -> list[Verdict]:
    """Verdicts that are *proof*: ran, did not crash, passed. See rule 1 above."""
    return [v for v in _claim_verdicts(ledger, claim) if v.ok]


def _unproven_for(claim_id: str, cover: dict[str, list[str]],
                  ledger: Ledger) -> list[tuple[str, str]]:
    """Covering gates that produced no pass that counts, each with its reason,
    led by the fact (`HUMAN["lead"]`): `unrun`, `errored: <exception>`,
    `skipped: <reason>`, `unqualified: <refusal>`, `fail: <detail>`.

    The test is `Verdict.ok` and the spine's mark — ran, passed, did not skip,
    did not error, is not refused — never merely "a verdict exists". Keying off
    existence was a live laundering hole: a claim covered by a cheap analytic
    gate and an expensive solver resolved PASS on the analytic one alone, and
    the gate that would have settled it vanished from the document. Under
    GLOSSARY §3's composition (P2.1) a Checked claim has none of these; a list
    beside a Checked status is a contradiction, never a PARTIAL row (D18).

    What slipped through (P2.0 F-10): a skipped-and-errored verdict's reason
    was its skip reason — a crash in a missing tool's words, on the page.
    """
    by_gate = {v.gate: v for v in ledger.verdicts}
    lead = HUMAN["lead"]
    out: list[tuple[str, str]] = []
    for gid in cover.get(claim_id, []):
        verdict = by_gate.get(gid)
        if verdict is None:
            out.append((gid, lead[ClaimCause.UNRUN]))
        elif getattr(verdict, "unqualified", ""):
            out.append((gid, f"{lead[ClaimCause.UNQUALIFIED]}: "
                             f"{_refusal_words(verdict.unqualified)}"))
        elif not verdict.ok:
            body = _verdict_body(verdict)
            head = {"error": lead[ClaimCause.ERRORED], "skipped": lead[ClaimCause.SKIPPED]
                    }.get(verdict.outcome, HUMAN["outcome"]["fail"])
            out.append((gid, f"{head}: {body}" if body else head))
    return out


def _disagreement(ledger: Ledger, claim: Claim, composed: Any,
                  cover: dict[str, list[str]]) -> str:
    """Why a claim the resolver calls `pass` is contradicted by its evidence —
    `<gate> <why>; …` — or `""`. Under GLOSSARY §3's composition this never
    happens; when it does, the resolver and the verdicts disagree, and the
    report says so loudly, outside the checked section (P2.1-D18, review): a
    contradiction kept under PROVEN, even marked, is PARTIAL under a new name."""
    if composed.status is not ClaimStatus.PASS:
        return ""
    unproven = _unproven_for(claim.id, cover, ledger)
    if unproven:
        return "; ".join(f"{gid} {why}" for gid, why in unproven)
    if not _ok_verdicts(ledger, claim):
        return "no verdict recorded"
    return ""


def _ids(claims: Iterable[Claim], limit: int = 4) -> str:
    ids = [c.id for c in claims]
    if len(ids) <= limit:
        return ", ".join(ids)
    return ", ".join(ids[:limit]) + f", +{len(ids) - limit} more"


# --------------------------------------------------------------------------- #
# the verdict sentence
# --------------------------------------------------------------------------- #
def _groups(ledger: Ledger, composed: Mapping[str, Any], chosen: Iterable[Claim]) -> str:
    """`1 failing (C1); 2 gaps (C6, C7); 1 pending build (C5)` — `chosen` grouped
    by status word in severity order, Skipped written `N skipped (k errored)`
    with the errored claims first. One producer for the readiness sentence and
    `check`'s line, so the two cannot disagree about what is unresolved."""
    buckets: dict[str, list[Claim]] = {}
    for claim in in_severity(ledger, composed, chosen):
        buckets.setdefault(words(composed[claim.id].status).key, []).append(claim)
    parts: list[str] = []
    for members in buckets.values():
        status = composed[members[0].id].status
        n = len(members)
        text = f"{n} {word(status, n=n)}"
        errored = sum(1 for c in members if composed[c.id].errored)
        if errored:
            text += f" ({errored} {HUMAN['lead'][ClaimCause.ERRORED]})"
        parts.append(f"{text} ({_ids(members)})")
    return "; ".join(parts)


def readiness(ledger: Ledger, composed: Mapping[str, Any]) -> dict[str, Any]:
    """What *ready* turns on, as lists of claims (GLOSSARY §4, W3): `required`;
    `unresolved` — required and not Checked, Pending build and Assumed included;
    `unbound` — the unresolved ones with a physical pass recorded that no article
    binds to the current inputs (each reads Pending build until article binding,
    so an agent's typed pass never makes a project ready); `ready` — at least
    one required claim, and none unresolved. `claims.summarise`'s
    `all_required_checked` is this predicate; `ready` in a JSON summary is not
    (it keeps "nothing stops check"). What slipped through (review of P2.1): an
    unbound pass read Checked everywhere but here, so *checked* meant two
    things — `5 checked` on the count line beside "is NOT ready: every required
    claim is checked, but…"."""
    required = [c for c in ledger.claims if c.critical]
    unresolved = [c for c in required if composed[c.id].status
                  not in (ClaimStatus.PASS, ClaimStatus.VERIFIED)]
    unbound = [c for c in required if composed[c.id].cause is ClaimCause.PHYSICAL_PASS]
    return {"required": required, "unresolved": unresolved, "unbound": unbound,
            "ready": bool(required) and not unresolved}


def not_ready_line(ledger: Ledger, composed: Mapping[str, Any]) -> str:
    """`check`'s line when nothing blocks it: `ready: …` only when *ready* holds
    (GLOSSARY §4), otherwise what stands between the project and it. What it
    replaced said `ready: no critical claim is blocking` while a claim waited
    for an article — GLOSSARY's *clean bill of health*."""
    found = readiness(ledger, composed)
    checked = word(ClaimStatus.PASS)
    if found["ready"]:
        return f"ready: every required claim is {checked}"
    if not found["required"]:
        return (f"nothing stops this check run — no claim is required, so nothing is "
                f"ready (listed in `atompipe report`)")
    n = len(found["unresolved"])
    return (f"nothing stops this check run — {n} required "
            f"{_plural(n, 'claim')} {_plural(n, 'is', 'are')} unresolved: "
            f"{_groups(ledger, composed, found['unresolved'])} "
            f"(listed in `atompipe report`)")


def _verdict_sentence(ledger: Ledger, composed: Mapping[str, Any], registry: Any,
                      *, stale: bool, markdown: bool) -> str:
    """The readiness sentence, then what stays true whatever it says. Bad news
    first, always.

    This function is the whole point of the report, so it is worth being explicit
    about the ordering rule it encodes: an unresolved required claim outranks any
    amount of good news. The moment a verdict is allowed to open with "8 of 9
    claims pass" while one required claim has no evaluator at all, the reader
    has been told the project is nearly done, and the one sentence that mattered
    is now a footnote.

    *Ready* only when every required claim reads Checked on the current inputs
    (GLOSSARY §4; `readiness`); otherwise `NOT ready`, with every unresolved
    required claim listed by its word — Pending build and Assumed included,
    because both are unresolved (GLOSSARY §3). What slipped through: the
    sentence said "clears every critical gate that is installed" while a claim
    waited for an article, and grouped only the blocking statuses under
    "unsettled", so a reader counted what was left wrong.

    The hardware sentence follows in EVERY branch, ready included — `Pending
    build: N claims need an article`, naming apart those with a pass recorded
    that no article binds — because that clause is what separates a design that
    clears its evaluators from a working thing (W13, GLOSSARY §9). What slipped
    through the P2.1 design (review): it folded the clause into the not-ready
    groups, so a ready project with a not-required physical claim said nothing
    about hardware.

    Every branch that is not ready SAYS so, the never-evaluated one included,
    and the claims not required are grouped by word as the required ones are,
    a crash counted apart. What slipped through (review of P2.1): a project
    with one typed physical pass and no verdict read "has never been evaluated
    … Nothing below is checked … 1 claim is checked on an article" — no NOT
    ready, and a contradiction in one sentence; and a crash on a claim not
    required was "unresolved", its errored said only on the count line.
    """
    rev = ledger.meta.revision or "this revision"
    bold = (lambda s: f"**{s}**") if markdown else (lambda s: s)
    total = len(ledger.claims)
    checked = word(ClaimStatus.PASS)

    if total == 0:
        return bold(f"{rev} has no claims recorded, so nothing has been evaluated.") + \
            " A project with no claims is not ready — start with one: write" \
            " `claims/C1.json`, a statement and an acceptance."

    found = readiness(ledger, composed)
    status_of = {cid: c.status for cid, c in composed.items()}
    n_checked = sum(1 for s in status_of.values() if s is ClaimStatus.PASS)
    tally = (f"{n_checked} of {total} {_plural(total, 'claim')} "
             f"{_plural(n_checked, 'is', 'are')} {checked} against the current inputs.")

    parts: list[str] = []
    # NB: "never evaluated" keys off whether any VERDICT exists, not off run
    # metadata. The sweep record this used to consult was bookkeeping a caller
    # could legitimately not have written; verdicts are the evidence.
    if not ledger.verdicts:
        verdict = (f"{rev} has never been evaluated" if found["ready"]
                   else f"{rev} is NOT ready and has never been evaluated")
        parts.append(bold(f"{verdict}: no verdict of any kind is"
                          f" recorded against its {total} {_plural(total, 'claim')}."))
        parts.append(f"Nothing below is {checked}, because no evaluator has run —"
                     f" `atompipe check --tier 0` is the first step.")
    elif not found["required"]:
        parts.append(bold(f"{rev} is NOT ready: none of its {total}"
                          f" {_plural(total, 'claim')} is required."))
        parts.append(tally)
    elif found["unresolved"]:
        n, req = len(found["unresolved"]), len(found["required"])
        parts.append(bold(
            f"{rev} is NOT ready: {n} of {req} required {_plural(req, 'claim')}"
            f" {_plural(n, 'is', 'are')} unresolved —"
            f" {_groups(ledger, composed, found['unresolved'])}."))
        parts.append(tally)
    else:
        parts.append(bold(f"{rev} is ready: every required claim is {checked} against"
                          f" the current inputs."))

    other = [c for c in ledger.claims if not c.critical
             and status_of.get(c.id) not in (ClaimStatus.PASS, ClaimStatus.VERIFIED)]
    if other:
        parts.append(f"{len(other)} {_plural(len(other), 'claim')} not required"
                     f" {_plural(len(other), 'is', 'are')} unresolved —"
                     f" {_groups(ledger, composed, other)}.")

    unrun = _unrun_specs(ledger, registry)
    if unrun and ledger.verdicts:
        parts.append(f"{len(unrun)} registered {_plural(len(unrun), 'gate')}"
                     f" {_plural(len(unrun), 'is', 'are')}"
                     f" {HUMAN['lead'][ClaimCause.UNRUN]}.")

    pending = [c for c in ledger.claims if status_of.get(c.id) is ClaimStatus.UNVERIFIED]
    if pending:
        recorded = [c for c in pending if composed[c.id].cause is ClaimCause.PHYSICAL_PASS]
        text = (f"{words(ClaimStatus.UNVERIFIED).term}: {len(pending)}"
                f" {_plural(len(pending), 'claim')}"
                f" {_plural(len(pending), 'needs', 'need')} an article ({_ids(pending)})")
        if recorded:
            text += (f"; {_ids(recorded)} {_plural(len(recorded), 'has', 'have')} a pass"
                     f" recorded that no article binds to the current inputs")
        parts.append(text + ".")
    return " ".join(parts)


# --------------------------------------------------------------------------- #
# markdown sections
# --------------------------------------------------------------------------- #
def _section_proven(ledger: Ledger, composed: Mapping[str, Any],
                    cover: dict[str, list[str]], *, stale: bool) -> list[str]:
    """The checked table. Every row cites a gate that ran and the file it wrote.

    The heading starts with `SECTION_PROVEN`, never a literal: invariant 4's tests
    find the section by that constant, and fail when it is missing. Only a claim
    that reads Checked (`pass`) on its evidence is here: one whose evaluators
    contradict the status is listed loudly in the failing section instead
    (`_disagreement`, D18).
    """
    out = [f"{SECTION_PROVEN} {_proven_qualifier()}", ""]
    rows: list[str] = []
    checked = word(ClaimStatus.PASS)

    for claim in ledger.claims:
        if composed[claim.id].status is not ClaimStatus.PASS:
            continue
        if _disagreement(ledger, claim, composed[claim.id], cover):
            continue
        verdicts = _ok_verdicts(ledger, claim)
        gates = ", ".join(_code(v.gate) for v in verdicts)
        measured_bits = []
        evidence: list[str] = []
        for v in verdicts:
            if v.measured is not None:
                measured_bits.append(f"{_num(v.measured)} {v.units}".strip())
            elif v.detail:
                # A boolean gate ("watertight: yes") has no number, and an
                # empty Measured cell reads as missing evidence rather than as
                # a pass with no scalar. Show the gate's one-line detail.
                measured_bits.append(_trunc(v.detail, 56))
            evidence.extend(v.evidence or [])
        measured = "; ".join(measured_bits) or "(no value reported)"

        if evidence:
            shown = evidence[:3]
            ev = ", ".join(_code(p) for p in shown)
            if len(evidence) > 3:
                ev += f" (+{len(evidence) - 3} more)"
        else:
            ev = "*none written*"

        rows.append("| " + " | ".join([
            f"**{_cell(claim.id)}** {_cell(_claim_text(claim))}",
            _cell(claim.acceptance.render()) or "—",
            _cell(measured),
            gates,
            ev,
        ]) + " |")

    if rows:
        out.append("| Claim | Acceptance | Measured | Gate | Evidence |")
        out.append("|---|---|---|---|---|")
        out.extend(rows)
        out.append("")
        out.append(f"Every row above is {checked}: each of its evaluators ran and passed "
                   f"against the inputs, code and control it has now. A skipped, errored, "
                   f"unqualified, invalidated or unrun evaluator puts its claim in another "
                   f"section with the reason, never here — and {checked} does not mean "
                   f"true.")
    elif not ledger.claims:
        out.append("Nothing — there are no claims.")
    elif stale:
        out.append(f"**Nothing.** Every verdict is marked invalidated, so no claim reads "
                   f"{words(ClaimStatus.PASS).term} now. Passing yesterday is not "
                   f"{checked} today. Re-run `atompipe check`.")
    elif not ledger.verdicts:
        out.append("**Nothing.** No evaluator has ever run in this project.")
    else:
        out.append(f"**Nothing.** No claim reads {words(ClaimStatus.PASS).term} now. "
                   f"The sections below say why for each one.")
    out.append("")
    return out


def _section_pending_build(ledger: Ledger, composed: Mapping[str, Any]) -> list[str]:
    """Physical claims that wait on an article: how to settle each, and those a
    physical pass was recorded for that no article binds to the current inputs
    (Pending build too, until article binding — the pass is listed, never
    counted as Checked)."""
    out = [HUMAN["heading"]["pending_build"], ""]
    status_of = {cid: c.status for cid, c in composed.items()}
    waiting = [c for c in ledger.claims if status_of.get(c.id) is ClaimStatus.UNVERIFIED]
    verified = [c for c in waiting if composed[c.id].cause is ClaimCause.PHYSICAL_PASS]
    pending = [c for c in waiting if composed[c.id].cause is not ClaimCause.PHYSICAL_PASS]
    # `==` rather than `is` — see the note in `_needs`. A PHYSICAL claim whose
    # kind is still a plain string would otherwise vanish from the
    # "physical claims with no written test" nag, which is the one line that
    # says a claim can never be settled by anything in this pipeline.
    physical = [c for c in ledger.claims if c.kind == ClaimKind.PHYSICAL]

    if pending:
        out.append("These need an article. No evaluator in any pack can settle them, and "
                   "no number of passing evaluators above changes that.")
        out.append("")
        for claim in pending:
            crit = "" if claim.critical else " *(not required)*"
            out.append(f"- **{claim.id}** {_claim_text(claim)}{crit}")
            out.append(f"  - **Test that would settle it:** {_physical_test(claim)}")
            if claim.rationale:
                out.append(f"  - **Why it matters:** {_trunc(claim.rationale, 200)}")
            out.append(f"  - **Record the result:** "
                       f"`atompipe claim physical {claim.id} --pass|--fail "
                       f"--detail \"...\" --when <ISO date>`")
        out.append("")

    if verified:
        out.append("These have a pass recorded that no article binds to the current "
                   "inputs, so they are not checked: nothing shows the article was built "
                   "from what the model says now.")
        out.append("")
        for claim in verified:
            res = claim.physical_result
            when = _one(res.when if res and res.when else "date not recorded")
            detail = f" — {_trunc(res.detail, 160)}" if res and res.detail else ""
            ev = ""
            if res and res.evidence:
                ev = " [" + ", ".join(_code(p) for p in res.evidence[:3]) + "]"
            crit = "" if claim.critical else " *(not required)*"
            out.append(f"- **{claim.id}** {_claim_text(claim)}{crit} — a pass "
                       f"{recorded_by(res.who if res else '')}, {when}{detail}{ev}; not "
                       f"bound to an article")
        out.append("")

    if not physical:
        # An honest absence. Almost every physical project has at least one claim
        # a tool cannot settle; a ledger with none usually means nobody asked the
        # question, not that the question has no answer.
        out.append("No claim in this project is marked `physical`. Either nothing "
                   "here depends on a property only an article can show — or "
                   "nobody has asked which properties those are. The second is far "
                   "more common.")
        out.append("")
    elif not waiting:
        out.append("No physical claim waits on an article: each reads under its own "
                   "status in another section.")
        out.append("")
    return out


def _physical_test(claim: Claim) -> str:
    """What experiment settles this claim — the claim's own note, or a derived one.

    A physical claim with no written procedure does not get settled; it gets
    remembered as "we should check that" until the build is finished and nobody
    can be bothered. When the note is empty the acceptance at least names the
    quantity and threshold, which is enough for somebody to design the test. When
    even that is missing, say so: the claim is currently unfalsifiable.
    """
    if claim.note:
        return _trunc(claim.note, 240)
    rendered = claim.acceptance.render()
    if rendered:
        return (f"measure {rendered} on the article and compare against the "
                f"acceptance")
    return ("**no test has been written down.** As stated, this claim cannot be "
            "settled by any observation — give it an acceptance or a procedure in "
            "its note, or it will stay on this list forever")


def _section_gaps(ledger: Ledger, composed: Mapping[str, Any], registry: Any, *,
                  stale_reasons: Mapping[str, str] | None = None) -> list[str]:
    """Every claim that reads Gap, by the fact that made it one (P2.1-D19), each
    wanting a different person to act: no evaluator — its gap records and tool
    options; an unqualified evaluator — which one, and why; an assumption with
    no attributed owner or no reason.

    What slipped through the design (P2.1): this section read only
    `find_gaps`, the automated claims no evaluator covers, so a Gap from an
    unqualified evaluator or an unowned assumption appeared in no section at
    all, and the C6 it did catch was told to run `gap --propose`.
    """
    out = [HUMAN["heading"]["gaps"], ""]
    needs = _needs(ledger, registry)
    gap = ClaimStatus.UNCLAIMED
    gaps = [c for c in ledger.claims if composed[c.id].status is gap]
    by_cause = {cause: [c for c in gaps if composed[c.id].cause is cause]
                for cause in ClaimCause}
    unqualified = by_cause[ClaimCause.UNQUALIFIED]
    unowned = (by_cause[ClaimCause.NO_OWNER] + by_cause[ClaimCause.OWNER_UNATTRIBUTED]
               + by_cause[ClaimCause.NO_REASON])
    no_evaluator = by_cause[ClaimCause.NO_EVALUATOR]

    if not gaps and not needs:
        if not ledger.claims:
            out.append("No claims, so nothing to gap. This is not good news.")
        else:
            out.append(f"None. Every automated claim has at least one evaluator, and no "
                       f"claim reads {words(gap).term}.")
        out.append("")
        return out

    if needs or no_evaluator:
        out.append(f"A claim with no evaluator is a {word(gap)}, not a defect. It is closed "
                   f"by installing or writing a tool — with the cost said out loud before "
                   f"anyone agrees to it.")
        out.append("")
    recorded = {cid for need in needs for cid in (need.claim_ids or ())}
    for need in needs:
        quantity = need.quantity or "(quantity not named)"
        out.append(f"### {need.id} — {quantity}  *({need_word(need.status)})*")
        for cid in need.claim_ids or []:
            claim = ledger.claim(cid)
            text = _claim_text(claim) if claim else "*(claim not in ledger)*"
            found = composed.get(cid)
            tag = word(found.status, errored=found.errored) if found is not None else "?"
            out.append(f"- **Claim:** {cid} — {text}  `{tag}`")
            if claim and claim.gates and found is not None and found.status in (
                    ClaimStatus.PENDING, ClaimStatus.BLOCKED):
                # Not a contradiction, and worth one line so nobody reads it as
                # one: the claim remembers a gate id from a pack that is not
                # installed *here*. "Unrun" and "no evaluator covers it" are both
                # true, and they want different fixes - install the pack, or run
                # the check.
                out.append(f"  - The claim names {', '.join(_code(g) for g in claim.gates)}"
                           f", which is not registered in this environment —"
                           f" install the pack that provides it, or write one.")
        if not need.claim_ids:
            out.append("- **Claim:** *(none linked — an unattached gap will never "
                       "be prioritised)*")
        if need.claim_class:
            out.append(f"- **Class:** {need.claim_class}")
        if need.note:
            out.append(f"- **Note:** {_trunc(need.note, 220)}")

        if need.candidates:
            out.append("- **Tool options:**")
            for cand in need.candidates:
                bits = [f"**{cand.name}**"]
                if cand.kind:
                    bits.append(f"({cand.kind})")
                head = " ".join(bits)
                cost = cand.cost or ("**cost not stated** — an unstated cost is how "
                                     "a 20-minute install becomes a surprise")
                line = f"  - {head} — {cand.why or 'no rationale recorded'}"
                out.append(line)
                out.append(f"    - Cost: {cost}")
                if cand.licence:
                    out.append(f"    - Licence: {cand.licence}")
                if cand.install:
                    out.append(f"    - Install: {_code(cand.install)}")
            if need.chosen:
                out.append(f"  - **Chosen:** {need.chosen}")
        else:
            out.append("- **Tool options:** none proposed yet — "
                       "`atompipe gap --propose`")
        out.append("")
    loose = [c for c in no_evaluator if c.id not in recorded]
    if loose:
        out.append(f"{len(loose)} {_plural(len(loose), 'claim')} "
                   f"{_plural(len(loose), 'reads', 'read')} {words(gap).term} with no "
                   f"evaluator and no gap record names "
                   f"{_plural(len(loose), 'it', 'them')}: "
                   f"{_ids(loose, limit=12)}. Run `atompipe gap --propose`.")
        out.append("")

    if unqualified:
        out.append("### Unqualified evaluators")
        out.append("")
        out.append("An evaluator that has not shown it can fail settles nothing: its claim "
                   f"is a {word(gap)} until it qualifies, whatever passed beside it.")
        out.append("")
        for claim in unqualified:
            why = reason(composed[claim.id], ledger, claim, full=True,
                         stale_reasons=stale_reasons)
            out.append(f"- **{claim.id}** {_claim_text(claim)} — {why}")
        out.append("")

    if unowned:
        out.append("### Assumptions nobody owns")
        out.append("")
        out.append(f"An assumption reads {words(ClaimStatus.ASSERTED).term} only with a "
                   f"reason and an owner who recorded it; until then it is a {word(gap)}.")
        out.append("")
        for claim in unowned:
            why = reason(composed[claim.id], ledger, claim, full=True,
                         stale_reasons=stale_reasons)
            out.append(f"- **{claim.id}** {_claim_text(claim)} — {why}")
        out.append("")
    return out


#: What the standing constraints say when the parameters' rationales cannot be
#: judged, completed by the reason (`_rationale_unknown`). *Rejected:* saying
#: nothing — the section's all-clear sentence then reads "every parameter carries
#: a rationale" about a model nobody read; and listing the records' own lack of a
#: rationale, which is what it did (review, checkpoint 1.3: a record holding only
#: `"source"` was printed as undefended at value `None`).
RATIONALE_UNKNOWN = "Parameter rationales are not known"


def _rationale_unknown(params: Sequence[Any] | None, model_error: str) -> str:
    """Why no parameter can be called defended or undefended here, or `""`.

    `params` is the caller's `modelio.param_view` (None: the caller gave none);
    a view with a `model_error` is one the model did not answer for, and its
    error's first line is the reason ("no model was loaded" for a project that
    names none) — said as it is, so a project with no model is not told that
    its model does not load."""
    if model_error:
        return "the model does not load"
    errors = [str(getattr(view, "model_error", "") or "") for view in params or ()]
    first = next((error for error in errors if error.strip()), "")
    if first:
        return _trunc(first.strip().splitlines()[0], 160)
    if params is None:
        return "no parameter view was given to this report"
    return ""


def _section_assumed(ledger: Ledger, composed: Mapping[str, Any],
                     params: Sequence[Any] | None = None,
                     model_error: str = "") -> list[str]:
    """Assumed claims, undefended numbers, and evidence nobody read.

    None of these is a failing gate, and that is exactly why they get their own
    section: they are the things that sink a build without ever turning a check
    red. An assumption nobody owns (that one is a Gap, above), a constant nobody
    can defend, and a datasheet nobody opened all behave identically at the
    moment they bite.

    The numbers are `params`, `modelio.param_view`'s views — the model's value,
    its rationale or the record's — judged by `modelio.undefended_params`, the
    rule `doctor` and `status` print. What slipped through (review, checkpoint
    1.3): this read `ledger.params`, the records, which from 1.3 hold only what
    the model cannot (a source, a grounding), so a field nobody explained never
    reached this section, and a record holding only `"source"` was listed as
    undefended at value `None` while `doctor` said every parameter carried a
    rationale.
    """
    out = [HUMAN["heading"]["assumed"], ""]
    assumed = [c for c in ledger.claims if composed[c.id].status is ClaimStatus.ASSERTED]
    unknown = _rationale_unknown(params, model_error)
    names = set(modelio.undefended_params(params or ()))
    undefended = [view for view in params or () if view.name in names]
    unread = unextracted(ledger)
    term = words(ClaimStatus.ASSERTED).term

    if not (assumed or undefended or unread):
        if unknown:
            out.append(f"None recorded: no claim reads {term}, and every ingested "
                       f"artifact has been read. {RATIONALE_UNKNOWN}: {unknown}.")
        else:
            out.append(f"None recorded: no claim reads {term}, every parameter carries "
                       f"a rationale, and every ingested artifact has been read.")
        out.append("")
        return out

    out.append(f"Carried on the record and not {word(ClaimStatus.PASS)}: all of it is "
               f"visible, which is the whole trade.")
    out.append("")

    if assumed:
        out.append(f"### {term} claims")
        out.append("")
        for claim in assumed:
            src = f" *(source: {_trunc(claim.source, 80)})*" if claim.source else ""
            why = reason(composed[claim.id], ledger, claim, full=True)
            out.append(f"- **{claim.id}** {_claim_text(claim)}{src} — {why}")
        out.append("")

    if unknown:
        out.append(f"{RATIONALE_UNKNOWN}: {unknown}, so no number here is called "
                   f"defended or undefended.")
        out.append("")

    if undefended:
        out.append("### Parameters with no recorded rationale")
        out.append("")
        out.append("A number nobody can defend is a number the next agent will "
                   "change — and then re-litigate, and then change back. "
                   "`atompipe why <param>` is empty for each of these.")
        out.append("")
        out.append("| Param | Value | Source | Protected by |")
        out.append("|---|---|---|---|")
        for param in undefended:
            value = f"{_num(param.value)} {param.units}".strip()
            gates = ", ".join(_code(g) for g in param.gates) if param.gates else "—"
            out.append("| " + " | ".join([
                _code(param.name), _cell(value),
                _cell(param.source) or "*unsourced*", gates,
            ]) + " |")
        out.append("")

    if unread:
        out.append("### Ingested evidence nobody read")
        out.append("")
        out.append("An artifact with no extraction is decoration: it is in the "
                   "repo, it looks like evidence, and it grounds nothing. "
                   "`atompipe extract <id> --what ... --grounds ...` fixes it.")
        out.append("")
        out.append("| Artifact | Kind | Path | Added |")
        out.append("|---|---|---|---|")
        for art in unread:
            where = art.path or art.url or "—"
            out.append("| " + " | ".join([
                _code(art.id), _cell(str(art.kind)),
                _code(where) if where != "—" else "—",
                _cell(art.added) or "—",
            ]) + " |")
        out.append("")
    return out


def _stale_suffix(claim: Claim, cover: dict[str, list[str]],
                  stale_gates: Collection[str]) -> str:
    """`: `g.one` is invalidated` — which covering gates made a claim Stale, when
    the caller said (``stale_gates``); ``""`` under the all-gates alias. The
    reason each gate moved is the resolver's and lives in `atompipe status`; the
    report names the gate so the reader knows which result to re-check."""
    stale = [gid for gid in (cover.get(claim.id) or list(claim.gates or []))
             if gid in set(stale_gates)]
    if not stale:
        return ""
    return (": " + ", ".join(_code(g) for g in stale)
            + f" {_plural(len(stale), 'is', 'are')} "
            + HUMAN["lead"][ClaimCause.INVALIDATED])


#: The statuses listed one by one under the failing section: GLOSSARY §9's
#: "Failing, stale, skipped or open". A Gap is a missing evaluator, not a defect,
#: and has its own section; Pending build and Assumed theirs.
_FAILING_SECTION: tuple[ClaimStatus, ...] = (
    ClaimStatus.FAIL, ClaimStatus.REFUTED, ClaimStatus.STALE,
    ClaimStatus.BLOCKED, ClaimStatus.PENDING,
)

def _bullet_rank(verdict: Verdict) -> int:
    """A claim's evaluator bullets in `claims.OUTCOME_ORDER` (P2.1-D16): what
    failed, what crashed, what skipped, what is refused, then what passed. What
    slipped through (P2.0 F-3): the bullets were in gate-id order, a skip above
    the crash; then (review of P2.1) the order was a third copy of the table
    `explaining_verdict` and `why` each kept."""
    return claim_logic.outcome_rank(verdict)


def _section_failing(ledger: Ledger, composed: Mapping[str, Any],
                     cover: dict[str, list[str]], registry: Any, *,
                     stale_gates: Collection[str] = (),
                     stale_reasons: Mapping[str, str] | None = None) -> list[str]:
    """Everything that is red, with the verdict line that made it red — and any
    claim the resolver calls Checked that its evidence contradicts, first and
    loudly (D18)."""
    out = [HUMAN["heading"]["failing"], ""]
    contradicted = {c.id: why for c in ledger.claims
                    if (why := _disagreement(ledger, c, composed[c.id], cover))}
    bad = in_severity(ledger, composed,
                      [c for c in ledger.claims
                       if composed[c.id].status in _FAILING_SECTION])
    bad = [c for c in ledger.claims if c.id in contradicted] + bad

    cited: set[str] = set()

    if bad:
        for claim in bad:
            found = composed[claim.id]
            flag = "critical" if claim.critical else "not required"
            if claim.id in contradicted:
                out.append(f"### {status_tag(found.status)} {claim.id} — "
                           f"{_claim_text(claim)}  *(status and evidence disagree, {flag})*")
                out.append(f"- **status and evidence disagree — "
                           f"{contradicted[claim.id]}**")
            else:
                label = word(found.status) + (", errored" if found.errored else "")
                out.append(f"### {status_tag(found.status, errored=found.errored)} "
                           f"{claim.id} — {_claim_text(claim)}  *({label}, {flag})*")
                out.append(f"- **Why:** {reason(found, ledger, claim, full=True, stale_reasons=stale_reasons)}")
            rendered = claim.acceptance.render()
            if rendered:
                out.append(f"- **Acceptance:** {rendered}")
            verdicts = sorted(_claim_verdicts(ledger, claim), key=_bullet_rank)
            for v in verdicts:
                cited.add(v.gate)
                out.append(f"- `{v.render()}`")
                if v.evidence:
                    out.append("  - evidence: "
                               + ", ".join(_code(p) for p in v.evidence[:3]))
            ran = {v.gate for v in verdicts}
            unrun = [g for g in (cover.get(claim.id) or list(claim.gates or []))
                     if g not in ran]
            if unrun:
                out.append(f"- {HUMAN['lead'][ClaimCause.UNRUN]}: "
                           + ", ".join(_code(g) for g in unrun))
            if not verdicts and not unrun:
                out.append("- No verdict and no covering gate recorded.")
            if found.status is ClaimStatus.STALE:
                out.append("- Passed, but not against the current inputs"
                           + _stale_suffix(claim, cover, stale_gates)
                           + f". Nothing here is {word(ClaimStatus.PASS)} *now* — "
                             f"`atompipe check` re-runs what moved.")
            if claim.physical_result and claim.physical_result.passed is not True:
                res = claim.physical_result
                out.append(f"- Physical result: failed {_one(res.when)}, "
                           f"{recorded_by(res.who)} — {_trunc(res.detail, 200)}")
            out.append("")
    else:
        out.append("No claim is failing, stale, skipped or open.")
        out.append("")

    # Gate-level problems that no claim surfaced. A gate that skipped while
    # covering nothing, or one that crashed, still showed nothing — and a crash
    # is not a failure, it is an absence of information wearing a failure's
    # clothes.
    loose = [v for v in ledger.verdicts if not v.ok and v.gate not in cited]
    unrun_specs = _unrun_specs(ledger, registry)
    if loose or unrun_specs:
        out.append("### Gates with no verdict that counts")
        out.append("")
        for v in sorted(loose, key=_bullet_rank):
            why = ("unqualified" if getattr(v, "unqualified", "")
                   else HUMAN["outcome"].get(v.outcome, v.outcome))
            out.append(f"- `{v.render()}`  *({why}; claims: "
                       f"{', '.join(v.claims) or 'none linked'})*")
        for spec in unrun_specs:
            covers = ", ".join(spec.claims) if spec.claims else "nothing recorded"
            out.append(f"- `{spec.id}` — registered ({_tier_label(spec.tier)}"
                       f"{', pack ' + spec.pack if spec.pack else ''}) but "
                       f"{HUMAN['lead'][ClaimCause.UNRUN]}. Would cover: {covers}.")
        out.append("")
        out.append("A gate with no verdict that counts is not a gate that passed: each "
                   "claim it covers reads under its own status in this report, never "
                   f"{words(ClaimStatus.PASS).term}.")
        out.append("")
    return out


def _code_files(registry: Any, root: str) -> dict[str, list[str]]:
    """gate id -> the files its code is, spelled as the verdict cache spells
    them (``gates/structural.py``, ``<pack:cad-solid>/gates/solid.py``) — the
    files whose bytes key its verdict, so an edit to any of them re-runs that
    gate and no other.

    Only with a ``root`` to spell them against, and a registry that hands out
    its gate functions: without the root a project's gate would be spelled by
    this machine's absolute path, and a tracked report that changes per
    checkout is the churn S-89 names. A registry that is only a list of specs
    (a test's, or a machine without the packs) lists no files."""
    pairs = getattr(registry, "pairs", None) if registry is not None else None
    if not root or not callable(pairs):
        return {}
    anchors = verdict_logic.anchors_for(root, registry, out_dir=store.out_dir(root))
    files: dict[str, list[str]] = {}
    for spec, fn in pairs():
        code = verdict_logic.code_digest(spec, fn, anchors=anchors)
        files[spec.id] = list(code.files)
    return files


def _section_reproduce(ledger: Ledger, registry: Any, *, root: str = "") -> list[str]:
    """The exact commands, and the code behind each row. A readiness report
    nobody can re-derive is a press release.

    No time, no rho, no hash of the run: those change on every re-run of an
    unchanged design, and this file is tracked (S-89). What it names instead is
    what a re-run would re-derive — the command per gate, and the files that
    gate's verdict is keyed on.
    """
    out = [HUMAN["heading"]["reproduce"], ""]
    specs = _specs(registry)
    max_tier = max((int(s.tier) for s in specs), default=0)

    out.append("This file is generated. Re-derive every row above with:")
    out.append("")
    out.append("```sh")
    out.append("atompipe check --tier 0"
               "          # the inner loop: analytic gates only, seconds")
    if max_tier > 0:
        out.append(f"atompipe check --tier {max_tier}"
                   f"          # everything registered"
                   f"{' — solvers included' if max_tier >= 2 else ''}")
    out.append("atompipe report --write"
               "          # regenerates docs/readiness.md")
    out.append("```")
    out.append("")
    out.append("`check` re-runs only the gates whose inputs, code or control moved; "
               "every other verdict is served from `.atompipe/verdicts/` as it was "
               "recorded. `atompipe check --force` re-runs all of them.")
    out.append("")

    ran = {v.gate for v in ledger.verdicts}
    code = _code_files(registry, root)
    per_gate: list[tuple[str, str]] = []
    for v in ledger.verdicts:
        per_gate.append((v.gate, ", ".join(v.claims) or "no claim linked"))
    for spec in specs:
        if spec.id not in ran:
            per_gate.append((spec.id,
                             (", ".join(spec.claims) or "no claim linked")
                             + f"  [{HUMAN['lead'][ClaimCause.UNRUN]}]"))
    if per_gate:
        out.append("One gate at a time — this is the command behind each row"
                   + (", and the code it runs:" if code else ":"))
        out.append("")
        out.append("```sh")
        width = max(len(g) for g, _ in per_gate[:_MAX_REPRODUCE_GATES])
        for gate_id, covers in per_gate[:_MAX_REPRODUCE_GATES]:
            files = code.get(gate_id)
            where = f" — {', '.join(files)}" if files else ""
            out.append(f"atompipe check --only {gate_id.ljust(width)}   # {covers}{where}")
        if len(per_gate) > _MAX_REPRODUCE_GATES:
            out.append(f"# ... and {len(per_gate) - _MAX_REPRODUCE_GATES} more;"
                       f" `atompipe gate list` prints them all")
        out.append("```")
        out.append("")

    out.append("And show the gates above can actually fail, which is the only "
               "reason their passes mean anything:")
    out.append("")
    out.append("```sh")
    out.append("atompipe gate selftest"
               "           # runs every negative control; a gate that passes its")
    out.append("                                 # own known-bad fixture is a logger, not a gate")
    out.append("```")
    out.append("")
    return out


# --------------------------------------------------------------------------- #
# public surface
# --------------------------------------------------------------------------- #
def render_markdown(ledger: Ledger, registry: Any, *, stale: bool = False,
                    stale_gates: Collection[str] = (), model_error: str = "",
                    title: str = "", root: str = "",
                    params: Sequence[Any] | None = None,
                    stale_reasons: Mapping[str, str] | None = None) -> str:
    """The full readiness report as markdown — the project's public deliverable.

    Sections, in the order a sceptical reader needs them: the readiness
    sentence, what is checked, what is pending build, the gaps, what is
    assumed, what is failing, stale, skipped or open, and the commands to check
    all of it. The sentence leads with the worst thing that is true. Every
    unresolved claim is in exactly one section, under its own word (P2.1-D19).

    `registry` is a `gates.Registry` (or None). It is a parameter rather than the
    module global so this function can be tested against a registry built in the
    test, and so a report can be rendered on a machine where the packs that
    produced the verdicts are not installed. It is typed `Any` deliberately:
    importing `gates` merely to name the type would make the report depend on the
    gate *runtime*, and rendering a ledger must never require the ability to run
    one.

    `ledger` carries the verdicts to render — the caller's resolution laid over
    the records (`verdicts.resolve`), never a list this function re-judges.
    `stale_gates` is that resolution's: the gates whose verdict is not current
    (invalidated, unknown, or with a control not demonstrated at this version).
    Each pass they cover reads Stale and leaves the checked table, and the
    failing section names the gate; `stale_reasons` (`{gate: why}`) says what
    moved, when the caller has it. `stale=True` marks every gate stale at once.
    `model_error` — the model does not load — is said under the readiness
    sentence: every verdict that reads it is then not current, and the reader
    should know why before reading the tables.

    `root`, when given, spells each gate's code files in `## Reproduce`
    relative to the project (`write_report` passes it). Without it the files
    are left out rather than spelled by this machine's absolute paths.

    `params` is the caller's `modelio.param_view` — every parameter the model
    holds, its value from the model and its rationale from the model or its
    record — and the assumed section judges it with
    `modelio.undefended_params`, the list `doctor` prints. Never `ledger.params`:
    from 1.3 those are sparse records (review). Without it, or with a model that
    does not load, the section says the rationales are not known
    (`RATIONALE_UNKNOWN`) instead of calling any number defended or not.

    The title is the project and its revision — no time: a regenerated report
    of an unchanged design must be byte-identical (S-89).
    """
    composed = _compositions(ledger, registry, stale, stale_gates)
    cover = _coverage(ledger, registry)

    name = ledger.meta.name or "(unnamed project)"
    rev = ledger.meta.revision or "unversioned"
    heading = title or f"{name} — readiness ({rev})"

    out: list[str] = [f"# {heading}", ""]
    out.append(_verdict_sentence(ledger, composed, registry, stale=stale, markdown=True))
    out.append("")
    if model_error:
        out.append(f"**The model does not load**, so no verdict that reads it is "
                   f"current: {_trunc(model_error, 300)}")
        out.append("")
    if ledger.meta.summary:
        out.append(f"> {_trunc(ledger.meta.summary, 400)}")
        out.append("")

    out += _section_proven(ledger, composed, cover, stale=stale)
    out += _section_pending_build(ledger, composed)
    out += _section_gaps(ledger, composed, registry, stale_reasons=stale_reasons)
    out += _section_assumed(ledger, composed, params, model_error)
    out += _section_failing(ledger, composed, cover, registry, stale_gates=stale_gates,
                            stale_reasons=stale_reasons)
    out += _section_reproduce(ledger, registry, root=root)

    out.append("---")
    out.append("")
    out.append("*Generated by `atompipe report` from the ledger and its verdict cache. "
               "Do not hand-edit: it is an output, not a source. If a line here is "
               "wrong, the ledger is wrong.*")
    return "\n".join(out).rstrip() + "\n"


def render_terminal(ledger: Ledger, registry: Any, *, stale: bool = False,
                    stale_gates: Collection[str] = (),
                    params: Sequence[Any] | None = None,
                    stale_reasons: Mapping[str, str] | None = None) -> str:
    """The same report compressed to something an agent can hold in context.

    Under ~40 lines for a healthy project, which is the point: this is what
    `atompipe status` prints on every loop, and a status command that costs a
    screenful stops being read. Checked claims are counted, never listed — the
    only thing worth a line each is what is *not* resolved, in severity order
    (P2.1-D16), each with its reason (`reason`).

    No ANSI colour anywhere. This output goes into transcripts, logs and pipes at
    least as often as it goes to a terminal, and an escape sequence in a diff is
    noise in all three. Status is carried by `HUMAN`'s five-wide tags instead,
    upper case for the loud ones (Failing, a crash's Skipped, Stale).

    `stale_gates`, `stale` and `stale_reasons` as for `render_markdown`. The
    head line carries no sweep time — `status` prints its own `invalidated:` and
    `last check run:` lines, each with a source.

    `params` as for `render_markdown`: the `standing:` line counts
    `modelio.undefended_params` over it, and says nothing about parameters
    without it (a model that does not load is `status`'s `model:` line).
    """
    composed = _compositions(ledger, registry, stale, stale_gates)
    lines: list[str] = []

    name = ledger.meta.name or "(unnamed)"
    rev = ledger.meta.revision or "unversioned"
    head = f"atompipe readiness — {name} {rev}"
    if not ledger.verdicts:
        head += " — never evaluated"
    if stale:
        head += f" — every verdict {HUMAN['lead'][ClaimCause.INVALIDATED]}"
    lines.append(head)
    lines.append(_verdict_sentence(ledger, composed, registry, stale=stale, markdown=False))
    lines.append(count_line(composed))

    # A claim the resolver calls Checked while its evidence says otherwise is
    # listed first, as the contradiction it is (D18) — never counted silently
    # among the Checked ones the terminal does not list.
    cover = _coverage(ledger, registry)
    contradicted = {c.id: why for c in ledger.claims
                    if (why := _disagreement(ledger, c, composed[c.id], cover))}
    problems = [c for c in ledger.claims if c.id in contradicted] + [
        c for c in in_severity(ledger, composed)
        if composed[c.id].status not in (ClaimStatus.PASS, ClaimStatus.VERIFIED)]
    for claim in problems[:_MAX_TERMINAL_CLAIMS]:
        found = composed[claim.id]
        why = (f"status and evidence disagree — {_trunc(contradicted[claim.id], 60)}"
               if claim.id in contradicted
               else reason(found, ledger, claim, stale_reasons=stale_reasons))
        lines.append(f"{status_tag(found.status, errored=found.errored)} {claim.id} "
                     f"{_trunc(claim.statement, 52)} — {why}")
    if len(problems) > _MAX_TERMINAL_CLAIMS:
        lines.append(f"       ... and {len(problems) - _MAX_TERMINAL_CLAIMS} more "
                     f"unresolved claims — see docs/readiness.md")

    # The gap RECORDS (`find_gaps`' Needs), with their tool options. Headed
    # "gap records", never "gaps": the count line above counts the claims that
    # read Gap — an unowned assumption and an unqualified evaluator included —
    # and two numbers under one word one screen apart is the reader guessing
    # which (review of the P2.1 design: `2 gaps` over `gaps 1:`).
    needs = _needs(ledger, registry)
    if needs:
        lines.append(f"gap records {len(needs)}:")
        for need in needs[:_MAX_TERMINAL_GAPS]:
            options = ", ".join(
                f"{c.name}" + (f" ({_trunc(c.cost, 40)})" if c.cost else " (cost unstated)")
                for c in need.candidates[:2]
            ) or "no tool option proposed"
            lines.append(f"  {need.id} {_trunc(need.quantity, 44)} "
                         f"({need_word(need.status)}) — {options}")
        if len(needs) > _MAX_TERMINAL_GAPS:
            lines.append(f"  ... and {len(needs) - _MAX_TERMINAL_GAPS} more")

    # One line of gate bookkeeping, keyed on `Verdict.outcome` and the spine's
    # mark — never the flags. What slipped through (P2.0 F-10): it read
    # `skipped` and `error` separately, so a verdict that said both was counted
    # as a skip AND a crash. `unrun` is the number that matters and the one
    # nothing else prints: gates that exist, cost nothing to run, and did not.
    refused = [v for v in ledger.verdicts if getattr(v, "unqualified", "")]
    counted = [v for v in ledger.verdicts if not getattr(v, "unqualified", "")]
    ran = [v for v in counted if v.outcome in ("pass", "fail")]
    skipped = [v for v in counted if v.outcome == "skipped"]
    errored = [v for v in counted if v.outcome == "error"]
    unrun = _unrun_specs(ledger, registry)
    gate_bits = [f"{len(ran)} ran"]
    moved = sorted({v.gate for v in ledger.verdicts} & set(stale_gates))
    if moved:
        gate_bits.append(f"{len(moved)} {HUMAN['lead'][ClaimCause.INVALIDATED]}")
    if skipped:
        gate_bits.append(f"{len(skipped)} {HUMAN['outcome']['skipped']}")
    if errored:
        gate_bits.append(f"{len(errored)} {HUMAN['outcome']['error']}")
    if refused:
        gate_bits.append(f"{len(refused)} {HUMAN['lead'][ClaimCause.UNQUALIFIED]}")
    if unrun:
        shown = ", ".join(s.id for s in unrun[:3])
        extra = f", +{len(unrun) - 3}" if len(unrun) > 3 else ""
        gate_bits.append(f"{len(unrun)} registered but {HUMAN['lead'][ClaimCause.UNRUN]} "
                         f"({shown}{extra})")
    if ledger.verdicts or unrun:
        lines.append("gates: " + ", ".join(gate_bits))

    constraints: list[str] = []
    undefended = modelio.undefended_params(params or ())
    if undefended:
        constraints.append(f"{len(undefended)} "
                           f"{_plural(len(undefended), 'param')} with no rationale")
    unread = unextracted(ledger)
    if unread:
        constraints.append(f"{len(unread)} ingested "
                           f"{_plural(len(unread), 'artifact')} nobody read")
    if constraints:
        lines.append("standing: " + ", ".join(constraints))

    lines.append("next: atompipe check --tier 0 ; atompipe gap --propose ; "
                 "atompipe report --write")
    return "\n".join(lines) + "\n"


def _terminal_reason(ledger: Ledger, claim: Claim, status: ClaimStatus,
                     cover: dict[str, list[str]], *, full: bool = False,
                     stale_gates: Collection[str] = (),
                     stale_reasons: Mapping[str, str] | None = None) -> str:
    """`reason` for one claim, composed here over `cover`'s gates — the wrapper
    the terminal rows and `ReasonsAgree` call (`cli._blocking_reason` is the
    other). `status` is the caller's, and must be what `compose` says: a
    reason built for another status would be the second story S-68 named."""
    known = list(cover.get(claim.id) or claim.gates or [])
    found = claim_logic.compose(_with_gates(claim, known), ledger.verdicts,
                                stale_gates=stale_gates)
    return reason(found, ledger, claim, full=full, stale_reasons=stale_reasons)


def _with_gates(claim: Claim, gates: list[str]) -> Claim:
    import dataclasses
    return dataclasses.replace(claim, gates=list(gates))


def write_report(root: str, ledger: Ledger, registry: Any, *,
                 stale: bool = False, stale_gates: Collection[str] = (),
                 model_error: str = "", params: Sequence[Any] | None = None,
                 stale_reasons: Mapping[str, str] | None = None) -> str:
    """Render the markdown report to `docs/readiness.md` and return its path.

    Written atomically: a half-truncated readiness report left behind by a crash
    would be a document that claims less than is true, which is a strange way to
    fail but still a wrong one.

    The destination comes from `store.project_paths`, not from a join spelled
    here. Layout is `store`'s job alone; a second module that knows where
    `docs/readiness.md` lives is a second module to edit when it moves.

    `stale_gates`, `stale_reasons`, `model_error` and `params` as for
    `render_markdown`; `root` spells the gates' code files. The file holds no
    time and no rho, so rewriting it for an unchanged design and unchanged
    outcomes leaves the tracked bytes alone (S-89). `model_error` reaches the
    file as it reaches `report`'s stdout: without it, a broken model's
    parameters would be judged from whatever records exist.
    """
    path = store.project_paths(root)["readiness"]
    ensure_dir(os.path.dirname(path))
    atomic_write_text(path, render_markdown(ledger, registry, stale=stale,
                                            stale_gates=stale_gates, root=root,
                                            model_error=model_error, params=params,
                                            stale_reasons=stale_reasons))
    return path


# --------------------------------------------------------------------------- #
# JUnit: the same judgement, in the one format every CI already renders
# --------------------------------------------------------------------------- #
#: Where `check --junit` and `gate selftest --junit` write when given no path,
#: relative to the project root.
#:
#: Why this value: `.atompipe/out/` is gate scratch, ignored both by the
#: `.atompipe/.gitignore` that `init` writes and by this repository's own
#: `.gitignore`, so a `check --junit` in CI or on a laptop never dirties the tree.
#: *Rejected:* the project root — tracked, so every run would leave a modified
#: file behind and the clean-tree gate (G5) would read red for a report.
JUNIT_DEFAULT = ".atompipe/out/junit.xml"

#: The suites `render_junit` always writes, in this order, even when one is
#: empty: a CI step (and `tests/oracle/bracket_signature.py`) finds them by name.
#: The claims are split by criticality because only the critical suite is the
#: exit code's judgement — its red count IS `len(claims.blocking())`.
#: *Rejected:* writing a suite only when it has testcases — an absent suite reads
#: exactly like one that had nothing to say; one suite for every claim — a failing
#: nice-to-have would then sit in the count the exit code is held to.
_JUNIT_SUITES = ("gates", "claims.critical", "claims.not-critical")

#: The code points XML 1.0 forbids (its `Char` production): C0 controls other
#: than tab, LF and CR; the surrogates, which a Python `str` can hold alone (bytes
#: decoded with `surrogateescape`) and UTF-8 cannot encode; and U+FFFE/U+FFFF.
#: What slipped through while designing this (the slice probe behind phase-1.md
#: 1.1): ElementTree writes every one of them raw — an ANSI colour escape from a
#: solver's log (`\x1b`), a NUL from a C string, a form feed from a pager — and the
#: file then fails to parse, so the CI step that reads it shows no failures at
#: all. *Rejected:* dropping them (the text a reader needs to recognise, an escape
#: sequence, vanishes); XML 1.1 (most CI parsers refuse it).
_XML_ILLEGAL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff￾￿]")


def junit_safe(text: Any) -> str:
    """`text` with every code point XML 1.0 forbids shown as visible `\\xNN` / `\\uNNNN`.

    `"\\x1b[31mred"` becomes the eleven visible characters `\\x1b[31mred`, so the
    escape a gate's log carried is still recognisable where CI shows it. Tab, LF,
    CR and every other character (accents, emoji, U+007F) are left alone.
    Backslashes are not escaped: a Windows path in a message must read as one.
    `None` is `""`; anything else is `str()`-ed first.
    """
    if text is None:
        return ""
    return _XML_ILLEGAL.sub(
        lambda m: (f"\\x{ord(m.group()):02x}" if ord(m.group()) < 0x100
                   else f"\\u{ord(m.group()):04x}"),
        str(text))


def _xml_sub(parent: Any, tag: str, **attrs: Any) -> Any:
    """A child element whose every attribute went through `junit_safe`.

    Built through this helper only, so no value reaches the tree unsanitised —
    a second, direct `SubElement` call is how one field would slip past.
    `makeelement` + `append` is what `SubElement` does, without an import here.
    """
    child = parent.makeelement(tag, {k: junit_safe(v) for k, v in attrs.items()})
    parent.append(child)
    return child


def _xml_text(element: Any, text: Any) -> None:
    safe = junit_safe(text)
    if safe:
        element.text = safe


def _xml_properties(parent: Any, pairs: Iterable[tuple[str, Any]]) -> None:
    props = _xml_sub(parent, "properties")
    for name, value in pairs:
        _xml_sub(props, "property", name=name, value=value)


def _junit_time(seconds: Any) -> str:
    """Seconds as a plain decimal (`0.0004`, never `4e-04`): JUnit's schema types
    `time` as xs:decimal, and a parser that follows it rejects an exponent."""
    try:
        value = float(seconds)
    except (TypeError, ValueError):
        return "0"
    if not math.isfinite(value) or value <= 0:
        return "0"
    return f"{value:.6f}".rstrip("0").rstrip(".") or "0"


def _junit_measured(verdict: Verdict) -> str:
    """`measured 0.699718 mm vs limit 0.5 mm`, then one line per evidence file."""
    units = f" {verdict.units}" if verdict.units else ""
    lines: list[str] = []
    if verdict.measured is not None:
        line = f"measured {_num(verdict.measured)}{units}"
        if verdict.limit is not None:
            line += f" vs limit {_num(verdict.limit)}{units}"
        lines.append(line)
    elif verdict.limit is not None:
        lines.append(f"limit {_num(verdict.limit)}{units}")
    lines += [f"evidence: {path}" for path in verdict.evidence or []]
    return "\n".join(lines)


def _junit_outcome(case: Any, verdict: Verdict) -> None:
    """The outcome child of one gate's testcase: nothing, iff `outcome == "pass"`.

    Keyed off `Verdict.outcome`, the one definition, and never off `passed`: a
    writer reading the flag renders a skip that also said `passed=True` as a
    green testcase — the generous direction phase-1.md names as this format's
    failure (R-5; `RenderersAgree.test_junit` holds all 8 flag combinations).
    A refused evaluator's error is `type="not-admitted"`, keyed on the spine's
    mark (`Verdict.unqualified`) and never on its text: "the instrument is not
    trusted" and "the instrument crashed" send a reader to different places,
    and a gate whose own crash reads "not admitted: …" is still a crash.
    """
    outcome = verdict.outcome
    if outcome == "pass":
        return
    if outcome == "fail":
        child = _xml_sub(case, "failure", type="fail",
                         message=verdict.detail or "the gate reported a failure")
        _xml_text(child, _junit_measured(verdict))
    elif outcome == "error":
        child = _xml_sub(case, "error",
                         type="not-admitted" if getattr(verdict, "unqualified", "")
                         else "error",
                         message=str(verdict.error))
        _xml_text(child, verdict.detail)
    else:
        _xml_sub(case, "skipped", message=verdict.skip_reason or "skipped")


def _junit_gate_case(suite: Any, gate_id: str, pack: str, verdict: Verdict | None, *,
                     cached: bool = False, not_run: str = "") -> None:
    """One gate's testcase. No verdict is a skip — "not run: <why>" — never a pass.

    A cached testcase has `time="0"` (a cached row never replays a duration: the
    run took none) and carries `<properties><property name="cached"
    value="true"/></properties>`, which is metadata, not an outcome.
    """
    case = _xml_sub(suite, "testcase",
                    classname=f"pack.{pack}" if pack else "project", name=gate_id,
                    time="0" if cached or verdict is None
                    else _junit_time(verdict.duration_s))
    if verdict is None:
        # A registered gate with nothing to say is the quiet failure rule 2 in
        # this module's docstring describes; here it stays visible as a skip.
        _xml_sub(case, "skipped",
                 message=f"not run: {not_run}" if not_run
                 else "not run: no verdict in this run")
        return
    if cached:
        _xml_properties(case, [("cached", "true")])
    _junit_outcome(case, verdict)


def _junit_red(element: Any) -> int:
    """Testcases under `element` with a failure or an error: what CI paints red."""
    return sum(1 for case in element.iter("testcase")
               if case.find("failure") is not None or case.find("error") is not None)


def _junit_tally(element: Any, suites: Iterable[Any]) -> None:
    """Set `tests failures errors skipped time` on `element` from the testcases
    inside `suites` — counted, never carried: a CI summary reads these
    attributes, and a count kept beside the children drifts from them."""
    cases = [case for suite in suites for case in suite.iter("testcase")]
    element.set("tests", str(len(cases)))
    element.set("failures", str(sum(1 for c in cases if c.find("failure") is not None)))
    element.set("errors", str(sum(1 for c in cases if c.find("error") is not None)))
    element.set("skipped", str(sum(1 for c in cases if c.find("skipped") is not None)))
    element.set("time", _junit_time(sum(float(c.get("time") or 0) for c in cases)))


def _junit_serialise(root: Any, suites: list[Any]) -> str:
    import xml.etree.ElementTree as ET
    for suite in suites:
        _junit_tally(suite, [suite])
    _junit_tally(root, suites)
    ET.indent(root, space="  ")
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            + ET.tostring(root, encoding="unicode") + "\n")


def _claim_case(suite: Any, ledger: Ledger, claim: Claim, composed: Any,
                cover: dict[str, list[str]], *, red: bool,
                stale_reasons: Mapping[str, str] | None = None) -> None:
    """One claim's testcase, asserting "this claim does not block the spend".

    `red` is the caller's: blocking (critical) or Failing (not critical). A claim
    Skipped by a crash is an `<error>`, critical or not — a crash reads louder
    than a missing tool (invariant 2; PLAN-v0.14 §1.5: "the claim stays a JUnit
    `<error>`"). What slipped through (P2.0 F-7): `<error>` was chosen only for
    a FAIL whose explaining verdict errored, so rung 4 moved alone would have
    made a required crash `<failure type="blocked">` and a not-required one
    `<skipped>`. A Checked status its evidence contradicts is a red
    `status-and-evidence-disagree` failure (D18). Everything else short of
    Checked is skipped with its word and reason (`pending build: needs an
    article`); childless only for a Checked claim its evidence backs.
    """
    case = _xml_sub(suite, "testcase", classname=suite.get("name"), name=claim.id,
                    time="0")
    rendered = claim.acceptance.render() if claim.acceptance else ""
    text = (claim.statement or "") + (f"\nacceptance: {rendered}" if rendered else "")
    status = composed.status
    contradicted = _disagreement(ledger, claim, composed, cover)
    if contradicted:
        child = _xml_sub(case, "failure", type="status-and-evidence-disagree",
                         message=f"status and evidence disagree — {contradicted}")
        _xml_text(child, text)
        return
    why = reason(composed, ledger, claim, full=True, stale_reasons=stale_reasons)
    if composed.errored:
        child = _xml_sub(case, "error", type="error", message=why)
        _xml_text(child, text)
        return
    if red:
        child = _xml_sub(case, "failure", type=status.value, message=why)
        _xml_text(child, text)
        return
    if status in (ClaimStatus.PASS, ClaimStatus.VERIFIED):
        return
    _xml_sub(case, "skipped", message=f"{word(status)}: {why}")


def render_junit(ledger: Ledger, verdicts: Iterable[Verdict], registry: Any, *,
                 tier: Any, ready: bool, exit_code: int, when: str,
                 not_run: Any = None, cached: Iterable[str] = frozenset(),
                 stale: bool = False, spine: str = "",
                 stale_gates: Collection[str] = (),
                 stale_reasons: Mapping[str, str] | None = None) -> str:
    """This command's run as JUnit XML — never greener than its exit code.

    A CI system renders this file, not the exit code, so the file carries the
    same judgement the exit code was made from and may only ever be redder:

    * **`gates`** — one testcase per REGISTERED gate, in registry order, so the
      count is stable between runs. Its outcome is the gate's verdict in
      `verdicts` (this command's rows): childless iff `outcome == "pass"`; fail
      -> `<failure type="fail">`; error -> `<error type="error">`, or
      `type="not-admitted"` for a refused evaluator (`Verdict.unqualified`);
      skipped -> `<skipped>`. A gate with no row is `<skipped message="not run:
      <why>">`, the why from `not_run` (`(gate, reason)` pairs or a mapping,
      e.g. "above the tier ceiling", "excluded by --only"). Gates in `cached`
      get `time="0"` and a `cached` property.
    * **`claims.critical`** — one testcase per critical claim, each asserting
      "does not block the spend". Its red testcases are exactly
      `claims.blocking(ledger, registry, stale=stale)`, so failures plus errors
      equal `len(blocking())`, a claim Skipped by a crash an `<error>`; zero
      claims adds one failing `no claims recorded` (zero blocking claims out of
      zero is not readiness, and `check` exits 1).
    * **`claims.not-critical`** — Failing red, a crash an `<error>`; every other
      claim short of Checked skipped with its word and reason.

    `ledger` must be the ledger the exit code was judged from, and `stale` and
    `stale_gates` what it was judged with (the resolution's stale gates, from
    1.2): the claim suites are recomputed from them, never from `verdicts`.
    Should a caller hand over an exit code the claims do not
    explain anyway — a non-zero code with nothing blocking, as a stale project
    rendered without `stale=True` would give — `claims.critical` gains one
    failing `exit code` testcase saying so. A caller's disagreement surfaces as
    red, never as a green file beside a red job.

    Root `<properties>`: `spine_version`, `exit_code`, `tier`, `ready` (the
    caller's: nothing stops `check`), `all_required_checked` (*ready* in
    GLOSSARY §4's sense, `claims.summarise`'s — critique of the P2.1 design: a
    `ready=true` property beside a claim waiting for an article was READY on
    one more channel), `when`, and `spine` when given (the spine digest, from
    Phase 1.2). `when` is the caller's timestamp; this function reads no clock.
    Every attribute and text value goes through `junit_safe`, so the file
    parses whatever a gate wrote.
    """
    import xml.etree.ElementTree as ET        # ~6 ms; only `--junit` pays it

    code = int(exit_code)
    composed = _compositions(ledger, registry, stale, stale_gates)
    root = ET.Element("testsuites", {"name": "atompipe check"})
    props = [("spine_version", __version__), ("exit_code", str(code)),
             ("tier", str(_int_or(tier))), ("ready", "true" if ready else "false"),
             ("all_required_checked",
              "true" if readiness(ledger, composed)["ready"] else "false"),
             ("when", when)]
    if spine:
        props.append(("spine", spine))
    _xml_properties(root, props)
    suites = {name: _xml_sub(root, "testsuite", name=name) for name in _JUNIT_SUITES}

    rows = list(verdicts or ())
    by_gate = {v.gate: v for v in rows}
    reasons = dict(not_run or ())
    cached = frozenset(cached or ())
    if registry is not None:
        gates = [(spec.id, spec.pack) for spec in _specs(registry)]
    else:
        # No registry, no list of what should have run: fall back to the rows
        # themselves, in order, rather than render an empty and green suite.
        gates = [(gate_id, "") for gate_id in dict.fromkeys(v.gate for v in rows)]
    for gate_id, pack in gates:
        verdict = by_gate.get(gate_id)
        _junit_gate_case(suites["gates"], gate_id, pack or (verdict.pack if verdict else ""),
                         verdict, cached=gate_id in cached,
                         not_run=str(reasons.get(gate_id, "")))

    cover = _coverage(ledger, registry)
    blocking = {c.id for c, _ in claim_logic.blocking(ledger, registry, stale=stale,
                                                      stale_gates=stale_gates)}
    for claim in ledger.claims:
        found = composed[claim.id]
        if claim.critical:
            _claim_case(suites["claims.critical"], ledger, claim, found, cover,
                        red=claim.id in blocking, stale_reasons=stale_reasons)
        else:
            _claim_case(suites["claims.not-critical"], ledger, claim, found, cover,
                        red=found.status in (ClaimStatus.FAIL, ClaimStatus.REFUTED),
                        stale_reasons=stale_reasons)

    critical = suites["claims.critical"]
    if not ledger.claims:
        case = _xml_sub(critical, "testcase", classname="claims.critical",
                        name="no claims recorded", time="0")
        _xml_sub(case, "failure", type="no-claims",
                 message="no claims recorded, so nothing was evaluated — an empty "
                         "ledger is not ready")
    if code != 0 and _junit_red(critical) == 0:
        case = _xml_sub(critical, "testcase", classname="claims.critical",
                        name="exit code", time="0")
        _xml_sub(case, "failure", type="exit-code",
                 message=f"the command exits {code} and no claim here blocks: the "
                         f"exit code and these verdicts disagree, and the exit code "
                         f"is the one CI obeys")
    return _junit_serialise(root, list(suites.values()))


#: The suffix `gates.selftest` files a control's verdict under (`f"{spec.id}#selftest"`,
#: spelled inline there). The `controls` testcase drops it: the suite already
#: says these are controls, and the bare gate id is what a reader searches the
#: code for. A result without the suffix keeps its name as it is. *Rejected:*
#: keeping it — `bracket.deflection#selftest` beside a suite named `controls` is
#: the same fact twice, and a CI that splits `classname.name` on dots reads it oddly.
_SELFTEST_SUFFIX = "#selftest"


def render_selftest_junit(results: Iterable[Verdict], *, exit_code: int, when: str,
                          baselines: Iterable[Verdict] | None = None) -> str:
    """`gate selftest` as JUnit XML — never greener than its exit code.

    * **`controls`** — one testcase per result of `gates.selftest`, named for
      the gate (the `#selftest` suffix dropped; the suite says what it is).
      Childless iff the control fired (`outcome == "pass"`); a control that did
      not fire, or crashed, is red; a tooling skip is `<skipped>`.
    * **`baselines`** — pack mode only (`baselines` not None): each gate's
      verdict on its pack's own `selftest/baseline.json`, childless iff it passed.

    A selftest that ran nothing and exits 1 (no `--allow-empty`) gains one
    failing `no controls ran` testcase; any other non-zero exit with nothing red
    gains a failing `exit code` testcase. An empty file beside a red job is the
    "ran zero controls and reported success" failure, in XML.
    """
    import xml.etree.ElementTree as ET        # ~6 ms; only `--junit` pays it

    code = int(exit_code)
    results = list(results or ())
    root = ET.Element("testsuites", {"name": "atompipe gate selftest"})
    _xml_properties(root, [("spine_version", __version__), ("exit_code", str(code)),
                           ("when", when)])
    controls = _xml_sub(root, "testsuite", name="controls")
    suites = [controls]
    for verdict in results:
        gate_id = verdict.gate
        if gate_id.endswith(_SELFTEST_SUFFIX):
            gate_id = gate_id[: -len(_SELFTEST_SUFFIX)]
        _junit_gate_case(controls, gate_id, verdict.pack, verdict)
    if baselines is not None:
        base = _xml_sub(root, "testsuite", name="baselines")
        suites.append(base)
        for verdict in baselines:
            _junit_gate_case(base, verdict.gate, verdict.pack, verdict)

    if code != 0 and _junit_red(root) == 0:
        if not results:
            case = _xml_sub(controls, "testcase", classname="controls",
                            name="no controls ran", time="0")
            _xml_sub(case, "failure", type="empty",
                     message=f"no control ran and the command exits {code}: a "
                             f"selftest that exercised nothing has shown no gate "
                             f"can fail")
        else:
            case = _xml_sub(controls, "testcase", classname="controls",
                            name="exit code", time="0")
            _xml_sub(case, "failure", type="exit-code",
                     message=f"the command exits {code} and no control or "
                             f"baseline here failed: the exit code and these "
                             f"results disagree, and the exit code is the one CI "
                             f"obeys")
    return _junit_serialise(root, suites)


def _int_or(value: Any) -> Any:
    """`int(value)` when it has one (a `Tier` renders as `0`, not `Tier.INSTANT`)."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return value


__all__ = [
    "HUMAN",
    "StatusWords",
    "STATUS_TAG",
    "SECTION_PROVEN",
    "JUNIT_DEFAULT",
    "RATIONALE_UNKNOWN",
    "words",
    "word",
    "status_tag",
    "severity",
    "in_severity",
    "count_bits",
    "count_line",
    "reason",
    "status_view",
    "words_table",
    "outcome_words",
    "need_word",
    "page_phrases",
    "recorded_by",
    "readiness",
    "not_ready_line",
    "render_terminal",
    "render_markdown",
    "write_report",
    "render_junit",
    "render_selftest_junit",
    "junit_safe",
]
