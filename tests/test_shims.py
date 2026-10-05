# SPDX-License-Identifier: Apache-2.0
"""The CLI's writers once records are files (checkpoint 1.3): `check` writes no
record, each kept writer writes exactly one, and three commands are gone.

What slipped through before this existed:

* **The whole-ledger writer.** Every writing command loaded the whole project and
  saved the whole project, so a command asked to record one decision rewrote every
  claim, every parameter and the run bookkeeping beside it — and `check`, a sweep,
  rewrote the records on every run (`sync_params`, `_link_grounding`,
  `_refresh_coverage`, then `store.save`). A record a human edited between two
  commands was overwritten by the second from its in-memory copy.
  `NoWholeLedgerWriterInCli` walks `cli.py`'s AST so that path cannot come back.
* **S-36.** `extract --grounds arm_length` wrote the back-reference into the
  parameter's record (`_link_grounding`), so deleting the extraction left the
  grounding behind forever: `why arm_length` said "GROUNDED BY arm" while `inputs`
  said `arm` was "NEVER READ". Grounding is derived from the extractions on read;
  the `extract` shim rewrites the artifact's record and nothing else.
* **S-37.** `claim edit --gates X` bound nothing, and the next `check`'s
  `_refresh_coverage` silently reverted it. Coverage is the registry's, and a claim
  is edited as its file, `claims/<id>.json`; `claim add`, `claim edit` and
  `packs remove` are gone (PLAN A-8), and `claim physical`'s refusal names the file
  edit instead of the command that no longer exists.
* **S-44.** `decide --when` backdated a decision: the one clock the CLI stamps at
  its edge could be overridden by a flag, and the log rendered in storage order,
  so a backdated entry sat on top as the newest. The flag is gone; `when` is the
  edge's stamp.

`CheckMigratesOnce` is the legacy bracket's first `check` under the real
`modelio.static_param_prose`: it migrates (no `params/*.json`, by the params rule),
exits 1 with the G4 signature, and a second `check` changes no byte; `--no-record`
migrates in memory only.

Every command runs in a subprocess (`_env.nopekit`) on a temp copy of the bracket;
nothing here writes into the tracked tree.

Run:  PYTHONPATH=src python3 -m unittest tests.test_shims -v
"""
from __future__ import annotations

import ast
import contextlib
import io
import json
import os
import re
import sys
import tempfile
import textwrap
import unittest

import _env
import _projects
from nopekit import cli, modelio, store

CLI_PY = os.path.join(_env.REPO, "src", "nopekit", "cli.py")
SIGNATURE = os.path.join(_env.REPO, "tests", "oracle", "bracket_signature.py")
EXPECTED = os.path.join(_env.REPO, "tests", "expected_bracket.json")

#: The generated index. It is ignored by git and rebuilt by every command, so a
#: shim's diff may carry it; what it may never carry is a second RECORD.
INDEX = ".nopekit/ledger.json"

#: A fixed stamp for the in-process migration that builds the migrated fixture:
#: it appears only in the migration's notice, never in a record.
WHEN = "2026-09-28T00:00:00Z"

#: What the edge stamps (`util.utcnow_iso`): second resolution, UTC, a `Z`.
STAMP = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")

#: Scratch and memory `check` writes on every run, all of it ignored by the
#: migration's deny-list block: gate scratch and the JUnit report (`out/`), the
#: last check's summary and the digest cache (`cache/`), and what each run cost
#: (`obs/`). "A second check changes no byte" is about everything else.
SCRATCH = (".nopekit/out/", ".nopekit/cache/", ".nopekit/obs/")


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _snapshot(root: str) -> dict[str, bytes]:
    """``{posix path: bytes}`` of every file under ``root``."""
    out: dict[str, bytes] = {}
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            full = os.path.join(dirpath, name)
            with open(full, "rb") as fh:
                out[os.path.relpath(full, root).replace(os.sep, "/")] = fh.read()
    return out


