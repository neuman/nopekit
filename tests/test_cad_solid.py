# SPDX-License-Identifier: Apache-2.0
"""cad-solid: the wall a ray budget never sampled, and the gap a clash gate cannot see.

What slipped through before 0.2.0:

- ``cad.wall_thickness`` cast from an evenly spaced subset of 4000 faces on a part
  with more. A thin wall is a few faces on a detailed part, so it is between the
  samples exactly when the part is detailed: a watertight 20,492-face part whose
  0.5 mm plate had its 12 faces between them read PASS at 59.983 mm against a
  1.0 mm minimum, and a 0.3 mm fin on a 49,126-face plate passed in 8 of 10 face
  orderings. It casts from every face now.
- Cast from every face, a ray from a small face beside an acute edge crossed the
  corner and hit the face on the other side of the edge almost at once: every part
  with such an edge read about 0 mm, set by the triangle size. A hit counts only on
  nonadjacent surfaces at every draft angle; the acute-corner exclusion is only
  between adjacent coplanar groups. The old 30-degree filter passed a thin rib
  drafted 16-20 degrees per side at the supporting block's 5 mm thickness.
- Nothing measured a clearance. ``cad.clash`` reads a 0.01 mm gap and an 8 mm gap
  both as PASS (no shared material, as PACK.md says), and a clearance claim tagged
  ``fit`` read Checked on that pass. ``cad.clearance`` measures the gap with
  manifold3d against the claim's own limit.

What slipped through before 0.3.0:

- ``cad.wall_thickness`` held every mesh to one minimum. An assembly with a bought
  part (cover glass, a board), a part thin on purpose (a living hinge) or a part made
  by another process (an SLA light pipe) read FAIL, and the only way out was to lower
  the one minimum, which passes a thin wall anywhere. Each part now takes its own
  minimum, an exempt part is named and not judged, and a box holds one region of a
  part to its own minimum. The records are strict: a malformed entry FAILS.
- In review of that change: a pass reported the wall with the lowest thickness over
  its own minimum, in mm, under "minimum wall thickness". A claim on that quantity
  read Checked at a 1.25 mm shell beside a 0.45 mm hinge. A pass now reports the
  thinnest wall. And a minimum written as a pattern that matched only an exempt part
  was dropped with no word; it is refused now, as the exact name was.

The meshes are built here and handed to the gates in ``ctx.extra["meshes"]``, the
channel the pack's fixtures use. Without trimesh or manifold3d (the CI-like
container) every case asserts the gate's availability skip instead.

Run:  PYTHONPATH=src python3 -m unittest tests.test_cad_solid -v
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest

from nopekit import claims as claims_mod
from nopekit import gates as gates_mod
from nopekit import packs as packs_mod
from nopekit.models import Claim, Ledger

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACK = os.path.join(REPO, "packs", "cad-solid")


def _baseline() -> dict:
    with open(os.path.join(PACK, "selftest", "baseline.json"), encoding="utf-8") as fh:
        return {k: v for k, v in json.load(fh).items() if not k.startswith("_")}


def _claim(limit: float = 1.0, units: str = "mm", comparator: str = ">=") -> Claim:
    return Claim.from_dict({"id": "C1", "statement": "the parts clear each other",
                            "kind": "measurable", "tags": ["clearance"],
                            "acceptance": {"quantity": "minimum clearance",
                                           "comparator": comparator, "limit": limit,
                                           "units": units}})


class _Case(unittest.TestCase):
    GATE = ""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="nopekit-cad-solid-")
        self.addCleanup(shutil.rmtree, self.dir, True)
        registry = gates_mod.Registry()
        packs_mod.load_gates("cad-solid", registry, root=REPO, include_env=False,
                             include_user=False)
        self.registry = registry
        self.available, self.why = gates_mod.availability(registry.get(self.GATE)[0])

    def _run(self, gate: str, meshes: dict, params: dict | None = None,
             claims: list | None = None):
        spec, fn = self.registry.get(gate)
        ctx = gates_mod.GateContext(
            root=self.dir, ledger=Ledger(claims=list(claims or [])),
            params=dict(_baseline(), **(params or {})), out_dir=os.path.join(self.dir, "out"),
            tier=1, extra={"meshes": meshes})
        return gates_mod.run_gate(spec, fn, ctx)

    def _skipped_here(self, gate: str | None = None) -> bool:
        """True, after asserting the availability skip, where the tools are absent."""
        if self.available:
            return False
        v = self._run(gate or self.GATE, {})
        self.assertTrue(v.skipped and not v.passed, v.render())
        self.assertEqual(v.skip_reason, self.why)
        return True


def _box(extents, at):
    import trimesh                                  # noqa: PLC0415 - tools present only

    box = trimesh.creation.box(extents=extents)
    box.apply_translation(at)
    return box


class EveryFaceIsCast(_Case):
    GATE = "cad.wall_thickness"

    def test_a_fin_between_the_old_samples_fails(self):
        """4364 faces: a sphere, a subdivided block and a 0.5 mm fin, each closed, with
        the fin's 12 faces put at indices the old 4000-ray linspace never cast from."""
        if self._skipped_here():
            return
        import numpy as np                          # noqa: PLC0415 - tools present only
        import trimesh                              # noqa: PLC0415

        sphere = trimesh.creation.icosphere(subdivisions=3, radius=10.0)
        block = _box((40.0, 40.0, 10.0), (60.0, 0.0, 0.0))
        for _ in range(4):
            block = block.subdivide()
        fin = _box((20.0, 20.0, 0.5), (0.0, 60.0, 0.0))
        body = trimesh.util.concatenate([sphere, block])
        n = len(body.faces) + len(fin.faces)
        sampled = set(np.unique(np.linspace(0, n - 1, 4000).round().astype(int)).tolist())
        gaps = [i for i in range(n) if i not in sampled][:len(fin.faces)]
        faces = np.empty((n, 3), dtype=int)
        others = [i for i in range(n) if i not in set(gaps)]
        faces[gaps] = fin.faces + len(body.vertices)
        faces[others] = body.faces
        mesh = trimesh.Trimesh(vertices=np.vstack([body.vertices, fin.vertices]),
                               faces=faces, process=False)
        self.assertTrue(mesh.is_watertight and mesh.is_volume)
        self.assertGreater(n, 4000)
        v = self._run(self.GATE, {"finned": mesh})
        self.assertEqual(v.outcome, "fail", v.render())
        self.assertAlmostEqual(v.measured, 0.5, places=3)


class AnEdgeIsNotAWall(_Case):
    GATE = "cad.wall_thickness"

    def test_a_45_degree_edge_reads_the_wall_not_the_corner(self):
        """A 10 mm prism with one 45 deg edge, subdivided to 3072 faces. Without the
        opposed-face filter it read 0.295 mm, a ray from the bottom face beside the
        edge hitting the slanted face; its walls are 10 mm."""
        if self._skipped_here():
            return
        import numpy as np                          # noqa: PLC0415 - tools present only
        import trimesh                              # noqa: PLC0415

        corners = [(x, y, z) for x, y in ((0, 0), (20, 0), (10, 10), (0, 10)) for z in (0, 10)]
        prism = trimesh.convex.convex_hull(np.array(corners, dtype=float))
        for _ in range(4):
            prism = prism.subdivide()
        self.assertEqual(len(prism.faces), 3072)
        v = self._run(self.GATE, {"wedge": prism})
        self.assertEqual(v.outcome, "pass", v.render())
        self.assertAlmostEqual(v.measured, 10.0, places=3)

    def test_two_opposed_faces_close_together_still_fail(self):
        """The filter keeps a wall: a 0.5 mm plate, its faces opposed, fails."""
        if self._skipped_here():
            return
        v = self._run(self.GATE, {"plate": _box((20.0, 20.0, 0.5), (0.0, 0.0, 0.0))})
        self.assertEqual(v.outcome, "fail", v.render())
        self.assertAlmostEqual(v.measured, 0.5, places=3)


