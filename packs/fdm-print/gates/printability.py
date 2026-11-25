# SPDX-License-Identifier: Apache-2.0
"""Tier-0 printability gates: arithmetic only, no dependencies, sub-second.

These five run on every edit. They read their numbers from ``ctx.params`` — the
model's projection — and never re-derive geometry, because a gate that recomputes
a derived value is checking its own arithmetic instead of the model's (rule 2).

Two kinds of number appear here and the difference matters:

* **Project parameters** — bed size, nozzle diameter, the thinnest section of the
  part, the load direction. These come from the model. If one is missing the gate
  SKIPS and names the key. It never substitutes a default for a quantity only the
  project can know (rule 7).
* **Domain constants** — the brim allowance, the perimeter count that makes a wall
  reliable, the layer-normal strength knockdown. These are properties of filament
  printing, not of any one project. They carry their provenance inline, they are
  overridable from ``ctx.params``, and the value used is printed in the verdict.

Units: millimetres, degrees, grams, seconds. Z is the build direction unless the
model says otherwise via ``build_axis``. Every length in and out is mm.
"""
from __future__ import annotations

import math
import os as _os
from typing import Any, Iterable, Sequence

from nopekit.gates import gate, GateContext, SCOPE_SEP
from nopekit.modelio import load_path as _load_path
from nopekit.models import Locator, NegativeControl, Tier, Verdict

# Shared helpers, loaded BY PATH through the spine's loader rather than imported
# by name: ``packs.load_gates`` puts the pack directory on ``sys.path`` only
# while gate modules load, so a by-name import works at load time and not at
# fixture time, and ``selftest/bad_params.py`` has to load the same helper — the
# gate and its control must read ONE copy of the arithmetic, not two that can
# drift (rule 2). ``load_path`` names each module after its absolute path: the
# gate and the fixture asking for one file get one module, and a second copy of
# this pack in the same process gets its own. What slipped through when the
# name was fixed (``nopekit_pack_fdm_print__process_model``) and whatever
# ``sys.modules`` held under it was served: a twin of this pack computed its
# print time with the FIRST copy's model, and never ran an edit to its own
# (S-26, packs:H4).
_HERE = _os.path.dirname(_os.path.abspath(__file__))

_pm = _load_path(_os.path.join(_HERE, "_process_model.py"))

# What the part is called once ``views/part.py`` has drawn it. Loaded the same way
# and for the same reason as the arithmetic above: the node name a locator carries
# is an interface, and an interface in two copies drifts. ``gates/mesh.py`` loads
# the same file and gets this same module.
_PARTS = _load_path(_os.path.join(_os.path.dirname(_HERE), "fdm_print_parts.py"))

# One verdict over a set of parts, the same module ``gates/mesh.py`` holds.
_FOLD = _load_path(_os.path.join(_HERE, "fdm_print_fold.py"))

# --------------------------------------------------------------------------- #
# domain constants — properties of the process, not of any project
# --------------------------------------------------------------------------- #

BRIM_MM_DEFAULT = 5.0
"""Skirt/brim allowance per side, in mm.

The brim is the thing people forget, and the bill arrives at the end of a long
print: a part sized to the raw bed collides with a clip, a wiper or the gantry's
soft limit once the brim is around it. 5 mm is a 6-loop brim at a 0.4 mm nozzle
with default line spacing, which is the setting most people reach for the moment
a part lifts a corner. Rejected: 0 mm (a bed-sized part is not a printable part)
and 10 mm (that is a raft allowance; charging every part for it shrinks the usable
bed by 4% of area for no reason)."""

PERIMETERS_MIN = 3
"""Perimeters a load-bearing wall needs.

Two perimeters is achievable and unreliable. The two beads meet along a single
interface with no third bead to tie them, so the bond is intermittent: it survives
the bed and fails in service, which is the worst place to find out. Three gives
the section an interior bead that both faces bond to."""

NOZZLE_MIN_WALL_MM: dict[float, float] = {
    0.25: 0.75,
    0.40: 1.20,
    0.60: 1.80,
    0.80: 2.40,
    1.00: 3.00,
    1.20: 3.60,
}
"""Minimum reliable wall by nozzle diameter, mm.

``PERIMETERS_MIN`` beads at the nozzle's default extrusion width (width == nozzle
diameter, which is what every slicer ships). A section thinner than this either
loses a perimeter or gets thinned by the slicer's gap fill, and gap fill is not a
structural feature. Nozzles outside the table fall back to
``PERIMETERS_MIN * extrusion width``.

Every row is exactly ``PERIMETERS_MIN * nozzle`` and the table exists only so the
common nozzles read at a glance. The 0.25 row was 0.80 mm for a while — a 6.7%
fudge with no derivation behind it, which is exactly the kind of number rule 3
exists to stop. It is 0.75 now. A project whose 0.25 profile really does ship a
wider default line states ``extrusion_width_mm`` and the gate uses that instead,
which is the honest way to carry a tuned profile."""

ADVERSE_LAYER_ANGLE_DEG = 60.0
"""Angle between the primary load and the layer plane past which the orientation
is wrong regardless of margin.

At 60 degrees, three quarters of the load is being carried across layer bonds. No
amount of structural margin computed from bulk coupon properties describes that
part; the honest response is to reorient it, not to thicken it."""

LAYER_NORMAL_STRENGTH_RATIO = 0.50
"""Layer-normal tensile strength as a fraction of in-plane strength.

Roughly half, across the common filaments, for ordinary print settings. It is a
band, not a constant — 0.35 for a cold, fast, badly cooled PLA part, 0.7 for a
well-tuned PETG part in a heated chamber — and 0.5 is the middle of it. Use the
gate to catch orientations that are wrong, not to certify ones that are marginal."""

LAYER_NORMAL_MODULUS_RATIO = 0.85
"""Layer-normal elastic modulus as a fraction of in-plane modulus.

Stiffness is far less anisotropic than strength: the bead material is the same
polymer in both directions and a weld that is half as *strong* is still nearly as
*stiff* until it starts to open. Measured ratios for common filaments sit around
0.8-0.9. This is the number ``fdm.layer_alignment`` derates a **deflection**
utilisation by. Using ``LAYER_NORMAL_STRENGTH_RATIO`` (0.50) on a deflection
margin over-derates it by about 1.7x and condemns orientations that are fine, so
the gate refuses to guess which quantity it was handed — see
``utilisation_kind``."""

# The bead cross-section, duty factor, startup overhead and shell fraction live in
# gates/_process_model.py, because the negative control has to solve the same model
# to size itself and two copies of that arithmetic would drift. Re-exported here so
# the names stay importable from the gate module that uses them.
SHELL_FRACTION_DEFAULT = _pm.SHELL_FRACTION_DEFAULT
DUTY_FACTOR = _pm.DUTY_FACTOR
STARTUP_OVERHEAD_S = _pm.STARTUP_OVERHEAD_S

MAX_PRINT_TIME_H_DEFAULT = 24.0
"""Default ceiling on a single uninterrupted print, hours.

Past about a day the risk is no longer the geometry: a 1 kg spool starts running
out, a clog or a layer shift has had a full day of opportunity, and a power blip
costs the whole part. Projects that own a printer farm or a filament runout sensor
should override ``max_print_time_h`` upward and say why."""

FILAMENT_DENSITY_G_CM3_DEFAULT = 1.24
"""PLA. Projects printing anything else should project
``filament_density_g_cm3`` — PETG is 1.27, ABS/ASA 1.04-1.07, TPU 1.21, PA-CF
1.10. See ``sourcing.md``."""

MAX_FILAMENT_G_DEFAULT = 1000.0
"""Filament one uninterrupted print may consume, grams.

A 1 kg spool is the unit filament is sold in, and it is a hard boundary rather
than a preference: a part that needs more than one spool needs a mid-print spool
change, which means either a runout sensor that parks and resumes cleanly or a
splice — and a splice at the wrong layer is a failure plane through the part.
Finding that out at hour 30 is the expensive way.

The figure is gross spool mass, so it is already optimistic: a nearly-new spool
is short of 1000 g by whatever the purge tower and the previous print took.
Rejected: 750 g (some brands sell it, but sizing every project's ceiling to the
smallest spool on the market fails parts that will print fine on what is loaded)
and 2000 g (real, and rare enough that inheriting it as a default would let a
two-spool part through everywhere). Projects that own 2 kg spools or a runout
sensor override ``max_filament_g`` and say which."""


_MISSING = object()


