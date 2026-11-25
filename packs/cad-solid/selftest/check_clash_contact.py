#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Behaviour matrix for ``cad.clash``: touching, interfering, and bonded.

    python3 packs/cad-solid/selftest/check_clash_contact.py

The unittest harness can only require a gate to FAIL — that is what a negative
control is — so it exercises ``selftest/bad_meshes.py:overlapping_pair`` and
stops. Half of what this gate has to get right is the other direction: a pair
that TOUCHES must come out clear, and it must come out clear without anybody
writing an allowlist entry for it. A gate hardened against false negatives loses
that quietly, and the symptom is a page of red on a correctly built assembly,
which is the exact moment somebody reaches for a wildcard.

So the two halves are asserted together, here, on sealed fixtures:

1. **A coplanar face contact PASSES**, is recorded as ``contact`` rather than
   ``clear``, is never booleaned, and carries no reported volume. This is the
   defect that prompted the file: a bulkhead and a deck sharing one face exactly
   were reported as ``worst reported 31277.200 mm^3 ... inf mm equivalent depth
   over 0.00 mm^2``. Two solids can only share material inside the intersection of
   their bounding boxes; that box was degenerate, so the shared volume was exactly
   zero and every digit of the headline was kernel noise.
2. **A real interference still FAILS**, at the same 2 mm overlap, so (1) cannot
   have been bought by loosening anything.
3. **An interference declared BONDED still FAILS.** ``bonded_joints`` waives the
   volume tolerance and keeps the depth tolerance; a pair 2 mm into another part
   is 2 mm in whatever the declaration says.
4. **A bond-line-scale overlap PASSES when declared and FAILS when not.** Both
   halves are needed: without the second the declaration might be doing nothing,
   and a mechanism that does nothing is worse than none because people trust it.
   The overlap is 0.03 mm: thicker than the prismatic depth tolerance, thinner
   than the bonded one.
4b. **A seated layer PASSES with nothing declared**, and a real interference of the
   same shape still FAILS. Two coincident faces left 0.005 mm into each other by
   tessellation share a layer whose volume is ten times the volume tolerance; the
   layer is measured on the shared solid and is thinner than the depth tolerance,
   so the volume is waived as on a bonded joint. A 0.084 mm layer is not waived.
5. **A wildcard, a missing reason, and a sliding pair are all REFUSED**, each
   failing the gate on the declaration rather than being honoured or dropped.