class ATaperedRibIsStillAWall(_Case):
    GATE = "cad.wall_thickness"

    def test_draft_past_fifteen_degrees_does_not_erase_the_rib(self):
        if self._skipped_here():
            return
        import importlib.util
        import math
        import numpy as np
        import trimesh

        if importlib.util.find_spec("manifold3d") is None:
            self.skipTest("manifold3d absent: cannot build the rib/block union")
        # The confirmed false pass: the 0.2-1.2 mm rib's two flanks have normals
        # outside the old 30-degree filter, which returned the 5 mm block alone.
        # In review of holdout round 3 the along-a-face rule did the same at 31 and
        # 35 deg per side (GRAZE_DEG at 30 deg): the gate passed at the block's
        # 5.000 mm, and this test tried 16 and 20 deg only. Each draft is run at the
        # subdivisions where a flank's normal ray reaches the other flank at all: at
        # 35 deg only from the rib's top fifth, so the two face centres of a flank
        # not subdivided (a third and two thirds up) send their rays into the block,
        # and at 38 deg only from its top tenth. That is the sampler's limit, the
        # same before the edge rules as after them.
        for draft, levels in ((16.0, (0, 1, 2)), (20.0, (0, 1, 2)), (31.0, (0, 1, 2)),
                              (35.0, (1, 2)), (38.0, (2, 3))):
            with self.subTest(draft=draft):
                h = (1.2 - 0.2) / 2 / math.tan(math.radians(draft))
                xz = [(-0.6, 0), (0.6, 0), (0.1, h), (-0.1, h)]
                vertices = [(x, y, 5.0 + z) for y in (-10.0, 10.0) for x, z in xz]
                rib = trimesh.convex.convex_hull(np.array(vertices))
                block = _box((20.0, 30.0, 5.0), (0.0, 0.0, 2.5))
                mesh = trimesh.boolean.union([block, rib], engine="manifold")
                for subdivisions in range(max(levels) + 1):
                    if subdivisions in levels:
                        with self.subTest(subdivisions=subdivisions):
                            self.assertTrue(mesh.is_volume)
                            v = self._run(self.GATE, {"ribbed": mesh},
                                          {"process_min_wall_mm": 1.0})
                            self.assertEqual(v.outcome, "fail", v.render())
                            self.assertLess(v.measured, 1.0)
                            self.assertTrue(v.locators)
                    mesh = mesh.subdivide()


# --------------------------------------------------------------------------- #
# cad.wall_thickness: a minimum per part, exempt parts, boxes (0.3.0)
# --------------------------------------------------------------------------- #
#: The fallback minimum every wall case below states.
MIN_WALL = {"process_min_wall_mm": 1.2}


def _plates(**thickness) -> dict:
    """One 20 x 20 mm plate per part, ``thickness`` mm thick, 40 mm apart."""
    return {name: _box((20.0, 20.0, t), (40.0 * i, 0.0, 0.0))
            for i, (name, t) in enumerate(thickness.items())}


#: The region of the door that holds its hinge, in the assembly frame, mm.
HINGE_BOX = [[27.0, -11.0, -1.0], [33.0, 11.0, 1.0]]


def _door(hinge: float, fin: float | None = None):
    """One part: a 2 mm door, a 4 mm wide hinge strip ``hinge`` mm thick at x = 30
    (inside ``HINGE_BOX``) and, when given, a fin ``fin`` mm thick at x = 50
    (outside it). Three closed shells in one mesh, so the part is watertight."""
    import trimesh                                  # noqa: PLC0415 - tools present only

    shells = [_box((20.0, 20.0, 2.0), (0.0, 0.0, 0.0)),
              _box((4.0, 20.0, hinge), (30.0, 0.0, 0.0))]
    if fin is not None:
        shells.append(_box((4.0, 20.0, fin), (50.0, 0.0, 0.0)))
    return trimesh.util.concatenate(shells)


def _hinge(**overrides) -> dict:
    """The ``wall_exceptions`` entry that holds the door's hinge to 0.4 mm."""
    entry = {"part": "door", "box_mm": HINGE_BOX, "min_mm": 0.4,
             "reason": "living hinge of the battery door, printed flat"}
    entry.update(overrides)
    return entry


class _WallCase(_Case):
    GATE = "cad.wall_thickness"

    def _evidence(self, v) -> dict:
        with open(v.evidence[0], encoding="utf-8") as fh:
            return json.load(fh)

    def assertRefused(self, v, text: str):
        self.assertEqual((v.outcome, v.units), ("fail", "refused entries"), v.render())
        self.assertIn(text, v.detail)


class TheBaselineCarriesNoWallRule(_WallCase):
    """The pack's own controls with the three keys empty: the known-good reads its
    3.0 mm walls against the one 2.5 mm minimum in the words it always used, and
    thin_plate still fails."""

    def test_the_known_good_passes_and_thin_plate_fails(self):
        if self._skipped_here():
            return
        spec, fn = self.registry.get(self.GATE)
        host = gates_mod.GateContext(root=self.dir, out_dir=self.dir, extra={"pack_dir": PACK})
        good = gates_mod.run_gate(spec, fn, gates_mod.run_good_fixture(
            spec, fn, host, trace=None, out_dir=self.dir))
        self.assertEqual(good.outcome, "pass", good.render())
        self.assertEqual((good.measured, good.limit), (3.0, 2.5))
        self.assertTrue(good.detail.startswith("thinnest wall 3.000 mm"), good.detail)
        self.assertIn("[process_min_wall_mm]", good.detail)
        bad = gates_mod.run_gate(spec, fn, gates_mod.run_fixture(
            spec, fn, host, trace=None, out_dir=self.dir))
        self.assertEqual(bad.outcome, "fail", bad.render())
        self.assertAlmostEqual(bad.measured, 2.5 / 4, places=3)


