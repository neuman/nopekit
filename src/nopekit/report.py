# SPDX-License-Identifier: Apache-2.0
"""nopekit.report — the ledger rendered: what is proven, what is not, and why.

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
markdown report prints no time at all: a regenerated `REPORT.md` (P2.5b; it was the
tracked `docs/readiness.md`) changes only when the claims or the verdict outcomes
do (S-89). It used to be titled
with the last sweep's time and to end with that sweep's model and inputs hashes,
so every re-run of an unchanged design rewrote a tracked file — and once
verdicts are served from the cache, "this run" would be false on every hit. The
registry is passed in — a report that reached for `gates.REGISTRY` could only be
tested against a project it had already imported.
"""
from __future__ import annotations

import dataclasses
import math
import os
import re
from collections.abc import Mapping
from types import MappingProxyType
from typing import Any, Callable, Collection, Iterable, NamedTuple, Sequence

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
from .util import atomic_write_text, ensure_dir, printable

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
# says, and the verb `nopekit check`).
#
# From P2.3 Checked's hint says *qualified*: a pass counts only from an
# evaluator qualified at its version — known-good pass, known-bad fail, and for
# an evaluator not from a bundled pack every conclusive mutation a fail — so
# the word is now what Table 1 says Checked rests on. Until P2.3 it never said
# so: a known-bad-shown evaluator still counted, and saying it would have been
# the overclaim the P2.1 design rejected. It still never says "built from
# them": no article binds a physical pass yet (critique: the hint reaches the
# page's chip title).
#
# P2.4 widened four hints with the facts the composition gained (critique 11 of
# its design: "every evaluator qualified and passed" no longer implied Checked):
# a value compared with the claim's acceptance condition must meet it, and a
# pass counts only inside its evaluator's operating context.
_CHECKED = _status_row(
    ClaimStatus.PASS, "checked", "Checked", "checked", "ok   ",
    "every evaluator is qualified and passed on the current inputs, inside its operating "
    "context, and every value compared with the acceptance condition meets it — checked "
    "does not mean true")
_FAILING = _status_row(
    ClaimStatus.FAIL, "failing", "Failing", "failing", "FAIL ",
    "an evaluator failed the current design, a physical result failed, or an "
    "evaluator's value does not meet the claim's acceptance condition")
_STALE = _status_row(
    ClaimStatus.STALE, "stale", "Stale", "stale", "STALE",
    "a pass whose read set has changed since, or whose prerequisite is invalidated or "
    "unrun: nothing is checked now")
_ASSUMED = _status_row(
    ClaimStatus.ASSERTED, "assumed", "Assumed", "assumed", "assum",
    "accepted provisionally, with a reason and an owner — an assumption, or a claim carried "
    "outside an evaluator's operating context — unresolved")
_PENDING_BUILD = _status_row(
    ClaimStatus.UNVERIFIED, "pending build", "Pending build", "pending build", "build",
    "waits on an article: no physical result yet, or a pass no article binds to the "
    "current inputs")
_GAP = _status_row(
    ClaimStatus.UNCLAIMED, "gap", "Gap", "gaps", "gap  ",
    "no evaluator, none qualified, one unqualified, or one outside its operating context; "
    "or an assumption nobody owns")
# Skipped's hint and the skipped chip name all three causes of a skip (S-54):
# "skipped" had come to mean "install a tool" everywhere — the hints, and the
# skill's "install the tool, and re-run" — while packs skip themselves for a
# missing PARAMETER and, from P2.2, an evaluator is skipped behind a
# prerequisite that failed, where installing changes nothing.
_SKIPPED = _status_row(
    ClaimStatus.BLOCKED, "skipped", "Skipped", "skipped", "skip ",
    "an evaluator skipped — its tool is missing here, it skipped itself on its input, or "
    "a prerequisite is not established — and none failed: no usable verdict")
_OPEN = _status_row(
    ClaimStatus.PENDING, "open", "Open", "open", "open ",
    "an evaluator of the claim is unrun on the current inputs")
#: Skipped by a crash: the same status, louder (PLAN-v0.14 §1.5) — Failing's
#: tone in its tag, above every skipped row in its rank.
_SKIPPED_ERRORED = _SKIPPED._replace(
    tag="SKIP ", rank=claim_logic.SEVERITY_ORDER.index("errored"),
    hint="an evaluator errored: it crashed, so nothing was evaluated, and the evaluator "
         "itself is broken")

