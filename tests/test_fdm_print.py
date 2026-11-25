# SPDX-License-Identifier: Apache-2.0
"""fdm-print on published designs: what its rules said about parts nobody built for it.

A holdout run put the pack in front of designs it had never seen, and each class
here is one place where its answer was wrong for a reason that would recur:

- PACK.md defined ``part_min_wall_mm`` as "the thinnest section anywhere in the
  part", and the guard's bead rule only holds across the layer plane. A 0.395 mm
  web printed as three 0.12 mm layers failed the guard as "another unit or cannot
  be printed", and every gate behind it read Skipped. A wall and a floor are
  different counts now (``AWallAndAFloorAreDifferentCounts``).
- ``fdm.overhang`` marked ``angles > limit``: a part whose only overhangs were 45
  deg chamfers, drawn at the rule, failed at 45.00-45.05 deg as exported
  (``AChamferAtTheRuleIsNotAnOverhang``).
- ``fdm.bridge_span`` gave a ceiling with no anchor a limit of 0, so a 0.00 mm^2
  sliver of tessellation scored infinite and outranked a real cantilever
  (``ASliverIsNotAnUnanchoredCeiling``).
- No part could be stated to print on support outside ``fdm.slice``
  (``APartOnStatedSupport``).
- ``fdm.min_wall``, ``fdm.print_time_est`` and the guard read one number each, so
  the person picked the part, and the guard skipped a set with no single box
  (``TheSingleNumberGatesTakeASet``).
- Every gate but the slicer carried the same four broad tags, so the structural
  ``fdm.layer_alignment`` bound every printability claim and skipped it until a
  load axis was stated (``EachGateHasATagOfItsOwn``).

The meshes are built here, small and synthetic; none of the published designs'
geometry is in the repository. Without trimesh and numpy the mesh cases assert
the gates' availability skip instead.

Run:  PYTHONPATH=src python3 -m unittest tests.test_fdm_print -v
"""
from __future__ import annotations

import json
import math
import os
import shutil
import tempfile
import unittest

from nopekit import claims as claims_mod
from nopekit import gates as gates_mod
from nopekit import packs as packs_mod
from nopekit.modelio import load_path
from nopekit.models import Claim, Ledger

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACK = os.path.join(REPO, "packs", "fdm-print")
#: The pack's own prism builder: an (x, z) outline, counter-clockwise, extruded
#: along +y into a closed solid with no T-junctions.
_MESH_SRC = os.path.join(PACK, "selftest", "baseline_mesh.py")


def _baseline() -> dict:
    with open(os.path.join(PACK, "selftest", "baseline.json"), encoding="utf-8") as fh:
        return {k: v for k, v in json.load(fh).items() if not k.startswith("_")}


def _single(params: dict) -> dict:
    """The baseline as ONE part: its two-part ``mesh_paths`` set taken out."""
    out = dict(params)
    out.pop("mesh_paths", None)
    return out


class _Case(unittest.TestCase):
    GATE = ""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="nopekit-fdm-print-")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.registry = gates_mod.Registry()
        packs_mod.load_gates("fdm-print", self.registry, root=REPO, include_env=False,
                             include_user=False)
        self.available, self.why = gates_mod.availability(self.registry.get(self.GATE)[0])

    def _run(self, params: dict, gate: str | None = None):
        spec, fn = self.registry.get(gate or self.GATE)
        ctx = gates_mod.GateContext(root=self.dir, ledger=Ledger(), params=params,
                                    out_dir=os.path.join(self.dir, "out"), tier=1)
        return gates_mod.run_gate(spec, fn, ctx)

    def _skipped_here(self) -> bool:
        """True, after asserting the availability skip, where trimesh is absent."""
        if self.available:
            return False
        v = self._run(_single(_baseline()))
        self.assertTrue(v.skipped and not v.passed, v.render())
        self.assertEqual(v.skip_reason, self.why)
        return True

    def _stl(self, name: str, mesh) -> str:
        self.assertTrue(mesh.is_watertight and mesh.volume > 0, name)
        path = os.path.join(self.dir, f"{name}.stl")
        mesh.export(path)
        return f"{name}.stl"

    def _prism(self, outline, depth: float = 10.0):
        return load_path(_MESH_SRC).prism(outline, depth)


