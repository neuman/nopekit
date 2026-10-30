# SPDX-License-Identifier: Apache-2.0
"""Known-bad geometry for the mesh gates. One defect each, in the gate's direction.

Every fixture returns ``{"meshes": {...}}``, which the selftest machinery merges
into ``ctx.extra``; the gates look there before the projection, so the gate under
test runs on this geometry and the project's own parts are left alone.

The discipline that makes these worth anything: each function starts from a CLEAN
box and changes exactly one physically meaningful thing, in the direction the gate
under test claims to detect. A corrupt file or an empty mesh would prove the gates
handle garbage — which is not the claim any of them makes.

trimesh is imported inside each function, never at module level. Two reasons: the
selftest machinery declines to load a fixture at all when the gate's tooling is
missing, and a module-level import would make this file unreadable on the very
machine where the honest answer is SKIPPED.

Two kinds of fixture live here, and the second kind is newer. Most are known-BAD:
the unittest harness runs them and requires the gate to fail. ``coplanar_touch_pair``
is the other direction — geometry the gate must come out CLEAR on, with no allowlist
entry — because ``cad.clash`` had a false positive (a bulkhead and a deck sharing one
face reported as 31277.200 mm^3) and a gate hardened against a false positive can
lose its true negatives quietly. The harness has no way to require a pass, so
``selftest/check_clash_contact.py`` runs the whole matrix and is the thing to run
after touching that gate.

Where a fixture necessarily trips a second gate as well, its docstring says so.
``sliver_pair`` duplicates one corner of a closed box and repoints a subset of the
faces meeting there, which is what a tessellator does when it emits a rim vertex
twice: the surface stays geometrically closed and goes topologically open, so
``cad.watertight`` fails it as well. That is not a side effect to apologise for, it
is the same defect seen by the other gate — but what the control has to show is that
the gate under test trips for the reason the fixture planted, which for
``cad.degenerate_faces`` is the repair count.
"""
from __future__ import annotations

BOX_MM = 20.0          # a part-sized cube; big enough that mm-scale defects are real
SLIVER_OFFSET_MM = 1e-6    # a duplicated vertex, well inside the 1e-4 mm weld tolerance


import json as _json
import os as _os

_BASELINE_PATH = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "baseline.json")


def _claims() -> list:
    """The claims the baseline states (``_claims``), fresh each call: the ledger
    every fixture hands its gate, never the host's. ``cad.clearance`` reads its
    limit from the claim tagged ``clearance``; in a host with no such claim, or
    one with another limit, a host ledger would crash or move its control. Built
    with ``Claim.from_dict``, as cfd-openfoam's fixtures build theirs (its
    ``bad_cases.py`` says why not ``packs.baseline_claims``)."""
    from nopekit.models import Claim
    try:
        with open(_BASELINE_PATH, "r", encoding="utf-8") as handle:
            rows = _json.load(handle).get("_claims") or ()
    except (OSError, ValueError):
        return []
    return [Claim.from_dict(row) for row in rows]


def _baseline() -> dict:
    """The pack's OWN plausible-good projection — never the host project's.

    A fixture that reads its severity from the host project is a control that
    passes in some repositories and fails in others, which is the same as having
    none. See tests/test_packs.py::ControlsAreSealed.
    """
    try:
        with open(_BASELINE_PATH, "r", encoding="utf-8") as handle:
            loaded = _json.load(handle)
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in loaded.items() if not k.startswith("_")}


def _sealed(ctx, meshes: dict):
    """A context carrying the pack's own projection AND the known-bad geometry.

    Returning a bare ``{"meshes": ...}`` dict merges only into ``ctx.extra`` and
    leaves the gate reading its THRESHOLD from the host project — the same
    defusal hole as an inherited projection, in a different shape. A wall gate
    handed a quarter-thickness plate and no minimum-wall figure does not fail; it
    skips, and a skip is not a control.
    """
    import dataclasses
    from nopekit.models import Ledger
    return dataclasses.replace(ctx, params=_baseline(), ledger=Ledger(claims=_claims()),
                               extra=dict(ctx.extra or {}, meshes=meshes))