# --------------------------------------------------------------------------- #
# parameter access
# --------------------------------------------------------------------------- #
# The key families this pack reads, primary spelling FIRST.
#
# Every one of these describes ONE PRINTED PART in its print orientation. That is
# the distinction the pack ecosystem lost: `cad-solid` reads `bbox_mm` for the
# ASSEMBLY, this pack read it for the part, and a project with both — every
# mechanical project — could only satisfy one of them. It measured a 480 mm boat
# against a 220 mm bed and called it a failure, which is true of the assembly and
# useless about any part of it.
#
# So each family now leads with a key that says WHICH OBJECT it is
# (`part_bbox_mm`), keeps the bare spelling as a documented fallback, and is
# resolved pack-scoped first (`fdm.part_bbox_mm`) by GateContext.param. Resolution
# order, in full: every scoped spelling in this order, then every bare spelling in
# this order. Written down here, in PACK.md, and in docs/PACK_FORMAT.md — the old
# order (`bbox_mm` before `bbox`) was discoverable only by experiment, and a user
# who guessed wrong got a confident verdict about the wrong object.
BBOX_KEYS = ("part_bbox_mm", "bbox_mm", "bbox", "footprint_mm")
VOLUME_KEYS = ("part_volume_mm3", "volume_mm3", "solid_volume_mm3")
#: The thinnest wall ACROSS THE LAYER PLANE: a section measured perpendicular to
#: the build axis, where the slicer lays perimeters side by side. What slipped
#: through when PACK.md called it "the thinnest section anywhere in the part": a
#: horizontal web 0.395 mm thick, printed as about three layers of 0.12 mm, was
#: stated here, and the guard's bead rule failed it as "another unit or cannot be
#: printed", so every gate behind the guard read Skipped on a sound part. A
#: section along the build axis is a count of layers, not of beads, and it has its
#: own key below.
MIN_WALL_KEYS = ("part_min_wall_mm", "min_wall_mm", "thinnest_wall_mm",
                 "min_feature_mm", "min_section_mm")
#: The thinnest section ALONG THE BUILD AXIS (a floor, a roof, a horizontal web),
#: mm. Optional. The guard holds it to one layer, as it holds a wall to one bead.
#: *Rejected:* folding it into ``part_min_wall_mm`` with a rule per direction (one
#: key cannot say which direction its number was measured in), and holding it to
#: a count of solid layers (that is a strength rule nobody here has a source for;
#: one layer is the floor below which the section cannot be printed at all).
MIN_FLOOR_KEYS = ("part_min_floor_mm", "min_floor_mm", "thinnest_floor_mm")
#: ``{part: mm}`` maps for a print set: the same quantities, one per part. The
#: names are the part set's (``fdm_print_parts.part_set``). A map wins over its
#: single number, as a set wins over a single ``mesh_path``: it is the more
#: complete statement, and the verdict names the key it read.
MIN_WALL_BY_PART_KEYS = ("part_min_wall_by_part_mm", "min_wall_by_part_mm")
MIN_FLOOR_BY_PART_KEYS = ("part_min_floor_by_part_mm", "min_floor_by_part_mm")
VOLUME_BY_PART_KEYS = ("part_volume_by_part_mm3", "volume_by_part_mm3")
AREA_BY_PART_KEYS = ("surface_area_by_part_mm2", "area_by_part_mm2")
LOAD_AXIS_KEYS = ("load_axis", "primary_load_axis", "load_direction")
BUILD_AXIS_KEYS = _PARTS.BUILD_AXIS_KEYS
NOZZLE_KEYS = ("nozzle_d_mm", "nozzle_diameter_mm", "nozzle_d")
LAYER_KEYS = ("layer_height_mm", "layer_h_mm", "layer_height")


def _look(ctx: GateContext, names: Sequence[str], default: Any = _MISSING) -> Any:
    """First of ``names`` present in the projection, else ``default``.

    Delegates to :meth:`GateContext.first_pack_param`, so the resolution order is
    the spine's and is documented once: **every pack-scoped spelling
    (``fdm.<name>``) in the order given, then every bare spelling in the order
    given.** A scoped key always wins, because it is the only one of the two that
    is an explicit statement about this pack.

    Several bare spellings are accepted because a model written for one pack
    should not have to be renamed for another: ``nozzle_d_mm`` and ``nozzle_d``
    are the same physical quantity and refusing the second helps nobody. What is
    NOT accepted is inventing the value — see :func:`_skip`.
    """
    return ctx.first_pack_param(names, default)


def _spellings(ctx: GateContext, names: Sequence[str], limit: int = 3) -> str:
    """The spellings this gate would have accepted, best first, for a skip line.

    A skip that names only the bare key teaches the wrong lesson on a project
    that has two packs fighting over it. Naming the scoped spelling first is how
    a reader learns the namespace exists at the moment they need it.
    """
    scope = (ctx.key_scope or "").strip()
    best = list(names)[:limit]
    out = [f"{scope}{SCOPE_SEP}{best[0]}"] if (scope and best) else []
    return " / ".join(out + best)


def _skip(gate_id: str, names: Sequence[str], what: str,
          ctx: GateContext | None = None) -> Verdict:
    """A SKIPPED verdict that names the key the model did not provide.

    The claim resolves BLOCKED and stays visible. The alternative — substituting a
    plausible number — produces a green verdict about a part nobody described,
    which is the precise way a readiness report starts lying (rule 4).

    ``ctx`` is optional so an older call site still reads correctly; with it the
    message also carries the pack-scoped spelling, which is the one to use on a
    project where another pack wants the same bare key.
    """
    spelled = _spellings(ctx, names) if ctx is not None else " / ".join(names)
    return Verdict(
        gate=gate_id,
        passed=False,
        skipped=True,
        skip_reason=f"the projection has no {spelled} ({what}); "
                    f"add it to the model and re-run",
    )


def _set_note(ctx: GateContext, what: str, map_key: str) -> str:
    """What a single-number verdict adds when the projection also states a set.

    ``fdm.bed_fit`` has said this since the set landed. The single-number gates
    did not: with thirteen meshes in ``mesh_paths`` and one ``part_min_wall_mm``,
    ``fdm.min_wall`` judged one part and its verdict named none, which is the
    complaint the part set was written to end (packs:holdout). So the part is
    named when the model names it (``part_name``), and a stated set that this
    number is not about is said. Only ``part_name`` names the part here: ``part``
    and ``name`` are words a project may use for something else.
    """
    note = ""
    name = _look(ctx, ("part_name",), None)
    if isinstance(name, str) and name.strip():
        note = f"; part {_PARTS.sanitise(name)}"
    parts = _PARTS.part_set(ctx)
    if parts and not parts.problem:
        note += (f"; the {len(parts)} part(s) in `{parts.source}` state no {what} of "
                 f"their own ({map_key}), so this verdict is about the one number "
                 f"above and not about them")
    return note


def _per_part_problem(gate_id: str, problem: str) -> Verdict:
    """A per-part map that cannot be read SKIPS, naming the key.

    The pack's rule for a set key stated in a shape it cannot read: never a quiet
    fall-back to the single number, which would answer a question about one part
    that was asked about several."""
    return Verdict(gate=gate_id, passed=False, skipped=True,
                   skip_reason=f"{problem}; nothing was judged — fix the key or remove it")


def _set_names(ctx: GateContext, gate_id: str, values: "Any"):
    """``(names, source, None)`` for a per-part map, or ``(None, None, verdict)``.

    With a part set stated, the map's names must be the set's: a name the set does
    not hold is a misspelt part, and the set's part with no number is reported as
    not measured rather than passed. With no set, the map names the parts.
    """
    parts = _PARTS.part_set(ctx)
    if parts.problem:
        return None, None, Verdict(gate=gate_id, passed=False, skipped=True,
                                   skip_reason=parts.problem)
    if not parts:
        return list(values.values), values.source, None
    names = [p.name for p in parts]
    unknown = sorted(set(values.values) - set(names))
    if unknown:
        return None, None, _per_part_problem(
            gate_id, f"{values.source} names {', '.join(map(repr, unknown[:4]))}, which "
                     f"is not a part in `{parts.source}` ({', '.join(names[:6])}"
                     f"{'...' if len(names) > 6 else ''})")
    return names, f"{parts.source} + {values.source}", None


def _look_named(ctx: GateContext, names: Sequence[str]) -> tuple[Any, str]:
    """Like :func:`_look`, but also returns WHICH spelling supplied the value.

    ``fdm.layer_alignment`` needs it: ``deflection_utilisation`` and
    ``stress_utilisation`` are not the same physical quantity and must not get
    the same knockdown, so the key name is evidence, not decoration. The name
    that comes back is the FULL spelling that matched, scope included, so a
    verdict can say `fdm.stress_utilisation` and be audited against the model.
    """
    return ctx.first_pack_param_named(names, _MISSING)


def _directional_knockdown(phi_deg: float, ratio: float) -> float:
    """Fraction of in-plane capacity available at ``phi`` degrees off the layer plane.

    ``k = 1 - (1 - ratio) * sin^2(phi)``: linear in ``sin^2``, which is the same
    shape as the resolved-area argument behind a Tsai-Hill-style off-axis
    interpolation, and it is exact at both ends by construction — ``k(0) = 1``
    (pure in-plane, no welds loaded) and ``k(90) = ratio`` (pure layer-normal,
    all of it through the welds).

    **What was rejected, and which way the error runs.** The other standard
    choice is Hankinson's formula at ``n = 2``,
    ``k = ratio / (ratio*cos^2(phi) + sin^2(phi))``, which agrees at the
    endpoints and dips harder in between. At ``ratio = 0.5``:

    ===========  ============  ================
    phi (deg)    this formula  Hankinson (n=2)
    ===========  ============  ================
    30           0.875         0.800
    45           0.750         0.667
    60           0.625         0.571
    ===========  ============  ================

    So this interpolation is the **optimistic** of the two by 9-13% through the
    middle of the range, and that matters at the boundary: a part at phi = 45
    with a utilisation of 0.74 reads 0.99 (pass) here and 1.11 (fail) under
    Hankinson. It is kept anyway, for a stated reason — Hankinson is an empirical
    fit to *wood*, transplanting its exponent to a bead-and-weld stack is a
    borrowed curve rather than a measured one, and a gate that is supposed to
    catch orientations that are plainly wrong should not also be condemning
    marginal ones on the strength of a curve nobody here has measured. The
    consequence is written down instead: **in the 20-70 degree band this gate is
    optimistic, and a part that only just passes it has not been proven.** A
    project that has coupon data for its own material and settings should state
    the measured ``layer_normal_strength_ratio``, which moves both ends of the
    curve and is worth more than arguing about its middle.
    """
    return 1.0 - (1.0 - ratio) * (math.sin(math.radians(phi_deg)) ** 2)