class AMinimumPerPart(_WallCase):
    """One minimum for every mesh failed a light pipe made in SLA beside FDM shells.
    Each part now takes its own: its exact name, then a pattern, then the fallback."""

    def test_a_part_takes_its_own_minimum(self):
        if self._skipped_here():
            return
        meshes = _plates(shell=2.0, sla_light_pipe=0.8)
        v = self._run(self.GATE, meshes, MIN_WALL)
        self.assertEqual((v.outcome, v.measured, v.limit), ("fail", 0.8, 1.2), v.render())
        v = self._run(self.GATE, meshes,
                      dict(MIN_WALL, process_min_wall_mm_by_part={"sla_*": 0.6}))
        self.assertEqual((v.outcome, v.measured, v.limit), ("pass", 0.8, 0.6), v.render())
        self.assertIn("process_min_wall_mm_by_part[sla_*]", v.detail)
        self.assertEqual(self._evidence(v)["minimums"]["shell"],
                         {"mm": 1.2, "from": "process_min_wall_mm"})

    def test_a_pass_reports_the_thinnest_wall_and_names_the_nearest(self):
        """1.3 mm against 1.2 mm is nearer its limit than 0.8 mm against 0.6 mm, but
        the gate settles "minimum wall thickness": a pass reports the 0.8 mm wall.
        Until the review of C1 it reported 1.3 mm, and a claim ">= 1.0 mm" read
        that beside the 0.8 mm wall (AWallClaimReadsTheThinnestWall)."""
        if self._skipped_here():
            return
        v = self._run(self.GATE, _plates(shell=1.3, sla_light_pipe=0.8),
                      dict(MIN_WALL, process_min_wall_mm_by_part={"sla_*": 0.6}))
        self.assertEqual((v.outcome, v.measured, v.limit), ("pass", 0.8, 0.6), v.render())
        self.assertTrue(v.detail.startswith("thinnest wall 0.800 mm on sla_light_pipe"),
                        v.detail)
        self.assertIn("nearest its own minimum: 1.300 mm on shell vs 1.200 mm "
                      "[process_min_wall_mm]", v.detail)
        evidence = self._evidence(v)
        self.assertEqual((evidence["thinnest"]["part"], evidence["thinnest"]["minimum_mm"]),
                         ("sla_light_pipe", 0.6))
        self.assertEqual((evidence["nearest_its_minimum"]["part"],
                          evidence["nearest_its_minimum"]["minimum_mm"]), ("shell", 1.2))

    def test_a_fail_reports_the_wall_furthest_below_its_own_minimum(self):
        """1.1 mm against 1.2 mm fails; 0.8 mm against 0.6 mm does not. The fail
        reports the wall that failed, and pins it."""
        if self._skipped_here():
            return
        v = self._run(self.GATE, _plates(shell=1.1, sla_light_pipe=0.8),
                      dict(MIN_WALL, process_min_wall_mm_by_part={"sla_*": 0.6}))
        self.assertEqual((v.outcome, v.measured, v.limit), ("fail", 1.1, 1.2), v.render())
        self.assertTrue(v.detail.startswith("worst wall against its own minimum: 1.100 mm "
                                            "on shell"), v.detail)
        self.assertNotIn("nearest its own minimum", v.detail)
        self.assertEqual(v.locators[0].target, "shell")

    def test_an_exact_name_beats_a_pattern(self):
        if self._skipped_here():
            return
        v = self._run(self.GATE, _plates(shell=2.0, sla_light_pipe=0.8), dict(
            MIN_WALL, process_min_wall_mm_by_part={"sla_*": 0.6, "sla_light_pipe": 1.0}))
        self.assertEqual((v.outcome, v.measured, v.limit), ("fail", 0.8, 1.0), v.render())
        self.assertIn("process_min_wall_mm_by_part[sla_light_pipe]", v.detail)
        self.assertTrue(v.locators)

    def test_two_patterns_that_disagree_are_refused(self):
        """The cache digests the map with its keys sorted, so an order between
        patterns could move the verdict without moving its key."""
        if self._skipped_here():
            return
        meshes = _plates(shell=2.0, sla_light_pipe=0.8)
        v = self._run(self.GATE, meshes, dict(
            MIN_WALL, process_min_wall_mm_by_part={"sla_*": 0.6, "*_pipe": 0.7}))
        self.assertRefused(v, "different minimums")
        v = self._run(self.GATE, meshes, dict(
            MIN_WALL, process_min_wall_mm_by_part={"sla_*": 0.6, "*_pipe": 0.6}))
        self.assertEqual((v.outcome, v.limit), ("pass", 0.6), v.render())

    def test_an_entry_that_judges_no_part_is_refused(self):
        if self._skipped_here():
            return
        meshes = _plates(shell=2.0, sla_light_pipe=0.8)
        for entry, text in (({"sla_lightpipe": 0.6}, "names no part in the assembly"),
                            ({"resin_*": 0.6}, "matches no part in the assembly"),
                            ({"sla_*": 0}, "a minimum is a positive number of mm"),
                            ({"sla_*": True}, "a minimum is a positive number of mm")):
            with self.subTest(entry=entry):
                v = self._run(self.GATE, meshes,
                              dict(MIN_WALL, process_min_wall_mm_by_part=entry))
                self.assertRefused(v, text)

    def test_a_part_with_a_minimum_of_its_own_needs_no_fallback(self):
        if self._skipped_here():
            return
        meshes = _plates(shell=2.0, sla_light_pipe=0.8)
        unset = {"process_min_wall_mm": None}
        v = self._run(self.GATE, meshes, dict(
            unset, process_min_wall_mm_by_part={"shell": 1.2, "sla_*": 0.6}))
        self.assertEqual((v.outcome, v.measured, v.limit), ("pass", 0.8, 0.6), v.render())
        v = self._run(self.GATE, meshes,
                      dict(unset, process_min_wall_mm_by_part={"sla_*": 0.6}))
        self.assertTrue(v.skipped and not v.passed, v.render())
        self.assertIn("'shell'", v.skip_reason)
        v = self._run(self.GATE, meshes, unset)
        self.assertTrue(v.skipped and not v.passed, v.render())
        self.assertIn("does not define", v.skip_reason)


class AnExemptPart(_WallCase):
    """Bought cover glass is 0.5 mm because its vendor made it so. Exempt, it is
    named in the detail and not judged; it is never passed at a minimum of zero."""

    GLASS = {"cover_glass": "bought glass; its vendor sets its thickness"}

    def test_an_exempt_part_is_named_and_not_judged(self):
        if self._skipped_here():
            return
        meshes = _plates(shell=2.0, cover_glass=0.5)
        v = self._run(self.GATE, meshes, MIN_WALL)
        self.assertEqual((v.outcome, v.measured), ("fail", 0.5), v.render())
        v = self._run(self.GATE, meshes, dict(MIN_WALL, wall_exempt_parts=self.GLASS))
        self.assertEqual((v.outcome, v.measured, v.limit), ("pass", 2.0, 1.2), v.render())
        self.assertIn("exempt, not judged: cover_glass", v.detail)
        evidence = self._evidence(v)
        self.assertEqual(evidence["exempt"], self.GLASS)
        self.assertEqual([r["part"] for r in evidence["parts"]], ["shell"])

    def test_every_part_exempt_skips(self):
        if self._skipped_here():
            return
        v = self._run(self.GATE, _plates(cover_glass=0.5),
                      dict(MIN_WALL, wall_exempt_parts=self.GLASS))
        self.assertTrue(v.skipped and not v.passed, v.render())
        self.assertIn("every part is exempt", v.skip_reason)

    def test_a_silent_or_blanket_exemption_is_refused(self):
        if self._skipped_here():
            return
        meshes = _plates(shell=2.0, cover_glass=0.5)
        for params, text in (
                ({"wall_exempt_parts": {"cover_glass": " "}}, "has no reason"),
                ({"wall_exempt_parts": {"cover_*": "bought"}}, "blanket"),
                ({"wall_exempt_parts": {"*": "bought"}}, "blanket"),
                ({"wall_exempt_parts": {"glass": "bought"}}, "names no part"),
                ({"wall_exempt_parts": ["cover_glass"]}, "write {part: reason}"),
                ({"wall_exempt_parts": self.GLASS,
                  "process_min_wall_mm_by_part": {"cover_glass": 0.4}}, "Delete one"),
                # Review of C1: the pattern form was accepted, judged nothing, and the
                # 0.3 mm its author wrote for the glass was dropped with no word.
                ({"wall_exempt_parts": self.GLASS,
                  "process_min_wall_mm_by_part": {"cover_*": 0.3}},
                 "matches only exempt part(s) 'cover_glass'")):
            with self.subTest(params=params):
                self.assertRefused(self._run(self.GATE, meshes, dict(MIN_WALL, **params)),
                                   text)

    def test_a_pattern_that_also_matches_a_judged_part_governs_it(self):
        """``cover_*`` matches the exempt glass and the judged frame: the frame takes
        its 0.8 mm, and the glass stays named and not judged."""
        if self._skipped_here():
            return
        v = self._run(self.GATE, _plates(shell=2.0, cover_glass=0.5, cover_frame=0.9),
                      dict(MIN_WALL, wall_exempt_parts=self.GLASS,
                           process_min_wall_mm_by_part={"cover_*": 0.8}))
        self.assertEqual((v.outcome, v.measured, v.limit), ("pass", 0.9, 0.8), v.render())
        self.assertIn("on cover_frame", v.detail)
        self.assertIn("exempt, not judged: cover_glass", v.detail)


