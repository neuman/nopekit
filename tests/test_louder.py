# SPDX-License-Identifier: Apache-2.0
"""Invariant 2's second sentence: an errored evaluator reads louder than a missing tool.

``test_invariants.ErrorIsNotPass`` holds the first sentence (a crash is never a
pass). This module holds the second, in what every command PRINTS, because that
is where it can be lost without a single status changing. Today a crash rides on
FAIL (``claims.resolve_status`` rung 4), so it is loud by accident of the status
it borrows. Phase 2 moves it to Skipped (GLOSSARY §3, PLAN-v0.14 §1.4), the
status a missing tool reads, and from then on "louder" has to be carried on
purpose, in four places (PLAN-v0.14 §1.5, row "Errored stays louder"): the
reason, the order and tone of the row, the counts, and the JUnit ``<error>``.
These tests land first (R-1), green on the code before rung 4 moves, so the
change that moves it cannot quietly drop one of the four.

What slipped through, read on the code before this module (the P2.0 probe).
The ``F-n`` are this module's own findings, named here and in ``KNOWN_QUIETER``
— not PLAN-v0.14 §4.3's paper feedback F1–F5:

* **F-1** A crash's reason is its traceback: every reason prefers ``detail``,
  which ``run_gate`` fills with the traceback tail, and ``status`` cuts the
  body at 56 characters, so the row never says what crashed; and no reason
  leads ``errored:`` (GLOSSARY §3).
* **F-2** ``check``'s BLOCKING list is in record order: ``[skip ] P1`` above the
  crashed ``[FAIL ] P2``.
* **F-3** ``why`` and ``claim show`` list a claim's evaluators in gate-id order:
  ``[skip]`` above ``[ERR ]``; the report's bullets under each claim the same.
* **F-4** ``doctor`` has two rows for a missing tool and none for a gate that
  crashes at its current inputs.
* **F-5** ``state.json`` lists claims in record order, and the page keeps it.
* **F-8** Rung 4 moved alone would call a crash "blocked on missing tooling":
  ``_STATUS_PHRASE[BLOCKED]``, the ``skip`` count tag, ``_blocking_reason``'s
  BLOCKED fallback.
* **F-10** A verdict that says skipped AND errored (``Verdict(skipped=True,
  error=…)``, or the dict form) is a crash to ``Verdict.outcome`` and the claim
  ladder, and a missing tool to everything that reads the flags: ``check``
  streams it under the skip digest's quiet ``[skip]`` tag with its skip reason,
  ``status`` counts it as skipped and errored at once, and ``why``, the report
  and ``gate show`` print ``[ERR ] … : requires … (not installed)`` —
  ``Verdict.render`` prefers ``skip_reason`` to ``error``; the page's unproven
  reason for it is the skip reason too. The first draft of this module planted
  only a raising gate, which never sets ``skipped``, and was green over all of
  it.

**The shape of the tests.** One planted project (``_louder_project``): the
bracket, migrated, plus three evaluators — ``probe.a_skip`` (its tool is
absent), ``probe.y_both`` (says skipped and errored at once) and
``probe.z_crash`` (raises on the live design) — and claims P1 (the skip alone),
P2–P7 (errored: alone, beside the skip, not required, beside a passing
evaluator in both kinds, and the skipped-and-errored one), with C4 made Stale by
a parameter edit after the check run. Every order that is alphabetical or
record order puts the skip FIRST, so a renderer that does not rank cannot pass
an order check by accident. Each command runs once; pure checkers turn its
output into ``Problem`` rows; each ``ErrorIsLouder`` test asserts a channel has
none. In process, ``render_terminal``, ``render_markdown`` and ``render_junit``
run on a ledger built from real ``run_gate`` verdicts, under the same checkers.

**Monotone, and ratcheted.** Every assertion holds today AND must hold after
Phase 2 moves rung 4 (P2.1): never a pass; never the skip's quiet tag or its
words, nor its lead ``skipped:``; above every row that is neither Failing nor
errored; counted once, never inside a missing-tool or ``skipped`` count unless
split out (``N skipped (k errored)``); a JUnit ``<error>`` critical or not; and a
fail beside an error still reads Failing. Where today does not hold one — the
reason's lead ``errored:`` and its traceback (F-1), F-2 to F-5, F-10 — the exact
problem is named in ``KNOWN_QUIETER``, a ratchet that may only shrink:
``ErrorNotYetLouder`` goes red when a named problem no longer shows, so the
change that fixes a channel deletes it in the same edit and ``ErrorIsLouder``
holds that channel from then on; any other problem on the channel is red at
once. Adding to an entry is weakening an invariant test (R-6). So PLAN-v0.14
§3's P2.0 row — across every renderer an errored claim's reason starts
``errored:`` — binds here from P2.0: today's shortfall is listed, channel by
channel, and P2.1 is done with this module when the ratchet is empty.
*Rejected* (in the P2.0 design and its review): pinning today's violations as
green tests (a contributor who honours invariant 2 early turned the suite red
for it); leaving them out with a comment (invisible to the one suite run every
iteration, which is how F-4 survived Phase 1); asserting P2.1's exact words now
(red on today's code, against R-1, and R-7 forbids an expected failure); a
ratchet keyed by channel and property (the first version: it excused a WORSE
problem on a listed channel — both crashes counted as missing tools, the word
errored gone from the gates line — as the listed one still showing).

**What P2.1 adds here** (the hand-off): ``test_counts_say_skipped_with_errored_apart``
(``N skipped (k errored)`` in every claim count and heading, which no channel
prints today), and the deletion of every ``KNOWN_QUIETER`` entry. Expected words
are literals typed here from GLOSSARY §3 (``ERRORED_LEAD``), never read from
``report.HUMAN``: a test that reads the table under test agrees with it by
construction.

Run:  PYTHONPATH=src python3 -m unittest discover -s tests -p test_louder.py -v
"""
from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
import types
import unittest
import xml.etree.ElementTree as ET
from typing import Any, Callable, NamedTuple
from unittest import mock

import _env
import _projects
from atompipe import claims as claims_mod
from atompipe import cli as cli_mod
from atompipe import gates as gates_mod
from atompipe import report as report_mod
from atompipe.models import (
    Acceptance, Claim, ClaimKind, ClaimStatus, Comparator, Ledger, NegativeControl,
    ProjectMeta, Verdict,
)

# --------------------------------------------------------------------------- #
# the planted world, as ground truth
# --------------------------------------------------------------------------- #
#: The tool the skipping evaluator requires. A name no machine has.
TOOL = "atompipe-no-such-tool-p2"
SKIP_GATE = "probe.a_skip"
BOTH_GATE = "probe.y_both"
CRASH_GATE = "probe.z_crash"
ERRORED_GATES = (BOTH_GATE, CRASH_GATE)
SKIPPED_GATES = (SKIP_GATE,)

#: The exception line each errored evaluator carries, and the skip reason the
#: skipped-and-errored one carries beside it (the words it must never read in).
EXCEPTION = {CRASH_GATE: "ZeroDivisionError: planted crash",
             BOTH_GATE: "RuntimeError: planted both"}
BOTH_SKIP_REASON = "requires probe-lib (not installed)"

#: errored claim -> the errored evaluator its reason must cite.
ERRORED_CLAIMS = {"P2": CRASH_GATE, "P3": CRASH_GATE, "P4": CRASH_GATE,
                  "P5": CRASH_GATE, "P6": CRASH_GATE, "P7": BOTH_GATE, "P8": CRASH_GATE}
SKIPPED_CLAIMS = {"P1": SKIP_GATE}
NOT_REQUIRED = frozenset({"P4", "P6", "P8"})

#: P8 is PHYSICAL, covered by the crash probe: from P2.1 a physical claim
#: composes its automated evaluators (S-49), so its crashing modelled half reads
#: Skipped, errored — louder than its missing article — and `claim physical P8
#: pass`, the command's own printed row, must say so too (P2.0's hand-off: the
#: change that lets a physical claim's modelled half set its status moves
#: `claim physical` into `RENDERED`).
PHYSICAL_PROBE = "P8"
#: Rows that read Failing on a measured fail: the one kind an errored row may
#: sit below. The bracket's C1 and its in-process stand-in's evaluator.
FAILING = frozenset({"C1", "bracket.deflection", "probe.fails"})

#: Words that say "a tool is missing". An errored row carrying any of them reads
#: as the dull case GLOSSARY §3 keeps Skipped for. Lower-cased; "requires " is
#: availability's own lead (`gates.availability`).
MISSING_TOOL_WORDS = ("missing tool", "not installed", "not on path", "not importable",
                      "requires ")

#: The planted claims: id -> (statement, tags, required).
PROBE_CLAIMS: dict[str, tuple[str, list[str], bool]] = {
    "P1": ("skip probe holds", ["skip-probe"], True),
    "P2": ("crash probe holds", ["crash-probe"], True),
    "P3": ("both probes hold", ["skip-probe", "crash-probe"], True),
    "P4": ("not-required crash probe holds", ["crash-probe"], False),
    "P5": ("walls hold and the crash probe holds", ["min-wall", "crash-probe"], True),
    "P6": ("not-required walls and crash probe hold", ["min-wall", "crash-probe"], False),
    "P7": ("the skipped-and-errored probe holds", ["both-probe"], True),
    "P8": ("a physical claim whose modelled half crashes", ["crash-probe"], False),
}

#: The evaluators the CLI fixture plants. `probe.z_crash` is honest on every
#: input but the live design's: it passes the known-good 8.0 (`t >= 7.6`),
#: fails its own known-bad, and raises only at 7.0. So it is admitted today and
#: stays a gate whose error is a CRASH, not a refusal of qualification: GLOSSARY
#: §2 and PLAN-v0.14 §1.4 keep "it crashed" (Skipped, errored) apart from "it
#: has not shown it can fail" (Gap, unqualified), and a fixture that blurred them
#: would test the wrong word.
#: Why its own fixture and 7.6 (review of the P2.0 design): reusing the
#: bracket's `quarter_thickness` tied this fixture to a control P2 recalibrates
#: (old 2.3, S-17: about 1.15x past the deflection limit, thickness ~7.5),
#: which would pass `t >= 3.0` and leave the gate unqualified, its claims Gap
#: instead of errored; and a threshold at 3.0 against a known-good of 8.0 left
#: a margin no small mutation crosses. *Rejected:* a gate that always raises —
#: never qualified once the known-good half runs (P2.3), so P2.3 would turn
#: this fixture's errored claims into Gaps silently. Which mutation operators
#: P2.3 runs is not designed yet: `_check_the_fixture`'s precondition is the
#: guard, and names the gate if the crash stops being one.
PROBE_GATES = f'''\
"""Planted by tests/test_louder.py: a missing tool, a crash, and both at once."""
from atompipe.gates import gate
from atompipe.models import NegativeControl, Verdict

_THIN = NegativeControl(fixture="selftest/probe_bad.py:thin",
                        note="a 1 mm section: every probe must fail it")


@gate(id="{SKIP_GATE}", claims=["skip-probe"], requires_tools=["{TOOL}"],
      negative_control=_THIN)
def needs_a_tool(ctx):
    t = ctx.params["config"]["thickness"]
    return Verdict(gate="{SKIP_GATE}", passed=t >= 7.6, detail=f"thickness {{t}} mm")


@gate(id="{BOTH_GATE}", claims=["both-probe"], negative_control=_THIN)
def says_both(ctx):
    t = ctx.params["config"]["thickness"]
    if t == 7.0:
        return Verdict(gate="{BOTH_GATE}", passed=False, skipped=True,
                       skip_reason="{BOTH_SKIP_REASON}", error="{EXCEPTION[BOTH_GATE]}")
    return Verdict(gate="{BOTH_GATE}", passed=t >= 7.6, detail=f"thickness {{t}} mm")


@gate(id="{CRASH_GATE}", claims=["crash-probe"], negative_control=_THIN)
def crashes(ctx):
    t = ctx.params["config"]["thickness"]
    if t == 7.0:
        raise ZeroDivisionError("planted crash")
    return Verdict(gate="{CRASH_GATE}", passed=t >= 7.6, detail=f"thickness {{t}} mm")
'''

