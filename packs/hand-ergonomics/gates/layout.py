# SPDX-License-Identifier: Apache-2.0
"""hand-ergonomics — ``ergo.layout_agrees``: is the declared layout the drawn one?

Tier 1, trimesh, numpy and manifold3d, under a second for a hand-held device. It
is the validity guard of ``ergo.digit_reach``'s inputs. That gate counts subjects
on the layout the model declares (``hand_controls``, ``hand_grips``,
``hand_grip_diameter_mm``); this one reads the exported scene and checks the parts
of that declaration the scene can show:

1. **The controls, both ways.** Every declared control is drawn as a part
   ``ctl-<id>``, and every drawn ``ctl-*`` part is declared. A drawn control the
   layout leaves out is never judged by the reach gate at all.
2. **Where each cap is.** The top of each control's part along its declared
   ``normal`` (the direction it is pressed) lies within :data:`POSITION_TOL_MM`
   of its declared ``at``.
3. **No handle is thicker than declared.** At each grip, the section of the
   housing through the grip centre, square to the handle axis, is no wider than
   ``hand_grip_diameter_mm`` plus :data:`GIRTH_TOL_MM`, read as the round handle
   its points lie on. One-sided: a larger declared diameter puts every wrist
   farther back, which is conservative, so a device with two different handles
   declares the larger and passes.
4. **Each grip is on its handle.** In that section, the declared grip centre lies
   within :data:`CENTRE_TOL_MM` of the drawn handle's axis, the centroid of the
   section's outer loop.

Where along the handle the hand holds, and the hand axis, are posture: the scene
shows the handles, not the hand, so they stay a declaration (GRIP-POSTURE).

**The scene, and the node names that are an interface.** The parts come from one
of two places, first match:

* ``hand_layout_scene``: one GLB or glTF file in the device frame, its nodes named
  by cad-solid's scheme, ``<part>__<body>`` (what cad-solid's ``assembly`` view
  writes, or a CAD export named the same way). A node's part is the site's own
  reading, ``nopekit.site.mover_name``: the text before ``__``. Node transforms
  apply.
* otherwise **cad-solid's mesh map** (``meshes``, ``solids``, ``parts``, in its
  order), named by cad-solid's ``movers`` and ``node`` exactly as its
  ``assembly`` view names them: part ``ctl-btn_a`` is node ``ctl-btn_a__0``. Each
  part's file is read as the view reads it (its bodies in sorted order,
  untransformed). So a project that already publishes cad-solid's assembly gets
  this gate with no extra export.

The names come from ``_names.py``: cad-solid's functions, copied, and held to
cad-solid's own by a test (that module says why it is not loaded from cad-solid).

What slipped through before this gate (the design's case, reproduced in
``tests/test_hand_ergonomics.py``): the reach gate judges the layout as declared.
A layout one revision behind the CAD, with btn_y drawn 3 mm farther up the face,
reads 97.96% of women in reach against ``>= 95%``; the drawn design reaches
94.33%. A 50 mm handle declared as 36 mm reads 97.96% where the truth is 83.92%.
The registry refuses this gate as a ``needs`` prerequisite of the reach gate (a
tier-1 guard above a tier-0 analysis), so it covers the reach claim's tag
instead: the claim waits for both.
"""
from __future__ import annotations

import json
import math
import os
from typing import Any

from nopekit.gates import gate, GateContext
from nopekit.modelio import load_path as _load_path
from nopekit.models import NegativeControl, Tier, Verdict
from nopekit.site import mover_name

_HERE = os.path.dirname(os.path.abspath(__file__))
A = _load_path(os.path.join(_HERE, "_anthro.py"))
NAMES = _load_path(os.path.join(_HERE, "_names.py"))

GID = "ergo.layout_agrees"

#: The claim tags this gate covers. ``digit-reach``: the reach claim waits for
#: both gates, so a reach pass never reads Checked on a layout whose drawn
#: controls, caps, handle girth or handle position the declaration does not
#: match (the grip posture stays a declaration, GRIP-POSTURE). ``control-layout``:
#: a claim that the declared layout is the drawn design, for a project that holds
#: it on its own. Rejected: a ``needs`` edge from ``ergo.digit_reach`` (the
#: registry refuses a tier-1 prerequisite on a tier-0 gate, as a build system
#: refuses a cheap target that waits on a costly one).
TAGS = ["digit-reach", "control-layout"]

