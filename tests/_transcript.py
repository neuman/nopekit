# SPDX-License-Identifier: Apache-2.0
"""Phase 1's target transcript, as data: the steps, the checkpoint that delivers
each, and the shapes every line must have.

A transcript nobody replays is a promise. The one in ``docs/plan/phase-1.md`` is
what a human sees on a fresh clone of the bracket once Phase 1 has landed; this
module is that transcript written so ``tests/test_fresh_clone.py`` can replay it
in a git-initialised copy, and so ``tests/test_shapes.py`` can hold every output
line to the shape the plan promised (R-11). It is a helper, not a test module:
nothing here is collected, and nothing here starts a process — the replay does
that, through ``_env``.

What slipped through while the transcript was first written, and why the data
looks the way it does:

* **A step replayed before its checkpoint lands fails for nothing.** The
  transcript is Phase 1's END state; at checkpoint 1.1 there is no cache, so
  ``0 executed, 6 cached`` cannot be true yet. Each step therefore carries the
  ``tag`` of the checkpoint that delivers it, and the replay runs exactly the
  steps with ``tag <= CURRENT``. A step is never skipped to go green: it is
  either not yet due, or asserted.
* **Transcripts edit the tree on purpose,** so "the tree is clean afterwards"
  could never hold as first written. The edit is reverted by an explicit step,
  and cleanliness is asserted only where the plan claims it: after the
  non-editing prefix and after that revert.
* **A clean status and ``cached`` depend on the interpreter.** They hold only
  when the committed cache entries were written by the spine that is running;
  on an interpreter whose canonical AST walk differs, every entry is stale and
  re-executes to the same outcome — honest, but neither cached nor clean. Those
  expectations are marked ``starred`` (the plan's ``*``) and are asserted only
  when every committed entry's spine equals ``verdicts.spine_digest()`` (PLAN
  G6). That is a plain conditional in the replay, never ``skipTest``: a skip
  would read as a pass somewhere, and ``BracketCacheIsCurrent`` (1.3) is the
  unconditional check that the committed cache is current.
* **In the copy the bracket IS the repository root,** so porcelain lines carry
  no ``examples/bracket/`` prefix — the in-repo transcript shows one because it
  runs in the atompipe checkout. ``test_fresh_clone`` refuses any expectation
  that names the prefix.

Run:  PYTHONPATH=src python3 -m unittest discover -s tests -p test_fresh_clone.py -v
"""
from __future__ import annotations

import json
import os
import re
import xml.etree.ElementTree as ET
from typing import Any, NamedTuple, Optional, Sequence, Tuple, Union

Tag = Tuple[int, int]

#: The checkpoints delivered so far, in order. A step's tag is one of these. P2.1
#: joins with the steps its statuses and words change (GLOSSARY §3, §9).
CHECKPOINTS: Tuple[Tag, ...] = ((1, 1), (1, 2), (1, 3), (2, 1))

#: The checkpoint this commit has delivered: the replay runs every step whose tag
#: is at or before it. Why (1, 3): checkpoint 1.3 is what landed — records as
#: files, the generated index, the migration, and the bracket migrated with its
#: six verdict entries and six control entries committed at the final spine
#: (U32), so a fresh clone's first check is all cache hits and leaves the tree
#: clean, and `why` reads the model's PARAMS. Every step of the transcript is now
#: replayed; the starred ones still only on the spine that wrote the committed
#: cache (G6), and `BracketCacheIsCurrent` fails, unconditionally, when that is
#: not the running one. It moved once per checkpoint, in the unit that closed
#: it: (1, 1) in U10, (1, 2) in U25, (1, 3) in U32 (spec §6.2, G6). *Rejected:*
#: setting it to (1, 3) early and letting the undelivered steps skip — every
#: skip reads green, and the transcript would be asserted nowhere until the last
#: wave; *rejected:* replaying only in the last wave (a judge's defect against
#: the first design) — a checkpoint that breaks a step already delivered would go
#: unseen for thirteen waves.
#: (2, 1): P2.1 moved C6 — an assumption nobody owns — to Gap, and every word a
#: status line says to GLOSSARY's; the steps it adds are tagged with it, and the
#: Phase 1 steps it changed in words were edited in place (R-6, named in its
#: commit: the BLOCKING count 2 -> 3, C6's row, C7's reason, `invalidated:`,
#: `verdicts current`, `last check run:`).
CURRENT: Tag = (2, 1)

