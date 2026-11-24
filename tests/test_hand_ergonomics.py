# SPDX-License-Identifier: Apache-2.0
"""hand-ergonomics (general-packs E1, E2): ``ergo.digit_reach``, ``ergo.layout_agrees``
and ``ergo.reach_envelope``.

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
* **The layout is the drawn one** (``TheLayoutIsTheDrawnOne``, E2): every control
  both ways, every cap within 0.5 mm, no handle thicker than declared (one-sided,
  read by the outer loop of the section, across two half shells, scaled back for
  what a coarse export hides), every grip centre within 0.5 mm of its drawn
  handle's axis, and the refusals; the stale scene hides a reach fail (94.33% drawn,
  97.96% declared), and so do handles drawn 10 mm back (68.52% drawn).
* **cad-solid's assembly is the scene** (``CadSolidsAssemblyIsTheScene``): its mesh
  map and the GLB its own ``assembly`` view writes both serve, on cad-solid's node
  names, copied and held to cad-solid's own on the same names; and the mesh map's
  keys are declared with cad-solid's meaning (``TheMeshMapKeysAreDeclared``), so
  doctor reports fdm-print's ``parts``.
* **The reach claim waits for both gates** (``TheReachClaimWaitsForBoth``), through
  ``check`` on the wrapped baseline.

The layout classes need trimesh, NumPy and manifold3d, as the gate does, and are
skipped, saying so, where they are absent; the wrapped projects then assert the
claim reads Skipped. Full suite only (not ``tests/fast.py``): the wrapped-project
classes run ``check`` several times. The rest is in process and runs in seconds.
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
from nopekit.util import NopekitError

PACK = os.path.join(_env.REPO, "packs", "hand-ergonomics")
DATA = os.path.join(PACK, "gates", "data")
A = load_path(os.path.join(PACK, "gates", "_anthro.py"))
DERIVE = load_path(os.path.join(DATA, "derive_hand_table.py"))
FIXTURES = load_path(os.path.join(PACK, "selftest", "bad_layouts.py"))
BUILD = load_path(os.path.join(PACK, "selftest", "make_handheld_glb.py"))
PROPOSER = os.path.join(PACK, "proposers", "reach_envelope.py")
GID = "ergo.digit_reach"
LAYOUT = "ergo.layout_agrees"


def _mesh_libraries() -> bool:
    """trimesh, NumPy and manifold3d, as ``ergo.layout_agrees`` requires them."""
    try:
        import manifold3d  # noqa: F401
        import numpy  # noqa: F401
        import trimesh  # noqa: F401
    except Exception:                                    # noqa: BLE001
        return False
    return True


MESH_LIBRARIES = _mesh_libraries()
NEEDS_MESH = unittest.skipUnless(MESH_LIBRARIES, "needs trimesh, numpy and manifold3d, as "
                                                 "ergo.layout_agrees does")


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
        """``check --tier 1``: the claim tagged digit-reach is covered by both gates
        (E2), so the layout guard must run too. Where it cannot (no trimesh, as on
        CI), the claim reads Skipped beside the pass outside the context: a skip
        is louder than a Gap (invariant 1), and a fail still reads Failing."""
        layout_runs = gates_mod.availability(_registry().get(LAYOUT)[0])[0]
        project = _projects.wrap_pack_baseline("hand-ergonomics",
                                               os.path.join(self.tmp(), "w"))
        proc = _env.nopekit(["check", "--tier", "1"], cwd=project)
        self.assertEqual(proc.returncode, 0 if layout_runs else 1,
                         proc.stdout[-2000:] + proc.stderr[-2000:])
        model = os.path.join(project, "model", "wrap.py")
        with open(model, encoding="utf-8") as fh:
            text = fh.read()
        self.assertEqual(text.count("'hand_grip_diameter_mm': 36,"), 1)
        with open(model, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text.replace("'hand_grip_diameter_mm': 36,", "'hand_grip_diameter_mm': 60,"))
        # G1 is the reach gate's claim and G2 the guard's; both carry the tag
        # digit-reach, so the limit moves on both, or the reach gate reads two
        # conditions for one tag and errors.
        claims = {}
        for cid in ("G1", "G2"):
            with open(os.path.join(project, "claims", f"{cid}.json"), encoding="utf-8") as fh:
                claims[cid] = json.load(fh)
            self.assertIn("digit-reach", claims[cid]["tags"])

        def status(limit):
            for cid, claim in claims.items():
                claim["acceptance"]["limit"] = limit
                with open(os.path.join(project, "claims", f"{cid}.json"), "w",
                          encoding="utf-8") as fh:
                    json.dump(claim, fh, indent=2)
            _env.nopekit(["check", "--tier", "1"], cwd=project)
            proc = _env.nopekit(["status", "--json"], cwd=project)
            return json.loads(proc.stdout)["statuses"]["G1"]

        outside = status(50.0)
        if layout_runs:
            self.assertEqual((outside["key"], outside["cause"]), ("gap", "outside-context"))
            self.assertIn("hand_grip_diameter_mm = 60", outside["reason"])
        else:
            self.assertEqual((outside["key"], outside["cause"]), ("skipped", "skipped"))
            self.assertIn(f"{LAYOUT} : requires python", outside["reason"])
        failing = status(95.0)
        self.assertEqual((failing["key"], failing["cause"]), ("failing", "failed"))


# --------------------------------------------------------------------------- #
class _Layout(_env.EnvCase):
    """Runs ``ergo.layout_agrees`` in process on the pack's baseline, edited."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        spec, fn = _registry().get(LAYOUT)
        cls.spec, cls.fn = spec, staticmethod(fn)

    def baseline(self):
        return packs_mod.baseline_context(PACK, out_dir=self.tmp())

    def params(self) -> dict:
        return copy.deepcopy(_projects.design_of(_projects.pack_baseline("hand-ergonomics")))

    def run_on(self, params=None, *, root=None):
        ctx = self.baseline()
        if params is not None:
            ctx = dataclasses.replace(ctx, params=params)
        if root is not None:
            ctx = dataclasses.replace(ctx, root=root)
        return gates_mod.run_gate(self.spec, self.fn, ctx)

    def evidence(self, verdict) -> dict:
        [path] = verdict.evidence
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)

    def meshes(self, *, stale=False, diameter=None) -> dict:
        """The baseline's parts, ``{part: mesh}``, as the builder draws them."""
        baseline = _projects.pack_baseline("hand-ergonomics")
        if diameter is not None:
            baseline = {**baseline, "hand_grip_diameter_mm": diameter}
        return BUILD.parts(baseline, stale=stale)

    def scene_file(self, meshes: dict) -> str:
        """``meshes`` written as cad-solid's view writes a scene; its absolute path."""
        path = os.path.join(self.tmp(), "scene.glb")
        with open(path, "wb") as fh:
            fh.write(BUILD.scene_of(meshes).export(file_type="glb"))
        return path

    def on_scene(self, meshes: dict, **edits):
        return self.run_on({**self.params(), "hand_layout_scene": self.scene_file(meshes),
                            **edits})

    def right_handle(self, mesh):
        """``mesh``, made along z about the origin, placed as the right handle."""
        import numpy as np
        import trimesh
        grip = next(g for g in _projects.pack_baseline("hand-ergonomics")["hand_grips"]
                    if g["id"] == "right")
        axis, centre = BUILD.handle_placement(grip)
        mesh.apply_transform(trimesh.geometry.align_vectors([0.0, 0.0, 1.0],
                                                            np.asarray(axis)))
        mesh.apply_translation(np.asarray(centre))
        return mesh


