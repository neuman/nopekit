# SPDX-License-Identifier: Apache-2.0
"""Tier-1 mesh gates: overhang angle and unsupported bridge span.

These two need the actual triangles, so they need ``trimesh`` and ``numpy``. Both
are declared in ``requires_python`` and imported LAZILY inside the gate bodies, so
this module imports cleanly on a machine that has neither. When they are absent
the spine's ``availability()`` reports SKIPPED with the missing module named, the
claim resolves BLOCKED, and it stays visible in the readiness report. That is the
whole point: a gate that quietly downgraded to a cheaper approximation would turn
"we did not check this" into "this is fine" (rule 4).

Frames and units
----------------
Lengths are mm. The mesh is read in its **print orientation**: the model is
responsible for exporting the part the way it will sit on the bed. The build
direction is ``build_axis`` from the projection, defaulting to +Z; every angle
below is measured against that axis, so a project that prints along a different
axis gets the right answer without re-exporting.

Angle convention: the **overhang angle** of a face is its inclination away from
the build direction, in degrees. A vertical wall is 0 deg. A 45 deg chamfer
underside is 45 deg. A flat ceiling is 90 deg. This is the number a slicer's
"support overhang threshold" refers to.

Both gates read the mesh through :func:`_read_mesh`, which refuses one that is not
a solid — not watertight, or wound inside out. That refusal is a FAIL and not a
skip: every number below is read off a face normal, and a face normal only means
"outward" if the winding says so. See :func:`_solid_defect`.

One part or many
----------------
Both gates accept a **part set** — ``mesh_paths`` as a mapping of name -> path, or
any of the spellings in ``fdm_print_parts.PART_SET_KEYS`` — and return ONE verdict
over it: the worst part's number as ``measured``, the worst part NAMED in the
detail with a count of how many were checked and how many are past the limit, a
locator per offending part, and the per-part table in ``evidence``. A part whose
mesh cannot be read is reported as unmeasured in that verdict and stays in the
denominator; it is never dropped from it.

With a single ``mesh_path`` and no set, both gates run exactly the path they
always have. That is not an accident of the implementation — it is the path this
pack's negative controls exercise, and "a set of one" would have moved it.
"""
from __future__ import annotations

import math
import os
from typing import Any, Iterable, Sequence

from atompipe.gates import gate, GateContext
from atompipe.modelio import load_path as _load_path
from atompipe.models import Locator, NegativeControl, Tier, Verdict

# The helpers below are loaded BY PATH, through the spine's loader. ``load_path``
# names a module after its absolute path, so this module and
# ``gates/printability.py``, asking for one file, get one module — and a second
# copy of this pack in the same process (a project's shadow, a user pack) gets
# its own. What slipped through before: the fold was loaded under a fixed name
# (``atompipe_pack_fdm_print__fold``) and the part set by a bare ``import`` off
# ``sys.path``, and both were served out of ``sys.modules`` by that name. A twin
# of this pack ran the first copy's fold and part set while every message named
# the twin, and never ran an edit to its own (S-26, packs:H4). The loader also
# compiles the bytes on disk and reuses a module only while those bytes are
# unchanged, and needs no ``sys.path`` entry at load time or at fixture time.
_HERE = os.path.dirname(os.path.abspath(__file__))

# What the part is called once ``views/part.py`` has drawn it, and where its mesh
# is. Shared rather than restated: the node name a locator carries is an INTERFACE
# between this file and that one.
PARTS = _load_path(os.path.join(os.path.dirname(_HERE), "fdm_print_parts.py"))

# One verdict over a set of parts. See its docstring for why the single-part path
# below is left exactly as it was. ``gates/printability.py`` gets this same
# module: two copies would each carry their own ``MAX_PART_LOCATORS`` and their
# own idea of what a ``PartOutcome`` is, and the drift would be silent (rule 2).
FOLD = _load_path(os.path.join(_HERE, "fdm_print_fold.py"))

#: How many faces get a pin before the gate stops drawing them. A part that needs
#: support usually has thousands of faces past the limit; pinning them all paints
#: the model red and tells a reader nothing they could not see from the colour.
#: The area fraction in ``measured`` is always the real one — the cap trims the
#: drawing, never the measurement.
MAX_FACE_LOCATORS = 12

# --------------------------------------------------------------------------- #
# domain constants
# --------------------------------------------------------------------------- #

OVERHANG_LIMIT_DEG_DEFAULT = 45.0
"""Steepest face a machine will print without support, degrees from vertical.

45 deg is the classic threshold and the conservative one: each layer is offset by
half a bead and still lands on solid material beneath it. Well-cooled PLA on a
part-cooling-fan machine reaches 50-55; ABS in a hot chamber with weak cooling
does not reach 45. Projects override ``overhang_limit_deg`` and should record
which machine and material the number came from. Rejected: 60 deg as a default —
it is reachable on a good machine with a tuned profile, and adopting it as the
default means every pack user inherits one person's tuning."""

OVERHANG_AREA_ALLOW_FRAC_DEFAULT = 0.005
"""Overhanging area tolerated, as a fraction of total surface area.

One face past the limit is a nub; a hundred is a surface. A tessellated cylinder
or fillet will always have a sliver of geometry a fraction of a degree past any
threshold, and failing a part for that is how a gate gets switched off. Half a
percent of surface area is small enough that a real unsupported region — the
underside of a boss, a horizontal hole's ceiling — clears it easily, and large
enough that tessellation noise does not. The verdict always reports the worst
angle, the face count and the absolute area, so a nub is visible even when it
does not fail."""

BRIDGE_CEILING_DEG_DEFAULT = 80.0
"""Overhang angle at which a face stops being a steep wall and starts being a
ceiling to bridge across. Above 80 deg the slicer's bridging logic takes over from
its support logic; below it the material is still climbing."""

MAX_BRIDGE_MM_DEFAULT = 30.0
"""Unsupported horizontal span a machine will bridge acceptably, mm.

30 mm is the span at which a well-cooled bridge is still flat enough to build on.
Longer bridges print — 50 to 100 mm is commonly demonstrated — but they sag, the
first solid layer above them is lumpy, and anything dimensional on that face is
lost. Chamfering the span to 45 deg or splitting the part is almost always
cheaper than supporting it. Projects override ``max_bridge_mm``.

This figure applies to a **bridge** — a ceiling anchored on opposite sides, where
the strand is pulled taut between two landings. It is not the allowance for a
cantilever; see ``MAX_CANTILEVER_MM_DEFAULT``."""