class ABoxHoldsAHinge(_WallCase):
    """A living hinge is thin on purpose. Lowering the door's minimum to the hinge's
    would pass a thin wall anywhere on the door; a box holds only the hinge."""

    def test_a_box_holds_a_hinge_to_its_own_minimum(self):
        if self._skipped_here():
            return
        door = {"door": _door(hinge=0.45)}
        v = self._run(self.GATE, door, MIN_WALL)
        self.assertEqual((v.outcome, v.measured, v.limit), ("fail", 0.45, 1.2), v.render())
        v = self._run(self.GATE, door, dict(MIN_WALL, wall_exceptions=[_hinge()]))
        self.assertEqual((v.outcome, v.measured, v.limit), ("pass", 0.45, 0.4), v.render())
        self.assertIn("wall_exceptions[0] box: living hinge", v.detail)
        box = self._evidence(v)["boxes"][0]
        self.assertEqual((box["entry"], box["box_mm"], box["min_mm"], box["reason"]),
                         ("wall_exceptions[0]", HINGE_BOX, 0.4, _hinge()["reason"]))
        self.assertAlmostEqual(box["thinnest_mm"], 0.45, places=3)

    def test_a_thinner_hinge_still_fails_in_its_box(self):
        if self._skipped_here():
            return
        v = self._run(self.GATE, {"door": _door(hinge=0.3)},
                      dict(MIN_WALL, wall_exceptions=[_hinge()]))
        self.assertEqual((v.outcome, v.measured, v.limit), ("fail", 0.3, 0.4), v.render())
        self.assertIn("living hinge", v.locators[0].label)

    def test_a_wall_outside_the_box_is_judged_at_the_part_minimum(self):
        """The fin is 0.8 mm: thicker than the hinge's 0.4 mm and outside its box."""
        if self._skipped_here():
            return
        v = self._run(self.GATE, {"door": _door(hinge=0.45, fin=0.8)},
                      dict(MIN_WALL, wall_exceptions=[_hinge()]))
        self.assertEqual((v.outcome, v.measured, v.limit), ("fail", 0.8, 1.2), v.render())
        self.assertIn("[process_min_wall_mm]", v.detail)
        self.assertTrue(48.0 <= v.locators[0].position[0] <= 52.0, v.locators)  # the fin

    def test_an_exception_entry_is_strict(self):
        if self._skipped_here():
            return
        door = {"door": _door(hinge=0.45)}
        hinge = _hinge()
        lid = dict(hinge, box_mm=[[27.0, -11.0, 0.5], [33.0, 11.0, 2.0]])
        for entries, extra, text in (
                ([dict(hinge, tol_mm=0.1)], {}, "unknown key(s) 'tol_mm'"),
                ([{k: v for k, v in dict(hinge, min=0.4).items() if k != "min_mm"}], {},
                 "did you mean 'min_mm'?"),
                ([{k: v for k, v in hinge.items() if k != "reason"}], {}, "has no reason"),
                ([dict(hinge, reason="")], {}, "has no reason"),
                ([dict(hinge, part="lid")], {}, "not in the assembly"),
                ([dict(hinge, box_mm=[HINGE_BOX[1], HINGE_BOX[0]])], {},
                 "low corner first"),
                ([dict(hinge, box_mm=[[100.0] * 3, [101.0] * 3])], {}, "holds no face"),
                ([dict(hinge, min_mm=-0.4)], {}, "positive number of mm"),
                ([hinge, lid], {}, "touch or overlap"),
                ([hinge], {"wall_exempt_parts": {"door": "bought"}}, "which is exempt"),
                (hinge, {}, "write a list")):
            with self.subTest(text=text):
                v = self._run(self.GATE, door,
                              dict(MIN_WALL, wall_exceptions=entries, **extra))
                self.assertRefused(v, text)


def _wall_claim(limit: float) -> Claim:
    """A claim the gate's tags cover, on the quantity it settles, in mm."""
    return Claim.from_dict({"id": "W1", "statement": "no wall is thinner than the limit",
                            "kind": "measurable", "tags": ["wall"],
                            "acceptance": {"quantity": "minimum wall thickness",
                                           "comparator": ">=", "limit": limit,
                                           "units": "mm"}})


class AWallClaimReadsTheThinnestWall(_WallCase):
    """The gate settles "minimum wall thickness" in mm, so a claim on that quantity
    is compared with its value (``claims.cross_check``). What slipped through (review
    of C1): a pass reported the wall with the lowest thickness over its own minimum.
    Beside a 0.45 mm hinge held to 0.4 mm, a 3.0 mm shell left the hinge as that wall
    and a claim ">= 1.2 mm" read Failing; thinned to 1.25 mm, the shell became that
    wall, the gate reported 1.25 mm, and the claim read Checked with the hinge still
    there. Making the design worse turned Failing into Checked."""

    def _door_and_shell(self, shell: float):
        return self._run(self.GATE,
                         {"door": _door(hinge=0.45),
                          "shell": _box((20.0, 20.0, shell), (0.0, 60.0, 0.0))},
                         dict(MIN_WALL, wall_exceptions=[_hinge()]))

    def test_a_thinner_shell_never_turns_failing_into_checked(self):
        if self._skipped_here():
            return
        claim = _wall_claim(1.2)
        for shell in (3.0, 1.25):
            with self.subTest(shell=shell):
                v = self._door_and_shell(shell)
                self.assertEqual((v.outcome, v.measured, v.limit), ("pass", 0.45, 0.4),
                                 v.render())
                self.assertEqual(claims_mod.cross_check(claim, v).state, "fails",
                                 v.render())
        self.assertIn("nearest its own minimum: 1.250 mm on shell", v.detail)

    def test_a_claim_at_the_lowest_minimum_holds(self):
        """A claim that says what is true reads it: the hinge is the thinnest wall,
        and it is above 0.4 mm."""
        if self._skipped_here():
            return
        v = self._door_and_shell(1.25)
        self.assertEqual(claims_mod.cross_check(_wall_claim(0.4), v).state, "holds",
                         v.render())

    def test_a_minimum_per_part_is_compared_at_the_thinnest_part(self):
        """The plates case: 0.8 mm held to 0.6 mm beside 1.25 mm held to 1.2 mm, and a
        claim ">= 1.0 mm". The 0.8 mm wall is there, so the claim fails."""
        if self._skipped_here():
            return
        v = self._run(self.GATE, _plates(shell=1.25, sla_light_pipe=0.8),
                      dict(MIN_WALL, process_min_wall_mm_by_part={"sla_*": 0.6}))
        self.assertEqual((v.outcome, v.measured), ("pass", 0.8), v.render())
        self.assertEqual(claims_mod.cross_check(_wall_claim(1.0), v).state, "fails",
                         v.render())


#: The declared pair every clearance case measures.
PAIR = {"clearance_pairs": [{"pair": ["a", "b"], "reason": "a runs past b"}]}


def _pair(gap: float) -> dict:
    """Two 20 mm cubes with flat faces ``gap`` mm apart along X."""
    return {"a": _box((20.0, 20.0, 20.0), (0.0, 0.0, 0.0)),
            "b": _box((20.0, 20.0, 20.0), (20.0 + gap, 0.0, 0.0))}


class ClashPassesWhatClearanceFails(_Case):
    GATE = "cad.clearance"

    def test_a_hundredth_of_a_millimetre_fails_clearance_and_passes_clash(self):
        if self._skipped_here():
            return
        meshes = _pair(0.01)
        v = self._run(self.GATE, meshes, PAIR, [_claim()])
        self.assertEqual(v.outcome, "fail", v.render())
        self.assertAlmostEqual(v.measured, 0.01, places=4)
        clash = self._run("cad.clash", meshes, PAIR)
        if not clash.skipped:
            self.assertEqual(clash.outcome, "pass", clash.render())

    def test_eight_millimetres_passes(self):
        if self._skipped_here():
            return
        v = self._run(self.GATE, _pair(8.0), PAIR, [_claim()])
        self.assertEqual(v.outcome, "pass", v.render())
        self.assertAlmostEqual(v.measured, 8.0, places=3)


class TheSearchCapIsNotAGap(_Case):
    GATE = "cad.clearance"

    def test_thirty_millimetres_reads_the_cap_and_says_so(self):
        if self._skipped_here():
            return
        meshes = _pair(30.0)
        v = self._run(self.GATE, meshes, PAIR, [_claim()])
        self.assertEqual((v.outcome, v.measured), ("pass", 10.0), v.render())
        self.assertIn("search cap", v.detail)
        v = self._run(self.GATE, meshes, PAIR, [_claim(limit=12.0)])
        self.assertEqual(v.outcome, "fail", v.render())
        self.assertIn("cannot be shown to hold", v.detail)