def _floats(value: Any, n: int) -> list[float] | None:
    """Coerce a sequence to exactly ``n`` floats, or None."""
    if isinstance(value, (str, bytes)) or not isinstance(value, Iterable):
        return None
    try:
        out = [float(v) for v in value]
    except (TypeError, ValueError):
        return None
    return out if len(out) == n else None


#: The axis names and the parser, one copy for the pack: ``fdm_print_parts.py``.
AXIS_NAMES = _PARTS.AXIS_NAMES
_direction = _PARTS.direction


def _direction_problem(name: str, value: Any) -> str:
    """Why a direction was rejected, in the words needed to fix it in one edit."""
    return (f"{name} is {value!r}, which is not a direction this gate can read — "
            f"give an [x, y, z] vector in the part's print orientation "
            f"(e.g. [0, 0, 1]) or an axis name ('z', '-y'). It was READ, not "
            f"missing, so nothing here says the key is absent")


# --------------------------------------------------------------------------- #
# fdm.bed_fit
# --------------------------------------------------------------------------- #
@gate(
    id="fdm.bed_fit",
    title="Part fits the build volume once the brim is on it",
    claims=["manufacturability", "fdm", "additive", "printability", "bed-fit"],
    tier=Tier.INSTANT,
    settles="bed fit",
    # Prerequisite fdm.process_model_valid (P2.2-D12): it checks the units and the process
    #    envelope this gate's arithmetic assumes; a part described in metres reads
    #    as a confident answer here about a different object.
    #    Isolated: the guard passes this gate's own known-bad control
    #    (test_packs.ControlsAreIsolated).
    needs=["fdm.process_model_valid"],
    negative_control=NegativeControl(
        fixture="selftest/bad_params.py:brim_overflow",
        note="a footprint sized halfway between the bed minus its brim and the "
             "raw bed — derived from both, so it is always inside the bed and "
             "always outside the brimmed area; the one case a naive bed check "
             "calls a pass and the printer calls a crash",
    ),
)
def bed_fit(ctx: GateContext) -> Verdict:
    """Footprint against the bed MINUS the brim allowance, both rotations tried.

    Bed size is a project parameter (it is the machine you own). The brim
    allowance is domain knowledge with a documented default, ``BRIM_MM_DEFAULT``.
    Height is checked against the raw Z: the brim costs nothing vertically.

    The gate tries the part as modelled and rotated 90 degrees in the XY plane,
    because that is a free change a slicer makes for you, and reports which
    orientation it took. Rotation about X or Y is NOT free — it changes the
    overhang, bridge and layer-alignment answers — so this gate does not consider
    it.
    """
    # Many parts, one verdict — but ONLY when the set states per-part extents.
    # A project that names thirteen meshes and one bounding box has told this
    # gate about one part, and skipping thirteen times would be a worse answer
    # than the one it can actually give. The fall-through says so in the verdict
    # rather than letting the subject change silently, which is the complaint
    # this whole multi-part mode exists to answer.
    parts = _PARTS.part_set(ctx)
    if parts.problem:
        return Verdict(gate="fdm.bed_fit", passed=False, skipped=True,
                       skip_reason=parts.problem)
    boxed = [p for p in parts if p.bbox is not None]
    if boxed:
        return _bed_fit_over_set(ctx, parts)
    set_note = ""
    if parts:
        set_note = (f"; the {len(parts)} part(s) in `{parts.source}` state no "
                    f"per-part extent, so this verdict is about the one box above "
                    f"and not about them")

    bbox = _floats(_look(ctx, BBOX_KEYS, None), 3)
    if bbox is None:
        return _skip("fdm.bed_fit", BBOX_KEYS,
                     "the [x, y, z] extent of ONE PRINTED PART in its print "
                     "orientation, mm — not the assembly envelope, which is what "
                     "cad-solid reads from cad.assembly_bbox_mm", ctx)

    machine = _machine(ctx)
    if isinstance(machine, Verdict):
        return machine
    bed_x, bed_y, bed_z, brim = machine
    fx, fy, fz = bbox

    usable = _usable(bed_x, bed_y, brim)
    if isinstance(usable, Verdict):
        return usable
    usable_x, usable_y = usable

    util, plan_util, z_util, how, driver = _bed_utilisation(
        fx, fy, fz, usable_x, usable_y, bed_z)

    # Locate the part that busts the envelope — the WHOLE part, because that is
    # what is too big. There is no face to blame and no point to pin: the bbox is
    # a property of the part, and a pin on the largest face would be a guess
    # dressed up as a measurement.
    #
    # Only when there is a mesh to draw. This is a tier-0 gate that runs on three
    # numbers, so it fires happily on a project that has stated a bounding box and
    # exported nothing — and a locator is a promise that something is drawn. One
    # aimed at a view that does not exist is reported by `site build` as a dangling
    # anchor, which would make an ordinary bbox-only project look like a broken
    # pack.
    locators = []
    if util > 1.0 and _PARTS.mesh_path(ctx):
        locators = [Locator(
            view=_PARTS.VIEW_ID, target=_PARTS.node(_PARTS.mover(ctx)), kind="part",
            value=round(util, 3),
            label=f"{util:.2f}x the usable build volume in {driver} "
                  f"({fx:.0f}x{fy:.0f}x{fz:.0f} mm {how})",
        )]

    return Verdict(
        gate="fdm.bed_fit",
        passed=util <= 1.0,
        measured=round(util, 3),
        limit=1.0, comparator="<=",
        units="utilisation",
        detail=f"{fx:.0f}x{fy:.0f}x{fz:.0f} mm vs {usable_x:.0f}x{usable_y:.0f} usable "
               f"({bed_x:.0f}x{bed_y:.0f} bed - 2x{brim:.1f} brim) x {bed_z:.0f} Z; "
               f"{how}, worst util {util:.2f} in {driver}" + set_note,
        locators=locators,
    )


def _machine(ctx: GateContext):
    """``(bed_x, bed_y, bed_z, brim)`` or a SKIPPED verdict naming what is missing.

    The machine is a project parameter — it is the printer you own — and the brim
    allowance is domain knowledge with a documented default. Shared by the
    one-part and many-part paths so the two cannot come to judge parts against
    different beds (rule 2).
    """
    bed_x = _look(ctx, ("bed_x_mm", "bed_xy_mm", "bed_xy", "bed_x"), _MISSING)
    if bed_x is _MISSING:
        return _skip("fdm.bed_fit", ("bed_x_mm", "bed_xy_mm"),
                     "the machine's usable bed X, mm")
    bed_y = _look(ctx, ("bed_y_mm", "bed_xy_mm", "bed_xy", "bed_y"), bed_x)
    bed_z = _look(ctx, ("bed_z_mm", "build_height_mm", "bed_z"), _MISSING)
    if bed_z is _MISSING:
        return _skip("fdm.bed_fit", ("bed_z_mm", "build_height_mm"),
                     "the machine's Z travel, mm")
    brim = float(_look(ctx, ("brim_mm", "brim_allowance_mm"), BRIM_MM_DEFAULT))
    return float(bed_x), float(bed_y), float(bed_z), brim


def _usable(bed_x: float, bed_y: float, brim: float):
    """The bed minus its brim on each side, or a verdict refusing the brim."""
    usable_x, usable_y = bed_x - 2.0 * brim, bed_y - 2.0 * brim
    if usable_x <= 0 or usable_y <= 0:
        return Verdict(
            gate="fdm.bed_fit", passed=False, measured=round(brim, 2),
            limit=round(min(bed_x, bed_y) / 2.0, 2), comparator="<", units="mm",
            detail=f"brim allowance {brim:.1f} mm per side consumes the whole "
                   f"{bed_x:.0f}x{bed_y:.0f} mm bed — check brim_mm",
        )
    return usable_x, usable_y


def _bed_utilisation(fx: float, fy: float, fz: float, usable_x: float,
                     usable_y: float, bed_z: float):
    """``(util, plan_util, z_util, how, driver)`` for one box on one machine.

    Both square rotations are tried, because that is a free change a slicer makes
    for you. Rotation about X or Y is NOT free — it changes the overhang, bridge
    and layer-alignment answers — so it is not considered here.
    """
    as_modelled = max(fx / usable_x, fy / usable_y)
    rotated = max(fy / usable_x, fx / usable_y)
    plan_util = min(as_modelled, rotated)
    how = "as modelled" if as_modelled <= rotated else "rotated 90 deg in XY"
    z_util = fz / bed_z if bed_z > 0 else float("inf")
    util = max(plan_util, z_util)
    driver = "XY" if plan_util >= z_util else "Z"
    return util, plan_util, z_util, how, driver