@NEEDS_MESH
class TheLayoutIsTheDrawnOne(_Layout):
    """The three checks, each shown to fail where it should and pass where the
    design says it must (an asymmetric pair of handles, a hollow handle, a
    handle in two shells)."""

    def test_the_baseline_scene_agrees_and_the_stale_one_does_not(self):
        good = self.run_on()
        self.assertEqual((good.outcome, good.measured, good.limit, good.comparator, good.units),
                         ("pass", 0.0, 0.5, "<=", "mm"))
        self.assertIn("12 of 12 controls drawn, 0 drawn and not declared", good.detail)
        self.assertIn("left 36.00 mm, right 36.00 mm across vs declared 36 mm + 1.0",
                      good.detail)
        self.assertIn("hand_layout_scene selftest/meshes/handheld.glb, 15 nodes", good.detail)
        bad = gates_mod.run_gate(self.spec, self.fn, FIXTURES.stale_scene(self.baseline()))
        self.assertEqual((bad.outcome, bad.measured, bad.units), ("fail", 3.0, "mm"))
        self.assertIn("worst cap 3.00 mm off (btn_y, node ctl-btn_y__0)", bad.detail)
        found = self.evidence(bad)
        self.assertEqual(found["disagreements"], [], "nothing structural is wrong")
        self.assertEqual(found["controls"]["btn_y"]["nodes"], ["ctl-btn_y__0"])

    def test_the_stale_scene_hides_a_reach_fail(self):
        """Why the guard exists: the reach gate counts the declared layout."""
        reach_spec, reach_fn = _registry().get(GID)
        declared = gates_mod.run_gate(reach_spec, reach_fn, self.baseline())
        params = self.params()
        drawn = BUILD.stale_at(_projects.pack_baseline("hand-ergonomics"))
        for control in params["hand_controls"]:
            if control["id"] == "btn_y":
                control["at"] = drawn
        actual = gates_mod.run_gate(reach_spec, reach_fn,
                                    dataclasses.replace(self.baseline(), params=params))
        self.assertEqual((declared.outcome, declared.measured), ("pass", 97.96))
        self.assertEqual((actual.outcome, actual.measured), ("fail", 94.33))
        params["hand_layout_scene"] = FIXTURES.STALE_SCENE
        moved = self.run_on(params)
        self.assertEqual((moved.outcome, moved.measured), ("pass", 0.0),
                         "a layout that follows the drawing agrees with it")

    def test_the_committed_scenes_are_the_baselines(self):
        """``make_handheld_glb.py``'s output, compared as geometry: GLB bytes are not
        stable across trimesh versions, a part's place and size are."""
        import numpy as np
        import trimesh
        baseline = _projects.pack_baseline("hand-ergonomics")
        for path, stale in ((BUILD.GOOD, False), (BUILD.STALE, True)):
            built = BUILD.scene(baseline, stale=stale)
            committed = trimesh.load(path, force="scene")
            with self.subTest(scene=os.path.basename(path)):
                self.assertEqual(sorted(committed.graph.nodes_geometry),
                                 sorted(built.graph.nodes_geometry))
                for node in built.graph.nodes_geometry:
                    a = built.geometry[built.graph[node][1]]
                    b = committed.geometry[committed.graph[node][1]]
                    np.testing.assert_allclose(b.bounds, a.bounds, atol=1e-4, err_msg=node)
                    np.testing.assert_allclose(b.centroid, a.centroid, atol=1e-4,
                                               err_msg=node)
                    self.assertAlmostEqual(b.volume, a.volume, delta=1e-2, msg=node)

    def test_a_control_missing_from_either_side_is_a_disagreement(self):
        params = self.params()
        params["hand_controls"] = [c for c in params["hand_controls"] if c["id"] != "btn_y"]
        found = self.run_on(params)
        self.assertEqual((found.outcome, found.measured, found.limit, found.units),
                         ("fail", 1.0, 0.0, "disagreements"))
        self.assertIn("part ctl-btn_y (ctl-btn_y__0) is drawn and not declared in "
                      "hand_controls", found.detail)
        params = self.params()
        params["hand_controls"].append({"id": "btn_z", "at": [46, -30, 3], "normal": [0, 0, 1],
                                        "operated_by": [{"grip": "right", "digit": "thumb"}]})
        found = self.run_on(params)
        self.assertEqual((found.outcome, found.measured), ("fail", 1.0))
        self.assertIn("control btn_z is declared and not drawn (no part ctl-btn_z, node "
                      "ctl-btn_z__0)", found.detail)

    def test_a_handle_thicker_than_declared_fails_and_a_larger_declaration_passes(self):
        thin = self.run_on({**self.params(), "hand_grip_diameter_mm": 34})
        self.assertEqual((thin.outcome, thin.measured, thin.units),
                         ("fail", 2.0, "disagreements"))
        self.assertIn("grip left's section is 36.00 mm across, more than the declared 34 mm "
                      "+ 1.0", thin.detail)
        self.assertEqual(self.run_on({**self.params(), "hand_grip_diameter_mm": 35}).outcome,
                         "pass", "36.00 is the declared 35 mm + 1.0, not more")
        self.assertEqual(self.run_on({**self.params(), "hand_grip_diameter_mm": 50}).outcome,
                         "pass", "one-sided: a larger declared handle is conservative")
        meshes = self.meshes()
        meshes["handle-right"] = self.meshes(diameter=44)["handle-right"]
        lopsided = self.on_scene(meshes)
        self.assertEqual((lopsided.outcome, lopsided.measured), ("fail", 1.0))
        self.assertIn("grip right's section is 44.00 mm across", lopsided.detail)
        self.assertEqual(self.on_scene(meshes, hand_grip_diameter_mm=44).outcome, "pass",
                         "two different handles pass when the larger is declared")

    def test_a_hollow_handle_is_read_by_its_outer_loop(self):
        import trimesh
        meshes = self.meshes()
        meshes["handle-right"] = self.right_handle(trimesh.creation.annulus(
            r_min=15.0, r_max=18.0, height=38.0, sections=96))
        self.assertEqual(self.on_scene(meshes).outcome, "pass")
        thin = self.on_scene(meshes, hand_grip_diameter_mm=30)
        self.assertEqual(thin.outcome, "fail", "the cavity's 30 mm loop is not the handle")
        self.assertIn("grip right's section is 36.00 mm across", thin.detail)

    def test_a_handle_in_two_shells_is_one_section(self):
        """Two shells split 5 mm off the axis: the larger alone surrounds the centre
        and reads about 32 mm; united in the plane they read the 36 mm handle."""
        import manifold3d
        import numpy as np
        import trimesh
        whole = manifold3d.Manifold.cylinder(38.0, 18.0, 18.0, 96, True)
        shells = []
        for half in whole.split_by_plane((1.0, 0.0, 0.0), 5.0):
            out = half.to_mesh()
            shells.append(self.right_handle(trimesh.Trimesh(
                np.asarray(out.vert_properties)[:, :3], np.asarray(out.tri_verts))))
        meshes = self.meshes()
        meshes["handle-right"], meshes["handle-right-shell"] = shells
        self.assertEqual(self.on_scene(meshes).outcome, "pass")
        thin = self.on_scene(meshes, hand_grip_diameter_mm=34)
        self.assertIn("grip right's section is 36.00 mm across", thin.detail)

    def test_a_grip_centre_outside_its_handle_is_a_disagreement(self):
        params = self.params()
        params["hand_grips"][1]["centre"] = [85, -50, -18]
        found = self.run_on(params)
        self.assertEqual((found.outcome, found.measured), ("fail", 1.0))
        self.assertIn("no drawn part surrounds grip right's centre", found.detail)

    def test_a_handle_drawn_off_its_grip_centre_is_a_disagreement(self):
        """Review of E2: the guard held only that some section surrounds each grip
        centre, so a centre up to one handle radius off the drawn axis passed. Both
        handles drawn back along the hand axis, the declared layout not following:
        the reach gate still counts the declared grips."""
        def drawn_back(shift):
            moved = copy.deepcopy(_projects.pack_baseline("hand-ergonomics"))
            for grip in moved["hand_grips"]:
                hand = A.unit(grip["hand_axis"], "hand_axis")
                grip["centre"] = [c - shift * u for c, u in zip(grip["centre"], hand)]
            return moved

        moved = drawn_back(10.0)
        found = self.on_scene(BUILD.parts(moved))
        self.assertEqual((found.outcome, found.measured, found.units),
                         ("fail", 2.0, "disagreements"))
        self.assertIn("grip left's centre is 10.00 mm off its drawn handle's axis (the "
                      "centroid of its section), more than 0.5 mm", found.detail)
        row = self.evidence(found)["grips"]["right"]
        self.assertEqual(row["centre_off_mm"], 10.0)
        self.assertEqual(row["section_diameter_mm"], 36.0, "the girth alone cannot see it")
        # The baseline's hand axis is 0.01 deg off square to its handle axis, so the
        # declared section plane meets the moved handle's axis 0.0015 mm along it.
        [right] = [g for g in moved["hand_grips"] if g["id"] == "right"]
        for drawn, expect in zip(row["drawn_centre"], right["centre"]):
            self.assertAlmostEqual(drawn, expect, delta=0.01)
        reach_spec, reach_fn = _registry().get(GID)
        declared = gates_mod.run_gate(reach_spec, reach_fn, self.baseline())
        actual = gates_mod.run_gate(reach_spec, reach_fn, dataclasses.replace(
            self.baseline(), params=_projects.design_of(moved)))
        self.assertEqual((declared.outcome, declared.measured), ("pass", 97.96))
        self.assertEqual((actual.outcome, actual.measured), ("fail", 68.52))
        near = self.on_scene(BUILD.parts(drawn_back(0.4)))
        self.assertEqual((near.outcome, near.measured), ("pass", 0.0), near.detail)
        self.assertIn("worst grip centre 0.40 mm off its handle's axis (left) vs <= 0.5 mm",
                      near.detail)
        self.assertEqual(self.on_scene(BUILD.parts(drawn_back(0.6))).outcome, "fail")

    def test_a_coarse_handle_reads_its_true_diameter(self):
        """Review of E2: a tessellated loop reads n sin(pi/n)/pi of its handle, 2.55%
        short at 8 segments, and the stated bound (0.65%) held only from 16. An
        8-segment 37.9 mm handle read 36.94 mm and passed a 36 mm declaration."""
        import trimesh
        baseline = _projects.pack_baseline("hand-ergonomics")
        length = BUILD.HANDLE_TO_MM - BUILD.HANDLE_FROM_MM

        def handles(diameter, segments):
            meshes = self.meshes()
            for grip in baseline["hand_grips"]:
                axis, centre = BUILD.handle_placement(grip)
                meshes[f"handle-{grip['id']}"] = BUILD._cylinder(diameter, length, segments,
                                                                  axis, centre)
            return meshes

        thick = self.on_scene(handles(37.9, 8))
        self.assertEqual((thick.outcome, thick.measured), ("fail", 2.0))
        self.assertIn("grip left's section is 37.90 mm across, more than the declared 36 mm "
                      "+ 1.0", thick.detail)
        for segments, chorded in ((8, 35.0818), (6, 34.3775), (16, 35.7691)):
            with self.subTest(segments=segments):
                found = self.on_scene(handles(36.0, segments))
                self.assertEqual(found.outcome, "pass", found.detail)
                row = self.evidence(found)["grips"]["left"]
                self.assertEqual(row["section_diameter_mm"], 36.0)
                self.assertAlmostEqual(row["section_chorded_mm"], chorded, places=3)
                self.assertAlmostEqual(row["section_steps"], float(segments), places=1)
        # A handle with sharp edges reads as the round handle on its corners: a
        # 30 mm square one is 38.20 mm by perimeter / pi and reads 42.43 mm. That is
        # the conservative side (PACK.md says so), never a thinner handle.
        meshes = self.meshes()
        meshes["handle-right"] = self.right_handle(trimesh.creation.box(
            extents=[30.0, 30.0, length]))
        square = self.on_scene(meshes, hand_grip_diameter_mm=42)
        self.assertEqual(square.outcome, "pass", square.detail)
        self.assertEqual(self.evidence(square)["grips"]["right"]["section_diameter_mm"],
                         42.43)
        self.assertEqual(self.on_scene(meshes, hand_grip_diameter_mm=38.2).outcome, "fail")

    def test_the_reach_gates_other_refusals_are_its_own(self):
        """PACK.md: the guard runs the parsers, not ``check_grip_ids`` or the rule
        that refuses a control behind its grip; on those the reach gate errors."""
        params = self.params()
        params["hand_controls"][3]["operated_by"][0]["grip"] = "rihgt"
        reach_spec, reach_fn = _registry().get(GID)
        reach = gates_mod.run_gate(reach_spec, reach_fn,
                                   dataclasses.replace(self.baseline(), params=params))
        self.assertEqual(reach.outcome, "error")
        self.assertIn("names grip 'rihgt', which no grip declares", reach.error)
        self.assertEqual(self.run_on(params).outcome, "pass")

    def test_the_guard_cannot_be_the_reach_gates_prerequisite(self):
        """Why it covers the tag instead of a ``needs`` edge (NO_EDGE_YET)."""
        registry = _registry()
        spec, fn = registry.get(GID)
        with self.assertRaises(NopekitError) as cm:
            registry.register(dataclasses.replace(spec, needs=[LAYOUT]), fn, replace=True)
        self.assertIn("costlier than its dependent", str(cm.exception))

    def test_skips_and_refusals(self):
        def edited(fn):
            params = self.params()
            fn(params)
            return self.run_on(params)

        cases = [
            ("no controls", lambda p: p.pop("hand_controls"), "skipped", "no hand_controls"),
            ("empty", lambda p: p.update(hand_controls=[]), "skipped", "declares no control"),
            ("no scene", lambda p: p.pop("hand_layout_scene"), "skipped",
             "no scene: the projection publishes neither hand_layout_scene"),
            ("unreadable", lambda p: p.update(hand_layout_scene="selftest/meshes/none.glb"),
             "skipped", "could not be read"),
            ("no diameter", lambda p: p.pop("hand_grip_diameter_mm"), "skipped",
             "no hand_grip_diameter_mm"),
            ("no normal", lambda p: p["hand_controls"][3].pop("normal"), "error",
             "control 'btn_a' has no normal"),
            ("zero normal", lambda p: p["hand_controls"][3].update(normal=[0, 0, 0]),
             "error", "zero vector"),
            ("one part, two ids", lambda p: p["hand_controls"].append(
                {"id": "btn y", "at": [37, -6, 3], "normal": [0, 0, 1],
                 "operated_by": [{"grip": "right", "digit": "thumb"}]}),
             "error", "both name part ctl-btn_y"),
            ("bad diameter", lambda p: p.update(hand_grip_diameter_mm="36"), "error",
             "it is a handle diameter in mm"),
        ]
        for name, fn, outcome, words in cases:
            with self.subTest(case=name):
                found = edited(fn)
                self.assertEqual(found.outcome, outcome, found)
                self.assertIn(words, found.skip_reason or found.error or "")
        housing = {k: v for k, v in self.meshes().items() if not k.startswith("ctl-")}
        found = self.on_scene(housing)
        self.assertEqual(found.outcome, "skipped")
        self.assertIn("draws no part named ctl-<id>", found.skip_reason)
        meshes = self.meshes()
        handle = meshes["handle-right"]
        handle.update_faces(list(range(len(handle.faces) - 4)))      # four faces gone
        found = self.on_scene(meshes)
        self.assertEqual(found.outcome, "error")
        # The section plane is unbounded: square to the shared handle axis, it
        # passes through both grips, so the left grip's section meets it first.
        self.assertIn("node handle-right__0 crosses the section and manifold3d built no "
                      "solid from it", found.error)


