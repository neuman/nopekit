# SPDX-License-Identifier: Apache-2.0
"""hand-ergonomics (general-packs E1): ``ergo.digit_reach`` and ``ergo.reach_envelope``.

The pack counts ANSUR 1988 subjects, one by one, whose thumb or index finger reaches
every control pressed from a grip. Each class holds one thing the design promised
and the place it would slip:

* **The table is the survey** (``TheTableIsTheSurvey``): 2,208 women and 1,774 men,
  its sha256 is the provenance's, its means are TR89-044's, an edited table is
  refused, and the derivation refuses a file that is not the public one.
* **The gate counts subjects** (``TheGateCountsSubjects``): the design's counts
  reproduce (97.96% and 99.71% on the baseline, 63.08% and 84.38% on the known-bad
  cluster, 83.92% of women with a 50 mm handle; the design read 99.72%, 63.09% and
  84.39%, the same counts rounded half-up), the percentile pair (48 mm for women) is
  not the subjects' own P5 (42 mm), and a share one subject short of the limit is
  floored, never rounded onto it.
* **It refuses what it cannot judge, and lists what it does not**
  (``ItJudgesOnlyWhatIsPressedFromAGrip``): a reversed or swapped hand axis included,
  which the 20 deg test cannot see.
* **Outside the survey a pass does not count** (``OutsideTheSurveyAPassDoesNotCount``),
  through ``check`` on the wrapped baseline.
* **The proposer proposes** (``TheProposerWritesOnlyItsProposal``): two runs give the
  same bytes, nothing lands outside its directory, and a boundary is the last whole
  step that holds, below zero too.
* **The scaffold reads** (``TheScaffoldIsRecords``).

Full suite only (not ``tests/fast.py``): the wrapped-project class runs ``check``
three times. The rest is in process and runs in a few seconds.
"""
from __future__ import annotations

import copy
import csv
import dataclasses
import glob
import hashlib
import io
import json
import math
import os
import shutil
import sys
import unittest

import _env
import _projects

from nopekit import gates as gates_mod
from nopekit import packs as packs_mod
from nopekit import store
from nopekit.modelio import load_path
from nopekit.models import ClaimKind

PACK = os.path.join(_env.REPO, "packs", "hand-ergonomics")
DATA = os.path.join(PACK, "gates", "data")
A = load_path(os.path.join(PACK, "gates", "_anthro.py"))
DERIVE = load_path(os.path.join(DATA, "derive_hand_table.py"))
FIXTURES = load_path(os.path.join(PACK, "selftest", "bad_layouts.py"))
PROPOSER = os.path.join(PACK, "proposers", "reach_envelope.py")
GID = "ergo.digit_reach"


def _provenance() -> dict:
    with open(os.path.join(DATA, "PROVENANCE.json"), encoding="utf-8") as fh:
        return json.load(fh)


def _registry():
    registry = gates_mod.Registry()
    packs_mod.load_gates("hand-ergonomics", registry, root=_env.REPO, include_env=False,
                         include_user=False)
    return registry