#: The probes' known-bad control: a literal 1 mm section, built from nothing the
#: host or the bracket's own selftest holds, so no recalibration reaches it.
PROBE_FIXTURE = '''\
"""Planted by tests/test_louder.py: the probes' known-bad control."""
import dataclasses

from atompipe.models import Ledger


def thin(ctx):
    return dataclasses.replace(ctx, params={"config": {"thickness": 1.0}, "thickness": 1.0},
                               ledger=Ledger(), extra={})
'''

#: The model edit made after the check runs, so C4 reads Stale in every later
#: command: `bracket.bed_fit` reads `bed_xy`, and nothing else the fixture
#: relies on does. A Stale row is one an errored row must stay above.
_BED_XY = re.compile(r"^(    bed_xy: float = )220\.0$", re.MULTILINE)


# --------------------------------------------------------------------------- #
# problems
# --------------------------------------------------------------------------- #
class Problem(NamedTuple):
    """One shortfall. `(channel, prop, subject, text)` is its identity — what
    `KNOWN_QUIETER` names, so `text` never carries what varies by machine or
    by run (a path, a traceback, a whole printed line); that goes in `seen`,
    shown to a reader and never compared."""
    channel: str
    prop: str
    subject: str
    text: str
    seen: str = ""

    @property
    def key(self) -> tuple[str, str, str, str]:
        return (self.channel, self.prop, self.subject, self.text)

    def __str__(self) -> str:
        head = f"{self.channel}: {self.prop}: {self.subject}: {self.text}"
        return f"{head} [{self.seen}]" if self.seen else head


class Row(NamedTuple):
    """One parsed row: a claim id or a gate id, its tag as printed, its body."""
    subject: str
    tag: str
    body: str = ""


def classify(subject: str) -> str:
    if subject in ERRORED_CLAIMS or subject in ERRORED_GATES:
        return "errored"
    if subject in SKIPPED_CLAIMS or subject in SKIPPED_GATES:
        return "skipped"
    if subject in FAILING:
        return "failing"
    return "other"


def tone_of(tag: str) -> str:
    """On a channel with no colour, Failing's tone is a LOUD tag — letters, all
    upper case (`FAIL`, `ERR `, `STALE`); `skip`, `gap`, `phys`, `ok` are quiet,
    as today's tags already split (the P2.0 design; its default for P2.1 is an
    errored claim tagged `[SKIP ]`, the status's one word in Failing's tone).
    *Rejected:* punctuation (`[skip!]`), which is not a tone anyone learns;
    `[ERR  ]` on a claim row, an outcome's tag on a status row."""
    core = tag.strip()
    return "loud" if any(c.isalpha() for c in core) and core == core.upper() else "quiet"


def tone_problems(channel: str, rows: list[Row]) -> list[Problem]:
    out = []
    for row in rows:
        kind = classify(row.subject)
        if kind == "errored" and tone_of(row.tag) != "loud":
            out.append(Problem(channel, "tone", row.subject, f"[{row.tag}] is quiet"))
        elif kind == "skipped" and tone_of(row.tag) != "quiet":
            out.append(Problem(channel, "tone", row.subject,
                               f"[{row.tag}] is as loud as a crash"))
    return out


def never_pass_problems(channel: str, rows: list[Row]) -> list[Problem]:
    return [Problem(channel, "never-pass", row.subject, f"tagged [{row.tag}]")
            for row in rows if classify(row.subject) == "errored"
            and row.tag.strip().lower() in ("ok", "ok-hw")]


def words_problems(channel: str, rows: list[Row]) -> list[Problem]:
    """An errored row's body says nothing a missing tool says (F-8, F-10), and
    never leads with the skip's GLOSSARY lead `skipped:` — a reason that names
    both says `errored:` first (GLOSSARY §3: "errored first"). Green today,
    where no reason says either; it binds the day P2.1 writes the leads."""
    out = []
    for row in rows:
        if classify(row.subject) != "errored":
            continue
        hit = [w for w in MISSING_TOOL_WORDS if w in row.body.lower()]
        if hit:
            out.append(Problem(channel, "skip-words", row.subject,
                               f"reads like a missing tool ({hit[0].strip()!r})", row.body[:80]))
        if row.body.lower().lstrip().startswith("skipped"):
            out.append(Problem(channel, "skip-words", row.subject,
                               "leads with the skip's 'skipped:'", row.body[:80]))
    return out


#: GLOSSARY §3's lead for an errored claim's reason, typed here (never read
#: from the table under test).
ERRORED_LEAD = "errored:"

#: What a traceback leaves in a reason: its head line, or a frame line.
#: `run_gate` keeps the last ~400 characters of the stack cut at a line boundary
#: (`gates._trace_tail`), so a long checkout path pushes the head out and only
#: frames remain. What slipped through a head-only marker: run from a tree under
#: a long temp path, every "carries the traceback" problem vanished, and the
#: ratchet went red for the path, not the code.
TRACEBACK = re.compile(r'Traceback \(most recent call last\)|\bFile "')


def lead_problems(channel: str, rows: list[Row], *, lead: bool = True) -> list[Problem]:
    """F-1. An errored claim's reason leads `errored:` (`lead`) and never
    carries the traceback: the exception's first line says what crashed, the
    tail of the stack says where the spine called the gate. `lead=False` for an
    evaluator's own row (`[ERR ] gate : …`), whose tag already leads. Today
    every reason leads with the gate id and a crash's is its traceback, so each
    channel's shortfall is in `KNOWN_QUIETER` until P2.1 writes the lead."""
    out = []
    for row in rows:
        if classify(row.subject) != "errored":
            continue
        if lead and not row.body.startswith(ERRORED_LEAD):
            out.append(Problem(channel, "lead", row.subject,
                               f"does not lead with {ERRORED_LEAD!r}", row.body[:60]))
        if TRACEBACK.search(row.body):
            out.append(Problem(channel, "lead", row.subject, "carries the traceback",
                               row.body[:60]))
    return out


def cites_problems(channel: str, rows: list[Row], *, full: bool) -> list[Problem]:
    """An errored claim's reason names its errored evaluator; where the channel
    does not cut the line (`full`), it carries the exception too."""
    out = []
    for row in rows:
        if classify(row.subject) != "errored":
            continue
        gate = ERRORED_CLAIMS.get(row.subject, row.subject)
        if row.subject in ERRORED_CLAIMS and gate not in row.body:
            out.append(Problem(channel, "cites", row.subject, f"does not name {gate}"))
        elif full and EXCEPTION[gate] not in row.body:
            out.append(Problem(channel, "cites", row.subject,
                               f"does not carry {EXCEPTION[gate]!r}"))
    return out


def order_problems(channel: str, rows: list[Row]) -> list[Problem]:
    """Every errored row sits above every row that is neither Failing nor errored
    — a skip above all, and a Stale, Gap or Open row too: an errored row may
    never read quieter than it reads today, directly under Failing."""
    out = []
    for index, row in enumerate(rows):
        if classify(row.subject) != "errored":
            continue
        above = [r.subject for r in rows[:index] if classify(r.subject) in ("skipped", "other")]
        if above:
            out.append(Problem(channel, "order", row.subject,
                               f"sorts below {', '.join(above[:3])}"))
    return out


def floor_problems(channel: str, rows: list[Row], *kinds: str) -> list[Problem]:
    """A parse that found nothing to judge is a problem, never a pass."""
    found = {classify(row.subject) for row in rows}
    return [Problem(channel, "floor", "*", f"parsed no {kind} row")
            for kind in kinds if kind not in found]


def row_problems(channel: str, rows: list[Row], *, full: bool = False, order: bool = True,
                 cites: bool = True, floor: tuple[str, ...] = ("errored", "skipped"),
                 lead: bool = False) -> list[Problem]:
    """Every row property; `lead` for rows whose body is a claim's reason."""
    out = floor_problems(channel, rows, *floor)
    out += never_pass_problems(channel, rows)
    out += tone_problems(channel, rows)
    out += words_problems(channel, rows)
    if cites:
        out += cites_problems(channel, rows, full=full)
    if order:
        out += order_problems(channel, rows)
    if lead:
        out += lead_problems(channel, rows)
    return out


# -- counts ----------------------------------------------------------------- #
def gate_count_problems(channel: str, line: str) -> list[Problem]:
    """`… 1 skipped, 2 errored …`: each errored evaluator is counted once — as
    `N errored`, or inside `N skipped (k errored)` — and never as a plain skip."""
    match = re.search(r"(\d+) skipped(?: \((\d+) errored\))?", line)
    plain = (int(match.group(1)) - int(match.group(2) or 0)) if match else 0
    inside = int(match.group(2) or 0) if match else 0
    alone = sum(int(m.group(2)) for m in re.finditer(r"(\(?)(\d+) errored", line)
                if not m.group(1))
    out = []
    if plain != len(SKIPPED_GATES):
        out.append(Problem(channel, "counts", "*",
                           f"{plain} counted as skipped, {len(SKIPPED_GATES)} skipped", line))
    if alone + inside != len(ERRORED_GATES) or (alone and inside):
        out.append(Problem(channel, "counts", "*",
                           f"errored counted {alone} + {inside} times for {len(ERRORED_GATES)}",
                           line))
    return out


_COUNT_BIT = re.compile(
    r"^(?:(?P<word>[A-Za-z][\w -]*?) (?P<n>\d+)|(?P<n2>\d+) (?P<word2>[A-Za-z][\w -]*?))"
    r"(?: \((?P<sub>\d+) errored\))?$")


def claim_count_problems(channel: str, line: str, n_claims: int) -> list[Problem]:
    """`claims 14 — ok 2 | FAIL 7 | … | skip 1`: the counts add up to the claims
    (none counted twice), and the skip count holds the skipped claims plus only
    the errored ones it says it holds, `skip 7 (6 errored)`."""
    bits = [b.strip() for b in re.split(r"[|·—]", line) if b.strip()]
    bits = [b for b in bits if not re.fullmatch(r"claims \d+|\d+ claims", b)]
    total, plain_skip, parsed = 0, None, 0
    for bit in bits:
        m = _COUNT_BIT.match(bit)
        if not m:
            continue
        parsed += 1
        n = int(m.group("n") or m.group("n2"))
        word = (m.group("word") or m.group("word2") or "").strip().lower()
        total += n
        if word in ("skip", "skipped"):
            plain_skip = n - int(m.group("sub") or 0)
    out = []
    if not parsed:
        out.append(Problem(channel, "floor", "*", "no count parsed", line))
    elif total != n_claims:
        out.append(Problem(channel, "counts", "*",
                           f"the counts add to {total} for {n_claims} claims", line))
    if plain_skip != len(SKIPPED_CLAIMS):
        out.append(Problem(channel, "counts", "*",
                           f"skip counts {plain_skip} for {len(SKIPPED_CLAIMS)} skipped", line))
    return out


#: One `N <word> [(k errored)] (ids)` group of the readiness sentence. Its
#: phrase never crosses a dash or a full stop: from P2.1's review the claims not
#: required are grouped by word too (`3 claims not required are unresolved — 3
#: skipped (3 errored) (P4, P6, P8)`), and a phrase that ran from the sentence
#: before ("2 of 15 claims are checked … — 3 skipped") misread both counts.
_ID_GROUP = re.compile(r"(\d+) ([^;:()—.]*?)(?: \((\d+) errored\))?"
                       r" \(([A-Z]\d+(?:, (?:[A-Z]\d+|\+\d+ more))*)\)")

#: A sentence group whose phrase holds one of these counts claims with no
#: usable verdict: today's "blocked on missing tooling", and GLOSSARY §3's word
#: for the status, "skipped".
_SKIP_PHRASES = ("missing tool", "tooling", "not installed", "skipped")


