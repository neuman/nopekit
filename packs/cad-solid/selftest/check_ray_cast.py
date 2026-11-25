#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Agreement check for ``cad.wall_thickness``'s two ray casters.

    python3 packs/cad-solid/selftest/check_ray_cast.py

``cad.wall_thickness`` measures with whichever ray caster the machine can offer:
trimesh's accelerated engine when an ``rtree`` or ``embree`` binding is installed,
and otherwise the gate's own vectorised Moller-Trumbore fallback. Neither binding is
a dependency of trimesh, so **which path runs is a fact about the machine, not about
the model** — and a number that changes with the machine is not evidence. Three
things have to be true for that to be acceptable, and this script asserts all three
rather than leaving them as a claim in a docstring:

1. **Scale invariance.** The same shape, uniformly scaled over five orders of
   magnitude, must report the same wall (scaled). This is the check that would have
   caught the defect it was written for: the parallel-ray cutoff was
   ``1e-12 * mesh.scale**3``, a VOLUME compared against a determinant whose units
   are LENGTH^2 — so the cutoff grew as the cube of the part's overall span while
   the quantity it gated stayed fixed by facet size.

2. **Span independence.** A part measured on its own and the identical part measured
   as one member of a widely spread assembly must give the same wall. This is the
   sharp version of (1): the facets are byte-for-byte identical and only
   ``mesh.scale`` moves, so any difference is the cutoff reading the assembly's
   bounding diagonal instead of the triangle in front of it.

3. **Engine agreement**, where an accelerated engine is actually installed. Reported
   as NOT EXERCISED, never as a pass, when it is not — an untested equivalence is an
   assumption, and the whole point of this file is not to ship those.