class _Gate(_env.EnvCase):
    """Runs the gate in process on the pack's baseline, edited."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        spec, fn = _registry().get(GID)
        cls.spec, cls.fn = spec, staticmethod(fn)    # a plain function: never bound

    def baseline(self):
        return packs_mod.baseline_context(PACK, out_dir=self.tmp())

    def params(self) -> dict:
        return copy.deepcopy(_projects.design_of(_projects.pack_baseline("hand-ergonomics")))

    def run_on(self, params=None, *, limit=None, comparator=None, units=None):
        ctx = self.baseline()
        if params is not None:
            ctx = dataclasses.replace(ctx, params=params)
        if limit is not None or comparator is not None or units is not None:
            [claim] = ctx.ledger.claims
            acc = claim.acceptance
            acc = dataclasses.replace(
                acc, limit=acc.limit if limit is None else limit,
                comparator=acc.comparator if comparator is None else type(acc.comparator)(
                    comparator),
                units=acc.units if units is None else units)
            ctx = dataclasses.replace(
                ctx, ledger=dataclasses.replace(
                    ctx.ledger, claims=[dataclasses.replace(claim, acceptance=acc)]))
        return gates_mod.run_gate(self.spec, self.fn, ctx)

    def evidence(self, verdict) -> dict:
        [path] = verdict.evidence
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)


# --------------------------------------------------------------------------- #
class TheTableIsTheSurvey(_env.EnvCase):
    """The shipped table is the two public files' subjects, unchanged."""

    def test_counts_hash_and_means(self):
        table = A.read_subjects(A.TABLE_PATH)
        prov = _provenance()
        self.assertEqual({s: len(table.subjects[s]) for s in A.SEXES}, {"F": 2208, "M": 1774})
        self.assertEqual(prov["table"]["subjects"], {"F": 2208, "M": 1774})
        with open(A.TABLE_PATH, "rb") as fh:
            self.assertEqual(hashlib.sha256(fh.read()).hexdigest(), prov["table"]["sha256"])
        self.assertEqual(table.sha256, prov["table"]["sha256"])
        fields = dict(zip(A.TABLE_HEADER[2:], A.Subject._fields))
        for column, spec in prov["columns"].items():
            for sex in A.SEXES:
                values = [getattr(s, fields[column]) for s in table.subjects[sex]]
                mean = sum(values) / len(values)
                with self.subTest(column=column, sex=sex):
                    # TR89-044 prints each mean in cm to two decimals: 0.1 mm.
                    self.assertLessEqual(abs(mean - spec["report_mean_mm"][sex]), 0.1)

    def test_an_edited_table_is_refused(self):
        here = self.tmp()
        for name in ("ansur1988_hand.csv", "PROVENANCE.json"):
            shutil.copyfile(os.path.join(DATA, name), os.path.join(here, name))
        path = os.path.join(here, "ansur1988_hand.csv")
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(text.replace("F,152,177,86,22,115,", "F,152,177,86,22,125,", 1))
        with self.assertRaises(ValueError) as caught:
            A.read_subjects(path)
        self.assertIn("derive_hand_table.py", str(caught.exception))

    def _raw(self, here: str, rows: dict) -> dict:
        """Planted public files: every mapped column, a column the table drops,
        and a byte-order mark, as the real files have."""
        prov = copy.deepcopy(_provenance())
        columns = [spec["source_column"] for spec in prov["columns"].values()]
        for source in prov["sources"]:
            out = io.StringIO(newline="")
            writer = csv.writer(out, lineterminator="\r\n")
            writer.writerow(["SUBJECT_NUMBER", "AB-EXT-DEPTH-SIT", *columns])
            for n, values in enumerate(rows[source["sex"]], 1):
                writer.writerow([n, 999, *values])
            data = ("﻿" + out.getvalue()).encode("utf-8")
            with open(os.path.join(here, source["file"]), "wb") as fh:
                fh.write(data)
            source["sha256"] = hashlib.sha256(data).hexdigest()
        return prov

    def test_the_derivation_keeps_the_mapped_columns_women_first(self):
        here = self.tmp()
        prov = self._raw(here, {"F": [[177, 86, 22, 115, 170, 60, 167]],
                                "M": [[193, 91, 24, 124, 178, 76, 178]]})
        self.assertEqual(DERIVE.derive(here, prov).decode("utf-8"),
                         ",".join(A.TABLE_HEADER) + "\n"
                         "F,1,177,86,22,115,170,60,167\nM,1,193,91,24,124,178,76,178\n")

    def test_a_file_that_is_not_the_public_one_is_refused(self):
        here = self.tmp()
        prov = self._raw(here, {"F": [[177, 86, 22, 115, 170, 60, 167]],
                                "M": [[193, 91, 24, 124, 178, 76, 178]]})
        prov["sources"][1]["sha256"] = "0" * 64
        with self.assertRaises(ValueError) as caught:
            DERIVE.derive(here, prov)
        said = str(caught.exception)
        self.assertIn("ansurMen.csv", said)
        self.assertIn("0" * 64, said)

    @unittest.skipUnless(os.environ.get("NOPEKIT_ANSUR1988_DIR"),
                         "set NOPEKIT_ANSUR1988_DIR to a directory holding the two public "
                         "files to rebuild the table from them (no network in the suite)")
    def test_the_public_files_rebuild_the_table_byte_for_byte(self):
        with open(A.TABLE_PATH, "rb") as fh:
            self.assertEqual(DERIVE.derive(os.environ["NOPEKIT_ANSUR1988_DIR"]), fh.read())