def sentence_problems(channel: str, sentence: str) -> list[Problem]:
    """The readiness sentence never counts an errored claim inside a skip: a
    group that names a missing tool or says `skipped` lists no errored claim
    unless it splits them out, `N skipped (k errored) (…)`, and then N − k is
    the critical skipped claims alone. What slipped through the first version:
    it knew only today's phrase, so GLOSSARY's `6 skipped (P1, P2, …)` — every
    crash counted as a missing tool — passed, and a group carrying the split
    was not parsed at all."""
    groups = list(_ID_GROUP.finditer(sentence))
    out = [] if groups else [Problem(channel, "floor", "*", "no id group", sentence)]
    critical_skipped = [cid for cid in SKIPPED_CLAIMS if cid not in NOT_REQUIRED]
    other_skipped = [cid for cid in SKIPPED_CLAIMS if cid in NOT_REQUIRED]
    for m in groups:
        phrase = m.group(2).lower()
        if not any(w in phrase for w in _SKIP_PHRASES):
            continue
        ids = [x.strip() for x in m.group(4).split(",") if not x.strip().startswith("+")]
        if m.group(3) is None:
            for cid in ids:
                if cid in ERRORED_CLAIMS:
                    out.append(Problem(channel, "skip-words", cid,
                                       f"listed under {m.group(2)!r}", sentence))
            continue
        # The group the claims not required are listed in, split the same way
        # (review of P2.1: they were one "unresolved" count, a crash among them
        # said nowhere) — its N − k is theirs; the required group's, theirs.
        mine = other_skipped if ids and set(ids) <= NOT_REQUIRED else critical_skipped
        if int(m.group(1)) - int(m.group(3)) != len(mine):
            out.append(Problem(channel, "counts", "*",
                               f"{m.group(1)} {m.group(2)} ({m.group(3)} errored) leaves "
                               f"{int(m.group(1)) - int(m.group(3))} skipped for "
                               f"{len(mine)}", sentence))
    # Invariant 2 in the most-read line: a crash on a claim not required is
    # said too, counted apart — never folded into a bare "N claims not required
    # are unresolved (…)", the count line the only place it showed.
    other_errored = [cid for cid in ERRORED_CLAIMS if cid in NOT_REQUIRED]
    said = [int(m.group(3)) for m in groups if m.group(3) is not None
            and {x.strip() for x in m.group(4).split(",")
                 if not x.strip().startswith("+")} <= NOT_REQUIRED]
    if groups and other_errored and said != [len(other_errored)]:
        out.append(Problem(channel, "counts", "*",
                           f"the {len(other_errored)} errored claims not required are "
                           f"counted errored {said or 'nowhere'}", sentence))
    return out


# -- JUnit ------------------------------------------------------------------ #
def junit_problems(channel: str, text: str) -> list[Problem]:
    """Every errored evaluator and every errored claim, required or not, is an
    `<error>`; the skip is not; the tallies are the children."""
    root = ET.fromstring(text)
    out: list[Problem] = []

    def child(suite: str, name: str) -> tuple[str | None, Any]:
        case = root.find(f"testsuite[@name='{suite}']/testcase[@name='{name}']")
        if case is None:
            return "absent", None
        kids = list(case)
        kids = [k for k in kids if k.tag != "properties"]
        return (kids[0].tag if kids else None), (kids[0] if kids else None)

    for gate in ERRORED_GATES:
        tag, el = child("gates", gate)
        if tag != "error" or el.get("type") != "error":
            out.append(Problem(channel, "junit", gate, f"is <{tag}>, not <error type=error>"))
        elif EXCEPTION[gate] not in (el.get("message") or ""):
            out.append(Problem(channel, "cites", gate, "message lacks the exception"))
    for gate in SKIPPED_GATES:
        tag, _el = child("gates", gate)
        if tag != "skipped":
            out.append(Problem(channel, "junit", gate, f"the skip is <{tag}>"))
    for cid, gate in ERRORED_CLAIMS.items():
        suite = "claims.not-critical" if cid in NOT_REQUIRED else "claims.critical"
        tag, el = child(suite, cid)
        if tag != "error":
            out.append(Problem(channel, "junit", cid, f"is <{tag}> in {suite}"))
            continue
        message = el.get("message") or ""
        out += cites_problems(channel, [Row(cid, "ERROR", message)], full=True)
        out += words_problems(channel, [Row(cid, "ERROR", message)])
        out += lead_problems(channel, [Row(cid, "ERROR", message)])
    for cid in SKIPPED_CLAIMS:
        tag, _el = child("claims.critical", cid)
        if tag in (None, "error", "absent"):
            out.append(Problem(channel, "junit", cid, f"the skipped claim is <{tag}>"))
    for suite in root.iter("testsuite"):
        cases = list(suite.iter("testcase"))
        for attr, kid in (("errors", "error"), ("skipped", "skipped")):
            counted = sum(1 for c in cases if c.find(kid) is not None)
            if int(suite.get(attr, -1)) != counted:
                out.append(Problem(channel, "counts", suite.get("name", "?"),
                                   f"{attr}={suite.get(attr)} for {counted} <{kid}>"))
    return out


# -- text parsers ----------------------------------------------------------- #
_CLAIM_ROW = re.compile(r"^\[(?P<tag>.{5})\] (?P<id>[A-Z]\d+)(?:\s+(?P<rest>.*))?$")
_GATE_ROW = re.compile(r"^\s*\[(?P<tag>.{4})\] (?P<gate>[\w.\-]+)(?: : (?P<body>.*))?$")
_REPORT_CLAIM = re.compile(r"^### \[(?P<tag>.{5})\] (?P<id>[A-Z]\d+) — ")
_REPORT_BULLET = re.compile(r"^- `\[(?P<tag>.{4})\] (?P<gate>[\w.\-]+)(?: : (?P<body>.*))?`")


def claim_rows(lines: list[str]) -> list[Row]:
    """`[FAIL ] P2 statement — reason` rows, in printed order."""
    rows = []
    for line in lines:
        m = _CLAIM_ROW.match(line)
        if m:
            _statement, _, reason = (m.group("rest") or "").partition(" — ")
            rows.append(Row(m.group("id"), m.group("tag"), reason))
    return rows


def gate_rows(lines: list[str]) -> list[Row]:
    """`[ERR ] probe.z_crash : body` rows, with `why`'s wrapped continuation lines
    joined back on and `check`'s `cached` column dropped."""
    rows: list[Row] = []
    for line in lines:
        m = _GATE_ROW.match(line)
        if m:
            body = re.sub(r"\s+cached$", "", m.group("body") or "")
            rows.append(Row(m.group("gate"), m.group("tag"), body))
        elif rows and line.startswith("         ") and line.strip():
            last = rows[-1]
            rows[-1] = last._replace(body=f"{last.body} {line.strip()}")
    return rows


def section(lines: list[str], head: str, stop: Callable[[str], bool]) -> list[str]:
    """The lines after the first line starting with `head`, up to `stop`."""
    out: list[str] = []
    inside = False
    for line in lines:
        if not inside:
            inside = line.startswith(head)
            continue
        if stop(line):
            break
        out.append(line)
    return out


def terminal_problems(channel: str, text: str, n_claims: int) -> list[Problem]:
    """`status` / `report.render_terminal`: the sentence, the counts, the claim
    rows (a truncating channel: the gate, not the exception), the gates line."""
    lines = text.splitlines()
    out = sentence_problems(f"{channel}.sentence", lines[1] if len(lines) > 1 else "")
    counts = [line for line in lines if re.match(r"^\d+ claims? · ", line)]
    out += (claim_count_problems(f"{channel}.counts", counts[0], n_claims) if counts
            else [Problem(f"{channel}.counts", "floor", "*", "no counts line")])
    out += row_problems(channel, claim_rows(lines), lead=True)
    gates_line = [line for line in lines if line.startswith("gates: ")]
    out += (gate_count_problems(f"{channel}.gates", gates_line[0]) if gates_line
            else [Problem(f"{channel}.gates", "floor", "*", "no gates line")])
    return out


def markdown_problems(channel: str, md: str) -> list[Problem]:
    """The report: nothing errored under PROVEN; the Failing section's claim
    heads loud and ranked; each errored claim's block carries a loud bullet for
    its errored evaluator with the exception (the report never cuts) and not the
    traceback (F-1), and lists it above every bullet that is neither a fail nor
    errored (channel `<channel>.<claim>`). What slipped through the first
    version: it held the bullets' presence, tone and words, never their order,
    so the report's skip-above-crash bullets (F-3's gate-id order, as in `why`)
    could outlive the fix to `why`."""
    lines = md.splitlines()
    out = sentence_problems(f"{channel}.sentence",
                            next((ln for ln in lines if ln.startswith("**")), ""))
    proven = section(lines, report_mod.SECTION_PROVEN, lambda ln: ln.startswith("## "))
    proven_ids = {m.group(1) for ln in proven for m in [re.match(r"^\| \*\*([A-Z]\d+)\*\*", ln)] if m}
    if not proven_ids:
        out.append(Problem(channel, "floor", "*", "the PROVEN section has no row"))
    for cid in sorted(proven_ids & set(ERRORED_CLAIMS)):
        out.append(Problem(channel, "never-pass", cid, "under PROVEN"))
    heads: list[Row] = []
    bullets: dict[str, list[Row]] = {}
    current = None
    for line in lines:
        m = _REPORT_CLAIM.match(line)
        if m:
            current = m.group("id")
            heads.append(Row(current, m.group("tag")))
            bullets[current] = []
            continue
        if line.startswith("## ") or line.startswith("### "):
            current = None
        b = _REPORT_BULLET.match(line)
        if b and current:
            bullets[current].append(Row(b.group("gate"), b.group("tag"), b.group("body") or ""))
    out += row_problems(channel, heads, cites=False)
    for cid, gate in ERRORED_CLAIMS.items():
        mine = [r for r in bullets.get(cid, []) if r.subject == gate]
        if not mine:
            out.append(Problem(channel, "cites", cid, f"no bullet for {gate}"))
            continue
        bullet = Row(cid, mine[0].tag, mine[0].body)
        out += tone_problems(channel, [bullet])
        out += words_problems(channel, [bullet])
        out += lead_problems(channel, [bullet], lead=False)
        if EXCEPTION[gate] not in bullet.body:
            out.append(Problem(channel, "cites", cid, f"bullet lacks {EXCEPTION[gate]!r}"))
        out += order_problems(f"{channel}.{cid}", bullets[cid])
    return out


def check_problems(text: str) -> list[Problem]:
    """`check`'s stdout: the gate stream and skip digest, the summary, BLOCKING."""
    lines = text.splitlines()
    summary_at = next((i for i, ln in enumerate(lines) if re.match(r"^\d+ gates: ", ln)), None)
    blocking_at = next((i for i, ln in enumerate(lines) if ln.startswith("BLOCKING — ")),
                       len(lines))
    out: list[Problem] = []
    if summary_at is None:
        return [Problem("check.summary", "floor", "*", "no summary line")]
    stream = gate_rows([ln for ln in lines[:blocking_at] if not ln.startswith("controls:")])
    # Every errored evaluator streams a row of its own, and it is loud.
    named = {r.subject for r in stream}
    for gate in ERRORED_GATES:
        if gate not in named:
            out.append(Problem("check.stream", "names", gate, "no row"))
    out += row_problems("check.stream", stream, cites=False)
    out += gate_count_problems("check.summary", lines[summary_at])
    blocking = claim_rows(lines[blocking_at + 1:])
    out += row_problems("check.blocking", blocking, full=True, lead=True)
    return out


def why_problems(channel: str, text: str, *, floor: tuple[str, ...]) -> list[Problem]:
    """`why`'s GATES rows: the evaluator's row is the claim's reason there, so
    the crash's carries no traceback (F-1)."""
    lines = text.splitlines()
    rows = gate_rows(section(lines, "GATES (", lambda ln: not ln.strip()))
    return (row_problems(channel, rows, cites=False, floor=floor)
            + lead_problems(channel, rows, lead=False))


