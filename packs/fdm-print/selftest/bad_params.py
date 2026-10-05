# SPDX-License-Identifier: Apache-2.0
"""Known-bad projections for the tier-0 printability gates.

Each function takes the real ``GateContext`` and returns one whose projection has
ONE physically meaningful thing changed, in the direction the gate under test
cares about. Everything else is left exactly as the project had it, so a failing
verdict can only be about the thing that moved.

That constraint is the whole point of a negative control. A fixture that is bad in
some other way — an empty dict, a missing key, a corrupt file — proves the gate
survives garbage, not that it measures what it claims to measure. ``nopekit gate
selftest`` requires each gate to FAIL here; a gate that passes its own known-bad
input is reported as broken and its green verdicts are not to be trusted.
"""
from __future__ import annotations

import json as _json
import os as _os

import dataclasses

from nopekit.modelio import load_path as _load_path


# ---------------------------------------------------------------------------
# SEALED FIXTURES
#
# A control must fire in EVERY project, not just a friendly one. Layering the
# known-bad values over the host project's projection looks safe — the override
# wins on every key it states — but gates resolve synonym families and derived
# quantities, so a key the fixture never mentions can still arrive from the
# project and neutralise the control.
#
# It was observed live in a sibling pack: a fixture raised a hull's centre of
# gravity to make it unstable, the host project happened to state a waterplane
# inertia (an honest thing to state, better than the pack's own fallback), and
# the gate PASSED ITS OWN KNOWN-BAD FIXTURE. A control whose severity depends on
# the host project's numbers passes in some repositories and fails in others,
# which is the same as having none.
#
# So the base here is the pack's OWN baseline.json, and nothing is inherited.
# `tests/test_packs.py::ControlsAreSealed` runs every control against an empty
# projection to keep it that way.
#
# SEALED IS NOT THE SAME AS HARDCODED. Sealing fixes WHICH projection the control
# is measured against; it does not license writing a literal magnitude into the
# fixture. A number that happens to beat the shipped threshold stops beating it
# the moment the threshold moves, and the control then passes silently — the
# identical failure, arrived at from the other side. So every fixture below reads
# the LIMIT IT MUST BEAT out of the baseline and derives its bad value from that,
# with the margin written down. `long_bridge` in bad_meshes.py has always done
# this (gap = 3 x max_bridge_mm); the rest of them do now.
# ---------------------------------------------------------------------------
_HERE = _os.path.dirname(_os.path.abspath(__file__))
_BASELINE_PATH = _os.path.join(_HERE, "baseline.json")

OVERSHOOT = 1.15
"""How far past its limit a control puts the quantity under test.

Enough that no rounding, no tessellation sliver and no 10% modelling refinement
can put it back inside — a 10.2% bead-area correction landed in this pack after
the fixtures were written and would have swallowed a 5% margin whole. Small
enough that the fixture still describes a part somebody could plausibly have
drawn, which is what separates a negative control from a corrupt input."""


def _baseline() -> dict:
    """The pack's own plausible-good projection, documentation keys stripped."""
    try:
        with open(_BASELINE_PATH, "r", encoding="utf-8") as handle:
            loaded = _json.load(handle)
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in loaded.items() if not k.startswith("_")}


def _helper(relpath: str):
    """Load a pack-local helper module by path, relative to this file.

    ``crawling_speed`` has to solve the gate's own time model for the speed that
    breaks the project's ceiling, and it must solve the SAME model the gate
    measures with — two copies of that arithmetic would drift and the control
    would slowly stop controlling anything (rule 2). By path, through the spine's
    ``load_path``, because the pack directory is only on ``sys.path`` while
    gates are being loaded, not when a fixture runs; and ``load_path`` names the
    module after its absolute path, which is how this fixture gets the module
    ``gates/printability.py`` holds and not another copy's. What slipped through
    when the name was fixed (``nopekit_pack_fdm_print__process_model``) and
    whatever ``sys.modules`` held under it was served: a second copy of this pack
    in the process got the first copy's model — caught in the gate, where the
    twin's print time ignored an edit to its own ``_process_model.py`` — and this
    fixture looked it up the same way (S-26, packs:H4).
    """
    return _load_path(_os.path.join(_HERE, relpath))