def _bed_fit_over_set(ctx: GateContext, parts) -> Verdict:
    """Every part in the set against the machine, one verdict, the worst named.

    A part named by the set with no extent of its own is reported as unmeasured
    and stays in the denominator — ``worst of 12`` on a thirteen-part project is
    the same lie the single-part workaround told, arrived at from the other side.
    """
    machine = _machine(ctx)
    if isinstance(machine, Verdict):
        return machine
    bed_x, bed_y, bed_z, brim = machine
    usable = _usable(bed_x, bed_y, brim)
    if isinstance(usable, Verdict):
        return usable
    usable_x, usable_y = usable

    outcomes = []
    for part in parts:
        if part.bbox is None:
            outcomes.append(_FOLD.skipped_part(
                part.name,
                "the set states no [x, y, z] extent for this part — add it to "
                "bbox_by_part_mm, or to this part's entry",
                path=part.raw))
            continue
        fx, fy, fz = part.bbox
        util, _plan, _z, how, driver = _bed_utilisation(
            fx, fy, fz, usable_x, usable_y, bed_z)
        # Same rule as the single-part path: a locator is a promise that something
        # is drawn, so only a part with a mesh behind it gets one.
        locators = []
        if util > 1.0 and part.raw:
            locators = [Locator(
                view=_PARTS.VIEW_ID, target=part.node, kind="part",
                value=round(util, 3),
                label=f"{util:.2f}x the usable build volume in {driver} "
                      f"({fx:.0f}x{fy:.0f}x{fz:.0f} mm {how})")]
        outcomes.append(_FOLD.measured_part(
            part.name, score=util, measured=round(util, 3), limit=1.0,
            note=f"at {util:.2f}x the usable build volume in {driver} "
                 f"({fx:.0f}x{fy:.0f}x{fz:.0f} mm {how})",
            path=part.raw,
            row=f"{fx:.1f}\t{fy:.1f}\t{fz:.1f}\t{how}\t{driver}",
            locators=locators))

    summary = _FOLD.write_table(
        ctx, "fdm-bed-fit-parts.txt", "fdm.bed_fit", parts.source, outcomes,
        header_lines=[
            f"{usable_x:.0f}x{usable_y:.0f} mm usable ({bed_x:.0f}x{bed_y:.0f} bed "
            f"- 2x{brim:.1f} brim) x {bed_z:.0f} mm Z",
            "score is the utilisation: the worst of footprint/usable and height/Z, "
            "after trying both square rotations",
        ],
        columns="x_mm\ty_mm\tz_mm\torientation\tdriver")
    return _FOLD.fold(
        "fdm.bed_fit", outcomes, source=parts.source, units="utilisation",
        quantity="build-volume fit", evidence=[summary],
        extra_detail=f"{usable_x:.0f}x{usable_y:.0f} usable ({bed_x:.0f}x{bed_y:.0f} "
                     f"bed - 2x{brim:.1f} brim) x {bed_z:.0f} Z")


# --------------------------------------------------------------------------- #
# fdm.min_wall
# --------------------------------------------------------------------------- #
@gate(
    id="fdm.min_wall",
    title="Thinnest wall across the layer plane carries at least three perimeters",
    # `perimeters` is this gate's own tag: a claim about the three-perimeter rule
    # binds it and nothing else here. The broad four stay, because a thin wall IS
    # a printability finding (see PACK.md, "Claim tags").
    claims=["manufacturability", "fdm", "additive", "printability", "perimeters"],
    tier=Tier.INSTANT,
    settles="wall thickness",
    # Prerequisite fdm.process_model_valid (P2.2-D12): it checks the units and the process
    #    envelope this gate's arithmetic assumes; a part described in metres reads
    #    as a confident answer here about a different object.
    #    Isolated: the guard passes this gate's own known-bad control
    #    (test_packs.ControlsAreIsolated).
    needs=["fdm.process_model_valid"],
    negative_control=NegativeControl(
        fixture="selftest/bad_params.py:two_perimeter_wall",
        note="the same part with its thinnest section cut to two beads of the "
             "line width the project actually uses — two perimeters and a seam "
             "of gap fill, which prints and then fails in service; nothing else "
             "about the part changes",
    ),
)
def min_wall(ctx: GateContext) -> Verdict:
    """Thinnest wall across the layer plane vs ``PERIMETERS_MIN`` perimeters.

    The wall is measured perpendicular to the build axis, where the slicer lays
    perimeters side by side (``MIN_WALL_KEYS`` says what slipped through when it
    was "the thinnest section anywhere"). A floor, roof or web is measured along
    the build axis and is a count of layers; this gate does not judge it.

    The threshold comes from ``NOZZLE_MIN_WALL_MM`` when the nozzle is in the
    table, and from ``PERIMETERS_MIN * extrusion width`` otherwise. An explicit
    ``extrusion_width_mm`` in the projection wins over the table, because a
    project that has tuned its line width knows more than the table does.

    Given ``part_min_wall_by_part_mm`` it judges every part and returns one
    verdict naming the worst, as the set gates do (:func:`_min_wall_over_set`).

    What this does NOT check: whether the thin section is thin *enough to matter*
    structurally. A 1 mm cosmetic fin failing this gate is a nuisance; a 1 mm
    load path failing it is a broken part. The gate cannot tell them apart, and
    a project that wants that distinction should carry two parameters.
    """
    walls = _PARTS.per_part(ctx, MIN_WALL_BY_PART_KEYS,
                            "the thinnest wall across the layer plane, mm")
    if walls.problem:
        return _per_part_problem("fdm.min_wall", walls.problem)
    if walls.values:
        return _min_wall_over_set(ctx, walls)

    thin = _look(ctx, MIN_WALL_KEYS, _MISSING)
    if thin is _MISSING:
        return _skip("fdm.min_wall", MIN_WALL_KEYS,
                     "the thinnest wall MEASURED across the layer plane of the part "
                     "(perpendicular to the build axis), mm — cad-solid's min_wall_mm "
                     "is the opposite kind of number, the thinnest wall a PROCESS "
                     "allows", ctx)
    rule = _wall_rule(ctx)
    if isinstance(rule, Verdict):
        return rule
    limit, bead, source = rule
    thin = float(thin)
    perims = thin / bead if bead > 0 else 0.0
    return Verdict(
        gate="fdm.min_wall",
        passed=thin >= limit,
        measured=round(thin, 3),
        limit=round(limit, 3), comparator=">=",
        units="mm",
        detail=f"thinnest wall across the layer plane {thin:.2f} mm = {perims:.1f} "
               f"beads vs {limit:.2f} mm minimum ({source})"
               + _set_note(ctx, "thinnest wall", MIN_WALL_BY_PART_KEYS[0]),
    )


def _wall_rule(ctx: GateContext):
    """``(limit, bead, source)`` for the three-perimeter rule, or a SKIP.

    One copy for the one-part and the many-part path, so the two cannot come to
    judge walls against different beads (rule 2).
    """
    nozzle = _look(ctx, NOZZLE_KEYS, _MISSING)
    if nozzle is _MISSING:
        return _skip("fdm.min_wall", ("nozzle_d_mm", "nozzle_diameter_mm"),
                     "the nozzle diameter the part will be printed with, mm")
    nozzle = float(nozzle)
    width = _look(ctx, ("extrusion_width_mm", "line_width_mm"), _MISSING)
    if width is not _MISSING:
        bead = float(width)
        limit = PERIMETERS_MIN * bead
        source = f"{PERIMETERS_MIN} x {bead:.2f} mm line width"
    else:
        table = NOZZLE_MIN_WALL_MM.get(round(nozzle, 2))
        limit = table if table is not None else PERIMETERS_MIN * nozzle
        # The bead the limit was built from, so the reported count and the limit
        # cannot disagree: dividing by the nozzle while the limit came from the
        # line width printed "2.40 mm = 6.0 beads" for a wall that is 5.7 beads
        # of the bead actually being laid, and PACK.md teaches readers to act on
        # that count.
        bead = limit / PERIMETERS_MIN if PERIMETERS_MIN else nozzle
        source = (f"{PERIMETERS_MIN} perimeters at a {nozzle:.2f} mm nozzle"
                  + ("" if table is not None else ", off-table"))
    return limit, bead, source