def doctor_problems(text: str) -> list[Problem]:
    """Each errored evaluator is named in a loud row above every row that names
    the missing tool (F-4). The floor: the tool's rows are there to be outranked."""
    rows = []
    for line in text.splitlines():
        m = re.match(r"^\[(?P<tag>.{4})\] (?P<name>\S+)\s+(?P<body>.*)$", line)
        if m:
            rows.append((m.group("tag"), m.group("name"), m.group("body")))
    tool_rows = [i for i, (_t, _n, body) in enumerate(rows) if TOOL in body or SKIP_GATE in body]
    out = [] if tool_rows else [Problem("doctor", "floor", "*", "no row names the missing tool")]
    for gate in ERRORED_GATES:
        mine = [i for i, (tag, _n, body) in enumerate(rows)
                if gate in body and tone_of(tag) == "loud"]
        if not mine:
            out.append(Problem("doctor", "names", gate, "no loud row names the crash"))
        elif tool_rows and min(mine) > min(tool_rows):
            out.append(Problem("doctor", "order", gate, "named below the missing tool"))
    return out


# -- JSON ------------------------------------------------------------------- #
def _tell(status: Any, errored: Any) -> tuple[str, bool]:
    """What a JSON reader can tell a claim apart by: its status, and its
    `errored` mark (P2.1: an errored claim's status is the skip's, `blocked`,
    and every row and map carries the mark beside it, R-14)."""
    return str(status), bool(errored)


def json_row_problems(channel: str, rows: list[dict], *, id_key: str) -> list[Problem]:
    """Rows of `check --json` `blocking` and `claim list --json`."""
    by_id = {row.get(id_key): row for row in rows}
    out = []
    for cid in list(ERRORED_CLAIMS) + list(SKIPPED_CLAIMS):
        if cid not in by_id and not (channel == "check.json" and cid in NOT_REQUIRED):
            out.append(Problem(channel, "floor", cid, "absent"))
    for cid in SKIPPED_CLAIMS:
        quiet = by_id.get(cid)
        for errored_id in ERRORED_CLAIMS:
            row = by_id.get(errored_id)
            if row is None or quiet is None:
                continue
            if str(row.get("status")) in ("pass", "verified"):
                out.append(Problem(channel, "never-pass", errored_id, f"status {row['status']}"))
            if _tell(row.get("status"), row.get("errored")) == _tell(
                    quiet.get("status"), quiet.get("errored")):
                out.append(Problem(channel, "tell-apart", errored_id,
                                   f"reads exactly as {cid}: {row.get('status')}"))
    return out


def json_map_problems(channel: str, doc: dict) -> list[Problem]:
    """`status --json` / `report --json`: `claims: {id: status}` and the summary."""
    claims = doc.get("claims") or {}
    marked = set(doc.get("errored") or ())
    out = []
    for cid in SKIPPED_CLAIMS:
        for errored_id in ERRORED_CLAIMS:
            if errored_id not in claims or cid not in claims:
                out.append(Problem(channel, "floor", errored_id, "absent"))
                continue
            if claims[errored_id] in ("pass", "verified"):
                out.append(Problem(channel, "never-pass", errored_id, claims[errored_id]))
            if _tell(claims[errored_id], errored_id in marked) == _tell(claims[cid], cid in marked):
                out.append(Problem(channel, "tell-apart", errored_id,
                                   f"reads exactly as {cid}: {claims[cid]}"))
    summary = doc.get("summary")
    by_status = summary.get("by_status") if isinstance(summary, dict) else None
    counts = summary.get("counts") if isinstance(summary, dict) else None
    if not isinstance(by_status, dict) or not by_status:
        # What slipped through the first version: `if by_status and …` dropped
        # the count check, silently, for a summary that moved or was renamed.
        out.append(Problem(channel, "floor", "*", "no summary.by_status to count"))
    elif not isinstance(counts, dict) or not counts or "errored" not in summary:
        out.append(Problem(channel, "floor", "*", "no summary.counts or summary.errored"))
    else:
        # P2.1 (R-6, stronger): the crash is counted apart under `summary.errored`
        # — a key beside `by_status`, never inside a map documented as every
        # ClaimStatus — and both maps add up to the claims.
        n = summary.get("n_claims")
        if sum(by_status.values()) != n or sum(counts.values()) != n:
            out.append(Problem(channel, "counts", "*", "a status map does not add up to "
                                                       "the claims"))
        if by_status.get("blocked", 0) - summary["errored"] != len(SKIPPED_CLAIMS):
            out.append(Problem(channel, "counts", "*",
                               f"by_status counts {by_status.get('blocked')} blocked, "
                               f"{summary['errored']} errored, for {len(SKIPPED_CLAIMS)} "
                               f"skipped"))
        if summary["errored"] != len(ERRORED_CLAIMS) or set(doc.get("errored") or ()) != set(
                ERRORED_CLAIMS):
            out.append(Problem(channel, "counts", "*", "the errored claims are not "
                                                       "counted apart"))
    return out


# -- the page --------------------------------------------------------------- #
def js_table(source: str, name: str) -> dict[str, dict[str, str]]:
    """`const NAME = { key: { label: "...", tone: "bad", ... }, ... };` from a JS module."""
    body = re.search(r"const " + name + r" = \{(.*?)\n\};", source, re.S)
    out: dict[str, dict[str, str]] = {}
    for m in re.finditer(r"^\s*(\w+):\s*\{(.*)\},?\s*$", body.group(1) if body else "", re.M):
        out[m.group(1)] = dict(re.findall(r'(\w+):\s*"([^"]*)"', m.group(2)))
    return out


def js_order(source: str) -> dict[str, int]:
    body = re.search(r"const VERDICT_ORDER = \{([^}]*)\}", source)
    return {k: int(v) for k, v in re.findall(r"(\w+):\s*(\d+)", body.group(1) if body else "")}


#: The statements in `panels.js` and `format.js` the claim-row tone mirror
#: below reproduces. If one changes, the mirror is no longer the page's, and the
#: test says so instead of judging a page that no longer exists (R-6: P2.1
#: changed them — the errored mark, the disagreement — and this mirror in the
#: same edit, as P2.0 required).
PANELS_CLAIM_TONE = ("const status = claimStatus(claim.status, { errored: !!claim.errored });",
                     'class: `claim tone-${claim.disagree ? "bad" : status.tone}`')
FORMAT_CLAIM_TONE = ('tone: errored ? "bad" : look.tone,',)


def claim_row_tone(row: dict, claim_table: dict[str, dict[str, str]]) -> str:
    """The tone `panels.js` paints a `state.json` claim row in: `bad` for a
    contradiction (`disagree`) or a crash (`errored`, Failing's tone), else
    `claimStatus`'s entry for the row's status, `muted` for one it does not
    know. A function of the ROW, not of a status, so the check is on what the
    page reads."""
    if row.get("disagree"):
        return "bad"
    tone = (claim_table.get(str(row.get("status"))) or {}).get("tone", "muted")
    return "bad" if row.get("errored") and str(row.get("status")) in claim_table else tone


def state_problems(state: dict, format_js: str, panels_js: str) -> list[Problem]:
    out: list[Problem] = []
    verdict_table = js_table(format_js, "VERDICT_STATUS")
    claim_table = js_table(format_js, "CLAIM_STATUS")
    if not verdict_table or not claim_table:
        return [Problem("site.page", "floor", "*", "format.js tables not found")]
    for needle in PANELS_CLAIM_TONE:
        if needle not in panels_js:
            out.append(Problem("site.page", "mirror", "*",
                               f"panels.js no longer says {needle!r}: update claim_row_tone"))
    for needle in FORMAT_CLAIM_TONE:
        if needle not in format_js:
            out.append(Problem("site.page", "mirror", "*",
                               f"format.js no longer says {needle!r}: update claim_row_tone"))
    fail_tone = verdict_table.get("fail", {}).get("tone")
    rows = {row.get("gate"): row for row in state.get("verdicts") or []}
    for gate in ERRORED_GATES + SKIPPED_GATES:
        row = rows.get(gate)
        if row is None:
            out.append(Problem("site.verdicts", "floor", gate, "no verdict row"))
            continue
        want = "errored" if gate in ERRORED_GATES else "skipped"
        if row.get("status") != want or row.get("ok"):
            out.append(Problem("site.verdicts", "status", gate,
                               f"status {row.get('status')}, ok {row.get('ok')}"))
        tone = verdict_table.get(str(row.get("status")), {}).get("tone")
        if (tone == fail_tone) != (gate in ERRORED_GATES):
            out.append(Problem("site.verdicts", "tone", gate, f"tone {tone} (fail: {fail_tone})"))
    order = js_order(panels_js)
    if not order.get("errored", 9) < order.get("skipped", -1):
        out.append(Problem("site.verdicts", "order", "*", f"VERDICT_ORDER {order}"))

    claims = state.get("claims") or []
    by_id = {row.get("id"): row for row in claims}
    failing = by_id.get("C1")
    if failing is None:
        return out + [Problem("site.claims", "floor", "C1", "no Failing claim row")]
    failing_tone = claim_row_tone(failing, claim_table)
    for cid in list(ERRORED_CLAIMS) + list(SKIPPED_CLAIMS):
        row = by_id.get(cid)
        if row is None:
            out.append(Problem("site.claims", "floor", cid, "absent"))
            continue
        if cid in ERRORED_CLAIMS and str(row.get("status")) in ("pass", "verified"):
            out.append(Problem("site.claims", "never-pass", cid, str(row.get("status"))))
        if (claim_row_tone(row, claim_table) == failing_tone) != (cid in ERRORED_CLAIMS):
            out.append(Problem("site.claims", "tone", cid,
                               f"tone {claim_row_tone(row, claim_table)} "
                               f"(Failing: {failing_tone})"))
        if cid in ERRORED_CLAIMS:
            # The page's "PARTIAL — a gate … produced no proof" list gives each
            # evaluator's reason (`unproven`): the crash's says errored, never
            # a missing tool (F-10 reached the page here unchecked).
            gate = ERRORED_CLAIMS[cid]
            why = [u.get("why", "") for u in row.get("unproven") or () if u.get("gate") == gate]
            if not why:
                out.append(Problem("site.claims", "cites", cid, f"no unproven reason for {gate}"))
            for text in why:
                out += words_problems("site.claims", [Row(cid, "", str(text))])
                if not str(text).startswith("errored"):
                    out.append(Problem("site.claims", "lead", cid,
                                       f"{gate}'s reason does not lead with 'errored'",
                                       str(text)[:60]))
    out += order_problems("site.claims", [Row(str(r.get("id")), "") for r in claims])
    readiness = state.get("readiness") or {}
    counts = readiness.get("counts") or {}
    if counts.get("blocked", 0) - int(readiness.get("errored", 0)) != len(SKIPPED_CLAIMS):
        out.append(Problem("site.readiness", "counts", "*",
                           "blocked less errored is not the skipped count", str(counts)))
    # The page's count strip (`readiness.tally`): the skipped item splits the
    # crashes out, and nothing else counts them (review of the P2.1 design).
    tally = " · ".join(str(item.get("label", "")) for item in readiness.get("tally") or ())
    out += claim_count_problems("site.readiness.tally",
                                f"{readiness.get('n_claims')} claims · {tally}",
                                int(readiness.get("n_claims") or 0))
    if f"({len(ERRORED_CLAIMS)} errored)" not in tally:
        out.append(Problem("site.readiness.tally", "counts", "*", "no `(k errored)` split",
                           tally))
    out += sentence_problems("site.readiness.sentence", str(readiness.get("verdict") or ""))
    return out


# --------------------------------------------------------------------------- #
# the ratchet, emptied
# --------------------------------------------------------------------------- #
#: P2.0 landed this module with a ratchet, `KNOWN_QUIETER`: each exact problem
#: where an errored row read quieter than invariant 2 allows on `860ffa6`
#: (F-1 the reason's lead and its traceback; F-2 BLOCKING's order; F-3 `why`,
#: `claim show` and the report's bullets in gate-id order; F-4 doctor naming no
#: crash; F-5 the page's claim order; F-10 a skipped-and-errored verdict read by
#: its flags), with `ErrorNotYetLouder` red when one stopped showing. P2.1
#: fixed every one and deleted both (its done criterion): every problem on
#: every channel is red from here on. `unexplained` stays as the one way the
#: tests below say "no problem" — with nothing excused.
def unexplained(problems: list[Problem]) -> list[str]:
    return [str(p) for p in problems]


