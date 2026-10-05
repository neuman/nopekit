# SPDX-License-Identifier: Apache-2.0
"""The CLI's readers once records are files (checkpoint 1.3): `init` makes a
project that is born migrated, `why` and `inputs` read the model and the
extractions rather than a copy of either, and `gap`, `model` and `doctor` write
nothing they were not asked to.

What slipped through before this existed:

* **Born legacy.** `init` wrote a `ledger.json` — the whole project, in the layout
  1.3 migrates away from — so every new project was a legacy one, auto-migrated by
  its first `check` with a `git rm --cached` notice about a file git never tracked.
  Its next steps told the user to run `nopekit model --set-entry`, a flag A-8
  removes: `.nopekit/project.json` owns the entry.
* **S-39.** `why` quoted the ledger's copy of a value — "thickness = 7" after the
  model said 8.0 — because `check` wrote the model into the records and `why` read
  the records. The model is read where a parameter is shown, and a model that does
  not load shows no number at all.
* **S-36.** Grounding had two homes: the extraction, and a copy in the parameter it
  grounds. Deleting the extraction left the copy, so `why arm_length` said
  "GROUNDED BY arm" while `inputs` said `arm` was "NEVER READ". With the copy gone
  and nothing derived in its place, the two disagreed the other way round: `inputs`
  said `arm` grounds `arm_length` and `why` said nothing did. Both derive it from
  the same extractions now, in both states.
* **S-43.** `gap`, which reads like a query, took the build lock and saved the
  whole ledger on every run, filing every gap it derived as a record nobody wrote.
* **`doctor` repairs nothing.** It is what runs when something is confusing, so it
  diagnoses and never writes: a doctor that migrated a legacy project, or rebuilt
  the index it is comparing with the records, would hide the problem from the next
  run. A record the strict reader refuses is a FAIL row, never an exit 2.
* **`last_check.json`'s `params`** stayed `{}` after 1.2 promised them: the one
  file an agent reads for "what does the model say, and what lost" beside the
  statuses had nothing in it.

Every command runs in a subprocess (`_env.nopekit`) on a temp copy of the bracket;
nothing here writes into the tracked tree.

Run:  PYTHONPATH=src python3 -m unittest tests.test_record_commands -v
"""
from __future__ import annotations

import json
import os
import re
import unittest

import _env
import _projects
import _transcript as T
import test_docs_commands as docs_check
from nopekit import cli, modelio, store
from nopekit.util import FileLock

#: The generated index. Ignored by git and rebuilt by every command but `doctor`
#: and `init`, so a read may rewrite it; it may never rewrite a record.
INDEX = ".nopekit/ledger.json"

#: A fixed stamp for the in-process migration that builds the migrated fixture:
#: it appears only in the migration's notice, never in a record.
WHEN = "2026-09-28T00:00:00Z"

#: The record directories `init` makes, as `store.RECORD_DIRS` names them.
RECORD_DIRS = ("claims", "params", "decisions", "needs", "inputs", "results", "views")

#: `why`'s GROUNDED BY section, and one artifact under it.
GROUNDED_HEAD = re.compile(r"^GROUNDED BY \((?P<count>\d+)\)$")
GROUNDED_ROW = re.compile(r"^  - (?P<id>\S+) +\[")


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _tree(root: str, *, skip: tuple[str, ...] = ()) -> tuple[dict[str, bytes], set[str]]:
    """``({posix path: bytes}, {posix directory})`` of everything under ``root``,
    ``__pycache__`` excepted (importing a model or a gate may write bytecode beside
    it, cli:H15 — that is Python's, never the command's), and ``skip`` paths."""
    files: dict[str, bytes] = {}
    dirs: set[str] = set()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        rel = os.path.relpath(dirpath, root).replace(os.sep, "/")
        dirs.add(rel)
        for name in filenames:
            path = name if rel == "." else f"{rel}/{name}"
            if path in skip:
                continue
            with open(os.path.join(dirpath, name), "rb") as fh:
                files[path] = fh.read()
    return files, dirs


