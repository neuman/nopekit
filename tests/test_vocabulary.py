# SPDX-License-Identifier: Apache-2.0
"""Every human word for a claim's status is GLOSSARY §3's, from one table.

PLAN D-16: one vocabulary table, `report.HUMAN`, and "no human channel prints a
status word from anywhere else". GLOSSARY §3 fixes the words: Table 1's seven —
Checked, Failing, Stale, Assumed, Pending build, Gap, Skipped — plus Open. What
slipped through before P2.1 (S-69): three vocabularies at once — `[uncl]` in
`check`, `[gap  ]` in `status`, NO GATE on the page — and words the paper's
readers misread: "machine-verified", "blocked on missing tooling" for a crash,
PARTIAL for a lesser success, READY over a claim waiting for an article.

* **StatusWordsAreTheGlossarys** (V9) — `HUMAN`'s status terms are GLOSSARY §3's
  Term column, parsed (exactly eight rows, or the parse is broken); every
  `ClaimStatus` and every `ClaimCause` has its entry; the outcome words are
  §1's four, and the outcome tags `models._RENDER_TAG` itself; Checked's hint
  claims no qualification and no article binding nothing yet provides.
* **StatusLinesSpeakTheTable** (V10) — on the louder, refused and bracket
  worlds, a project with no claims, one never evaluated and one whose every
  verdict is invalidated, every status-bearing line carries `HUMAN`'s tag for
  its (status, errored), and no GLOSSARY §3 non-italic Never-say — the list
  PARSED from GLOSSARY, never typed ("this file is the list", §7), with a
  floor so a parse that returns nothing is red. A claim's reason line is held
  to §2's Never-says too (an unqualified evaluator's refusal). One allowlisted
  hit: `report.SECTION_PROVEN`'s text, until A-11.
* **StatusWordsComeFromOneTable** (V11) — `HUMAN` patched to sentinels moves
  every channel rendered in process (the terminal and markdown reports, JUnit,
  `status`, `check`, `claim list|show`, `why`, `report`, `state.json`'s words):
  a renderer that prints the right word from its own literal passes a
  Never-say scan and fails here. And the page's scripts hold no status word.
* **ReadyMeansEveryRequiredClaimChecked** (V15) — *ready* (GLOSSARY §4: every
  required claim Checked) is said only when true, on every channel: the
  readiness sentence, `check`'s line, the page's headline, and the machine
  keys beside `ready` (`all_required_checked`), which keeps "nothing stops
  check" for older readers.

Expected words are literals typed here from GLOSSARY (P2.0 D-7), never read from
`report.HUMAN`: a test that reads the table under test agrees with it by
construction. The Never-say list is GLOSSARY's own, parsed — a new row there is
enforced with no edit here.

Run:  PYTHONPATH=src python3 -m unittest discover -s tests -p test_vocabulary.py -v
"""
from __future__ import annotations

import contextlib
import dataclasses
import io
import json
import os
import re
import shutil
import tempfile
import unittest
import xml.etree.ElementTree as ET
from types import MappingProxyType
from typing import Any, NamedTuple
from unittest import mock

import _env
import _projects
import test_louder
import test_status_table
from atompipe import claims as claims_mod
from atompipe import cli as cli_mod
from atompipe import models as models_mod
from atompipe import report as report_mod
from atompipe import site as site_mod
from atompipe.models import Claim, ClaimKind, ClaimStatus, Ledger

GLOSSARY = os.path.join(_env.REPO, "docs", "GLOSSARY.md")

#: GLOSSARY §3's terms, typed (V9 compares HUMAN with the PARSE; this pins the
#: parse itself, so a glossary edit that drops a row is seen as one).
TERMS = ("Checked", "Failing", "Stale", "Assumed", "Pending build", "Gap", "Skipped", "Open")

#: Each enum value's tag (P2.1-D14), typed; a crash's Skipped is `SKIP `.
TAGS = {"pass": "ok   ", "verified": "ok   ", "fail": "FAIL ", "refuted": "FAIL ",
        "stale": "STALE", "asserted": "assum", "unverified": "build", "unclaimed": "gap  ",
        "blocked": "skip ", "pending": "open "}
ERRORED_TAG = "SKIP "

#: The floor under the parsed Never-say list: a parse that lost any of these is
#: broken, not permissive.
NEVER_FLOOR = {"proven", "machine-verified", "ok-hw", "refuted", "blocked", "unclaimed",
               "capability gap", "unverified", "phys", "asserted", "on faith", "not run",
               "never run", "not current"}

#: The one line a status Never-say may appear on: the checked section's
#: heading, whose text changes only with METHOD (A-11, PLAN D-14).
ALLOWLIST = (report_mod.SECTION_PROVEN,)


# --------------------------------------------------------------------------- #
# GLOSSARY, parsed
# --------------------------------------------------------------------------- #
def _section(text: str, number: int) -> str:
    match = re.search(rf"^## {number}\. .*?(?=^## {number + 1}\. |\Z)", text, re.M | re.S)
    return match.group(0) if match else ""


def _rows(section: str) -> list[list[str]]:
    """The cells of each `| **Term** | … |` row, `\\|` kept inside a cell."""
    out = []
    for line in section.splitlines():
        if not line.startswith("| **"):
            continue
        cells = [c.strip() for c in re.split(r"(?<!\\)\|", line.strip().strip("|"))]
        out.append(cells)
    return out


def glossary_terms(text: str) -> list[str]:
    """§3's bold Term column."""
    return [re.sub(r"\*\*", "", row[0]).strip() for row in _rows(_section(text, 3))]


def never_says(text: str, *sections: int) -> set[str]:
    """The non-italic Never-say items of `sections`' tables, lower-cased, each
    without its parenthetical. Italic ones (`*green*`) have an innocent second
    sense and are review's, not the scanner's (GLOSSARY, its preamble)."""
    out: set[str] = set()
    for number in sections:
        for row in _rows(_section(text, number)):
            for item in row[-1].split(" · "):
                item = item.strip()
                if not item or item.startswith("*") or item == "—":
                    continue
                item = re.sub(r"\s*\(.*?\)\s*", " ", item).strip().lower()
                if item:
                    out.add(item)
    return out


def masked_terms(text: str) -> list[str]:
    """Every bold term of §1–§5, longest first: a Never-say inside a term
    (*pending* in *Pending build*) is the term, not the word."""
    found: set[str] = set()
    for number in range(1, 6):
        for row in _rows(_section(text, number)):
            for term in re.findall(r"\*\*([^*]+)\*\*", row[0]):
                found.add(term.strip())
    return sorted(found, key=len, reverse=True)


def _glossary() -> str:
    with open(GLOSSARY, encoding="utf-8") as fh:
        return fh.read()


def never_say_hits(line: str, banned: set[str], masks: list[str]) -> list[str]:
    """The Never-says `line` says, in prose: code spans (`atompipe gap
    --propose`) are stripped first, as GLOSSARY §7's scanner strips code and
    command lines, and every glossary term is masked."""
    if any(line.startswith(allowed) for allowed in ALLOWLIST):
        return []
    scrubbed = re.sub(r"`[^`]*`", " ", line)
    for term in masks:
        scrubbed = re.sub(rf"(?i)\b{re.escape(term)}\b", " ", scrubbed)
    return sorted(word for word in banned
                  if re.search(rf"(?i)(?<![\w-]){re.escape(word)}(?![\w-])", scrubbed))


# --------------------------------------------------------------------------- #
# V9
# --------------------------------------------------------------------------- #
def table_problems(text: str, human: Any = None) -> list[str]:
    human = human if human is not None else report_mod.HUMAN
    out: list[str] = []
    terms = glossary_terms(text)
    if len(terms) != 8:
        out.append(f"GLOSSARY §3 parses to {len(terms)} terms, not 8: {terms}")
    said = {row.term for row in human["status"].values()}
    if said != set(terms):
        out.append(f"HUMAN's terms {sorted(said)} are not GLOSSARY's {sorted(terms)}")
    missing = [s.value for s in ClaimStatus if s not in human["status"]]
    if missing:
        out.append(f"no HUMAN row for {missing}")
    leads = [c.value for c in claims_mod.ClaimCause if c not in human["lead"]]
    if leads:
        out.append(f"no HUMAN lead for {leads}")
    outcomes = re.search(r"\*\*pass\*\*, \*\*fail\*\*, \*\*skipped\*\* or \*\*errored\*\*",
                         _section(text, 1))
    if not outcomes:
        out.append("GLOSSARY §1's four outcomes did not parse")
    if sorted(human["outcome"].values()) != sorted(["pass", "fail", "skipped", "errored"]):
        out.append(f"HUMAN's outcome words {sorted(human['outcome'].values())}")
    hint = human["status"][ClaimStatus.PASS].hint
    # Flipped in the open by P2.3 (R-6, with V3): until qualification was both
    # controls — and the mutation pass for a project evaluator — "qualified" in
    # Checked's hint was an overclaim, refused here; from P2.3 it is what
    # Checked stands on, and a hint without it says less than the rule does.
    if "qualified" not in hint:
        out.append(f"Checked's hint does not say 'qualified': {hint!r}")
    if "built from" in hint:
        out.append(f"Checked's hint says 'built from': {hint!r}")
    return out


class StatusWordsAreTheGlossarys(unittest.TestCase):
    """(V9) `report.HUMAN` equals GLOSSARY §3, parsed."""

    def test_the_table_is_the_glossarys(self):
        self.assertEqual(glossary_terms(_glossary()), list(TERMS))
        self.assertEqual(table_problems(_glossary()), [])

    def test_the_outcome_tags_are_one_table(self):
        """`HUMAN["outcome_tag"]` is `models._RENDER_TAG`, re-exported: a tag
        changed there is changed here, with nothing to hold equal."""
        with mock.patch.dict(models_mod._RENDER_TAG, {"pass": "ZZ  "}):
            self.assertEqual(report_mod.HUMAN["outcome_tag"]["pass"], "ZZ  ")
        self.assertEqual(dict(report_mod.HUMAN["outcome_tag"]), dict(models_mod._RENDER_TAG))

    def test_the_tag_view_is_a_mapping(self):
        """`report.STATUS_TAG` (exported) answers like the dict it replaced: a
        status it does not know is a missing key — `.get` gives None and `in`
        False — never a ValueError out of both. What slipped through (review of
        P2.1): the view raised `ValueError: 'bogus' is not a valid ClaimStatus`,
        which `Mapping.get` and `__contains__` do not catch."""
        tags = report_mod.STATUS_TAG
        self.assertIsNone(tags.get("bogus"))
        self.assertNotIn("bogus", tags)
        self.assertIn(ClaimStatus.PASS, tags)
        self.assertEqual((tags["pass"], tags.get(ClaimStatus.UNVERIFIED)), ("ok   ", "build"))
        with self.assertRaises(KeyError):
            tags["bogus"]

    def test_the_comparison_refuses_what_it_forbids(self):
        glossary = _glossary()
        ninth = glossary.replace("| **Open** |", "| **Ajar** | x | x | x | x |\n| **Open** |", 1)
        self.assertTrue(any("not 8" in p for p in table_problems(ninth)))
        rows = dict(report_mod.HUMAN["status"])
        rows[ClaimStatus.PASS] = rows[ClaimStatus.PASS]._replace(term="Proven")
        planted = MappingProxyType(dict(report_mod.HUMAN, status=MappingProxyType(rows)))
        self.assertTrue(any("not GLOSSARY's" in p for p in table_problems(glossary, planted)))
        rows[ClaimStatus.PASS] = rows[ClaimStatus.PASS]._replace(
            term="Checked", hint="every evaluator passed")
        planted = MappingProxyType(dict(report_mod.HUMAN, status=MappingProxyType(rows)))
        self.assertTrue(any("'qualified'" in p for p in table_problems(glossary, planted)))