#: The prefix that makes a part a control: part ``ctl-<id>`` is the control
#: declared with id ``<id>``. A prefix, not a suffix or a group: cad-solid's
#: scheme keeps ``-`` and collapses ``_``, so ``ctl-`` survives sanitising, and
#: ``__`` is the body separator. Rejected: ``control__<id>`` (the design's: the
#: site's explode reads everything before ``__`` as one part, so every control of
#: one GLB would explode as one part called ``control``, and the scene would not
#: be cad-solid's); ``housing__<part>`` for the shell (every part that is not a
#: control is the housing here, so no second name is needed).
CONTROL_PREFIX = "ctl-"

#: How far the drawn cap top may lie from the declared ``at``, mm. Two exports of
#: one model agree to their tessellation; a stale layout does not. On the
#: baseline, moving the limiting button 0.5 mm farther from the wrist moves the
#: women's count by 0.64 points (97.96% to 97.32%), well inside the 5.6 to 5.8 mm
#: spread of reach in each sex. Rejected: 0.05 mm (a domed cap sampled at its
#: vertices moves about 0.1 mm with its segment count); 2 mm (a layout one 1.5 mm
#: nudge behind passes, and 2 mm on the baseline's btn_y is 1.95 points).
POSITION_TOL_MM = 0.5

#: How much wider than declared a handle's section may be, mm of diameter. One
#: millimetre of diameter moves every wrist point 0.5 mm (the girth term is half
#: the difference), the same as :data:`POSITION_TOL_MM`; on the baseline 37 mm
#: declared as 36 mm moves the women's count by 0.55 points. The section is read
#: as the round handle its points lie on (:func:`_round_factor`), so an evenly
#: tessellated export hides no girth from this tolerance. Rejected: 0.1 mm (a
#: handle declared to the nearest millimetre fails); 5 mm (a 41 mm handle declared
#: as 36 mm passes: 2.5 mm of wrist, and 2.58 points of women on the baseline).
GIRTH_TOL_MM = 1.0

#: How far the declared grip centre may lie from the drawn handle's axis, mm: the
#: area centroid of the section's outer loop, in the plane through the centre
#: square to the handle axis. A centre off by d moves every wrist point by d, as a
#: cap off by d moves its control by d, so the tolerance is
#: :data:`POSITION_TOL_MM`'s; on the baseline both handles drawn 0.5 mm back along
#: the hand axis move the women's count by 0.55 points (97.96% to 97.41%). Where
#: along the handle the hand holds is not in this plane: that is posture
#: (GRIP-POSTURE), and the scene cannot show it. What slipped through (review of
#: E2): the guard held only that some loop surrounds the centre, so a centre up to
#: one handle radius off the drawn axis passed. Both handles drawn 10 mm back from
#: the controls, the layout not following, passed at 0.00 mm, and the reach claim
#: read Checked at 97.96% where the drawn design reaches 68.52%. Rejected: a share
#: of the declared diameter (5% is 1.8 mm of wrist, 1.59 points of women: the wrist
#: does not move less on a thick handle); the loop's bounding-box centre (it is
#: not the centre of an asymmetric section, and moves with a fillet on one side);
#: the vertex mean (it moves with how densely the exporter meshed one side).
CENTRE_TOL_MM = POSITION_TOL_MM

#: Consecutive section points closer than this, mm, are one point: a section
#: united from two half shells keeps both shells' points where they meet, and a
#: zero-length edge has no direction (its turn would be noise up to pi, and
#: :func:`_even_steps` squares it). Far below any real edge (a 36 mm handle at
#: 1,000,000 segments has 0.11 um edges) and far above float32's 1e-5 mm in the
#: device frame.
_SAME_POINT_MM = 1e-4

#: The noise floor below which a disagreement is no disagreement, as decimals of a
#: millimetre: a GLB stores float32, about 1e-5 mm at 100 mm from the origin, so a
#: cap built exactly at its ``at`` reads a few 1e-6 mm off. A value is rounded to
#: 1e-4 mm first, then UP to 0.01 mm, the failing side of ``<=``, and judged as
#: reported. What slipped through on the reach gate's share (review of E1): a
#: value rounded half-up before it was judged read a fail as a pass. Rejected:
#: ceil of the raw float (float32 noise reads 0.01 mm on a cap that is exact); the
#: exact float judged beside a rounded value shown (two numbers for one verdict).
_NOISE_DECIMALS = 4