def _changed(before: tuple[dict, set], after: tuple[dict, set]) -> set[str]:
    """Every file added, removed or rewritten, and every directory made or removed."""
    (fa, da), (fb, db) = before, after
    return ({p for p in set(fa) | set(fb) if fa.get(p) != fb.get(p)}
            | {d + "/" for d in da ^ db})


def _run(project: str, *argv: str):
    return _env.nopekit(list(argv), cwd=project)


def _json(proc) -> dict:
    try:
        return json.loads(proc.stdout)
    except ValueError as exc:                     # pragma: no cover - reported, not raised
        raise AssertionError(f"not one JSON document ({exc}):\n{proc.stdout}\n{proc.stderr}")


def _migrated(dest: str) -> str:
    """A copy of the bracket migrated to record files in-process — the layout every
    command meets after a project's first `check` or shim."""
    project = _projects.bracket_copy(dest)
    store.migrate_legacy(project, apply=True, when=WHEN,
                         model_prose=modelio.static_param_prose)
    return project


def _write(project: str, rel: str, text: str) -> str:
    path = os.path.join(project, *rel.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    return path


def _edit(project: str, rel: str, old: str, new: str) -> None:
    path = os.path.join(project, *rel.split("/"))
    with open(path, encoding="utf-8", newline="") as fh:
        text = fh.read()
    if text.count(old) != 1:
        raise AssertionError(f"{rel}: expected {old!r} exactly once")
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text.replace(old, new))


def _rows(proc) -> dict[str, dict]:
    """`doctor --json`'s rows by name, the first of each."""
    rows: dict[str, dict] = {}
    for row in _json(proc)["checks"]:
        rows.setdefault(row["check"], row)
    return rows


# --------------------------------------------------------------------------- #
# init: born migrated
# --------------------------------------------------------------------------- #
class InitIsBornMigrated(_env.EnvCase):
    """`init` writes the records layout and no `ledger.json`, so the first command
    after it finds nothing to migrate (phase-1.md 1.3, `init`'s V row)."""

    def _init(self, *extra: str) -> tuple[str, object]:
        root = os.path.join(self.tmp(), "fresh")
        proc = _env.nopekit(["init", "--name", "fresh", "-C", root, *extra], cwd=self.tmp())
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        return root, proc

    def test_init_then_status_prints_no_migration_notice(self):
        root, _proc = self._init()
        dot = os.path.join(root, ".nopekit")
        self.assertTrue(os.path.isfile(os.path.join(dot, "project.json")))
        self.assertFalse(os.path.exists(os.path.join(dot, "ledger.json")),
                         "init wrote a ledger.json: the project is born legacy")
        for kind in RECORD_DIRS:
            self.assertTrue(os.path.isdir(os.path.join(root, kind)), kind)
        for rel in (".gitignore", ".gitattributes", ".nopekit/.gitignore"):
            with open(os.path.join(root, *rel.split("/")), encoding="utf-8") as fh:
                self.assertIn("# nopekit:begin", fh.read(), rel)

        with open(os.path.join(dot, "project.json"), "rb") as fh:
            project = fh.read()
        proc = _run(root, "status")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertNotRegex(proc.stderr.lower(), r"migrat|git rm", proc.stderr)
        self.assertNotRegex(proc.stdout.lower(), r"migrat|git rm", proc.stdout)
        self.assertFalse(os.path.exists(os.path.join(dot, "ledger.legacy.json")),
                         "the first command after init migrated something")
        with open(os.path.join(dot, "project.json"), "rb") as fh:
            self.assertEqual(fh.read(), project)
        self.assertEqual(store.agree(root), [])

    def test_the_next_steps_name_only_commands_that_exist(self):
        """Every command `init` prints — text and `--json` — parses, and the step
        that recorded the model names the file that owns the entry now."""
        parser = cli.build_parser()
        for label, extra in (("text", ()), ("json", ("--json",))):
            with self.subTest(output=label):
                _root, proc = self._init(*extra)
                text = ("\n".join(_json(proc)["next"]) if extra else proc.stdout)
                commands = docs_check.string_commands(text, parser)
                self.assertTrue(commands, f"init printed no command:\n{text}")
                problems = [p for _off, words in commands
                            for p in docs_check.command_problems(words, parser)]
                self.assertEqual(problems, [], text)
                self.assertNotIn("--set-entry", text)
                self.assertIn("model_entry", text)
                self.assertIn(".nopekit/project.json", text)

    def test_json_names_the_project_file_not_a_ledger(self):
        root, proc = self._init("--json")
        data = _json(proc)
        self.assertEqual(data["project"], ".nopekit/project.json")
        self.assertTrue(os.path.isfile(os.path.join(root, *data["project"].split("/"))))
        self.assertNotIn("ledger", data, "init --json named a ledger it does not write")