MAX_CANTILEVER_MM_DEFAULT = 2.0
"""Unsupported horizontal projection off a SINGLE anchor, mm.

A bridge and a cantilever are not the same problem and must not share a number. A
bridge is a strand under tension between two landings: it is pulled straight, it
cools straight, and 30 mm of it is routine. A cantilevered ceiling is anchored at
one end and lands on nothing, so the strand has no second attachment to pull
against — it droops under its own weight the moment it leaves the nozzle, curls up
into the path of the next pass, and the layer above lands on a ridge. The length
that survives is roughly the length that cools before it sags, which on ordinary
part cooling is a couple of millimetres, not tens.

2 mm is a little over four beads at a 0.42 mm line width, which is about what a
horizontal hole's crown or a small ledge gets away with unsupported. Rejected:
0 mm — every tessellated fillet and every hole crown produces a sliver of
one-sided ceiling, and a gate that fails all of them is a gate people switch off;
and 5 mm, which is demonstrably printable but arrives visibly curled and is not
something to certify silently. Projects override ``max_cantilever_mm`` with the
material and cooling it was measured on."""

CANTILEVER_HALF_PLANE_TOL_DEG = 5.0
"""How far the anchors around the worst point may fall short of surrounding it
before the region is called a cantilever, degrees.

The test is whether the directions from the worst point to its anchors leave a
gap wider than a half-plane: 180 deg exactly for a perfect bridge (two anchors
dead opposite), more when everything holding the region up is on one side. The
tolerance keeps a bridge whose two landings are slightly skew — a tapered slot, a
tessellated arch — from tipping into the cantilever branch on a fraction of a
degree. Rejected: 0 (the perfect-bridge case sits exactly on the boundary and
floating point puts it on either side of it) and 45 (wide enough to let a genuine
corner ledge, whose two perpendicular walls span 90 deg, be scored as a bridge)."""

BED_TOLERANCE_MM = 0.05
"""How close to the lowest point a face must be to count as sitting ON the bed.

This exclusion is not cosmetic. The bottom face of any part is, geometrically, a
90 deg overhang facing straight down; without excluding it every solid ever
modelled fails the overhang gate and the gate becomes noise."""

_MISSING = object()


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _look(ctx: GateContext, names: Sequence[str], default: Any = _MISSING) -> Any:
    """First of ``names`` the projection states, else ``default``.

    Delegates to :meth:`GateContext.first_pack_param`: **every pack-scoped
    spelling (``fdm.<name>``) in the order given, then every bare spelling in the
    order given.** One resolution order, documented in one place, so a project
    that has to disambiguate between two packs can and a project that does not
    never notices.
    """
    return ctx.first_pack_param(names, default)


def _skip(gate_id: str, reason: str) -> Verdict:
    return Verdict(gate=gate_id, passed=False, skipped=True, skip_reason=reason)


#: Axis NAMES accepted anywhere this pack wants a direction, alongside the
#: ``[x, y, z]`` vector form. ``build_axis: "z"`` is what a person writes, and a
#: reader who writes it must not be told the key is missing.
AXIS_NAMES = {"x": (1.0, 0.0, 0.0), "y": (0.0, 1.0, 0.0), "z": (0.0, 0.0, 1.0)}

#: Where the build direction is stated, primary spelling first. Resolved
#: pack-scoped first (``fdm.build_axis``) by ``GateContext.param``.
BUILD_AXIS_KEYS = ("build_axis", "layer_normal", "print_axis")


def _direction(value: Any) -> list[float] | None:
    """A direction from an ``[x, y, z]`` vector OR an axis name, else None.

    Accepted: ``[0, 0, 1]``, ``"z"``, ``"+Z"``, ``"-y"``. Kept in step with
    ``gates/printability.py``'s ``_direction`` — two gates in one pack disagreeing
    about what ``build_axis: "z"`` means would be worse than neither accepting it.
    """
    if isinstance(value, str):
        text = value.strip().lower().replace(" ", "").replace("-axis", "").replace("_axis", "")
        sign = 1.0
        if text[:1] in "+-":
            sign = -1.0 if text[0] == "-" else 1.0
            text = text[1:]
        base = AXIS_NAMES.get(text)
        return [sign * c for c in base] if base else None
    if isinstance(value, (str, bytes)) or not isinstance(value, Iterable):
        return None
    try:
        out = [float(v) for v in value]
    except (TypeError, ValueError):
        return None
    return out if len(out) == 3 else None


def _build_axis(ctx: GateContext):
    """Unit build direction and an orthonormal basis for the bed plane.

    Returned as plain tuples so the caller can hand them to numpy without this
    helper importing it.
    """
    raw = _look(ctx, BUILD_AXIS_KEYS, None)
    vec = [0.0, 0.0, 1.0]
    candidate = _direction(raw)
    if candidate and math.sqrt(sum(c * c for c in candidate)) > 0:
        vec = candidate
    n = math.sqrt(sum(c * c for c in vec))
    return tuple(c / n for c in vec)


def _parse_mesh(path: str):
    """``(mesh, defect, empty)`` for the file at ``path``, or None when there is none.

    The loader :func:`_read_mesh` hands the spine's memo, so it runs once per
    file per sweep and every gate that asks after that gets the same answer.
    ``defect`` is decided here, with the parse, because it is a property of the
    file and both gates want it. ``empty`` is the reason when the file parsed to
    no faces at all; ``mesh`` and ``defect`` are None then.

    Module-level on purpose: the memo is keyed on the loader's identity, and a
    function defined inside a gate body is a new object on every call, so it
    would never hit. A missing file comes back as None rather than raising: its
    absence is what the gate decides on, and asking through the memo is what
    records the path as this gate's input either way.
    """
    if not os.path.isfile(path):
        return None
    import trimesh                                    # noqa: PLC0415 - lazy on purpose

    loaded = trimesh.load_mesh(path, process=True)
    if isinstance(loaded, trimesh.Scene):
        try:
            loaded = loaded.dump(concatenate=True)
        except TypeError:                              # older trimesh
            loaded = trimesh.util.concatenate(loaded.dump())
    if getattr(loaded, "faces", None) is None or len(loaded.faces) == 0:
        return None, None, f"{os.path.basename(path)} loaded with no faces"
    return loaded, _solid_defect(loaded, os.path.basename(path)), None


