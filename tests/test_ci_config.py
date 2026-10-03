# SPDX-License-Identifier: Apache-2.0
"""CI that cannot swallow a crash, and runs the merge check where the docs say.

What slipped through, each read straight off `.github/workflows/ci.yml` as it
stood at the start of Phase 1:

* **S-83 — the reference project's exit code was swallowed.** The bracket fails
  on purpose (thickness 7.0), so CI ran `atompipe check || true`, and `init ...
  || true` before it. `|| true` cannot tell the intended failure from a crash
  (exit 2) or from a second, unintended failure: all three read green. The same
  step ran `init` and `model --set-entry` in the TRACKED `examples/bracket`, so
  every CI run rewrote files the repository commits.
* **S-11 — the docs said CI runs every pack's controls; it ran none.**
  `docs/PACK_FORMAT.md` said "CI runs this plus `atompipe gate selftest` over
  every pack", and `CONTRIBUTING.md` that "CI runs every control". CI ran
  `gate selftest` once, inside the bracket, which loads no pack at all.

So the bracket now runs in a temp copy, its exit code is captured and compared,
together with its JUnit report, against one pinned signature
(`tests/expected_bracket.json`); and `gate selftest --junit` runs at the repo
root, where it demonstrates every bundled pack (pack mode, U08).

The workflow is read without a YAML library — CI installs nothing, and neither
may the test that reads it. The reader understands the shape this file uses
(block-style steps, `run: |` scalars, heredocs); each checker is a pure function
of text with planted violators it must refuse.

Run:  PYTHONPATH=src python3 -m unittest tests.test_ci_config -v
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import re
import shlex
import shutil
import sys
import textwrap
import unittest

import _env

CI = os.path.join(_env.REPO, ".github", "workflows", "ci.yml")
BRACKET = os.path.join(_env.REPO, "examples", "bracket")
SIGNATURE = os.path.join(_env.REPO, "tests", "oracle", "bracket_signature.py")
EXPECTED = os.path.join(_env.REPO, "tests", "expected_bracket.json")

#: The bracket step as it stood at the start of Phase 1 (`ci.yml:71-81` at
#: 7ecf953), verbatim inside a minimal job: the planted violator for every
#: structural check below, because a checker that cannot refuse the file that
#: slipped through has not been shown to refuse anything.
OLD_BRACKET_STEP = """\
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Reference project runs, and its gates prove they can fail
        run: |
          cd examples/bracket
          PYTHONPATH=../../src python -m atompipe init --name bracket-ci --summary "CI run" || true
          PYTHONPATH=../../src python -m atompipe model --set-entry model/bracket.py
          # The default model is deliberately marginal: one gate FAILS, so `check`
          # exits 1 here and that is the expected result, not a broken build.
          PYTHONPATH=../../src python -m atompipe check || true
          # This one must pass. A gate that passes its own known-bad fixture is a
          # logger, and merging one is the failure this whole project exists to stop.
          PYTHONPATH=../../src python -m atompipe gate selftest
