# SPDX-License-Identifier: Apache-2.0
"""fdm.slice: what OrcaSlicer says "Success." over, and what the gate says instead.

What slipped through before the gate: fdm-print read only the mesh, and Orca 2.4.2,
run by hand, sliced an 80 mm unsupported bridge with return code 0, "Success." and
no warning; gripster's 80 mm tunnel roof sliced the same way and then failed in
print. It also dropped a part 15 mm above the bed to the bed and said nothing, and
sliced a preset with a misspelled key without the key. Each case below is one of
those, or a way the gate's own reading could be wrong (a bridge over support read
as free, a run cut into segments, arcs, another part's G-code).

The cases run the real gate through a stand-in ``orca-slicer`` on PATH
(``tests/_orca_standin.py``) that writes result.json and a synthetic G-code in
Orca 2.4.2's shapes, with toolpaths this file draws. No Orca output is committed.
One class slices the pack's own controls on a real OrcaSlicer, where one is on
PATH. Without trimesh (the CI-like container) every case asserts the gate's
availability skip instead.

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
        self.assertIn("Orca ignored wall_loops", v.error)

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