# --------------------------------------------------------------------------- #
# a wall across the layer plane is beads; a floor along the build axis is layers
# --------------------------------------------------------------------------- #
class AWallAndAFloorAreDifferentCounts(_Case):
    GATE = "fdm.process_model_valid"

    def test_a_floor_is_held_to_one_layer_not_one_bead(self):
        """A 0.36 mm web is three 0.12 mm layers: it passes, said in layers. A
        0.08 mm floor is under one layer and fails. Before, the floor had no key,
        and stated as the wall it failed the bead rule at 0.36 mm too."""
        base = dict(_baseline(), layer_height_mm=0.12)
        v = self._run(dict(base, part_min_floor_mm=0.36))
        self.assertEqual(v.outcome, "pass", v.render())
        self.assertIn("thinnest floor 3.0 layers deep", v.detail)
        v = self._run(dict(base, part_min_floor_mm=0.08))
        self.assertEqual(v.outcome, "fail", v.render())
        self.assertIn("under one 0.12 mm layer", v.detail)

    def test_a_wall_under_one_bead_still_fails_and_says_where_a_web_goes(self):
        v = self._run(dict(_baseline(), part_min_wall_mm=0.3))
        self.assertEqual(v.outcome, "fail", v.render())
        self.assertIn("across the layer plane", v.detail)
        self.assertIn("part_min_floor_mm", v.detail)

    def test_min_wall_names_the_layer_plane(self):
        v = self._run(_single(_baseline()), gate="fdm.min_wall")
        self.assertEqual(v.outcome, "pass", v.render())
        self.assertTrue(v.detail.startswith("thinnest wall across the layer plane 3.20 mm"),
                        v.detail)


# --------------------------------------------------------------------------- #
# fdm.overhang: a face drawn at the rule is not past it
# --------------------------------------------------------------------------- #
def _lip(angle_deg: float):
    """A post with a lip whose underside leans ``angle_deg`` from vertical, (x, z)."""
    run = 10.0
    rise = run / math.tan(math.radians(angle_deg))
    return [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (10.0 + run, 10.0 + rise),
            (10.0 + run, 22.0), (0.0, 22.0)]


class AChamferAtTheRuleIsNotAnOverhang(_Case):
    GATE = "fdm.overhang"

    def test_a_chamfer_read_at_45_02_passes_a_45_degree_rule(self):
        if self._skipped_here():
            return
        params = _single(_baseline())
        v = self._run(dict(params, mesh_path=self._stl("lip45", self._prism(_lip(45.02)))))
        self.assertEqual(v.outcome, "pass", v.render())
        self.assertIn("+0.5 deg tessellation tolerance", v.detail)
        # The tolerance is stated, and a project that states 0 gets the old rule.
        v = self._run(dict(params, mesh_path="lip45.stl", overhang_tessellation_tol_deg=0.0))
        self.assertEqual(v.outcome, "fail", v.render())

    def test_a_face_a_degree_past_the_rule_still_fails(self):
        if self._skipped_here():
            return
        v = self._run(dict(_single(_baseline()),
                           mesh_path=self._stl("lip46", self._prism(_lip(46.0)))))
        self.assertEqual(v.outcome, "fail", v.render())
        self.assertGreater(v.measured, v.limit)

    def test_a_tolerance_that_reaches_the_ceiling_is_inert(self):
        if self._skipped_here():
            return
        v = self._run(dict(_single(_baseline()),
                           mesh_path=self._stl("lip45", self._prism(_lip(45.02))),
                           overhang_limit_deg=79.0, overhang_tessellation_tol_deg=1.0))
        self.assertEqual(v.outcome, "fail", v.render())
        self.assertIn("INERT CONFIGURATION", v.detail)