def _read_mesh(ctx: GateContext, raw: str, label: str):
    """``(mesh, path, unreadable, defect)`` for one stated mesh path.

    The one place in this pack that reads a mesh, so both gates and both modes —
    one part or thirteen — agree on what "unreadable" and "not a solid" mean.
    ``label`` is how the reason names the source of the path: ``mesh_path`` for
    the single-part projection, the part's own name for a member of a set.

    ``unreadable`` is ``(short, long)`` or None. Two spellings because they have
    different budgets: one gate reporting one part can afford the resolved
    absolute path, and one verdict reporting a dozen unread parts cannot — the
    spine caps a verdict line, and a reason that runs out of room before it says
    which part is a reason that sent the reader nowhere. Both name the same file.

    ``unreadable`` and ``defect`` are never both set. When ``defect`` is set the
    mesh comes back anyway, because the caller reports its signed volume as the
    measured quantity — that number is exactly what shows the winding is inverted.

    **Each file is parsed once per sweep**, through ``ctx.load_file`` — the
    spine's per-sweep memo — so thirteen parts through both mesh gates cost
    thirteen parses rather than twenty-six: ``fdm.overhang`` and
    ``fdm.bridge_span`` read the same files back to back, and a tier-1 sweep that
    takes forty seconds is a sweep somebody runs less often (rule 10). The memo
    re-parses a file whose bytes moved between two gates, and it records the file
    as an input of EVERY gate that asks, hit or miss. What slipped through before
    it: this pack kept its own cache on the shared ``ctx.extra``, keyed on
    ``os.stat``, and a hit opened nothing — ``os.stat`` raises no audit event — so
    ``fdm.bridge_span``'s verdict named no file among its inputs at all (S-27).
    The old cache was capped at 64 meshes because the context it lived on could
    outlive its sweep; the memo cannot — ``run_all`` makes one per sweep and
    drops it — so there is no cap to carry over. The mesh a hit returns is
    shared: nothing here may change it in place.

    A context no sweep made — a hand-run script, a test, an older spine — carries
    no memo, and then the file is simply parsed here.
    """
    path = str(raw)
    if not os.path.isabs(path):
        path = os.path.join(ctx.root or os.curdir, path)
    if getattr(ctx, "memo", None) is None:
        parsed = _parse_mesh(os.path.abspath(path))
    else:
        parsed = ctx.load_file(path, loader=_parse_mesh)
    if parsed is None:
        return None, path, (f"no file at {raw}",
                            f"{label} points at {path}, which does not exist — "
                            f"nothing was measured"), None
    mesh, defect, empty = parsed
    if empty is not None:
        return None, path, (empty, empty), None
    return mesh, path, None, defect


def _load_mesh(ctx: GateContext, gate_id: str):
    """``(mesh, path, None)`` or ``(None, None, verdict)`` — the SINGLE-part path.

    A missing ``mesh_path`` and a path that does not exist both SKIP rather than
    error: in both cases the gate did not measure the design, and the claim must
    resolve BLOCKED rather than FAIL. A mesh that exists and will not parse is a
    different animal and is allowed to raise — that is a broken artifact, and a
    broken artifact is a real finding.

    A mesh that loads but is **not a solid** is the third case, and it FAILS
    rather than skipping — see :func:`_solid_defect`.
    """
    path = _look(ctx, PARTS.MESH_PATH_KEYS, _MISSING)
    if path is _MISSING:
        return None, None, _skip(
            gate_id,
            "the projection has no mesh_path / stl_path (the exported part mesh in "
            "its print orientation) and no mesh_paths / parts set; generate the "
            "mesh and add the path to the model",
        )

    mesh, resolved, unreadable, defect = _read_mesh(ctx, str(path), "mesh_path")
    if unreadable is not None:
        return None, None, _skip(gate_id, unreadable[1])
    if defect is not None:
        return None, None, Verdict(
            gate=gate_id, passed=False, measured=round(float(mesh.volume), 1),
            limit=0.0, units="mm^3 signed volume", detail=defect,
        )
    return mesh, resolved, None


def _solid_defect(mesh, name: str) -> str | None:
    """A one-line description of why this mesh is not a solid, or None.

    **Why this FAILS the gate rather than skipping it.** Every number both mesh
    gates produce is read off ``face_normals``, and a face normal only means
    "outward" if the winding says so. Flip the winding and the signed volume goes
    negative, every overhang angle comes out mirrored, and a part covered in 70
    deg overhangs reports ``worst face 0.0 deg`` — a confident green about a
    quantity that was never measured. That is not "the gate could not run", which
    is what a skip means; it is a defect in the artifact the project handed over,
    and it is exactly the kind of finding a readiness report exists to carry.

    The pack's own fixture builder has asserted this on the solids it generates
    since the day it was written, with a comment explaining that mirrored normals
    would make a control "work" for the wrong reason. The project's mesh gets the
    same check, for the same reason.

    ``is_winding_consistent`` is deliberately NOT part of the test: an STL is a
    soup of independent triangles, and a perfectly good solid comes back from the
    round trip with that flag false while ``is_watertight`` and a positive signed
    volume both hold. Testing it would fail honest meshes, and a gate that fails
    honest input gets switched off.
    """
    import numpy as np                                 # noqa: PLC0415

    if not mesh.is_watertight:
        edges = np.sort(mesh.edges, axis=1)
        _, counts = np.unique(edges, axis=0, return_counts=True)
        open_edges = int((counts != 2).sum())
        return (f"{name} is not watertight: {open_edges} edge(s) are not shared by "
                f"exactly two faces, so it has holes or self-intersects. Inside and "
                f"outside are undefined on such a mesh, which makes every face "
                f"normal — and so every overhang and bridge number below — "
                f"meaningless. Repair the mesh or re-export it as a solid")
    if mesh.volume <= 0:
        return (f"{name} has non-positive signed volume ({mesh.volume:.1f} mm^3): its "
                f"faces are wound inside out. Every overhang angle would come out "
                f"mirrored, so a part that is nothing but steep overhangs reports "
                f"0.0 deg and passes. Flip the normals (trimesh: fix_normals) and "
                f"re-export")
    return None


def _face_angles(mesh, axis):
    """Overhang angle per face, in degrees, against the build axis.

    ``asin`` of the downward component of the unit normal: 0 for a vertical wall,
    90 for a face pointing straight at the bed. Upward-facing faces come back
    negative and are simply never selected.
    """
    import numpy as np                                 # noqa: PLC0415

    b = np.asarray(axis, dtype=float)
    comp = np.clip(mesh.face_normals @ b, -1.0, 1.0)
    return np.degrees(np.arcsin(-comp)), comp


def _anchors_surround(vectors, tol_deg: float) -> bool:
    """Do these directions surround the origin, or do they all lie to one side?

    ``vectors`` are the in-plane directions from the worst point of a ceiling
    region to the nearest point on each of its anchored edges. Sort them by angle
    and look at the widest gap between neighbours: a point held between two
    landings has a gap of exactly 180 deg (the two anchors are opposite); a point
    hanging off a single wall — or off two walls meeting at a corner — leaves
    everything on one side and a gap wider than a half-plane.

    That distinction is the whole physical difference between a bridge and a
    cantilever, and it is why this is a direction test rather than a count of
    anchored edges. A ceiling's wall side is triangulated into several boundary
    edges, so "one anchored edge" and "anchored on one side" are not the same
    statement, and counting edges gets an L-shaped corner ledge wrong.
    """
    import numpy as np                                 # noqa: PLC0415

    v = np.asarray(vectors, dtype=float)
    v = v[np.linalg.norm(v, axis=1) > 1e-9]
    if len(v) == 0:
        return True                                    # the point IS an anchor
    ang = np.sort(np.degrees(np.arctan2(v[:, 1], v[:, 0])))
    gaps = np.append(np.diff(ang), ang[0] + 360.0 - ang[-1])
    return float(gaps.max()) <= 180.0 + tol_deg


