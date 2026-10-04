# SPDX-License-Identifier: Apache-2.0
"""Solid-geometry gates: is this actually a solid, and do the solids fit together?

Order in this file is the order the sweep runs, and it is deliberate. The cheap
arithmetic envelope check goes first; then the three mesh-validity gates, cheapest
and most fundamental first; then the two gates that are only meaningful on a mesh
that already passed those three. A pack that reports thin walls on a mesh which is
not even closed has measured nothing and said something.

trimesh is imported INSIDE the gate functions, never at module import. Two reasons,
both load-bearing:

* this module must import cleanly on a machine with no mesh library, so the pack is
  discoverable and `atompipe gate list` still works and says BLOCKED;
* the tier-0 gate (``cad.bounding``) declares no mesh dependency and must actually
  run there. A top-level ``import trimesh`` would take the whole module down and the
  cheap gate with it.

Every gate reads its numbers from ``ctx.params`` (the model projection) or from the
meshes the projection points at. Nothing here re-derives a model quantity: a gate
that recomputes a derived value is checking its own arithmetic instead of the
model's.
"""
from __future__ import annotations

import json
import math
import os
import sys
from typing import Any

from atompipe.gates import gate, GateContext
from atompipe.models import Locator, NegativeControl, Tier, Verdict

# Where the geometry is, and what each part is called once the site has drawn it.
# Shared with ``views/assembly.py`` rather than restated here: the node names a
# locator carries are an INTERFACE between this file and that one, and an
# interface that exists in two copies is an interface that will drift. The pack
# directory is already on sys.path under ``packs.load_gates``; the guard is for a
# fixture or a unit test that imported this module directly.
_PACK_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PACK_DIR not in sys.path:
    sys.path.insert(0, _PACK_DIR)

import cad_solid_parts as PARTS  # noqa: E402


# --------------------------------------------------------------------------- #
# the key families this pack reads, primary spelling FIRST
# --------------------------------------------------------------------------- #
# Every measurement here is of the ASSEMBLY — every solid, placed, in the assembly
# frame. That is not what `fdm-print` means by the same bare words: it reads
# `bbox_mm` for ONE PART in its print orientation. The two packs ship in the same
# default set and every mechanical project has both an assembly and printed parts,
# so on a real project the bare key had one meaning and two readers. Published as
# the assembly, `fdm.bed_fit` measured a 480 mm boat against a 220 mm printer bed;
# published as the part, `cad.bounding` skipped and the envelope claim went unheld.
#
# So each family leads with a key that says WHICH OBJECT it describes
# (`assembly_bbox_mm`), keeps the bare spelling as a documented fallback, and is
# resolved pack-scoped first (`cad.assembly_bbox_mm`, `cad.bbox_mm`) by
# GateContext.param. Resolution order, in full: every pack-scoped spelling in the
# order below, then every bare spelling in the order below. Written down here, in
# PACK.md and in docs/PACK_FORMAT.md, because the previous order was discoverable
# only by experiment.
BBOX_KEYS = ("assembly_bbox_mm", "bbox_mm")
BBOX_LIMIT_KEYS = ("assembly_bbox_limit_mm", "bbox_limit_mm")
VOLUME_KEYS = ("assembly_volume_mm3", "volume_mm3")
VOLUME_LIMIT_KEYS = ("assembly_volume_limit_mm3", "volume_limit_mm3")
COM_KEYS = ("assembly_com_mm", "com_mm")
COM_TARGET_KEYS = ("assembly_com_target_mm", "com_target_mm")
COM_TOL_KEYS = ("assembly_com_tol_mm", "com_tol_mm")

#: The thinnest wall the PROCESS allows — a limit the model states, not a
#: measurement of the geometry. `fdm-print`'s `min_wall_mm` is the opposite kind of
#: number (the thinnest section actually present in a part), which is why the
#: primary spelling here says `process_`: one bare key cannot be both the rule and
#: the reading, and a project that publishes its measured thinnest wall under the
#: bare name hands this gate a limit it never chose.
MIN_WALL_KEYS = ("process_min_wall_mm", "min_wall_mm")


# --------------------------------------------------------------------------- #
# domain constants — properties of mesh geometry, not of any one project
# --------------------------------------------------------------------------- #

#: Vertex weld tolerance. Two vertices closer than this are the same vertex that a
#: tessellator emitted twice. Chosen an order of magnitude below the smallest
#: feature any of these gates claims to resolve (~1e-3 mm) and several orders above
#: float32 noise on a 100 mm part (~1e-5 mm), so it cannot weld a real feature shut
#: and cannot miss a duplicate. Rejected: 1e-3 mm, which welds the two sides of a
#: genuine 1 um-scale seam on a scaled-up model and reports a clean part; and
#: 1e-6 mm, which leaves float32-round-tripped STL duplicates unwelded so every
#: exported part looks defective.
WELD_TOL_MM = 1e-4

#: Below this, a triangle has no area. This is a NUMERIC zero, not a sliver test:
#: a 1e-6 mm^2 needle is about a hundred times larger than this and passes. See the
#: "what this pack cannot settle" section of PACK.md.
ZERO_AREA_MM2 = 1e-8

#: Per-pair intersection tolerance, prismatic default. Two planar faces meeting
#: face-to-face tessellate to coincident triangles; the boolean kernel resolves
#: them to slivers whose total volume stays well under a tenth of a cubic
#: millimetre on parts of ordinary size. Anything above this is geometry, not noise.
#:
#: A VOLUME tolerance alone is not enough and must never be read as one: it
#: forgives a fixed volume, so the penetration DEPTH it forgives grows without
#: bound as the contact area shrinks. At 0.2 mm^3 a 0.5 mm pin may be driven
#: 0.79 mm into a block — deeper than the pin is wide — and still read as clear.
#: That is why every pair is also checked against a depth tolerance below.
PRISMATIC_CLASH_TOL_MM3 = 0.2

#: Per-pair tolerance for a pair where either part is organic/curved (a printed
#: fillet, a lofted shell, a revolved boss). Chord-height error on a tessellated
#: curve is of order the chord deviation times the mating area, which reaches a few
#: cubic millimetres on a part of ordinary size (roughly 100 mm across) at default
#: export settings — that size is the constant's VALIDITY REGIME, and it is stated
#: in PACK.md as well as here because a reader calibrating a tolerance will not
#: read the source. On a part an order of magnitude larger the mating area grows
#: with it and this number is too small; the depth tolerance below is the
#: scale-free half of the pair and is what actually governs a slender contact.
#: Raising the GLOBAL tolerance to 3 mm^3 to silence one curved pair is how a real
#: 2 mm^3 interference between two flat plates gets hidden; that is why this is
#: per-pair.
ORGANIC_CLASH_TOL_MM3 = 3.0

#: Per-pair penetration DEPTH tolerance, prismatic default. This is the half of the
#: clash test that does not depend on how big the contact patch is: mean depth is
#: shared volume over contact area, so it stays the same physical statement on a
#: 5 mm pin and a 200 mm flange while a volume tolerance does not. 0.01 mm is the
#: linear deviation an ordinary exporter puts between two nominally coincident
#: planar faces at default settings (see sourcing.md), so it is the largest
#: apparent penetration that is definitely tessellation and not geometry.
#: Rejected: 0 mm, which fails every intended face contact on export noise alone;
#: and 0.1 mm, which is a real light press fit on a part of this size — forgiving
#: it would hide exactly the interference this gate exists to find.
PRISMATIC_CLASH_DEPTH_TOL_MM = 0.01

#: The same for a pair where either part is curved. The error here is the chord
#: height of the tessellated curve, not the exporter's planar deviation: at default
#: angular/chord settings that is a few hundredths of a millimetre on a part of
#: ordinary size. Rejected: sharing the prismatic 0.01 mm, which reports every
#: curved mating face in the assembly as an interference at default export
#: settings and trains everyone to widen the tolerance globally.
ORGANIC_CLASH_DEPTH_TOL_MM = 0.05

#: Penetration DEPTH allowed on a pair declared in ``bonded_joints``: a joint whose
#: two faces are, by design, two tessellations of ONE nominal mating surface. The
#: deviation between them is therefore the tessellation deviation and nothing else,
#: which is the same chord-height figure as the organic depth tolerance above — so
#: this is that number, for that reason, and not a new licence.
#:
#: What a bonded declaration waives is the VOLUME tolerance, never this one. Shared
#: volume on a bond line is depth times GLUE AREA, and the glue area is a design
#: quantity that is legitimately large: a 2400 mm^2 epoxy fillet at 0.011 mm of
#: tessellation overlap is 26 mm^3, a hundred times the prismatic volume tolerance,
#: with nothing wrong. Judging that by volume is what turns a correctly modelled
#: glued assembly into a page of red, and a page of red is when somebody writes a
#: wildcard. Depth does not move with the size of the joint, so it is the half that
#: can still say "this part is 2 mm into that one" about a bonded pair.
#:
#: Rejected: an adhesive bond line (0.1-0.2 mm). A bond line is a GAP the parts
#: must leave for the glue, not material they may share; forgiving 0.2 mm of shared
#: material because the adhesive is 0.2 mm thick confuses a clearance with an
#: interference, and 0.2 mm is a real light press fit at this size.
#: Rejected: waiving the depth check for bonded pairs as well — that is
#: ``clash_allow`` with extra steps, and the pack already has ``clash_allow``.
BONDED_CONTACT_DEPTH_TOL_MM = 0.05

#: Bounding boxes within this of each other still get looked at rather than being
#: filtered out by an arithmetic hair. Note what this margin does NOT do: it never
#: sends a non-overlapping pair to the boolean. Two solids can only share material
#: inside the intersection of their bounding boxes, so a box intersection that is
#: degenerate on any axis holds no volume at all and there is nothing for a kernel
#: to measure — see the contact branch in :func:`clash`.
AABB_MARGIN_MM = 0.05

#: Ray budget for the wall-thickness sampler, as an exact ray COUNT. Above this many
#: faces the gate casts from an evenly spaced deterministic subset of exactly this
#: size rather than from every facet; a gate whose cost scales with tessellation
#: density stops being runnable exactly when the model gets detailed enough to need
#: it. Rejected: a stride (``n_faces // budget``), which is what this was — it gives
#: stride 1 for any face count below twice the budget, so the real ceiling was about
#: double the number stated here and the gate's cost was not the cost it declared.
MAX_WALL_SAMPLES = 4000

#: Formats that store a bag of triangles with no vertex identity. A file in one of
#: these has no topology until somebody welds it, so this pack welds it once at the
#: weld tolerance and says so in the verdict rather than pretending the file was
#: closed or that every repeated corner is a defect.
SOUP_SUFFIXES = (".stl",)

#: The geometry lookup and the missing-geometry message both live in
#: ``cad_solid_parts`` now, because ``views/assembly.py`` has to resolve the same
#: mesh map from the same projection — see that module's docstring.
_MISSING_GEOMETRY = PARTS.MISSING_GEOMETRY

#: How many offending parts or interfering pairs get a locator before the gate
#: stops pinning them. A verdict that lights up every part in the assembly has
#: highlighted nothing: the overlay is read by eye, and past a dozen pins the eye
#: sees a red model rather than a red part. The count in ``detail`` is always the
#: real one — the cap trims the drawing, never the measurement.
_MAX_LOCATORS = 12


# --------------------------------------------------------------------------- #
# shared plumbing
# --------------------------------------------------------------------------- #
def _skipped(gate_id: str, reason: str) -> Verdict:
    """A gate that could not measure says so. It never returns a pass."""
    return Verdict(gate=gate_id, passed=False, skipped=True, skip_reason=reason)


def _locate(movers: dict, part: str, label: str = "", value: Any = None,
            severity: str = "") -> Locator:
    """One locator pointing at a PART of the assembly view.

    ``movers`` is ``PARTS.movers(meshes)``, resolved once per gate: the SAME part
    list the viewgen will see (the keys of the resolved mesh map), so both sides
    compute the same names without consulting each other.

    The target is the mover — the part — and never a node index and never a
    position, because the part is genuinely what these gates know. A
    watertightness failure is a property of the whole part; inventing a body index
    or an xyz to go with it would put a confident pin on a spot nobody measured,
    and the contract is explicit that a wrong highlight is worse than none (the
    reader inspects a part that is fine, and stops trusting the overlay).
    """
    return Locator(
        view=PARTS.VIEW_ID,
        target=movers.get(part, PARTS.sanitise(part)),
        kind="part",
        label=label,
        severity=severity,
        # NaN and inf are dropped rather than carried. They reach here honestly —
        # a kernel that returns a non-finite volume is exactly what cad.is_volume
        # reports — but `json.dumps` writes them as bare `NaN`, which is not JSON,
        # and the page that fails to parse state.json shows nothing at all rather
        # than one unmeasurable part. The reason survives in `label`.
        value=(float(value) if isinstance(value, (int, float))
               and not isinstance(value, bool) and math.isfinite(value) else None),
    )


def _load_one(source: Any, root: str) -> tuple[Any, bool]:
    """``(mesh, welded_on_load)``. Accepts an in-memory mesh or a path.

    ``process=False`` is not an optimisation. The default load pipeline welds
    vertices and drops degenerate faces on the way in — it would repair, silently
    and in memory, the exact defects ``cad.degenerate_faces`` exists to report, and
    the gate would then certify a file that no other program will read the same way.
    Validate the bytes you are going to send.

    The exception is a **soup format**. An STL file is a bag of independent
    triangles with no vertex identity whatsoever: every corner is written three to
    six times and the topology exists only once somebody decides which corners are
    the same point. Loading one unprocessed and then counting duplicate vertices
    would report every STL ever exported as catastrophically defective, which is a
    true statement about the format and a useless one about the part. So a soup
    format is welded ONCE at the weld tolerance on load, that weld is recorded, and
    the gates say so in their verdicts — because the welded mesh is an
    interpretation of the file, not the file.
    """
    if hasattr(source, "faces") and hasattr(source, "vertices"):
        return source, False

    import trimesh

    path = str(source)
    if not os.path.isabs(path) and root:
        path = os.path.join(root, path)
    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    loaded = trimesh.load_mesh(path, process=False)
    if hasattr(loaded, "geometry") and not hasattr(loaded, "faces"):   # a Scene
        parts = list(loaded.geometry.values())
        if not parts:
            raise ValueError(f"{path} contains no geometry")
        loaded = parts[0] if len(parts) == 1 else trimesh.util.concatenate(parts)

    if path.lower().endswith(SOUP_SUFFIXES):
        digits = max(0, int(round(-math.log10(WELD_TOL_MM))))
        try:
            loaded.merge_vertices(digits_vertex=digits)
        except TypeError:                              # older trimesh signature
            loaded.merge_vertices()
        return loaded, True
    return loaded, False


def _load_all(ctx: GateContext, gate_id: str):
    """``(meshes, welded_on_load, skip)``. Never invents geometry.

    ``welded_on_load`` names the parts that arrived in a soup format and were welded
    to give them a topology at all. Gates that report topology carry that fact into
    their detail line: a reader has to be able to tell a measurement of the file from
    a measurement of an interpretation of the file.
    """
    sources, where = PARTS.mesh_sources(ctx)
    if sources is None:
        return None, (), _skipped(gate_id, where)
    meshes: dict[str, Any] = {}
    welded: list[str] = []
    for name in sorted(sources, key=str):
        try:
            mesh, was_welded = _load_one(sources[name], ctx.root or "")
        except Exception as exc:                       # noqa: BLE001 - user data
            return None, (), _skipped(
                gate_id,
                f"part {name!r} from {where} could not be read "
                f"({type(exc).__name__}: {exc}) — nothing was measured",
            )
        meshes[str(name)] = mesh
        if was_welded:
            welded.append(str(name))
    if not meshes:
        return None, (), _skipped(gate_id, _MISSING_GEOMETRY)
    return meshes, tuple(welded), None


def _welded_note(welded) -> str:
    """One clause naming the interpretation, or nothing."""
    if not welded:
        return ""
    return (f"; {len(welded)} part(s) welded at {WELD_TOL_MM:g} mm on load "
            f"({', '.join(sorted(welded)[:3])}{'...' if len(welded) > 3 else ''}) — "
            f"a soup format has no vertex identity of its own")


def _edge_counts(mesh: Any) -> tuple[int, int, int]:
    """``(open_edges, nonmanifold_edges, n_faces)`` computed from the face table.

    Deliberately computed here with numpy rather than read off a trimesh property.
    The numbers have to survive a version bump, and "how many edges are used once"
    is four lines of arithmetic that cannot quietly change meaning under us.
    """
    import numpy as np

    faces = np.asarray(mesh.faces)
    if faces.size == 0:
        return 0, 0, 0
    edges = np.vstack([faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]])
    edges = np.sort(edges, axis=1)
    _, counts = np.unique(edges, axis=0, return_counts=True)
    return int((counts == 1).sum()), int((counts > 2).sum()), int(len(faces))


def _write(ctx: GateContext, filename: str, payload: Any) -> str:
    """Bulk output to out_dir, returned as a path for ``Verdict.evidence``."""
    path = ctx.out_path("cad-solid", filename)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True, default=str)
    return path


# --------------------------------------------------------------------------- #
# tier 0 — arithmetic on the projection, runs with no mesh library at all
# --------------------------------------------------------------------------- #
def _as_triple(value: Any, allow_scalar: bool = False) -> list[float] | None:
    """``[x, y, z]`` from a 3-list, or from a scalar ONLY where that is meaningful.

    ``allow_scalar`` is true for a LIMIT, where "the same in every axis" is a real
    envelope — a 40 mm cube of clearance is a sentence somebody means. It is false
    for every MEASUREMENT (``bbox_mm``, ``com_mm``, ``com_target_mm``), where one
    number in place of three is not a compact spelling, it is two missing axes: a
    projection that writes ``bbox_mm: 96`` would otherwise get a confident PASS on
    a Y and Z this gate invented. A gate that refuses to guess a filename must not
    guess two thirds of a bounding box.
    """
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return [float(value)] * 3 if allow_scalar else None
    if isinstance(value, (list, tuple)) and len(value) == 3:
        try:
            return [float(v) for v in value]
        except (TypeError, ValueError):
            return None
    return None


def _spellings(ctx: GateContext, names: "tuple[str, ...]") -> str:
    """The spellings this gate accepts for one quantity, best first.

    The pack-scoped form comes first because it is the one to reach for on a
    project where another pack wants the same bare key — and a skip message is
    exactly where a reader finds out the namespace exists.
    """
    scope = (ctx.key_scope or "").strip()
    scoped = [f"{scope}.{names[0]}"] if scope and names else []
    return "/".join(scoped + list(names))


def _triple_problem(name: str, value: Any, allow_scalar: bool) -> str:
    """Why a triple was rejected, named precisely enough to fix in one edit."""
    if value is None:
        return f"{name} is absent"
    if isinstance(value, (int, float)) and not isinstance(value, bool) and not allow_scalar:
        return (f"{name} is the single number {value!r}; it is a measurement, so it "
                f"needs all three axes [X, Y, Z] — this gate will not expand it to a cube")
    return f"{name} is {type(value).__name__} {value!r}, not a 3-list of mm"