#: Where `check --junit` writes when given no path, spelled as the transcript
#: spells it rather than read from `report.JUNIT_DEFAULT`: an expectation taken
#: from the code under test agrees with that code by construction.
JUNIT_PATH = ".atompipe/out/junit.xml"

#: Where the per-gate cache lives in a project (spec §3.7). The revert step
#: deletes what appeared here after the edit; the spine rule reads what was
#: committed here.
VERDICTS_DIR = ".atompipe/verdicts"


# --------------------------------------------------------------------------- #
# Shapes (spec §3.13, R-11). Every pattern is anchored and matched against ONE
# line with `fullmatch`; `re.M` over a whole output works too. Tags are matched
# by width, not by vocabulary: the claim and verdict vocabularies grow in P2
# ("unknown"), and a shape that enumerated them would break on a word, not on a
# shape.
# --------------------------------------------------------------------------- #
#: One gate row as `Verdict.render` prints it: `[FAIL] bracket.deflection : …`.
#: A cached row matches this too; `CACHED_ROW` is the stricter reading.
ROW = re.compile(r"^\[(?P<tag>.{4})\] (?P<gate>\S+)(?: : (?P<body>.*))?$")

#: A cached, non-ok row: `f"{line:<77} cached"` (spec §7: `cached` from column
#: 79). The shape pins `\s+cached$`, never the padding width, so a row longer
#: than the column still reads as cached. *Rejected:* a tab (renders per
#: terminal); a changed tag (breaks every grep for `[FAIL]`).
CACHED_ROW = re.compile(r"^(?P<row>\[(?P<tag>.{4})\] (?P<gate>\S+) : (?P<body>.*?))\s+cached$")

#: `check`'s summary. `, F FAIL`, `, S skipped`, `, R errored`, `, U
#: unqualified` and `, O outside operating context` appear only when non-zero
#: (the spec prints the bracket's case; today's summary omits a zero FAIL count,
#: and nothing asks for that to change). Time and the model hash left this line
#: in 1.2; `unqualified` joined it in P2.1's review, and this shape in P2.3 (R-6:
#: it is counted); `outside operating context` in P2.4 (R-6 again: a pass outside
#: its evaluator's operating context is counted apart, never `unqualified`).
CHECK_SUMMARY = re.compile(
    r"^(?P<gates>\d+) gates: (?P<executed>\d+) executed, (?P<cached>\d+) cached"
    r" — (?P<ok>\d+) ok(?:, (?P<failed>\d+) FAIL)?(?:, (?P<skipped>\d+) skipped)?"
    r"(?:, (?P<errored>\d+) errored)?(?:, (?P<unqualified>\d+) unqualified)?"
    r"(?:, (?P<outside>\d+) outside operating context)?"
    r" — tier (?P<tier>\d+)$")

#: `check`'s line for a pass outside its evaluator's operating context (P2.4),
#: after the qualification lines: the evaluator, the key, what it holds now and
#: the range it was qualified on.
CONTEXT_LINE = re.compile(
    r"^(?P<gate>[^\s\[]\S*) : outside operating context: (?P<key>\S+) "
    r"(?:= \S+(?: \(not a number\))?|absent|not read by the run that passed), "
    r"qualified on (?P<interval>[\[(]\S+, \S+[\])])$")

#: `check`'s warning for an evaluator whose own limit is not its claim's (P2.4,
#: S-35): one line per compared pair, after the qualification lines.
LIMIT_WARNING = re.compile(
    r"^warning: (?P<gate>\S+) : its limit (?P<limit>\S+(?: \S+)?) is not "
    r"(?P<claim>\S+)'s acceptance condition \((?P<condition>.+)\) — one number in two "
    r"places$")

