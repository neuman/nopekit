# SPDX-License-Identifier: Apache-2.0
"""Every human-facing output is a fixed shape, pinned, with its own negative control (R-11).

A shape nothing pins drifts into prose, and prose is where a fact turns into an
opinion: a `status` that grows a friendly summary line, a `check` whose cached
FAIL stops saying `cached`, a `last check:` that loses its age. So each command
below has one matcher — a pure function of the command's stdout that returns
what is wrong with it, line by line — built from the shapes in
`tests/_transcript.py` (spec §3.13), and each matcher is run twice:

* on a REAL transcript: a copy of the bracket, driven through the transcript's
  own steps (`check` twice, the `bed_xy` edit, `status`, `check`, `why thickness`,
  `gate show`, `gate selftest`, and last `why thickness` on a model that no longer
  loads), which it must accept; and
* on MUTATED copies of that transcript — a line added, `cached` removed, the age
  removed, the control id removed, the time removed — each of which it must
  refuse. A matcher that accepts its mutant is a logger.

The matchers pin line ORDER and line COUNT, not only a regex per line: an added
line is refused wherever it lands, and the counts a summary line states must
agree with the rows printed above it (a cached FAIL row that lost `cached` makes
"0 executed" disagree with one uncached row). Where `_transcript` has no shape —
the lines `render_terminal` prints above `status`'s fixed block, `gate show`'s
body — the shape is written here, next to the matcher that uses it.

What slipped through without it: the shapes existed as regexes in
`_transcript.py` and as single-line asserts in `test_check_cache`; nothing held a
whole output to them, so a line inserted between the summary and the BLOCKING
list, or a status line that dropped its age, stayed green everywhere.

`TranscriptShapes` (U32) holds the rest of the plan's transcript, which only a
clone of the MIGRATED bracket can print — the copy above is the bracket before
its migration, so its first check runs everything: the fresh clone's first
check line for line (five lines, all cached), `git status --porcelain` empty
after it and exactly the edit plus one new entry after the re-check (never a
control entry, never bytecode), the re-check's controls line (six re-verified,
none executed), pack mode's selftest, and `check --junit`.

Run:  PYTHONPATH=src python3 -m unittest tests.test_shapes -v
"""
from __future__ import annotations

import os
import re
import shutil
import tempfile
import unittest
from typing import Callable

import _env
import _projects
import _transcript as T

# --------------------------------------------------------------------------- #
# the lines `_transcript` does not pin
# --------------------------------------------------------------------------- #
#: `check`'s skip digest after the summary: one reason for several gates, the
#: gate ids wrapped under it at seven spaces — or a single skip as a plain row.
SKIP_GROUP = re.compile(r"^\[skip\] (?P<count>\d+) gates — (?P<reason>.+)$")
SKIP_ONE = re.compile(r"^\[skip\] (?P<gate>\S+) : (?P<reason>.+)$")
WRAPPED = re.compile(r"^       \S.*$")

#: `check`'s note on registered gates the sweep did not select.
CARRIED_NOTE = re.compile(
    r"^note: \d+ gate\(s\) outside this sweep keep their last verdict \(.+\)"
    r"(?: — \d+ of them invalidated)?$")

#: `check`'s last line when nothing critical blocks: `ready:` only when every
#: required claim reads Checked (GLOSSARY §4, P2.1), else what stands between
#: the project and ready; or the line for no claims at all. (R-6, P2.1: `ready:
#: no critical claim is blocking` said ready while a claim waited for an article.)
READY = re.compile(r"^(?:ready: every required claim is checked"
                   r"|nothing stops this check run — .+)$")
NO_CLAIMS = re.compile(r"^no claims recorded, so nothing was evaluated — .+$")

#: The readiness block `report.render_terminal` prints at the top of `status`, in
#: its order: the head, one sentence, the counts, the unsettled claims (and how
#: many more), the gaps, the gate tally, what constrains the project, `next:`.
STATUS_HEAD = (
    ("head", re.compile(r"^atompipe readiness — .+$"), 1, 1),
    ("sentence", re.compile(r"^\S.*$"), 1, 1),
    # GLOSSARY §9's count line (P2.1, R-6): `7 claims · 3 checked · 1 failing`,
    # Skipped with its crashes apart, `N skipped (k errored)`.
    ("counts", re.compile(r"^\d+ claims?(?: · \d+ [a-z ]+(?: \(\d+ errored\))?)*$"), 1, 1),
    ("claim", re.compile(r"^\[.{5}\] \S+ .+ — .+$"), 0, None),
    ("more claims", re.compile(r"^       \.\.\. and \d+ more unresolved claims — .+$"), 0, 1),
    ("gaps", re.compile(r"^gap records \d+:$"), 0, 1),
    ("gap", re.compile(r"^  \S+ .+ \(\S+\) — .+$"), 0, None),
    ("more gaps", re.compile(r"^  \.\.\. and \d+ more$"), 0, 1),
    ("gates", re.compile(r"^gates: .+$"), 0, 1),
    ("standing", re.compile(r"^standing: .+$"), 0, 1),
    ("next", re.compile(r"^next: .+$"), 1, 1),
)

#: What `status` may print after its fixed block, in this order (spec §3.13).
STATUS_TAIL = (
    ("packs", re.compile(r"^packs: .+$"), 0, 1),
    ("site", re.compile(r"^site: .+$"), 0, 1),
    ("unread", re.compile(r"^unread evidence: .+$"), 0, 1),
    ("undefended", re.compile(r"^undefended params: .+$"), 0, 1),
    ("problem", re.compile(r"^\[FAIL\] .+$"), 0, None),
)

#: `gate show`'s body, after `gates.describe`'s line and the description.
DESCRIBE = re.compile(r"^(?P<gate>\S+) +\[t(?P<tier>\d)(?: \S+)?\] +.+$")
SHOW_TIER = re.compile(r"^  tier (?P<tier>\d)  pack (?P<pack>\S+)  entry \S*$")
SHOW_CLAIMS = re.compile(r"^  claims: .+$")
SHOW_RUNNABLE = re.compile(r"^  runnable here: (?:yes|NO — .+)$")
#: P2.2: the prerequisite edge, both ways — each line only when it has an id, so
#: a gate with no edge prints neither and keeps the shape it had.
SHOW_PREREQUISITES = re.compile(r"^  prerequisites: \S+(?:, \S+)*(?: \(.+ not registered\))?$")
SHOW_PREREQUISITE_OF = re.compile(r"^  prerequisite of: \S+(?:, \S+)*$")
#: P2.4: a declared operating context, only when there is one — each key and its
#: range, then what it means.
SHOW_CONTEXT = re.compile(
    r"^  operating context: \S+ in [\[(]\S+, \S+[\])](?:, \S+ in [\[(]\S+, \S+[\])])* — "
    r"outside it a pass does not count; a fail still does$")
SHOW_CONTROL = re.compile(r"^  control: (?:\S+ \(must \w+\)|NONE — .+)$")
SHOW_NOTE = re.compile(r"^           \S.*$")
SHOW_VERDICT = re.compile(r"^  last verdict: (?:\[.{4}\] \S+.*|\(never run\))$")

#: `gate selftest` (project mode, P2.3): one qualification line per evaluator
#: (``T.QUALIFICATION_LINE``), or its skip — `<id> : skipped: <why>` — each
#: line followed by the isolation notes it has (P2.3-D20); then the summary.
#: (R-6, D18's words: a `[tag] <gate>#selftest` row per control, the refusal
#: head and one line per broken gate before.)
SELFTEST_SKIP = re.compile(r"^(?P<gate>\S+) : skipped: .+$")
SELFTEST_NOTE = re.compile(r"^note: \S+: control not isolated — .+$")
SELFTEST_EMPTY = re.compile(r"^(?:note: )?no control ran \(.+\).*$")


# --------------------------------------------------------------------------- #
# the matchers: stdout in, problems out ([] when the shape holds)
# --------------------------------------------------------------------------- #
def _count(pattern: re.Pattern, line: str, group: str) -> int:
    match = pattern.fullmatch(line)
    value = match.group(group) if match else None
    return int(value) if value else 0


def _grammar(lines: list[str], at: int, grammar: tuple, problems: list[str],
             where: str) -> int:
    """Consume ``lines`` from ``at`` by ``grammar`` — ``(name, pattern, min, max)``
    in order — and return where it stopped. A part seen fewer than ``min``
    times is a problem; ``max`` None is unbounded."""
    for name, pattern, low, high in grammar:
        seen = 0
        while at < len(lines) and (high is None or seen < high) \
                and pattern.fullmatch(lines[at]):
            at += 1
            seen += 1
        if seen < low:
            got = repr(lines[at]) if at < len(lines) else "the end of the output"
            problems.append(f"{where}: expected {name} at line {at + 1}, got {got}")
    return at


def _trailing(lines: list[str], at: int, problems: list[str], what: str) -> None:
    for index in range(at, len(lines)):
        problems.append(f"{what}: line {index + 1} fits no shape: {lines[index]!r}")


def check_problems(stdout: str) -> list[str]:
    """`check` text (spec §3.13): gate rows (executed as rows, cached non-ok rows
    ending in `cached`), the summary, the controls line, the skip digest, the
    carried-over note, then either the BLOCKING list or the ready line."""
    lines = stdout.splitlines()
    problems: list[str] = []
    at = 0
    rows = []
    while at < len(lines) and T.ROW.fullmatch(lines[at]):
        rows.append(lines[at])
        at += 1
    if at == len(lines) or not T.CHECK_SUMMARY.fullmatch(lines[at]):
        got = repr(lines[at]) if at < len(lines) else "the end of the output"
        return [f"check: expected the summary after {len(rows)} row(s), got {got}"]
    summary = T.CHECK_SUMMARY.fullmatch(lines[at])
    at += 1
    if at < len(lines) and T.CONTROLS_SUMMARY.fullmatch(lines[at]):
        at += 1
    # P2.3-D16: a qualification line per unqualified evaluator, and per walked
    # one whose qualification ran in this check run; P2.4: interleaved with the
    # line of a pass outside its evaluator's operating context, then the limit
    # warnings (one number in two places, S-35).
    while at < len(lines) and (T.QUALIFICATION_LINE.fullmatch(lines[at])
                               or T.CONTEXT_LINE.fullmatch(lines[at])):
        at += 1
    while at < len(lines) and T.LIMIT_WARNING.fullmatch(lines[at]):
        at += 1
    while at < len(lines) and (SKIP_GROUP.fullmatch(lines[at]) or SKIP_ONE.fullmatch(lines[at])):
        at += 1
        while at < len(lines) and WRAPPED.fullmatch(lines[at]):
            at += 1
    if at < len(lines) and CARRIED_NOTE.fullmatch(lines[at]):
        at += 1
    if at < len(lines) and T.BLOCKING_HEAD.fullmatch(lines[at]):
        want = int(T.BLOCKING_HEAD.fullmatch(lines[at]).group("count"))
        at += 1
        seen = 0
        while at < len(lines) and T.BLOCKING_ROW.fullmatch(lines[at]):
            at += 1
            seen += 1
        if seen != want:
            problems.append(f"check: BLOCKING says {want} claim(s), {seen} listed")
    elif at < len(lines) and (READY.fullmatch(lines[at]) or NO_CLAIMS.fullmatch(lines[at])):
        at += 1
    else:
        problems.append("check: no BLOCKING list and no ready line after the summary")
    _trailing(lines, at, problems, "check")

    # The rows above the summary must be the ones it counts.
    cached_rows = [line for line in rows if T.CACHED_ROW.fullmatch(line)]
    fresh_rows = [line for line in rows if not T.CACHED_ROW.fullmatch(line)]
    executed, cached = int(summary.group("executed")), int(summary.group("cached"))
    skipped = int(summary.group("skipped") or 0)
    # A pass outside its operating context ran and streams no row: its line
    # follows the summary (P2.4), as an unqualified evaluator's does.
    outside = int(summary.group("outside") or 0)
    if not executed - skipped - outside <= len(fresh_rows) <= executed:
        problems.append(f"check: the summary says {executed} executed ({skipped} skipped) "
                        f"but {len(fresh_rows)} row(s) print as executed")
    if len(cached_rows) > cached:
        problems.append(f"check: {len(cached_rows)} cached row(s) for {cached} cached")
    for line in cached_rows:
        if T.CACHED_ROW.fullmatch(line).group("tag") == "ok  ":
            problems.append(f"check: a cached pass is printed: {line!r}")
    total = sum(int(summary.group(k) or 0)
                for k in ("ok", "failed", "skipped", "errored", "unqualified", "outside"))
    if total != int(summary.group("gates")):
        problems.append(f"check: {summary.group(0)!r} does not add up")
    return problems