def _changed(before: dict[str, bytes], after: dict[str, bytes]) -> set[str]:
    """Every path added, removed or rewritten between two snapshots."""
    return {path for path in set(before) | set(after) if before.get(path) != after.get(path)}


def _outside(paths: set[str], prefixes: tuple[str, ...]) -> set[str]:
    return {p for p in paths if not p.startswith(prefixes)}


def _write(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def _run(project: str, *argv: str, identity: bool = False):
    return _env.nopekit(list(argv), cwd=project, identity=identity)


def _json(proc) -> dict:
    try:
        return json.loads(proc.stdout)
    except ValueError as exc:                     # pragma: no cover - reported, not raised
        raise AssertionError(f"not one JSON document ({exc}):\n{proc.stdout}\n{proc.stderr}")


def _migrated(dest: str) -> str:
    """A copy of the bracket, migrated to record files in-process — the layout a
    shim meets on every project after its first `check`."""
    project = _projects.bracket_copy(dest)
    store.migrate_legacy(project, apply=True, when=WHEN,
                         model_prose=modelio.static_param_prose)
    return project


def _evidence(directory: str, name: str = "notes.txt",
              text: str = "arm measured at 60.2 mm with calipers\n") -> str:
    """An evidence file OUTSIDE the project, as a user's download would be."""
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, name)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    return path


