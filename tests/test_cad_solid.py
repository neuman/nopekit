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
  a face that opposes the one the ray left, within 30 deg.
- Nothing measured a clearance. ``cad.clash`` reads a 0.01 mm gap and an 8 mm gap
  both as PASS (no shared material, as PACK.md says), and a clearance claim tagged
  ``fit`` read Checked on that pass. ``cad.clearance`` measures the gap with
  manifold3d against the claim's own limit.

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