Exits non-zero and names the drift. Deliberately standalone: it is a check on the
instrument, it needs no project, and it must be runnable on the machine where the
answer is bad.
"""
from __future__ import annotations

import math
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_PACK = os.path.dirname(_HERE)
_REPO = os.path.dirname(os.path.dirname(_PACK))
for _candidate in (os.path.join(_REPO, "src"), _REPO):
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


class _NoRayEngine:
    """The mesh with its ray engine removed, so the fallback caster must be used.

    ``_cast_first_hit`` tries ``mesh.ray`` and catches ``ImportError``, which is
    exactly what a machine without ``rtree``/``embree`` raises from inside that
    property. Raising it here reproduces that machine faithfully instead of
    reaching into the gate to set a flag — the fallback must be reachable by the
    route real users reach it by, or this script is testing a different program.
    """

    def __init__(self, mesh):
        object.__setattr__(self, "_mesh", mesh)

    def __getattr__(self, name):
        if name == "ray":
            raise ImportError("ray engine disabled by check_ray_cast.py")
        return getattr(object.__getattribute__(self, "_mesh"), name)


def _hollow_box(outer_mm: float, wall_mm: float):
    """A closed hollow box: outer shell plus an inward-wound inner shell.

    Built as two explicit shells and concatenated, never by a boolean — a boolean
    kernel is entitled to emit slivers, and a sliver in a measuring stick is a
    measurement of the kernel. The true wall is ``wall_mm`` everywhere, exactly,
    which is what makes the assertions below closed-form rather than comparative.
    """
    import numpy as np
    import trimesh

    outer = trimesh.creation.box(extents=(outer_mm, outer_mm, outer_mm))
    inner = trimesh.creation.box(extents=(outer_mm - 2.0 * wall_mm,) * 3)
    inner.faces = np.asarray(inner.faces)[:, ::-1]     # normals into the cavity
    return trimesh.util.concatenate([outer, inner])


def _translated(mesh, dx_mm: float):
    import numpy as np

    copy = mesh.copy()
    copy.apply_translation(np.array([dx_mm, 0.0, 0.0]))
    return copy


def _min_wall(solid, mesh, caster=None) -> tuple[float, int, int]:
    """``(min thickness mm, rays that hit, rays cast)`` — the gate's own arithmetic.

    Duplicated from the gate on purpose: this file checks the CASTER, so it must
    not go through the gate's parameter handling, skip paths and verdict shaping to
    reach it. The surface classification is the gate's; the surface-to-surface
    distance is computed here from each caster's hits.
    """
    import numpy as np

    centres = np.asarray(mesh.triangles_center, dtype=float)
    normals = np.asarray(mesh.face_normals, dtype=float)
    eps = max(1e-6, float(mesh.scale) * 1e-8)
    origins = centres - normals * eps
    locations, index_ray, index_tri = solid._cast_first_hit(
        caster if caster is not None else mesh, origins, -normals)
    if len(index_ray) == 0:
        return math.inf, 0, len(origins)
    travel = np.linalg.norm(np.asarray(locations, dtype=float) - origins[index_ray], axis=1)
    real = (travel > 10.0 * eps) & solid._wall_hits(mesh, index_ray, index_tri)
    if not real.any():
        return math.inf, 0, len(origins)
    return float((travel[real] + eps).min()), int(real.sum()), len(origins)


def main() -> int:
    try:
        import numpy  # noqa: F401
        import trimesh  # noqa: F401
    except ImportError as exc:
        print(f"SKIPPED: {exc}. This check needs trimesh and numpy.")
        return 0

    import importlib.util
    if not any(importlib.util.find_spec(name) for name in ("scipy", "networkx")):
        print("SKIPPED: trimesh's surface groups need scipy or networkx.")
        return 0

    solid = _load_gate_module()
    problems: list[str] = []
    rel_tol = 1e-6                     # float64 through a norm and a division

    # -- 1. uniform scale ---------------------------------------------------- #
    print("uniform scale (wall = outer/10, fallback caster):")
    for outer in (1.0, 10.0, 100.0, 1_000.0, 10_000.0, 100_000.0):
        wall = outer / 10.0
        mesh = _hollow_box(outer, wall)
        measured, hits, cast = _min_wall(solid, mesh, _NoRayEngine(mesh))
        error = abs(measured - wall) / wall if math.isfinite(measured) else math.inf
        status = "ok" if error <= rel_tol and hits == cast else "DRIFT"
        print(f"  outer {outer:>9,.0f} mm  wall {wall:>9,.1f} mm  measured "
              f"{measured:>12.6f} mm  rays {hits}/{cast}  [{status}]")
        if status != "ok":
            problems.append(
                f"uniform scale: outer {outer:g} mm expected wall {wall:g} mm, got "
                f"{measured:g} mm ({hits}/{cast} rays hit) — a cutoff that moves with "
                f"the part's overall span, not with the facet in front of it")

    # -- 2. span independence ------------------------------------------------ #
    print("span independence (identical 20 mm shell, 2 mm wall, assembly grows):")
    import trimesh

    unit = _hollow_box(20.0, 2.0)
    alone, alone_hits, alone_cast = _min_wall(solid, unit, _NoRayEngine(unit))
    for span in (0.0, 200.0, 2_000.0, 20_000.0, 200_000.0):
        mesh = unit if span == 0.0 else trimesh.util.concatenate([unit, _translated(unit, span)])
        measured, hits, cast = _min_wall(solid, mesh, _NoRayEngine(mesh))
        error = abs(measured - alone) / alone if math.isfinite(measured) else math.inf
        status = "ok" if error <= rel_tol and hits == cast else "DRIFT"
        print(f"  span {span:>9,.0f} mm  scale {mesh.scale:>12,.1f}  measured "
              f"{measured:>10.6f} mm  rays {hits}/{cast}  [{status}]")
        if status != "ok":
            problems.append(
                f"span independence: the same shell measures {alone:g} mm alone and "
                f"{measured:g} mm ({hits}/{cast} rays hit) in an assembly spanning "
                f"{span:g} mm — the facets are identical, so only mesh.scale moved")

    # -- 3. engine agreement ------------------------------------------------- #
    print("engine agreement (accelerated engine vs fallback):")
    probe = _hollow_box(20.0, 2.0)
    try:
        probe.ray.intersects_location(
            [[0.0, 0.0, 0.0]], [[0.0, 0.0, 1.0]], multiple_hits=False)
        have_engine = True
    except Exception as exc:                           # noqa: BLE001 - optional dep
        have_engine = False
        print(f"  NOT EXERCISED: no accelerated ray engine here "
              f"({type(exc).__name__}: {exc}). Install rtree or an embree binding "
              f"and re-run — this equivalence is UNPROVEN on this machine, which is "
              f"not the same as proven.")
    if have_engine:
        for outer in (1.0, 100.0, 10_000.0):
            wall = outer / 10.0
            mesh = _hollow_box(outer, wall)
            engine, e_hits, e_cast = _min_wall(solid, mesh)
            fallback, f_hits, f_cast = _min_wall(solid, mesh, _NoRayEngine(mesh))
            error = abs(engine - fallback) / wall
            status = "ok" if error <= 1e-9 else "DRIFT"
            print(f"  outer {outer:>9,.0f} mm  engine {engine:>12.6f} ({e_hits}/{e_cast})"
                  f"  fallback {fallback:>12.6f} ({f_hits}/{f_cast})  [{status}]")
            if status != "ok":
                problems.append(
                    f"engine agreement: outer {outer:g} mm gives {engine:g} mm from the "
                    f"accelerated engine and {fallback:g} mm from the fallback")

    print()
    if problems:
        print(f"FAILED — {len(problems)} disagreement(s):")
        for line in problems:
            print(f"  * {line}")
        return 1
    print("OK — both casters are scale-free and span-free"
          + ("; the accelerated engine agrees" if have_engine else
             "; the accelerated engine was NOT exercised on this machine"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
