# SPDX-License-Identifier: Apache-2.0
"""``pcb.enclosure_fit``'s geometry, run by the gate in a child process:

    python3 _fit.py fit <request.json>
    python3 _fit.py regions <request.json>
    python3 _fit.py boxes <request.json>

``fit``: the request names the enclosure's STEP and, for each board fitted in it,
the board's STEP (as ``kicad-cli pcb export step`` wrote it) and where the board
sits (one 3x3 rotation, then a translation). The child prints one JSON line. For
every named shape of each board (each footprint under its reference, the board
body under ``board``) it gives the volume the shape shares with the enclosure and
its distance to it. For each pair of boards in the same enclosure it gives, once,
the volume and distance from every shape of the first to the second. All of them
are OpenCascade's (a boolean common and BRepExtrema, through CadQuery).

A by-design contact is scoped to one shape and one feature, never to a shape
against everything. A feature is a region of the enclosure (a box in its frame)
or one shape of another board. The distance is then measured to what is left
when that feature is taken out, and the distance to the feature itself is listed
beside it. The shared volume is always measured against all of the enclosure and
all of the other board: a contact excuses closeness, never a collision. (What
slipped through elsewhere: an allow-list entry for "this part against anything"
hid a real interference. Here a contact names its feature, and shared volume
still counts.) A part with a region also gets its box as placed, and each region
the box of the enclosure material it took out, so the gate can hold a region to
what the part can reach.

``regions``: the same request, and for each part with a contact region only
what the gate holds a region to: the part's box as placed, whether its regions
leave any enclosure, and each region's box of enclosure material. No distance
and no shared volume, so the gate can refuse every region drawn too wide before
it pays for the full fit (a holdout run: two to four minutes on a populated
board, for each region refused).

``boxes``: writes a STEP for each part fitted at a declared height: its outline
(a box, a cylinder or a prism on a polygon) raised to its height, and a cylinder
for each lead under it, one solid each in one compound. It is in the footprint's
own model frame, so kicad-cli places it as it places any model, on the same
seating plane, turned and flipped with its footprint.

In a child, not in nopekit's process: an OpenCascade boolean can crash the
interpreter on bad geometry, and a crash here must read as this gate erroring,
not as the whole check run dying. It also keeps OpenCascade's imports out of the
gate's trace.
"""
from __future__ import annotations

import json
import sys


def _named_shapes(path: str) -> dict:
    """{name: shape} of the top assembly's components in a STEP file, read
    through OpenCascade's XCAF reader so each component keeps its name (KiCad
    names each footprint's model by its reference) and its placement. Shapes
    that share a name, or have none KiCad set, are fused into one compound."""
    import cadquery as cq
    from OCP.STEPCAFControl import STEPCAFControl_Reader
    from OCP.TCollection import TCollection_ExtendedString
    from OCP.TDataStd import TDataStd_Name
    from OCP.TDF import TDF_LabelSequence
    from OCP.TDocStd import TDocStd_Document
    from OCP.XCAFDoc import XCAFDoc_DocumentTool

    doc = TDocStd_Document(TCollection_ExtendedString("board"))
    reader = STEPCAFControl_Reader()
    reader.SetNameMode(True)
    if int(reader.ReadFile(path)) != 1:                    # IFSelect_RetDone
        raise RuntimeError(f"OpenCascade could not read {path}")
    reader.Transfer(doc)
    tool = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
    free = TDF_LabelSequence()
    tool.GetFreeShapes(free)
    found: dict[str, list] = {}

    def name_of(label) -> str:
        attr = TDataStd_Name()
        return attr.Get().ToExtString() if label.FindAttribute(TDataStd_Name.GetID_s(), attr) else ""

    for i in range(1, free.Length() + 1):
        top = free.Value(i)
        comps = TDF_LabelSequence()
        tool.GetComponents_s(top, comps)
        if comps.Length() == 0:
            found.setdefault(name_of(top), []).append(cq.Shape.cast(tool.GetShape_s(top)))
        for j in range(1, comps.Length() + 1):
            comp = comps.Value(j)
            found.setdefault(name_of(comp), []).append(cq.Shape.cast(tool.GetShape_s(comp)))
    return {name: shapes[0] if len(shapes) == 1 else cq.Compound.makeCompound(shapes)
            for name, shapes in found.items()}


