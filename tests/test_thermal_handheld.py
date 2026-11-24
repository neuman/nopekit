# SPDX-License-Identifier: Apache-2.0
"""thermal-analytic's handheld gate: what its selftest cannot show.

The selftest shows ``thermal.skin_temperature`` passing the baseline's
commissioning terminal (38.26 C against TH1's 48 C) and failing the same
electronics in a shell at half scale (65.84 C). These tests hold the rest:

- the arithmetic against the numbers the design was judged on (34.46, 31.34 and
  38.26 C in the three states, 65.84 C at half scale) and the linear stage's
  worst corner (1.125 W against the nominal 0.585 W);
- the bias the convective length is chosen for: one vertical plate at the
  longest dimension gives 81-92% of a level device's top-and-bottom convection;
  and the biases that err cold, with their sizes: outside that range, on a face
  that points down, and what crediting the covered area would be worth;
- the clause that says the value is the average and not the peak survives the
  spine's 500-character cut, however many stages the hottest state names;
- every refusal: a record stated wrongly is an error naming the field, a
  missing key or a vented shell is a skip before the claim is read, a claim on
  the wrong side or in other units is an error;
- the value never moves when the claim's limit moves;
- the fixture is sealed and escalates past half scale when the claim's limit
  is generous.

What slipped through while the gate was designed, each held here: a heat-source
list put every source's numbers out of the mutation walk's reach (a list is one
opaque leaf), so the sources are an object keyed by name; and the charger's loss
estimated at 5 V in and 3.7 V out is about half its worst corner. In review of
T1: the docs said every bias errs hot, with no range, and called a FAIL sound,
when a model that errs hot makes its PASS the conservative verdict; and the
peak clause came last in the detail, so two LDOs cut its instruction off.

Run:  PYTHONPATH=src python3 -m unittest discover -s tests -p test_thermal_handheld.py -v
"""
from __future__ import annotations

import copy
import json
import os
import sys
import unittest
from unittest import mock

import _env
from nopekit import gates as gates_mod
from nopekit import packs as packs_mod
from nopekit.modelio import load_path
from nopekit.models import Acceptance, Claim, Comparator, Ledger, ProjectMeta

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACK = os.path.join(REPO, "packs", "thermal-analytic")
GID = "thermal.skin_temperature"

P = load_path(os.path.join(PACK, "gates", "_thermal_physics.py"))


def _raw() -> dict:
    with open(os.path.join(PACK, "selftest", "baseline.json"), encoding="utf-8") as fh:
        return json.load(fh)


def _baseline() -> dict:
    return {k: v for k, v in _raw().items() if not k.startswith("_")}


def _claims(**acceptance) -> list:
    """The baseline's own claims, TH1's condition moved by ``acceptance``."""
    claims = packs_mod.baseline_claims(_raw())
    for claim in claims:
        for key, value in acceptance.items():
            setattr(claim.acceptance, key, value)
    return claims


def _registry() -> gates_mod.Registry:
    registry = gates_mod.Registry()
    packs_mod.load_gates("thermal-analytic", registry, root=REPO, include_env=False,
                         include_user=False)
    return registry


