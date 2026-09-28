# SPDX-License-Identifier: Apache-2.0
"""Every `atompipe ...` command the docs print must be one the parser accepts.

What slipped through (S-10): `docs/PACK_FORMAT.md`, `docs/EXTENSION_PROTOCOL.md`
and the pack-authoring skill told pack authors to run `atompipe pack new` and
`atompipe pack export` — the second "with the selftest evidence attached" — and
argparse answered `invalid choice` to both. The skill an agent loads to write a
pack opened on a command that does not exist, and closed on one that claimed to
attach evidence nobody could produce. Nothing turned red, because nothing read
the docs against `build_parser()`.

The checker is a pure function of text, so the violation test plants a string
instead of editing the tree. Its precision is the whole design (tests:H14): too
loose and `pack new` passes; too strict and it fails on the grammar the docs use
on purpose. So it validates **the subcommand path and every named `--flag`**
against the parser, never a full `parse_args` — required arguments are elided
all the time (`atompipe decide --title ...`), and that is shorthand, not a bug —
and it tolerates, by rule:

* placeholders `<x>` and `<x|y>`; optional groups `[--x]`, `[--tier N]`,
  `[list|show]`; alternatives `a|b`; the ellipsis `...`;
* two-column code tables (a command, two or more spaces, a description or a
  second command), and prose after a leaf command;
* paths and module names that merely contain the word: `src/atompipe/`,
  `~/.atompipe/packs`, `atompipe.site.derive_explode` — a command starts only
  where a shell would start one (`$ `, `VAR=x `, `python3 -m `, or after `&&`,
  `||`, `;` or ` | `).

**The file list is explicit.** It never globs `docs/*.md`: the user's untracked
draft lives there in the main checkout and must not decide whether the suite is
green, and the plan documents quote the phantom commands on purpose, as the
defects they record. `skills/*/SKILL.md`, `packs/*/PACK.md` and
`packs/*/references/*.md` are globbed, because every file matching them is
shipped to an agent as instructions.

Run:  PYTHONPATH=src python3 -m unittest tests.test_docs_commands -v
"""
from __future__ import annotations

import argparse
import glob
import os
import re
import textwrap
import unittest

from atompipe import cli

import _env

#: The documents an agent or a contributor reads for commands, named one by one.
#: *Rejected:* `docs/*.md` — it reaches the plan documents, which quote the
#: phantom commands as the defects they record, and the user's untracked draft.
EXPLICIT_FILES = (
    "README.md", "CLAUDE.md", "CONTRIBUTING.md", "METHOD.md",
    "docs/EXTENSION_PROTOCOL.md", "docs/PACK_FORMAT.md",
    "docs/SITE_CONTRACT.md", "docs/SPINE_CONTRACT.md",
)

#: Globbed because every file they match ships to an agent as instructions.
SHIPPED_GLOBS = ("skills/*/SKILL.md", "packs/*/PACK.md", "packs/*/references/*.md")

#: The fewest commands the real file list must yield. A checker that extracts
#: nothing passes everything, so the count is asserted, not assumed. Measured
#: 2026-09-27 on the checkpoint-1.1 tree, before the S-10 fixes: 141 commands in
#: 41 files; the floor sits well below it so a docs rewrite does not trip it,
#: and far above zero so a broken extractor does. *Rejected:* "at least one" (a
#: regex that matches only README's first line would pass).
MIN_COMMANDS = 80


def doc_files(repo: str = _env.REPO) -> list[str]:
    """The checked documents, relative to ``repo``: the explicit list, then the globs."""
    out = list(EXPLICIT_FILES)
    for pattern in SHIPPED_GLOBS:
        out += sorted(os.path.relpath(p, repo).replace(os.sep, "/")
                      for p in glob.glob(os.path.join(repo, pattern)))
    return out


# --------------------------------------------------------------------------- #
# Extraction: where a command starts
# --------------------------------------------------------------------------- #
_FENCE = re.compile(r"^\s*(```|~~~)")
_SPAN = re.compile(r"(?<!`)`([^`\n]+)`(?!`)")
#: Shell list operators, and a pipe with whitespace around it. A bare `a|b` is
#: grammar for alternatives, never a pipe, so it does not split.
_SEPARATORS = re.compile(r"&&|\|\||;|\s\|\s")
#: A command table's column gap: two or more spaces between cells.
_COLUMNS = re.compile(r"\s{2,}")
#: What may precede `atompipe` at the start of a command.
_PREFIX = re.compile(
    r"^(?:\$\s+)?"                                  # a prompt
    r"(?:[A-Za-z_][A-Za-z0-9_]*=\S*\s+)*"           # VAR=value assignments
    r"(?:(?:python(?:3(?:\.\d+)?)?|py)\s+-m\s+)?"   # python3 -m
)
#: Where a command's words end: a comment, or an arrow or dash before prose.
_TAIL = re.compile(r"\s#.*$|\s(?:—|->|→)\s.*$")


def _command_words(segment: str) -> list[str] | None:
    """The words after ``atompipe`` if ``segment`` starts a command, else None."""
    text = _TAIL.sub("", segment.strip())
    text = _PREFIX.sub("", text, count=1)
    match = re.match(r"atompipe(?:\s+(.*))?$", text)
    if not match:
        return None
    return (match.group(1) or "").split()