class AnUnweldedMeshIsRefused(_Case):
    GATE = "cad.clearance"

    def test_a_triangle_soup_is_errored(self):
        if self._skipped_here():
            return
        import trimesh                              # noqa: PLC0415 - tools present only

        box = _box((20.0, 20.0, 20.0), (0.0, 0.0, 0.0))
        soup = trimesh.Trimesh(vertices=box.triangles.reshape(-1, 3),
                               faces=[[3 * i, 3 * i + 1, 3 * i + 2] for i in range(12)],
                               process=False)
        v = self._run(self.GATE, {"a": soup, "b": _box((20.0, 20.0, 20.0), (25.0, 0, 0))},
                      PAIR, [_claim()])
        self.assertEqual(v.outcome, "error", v.render())
        self.assertIn("manifold3d built no solid", v.error)


class NoPairsIsSkipped(_Case):
    GATE = "cad.clearance"

    def test_no_pair_skips_even_with_no_clearance_claim(self):
        v = self._run(self.GATE, {}, {"clearance_pairs": []}, [])
        self.assertTrue(v.skipped and not v.passed, v.render())
        if self.available:
            self.assertIn("nothing declared", v.skip_reason)

    def test_an_entry_with_no_reason_fails(self):
        if self._skipped_here():
            return
        meshes = _pair(8.0)
        v = self._run(self.GATE, meshes, {"clearance_pairs": [{"pair": ["a", "b"]}]},
                      [_claim()])
        self.assertEqual(v.outcome, "fail", v.render())
        self.assertIn("has no reason", v.detail)


class AClearancePairStatesNoNumber(_Case):
    GATE = "cad.clearance"

    def test_a_tolerance_on_a_pair_is_refused_in_a_clearances_words(self):
        """The mating reader took ``tol_mm`` and the gate dropped it: the pair was
        judged at the claim's limit, with no word about the 0.5."""
        if self._skipped_here():
            return
        entry = {"pair": ["a", "b"], "reason": "a runs past b", "tol_mm": 0.5}
        v = self._run(self.GATE, _pair(8.0), {"clearance_pairs": [entry]}, [_claim()])
        self.assertEqual(v.outcome, "fail", v.render())
        self.assertIn("states tol_mm = 0.5", v.detail)
        v = self._run(self.GATE, _pair(8.0),
                      {"clearance_pairs": [{"pair": ["a", "*"], "reason": "x"}]}, [_claim()])
        self.assertIn("a clearance from 'anything'", v.detail)
        self.assertNotIn("touch", v.detail)


class TheKnownGoodReachesTheTool(_Case):
    GATE = "cad.clearance"

    def test_the_baseline_measures_a_pair_inside_the_search(self):
        """The baseline's one pair was 23 mm apart by box, so its known-good never
        reached manifold3d and a gate that read 0.0 for every pair it measured
        qualified. Its carriage now stands 7.0 mm off the roller."""
        if self._skipped_here():
            return
        spec, fn = self.registry.get(self.GATE)
        host = gates_mod.GateContext(root=self.dir, out_dir=self.dir, extra={"pack_dir": PACK})
        good = gates_mod.run_gate(spec, fn, gates_mod.run_good_fixture(
            spec, fn, host, trace=None, out_dir=self.dir))
        self.assertEqual(good.outcome, "pass", good.render())
        self.assertAlmostEqual(good.measured, 7.0, places=3)
        self.assertNotIn("search cap", good.detail)


class TheLimitIsTheClaims(_Case):
    GATE = "cad.clearance"

    def test_a_limit_in_micrometres_is_errored(self):
        if self._skipped_here():
            return
        meshes = _pair(8.0)
        v = self._run(self.GATE, meshes, PAIR,
                      [_claim(limit=1000.0, units="um")])
        self.assertEqual(v.outcome, "error", v.render())
        self.assertIn("'um'", v.error)


# --------------------------------------------------------------------------- #
# holdout: a published design's chamfer, a sliver face, and a seated layer
# --------------------------------------------------------------------------- #
def _knife_rim():
    """A 4 mm disc whose rim is a revolved 45 deg knife edge, the chamfer drawn in
    three strips that are not coplanar, as a CAD tessellator writes a curved
    chamfer: only the last strip shares an edge with the top face."""
    import numpy as np                              # noqa: PLC0415 - tools present only
    import trimesh                                  # noqa: PLC0415

    profile = np.array([(0.0, 0.0), (8.0, 0.0), (11.6, 3.6), (11.8, 3.81), (12.0, 4.0),
                        (0.0, 4.0)])
    return trimesh.creation.revolve(profile, sections=64)


def _sliver_box(h: float = 1e-6):
    """A 10 mm cube whose top face holds one sliver triangle 0.1 mm in from its +X
    edge: altitude ``h``, its normal tilted 60 deg toward -X by the tiny dip of its
    third vertex. Its inward ray runs into the +X wall after 0.115 mm."""
    import numpy as np                              # noqa: PLC0415 - tools present only
    import trimesh                                  # noqa: PLC0415
    from nopekit.modelio import load_path           # noqa: PLC0415

    earclip = load_path(os.path.join(REPO, "packs", "fdm-print", "selftest",
                                     "baseline_mesh.py"))._earclip
    top = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)]
    a, b = (9.9, 2.0), (9.9, 8.0)
    c = (9.9 - 0.5 * h, 5.0)
    verts = ([(x, y, 10.0) for x, y in top] + [(x, y, 0.0) for x, y in top]
             + [(a[0], a[1], 10.0), (b[0], b[1], 10.0), (c[0], c[1], 10.0 - 0.866 * h)])
    P1, P2, P3, P4, Q1, Q2, Q3, Q4, A, B, C = range(11)
    left = [P1, A, C, B, P4]
    right = [P1, P2, P3, P4, B, A]
    xy = [v[:2] for v in verts]
    faces = []
    for ring in (left, right):
        faces += [(ring[i], ring[j], ring[k]) for i, j, k in earclip([xy[n] for n in ring])]
    faces += [(A, B, C),
              (Q1, Q3, Q2), (Q1, Q4, Q3),
              (Q1, Q2, P2), (Q1, P2, P1), (Q2, Q3, P3), (Q2, P3, P2),
              (Q3, Q4, P4), (Q3, P4, P3), (Q4, Q1, P1), (Q4, P1, P4)]
    return trimesh.Trimesh(vertices=np.asarray(verts, dtype=float),
                           faces=np.asarray(faces), process=False)


class ATessellatedChamferIsNotAWall(_Case):
    """A published housing read a 0.188 mm wall: a ray from the second strip of a
    revolved 45 deg chamfer crossed the feather edge into the ledge. The strips
    were coplanar groups of their own, and only the first shared an edge with the
    ledge. A surface is a smooth patch now (``SMOOTH_PATCH_DEG``)."""

    GATE = "cad.wall_thickness"

    def test_the_second_strip_reads_the_disc_not_the_edge(self):
        if self._skipped_here():
            return
        rim = _knife_rim()
        self.assertTrue(rim.is_watertight and rim.is_volume)
        v = self._run(self.GATE, {"rim": rim}, {"process_min_wall_mm": 1.0})
        self.assertEqual(v.outcome, "pass", v.render())
        self.assertAlmostEqual(v.measured, 4.0, places=3)

    def test_a_thin_rim_on_the_same_disc_still_fails(self):
        """The patch rule removes the corner, never a wall: a 0.5 mm disc fails."""
        if self._skipped_here():
            return
        import numpy as np                          # noqa: PLC0415 - tools present only
        import trimesh                              # noqa: PLC0415

        thin = trimesh.creation.revolve(np.array([(0.0, 0.0), (12.0, 0.0), (12.0, 0.5),
                                                  (0.0, 0.5)]), sections=64)
        v = self._run(self.GATE, {"disc": thin}, {"process_min_wall_mm": 1.0})
        self.assertEqual(v.outcome, "fail", v.render())
        self.assertAlmostEqual(v.measured, 0.5, places=3)