# --------------------------------------------------------------------------- #
# the worlds V10 and V15 read
# --------------------------------------------------------------------------- #
class _Proc(NamedTuple):
    stdout: str
    returncode: int = 0


class World(NamedTuple):
    """A project and what each command printed or wrote."""
    root: str
    out: dict[str, Any]
    files: dict[str, str]


_WORLDS: dict[str, World] = {}


def _run_world(name: str, root: str, runs: tuple, files: tuple = ()) -> World:
    home = os.path.join(os.path.dirname(root), "home")
    os.makedirs(home, exist_ok=True)
    out = {key: _env.atompipe(argv, cwd=root, home=home) for key, argv in runs}
    read = {}
    for key, rel in files:
        path = os.path.join(root, *rel.split("/"))
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as fh:
                read[key] = fh.read()
    world = World(root, out, read)
    _WORLDS[name] = world
    return world


def _tmp(prefix: str) -> str:
    tmp = tempfile.mkdtemp(prefix=prefix)
    unittest.addModuleCleanup(_env._rmtree, tmp)
    return tmp


#: The export channel's runs (P2.5b): each scanned for GLOSSARY §3's, §4's and
#: §5's Never-says — *ready*, *required*, *milestone* and *latency* are §4's
#: and §5's terms, and their Never-says (*blocking*, *non-critical*,
#: *turnaround*, *cached*) are what an export line would reach for.
EXPORT_RUNS = (("export", ["export"]), ("export.dry", ["export", "print-v1", "--dry-run"]),
               ("report.milestone", ["report", "--milestone", "print-v1"]))
EXPORT_KEYS = tuple(key for key, _argv in EXPORT_RUNS)

_BRACKET_RUNS = (("check", ["check", "--junit"]), ("status", ["status"]),
                 ("status.json", ["status", "--json"]), ("report", ["report"]),
                 ("claim.list", ["claim", "list"]), ("claim.show", ["claim", "show", "C6"]),
                 ("why.C6", ["why", "C6"]), ("why.C7", ["why", "C7"]),
                 ("site.init", ["site", "init"]), ("site.build", ["site", "build"]),
                 # P2.5b (critique 11 of its design): the milestone list, the
                 # boundary's dry run and the milestone's report are channels too.
                 *EXPORT_RUNS)
_FILES = (("junit", ".atompipe/out/junit.xml"), ("state", "site/data/state.json"))


def bracket_world() -> World:
    """The bracket as a clone holds it: committed cache, today's records."""
    if "bracket" not in _WORLDS:
        root = _projects.bracket_copy(os.path.join(_tmp("atompipe-vocab-"), "bracket"),
                                      migrated=True)
        _run_world("bracket", root, _BRACKET_RUNS, _FILES)
    return _WORLDS["bracket"]


def empty_world() -> World:
    """A project with no claims at all."""
    if "empty" not in _WORLDS:
        root = os.path.join(_tmp("atompipe-vocab-empty-"), "empty")
        os.makedirs(root)
        _env.atompipe(["init", "--name", "empty"], cwd=root)
        _run_world("empty", root, (("status", ["status"]), ("report", ["report"]),
                                   ("check", ["check"])))
    return _WORLDS["empty"]


def unevaluated_world() -> World:
    """The bracket with no verdict at all: never evaluated."""
    if "unevaluated" not in _WORLDS:
        root = _projects.bracket_copy(os.path.join(_tmp("atompipe-vocab-none-"), "bracket"),
                                      migrated=True)
        shutil.rmtree(os.path.join(root, ".atompipe", "verdicts"))
        _run_world("unevaluated", root, (("status", ["status"]), ("report", ["report"])))
    return _WORLDS["unevaluated"]


# --------------------------------------------------------------------------- #
# V10
# --------------------------------------------------------------------------- #
_CLAIM_ROW = re.compile(r"^\[(?P<tag>.{5})\] (?P<id>[A-Z]\d+)\b")


def _lines(text: str) -> list[str]:
    return [ln for ln in text.splitlines() if ln.strip()]


def _status_view(doc: dict) -> dict[str, tuple[str, bool]]:
    views = doc.get("statuses") or {}
    return {cid: (status, bool((views.get(cid) or {}).get("errored")))
            for cid, status in (doc.get("claims") or {}).items()}


def tag_problems(channel: str, text: str, statuses: dict[str, tuple[str, bool]]) -> list[str]:
    out = []
    for line in text.splitlines():
        m = _CLAIM_ROW.match(line)
        if not m or m.group("id") not in statuses:
            continue
        status, errored = statuses[m.group("id")]
        want = ERRORED_TAG if errored else TAGS[status]
        if m.group("tag") != want:
            out.append(f"{channel}: {m.group('id')} tagged [{m.group('tag')}], "
                       f"not [{want}] ({status}{', errored' if errored else ''})")
    return out


def scan(channel: str, lines: list[str], banned: set[str], masks: list[str],
         reasons: set[str] = frozenset()) -> list[str]:
    """Never-say hits on `lines`; `reasons` holds §2's for claim reason lines."""
    out = []
    if not lines:
        out.append(f"{channel}: no line scanned (floor)")
    for line in lines:
        words = set(banned)
        if _CLAIM_ROW.match(line) or line.lstrip().startswith(("- **", "### [")):
            words |= reasons
        for hit in never_say_hits(line, words, masks):
            out.append(f"{channel}: says {hit!r}: {line[:100]}")
        if "PARTIAL" in line:
            out.append(f"{channel}: says PARTIAL: {line[:100]}")
    return out


def readme_status_lines() -> list[str]:
    """README's "Why you should believe the output", line by line: where it
    defines the statuses for a reader."""
    with open(os.path.join(_env.REPO, "README.md"), encoding="utf-8") as fh:
        text = fh.read()
    section = text.split("## Why you should believe the output", 1)[1].split("\n## ", 1)[0]
    return [ln for ln in section.splitlines() if ln.strip()]


def _markdown_lines(md: str) -> list[str]:
    """The report minus `## Reproduce` (commands and file names, not prose)."""
    head, _, _ = md.partition("## Reproduce")
    return _lines(head)


def _junit_messages(text: str) -> list[str]:
    root = ET.fromstring(text)
    out = []
    for suite in root.iterfind("testsuite"):
        if not str(suite.get("name", "")).startswith("claims."):
            continue
        for case in suite.iterfind("testcase"):
            out += [str(k.get("message") or "") for k in case if k.tag != "properties"]
    return out


def _json_words(doc: dict) -> list[str]:
    out = []
    for view in (doc.get("statuses") or {}).values():
        out += [str(view.get("word", "")), str(view.get("reason", ""))]
    return out


def _state_lines(state: dict) -> list[str]:
    out = [str((state.get("readiness") or {}).get("verdict") or "")]
    out += [str(item.get("label", "")) for item in (state.get("readiness") or {}).get("tally") or ()]
    for row in state.get("claims") or ():
        out += [str(row.get("word", "")), str(row.get("reason", ""))]
        out += [f"{u.get('gate')} — {u.get('why')}" for u in row.get("unproven") or ()]
    for words in (state.get("words") or {}).values():
        out += [str(words.get(k, "")) for k in ("word", "term", "plural", "hint")]
    return [ln for ln in out if ln]


def vocabulary_problems(worlds: dict[str, World], banned: set[str], masks: list[str],
                        reasons: set[str], wider: set[str] = frozenset()) -> list[str]:
    out: list[str] = []
    for name, world in worlds.items():
        statuses_ = {}
        if "status.json" in world.out:
            statuses_ = _status_view(json.loads(world.out["status.json"].stdout))
        for key in EXPORT_KEYS:
            if key in world.out:
                text = world.out[key].stdout
                lines = _markdown_lines(text) if key.startswith("report") else _lines(text)
                out += scan(f"{name}.{key}", lines, set(banned) | set(wider), masks, reasons)
                out += tag_problems(f"{name}.{key}", text, statuses_)
        statuses = {}
        if "status.json" in world.out:
            statuses = _status_view(json.loads(world.out["status.json"].stdout))
        for key in ("status", "check", "claim.list", "claim.show", "claim.physical"):
            if key in world.out:
                text = world.out[key].stdout
                if key == "check":
                    at = next((i for i, ln in enumerate(text.splitlines())
                               if ln.startswith(("BLOCKING", "ready", "nothing stops",
                                                 "no claims"))), None)
                    text = "\n".join(text.splitlines()[at:]) if at is not None else ""
                out += scan(f"{name}.{key}", _lines(text), banned, masks, reasons)
                out += tag_problems(f"{name}.{key}", text, statuses)
        for key in ("report", "report.file"):
            text = world.out[key].stdout if key in world.out else world.files.get("readiness")
            if text:
                out += scan(f"{name}.{key}", _markdown_lines(text), banned, masks, reasons)
        for key in [k for k in world.out if k.startswith("why")]:
            gates = [ln for ln in world.out[key].stdout.splitlines() if "(none —" in ln
                     or ln.strip().startswith("[")]
            if gates:
                out += scan(f"{name}.{key}", gates, banned, masks)
        if "junit" in world.files:
            out += scan(f"{name}.junit", _junit_messages(world.files["junit"]), banned, masks,
                        reasons)
        for key in ("status.json", "report.json"):
            if key in world.out:
                out += scan(f"{name}.{key}", _json_words(json.loads(world.out[key].stdout)),
                            banned, masks, reasons)
        if "state" in world.files:
            out += scan(f"{name}.state", _state_lines(json.loads(world.files["state"])),
                        banned, masks, reasons)
    return out


def _louder() -> World:
    run = test_louder._louder_project()
    return World(run.root, dict(run.out), {"junit": run.files["junit"],
                                          "state": run.files["state"],
                                          "readiness": run.files["readiness"]})


def _refused() -> World:
    run = test_status_table._refused_project()
    return World("", dict(run.out), {"junit": run.junit, "state": json.dumps(run.state)})


def _invalidated() -> World:
    """Every verdict marked invalidated (the all-gates alias), in process."""
    registry = test_louder._probe_registry()
    root = _tmp("atompipe-vocab-stale-")
    ledger = test_louder._probe_ledger(registry, root)
    return World(root, {"status": _Proc(report_mod.render_terminal(ledger, registry,
                                                                   stale=True)),
                        "report": _Proc(report_mod.render_markdown(ledger, registry,
                                                                   stale=True))}, {})