#: The site's words (architecture phase 5, SPEC 34 and 35): every string the
#: front page (`page.json`, `map.json`) bakes that is not record text, in
#: ASD-STE100 — short sentences, one word for one thing, present tense, the
#: imperative for an instruction, no contraction, no em-dash aside, no "not X
#: but Y", no question. CLI output and REPORT.md keep the glossary's register
#: (`HUMAN`'s other keys); the readiness sentence has one builder with two
#: registers (`_verdict_sentence(..., register="site")`), so the page and the
#: terminal never give two answers to "is it ready". `test_style` scans every
#: string here, formatted with sample values. *Rejected:* rewriting the CLI's
#: strings to STE (its transcript and shapes are pinned byte for byte, and the
#: glossary is that channel's rule); a second sentence builder for the page.
#:
#: The page is for a newcomer, so it says no word the paper or the code made
#: (follow-up after phase 3b): a built part where the CLI says an article
#: (GLOSSARY §1 forbids *real part* and *built object*, not this), "showed that
#: it can fail" for qualified, "does not count" for an unqualified evaluator's
#: outcome, "the current design" for the current inputs (the page's hints
#: already said it), "the results" for the verdict cache. What slipped
#: through: the page read "Pending build: 1 claim needs an article (C5). No
#: claim is checked on any article." — the CLI's hardware clause, which phase 5
#: let through because it passed the style scan — and "This test on the real
#: part", an article's Never-say. `test_style.TheSiteSaysABuiltPart` fails on
#: "article" in any string here, in what the build bakes, or in the template.
_SITE_WORDS: Mapping[str, Any] = MappingProxyType({
    "generated": "nopekit site build wrote this file from the records, the results in "
                 ".nopekit/verdicts/ and site/content/. It is not a source. Change those, "
                 "then build again.",
    "other_board": "Other",
    "all_board": "Claims",
    "other_zone": "Other choices",
    "all_zone": "Choices",
    "no_representation": "Add a model3d view to show the design here. An image view or a "
                         "diagram view also works.",
    "value": "{measured} {units} against a limit of {limit}",
    "value_bare": "{measured} {units}",
    "margin_inside": ", {pct} inside the limit",
    "margin_past": ", {pct} past the limit",
    "margin_at": ", at the limit",
    "chart_margin": MappingProxyType({"inside": "{delta} inside the limit",
                                      "past": "{delta} past the limit"}),
    # One producer for a part to build again (`site_rebuild_text`): the
    # readiness sentence's clause and next steps' row say it alike.
    "rebuild": "Part {article} is from an earlier design. Build a new part for {claims}. "
               "These values changed: {moves}.",
    "rebuild_bare": "Part {article} is from an earlier design. Build a new part for "
                    "{claims}.",
    "ready_word": "ready",
    "not_ready_word": "not ready",
    # The readiness sentence in the site's register: one key per branch of
    # `_verdict_sentence`, mirrored one for one.
    "readiness": MappingProxyType({
        "no_claims": "{rev} has no claims, so nothing is evaluated. A project with no claims "
                     "is not ready. Write claims/C1.json with a statement and an acceptance "
                     "condition to start.",
        "never": "{rev} is NOT ready. No evaluator ran on its {n} {claims}. Run nopekit "
                 "check --tier 0 to start.",
        "never_ready": "{rev} is ready, as last evaluated. No evaluator ran on its {n} "
                       "{claims}.",
        "none_required": "{rev} is NOT ready. None of its {n} {claims} is required.",
        "not_ready": "{rev} is NOT ready. {k} of {req} required {claims} {is_} unresolved: "
                     "{groups}.",
        "tally": "{k} of {n} {claims} {is_} {checked} on the current design.",
        "ready_last": "{rev} is ready, as last evaluated. Every required claim is {checked} "
                      "on the current design.",
        "other": "{n} {claims} that {is_} not required {is_} unresolved: {groups}.",
        "unrun": "{n} {evaluators} did not run.",
        "ready_for": "{rev} is ready for {m}. Every claim that {m} requires is {checked} "
                     "on the current design.",
        "not_ready_for": "{rev} is NOT ready for {m}. {k} of {n} required {claims} {is_} "
                         "unresolved: {groups}.",
        "ready_for_last": "{rev} is ready for {m}, as last evaluated. Every claim that {m} "
                          "requires is {checked} on the current design.",
        "not_ready_for_last": "{rev} is NOT ready for {m}, as last evaluated. {k} of {n} "
                              "required {claims} {is_} unresolved: {groups}.",
        "requires_nothing": "{rev} is NOT ready for {m}. {m} requires no claim. Add the "
                            "claims that it needs to milestones/{m}.json.",
        # The hardware clause (`_hardware_clause(register="site")`), in every
        # branch as the CLI's: the parts to build again are `rebuild` above.
        "pending": "{Pending}: {n} {claims} {need} a test on a built part ({ids})",
        "on_article": "{Checked} on a built part: {n} ({ids}).",
        "none_on_article": "No claim is {checked} on any built part.",
        # "the {n} {claims} on this page", never "these {n}": "these 1 claim"
        # (review of P2, the one-claim demo project).
        "limits": "{Checked} does not mean true. Its evaluators have shown that they can "
                  "fail. They have not shown that they are correct or sufficient. Nothing "
                  "outside the {n} {claims} on this page is evaluated.",
    }),
    # Each status token's hint on the page. The checked hint does not say
    # "does not mean true": the page says that once, in `verdict.limits`.
    "hint": MappingProxyType({
        "checked": "Every evaluator showed that it can fail, and each one passed on the "
                   "current design.",
        "failing": "An evaluator or a test on a built part failed the current design.",
        "errored": "An evaluator stopped with an error. A crash settles nothing.",
        # Every cause of a skip (S-54), as the CLI's hint says them. What slipped
        # through (P8, the motorbike demo): a claim skipped behind a guard that
        # FAILED carried "A tool that it needs is missing." on its chip.
        "skipped": "An evaluator did not run. A tool or a value that it needs is missing, "
                   "or an evaluator that it needs first did not pass.",
        "gap": "Nothing can settle this claim yet.",
        "open": "An evaluator can settle this claim. It did not run on the current design.",
        # Both of Stale's evaluator causes (issue #15, D1): a moved design, or
        # an evaluator nopekit cannot see into. What slipped through: the hint
        # said "The design changed" beside a solver's result on an untouched
        # design.
        "stale": "The design changed after the result, or nopekit cannot see if the result "
                 "is current.",
        "pending_build": "Only a test on a built part can settle this claim.",
        "assumed": "A person accepts this claim for now.",
    }),
    # One sentence per ClaimCause: why a claim reads what it reads.
    "cause": MappingProxyType({
        "failed": "An evaluator failed the current design.",
        "physical-fail": "A test on a built part failed.",
        "errored": "An evaluator stopped with an error. A crash settles nothing.",
        "prerequisite-errored": "An evaluator that this claim needs first stopped with an "
                                "error.",
        "skipped": "An evaluator did not run because a tool that it needs is missing.",
        "prerequisite": "An evaluator that this claim needs first did not pass, so the next "
                        "evaluator did not run.",
        "unqualified": "An evaluator did not show that it can fail, so its result does not "
                       "count.",
        "no-evaluator": "No evaluator checks this claim yet.",
        "no-owner": "This assumption has no owner.",
        "owner-unattributed": "The owner of this assumption did not record it in their own "
                              "shell.",
        "no-reason": "This assumption has no reason.",
        "unrun": "An evaluator did not run on the current design.",
        "invalidated": "The design changed after the result.",
        # Issue #15, D1: a solver's own reads happen in a program that
        # nopekit does not watch, so between check runs nothing can say that
        # its result is current. *Rejected:* "The design changed" (false: it
        # did not); "is not current" (nopekit does not know that either).
        "unseen": "An evaluator reads inputs that nopekit cannot see. Only a check run can "
                  "show that its result is current.",
        # Not a cause of its own: Failing `failed`, when the failing evaluator
        # is unseen (`page.bake_page`). What slipped through (review of D1):
        # "An evaluator failed the current design." sat over the row that said
        # nopekit cannot show that the same result is current.
        "failed_unseen": "An evaluator failed the design in the last check run. It reads "
                         "inputs that nopekit cannot see, and a fail counts until a check "
                         "run passes it.",
        "no-article": "No test on a built part is recorded.",
        "owned": "Its owner accepts this assumption for now.",
        "physical-pass": "A pass is recorded. It does not count for the current design.",
        "checked": "Every evaluator showed that it can fail, and each one passed on the "
                   "current design.",
        "acceptance": "An evaluator passed. Its value does not meet the acceptance "
                      "condition.",
        "outside-context": "The design is outside the range that the evaluator was tested "
                           "on.",
        "fallback": "The design is outside the range of the evaluator. A person accepts the "
                    "claim for now.",
        "contradiction": "A test on a built part failed a claim that an evaluator passed.",
        "judged-fail": "An expert judged that the claim fails.",
        "no-authority": "The claim names no expert to judge it.",
        "authority-unattributed": "The expert did not record the judgment in their own "
                                  "shell.",
        "article-moved": "The design changed after the test on the built part.",
        "claim-moved": "The claim changed after its test or its judgment.",
        "judgment-moved": "The design changed after the expert judgment.",
        "article-unjudged": "The model does not load, so the test cannot be compared with "
                            "the design.",
        "awaiting-judgment": "The claim waits for the judgment of its expert.",
        "on-article": "A test on a part built from the current design passed.",
        "judged": "The expert judged that the claim holds.",
    }),
    # What settles each cause: STE text and the commands (`page.settle`).
    "settle": MappingProxyType({
        "fix": "Change the design until {id} passes. Then run the check again.",
        "built_fail": "A test on a built part failed {id}. Change the design, build a new "
                      "part and test it again.",
        "crash": "An evaluator stopped with an error. Repair it, then run the check again.",
        "tool": "A tool that an evaluator needs is missing. Install it, then run the check "
                "again.",
        # A skip behind a prerequisite (`page.settle`): the root, never the
        # missing tool. What slipped through (P8, the motorbike demo): C1,
        # skipped because the mesh ladder of C7 FAILED, read "A tool that an
        # evaluator needs is missing. Install it" beside `nopekit doctor`, and
        # installing changes nothing when a guard failed. {first} is the claim
        # the root covers, or the root itself when it covers none.
        "prerequisite": "{first} fails first: {root} failed, so {gate} did not run. Change "
                        "the design until {first} passes. Then run the check again.",
        "prerequisite_open": "{gate} did not run, because {root} did not pass first. Settle "
                             "{first} first. Then run the check again.",
        "prerequisite_crash": "{root} stopped with an error, so {gate} did not run. Repair "
                              "{root}, then run the check again.",
        # A root nothing registers (a pack left out of `packs`, a misspelled
        # `needs`): `gate show <root>` refuses it, and `doctor` names it.
        # What slipped through (review of P8): the page gave `nopekit gate show
        # x.gone`, which exits 2 with "no gate 'x.gone'".
        "prerequisite_unregistered": "{gate} did not run, because the evaluator it needs "
                                     "first, {root}, is not registered here. Add the pack "
                                     "that has it, or correct the name. Then run the check "
                                     "again.",
        "run": "Run the check. It runs the evaluators and shows that each one can fail.",
        # Review of D1: "Run the check." promised what a check run cannot do —
        # the page reads the same after it. With a milestone that requires the
        # claim, the step is the boundary's, which runs the evaluator again.
        "unseen": "{gate} reads inputs that nopekit cannot see. Its result is from the last "
                  "check run, and this page cannot show that it is still current. Only the "
                  "output of a check run can.",
        "unseen_spend": "{gate} reads inputs that nopekit cannot see. Its result is from the "
                        "last check run, and this page cannot show that it is still current. "
                        "Before the spend, nopekit export {milestone} runs it again.",
        "outside": "The design is outside the range that the evaluator was tested on.",
        "find": "No evaluator checks {id} yet. Find one or write one.",
        "owner": "{id} is an assumption. Its owner records it in their own shell.",
        "reason": "{id} is an assumption with no reason. Write the reason in the claim file.",
        "owned": "Its owner accepts {id} for now. A test can replace the assumption.",
        "article": "{id} needs a test on a built part. Build the part, test it and record "
                   "the result in your own shell.",
        "article_free": "{id} needs a test on a built part. Build the part from the current "
                        "design, test it and record the result in your own shell. "
                        "To build it with nopekit export, name {id} in a milestone file.",
        "no_authority": "{id} needs an expert judgment, and the claim names no expert.",
        "judgment": "{id} needs a judgment from {authority}. That person records it in their "
                    "own shell.",
        "missing": "{milestone} requires {id}, and no claim file holds it.",
    }),
    "lanes": MappingProxyType({"requirements": "What was asked", "zones": "Choices",
                               "stations": "Shared values", "boards": "Claims"}),
    "kinds": MappingProxyType({"measurable": "Automated", "physical": "Test on a built part",
                               "assumption": "Assumption"}),
    "by": MappingProxyType({
        "automated": "An evaluator settles it.",
        "closed_form": "A formula settles it.",
        "solver": "A solver settles it.",
        "datasheet": "A datasheet settles it.",
        "measurement": "A measurement on a built part settles it.",
        "human": "A person settles it.",
        "none": "Nothing settles it. It is accepted for now.",
    }),
    "basis": MappingProxyType({
        "established": "An existing tool gives the answer.",
        "sourced": "The logic follows a cited outside source.",
        "model-only": "A model wrote the logic. It has no outside source.",
        "undeclared": "The evaluator does not say where its answers come from.",
    }),
    "outcome": MappingProxyType({
        "pass": "pass", "fail": "fail", "skipped": "skipped", "errored": "errored",
        "unqualified": "does not count", "outside-context": "outside its range",
        "unrun": "did not run",
        # A recorded result the judge does not count (`RESULT_TOKENS`): the
        # same words as an unqualified evaluator's, one thing said one way.
        "not-counted": "does not count",
    }),
    # The marketing page's headings are here, never literals in its script:
    # every heading either page prints is a word the build bakes and the style
    # scan reads (`story` to `whole_decision`, the two pages).
    "headings": MappingProxyType({"map": "Grounding map", "next": "Next steps",
                                  "floor": "Show the data", "technical": "Technical record",
                                  "project": "This revision", "open": "Other open claims",
                                  "rebuild": "Parts to build again",
                                  "made_with": "Made with nopekit",
                                  "story": "The story", "asked": "What it must do",
                                  "decisions": "The choices behind it",
                                  "losers": "Other values and what lost to them",
                                  "evidence": "See the evidence",
                                  "unsettled": "What is not settled",
                                  "whole_decision": "Read the whole decision"}),
    "notices": MappingProxyType({
        "prose_stale": "This text was written before the latest results. The data below "
                       "shows the results now.",
        "prose_unstamped": "This text has no stamp. It can be older than the results. The "
                           "data below shows the results now.",
        "unmatched": "No result on this page has this number.",
        "unmatched_note": "A number with this mark is not in a result on this page.",
        "build_failed": "The last build failed. This page shows the build before it.",
        "no_intro": "Add site/content/intro.md to describe the design.",
        "no_story": "Add site/content/story.md to tell the story of the design.",
        "invalidated": "Some results are older than the design. Run nopekit check, then "
                       "build the site again.",
        # Issue #15, D1: every result that is not current reads inputs that
        # nopekit cannot see. The command carries the tier that reaches them
        # (a plain `nopekit check` is tier 0 and runs no solver).
        # Review of D1: "Run nopekit check --tier 2, then build the site
        # again." gave back this same notice after both; and the notice is
        # chosen by the resolution's stale gates, not the Stale claims, so a
        # Failing claim's unseen fail reads it too (`site.state`'s
        # `meta.stale_unseen`).
        "unseen": "Some evaluators read inputs that nopekit cannot see. Their results are "
                  "from the last check run, and this page cannot show that they are still "
                  "current. Only the output of a check run can: nopekit check{tier_flag}.",
        # The marketing page's stale notice: the fact without the command. A
        # reader of that page is not the person who runs `nopekit check`, and
        # the engineering page one link away says what to run.
        "stale_public": "Some results are older than the design.",
        "unseen_public": "This page cannot show that some results are current.",
        "reads_all": "This evaluator reads every value of the design.",
        "disagree": "The status and the evidence of this claim disagree. This is a defect "
                    "in nopekit. Report it.",
    }),
    # Architecture phase 3b: the map's middle (the trace) and amber, in the
    # site's register. "Assumption", never decision 39's "estimate": one word
    # for one thing, and the baked kind word is already "Assumption".
    "trace": MappingProxyType({
        "notice_traced": "At round {round}, the model was built again with each choice "
                         "changed by 10%. A thin line joins a group of choices to each shared "
                         "value that moved.",
        "notice_traced_no_round": "The model was built again with each choice changed by "
                                  "10%. A thin line joins a group of choices to each shared "
                                  "value that moved.",
        "notice_untraced": "The links from the choices are not measured yet. Each line starts "
                           "at the values that its evaluators read.",
        "running": "The links from the choices are being measured now.",
        "skipped": "The test did not change {n} choices: {names}. The map cannot show what "
                   "they change.",
        "skipped_one": "The test did not change the choice {names}. The map cannot show what "
                       "it changes.",
        "unmoved": "The test did not see {n} shared values change: {names}. This does not "
                   "show that no choice changes them.",
        "unmoved_one": "The test did not see the shared value {names} change. This does not "
                       "show that no choice changes it.",
        "caption": "Each line is one claim. It goes through the shared values that its "
                   "evaluators read, in the order that the test found. It ends on its board.",
        "amber_legend": "Amber: passes if the assumptions hold.",
        "amber_flips": "This pass changes to a fail if an assumption is wrong by 10%.",
        # A requirement's or a board's amber (`page._group_marks`: one of its
        # claims flips). What slipped through (review of SPEC 40): the group's
        # chip carried the claim's word, so a Failing requirement read "This
        # pass changes to a fail" beside its own fail.
        "amber_group": "A pass of one of its claims changes to a fail if an assumption is "
                       "wrong by 10%.",
        "amber_holds": "This pass stays a pass if an assumption is wrong by 10%.",
        "amber_unclear": "The assumption test for this pass did not give an answer.",
        "amber_not_current": "The assumption test for this pass is out of date.",
        "amber_untraced": "The assumptions under this pass are not tested yet.",
        "amber_item": "If {choice} changes by {step} ({assumption}), {evaluator} gives "
                      "{value_text}.",
        "rides": "Rides",
        "fed_by": "Fed by",
        "upstream_stop": "The test puts this value before the values that this claim reads.",
        "upstream_rides": "Before them, from the test",
        "unseen_line": "The test did not see a change in the values that this claim reads.",
        "sets": "This assumption sets choices that change these shared values.",
        "settles": "This test on a built part settles the assumptions at these shared "
                   "values.",
        "zones_summary": "{n} choices in {z} groups. The bar shows where each number comes "
                         "from.",
        "zone_idle": "No line uses these choices.",
        "zone_unmeasured": "The test did not measure what these choices change.",
        "feeds_key": "A thin line shows which shared values a group of choices changes.",
        "sources": MappingProxyType({"chosen": "Design choice", "datasheet": "Datasheet",
                                     "estimate": "Estimate", "assumption": "Assumption",
                                     "unrecorded": "No source recorded"}),
    }),
    # The words beside evidence that is not on the page. They depend only on
    # the path: why a file inside the project was not copied (its size, a
    # directory, gone) reads one machine's disk, so the build warns with it
    # and never bakes it (review of phase 5: a fresh clone baked "not on this
    # computer" where the author's machine baked the file's size).
    "evidence": MappingProxyType({
        "outside": "Not on the page. The file is outside the project.",
        "not_copied": "Not on the page. The site build gives the reason.",
    }),
    # A bound figure's currency (issue #15, B.1.5; `page.FIGURE_CURRENCIES`):
    # one sentence per word, never a reason that reads this computer's disk
    # (why a figure is absent goes to the build's warnings), so a clone bakes
    # the same bytes. *Rejected:* "pvbatch is not on this computer" on the page
    # (the author's machine and a clone would bake two pages from one record).
    "figure": MappingProxyType({
        "current": "This picture is from the result on this page.",
        "stale": "This picture is from the result on this page. That result is not current.",
        # Issue #15, D1: the served result's evaluator reads inputs nopekit
        # cannot see; the picture is that result's, and only a check run can
        # say if it is current.
        "unseen": "This picture is from the result on this page. Its evaluator reads inputs "
                  "that nopekit cannot see, so only a check run can show that the result "
                  "is current.",
        "older": "This picture is from another result. The result on this page has no "
                 "picture yet.",
        "none": "No picture of this result is on the page.",
        # P1.4: the words beside a figure (`page.figure_row`). ``drawn`` is the
        # other result's own outcome and value, from its cache entry, beside
        # an ``older`` picture. ``shown`` and ``other`` name the result each
        # gate's pictures come from, once per gate and result. "Another", never
        # "earlier": the picture beside a design that went A, B, A is B's, the
        # later run (P1.3's first word said "an earlier result" there).
        # *Rejected:* "These pictures come from result ..." once per claim (two
        # gates' pictures under one claim would share one sentence that names
        # one result); a short entry name such as "9c1e...-3a7b..." (two entries
        # can share both ends, and the folder on disk has the whole name).
        # The evaluator's own words on a card that does not pass
        # (`page.card_reason`): the gate, then its detail. What slipped through
        # (P8, the motorbike demo): C7's card said "An evaluator failed the
        # design in the last check run" and never why, while the check log said
        # "level 2 (2674337 cells) is not converged".
        "reason": "{gate}: {text}",
        "reason_prerequisite": "{gate} did not run, because {root} did not pass first.",
        "reason_prerequisite_named": "{gate} did not run, because {root} did not pass first "
                                     "({claims}).",
        "drawn": "That other result: {outcome}, {value}.",
        "drawn_bare": "That other result: {outcome}.",
        "shown": "The pictures of {gate} come from its result {entry}. This page shows "
                 "that result.",
        "other": "The pictures of {gate} come from its result {entry}. This page shows "
                 "another result of {gate}.",
        "limit_label": "Limit {limit}",
        "turn": "Turn it in 3D",
        # Where the 3D file is, and when the site keeps it (review of P2:
        # "on this computer only" said nothing a reader could act on).
        "live": "This 3D view is on this computer only. The site keeps it after nopekit "
                "export runs this result again.",
        # O10: a renderer accepted by an environment bypass, not by the pack's
        # lock (Phase 2's test stand-in), says so beside its picture.
        "tool_unchecked": "The tool that drew this picture is not the build that the pack "
                          "locks.",
        # P1.5: the claim card's own words (`lib/figures.js`), here so that the
        # page owns no sentence and the scan reads each. ``judged`` names the
        # evaluator whose value the large line is; ``pictures`` is the
        # drawer's one line, which links to the card; ``table`` stands where a
        # chart's library did not load, above the numbers it would draw;
        # ``turn_failed`` where the 3D view did not load. What slipped through
        # (review of P1): both places printed the error's own English, a
        # sentence the page owned and no scan read.
        # *Rejected:* "from the result that this page shows" in the drawer line
        # (false beside an older picture; each figure's own currency sentence
        # says which result it is).
        "judged": "Value from {gate}",
        "others": "Other evaluators",
        "pictures": "Pictures: {n}. Show them.",
        "table": "The chart did not load. This table shows its numbers.",
        # The rows of a long series a chart's table leaves out, and a band's
        # two ends, in `page.chart_table` (review of P2).
        "table_more": "{n} more rows are not in this table.",
        "table_band": "{lo} to {hi}",
        "chart_empty": "This chart has no series. There is nothing to draw.",
        "turn_failed": "The 3D view did not load.",
    }),
    # Design P2: a figure with several colour scales (`page.SCALE_ON`) names
    # where each is drawn beside its legend's glyph (a swatch for a surface, a
    # line for lines), and a lit 3D view (`page.LIGHTS`) has a toggle to its
    # flat colours, whose pixels are the key's. Baked into the figure row only
    # where a sidecar states them (`page.figure_row`), so a one-scale page
    # bakes the bytes it did. *Rejected:* "Unlit" for the toggle (a word of
    # the renderer, not of the reader).
    "light": MappingProxyType({
        "surface": "On the surface",
        "lines": "On the lines",
        "flat": "Flat colours",
    }),
    # A rejected alternative on the marketing page (`rejection_words(register=
    # "site")`), branch for branch `HUMAN["rejection"]`'s: the type, and the
    # state of the return condition with no record text inside it (the
    # condition as written is baked apart, as record text). A preferred_against
    # rejection has no entry here: its phrase is `HUMAN["rejection"]`'s own
    # `recorded` / `not_recorded`, capitalised, so invariant 13's "preferred
    # against, not recorded by <name>" has one home. What it closes before it
    # could slip through: the CLI's words (`may_return`'s em-dash, "infeasible",
    # "dominated") baked under a word key, or a type word "preferred against"
    # beside a preference word the page could drop.
    "rejection": MappingProxyType({
        "types": MappingProxyType({
            "infeasible": "Not possible", "dominated": "Another option is better on every count",
            "unevaluated": "Not evaluated", "untyped": ""}),
        "holds": "The return condition holds: {condition}, now {observed}.",
        "does_not_hold": "The return condition does not hold: {condition}, now {observed}.",
        "cannot": "The return condition cannot be evaluated: {claim} is {status}.",
        "cannot_no_claim": "The return condition cannot be evaluated: {claim} is not a claim.",
        "cannot_no_value": "The return condition cannot be evaluated: no value of {claim} is "
                           "compared with its acceptance condition.",
        "cannot_no_margin": "The return condition cannot be evaluated: {claim} has no margin.",
        "cannot_no_limit": "The return condition cannot be evaluated: {claim} states no limit.",
        "cannot_band": "The return condition cannot be evaluated: the acceptance condition of "
                       "{claim} is a band.",
        "text": "A person evaluates the return condition.",
        # Baked counts, so the browser does no plural arithmetic.
        "lost_one": "1 option lost",
        "lost": "{n} options lost",
        "more_one": "1 more decision",
        "more": "{n} more decisions",
    }),
    # A recorded result in the drawer (`result_facts(register="site")`): its
    # heading, who recorded it, and why it does not count, in the branches of
    # the CLI's `HUMAN["physical"]`, `["not_counted"]` and `["recorded"]`.
    # What slipped through (follow-up after phase 3b): the page baked the
    # CLI's own words here ("the result on article 3f2a… was recorded on a
    # design that has since moved — a new article is needed, not a rerun"),
    # exempt from the style scan as a shared producer.
    "result": MappingProxyType({
        "heading_test": "Test on a built part",
        "heading_judgment": "Expert judgment",
        "recorded": "Recorded by {who}.",
        "recorded_agent": "Recorded by {who} from an agent session.",
        "recorded_pipe": "Recorded by {who} from a pipe or a script.",
        "recorded_none": "No person is named for this result.",
        "superseded": "A pass on part {article} replaces this fail, because the design "
                      "changed. The fail counts again if the design changes back.",
        "moved": "The design changed after part {article} was built. These values "
                 "changed: {moves}. Build a new part and test it.",
        "moved_bare": "The design changed after part {article} was built. Build a new part "
                      "and test it.",
        "claim_moved": "The claim changed after the test on part {article}. Test it again.",
        "unjudged": "The model does not load, so part {article} cannot be compared with the "
                    "design.",
        "judgment_moved": "{authority} judged an earlier design. {authority} judges it "
                          "again.",
        "judgment_claim_moved": "The claim changed after {authority} judged it. {authority} "
                                "judges it again.",
        "judgment_unjudged": "The model does not load, so the judgment of {authority} cannot "
                             "be compared with the design.",
        "not_counted": MappingProxyType({
            "agent-session": "A pass recorded from an agent session does not count.",
            "non-interactive": "A pass recorded from a pipe or a script does not count. The "
                               "person who did the test records it in their own shell.",
            "legacy": "A pass recorded before results were tied to a built part does not "
                      "count. Record it again in your own shell.",
            "who": "A pass that names no person does not count.",
            "measured": "A pass at a value that does not meet the acceptance condition does "
                        "not count.",
            "evidence": "The evidence file {path} changed or is missing since the pass was "
                        "recorded. The pass does not count. Record it again.",
            "beside": "An evaluator settles this claim, so a pass on a built part settles "
                      "nothing here.",
            "none": "A pass does not settle an assumption. Its owner records it.",
            "authority": "A judgment that {authority} did not record does not count.",
            "export-missing": "No export record holds the built part of this pass, so the "
                              "pass does not count. Restore the export record from git, or "
                              "record the result again.",
        }),
        "judgment_not_counted": MappingProxyType({
            "agent-session": "A judgment recorded from an agent session does not count.",
            "non-interactive": "A judgment recorded from a pipe or a script does not count.",
            "legacy": "A judgment recorded before results were tied to the design does not "
                      "count.",
            "who": "A judgment that names no person does not count.",
            "authority": "A judgment that {authority} did not record does not count.",
            "evidence": "The evidence file {path} changed since the judgment was recorded. "
                        "The judgment does not count. {authority} records it again.",
            "measured": "A judgment at a value that does not meet the acceptance condition "
                        "does not count.",
        }),
    }),
})


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
        ClaimCause.PREREQUISITE_ERRORED: "errored",
        ClaimCause.SKIPPED: "skipped",
        ClaimCause.PREREQUISITE: "skipped",
        ClaimCause.UNQUALIFIED: "unqualified",
        ClaimCause.NO_EVALUATOR: "no evaluator",
        ClaimCause.NO_OWNER: "no owner recorded",
        ClaimCause.OWNER_UNATTRIBUTED: "owner {owner} is named in claims/{id}.json and "
                                       "has not recorded it — {owner} records it in their "
                                       "own shell: nopekit claim physical {id} assume",
        ClaimCause.NO_REASON: "no reason recorded",
        ClaimCause.UNRUN: "unrun",
        ClaimCause.INVALIDATED: "invalidated",
        # Issue #15, D1: not "invalidated" — nothing moved that nopekit sees.
        ClaimCause.UNSEEN: "unseen inputs",
        ClaimCause.NO_ARTICLE: "needs an article",
        ClaimCause.OWNED: "assumed by {owner}",
        ClaimCause.PHYSICAL_PASS: "a pass {recorded}, not bound to an article",
        ClaimCause.CHECKED: "—",
        # P2.4: a value outside the claim's condition (D9), a pass outside its
        # evaluator's operating context (D15), and one an owned fallback carries.
        ClaimCause.ACCEPTANCE: "acceptance condition not met",
        ClaimCause.OUTSIDE_CONTEXT: "outside operating context",
        ClaimCause.FALLBACK: "assumed by {owner} outside operating context",
        # P2.5a (its D18, D19): the physical path and expert judgment. No
        # *signed*, *confirmed* or *verified* (GLOSSARY Never-says), and the
        # console is "their own shell" — never "a terminal", GLOSSARY §6's
        # *terminal* being where evidence bottoms out.
        ClaimCause.CONTRADICTION: "contradiction",
        ClaimCause.JUDGED_FAIL: "judged failing by {authority}",
        ClaimCause.NO_AUTHORITY: "no authority named",
        ClaimCause.AUTHORITY_UNATTRIBUTED: "awaits {authority}: named in claims/{id}.json, "
                                           "not recorded by them",
        ClaimCause.ARTICLE_MOVED: "invalidated",
        ClaimCause.CLAIM_MOVED: "{id} changed since article {article} was tested",
        ClaimCause.JUDGMENT_MOVED: "{authority} judged it on other inputs",
        ClaimCause.ARTICLE_UNJUDGED: "article {article} cannot be judged: the model does "
                                     "not load",
        ClaimCause.AWAITING_JUDGMENT: "awaits {authority}'s judgment",
        ClaimCause.ON_ARTICLE: "checked on article {article}",
        ClaimCause.JUDGED: "judged by {authority}",
    }),
    # Where a claim's evidence bottoms out, as every human channel prints it
    # (P2.5a-D1, D20; GLOSSARY §1 *terminal*). In P2.5a every automated claim
    # prints *automated*, declared or not: a declared closed_form, solver or
    # datasheet is validated and carried in JSON, but no human line prints
    # "simulation" over evidence nobody has judged to be one — P2.5b's
    # `terminal-unmet` judges the declaration, then its word prints (critique
    # 14 of the P2.5a design: closed-form calculation, simulation and datasheet
    # are P2.5b's). `none` prints *assumption*, the display word GLOSSARY §1
    # gives an assumption's terminal (added with P2.5a: GLOSSARY's "a claim
    # with no terminal is a Gap" is said by its status, Gap until an owner
    # records it).
    "terminal": MappingProxyType({
        "measurement": "measurement", "human": "expert judgment: {authority}",
        "human_unnamed": "expert judgment", "none": "assumption", "automated": "automated"}),
    # Why a pass does not count (P2.5a-D11), by `EntryStanding.why`. A
    # judgment's own forms where the claim ends in expert judgment.
    "not_counted": MappingProxyType({
        "agent-session": "a pass recorded from an agent session does not count",
        "non-interactive": "a pass recorded from a pipe or a script does not count — the "
                           "person who tested it records it in their own shell",
        "legacy": "a pass recorded before results were bound to articles does not count — "
                  "record it again in your own shell",
        "who": "a pass recorded by no one does not count",
        "measured": "a pass at a value its acceptance condition does not admit does not "
                    "count",
        "evidence": "a pass whose evidence {path} changed or went missing since it was "
                    "recorded does not count — record it again",
        "beside": "a physical pass beside the automated evaluator settles nothing",
        "none": "nothing settles an assumption with a pass",
        "authority": "a judgment recorded by anyone but {authority} does not count",
        # P2.5b-D15: a pass on an exported article counts only while the export
        # that built it is on record — an export removed to escape a rebuild
        # prediction leaves the pass counting for nothing.
        "export-missing": "a pass on an exported article no record in exports/ holds does "
                          "not count — restore the export record from git, or record the "
                          "result again on an article an export holds",
    }),
    "judgment_not_counted": MappingProxyType({
        "agent-session": "a judgment recorded from an agent session does not count",
        "non-interactive": "a judgment recorded from a pipe or a script does not count",
        "legacy": "a judgment recorded before results were bound does not count",
        "who": "a judgment recorded by no one does not count",
        "authority": "a judgment recorded by anyone but {authority} does not count",
        "evidence": "a judgment whose evidence {path} changed since it was recorded does "
                    "not count — {authority} records it again",
        "measured": "a judgment at a value its acceptance condition does not admit does "
                    "not count",
    }),
    # `claim physical`'s own lines (P2.5a §2, the R-11 targets): the prompt a
    # person reads before typing the claim's id, the recorded lines, the help,
    # and every refusal — each naming the way, never only the wall.
    "signing": MappingProxyType({
        "help": "record a physical result, or an owner or authority, in your own shell",
        "act_help": "pass or fail — a physical result; assume — the owner (or, for an "
                    "expert-judgment claim, its authority) accepting the claim",
        "detail_help": "what was observed (say when it was observed, too)",
        "evidence_help": "a photo, log or measurement file under the project (repeatable; a "
                         "pass on a physical claim needs one)",
        "measured_help": "the value measured, in the claim's acceptance units — it decides "
                         "pass or fail where the claim has a limit",
        "authority_help": "the authority the claim file names, typed as a confirmation (an "
                          "expert-judgment claim)",
        "article_help": "the exported article it was measured on: at least 12 hex of its "
                        "hash, as `nopekit export` prints it",
        # The flags' help (review of P2.5a: "(what the report prints)" stopped
        # being true when the report moved to the positional `pass|fail`).
        "pass_help": "same as the positional `pass`",
        "fail_help": "same as the positional `fail`",
        # A positional act and a flag that disagree, and `assume` with an
        # outcome's flag (review of P2.5a: `fail --pass` recorded and counted a
        # PASS, reported in --json as act "fail"; `assume --fail` recorded an
        # attribution).
        "act_disagrees": "you typed {act} and {flag} — say one outcome. Nothing was written.",
        "assume_flags": "assume records an owner or an authority accepting {id}; it takes no "
                        "{flag}. Nothing was written.",
        "meets": "meets",
        "fails": "fails",
        # Which agent marker made this shell an agent session (review of P2.5a:
        # a person's own exported Claude Code setting made their shell read as an
        # agent's, and nothing said which variable did it).
        "marker": " ({marker} is set in this shell, as in an agent session)",
        "who": "--who is not accepted: who recorded a result is read from git's identity "
               "(git config user.name and user.email), never typed. Nothing was written.",
        "when": "--when is not accepted: a result is dated when it is recorded — say when "
                "it was observed in --detail. Nothing was written.",
        "no_identity": "no git identity here — set git config user.name and user.email, "
                       "then record it again. Nothing was written.",
        "no_act": "say what happened: nopekit claim physical {id} pass|fail (or assume), "
                  "with --detail saying what was observed. Nothing was written.",
        "typed": "you typed {typed}, not {id} — nothing was recorded",
        "assume_channel": "assume records a person accepting {id}; it is typed in their own "
                          "shell, never from an agent session or a script{why}. Nothing was "
                          "written. Ask {name} to run: nopekit claim physical {id} assume{flag}",
        "assume_pipe": " (stdin is not a terminal)",
        "assume_nothing": "{id} has nothing to assume: an owner records an assumption or a "
                          "fallback (\"owner\" and \"fallback\" in claims/{id}.json), an "
                          "authority an expert-judgment claim. Nothing was written.",
        "no_owner": "{id} names no owner — write \"owner\" in claims/{id}.json, then the "
                    "owner records it. Nothing was written.",
        "no_reason": "{id} gives no reason to carry it — write its {field} in claims/{id}.json "
                     "first. Nothing was written.",
        "not_owner": "{id}'s {role} is {name!r} in claims/{id}.json, and this shell's git "
                     "identity is {who!r} — only the {role} records it: run it in their "
                     "shell, or name the {role} as git names them. Nothing was written.",
        "no_authority": "{id} ends in expert judgment and names no authority — write "
                        "\"authority\" in claims/{id}.json. Nothing was written.",
        "authority_flag": "{id} is settled by its authority's judgment: add --authority "
                          "\"{authority}\" to confirm whose. Nothing was written.",
        "authority_other": "--authority {given!r} is not {id}'s authority ({authority!r}, in "
                           "claims/{id}.json). Nothing was written.",
        "authority_not_here": "--authority is for a claim that ends in expert judgment, and "
                              "{id} does not. Nothing was written.",
        "none_pass": "nothing settles an assumption with a pass — its owner records it: "
                     "nopekit claim physical {id} assume. Nothing was written.",
        "no_test": "{id} has no test written down — a pass needs an acceptance condition or a "
                   "mechanism saying what was done (\"mechanism\": {{\"how\": …}} in "
                   "claims/{id}.json). Nothing was written.",
        "no_evidence": "a pass on {id} needs --evidence <file>: a photo, a log or a "
                       "measurement file under the project. Nothing was written.",
        "bad_evidence": "--evidence {path}: {why}. Nothing was written.",
        "not_text": "{flag} holds bytes that are not text (not UTF-8) — type it again as "
                    "text. Nothing was written.",
        "no_model": "the model does not load, so there is no article to bind a pass to: "
                    "{error}. Nothing was written.",
        "unread": "{gates} never ran here, so the files the design's evaluators read are "
                  "not known and the article would leave them out — run nopekit check, "
                  "then record the pass on {id}. Nothing was written.",
        "measured_bad": "--measured {value} is not a finite number. Nothing was written.",
        "measured_disagrees": "--measured {value} {verdict} {condition}, and you typed "
                              "{act}. Nothing was written.",
        "measured_no_limit": "{id} has no limit, so --measured {value} decides nothing: "
                             "say pass or fail. Nothing was written.",
        "prompt_status": "{terminal} · {required} · reads now: {status}",
        "required": "required",
        "not_required": "not required",
        "you_record": "you record: {act} — {detail}",
        "no_detail": "no detail",
        "you_record_measured": "you record: {act} — {measured} against {condition}",
        "article": "on article {article}: the design as the model holds it now ({values})",
        # P2.5b-D13: a result bound to an exported article, and `--article`'s
        # refusals — each naming the way.
        "article_exported": "article {article} — exported for {milestone} by {who}, {when} "
                            "(revision {revision})",
        "article_short": "--article takes at least 12 hex of an exported article's hash, as "
                         "`nopekit export` prints it. Nothing was written.",
        "article_unknown": "no export recorded article {article} — `nopekit export` lists "
                           "each milestone's last export. Nothing was written.",
        "article_ambiguous": "--article {article} names two articles: {articles} — give more "
                             "of the hash. Nothing was written.",
        "no_article": "no article: the model does not load ({error})",
        "revision": "revision {revision}",
        "dirty": ", with uncommitted changes to model/",
        "contradicts": "this contradicts {gate}, which passed {id} at {value} (version {code})",
        "assume_row": "you record: {name} as {id}'s {role} — {reason}",
        "recorded_by": "recorded by: {who}",
        "type": "type {id} to record it (anything else records nothing): ",
        "recorded": "recorded {id} {act} in results/{id}.json (entry {n})",
        "recorded_assume": "recorded {id}'s {role} {name} in results/{id}.json "
                           "(attribution {n})",
        "agent_pass": " — from an agent session: a pass recorded here does not count; the "
                      "person who tested it records it in their own shell{marker}",
        "agent_fail": " — from an agent session: a fail counts wherever it is recorded"
                      "{marker}",
        "pipe_pass": " — from a pipe or a script: a pass recorded here does not count; the "
                     "person who tested it records it in their own shell",
        "pipe_fail": " — from a pipe or a script: a fail counts wherever it is recorded",
        "beside": " — beside an automated evaluator, a physical pass settles nothing; a "
                  "fail would",
    }),
    # The physical path's own sentences (P2.5a §2, the R-11 targets).
    "physical": MappingProxyType({
        "failed_on": "failed on article {article}",
        "failed": "failed on an article",
        "moved_tail": "the result on article {article} was recorded on a design that has "
                      "since moved: {moves} — a new article is needed, not a rerun",
        "fail_moved": "(invalidated: article {article}'s design moved: {moves} — a new "
                      "article is needed)",
        "claim_moved_tail": "test it again",
        "judgment_moved_tail": "{moves} — {authority} judges it again",
        "unjudged_tail": "fix the model; the result is judged again on the next read",
        "had_passed": "{gate} had passed it at {value}",
        "awaiting_tail": "{rationale}",
        "authority_act": "{authority} records it in their own shell: nopekit claim "
                         "physical {id} assume --authority \"{authority}\"",
        "no_authority_full": "an expert-judgment claim names the person or institution it "
                             "stays with: \"authority\" in claims/{id}.json",
        "beside": "a physical pass beside the automated evaluator, recorded {when}: it "
                  "settles nothing an evaluator settles",
        "record": "`nopekit claim physical {id} pass|fail --evidence <file> --detail "
                  "\"...\"` — run by the person who tested it, in their own shell",
        "record_article": "`nopekit claim physical {id} pass|fail --article {article} "
                          "--evidence <file> --detail \"...\"` — run by the person who "
                          "tested it, in their own shell",
        "rebuild": "rebuild: article {article} ({claims}) — {moves}",
        "rebuild_heading": "### Articles to rebuild",
        "rebuild_intro": "A result counts on the article it was recorded on, and the design "
                         "these were recorded on has moved. Each needs a new article — a "
                         "check run cannot restore it:",
        "rebuild_row": "- article `{article}` ({claims}) — {moves}",
        "contradicts": "contradicts: {gate} (version {code}) — on its track record",
        "contradiction_row": "- **Contradiction:** {gate} (version {code}) had passed it at "
                             "{value}; the physical result {measured}",
        "track": "track record: {now} at this version, {earlier} at earlier versions",
        "track_one": "{claim} on article {article} ({when}, {recorded}: {measured} where it "
                     "gave {value})",
        "track_none": "no contradiction recorded",
        "counts": "it counts",
        "does_not_count": "it does not count: {why}",
        "contradiction_of": "a contradiction of {gate} (version {code}), which gave {value}",
        "checked_article": "Checked on an article",
        # P2.5b-D14 (PLAN Q2.11): a fail a pass on another exported article
        # superseded, named on the claim's row — kept on the evaluator's track
        # record, and counting again the moment the design returns to it.
        "superseded": "(the fail on article {article} is superseded: the design is no "
                      "longer the one that failed, and it counts again if it returns)",
        # The superseded fail's own row (`result_facts`): why it does not count
        # now, naming the pass that superseded it.
        "superseded_by": "superseded by the pass on article {article}: the design is no "
                         "longer the one that failed, and it counts again if it returns",
        "checked_judgment": "Checked by expert judgment",
        # An expert judgment in its own words (review of P2.5a): it read as an
        # article test — "C8 changed since article … was tested — test it
        # again", "a new article is needed", a `PHYSICAL RESULTS` block, "A
        # physical result was recorded" — where GLOSSARY §1's physical result
        # is a physical evaluator's verdict on one article, `contradicted_by`
        # says "an expert judgment is not a physical result", and D18 gives a
        # judgment its own words ("<authority> judges it again").
        "failed_judgment": "a fail was recorded",
        "judgment_claim_moved": "{id} changed since {authority} judged it — {authority} "
                                "judges it again",
        "judgment_unjudged": "{authority}'s judgment cannot be compared with the design: "
                             "the model does not load",
        "judgment_unjudged_tail": "fix the model; the judgment is compared again on the next "
                                  "read",
        "results_head": "PHYSICAL RESULTS ({n}, oldest first)",
        "judgments_head": "JUDGMENTS ({n}, oldest first)",
        "result_recorded": "A physical result was recorded:",
        "judgment_recorded": "A judgment was recorded:",
        "failed_row": "- Physical result: failed {when}{on}, {recorded} — {detail}",
        "judgment_failed_row": "- Judgment recorded: fail, {when}, {recorded} — {detail}",
        # A results file no claim file holds (`Ledger.removed`, review of P2.5a:
        # a claim file renamed away from its sealed fail stopped failing).
        "removed_tail": "— no claims/{id}.json holds it: restore claims/{id}.json — a result "
                        "is sealed to its claim's id, and a fail counts across every edit",
        "removed_tag": "its claim file removed",
        # `doctor`'s results rows (P2.5a-D21; review of P2.5a: a fail's changed
        # photo was told "its pass does not count").
        "doctor_evidence": "evidence {id}: {path} {how} since it was recorded — {then}",
        "doctor_evidence_pass": "its pass does not count until recorded again",
        "doctor_evidence_fail": "the fail still counts (a fail counts whatever happens to its "
                                "evidence); the file no longer shows what was recorded",
        "doctor_removed": "results/{id}.json holds {n} fail(s) on {id} — the latest: {detail} — "
                          "and no claims/{id}.json holds that claim: the fail still counts and "
                          "stops check. Restore claims/{id}.json: a result is sealed to its "
                          "claim's id, so a renamed claim leaves its results behind",
        "doctor_removed_passes": "results/{id}.json holds results on {id}, and no "
                                 "claims/{id}.json holds that claim — its passes settle nothing",
        "unbound": "{ids} {has} a pass that does not count",
        # Each Stale cause's advice in the failing section (critique 9 of the
        # P2.5a design: a moved article was told `nopekit check` re-runs what
        # moved, which a check run cannot do).
        "stale_advice": MappingProxyType({
            # `{check}` is `nopekit check` with the tier that reaches the gates.
            "invalidated": "`{check}` re-runs what moved.",
            "unseen": "Only a check run can show that it is current: `{check}`.",
            "article-moved": "A new article is needed — a check run cannot restore it.",
            "claim-moved": "Test it again on an article — a check run cannot restore it.",
            "judgment-moved": "{authority} judges it again — a check run cannot restore it.",
            "article-unjudged": "Fix the model so the article can be judged.",
            "judgment-unjudged": "Fix the model so {authority}'s judgment can be compared "
                                 "with the design.",
        }),
    }),
    # An evaluator's operating context (P2.4, GLOSSARY §2): the words after
    # `outside operating context: <evaluator> : `, by the breach's kind; the
    # declaration as `gate show` prints it; `check`'s and `status`'s tally word;
    # and the long reason's hints — split by what the claim file already says
    # (critique 12 of the design: an owner who had named themselves and a
    # fallback was told to go and name them).
    "context": MappingProxyType({
        "outside": "{key} = {value}, qualified on {interval}",
        "absent": "{key} absent, qualified on {interval}",
        "not-a-number": "{key} = {value} (not a number), qualified on {interval}",
        "unread": "{key} not read by the run that passed, qualified on {interval}",
        "declared": "operating context: {ranges} — outside it a pass does not count; a fail "
                    "still does",
        # The report's subsection under Gaps (review of P2.4: it was a literal
        # beside this table, a second copy of `declared`'s words).
        "gaps_intro": "An evaluator was qualified on a range of its inputs; outside it a "
                      "{pass_} does not count, so its claim is a {gap} — and a {fail} outside "
                      "it still does.",
        "range": "{key} in {interval}",
        "tally": "outside operating context",
        # The page's chip title for such a pass (`page_phrases`).
        "hint": "the evaluator passed on inputs outside the range it was qualified on — "
                "the pass does not count; a fail there would",
        # P2.5a: the act that records the owner now exists (`assume`).
        "fallback_hint": "an owned assumption would carry it as Assumed: name its owner and a "
                         "fallback reason in claims/{id}.json (owner, fallback), and the owner "
                         "records it in their own shell: nopekit claim physical {id} assume",
        "fallback_unattributed": "{owner} is named with a fallback in claims/{id}.json and "
                                 "has not recorded it — {owner} records it in their own shell: "
                                 "nopekit claim physical {id} assume",
    }),
    # Issue #15, D1 ("own word, keep stale"): an evaluator whose entry read
    # through a channel nopekit cannot see — a solver's own reads in a
    # subprocess — is never current outside the check run that ran it. Each
    # reader says so in these words, never "the design changed". The channel
    # list itself (often 2 KB of paths) is `doctor`'s `opaque-inputs` row;
    # every other reader prints a count and one example. *Rejected:* the list
    # in full on `status` and `site build` (the line the eye needs was below
    # it); hiding it entirely (a person who asks "which files?" gets no
    # answer).
    "unseen": MappingProxyType({
        "only": "only a check run can show that the result is current",
        # Review of D1: the line said "Only a check run can show that their
        # results are current: nopekit check --tier 2", and a person who ran
        # it got this same line back, every time — no reader after a check run
        # can say more. It says where the results are from, what can show them
        # current (a check run's own output), and, with a milestone, the
        # command that runs them again where it counts (`export`).
        "status": "unseen inputs: {shown} — {n} {evaluators} {read} inputs that nopekit "
                  "cannot see ({k} in all, for example {example}; the full list: "
                  "nopekit doctor). {Their} {results} {are} from the last check run, and no "
                  "reader after it can show that {they} {are} still current. Only the "
                  "output of a check run can: nopekit check{tier_flag}{spend}",
        # `status`'s clause when the project has a milestone: the boundary that
        # spends re-runs every evaluator a required claim rests on (invariant 12).
        "spend": "; before a spend, nopekit export <milestone> runs them again",
        # A fail's suffix (`reason`): the fail still counts (D-08).
        "failed": "from the last check run: nopekit cannot see all of its inputs, and a fail "
                  "counts until a check run passes it",
        # `gate show` and `doctor`, for an evaluator whose controls read
        # through such a channel (`verdicts._control_moved`: "an opaque control
        # is never current"). What slipped through (review of D1): both said
        # "not yet qualified at this version — the next check run qualifies
        # it" right after a check run had, and after every check run since.
        "qualification": "qualified at this version in the check run that ran its "
                         "controls; they read inputs that nopekit cannot see, so between "
                         "check runs no reader can show that this qualification is current",
        "doctor_q": "{n} {evaluators} qualified at {their} version in the check run that ran "
                    "{their} controls; the controls read inputs that nopekit cannot see, so "
                    "between check runs no reader can show that the qualification is "
                    "current: {rows}",
        # `meta.stale_reason`'s clause for them (`site._stale_reason`), which
        # `site build`, `site status`, `doctor` and the technical page print.
        "meta": "{shown} {read} inputs that nopekit cannot see ({k} in all, for example "
                "{example}), so only a check run can show that {their} {results} {are} current",
        "ready": "note: this ready holds for this check run only. {n} {evaluators} that it "
                 "rests on {read} inputs that nopekit cannot see ({shown}), so status, the "
                 "report and the site read {their} claims stale: only a check run can show "
                 "that {their} {results} {are} current",
        "doctor": "{n} {evaluators} {read} inputs that nopekit cannot see, so {their} "
                  "claims read stale between check runs, and only a check run can show "
                  "that {their} {results} {are} current: {rows}",
    }),
    # The claim comparison and the goalposts (P2.4-D9-D13): the Failing
    # reason's body, the checked table's columns (GLOSSARY §9: the value column
    # is *Value*, the evaluator column *Evaluator* — critique 13 of the design:
    # `Gate`, a Never-say, beside the word it moved), the not-compared note and
    # the limit-disagreement warning.
    "acceptance": MappingProxyType({
        # "its limit when it ran", not "its own limit" (review of issue #15's
        # D2): a gate that reads the claim's condition (`ctx.acceptance`) has
        # no limit of its own, and after the claim's limit moved the verdict's
        # is the claim's old one — "its own limit 5 Pa" told the reader the
        # evaluator kept a second limit, the S-35 slip D2 removed.
        "reason": "{gate} : {value} against {claim}'s {condition} (its limit when it ran: "
                  "{limit})",
        "reason_no_limit": "{gate} : {value} against {claim}'s {condition}",
        "columns": ("Claim", "Acceptance", "Value", "Evaluator", "Evidence"),
        "no_value": "(no value compared with it)",
        "not_compared_head": "Not compared with its claim's acceptance condition — each of "
                             "these values is judged against its evaluator's own limit only:",
        "not_compared_row": "- **{claim}** `{gate}` : {value} ({settles}, not {quantity})",
        "not_compared_units": "- **{claim}** `{gate}` : {value} (in {units}, not {wanted})",
        "limits": "{gate} : its limit {limit} is not {claim}'s acceptance condition "
                  "({condition}) — one number in two places",
        "warning": "warning: {line}",
        # The checked table's closing sentence: what every row stands on beyond
        # "ran and passed" (P2.4), and what keeps a claim out of it.
        "closing_counts": "inside its operating context, and every value compared with the "
                          "claim's acceptance condition meets it",
        "closing_more": "or one outside its operating context, or a value its claim's "
                        "acceptance condition does not admit",
        "doctor_ok": "every compared evaluator judges against its claim's own limit",
        # `why`'s acceptance line with none written (review of P2.5a: it said
        # "NONE — without a threshold this is a wish, not a claim" for a physical
        # claim whose note is its written test, which P2.5a-D9 accepts, and for an
        # expert judgment, which takes none by design; *threshold* is the
        # acceptance condition's Never-say).
        "why_none": "acceptance: no acceptance condition",
        "why_note": "acceptance: none — the test is its mechanism",
        "why_judgment": "acceptance: none — settled only by {authority}'s judgment",
        "doctor_warn": "{n} evaluator limit(s) part from their claim's: {list}",
        # The limits row's clause for each covering evaluator it compared no
        # limit of: its result is not current here (`cli._limits_left_out`).
        # What slipped through (holdout, general packs, round 2, D06): the row
        # read "ok" over an evaluator that reads inputs nopekit cannot see,
        # which no reader after a check run calls current, so the row never
        # compared it and said nothing of it. `check` compares it in its own
        # run, and warns there.
        # The command unquoted, as `unseen`'s "nopekit check{tier_flag}": a code
        # span is a command `test_docs_commands` parses, and a template's
        # placeholder in one reads as part of the subcommand.
        "doctor_left_out": "not compared: {list} — nopekit check{tier_flag} compares {them}, "
                           "and warns for each limit that parts",
        "left_out_unseen": "{gate} (it reads inputs that nopekit cannot see)",
        "left_out_stale": "{gate} (its result is stale)",
    }),
    # Who entered a physical result (GLOSSARY §1, *recorded by*): named, or the
    # unattributed form. What slipped through (review of P2.1): one template for
    # both, filled with the word "unattributed" — "recorded by unattributed".
    "recorded": MappingProxyType({"named": "recorded by {who}",
                                  "unattributed": "recorded, unattributed",
                                  # P2.5a: the channel beside the name (D18).
                                  "agent": "recorded by {who}, from an agent session",
                                  "non_interactive": "recorded by {who}, from a pipe or a "
                                                     "script"}),
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
        "skipped": "nothing was evaluated: its tool is missing here, it skipped itself on "
                   "its input, or a prerequisite is not established",
        "error": "the evaluator crashed — nothing was evaluated, and the evaluator itself "
                 "is broken",
    }),
    "outcome_tag": MappingProxyType(_RENDER_TAG),
    # A claim Skipped behind a prerequisite (P2.2-D10, D11): the phrase after
    # `skipped: <evaluator> : ` — `prerequisite failed: <root>` only when the
    # root failed (D-03), `prerequisite not established: <root> (<kind>)`
    # otherwise — and the kind word with no glossary term. The other kind words
    # are the outcome words above (errored, skipped) and the lead `unqualified`.
    # What slipped through the design (critique): the words lived in `gates`,
    # a second outcome-word table no sentinel reached; the spine keeps its own
    # spelling for the gate channel (`gates.PREREQUISITE_FAILED`, a skip reason)
    # and this table words the claim channel.
    "prerequisite": MappingProxyType({
        "failed": "prerequisite failed: {root}",
        "not-established": "prerequisite not established: {root} ({kind})",
        "not-registered": "not registered",
    }),
    # The report's section headings (GLOSSARY §9), SECTION_PROVEN's text excepted:
    # it changes only with METHOD's (A-11, PLAN D-14).
    "heading": MappingProxyType({
        "pending_build": "## Pending build",
        "gaps": "## Gaps",
        "assumed": "## Assumed",
        "failing": "## Failing, stale, skipped or open",
        "reproduce": "## Reproduce",
        "gaps_context": "### Outside an evaluator's operating context",
        "requirements": "## Requirements",
    }),
    # A milestone's one line (P2.5b-D18; critique 20 of its design: one shape
    # everywhere — the `export` list, REPORT.md, `export <m>`), the list's head,
    # and REPORT.md's: *ready* as last evaluated, from the verdicts on record
    # (critique 17: the boundary's is `export <m> --dry-run`'s). GLOSSARY §4:
    # *milestone*, *required claim*, *ready*; no *blocking*, *non-critical*.
    "milestone": MappingProxyType({
        "line": "{name}: {k} of {n} required {claims} {checked} · {s} {stale}{stale_ids}"
                "{rest}",
        "nothing": "{name}: requires no claim, so nothing can be ready for it — list the "
                   "claims it needs in milestones/{name}.json (\"requires\")",
        "missing": "{n} with no claim file ({ids})",
        "last_export": " · last export {article} by {who}, {when}",
        "list_head": "{n} {milestones}, as last evaluated — `nopekit export <milestone> "
                     "--dry-run` re-runs what each requires",
        "none": "no milestone declared — a milestone is milestones/<name>.json: "
                "{\"description\": \"…\", \"requires\": [\"C1\", …]}",
        "report_head": "Milestones, as last evaluated — `nopekit export <milestone> "
                       "--dry-run` re-runs what each requires:",
    }),
    # The readiness sentence's milestone forms and its hardware clause, in every
    # branch (W13; P2.5a's hand-off), and the limits paragraph (M18.1: "Say so,
    # in the report, every time"). GLOSSARY §9: no *unverified in physical
    # hardware* — Pending build's Never-say.
    "readiness": MappingProxyType({
        # The boundary's (`export` over its re-run, and the package it writes):
        # said plainly. Every other reader reads the cache, and says *ready* as
        # last evaluated (invariant 12; review of P2.5b: `report --milestone`
        # said the boundary's words over a forged cache).
        "ready_for": "{rev} is ready for {m}: every claim it requires is checked against "
                     "the current inputs.",
        "not_ready_for": "{rev} is NOT ready for {m}: {k} of {n} required {claims} {is_} "
                         "unresolved — {groups}.",
        "ready_for_last": "{rev} is ready for {m}, as last evaluated: every claim it "
                          "requires is checked against the current inputs.",
        "not_ready_for_last": "{rev} is NOT ready for {m}, as last evaluated: {k} of {n} "
                              "required {claims} {is_} unresolved — {groups}.",
        "ready_last": "{rev} is ready, as last evaluated: every required claim is {checked} "
                      "against the current inputs.",
        "requires_nothing": "{rev} is NOT ready for {m}: {m} requires no claim, so nothing "
                            "can be ready for it — list the claims it needs in "
                            "milestones/{m}.json (\"requires\").",
        "missing": "{n} with no claim file ({ids})",
        "rebuild": "Rebuild article {article} ({claims}): {moves}.",
        # The status word comes from its row, as every channel's does (D-16):
        # `{Checked}` the term, `{checked}` the word. "on any article", so the
        # clause never reads as P2.1's "checked on an article" said of a pass
        # that does not count (`ReadyIsThePredicate`'s rule).
        "on_article": "{Checked} on an article: {n} ({ids}).",
        "none_on_article": "No claim is {checked} on any article.",
        # Its evaluators "have shown they can fail": qualification's own words
        # are its table's alone (`HUMAN["qualification"]`).
        "limits": "{Checked} does not mean true: its evaluators have shown they can fail, "
                  "not that they are right or enough, and nothing outside the {n} "
                  "{claims} here has been evaluated.",
        "title_for": "{name} — readiness for {m} ({rev})",
        "as_last_evaluated": "Claims {m} does not require are shown as last evaluated: "
                             "`nopekit export {m}` re-runs only the evaluators of the {n} "
                             "it requires.",
        # `report --milestone` (the cache's): nothing was re-run, the required
        # claims included — never the package's line above, which implies they
        # were (review of P2.5b, finding 3).
        "nothing_rerun": "Nothing here was re-run: every claim is shown as last evaluated — "
                         "`nopekit export {m} --dry-run` re-runs the evaluators of the {n} "
                         "it requires, at the boundary.",
    }),
    # A physical claim's latency (W3; GLOSSARY §5: "declared ahead only for a
    # physical evaluator, until an article measures it") — never *duration*,
    # *run time* or *turnaround*.
    "latency": MappingProxyType({
        "declared": "expected {declared} (declared)",
        "measured": "measured {measured} on article {article} (expected {declared})",
        "measured_bare": "measured {measured} on article {article}",
        "none": "no expected latency declared",
        # `why`'s and `claim show`'s row (P2.5b-D19), only where one is declared
        # or measured: an empty row on every physical claim says nothing.
        "row": "latency: {words}",
    }),
    # `export` (P2.5b §2): every line it prints. A boundary *refuses*
    # (GLOSSARY §6); a disagreement is "two outcomes" — the words the filed
    # re-run reads as everywhere after (critique 14 of the design: *disagree*
    # is the status-and-evidence defect's word).
    "export": MappingProxyType({
        "head": "{name} — {description}",
        "rerun": "re-run: {n} {evaluators} at tier {tier}, with their controls — {how}",
        "as_recorded": "every outcome as recorded",
        "differ": "{k} {differs} from the record",
        "rerun_none": "re-run: none — no evaluator settles a claim {m} requires",
        "rerun_model": "re-run: none — the model does not load, so none of the {n} "
                       "{evaluators} {m} requires could run",
        "model_broken": "the model does not load, so none of the {n} evaluators {m} "
                        "requires ran: {why}",
        "not_required": "not required by {m}, unresolved: {groups}",
        "two_outcomes": "[two outcomes] {gate} : recorded {before} at ρ {rho} (entry "
                        "{entry}), a re-run gives {after}{detail}",
        "two_outcomes_records": "[two outcomes] {gate} : {text}",
        "two_outcomes_qualification": "[two outcomes] {gate} : qualified on the record, "
                                      "unqualified when re-run — {why}",
        "would_refuse": "export: would refuse — {reasons}",
        "refused": "export: refused — {reasons}",
        "would_write": "export: would write {path}/ — article {article} · package {package} "
                       "· {n} {files} (dry run: nothing written)",
        "written": "export: {path}/ — article {article} · package {package} · {n} {files}, "
                   "recorded in exports/{m}.json",
        "unresolved": "{id} {word}",
        "reprint": "{id} failed on article {article}, which the design has since moved "
                   "from: building a new article to test it again is a decision — "
                   "nopekit export {m} --proceed --why \"…\"",
        # A fail no reprint can release (review of P2.5b, finding 17): its
        # article is not one an export built and traced.
        "reprint_never": "{id} failed on article {article}, {which}: no pass on a reprint "
                         "releases that fail, so it counts on every design — going ahead "
                         "over it is a decision each time: nopekit export {m} --proceed "
                         "--why \"…\"",
        "reprint_unexported": "which no export built (recorded without --article)",
        "reprint_untraced": "whose export did not trace what its generator read",
        "missing": "{m} requires {id}, which no claim file holds",
        "requires_nothing": "{m} requires no claim",
        # The way out that works (review of P2.5b, findings 12, 20): two
        # outcomes at one ρ are two entries in the cache, and one of them is
        # not this evaluator's output. A re-run cannot clear it (the refused
        # export filed one already, and `check --force` only files another),
        # nor can a go-ahead (D7). *Rejected:* "`nopekit check --force` records
        # the re-run" — what it said, and the next export said it again.
        "disagrees": "{gate}: two outcomes for one read set — its entries in "
                     ".nopekit/verdicts/{gate}/ named above disagree, so one is not this "
                     "evaluator's output: remove the one that is wrong, or find why it "
                     "does not repeat (no re-run and no go-ahead clears it)",
        "generator_errored": "generator errored: {first}",
        "generator_none": "generator wrote no file",
        "generator_outside": "generator wrote {path}, outside its directory",
        "generator_linked": "generator linked {path} into its directory — a package holds "
                            "the bytes it hands the builder, never a link to a project "
                            "file: copy it",
        "generator_load": "generator does not load: {why}",
        "generator_model": "the model does not load, so the generator cannot run: {why}",
        "package_foreign": "out/{m}/{file} was not written by an export of {m}",
        "package_edited": "out/{m}/{file} was edited after export {package}",
        "package_moved": "inputs moved during export: {what} — run nopekit export {m} again",
        "identity": "no git identity: an export names who spent — set git config "
                    "user.name and user.email",
        "legacy": "the project keeps its records in the legacy .nopekit/ledger.json — "
                  "run nopekit check, which migrates it",
        "proceed_shell": "going ahead with unresolved claims is a decision a person records "
                         "in their own shell: they run nopekit export {m} --proceed --why "
                         "\"…\"{why}",
        "proceed_agent": " (this shell is an agent session: {marker} is set)",
        "proceed_pipe": " (stdin is not a terminal)",
        "proceed_why": "--proceed needs --why \"…\": why a person goes ahead is part of the "
                       "decision recorded. Nothing was written.",
        "proceed_dry": "a dry run records no decision — drop --proceed or --dry-run. "
                       "Nothing was written.",
        # The prompt ends as `claim physical`'s does (`HUMAN["signing"]["type"]`):
        # one convention for every line a person answers by typing a name.
        "proceed_prompt": "go ahead over {groups}? type {m} to record that decision "
                          "(anything else records nothing): ",
        "proceed_nothing": "nothing to decide: every required claim is checked",
        "decided": "decided by {who}: go ahead over {groups} — {why}",
        "typed": "you typed {typed}, not {m} — nothing was recorded",
        "no_milestone": "no milestone {name!r} — {declared}",
        "declared": "the declared ones are {names}",
        "none_declared": "none is declared: a milestone is milestones/<name>.json",
        "card_head": "test card for article {article}: {n} physical {results} to record, "
                     "{k} {checks}",
        "card_heading": "### Test card for article {article}",
        "card_row": "{id} {statement} — {test} — {latency}",
        "card_command": "nopekit claim physical {id} pass|fail --article {article} "
                        "--evidence <file> --detail \"…\"",
        "card_cross": "cross-check {id} {statement} — {condition}; its evaluators: {gates}",
        # The value decides the outcome (P2.5a-D15), never a typed `fail`: a
        # measurement that agrees is recorded too — E4's denominator — where a
        # hard-coded `fail` was refused for it (review of P2.5b, finding 22).
        "card_cross_command": "nopekit claim physical <id> --article {article} "
                              "--measured <value> --detail \"…\" — the value decides; a "
                              "fail on a cross-check contradicts its evaluator, on its track "
                              "record",
        "no_test": "no test written down: a pass needs one in claims/{id}.json "
                   "(\"mechanism\": {{\"how\": …}})",
        "help": "the boundary that spends: re-run what a milestone requires, refuse while "
                "a required claim is unresolved, and write its package",
        "milestone_help": "the milestone (milestones/<name>.json); none lists them",
        "dry_run_help": "the same run, writing nothing: what the export would do",
        "proceed_help": "go ahead over unresolved required claims — a decision a person "
                        "records in their own shell, typing the milestone's name",
        "why_help": "why the person goes ahead (with --proceed)",
    }),
    # Qualification (GLOSSARY §2, P2.3-D13, D14): every word of the line, the
    # reasons a claim's Gap row gives, `gate show`'s rows, `gate selftest`'s
    # summary and pack rows, `check`'s controls line and `doctor`'s rows. The
    # spine records facts and mints tokens (`verdicts.QualificationFacts`,
    # `Verdict.unqualified`); only this table words them. What it replaced:
    # four vocabularies for one fact — `last selftest: fired`, `6 fired, 0
    # BROKEN`, `re-verified`, and the refusal text the spine minted (`not
    # admitted: PASSED its own known-bad fixture`), reworded here by string
    # surgery until P2.3 rewrote the source.
    "qualification": MappingProxyType({
        "known_good": "known-good",
        "known_bad": "known-bad",
        # A control's outcome on the line: GLOSSARY §1's outcome words, `not
        # run` for a known-good control that does not exist (§2's own reason
        # for known-bad shown), `reads the current design` for one handed the
        # live design (GLOSSARY §1: a *candidate* is an alternative design with
        # a disposition, SPEC decision 15; the design in the checkout is the
        # current design).
        "outcome": MappingProxyType({"pass": "pass", "fail": "fail", "errored": "errored",
                                     "skipped": "skipped", "not-run": "not run",
                                     "live": "reads the current design",
                                     # P2.4-D19: it passed outside its own
                                     # operating context.
                                     "outside": "outside its operating context"}),
        # A known-bad control declared `expect="error"` that raised, as declared.
        "raised": "fail (raised, as declared)",
        "sep": " · ",
        "id_sep": " : ",
        "mutation": "mutation {fails}/{conclusive} fail",
        "none": "mutation 0 conclusive",
        "inconclusive": " ({k} inconclusive)",
        "none_made": " (none made: {why})",
        "why": MappingProxyType({"no-limit": "no value against a limit",
                                 "at-limit": "its value is at its limit",
                                 "tier": "tier {tier}"}),
        "could_not_run": "mutation could not run",
        "could_not_finish": "mutation could not finish",
        "walk_errored": "mutation errored",
        # One segment per channel fact (review of P2.3: `channels differ` was
        # printed for channels:check too, where both controls hand the SAME
        # keys — one word for two facts, the conflation D8 fixed for `none
        # made` — and an author who "fixed" the channels got the line again).
        "channels": "channels differ",
        "check_channel": "known-good and known-bad via ctx.extra",
        "ledger": "check run reads another ledger",
        # P2.4 (critique 1): the known-good value moved with a limit it read.
        # No *goalpost* on a human channel (GLOSSARY §1: the acceptance
        # condition, or its limit — review of P2.4).
        "goalpost": "value moves with its limit",
        "blocker": MappingProxyType({"two-outcomes": "two outcomes",
                                     "tier": "outcomes differ by tier",
                                     "differs": "outcome differs from its cached entry",
                                     # Review of P2.4: the writer made an entry
                                     # its own reader refuses — a defect, held.
                                     "unwritable": "qualification not recorded"}),
        "qualified": "→ qualified",
        "unqualified": "→ unqualified",
        # The reason after `unqualified: <evaluator> : ` — the FIRST fact that
        # does not hold, in the rule's order (P2.3-D15), by the token's kind.
        "reason": MappingProxyType({
            "known-bad:pass": "known-bad pass",
            "known-bad:errored": "known-bad errored: {text}",
            "known-bad:skipped": "known-bad skipped itself: {text}",
            "known-good:fail": "known-good fail",
            "known-good:errored": "known-good errored: {text}",
            "known-good:skipped": "known-good skipped itself: {text}",
            "known-good:not-run": "known-good not run",
            "known-good:live": "known-good control reads the current design",
            "known-good:outside": "known-good outside its operating context",
            "goalpost:moves": "its known-good value moves when the acceptance condition it "
                              "reads ({text}) moves: a value is measured, never chosen by the "
                              "limit it is judged against",
            "channels:differ": "known-good and known-bad reach it through different channels "
                               "(ctx.extra: known-bad {{{bad}}}, known-good {{{good}}})",
            "channels:check": "its controls reach it through ctx.extra, which a check run never "
                              "hands it (known-bad {{{bad}}}, known-good {{{good}}})",
            "channels:ledger": "its check run read ledger {keys} at a value no qualification "
                               "run read: hand its known-good design the same claims",
            "mutation:pass": "mutation {text} fail",
            "mutation:could-not-run": "mutation pass could not run: {text}",
            "mutation:could-not-finish": "mutation pass could not finish: the values that move "
                                         "its value need more than {text} runs",
            "mutation:errored": "mutation pass errored and did not repeat it: {text}",
            "control:two-outcomes": "two control outcomes recorded for identical inputs "
                                    "({text})",
            "control:tier": "qualification differs by ctx.tier ({text}): unqualified on one "
                            "tier's path",
            "control:differs": "control outcome differs from its cached entry ({text})",
            "control:unwritable": "its qualification could not be recorded — nopekit made "
                                  "a control entry its own reader refuses ({text}); a defect "
                                  "to report, and the next check run tries again",
            "qualification:not-yet": "not yet qualified at this version — {how}",
        }),
        # `channels:ledger` over an acceptance condition (P2.4's `acceptance:` and
        # `acceptance-shape:` keys): its own words, never the raw keys. What
        # slipped through (review of P2.4): the reason printed `ledger
        # acceptance-shape:C1` and said "hand its known-good design the same
        # claims" — copying the live C1 into CLAIMS, the coupling D2 measured as
        # wrong (the live C1 at 0.4 read `known-good fail`).
        "acceptance_ledger": "the acceptance condition of {claims} is not one a qualification "
                             "run read, and only a limit its qualification moved may differ: "
                             "state {claims} in selftest/known_good.py CLAIMS with the same "
                             "quantity, comparator and units, keeping the limit it was "
                             "calibrated at",
        # What to do, after a pack-mode problem's reason (`packs._unqualified_problem`),
        # by the token's kind; none where the reason says it.
        "remedy": MappingProxyType({
            "known-bad:pass": "the fixture is not bad in the way this evaluator checks, "
                              "or the evaluator is a logger",
            "known-good:fail": "the known-good fixture is not good or the evaluator is "
                               "wrong, and its known-bad control shows nothing until one "
                               "of them is fixed",
            "channels:differ": "declare a known-good fixture (NegativeControl.good) that "
                               "hands it the same keys",
        }),
        # A prerequisite that does not pass a dependent's known-bad control
        # (P2.2-D13): `gate selftest`'s note and `pack validate`'s problem.
        "isolation": "control not isolated — its prerequisite {need} does not pass "
                     "{gate}'s known-bad control ({word}: {body})",
        "isolation_fix": "; the guard pre-empts the control wherever both run. Make the "
                         "control move only what {gate} judges, or drop the edge",
        "how": MappingProxyType({"plain": "the next check run qualifies it",
                                 "tier": "nopekit check --tier {tier} qualifies it",
                                 # A dependent its prerequisite prunes: no check run
                                 # reaches its qualification until the root holds
                                 # (review of P2.3: it was promised the next one).
                                 "once": "it qualifies once {root} is established"}),
        # `gate show`'s `qualification:` row, for the states with no line.
        "pending": "due to re-qualify — {moved} moved; the next check run re-qualifies it",
        "undemonstrated": "not yet qualified at this version — the next check run qualifies it",
        "last": "(last: {line})",
        # `gate show`'s detail rows under it.
        "detail": MappingProxyType({
            "known_good": "known-good", "known_bad": "known-bad", "mutation": "mutation",
            "not_mutated": "not mutated", "why": "why",
            "none": "none: no known-good control exists for it (a pack's "
                    "selftest/baseline.json, a project's selftest/known_good.py, or a "
                    "good= fixture)",
            "baseline": "selftest/baseline.json", "known_good_py": "selftest/known_good.py",
            "value": "{measured} (limit {limit})",
            "result": "{key} {before} -> {after}: {outcome}",
            "inconclusive": "{key} -> {after}: {outcome} ({why})",
            "no-limit": "none: it reports no value against a limit",
            "at-limit": "none: its value is exactly at its limit",
            "tier": "none: the walk runs at tier 0, and this evaluator is tier {tier}",
            "budget": "it could not finish: the values that move its value need more runs",
            # A template: the ladder's ends and the margin are the walk's own
            # constants (`gates.MUTATION_RUNGS`, `MUTATION_MARGIN`), filled when the
            # row is rendered — never a second home for the numbers (review of
            # P2.3: retuning the margin would have left this saying 15%).
            "never-lands": "from x{lo} to x{hi} its value never landed {margin}% past its "
                           "limit",
            "word": "a word: not walked", "zero": "zero: not walked", "none_value": "None: "
            "not walked", "not-finite": "not a finite number: not walked",
            "other": "not a number, a flag or a list of numbers: not walked",
            "budget_key": "the walk's budget ran out before it",
            # One row per run with a limit moved (`gates.goalpost_runs`).
            "goalpost": "limit moved",
            "goalpost_row": "{key} {end} -> {limit}: {outcome}, {value}",
            "ends": MappingProxyType({"limit": "limit", "limit_hi": "upper limit"}),
            "no_value": "no value",
        }),
        "selftest_summary": "{n} evaluators in {t}: {q} qualified, {u} unqualified, "
                            "{s} skipped",
        "pack_row": "{pack} ({origin}) : {q} qualified",
        "pack_unqualified": ", {u} unqualified",
        "pack_skipped": ", {s} skipped",
        "controls": "controls: {run} run, {preserved} preserved, {requalified} re-qualified",
        "doctor": MappingProxyType({
            "ok": "every evaluator qualified at its version",
            "unqualified": "{n} evaluator(s) unqualified: {list}",
            "also_waiting": "; {n} more not yet qualified at their version",
            "not_yet": "{n} evaluator(s) not yet qualified at their version: {list}",
            "known_good_ok": "every project evaluator has a known-good control",
            "known_good": "{n} project evaluator(s) with no known-good control ({list}): "
                          "each reads \"known-good not run\", so its claims read Gap; write "
                          "selftest/known_good.py's context(ctx) returning the design every "
                          "control is one change from, or declare a good= fixture",
        }),
        "help": "run each evaluator's known-good and known-bad controls, and the mutation "
                "pass for one not from a bundled pack; exits 1 on any unqualified evaluator",
    }),
    # SPEC decision 13: what would settle a claim, and the cross-check of the
    # evaluators its mechanism names (never coverage, never a status). Never
    # "not run" (Open's word): a mechanism "has yet to run".
    "mechanism": MappingProxyType({
        # The label apart from the body (review of phase 1): the report's gap
        # section puts its own bold label in front, and read "**Mechanism:**
        # mechanism: automated — …".
        "line": "mechanism: {body}",
        "body": "{by} — {how}",
        "unsaid": "{by} — what would settle it is not written down: say it in "
                  "claims/{id}.json (\"mechanism\": {{\"how\": …}})",
        "names": "evaluators it expects: {evaluators}",
        "unregistered": "{id}'s mechanism names {evaluator}, which is not registered here — "
                        "no status moves; install the pack that provides it, or fix the "
                        "name in claims/{id}.json",
        # Never "tag the claim for it" (review of phase 1): tags are coverage,
        # so that advice turned the cross-check into coverage — C7 (first
        # mode) read Checked on the bearing-stress pass once tagged `bearing`.
        "uncovered": "{id}'s mechanism names {evaluator}, which is not one of {id}'s "
                     "evaluators (it is filed under {filed}) — no status moves; it "
                     "settles another claim's quantity: fix the name in "
                     "claims/{id}.json, or name an evaluator that computes {id}'s own",
        "filed_none": "nothing",
        "warning": "warning: {line}",
        "doctor_ok": "every evaluator a claim's mechanism names is registered here and "
                     "covers that claim",
        "doctor_none": "no claim's mechanism names an evaluator",
        "doctor_warn": "{n} evaluator name(s) in claims' mechanisms do not match the "
                       "registry: {list}",
        "modelled_part": "a pack's evaluator can check a modelled part of {id}; its pass "
                         "never settles {id} — {by} does",
        "empty": "nothing to list: no claim reads {gap}, {pending_build} or {assumed}",
        "footer": "{n} {claims} whose mechanism has yet to run: {counts}. Name the "
                  "quantity, try the analytic answer first, and only then propose a "
                  "solver — with its real cost said out loud.",
        "need": "gap record {id} {quantity} ({word})",
        "no_match": "no installed pack settles this vocabulary — this is the extension "
                    "protocol's trigger (docs/EXTENSION_PROTOCOL.md)",
        # A physical, judged or assumed claim (review of phase 1): the protocol
        # never fires there, so no pack is not its trigger.
        "no_match_settled": "no installed pack checks a modelled part of this — and "
                            "none is needed: {by} settles it, never a solver",
        "footer_settled": "{n} {claims} whose mechanism has yet to run: {counts}. Each "
                          "is settled by its mechanism — a test on an article, a "
                          "person's judgment or an owner — never by a solver.",
    }),
    # SPEC decision 12: what the person asked for, rolled up from its claims by
    # `claims.requirement_statuses`, the one producer. Never a status of its
    # own: the eight words, and *ready* stays the claims'.
    "requirement": MappingProxyType({
        "intro": "What the person asked for, in their words. Each reads the worst status "
                 "among the claims that serve it, and one no claim serves reads {gap}. "
                 "Ready is judged on the claims a milestone requires, never on these.",
        "md_row": "- **{id}** *({word})* {statement} — {claims}",
        "group": "{ids} {word}",
        "sep": " · ",
        "no_claim": "no claim serves it",
        "line": "requirements {n} — {items}",
        "item": "{id} {word}",
        "item_worst": "{id} {word} ({ids})",
        "item_none": "{id} {word} (no claim)",
        # D18 carried up (review of phase 1): a requirement over a claim the
        # resolver calls Checked while its evidence says otherwise is never a
        # bare "checked" — the claim rows list that claim as a contradiction.
        "item_disagree": "{id} {word} (status and evidence disagree: {ids})",
        "md_disagree": "**status and evidence disagree: {ids}** — ",
        "why_disagree": "status and evidence disagree on {ids} — the resolver and the "
                        "verdicts disagree, a defect to report",
        "more": "+{k} more",
        "more_unresolved": "+{k} more ({u} unresolved)",
        "why_head": "requirement  {id}  [{word}]",
        "why_source": "source: {source}",
        "why_claims": "CLAIMS ({n})",
        "why_none": "(none — no claim names {id} in \"requirements\", so it reads {gap}: "
                    "write a claim that settles it, and name {id} there)",
        "serves": "serves: {ids}",
        "serves_missing": "{id} (no requirements/{id}.json)",
        "doctor_ok": "{n} requirement(s), each served by a claim",
        "doctor_none": "none recorded — a requirement is requirements/<id>.json: the "
                       "person's words",
        "doctor_unserved": "{ids}: served by no claim — each reads {gap} until a claim "
                           "names it in \"requirements\"",
        "doctor_dangling": "claims/{claim}.json names requirement {id}, which has no "
                           "requirements/{id}.json",
    }),
    # SPEC 14, §0.2 (architecture phase 2): what lost, why, and what would
    # bring it back — `claims.rejection_rows`' tokens in words. A condition on
    # a claim that is not Checked says so; it never silently reads false.
    "rejection": MappingProxyType({
        "types": MappingProxyType({
            "infeasible": "infeasible", "dominated": "dominated",
            "preferred_against": "preferred against", "unevaluated": "unevaluated",
            "untyped": "untyped"}),
        "recorded": "preferred against, recorded by {name} ({when})",
        "not_recorded": "preferred against, not recorded by {name}",
        "condition": "{claim} {quantity} {comparator} {value}",
        "holds": "holds: {condition}, now {observed}{gate}",
        "does_not_hold": "does not hold: {condition}, now {observed}{gate}",
        "cannot": "cannot be evaluated: {claim} is {status}",
        "cannot_no_claim": "cannot be evaluated: {claim} is not a claim",
        "cannot_no_value": "cannot be evaluated: no value of {claim} is compared with its "
                           "acceptance condition",
        "cannot_no_margin": "cannot be evaluated: {gate} gives {claim} no margin ({detail})",
        "cannot_no_limit": "cannot be evaluated: {claim} states no limit",
        "cannot_band": "cannot be evaluated: {claim}'s acceptance condition is a band",
        "margin_why": MappingProxyType({
            "no-value": "it gave no value", "no-limit": "it gave no limit",
            "band": "its limit is a band", "no-side": "its comparator has no side",
            "zero-limit": "its limit is zero",
            "disagrees": "its side and its outcome disagree",
            "no-comparator": "it names no side of its limit",
            "no-verdict": "it gave no usable verdict"}),
        "text": "not machine-checkable: {text}",
        "none": "no return condition",
        "input": "source: input {input}",
        "facts": "{type} · {condition}",
        "may_return": "may return: {value} — rejected in decision {decision} ({type}); {state}",
        # `decide`'s static row and the generated log: never a live state (the
        # log is a generated document; the row is what was just written).
        "returns_if": "returns if {condition}",
        "returns_text": "returns if (not machine-checkable) \"{text}\"",
        # The generated log's phrase: it is regenerated only by `report --write`,
        # so it never says whether the person recorded it (a state that moves
        # without the log), only where to read it.
        "named": "preferred against, naming {name} — nopekit why {id} says whether they "
                 "recorded it",
        "records_it": "{name} records it in their own shell: nopekit prefer {id}",
        "doctor_ok": "{n} decision(s), {m} rejection(s): every pointer resolves, every "
                     "preference recorded",
        "doctor_none": "no decision records a rejected alternative",
        "doctor_dangling": "{where} names {what}, which has no {home}",
        "doctor_orphan": "preferences/{id}.json: no decisions/{id}.json holds that decision",
        "doctor_unrecorded": "{k} preferred_against rejection(s) not recorded: {items}",
        "doctor_unrecorded_item": "{decision} ({value}, by {name}) — {name} runs: nopekit "
                                  "prefer {decision}",
    }),
    # SPEC 38: where an evaluator's or a proposer's verdict logic comes from.
    # Each word equals its token; shown, never a status. A basis word is said
    # beside "basis" (GLOSSARY §6: `basis established`, apart from invariant
    # 10's *prerequisite not established*) — review of phase 2.
    "basis": MappingProxyType({
        "established": "established", "sourced": "sourced", "model-only": "model-only",
        "undeclared": "undeclared",
        "doctor_ok": "every evaluator declares its basis",
        "doctor_undeclared": "{n} evaluator(s) declare no basis and show as undeclared: "
                             "{ids}",
        "doctor_inputs": "{k} input(s) say they were proposed by something no discoverable "
                         "pack declares: {ids}",
        "proposed_by": "proposed by {id} (basis {basis})",
    }),
    # `decide`'s per-rejection flags (SPEC 14): each refusal writes nothing.
    "decide": MappingProxyType({
        "part_first": "{flag} applies to the --rejected before it, and none comes before "
                      "it. Nothing was written.",
        "part_twice": "{flag} is given twice for \"{value}\". Nothing was written.",
        "both": "--returns-if and --returns-text both given for \"{value}\" — a return "
                "condition is one or the other. Nothing was written.",
        "bad_type": "--type \"{v}\" is not a rejection type{guess} — one of infeasible, "
                    "dominated, preferred_against, unevaluated. Nothing was written.",
        "grammar": "--returns-if \"{v}\" is not a return condition: say \"<claim> "
                   "margin|value|limit <|<=|>|>= <number>\", e.g. \"C1 margin > 0.02\"; a "
                   "condition in words goes in --returns-text. Nothing was written.",
        "no_claim": "--returns-if names {cid}, which is no claim (claims/{cid}.json) — check "
                    "evaluates the condition against that claim. Nothing was written.",
        "no_input": "--input {aid!r} is no input (inputs/{aid}.json){close}. Nothing was "
                    "written.",
        "preferred_needs_by": "a preferred_against rejection is a person's preference: name "
                              "them with --by \"<their name, as git knows them>\"; they "
                              "record it with nopekit prefer <decision id>. Nothing was "
                              "written.",
        "by_needs_type": "--by names whose preference a rejection is, and \"{value}\" is not "
                         "--type preferred_against. Nothing was written.",
        "type_help": "the --rejected before it: infeasible, dominated, preferred_against "
                     "(a person's preference, recorded with nopekit prefer) or unevaluated",
        "by_help": "the --rejected before it is this person's preference (with --type "
                   "preferred_against), named as git knows them",
        "returns_if_help": "the --rejected before it returns when \"<claim> "
                           "margin|value|limit <op> <number>\" holds, e.g. \"C1 margin > "
                           "0.02\" — check evaluates it",
        "returns_text_help": "what would bring the --rejected before it back, in words — "
                             "shown, never evaluated",
        "input_help": "the input (inputs/<id>.json) the --rejected before it rests on",
    }),
    # `nopekit prefer` (SPEC 14, invariant 13): the person's channel for a
    # preference, the shape of `claim physical`'s owner path.
    "prefer": MappingProxyType({
        "help": "record your preference against a rejected alternative, typed in your own "
                "shell; the rejection's \"by\" must name you as git does",
        "value_help": "which preferred_against alternative, by its value, when the decision "
                      "names you for several",
        "who": "--who is not accepted: who records a preference is read from git's identity "
               "(git config user.name and user.email), never typed. Nothing was written.",
        "when": "--when is not accepted: a preference is dated when it is recorded. Nothing "
                "was written.",
        "no_decision": "no decision {id!r} — decisions/ holds each one, and nopekit why "
                       "<decision id> shows one. Nothing was written.",
        "none_preferred": "decision {id} has no preferred_against rejection — nothing in it "
                          "is a person's preference to record. Nothing was written.",
        "channel": "a preference counts only as its person records it, typed in their own "
                   "shell, never from an agent session or a script{why}. Nothing was "
                   "written. Ask {names} to run: nopekit prefer {id}",
        "not_by": "decision {id} names {names} for its preferred_against rejections, and this "
                  "shell's git identity is {who!r} — only the person named records it: run "
                  "it in their shell, or name them in \"by\" as git names them. Nothing was "
                  "written.",
        "which": "decision {id} names you for {n} preferred_against rejections ({values}) — "
                 "name one with --value. Nothing was written.",
        "no_value": "decision {id} names you for no preferred_against rejection {value!r} — "
                    "it names you for {values}. Nothing was written.",
        "already": "{name}'s preference against {value!r} is already recorded ({when}) for "
                   "the rejection as it reads now. Nothing was written.",
        "row": "you record: your preference against {value!r} — {why}",
        "returns_row": "reconsider: {condition}",
        "recorded": "recorded {name}'s preference against {value!r} in preferences/{id}.json "
                    "(entry {n})",
    }),
    # Architecture phase 3a (SPEC 18, 24): `nopekit round`'s one paragraph, and
    # the round record's state as `status` and `doctor` say it. `{path}` is
    # `rounds.ROUND_REL`, filled by the caller: the file's name has one home.
    # *Ready* is said as last evaluated (invariant 12): nothing a round reads
    # was re-run at a boundary.
    "round": MappingProxyType({
        "help": "end a round: a tier-0 check, the site rebuilt when site/ exists, and {path} "
                "written when anything it records moved; with --commit, a commit of every "
                "file in the project that git does not ignore, as a listed person; never "
                "blocks",
        # Architecture phase 6 (SPEC 24 with §0.3): the round's commit, the Stop
        # hook's message, the commit message, and doctor's `people` row.
        "commit_help": "then commit every file in the project's directory that git does not "
                       "ignore (one over 50 MiB or named like a secret is named instead), only "
                       "when git's identity is one of .nopekit/project.json's people; never "
                       "pushes",
        "hook_help": "Claude Code's Stop hook: read the hook's JSON on stdin, run --commit, "
                     "print one JSON object; always exits 0",
        "committed": " Committed {oid} as {who}: {n} {files}.",
        "nothing": " Nothing to commit: no file in the project's directory changed.",
        # The acceptance run after phase 3b: nothing staged, and something
        # named below — "no file changed" beside "not staged: .env" would
        # contradict itself.
        "nothing_named": " Nothing to commit but what a round never stages.",
        "no_repository": " Not committed: the project is in no git repository (`git init` "
                         "starts one), or git is not on PATH.",
        "no_identity": " Not committed: git has no user.name and user.email configured here.",
        "no_people": " Not committed: .nopekit/project.json lists no people; a round commits "
                     "only as a person listed there.",
        "not_listed": " Not committed: git's identity here, {who}, is not in "
                      ".nopekit/project.json's people.",
        "committer_not_listed": " Not committed: git's committer here, {who} "
                                "(committer.name and committer.email, or GIT_COMMITTER_*), "
                                "is not in .nopekit/project.json's people.",
        "ignored": " Not committed: the enclosing git repository ignores this project "
                   "(.nopekit/project.json is ignored), so no change here is ever listed; "
                   "un-ignore the project's directory, or `git init` in it.",
        "in_progress": " Not committed: a {operation} is in progress in this repository; the "
                       "next round commits after it ends.",
        "detached": " Not committed: HEAD is detached; a round commits only on a branch.",
        "newer_record": " Not committed: the round record was written by a newer nopekit.",
        "add_failed": " Not committed: git did not stage the round's files ({why}).",
        "refused": " Not committed: git refused the commit ({why}); the round's files are "
                   "left staged.",
        "unlisted_changes": " Not committed: git did not answer when asked which files "
                            "changed.",
        "timeout": " Not committed: git commit did not finish in {s} s (a slow commit hook?); "
                   "the round's files are left staged.",
        "secret": " Not staged, named like a secret: {paths}.",
        "too_large": " Not staged, over 50 MiB: {paths}.",
        "not_files": " Not staged, not a file: {paths}.",
        "prose_behind": " Board text written before the latest results: {subjects} — read it "
                        "against the page, rewrite it, then `nopekit site stamp <board>`.",
        "paths_more": ", and {k} more",
        "hook_not_run": "nopekit round not run: {why}. Nothing was committed.",
        "hook_crash": "nopekit round crashed ({kind}: {line}); the round may be incomplete. "
                      "Run `nopekit round` in the project to see the traceback.",
        "commit_subject": "round {n}: {what}",
        "commit_first": "the first round record",
        "commit_compact": MappingProxyType({
            "statuses": "{k} {statuses} moved", "verdicts": "{v} new {verdicts}",
            "instructions": "instructions moved"}),
        "commit_files": "{n} {files} committed: {groups}.",
        "commit_earlier_one": "Round {a} was not committed; this commit holds it too.",
        "commit_earlier_many": "Rounds {a} to {b} were not committed; this commit holds them "
                               "too.",
        "commit_secret": "Not committed, named like a secret: {paths}.",
        "commit_too_large": "Not committed, over 50 MiB: {paths}.",
        "commit_not_files": "Not committed, not a file: {paths}.",
        "commit_parts": MappingProxyType({
            "records": "records", "model": "the model", "evaluators": "evaluators",
            "generators": "generators", "verdicts": "verdicts", "site_text": "site text",
            "site": "the site", "trace": "the trace", "packs": "packs",
            "project": "project files", "round": "the round record",
            "files": "other files"}),
        "commit_changed": "{parts} changed",
        "people_ok": "{n} listed ({names}); git's identity here is listed, so rounds commit",
        "people_none": "none listed — rounds make no commit; the person adds themselves to "
                       "\"people\" in .nopekit/project.json as {{\"name\": \"...\", "
                       "\"email\": \"...\"}}",
        "people_not_listed": "{n} listed ({names}); git's identity here, {who}, is not one of "
                             "them — rounds make no commit",
        "people_no_identity": "{n} listed; git has no user.name and user.email here — rounds "
                              "make no commit",
        "people_committer_not_listed": "{n} listed ({names}); git's committer here, {who}, "
                                       "is not one of them — rounds make no commit",
        "people_no_repo": "{n} listed; the project is in no git repository — rounds make no "
                          "commit (`git init`)",
        "written": "round {n}: {changes}.",
        "first": "round 1: the first round record.",
        "replaced": "round {n}: the last round record was {how}, so nothing is compared.",
        "replaced_how": MappingProxyType({"edited": "edited by hand",
                                          "unreadable": "unreadable"}),
        "unchanged": "round {n} stands: nothing it records moved.",
        "newer": "round: {path} was written by a newer nopekit (schema {schema}); this one "
                 "does not rewrite it.",
        "ready": "Ready, as last evaluated.",
        "not_ready": "NOT ready, as last evaluated: {k} of {n} required {claims} {are} "
                     "unresolved.",
        "check_ran": "Tier-0 check: {executed} run, {cached} from the cache.",
        "check_not_run": "Tier-0 check not run: {why}.",
        "site_built": " Site built.",
        "site_warnings": " Site built with {n} {warnings} — `nopekit site build` lists them.",
        "site_failed": " Site not built: {why}.",
        "wrote": " Wrote {path}.",
        "not_run": "round not run: {why}.",
        "no_project": "round: no nopekit project here — nothing to record.",
        "flip": "{id} {before} -> {after}",
        "appeared": "{id} new ({after})",
        "gone": "{id} gone (was {before})",
        "more": "and {n} more",
        "verdicts": "{n} new {verdicts} ({ids})",
        "ids_more": "+{k} more",
        "instructions": "instructions moved ({paths})",
        "instructions_unrecorded": "instructions not recorded (the model does not load)",
        "no_status": "no status moved ({what} moved)",
        "moved": MappingProxyType({"records": "the records", "verdicts": "the verdict cache",
                                   "design": "the design", "trace": "the trace",
                                   "resolution": "the resolution (an evaluator's code, its "
                                                 "controls, a pack or a tool)"}),
        "state_head": "round record: {line}",
        "state_absent": "none — `nopekit round` writes the first",
        "state_current": "{round}current with the records, the verdict cache and the design",
        "state_behind": "{round}behind the records — {what} moved since it was written; "
                        "`nopekit round` writes the next",
        "state_edited": "{round}edited by hand — its digest does not match what it holds; "
                        "`nopekit round` writes it again, and nothing reads it for truth",
        "state_unreadable": "does not read ({why}) — `nopekit round` writes it again",
        "state_newer": "written by a newer nopekit (schema {schema}) — this one neither reads "
                       "nor rewrites it",
        "graph_ok": "{n} nodes, {m} edges ({k} rests on, {r} resolves)",
        "graph_untraced": "; derivation not traced yet — each rests-on edge is a direct read",
        "graph_traced": "; derivation {traced}",
        "traced_at": "traced at round {r}",
        "traced": "traced (no round record named it)",
        # Architecture phase 3b: what the trace observed, and amber, in `why`.
        "amber_flips": "amber: flips — {choice} {step} ({aid}, not confirmed) gives "
                       "{evaluator} {measured} against {limit} {units}; shown beside Checked, it "
                       "changes no status",
        "amber_holds": "amber: holds — ±10% on {choices} ({aids}) leaves it Checked",
        "amber_unclear": "amber: could not tell — {token} at {choice} {step}",
        "amber_not_current": "amber: not current — {why}; `nopekit model trace`",
        "amber_untraced": "amber: not traced — {why}",
        "moves": "moves: {items} — {traced}",
        "moves_none": "moves: nothing at ±10%",
        "moves_raised": "moves: not observed — both moved builds raised {type}",
        "moves_raised_one": "the {step} build raised {type}: nothing observed that way",
        "moves_size": " (an integer: moved {lo} and {hi})",
        "not_moved": "not moved by the trace: {word}",
        "derived_from": "derived from: {items} — {traced}",
        "after_of": "after: {before} · before: {after} (inferred from the trace: it moves with "
                    "every choice that moves each of them, and with more)",
        "ties": "moves together with: {ids}; the trace cannot order them",
        "single": "moved by one choice only; the trace does not order it",
        "unmoved": "not seen to move under any ±10% move; that does not show that it depends "
                   "on nothing",
        "changed": "changed",
        "trace_started": " Tracing the derivation in the background (log {log}).",
        "trace_amber": " Re-running amber in the background (log {log}).",
        "trace_running": " A trace is running already (log {log}).",
        "trace_incomplete": " The last trace stopped before it finished; `nopekit model trace` "
                            "tries again.",
        "trace_failed": " The trace did not start: {why}.",
        # `why`'s lines (SPEC 10, 19): what reads a value, what sources it, and
        # what a claim rests on, resolves or is resolved by.
        "read_by": "read by: {ids}",
        "read_by_none": "read by: none — no evaluator's verdict on record read it",
        "sourced_from": "sourced from: {items}",
        "sourced_from_none": "sourced from: nothing recorded",
        "source_assumption": "assumption {id} ({word})",
        "source_input": "input {id}",
        "derived_from_untraced": "derived from: not traced yet",
        "value_head": "value {name}{value}   (derived by build() in {entry})",
        "rests_on": "rests on: {items}",
        "rests_on_item": "{id} ({word}; {via})",
        "rests_on_via": "{evaluator} read {value}",
        "rests_on_untraced": "rests on, through derived values: not traced yet — only the "
                             "values its evaluators read directly are followed",
        "resolves": "resolves: {items}",
        "resolved_by": "resolved by: {items}",
        "item": "{id} ({word})",
        "item_missing": "{id} (no such claim)",
    }),
    # `nopekit propose` (SPEC 22, 25): a pack's tool proposes, never settles.
    # The noun "proposer" stays in keys and flags (SPEC §0.9).
    "propose": MappingProxyType({
        "help": "run a tool a pack ships to propose options or evidence, and record each "
                "file it writes as an input — a proposal settles nothing",
        "row": "{id}  basis {basis}  {description}  (pack {pack})",
        "footer": "nopekit propose <id> [arguments] runs one and records each file it writes "
                  "as an input",
        "empty": "no discoverable pack ships anything to propose — a pack declares it in "
                 "pack.json's provides_proposers",
        "errored": "{id} exited {code}; nothing was recorded. Its last lines: {tail}",
        "timeout": "{id} ran past --timeout {t:g} s and was stopped; nothing was recorded",
        "outside": "{id} wrote outside $NOPEKIT_PROPOSAL_DIR while it ran: {paths}; nothing "
                   "was recorded — a proposal writes only into that directory, and what it "
                   "wrote here is not undone: git status shows it",
        "outside_more": " and {n} more",
        "none": "{id} wrote no file; nothing was recorded",
        "known": "{where} is already recorded as {aid} — its record is unchanged",
        "done": "{n} input(s) recorded, proposed by {id} (basis {basis}). A proposal "
                "settles nothing: extract what each shows, and let an evaluator check what "
                "it is the source of.",
        "desc": "proposed by {id}",
        "ingest_known": "{where} is already recorded as {aid} {was}; its record is unchanged",
        "ingest_was_none": "with no proposer",
        "proposer_help": "what produced this file (a pack's provides_proposers id), when the "
                         "agent ran it by hand",
        "kind_help": "the kind of every file it writes (default: from each extension)",
        "desc_help": "a description for every file it writes (default: proposed by <id>)",
        "timeout_help": "seconds before it is stopped (default 600)",
        "args_help": "what the script is handed; a flag of the script's goes after --, as in "
                     "nopekit propose <id> -- --limit 5",
    }),
    # `nopekit model trace` and every reader of the trace and of amber
    # (architecture phase 3b, SPEC 39). Never "independent": a value not seen
    # to move under a ±10% move is said as not seen to move, and the trace is
    # one point of a nonlinear model (invariant 16).
    "trace": MappingProxyType({
        "help": "rebuild the model in copies with each choice moved -10% and +10%, and record "
                "which derived values moved (the trace) and which passes an assumption's move "
                "flips (amber)",
        "choices_help": "trace only these choices (comma-separated, repeatable); the trace "
                        "file is written when every choice is traced",
        "workers_help": "builds at once (default: {n}, the cores less one, at most 8)",
        "timeout_help": "seconds a build may take (default: the larger of {floor:.0f} and "
                        "{factor:.0f}x the base build)",
        "skip": MappingProxyType({
            "yes-no": "a yes/no choice", "word": "a word", "none": "no value",
            "structure": "a structure", "zero": "zero: no relative move",
            "not-finite": "not a finite number", "fixed": "not settable (init=False)"}),
        "refuse": MappingProxyType({
            "legacy": "this project's records are still in the legacy ledger: `nopekit check` "
                      "migrates them first",
            "no_model": "the model does not load, so nothing can be traced: {why}",
            "unrecorded": "the model's code was not recorded (code loaded from a string or "
                          "exec), so no trace can be keyed to it",
            "unknown_choice": "--choices names {name}, which is not a choice of {entry} (its "
                              "Config){hint}",
            "hint": "; did you mean {hint}?",
            "workers": "--workers must be 1 or more",
            "inside": "its temp directory {base} is inside {clash}: a build's copy would land "
                      "in the tree the trace may not write (set TMPDIR outside the project)",
            "running": "a trace is already running for this project (process {pid}; log "
                       "{log}); wait for it",
            "big": "the project is {size} without .git, out/, site/ and caches, more than the "
                   "1 GiB a trace copies; the largest directories are {dirs}",
            "base_raised": "the design does not build in a copy of the project: {line}",
            "edited": "{path} reads {state}: {line}. Delete it to trace this design again",
            "wrote": "{choice} {step}: the build wrote inside the project: {paths} — the "
                     "trace keeps every build in a copy; make build() write under its own "
                     "directory",
            "wrote_base": "the base build wrote inside the project: {paths} — the trace "
                          "keeps every build in a copy; make build() write under its own "
                          "directory",
        }),
        "head": "trace: {entry}, design {key12} — {n} choices to move by ±10% ({builds} builds "
                "on {workers} workers)",
        "not_nudged": "  not moved: {items}",
        "and_more": " and {n} more",
        "steady": "  base built twice: steady",
        "unsteady": "  base built twice: {names} differ between builds; the trace draws no "
                    "edge to them",
        "progress": "  [{done:>4}/{total}] {choice} {step}: {n} values moved",
        "progress_raised": "  [{done:>4}/{total}] {choice} {step}: raised {type}: {line}",
        "timed_out": "{choice} {step}: no result in {s:.0f} s (timed out)",
        "dead": "{choice} {step}: the build process exited {code} with no result {why}",
        "base_timed_out": "the base build gave no result in {s:.0f} s (timed out)",
        "base_dead": "the base build exited {code} with no result: {why}",
        "environmental": "{choice} {step}: raised {a}, then {b} when run alone — an "
                         "environmental failure",
        "written": "traced: {n} choices moved both ways; {moved} derived values moved, "
                   "{unmoved} not seen to move ({names})",
        "order": "  derives {d} edges · after {a} edges · {t} groups move together · {s} "
                 "values moved by one choice",
        "amber": "amber: {n} automated claims evaluated on {k} assumption-sourced choices; "
                 "{flips}",
        "amber_flip_item": "{id} flips",
        "amber_none": "none flips",
        "wrote": "written: {paths} ({seconds:.0f} s)",
        "current": "trace: current for this design ({path}); amber current — nothing to "
                   "observe",
        "partial": "traced {done} of {total} choices for this design; the trace is written "
                   "when every choice is traced — `nopekit model trace`",
        "incomplete": "trace not written: {n} build(s) did not finish ({items}). The {k} that "
                      "finished are kept for the next run",
        "retry": "  run again with --timeout {suggest:.0f}",
        # amber's reasons, said after "not traced —" or "not current —"
        "amber_why": MappingProxyType({
            "no_trace": "the design has no trace yet",
            "skipped": "the trace does not move {name} ({word})",
            "no_file": "no amber run holds it yet",
            "not_run": "amber did not run on {name}",
            "evaluators": "the evaluators changed",
            "acceptance": "the acceptance condition changed",
            "gate": "{gate} changed",
        }),
        # `status`
        "status_none": "trace: not traced for this design — `nopekit round` starts it in the "
                       "background; `nopekit model trace` runs it here",
        "status_running": "trace: running (process {pid}; log {log})",
        "status_incomplete": "trace: incomplete for this design — {k} build(s) did not "
                             "finish; `nopekit model trace --timeout {s:.0f}` tries again",
        "status_edited": "trace: {path} reads {state} — nothing reads it; delete it and run "
                         "`nopekit model trace`",
        "status_unrecorded": "trace: not traced — {why}",
        "unrecorded_why": "the model's code was not recorded",
        "amber_head": "amber: {items}",
        "status_flips": "flips ({aid} {choice} {step})",
        "status_not_current": "not current ({why})",
        "status_unclear": "could not tell ({token} at {choice} {step})",
        "status_untraced": "not traced",
        "status_item": "{id} {words}",
        # `doctor`
        "doctor_ok": "current for this design: {n} choices moved, {k} not moved; after {a} "
                     "edges, {t} groups move together",
        "doctor_others": "{m} trace files for other designs ({size}); git keeps them; `git rm` "
                         "the ones you do not need",
        # `site build`
        "map_traced": "map: {lines} lines, {boards} boards, {zones} zones, {stations} stations "
                      "in {layers} layers, {feeds} feeds; derivation {traced}",
        # the report's `## Amber`
        "report_heading": "Amber",
        "report_line": "Amber changes no status.",
    }),
    # Architecture phase 5: the front page's words, in ASD-STE100 (`_SITE_WORDS`).
    "site": _SITE_WORDS,
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