def _with(ctx, **overrides):
    """Copy the context with the projection overridden. Nothing else moves."""
    params = _baseline()
    params.update(overrides)
    return dataclasses.replace(ctx, params=params)


def brim_overflow(ctx):
    """fdm.bed_fit — a footprint that fits the raw bed and not the bed minus brim.

    This is the fixture that matters for this gate. Any bed check catches a part
    that is bigger than the bed; the one that costs a print is the part that
    measures comfortably inside the bed, gets a brim because it is a tall thin
    thing that would otherwise lift, and then fouls a bed clip four hours in.

    The footprint is the MIDPOINT between the usable area and the raw bed:

        usable = bed - 2 x brim   <   footprint = (usable + bed) / 2   <=   bed

    which is in character by construction — outside the brimmed area, inside the
    raw bed — for every brim and every bed, instead of only for the 5 mm brim and
    220 mm bed this pack happens to ship. The version that hardcoded ``bed - 6 mm``
    fired at brim 5.0 and passed at brim 3.0 and below, which is most of the
    profiles people actually run.

    A projection with no brim at all has no such footprint to find — with no brim
    the usable area IS the bed — so the fixture falls back to a part 5% wider than
    the bed and says so, which is still a real bed-fit failure.
    """
    base = _baseline()
    bed_x = float(base.get("bed_x_mm", base.get("bed_xy_mm", 220.0)))
    bed_y = float(base.get("bed_y_mm", bed_x))
    height = list(base.get("part_bbox_mm", base.get("bbox_mm", [100.0, 100.0, 40.0])))[2]
    brim = float(base.get("brim_mm", base.get("brim_allowance_mm", 5.0)))

    def overflowing(bed: float) -> float:
        usable = bed - 2.0 * brim
        return 0.5 * (usable + bed) if usable < bed else 1.05 * bed

    return _with(ctx, part_bbox_mm=[overflowing(bed_x), overflowing(bed_y), height])


def two_perimeter_wall(ctx):
    """fdm.min_wall — the thinnest section drops below three beads.

    Two perimeters fit, the third does not, and what is left between them is gap
    fill. It prints, it looks right, and it splits along that seam under load some
    weeks later.

    The section is set to ``2 / PERIMETERS_MIN`` of the limit — two beads where
    three are required — computed from the baseline's own line width rather than
    written down as 0.85 mm, so the fixture still describes a two-bead wall on a
    0.8 mm nozzle. The nozzle, the geometry and the process are otherwise
    unchanged.
    """
    base = _baseline()
    bead = float(base.get("extrusion_width_mm",
                          base.get("line_width_mm", base.get("nozzle_d_mm", 0.4))))
    return _with(ctx, part_min_wall_mm=round(2.0 * bead, 3))


def load_across_layers(ctx):
    """fdm.layer_alignment — the part is rotated so the load runs up the build axis.

    The change under test is the load direction relative to the layers: rotated
    onto the build axis, every bit of the load is carried by the welds between
    beads instead of by the beads. That is exactly the situation the gate exists
    for — a part that passed a stress gate computed on bulk coupon properties.

    The utilisation is **scaled, not carried over**, and this is deliberate. At
    phi = 90 the knockdown is exactly the layer-normal strength ratio, so the
    derated utilisation is ``util / ratio`` and the control only fires when the
    project's own utilisation happens to exceed that ratio: the version that
    carried 0.62 over untouched passed silently at any utilisation of 0.50 or
    less, which is most healthy designs. Setting it to ``OVERSHOOT x ratio`` puts
    the derated figure at exactly ``OVERSHOOT`` whatever the material ratio is.
    """
    base = _baseline()
    ratio = float(base.get("layer_normal_strength_ratio", 0.50))
    return _with(ctx, load_axis=[0.0, 0.0, 1.0], build_axis=[0.0, 0.0, 1.0],
                 utilisation=round(OVERSHOOT * ratio, 4), utilisation_kind="stress")