class _Case(_env.EnvCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        # A tuple, never a bare function on the class: a function stored there
        # becomes a bound method and takes the test case as its context.
        cls.pair = _registry().get(GID)
        # The gate's own clause, never a copy typed here that could drift.
        cls.clause = cls.pair[1].__globals__["AVERAGE_NOT_PEAK"]

    def ctx(self, params=None, claims=None) -> gates_mod.GateContext:
        return gates_mod.GateContext(
            root=PACK, ledger=Ledger(meta=ProjectMeta(name="handheld"),
                                     claims=_claims() if claims is None else claims),
            model=None, params=_baseline() if params is None else params,
            out_dir=self.tmp(), tier=0, log=lambda _m: None, extra={})

    def run_with(self, params=None, claims=None):
        return gates_mod.run_gate(*self.pair, self.ctx(params, claims))

    def edited(self, **changes) -> dict:
        params = _baseline()
        params.update(changes)
        return params


# --------------------------------------------------------------------------- #
# the arithmetic
# --------------------------------------------------------------------------- #
class TheArithmeticIsTheDesigns(unittest.TestCase):
    """The numbers the design was judged on, recomputed with the pack's own
    physics: a change to the correlation, the air table or the solve moves
    them here first."""

    AMBIENT_K = 298.15
    OPEN_AREA = 0.03904 * (1.0 - 0.35)

    def skin(self, power, area=OPEN_AREA, length=0.160, eps=0.90) -> dict:
        return P.shell_skin_temperature(power, self.AMBIENT_K, area, length, eps)

    def test_a_linear_stage_is_judged_at_its_worst_corner(self):
        self.assertAlmostEqual(P.linear_dissipation(5.50, 3.00, 0.45), 1.125, places=9)
        # The nominal estimate (5 V in, 3.7 V out) is about half of it.
        self.assertAlmostEqual(P.linear_dissipation(5.00, 3.70, 0.45), 0.585, places=9)

    def test_a_linear_stage_that_raises_its_voltage_is_refused(self):
        for vin, vout, amps in ((3.0, 3.0, 0.45), (3.0, 5.5, 0.45), (5.5, 3.0, 0.0)):
            with self.subTest(vin=vin, vout=vout, amps=amps):
                with self.assertRaises(ValueError):
                    P.linear_dissipation(vin, vout, amps)

    def test_the_baselines_three_states(self):
        for power, expected in ((2.2768, 34.46), (1.4493, 31.34), (3.3493, 38.26)):
            with self.subTest(power=power):
                got = self.skin(power)
                self.assertTrue(got["film_ok"])
                self.assertAlmostEqual(P.k_to_c(got["t_s_k"]), expected, places=2)
                # The balance it solved: P = A (h_c + h_r)(T_s - T_a).
                shed = self.OPEN_AREA * (got["h_c"] + got["h_r"]) * (got["t_s_k"]
                                                                     - self.AMBIENT_K)
                self.assertAlmostEqual(shed, power, places=6)

    def test_radiation_is_more_than_half_the_loss(self):
        got = self.skin(3.3493)
        self.assertAlmostEqual(got["h_c"], 4.17, places=2)
        self.assertAlmostEqual(got["h_r"], 5.78, places=2)
        self.assertGreater(got["q_rad_w"], got["q_conv_w"])

    def test_half_scale_is_the_named_fault(self):
        got = self.skin(3.3493, area=self.OPEN_AREA * 0.25, length=0.080)
        self.assertAlmostEqual(P.k_to_c(got["t_s_k"]), 65.84, places=2)

    def test_a_bare_metal_shell_runs_ten_kelvin_hotter(self):
        painted = P.k_to_c(self.skin(3.3493)["t_s_k"])
        bare = P.k_to_c(self.skin(3.3493, eps=0.10)["t_s_k"])
        self.assertAlmostEqual(bare - painted, 10.5, delta=0.05)

    def test_no_power_is_the_ambient(self):
        self.assertAlmostEqual(self.skin(0.0)["t_s_k"], self.AMBIENT_K, places=6)

    def test_a_balance_above_the_air_table_is_a_bound_not_a_value(self):
        got = self.skin(1.0e4)
        self.assertFalse(got["film_ok"])
        # The film at the bound is the table's top, 600 K.
        self.assertAlmostEqual((got["t_s_k"] + self.AMBIENT_K) / 2.0, 600.0, places=6)

    def test_an_ambient_outside_the_air_table_is_refused(self):
        for ambient_k in (240.0, 600.0, 650.0):
            with self.subTest(ambient_k=ambient_k):
                with self.assertRaises(ValueError):
                    P.shell_skin_temperature(1.0, ambient_k, self.OPEN_AREA, 0.16, 0.9)

    def convection(self, a: float, b: float, dt: float) -> dict:
        """h, W/m2K, of an a x b face at a rise ``dt``: one Churchill-Chu vertical
        plate at the longest dimension (the model's), and McAdams hot-face-up and
        hot-face-down at L = A/P, on the module's own correlations."""
        props = P.fluid_properties("air", self.AMBIENT_K + dt / 2.0)
        lv, lh = max(a, b), a * b / (2.0 * (a + b))
        hv = (P.natural_nusselt("vertical_plate", P.rayleigh(dt, lv, props),
                                props["pr"], dt)["nu"] * props["k"] / lv)
        ra = P.rayleigh(dt, lh, props)
        up = P.natural_nusselt("horizontal_plate_hot_up", ra, props["pr"], dt)
        down = P.natural_nusselt("horizontal_plate_hot_down", ra, props["pr"], dt)
        return {"vertical": hv, "up": up["nu"] * props["k"] / lh,
                "down": down["nu"] * props["k"] / lh}

    def level(self, a: float, b: float, dt: float) -> float:
        """The model's convection over a level device's top-and-bottom mean."""
        h = self.convection(a, b, dt)
        return h["vertical"] / ((h["up"] + h["down"]) / 2.0)

    def power_at(self, target_c: float, area: float, length: float) -> float:
        """The power the model puts at an average of ``target_c``."""
        lo, hi = 0.0, 100.0
        for _ in range(100):
            mid = (lo + hi) / 2.0
            t_s = P.k_to_c(self.skin(mid, area=area, length=length)["t_s_k"])
            lo, hi = (mid, hi) if t_s < target_c else (lo, mid)
        return lo

    def test_the_convective_length_errs_hot(self):
        """One Churchill-Chu vertical plate at the longest dimension against
        McAdams hot-face-up plus hot-face-down at L = A/P, on the module's own
        correlations (outside McAdams' bands too: that is the point, the small
        faces of a watch are below them)."""
        ratios = [self.level(a, b, dt)
                  for a, b in ((0.160, 0.080), (0.140, 0.060), (0.040, 0.040))
                  for dt in (5.0, 10.0, 15.0, 20.0, 25.0)]
        self.assertGreaterEqual(min(ratios), 0.80)
        self.assertLessEqual(max(ratios), 0.925)
        self.assertLess(max(ratios), 1.0)      # always the cooler side: it errs hot

    def test_outside_the_measured_range_it_can_err_cold(self):
        """The numbers references/handheld.md section 1 gives for the biases that
        err cold. What slipped through (review of T1): "it errs hot" was said
        with no range, and on the module's own correlations it reverses for a
        large square face, or a small face at a small rise."""
        for (a, b, dt), more in (((0.400, 0.300, 15.0), 1.015), ((0.400, 0.300, 23.0), 1.03),
                                 ((0.500, 0.350, 25.0), 1.06), ((0.020, 0.020, 2.0), 1.04)):
            with self.subTest(face=(a, b), dt=dt):
                self.assertGreater(self.level(a, b, dt), 1.0)
                self.assertAlmostEqual(self.level(a, b, dt), more, delta=0.005)

    def test_a_face_that_points_down_gets_more_than_hot_face_down(self):
        """1.2-1.6x what hot-face-down alone gives, over the measured faces and
        the large ones; a 160 x 80 mm face the model puts at 48 C is about
        50.2 C under hot-face-down."""
        ratios = [h["vertical"] / h["down"] for h in
                  (self.convection(a, b, dt)
                   for a, b in ((0.160, 0.080), (0.140, 0.060), (0.040, 0.040),
                                (0.400, 0.300), (0.500, 0.350))
                   for dt in (5.0, 15.0, 25.0))]
        self.assertGreaterEqual(min(ratios), 1.2)
        self.assertLessEqual(max(ratios), 1.6)
        a, b = 0.160, 0.080
        area, lh = a * b, a * b / (2.0 * (a + b))
        power = self.power_at(48.0, area, 0.160)

        def shed_down(t_s: float) -> float:
            dt = t_s - self.AMBIENT_K
            props = P.fluid_properties("air", (t_s + self.AMBIENT_K) / 2.0)
            nu = P.natural_nusselt("horizontal_plate_hot_down", P.rayleigh(dt, lh, props),
                                   props["pr"], dt)["nu"]
            q_rad = P.radiation_exchange(t_s, self.AMBIENT_K, area, 0.90, 1.0)["q_w"]
            return nu * props["k"] / lh * area * dt + q_rad

        lo, hi = self.AMBIENT_K, self.AMBIENT_K + 200.0
        for _ in range(100):
            mid = (lo + hi) / 2.0
            lo, hi = (mid, hi) if shed_down(mid) < power else (lo, mid)
        self.assertAlmostEqual(P.k_to_c(lo), 50.2, delta=0.05)

    def test_what_crediting_the_covered_area_would_be_worth(self):
        """The covered area sheds nothing, which errs hot. Had the covered 35%
        shed as the open area does: 4.2 K on the baseline (34.10 C against
        38.26 C), and 7.1 K for the same shell at 48 C. That is the size of
        one hot bias, and so of a FAIL it can make wrongly; before review of T1
        the docs said "only within a few kelvin", with no measurement behind it."""
        whole = 0.03904
        baseline = P.k_to_c(self.skin(3.3493)["t_s_k"])
        credited = P.k_to_c(self.skin(3.3493, area=whole)["t_s_k"])
        self.assertAlmostEqual(credited, 34.10, places=2)
        self.assertAlmostEqual(baseline - credited, 4.2, delta=0.05)
        at_limit = self.power_at(48.0, self.OPEN_AREA, 0.160)
        credited = P.k_to_c(self.skin(at_limit, area=whole)["t_s_k"])
        self.assertAlmostEqual(48.0 - credited, 7.1, delta=0.05)


# --------------------------------------------------------------------------- #
# the gate
# --------------------------------------------------------------------------- #
class TheGateJudgesTheHottestState(_Case):

    def test_the_baseline_passes_at_the_charging_while_in_use_state(self):
        v = self.run_with()
        self.assertTrue(v.passed, v.error or v.detail)
        self.assertEqual((v.measured, v.limit, v.comparator, v.units, v.settles),
                         (38.26, 48.0, "<=", "degC", "average skin temperature"))
        self.assertIn("'charge-in-use'", v.detail)
        # Every linear stage in the hottest state is named with its corner.
        self.assertIn("'linear charger' 1.125 W = (5.5 - 3) V x 0.45 A", v.detail)
        self.assertIn("radiation 58% of the loss", v.detail)
        # The clause comes right after the value, before any name a record types.
        self.assertTrue(v.detail.startswith(f"average skin 38.26 C within <= 48 C, "
                                            f"{self.clause}; "), v.detail)
        with open(v.evidence[0], encoding="utf-8") as fh:
            rows = fh.read()
        for state in ("use", "charge", "charge-in-use"):
            self.assertRegex(rows, rf"(?m)^{state} +\d")

    def test_the_peak_clause_survives_the_spines_cut(self):
        """The spine cuts a detail at 500 characters. What slipped through
        (review of T1): the clause came last, and two LDOs added to the
        hottest state cut "settle the peak by claim physical or a solver" off
        it. Four LDOs, and then one source and one state with long names: the
        detail is cut each time, and the clause is whole each time."""
        params = _baseline()
        for n in range(4):
            params["heat_sources"][f"LDO {n} for the sensor rail"] = {
                "linear": {"vin_max_v": 4.2, "vout_min_v": 3.3, "i_max_a": 0.2},
                "states": ["charge-in-use"], "source": "datasheet, full load"}
        long_name = _baseline()
        long_name["heat_sources"]["linear charger"]["states"] = ["charge", "x" * 300]
        long_name["heat_sources"]["system at full load"]["states"] = ["use", "x" * 300]
        long_name["heat_sources"]["y" * 300] = long_name["heat_sources"].pop(
            "system at full load")
        for label, case in (("four LDOs", params), ("long names", long_name)):
            with self.subTest(label):
                v = self.run_with(case)
                self.assertTrue(v.passed, v.error or v.detail)
                self.assertTrue(v.detail.endswith("..."), v.detail)   # it was cut
                self.assertIn(self.clause, v.detail)

    def test_a_state_named_twice_in_one_source_counts_once(self):
        params = _baseline()
        params["heat_sources"]["linear charger"]["states"] = ["charge", "charge-in-use",
                                                             "charge-in-use"]
        self.assertEqual(self.run_with(params).measured, 38.26)

    def test_a_fail_past_the_claim(self):
        v = self.run_with(claims=_claims(limit=35.0))
        self.assertFalse(v.passed)
        self.assertFalse(v.error)
        self.assertEqual((v.measured, v.limit), (38.26, 35.0))
        self.assertIn("past <= 35 C", v.detail)

    def test_a_strict_side_is_judged_as_stated(self):
        self.assertFalse(self.run_with(claims=_claims(comparator=Comparator.LT,
                                                      limit=38.26)).passed)
        self.assertTrue(self.run_with(claims=_claims(comparator=Comparator.LE,
                                                     limit=38.26)).passed)

    def test_the_value_never_moves_with_the_limit(self):
        """The qualification moves each limit x0.1, x0.5, x2 and x10 where it was
        read; the value must stay where the inputs put it."""
        for factor in (0.1, 0.5, 2.0, 10.0):
            with self.subTest(factor=factor):
                v = self.run_with(claims=_claims(limit=48.0 * factor))
                self.assertEqual((v.measured, v.units), (38.26, "degC"))
                self.assertEqual(v.passed, 38.26 <= 48.0 * factor)

    def test_a_shell_that_cannot_shed_its_power_fails_with_no_value(self):
        params = self.edited(shell_area_m2=1.0e-4, shell_length_m=0.01)
        v = self.run_with(params)
        self.assertFalse(v.passed)
        self.assertFalse(v.error)
        self.assertIsNone(v.measured)
        self.assertIsNone(v.limit)
        self.assertIn("needs a film above the air table", v.detail)
        self.assertIn(self.clause, v.detail)

    def test_a_bound_under_the_claims_limit_decides_nothing(self):
        params = self.edited(shell_area_m2=1.0e-4, shell_length_m=0.01)
        v = self.run_with(params, claims=_claims(limit=5000.0))
        self.assertIn("the air table cannot decide it", v.error)


class EveryRefusalNamesWhatIsWrong(_Case):
    """A key the model does not state skips before the claim is read, so a
    project with no handheld never errors; a shell the model does not cover
    skips; a record or a claim stated wrongly errors, naming the field."""

    def test_a_missing_key_skips_before_the_claim_is_read(self):
        for key in ("shell_area_m2", "shell_length_m", "shell_emissivity",
                    "shell_covered_fraction", "shell_cooling", "shell_ambient_c",
                    "heat_sources"):
            with self.subTest(key=key):
                params = _baseline()
                del params[key]
                v = self.run_with(params, claims=[])       # no claim at all
                self.assertTrue(v.skipped, v.error or v.detail)
                self.assertIn(key, v.skip_reason)

    def test_every_key_present_and_no_claim_is_an_error(self):
        v = self.run_with(claims=[])
        self.assertIn("average-skin-temperature", v.error)

    def test_a_vented_or_fan_shell_skips(self):
        for word in ("vented", "fan", "Vented"):
            with self.subTest(word=word):
                v = self.run_with(self.edited(shell_cooling=word), claims=[])
                self.assertTrue(v.skipped)
                self.assertIn("outside the sealed-shell model", v.skip_reason)

    def errors(self, params=None, claims=None) -> str:
        v = self.run_with(params, claims)
        self.assertFalse(v.passed)
        self.assertFalse(v.skipped, v.skip_reason)
        self.assertTrue(v.error, v.detail)
        return v.error

    def test_a_wrong_record_is_an_error_naming_the_field(self):
        def sources(edit):
            params = _baseline()
            edit(params["heat_sources"])
            return params

        cases = {
            "an unknown cooling word": (self.edited(shell_cooling="potted"), "shell_cooling"),
            "a string boolean": (self.edited(shell_covered_fraction="true"),
                                 "shell_covered_fraction"),
            "a bool for a number": (self.edited(shell_area_m2=True), "shell_area_m2"),
            "a zero area": (self.edited(shell_area_m2=0.0), "shell_area_m2"),
            "a negative length": (self.edited(shell_length_m=-0.1), "shell_length_m"),
            "no emissivity": (self.edited(shell_emissivity=0.0), "shell_emissivity"),
            "an emissivity past 1": (self.edited(shell_emissivity=1.2), "shell_emissivity"),
            "a shell covered whole": (self.edited(shell_covered_fraction=1.0),
                                      "shell_covered_fraction"),
            "an ambient below the air table": (self.edited(shell_ambient_c=-40.0),
                                               "outside the air table"),
            "a list of sources": (self.edited(heat_sources=list(_baseline()["heat_sources"]
                                                                .values())),
                                  "non-empty object"),
            "no sources": (self.edited(heat_sources={}), "non-empty object"),
            "vin_max <= vout_min": (sources(lambda s: s["linear charger"]["linear"].update(
                vin_max_v=3.0)), "'linear charger'"),
            "a missing source": (sources(lambda s: s["system at full load"].pop("source")),
                                 "has no source"),
            "an empty source": (sources(lambda s: s["system at full load"].update(
                source="  ")), "has no source"),
            "both w and linear": (sources(lambda s: s["linear charger"].update(w=1.0)),
                                  "both of w and linear"),
            "neither w nor linear": (sources(lambda s: s["system at full load"].pop("w")),
                                     "neither of w and linear"),
            "a missing states list": (sources(lambda s: s["system at full load"].pop(
                "states")), "states"),
            "an empty states list": (sources(lambda s: s["system at full load"].update(
                states=[])), "states"),
            "an unknown source key": (sources(lambda s: s["system at full load"].update(
                duty=0.5)), "duty"),
            "a linear record short a key": (sources(lambda s: s["linear charger"]["linear"]
                                                    .pop("i_max_a")), "i_max_a"),
            "a negative w": (sources(lambda s: s["system idle while charging"].update(
                w=-0.3)), "'system idle while charging'"),
        }
        for label, (params, said) in cases.items():
            with self.subTest(label):
                self.assertIn(said, self.errors(params))

    def test_a_claim_on_the_wrong_side_or_in_other_units_is_an_error(self):
        for label, change in (("at least", {"comparator": Comparator.GE}),
                              ("a band", {"comparator": Comparator.BETWEEN,
                                          "limit_hi": 50.0}),
                              ("kelvin", {"units": "K"}),
                              ("a synonym", {"units": "C"})):
            with self.subTest(label):
                self.assertIn("state the limit so", self.errors(claims=_claims(**change)))


# --------------------------------------------------------------------------- #
# the fixture
# --------------------------------------------------------------------------- #
class TheFixtureIsSealedAndDerived(_Case):
    """The control computes its severity from the baseline claim's limit, with
    the gate's own arithmetic, and hands the baseline's claims as its ledger."""

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        sys.path.insert(0, os.path.join(PACK, "gates"))
        cls.B = load_path(os.path.join(PACK, "selftest", "bad_thermal.py"))

    def test_half_scale_runs_on_the_shipped_baseline(self):
        bad = self.B.shell_shrunk(self.ctx(params={}, claims=[]))
        self.assertAlmostEqual(bad.params["shell_area_m2"], 0.03904 * 0.25, places=12)
        self.assertAlmostEqual(bad.params["shell_length_m"], 0.080, places=12)
        self.assertEqual([c.id for c in bad.ledger.claims], ["TH1"])
        v = gates_mod.run_gate(*self.pair, bad)
        self.assertEqual((v.passed, v.measured), (False, 65.84))

    def test_a_generous_limit_shrinks_it_further(self):
        generous = [Claim(id="TH1", statement="s", tags=["average-skin-temperature"],
                          acceptance=Acceptance(quantity="average skin temperature",
                                                comparator=Comparator.LE, limit=90.0,
                                                units="degC"))]
        with mock.patch.object(self.B, "_claims", lambda: copy.deepcopy(generous)):
            bad = self.B.shell_shrunk(self.ctx(params={}, claims=[]))
        self.assertLess(bad.params["shell_length_m"], 0.080)
        v = gates_mod.run_gate(*self.pair, bad)
        self.assertFalse(v.passed)
        # T_a + MARGIN x (limit - T_a) = 25 + 1.15 x 65 = 99.75 C
        self.assertAlmostEqual(v.measured, 99.75, delta=0.02)

    def test_a_host_claim_never_reaches_the_control(self):
        host = _claims(limit=1.0e9)
        bad = self.B.shell_shrunk(self.ctx(claims=host))
        self.assertEqual([c.acceptance.limit for c in bad.ledger.claims], [48.0])


if __name__ == "__main__":
    unittest.main()
