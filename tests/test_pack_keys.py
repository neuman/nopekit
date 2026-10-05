# SPDX-License-Identifier: Apache-2.0
"""Two packs, one word, two meanings — and the machinery that keeps them apart.

`cad-solid` reads `bbox_mm` as the ASSEMBLY envelope. `fdm-print` reads it as ONE
PART in print orientation. Both ship in the default set, and every mechanical
project has an assembly and printed parts, so on a real project the bare key had
one meaning and two readers:

    [FAIL] fdm.bed_fit : 480x186x87 mm vs 208x208 usable ...   # the boat, on a bed
    [skip] cad.bounding : bbox_mm is absent                    # the other choice

Three things fix that and each one is asserted here:

1. `GateContext.param` resolves the PACK-SCOPED spelling first, so one projection
   can carry `cad.bbox_mm` and `fdm.bbox_mm` and each gate reads the one it means.
2. The resolution ORDER is fixed and documented — scoped sweep before bare sweep —
   rather than discovered by experiment, which is how `bbox_mm before bbox` was
   found the first time.
3. `packs.key_collisions` DIFFS the installed packs' vocabularies so the clash is
   reported by `nopekit doctor` instead of by a verdict about the wrong object.

Each check keeps a negative control alongside it, because a test that can only pass
proves as little as a gate that can only pass: the collision detector is asserted
to stay SILENT on two packs that describe a key identically, and the scoped lookup
is asserted to fall back when no scoped key is published.

Run:  PYTHONPATH=src python3 -m unittest discover -s tests -v
"""
from __future__ import annotations

import dataclasses
import json
import os
import shutil
import tempfile
import unittest

from nopekit import gates as gates_mod
from nopekit import packs as packs_mod
from nopekit.models import Ledger, ProjectMeta

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACKS_DIR = os.path.join(REPO, "packs")

#: The projection the friction log could not write: an assembly AND a printed part,
#: both stating an extent, disambiguated only by scope. 480 mm of boat, 190 mm of
#: the widest part that goes on the bed.
ASSEMBLY_BBOX = [480.0, 186.0, 87.0]
PART_BBOX = [190.0, 186.0, 77.0]


def _ctx(params: dict) -> gates_mod.GateContext:
    return gates_mod.GateContext(
        root=REPO, ledger=Ledger(meta=ProjectMeta(name="scoped")), model=None,
        params=params, out_dir=os.path.join(tempfile.gettempdir(), "nopekit-keys"),
        tier=0, log=lambda _m: None, extra={})


def _registry(*names: str) -> gates_mod.Registry:
    registry = gates_mod.Registry()
    for name in names:
        packs_mod.load_gates(name, registry, root=REPO)
    return registry


def _run(registry: gates_mod.Registry, gate_id: str, ctx: gates_mod.GateContext):
    spec, fn = registry.get(gate_id)
    return gates_mod.run_gate(spec, fn, ctx)