@NEEDS_MESH
class CadSolidsAssemblyIsTheScene(_Layout):
    """A project that publishes cad-solid's assembly gets the guard with no extra
    export: the mesh map itself, or the GLB cad-solid's ``assembly`` view writes
    from it, on cad-solid's node names (copied into ``gates/_names.py``, and held
    to cad-solid's own here)."""

    def write_parts(self, meshes: dict) -> dict:
        here = self.tmp()
        out = {}
        for name, mesh in meshes.items():
            path = os.path.join(here, f"{name}.stl")
            mesh.export(path)
            out[name] = path
        return out

    def test_the_copied_names_are_cad_solids(self):
        """``gates/_names.py`` against cad-solid's ``cad_solid_parts.py``, on names
        that exercise every rule: punctuation, ``__`` runs, an empty result, a
        collision and its suffix, an unsorted list."""
        names = load_path(os.path.join(PACK, "gates", "_names.py"))
        cad = load_path(os.path.join(_env.REPO, "packs", "cad-solid", "cad_solid_parts.py"))
        corpus = ["ctl-btn_y", "ctl-btn y", "ctl-btn__y", "end  cap", "back-left", "back left",
                  "???", "", "bracket.v3", "_lid_", "ctl-stick_l", "housing", "ctl-ä", "a__0"]
        for name in corpus:
            self.assertEqual(names.sanitise(name), cad.sanitise(name), name)
        for order in (corpus, list(reversed(corpus)), corpus[::2] + corpus[1::2]):
            self.assertEqual(names.movers(order), cad.movers(order))
        for body in (0, 1, 7):
            self.assertEqual(names.node("ctl-btn_y", body), cad.node("ctl-btn_y", body))
        self.assertEqual(names.MESH_KEYS, cad.MESH_KEYS)
        module = sys.modules[self.fn.__module__]
        self.assertFalse(hasattr(module, "PARTS"), "the gate reads no other pack's code")

    def test_a_copy_that_drifts_is_caught(self):
        """The test above, shown to fail: a copy that kept ``_`` runs."""
        names = load_path(os.path.join(PACK, "gates", "_names.py"))
        cad = load_path(os.path.join(_env.REPO, "packs", "cad-solid", "cad_solid_parts.py"))

        def drifted(name):
            return "".join(c if c in names._KEEP else "_" for c in str(name)).strip("_") or "part"

        self.assertNotEqual(drifted("end  cap"), cad.sanitise("end  cap"))
        self.assertEqual(names.sanitise("end  cap"), cad.sanitise("end  cap"))

    def test_the_mesh_map_serves_with_no_extra_export(self):
        bare = {k: v for k, v in self.params().items() if k != "hand_layout_scene"}
        good, stale = self.write_parts(self.meshes()), self.write_parts(self.meshes(stale=True))
        for key in ("meshes", "cad.meshes", "solids"):
            with self.subTest(key=key):
                found = self.run_on({**bare, key: good})
                self.assertEqual((found.outcome, found.measured), ("pass", 0.0), found.detail)
                self.assertEqual(self.evidence(found)["controls"]["btn_y"]["nodes"],
                                 ["ctl-btn_y__0"])
                moved = self.run_on({**bare, key: stale})
                self.assertEqual((moved.outcome, moved.measured), ("fail", 3.0))

    def test_the_assembly_views_own_glb_is_a_scene(self):
        from nopekit import site as site_mod
        registry = site_mod.ViewRegistry()
        site_mod.load_viewgens(os.path.join(_env.REPO, "packs", "cad-solid", "views"),
                               registry, pack="cad-solid", root=_env.REPO)
        _spec, view = registry.get("assembly")
        for stale, expect in ((False, ("pass", 0.0)), (True, ("fail", 3.0))):
            here = self.tmp()
            ctx = site_mod.ViewContext(root=here, assets_dir=os.path.join(here, "assets"),
                                       params={"meshes": self.write_parts(
                                           self.meshes(stale=stale))})
            drawn = view(ctx)
            found = self.run_on({**self.params(),
                                 "hand_layout_scene": os.path.join(here, drawn.src)})
            with self.subTest(stale=stale):
                self.assertEqual((found.outcome, found.measured), expect, found.detail)