class StatusLinesSpeakTheTable(unittest.TestCase):
    """(V10) No status-bearing line on any channel says a GLOSSARY §3 Never-say,
    and every claim row's tag is `HUMAN`'s for its status."""

    @classmethod
    def worlds(cls) -> dict[str, World]:
        return {"louder": _louder(), "refused": _refused(), "bracket": bracket_world(),
                "empty": empty_world(), "unevaluated": unevaluated_world(),
                "invalidated": _invalidated()}

    def _lists(self):
        text = _glossary()
        banned = never_says(text, 3)
        return banned, masked_terms(text), never_says(text, 2) - {"verified", "validated"}

    def test_the_parse_is_the_glossarys_list(self):
        banned, masks, reasons = self._lists()
        self.assertTrue(NEVER_FLOOR <= banned, sorted(NEVER_FLOOR - banned))
        self.assertIn("Pending build", masks)
        self.assertTrue({"admitted", "bad fixture", "negative control"} <= reasons, reasons)

    def test_every_status_line_speaks_the_table(self):
        banned, masks, reasons = self._lists()
        wider = never_says(_glossary(), 4) | never_says(_glossary(), 5)
        self.assertEqual(vocabulary_problems(self.worlds(), banned, masks, reasons, wider), [])

    def test_the_export_channel_is_scanned(self):
        """P2.5b: the export lines reach the scan, with §4's and §5's lists —
        and a planted *blocking* or *turnaround* there is caught."""
        wider = never_says(_glossary(), 4) | never_says(_glossary(), 5)
        self.assertTrue({"blocking", "non-critical", "turnaround", "cached"} <= wider, wider)
        banned, masks, reasons = self._lists()
        world = bracket_world()
        for key in EXPORT_KEYS:
            self.assertIn(key, world.out)
            self.assertTrue(_lines(world.out[key].stdout), f"{key} printed nothing")
        for planted in ("1 blocking claim (C1)", "turnaround 1 day"):
            with self.subTest(planted):
                text = world.out["export.dry"].stdout + planted + "\n"
                found = vocabulary_problems(
                    {"p": World("", {"export.dry": _Proc(text)}, {})}, banned, masks, reasons,
                    wider)
                self.assertTrue(found, planted)

    def test_every_sentence_branch_is_reached(self):
        """The worlds reach each readiness branch: not ready, never evaluated,
        no claims, every verdict invalidated."""
        worlds = self.worlds()
        sentences = {name: w.out["status"].stdout.splitlines()[1] for name, w in worlds.items()
                     if "status" in w.out}
        self.assertIn("is NOT ready", sentences["bracket"])
        self.assertIn("has never been evaluated", sentences["unevaluated"])
        self.assertIn("has no claims recorded", sentences["empty"])
        self.assertIn("is NOT ready", sentences["invalidated"])

    def test_the_readme_says_the_statuses_in_their_words(self):
        """README's account of the statuses — what a sandboxed agent quotes — in
        GLOSSARY §3's words (a §7 channel). What slipped through (review of P2.1,
        lines that commit wrote): Skipped was "a gate did not run" and Open "has
        not run", Open's Never-say twice, and the checked rows were "the PROVEN
        table"."""
        banned, masks, _reasons = self._lists()
        self.assertEqual(scan("README", readme_status_lines(), banned, masks), [])
        planted = [*readme_status_lines(),
                   "- **Open** — a gate exists and has not run on the current inputs.",
                   "Every row in the PROVEN table cites the gate that passed it."]
        found = scan("README", planted, banned, masks)
        for word in ("'not run'", "'proven'"):
            with self.subTest(word):
                self.assertTrue(any(word in p for p in found), found)

    def test_the_scan_refuses_what_it_forbids(self):
        banned, masks, reasons = self._lists()
        world = bracket_world()
        status = world.out["status"].stdout
        planted = {
            "a status row printing PROVEN": status.replace("[FAIL ] C1 Tip",
                                                           "[ok   ] C1 PROVEN: Tip"),
            "a sentence saying blocked on missing tooling": status.replace(
                "1 failing (C1)", "1 blocked on missing tooling (C1)"),
            "a count line saying unverified": status.replace("1 pending build",
                                                             "1 unverified"),
        }
        for name, text in planted.items():
            with self.subTest(name):
                found = vocabulary_problems(
                    {"p": World("", {"status": _Proc(text), "status.json": world.out[
                        "status.json"]}, {})}, banned, masks, reasons)
                self.assertTrue(found, name)
        self.assertTrue(vocabulary_problems({"p": World("", {"status": _Proc("")}, {})},
                                            banned, masks, reasons),
                        "a channel that printed nothing passed the floor")
        self.assertEqual(never_say_hits("Pending build: 1 claim needs an article (C5).",
                                        banned, masks), [])
        self.assertEqual(never_say_hits(report_mod.SECTION_PROVEN + " (x)", banned, masks), [])


# --------------------------------------------------------------------------- #
# V11
# --------------------------------------------------------------------------- #
def sentinel_human() -> Any:
    """`report.HUMAN` with every status's word, term, plural and tag, every
    lead and every heading replaced by a sentinel."""
    human = report_mod.HUMAN
    rows = {}
    for status, row in human["status"].items():
        rows[status] = row._replace(word=f"zz{row.key}", term=f"Zz{row.key}",
                                    plural=f"zz{row.key}s", tag=f"Z{row.rank}   "[:5])
    errored = human["errored"]._replace(word="zzskipped", term="Zzskipped",
                                        plural="zzskippeds", tag="ZE   ")
    leads = {cause: (f"zz{text}" if text and text != "—" else text)
             for cause, text in human["lead"].items()}
    headings = {key: f"## Zz{key}" for key in human["heading"]}
    extra = {}
    if "prerequisite" in human:
        # P2.2: a prerequisite skip's phrase and its kind words are HUMAN's too
        # (critique of its design: spelled in the spine, they were a second
        # outcome-word table no sentinel reached).
        extra["prerequisite"] = MappingProxyType({key: f"zz{text}" for key, text
                                                  in human["prerequisite"].items()})
        extra["outcome"] = MappingProxyType({key: f"zz{text}" for key, text
                                             in human["outcome"].items()})
    return MappingProxyType(dict(human, status=MappingProxyType(rows), errored=errored,
                                 lead=MappingProxyType(leads),
                                 heading=MappingProxyType(headings), **extra))


#: The original status words that must not survive in a status-bearing line
#: rendered under the sentinel table.
_ORIGINAL = ("checked", "failing", "stale", "assumed", "pending build", "gap", "gaps",
             "skipped", "open")


def _captured(argv: list[str]) -> str:
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        cli_mod.main(argv)
    return out.getvalue()


def routing_problems(channel: str, lines: list[str]) -> list[str]:
    out = []
    rows = [ln for ln in lines if _CLAIM_ROW.match(ln)]
    if not rows:
        out.append(f"{channel}: no claim row (floor)")
    for line in lines:
        m = _CLAIM_ROW.match(line)
        if m and not m.group("tag").startswith("Z"):
            out.append(f"{channel}: {m.group('id')} tagged [{m.group('tag')}], not HUMAN's")
        body = line.split(" — ", 1)[1] if m and " — " in line else (line if not m else "")
        for word in _ORIGINAL:
            if re.search(rf"(?i)(?<![\w-]){re.escape(word)}(?![\w-])", body):
                out.append(f"{channel}: says {word!r} not from HUMAN: {line[:90]}")
    return out