#: How many disagreements the detail line names before it says "and N more".
_NAMED = 3


def _skip(reason: str) -> Verdict:
    return Verdict(gate=GID, passed=False, skipped=True, skip_reason=reason)


def _error(message: str) -> Verdict:
    return Verdict(gate=GID, passed=False, error=message)


def _up(value: float) -> float:
    """``value`` rounded to the noise floor, then up to 0.01 (the failing side)."""
    return math.ceil(round(float(value), _NOISE_DECIMALS) * 100) / 100


def _r(value: float) -> float:
    return round(float(value), _NOISE_DECIMALS)


# --------------------------------------------------------------------------- #
# the scene
# --------------------------------------------------------------------------- #
def _read_scene(path: str) -> Any:
    """A module-level loader for ``ctx.load_file``: the scene file as trimesh reads
    it. Shared through the sweep's memo, so it is never changed here."""
    import trimesh
    return trimesh.load(path, force="scene")


def _read_part(path: str) -> list:
    """One part's bodies, as cad-solid's ``assembly`` view reads them: a scene's
    geometries in sorted order, untransformed, or the one mesh."""
    import trimesh
    loaded = trimesh.load(path)
    if isinstance(loaded, trimesh.Scene):
        return [loaded.geometry[key] for key in sorted(loaded.geometry)]
    return [loaded]


def _mesh_map(ctx: GateContext) -> tuple[dict | None, str]:
    """``({part: source}, where)``: cad-solid's mesh map from the projection, the
    first of its keys that holds one, as cad-solid's ``mesh_sources`` finds it (a
    ``{name: path}`` map, or a list of paths named by their file stems). Each key
    is read under this pack's scopes, then cad-solid's (``cad.meshes``), then
    bare, so a map a project scoped to cad-solid is the scene here too.

    Not ``ctx.extra``, which cad-solid reads first: that is how its own fixtures
    hand it geometry, and no control here uses it."""
    scopes = list(ctx.scopes()) + ["cad", "cad-solid"]
    for key in NAMES.MESH_KEYS:
        value = ctx.param(key, scope=scopes)
        if isinstance(value, dict) and value:
            return dict(value), f"params[{key!r}]"
        if isinstance(value, (list, tuple)) and value:
            named = {}
            for i, item in enumerate(value):
                stem = os.path.splitext(os.path.basename(str(item)))[0] or f"part{i}"
                named[stem] = item
            return named, f"params[{key!r}]"
    return None, ""


def _shown(path: str, root: str) -> str:
    """``path`` as a verdict names it: relative to the project when it lies inside,
    else its file name. A verdict entry's bytes must not depend on where the
    project sits (``tests/test_determinism.py``), and a known-bad fixture hands an
    absolute path into this pack."""
    if not os.path.isabs(path):
        return path.replace(os.sep, "/")
    if root:
        rel = os.path.relpath(path, root)
        if not rel.startswith(os.pardir):
            return rel.replace(os.sep, "/")
    return os.path.basename(path)


