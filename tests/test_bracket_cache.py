# SPDX-License-Identifier: Apache-2.0
"""The bracket's committed cache is current, and the bracket is the one 1.3 migrated (G5).

`examples/bracket` commits six verdict entries and six control entries
(`.atompipe/verdicts/`), so a fresh clone's first `check` is six cache hits, runs
no gate and no control, and leaves `git status` empty — the transcript's opening
and G5's clean tree. Those entries are keyed on the spine digest
(`verdicts.spine_digest()`: a canonical walk of `models`, `gates`, `modelio`,
`verdicts`) and on the bracket's own model, gates and selftest. So any CODE edit
to those, after the entries were written, stales all twelve at once, and the
tree the phase gate checks is dirty again: every `check` writes six new `??`
entries.

What slipped through before this test existed: nothing tied the committed bytes
to the code. The plan's first design made "regenerate the cache after the last
spine edit" a step in a procedure (judges J1, J3: a freeze that was process
only), and a process step is the first thing a later fix forgets — found, if at
all, as a dirty tree at the phase gate's last step, with no word about why.
Here forgetting is a red test that names the commands.

It is **unconditional** — stronger than G6's allowance, which lets the
transcript's starred steps wait for a matching spine (spec §8). When the
canonical walk itself is not portable to this interpreter (the pinned digest of
`test_spine_digest.FIXTURE` differs here too), the committed cache IS stale for
every user of this Python, and the test says that instead of naming the
regeneration: regenerating on a non-portable walk would only move the red to
every other interpreter.

`BracketIsMigrated` pins the rest of what checkpoint 1.3 committed in the
bracket (spec §6.5): the records, the marker, the three marked blocks, no index
and no run history in git, no `params/*.json` (by the params rule, not by hand),
C1 key for key as phase-1.md writes it, and thickness still 7.0 — the failure a
fresh clone is meant to show.

Every checker is a pure function run on the real bracket and on planted
violations of it (a checker that cannot fail is a logger); the regenerate
message is asserted with the checker, so a red test always says what to do.

Run:  PYTHONPATH=src python3 -m unittest tests.test_bracket_cache -v
"""
from __future__ import annotations

import json
import os
import platform
import re
import unittest
from typing import Any

import _env
import _projects
import _transcript as T
import test_fresh_clone
import test_spine_digest
from atompipe import verdicts

#: The bracket's six gates, in registration order.
BRACKET_GATES = ("bracket.deflection", "bracket.bending_stress", "bracket.bearing",
                 "bracket.model_validity", "bracket.bed_fit", "bracket.min_wall")

#: What a red test here tells the person reading it (spec U32, verbatim). The
#: regeneration is idempotent; examples/bracket/README.md spells out every step.
REGENERATE = ("the committed bracket cache was written by another spine or model: in "
              "examples/bracket run `rm -rf .atompipe/verdicts && atompipe check; "
              "atompipe gate selftest; atompipe report --write` and commit the result "
              "(examples/bracket/README.md, \"The committed cache\")")

#: What it says instead when the walk, not the cache, is what moved.
NOT_PORTABLE = ("the canonical AST walk is not portable to Python {version}: "
                "SpineDigestIsPortable's pinned fixture digest differs here too, so every "
                "committed entry reads stale on this interpreter — fix "
                "verdicts.canonical_ast_digest, not the cache")

#: phase-1.md's `claims/C1.json`, as (key, value) pairs so key ORDER is compared
#: too, nested included: the record writer writes dataclass field order, and a
#: file that reads the same but orders its keys otherwise was not written by it.
C1_PAIRS = [
    ("statement", "Tip sags no more than 0.5 mm at rated load"),
    ("kind", "measurable"),
    ("acceptance", [("quantity", "tip deflection"), ("comparator", "<="),
                    ("limit", 0.5), ("units", "mm")]),
    ("rationale", "past ~0.5 mm the droop is visible against a level shelf edge; "
                  "this is a product decision, not a physics one"),
    ("tags", ["stiffness"]),
]