class StatusWordsComeFromOneTable(unittest.TestCase):
    """(V11) Patching `report.HUMAN` moves every channel: no renderer holds a
    status word of its own. A Never-say scan cannot see a renderer that prints
    the RIGHT word from a literal; this can."""

    def _ledger(self):
        registry = test_louder._probe_registry()
        root = tempfile.mkdtemp(prefix="atompipe-vocab-route-")
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        return test_louder._probe_ledger(registry, root), registry

    def _terminal_lines(self, text: str) -> list[str]:
        lines = text.splitlines()
        return [lines[1], lines[2], *(ln for ln in lines if _CLAIM_ROW.match(ln))]

    def test_the_reports_route_through_human(self):
        ledger, registry = self._ledger()
        with mock.patch.object(report_mod, "HUMAN", sentinel_human()):
            terminal = report_mod.render_terminal(ledger, registry, stale_gates={"probe.moved"})
            md = report_mod.render_markdown(ledger, registry, stale_gates={"probe.moved"})
            junit = report_mod.render_junit(ledger, ledger.verdicts, registry, tier=0,
                                            ready=False, exit_code=1, when="t",
                                            stale_gates={"probe.moved"})
            words = report_mod.words_table()
        self.assertEqual(routing_problems("render_terminal", self._terminal_lines(terminal)), [])
        self.assertIn("zzchecked", terminal.splitlines()[2])
        self.assertRegex(terminal.splitlines()[2], r"\d+ zzskippeds? \(\d+ zzerrored\)")
        heads = [ln for ln in md.splitlines() if ln.startswith("## ")]
        self.assertEqual([h for h in heads if not h.startswith(("## Zz", report_mod.SECTION_PROVEN))],
                         [], heads)
        self.assertTrue(re.search(r"^### \[Z.{4}\] P2 .*\*\(zzskipped, errored, critical\)\*$",
                                  md, re.M), md)
        messages = _junit_messages(junit)
        self.assertTrue(any(m.startswith("zzerrored: probe.z_crash") for m in messages),
                        messages)
        self.assertTrue(all(w["word"].startswith("zz") for w in words.values()), words)

    def test_the_commands_route_through_human(self):
        root = _projects.bracket_copy(os.path.join(tempfile.mkdtemp(
            prefix="atompipe-vocab-cli-"), "bracket"), migrated=True)
        self.addCleanup(shutil.rmtree, os.path.dirname(root), ignore_errors=True)
        with mock.patch.object(report_mod, "HUMAN", sentinel_human()):
            outputs = {key: _captured([*argv, "-C", root]) for key, argv in (
                ("status", ["status"]), ("check", ["check", "--no-record"]),
                ("claim.list", ["claim", "list"]), ("claim.show", ["claim", "show", "C6"]),
                ("why", ["why", "C6"]), ("report", ["report"]),
                # P2.5b (critique 11 of its design): the export channel.
                ("export", ["export", "print-v1", "--dry-run"]),
                ("report.milestone", ["report", "--milestone", "print-v1"]))}
        export_rows = [ln for ln in outputs["export"].splitlines() if _CLAIM_ROW.match(ln)]
        self.assertEqual(routing_problems("export", export_rows), [])
        self.assertIn("## Zzgaps", outputs["report.milestone"])
        status = outputs["status"].splitlines()
        self.assertEqual(routing_problems("status", self._terminal_lines(outputs["status"])),
                         [])
        check = outputs["check"].splitlines()
        at = next(i for i, ln in enumerate(check) if ln.startswith("BLOCKING"))
        self.assertEqual(routing_problems("check", check[at + 1:]), [])
        self.assertEqual(routing_problems("claim.list", outputs["claim.list"].splitlines()), [])
        self.assertTrue(outputs["claim.show"].startswith("[Z"), outputs["claim.show"][:20])
        self.assertIn("zzgap", " ".join(outputs["why"].split()))
        self.assertIn("## Zzgaps", outputs["report"])
        self.assertTrue(status)

    def test_claim_list_status_takes_a_word_and_refuses_anything_else(self):
        """`claim list --status` takes a status's enum value, token or word, and
        refuses a spelling that is none of them, naming the words. What slipped
        through (review of P2.1, which traded argparse's `choices` for a free
        filter): `--status failed` printed "no claims match that filter", exit 0
        — an agent told that nothing is failing."""
        # A bracket copy of its own, not `bracket_world()`: the committed cache
        # already reads C1 Failing, C5 Pending build, C6 and C7 Gap, and the
        # world runs fourteen commands (P2.5b's export among them, ~4.5 s) for
        # one in-process question — the fast tier's pay-back for P2.5b.
        root = _projects.bracket_copy(os.path.join(_tmp("atompipe-vocab-status-"), "bracket"),
                                      migrated=True)

        def run(*argv):
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = cli_mod.main(["claim", "list", *argv, "-C", root])
            return code, out.getvalue(), err.getvalue()

        for spelling, ids in (("failing", ["C1"]), ("fail", ["C1"]),
                              ("pending build", ["C5"]), ("pending_build", ["C5"]),
                              ("unclaimed", ["C6", "C7"]), ("Gap", ["C6", "C7"])):
            with self.subTest(spelling):
                code, out, _err = run("--status", spelling)
                self.assertEqual(code, 0)
                self.assertEqual(re.findall(r"^\[.{5}\] (C\d+)", out, re.M), ids, out)
        for spelling in ("failed", "chekced", "errored"):
            with self.subTest(spelling):
                code, out, err = run("--status", spelling)
                self.assertEqual(code, 2, out)
                self.assertIn("is no status", err)
                self.assertIn('"pending build"', err)
                self.assertNotIn("no claims match", out)

    def test_why_says_unrun_in_the_tables_word(self):
        """`why`'s line for an unrun evaluator takes its word from `HUMAN` (review
        of P2.1: it was a literal in `decisions`, outside the one table)."""
        from atompipe import decisions as decisions_mod
        from atompipe.models import Claim, Ledger
        ledger = Ledger(claims=[Claim(id="C1", statement="s", gates=["g.never"])])
        with mock.patch.object(report_mod, "HUMAN", sentinel_human()):
            said = decisions_mod.why(ledger, "C1")
        self.assertRegex(said, r"\[ -- \] g\.never : zzunrun")

    def test_a_prerequisite_reason_routes_through_human(self):
        """A claim Skipped behind a prerequisite reads its lead, its phrase and
        the root's kind from `HUMAN`, never from the spine's `skip_reason`
        (critique of the P2.2 design: "(errored)", "(skipped)" and "not
        registered" were spelled in `gates`, where a sentinel never reached)."""
        from atompipe.models import Claim, Ledger, Verdict
        claim = Claim(id="C1", statement="s", tags=["d"])
        for kind, root, lead in (("failed", "t.p", "zzskipped"),
                                 ("errored", "t.p", "zzerrored"),
                                 ("skipped", "t.p", "zzskipped"),
                                 ("unqualified", "t.p", "zzskipped"),
                                 ("not-registered", "t.ghost", "zzskipped")):
            with self.subTest(kind):
                verdict = Verdict(gate="t.d", claims=["d"], skipped=True,
                                  skip_reason="SPINE TEXT", blocked_by=[root],
                                  blocked_kind=kind)
                ledger = Ledger(claims=[claim], verdicts=[verdict])
                with mock.patch.object(report_mod, "HUMAN", sentinel_human()):
                    composed = claims_mod.compose(claim, ledger.verdicts)
                    said = report_mod.reason(composed, ledger, claim)
                self.assertTrue(said.startswith(f"{lead}: t.d : zzprerequisite"), said)
                self.assertIn(root, said)
                self.assertNotIn("SPINE TEXT", said)
                if kind != "failed":
                    self.assertRegex(said, r"\(zz[^)]*\)$")

    def test_a_renderer_with_its_own_literal_is_caught(self):
        """Planted: a `status_tag` that appends a literal `checked`."""
        ledger, registry = self._ledger()
        real = report_mod.reason

        def literal(*args, **kwargs):
            return real(*args, **kwargs) + " (checked)"

        with mock.patch.object(report_mod, "HUMAN", sentinel_human()), \
                mock.patch.object(report_mod, "reason", literal):
            terminal = report_mod.render_terminal(ledger, registry, stale_gates={"probe.moved"})
        self.assertTrue([p for p in routing_problems("render_terminal",
                                                     self._terminal_lines(terminal))
                         if "'checked'" in p])

    def test_the_page_holds_no_status_word(self):
        """`app.js`, `format.js` and `panels.js` take every status word — and
        `invalidated`, and a gap record's state — from `state.json` (D-16: the
        site never owns one), in EVERY string literal: a ternary's arm and a
        group's blurb as much as a `label:`. What slipped through (review of
        P2.1): the scan read `label:`/`hint:`/`text:` values only, so "No gate
        covers this claim" (a ternary arm), "An assumption nobody owns is a
        gap." (a blurb) and `≈ STALE` (app.js, unscanned) passed it."""
        self.assertEqual(page_word_problems(_page_sources()), [])
        planted = dict(_page_sources())
        planted["format.js"] = planted["format.js"].replace(
            'pass:       { glyph: "✓", tone: "ok" },',
            'pass:       { label: "PROVEN", glyph: "✓", tone: "ok" },')
        self.assertTrue(page_word_problems(planted))

    def test_a_ternary_and_a_blurb_are_scanned(self):
        """Planted: the two literals the review found, back in place."""
        sources = _page_sources()
        for name, old, new in (
                ("panels.js", "claim.reason && claim.cause !== \"checked\"",
                 "claim.reason || \"No gate covers this claim.\""),
                ("format.js", "Each row says whether its owner has recorded it.",
                 "An assumption nobody owns is a gap."),
                ("app.js", 'text: `≈ ${phrase("invalidated")}`', 'text: "≈ STALE"'),
                ("panels.js", "tag(needWord(g.status),", 'tag(g.status === "open" ? '
                                                          '"identified" : g.status,')):
            with self.subTest(name=name, new=new):
                self.assertIn(old, sources[name])
                planted = dict(sources, **{name: sources[name].replace(old, new)})
                self.assertTrue(page_word_problems(planted), new)

    def test_a_checked_claim_shows_no_reason_on_the_page(self):
        """A Checked claim's reason is `—` (the terminal's "no reason" cell), and
        the page prints a claim's reason only when its cause is not `checked`.
        What slipped through (review of P2.1): every Checked row got a lone `—`
        paragraph, because the dash is truthy."""
        from atompipe.models import Claim, Ledger, Verdict
        claim = Claim(id="C1", statement="s", gates=["g.1"])
        found = claims_mod.compose(claim, [Verdict(gate="g.1", claims=["C1"], passed=True)])
        view = report_mod.status_view(found, Ledger(claims=[claim]), claim)
        self.assertEqual((view["cause"], view["reason"]), ("checked", "—"))
        panels = _page_sources()["panels.js"]
        self.assertRegex(panels, r'claim\.reason && claim\.cause !== "checked"\s*\n\s*\? el\("p", '
                                 r'\{ class: "claim-reason"')

    def test_the_page_takes_its_other_words_from_state(self):
        """`state.json`'s `phrases` is `HUMAN`'s: `invalidated`, every gap
        record state's word (`identified` for `open`, GLOSSARY §6), and a title
        for each verdict chip's outcome — none of them the page's own."""
        phrases = report_mod.page_phrases()
        self.assertEqual(phrases["invalidated"], "invalidated")
        self.assertEqual(phrases["need"]["open"], "identified")
        # R-6, widened (review of P2.4): a pass outside its operating context
        # has a chip of its own, and so a title from the table.
        self.assertEqual(sorted(phrases["outcome_hint"]),
                         ["errored", "fail", "outside-context", "pass", "skipped"])
        self.assertNotIn("hint:", _page_sources()["format.js"].split("const VERDICT_STATUS", 1)[1]
                         .split("};", 1)[0])
        with mock.patch.object(report_mod, "HUMAN", sentinel_human()):
            self.assertEqual(report_mod.page_phrases()["invalidated"], "zzinvalidated")


def _page_sources() -> dict[str, str]:
    out = {}
    for name in ("app.js", "lib/format.js", "lib/panels.js"):
        with open(os.path.join(site_mod.TEMPLATE_DIR, *name.split("/")),
                  encoding="utf-8") as fh:
            out[os.path.basename(name)] = fh.read()
    return out


def js_literals(source: str) -> list[tuple[str, str]]:
    """Every string literal of a page script, with the code just before it —
    double- and single-quoted, and template literals with each `${…}` blanked —
    comments skipped."""
    out: list[tuple[str, str]] = []
    i, n = 0, len(source)
    while i < n:
        if source.startswith("//", i):
            end = source.find("\n", i)
            i = n if end < 0 else end
        elif source.startswith("/*", i):
            end = source.find("*/", i + 2)
            i = n if end < 0 else end + 2
        elif source[i] in "\"'`":
            quote, j, buf = source[i], i + 1, []
            while j < n and source[j] != quote:
                if source[j] == "\\":
                    buf.append(source[j:j + 2])
                    j += 2
                elif quote == "`" and source.startswith("${", j):
                    depth, j = 1, j + 2
                    while j < n and depth:
                        depth += {"{": 1, "}": -1}.get(source[j], 0)
                        j += 1
                    buf.append(" ")
                elif quote != "`" and source[j] == "\n":
                    break
                else:
                    buf.append(source[j])
                    j += 1
            out.append(("".join(buf), source[max(0, i - 12):i]))
            i = j + 1
        else:
            i += 1
    return out


#: Status words, and the ledger's other words the page takes from `state.json`
#: (`invalidated`'s Never-say twins, a gap record's state), in any string a
#: page script holds. A lone lower-case token compared with `===`/`!==` or given
#: as a `class:` is an identifier (a status value, a class name) and is exempt;
#: anything else is read — a ternary's arm too; a backticked span is a command
#: (`atompipe gap --propose`).
_IDENTIFIER = re.compile(r"[a-z][a-z0-9_-]*")
_IDENTIFIER_SLOT = re.compile(r"(?:[!=]==?|class:)\s*$")
_PAGE_WORDS = ("proven", "checked", "failing", "stale", "assumed", "pending build", "gap",
               "skipped", "open", "blocked", "unverified", "verified", "refuted",
               "not run", "never run", "no gate", "not current", "identified")
#: Phrases that hold a word above as the name of something else: the record kind
#: the gap panel lists ("Gap records", the terminal's `gap records` line).
_PAGE_MASKED = ("gap record",)


