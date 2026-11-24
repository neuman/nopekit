# SPDX-License-Identifier: Apache-2.0
"""Build the two scenes ``ergo.layout_agrees`` is demonstrated on, from the baseline.

    python3 packs/hand-ergonomics/selftest/make_handheld_glb.py

writes, beside this file:

* ``meshes/handheld.glb``: the known-good scene. The baseline's body, a handle
  under each grip at the declared diameter, and one cap per declared control whose
  top centre lies exactly at its ``at``, pressed along its ``normal``.
* ``meshes/handheld_stale.glb``: the known-bad scene. The same, with btn_y's cap
  drawn :data:`STALE_SHIFT_MM` farther up the face, away from the right wrist: the
  CAD one revision ahead of the declared layout. The reach gate reads 97.96% of
  women on the declared layout; the drawn design reaches 94.33%, under the
  baseline claim's 95%.

Both scenes are what cad-solid's ``assembly`` view writes for the same parts: one
node per part body, named by cad-solid's own ``movers`` and ``node`` (part
``ctl-btn_y`` is node ``ctl-btn_y__0``), in the device frame, untransformed.

Re-run it after an edit to the baseline's controls, grips or handle diameter, and
commit both files. ``tests/test_hand_ergonomics.py`` rebuilds them in memory and
fails while the committed files differ from the baseline's geometry. Requires
trimesh and numpy (the gate's own requirements); manifold3d is not needed to build.
"""
from __future__ import annotations

import json
import math
import os
import sys

from nopekit.modelio import load_path

_HERE = os.path.dirname(os.path.abspath(__file__))
_PACK_DIR = os.path.dirname(_HERE)
BASELINE = os.path.join(_HERE, "baseline.json")
GOOD = os.path.join(_HERE, "meshes", "handheld.glb")
STALE = os.path.join(_HERE, "meshes", "handheld_stale.glb")
PARTS = load_path(os.path.join(os.path.dirname(_PACK_DIR), "cad-solid", "cad_solid_parts.py"))

#: The body, from the baseline's ``_description``: 150 x 60 x 22 mm, x -75..75,
#: y -40..20, z -22..0 (the face at z = 0).
BODY_MIN = (-75.0, -40.0, -22.0)
BODY_MAX = (75.0, 20.0, 0.0)

#: Each handle is a cylinder on its handle axis, from 8 mm on the body's side of
#: the grip centre to 30 mm past it. At -8 mm the end disc stays under the face
#: (z < 0), so no handle stands proud of it; at +30 mm the handle ends below the
#: last finger. Its section through the grip centre is the full circle.
HANDLE_FROM_MM = -8.0
HANDLE_TO_MM = 30.0

#: 96 segments: a 36 mm handle slices to 35.99 mm, inside the gate's 1.0 mm.
HANDLE_SEGMENTS = 96

#: Each cap: a 9 mm disc 4 mm tall on its normal, the top centre at ``at``.
#: A cylinder's surface centroid lies on its axis, so its top along the normal is
#: ``at`` to float32's precision.
CAP_DIAMETER_MM = 9.0
CAP_HEIGHT_MM = 4.0
CAP_SEGMENTS = 32

#: The known-bad: btn_y drawn 3 mm farther along the right hand's axis projected
#: onto the face (the direction digit_reach's own known-bad moves the cluster).
#: Six times the gate's 0.5 mm, and enough to take the drawn design under the
#: baseline claim (94.33% of women).
STALE_CONTROL = "btn_y"
STALE_GRIP = "right"
STALE_SHIFT_MM = 3.0


def _read() -> dict:
    with open(BASELINE, encoding="utf-8") as fh:
        return json.load(fh)


def _unit(v) -> tuple:
    n = math.sqrt(sum(float(c) * float(c) for c in v))
    return tuple(float(c) / n for c in v)


def _cylinder(diameter: float, height: float, sections: int, axis, centre):
    """A closed cylinder of ``diameter`` and ``height`` on ``axis``, centred at
    ``centre``."""
    import numpy as np
    import trimesh
    mesh = trimesh.creation.cylinder(radius=diameter / 2.0, height=height, sections=sections)
    rotate = trimesh.geometry.align_vectors([0.0, 0.0, 1.0], np.asarray(axis, dtype=float))
    mesh.apply_transform(rotate)
    mesh.apply_translation(np.asarray(centre, dtype=float))
    return mesh


def stale_at(baseline: dict) -> list:
    """btn_y's drawn top centre in the known-bad scene."""
    grip = next(g for g in baseline["hand_grips"] if g["id"] == STALE_GRIP)
    hx, hy = float(grip["hand_axis"][0]), float(grip["hand_axis"][1])
    norm = math.hypot(hx, hy)
    control = next(c for c in baseline["hand_controls"] if c["id"] == STALE_CONTROL)
    at = [float(c) for c in control["at"]]
    return [at[0] + STALE_SHIFT_MM * hx / norm, at[1] + STALE_SHIFT_MM * hy / norm, at[2]]


def handle_placement(grip: dict) -> tuple:
    """``(unit handle axis, centre of the handle's cylinder)`` for one declared grip."""
    axis = _unit(grip["handle_axis"])
    mid = (HANDLE_FROM_MM + HANDLE_TO_MM) / 2.0
    return axis, [float(c) + mid * a for c, a in zip(grip["centre"], axis)]


def parts(baseline: dict, *, stale: bool = False) -> dict:
    """``{part name: mesh}`` for the scene, in the device frame."""
    import trimesh
    out = {}
    body = trimesh.creation.box(bounds=[BODY_MIN, BODY_MAX])
    out["body"] = body
    diameter = float(baseline["hand_grip_diameter_mm"])
    for grip in baseline["hand_grips"]:
        axis, centre = handle_placement(grip)
        out[f"handle-{grip['id']}"] = _cylinder(diameter, HANDLE_TO_MM - HANDLE_FROM_MM,
                                                HANDLE_SEGMENTS, axis, centre)
    for control in baseline["hand_controls"]:
        normal = _unit(control["normal"])
        top = stale_at(baseline) if stale and control["id"] == STALE_CONTROL else control["at"]
        centre = [float(c) - (CAP_HEIGHT_MM / 2.0) * n for c, n in zip(top, normal)]
        out[f"ctl-{control['id']}"] = _cylinder(CAP_DIAMETER_MM, CAP_HEIGHT_MM, CAP_SEGMENTS,
                                                normal, centre)
    return out


def scene_of(meshes: dict):
    """``{part: mesh}`` as cad-solid's ``assembly`` view writes it: one node per
    part, named by cad-solid's ``movers`` and ``node``, untransformed."""
    import trimesh
    movers = PARTS.movers(meshes)
    out = trimesh.Scene()
    for part in sorted(meshes):
        name = PARTS.node(movers[part], 0)
        out.add_geometry(meshes[part], node_name=name, geom_name=name)
    return out


def scene(baseline: dict, *, stale: bool = False):
    """The known-good scene, or with ``stale`` the known-bad one."""
    return scene_of(parts(baseline, stale=stale))


def main() -> int:
    baseline = _read()
    os.makedirs(os.path.dirname(GOOD), exist_ok=True)
    for path, stale in ((GOOD, False), (STALE, True)):
        data = scene(baseline, stale=stale).export(file_type="glb")
        with open(path, "wb") as fh:
            fh.write(data)
        print(f"wrote {os.path.relpath(path, _PACK_DIR)} ({len(data)} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