def _on_bed(mesh, axis):
    """Boolean mask of faces lying in the first layer, which are not overhangs."""
    import numpy as np                                 # noqa: PLC0415

    b = np.asarray(axis, dtype=float)
    height = mesh.vertices @ b
    lowest = float(height.min())
    return height[mesh.faces].max(axis=1) <= lowest + BED_TOLERANCE_MM


# --------------------------------------------------------------------------- #
# fdm.overhang
# --------------------------------------------------------------------------- #
@gate(
    id="fdm.overhang",
    title="Unsupported faces within the printable overhang angle",
    claims=["manufacturability", "fdm", "additive", "printability"],
    tier=Tier.BUILD,
    settles="overhang angle",
    requires_python=["trimesh", "numpy"],
    # Prerequisite fdm.process_model_valid (P2.2-D12): it checks the units and the process
    #    envelope this gate's arithmetic assumes; a part described in metres reads
    #    as a confident answer here about a different object.
    #    Isolated: the guard passes this gate's own known-bad control
    #    (test_packs.ControlsAreIsolated).
    needs=["fdm.process_model_valid"],
    negative_control=NegativeControl(
        fixture="selftest/bad_meshes.py:steep_cone",
        note="a cone stood on its apex with a 70-degree half angle: every side "
             "face is 70 deg from vertical, well past the limit, and nothing "
             "else about the solid is wrong — it is watertight, on the bed, and "
             "the right size",
    ),
)
def overhang(ctx: GateContext) -> Verdict:
    """Face normals past the unsupported-angle limit, by worst angle AND by area.

    Both numbers are reported because they mean different things. The worst angle
    says how bad the geometry gets; the area says how much of the part is like
    that. One face at 89 deg is a nub that will print as a small blob. A hundred
    faces at 60 deg is a surface that will print as strings, and the part either
    needs support (and the scar that comes with it) or a different orientation.

    The gate fails on AREA, against ``OVERHANG_AREA_ALLOW_FRAC_DEFAULT``, because
    that is the number that decides whether the part is printable. The angle is in
    the verdict so a human can tell a nub from a surface without opening the mesh.

    Two classes of face are excluded, both deliberately:

    * **First-layer faces.** The bottom of the part is a 90 deg overhang onto the
      bed, which is where it is supposed to be.
    * **Flat ceilings**, at or past ``BRIDGE_CEILING_DEG_DEFAULT``. A slicer does
      not support those, it bridges them, and whether that works is a question
      about span rather than angle. ``fdm.bridge_span`` owns them.

    The ceiling exclusion has a reporting trap in it and the verdict closes it.
    A part whose only downward geometry is a flat ceiling has nothing left in this
    gate's selection, so the worst *supportable* angle is 0.0 deg — true, and read
    on its own it says "this part has no overhangs" about a part with a 90 deg face
    on it. So the verdict reports BOTH: the worst angle this gate judged, and the
    steepest downward face on the part regardless of who owns it.

    It also refuses to run at all when ``overhang_limit_deg`` is set at or past
    ``bridge_ceiling_deg``, because the two selections then have no gap between
    them and no geometry can trip the gate — see :func:`_inert_overhang`.

    Given a part SET (``mesh_paths`` / ``parts``) it measures every member and
    returns one verdict naming the worst — see :func:`_overhang_over_set`. With a
    single ``mesh_path`` it runs exactly as it always has.

    What this does NOT settle: whether supports can be *removed* afterwards. A
    steep face inside a closed pocket passes this gate and traps its support
    forever. See ``lenses.md``.
    """
    axis = _build_axis(ctx)
    limit = float(_look(ctx, ("overhang_limit_deg", "max_overhang_deg"),
                        OVERHANG_LIMIT_DEG_DEFAULT))
    allow = float(_look(ctx, ("overhang_area_allow_frac",),
                        OVERHANG_AREA_ALLOW_FRAC_DEFAULT))
    ceiling_deg = float(_look(ctx, ("bridge_ceiling_deg",),
                              BRIDGE_CEILING_DEG_DEFAULT))

    # Many parts, one verdict. Resolved before anything is opened, because the set
    # decides WHICH files get opened. An empty set means the projection describes
    # a single part, and the original path below runs untouched.
    parts = PARTS.part_set(ctx)
    if parts.problem:
        return _skip("fdm.overhang", parts.problem)
    # A bbox-only set states no geometry, and an overhang angle is read off a face
    # normal. Measuring the single stated mesh is strictly more informative than
    # reporting N unmeasurable parts, so the set is only taken when it can answer.
    if parts and PARTS.has_geometry(parts):
        if limit >= ceiling_deg:
            return _inert_overhang(limit, ceiling_deg)
        return _overhang_over_set(ctx, parts, axis, limit, allow, ceiling_deg)

    mesh, mesh_path, bail = _load_mesh(ctx, "fdm.overhang")
    if bail is not None:
        return bail

    if limit >= ceiling_deg:
        return _inert_overhang(limit, ceiling_deg)

    m = _overhang_measure(mesh, axis, limit, ceiling_deg)
    report = _overhang_faces_file(ctx, ("fdm-overhang.txt",), mesh, mesh_path,
                                  axis, limit, m)

    # Pin the faces. This gate is the one place in the pack that knows a POSITION
    # rather than a part: a face centroid is a real point in the model frame, and
    # views/part.py exports the mesh untransformed so it is the same point in the
    # GLB. "3.4% of the surface is past the limit" is a number to act on; the same
    # verdict with pins on the faces is the underside of the part, lit up.
    locators: list[Locator] = []
    if m["frac"] > allow and m["n_past"]:
        locators = _overhang_face_locators(mesh, m, limit,
                                           PARTS.node(PARTS.mover(ctx)),
                                           MAX_FACE_LOCATORS)

    return Verdict(
        gate="fdm.overhang",
        passed=m["frac"] <= allow,
        measured=round(m["frac"], 5),
        limit=round(allow, 5),
        units="area fraction",
        detail=_overhang_note(m, limit, allow, ceiling_deg),
        evidence=[report],
        locators=locators,
    )


def _inert_overhang(limit: float, ceiling_deg: float) -> Verdict:
    """Not a measurement, a refusal.

    Every face past ``limit`` is also past ``ceiling_deg``, so it is handed to
    ``fdm.bridge_span`` as a ceiling and this gate's selection is empty by
    construction: it would return "0 faces past it, 0.00% of surface" on a part
    made entirely of 90 deg overhangs and call that a pass. A gate that cannot
    fail is a logger (rule 5), so it says so instead of returning a green it has
    not earned.

    It is a property of the configuration and not of any one part, so a set of
    thirteen is refused once rather than thirteen times — and refused before any
    mesh is opened, because none of them could change the answer.
    """
    return Verdict(
        gate="fdm.overhang", passed=False, measured=round(limit, 1),
        limit=round(ceiling_deg, 1), units="deg",
        detail=f"INERT CONFIGURATION: overhang_limit_deg {limit:.0f} is at or past "
               f"bridge_ceiling_deg {ceiling_deg:.0f}, so every face this gate could "
               f"fail on is already excluded as a ceiling and no geometry can ever "
               f"trip it. Lower overhang_limit_deg below bridge_ceiling_deg, or "
               f"raise bridge_ceiling_deg if the machine really does bridge from "
               f"{limit:.0f} deg",
    )