class ASliverFaceIsNotCast(_Case):
    """A face of almost no area on a printed top read 0.126 mm where 2.57 mm and
    3.1 mm of material lay: its normal was not known to a degree, and its ray went
    sideways. A face under ``MIN_CAST_ALTITUDE_MM`` is not cast from."""

    GATE = "cad.wall_thickness"

    def test_the_sliver_is_counted_and_not_cast(self):
        if self._skipped_here():
            return
        box = _sliver_box()
        self.assertTrue(box.is_watertight and box.is_volume)
        v = self._run(self.GATE, {"box": box}, {"process_min_wall_mm": 1.0})
        self.assertEqual(v.outcome, "pass", v.render())
        self.assertAlmostEqual(v.measured, 10.0, places=3)
        self.assertIn("1 sliver face(s)", v.detail)
        self.assertEqual(self._evidence(v)["sliver_faces_not_cast"], 1)

    def test_a_well_shaped_face_of_the_same_tilt_is_still_cast(self):
        """The floor is the altitude, never the tilt: at 0.05 mm the same face is
        cast. Until round 3 its 0.125 mm reading stood and failed the cube. That ray
        crosses the cube's corner: the top face is 0.046 mm from its midpoint, the
        two faces it joins 0.054 and 0.063 mm, so it is a ray past an edge
        (AChamferedEdgeIsNotAWall). It is counted there and not as a sliver, which
        is what says the face was cast, and the cube reads its 10 mm less the
        0.013 mm the dimple tilts the top beside it."""
        if self._skipped_here():
            return
        v = self._run(self.GATE, {"box": _sliver_box(h=0.05)}, {"process_min_wall_mm": 1.0})
        self.assertEqual(v.outcome, "pass", v.render())
        self.assertAlmostEqual(v.measured, 9.987, places=3)
        evidence = self._evidence(v)
        self.assertEqual(evidence["sliver_faces_not_cast"], 0)
        self.assertEqual(evidence["edge_rays_not_counted"], 1)

    def _evidence(self, v) -> dict:
        with open(v.evidence[0], encoding="utf-8") as fh:
            return json.load(fh)


# --------------------------------------------------------------------------- #
# holdout round 3: a land between two faces, a lip, a groove, a ray along a face
# --------------------------------------------------------------------------- #
def _prism(profile, apex: int | None = None, length: float = 10.0):
    """The closed solid of a polygon in the (x, z) plane run ``length`` mm along y.

    Each side of the polygon is one strip of two triangles the length of the
    solid, as a CAD tessellator writes a flat face, so a narrow side puts face
    centres where the published meshes had them. Each end is a fan from vertex
    ``apex``, which must see every other vertex inside the polygon, or with no
    apex the ear clipping fdm-print's baseline uses (the polygon counter-clockwise)."""
    import numpy as np                              # noqa: PLC0415 - tools present only
    import trimesh                                  # noqa: PLC0415
    from nopekit.modelio import load_path           # noqa: PLC0415

    n = len(profile)
    verts = [(x, 0.0, z) for x, z in profile] + [(x, length, z) for x, z in profile]
    faces = []
    for i in range(n):
        j = (i + 1) % n
        faces += [(i, j, n + j), (i, n + j, n + i)]
    if apex is None:
        earclip = load_path(os.path.join(REPO, "packs", "fdm-print", "selftest",
                                         "baseline_mesh.py"))._earclip
        ends = earclip(list(profile))
    else:
        ends = [(apex, i, (i + 1) % n) for i in range(n) if apex not in (i, (i + 1) % n)]
    for a, b, c in ends:
        faces += [(a, c, b), (n + a, n + b, n + c)]
    mesh = trimesh.Trimesh(vertices=np.asarray(verts, dtype=float),
                           faces=np.asarray(faces), process=False)
    if mesh.volume < 0:
        mesh.invert()
    return mesh


def _land_plate():
    """A 1.4 mm plate whose edge is a 0.6 mm vertical land under a 45 deg chamfer,
    with a strip of the underside 0.015 mm wide along the land: its two face centres
    sit 0.005 and 0.01 mm from it, as a published bottom plate's did."""
    return _prism([(-20.0, 0.0), (-0.015, 0.0), (0.0, 0.0), (0.0, 0.6), (-0.8, 1.4),
                   (-20.0, 1.4)], apex=3)


def _land_rim():
    """A 1.0 mm wall standing on a 0.5 mm land, an inside 45 deg chamfer from the
    land up to the wall, and a strip of the outside face 0.3 mm tall above the land,
    as a published housing's rim."""
    return _prism([(-0.5, 0.0), (0.0, 0.0), (0.0, 0.3), (0.0, 5.0), (-1.0, 5.0),
                   (-1.0, 0.5)], apex=3)


def _chamfered_lip():
    """A lip 0.712 mm deep and 0.75 mm thick off a 0.888 mm wall, its tip a 0.25 mm
    land under a 45 deg chamfer, its underside one strip from the land to the wall,
    as a printed top's: the strip's face centres sit 0.237 and 0.475 mm from the
    land and read 0.487 and 0.725 mm."""
    return _prism([(0.0, 0.0), (0.712, 0.0), (0.712, -3.0), (1.6, -3.0), (1.6, 0.75),
                   (0.5, 0.75), (0.0, 0.25)], apex=4)


def _grooved_wall():
    """A 1.5 mm wall with a 90 deg V-groove in its inner face, 0.475 mm of wall left
    at the root, and strips of the outer face 0.2 mm tall either side of the root:
    their rays hit a flank just past the root and read 0.542 mm."""
    return _prism([(0.0, -5.0), (0.0, -0.2), (0.0, 0.0), (0.0, 0.2), (0.0, 5.0),
                   (-1.5, 5.0), (-1.5, 1.025), (-0.475, 0.0), (-1.5, -1.025),
                   (-1.5, -5.0)], apex=2)


def _leaning_block():
    """A 10 mm block whose top leans 4 deg, a 0.02 mm chamfer between the top and the
    left face (so they share no edge), and a strip of the top 0.08 mm wide beside the
    chamfer. A ray from that strip leans toward the left face and meets it 4 deg from
    grazing, 0.67 and 1.05 mm down, as a ray from a published housing's top did."""
    import math                                     # noqa: PLC0415

    def top(x):
        return 10.0 - x * math.tan(math.radians(4.0))

    return _prism([(0.0, 0.0), (10.0, 0.0), (10.0, top(10.0)), (0.1, top(0.1)),
                   (0.02, top(0.02)), (0.0, top(0.02) - 0.02)], apex=1)


def _lapped_web():
    """A Z section: a block 1.7 mm thick and a block 1.0 mm thick joined by a web
    0.4 mm thick and 1.7 mm across. The web's top is the first block's top face and
    its underside the second block's underside, two faces that each run on past it
    as one strip, so no face centre lies over the web, as on a published housing."""
    return _prism([(-10.0, 0.0), (0.0, 0.0), (0.0, 1.3), (10.0, 1.3), (10.0, 2.3),
                   (1.7, 2.3), (1.7, 1.7), (-10.0, 1.7)])


#: The box over the web, in mm. It holds one face centre, on the underside 1.6 mm
#: past the web, whose ray reads the second block's 1.0 mm.
WEB_BOX = [[-0.2, 1.0, 1.2], [4.0, 9.0, 1.8]]


def _web_rule(min_mm: float) -> dict:
    return {"process_min_wall_mm": 0.8,
            "wall_exceptions": [{"part": "lap", "box_mm": WEB_BOX, "min_mm": min_mm,
                                 "reason": "web between the two blocks, thin on purpose"}]}


