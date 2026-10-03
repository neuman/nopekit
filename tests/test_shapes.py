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
SHOW_CONTROL = re.compile(r"^  control: (?:\S+ \(must \w+\)|NONE — .+)$")
SHOW_NOTE = re.compile(r"^           \S.*$")
SHOW_VERDICT = re.compile(r"^  last verdict: (?:\[.{4}\] \S+.*|\(never run\))$")
#: The fifth state `gate show` can print, beside §3.13's four: a control that is
#: not admitted for a reason other than passing its known-bad input (a crash, two
#: disagreeing controls). Pinned so the matcher accepts every line the CLI prints.
SHOW_NOT_ADMITTED = re.compile(
    r"^  last selftest: \[FAIL\] not admitted at this version — .+$")

#: `gate selftest` (project mode): one row per control, then the summary; a
#: broken control adds the refusal head and one line per broken gate.
SELFTEST_ROW = re.compile(r"^\[(?P<tag>.{4})\] (?P<gate>\S+)#selftest(?: : (?P<body>.*))?$")
SELFTEST_BROKEN_HEAD = re.compile(r"^these gates cannot be trusted — .+:$")
SELFTEST_BROKEN_ROW = re.compile(r"^\[FAIL\] (?P<gate>\S+)#selftest — .+$")
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
    if not executed - skipped <= len(fresh_rows) <= executed:
        problems.append(f"check: the summary says {executed} executed ({skipped} skipped) "
                        f"but {len(fresh_rows)} row(s) print as executed")
    if len(cached_rows) > cached:
        problems.append(f"check: {len(cached_rows)} cached row(s) for {cached} cached")
    for line in cached_rows:
        if T.CACHED_ROW.fullmatch(line).group("tag") == "ok  ":
            problems.append(f"check: a cached pass is printed: {line!r}")
    total = sum(int(summary.group(k) or 0) for k in ("ok", "failed", "skipped", "errored"))
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
    claims, runnable, the control (and its note), the last verdict, and the last
    selftest as the LAST line (spec §3.13)."""
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
            ("runnable", SHOW_RUNNABLE, 1, 1), ("control", SHOW_CONTROL, 1, 1),
            ("control note", SHOW_NOTE, 0, 1), ("last verdict", SHOW_VERDICT, 1, 1))
    at = _grammar(lines, at, body, problems, "gate show")
    if at < len(lines) and (T.LAST_SELFTEST.fullmatch(lines[at])
                            or SHOW_NOT_ADMITTED.fullmatch(lines[at])):
        at += 1
    else:
        got = repr(lines[at]) if at < len(lines) else "the end of the output"
        problems.append(f"gate show: expected `last selftest:` at line {at + 1}, got {got}")
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
    """`gate selftest` text (project mode): one row per control, the summary
    (spec §3.13), then — only when something needs saying — why nothing ran, or
    the refusal and one line per broken control. The counts must be the rows'."""
    lines = stdout.splitlines()
    problems: list[str] = []
    at = 0
    rows = []
    while at < len(lines) and SELFTEST_ROW.fullmatch(lines[at]):
        rows.append(SELFTEST_ROW.fullmatch(lines[at]))
        at += 1
    if at == len(lines) or not T.SELFTEST_SUMMARY.fullmatch(lines[at]):
        got = repr(lines[at]) if at < len(lines) else "the end of the output"
        return [f"selftest: expected the summary after {len(rows)} row(s), got {got}"]
    summary = T.SELFTEST_SUMMARY.fullmatch(lines[at])
    at += 1
    controls, fired = int(summary.group("controls")), int(summary.group("fired"))
    broken, skipped = int(summary.group("broken")), int(summary.group("skipped"))
    if controls != len(rows):
        problems.append(f"selftest: the summary counts {controls} control(s), "
                        f"{len(rows)} row(s) printed")
    if fired + broken + skipped != controls:
        problems.append(f"selftest: {summary.group(0)!r} does not add up")
    if fired != sum(1 for row in rows if row.group("tag") == "ok  "):
        problems.append("selftest: the fired count is not the [ok  ] rows")
    if fired + broken == 0:
        at = _grammar(lines, at, (("why nothing ran", SELFTEST_EMPTY, 1, 1),),
                      problems, "selftest")
    if broken:
        at = _grammar(lines, at, (("the refusal", SELFTEST_BROKEN_HEAD, 1, 1),
                                  ("a broken control", SELFTEST_BROKEN_ROW, broken, broken)),
                      problems, "selftest")
    _trailing(lines, at, problems, "selftest")
    return problems


