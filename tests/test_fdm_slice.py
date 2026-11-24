# SPDX-License-Identifier: Apache-2.0
"""fdm.slice: what OrcaSlicer says "Success." over, and what the gate says instead.

What slipped through before the gate: fdm-print read only the mesh, and Orca 2.4.2,
run by hand, sliced an 80 mm unsupported bridge with return code 0, "Success." and
no warning, though a span that long sags in print. It also dropped a part 15 mm
above the bed to the bed and said nothing, and sliced a preset with a misspelled
key without the key. Each case below is one of those, or a way the gate's own
reading could be wrong (a bridge over support read as free, a run cut into
segments, arcs, another part's G-code). Since fdm-print 0.2.1 a part may slice on a
preset group of its own: the classes from ``PresetGroupsAreChosenPerPart`` on hold
which group a part takes, and that a statement the gate cannot read slices nothing
and never hides a part out of print pose.

The cases run the real gate through a stand-in ``orca-slicer`` on PATH
(``tests/_orca_standin.py``) that writes result.json and a synthetic G-code in
Orca 2.4.2's shapes, with toolpaths this file draws. No Orca output is committed.
One class slices the pack's own controls on a real OrcaSlicer, where one is on
PATH. Without trimesh (the CI-like container) every case that runs the gate asserts
its availability skip instead; ``PresetGroupsAreChosenPerPart`` and
``AStatementIsReadStrictlyEverywhere`` call the gate's readers as functions, and
run on every machine.

Run:  PYTHONPATH=src python3 -m unittest tests.test_fdm_slice -v
"""
from __future__ import annotations

import json
import os
import shutil
import stat
import sys
import tempfile
import unittest
from unittest import mock

from nopekit import gates as gates_mod
from nopekit import packs as packs_mod
from nopekit.models import Ledger, ProjectMeta

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACK = os.path.join(REPO, "packs", "fdm-print")
STANDIN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_orca_standin.py")
GATE = "fdm.slice"


def _registry() -> gates_mod.Registry:
    registry = gates_mod.Registry()
    packs_mod.load_gates("fdm-print", registry, root=REPO, include_env=False,
                         include_user=False)
    return registry


def _baseline() -> dict:
    with open(os.path.join(PACK, "selftest", "baseline.json"), encoding="utf-8") as fh:
        data = {k: v for k, v in json.load(fh).items() if not k.startswith("_")}
    data.pop("mesh_paths", None)
    data["slicer_presets"] = {r: os.path.join(PACK, p) for r, p in data["slicer_presets"].items()}
    return data


def _walls(x1: float, y1: float, top: float, x0: float = 0.2, y0: float = 0.2,
           layer: float = 0.2) -> list[str]:
    """An outer wall loop on every layer up to ``top``: the part's own extrusions."""
    lines = []
    n = int(round(top / layer))
    for i in range(1, n + 1):
        z = round(i * layer, 3)
        lines += [f"G1 Z{z:g} F600", ";TYPE:Outer wall", f"G1 X{x0:g} Y{y0:g} F9000",
                  f"G1 X{x1:g} Y{y0:g} E1", f"G1 X{x1:g} Y{y1:g} E1",
                  f"G1 X{x0:g} Y{y1:g} E1", f"G1 X{x0:g} Y{y0:g} E1"]
    return lines


def _line(role: str, z: float, points: list[tuple[float, float]]) -> list[str]:
    """One extrusion path at ``z``: a travel to its start, then each point."""
    out = [f"G1 Z{z:g} F600", f";TYPE:{role}", f"G1 X{points[0][0]:g} Y{points[0][1]:g} F9000"]
    out += [f"G1 X{x:g} Y{y:g} E0.5" for x, y in points[1:]]
    return out


#: An 80.4 x 20.4 x 12 mm box drawn as walls 0.2 mm inside its faces, the way an
#: outer wall's centre line runs half a line width inside the surface.
BOX = (80.4, 20.4, 12.0)


def _bridge_body(*, segments: int = 1, support: bool = False, wide: float = 0.0) -> str:
    """The walls, and an 80 mm Bridge line across the box at z 10 from wall to wall."""
    lines = _walls(BOX[0] - 0.2 + wide, BOX[1] - 0.2, BOX[2])
    if support:
        # A support interface 0.4 mm below, parallel to the bridge and one cell
        # (0.5 mm) to the side: a reader with no one-cell neighbourhood reads the
        # bridge as free over it.
        lines += _line("Support interface", 9.6, [(0.6, 10.7), (79.8, 10.7)])
    xs = [0.2 + (80.0 * k / segments) for k in range(segments + 1)]
    lines += _line("Bridge", 10.0, [(x, 10.2) for x in xs])
    return "\n".join(lines)