def _drafted_fin(land: float = 0.3, base: float = 1.2, tall: float = 1.0,
                 step: float = 0.025):
    """A fin on a 1 mm plate, ``land`` mm across its flat top and ``base`` mm at the
    plate, ``tall`` mm high (0.3, 1.2 and 1.0: 24 deg per side), each flank cut into
    strips ``step`` mm tall, as a fine tessellation puts face centres near the top."""
    cuts = [round(step * k, 6) for k in range(1, int(round(tall / step)))]
    right = [(land / 2 + (base - land) / 2 * d / tall, 1.0 + tall - d) for d in cuts]
    left = [(-x, z) for x, z in right]
    return _prism([(-10.0, 0.0), (10.0, 0.0), (10.0, 1.0), (base / 2, 1.0)] + right[::-1]
                  + [(land / 2, 1.0 + tall), (-land / 2, 1.0 + tall)] + left
                  + [(-base / 2, 1.0), (-10.0, 1.0)])


class ABoxIsMeasuredWhereNoFaceCentreLies(_WallCase):
    """Round 2 of the held-out designs: a housing's box held a 0.4 mm web, and its
    27 rays, each from a face centre that lay past the web, read 1.0 mm. A web under
    the box's 0.36 mm minimum would have passed. In a box, rays also leave from a
    grid at half its minimum (``BOX_SPACING_OF_MIN``)."""

    def test_a_web_under_its_box_minimum_fails(self):
        if self._skipped_here():
            return
        lap = _lapped_web()
        self.assertTrue(lap.is_watertight and lap.is_volume)
        v = self._run(self.GATE, {"lap": lap}, _web_rule(0.5))
        self.assertEqual((v.outcome, v.limit), ("fail", 0.5), v.render())
        self.assertAlmostEqual(v.measured, 0.4, places=3)
        self.assertIn("more in wall_exceptions boxes", v.detail)

    def test_the_box_reports_the_web_it_holds(self):
        if self._skipped_here():
            return
        v = self._run(self.GATE, {"lap": _lapped_web()}, _web_rule(0.35))
        self.assertEqual(v.outcome, "pass", v.render())
        box = self._evidence(v)["boxes"][0]
        self.assertAlmostEqual(box["thinnest_mm"], 0.4, places=3)
        self.assertEqual(box["spacing_mm"], 0.175)
        self.assertGreater(box["rays"], 1)

    def test_a_box_too_large_to_sample_is_refused(self):
        """A box around the whole door at 0.02 mm would take millions of rays: it
        lowers the door's minimum everywhere, and is refused, named."""
        if self._skipped_here():
            return
        v = self._run(self.GATE, {"door": _door(hinge=0.45)},
                      dict(MIN_WALL, wall_exceptions=[_hinge(
                          box_mm=[[-11.0, -11.0, -2.0], [33.0, 11.0, 2.0]], min_mm=0.02)]))
        self.assertRefused(v, "holds too much of 'door' to sample")

    def test_a_box_is_counted_on_the_rays_it_casts(self):
        """Review of round 3: the budget counted the cells of each piece's bounding
        rectangle, and a triangle's piece casts from about half of them. A box over
        441 mm^2 of a plate at a 0.4 mm minimum casts about 11,000 rays and was
        refused as more than 20,000. It is sampled now; one over 900 mm^2 is not."""
        if self._skipped_here():
            return
        plate = _box((40.0, 40.0, 1.0), (0.0, 0.0, 0.0))

        def rule(half: float) -> dict:
            return {"process_min_wall_mm": 0.8, "wall_exceptions": [{
                "part": "plate", "box_mm": [[-half, -half, 0.4], [half, half, 0.6]],
                "min_mm": 0.4, "reason": "a region of the top, sampled at 0.2 mm"}]}

        v = self._run(self.GATE, {"plate": plate}, rule(10.5))
        self.assertEqual(v.outcome, "pass", v.render())
        box = self._evidence(v)["boxes"][0]
        self.assertGreater(box["rays"], 10_000)
        self.assertLess(box["rays"], 20_000)
        self.assertRefused(self._run(self.GATE, {"plate": plate}, rule(15.0)),
                           "holds too much of 'plate' to sample")

    def test_a_vertex_on_a_box_plane_keeps_its_piece(self):
        """Review of round 3: a face whose first vertex lay on a box's plane, its
        second outside, came out of the clip with that vertex twice, so the grid's
        first side had no length and the piece cast no ray. 12.5 mm^2 inside the box
        read nothing in one vertex order and 225 points in another. A box drawn at a
        feature's coordinates puts its planes through the mesh's vertices."""
        if self._skipped_here():
            return
        import numpy as np                          # noqa: PLC0415 - tools present only
        import trimesh                              # noqa: PLC0415

        box_points = self.registry.get(self.GATE)[1].__globals__["_box_points"]
        corners = np.array([(0.0, 0.0, 0.0), (-5.0, 5.0, 0.0), (5.0, 5.0, 0.0)])
        lo, hi = np.array([0.0, -1.0, -1.0]), np.array([10.0, 10.0, 1.0])
        counts = []
        for order in ([0, 1, 2], [1, 2, 0], [2, 0, 1]):
            face = trimesh.Trimesh(vertices=corners, faces=[order], process=False)
            points, faces, count = box_points(face, lo, hi, 0.25, 10_000)
            self.assertEqual(len(points), count)
            counts.append(count)
        self.assertGreater(counts[0], 0)
        self.assertEqual(len(set(counts)), 1, counts)


class AChamferedEdgeIsNotAWall(_Case):
    """Round 2 of the held-out designs: a bottom plate 1.35-1.4 mm thick read 0.605
    mm, a ray from its underside 0.005 mm in from a 0.6 mm land that ran up beside
    the land and hit the chamfer over it, and a housing's 1.0 mm wall read 0.699 mm
    across the land under it. The land kept the two faces from sharing an edge, so
    the acute-edge rule did not see the corner. A third surface nearer the ray's
    midpoint than the two it joins makes it a ray past an edge (``_edge_rays``)."""

    GATE = "cad.wall_thickness"

    def test_a_plate_edge_over_a_land_reads_the_plate(self):
        if self._skipped_here():
            return
        plate = _land_plate()
        self.assertTrue(plate.is_watertight and plate.is_volume)
        v = self._run(self.GATE, {"plate": plate}, {"process_min_wall_mm": 0.84})
        self.assertEqual(v.outcome, "pass", v.render())
        # The chamfer's ray to the underside, 1.226 mm: the land is 0.7 mm from its
        # midpoint and the underside 0.433 mm, so it is a wall. The underside's two
        # rays beside the land read 0.605 and 0.610 mm, the land 0.005 and 0.01 mm
        # from their midpoints and the faces they join 0.298 and 0.295 mm.
        self.assertAlmostEqual(v.measured, 1.226, places=3)
        self.assertIn("ray(s) past an edge or along a face not counted", v.detail)

    def test_a_rim_over_a_land_reads_the_wall(self):
        if self._skipped_here():
            return
        rim = _land_rim()
        self.assertTrue(rim.is_watertight and rim.is_volume)
        v = self._run(self.GATE, {"rim": rim}, {"process_min_wall_mm": 0.84})
        self.assertEqual(v.outcome, "pass", v.render())
        # The chamfer's ray to the outside face, 0.943 mm. The outside face's rays
        # read 0.6 and 0.7 mm across the land, the land 0.1 and 0.2 mm from their
        # midpoints and the chamfer 0.224 and 0.247 mm.
        self.assertAlmostEqual(v.measured, 0.943, places=3)

    def test_a_thin_plate_with_the_same_edge_still_fails(self):
        """The rule removes the edge, never the plate: 0.5 mm thick, a 0.2 mm land,
        fails. What it reports is the chamfer's ray to the underside, 0.424 mm: the
        underside is the nearest surface to its midpoint (0.15 mm, the land
        0.25 mm), so it is a wall, and thinner than the plate's own 0.5 mm."""
        if self._skipped_here():
            return
        thin = _prism([(-20.0, 0.0), (-0.015, 0.0), (0.0, 0.0), (0.0, 0.2), (-0.3, 0.5),
                       (-20.0, 0.5)], apex=3)
        v = self._run(self.GATE, {"plate": thin}, {"process_min_wall_mm": 0.84})
        self.assertEqual(v.outcome, "fail", v.render())
        self.assertAlmostEqual(v.measured, 0.424, places=3)


