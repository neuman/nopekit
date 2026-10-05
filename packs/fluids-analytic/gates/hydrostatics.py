# SPDX-License-Identifier: Apache-2.0
"""Closed-form hydrostatics: does it float, how deep, and does it stay upright.

Four tier-0 gates. Pure arithmetic on the model projection, stdlib only, all of
them under a millisecond — which is the point. These are the questions you ask
on every edit, before anyone proposes installing a solver to answer them.

Each gate binds to the QUANTITY it settles, not to "fluids". A gate that
declared `claims=["fluids"]` would assert it can settle any fluid claim, and one
failing gate would then drag every hull claim to FAIL — a trim problem reading as
a drag problem sends the reader to the wrong file.

Fixtures live in `../selftest/bad_hydrostatics.py`. Each changes exactly ONE
physically meaningful quantity in the direction its gate cares about — mass up,
deck edge down, centre of gravity up — and each solves for the value that misses
the acceptance THE BASELINE STATES by a fixed ratio, so no control can be defused
by a later change to a threshold.
"""
from __future__ import annotations

import math

from nopekit.gates import gate, GateContext
from nopekit.models import NegativeControl, Tier, Verdict

from gates._fluids_analytic import (
    G, hydro, gm as resolve_gm, missing, number, skip,
    AWP_KEYS, BEAM_KEYS, DEPTH_KEYS, HULL_VOLUME_KEYS,
    # Three stability constants that used to be defined below and now live in the
    # shared module. `views/curves.py` has to stop the GZ chart at exactly the
    # angle this gate stops answering at, and draw exactly the acceptance line
    # this gate compares against — and a view module that imported a GATE module
    # to reach them would register this pack's gates a second time. So they live
    # beside the arithmetic, and both sides read one copy (rule 2).
    HEEL_KEYS, MIN_GZ_BEAM_FRACTION, SMALL_ANGLE_MAX_DEG,
)

# --------------------------------------------------------------------------- #
# Pack-level acceptance defaults. These are JUDGEMENTS, not physics, and every
# one of them is overridable by a model key. They exist so that a project which
# has not yet argued about its margins still gets a gate that can fail, rather
# than a gate that certifies "it floats, technically".
# --------------------------------------------------------------------------- #

#: Fraction of the watertight volume that may be consumed at rest. 1.0 means the
#: hull floats exactly awash with zero reserve — a state from which any wave,
#: any rain and any leak is terminal. 0.90 leaves a tenth of the volume as
#: reserve buoyancy. Override with `max_volume_fraction`.
#:
#: It is a VOLUME fraction and the name says so. `max_draft_fraction` is kept as
#: a deprecated synonym, but the two are only the same number on the prismatic
#: path where V_hull = Awp.depth. On a stated non-prismatic volume they diverge
#: with no tag: a V-bottom hull reporting 0.878 here can be floating at 0.37 of
#: its depth, so a project that set 0.5 meaning "never immerse more than half the
#: hull" was getting a limit on volume instead. Draft against depth is
#: fluid.freeboard's measurement, and that is the gate to bind such a claim to.
MAX_DRAFT_FRACTION = 0.90

#: Minimum freeboard as a fraction of hull depth, when the project has not
#: stated an absolute one. Scale-free on purpose: an absolute default in metres
#: is either absurd on a 300 mm float or negligent on a 6 m boat. Override with
#: `min_freeboard_m`.
MIN_FREEBOARD_FRACTION = 0.15

#: Minimum GM as a fraction of waterline beam, when the project has not stated
#: an absolute one. A GM of +1 mm is positive and meaningless: the model's KG is
#: not known to a millimetre, free surface has not been subtracted, and the crew
#: moves. Override with `min_gm_m`.
MIN_GM_BEAM_FRACTION = 0.05