@gate(
    id="cad.bounding",
    title="Bounding box, volume and centre of mass within their stated limits",
    claims=["geometry", "envelope", "packaging", "mechanical", "cad"],
    tier=Tier.INSTANT,
    settles="bounding box",
    negative_control=NegativeControl(
        fixture="selftest/bad_bounds.py:tall_part",
        note="the same part 15% past its stated Z envelope, taken from the "
             "projection's own limit; every other number is untouched, so only the "
             "height check trips",
    ),
)
def bounding(ctx: GateContext) -> Verdict:
    """Envelope arithmetic: does the part fit where it has to live, and balance there?

    This is the pack's tier-0 gate and the only one that runs without a mesh library.
    It checks the model's OWN numbers against the model's OWN limits — it does not
    open the geometry, so it cannot tell you that ``bbox_mm`` still matches the mesh
    that was exported. That agreement is a separate claim and needs a separate gate.

    Three sub-checks, reported as the worst utilisation of the three. Volume and
    centre of mass are optional and are named as unchecked in the detail line when
    the projection does not carry them; the bounding box is not optional, because a
    gate with nothing at all to compare would be a logger.
    """
    raw_bbox, bbox_key = ctx.first_pack_param_named(BBOX_KEYS)
    raw_limit, limit_key = ctx.first_pack_param_named(BBOX_LIMIT_KEYS)
    bbox = _as_triple(raw_bbox)
    limit = _as_triple(raw_limit, allow_scalar=True)
    if bbox is None or limit is None:
        problems = [_triple_problem(n, v, s) for n, v, s, ok in
                    ((bbox_key or BBOX_KEYS[0], raw_bbox, False, bbox),
                     (limit_key or BBOX_LIMIT_KEYS[0], raw_limit, True, limit))
                    if ok is None]
        return _skipped(
            "cad.bounding",
            f"{'; '.join(problems)} — nothing to compare, so nothing is claimed. "
            f"This gate measures the ASSEMBLY: it reads "
            f"{_spellings(ctx, BBOX_KEYS)} (and {_spellings(ctx, BBOX_LIMIT_KEYS)}), "
            f"in that order. fdm-print's part-sized bbox_mm is a different object",
        )

    axes = ("X", "Y", "Z")
    ratios = [(b / l if l else math.inf, a, b, l) for a, b, l in zip(axes, bbox, limit)]
    worst_ratio, worst_axis, worst_val, worst_lim = max(ratios, key=lambda r: r[0])
    parts = [
        f"bbox {bbox[0]:.2f}x{bbox[1]:.2f}x{bbox[2]:.2f} vs "
        f"{limit[0]:.2f}x{limit[1]:.2f}x{limit[2]:.2f} mm "
        f"(worst {worst_axis} {worst_val:.2f}/{worst_lim:.2f} = {worst_ratio * 100:.0f}%)"
    ]
    worst = worst_ratio

    volume = ctx.first_pack_param(VOLUME_KEYS)
    volume_limit = ctx.first_pack_param(VOLUME_LIMIT_KEYS)
    if isinstance(volume, (int, float)) and isinstance(volume_limit, (int, float)) and volume_limit:
        ratio = float(volume) / float(volume_limit)
        worst = max(worst, ratio)
        parts.append(f"vol {float(volume):.0f}/{float(volume_limit):.0f} mm^3 ({ratio * 100:.0f}%)")
    else:
        parts.append(f"vol not checked ({'/'.join(VOLUME_KEYS)} or "
                     f"{'/'.join(VOLUME_LIMIT_KEYS)} absent)")

    raw_com, com_key = ctx.first_pack_param_named(COM_KEYS)
    raw_target, target_key = ctx.first_pack_param_named(COM_TARGET_KEYS)
    com = _as_triple(raw_com)
    com_target = _as_triple(raw_target)
    com_tol = ctx.first_pack_param(COM_TOL_KEYS)
    if com and com_target and isinstance(com_tol, (int, float)) and com_tol:
        offset = math.dist(com, com_target)
        ratio = offset / float(com_tol)
        worst = max(worst, ratio)
        parts.append(f"com {offset:.2f}/{float(com_tol):.2f} mm off target ({ratio * 100:.0f}%)")
    elif (raw_com is not None and com is None) or (raw_target is not None and com_target is None):
        # Present but unusable is not the same as absent, and reporting it as
        # "not checked" would let a malformed centre of mass pass unremarked.
        bad = [_triple_problem(n, v, False) for n, v, ok in
               ((com_key or COM_KEYS[0], raw_com, com),
                (target_key or COM_TARGET_KEYS[0], raw_target, com_target))
               if v is not None and ok is None]
        parts.append(f"com NOT checked — {'; '.join(bad)}")
    else:
        parts.append(f"com not checked ({COM_KEYS[0]}/{COM_TARGET_KEYS[0]}/"
                     f"{COM_TOL_KEYS[0]} absent)")

    return Verdict(
        gate="cad.bounding",
        passed=worst <= 1.0,
        measured=round(worst, 4),
        limit=1.0,
        units="utilisation",
        detail="; ".join(parts),
    )


# --------------------------------------------------------------------------- #
# tier 1 — mesh validity. Run these before anything trusts the geometry.
# --------------------------------------------------------------------------- #
@gate(
    id="cad.watertight",
    title="Every solid is closed: no open edges, no non-manifold edges",
    claims=["geometry", "mesh", "cad", "manufacturability"],
    tier=Tier.BUILD,
    settles="mesh watertightness",
    requires_python=["trimesh", "numpy"],
    negative_control=NegativeControl(
        fixture="selftest/bad_meshes.py:holed_box",
        # the known-good control on the channel this fixture uses
        # (ctx.extra): selftest/good_meshes.py says why (P2.3-D5)
        good="selftest/good_meshes.py:baseline_meshes",
        note="a box with the two triangles of its +Z facet deleted: 4 open edges, "
             "winding and every other facet untouched",
    ),
)
def watertight(ctx: GateContext) -> Verdict:
    """Closure, gated before anything downstream trusts the mesh.

    A surface with a hole in it is not a solid, and the damage is not confined to
    this check. A boolean intersection against a leaking mesh can return an empty
    result without raising, which reads downstream as "this part collides with
    nothing" — the most dangerous possible false negative, because it is the answer
    everyone wanted. Run this first and treat a failure here as poisoning every
    later verdict on the same part.

    Measures open edges (used by exactly one face) plus non-manifold edges (used by
    three or more). Both are zero on a solid; the two are reported separately
    because they have different causes — a hole versus a self-intersecting or
    doubled surface — and different fixes.
    """
    meshes, welded, skip = _load_all(ctx, "cad.watertight")
    if skip is not None:
        return skip

    rows = []
    total = 0
    for name, mesh in meshes.items():
        open_edges, nonmanifold, n_faces = _edge_counts(mesh)
        total += open_edges + nonmanifold
        rows.append({
            "part": name, "faces": n_faces, "open_edges": open_edges,
            "nonmanifold_edges": nonmanifold,
            "trimesh_is_watertight": bool(getattr(mesh, "is_watertight", False)),
        })

    worst = max(rows, key=lambda r: r["open_edges"] + r["nonmanifold_edges"])
    evidence = [_write(ctx, "watertight.json", rows)] if total else []

    # Locate the parts that are actually open, worst first, and nothing else. The
    # gate knows WHICH PART leaks and does not know where on it the hole is — the
    # edge indices it counted are indices into a face table, not a position — so
    # the pin goes on the part and carries the count. A pin placed at the centroid
    # of the open edges would look like an answer to "where is the hole" that
    # nothing here measured.
    movers = PARTS.movers(meshes)
    bad_rows = sorted((r for r in rows if r["open_edges"] + r["nonmanifold_edges"]),
                      key=lambda r: -(r["open_edges"] + r["nonmanifold_edges"]))
    locators = [
        _locate(movers, r["part"],
                label=f"{r['open_edges']} open, {r['nonmanifold_edges']} non-manifold "
                      f"edge(s) of {r['faces']} faces",
                value=r["open_edges"] + r["nonmanifold_edges"])
        for r in bad_rows[:_MAX_LOCATORS]
    ]

    return Verdict(
        gate="cad.watertight",
        passed=total == 0,
        measured=float(total),
        limit=0.0,
        units="edges",
        detail=f"{total} bad edge(s) across {len(rows)} part(s), limit 0 — worst "
               f"{worst['part']}: {worst['open_edges']} open, "
               f"{worst['nonmanifold_edges']} non-manifold of {worst['faces']} faces"
               + _welded_note(welded),
        evidence=evidence,
        locators=locators,
    )


@gate(
    id="cad.is_volume",
    title="Every solid is a volume: closed, consistently wound, positive volume",
    claims=["geometry", "mesh", "cad", "manufacturability"],
    tier=Tier.BUILD,
    settles="solid validity",
    requires_python=["trimesh", "numpy"],
    negative_control=NegativeControl(
        fixture="selftest/bad_meshes.py:flipped_facet",
        # the known-good control on the channel this fixture uses
        # (ctx.extra): selftest/good_meshes.py says why (P2.3-D5)
        good="selftest/good_meshes.py:baseline_meshes",
        note="a closed box with one triangle's winding reversed: still watertight, "
             "no longer a volume — the case cad.watertight cannot see",
    ),
)
def is_volume(ctx: GateContext) -> Verdict:
    """Closed AND consistently wound AND enclosing a positive volume.

    Watertightness is necessary and not sufficient. A mesh can be perfectly closed
    with one facet wound backwards; nothing about its edges gives it away, and it
    cannot be booleaned at all — depending on the kernel it raises, returns garbage,
    or returns a signed volume of the wrong sign. Reported per part with the
    property that actually failed, because "not a volume" alone sends people
    re-exporting a file whose real problem was a mirrored transform.
    """
    meshes, welded, skip = _load_all(ctx, "cad.is_volume")
    if skip is not None:
        return skip

    rows = []
    bad = 0
    for name, mesh in meshes.items():
        closed = bool(getattr(mesh, "is_watertight", False))
        wound = bool(getattr(mesh, "is_winding_consistent", False))
        try:
            volume = float(mesh.volume)
        except Exception as exc:                       # noqa: BLE001 - kernel
            volume = float("nan")
            wound = wound and False
            ctx.log(f"cad.is_volume: {name} volume raised {type(exc).__name__}")
        ok = bool(getattr(mesh, "is_volume", False)) and math.isfinite(volume) and volume > 0.0
        if not ok:
            bad += 1
        reasons = []
        if not closed:
            reasons.append("not closed")
        if not wound:
            reasons.append("winding inconsistent")
        if not math.isfinite(volume):
            reasons.append("volume is not finite")
        elif volume <= 0.0:
            reasons.append(f"volume {volume:.3f} <= 0 (inside-out)")
        rows.append({"part": name, "is_volume": ok, "watertight": closed,
                     "winding_consistent": wound, "volume_mm3": volume,
                     "why": ", ".join(reasons) or "ok"})

    first_bad = next((r for r in rows if not r["is_volume"]), None)
    evidence = [_write(ctx, "is_volume.json", rows)] if bad else []
    detail = f"{bad} of {len(rows)} part(s) are not volumes, limit 0"
    if first_bad:
        detail += f" — {first_bad['part']}: {first_bad['why']}"
    else:
        detail += f" (total {sum(r['volume_mm3'] for r in rows):.0f} mm^3 enclosed)"
    detail += _welded_note(welded)

    # One pin per part that is not a volume, carrying the reason rather than the
    # verdict's summary: "winding inconsistent" and "volume -412 mm^3 (inside-out)"
    # send a reader to two different fixes, and the whole value of the overlay is
    # that they read it while looking at the part.
    movers = PARTS.movers(meshes)
    locators = [
        _locate(movers, r["part"], label=r["why"], value=r["volume_mm3"])
        for r in rows if not r["is_volume"]
    ][:_MAX_LOCATORS]

    return Verdict(
        gate="cad.is_volume",
        passed=bad == 0,
        measured=float(bad),
        limit=0.0,
        units="parts",
        detail=detail,
        evidence=evidence,
        locators=locators,
    )


def _drop_unreferenced(mesh: Any) -> None:
    """Remove vertices no face points at, in place. Best-effort across versions."""
    remove = getattr(mesh, "remove_unreferenced_vertices", None)
    if callable(remove):
        remove()


def _weld_and_drop(mesh: Any, digits: int, area_eps: float, max_passes: int = 8) -> tuple[int, int, int]:
    """Alternate welding and dropping until neither does anything.

    ``(welded_vertices, dropped_faces, passes)`` on a COPY; the caller's mesh is
    never mutated.

    The alternation is the whole point and the order matters. A sliver straddles a
    pair of duplicated vertices: before the weld it has a small but genuinely
    non-zero area and survives an area test; after the weld its two endpoints are
    one vertex and its area is exactly zero. Drop-then-weld finds nothing and
    reports a clean part. Weld-then-drop finds it — and then the drop can expose a
    further duplicate, which is why this loops rather than running once.

    UNREFERENCED vertices are cleared before every measurement, and that is not
    tidiness. ``merge_vertices`` also discards any vertex the previous pass's
    ``update_faces`` left pointing at nothing, so a raw vertex-count delta counts
    those as welds and the verdict then states a repair the mesh never needed:
    pass/fail is unchanged (still non-zero when non-zero) but the number a reader
    is supposed to act on is inflated. Clearing them first makes the delta mean
    only "duplicates merged".
    """
    import numpy as np

    work = mesh.copy()
    _drop_unreferenced(work)
    welded = dropped = passes = 0
    for _ in range(max_passes):
        before = len(work.vertices)
        try:
            work.merge_vertices(digits_vertex=digits)
        except TypeError:                              # older trimesh signature
            work.merge_vertices()
        this_weld = before - len(work.vertices)

        areas = np.asarray(work.area_faces, dtype=float)
        keep = areas > area_eps
        this_drop = int((~keep).sum())
        if this_drop:
            work.update_faces(keep)
            _drop_unreferenced(work)

        welded += this_weld
        dropped += this_drop
        passes += 1
        if this_weld == 0 and this_drop == 0:
            break
    return welded, dropped, passes


@gate(
    id="cad.degenerate_faces",
    title="No duplicate vertices and no zero-area triangles",
    claims=["geometry", "mesh", "cad", "manufacturability"],
    tier=Tier.BUILD,
    settles="degenerate faces",
    requires_python=["trimesh", "numpy"],
    # No edge to cad.watertight: this gate's own control, split_corner_box, is
    # open and inconsistently wound too, so it fails both guards — they would
    # pre-empt it wherever both run.
    negative_control=NegativeControl(
        fixture="selftest/bad_meshes.py:sliver_pair",
        # the known-good control on the channel this fixture uses
        # (ctx.extra): selftest/good_meshes.py says why (P2.3-D5)
        good="selftest/good_meshes.py:baseline_meshes",
        note="one corner of a closed box emitted twice 1e-6 mm apart with a subset "
             "of its faces repointed at the copy: adjacency splits on a surface "
             "that is still geometrically closed, and the face spanning the two "
             "copies has non-zero area until the weld and exactly zero after it",
    ),
)
def degenerate_faces(ctx: GateContext) -> Verdict:
    """Weld duplicates at the weld tolerance, then drop zero-area faces, and repeat.

    A mesh can be geometrically closed and topologically open at the same time, on a
    part the CAD kernel calls valid: the tessellator emitted the rim vertex twice,
    so the two triangles either side of it are not neighbours even though they touch
    everywhere. Every downstream algorithm that walks adjacency — thickness, offset,
    slicing, boolean — then works on a surface with a seam through it.

    The measurement is the number of repairs this gate would have to make. Zero
    repairs is the only pass; anything above zero means the file on disk is not the
    file the kernel thinks it wrote, and it is reported without repairing anything,
    because a gate that silently fixes its input has removed the evidence.
    """
    meshes, welded, skip = _load_all(ctx, "cad.degenerate_faces")
    if skip is not None:
        return skip

    digits = max(0, int(round(-math.log10(WELD_TOL_MM))))
    rows = []
    total = 0
    for name, mesh in meshes.items():
        # NOT `welded`: that name already holds the parts welded on load, and
        # shadowing it here silently turns the verdict's own note into a crash.
        welds, drops, passes = _weld_and_drop(mesh, digits, ZERO_AREA_MM2)
        total += welds + drops
        rows.append({"part": name, "welded_vertices": welds, "dropped_faces": drops,
                     "passes": passes, "faces": int(len(mesh.faces))})

    worst = max(rows, key=lambda r: r["welded_vertices"] + r["dropped_faces"])
    evidence = [_write(ctx, "degenerate_faces.json", rows)] if total else []

    # Worst part first, and the label says which of the two repairs it needs: a
    # part that only needs welding has a duplicated vertex (a tessellator bug), a
    # part that only needs drops has needle triangles (a boolean's leftovers), and
    # the two are fixed at different points in the export chain.
    movers = PARTS.movers(meshes)
    repaired = sorted((r for r in rows if r["welded_vertices"] + r["dropped_faces"]),
                      key=lambda r: -(r["welded_vertices"] + r["dropped_faces"]))
    locators = [
        _locate(movers, r["part"],
                label=f"{r['welded_vertices']} duplicate vertex/vertices, "
                      f"{r['dropped_faces']} zero-area face(s) of {r['faces']}",
                value=r["welded_vertices"] + r["dropped_faces"])
        for r in repaired[:_MAX_LOCATORS]
    ]

    return Verdict(
        gate="cad.degenerate_faces",
        passed=total == 0,
        measured=float(total),
        limit=0.0,
        units="repairs",
        detail=f"{total} repair(s) needed across {len(rows)} part(s), limit 0 — worst "
               f"{worst['part']}: welded {worst['welded_vertices']} vertex/vertices at "
               f"{WELD_TOL_MM:g} mm, dropped {worst['dropped_faces']} face(s) below "
               f"{ZERO_AREA_MM2:g} mm^2 over {worst['passes']} pass(es)"
               + _welded_note(welded),
        evidence=evidence,
        locators=locators,
    )


# --------------------------------------------------------------------------- #
# tier 1 — measurements that are only meaningful on a valid solid
# --------------------------------------------------------------------------- #

#: Rays cast per numpy chunk, as a ray-times-triangle product. The fallback caster
#: below builds a (rays x triangles x 3) array, so the chunk size has to be set on
#: that product rather than on a ray count: 4000 rays against a 12-triangle box and
#: 4000 rays against a 200k-triangle casting are four orders of magnitude apart in
#: memory and identical in ray count. 2e6 keeps a chunk around 50 MB in float64.
_CAST_CHUNK_RT = 2_000_000

#: Parallel-ray cutoff for the fallback caster, as a COSINE. With a unit direction,
#: Moller-Trumbore's determinant is ``e1 . (d x e2)`` = 2*area*cos(ray, facet
#: normal): units of LENGTH^2, magnitude set by the FACET, never by the assembly.
#: Dividing it by the triangle's own 2*area leaves a dimensionless cosine, so the
#: test "is this ray edge-on to this triangle" means the same thing on a 1 mm part
#: and a 10 m one and cannot be moved by anything else in the file.
#:
#: REJECTED, and this is the whole reason the constant is written this way: a cutoff
#: scaled off ``mesh.scale`` (the bounding diagonal). ``1e-12 * scale**3`` is
#: dimensionally a volume being compared to an area — it grows as the cube of the
#: part's overall span while the quantity it gates stays fixed by facet size, so on
#: a large assembly with ordinary facets every triangle is classified "parallel",
#: every ray misses, and the gate SKIPS for a reason that has nothing to do with the
#: mesh. ``1e-12 * scale**2`` is dimensionally right and still wrong in kind: one
#: number derived from the assembly's diagonal cannot be correct for both the
#: largest and the smallest facet on the same mesh.
#:
#: 1e-12 is chosen as a cosine that no real geometry reaches: a facet that far
#: edge-on contributes a hit whose barycentric coordinates are pure float64 noise,
#: and a genuinely grazing surface is found by its neighbours, which are not.
_PARALLEL_COS_EPS = 1e-12