class ScopedLookup(unittest.TestCase):
    """`ctx.param` resolves `<scope>.<key>` before the bare key."""

    def test_scope_comes_from_the_gate_id(self):
        self.assertEqual(gates_mod.scope_of("fdm.bed_fit"), "fdm")
        self.assertEqual(gates_mod.scope_of("cad.bounding"), "cad")
        self.assertEqual(gates_mod.scope_of("envelope"), "",
                         "an undotted id has no namespace and must not invent one")

    def test_scoped_key_wins_and_bare_key_still_works(self):
        ctx = _ctx({"bbox_mm": ASSEMBLY_BBOX, "fdm.bbox_mm": PART_BBOX})
        ctx = dataclasses.replace(ctx, key_scope="fdm", pack="fdm-print")
        self.assertEqual(ctx.param("bbox_mm"), PART_BBOX)
        self.assertEqual(ctx.param("bbox_mm", scope=None), ASSEMBLY_BBOX,
                         "scope=None must read the bare key deliberately")
        # negative control: with nothing scoped published, the bare key is the answer
        bare = dataclasses.replace(ctx, params={"bbox_mm": ASSEMBLY_BBOX})
        self.assertEqual(bare.param("bbox_mm"), ASSEMBLY_BBOX)

    def test_nested_group_is_the_same_as_a_dotted_key(self):
        ctx = _ctx({"bbox_mm": ASSEMBLY_BBOX, "fdm": {"bbox_mm": PART_BBOX}})
        ctx = dataclasses.replace(ctx, key_scope="fdm")
        self.assertEqual(ctx.param("bbox_mm"), PART_BBOX)

    def test_family_order_is_every_scoped_then_every_bare(self):
        """The documented order, and the one a user had to discover by experiment.

        A scoped key beats an unscoped one whatever its rank in the family — it is
        the only one of the two that is a statement about THIS pack.
        """
        ctx = dataclasses.replace(
            _ctx({"bbox": [1.0, 1.0, 1.0], "fdm.bbox_mm": PART_BBOX}), key_scope="fdm")
        value, key = ctx.first_pack_param_named(("bbox_mm", "bbox"))
        self.assertEqual((value, key), (PART_BBOX, "fdm.bbox_mm"))

        # negative control: no scoped key -> declared order decides, first spelling wins
        ctx = dataclasses.replace(
            _ctx({"bbox": [1.0, 1.0, 1.0], "bbox_mm": ASSEMBLY_BBOX}), key_scope="fdm")
        value, key = ctx.first_pack_param_named(("bbox_mm", "bbox"))
        self.assertEqual((value, key), (ASSEMBLY_BBOX, "bbox_mm"))

    def test_run_gate_stamps_the_scope_a_caller_cannot(self):
        """One context is handed to forty gates from five packs; each must read its own."""
        registry = _registry("cad-solid", "fdm-print")
        seen: dict[str, str] = {}
        for gate_id in ("cad.bounding", "fdm.bed_fit"):
            spec, _fn = registry.get(gate_id)
            gates_mod.run_gate(spec, lambda c, g=gate_id: seen.setdefault(g, c.key_scope)
                               or {"passed": True}, _ctx({}))
        self.assertEqual(seen, {"cad.bounding": "cad", "fdm.bed_fit": "fdm"})


class TwoPacksOneProjection(unittest.TestCase):
    """The end of the friction log's section 5, asserted on the shipped packs."""

    def test_assembly_and_part_are_both_judged_from_one_projection(self):
        registry = _registry("cad-solid", "fdm-print")
        ctx = _ctx({
            "cad.bbox_mm": ASSEMBLY_BBOX,
            "cad.bbox_limit_mm": [560.0, 230.0, 170.0],
            "fdm.bbox_mm": PART_BBOX,
            "bed_x_mm": 220.0, "bed_y_mm": 220.0, "bed_z_mm": 250.0, "brim_mm": 6.0,
        })
        envelope = _run(registry, "cad.bounding", ctx)
        bed = _run(registry, "fdm.bed_fit", ctx)
        self.assertTrue(envelope.passed and not envelope.skipped, envelope.detail)
        self.assertTrue(bed.passed and not bed.skipped, bed.detail or bed.skip_reason)
        self.assertIn("480", envelope.detail, "the envelope gate must see the ASSEMBLY")
        self.assertIn("190", bed.detail, "the bed gate must see the PART")

    def test_the_primary_keys_say_which_object_without_any_scope(self):
        registry = _registry("cad-solid", "fdm-print")
        ctx = _ctx({
            "assembly_bbox_mm": ASSEMBLY_BBOX,
            "assembly_bbox_limit_mm": [560.0, 230.0, 170.0],
            "part_bbox_mm": PART_BBOX,
            "bed_x_mm": 220.0, "bed_y_mm": 220.0, "bed_z_mm": 250.0, "brim_mm": 6.0,
        })
        envelope = _run(registry, "cad.bounding", ctx)
        bed = _run(registry, "fdm.bed_fit", ctx)
        self.assertIn("480", envelope.detail)
        self.assertIn("190", bed.detail)

    def test_the_old_collision_is_what_it_always_was_without_the_fix(self):
        """The negative control for the whole mechanism.

        One bare `bbox_mm` carrying the assembly is still read by the bed-fit gate
        as a part, and still fails — a 480 mm assembly does not fit a 220 mm bed.
        That is the behaviour the scoped keys exist to make unnecessary, and it has
        to stay reachable or the tests above are proving nothing.
        """
        registry = _registry("fdm-print")
        bed = _run(registry, "fdm.bed_fit", _ctx({
            "bbox_mm": ASSEMBLY_BBOX,
            "bed_x_mm": 220.0, "bed_y_mm": 220.0, "bed_z_mm": 250.0, "brim_mm": 6.0,
        }))
        self.assertFalse(bed.passed)
        self.assertFalse(bed.skipped)