def status_problems(stdout: str) -> list[str]:
    """`status` text (spec §3.13): the readiness block, then — fixed order — the
    `stale:` block, `last check:`, the `note:` lines, `model:` when broken, then
    packs, site, unread evidence, undefended params and load problems."""
    lines = stdout.splitlines()
    problems: list[str] = []
    at = _grammar(lines, 0, STATUS_HEAD, problems, "status head")

    # stale: one block, the counts on its last line and nowhere else
    first = lines[at] if at < len(lines) else ""
    if T.STALE_NONE.fullmatch(first):
        at += 1
    elif T.STALE_LINE.fullmatch(first):
        block = [T.STALE_LINE.fullmatch(first)]
        at += 1
        while block[-1].group("current") is None and at < len(lines) \
                and T.STALE_MORE.fullmatch(lines[at]):
            block.append(T.STALE_MORE.fullmatch(lines[at]))
            at += 1
        if block[-1].group("current") is None:
            problems.append("status: the stale block ends without its counts")
    else:
        problems.append(f"status: expected the stale block at line {at + 1}, got {first!r}")

    if at < len(lines) and T.LAST_CHECK.fullmatch(lines[at]):
        at += 1
    else:
        got = repr(lines[at]) if at < len(lines) else "the end of the output"
        problems.append(f"status: expected `last check:` at line {at + 1}, got {got}")
    while at < len(lines) and T.NOTE_INSTRUMENT.fullmatch(lines[at]):
        at += 1
    if at < len(lines) and T.NOTE_PENDING.fullmatch(lines[at]):
        at += 1
    if at < len(lines) and T.MODEL_BROKEN.fullmatch(lines[at]):
        at += 1
    at = _grammar(lines, at, STATUS_TAIL, problems, "status tail")
    _trailing(lines, at, problems, "status")
    return problems


def gate_show_problems(stdout: str) -> list[str]:
    """`gate show` text: `describe`'s line, the description, tier/pack/entry,
    claims, runnable, the control (and its note), the last verdict, then the
    qualification and its detail rows, last (spec §3.13; P2.3-D18 — R-6, words:
    the last line was `last selftest:` before)."""
    lines = stdout.splitlines()
    problems: list[str] = []
    if not lines or not DESCRIBE.fullmatch(lines[0]):
        return [f"gate show: line 1 is not the gate's description line: "
                f"{lines[0] if lines else ''!r}"]
    at = 1
    while at < len(lines) and not SHOW_TIER.fullmatch(lines[at]):
        if lines[at] and not lines[at].startswith("  "):
            problems.append(f"gate show: line {at + 1} is not description text: {lines[at]!r}")
        at += 1
    body = (("tier", SHOW_TIER, 1, 1), ("claims", SHOW_CLAIMS, 1, 1),
            ("runnable", SHOW_RUNNABLE, 1, 1),
            ("prerequisites", SHOW_PREREQUISITES, 0, 1),
            ("prerequisite of", SHOW_PREREQUISITE_OF, 0, 1),
            ("operating context", SHOW_CONTEXT, 0, 1),
            ("control", SHOW_CONTROL, 1, 1),
            ("control note", SHOW_NOTE, 0, 1), ("last verdict", SHOW_VERDICT, 1, 1))
    at = _grammar(lines, at, body, problems, "gate show")
    shown = T.QUALIFICATION_SHOW.fullmatch(lines[at]) if at < len(lines) else None
    if shown:
        # A qualified evaluator stands on a control entry, and the row names it.
        if shown.group("line") and shown.group("line").endswith("→ qualified") \
                and not shown.group("control"):
            problems.append("gate show: a qualified evaluator's row names no control entry")
        at += 1
        while at < len(lines) and T.QUALIFICATION_DETAIL.fullmatch(lines[at]):
            at += 1
    else:
        got = repr(lines[at]) if at < len(lines) else "the end of the output"
        problems.append(f"gate show: expected `qualification:` at line {at + 1}, got {got}")
    # P2.5a (R-6, an addition): the evaluator's track record, last, always.
    if at < len(lines) and TRACK.fullmatch(lines[at]):
        at += 1
    else:
        got = repr(lines[at]) if at < len(lines) else "the end of the output"
        problems.append(f"gate show: expected `track record:` at line {at + 1}, got {got}")
    _trailing(lines, at, problems, "gate show")
    return problems


#: `why <param>`'s sections after the value line and its facts, in order: each
#: head preceded by one blank line. The counted ones carry their count.
WHY_SECTIONS = (
    ("WHY", re.compile(r"^WHY$")),
    ("REJECTED", T.WHY_REJECTED_HEAD),
    ("GATES", re.compile(r"^GATES \((?P<count>\d+)\)$")),
    ("GROUNDED BY", re.compile(r"^GROUNDED BY \((?P<count>\d+)\)$")),
    ("DECISIONS", re.compile(r"^DECISIONS \((?P<count>\d+), newest first\)$")),
)
#: The value line when the model does not hold a number: the name, and the
#: record's path when there is one — never a `=`.
WHY_NO_VALUE = re.compile(r"^param \S+(?:   \(\S+\))?$")
#: Every line under the value line and under a section head: indented.
WHY_BODY = re.compile(r"^  +\S.*$")
#: A row under REJECTED that is not a loser: none recorded, or a loser's evidence.
WHY_REJECTED_NONE = re.compile(r"^  \(none recorded.*\)$")
WHY_EVIDENCE = re.compile(r"^      evidence: .+$")
#: One artifact under GROUNDED BY, one decision under DECISIONS; the lines under
#: each are indented further.
WHY_ITEM = re.compile(r"^  - \S.*$")
#: What a section says when it has nothing to list, on one line.
WHY_EMPTY = re.compile(r"^  \(.+\)$")


def why_problems(stdout: str) -> list[str]:
    """`why <param>` text: the model's value and where it lives (or no number, when
    the model does not load), its facts, then WHY, REJECTED (n), GATES (n),
    GROUNDED BY (n), DECISIONS (n, newest first) — each after one blank line, every
    line under it indented. Every loser under REJECTED names the file it lives in,
    and each count is the rows'."""
    lines = stdout.splitlines()
    problems: list[str] = []
    numbered = bool(lines) and T.WHY_PARAM.fullmatch(lines[0]) is not None
    if not lines or not (numbered or WHY_NO_VALUE.fullmatch(lines[0])):
        return [f"why: line 1 is not the value line: {lines[0] if lines else ''!r}"]
    at = 1
    while at < len(lines) and lines[at] != "":
        if not WHY_BODY.fullmatch(lines[at]):
            problems.append(f"why: line {at + 1} is not a fact: {lines[at]!r}")
        if numbered and lines[at].startswith("  model does not load: "):
            problems.append("why: a number beside `model does not load`")
        at += 1
    for name, head in WHY_SECTIONS:
        if at < len(lines) and lines[at] == "":
            at += 1
        match = head.fullmatch(lines[at]) if at < len(lines) else None
        if match is None:
            got = repr(lines[at]) if at < len(lines) else "the end of the output"
            return problems + [f"why: expected {name} after one blank line, at line "
                               f"{at + 1}; got {got}"]
        at += 1
        body: list[str] = []
        while at < len(lines) and lines[at] != "":
            if not WHY_BODY.fullmatch(lines[at]):
                problems.append(f"why: line {at + 1} under {name} is not indented: "
                                f"{lines[at]!r}")
            body.append(lines[at])
            at += 1
        want = int(match.group("count")) if "count" in head.groupindex else None
        if not body:
            problems.append(f"why: {name} lists nothing and says nothing")
        elif want == 0 and not (len(body) == 1 and WHY_EMPTY.fullmatch(body[0])):
            problems.append(f"why: {name} (0) must say why in one line, got {body}")
        if name == "REJECTED":
            rows = [line for line in body if T.WHY_REJECTED_ROW.fullmatch(line)]
            problems += [f"why: a REJECTED row that names no home: {line!r}"
                         for line in body if not (T.WHY_REJECTED_ROW.fullmatch(line)
                                                  or WHY_REJECTED_NONE.fullmatch(line)
                                                  or WHY_EVIDENCE.fullmatch(line))]
            if len(rows) != want:
                problems.append(f"why: REJECTED says {want}, {len(rows)} row(s) listed")
        elif name in ("GROUNDED BY", "DECISIONS") and want:
            items = sum(1 for line in body if WHY_ITEM.fullmatch(line))
            if items != want:
                problems.append(f"why: {name} says {want}, {items} listed")
    _trailing(lines, at, problems, "why")
    return problems