def _min_wall_over_set(ctx: GateContext, walls) -> Verdict:
    """Every part's thinnest wall against the one rule, the worst part named.

    The score is the limit over the wall, so above 1.0 is a wall thinner than
    three beads, and the fold's ranking (worst first) is the thinnest wall for the
    rule. A part of the set with no number in the map is not measured, stays in
    the denominator, and keeps the set from passing.
    """
    names, source, bail = _set_names(ctx, "fdm.min_wall", walls)
    if bail is not None:
        return bail
    rule = _wall_rule(ctx)
    if isinstance(rule, Verdict):
        return rule
    limit, bead, rule_source = rule
    outcomes = []
    for name in names:
        thin = walls.values.get(name)
        if thin is None:
            outcomes.append(_FOLD.skipped_part(
                name, f"{walls.source} states no thinnest wall for this part"))
            continue
        outcomes.append(_FOLD.measured_part(
            name, score=limit / thin, measured=round(thin, 3), limit=round(limit, 3),
            note=f"at {thin:.2f} mm = {thin / bead:.1f} beads across the layer plane vs "
                 f"{limit:.2f} mm minimum",
            row=f"{thin:.3f}\t{thin / bead:.2f}"))
    summary = _FOLD.write_table(
        ctx, "fdm-min-wall-parts.txt", "fdm.min_wall", source, outcomes,
        header_lines=[f"minimum {limit:.2f} mm ({rule_source}); score is the minimum "
                      f"over the part's thinnest wall across the layer plane"],
        columns="wall_mm\tbeads")
    return _FOLD.fold("fdm.min_wall", outcomes, source=source, units="mm",
                      quantity="thinnest wall", evidence=[summary],
                      extra_detail=f"minimum {limit:.2f} mm ({rule_source})",
                      comparator=">=")


# --------------------------------------------------------------------------- #
# fdm.layer_alignment
# --------------------------------------------------------------------------- #
@gate(
    id="fdm.layer_alignment",
    title="Primary load is not carried across the layer bonds",
    # A STRUCTURAL screen, tagged only as one. What slipped through with the four
    # broad tags it shared with every fdm gate: a claim that a cover fits the bed,
    # has thick walls and needs no support bound this gate too, and it skips until
    # a load axis is stated, so the claim read Skipped on a part that carries no
    # load; projects stated a load axis they had no reason for to clear it. A
    # claim on layer loading carries `structural` or `layer-alignment` (PACK.md,
    # "Claim tags"; the move for older claims is written there). fdm.slice was
    # taken off the broad tags for the same reason.
    claims=["structural", "layer-alignment"],
    tier=Tier.INSTANT,
    settles="layer-normal load alignment",
    # Prerequisite fdm.process_model_valid (P2.2-D12): it checks the units and the process
    #    envelope this gate's arithmetic assumes; a part described in metres reads
    #    as a confident answer here about a different object.
    #    Isolated: the guard passes this gate's own known-bad control
    #    (test_packs.ControlsAreIsolated).
    needs=["fdm.process_model_valid"],
    negative_control=NegativeControl(
        fixture="selftest/bad_params.py:load_across_layers",
        note="the same part rotated so the load runs along the build axis, with "
             "the utilisation scaled to sit 15% past the layer-normal knockdown "
             "— the one change that turns a passing structural result into a "
             "part that snaps along a layer",
    ),
)
def layer_alignment(ctx: GateContext) -> Verdict:
    """Is the primary load direction across the layer lines?

    This is the gate that catches the failure a structural pack cannot see. A
    stress or deflection gate computes with bulk material properties taken from an
    injection-moulded or extruded coupon. A printed part is not that material: it
    is a stack of welded beads, and pulling perpendicular to the beads loads the
    welds, which are worth roughly ``LAYER_NORMAL_STRENGTH_RATIO`` of the bead.
    So a part can pass every structural gate in the ledger and still snap along a
    layer boundary the first time it is used.

    Two numbers come out:

    * ``phi`` — the angle between the primary load direction and the layer plane.
      0 deg is fully in-plane (best); 90 deg is fully layer-normal (worst).
    * the **derated utilisation** — the structural gate's utilisation divided by
      the directional knockdown :func:`_directional_knockdown`, whose docstring
      carries the interpolation's provenance and the direction its error runs in.
      That is the honest number, and it is what the gate fails on when the
      projection carries a utilisation.

    The knockdown depends on WHICH utilisation it was handed. A stress margin is
    derated by ``LAYER_NORMAL_STRENGTH_RATIO`` (~0.5); a deflection margin by
    ``LAYER_NORMAL_MODULUS_RATIO`` (~0.85), because stiffness is far less
    anisotropic than strength. Project ``utilisation_kind`` as ``"stress"`` or
    ``"deflection"``; a key named ``deflection_utilisation`` is taken at its word,
    and anything unclassifiable makes the gate skip rather than pick one.

    If no utilisation is projected, the gate falls back to the angle alone against
    ``ADVERSE_LAYER_ANGLE_DEG`` and says so in the verdict. That is a weaker check
    and it is labelled as one; it is not a substitute for knowing the margin.
    """
    raw_load, load_axis_key = _look_named(ctx, LOAD_AXIS_KEYS)
    if raw_load is _MISSING:
        return _skip("fdm.layer_alignment", LOAD_AXIS_KEYS,
                     "the primary load direction in the part's print orientation, "
                     "as an [x, y, z] vector or an axis name ('z', '-y')", ctx)
    load = _direction(raw_load)
    if load is None:
        # Present and unreadable is NOT absent, and the two must not share a
        # sentence: a project that stated `load_axis: "z"` was told the key was
        # missing and went looking in the model for something already there.
        return Verdict(
            gate="fdm.layer_alignment", passed=False, skipped=True,
            skip_reason=_direction_problem(load_axis_key or "load_axis", raw_load),
        )

    raw_build = _look(ctx, BUILD_AXIS_KEYS, _MISSING)
    build = None if raw_build is _MISSING else _direction(raw_build)
    if raw_build is not _MISSING and build is None:
        return Verdict(
            gate="fdm.layer_alignment", passed=False, skipped=True,
            skip_reason=_direction_problem("build_axis", raw_build),
        )
    if build is None:
        build = [0.0, 0.0, 1.0]
        build_note = "build axis assumed +Z"
    else:
        build_note = "build axis from the model"

    load_n = math.sqrt(sum(c * c for c in load))
    build_n = math.sqrt(sum(c * c for c in build))
    if load_n <= 0 or build_n <= 0:
        return Verdict(
            gate="fdm.layer_alignment", passed=False,
            detail=f"load_axis {load} / build_axis {build}: a zero-length direction "
                   f"vector describes no load — fix the model",
        )

    cos = abs(sum(a * b for a, b in zip(load, build))) / (load_n * build_n)
    cos = min(1.0, max(0.0, cos))
    phi = math.degrees(math.asin(cos))          # angle from the LAYER PLANE

    util, util_key = _look_named(
        ctx, ("utilisation", "structural_utilisation", "utilization",
              "stress_utilisation", "deflection_utilisation",
              "deflection_utilization", "stiffness_utilisation"))

    # WHICH quantity was handed over decides WHICH knockdown applies. A strength
    # knockdown on a deflection margin over-derates it by ~1.7x and condemns
    # orientations that are fine; a modulus knockdown on a stress margin is far
    # worse, because it under-derates the one that breaks the part. The gate does
    # not guess: an explicit utilisation_kind wins, a deflection-named key is
    # taken at its word, and anything else it cannot classify makes it skip.
    kind = _look(ctx, ("utilisation_kind", "utilization_kind"), _MISSING)
    if kind is _MISSING:
        # The scope, if the project wrote one, is not part of the key's meaning:
        # `fdm.deflection_utilisation` is a deflection utilisation exactly as
        # `deflection_utilisation` is, and reading the prefix as part of the name
        # would silently apply the STRENGTH knockdown to a stiffness margin.
        bare_util_key = util_key.rsplit(SCOPE_SEP, 1)[-1]
        kind = "deflection" if bare_util_key.startswith(("deflection", "stiffness")) else "stress"
    kind = str(kind).strip().lower()
    if kind in ("stress", "strength"):
        ratio = float(_look(ctx, ("layer_normal_strength_ratio",),
                            LAYER_NORMAL_STRENGTH_RATIO))
        ratio_note = f"layer-normal strength {ratio:.2f}x in-plane"
    elif kind in ("deflection", "stiffness", "modulus"):
        ratio = float(_look(ctx, ("layer_normal_modulus_ratio",),
                            LAYER_NORMAL_MODULUS_RATIO))
        ratio_note = f"layer-normal modulus {ratio:.2f}x in-plane"
    else:
        return Verdict(
            gate="fdm.layer_alignment", passed=False, skipped=True,
            skip_reason=f"utilisation_kind is {kind!r}, which is not a quantity this "
                        f"gate knows how to derate — say 'stress' (knocked down by "
                        f"layer_normal_strength_ratio, ~0.5) or 'deflection' (by "
                        f"layer_normal_modulus_ratio, ~0.85). Guessing between them "
                        f"is a 1.7x error in one direction or the other, so nothing "
                        f"was measured")

    knock = _directional_knockdown(phi, ratio)

    if util is _MISSING:
        return Verdict(
            gate="fdm.layer_alignment",
            passed=phi <= ADVERSE_LAYER_ANGLE_DEG,
            measured=round(phi, 1),
            limit=ADVERSE_LAYER_ANGLE_DEG, comparator="<=",
            units="deg from layer plane",
            detail=f"load at {phi:.0f} deg to the layer plane ({build_note}), "
                   f"knockdown {knock:.2f}x; ANGLE ONLY — no 'utilisation' in the "
                   f"projection, so the derating was not applied to a margin",
        )

    util = float(util)
    derated = util / knock if knock > 0 else float("inf")
    return Verdict(
        gate="fdm.layer_alignment",
        passed=derated <= 1.0,
        measured=round(derated, 3),
        limit=1.0, comparator="<=",
        units="derated utilisation",
        detail=f"load at {phi:.0f} deg to the layer plane ({build_note}): "
               f"{kind} utilisation {util:.2f} ({util_key}) / knockdown {knock:.2f} "
               f"= {derated:.2f} vs 1.00 ({ratio_note})",
    )