#: The claims the bracket's ledger held, migrated one file each.
CLAIM_IDS = ("C1", "C2", "C3", "C4", "C5", "C6", "C7")


# --------------------------------------------------------------------------- #
# the checkers: facts in, problems out ([] when the cache is current)
# --------------------------------------------------------------------------- #
def walk_is_portable() -> bool:
    """Whether the canonical walk gives the pinned digest on this interpreter."""
    return (verdicts.canonical_ast_digest(test_spine_digest.FIXTURE)
            == test_spine_digest.PINNED)


def fix_message(*, portable: bool | None = None) -> str:
    """The one line a red test ends with: regenerate, or fix the walk."""
    portable = walk_is_portable() if portable is None else portable
    if portable:
        return REGENERATE
    return NOT_PORTABLE.format(version=platform.python_version())


def committed_entries(files: list[str] | None = None) -> dict[str, dict]:
    """``{project-relative path: parsed entry}`` for every file under the bracket's
    ``.atompipe/verdicts/`` that a commit of this tree holds (the fresh-clone
    listing: git where it tracks the bracket, the walk where nothing does)."""
    if files is None:
        _source, files = test_fresh_clone.bracket_listing()
    out: dict[str, dict] = {}
    for rel in files:
        if not rel.startswith(T.VERDICTS_DIR + "/"):
            continue
        path = os.path.join(test_fresh_clone.BRACKET, *rel.split("/"))
        with open(path, "r", encoding="utf-8") as fh:
            try:
                data = json.load(fh)
            except ValueError as exc:
                raise AssertionError(f"committed entry {rel} does not parse: {exc}") from exc
        out[rel] = data
    return out


def entry_problems(entries: dict[str, dict], running: str,
                   gates: tuple[str, ...] = BRACKET_GATES) -> list[str]:
    """The committed entries are exactly one verdict entry and one control entry per
    gate, each written by the ``running`` spine."""
    problems: list[str] = []
    if not running:
        problems.append("verdicts.spine_digest() is empty: the spine's sources are "
                        "unreadable here, so no entry can be Fresh (S-29)")
    kinds: dict[str, dict[str, list[str]]] = {g: {"verdict": [], "control": []}
                                              for g in gates}
    for rel, data in sorted(entries.items()):
        entry = T.ENTRY_PATH.fullmatch(rel)
        control = T.CONTROL_ENTRY_PATH.fullmatch(rel)
        match = entry or control
        if match is None:
            problems.append(f"{rel}: not a verdict or control entry path")
            continue
        gate = match.group("gate")
        kind = "control" if control else "verdict"
        if not isinstance(data, dict):
            problems.append(f"{rel}: not a JSON object")
            continue
        if (data.get("kind") == "control") != (kind == "control"):
            problems.append(f"{rel}: named a {kind} entry, holds kind={data.get('kind')!r}")
        if data.get("gate") != gate:
            problems.append(f"{rel}: under {gate}/ but records gate {data.get('gate')!r}")
        spine = T.entry_spine(data)
        if spine != running:
            problems.append(f"{rel}: written by spine {str(spine)[:12]}, running "
                            f"{running[:12]}")
        if gate not in kinds:
            problems.append(f"{rel}: {gate} is not one of the bracket's gates")
            continue
        kinds[gate][kind].append(rel)
    for gate, found in kinds.items():
        for kind, rels in found.items():
            if len(rels) != 1:
                problems.append(f"{gate}: {len(rels)} committed {kind} "
                                f"entr{'y' if len(rels) == 1 else 'ies'}, expected exactly 1"
                                + (f" ({', '.join(rels)})" if rels else ""))
    return problems


