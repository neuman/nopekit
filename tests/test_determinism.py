# SPDX-License-Identifier: Apache-2.0
"""Two cold runs, two directories, the same bytes — and what that measurement licenses.

A verdict entry is a tracked file named by what its gate read (spec §3.7). Two
machines that check the same design must write the same bytes, or a merge of two
branches that both ran `check` is an add/add conflict on a file nobody should
ever hand-edit (D-29), and "two outcomes for identical inputs" stops being a
signal and becomes noise. What slipped through before this was measured:

* **S-34.** openmodelica wrote wall-clock time into `Verdict.detail`, so identical
  inputs gave different bytes: 12 false misses in the E4 probe. Fixed at the pack
  (U15); this is the property that says it stays fixed, for every bundled gate.
* **Absolute paths.** omc's error text embeds the absolute `.mo` path, 33 of 54
  gates cited absolute evidence, and fixtures set absolute mesh paths (packs:H5,
  H6, H9). An entry written from `/tmp/a/...` and one from `/tmp/b/...` differed
  in nothing but where the project happened to live.
* **Import order.** Instruments derived from import audit events depended on
  which gate imported numpy first (packs:H3).

**EntriesAreDeterministic** runs every bundled pack's baseline, wrapped as a
project (`tests/_projects.py`), and the bracket, each in TWO temp directories
with TWO cold processes per directory — a fresh `python -m atompipe check` on a
pristine copy each time — and demands byte-identical entry and control-entry
files across all four runs. A gate whose tool is missing here must read the
availability skip and write nothing: the CI runner has neither trimesh nor omc,
and there the asserted outcome is the skip, never a skipped test.

**TwoOutcomes** is what the measurement licenses (R-4): once no bundled gate
writes two outcomes for one rho, two outcomes under the same instruments can be
an ERROR — `verdicts.TWO_OUTCOMES_IS_ERROR`, flipped in the same unit that made
this file green. Its negative half: the same two outcomes under DIFFERENT
instruments (another numpy, merged from another machine) are not an error; the
entry recorded under this machine's instruments wins.

Run:  PYTHONPATH=src python3 -m unittest tests.test_determinism -v
"""
from __future__ import annotations

import dataclasses
import difflib
import json
import os
import shutil

from atompipe import gates, verdicts
from atompipe import report as report_mod

import _env
import _projects

#: The bracket's six gates, spelled here (see test_admission.BRACKET_GATES).
BRACKET_GATES = ("bracket.deflection", "bracket.bending_stress", "bracket.bearing",
                 "bracket.model_validity", "bracket.bed_fit", "bracket.min_wall")

#: What a cold run starts without. The whole project is restored from a pristine
#: copy before each run, so nothing a previous run wrote — its cache, its
#: remembered outcomes, the ledger it rewrote, bytecode beside the model — is
#: there to be read. *Rejected:* deleting only `.atompipe/verdicts/`: the first
#: run of the 1.2 spine rewrites `ledger.json`, and a second run on the rewritten
#: ledger would compare a different input, not a second run of the same one.
_PRISTINE = ".pristine"


def _bundled_packs() -> list[str]:
    return sorted(name for name in os.listdir(_projects.PACKS)
                  if os.path.isfile(os.path.join(_projects.PACKS, name, "pack.json")))


def _cache_bytes(project: str) -> dict[str, bytes]:
    """``{<gate>/<file>: bytes}`` of every file under the project's verdict cache."""
    base = os.path.join(project, ".atompipe", "verdicts")
    out: dict[str, bytes] = {}
    for dirpath, _dirs, files in os.walk(base):
        for name in files:
            path = os.path.join(dirpath, name)
            with open(path, "rb") as fh:
                out[os.path.relpath(path, base).replace(os.sep, "/")] = fh.read()
    return out