# --------------------------------------------------------------------------- #
# fdm.bridge_span: a sliver of tessellation is not a ceiling
# --------------------------------------------------------------------------- #
def _hook(tip: float):
    """A post with an arm whose underside is a V; the V's flat tip, ``tip`` mm wide,
    is the part's only ceiling, and nothing under either side holds it up."""
    return [(0.0, 0.0), (2.0, 0.0), (2.0, 7.0), (6.0, 3.0), (6.0 + tip, 3.0),
            (10.0 + tip, 7.0), (10.0 + tip, 10.0), (0.0, 10.0)]


class ASliverIsNotAnUnanchoredCeiling(_Case):
    GATE = "fdm.bridge_span"

    def test_a_sliver_under_one_bead_is_listed_and_not_judged(self):
        """0.01 mm x 10 mm = 0.1 mm^2, under one 0.42 mm bead's 0.176 mm^2. Before,
        it read UNANCHORED, infinite, and failed the part."""
        if self._skipped_here():
            return
        v = self._run(dict(_single(_baseline()),
                           mesh_path=self._stl("hook", self._prism(_hook(0.01)))))
        self.assertEqual(v.outcome, "pass", v.render())
        self.assertIn("under one bead's footprint", v.detail)
        with open(v.evidence[0], encoding="utf-8") as fh:
            self.assertIn("UNDER ONE BEAD", fh.read())

    def test_a_real_unanchored_ceiling_still_fails(self):
        if self._skipped_here():
            return
        v = self._run(dict(_single(_baseline()),
                           mesh_path=self._stl("hook", self._prism(_hook(2.0)))))
        self.assertEqual(v.outcome, "fail", v.render())
        self.assertIn("UNANCHORED", v.detail)

    def test_in_a_set_the_real_cantilever_is_the_headline(self):
        """The sliver's part passes and the shelf's part is the worst named."""
        if self._skipped_here():
            return
        shelf = [(0.0, 0.0), (4.0, 0.0), (4.0, 6.0), (14.0, 6.0), (14.0, 8.0),
                 (0.0, 8.0)]
        params = dict(_single(_baseline()), mesh_paths={
            "hook": self._stl("hook", self._prism(_hook(0.01))),
            "shelf": self._stl("shelf", self._prism(shelf))})
        v = self._run(params)
        self.assertEqual(v.outcome, "fail", v.render())
        self.assertIn("worst of 2 in `mesh_paths`: shelf", v.detail)
        self.assertIn("CANTILEVER", v.detail)


# --------------------------------------------------------------------------- #
# supports_by_part: a part that prints on support, said so, with a reason
# --------------------------------------------------------------------------- #
SHELF = [(0.0, 0.0), (4.0, 0.0), (4.0, 6.0), (14.0, 6.0), (14.0, 8.0), (0.0, 8.0)]
TREE = {"supports": "tree", "reason": "the lip prints on tree supports by design"}


def _hollow(outer: float, height: float, void: float, void_h: float):
    """A box with a box-shaped void sealed inside it, the void's faces wound in."""
    import trimesh                                  # noqa: PLC0415 - tools present only

    box = trimesh.creation.box(extents=(outer, outer, height))
    box.apply_translation((0.0, 0.0, height / 2.0))
    hole = trimesh.creation.box(extents=(void, void, void_h))
    hole.apply_translation((0.0, 0.0, height / 2.0))
    hole.invert()
    return trimesh.util.concatenate([box, hole])


