# SPDX-License-Identifier: Apache-2.0
"""The assembly, as a GLB the site can spin, explode, isolate and light up.

This is the view that turns a readiness report into a debugging tool. Every gate
in this pack that knows *which part* has a problem attaches a
:class:`~nopekit.models.Locator` naming a node in here, and the page isolates it.
So this module owes the rest of the pack three guarantees, and each one is a way
the link breaks if it is dropped:

* **The node names come from ``cad_solid_parts``**, the same module the gates
  spell their locator targets with. Naming nodes here and targeting them there,
  from two copies of one convention, is the drift the contract warns about and it
  is silent on both ends: the pack author sees a clean import and the reader sees
  an unannotated verdict.
* **The geometry is exported in the MODEL FRAME, untransformed.** trimesh's glTF
  writer preserves vertex coordinates exactly (no Z-up/Y-up rebasing at export,
  no node matrices), so a millimetre in the projection is a millimetre in the
  GLB — which is what makes an explode offset in ``derive_explode``'s output, or
  any future locator carrying an explicit ``position``, mean the same thing on
  both sides. Recentring the assembly on its bounding box here would be a
  friendlier default and would quietly invalidate every position the domain
  produces.
* **Nothing to draw returns ``None``.** A project with no exported geometry —
  and there are whole domains of them — still gets claims, verdicts, evidence
  and a readiness sentence. A viewgen that raised there, or emitted an empty
  view that renders as a broken box, would make "this project has no CAD" look
  like "the CAD is broken".

The explode manifest is DERIVED by :func:`nopekit.site.derive_explode` from the
node bounding boxes, then merged with whatever ``site/explode.json`` says. The
derivation exists to remove transcription, not to replace judgment: it measures
twenty bounding boxes for you and it does not know that the lid comes off before
the board. Authoring that file is the normal path.
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any

from nopekit.models import View, ViewKind
from nopekit.site import ViewContext, derive_explode, viewgen
from nopekit.util import NopekitError

# The naming interface, shared with gates/solid.py. See the import comment there.
_PACK_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PACK_DIR not in sys.path:
    sys.path.insert(0, _PACK_DIR)

import cad_solid_parts as PARTS  # noqa: E402

#: Authored explode overrides, relative to the project root. Read HERE rather
#: than in ``site.build`` because this module is the only thing that knows what
#: the movers are called — merging overrides against names nobody has derived yet
#: would mean matching a hand-written file against nothing.
EXPLODE_OVERRIDES = os.path.join("site", "explode.json")


def _load(source: Any, root: str) -> list:
    """Every solid body in one part, as a list of meshes. Raises on unreadable input.

    Unlike the gates, this loader uses trimesh's default processing. That is not
    an inconsistency to fix: the gates validate the bytes that will be sent to a
    manufacturer and must not let a load-time repair hide a defect
    (``cad.degenerate_faces`` exists to report exactly what processing silently
    fixes), while this function is producing a picture. A welded copy draws
    identically and loads faster, and nothing downstream of here measures
    anything.

    A scene with several geometries comes back as several bodies, which become
    several nodes under one mover — see :func:`cad_solid_parts.node`.
    """
    import trimesh

    if hasattr(source, "faces") and hasattr(source, "vertices"):
        return [source]

    path = str(source)
    if not os.path.isabs(path) and root:
        path = os.path.join(root, path)
    loaded = trimesh.load(path)
    if hasattr(loaded, "geometry") and not hasattr(loaded, "faces"):        # a Scene
        # Sorted so the body index is stable across runs: an unsorted dict walk
        # would renumber `housing__0` and `housing__1` between two builds of the
        # same model, and the diff of a generated file is the only thing telling a
        # reviewer what actually moved.
        return [loaded.geometry[key] for key in sorted(loaded.geometry)]
    return [loaded]


def _overrides(root: str, log) -> tuple[dict | None, str]:
    """``(overrides, problem)`` from ``site/explode.json``. Absent is not a problem.

    A malformed override file is REPORTED and then ignored, rather than taken
    down with the view. The person who hand-wrote it is looking at a viewer that
    is not obeying them, and the one thing they must not get is a missing model
    with no explanation — that reads as broken geometry and sends them to the CAD.
    """
    path = os.path.join(root or os.curdir, EXPLODE_OVERRIDES)
    if not os.path.isfile(path):
        return None, ""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        problem = f"{EXPLODE_OVERRIDES} could not be read ({exc}); using the derived explode"
        log(f"assembly: {problem}")
        return None, problem
    if not isinstance(data, dict):
        problem = (f"{EXPLODE_OVERRIDES} must be a JSON object of movers, got "
                   f"{type(data).__name__}; using the derived explode")
        log(f"assembly: {problem}")
        return None, problem
    return data, ""


@viewgen(
    id=PARTS.VIEW_ID,
    kind=ViewKind.MODEL3D,
    title="Assembly",
    description="Every placed solid in the model frame, one node per body, "
                "explodable along the stack axis.",
    requires_python=["trimesh", "numpy"],
    order=10,
    gates=["cad.clash", "cad.assembly_connected", "cad.watertight", "cad.is_volume",
           "cad.degenerate_faces", "cad.wall_thickness", "cad.bounding"],
)
def assembly(ctx: ViewContext) -> View | None:
    """The placed solids as a GLB, with a derived explode manifest.

    Nodes are ``<part>__<body>`` — see ``PACK.md``, "node names are an interface".
    """
    sources, where = PARTS.mesh_sources(ctx)
    if not sources:
        # The normal outcome in a project with no geometry, and in a project whose
        # geometry has not been exported yet. Not an error, and not a warning.
        ctx.log(f"assembly: {where}")
        return None

    import trimesh

    movers = PARTS.movers(sources)
    scene = trimesh.Scene()
    bounds: dict[str, tuple] = {}
    part_nodes: dict[str, list[str]] = {}
    unreadable: list[str] = []

    for part in sorted(sources, key=str):
        try:
            bodies = _load(sources[part], ctx.root or "")
        except Exception as exc:                        # noqa: BLE001 - user data
            # One unreadable part does not cost the other nineteen their picture.
            # It IS reported: a part silently missing from the assembly is a part
            # whose locators land on nothing, and the reader would be looking at a
            # model that is wrong in a way nothing on the page mentions.
            unreadable.append(f"{part} ({type(exc).__name__}: {exc})")
            ctx.log(f"assembly: {part!r} could not be read — {type(exc).__name__}: {exc}")
            continue
        for index, mesh in enumerate(bodies):
            if getattr(mesh, "faces", None) is None or not len(mesh.faces):
                continue                                # a point cloud or an empty body
            name = PARTS.node(movers[part], index)
            scene.add_geometry(mesh, node_name=name, geom_name=name)
            low, high = mesh.bounds
            bounds[name] = ([float(v) for v in low], [float(v) for v in high])
            part_nodes.setdefault(part, []).append(name)

    if not bounds:
        ctx.log("assembly: the projection names parts but none of them carried faces")
        return None

    src = ctx.write_asset("assembly.glb", scene.export(file_type="glb"))

    overrides, override_problem = _overrides(ctx.root or "", ctx.log)
    try:
        explode = derive_explode(bounds, overrides=overrides)
    except NopekitError as exc:
        # Same reasoning as `_overrides`: an override that will not validate costs
        # the override, never the model.
        override_problem = f"{EXPLODE_OVERRIDES}: {exc}; using the derived explode"
        ctx.log(f"assembly: {override_problem}")
        explode = derive_explode(bounds)

    meta: dict[str, Any] = {
        "nodes": sorted(bounds),
        "explode": explode,
        "units": "mm",
        # The map back from the projection's own part names to the nodes they
        # became. A reader looking at a verdict that names `guide roller` and a
        # viewer that names `guide_roller__0` needs to be able to see that those
        # are the same thing without reading this file.
        "parts": {part: nodes for part, nodes in sorted(part_nodes.items())},
        "source": where,
        "frame": "model frame, untransformed — a locator position in the model's "
                 "coordinates addresses the same point in this GLB",
    }
    if unreadable:
        meta["unreadable_parts"] = unreadable
    if override_problem:
        meta["explode_override_problem"] = override_problem

    return View(
        id=PARTS.VIEW_ID,
        kind=ViewKind.MODEL3D,
        title="Assembly",
        src=src,
        meta=meta,
    )
