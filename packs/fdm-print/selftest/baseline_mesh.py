# SPDX-License-Identifier: Apache-2.0
"""The solid the pack's baseline projection describes, and the code that made it.

``selftest/baseline.json`` is a projection of a GOOD design: every gate in this
pack passes on it, which is what makes the known-bad fixtures in
``bad_params.py`` and ``bad_meshes.py`` meaningful — each one moves exactly one
thing and the gate under test flips to FAIL.

Five of the seven gates read scalars, and the JSON carries them. The other two —
``fdm.overhang`` and ``fdm.bridge_span`` — read triangles, and a JSON file cannot
hold triangles. So the projection points ``mesh_path`` at
``selftest/baseline_part.stl``, and this module is the source that generated it.
The STL is committed next to it so the gates can run with nothing else present;
re-running this module reproduces it byte-for-byte from the numbers below.

The part
--------
A conduit saddle clamp. An inverted-U body straddles a 30 mm conduit and is
through-bolted to a panel by two outward feet; the feet are drilled on assembly,
which is why the printed body carries no holes (see ``references/geometry_rules.md``
on printed hole sizes). It is printed the way it is used: feet on the bed, the
opening arching over the conduit, so the bolt feet are solid against the plate and
the outer faces come off the machine as walls rather than as stepped overhangs.

The cross-section is constant along the conduit, so the solid is a prism: one
closed outline in the (x, z) plane extruded along +y. That is not a modelling
shortcut, it is the design — a constant section is what makes the part printable
in this orientation at all.

Why it passes each mesh gate, by construction:

* **Overhang.** The only downward-facing surfaces that are not on the bed are the
  two chamfers at the top corners of the opening, at
  ``atan(CHAMFER_RUN_MM / CHAMFER_RISE_MM)`` = 39.8 deg from vertical. That is
  inside the 45 deg limit with room to spare, and it is the textbook move: a
  chamfer instead of support.
* **Bridge span.** Those chamfers also cut the flat ceiling down to
  ``OPENING_W_MM - 2 * CHAMFER_RUN_MM`` = 18 mm, which is inside the 30 mm
  bridgeable maximum and inside the 20 mm below which a bridge is reliably flat.
  The ceiling is anchored on both chamfers, so it is a real bridge and not a
  ceiling hanging off nothing.

``trimesh`` and ``numpy`` are imported inside the functions, like the rest of the
pack's mesh code, so this file can be read on a machine without them.
"""
from __future__ import annotations

import math
import os

# --------------------------------------------------------------------------- #
# the part, in millimetres
# --------------------------------------------------------------------------- #

FOOT_EXT_MM = 16.0
"""How far each bolt foot reaches outboard of the wall it carries."""

FOOT_T_MM = 4.0
"""Foot thickness. Thick enough to take a washer face without dishing."""

WALL_T_MM = 3.2
"""Side wall thickness — eight beads at the 0.4 mm nozzle. This is the
thinnest wall across the layer plane, and it is what ``part_min_wall_mm``
reports. The feet and the roof, 4 mm along the build axis, are its thinnest
floor (``part_min_floor_mm``)."""

OPENING_W_MM = 30.0
"""Clear width of the opening: the conduit it straddles, plus fit clearance."""

OPENING_H_MM = 20.0
"""Height of the vertical part of the opening, bed to the start of the chamfer."""

CHAMFER_RUN_MM = 6.0
CHAMFER_RISE_MM = 7.2
"""The 45-deg-rule chamfer at each top corner of the opening, as run and rise.
``atan(6.0 / 7.2)`` = 39.8 deg from vertical: inside the overhang limit, and it
shortens the bridge at the same time. Keep rise >= run or the face becomes an
overhang the machine cannot print."""

ROOF_T_MM = 4.0
"""Roof thickness over the opening. Also the number of solid layers the bridge
has to be built back up with, so it is not thinner than the walls."""

LENGTH_MM = 80.0
"""Length along the conduit. The clamp spreads its bolt load over this much of
the run; it is also the extrusion depth of the prism."""


def outline() -> list[tuple[float, float]]:
    """The closed cross-section in (x, z), counter-clockwise, z = 0 on the bed.

    Counter-clockwise is load-bearing: every face winding in :func:`prism` is
    derived from it, and a clockwise outline would turn the solid inside out.
    :func:`_assert_solid` catches that rather than trusting it.
    """
    x0 = 0.0                                  # outboard edge, left foot
    x1 = FOOT_EXT_MM                          # outer face, left wall
    x2 = x1 + WALL_T_MM                       # inner face, left wall
    x3 = x2 + OPENING_W_MM                    # inner face, right wall
    x4 = x3 + WALL_T_MM                       # outer face, right wall
    x5 = x4 + FOOT_EXT_MM                     # outboard edge, right foot

    z1 = FOOT_T_MM                            # top of the feet
    z2 = OPENING_H_MM                         # start of the chamfers
    z3 = z2 + CHAMFER_RISE_MM                 # the flat ceiling
    z4 = z3 + ROOF_T_MM                       # top of the roof

    return [
        (x0, 0.0),                            # left foot, on the bed
        (x2, 0.0),
        (x2, z2),                             # up the inside of the left wall
        (x2 + CHAMFER_RUN_MM, z3),            # chamfer into the ceiling
        (x3 - CHAMFER_RUN_MM, z3),            # the bridged ceiling
        (x3, z2),                             # chamfer back down
        (x3, 0.0),                            # down the inside of the right wall
        (x5, 0.0),                            # right foot, on the bed
        (x5, z1),
        (x4, z1),                             # top of the right foot
        (x4, z4),                             # up the outside of the right wall
        (x1, z4),                             # across the top of the roof
        (x1, z1),                             # down the outside of the left wall
        (x0, z1),                             # top of the left foot
    ]