class AThinLipStillFails(_Case):
    """What the edge rule keeps. A printed top's lip is thin all the way across: its
    0.487 mm ray has the chamfer 0.172 mm from its midpoint and the land 0.237 mm,
    and it fails a 1.2 mm minimum at 0.487 mm, as before. A groove's root is a point
    of both its flanks, so the ray that hits one flank just past it ties with the
    other flank, and a tie is a wall."""

    GATE = "cad.wall_thickness"

    def test_a_lip_with_a_chamfered_tip_fails_at_its_reading(self):
        if self._skipped_here():
            return
        lip = _chamfered_lip()
        self.assertTrue(lip.is_watertight and lip.is_volume)
        v = self._run(self.GATE, {"top": lip}, {"process_min_wall_mm": 1.2})
        self.assertEqual(v.outcome, "fail", v.render())
        self.assertAlmostEqual(v.measured, 0.487, places=3)

    def test_a_groove_root_is_a_wall(self):
        if self._skipped_here():
            return
        wall = _grooved_wall()
        self.assertTrue(wall.is_watertight and wall.is_volume)
        v = self._run(self.GATE, {"wall": wall}, {"process_min_wall_mm": 1.0})
        self.assertEqual(v.outcome, "fail", v.render())
        self.assertAlmostEqual(v.measured, 0.542, places=3)


class ARayAlongAFaceIsNotAWall(_Case):
    """A published housing read 0.731 mm from a ray off a top face tilted 4 deg: it
    ran down 0.017-0.05 mm beside a vertical face and met it 3.7 deg from grazing,
    where the wall it ran beside goes on down past 1.6 mm. The face it hit was the
    nearest surface to its midpoint, so the third-surface rule kept it. A face that
    runs along the ray (``GRAZE_DEG``) makes it a ray past an edge."""

    GATE = "cad.wall_thickness"

    def test_a_ray_that_leans_into_a_face_reads_the_block(self):
        if self._skipped_here():
            return
        block = _leaning_block()
        self.assertTrue(block.is_watertight and block.is_volume)
        v = self._run(self.GATE, {"block": block}, {"process_min_wall_mm": 1.0})
        self.assertEqual(v.outcome, "pass", v.render())
        self.assertGreater(v.measured, 9.0)


class ADraftedFinIsReadBelowItsLand(_Case):
    """The cost of the edge rule, held so that it is a known case and moves only on
    purpose. A fin drafted 15-30 deg per side loses the rays just under its top land:
    the land is nearer their midpoints than the flank they hit, as a plate's land is
    beside the ray that runs up past it. A 0.3 mm land on a 1.2 mm base, 1.0 mm tall,
    is thinner than 0.45 mm for its top 0.17 mm, and the gate reads 0.495 mm, where
    the code before the edge rule read 0.423 mm (review of round 3)."""

    GATE = "cad.wall_thickness"

    def test_a_minimum_under_the_reading_passes_a_thinner_top(self):
        if self._skipped_here():
            return
        fin = _drafted_fin()
        self.assertTrue(fin.is_watertight and fin.is_volume)
        v = self._run(self.GATE, {"fin": fin}, {"process_min_wall_mm": 0.45})
        self.assertEqual(v.outcome, "pass", v.render())
        self.assertAlmostEqual(v.measured, 0.495, places=3)
        self.assertIn("ray(s) past an edge or along a face not counted", v.detail)

    def test_a_minimum_over_the_reading_still_fails(self):
        if self._skipped_here():
            return
        v = self._run(self.GATE, {"fin": _drafted_fin()}, {"process_min_wall_mm": 0.5})
        self.assertEqual(v.outcome, "fail", v.render())
        self.assertAlmostEqual(v.measured, 0.495, places=3)


class TheExemptPartsSurviveTheCap(_WallCase):
    """Review of round 3: the box and edge counts came before the exempt parts, and
    on an assembly with a box, a sliver, edge rays, a wall nearest its own minimum
    and six exempt parts the detail reached the spine's 500 characters at "exempt,
    not judge...". PACK.md says the detail names an exempt part so that a reader sees
    the gap. The counts come last now, and are all in the evidence."""

    def test_a_long_detail_still_names_the_exempt_parts(self):
        if self._skipped_here():
            return
        exempt = ("battery", "board", "cover_glass", "display", "motor", "speaker")
        meshes = {"lap": _lapped_web(), "edge": _land_plate(),
                  "button": _box((20.0, 20.0, 0.48), (0.0, 40.0, 0.0)),
                  "cube": _sliver_box()}
        for name in exempt:
            meshes[name] = _box((5.0, 5.0, 5.0), (0.0, -40.0, 0.0))
        v = self._run(self.GATE, meshes, {
            "process_min_wall_mm": 0.45,
            "wall_exempt_parts": {name: "bought, its vendor sets it" for name in exempt},
            "wall_exceptions": [{"part": "lap", "box_mm": WEB_BOX, "min_mm": 0.36,
                                 "reason": "the web between the two blocks is thin on "
                                           "purpose, so that the lap can flex"}]})
        self.assertEqual(v.outcome, "pass", v.render())
        evidence = self._evidence(v)
        self.assertGreater(evidence["box_rays"], 0)
        self.assertGreater(evidence["sliver_faces_not_cast"], 0)
        self.assertGreater(evidence["edge_rays_not_counted"], 0)
        self.assertIn("nearest its own minimum: 0.480 mm on button", v.detail)
        self.assertIn("exempt, not judged: battery, board, cover_glass, display "
                      "and 2 more", v.detail)


def _stack(overlap: float) -> dict:
    """A 40 x 40 mm plate seated on another, ``overlap`` mm into it."""
    return {"base": _box((40.0, 40.0, 10.0), (0.0, 0.0, 5.0)),
            "plate": _box((40.0, 40.0, 4.0), (0.0, 0.0, 12.0 - overlap))}


class ASeatedLayerIsNotAClash(_Case):
    """Two coincident faces left a few microns into each other share a layer whose
    volume is depth times area, and a seated flat contact over 1600 mm^2 failed
    the 0.2 mm^3 volume tolerance with nothing wrong. Measured on the shared solid,
    a layer within the depth tolerance is judged on depth, as a bonded joint is."""

    GATE = "cad.clash"

    def test_a_three_micron_layer_passes_with_nothing_declared(self):
        if self._skipped_here():
            return
        v = self._run(self.GATE, _stack(0.003))
        self.assertEqual(v.outcome, "pass", v.render())
        self.assertIn("1 seated layer(s) judged on depth", v.detail)
        with open(v.evidence[0], encoding="utf-8") as fh:
            row = json.load(fh)["pairs"][0]
        self.assertTrue(row["seated"])
        self.assertGreater(row["volume_mm3"], 0.2)

    def test_a_real_interference_of_the_same_shape_still_fails(self):
        """0.084 mm is how far a published design modelled a part into its pocket."""
        if self._skipped_here():
            return
        v = self._run(self.GATE, _stack(0.084))
        self.assertEqual(v.outcome, "fail", v.render())
        with open(v.evidence[0], encoding="utf-8") as fh:
            row = json.load(fh)["pairs"][0]
        self.assertFalse(row["seated"])

    def test_two_layers_at_two_heights_are_not_one_layer(self):
        """The bound is the whole shared solid's: two thin contacts 10 mm apart in
        height are not a layer, and stay judged on volume."""
        if self._skipped_here():
            return
        import trimesh                              # noqa: PLC0415 - tools present only

        frame = trimesh.util.concatenate([_box((40.0, 10.0, 10.0), (0.0, -15.0, 5.0)),
                                          _box((40.0, 10.0, 10.0), (0.0, 15.0, 15.0))])
        bar = trimesh.util.concatenate([_box((40.0, 10.0, 2.0), (0.0, -15.0, 10.997)),
                                        _box((40.0, 10.0, 2.0), (0.0, 15.0, 20.997))])
        v = self._run(self.GATE, {"frame": frame, "bar": bar})
        self.assertEqual(v.outcome, "fail", v.render())


if __name__ == "__main__":
    unittest.main()