def _pyramid_void(outer: float, height: float):
    """A box with a pyramid-shaped void sealed inside it, apex up: its sloped
    faces lean 63.4 deg from vertical, overhangs no support can reach."""
    import numpy as np                              # noqa: PLC0415 - tools present only
    import trimesh                                  # noqa: PLC0415

    box = trimesh.creation.box(extents=(outer, outer, height))
    box.apply_translation((0.0, 0.0, height / 2.0))
    half, z0, apex = outer * 0.3, height * 0.25, height * 0.25 + outer * 0.15
    pts = np.array([(-half, -half, z0), (half, -half, z0), (half, half, z0),
                    (-half, half, z0), (0.0, 0.0, apex)])
    hole = trimesh.convex.convex_hull(pts)
    hole.invert()
    return trimesh.util.concatenate([box, hole])


class APartOnStatedSupport(_Case):
    GATE = "fdm.bridge_span"

    def test_a_cantilever_on_stated_support_is_carried(self):
        if self._skipped_here():
            return
        params = dict(_single(_baseline()), mesh_path=self._stl("shelf", self._prism(SHELF)))
        v = self._run(params)
        self.assertEqual(v.outcome, "fail", v.render())
        v = self._run(dict(params, supports_by_part={"shelf": TREE}))
        self.assertEqual(v.outcome, "pass", v.render())
        self.assertIn("carried by the stated tree support", v.detail)
        self.assertIn("the lip prints on tree supports", v.detail)

    def test_a_ceiling_on_a_sealed_void_is_judged_whatever_is_stated(self):
        """A 44 mm ceiling inside a sealed void: no support can be put there."""
        if self._skipped_here():
            return
        params = dict(_single(_baseline()),
                      mesh_path=self._stl("hollow", _hollow(60.0, 20.0, 44.0, 6.0)))
        v = self._run(params)
        self.assertEqual(v.outcome, "fail", v.render())
        v = self._run(dict(params, supports_by_part={"hollow": TREE}))
        self.assertEqual(v.outcome, "fail", v.render())
        self.assertIn("sealed void", v.detail)

    def test_overhang_carries_and_judges_alike(self):
        if self._skipped_here():
            return
        lip = self._stl("lip", self._prism(_lip(60.0)))
        params = dict(_single(_baseline()), mesh_path=lip)
        v = self._run(params, gate="fdm.overhang")
        self.assertEqual(v.outcome, "fail", v.render())
        v = self._run(dict(params, supports_by_part={"lip": TREE}), gate="fdm.overhang")
        self.assertEqual(v.outcome, "pass", v.render())
        self.assertIn("prints on stated tree support", v.detail)
        void = self._stl("void", _pyramid_void(50.0, 20.0))
        v = self._run(dict(params, mesh_path=void, supports_by_part={"void": TREE}),
                      gate="fdm.overhang")
        self.assertEqual(v.outcome, "fail", v.render())
        self.assertIn("sealed void", v.detail)

    def test_a_set_names_every_part_on_stated_support(self):
        if self._skipped_here():
            return
        params = dict(_single(_baseline()), mesh_paths={
            "shelf": self._stl("shelf", self._prism(SHELF)),
            "clamp": os.path.join(PACK, "selftest", "baseline_part.stl")},
            supports_by_part={"shelf": TREE})
        v = self._run(params)
        self.assertEqual(v.outcome, "pass", v.render())
        self.assertIn("1 of 2 part(s) print on stated support: shelf (tree)", v.detail)

    def test_a_statement_is_read_strictly(self):
        if self._skipped_here():
            return
        params = dict(_single(_baseline()), mesh_path=self._stl("shelf", self._prism(SHELF)))
        for entry, text in (
                ({"shelve": TREE}, "did you mean 'shelf'?"),
                ({"shelf": dict(TREE, supports="trees")}, "say one of tree, normal, none"),
                ({"shelf": {"supports": "tree", "reason": " "}}, "has no reason"),
                ({"shelf": dict(TREE, density=0.15)}, "unknown key 'density'"),
                ({"shelf": "tree"}, "write {supports, reason}"),
                (["shelf"], "write {part: {supports:")):
            for gate in ("fdm.bridge_span", "fdm.overhang"):
                with self.subTest(entry=entry, gate=gate):
                    v = self._run(dict(params, supports_by_part=entry), gate=gate)
                    self.assertEqual(v.outcome, "error", v.render())
                    self.assertIn(text, v.error)

    def test_none_is_the_default_said_aloud(self):
        if self._skipped_here():
            return
        params = dict(_single(_baseline()), mesh_path=self._stl("shelf", self._prism(SHELF)),
                      supports_by_part={"shelf": {"supports": "none",
                                                  "reason": "prints unsupported"}})
        self.assertEqual(self._run(params).outcome, "fail")