# --------------------------------------------------------------------------- #
# the in-process world: real run_gate verdicts, the report's own renderers
# --------------------------------------------------------------------------- #
#: The in-process stand-ins for the bracket's claims the fixture relies on:
#: C1 fails on a measurement, C2 passes (so PROVEN has a row), C4 is Stale,
#: C7 has no evaluator.
_STAND_INS: dict[str, tuple[str, list[str]]] = {
    "C1": ("Tip sags no more than 0.5 mm", ["deflection"]),
    "C2": ("Walls are printable", ["min-wall"]),
    "C4": ("Prints on the bed", ["bed-fit"]),
    "C7": ("First mode is clear of the pump", ["first-mode"]),
}


def _probe_registry(both: str = "verdict") -> gates_mod.Registry:
    """The planted evaluators, plus a pass, a stale pass and a measured fail.
    `both` picks the form the skipped-and-errored one returns: a `Verdict`, or
    the dict a gate may return instead (F-10's second spelling)."""
    reg = gates_mod.Registry()
    thin = NegativeControl(fixture="selftest/probe_bad.py:thin")

    @gates_mod.gate(id=SKIP_GATE, claims=["skip-probe"], requires_tools=[TOOL],
                    negative_control=thin, registry=reg)
    def needs_a_tool(ctx):
        return Verdict(gate=SKIP_GATE, passed=True)

    @gates_mod.gate(id=BOTH_GATE, claims=["both-probe"], negative_control=thin, registry=reg)
    def says_both(ctx):
        if both == "dict":
            return {"passed": False, "skipped": True, "skip_reason": BOTH_SKIP_REASON,
                    "error": EXCEPTION[BOTH_GATE]}
        return Verdict(gate=BOTH_GATE, passed=False, skipped=True,
                       skip_reason=BOTH_SKIP_REASON, error=EXCEPTION[BOTH_GATE])

    @gates_mod.gate(id=CRASH_GATE, claims=["crash-probe"], negative_control=thin, registry=reg)
    def crashes(ctx):
        raise ZeroDivisionError("planted crash")

    @gates_mod.gate(id="probe.fails", claims=["deflection"], negative_control=thin,
                    registry=reg)
    def fails(ctx):
        return Verdict(gate="probe.fails", passed=False, measured=0.7, limit=0.5, units="mm",
                       detail="0.700 mm at 15 N (limit 0.5 mm)")

    @gates_mod.gate(id="probe.holds", claims=["min-wall"], negative_control=thin,
                    registry=reg)
    def holds(ctx):
        return Verdict(gate="probe.holds", passed=True, detail="7.0 mm wall")

    @gates_mod.gate(id="probe.moved", claims=["bed-fit"], negative_control=thin,
                    registry=reg)
    def moved(ctx):
        return Verdict(gate="probe.moved", passed=True, detail="74 mm of 204")

    return reg


def _claim(cid: str, statement: str, tags: list[str], required: bool = True, *,
           physical: bool = False) -> Claim:
    return Claim(id=cid, statement=statement,
                 kind=ClaimKind.PHYSICAL if physical else ClaimKind.MEASURABLE,
                 acceptance=Acceptance(quantity="q", comparator=Comparator.LE, limit=1.0,
                                       units="mm"),
                 tags=list(tags), critical=required)


def _probe_ledger(registry: gates_mod.Registry, root: str) -> Ledger:
    ctx = gates_mod.GateContext(root=root, params={"config": {"thickness": 7.0}},
                                out_dir=root, tier=0)
    verdicts = [gates_mod.run_gate(spec, fn, ctx) for spec, fn in registry.pairs()]
    claims = [_claim(cid, st, tags) for cid, (st, tags) in _STAND_INS.items()]
    claims += [_claim(cid, st, tags, required, physical=cid == PHYSICAL_PROBE)
               for cid, (st, tags, required) in PROBE_CLAIMS.items()]
    return Ledger(meta=ProjectMeta(name="probe", revision="v0.1"), claims=claims,
                  verdicts=verdicts)


#: `report.severity` with the plain skip ranked above everything — Failing and a
#: crash included: the planted renderer the order checker must catch on the
#: real render path, so a checker that reads nothing cannot pass. P2.1 moved
#: the patch from `_SEVERITY` to `report.severity`, which replaced it
#: (strengthening-neutral, R-6).
_REAL_SEVERITY = report_mod.severity


def _SKIP_FIRST(composed: Any) -> int:
    if composed.status is ClaimStatus.BLOCKED and not composed.errored:
        return -1
    return _REAL_SEVERITY(composed)


# --------------------------------------------------------------------------- #
# the CLI world: one planted project, one run per command, shared
# --------------------------------------------------------------------------- #
#: (key, argv), in the order they run. The two check runs come before the model
#: edit that makes C4 Stale; everything after reads it.
_BEFORE_EDIT = (("check", ["check", "--junit"]), ("check.json", ["check", "--json"]))
_AFTER_EDIT = (
    ("status", ["status"]), ("status.json", ["status", "--json"]),
    ("report", ["report"]), ("report.json", ["report", "--json"]),
    ("report.write", ["report", "--write"]),
    ("why.P3", ["why", "P3"]), ("why.P7", ["why", "P7"]),
    ("claim.list", ["claim", "list"]), ("claim.list.json", ["claim", "list", "--json"]),
    ("claim.show.P3", ["claim", "show", "P3"]),
    ("claim.show.json", ["claim", "show", "P3", "--json"]),
    ("gate.show", ["gate", "show", BOTH_GATE]),
    ("doctor", ["doctor"]),
    ("site.init", ["site", "init"]), ("site.build", ["site", "build"]),
    # Last: it records a result, and every command above reads P8 without one.
    # With its evidence (P2.5a-D9: a pass on a physical claim needs a file), and
    # under the test identity (D5: with none, nothing is recorded).
    ("claim.physical", ["claim", "physical", PHYSICAL_PROBE, "--pass",
                        "--evidence=photos/p8.jpg"]),
)

#: Every CLI command path that renders a claim's status or an evaluator's
#: verdict, each run above. `test_every_command_is_rendered_or_says_why` holds
#: the parser to this set and the next.
RENDERED = frozenset({("check",), ("status",), ("report",), ("why",), ("claim", "list"),
                      ("claim", "show"), ("claim", "physical"), ("gate", "show"),
                      ("doctor",), ("site", "init"), ("site", "build")})

#: The rest, each with why it shows no errored claim or verdict. A new command
#: that does — `export <milestone>`, `export --dry-run`, the `/ready` path (P2,
#: W3, W11) — is in neither until someone runs it above.
NOT_A_STATUS_RENDERER: dict[tuple[str, ...], str] = {
    ("init",): "scaffolds an empty project; no verdict exists yet",
    ("ask",): "lists evidence to request, from the inputs alone; reads no verdict",
    ("ingest",): "files an input; reads no verdict",
    ("inputs",): "lists inputs; reads no verdict",
    ("extract",): "records what an input says; reads no verdict",
    ("gap",): "lists claims with no evaluator; an errored claim has one",
    ("gate", "list"): "describes registered gates from their specs; reads no verdict",
    ("gate", "selftest"): "prints known-bad control outcomes, not a claim's status: "
                          "a control that crashes is invariant 9's (test_admission)",
    ("decide",): "records a decision; reads no verdict",
    ("packs", "list"): "lists packs", ("packs", "show"): "prints a pack's guide",
    ("packs", "validate"): "checks a pack's layout and its own controls, not a claim",
    ("packs", "add"): "opts a project into a pack",
    ("model",): "prints the model's projection",
    ("site", "serve"): "serves the files `site build` wrote; the page is held through "
                       "`site init` and `site build` above",
    ("site", "vendor"): "downloads the page's libraries",
    ("site", "status"): "prints whether the site is built and current, and counts",
}


def command_paths(parser: argparse.ArgumentParser) -> set[tuple[str, ...]]:
    """Every leaf command path the parser accepts, aliases folded to the first name."""
    out: set[tuple[str, ...]] = set()

    def walk(p: argparse.ArgumentParser, prefix: tuple[str, ...]) -> None:
        subs = [a for a in p._actions if isinstance(a, argparse._SubParsersAction)]
        if not subs:
            if prefix:
                out.add(prefix)
            return
        seen: set[int] = set()
        for action in subs:
            for name, child in action.choices.items():
                if id(child) in seen:
                    continue
                seen.add(id(child))
                walk(child, prefix + (name,))

    walk(parser, ())
    return out


class _Run(NamedTuple):
    root: str
    out: dict[str, Any]          # key -> CompletedProcess
    files: dict[str, str]        # name -> text


_SHARED: list[_Run] = []


def _check_the_fixture(run: _Run) -> None:
    """The fixture's precondition: the skip is a skip, each crash is a crash (not
    a refusal of qualification, whose error starts `unqualified:`), C4 is
    Stale. A rotted fixture fails HERE, naming the evaluator, instead of passing
    every property on nothing."""
    for key, proc in run.out.items():
        want = 1 if key.startswith("check") else 0
        if proc.returncode != want:
            raise AssertionError(f"`atompipe {key}` exited {proc.returncode}, not {want}:\n"
                                 f"{proc.stdout[-2000:]}\n{proc.stderr[-2000:]}")
    rows = {row["gate"]: row for row in json.loads(run.out["check.json"].stdout)["verdicts"]}
    for gate in ERRORED_GATES:
        row = rows.get(gate) or {}
        if row.get("outcome") != "error" or str(row.get("error", "")).startswith("unqualified:"):
            raise AssertionError(f"fixture rotted: {gate} is not a crash any more — {row}")
        if EXCEPTION[gate] not in str(row.get("error")):
            raise AssertionError(f"fixture rotted: {gate} errored with {row.get('error')!r}")
    if (rows.get(SKIP_GATE) or {}).get("outcome") != "skipped":
        raise AssertionError(f"fixture rotted: {SKIP_GATE} is not skipped — {rows.get(SKIP_GATE)}")
    statuses = json.loads(run.out["status.json"].stdout)["claims"]
    if statuses.get("C4") != "stale":
        raise AssertionError(f"fixture rotted: the bed_xy edit left C4 {statuses.get('C4')}")


def _louder_project() -> _Run:
    """The planted bracket, every command run once, cached for the module."""
    if _SHARED:
        return _SHARED[0]
    tmp = tempfile.mkdtemp(prefix="atompipe-louder-")
    unittest.addModuleCleanup(_env._rmtree, tmp)
    root = _projects.bracket_copy(os.path.join(tmp, "bracket"), migrated=True)
    home = os.path.join(tmp, "home")
    os.makedirs(home)
    with open(os.path.join(root, "gates", "zz_probe.py"), "w", encoding="utf-8") as fh:
        fh.write(PROBE_GATES)
    with open(os.path.join(root, "selftest", "probe_bad.py"), "w", encoding="utf-8") as fh:
        fh.write(PROBE_FIXTURE)
    for cid, (statement, tags, required) in PROBE_CLAIMS.items():
        record = {"statement": statement,
                  "kind": "physical" if cid == PHYSICAL_PROBE else "measurable", "tags": tags,
                  "acceptance": {"quantity": "q", "comparator": "<=", "limit": 1.0,
                                 "units": "mm"}}
        if not required:
            record["critical"] = False
        with open(os.path.join(root, "claims", f"{cid}.json"), "w", encoding="utf-8") as fh:
            json.dump(record, fh, indent=2)
    out: dict[str, Any] = {}
    for key, argv in _BEFORE_EDIT:
        out[key] = _env.atompipe(argv, cwd=root, home=home)
    model = os.path.join(root, "model", "bracket.py")
    with open(model, encoding="utf-8") as fh:
        text = fh.read()
    edited, n = _BED_XY.subn(r"\g<1>250.0", text)
    if n != 1:
        raise AssertionError(f"{model}: expected one `bed_xy: float = 220.0` line, found {n}")
    with open(model, "w", encoding="utf-8") as fh:
        fh.write(edited)
    os.makedirs(os.path.join(root, "photos"), exist_ok=True)
    with open(os.path.join(root, "photos", "p8.jpg"), "wb") as fh:
        fh.write(b"\xff\xd8 the probe article \xff\xd9")
    for key, argv in _AFTER_EDIT:
        out[key] = _env.atompipe(argv, cwd=root, home=home, identity=True)
    files = {}
    for name, rel in (("junit", report_mod.JUNIT_DEFAULT), ("readiness", "docs/readiness.md"),
                      ("state", "site/data/state.json"), ("format.js", "site/lib/format.js"),
                      ("panels.js", "site/lib/panels.js"),
                      ("last_check", ".atompipe/cache/last_check.json")):
        with open(os.path.join(root, *rel.split("/")), encoding="utf-8") as fh:
            files[name] = fh.read()
    run = _Run(root, out, files)
    _check_the_fixture(run)
    _SHARED.append(run)
    return run