#: `check`'s controls line, printed only when a control ran or was re-qualified
#: by its values — in GLOSSARY §9's words from P2.3 (R-6, words only: `N
#: executed, N cached, N re-verified` before).
CONTROLS_SUMMARY = re.compile(
    r"^controls: (?P<run>\d+) run, (?P<preserved>\d+) preserved, "
    r"(?P<requalified>\d+) re-qualified$")

#: The head of `check`'s blocking list.
BLOCKING_HEAD = re.compile(
    r"^BLOCKING — (?P<count>\d+) critical claim\(s\) must not be spent against:$")

#: One blocking claim: `report.status_tag` (five wide), the id, the statement, and
#: the reason after the LAST ` — ` that follows the statement. The `gap --propose`
#: suffix is gone from 1.2 on; nothing here requires or forbids it.
BLOCKING_ROW = re.compile(
    r"^\[(?P<tag>.{5})\] (?P<claim>\S+) (?P<statement>.+?) — (?P<reason>.+)$")

#: The `(N verdicts current[, n unrun])` suffix `status` puts on the last line of
#: its invalidated block (GLOSSARY §6: a moved verdict is *invalidated*, a gate
#: with none *unrun*; P2.1, R-6 words only — `checks current`, `never run` before).
_COUNTS = r"   \((?P<current>\d+) verdicts current(?:, (?P<never>\d+) unrun)?\)"

#: `status`: the first invalidated gate, with the counts when it is the only one.
STALE_LINE = re.compile(rf"^invalidated: (?P<gate>\S+) — (?P<reasons>.+?)(?:{_COUNTS})?$")

#: `status`: each further invalidated gate, indented under the head (13 spaces).
STALE_MORE = re.compile(rf"^             (?P<gate>\S+) — (?P<reasons>.+?)(?:{_COUNTS})?$")

#: `status` when nothing is invalidated.
STALE_NONE = re.compile(rf"^invalidated: none{_COUNTS}$")

#: `status`: when the last check run was, from `last_check.json`, with its age —
#: or never (GLOSSARY §6: one invocation of `check` is a *check run*).
LAST_CHECK = re.compile(
    r"^last check run: (?:never|(?P<when>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)"
    r" \((?P<age>[^()]+) ago\))$")

#: `status`: an instrument recorded under another library version (Q1.3).
NOTE_INSTRUMENT = re.compile(
    r"^note: (?P<gate>\S+) — recorded under (?P<module>\S+) (?P<recorded>\S+); "
    r"here (?P<here>\S+)$")

#: `status`: controls whose fixture closure moved since they were demonstrated —
#: in GLOSSARY §9's words from P2.1 ("pending" was Open's Never-say on the screen
#: that lists the claims).
NOTE_PENDING = re.compile(
    r"^note: (?P<count>\d+) evaluator\(s\) to re-qualify — control inputs moved "
    r"\((?P<files>.+)\); the next check run re-qualifies them$")

#: `status` when the model entry does not load.
MODEL_BROKEN = re.compile(r"^model: (?P<entry>\S+) DOES NOT LOAD — (?P<error>.+)$")


#: One evaluator's qualification line (P2.3, GLOSSARY §6 *reject*): each
#: control's outcome in outcome words — `not run` for a known-good control that
#: does not exist, `reads the candidate` for one handed the live design — then,
#: for an evaluator walked by the mutation pass, its tally or why it made none,
#: then a control-level fact, then the decision. Its own negative control lives
#: in `test_shapes` (a line missing `→ …`, `0/0`, the walkthrough's `ok`,
#: `rejected`, `flipped`, a fourth segment, an outcome tag before the id).
QUALIFICATION_LINE = re.compile(
    r"^(?P<id>[^\s\[]\S*) : known-good (?P<good>pass|fail|skipped|errored|not run|reads the candidate"
    r"|outside its operating context)"
    r" · known-bad (?P<bad>pass|fail(?: \(raised, as declared\))?|skipped|errored)"
    r"(?: · channels differ| · known-good and known-bad via ctx\.extra)?"
    r"(?: · mutation (?:(?P<fails>\d+)/(?P<conclusive>[1-9]\d*) fail|0 conclusive)"
    r"(?: \((?P<inconclusive>\d+) inconclusive\)| \(none made: [^()]+\))?"
    r"| · mutation could not (?:run|finish)| · mutation errored)?"
    r"(?: · check run reads another ledger)?"
    r"(?: · value moves with its goalpost)?"
    r"(?: · two outcomes| · outcomes differ by tier| · outcome differs from its cached entry)?"
    r" → (?P<verdict>qualified|unqualified)$")

