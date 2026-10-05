# SPDX-License-Identifier: Apache-2.0
"""The part — or the whole plate — as it will sit on the bed, so the overhangs
can be pinned onto it.

``fdm.overhang`` knows the centroid of every face past the limit. That is a list
of coordinates in a text file until there is geometry to put them on; with this
view it is the underside of the part, lit up where it will need support. Same for
``fdm.bed_fit``, which knows the part is too big and has, until now, had nothing
to point at.

Three things this viewgen owes the gates:

* **The mesh goes in untransformed**, in the print orientation the project
  exported it in. trimesh's glTF writer preserves vertex coordinates exactly, so a
  face centroid measured by ``fdm.overhang`` is that same point in the GLB. Any
  recentring here — and recentring an exported part on the origin is the friendly
  default — would move every pin off its face with nothing to show it had
  happened.
* **The node name comes from ``fdm_print_parts``**, the module the gates spell
  their locator targets with.
* **No mesh is not an error.** ``None``, and the pack's tier-0 gates still report
  bed fit, wall thickness, print time and layer alignment from the projection's
  numbers. A project that has not exported geometry yet is an ordinary project.

The build direction is the model's ``build_axis`` and is published in ``meta``
rather than baked into the geometry, for the same reason the mesh is not
reoriented: the angles in the verdicts were measured against that axis in this
frame, and a viewer that wants +Z up can rotate the camera.
"""
from __future__ import annotations

import os
import sys
from typing import Any

from nopekit.models import View, ViewKind
from nopekit.site import ViewContext, derive_explode, viewgen

_PACK_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PACK_DIR not in sys.path:
    sys.path.insert(0, _PACK_DIR)

import fdm_print_parts as PARTS  # noqa: E402


@viewgen(
    id=PARTS.VIEW_ID,
    kind=ViewKind.MODEL3D,
    title="Printed part",
    description="The exported part in its print orientation, with the build axis "
                "and bed envelope published alongside it.",
    requires_python=["trimesh", "numpy"],
    order=10,
    gates=["fdm.overhang", "fdm.bridge_span", "fdm.bed_fit"],
)
def part(ctx: ViewContext) -> View | None:
    """The part meshes as a GLB, in print orientation, one node per body.

    Nodes are ``<part>__<body>`` — see ``PACK.md``, "the node name is an interface".
    A projection that states a part SET gets one mover per part in it; a
    projection that states a single ``mesh_path`` gets the one it always did. A
    part in the set whose file is missing is logged and left out, and the rest of
    the plate is still drawn — the gates report that part as unmeasured, so the
    page and the verdict agree about which parts were looked at.
    """
    # The whole print set when the model states one, otherwise the single part.
    # The gates make the same call, so the nodes drawn here and the nodes their
    # locators name are the same list by construction. They were not, for one
    # commit: the gates measured thirteen parts and this drew the one the
    # projection happened to call mesh_path, and twelve pins had nothing under
    # them — which `site build` reports as dangling anchors and a reader reads as
    # a broken pack.
    parts = PARTS.part_set(ctx)
    if parts:
        plate = [(part.name, part.raw) for part in parts if part.raw]
        if not plate:
            ctx.log(f"print_part: the {len(parts)} part(s) in the set name no mesh "
                    f"— nothing to draw")
            return None
        raw = ", ".join(path for _name, path in plate)
    else:
        single = PARTS.mesh_path(ctx)
        if not single:
            ctx.log("print_part: the projection names no mesh_path and no part set "
                    "— nothing to draw")
            return None
        plate = [(PARTS.mover(ctx), single)]
        raw = single

    import trimesh

    scene = trimesh.Scene()
    bounds: dict[str, tuple] = {}
    for mover, stated in plate:
        path = (stated if os.path.isabs(stated)
                else os.path.join(ctx.root or os.curdir, stated))
        if not os.path.isfile(path):
            # Reported, not raised, and not silent. A path pointing at a file that
            # is not there is a build that did not run, and the reader needs to
            # know that is why the part is missing rather than concluding it has
            # no geometry.
            ctx.log(f"print_part: {mover} points at {path}, which does not exist")
            continue
        try:
            loaded = trimesh.load(path)
        except Exception as exc:                        # noqa: BLE001 - user data
            ctx.log(f"print_part: {os.path.basename(path)} could not be read — "
                    f"{type(exc).__name__}: {exc}")
            continue

        if hasattr(loaded, "geometry") and not hasattr(loaded, "faces"):    # a Scene
            # Sorted so body numbering is stable between two builds of the same
            # model: the diff of a generated file is the only thing telling a
            # reviewer what actually moved.
            bodies = [loaded.geometry[key] for key in sorted(loaded.geometry)]
        else:
            bodies = [loaded]

        drawn = 0
        for index, mesh in enumerate(bodies):
            if getattr(mesh, "faces", None) is None or not len(mesh.faces):
                continue
            name = PARTS.node(mover, index)
            scene.add_geometry(mesh, node_name=name, geom_name=name)
            low, high = mesh.bounds
            bounds[name] = ([float(v) for v in low], [float(v) for v in high])
            drawn += 1
        if not drawn:
            ctx.log(f"print_part: {os.path.basename(path)} carries no faces")

    if not bounds:
        ctx.log("print_part: nothing in the print set could be drawn")
        return None

    src = ctx.write_asset("print_part.glb", scene.export(file_type="glb"))

    meta: dict[str, Any] = {
        "nodes": sorted(bounds),
        # A single part explodes to nothing, and that is the honest manifest for
        # one body. It is derived anyway so the payload has the same shape in the
        # multi-body case — a print plate carrying the part and its test coupon —
        # where it does separate them.
        #
        # A whole PRINT SET needs it. Every part goes in untransformed, in its own
        # print orientation, so thirteen parts all sit on the same origin and the
        # plate is a pile until the viewer pulls it apart. Moving them here would
        # break the promise the frame note below makes to fdm.overhang, whose
        # locators are positions in each part's own coordinates.
        "explode": derive_explode(bounds),
        "units": "mm",
        "mesh": raw,
        "build_axis": list(ctx.param("build_axis") or [0.0, 0.0, 1.0]),
        "frame": "print orientation, untransformed — a locator position in the "
                 "model's coordinates addresses the same point in this GLB",
    }
    bed = [ctx.param("bed_x_mm", ctx.param("bed_xy_mm")),
           ctx.param("bed_y_mm", ctx.param("bed_xy_mm")),
           ctx.param("bed_z_mm", ctx.param("build_height_mm"))]
    if all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in bed):
        # The envelope the part has to fit, so the viewer can draw the box the
        # verdict is comparing against instead of just reporting a ratio.
        meta["bed_mm"] = [float(v) for v in bed]
        brim = ctx.param("brim_mm", ctx.param("brim_allowance_mm"))
        if isinstance(brim, (int, float)) and not isinstance(brim, bool):
            meta["brim_mm"] = float(brim)

    return View(
        id=PARTS.VIEW_ID,
        kind=ViewKind.MODEL3D,
        title="Printed part",
        src=src,
        meta=meta,
    )