def page_word_problems(sources: dict[str, str]) -> list[str]:
    out = []
    for name, text in sources.items():
        for literal, before in js_literals(text):
            if _IDENTIFIER.fullmatch(literal) and _IDENTIFIER_SLOT.search(before):
                continue
            said = re.sub(r"`[^`]*`", " ", literal)
            for phrase in _PAGE_MASKED:
                said = re.sub(rf"(?i){re.escape(phrase)}", " ", said)
            for word in _PAGE_WORDS:
                if re.search(rf"(?i)(?<![\w-]){re.escape(word)}(?![\w-])", said):
                    out.append(f"{name}: {said!r} says {word!r}")
    return out


# --------------------------------------------------------------------------- #
# V15
# --------------------------------------------------------------------------- #
_READY_RUNS = (("check", ["check", "--junit"]), ("check.json", ["check", "--json"]),
               ("status", ["status"]), ("status.json", ["status", "--json"]))
#: The page's half, built on the variants that need it (the site build is the
#: slowest command a variant runs).
_SITE_RUNS = (("site.init", ["site", "init"]), ("site.build", ["site", "build"]))


def _variant(name: str, *, drop: tuple = (), not_required: tuple = (),
             physical_pass: bool = False, edit_model: bool = False,
             site: bool = False) -> World:
    """The bracket at thickness 8.0 (C1 passes), with claims dropped or made not
    required, optionally a typed physical pass on C5 and a model edit after."""
    if name in _WORLDS:
        return _WORLDS[name]
    base = _WORLDS.get("@8.0")
    if base is None:
        root = _projects.bracket_copy(os.path.join(_tmp("atompipe-vocab-8-"), "bracket"),
                                      thickness=8.0, migrated=True)
        _env.atompipe(["check"], cwd=root)
        base = _WORLDS["@8.0"] = World(root, {}, {})
    tmp = _tmp(f"atompipe-vocab-{name}-")
    root = os.path.join(tmp, "bracket")
    shutil.copytree(base.root, root)
    for cid in drop:
        os.remove(os.path.join(root, "claims", f"{cid}.json"))
    for cid in not_required:
        path = os.path.join(root, "claims", f"{cid}.json")
        with open(path, encoding="utf-8") as fh:
            record = json.load(fh)
        record["critical"] = False
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(record, fh, indent=2)
    if physical_pass:
        # A pass from a pipe (P2.5a): recorded, and it counts for nothing. C5
        # is given what a pass needs — a test written down and evidence — so
        # the refusals of an incomplete pass are not what this world shows.
        path = os.path.join(root, "claims", "C5.json")
        with open(path, encoding="utf-8") as fh:
            record = json.load(fh)
        record["note"] = "outdoor rack, two winters, look for crazing at the root"
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(record, fh, indent=2)
        os.makedirs(os.path.join(root, "photos"), exist_ok=True)
        with open(os.path.join(root, "photos", "c5.jpg"), "wb") as fh:
            fh.write(b"a photo")
        proc = _env.atompipe(["claim", "physical", "C5", "--pass", "--evidence",
                              "photos/c5.jpg"], cwd=root, identity=True)
        if proc.returncode != 0:
            raise AssertionError(f"claim physical: {proc.stderr}")
    if edit_model:
        model = os.path.join(root, "model", "bracket.py")
        with open(model, encoding="utf-8") as fh:
            text = fh.read()
        with open(model, "w", encoding="utf-8") as fh:
            fh.write(re.sub(r"^(    bed_xy: float = )220\.0$", r"\g<1>250.0", text,
                            flags=re.M))
    return _run_world(name, root, _READY_RUNS + (_SITE_RUNS if site else ()), _FILES)


def ready_problems(world: World, *, ready: bool) -> list[str]:
    """Where *ready* is said and false, or true and not said — on the readiness
    sentence, `check`'s line, the JSON and JUnit keys beside `ready`, and the
    page's headline input."""
    out = []
    sentence = world.out["status"].stdout.splitlines()[1]
    says = bool(re.search(r"\bis ready\b", sentence))
    if says != ready:
        out.append(f"status: the sentence {'says' if says else 'does not say'} ready: "
                   f"{sentence[:90]}")
    check = world.out["check"].stdout.splitlines()
    readies = [ln for ln in check if ln.startswith("ready:")]
    if bool(readies) != ready:
        out.append(f"check: {'a' if readies else 'no'} `ready:` line: {readies or check[-1:]}")
    doc = json.loads(world.out["check.json"].stdout)
    if doc.get("all_required_checked") is not ready:
        out.append(f"check.json: all_required_checked {doc.get('all_required_checked')}")
    status = json.loads(world.out["status.json"].stdout)["summary"]
    if status.get("all_required_checked") is not ready:
        out.append(f"status.json: all_required_checked {status.get('all_required_checked')}")
    junit = ET.fromstring(world.files["junit"])
    props = {p.get("name"): p.get("value") for p in junit.iterfind("properties/property")}
    if props.get("all_required_checked") != ("true" if ready else "false"):
        out.append(f"junit: all_required_checked {props.get('all_required_checked')}")
    if "state" in world.files:
        state = json.loads(world.files["state"])["readiness"]
        if state.get("all_required_checked") is not ready:
            out.append(f"state: all_required_checked {state.get('all_required_checked')}")
    return out


class ReadyIsThePredicate(unittest.TestCase):
    """(V15, in process) `report.readiness` — the one predicate the readiness
    sentence, `check`'s line, the JSON and JUnit keys and the page's headline
    read — over a ledger for each case: one claim waiting for an article, every
    required claim checked, a typed physical pass, no required claim, a project
    never evaluated whose one claim has a typed pass. The fast tier's half of
    V15; the commands are `ReadyMeansEveryRequiredClaimChecked`'s.

    Every sentence that is not ready says `NOT ready` (review of P2.1: the
    never-evaluated branch never did, and beside a typed pass it said "Nothing
    below is checked … 1 claim is checked on an article"), and a typed pass is
    Pending build in every line, never "checked on an article" (R-6: the case
    asked for that phrase, which said *checked* of a claim that is not)."""

    def _composed(self, *claims_and_verdicts):
        from atompipe.models import Claim, ClaimKind, Ledger, PhysicalResult, Verdict
        claims, verdicts = [], []
        for cid, kind, required, result in claims_and_verdicts:
            gates = [f"g.{cid}"] if kind == "measurable" else []
            claims.append(Claim(id=cid, statement=cid, kind=ClaimKind(kind), gates=gates,
                                critical=required,
                                physical_result=None if result is None
                                else PhysicalResult(passed=result)))
            if gates:
                verdicts.append(Verdict(gate=gates[0], claims=[cid], passed=True))
        ledger = Ledger(claims=claims, verdicts=verdicts)
        return ledger, claims_mod.compositions(ledger)

    def _says(self, ledger, composed):
        found = report_mod.readiness(ledger, composed)
        sentence = report_mod._verdict_sentence(ledger, composed, None, stale=False,
                                                markdown=False)
        return found["ready"], sentence, report_mod.not_ready_line(ledger, composed)

    CASES = {
        "a claim waiting for an article": (
            [("C1", "measurable", True, None), ("C5", "physical", True, None)], False,
            "1 pending build (C5)"),
        "every required claim checked": ([("C1", "measurable", True, None)], True,
                                         "ready: every required claim is checked"),
        "a typed physical pass": ([("C1", "measurable", True, None),
                                   ("C5", "physical", True, True)], False,
                                  "1 pending build (C5)"),
        "never evaluated, a typed physical pass": ([("C5", "physical", True, True)], False,
                                                   "1 pending build (C5)"),
        "no required claim": ([("C1", "measurable", False, None)], False,
                              "no claim is required"),
        "ready, a claim not required waiting for an article": (
            [("C1", "measurable", True, None), ("C5", "physical", False, None)], True,
            "ready: every required claim is checked"),
    }

    def problems(self) -> list[str]:
        out = []
        for name, (rows, ready, line) in self.CASES.items():
            ledger, composed = self._composed(*rows)
            said, sentence, check_line = self._says(ledger, composed)
            if said is not ready:
                out.append(f"{name}: readiness says {said}")
            if bool(re.search(r"\bis ready\b", sentence)) != ready:
                out.append(f"{name}: the sentence: {sentence[:80]}")
            if line not in check_line:
                out.append(f"{name}: check says {check_line[:80]}")
            if not ready and "NOT ready" not in sentence:
                out.append(f"{name}: the sentence never says NOT ready: {sentence[:80]}")
            if any(kind == "physical" and result is None for _c, kind, _r, result in rows) \
                    and "Pending build: 1 claim needs an article (C5)." not in sentence:
                out.append(f"{name}: no hardware sentence")
            if any(kind == "physical" and result is True for _c, kind, _r, result in rows):
                if "Pending build: 1 claim needs an article (C5); C5 has a pass that does " \
                        "not count." not in sentence:
                    out.append(f"{name}: the hardware sentence does not name the typed pass")
                if re.search(r"(?i)\bchecked on an article\b", sentence + check_line):
                    out.append(f"{name}: says the typed pass is checked")
                if report_mod.count_line(composed) != f"{len(rows)} claim" + (
                        "s" if len(rows) != 1 else "") + (
                        " · 1 checked" if len(rows) > 1 else "") + " · 1 pending build":
                    out.append(f"{name}: counts {report_mod.count_line(composed)}")
        return out

    def test_each_case(self):
        self.assertEqual(self.problems(), [])

    def test_a_predicate_keyed_on_nothing_blocking_is_caught(self):
        """Planted: `readiness` as `860ffa6` read it — ready when nothing blocks."""
        def nothing_blocks(ledger_, composed_):
            required = [c for c in ledger_.claims if c.critical]
            return {"required": required, "unresolved": [], "unbound": [], "ready": True}

        with mock.patch.object(report_mod, "readiness", nothing_blocks):
            found = self.problems()
        for name in ("a claim waiting for an article", "a typed physical pass"):
            with self.subTest(name):
                self.assertTrue(any(p.startswith(f"{name}: readiness says True")
                                    for p in found), found)