def _scene_bodies(ctx: GateContext) -> tuple[dict | None, str]:
    """``({part: [(node, mesh)]}, where)``, or ``(None, why)`` when no scene is
    published or it cannot be read. Never invents geometry."""
    path = ctx.param("hand_layout_scene")
    if path is not None:
        if not isinstance(path, str) or not path.strip():
            return None, (f"hand_layout_scene is {path!r}; it is the path of a GLB or "
                          f"glTF scene in the device frame")
        where = f"hand_layout_scene {_shown(path, ctx.root or '')}"
        full = path if os.path.isabs(path) else os.path.join(ctx.root or "", path)
        try:
            scene = ctx.load_file(full, loader=_read_scene)
        except Exception as exc:                       # noqa: BLE001 - user data
            return None, f"{where} could not be read ({type(exc).__name__}: {exc})"
        parts: dict[str, list] = {}
        for node in sorted(scene.graph.nodes_geometry):
            transform, geometry = scene.graph[node]
            mesh = scene.geometry.get(geometry)
            if getattr(mesh, "faces", None) is None or not len(mesh.faces):
                continue
            placed = mesh.copy()
            placed.apply_transform(transform)
            # Sanitised as cad-solid names a part, so a CAD export's raw node name
            # (`ctl-btn a`) reads as the part cad-solid would call it; idempotent on
            # cad-solid's own names.
            parts.setdefault(NAMES.sanitise(mover_name(node)), []).append((str(node), placed))
        return parts, where
    sources, where = _mesh_map(ctx)
    if sources is None:
        return None, ("no scene: the projection publishes neither hand_layout_scene (a GLB "
                      "whose nodes are <part>__<body>) nor cad-solid's mesh map (meshes, "
                      "solids or parts, scoped ergo. or cad.); publish one, with each "
                      f"control drawn as part {CONTROL_PREFIX}<id> (PACK.md, the node "
                      f"interface)")
    movers = NAMES.movers(sources)
    parts = {}
    for part in sorted(sources, key=str):
        source = sources[part]
        try:
            if hasattr(source, "faces") and hasattr(source, "vertices"):
                bodies = [source]
            else:
                text = str(source)
                full = text if os.path.isabs(text) else os.path.join(ctx.root or "", text)
                bodies = ctx.load_file(full, loader=_read_part)
        except Exception as exc:                       # noqa: BLE001 - user data
            return None, (f"part {part!r} from {where} could not be read "
                          f"({type(exc).__name__}: {exc})")
        for index, mesh in enumerate(bodies):
            if getattr(mesh, "faces", None) is None or not len(mesh.faces):
                continue
            parts.setdefault(movers[part], []).append((NAMES.node(movers[part], index), mesh))
    return parts, where


# --------------------------------------------------------------------------- #
# the three checks
# --------------------------------------------------------------------------- #
def _cap_top(bodies: list, normal: tuple) -> tuple:
    """The top of a control's part along its press direction: the area-weighted
    centroid of its surface, carried along ``normal`` to the farthest vertex.

    The surface centroid, not the vertex mean: a vertex mean moves with how
    densely an exporter meshed one side of a cap. Rejected: the bounding box's
    centre (it is not the centre of a tilted cap)."""
    import numpy as np
    n = np.asarray(normal, dtype=float)
    weighted = np.zeros(3)
    total = 0.0
    top = -math.inf
    for _node, mesh in bodies:
        tri = np.asarray(mesh.triangles, dtype=float)
        areas = 0.5 * np.linalg.norm(np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]),
                                     axis=1)
        weighted += (areas[:, None] * tri.mean(axis=1)).sum(axis=0)
        total += float(areas.sum())
        top = max(top, float((np.asarray(mesh.vertices, dtype=float) @ n).max()))
    centroid = weighted / total if total > 0 else np.zeros(3)
    return tuple(float(v) for v in centroid + (top - float(centroid @ n)) * n)