#: `gate show`'s `qualification:` row (it replaced `last selftest:`), one shape
#: per state: the line without its id (with the control entry's rho to 12
#: places where there is one), due to re-qualify, or not yet qualified.
QUALIFICATION_SHOW = re.compile(
    r"^  qualification: (?:(?P<line>known-good .+ → (?:qualified|unqualified))"
    r"(?: \(control (?P<control>[0-9a-f]{12})\))?"
    r"|due to re-qualify — .+ moved; the next check run re-qualifies it"
    r"|not yet qualified at this version — .+)$")

#: `gate show`'s detail rows under `qualification:`.
QUALIFICATION_DETAIL = re.compile(
    r"^    (?P<what>known-good|known-bad|mutation|not mutated|why|goalpost) +(?P<body>\S.*)$")

#: `gate selftest`'s summary, both modes, in qualification's words (P2.3; R-6,
#: words only: `N control(s) in T: F fired, B BROKEN, S skipped (tooling)`
#: before). `\S+` for the time: `human_duration` renders one token below a minute.
QUALIFIED_SUMMARY = re.compile(
    r"^(?P<evaluators>\d+) evaluators? in (?P<time>\S+): (?P<qualified>\d+) qualified, "
    r"(?P<unqualified>\d+) unqualified, (?P<skipped>\d+) skipped$")

#: Pack mode's row per pack.
PACK_ROW = re.compile(
    r"^(?P<pack>\S+) \((?P<origin>project|user|path|bundled)\) : (?P<qualified>\d+) qualified"
    r"(?:, (?P<unqualified>\d+) unqualified)?(?:, (?P<skipped>\d+) skipped)?$")

#: `check`'s controls line, in GLOSSARY §9's words (P2.3): ``CONTROLS_SUMMARY``.
QUALIFIED_CONTROLS = CONTROLS_SUMMARY

#: Pack mode's head of the failed-baseline rows.
BASELINES_FAILED = re.compile(r"^(?P<count>\d+) baseline\(s\) failed:$")

#: `why <param>`: the model's current value and where it lives, then what lost.
WHY_PARAM = re.compile(r"^param (?P<name>\S+) = (?P<value>.+?)   \((?P<source>.+)\)$")
WHY_REJECTED_HEAD = re.compile(r"^REJECTED \((?P<count>\d+)\)$")
WHY_REJECTED_ROW = re.compile(r"^  (?P<value>.+?) — (?P<why>.+?)   \((?P<source>.+)\)$")

#: A verdict entry and a control entry, as project-relative paths (spec §3.7-3.8).
ENTRY_PATH = re.compile(
    r"^\.atompipe/verdicts/(?P<gate>[^/]+)/(?P<rho>[0-9a-f]{16})-(?P<out>[0-9a-f]{8})\.json$")
CONTROL_ENTRY_PATH = re.compile(
    r"^\.atompipe/verdicts/(?P<gate>[^/]+)/control-(?P<rho>[0-9a-f]{16})-"
    r"(?P<out>[0-9a-f]{8})\.json$")

#: One `git status --porcelain` line (v1): two status columns, a space, the path.
PORCELAIN_LINE = re.compile(r"^(?P<xy>[ MTADRCU?!]{2}) (?P<path>.+)$")

