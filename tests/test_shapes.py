# SPDX-License-Identifier: Apache-2.0
"""Every human-facing output is a fixed shape, pinned, with its own negative control (R-11).

A shape nothing pins drifts into prose, and prose is where a fact turns into an
opinion: a `status` that grows a friendly summary line, a `check` whose cached
FAIL stops saying `cached`, a `last check:` that loses its age. So each command
below has one matcher — a pure function of the command's stdout that returns
what is wrong with it, line by line — built from the shapes in
`tests/_transcript.py` (spec §3.13), and each matcher is run twice:

* on a REAL transcript: a copy of the bracket, driven through the transcript's
  own steps (`check` twice, the `bed_xy` edit, `status`, `check`, `gate show`,
  `gate selftest`), which it must accept; and
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
    r"(?: — \d+ of them stale)?$")

#: `check`'s last line when nothing critical blocks, or when there are no claims.
READY = re.compile(r"^ready: no critical claim is blocking .+$")
NO_CLAIMS = re.compile(r"^no claims recorded, so nothing was checked — .+$")

#: The readiness block `report.render_terminal` prints at the top of `status`, in
#: its order: the head, one sentence, the counts, the unsettled claims (and how
#: many more), the gaps, the gate tally, what constrains the project, `next:`.
STATUS_HEAD = (
    ("head", re.compile(r"^atompipe readiness — .+$"), 1, 1),
    ("sentence", re.compile(r"^\S.*$"), 1, 1),
    ("counts", re.compile(r"^claims \d+(?: — .+)?$"), 1, 1),
    ("claim", re.compile(r"^\[.{5}\] \S+ .+ — .+$"), 0, None),
    ("more claims", re.compile(r"^       \.\.\. and \d+ more unsettled claims — .+$"), 0, 1),
    ("gaps", re.compile(r"^gaps \d+:$"), 0, 1),
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
        steps["gate-show"] = run("gate", "show", "bracket.deflection")
        steps["selftest"] = run("gate", "selftest")
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
        for where, after in (("in the head", lambda line: line.startswith("claims ")),
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
                             "`last check:` without its age")


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


if __name__ == "__main__":
    unittest.main()