class ReadyMeansEveryRequiredClaimChecked(unittest.TestCase):
    """(V15) *Ready* — the word, READY on the page, the keys beside `ready` — iff
    every required claim reads Checked on the current inputs (GLOSSARY §4)."""

    def test_the_bracket_is_not_ready(self):
        world = bracket_world()
        self.assertIn("is NOT ready", world.out["status"].stdout.splitlines()[1])
        self.assertFalse([ln for ln in world.out["check"].stdout.splitlines()
                          if ln.startswith("ready:")])
        self.assertEqual(world.out["check"].returncode, 1)
        self.assertEqual(ready_problems(_variant("at8"), ready=False), [])
        self.assertEqual(_WORLDS["at8"].out["check"].returncode, 1)

    def test_a_claim_waiting_for_an_article_is_not_ready_and_does_not_stop_check(self):
        """Only C5, Pending build, is unresolved: `check` exits 0 — nothing
        stops it — and says what stands between the project and ready; `ready`
        stays true for its old readers and `all_required_checked` says false."""
        world = _variant("pending", drop=("C6", "C7"), site=True)
        self.assertEqual(world.out["check"].returncode, 0)
        self.assertEqual(ready_problems(world, ready=False), [])
        self.assertIn("nothing stops this check run — 1 required claim is unresolved: "
                      "1 pending build (C5)", world.out["check"].stdout)
        for key in ("check.json",):
            self.assertIs(json.loads(world.out[key].stdout)["ready"], True)
        self.assertIs(json.loads(world.out["status.json"].stdout)["summary"]["ready"], True)

    def test_every_required_claim_checked_is_ready(self):
        world = _variant("ready", drop=("C5", "C6", "C7"), site=True)
        self.assertEqual(world.out["check"].returncode, 0)
        self.assertEqual(ready_problems(world, ready=True), [])
        self.assertIn("ready: every required claim is checked", world.out["check"].stdout)

    def test_a_typed_physical_pass_never_makes_a_project_ready(self):
        """An unattributed pass typed with `claim physical` reads Pending build —
        a pass no article binds to the current inputs — never Checked and never
        ready, before a model edit or after it (review of the P2.1 design; and of
        P2.1, R-6 toward unresolved: it read Checked everywhere but *ready*)."""
        world = _variant("typed", drop=("C6", "C7"), physical_pass=True)
        self.assertEqual(ready_problems(world, ready=False), [])
        status = world.out["status"].stdout
        self.assertIn("C5 has a pass that does not count", status)
        self.assertTrue(any(ln.startswith("[build] C5 ") and ln.endswith(
            "a pass recorded from a pipe or a script does not count — the person who tested "
            "it records it in their own shell (recorded by atompipe tests "
            "<tests@atompipe.invalid>)")
            for ln in status.splitlines()), status)
        self.assertRegex(status, r"(?m)^\d+ claims · \d+ checked · 1 pending build$")
        world = _variant("typed-edited", drop=("C6", "C7"), physical_pass=True,
                         edit_model=True)
        self.assertEqual(ready_problems(world, ready=False), [])

    def test_no_required_claim_is_not_ready(self):
        world = _variant("none-required",
                         not_required=("C1", "C2", "C3", "C4", "C5", "C6", "C7"))
        self.assertEqual(world.out["check"].returncode, 0)
        self.assertEqual(ready_problems(world, ready=False), [])

    def test_ready_still_says_what_waits_for_an_article(self):
        """The hardware sentence stays in every branch (W13, GLOSSARY §9): a
        ready project with a not-required physical claim says it."""
        world = _variant("ready-pending", drop=("C6", "C7"), not_required=("C5",))
        self.assertEqual(ready_problems(world, ready=True), [])
        self.assertIn("Pending build: 1 claim needs an article (C5).",
                      world.out["status"].stdout.splitlines()[1])

    def test_the_ready_checks_refuse_what_they_forbid(self):
        """Planted: a sentence keyed on `summary["ready"]`, `check`'s old
        `ready:` line, a JSON writer that drops the new key, and the page's
        headline wired to `readiness.ready`."""
        world = _variant("pending", drop=("C6", "C7"), site=True)
        status = world.out["status"].stdout.replace("is NOT ready", "is ready", 1)
        check = world.out["check"].stdout + "ready: no critical claim is blocking\n"
        doc = json.loads(world.out["check.json"].stdout)
        doc.pop("all_required_checked")
        planted = world._replace(out=dict(world.out, status=_Proc(status), check=_Proc(check),
                                          **{"check.json": _Proc(json.dumps(doc))}))
        found = ready_problems(planted, ready=False)
        for want in ("status: the sentence says ready", "check: a `ready:` line",
                     "check.json: all_required_checked None"):
            with self.subTest(want):
                self.assertTrue(any(p.startswith(want) for p in found), found)
        with open(os.path.join(site_mod.TEMPLATE_DIR, "lib", "panels.js"),
                  encoding="utf-8") as fh:
            panels = fh.read()
        self.assertIn("const ready = !!r.all_required_checked;", panels)
        self.assertNotIn("const ready = !!r.ready;", panels)


if __name__ == "__main__":
    unittest.main(verbosity=2)


# --------------------------------------------------------------------------- #
# V4 (P2.3): qualification's words, from one table, on every channel
# --------------------------------------------------------------------------- #
#: GLOSSARY §2's qualification Never-says, and its mutation and known-bad
#: control rows' (non-italic, plus the italic ones a scanner can still read on
#: these channels: *fired*, *flip*, *BROKEN*), parsed where they parse and
#: typed where §2's italics put them out of the parser's reach.
QUALIFICATION_NEVER = re.compile(
    r"(?i)(?<![\w-])(?:admitted|admission|undemonstrated|selftest passed|re-verif\w*"
    r"|negative control|known-bad input|mutants?|killed|survived|flip\w*|perturb\w*"
    r"|fire|fires|fired|broken)(?![\w-])")

#: Qualification's own words: none may reach a channel except through
#: `HUMAN["qualification"]`.
_QUALIFICATION_WORDS = re.compile(
    r"(?<![\w-])(?:known-good|known-bad|mutation|qualified|unqualified|conclusive|"
    r"inconclusive)(?![\w-])")


def _zz(text: str) -> str:
    """Every word of a template prefixed `zz`, its `{fields}` untouched."""
    out = []
    for part in re.split(r"(\{[^}]*\})", text):
        out.append(part if part.startswith("{") else
                   re.sub(r"(?<![\w-])([A-Za-z][\w-]*)", r"zz\1", part))
    return "".join(out)


def _sentinel(value: Any) -> Any:
    if isinstance(value, str):
        return _zz(value)
    if isinstance(value, (dict, MappingProxyType)):
        return MappingProxyType({k: _sentinel(v) for k, v in value.items()})
    return value


def qualification_sentinel() -> Any:
    """``HUMAN`` with its qualification words prefixed ``zz``: the
    ``qualification`` section, and the one word another section holds — the
    ``unqualified`` lead of a Gap reason (``HUMAN["lead"]``), which the check
    summary, ``status``'s counts and the report's verdict rows say too."""
    human = report_mod.HUMAN
    lead = dict(human["lead"])
    lead[claims_mod.ClaimCause.UNQUALIFIED] = _zz(lead[claims_mod.ClaimCause.UNQUALIFIED])
    return MappingProxyType(dict(human, qualification=_sentinel(human["qualification"]),
                                 lead=MappingProxyType(lead)))


#: An always-False evaluator in a bracket copy, with a claim of its own.
_NEVER = '''\
from atompipe.gates import gate
from atompipe.models import NegativeControl, Verdict


@gate(id="bracket.never", claims=["never-probe"],
      negative_control=NegativeControl(fixture="selftest/bad_configs.py:quarter_thickness"))
def never(ctx):
    d = float(ctx.params["deflection"])
    return Verdict(gate="bracket.never", passed=False, measured=round(d, 4), limit=0.5)
'''


def _never_bracket() -> str:
    root = _projects.bracket_copy(os.path.join(_tmp("atompipe-vocab-q-"), "bracket"),
                                  migrated=True)
    with open(os.path.join(root, "gates", "zz_never.py"), "w", encoding="utf-8") as fh:
        fh.write(_NEVER)
    with open(os.path.join(root, "claims", "C9.json"), "w", encoding="utf-8") as fh:
        json.dump({"statement": "the never probe holds", "kind": "measurable",
                   "tags": ["never-probe"]}, fh)
    return root


#: A planted logger in a copy of beam-analytic outside the bundled packs: its
#: deflection gate passes whatever it is handed. Pack mode's channels.
_LOGGER_PLANT = ("def deflection(ctx: GateContext) -> Verdict:\n",
                 "    return Verdict(gate=\"beam.deflection\", passed=True)\n")