# --------------------------------------------------------------------------- #
# qualification, in words (P2.3-D13, D14): facts in, the table's words out
# --------------------------------------------------------------------------- #
def _bad_word(facts: Any) -> str:
    q = HUMAN["qualification"]
    if facts.known_bad == "fail" and facts.expect == "error":
        # Failed as its control declares, by raising: never the bare `errored`,
        # which a crashed known-bad control prints too — the same segment ended
        # `→ qualified` on one line and `→ unqualified` on the next (review of
        # P2.3; GLOSSARY §1 keeps *errored* for a crash).
        return q["raised"]
    return q["outcome"].get(facts.known_bad, facts.known_bad)


def mutation_words(fails: int, conclusive: int, inconclusive: int, boundary: str = "") -> str:
    """The mutation segment of the line: `mutation n/m fail`, or `mutation 0
    conclusive` with none conclusive (PLAN-v0.14 §1.5) — never `0/0` — then `(k
    inconclusive)` when the walk saw any, or `(none made: <why>)` when nothing
    was walked; `mutation could not finish` when the budget ran out."""
    q = HUMAN["qualification"]
    if boundary == "budget":
        return q["could_not_finish"]
    head = (q["mutation"].format(fails=fails, conclusive=conclusive) if conclusive
            else q["none"])
    if inconclusive:
        return head + q["inconclusive"].format(k=inconclusive)
    kind, _sep, tier = boundary.partition(":")
    if kind in ("no-limit", "at-limit", "tier"):
        return head + q["none_made"].format(why=q["why"][kind].format(tier=tier))
    return head