#: Pack mode's per-pack line (`cli._pack_line`): the tag, the pack and where it
#: came from, then its counts — or why it ran nothing.
PACK_ROW = re.compile(r"^\[(?P<tag>.{4})\] (?P<pack>\S+) \((?P<origin>[^()]+)\) : (?P<body>.+)$")
PACK_COUNTS = re.compile(
    r"^(?P<fired>\d+) fired(?:, (?P<broken>\d+) BROKEN)?"
    r"(?:, (?P<skipped>\d+) skipped \(tooling\))?(?:, (?P<failed>\d+) baseline\(s\) failed)?$")
PACK_NOTHING = re.compile(r"^no gates?(?: at or below tier \d+)?$")
#: A tooling note pack mode prints before its rows (what did not run here, and why).
PACK_NOTE = re.compile(r"^note: .+$")
#: One broken control or failed baseline under pack mode's refusal heads.
PACK_FAILED_ROW = re.compile(r"^\[FAIL\] \S+.* — .+$")


def pack_selftest_problems(stdout: str) -> list[str]:
    """`gate selftest` in pack mode (no project here; spec §3.13, U08): tooling
    notes, one row per pack, the summary — then, only when something failed, the
    refusal and its rows. The summary's counts must be the rows' sums, so a pack
    row that lost a control cannot hide under a summary that kept it."""
    lines = stdout.splitlines()
    problems: list[str] = []
    at = 0
    while at < len(lines) and PACK_NOTE.fullmatch(lines[at]):
        at += 1
    sums = {"fired": 0, "broken": 0, "skipped": 0, "failed": 0}
    rows = 0
    while at < len(lines) and PACK_ROW.fullmatch(lines[at]):
        body = PACK_ROW.fullmatch(lines[at]).group("body")
        counts = PACK_COUNTS.fullmatch(body)
        if counts:
            for key in sums:
                sums[key] += int(counts.group(key) or 0)
        elif not PACK_NOTHING.fullmatch(body):
            problems.append(f"pack selftest: line {at + 1} is not a pack's counts: {body!r}")
        rows += 1
        at += 1
    if not rows:
        problems.append("pack selftest: no pack row")
    if at == len(lines) or not T.SELFTEST_SUMMARY.fullmatch(lines[at]):
        got = repr(lines[at]) if at < len(lines) else "the end of the output"
        return problems + [f"pack selftest: expected the summary after {rows} pack row(s), "
                           f"got {got}"]
    summary = T.SELFTEST_SUMMARY.fullmatch(lines[at])
    at += 1
    for key, group in (("fired", "fired"), ("broken", "broken"), ("skipped", "skipped")):
        if int(summary.group(group)) != sums[key]:
            problems.append(f"pack selftest: the summary says {summary.group(group)} {key}, "
                            f"the pack rows {sums[key]}")
    if int(summary.group("controls")) != sums["fired"] + sums["broken"] + sums["skipped"]:
        problems.append(f"pack selftest: {summary.group(0)!r} does not add up")
    if sums["broken"]:
        at = _grammar(lines, at, (("the refusal", SELFTEST_BROKEN_HEAD, 1, 1),
                                  ("a broken control", PACK_FAILED_ROW,
                                   sums["broken"], sums["broken"])),
                      problems, "pack selftest")
    if sums["failed"]:
        at = _grammar(lines, at, (("the failed-baseline head", T.BASELINES_FAILED, 1, 1),
                                  ("a failed baseline", PACK_FAILED_ROW, 1, None)),
                      problems, "pack selftest")
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
        self.assertTrue(T.LAST_SELFTEST_FIRED.fullmatch(text.splitlines()[-1]), text)

    def test_a_line_added_is_refused(self):
        text = self.out("gate-show", 0)
        for where, after in (("after the tier line", SHOW_TIER.fullmatch),
                             ("after the last verdict", SHOW_VERDICT.fullmatch)):
            with self.subTest(where=where):
                self.refuses(add_line(text, after), f"prose {where}")
        self.refuses(text + PROSE + "\n", "prose after the last selftest")

    def test_the_control_removed_is_refused(self):
        text = self.out("gate-show", 0)
        self.refuses(sub_line(text, T.LAST_SELFTEST_FIRED, r" \(control [0-9a-f]{12}\)$", ""),
                     "a last selftest that names no control entry")


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
        self.assertEqual(sum(1 for line in text.splitlines() if SELFTEST_ROW.fullmatch(line)), 6)

    def test_a_line_added_is_refused(self):
        text = self.out("selftest", 0)
        self.refuses(add_line(text, SELFTEST_ROW.fullmatch), "prose between the rows")
        self.refuses(text + PROSE + "\n", "prose after the summary")

    def test_the_time_removed_is_refused(self):
        text = self.out("selftest", 0)
        self.refuses(sub_line(text, T.SELFTEST_SUMMARY, r" in \S+:", ":"),
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

#: `check` after the `bed_xy` edit: rows in registration order (the plan's
#: transcript lists the executed row first; the CLI streams rows as the gates
#: come), one gate executed, and the six controls re-verified by their fixtures
#: alone — none executed, none written (spec §3.8's E4 consequence).
CHECK_AFTER_EDIT = (
    ("the cached FAIL", _CACHED_DEFLECTION),
    ("the one executed row", r"^\[ok  \] bracket\.bed_fit : 74 x 30 x 7 mm vs 234 mm usable "
                             r"\(250 bed - 2x8 brim\)$"),
    ("the summary", r"^6 gates: 1 executed, 5 cached — 5 ok, 1 FAIL — tier 0$"),
    ("the controls line", r"^controls: 0 executed, 0 cached, 6 re-verified$"),
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
                    PROSE, "controls: 6 executed, 0 cached, 0 re-verified"))):
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
                                                r"^controls: 0 executed, 0 cached, 6",
                                                "controls: 1 executed, 0 cached, 5")),
                ("the controls line dropped", "\n".join(
                    line for line in text.splitlines()
                    if not T.CONTROLS_SUMMARY.fullmatch(line)) + "\n"),
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
        summary = T.SELFTEST_SUMMARY.fullmatch(text.splitlines()[-1])
        self.assertIsNotNone(summary, "the summary is not the last line")
        self.assertEqual(summary.group("broken"), "0")
        row = next(line for line in text.splitlines() if PACK_ROW.fullmatch(line))
        for label, mutant in (
                ("the time removed", sub_line(text, T.SELFTEST_SUMMARY, r" in \S+:", ":")),
                ("prose after the summary", text + PROSE + "\n"),
                ("a pack row the summary disagrees with", text.replace(
                    row, re.sub(r"(\d+) fired", lambda m: f"{int(m.group(1)) + 1} fired", row),
                    1)),
                ("a BROKEN count with no refusal", sub_line(text, T.SELFTEST_SUMMARY,
                                                            r", 0 BROKEN", ", 1 BROKEN")),
                ("no pack row", "\n".join(line for line in text.splitlines()
                                          if not PACK_ROW.fullmatch(line)) + "\n")):
            with self.subTest(label):
                self.assertTrue(pack_selftest_problems(mutant), f"accepted {label}:\n{mutant}")

    def test_check_junit_prints_the_check_and_writes_the_xml(self):
        text = self.out("check-junit", 1)
        self.holds(exact_problems(text, FIRST_CHECK, "check --junit"), text)
        result = T.Result("atompipe check --junit", 1, text, "", self.steps["project"])
        self.assertIsNone(T.problem(T.junit(), result))
        self.assertIsNotNone(T.problem(T.junit(), result._replace(
            cwd=os.path.join(self.steps["project"], "elsewhere"))))


if __name__ == "__main__":
    unittest.main()