def selftest_problems(stdout: str) -> list[str]:
    """`gate selftest` text (project mode): one qualification line or skip per
    evaluator, each with its isolation notes, the summary (P2.3-D18), then —
    only when nothing ran — why. The counts must be the lines'."""
    lines = stdout.splitlines()
    problems: list[str] = []
    at = 0
    rows: list[str] = []
    while at < len(lines) and (T.QUALIFICATION_LINE.fullmatch(lines[at])
                               or SELFTEST_SKIP.fullmatch(lines[at])):
        rows.append(lines[at])
        at += 1
        while at < len(lines) and SELFTEST_NOTE.fullmatch(lines[at]):
            at += 1
    if at == len(lines) or not T.QUALIFIED_SUMMARY.fullmatch(lines[at]):
        got = repr(lines[at]) if at < len(lines) else "the end of the output"
        return [f"selftest: expected the summary after {len(rows)} line(s), got {got}"]
    summary = T.QUALIFIED_SUMMARY.fullmatch(lines[at])
    at += 1
    evaluators = int(summary.group("evaluators"))
    qualified, unqualified = int(summary.group("qualified")), int(summary.group("unqualified"))
    skipped = int(summary.group("skipped"))
    if evaluators != len(rows):
        problems.append(f"selftest: the summary counts {evaluators} evaluator(s), "
                        f"{len(rows)} line(s) printed")
    if qualified + unqualified + skipped != evaluators:
        problems.append(f"selftest: {summary.group(0)!r} does not add up")
    lines_say = [T.QUALIFICATION_LINE.fullmatch(row) for row in rows]
    if qualified != sum(1 for m in lines_say if m and m.group("verdict") == "qualified"):
        problems.append("selftest: the qualified count is not the `→ qualified` lines")
    if unqualified != sum(1 for m in lines_say if m and m.group("verdict") == "unqualified"):
        problems.append("selftest: the unqualified count is not the `→ unqualified` lines")
    if qualified + unqualified == 0:
        at = _grammar(lines, at, (("why nothing ran", SELFTEST_EMPTY, 1, 1),),
                      problems, "selftest")
    _trailing(lines, at, problems, "selftest")
    return problems


#: Pack mode's per-pack line (`cli._pack_line`, P2.3-D18): the pack and where it
#: came from, then its counts — or why it ran nothing. No outcome tag (R-6,
#: words: `[ok  ] <pack> (<origin>) : N fired` before).
PACK_ROW = re.compile(r"^(?P<pack>\S+) \((?P<origin>[^()]+)\) : (?P<body>.+)$")
PACK_COUNTS = re.compile(
    r"^(?P<qualified>\d+) qualified(?:, (?P<unqualified>\d+) unqualified)?"
    r"(?:, (?P<skipped>\d+) skipped)?$")
PACK_NOTHING = re.compile(r"^no gates?(?: at or below tier \d+)?$")
#: A tooling note pack mode prints before its rows (what did not run here, and why).
PACK_NOTE = re.compile(r"^note: .+$")
#: A pack defect under its row: `problem: <gate>: <why>`.
PACK_PROBLEM = re.compile(r"^problem: .+$")


def pack_selftest_problems(stdout: str) -> list[str]:
    """`gate selftest` in pack mode (no project here; spec §3.13, U08): tooling
    notes, one row per pack — each followed by the lines of its unqualified
    evaluators (every one under `-v`) and its problems — then the summary, whose
    counts must be the rows' sums, so a pack row that lost an evaluator cannot
    hide under a summary that kept it."""
    lines = stdout.splitlines()
    problems: list[str] = []
    at = 0
    while at < len(lines) and PACK_NOTE.fullmatch(lines[at]):
        at += 1
    sums = {"qualified": 0, "unqualified": 0, "skipped": 0}
    rows = 0
    unqualified_lines = 0
    while at < len(lines) and PACK_ROW.fullmatch(lines[at]) \
            and not T.QUALIFIED_SUMMARY.fullmatch(lines[at]):
        body = PACK_ROW.fullmatch(lines[at]).group("body")
        counts = PACK_COUNTS.fullmatch(body)
        if counts:
            for key in sums:
                sums[key] += int(counts.group(key) or 0)
        elif not PACK_NOTHING.fullmatch(body):
            problems.append(f"pack selftest: line {at + 1} is not a pack's counts: {body!r}")
        rows += 1
        at += 1
        while at < len(lines) and (T.QUALIFICATION_LINE.fullmatch(lines[at])
                                   or PACK_PROBLEM.fullmatch(lines[at])):
            match = T.QUALIFICATION_LINE.fullmatch(lines[at])
            if match and match.group("verdict") == "unqualified":
                unqualified_lines += 1
            at += 1
    if not rows:
        problems.append("pack selftest: no pack row")
    if at == len(lines) or not T.QUALIFIED_SUMMARY.fullmatch(lines[at]):
        got = repr(lines[at]) if at < len(lines) else "the end of the output"
        return problems + [f"pack selftest: expected the summary after {rows} pack row(s), "
                           f"got {got}"]
    summary = T.QUALIFIED_SUMMARY.fullmatch(lines[at])
    at += 1
    for key in sums:
        if int(summary.group(key)) != sums[key]:
            problems.append(f"pack selftest: the summary says {summary.group(key)} {key}, "
                            f"the pack rows {sums[key]}")
    if int(summary.group("evaluators")) != sum(sums.values()):
        problems.append(f"pack selftest: {summary.group(0)!r} does not add up")
    if unqualified_lines != sums["unqualified"]:
        problems.append(f"pack selftest: {sums['unqualified']} unqualified, "
                        f"{unqualified_lines} line(s) say which")
    _trailing(lines, at, problems, "pack selftest")
    return problems


def exact_problems(stdout: str, expected: tuple, what: str) -> list[str]:
    """``stdout``'s lines are ``expected`` — ``(name, pattern)`` pairs — one to one,
    in order: the transcript's own lines, nothing added, nothing dropped."""
    lines = stdout.splitlines()
    problems = [f"{what}: line {i + 1} is not {name}: "
                f"{lines[i] if i < len(lines) else 'the end of the output'!r}"
                for i, (name, pattern) in enumerate(expected)
                if i >= len(lines) or not re.fullmatch(pattern, lines[i])]
    _trailing(lines, len(expected), problems, what)
    return problems


def porcelain_problems(stdout: str, expected: tuple = ()) -> list[str]:
    """`git status --porcelain` in the clone: exactly one line per ``expected``
    pattern, each a porcelain line — and never a control entry (a re-verified
    control writes no tracked file, spec §5 risk 18) or bytecode (G6)."""
    lines = [line for line in stdout.splitlines() if line]
    problems: list[str] = []
    for line in lines:
        parsed = T.PORCELAIN_LINE.fullmatch(line)
        if parsed is None:
            problems.append(f"porcelain: not a porcelain line: {line!r}")
            continue
        path = parsed.group("path")
        if T.CONTROL_ENTRY_PATH.fullmatch(path):
            problems.append(f"porcelain: a control entry was written: {path}")
        if "__pycache__/" in path or path.endswith(".pyc"):
            problems.append(f"porcelain: bytecode shows: {path}")
    for pattern in expected:
        hits = [line for line in lines if re.fullmatch(pattern, line)]
        if len(hits) != 1:
            problems.append(f"porcelain: {len(hits)} line(s) match {pattern}")
    unmatched = [line for line in lines if not any(re.fullmatch(p, line) for p in expected)]
    problems += [f"porcelain: unexpected {line!r}" for line in unmatched]
    return problems


# --------------------------------------------------------------------------- #
# mutations: each must be refused by the matcher it is aimed at
# --------------------------------------------------------------------------- #
#: A line of prose, the thing R-11 exists to keep out.
PROSE = "Looks good overall — nothing here needs your attention."


def add_line(text: str, after: Callable[[str], bool]) -> str:
    """``PROSE`` inserted after the first line ``after`` accepts."""
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if after(line):
            return "\n".join(lines[:index + 1] + [PROSE] + lines[index + 1:]) + "\n"
    raise AssertionError(f"no line to add after in:\n{text}")


def sub_line(text: str, pattern: re.Pattern, old: str, new: str) -> str:
    """The regex ``old`` -> ``new`` in the one line ``pattern`` matches."""
    lines = text.splitlines()
    hits = [i for i, line in enumerate(lines) if pattern.fullmatch(line)]
    if len(hits) != 1 or not re.search(old, lines[hits[0]]):
        raise AssertionError(f"expected one line matching {pattern.pattern} holding "
                             f"{old!r}, found {[lines[i] for i in hits]}")
    lines[hits[0]] = re.sub(old, new, lines[hits[0]], count=1)
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# the real transcript
# --------------------------------------------------------------------------- #
class _Transcript:
    """The bracket driven through the transcript's steps, once per module."""

    steps: dict[str, object] = {}
    base = ""

    @classmethod
    def get(cls) -> dict[str, object]:
        if cls.steps:
            return cls.steps
        cls.base = tempfile.mkdtemp(prefix="atompipe-shapes-")
        project = _projects.bracket_copy(os.path.join(cls.base, "bracket"))
        run = lambda *argv: _env.atompipe(list(argv), cwd=project)   # noqa: E731
        steps: dict[str, object] = {}
        steps["check-first"] = run("check")
        steps["check-cached"] = run("check")
        steps["status-current"] = run("status")
        path = os.path.join(project, "model", "bracket.py")
        with open(path, encoding="utf-8", newline="") as fh:
            text = fh.read()
        if text.count("bed_xy: float = 220.0") != 1:
            raise AssertionError("the bracket's bed_xy default moved; the transcript's edit "
                                 "cannot be made")
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(text.replace("bed_xy: float = 220.0", "bed_xy: float = 250.0"))
        steps["status-stale"] = run("status")
        steps["check-after-edit"] = run("check")
        steps["why-thickness"] = run("why", "thickness")
        steps["gate-show"] = run("gate", "show", "bracket.deflection")
        steps["selftest"] = run("gate", "selftest")
        # Last, because it breaks the model: `why` with no number to show.
        with open(path, "a", encoding="utf-8", newline="") as fh:
            fh.write('\nraise RuntimeError("the model is mid-edit")\n')
        steps["why-no-model"] = run("why", "thickness")
        cls.steps = steps
        return steps


def tearDownModule() -> None:
    if _Transcript.base:
        _env._rmtree(_Transcript.base)
        _Transcript.base = ""
        _Transcript.steps = {}


class _ShapeCase(unittest.TestCase):
    matcher: Callable[[str], list[str]]

    @classmethod
    def setUpClass(cls) -> None:
        cls.steps = _Transcript.get()

    def out(self, step: str, code: int) -> str:
        proc = self.steps[step]
        self.assertEqual(proc.returncode, code, f"{step}: exit {proc.returncode}\n"
                                                f"{proc.stdout}\n{proc.stderr}")
        return proc.stdout

    def accepts(self, text: str) -> None:
        problems = type(self).matcher(text)
        self.assertEqual(problems, [], "\n".join(problems) + "\n--- output ---\n" + text)

    def refuses(self, text: str, why: str) -> None:
        problems = type(self).matcher(text)
        self.assertTrue(problems, f"the matcher accepted {why}:\n{text}")