def qualification_line(gate_id: str, facts: Any) -> str:
    """One evaluator's qualification, as one line (P2.3-D14): `<id> : known-good
    <o> · known-bad <o>[ · <channel, walk or mutation segment>][ · <control
    fact>] → qualified | unqualified`. Each control's outcome in outcome words
    (GLOSSARY §6, *reject*: the walkthrough's `ok` and `rejected` are an
    evaluator's Never-says); the mutation segment only where the walk applies
    and both controls held; the ending is the one judge's
    (``verdicts._qualification``), never this function's (invariant 12, V5).
    Rendered from the facts, never stored. *Rejected:* `→ unqualified: <reason>`
    (the claim row and `gate show` carry the reason); an outcome tag before the
    id (a qualification is not an outcome, GLOSSARY §1); ` · k inconclusive`
    (reads as a fourth control)."""
    q = HUMAN["qualification"]
    words = q["outcome"]
    parts = [f"{gate_id}{q['id_sep']}{q['known_good']} "
             f"{words.get(facts.known_good, facts.known_good)}",
             f"{q['known_bad']} {_bad_word(facts)}"]
    if facts.channels:
        parts.append(q["channels"])
    elif facts.check_channel:
        parts.append(q["check_channel"])
    elif facts.walk:
        kind = verdict_logic.parse_token(facts.walk)[0]
        parts.append(q["could_not_run"] if kind == "could-not-run" else q["walk_errored"])
    elif facts.mutation is not None:
        parts.append(mutation_words(*facts.mutation, boundary=facts.boundary))
    if getattr(facts, "ledger", ()):
        parts.append(q["ledger"])
    if getattr(facts, "goalpost", ""):
        parts.append(q["goalpost"])
    if facts.blocker:
        kind = verdict_logic.parse_token(facts.blocker)[0]
        parts.append(q["blocker"].get(kind, kind))
    token = verdict_logic._qualification(facts)
    return q["sep"].join(parts) + " " + (q["unqualified"] if token else q["qualified"])


def qualification_reason(token: Any, *, pruned_by: str = "") -> str:
    """A token's words (``verdicts.parse_token`` reads it; nothing here splits
    the string itself): what follows `unqualified: <evaluator> : ` on a claim's
    row — the first fact that does not hold (P2.3-D15). ``""`` for no token.
    ``pruned_by``: the prerequisite root that prunes the evaluator, for a
    not-yet token — then it qualifies once that root is established, never at
    "the next check run", which would prune it again."""
    kind, text = verdict_logic.parse_token(token)
    if not kind:
        return ""
    if kind == "context:outside":
        return context_words(token)
    q = HUMAN["qualification"]
    template = q["reason"].get(kind)
    if template is None:
        return f"{kind}: {text}" if text else kind
    if kind == "channels:ledger":
        keys = [k for k in text.split(",") if k]
        goal = (verdict_logic.ACCEPTANCE_KEY, verdict_logic.SHAPE_KEY)
        named = sorted({k.split(":", 1)[1] for k in keys if k.startswith(goal)})
        rest = [k for k in keys if not k.startswith(goal)]
        said = ([q["acceptance_ledger"].format(claims=", ".join(named))] if named else []) \
            + ([template.format(keys=", ".join(rest))] if rest else [])
        return "; ".join(said) or template.format(keys="")
    if kind == "goalpost:moves":
        return template.format(text=text)
    if kind.startswith("channels:"):
        bad, _sep, good = text.partition("/")
        return template.format(bad=", ".join(k for k in bad.split(",") if k),
                               good=", ".join(k for k in good.split(",") if k))
    if kind == "qualification:not-yet":
        how = (q["how"]["once"].format(root=pruned_by) if pruned_by
               else q["how"]["tier"].format(tier=text) if text and text != "0"
               else q["how"]["plain"])
        return template.format(how=how)
    return template.format(text=text)


def unqualified_text(token: Any) -> str:
    """`unqualified: <the first fact that does not hold>` — an unqualified
    evaluator's own row where the evaluator is the row (the page's verdict row,
    a JUnit message), in the table's words. What slipped through (review of
    P2.3): the page rendered `v.error` and JUnit `verdict.error`, which carry
    R-2's fallback `unqualified: <token>`, so a person read
    `unqualified: qualification:not-yet|0` — the spine's token on a human
    channel P2.3-D13 says only `HUMAN` words. A pass outside its evaluator's
    operating context leads with its own fact (P2.4-D22): it is no refusal of
    the evaluator — GLOSSARY §2: *qualified* is "at its current version", and
    outside its context the evaluator still is."""
    if is_outside(token):
        return f"{HUMAN['lead'][ClaimCause.OUTSIDE_CONTEXT]}: {context_words(token)}"
    return f"{HUMAN['lead'][ClaimCause.UNQUALIFIED]}: {qualification_reason(token)}"


def is_outside(token: Any) -> bool:
    """Whether a ``Verdict.unqualified`` token is the operating-context mark."""
    return verdict_logic.parse_token(token)[0] == "context:outside"


def _interval(lo: Any, hi: Any) -> str:
    """``[a, b]``, ``[a, ∞)`` or ``(−∞, b]`` — closed where bounded."""
    left = f"[{_num(lo)}" if lo is not None else "(−∞"
    right = f"{_num(hi)}]" if hi is not None else "∞)"
    return f"{left}, {right}"


def context_words(token: Any) -> str:
    """A ``context:outside`` token's words (``HUMAN["context"]``): `load_n = 60,
    qualified on [0, 40]` — the key, what it holds now, and the range. ``""``
    for any other token."""
    from . import gates as _gates                  # not at import: report stays light
    breach = _gates.context_of(token)
    if breach is None:
        return ""
    said = HUMAN["context"]
    template = said.get(breach.why) or said["outside"]
    value = breach.value
    shown = _num(value) if isinstance(value, (int, float)) and not isinstance(value, bool) \
        else str(value)
    return template.format(key=breach.key, value=shown,
                           interval=_interval(breach.lo, breach.hi))


def context_declared(context: Mapping[str, Any]) -> str:
    """``gate show``'s row for a declared operating context: `operating context:
    load_n in [0, 40] — outside it a pass does not count; a fail still does`."""
    said = HUMAN["context"]
    ranges = ", ".join(said["range"].format(key=key, interval=_interval(*context[key]))
                       for key in sorted(context))
    return said["declared"].format(ranges=ranges)


def verdict_line(verdict: Verdict, qualification: Any = None) -> str:
    """How one verdict streams in `check`: its own row (`Verdict.render`), unless
    the evaluator is unqualified — then its qualification line, or with no facts
    the reason in the table's words. Never `[ERR ]` and never *errored* for an
    unqualified evaluator: a crash's tag on something that crashed nothing
    (P2.3-D17; the slip P2.1's review closed in the count and left in the row)."""
    token = getattr(verdict, "unqualified", "") or ""
    if not token:
        return verdict.render()
    if is_outside(token):
        # Its qualification holds (its facts say qualified): what does not is
        # this pass's inputs (P2.4-D22).
        return f"{verdict.gate}{HUMAN['qualification']['id_sep']}{unqualified_text(token)}"
    if qualification is not None:
        return qualification_line(verdict.gate, qualification)
    q = HUMAN["qualification"]
    return (f"{verdict.gate}{q['id_sep']}{HUMAN['lead'][ClaimCause.UNQUALIFIED]}: "
            f"{qualification_reason(token)}")


def _value_words(measured: Any, limit: Any, units: str) -> str:
    unit = f" {units}" if units else ""
    if measured is None:
        return ""
    if limit is None:
        return f"{_num(measured)}{unit}"
    return HUMAN["qualification"]["detail"]["value"].format(
        measured=f"{_num(measured)}{unit}", limit=f"{_num(limit)}{unit}")


def qualification_detail(entry: Any, spec: Any, *, facts: Any = None) -> list[str]:
    """`gate show`'s rows under `qualification:`, read off the control entry —
    which known-good and known-bad controls, with their values; each mutation
    with its value before and after; every read value not mutated, and why."""
    d = HUMAN["qualification"]["detail"]
    rows: list[str] = []

    def row(head: str, body: str) -> None:
        rows.append(f"    {head:<12} {body}")

    nc = getattr(spec, "negative_control", None)
    good_ref = (getattr(nc, "good", "") or "").strip() if nc is not None else ""
    good = getattr(entry, "good", None) if entry is not None else None
    if entry is not None or facts is not None:
        if isinstance(good, Mapping) and good.get("outcome") != "not-run":
            source = good_ref or (d["baseline"] if getattr(spec, "pack", "") else d["known_good_py"])
            value = _value_words(good.get("measured"), good.get("limit"), good.get("units") or "")
            row(d["known_good"], source + (f" — {value}" if value else ""))
        elif (isinstance(good, Mapping) and good.get("outcome") == "not-run") or \
                (facts is not None and facts.known_good == "not-run"):
            row(d["known_good"], d["none"])
    if isinstance(good, Mapping):
        for group in good.get("goalpost") or ():
            for r in (group.get("runs") or ()) if isinstance(group, Mapping) else ():
                row(d["goalpost"], d["goalpost_row"].format(
                    key=group.get("key"), end=d["ends"].get(r.get("end"), r.get("end")),
                    limit=_num(r.get("limit")), outcome=r.get("outcome"),
                    value=_value_words(r.get("measured"), None, r.get("units") or "")
                    or d["no_value"]))
    if entry is not None:
        value = _value_words(entry.measured, entry.limit, entry.units or "")
        fixture = getattr(nc, "fixture", "") if nc is not None else ""
        row(d["known_bad"], fixture + (f" — {value}" if value else ""))
        walk = entry.mutation if isinstance(entry.mutation, Mapping) else None
        if walk is not None:
            kind, _sep, tier = (walk.get("boundary") or "").partition(":")
            if kind in ("no-limit", "at-limit", "tier", "budget"):
                row(d["mutation"], d[kind].format(tier=tier))
            for r in walk.get("results") or ():
                key = ".".join(str(p) for p in r["key"])
                body = d["result"].format(key=key, before=_num(r["before"]),
                                          after=_num(r["after"]), outcome=r["outcome"])
                value = _value_words(r.get("measured"), r.get("limit"), entry.units or "")
                row(d["mutation"], body + (f" — {value}" if value else ""))
            for r in walk.get("inconclusive") or ():
                key = ".".join(str(p) for p in r["key"])
                row(d["mutation"], d["inconclusive"].format(key=key, after=_num(r["after"]),
                                                            outcome=r["outcome"],
                                                            why=r.get("why") or ""))
            for r in walk.get("not_mutated") or ():
                key = ".".join(str(p) for p in r["key"])
                why = {"none": "none_value", "budget": "budget_key"}.get(r["why"], r["why"])
                row(d["not_mutated"], f"{key} — {_not_mutated_words(d, why, r['why'])}")
    return rows