def _logger_pack() -> str:
    """The planted copy, under a pack name of its own (one process loads a
    pack name from one place)."""
    base = _tmp("atompipe-vocab-pack-")
    name = "beamvocab"
    pack_dir = os.path.join(base, name)
    shutil.copytree(os.path.join(_projects.PACKS, "beam-analytic"), pack_dir,
                    ignore=shutil.ignore_patterns("__pycache__", ".selftest-out"))
    with open(os.path.join(pack_dir, "pack.json"), encoding="utf-8") as fh:
        manifest = json.load(fh)
    manifest["name"] = name
    with open(os.path.join(pack_dir, "pack.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)
    path = os.path.join(pack_dir, "gates", "beam.py")
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    anchor, body = _LOGGER_PLANT
    assert text.count(anchor) == 1, "the beam pack moved: plant the logger elsewhere"
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text.replace(anchor, anchor + body))
    return pack_dir


def _junit_file_messages(path: str) -> list[str]:
    """Every outcome message a JUnit file carries, gate and claim suites alike:
    a human channel (GLOSSARY §7)."""
    tree = ET.parse(path)
    return [el.get("message", "") for el in tree.iter()
            if el.tag in ("failure", "error", "skipped") and el.get("message")]


def _qualification_channels(root: str, pack: str = "") -> dict[str, list[str]]:
    """Every channel that shows a qualification, captured in process: `check`
    and its JUnit messages, `status`, `gate show`, `gate selftest` (project mode,
    and pack mode with `pack validate` over ``pack``), `report`, `doctor`, and
    the help of the two commands that run controls. What slipped through
    (review of P2.3): pack mode and the JUnit message were scanned by nothing,
    so `control did not fire … PASSED its own known-bad fixture` and the
    token `unqualified: qualification:not-yet|0` reached them."""
    out: dict[str, list[str]] = {}
    for key, argv in (("check", ["check"]), ("status", ["status"]),
                      ("gate.show", ["gate", "show", "bracket.never"]),
                      ("gate.show.ok", ["gate", "show", "bracket.deflection"]),
                      ("gate.selftest", ["gate", "selftest"]), ("report", ["report"]),
                      ("doctor", ["doctor"])):
        out[key] = _captured([*argv, "-C", root]).splitlines()
    junit = os.path.join(_tmp("atompipe-vocab-junit-"), "check.xml")
    _captured(["check", "--junit", junit, "-C", root])
    out["check.junit"] = _junit_file_messages(junit)
    if pack:
        out["gate.selftest.pack"] = _captured(["gate", "selftest", "--pack", pack]).splitlines()
        out["pack.validate"] = _captured(["pack", "validate", pack]).splitlines()
    for key, argv in (("help.selftest", ["gate", "selftest", "--help"]),
                      ("help.check", ["check", "--help"])):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
            try:
                cli_mod.main(argv)
            except SystemExit:
                pass
        out[key] = buf.getvalue().splitlines()
    return out


class QualificationWordsComeFromOneTable(unittest.TestCase):
    """(V4, D-16) Patching `HUMAN["qualification"]` moves every channel that
    shows a qualification, and none of them says a word GLOSSARY §2 forbids —
    `admitted`, `re-verified`, `negative control`, `fired`, `BROKEN` and the
    rest. What slipped through before P2.3: a qualification was worded in four
    places (`gate show`'s `last selftest`, `gate selftest`'s `fired`/`BROKEN`,
    `check`'s `re-verified`, the refusal text `not admitted: …`), and the spine
    minted the reason a claim row printed."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.root = _never_bracket()
        cls.pack = _logger_pack()
        with mock.patch.object(report_mod, "HUMAN", qualification_sentinel()):
            cls.patched = _qualification_channels(cls.root, cls.pack)
        cls.real = _qualification_channels(cls.root, cls.pack)

    def test_pack_mode_and_junit_are_scanned(self):
        """The floor for the channels review of P2.3 found unscanned: each says
        something, and says the planted logger is unqualified."""
        for channel in ("gate.selftest.pack", "pack.validate", "check.junit"):
            with self.subTest(channel=channel):
                self.assertTrue(self.real[channel], f"{channel} captured nothing")
        self.assertTrue(any("beam.deflection" in ln and "unqualified" in ln
                            for ln in self.real["pack.validate"]), self.real["pack.validate"])
        self.assertTrue(any("bracket.never" in ln or "unqualified" in ln
                            for ln in self.real["check.junit"]), self.real["check.junit"])
        self.assertIsNotNone(QUALIFICATION_NEVER.search(
            "problem: beam.deflection: control did not fire: it is a logger"),
            "the pack-mode line review of P2.3 found is a Never-say")

    def test_every_qualification_word_comes_from_the_table(self):
        problems = []
        seen = 0
        for channel, lines in self.patched.items():
            if channel.startswith("help."):
                continue
            for line in lines:
                if "zz" in line:
                    seen += 1
                for word in _QUALIFICATION_WORDS.findall(line):
                    problems.append(f"{channel}: {word!r} not from HUMAN: {line[:100]}")
        self.assertEqual(problems, [])
        self.assertGreaterEqual(seen, 10, "a floor: the sentinel reached the channels")

    def test_no_channel_says_a_never_say(self):
        problems = []
        scanned = 0
        for channel, lines in self.real.items():
            for line in lines:
                scanned += 1
                hit = QUALIFICATION_NEVER.search(line)
                if hit:
                    problems.append(f"{channel}: {hit.group(0)!r}: {line[:100]}")
        self.assertEqual(problems, [])
        self.assertGreater(scanned, 60, "a floor on lines scanned")

    def test_the_planted_renderers_are_caught(self):
        real = report_mod.qualification_line

        def own_literal(gate_id, facts):
            return real(gate_id, facts).rsplit(" → ", 1)[0] + " → qualified"

        with mock.patch.object(report_mod, "HUMAN", qualification_sentinel()), \
                mock.patch.object(report_mod, "qualification_line", own_literal):
            lines = _captured(["gate", "selftest", "-C", self.root]).splitlines()
        self.assertTrue(any(_QUALIFICATION_WORDS.search(ln) for ln in lines),
                        "a renderer with its own `qualified` literal is seen under the sentinel")
        self.assertIsNotNone(QUALIFICATION_NEVER.search(
            "[ERR ] bracket.never : not admitted: known-good fail"),
            "a `not admitted:` in a rendered row is a Never-say")


# --------------------------------------------------------------------------- #
# review of P2.4 — the acceptance condition has one name
# --------------------------------------------------------------------------- #
def acceptance_never_says(text: str) -> set[str]:
    """The non-italic Never-says of GLOSSARY §1's *acceptance condition* row
    alone, parsed — §1's other rows forbid words (*gate*) the shipped doctrine
    still uses until the rename pass, so this scan reads the one row P2.4's
    words went around."""
    for row in _rows(_section(text, 1)):
        if re.sub(r"\*\*", "", row[0]).strip() == "acceptance condition":
            return {re.sub(r"\s*\(.*?\)\s*", " ", item).strip().lower()
                    for item in row[-1].split(" · ")
                    if item.strip() and not item.strip().startswith("*")}
    return set()


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, (dict, MappingProxyType)):
        return [s for v in value.values() for s in _strings(v)]
    if isinstance(value, (tuple, list)):
        return [s for v in value for s in _strings(v)]
    return [s for s in (getattr(value, f, None) for f in getattr(value, "_fields", ()))
            if isinstance(s, str)]


#: The shipped doctrine an agent quotes (GLOSSARY §7): every word P2.4 put on it.
_DOCTRINE = ("CLAUDE.md", "README.md", "METHOD.md", "skills/atompipe/SKILL.md",
             "skills/pack-authoring/SKILL.md", "examples/bracket/README.md")


class TheAcceptanceConditionHasOneName(unittest.TestCase):
    """What slipped through P2.4 (review): *goalpost* — in no GLOSSARY row — named
    the acceptance condition or its limit on every human channel P2.4 touched:
    `value moves with its goalpost` on the qualification line, `never chosen by
    its goalpost` in a Gap reason, `gate show`'s `goalpost` rows, an errored
    pass's detail, CLAUDE.md's invariants 4, 7 and 9, both skills and the
    bracket's README. One term per concept (D-16): it is now a Never-say of the
    acceptance condition's row, parsed here, so the next one is red."""

    def _lines(self) -> dict[str, list[str]]:
        out = {"HUMAN": _strings(report_mod.HUMAN)}
        for rel in _DOCTRINE:
            with open(os.path.join(_env.REPO, *rel.split("/")), encoding="utf-8") as fh:
                out[rel] = fh.read().splitlines()
        from atompipe import gates as gates_mod, verdicts as verdicts_mod
        from atompipe.models import Acceptance, Claim, Comparator, Ledger, Verdict
        claim = Claim(id="c1", statement="c1", acceptance=Acceptance(
            quantity="x", comparator=Comparator.LE, limit=0.5, units="mm"))

        def lying(ctx):
            ctx.acceptance("c1")
            return Verdict(gate="t.g", passed=True, measured=0.7, units="mm")
        spec = gates_mod.GateSpec(id="t.g", claims=["c1"], negative_control=(
            models_mod.NegativeControl(fixture="selftest/bad.py:make")))
        ctx = gates_mod.GateContext(root="", ledger=Ledger(claims=[claim]), params={})
        held = gates_mod.run_gate(spec, lying, ctx, trace=verdicts_mod.GateTrace())
        out["errored pass"] = [held.error, held.detail]
        out["no claim"] = [verdicts_mod.acceptance_of([claim], "nowhere").problem]
        return out

    def test_the_row_forbids_goalpost(self):
        self.assertTrue({"goalpost", "goalposts", "pass criteria"}
                        <= acceptance_never_says(_glossary()))

    def test_no_human_channel_says_it(self):
        banned = acceptance_never_says(_glossary())
        masks = masked_terms(_glossary())
        found = [f"{channel}: {hit}: {line[:90]}" for channel, lines in self._lines().items()
                 for line in lines for hit in never_say_hits(line, banned, masks)]
        self.assertEqual(found, [])

    def test_the_scan_finds_the_words_p24_shipped(self):
        banned = acceptance_never_says(_glossary())
        masks = masked_terms(_glossary())
        for line in ("t.keyed : known-good pass · known-bad fail · value moves with its "
                     "goalpost → unqualified",
                     "**Never move a goalpost in a gate.**",
                     "the acceptance conditions it read as goalposts"):
            with self.subTest(line=line):
                self.assertTrue(never_say_hits(line, banned, masks))


# --------------------------------------------------------------------------- #
# review of P2.4 — the operating context's and the comparison's prose
# --------------------------------------------------------------------------- #
def _context_world():
    """A ledger with one claim of each P2.4 fact: Checked (c2, its value
    compared and inside), a pass outside its operating context (c1) and a pass
    whose value misses its claim's condition (c3)."""
    from atompipe import gates as gates_mod
    from atompipe.models import (Acceptance, Claim, Comparator, GateSpec, Ledger,
                                 NegativeControl, Verdict)
    nc = NegativeControl(fixture="selftest/bad.py:make")
    specs = [GateSpec(id=gid, claims=[cid], settles="sag", negative_control=nc,
                      operating_context=({"load_n": (0.0, 40.0)} if gid == "t.span" else {}))
             for gid, cid in (("t.span", "c1"), ("t.ok", "c2"), ("t.loose", "c3"))]
    token = gates_mod.context_token(gates_mod.ContextBreach("load_n", 60, 0.0, 40.0, "outside"))

    def claim(cid):
        return Claim(id=cid, statement=f"{cid} holds", gates=[], acceptance=Acceptance(
            quantity="sag", comparator=Comparator.LE, limit=0.5, units="mm"))

    def passed(gid, cid, measured, limit, **kw):
        return Verdict(gate=gid, passed=True, claims=[cid], measured=measured, limit=limit,
                       units="mm", comparator="<=", settles="sag", **kw)
    ledger = Ledger(claims=[claim("c1"), claim("c2"), claim("c3")],
                    verdicts=[passed("t.span", "c1", 0.3, 0.5, unqualified=token),
                              passed("t.ok", "c2", 0.3, 0.5),
                              passed("t.loose", "c3", 0.7, 1.0)])
    registry = gates_mod.Registry()
    for spec in specs:
        registry.register(spec, lambda ctx: None)
    return ledger, registry


class ContextAndComparisonWordsComeFromOneTable(unittest.TestCase):
    """What slipped through P2.4 (review): the report's `### Outside an
    evaluator's operating context` paragraph and the checked table's new clause
    were literals beside `HUMAN["context"]` and `HUMAN["acceptance"]`, which hold
    the same words — editing the table left them behind, and V11's sentinel had
    no outside-context world to notice. P2.4-D22: words only in `report.HUMAN`."""

    def _rendered(self, human):
        ledger, specs = _context_world()
        with mock.patch.object(report_mod, "HUMAN", human):
            return report_mod.render_markdown(ledger, specs)

    def test_the_world_reaches_each_fact(self):
        text = self._rendered(report_mod.HUMAN)
        self.assertIn("### Outside an evaluator's operating context", text)
        self.assertIn("An evaluator was qualified on a range of its inputs", text)
        self.assertIn("inside its operating context, and every value compared", text)
        self.assertIn("acceptance condition not met", text)

    def test_the_prose_moves_with_the_table(self):
        human = report_mod.HUMAN
        moved = MappingProxyType(dict(human, context=_sentinel(human["context"]),
                                      acceptance=_sentinel(human["acceptance"])))
        text = self._rendered(moved)
        for literal in ("An evaluator was qualified on a range of its inputs",
                        "outside it a pass does not count",
                        "inside its operating context, and every value compared"):
            with self.subTest(literal=literal):
                self.assertNotIn(literal, text)
        self.assertIn("zzAn zzevaluator zzwas zzqualified zzon zza zzrange", text)
        self.assertIn("zzinside zzits zzoperating zzcontext", text)


# --------------------------------------------------------------------------- #
# V-15 (P2.5a): the physical path's words come from one table
# --------------------------------------------------------------------------- #
#: GLOSSARY's Never-says for what the physical path prints (§1 *physical result*,
#: *recorded by*, *authority*, *article*; §2 *verified*; §3 *Checked*) — and the
#: P2.5a design's own rejected words (*signed*, *unsigned*, *attested*).
PHYSICAL_NEVER_SAY = ("signed", "unsigned", "signer", "verified", "unverified", "confirmed",
                      "attested", "proven", "hardware", "real part", "approver", "real-world")

#: A terminal's identifier on a human line (P2.5a-D1: the display words only).
_RAW_TERMINAL = re.compile(r"\b(?:closed_form|solver|human)\b|\[none\]")


def physical_word_problems(lines: dict[str, str], masks: tuple = ()) -> list[str]:
    """Every Never-say and every raw terminal token on the physical path's
    human lines, and a *contradict* anywhere but a contradiction's own line.
    A line that is a record's own text (``masks``: a claim's statement,
    rationale or note — the user's words, which no table owns) and the checked
    section's heading (``SECTION_PROVEN``, allowlisted until A-11) are not
    scanned."""
    out: list[str] = []
    record = " ".join(" ".join(str(m).split()) for m in masks)
    for key, text in lines.items():
        for line in text.splitlines():
            low = line.lower()
            if line.startswith(report_mod.SECTION_PROVEN) or (
                    line.strip() and " ".join(line.split()) in record):
                continue
            for word in PHYSICAL_NEVER_SAY:
                if re.search(rf"(?<![\w-]){re.escape(word)}(?![\w-])", low):
                    out.append(f"{key}: says {word!r}: {line[:90]}")
            if _RAW_TERMINAL.search(line) and not line.lstrip().startswith(("usage:", "-")):
                out.append(f"{key}: a raw terminal token: {line[:90]}")
            if "contradict" in low and not re.search(r"contradiction|contradicts", low):
                out.append(f"{key}: 'contradict' off a contradiction line: {line[:90]}")
    return out


class PhysicalWordsComeFromOneTable(unittest.TestCase):
    """(V-15; D-16, GLOSSARY §7) Every word `claim physical`, its prompt, its
    refusals, `gate show`'s track record, `why`'s results and the report say
    about a physical result, an owner or an authority is `report.HUMAN`'s, and
    none is a Never-say."""

    def test_no_line_says_a_never_say(self):
        import _physical as P
        from atompipe import store
        found = P.transcript()
        lines = P.human_lines(found)
        self.assertGreaterEqual(len(lines), 15)
        masks = tuple(text for claim in store.load(found["root"]).claims
                      for text in (claim.statement, claim.rationale, claim.note) if text)
        self.assertEqual(physical_word_problems(lines, masks), [])

    def test_the_planted_lines_are_caught(self):
        planted = {"recorded": "recorded C5 pass — confirmed in hardware",
                   "claim.list": "[ok   ] C1     Tip sags  [measurable] tip deflection <= 0.5 mm",
                   "page": "the resolver and the verdicts contradict each other",
                   "terminal": "[gap  ] C8 Safe  [human] no acceptance condition"}
        found = physical_word_problems(planted)
        for key in ("recorded", "page", "terminal"):
            with self.subTest(key):
                self.assertTrue(any(p.startswith(f"{key}:") for p in found), found)

    def test_the_page_owns_no_contradict(self):
        path = os.path.join(_env.REPO, "src", "atompipe", "site_template", "lib", "panels.js")
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        self.assertNotIn("contradict each other", text)
        self.assertIn('phrase("disagree")', text)

    def test_claim_list_prints_the_terminals_word(self):
        import _physical as P
        rows = [ln for ln in P.transcript()["claim.list"].stdout.splitlines()
                if re.match(r"^\[.{5}\] C\d", ln)]
        self.assertTrue(rows)
        for row in rows:
            with self.subTest(row=row[:20]):
                self.assertRegex(row, r"  \[(automated|measurement|assumption|expert judgment: "
                                      r".+)\] ")
                self.assertNotRegex(row, r"\[(measurable|physical)\]")

    def test_the_words_move_with_the_table(self):
        """The physical sentences are the table's: a sentinel table reaches the
        rendered reasons."""
        from atompipe.models import EntryStanding, PhysicalResult, Standing
        result = PhysicalResult(passed=True, who="Sam <s@x>", channel="agent-session s1",
                                article={"hash": "a" * 64})
        claim = Claim(id="C5", statement="s", kind=ClaimKind.PHYSICAL, physical_result=result,
                      results=(result,),
                      standing=Standing("not-counted:agent-session", 0, "a" * 64, (),
                                        (EntryStanding(0, True, False, "agent-session",
                                                       "a" * 64, "current"),)))
        moved = dataclasses.replace(claim, standing=Standing(
            "article-moved", 0, "a" * 64, ("config.t 7.0 -> 8.0",),
            (EntryStanding(0, True, False, "article-moved", "a" * 64, "moved"),)))
        human = report_mod.HUMAN
        sentinel = MappingProxyType(dict(human, not_counted=_sentinel(human["not_counted"]),
                                         physical=_sentinel(human["physical"])))
        with mock.patch.object(report_mod, "HUMAN", sentinel):
            said = report_mod.reason(claims_mod.compose(claim, []), Ledger(), claim)
            said_moved = report_mod.reason(claims_mod.compose(moved, []), Ledger(), moved)
        self.assertNotIn("a pass recorded from an agent session", said)
        self.assertIn("zza zzpass zzrecorded zzfrom zzan zzagent zzsession", said)
        self.assertIn("zza zznew zzarticle zzis zzneeded", said_moved)



# --------------------------------------------------------------------------- #
# V-11 (P2.5b) — the hardware clause, in every branch of every rendering
# --------------------------------------------------------------------------- #
#: The two forms of the clause that says what is checked on an article.
_ON_ARTICLE = re.compile(r"Checked on an article: \d+ \(|No claim is checked on any article\.")
_LIMITS = "Checked does not mean true: "


def hardware_problems(name: str, sentence: str, ledger: Any, composed: Any) -> list[str]:
    """The hardware clause (W13) in one rendering of the readiness sentence:
    exactly one on-article clause; `Pending build: …` iff a claim reads Pending
    build; one `Rebuild article <a12>` per rebuild row."""
    out = []
    found = _ON_ARTICLE.findall(sentence)
    if len(found) != 1:
        out.append(f"{name}: {len(found)} on-article clauses: {sentence[-160:]}")
    pending = any(c.status is ClaimStatus.UNVERIFIED for c in composed.values())
    if ("Pending build: " in sentence) is not pending:
        out.append(f"{name}: Pending build {'missing' if pending else 'said'}: "
                   f"{sentence[-160:]}")
    rows = claims_mod.rebuild(ledger)
    if sentence.count("Rebuild article ") != len(rows):
        out.append(f"{name}: {sentence.count('Rebuild article ')} rebuild clauses for "
                   f"{len(rows)} rows")
    for row in rows:
        if f"Rebuild article {row.article[:12]} " not in sentence:
            out.append(f"{name}: no rebuild clause for {row.article[:12]}")
    return out


def _sentence_of(md: str) -> str:
    return next(ln for ln in md.splitlines()[1:] if ln.strip())


class TheHardwareClauseIsAlwaysSaid(unittest.TestCase):
    """(V-11, W13; P2.5a's hand-off) Every rendering of the readiness sentence —
    the project's and a milestone's, markdown and plain, `report`, REPORT.md and
    the page — says, in every branch, what is pending build, which articles
    need a rebuild and what is checked on an article; REPORT.md carries the
    limits paragraph; and no rendering says a GLOSSARY §3-§5 Never-say."""

    def ledgers(self) -> list[tuple[str, Any, frozenset]]:
        import test_export
        out = []
        case = ReadyIsThePredicate()
        for name, (rows, _ready, _line) in ReadyIsThePredicate.CASES.items():
            ledger, _composed = case._composed(*rows)
            out.append((name, ledger, frozenset()))
        for seed in range(80):
            ledger, stale = test_export.seeded(seed)
            out.append((f"seed {seed}", ledger, stale))
        return out

    def problems(self) -> list[str]:
        text = _glossary()
        banned = never_says(text, 3) | never_says(text, 4) | never_says(text, 5)
        masks = masked_terms(text)
        out = []
        for name, ledger, stale in self.ledgers():
            composed = claims_mod.compositions(ledger, stale_gates=stale)
            renderings = {}
            for markdown in (False, True):
                renderings[f"sentence{'.md' if markdown else ''}"] = report_mod._verdict_sentence(
                    ledger, composed, None, stale=False, markdown=markdown)
            md = report_mod.render_markdown(ledger, None, stale_gates=stale)
            renderings["REPORT.md"] = _sentence_of(md)
            if md.count(_LIMITS) != 1:
                out.append(f"{name}: REPORT.md carries {md.count(_LIMITS)} limits paragraphs")
            for milestone in getattr(ledger, "milestones", None) or ():
                renderings[f"sentence[{milestone.id}]"] = report_mod._verdict_sentence(
                    ledger, composed, None, stale=False, markdown=False, milestone=milestone)
                mmd = report_mod.render_markdown(ledger, None, stale_gates=stale,
                                                 milestone=milestone)
                renderings[f"REPORT.md[{milestone.id}]"] = _sentence_of(mmd)
                if mmd.count(_LIMITS) != 1:
                    out.append(f"{name}: REPORT.md[{milestone.id}] carries "
                               f"{mmd.count(_LIMITS)} limits paragraphs")
            for key, sentence in renderings.items():
                out += hardware_problems(f"{name} {key}", sentence, ledger, composed)
                for hit in never_say_hits(sentence, banned, masks):
                    out.append(f"{name} {key}: says {hit!r}")
        return out

    def test_every_branch(self):
        found = self.problems()
        self.assertEqual(found[:10], [], f"{len(found)} problems")

    def test_the_seeds_reach_a_rebuild_and_an_article(self):
        """Not vacuous: some ledger names a rebuild, some a claim checked on an
        article, some neither."""
        kinds = set()
        for _name, ledger, stale in self.ledgers():
            composed = claims_mod.compositions(ledger, stale_gates=stale)
            sentence = report_mod._verdict_sentence(ledger, composed, None, stale=False,
                                                    markdown=False)
            kinds |= {k for k, needle in (("rebuild", "Rebuild article "),
                                          ("article", "Checked on an article: "),
                                          ("none", "No claim is checked on any article."),
                                          ("pending", "Pending build: "))
                      if needle in sentence}
        self.assertEqual(kinds, {"rebuild", "article", "none", "pending"})

    def test_a_ready_branch_without_the_clause_is_caught(self):
        """Planted: the ready branch drops the clause."""
        real = report_mod._verdict_sentence

        def dropped(*args, **kwargs):
            said = real(*args, **kwargs)
            if re.search(r"\bis ready\b", said):
                said = _ON_ARTICLE.sub("", said)
            return said

        with mock.patch.object(report_mod, "_verdict_sentence", dropped):
            found = self.problems()
        self.assertTrue([p for p in found if "on-article clauses" in p], found[:5])