def _in_process(argv: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(argv)
    return code, out.getvalue(), err.getvalue()


# --------------------------------------------------------------------------- #
# the shims: one record each
# --------------------------------------------------------------------------- #
class ShimsWriteExactlyOneFile(_env.EnvCase):
    """Snapshot the tree, run one shim, and the diff is exactly its record — plus
    the bytes for `ingest`, plus the ignored index. Never a second record, never a
    generated document, never the whole ledger."""

    def setUp(self) -> None:
        self.project = _migrated(os.path.join(self.tmp(), "bracket"))
        self.outside = os.path.join(self.tmp(), "downloads")

    def _one_shim(self, *argv: str, want: set[str], identity: bool = False):
        before = _snapshot(self.project)
        proc = _run(self.project, *argv, identity=identity)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        after = _snapshot(self.project)
        self.assertEqual(_changed(before, after) - {INDEX}, want,
                         f"`nopekit {' '.join(argv)}` wrote other than its one record")
        self.assertEqual(store.agree(self.project), [],
                         "the index disagrees with the records after the shim")
        return proc

    def _ingest(self) -> dict:
        path = _evidence(self.outside)
        proc = _run(self.project, "ingest", path, "--desc", "caliper reading", "--json")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        (artifact,) = _json(proc)["ingested"]
        return artifact

    def test_ingest_writes_its_bytes_and_one_record(self):
        path = _evidence(self.outside)
        before = _snapshot(self.project)
        proc = _run(self.project, "ingest", path, "--desc", "caliper reading", "--json")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        (artifact,) = _json(proc)["ingested"]
        after = _snapshot(self.project)
        record = f"inputs/{artifact['id']}.json"
        self.assertEqual(_changed(before, after) - {INDEX}, {artifact["path"], record})
        with open(path, "rb") as fh:
            self.assertEqual(after[artifact["path"]], fh.read(),
                             "the bytes were not copied as they were")
        pinned = store.read_record(os.path.join(self.project, *record.split("/")), "inputs")
        self.assertEqual(pinned.sha256, artifact["sha256"])
        self.assertEqual(store.agree(self.project), [])

        again = _run(self.project, "ingest", path, "--json")
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
        self.assertEqual(_changed(after, _snapshot(self.project)) - {INDEX}, set(),
                         "re-ingesting the same bytes wrote something")

    def test_extract_rewrites_one_input_record(self):
        artifact = self._ingest()
        self._one_shim("extract", artifact["id"], "--what", "arm is 60.2 mm",
                       "--grounds", "arm_length", "--confidence", "measured",
                       want={f"inputs/{artifact['id']}.json"})
        record = store.read_record(
            os.path.join(self.project, "inputs", artifact["id"] + ".json"), "inputs")
        self.assertEqual([(e.what, e.grounds) for e in record.extractions],
                         [("arm is 60.2 mm", ["arm_length"])])
        self.assertFalse(os.listdir(os.path.join(self.project, "params")),
                         "S-36: extract wrote a back-reference into a param record")

    def test_decide_writes_one_decision_record(self):
        proc = self._one_shim("decide", "--title", "Thickness stays 7 mm",
                              "--summary", "the deflection gate fails on purpose",
                              "--rejected", "4.0 mm|3.75 mm deflection, 7.5x the limit",
                              "--json", want={"decisions/thickness-stays-7-mm.json"})
        data = _json(proc)
        self.assertEqual(data["id"], "thickness-stays-7-mm")
        self.assertFalse(os.path.exists(os.path.join(self.project, "docs", "decisions.md")),
                         "decide regenerated the decision log; it is report --write's output")

    def test_packs_add_writes_only_project_json(self):
        self._one_shim("packs", "add", "beam-analytic", want={".nopekit/project.json"})
        self.assertEqual(store.read_project(self.project).packs, ["beam-analytic"])
        again = _snapshot(self.project)
        proc = _run(self.project, "packs", "add", "beam-analytic")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(_changed(again, _snapshot(self.project)) - {INDEX}, set(),
                         "adding an installed pack again wrote something")

    def test_claim_physical_appends_to_results(self):
        """P2.5a (R-6, a strengthening): no `--who` — who recorded it is the git
        identity, never typed — and a pass on C5 needs its written test and its
        evidence, which the project is given first, outside the shim's run."""
        with open(os.path.join(self.project, "claims", "C5.json"), encoding="utf-8") as fh:
            record = json.load(fh)
        record["note"] = "hang 1.5 kg for 24 h; look for creep at the root"
        _write(os.path.join(self.project, "claims", "C5.json"), json.dumps(record, indent=2))
        _write(os.path.join(self.project, "photos", "c5.jpg"), "a photo")
        self._one_shim("claim", "physical", "C5", "pass", "--evidence", "photos/c5.jpg",
                       "--detail", "held 1.5 kg for 24 h", want={"results/C5.json"},
                       identity=True)
        path = os.path.join(self.project, "results", "C5.json")
        with open(path, "rb") as fh:
            first = fh.read()
        self._one_shim("claim", "physical", "C5", "--fail",
                       "--detail", "cracked at the bolt after a week",
                       want={"results/C5.json"}, identity=True)
        results = store.read_record(path, "results")
        self.assertEqual([r.passed for r in results], [True, False],
                         "a result must be appended, never replace the one before")
        self.assertEqual(results[0].detail, "held 1.5 kg for 24 h")
        who = f"{_env.IDENTITY['GIT_AUTHOR_NAME']} <{_env.IDENTITY['GIT_AUTHOR_EMAIL']}>"
        self.assertEqual([r.who for r in results], [who, who])
        self.assertTrue(STAMP.fullmatch(results[1].when), results[1].when)
        with open(path, "rb") as fh:
            self.assertEqual(json.loads(first)["results"][0], json.loads(fh.read())["results"][0])
        shown = _json(_run(self.project, "claim", "show", "C5", "--json"))
        self.assertEqual(shown["status"], "refuted", "the claim reads its LAST result")

    def test_a_shim_on_a_legacy_project_migrates_first(self):
        """Q1.5: a shim is a migration trigger. On a legacy copy `decide` migrates
        (the one notice, with its `git rm --cached` line), then writes its record
        into the layout it just made — never into the legacy ledger."""
        project = _projects.bracket_copy(os.path.join(self.tmp(), "legacy"))
        with open(os.path.join(project, INDEX), "rb") as fh:
            legacy = fh.read()
        proc = _run(project, "decide", "--title", "Keep PETG", "--summary", "heat is fine")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(proc.stderr.count("git rm --cached .nopekit/ledger.json"), 1,
                         proc.stderr)
        self.assertTrue(os.path.isfile(os.path.join(project, ".nopekit", "project.json")))
        self.assertTrue(os.path.isfile(os.path.join(project, "decisions", "keep-petg.json")))
        with open(os.path.join(project, ".nopekit", "ledger.legacy.json"), "rb") as fh:
            self.assertEqual(fh.read(), legacy, "the legacy ledger was not kept as it was")
        self.assertEqual(store.agree(project), [])


class DecideTakesTheEdgeClock(_env.EnvCase):
    """S-44: a decision's `when` is the CLI's one clock stamp, never a flag."""

    def test_decide_has_no_when_flag(self):
        project = _migrated(os.path.join(self.tmp(), "bracket"))
        before = _snapshot(project)
        backdated = _run(project, "decide", "--title", "Backdated", "--summary", "s",
                         "--when", "2001-01-01T00:00:00Z")
        self.assertEqual(backdated.returncode, 2, backdated.stdout + backdated.stderr)
        self.assertIn("unrecognized arguments: --when", backdated.stderr)
        self.assertEqual(_changed(before, _snapshot(project)), set(),
                         "a refused command wrote something")

        stamped = _run(project, "decide", "--title", "Stamped", "--summary", "s", "--json")
        self.assertEqual(stamped.returncode, 0, stamped.stdout + stamped.stderr)
        when = _json(stamped)["when"]
        self.assertTrue(STAMP.fullmatch(when), when)
        record = store.read_record(os.path.join(project, "decisions", "stamped.json"),
                                   "decisions")
        self.assertEqual(record.when, when)


class ClaimPhysicalNamesTheFile(_env.EnvCase):
    """`claim edit` is gone, so the refusal that sent a user to it names the edit
    that replaces it."""

    def test_a_result_on_an_automated_claim_settles_nothing(self):
        """P2.5a-D10 (R-6: reversed from `test_claim_physical_refusal_names_the_file_edit`,
        and stronger): a physical result is recordable against a claim an
        automated evaluator settles — E4 needs it (PLAN-v0.14 §1.2), F3 — and a
        pass there settles nothing: C1 reads by its evaluator on every channel,
        exactly as before. The property the refusal stood for is kept: a typed
        pass never stands in for an evaluator."""
        project = _migrated(os.path.join(self.tmp(), "bracket"))
        before = {key: _json(_run(project, *argv)) for key, argv in (
            ("status", ("status", "--json")), ("report", ("report", "--json")),
            ("show", ("claim", "show", "C1", "--json")))}
        proc = _run(project, "claim", "physical", "C1", "pass", "--detail", "looked fine",
                    identity=True)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("settles nothing", proc.stdout)
        self.assertTrue(os.path.exists(os.path.join(project, "results", "C1.json")))
        after = {key: _json(_run(project, *argv)) for key, argv in (
            ("status", ("status", "--json")), ("report", ("report", "--json")),
            ("show", ("claim", "show", "C1", "--json")))}
        for key in ("status", "report"):
            with self.subTest(key):
                self.assertEqual(after[key]["claims"]["C1"], before[key]["claims"]["C1"])
                self.assertEqual(after[key]["statuses"]["C1"]["cause"],
                                 before[key]["statuses"]["C1"]["cause"])
        self.assertEqual((after["show"]["status"], after["show"]["cause"]),
                         (before["show"]["status"], before["show"]["cause"]))


# --------------------------------------------------------------------------- #
# the removed commands
# --------------------------------------------------------------------------- #
class RemovedCommands(_env.EnvCase):
    def test_removed_commands_are_gone(self):
        """A-8: argparse refuses each as an invalid choice; the kept shims parse.

        Run against an empty temp directory (`-C`), so that on a spine where a
        command still exists it fails for want of a project instead of writing
        into whichever project the test runner's cwd sits in."""
        empty = self.tmp()
        for argv, word in ((["claim", "add", "--statement", "x"], "'add'"),
                           (["claim", "edit", "C1", "--limit", "0.4"], "'edit'"),
                           (["packs", "remove", "beam-analytic"], "'remove'"),
                           (["pack", "remove", "beam-analytic"], "'remove'")):
            with self.subTest(argv=argv):
                code, out, err = _in_process(["-C", empty, *argv])
                self.assertEqual(code, 2, out + err)
                self.assertIn("invalid choice", err)
                self.assertIn(word, err)
        parser = cli.build_parser()
        for argv in (["extract", "a", "--what", "w"], ["decide", "--title", "t", "--summary", "s"],
                     ["packs", "add", "beam-analytic"], ["claim", "physical", "C5", "pass"],
                     ["ingest", "f"]):
            with self.subTest(kept=argv):
                args = parser.parse_args(argv)
                self.assertTrue(callable(args.func))
        names = {node.name for node in ast.walk(ast.parse(_read(CLI_PY)))
                 if isinstance(node, ast.FunctionDef)}
        self.assertEqual(names & {"cmd_claim_add", "cmd_claim_edit", "cmd_packs_remove",
                                  "_acceptance_from", "_link_grounding", "_refresh_coverage",
                                  "_refresh_param_gates"}, set())


# --------------------------------------------------------------------------- #
# check: migrates once, writes no record
# --------------------------------------------------------------------------- #
class CheckMigratesOnce(_env.EnvCase):
    """The legacy bracket's first recorded `check` is its migration: the claims,
    `project.json`, the three blocks and `ledger.legacy.json` — and no
    `params/*.json`, because the real `static_param_prose` reads a docstring for
    every Config field (the params rule, not a hand deletion). It exits 1 with the
    G4 signature. A second `check` changes no byte outside ignored scratch."""

    @classmethod
    def setUpClass(cls) -> None:
        base = tempfile.mkdtemp(prefix="nopekit-shims-migrate-")
        cls.addClassCleanup(_env._rmtree, base)
        cls.project = _projects.bracket_copy(os.path.join(base, "bracket"))
        cls.legacy = _snapshot(cls.project)
        cls.first = _run(cls.project, "check", "--junit")
        cls.after_first = _snapshot(cls.project)
        cls.second = _run(cls.project, "check")
        cls.after_second = _snapshot(cls.project)

    def test_the_first_check_migrates(self):
        proc = self.first
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertEqual(proc.stderr.count("git rm --cached .nopekit/ledger.json"), 1,
                         proc.stderr)
        self.assertNotIn("git rm", proc.stdout, "the notice belongs on stderr")
        files = set(self.after_first)
        claims = sorted(p for p in files if p.startswith("claims/"))
        self.assertEqual(claims, [f"claims/C{n}.json" for n in range(1, 8)])
        self.assertEqual(sorted(p for p in files if p.startswith("params/")), [],
                         "the params rule wrote a param the model already states")
        for path in (".nopekit/project.json", ".nopekit/ledger.legacy.json",
                     ".gitignore", ".gitattributes"):
            self.assertIn(path, files)
        self.assertEqual(self.after_first[".nopekit/ledger.legacy.json"], self.legacy[INDEX],
                         "the legacy ledger is kept byte for byte")
        self.assertTrue(self.after_first[".nopekit/.gitignore"].startswith(b"# nopekit:begin"))
        index = json.loads(self.after_first[INDEX])
        self.assertEqual(index["generated"], store.INDEX_BANNER, "ledger.json is not the index")
        self.assertEqual(store.agree(self.project), [])

    def test_the_first_check_fails_as_pinned(self):
        """G4: exit 1 and the JUnit signature `tests/expected_bracket.json` pins."""
        junit = os.path.join(self.project, ".nopekit", "out", "junit.xml")
        verdict = _env.run([sys.executable, SIGNATURE, junit, EXPECTED,
                            "--exit-code", str(self.first.returncode)], cwd=self.project)
        self.assertEqual(verdict.returncode, 0, verdict.stdout + verdict.stderr)

    def test_check_writes_no_record(self):
        """What the first check wrote beyond the migration's own files is verdict
        entries and ignored scratch; the migration's files are exactly the plan."""
        written = _outside(_changed(self.legacy, self.after_first),
                           SCRATCH + (".nopekit/verdicts/",))
        migration = {f"claims/C{n}.json" for n in range(1, 8)} | {
            ".nopekit/project.json", ".nopekit/ledger.legacy.json", ".nopekit/.gitignore",
            ".gitignore", ".gitattributes", INDEX}
        self.assertEqual(written, migration)

    def test_a_second_check_changes_no_byte(self):
        self.assertEqual(self.second.returncode, 1, self.second.stdout + self.second.stderr)
        self.assertNotIn("git rm", self.second.stderr, "the migration ran twice")
        self.assertEqual(_outside(_changed(self.after_first, self.after_second), SCRATCH),
                         set())

    def test_no_record_migrates_in_memory_only(self):
        project = _projects.bracket_copy(os.path.join(tempfile.mkdtemp(
            prefix="nopekit-shims-dry-"), "bracket"))
        self.addCleanup(_env._rmtree, os.path.dirname(project))
        before = _snapshot(project)
        proc = _run(project, "check", "--no-record", "--json")
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        data = _json(proc)
        self.assertEqual({row["gate"]: row["outcome"] for row in data["verdicts"]}
                         ["bracket.deflection"], "fail")
        self.assertIn("will migrate", proc.stderr)
        self.assertNotIn("git rm", proc.stderr)
        self.assertEqual(_outside(_changed(before, _snapshot(project)), (".nopekit/out/",)),
                         set(), "--no-record wrote outside gate scratch")


# --------------------------------------------------------------------------- #
# the index after every command
# --------------------------------------------------------------------------- #
def _edit_limit(project: str, claim: str, limit: float) -> None:
    """A hand edit of one record, as a human (or an agent's Edit) makes it."""
    path = os.path.join(project, "claims", f"{claim}.json")
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    data["acceptance"]["limit"] = limit
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(data, indent=2, ensure_ascii=False) + "\n")