class CheckShape(_ShapeCase):
    matcher = staticmethod(check_problems)

    def test_the_real_transcript_matches(self):
        for step in ("check-first", "check-cached", "check-after-edit"):
            with self.subTest(step=step):
                self.accepts(self.out(step, 1))
        cached = self.out("check-cached", 1).splitlines()
        self.assertTrue(any(T.CACHED_ROW.fullmatch(line) for line in cached),
                        "the all-cached check printed no cached row to pin")

    def test_a_line_added_is_refused(self):
        for step in ("check-first", "check-cached"):
            text = self.out(step, 1)
            with self.subTest(step=step, where="after the summary"):
                self.refuses(add_line(text, T.CHECK_SUMMARY.fullmatch), "prose after the summary")
            with self.subTest(step=step, where="first"):
                self.refuses(PROSE + "\n" + text, "prose before the rows")
            with self.subTest(step=step, where="last"):
                self.refuses(text + PROSE + "\n", "prose after the BLOCKING list")

    def test_cached_removed_is_refused(self):
        text = self.out("check-cached", 1)
        self.refuses(sub_line(text, T.CACHED_ROW, r"\s+cached$", ""),
                     "a cached FAIL row without `cached`")
        text = self.out("check-after-edit", 1)
        self.refuses(sub_line(text, T.CACHED_ROW, r"\s+cached$", ""),
                     "a cached FAIL row without `cached` beside an executed row")


class StatusShape(_ShapeCase):
    matcher = staticmethod(status_problems)

    def test_the_real_transcript_matches(self):
        for step in ("status-current", "status-stale"):
            with self.subTest(step=step):
                self.accepts(self.out(step, 0))
        stale = self.out("status-stale", 0).splitlines()
        self.assertTrue(any(T.STALE_LINE.fullmatch(line) for line in stale))
        self.assertTrue(any(T.NOTE_PENDING.fullmatch(line) for line in stale))

    def test_a_line_added_is_refused(self):
        text = self.out("status-stale", 0)
        for where, after in (("in the head", lambda line: bool(re.match(r"^\d+ claims ", line))),
                             ("after the stale block", T.STALE_LINE.fullmatch),
                             ("after last check", T.LAST_CHECK.fullmatch),
                             ("after the note", T.NOTE_PENDING.fullmatch)):
            with self.subTest(where=where):
                self.refuses(add_line(text, after), f"prose {where}")

    def test_the_age_removed_is_refused(self):
        for step in ("status-current", "status-stale"):
            with self.subTest(step=step):
                text = self.out(step, 0)
                self.refuses(sub_line(text, T.LAST_CHECK, r" \([^()]+ ago\)$", ""),
                             "`last check run:` without its age")


class GateShowShape(_ShapeCase):
    matcher = staticmethod(gate_show_problems)

    def test_the_real_transcript_matches(self):
        text = self.out("gate-show", 0)
        self.accepts(text)
        shown = [T.QUALIFICATION_SHOW.fullmatch(line) for line in text.splitlines()]
        self.assertTrue(any(m and m.group("control") for m in shown), text)

    def test_a_line_added_is_refused(self):
        text = self.out("gate-show", 0)
        for where, after in (("after the tier line", SHOW_TIER.fullmatch),
                             ("after the last verdict", SHOW_VERDICT.fullmatch)):
            with self.subTest(where=where):
                self.refuses(add_line(text, after), f"prose {where}")
        self.refuses(text + PROSE + "\n", "prose after the qualification")

    def test_the_prerequisite_lines_are_where_they_belong(self):
        """P2.2: `bracket.deflection` shows its prerequisite, after `runnable`
        and before the control; out of place it is refused."""
        text = self.out("gate-show", 0)
        lines = text.splitlines()
        self.assertIn("  prerequisites: bracket.model_validity", lines)
        moved = [ln for ln in lines if not SHOW_PREREQUISITES.fullmatch(ln)]
        at = next(i for i, ln in enumerate(moved) if SHOW_VERDICT.fullmatch(ln))
        moved.insert(at, "  prerequisites: bracket.model_validity")
        self.refuses("\n".join(moved) + "\n", "a prerequisites line after the control")

    def test_the_control_removed_is_refused(self):
        text = self.out("gate-show", 0)
        self.refuses(sub_line(text, T.QUALIFICATION_SHOW, r" \(control [0-9a-f]{12}\)$", ""),
                     "a qualification that names no control entry")


class WhyShape(_ShapeCase):
    """`why <param>` (spec U29): the model's value and its home, the sections in
    order, every loser tagged with the file it lives in, counts that are the rows'."""
    matcher = staticmethod(why_problems)

    def test_the_real_transcript_matches(self):
        text = self.out("why-thickness", 0)
        self.accepts(text)
        lines = text.splitlines()
        self.assertTrue(T.WHY_PARAM.fullmatch(lines[0]), text)
        self.assertEqual(sum(1 for line in lines if T.WHY_REJECTED_ROW.fullmatch(line)), 1)
        broken = self.out("why-no-model", 0)
        self.accepts(broken)
        self.assertIn("  model does not load: ", broken)

    def test_a_line_added_is_refused(self):
        text = self.out("why-thickness", 0)
        for where, after in (("after the value", T.WHY_PARAM.fullmatch),
                             ("after REJECTED", T.WHY_REJECTED_HEAD.fullmatch),
                             ("after a loser", T.WHY_REJECTED_ROW.fullmatch)):
            with self.subTest(where=where):
                self.refuses(add_line(text, after), f"prose {where}")
        self.refuses(text + PROSE + "\n", "prose after DECISIONS")

    def test_a_home_removed_is_refused(self):
        text = self.out("why-thickness", 0)
        self.refuses(sub_line(text, T.WHY_REJECTED_ROW, r"   \([^()]+\)$", ""),
                     "a loser that names no file")
        self.refuses(sub_line(text, T.WHY_PARAM, r"   \([^()]+\)$", ""),
                     "a value that names no home")

    def test_a_count_that_lies_is_refused(self):
        text = self.out("why-thickness", 0)
        self.refuses(sub_line(text, T.WHY_REJECTED_HEAD, r"\(1\)", "(2)"),
                     "REJECTED (2) over one row")

    def test_a_number_beside_a_broken_model_is_refused(self):
        lines = self.out("why-no-model", 0).splitlines()
        lines[0] = "param thickness = 7.0 mm   (model/bracket.py Config.thickness)"
        self.refuses("\n".join(lines) + "\n", "a number where the model does not load")


class SelftestShape(_ShapeCase):
    matcher = staticmethod(selftest_problems)

    def test_the_real_transcript_matches(self):
        text = self.out("selftest", 0)
        self.accepts(text)
        self.assertEqual(sum(1 for line in text.splitlines()
                             if T.QUALIFICATION_LINE.fullmatch(line)), 6)

    def test_a_line_added_is_refused(self):
        text = self.out("selftest", 0)
        self.refuses(add_line(text, T.QUALIFICATION_LINE.fullmatch), "prose between the rows")
        self.refuses(text + PROSE + "\n", "prose after the summary")

    def test_the_time_removed_is_refused(self):
        text = self.out("selftest", 0)
        self.refuses(sub_line(text, T.QUALIFIED_SUMMARY, r" in \S+:", ":"),
                     "a summary without its time")

    def test_a_row_dropped_is_refused(self):
        lines = self.out("selftest", 0).splitlines()
        self.refuses("\n".join(lines[1:]) + "\n", "five rows under a summary of six")


# --------------------------------------------------------------------------- #
# the transcript itself, on a clone of the migrated bracket (U32)
# --------------------------------------------------------------------------- #
#: The cached FAIL row the transcript opens with (`f"{line:<77} cached"`).
_CACHED_DEFLECTION = (r"^\[FAIL\] bracket\.deflection : 0\.700 mm at 15 N "
                      r"\(limit 0\.5 mm\)\s+cached$")
_BLOCKING_LINES = (
    ("the BLOCKING head", r"^BLOCKING — 3 critical claim\(s\) must not be spent against:$"),
    ("C1", T._C1),
    ("C6", T._C6),
    ("C7", T._C7),
)

#: `check` on a fresh clone: one row (the cached FAIL; cached passes are not
#: printed), the all-cached summary, no controls line (none ran or was
#: re-verified), the BLOCKING list. Exactly these lines.
FIRST_CHECK = (
    ("the cached FAIL", _CACHED_DEFLECTION),
    ("the all-cached summary", r"^6 gates: 0 executed, 6 cached — 5 ok, 1 FAIL — tier 0$"),
    *_BLOCKING_LINES,
)

#: `check --junit`, replayed after the `bed_xy` edit and before the revert: the
#: first check's lines with the limit warning the edit left behind (P2.4, S-35 —
#: R-6: a line the state now prints, pinned where it sits, after the summary).
CHECK_JUNIT = (
    *FIRST_CHECK[:2],
    ("the limit warning", r"^warning: bracket\.bed_fit : its limit 234 mm is not C4's "
                          r"acceptance condition \(bed fit <= 204\.0 mm\) — one number in "
                          r"two places$"),
    *FIRST_CHECK[2:],
)

#: `check` after the `bed_xy` edit: rows in registration order (the plan's
#: transcript lists the executed row first; the CLI streams rows as the gates
#: come), one gate executed, and the six controls re-verified by their fixtures
#: alone — none executed, none written (spec §3.8's E4 consequence).
CHECK_AFTER_EDIT = (
    ("the cached FAIL", _CACHED_DEFLECTION),
    ("the one executed row", r"^\[ok  \] bracket\.bed_fit : 74 x 30 x 7 mm vs 234 mm usable "
                             r"\(250 bed - 2x8 brim\)$"),
    ("the summary", r"^6 gates: 1 executed, 5 cached — 5 ok, 1 FAIL — tier 0$"),
    ("the controls line", r"^controls: 0 run, 0 preserved, 6 re-qualified$"),
    # P2.4 (S-35, R-6: a line the edit now prints, pinned): bed_fit's computed
    # limit is 234 while C4 still says 204.
    ("the limit warning", r"^warning: bracket\.bed_fit : its limit 234 mm is not C4's "
                          r"acceptance condition \(bed fit <= 204\.0 mm\) — one number in "
                          r"two places$"),
    *_BLOCKING_LINES,
)

#: The porcelain after that check: the edit, and the one new piece of evidence.
PORCELAIN_AFTER_EDIT = (
    r"^ M model/bracket\.py$",
    r"^\?\? \.atompipe/verdicts/bracket\.bed_fit/[0-9a-f]{16}-[0-9a-f]{8}\.json$",
)