def status_problems(data: dict, gates: tuple[str, ...] = BRACKET_GATES) -> list[str]:
    """``status --json`` on a fresh copy: nothing stale, every gate Fresh and admitted."""
    problems: list[str] = []
    if data.get("stale") is not False:
        problems.append(f"status says stale={data.get('stale')!r}: "
                        f"{data.get('stale_reason') or ''}")
    if data.get("stale_gates"):
        problems.append(f"stale gates: {', '.join(data['stale_gates'])}")
    freshness = data.get("freshness") or {}
    if sorted(freshness) != sorted(gates):
        problems.append(f"freshness names {sorted(freshness)}, expected {sorted(gates)}")
    for gate in gates:
        row = freshness.get(gate) or {}
        if row.get("state") != "fresh":
            problems.append(f"{gate}: {row.get('state')!r}, not fresh"
                            + (f" — {'; '.join(row.get('reasons') or ())}"
                               if row.get("reasons") else ""))
        if row.get("admission") != "admitted":
            problems.append(f"{gate}: admission {row.get('admission')!r}, not admitted")
    return problems


def check_problems(data: dict, gates: tuple[str, ...] = BRACKET_GATES) -> list[str]:
    """``check --json`` on a fresh copy: six hits, no gate and no control run, and
    the one deliberate FAIL still a FAIL."""
    problems: list[str] = []
    counts = data.get("counts") or {}
    controls = counts.get("controls") or {}
    for label, got, want in (("counts.executed", counts.get("executed"), 0),
                             ("counts.cached", counts.get("cached"), len(gates)),
                             ("controls.executed", controls.get("executed"), 0),
                             ("controls.reverified", controls.get("reverified"), 0)):
        if got != want:
            problems.append(f"{label} = {got!r}, expected {want}")
    rows = {row.get("gate"): row for row in data.get("verdicts") or ()}
    if sorted(rows) != sorted(gates):
        problems.append(f"check rows name {sorted(rows)}, expected {sorted(gates)}")
    for gate, row in sorted(rows.items()):
        if not row.get("cached"):
            problems.append(f"{gate}: not served from the cache")
        want = "fail" if gate == "bracket.deflection" else "pass"
        if row.get("outcome") != want:
            problems.append(f"{gate}: {row.get('outcome')!r}, expected {want}")
    return problems


def _json(proc) -> dict:
    try:
        return json.loads(proc.stdout)
    except ValueError as exc:
        raise AssertionError(f"not one JSON document ({exc}):\n{proc.stdout[-2000:]}\n"
                             f"{proc.stderr[-2000:]}") from exc


def _listing(root: str, under: str) -> dict[str, bytes]:
    out: dict[str, bytes] = {}
    base = os.path.join(root, *under.split("/"))
    for dirpath, _dirnames, filenames in os.walk(base):
        for name in filenames:
            path = os.path.join(dirpath, name)
            with open(path, "rb") as fh:
                out[os.path.relpath(path, root).replace(os.sep, "/")] = fh.read()
    return out


class BracketCacheIsCurrent(_env.EnvCase):
    """Every committed entry was written by this spine, and a fresh copy serves all
    six from the cache with nothing run. Never skipped (spec §8)."""

    def fail_with(self, problems: list[str]) -> None:
        if problems:
            self.fail("\n".join(problems) + "\n" + fix_message())

    def test_every_committed_entry_is_at_the_running_spine(self):
        entries = committed_entries()
        self.assertTrue(entries, "examples/bracket commits no verdict entry: " + fix_message())
        self.fail_with(entry_problems(entries, verdicts.spine_digest()))

    def test_a_fresh_copy_reads_every_gate_fresh_and_admitted(self):
        project = _projects.bracket_copy(os.path.join(self.tmp(), "bracket"), migrated=True)
        proc = _env.atompipe(["status", "--json"], cwd=project)
        self.assertEqual(proc.returncode, 0, proc.stderr[-2000:])
        self.fail_with(status_problems(_json(proc)))

    def test_a_fresh_copy_checks_with_nothing_executed(self):
        project = _projects.bracket_copy(os.path.join(self.tmp(), "bracket"), migrated=True)
        before = _listing(project, T.VERDICTS_DIR)
        proc = _env.atompipe(["check", "--json"], cwd=project)
        self.assertEqual(proc.returncode, 1, "the bracket fails bracket.deflection on "
                                             "purpose:\n" + proc.stderr[-2000:])
        problems = check_problems(_json(proc))
        written = sorted(set(_listing(project, T.VERDICTS_DIR)) - set(before))
        if written:
            problems.append(f"check wrote new entries: {', '.join(written)}")
        self.fail_with(problems)