class _SliceCase(unittest.TestCase):
    """The gate run in process against the stand-in, with a mesh this case builds."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="nopekit-fdm-slice-")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.bin = os.path.join(self.dir, "bin")
        os.makedirs(self.bin)
        self.calls = os.path.join(self.bin, "calls")
        cli = os.path.join(self.bin, "orca-slicer")
        with open(STANDIN, encoding="utf-8") as src, open(cli, "w", encoding="utf-8") as fh:
            fh.write(f"#!{sys.executable}\n" + src.read())
        os.chmod(cli, os.stat(cli).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        self.registry = _registry()
        self.spec, self.fn = self.registry.get(GATE)
        self.path = self.bin + os.pathsep + os.environ.get("PATH", "")
        with mock.patch.dict(os.environ, {"PATH": self.path}):
            self.available, self.why = gates_mod.availability(self.spec)

    def _mesh(self, *, lift: float = 0.0) -> str:
        import trimesh                              # noqa: PLC0415 - tools present only

        box = trimesh.creation.box(extents=BOX)
        box.apply_translation([BOX[0] / 2, BOX[1] / 2, BOX[2] / 2 + lift])
        path = os.path.join(self.dir, "part.stl")
        box.export(path)
        return path

    def _run(self, mode: dict, params: dict | None = None, *, lift: float = 0.0,
             path: str | None = None):
        """The verdict, or None after asserting the availability skip."""
        with open(os.path.join(self.bin, "mode.json"), "w", encoding="utf-8") as fh:
            json.dump(mode, fh)
        base = _baseline()
        if self.available:
            base["mesh_path"] = self._mesh(lift=lift)
        base.update(params or {})
        ctx = gates_mod.GateContext(
            root=self.dir, ledger=Ledger(meta=ProjectMeta(name="slice")), params=base,
            out_dir=os.path.join(self.dir, "out"), tier=2, log=lambda _m: None, extra={})
        with mock.patch.dict(os.environ, {"PATH": path if path is not None else self.path}):
            verdict = gates_mod.run_gate(self.spec, self.fn, ctx)
        if not self.available:
            # The CI-like run has no trimesh: the outcome to assert is the
            # availability skip, never a quiet pass and never a skipped test.
            self.assertTrue(verdict.skipped and not verdict.passed, verdict.render())
            self.assertEqual(verdict.skip_reason, self.why)
            return None
        return verdict

    def _slices(self) -> int:
        if not os.path.exists(self.calls):
            return 0
        with open(self.calls, encoding="utf-8") as fh:
            return sum(1 for line in fh if "--slice" in line)


class ASuccessIsNotAPass(_SliceCase):
    def test_an_80_mm_free_bridge_fails_on_success(self):
        v = self._run({"body": _bridge_body()})
        if v is None:
            return
        self.assertEqual(v.outcome, "fail", v.render())
        self.assertTrue(79.0 < v.measured < 81.0, v.render())
        self.assertEqual(self._slices(), 1)


class ASupportedBridgeIsNotASpan(_SliceCase):
    def test_a_bridge_over_support_one_cell_aside_passes(self):
        v = self._run({"body": _bridge_body(support=True)})
        if v is None:
            return
        self.assertEqual(v.outcome, "pass", v.render())
        self.assertEqual(v.measured, 0.0)


class ARunIsJoinedAcrossSegments(_SliceCase):
    def test_four_collinear_segments_are_one_80_mm_run(self):
        v = self._run({"body": _bridge_body(segments=4)})
        if v is None:
            return
        self.assertEqual(v.outcome, "fail", v.render())
        self.assertTrue(79.0 < v.measured < 81.0, v.render())


class AFloatingPartFails(_SliceCase):
    def test_a_part_15_mm_off_the_bed_fails_before_the_slicer_runs(self):
        v = self._run({"body": _bridge_body()}, lift=15.0)
        if v is None:
            return
        self.assertEqual(v.outcome, "fail", v.render())
        self.assertIsNone(v.measured)
        self.assertIn("not in print pose: z-min 15.00 mm", v.detail)
        self.assertFalse(os.path.exists(self.calls), "the slicer ran on a part not in pose")


class ARefusalIsAFail(_SliceCase):
    def test_minus_50_fails_in_the_slicers_words(self):
        v = self._run({"rc": -50, "words": "stand-in: no object fits"})
        if v is None:
            return
        self.assertEqual(v.outcome, "fail", v.render())
        self.assertIn("-50 CLI_NO_SUITABLE_OBJECTS", v.detail)
        self.assertIn("stand-in: no object fits", v.detail)


class AWarningIsAFail(_SliceCase):
    def test_a_warning_fails_on_success(self):
        v = self._run({"body": _bridge_body(support=True), "warning": "stand-in warning"})
        if v is None:
            return
        self.assertEqual(v.outcome, "fail", v.render())
        self.assertIn("stand-in warning", v.detail)


class AToolFailureIsAnError(_SliceCase):
    def test_each_is_errored_never_a_fail(self):
        body = _bridge_body(support=True)
        cases = {
            "-51": ({"rc": -51, "words": "stand-in: bad parameters"}, "returned -51"),
            "-17": ({"rc": -17}, "returned -17"),
            "exit disagrees": ({"body": body, "exit": 1}, "exited 1 but"),
            "no result.json": ({"body": body, "no_result": True}, "wrote no out/result.json"),
        }
        for label, (mode, words) in cases.items():
            with self.subTest(label):
                v = self._run(mode)
                if v is None:
                    return
                self.assertEqual(v.outcome, "error", v.render())
                self.assertIn(words, v.error)

    def test_a_timeout_is_errored(self):
        with mock.patch.dict(self.fn.__globals__, {"SLICE_TIMEOUT_S": 1}):
            v = self._run({"body": _bridge_body(support=True), "sleep": 5})
        if v is None:
            return
        self.assertEqual(v.outcome, "error", v.render())
        self.assertIn("ran past 1 s", v.error)


class AnIgnoredKeyIsAnError(_SliceCase):
    def test_a_preset_key_missing_from_the_echo_is_errored(self):
        v = self._run({"body": _bridge_body(support=True), "drop": ["wall_loops"]})
        if v is None:
            return
        self.assertEqual(v.outcome, "error", v.render())
        self.assertIn("Orca did not use wall_loops", v.error)

    def test_a_value_the_slicer_changed_is_errored(self):
        v = self._run({"body": _bridge_body(support=True), "set": {"brim_width": "8"}})
        if v is None:
            return
        self.assertEqual(v.outcome, "error", v.render())
        self.assertIn("brim_width", v.error)


class TheSlicerVersionIsBound(_SliceCase):
    def test_another_release_is_skipped(self):
        v = self._run({"version": "2.5.0", "body": _bridge_body()})
        if v is None:
            return
        self.assertTrue(v.skipped and not v.passed, v.render())
        self.assertIn("OrcaSlicer-2.5.0:", v.skip_reason)
        self.assertEqual(self._slices(), 0)

    def test_a_header_that_disagrees_with_the_banner_is_errored(self):
        v = self._run({"header": "2.4.1", "body": _bridge_body(support=True)})
        if v is None:
            return
        self.assertEqual(v.outcome, "error", v.render())
        self.assertIn("OrcaSlicer 2.4.1", v.error)


class ArcsAreRefused(_SliceCase):
    def test_an_arc_or_absolute_extrusion_is_errored(self):
        body = _bridge_body(support=True)
        for label, mode in (("G2", {"body": body + "\nG2 X1 Y1 I1 J0 E0.1"}),
                            ("M82", {"body": body, "absolute": True})):
            with self.subTest(label):
                v = self._run(mode)
                if v is None:
                    return
                self.assertEqual(v.outcome, "error", v.render())


class TheGcodeIsThisPart(_SliceCase):
    """A G-code whose extrusions are not the mesh's extents is a FAIL with no
    measured value: what Orca sliced is not the part the claim is about."""

    def test_extents_5_mm_off_the_mesh_fail(self):
        v = self._run({"body": _bridge_body(support=True, wide=5.0)})
        if v is None:
            return
        self.assertEqual(v.outcome, "fail", v.render())
        self.assertIsNone(v.measured)
        self.assertIn("the G-code is not this part", v.detail)


class NothingToSliceIsSkipped(_SliceCase):
    def test_no_presets_is_skipped_and_slices_nothing(self):
        params = _baseline()
        params.pop("slicer_presets")
        with open(os.path.join(self.bin, "mode.json"), "w", encoding="utf-8") as fh:
            json.dump({}, fh)
        ctx = gates_mod.GateContext(root=self.dir, params=params,
                                    out_dir=os.path.join(self.dir, "out"), tier=2)
        with mock.patch.dict(os.environ, {"PATH": self.path}):
            v = gates_mod.run_gate(self.spec, self.fn, ctx)
        self.assertTrue(v.skipped and not v.passed, v.render())
        if self.available:
            self.assertIn("no slicer_presets", v.skip_reason)
        self.assertEqual(self._slices(), 0)

    def test_no_orca_is_the_availability_skip(self):
        empty = os.path.join(self.dir, "empty")
        os.makedirs(empty)
        ctx = gates_mod.GateContext(root=self.dir, params=_baseline(),
                                    out_dir=os.path.join(self.dir, "out"), tier=2)
        with mock.patch.dict(os.environ, {"PATH": empty}):
            v = gates_mod.run_gate(self.spec, self.fn, ctx)
            available, why = gates_mod.availability(self.spec)
        self.assertFalse(available)
        self.assertTrue(v.skipped and not v.passed, v.render())
        self.assertEqual(v.skip_reason, why)


def _bar_body(role: str) -> str:
    """The walls, and one 80 mm run of ``role`` across the box at z 10."""
    return "\n".join(_walls(BOX[0] - 0.2, BOX[1] - 0.2, BOX[2])
                     + _line(role, 10.0, [(0.2, 10.2), (80.2, 10.2)]))


class EveryRoleOverAirIsABridge(_SliceCase):
    """Orca 2.4.2 prints an 80 mm bar 2 mm wide as ``Overhang wall`` only, and a
    roof over 0% infill as ``Internal Bridge``: a reader of ``Bridge`` alone read
    0.0 on both (measured on the real slicer: 79.85 and 87.06 mm)."""

    def test_an_overhang_wall_or_internal_bridge_over_air_fails(self):
        for role in ("Overhang wall", "Internal Bridge"):
            with self.subTest(role):
                v = self._run({"body": _bar_body(role)})
                if v is None:
                    return
                self.assertEqual(v.outcome, "fail", v.render())
                self.assertTrue(79.0 < v.measured < 81.0, v.render())

    def test_an_outer_wall_is_not_read(self):
        v = self._run({"body": _bar_body("Outer wall")})
        if v is None:
            return
        self.assertEqual(v.outcome, "pass", v.render())
        self.assertEqual(v.measured, 0.0)


class ALegacyKeyIsReadAsOrcaReadsIt(_SliceCase):
    """What Orca 2.4.2's own source renames or retires is not "ignored": measured,
    ``wall_infill_order`` reached the block as ``wall_sequence``. Anything else not
    in the block is still an error."""

    def _process(self, **extra) -> dict:
        with open(os.path.join(PACK, "selftest", "orca", "process.json"), encoding="utf-8") as fh:
            preset = json.load(fh)
        preset.update(extra)
        path = os.path.join(self.dir, "process.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(preset, fh)
        presets = _baseline()["slicer_presets"]
        return {"slicer_presets": dict(presets, process=path)}

    def test_a_renamed_key_binds_under_its_new_name(self):
        params = self._process(wall_infill_order="inner wall/outer wall/infill")
        mode = {"body": _bridge_body(support=True), "drop": ["wall_infill_order"],
                "set": {"wall_sequence": "inner wall/outer wall"}}
        v = self._run(mode, params)
        if v is None:
            return
        self.assertEqual(v.outcome, "pass", v.render())
        v = self._run({"body": _bridge_body(support=True), "drop": ["wall_infill_order"]},
                      params)
        self.assertEqual(v.outcome, "error", v.render())
        self.assertIn("as wall_sequence", v.error)

    def test_a_retired_key_is_named_and_an_unknown_one_errors(self):
        params = self._process(adaptive_layer_height="1")
        v = self._run({"body": _bridge_body(support=True), "drop": ["adaptive_layer_height"]},
                      params)
        if v is None:
            return
        self.assertEqual(v.outcome, "pass", v.render())
        with open(os.path.join(self.dir, "out", "fdm-slice", "part", "fdm-slice.json"),
                  encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["retired_keys_not_used"],
                             ["process.adaptive_layer_height"])
        params = self._process(bed_type="Textured PEI Plate")
        v = self._run({"body": _bridge_body(support=True), "drop": ["bed_type"]}, params)
        self.assertEqual(v.outcome, "error", v.render())
        self.assertIn("Orca did not use bed_type", v.error)


class ASlicerThatCannotStartIsErrored(_SliceCase):
    def test_a_help_that_exits_127_is_errored_not_skipped(self):
        v = self._run({"help_crash": True, "body": _bridge_body()})
        if v is None:
            return
        self.assertEqual(v.outcome, "error", v.render())
        self.assertIn("--help exited 127", v.error)
        self.assertEqual(self._slices(), 0)


class AFailIsNeverFiledUnderAnError(_SliceCase):
    def test_a_part_out_of_pose_fails_beside_a_part_whose_run_errored(self):
        if not self.available:
            self._run({})                           # asserts the availability skip
            return
        import trimesh                              # noqa: PLC0415 - tools present only

        paths = {}
        for name, lift in (("floating", 15.0), ("grounded", 0.0)):
            box = trimesh.creation.box(extents=BOX)
            box.apply_translation([BOX[0] / 2, BOX[1] / 2, BOX[2] / 2 + lift])
            paths[name] = os.path.join(self.dir, f"{name}.stl")
            box.export(paths[name])
        v = self._run({"rc": -51, "words": "stand-in: bad parameters"},
                      {"mesh_paths": paths, "mesh_path": None})
        if v is None:
            return
        self.assertEqual(v.outcome, "fail", v.render())
        self.assertIn("floating: not in print pose", v.detail)
        self.assertIn("1 part(s) errored", v.detail)


class TheBuildAxisIsThePacksParse(_SliceCase):
    def test_spellings_the_mesh_gates_read_as_plus_z_slice(self):
        for axis in ("z-axis", "+Z", [0, 0, 2]):
            with self.subTest(axis=axis):
                v = self._run({"body": _bridge_body(support=True)}, {"build_axis": axis})
                if v is None:
                    return
                self.assertEqual(v.outcome, "pass", v.render())
        v = self._run({"body": _bridge_body(support=True)}, {"build_axis": "y"})
        self.assertTrue(v.skipped, v.render())


#: The preset groups' own names for the stand-in's ``bodies``.
SUPPORTS = "nopekit fdm-print test 0.20 PETG supports"


class PresetGroupsAreChosenPerPart(unittest.TestCase):
    """Which group a part slices on (``_assign``), as a function: an exact name,
    else the one glob that matches, else the default (None). It reads no key order:
    the verdict cache digests ``slicer_presets`` with its keys sorted, so an order
    that chose would let one statement's verdict be served for another. A key that
    reaches no part is a problem, never a quiet fall-back to the default: a
    misspelled part name sent that part to the global triple, the silent mode the
    groups exist to close."""

    PARTS = ["lid", "hinge_pin", "hinge_cap", "gasket"]

    @classmethod
    def setUpClass(cls):
        cls.assign = staticmethod(_registry().get(GATE)[1].__globals__["_assign"])

    def test_an_exact_name_then_one_glob_then_the_default(self):
        chosen, problems = self.assign(self.PARTS, ["hinge_*", "hinge_cap", "lid"],
                                       "mesh_paths")
        self.assertEqual(problems, [])
        self.assertEqual(chosen, {"lid": "lid", "hinge_pin": "hinge_*",
                                  "hinge_cap": "hinge_cap", "gasket": None})

    def test_two_globs_on_one_part_is_a_problem_in_either_order(self):
        seen = []
        for keys in (["hinge_*", "*_pin"], ["*_pin", "hinge_*"]):
            with self.subTest(keys=keys):
                chosen, problems = self.assign(self.PARTS, keys, "mesh_paths")
                self.assertEqual(len(problems), 1, problems)
                self.assertIn('part hinge_pin is matched by 2 globs in slicer_presets '
                              '("*_pin", "hinge_*")', problems[0])
                self.assertEqual(chosen["hinge_cap"], "hinge_*")
                seen.append((chosen, problems))
        self.assertEqual(seen[0], seen[1])

    def test_an_unknown_part_name_is_a_problem_that_names_the_closest(self):
        chosen, problems = self.assign(self.PARTS, ["hinge_pn"], "mesh_paths")
        self.assertEqual(problems, ['slicer_presets["hinge_pn"] names no part in '
                                    '`mesh_paths` (did you mean "hinge_pin"?)'])
        self.assertIsNone(chosen["hinge_pin"])

    def test_a_glob_that_matches_no_part_is_a_problem(self):
        _chosen, problems = self.assign(self.PARTS, ["gaskt*"], "mesh_paths")
        self.assertEqual(problems, ['slicer_presets["gaskt*"] matches no part in '
                                    '`mesh_paths`'])

    def test_case_counts(self):
        chosen, problems = self.assign(["Lid"], ["lid", "L*"], "mesh_path")
        self.assertEqual(chosen, {"Lid": "L*"})
        self.assertEqual(problems, ['slicer_presets["lid"] names no part in `mesh_path` '
                                    '(did you mean "Lid"?)'])


class AStatementIsReadStrictlyEverywhere(unittest.TestCase):
    """What ``slicer_presets`` says (``_statement``), as a function on a bare
    context: ``(default, groups, None)``, or ``(None, None, verdict)``. What slipped
    through: every case of the strict reader ran through the gate, which without
    trimesh (CI) stops at the availability skip, so a reader that took any
    statement and returned the three roles merged with CI green. This class needs
    no mesh and no slicer, and runs on every machine."""

    DEFAULT = {"machine": "m.json", "process": "p.json", "filament": "f.json"}
    GROUP = {"machine": "m.json", "process": "s.json", "filament": "f.json"}

    @classmethod
    def setUpClass(cls):
        cls.statement = staticmethod(_registry().get(GATE)[1].__globals__["_statement"])

    def _read(self, presets):
        return self.statement(gates_mod.GateContext(params={"slicer_presets": presets}))

    def test_a_default_and_its_groups_are_read(self):
        other = dict(self.GROUP, filament="tpu.json")
        default, groups, bail = self._read({**self.DEFAULT, "hinge_*": self.GROUP,
                                            "gasket": other})
        self.assertIsNone(bail)
        self.assertEqual(default, self.DEFAULT)
        self.assertEqual(groups, {"hinge_*": self.GROUP, "gasket": other})

    def test_groups_alone_need_no_default(self):
        default, groups, bail = self._read({"*": self.GROUP})
        self.assertIsNone(bail)
        self.assertEqual((default, groups), ({}, {"*": self.GROUP}))

    def test_a_path_object_is_a_path(self):
        import pathlib                              # noqa: PLC0415 - this case only

        default, _groups, bail = self._read(dict(self.DEFAULT,
                                                 machine=pathlib.PurePosixPath("m.json")))
        self.assertIsNone(bail)
        self.assertEqual(default["machine"], "m.json")

    def test_each_problem_is_an_error_that_names_it(self):
        short = {"machine": "m.json", "process": "s.json"}
        cases = {
            "misspelled role": ({**self.DEFAULT, "filamnet": "f.json"},
                                'slicer_presets has an unknown key "filamnet" (did you '
                                'mean "filament"?): it takes machine, process and filament'),
            "a part name with a path, not a group": (
                {**self.DEFAULT, "lid": "s.json"}, 'slicer_presets has an unknown key "lid"'),
            "group short of a role": ({**self.DEFAULT, "hinge_*": short},
                                      'slicer_presets["hinge_*"] names no filament preset: '
                                      'a group names all three, and takes none from the '
                                      'default'),
            "a role left empty in a group": ({"hinge_*": dict(self.GROUP, process="")},
                                             'slicer_presets["hinge_*"] names no process '
                                             'preset'),
            "unknown key in a group": ({**self.DEFAULT,
                                        "lid": {**self.GROUP, "proces": "x.json"}},
                                       'slicer_presets["lid"] has an unknown key "proces" '
                                       '(did you mean "process"?): a group takes machine, '
                                       'process and filament'),
            "a role as a group": ({**self.DEFAULT, "machine": self.GROUP},
                                  'slicer_presets.machine is a group, but machine is a '
                                  'preset role and takes a path; a part named machine takes '
                                  'a glob that matches it, such as "machin[e]"'),
            "a list for a path": ({**self.DEFAULT,
                                   "hinge_*": {**self.GROUP, "filament": ["a.json"]}},
                                  'slicer_presets["hinge_*"].filament is a list, not a path '
                                  'to a preset file'),
            "a number for a role": ({**self.DEFAULT, "process": 3},
                                    "slicer_presets.process is a number, not a path to a "
                                    "preset file"),
        }
        for label, (presets, words) in cases.items():
            with self.subTest(label):
                default, groups, bail = self._read(presets)
                self.assertIsNone(default)
                self.assertIsNone(groups)
                self.assertEqual(bail.outcome, "error", bail.render())
                self.assertIn(words, bail.error)

    def test_a_triple_short_of_a_role_is_skipped(self):
        cases = {
            "no slicer_presets": (None, "no slicer_presets: the slicer gate binds its "
                                        "verdict to a named preset"),
            "an empty statement": ({}, "slicer_presets names no machine preset; all three "
                                       "are needed"),
            "a default short of a role": ({"machine": "m.json", "process": "p.json"},
                                          "names no filament preset; all three are needed"),
            "an empty role": (dict(self.DEFAULT, filament=""), "names no filament preset"),
            "a short default beside a group": ({"machine": "m.json", "lid": self.GROUP},
                                               "names no process preset"),
        }
        for label, (presets, words) in cases.items():
            with self.subTest(label):
                default, groups, bail = self._read(presets)
                self.assertIsNone(default)
                self.assertTrue(bail.skipped and not bail.passed, bail.render())
                self.assertIn(words, bail.skip_reason)

    def test_no_key_order_moves_the_reading(self):
        """The verdict cache digests ``slicer_presets`` with its keys sorted, so two
        statements that differ only in key order must read alike, a problem
        included: the first one named must not hang on the order written."""
        short = {"machine": "m.json", "process": "s.json"}
        tpu = dict(short, filament="t.json")
        pairs = {
            "a good statement": ({**self.DEFAULT, "lid": self.GROUP, "hinge_*": tpu},
                                 {"hinge_*": tpu, "lid": self.GROUP, **self.DEFAULT}),
            "two bad keys": ({"lid": short, **self.DEFAULT, "filamnet": "x.json"},
                             {"filamnet": "x.json", **self.DEFAULT, "lid": short}),
            "two bad keys in a group": ({"lid": {"proces": "x.json", "machin": "y.json",
                                                 "filament": "f.json"}},
                                        {"lid": {"machin": "y.json", "proces": "x.json",
                                                 "filament": "f.json"}}),
        }
        for label, (one, two) in pairs.items():
            with self.subTest(label):
                first, second = self._read(one), self._read(two)
                self.assertEqual(first[:2], second[:2])
                if first[2] is not None or second[2] is not None:
                    self.assertEqual(first[2].error, second[2].error)
                    self.assertTrue(first[2].error, first[2].render())


class _GroupCase(_SliceCase):
    """Two parts, ``lid`` and ``hinge_pin``, each the box with an 80 mm bridge. The
    stand-in prints the bridge free on the baseline process and held on
    ``SUPPORTS``, a copy of it with support on."""

    def setUp(self):
        super().setUp()
        with open(os.path.join(PACK, "selftest", "orca", "process.json"), encoding="utf-8") as fh:
            preset = json.load(fh)
        preset.update(name=SUPPORTS, enable_support="1")
        self.supports = os.path.join(self.dir, "supports.json")
        with open(self.supports, "w", encoding="utf-8") as fh:
            json.dump(preset, fh)
        base = _baseline()["slicer_presets"]
        self.default = dict(base)
        self.on_supports = dict(base, process=self.supports)
        self.mode = {"body": _bridge_body(), "bodies": {SUPPORTS: _bridge_body(support=True)}}

    def _parts(self, presets: dict, **mode):
        params = {"slicer_presets": presets}
        if self.available:
            path = self._mesh()
            params.update(mesh_path=None, mesh_paths={"lid": path, "hinge_pin": path})
        return self._run(dict(self.mode, **mode), params)

    def _evidence(self, part: str) -> dict:
        with open(os.path.join(self.dir, "out", "fdm-slice", part, "fdm-slice.json"),
                  encoding="utf-8") as fh:
            return json.load(fh)


class APartSlicesOnItsOwnGroup(_GroupCase):
    def test_one_global_triple_judges_the_hinge_on_a_print_nobody_makes(self):
        v = self._parts(self.default)
        if v is None:
            return
        self.assertEqual(v.outcome, "fail", v.render())
        self.assertIn("2 of 2 past the limit", v.detail)

    def test_the_hinges_group_puts_it_on_supports(self):
        v = self._parts(dict(self.default, **{"hinge_*": self.on_supports}))
        if v is None:
            return
        self.assertEqual(v.outcome, "fail", v.render())
        self.assertIn("1 of 2 past the limit (lid)", v.detail)
        self.assertIn("presets by part: default ", v.detail)
        hinge, lid = self._evidence("hinge_pin"), self._evidence("lid")
        self.assertEqual((hinge["preset_group"], hinge["worst_span_mm"]), ("hinge_*", 0.0))
        self.assertEqual(hinge["presets"]["process"]["name"], SUPPORTS)
        self.assertIsNone(lid["preset_group"])
        self.assertNotEqual(lid["presets"]["process"]["name"], SUPPORTS)
        with open(self.supports, "rb") as want, \
                open(os.path.join(self.dir, "out", "fdm-slice", "hinge_pin", "process.json"),
                     "rb") as got:
            self.assertEqual(got.read(), want.read(), "the hinge was handed another process")
        self.assertEqual(self._slices(), 2)

    def test_a_glob_for_every_part_needs_no_default(self):
        v = self._parts({"*": self.on_supports})
        if v is None:
            return
        self.assertEqual(v.outcome, "pass", v.render())
        self.assertEqual(v.measured, 0.0)
        self.assertIn(f"'{SUPPORTS}'", v.detail)
        self.assertIn('of "*"', v.detail)

    def test_a_part_no_group_names_with_no_default_is_skipped(self):
        v = self._parts({"hinge_*": self.on_supports})
        if v is None:
            return
        self.assertTrue(v.skipped and not v.passed, v.render())
        self.assertIn("lid (no group in slicer_presets names it", v.skip_reason)
        self.assertEqual(self._slices(), 1)


class AStatementTheGateCannotReadIsAnError(_GroupCase):
    """Each is errored, and no part is sliced: a key the gate does not read could
    put any part on the wrong triple."""

    def test_each_is_errored_and_slices_nothing(self):
        short = {"machine": self.default["machine"], "process": self.supports}
        cases = {
            "misspelled role": ({**self.default, "filamnet": self.default["filament"]},
                                'unknown key "filamnet" (did you mean "filament"?)'),
            "group short of a role": ({**self.default, "hinge_*": short},
                                      'slicer_presets["hinge_*"] names no filament preset: '
                                      'a group names all three, and takes none from the '
                                      'default'),
            "unknown key in a group": ({**self.default,
                                        "lid": {**self.on_supports, "proces": "x.json"}},
                                       'slicer_presets["lid"] has an unknown key "proces" '
                                       '(did you mean "process"?)'),
            "a role as a group": ({**self.default, "machine": self.on_supports},
                                  "slicer_presets.machine is a group"),
            "a list for a path": ({**self.default,
                                   "hinge_*": {**self.on_supports, "filament": ["a.json"]}},
                                  'slicer_presets["hinge_*"].filament is a list, not a path'),
            "unknown part name": ({**self.default, "hinge_pn": self.on_supports},
                                  'slicer_presets["hinge_pn"] names no part in '
                                  '`mesh_paths` (did you mean "hinge_pin"?)'),
            "a glob on nothing": ({**self.default, "gasket_*": self.on_supports},
                                  'slicer_presets["gasket_*"] matches no part'),
            "two globs": ({**self.default, "hinge_*": self.on_supports,
                           "*_pin": self.on_supports}, "matched by 2 globs"),
        }
        for label, (presets, words) in cases.items():
            with self.subTest(label):
                v = self._parts(presets)
                if v is None:
                    return
                self.assertEqual(v.outcome, "error", v.render())
                self.assertIn(words, v.error)
                self.assertEqual(self._slices(), 0)

    def test_the_error_does_not_hang_on_key_order(self):
        short = {"machine": self.default["machine"], "process": self.supports}
        pairs = {
            "two globs": ({"hinge_*": self.on_supports, "*_pin": self.on_supports,
                           **self.default},
                          {**self.default, "*_pin": self.on_supports,
                           "hinge_*": self.on_supports}),
            "two bad keys": ({"lid": short, **self.default, "filamnet": "x.json"},
                             {"filamnet": "x.json", **self.default, "lid": short}),
        }
        for label, (one, two) in pairs.items():
            with self.subTest(label):
                errors = []
                for presets in (one, two):
                    v = self._parts(presets)
                    if v is None:
                        return
                    errors.append(v.error)
                self.assertEqual(errors[0], errors[1])

    def test_a_part_out_of_pose_still_fails_beside_it(self):
        """Every problem with the statement, and a missing preset file, is held
        until each part's pose is read. What slipped through: only a key that
        reached no part was held; a misspelled role, a group short of a role and a
        missing file returned before any part was read, so a part 15 mm above the
        bed read errored or skipped where this file promised the FAIL."""
        if not self.available:
            self._run({})                           # asserts the availability skip
            return
        import trimesh                              # noqa: PLC0415 - tools present only

        box = trimesh.creation.box(extents=BOX)
        box.apply_translation([BOX[0] / 2, BOX[1] / 2, BOX[2] / 2 + 15.0])
        floating = os.path.join(self.dir, "floating.stl")
        box.export(floating)
        short = {"machine": self.default["machine"], "process": self.supports}
        missing = dict(self.on_supports, process=os.path.join(self.dir, "nowhere.json"))
        cases = {
            "unknown part name": ({**self.default, "hinge_pn": self.on_supports},
                                  'slicer_presets["hinge_pn"] names no part'),
            "misspelled role": ({**self.default, "filamnet": self.default["filament"]},
                                'slicer_presets has an unknown key "filamnet"'),
            "group short of a role": ({**self.default, "hinge_*": short},
                                      'slicer_presets["hinge_*"] names no filament preset'),
            "a missing preset file": ({**self.default, "hinge_*": missing},
                                      'slicer_presets["hinge_*"].process points at'),
            "no slicer_presets": (None, "no slicer_presets"),
        }
        for label, (presets, words) in cases.items():
            with self.subTest(label):
                v = self._run(self.mode, {
                    "slicer_presets": presets, "mesh_path": None,
                    "mesh_paths": {"lid": floating, "hinge_pin": self._mesh()}})
                self.assertEqual(v.outcome, "fail", v.render())
                self.assertIn("1 of 2 part(s) in `mesh_paths` fail", v.detail)
                self.assertIn("lid: not in print pose", v.detail)
                self.assertIn(f"no other part sliced: {words}", v.detail)
                self.assertEqual(self._slices(), 0)


class AMissingPresetFileSlicesNothing(_GroupCase):
    """A group whose preset file does not exist is a skip that names the file, and
    no part is sliced: the default's parts too, as with every statement problem."""

    def test_a_missing_file_in_a_group_is_skipped(self):
        nowhere = os.path.join(self.dir, "nowhere.json")
        v = self._parts({**self.default, "hinge_*": dict(self.on_supports, process=nowhere)})
        if v is None:
            return
        self.assertTrue(v.skipped and not v.passed, v.render())
        self.assertIn(f'slicer_presets["hinge_*"].process points at {nowhere}, which does '
                      f'not exist', v.skip_reason)
        self.assertEqual(self._slices(), 0)