@gate(
    id="fluid.buoyancy",
    title="Displaced mass exceeds total mass, with reserve buoyancy left over",
    claims=["buoyancy", "flotation", "displacement"],
    tier=Tier.INSTANT,
    settles="displaced volume fraction",
    negative_control=NegativeControl(
        fixture="selftest/bad_hydrostatics.py:overloaded",
        note="the same hull loaded until flotation consumes 1.15x the volume fraction the "
             "baseline allows itself; Archimedes is linear in mass so nothing about the "
             "geometry moves, and the mass is solved from that stated limit rather than typed",
    ),
)
def buoyancy(ctx: GateContext) -> Verdict:
    """Archimedes, with the reserve stated.

    A floating body displaces its own mass of fluid. The question a gate can
    settle is not "does it float" but "what fraction of the watertight volume
    does floating already consume" — because the answer 0.98 is a pass on the
    first question and a disaster on the water.
    """
    h = hydro(ctx, "fluid.buoyancy")
    if isinstance(h, Verdict):
        return h
    if h.v_hull is None or h.v_hull <= 0:
        return skip("fluid.buoyancy",
                    f"model provides no watertight volume: {missing(HULL_VOLUME_KEYS)} "
                    f"(m3), or {missing(AWP_KEYS)} with {missing(DEPTH_KEYS)} for the "
                    f"prismatic approximation")

    limit = number(ctx, ("max_volume_fraction", "max_draft_fraction"), MAX_DRAFT_FRACTION)
    fraction = h.v_req / h.v_hull
    verdict_word = "SINKS" if fraction > 1.0 else "floats"
    return Verdict(
        gate="fluid.buoyancy",
        passed=fraction <= limit,
        measured=round(fraction, 4),
        limit=round(float(limit), 4), comparator="<=",
        units="V_displaced/V_hull",
        detail=f"{verdict_word}: {h.mass:.1f} kg needs {h.v_req:.4f} m3 of the "
               f"{h.v_hull:.4f} m3 watertight volume at rho {h.rho:.0f} kg/m3 -> volume "
               f"fraction {fraction:.3f} vs {float(limit):.2f} limit "
               f"({(1 - fraction) * h.v_hull:.4f} m3 reserve buoyancy); this is a VOLUME "
               f"fraction, not a draft fraction - see fluid.freeboard for draft {h.tag}".strip(),
    )


@gate(
    id="fluid.freeboard",
    title="Freeboard remaining at the loaded waterline",
    claims=["freeboard", "reserve-buoyancy"],
    tier=Tier.INSTANT,
    settles="freeboard at load",
    negative_control=NegativeControl(
        fixture="selftest/bad_hydrostatics.py:low_deck_edge",
        note="the same hull and the same load with the deck edge dropped until freeboard "
             "is 1/1.15 of the stated minimum; mass, waterplane and draft are untouched "
             "and the watertight volume is re-derived from the new depth so the box stays "
             "coherent - only the distance the water has to climb changes",
    ),
)
def freeboard(ctx: GateContext) -> Verdict:
    """Deck-edge height above the loaded waterline.

    Freeboard is what turns a wave into spray instead of into cargo. It is a
    separate claim from buoyancy because the two fail independently: a hull can
    have generous reserve volume in a tall narrow shape and still ship water over
    a low gunwale, and a hull with plenty of freeboard can still be loaded past
    its volume.

    Draft is taken from the model if stated. Otherwise it is displaced volume
    over waterplane area, which assumes the hull is wall-sided at the waterline —
    true for a box, a pontoon or a tube, optimistic for flared sides and
    pessimistic for tumblehome. The verdict says which path ran.
    """
    h = hydro(ctx, "fluid.freeboard")
    if isinstance(h, Verdict):
        return h
    if h.depth is None or h.depth <= 0:
        return skip("fluid.freeboard",
                    f"model provides no hull depth to the deck edge: {missing(DEPTH_KEYS)} "
                    f"(m, from the same datum the draft is measured to)")
    if h.draft is None:
        return skip("fluid.freeboard",
                    f"model provides no draft and none can be derived: "
                    f"{missing(('draft_m', 'draught_m'))}, or {missing(AWP_KEYS)} to get "
                    f"it from displaced volume")

    fb = h.depth - h.draft
    stated = number(ctx, ("min_freeboard_m", "minimum_freeboard_m"), None)
    if stated is not None:
        limit, basis = float(stated), "stated min_freeboard_m"
    else:
        limit = MIN_FREEBOARD_FRACTION * h.depth
        basis = f"{MIN_FREEBOARD_FRACTION:.0%} of {h.depth:.3f} m depth (pack default)"
    return Verdict(
        gate="fluid.freeboard",
        passed=fb >= limit,
        measured=round(fb, 4),
        limit=round(limit, 4), comparator=">=",
        units="m",
        detail=f"freeboard {fb:.3f} m = depth {h.depth:.3f} - draft {h.draft:.3f} at "
               f"{h.mass:.1f} kg, vs {limit:.3f} m minimum [{basis}] {h.tag}".strip(),
    )