def _segments(text: str, *, columns: bool) -> list[str]:
    parts = [p for p in _SEPARATORS.split(text) if p.strip()]
    if columns:
        parts = [cell for p in parts for cell in _COLUMNS.split(p.strip()) if cell.strip()]
    return parts


def extract_commands(markdown: str) -> list[tuple[int, list[str]]]:
    """``(line number, words after atompipe)`` for every command in ``markdown``.

    Inside a fenced block each line is split into shell segments and then into
    table columns; outside one, only inline code spans are read, since prose
    that says "atompipe will..." is not a command. Spans are read inside fences
    too: a directory tree or a sample of output quotes its commands that way
    (`vendor/  three.js etc. after \\`atompipe site vendor\\``).
    """
    found: list[tuple[int, list[str]]] = []
    fenced = False
    for number, line in enumerate(markdown.splitlines(), 1):
        if _FENCE.match(line):
            fenced = not fenced
            continue
        candidates = [seg for span in _SPAN.findall(line)
                      for seg in _segments(span, columns=False)]
        if fenced:
            candidates += _segments(line, columns=True)
        seen: list[list[str]] = []
        for candidate in candidates:
            words = _command_words(candidate)
            if words is not None and words not in seen:
                seen.append(words)
                found.append((number, words))
    return found


# --------------------------------------------------------------------------- #
# Validation: the path and the flags, against build_parser()
# --------------------------------------------------------------------------- #
def _subcommands(parser: argparse.ArgumentParser) -> dict[str, argparse.ArgumentParser]:
    """A parser's subcommands, aliases included. Duck-typed like
    `cli._tag_subparsers`: a subparsers action is the one whose choices is a dict."""
    out: dict[str, argparse.ArgumentParser] = {}
    for action in parser._actions:
        if isinstance(action.choices, dict):
            out.update(action.choices)
    return out


def _clean(token: str) -> str:
    """A token with optional-group brackets and quoting punctuation stripped."""
    return token.strip("[](){}\"'").rstrip(",;:")


def _flag_like(token: str) -> bool:
    return token.startswith("-") and len(token) > 1 and not re.match(r"^-\d", token)


def _values_taken(action: argparse.Action) -> int | str:
    """How many following words a flag consumes: a count, or '?' / '*'."""
    nargs = action.nargs
    if nargs is None:
        return 1
    if isinstance(nargs, int):
        return nargs
    if nargs == "?":
        return "?"
    return "*"                                    # '*', '+', REMAINDER


def command_problems(words: list[str], parser: argparse.ArgumentParser) -> list[str]:
    """What is wrong with ``atompipe <words>``: an unknown subcommand, or a flag
    no parser on the path accepts. Positionals, placeholders and prose after a
    leaf command are not judged — only what a reader would type literally."""
    problems: list[str] = []
    parsers = [parser]
    path = ["atompipe"]
    resolving = True
    tokens = [_clean(w) for w in words]
    i = 0
    while i < len(tokens):
        token = tokens[i]
        i += 1
        if not token or set(token) <= {".", "…"}:
            continue
        if token.startswith("<"):
            if resolving and any(_subcommands(p) for p in parsers):
                resolving = False         # `atompipe <command>`: nothing literal to check
            continue
        if _flag_like(token):
            flag = token.split("=", 1)[0]
            actions = [p._option_string_actions[flag] for p in parsers
                       if flag in p._option_string_actions]
            if not actions:
                problems.append(f"`{' '.join(path)}` has no option {flag}")
                continue
            if "=" in token:
                continue
            taken = _values_taken(actions[0])
            if taken == "?":
                if i < len(tokens) and tokens[i] and not _flag_like(tokens[i]):
                    nxt = tokens[i]
                    if not (resolving and any(nxt in _subcommands(p) for p in parsers)):
                        i += 1
            elif taken == "*":
                while i < len(tokens) and not _flag_like(tokens[i]):
                    i += 1
            else:
                i += int(taken)
            continue
        choices: dict[str, argparse.ArgumentParser] = {}
        for p in parsers:
            choices.update(_subcommands(p))
        if not (resolving and choices):
            resolving = False             # a positional, a metavar or prose: not judged
            continue
        chosen: list[argparse.ArgumentParser] = []
        for alternative in token.split("|"):
            if alternative in choices:
                chosen.append(choices[alternative])
            else:
                problems.append(
                    f"`{' '.join(path)}` has no subcommand {alternative!r} "
                    f"(choose from {', '.join(sorted(choices))})")
        if not chosen:
            return problems
        parsers = chosen
        path.append(token)
        resolving = any(_subcommands(p) for p in parsers)
    return problems


def markdown_findings(markdown: str, filename: str,
                      parser: argparse.ArgumentParser) -> tuple[int, list[str]]:
    """``(commands checked, findings)`` for one document."""
    commands = extract_commands(markdown)
    findings: list[str] = []
    for number, words in commands:
        for problem in command_problems(words, parser):
            findings.append(f"{filename}:{number}: atompipe {' '.join(words)[:80]} — {problem}")
    return len(commands), findings


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