def _not_mutated_words(d: Mapping[str, str], why: str, said: str) -> str:
    """A not-mutated reason's words; ``never-lands`` filled from the walk's own
    constants — the ladder's ends (``1/MUTATION_RUNGS[-1]`` to
    ``MUTATION_RUNGS[-1]``) and ``MUTATION_MARGIN`` — at render time."""
    template = d.get(why)
    if template is None:
        return said
    if why != "never-lands":
        return template
    from . import gates as _gates                  # not at import: report stays light
    top = max(_gates.MUTATION_RUNGS)
    return template.format(lo=_num(1.0 / top), hi=_num(top),
                           margin=_num(_gates.MUTATION_MARGIN * 100))


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
    traceback; a skip `skipped: <gate> : <reason>`; an evaluator not run
    behind a prerequisite `skipped: <gate> : prerequisite failed: <root>` (or
    `errored: …` when the root crashed: `prerequisite_phrase`); a refused
    evaluator `unqualified: <gate> : <why>`; an unrun one `unrun: <gates>`; an invalidated
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
    if cause is ClaimCause.ACCEPTANCE and verdict is not None:
        said = HUMAN["acceptance"]
        unit = f" {verdict.units}" if verdict.units else ""
        body = (said["reason"] if verdict.limit is not None else said["reason_no_limit"]).format(
            gate=verdict.gate, value=f"{_num(verdict.measured)}{unit}", claim=claim.id,
            condition=claim.acceptance.render(), limit=f"{_num(verdict.limit)}{unit}")
        return f"{lead}: {body}"
    if cause in (ClaimCause.OUTSIDE_CONTEXT, ClaimCause.FALLBACK) and verdict is not None:
        words_ = context_words(verdict.unqualified)
        if cause is ClaimCause.FALLBACK:
            return f"{lead.format(owner=_one(claim.owner))}: {verdict.gate} : {words_}"
        text = f"{lead}: {verdict.gate} : {words_}"
        if not full:
            return text
        said = HUMAN["context"]
        owner = str(getattr(claim, "owner", "") or "").strip()
        fallback = str(getattr(claim, "fallback", "") or "").strip()
        hint = (said["fallback_unattributed"].format(owner=_one(owner), id=claim.id)
                if owner and fallback else said["fallback_hint"].format(id=claim.id))
        return f"{text} — {hint}"
    if cause is ClaimCause.FAILED and verdict is not None:
        body = _verdict_body(verdict)
        text = f"{verdict.gate} : {cut_(body, 56)}" if body else f"{verdict.gate} did not pass"
        moved = stale_reasons.get(verdict.gate)
        if moved and claim_logic.unseen_reason(moved):
            # Issue #15, review of D1: the fail of an evaluator nopekit cannot
            # see into is the last check run's, and still counts — never
            # "invalidated" of a design nobody touched, and never its channel
            # list (often 2 KB of solver paths: `doctor`'s).
            text += f" ({HUMAN['unseen']['failed']})"
        elif moved:
            text += f" ({HUMAN['lead'][ClaimCause.INVALIDATED]}: {cut_(moved, 80)})"
        return text
    if cause in (ClaimCause.PHYSICAL_FAIL, ClaimCause.CONTRADICTION, ClaimCause.JUDGED_FAIL):
        return _fail_reason(cause, claim, cut_)
    if cause in (ClaimCause.PREREQUISITE, ClaimCause.PREREQUISITE_ERRORED) \
            and verdict is not None:
        # Cut at 96, not the 56 a gate's own words get: the root's id is the
        # actionable half, and the phrase around it is bounded.
        return f"{lead}: {verdict.gate} : {cut_(prerequisite_phrase(verdict), 96)}"
    if cause in (ClaimCause.ERRORED, ClaimCause.SKIPPED, ClaimCause.UNQUALIFIED) \
            and verdict is not None:
        if cause is ClaimCause.UNQUALIFIED:
            body = (qualification_reason(verdict.unqualified) if verdict.unqualified
                    else _verdict_body(verdict))
        else:
            body = _verdict_body(verdict)
        return f"{lead}: {verdict.gate} : {cut_(body, 56)}" if body \
            else f"{lead}: {verdict.gate}"
    if cause is ClaimCause.NO_OWNER:
        return lead + (f" — an assumption reads Assumed only once its owner records it: "
                       f"name the owner in claims/{claim.id}.json (\"owner\"), and they run "
                       f"nopekit claim physical {claim.id} assume in their own shell"
                       if full else "")
    if cause is ClaimCause.OWNER_UNATTRIBUTED:
        return lead.format(owner=_one(claim.owner), id=claim.id)
    if cause in _PHYSICAL_CAUSES:
        return _physical_reason(composed, ledger, claim, full=full, cut_=cut_,
                                stale_reasons=stale_reasons)
    if cause is ClaimCause.UNRUN:
        shown = ", ".join(composed.cites[:3])
        more = f", +{len(composed.cites) - 3} more" if len(composed.cites) > 3 else ""
        return f"{lead}: {shown}{more}"
    if cause is ClaimCause.UNSEEN:
        # Issue #15, D1: the gate, then the one remedy. Never its channel
        # list, which is `doctor`'s (often 2 KB of solver paths).
        more = (f" (+{len(composed.cites) - 1} more)" if len(composed.cites) > 1 else "")
        return (f"{lead}: {', '.join(composed.cites[:1]) or 'every verdict'}{more}; "
                f"{HUMAN['unseen']['only']}")
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
        standing = getattr(claim, "standing", None)
        if standing is None or not str(getattr(standing, "state", "")).startswith(
                "not-counted:"):
            # A raw ledger nobody judged: P2.1's words, unchanged (P2.5a-D13).
            result = claim.physical_result
            return lead.format(recorded=recorded_by(result.who if result else ""))
        entry = _entry(claim, standing.counted)
        return (f"{_not_counted(claim, standing.state[len('not-counted:'):])} "
                f"({recorded_by(getattr(entry, 'who', ''))})")
    return lead


def prerequisite_phrase(verdict: Verdict) -> str:
    """`prerequisite failed: <root>` / `prerequisite not established: <root>
    (<kind>)` for a verdict not run behind a prerequisite — from `HUMAN` and the
    spine's mark (`blocked_by`, `blocked_kind`), never from its `skip_reason`
    text (a gate cannot write the mark; it can write a reason)."""
    said = HUMAN["prerequisite"]
    root = (list(verdict.blocked_by) or [""])[0]
    kind = str(getattr(verdict, "blocked_kind", "") or "")
    if kind == "failed":
        return said["failed"].format(root=root)
    word = {"errored": HUMAN["outcome"]["error"], "skipped": HUMAN["outcome"]["skipped"],
            "unqualified": HUMAN["lead"][ClaimCause.UNQUALIFIED],
            "not-registered": said["not-registered"]}.get(kind, HUMAN["outcome"]["skipped"])
    return said["not-established"].format(root=root, kind=word)


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
    """`recorded by dana`, or `recorded, unattributed` with no one named
    (`HUMAN["recorded"]`; GLOSSARY §1, *recorded by*) — the one place a
    physical result's recorder is worded, on one line."""
    name = _one(who)
    said = HUMAN["recorded"]
    return said["named"].format(who=name) if name else said["unattributed"]


#: The causes `_physical_reason` words (P2.5a): each about a physical result or
#: an expert judgment, never an evaluator's verdict.
_PHYSICAL_CAUSES = frozenset({
    ClaimCause.NO_AUTHORITY, ClaimCause.AUTHORITY_UNATTRIBUTED, ClaimCause.ARTICLE_MOVED,
    ClaimCause.CLAIM_MOVED, ClaimCause.JUDGMENT_MOVED, ClaimCause.ARTICLE_UNJUDGED,
    ClaimCause.AWAITING_JUDGMENT, ClaimCause.ON_ARTICLE, ClaimCause.JUDGED})


def article12(article: Any) -> str:
    """An article as it prints: the first 12 hex of its hash (P2.5a-D8)."""
    found = article.get("hash") if isinstance(article, Mapping) else article
    return str(found or "")[:12]


def _entry(claim: Claim, index: Any) -> Any:
    results = list(getattr(claim, "results", ()) or ())
    if isinstance(index, int) and 0 <= index < len(results):
        return results[index]
    return claim.physical_result


def recorded_words(entry: Any) -> str:
    """Who recorded a physical result, and from where when that is not their own
    shell — `recorded by Dana`, `recorded by Dana, from an agent session`, `…,
    from a pipe or a script` (P2.5a-D18; GLOSSARY *recorded by*). A legacy
    entry, recorded before results carried a channel, reads as P2.1 worded it."""
    if entry is None:
        return recorded_by("")
    name = _one(getattr(entry, "who", ""))
    channel = str(getattr(entry, "channel", "") or "")
    said = HUMAN["recorded"]
    if not name:
        return said["unattributed"]
    if channel.startswith("agent-session"):
        return said["agent"].format(who=name)
    if channel == "non-interactive":
        return said["non_interactive"].format(who=name)
    return said["named"].format(who=name)


def terminal_word(claim: Claim) -> str:
    """Where ``claim``'s evidence bottoms out, in its display word (P2.5a-D1,
    D20): *measurement*, *expert judgment: <authority>*, *assumption*, or
    *automated* for every automated claim, declared or not."""
    said = HUMAN["terminal"]
    try:
        terminal = claim_logic.terminal_of(claim)
    except Exception:                                      # noqa: BLE001 — an unknown value
        return said["automated"]
    if terminal == "human":
        authority = _one(getattr(claim, "authority", ""))
        return said["human"].format(authority=authority) if authority \
            else said["human_unnamed"]
    if terminal in ("measurement", "none"):
        return said[terminal]
    return said["automated"]


def _not_counted(claim: Claim, why: str) -> str:
    """Why a pass does not count, in words (`HUMAN["not_counted"]`)."""
    judgment = getattr(claim, "terminal", "") == "human"
    table = HUMAN["judgment_not_counted"] if judgment else HUMAN["not_counted"]
    key, _sep, rest = why.partition(":")
    template = table.get(key) or HUMAN["not_counted"].get(key) or why
    return template.format(path=rest, authority=_one(getattr(claim, "authority", "")))


def _moves(standing: Any, limit: int | None = None) -> str:
    moved = [str(m) for m in (getattr(standing, "moved", ()) or ())]
    return verdict_logic._stale_text(moved) if moved else "the design moved"


def _judgment(claim: Claim) -> bool:
    """Whether ``claim`` ends in an expert judgment (terminal ``human``) — the
    one test every judgment-worded line asks (review of P2.5a)."""
    try:
        return claim_logic.terminal_of(claim) == "human"
    except Exception:                                      # noqa: BLE001 — an unknown value
        return False


def _fail_reason(cause: Any, claim: Claim, cut_: Callable[[str, int], str]) -> str:
    """A physical fail's reason: `failed on article <a>: <detail> (<recorded>)`
    — led by `contradiction:` and ended by the evaluator it contradicts when it
    carries one; the authority's own no in a judgment's words; and, when its
    article moved, `(invalidated: …)` — a fail keeps its power to fail across
    every change (R-3), and the reader learns which print the fix needs.

    A fail on an expert-judgment claim names no article and never "a new
    article is needed" (review of P2.5a: the row said a rebuild was needed for
    C8 while the rebuild prediction, rightly, left a judgment out — a person
    settles it, not a print). A fail whose claim file is gone says what to
    restore (`Ledger.removed`)."""
    result = claim.physical_result
    said = HUMAN["physical"]
    lead = HUMAN["lead"][cause]
    detail = cut_(result.detail if result and result.detail else "no detail recorded", 60)
    article = article12(getattr(result, "article", None) or {})
    standing = getattr(claim, "standing", None)
    removed = (" " + said["removed_tail"].format(id=claim.id)
               if getattr(standing, "state", "") == verdict_logic.REMOVED else "")
    if cause is ClaimCause.JUDGED_FAIL:
        return (f"{lead.format(authority=_one(claim.authority))}: {detail} "
                f"({_one(getattr(result, 'when', ''))})") + removed
    judgment = _judgment(claim)
    head = (said["failed_judgment"] if judgment
            else said["failed_on"].format(article=article) if article else said["failed"])
    text = f"{head}: {detail} ({recorded_words(result)})"
    entry = None
    if standing is not None and result is not None:
        results = list(getattr(claim, "results", ()) or ())
        index = max((i for i, item in enumerate(results) if item == result), default=None)
        entry = next((e for e in getattr(standing, "entries", ()) or ()
                      if e.index == index), None)
    if entry is not None and entry.article_state == "moved" and not judgment:
        text += " " + said["fail_moved"].format(
            article=article, moves=verdict_logic._stale_text(entry.moved))
    if cause is ClaimCause.CONTRADICTION:
        bits = []
        for item in getattr(result, "contradicts", None) or ():
            if isinstance(item, Mapping) and item.get("inside") is True:
                unit = f" {item.get('units')}" if item.get("units") else ""
                bits.append(said["had_passed"].format(
                    gate=item.get("gate"), value=f"{_num(item.get('value'))}{unit}"))
        text = f"{lead}: {text}" + (f" — {'; '.join(bits)}" if bits else "")
    return text + removed


def _physical_reason(composed: Any, ledger: Ledger, claim: Claim, *, full: bool,
                     cut_: Callable[[str, int], str],
                     stale_reasons: Mapping[str, str] | None) -> str:
    """The reason of a claim whose status a physical result or an expert
    judgment set (P2.5a's causes): led by the fact, naming the article, who
    recorded it, and — for a Stale one — what a person must do, which a check
    run cannot."""
    cause = composed.cause
    lead = HUMAN["lead"][cause]
    said = HUMAN["physical"]
    standing = getattr(claim, "standing", None)
    authority = _one(getattr(claim, "authority", ""))
    entry = _entry(claim, getattr(standing, "counted", None))
    article = article12(getattr(standing, "article", "") or
                        (getattr(entry, "article", None) or {}))
    if cause is ClaimCause.NO_AUTHORITY:
        return lead + (f" — {said['no_authority_full'].format(id=claim.id)}" if full else "")
    if cause is ClaimCause.AUTHORITY_UNATTRIBUTED:
        text = (f"{lead.format(authority=authority, id=claim.id)} — "
                f"{said['authority_act'].format(authority=authority, id=claim.id)}")
        return text + _uncounted_tail(claim, standing)
    if cause is ClaimCause.AWAITING_JUDGMENT:
        text = lead.format(authority=authority)
        if claim.rationale:
            text += f": {cut_(claim.rationale, 52)}"
        return text + _uncounted_tail(claim, standing)
    if cause is ClaimCause.ON_ARTICLE:
        text = f"{lead.format(article=article)} ({recorded_words(entry)})"
        superseded = tuple(getattr(standing, "superseded", ()) or ())
        if superseded:
            # P2.5b-D14: the fail it superseded named on the row, and kept on
            # the evaluator's track record.
            failed = _entry(claim, superseded[-1])
            text += " " + said["superseded"].format(
                article=article12(getattr(failed, "article", None) or {}))
        return text
    if cause is ClaimCause.JUDGED:
        return f"{lead.format(authority=authority)} ({_one(getattr(entry, 'when', ''))})"
    cited = ""
    named = [g for g in composed.cites if (stale_reasons or {}).get(g)]
    if composed.cites:
        gate = named[0] if named else composed.cites[0]
        why = (stale_reasons or {}).get(gate)
        cited = (f" (and {HUMAN['lead'][ClaimCause.INVALIDATED]}: {gate}"
                 + (f" : {cut_(why, 56)}" if why else "") + ")")
    if cause is ClaimCause.ARTICLE_MOVED:
        return (f"{lead}: " + said["moved_tail"].format(article=article,
                                                        moves=_moves(standing)) + cited)
    if cause is ClaimCause.CLAIM_MOVED and _judgment(claim):
        return said["judgment_claim_moved"].format(id=claim.id, authority=authority) + cited
    if cause is ClaimCause.CLAIM_MOVED:
        return (f"{lead.format(id=claim.id, article=article)} — "
                f"{said['claim_moved_tail']}" + cited)
    if cause is ClaimCause.JUDGMENT_MOVED:
        return (f"{lead.format(authority=authority)}: "
                + said["judgment_moved_tail"].format(moves=_moves(standing),
                                                     authority=authority) + cited)
    if cause is ClaimCause.ARTICLE_UNJUDGED and _judgment(claim):
        return (f"{said['judgment_unjudged'].format(authority=authority)} — "
                f"{said['judgment_unjudged_tail']}" + cited)
    if cause is ClaimCause.ARTICLE_UNJUDGED:
        return f"{lead.format(article=article)} — {said['unjudged_tail']}" + cited
    return lead


def _uncounted_tail(claim: Claim, standing: Any) -> str:
    """`, and <why a judgment did not count>` when a pass sits beside an
    unsettled judgment — said, so a reader who recorded one learns why it
    settles nothing (V-11 row g)."""
    state = str(getattr(standing, "state", "") or "")
    if state == "claim-moved" and _judgment(claim):
        # A judgment of earlier words (review of P2.5a): the claim reads as one
        # written fresh, and says why its judgment no longer counts.
        return " — " + HUMAN["physical"]["judgment_claim_moved"].format(
            id=claim.id, authority=_one(getattr(claim, "authority", "")))
    if not state.startswith("not-counted:"):
        return ""
    return " — " + _not_counted(claim, state[len("not-counted:"):])


def rebuild_line(found: Any) -> str:
    """`rebuild: article <a12> (C5, C9) — config.thickness 7.0 -> 7.5` — one
    article of the rebuild prediction (P2.5a-D16), as `check` and `status`
    print it. A prediction, never "you must"."""
    return HUMAN["physical"]["rebuild"].format(
        article=article12(found.article), claims=", ".join(found.claims),
        moves=verdict_logic._stale_text(found.moved) if found.moved else "the design moved")


def track_words(gate_id: str, contradictions: Iterable[Any], code_now: str) -> str:
    """`gate show`'s row (P2.5a-D14): `track record: N contradiction(s) at this
    version, M at earlier versions — <the newest>`. Keyed by the evaluator's
    code digest, so a contradiction at an earlier version never reads as this
    one's (V-10)."""
    found = list(contradictions or ())
    now = [c for c in found if c.code == code_now]
    earlier = [c for c in found if c.code != code_now]
    said = HUMAN["physical"]
    text = said["track"].format(
        now=f"{len(now)} {_plural(len(now), 'contradiction')}", earlier=len(earlier))
    newest = (now or earlier)[-1] if found else None
    if newest is not None:
        unit = f" {newest.units}" if newest.units else ""
        claim = newest.claim + (f" ({said['removed_tag']})"
                                if getattr(newest, "removed", False) else "")
        text += " — " + said["track_one"].format(
            claim=claim, article=article12(newest.article),
            when=_one(newest.when)[:10], recorded=recorded_words(newest),
            measured=(f"{_num(newest.measured)}{unit}" if newest.measured is not None
                      else "a fail"),
            value=f"{_num(newest.value)}{unit}")
    return text


def claim_json(claim: Claim, *, composed: Any = None,
               exports: Iterable[Any] | None = None) -> dict[str, Any]:
    """A claim as every JSON channel shows it (P2.5a-D20; additive, P2.1-D12):
    its record fields and `physical_result`, never the in-memory `results`,
    `attributions` and `standing` objects, and beside them `terminal` (as
    declared), `terminal_word`, `authority`, `article` (the deciding result's
    hash), `standing` (the judge's state) and `contradicts` (evaluator ids).
    From P2.5b, additive: `superseded` (the fails a pass on another exported
    article superseded, by index) and `latency` (`{seconds, source, article}`:
    measured from an export, else declared — `claims.latency`). From phase 1
    (SPEC decision 13), additive: `mechanism` — `{by, how, evaluators}` as
    declared, all three keys always present — in place of a flat `evaluators`
    (one spelling per channel); `requirements` stays flat, as the record holds it."""
    row = claim.to_dict()
    for name in ("results", "attributions", "standing", "evaluators"):
        row.pop(name, None)
    standing = getattr(claim, "standing", None)
    row["terminal"] = str(getattr(claim, "terminal", "") or "")
    row["terminal_word"] = terminal_word(claim)
    row["authority"] = str(getattr(claim, "authority", "") or "")
    row["article"] = str(getattr(standing, "article", "") or "")
    row["standing"] = str(getattr(standing, "state", "") or "")
    row["contradicts"] = list(composed.cites) if composed is not None and \
        composed.cause is ClaimCause.CONTRADICTION else []
    row["superseded"] = list(getattr(standing, "superseded", ()) or ())
    found = claim_logic.latency(claim, exports or ())
    row["latency"] = {"seconds": found.seconds, "source": found.source,
                      "article": found.article}
    row["mechanism"] = {"by": str(getattr(claim, "terminal", "") or ""),
                        "how": str(getattr(claim, "note", "") or ""),
                        "evaluators": list(getattr(claim, "evaluators", None) or ())}
    if claim.physical_result is not None and isinstance(row.get("physical_result"), dict):
        row["physical_result"].update(result_facts(claim, claim.physical_result))
    return row


def mechanism_body(claim: Claim) -> str:
    """`<by> — <how>` (SPEC decision 13): where the claim's evidence bottoms
    out, in its display word (`terminal_word`, never the raw token), and in
    words what would settle it — or that nothing says so yet, and where to say
    it. One line: `how` comes from a record (`_one`). Unlabelled, for a reader
    that labels it itself (the report's `**Mechanism:**`, `why`'s MECHANISM)."""
    said = HUMAN["mechanism"]
    how = _one(getattr(claim, "note", ""))
    if how:
        return said["body"].format(by=terminal_word(claim), how=how)
    return said["unsaid"].format(by=terminal_word(claim), id=claim.id)


def mechanism_line(claim: Claim) -> str:
    """`mechanism: <by> — <how>` — `mechanism_body`, labelled: `gap`'s line."""
    return HUMAN["mechanism"]["line"].format(body=mechanism_body(claim))


def mechanism_problem_words(problem: Any) -> str:
    """One `claims.MechanismProblem` in words — the line `check` warns with and
    `doctor` lists: the claim, the evaluator its mechanism names, and that no
    status moves."""
    said = HUMAN["mechanism"]
    return said[problem.kind].format(
        id=problem.claim, evaluator=problem.evaluator,
        filed=", ".join(problem.filed) or said["filed_none"])


#: How many requirements `status`'s one line names before `+N more`, and how
#: many of a requirement's worst claims each names. Eight keeps the line near
#: one screen width for the ids a project numbers R1..R8; *rejected:* one row
#: per requirement (lines out of status's ~40-line budget, and a `[tag]` row the
#: claim-row parsers would read as a claim).
_MAX_REQUIREMENT_ITEMS = 8
_MAX_WORST_IDS = 3


def status_word(status: Any, *, errored: bool = False) -> str:
    """A status's word with a crash said apart — `skipped, errored` — as the
    report's section heads say it (invariant 2: louder than a missing tool,
    wherever a status is rolled up and its tag is not printed beside it)."""
    shown = word(status, errored=errored)
    if errored and _norm_status(status) is ClaimStatus.BLOCKED:
        shown += f", {HUMAN['lead'][ClaimCause.ERRORED]}"
    return shown


# --------------------------------------------------------------------------- #
# rejected alternatives (SPEC 14, §0.2): `claims.rejection_rows` in words
# --------------------------------------------------------------------------- #
def condition_text(cond: Any) -> str:
    """A structured return condition as written — `C3 margin < 0.1` — or its
    text; `""` for none."""
    if cond is None:
        return ""
    text = str(getattr(cond, "text", "") or "")
    if text:
        return _one(text)
    return HUMAN["rejection"]["condition"].format(
        claim=_one(getattr(cond, "claim", "")), quantity=getattr(cond, "quantity", ""),
        comparator=getattr(cond, "comparator", ""), value=_num(getattr(cond, "value", "")))


def _type_phrase(kind: str, preference: Any, by: str = "") -> str:
    said = HUMAN["rejection"]
    if kind == "preferred_against":
        if preference is not None and getattr(preference, "state", "") == "recorded":
            return said["recorded"].format(name=_one(preference.name),
                                           when=_one(preference.when))
        name = getattr(preference, "name", "") if preference is not None else by
        return said["not_recorded"].format(name=_one(name) or "no one")
    return said["types"].get(kind or "untyped", _one(kind))


def _status_token_word(token: str) -> str:
    """A `STATUS_KEY` token (or `errored`) as its lower-case word."""
    if token == "errored":
        return status_word(ClaimStatus.BLOCKED, errored=True)
    for status, key in claim_logic.STATUS_KEY.items():
        if key == token:
            return word(status)
    return _one(token)


#: The public name of `_status_token_word` (architecture phase 3a): `round`'s
#: summary says a flipped status token in the same words as a return condition,
#: and the private name stays for its callers.
status_token_word = _status_token_word


def round_moved(moved: Iterable[str]) -> str:
    """`the records, the verdict cache and the design` — what moved since a
    round record, in `HUMAN["round"]["moved"]`'s words, in the record's order."""
    said = HUMAN["round"]["moved"]
    names = [said.get(key, key) for key in moved]
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + " and " + names[-1]


def _flip_words(flip: Any) -> str:
    said = HUMAN["round"]
    if flip.before is None:
        return said["appeared"].format(id=flip.id, after=_status_token_word(flip.after))
    if flip.after is None:
        return said["gone"].format(id=flip.id, before=_status_token_word(flip.before))
    return said["flip"].format(id=flip.id, before=_status_token_word(flip.before),
                               after=_status_token_word(flip.after))


#: How many claim and requirement flips `round`'s paragraph names before `and N
#: more`, and how many new verdicts it names. Why 8: the status line's
#: requirement cap — one short paragraph is the hook's whole message, and a
#: round that flipped thirty claims says how many, never all thirty. *Rejected:*
#: every flip (a wall in a hook's message); none (the one flip a person needs
#: is the reason the paragraph exists).
_ROUND_FLIPS = 8
_ROUND_VERDICT_IDS = 4


def _round_changes(changes: Any) -> str:
    said = HUMAN["round"]
    flips = [_flip_words(f) for f in (*changes.claims, *changes.requirements)]
    parts: list[str] = []
    if flips:
        shown = flips[:_ROUND_FLIPS]
        if len(flips) > _ROUND_FLIPS:
            shown.append(said["more"].format(n=len(flips) - _ROUND_FLIPS))
        parts.append(", ".join(shown))
    if changes.verdicts:
        ids = list(changes.verdicts[:_ROUND_VERDICT_IDS])
        if len(changes.verdicts) > _ROUND_VERDICT_IDS:
            ids.append(said["ids_more"].format(k=len(changes.verdicts) - _ROUND_VERDICT_IDS))
        parts.append(said["verdicts"].format(n=len(changes.verdicts),
                                             verdicts=_plural(len(changes.verdicts), "verdict"),
                                             ids=", ".join(ids)))
    if changes.instructions:
        parts.append(said["instructions"].format(paths=", ".join(
            nid.split(":", 1)[-1] for nid in changes.instructions)))
    if getattr(changes, "unrecorded", False):
        parts.append(said["instructions_unrecorded"])
    if not parts:
        parts.append(said["no_status"].format(what=round_moved(changes.moved)
                                              or round_moved(("records",))))
    return "; ".join(parts)


def round_paragraph(shape: str, number: Any, changes: Any, *, view: Ledger,
                    composed: Mapping[str, Any], check: Mapping[str, Any],
                    site: Mapping[str, Any], wrote: bool, schema: Any, path: str,
                    trace: Mapping[str, Any] | None = None) -> str:
    """`nopekit round`'s one paragraph (architecture phase 3a): what changed since
    the last round record (``shape``: ``written``, ``first``, ``replaced``,
    ``unchanged`` or ``newer``), the count line, *ready* as last evaluated
    (`claims.unresolved`, through `readiness`), what the tier-0 check did, the
    site, the write, and (phase 3b) the background trace when one was started
    or is running — ``trace`` ``{"state", "log", "why"}``; nothing when the
    trace is current. One paragraph, because it is the Stop hook's message
    (phase 6)."""
    said = HUMAN["round"]
    if shape == "newer":
        opening = said["newer"].format(path=path, schema=schema)
    elif shape == "first":
        opening = said["first"]
    elif shape == "replaced":
        opening = said["replaced"].format(
            n=number, how=said["replaced_how"].get(changes.replaced, changes.replaced))
    elif shape == "unchanged":
        opening = said["unchanged"].format(n=number)
    else:
        opening = said["written"].format(n=number, changes=_round_changes(changes))
    found = readiness(view, composed)
    if found["ready"]:
        ready = said["ready"]
    else:
        k = len(found["unresolved"]) + len(found["missing"])
        n = len(found["required"]) + len(found["missing"])
        ready = said["not_ready"].format(k=k, n=n, claims=_plural(n, "claim"),
                                         are="is" if k == 1 else "are")
    if check.get("ran"):
        ran = said["check_ran"].format(executed=check.get("executed", 0),
                                       cached=check.get("cached", 0))
    else:
        ran = said["check_not_run"].format(why=str(check.get("why") or "").rstrip("."))
    if not site.get("present"):
        built = ""
    elif site.get("built") and site.get("warnings"):
        built = said["site_warnings"].format(n=site["warnings"],
                                             warnings=_plural(site["warnings"], "warning"))
    elif site.get("built"):
        built = said["site_built"]
    else:
        built = said["site_failed"].format(why=str(site.get("why") or "").rstrip("."))
    tail = said["wrote"].format(path=path) if wrote else ""
    return f"{opening} {count_line(composed)}. {ready} {ran}{built}{tail}" \
        f"{round_trace_sentence(trace)}"


def round_trace_sentence(trace: Mapping[str, Any] | None) -> str:
    """The background trace's sentence in `round`'s paragraph (architecture
    phase 3b), with its leading space: ``trace`` ``{"state", "log", "why"}``,
    the state `cli._round_start_trace` returns — ``started``, ``amber``,
    ``running``, ``incomplete`` or ``failed`` — else ``""``. One home for the
    words, because phase 6 appends it after the commit's sentence, where
    `round_paragraph` cannot."""
    said = HUMAN["round"]
    state = str((trace or {}).get("state") or "")
    log = str((trace or {}).get("log") or "")
    return {"started": said["trace_started"].format(log=log),
            "amber": said["trace_amber"].format(log=log),
            "running": said["trace_running"].format(log=log),
            "incomplete": said["trace_incomplete"],
            "failed": said["trace_failed"].format(
                why=str((trace or {}).get("why") or "").rstrip("."))}.get(state, "")


# --------------------------------------------------------------------------- #
# the round's commit (architecture phase 6, SPEC 24 with §0.3)
# --------------------------------------------------------------------------- #
#: How many paths a not-staged sentence names before ", and k more", and how many
#: boards the prose sentence names. One short paragraph is the hook's whole
#: message. *Rejected:* every path (a stray build directory of a thousand files
#: would be the message); none (the person must see what the round left out).
_ROUND_PATHS = 3
_ROUND_SUBJECTS = 4

#: The commit subject's limit: git's own convention (`git log --oneline` and
#: every forge cut a longer one). *Rejected:* no limit (a thirty-claim flip list
#: as a subject); 50 (most rounds' first flip would not fit).
COMMIT_SUBJECT_MAX = 72


def _paths_words(paths: Sequence[str], limit: int = _ROUND_PATHS) -> str:
    shown = ", ".join(str(p) for p in paths[:limit])
    if len(paths) > limit:
        shown += HUMAN["round"]["paths_more"].format(k=len(paths) - limit)
    return shown


def _sized(items: Sequence[Any]) -> list[str]:
    out = []
    for item in items:
        path, size = (item[0], item[1]) if isinstance(item, (tuple, list)) else \
            (item.get("path"), item.get("bytes"))
        size = int(size)
        out.append(f"{path} ({size / (1024 * 1024):.0f} MiB)" if size >= 1024 * 1024
                   else f"{path} ({size / 1024:.0f} KiB)")
    return out