class CollisionsAreDetected(unittest.TestCase):
    """`nopekit doctor`'s diff of the installed packs' key vocabularies."""

    def test_the_shipped_collision_is_reported_with_both_packs_named(self):
        found = {c.key: c for c in
                 packs_mod.key_collisions(["cad-solid", "fdm-print"], REPO)}
        self.assertIn("bbox_mm", found,
                      "the collision the friction log found must be reported")
        clash = found["bbox_mm"]
        self.assertEqual(sorted(clash.packs), ["cad-solid", "fdm-print"])
        self.assertEqual(clash.scoped["cad-solid"], "cad.bbox_mm")
        self.assertEqual(clash.scoped["fdm-print"], "fdm.bbox_mm")
        self.assertTrue(clash.meanings["cad-solid"] and clash.meanings["fdm-print"],
                        "a collision report must carry what each pack means by it")
        self.assertNotEqual(clash.primary["cad-solid"], clash.primary["fdm-print"])

    def test_live_says_whether_this_project_publishes_the_ambiguous_key(self):
        latent = packs_mod.key_collisions(["cad-solid", "fdm-print"], REPO,
                                          projection_keys=["something_else"])
        live = packs_mod.key_collisions(["cad-solid", "fdm-print"], REPO,
                                        projection_keys=["bbox_mm"])
        self.assertFalse(any(c.live for c in latent))
        self.assertTrue(any(c.live and c.key == "bbox_mm" for c in live))

    def test_one_pack_alone_collides_with_nothing(self):
        """Negative control: the detector must not invent a clash out of one pack."""
        self.assertEqual(packs_mod.key_collisions(["fdm-print"], REPO), [])

    def test_packs_that_agree_are_not_reported(self):
        """Negative control: identical declarations are not a collision.

        A copy of a pack under another name reads every key exactly as the original
        does. Reporting that would make the check noise, and a noisy check is one
        people stop reading — which is the failure mode the friction log describes
        for every other warning it met.
        """
        tmp = tempfile.mkdtemp()
        try:
            twin_dir = os.path.join(tmp, "fdm-twin")
            shutil.copytree(os.path.join(PACKS_DIR, "fdm-print"), twin_dir,
                            ignore=shutil.ignore_patterns("__pycache__"))
            manifest_path = os.path.join(twin_dir, "pack.json")
            with open(manifest_path, "r", encoding="utf-8") as handle:
                manifest = json.load(handle)
            manifest["name"] = "fdm-twin"
            with open(manifest_path, "w", encoding="utf-8") as handle:
                json.dump(manifest, handle)
            previous = os.environ.get(packs_mod.PACK_PATH_ENV)
            os.environ[packs_mod.PACK_PATH_ENV] = os.pathsep.join(
                [tmp, os.path.join(REPO, "packs")])
            try:
                twin = packs_mod.key_collisions(["fdm-print", "fdm-twin"], REPO)
            finally:
                if previous is None:
                    os.environ.pop(packs_mod.PACK_PATH_ENV, None)
                else:
                    os.environ[packs_mod.PACK_PATH_ENV] = previous
            self.assertEqual(
                [c.key for c in twin], [],
                "two packs that describe a key identically do not collide")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class AxisNamesAreRead(unittest.TestCase):
    """`load_axis: "z"` is what a person writes, and it used to read as absent."""

    def test_a_named_axis_is_measured_and_a_missing_one_still_skips(self):
        registry = _registry("fdm-print")
        base = {"utilisation": 0.62, "utilisation_kind": "stress"}
        named = _run(registry, "fdm.layer_alignment",
                     _ctx({**base, "load_axis": "z", "build_axis": "z"}))
        self.assertFalse(named.skipped,
                         f"a named axis must be read, not reported absent: {named.skip_reason}")

        absent = _run(registry, "fdm.layer_alignment", _ctx(dict(base)))
        self.assertTrue(absent.skipped)
        self.assertIn("load_axis", absent.skip_reason)

        unreadable = _run(registry, "fdm.layer_alignment",
                          _ctx({**base, "load_axis": "diagonal"}))
        self.assertTrue(unreadable.skipped)
        self.assertIn("not a direction", unreadable.skip_reason,
                      "present-and-unreadable must not read as 'you did not set it'")


if __name__ == "__main__":
    unittest.main(verbosity=2)