def cli_problems(run: _Run) -> list[Problem]:
    """Every channel of the shared project, judged."""
    n_claims = 7 + len(PROBE_CLAIMS)
    out = check_problems(run.out["check"].stdout)
    out += junit_problems("check.junit", run.files["junit"])
    check_doc = json.loads(run.out["check.json"].stdout)
    out += json_row_problems("check.json", check_doc["blocking"], id_key="claim")
    for row in check_doc["verdicts"]:
        if row["gate"] in ERRORED_GATES and (row.get("ok") or row.get("outcome") != "error"):
            out.append(Problem("check.json", "outcome", row["gate"],
                               f"ok {row.get('ok')}, outcome {row.get('outcome')}", str(row)))
    out += terminal_problems("status", run.out["status"].stdout, n_claims)
    out += json_map_problems("status.json", json.loads(run.out["status.json"].stdout))
    out += markdown_problems("report", run.out["report"].stdout)
    out += markdown_problems("report.file", run.files["readiness"])
    out += json_map_problems("report.json", json.loads(run.out["report.json"].stdout))
    out += why_problems("why.P3", run.out["why.P3"].stdout, floor=("errored", "skipped"))
    out += why_problems("why.P7", run.out["why.P7"].stdout, floor=("errored",))
    # `claim list` is in record order on purpose — its docstring: "an agent
    # reading this twenty minutes apart needs the same line in the same place,
    # and a list that reorders itself as things pass is unreadable as a diff".
    # So it is held to tone and never-pass only; whether invariant 2's order
    # binds it is a P2.1 panel question (default: no — the tag carries it).
    out += row_problems("claim.list", claim_rows(run.out["claim.list"].stdout.splitlines()),
                        order=False, cites=False)
    out += json_row_problems("claim.list.json",
                             json.loads(run.out["claim.list.json"].stdout)["claims"],
                             id_key="id")
    show = run.out["claim.show.P3"].stdout.splitlines()
    out += row_problems("claim.show.P3", claim_rows(show[:1]), order=False, cites=False,
                        floor=("errored",))
    out += why_problems("claim.show.P3", "\n".join(show[1:]), floor=("errored", "skipped"))
    last = [ln.split("last verdict:", 1)[1] for ln in run.out["gate.show"].stdout.splitlines()
            if "last verdict:" in ln]
    out += row_problems("gate.show", gate_rows(last), cites=False, floor=("errored",))
    out += doctor_problems(run.out["doctor"].stdout)
    # `claim physical`'s own row: the status composed after the write — never
    # one built from the result alone (review of the P2.1 design: it printed
    # `[ok   ]` beside a crashing modelled half that `status` read Skipped).
    out += row_problems("claim.physical",
                        claim_rows(run.out["claim.physical"].stdout.splitlines()),
                        order=False, floor=("errored",), lead=True)
    out += state_problems(json.loads(run.files["state"]), run.files["format.js"],
                          run.files["panels.js"])
    return out


def _channel(problems: list[Problem], *prefixes: str) -> list[Problem]:
    return [p for p in problems if p.channel.startswith(prefixes)]


_CLI_PROBLEMS: list[list[Problem]] = []


def _cli() -> list[Problem]:
    if not _CLI_PROBLEMS:
        _CLI_PROBLEMS.append(cli_problems(_louder_project()))
    return _CLI_PROBLEMS[0]