# --------------------------------------------------------------------------- #
# the single-number gates take a set, and the guard reads it
# --------------------------------------------------------------------------- #
#: Two parts named by box, in print pose, as a project states its set.
BOXES = {"housing": [41.0, 55.5, 13.6], "button": [8.7, 13.0, 3.0]}


class TheSingleNumberGatesTakeASet(_Case):
    GATE = "fdm.min_wall"

    def _set(self, **extra) -> dict:
        params = _single(_baseline())
        params.pop("part_bbox_mm")
        params["bbox_by_part_mm"] = dict(BOXES)
        params.update(extra)
        return params

    def test_min_wall_judges_every_part_and_names_the_worst(self):
        v = self._run(self._set(part_min_wall_by_part_mm={"housing": 1.6, "button": 0.9}))
        self.assertEqual((v.outcome, v.measured, v.comparator), ("fail", 0.9, ">="),
                         v.render())
        self.assertIn("worst of 2", v.detail)
        self.assertIn("button", v.detail)
        v = self._run(self._set(part_min_wall_by_part_mm={"housing": 1.6, "button": 1.3}))
        self.assertEqual((v.outcome, v.measured, v.limit), ("pass", 1.3, 1.26), v.render())

    def test_a_part_with_no_wall_keeps_the_set_unproven(self):
        v = self._run(self._set(part_min_wall_by_part_mm={"housing": 1.6}))
        self.assertTrue(v.skipped and not v.passed, v.render())
        self.assertIn("button", v.skip_reason)

    def test_a_misspelt_part_is_named_never_dropped(self):
        for entry, text in (({"housing": 1.6, "buton": 1.3}, "'buton'"),
                            ({"housing": "thick"}, "a positive number"),
                            ([1.6, 1.3], "write {part:")):
            with self.subTest(entry=entry):
                v = self._run(self._set(part_min_wall_by_part_mm=entry))
                self.assertTrue(v.skipped and not v.passed, v.render())
                self.assertIn(text, v.skip_reason)

    def test_a_single_number_says_the_set_it_is_not_about(self):
        v = self._run(self._set(part_min_wall_mm=1.6, part_name="housing"))
        self.assertEqual(v.outcome, "pass", v.render())
        self.assertIn("part housing", v.detail)
        self.assertIn("the 2 part(s) in `bbox_by_part_mm` state no thinnest wall of "
                      "their own", v.detail)

    def test_the_guard_runs_on_a_set_with_no_single_box(self):
        """Before, it read part_bbox_mm alone and skipped, and so did every gate
        behind it."""
        v = self._run(self._set(), gate="fdm.process_model_valid")
        self.assertEqual(v.outcome, "pass", v.render())
        self.assertIn("2 of 2 extents read as millimetres", v.detail)

    def test_the_guard_names_the_part_in_another_unit(self):
        params = self._set(bbox_by_part_mm=dict(BOXES, button=[0.0087, 0.013, 0.003]),
                           part_min_wall_by_part_mm={"housing": 1.6, "button": 0.12},
                           part_min_floor_by_part_mm={"housing": 0.36, "button": 0.05},
                           layer_height_mm=0.12)
        v = self._run(params, gate="fdm.process_model_valid")
        self.assertEqual((v.outcome, v.measured), ("fail", 3.0), v.render())
        self.assertTrue(v.detail.startswith("button: longest dimension"), v.detail)
        # The detail is capped; the evidence names every problem in full.
        with open(v.evidence[0], encoding="utf-8") as fh:
            problems = fh.read()
        for text in ("button: longest dimension", "button: thinnest wall across the "
                     "layer plane 0.12", "button: thinnest section along the build axis"):
            self.assertIn(text, problems)

    def test_print_time_estimates_every_part(self):
        params = self._set(part_volume_by_part_mm3={"housing": 9000.0, "button": 300.0})
        v = self._run(params, gate="fdm.print_time_est")
        self.assertEqual(v.outcome, "pass", v.render())
        self.assertIn("worst of 2", v.detail)
        self.assertIn("housing", v.detail)
        self.assertIn("one after another", v.detail)
        v = self._run(dict(params, max_print_time_h=0.5), gate="fdm.print_time_est")
        self.assertEqual(v.outcome, "fail", v.render())