class CacheCheckersRefuse(_env.EnvCase):
    """The checkers above, each on a planted violation: a cache written by another
    spine, a missing or doubled entry, a stale or unadmitted gate, a gate that ran."""

    def setUp(self):
        self.entries = committed_entries()
        self.running = verdicts.spine_digest()
        self.assertEqual(entry_problems(self.entries, self.running), [],
                         "the real cache must pass before a planted one can fail: "
                         + fix_message())

    def test_another_spine_is_refused(self):
        other = "0" * 64
        problems = entry_problems(self.entries, other)
        self.assertEqual(len([p for p in problems if "written by spine" in p]),
                         len(self.entries), problems)
        self.assertTrue(entry_problems(self.entries, ""))

    def test_a_missing_or_a_second_entry_is_refused(self):
        control = next(rel for rel in self.entries if T.CONTROL_ENTRY_PATH.fullmatch(rel))
        missing = {k: v for k, v in self.entries.items() if k != control}
        self.assertTrue(any("0 committed control entries" in p
                            for p in entry_problems(missing, self.running)))
        entry = next(rel for rel in self.entries if T.ENTRY_PATH.fullmatch(rel))
        doubled = dict(self.entries)
        doubled[re.sub(r"/[0-9a-f]{16}-", "/" + "f" * 16 + "-", entry)] = self.entries[entry]
        self.assertTrue(any("2 committed verdict entries" in p
                            for p in entry_problems(doubled, self.running)))
        stray = dict(self.entries)
        stray[".atompipe/verdicts/bracket.deflection/notes.json"] = {}
        self.assertTrue(entry_problems(stray, self.running))

    def test_a_stale_or_unadmitted_gate_is_refused(self):
        good = {"stale": False, "stale_gates": [], "freshness": {
            g: {"state": "fresh", "admission": "admitted", "reasons": []}
            for g in BRACKET_GATES}}
        self.assertEqual(status_problems(good), [])
        stale = json.loads(json.dumps(good))
        stale["freshness"]["bracket.bed_fit"] = {
            "state": "stale", "admission": "admitted",
            "reasons": ["config.bed_xy 220.0 -> 250.0"]}
        self.assertTrue(status_problems(stale))
        pending = json.loads(json.dumps(good))
        pending["freshness"]["bracket.bearing"]["admission"] = "pending"
        self.assertTrue(status_problems(pending))
        flagged = dict(good, stale=True, stale_gates=["bracket.min_wall"])
        self.assertTrue(status_problems(flagged))

    def test_a_gate_or_a_control_that_ran_is_refused(self):
        rows = [{"gate": g, "cached": True,
                 "outcome": "fail" if g == "bracket.deflection" else "pass"}
                for g in BRACKET_GATES]
        good = {"counts": {"executed": 0, "cached": 6,
                           "controls": {"executed": 0, "cached": 6, "reverified": 0}},
                "verdicts": rows}
        self.assertEqual(check_problems(good), [])
        for label, mutate in (
                ("a gate executed", lambda d: d["counts"].update(executed=1, cached=5)),
                ("a control executed", lambda d: d["counts"]["controls"].update(executed=1)),
                ("a control re-verified",
                 lambda d: d["counts"]["controls"].update(reverified=6)),
                ("deflection passed", lambda d: d["verdicts"][0].update(outcome="pass")),
                ("a row not cached", lambda d: d["verdicts"][4].update(cached=False))):
            with self.subTest(label):
                planted = json.loads(json.dumps(good))
                mutate(planted)
                self.assertTrue(check_problems(planted), label)

    def test_a_model_edit_reads_stale_through_the_cli(self):
        """The status checker on a real copy whose model moved: the planted
        violation a later spine or model edit would produce, end to end."""
        project = _projects.bracket_copy(os.path.join(self.tmp(), "bracket"),
                                         migrated=True, thickness=8.0)
        proc = _env.atompipe(["status", "--json"], cwd=project)
        self.assertEqual(proc.returncode, 0, proc.stderr[-2000:])
        problems = status_problems(_json(proc))
        self.assertTrue(any(p.startswith("bracket.deflection:") for p in problems), problems)

    def test_the_message_names_the_fix(self):
        self.assertIn("rm -rf .atompipe/verdicts && atompipe check", fix_message(portable=True))
        self.assertIn("atompipe report --write", fix_message(portable=True))
        not_portable = fix_message(portable=False)
        self.assertIn("not portable", not_portable)
        self.assertIn(platform.python_version(), not_portable)
        self.assertNotIn("rm -rf", not_portable)
        self.assertEqual(fix_message(), fix_message(portable=walk_is_portable()))