def _box(extents, translate=(0.0, 0.0, 0.0)):
    """A clean, watertight, correctly wound box at a placement."""
    import numpy as np
    import trimesh

    transform = np.eye(4)
    transform[:3, 3] = np.asarray(translate, dtype=float)
    return trimesh.creation.box(extents=extents, transform=transform)


def holed_box(ctx):
    """cad.watertight — a box with its +Z facet deleted.

    Two triangles removed and nothing else touched: 4 open edges on an otherwise
    perfect part. This is the shape of the defect that matters, because a mesh with
    a hole booleans to an empty intersection without raising, and a part that
    collides with nothing is the answer everybody was hoping for.
    """
    import numpy as np

    mesh = _box((BOX_MM, BOX_MM, BOX_MM))
    top = np.isclose(np.asarray(mesh.triangles_center)[:, 2], BOX_MM / 2.0)
    mesh.update_faces(~top)
    return _sealed(ctx, {"holed_box": mesh})


def flipped_facet(ctx):
    """cad.is_volume — a closed box with one triangle wound backwards.

    Still watertight: every edge is still used by exactly two faces, so
    ``cad.watertight`` passes this happily. It is not a volume, it cannot be
    booleaned, and its signed volume is wrong by twice that facet's contribution.
    This is precisely the case that watertightness cannot see, which is why the two
    gates are separate.
    """
    mesh = _box((BOX_MM, BOX_MM, BOX_MM))
    faces = mesh.faces.copy()
    faces[0] = faces[0][::-1]
    mesh.faces = faces
    return _sealed(ctx, {"flipped_box": mesh})