# --------------------------------------------------------------------------- #
class TheGateCountsSubjects(_Gate):
    """The design's numbers, reproduced by the gate; and the count is a count."""

    def test_the_baseline_and_the_known_bad_cluster(self):
        good = self.run_on()
        self.assertEqual((good.outcome, good.measured, good.limit, good.comparator, good.units),
                         ("pass", 97.96, 95.0, ">=", "%"))
        self.assertIn("worst-sex reach 97.96% (women; men 99.71%)", good.detail)
        self.assertIn("btn_y limits at 97.96%", good.detail)
        self.assertIn("11 judged, 1 regrip (power), 0 other", good.detail)
        self.assertIn("ANSUR 1988 2208 F / 1774 M", good.detail)
        bad = gates_mod.run_gate(self.spec, self.fn,
                                 FIXTURES.face_cluster_out_of_reach(self.baseline()))
        self.assertEqual((bad.outcome, bad.measured), ("fail", 63.08))
        self.assertIn("men 84.38%", bad.detail)

    def test_an_understated_handle_hides_fourteen_points(self):
        params = self.params()
        params["hand_grip_diameter_mm"] = 50
        found = self.run_on(params)
        self.assertEqual((found.outcome, found.measured), ("fail", 83.92))
        self.assertIn("men 95.49%", found.detail)

    def test_the_percentile_pair_is_not_the_subjects_own(self):
        """A control on the hand axis ``x`` mm past the grip centre, on the dowel
        (D = 31.75), is reached by exactly the subjects whose wrist-thumbtip minus
        wrist-centre of grip is at least ``x``."""
        table = A.read_subjects(A.TABLE_PATH)
        women = table.subjects["F"]
        reach = [s.wtt - s.wcg for s in women]
        self.assertEqual(A.percentile(reach, 5), 42.0)
        self.assertEqual(A.percentile([s.wtt for s in women], 5)
                         - A.percentile([s.wcg for s in women], 5), 48.0)
        params = self.params()
        grip = next(g for g in params["hand_grips"] if g["id"] == "right")
        u = A.unit(grip["hand_axis"], "u")
        params["hand_grip_diameter_mm"] = 31.75
        for x in (42, 48):
            at = [c + x * d for c, d in zip(grip["centre"], u)]
            params["hand_controls"] = [{"id": "probe", "at": at,
                                        "operated_by": [{"grip": "right", "digit": "thumb"}]}]
            found = self.evidence(self.run_on(params, limit=1))
            expected = (10000 * sum(1 for r in reach if r >= x) // len(women)) / 100
            with self.subTest(x=x):
                self.assertEqual(found["controls"]["probe"]["women_pct"], expected)
        # 48 mm, the percentile pair's "P5" (a limit 95% of women should reach),
        # is reached by 74.54% of them (1646 of 2208): a quarter left out, not a
        # twentieth.
        self.assertLess(found["controls"]["probe"]["women_pct"], 80.0)

    def test_a_share_one_subject_short_is_floored_never_rounded_onto_the_limit(self):
        """Review of E1: half-up to 0.01% read 1703 of 1774 men (95.998%) as 96.00,
        a pass against ``>= 96`` with 1704 needed. The share is floored: the
        failing side for every comparator the gate accepts."""
        [claim] = self.baseline().ledger.claims
        at = {limit: dataclasses.replace(claim.acceptance, limit=limit) for limit in (96, 88)}
        for count, n, limit, floored in ((1703, 1774, 96, 95.99), (1943, 2208, 88, 87.99)):
            with self.subTest(count=count, n=n):
                self.assertEqual(round(100.0 * count / n, 2), limit, "half-up meets it")
                self.assertEqual(A.share_pct(count, n), floored)
                self.assertFalse(at[limit].holds(A.share_pct(count, n)))
                self.assertTrue(at[limit].holds(A.share_pct(count + 1, n)))
        self.assertEqual(A.share_pct(1774, 1774), 100.0)
        self.assertEqual(A.share_pct(2163, 2208), 97.96)
        # Through the gate: a probe 42 mm past the right grip on the dowel is reached
        # by 2107 of 2208 women (95.4257%), the worse sex. Half-up read 95.43 and
        # passed a claim of >= 95.43.
        params = self.params()
        grip = next(g for g in params["hand_grips"] if g["id"] == "right")
        u = A.unit(grip["hand_axis"], "u")
        params["hand_grip_diameter_mm"] = 31.75
        params["hand_controls"] = [{"id": "probe", "at": [c + 42 * d for c, d in
                                                          zip(grip["centre"], u)],
                                    "operated_by": [{"grip": "right", "digit": "thumb"}]}]
        found = self.run_on(params, limit=95.43)
        self.assertEqual((found.outcome, found.measured), ("fail", 95.42), found.render())
        self.assertEqual(self.evidence(found)["accommodated"]["women"]["reach_every_control"],
                         2107)
        self.assertEqual(self.run_on(params, limit=95.42).outcome, "pass")

    def test_a_mirrored_layout_reads_the_same(self):
        params = self.params()
        for control in params["hand_controls"]:
            if control["id"] == "btn_y":
                control["at"] = [37, -4, 3]                  # one control off symmetry
        mirrored = copy.deepcopy(params)
        swap = {"left": "right", "right": "left"}
        for grip in mirrored["hand_grips"]:
            for key in ("centre", "hand_axis", "handle_axis"):
                grip[key] = [-grip[key][0], *grip[key][1:]]
            grip["id"] = swap[grip["id"]]
        for control in mirrored["hand_controls"]:
            control["at"] = [-control["at"][0], *control["at"][1:]]
            for op in control["operated_by"]:
                op["grip"] = swap[op["grip"]]
        one, two = self.run_on(params), self.run_on(mirrored)
        self.assertEqual((one.measured, one.outcome), (two.measured, two.outcome))

        def shares(verdict):
            return {cid: (row.get("women_pct"), row.get("men_pct"))
                    for cid, row in self.evidence(verdict)["controls"].items()}
        self.assertEqual(shares(one), shares(two))
        self.assertNotEqual(one.measured, self.run_on().measured, "btn_y moved the count")

    def test_the_value_never_moves_with_the_limit(self):
        base = self.run_on()
        for factor in (0.1, 0.5, 2.0, 10.0):
            with self.subTest(factor=factor):
                moved = self.run_on(limit=95.0 * factor)
                self.assertEqual((moved.measured, moved.units), (base.measured, base.units))


# --------------------------------------------------------------------------- #
class ItJudgesOnlyWhatIsPressedFromAGrip(_Gate):
    """Regrip and other-digit controls are listed; a layout it cannot judge is a
    refusal (errored), never a fail."""

    def _control(self, params, cid):
        return next(c for c in params["hand_controls"] if c["id"] == cid)

    def test_a_regrip_control_is_listed_not_judged(self):
        params = self.params()
        self._control(params, "power")["at"] = [0, 400, 0]   # out of every reach
        found = self.run_on(params)
        self.assertEqual((found.outcome, found.measured), ("pass", 97.96))
        self.assertEqual(self.evidence(found)["controls"]["power"],
                         {"judged": False,
                          "regrip_reason": "pressed once per session, by either hand"})

    def test_an_other_digit_control_is_counted_not_judged(self):
        params = self.params()
        params["hand_controls"].append({"id": "paddle", "at": [-50, -60, -40],
                                        "operated_by": [{"grip": "left", "digit": "other"}]})
        found = self.run_on(params)
        self.assertEqual((found.outcome, found.measured), ("pass", 97.96))
        self.assertIn("11 judged, 1 regrip (power), 1 other", found.detail)

    def test_refusals(self):
        def edit(fn):
            params = self.params()
            fn(params)
            return params

        cases = {
            "a regrip with no reason": (
                lambda p: self._control(p, "power").pop("regrip_reason"), "regrip_reason"),
            "a grip and a reason": (
                lambda p: self._control(p, "btn_a").update(regrip_reason="rare"), "not both"),
            "an undeclared grip": (
                lambda p: self._control(p, "btn_a")["operated_by"][0].update(grip="middle"),
                "'middle'"),
            "a zero axis": (
                lambda p: p["hand_grips"][0].update(hand_axis=[0, 0, 0]), "zero vector"),
            "a hand axis 30 deg off square": (
                lambda p: p["hand_grips"][0].update(hand_axis=[0.0, 0.5, -0.866]),
                "from perpendicular"),
            "an unknown digit": (
                lambda p: self._control(p, "btn_a")["operated_by"][0].update(digit="ring"),
                "'ring'"),
            "a duplicate id": (
                lambda p: p["hand_controls"].append(dict(self._control(p, "btn_a"))),
                "twice"),
            "a diameter that is not a number": (
                lambda p: p.update(hand_grip_diameter_mm="36 mm"), "hand_grip_diameter_mm"),
        }
        for name, (fn, said) in cases.items():
            with self.subTest(case=name):
                found = self.run_on(edit(fn))
                self.assertEqual(found.outcome, "error", found.render())
                self.assertIn(said, found.error)

    def test_a_reversed_or_swapped_hand_axis_is_refused_never_passed(self):
        """Review of E1: the 20 deg test does not depend on the axis's sign. A hand
        axis declared from the hand to the wrist, or swapped with the handle axis,
        put every wrist in front of its grip, and the known-bad cluster (a fail at
        63.08%) read a pass at 100.0%. A judged control behind its grip centre
        along the hand axis is refused, which no reversed axis escapes here."""
        bad = FIXTURES.face_cluster_out_of_reach(self.baseline())
        self.assertEqual(gates_mod.run_gate(self.spec, self.fn, bad).outcome, "fail")

        def reversed_(grip):
            grip["hand_axis"] = [-c for c in grip["hand_axis"]]

        def swapped(grip):
            grip["hand_axis"], grip["handle_axis"] = grip["handle_axis"], grip["hand_axis"]

        far = self.params()                      # btn_y 46 mm up the face: 0% as declared
        self._control(far, "btn_y")["at"] = [37, 40, 3]
        declared = self.run_on(far)
        self.assertEqual((declared.outcome, declared.measured), ("fail", 0.0))
        bumpers = self.params()                  # an index-only layout
        bumpers["hand_controls"] = [c for c in bumpers["hand_controls"]
                                    if c["id"] in ("bumper_l", "bumper_r")]
        self.assertEqual(self.run_on(bumpers).outcome, "pass")
        for layout, params in (("known-bad", bad.params), ("btn_y far", far),
                               ("bumpers", bumpers)):
            for name, slip in (("reversed", reversed_), ("swapped", swapped)):
                edited = copy.deepcopy(dict(params))
                for grip in edited["hand_grips"]:
                    slip(grip)
                ctx = dataclasses.replace(bad, params=edited)
                with self.subTest(layout=layout, slip=name):
                    found = gates_mod.run_gate(self.spec, self.fn, ctx)
                    self.assertEqual(found.outcome, "error", found.render())
                    self.assertIn("behind grip", found.error)
                    self.assertIn("hand_axis points from the wrist to the hand", found.error)

    def test_a_control_under_the_palm_is_refused(self):
        params = self.params()
        right = next(g for g in params["hand_grips"] if g["id"] == "right")
        grip = A.parse_grips([right])["right"]
        self._control(params, "stick_r")["at"] = [60, -60, -5]   # behind the centre
        self.assertLess(A.distal_mm((60.0, -60.0, -5.0), grip), 0)
        found = self.run_on(params)
        self.assertEqual(found.outcome, "error", found.render())
        self.assertIn("control 'stick_r' lies", found.error)
        self.assertIn("behind grip 'right'", found.error)
        # On its centre's plane is not behind it; a regrip control is never checked.
        params = self.params()
        on_plane = self._control(params, "stick_r")
        on_plane["at"] = list(right["centre"])
        self.assertNotEqual(self.run_on(params).outcome, "error")
        params = self.params()
        self._control(params, "power")["at"] = [60, -60, -5]
        self.assertEqual(self.run_on(params).outcome, "pass")

    def test_a_claim_it_cannot_hold_is_refused(self):
        for kw in ({"comparator": "<="}, {"units": "percent"}):
            with self.subTest(**kw):
                found = self.run_on(**kw)
                self.assertEqual(found.outcome, "error")
                self.assertIn("held as >= or >", found.error)

    def test_nothing_pressed_from_a_grip_skips(self):
        params = self.params()
        params["hand_controls"] = [self._control(params, "power")]
        found = self.run_on(params)
        self.assertEqual(found.outcome, "skipped")
        self.assertIn("no control is pressed from a grip", found.skip_reason)
        params.pop("hand_controls")
        self.assertIn("no hand_controls", self.run_on(params).skip_reason)


# --------------------------------------------------------------------------- #
class OutsideTheSurveyAPassDoesNotCount(_Gate):
    """Handles 25-50 mm and users 17 and older: outside, a pass reads Gap and a
    fail still counts (invariant 9's context clause, on a bundled gate)."""

    def test_the_breach_rule_on_its_two_keys(self):
        params = self.params()
        self.assertIsNone(gates_mod.context_breach(self.spec, params))
        for key, value, why in (("hand_grip_diameter_mm", 60, "outside"),
                                ("hand_grip_diameter_mm", 24.9, "outside"),
                                ("hand_user_age_min_years", 16, "outside")):
            with self.subTest(key=key, value=value):
                got = gates_mod.context_breach(self.spec, {**params, key: value})
                self.assertEqual((got.key, got.why), (key, why))
        for value in (25, 50):
            self.assertIsNone(gates_mod.context_breach(
                self.spec, {**params, "hand_grip_diameter_mm": value}))
        params.pop("hand_user_age_min_years")
        self.assertEqual(gates_mod.context_breach(self.spec, params).why, "absent")
        found = self.run_on(params)
        self.assertEqual(found.outcome, "pass", "an absent age is read, so the pass is not "
                                                "errored; the spine reads it outside")
        self.assertIn("youngest age not stated", found.detail)

    def test_check_reads_gap_outside_and_failing_on_a_fail(self):
        project = _projects.wrap_pack_baseline("hand-ergonomics",
                                               os.path.join(self.tmp(), "w"))
        proc = _env.nopekit(["check"], cwd=project)
        self.assertEqual(proc.returncode, 0, proc.stdout[-2000:] + proc.stderr[-2000:])
        model = os.path.join(project, "model", "wrap.py")
        with open(model, encoding="utf-8") as fh:
            text = fh.read()
        self.assertEqual(text.count("'hand_grip_diameter_mm': 36,"), 1)
        with open(model, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text.replace("'hand_grip_diameter_mm': 36,", "'hand_grip_diameter_mm': 60,"))
        claim_path = os.path.join(project, "claims", "G1.json")
        with open(claim_path, encoding="utf-8") as fh:
            claim = json.load(fh)

        def status(limit):
            claim["acceptance"]["limit"] = limit
            with open(claim_path, "w", encoding="utf-8") as fh:
                json.dump(claim, fh, indent=2)
            _env.nopekit(["check"], cwd=project)
            proc = _env.nopekit(["status", "--json"], cwd=project)
            return json.loads(proc.stdout)["statuses"]["G1"]

        outside = status(50.0)
        self.assertEqual((outside["key"], outside["cause"]), ("gap", "outside-context"))
        self.assertIn("hand_grip_diameter_mm = 60", outside["reason"])
        failing = status(95.0)
        self.assertEqual((failing["key"], failing["cause"]), ("failing", "failed"))


# --------------------------------------------------------------------------- #
class TheProposerWritesOnlyItsProposal(_env.EnvCase):
    """``ergo.reach_envelope`` writes under its directory alone, the same bytes
    twice, and refuses a layout the gate would refuse with nothing written."""

    def _layout(self, here, edit=None) -> str:
        design = _projects.design_of(_projects.pack_baseline("hand-ergonomics"))
        if edit:
            edit(design)
        path = os.path.join(here, "layout.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(design, fh)
        return path

    def _tree(self, root):
        return sorted((os.path.relpath(os.path.join(dp, f), root),
                       os.path.getsize(os.path.join(dp, f)))
                      for dp, _dn, fs in os.walk(root) for f in fs)

    def _run(self, project, out, *args):
        before = self._tree(project)
        proc = _env.run([sys.executable, PROPOSER, "--layout", "layout.json", *args],
                        cwd=project, env={"NOPEKIT_PROPOSAL_DIR": out})
        self.assertEqual(self._tree(project), before, "it wrote outside its proposal dir")
        return proc

    def _bytes(self, out):
        found = {}
        for name in sorted(os.listdir(out)):
            with open(os.path.join(out, name), "rb") as fh:
                found[name] = fh.read()
        return found

    def test_two_runs_write_the_same_bytes(self):
        project = self.tmp()
        self._layout(project)
        args = ("--pitch", "15", "--face=-75,75,-40,20", "--tester-wtt-mm", "112",
                "--tester-wcg-mm", "70")
        outs = [self.tmp(), self.tmp()]
        for out in outs:
            proc = self._run(project, out, *args)
            self.assertEqual(proc.returncode, 0, proc.stderr)
        one, two = self._bytes(outs[0]), self._bytes(outs[1])
        self.assertEqual(one, two)
        self.assertLessEqual({"reach_map.json", "design_range.json", "grip_spacing.json",
                              "fit_counts.json"}, set(one))
        ranges = json.loads(one["design_range.json"])
        self.assertEqual(ranges["reach_testers"]["small_at_or_below_women_P10"], 44.0)
        self.assertEqual(ranges["reach_testers"]["large_at_or_above_men_P90"], 62.0)
        self.assertTrue(ranges["tester"]["small_tester"])
        mapped = json.loads(one["reach_map.json"])
        self.assertEqual(mapped["controls"]["btn_y"]["women_pct"], 97.96)
        self.assertGreater(mapped["controls"]["btn_y"]["outward_mm"], 0)
        spacing = json.loads(one["grip_spacing.json"])
        self.assertEqual(spacing["centre_controls"], ["menu", "view"])
        self.assertGreater(spacing["widest_spacing_mm"], spacing["spacing_now_mm"])
        fits = json.loads(one["fit_counts.json"])["pitch_mm"]["15"]
        self.assertEqual(fits["left thumb"]["fits"], fits["right thumb"]["fits"],
                         "the baseline is mirror-symmetric")

    def test_a_refused_layout_writes_nothing(self):
        project = self.tmp()
        self._layout(project, lambda d: d["hand_grips"][0].update(hand_axis=[0, 0, 0]))
        out = self.tmp()
        proc = self._run(project, out)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("zero vector", proc.stderr)
        self.assertEqual(os.listdir(out), [])

    def test_a_reversed_hand_axis_is_refused_with_nothing_written(self):
        def reverse(design):
            for grip in design["hand_grips"]:
                grip["hand_axis"] = [-c for c in grip["hand_axis"]]
        project = self.tmp()
        self._layout(project, reverse)
        out = self.tmp()
        proc = self._run(project, out)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("behind grip", proc.stderr)
        self.assertEqual(os.listdir(out), [])

    def test_a_boundary_is_the_last_whole_step_that_holds(self):
        """Review of E1: ``_boundary`` rounded a negative boundary with ceil, toward
        zero and past the last x seen true: -7.03 read -7.0, where the target may
        already fail. It returns the last whole step that holds, at either sign.
        The proposer is loaded in process; what its import changes in the test
        process (its gates directory on the path, ``_anthro`` in the module
        table, bytecode off) is put back."""
        saved = (list(sys.path), sys.dont_write_bytecode, "_anthro" in sys.modules)
        try:
            proposer = load_path(PROPOSER)
        finally:
            sys.path[:], sys.dont_write_bytecode = saved[:2]
            if not saved[2]:
                sys.modules.pop("_anthro", None)
        for edge in (-7.03, -0.1, 0.1, 7.03, 33.3):
            with self.subTest(edge=edge):
                found = proposer._boundary(lambda x, e=edge: x <= e, -60.0, 60.0, 0.25)
                self.assertEqual(found, math.floor(edge / 0.25) * 0.25)
        self.assertIsNone(proposer._boundary(lambda x: False, -60.0, 60.0, 0.25))
        self.assertEqual(proposer._boundary(lambda x: True, -60.0, 60.0, 0.25), 60.0)

    def test_outside_propose_it_refuses(self):
        project = self.tmp()
        self._layout(project)
        proc = _env.run([sys.executable, PROPOSER, "--layout", "layout.json"], cwd=project,
                        env={"NOPEKIT_PROPOSAL_DIR": ""})
        self.assertEqual(proc.returncode, 2)
        self.assertIn("nopekit propose", proc.stderr)


# --------------------------------------------------------------------------- #
class TheScaffoldIsRecords(unittest.TestCase):
    """``scaffold/`` holds record files a project copies: each reads strictly, the
    milestones name only its claims, and the reach claim is the one the baseline
    was calibrated against."""

    def test_each_record_reads_and_they_agree(self):
        claims = {c.id: c for c in (store.read_record(p, "claims") for p in sorted(
            glob.glob(os.path.join(PACK, "scaffold", "claims", "*.json"))))}
        self.assertEqual(sorted(claims), ["GRIP-POSTURE", "REACH", "REACH-HELD"])
        self.assertEqual(claims["REACH"].kind, ClaimKind.MEASURABLE)
        self.assertEqual(claims["REACH"].tags, ["digit-reach"])
        [calibrated] = packs_mod.baseline_claims(_projects.pack_baseline("hand-ergonomics"))
        self.assertEqual(claims["REACH"].acceptance, calibrated.acceptance)
        self.assertEqual(claims["GRIP-POSTURE"].kind, ClaimKind.ASSUMPTION)
        self.assertEqual(claims["REACH-HELD"].kind, ClaimKind.PHYSICAL)
        self.assertEqual(claims["REACH-HELD"].resolves, ["GRIP-POSTURE"])
        spec, _fn = _registry().get(GID)
        self.assertTrue(set(spec.claims) & set(claims["REACH"].tags))
        self.assertFalse(set(spec.claims) & set(claims["REACH-HELD"].tags),
                         "a skip of the gate must never block the only way to settle it")
        milestones = {m.id: m for m in (store.read_record(p, "milestones") for p in sorted(
            glob.glob(os.path.join(PACK, "scaffold", "milestones", "*.json"))))}
        self.assertEqual({k: m.requires for k, m in milestones.items()},
                         {"reach-first-print": ["REACH"],
                          "reach-board-order": ["REACH", "REACH-HELD"]})


if __name__ == "__main__":
    unittest.main(verbosity=2)