# --------------------------------------------------------------------------- #
# fdm.print_time_est
# --------------------------------------------------------------------------- #
@gate(
    id="fdm.print_time_est",
    title="Estimated print time within the project's ceiling",
    # A COST question, tagged only as one, for the reason fdm.layer_alignment is:
    # with the broad tags every printability claim bound it and read Skipped
    # until a print speed was stated.
    claims=["cost", "print-time"],
    tier=Tier.INSTANT,
    settles="print time",
    # Prerequisite fdm.process_model_valid (P2.2-D12): it checks the units and the process
    #    envelope this gate's arithmetic assumes; a part described in metres reads
    #    as a confident answer here about a different object.
    #    Isolated: the guard passes this gate's own known-bad control
    #    (test_packs.ControlsAreIsolated).
    needs=["fdm.process_model_valid"],
    negative_control=NegativeControl(
        fixture="selftest/bad_params.py:crawling_speed",
        note="the same part at the speed that puts it 15% past the project's own "
             "print-time ceiling — solved from the gate's own model, in the "
             "flexible-filament regime; geometry untouched",
    ),
)
def print_time_est(ctx: GateContext) -> Verdict:
    """Rough time AND filament mass from volume, infill and speed. NOT a slice.

    Extruded volume is shell plus infill of the remainder; extruded length is that
    volume divided by the cross-section of one bead (a stadium, not a rectangle —
    see :func:`_process_model.bead_area_mm2`); time is that length at speed,
    divided by ``DUTY_FACTOR`` to pay for acceleration, travel and cooling holds,
    plus a fixed startup. Mass is the same extruded volume times the filament
    density.

    **Two ceilings, both of which fail the gate**: ``max_print_time_h`` and
    ``max_filament_g``. The second one is not decoration — a part that needs more
    than one spool needs a spool change or a splice mid-print, and that is a
    different (and worse) problem from a long print. The gate reports whichever
    of the two is closer to its limit.

    Expect +/- 30% against a real slicer, and worse on small, tall or many-island
    parts where travel dominates. It is here to catch the order-of-magnitude
    mistake — the part that is a three-day print, the part that will not fit on
    one spool — not to schedule a farm. When the number matters, slice it.

    Given ``part_volume_by_part_mm3`` it estimates every part as its own print
    and returns one verdict naming the part nearest a ceiling, with the set's
    total if the parts are printed one after another (:func:`_time_over_set`).
    """
    volumes = _PARTS.per_part(ctx, VOLUME_BY_PART_KEYS, "the part's solid volume, mm^3")
    if volumes.problem:
        return _per_part_problem("fdm.print_time_est", volumes.problem)
    if volumes.values:
        return _time_over_set(ctx, volumes)

    vol = _look(ctx, VOLUME_KEYS, _MISSING)
    if vol is _MISSING:
        return _skip("fdm.print_time_est", ("volume_mm3", "solid_volume_mm3"),
                     "the part's solid volume, mm^3")
    proc = _time_process(ctx)
    if isinstance(proc, Verdict):
        return proc
    vol = float(vol)
    if vol <= 0:
        return Verdict(
            gate="fdm.print_time_est", passed=False,
            detail=f"volume {vol}, speed {proc['speed']}, layer {proc['layer_h']}, width "
                   f"{proc['width']}: a non-positive value here makes the estimate "
                   f"meaningless",
        )
    area = _look(ctx, ("surface_area_mm2", "area_mm2"), _MISSING)
    est = _estimate(vol, None if area is _MISSING else float(area), proc)
    worst = max(est["time_frac"], est["mass_frac"])
    return Verdict(
        gate="fdm.print_time_est",
        passed=worst <= 1.0,
        measured=round(worst, 3),
        limit=1.0, comparator="<=",
        units="of limit",
        detail=f"ESTIMATE ~{est['hours']:.1f} h of {proc['limit_h']:.0f} h, "
               f"~{est['grams']:.0f} g of {proc['limit_g']:.0f} g "
               f"({est['extruded'] / 1000.0:.1f} cm^3 at {proc['infill']:.0%} infill, "
               f"{est['shell']}) — {proc['layer_h']:.2f} mm layers at "
               f"{proc['speed']:.0f} mm/s x {DUTY_FACTOR:.2f} duty; {est['driver']} is "
               f"the binding one at {worst:.2f} of its ceiling. Not a slice."
               + _set_note(ctx, "volume", VOLUME_BY_PART_KEYS[0]),
    )


def _time_process(ctx: GateContext):
    """The process half of the estimate, read once for one part or a set, or a SKIP.

    Shared by both paths so a set's parts are estimated on the same machine,
    speed and ceilings as a single part would be (rule 2).
    """
    speed = _look(ctx, ("print_speed_mm_s", "speed_mm_s"), _MISSING)
    if speed is _MISSING:
        return _skip("fdm.print_time_est", ("print_speed_mm_s", "speed_mm_s"),
                     "the nominal printing speed, mm/s")
    layer_h = _look(ctx, ("layer_height_mm", "layer_h_mm"), _MISSING)
    if layer_h is _MISSING:
        return _skip("fdm.print_time_est", ("layer_height_mm",),
                     "the layer height, mm")
    nozzle = _look(ctx, NOZZLE_KEYS, _MISSING)
    width = _look(ctx, ("extrusion_width_mm", "line_width_mm"), nozzle)
    if width is _MISSING:
        return _skip("fdm.print_time_est", ("extrusion_width_mm", "nozzle_d_mm"),
                     "the extrusion width (or the nozzle diameter to stand in for "
                     "it), mm")
    speed, layer_h, width = float(speed), float(layer_h), float(width)
    if min(speed, layer_h, width) <= 0:
        return Verdict(
            gate="fdm.print_time_est", passed=False,
            detail=f"speed {speed}, layer {layer_h}, width {width}: a non-positive "
                   f"value here makes the estimate meaningless",
        )
    return {
        "speed": speed, "layer_h": layer_h, "width": width,
        "infill": float(_look(ctx, ("infill_fraction", "infill"), 0.20)),
        "perims": int(_look(ctx, ("perimeters", "wall_count"), PERIMETERS_MIN)),
        "density": float(_look(ctx, ("filament_density_g_cm3", "density_g_cm3"),
                               FILAMENT_DENSITY_G_CM3_DEFAULT)),
        "limit_h": float(_look(ctx, ("max_print_time_h", "print_time_limit_h"),
                               MAX_PRINT_TIME_H_DEFAULT)),
        "limit_g": float(_look(ctx, ("max_filament_g", "max_filament_mass_g",
                                     "spool_g"), MAX_FILAMENT_G_DEFAULT)),
    }


def _estimate(vol: float, area: float | None, proc: dict) -> dict:
    """Hours, grams and the fraction of each ceiling for one part. Decides nothing.

    Whichever ceiling is closer is reported, as a fraction of itself, so one number
    answers "is this print orderable" for both. The raw hours and grams are in the
    detail either way: a reader who only cares about time should not have to divide
    anything to find it.
    """
    extruded, shell = _pm.extruded_volume_mm3(
        vol, infill=proc["infill"], perimeters=proc["perims"], width=proc["width"],
        surface_area_mm2=area)
    hours = _pm.print_hours(extruded, proc["speed"], proc["layer_h"], proc["width"])
    grams = _pm.grams(extruded, proc["density"])
    time_frac = hours / proc["limit_h"] if proc["limit_h"] > 0 else float("inf")
    mass_frac = grams / proc["limit_g"] if proc["limit_g"] > 0 else float("inf")
    return {"extruded": extruded, "shell": shell, "hours": hours, "grams": grams,
            "time_frac": time_frac, "mass_frac": mass_frac,
            "driver": "time" if time_frac >= mass_frac else "filament mass"}


def _time_over_set(ctx: GateContext, volumes) -> Verdict:
    """Every part as its own print, one verdict, the part nearest a ceiling named.

    A part of the set with no volume in the map is not estimated and keeps the set
    from passing. The total is the sum of the parts printed one after another: it
    is not a plate's time, which this pack does not lay out.
    """
    names, source, bail = _set_names(ctx, "fdm.print_time_est", volumes)
    if bail is not None:
        return bail
    proc = _time_process(ctx)
    if isinstance(proc, Verdict):
        return proc
    areas = _PARTS.per_part(ctx, AREA_BY_PART_KEYS, "the part's surface area, mm^2")
    if areas.problem:
        return _per_part_problem("fdm.print_time_est", areas.problem)
    outcomes = []
    total_h = total_g = 0.0
    for name in names:
        vol = volumes.values.get(name)
        if vol is None:
            outcomes.append(_FOLD.skipped_part(
                name, f"{volumes.source} states no volume for this part"))
            continue
        est = _estimate(vol, areas.values.get(name), proc)
        total_h += est["hours"]
        total_g += est["grams"]
        worst = max(est["time_frac"], est["mass_frac"])
        outcomes.append(_FOLD.measured_part(
            name, score=worst, measured=round(worst, 3), limit=1.0,
            note=f"at ~{est['hours']:.1f} h and ~{est['grams']:.0f} g, {est['driver']} "
                 f"binding at {worst:.2f} of its ceiling",
            row=f"{est['hours']:.2f}\t{est['grams']:.1f}\t{est['driver']}"))
    summary = _FOLD.write_table(
        ctx, "fdm-print-time-parts.txt", "fdm.print_time_est", source, outcomes,
        header_lines=[f"ceilings {proc['limit_h']:.0f} h and {proc['limit_g']:.0f} g per "
                      f"print; {proc['layer_h']:.2f} mm layers at {proc['speed']:.0f} mm/s "
                      f"x {DUTY_FACTOR:.2f} duty; an ESTIMATE, not a slice",
                      "score is the nearer ceiling's fraction, each part its own print"],
        columns="hours\tgrams\tbinding")
    return _FOLD.fold(
        "fdm.print_time_est", outcomes, source=source, units="of limit",
        quantity="print time and filament mass", evidence=[summary],
        extra_detail=f"ESTIMATE, each part its own print; one after another ~{total_h:.1f} "
                     f"h and ~{total_g:.0f} g in all. Not a slice")