Exits non-zero and names every row that came out wrong. Standalone on purpose: it
is a check on the instrument, it needs no project, and it must be runnable on the
machine where the answer is bad.
"""
from __future__ import annotations

import dataclasses
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_PACK = os.path.dirname(_HERE)
_REPO = os.path.dirname(os.path.dirname(_PACK))
for _candidate in (os.path.join(_REPO, "src"), _PACK):
    if os.path.isdir(_candidate) and _candidate not in sys.path:
        sys.path.insert(0, _candidate)


def _load_gate_module():
    """Import the pack's gate module by path, so this runs from any directory."""
    import importlib.util

    path = os.path.join(_PACK, "gates", "solid.py")
    spec = importlib.util.spec_from_file_location("cad_solid_gates_solid", path)
    if spec is None or spec.loader is None:            # pragma: no cover - packaging
        raise SystemExit(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _baseline() -> dict:
    """The pack's OWN projection. Never a host project's — see bad_meshes._sealed."""
    with open(os.path.join(_HERE, "baseline.json"), encoding="utf-8") as handle:
        loaded = json.load(handle)
    return {k: v for k, v in loaded.items() if not k.startswith("_")}


def _ctx(gates, meshes, **overrides):
    """A sealed context: the pack's baseline, this geometry, these declarations."""
    params = _baseline()
    params.update(overrides)
    return gates.GateContext(
        root=_PACK, params=params, out_dir=os.path.join(_PACK, ".selftest-out"),
        tier=3, log=lambda _m: None, extra={"meshes": meshes})


def _blocks(offset_mm):
    """Two clean 20 mm boxes, the second placed ``offset_mm`` along X.

    ``offset_mm == BOX`` is an exact coplanar face contact; anything less is that
    much interference over the full 20 x 20 mm face.
    """
    import numpy as np
    import trimesh

    box = 20.0
    a = trimesh.creation.box(extents=(box, box, box))
    transform = np.eye(4)
    transform[0, 3] = offset_mm
    b = trimesh.creation.box(extents=(box, box, box), transform=transform)
    return {"block_a": a, "block_b": b}, box


def _row(verdict_rows, a="block_a", b="block_b"):
    for row in verdict_rows:
        if {row["a"], row["b"]} == {a, b}:
            return row
    return {}


def _pairs(gates, verdict):
    """The per-pair evidence the gate just wrote, read back off disk.

    Read back rather than returned: the evidence file is what a reader and the
    site actually see, so asserting on it is asserting on the deliverable. It also
    catches a row that cannot be serialised — a non-finite number written as a
    bare `Infinity` is not JSON, and a page that cannot parse `state.json` shows
    nothing at all rather than one unmeasurable pair.
    """
    del gates
    if not verdict.evidence:
        return []
    with open(verdict.evidence[0], encoding="utf-8") as handle:
        return json.load(handle)["pairs"]


def main() -> int:                                     # noqa: C901 - a flat check list
    gates = _load_gate_module()
    problems: list[str] = []

    def check(label, condition, detail=""):
        print(f"  [{'ok  ' if condition else 'BAD '}] {label}"
              + (f" — {detail}" if detail else ""))
        if not condition:
            problems.append(f"{label}: {detail}")

    engines = gates._boolean_engines()
    if not engines:
        print("NOT EXERCISED: no boolean engine on this machine "
              f"({', '.join(n for _, n in gates._BOOLEAN_ENGINES)}). `cad.clash` "
              "correctly SKIPS here, so none of the behaviour below was tested — "
              "which is not the same as it being right. `pip install manifold3d`.")
        return 0
    print(f"boolean engine(s): {', '.join(engines)}\n")

    box = 20.0

    # -- 1. a coplanar face contact is not an interference ------------------ #
    print("1. coplanar face contact (two 20 mm boxes, exactly 20 mm apart)")
    meshes, box = _blocks(box)
    verdict = gates.clash(_ctx(gates, meshes))
    row = _row(_pairs(gates, verdict))
    check("gate passes", verdict.passed and not verdict.skipped,
          verdict.detail or verdict.skip_reason)
    check("pair is recorded as contact, not clear", row.get("verdict") == "contact",
          f"verdict={row.get('verdict')!r}")
    check("no boolean was run on it", row.get("booleaned") is False,
          f"booleaned={row.get('booleaned')!r}")
    check("no volume is claimed", row.get("volume_mm3") == 0.0,
          f"volume_mm3={row.get('volume_mm3')!r}")
    check("the touching area is reported",
          abs(float(row.get("touch_mm2") or 0.0) - box * box) < 1e-6,
          f"touch_mm2={row.get('touch_mm2')!r} vs {box * box:g}")
    check("no allowlist entry was needed",
          not any(e.get("pair") == ["block_a", "block_b"]
                  for e in _baseline().get("clash_allow", [])),
          "the baseline does not allowlist this pair")

    # -- 2. a real interference still fails --------------------------------- #
    print("\n2. genuine interference (the same boxes, 2 mm into each other)")
    meshes, _ = _blocks(box - 2.0)
    verdict = gates.clash(_ctx(gates, meshes))
    row = _row(_pairs(gates, verdict))
    check("gate fails", not verdict.passed and not verdict.skipped, verdict.detail)
    check("pair is recorded as a clash", row.get("verdict") == "clash",
          f"verdict={row.get('verdict')!r}")
    check("the shared volume is reported",
          abs(float(row.get("volume_mm3") or 0.0) - 2.0 * box * box) < 1.0,
          f"volume_mm3={row.get('volume_mm3')!r} vs {2.0 * box * box:g}")

    # -- 3. bonded does not forgive a real interference --------------------- #
    print("\n3. the same 2 mm interference, declared a bonded joint")
    bond = [{"pair": ["block_a", "block_b"], "reason": "epoxy-bonded mating face"}]
    verdict = gates.clash(_ctx(gates, meshes, bonded_joints=bond))
    row = _row(_pairs(gates, verdict))
    check("gate still fails", not verdict.passed and not verdict.skipped, verdict.detail)
    check("it fails on DEPTH, not volume", row.get("tolerance_mm3") is None
          and float(row.get("depth_mm") or 0.0) > gates.BONDED_CONTACT_DEPTH_TOL_MM,
          f"tolerance_mm3={row.get('tolerance_mm3')!r} depth_mm={row.get('depth_mm')!r}")

    # -- 3b. bonded says nothing about the kernel being trustworthy --------- #
    #
    # A declaration is a statement about the DESIGN — "these two are glued". It is
    # not a statement about the boolean, so a pair the boolean cannot answer for is
    # still a clash however it was declared. Without this, `bonded_joints` would be
    # a way to make an unmeasurable pair disappear, which is strictly worse than a
    # known overlap: the gate would be reporting confidence it does not have.
    print("\n3b. a bonded pair whose geometry is not a volume")
    meshes, _ = _blocks(box)
    broken = meshes["block_b"].copy()
    faces = broken.faces.copy()
    faces[0] = faces[0][::-1]              # watertight, consistently wound no longer
    broken.faces = faces
    meshes["block_b"] = broken
    verdict = gates.clash(_ctx(gates, meshes, bonded_joints=bond))
    row = _row(_pairs(gates, verdict))
    check("gate still fails", not verdict.passed and not verdict.skipped, verdict.detail)
    check("no volume is claimed for it", row.get("volume_mm3") is None,
          f"volume_mm3={row.get('volume_mm3')!r}")

    # -- 4. a bond-line overlap: declared passes, undeclared fails ---------- #
    #
    # 0.03 mm over the full 20 x 20 mm face: 12 mm^3, sixty times the prismatic
    # VOLUME tolerance and three fifths of the bonded DEPTH tolerance. It was
    # 0.01 mm, exactly the prismatic depth tolerance, until a seated layer that
    # thin passed with nothing declared (4b): the declaration is now for a layer
    # thicker than tessellation noise between flat faces and within the bonded
    # tolerance, which is what a bond line between two tessellations is.
    print("\n4. a bond-line-scale overlap (0.03 mm over the whole 20 x 20 mm face)")
    meshes, _ = _blocks(box - 0.03)
    undeclared = gates.clash(_ctx(gates, meshes))
    declared = gates.clash(_ctx(gates, meshes, bonded_joints=bond))
    row = _row(_pairs(gates, declared))
    check("fails when NOT declared", not undeclared.passed and not undeclared.skipped,
          undeclared.detail)
    check("passes when declared bonded", declared.passed and not declared.skipped,
          declared.detail)
    check("the shared volume is still reported, just not judged",
          isinstance(row.get("volume_mm3"), float) and row.get("volume_mm3") > 1.0
          and row.get("tolerance_mm3") is None,
          f"volume_mm3={row.get('volume_mm3')!r} tolerance_mm3={row.get('tolerance_mm3')!r}")
    check("the reason is carried into the evidence",
          bool(row.get("bond_reason")), f"bond_reason={row.get('bond_reason')!r}")

    # -- 4b. a seated layer passes undeclared; a real one does not ---------- #
    print("\n4b. a seated layer (0.005 mm over the whole face) and a 0.084 mm one")
    meshes, _ = _blocks(box - 0.005)
    verdict = gates.clash(_ctx(gates, meshes))
    row = _row(_pairs(gates, verdict))
    check("a 0.005 mm layer passes with nothing declared",
          verdict.passed and not verdict.skipped, verdict.detail)
    check("it is recorded as a seated layer, its volume over the tolerance",
          row.get("seated") is True and float(row.get("volume_mm3") or 0.0) > 0.2,
          f"seated={row.get('seated')!r} volume_mm3={row.get('volume_mm3')!r}")
    meshes, _ = _blocks(box - 0.084)
    verdict = gates.clash(_ctx(gates, meshes))
    row = _row(_pairs(gates, verdict))
    check("a 0.084 mm layer still fails", not verdict.passed and not verdict.skipped,
          verdict.detail)
    check("and is not called seated", row.get("seated") is False,
          f"seated={row.get('seated')!r} layer_mm={row.get('layer_mm')!r}")

    # -- 5. the three refusals ---------------------------------------------- #
    print("\n5. declarations the gate must refuse")
    meshes, _ = _blocks(box)          # geometry that does NOT interfere
    for label, entries in (
        ("a wildcard", [{"pair": ["block_a", "*"], "reason": "bonded assembly"}]),
        ("no reason", [{"pair": ["block_a", "block_b"], "reason": "  "}]),
        ("a sliding pair", [{"pair": ["carriage", "housing"], "reason": "bonded in"}]),
        ("also allowlisted", [{"pair": ["cover", "housing"], "reason": "bonded rim"}]),
    ):
        verdict = gates.clash(_ctx(gates, meshes, bonded_joints=entries))
        check(f"refuses {label}",
              not verdict.passed and not verdict.skipped
              and verdict.units == "refused entries",
              verdict.detail or verdict.skip_reason)

    # -- 6. the fixtures the pack ships, run as the harness runs them ------- #
    print("\n6. the shipped fixtures")
    import bad_meshes

    base = gates.GateContext(root=_PACK, params=_baseline(),
                             out_dir=os.path.join(_PACK, ".selftest-out"), tier=3,
                             log=lambda _m: None, extra={})
    for name, must_pass in (("coplanar_touch_pair", True),
                            ("overlapping_pair", False),
                            ("bonded_over_interference", False),
                            ("bonded_wildcard", False),
                            ("bonded_sliding_fit", False)):
        fixture_ctx = getattr(bad_meshes, name)(dataclasses.replace(base))
        verdict = gates.clash(fixture_ctx)
        ok = (verdict.passed if must_pass else not verdict.passed) and not verdict.skipped
        check(f"{name} {'passes' if must_pass else 'fails'}", ok,
              verdict.detail or verdict.skip_reason)

    print()
    if problems:
        print(f"FAILED — {len(problems)} wrong answer(s):")
        for line in problems:
            print(f"  * {line}")
        return 1
    print("OK — touching is not interfering, interference still fails, and a bonded "
          "declaration waives the volume tolerance without waiving the depth one")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