def round_commit_sentence(commit: Any) -> str:
    """The sentences `round --commit` adds to the paragraph: what the commit
    did (``commit.why``: ``committed``, ``nothing-changed``, or why not), then
    the changed paths it named instead of staging — a name that looks like a
    secret, a file over the cap, what is not a file. ``commit`` is
    `cli.CommitOutcome`'s shape."""
    said = HUMAN["round"]
    why = getattr(commit, "why", "")
    named = [(key, list(getattr(commit, key, ()) or ()))
             for key in ("secret", "too_large", "not_files")]
    if why == "committed":
        n = len(commit.staged)
        text = said["committed"].format(oid=(commit.oid or "")[:7], who=commit.who,
                                        n=n, files=_plural(n, "file"))
    elif why == "nothing-changed":
        text = said["nothing_named"] if any(paths for _key, paths in named) else said["nothing"]
    elif why == "no-repository":
        text = said["no_repository"]
    elif why == "no-identity":
        text = said["no_identity"]
    elif why == "no-people":
        text = said["no_people"]
    elif why == "not-listed":
        text = said["not_listed"].format(who=commit.who)
    elif why == "committer-not-listed":
        text = said["committer_not_listed"].format(who=commit.who)
    elif why == "ignored":
        text = said["ignored"]
    elif why == "in-progress":
        text = said["in_progress"].format(operation=commit.detail)
    elif why == "detached":
        text = said["detached"]
    elif why == "newer-record":
        text = said["newer_record"]
    elif why == "add-failed":
        text = said["add_failed"].format(why=commit.detail)
    elif why == "unlisted":
        text = said["unlisted_changes"]
    elif why == "timeout":
        text = said["timeout"].format(s=commit.detail)
    else:
        text = said["refused"].format(why=commit.detail or why)
    for key, paths in named:
        if paths:
            text += said[key].format(paths=_paths_words(_sized(paths) if key == "too_large"
                                                        else paths))
    return text


def round_prose_sentence(prose: Iterable[Mapping[str, Any]]) -> str:
    """`" Board text written before the latest results: strength — …"`, or ``""``
    when every board's text is stamped against the current results (``prose``:
    `page.stamp_states`' rows). A file that names no board is not on the page,
    and `doctor` says so; it is not a board to restamp."""
    behind = [str(row.get("subject")) for row in prose
              if row.get("subject") and row.get("stamp") != "current"]
    if not behind:
        return ""
    return HUMAN["round"]["prose_behind"].format(
        subjects=_paths_words(behind, _ROUND_SUBJECTS))


#: Which `commit_parts` word a staged path falls under, by its first path part
#: (two parts under `.nopekit/`), in the order the subject names them. `files`
#: is a directory nopekit does not name (the acceptance run after phase 3b: a
#: round stages them now — `bom/`, `build/` — and they are not records).
_PART_ORDER = ("records", "model", "evaluators", "generators", "verdicts", "trace", "packs",
               "site_text", "site", "round", "project", "files")


def _commit_part(rel: str, round_rel: str = "") -> str:
    parts = str(rel).split("/")
    if round_rel and rel == round_rel:
        return "round"
    if parts[0] == store.NOPEKIT_DIR and len(parts) > 1:
        second = parts[1]
        if second == store.VERDICTS_NAME:
            return "verdicts"
        if second == "trace":
            return "trace"
        if second == store.PACKS_NAME:
            return "packs"
        return "project"
    head = parts[0]
    if len(parts) == 1:
        return "project"
    if head == "model":
        return "model"
    if head in ("gates", "selftest"):
        return "evaluators"
    if head == "generators":
        return "generators"
    if head == "site":
        return "site_text" if len(parts) > 2 and parts[1] == "content" else "site"
    if head in store.RECORD_DIRS:
        return "records"
    return "files"


def _commit_group(rel: str) -> str:
    parts = str(rel).split("/")
    if parts[0] == store.NOPEKIT_DIR and len(parts) > 2:
        return "/".join(parts[:2])
    return parts[0]


def _and_list(items: Sequence[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def _wrap_message(text: str, width: int = COMMIT_SUBJECT_MAX) -> str:
    """One paragraph wrapped at ``width``, never leaving a line that starts with
    one token and a colon: `git interpret-trailers` reads such a line in the
    last paragraph as a trailer, and the commit carries none (the house rule:
    no attribution, nothing trailer-shaped)."""
    import textwrap
    lines = textwrap.wrap(" ".join(text.split()), width=width, break_long_words=False,
                          break_on_hyphens=False)
    out: list[str] = []
    for line in lines:
        if out and re.match(r"^[^\s:]+:(\s|$)", line):
            out[-1] = out[-1] + " " + line
        else:
            out.append(line)
    return "\n".join(out)


def round_commit_message(number: Any, shape: str, changes: Any, staged: Sequence[str],
                         not_files: Sequence[str], too_large: Sequence[Any],
                         secret: Sequence[str], earlier: tuple | None, paragraph: str, *,
                         round_rel: str = "") -> tuple[str, str]:
    """``(subject, body)`` of a round's commit (SPEC 24): ``round N: <what
    changed>`` — the flips and new verdicts when the record was written, ``the
    first round record`` for round 1, else which kinds of the project's files
    changed (a standing round's later work is still that round, so its number
    is reused); compacted to counts, then cut, past `COMMIT_SUBJECT_MAX`. The
    body: the paragraph the person saw, the files grouped by directory, the
    earlier rounds this commit also holds, and what it left out (a name that
    looks like a secret, a file over the cap, what is not a file). No trailer
    and no attribution. ``round_rel`` is `rounds.ROUND_REL`, handed in: the
    round record's file name has one home, and only `cli` imports `rounds`."""
    said = HUMAN["round"]
    if shape == "written":
        what = _round_changes(changes)
    elif shape == "first":
        what = said["commit_first"]
    else:
        kinds = {_commit_part(rel, round_rel) for rel in staged}
        words = [said["commit_parts"][k] for k in _PART_ORDER if k in kinds]
        what = said["commit_changed"].format(parts=_and_list(words))
    subject = said["commit_subject"].format(n=number, what=what)
    if len(subject) > COMMIT_SUBJECT_MAX and shape == "written":
        compact = said["commit_compact"]
        bits = []
        flips = len(getattr(changes, "claims", ())) + len(getattr(changes, "requirements", ()))
        if flips:
            bits.append(compact["statuses"].format(k=flips,
                                                   statuses=_plural(flips, "status", "statuses")))
        if getattr(changes, "verdicts", ()):
            n = len(changes.verdicts)
            bits.append(compact["verdicts"].format(v=n, verdicts=_plural(n, "verdict")))
        if getattr(changes, "instructions", ()):
            bits.append(compact["instructions"])
        if bits:
            subject = said["commit_subject"].format(n=number, what="; ".join(bits))
    if len(subject) > COMMIT_SUBJECT_MAX:
        subject = subject[:COMMIT_SUBJECT_MAX - 1].rstrip() + "…"
    paragraphs = [paragraph.strip()]
    groups: dict[str, int] = {}
    for rel in staged:
        key = _commit_group(rel)
        groups[key] = groups.get(key, 0) + 1
    group_words = ", ".join(f"{key} ({n})" for key, n in groups.items())
    paragraphs.append(said["commit_files"].format(n=len(staged),
                                                  files=_plural(len(staged), "file"),
                                                  groups=group_words))
    if earlier:
        a, b = earlier
        paragraphs.append(said["commit_earlier_one"].format(a=a) if a == b
                          else said["commit_earlier_many"].format(a=a, b=b))
    if secret:
        paragraphs.append(said["commit_secret"].format(paths=", ".join(secret)))
    if too_large:
        paragraphs.append(said["commit_too_large"].format(paths=", ".join(_sized(too_large))))
    if not_files:
        paragraphs.append(said["commit_not_files"].format(paths=", ".join(not_files)))
    body = "\n\n".join(_wrap_message(p) for p in paragraphs if p)
    return subject, body


def round_hook_message(outcome: Any) -> str:
    """The Stop hook's ``systemMessage`` (SPEC 24): ``nopekit `` and the
    paragraph `round --commit` prints, or, for a round that did not run, why
    and that nothing was committed. ``outcome`` has ``not_run`` and
    ``summary``."""
    if getattr(outcome, "not_run", ""):
        return HUMAN["round"]["hook_not_run"].format(
            why=str(outcome.not_run).rstrip(".") or "it did not run")
    return "nopekit " + str(getattr(outcome, "summary", "") or "").strip()


def round_hook_crash(exc: BaseException) -> str:
    """The Stop hook's message for a crash: its type and first line."""
    line = (str(exc).strip().splitlines() or [""])[0]
    return HUMAN["round"]["hook_crash"].format(kind=type(exc).__name__,
                                               line=line or "no message")


def _site_status_word(token: str) -> str:
    """A status token's word as the page bakes it (`page._word`'s rule: the
    status's row, the errored row for a crash's Skipped). Kept here because
    `page` imports this module; the two are one rule, and `page.status_problems`
    holds a baked return phrase to this function's."""
    errored = token == "errored"
    for status, key in claim_logic.STATUS_KEY.items():
        if key == ("skipped" if errored else token):
            return words(status, errored=errored).word
    return _one(token)


def return_words(row: Any, ledger: Ledger | None = None, *, register: str = "cli") -> str:
    """The state of `row`'s return condition now, in words — from its
    `ReturnState`, never computed here. `ledger` gives a value or a limit its
    claim's units.

    ``register="site"`` (the marketing page): the same branches in
    ``HUMAN["site"]["rejection"]``'s words, with no record text inside — a
    condition a person evaluates says only that (its text is baked apart, as
    record text, `condition_text`), and no condition says ``""``; a status
    inside it is the page's word for its token (`_site_status_word`). The CLI
    branch's bytes are untouched (its transcript is pinned)."""
    site = register == "site"
    said = HUMAN["site"]["rejection"] if site else HUMAN["rejection"]
    state = row.returns
    cond = row.condition
    if state.state == "none":
        return "" if site else said["none"]
    if state.state == "not-machine-checkable":
        return said["text"] if site else said["text"].format(text=condition_text(cond))
    claim = _one(state.claim or getattr(cond, "claim", ""))
    if state.state == "cannot-evaluate":
        if state.why == "status":
            return said["cannot"].format(
                claim=claim, status=(_site_status_word(state.status) if site
                                     else _status_token_word(state.status)))
        if state.why == "no-claim":
            return said["cannot_no_claim"].format(claim=claim)
        if state.why == "no-margin":
            if site:
                return said["cannot_no_margin"].format(claim=claim)
            detail = said["margin_why"].get(state.detail, _one(state.detail))
            return said["cannot_no_margin"].format(gate=state.gate or "its evaluator",
                                                   claim=claim, detail=detail)
        if state.why == "no-limit":
            return said["cannot_no_limit"].format(claim=claim)
        if state.why == "band":
            return said["cannot_band"].format(claim=claim)
        return said["cannot_no_value"].format(claim=claim)
    units = ""
    if ledger is not None and getattr(cond, "quantity", "") in ("value", "limit"):
        found = ledger.claim(state.claim)
        units = str(getattr(getattr(found, "acceptance", None), "units", "") or "") \
            if found is not None else ""
    observed = _num(state.observed) + (f" {_one(units)}" if units else "")
    key = "holds" if state.state == "holds" else "does_not_hold"
    if site:
        return said[key].format(condition=condition_text(cond), observed=observed)
    gate = f" ({state.gate})" if state.gate else ""
    return said[key].format(condition=condition_text(cond), observed=observed, gate=gate)


def _site_type_phrase(kind: str, preference: Any, by: str = "") -> str:
    """`_type_phrase` on the page: a preferred_against rejection is the CLI's
    phrase with a capital, built from the same template (`HUMAN["rejection"]`'s
    `recorded` / `not_recorded`), so invariant 13's *preferred against, not
    recorded by <name>* has one home; any other type is the page's word,
    ``""`` for none (the page then shows nothing, never "None")."""
    if kind == "preferred_against":
        phrase = _type_phrase(kind, preference, by)
        return phrase[:1].upper() + phrase[1:]
    return HUMAN["site"]["rejection"]["types"].get(kind or "untyped", "")


def rejection_words(row: Any, ledger: Ledger | None = None, *,
                    register: str = "cli") -> tuple[str, str]:
    """`(type phrase, condition phrase)` for one `claims.RejectionRow` — the
    words every reader of a rejection prints (`why`, `check`'s *may return*,
    and with ``register="site"`` the marketing page, which `page.status_problems`
    holds to this function)."""
    if register == "site":
        return (_site_type_phrase(row.type, row.preference, row.by),
                return_words(row, ledger, register="site"))
    return (_type_phrase(row.type, row.preference, row.by), return_words(row, ledger))


def rejection_facts(row: Any, ledger: Ledger | None = None) -> str:
    """`why`'s facts line under a decision's rejection: `{type} · {condition}`
    and, when an input is its source, `· source: input {id}`."""
    kind, condition = rejection_words(row, ledger)
    line = HUMAN["rejection"]["facts"].format(type=kind, condition=condition)
    if row.input:
        line += " · " + HUMAN["rejection"]["input"].format(input=_one(row.input))
    return line


def may_return_line(row: Any, ledger: Ledger | None = None) -> str:
    """`check`'s line for a rejected alternative whose return condition holds."""
    kind, condition = rejection_words(row, ledger)
    return HUMAN["rejection"]["may_return"].format(
        value=_one(row.value), decision=row.decision, type=kind, state=condition)


def rejection_suffix(rejection: Any, *, decision: str = "") -> str:
    """What `decide` prints after a rejection it wrote, and the decision log
    after each loser: its type, its condition as written and its input —
    static, never a live state. `decide` says a preference it just wrote is not
    recorded (true when it is written); the log (``decision`` given, its id)
    says only whom it names and where to read whether they recorded it, since
    the log is regenerated by `report --write`, not by a person's `prefer`.
    `""` for a legacy rejection that states none of them."""
    said = HUMAN["rejection"]
    kind = str(getattr(rejection, "type", "") or "")
    name = claim_logic.name_of(getattr(rejection, "by", ""))
    if kind == "preferred_against" and decision:
        head = said["named"].format(name=_one(name) or "no one", id=decision)
    else:
        head = _type_phrase(kind, None, name) if kind else ""
    parts: list[str] = []
    cond = getattr(rejection, "returns_if", None)
    if cond is not None and str(getattr(cond, "text", "") or ""):
        parts.append(said["returns_text"].format(text=_one(cond.text)))
    elif cond is not None:
        parts.append(said["returns_if"].format(condition=condition_text(cond)))
    if str(getattr(rejection, "input", "") or ""):
        parts.append(said["input"].format(input=_one(rejection.input)))
    if not head and not parts:
        return ""
    return " — " + " · ".join([p for p in (head, *parts) if p])


def requirement_statuses(ledger: Ledger, composed: Mapping[str, Any],
                         cover: Mapping[str, list[str]]) -> dict[str, Any]:
    """`claims.requirement_statuses` — the one producer, its statuses untouched —
    with each row's `disagree` filled: the serving claims whose status and
    evidence disagree (`_disagreement`, D18), record order. Every requirement
    reader asks this, so none prints a requirement bare "checked" over a claim
    the claim rows list as a contradiction. What slipped through (review of
    phase 1): the roll-up ranked by the composition alone, so a resolver
    regression the claim rows caught read `R1 checked` one line below them."""
    found = claim_logic.requirement_statuses(ledger, composed)
    cover = dict(cover)
    out: dict[str, Any] = {}
    for rid, row in found.items():
        bad = tuple(cid for cid in row.claims
                    if (claim := ledger.claim(cid)) is not None
                    and _disagreement(ledger, claim, composed[cid], cover))
        out[rid] = row._replace(disagree=bad) if bad else row
    return out


def _requirement_order(requirements: list[Any], found: Mapping[str, Any]) -> list[Any]:
    """The requirements `status`'s line names before `+N more`: worst first, in
    `claims.SEVERITY_ORDER` as the claim rows above it are (a disagreement
    first, as those rows list it), record order within a rank; Gap with no
    claim at Gap's rank. What slipped through (review of phase 1): the line cut
    at eight in record order, so a failing ninth read `+1 more` under eight
    checked ones."""
    def rank(requirement: Any) -> int:
        row = found[requirement.id]
        if row.disagree:
            return -1
        key = "errored" if row.errored else claim_logic.STATUS_KEY[row.status]
        return claim_logic.SEVERITY_ORDER.index(key)
    return sorted(requirements, key=rank)


def requirement_line(ledger: Ledger, composed: Mapping[str, Any],
                     cover: Mapping[str, list[str]] | None = None) -> str | None:
    """`requirements 4 — R1 failing (C1) · R2 gap (C7) · R4 checked` — the
    requirements line `status` prints (SPEC decision 12), or None when the
    project records none. Every status from `requirement_statuses` over
    `claims.requirement_statuses`, the one producer; record order while every
    one fits, else worst first (`_requirement_order`) with `+N more (k
    unresolved)`; Checked ones name no claim unless one disagrees with its
    evidence. ``cover`` is the caller's coverage (`_coverage`); None reads
    each claim's own cached list."""
    requirements = list(getattr(ledger, "requirements", None) or ())
    if not requirements:
        return None
    said = HUMAN["requirement"]
    if cover is None:
        cover = _coverage(ledger, None)
    found = requirement_statuses(ledger, composed, cover)
    shown_ones = (requirements if len(requirements) <= _MAX_REQUIREMENT_ITEMS
                  else _requirement_order(requirements, found)[:_MAX_REQUIREMENT_ITEMS])
    items = []
    for requirement in shown_ones:
        status = found[requirement.id]
        shown = status_word(status.status, errored=status.errored)
        if status.disagree:
            items.append(said["item_disagree"].format(
                id=requirement.id, word=shown, ids=", ".join(status.disagree)))
        elif not status.claims:
            items.append(said["item_none"].format(id=requirement.id, word=shown))
        elif status.status in (ClaimStatus.PASS, ClaimStatus.VERIFIED):
            items.append(said["item"].format(id=requirement.id, word=shown))
        else:
            ids = ", ".join(status.worst[:_MAX_WORST_IDS]) + (
                f", {said['more'].format(k=len(status.worst) - _MAX_WORST_IDS)}"
                if len(status.worst) > _MAX_WORST_IDS else "")
            items.append(said["item_worst"].format(id=requirement.id, word=shown, ids=ids))
    if len(requirements) > _MAX_REQUIREMENT_ITEMS:
        kept = {r.id for r in shown_ones}
        hidden = [found[r.id] for r in requirements if r.id not in kept]
        left = sum(1 for r in hidden if r.disagree or r.status not in (
            ClaimStatus.PASS, ClaimStatus.VERIFIED))
        items.append(said["more_unresolved"].format(k=len(hidden), u=left) if left
                     else said["more"].format(k=len(hidden)))
    return said["line"].format(n=len(requirements), items=said["sep"].join(items))


def requirement_json(requirement: Any, found: Any) -> dict[str, Any]:
    """A requirement as every JSON channel shows it — `status --json`, `report
    --json` and `state.json` (SPEC decision 12): its record, and its status from
    `claims.requirement_statuses` (`found`), worded once here."""
    return {"id": requirement.id, "statement": str(requirement.statement or ""),
            "source": str(requirement.source or ""), "status": found.status.value,
            "key": found.key, "word": status_word(found.status, errored=found.errored),
            "errored": bool(found.errored), "claims": list(found.claims),
            "worst": list(found.worst),
            # D18 carried up: the serving claims whose status and evidence
            # disagree (`requirement_statuses`); empty under the composition.
            "disagree": list(getattr(found, "disagree", ()) or ())}


def _section_requirements(ledger: Ledger, composed: Mapping[str, Any],
                          cover: Mapping[str, list[str]] | None = None) -> list[str]:
    """The report's `## Requirements` (SPEC decision 12): each requirement, its
    status from `claims.requirement_statuses`, its words, and the claims that
    serve it grouped by status, most urgent first. Empty when the project
    records none, so a report without requirements is byte-identical to one
    before phase 1. After the Failing section: SECTION_PROVEN stays first, and a
    requirement is never a claim row (no JUnit suite either)."""
    requirements = list(getattr(ledger, "requirements", None) or ())
    if not requirements:
        return []
    said = HUMAN["requirement"]
    found = requirement_statuses(ledger, composed,
                                 _coverage(ledger, None) if cover is None else cover)
    out = [HUMAN["heading"]["requirements"], "",
           said["intro"].format(gap=word(ClaimStatus.UNCLAIMED)), ""]
    for requirement in requirements:
        status = found[requirement.id]
        groups: dict[tuple, list[str]] = {}
        for cid in status.claims:
            one = composed[cid]
            groups.setdefault((claim_logic.severity(one), one.status, one.errored),
                              []).append(cid)
        claims = said["sep"].join(
            said["group"].format(ids=", ".join(ids), word=status_word(st, errored=err))
            for (_rank, st, err), ids in sorted(groups.items(), key=lambda kv: kv[0][0])
        ) or said["no_claim"]
        statement = _trunc(_one(requirement.statement), 160)
        if status.disagree:
            statement = said["md_disagree"].format(ids=", ".join(status.disagree)) + statement
        out.append(said["md_row"].format(
            id=requirement.id, word=status_word(status.status, errored=status.errored),
            statement=statement, claims=claims))
    out.append("")
    return out


#: A recorded result's token (follow-up after phase 3b): its outcome as the
#: judge counts it. ``pass`` is a pass that counts; ``fail`` a fail that counts,
#: which is every fail but one a pass on another exported article superseded
#: (R-3, P2.5b-D14); ``not-counted`` any other. Both pages colour and word a
#: recorded result from this token alone, through one table (format.js
#: `outcomeLook`). What slipped through: the front page's drawer coloured the
#: block on ``counts`` alone, so a counted fail read green beside its Failing
#: chip, and the technical record kept a second tone rule of its own.
#: *Rejected:* the bare outcome, ``pass`` or ``fail`` (a pass that does not count
#: would then read green, critique 3 of the P2.5a design); two flags the browser
#: combines (the page computes no tone, SPEC 32).
RESULT_TOKENS = ("pass", "fail", "not-counted")


def result_facts(claim: Claim, entry: Any, *, register: str = "cli") -> dict[str, Any]:
    """What a renderer may show about one recorded result (critique 3 of the
    P2.5a design): ``counts`` — the judge counted it (a pass that settles the
    claim now, or any fail: R-3); ``token`` — its `RESULT_TOKENS` entry, the one
    key a page colours it by; ``why`` — why not, in words; ``recorded`` — who
    and from where. The page paints the ok tone only on a pass that counts,
    never on ``passed`` (site.py's rule for verdict rows, now for results too).

    ``register="site"`` (follow-up after phase 3b): the same branches, the same
    ``counts`` and ``token``, worded by ``HUMAN["site"]["result"]`` for the
    front page's drawer, with ``word``, the token's site word; `state.json`, the
    technical record, keeps the CLI's words. One function, so the two pages
    never disagree on whether a result counts, or on how it looks."""
    site = register == "site"
    standing = getattr(claim, "standing", None)
    results = list(getattr(claim, "results", ()) or ())
    index = max((i for i, item in enumerate(results) if item == entry), default=None)
    found = next((e for e in getattr(standing, "entries", ()) or () if e.index == index),
                 None)
    superseded = tuple(getattr(standing, "superseded", ()) or ())
    if getattr(entry, "passed", None) is not True and index in superseded:
        # P2.5b-D14: a fail a pass on another exported article superseded is
        # the one fail that does not count — said, with the pass that did it.
        # What slipped through without it: `why` and the page read every fail
        # "it counts" (R-3's rule before P2.5b), beside a row reading Checked.
        passing = (_entry(claim, standing.counted)
                   if getattr(standing, "counted", None) is not None else None)
        counts = False
        why = (HUMAN["site"]["result"]["superseded"] if site
               else HUMAN["physical"]["superseded_by"]).format(
            article=article12(getattr(passing, "article", None) or {}))
    elif getattr(entry, "passed", None) is not True:
        counts, why = True, ""
    elif found is None:
        counts = False
        why = "" if standing is not None else (_site_not_counted(claim, "legacy") if site
                                               else HUMAN["not_counted"]["legacy"])
    else:
        counts = bool(found.counts)
        why = "" if counts else (_site_why_words(claim, found) if site
                                 else _why_words(claim, found))
    token = (("pass" if getattr(entry, "passed", None) is True else "fail") if counts
             else "not-counted")
    if site:
        said = HUMAN["site"]["result"]
        return {"counts": counts, "token": token, "word": HUMAN["site"]["outcome"][token],
                "why": why, "recorded": _site_recorded(entry),
                "heading": said["heading_judgment" if _judgment(claim) else "heading_test"]}
    said = HUMAN["physical"]
    return {"counts": counts, "token": token, "why": why, "recorded": recorded_words(entry),
            # The block's heading in the page's words (review of P2.5a: a
            # judgment read "A physical result was recorded").
            "heading": said["judgment_recorded" if _judgment(claim) else "result_recorded"]}


def _why_words(claim: Claim, found: Any) -> str:
    why = str(found.why or "")
    if why == "article-moved":
        return HUMAN["physical"]["moved_tail"].format(
            article=article12(found.article),
            moves=verdict_logic._stale_text(found.moved) if found.moved else "the design moved")
    if why == "judgment-moved":
        return HUMAN["lead"][ClaimCause.JUDGMENT_MOVED].format(
            authority=_one(getattr(claim, "authority", "")))
    authority = _one(getattr(claim, "authority", ""))
    if why == "claim-moved" and _judgment(claim):
        return HUMAN["physical"]["judgment_claim_moved"].format(id=claim.id,
                                                                authority=authority)
    if why == "claim-moved":
        return (HUMAN["lead"][ClaimCause.CLAIM_MOVED].format(
            id=claim.id, article=article12(found.article)) + " — "
                + HUMAN["physical"]["claim_moved_tail"])
    if why == "article-unjudged" and _judgment(claim):
        return HUMAN["physical"]["judgment_unjudged"].format(authority=authority)
    if why == "article-unjudged":
        return HUMAN["lead"][ClaimCause.ARTICLE_UNJUDGED].format(
            article=article12(found.article))
    return _not_counted(claim, why)


def _site_why_words(claim: Claim, found: Any) -> str:
    """`_why_words`, branch for branch, in the site's words."""
    said = HUMAN["site"]["result"]
    why = str(found.why or "")
    authority = _one(getattr(claim, "authority", ""))
    article = article12(found.article)
    if why == "article-moved":
        if found.moved:
            return said["moved"].format(article=article,
                                        moves=verdict_logic._stale_text(found.moved))
        return said["moved_bare"].format(article=article)
    if why == "judgment-moved":
        return said["judgment_moved"].format(authority=authority)
    if why == "claim-moved" and _judgment(claim):
        return said["judgment_claim_moved"].format(authority=authority)
    if why == "claim-moved":
        return said["claim_moved"].format(article=article)
    if why == "article-unjudged" and _judgment(claim):
        return said["judgment_unjudged"].format(authority=authority)
    if why == "article-unjudged":
        return said["unjudged"].format(article=article)
    return _site_not_counted(claim, why)


def _site_not_counted(claim: Claim, why: str) -> str:
    """`_not_counted` in the site's words: the judgment table for an expert
    judgment, the pass table's key otherwise, the token itself when no table
    knows it (as the CLI's)."""
    said = HUMAN["site"]["result"]
    table = said["judgment_not_counted"] if _judgment(claim) else said["not_counted"]
    key, _sep, rest = why.partition(":")
    template = table.get(key) or said["not_counted"].get(key) or why
    return template.format(path=rest, authority=_one(getattr(claim, "authority", "")))


def _site_recorded(entry: Any) -> str:
    """`recorded_words` in the site's words: who recorded a result, and from
    where when that is not their own shell."""
    said = HUMAN["site"]["result"]
    name = _one(getattr(entry, "who", "")) if entry is not None else ""
    channel = str(getattr(entry, "channel", "") or "") if entry is not None else ""
    if not name:
        return said["recorded_none"]
    if channel.startswith("agent-session"):
        return said["recorded_agent"].format(who=name)
    if channel == "non-interactive":
        return said["recorded_pipe"].format(who=name)
    return said["recorded"].format(who=name)


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
            "errored": said["error"],
            # An unqualified evaluator's verdict row (P2.3-D17): never a crash's
            # word; Gap's tone on the page.
            "unqualified": HUMAN["lead"][ClaimCause.UNQUALIFIED],
            # A pass outside its operating context (P2.4-D22): the tally's word,
            # never `unqualified` (review of P2.4: the page said UNQUALIFIED).
            "outside-context": HUMAN["context"]["tally"]}


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
            # A verdict not current only for a channel nopekit cannot see
            # (`claims.unseen_gates`; the row's `unseen`): the technical page's
            # flag, label and chip for it. What slipped through (review of issue
            # #15's D1): each read "≈ invalidated" beside a reason that said
            # nothing moved.
            "unseen": HUMAN["lead"][ClaimCause.UNSEEN],
            # The title the page puts on a claim whose status its evidence does
            # not back (P2.1-D18) — the page's own words until P2.5a, which said
            # "contradict", a second sense for GLOSSARY's *contradiction*
            # (P2.5a-D18). The site never owns a word.
            "disagree": "the resolver and the verdicts disagree — a defect to report",
            # A recorded physical result's block (critique 3 of the P2.5a
            # design): the page paints the ok tone only on a pass the judge
            # counted (`result_facts`), and says so in these words.
            "result_recorded": HUMAN["physical"]["result_recorded"],
            # An expert judgment's group (review of P2.5a: it sat under the
            # assumptions' blurb — "with a reason and an owner", where *owner*
            # is authority's Never-say — with no word saying whose judgment).
            "judgment_title": "Expert judgments",
            "judgment_blurb": "Each stays with the person or institution it names, who "
                              "settles it in their own shell — no evaluator here does. Each "
                              "row says whether its authority has recorded it.",
            # A chart view on the technical record (issue #15, P2.5): what
            # its box says when the chart library does not load, and when
            # the view holds nothing to draw. The engineering page's words.
            "chart_table": HUMAN["site"]["figure"]["table"],
            "chart_empty": HUMAN["site"]["figure"]["chart_empty"],
            "result_counts": "it counts",
            "result_not_counted": "it does not count",
            "need": {str(status.value): said for status, said in HUMAN["need"].items()},
            "outcome_hint": {"pass": hints["pass"], "fail": hints["fail"],
                             "skipped": hints["skipped"], "errored": hints["error"],
                             "outside-context": HUMAN["context"]["hint"]}}


def need_word(status: Any) -> str:
    """A gap record's state, in its word: `identified` for `open` (GLOSSARY §6)."""
    try:
        return HUMAN["need"][NeedStatus(status)]
    except ValueError:
        return str(status)


# --------------------------------------------------------------------------- #
# small render helpers
# --------------------------------------------------------------------------- #
def limit_words(acceptance: Any) -> str:
    """An acceptance condition's limit as a page or a line shows it beside a
    value: `0.5 mm`, a band's `0.2..0.8 mm`, ``""`` with no finite limit."""
    limit = getattr(acceptance, "limit", None)
    if not _finite(limit):
        return ""
    units = str(getattr(acceptance, "units", "") or "")
    comparator = getattr(acceptance, "comparator", None)
    hi = getattr(acceptance, "limit_hi", None)
    if str(getattr(comparator, "value", comparator) or "") == "between" and _finite(hi):
        return f"{_num(limit)}..{_num(hi)} {units}".strip()
    return f"{_num(limit)} {units}".strip()


def _finite(value: Any) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))


def _num(value: Any) -> str:
    """Render a measured number without trailing-zero noise: 220.0 -> `220`."""
    if isinstance(value, bool):          # bool is an int; check it first
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return f"{value:g}"
    return str(value)