def _one(shapes: list):
    """One shape of ``shapes`` (a compound of several), or None for none."""
    import cadquery as cq
    if not shapes:
        return None
    return shapes[0] if len(shapes) == 1 else cq.Compound.makeCompound(shapes)


def _location(rotation: list, translation: list):
    """The placement as one OpenCascade transform: the 3x3 rotation, then the
    translation. The gate composes the rotation from its three angles; this
    applies the matrix as given."""
    import cadquery as cq
    from OCP.gp import gp_Trsf

    (a, b, c), (d, e, f), (g, h, i) = rotation
    x, y, z = translation
    trsf = gp_Trsf()
    trsf.SetValues(a, b, c, x, d, e, f, y, g, h, i, z)
    return cq.Location(trsf)


def _box(lo: list, hi: list):
    import cadquery as cq
    return cq.Solid.makeBox(hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2],
                            pnt=cq.Vector(*lo))


def _shared(shape, other) -> float:
    """The volume ``shape`` and ``other`` share, mm^3."""
    common = shape.intersect(other)
    return common.Volume() if common.Solids() else 0.0


def _bbox(shape, solid: bool = True):
    """``[[xmin, ymin, zmin], [xmax, ymax, zmax]]`` of ``shape``, mm (OpenCascade's
    optimal box), or None when it holds no solid and ``solid`` asks for one. A
    part's box is taken whatever it is made of: a vendor model can be shells."""
    if shape is None or solid and not shape.Solids():
        return None
    bb = shape.BoundingBox()
    return [[bb.xmin, bb.ymin, bb.zmin], [bb.xmax, bb.ymax, bb.zmax]]


def _distance(shape, other):
    """The least distance between the two, mm, or None when ``other`` holds no
    solid (a region with no enclosure material in it, or nothing left)."""
    if other is None or not other.Solids():
        return None
    return shape.distance(other)


def _solid(spec: dict):
    """One solid of a declared part: ``{"box": [lo, hi]}``, ``{"cylinder": {at,
    r, z: [z0, z1]}}`` (upright) or ``{"prism": {points, z: [z0, z1]}}`` (a
    polygon extruded upright)."""
    import cadquery as cq
    if "box" in spec:
        return _box(*spec["box"])
    if "cylinder" in spec:
        c = spec["cylinder"]
        z0, z1 = c["z"]
        return cq.Solid.makeCylinder(c["r"], z1 - z0, cq.Vector(c["at"][0], c["at"][1], z0),
                                     cq.Vector(0, 0, 1))
    p = spec["prism"]
    z0, z1 = p["z"]
    return (cq.Workplane("XY", origin=(0, 0, z0)).polyline([tuple(q) for q in p["points"]])
            .close().extrude(z1 - z0).val())


def boxes(req: dict) -> dict:
    import cadquery as cq
    for item in req["boxes"]:
        shapes = [_solid(spec) for spec in item["solids"]]
        shape = shapes[0] if len(shapes) == 1 else cq.Compound.makeCompound(shapes)
        shape.exportStep(item["path"])
    return {"written": [item["path"] for item in req["boxes"]]}


def _enclosure(path: str):
    """``(the enclosure's solids as one shape, its box)``."""
    import cadquery as cq

    solids = cq.importers.importStep(path).solids().vals()
    if not solids:
        raise RuntimeError(f"no solid in {path}: an enclosure is a volume")
    enclosure = solids[0] if len(solids) == 1 else cq.Compound.makeCompound(solids)
    bb = enclosure.BoundingBox()
    return enclosure, [[bb.xmin, bb.ymin, bb.zmin], [bb.xmax, bb.ymax, bb.zmax]]