# --------------------------------------------------------------------------- #
# tags: every gate a narrow tag of its own; broad tags only where they hold
# --------------------------------------------------------------------------- #
def _claim(*tags: str) -> Claim:
    return Claim.from_dict({"id": "C1", "statement": "the part prints",
                            "kind": "measurable", "tags": list(tags)})


class EachGateHasATagOfItsOwn(unittest.TestCase):
    def setUp(self):
        self.fdm = gates_mod.Registry()
        packs_mod.load_gates("fdm-print", self.fdm, root=REPO, include_env=False,
                             include_user=False)
        self.cad = gates_mod.Registry()
        packs_mod.load_gates("cad-solid", self.cad, root=REPO, include_env=False,
                             include_user=False)

    def _covered(self, registry, *tags) -> set:
        return {spec.id for spec in registry.specs() if claims_mod.covers(spec, _claim(*tags))}

    def test_every_fdm_gate_carries_a_tag_no_other_fdm_gate_carries(self):
        specs = list(self.fdm.specs())
        for spec in specs:
            with self.subTest(gate=spec.id):
                others = {t for s in specs if s.id != spec.id for t in s.claims}
                self.assertTrue(set(spec.claims) - others, spec.claims)

    def test_a_printability_claim_does_not_bind_the_load_or_the_clock(self):
        """Before, it bound fdm.layer_alignment, which skips with no load axis, and
        fdm.print_time_est, which skips with no speed."""
        printable = self._covered(self.fdm, "printability")
        self.assertEqual(printable, {"fdm.process_model_valid", "fdm.bed_fit",
                                     "fdm.min_wall", "fdm.overhang", "fdm.bridge_span"})
        self.assertEqual(self._covered(self.fdm, "fdm"), printable)
        self.assertEqual(self._covered(self.fdm, "layer-alignment"), {"fdm.layer_alignment"})
        self.assertIn("fdm.layer_alignment", self._covered(self.fdm, "structural"))
        self.assertEqual(self._covered(self.fdm, "print-time"), {"fdm.print_time_est"})
        self.assertEqual(self._covered(self.fdm, "bed-fit"), {"fdm.bed_fit"})

    def test_the_manifest_declares_every_tag_a_gate_carries(self):
        for name, registry in (("fdm-print", self.fdm), ("cad-solid", self.cad)):
            manifest = packs_mod.read_manifest(os.path.join(REPO, "packs", name))
            carried = {t for spec in registry.specs() for t in spec.claims}
            with self.subTest(pack=name):
                self.assertEqual(carried - set(manifest.claim_classes), set())

    def test_mesh_validity_selects_closure_without_the_process_wall(self):
        closure = {"cad.watertight", "cad.is_volume", "cad.degenerate_faces"}
        self.assertEqual(self._covered(self.cad, "mesh-validity"), closure)
        self.assertEqual(self._covered(self.cad, "mesh"), closure)
        self.assertEqual(self._covered(self.cad, "wall"), {"cad.wall_thickness"})


if __name__ == "__main__":                              # pragma: no cover
    unittest.main()