# --------------------------------------------------------------------------- #
# doctor: diagnoses, never repairs
# --------------------------------------------------------------------------- #
class DoctorWritesNothing(_env.EnvCase):
    """A byte snapshot of the whole tree around `doctor`, on a legacy project and on
    a migrated one; `__pycache__` tolerated (cli:H15)."""

    def _doctor_twice(self, project: str) -> tuple[object, object]:
        before = _tree(project)
        text = _run(project, "doctor")
        data = _run(project, "doctor", "--json")
        self.assertEqual(_changed(before, _tree(project)), set(),
                         "doctor wrote into the project it was diagnosing")
        self.assertIn(text.returncode, (0, 1), text.stdout + text.stderr)
        self.assertIn(data.returncode, (0, 1), data.stdout + data.stderr)
        return text, data

    def test_on_a_legacy_project(self):
        project = _projects.bracket_copy(os.path.join(self.tmp(), "legacy"))
        self.assertTrue(os.path.isdir(os.path.join(project, ".nopekit", "runs")))
        text, data = self._doctor_twice(project)
        rows = _rows(data)
        self.assertEqual(rows["records"]["status"], "warn", rows["records"])
        self.assertIn("will migrate on next command", rows["records"]["detail"])
        self.assertIn("will migrate on next command", text.stdout)
        self.assertEqual(rows["run-history"]["status"], "warn")
        self.assertIn("`runs/`", rows["run-history"]["detail"])
        self.assertIn("nothing reads it", rows["run-history"]["detail"])
        self.assertFalse(os.path.exists(os.path.join(project, ".nopekit", "project.json")))

    def test_on_a_migrated_project_whose_index_is_behind(self):
        """A record edited by hand after the last command: the index is behind, and
        doctor says so — and leaves it behind (every other command rebuilds it)."""
        project = _migrated(os.path.join(self.tmp(), "migrated"))
        proc = _run(project, "status")                       # builds the index
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        _edit(project, "claims/C1.json", '"limit": 0.5', '"limit": 0.6')
        self.assertTrue(store.agree(project))
        _text, data = self._doctor_twice(project)
        rows = _rows(data)
        self.assertEqual(rows["records"]["status"], "ok", rows["records"])
        self.assertEqual(rows["index"]["status"], "warn", rows["index"])
        self.assertIn("claims.C1.acceptance.limit", rows["index"]["detail"])
        self.assertTrue(store.agree(project), "doctor rebuilt the index")
        # The migration leaves the run history where it was; doctor names it.
        self.assertEqual(rows["run-history"]["status"], "warn")

    def test_a_record_the_strict_reader_refuses_is_a_fail_row(self):
        """Every refused record is its own FAIL row naming its file — exit 1, never
        the exit 2 of a command that could not read the project."""
        project = _migrated(os.path.join(self.tmp(), "broken"))
        _write(project, "claims/C8.json", '{"statment": "a typo"}\n')
        _write(project, "decisions/d1.json", '{"title": "x", "title": "y"}\n')
        _text, data = self._doctor_twice(project)
        self.assertEqual(data.returncode, 1, data.stdout + data.stderr)
        fails = [row for row in _json(data)["checks"] if row["status"] == "FAIL"]
        details = "\n".join(row["detail"] for row in fails)
        self.assertIn("claims/C8.json", details)
        self.assertIn("statement", details, "the refusal names the suggestion")
        self.assertIn("decisions/d1.json", details)
        self.assertEqual({row["check"] for row in fails}, {"records"}, fails)
        self.assertNotIn("Traceback", data.stderr)