class TheMeshMapKeysAreDeclared(unittest.TestCase):
    """Review of E2: the guard read cad-solid's mesh map (``meshes``, ``solids``,
    ``parts``, bare too) and the baseline declared none of them, so doctor could
    not see that a bare ``parts`` is fdm-print's print set, each part in print
    orientation. With the keys declared as cad-solid declares them, the clash is
    reported, and cad-solid and hand-ergonomics agree on all three."""

    def test_the_clash_with_fdm_prints_parts_is_reported(self):
        found = {c.key: c for c in packs_mod.key_collisions(
            ["hand-ergonomics", "fdm-print"], _env.REPO)}
        self.assertIn("parts", found)
        self.assertEqual(found["parts"].primary["hand-ergonomics"], "meshes")
        self.assertEqual(found["parts"].primary["fdm-print"], "mesh_paths")

    def test_cad_solid_and_this_pack_mean_one_thing_by_each(self):
        """The negative control: the same words, the same meaning, no warning."""
        vocabulary = packs_mod.key_vocabulary("hand-ergonomics", _env.REPO)
        names = load_path(os.path.join(PACK, "gates", "_names.py"))
        for key in names.MESH_KEYS:
            self.assertEqual(vocabulary[key]["primary"], "meshes", key)
        clashes = [c.key for c in packs_mod.key_collisions(["hand-ergonomics", "cad-solid"],
                                                            _env.REPO)]
        self.assertFalse(set(clashes) & set(names.MESH_KEYS), clashes)