class IndexIsTouched(_env.EnvCase):
    """`_touch_index`: every command on a migrated project leaves the index
    agreeing with the records — a hand edit made since the last command
    included — except `doctor`, `init` and a `--no-record` run, which write
    nothing of it; and a legacy project, whose `ledger.json` IS the records, is
    never indexed."""

    def _index(self, project: str) -> bytes | None:
        try:
            with open(os.path.join(project, INDEX), "rb") as fh:
                return fh.read()
        except FileNotFoundError:
            return None

    def test_a_read_command_indexes_a_hand_edit(self):
        project = _migrated(os.path.join(self.tmp(), "bracket"))
        self.assertIsNone(self._index(project), "the in-process migration wrote an index")
        _edit_limit(project, "C1", 0.4)
        proc = _run(project, "claim", "list")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(store.agree(project), [])
        index = json.loads(self._index(project))
        (c1,) = [c for c in index["claims"] if c["id"] == "C1"]
        self.assertEqual(c1["acceptance"]["limit"], 0.4)

        again = self._index(project)
        _run(project, "status")
        self.assertEqual(self._index(project), again, "an unchanged index was rewritten")

    def test_doctor_and_a_dry_run_leave_it(self):
        project = _migrated(os.path.join(self.tmp(), "bracket"))
        _run(project, "status")
        _edit_limit(project, "C1", 0.4)
        stale = self._index(project)
        self.assertTrue(store.agree(project), "the precondition: the edit staled the index")
        for argv in (["doctor"], ["check", "--no-record"], ["gate", "selftest", "--no-record"]):
            with self.subTest(argv=argv):
                proc = _run(project, *argv)
                self.assertIn(proc.returncode, (0, 1), proc.stdout + proc.stderr)
                self.assertEqual(self._index(project), stale, f"{argv} wrote the index")

    def test_a_legacy_project_is_never_indexed(self):
        project = _projects.bracket_copy(os.path.join(self.tmp(), "legacy"))
        legacy = self._index(project)
        for argv in (["status"], ["claim", "list"], ["report"], ["why", "C1"]):
            with self.subTest(argv=argv):
                proc = _run(project, *argv)
                self.assertIn(proc.returncode, (0, 1), proc.stdout + proc.stderr)
                self.assertEqual(self._index(project), legacy, f"{argv} overwrote the legacy ledger")
                self.assertFalse(os.path.exists(os.path.join(project, ".nopekit",
                                                             "project.json")),
                                 f"{argv} migrated a project it was only reading")

    def test_init_writes_no_index(self):
        root = os.path.join(self.tmp(), "fresh")
        proc = _env.nopekit(["init", "--name", "fresh", "-C", root], cwd=self.tmp())
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertTrue(os.path.isfile(os.path.join(root, ".nopekit", "project.json")))
        self.assertIsNone(self._index(root), "init wrote a ledger.json")


