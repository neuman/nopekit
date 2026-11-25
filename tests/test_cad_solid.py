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
        for draft in (16.0, 20.0):
            with self.subTest(draft=draft):
                h = (1.2 - 0.2) / 2 / math.tan(math.radians(draft))
                xz = [(-0.6, 0), (0.6, 0), (0.1, h), (-0.1, h)]
                vertices = [(x, y, 5.0 + z) for y in (-10.0, 10.0) for x, z in xz]
                rib = trimesh.convex.convex_hull(np.array(vertices))
                block = _box((20.0, 30.0, 5.0), (0.0, 0.0, 2.5))
                mesh = trimesh.boolean.union([block, rib], engine="manifold")
                for subdivisions in range(3):
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
        a real one, and its 0.115 mm reading stands."""
        if self._skipped_here():
            return
        v = self._run(self.GATE, {"box": _sliver_box(h=0.05)}, {"process_min_wall_mm": 1.0})
        self.assertEqual(v.outcome, "fail", v.render())
        self.assertLess(v.measured, 0.2)

    def _evidence(self, v) -> dict:
        with open(v.evidence[0], encoding="utf-8") as fh:
            return json.load(fh)


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