# --------------------------------------------------------------------------- #
# fdm.process_model_valid  — the validity guard
# --------------------------------------------------------------------------- #

UNIT_SANITY_MIN_MM = 1.0
"""Smallest plausible largest-dimension for a printed part, mm.

Below this the model is almost certainly not in millimetres. A part whose longest
edge is 0.08 is 80 mm expressed in metres, and every threshold in this pack — bed
size, wall thickness, bridge span — is then wrong by a factor of 1000."""

UNIT_SANITY_MAX_MM = 5000.0
"""Largest plausible largest-dimension, mm. Past 5 m the model is in some unit
this pack was not written for, or the geometry is broken. Either way its numbers
are not being compared against anything meaningful."""

LAYER_NOZZLE_RATIO_MAX = 0.75
"""Layer height as a fraction of nozzle diameter.

Above roughly 0.75 the extruded bead cannot be pressed into the layer below it:
there is no squish, so the bond is a tangent line between two cylinders instead of
a fused interface. Everything this pack says about wall strength and bridging
assumes a squished bead. Rejected: 0.8 (the commonly quoted ceiling, which is a
speed figure for non-structural parts, not a strength one)."""

LAYER_NOZZLE_RATIO_MIN = 0.10
"""Below about a tenth of the nozzle the bead is thinner than the tolerance of the
machine putting it down, and layer time dominates to no benefit."""

WIDTH_NOZZLE_RATIO_BAND = (0.8, 2.0)
"""Extrusion width as a fraction of nozzle diameter, low and high.

``NOZZLE_MIN_WALL_MM`` counts perimeters at a width near the nozzle diameter,
which is what every slicer ships. Under 0.8 the slicer is asking for less material
than the orifice meters cleanly; over 2.0 the bead cannot be pressed flat. Outside
the band the minimum-wall table is being applied to a bead it does not describe."""

MIN_SOLID_FILL_FRACTION = 0.001
"""Least plausible ``volume_mm3 / (bbox_x * bbox_y * bbox_z)``.

The bounding box is the one cross-check on ``volume_mm3`` that needs no other
parameter, and it is exact in one direction — a solid **cannot** occupy more than
its own bounding box, so a fill fraction above 1 is proof that the two numbers are
in different units. The floor is the softer half of the same check: a part whose
volume is a thousandth of its box is almost always a volume quoted in cm^3 under a
key that says mm^3 (a 1000x slip lands near 2e-4), not a real object.

Rejected: 0.01, which is a plausible-looking round number and wrongly condemns
genuinely sparse geometry — a 200 mm cube wireframe of 2 mm rods is 1.2e-3 — and
1e-4, which lets the common cm^3-for-mm^3 slip through. Anything sparser than
0.001 in a real part is a lattice, and a lattice is exactly the case where the
shell-and-infill time model has stopped applying anyway."""