# --------------------------------------------------------------------------- #
# why: the model's number, or none
# --------------------------------------------------------------------------- #
class WhyReadsTheModel(_env.EnvCase):
    """S-39: `why` prints the model's value, read when it is shown."""

    def _why(self, project: str, name: str = "thickness"):
        return _run(project, "why", name)

    def test_the_model_value_with_no_check(self):
        for label, make in (("legacy", _projects.bracket_copy), ("migrated", _migrated)):
            with self.subTest(layout=label):
                project = make(os.path.join(self.tmp(), label))
                _projects.set_thickness(project, 8.0)
                proc = self._why(project)
                self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                self.assertIn("param thickness = 8.0 mm   (model/bracket.py Config.thickness)",
                              proc.stdout.splitlines(), proc.stdout)
                self.assertNotIn("7.0", proc.stdout.splitlines()[0])

    def test_a_record_carrying_a_value_is_refused_naming_the_model(self):
        project = _migrated(os.path.join(self.tmp(), "record"))
        _write(project, "params/thickness.json", '{"value": 9.0}\n')
        proc = self._why(project)
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertIn("params/thickness.json", proc.stderr)
        self.assertIn("model/bracket.py", proc.stderr)
        self.assertNotIn("9.0", proc.stdout)

    def test_a_model_that_raises_prints_no_number(self):
        for label, make in (("legacy", _projects.bracket_copy), ("migrated", _migrated)):
            with self.subTest(layout=label):
                project = make(os.path.join(self.tmp(), label))
                _edit(project, "model/bracket.py", "CONFIG = Config()",
                      'raise RuntimeError("the model is mid-edit")')
                proc = self._why(project)
                self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                lines = proc.stdout.splitlines()
                self.assertTrue(lines and re.fullmatch(r"param thickness(?:   \(\S+\))?",
                                                       lines[0]), proc.stdout)
                self.assertTrue(any(line.startswith("  model does not load: ")
                                    and "mid-edit" in line for line in lines), proc.stdout)
                self.assertFalse(any(T.WHY_PARAM.fullmatch(line) for line in lines),
                                 "a number where the model should answer")

    def test_the_transcript_lines(self):
        """The three lines phase-1.md's transcript prints for `why thickness`."""
        project = _migrated(os.path.join(self.tmp(), "transcript"))
        lines = self._why(project).stdout.splitlines()
        want = ["param thickness = 7.0 mm   (model/bracket.py Config.thickness)",
                "REJECTED (1)",
                "  4.0 mm — 3.75 mm deflection, 7.5x the limit   (model/bracket.py PARAMS)"]
        found = [line for line in lines if line in want]
        self.assertEqual(found, want, "\n".join(lines))