def sliver_pair(ctx):
    """cad.degenerate_faces — ONE corner of the box emitted twice by the tessellator.

    This is the defect PACK.md names, planted on the box rather than beside it: a
    real corner of a closed surface is written a second time 1e-6 mm away, a subset
    of the faces that meet there is repointed at the copy, and one face spans the
    two. One physical change, two consequences, both of which this gate is the one
    that sees:

    * **adjacency splits.** The faces on either side of the duplicated corner are
      no longer neighbours even though they touch everywhere, so the surface is
      topologically open where it is geometrically closed — every algorithm that
      walks adjacency (offset, slicing, thickness, boolean) now works across a seam.
      The two copies weld back together at 1e-4 mm: one repair.
    * **a sliver appears only after the weld.** The face spanning the two copies has
      an area of 0.5 * 1e-6 mm * 20 mm = 1e-5 mm^2 — about a thousand times the
      1e-8 mm^2 zero-area threshold, so it survives an area test comfortably. Weld
      the corner and its two ends become one point and its area is exactly zero.
      A drop-then-weld implementation finds nothing here and reports a clean part;
      weld-then-drop finds it. That ordering is what this fixture exists to prove.

    An earlier version of this fixture put the sliver at ``(BOX_MM, 0, 0)`` — 10 mm
    clear of a box spanning [-10, 10], floating in empty space. The gate detected
    it, but a disconnected triangle appended to a file is "added garbage", not the
    adjacency split the pack documents, and it proved nothing about a duplicated rim
    vertex on a closed surface. It is rebuilt on the geometry here so the claim and
    the evidence are the same statement.

    Also trips cad.watertight, and now genuinely for the documented reason: the
    repointed faces carry their edges to the copy, so those edges are used once
    instead of twice. Geometrically closed, topologically open. The measurement that
    matters to THIS gate is the repair count.
    """
    import numpy as np
    import trimesh

    box = _box((BOX_MM, BOX_MM, BOX_MM))
    vertices = np.asarray(box.vertices, dtype=float)
    faces = np.asarray(box.faces).copy()

    half = BOX_MM / 2.0
    corner = int(np.argmin(np.linalg.norm(vertices - [half, half, half], axis=1)))
    # The far end of one box EDGE from that corner: a real vertex 20 mm away, so
    # the spanning face is as long as the part and still 1e-6 mm wide.
    arm = int(np.argmin(np.linalg.norm(vertices - [-half, half, half], axis=1)))

    duplicate = len(vertices)
    vertices = np.vstack([vertices, vertices[corner] + [0.0, 0.0, SLIVER_OFFSET_MM]])

    incident = np.nonzero((faces == corner).any(axis=1))[0]
    for row in incident[: max(1, len(incident) // 2)]:       # a SUBSET: the split
        faces[row][faces[row] == corner] = duplicate
    faces = np.vstack([faces, [[corner, duplicate, arm]]])

    # process=False or trimesh welds the duplicate on construction and hands the
    # gate a repaired mesh — the fixture would then be testing nothing.
    mesh = trimesh.Trimesh(vertices=vertices, faces=faces, process=False)
    return _sealed(ctx, {"split_corner_box": mesh})


def overlapping_pair(ctx):
    """cad.clash — two valid solids sharing 800 mm^3 of material.

    Both boxes are watertight, consistently wound, degenerate-free volumes. The only
    thing wrong is where the second one is: 18 mm along X from a 20 mm box, so they
    share a 2 x 20 x 20 mm slab. That is three orders of magnitude past any
    tessellation tolerance this gate carries, and no allowlist entry covers it.
    """
    a = _box((BOX_MM, BOX_MM, BOX_MM))
    b = _box((BOX_MM, BOX_MM, BOX_MM), translate=(BOX_MM - 2.0, 0.0, 0.0))
    return _sealed(ctx, {"block_a": a, "block_b": b})


def coplanar_touch_pair(ctx):
    """cad.clash — the POSITIVE control: two solids that touch and must PASS.

    Two clean 20 mm boxes placed exactly 20 mm apart along X: one face is shared,
    to the last bit, and no material is. This is the opposite of
    :func:`overlapping_pair` and it is a control in the same sense — it proves the
    gate can still say "clear", which a gate that has been hardened against false
    negatives can quietly lose.

    It exists because of a real false positive. A bulkhead and a deck panel sharing
    one face exactly were reported as ``worst reported 31277.200 mm^3 ... inf mm
    equivalent depth over 0.00 mm^2``: a boolean kernel asked about a coplanar
    contact returns noise, and the gate printed the noise as a headline. The verdict
    line said so — an infinite depth over zero area is a kernel shrugging — but 31
    cm^3 reads as real interference, and somebody went looking for geometry that was
    not there.

    Run by ``selftest/check_clash_contact.py``, which asserts PASS. The unittest
    harness only knows how to require a FAILURE, so a fixture the gate must pass
    cannot be the declared negative control — but it is the same kind of evidence
    and it lives with the others.
    """
    a = _box((BOX_MM, BOX_MM, BOX_MM))
    b = _box((BOX_MM, BOX_MM, BOX_MM), translate=(BOX_MM, 0.0, 0.0))
    return _sealed(ctx, {"block_a": a, "block_b": b})


def bonded_over_interference(ctx):
    """cad.clash — a REAL interference declared as a bonded joint. Must still FAIL.

    The same 2 mm overlap as :func:`overlapping_pair`, with a properly formed
    ``bonded_joints`` entry over it: a named pair and a plausible reason, nothing a
    reviewer would blink at. The declaration waives the VOLUME tolerance, so the
    800 mm^3 no longer counts against the pair — and the gate must fail it anyway,
    on the 2 mm of equivalent depth against the 0.05 mm a bond line allows.

    This is the fixture that decides whether ``bonded_joints`` is an honest middle
    or a second allowlist. If the gate passes this, the declaration has become a
    way to switch the check off for a pair, which is what ``clash_allow`` already
    is and is refused from being spelled with a wildcard.
    """
    a = _box((BOX_MM, BOX_MM, BOX_MM))
    b = _box((BOX_MM, BOX_MM, BOX_MM), translate=(BOX_MM - 2.0, 0.0, 0.0))
    ctx = _sealed(ctx, {"block_a": a, "block_b": b})
    ctx.params["bonded_joints"] = [
        {"pair": ["block_a", "block_b"],
         "reason": "the two blocks are epoxy-bonded across their mating face"},
    ]
    return ctx


def bonded_wildcard(ctx):
    """cad.clash — a wildcard in ``bonded_joints``. Must FAIL on the declaration.

    Geometry identical to the pack's own baseline, so nothing here interferes: the
    only bad thing is the entry. ``"block_a" vs "*"`` waives the volume check for
    every pair that part is ever in, which is the mechanism by which a real
    interference gets hidden — one line, and the report stays green as the design
    moves underneath it. The gate must refuse the entry and fail, rather than
    honour it or drop it quietly.

    The gate fails on the DOCUMENT here, not on geometry, which is why this fixture
    does not need a clashing pair to be known-bad.
    """
    a = _box((BOX_MM, BOX_MM, BOX_MM))
    b = _box((BOX_MM, BOX_MM, BOX_MM), translate=(BOX_MM, 0.0, 0.0))
    ctx = _sealed(ctx, {"block_a": a, "block_b": b})
    ctx.params["bonded_joints"] = [{"pair": ["block_a", "*"], "reason": "bonded assembly"}]
    return ctx


def bonded_sliding_fit(ctx):
    """cad.clash — a sliding pair declared bonded. Must FAIL on the declaration.

    A bond has zero degrees of freedom; a sliding fit has one. The two statements
    cannot both be true of one pair, and the gate refuses the entry for the same
    reason the allowlist refuses it: at the one pose this gate sees, "they touch"
    and "they jam" are the same picture, so the pair that must move is the pair
    that most needs the check — and waiving its volume tolerance is exactly the
    wrong move.

    The baseline already declares ``carriage``/``housing`` as a sliding fit, so the
    fixture changes one thing: it declares that same pair bonded.
    """
    # An EMPTY mesh override, so the gate falls through to the baseline's own four
    # solids: `mesh_sources` skips an empty map in `extra`. The geometry is the
    # known-good assembly and only the declaration is bad, which is the whole point.
    ctx = _sealed(ctx, {})
    ctx.params["bonded_joints"] = [
        {"pair": ["carriage", "housing"],
         "reason": "the carriage is bonded into the housing cavity"},
    ]
    return ctx


def thin_plate(ctx):
    """cad.wall_thickness — a plate at a quarter of the project's own minimum wall.

    A closed, valid, correctly wound solid whose only fault is being too thin, taken
    from the limit in the projection so the fixture tracks whatever process the
    project is actually using. Thickness enters deflection as a cube and wall
    failures as a threshold; a quarter is unambiguous without being a caricature.
    """
    base = _baseline()
    limit = base.get("process_min_wall_mm", base.get("min_wall_mm"))
    limit = float(limit) if isinstance(limit, (int, float)) and limit > 0 else 1.2
    return _sealed(ctx, {"thin_plate": _box((30.0, 30.0, limit / 4.0))})


# --------------------------------------------------------------------------- #
# cad.assembly_connected — the gate that asserts an ABSENCE, of a gap
# --------------------------------------------------------------------------- #
# These fixtures are shaped differently from the ones above, and the difference is
# the point. Every other fixture here plants a defect IN a part: a hole, a reversed
# facet, a duplicated vertex, a plate that is too thin. A disconnected part has none
# of those — its mesh is perfect, it is inside the envelope, it interferes with
# nothing — so the only way to build known-bad input for a connectivity gate is to
# MOVE a good part away from the good part it is supposed to be touching.


def _baseline_mesh(name: str):
    """One of the pack's own baseline solids, loaded and welded like the gate does.

    STL is a soup format with no vertex identity, so a mesh loaded from one is not a
    volume until somebody welds it — and a part that is not a volume cannot be asked
    what is inside it, which is half of how this gate decides a pair is connected.
    The gate welds on load (``_load_one``); a fixture that hands it an unwelded
    in-memory mesh would be quietly testing a different code path.
    """
    import trimesh

    path = _os.path.join(_os.path.dirname(_BASELINE_PATH), "meshes", f"{name}.stl")
    mesh = trimesh.load_mesh(path, process=False)
    try:
        mesh.merge_vertices(digits_vertex=4)           # 1e-4 mm, the pack's weld
    except TypeError:                                  # older trimesh signature
        mesh.merge_vertices()
    return mesh


def _baseline_assembly(**moved):
    """The baseline's four solids, with named parts replaced by moved copies.

    ``_baseline_assembly(carriage=(0, 0, 3.0))`` lifts the carriage 3 mm and leaves
    everything else exactly as the known-good projection has it.
    """
    parts = {}
    for name in ("housing", "cover", "carriage", "guide_roller"):
        mesh = _baseline_mesh(name)
        if name in moved:
            mesh.apply_translation(list(moved[name]))
        parts[name] = mesh
    return parts


def broken_chain(ctx):
    """cad.assembly_connected — the cover lifted 3 mm off the rim it seats on.

    ONE physically meaningful change, in the direction the gate measures: the
    baseline declares the chain ``cover -> housing -> carriage``, and the cover now
    floats 3 mm above the housing rim that is supposed to carry it — fifteen times
    the 0.2 mm mating tolerance the projection states. A lid that is not on the box.

    What makes this the right control is everything that does NOT change. The cover
    is still a closed, consistently wound, correctly sized solid; the assembly's
    stated envelope, volume and centre of mass are untouched; nothing interferes
    with anything — `cad.clash` is *happier* than before, because the part now
    touches nothing at all. Every other gate in this pack passes this fixture. That
    is exactly the hole this gate was written for: on a real boat the same
    arithmetic gave 34 of 37 gates green on an assembly whose steering was not
    connected.

    3 mm rather than 0.3 mm because a part that has come off its seat is a binary
    mistake, not a marginal one, and a control sitting just past the threshold tests
    the threshold rather than the gate.

    **Why the COVER and not the carriage**, which was the first attempt: lift the
    carriage 3 mm off the cavity floor and this gate reports the pair 0.40 mm apart,
    not 3 mm, because the carriage is still 0.40 mm from the cavity side walls that
    guide it and the measurement is the least distance between the two SOLIDS. It
    fires — 0.40 mm is past the 0.2 mm tolerance — but it fires on a number that has
    nothing to do with the 3 mm lift, and a control whose severity is not the
    severity it planted is a control that can pass for the wrong reason after any
    innocent change to the clearance. The limit that showed up there is real and is
    written down in PACK.md: this gate proves a declared pair is not adrift, not
    that it is mating on the faces you meant.
    """
    return _sealed(ctx, _baseline_assembly(cover=(0.0, 0.0, 3.0)))


def carriage_under_cover(ctx):
    """cad.clearance — the carriage made taller, its top 0.01 mm under the cover.

    One edit: the carriage's height grows from 8 mm to 30.99 mm with its bottom
    kept on the cavity floor, so every contact it had is kept and its top face
    stands 0.01 mm below the cover's underside. The two share no material, so
    ``cad.clash`` passes this assembly; the baseline declares the pair in
    ``clearance_pairs`` against the claim's 1.0 mm, and 0.01 mm is far inside it.
    Measured with manifold3d: a flat 0.01 mm gap reads 0.0100002 mm.
    """
    parts = _baseline_assembly()
    car = parts["carriage"]
    floor = float(car.bounds[0][2])
    cover = float(parts["cover"].bounds[0][2])
    verts = car.vertices.copy()
    verts[:, 2] = floor + (verts[:, 2] - floor) * ((cover - 0.01 - floor)
                                                    / float(car.bounds[1][2] - floor))
    car.vertices = verts
    return _sealed(ctx, parts)


def lifted_stop(ctx):
    """cad.assembly_connected — the guide roller lifted 2 mm off the cavity floor.

    The other declaration form: this pair comes from ``mating_pairs`` rather than
    from a chain, so it exercises the same measurement through the other door. The
    roller is the carriage's end stop and it is located by the floor it sits on; a
    stop floating 2 mm above that floor stops the carriage 2 mm late, or not at all
    if it is free to rotate out of the way.

    Run by ``selftest/check_connectivity.py``. A gate can only declare one negative
    control and the chain form is the more informative of the two, so this one lives
    in the matrix.
    """
    return _sealed(ctx, _baseline_assembly(guide_roller=(0.0, 0.0, 2.0)))


def orphaned_part(ctx):
    """cad.assembly_connected — a part moved off everything, with NOTHING declared.

    **The control for coverage, and the one that answers the question the first
    version of this gate could not.** The guide roller is lifted 12 mm off the cavity
    floor it is located by, into clear air inside the housing: it touches no wall, no
    carriage and no cover, and the projection's `clash_allow`, `mating_pairs` and
    `assembly_chains` are all emptied, so not one word says this pair — or any pair —
    was ever meant to touch.

    The old gate SKIPPED on exactly this input, and that is not a hypothetical about
    a fixture: on the real 30-body assembly this pack was hardened against, the
    projection declared no required contacts and the gate reported "the projection
    declares no required contacts" while a rudder hung 42.9 mm below its bracket.
    Catching that depended on somebody having declared the pair that broke, and a
    project that knew to declare it would probably not have broken it.

    So the gate must FAIL here on the geometry alone and name the roller. Everything
    else about the fixture is the baseline: every other part is where it was, every
    other gate in this pack still passes, and `cad.clash` is happier than before
    because the roller now interferes with even less.

    12 mm rather than 2 mm — which `lifted_stop` already covers, with declarations —
    because this control is about the absence of a contact, not about the threshold:
    at 12 mm the roller is unambiguously in free space and the finding cannot be an
    argument about tessellation. It stays inside the housing's bounding box on
    purpose, so the containment branch is exercised too: a part in the cavity is
    inside the box and not inside the material.
    """
    ctx = _sealed(ctx, _baseline_assembly(guide_roller=(0.0, 0.0, 12.0)))
    ctx.params["clash_allow"] = []
    ctx.params["mating_pairs"] = []
    ctx.params["assembly_chains"] = {}
    return ctx


def free_standing_silencer(ctx):
    """cad.assembly_connected — a free-standing declaration with no reason. Must FAIL.

    The counterpart of `mating_no_reason`, on the other list. `free_standing_parts`
    is how a project says "this one is meant to be attached to nothing" — a loose
    tool, a part shown for context — and it is the only thing that can switch the
    coverage finding off, so an entry with no stated reason is a silencer rather than
    a declaration. The geometry is `orphaned_part`'s, so the gate has a real finding
    to be silenced; only the entry is wrong.
    """
    ctx = orphaned_part(ctx)
    ctx.params["free_standing_parts"] = [{"part": "guide_roller", "reason": ""}]
    return ctx


def missing_member(ctx):
    """cad.assembly_connected — a declared contact whose member was never modelled.

    The completest way for a joint to be open, and the one that actually happened:
    the boat's steering declared ``pushrod -> rudder``, and for a whole revision
    there was no rudder stock and no tiller arm in the assembly at all. Nothing
    interfered, nothing was thin, nothing was open — the parts were not there.

    Here the guide roller is simply absent from the mesh map while the projection
    still declares that it rests on the housing floor. The gate must FAIL and name
    the missing part, never skip: a contact that cannot be measured because one end
    of it does not exist has not been proven, and a skip would read as 'no evidence'
    when the evidence is conclusive.
    """
    parts = _baseline_assembly()
    parts.pop("guide_roller")
    return _sealed(ctx, parts)


def mating_wildcard(ctx):
    """cad.assembly_connected — a wildcard required contact. Must FAIL on the entry.

    Geometry identical to the baseline, so every declared contact is closed: the only
    bad thing is the declaration. ``"carriage" vs "*"`` says the carriage must touch
    *something*, which is a claim that cannot be false — a required contact that
    cannot fail is not a requirement, and it is a permanent green tick on the one
    gate that exists to notice an absence.
    """
    ctx = _sealed(ctx, _baseline_assembly())
    ctx.params["mating_pairs"] = [
        {"pair": ["carriage", "*"], "reason": "the carriage is located by the housing"},
    ]
    return ctx


def mating_no_reason(ctx):
    """cad.assembly_connected — a required contact with no reason. Must FAIL.

    The same refusal ``clash_allow`` makes, for the same reason one step further on:
    an unexplained permission to share material is one nobody dares delete, and an
    unexplained requirement to touch is one nobody can check, defend or remove when
    the design moves. Geometry is the baseline's, so only the entry is wrong.
    """
    ctx = _sealed(ctx, _baseline_assembly())
    ctx.params["mating_pairs"] = [{"pair": ["carriage", "housing"], "reason": "   "}]
    return ctx


def mating_unknown_role(ctx):
    """cad.assembly_connected — a misspelled role on a clash_allow entry. Must FAIL.

    ``"role": "requird"`` on the cover/housing entry. This one matters more than it
    looks: if an unrecognised role were treated as "not a required contact", the typo
    would silently remove the pair from this gate's list and the report would stay
    green — which is this gate's own failure mode, an absence nobody notices,
    reappearing inside its own declaration. So the gate refuses the entry instead.
    """
    ctx = _sealed(ctx, _baseline_assembly())
    ctx.params["clash_allow"] = [
        {"pair": ["cover", "housing"], "role": "requird",
         "reason": "the cover is bolted flat onto the housing rim"},
    ]
    return ctx


def shaft_in_bore(ctx):
    """cad.assembly_connected — the POSITIVE control, and the more valuable one.

    A 6 mm shaft running through a bearing boss whose bore is not modelled: two
    solids that share material by design, which is how a boss and the shaft in it
    are drawn when the bore is a hole you drill rather than a feature you model. The
    gate must report this pair CONNECTED, with a gap of exactly 0.

    **This fixture falsifies the wrong implementation rather than the wrong design,
    which is why it is worth more than the known-bad ones.** Measure this pair at its
    VERTICES and it reads as 11.14 mm apart (nearest vertex to the other surface) or
    36.73 mm (nearest vertex pair): a tessellated cylinder has vertices only at its
    two end rings, 40 mm clear of the boss at either end, and the boss's own vertices
    are its eight corners, out at the block's edges. Nothing either solid stores as a
    point is anywhere near the place where they actually meet. A gate written that
    way would have reported the one correctly assembled joint on the boat as broken
    and passed the two that were not connected at all — exactly inverted, and
    confident. ``selftest/check_connectivity.py`` measures all three numbers and
    prints them side by side.

    Also: interpenetration is CONTACT here. This is the opposite question from
    `cad.clash`, asked about a different set of pairs, and the pair is declared in
    `clash_allow` as well — one list, read from both ends.
    """
    import numpy as np
    import trimesh

    boss = _box((20.0, 10.0, 20.0))
    axis_to_y = trimesh.transformations.rotation_matrix(np.pi / 2.0, (1.0, 0.0, 0.0))
    shaft = trimesh.creation.cylinder(radius=3.0, height=80.0, sections=32,
                                      transform=axis_to_y)
    ctx = _sealed(ctx, {"bearing_boss": boss, "shaft": shaft})
    ctx.params["clash_allow"] = [
        {"pair": ["bearing_boss", "shaft"], "role": "required",
         "reason": "the shaft turns in the boss's bore. The bore is drilled, not "
                   "modelled, so the only way to say the shaft runs in it is to let "
                   "the two interpenetrate"},
    ]
    ctx.params["mating_pairs"] = []
    ctx.params["assembly_chains"] = {}
    return ctx