# --------------------------------------------------------------------------- #
# the AST rule: no whole-ledger writer in the CLI
# --------------------------------------------------------------------------- #
def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def whole_ledger_writes(source: str) -> list[str]:
    """``<function>:<line>`` for every way ``source`` reaches `store.save`: a call
    or a bare reference (`f = store.save`), `getattr(store, "save")`, and `save`
    imported from the store module under any name. A command writes the ONE record
    it was asked to (`store.write_record`, `store.write_project`); the whole-ledger
    writer is for tests and the migration (spec §3.17)."""
    tree = ast.parse(source)
    owner: dict[int, str] = {}
    for fn in ast.walk(tree):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for node in ast.walk(fn):
                owner.setdefault(id(node), fn.name)
    store_names = {"store"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module in (None, "nopekit"):
            store_names |= {a.asname or a.name for a in node.names if a.name == "store"}
        elif isinstance(node, ast.Import):
            store_names |= {a.asname for a in node.names
                            if a.name == "nopekit.store" and a.asname}
    found: list[str] = []
    for node in ast.walk(tree):
        where = f"{owner.get(id(node), '<module>')}:{getattr(node, 'lineno', 0)}"
        if (isinstance(node, ast.Attribute) and node.attr == "save"
                and isinstance(node.value, ast.Name) and node.value.id in store_names):
            found.append(where)
        elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
              and node.func.id == "getattr" and len(node.args) >= 2
              and isinstance(node.args[0], ast.Name) and node.args[0].id in store_names
              and isinstance(node.args[1], ast.Constant) and node.args[1].value == "save"):
            found.append(where)
        elif (isinstance(node, ast.ImportFrom)
              and (node.module or "").split(".")[-1] == "store"
              and any(a.name == "save" for a in node.names)):
            found.append(where)
    return sorted(set(found), key=lambda s: (int(s.rsplit(":", 1)[1]), s))