# --------------------------------------------------------------------------- #
# grounding: derived from the extractions, in both commands
# --------------------------------------------------------------------------- #
class GroundingIsDerived(_env.EnvCase):
    """S-36: `why` and `inputs` read grounding off the same extractions, so
    deleting one moves both — they agree before and after."""

    def setUp(self) -> None:
        self.project = _migrated(os.path.join(self.tmp(), "bracket"))
        evidence = _write(self.tmp(), "downloads/arm.txt", "arm measured at 60.2 mm\n")
        proc = _run(self.project, "ingest", evidence, "--desc", "caliper reading", "--json")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        (artifact,) = _json(proc)["ingested"]
        self.artifact = artifact["id"]
        self.record = f"inputs/{self.artifact}.json"

    def _why_grounds(self, name: str) -> list[str]:
        proc = _run(self.project, "why", name)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        lines = proc.stdout.splitlines()
        heads = [i for i, line in enumerate(lines) if GROUNDED_HEAD.fullmatch(line)]
        self.assertEqual(len(heads), 1, proc.stdout)
        found: list[str] = []
        for line in lines[heads[0] + 1:]:
            if not line.startswith("  "):
                break
            match = GROUNDED_ROW.match(line)
            if match:
                found.append(match.group("id"))
        count = int(GROUNDED_HEAD.fullmatch(lines[heads[0]]).group("count"))
        self.assertEqual(count, len(found), proc.stdout)
        return found

    def _inputs(self) -> tuple[dict, str]:
        data = _json(_run(self.project, "inputs", "--json"))
        text = _run(self.project, "inputs")
        self.assertEqual(text.returncode, 0, text.stdout + text.stderr)
        return data, text.stdout

    def _inputs_grounds(self, name: str) -> list[str]:
        data, _text = self._inputs()
        rows = [row["id"] for row in data["inputs"] if name in row["grounds"]]
        self.assertEqual(sorted(rows), sorted(data["grounding"].get(name, [])), data)
        return rows

    def test_why_and_inputs_agree_before_and_after_the_extraction_goes(self):
        proc = _run(self.project, "extract", self.artifact, "--what", "arm is 60.2 mm",
                    "--grounds", "arm_length", "--confidence", "measured")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(self._why_grounds("arm_length"), [self.artifact])
        self.assertEqual(self._inputs_grounds("arm_length"), [self.artifact])
        _data, text = self._inputs()
        line = next(line for line in text.splitlines() if self.artifact in line)
        self.assertNotIn("NEVER READ", line)
        self.assertIn("arm_length", line)

        # A hand edit of the one record: the extraction is gone.
        path = os.path.join(self.project, *self.record.split("/"))
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        del data["extractions"]
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(data, indent=2) + "\n")
        self.assertEqual(self._why_grounds("arm_length"), [])
        self.assertEqual(self._inputs_grounds("arm_length"), [])
        _data, text = self._inputs()
        line = next(line for line in text.splitlines() if self.artifact in line)
        self.assertIn("NEVER READ", line)

    def test_inputs_shows_the_bytes_as_the_index_does(self):
        """`inputs` carries each artifact's record, the digest of its bytes NOW,
        the pinned one, drift and existence — the index's own facts (S-45)."""
        def agree() -> dict:
            data, _text = self._inputs()
            row = next(r for r in data["inputs"] if r["id"] == self.artifact)
            built = next(r for r in store.build_index(self.project)["inputs"]
                         if r["id"] == self.artifact)
            self.assertEqual(row["record"], self.record)
            for key in ("sha256", "pinned", "drift", "exists"):
                self.assertEqual(row[key], built[key], key)
            return row

        row = agree()
        self.assertTrue(row["exists"])
        self.assertFalse(row["drift"])
        path = os.path.join(self.project, *row["path"].split("/"))
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("arm measured at 61.0 mm\n")
        row = agree()
        self.assertTrue(row["drift"])
        self.assertIn("DRIFT", self._inputs()[1])
        os.remove(path)
        row = agree()
        self.assertFalse(row["exists"])
        self.assertIn("MISSING", self._inputs()[1])


# --------------------------------------------------------------------------- #
# gap: a read
# --------------------------------------------------------------------------- #
class GapIsReadOnly(_env.EnvCase):
    """S-43: `gap` takes no lock and writes nothing — the whole tree on a legacy
    project; every record, with the build lock held by another process, on a
    migrated one (the ignored index is the one file a read may refresh)."""

    def test_gap_on_a_legacy_project_writes_nothing(self):
        project = _projects.bracket_copy(os.path.join(self.tmp(), "legacy"))
        before = _tree(project)
        for argv in (["gap"], ["gap", "--json"], ["gap", "--propose"]):
            with self.subTest(argv=argv):
                proc = _run(project, *argv)
                self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                self.assertEqual(_changed(before, _tree(project)), set(),
                                 f"`nopekit {' '.join(argv)}` wrote into the project")

    def test_gap_takes_no_lock_and_writes_no_record(self):
        project = _migrated(os.path.join(self.tmp(), "migrated"))
        lock = FileLock(os.path.join(project, ".nopekit", "build.lock")).acquire()
        self.addCleanup(lock.release)
        before = _tree(project, skip=(INDEX,))
        proc = _run(project, "gap", "--json")
        self.assertEqual(proc.returncode, 0,
                         "gap waited on the build lock a sweep holds:\n"
                         + proc.stdout + proc.stderr)
        data = _json(proc)
        self.assertEqual([need["claim_ids"] for need in data["gaps"]], [["C7"]])
        self.assertEqual(_changed(before, _tree(project, skip=(INDEX,))), set(),
                         "gap wrote into the project")
        self.assertEqual(os.listdir(os.path.join(project, "needs")), [],
                         "a gap gap derived became a record nobody wrote")