def crawling_speed(ctx):
    """fdm.print_time_est — the same part, printed slowly enough to blow the ceiling.

    Nothing about the geometry, the infill or the layer height changes. The speed
    is **solved** from the gate's own time model for the value that lands the part
    at ``OVERSHOOT`` times the project's ``max_print_time_h``, so the fixture is
    bad for this part and this ceiling rather than for the one the pack shipped.
    The version that hardcoded 6 mm/s fired on a 48 cm^3 part and passed on a
    bracket-sized 8 cm^3 one — the same gate, proven or not depending on what the
    project happened to be printing.

    The answer lands in the low single digits of mm/s, which is the real regime it
    describes: roughly where a bowden machine has to run to print TPU without the
    filament buckling in the tube.
    """
    base = _baseline()
    pm = _helper(_os.path.join(_os.pardir, "gates", "_process_model.py"))
    width = float(base.get("extrusion_width_mm", base.get("nozzle_d_mm", 0.4)))
    layer_h = float(base.get("layer_height_mm", 0.2))
    area = base.get("surface_area_mm2")
    extruded, _note = pm.extruded_volume_mm3(
        float(base.get("part_volume_mm3", 40000.0)),
        infill=float(base.get("infill_fraction", 0.20)),
        perimeters=int(base.get("perimeters", 3)),
        width=width,
        surface_area_mm2=None if area is None else float(area),
    )
    hours = OVERSHOOT * float(base.get("max_print_time_h", 24.0))
    speed = pm.speed_for_hours(extruded, hours, layer_h, width)
    return _with(ctx, print_speed_mm_s=round(speed, 3))


def metres_not_millimetres(ctx):
    """fdm.process_model_valid — the same part and machine, described in metres.

    One physically meaningful change: the unit. **Every** length in the projection
    is divided by 1000, areas by 1000^2 and volumes by 1000^3, which is what "the
    model is in metres" actually means — the earlier version rescaled only the
    bounding box while its docstring claimed everything else was untouched, and
    those two statements cannot both be true. A model genuinely in metres carries
    ``nozzle_d_mm = 0.0004`` as well, and rescaling it is what makes this fixture
    fire on any part at all: a 1.2 m enclosure panel is still inside the absolute
    extent floor after the 1000x shrink, but no nozzle is.

    This is the fixture that matters because nothing else in the pack notices — a
    part 0.08 long "fits any bed" and "bridges nothing", so every other gate goes
    on returning a confident green about an object 1000x smaller than the one
    anybody meant to print.
    """
    base = _baseline()
    # exponent on the 1/1000 factor: 1 for a length, 2 for an area, 3 for a volume
    dimension = {
        "part_bbox_mm": 1, "bbox_mm": 1, "bbox": 1, "footprint_mm": 1,
        "part_min_wall_mm": 1, "min_wall_mm": 1,
        "nozzle_d_mm": 1, "extrusion_width_mm": 1,
        "layer_height_mm": 1, "brim_mm": 1, "max_bridge_mm": 1,
        "max_cantilever_mm": 1, "bed_x_mm": 1, "bed_y_mm": 1, "bed_z_mm": 1,
        "print_speed_mm_s": 1,
        "surface_area_mm2": 2,
        "part_volume_mm3": 3, "volume_mm3": 3,
    }
    shrunk: dict = {}
    for key, power in dimension.items():
        if key not in base:
            continue
        factor = 1000.0 ** -power
        value = base[key]
        if isinstance(value, (list, tuple)):
            shrunk[key] = [float(v) * factor for v in value]
        else:
            shrunk[key] = float(value) * factor
    return _with(ctx, **shrunk)