#: Every shape by name, for `test_shapes` to iterate.
SHAPES = {
    "ROW": ROW, "CACHED_ROW": CACHED_ROW, "CHECK_SUMMARY": CHECK_SUMMARY,
    "CONTROLS_SUMMARY": CONTROLS_SUMMARY, "BLOCKING_HEAD": BLOCKING_HEAD,
    "BLOCKING_ROW": BLOCKING_ROW, "STALE_LINE": STALE_LINE, "STALE_MORE": STALE_MORE,
    "STALE_NONE": STALE_NONE, "LAST_CHECK": LAST_CHECK,
    "NOTE_INSTRUMENT": NOTE_INSTRUMENT, "NOTE_PENDING": NOTE_PENDING,
    "MODEL_BROKEN": MODEL_BROKEN, "BASELINES_FAILED": BASELINES_FAILED,
    "QUALIFICATION_LINE": QUALIFICATION_LINE, "QUALIFICATION_SHOW": QUALIFICATION_SHOW,
    "QUALIFICATION_DETAIL": QUALIFICATION_DETAIL, "QUALIFIED_SUMMARY": QUALIFIED_SUMMARY,
    "PACK_ROW": PACK_ROW, "QUALIFIED_CONTROLS": QUALIFIED_CONTROLS,
    "WHY_PARAM": WHY_PARAM, "WHY_REJECTED_HEAD": WHY_REJECTED_HEAD,
    "WHY_REJECTED_ROW": WHY_REJECTED_ROW, "ENTRY_PATH": ENTRY_PATH,
    "CONTROL_ENTRY_PATH": CONTROL_ENTRY_PATH, "PORCELAIN_LINE": PORCELAIN_LINE,
}


# --------------------------------------------------------------------------- #
# Actions: what a step does. The replay (`test_fresh_clone`) executes them.
# --------------------------------------------------------------------------- #
class Atompipe(NamedTuple):
    """`atompipe <argv>` in the project copy, or (``where="empty"``) in a fresh
    empty directory that is no project at all — pack mode."""
    argv: Tuple[str, ...]
    where: str = "project"

    def __str__(self) -> str:
        suffix = "   (in an empty directory)" if self.where == "empty" else ""
        return "atompipe " + " ".join(self.argv) + suffix


class Git(NamedTuple):
    """`git <argv>` in the project copy."""
    argv: Tuple[str, ...]

    def __str__(self) -> str:
        return "git " + " ".join(self.argv)


class SameRun(NamedTuple):
    """No new command: the step reads the previous command's result again. The
    first check is ONE run in the transcript; three steps read it, each from the
    checkpoint that makes its reading true."""

    def __str__(self) -> str:
        return "(same run)"


class Edit(NamedTuple):
    """Replace ``old`` with ``new`` in ``path`` — exactly one occurrence, or the
    replay fails before running anything against a model it did not mean to edit."""
    path: str
    old: str
    new: str

    def __str__(self) -> str:
        return f"edit {self.path}: {self.old!r} -> {self.new!r}"


class Restore(NamedTuple):
    """Restore ``path`` from the commit, and delete every file that appeared
    under ``.atompipe/verdicts`` since step ``since`` began: the new evidence
    the edit caused, and nothing the first check wrote before it."""
    path: str
    since: str

    def __str__(self) -> str:
        return f"restore {self.path}; delete the entries written since {self.since}"


class Seq(NamedTuple):
    """Several actions; the step reads the result of the last command among them."""
    actions: Tuple[Any, ...]

    def __str__(self) -> str:
        return "; ".join(str(a) for a in self.actions)


Action = Union[Atompipe, Git, SameRun, Edit, Restore, Seq]

SAME_RUN = SameRun()
CHECK = Atompipe(("check",))
PORCELAIN = Git(("status", "--porcelain"))


# --------------------------------------------------------------------------- #
# Expectations: what a step asserts. Pure functions of a `Result`.
# --------------------------------------------------------------------------- #
class Result(NamedTuple):
    """What one command left behind: its exit code, its output, where it ran."""
    command: str
    returncode: int
    stdout: str
    stderr: str
    cwd: str


class Expect(NamedTuple):
    """One assertion on a step's result.

    ``kind`` is one of ``exit``, ``line`` (some line matches), ``last-line``,
    ``in-order`` (each pattern matches a later line than the one before),
    ``exactly`` (the output's lines and the patterns match one to one — the
    porcelain assertion; no patterns means no output), ``no-line-containing``,
    and ``junit`` (the file at ``value`` under the command's directory parses as
    XML whose root is ``testsuites``). ``at`` is the checkpoint the expectation
    is due from when it is later than its step's (``None``: the step's own tag);
    ``starred`` is the plan's ``*`` — asserted only when every committed entry's
    spine is the running spine.
    """
    kind: str
    value: Any
    at: Optional[Tag] = None
    starred: bool = False