def _overhang_measure(mesh, axis, limit: float, ceiling_deg: float) -> dict:
    """Every number this gate reports, for one mesh. It decides nothing.

    Split out of the gate body so the one-part path and the many-part path run
    the same measurement rather than two that can drift (rule 2). The gate
    applies the threshold; this says what the part is.

    Two classes of face are excluded, both deliberately:

    * **First-layer faces.** The bottom of the part is a 90 deg overhang onto the
      bed, which is where it is supposed to be.
    * **Flat ceilings**, at or past ``ceiling_deg``. A slicer does not support
      those, it bridges them, and whether that works is a question about span
      rather than angle. ``fdm.bridge_span`` owns them.
    """
    angles, _ = _face_angles(mesh, axis)
    areas = mesh.area_faces
    total_area = float(areas.sum())
    bed = _on_bed(mesh, axis)

    ceilings = (angles >= ceiling_deg) & (~bed)      # bridges, not supports
    past = (angles > limit) & (~ceilings) & (~bed)
    n_past = int(past.sum())
    area_past = float(areas[past].sum())
    frac = area_past / total_area if total_area > 0 else 0.0
    candidates = angles[(angles > 0.0) & (~ceilings) & (~bed)]
    worst = float(candidates.max()) if candidates.size else 0.0
    # The worst angle INCLUDING the ceilings this gate handed away. Without it the
    # verdict on a part whose only downward geometry is a flat ceiling reads
    # "worst face 0.0 deg" — true of what this gate measured and badly misleading
    # about the part, which has a 90 deg face on it.
    downward = angles[(angles > 0.0) & (~bed)]
    worst_all = float(downward.max()) if downward.size else 0.0
    return {
        "angles": angles, "areas": areas, "past": past, "n_past": n_past,
        "area_past": area_past, "total_area": total_area, "frac": frac,
        "worst": worst, "worst_all": worst_all,
        "n_ceilings": int(ceilings.sum()), "n_faces": int(len(mesh.faces)),
    }


def _overhang_note(m: dict, limit: float, allow: float, ceiling_deg: float) -> str:
    """The dense line this gate says about one part.

    Verbatim in a single-part verdict; the worst part's copy of it is the
    headline of a multi-part one.
    """
    return (f"worst supportable face {m['worst']:.1f} deg vs {limit:.0f} deg limit; "
            f"{m['n_past']} faces past it covering {m['area_past']:.1f} mm^2 = "
            f"{m['frac'] * 100:.2f}% of surface (allowance {allow * 100:.2f}%); "
            f"steepest downward face on the part {m['worst_all']:.1f} deg, "
            f"{m['n_ceilings']} at or past {ceiling_deg:.0f} deg excluded as "
            f"ceilings — they are fdm.bridge_span's")


def _overhang_faces_file(ctx: GateContext, where, mesh, mesh_path, axis,
                         limit: float, m: dict) -> str:
    """The per-face table for one part, steepest first. ``where`` is an out_path."""
    import numpy as np                                 # noqa: PLC0415

    angles, areas, past = m["angles"], m["areas"], m["past"]
    report = ctx.out_path(*where)
    order = np.argsort(-angles * past)
    with open(report, "w", encoding="utf-8") as fh:
        fh.write(f"# fdm.overhang — {mesh_path}\n")
        fh.write(f"# build axis {axis}, limit {limit} deg, {m['n_faces']} faces, "
                 f"{m['total_area']:.1f} mm^2 total surface\n")
        fh.write(f"# {m['n_past']} faces past the limit, {m['area_past']:.2f} mm^2 "
                 f"({m['frac'] * 100:.3f}% of surface)\n")
        fh.write("face_index\tangle_deg\tarea_mm2\tcentroid_x\tcentroid_y\tcentroid_z\n")
        centroids = mesh.triangles_center
        for i in order[:200]:
            if not past[i]:
                break
            c = centroids[i]
            fh.write(f"{int(i)}\t{angles[i]:.2f}\t{areas[i]:.4f}\t"
                     f"{c[0]:.3f}\t{c[1]:.3f}\t{c[2]:.3f}\n")
    return report


def _overhang_face_locators(mesh, m: dict, limit: float, node: str,
                            budget: int) -> list[Locator]:
    """Pins on the faces past the limit, worst first.

    Two rankings, because they answer two different questions and a reader wants
    both: the STEEPEST face first (how bad does it get — an 89 deg nub prints as
    a blob), then the LARGEST-area ones (how much of the part is like this, which
    is what the gate actually fails on). Ranking by angle alone would pin twelve
    slivers and miss the shelf; by area alone it would miss the nub.

    ``budget`` is how many pins this part may have. One part on its own gets
    ``MAX_FACE_LOCATORS``, because the question is *where on this part*. One part
    out of thirteen gets ONE, because the question has become *which parts*, and
    that part's faces are in its own evidence file either way.
    """
    import numpy as np                                 # noqa: PLC0415

    angles, areas, past = m["angles"], m["areas"], m["past"]
    centroids = mesh.triangles_center
    past_idx = np.flatnonzero(past)
    by_angle = past_idx[np.argsort(-angles[past_idx])]
    by_area = past_idx[np.argsort(-areas[past_idx])]
    chosen: list[int] = []
    for index in [*by_angle[:1], *by_area]:
        if index not in chosen:
            chosen.append(int(index))
        if len(chosen) >= budget:
            break
    locators: list[Locator] = []
    for rank, index in enumerate(chosen):
        centre = centroids[index]
        locators.append(Locator(
            view=PARTS.VIEW_ID, target=node, kind="face",
            position=[round(float(c), 4) for c in centre],
            value=round(float(angles[index]), 2),
            label=f"{angles[index]:.1f} deg vs {limit:.0f} deg limit, "
                  f"{areas[index]:.2f} mm^2"
                  + (" (steepest)" if rank == 0 else ""),
        ))
    return locators