class TheRealSlicer(unittest.TestCase):
    """The pack's own controls on a real OrcaSlicer 2.4.2, where one is on PATH:
    the baseline clamp passes (~17.9 mm, measured) and the 90 mm arch fails
    (~89.9 mm). Skipped, saying not walked, elsewhere."""

    def test_the_controls_on_a_real_orca(self):
        registry = _registry()
        spec, fn = registry.get(GATE)
        available, why = gates_mod.availability(spec)
        if not available:
            self.skipTest(f"not walked: {why}")
        out = tempfile.mkdtemp(prefix="nopekit-fdm-real-")
        self.addCleanup(shutil.rmtree, out, True)
        good = gates_mod.run_gate(spec, fn, packs_mod.baseline_context(PACK, out_dir=out))
        if good.skipped:
            self.skipTest(f"not walked: {good.skip_reason}")
        self.assertEqual(good.outcome, "pass", good.render())
        self.assertTrue(17.0 < good.measured < 18.5, good.render())
        host = gates_mod.GateContext(root=out, out_dir=out, extra={"pack_dir": PACK})
        bad = gates_mod.run_gate(spec, fn, gates_mod.run_fixture(spec, fn, host, trace=None,
                                                                 out_dir=out))
        self.assertEqual(bad.outcome, "fail", bad.render())
        self.assertTrue(89.0 < bad.measured < 90.5, bad.render())


if __name__ == "__main__":
    unittest.main()