class BracketIsMigrated(unittest.TestCase):
    """What checkpoint 1.3 committed in examples/bracket (spec §6.5)."""

    @classmethod
    def setUpClass(cls):
        cls.source, files = test_fresh_clone.bracket_listing()
        cls.files = set(files)

    def read(self, rel: str) -> str:
        with open(os.path.join(test_fresh_clone.BRACKET, *rel.split("/")),
                  encoding="utf-8", newline="") as fh:
            return fh.read()

    def test_the_records_and_the_marker_are_committed(self):
        for rel in (".atompipe/project.json", ".atompipe/.gitignore", ".gitignore",
                    ".gitattributes", *(f"claims/{c}.json" for c in CLAIM_IDS)):
            self.assertIn(rel, self.files, f"{rel} is not committed ({self.source} listing)")
        self.assertEqual(json.loads(self.read(".atompipe/project.json"))["schema"], 2)

    def test_the_index_the_run_history_and_params_are_not(self):
        leftovers = sorted(rel for rel in self.files
                           if rel in (".atompipe/ledger.json", ".atompipe/ledger.legacy.json")
                           or rel.startswith((".atompipe/runs/", "params/")))
        self.assertEqual(leftovers, [], "the index is an output (D-06), the run history "
                                        "is gone (D-05), and no param record is written "
                                        "where the model states the prose (§3.15)")

    def test_c1_is_the_phase_1_example_key_for_key(self):
        pairs = json.loads(self.read("claims/C1.json"), object_pairs_hook=list)
        self.assertEqual(pairs, C1_PAIRS)

    def test_the_ignore_blocks_are_the_migrations(self):
        text = self.read(".atompipe/.gitignore")
        self.assertTrue(text.startswith("# atompipe:begin\n"), text)
        self.assertTrue(text.endswith("# atompipe:end\n"), text)
        patterns = [line for line in text.splitlines() if line and not line.startswith("#")]
        for pattern in ("ledger.json", "ledger.legacy.json", "obs/", "cache/", "out/",
                        "runs/"):
            self.assertIn(pattern, patterns)
        self.assertFalse([p for p in patterns if p.startswith("!")],
                         "an allow-line would re-add the index (D-06)")
        for rel, needles in ((".gitignore", ("__pycache__/", "*.py[cod]")),
                             (".gitattributes", ("* text=auto eol=lf", "*.stl -text"))):
            block = self.read(rel)
            self.assertIn("# atompipe:begin", block, rel)
            for needle in needles:
                self.assertIn(needle, block.splitlines(), rel)

    def test_thickness_is_still_seven(self):
        """The failure a fresh clone is meant to show (phase-1.md, the brief)."""
        found = re.findall(r"^    thickness: float = (\S+)$", self.read("model/bracket.py"),
                           re.MULTILINE)
        self.assertEqual(found, ["7.0"])


if __name__ == "__main__":
    unittest.main()
