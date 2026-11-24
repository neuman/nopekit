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

    def test_the_worst_wall_for_its_minimum_is_reported_not_the_thinnest(self):
        """1.3 mm against 1.2 mm is nearer its limit than 0.8 mm against 0.6 mm."""
        if self._skipped_here():
            return
        v = self._run(self.GATE, _plates(shell=1.3, sla_light_pipe=0.8),
                      dict(MIN_WALL, process_min_wall_mm_by_part={"sla_*": 0.6}))
        self.assertEqual((v.outcome, v.measured, v.limit), ("pass", 1.3, 1.2), v.render())
        self.assertIn("on shell", v.detail)
        self.assertIn("worst wall against its own minimum", v.detail)

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
                  "process_min_wall_mm_by_part": {"cover_glass": 0.4}}, "Delete one")):
            with self.subTest(params=params):
                self.assertRefused(self._run(self.GATE, meshes, dict(MIN_WALL, **params)),
                                   text)


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


if __name__ == "__main__":
    unittest.main()