# --------------------------------------------------------------------------- #
class TheReachClaimWaitsForBoth(_env.EnvCase):
    """Through ``check --tier 1`` on the wrapped baseline: with the scene one
    revision ahead of the layout, the reach gate still passes at 97.96% and the
    claim reads Failing on the guard's fail. Where the guard cannot run, the
    claim reads Skipped, never Checked (invariant 1)."""

    def test_a_reach_pass_on_a_stale_layout_is_never_checked(self):
        layout_runs = gates_mod.availability(_registry().get(LAYOUT)[0])[0]
        project = _projects.wrap_pack_baseline("hand-ergonomics",
                                               os.path.join(self.tmp(), "w"))
        model = os.path.join(project, "model", "wrap.py")
        with open(model, encoding="utf-8") as fh:
            text = fh.read()
        scene = "'hand_layout_scene': 'selftest/meshes/handheld.glb'"
        self.assertEqual(text.count(scene), 1)
        with open(model, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text.replace(scene, scene.replace("handheld.glb", "handheld_stale.glb")))
        proc = _env.nopekit(["check", "--tier", "1", "--json"], cwd=project)
        self.assertEqual(proc.returncode, 1, proc.stdout[-2000:] + proc.stderr[-2000:])
        rows = {row["gate"]: row for row in json.loads(proc.stdout)["verdicts"]}
        self.assertEqual((rows[GID]["outcome"], rows[GID]["measured"]), ("pass", 97.96))
        status = json.loads(_env.nopekit(["status", "--json"], cwd=project).stdout)["statuses"]
        if layout_runs:
            self.assertEqual((rows[LAYOUT]["outcome"], rows[LAYOUT]["measured"]), ("fail", 3.0))
            self.assertEqual((status["G1"]["key"], status["G1"]["cause"]), ("failing", "failed"))
            self.assertIn(LAYOUT, status["G1"]["reason"])
        else:
            self.assertEqual(rows[LAYOUT]["outcome"], "skipped")
            self.assertEqual(status["G1"]["key"], "skipped")


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
        for gid in (GID, LAYOUT):
            spec, _fn = _registry().get(gid)
            self.assertTrue(set(spec.claims) & set(claims["REACH"].tags), gid)
            self.assertIn(gid, claims["REACH"].evaluators)
            self.assertFalse(set(spec.claims) & set(claims["REACH-HELD"].tags),
                             f"a skip of {gid} must never block the only way to settle it")
        milestones = {m.id: m for m in (store.read_record(p, "milestones") for p in sorted(
            glob.glob(os.path.join(PACK, "scaffold", "milestones", "*.json"))))}
        self.assertEqual({k: m.requires for k, m in milestones.items()},
                         {"reach-first-print": ["REACH"],
                          "reach-board-order": ["REACH", "REACH-HELD"]})


if __name__ == "__main__":
    unittest.main(verbosity=2)