def _first_difference(a: bytes, b: bytes) -> str:
    """The first differing lines of two entries, for a failure message that says
    WHICH field moved (a timestamp, a path) instead of only that one did."""
    diff = difflib.unified_diff(a.decode("utf-8", "replace").splitlines(),
                                b.decode("utf-8", "replace").splitlines(), lineterm="", n=0)
    return "\n".join(list(diff)[:12])


class _Corpus:
    """Each project of the corpus: a name, its gates, and a builder into a dir."""

    @staticmethod
    def projects() -> list[tuple[str, list, object]]:
        corpus = [("bracket", None,
                   lambda dest: _projects.bracket_copy(dest))]
        for pack in _bundled_packs():
            corpus.append((pack, _projects.pack_gates(pack),
                           lambda dest, pack=pack: _projects.wrap_pack_baseline(pack, dest)))
        return corpus


# --------------------------------------------------------------------------- #
class EntriesAreDeterministic(_env.EnvCase):
    """Every bundled gate: two directories, two cold processes each, one set of bytes."""

    def _cold_check(self, project: str) -> tuple[int, dict]:
        """Restore ``project`` from its pristine copy, then one fresh ``check``."""
        shutil.rmtree(project)
        shutil.copytree(project + _PRISTINE, project, symlinks=True)
        proc = _env.atompipe(["check", "--tier", "3", "--json"], cwd=project)
        self.assertIn(proc.returncode, (0, 1), f"check crashed in {project}:\n"
                                               f"{proc.stdout}\n{proc.stderr}")
        return proc.returncode, json.loads(proc.stdout)

    def test_two_directories_two_cold_processes_the_same_bytes(self):
        written = {"entries": 0, "controls": 0}
        for name, specs, build in _Corpus.projects():
            with self.subTest(project=name):
                runs = []
                # Two directories that differ in more than a random suffix: a
                # different depth, so a path that leaked into an entry changes its
                # length, not only its letters.
                for where in (os.path.join(self.tmp(), name),
                              os.path.join(self.tmp(), "another", "place", name)):
                    build(where + _PRISTINE)
                    os.makedirs(where)
                    for attempt in (1, 2):
                        code, data = self._cold_check(where)
                        runs.append((f"{where} run {attempt}", code, data,
                                     _cache_bytes(where)))

                label, code, data, first = runs[0]
                self.assertTrue(first, f"{name}: the first run wrote no entry at all")
                for other, other_code, other_data, got in runs[1:]:
                    self.assertEqual(other_code, code, f"{name}: exit {code} in {label}, "
                                                       f"{other_code} in {other}")
                    self.assertEqual(sorted(got), sorted(first),
                                     f"{name}: {other} wrote other entry names than {label}")
                    for path in sorted(first):
                        self.assertEqual(got[path], first[path],
                                         f"{name}: {path} differs between {label} and "
                                         f"{other}:\n"
                                         f"{_first_difference(first[path], got[path])}")

                # Every gate accounted for: one entry and one control entry where
                # it can run; the availability skip, and nothing written, where not.
                rows = {row["gate"]: row for row in data["verdicts"]}
                ids = BRACKET_GATES if specs is None else [spec.id for spec in specs]
                self.assertEqual(sorted(rows), sorted(ids), f"{name}: rows")
                for spec_id in ids:
                    spec = None if specs is None else next(s for s in specs if s.id == spec_id)
                    ok, why = (True, "") if spec is None else gates.availability(spec)
                    mine = sorted(p.split("/", 1)[1] for p in first
                                  if p.split("/", 1)[0] == spec_id)
                    row = rows[spec_id]
                    if ok:
                        controls = [n for n in mine if n.startswith("control-")]
                        entries = [n for n in mine if not n.startswith("control-")]
                        self.assertEqual((len(entries), len(controls)), (1, 1),
                                         f"{spec_id}: {mine} ({row})")
                        self.assertIn(row["outcome"], ("pass", "fail"), row)
                        written["entries"] += 1
                        written["controls"] += 1
                    else:
                        self.assertEqual((row["outcome"], row.get("skip_reason")),
                                         ("skipped", why), row)
                        self.assertEqual(mine, [], f"{spec_id} cannot run here and wrote "
                                                   f"{mine}")
        self.assertGreater(written["entries"], 0)