class DocsCommandsParse(unittest.TestCase):
    def setUp(self):
        self.parser = cli.build_parser()

    def _findings(self, markdown: str) -> list[str]:
        return markdown_findings(textwrap.dedent(markdown), "<planted>", self.parser)[1]

    def test_every_documented_command_parses(self):
        total = 0
        findings: list[str] = []
        for rel in doc_files():
            path = os.path.join(_env.REPO, rel)
            self.assertTrue(os.path.isfile(path), f"{rel} is on the explicit list but "
                            f"does not exist — renamed? update EXPLICIT_FILES")
            count, found = markdown_findings(_read(path), rel, self.parser)
            total += count
            findings += found
        self.assertEqual(findings, [], "the docs print commands atompipe refuses:\n  "
                         + "\n  ".join(findings))
        self.assertGreaterEqual(
            total, MIN_COMMANDS,
            f"only {total} commands extracted from {len(doc_files())} files — the "
            f"extractor has gone blind, and a blind checker passes everything")

    def test_the_file_list_is_explicit(self):
        """docs/ contributes exactly the four named contracts, however many other
        documents sit beside them (the plan quotes `pack new` as a defect)."""
        files = doc_files()
        in_docs = sorted(f for f in files if f.startswith("docs/"))
        self.assertEqual(in_docs, sorted(f for f in EXPLICIT_FILES if f.startswith("docs/")))
        self.assertTrue(any(f.startswith("skills/") for f in files), files)
        self.assertTrue(any(f.startswith("packs/") for f in files), files)

    def test_a_planted_phantom_is_reported(self):
        """V: the commands S-10 found, and the wrong spellings next to them."""
        planted = {
            "fenced pack new": """
                ```
                atompipe pack new <name>
                ```
            """,
            "fenced pack export, with a comment": """
                ```
                atompipe pack validate <name> # fine
                atompipe pack export <name>   # PR-ready, with the evidence attached
                ```
            """,
            "inline pack new": "Scaffold with `atompipe pack new <name>` first.\n",
            "a python -m prefix": "```sh\nPYTHONPATH=src python3 -m atompipe pack new x\n```\n",
            "after &&": "```sh\ncd examples/bracket && atompipe chek\n```\n",
            "an unknown flag": "Run `atompipe check --tierr 1`.\n",
            "a flag on the wrong level": "Run `atompipe gate --selftest`.\n",
            "one bad alternative": "`atompipe site build|deploy`\n",
            "a bad subcommand in an optional group": "`atompipe packs [list|publish]`\n",
            "a table row": """
                ```
                atompipe init                  atompipe pack new
                ```
            """,
        }
        for label, markdown in planted.items():
            with self.subTest(planted=label):
                self.assertTrue(self._findings(markdown), f"{label}: not reported")

    def test_the_grammar_is_tolerated(self):
        """C: the shorthand the docs use on purpose is not a finding."""
        tolerated = {
            "placeholders": "`atompipe why <param|claim>` and `atompipe ingest <files>`\n",
            "an elided required arg": "`atompipe decide --title ...` and `atompipe extract`\n",
            "optional flags with values": "`atompipe check [--tier N] [--only GATE]`\n",
            "an optional flag value": "`atompipe gate selftest [--junit [PATH]]`\n",
            "alternatives": "`atompipe site build|serve` and `atompipe packs [list|show|validate]`\n",
            "a two-column table": """
                ```
                atompipe init                  atompipe status
                atompipe ask                   what evidence to request from the human
                atompipe check [--tier N]      run gates; exits non-zero while anything blocks
                atompipe doctor                run this first when something is confusing
                ```
            """,
            "paths and modules": ("`src/atompipe/`, `~/.atompipe/packs`, "
                                  "`atompipe.site.derive_explode`, `pip install atompipe`\n"),
            "top-level flags": "`atompipe --help`, `atompipe --version`, "
                               "`atompipe -C examples/bracket check`\n",
            "the alias": "`atompipe pack validate <name>`\n",
            "a prompt and env": "```\n$ PYTHONPATH=src python3 -m atompipe gate selftest --pack x\n```\n",
            "prose after a leaf": "```\natompipe gate selftest every negative control\n```\n",
            "an XML sample": '```\n<testsuites name="atompipe check" tests="13">\n```\n',
            "gate ids and pack names": "`atompipe gate selftest sourcing modelica.checks`\n",
        }
        for label, markdown in tolerated.items():
            with self.subTest(tolerated=label):
                self.assertEqual(self._findings(markdown), [])

    def test_the_extractor_sees_what_it_tolerates(self):
        """The tolerance is in validation, not in extraction: each tolerated form
        is still read as a command, so a typo inside it is still caught."""
        self.assertEqual(len(extract_commands("```\natompipe init    atompipe status\n```\n")), 2)
        self.assertTrue(self._findings("`atompipe check [--tierr N]`\n"))
        self.assertTrue(self._findings("`atompipe why <param|claim> --verbose`\n"))


if __name__ == "__main__":
    unittest.main()