# --------------------------------------------------------------------------- #
class ErrorIsLouder(_env.EnvCase):
    """(2, second sentence) A crash reads louder than a missing tool, in every
    renderer: never a pass, never the skip's quiet tag or its words, above every
    skipped row, counted once and never as a missing tool, a JUnit `<error>`
    whether or not a milestone requires its claim, and quieter than a measured
    fail only (`KNOWN_QUIETER` names where today falls short)."""

    # -- the checkers, on planted rows ------------------------------------ #
    def test_the_checkers_refuse_what_they_forbid(self):
        clean = [Row("C1", "FAIL ", "probe.fails : 0.7 mm"),
                 Row("P2", "FAIL ", f"{CRASH_GATE} : x | {EXCEPTION[CRASH_GATE]}"),
                 Row("P7", "FAIL ", f"{BOTH_GATE} : {EXCEPTION[BOTH_GATE]}"),
                 Row("C4", "STALE", "passed, but not current"),
                 Row("P1", "skip ", f"{SKIP_GATE} : requires {TOOL} (not on PATH)")]
        self.assertEqual(row_problems("t", clean, full=True), [])
        planted = {
            "errored row below the skip": ([clean[0], clean[4], clean[1], clean[2], clean[3]],
                                           "order"),
            "errored row below a stale one": ([clean[3], *clean[:3], clean[4]], "order"),
            "its tag lowered to the skip's": ([clean[0], clean[1]._replace(tag="skip "),
                                               *clean[2:]], "tone"),
            "the skip as loud as a crash": ([*clean[:4], clean[4]._replace(tag="SKIP ")],
                                            "tone"),
            "an ok tag on a crash": ([clean[0], clean[1]._replace(tag="ok   "), *clean[2:]],
                                     "never-pass"),
            "its reason swapped for the skip's": (
                [clean[0], clean[1]._replace(body=f"{CRASH_GATE} : requires {TOOL}"),
                 *clean[2:]], "skip-words"),
            "a reason without the exception": (
                [clean[0], clean[1]._replace(body=f"{CRASH_GATE} : Traceback (most"),
                 *clean[2:]], "cites"),
            "a reason citing another gate": (
                [clean[0], clean[1]._replace(body=f"probe.fails : {EXCEPTION[CRASH_GATE]}"),
                 *clean[2:]], "cites"),
            "a parse that read no errored row": ([clean[0], clean[3], clean[4]], "floor"),
            "a parse that read nothing": ([], "floor"),
        }
        for name, (rows, prop) in planted.items():
            with self.subTest(name):
                found = row_problems("t", rows, full=True)
                self.assertTrue(any(p.prop == prop for p in found), (prop, found))

        self.assertEqual(gate_count_problems("t", "9 gates: 1 skipped, 2 errored"), [])
        self.assertEqual(gate_count_problems("t", "9 gates: 3 skipped (2 errored)"), [])
        for line in ("6 ran, 2 skipped, 2 errored", "6 ran, 3 skipped, 1 errored",
                     "6 ran, 3 skipped (2 errored), 2 errored", "6 ran, 1 skipped"):
            with self.subTest(line):
                self.assertTrue(gate_count_problems("t", line))
        good = "claims 14 — ok 2 | FAIL 7 | STALE 1 | skip 1 | gap 1 | phys 1 | assum 1"
        self.assertEqual(claim_count_problems("t", good, 14), [])
        self.assertEqual(claim_count_problems(
            "t", "14 claims · 2 checked · 1 failing · 7 skipped (6 errored) · 1 stale · "
                 "1 gap · 1 pending build · 1 assumed", 14), [])
        for line in ("claims 14 — ok 2 | FAIL 1 | STALE 1 | skip 7 | gap 1 | phys 1 | assum 1",
                     "claims 14 — ok 2 | FAIL 7 | STALE 1 | skip 2 | gap 1 | phys 1 | assum 1",
                     "claims 14 — nothing"):
            with self.subTest(line):
                self.assertTrue(claim_count_problems("t", line, 14))
        # The claims not required — all three crashed — in the clause P2.1's
        # review split by word (a crash counted apart there too).
        others = " 3 claims not required are unresolved — 3 skipped (3 errored) (P4, P6, P8)."
        self.assertEqual(sentence_problems(
            "t", "8 of 12 — 5 failing (C1, P2, P3, P5, +1 more); 1 blocked on missing "
                 "tooling (P1); 1 with no gate at all (C7)." + others), [])
        # Planted: the clause as P2.1 wrote it — the three crashes one bare count.
        self.assertTrue(any(p.prop == "counts" and "not required" in p.text
                            for p in sentence_problems(
                                "t", "8 of 12 — 1 failing (C1); 5 skipped (4 errored) (P2, P3, "
                                     "P5, P7, +1 more). 3 claims not required are unresolved "
                                     "(P4, P6, P8).")))
        self.assertTrue(sentence_problems(
            "t", "2 blocked on missing tooling (P1, P2); 1 failing (C1)."))
        self.assertTrue(sentence_problems("t", "nothing to see"))
        # GLOSSARY's word for the status, with and without the errored split.
        self.assertEqual(sentence_problems(
            "t", "8 of 12 — 1 failing (C1); 5 skipped (4 errored) (P2, P3, P5, P7, +1 more); "
                 "1 gap (C7)." + others), [])
        for sentence, prop in (
                ("8 of 12 — 1 failing (C1); 5 skipped (P1, P2, P3, P5, +1 more); 1 gap (C7).",
                 "skip-words"),
                ("8 of 12 — 1 failing (C1); 5 skipped (3 errored) (P2, P3, P5, +2 more).",
                 "counts")):
            with self.subTest(sentence):
                self.assertTrue(any(p.prop == prop for p in sentence_problems("t", sentence)),
                                sentence_problems("t", sentence))

        # The skip's GLOSSARY lead on an errored row, under a loud tag (P2.1's
        # default `[SKIP ]`): never, from today.
        led = [clean[0], clean[1]._replace(tag="SKIP ", body=f"skipped: {clean[1].body}"),
               *clean[2:]]
        self.assertTrue(any(p.prop == "skip-words" and p.subject == "P2"
                            for p in row_problems("t", led, full=True)))
        # The reason's lead (F-1): `errored:` and the exception, never the traceback.
        self.assertEqual(lead_problems("t", [Row("P2", "SKIP ", f"errored: {CRASH_GATE} : "
                                                              f"{EXCEPTION[CRASH_GATE]}")]), [])
        for body in (f"{CRASH_GATE} : {EXCEPTION[CRASH_GATE]}",
                     f"skipped: {CRASH_GATE} : {EXCEPTION[CRASH_GATE]}",
                     f"errored: {CRASH_GATE} : Traceback (most recent call last): | File",
                     f'errored: {CRASH_GATE} : File "/a/long/path/gates.py", line 1695, in '
                     f'run_gate | {EXCEPTION[CRASH_GATE]}'):
            with self.subTest(body):
                self.assertTrue(lead_problems("t", [Row("P2", "SKIP ", body)]))
        # A JSON map with no summary to count is a floor, never a pass.
        self.assertTrue(any(p.prop == "floor" for p in json_map_problems(
            "t", {"claims": {"P1": "blocked", **{c: "fail" for c in ERRORED_CLAIMS}},
                  "errored": list(ERRORED_CLAIMS)})))
        # The report's evaluator bullets: the crash above the skip in its block.
        def block(*bullets: str) -> str:
            return "\n".join([f"**x — 1 failing (P3).**", f"{report_mod.SECTION_PROVEN} (x)",
                              "| **C2** x |", "## Failing / blocked",
                              "### [FAIL ] P3 — both probes hold", *bullets])
        skip_bullet = f"- `[skip] {SKIP_GATE} : requires {TOOL} (not on PATH)`"
        err_bullet = f"- `[ERR ] {CRASH_GATE} : {EXCEPTION[CRASH_GATE]}`"
        self.assertTrue([p for p in markdown_problems("t", block(skip_bullet, err_bullet))
                         if p.channel == "t.P3" and p.prop == "order"])
        self.assertFalse([p for p in markdown_problems("t", block(err_bullet, skip_bullet))
                          if p.channel == "t.P3" and p.prop == "order"])

    def test_a_gates_line_that_folds_a_crash_into_the_skip_is_caught(self):
        """Planted: a render that counts both crashes as missing tools and drops
        the word errored (`gates: 3 ran, 1 invalidated, 4 skipped`), and a worse
        order in `check`'s BLOCKING list. (Was `test_the_ratchet_excuses_only_
        the_problems_it_names`, R-6: with `KNOWN_QUIETER` emptied by P2.1 there
        is nothing left to excuse, and the plant now stands on its own.)"""
        registry = _probe_registry()
        ledger = _probe_ledger(registry, self.tmp())
        real = report_mod.render_terminal(ledger, registry, stale_gates={"probe.moved"})

        def fold(match: re.Match) -> str:
            line = match.group(0)
            n = sum(int(x) for x in re.findall(r"(\d+) (?:skipped|errored)", line))
            return re.sub(r"\d+ skipped", f"{n} skipped", re.sub(r", \d+ errored", "", line))

        planted = re.sub(r"^gates: .*$", fold, real, flags=re.M)
        self.assertIn("3 skipped", planted)
        self.assertNotIn("errored", next(ln for ln in planted.splitlines()
                                         if ln.startswith("gates: ")))
        found = unexplained(terminal_problems("render_terminal", planted, len(ledger.claims)))
        self.assertTrue([p for p in found if p.startswith("render_terminal.gates: counts")],
                        found)
        self.assertTrue(unexplained(gate_count_problems(
            "status.gates", "gates: 6 ran, 1 invalidated, 4 skipped")))
        worse = [Row("C4", "STALE", "passed, but not current"), Row("C7", "gap  ", "no gate"),
                 Row("P1", "skip ", f"{SKIP_GATE} : requires {TOOL}"),
                 Row("P2", "FAIL ", f"{CRASH_GATE} : {EXCEPTION[CRASH_GATE]}")]
        self.assertTrue(unexplained(order_problems("check.blocking", worse)))

    # -- in process ------------------------------------------------------- #
    def _render(self, both: str = "verdict") -> tuple[Ledger, gates_mod.Registry]:
        registry = _probe_registry(both)
        return _probe_ledger(registry, self.tmp()), registry

    def test_render_terminal(self):
        for both in ("verdict", "dict"):
            with self.subTest(both=both):
                ledger, registry = self._render(both)
                text = report_mod.render_terminal(ledger, registry,
                                                  stale_gates={"probe.moved"})
                problems = terminal_problems("render_terminal", text, len(ledger.claims))
                self.assertEqual(unexplained(problems), [], text)
                with mock.patch.object(report_mod, "severity", _SKIP_FIRST):
                    planted = report_mod.render_terminal(ledger, registry,
                                                         stale_gates={"probe.moved"})
                order = [p for p in terminal_problems("render_terminal", planted,
                                                      len(ledger.claims)) if p.prop == "order"]
                self.assertEqual({p.subject for p in order}, set(ERRORED_CLAIMS), planted)

    def test_render_markdown(self):
        for both in ("verdict", "dict"):
            with self.subTest(both=both):
                ledger, registry = self._render(both)
                md = report_mod.render_markdown(ledger, registry, stale_gates={"probe.moved"})
                self.assertEqual(unexplained(markdown_problems("render_markdown", md)), [], md)
                with mock.patch.object(report_mod, "severity", _SKIP_FIRST):
                    planted = report_mod.render_markdown(ledger, registry,
                                                         stale_gates={"probe.moved"})
                # The claim heads' channel: each block's bullets are judged
                # under `render_markdown.<claim>`, and `_SEVERITY` orders heads.
                order = [p for p in markdown_problems("render_markdown", planted)
                         if p.prop == "order" and p.channel == "render_markdown"]
                self.assertEqual({p.subject for p in order}, set(ERRORED_CLAIMS))

    def _junit(self, ledger: Ledger, registry: gates_mod.Registry) -> str:
        return report_mod.render_junit(ledger, ledger.verdicts, registry, tier=0, ready=False,
                                       exit_code=1, when="2026-10-03T00:00:00Z",
                                       stale_gates={"probe.moved"})

    def test_render_junit_required_or_not(self):
        """F-7: `_claim_case` picks `<error>` only for a FAIL whose explaining
        verdict errored, and a not-required claim is red only for FAIL or
        REFUTED — so rung 4 moved alone would make a required errored claim
        `<failure type="blocked">` and a not-required one `<skipped>`. Pinned on
        both suites now, with a planted `_claim_case` as the violator."""
        for both in ("verdict", "dict"):
            with self.subTest(both=both):
                ledger, registry = self._render(both)
                self.assertEqual(unexplained(junit_problems("render_junit",
                                                            self._junit(ledger, registry))), [])
        ledger, registry = self._render()
        original = report_mod._claim_case

        def planted(suite, ledger_, claim, composed, cover, *, red, stale_reasons=None):
            if claim.id not in ERRORED_CLAIMS:
                return original(suite, ledger_, claim, composed, cover, red=red,
                                stale_reasons=stale_reasons)
            case = report_mod._xml_sub(suite, "testcase", classname=suite.get("name"),
                                       name=claim.id, time="0")
            report_mod._xml_sub(case, "skipped", message="skipped")
            return None

        with mock.patch.object(report_mod, "_claim_case", planted):
            found = junit_problems("render_junit", self._junit(ledger, registry))
        self.assertEqual({p.subject for p in found if p.prop == "junit"}, set(ERRORED_CLAIMS))

    def test_a_skipped_and_errored_verdict_is_a_crash_in_either_form(self):
        """F-10's verdict: `run_gate` hands both spellings back as an error, never
        ok, and both render the same — so a fix keyed on `outcome` covers both."""
        texts = []
        for both in ("verdict", "dict"):
            ledger, registry = self._render(both)
            verdict = next(v for v in ledger.verdicts if v.gate == BOTH_GATE)
            with self.subTest(both=both):
                self.assertEqual(verdict.outcome, "error")
                self.assertFalse(verdict.ok)
                self.assertEqual(verdict.error, EXCEPTION[BOTH_GATE])
                self.assertEqual(claims_mod.explaining_verdict(
                    next(c for c in ledger.claims if c.id == "P7"), ledger.verdicts), verdict)
            texts.append(report_mod.render_terminal(ledger, registry,
                                                    stale_gates={"probe.moved"}))
        self.assertEqual(texts[0], texts[1])

    def test_a_fail_beside_an_error_reads_failing(self):
        """The ranking's other half: an evaluator that failed the candidate
        outranks one that settled nothing, in every order, in `status`, in
        `check`'s reason and in JUnit. P2.1 must keep it so (a fail beside an
        error is Failing, GLOSSARY §3: first match wins)."""
        registry = _probe_registry()
        ledger = _probe_ledger(registry, self.tmp())
        claim = _claim("C9", "the deflection and the crash probe hold",
                       ["deflection", "crash-probe"])
        ledger.claims.append(claim)
        want = "probe.fails : 0.700 mm at 15 N (limit 0.5 mm)"
        for order in (ledger.verdicts, ledger.verdicts[::-1]):
            with self.subTest(first=order[0].gate):
                ledger.verdicts = list(order)
                status = claims_mod.resolve_status(claim, ledger.verdicts)
                self.assertEqual(status, ClaimStatus.FAIL)
                self.assertEqual(cli_mod._blocking_reason(ledger, claim, status), want)
                rows = [r for r in claim_rows(report_mod.render_terminal(
                    ledger, registry).splitlines()) if r.subject == "C9"]
                self.assertEqual(len(rows), 1)
                self.assertEqual((rows[0].tag, rows[0].body), (report_mod.STATUS_TAG[status],
                                                               want))
                case = ET.fromstring(self._junit(ledger, registry)).find(
                    "testsuite[@name='claims.critical']/testcase[@name='C9']")
                self.assertEqual([(c.tag, c.get("type"), c.get("message")) for c in case],
                                 [("failure", "fail", want)])

    # -- through the commands --------------------------------------------- #
    def test_the_fixture_is_what_it_says(self):
        """Discriminating by construction: every alphabetical or record order
        puts the skip first, so no order check passes on a renderer that does
        not rank, and the ratchet's order entries show because the fixture can
        show them, not by accident."""
        run = _louder_project()
        self.assertLess("P1", min(ERRORED_CLAIMS))
        self.assertLess(SKIP_GATE, min(ERRORED_GATES))
        listed = [row["id"] for row in json.loads(run.out["claim.list.json"].stdout)["claims"]]
        self.assertLess(listed.index("P1"), min(listed.index(c) for c in ERRORED_CLAIMS))
        self.assertLess(listed.index("C4"), min(listed.index(c) for c in ERRORED_CLAIMS))
        freshness = json.loads(run.out["status.json"].stdout)["freshness"]
        self.assertIn("remembered error", freshness[CRASH_GATE]["notes"],
                      "the crash is not remembered at the current inputs")

    def _holds(self, *prefixes: str) -> None:
        problems = _channel(_cli(), *prefixes)
        self.assertEqual(unexplained(problems), [])

    def test_check_output(self):
        self._holds("check.stream", "check.summary", "check.blocking")

    def test_claim_physical(self):
        self._holds("claim.physical")

    # -- P2.1's additions: the lead, and the count apart ------------------ #
    def test_reason_leads_with_errored(self):
        """Every errored claim's reason, on every channel that prints one, leads
        `errored: <gate> : <exception>` and never carries the traceback (P2.0
        F-1). Planted: a `reason` that drops the lead."""
        registry = _probe_registry()
        ledger = _probe_ledger(registry, self.tmp())
        leads = [p for p in _cli() if p.prop == "lead"]
        text = report_mod.render_terminal(ledger, registry, stale_gates={"probe.moved"})
        leads += [p for p in terminal_problems("render_terminal", text, len(ledger.claims))
                  if p.prop == "lead"]
        self.assertEqual([str(p) for p in leads], [])
        channels = {"status", "check.blocking", "check.junit", "render_terminal", "report",
                    "claim.physical"}
        real = report_mod.reason

        def unled(*args, **kwargs):
            said = real(*args, **kwargs)
            return said.split(": ", 1)[1] if said.startswith("errored: ") else said

        with mock.patch.object(report_mod, "reason", unled):
            planted = report_mod.render_terminal(ledger, registry, stale_gates={"probe.moved"})
            junit = junit_problems("render_junit", self._junit(ledger, registry))
        found = {p.subject for p in terminal_problems("render_terminal", planted,
                                                      len(ledger.claims)) + junit
                 if p.prop == "lead"}
        self.assertEqual(found, set(ERRORED_CLAIMS))
        self.assertTrue(channels)

    def test_counts_say_skipped_with_errored_apart(self):
        """`N skipped (k errored)` wherever claims are counted — `status`'s and
        render_terminal's count lines, the readiness sentence in `status`, the
        report and `state.json`, `summary.errored`, `readiness.errored` and the
        page's strip — and never a crash inside a plain skip. Planted: a count
        line that folds the crashes into the skip."""
        run = _louder_project()
        n = len(ERRORED_CLAIMS) + len(SKIPPED_CLAIMS)
        split = f"{n} skipped ({len(ERRORED_CLAIMS)} errored)"
        status = run.out["status"].stdout.splitlines()
        self.assertTrue(any(re.match(r"^\d+ claims · ", ln) and split in ln for ln in status),
                        status[:4])
        state = json.loads(run.files["state"])
        self.assertEqual(state["readiness"]["errored"], len(ERRORED_CLAIMS))
        for where in ("status.json", "report.json"):
            self.assertEqual(json.loads(run.out[where].stdout)["summary"]["errored"],
                             len(ERRORED_CLAIMS), where)
        problems = [p for p in _cli() if p.prop in ("counts", "floor")
                    and (p.channel.endswith(("counts", "sentence", "tally", "gates"))
                         or p.channel in ("status.json", "report.json"))]
        self.assertEqual([str(p) for p in problems], [])
        registry = _probe_registry()
        ledger = _probe_ledger(registry, self.tmp())
        real = report_mod.count_bits

        def folded(composed):
            bits = real(composed)
            return [dict(b, errored=0, label=re.sub(r" \(\d+ errored\)", "", b["label"]))
                    for b in bits]

        with mock.patch.object(report_mod, "count_bits", folded):
            planted = report_mod.render_terminal(ledger, registry, stale_gates={"probe.moved"})
        self.assertTrue([p for p in terminal_problems("render_terminal", planted,
                                                      len(ledger.claims))
                         if p.channel == "render_terminal.counts"], planted)

    def test_check_junit(self):
        self._holds("check.junit")

    def test_check_json(self):
        self._holds("check.json")

    def test_status(self):
        self._holds("status")

    def test_report(self):
        self._holds("report")

    def test_why_and_claim_show(self):
        self._holds("why.", "claim.show")

    def test_claim_list(self):
        self._holds("claim.list")

    def test_gate_show(self):
        self._holds("gate.show")

    def test_doctor(self):
        self._holds("doctor")

    def test_site_state(self):
        self._holds("site.")

    def test_every_command_is_rendered_or_says_why(self):
        """A command that renders a claim's status joins `RENDERED` and the runs
        above, or says in `NOT_A_STATUS_RENDERER` why it never shows an errored
        claim. What this stops: `export <milestone>` (P2) shipping its refusal
        in `_STATUS_PHRASE`'s words — "C2 blocked on missing tooling" for a
        crash — with no run of it anywhere in this module."""
        paths = command_paths(cli_mod.build_parser())
        self.assertGreater(len(paths), 20, sorted(paths))
        self.assertEqual(sorted(paths - RENDERED - set(NOT_A_STATUS_RENDERER)), [])
        self.assertEqual(sorted((RENDERED | set(NOT_A_STATUS_RENDERER)) - paths), [],
                         "a listed command no longer exists")
        self.assertEqual(sorted(RENDERED & set(NOT_A_STATUS_RENDERER)), [])
        ran = {tuple(a for a in argv if not a.startswith("-") and a not in ERRORED_CLAIMS
                     and a not in ERRORED_GATES)
               for _k, argv in _BEFORE_EDIT + _AFTER_EDIT}
        self.assertEqual(sorted(RENDERED - ran), [], "a rendered command nobody runs")
        planted = cli_mod.build_parser()
        sub = next(a for a in planted._actions if isinstance(a, argparse._SubParsersAction))
        sub.add_parser("export")
        self.assertEqual(sorted(command_paths(planted) - RENDERED - set(NOT_A_STATUS_RENDERER)),
                         [("export",)])