def exit_code(code: int) -> Expect:
    return Expect("exit", code)


def line(pattern: str) -> Expect:
    return Expect("line", pattern)


def last_line(pattern: str) -> Expect:
    return Expect("last-line", pattern)


def in_order(*patterns: str) -> Expect:
    return Expect("in-order", tuple(patterns))


def exactly(*patterns: str) -> Expect:
    return Expect("exactly", tuple(patterns))


def no_line_containing(text: str) -> Expect:
    return Expect("no-line-containing", text)


def junit(path: str = JUNIT_PATH, root: str = "testsuites") -> Expect:
    return Expect("junit", (path, root))


def starred(*expects: Expect, at: Optional[Tag] = None) -> Tuple[Expect, ...]:
    """The plan's ``*``: these hold only on the spine that wrote the committed cache."""
    return tuple(e._replace(starred=True, at=at if at is not None else e.at) for e in expects)


class Step(NamedTuple):
    """One transcript step: ``(id, tag, action, expect)`` (spec §4, U10)."""
    id: str
    tag: Tag
    action: Action
    expect: Tuple[Expect, ...]


# The bracket's lines, as the transcript prints them. Kept apart from the table so
# a step and a shape test can name the same line.
_C1 = (r"^\[FAIL \] C1 Tip sags no more than 0\.5 mm at rated load — "
       r"bracket\.deflection : 0\.700 mm at 15 N \(limit 0\.5 mm\)$")
_C6 = (r"^\[gap  \] C6 The load is static and centred on the arm — no owner recorded$")
_C7 = (r"^\[gap  \] C7 First mode is clear of the pump that sits on the shelf — "
       r"no evaluator$")