def _overhang_over_set(ctx: GateContext, parts, axis, limit: float, allow: float,
                       ceiling_deg: float) -> Verdict:
    """Every part in the set measured, one verdict, the worst part named.

    Each part gets the same measurement the single-part path runs. The per-part
    table is written whether the set passes or fails: a passing sweep's table is
    how a reader finds out, a month later, whether the part they are worried
    about was in the set at all — which is the question the "compute the worst
    part in the model and hand that one over" workaround could never answer.
    """
    outcomes = []
    faces_files: list[str] = []
    for part in parts:
        if not part.raw:
            outcomes.append(FOLD.skipped_part(
                part.name,
                "the set states no mesh for this part, and an overhang angle is "
                "read off a face normal — there is nothing to read"))
            continue
        mesh, path, unreadable, defect = _read_mesh(ctx, part.raw, part.name)
        if unreadable is not None:
            outcomes.append(FOLD.skipped_part(part.name, unreadable[0], path=path))
            continue
        if defect is not None:
            outcomes.append(FOLD.defective_part(
                part.name, defect, path=path,
                locators=[Locator(view=PARTS.VIEW_ID, target=part.node, kind="part",
                                  label="not a solid — every overhang angle read "
                                        "off it would be a fiction")]))
            continue
        m = _overhang_measure(mesh, axis, limit, ceiling_deg)
        score = (m["frac"] / allow) if allow > 0 else (
            float("inf") if m["frac"] > 0 else 0.0)
        locators: list[Locator] = []
        if m["frac"] > allow and m["n_past"]:
            # ONE pin per offending part: in a set the question is which parts,
            # and this part's own faces file carries the rest.
            locators = _overhang_face_locators(mesh, m, limit, part.node, 1)
            faces_files.append(_overhang_faces_file(
                ctx, ("fdm-overhang", f"{part.name}.txt"), mesh, path, axis, limit, m))
        outcomes.append(FOLD.measured_part(
            part.name, score=score, measured=round(m["frac"], 5),
            limit=round(allow, 5),
            note=f"at {m['frac'] * 100:.2f}% of surface past the {limit:.0f} deg "
                 f"limit vs {allow * 100:.2f}% allowed (worst face {m['worst']:.1f} "
                 f"deg, {m['n_past']} faces, {m['area_past']:.1f} mm^2)",
            path=path,
            row=f"{m['frac']:.5f}\t{m['worst']:.2f}\t{m['worst_all']:.2f}\t"
                f"{m['n_past']}\t{m['area_past']:.2f}\t{m['total_area']:.1f}",
            locators=locators))

    summary = FOLD.write_table(
        ctx, "fdm-overhang-parts.txt", "fdm.overhang", parts.source, outcomes,
        header_lines=[
            f"build axis {axis}, limit {limit} deg, allowance {allow * 100:.2f}% of "
            f"surface; faces at or past {ceiling_deg:.0f} deg are ceilings and belong "
            f"to fdm.bridge_span",
        ],
        columns=("area_frac\tworst_deg\tworst_downward_deg\tfaces_past\t"
                 "area_past_mm2\ttotal_area_mm2"))
    return FOLD.fold(
        "fdm.overhang", outcomes, source=parts.source, units="area fraction",
        quantity="overhanging area", evidence=[summary, *faces_files[:3]])


# --------------------------------------------------------------------------- #
# fdm.bridge_span
# --------------------------------------------------------------------------- #
@gate(
    id="fdm.bridge_span",
    title="Unsupported horizontal spans within the bridgeable maximum",
    claims=["manufacturability", "fdm", "additive", "printability"],
    tier=Tier.BUILD,
    settles="bridge span",   # and the cantilever case, which is the same measurement
    requires_python=["trimesh", "numpy"],
    # Prerequisite fdm.process_model_valid (P2.2-D12): it checks the units and the process
    #    envelope this gate's arithmetic assumes; a part described in metres reads
    #    as a confident answer here about a different object.
    #    Isolated: the guard passes this gate's own known-bad control
    #    (test_packs.ControlsAreIsolated).
    needs=["fdm.process_model_valid"],
    negative_control=NegativeControl(
        fixture="selftest/bad_meshes.py:long_bridge",
        note="a PLATE of three parts whose middle one is an arch: its flat "
             "ceiling spans three times the limit between two properly anchored "
             "legs, so the gate has to measure a real bridge rather than notice "
             "a cantilever, and it has to FIND it between two copies of the good "
             "baseline clamp — a fold that reported the first part, the last "
             "part or an average would pass. The span is the only thing wrong: "
             "every solid on the plate is watertight, sits on the bed and fits "
             "the build volume",
    ),
)
def bridge_span(ctx: GateContext) -> Verdict:
    """Longest unsupported horizontal span the printer would have to bridge.

    The measurement, stated plainly so a reader can judge it:

    1. Select near-horizontal downward faces — overhang angle at or past
       ``BRIDGE_CEILING_DEG_DEFAULT``. These are ceilings. Faces in the first
       layer are excluded; they are the bed.
    2. Group them into connected regions by shared edges. One region is one
       ceiling.
    3. Find each region's boundary edges, and call an edge **anchored** when the
       face on the other side of it descends — that is where the ceiling meets a
       wall that carries it down to material below. A boundary edge whose
       neighbour rises is a free edge: the ceiling simply ends there with nothing
       under it.
    4. Take the point of the region furthest, in the bed plane, from its nearest
       anchor, and ask what is holding that point up — see
       :func:`_anchors_surround`. If the anchors surround it, it is the middle of
       a **bridge** and the span is twice that distance, because the strand runs
       landing to landing. If they all lie to one side, it is the tip of a
       **cantilever**: the span is that distance once, and it is measured against
       ``MAX_CANTILEVER_MM_DEFAULT`` instead, which is more than an order of
       magnitude smaller.

    That third case is not a refinement, it is the commonest support question
    there is. A 15 mm shelf projecting off a wall into thin air used to be scored
    as "a 30 mm bridge" and compared against a bridge limit, which passed it —
    while the overhang gate had already handed its 90 deg underside away as a
    ceiling. A cantilever is not half a bridge of twice the length: there is no
    second landing, the strand is unsupported at its free end from the first pass,
    and it droops and curls instead of pulling taut.

    This is an approximation and worth knowing the edges of. It measures the
    *geometry* of the unsupported region, not what a particular slicer will do
    with it: a slicer may anchor a bridge on an infill line inside the region, or
    lay the bridge diagonally, both of which shorten the real span. It will not
    find a span that only exists between two separately-printed islands. A region
    with no anchored edge at all — a ceiling hanging off nothing — is reported as
    UNANCHORED against a limit of zero, because no span is short enough to save a
    ceiling that lands on nothing at all.

    What this does NOT settle: whether the bridge will *look* acceptable, or
    whether a dimensional feature on the bridged face survives. A 25 mm bridge
    passes and still sags half a millimetre in the middle.
    """
    axis = _build_axis(ctx)
    limit = float(_look(ctx, ("max_bridge_mm", "bridge_limit_mm"),
                        MAX_BRIDGE_MM_DEFAULT))
    cantilever_limit = float(_look(ctx, ("max_cantilever_mm",),
                                   MAX_CANTILEVER_MM_DEFAULT))
    ceiling_deg = float(_look(ctx, ("bridge_ceiling_deg",),
                              BRIDGE_CEILING_DEG_DEFAULT))

    # Many parts, one verdict; an empty set means one part and the original path.
    parts = PARTS.part_set(ctx)
    if parts.problem:
        return _skip("fdm.bridge_span", parts.problem)
    if parts and PARTS.has_geometry(parts):
        return _bridge_over_set(ctx, parts, axis, limit, cantilever_limit, ceiling_deg)

    mesh, mesh_path, bail = _load_mesh(ctx, "fdm.bridge_span")
    if bail is not None:
        return bail

    m = _bridge_measure(mesh, axis, limit, cantilever_limit, ceiling_deg)
    if m["n_sel"] == 0:
        return Verdict(
            gate="fdm.bridge_span", passed=True, measured=0.0, limit=round(limit, 2),
            units="mm",
            detail=f"no ceiling faces at or past {ceiling_deg:.0f} deg off the bed — "
                   f"nothing to bridge ({m['n_faces']} faces checked)",
        )

    report = _bridge_regions_file(ctx, ("fdm-bridge-span.txt",), mesh_path, axis,
                                 limit, cantilever_limit, ceiling_deg, m)
    return Verdict(
        gate="fdm.bridge_span",
        passed=m["worst_ratio"] <= 1.0,
        measured=round(m["worst_span"], 2),
        limit=round(m["worst_limit"], 2),
        units="mm",
        detail=_bridge_note(m, limit, cantilever_limit),
        evidence=[report],
    )