# --------------------------------------------------------------------------- #
# model: no --set-entry, no record
# --------------------------------------------------------------------------- #
class ModelHasNoSetEntry(_env.EnvCase):
    """A-8: `.nopekit/project.json` owns the model entry; `model` writes no record."""

    def test_the_flag_is_gone(self):
        project = _migrated(os.path.join(self.tmp(), "bracket"))
        proc = _run(project, "model", "--set-entry", "model/bracket.py")
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertIn("--set-entry", proc.stderr)
        model = docs_check._subcommands(cli.build_parser())["model"]
        self.assertNotIn("--set-entry", model._option_string_actions)

    def test_no_entry_names_the_file_to_edit(self):
        root = os.path.join(self.tmp(), "fresh")
        proc = _env.nopekit(["init", "-C", root], cwd=self.tmp())
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        proc = _run(root, "model")
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertIn('"model_entry"', proc.stderr)
        self.assertIn(".nopekit/project.json", proc.stderr)
        self.assertNotIn("--set-entry", proc.stderr)

    def test_model_writes_only_the_projection_it_names(self):
        project = _migrated(os.path.join(self.tmp(), "bracket"))
        before = _tree(project, skip=(INDEX,))
        proc = _run(project, "model")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(_changed(before, _tree(project, skip=(INDEX,))), set())
        proc = _run(project, "model", "--write")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(_changed(before, _tree(project, skip=(INDEX,))),
                         {".nopekit/model.json"})
        legacy = _projects.bracket_copy(os.path.join(self.tmp(), "legacy"))
        before = _tree(legacy)
        proc = _run(legacy, "model")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(_changed(before, _tree(legacy)), set(),
                         "`model` wrote into a legacy project it was only reading")

    def test_orphans_come_from_the_records(self):
        """A param record whose field the model no longer defines is named; a
        field the model owns entirely has no record and is no orphan."""
        project = _migrated(os.path.join(self.tmp(), "bracket"))
        _write(project, "params/span_mm.json", '{"source": "the old drawing"}\n')
        data = _json(_run(project, "model", "--json"))
        self.assertEqual(data["orphans"], ["span_mm"])
        text = _run(project, "model").stdout
        self.assertIn("span_mm", text)


# --------------------------------------------------------------------------- #
# last_check.json: the parameter view beside the statuses
# --------------------------------------------------------------------------- #
class LastCheckHoldsTheParamView(_env.EnvCase):
    """`check` fills `last_check.json`'s `params` from `modelio.param_view`: the
    model's value, where it lives, and what lost from both homes."""

    def test_params_are_the_view(self):
        project = _projects.bracket_copy(os.path.join(self.tmp(), "bracket"))
        proc = _run(project, "check")
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        with open(os.path.join(project, ".nopekit", "cache", "last_check.json"),
                  encoding="utf-8") as fh:
            params = json.load(fh)["params"]
        thickness = params["thickness"]
        self.assertEqual(thickness["value"], 7.0)
        self.assertEqual(thickness["home"], "model/bracket.py Config.thickness")
        self.assertEqual([(r["value"], r["origin"]) for r in thickness["rejected"]],
                         [("4.0 mm", "model/bracket.py PARAMS")])
        self.assertTrue(set(params) >= {"arm_length", "hole_d", "bed_xy"}, params)


if __name__ == "__main__":
    unittest.main()