def _trunc(text: str, limit: int | None) -> str:
    """One line of `text`, cut to `limit` characters; `None` normalises and never
    cuts. Every control character left after the whitespace is collapsed is
    escaped as visible text (`util.printable`): what slipped through (review of
    P2.5a) — a claim statement holding `\x1b[1G\x1b[2K` erased itself on `claim
    physical`'s prompt and showed another claim's words, so a person typed the
    id of a claim they had not read."""
    text = printable(text or "")
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
                  stale_gates: Collection[str] = (),
                  unseen_gates: Collection[str] = ()) -> dict[str, Any]:
    """Every claim's `claims.Composed` — status AND cause — judged against the
    LIVE registry, not the cache.

    `claims.compositions` accepts the registry as a keyword and the difference
    is not cosmetic: without it, coverage is read from `claim.gates` alone, and
    a claim whose gate arrived with a pack installed after the claim was
    written reads Gap ("nobody can check this — go find a solver") instead of
    Open ("the evaluator is right there, run it"). Those two send an agent in
    opposite directions, and only one of them is true. No `owners`: nothing in
    P2.1 attributes an owner (the signing channel is later in Phase 2), so every
    assumption reads Gap here, as everywhere. `unseen_gates` as for
    `claims.compose`: the words of a Stale claim, never its status.
    """
    return claim_logic.compositions(ledger, registry=registry, stale=stale,
                                    stale_gates=stale_gates, unseen_gates=unseen_gates)


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


def _bases(registry: Any) -> dict[str, str]:
    """`{gate id: basis token}` of every registered evaluator (SPEC 38;
    `gates.basis_of`'s rule): an unregistered gate is absent, and shows none."""
    from .models import BASIS_UNDECLARED
    return {s.id: (getattr(s, "basis", "") or BASIS_UNDECLARED) for s in _specs(registry)}


def _unrun_specs(ledger: Ledger, registry: Any) -> list[Any]:
    """Gates that are registered but have no verdict at all: never run here.

    These are the quiet ones. They do not fail, they do not skip, they do not
    appear in `nopekit check` output at all — they are simply absent, and a
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
            out.append((gid, unqualified_text(verdict.unqualified)))
        elif not verdict.ok:
            body = _verdict_body(verdict)
            head = {"error": lead[ClaimCause.ERRORED], "skipped": lead[ClaimCause.SKIPPED]
                    }.get(verdict.outcome, HUMAN["outcome"]["fail"])
            out.append((gid, f"{head}: {body}" if body else head))
    return out


def _disagreement(ledger: Ledger, claim: Claim, composed: Any,
                  cover: dict[str, list[str]]) -> str:
    """Why a claim the resolver calls `pass` disagrees with its evidence —
    `<gate> <why>; …` — or `""`. Under GLOSSARY §3's composition this never
    happens; when it does, the resolver and the verdicts disagree, and the
    report says so loudly, outside the checked section (P2.1-D18, review): a
    contradiction kept under PROVEN, even marked, is PARTIAL under a new name."""
    if composed.status is ClaimStatus.VERIFIED:
        # A physical or expert-judgment Checked stands on its standing, not on
        # verdicts (critique 2 of the P2.5a design: this check read PASS only,
        # so a resolver that minted VERIFIED over a moved article or beside an
        # unrun evaluator reached JUnit and the page as a clean Checked).
        standing = getattr(claim, "standing", None)
        why = []
        if standing is None:
            why.append("no physical result was judged")
        elif getattr(standing, "state", "") != "current":
            why.append(f"its physical result is {getattr(standing, 'state', '') or 'none'}, "
                       f"not current")
        elif not any(getattr(e, "counts", False) for e in getattr(standing, "entries", ())):
            why.append("no physical result counts")
        why += [f"{gid} {text}" for gid, text in _unproven_for(claim.id, cover, ledger)]
        return "; ".join(why)
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


def readiness(ledger: Ledger, composed: Mapping[str, Any],
              milestone: Any = None) -> dict[str, Any]:
    """What *ready* turns on, as lists of claims (GLOSSARY §4, W3) — for
    ``milestone``, or with none for the project (``critical``, what ``check``
    blocks on): `required`; `unresolved` — required and not Checked, Pending
    build and Assumed included; `missing` — required ids no claim holds (P2.5b);
    `unbound` — the unresolved ones with a physical pass recorded that does not
    count; `ready` — at least one required claim, and none unresolved or
    missing. `claims.unresolved` — THE predicate (P2.5b-D3) — decides; this is
    its fields as the dict every reader took before. `ready` in a JSON summary
    is not this (it keeps "nothing stops check"). What slipped through (review
    of P2.1): an unbound pass read Checked everywhere but here, so *checked*
    meant two things."""
    found = claim_logic.unresolved(ledger, composed, milestone)
    return {"required": list(found.required), "unresolved": list(found.unresolved),
            "unbound": list(found.unbound), "missing": list(found.missing),
            "ready": found.ready}


def _missing_words(missing: Sequence[str]) -> str:
    return HUMAN["readiness"]["missing"].format(n=len(missing), ids=", ".join(missing))


def milestone_line(ledger: Ledger, composed: Mapping[str, Any], milestone: Any) -> str:
    """`print-v1: 3 of 4 required claims checked · 0 stale · 1 failing (C1)` —
    ONE line per milestone, one shape wherever it is printed (critique 20 of the
    P2.5b design): the `export` list, REPORT.md and `export <m>`. The stale
    count always printed (W11: "required claims checked and stale"), the other
    unresolved required claims grouped by word, ids no claim holds last.
    *Ready* is what the line's numbers say — k of n, nothing stale, nothing
    else — and the readiness sentence says the word."""
    name = str(getattr(milestone, "id", "") or "")
    found = claim_logic.unresolved(ledger, composed, milestone)
    said = HUMAN["milestone"]
    if not list(getattr(milestone, "requires", None) or ()):
        return said["nothing"].format(name=name)
    n = len(found.required) + len(found.missing)
    stale = [c for c in found.unresolved if composed[c.id].status is ClaimStatus.STALE]
    rest = [c for c in found.unresolved if composed[c.id].status is not ClaimStatus.STALE]
    tail = ""
    if rest:
        tail += " · " + _groups(ledger, composed, rest)
    if found.missing:
        tail += " · " + _missing_words(found.missing)
    return said["line"].format(
        name=name, k=len(found.required) - len(found.unresolved), n=n,
        claims=_plural(n, "claim"), checked=word(ClaimStatus.PASS), s=len(stale),
        stale=word(ClaimStatus.STALE, n=len(stale)),
        stale_ids=f" ({_ids(stale)})" if stale else "", rest=tail)


def _hardware_clause(ledger: Ledger, composed: Mapping[str, Any], *,
                     register: str = "cli") -> list[str]:
    """The readiness sentence's hardware clause (W13), in EVERY branch, ready
    included: `Pending build: N claims need an article (ids)` when any does —
    naming apart those with a pass that does not count — then one `Rebuild
    article <a12> (ids): <moved>.` per article the rebuild prediction names
    (`claims.rebuild`, the producer `### Articles to rebuild` reads), then
    `Checked on an article: N (ids).` or `No claim is checked on any article.`
    What slipped through before P2.5b: the sentence named what waited for an
    article and said nothing of what had been tested on one, or of the prints
    a design change had made obsolete — they were a heading below. *Rejected:*
    dropping the clause where no physical claim exists (that design is the one
    most easily read as built); "It is unverified in physical hardware"
    (GLOSSARY §9: *unverified* is Pending build's Never-say).

    ``register="site"`` (follow-up after phase 3b): the same three clauses,
    from the same lists, in ``HUMAN["site"]["readiness"]``'s words: a built
    part where the CLI says an article. What slipped through: phase 5 left
    this clause shared, since it passed the style scan, and the page said "1
    claim needs an article" to a reader who had never met the word."""
    status_of = {cid: c.status for cid, c in composed.items()}
    site = register == "site"
    said = HUMAN["site"]["readiness"] if site else HUMAN["readiness"]
    parts: list[str] = []
    pending = [c for c in ledger.claims if status_of.get(c.id) is ClaimStatus.UNVERIFIED]
    if pending:
        recorded = [c for c in pending if composed[c.id].cause is ClaimCause.PHYSICAL_PASS]
        if site:
            text = said["pending"].format(Pending=words(ClaimStatus.UNVERIFIED).term,
                                          n=len(pending), claims=_plural(len(pending), "claim"),
                                          need=_plural(len(pending), "needs", "need"),
                                          ids=_ids(pending))
        else:
            text = (f"{words(ClaimStatus.UNVERIFIED).term}: {len(pending)}"
                    f" {_plural(len(pending), 'claim')}"
                    f" {_plural(len(pending), 'needs', 'need')} an article ({_ids(pending)})")
        if recorded:
            text += "; " + HUMAN["physical"]["unbound"].format(
                ids=_ids(recorded), has=_plural(len(recorded), "has", "have"))
        parts.append(text + ".")
    for found in claim_logic.rebuild(ledger):
        parts.append(site_rebuild_text(found) if site else said["rebuild"].format(
            article=article12(found.article), claims=", ".join(found.claims),
            moves=verdict_logic._stale_text(found.moved) if found.moved
            else "the design moved"))
    on_article = [c for c in ledger.claims if c.id in composed
                  and composed[c.id].cause is ClaimCause.ON_ARTICLE]
    parts.append(said["on_article"].format(Checked=words(ClaimStatus.PASS).term,
                                           n=len(on_article), ids=_ids(on_article))
                 if on_article else said["none_on_article"].format(
                     checked=word(ClaimStatus.PASS)))
    return parts


def site_rebuild_text(found: Any) -> str:
    """One part to build again, in the site's words (`claims.rebuild`'s row):
    the readiness sentence's clause and next steps' row both say this, so the
    page never names one part two ways."""
    said = HUMAN["site"]
    values = {"article": article12(found.article), "claims": ", ".join(found.claims)}
    if found.moved:
        return said["rebuild"].format(moves=verdict_logic._stale_text(found.moved), **values)
    return said["rebuild_bare"].format(**values)


def limits_line(ledger: Ledger, register: str = "cli") -> str:
    """The limits line (M18.1; METHOD rule 9: "Say so, in the report, every
    time"): what a checked claim does not mean, and that nothing outside the
    claims was evaluated — REPORT.md's paragraph and `export`'s line, verbatim
    on both (critique 10 of the P2.5b design: `/ready` must carry it too).
    ``register="site"``: the same facts in the site's words (SPEC 34), the
    page's only "Checked does not mean true"."""
    n = len(ledger.claims)
    table = HUMAN["site"]["readiness"] if register == "site" else HUMAN["readiness"]
    return table["limits"].format(Checked=words(ClaimStatus.PASS).term, n=n,
                                  claims=_plural(n, "claim"))


def latency_words(claim: Claim, exports: Iterable[Any]) -> str:
    """A physical claim's latency in words (W3): `measured 26 h on article
    <a12> (expected 1 day)`, `expected 1 day (declared)`, or that none is
    declared (`claims.latency`)."""
    found = claim_logic.latency(claim, exports)
    said = HUMAN["latency"]
    declared = getattr(claim, "expected_latency", None) or {}
    declared_text = ""
    if isinstance(declared, Mapping) and declared.get("units"):
        value = declared.get("value")
        units = str(declared.get("units"))
        plural = units in ("day", "week", "year") and value != 1
        declared_text = f"{_num(value)} {units}{'s' if plural else ''}"
    if found.source == "measured":
        measured = _span(found.seconds)
        if declared_text:
            return said["measured"].format(measured=measured, article=found.article[:12],
                                           declared=declared_text)
        return said["measured_bare"].format(measured=measured, article=found.article[:12])
    if found.source == "declared":
        return said["declared"].format(declared=declared_text)
    return said["none"]


def _span(seconds: Any) -> str:
    """A measured span as a person reads it: minutes under an hour, hours under
    two days, days past that — rounded, the way a print log says it. Why these
    cuts: an hour is where "N min" stops reading at a glance; two days, so a
    print-and-test that took 26 h reads "26 h" (a day and a bit, said exactly)
    rather than "1 day", and anything a week or a winter long reads in days.
    *Rejected:* seconds (no person reads 93600 s); one unit per declared
    `expected_latency` unit (a measured span would change its unit when the
    declaration did); cutting at one day (26 h would read "1 day", 31 h too)."""
    value = float(seconds or 0.0)
    if value < 3600:
        return f"{round(value / 60)} min"
    if value < 2 * 86400:
        return f"{round(value / 3600)} h"
    days = round(value / 86400)
    return f"{days} {_plural(days, 'day')}"


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
                f"ready (listed in `nopekit report`)")
    n = len(found["unresolved"])
    return (f"nothing stops this check run — {n} required "
            f"{_plural(n, 'claim')} {_plural(n, 'is', 'are')} unresolved: "
            f"{_groups(ledger, composed, found['unresolved'])} "
            f"(listed in `nopekit report`)")