@gate(
    id="fluid.metacentric",
    title="Initial metacentric height GM is positive with a usable margin",
    claims=["stability", "metacentric-height"],
    tier=Tier.INSTANT,
    settles="metacentric height",
    negative_control=NegativeControl(
        fixture="selftest/bad_hydrostatics.py:top_heavy",
        note="the same hull with mast-head equipment raising KG until GM is 1/1.15 of the "
             "stated minimum; geometry, mass and waterplane are untouched, so BM is "
             "identical and only the KG term moves",
    ),
)
def metacentric(ctx: GateContext) -> Verdict:
    """GM = BM - BG, with BM = I_waterplane / displaced volume.

    **Positive GM is necessary and NOT sufficient.** It says the hull develops a
    righting moment for an infinitesimal heel. It says nothing about what happens
    at 20 degrees, nothing about the angle at which the deck edge immerses and the
    righting arm collapses, nothing about a wave that removes the waterplane
    entirely as it passes, and nothing about free liquid inside the hull, which
    subtracts i/V from GM every time it sloshes. A design that passes this gate
    and nothing else is a design whose stability has been checked at exactly one
    angle: zero.

    The waterplane inertia is taken from the model when present. The fallback
    I = A_wp.B^2/12 is the RECTANGULAR waterplane, which overstates I for any
    hull with fine ends — an optimistic error, so the verdict flags it.
    """
    h = hydro(ctx, "fluid.metacentric")
    if isinstance(h, Verdict):
        return h
    resolved = resolve_gm(ctx, h, "fluid.metacentric")
    if isinstance(resolved, Verdict):
        return resolved
    gm_m, bm, bg, notes = resolved

    stated = number(ctx, ("min_gm_m", "minimum_gm_m"), None)
    if stated is not None:
        limit, basis = float(stated), "stated min_gm_m"
    elif h.beam:
        limit = MIN_GM_BEAM_FRACTION * h.beam
        basis = f"{MIN_GM_BEAM_FRACTION:.0%} of {h.beam:.3f} m beam (pack default)"
    else:
        limit, basis = 0.0, "SIGN ONLY - no beam and no min_gm_m, so no margin is checked"
    tags = ", ".join(h.notes + notes)
    return Verdict(
        gate="fluid.metacentric",
        passed=gm_m >= limit and gm_m > 0.0,
        measured=round(gm_m, 4),
        limit=round(limit, 4), comparator=">=",
        units="m",
        detail=f"GM {gm_m:.3f} m = BM {bm:.3f} - BG {bg:.3f}, vs {limit:.3f} m "
               f"[{basis}]; positive GM is necessary NOT sufficient - it is the slope at "
               f"0 deg only" + (f" [{tags}]" if tags else ""),
    )