@gate(
    id="fdm.process_model_valid",
    title="The process model this pack assumes actually applies",
    # DELIBERATELY BROAD. This gate does not measure the part — it decides whether
    # the other gates' numbers mean anything, so when it trips it should drag every
    # printability claim down with it. See docs/PACK_FORMAT.md on validity guards.
    # `structural` is in the list because fdm.layer_alignment carries it: if the
    # units are wrong, the utilisation that gate derates describes a different
    # object, and a structural claim must not read green off a number this gate has
    # just declared untrustworthy.
    claims=["manufacturability", "fdm", "additive", "printability", "structural",
            "process-validity"],
    tier=Tier.INSTANT,
    settles="process model validity",
    negative_control=NegativeControl(
        fixture="selftest/bad_params.py:metres_not_millimetres",
        note="the same part and machine described in metres — every length, area "
             "and volume in the projection divided by 1000 to the right power: "
             "one physically meaningful change, and every other gate in this "
             "pack goes on returning confident answers about it",
    ),
)
def process_model_valid(ctx: GateContext) -> Verdict:
    """Guard the assumptions every other gate in this pack is standing on.

    Closed-form and rule-of-thumb gates do not fail loudly when they stop applying.
    They keep returning confident numbers, and the numbers are wrong. This gate is
    the thing that notices.

    Two failure modes, both silent without it:

    * **Wrong units.** A model in metres makes a 80 mm part measure 0.08. The bed
      check passes trivially, the bridge check passes trivially, and the wall check
      fails for a reason that has nothing to do with the wall. Unit confusion is the
      most common cross-pack defect there is, and it is entirely preventable at this
      one boundary.

      One absolute floor only catches the 1000x slip. Centimetres and inches read
      as ordinary small parts and slide straight past it, so the gate also checks
      the projection against **itself**: the stated volume has to fit inside the
      stated bounding box, and the thinnest section has to be at least one bead of
      the stated nozzle. A part cannot be denser than its own box or thinner than
      one extrusion, whatever unit it is written in, so those two comparisons are
      scale-free in a way an absolute bound can never be.
    * **A process outside the envelope the rules were written for.** The minimum-wall
      table, the overhang limit and the bridging figures all assume a bead that gets
      squished into the layer below it. At a layer height near the nozzle diameter
      there is no squish, and none of those numbers describe the part any more.

    Passing this gate does not make the part printable. It means the rest of this
    pack is entitled to an opinion.

    **Over a print set** it checks every part the projection describes: each
    extent in ``bbox_by_part_mm``, each volume in ``part_volume_by_part_mm3``
    against that part's own box, each wall in ``part_min_wall_by_part_mm``
    against one bead and each floor in ``part_min_floor_by_part_mm`` against one
    layer, every problem named with its part. What slipped through when it read
    only ``part_bbox_mm``: a project that stated its set and no single box
    read Skipped here, so every gate behind it read Skipped too, until the person
    picked one part's box by hand — and then the guard vouched for one part.

    **A wall and a floor are different counts.** A wall across the layer plane is
    a whole number of beads, so one under a bead is another unit or unprintable.
    A floor along the build axis is a whole number of layers, so it is held to one
    layer, not one bead (``MIN_WALL_KEYS`` says what slipped through when the two
    shared one rule).
    """
    problems: list[str] = []
    notes: list[str] = []

    parts = _PARTS.part_set(ctx)
    set_boxes = [] if parts.problem else [(p.name, p.bbox) for p in parts
                                          if p.bbox is not None]
    # The per-part maps are read here only to be checked. A map this guard cannot
    # read is named in the detail and NOT counted as a problem: the gate that
    # reads it (fdm.min_wall, fdm.print_time_est) refuses it in its own verdict,
    # and failing here would skip every gate behind the guard, bed fit included,
    # over a key bed fit never reads.
    maps = {}
    for keys, what in ((MIN_WALL_BY_PART_KEYS, "a wall, mm"),
                       (MIN_FLOOR_BY_PART_KEYS, "a floor, mm"),
                       (VOLUME_BY_PART_KEYS, "a volume, mm^3")):
        found = _PARTS.per_part(ctx, keys, what)
        if found.problem:
            notes.append(f"not read: {found.problem}")
        maps[keys[0]] = found.values

    bbox = _floats(_look(ctx, BBOX_KEYS, None), 3)
    if bbox is None and not set_boxes:
        # Not a pass. An absolute bound on one length is the anchor every other
        # check here hangs off, and with no extent to measure, no unit has been
        # verified — reporting "process model applies" for a projection nothing
        # was checked against is precisely the lie this gate exists to prevent.
        return _skip("fdm.process_model_valid", BBOX_KEYS + ("bbox_by_part_mm",),
                     "the part's [x, y, z] extent in mm, or each part's in "
                     "bbox_by_part_mm — with no length in the projection no unit can "
                     "be verified, and every other gate here is standing on the "
                     "assumption that it is millimetres")

    def named(label: str | None, text: str) -> str:
        return f"{label}: {text}" if label else text

    extents = ([(None, bbox)] if bbox is not None else []) + set_boxes
    sane = []
    for label, box in extents:
        longest = max(abs(v) for v in box)
        if longest < UNIT_SANITY_MIN_MM:
            problems.append(named(label,
                f"longest dimension {longest:g} — below {UNIT_SANITY_MIN_MM:g} mm, so "
                f"the model is almost certainly in metres ({longest * 1000:.0f} mm?) "
                f"and every threshold in this pack is out by 1000x"))
        elif longest > UNIT_SANITY_MAX_MM:
            problems.append(named(label,
                f"longest dimension {longest:g} — above {UNIT_SANITY_MAX_MM:g} mm, so "
                f"the model is in a unit this pack was not written for, or the "
                f"geometry is broken"))
        else:
            sane.append(longest)
    if sane and len(extents) == 1:
        notes.append(f"extent {sane[0]:.0f} mm reads as millimetres")
    elif sane:
        notes.append(f"{len(sane)} of {len(extents)} extents read as millimetres "
                     f"(longest {max(sane):.0f} mm)")

    # ---- internal consistency, which is what catches cm and inches ---------- #
    # A single absolute floor only sees a 1000x error. Centimetres (a 9 mm part)
    # and inches (a 3.5 mm part) both read as perfectly ordinary small parts, and
    # PACK.md promises this gate notices them. What gives them away is that the
    # part stops agreeing with the OTHER numbers in the same projection: its
    # stated volume no longer fits inside its own bounding box, or its thinnest
    # wall is narrower than one bead of the nozzle printing it.
    fills = []
    pairs = []
    vol = _look(ctx, VOLUME_KEYS, _MISSING)
    if vol is not _MISSING and bbox is not None:
        pairs.append((None, float(vol), bbox))
    boxes_by_name = dict(set_boxes)
    for name, part_vol in maps[VOLUME_BY_PART_KEYS[0]].items():
        if name in boxes_by_name:
            pairs.append((name, part_vol, boxes_by_name[name]))
    for label, part_vol, box in pairs:
        box_vol = abs(box[0] * box[1] * box[2])
        if box_vol <= 0:
            continue
        fill = part_vol / box_vol
        if part_vol <= 0:
            problems.append(named(label,
                f"volume_mm3 {part_vol:g} is not positive — a solid has volume; this is "
                f"a broken projection, not a thin part"))
        elif fill > 1.0:
            problems.append(named(label,
                f"volume_mm3 {part_vol:g} is {fill:.1f}x its own bounding box "
                f"({box[0]:g}x{box[1]:g}x{box[2]:g} = {box_vol:g}) — geometrically "
                f"impossible, so the two are in different units (a bbox in cm with a "
                f"volume in mm^3 gives exactly this)"))
        elif fill < MIN_SOLID_FILL_FRACTION:
            problems.append(named(label,
                f"volume_mm3 {part_vol:g} fills {fill:.2e} of its bounding box (floor "
                f"{MIN_SOLID_FILL_FRACTION:g}) — that is a volume quoted in cm^3 under "
                f"a key that says mm^3, or geometry this pack's shell-and-infill model "
                f"does not describe"))
        else:
            fills.append(fill)
    if fills and len(pairs) == 1:
        notes.append(f"volume fills {fills[0]:.2f} of the bbox")
    elif fills:
        notes.append(f"{len(fills)} volume(s) fill {min(fills):.2f}-{max(fills):.2f} of "
                     f"their boxes")

    nozzle = _look(ctx, ("nozzle_d_mm", "nozzle_d", "nozzle_diameter_mm"), _MISSING)
    layer = _look(ctx, LAYER_KEYS, _MISSING)
    layer = None if layer is _MISSING else float(layer)
    if nozzle is not _MISSING:
        nozzle = float(nozzle)
        known = sorted(NOZZLE_MIN_WALL_MM)
        if not (known[0] <= nozzle <= known[-1]):
            problems.append(
                f"nozzle {nozzle:g} mm is outside the {known[0]:g}-{known[-1]:g} mm range "
                f"this pack's wall and bridging rules were written for — extrapolating "
                f"them is guesswork, not a measurement")
        if layer is not None:
            ratio = layer / nozzle if nozzle else float("inf")
            if ratio > LAYER_NOZZLE_RATIO_MAX:
                problems.append(
                    f"layer {layer:g} mm is {ratio:.2f}x the {nozzle:g} mm nozzle "
                    f"(max {LAYER_NOZZLE_RATIO_MAX}) — the bead cannot be squished into "
                    f"the layer below, so every strength and bridging figure here "
                    f"assumes a bond the part will not have")
            elif ratio < LAYER_NOZZLE_RATIO_MIN:
                problems.append(
                    f"layer {layer:g} mm is only {ratio:.2f}x the {nozzle:g} mm nozzle "
                    f"(min {LAYER_NOZZLE_RATIO_MIN}) — thinner than the machine's own "
                    f"positioning tolerance")
            else:
                notes.append(f"layer/nozzle {ratio:.2f} inside "
                             f"{LAYER_NOZZLE_RATIO_MIN}-{LAYER_NOZZLE_RATIO_MAX}")

        width = _look(ctx, ("extrusion_width_mm", "line_width_mm"), _MISSING)
        if width is not _MISSING and nozzle:
            wr = float(width) / nozzle
            lo, hi = WIDTH_NOZZLE_RATIO_BAND
            if not (lo <= wr <= hi):
                problems.append(
                    f"extrusion width {float(width):g} mm is {wr:.2f}x the {nozzle:g} mm "
                    f"nozzle (band {lo}-{hi}) — NOZZLE_MIN_WALL_MM counts perimeters at "
                    f"a width near the nozzle, so the wall rule is being applied to a "
                    f"bead it does not describe")
            else:
                notes.append(f"width/nozzle {wr:.2f} inside {lo}-{hi}")

        # The other unit cross-check, and the one that catches inches. A wall
        # across the layer plane is a whole number of beads, so a part cannot HAVE
        # one narrower than one bead of the nozzle printing it. A model in cm
        # reports a 3.2 mm wall as 0.32; in inches, as 0.126. Both are below any
        # nozzle in the table and neither is below the absolute metres floor.
        walls = []
        thin = _look(ctx, MIN_WALL_KEYS, _MISSING)
        if thin is not _MISSING:
            walls.append((None, float(thin)))
        walls += list(maps[MIN_WALL_BY_PART_KEYS[0]].items())
        beads = []
        for label, wall in walls:
            if nozzle <= 0:
                break
            if wall <= 0:
                problems.append(named(label,
                    f"min_wall_mm {wall:g} is not positive — a section has thickness"))
            elif wall < nozzle:
                problems.append(named(label,
                    f"thinnest wall across the layer plane {wall:g} mm is narrower than "
                    f"one {nozzle:g} mm bead ({wall / nozzle:.2f} beads) — a wall across "
                    f"the layer plane is a whole number of beads, so either the lengths "
                    f"are in another unit (x25.4 = {wall * 25.4:.2f} mm if inches, x10 = "
                    f"{wall * 10:.2f} mm if cm) or the feature cannot be printed at all. "
                    f"A floor or web along the build axis is a count of layers: state it "
                    f"as part_min_floor_mm"))
            else:
                beads.append(wall / nozzle)
        if beads and len(walls) == 1:
            notes.append(f"thinnest wall {beads[0]:.1f} beads wide")
        elif beads:
            notes.append(f"{len(beads)} walls {min(beads):.1f}+ beads wide")

    # A floor, roof or web along the build axis is a whole number of LAYERS, so it
    # is held to one layer. Without a layer height there is nothing to hold it to,
    # and the detail says it was not checked rather than passing it.
    floors = []
    floor = _look(ctx, MIN_FLOOR_KEYS, _MISSING)
    if floor is not _MISSING:
        floors.append((None, float(floor)))
    floors += list(maps[MIN_FLOOR_BY_PART_KEYS[0]].items())
    if floors and layer is None:
        notes.append(f"{len(floors)} floor(s) not checked: no layer_height_mm")
    elif floors and layer > 0:
        counts = []
        for label, value in floors:
            if value <= 0:
                problems.append(named(label,
                    f"min_floor_mm {value:g} is not positive — a section has thickness"))
            elif value < layer:
                problems.append(named(label,
                    f"thinnest section along the build axis {value:g} mm is under one "
                    f"{layer:g} mm layer ({value / layer:.2f} layers) — a floor is a "
                    f"whole number of layers, so either the lengths are in another unit "
                    f"or the feature cannot be printed at all"))
            else:
                counts.append(value / layer)
        if counts:
            notes.append(f"thinnest floor {min(counts):.1f} layers deep")

    checked = "; ".join(notes) or "nothing checkable in the projection"
    # Every problem in full, one per line. The spine caps a verdict line, and over
    # a set the third part's problem would be cut from the detail: the count in
    # `measured` is always every problem, and this file names each one.
    evidence = []
    if problems:
        evidence = [ctx.out_path("fdm-process-model-problems.txt")]
        with open(evidence[0], "w", encoding="utf-8") as fh:
            fh.write(f"# fdm.process_model_valid — {len(problems)} problem(s)\n")
            fh.write("".join(f"{line}\n" for line in problems))
    return Verdict(
        gate="fdm.process_model_valid",
        passed=not problems,
        measured=float(len(problems)),
        limit=0.0, comparator="<=",
        units="problems",
        detail=("; ".join(problems) if problems
                else f"process model applies — {checked}"),
        evidence=evidence,
    )
