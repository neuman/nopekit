# SPDX-License-Identifier: Apache-2.0
"""`status` names the check an edit affected, runs nothing, and keeps no run history.

What slipped through, each seen on the CLI before checkpoint 1.2's staleness was
per gate:

* **M11.7 / M11.11.** `status` said "model <hash> -> <hash>": THAT something
  moved, never which check it touched. It now names the gate and the input —
  `stale: bracket.bed_fit — config.bed_xy 220.0 -> 250.0` — and it does so by
  reading the verdict cache, never by running a gate or a fixture: a `status`
  that re-ran what it reported on would be a `check` with no lock and no record.
* **S-21.** A model that failed to import disabled staleness: the global hash of
  a projection that did not exist compared equal, and `status` listed three
  PROVEN claims for a design that could not be built.
* **S-33.** Ingesting one unrelated artifact made every measurable claim STALE,
  through one hash over every ingested input. An input no gate read is no gate's
  input.
* **The last check has an age.** A fact on screen carries its source and its
  age; `last check: <when> (<age> ago)` is the one line that says how old the
  cache's summary is, and `never` before the first.

`RunHistoryIsGone` holds the run history's removal to what it was for:

* **S-31.** `.atompipe/runs/` mixed `<gate>#selftest` rows into the same series
  as the sweep's rows, and no latency reader filtered them — a median over both
  is the cost of neither. What a run cost lives in obs now, split by kind, and
  nothing reads `runs/`: a corrupt file there is nobody's problem.
* **S-89 (runs half).** Every `check` and every `gate selftest` appended a
  tracked run file. `models.RunMeta`, `Ledger.last_run` and the store's run API
  are gone; an old ledger that carries `last_run` still loads (R-2).
* **S-76 (the early ignore lines).** 1.2 writes `.atompipe/cache/` and
  `.atompipe/obs/` and nothing ignored them, so the first `check` dirtied
  `git status` (cli:H2). `init` writes both lines now, and the bracket's tracked
  ignore file APPENDS them after the 1e09113 template — append-only, because the
  1.3 migration recognises that template as a prefix and replaces it.

Everything runs on a copy of the bracket, through `_env.atompipe` subprocesses.

Run:  PYTHONPATH=src python3 -m unittest tests.test_status_stale -v
"""
from __future__ import annotations

import dataclasses
import json
import os
import re
import shutil
import tempfile
import unittest

import _env
import _projects
import _transcript
import atompipe
from atompipe import models, store, verdicts
from atompipe.models import Ledger, ProjectMeta

#: The bracket's six gates, in registration order.
BRACKET_GATES = ("bracket.deflection", "bracket.bending_stress", "bracket.bearing",
                 "bracket.model_validity", "bracket.bed_fit", "bracket.min_wall")

#: The transcript's edit (docs/plan/phase-1.md): one input only `bed_fit` reads.
BED_XY_OLD = "bed_xy: float = 220.0"
BED_XY_NEW = "bed_xy: float = 250.0"

#: The bracket's ignore file as it was tracked through checkpoint 1.2: the
#: 1e09113 template, then the two 1.2 lines. From 1.3 the tracked file is the
#: migration's marked block (pinned in test_bracket_cache), and these bytes live
#: on as the legacy bracket every `_projects.bracket_copy` starts from.
BRACKET_GITIGNORE = os.path.join(_projects.LEGACY_BRACKET, "gitignore")


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _run(project: str, *argv: str):
    return _env.atompipe(list(argv), cwd=project)


def _ok(case: unittest.TestCase, proc, *codes: int) -> None:
    case.assertIn(proc.returncode, codes or (0,),
                  f"exit {proc.returncode}\n{proc.stdout}\n{proc.stderr}")


def _json(proc) -> dict:
    try:
        return json.loads(proc.stdout)
    except ValueError as exc:                        # pragma: no cover - reported
        raise AssertionError(f"not one JSON document ({exc}):\n{proc.stdout}\n{proc.stderr}")


def _edit(project: str, rel: str, old: str, new: str) -> None:
    """Replace exactly one ``old`` with ``new`` in ``rel``."""
    path = os.path.join(project, *rel.split("/"))
    with open(path, "r", encoding="utf-8", newline="") as fh:
        text = fh.read()
    if text.count(old) != 1:
        raise AssertionError(f"{rel}: expected exactly one {old!r}, found {text.count(old)}")
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text.replace(old, new))