#: The phase-1 target transcript (docs/plan/phase-1.md), in order. The table in
#: spec §4 (U10) is binding: a step leaves it only by an edit a reviewer sees,
#: which `TranscriptIsWellFormed` makes explicit.
STEPS: Tuple[Step, ...] = (
    Step("first-check-exit", (1, 1), CHECK, (exit_code(1),)),
    Step("first-check-shape", (1, 2), SAME_RUN, (
        exit_code(1),
        line(r"^6 gates: \d+ executed, \d+ cached — 5 ok, 1 FAIL — tier 0$"),
        line(r"^BLOCKING — 3 critical claim\(s\) must not be spent against:$"),
        line(_C1),
        line(_C6),
        line(_C7),
    )),
    Step("first-check-cached", (1, 3), SAME_RUN, starred(
        line(r"^6 gates: 0 executed, 6 cached — 5 ok, 1 FAIL — tier 0$"),
        line(r"^\[FAIL\] bracket\.deflection : 0\.700 mm at 15 N \(limit 0\.5 mm\)\s+cached$"),
    )),
    Step("clean-after-check", (1, 3), PORCELAIN, starred(exactly())),
    # P2.1: what a person reads after the first check, in GLOSSARY §3's words —
    # the readiness sentence naming every unresolved required claim, the count
    # line, C6 a Gap with its reason, C5 waiting on an article.
    Step("status-words", (2, 1), Atompipe(("status",)), (
        line(r"^v0\.1 is NOT ready: 4 of 7 required claims are unresolved — 1 failing \(C1\); "
             r"2 gaps \(C6, C7\); 1 pending build \(C5\)\. 3 of 7 claims are checked against "
             r"the current inputs\. Pending build: 1 claim needs an article \(C5\)\.$"),
        line(r"^7 claims · 3 checked · 1 failing · 2 gaps · 1 pending build$"),
        line(r"^\[gap  \] C6 The load is static and centred on the arm — no owner recorded$"),
        line(r"^\[build\] C5 .+ — needs an article; no test written down$"),
        line(r"^invalidated: none   \(6 verdicts current\)$"),
    )),
    Step("edit-bed-xy", (1, 2),
         Edit("model/bracket.py", "bed_xy: float = 220.0", "bed_xy: float = 250.0"), ()),
    Step("status-stale", (1, 2), Atompipe(("status",)), (
        line(r"^invalidated: bracket\.bed_fit — config\.bed_xy 220\.0 -> 250\.0   "
             r"\(5 verdicts current\)$"),
    )),
    Step("check-after-edit", (1, 2), CHECK, (
        exit_code(1),
        line(r"^\[ok  \] bracket\.bed_fit : 74 x 30 x 7 mm vs 234 mm usable "
             r"\(250 bed - 2x8 brim\)$"),
        line(r"^6 gates: 1 executed, 5 cached — 5 ok, 1 FAIL — tier 0$"),
        # P2.4 (S-35): the evaluator now judges 234 mm while C4 still says 204 —
        # one number in two places, said once, and C4 stays Checked (73.5 <= 204).
        line(r"^warning: bracket\.bed_fit : its limit 234 mm is not C4's acceptance "
             r"condition \(bed fit <= 204\.0 mm\) — one number in two places$"),
    )),
    Step("porcelain-after-edit", (1, 3), PORCELAIN, (
        exactly(r"^ M model/bracket\.py$",
                r"^\?\? \.atompipe/verdicts/bracket\.bed_fit/[0-9a-f]{16}-[0-9a-f]{8}\.json$"),
    )),
    Step("why-thickness", (1, 3), Atompipe(("why", "thickness")), (
        in_order(r"^param thickness = 7\.0 mm   \(model/bracket\.py Config\.thickness\)$",
                 r"^REJECTED \(1\)$",
                 r"^  4\.0 mm — 3\.75 mm deflection, 7\.5x the limit   "
                 r"\(model/bracket\.py PARAMS\)$"),
    )),
    # P2.3 (R-6, D18's words; the ids kept, D23): `qualification:` replaced
    # `last selftest:` and its detail rows follow it, and pack mode's summary
    # counts evaluators qualified — every one of them, or it exits 1.
    Step("gate-show-last-selftest", (1, 2), Atompipe(("gate", "show", "bracket.deflection")), (
        line(r"^  qualification: known-good pass · known-bad fail · mutation 1/1 fail "
             r"→ qualified \(control [0-9a-f]{12}\)$"),
    )),
    Step("pack-mode-selftest", (1, 1), Atompipe(("gate", "selftest"), where="empty"), (
        exit_code(0),
        last_line(r"^\d+ evaluators in \S+: \d+ qualified, 0 unqualified, \d+ skipped$"),
    )),
    Step("check-junit", (1, 1), Atompipe(("check", "--junit")), (
        exit_code(1),
        junit(JUNIT_PATH, "testsuites"),
    )),
    Step("revert", (1, 2), Seq((Restore("model/bracket.py", since="edit-bed-xy"), PORCELAIN)),
         starred(exactly(), at=(1, 3))),
    Step("no-bytecode-shown", (1, 3), Seq((CHECK, Atompipe(("gate", "selftest")), PORCELAIN)), (
        no_line_containing("__pycache__/"),
    )),
)


def due(step: Step, current: Optional[Tag] = None) -> bool:
    """Whether ``step`` is replayed at checkpoint ``current`` (default: ``CURRENT``,
    read at call time — a default bound at definition would not follow it)."""
    current = CURRENT if current is None else current
    return tuple(step.tag) <= tuple(current)


def reached(expect: Expect, step: Step, current: Optional[Tag] = None) -> bool:
    """Whether ``expect`` is asserted at ``current`` (the star aside)."""
    current = CURRENT if current is None else current
    at = expect.at if expect.at is not None else step.tag
    return due(step, current) and tuple(at) <= tuple(current)


def _lines(text: str) -> list:
    """The output's lines, as `tail`/`grep` see them. Never stripped: a porcelain
    line starts with a space (` M model/bracket.py`) that carries meaning."""
    return text.splitlines()