class _CloneTranscript:
    """The plan's transcript, on a git clone of the bracket as 1.3 committed it."""

    steps: dict[str, object] = {}
    base = ""

    @classmethod
    def get(cls) -> dict[str, object]:
        if cls.steps:
            return cls.steps
        cls.base = tempfile.mkdtemp(prefix="atompipe-transcript-")
        project = _projects.bracket_copy(os.path.join(cls.base, "bracket"), migrated=True,
                                         git=True)
        home = os.path.join(cls.base, "home")
        os.makedirs(home)
        run = lambda *argv: _env.atompipe(list(argv), cwd=project, home=home)  # noqa: E731
        porcelain = lambda: _env.git(["status", "--porcelain", "--untracked-files=all"],  # noqa: E731
                                     cwd=project, home=home)
        steps: dict[str, object] = {"project": project}
        steps["check-first"] = run("check")
        steps["porcelain-clean"] = porcelain()
        path = os.path.join(project, "model", "bracket.py")
        with open(path, encoding="utf-8", newline="") as fh:
            text = fh.read()
        if text.count("bed_xy: float = 220.0") != 1:
            raise AssertionError("the bracket's bed_xy default moved; the transcript's edit "
                                 "cannot be made")
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(text.replace("bed_xy: float = 220.0", "bed_xy: float = 250.0"))
        steps["check-after-edit"] = run("check")
        steps["porcelain-after-edit"] = porcelain()
        empty = os.path.join(cls.base, "empty")
        os.makedirs(empty)
        steps["pack-selftest"] = _env.atompipe(["gate", "selftest"], cwd=empty, home=home)
        steps["check-junit"] = run("check", "--junit")
        cls.steps = steps
        return steps


def _tear_down_clone_transcript() -> None:
    if _CloneTranscript.base:
        _env._rmtree(_CloneTranscript.base)
        _CloneTranscript.base = ""
        _CloneTranscript.steps = {}