def _write(project: str, rel: str, text: str) -> str:
    path = os.path.join(project, *rel.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


def _lines(proc) -> list[str]:
    return proc.stdout.splitlines()


def _matching(pattern: re.Pattern, proc) -> list[re.Match]:
    return [m for line in _lines(proc) if (m := pattern.fullmatch(line))]


class _Checked:
    """One bracket copy that has run `check` once, shared by a class and copied
    per test: a copy carries its cache, so a test starts from "everything is
    current" without paying for a first sweep of its own."""

    def __init__(self, prefix: str) -> None:
        self.base = tempfile.mkdtemp(prefix=prefix)
        self.project = _projects.bracket_copy(os.path.join(self.base, "bracket"))
        self.first = _run(self.project, "check")

    def copy(self, case: _env.EnvCase) -> str:
        dest = os.path.join(case.tmp(), "bracket")
        shutil.copytree(self.project, dest)
        return dest

    def close(self) -> None:
        _env._rmtree(self.base)


# --------------------------------------------------------------------------- #
# StatusNamesTheCheck
# --------------------------------------------------------------------------- #
#: A project gate and its fixture that each append to a sentinel file OUTSIDE the
#: project when they run (a write is not a read, so the gate stays cacheable).
#: `status` must leave both files absent (M11.11: it reads the cache, and runs no
#: gate and no fixture — not even a fixture whose code moved).
_SENTINEL_GATE = '''\
# SPDX-License-Identifier: Apache-2.0
"""Planted by tests/test_status_stale.py: a gate that says when it ran."""
from atompipe.gates import gate
from atompipe.models import NegativeControl, Tier, Verdict

SENTINEL = {sentinel!r}


@gate(id="bracket.sentinel", title="writes a sentinel whenever it runs",
      claims=["sentinel"], tier=Tier.INSTANT,
      negative_control=NegativeControl(fixture="selftest/sentinel.py:loud",
                                       note="the deflection pushed far past the limit"))
def sentinel(ctx):
    """Passes while the tip deflection stays under 5 mm; says that it ran."""
    with open(SENTINEL, "a", encoding="utf-8") as fh:
        fh.write("gate ran\\n")
    value = float(ctx.params["deflection"])
    return Verdict(gate="bracket.sentinel", passed=value < 5.0, measured=value,
                   limit=5.0, units="mm")
'''

_SENTINEL_FIXTURE = '''\
# SPDX-License-Identifier: Apache-2.0
"""Planted by tests/test_status_stale.py: a fixture that says when it ran."""
import dataclasses

SENTINEL = {sentinel!r}


def loud(ctx):
    """The known-good design with its deflection pushed past the sentinel's limit."""
    with open(SENTINEL, "a", encoding="utf-8") as fh:
        fh.write("fixture ran\\n")
    params = dict(ctx.params)
    params["deflection"] = 1000.0
    return dataclasses.replace(ctx, params=params)
'''


class StatusNamesTheCheck(_env.EnvCase):
    """`status` names the gate and the input an edit moved, never runs a gate to
    say so, and never reads PASS where a verdict cannot be current."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.checked = _Checked("atompipe-status-stale-")
        cls.addClassCleanup(cls.checked.close)

    def test_the_first_check_left_a_current_cache(self):
        """The shared starting point: six gates, one deliberate FAIL."""
        self.assertEqual(self.checked.first.returncode, 1,
                         self.checked.first.stdout + self.checked.first.stderr)
        proc = _run(self.checked.project, "status")
        _ok(self, proc)
        found = _matching(_transcript.STALE_NONE, proc)
        self.assertEqual([m.group("current") for m in found], ["6"], proc.stdout)

    def test_stale_line_names_gate_and_input(self):
        project = self.checked.copy(self)
        _edit(project, "model/bracket.py", BED_XY_OLD, BED_XY_NEW)
        proc = _run(project, "status")
        _ok(self, proc)
        stale = [line for line in _lines(proc) if line.startswith("invalidated:")]
        self.assertEqual(len(stale), 1, proc.stdout)
        match = _transcript.STALE_LINE.fullmatch(stale[0])
        self.assertIsNotNone(match, stale[0])
        self.assertEqual(match.group("gate"), "bracket.bed_fit")
        self.assertEqual(match.group("reasons"), "config.bed_xy 220.0 -> 250.0")
        self.assertEqual(match.group("current"), "5", "the other five checks are current")
        self.assertFalse(_matching(_transcript.STALE_MORE, proc),
                         "an input only bed_fit reads staled another gate")

        data = _json(_run(project, "status", "--json"))
        self.assertEqual(data["stale_gates"], ["bracket.bed_fit"])
        self.assertTrue(data["stale"])
        self.assertEqual(data["freshness"]["bracket.bed_fit"]["reasons"],
                         ["config.bed_xy 220.0 -> 250.0"])

    def test_status_never_runs_a_gate(self):
        project = self.checked.copy(self)
        outside = self.tmp()
        gate_ran = os.path.join(outside, "gate-ran")
        fixture_ran = os.path.join(outside, "fixture-ran")
        _write(project, "gates/sentinel.py", _SENTINEL_GATE.format(sentinel=gate_ran))
        _write(project, "selftest/sentinel.py", _SENTINEL_FIXTURE.format(sentinel=fixture_ran))

        # Positive control first: `check` runs the new gate and its control, so
        # the sentinels do fire when anything runs them.
        _ok(self, _run(project, "check"), 1)
        self.assertTrue(os.path.isfile(gate_ran), "the planted gate never ran under check")
        self.assertTrue(os.path.isfile(fixture_ran), "the planted fixture never ran under check")
        os.remove(gate_ran)
        os.remove(fixture_ran)

        # A current cache, then one where the model moved: bed_fit is stale and
        # five controls wait on re-verification — exactly where a `status` that
        # re-verified would reach for a fixture.
        for label, edit in (("current", None), ("model moved", (BED_XY_OLD, BED_XY_NEW))):
            if edit is not None:
                _edit(project, "model/bracket.py", *edit)
            for argv in (["status"], ["status", "--json"]):
                with self.subTest(state=label, argv=argv):
                    _ok(self, _run(project, *argv))
                    self.assertFalse(os.path.exists(gate_ran), f"{argv} ran a gate ({label})")
                    self.assertFalse(os.path.exists(fixture_ran),
                                     f"{argv} ran a fixture ({label})")

    def test_s21_a_model_that_does_not_load_reads_no_pass(self):
        project = self.checked.copy(self)
        with open(os.path.join(project, "model", "bracket.py"), "a", encoding="utf-8") as fh:
            fh.write('\n\nraise RuntimeError("the model is mid-edit")\n')

        proc = _run(project, "status")
        _ok(self, proc)
        broken = _matching(_transcript.MODEL_BROKEN, proc)
        self.assertEqual(len(broken), 1, proc.stdout)
        self.assertEqual(broken[0].group("entry"), "model/bracket.py")
        self.assertFalse([line for line in _lines(proc) if line.startswith("[PASS")],
                         "S-21: a claim read PASS against a model that does not load")
        self.assertFalse(_matching(_transcript.STALE_NONE, proc),
                         "S-21: staleness said nothing moved while the model was broken")

        data = _json(_run(project, "status", "--json"))
        self.assertFalse(data["model"]["loaded"])
        self.assertEqual(sorted(data["stale_gates"]), sorted(BRACKET_GATES))
        self.assertNotIn("pass", set(data["claims"].values()), data["claims"])
        for cid in ("C2", "C3", "C4"):
            self.assertEqual(data["claims"][cid], "stale", (cid, data["claims"]))
        self.assertEqual(data["claims"]["C1"], "fail", "a refutation keeps its power (R-3)")

    def test_s33_ingesting_an_unrelated_file_stales_nothing(self):
        project = self.checked.copy(self)
        notes = os.path.join(self.tmp(), "shelf-notes.txt")
        with open(notes, "w", encoding="utf-8") as fh:
            fh.write("the shelf is painted pine; nobody measured anything\n")
        _ok(self, _run(project, "ingest", notes, "--desc", "unrelated notes"))

        proc = _run(project, "status")
        _ok(self, proc)
        found = _matching(_transcript.STALE_NONE, proc)
        self.assertEqual([m.group("current") for m in found], ["6"],
                         "S-33: an input no gate read staled a gate:\n" + proc.stdout)

        data = _json(_run(project, "status", "--json"))
        self.assertEqual(data["stale_gates"], [])
        self.assertEqual(data["inputs"]["total"], 1, "the ingest did register the file")
        for cid in ("C2", "C3", "C4"):
            self.assertEqual(data["claims"][cid], "pass", (cid, data["claims"]))

    def test_last_check_line_has_an_age(self):
        fresh = _projects.bracket_copy(os.path.join(self.tmp(), "fresh"))
        before = _run(fresh, "status")
        _ok(self, before)
        never = [m for m in _matching(_transcript.LAST_CHECK, before)]
        self.assertEqual(len(never), 1, before.stdout)
        self.assertIsNone(never[0].group("when"), "a copy that never ran check has no age")
        self.assertIn("last check run: never", before.stdout.splitlines())
        self.assertIsNone(_json(_run(fresh, "status", "--json"))["last_check"]["when"])

        project = self.checked.copy(self)
        proc = _run(project, "status")
        _ok(self, proc)
        found = _matching(_transcript.LAST_CHECK, proc)
        self.assertEqual(len(found), 1, proc.stdout)
        self.assertIsNotNone(found[0].group("when"), found[0].group(0))
        self.assertTrue(found[0].group("age"), "the last check is shown without its age")

        with open(os.path.join(project, ".atompipe", "cache", "last_check.json"),
                  encoding="utf-8") as fh:
            recorded = json.load(fh)["when"]
        self.assertEqual(found[0].group("when"), recorded, "the line is last_check.json's")
        data = _json(_run(project, "status", "--json"))
        self.assertEqual(data["last_check"]["when"], recorded)
        self.assertIsInstance(data["last_check"]["age_s"], (int, float))
        self.assertGreaterEqual(data["last_check"]["age_s"], 0)


# --------------------------------------------------------------------------- #
# RunHistoryIsGone
# --------------------------------------------------------------------------- #
def _history_series(project: str) -> dict[str, list[str]]:
    """``{gate: ["sweep" | "selftest", ...]}`` as a latency reader of
    `.atompipe/runs/` saw it: each run file's rows, keyed by the gate they time."""
    series: dict[str, list[str]] = {}
    directory = os.path.join(project, ".atompipe", "runs")
    for name in sorted(os.listdir(directory)):
        with open(os.path.join(directory, name), encoding="utf-8") as fh:
            record = json.load(fh)
        for row in record.get("verdicts") or []:
            gate, _hash, kind = str(row.get("gate", "")).partition("#")
            series.setdefault(gate, []).append("selftest" if kind == "selftest" else "sweep")
    return series


def _patterns(text: str) -> list[str]:
    """The pattern lines of an ignore file: no comments, no blanks."""
    return [line.strip() for line in text.splitlines()
            if line.strip() and not line.lstrip().startswith("#")]


def _ignored(repo: str, rel: str) -> bool:
    """Whether git ignores ``rel`` in ``repo`` (`check-ignore` exits 0 when it does)."""
    proc = _env.git(["check-ignore", "-q", "--", rel], cwd=repo)
    if proc.returncode not in (0, 1):
        raise AssertionError(f"git check-ignore {rel} in {repo}: {proc.stderr.strip()}")
    return proc.returncode == 0


class RunHistoryIsGone(_env.EnvCase):
    """No run history is written or read; what a run cost lives in obs."""

    def test_s31_no_reader_mixes_selftest_rows_into_a_latency_series(self):
        project = _projects.bracket_copy(os.path.join(self.tmp(), "bracket"))

        # The evidence S-31 was read from ships with the bracket: its history
        # times `<gate>#selftest` in the same series as the sweep's `<gate>`.
        mixed = {gate: kinds for gate, kinds in _history_series(project).items()
                 if {"sweep", "selftest"} <= set(kinds)}
        self.assertTrue(mixed, "the bracket's runs/ no longer holds the mixed series S-31 "
                               "was read from; this test's premise is gone")

        # What a run costs is in obs, gate runs and control runs apart.
        _ok(self, _run(project, "check"), 1)
        _ok(self, _run(project, "gate", "selftest"))
        for gate in BRACKET_GATES:
            with self.subTest(gate=gate):
                runs = verdicts.read_obs(project, gate)
                controls = verdicts.read_obs(project, gate, control=True)
                self.assertTrue(runs and controls, (runs, controls))
                self.assertEqual([r["entry"] for r in runs
                                  if r["entry"].startswith("control-")], [],
                                 "a control run in the gate's latency series")
                self.assertEqual([r["entry"] for r in controls
                                  if not r["entry"].startswith("control-")], [],
                                 "a gate run in the control's latency series")

        # Nothing reads the history any more: a corrupt run file is no command's
        # problem, and doctor — the one reader it had — does not mention it.
        corrupt = _write(project, ".atompipe/runs/9999-deadbeef.json", "{ not json")
        proc = _run(project, "doctor", "--json")
        rows = [row for row in _json(proc)["checks"]
                if ".atompipe/runs" in row["detail"] or "9999-deadbeef" in row["detail"]]
        self.assertEqual(rows, [], "doctor still reads the run history")
        self.assertTrue(os.path.isfile(corrupt))

        # And the spine exposes no reader or writer of it.
        for name in ("load_runs", "record_run", "runs_dir", "RUNS_NAME"):
            self.assertFalse(hasattr(store, name), f"store.{name} still exists")

    def test_the_run_record_is_gone_and_an_old_ledger_still_loads(self):
        self.assertFalse(hasattr(models, "RunMeta"), "models.RunMeta still exists")
        self.assertNotIn("RunMeta", models.__all__)
        self.assertNotIn("RunMeta", atompipe.__all__)
        self.assertNotIn("last_run", {f.name for f in dataclasses.fields(Ledger)})

        # R-2: a ledger an older spine wrote still loads, and the key is not
        # carried into the next save.
        legacy = {"meta": {"name": "old"}, "claims": [], "verdicts": [],
                  "last_run": {"when": "2026-09-11T23:31:00Z", "tier": 0,
                               "model_hash": "321107b55616", "inputs_hash": "e3b0c44298fc",
                               "spine_version": "0.1.0", "duration_s": 0.1}}
        ledger = Ledger.from_dict(legacy)
        self.assertEqual(ledger.meta.name, "old")
        self.assertNotIn("last_run", ledger.to_dict())

    def test_init_makes_no_run_history_and_ignores_the_checkouts_memory(self):
        root = os.path.join(self.tmp(), "project")
        store.init(root, ProjectMeta(name="p", created="2026-09-27T00:00:00Z"))
        self.assertFalse(os.path.exists(os.path.join(root, ".atompipe", "runs")),
                         "init made a run-history directory")
        self.assertNotIn("runs", store.project_paths(root))

        with open(os.path.join(root, ".atompipe", ".gitignore"), encoding="utf-8") as fh:
            patterns = _patterns(fh.read())
        for line in ("out/", "cache/", "obs/"):
            self.assertIn(line, patterns)
        self.assertNotIn("!runs/", patterns)
        self.assertNotIn("!ledger.json", patterns)

        # git agrees: the checkout's memory is ignored; the project and the
        # verdict cache are not. From checkpoint 1.3 the project is its records
        # and `.atompipe/project.json`, and `.atompipe/ledger.json` is their
        # GENERATED index (D-06) — an output, ignored like the rest, with the
        # ledger a legacy project migrated from (U26 moved it across).
        _ok(self, _env.git(["-c", "init.defaultBranch=main", "init", "-q"], cwd=root))
        for rel in (".atompipe/cache/last_check.json", ".atompipe/obs/g.json",
                    ".atompipe/out/mesh.stl", ".atompipe/ledger.json",
                    ".atompipe/ledger.legacy.json", "claims/C1.json",
                    ".atompipe/verdicts/g/0123456789abcdef-01234567.json"):
            _write(root, rel, "{}\n")
        for rel in (".atompipe/cache/last_check.json", ".atompipe/obs/g.json",
                    ".atompipe/out/mesh.stl", ".atompipe/ledger.json",
                    ".atompipe/ledger.legacy.json"):
            self.assertTrue(_ignored(root, rel), f"{rel} is not ignored")
        for rel in (".atompipe/project.json", "claims/C1.json",
                    ".atompipe/verdicts/g/0123456789abcdef-01234567.json"):
            self.assertFalse(_ignored(root, rel), f"{rel} is ignored")

    def test_the_bracket_appends_cache_and_obs_to_its_template(self):
        with open(BRACKET_GITIGNORE, encoding="utf-8", newline="") as fh:
            text = fh.read()
        self.assertTrue(text.startswith(_projects.LEGACY_GITIGNORE),
                        "the bracket's ignore file no longer begins with the 1e09113 "
                        "template, which the 1.3 migration recognises as a prefix")
        tail = text[len(_projects.LEGACY_GITIGNORE):]
        self.assertEqual(_patterns(tail), ["cache/", "obs/"])
        self.assertIn("cli:H2", tail, "the appended lines do not say why they are there")

        # In a clone, after a check: nothing under cache/ or obs/ shows.
        project = _projects.bracket_copy(os.path.join(self.tmp(), "bracket"), git=True)
        _ok(self, _run(project, "check"), 1)
        for rel in (".atompipe/cache/last_check.json", ".atompipe/obs"):
            self.assertTrue(os.path.exists(os.path.join(project, *rel.split("/"))),
                            f"check wrote no {rel}: this test would pass vacuously")
        porcelain = _env.git(["status", "--porcelain", "--untracked-files=all"], cwd=project)
        _ok(self, porcelain)
        shown = [line for line in porcelain.stdout.splitlines()
                 if ".atompipe/cache/" in line or ".atompipe/obs/" in line]
        self.assertEqual(shown, [], "S-76: check dirtied git status with the checkout's "
                                    "memory:\n" + porcelain.stdout)


if __name__ == "__main__":
    unittest.main()
