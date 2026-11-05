# SPDX-License-Identifier: Apache-2.0
"""Input validation needs no mesh library or solver."""
import math


def case(ctx):
    p = ctx.param("simp_case")
    if not isinstance(p, dict):
        # ParamTrace is a Mapping, not necessarily a dict.
        from collections.abc import Mapping
        if not isinstance(p, Mapping):
            raise ValueError("simp_case must be an object")
    p = dict(p)
    expected = {"mesh", "young_mpa", "poisson", "mesh_mm", "support", "loads", "stress_region"}
    if set(p) != expected:
        raise ValueError(f"simp_case keys must be {sorted(expected)}")
    if not isinstance(p["mesh"], str) or not p["mesh"]:
        raise ValueError("mesh must name an exported STL")
    for key in ("young_mpa", "mesh_mm"):
        if not number(p[key]) or p[key] <= 0:
            raise ValueError(f"{key} must be finite and positive")
    if not number(p["poisson"]) or not -1 < p["poisson"] < 0.5:
        raise ValueError("poisson must lie strictly between -1 and 0.5")
    box(p["support"])
    box(p["stress_region"])
    if not p["loads"]:
        raise ValueError("at least one load patch is required")
    for load in p["loads"]:
        if set(load) != {"box", "force_n"}:
            raise ValueError("each load needs box and force_n")
        box(load["box"])
        v = load["force_n"]
        if len(v) != 3 or not all(number(x) for x in v) or not any(v):
            raise ValueError("force_n must be a finite nonzero 3-vector")
    return p


def number(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def box(b):
    if len(b) != 2 or any(len(v) != 3 for v in b):
        raise ValueError("box must be [[xmin,ymin,zmin],[xmax,ymax,zmax]]")
    if not all(number(x) for v in b for x in v) or any(a > z for a, z in zip(*b)):
        raise ValueError("box endpoints must be finite and ordered")