def _cast_first_hit(mesh: Any, origins: Any, directions: Any) -> tuple[Any, Any]:
    """``(locations, index_ray)`` of each ray's first forward hit.

    trimesh's own ray engines are used when one of them is importable. Neither is
    guaranteed: the pure-python engine culls candidates with an ``rtree`` index and
    the fast one needs an embree binding, and NEITHER is a dependency of trimesh.
    On a machine with just trimesh and numpy — exactly what this gate declares in
    ``requires_python`` — ``mesh.ray`` raises ``ModuleNotFoundError`` from inside the
    property, which reaches the spine as a CRASHED gate. A crash settles nothing and
    reads as "the design is wrong" rather than "the tool is missing", so the gate has
    to be able to do this itself with what it declared.

    The fallback is Möller-Trumbore, vectorised over triangles and chunked over
    rays: the same intersection test the engines implement, brute-forced over the
    whole face list instead of culled by a spatial index, which is why the
    accelerated engine is still preferred when it is present.

    The honest statement of how far that equivalence goes: the two paths agree to
    floating-point round-off on the hits they both find, and neither is an
    approximation of the other's geometry — but they are two implementations with
    two sets of edge-case rules (grazing facets, coincident surfaces, a ray that
    starts exactly on a face), so which one ran is a fact about the machine and
    ``selftest/check_ray_cast.py`` exists to keep the difference measured rather
    than assumed. Do not read "fallback" as "degraded"; read it as "the other one".
    """
    import numpy as np

    origins = np.asarray(origins, dtype=float)
    directions = np.asarray(directions, dtype=float)
    try:
        locations, index_ray, _ = mesh.ray.intersects_location(
            origins, directions, multiple_hits=False)
        return np.asarray(locations, dtype=float), np.asarray(index_ray, dtype=int)
    except ImportError:
        pass

    tri = np.asarray(mesh.triangles, dtype=float)
    if len(tri) == 0 or len(origins) == 0:
        return np.zeros((0, 3)), np.zeros(0, dtype=int)
    v0 = tri[:, 0, :]
    e1 = tri[:, 1, :] - v0
    e2 = tri[:, 2, :] - v0

    # Per-triangle parallel cutoff: |det| against this triangle's own 2*area makes
    # the test a cosine (see _PARALLEL_COS_EPS). Computed from e1/e2 rather than
    # read off `mesh.area_faces` so it is the same arithmetic, in the same order,
    # as the determinant it gates. A genuinely zero-area triangle gets a cutoff of
    # zero — and a determinant of zero — so `>` excludes it, which is correct: a
    # triangle with no area has no interior to hit.
    two_area = np.linalg.norm(np.cross(e1, e2), axis=1)
    det_eps = _PARALLEL_COS_EPS * two_area

    chunk = max(1, int(_CAST_CHUNK_RT // max(1, len(tri))))
    best = np.full(len(origins), np.inf)
    for start in range(0, len(origins), chunk):
        o = origins[start:start + chunk]
        d = directions[start:start + chunk]
        pvec = np.cross(d[:, None, :], e2[None, :, :])
        det = np.einsum("fj,rfj->rf", e1, pvec)
        live = np.abs(det) > det_eps[None, :]
        inv = np.where(live, 1.0 / np.where(live, det, 1.0), 0.0)
        tvec = o[:, None, :] - v0[None, :, :]
        u = np.einsum("rfj,rfj->rf", tvec, pvec) * inv
        qvec = np.cross(tvec, e1[None, :, :])
        v = np.einsum("rj,rfj->rf", d, qvec) * inv
        t = np.einsum("fj,rfj->rf", e2, qvec) * inv
        hit = live & (u >= 0.0) & (v >= 0.0) & (u + v <= 1.0) & (t > 0.0)
        best[start:start + chunk] = np.where(hit, t, np.inf).min(axis=1)

    index_ray = np.nonzero(np.isfinite(best))[0]
    locations = origins[index_ray] + directions[index_ray] * best[index_ray][:, None]
    return locations, index_ray


@gate(
    id="cad.wall_thickness",
    title="Thinnest wall found by inward ray casting, against the minimum",
    claims=["geometry", "mesh", "cad", "manufacturability", "wall"],
    tier=Tier.BUILD,
    settles="minimum wall thickness",
    requires_python=["trimesh", "numpy"],
    # Prerequisite cad.watertight (P2.2-D12): ray casting through an open mesh
    #    leaks out of the hole and reports a thickness that is not there.
    #    Isolated: the guard passes this gate's own known-bad control
    #    (test_packs.ControlsAreIsolated).
    needs=["cad.watertight"],
    negative_control=NegativeControl(
        fixture="selftest/bad_meshes.py:thin_plate",
        # the known-good control on the channel this fixture uses
        # (ctx.extra): selftest/good_meshes.py says why (P2.3-D5)
        good="selftest/good_meshes.py:baseline_meshes",
        note="a plate at a quarter of the project's own minimum wall; the same "
             "footprint, watertight, a valid volume, and too thin",
    ),
)
def wall_thickness(ctx: GateContext) -> Verdict:
    """Minimum wall by casting a ray inward from each facet along its own normal.

    Method, stated plainly because its limits matter more than its number: from the
    centroid of each facet — an evenly spaced, deterministic subset of exactly the
    ray budget once the face count exceeds it — a ray is cast along the inward
    normal and the distance to the first hit is the local thickness. The minimum
    over all samples is reported.

    What this does NOT measure, and nobody should assume it does:

    * It is a LOWER-BOUND SAMPLER, not a proof. It sees thickness only where a facet
      exists and only along that facet's normal. A thin region whose thinnest
      direction is not normal to any sampled facet is missed.
    * On a coarsely tessellated curved wall it UNDER-reports, by roughly
      ``t*(1 - cos(pi/N))`` for a wall of thickness ``t`` across an N-sided
      tessellation of the curve. The chord sits inside the true surface, so a ray
      leaving one chord and landing on the other crosses ``t*cos(pi/N)``, not ``t``.
      The error is conservative — false alarms, never false passes — but it is a
      real number: 16 facets around a curve cost 1.9% of the wall, 8 cost 7.6%.
      (An over-reporting regime exists and is a different mechanism: on an
      IRREGULAR tessellation a facet normal need not point at the opposing surface
      at all, and an oblique ray travels further than the wall is thick. Do not
      conflate the two — the chord effect is systematic and one-signed, the
      obliquity effect is not.)
    * It measures material, not clearance. A ray crossing an internal cavity reports
      the wall on this side and says nothing about the rib beyond it.
    * On an open or non-manifold mesh it is meaningless, so the gate refuses to run
      rather than returning a number — see ``cad.watertight``.

    For a proof rather than a sample, the domain tool is a medial-axis or
    sphere-inscription analysis; ``references/mesh_hygiene.md`` says what that costs.
    """
    limit, limit_key = ctx.first_pack_param_named(MIN_WALL_KEYS)
    if not isinstance(limit, (int, float)) or isinstance(limit, bool) or limit <= 0:
        return _skipped(
            "cad.wall_thickness",
            f"the projection does not define "
            f"{_spellings(ctx, MIN_WALL_KEYS)} as a positive number of mm "
            f"(read: {limit!r}) — the minimum wall is a PROCESS decision the model "
            f"must carry, and this gate will not invent one. It is not the same "
            f"quantity as fdm-print's min_wall_mm, which is the thinnest section "
            f"measured in a part; publish this one as process_min_wall_mm (or "
            f"cad.min_wall_mm) when both packs are installed",
        )
    limit = float(limit)

    meshes, welded, skip = _load_all(ctx, "cad.wall_thickness")
    if skip is not None:
        return skip

    import numpy as np

    # `int(ctx.param(...) or MAX)` swallowed an explicit 0 and turned it into 4000,
    # so a projection asking for no sampling silently got the full budget. A ray
    # budget is a number the model states on purpose; an unusable one is a defect
    # in the projection, not something to paper over with a default.
    raw_budget = ctx.param("wall_samples")
    if raw_budget is None:
        budget = MAX_WALL_SAMPLES
    else:
        try:
            budget = int(raw_budget)
        except (TypeError, ValueError):
            budget = 0
        if budget <= 0:
            return _skipped(
                "cad.wall_thickness",
                f"'wall_samples' is {raw_budget!r}; it must be a positive integer "
                f"number of rays (omit it for the default {MAX_WALL_SAMPLES}) — a "
                f"budget of zero would measure nothing and report a pass",
            )
    rows = []
    worst_value = math.inf
    worst_part = ""
    worst_point: list[float] = []
    for name, mesh in meshes.items():
        if not bool(getattr(mesh, "is_watertight", False)):
            return _skipped(
                "cad.wall_thickness",
                f"part {name!r} is not watertight, so a ray cast from inside it can "
                f"escape and thickness is undefined — settle cad.watertight first",
            )

        centres = np.asarray(mesh.triangles_center, dtype=float)
        normals = np.asarray(mesh.face_normals, dtype=float)
        n_faces = len(centres)
        # Honour the budget exactly. `stride = n_faces // budget` gives stride 1 for
        # any face count below 2x the budget, so the real ceiling was about twice
        # the number the constant promises and the gate's cost was not the cost it
        # declared. An evenly spaced index of exactly min(n_faces, budget) samples
        # is deterministic in the same way a stride is, and is the stated budget.
        n_rays = min(n_faces, budget)
        idx = np.unique(np.linspace(0, n_faces - 1, n_rays).round().astype(int))
        eps = max(1e-6, float(getattr(mesh, "scale", 1.0)) * 1e-8)
        origins = centres[idx] - normals[idx] * eps
        directions = -normals[idx]

        locations, index_ray = _cast_first_hit(mesh, origins, directions)
        if len(index_ray) == 0:
            return _skipped(
                "cad.wall_thickness",
                f"no inward ray hit anything on part {name!r} ({len(idx)} rays) — the "
                f"ray engine returned nothing, so no thickness was measured",
            )
        # The ray origin is pushed eps ALONG THE INWARD direction, i.e. eps below
        # the surface it starts from (trimesh face normals point outward and the
        # origin is centre - normal*eps). The wall runs surface to surface, so the
        # thickness is eps + |hit - origin|: the correction ADDS back the sliver the
        # offset skipped. Subtracting it biased every reading low by 2*eps —
        # invisible at eps = 1e-6 mm on a mm-scale part, and the same order as the
        # tolerance being arbitrated once eps tracks mesh.scale on a metre-scale
        # assembly.
        travel = np.linalg.norm(
            np.asarray(locations, dtype=float) - origins[index_ray], axis=1)
        real = travel > 10.0 * eps
        if not real.any():
            return _skipped(
                "cad.wall_thickness",
                f"every inward ray on part {name!r} hit its own facet — the sampler "
                f"measured nothing",
            )
        distances = travel[real] + eps
        hit_rows = np.asarray(index_ray)[real]
        local_min = float(distances.min())
        where = origins[hit_rows[int(distances.argmin())]]
        rows.append({"part": name, "min_mm": local_min, "rays": int(len(idx)),
                     "hits": int(len(distances)), "faces": int(n_faces),
                     "at_mm": [round(float(c), 3) for c in where]})
        if local_min < worst_value:
            worst_value = local_min
            worst_part = name
            worst_point = [round(float(c), 3) for c in where]

    evidence = [_write(ctx, "wall_thickness.json", rows)]
    sampled = sum(r["hits"] for r in rows)
    cast = sum(r["rays"] for r in rows)

    # The one gate in this pack that knows a POINT rather than a part: the thin
    # spot is where a ray started, in the same model coordinates the assembly GLB
    # is exported in, so the pin goes exactly there. It is still honest about what
    # it is — a sampler found the thinnest of the walls it happened to cast
    # through, which is why the locator names the ray count in its label and why
    # only the worst one is pinned. Pinning the runner-up on every part would
    # suggest a thickness map the gate did not produce.
    locators = []
    if worst_value < limit and worst_part:
        movers = PARTS.movers(meshes)
        locators = [
            Locator(view=PARTS.VIEW_ID, target=movers.get(worst_part, worst_part),
                    kind="point", position=list(worst_point),
                    value=round(worst_value, 4),
                    label=f"{worst_value:.3f} mm wall vs {limit:.3f} mm minimum "
                          f"({cast} rays sampled)")
        ]

    return Verdict(
        gate="cad.wall_thickness",
        passed=worst_value >= limit,
        measured=round(worst_value, 4),
        limit=round(limit, 4),
        units="mm",
        detail=f"thinnest wall {worst_value:.3f} mm on {worst_part} at "
               f"({', '.join(f'{c:g}' for c in worst_point)}) vs {limit:.3f} mm minimum "
               f"[{limit_key or MIN_WALL_KEYS[0]}] "
               f"({sampled}/{cast} inward face-normal rays hit; sampler, not a proof)",
        evidence=evidence,
        locators=locators,
    )


# --------------------------------------------------------------------------- #
# tier 1 — interference
# --------------------------------------------------------------------------- #
#: The back-ends trimesh can dispatch a boolean to, as (probe, human name). trimesh
#: does not implement booleans itself and declares NONE of these as a dependency, so
#: `pip install trimesh numpy` — exactly what this gate's requires_python states —
#: leaves ``trimesh.boolean.intersection`` raising from inside the call.
_BOOLEAN_ENGINES = (
    ("manifold3d", "manifold3d (pip install manifold3d)"),
    ("blender", "a headless Blender on PATH"),
    ("openscad", "OpenSCAD on PATH"),
)


def _boolean_engines() -> list[str]:
    """Human names of the boolean back-ends actually usable on this machine.

    The gate needs ANY ONE of three unrelated things, and ``requires_python`` and
    ``requires_tools`` are ANDed, so the disjunction is DECLARED on the gate as
    ``requires_one_of`` — that is what makes ``availability`` skip the gate on a
    machine with no engine, before its body runs. This probe is the second line:
    it runs once before the pair loop, and an empty result is still a SKIP, never
    a verdict about the design. Probed and not imported where possible: the answer
    has to be obtainable on the machine where the answer is "none of them".
    """
    found: list[str] = []
    try:
        import importlib.util
        if importlib.util.find_spec("manifold3d") is not None:
            found.append(_BOOLEAN_ENGINES[0][1])
    except Exception:                                  # noqa: BLE001 - third-party import side effects
        pass
    try:
        import trimesh.interfaces as interfaces
        for attr, name in (("blender", _BOOLEAN_ENGINES[1][1]),
                           ("scad", _BOOLEAN_ENGINES[2][1])):
            iface = getattr(interfaces, attr, None)
            if iface is not None and bool(getattr(iface, "exists", False)):
                found.append(name)
    except Exception:                                  # noqa: BLE001 - third-party import side effects
        pass
    return found


def _pair_key(a: str, b: str) -> tuple[str, str]:
    return (a, b) if a <= b else (b, a)


def _normalise_pair(entry: Any) -> tuple[str, str] | None:
    if isinstance(entry, dict):
        entry = entry.get("pair") or entry.get("parts")
    if isinstance(entry, str):
        for sep in ("|", ",", "/"):
            if sep in entry:
                bits = [p.strip() for p in entry.split(sep, 1)]
                return _pair_key(bits[0], bits[1])
        return None
    if isinstance(entry, (list, tuple)) and len(entry) == 2:
        return _pair_key(str(entry[0]).strip(), str(entry[1]).strip())
    return None


def _read_allowlist(ctx: GateContext, sliding: set[tuple[str, str]]) -> tuple[dict, list[str]]:
    """``(allowed, refusals)``. An entry this function refuses is a gate FAILURE.

    Three refusals, all of them earned the expensive way:

    * **no reason.** An allowlist entry without a stated reason is an unexplained
      permission to interfere, and nobody will ever dare delete it.
    * **a blanket entry** — ``"bracket vs anything"``, a ``"*"`` on either side.
      This is the single most effective way to hide real interference: one line
      switches the check off for a part and the report stays green forever.
    * **a sliding fit.** The pair whose whole job is to move relative to the other
      is the pair that most needs the check. An intended CONTACT is allowlistable;
      an intended MOTION is not, because "they touch" and "they jam" look identical
      to a boolean at one pose.
    """
    raw = ctx.param("clash_allow") or []
    if isinstance(raw, dict):
        raw = [dict(v or {}, pair=k) for k, v in raw.items()]
    allowed: dict[tuple[str, str], str] = {}
    refusals: list[str] = []
    for entry in raw if isinstance(raw, (list, tuple)) else []:
        reason = str((entry or {}).get("reason", "")).strip() if isinstance(entry, dict) else ""
        pair = _normalise_pair(entry)
        text = json.dumps(entry, default=str)[:80]
        if pair is None:
            refusals.append(f"unreadable allowlist entry {text}")
            continue
        if any(p in ("*", "any", "anything", "") for p in pair):
            refusals.append(
                f"blanket allowlist entry {pair[0]!r} vs {pair[1]!r} refused — a wildcard "
                f"switches interference checking off for a part and the report stays green")
            continue
        if not reason:
            refusals.append(f"allowlist entry {pair[0]!r} vs {pair[1]!r} has no reason")
            continue
        if pair in sliding:
            refusals.append(
                f"allowlist entry {pair[0]!r} vs {pair[1]!r} refused: declared a sliding "
                f"fit, and a pair that must MOVE is the pair that most needs this check")
            continue
        allowed[pair] = reason
    return allowed, refusals


def _read_bonded(ctx: GateContext, sliding: set[tuple[str, str]],
                 allowed: dict[tuple[str, str], str]) -> tuple[dict, list[str]]:
    """``(bonded, refusals)``. An entry this function refuses is a gate FAILURE.

    ``bonded_joints`` is the honest middle between "every contact is a failure" and
    ``clash_allow``. A glued, welded or bonded joint is DESIGNED to be face to face,
    and on a correctly modelled one the two faces are two tessellations of the same
    nominal surface — so the pair reports a shared volume of depth times glue area,
    and the glue area is legitimately large. Judged by the prismatic volume
    tolerance, a correctly built bonded assembly opens as a page of failures, and a
    page of failures is the moment somebody writes a wildcard. This declaration
    exists to remove that moment without removing the check:

    * the VOLUME tolerance is waived for the pair, because volume scales with the
      joint's area and the area is a design quantity, not a defect;
    * the DEPTH tolerance is NOT waived — it becomes
      :data:`BONDED_CONTACT_DEPTH_TOL_MM`, and a bonded pair over it still FAILS.
      Depth is the scale-free half, so "this part is 2 mm into that one" is still
      sayable about a glued joint;
    * an unanswerable pair — a kernel that raised, a part that is not a volume, a
      negative or non-finite intersection — is still a clash for a bonded pair. The
      declaration says the contact is intended; it says nothing about the boolean
      being trustworthy.

    The refusals mirror :func:`_read_allowlist` key for key, and for the same
    reasons — an entry with no reason, a wildcard, a sliding fit — plus one more
    that only exists here: a pair that is ALSO allowlisted. The two declarations
    give opposite answers about whether the pair is still checked, and a reader who
    has to guess which one won is reading a green report they cannot defend.

    **A sliding fit can never be declared bonded, and this is not a technicality.**
    The two statements contradict each other in physics before they contradict each
    other in this file: a bond is a joint that has been given zero degrees of
    freedom, and a sliding fit is a joint whose entire purpose is one. A pair
    cannot both be glued and free to move. If it were accepted, the declaration
    would waive the volume check on exactly the pair whose failure mode is
    interference at a pose this gate never sees — and at one pose "they touch" and
    "they jam" look identical, which is why the allowlist refuses it too.
    """
    raw = ctx.param("bonded_joints") or []
    if isinstance(raw, dict):
        raw = [dict(v or {}, pair=k) for k, v in raw.items()]
    bonded: dict[tuple[str, str], str] = {}
    refusals: list[str] = []
    for entry in raw if isinstance(raw, (list, tuple)) else []:
        reason = str((entry or {}).get("reason", "")).strip() if isinstance(entry, dict) else ""
        pair = _normalise_pair(entry)
        text = json.dumps(entry, default=str)[:80]
        if pair is None:
            refusals.append(f"unreadable bonded_joints entry {text}")
            continue
        if any(p in ("*", "any", "anything", "") for p in pair):
            refusals.append(
                f"blanket bonded_joints entry {pair[0]!r} vs {pair[1]!r} refused — a "
                f"wildcard waives the volume check for every pair a part is in, and a "
                f"bonded declaration is a statement about ONE joint")
            continue
        if not reason:
            refusals.append(
                f"bonded_joints entry {pair[0]!r} vs {pair[1]!r} has no reason — an "
                f"undeclared bond is an unexplained permission to share material")
            continue
        if pair in sliding:
            refusals.append(
                f"bonded_joints entry {pair[0]!r} vs {pair[1]!r} refused: declared a "
                f"sliding fit. A bond has zero degrees of freedom and a sliding fit has "
                f"one — the pair cannot be both, and the sliding pair is the one whose "
                f"interference this gate is least able to see at a single pose")
            continue
        if pair in allowed:
            refusals.append(
                f"pair {pair[0]!r} vs {pair[1]!r} is declared both bonded and allowlisted "
                f"— one waives the pair entirely and the other keeps it under a depth "
                f"check, so which one governs is a guess. Delete one")
            continue
        bonded[pair] = reason
    return bonded, refusals


@gate(
    id="cad.clash",
    title="No unintended interference between placed solids",
    claims=["geometry", "fit", "assembly", "interference", "mechanical", "cad"],
    tier=Tier.BUILD,
    settles="part interference",
    requires_python=["trimesh", "numpy"],
    # ANY ONE boolean engine, declared so `availability` can see the disjunction.
    # What slipped through while it lived only in the body (`_boolean_engines`):
    # on a machine with trimesh and numpy but no engine, availability said the
    # tooling was present, and this gate skipped its own baseline AND its own
    # negative control — a control that never fired, which the pack tests filed
    # as "honestly blocked" and `gate selftest` counted as a tooling skip. Green
    # here (manifold3d installed) and in CI (no trimesh, so availability fails
    # first); wrong only on the machine in between.
    # *Rejected:* telling a tooling skip from any other by its `skip_reason`
    # text — that lets the gate decide for itself that its own skip is about
    # tooling. Declared, the answer is availability's alone. The in-body probe
    # stays as defence in depth for an engine that is found but will not load.
    requires_one_of=["python:manifold3d", "tool:blender", "tool:openscad"],
    # Prerequisite cad.is_volume (P2.2-D12): a boolean against a mesh that is not
    #    a volume returns garbage or nothing, and nothing reads as no clash.
    #    Isolated: the guard passes this gate's own known-bad control
    #    (test_packs.ControlsAreIsolated).
    needs=["cad.is_volume"],
    negative_control=NegativeControl(
        fixture="selftest/bad_meshes.py:overlapping_pair",
        # the known-good control on the channel this fixture uses
        # (ctx.extra): selftest/good_meshes.py says why (P2.3-D5)
        good="selftest/good_meshes.py:baseline_meshes",
        note="two valid 20 mm boxes placed 18 mm apart: 800 mm^3 of shared material, "
             "nothing else about either part changed and no allowlist entry",
    ),
)
def clash(ctx: GateContext) -> Verdict:
    """Pairwise intersection volume, AABB pre-filtered, with a per-pair tolerance.

    The meshes must already be placed in the ASSEMBLY frame. Parts booleaned at
    their own local origins either all clash or none do, and both answers are
    confident nonsense — see PACK.md, "units and frames".

    Two numbers per pair, and both must be under tolerance. The intersection VOLUME
    is the measure — it goes to zero continuously as parts separate, which is what
    lets a tolerance absorb tessellation noise. But a fixed volume tolerance
    forgives an unbounded penetration DEPTH as the contact area shrinks (0.2 mm^3
    is a 0.5 mm pin driven 0.79 mm into a block), so the equivalent depth —
    volume over the largest face of the two parts' overlapping bounding boxes — is
    checked against its own tolerance as well. That depth is a LOWER bound on the
    real penetration: a pair that fails it is at least that far in.

    **Touching is not interfering, and the gate settles that with arithmetic rather
    than with a kernel.** Two solids can only share material inside the
    INTERSECTION OF THEIR BOUNDING BOXES, so if that box is degenerate on any axis —
    the boxes meet on a face, an edge or a corner, or stand a hair apart — it has
    zero volume and the shared volume is exactly zero. No boolean is run for such a
    pair; it is recorded as ``contact``, and it is not a clash. This branch exists
    because a boolean kernel asked about two solids that touch on a coplanar face
    returns nonsense, and the nonsense arrived as a headline: *31277.200 mm^3 on
    two parts whose boxes do not overlap on any axis*, with the gate's own line
    already saying ``inf mm equivalent depth over 0.00 mm^2`` — the kernel
    shrugging, printed underneath a number that reads like 31 cm^3 of real
    interference. A reader went looking for geometry that was not there. The
    inequality above is exact, so this can never hide a real overlap: a pair
    sharing any volume at all has an overlap box that contains it, hence a positive
    extent on all three axes, hence it reaches the boolean.

    A boolean that raises ON A MACHINE THAT HAS AN ENGINE, or returns a negative or
    non-finite volume, is counted as a CLASH. That is not defensive coding, it is
    the failure mode: an inverted or corrupt input can make a kernel report a hugely
    negative intersection volume, and any code that compares a signed volume to a
    small positive tolerance will call that "well under tolerance" and report no
    clashes at all. The number that looks most like a pass is the one produced by
    the worst input.

    A machine with NO engine is the opposite situation and gets the opposite answer:
    the gate SKIPS. trimesh implements no booleans of its own and declares none of
    manifold3d, Blender or OpenSCAD as a dependency, so a bare `pip install trimesh`
    reaches the pair loop and every pair raises — which, counted as clashes, reports
    a missing tool as a broken design. That inversion is the one thing this pack
    exists to prevent, so the engines are probed once before any pair is touched.

    A part that is not a volume is also counted as a clash for the same reason as a
    raising kernel: the boolean's answer about it means nothing, and silence is the
    wrong way to say so.

    Two declarations change what a pair is judged against, and neither can be
    spelled with a wildcard or without a reason. ``clash_allow`` waives the pair
    (see :func:`_read_allowlist`). ``bonded_joints`` is the honest middle for a
    joint that is DESIGNED to be face to face: it waives the volume tolerance,
    because shared volume on a bond line is depth times glue area and glue area is
    a design quantity, and keeps a depth tolerance, because depth does not move
    with the size of the joint. A bonded pair over that depth still FAILS (see
    :func:`_read_bonded`).
    """
    meshes, welded, skip = _load_all(ctx, "cad.clash")
    if skip is not None:
        return skip
    if len(meshes) < 2:
        return _skipped(
            "cad.clash",
            f"only {len(meshes)} part(s) in the projection — interference needs at "
            f"least two placed solids; a single-part check would pass vacuously",
        )

    import numpy as np
    import trimesh

    engines = _boolean_engines()
    if not engines:
        return _skipped(
            "cad.clash",
            "no boolean engine is installed, so no pair could be intersected and no "
            "interference was measured: looked for "
            + ", ".join(name for _, name in _BOOLEAN_ENGINES)
            + ". trimesh implements no booleans itself and declares none of them as "
            "a dependency, so `pip install trimesh` alone is not enough for this gate",
        )
    ctx.log(f"cad.clash: boolean engine(s) available: {', '.join(engines)}")

    sliding = {p for p in (_normalise_pair(e) for e in (ctx.param("sliding_fits") or []))
               if p is not None}
    allowed, refusals = _read_allowlist(ctx, sliding)
    bonded, bonded_refusals = _read_bonded(ctx, sliding, allowed)
    # One refusal list, not two. Both declarations are the same document as far as
    # a reader is concerned — "which pairs get special treatment, and why" — and a
    # gate that failed on a bad allowlist while quietly ignoring a bad bonded entry
    # would have a hole in exactly the shape of the newer mechanism.
    refusals = refusals + bonded_refusals

    organic = {str(p) for p in (ctx.param("organic_parts") or [])}
    default_tol = ctx.param("clash_tolerance_mm3")
    default_tol = float(default_tol) if isinstance(default_tol, (int, float)) else PRISMATIC_CLASH_TOL_MM3
    per_pair_raw = ctx.param("clash_tolerances_mm3") or {}
    per_pair: dict[tuple[str, str], float] = {}
    if isinstance(per_pair_raw, dict):
        for key, value in per_pair_raw.items():
            pair = _normalise_pair(key)
            if pair is not None:
                per_pair[pair] = float(value)

    # The depth tolerance mirrors the volume one key for key, on purpose: the
    # argument against a global override ("raising it to silence one curved pair is
    # how a flat-plate interference stays hidden") is exactly as true for depth.
    default_depth = ctx.param("clash_depth_tol_mm")
    default_depth = (float(default_depth) if isinstance(default_depth, (int, float))
                     and not isinstance(default_depth, bool) else PRISMATIC_CLASH_DEPTH_TOL_MM)
    per_pair_depth_raw = ctx.param("clash_depth_tols_mm") or {}
    per_pair_depth: dict[tuple[str, str], float] = {}
    if isinstance(per_pair_depth_raw, dict):
        for key, value in per_pair_depth_raw.items():
            pair = _normalise_pair(key)
            if pair is not None:
                per_pair_depth[pair] = float(value)

    names = sorted(meshes)
    rows: list[dict[str, Any]] = []
    clashes: list[dict[str, Any]] = []
    contacts: list[dict[str, Any]] = []
    worst_over = 0.0

    for i, a in enumerate(names):
        for b in names[i + 1:]:
            pair = _pair_key(a, b)
            curved = a in organic or b in organic
            glued = pair in bonded
            # An EXPLICIT per-pair override still wins over the bonded default, in
            # both directions. It is the pack's existing mechanism, it costs the
            # author the pair name, and it is the only way to say "this bonded
            # joint has a deliberate 0.3 mm modelled overlap" without a wildcard.
            tol = per_pair.get(pair)
            if tol is None:
                # A bonded joint is not judged on volume at all: see
                # BONDED_CONTACT_DEPTH_TOL_MM. `inf` stays local — the row records
                # None, because `json.dump` writes a bare `Infinity` that is not
                # JSON and the evidence file has to survive being read back.
                tol = (math.inf if glued else
                       (ORGANIC_CLASH_TOL_MM3 if curved else default_tol))
            depth_tol = per_pair_depth.get(pair)
            if depth_tol is None:
                depth_tol = (BONDED_CONTACT_DEPTH_TOL_MM if glued else
                             (ORGANIC_CLASH_DEPTH_TOL_MM if curved else default_depth))
            tol_text = ("not judged (bonded joint — volume scales with glue area)"
                        if math.isinf(tol) else f"{tol:g} mm^3 tolerance")
            row: dict[str, Any] = {"a": a, "b": b,
                                   "tolerance_mm3": None if math.isinf(tol) else tol,
                                   "depth_tolerance_mm": depth_tol,
                                   "allowed": pair in allowed, "booleaned": False,
                                   "allow_reason": allowed.get(pair, ""),
                                   "bonded": glued,
                                   "bond_reason": bonded.get(pair, "")}

            ma, mb = meshes[a], meshes[b]
            bad = [n for n, m in ((a, ma), (b, mb)) if not bool(getattr(m, "is_volume", False))]
            if bad:
                row.update(volume_mm3=None, depth_mm=None, verdict="clash",
                           why=f"{', '.join(bad)} is not a volume; the boolean's answer "
                               f"about this pair is meaningless")
                rows.append(row)
                if pair not in allowed:
                    clashes.append(row)
                continue

            lo_a, hi_a = np.asarray(ma.bounds, dtype=float)
            lo_b, hi_b = np.asarray(mb.bounds, dtype=float)
            # Per-axis overlap of the two bounding boxes, signed. `gap` is the
            # largest SEPARATION over the three axes, which is exactly the negative
            # of the smallest overlap — so `gap >= 0` and "the overlap box is
            # degenerate on at least one axis" are the same statement, and both are
            # decided here rather than inferred from a kernel's answer later.
            overlap = np.minimum(hi_a, hi_b) - np.maximum(lo_a, lo_b)
            axis = int(np.argmin(overlap))
            # `or 0.0` normalises negative zero: two faces that coincide exactly give
            # an overlap of +0.0 and a gap of -0.0, which formats as "-0.0000 mm
            # apart" and reads like a sign error in the one line explaining why the
            # pair was not booleaned.
            gap = float(-overlap[axis]) or 0.0
            if gap > AABB_MARGIN_MM:
                row.update(volume_mm3=0.0, depth_mm=0.0, verdict="clear",
                           why=f"bounding boxes {gap:.3f} mm apart, boolean not run")
                rows.append(row)
                continue

            if gap >= 0.0:
                # CONTACT, NOT INTERFERENCE — and the gate says so without asking a
                # kernel a question the kernel cannot answer. The two solids meet on
                # this axis without overlapping on it, so the intersection of their
                # bounding boxes is a plane, a line or a point; it has no volume,
                # and the intersection of the SOLIDS is contained in it. The shared
                # volume is therefore exactly zero by construction, and there is
                # nothing left for a boolean to measure.
                #
                # This is the branch that removes the false positive from the
                # friction log: a bulkhead and a deck sharing one face exactly,
                # reported as "31277.200 mm^3 ... inf mm equivalent depth over
                # 0.00 mm^2" — a kernel shrugging, underneath a headline that reads
                # like 31 cm^3 of real material. The gate already knew how to say
                # "the boolean's answer about this pair is meaningless"; it says it
                # here too, and it does not report a volume it does not believe.
                #
                # The touching AREA is reported because it is the thing that is
                # actually known, and because it separates a face contact (a mating
                # surface, thousands of mm^2) from an edge or corner touch (~0),
                # which read identically in a single "they touch".
                ext = np.maximum(overlap, 0.0)
                face = float(np.prod(np.delete(ext, axis)))
                row.update(volume_mm3=0.0, depth_mm=0.0, touch_mm2=round(face, 3),
                           verdict="contact",
                           why=f"the bounding boxes do not overlap on "
                               f"{'XYZ'[axis]} ({gap:.4f} mm apart there), so no shared "
                               f"volume is geometrically possible: the parts meet over "
                               f"{face:.2f} mm^2 and do not interfere. Boolean not run — "
                               f"a kernel asked about a coplanar contact answers with "
                               f"noise, and this gate will not report a volume it does "
                               f"not believe")
                rows.append(row)
                contacts.append(row)
                continue

            row["booleaned"] = True
            try:
                inter = trimesh.boolean.intersection([ma, mb])
                volume = float(inter.volume) if inter is not None and len(inter.faces) else 0.0
            except Exception as exc:                   # noqa: BLE001 - kernel
                row.update(volume_mm3=None, depth_mm=None, verdict="clash",
                           why=f"boolean raised {type(exc).__name__}: {exc} — an "
                               f"un-booleanable pair is not a clear pair")
                rows.append(row)
                if pair not in allowed:
                    clashes.append(row)
                continue

            if not math.isfinite(volume) or volume < 0.0:
                row.update(volume_mm3=volume, depth_mm=None, verdict="clash",
                           why=f"boolean returned {volume} — a negative or non-finite "
                               f"intersection is a corrupt result, never a clear pair")
                rows.append(row)
                if pair not in allowed:
                    clashes.append(row)
                continue

            # Equivalent penetration depth. Only the overlap of the two bounding
            # boxes can carry shared material, so the widest plausible contact
            # patch is the LARGEST face of that box; mean depth over it is
            # volume/area. Taking the largest face makes this a LOWER bound on the
            # real penetration — the number errs towards forgiving, which is the
            # only direction a derived quantity may err in a gate that fails on it.
            ext = np.maximum(overlap, 0.0)
            contact = float(max(ext[0] * ext[1], ext[1] * ext[2], ext[0] * ext[2]))
            depth = (volume / contact) if contact > 0.0 else math.inf

            if not math.isfinite(depth):
                # Unreachable from here — `gap < 0` puts a positive extent on all
                # three axes, so `contact` is positive — and kept anyway, because
                # the cost of it being reachable is the exact defect above: a
                # headline volume nobody can act on, over a contact patch the gate
                # measured as zero. A depth of `inf` is the arithmetic saying the
                # kernel put material in a region with no room for it. The volume
                # is recorded as DISCARDED rather than reported: the evidence file
                # keeps what the kernel said, the verdict does not repeat it.
                row.update(volume_mm3=None, depth_mm=None, contact_mm2=contact,
                           discarded_volume_mm3=volume, verdict="contact",
                           why=f"the overlap of the two bounding boxes has no area "
                               f"({contact:g} mm^2), so it can hold no volume: the "
                               f"kernel's {volume:.4g} mm^3 is discarded rather than "
                               f"reported. These parts touch; nothing here says they "
                               f"interfere")
                rows.append(row)
                contacts.append(row)
                continue

            over = volume - tol
            over_depth = depth - depth_tol
            row.update(volume_mm3=volume, depth_mm=depth, contact_mm2=contact,
                       verdict="clash" if (over > 0 or over_depth > 0) else "clear",
                       why=f"{volume:.4f} mm^3 vs {tol_text}; "
                           f"{depth:.4f} mm equivalent depth over {contact:.2f} mm^2 "
                           f"vs {depth_tol:g} mm"
                           + (f" (bonded: {bonded[pair]})" if glued else ""))
            rows.append(row)
            if over > 0 or over_depth > 0:
                worst_over = max(worst_over, over)
                if pair not in allowed:
                    clashes.append(row)

    evidence = [_write(ctx, "clash_pairs.json",
                       {"pairs": rows, "refused_allowlist_entries": refusals,
                        "bonded_joints": [{"pair": list(p), "reason": r}
                                          for p, r in sorted(bonded.items())]})]
    n_pairs = len(rows)
    booleaned = sum(1 for r in rows if r["booleaned"])
    # Worst first: an unmeasurable pair (None) outranks any measured volume, because
    # "the kernel could not answer" is a bigger problem than a known overlap.
    clashes.sort(key=lambda r: (isinstance(r.get("volume_mm3"), float),
                                -(r.get("volume_mm3") or 0.0)))
    worst = max((r for r in rows if isinstance(r.get("volume_mm3"), float)),
                key=lambda r: r["volume_mm3"], default=None)
    measured = max((r["volume_mm3"] for r in rows
                    if isinstance(r.get("volume_mm3"), float)), default=0.0)

    if refusals:
        detail = (f"{len(refusals)} clash_allow/bonded_joints entry/entries refused, so "
                  f"the declarations are not trustworthy: {refusals[0]}")
        # No locators, deliberately. This failure is about the allowlist DOCUMENT,
        # not about geometry: no pair was measured past tolerance, and pinning the
        # parts named in a refused entry would light up two parts that may fit
        # perfectly. An unlocatable failure carries none and the site says so.
        return Verdict(gate="cad.clash", passed=False, measured=float(len(refusals)),
                       limit=0.0, units="refused entries", detail=detail, evidence=evidence)

    if clashes:
        first = clashes[0]
        vol = first.get("volume_mm3")
        shown = f"{vol:.3f}" if isinstance(vol, float) else "unmeasurable"
        # A bonded pair carries no volume limit, so the headline cannot print one.
        # Saying "vs 0.2 mm^3" for a pair nothing compared against 0.2 mm^3 would be
        # a number the gate did not use, in the line a reader acts on.
        first_tol = first.get("tolerance_mm3")
        tol_shown = (f"{first_tol:g} mm^3" if isinstance(first_tol, (int, float))
                     else "no volume limit (bonded joint)")
        detail = (f"{len(clashes)} interfering pair(s) of {n_pairs} — worst reported "
                  f"{shown} mm^3 on {first['a']}/{first['b']} vs "
                  f"{tol_shown} / "
                  f"{first['depth_tolerance_mm']:g} mm tolerance: {first['why']}")
        # THE PAYOFF. Two pins per interfering pair, each naming the other part and
        # carrying the shared volume, so `back_left interferes with grip_lid_left by
        # 0.41 mm^3` stops being a sentence somebody has to go and act on and
        # becomes two parts lit up in the viewer at the pose where it happens.
        #
        # A pin per BODY rather than one per pair: the reader clicks a verdict and
        # both offenders isolate. One pin on `a` would leave `b` — equally
        # responsible, and the one that usually moves — dark.
        #
        # Worst pair first (the list is already sorted that way, unmeasurable
        # ahead of measured) and capped, because a model exported at the wrong
        # scale clashes on every pair at once and a hundred pins is a red model,
        # not a finding. `measured` still reports every pair.
        movers = PARTS.movers(meshes)
        locators: list[Locator] = []
        # Two pins per pair, so the cap is on PAIRS and half the pin budget.
        for row in clashes[:_MAX_LOCATORS // 2]:
            volume = row.get("volume_mm3")
            depth = row.get("depth_mm")
            for near, far in ((row["a"], row["b"]), (row["b"], row["a"])):
                if isinstance(volume, float) and math.isfinite(volume):
                    label = f"{volume:.4g} mm^3 into {far}"
                    if isinstance(depth, float) and math.isfinite(depth):
                        label += f", {depth:.3g} mm deep"
                else:
                    # The kernel could not answer for this pair. Saying so on the
                    # pin matters more than the number would: "unmeasurable" is a
                    # bigger problem than a known overlap, and a blank label reads
                    # as a smaller one. The reason is built with an f-string and
                    # never with %-formatting — it can carry a kernel's own error
                    # text, and one stray `%` in that would raise out of the gate.
                    label = (f"interference unmeasurable against {far} — "
                             f"{row['why'][:60]}")
                locators.append(_locate(movers, near, label=label, value=volume))
        return Verdict(gate="cad.clash", passed=False, measured=float(len(clashes)),
                       limit=0.0, units="pairs", detail=detail, evidence=evidence,
                       locators=locators)

    deepest = max((r["depth_mm"] for r in rows
                   if isinstance(r.get("depth_mm"), float)), default=0.0)
    worst_tol = worst.get("tolerance_mm3") if worst else None
    # Contacts are counted in the pass line and never folded into "AABB-clear".
    # They are the pairs somebody designed to touch, and a reader who cannot see
    # how many there are cannot tell an assembly that mates from one that floats.
    detail = (f"0 interfering pairs of {n_pairs} ({booleaned} booleaned, "
              f"{len(contacts)} in surface contact, rest AABB-clear); "
              f"largest shared volume {measured:.4f} mm^3"
              + (f" on {worst['a']}/{worst['b']} vs "
                 + (f"{worst_tol:g} mm^3 tolerance" if isinstance(worst_tol, (int, float))
                    else "no volume limit (bonded joint)")
                 if worst else "")
              + f"; deepest equivalent penetration {deepest:.4f} mm"
              + (f"; {len(allowed)} intended contact(s) allowlisted" if allowed else "")
              + (f"; {len(bonded)} bonded joint(s) held to "
                 f"{BONDED_CONTACT_DEPTH_TOL_MM:g} mm of penetration" if bonded else ""))
    return Verdict(gate="cad.clash", passed=True, measured=0.0, limit=0.0,
                   units="pairs", detail=detail, evidence=evidence)


# --------------------------------------------------------------------------- #
# tier 1 — connectivity: the one gate in this pack that asserts an ABSENCE
# --------------------------------------------------------------------------- #
# Every other gate here asserts a PRESENCE: this thing exists and is too big, too
# thin, too close, too steep. None of them can express an absence, and a part that
# is attached to nothing has a perfectly good mesh that passes every check ABOUT
# ITSELF. `cad.clash` is the most cheerful of all about it, because a part that
# touches nothing is interfering with nothing.
#
# That is not a hypothetical. On a real assembly a rudder blade hung 42.9 mm below
# its own bracket, attached to nothing, and a pushrod stopped 11.0 mm short of the
# thing it was supposed to push. Every geometry gate was green: 34 of 37. The boat's
# steering was not connected and the report said it was fine.
#
# THE FIRST VERSION OF THIS GATE WOULD NOT HAVE CAUGHT THAT, and the reason is worth
# more than the gate. It asked only about pairs the projection DECLARED, so on the
# very project whose rudder was hanging in space it reported
#
#     [skip] cad.assembly_connected : the projection declares no required contacts
#
# — an absence of evidence rendered as an absence of problems, which is the failure
# mode of the whole method reproduced inside a gate meant to prevent it. Catching the
# defect depended on the project remembering to declare the exact pair that was
# broken, and a project that knew to declare it would probably not have broken it.
#
# So the DEFAULT question here needs no foresight:
#
#     EVERY PART MUST BE HELD BY SOMETHING.
#
# The contact graph is built from the geometry — two parts whose surfaces come within
# the mating tolerance are an edge — and the gate reports any part in no contact at
# all, and any connected COMPONENT of that graph that is not joined to the rest of
# the assembly. A floating rudder is then caught by construction, with nothing
# declared. A project may still declare a part free-standing ON PURPOSE, with a
# stated reason, exactly as `clash_allow` declares an intended overlap — but the
# default is that an unattached part is a finding, not silence.
#
# The declared pairs and chains are KEPT, because they carry intent the geometry
# cannot express: WHICH contacts matter, and in what order. Coverage says the rudder
# is attached to something; only a declaration says it must be attached to the
# BRACKET, and only a chain says the steering runs servo -> pushrod -> rudder.
#
# The gate skips on one condition and no other: fewer than two parts, where there is
# genuinely nothing to measure.

#: How far two parts that are DECLARED to be in contact may be apart before the
#: joint is not a joint, mm. A project decision, exactly like the minimum wall: it is
#: the assembly slop the design intends to absorb. It is also the threshold the
#: coverage graph uses for "these two parts touch", because that is the same physical
#: question asked without a name attached.
MATING_TOL_KEYS = ("max_mating_gap_mm", "mating_tolerance_mm")

#: Where a project declares a part that is attached to nothing ON PURPOSE — a loose
#: tool, a part shown for context, a component that is cable-tied rather than
#: fastened. Same shape and same three refusals as `clash_allow`: a stated reason, no
#: wildcards, and the gate FAILS on a bad entry rather than honouring or dropping it.
#: An undeclared free-standing part is a FINDING; this is how a project says "yes,
#: and here is why", and the reason is what a reviewer argues with later.
FREE_STANDING_KEYS = ("free_standing_parts", "unattached_parts", "context_parts")

#: Contact tolerance used when the projection states none, mm.
#:
#: The gate will still not invent a tolerance for a DECLARED contact — that is an
#: assembly decision and a declared requirement measured against a made-up number is
#: a verdict nobody chose. But the coverage question ("is this part touching anything
#: at all?") has to be answerable on a project that declared nothing, or the gate is
#: back to skipping for want of a declaration.
#:
#: So the fallback is not an assembly slop figure at all: it is the linear deviation
#: between two surfaces that are NOMINALLY THE SAME SURFACE after tessellation, which
#: is :data:`ORGANIC_CLASH_DEPTH_TOL_MM` and is derived from it rather than retyped.
#: Two parts closer than that are as coincident as an exported mesh can express.
#:
#: The error is one-signed: a tolerance this tight reports MORE parts as held by
#: nothing, never fewer, so the fallback cries wolf and cannot hide a floating part.
#: Rejected: a millimetre-scale default, which would quietly call a part resting
#: 0.8 mm above its seat "attached"; and skipping, which is the defect this whole
#: gate exists to remove.
CONTACT_FALLBACK_TOL_MM = ORGANIC_CLASH_DEPTH_TOL_MM

#: Surface sample points per part, per side of a pair, ON TOP OF every vertex. See
#: :func:`_surface_points` for why sampling the faces at all is the whole gate.
#:
#: This budget only sets how good the FIRST answer is. It is no longer the last word
#: on any pair: :func:`_pair_contact` refines a pair that comes out apart on a
#: deterministic barycentric lattice over exactly the triangles that could still hold
#: a contact, and reports that lattice's covering radius as the error bound.
#:
#: The calibration case is two 40 mm tessellated cylinders crossing at right angles
#: 0.500 mm apart, because a cylinder has vertices ONLY at its two end rings and the
#: closest approach therefore lands in the middle of a 200 mm facet with no vertex
#: within 100 mm of it — the one shape where a sampled distance is genuinely hard.
#: Measured (`selftest/check_connectivity.py` section 7):
#:
#:     50 samples -> 4.272 mm     200 -> 0.761 mm     2000 -> 0.797 mm     20000 -> 0.605 mm
#:
#: The coarse pass is still +0.30 mm at this budget and does not converge usefully at
#: ten times it, which is the measured reason the refinement exists rather than a
#: larger number here: refined, the same pair reads 0.5000 mm.
#:
#: 2000 is therefore chosen as the point where the coarse pass is close enough to
#: seed the refinement cheaply on every pair in a real assembly, not as an answer.
#: Rejected: 50, where the coarse pass is out by 3.8 mm and the refinement's near-face
#: selection has to do all the work; and 20000, which costs ten times as much for
#: 0.19 mm and still needs refining.
#:
#: Rejected outright: a ball on a plate, which was the old calibration case. Its
#: closest point is always its lowest VERTEX, so now that no vertex is thinned away
#: it reads 0.500 mm at every budget from 50 up — a check that cannot fail, measuring
#: the thinning that used to happen rather than the sampling.
CONTACT_SAMPLES = 2000

#: Points actually tested for being INSIDE the other solid, per direction. The
#: containment test only has to answer yes/no — one sample inside is
#: interpenetration — so it runs on an evenly spaced subset of the samples rather
#: than all of them.
CONTACT_PROBES = 256

#: Probes used when the pair has already been PROVEN further apart than the
#: tolerance and the only question left is whether one part is wholly inside the
#: other. One probe would very nearly do: two closed surfaces that do not meet leave
#: the whole of one either inside the other's material or outside it, with no mixed
#: case to sample for, so the answer does not depend on WHICH sample is asked. The
#: budget is 16 rather than 1 because a "part" may be several disjoint shells in one
#: mesh (a pair of screws exported as one body), and each shell is independently in
#: or out. Rejected: the full 256, which is what made this branch cost 89% of the
#: gate on a 30-body assembly — ten small components nested inside a hull's bounding
#: box, each asking a 12,000-triangle winding number 256 times to re-answer a
#: question one probe had already settled.
NESTED_PROBES = 16

#: Points used to seed :func:`_directed_gap`'s ceiling before any exact distance is
#: computed. Eight is enough because they are the eight the bounding-box bound
#: already ranks best, and the seed only has to be a VALID upper bound, not a good
#: one — every point and every triangle it fails to exclude is still measured.
_SEED_POINTS = 8

#: Sample points per branch-and-bound block in :func:`_directed_gap`. Small on
#: purpose: the block's own bounding box is what culls the other part's triangles, so
#: a tight block culls hard. Rejected: one block for every candidate point, which
#: culls nothing on a part whose samples are spread over a whole hull.
_GAP_BLOCK = 64

#: Target covering radius of the refinement lattice, as a FRACTION of the contact
#: tolerance. A pair is only refined when the coarse pass puts it further apart than
#: the tolerance, and the refinement's job is to settle that: at a tenth of the
#: tolerance the answer is either under the limit (contact) or over it by more than
#: the lattice could possibly be wrong (proven apart).
DENSE_TARGET_FRACTION = 0.1

#: Ceiling on the refinement lattice, in points per part per pair. Reached only where
#: a pair's near-contact region is both large and coarsely tessellated; the verdict
#: and the evidence then carry the covering radius that WAS achieved, and a pair the
#: lattice could not settle is reported as undecided rather than as either answer.
DENSE_POINT_CAP = 60_000

#: Chunk size for the (points x triangles) arrays below, as a PRODUCT. The same
#: reasoning as `_CAST_CHUNK_RT`: 256 probes against a 12-triangle box and 256
#: probes against a 200k-triangle hull are three orders of magnitude apart in
#: memory and identical in point count, so the budget has to be set on the product.
_NEAR_CHUNK_PT = 2_000_000

#: Generalised winding number above which a point counts as INSIDE a closed solid.
#: It is 1 strictly inside and 0 strictly outside for a watertight, consistently
#: wound mesh, and 0.5 for a point lying exactly ON the surface — so the threshold
#: has to sit between 0.5 and 1, not at 0.5. Two parts mating face to face have
#: samples sitting exactly on each other's boundary, and that is not a rare case,
#: it is what a flat joint IS. 0.75 is the midpoint of the two values that can
#: occur away from the boundary; nothing real lands between them.
#:
#: The threshold alone does not settle the boundary case and is not asked to: the
#: sum for a coincident face came back as 1.0 rather than 0.5 in this pack's own
#: baseline, because `atan2` on a numerically-zero numerator with a negative
#: denominator returns +-pi. What settles it is the weld-tolerance margin in
#: :func:`_interpenetrates`; this constant is what keeps a point that DOES evaluate
#: to 0.5 from being called inside.
#:
#: The winding number is used instead of a parity ray cast for the same kind of
#: reason: a ray that clips an edge or passes exactly through a vertex is counted
#: twice and flips that point's answer, and "are these two parts assembled into
#: each other or have they come apart" is not a question to settle on a coin toss.
_WINDING_INSIDE = 0.75

#: Words a `clash_allow` entry's `role` may carry. The pack's own spelling is
#: "required"; the rest are accepted because people write them and no two of them
#: could mean anything else. Anything NOT in one of these two sets is REFUSED
#: rather than ignored — an unrecognised role (a typo, `"requird"`) would otherwise
#: mean the entry silently stops being a required contact, which is this gate's own
#: failure mode reappearing inside its own declaration.
REQUIRED_ROLES = frozenset({"required", "required_contact", "must_touch", "mating",
                            "contact", "joint"})
#: Roles that explicitly say "allowed to interfere, NOT required to touch".
PERMITTED_ROLES = frozenset({"permitted", "allowed", "optional", "clearance",
                             "interference", "none"})

#: How many parts a coverage finding names in one verdict line before it stops
#: listing them. The count in `detail` is always the real one.
_MAX_NAMED = 4


def _halton(count: int, base: int):
    """The first ``count`` terms of the radical-inverse sequence in ``base``.

    Deterministic, and that is the point rather than the low discrepancy: a gate
    whose number moves between two runs on the same geometry cannot be cited in a
    readiness report. `mesh.sample` draws from the global numpy RNG, so two sweeps
    of the same model would report two different gaps and neither would be wrong.
    """
    import numpy as np

    index = np.arange(1, int(count) + 1, dtype=np.int64)
    out = np.zeros(int(count), dtype=float)
    fraction = 1.0
    while index.any():
        fraction /= base
        out += fraction * (index % base)
        index //= base
    return out


def _surface_points(mesh: Any, budget: int):
    """EVERY vertex AND area-weighted points ON THE FACES, as one (n, 3) array.

    **Sampling the faces is the trap, and it is a measured fact about how this gate
    goes wrong, not a style preference.** A tessellated cylinder has vertices only at
    its two end rings — there is not one vertex anywhere along its length. A rudder
    stock passing CLEAN THROUGH a bracket's bore therefore has no vertex anywhere
    near the bracket, and a vertex-only distance reported that pair **5.5 mm apart**
    while they interpenetrate. Run that way, this gate would have flagged the one
    correctly assembled joint on the boat and passed the two that were not connected
    at all: exactly inverted, and confident.

    So the surfaces are sampled as well: stratified over the cumulative face area,
    so a big triangle gets proportionally many samples and a sliver gets none, with
    barycentric coordinates from a Halton sequence so the same mesh gives the same
    points every run.

    **No vertex is dropped.** An earlier version thinned the vertex list to the
    budget, which was an optimisation for a cloud-to-cloud stage that no longer
    exists — :func:`_directed_gap` culls by bounding box now, so a long vertex list
    costs one subtraction per point. Dropping vertices also dropped the FEATURES they
    sit on (a rim, a boss, a screw head), which are exactly the places two parts
    touch.
    """
    import numpy as np

    verts = np.asarray(mesh.vertices, dtype=float)
    tri = np.asarray(mesh.triangles, dtype=float)
    areas = np.asarray(mesh.area_faces, dtype=float)
    total = float(areas.sum())
    if len(tri) == 0 or not math.isfinite(total) or total <= 0.0 or budget <= 0:
        return verts

    cumulative = np.cumsum(areas) / total
    strata = (np.arange(budget, dtype=float) + 0.5) / float(budget)
    faces = np.clip(np.searchsorted(cumulative, strata), 0, len(tri) - 1)
    r1 = _halton(budget, 2)
    r2 = _halton(budget, 3)
    root = np.sqrt(r1)
    v0, v1, v2 = tri[faces, 0, :], tri[faces, 1, :], tri[faces, 2, :]
    samples = (v0 * (1.0 - root)[:, None]
               + v1 * (root * (1.0 - r2))[:, None]
               + v2 * (root * r2)[:, None])
    return np.vstack([verts, samples])


def _point_triangle_min(points: Any, tri: Any) -> Any:
    """Least distance from each point to the closest point of ANY triangle.

    The textbook closest-point-on-triangle (Ericson, *Real-Time Collision
    Detection* 5.1.5), vectorised over triangles and chunked over points on the
    (points x triangles) product. Written here rather than called from
    ``trimesh.proximity`` for the same reason ``_cast_first_hit`` carries its own
    Moller-Trumbore: ``trimesh.proximity.closest_point`` culls candidates with an
    ``rtree`` index, ``rtree`` is not a dependency of trimesh, and on a machine
    carrying exactly what this gate declares (``trimesh``, ``numpy``) it raises
    ``ModuleNotFoundError`` from inside the call. A crash reads as "the design is
    wrong" rather than "the tool is missing".
    """
    import numpy as np

    if len(points) == 0 or len(tri) == 0:
        return np.zeros(len(points))

    a = tri[:, 0, :]
    ab = tri[:, 1, :] - a
    ac = tri[:, 2, :] - a
    out = np.empty(len(points), dtype=float)
    chunk = max(1, int(_NEAR_CHUNK_PT // max(1, len(tri))))
    for start in range(0, len(points), chunk):
        p = points[start:start + chunk]
        ap = p[:, None, :] - a[None, :, :]
        d1 = np.einsum("fj,rfj->rf", ab, ap)
        d2 = np.einsum("fj,rfj->rf", ac, ap)
        bp = ap - ab[None, :, :]
        d3 = np.einsum("fj,rfj->rf", ab, bp)
        d4 = np.einsum("fj,rfj->rf", ac, bp)
        cp = ap - ac[None, :, :]
        d5 = np.einsum("fj,rfj->rf", ab, cp)
        d6 = np.einsum("fj,rfj->rf", ac, cp)
        vc = d1 * d4 - d3 * d2
        vb = d5 * d2 - d1 * d6
        va = d3 * d6 - d5 * d4

        def _safe(numerator, denominator):
            """``numerator/denominator`` with the zero-denominator case pinned to 0.

            Every use below is inside a region test that has already decided the
            denominator is positive; the guard is for the degenerate triangle that
            reaches here anyway, where any barycentric coordinate is as good as
            another and NaN is not."""
            live = np.abs(denominator) > 0.0
            return np.where(live, numerator / np.where(live, denominator, 1.0), 0.0)

        # Barycentric (v, w) of the closest point, by region. Order matters: the
        # vertex regions are tested first, then the three edges, then the interior.
        interior_denominator = va + vb + vc
        v = _safe(vb, interior_denominator)
        w = _safe(vc, interior_denominator)

        edge_ab = (vc <= 0.0) & (d1 >= 0.0) & (d3 <= 0.0)
        v = np.where(edge_ab, _safe(d1, d1 - d3), v)
        w = np.where(edge_ab, 0.0, w)

        edge_ac = (vb <= 0.0) & (d2 >= 0.0) & (d6 <= 0.0)
        v = np.where(edge_ac, 0.0, v)
        w = np.where(edge_ac, _safe(d2, d2 - d6), w)

        edge_bc = (va <= 0.0) & ((d4 - d3) >= 0.0) & ((d5 - d6) >= 0.0)
        t_bc = _safe(d4 - d3, (d4 - d3) + (d5 - d6))
        v = np.where(edge_bc, 1.0 - t_bc, v)
        w = np.where(edge_bc, t_bc, w)

        corner_a = (d1 <= 0.0) & (d2 <= 0.0)
        v = np.where(corner_a, 0.0, v)
        w = np.where(corner_a, 0.0, w)
        corner_b = (d3 >= 0.0) & (d4 <= d3)
        v = np.where(corner_b, 1.0, v)
        w = np.where(corner_b, 0.0, w)
        corner_c = (d6 >= 0.0) & (d5 <= d6)
        v = np.where(corner_c, 0.0, v)
        w = np.where(corner_c, 1.0, w)

        closest = a[None, :, :] + ab[None, :, :] * v[:, :, None] + ac[None, :, :] * w[:, :, None]
        out[start:start + chunk] = np.sqrt(((p[:, None, :] - closest) ** 2).sum(axis=2)).min(axis=1)
    return out


def _box_gap(lo_a: Any, hi_a: Any, lo_b: Any, hi_b: Any) -> float:
    """Least distance between two axis-aligned boxes. An EXACT lower bound on the
    distance between anything inside one and anything inside the other."""
    import numpy as np

    separation = np.maximum(np.maximum(lo_a - hi_b, lo_b - hi_a), 0.0)
    return float(np.linalg.norm(separation))


def _point_box_distance(points: Any, lo: Any, hi: Any):
    """Distance from each point to a box. A lower bound on its distance to any
    surface inside that box, which is what makes it safe to prune on."""
    import numpy as np

    outside = np.maximum(np.maximum(lo - points, points - hi), 0.0)
    return np.sqrt((outside ** 2).sum(axis=1))


def _triangle_cover(tri: Any, areas: Any):
    """Per triangle, how far a point inside it can be from its NEAREST VERTEX.

    This is the covering radius of the three corners over the triangle, and it is
    what turns a barycentric lattice into a stated error bound: subdividing a
    triangle into ``k^2`` similar sub-triangles divides this number by ``k``.

    It is the circumradius for an acute triangle and half the longest edge for an
    obtuse one; ``min(circumradius, longest edge)`` bounds both and stays finite on
    the degenerate triangle whose area is zero, where the circumradius is not.
    """
    import numpy as np

    e0 = np.linalg.norm(tri[:, 1, :] - tri[:, 0, :], axis=1)
    e1 = np.linalg.norm(tri[:, 2, :] - tri[:, 1, :], axis=1)
    e2 = np.linalg.norm(tri[:, 0, :] - tri[:, 2, :], axis=1)
    longest = np.maximum(np.maximum(e0, e1), e2)
    area = np.asarray(areas, dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        circum = np.where(area > 0.0, e0 * e1 * e2 / (4.0 * np.where(area > 0.0, area, 1.0)),
                          np.inf)
    return np.minimum(circum, longest)


def _lattice(tri: Any, order: int):
    """A deterministic barycentric lattice of the given order on every triangle.

    Order ``k`` places the ``(k+1)(k+2)/2`` points ``(i/k, j/k)`` on each triangle —
    the three corners included — which splits it into ``k^2`` sub-triangles each
    similar to the original at ``1/k`` scale. Every point of the triangle is
    therefore within ``cover(T)/k`` of a lattice point, and THAT is the error bound
    the verdict quotes. Nothing here is random: the same mesh gives the same points
    on every run, which is the same requirement the Halton sampler above exists for.
    """
    import numpy as np

    order = max(1, int(order))
    i, j = np.meshgrid(np.arange(order + 1), np.arange(order + 1), indexing="ij")
    keep = (i + j) <= order
    u = (i[keep] / float(order)).astype(float)
    v = (j[keep] / float(order)).astype(float)
    w = 1.0 - u - v
    a, b, c = tri[:, 0, :], tri[:, 1, :], tri[:, 2, :]
    pts = (a[:, None, :] * w[None, :, None]
           + b[:, None, :] * u[None, :, None]
           + c[:, None, :] * v[None, :, None])
    return pts.reshape(-1, 3)


class _Solid:
    """One part, with the derived arrays every pair test wants, computed ONCE.

    A 30-body assembly is 435 pairs. Rebuilding a part's triangle table and its
    per-triangle bounding boxes inside the pair loop is how a gate that measures
    435 cheap things becomes a gate nobody runs (method rule 10) — so the arrays
    live here, per part, for the life of one gate call.
    """

    __slots__ = ("name", "mesh", "tri", "areas", "tlo", "thi", "lo", "hi",
                 "is_volume", "centroids", "_points", "_cover")

    def __init__(self, name: str, mesh: Any) -> None:
        import numpy as np

        self.name = name
        self.mesh = mesh
        self.tri = np.asarray(mesh.triangles, dtype=float)
        self.areas = np.asarray(mesh.area_faces, dtype=float)
        if len(self.tri):
            self.tlo = self.tri.min(axis=1)
            self.thi = self.tri.max(axis=1)
            self.centroids = self.tri.mean(axis=1)
        else:                                          # a part with no surface
            self.tlo = self.thi = np.zeros((0, 3), dtype=float)
            self.centroids = np.zeros((0, 3), dtype=float)
        bounds = getattr(mesh, "bounds", None)
        if bounds is None:                             # a mesh with no geometry at all
            self.lo = self.hi = np.zeros(3, dtype=float)
        else:
            bounds = np.asarray(bounds, dtype=float)
            self.lo, self.hi = bounds[0], bounds[1]
        self.is_volume = bool(getattr(mesh, "is_volume", False))
        self._points = None
        self._cover = None

    def points(self, budget: int):
        """This part's coarse sample cloud, built once however many pairs it is in."""
        if self._points is None:
            self._points = _surface_points(self.mesh, budget)
        return self._points

    def cover(self):
        """Per-triangle covering radius of its own corners. See :func:`_triangle_cover`."""
        if self._cover is None:
            self._cover = _triangle_cover(self.tri, self.areas)
        return self._cover

    def near_faces(self, lo: Any, hi: Any, radius: float):
        """Indices of the triangles whose bounding box is within ``radius`` of a box.

        Exact as a FILTER: a triangle further than ``radius`` from that box cannot
        contain a point within ``radius`` of anything inside it, so nothing that
        could matter is dropped.
        """
        import numpy as np

        if len(self.tri) == 0:
            return np.zeros(0, dtype=int)
        outside = np.maximum(np.maximum(self.tlo - hi, lo - self.thi), 0.0)
        return np.nonzero(np.sqrt((outside ** 2).sum(axis=1)) <= radius)[0]


def _directed_gap(points: Any, other: "_Solid", ceiling: float) -> float:
    """Least distance from ``points`` to ``other``'s SURFACE — exactly, or > ceiling.

    **This is the function the old one got wrong, and the way it was wrong is the
    kind that reads as a measurement.** It ran a cloud-to-cloud pass, took the 256
    candidates with the smallest cloud-to-cloud distance and refined only those
    against the triangles. Nearest-point-in-the-other-cloud does not rank
    nearest-to-the-other-SURFACE — a sample facing the middle of a large triangle is
    far from every vertex of it and zero distance from the face — so on a curved
    contact at the default budget the 256 that survived were not the 256 that held
    the answer, and the refined number was a confident value with no bound under it.

    What replaces it is a branch and bound with no arbitrary cut anywhere in it:

    * every point's distance to ``other``'s BOUNDING BOX is a rigorous lower bound on
      its distance to ``other``'s surface, because the surface is inside the box;
    * so the points are sorted by that bound and walked in blocks, and the walk STOPS
      at the first block whose bound is already worse than the best exact distance
      found — those points cannot improve on it, whatever their cloud rank;
    * inside a block, ``other``'s triangles are culled to those whose own bounding
      box is within the current best of the block's bounding box, by the same
      argument in the other direction.

    The result is exactly ``min(d(p, surface) for p in points)``, capped at
    ``ceiling`` (the caller only cares whether a pair is closer than the tolerance,
    and walking past that is work with nothing to buy). It is therefore an UPPER
    bound on the true surface-to-surface distance whose only error is that the truly
    closest point of the other part may lie BETWEEN two samples — which is what
    :func:`_pair_contact`'s lattice refinement measures and bounds, rather than
    leaving to luck.
    """
    import numpy as np

    if len(points) == 0 or len(other.tri) == 0:
        return math.inf

    bound = _point_box_distance(points, other.lo, other.hi)
    keep = np.nonzero(bound <= ceiling)[0]
    if len(keep) == 0:
        return math.inf
    order = keep[np.argsort(bound[keep], kind="stable")]
    ordered = points[order]
    bound = bound[order]

    # A cheap CEILING before any exact work, from the handful of points the lower
    # bound already likes. The distance from a point to a triangle's CENTROID is an
    # upper bound on its distance to that triangle, so the smallest such distance is
    # an upper bound on the answer — and one pass of that over the triangle list
    # collapses both the point list and the triangle list before the expensive kernel
    # runs even once. Without it the block loop walks every point whose lower bound
    # happens to be under a large answer, and re-culls the whole triangle table each
    # time: on a 30-body assembly that was two thirds of the distance measurement.
    head = ordered[:_SEED_POINTS]
    seed = math.inf
    if len(other.centroids):
        deltas = head[:, None, :] - other.centroids[None, :, :]
        seed = float(np.sqrt((deltas ** 2).sum(axis=2)).min())
    limit = min(ceiling, seed)
    if not math.isfinite(limit):
        limit = ceiling
    within = np.nonzero(bound <= limit)[0]
    if len(within) == 0:                               # pragma: no cover - head is in it
        within = np.array([0])
    ordered = ordered[within]
    bound = bound[within]
    faces = other.near_faces(ordered.min(axis=0), ordered.max(axis=0), limit)
    if len(faces) == 0:
        return math.inf
    tri = other.tri[faces]
    tlo, thi = other.tlo[faces], other.thi[faces]

    best = math.inf
    for start in range(0, len(ordered), _GAP_BLOCK):
        reach = min(best, limit)
        if bound[start] >= reach:
            break                                # nothing further can improve on it
        block = ordered[start:start + _GAP_BLOCK]
        outside = np.maximum(np.maximum(tlo - block.max(axis=0),
                                        block.min(axis=0) - thi), 0.0)
        near = np.nonzero(np.sqrt((outside ** 2).sum(axis=1)) <= reach)[0]
        if len(near) == 0:
            continue
        best = min(best, float(_point_triangle_min(block, tri[near]).min()))
    return best


def _coarse_gap(a: "_Solid", b: "_Solid", budget: int, ceiling: float) -> float:
    """The two-directional sampled distance, exact over the samples."""
    gap = _directed_gap(a.points(budget), b, ceiling)
    return min(gap, _directed_gap(b.points(budget), a, min(gap, ceiling)))


def _refine_gap(a: "_Solid", b: "_Solid", tol: float, budget: int) -> tuple:
    """``(gap_mm, error_bound_mm, lattice_points)`` for a pair the coarse pass put apart.

    ``(inf, None, 0)`` means PROVEN apart before any arithmetic, by the filter
    described below. The error is ``None`` rather than 0 there on purpose: the
    conclusion is exact, the DISTANCE is still whatever the coarse sampled pass said,
    which is an upper bound and nothing tighter.

    **The error bound is the point of this function**, and it is what lets the gate
    say a pair is apart rather than merely report a number that came out large.

    Only triangles within ``tol`` of the other part's bounding box can hold a contact
    at all, so those are the only ones refined — usually a handful even on a hull. If
    either side has none, the pair is PROVEN apart with no arithmetic: no point of
    one surface is within ``tol`` of the other's box, let alone its surface.

    Otherwise each of those triangles gets a barycentric lattice of order ``k``
    (:func:`_lattice`), which puts every point of the triangle within ``cover(T)/k``
    of a lattice point. Write that worst case ``d``. Then for the true closest point
    ``x*`` of one surface there is a lattice point within ``d`` of it, so

        measured <= true + d      and therefore      true >= measured - d

    and ``measured - d > tol`` PROVES the pair is further apart than the tolerance.
    The error is one-signed in the safe direction throughout: a sampled distance is
    an upper bound on the true one, so a thin lattice cries wolf and cannot hide a
    contact.

    ``k`` is chosen to reach a tenth of the tolerance and capped at
    :data:`DENSE_POINT_CAP` points per side; when the cap binds, the bound that WAS
    achieved is returned and the caller reports the pair as undecided rather than
    picking whichever answer it prefers.
    """
    import numpy as np

    target = max(tol * DENSE_TARGET_FRACTION, WELD_TOL_MM)

    def box_of(solid, faces):
        block = solid.tri[faces]
        return block.min(axis=(0, 1)), block.max(axis=(0, 1))

    # Narrow each side against the OTHER SIDE'S NEAR REGION rather than against its
    # whole bounding box, three rounds, and the sets collapse. Each round is exact as
    # a filter: a point of one surface within `tol` of the other has its closest
    # partner inside the region the previous round kept, so it survives every round.
    # A wire lying inside a hull's bounding box otherwise gets a lattice over every
    # one of its triangles to settle a contact that is confined to one end of it.
    faces_a = a.near_faces(b.lo, b.hi, tol)
    if len(faces_a) == 0:
        return math.inf, None, 0                     # proven apart, nothing measured
    faces_b = b.near_faces(*box_of(a, faces_a), tol)
    if len(faces_b) == 0:
        return math.inf, None, 0
    faces_a = a.near_faces(*box_of(b, faces_b), tol)
    if len(faces_a) == 0:                            # pragma: no cover - monotone
        return math.inf, None, 0

    def sweep(aim: float, cap: int, ceiling: float) -> tuple:
        """One pass at a stated covering radius. The FINER side goes first: its
        lattice is the one that can carry a small error bound, and once
        ``measured - bound > tol`` the pair is settled and the coarser side's lattice
        is work with nothing left to buy."""
        plan = []
        for side, faces, other in ((a, faces_a, b), (b, faces_b, a)):
            cover = side.cover()[faces]
            finite = cover[np.isfinite(cover)]
            span = float(finite.max()) if len(finite) else 0.0
            order = 1 if span <= aim else int(math.ceil(span / aim))
            if len(faces) * ((order + 1) * (order + 2) // 2) > cap:
                # Largest order that fits: (k+1)(k+2)/2 <= cap/len(faces).
                allowance = max(1.0, cap / float(len(faces)))
                order = max(1, int(math.floor(math.sqrt(2.0 * allowance)) - 1))
            plan.append((span / float(order), side, faces, order, other))
        plan.sort(key=lambda item: item[0])

        gap, bound, spent = ceiling, math.inf, 0                # noqa: F841
        for achieved, side, faces, order, other in plan:
            points = _lattice(side.tri[faces], order)
            spent += len(points)
            gap = min(gap, _directed_gap(points, other, gap))
            bound = min(bound, achieved)
            if gap <= tol or (gap - bound) > tol:
                break                                # settled, either way
        return gap, (0.0 if math.isinf(bound) else bound), spent

    gap, best_cover, total = sweep(target, DENSE_POINT_CAP, math.inf)
    if math.isfinite(gap) and tol < gap <= tol + best_cover:
        # Marginal: further apart than the tolerance, but by less than the lattice
        # can vouch for. Aim the second pass at the margin that is actually in
        # dispute rather than at a fixed fraction of the tolerance, and pay for it —
        # this runs for one pair in an assembly, not for every pair.
        aim = max((gap - tol) / 3.0, WELD_TOL_MM)
        if aim < target:
            again, cover, spent = sweep(aim, 4 * DENSE_POINT_CAP, gap)
            total += spent
            if cover < best_cover or again < gap:
                gap, best_cover = min(gap, again), min(best_cover, cover)
    return gap, best_cover, total


def _any_inside(points: Any, mesh: Any) -> bool:
    """Is ANY of ``points`` inside the closed solid ``mesh``?

    Generalised winding number (Van Oosterom & Strackee's solid angle, summed over
    the faces): +-1 inside a closed consistently wound mesh and 0 outside. Chosen
    over a parity ray cast because a ray that clips an edge or passes exactly
    through a vertex is counted twice and flips the answer for that point, and this
    decision is what separates "these two parts are assembled into each other" from
    "these two parts have come apart" — a coin-flip there is worse than no test.
    """
    import numpy as np

    tri = np.asarray(mesh.triangles, dtype=float)
    if len(points) == 0 or len(tri) == 0:
        return False
    chunk = max(1, int(_NEAR_CHUNK_PT // max(1, len(tri))))
    for start in range(0, len(points), chunk):
        p = points[start:start + chunk]
        a = tri[None, :, 0, :] - p[:, None, :]
        b = tri[None, :, 1, :] - p[:, None, :]
        c = tri[None, :, 2, :] - p[:, None, :]
        la = np.linalg.norm(a, axis=2)
        lb = np.linalg.norm(b, axis=2)
        lc = np.linalg.norm(c, axis=2)
        numerator = np.einsum("rfj,rfj->rf", a, np.cross(b, c))
        denominator = (la * lb * lc
                       + np.einsum("rfj,rfj->rf", a, b) * lc
                       + np.einsum("rfj,rfj->rf", a, c) * lb
                       + np.einsum("rfj,rfj->rf", b, c) * la)
        winding = np.arctan2(numerator, denominator).sum(axis=1) / (2.0 * math.pi)
        if bool((np.abs(winding) > _WINDING_INSIDE).any()):
            return True
    return False


def _probe_subset(points: Any, mesh: Any, budget: int = CONTACT_PROBES):
    """The samples worth asking the containment question about: those inside
    ``mesh``'s bounding box, thinned to ``budget``, evenly spaced.

    A point outside the box cannot be inside the solid, so the filter is exact and
    free. The thinning is not exact and does not need to be: one sample inside is
    enough to say the pair shares material, and the case a thin probe set could
    miss — two surfaces crossing over a sliver of each other — has a
    surface-to-surface distance of ~0 anyway, so it comes out CONNECTED by the
    distance instead.
    """
    import numpy as np

    if len(points) == 0:
        return points
    lo, hi = np.asarray(mesh.bounds, dtype=float)
    inside_box = np.all((points >= lo) & (points <= hi), axis=1)
    kept = points[inside_box]
    if len(kept) > budget:
        idx = np.unique(np.linspace(0, len(kept) - 1, int(budget)).round().astype(int))
        kept = kept[idx]
    return kept


def _probe_budget(mesh: Any) -> int:
    """How many points the containment test may ask about, for THIS mesh.

    The winding number costs one solid angle per (probe x triangle), so 256 probes
    against a 12-triangle box and 256 against a 12,000-triangle hull are three orders
    of magnitude apart in work and identical in probe count. The budget is therefore
    set on the PRODUCT, the same way every other chunk in this file is, and floors at
    16 so the test never degenerates into a single lucky sample.

    This is the constant that used to make this gate unaffordable. Containment ran
    first, at a flat 256 probes, on every pair whose boxes overlapped — 89% of the
    gate's run time on a 30-body assembly, spent deciding pairs the distance test was
    about to call connected anyway.
    """
    try:
        faces = max(1, int(len(mesh.faces)))
    except (AttributeError, TypeError):                # pragma: no cover - not a mesh
        faces = 1
    return int(max(16, min(CONTACT_PROBES, _NEAR_CHUNK_PT // (8 * faces))))


def _interpenetrates(points: Any, mesh: Any, probes: int = CONTACT_PROBES) -> bool:
    """Does any of ``points`` sit INSIDE ``mesh``, by more than a weld tolerance?

    The margin is what makes the answer stable, and it was put here by a measured
    misclassification rather than by caution. Two parts mating face to face have
    samples lying EXACTLY on each other's boundary, where the winding number is 0.5
    in exact arithmetic — and where `atan2` on a near-zero numerator with a negative
    denominator returns +-pi rather than 0, so the sum lands on 1.0 and a flat
    contact reads as interpenetration. The baseline's carriage sitting on the
    cavity floor did exactly that: every one of its bottom-face samples, at z = 3.0
    against a floor at z = 3.0, came back "inside the housing".

    So a point is only inside if it is inside AND further from the surface than
    :data:`WELD_TOL_MM` — the distance at which this pack already says two points
    are the same point. Below that a sample is ON the surface, not in the material.
    A real press fit is orders of magnitude deeper than the weld tolerance and is
    unaffected; the gap that the pair reports is 0 either way, so this changes what
    the evidence CALLS the pair, never whether it passes.
    """
    import numpy as np

    probes = _probe_subset(points, mesh, probes)
    if len(probes) == 0:
        return False
    depth = _point_triangle_min(probes, np.asarray(mesh.triangles, dtype=float))
    deep = probes[depth > WELD_TOL_MM]
    if len(deep) == 0:
        return False
    return _any_inside(deep, mesh)


def _nested(inner: "_Solid", outer: "_Solid") -> bool:
    """Is ``inner``'s bounding box entirely within ``outer``'s?"""
    import numpy as np

    return bool(np.all(inner.lo >= outer.lo) and np.all(inner.hi <= outer.hi))


def _pair_contact(a: "_Solid", b: "_Solid", tol: float, budget: int,
                  label: bool = False) -> dict:
    """``{gap_mm, error_mm, touching, interpenetrating, decidable, why}`` for one pair.

    **Interpenetration is connection, and its gap is 0.** A shaft in a coupler, a
    screw in an insert and a stock in a bearing all overlap by design, so shared
    material is the HEALTHY answer here and only a GAP is a finding: a rod modelled
    inside a solid tube has two surfaces that never touch, and an unsigned distance
    would report the annulus between them as a gap and call an assembled driveline
    broken.

    The ORDER of the two tests is the cost of this gate, and the old order was
    backwards. Containment ran first, on every pair whose boxes overlapped, and cost
    **89% of the gate's run time** on a 30-body assembly — a winding number over
    every triangle of a 12,000-triangle hull, for pairs that the distance test was
    about to call connected anyway. Reversed, it is nearly free, and the answer is
    identical: if the surfaces are within the tolerance the pair is connected and
    containment cannot change that; only a pair that is measurably APART can still
    be sharing material.

    And once a pair is PROVEN apart by more than the tolerance, containment is
    almost always ruled out by arithmetic instead of by a winding number. Two closed
    solids whose boundaries do not meet are either disjoint or one is wholly inside
    the other, and wholly inside means its bounding box is inside too. So the
    containment test runs only where a box is nested in a box, and only in the
    direction that nesting allows.

    ``decidable`` is that reasoning made explicit, and it is an AND, not an OR. Only
    a VOLUME can be asked what is inside it. If both nesting directions are
    geometrically possible then BOTH parts must be volumes before "they do not share
    material" is a conclusion rather than a hope — one direction tested and one
    untested rules out nothing. Where no nesting is possible, nothing needs to be a
    volume: the geometry has already answered.

    ``label`` asks for the extra question a CONNECTED pair can still be asked: are
    these two touching face to face, or driven into each other? It changes no
    verdict — both are contact — so it is asked only for the pairs a project
    DECLARED, where the evidence row saying "interpenetrating by design" is what
    tells a reader the press fit is still a press fit. A coverage edge is not asked,
    because paying a winding number per edge to improve a sentence is how this gate
    stopped being runnable the first time.
    """
    gap = _coarse_gap(a, b, budget, math.inf)
    error: float | None = 0.0
    lattice = 0
    filtered = False
    if gap > tol and math.isfinite(gap):
        refined, error, lattice = _refine_gap(a, b, tol, budget)
        if math.isinf(refined):
            filtered = True                          # proven apart without measuring
        else:
            gap = min(gap, refined)

    if gap <= tol:
        shared = False
        if label and _box_gap(a.lo, a.hi, b.lo, b.hi) <= 0.0:
            for inner, outer in ((a, b), (b, a)):
                if outer.is_volume and _interpenetrates(inner.points(budget), outer.mesh,
                                                        _probe_budget(outer.mesh)):
                    shared = True
                    break
        return {"gap_mm": 0.0 if shared else float(gap), "error_mm": 0.0,
                "touching": True, "interpenetrating": shared, "decidable": True,
                "lattice_points": lattice,
                "why": ("the parts share material — interpenetration is contact" if shared
                        else f"{gap:.4f} mm between the nearest surfaces, "
                             f"within {tol:g} mm")}

    proven_apart = (filtered or math.isinf(gap)
                    or (error is not None and (gap - error) > tol))
    directions = []
    if _nested(a, b):
        directions.append((a, b))
    if _nested(b, a):
        directions.append((b, a))
    if not proven_apart:
        # The lattice could not settle it, so the nesting shortcut is not available
        # either: fall back to asking the containment question in both directions.
        directions = [(a, b), (b, a)]

    decidable = True
    probes = NESTED_PROBES if proven_apart else CONTACT_PROBES
    for inner, outer in directions:
        if not outer.is_volume:
            decidable = False                   # cannot ask a non-solid what is in it
            continue
        if _interpenetrates(inner.points(budget), outer.mesh, probes):
            return {"gap_mm": 0.0, "error_mm": 0.0, "touching": True,
                    "interpenetrating": True, "decidable": True,
                    "lattice_points": lattice,
                    "why": "the parts share material — interpenetration is contact"}

    shown = "unmeasurable" if math.isinf(gap) else f"{gap:.4f} mm"
    why = f"{shown} between the nearest surfaces"
    if filtered:
        why += (f"; proven more than {tol:g} mm apart by the bounding-box filter — no "
                f"triangle of either part comes within the tolerance of the other, so "
                f"no lattice was needed and the figure is a sampled UPPER bound")
    elif error:
        why += f" (+-{error:.4f} mm, the refinement lattice's covering radius)"
    if not proven_apart:
        why += ("; the refinement lattice hit its point cap before it could separate "
                "this from the tolerance, so the pair is UNDECIDED")
    if not decidable:
        why += ("; one part is inside the other's bounding box and the enclosing part "
                "is not a volume, so interpenetration could not be tested — settle "
                "cad.is_volume first")
    return {"gap_mm": float(gap) if math.isfinite(gap) else None,
            "error_mm": None if error is None else float(error),
            "touching": False, "interpenetrating": False,
            "decidable": bool(decidable and proven_apart), "lattice_points": lattice,
            "why": why}


#: Where a project says which pairs MUST touch, primary spelling first.
#:
#: Three spellings, one requirement, and the first one is not new: a project that
#: has written `clash_allow` has already listed most of these pairs, each with the
#: stated reason this gate needs, and making it write them a second time under
#: another key is how the two lists go out of step. So a `clash_allow` entry may
#: carry `"role": "required"` and it becomes a required contact as well as a
#: permitted interference. `mating_pairs` is for the contacts that never interfere
#: and therefore never needed an allowlist entry — a foot on a floor, a flange on a
#: rim. `assembly_chains` is the ordered form.
#:
#: None of these is required for the gate to say something useful. They add the
#: intent the geometry cannot express — WHICH contacts matter, and in what order —
#: on top of the coverage question, which needs no declaration at all.
MATING_PAIR_KEYS = ("mating_pairs", "assembly_joints")
MATING_CHAIN_KEYS = ("assembly_chains", "mating_chains", "linkages")
MATING_SAMPLE_KEYS = ("mating_samples",)


def _pair_text(entry: Any) -> str:
    """``'a' vs 'b'`` for a message, or the raw entry when it is unreadable."""
    pair = _normalise_pair(entry)
    if pair is None:
        return json.dumps(entry, default=str)[:80]
    return f"{pair[0]!r} vs {pair[1]!r}"


def _entry_tolerance(entry: Any, label: str, refusals: list) -> float | None:
    """A per-entry mating tolerance in mm, or ``None`` to inherit the project's.

    A tolerance that is present and unusable is refused rather than ignored, for
    the same reason `wall_samples` is: a projection that states a number means it,
    and silently replacing it with a default is a gate measuring against a limit
    nobody chose.
    """
    if not isinstance(entry, dict):
        return None
    for key in ("tol_mm", "max_gap_mm", "gap_tol_mm"):
        if key not in entry:
            continue
        value = entry.get(key)
        if (isinstance(value, (int, float)) and not isinstance(value, bool)
                and math.isfinite(float(value)) and float(value) >= 0.0):
            return float(value)
        refusals.append(
            f"{label}: {key} is {value!r}; a mating tolerance is a non-negative number "
            f"of mm, and this gate will not substitute a default for a stated one")
        return None
    return None


def _contact_from(entry: Any, source: str, refusals: list) -> tuple | None:
    """``(pair, reason, tol)`` from one declared contact, or ``None`` + a refusal.

    The refusals mirror :func:`_read_allowlist` key for key, and deliberately: a
    project should not have to learn two sets of rules for two lists that are the
    same document. A wildcard here is worse than a wildcard there, in fact — "this
    part must touch anything" is not a claim that can be false, so it is a
    permanent green tick on the one gate that exists to notice an absence.
    """
    pair = _normalise_pair(entry)
    text = json.dumps(entry, default=str)[:80]
    if pair is None:
        refusals.append(f"unreadable {source} entry {text}")
        return None
    label = f"{source} entry {pair[0]!r} vs {pair[1]!r}"
    if any(p in ("*", "any", "anything", "") for p in pair):
        refusals.append(
            f"blanket {label} refused — 'must touch anything' is a claim that cannot "
            f"be false, and a required contact that cannot fail is not a requirement")
        return None
    if pair[0] == pair[1]:
        refusals.append(
            f"{label} names one part twice — a part is always in contact with itself")
        return None
    reason = str((entry or {}).get("reason", "")).strip() if isinstance(entry, dict) else ""
    if not reason:
        refusals.append(
            f"{label} has no reason — an undeclared requirement to touch is one "
            f"nobody can check, delete or defend when the design moves")
        return None
    return pair, reason, _entry_tolerance(entry, label, refusals)


def _read_required_contacts(ctx: GateContext) -> tuple[list, list, dict]:
    """``(contacts, refusals, chains)``. A refused entry is a gate FAILURE.

    One pair may be declared from more than one place — a `clash_allow` entry
    marked required and a chain step naming the same two parts is the ordinary
    case, not a mistake — so the sources are merged into one row and **the
    tightest stated tolerance wins**. That is a rule, not a guess: two
    declarations of a required contact do not contradict each other the way
    `clash_allow` and `bonded_joints` do (one waives the pair, the other keeps it
    under a depth check, so which governs would be a coin toss); they both say
    "these must touch", and the stricter of two compatible statements is the one
    that has to hold.
    """
    contacts: dict[tuple[str, str], dict] = {}
    refusals: list[str] = []
    chains: dict[str, list] = {}

    def add(pair, reason, tol, source, chain="", index=0, arrow=""):
        row = contacts.setdefault(pair, {"pair": pair, "reasons": [], "sources": [],
                                         "chains": [], "steps": [], "tol_mm": None})
        if reason not in row["reasons"]:
            row["reasons"].append(reason)
        if source not in row["sources"]:
            row["sources"].append(source)
        if chain and chain not in row["chains"]:
            row["chains"].append(chain)
        if chain:
            row["steps"].append({"chain": chain, "index": index, "step": arrow})
        if tol is not None:
            row["tol_mm"] = tol if row["tol_mm"] is None else min(row["tol_mm"], tol)

    # -- 1. the list the project already wrote ----------------------------- #
    raw_allow = ctx.param("clash_allow") or []
    if isinstance(raw_allow, dict):
        raw_allow = [dict(v or {}, pair=k) for k, v in raw_allow.items()]
    for entry in raw_allow if isinstance(raw_allow, (list, tuple)) else []:
        if not isinstance(entry, dict):
            continue                      # cad.clash refuses it; not this gate's job
        stated = entry.get("role", entry.get("contact"))
        must = entry.get("must_touch")
        if stated is None and must is None:
            continue                      # a plain permission, and that is fine
        word = str(stated).strip().lower() if stated is not None else ""
        if must is True:
            word = word or "required"
        elif must is False:
            continue
        elif must is not None:
            refusals.append(
                f"clash_allow entry {_pair_text(entry)}: must_touch is {must!r}, "
                f"which is neither true nor false")
            continue
        if word in PERMITTED_ROLES:
            continue
        if word not in REQUIRED_ROLES:
            refusals.append(
                f"clash_allow entry {_pair_text(entry)} has role {stated!r}, which "
                f"this gate does not recognise. Use one of "
                f"{', '.join(sorted(REQUIRED_ROLES))} for a pair that MUST touch, or one "
                f"of {', '.join(sorted(PERMITTED_ROLES))} for one that merely may — an "
                f"unrecognised role would silently mean 'not required', which is this "
                f"gate's own failure mode hiding inside its own declaration")
            continue
        parsed = _contact_from(entry, "clash_allow", refusals)
        if parsed is not None:
            add(parsed[0], parsed[1], parsed[2], f"clash_allow(role={word})")

    # -- 2. the contacts that never interfere, so never needed an allowlist -- #
    raw_pairs, pair_key = ctx.first_pack_param_named(MATING_PAIR_KEYS)
    if isinstance(raw_pairs, dict):
        raw_pairs = [dict(v or {}, pair=k) for k, v in raw_pairs.items()]
    for entry in raw_pairs if isinstance(raw_pairs, (list, tuple)) else []:
        parsed = _contact_from(entry, pair_key or MATING_PAIR_KEYS[0], refusals)
        if parsed is not None:
            add(parsed[0], parsed[1], parsed[2], pair_key or MATING_PAIR_KEYS[0])

    # -- 3. ordered chains ------------------------------------------------- #
    raw_chains, chain_key = ctx.first_pack_param_named(MATING_CHAIN_KEYS)
    chain_key = chain_key or MATING_CHAIN_KEYS[0]
    entries: list[tuple[str, Any]] = []
    if isinstance(raw_chains, dict):
        entries = [(str(k), v) for k, v in raw_chains.items()]
    elif isinstance(raw_chains, (list, tuple)):
        entries = [(str((e or {}).get("name", f"chain{i + 1}"))
                    if isinstance(e, dict) else f"chain{i + 1}", e)
                   for i, e in enumerate(raw_chains)]
    for name, entry in entries:
        body = (entry.get("chain", entry.get("members", entry.get("parts")))
                if isinstance(entry, dict) else entry)
        reason = str((entry or {}).get("reason", "")).strip() if isinstance(entry, dict) else ""
        label = f"{chain_key} {name!r}"
        tol = _entry_tolerance(entry, label, refusals)
        if not isinstance(body, (list, tuple)) or len(body) < 2:
            refusals.append(
                f"{label} is not an ordered list of at least two parts ({body!r}) — a "
                f"chain of one member states nothing")
            continue
        members = [str(m).strip() for m in body]
        if not reason:
            refusals.append(
                f"{label} has no reason — a chain is the most load-bearing declaration "
                f"here (it says what the assembly is FOR) and an unexplained one is the "
                f"first thing deleted when it starts failing")
            continue
        if any(p in ("*", "any", "anything", "") for p in members):
            refusals.append(f"{label} contains a wildcard member — refused for the same "
                            f"reason a wildcard pair is")
            continue
        chains[name] = members
        for index, (a, b) in enumerate(zip(members[:-1], members[1:])):
            if a == b:
                refusals.append(f"{label} repeats {a!r} consecutively")
                continue
            add(_pair_key(a, b), reason, tol, chain_key, chain=name,
                index=index + 1, arrow=f"{a} -> {b}")

    ordered = sorted(contacts.values(), key=lambda r: r["pair"])
    return ordered, refusals, chains


def _read_free_standing(ctx: GateContext) -> tuple[dict, list, str]:
    """``({part: reason}, refusals, key)`` — the parts declared to touch nothing.

    The counterpart of `clash_allow` for the opposite finding. Coverage's default is
    that an unattached part is a defect, because on the assembly this gate was
    written for the unattached part WAS the defect and nobody had declared anything.
    But a loose tool, a part shown for context and a component held by a cable tie
    that is not modelled are all real, so a project may say so — with a stated
    reason, which is the whole difference between a declaration and a silencer.

    Refused, not ignored, for the same three things `clash_allow` refuses: an
    unreadable entry, a wildcard (``"everything is free-standing"`` is not a claim
    that can be false) and a missing reason.
    """
    raw, key = ctx.first_pack_param_named(FREE_STANDING_KEYS)
    key = key or FREE_STANDING_KEYS[0]
    refusals: list[str] = []
    out: dict[str, str] = {}
    if raw is None:
        return out, refusals, key

    entries: list[tuple[Any, Any]] = []
    if isinstance(raw, dict):
        entries = list(raw.items())
    elif isinstance(raw, (list, tuple)):
        for item in raw:
            if isinstance(item, dict):
                name = item.get("part", item.get("name", item.get("id")))
                entries.append((name, item))
            else:
                entries.append((item, None))
    else:
        refusals.append(
            f"{key} is {type(raw).__name__} {raw!r}; it is a list of "
            f"{{part, reason}} or a {{part: reason}} map")
        return out, refusals, key

    for name, body in entries:
        label = f"{key} entry {json.dumps(name, default=str)[:40]}"
        if not isinstance(name, str) or not name.strip():
            refusals.append(f"unreadable {label} — a free-standing declaration names "
                            f"one part")
            continue
        part = name.strip()
        if part in ("*", "any", "anything"):
            refusals.append(
                f"blanket {key} entry {part!r} refused — 'everything is free-standing' "
                f"switches off the one check here that needs no foresight, and a "
                f"declaration that cannot be wrong is not a declaration")
            continue
        if isinstance(body, dict):
            reason = str(body.get("reason", "")).strip()
        elif body is None:
            reason = ""
        else:
            reason = str(body).strip()
        if not reason:
            refusals.append(
                f"{key} entry {part!r} has no reason — an undeclared free-standing "
                f"part is indistinguishable from a part somebody forgot to attach, "
                f"which is the defect this gate exists to find")
            continue
        out[part] = reason
    return out, refusals, key


def _components(names: list, edges: dict) -> list:
    """Connected components of the contact graph, largest first, each sorted.

    Plain union-find rather than a library: the graph has one edge per touching pair
    and the answer has to be reproducible in a report, not fast.
    """
    parent = {name: name for name in names}

    def find(item):
        while parent[item] != item:
            parent[item] = parent[parent[item]]
            item = parent[item]
        return item

    for (a, b), row in edges.items():
        if not row["touching"]:
            continue
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    groups: dict[str, list] = {}
    for name in names:
        groups.setdefault(find(name), []).append(name)
    return sorted((sorted(g) for g in groups.values()),
                  key=lambda g: (-len(g), g[0]))


def _nearest(solid: "_Solid", others: list, budget: int, cap: int = 24) -> tuple:
    """``(name, gap_mm)`` — the closest other part, and how far away it is.

    Only ever asked about a part that is already known to be touching nothing, so
    the number is a description of the finding rather than the finding itself: "the
    rudder is 42.9 mm from the nearest thing in the assembly" is the sentence that
    makes a reader go and look.

    The search is ordered by bounding-box distance, which is an exact lower bound, so
    the running best is a ceiling for every pair after it and most candidates are
    rejected on arithmetic. ``cap`` bounds the work on an assembly where everything
    is adrift; the answer is then the nearest of the ones examined, which is an upper
    bound and still true as "no further than".
    """
    ranked = sorted(others, key=lambda other: _box_gap(solid.lo, solid.hi,
                                                       other.lo, other.hi))
    best_name, best = "", math.inf
    for other in ranked[:cap]:
        if _box_gap(solid.lo, solid.hi, other.lo, other.hi) >= best:
            break
        gap = _coarse_gap(solid, other, budget, best)
        if gap < best:
            best, best_name = gap, other.name
    return best_name, best


def _gap_text(gap: Any) -> str:
    """One number, or the honest word for not having one."""
    if isinstance(gap, (int, float)) and math.isfinite(gap):
        return f"{float(gap):.2f} mm"
    return "an unmeasurable distance"


@gate(
    id="cad.assembly_connected",
    title="Every part is held by something, and every declared contact still closed",
    claims=["geometry", "fit", "assembly", "connectivity", "linkage", "mechanical", "cad"],
    tier=Tier.BUILD,
    settles="assembly connectivity",
    requires_python=["trimesh", "numpy"],
    # Prerequisite cad.is_volume (P2.2-D12): contact between parts is a boolean
    #    question, and booleans need volumes.
    #    Isolated: the guard passes this gate's own known-bad control
    #    (test_packs.ControlsAreIsolated).
    needs=["cad.is_volume"],
    negative_control=NegativeControl(
        fixture="selftest/bad_meshes.py:broken_chain",
        # the known-good control on the channel this fixture uses
        # (ctx.extra): selftest/good_meshes.py says why (P2.3-D5)
        good="selftest/good_meshes.py:baseline_meshes",
        note="the baseline assembly with the cover lifted 3 mm off the housing rim "
             "it is declared to seat on — a lid that is not on the box. One member "
             "of one declared chain moved, in the direction this gate measures, and "
             "nothing else: the cover is still a valid watertight solid, still "
             "inside the envelope, still the right thickness, and now interferes "
             "with even less than before. Every other gate in this pack passes it, "
             "which is the whole reason this gate exists. The cover is also then "
             "attached to nothing at all, so the fixture fires the coverage check "
             "and the declared-contact check at once — which is the relationship "
             "between the two: the declaration says WHICH contact, coverage says "
             "there has to be one",
    ),
)
def assembly_connected(ctx: GateContext) -> Verdict:
    """Every part held by something, and every pair declared to touch still touching.

    **This is the only gate in the pack that asserts an ABSENCE — of a gap.** Every
    other one asserts a presence: this thing exists and is too big, too thin, too
    close, too steep. A part that is attached to nothing passes all of them, because
    its own mesh is fine and it is interfering with nobody; `cad.clash` is happiest
    of all about a part that touches nothing. On a real boat that arithmetic gave 34
    of 37 gates green on an assembly whose steering was not connected: a rudder blade
    hanging 42.9 mm below its own bracket and a pushrod stopping 11.0 mm short of the
    tiller it was supposed to push.

    **The default question is coverage, and it needs no declaration.** The contact
    graph is built from the geometry — two parts whose surfaces come within the
    mating tolerance are an edge — and a part in no contact at all, or a group of
    parts joined to each other but not to the rest of the assembly, is a finding. The
    first version of this gate asked only about DECLARED pairs and therefore skipped
    on the very project the defect came from, which is an absence of evidence
    rendered as an absence of problems. Coverage is what makes the answer independent
    of whether anybody thought of the pair that broke.

    **The declarations are kept, because they carry what geometry cannot.** Coverage
    can say the rudder is touching something; only `clash_allow` with
    ``"role": "required"``, `mating_pairs` or `assembly_chains` can say it must be
    touching the BRACKET, and only a chain can say the steering runs servo ->
    pushrod -> rudder and name the step that broke.

    Four things about the measurement, each of which is a way this gate can be
    written wrong:

    * **It samples the FACES, not just the vertices.** A tessellated cylinder has
      vertices only at its two end rings, so a stock passing clean through a bearing
      bore has no vertex anywhere near it and a vertex-only distance called that pair
      5.5 mm apart. Written that way this gate would have failed the one joint that
      was assembled and passed the two that were not. See :func:`_surface_points`.
    * **A pair that comes out APART is refined until the answer has a bound under
      it.** The sampled distance is an upper bound on the true one; a pair further
      apart than the tolerance is re-measured on a barycentric lattice over exactly
      the triangles that could still hold a contact, and the lattice's covering
      radius is reported as the error. ``measured - error > tolerance`` is a proof,
      not an impression. See :func:`_refine_gap`.
    * **Interpenetration is contact and its gap is 0.** A screw in its insert, a
      stock in its bearing and a shaft in its tube overlap by design, and a rod
      modelled inside a solid tube has two surfaces that never meet at all. This gate
      is asking the opposite question from `cad.clash`, about a different set of
      pairs, and the two never disagree: one bounds how much material a pair may
      share, the other bounds how far apart it may be.
    * **A declared member that is not in the assembly is a failure, not a skip.**
      The most complete way for a contact to be open is for one end of it never to
      have been modelled — which is exactly what happened to the rudder stock and
      the tiller arm that the pushrod was measured against.

    The gate skips on one condition: fewer than two parts, where there is genuinely
    nothing to measure.
    """
    contacts, refusals, chains = _read_required_contacts(ctx)
    free_standing, free_refusals, free_key = _read_free_standing(ctx)
    refusals = refusals + free_refusals

    def refused(items: list) -> Verdict:
        # No locators: this failure is about the DOCUMENT, not the geometry, and
        # pinning the parts named in a refused entry would light up two parts that
        # may be perfectly assembled. The same choice cad.clash makes.
        return Verdict(
            gate="cad.assembly_connected", passed=False, measured=float(len(items)),
            limit=0.0, units="refused entries",
            detail=f"{len(items)} connectivity declaration(s) refused, so the list this "
                   f"gate would check is not trustworthy: {items[0]}"
                   + (f" (+{len(items) - 1} more)" if len(items) > 1 else ""),
            evidence=[_write(ctx, "assembly_connected.json", {"refused_entries": items})])

    if refusals:
        return refused(refusals)

    meshes, welded, skip = _load_all(ctx, "cad.assembly_connected")
    if skip is not None:
        return skip

    names = sorted(meshes)
    if len(names) < 2:
        # The ONLY skip. Connectivity is a statement about parts holding each other,
        # and one part cannot hold itself. Everything else this gate might not know
        # is reported, never skipped.
        return _skipped(
            "cad.assembly_connected",
            f"the assembly has {len(names)} part(s); connectivity is a relation "
            f"between parts and there is nothing to measure below two of them. Export "
            f"every placed solid into the projection's mesh map, not just the one "
            f"being worked on")

    unknown = sorted(p for p in free_standing if p not in meshes)
    if unknown:
        return refused([
            f"{free_key} names {p!r}, which is not in the assembly — a part that is "
            f"not modelled cannot be declared free-standing, and the entry is more "
            f"likely a stale name than a decision" for p in unknown])

    # -- the tolerance ------------------------------------------------------ #
    declared_tol, tol_key = ctx.first_pack_param_named(MATING_TOL_KEYS)
    stated = (isinstance(declared_tol, (int, float)) and not isinstance(declared_tol, bool)
              and math.isfinite(float(declared_tol)) and float(declared_tol) >= 0.0)
    if stated:
        tol = float(declared_tol)
        tol_source = f"{tol_key or MATING_TOL_KEYS[0]}={tol:g} mm"
    else:
        tol = float(CONTACT_FALLBACK_TOL_MM)
        tol_source = (f"{_spellings(ctx, MATING_TOL_KEYS)} is not stated, so contact "
                      f"is measured at this pack's tessellation floor, {tol:g} mm")
        if any(row["tol_mm"] is None for row in contacts):
            return refused([
                f"{len(contacts)} required contact(s) are declared but no mating "
                f"tolerance is: {_spellings(ctx, MATING_TOL_KEYS)} is {declared_tol!r} "
                f"and at least one entry states no tol_mm. How far apart two parts that "
                f"are DECLARED to touch may be before the joint is not a joint is an "
                f"assembly decision the model must carry, and this gate will not invent "
                f"one for a requirement somebody wrote down. (Coverage needs no "
                f"declaration and would have run at {tol:g} mm; delete the declarations "
                f"or state the tolerance.)"])

    raw_budget = ctx.first_pack_param(MATING_SAMPLE_KEYS)
    if raw_budget is None:
        budget = CONTACT_SAMPLES
    else:
        try:
            budget = int(raw_budget)
        except (TypeError, ValueError):
            budget = 0
        if budget <= 0:
            return refused([
                f"'{MATING_SAMPLE_KEYS[0]}' is {raw_budget!r}; it must be a positive "
                f"number of surface samples per part (omit it for the default "
                f"{CONTACT_SAMPLES}) — sampling nothing would measure the vertices "
                f"alone, which is the one way this gate is known to be wrong"])

    solids = {name: _Solid(name, meshes[name]) for name in names}
    measured: dict[tuple[str, str], dict] = {}

    def measure(a: str, b: str, pair_tol: float, label: bool = False) -> dict:
        """One pair, measured once. The DECLARED pairs are measured first, so a pair
        that is both declared and a coverage candidate is judged at the tolerance its
        own declaration states — which is the stricter of the two whenever they
        differ, since a per-entry `tol_mm` exists to tighten a joint rather than to
        loosen it. Conservative either way: a tighter tolerance can only move a pair
        OUT of the contact graph, which is a finding, never a silence."""
        key = _pair_key(a, b)
        if key not in measured:
            measured[key] = _pair_contact(solids[key[0]], solids[key[1]],
                                          pair_tol, budget, label=label)
            measured[key]["tolerance_mm"] = float(pair_tol)
        return measured[key]

    # -- 1. the declared contacts, at the tolerance the project stated ------ #
    rows: list[dict[str, Any]] = []
    for row in contacts:
        a, b = row["pair"]
        pair_tol = row["tol_mm"] if row["tol_mm"] is not None else tol
        record: dict[str, Any] = {
            "a": a, "b": b, "tolerance_mm": pair_tol, "sources": row["sources"],
            "chains": row["chains"], "steps": row["steps"],
            "reason": row["reasons"][0] if row["reasons"] else "",
        }
        missing = [name for name in (a, b) if name not in meshes]
        if missing:
            record.update(gap_mm=None, error_mm=None, closed=False,
                          interpenetrating=False, decidable=False, missing=missing,
                          why=f"{' and '.join(missing)} is not in the assembly at all — "
                              f"a contact whose member was never modelled is the most "
                              f"complete way for a joint to be open")
            rows.append(record)
            continue
        result = measure(a, b, pair_tol, label=True)
        gap = result["gap_mm"]
        record.update(
            gap_mm=round(gap, 4) if isinstance(gap, float) else None,
            error_mm=(None if result["error_mm"] is None
                      else round(result["error_mm"], 4)), missing=[],
            interpenetrating=result["interpenetrating"], decidable=result["decidable"],
            closed=bool(result["touching"]), why=result["why"])
        rows.append(record)

    # -- 2. coverage: every part must be held by something ------------------ #
    #
    # The broad phase is exact as a filter — two parts whose bounding boxes are
    # further apart than the tolerance cannot have surfaces closer than it — so the
    # pairs it drops are dropped on arithmetic, not on a budget. On a 30-body boat it
    # takes 435 pairs down to 61.
    pairs_total = len(names) * (len(names) - 1) // 2
    candidates = []
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            box = _box_gap(solids[a].lo, solids[a].hi, solids[b].lo, solids[b].hi)
            if box <= tol:
                candidates.append((box, a, b))
    candidates.sort()
    for _box, a, b in candidates:
        measure(a, b, tol)

    edges = {key: value for key, value in measured.items() if value["touching"]}
    degree: dict[str, int] = {name: 0 for name in names}
    closest: dict[str, tuple] = {}
    for (a, b), value in edges.items():
        degree[a] += 1
        degree[b] += 1
        gap = value["gap_mm"] if isinstance(value["gap_mm"], float) else 0.0
        for near, far in ((a, b), (b, a)):
            if near not in closest or gap < closest[near][1]:
                closest[near] = (far, gap)

    groups = _components(names, measured)
    held = [name for name in names if degree[name]]
    unheld = [name for name in names if not degree[name] and name not in free_standing]
    detached = [group for group in groups[1:]
                if len(group) > 1 and not all(m in free_standing for m in group)]

    coverage = {
        "parts": len(names), "pairs_total": pairs_total,
        "pairs_within_reach": len(candidates), "pairs_measured": len(measured),
        "contacts_found": len(edges), "parts_held": len(held),
        "components": groups, "unheld": unheld,
        "detached_groups": detached,
        "free_standing": free_standing, "free_standing_key": free_key,
        "contact_tolerance_mm": tol, "tolerance_source": tol_source,
    }

    # -- 3. one headline row, and every number in the verdict comes FROM it -- #
    #
    # `measured`, `limit` and the pair named in `detail` used to be taken from
    # DIFFERENT rows: the worst gap in the assembly, the tolerance of the worst
    # OPEN pair, and the name of a third. On a pass it could print a measurement
    # larger than the limit it was compared against. A verdict that misattributes
    # its own number is the exact thing this project exists to prevent, so the
    # headline is built once, here, and nothing below reads anything else.
    headline: dict[str, Any] = {}

    def head(kind, a, b, value, limit, note):
        return {"kind": kind, "a": a, "b": b, "measured_mm": value,
                "limit_mm": limit, "note": note}

    if unheld:
        scanned = unheld[:_MAX_NAMED]
        for name in scanned:
            others = [solids[o] for o in names if o != name]
            near, gap = _nearest(solids[name], others, budget)
            coverage.setdefault("nearest", {})[name] = {
                "part": near, "gap_mm": round(gap, 4) if math.isfinite(gap) else None}
        worst = max(scanned,
                    key=lambda n: (coverage["nearest"][n]["gap_mm"] is None,
                                   coverage["nearest"][n]["gap_mm"] or 0.0))
        near = coverage["nearest"][worst]
        headline = head("unattached part", worst, near["part"] or "",
                        near["gap_mm"], round(tol, 4),
                        "held by nothing: no other part comes within the contact "
                        "tolerance of it")
    elif detached:
        group = detached[0]
        main = set(groups[0])
        best = ("", "", math.inf)
        for name in group:
            others = [solids[o] for o in sorted(main)]
            near, gap = _nearest(solids[name], others, budget)
            if gap < best[2]:
                best = (name, near, gap)
        coverage["detachment"] = {
            "group": group, "part": best[0], "nearest_in_assembly": best[1],
            "gap_mm": round(best[2], 4) if math.isfinite(best[2]) else None}
        headline = head("detached group", best[0], best[1],
                        round(best[2], 4) if math.isfinite(best[2]) else None,
                        round(tol, 4),
                        f"a group of {len(group)} part(s) joined to each other and to "
                        f"nothing else in the assembly")

    open_rows = [r for r in rows if not r["closed"]]
    # Worst first, and a member that does not exist outranks any measured gap: "the
    # part is not there" is a bigger statement than "the part is 3 mm away".
    open_rows.sort(key=lambda r: (r["gap_mm"] is not None,
                                  -((r["gap_mm"] or 0.0) - (r["tolerance_mm"] or 0.0))))
    closed = len(rows) - len(open_rows)
    sharing = sum(1 for r in rows if r["interpenetrating"])

    if not headline and open_rows:
        first = open_rows[0]
        headline = head("open declared contact", first["a"], first["b"],
                        first["gap_mm"], first["tolerance_mm"],
                        "a declared member is not in the assembly" if first["missing"]
                        else "declared to be in contact and measurably apart")

    # WHERE a chain breaks, not just which pairs are open. A chain reported as
    # "servo -> pushrod closed, pushrod -> rudder OPEN 11.0 mm" is a sentence a
    # reader can act on; the same fact as a set of unordered pairs is a puzzle.
    breaks = []
    for name in sorted(chains):
        steps = sorted(((step["index"], step["step"], row)
                        for row in rows for step in row["steps"]
                        if step["chain"] == name), key=lambda item: item[0])
        for index, arrow, row in steps:
            if row["closed"]:
                continue
            gap = row["gap_mm"]
            breaks.append(f"chain {name!r} breaks at step {index}, {arrow} "
                          + (f"({gap:.1f} mm apart)" if isinstance(gap, float)
                             else "(member missing)"))
            break

    # How much of the assembly was actually looked at. The old pass line read
    # "N of N declared contact(s) closed", whose denominator is the DECLARATION —
    # a statement about the document that reads as a statement about the assembly.
    examined = (f"{len(names)} of {len(names)} parts examined; {pairs_total} part "
                f"pair(s), {len(candidates)} within contact reach, {len(measured)} "
                f"measured in all at {tol:g} mm ({tol_source})")

    failed = bool(unheld or detached or open_rows)
    if not headline:
        # -- a pass. The headline is the most LOOSELY held part in the graph --- #
        if closest:
            loosest = max(names, key=lambda n: closest.get(n, ("", 0.0))[1])
            partner, slack = closest[loosest]
            headline = head("loosest contact", loosest, partner,
                            round(float(slack), 4), round(tol, 4),
                            "the part whose closest contact is the widest of any "
                            "part's")
        else:
            # Reachable only when every part is declared free-standing: nothing
            # touches anything and every absence is accounted for. There is no
            # contact to quote, so the verdict quotes that rather than inventing a
            # distance for a pair that does not exist.
            headline = head("no contacts", "", "", 0.0, 0.0,
                            "every part in the assembly is declared free-standing")

    movers = PARTS.movers(meshes)
    undecided = sorted(f"{a}/{b}" for (a, b), v in measured.items()
                       if not v["touching"] and not v["decidable"])
    caveat = (f"; {len(undecided)} pair(s) UNDECIDED ({', '.join(undecided[:2])}"
              + ("..." if len(undecided) > 2 else "") + ")") if undecided else ""

    evidence = [_write(ctx, "assembly_connected.json", {
        "contacts": rows, "chains": chains, "coverage": coverage,
        "headline": headline, "default_tolerance_mm": tol if stated else None,
        "tolerance_key": tol_key or MATING_TOL_KEYS[0],
        "samples_per_part": budget, "refused_entries": [],
        "pairs": [dict(value, a=a, b=b) for (a, b), value in sorted(measured.items())],
    })]

    if failed:
        locators: list[Locator] = []
        if headline["kind"] == "unattached part":
            for name in unheld[:_MAX_LOCATORS]:
                near = (coverage.get("nearest") or {}).get(name, {})
                gap = near.get("gap_mm")
                label = ("held in place by nothing — nearest part "
                         + (f"{near.get('part')} {_gap_text(gap)} away"
                            if near.get("part") else "unknown")
                         + f", and contact is {tol:g} mm")
                locators.append(_locate(movers, name, label=label, value=gap))
            detail = (f"{len(unheld)} part(s) HELD BY NOTHING — worst "
                      f"{headline['a']} {_gap_text(headline['measured_mm'])} from "
                      f"{headline['b']}, the nearest part in the assembly, vs "
                      f"{tol:g} mm contact"
                      + (f" ({', '.join(unheld[:_MAX_NAMED])}"
                         + (f" +{len(unheld) - _MAX_NAMED} more" if len(unheld) > _MAX_NAMED else "")
                         + ")" if len(unheld) > 1 else "")
                      + (f"; {len(detached)} detached group(s)" if detached else "")
                      + (f"; {len(open_rows)} of {len(rows)} declared contact(s) open"
                         if rows else "; nothing declared")
                      + (f"; {breaks[0]}" if breaks else "")
                      + f"; {examined}" + caveat + _welded_note(welded))
        elif headline["kind"] == "detached group":
            group = coverage["detachment"]["group"]
            for name in group[:_MAX_LOCATORS]:
                locators.append(_locate(
                    movers, name,
                    label=f"in a {len(group)}-part group joined to nothing else in the "
                          f"assembly; nearest is {headline['b']} "
                          f"{_gap_text(headline['measured_mm'])} away"))
            detail = (f"the assembly is in {len(groups)} disconnected piece(s) — "
                      f"{len(group)} part(s) ({', '.join(group[:_MAX_NAMED])}"
                      + (f" +{len(group) - _MAX_NAMED} more" if len(group) > _MAX_NAMED else "")
                      + f") joined to each other and to nothing else; closest approach "
                      f"{headline['a']} to {headline['b']} "
                      f"{_gap_text(headline['measured_mm'])} vs {tol:g} mm contact"
                      + (f"; {len(open_rows)} of {len(rows)} declared contact(s) open"
                         if rows else "; nothing declared")
                      + (f"; {breaks[0]}" if breaks else "")
                      + f"; {examined}" + caveat + _welded_note(welded))
        else:
            first = open_rows[0]
            shown = (f"{first['gap_mm']:.2f} mm apart"
                     if isinstance(first["gap_mm"], float)
                     else (f"{' and '.join(first['missing'])} missing from the assembly"
                           if first["missing"] else "unmeasurable"))
            detail = (f"{len(open_rows)} of {len(rows)} declared contact(s) OPEN — worst "
                      f"{first['a']}/{first['b']} {shown} vs "
                      f"{first['tolerance_mm']:g} mm allowed"
                      + (f"; {breaks[0]}" if breaks else "")
                      + f"; every part is held by something"
                      + f"; {examined}" + caveat + _welded_note(welded))
            # THE PAYOFF, and the reason this gate carries locators at all: a part
            # attached to nothing is invisible in a render and obvious the moment both
            # ends of the joint that is not a joint light up. Two pins per open pair —
            # the floating part and what it is supposed to be attached to — because
            # either one of them may be the one that moved.
            for row in open_rows[:_MAX_LOCATORS // 2]:
                gap = row["gap_mm"]
                for near, far in ((row["a"], row["b"]), (row["b"], row["a"])):
                    if near not in meshes:
                        continue
                    if isinstance(gap, float):
                        label = (f"{gap:.2f} mm from {far}, declared to be in contact "
                                 f"with it (limit {row['tolerance_mm']:g} mm)")
                    elif row["missing"]:
                        label = f"declared to touch {far}, which is not in the assembly"
                    else:
                        label = f"contact with {far} could not be measured: {row['why'][:60]}"
                    locators.append(_locate(movers, near, label=label, value=gap))
        return Verdict(
            gate="cad.assembly_connected", passed=False,
            measured=headline["measured_mm"], limit=headline["limit_mm"],
            units="mm", detail=detail, evidence=evidence, locators=locators)

    # -- 4. a pass ---------------------------------------------------------- #
    declared_clause = ""
    if rows:
        worst_row = max(rows, key=lambda r: (r["gap_mm"] or 0.0) - (r["tolerance_mm"] or 0.0))
        declared_clause = (
            f"; {len(rows)} declared contact(s) all closed ({sharing} interpenetrating "
            f"by design), worst {worst_row['a']}/{worst_row['b']} "
            f"{_gap_text(worst_row['gap_mm'])} vs {worst_row['tolerance_mm']:g} mm allowed")
        if chains:
            declared_clause += ("; " + ", ".join(
                f"chain {n!r} continuous over {len(chains[n]) - 1} step(s)"
                for n in sorted(chains)))
    else:
        declared_clause = ("; nothing is declared, so WHICH parts hold which is "
                           "unproven — coverage says only that none is adrift")
    free_clause = (f"; free-standing by declaration: "
                   f"{', '.join(sorted(free_standing)[:_MAX_NAMED])}"
                   + (f" +{len(free_standing) - _MAX_NAMED} more"
                      if len(free_standing) > _MAX_NAMED else "")
                   if free_standing else "")
    # Never say "one connected assembly" when it is not one. A pass here can still
    # have more than one piece — every extra piece declared free-standing — and a
    # verdict that rounds that off to "connected" is claiming something nobody
    # proved, which is the same defect as the misattributed measurement above.
    shape = ("one connected assembly" if len(groups) == 1 else
             f"{len(groups)} separate piece(s), every one beyond the largest declared "
             f"free-standing")
    lead = (f"every one of {len(names)} part(s) is held" if len(held) == len(names)
            else f"{len(held)} of {len(names)} part(s) held by another part, the "
                 f"remaining {len(names) - len(held)} declared free-standing")
    detail = ((f"{lead}: {shape}, {len(edges)} contact(s), loosest "
               f"{headline['a']}/{headline['b']} at "
               f"{_gap_text(headline['measured_mm'])} vs {tol:g} mm"
               if headline["kind"] == "loosest contact" else
               f"all {len(names)} part(s) are declared free-standing and none touches "
               f"another; there is no contact in this assembly to measure")
              + free_clause + declared_clause + f"; {examined}" + caveat
              + _welded_note(welded))
    return Verdict(
        gate="cad.assembly_connected", passed=True,
        measured=headline["measured_mm"], limit=headline["limit_mm"],
        units="mm", detail=detail, evidence=evidence)