def _regions(shape, enclosure, mine: dict) -> dict:
    """What the gate holds one part's regions to: its box as placed, whether
    anything of the enclosure is left once its regions are taken out, the rest
    itself, and each region's material box (and the material, for a distance)."""
    rest = enclosure
    excused, inside = {}, {}
    for feature, (lo, hi) in sorted(mine.items()):
        box = _box(lo, hi)
        rest = rest.cut(box)
        inside[feature] = enclosure.intersect(box)
        excused[feature] = {"material_bbox": _bbox(inside[feature])}
    return {"bbox": _bbox(shape, solid=False), "excused": excused,
            "rest_empty": not rest.Solids(), "rest": rest, "inside": inside}


def regions(req: dict) -> dict:
    enclosure, bbox = _enclosure(req["enclosure_step"])
    out: dict = {"enclosure_bbox": bbox, "boards": {}}
    for board in req["boards"]:
        rows: dict = {}
        mine = board.get("regions", {})
        if mine:
            shapes = _named_shapes(board["board_step"])
            loc = _location(board["rotation"], board["translation"])
            for name, feats in sorted(mine.items()):
                if name in shapes and name in set(board["refs"]):
                    got = _regions(shapes[name].moved(loc), enclosure, feats)
                    rows[name] = {"enclosure": {k: got[k] for k in
                                                ("bbox", "excused", "rest_empty")}}
        out["boards"][board["id"]] = {"parts": rows}
    return out


def fit(req: dict) -> dict:
    enclosure, bbox = _enclosure(req["enclosure_step"])
    out: dict = {"enclosure_bbox": bbox, "boards": {}}
    placed: dict[str, dict] = {}
    for board in req["boards"]:
        shapes = _named_shapes(board["board_step"])
        refs = set(board["refs"])
        parts = {n: s for n, s in shapes.items() if n in refs}
        body = _one([s for n, s in shapes.items() if n not in refs])
        if body is not None:
            parts["board"] = body
        loc = _location(board["rotation"], board["translation"])
        placed[board["id"]] = {n: s.moved(loc) for n, s in parts.items()}
        out["boards"][board["id"]] = {"names": sorted(shapes), "parts": {}}

    for board in req["boards"]:
        rows = out["boards"][board["id"]]["parts"]
        regions = board.get("regions", {})
        for name, shape in sorted(placed[board["id"]].items()):
            row: dict = {"interference_mm3": _shared(shape, enclosure)}
            mine = regions.get(name, {})
            if mine:
                got = _regions(shape, enclosure, mine)
                for feature, inside in got["inside"].items():
                    got["excused"][feature]["clearance_mm"] = _distance(shape, inside)
                row["clearance_mm"] = _distance(shape, got["rest"])
                row["excused"] = got["excused"]
                row["bbox"] = got["bbox"]
                row["rest_empty"] = got["rest_empty"]
            else:
                row["clearance_mm"] = shape.distance(enclosure)
            rows[name] = {"enclosure": row, "boards": {}}

    # Each pair of boards once: every shape of the first against the second.
    # That covers every pair of shapes, the bodies included. The gate has made
    # each contact between two boards hold both ways before it wrote the request.
    for i, first in enumerate(req["boards"]):
        for second in req["boards"][i + 1:]:
            theirs = placed[second["id"]]
            whole = _one(list(theirs.values()))
            excused_all = first.get("excused", {})
            for name, shape in sorted(placed[first["id"]].items()):
                skip = set(excused_all.get(name, {}).get(second["id"], []))
                row = {"interference_mm3": _shared(shape, whole) if whole is not None else 0.0}
                if row["interference_mm3"] > 0.0:
                    row["with"] = {q: v for q, s in sorted(theirs.items())
                                   if (v := _shared(shape, s)) > 0.0}
                row["clearance_mm"] = _distance(
                    shape, _one([s for q, s in theirs.items() if q not in skip]))
                row["excused"] = {q: {"clearance_mm": shape.distance(theirs[q])}
                                  for q in sorted(skip) if q in theirs}
                out["boards"][first["id"]]["parts"][name]["boards"][second["id"]] = row
    return out


def main(argv: list[str]) -> int:
    mode, request_path = argv
    with open(request_path, encoding="utf-8") as fh:
        req = json.load(fh)
    result = {"fit": fit, "regions": regions, "boxes": boxes}[mode](req)
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