"""


# --------------------------------------------------------------------------- #
# The workflow reader
# --------------------------------------------------------------------------- #
def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def steps(yaml_text: str) -> list[dict]:
    """Every step under every ``steps:`` key, in order.

    Each step is ``{"line", "keys", "run"}``: ``keys`` maps a step key to its
    inline value, and ``run`` is ``[(line number, text)]`` for its script (a
    block scalar's lines with the block indentation removed, or the one inline
    line).
    """
    lines = yaml_text.splitlines()
    out: list[dict] = []
    i = 0
    while i < len(lines):
        match = re.match(r"^(\s*)steps:\s*$", lines[i])
        i += 1
        if not match:
            continue
        base = len(match.group(1))
        item: int | None = None
        step: dict | None = None
        while i < len(lines):
            line = lines[i]
            if line.strip() and _indent(line) <= base:
                break
            dash = re.match(r"^(\s*)-\s+(.*)$", line)
            if dash and (item is None or len(dash.group(1)) == item):
                item = len(dash.group(1))
                step = {"line": i + 1, "keys": {}, "run": []}
                out.append(step)
                i = _step_key(lines, i, step, item + 2, dash.group(2))
                continue
            if step is not None and line.strip() and _indent(line) == item + 2:
                i = _step_key(lines, i, step, item + 2, line.strip())
                continue
            i += 1
    return out


def _step_key(lines: list[str], i: int, step: dict, key_indent: int, text: str) -> int:
    """Read one ``key: value`` of a step starting at line ``i``; return the next line."""
    match = re.match(r"^([\w-]+):\s*(.*)$", text)
    if not match:
        return i + 1
    key, value = match.group(1), match.group(2).strip()
    step["keys"][key] = value
    i += 1
    if key == "run" and re.match(r"^[|>][-+0-9]*$", value):
        block: list[tuple[int, str]] = []
        while i < len(lines) and (not lines[i].strip() or _indent(lines[i]) > key_indent):
            block.append((i + 1, lines[i]))
            i += 1
        while block and not block[-1][1].strip():
            block.pop()
        width = min((_indent(t) for _, t in block if t.strip()), default=0)
        step["run"] = [(n, t[width:]) for n, t in block]
    elif key == "run":
        step["run"] = [(i, value)]
    else:
        while i < len(lines) and lines[i].strip() and _indent(lines[i]) > key_indent:
            i += 1                              # a nested map (`with:`, `env:`)
    return i


def shell_lines(run: list[tuple[int, str]]) -> list[tuple[int, str]]:
    """The shell a ``run`` block executes: comments dropped, continuations joined,
    heredoc bodies (another language's source) left out."""
    out: list[tuple[int, str]] = []
    heredoc: str | None = None
    pending: tuple[int, str] | None = None
    for number, raw in run:
        if heredoc is not None:
            if raw.strip() == heredoc:
                heredoc = None
            continue
        text = _strip_comment(raw).rstrip()
        if pending is not None:
            number, text = pending[0], pending[1] + " " + text.strip()
            pending = None
        if text.endswith("\\"):
            pending = (number, text[:-1].rstrip())
            continue
        tag = re.search(r"<<-?\s*(['\"]?)(\w+)\1", text)
        if tag:
            heredoc = tag.group(2)
        if text.strip():
            out.append((number, text.strip()))
    if pending is not None:
        out.append(pending)
    return out


def _strip_comment(text: str) -> str:
    quote = None
    for index, char in enumerate(text):
        if quote:
            if char == quote:
                quote = None
        elif char in "'\"":
            quote = char
        elif char == "#" and (index == 0 or text[index - 1].isspace()):
            return text[:index]
    return text


def commands(line: str) -> list[tuple[str, str]]:
    """``[(operator before it, command)]`` for one shell line, split outside quotes
    on ``&&``, ``||``, ``;``, ``|`` and ``&``. The first operator is ``""``."""
    out: list[tuple[str, str]] = []
    quote = None
    start = 0
    op = ""
    index = 0
    while index < len(line):
        char = line[index]
        if quote:
            if char == quote:
                quote = None
            index += 1
            continue
        if char in "'\"":
            quote = char
            index += 1
            continue
        if char == "&" and ((index and line[index - 1] in "<>")
                            or line.startswith("&>", index)):
            index += 1                          # `2>&1`, `&>file`: a redirect, not a job
            continue
        for candidate in ("&&", "||", ";", "|", "&"):
            if line.startswith(candidate, index):
                out.append((op, line[start:index].strip()))
                op = candidate
                index += len(candidate)
                start = index
                break
        else:
            index += 1
    out.append((op, line[start:].strip()))
    return out


def _words(command: str) -> list[str]:
    try:
        return shlex.split(command)
    except ValueError:
        return command.split()


def atompipe_args(command: str) -> list[str] | None:
    """The arguments after ``atompipe`` when ``command`` runs it, else None."""
    words = _words(command)
    for index, word in enumerate(words):
        if word == "atompipe" and (index == 0 or words[index - 1] == "-m"):
            return words[index + 1:]
    return None


def _runs_a_check(command: str) -> bool:
    """A command whose exit code is a verdict: atompipe, python, the oracle."""
    return bool(re.search(r"\b(atompipe|python3?|unittest)\b", command))


_CAPTURE = re.compile(r"^([A-Za-z_]\w*)=\$\?$")


# --------------------------------------------------------------------------- #
# NoSwallowedExitCodes
# --------------------------------------------------------------------------- #
def swallows(yaml_text: str) -> list[str]:
    """Every route by which a failing command in the workflow reads as a pass.

    Under GitHub's default ``bash -e``, a command's failure ends the step, except:
    after ``||`` (the right side runs instead: ``|| true``, ``|| :``, ``|| exit
    0``, ``|| echo``); in a ``&&`` list anywhere but last; negated with ``!``;
    backgrounded with ``&``; piped without ``pipefail`` (the pipe's status is the
    last command's); after ``set +e``; or under ``continue-on-error``. The one
    permitted ``||`` is an exit-code capture, ``|| rc=$?``, whose variable a later
    line of the same step hands on (``--exit-code "$rc"`` or ``exit "$rc"``): a
    capture nobody reads is ``|| true`` spelled longer.
    """
    findings: list[str] = []
    for number, line in enumerate(yaml_text.splitlines(), 1):
        match = re.match(r"^\s*-?\s*continue-on-error:\s*(.*)$", line)
        if match and match.group(1).strip().lower() not in ("false", "'false'", '"false"'):
            findings.append(f"ci.yml:{number}: continue-on-error: {match.group(1).strip()}")
    for step in steps(yaml_text):
        lines = shell_lines(step["run"])
        pipefail = False
        for position, (number, line) in enumerate(lines):
            where = f"ci.yml:{number}"
            if re.search(r"\bset\s+(-\w*o\s+pipefail|-o\s+pipefail)", line):
                pipefail = True
            if re.search(r"\bset\s+\+\w*e|\bset\s+\+o\s+errexit", line):
                findings.append(f"{where}: `set +e` — every later failure in the step is ignored")
            parts = commands(line)
            later = " ".join(text for _, text in lines[position + 1:])
            for index, (op, command) in enumerate(parts):
                following = parts[index + 1][0] if index + 1 < len(parts) else ""
                if op == "||":
                    capture = _CAPTURE.match(command)
                    if not capture:
                        findings.append(f"{where}: `|| {command}` swallows the exit code of "
                                        f"`{parts[index - 1][1]}`")
                    elif not re.search(
                            r"(--exit-code|\bexit)\s+\"?\$\{?%s\}?\"?" % capture.group(1), later):
                        findings.append(f"{where}: `|| {command}` captures an exit code no "
                                        f"later line hands on — `|| true`, spelled longer")
                if not _runs_a_check(command) or op == "||":
                    continue
                if command.startswith("!"):
                    findings.append(f"{where}: `{command}` — a negated command never fails the step")
                if following == "&&":
                    findings.append(f"{where}: `{command} &&` — a failure before `&&` "
                                    f"does not end a `bash -e` step")
                if following == "&":
                    findings.append(f"{where}: `{command} &` — a background job's exit is lost")
                if following == "|" and not pipefail:
                    findings.append(f"{where}: `{command} | ...` — without pipefail the "
                                    f"pipe's exit is the last command's")
    return findings


class NoSwallowedExitCodes(unittest.TestCase):
    def test_no_or_true_on_atompipe_lines(self):
        """No `|| true` on any atompipe line of ci.yml — nor on any other line,
        nor any of the other routes `swallows` names: the unit tests and the pack
        validation are verdicts too."""
        text = _read(CI)
        checked = [line for step in steps(text) for _, line in shell_lines(step["run"])
                   if any(atompipe_args(c) is not None for _, c in commands(line))]
        self.assertGreaterEqual(len(checked), 3,
                                f"found {len(checked)} atompipe lines in ci.yml — the "
                                f"reader has gone blind, or CI stopped running atompipe")
        found = swallows(text)
        self.assertEqual(found, [], "ci.yml can read green on a failure:\n  "
                         + "\n  ".join(found))

    def test_planted_swallows_are_caught(self):
        """V: each route, planted into a step."""
        planted = {
            "|| true": "PYTHONPATH=src python -m atompipe check || true",
            "|| :": "python -m atompipe check || :",
            "|| exit 0": "python -m atompipe check || exit 0",
            "|| echo": "python -m atompipe check || echo 'expected failure'",
            "a capture nobody reads": "rc=0\npython -m atompipe check || rc=$?\necho done",
            "set +e": "set +e\npython -m atompipe check",
            "negated": "! python -m atompipe check",
            "&& not last": "python -m atompipe check && echo ok\necho next",
            "a pipe": "python -m atompipe check | tee check.log",
            "a background job": "python -m atompipe gate selftest &",
            "a continuation": "python -m atompipe check \\\n  --junit || true",
            "the old bracket step": None,
        }
        for label, script in planted.items():
            with self.subTest(planted=label):
                yaml_text = OLD_BRACKET_STEP if script is None else _workflow(script)
                self.assertTrue(swallows(yaml_text), f"{label}: not caught")
        yaml_text = _workflow("echo hi\n") + "        continue-on-error: true\n"
        self.assertTrue(swallows(yaml_text), "continue-on-error: not caught")

    def test_honest_captures_are_not_swallows(self):
        """C: the capture CI uses, a heredoc's own `||`, and pipefail."""
        honest = [
            'rc=0\npython -m atompipe -C "$copy" check --junit || rc=$?\n'
            'python tests/oracle/bracket_signature.py x.xml e.json --exit-code "$rc"',
            "rc=0\npython -m atompipe check || rc=$?\nexit $rc",
            "python - <<'PY'\nfailed = a || b\nPY",
            "set -o pipefail\npython -m atompipe check | tee check.log",
            'cd "$copy" && python -m atompipe check',
        ]
        for script in honest:
            with self.subTest(script=script):
                self.assertEqual(swallows(_workflow(script)), [])


def _workflow(script: str) -> str:
    """One job, one step, running ``script``."""
    body = textwrap.indent(script, " " * 10)
    return ("jobs:\n  test:\n    runs-on: ubuntu-latest\n    steps:\n"
            "      - uses: actions/checkout@v4\n"
            "      - name: planted\n        run: |\n" + body + "\n")


# --------------------------------------------------------------------------- #
# CiRunsWhatTheDocsSay
# --------------------------------------------------------------------------- #
def atompipe_calls(yaml_text: str) -> list[dict]:
    """Every atompipe invocation: ``{"line", "args", "path", "cwd", "step"}``.

    ``cwd`` is ``"."`` for the repository root, else the directory the call runs
    against — from the step's ``working-directory``, an earlier ``cd`` in the
    same step, or the call's own ``-C``/``--dir``. ``path`` is the subcommand
    path (``"gate selftest"``).
    """
    calls: list[dict] = []
    for index, step in enumerate(steps(yaml_text)):
        cwd = step["keys"].get("working-directory", "").strip("'\"") or "."
        for number, line in shell_lines(step["run"]):
            for _, command in commands(line):
                words = _words(command)
                if words[:1] in (["cd"], ["pushd"]):
                    cwd = words[1] if len(words) > 1 else "~"
                    continue
                args = atompipe_args(command)
                if args is None:
                    continue
                where = cwd
                rest = list(args)
                while rest and rest[0].startswith("-"):
                    flag = rest.pop(0)
                    if flag in ("-C", "--dir") and rest:
                        where = rest.pop(0)
                    elif flag.startswith("--dir="):
                        where = flag.split("=", 1)[1]
                path = [w for w in rest[:2] if not w.startswith("-")]
                if path[:1] != ["gate"] and path[:1] != ["pack"] and path[:1] != ["packs"]:
                    path = path[:1]
                calls.append({"line": number, "args": args, "path": " ".join(path),
                              "cwd": where, "step": index, "command": command})
    return calls


def root_selftests(yaml_text: str) -> list[dict]:
    """`gate selftest --junit` calls at the repo root with no `--pack`: pack mode
    over every bundled pack, which is what the docs say CI runs (S-11)."""
    return [c for c in atompipe_calls(yaml_text)
            if c["path"] == "gate selftest" and c["cwd"] == "."
            and _has_flag(c["args"], "--junit") and not _has_flag(c["args"], "--pack")]


def tracked_bracket_problems(yaml_text: str) -> list[str]:
    """What runs against, or rewrites, the TRACKED reference project (S-83)."""
    problems: list[str] = []
    for call in atompipe_calls(yaml_text):
        where = f"ci.yml:{call['line']}"
        if os.path.normpath(call["cwd"]).replace(os.sep, "/").startswith("examples"):
            problems.append(f"{where}: `atompipe {call['path']}` runs in the tracked "
                            f"{call['cwd']} — copy it to a temp dir first")
        if call["path"] == "init":
            problems.append(f"{where}: `atompipe init` — the bracket is already a project")
        if call["path"] == "model" and _has_flag(call["args"], "--set-entry"):
            problems.append(f"{where}: `model --set-entry` rewrites a tracked record")
    return problems


def signature_problems(yaml_text: str) -> list[str]:
    """The bracket must run as `check --junit` in a temp copy, its exit code
    captured and handed with the report to the signature oracle, and its
    controls must run in the same copy (`gate selftest --junit`)."""
    problems: list[str] = []
    calls = atompipe_calls(yaml_text)
    checks = [c for c in calls if c["path"] == "check" and _has_flag(c["args"], "--junit")
              and c["cwd"] != "."]
    if not checks:
        return ["no `atompipe check --junit` runs against a copy of the bracket"]
    for check in checks:
        step = steps(yaml_text)[check["step"]]
        script = "\n".join(text for _, text in shell_lines(step["run"]))
        copy = re.escape(check["cwd"])
        if not re.search(r'=\s*"?\$\(mktemp -d\)', script):
            problems.append(f"ci.yml:{check['line']}: the copy is not made under `mktemp -d`")
        if not re.search(r"cp -[rR]\w* examples/bracket\b", script):
            problems.append(f"ci.yml:{check['line']}: examples/bracket is never copied")
        capture = re.search(re.escape(check["command"]) + r"\s*\|\|\s*(\w+)=\$\?", script)
        if not capture:
            problems.append(f"ci.yml:{check['line']}: `check`'s exit code is not captured")
            continue
        oracle = re.search(
            r"tests/oracle/bracket_signature\.py\s+\"?%s/\.atompipe/out/junit\.xml\"?\s+"
            r"tests/expected_bracket\.json\s+--exit-code\s+\"?\$\{?%s\}?\"?"
            % (copy, capture.group(1)), script)
        if not oracle:
            problems.append(f"ci.yml:{check['line']}: the report and the captured exit "
                            f"code never reach tests/oracle/bracket_signature.py")
        if not any(c["path"] == "gate selftest" and c["cwd"] == check["cwd"]
                   and _has_flag(c["args"], "--junit") for c in calls):
            problems.append(f"ci.yml:{check['line']}: `gate selftest --junit` does not "
                            f"run in the same copy")
    return problems


def _has_flag(args: list[str], flag: str) -> bool:
    return any(a == flag or a.startswith(flag + "=") for a in args)


def force_problems(yaml_text: str) -> list[str]:
    """R-9: the bracket's `check --junit` in CI re-executes every gate and its
    control (`--force`) rather than serving the committed cache.

    What would slip through without it: from 1.2 a check serves the tracked
    verdict cache, and an entry is only as honest as whoever committed it — a
    hand-placed entry, or one written by a spine with a bug since fixed, would
    read green in CI forever without a single gate running. The inner loop may
    trust the cache; the money boundary re-runs it (PLAN R-9)."""
    checks = [c for c in atompipe_calls(yaml_text)
              if c["path"] == "check" and _has_flag(c["args"], "--junit") and c["cwd"] != "."]
    if not checks:
        return ["no `atompipe check --junit` runs against a copy of the bracket"]
    return [f"ci.yml:{c['line']}: `atompipe check --junit` without `--force` serves the "
            f"committed cache instead of re-running it (R-9)"
            for c in checks if not _has_flag(c["args"], "--force")]


class CiRunsWhatTheDocsSay(unittest.TestCase):
    def test_gate_selftest_runs_at_the_repo_root(self):
        """S-11: every bundled pack's controls run in CI, as the docs say they do."""
        self.assertTrue(root_selftests(_read(CI)),
                        "ci.yml never runs `atompipe gate selftest --junit` at the repo "
                        "root, so no bundled pack's control runs in CI (S-11)")

    def test_the_bracket_runs_against_its_signature(self):
        """S-83: the exit code is compared, never swallowed, in a temp copy."""
        problems = signature_problems(_read(CI))
        self.assertEqual(problems, [], "\n".join(problems))

    def test_ci_never_touches_the_tracked_bracket(self):
        problems = tracked_bracket_problems(_read(CI))
        self.assertEqual(problems, [], "\n".join(problems))

    def test_the_old_step_is_refused(self):
        """V: each structural check refuses the step that slipped through."""
        self.assertEqual(root_selftests(OLD_BRACKET_STEP), [])
        self.assertTrue(signature_problems(OLD_BRACKET_STEP))
        problems = "\n".join(tracked_bracket_problems(OLD_BRACKET_STEP))
        for needle in ("examples/bracket", "atompipe init", "--set-entry"):
            self.assertIn(needle, problems)

    def test_a_selftest_elsewhere_is_not_at_the_root(self):
        """V: a `cd`, a `-C`, a `working-directory` or a `--pack` each move the
        selftest off 'every bundled pack at the root'."""
        for script in ("cd packs && python -m atompipe gate selftest --junit",
                       'python -m atompipe -C "$copy" gate selftest --junit',
                       "python -m atompipe gate selftest --junit --pack beam-analytic",
                       "python -m atompipe gate selftest"):
            with self.subTest(script=script):
                self.assertEqual(root_selftests(_workflow(script)), [])
        moved = _workflow("python -m atompipe gate selftest --junit").replace(
            "        run: |", "        working-directory: examples\n        run: |")
        self.assertEqual(root_selftests(moved), [])
        self.assertTrue(root_selftests(_workflow("python -m atompipe gate selftest --junit")))


class CiReExecutesTheCache(unittest.TestCase):
    """R-9 at the money boundary CI is: the bracket's check re-runs every gate
    and its control (`check --force`), so a committed cache entry is re-proven
    on every push instead of trusted."""

    def test_the_bracket_check_runs_with_force(self):
        problems = force_problems(_read(CI))
        self.assertEqual(problems, [], "\n".join(problems))

    def test_a_check_without_force_is_refused(self):
        """V: the step as it stood at 1.1 — a copy, the exit code captured, the
        signature compared — still serves the cache, and is refused for it."""
        text = _read(CI)
        stripped = re.sub(r"(atompipe -C \"\$copy\" check) --force", r"\1", text)
        self.assertTrue(stripped != text, "the bracket step no longer reads "
                        "`atompipe -C \"$copy\" check --force ...`; update the planted form")
        self.assertTrue(force_problems(stripped))
        self.assertEqual(signature_problems(stripped), [],
                         "the planted step must differ from the real one in --force only")
        self.assertTrue(force_problems(OLD_BRACKET_STEP))


# --------------------------------------------------------------------------- #
# SignatureDetectsAMissingFailure
# --------------------------------------------------------------------------- #
class SignatureDetectsAMissingFailure(_env.EnvCase):
    """The oracle must notice the intended failure going AWAY, not only a new
    one arriving: a regression that makes `bracket.deflection` pass would turn
    the reference project's one deliberate red light off, and `|| true` read
    that as green too. At thickness 8.0 the tip sags 0.47 mm (limit 0.5 mm), so
    the gate passes and C1 stops blocking while C7 still does — the exit code is
    still 1, and only the signature can tell."""

    def _run(self, thickness: str | None):
        self.assertTrue(os.path.isfile(SIGNATURE), f"{SIGNATURE} does not exist")
        self.assertTrue(os.path.isfile(EXPECTED), f"{EXPECTED} does not exist")
        project = os.path.join(self.tmp(), "bracket")
        shutil.copytree(BRACKET, project, ignore=shutil.ignore_patterns("__pycache__", "out"))
        if thickness is not None:
            model = os.path.join(project, "model", "bracket.py")
            source = _read(model)
            self.assertEqual(source.count("thickness: float = 7.0"), 1)
            with open(model, "w", encoding="utf-8") as fh:
                fh.write(source.replace("thickness: float = 7.0",
                                        f"thickness: float = {thickness}"))
        check = _env.atompipe(["check", "--junit"], cwd=project)
        junit = os.path.join(project, ".atompipe", "out", "junit.xml")
        oracle = _env.run([sys.executable, SIGNATURE, junit, EXPECTED,
                           "--exit-code", str(check.returncode)], cwd=_env.REPO)
        return check, oracle

    def test_thickness_8_is_a_missing_failure(self):
        """V: exit non-zero — and 1, the "differs" code, not 2 ("could not read")."""
        check, oracle = self._run("8.0")
        self.assertEqual(check.returncode, 1, check.stdout + check.stderr)
        self.assertEqual(oracle.returncode, 1, oracle.stdout + oracle.stderr)
        self.assertIn("bracket.deflection", oracle.stdout)
        self.assertIn("C1", oracle.stdout)

    def test_thickness_7_matches(self):
        """C: the tracked design, unchanged, is exactly the pinned signature."""
        check, oracle = self._run(None)
        self.assertEqual(check.returncode, 1, check.stdout + check.stderr)
        self.assertEqual(oracle.returncode, 0, oracle.stdout + oracle.stderr)


# --------------------------------------------------------------------------- #
# SignatureIsExact — the oracle's own negative controls, on planted reports
# --------------------------------------------------------------------------- #
def _oracle():
    spec = importlib.util.spec_from_file_location("bracket_signature", SIGNATURE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _report(*, exit_code: str = "1", deflection: str = '<failure type="fail" message="m"/>',
            extra_gate: str = "",
            c5: str = '<skipped message="pending build: needs an article; no test written down"/>'
            ) -> str:
    return textwrap.dedent(f"""\
        <?xml version="1.0" encoding="UTF-8"?>
        <testsuites name="atompipe check">
          <properties><property name="exit_code" value="{exit_code}"/></properties>
          <testsuite name="gates">
            <testcase classname="project" name="bracket.deflection">{deflection}</testcase>
            <testcase classname="project" name="bracket.bending_stress"/>
            {extra_gate}
          </testsuite>
          <testsuite name="claims.critical">
            <testcase classname="claims.critical" name="C1"><failure type="fail" message="m"/></testcase>
            <testcase classname="claims.critical" name="C2"/>
            <testcase classname="claims.critical" name="C5">{c5}</testcase>
            <testcase classname="claims.critical" name="C6"><failure type="unclaimed" message="m"/></testcase>
            <testcase classname="claims.critical" name="C7"><failure type="unclaimed" message="m"/></testcase>
          </testsuite>
          <testsuite name="claims.not-critical"/>
        </testsuites>
        """)


class SignatureIsExact(_env.EnvCase):
    def _main(self, xml: str | None, exit_code: int, expected: str | None = None) -> tuple[int, str]:
        self.assertTrue(os.path.isfile(SIGNATURE), f"{SIGNATURE} does not exist")
        tmp = self.tmp()
        junit = os.path.join(tmp, "junit.xml")
        if xml is not None:
            with open(junit, "w", encoding="utf-8") as fh:
                fh.write(xml)
        wanted = EXPECTED
        if expected is not None:
            wanted = os.path.join(tmp, "expected.json")
            with open(wanted, "w", encoding="utf-8") as fh:
                fh.write(expected)
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = _oracle().main([junit, wanted, "--exit-code", str(exit_code)])
        return code, out.getvalue()

    def test_the_planted_report_matches(self):
        """C: the planted report below is the signature, so each V differs by one thing."""
        code, out = self._main(_report(), 1)
        self.assertEqual(code, 0, out)

    def test_each_difference_is_reported(self):
        cases = {
            "a crash": (_report(exit_code="2"), 2, "exit_code"),
            "the file disagrees with the process": (_report(exit_code="0"), 1, "exit_code"),
            "the failure became an error": (
                _report(deflection='<error type="error" message="boom"/>'), 1,
                "bracket.deflection"),
            "a second failure": (
                _report(extra_gate='<testcase classname="project" name="bracket.bearing">'
                                   '<failure type="fail" message="m"/></testcase>'), 1,
                "bracket.bearing"),
            "a gate skipped": (
                _report(extra_gate='<testcase classname="project" name="bracket.bed_fit">'
                                   '<skipped message="requires trimesh"/></testcase>'), 1,
                "bracket.bed_fit"),
            "a claim's reason changed": (_report(c5='<skipped message="assumed"/>'), 1, "C5"),
        }
        for label, (xml, exit_code, needle) in cases.items():
            with self.subTest(case=label):
                code, out = self._main(xml, exit_code)
                self.assertEqual(code, 1, out)
                self.assertIn(needle, out)

    def test_an_unreadable_report_is_not_a_match(self):
        """No file is what a crash leaves (`--junit` unlinks first), and it must
        never compare equal to anything; nor may a file that does not parse."""
        for xml in (None, "<testsuites><unclosed>"):
            with self.subTest(xml=xml):
                code, out = self._main(xml, 1)
                self.assertEqual(code, 2, out)

    def test_a_category_absent_from_the_expectation_is_empty(self):
        """`claims.critical` pins no `error` key: a claim that errors must still
        differ, because absent means "none", never "anything"."""
        xml = _report().replace('<failure type="unclaimed" message="m"/>',
                                '<error type="error" message="m"/>')
        code, out = self._main(xml, 1)
        self.assertEqual(code, 1, out)
        self.assertIn("C7", out)

    def test_the_pinned_file_is_the_spec_json(self):
        """The signature is a fixture: a phase that changes it on purpose changes
        this file in the same commit and says why (PLAN G4). P2.1 changed it: C6,
        an assumption nobody owns, reads Gap and blocks until its owner records
        it through the signing channel (later in Phase 2, when it moves back);
        C5's message is GLOSSARY's words (R-6, the synthetic report above with
        it)."""
        with open(EXPECTED, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh), {
                "exit_code": 1,
                "gates": {"fail": ["bracket.deflection"], "error": [],
                          "not-admitted": [], "skipped": []},
                "claims.critical": {"fail": {"C1": "fail", "C6": "unclaimed",
                                             "C7": "unclaimed"},
                                    "skipped": {"C5": "pending build: needs an article; "
                                                      "no test written down"}},
            })


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


if __name__ == "__main__":
    unittest.main()