# --------------------------------------------------------------------------- #
class TwoOutcomes(_env.EnvCase):
    """Two outcomes for one rho: an error under equal instruments, never under
    different ones. Planted on a checked bracket copy; read through the CLI."""

    def _checked(self) -> str:
        project = _projects.bracket_copy(os.path.join(self.tmp(), "bracket"))
        proc = _env.atompipe(["check"], cwd=project)
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        return project

    def _plant(self, project: str, gate_id: str, *, instruments=None) -> None:
        """A second entry for ``gate_id`` at the same rho, with the other answer —
        what a nondeterministic gate, or a merged hand edit, leaves behind."""
        [entry] = verdicts.read_entries(project, gate_id)
        other = dataclasses.replace(
            entry, verdict={**entry.verdict, "passed": not entry.verdict["passed"]},
            instruments=entry.instruments if instruments is None else instruments)
        result = verdicts.write_entry(project, other)
        self.assertEqual(result.status, "written", result)
        self.assertEqual(len(verdicts.read_entries(project, gate_id)), 2)

    def _doctor(self, project: str) -> tuple[int, dict]:
        proc = _env.atompipe(["doctor", "--json"], cwd=project)
        rows = {row["check"]: row for row in json.loads(proc.stdout)["checks"]}
        return proc.returncode, rows

    def _status(self, project: str) -> dict:
        proc = _env.atompipe(["status", "--json"], cwd=project)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        return json.loads(proc.stdout)

    def _proven(self, project: str) -> str:
        proc = _env.atompipe(["report"], cwd=project)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        text = proc.stdout
        heading = report_mod.SECTION_PROVEN
        self.assertEqual(text.count("\n" + heading), 1, text)
        return text.split("\n" + heading, 1)[1].split("\n## ", 1)[0]

    def test_equal_instruments_is_an_error(self):
        self.assertIs(verdicts.TWO_OUTCOMES_IS_ERROR, True,
                      "staged until EntriesAreDeterministic measured the corpus (R-4)")
        project = self._checked()
        self.assertEqual(self._status(project)["claims"]["C4"], "pass",
                         "the positive control: bed_fit and min_wall pass C4")
        self._plant(project, "bracket.bed_fit")

        code, rows = self._doctor(project)
        self.assertEqual(code, 1, rows)
        row = rows["two-outcomes"]
        self.assertEqual(row["status"], "FAIL", row)
        self.assertIn("bracket.bed_fit", row["detail"])
        self.assertIn("two outcomes recorded for identical inputs", row["detail"])

        status = self._status(project)
        self.assertEqual(status["claims"]["C4"], "fail", status["freshness"]["bracket.bed_fit"])
        self.assertNotIn("**C4**", self._proven(project))

        # A re-run agrees with one of the two and settles nothing: the entry it
        # writes already exists, both files stay, and the claim still FAILs.
        proc = _env.atompipe(["check"], cwd=project)
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertEqual(len(verdicts.read_entries(project, "bracket.bed_fit")), 2)
        self.assertEqual(self._status(project)["claims"]["C4"], "fail")

    def test_different_instruments_is_not_an_error(self):
        # The negative half: an entry merged from a machine with another numpy
        # is provenance, not a contradiction (Q1.3). A detector that flagged
        # this would turn every cross-machine merge red.
        project = self._checked()
        self._plant(project, "bracket.bed_fit", instruments={"numpy": "0.0.0-elsewhere"})
        code, rows = self._doctor(project)
        self.assertNotEqual(rows["two-outcomes"]["status"], "FAIL", rows["two-outcomes"])
        self.assertEqual(code, 0, rows)
        self.assertEqual(self._status(project)["claims"]["C4"], "pass",
                         "the entry recorded under this machine's instruments wins")