# --------------------------------------------------------------------------- #
# prism construction
# --------------------------------------------------------------------------- #
def _signed_area(poly: list[tuple[float, float]]) -> float:
    n = len(poly)
    return 0.5 * sum(poly[i][0] * poly[(i + 1) % n][1] - poly[(i + 1) % n][0] * poly[i][1]
                     for i in range(n))


def _in_triangle(p, a, b, c, eps: float = 1e-12) -> bool:
    def side(u, v, w):
        return (v[0] - u[0]) * (w[1] - u[1]) - (v[1] - u[1]) * (w[0] - u[0])
    d1, d2, d3 = side(a, b, p), side(b, c, p), side(c, a, p)
    return d1 >= -eps and d2 >= -eps and d3 >= -eps


def _earclip(poly: list[tuple[float, float]]) -> list[tuple[int, int, int]]:
    """Triangulate a simple counter-clockwise polygon into index triples.

    Ear clipping rather than a library call because the pack declares ``trimesh``
    and ``numpy`` and nothing else, and a fixture that needs a dependency the pack
    does not declare is a fixture that silently stops running.

    The triangulation uses ONLY the outline's own vertices, which is the property
    that matters here: no T-junctions, so the end caps share whole edges with the
    side walls and the solid closes. ``fdm.bridge_span`` decides whether a ceiling
    edge is anchored by looking at the face on the other side of it, and a
    T-junction is an edge with no other side.
    """
    idx = list(range(len(poly)))
    out: list[tuple[int, int, int]] = []
    guard = 0
    while len(idx) > 3:
        guard += 1
        if guard > 4 * len(poly) ** 2:                  # pragma: no cover
            raise ValueError("ear clipping stalled: the outline is not simple")
        for k in range(len(idx)):
            i0, i1, i2 = idx[k - 1], idx[k], idx[(k + 1) % len(idx)]
            a, b, c = poly[i0], poly[i1], poly[i2]
            cross = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
            if cross <= 0:                              # reflex or collinear
                continue
            if any(_in_triangle(poly[j], a, b, c)
                   for j in idx if j not in (i0, i1, i2)):
                continue
            out.append((i0, i1, i2))
            idx.pop(k)
            break
        else:                                           # pragma: no cover
            raise ValueError("no ear found: the outline is not simple")
    out.append((idx[0], idx[1], idx[2]))
    return out


def prism(poly: list[tuple[float, float]], depth: float):
    """Extrude a counter-clockwise (x, z) outline along +y into a closed solid."""
    import numpy as np
    import trimesh

    if _signed_area(poly) <= 0:
        raise ValueError("outline is clockwise; every face would be wound inward")

    n = len(poly)
    verts = [(x, 0.0, z) for x, z in poly] + [(x, depth, z) for x, z in poly]
    faces: list[tuple[int, int, int]] = []

    for i in range(n):                                  # side walls
        j = (i + 1) % n
        faces.append((i, j + n, j))
        faces.append((i, i + n, j + n))
    for a, b, c in _earclip(poly):                      # the two end caps
        faces.append((a, c, b))                         # y = 0, normal -y
        faces.append((a + n, b + n, c + n))             # y = depth, normal +y

    mesh = trimesh.Trimesh(vertices=np.asarray(verts, dtype=float),
                           faces=np.asarray(faces), process=True)
    _assert_solid(mesh, "baseline saddle clamp")
    return mesh


def _assert_solid(mesh, what: str) -> None:
    """A baseline that is quietly broken proves as little as a bad control does.

    If the solid is not watertight the overhang area fraction is computed against
    a surface that does not close, and if the winding is inverted every overhang
    angle comes out mirrored — the gates would report a clean pass on a part that
    is upside down.
    """
    if not mesh.is_watertight:
        raise ValueError(f"{what} is not watertight")
    if mesh.volume <= 0:
        raise ValueError(f"{what} has non-positive signed volume ({mesh.volume:.3f}): "
                         f"its faces are wound inside out")


def build():
    """The baseline part as a trimesh solid, in its print orientation."""
    return prism(outline(), LENGTH_MM)


DEFAULT_STL = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "baseline_part.stl")


def write(path: str = DEFAULT_STL) -> str:
    """Export the solid and report the numbers the projection has to agree with."""
    mesh = build()
    mesh.export(path)
    return path


if __name__ == "__main__":                              # pragma: no cover
    mesh = build()
    mesh.export(DEFAULT_STL)
    lo, hi = mesh.bounds
    print(f"wrote {DEFAULT_STL}")
    print(f"bbox_mm          {[round(float(v), 3) for v in (hi - lo)]}")
    print(f"volume_mm3       {mesh.volume:.1f}")
    print(f"surface_area_mm2 {mesh.area:.1f}")
    print(f"chamfer          {math.degrees(math.atan2(CHAMFER_RUN_MM, CHAMFER_RISE_MM)):.1f} deg from vertical")
    print(f"flat ceiling     {OPENING_W_MM - 2 * CHAMFER_RUN_MM:.1f} mm span")