def _bridge_measure(mesh, axis, limit: float, cantilever_limit: float,
                    ceiling_deg: float) -> dict:
    """The span measurement for one mesh. It decides nothing.

    Split out of the gate body so the one-part path and the many-part path run
    the same measurement rather than two that can drift (rule 2).

    ``worst_ratio`` is span over the limit that applies to THAT region, which is
    what makes one part comparable with another: a 25 mm bridge and a 3 mm
    cantilever are not ranked by length, they are ranked by how far past their
    own allowance they are. A region with no anchor at all has a limit of zero
    and a ratio of infinity — no span is short enough to save a ceiling that
    lands on nothing.
    """
    import numpy as np                                 # noqa: PLC0415

    angles, _ = _face_angles(mesh, axis)
    sel = (angles >= ceiling_deg) & (~_on_bed(mesh, axis))
    n_sel = int(sel.sum())
    blank = {"n_sel": 0, "n_faces": int(len(mesh.faces)), "n_regions": 0,
             "worst_ratio": 0.0, "worst_span": 0.0, "worst_limit": limit,
             "worst_note": "no ceiling region", "worst_area": 0.0, "worst_h": 0.0,
             "rows": []}
    if n_sel == 0:
        return blank

    b = np.asarray(axis, dtype=float)
    u = np.array([1.0, 0.0, 0.0]) if abs(b[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
    u = u - b * (u @ b)
    u /= np.linalg.norm(u)
    v = np.cross(b, u)
    verts = mesh.vertices
    plane = np.column_stack((verts @ u, verts @ v))     # every vertex in bed coords
    height = verts @ b

    # ---- edge bookkeeping: which faces share which edge --------------------- #
    edges = mesh.edges_sorted
    _, edge_id = np.unique(edges, axis=0, return_inverse=True)
    edge_id = np.asarray(edge_id).ravel()
    face_of = np.repeat(np.arange(len(mesh.faces)), 3)
    order = np.argsort(edge_id, kind="stable")
    eid_sorted, face_sorted = edge_id[order], face_of[order]
    starts = np.searchsorted(eid_sorted, np.arange(eid_sorted[-1] + 1), side="left")
    ends = np.searchsorted(eid_sorted, np.arange(eid_sorted[-1] + 1), side="right")
    edge_verts = edges[order[starts]]                   # one vertex pair per edge id

    # ---- connected regions of ceiling faces --------------------------------- #
    parent = {int(f): int(f) for f in np.flatnonzero(sel)}

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    shared = np.flatnonzero((ends - starts) == 2)
    for eid in shared:
        a, c = int(face_sorted[starts[eid]]), int(face_sorted[starts[eid] + 1])
        if sel[a] and sel[c]:
            ra, rc = find(a), find(c)
            if ra != rc:
                parent[ra] = rc

    regions: dict[int, list[int]] = {}
    for f in parent:
        regions.setdefault(find(f), []).append(f)

    centroids = mesh.triangles_center
    areas = mesh.area_faces
    worst_ratio = -1.0
    worst_span, worst_note, worst_area, worst_h = 0.0, "no ceiling region", 0.0, 0.0
    worst_limit = limit
    rows: list[str] = []

    for root, faces in regions.items():
        faces_arr = np.asarray(faces)
        region_mask = np.zeros(len(mesh.faces), dtype=bool)
        region_mask[faces_arr] = True

        # boundary edges of the region: edges used by exactly one of its faces
        eids = edge_id.reshape(-1, 3)[faces_arr].ravel()
        uniq, counts = np.unique(eids, return_counts=True)
        boundary = uniq[counts == 1]

        anchors = []
        for eid in boundary:
            lo, hi = starts[eid], ends[eid]
            mid_h = float(height[edge_verts[eid]].mean())
            for k in range(lo, hi):
                f = int(face_sorted[k])
                if region_mask[f]:
                    continue
                if float(centroids[f] @ b) < mid_h - 1e-9:
                    anchors.append(edge_verts[eid])
                    break

        # sample the region densely enough that its middle is represented
        tri = mesh.faces[faces_arr]
        sample_idx = np.unique(tri)
        samples = np.vstack((plane[sample_idx], centroids[faces_arr] @ np.column_stack((u, v))))
        for a, c in ((0, 1), (1, 2), (2, 0)):
            samples = np.vstack((samples, 0.5 * (plane[tri[:, a]] + plane[tri[:, c]])))

        area = float(areas[faces_arr].sum())
        z = float((centroids[faces_arr] @ b).mean())

        if not anchors:
            lo, hi = samples.min(axis=0), samples.max(axis=0)
            span = float(np.linalg.norm(hi - lo))
            note, region_limit = "UNANCHORED", 0.0
        else:
            seg = np.asarray(anchors)                   # (A, 2) vertex index pairs
            p0, p1 = plane[seg[:, 0]], plane[seg[:, 1]]
            d = p1 - p0
            denom = np.einsum("ij,ij->i", d, d)
            denom[denom == 0] = 1e-12
            diff = samples[:, None, :] - p0[None, :, :]
            t = np.clip(np.einsum("ijk,jk->ij", diff, d) / denom, 0.0, 1.0)
            closest = p0[None, :, :] + t[:, :, None] * d[None, :, :]
            offset = closest - samples[:, None, :]      # (S, A, 2) toward each anchor
            dist = np.linalg.norm(offset, axis=2).min(axis=1)
            far = int(np.argmax(dist))
            reach = float(dist[far])
            if _anchors_surround(offset[far], CANTILEVER_HALF_PLANE_TOL_DEG):
                # Held on both sides: the worst point is the middle of a bridge.
                span, region_limit = 2.0 * reach, limit
                note = f"BRIDGE, {len(anchors)} anchored edge(s) around it"
            else:
                # Everything holding it up is on one side: the worst point is the
                # free tip of a cantilever and the span is its projection, once.
                span, region_limit = reach, cantilever_limit
                note = f"CANTILEVER, {len(anchors)} anchored edge(s) all to one side"

        ratio = (span / region_limit) if region_limit > 0 else (
            float("inf") if span > 0 else 0.0)
        rows.append(f"{len(faces_arr)}\t{area:.2f}\t{z:.2f}\t{span:.2f}\t"
                    f"{region_limit:.2f}\t{note}")
        if ratio > worst_ratio:
            worst_ratio = ratio
            worst_span, worst_note, worst_area, worst_h = span, note, area, z
            worst_limit = region_limit

    return {"n_sel": n_sel, "n_faces": int(len(mesh.faces)), "n_regions": len(regions),
            "worst_ratio": worst_ratio, "worst_span": worst_span,
            "worst_limit": worst_limit, "worst_note": worst_note,
            "worst_area": worst_area, "worst_h": worst_h, "rows": rows}


def _bridge_note(m: dict, limit: float, cantilever_limit: float) -> str:
    """The dense line this gate says about one part."""
    limit_note = ("no span is acceptable" if m["worst_limit"] <= 0
                  else f"{m['worst_limit']:.0f} mm limit")
    return (f"worst unsupported span {m['worst_span']:.1f} mm vs {limit_note} "
            f"({m['worst_note']}) on a {m['worst_area']:.0f} mm^2 ceiling at "
            f"{m['worst_h']:.1f} mm; {m['n_regions']} ceiling region(s) from "
            f"{m['n_sel']} faces, bridges judged at {limit:.0f} mm and cantilevers "
            f"at {cantilever_limit:.0f} mm")


def _bridge_regions_file(ctx: GateContext, where, mesh_path, axis, limit: float,
                         cantilever_limit: float, ceiling_deg: float,
                         m: dict) -> str:
    """The per-region table for one part."""
    report = ctx.out_path(*where)
    with open(report, "w", encoding="utf-8") as fh:
        fh.write(f"# fdm.bridge_span — {mesh_path}\n")
        fh.write(f"# build axis {axis}, ceiling >= {ceiling_deg} deg, bridge limit "
                 f"{limit} mm, cantilever limit {cantilever_limit} mm\n")
        fh.write(f"# {m['n_regions']} ceiling region(s) from {m['n_sel']} faces\n")
        fh.write("faces\tarea_mm2\theight_mm\tspan_mm\tlimit_mm\tanchoring\n")
        fh.write("\n".join(m["rows"]) + "\n")
    return report


def _bridge_over_set(ctx: GateContext, parts, axis, limit: float,
                     cantilever_limit: float, ceiling_deg: float) -> Verdict:
    """Every part in the set measured, one verdict, the worst part named.

    The locator here is a ``part`` pin and not a point, and that is the honest
    shape of what this gate knows. A span is measured between anchors the gate
    finds by direction; there is no single coordinate it could defend as "the
    problem", and a confident pin on the wrong spot is worse than none. But
    **which part** it is on is known exactly, and in a thirteen-part set that is
    the answer the reader needs — it was not available at all while the gate
    judged one part handed to it by the model.
    """
    outcomes = []
    region_files: list[str] = []
    for part in parts:
        if not part.raw:
            outcomes.append(FOLD.skipped_part(
                part.name,
                "the set states no mesh for this part, and a span is measured "
                "between anchors on the triangles — there is nothing to measure"))
            continue
        mesh, path, unreadable, defect = _read_mesh(ctx, part.raw, part.name)
        if unreadable is not None:
            outcomes.append(FOLD.skipped_part(part.name, unreadable[0], path=path))
            continue
        if defect is not None:
            outcomes.append(FOLD.defective_part(
                part.name, defect, path=path,
                locators=[Locator(view=PARTS.VIEW_ID, target=part.node, kind="part",
                                  label="not a solid — no span read off it would "
                                        "mean anything")]))
            continue
        m = _bridge_measure(mesh, axis, limit, cantilever_limit, ceiling_deg)
        if m["n_sel"] == 0:
            outcomes.append(FOLD.measured_part(
                part.name, score=0.0, measured=0.0, limit=round(limit, 2),
                note=f"has no ceiling face at or past {ceiling_deg:.0f} deg off the "
                     f"bed — nothing to bridge",
                path=path, row=f"0.00\t{limit:.2f}\t0\tno ceiling region"))
            continue
        ratio = m["worst_ratio"]
        locators: list[Locator] = []
        if ratio > 1.0:
            locators = [Locator(
                view=PARTS.VIEW_ID, target=part.node, kind="part",
                value=round(m["worst_span"], 2),
                label=f"{m['worst_span']:.1f} mm unsupported span vs "
                      f"{m['worst_limit']:.0f} mm ({m['worst_note'].split(',')[0]})")]
            region_files.append(_bridge_regions_file(
                ctx, ("fdm-bridge-span", f"{part.name}.txt"), path, axis, limit,
                cantilever_limit, ceiling_deg, m))
        limit_note = ("no span is acceptable" if m["worst_limit"] <= 0
                      else f"{m['worst_limit']:.0f} mm limit")
        outcomes.append(FOLD.measured_part(
            part.name, score=ratio, measured=round(m["worst_span"], 2),
            limit=round(m["worst_limit"], 2),
            note=f"at {m['worst_span']:.1f} mm unsupported vs {limit_note} "
                 f"({m['worst_note']}) on a {m['worst_area']:.0f} mm^2 ceiling at "
                 f"{m['worst_h']:.1f} mm",
            path=path,
            row=f"{m['worst_span']:.2f}\t{m['worst_limit']:.2f}\t{m['n_regions']}\t"
                f"{m['worst_note']}",
            locators=locators))

    summary = FOLD.write_table(
        ctx, "fdm-bridge-span-parts.txt", "fdm.bridge_span", parts.source, outcomes,
        header_lines=[
            f"build axis {axis}, ceiling >= {ceiling_deg:.0f} deg, bridges judged at "
            f"{limit:.0f} mm, cantilevers at {cantilever_limit:.0f} mm, unanchored "
            f"ceilings at 0 mm",
            "score is span / the limit that applies to that region, so a bridge and "
            "a cantilever are comparable",
        ],
        columns="worst_span_mm\tits_limit_mm\tregions\tanchoring")
    return FOLD.fold(
        "fdm.bridge_span", outcomes, source=parts.source, units="mm",
        quantity="unsupported span", evidence=[summary, *region_files[:3]],
        extra_detail=f"bridges judged at {limit:.0f} mm, cantilevers at "
                     f"{cantilever_limit:.0f} mm")