class TranscriptShapes(unittest.TestCase):
    """The transcript's lines no matcher above holds whole: the fresh clone's first
    check line for line, both porcelains, the re-check with its controls line,
    pack mode's selftest, and `check --junit` — each on the real run and on a
    mutation of it that must be refused."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.steps = _CloneTranscript.get()
        cls.addClassCleanup(_tear_down_clone_transcript)

    def out(self, step: str, code: int) -> str:
        proc = self.steps[step]
        self.assertEqual(proc.returncode, code, f"{step}: exit {proc.returncode}\n"
                                                f"{proc.stdout}\n{proc.stderr}")
        return proc.stdout

    def holds(self, problems: list[str], text: str) -> None:
        self.assertEqual(problems, [], "\n".join(problems) + "\n--- output ---\n" + text)

    def test_the_first_check_is_the_transcripts_five_lines(self):
        text = self.out("check-first", 1)
        self.holds(exact_problems(text, FIRST_CHECK, "first check"), text)
        self.holds(check_problems(text), text)
        for label, mutant in (
                ("prose after the summary", add_line(text, T.CHECK_SUMMARY.fullmatch)),
                ("`cached` removed", sub_line(text, T.CACHED_ROW, r"\s+cached$", "")),
                ("a gate executed", sub_line(text, T.CHECK_SUMMARY, "0 executed, 6 cached",
                                             "1 executed, 5 cached")),
                ("a cached pass printed", add_line(text, T.CACHED_ROW.fullmatch).replace(
                    PROSE, f"{'[ok  ] bracket.bed_fit : 74 x 30 x 7 mm':<77} cached")),
                ("a controls line", add_line(text, T.CHECK_SUMMARY.fullmatch).replace(
                    PROSE, "controls: 6 run, 0 preserved, 0 re-qualified"))):
            with self.subTest(label):
                self.assertTrue(exact_problems(mutant, FIRST_CHECK, "first check"),
                                f"accepted {label}:\n{mutant}")

    def test_the_clone_is_clean_after_the_first_check(self):
        text = self.out("porcelain-clean", 0)
        self.holds(porcelain_problems(text), text)
        for label, mutant in (
                ("an entry written", "?? .atompipe/verdicts/bracket.bed_fit/"
                                     "0123456789abcdef-01234567.json\n"),
                ("a record rewritten", " M claims/C1.json\n"),
                ("bytecode", "?? model/__pycache__/bracket.cpython-312.pyc\n")):
            with self.subTest(label):
                self.assertTrue(porcelain_problems(text + mutant), f"accepted {label}")

    def test_the_recheck_runs_one_gate_and_no_control(self):
        text = self.out("check-after-edit", 1)
        self.holds(exact_problems(text, CHECK_AFTER_EDIT, "check after the edit"), text)
        self.holds(check_problems(text), text)
        for label, mutant in (
                ("a control executed", sub_line(text, T.CONTROLS_SUMMARY,
                                                r"^controls: 0 run, 0 preserved, 6",
                                                "controls: 1 run, 0 preserved, 5")),
                ("the controls line dropped", "\n".join(
                    line for line in text.splitlines()
                    if not T.CONTROLS_SUMMARY.fullmatch(line)) + "\n"),
                ("the limit warning dropped", "\n".join(
                    line for line in text.splitlines()
                    if not T.LIMIT_WARNING.fullmatch(line)) + "\n"),
                ("the warning naming another limit", text.replace("its limit 234 mm",
                                                                  "its limit 204 mm")),
                ("prose after BLOCKING", text + PROSE + "\n")):
            with self.subTest(label):
                self.assertTrue(exact_problems(mutant, CHECK_AFTER_EDIT, "check after the edit"),
                                f"accepted {label}:\n{mutant}")

    def test_the_edit_leaves_the_model_and_one_new_entry(self):
        text = self.out("porcelain-after-edit", 0)
        self.holds(porcelain_problems(text, PORCELAIN_AFTER_EDIT), text)
        entry = next(line for line in text.splitlines() if line.startswith("?? "))
        for label, mutant in (
                ("a control entry", text + entry.replace("/bracket.bed_fit/",
                                                         "/bracket.bed_fit/control-") + "\n"),
                ("another gate's entry", text + entry.replace("bed_fit", "deflection") + "\n"),
                ("the entry name cut short", text.replace(entry, entry[:-10] + ".json")),
                ("the edit missing", "\n".join(line for line in text.splitlines()
                                               if not line.startswith(" M")) + "\n")):
            with self.subTest(label):
                self.assertTrue(porcelain_problems(mutant, PORCELAIN_AFTER_EDIT),
                                f"accepted {label}:\n{mutant}")

    def test_pack_mode_selftest_ends_on_its_summary(self):
        text = self.out("pack-selftest", 0)
        self.holds(pack_selftest_problems(text), text)
        summary = T.QUALIFIED_SUMMARY.fullmatch(text.splitlines()[-1])
        self.assertIsNotNone(summary, "the summary is not the last line")
        self.assertEqual(summary.group("unqualified"), "0")
        row = next(line for line in text.splitlines() if PACK_ROW.fullmatch(line))
        for label, mutant in (
                ("the time removed", sub_line(text, T.QUALIFIED_SUMMARY, r" in \S+:", ":")),
                ("prose after the summary", text + PROSE + "\n"),
                ("a pack row the summary disagrees with", text.replace(
                    row, re.sub(r"(\d+) qualified",
                                lambda m: f"{int(m.group(1)) + 1} qualified", row), 1)),
                ("an unqualified count no line explains", sub_line(
                    text, T.QUALIFIED_SUMMARY, r", 0 unqualified", ", 1 unqualified")),
                ("no pack row", "\n".join(line for line in text.splitlines()
                                          if not PACK_ROW.fullmatch(line)) + "\n")):
            with self.subTest(label):
                self.assertTrue(pack_selftest_problems(mutant), f"accepted {label}:\n{mutant}")

    def test_check_junit_prints_the_check_and_writes_the_xml(self):
        text = self.out("check-junit", 1)
        self.holds(exact_problems(text, CHECK_JUNIT, "check --junit"), text)
        result = T.Result("atompipe check --junit", 1, text, "", self.steps["project"])
        self.assertIsNone(T.problem(T.junit(), result))
        self.assertIsNotNone(T.problem(T.junit(), result._replace(
            cwd=os.path.join(self.steps["project"], "elsewhere"))))


if __name__ == "__main__":
    unittest.main()


class QualificationLineShape(unittest.TestCase):
    """(V10, R-11) The qualification line's shape, with its own negative control:
    every form the spine prints matches, and every near miss — the
    walkthrough's words GLOSSARY replaced, a ratio over nothing, a fourth
    segment, an outcome tag — is refused."""

    GOOD = (
        "bracket.deflection : known-good pass · known-bad fail · mutation 1/1 fail → qualified",
        "q.never : known-good fail · known-bad fail → unqualified",
        "q.parser : known-good pass · known-bad fail (raised, as declared) · mutation 0 "
        "conclusive (1 inconclusive) → qualified",
        "q.crashy : known-good pass · known-bad errored → unqualified",
        "cad.watertight : known-good pass · known-bad fail · known-good and known-bad via "
        "ctx.extra → unqualified",
        "q.waiver : known-good pass · known-bad fail · mutation 1/1 fail · check run reads "
        "another ledger → unqualified",
        "q.slow1 : known-good pass · known-bad fail · mutation 0 conclusive (none made: tier 1) "
        "→ qualified",
        "beam.deflection : known-good pass · known-bad fail → qualified",
        "q.honest : known-good not run · known-bad fail → unqualified",
        "qpack.span : known-good reads the candidate · known-bad fail → unqualified",
        "q.extra : known-good pass · known-bad fail · channels differ → unqualified",
        "q.honest : known-good pass · known-bad fail · mutation could not run → unqualified",
        "q.tiered : known-good pass · known-bad fail · mutation 0/1 fail · outcomes differ by "
        "tier → unqualified",
    )
    BAD = (
        "bracket.deflection : known-good pass · known-bad fail · mutation 1/1 fail",
        "bracket.deflection : known-good pass · known-bad fail · mutation 0/0 fail → qualified",
        "bracket.deflection : known-good ok · known-bad fail → qualified",
        "bracket.deflection : known-good pass · known-bad rejected → qualified",
        "bracket.deflection : known-good pass · known-bad fail · mutation 4/4 flipped → qualified",
        "bracket.deflection : known-good pass · known-bad fail · mutation 1/1 fail · extra "
        "→ qualified",
        "[ok  ] bracket.deflection : known-good pass · known-bad fail → qualified",
        "gate bracket.deflection: known-good pass · known-bad fail → qualified",
        "q.parser : known-good pass · known-bad raised → qualified",
        "cad.watertight : known-good pass · known-bad fail · channels differ · known-good "
        "and known-bad via ctx.extra → unqualified",
    )

    def test_every_printed_form_matches(self):
        for line in self.GOOD:
            with self.subTest(line=line):
                self.assertRegex(line, T.QUALIFICATION_LINE)

    def test_every_near_miss_is_refused(self):
        for line in self.BAD:
            with self.subTest(line=line):
                self.assertIsNone(T.QUALIFICATION_LINE.fullmatch(line), line)

    def test_the_selftest_summary_pack_row_and_controls_line(self):
        self.assertRegex("6 evaluators in 0.6s: 6 qualified, 0 unqualified, 0 skipped",
                         T.QUALIFIED_SUMMARY)
        self.assertIsNone(T.QUALIFIED_SUMMARY.fullmatch(
            "6 control(s) in 0.6s: 6 fired, 0 BROKEN, 0 skipped (tooling)"))
        self.assertRegex("beam-analytic (bundled) : 8 qualified", T.PACK_ROW)
        self.assertIsNone(T.PACK_ROW.fullmatch("[ok  ] beam-analytic (bundled) : 8 fired"))
        self.assertRegex("controls: 6 run, 0 preserved, 0 re-qualified", T.QUALIFIED_CONTROLS)
        self.assertIsNone(T.QUALIFIED_CONTROLS.fullmatch(
            "controls: 6 executed, 0 cached, 0 re-verified"))
        self.assertRegex("  qualification: known-good pass · known-bad fail · mutation 1/1 "
                         "fail → qualified (control 75cbd091db21)", T.QUALIFICATION_SHOW)
        self.assertRegex("    not mutated  config.load_n — never lands 15% past its limit",
                         T.QUALIFICATION_DETAIL)
        self.assertIsNone(T.QUALIFICATION_SHOW.fullmatch(
            "  last selftest: [ok  ] fired at this version (control 75cbd091db21)"))

    def test_a_non_bundled_extra_channel_pack_prints_its_own_segment(self):
        """Review of P2.3: a copy of sourcing outside the bundled packs read
        `channels differ` on all seven lines, while both controls hand the
        SAME ctx.extra key — the reason, which said so, was suppressed. In pack
        mode, on the line itself: every line matches, and names the fact."""
        base = tempfile.mkdtemp(prefix="atompipe-shape-pack-")
        self.addCleanup(shutil.rmtree, base, ignore_errors=True)
        pack = os.path.join(base, "srcshape")
        shutil.copytree(os.path.join(_projects.PACKS, "sourcing"), pack,
                        ignore=shutil.ignore_patterns("__pycache__"))
        out = _env.atompipe(["gate", "selftest", "--pack", pack], cwd=base).stdout.splitlines()
        lines = [ln for ln in out if ln.startswith("bom.")]
        self.assertEqual(len(lines), 7, out)
        for line in lines:
            with self.subTest(line=line):
                self.assertRegex(line, T.QUALIFICATION_LINE)
                self.assertIn(" · known-good and known-bad via ctx.extra → unqualified", line)
                self.assertNotIn("channels differ", line)

    def test_the_bracket_selftest_prints_six_lines_and_the_summary(self):
        root = _projects.bracket_copy(os.path.join(tempfile.mkdtemp(prefix="atompipe-shape-"),
                                                   "bracket"), migrated=True)
        self.addCleanup(shutil.rmtree, os.path.dirname(root), ignore_errors=True)
        out = _env.atompipe(["gate", "selftest"], cwd=root).stdout.splitlines()
        lines = [ln for ln in out if T.QUALIFICATION_LINE.fullmatch(ln)]
        self.assertEqual(len(lines), 6, out)
        self.assertTrue(all(ln.endswith("→ qualified") for ln in lines), lines)
        self.assertRegex(out[-1], T.QUALIFIED_SUMMARY)


class GoalpostAndContextLinesShape(unittest.TestCase):
    """(R-11, P2.4) Every line P2.4 adds to a human channel has a shape, each held
    with its own negative control: `check`'s tally of passes outside an operating
    context, the context line and the limit warning after the qualification lines,
    the qualification line's two new facts, `gate show`'s operating-context row,
    and its goalpost detail rows. A near miss — the line in the wrong place, the
    wrong words, a count that does not add up — is refused (critique 6 of the
    P2.4 design: the bracket's transcript printed a warning nothing pinned)."""

    CHECK = "\n".join([
        "1 gates: 1 executed, 0 cached — 0 ok, 1 outside operating context — tier 0",
        "controls: 1 run, 0 preserved, 0 re-qualified",
        "t.span : known-good pass · known-bad fail · mutation 1/1 fail → qualified",
        "t.span : outside operating context: load_n = 60, qualified on [0, 40]",
        "warning: t.other : its limit 234 mm is not c4's acceptance condition (bed fit <= "
        "204.0 mm) — one number in two places",
        "BLOCKING — 1 critical claim(s) must not be spent against:",
        "[gap  ] c1 Sag stays under 0.5 mm — outside operating context: t.span : load_n = 60, "
        "qualified on [0, 40]",
    ]) + "\n"

    def test_the_check_lines_hold(self):
        self.assertEqual(check_problems(self.CHECK), [])

    def test_each_near_miss_is_refused(self):
        lines = self.CHECK.splitlines()
        for label, mutant in (
                ("the context line before the summary", "\n".join(
                    [lines[3]] + lines[:3] + lines[4:])),
                ("the warning after BLOCKING", "\n".join(
                    lines[:4] + lines[5:] + [lines[4]])),
                ("the tally counted unqualified", self.CHECK.replace(
                    "1 outside operating context", "1 unqualified, 1 outside operating context")),
                ("the context line in other words", self.CHECK.replace(
                    "qualified on [0, 40]\nwarning", "valid on [0, 40]\nwarning")),
                ("the warning in other words", self.CHECK.replace(
                    "one number in two places", "limits differ"))):
            with self.subTest(label):
                self.assertTrue(check_problems(mutant + "\n"), f"accepted {label}")

    def test_the_context_line_and_warning_shapes(self):
        for good in ("t.span : outside operating context: load_n = 60, qualified on [0, 40]",
                     "t.span : outside operating context: load_n absent, qualified on [0, 40]",
                     "t.span : outside operating context: load_n = 'x' (not a number), "
                     "qualified on (−∞, 40]",
                     "t.span : outside operating context: load_n not read by the run that "
                     "passed, qualified on [0, ∞)"):
            with self.subTest(good=good):
                self.assertRegex(good, T.CONTEXT_LINE)
        for bad in ("[ERR ] t.span : unqualified: context:outside|{}",
                    "t.span : unqualified: outside operating context: load_n = 60",
                    "t.span : outside operating context: load_n = 60, qualified on 0..40"):
            with self.subTest(bad=bad):
                self.assertIsNone(T.CONTEXT_LINE.fullmatch(bad))
        self.assertIsNone(T.LIMIT_WARNING.fullmatch(
            "bracket.bed_fit : its limit 234 mm is not C4's acceptance condition (bed fit <= "
            "204.0 mm) — one number in two places"), "the warning without its lead")

    def test_the_qualification_line_gains_two_facts(self):
        for good in ("t.span : known-good outside its operating context · known-bad fail "
                     "→ unqualified",
                     "t.keyed : known-good pass · known-bad fail · mutation 1/1 fail · value "
                     "moves with its limit → unqualified"):
            with self.subTest(good=good):
                self.assertRegex(good, T.QUALIFICATION_LINE)
        for bad in ("t.span : known-good outside · known-bad fail → unqualified",
                    "t.keyed : known-good pass · known-bad fail · value moves with its "
                    "limit · mutation 1/1 fail → unqualified",
                    "t.keyed : known-good pass · known-bad fail · mutation 1/1 fail · value "
                    "moves with its goalpost → unqualified"):
            with self.subTest(bad=bad):
                self.assertIsNone(T.QUALIFICATION_LINE.fullmatch(bad))

    def test_why_and_claim_show_rows(self):
        """Review of P2.4: `why` and `claim show` printed a marked verdict as
        `[ERR ] <gate> : unqualified: <token>`; each row now reads as `check` and
        `gate show` print it, and `claim show` says a comparison's Failing under
        its header."""
        self.assertRegex("t.span : outside operating context: load_n = 60, qualified on "
                         "[0, 40]", T.CONTEXT_LINE)
        self.assertRegex("t.keyed : unqualified: its known-good value moves when the "
                         "acceptance condition it reads (c2) moves: a value is measured, "
                         "never chosen by the limit it is judged against", T.UNQUALIFIED_ROW)
        for bad in ("[ERR ] t.span : unqualified: context:outside|{}",
                    "[ERR ] t.keyed : unqualified: goalpost:moves|c2",
                    "t.keyed : unqualified: goalpost:moves|c2",
                    "t.keyed : unqualified: qualification:not-yet"):
            with self.subTest(bad=bad):
                self.assertIsNone(T.UNQUALIFIED_ROW.fullmatch(bad))
                self.assertIsNone(T.CONTEXT_LINE.fullmatch(bad))
        self.assertRegex("  acceptance condition not met: bracket.bearing : 0.195 MPa against "
                         "C3's bearing stress <= 0.1 MPa (its own limit 15 MPa)",
                         T.ACCEPTANCE_REASON)
        for bad in ("acceptance condition not met: bracket.bearing : 0.195 MPa against C3's "
                    "bearing stress <= 0.1 MPa (its own limit 15 MPa)",
                    "  [ok  ] bracket.bearing : 0.19 MPa on 77 mm^2 across 2 bolt(s)"):
            with self.subTest(bad=bad):
                self.assertIsNone(T.ACCEPTANCE_REASON.fullmatch(bad))

    def test_gate_show_places_the_operating_context(self):
        body = "\n".join([
            "t.span  [t0]  sag  claims: c1",
            "  tier 0  pack (project)  entry gates.g:span",
            "  claims: c1",
            "  runnable here: yes",
            "  operating context: load_n in [0, 40] — outside it a pass does not count; a fail "
            "still does",
            "  control: selftest/bad.py:soft (must fail)",
            "  last verdict: [ok  ] t.span : 0.250 mm (limit 0.5 mm)",
            "  qualification: known-good pass · known-bad fail · mutation 1/1 fail → qualified "
            "(control 0123456789ab)",
            "    limit moved  c1 limit -> 0.05: fail, 0.4395 mm",
            # P2.5a (R-6, an addition): the track record row, last.
            "  track record: 0 contradictions at this version, 0 at earlier versions",
        ]) + "\n"
        self.assertEqual(gate_show_problems(body), [])
        lines = body.splitlines()
        moved = "\n".join(lines[:4] + lines[5:6] + [lines[4]] + lines[6:]) + "\n"
        self.assertTrue(gate_show_problems(moved), "the context row after the control")
        worded = body.replace("a fail still does", "fails still count")
        self.assertTrue(gate_show_problems(worded), "the context row in other words")


# --------------------------------------------------------------------------- #
# V-16 (P2.5a): the physical path's lines, each pinned with its negative control
# --------------------------------------------------------------------------- #
#: `claim physical`'s prompt (stderr), in order: the claim, what it is and how it
#: reads now, what is recorded, the article (or why there is none), the revision
#: when there is one, any contradiction, who records it, the typed-id line.
PROMPT = (
    ("claim", re.compile(r"^C\d+ .+$"), 1, 1),
    ("status", re.compile(r"^  (?:automated|measurement|assumption|expert judgment(?:: .+)?)"
                          r" · (?:required|not required) · reads now: .+$"), 1, 1),
    ("record", re.compile(r"^  you record: (?:pass|fail) — .+$"), 1, 1),
    ("article", re.compile(r"^  on article [0-9a-f]{12}: the design as the model holds it "
                           r"now \(.+\)$"), 1, 1),
    ("revision", re.compile(r"^  revision [0-9a-f]{12}(?:, with uncommitted changes to "
                            r"model/)?$"), 0, 1),
    ("contradicts", re.compile(r"^  this contradicts \S+, which passed C\d+ at .+ \(version "
                               r"[0-9a-f]{12}\)$"), 0, 9),
    ("recorded by", re.compile(r"^  recorded by: .+ <.+>$"), 1, 1),
    ("type", re.compile(r"^type C\d+ to record it \(anything else records nothing\): $"), 1, 1),
)
#: `assume`'s prompt (stderr): the claim, how it reads, who is recorded as what
#: and for which reason, who records it, the typed-id line — the review of
#: P2.5a's addition: neither `assume`'s prompt nor an authority's judgment
#: prompt was pinned (R-11), so either could gain, lose or reword a row unseen.
ASSUME_PROMPT = (
    PROMPT[0], PROMPT[1],
    ("assume", re.compile(r"^  you record: .+ as C\d+'s (?:owner|authority) — .+$"), 1, 1),
    PROMPT[-2], PROMPT[-1],
)
RECORDED = re.compile(r"^recorded C\d+ (?:pass|fail) in results/C\d+\.json \(entry \d+\)"
                      r"(?: — from (?:an agent session|a pipe or a script): .+)?$")
RECORDED_ASSUME = re.compile(r"^recorded C\d+'s (?:owner|authority) .+ in results/C\d+\.json "
                             r"\(attribution \d+\)$")
CLAIM_ROW = re.compile(r"^\[.{5}\] C\d+ .+ — .+$")
CONTRADICTS = re.compile(r"^contradicts: \S+ \(version [0-9a-f]{12}\) — on its track record$")
REFUSALS = {
    "refuse.who": re.compile(r"^error: --who is not accepted: who recorded a result is read "
                             r"from git's identity .+ Nothing was written\.$"),
    "refuse.when": re.compile(r"^error: --when is not accepted: .+ --detail\. Nothing was "
                              r"written\.$"),
    "refuse.identity": re.compile(r"^error: no git identity here — .+ Nothing was written\.$"),
    "refuse.typed": re.compile(r"^error: you typed 'C4', not C5 — nothing was recorded$"),
    "refuse.assume": re.compile(r"^error: assume records a person accepting C6; .+ Ask .+ to "
                                r"run: atompipe claim physical C6 assume$"),
}
REBUILD = re.compile(r"^rebuild: article [0-9a-f]{12} \(C\d+(?:, C\d+)*\) — .+ -> .+$")
TRACK = re.compile(r"^  track record: \d+ contradictions? at this version, \d+ at earlier "
                   r"versions(?: — C\d+ on article [0-9a-f]{12} \(.+\))?$")
CLAIM_LIST = re.compile(r"^\[.{5}\] C\d+\s+.+  \[(?:automated|measurement|assumption|expert "
                        r"judgment: .+)\] .+$")
RESULTS_HEAD = re.compile(r"^PHYSICAL RESULTS \(\d+, oldest first\)$")
JUDGMENTS_HEAD = re.compile(r"^JUDGMENTS \(\d+, oldest first\)$")
#: `why`'s acceptance line when the claim writes none (review of P2.5a: "NONE —
#: without a threshold this is a wish", for a note's test and a judgment alike).
ACCEPTANCE_NONE = re.compile(r"^  acceptance: (?:no acceptance condition|none — the test is "
                             r"its note|none — settled only by .+'s judgment)$")
RESULT_ROW = re.compile(r"^  (?:pass|fail)(?: on article [0-9a-f]{12})? \(recorded .+$")


def prompt_problems(stderr: str) -> list[str]:
    """The prompt's rows in order, once each, then the typed-id line last."""
    lines = stderr.splitlines()
    problems: list[str] = []
    at = _grammar(lines, 0, PROMPT, problems, "prompt")
    if at != len(lines):
        problems.append(f"prompt: {len(lines) - at} line(s) after the typed-id line")
    return problems


def assume_prompt_problems(stderr: str) -> list[str]:
    """`assume`'s prompt rows in order, once each, then the typed-id line last."""
    lines = stderr.splitlines()
    problems: list[str] = []
    at = _grammar(lines, 0, ASSUME_PROMPT, problems, "assume prompt")
    if at != len(lines):
        problems.append(f"assume prompt: {len(lines) - at} line(s) after the typed-id line")
    return problems


def recorded_problems(stdout: str, *, assume: bool = False) -> list[str]:
    """`claim physical`'s stdout: the recorded line, the composed row, and any
    `contradicts:` lines — nothing else."""
    lines = stdout.splitlines()
    out: list[str] = []
    if not lines or not (RECORDED_ASSUME if assume else RECORDED).match(lines[0]):
        out.append(f"recorded: {lines[:1]}")
    if len(lines) < 2 or not CLAIM_ROW.match(lines[1]):
        out.append(f"row: {lines[1:2]}")
    out += [f"extra: {ln}" for ln in lines[2:] if not CONTRADICTS.match(ln)]
    return out


def physical_shape_problems(found: dict) -> list[str]:
    """Every physical-path line of a transcript (`_physical.transcript`) held to
    its shape."""
    out = [f"tty.pass {p}" for p in prompt_problems(found["tty.pass"].stderr)]
    out += [f"contradiction {p}" for p in prompt_problems(found["contradiction"].stderr)]
    out += [f"judgment {p}" for p in prompt_problems(found["judgment"].stderr)]
    for key in ("assume", "assume.authority"):
        out += [f"{key} {p}" for p in assume_prompt_problems(found[key].stderr)]
    for key in ("tty.pass", "agent.pass", "agent.fail", "contradiction", "judgment"):
        out += [f"{key} {p}" for p in recorded_problems(found[key].stdout)]
    for key in ("assume", "assume.authority"):
        out += [f"{key} {p}" for p in recorded_problems(found[key].stdout, assume=True)]
    if not any(CONTRADICTS.match(ln) for ln in found["contradiction"].stdout.splitlines()):
        out.append("contradiction: no contradicts: line")
    for key, pattern in REFUSALS.items():
        lines = found[key].stderr.splitlines()
        # A refusal after the prompt follows the typed-id line on the same
        # terminal line: the person's Enter is echoed by their terminal, not here.
        last = re.sub(r"^type C\d+ to record it \(anything else records nothing\): ", "",
                      lines[-1]) if lines else ""
        if key != "refuse.typed" and len(lines) != 1:
            out.append(f"{key}: {lines}")
        elif not pattern.match(last):
            out.append(f"{key}: {last}")
    rebuild = [ln for ln in found["status"].stdout.splitlines() if ln.startswith("rebuild:")]
    if len(rebuild) != 1 or not REBUILD.match(rebuild[0]):
        out.append(f"status rebuild: {rebuild}")
    check = [ln for ln in found["check"].stdout.splitlines() if ln.startswith("rebuild:")]
    if check != rebuild:
        out.append(f"check rebuild: {check} != {rebuild}")
    track = [ln for ln in found["gate.show"].stdout.splitlines() if "track record" in ln]
    if len(track) != 1 or not TRACK.match(track[0]):
        out.append(f"gate show track: {track}")
    rows = [ln for ln in found["claim.list"].stdout.splitlines() if ln.startswith("[")]
    out += [f"claim list: {ln}" for ln in rows if not CLAIM_LIST.match(ln)]
    lines = found["why.C1"].stdout.splitlines()
    heads = [i for i, ln in enumerate(lines) if RESULTS_HEAD.match(ln)]
    if len(heads) != 1 or not RESULT_ROW.match(lines[heads[0] + 1] if heads and
                                               heads[0] + 1 < len(lines) else ""):
        out.append(f"why results: {[lines[i] for i in heads]}")
    lines = found["why.C8"].stdout.splitlines()
    heads = [i for i, ln in enumerate(lines) if JUDGMENTS_HEAD.match(ln)]
    if (len(heads) != 1 or any(RESULTS_HEAD.match(ln) for ln in lines)
            or not RESULT_ROW.match(lines[heads[0] + 1] if heads and heads[0] + 1 < len(lines)
                                    else "")):
        out.append(f"why judgments: {[lines[i] for i in heads]}")
    for key in ("why.C5", "why.C8"):
        rows = [ln for ln in found[key].stdout.splitlines() if ln.startswith("  acceptance:")]
        if len(rows) != 1 or not ACCEPTANCE_NONE.match(rows[0]):
            out.append(f"{key} acceptance: {rows}")
    return out


class PhysicalLinesShape(unittest.TestCase):
    """(V-16, R-11) `claim physical`'s prompt, its recorded lines, its
    `contradicts:` line and refusals, the `rebuild:` line, `gate show`'s `track
    record:` row, `why`'s `PHYSICAL RESULTS` block and `claim list`'s terminal
    column — each matched, and each matcher run against a mutated transcript
    that must fail."""

    def setUp(self):
        import _physical as P
        self.found = P.transcript()

    def test_the_real_transcript_matches(self):
        self.assertEqual(physical_shape_problems(self.found), [])

    def test_each_mutation_is_refused(self):
        class _Out:
            def __init__(self, stdout="", stderr=""):
                self.stdout, self.stderr = stdout, stderr

        f = self.found
        mutations = {
            "a prompt row added": ("tty.pass", _Out(f["tty.pass"].stdout,
                                                    "  a friendly note\n" + f["tty.pass"].stderr)),
            "the article id removed": ("tty.pass", _Out(f["tty.pass"].stdout, re.sub(
                r"on article [0-9a-f]{12}", "on the article", f["tty.pass"].stderr))),
            "a recorded line with a word added": ("agent.pass", _Out(
                f["agent.pass"].stdout.replace("recorded C5", "signed C5", 1))),
            "the contradicts line without its version": ("contradiction", _Out(re.sub(
                r"\(version [0-9a-f]{12}\) ", "", f["contradiction"].stdout),
                f["contradiction"].stderr)),
            "a refusal without its way": ("refuse.who", _Out("", "error: --who is refused\n")),
            "the rebuild line without its article": ("status", _Out(re.sub(
                r"rebuild: article [0-9a-f]{12}", "rebuild: the part", f["status"].stdout))),
            "the track record without its count": ("gate.show", _Out(f["gate.show"].stdout
                                                                      .replace(" at earlier",
                                                                               " earlier"))),
            "claim list printing the kind": ("claim.list", _Out(f["claim.list"].stdout.replace(
                "[measurement]", "[physical]"))),
            "why without its results head": ("why.C1", _Out(f["why.C1"].stdout.replace(
                "PHYSICAL RESULTS", "RESULTS"))),
            "assume's prompt with a row added": ("assume", _Out(
                f["assume"].stdout, "  a friendly note\n" + f["assume"].stderr)),
            "assume's prompt without its reason": ("assume.authority", _Out(
                f["assume.authority"].stdout,
                re.sub(r"(?m)^(  you record: .+?) — .+$", r"\1", f["assume.authority"].stderr))),
            "a judgment's prompt without its article": ("judgment", _Out(
                f["judgment"].stdout, re.sub(r"on article [0-9a-f]{12}", "on the article",
                                             f["judgment"].stderr))),
            "a judgment headed as a physical result": ("why.C8", _Out(
                f["why.C8"].stdout.replace("JUDGMENTS", "PHYSICAL RESULTS"))),
            "the acceptance line in a threshold's words": ("why.C5", _Out(re.sub(
                r"(?m)^  acceptance: .*$",
                "  acceptance: NONE — without a threshold this is a wish, not a claim",
                f["why.C5"].stdout))),
        }
        for name, (key, mutated) in mutations.items():
            with self.subTest(name):
                self.assertNotEqual(physical_shape_problems(dict(f, **{key: mutated})), [],
                                    f"{name} was not refused")



# --------------------------------------------------------------------------- #
# V-15 (P2.5b) — `export`'s lines and REPORT.md's head
# --------------------------------------------------------------------------- #
MILESTONE_ROW = re.compile(
    r"^  (?P<m>[a-z0-9][a-z0-9._-]*): (?:\d+ of \d+ required claims? checked · \d+ stale"
    r"(?: \([^)]*\))?(?: · .+)?|requires no claim, so nothing can be ready for it — .+)$")
EXPORT_LIST = (
    ("head", re.compile(r"^(?:\d+ milestones?, as last evaluated — `atompipe export "
                        r"<milestone> --dry-run` re-runs what each requires|no milestone "
                        r"declared — a milestone is milestones/<name>\.json: .+)$"), 1, 1),
    ("milestone", MILESTONE_ROW, 0, None),
)
EXPORT_HEAD = re.compile(r"^(?P<m>[a-z0-9][a-z0-9._-]*)(?: — .+)?$")
MILESTONE_LINE = re.compile(
    r"^(?P<m>[a-z0-9][a-z0-9._-]*): (?:\d+ of \d+ required claims? checked · \d+ stale"
    r"(?: \([^)]*\))?(?: · (?P<groups>.+))?|requires no claim, so nothing can be ready for "
    r"it — .+)$")
EXPORT_ROW = re.compile(r"^\[.{5}\] (?P<id>\S+) .+ — .+$")
NOT_REQUIRED = re.compile(r"^not required by \S+, unresolved: .+$")
RERUN = re.compile(r"^re-run: (?:\d+ evaluators? at tier \d, with their controls — .+|none — "
                   r".+)$")
TWO_OUTCOMES = re.compile(r"^\[two outcomes\] \S+ : .+$")
EXPORT_SENTENCE = re.compile(
    r"^\S+ is (?:NOT )?ready for \S+: .+ (?:Checked on an article: \d+ \([^)]+\)\."
    r"|No claim is checked on any article\.)$")
LIMITS = re.compile(r"^Checked does not mean true: its evaluators have shown they can "
                    r"fail, not that they are right or enough, and nothing outside these \d+ "
                    r"claims? has been evaluated\.$")
DECIDED = re.compile(r"^(?:decided by .+: go ahead over .+|nothing to decide: every required "
                     r"claim is checked)$")
OUTCOME = re.compile(
    r"^export: (?:would refuse — .+|refused — .+"
    r"|would write out/\S+/ — article [0-9a-f]{12} · package [0-9a-f]{12} · \d+ files? "
    r"\(dry run: nothing written\)"
    r"|out/\S+/ — article [0-9a-f]{12} · package [0-9a-f]{12} · \d+ files?, recorded in "
    r"exports/\S+\.json)$")
CARD_HEAD = re.compile(r"^test card for article [0-9a-f]{12}: .+$")
CARD_ROW = re.compile(r"^  \S.*$")
EXPORT_RUN = (
    ("head", EXPORT_HEAD, 1, 1),
    ("milestone line", MILESTONE_LINE, 1, 1),
    ("claim row", EXPORT_ROW, 0, None),
    ("not-required line", NOT_REQUIRED, 0, 1),
    ("re-run line", RERUN, 1, 1),
    ("two outcomes", TWO_OUTCOMES, 0, None),
    ("sentence", EXPORT_SENTENCE, 1, 1),
    ("limits line", LIMITS, 1, 1),
    ("decision", DECIDED, 0, 1),
    ("outcome", OUTCOME, 1, 1),
    ("test card", CARD_HEAD, 0, 1),
    ("card row", CARD_ROW, 0, None),
)


def export_list_problems(stdout: str) -> list[str]:
    lines = stdout.splitlines()
    problems: list[str] = []
    at = _grammar(lines, 0, EXPORT_LIST, problems, "export list")
    _trailing(lines, at, problems, "export list")
    return problems


def export_problems(stdout: str) -> list[str]:
    """`export <m>` text (P2.5b §2.2, §2.3): the head, the milestone line, a row
    per unresolved required claim, the not-required line, the re-run line and its
    two-outcomes rows, the readiness sentence with its hardware clause, the
    limits line, the decision, the outcome and the test card. And what the lines
    must agree on: each required claim the milestone line names unresolved has a
    row; a sentence naming claims the milestone does not require has the
    not-required line; a written or would-write outcome names its article."""
    lines = stdout.splitlines()
    problems: list[str] = []
    at = _grammar(lines, 0, EXPORT_RUN, problems, "export")
    _trailing(lines, at, problems, "export")
    rows = {m.group("id") for m in map(EXPORT_ROW.fullmatch, lines) if m}
    line = next((MILESTONE_LINE.fullmatch(ln) for ln in lines if MILESTONE_LINE.fullmatch(ln)),
                None)
    groups = (line.group("groups") or "") if line else ""
    named = set(re.findall(r"\b([A-Z]+\d+)\b", re.sub(r"\d+ with no claim file \([^)]*\)", "",
                                                      groups)))
    for cid in sorted(named - rows):
        problems.append(f"export: {cid} is unresolved and has no row")
    sentence = next((ln for ln in lines if EXPORT_SENTENCE.fullmatch(ln)), "")
    pending = re.search(r"Pending build: \d+ claims? needs? an article \(([^)]*)\)", sentence)
    unrequired = [cid for cid in re.findall(r"\b([A-Z]+\d+)\b", pending.group(1))
                  if cid not in rows] if pending else []
    line_ = " ".join(ln for ln in lines if NOT_REQUIRED.fullmatch(ln))
    for cid in unrequired:
        if not re.search(rf"\b{cid}\b", line_):
            problems.append(f"export: {cid} is unresolved, not required, and on no "
                            f"not-required line")
    return problems


def report_head_problems(md: str) -> list[str]:
    """REPORT.md's head (P2.5b §2.4): the title, the readiness sentence with its
    hardware clause, the limits paragraph, the milestones as last evaluated."""
    lines = md.splitlines()
    out = []
    if not re.fullmatch(r"# .+ — readiness(?: for \S+)? \(.+\)", lines[0] if lines else ""):
        out.append(f"title: {lines[:1]}")
    sentence = lines[2] if len(lines) > 2 else ""
    if not re.search(r"(?:Checked on an article: \d+ \([^)]+\)\.|No claim is checked on any "
                     r"article\.)$", sentence):
        out.append(f"sentence without its hardware clause: {sentence[-80:]}")
    if len(lines) < 5 or not LIMITS.fullmatch(lines[4]):
        out.append(f"limits: {lines[4:5]}")
    return out


class ExportShape(unittest.TestCase):
    """(V-15, R-11) `export`'s list, `export <m>` refused, would-refuse,
    would-write, written, over a disagreement and over a decision, and REPORT.md's
    head — each matched, and each matcher run against mutants it must refuse."""

    @classmethod
    def setUpClass(cls):
        import _physical as P
        import test_export as X
        cls.base = tempfile.mkdtemp(prefix="atompipe-shapes-export-")
        cls.addClassCleanup(_env._rmtree, cls.base)
        seven = X.bracket(os.path.join(cls.base, "seven"), thickness=7.0, git=True)
        eight = X.bracket(os.path.join(cls.base, "eight"), thickness=8.0, git=True)
        forged = X.bracket(os.path.join(cls.base, "forged"), thickness=7.0, git=True)
        decided = X.bracket(os.path.join(cls.base, "decided"), thickness=7.0, git=True)
        for root in (seven, eight, forged, decided):
            P.run(root, "check")
        X.forge_entry(forged, "bracket.deflection", passed=True)
        cls.out = {
            "list": P.run(seven, "export", code=0),
            "would-refuse": P.run(seven, "export", "print-v1", "--dry-run", code=1),
            "refused": P.run(seven, "export", "print-v1", code=1),
            "would-write": P.run(eight, "export", "print-v1", "--dry-run", code=0),
            "written": P.run(eight, "export", "print-v1", code=0),
            "disagree": P.run(forged, "export", "print-v1", "--dry-run", code=1),
            "decided": P.tty(decided, "export", "print-v1", "--proceed", "--why",
                             "a fit print", answer="print-v1", code=0),
            "report": P.run(seven, "report", code=0),
            "report.milestone": P.run(seven, "report", "--milestone", "print-v1", code=0),
        }

    def test_the_real_transcripts_match(self):
        self.assertEqual(export_list_problems(self.out["list"].stdout), [])
        for key in ("would-refuse", "refused", "would-write", "written", "disagree",
                    "decided"):
            with self.subTest(key):
                problems = export_problems(self.out[key].stdout)
                self.assertEqual(problems, [], "\n".join(problems) + "\n" +
                                 self.out[key].stdout)
        self.assertTrue(any(TWO_OUTCOMES.fullmatch(ln)
                            for ln in self.out["disagree"].stdout.splitlines()))
        for key in ("report", "report.milestone"):
            with self.subTest(key):
                self.assertEqual(report_head_problems(self.out[key].stdout), [])

    def test_each_mutant_is_refused(self):
        refused = self.out["would-refuse"].stdout
        written = self.out["written"].stdout
        mutants = {
            "a line added": add_line(refused, lambda ln: ln.startswith("re-run:")),
            "the hardware clause removed": re.sub(
                r" (?:No claim is checked on any article\.|Checked on an article: [^.]+\.)$", "",
                refused, flags=re.M),
            "the article hash removed": re.sub(r"article [0-9a-f]{12}", "article", written),
            "the re-run line removed": "\n".join(ln for ln in refused.splitlines()
                                                 if not ln.startswith("re-run:")) + "\n",
            "a refusal row removed": "\n".join(ln for ln in refused.splitlines()
                                               if not ln.startswith("[FAIL ] C1")) + "\n",
            "the not-required line removed": "\n".join(
                ln for ln in refused.splitlines() if not ln.startswith("not required by")) + "\n",
            "the limits line removed": "\n".join(
                ln for ln in refused.splitlines() if not LIMITS.fullmatch(ln)) + "\n",
        }
        for name, text in mutants.items():
            with self.subTest(name):
                self.assertTrue(export_problems(text), f"{name} was not refused:\n{text}")
        report = self.out["report"].stdout
        for name, text in (("the report's hardware clause removed", re.sub(
                r" No claim is checked on any article\.", "", report, count=1)),
                           ("the report's limits removed", report.replace(
                               "Checked does not mean true: ", "Note: ", 1))):
            with self.subTest(name):
                self.assertTrue(report_head_problems(text), name)