def problem(expect: Expect, result: Optional[Result]) -> Optional[str]:
    """Why ``result`` does not meet ``expect``, or None when it does."""
    if result is None:
        return f"{expect.kind}: no command ran in this step"
    lines = _lines(result.stdout)
    kind, value = expect.kind, expect.value
    if kind == "exit":
        if result.returncode != value:
            return f"exit {result.returncode}, expected {value}"
        return None
    if kind == "line":
        pattern = re.compile(value)
        if not any(pattern.fullmatch(text) for text in lines):
            return f"no line matches {value}"
        return None
    if kind == "last-line":
        last = lines[-1] if lines else ""
        if not re.fullmatch(value, last):
            return f"last line {last!r} does not match {value}"
        return None
    if kind == "in-order":
        at = 0
        for pattern in value:
            compiled = re.compile(pattern)
            while at < len(lines) and not compiled.fullmatch(lines[at]):
                at += 1
            if at == len(lines):
                return f"no line matches {pattern} after the ones before it"
            at += 1
        return None
    if kind == "exactly":
        got = [text for text in lines if text != ""]
        patterns = [re.compile(p) for p in value]
        unmatched = [text for text in got if not any(p.fullmatch(text) for p in patterns)]
        counts = {p.pattern: sum(1 for text in got if p.fullmatch(text)) for p in patterns}
        wrong = {p: n for p, n in counts.items() if n != 1}
        if unmatched or wrong or len(got) != len(patterns):
            return (f"expected exactly {list(value) or 'no lines'}; got {got or 'no lines'}"
                    + (f"; unexpected {unmatched}" if unmatched else "")
                    + (f"; match counts {wrong}" if wrong else ""))
        return None
    if kind == "no-line-containing":
        hits = [text for text in lines if value in text]
        if hits:
            return f"{value!r} appears: {hits}"
        return None
    if kind == "junit":
        path, root_tag = value
        full = os.path.join(result.cwd, *path.split("/"))
        if not os.path.isfile(full):
            return f"{path} was not written"
        try:
            root = ET.parse(full).getroot()
        except ET.ParseError as exc:
            return f"{path} does not parse: {exc}"
        if root.tag != root_tag:
            return f"{path}: root <{root.tag}>, expected <{root_tag}>"
        return None
    return f"unknown expectation kind {kind!r}"


# --------------------------------------------------------------------------- #
# The spine rule for starred expectations (PLAN G6).
# --------------------------------------------------------------------------- #
def entry_spine(data: dict) -> Optional[str]:
    """The spine digest a committed entry was written under: a verdict entry's
    ``spine``, a control entry's ``static_parts.spine`` (spec §3.7-3.8)."""
    if data.get("kind") == "control":
        parts = data.get("static_parts")
        return parts.get("spine") if isinstance(parts, dict) else None
    spine = data.get("spine")
    return spine if isinstance(spine, str) else None


def read_spines(project: str, paths: Sequence[str]) -> list:
    """The spine of each committed entry under ``project``. A committed entry that
    does not parse is an error here, not a mismatch: a mismatch quietly stops
    the starred expectations from being asserted, and a corrupt cache must not
    buy that silence."""
    spines = []
    for rel in paths:
        if not rel.endswith(".json"):
            continue
        with open(os.path.join(project, *rel.split("/")), "r", encoding="utf-8") as fh:
            try:
                data = json.load(fh)
            except ValueError as exc:
                raise AssertionError(f"committed entry {rel} does not parse: {exc}") from exc
        if not isinstance(data, dict):
            raise AssertionError(f"committed entry {rel} is not a JSON object")
        spines.append(entry_spine(data))
    return spines


def spine_is_current(spines: Sequence[Optional[str]]) -> bool:
    """Whether every committed entry was written by the running spine.

    Vacuously true with no committed entry: a starred expectation that is due
    and finds no cache is asserted, and fails, instead of passing unasserted.
    Reached only once a starred step is due — at 1.3, when ``verdicts`` exists;
    reaching it without one is a broken checkpoint, and says so.
    """
    try:
        from atompipe import verdicts
        running = verdicts.spine_digest()
    except (ImportError, AttributeError) as exc:
        raise AssertionError(
            "a starred transcript step is due, but atompipe.verdicts.spine_digest() "
            f"is not available: {exc}") from exc
    return all(spine == running for spine in spines)