def _frame(axis: tuple):
    """Rows ``e1, e2, axis``: a right-handed basis whose third row is ``axis``."""
    import numpy as np
    h = np.asarray(axis, dtype=float)
    seed = np.array([1.0, 0.0, 0.0]) if abs(h[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
    e1 = np.cross(h, seed)
    e1 /= np.linalg.norm(e1)
    return np.vstack([e1, np.cross(h, e1), h])


def _manifold(vertices, faces):
    """A manifold3d solid, or raises: a slice of a solid manifold3d could not build
    is empty, and an empty section reads "no handle there". cad-solid's
    ``_manifold``, copied because a gate module cannot load another (its gates
    would register twice)."""
    import manifold3d
    import numpy as np
    solid = manifold3d.Manifold(manifold3d.Mesh(
        vert_properties=np.asarray(vertices, dtype=np.float32),
        tri_verts=np.asarray(faces, dtype=np.uint32)))
    status = solid.status()
    if status != manifold3d.Error.NoError or solid.is_empty():
        raise ValueError(f"manifold3d built no solid from it ({status.name}"
                         f"{', empty' if solid.is_empty() else ''})")
    return solid


def _contains_origin(loop) -> bool:
    """Even-odd ray test: does the closed ``loop`` (n x 2) surround (0, 0)?"""
    import numpy as np
    x, y = loop[:, 0], loop[:, 1]
    x2, y2 = np.roll(x, -1), np.roll(y, -1)
    crosses = (y > 0) != (y2 > 0)
    with np.errstate(divide="ignore", invalid="ignore"):
        at = x + (0.0 - y) * (x2 - x) / (y2 - y)
    return bool(np.count_nonzero(crosses & (at > 0)) % 2)


def _even_steps(loop) -> float:
    """The number of even steps the closed ``loop`` (n x 2) turns like: 4 pi^2 over
    the sum of the squared turns at its points, after points closer than
    :data:`_SAME_POINT_MM` are merged. An n-gon with even steps gives n back.

    A section cuts a side face's diagonals as well as its long edges, so a
    96-segment cylinder slices to 192 points, half of them on a chord between two
    corners. Such a point does not turn, so it adds nothing to the sum: on the
    committed GLB, float32 noise turns the 96 of them by 3e-6 rad at most, 2e-10
    in all against the 0.41 the 96 real corners add. A loop with uneven steps
    counts its coarse corners most, as the share it hides does. Rejected: counting
    the points (the 192-point loop reads as 192 steps, and a coarse handle's
    correction falls to a quarter); counting the points that turn by more than a threshold (on
    the committed GLB, float32 noise turned 37 chord points past 1e-6 rad, so its
    96-corner handles read as 133; a threshold above the noise, 1e-3 rad, sees no
    corner on a mesh finer than 6,283 segments, and a handle there with two sharp
    edges reads as a three-sided loop, 21% thick)."""
    import numpy as np
    kept = [loop[0]]
    for p in loop[1:]:
        if float(np.linalg.norm(p - kept[-1])) >= _SAME_POINT_MM:
            kept.append(p)
    while len(kept) > 1 and float(np.linalg.norm(kept[0] - kept[-1])) < _SAME_POINT_MM:
        kept.pop()
    pts = np.asarray(kept, dtype=float)
    edges = np.roll(pts, -1, axis=0) - pts
    heading = np.arctan2(edges[:, 1], edges[:, 0])
    turn = (np.roll(heading, -1) - heading + math.pi) % (2.0 * math.pi) - math.pi
    squared = float(np.dot(turn, turn))
    return (4.0 * math.pi * math.pi / squared) if squared > 0 else math.inf


def _round_factor(steps: float) -> float:
    """``(pi / n) / sin(pi / n)``: what a loop of ``n`` even steps on a round handle
    is scaled by to read the handle's own perimeter / pi.

    A tessellated handle slices short: ``n`` corners on a circle of diameter D make
    a loop whose perimeter / pi is ``D n sin(pi / n) / pi``, 0.64% short at 16
    segments, 2.55% at 8 and 4.51% at 6. What slipped through (review of E2): the
    stated bound, "up to 0.65%", held only at 16 segments and above, and an
    8-segment 37.9 mm handle read 36.94 mm and passed a 36 mm declaration. Exact
    for a handle exported at one angular step, as CAD exports a round face. A loop
    fine in one part and coarse in another is an estimate, and a handle drawn as a
    polygon reads as the round handle on its corners, which is larger
    (conservative: a square handle reads 11% thick). Both are named in PACK.md.
    Rejected: refusing a loop with few steps (a handle designed as a polygon would
    be refused); twice the farthest point from the centroid (an oval handle's long
    axis is not its perimeter / pi)."""
    n = float(steps)
    if not math.isfinite(n):
        return 1.0                 # a loop that never turns: no chord to correct
    n = max(n, 3.0)
    return (math.pi / n) / math.sin(math.pi / n)


def _loop_area(p) -> float:
    """The signed area of the closed loop ``p`` (n x 2), the shoelace sum."""
    import numpy as np
    return 0.5 * float(np.dot(p[:, 0], np.roll(p[:, 1], -1))
                       - np.dot(p[:, 1], np.roll(p[:, 0], -1)))


def _loop_centroid(p) -> tuple:
    """The area centroid of the closed loop ``p`` (n x 2), as a filled polygon."""
    import numpy as np
    x, y = p[:, 0], p[:, 1]
    x2, y2 = np.roll(x, -1), np.roll(y, -1)
    cross = x * y2 - x2 * y
    six_area = 3.0 * float(cross.sum())
    return (float(((x + x2) * cross).sum()) / six_area,
            float(((y + y2) * cross).sum()) / six_area)


def _section(housing: list, centre: tuple, axis: tuple) -> dict | None:
    """The outer loop of the housing's section through ``centre``, square to
    ``axis``; None when no loop surrounds the centre. Its keys:

    * ``across``: the loop's perimeter / pi, scaled by :func:`_round_factor` for
      its steps: the round handle it was cut from.
    * ``chorded``: the loop's own perimeter / pi, before that scale.
    * ``steps``: the even steps it turns like (:func:`_even_steps`).
    * ``drawn_centre``: the loop's area centroid, the drawn handle's axis in this
      plane, in the device frame.
    * ``off``: how far ``centre`` lies from it.

    Each part that crosses the plane is sliced on its own and the sections are
    united in the plane, so a handle drawn as two half shells is one loop. Of the
    loops that surround the centre, the largest is the outer envelope: a hollow
    shell's cavity loop surrounds it too. Raises ``ValueError`` naming a part that
    crosses the plane and is not a solid."""
    import numpy as np
    rows = _frame(axis)
    g = np.asarray(centre, dtype=float)
    sections = []
    for name, mesh in housing:
        local = (np.asarray(mesh.vertices, dtype=float) - g) @ rows.T
        if local[:, 2].min() > 0.0 or local[:, 2].max() < 0.0:
            continue
        try:
            solid = _manifold(local, mesh.faces)
        except ValueError as exc:
            raise ValueError(f"node {name} crosses the section and {exc}") from None
        cut = solid.slice(0.0)
        if not cut.is_empty():
            sections.append(cut)
    if not sections:
        return None
    union = sections[0]
    for cut in sections[1:]:
        union = union + cut
    loops = [np.asarray(p, dtype=float) for p in union.to_polygons() if len(p) >= 3]
    around = [p for p in loops if _contains_origin(p)]
    if not around:
        return None
    outer = max(around, key=lambda p: abs(_loop_area(p)))
    perimeter = float(np.linalg.norm(np.roll(outer, -1, axis=0) - outer, axis=1).sum())
    chorded = perimeter / math.pi
    steps = _even_steps(outer)
    cx, cy = _loop_centroid(outer)
    drawn = g + cx * rows[0] + cy * rows[1]
    return {"across": chorded * _round_factor(steps), "chorded": chorded,
            "steps": steps, "drawn_centre": tuple(float(v) for v in drawn),
            "off": math.hypot(cx, cy)}


def _normals(raw: list, controls: list) -> dict:
    """``{control id: unit normal}``; :class:`A.LayoutError` for a control with
    none. ``raw`` is the declared list ``A.parse_controls`` accepted, in order."""
    out = {}
    for entry, control in zip(raw, controls):
        value = entry.get("normal")
        if value is None:
            raise A.LayoutError(
                f"control {control.id!r} has no normal: the direction its cap is pressed, "
                f"[x, y, z] in the device frame (ergo.layout_agrees reads the cap's top "
                f"along it)")
        out[control.id] = A.unit(value, f"control {control.id!r} normal")
    return out


def _evidence(ctx: GateContext, payload: dict) -> list[str]:
    path = ctx.out_path("hand-ergonomics", "layout_agreement.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True)
        fh.write("\n")
    return [path]


@gate(
    id=GID,
    basis="model-only",
    title="The declared layout is the drawn one: every drawn control declared, every cap "
          "where the layout says, no handle thicker than declared, every grip on its "
          "handle",
    claims=TAGS,
    tier=Tier.BUILD,
    settles="declared layout agreement",
    requires_python=["trimesh", "numpy", "manifold3d"],
    description=(
        "The validity guard of digit_reach's inputs: the declared controls and handle "
        "diameter against the exported scene, on cad-solid's node names (part ctl-<id>). "
        "The control set both ways, each cap within 0.5 mm of its declared position, no "
        "handle section wider than the declared diameter plus 1.0 mm (one-sided), and each "
        "grip centre within 0.5 mm of its drawn handle's axis. It "
        "covers the reach claim's tag, because the registry refuses a tier-1 prerequisite "
        "on a tier-0 gate."),
    negative_control=NegativeControl(
        fixture="selftest/bad_layouts.py:stale_scene",
        note="the baseline layout against selftest/meshes/handheld_stale.glb, the baseline "
             "scene with btn_y's cap drawn 3 mm farther up the face than declared: 3.00 mm "
             "against 0.5 mm",
    ),
)
def layout_agrees(ctx: GateContext) -> Verdict:
    """The declared layout against the exported scene.

    Skips when the projection declares no controls, publishes no scene, or draws
    no ``ctl-`` part (an export requirement, not a judgment of the design), and
    when a declared grip comes with no handle diameter. Errors on a layout it
    cannot judge: what ``_anthro``'s parsers refuse (``parse_controls``,
    ``parse_grips``), a control with no ``normal``, two declared ids that name one
    part, and a part that crosses a grip's section and is not a solid. The reach
    gate's other refusals (a control that names a grip no grip declares, a judged
    control behind its grip centre) are not run here: on those
    ``ergo.digit_reach`` errors, so the reach claim is not Checked. Fails on any
    structural disagreement (a control drawn and not declared or declared and not
    drawn, a grip centre no section surrounds, a handle wider than declared, a
    grip centre off its handle's axis), counted against 0; otherwise on the worst
    cap, in mm, against :data:`POSITION_TOL_MM`."""
    raw_controls = ctx.param("hand_controls")
    if raw_controls is None:
        return _skip("no hand_controls: the controls whose drawn caps this gate checks "
                     "(see the pack's selftest/baseline.json)")
    try:
        controls = A.parse_controls(raw_controls)
    except A.LayoutError as exc:
        return _error(str(exc))
    if not controls:
        return _skip("hand_controls declares no control: nothing to find in the scene")
    parts, where = _scene_bodies(ctx)
    if parts is None:
        return _skip(where)
    drawn = {mover for mover in parts if mover.startswith(CONTROL_PREFIX)}
    if not drawn:
        return _skip(f"{where} draws no part named {CONTROL_PREFIX}<id>: name each control's "
                     f"part {CONTROL_PREFIX}<id> (node {CONTROL_PREFIX}<id>__0), or this gate "
                     f"cannot tell a cap from the housing")
    raw_grips = ctx.param("hand_grips")
    diameter = ctx.param("hand_grip_diameter_mm")
    try:
        normals = _normals(list(raw_controls), controls)
        grips = A.parse_grips(raw_grips) if raw_grips is not None else {}
    except A.LayoutError as exc:
        return _error(str(exc))
    if grips and diameter is None:
        return _skip("no hand_grip_diameter_mm: the declared handle diameter each grip's "
                     "drawn section is held to")
    if grips and (not A._real(diameter) or not float(diameter) > 0):
        return _error(f"hand_grip_diameter_mm is {diameter!r}; it is a handle diameter in mm, "
                      f"more than 0")

    declared: dict[str, str] = {}
    for control in controls:
        mover = NAMES.sanitise(CONTROL_PREFIX + control.id)
        if mover in declared:
            return _error(f"controls {declared[mover]!r} and {control.id!r} both name part "
                          f"{mover}: give them ids that differ after cad-solid's sanitising")
        declared[mover] = control.id

    problems: list[str] = []
    rows: dict[str, dict] = {}
    for control in controls:
        mover = NAMES.sanitise(CONTROL_PREFIX + control.id)
        if mover not in parts:
            problems.append(f"control {control.id} is declared and not drawn (no part "
                            f"{mover}, node {NAMES.node(mover, 0)})")
            rows[control.id] = {"drawn": False, "part": mover}
            continue
        top = _cap_top(parts[mover], normals[control.id])
        off = _up(math.dist(top, control.at))
        rows[control.id] = {"drawn": True, "part": mover,
                            "nodes": [node for node, _mesh in parts[mover]],
                            "declared_at": list(control.at),
                            "normal": [_r(v) for v in normals[control.id]],
                            "drawn_top": [_r(v) for v in top], "off_mm": off}
    for mover in sorted(drawn - set(declared)):
        nodes = ", ".join(node for node, _mesh in parts[mover])
        problems.append(f"part {mover} ({nodes}) is drawn and not declared in hand_controls")

    housing = [(node, mesh) for mover in sorted(parts) if mover not in drawn
               for node, mesh in parts[mover]]
    grip_rows: dict[str, dict] = {}
    allowed = (float(diameter) + GIRTH_TOL_MM) if grips else None
    for grip_id in sorted(grips):
        grip = grips[grip_id]
        try:
            section = _section(housing, grip.centre, grip.handle_axis)
        except ValueError as exc:
            return _error(f"grip {grip_id}: {exc}: its handle's section cannot be measured "
                          f"(cad-solid's cad.is_volume says why)")
        if section is None:
            problems.append(f"no drawn part surrounds grip {grip_id}'s centre in its section "
                            f"square to the handle axis")
            grip_rows[grip_id] = {"surrounds_centre": False}
            continue
        across = _up(section["across"])
        off = _up(section["off"])
        grip_rows[grip_id] = {"surrounds_centre": True, "section_diameter_mm": across,
                              "section_chorded_mm": _r(section["chorded"]),
                              "section_steps": (round(section["steps"], 1)
                                                if math.isfinite(section["steps"]) else None),
                              "declared_centre": list(grip.centre),
                              "drawn_centre": [_r(v) for v in section["drawn_centre"]],
                              "centre_off_mm": off}
        if across > allowed:
            problems.append(f"grip {grip_id}'s section is {across:.2f} mm across, more than "
                            f"the declared {float(diameter):g} mm + {GIRTH_TOL_MM:.1f}")
        if off > CENTRE_TOL_MM:
            problems.append(f"grip {grip_id}'s centre is {off:.2f} mm off its drawn handle's "
                            f"axis (the centroid of its section), more than "
                            f"{CENTRE_TOL_MM:g} mm")

    placed = [cid for cid, row in rows.items() if row["drawn"]]
    # The first of the worst in declared order: `max` keeps the first it meets.
    worst_id = max(placed, key=lambda cid: rows[cid]["off_mm"]) if placed else None
    worst = rows[worst_id]["off_mm"] if worst_id else 0.0
    worst_words = (f"worst cap {worst:.2f} mm off ({worst_id}, node "
                   f"{rows[worst_id]['nodes'][0]})" if worst_id else "no cap drawn")
    grip_words = (", ".join(f"{gid} {row['section_diameter_mm']:.2f} mm"
                            if row["surrounds_centre"] else f"{gid} no section"
                            for gid, row in sorted(grip_rows.items()))
                  + f" across vs declared {float(diameter):g} mm + {GIRTH_TOL_MM:.1f}"
                  if grips else "no grip declared, no section checked")
    centred = [gid for gid, row in sorted(grip_rows.items()) if row["surrounds_centre"]]
    if centred:
        # The first of the worst in sorted order, as for the caps.
        far = max(centred, key=lambda gid: grip_rows[gid]["centre_off_mm"])
        grip_words += (f"; worst grip centre {grip_rows[far]['centre_off_mm']:.2f} mm off its "
                       f"handle's axis ({far}) vs <= {CENTRE_TOL_MM:g} mm")
    counts = (f"{len(placed)} of {len(controls)} controls drawn, "
              f"{len(drawn - set(declared))} drawn and not declared")
    source = f"{where}, {sum(len(v) for v in parts.values())} nodes"
    if problems:
        named = "; ".join(problems[:_NAMED])
        more = f"; and {len(problems) - _NAMED} more" if len(problems) > _NAMED else ""
        measured, limit, units = float(len(problems)), 0.0, "disagreements"
        detail = (f"{len(problems)} disagreement(s) vs <= 0: {named}{more}; {worst_words} "
                  f"vs <= {POSITION_TOL_MM:g} mm; {grip_words}; {counts}; {source}")
    else:
        measured, limit, units = worst, POSITION_TOL_MM, "mm"
        detail = (f"{worst_words} vs <= {POSITION_TOL_MM:g} mm; {counts}; {grip_words}; "
                  f"{source}")
    passed = measured <= limit
    evidence = _evidence(ctx, {
        "scene": where,
        "controls": rows,
        "grips": grip_rows,
        "declared_diameter_mm": float(diameter) if grips else None,
        "disagreements": problems,
        "position_tol_mm": POSITION_TOL_MM,
        "girth_tol_mm": GIRTH_TOL_MM,
        "centre_tol_mm": CENTRE_TOL_MM,
    })
    return Verdict(gate=GID, passed=passed, measured=measured, limit=limit, comparator="<=",
                   units=units, detail=detail, evidence=evidence)