# --------------------------------------------------------------------------- #
# last_check.json's `worst`: the most urgent blocker, never the first by record
# --------------------------------------------------------------------------- #
def _worst(root: str, order: tuple[str, ...]) -> dict:
    """`write_last_check`'s `worst` over three critical claims written in
    `order` — S skipped (a tool missing), E errored (a crash), F failing."""
    from atompipe import store as store_mod
    from atompipe import verdicts as verdicts_mod
    from atompipe.models import GateSpec, Tier
    made = {
        "S": Verdict(gate="g.s", claims=["S"], skipped=True,
                     skip_reason="requires atompipe-no-such-tool (not on PATH)"),
        "E": Verdict(gate="g.e", claims=["E"], error="RuntimeError: planted crash"),
        "F": Verdict(gate="g.f", claims=["F"], passed=False, detail="0.7 mm vs 0.5 mm"),
    }
    specs = [GateSpec(id=f"g.{cid.lower()}", claims=[cid], tier=Tier.INSTANT,
                      negative_control=NegativeControl(fixture="x:y")) for cid in order]
    ledger = Ledger(meta=ProjectMeta(name="w", revision="v0.1"),
                    claims=[Claim(id=cid, statement=cid, gates=[f"g.{cid.lower()}"])
                            for cid in order])
    if not os.path.isdir(os.path.join(root, ".atompipe")):
        store_mod.init(root, ProjectMeta(name="w", revision="v0.1"))
    path = verdicts_mod.write_last_check(
        root, verdicts_mod.SweepResult(record=True, ledger=ledger, registry=specs),
        verdicts_mod.Resolution(verdicts=[made[cid] for cid in order]), now="t")
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)["worst"]


class WorstIsTheMostUrgent(unittest.TestCase):
    """(2) `last_check.json`'s `worst` — what P3's hook will say first — is the
    most urgent blocking claim in `claims.severity`'s order, the order `check`
    prints its BLOCKING list in: a fail, then a crash, then a missing tool.
    What slipped through (review of P2.1, P2.0 F-2 again on the JSON channel):
    `blocking[0]`, record order, so a skip written first named the missing tool
    and hid the crash `check` printed above it."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="atompipe-worst-")
        self.addCleanup(_env._rmtree, self.root)

    def test_a_crash_outranks_a_skip_and_a_fail_outranks_both(self):
        for order, want in ((("S", "E"), ("E", "errored")), (("S", "E", "F"), ("F", "failed")),
                            (("E", "S"), ("E", "errored"))):
            with self.subTest(order=order):
                worst = _worst(self.root, order)
                self.assertEqual((worst["claim"], worst["cause"]), want, worst)

    def test_record_order_is_caught(self):
        """Planted: a severity that ranks nothing — record order again."""
        with mock.patch.object(claims_mod, "severity", lambda composed: 0):
            worst = _worst(self.root, ("S", "E"))
        self.assertEqual((worst["claim"], worst["cause"]), ("S", "skipped"))


if __name__ == "__main__":
    unittest.main(verbosity=2)


# --------------------------------------------------------------------------- #
# V11 (invariant 2): an unqualified evaluator never wears a crash's tag or word
# --------------------------------------------------------------------------- #
#: An always-False evaluator planted into a bracket copy, with a claim of its
#: own: it fails its known-bad control and its known-good one, so it is
#: unqualified — a refusal of qualification that crashed nothing.
NEVER_GATE = "bracket.never"
NEVER_SOURCE = f'''\
"""Planted by tests/test_louder.py: an evaluator that fails everything (S-04)."""
from atompipe.gates import gate
from atompipe.models import NegativeControl, Verdict


@gate(id="{NEVER_GATE}", claims=["never-probe"],
      negative_control=NegativeControl(fixture="selftest/bad_configs.py:quarter_thickness"))
def never(ctx):
    d = float(ctx.params["deflection"])
    return Verdict(gate="{NEVER_GATE}", passed=False, measured=round(d, 4), limit=0.5,
                   units="mm", detail=f"{{d:.3f}} mm")
'''
NEVER_CLAIM = {"statement": "the never probe holds", "kind": "measurable",
               "tags": ["never-probe"],
               "acceptance": {"quantity": "q", "comparator": "<=", "limit": 0.5, "units": "mm"}}

#: What an unqualified evaluator must never read as, on any channel: a crash.
CRASH_WORDS = re.compile(r"\[ERR \]|\berrored\b|not admitted")


def _never_project(case: unittest.TestCase) -> str:
    tmp = tempfile.mkdtemp(prefix="atompipe-unqualified-")
    case.addCleanup(_env._rmtree, tmp)
    root = _projects.bracket_copy(os.path.join(tmp, "bracket"), migrated=True)
    with open(os.path.join(root, "gates", "zz_never.py"), "w", encoding="utf-8") as fh:
        fh.write(NEVER_SOURCE)
    with open(os.path.join(root, "claims", "C9.json"), "w", encoding="utf-8") as fh:
        json.dump(NEVER_CLAIM, fh, indent=2)
    return root


class UnqualifiedNeverWearsAnOutcome(_env.EnvCase):
    """(V11, invariant 2 read the other way) An unqualified evaluator is not a
    crash: no channel tags it `[ERR ]`, words it *errored* or *not admitted*, or
    counts it errored. What slipped through P2.1's review: the count was fixed
    and the row was not — `check` streamed `[ERR ] <gate> : not admitted: …`
    and the page's verdict row read *errored*, a crash's loudness on something
    that crashed nothing."""

    def test_no_channel_reads_an_unqualified_evaluator_as_a_crash(self):
        root = _never_project(self)
        check = _env.atompipe(["check", "--junit"], cwd=root)
        self.assertEqual(check.returncode, 1, check.stderr)
        mine = [ln for ln in check.stdout.splitlines() if NEVER_GATE in ln]
        self.assertIn(f"{NEVER_GATE} : known-good fail · known-bad fail → unqualified", mine)
        for line in mine:
            self.assertIsNone(CRASH_WORDS.search(line), line)
        summary = next(ln for ln in check.stdout.splitlines() if " gates: " in ln
                       or " evaluators: " in ln)
        self.assertNotIn("errored", summary)
        doc = json.loads(_env.atompipe(["check", "--json"], cwd=root).stdout)
        self.assertEqual(doc["counts"]["errored"], 0, doc["counts"])
        self.assertEqual(doc["counts"]["unqualified"], 1, doc["counts"])
        status = _env.atompipe(["status"], cwd=root).stdout
        row = next(ln for ln in status.splitlines() if re.match(r"^\[.{5}\] C9 ", ln))
        self.assertTrue(row.startswith("[gap  ] C9 "), row)
        self.assertTrue(row.endswith(f"unqualified: {NEVER_GATE} : known-good fail"), row)
        show = _env.atompipe(["gate", "show", NEVER_GATE], cwd=root).stdout
        for line in show.splitlines():
            self.assertIsNone(CRASH_WORDS.search(line), line)
        self.assertIn(f"  qualification: known-good fail · known-bad fail → unqualified", show)
        shown = json.loads(_env.atompipe(["gate", "show", NEVER_GATE, "--json"],
                                         cwd=root).stdout)
        self.assertEqual(shown["last_selftest"]["outcome"], "unqualified", shown)
        self.assertEqual(shown["qualification"]["line"],
                         f"{NEVER_GATE} : known-good fail · known-bad fail → unqualified")
        with open(os.path.join(root, report_mod.JUNIT_DEFAULT), encoding="utf-8") as fh:
            junit = ET.fromstring(fh.read())
        case = next(tc for tc in junit.iter("testcase") if tc.get("name", "").startswith("C9"))
        self.assertIsNone(case.find("error"), ET.tostring(case))
        self.assertIsNotNone(case.find("failure"), ET.tostring(case))
        with open(os.path.join(root, ".atompipe", "cache", "last_check.json"),
                  encoding="utf-8") as fh:
            last = json.load(fh)
        self.assertNotIn("C9", json.dumps(last.get("errored", [])))
        md = _env.atompipe(["report"], cwd=root).stdout
        for line in md.splitlines():
            if "C9" in line or NEVER_GATE in line:
                self.assertIsNone(CRASH_WORDS.search(line), line)
        _env.atompipe(["site", "init"], cwd=root)
        _env.atompipe(["site", "build"], cwd=root)
        with open(os.path.join(root, "site", "data", "state.json"), encoding="utf-8") as fh:
            state = json.load(fh)
        rows = [v for v in state["verdicts"] if v.get("gate") == NEVER_GATE]
        self.assertEqual([v["status"] for v in rows], ["unqualified"], rows)

    def test_the_planted_stream_row_is_caught(self):
        """P2.2's `_check_row_line`: the refusal streamed as its verdict."""
        verdict = Verdict(gate=NEVER_GATE, passed=False, unqualified="known-good:fail")
        row = types.SimpleNamespace(verdict=verdict, cached=False, admission=None)
        self.assertIsNotNone(CRASH_WORDS.search(verdict.render()),
                             "the planted row: the verdict's own render is a crash's")
        line = report_mod.verdict_line(verdict)
        self.assertIsNone(CRASH_WORDS.search(line or ""), line)
        del row

    def test_a_crash_in_a_qualified_evaluators_live_run_still_reads_errored(self):
        verdict = Verdict(gate="g.x", passed=False, error="ZeroDivisionError: boom")
        self.assertTrue(report_mod.verdict_line(verdict).startswith("[ERR ] g.x"))