@gate(
    id="fluid.righting_arm",
    title="Righting arm GZ at the stated heel meets the demand on it",
    claims=["righting-arm", "stability-margin"],
    tier=Tier.INSTANT,
    settles="righting arm at heel",
    negative_control=NegativeControl(
        fixture="selftest/bad_hydrostatics.py:masthead_load",
        note="KG raised until the arm the hull produces is 1/1.15 of the arm an UNCHANGED "
             "heeling moment demands; the demand side is bit-identical to the baseline, so "
             "unlike a fixture that scales the moment this one falsifies GZ = GM.sin(theta) "
             "itself and not just the float comparison around it",
    ),
)
def righting_arm(ctx: GateContext) -> Verdict:
    """GZ = GM.sin(theta), and the angle past which that sentence is false.

    The small-angle righting arm is the initial slope of the real GZ curve. It is
    a good approximation while the waterplane has not moved much — conventionally
    to about 10 degrees, and that is the ceiling this gate enforces. Asked for a
    heel beyond it, the gate SKIPS rather than answering: past that angle the
    immersed shape changes, the deck edge may enter the water, and GM.sin(theta)
    over-predicts the arm the hull actually develops. Reporting a comfortable
    number there is precisely the silent error this pack exists to refuse.

    The limit comes from the most specific source available: a stated minimum
    arm, else the arm implied by a stated heeling moment, else a fraction of
    beam. The verdict says which.
    """
    h = hydro(ctx, "fluid.righting_arm")
    if isinstance(h, Verdict):
        return h
    resolved = resolve_gm(ctx, h, "fluid.righting_arm")
    if isinstance(resolved, Verdict):
        return resolved
    gm_m, bm, _bg, notes = resolved

    heel = number(ctx, HEEL_KEYS, None)
    if heel is None:
        return skip("fluid.righting_arm",
                    f"model states no heel angle: {missing(HEEL_KEYS)} (deg, at or under "
                    f"{SMALL_ANGLE_MAX_DEG:.0f}). GZ is a function OF the angle, so a "
                    f"default here would be the gate choosing the load case - and the "
                    f"flattering end of it, since GM.sin(theta) grows all the way to the "
                    f"small-angle ceiling")
    heel = float(heel)
    if heel <= 0:
        return skip("fluid.righting_arm",
                    f"heel angle {heel} deg is not a heel — set heel_angle_deg > 0")
    if heel > SMALL_ANGLE_MAX_DEG:
        return skip("fluid.righting_arm",
                    f"heel_angle_deg {heel:.1f} is past the {SMALL_ANGLE_MAX_DEG:.0f} deg "
                    f"small-angle ceiling: GZ=GM.sin(theta) over-predicts once the "
                    f"waterplane moves, so this needs a full GZ curve from hydrostatics "
                    f"at each angle (tier 2), not a closed form")

    gz = gm_m * math.sin(math.radians(heel))
    weight = h.mass * G
    righting_moment = weight * gz

    heeling_moment = number(ctx, ("heeling_moment_nm", "heeling_moment_n_m",
                                  "upsetting_moment_nm"), None)
    stated = number(ctx, ("min_righting_arm_m", "min_gz_m"), None)
    if stated is not None:
        limit, basis = float(stated), "stated min_righting_arm_m"
    elif heeling_moment is not None and heeling_moment > 0:
        limit = float(heeling_moment) / weight
        basis = f"arm demanded by a {float(heeling_moment):.0f} N.m heeling moment"
    elif h.beam:
        limit = MIN_GZ_BEAM_FRACTION * h.beam
        basis = f"{MIN_GZ_BEAM_FRACTION:.0%} of {h.beam:.3f} m beam (pack default)"
    else:
        return skip("fluid.righting_arm",
                    f"nothing to compare GZ against: give min_righting_arm_m, or "
                    f"heeling_moment_nm, or {missing(BEAM_KEYS)} for the default margin")

    tags = ", ".join(h.notes + notes)
    return Verdict(
        gate="fluid.righting_arm",
        passed=gz >= limit,
        measured=round(gz, 4),
        limit=round(limit, 4), comparator=">=",
        units="m",
        detail=f"GZ {gz:.4f} m at {heel:.1f} deg (GM {gm_m:.3f}, BM {bm:.3f}), righting "
               f"moment {righting_moment:.0f} N.m vs demand {limit * weight:.0f} N.m; "
               f"limit {limit:.4f} m [{basis}]; small-angle void past "
               f"{SMALL_ANGLE_MAX_DEG:.0f} deg" + (f" [{tags}]" if tags else ""),
    )