class NoWholeLedgerWriterInCli(unittest.TestCase):
    def test_cli_never_calls_store_save(self):
        found = whole_ledger_writes(_read(CLI_PY))
        self.assertEqual(found, [], "cli.py still writes the whole ledger: " + ", ".join(found))

    def test_the_rule_catches_a_planted_writer(self):
        """V: each spelling of the whole-ledger writer is found; a record writer is not."""
        planted = {
            "a call": ("def cmd(root, ledger):\n    store.save(root, ledger)\n", ["cmd:2"]),
            "a reference": ("def cmd(root, ledger):\n    write = store.save\n"
                            "    write(root, ledger)\n", ["cmd:2"]),
            "getattr": ("def cmd(root, ledger):\n    getattr(store, 'save')(root, ledger)\n",
                        ["cmd:2"]),
            "imported": ("from .store import save as persist\n"
                         "def cmd(root, ledger):\n    persist(root, ledger)\n", ["<module>:1"]),
            "an alias of the module": ("from . import store as st\n"
                                       "def cmd(root, ledger):\n    st.save(root, ledger)\n",
                                       ["cmd:3"]),
        }
        for label, (source, want) in planted.items():
            with self.subTest(label):
                self.assertEqual(whole_ledger_writes(textwrap.dedent(source)), want)
        clean = ("def cmd(root, claim):\n    store.write_record(root, 'claims', claim)\n"
                 "    store.write_project(root, claim)\n    self.save()\n")
        self.assertEqual(whole_ledger_writes(clean), [])


if __name__ == "__main__":
    unittest.main()