def _verdict_sentence(ledger: Ledger, composed: Mapping[str, Any], registry: Any,
                      *, stale: bool, markdown: bool, milestone: Any = None,
                      boundary: bool = False, register: str = "cli") -> str:
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
    about hardware. From P2.5b the clause also names each article the rebuild
    prediction names and what is checked on an article (`_hardware_clause`),
    in every branch, the no-claims one too.

    ``boundary`` (review of P2.5b): the sentence is the boundary's — `export`
    over its re-run, the package it writes — and says *ready* plainly. Every
    other caller reads the verdict cache, which the inner loop never re-executes
    and a hand can forge, so its *ready* (the project's or a milestone's) is
    "as last evaluated", said in the sentence (invariant 12). The default is the
    cache's: a new reader that forgets the flag says less, never more.

    ``milestone`` (P2.5b-D18): the same sentence for one spend — `<rev> is
    ready for <m>: …` / `<rev> is NOT ready for <m>: k of n required claims …`,
    from the one predicate (`claims.unresolved` with the milestone), an id no
    claim holds named among them; the hardware clause after it as for the
    project. One renderer: REPORT.md's milestone variant, the package's
    REPORT.md and `export`'s line all call this. *Rejected:* a second sentence
    builder for milestones (two answers to "is it ready").

    Every branch that is not ready SAYS so, the never-evaluated one included,
    and the claims not required are grouped by word as the required ones are,
    a crash counted apart. What slipped through (review of P2.1): a project
    with one typed physical pass and no verdict read "has never been evaluated
    … Nothing below is checked … 1 claim is checked on an article" — no NOT
    ready, and a contradiction in one sentence; and a crash on a claim not
    required was "unresolved", its errored said only on the count line.

    ``register="site"`` (architecture phase 5, SPEC 34): the same branches,
    the same predicate and the same groups, each branch's words from
    ``HUMAN["site"]["readiness"]`` in ASD-STE100, never markdown. The CLI
    branch's bytes are untouched (its transcript and shapes are pinned).
    *Rejected:* a second sentence builder for the page (two answers to "is it
    ready").
    """
    if register == "site":
        return _site_sentence(ledger, composed, registry, milestone=milestone,
                              boundary=boundary)
    rev = ledger.meta.revision or "this revision"
    bold = (lambda s: f"**{s}**") if markdown else (lambda s: s)
    total = len(ledger.claims)
    checked = word(ClaimStatus.PASS)

    if total == 0:
        return bold(f"{rev} has no claims recorded, so nothing has been evaluated.") + \
            " A project with no claims is not ready — start with one: write" \
            " `claims/C1.json`, a statement and an acceptance. " + \
            " ".join(_hardware_clause(ledger, composed))

    if milestone is not None:
        return " ".join([_milestone_sentence(ledger, composed, milestone, rev, bold,
                                             boundary=boundary),
                         *_hardware_clause(ledger, composed)])

    found = readiness(ledger, composed)
    status_of = {cid: c.status for cid, c in composed.items()}
    # Checked on an article counts as Checked (review of P2.5a: the tally read
    # PASS alone, so "3 of 7 claims are checked" sat above "7 claims · 4
    # checked" and a report listing C5 under the checked section).
    n_checked = sum(1 for s in status_of.values()
                    if s in (ClaimStatus.PASS, ClaimStatus.VERIFIED))
    tally = (f"{n_checked} of {total} {_plural(total, 'claim')} "
             f"{_plural(n_checked, 'is', 'are')} {checked} against the current inputs.")

    parts: list[str] = []
    # NB: "never evaluated" keys off whether any VERDICT exists, not off run
    # metadata. The sweep record this used to consult was bookkeeping a caller
    # could legitimately not have written; verdicts are the evidence.
    # A gate with nothing recorded reads "not yet qualified" from P2.3 — a
    # verdict the resolver words, of no run (`verdicts.never_run`): it is not an
    # evaluation, and a project whose only verdicts are those has never been
    # evaluated. What slipped through the first cut: the branch went dead.
    if not [v for v in ledger.verdicts if not verdict_logic.never_run(v)]:
        verdict = (f"{rev} has never been evaluated" if found["ready"]
                   else f"{rev} is NOT ready and has never been evaluated")
        parts.append(bold(f"{verdict}: no verdict of any kind is"
                          f" recorded against its {total} {_plural(total, 'claim')}."))
        parts.append(f"Nothing below is {checked}, because no evaluator has run —"
                     f" `nopekit check --tier 0` is the first step.")
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
        parts.append(bold(HUMAN["readiness"]["ready_last"].format(rev=rev, checked=checked)))

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

    parts += _hardware_clause(ledger, composed)
    return " ".join(parts)


def _first_sentence(template: str) -> str:
    """A branch template's first sentence, cut in the TEMPLATE before it is
    formatted, so a revision named ``v0.2`` or ``rev. 3`` never moves the cut."""
    head, dot, _rest = template.partition(". ")
    return head + "." if dot else template


def site_headline(ledger: Ledger, composed: Mapping[str, Any]) -> str:
    """The readiness sentence's first sentence on the page — `v0.2 is NOT
    ready.`, `v0.2 is ready, as last evaluated.` — from the one builder
    (`_site_sentence`'s branch, its template cut by `_first_sentence`), so the
    marketing page's large line is never a second answer to "is it ready".
    *Rejected:* splitting the baked sentence at its first ". " (a revision with
    a dot in it moves the cut); a headline table of its own (two producers)."""
    return _site_sentence(ledger, composed, None, headline=True)


def _site_sentence(ledger: Ledger, composed: Mapping[str, Any], registry: Any, *,
                   milestone: Any = None, boundary: bool = False,
                   headline: bool = False) -> str:
    """`_verdict_sentence`'s site register: branch for branch the CLI's, from
    the same `readiness`, `_groups`, `_unrun_specs` and `_hardware_clause`,
    worded by ``HUMAN["site"]["readiness"]``. ``headline`` returns only the
    first sentence of the project's branch (`site_headline`); no other part is
    built, so ``registry`` is not read."""
    said = HUMAN["site"]["readiness"]
    rev = ledger.meta.revision or "this revision"
    total = len(ledger.claims)
    checked = word(ClaimStatus.PASS)
    if total == 0:
        if headline:
            return _first_sentence(said["no_claims"]).format(rev=rev)
        return " ".join([said["no_claims"].format(rev=rev),
                         *_hardware_clause(ledger, composed, register="site")])
    if milestone is not None and not headline:
        return " ".join([_milestone_sentence(ledger, composed, milestone, rev, lambda t: t,
                                             boundary=boundary, register="site"),
                         *_hardware_clause(ledger, composed, register="site")])
    found = readiness(ledger, composed)
    status_of = {cid: c.status for cid, c in composed.items()}
    n_checked = sum(1 for st in status_of.values()
                    if st in (ClaimStatus.PASS, ClaimStatus.VERIFIED))
    tally = said["tally"].format(k=n_checked, n=total, claims=_plural(total, "claim"),
                                 is_=_plural(n_checked, "is", "are"), checked=checked)
    if not [v for v in ledger.verdicts if not verdict_logic.never_run(v)]:
        key = "never_ready" if found["ready"] else "never"
    elif not found["required"]:
        key = "none_required"
    elif found["unresolved"]:
        key = "not_ready"
    else:
        key = "ready_last"
    if headline:
        return _first_sentence(said[key]).format(rev=rev)
    parts: list[str] = []
    if key in ("never_ready", "never"):
        parts.append(said[key].format(rev=rev, n=total, claims=_plural(total, "claim")))
    elif key == "none_required":
        parts.append(said["none_required"].format(rev=rev, n=total,
                                                  claims=_plural(total, "claim")))
        parts.append(tally)
    elif key == "not_ready":
        n, req = len(found["unresolved"]), len(found["required"])
        parts.append(said["not_ready"].format(
            rev=rev, k=n, req=req, claims=_plural(req, "claim"), is_=_plural(n, "is", "are"),
            groups=_groups(ledger, composed, found["unresolved"])))
        parts.append(tally)
    else:
        parts.append(said["ready_last"].format(rev=rev, checked=checked))
    other = [c for c in ledger.claims if not c.critical
             and status_of.get(c.id) not in (ClaimStatus.PASS, ClaimStatus.VERIFIED)]
    if other:
        parts.append(said["other"].format(n=len(other), claims=_plural(len(other), "claim"),
                                          is_=_plural(len(other), "is", "are"),
                                          groups=_groups(ledger, composed, other)))
    unrun = _unrun_specs(ledger, registry)
    if unrun and ledger.verdicts:
        parts.append(said["unrun"].format(n=len(unrun),
                                          evaluators=_plural(len(unrun), "evaluator")))
    parts += _hardware_clause(ledger, composed, register="site")
    return " ".join(parts)


def _milestone_sentence(ledger: Ledger, composed: Mapping[str, Any], milestone: Any,
                        rev: str, bold: Callable[[str], str], *,
                        boundary: bool = False, register: str = "cli") -> str:
    """The milestone variant's head (P2.5b §2.4), from `claims.unresolved` —
    the boundary's words plainly, a cache reader's as last evaluated.
    ``register="site"``: the site's words for the same branches."""
    said = HUMAN["site"]["readiness"] if register == "site" else HUMAN["readiness"]

    name = str(getattr(milestone, "id", "") or "")
    if not list(getattr(milestone, "requires", None) or ()):
        return bold(said["requires_nothing"].format(rev=rev, m=name))
    found = claim_logic.unresolved(ledger, composed, milestone)
    tail = "" if boundary else "_last"
    if found.ready:
        return bold(said["ready_for" + tail].format(rev=rev, m=name,
                                                    checked=word(ClaimStatus.PASS)))
    n = len(found.required) + len(found.missing)
    k = len(found.unresolved) + len(found.missing)
    groups = "; ".join(part for part in (
        _groups(ledger, composed, found.unresolved) if found.unresolved else "",
        _missing_words(found.missing) if found.missing else "") if part)
    return bold(said["not_ready_for" + tail].format(rev=rev, m=name, k=k, n=n,
                                                    claims=_plural(n, "claim"),
                                                    is_=_plural(k, "is", "are"),
                                                    groups=groups))


# --------------------------------------------------------------------------- #
# markdown sections
# --------------------------------------------------------------------------- #
def _section_proven(ledger: Ledger, composed: Mapping[str, Any],
                    cover: dict[str, list[str]], *, stale: bool,
                    registry: Any = None) -> list[str]:
    """The checked table. Every row cites a gate that ran and the file it wrote.

    The heading starts with `SECTION_PROVEN`, never a literal: invariant 4's tests
    find the section by that constant, and fail when it is missing. Only a claim
    that reads Checked (`pass`) on its evidence is here: one whose evaluators
    contradict the status is listed loudly in the failing section instead
    (`_disagreement`, D18).

    The *Value* column (GLOSSARY §9; P2.4-D11) holds the values compared with
    the claim's acceptance condition (`claims.cross_check`), and the detail of
    a pass with no value; a value of another quantity or units is listed under
    the table as not compared (`claims.not_compared`), and a guard's — a
    prerequisite of another of the claim's evaluators — leaves the cell (its
    evaluator is still named). What slipped through (S-46): the column read
    `Measured`, and C2's cell held the guard's `8.57 L/h` and C4's
    `min_wall`'s 7 mm beside its bed fit — values never compared with the
    claim, printed as if they had been.
    """
    out = [f"{SECTION_PROVEN} {_proven_qualifier()}", ""]
    rows: list[str] = []
    notes: list[str] = []
    checked = word(ClaimStatus.PASS)
    said = HUMAN["acceptance"]
    needs = {s.id: list(getattr(s, "needs", None) or ()) for s in _specs(registry)}
    bases = _bases(registry)

    for claim in ledger.claims:
        if composed[claim.id].status is not ClaimStatus.PASS:
            continue
        if _disagreement(ledger, claim, composed[claim.id], cover):
            continue
        verdicts = _ok_verdicts(ledger, claim)
        # SPEC 38: each evaluator's basis beside it — shown, never a status.
        gates = ", ".join(_code(v.gate) + (f" (basis: {bases[v.gate]})" if v.gate in bases
                                           else "") for v in verdicts)
        unlisted = dict(claim_logic.not_compared(claim, verdicts, needs))
        measured_bits = []
        evidence: list[str] = []
        for v in verdicts:
            compared = claim_logic.cross_check(claim, v).state in ("holds", "fails")
            if compared:
                measured_bits.append(f"{_num(v.measured)} {v.units}".strip())
            elif v.measured is None and v.detail:
                # A boolean gate ("watertight: yes") has no number, and an
                # empty value cell reads as missing evidence rather than as
                # a pass with no scalar. Show the gate's one-line detail.
                measured_bits.append(_trunc(v.detail, 56))
            if v.gate in unlisted:
                value = f"{_num(v.measured)} {v.units}".strip()
                if unlisted[v.gate] == "units":
                    notes.append(said["not_compared_units"].format(
                        claim=_cell(claim.id), gate=v.gate, value=value,
                        units=v.units or "no units",
                        wanted=claim.acceptance.units or "no units"))
                else:
                    notes.append(said["not_compared_row"].format(
                        claim=_cell(claim.id), gate=v.gate, value=value,
                        settles=getattr(v, "settles", "") or "another quantity",
                        quantity=claim.acceptance.quantity or "its quantity"))
            evidence.extend(v.evidence or [])
        measured = "; ".join(measured_bits) or said["no_value"]

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

    physical = _proven_physical(ledger, composed, cover)
    if rows or physical:
        if rows:
            columns = said["columns"]
            out.append("| " + " | ".join(columns) + " |")
            out.append("|" + "---|" * len(columns))
            out.extend(rows)
            out.append("")
        out.extend(physical)
        if notes:
            out.append(said["not_compared_head"])
            out.extend(notes)
            out.append("")
        lead = HUMAN["lead"]
        out.append(f"Every row above is {checked}: each of its evaluators ran and passed "
                   f"against the inputs, code and controls it has now, "
                   f"{said['closing_counts']}. A "
                   f"{lead[ClaimCause.SKIPPED]}, {lead[ClaimCause.ERRORED]}, "
                   f"{lead[ClaimCause.UNQUALIFIED]}, {lead[ClaimCause.INVALIDATED]} or "
                   f"{lead[ClaimCause.UNRUN]} evaluator — {said['closing_more']} — puts its "
                   f"claim in another section with the reason, never here. And {checked} "
                   f"does not mean true.")
    elif not ledger.claims:
        out.append("Nothing — there are no claims.")
    elif stale:
        out.append(f"**Nothing.** Every verdict is marked invalidated, so no claim reads "
                   f"{words(ClaimStatus.PASS).term} now. Passing yesterday is not "
                   f"{checked} today. Re-run `nopekit check`.")
    elif not [v for v in ledger.verdicts if not verdict_logic.never_run(v)]:
        out.append("**Nothing.** No evaluator has ever run in this project.")
    else:
        out.append(f"**Nothing.** No claim reads {words(ClaimStatus.PASS).term} now. "
                   f"The sections below say why for each one.")
    out.append("")
    return out


def _proven_physical(ledger: Ledger, composed: Mapping[str, Any],
                     cover: dict[str, list[str]]) -> list[str]:
    """The checked section's physical rows (P2.5a; invariant 4): "Checked on an
    article" — the article, who recorded it, the evidence — and "Checked by
    expert judgment" — the authority and when. Only a VERIFIED claim whose
    standing counts (``_disagreement`` empty); a moved, uncounted or
    claim-moved pass is in its own section, never here. What slipped through
    the code before P2.5a (V-9): ``_section_proven`` listed ``pass`` only, so
    the first VERIFIED would have appeared in no section at all."""
    said = HUMAN["physical"]
    article_rows: list[str] = []
    judged_rows: list[str] = []
    for claim in ledger.claims:
        found = composed[claim.id]
        if found.status is not ClaimStatus.VERIFIED:
            continue
        if _disagreement(ledger, claim, found, cover):
            continue
        standing = getattr(claim, "standing", None)
        entry = _entry(claim, getattr(standing, "counted", None))
        if found.cause is ClaimCause.JUDGED:
            judged_rows.append(f"- **{_cell(claim.id)}** {_cell(_claim_text(claim))} — "
                               f"{HUMAN['lead'][ClaimCause.JUDGED].format(authority=_one(claim.authority))}, "
                               f"{_one(getattr(entry, 'when', ''))} "
                               f"({recorded_words(entry)})")
            continue
        evidence = list(getattr(entry, "evidence", None) or ())
        shown = ", ".join(_code(p) for p in evidence[:3]) or "*none written*"
        article_rows.append(f"- **{_cell(claim.id)}** {_cell(_claim_text(claim))} — "
                            f"article `{article12(getattr(standing, 'article', ''))}`, "
                            f"{recorded_words(entry)}, {_one(getattr(entry, 'when', ''))}; "
                            f"evidence {shown}")
    out: list[str] = []
    if article_rows:
        out += [f"{said['checked_article']}:", ""] + article_rows + [""]
    if judged_rows:
        out += [f"{said['checked_judgment']}:", ""] + judged_rows + [""]
    return out


def _record_line(claim_id: str, article: str = "") -> str:
    """The command that records a result: on ``article`` when the report is a
    package's (`--article`, so the result binds to what was built and a fail
    on it can be answered by a reprint), else on the design."""
    if article:
        return HUMAN["physical"]["record_article"].format(id=claim_id,
                                                          article=article12(article))
    return HUMAN["physical"]["record"].format(id=claim_id)


def _section_pending_build(ledger: Ledger, composed: Mapping[str, Any], *,
                           article: str = "") -> list[str]:
    """Physical claims that wait on an article: how to settle each, and those a
    physical pass was recorded for that no article binds to the current inputs
    (Pending build too, until article binding — the pass is listed, never
    counted as Checked). ``article``: the package's (`_record_line`)."""
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
            out.append(f"  - **Record the result:** " + _record_line(claim.id, article))
        out.append("")

    if verified:
        out.append("These have a pass recorded that does not count, so they are not "
                   "checked — each says why, and the person who tested it records it in "
                   "their own shell:")
        out.append("")
        for claim in verified:
            res = claim.physical_result
            standing = getattr(claim, "standing", None)
            entry = _entry(claim, getattr(standing, "counted", None)) if standing else res
            when = _one(getattr(entry, "when", "") or "date not recorded")
            detail = (f" — {_trunc(entry.detail, 160)}"
                      if entry is not None and entry.detail else "")
            ev = ""
            if entry is not None and entry.evidence:
                ev = " [" + ", ".join(_code(p) for p in entry.evidence[:3]) + "]"
            crit = "" if claim.critical else " *(not required)*"
            why = reason(composed[claim.id], ledger, claim, full=True)
            out.append(f"- **{claim.id}** {_claim_text(claim)}{crit} — {why}, "
                       f"{when}{detail}{ev}")
            out.append(f"  - **Record the result:** " + _record_line(claim.id, article))
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
            "settled by any observation — give it an acceptance condition, or say what "
            "would settle it in its mechanism (\"how\"), or it will stay on this list "
            "forever")


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
    outside = by_cause[ClaimCause.OUTSIDE_CONTEXT]
    unjudged = (by_cause[ClaimCause.NO_AUTHORITY]
                + by_cause[ClaimCause.AUTHORITY_UNATTRIBUTED])

    if not gaps and not needs:
        if not ledger.claims:
            out.append("No claims, so nothing to gap. This is not good news.")
        else:
            out.append(f"None. Every automated claim has at least one evaluator, and no "
                       f"claim reads {words(gap).term}.")
        out.append("")
        return out

    if needs or no_evaluator:
        out.append(f"A claim with no evaluator is a {word(gap)}, not a defect. Its mechanism "
                   f"says what would settle it; the cheapest evaluator that runs it comes "
                   f"first, and a tool is installed or written only with its cost said out "
                   f"loud before anyone agrees to it.")
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
            if claim and claim.note:
                out.append(f"  - **Mechanism:** {_trunc(mechanism_body(claim), 220)}")
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
                       "`nopekit gap --propose`")
        out.append("")
    loose = [c for c in no_evaluator if c.id not in recorded]
    if loose:
        out.append(f"{len(loose)} {_plural(len(loose), 'claim')} "
                   f"{_plural(len(loose), 'reads', 'read')} {words(gap).term} with no "
                   f"evaluator and no gap record names "
                   f"{_plural(len(loose), 'it', 'them')}: "
                   f"{_ids(loose, limit=12)}. Run `nopekit gap --propose`.")
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
            if claim.note:
                out.append(f"  - **Mechanism:** {_trunc(mechanism_body(claim), 220)}")
        out.append("")

    if outside:
        # P2.4 (P2.1-D19: every unresolved claim in exactly one section): a pass
        # outside its evaluator's operating context counts nowhere.
        out.append(HUMAN["heading"]["gaps_context"])
        out.append("")
        out.append(HUMAN["context"]["gaps_intro"].format(
            pass_=HUMAN["outcome"]["pass"], fail=HUMAN["outcome"]["fail"], gap=word(gap)))
        out.append("")
        for claim in outside:
            why = reason(composed[claim.id], ledger, claim, full=True,
                         stale_reasons=stale_reasons)
            out.append(f"- **{claim.id}** {_claim_text(claim)} — {why}")
            if claim.note:
                out.append(f"  - **Mechanism:** {_trunc(mechanism_body(claim), 220)}")
        out.append("")

    if unjudged:
        # P2.5a (P2.1-D19: every unresolved claim in exactly one section).
        out.append("### Expert judgments their authority has not recorded")
        out.append("")
        out.append(f"A claim that ends in expert judgment reads "
                   f"{words(ClaimStatus.ASSERTED).term} under its authority's name only "
                   f"once they record it in their own shell; a name in the claim file "
                   f"records nothing, so until then it is a {word(gap)}.")
        out.append("")
        for claim in unjudged:
            why = reason(composed[claim.id], ledger, claim, full=True,
                         stale_reasons=stale_reasons)
            out.append(f"- **{claim.id}** {_claim_text(claim)} — {why}")
            if claim.note:
                out.append(f"  - **Mechanism:** {_trunc(mechanism_body(claim), 220)}")
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
            if claim.note:
                out.append(f"  - **Mechanism:** {_trunc(mechanism_body(claim), 220)}")
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
                   "`nopekit why <param>` is empty for each of these.")
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
                   "`nopekit extract <id> --what ... --grounds ...` fixes it.")
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
                  stale_gates: Collection[str], *, unseen: bool = False) -> str:
    """`: `g.one` is invalidated` — which covering gates made a claim Stale, when
    the caller said (``stale_gates``); ``""`` under the all-gates alias. The
    reason each gate moved is the resolver's and lives in `nopekit status`; the
    report names the gate so the reader knows which result to re-check. With
    ``unseen`` (the claim's cause is `unseen`, issue #15 D1): `: `g.one` reads
    inputs that nopekit cannot see` — never "invalidated" of a gate whose
    inputs, as far as nopekit sees, did not move."""
    stale = [gid for gid in (cover.get(claim.id) or list(claim.gates or []))
             if gid in set(stale_gates)]
    if not stale:
        return ""
    if unseen:
        return (": " + ", ".join(_code(g) for g in stale)
                + f" {_plural(len(stale), 'reads', 'read')} inputs that nopekit cannot see")
    return (": " + ", ".join(_code(g) for g in stale)
            + f" {_plural(len(stale), 'is', 'are')} "
            + HUMAN["lead"][ClaimCause.INVALIDATED])


def _tier_flag(registry: Any, gates_: Iterable[str]) -> str:
    """`` --tier N`` for the costliest of ``gates_`` above `nopekit check`'s
    default tier (0), else ``""`` — the flag a suggested check run needs to
    reach them (`page.settle` keeps the same rule for a claim's evaluators).
    What slipped through (review of P2, and again in issue #15's D1): a reader
    advised a plain `nopekit check` for a tier-2 solver it cannot run."""
    wanted = set(gates_ or ())
    tier = max((int(spec.tier) for spec in _specs(registry) if spec.id in wanted), default=0)
    return f" --tier {tier}" if tier > 0 else ""


#: The head of `verdicts._admission`'s reason for an evaluator whose control
#: entries at this version are not current only because they read through a
#: channel nopekit cannot see (``_not_yet(... f"control inputs moved: {why}")``
#: over ``_control_moved``'s ``"opaque control inputs: "``). Spelled here, as
#: `claims.OPAQUE_REASON` is, because no reader may edit `verdicts` for a word;
#: `test_unseen.TheCfdReadersSayIt` holds it to a real admission.
OPAQUE_CONTROL_REASON = "control inputs moved: opaque control inputs: "


def unseen_qualification(admission: Any) -> bool:
    """Is ``admission`` (`verdicts.admission_state`'s) an evaluator qualified at
    this version — its newest control entry here is paired — whose controls are
    not current only because they read inputs nopekit cannot see? Then `gate
    show` and `doctor` say ``HUMAN["unseen"]["qualification"]``, never "the
    next check run qualifies it": every check run since had (review of issue
    #15's D1)."""
    entry = getattr(admission, "entry", None)
    return (getattr(admission, "state", "") == "undemonstrated"
            and str(getattr(admission, "reason", "") or "").startswith(OPAQUE_CONTROL_REASON)
            and getattr(entry, "admitted", "") == "paired")


#: Programs a gate runs only to start another (`bash -c`, `sh -c`): a poor
#: example of what nopekit cannot see, so `unseen_words` names another first.
_LAUNCHERS = frozenset({"subprocess:bash", "subprocess:sh", "subprocess:env"})


def unseen_words(key: str, resolution: Any, unseen: Iterable[str], *,
                 registry: Any = None, rows: str = "", spend: bool = False) -> str:
    """``HUMAN["unseen"][key]`` for the gates in ``unseen`` (`claims.unseen_gates`),
    in the resolution's order: how many, the first by name, how many channels
    their entries recorded and one of them — a program first, never a launcher
    shell (`_LAUNCHERS`) — and the tier flag that reaches them; ``""`` when
    there are none. ``rows`` is `doctor`'s per-gate list; ``spend`` (the project
    has a milestone) adds the boundary that runs them again. One producer for
    `status`, `site build`, `check` and `doctor` (issue #15, D1)."""
    wanted = set(unseen or ())
    order = [g for g in (getattr(resolution, "rows", None) or {}) if g in wanted]
    order += sorted(wanted - set(order))
    if not order:
        return ""
    channels: list[str] = []
    for gid in order:
        row = (getattr(resolution, "rows", None) or {}).get(gid)
        reads = getattr(getattr(row, "entry", None), "reads", None)
        for name in (reads.get("opaque") or () if isinstance(reads, Mapping) else ()):
            if name not in channels:
                channels.append(str(name))
    programs = [c for c in channels if c.startswith("subprocess:") and c not in _LAUNCHERS]
    example = (programs or channels or ["?"])[0]
    n = len(order)
    shown = order[0] + (f", +{n - 1}" if n > 1 else "")
    their = _plural(n, "its", "their")
    return HUMAN["unseen"][key].format(
        shown=shown, n=n, evaluators=_plural(n, "evaluator"), read=_plural(n, "reads", "read"),
        their=their, Their=their.capitalize(), they=_plural(n, "it", "they"),
        results=_plural(n, "result"), are=_plural(n, "is", "are"), k=len(channels),
        example=example, tier_flag=_tier_flag(registry, order), rows=rows,
        spend=HUMAN["unseen"]["spend"] if spend else "")


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
                     stale_reasons: Mapping[str, str] | None = None,
                     required_word: str = "critical") -> list[str]:
    """Everything that is red, with the verdict line that made it red — and any
    claim the resolver calls Checked that its evidence contradicts, first and
    loudly (D18)."""
    out = [HUMAN["heading"]["failing"], ""]
    disagreeing = {c.id: why for c in ledger.claims
                    if (why := _disagreement(ledger, c, composed[c.id], cover))}
    bad = in_severity(ledger, composed,
                      [c for c in ledger.claims
                       if composed[c.id].status in _FAILING_SECTION])
    bad = [c for c in ledger.claims if c.id in disagreeing] + bad

    cited: set[str] = set()

    if bad:
        for claim in bad:
            found = composed[claim.id]
            flag = required_word if claim.critical else "not required"
            if claim.id in disagreeing:
                out.append(f"### {status_tag(found.status)} {claim.id} — "
                           f"{_claim_text(claim)}  *(status and evidence disagree, {flag})*")
                out.append(f"- **status and evidence disagree — "
                           f"{disagreeing[claim.id]}**")
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
                # An unqualified evaluator in its qualification's words, never
                # a crash's tag (P2.3-D17, invariant 2 read the other way).
                out.append(f"- `{verdict_line(v)}`")
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
                advice = HUMAN["physical"]["stale_advice"].get(
                    found.cause.value, HUMAN["physical"]["stale_advice"]["invalidated"])
                key = found.cause.value
                if key == "article-unjudged" and _judgment(claim):
                    key = "judgment-unjudged"
                advice = HUMAN["physical"]["stale_advice"].get(key, advice)
                # Two sentences, never a clause run into a capitalised one
                # (review of P2.5a: "… *now* — A new article is needed — …").
                # The command carries the tier that reaches the gates it names:
                # what slipped through (review of P2), a plain `nopekit check`
                # is tier 0, which runs no solver.
                head = ("- Passed, but nopekit cannot see if the result is current"
                        if found.cause is ClaimCause.UNSEEN
                        else "- Passed, but not against the current inputs")
                out.append(head
                           + _stale_suffix(claim, cover, stale_gates,
                                           unseen=found.cause is ClaimCause.UNSEEN)
                           + f". Nothing here is {word(ClaimStatus.PASS)} *now*. "
                           + advice.format(authority=_one(getattr(claim, "authority", "")),
                                           check="nopekit check"
                                           + _tier_flag(registry, found.cites)))
            if claim.physical_result and claim.physical_result.passed is not True:
                res = claim.physical_result
                article = article12(getattr(res, "article", None) or {})
                on = f" on article `{article}`" if article else ""
                row = "judgment_failed_row" if _judgment(claim) else "failed_row"
                out.append(HUMAN["physical"][row].format(
                    when=_one(res.when), on=on, recorded=recorded_words(res),
                    detail=_trunc(res.detail, 200)))
                if found.cause is ClaimCause.CONTRADICTION:
                    for item in getattr(res, "contradicts", None) or ():
                        if not (isinstance(item, Mapping) and item.get("inside") is True):
                            continue
                        unit = f" {item.get('units')}" if item.get("units") else ""
                        measured = (f"measured {_num(res.measured)}{unit}"
                                    if res.measured is not None else "failed")
                        out.append(HUMAN["physical"]["contradiction_row"].format(
                            gate=_code(str(item.get("gate"))),
                            code=article12(item.get("code")),
                            value=f"{_num(item.get('value'))}{unit}", measured=measured))
            out.append("")
    else:
        out.append("No claim is failing, stale, skipped or open.")
        out.append("")

    predicted = claim_logic.rebuild(ledger)
    if predicted:
        said = HUMAN["physical"]
        out.append(said["rebuild_heading"])
        out.append("")
        out.append(said["rebuild_intro"])
        out.append("")
        for found in predicted:
            out.append(said["rebuild_row"].format(
                article=article12(found.article), claims=", ".join(found.claims),
                moves=verdict_logic._stale_text(found.moved) if found.moved
                else "the design moved"))
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
            token = getattr(v, "unqualified", "") or ""
            why = ((HUMAN["context"]["tally"] if is_outside(token)
                    else HUMAN["lead"][ClaimCause.UNQUALIFIED]) if token
                   else HUMAN["outcome"].get(v.outcome, v.outcome))
            out.append(f"- `{verdict_line(v)}`  *({why}; claims: "
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


#: The column the Reproduce block's comments start at: `nopekit check --tier 0`
#: and ten spaces, where the block's first commands put theirs. A command as long
#: or longer keeps two spaces before its `#` (`_commented`). What slipped through
#: (review of P2.5b, findings 13, 21): `.ljust(33)` pads nothing past 33
#: characters, so the bracket's own `nopekit export print-v1 --dry-run` was
#: glued to its comment — `--dry-run#` is one word to a shell, and the pasted
#: line failed. *Rejected:* a column computed from the longest command (one
#: long milestone name would push every comment in the block across the page).
_REPRODUCE_COLUMN = 33


def _commented(command: str, comment: str) -> str:
    """``command``, then ``# comment`` at ``_REPRODUCE_COLUMN`` — or two spaces
    after a command that reaches it: a `#` is never glued to a word."""
    return command.ljust(max(_REPRODUCE_COLUMN, len(command) + 2)) + "# " + comment


def _section_reproduce(ledger: Ledger, registry: Any, *, root: str = "") -> list[str]:
    """The exact commands, and the code behind each row. A readiness report
    nobody can re-derive is a press release.

    No time, no rho, no hash of the run: those change on every re-run of an
    unchanged design, and this file is tracked (S-89). What it names instead is
    what a re-run would re-derive — the command per gate, and the files that
    gate's verdict is keyed on.
    """
    out = [HUMAN["heading"]["reproduce"], ""]
    # Every milestone gets its line: the cap of four it replaced dropped the
    # fifth with nothing said (review of P2.5b, finding 21).
    specs = _specs(registry)
    max_tier = max((int(s.tier) for s in specs), default=0)

    out.append("This file is generated. Re-derive every row above with:")
    out.append("")
    out.append("```sh")
    out.append("nopekit check --tier 0"
               "          # the inner loop: analytic gates only, seconds")
    if max_tier > 0:
        out.append(f"nopekit check --tier {max_tier}"
                   f"          # everything registered"
                   f"{' — solvers included' if max_tier >= 2 else ''}")
    out.append("nopekit report --write"
               "          # renders REPORT.md, an output git ignores")
    for declared in list(getattr(ledger, "milestones", None) or ()):
        out.append(_commented(f"nopekit export {declared.id} --dry-run",
                              "re-runs what it requires, at the boundary"))
    out.append("```")
    out.append("")
    out.append("`check` re-runs only the gates whose inputs, code or control moved; "
               "every other verdict is served from `.nopekit/verdicts/` as it was "
               "recorded. `nopekit check --force` re-runs all of them.")
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
        bases = _bases(registry)
        for gate_id, covers in per_gate[:_MAX_REPRODUCE_GATES]:
            files = code.get(gate_id)
            where = f" — {', '.join(files)}" if files else ""
            basis = f" — basis {bases[gate_id]}" if gate_id in bases else ""
            out.append(f"nopekit check --only {gate_id.ljust(width)}   # {covers}{basis}"
                       f"{where}")
        if len(per_gate) > _MAX_REPRODUCE_GATES:
            out.append(f"# ... and {len(per_gate) - _MAX_REPRODUCE_GATES} more;"
                       f" `nopekit gate list` prints them all")
        out.append("```")
        out.append("")

    # The comment is `gate selftest`'s own help, from the one table (P2.3-D16):
    # the lines it replaced said "negative control" and named the known-bad
    # half alone — a GLOSSARY §2 Never-say, and half of what qualifies a gate.
    out.append("And show the gates above can tell a good design from a bad one, which "
               "is the only reason their passes mean anything:")
    out.append("")
    out.append("```sh")
    words = HUMAN["qualification"]["help"].split()
    lines, line = [], ""
    for word in words:
        if line and len(line) + 1 + len(word) > 60:
            lines.append(line)
            line = word
        else:
            line = f"{line} {word}" if line else word
    lines.append(line)
    out.append(_commented("nopekit gate selftest", lines[0]))
    out.extend(" " * _REPRODUCE_COLUMN + "# " + rest for rest in lines[1:])
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
                    stale_reasons: Mapping[str, str] | None = None,
                    milestone: Any = None, boundary: bool = False,
                    article: str = "", test_card: Sequence[str] = (),
                    amber: Sequence[str] | None = None,
                    unseen_gates: Collection[str] = ()) -> str:
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

    From P2.5b, under the sentence (its head unmoved, C-6), the limits
    paragraph (M18.1) and each milestone's line, as last evaluated
    (`milestone_line`; no `##` heading of its own: `ReportSectionOrder` pins the
    order, and a heading above `SECTION_PROVEN` for a list of one-liners would
    move it). ``milestone`` renders the report for one spend — the package's
    REPORT.md, `report --milestone`: titled for it, its sentence the milestone
    variant, every claim flagged required or not by the milestone's
    ``requires`` and never by ``critical`` (critique 7 of the P2.5b design: the
    package's body said `critical` under a head that said the milestone), and a
    line saying that the claims it does not require are shown as last evaluated
    (critique 18).

    ``boundary`` (review of P2.5b): the package's copy, rendered over the
    export's re-run — its sentence plain, its line naming the claims that were
    not re-run. Without it (`report --milestone`, the cache's) the sentence is
    "as last evaluated" and the line says nothing here was re-run: what slipped
    through was the package's words on the cache's view, a forged pass read
    "ready … checked against the current inputs". ``article`` and
    ``test_card`` (the package's alone): every "Record the result" line names
    the article the package was built as (`--article`), and the card — what
    to measure on it — is printed under the head (finding 16: a builder
    following the package's REPORT.md recorded on a design article no reprint
    could ever answer).
    """
    composed = _compositions(ledger, registry, stale, stale_gates, unseen_gates)
    cover = _coverage(ledger, registry)

    name = ledger.meta.name or "(unnamed project)"
    rev = ledger.meta.revision or "unversioned"
    said = HUMAN["readiness"]
    if milestone is not None:
        heading = title or said["title_for"].format(name=name, m=milestone.id, rev=rev)
    else:
        heading = title or f"{name} — readiness ({rev})"

    out: list[str] = [f"# {heading}", ""]
    out.append(_verdict_sentence(ledger, composed, registry, stale=stale, markdown=True,
                                 milestone=milestone, boundary=boundary))
    out.append("")
    out.append(limits_line(ledger))
    out.append("")
    requires = set(getattr(milestone, "requires", None) or ())
    if milestone is not None:
        others = [c for c in ledger.claims if c.id not in requires]
        if not boundary:
            out.append(said["nothing_rerun"].format(m=milestone.id, n=len(requires)))
            out.append("")
        elif others:
            out.append(said["as_last_evaluated"].format(m=milestone.id,
                                                        n=len(requires)))
            out.append("")
        if test_card:
            out.append(HUMAN["export"]["card_heading"].format(article=article12(article)))
            out.append("")
            out.append("```")
            out += list(test_card)
            out.append("```")
            out.append("")
        # The sections flag a claim by the milestone's `requires`: the same
        # claims, each `critical` read as "required by this milestone".
        ledger = dataclasses.replace(ledger, claims=[
            dataclasses.replace(c, critical=c.id in requires) for c in ledger.claims])
    else:
        declared = list(getattr(ledger, "milestones", None) or ())
        if declared:
            out.append(HUMAN["milestone"]["report_head"])
            out += [f"- {milestone_line(ledger, composed, m)}" for m in declared]
            out.append("")
    if model_error:
        out.append(f"**The model does not load**, so no verdict that reads it is "
                   f"current: {_trunc(model_error, 300)}")
        out.append("")
    if ledger.meta.summary:
        out.append(f"> {_trunc(ledger.meta.summary, 400)}")
        out.append("")

    out += _section_proven(ledger, composed, cover, stale=stale, registry=registry)
    out += _section_amber(amber)
    out += _section_pending_build(ledger, composed, article=article)
    out += _section_gaps(ledger, composed, registry, stale_reasons=stale_reasons)
    out += _section_assumed(ledger, composed, params, model_error)
    out += _section_failing(ledger, composed, cover, registry, stale_gates=stale_gates,
                            stale_reasons=stale_reasons,
                            required_word="required" if milestone is not None else "critical")
    out += _section_requirements(ledger, composed, cover)
    out += _section_reproduce(ledger, registry, root=root)

    out.append("---")
    out.append("")
    out.append("*Generated by `nopekit report` from the ledger and its verdict cache. "
               "Do not hand-edit: it is an output, not a source. If a line here is "
               "wrong, the ledger is wrong.*")
    return "\n".join(out).rstrip() + "\n"


def _section_amber(amber: Sequence[str] | None) -> list[str]:
    """``## Amber`` (architecture phase 3b): after the Checked section, only
    when a Checked claim's amber flips or is not current — one line per claim
    in `why`'s words, and the sentence that amber changes no status. The
    lines are the caller's (`decisions.amber_text` over
    `derivation.amber_states`): this module computes no amber."""
    if not amber:
        return []
    said = HUMAN["trace"]
    return [f"## {said['report_heading']}", "", *[f"- {line}" for line in amber], "",
            said["report_line"], ""]


def render_terminal(ledger: Ledger, registry: Any, *, stale: bool = False,
                    stale_gates: Collection[str] = (),
                    params: Sequence[Any] | None = None,
                    stale_reasons: Mapping[str, str] | None = None,
                    unseen_gates: Collection[str] = ()) -> str:
    """The same report compressed to something an agent can hold in context.

    Under ~40 lines for a healthy project, which is the point: this is what
    `nopekit status` prints on every loop, and a status command that costs a
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
    composed = _compositions(ledger, registry, stale, stale_gates, unseen_gates)
    lines: list[str] = []

    name = ledger.meta.name or "(unnamed)"
    rev = ledger.meta.revision or "unversioned"
    head = f"nopekit readiness — {name} {rev}"
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
    disagreeing = {c.id: why for c in ledger.claims
                    if (why := _disagreement(ledger, c, composed[c.id], cover))}
    problems = [c for c in ledger.claims if c.id in disagreeing] + [
        c for c in in_severity(ledger, composed)
        if composed[c.id].status not in (ClaimStatus.PASS, ClaimStatus.VERIFIED)]
    for claim in problems[:_MAX_TERMINAL_CLAIMS]:
        found = composed[claim.id]
        why = (f"status and evidence disagree — {_trunc(disagreeing[claim.id], 60)}"
               if claim.id in disagreeing
               else reason(found, ledger, claim, stale_reasons=stale_reasons))
        lines.append(f"{status_tag(found.status, errored=found.errored)} {claim.id} "
                     f"{_trunc(claim.statement, 52)} — {why}")
    if len(problems) > _MAX_TERMINAL_CLAIMS:
        lines.append(f"       ... and {len(problems) - _MAX_TERMINAL_CLAIMS} more "
                     f"unresolved claims — see `nopekit report`")
    lines.extend(rebuild_line(found) for found in claim_logic.rebuild(ledger))
    # SPEC decision 12: one untagged line, so status keeps its budget and no
    # claim-row parser reads R1 as a claim; only when requirements are recorded.
    said_requirements = requirement_line(ledger, composed, cover)
    if said_requirements is not None:
        lines.append(said_requirements)

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
    marked = [v for v in ledger.verdicts if getattr(v, "unqualified", "")]
    refused = [v for v in marked if not is_outside(v.unqualified)]
    outside = [v for v in marked if is_outside(v.unqualified)]
    counted = [v for v in ledger.verdicts if not getattr(v, "unqualified", "")]
    ran = [v for v in counted if v.outcome in ("pass", "fail")]
    skipped = [v for v in counted if v.outcome == "skipped"]
    errored = [v for v in counted if v.outcome == "error"]
    unrun = _unrun_specs(ledger, registry)
    gate_bits = [f"{len(ran)} ran"]
    moved = sorted({v.gate for v in ledger.verdicts} & set(stale_gates))
    # Issue #15, D1: a gate not current only for a channel nopekit cannot see
    # is counted under its own words, never as "invalidated".
    unseen_moved = [g for g in moved if g in set(unseen_gates or ())]
    if len(moved) > len(unseen_moved):
        gate_bits.append(f"{len(moved) - len(unseen_moved)} "
                         f"{HUMAN['lead'][ClaimCause.INVALIDATED]}")
    if unseen_moved:
        gate_bits.append(f"{len(unseen_moved)} with {HUMAN['lead'][ClaimCause.UNSEEN]}")
    if skipped:
        gate_bits.append(f"{len(skipped)} {HUMAN['outcome']['skipped']}")
    if errored:
        gate_bits.append(f"{len(errored)} {HUMAN['outcome']['error']}")
    if refused:
        gate_bits.append(f"{len(refused)} {HUMAN['lead'][ClaimCause.UNQUALIFIED]}")
    if outside:
        gate_bits.append(f"{len(outside)} {HUMAN['context']['tally']}")
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

    # The tier that runs every invalidated and unrun evaluator. What slipped
    # through (review of P2): the line always said `--tier 0`, so a project
    # whose stale claims rest on a tier-2 solver was told to run a check that
    # cannot reach them.
    # A gate not current only for a channel nopekit cannot see is left out:
    # no check run makes this line read otherwise (review of issue #15's D1,
    # where `next: nopekit check --tier 2` came back after every such run).
    # When nothing else needs a check run, a milestone's boundary is the step:
    # `export` runs those evaluators again where it counts.
    unseen_set = set(unseen_gates or ())
    reach = (set(moved) - unseen_set) | {s.id for s in unrun}
    tier = max((int(s.tier) for s in _specs(registry) if s.id in reach), default=0)
    first = [f"nopekit check --tier {tier}"]
    if not reach and unseen_set & set(moved):
        waiting = [m for m in sorted(getattr(ledger, "milestones", None) or (),
                                     key=lambda m: str(m.id))
                   if not claim_logic.unresolved(ledger, composed, m).ready]
        first = [f"nopekit export {waiting[0].id} --dry-run"] if waiting else []
    lines.append("next: " + " ; ".join(first + ["nopekit gap --propose",
                                                "nopekit report --write"]))
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
                 stale_reasons: Mapping[str, str] | None = None,
                 amber: Sequence[str] | None = None,
                 unseen_gates: Collection[str] = ()) -> str:
    """Render the markdown report to `REPORT.md` at the project root — an output
    git ignores (P2.5b-D17, PLAN D-14) — and return its path. What slipped
    through while it was the tracked `docs/readiness.md` (S-41): it drifted from
    its ledger, and every candidate branch rewrote it. The CLI writes the root
    ignore block first (`store.ensure_ignore_blocks`), so a project migrated
    before P2.5b gains `/REPORT.md` before the file exists.

    Written atomically: a half-truncated readiness report left behind by a crash
    would be a document that claims less than is true, which is a strange way to
    fail but still a wrong one.

    The destination comes from `store.project_paths`, not from a join spelled
    here. Layout is `store`'s job alone; a second module that knows where the
    report lives is a second module to edit when it moves.

    `stale_gates`, `stale_reasons`, `model_error` and `params` as for
    `render_markdown`; `root` spells the gates' code files. The file holds no
    time and no rho, so rewriting it for an unchanged design and unchanged
    outcomes leaves the tracked bytes alone (S-89). `model_error` reaches the
    file as it reaches `report`'s stdout: without it, a broken model's
    parameters would be judged from whatever records exist.
    """
    path = store.project_paths(root)["report"]
    ensure_dir(os.path.dirname(path))
    atomic_write_text(path, render_markdown(ledger, registry, stale=stale,
                                            stale_gates=stale_gates, root=root,
                                            model_error=model_error, params=params,
                                            stale_reasons=stale_reasons, amber=amber,
                                            unseen_gates=unseen_gates))
    return path


# --------------------------------------------------------------------------- #
# JUnit: the same judgement, in the one format every CI already renders
# --------------------------------------------------------------------------- #
#: Where `check --junit` and `gate selftest --junit` write when given no path,
#: relative to the project root.
#:
#: Why this value: `.nopekit/out/` is gate scratch, ignored both by the
#: `.nopekit/.gitignore` that `init` writes and by this repository's own
#: `.gitignore`, so a `check --junit` in CI or on a laptop never dirties the tree.
#: *Rejected:* the project root — tracked, so every run would leave a modified
#: file behind and the clean-tree gate (G5) would read red for a report.
JUNIT_DEFAULT = ".nopekit/out/junit.xml"

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
    and a gate whose own crash reads "unqualified: …" is still a crash.
    """
    outcome = verdict.outcome
    if outcome == "pass":
        return
    if outcome == "fail":
        child = _xml_sub(case, "failure", type="fail",
                         message=verdict.detail or "the gate reported a failure")
        _xml_text(child, _junit_measured(verdict))
    elif outcome == "error":
        token = getattr(verdict, "unqualified", "") or ""
        kind = ("outside-context" if is_outside(token) else "not-admitted") if token else "error"
        child = _xml_sub(case, "error", type=kind,
                         message=unqualified_text(token) if token else str(verdict.error))
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
    disagreeing = _disagreement(ledger, claim, composed, cover)
    if disagreeing:
        child = _xml_sub(case, "failure", type="status-and-evidence-disagree",
                         message=f"status and evidence disagree — {disagreeing}")
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
                 stale_reasons: Mapping[str, str] | None = None,
                 unseen_gates: Collection[str] = (), ready_note: str = "") -> str:
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
    Phase 1.2); and `ready_note` when given — `check`'s note that its *ready*
    holds for this run only, its evaluators reading inputs nopekit cannot see
    (review of issue #15's D1: the file said `ready=true` beside nothing that
    joined it to the NOT ready of every reader after it). `when` is the caller's timestamp; this function reads no clock.
    Every attribute and text value goes through `junit_safe`, so the file
    parses whatever a gate wrote.
    """
    import xml.etree.ElementTree as ET        # ~6 ms; only `--junit` pays it

    code = int(exit_code)
    composed = _compositions(ledger, registry, stale, stale_gates, unseen_gates)
    root = ET.Element("testsuites", {"name": "nopekit check"})
    props = [("spine_version", __version__), ("exit_code", str(code)),
             ("tier", str(_int_or(tier))), ("ready", "true" if ready else "false"),
             ("all_required_checked",
              "true" if readiness(ledger, composed)["ready"] else "false"),
             ("when", when)]
    if spine:
        props.append(("spine", spine))
    if ready_note:
        props.append(("ready_note", ready_note.removeprefix("note: ")))
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
    root = ET.Element("testsuites", {"name": "nopekit gate selftest"})
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
    "RESULT_TOKENS",
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
    "prerequisite_phrase",
    "status_view",
    "words_table",
    "outcome_words",
    "need_word",
    "page_phrases",
    "limit_words",
    "recorded_by",
    "readiness",
    "not_ready_line",
    "unseen_words",
    "unseen_qualification",
    "OPAQUE_CONTROL_REASON",
    "render_terminal",
    "render_markdown",
    "write_report",
    "render_junit",
    "render_selftest_junit",
    "junit_safe",
    "mechanism_line",
    "mechanism_body",
    "mechanism_problem_words",
    "requirement_line",
    "requirement_json",
    "requirement_statuses",
    "status_word",
    "condition_text",
    "return_words",
    "rejection_words",
    "rejection_facts",
    "rejection_suffix",
    "may_return_line",
    # architecture phase 3a: the round's summary
    "status_token_word",
    "round_paragraph",
    "round_trace_sentence",
    "round_moved",
    # architecture phase 6: the round's commit and the Stop hook
    "COMMIT_SUBJECT_MAX",
    "round_commit_sentence",
    "round_prose_sentence",
    "round_commit_message",
    "round_hook_message",
    "round_hook_crash",
    # the two pages: the marketing page's headline
    "site_headline",
]
