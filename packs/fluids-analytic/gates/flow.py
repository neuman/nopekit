# SPDX-License-Identifier: Apache-2.0
"""Closed-form flow: drag on a body, pressure drop in a line, and the regime guard.

Three tier-0 gates, stdlib only. The third one polices the first two.

The silent error this module exists to stop is a **correlation used outside the
range it was fitted over**. A drag coefficient is not a property of a shape; it
is a property of a shape AT A REYNOLDS NUMBER. Cd = 0.47 for a sphere is true
from about Re 1e3 to 2e5 and then falls by a factor of three through the drag
crisis. A laminar friction factor evaluated at Re 1e6 is wrong by an order of
magnitude, returns a plausible-looking float, and nothing complains. So
`fluid.drag` refuses to use a table Cd outside its band, and `fluid.flow_regime`
fails outright when the flow sits in the transitional gap where neither pipe
friction law holds, when the relative roughness is past the Moody chart's 0.05
ceiling, or when the friction factor that comes out is outside the band a Darcy f
can physically occupy.

The second silent error is a quantity borrowed from the wrong system. This pack
ships hull gates and pipe gates in one manifest, so a project stating both a
cruise speed and a bore is the normal case — and `fluid.pipe_pressure_drop` will
NOT read the former as the latter's bulk velocity. It skips and names the keys.
The third is a sign: a stated elevation lift is a DEMAND on head and is
subtracted from what the pump supplies, never added to the friction budget.

Fixtures live in `../selftest/bad_flow.py`.
"""
from __future__ import annotations

import math

from atompipe.gates import gate, GateContext
from atompipe.models import NegativeControl, Tier, Verdict

from gates._fluids_analytic import (
    F_TURBULENT_MAX, F_TURBULENT_MIN, G, REL_ROUGHNESS_MAX, RE_LAMINAR_MAX,
    RE_TURBULENT_MIN, eng, fluid, friction_factor, missing, number, regime_name,
    skip, text,
)

# --------------------------------------------------------------------------- #
# accepted key spellings
# --------------------------------------------------------------------------- #
VEL_KEYS = ("flow_velocity_m_s", "velocity_m_s", "speed_m_s",
            "free_stream_velocity_m_s", "cruise_speed_m_s", "u_m_s")
AREA_KEYS = ("frontal_area_m2", "reference_area_m2", "a_frontal_m2",
             "projected_area_m2")
CD_KEYS = ("drag_coefficient", "cd", "cd_frontal")
SHAPE_KEYS = ("drag_shape_class", "shape_class", "drag_shape")
LCHAR_KEYS = ("characteristic_length_m", "reference_length_m", "body_diameter_m",
              "diameter_m", "chord_m")
DRAG_BUDGET_KEYS = ("max_drag_force_n", "drag_budget_n", "thrust_available_n",
                    "allowable_drag_n")

PIPE_D_KEYS = ("pipe_diameter_m", "inner_diameter_m", "bore_m",
               "hydraulic_diameter_m", "duct_diameter_m", "pipe_id_m")
PIPE_L_KEYS = ("pipe_length_m", "duct_length_m", "run_length_m", "line_length_m")
ROUGHNESS_KEYS = ("pipe_roughness_m", "absolute_roughness_m", "roughness_m",
                  "epsilon_m")
FLOW_KEYS = ("flow_rate_m3_s", "volumetric_flow_m3_s", "q_m3_s", "discharge_m3_s")
PIPE_V_KEYS = ("pipe_velocity_m_s", "bulk_velocity_m_s", "mean_velocity_m_s")
K_KEYS = ("minor_loss_k_total", "k_total", "sum_k", "minor_k", "fittings_k_total")
DP_BUDGET_KEYS = ("max_pressure_drop_pa", "pressure_budget_pa",
                  "allowable_pressure_drop_pa", "available_head_pa")

#: Head the system SUPPLIES. A pump curve point, or the surface-to-surface drop
#: of a gravity feed. This is the only family that may be multiplied by rho.g
#: into a friction budget.
HEAD_KEYS = ("available_head_m", "pump_head_m", "delivered_head_m")

#: Head the system SPENDS on elevation before any friction is paid — the height
#: the liquid has to be lifted between the two free surfaces. It is a DEMAND, and
#: it is SUBTRACTED from the supplied head.
#:
#: `static_head_m` lives here, not in HEAD_KEYS, and that is the one judgement in
#: this module worth arguing with. In pump and irrigation practice "static head"
#: is unambiguously the elevation the pump works against (total head = static +
#: friction), so reading it as an available supply inverts the sign of a real
#: quantity and silently INVENTS allowance: a 5 m lift became 49 kPa of friction
#: budget. Sign convention: POSITIVE lifts (the outlet is above the source) and
#: is subtracted; a NEGATIVE value is a downhill run and is added back, which is
#: the same equation and needs no special case.
STATIC_LIFT_KEYS = ("static_lift_m", "elevation_head_m", "static_head_m",
                    "elevation_rise_m", "lift_m")


# --------------------------------------------------------------------------- #
# drag coefficients, each WITH the Reynolds band it was measured over
# --------------------------------------------------------------------------- #
#: name -> (Cd, Re_lo, Re_hi, reference area, note)
#:
#: The Re band is not decoration. Sharp-edged bluff bodies separate at their
#: edges, so their Cd is nearly Re-independent over decades; rounded bodies move
#: their separation point with Re and their Cd is not. Mixing the two up is how
#: a sphere gets designed with three times the drag it will have, or a third of
#: it. Full table with sources and caveats: references/drag_coefficients.md
#:
#: The reference-area column is the OTHER thing that is not decoration, and it is
#: not uniformly "frontal". Every row but one is the projected area seen from
#: upstream. `cube_edge_on` is the exception and it is called out in its own note:
#: the published value is referenced to the cube's plain face a^2, while the true
#: projected area of a cube yawed 45 deg is sqrt(2).a^2. A reader who follows the
#: blanket frontal-area rule there computes 41% too much drag — the exact
#: factor-of-several, no-symptom error this column exists to prevent.
DRAG_TABLE: dict[str, tuple[float, float, float, str, str]] = {
    "sphere":                 (0.47, 1e3, 2e5, "pi.D^2/4",  "subcritical; drag crisis near Re 3e5"),
    "sphere_supercritical":   (0.20, 4e5, 1e6, "pi.D^2/4",  "post-crisis, turbulent boundary layer"),
    "cylinder_cross":         (1.20, 1e4, 2e5, "D.L",       "long circular cylinder, flow across the axis"),
    # 0.70, not 0.80. Finite-length circular cylinders lose Cd to end relief
    # roughly as Hoerner's end-effect factor: L/D 1 -> ~0.63, 2 -> ~0.68,
    # 5 -> ~0.74, 10 -> ~0.82, 40 -> ~0.98, infinite -> 1.20 (Cengel & Cimbala,
    # Fluid Mechanics, Table 11-2; Hoerner, Fluid-Dynamic Drag, ch. 3). The 0.80
    # this row shipped with is the L/D ~ 10 value carrying an "L/D about 2"
    # label, ~15% high against a table that declares itself +/-10% at best.
    # Rejected alternative: keep 0.80 and relabel the row L/D ~ 10 — a short
    # strut with free ends is the common case a reader reaches for, so the
    # geometry stays and the number moves.
    "cylinder_cross_short":   (0.70, 1e4, 2e5, "D.L",       "L/D about 2, free ends relieve the wake"),
    "square_cylinder":        (2.05, 1e4, 1e6, "D.L",       "sharp-edged square rod, face-on; Re-insensitive"),
    "flat_plate_normal":      (1.17, 1e4, 1e7, "plate area", "square or round plate normal to the flow"),
    "disk_normal":            (1.17, 1e4, 1e7, "pi.D^2/4",  "same separation as the square plate"),
    "cube_face_on":           (1.05, 1e4, 1e6, "face area", "sharp-edged, Re-insensitive"),
    "cube_edge_on":           (0.80, 1e4, 1e6, "face area a^2 NOT frontal",
                               "rotated 45 deg about the vertical; A is the PLAIN FACE a^2, "
                               "not the sqrt(2).a^2 the flow actually sees"),
    "hemisphere_open_upstream": (1.42, 1e4, 1e6, "pi.D^2/4", "cup facing the flow, as on an anemometer"),
    "hemisphere_open_downstream": (0.38, 1e4, 1e6, "pi.D^2/4", "dome facing the flow"),
    "streamlined_strut":      (0.10, 1e5, 1e7, "t.L frontal", "2D fairing, thickness/chord about 0.25"),
    "streamlined_body":       (0.05, 1e5, 1e7, "max frontal", "body of revolution, fineness about 4:1"),
}


def _pipe_velocity(ctx: GateContext, diameter: float) -> tuple[float, str] | None:
    """Bulk velocity IN THE LINE, and where it came from. None if unstated.

    Volumetric flow is preferred over a stated velocity because flow is what is
    conserved along a line while velocity is a consequence of the bore — and the
    bore is the thing that changes when somebody substitutes a fitting.

    **VEL_KEYS is deliberately not in this chain, and that is the point of the
    function.** The generic external-flow spellings (`cruise_speed_m_s`,
    `free_stream_velocity_m_s`, `speed_m_s`, `flow_velocity_m_s`) describe the
    stream the BODY moves through. This pack ships hull gates and pipe gates in
    one manifest, so a project stating both is the normal case, not an edge one —
    and a fallback that reached across would read a 6 m/s cruise speed as 6 m/s
    in a 10 mm cooling line and return a confident 147 kPa for a flow nobody ever
    specified. That was the behaviour here and it was a FALSE PASS: not a wrong
    number the reader could see, a right-looking number about the wrong system.
    Mixing an external free-stream velocity into an internal bulk velocity is
    exactly the silent cross-domain error this module exists to refuse, so the
    gate now SKIPS and names the two key families it will accept.
    """
    area = math.pi * diameter * diameter / 4.0
    q = number(ctx, FLOW_KEYS, None)
    if q is not None and q > 0:
        return q / area, f"v=Q/A from {q * 1000:.3f} L/s"
    v = number(ctx, PIPE_V_KEYS, None)
    if v is not None and v > 0:
        return v, "stated bulk velocity"
    return None


@gate(
    id="fluid.drag",
    title="Drag force at speed, against the available thrust budget",
    claims=["drag", "resistance"],
    tier=Tier.INSTANT,
    settles="drag force",
    # Prerequisite fluid.flow_regime (P2.2-D12): it checks the Reynolds and roughness
    #    bands this gate's correlation was fitted in; outside them the arithmetic
    #    still returns a confident number, and it is wrong.
    #    Isolated: the guard passes this gate's own known-bad control
    #    (test_packs.ControlsAreIsolated).
    needs=["fluid.flow_regime"],
    negative_control=NegativeControl(
        fixture="selftest/bad_flow.py:oversped",
        note="the same body at 3x the speed: drag goes as v^2 so this is ~9x the force, "
             "and the speed was chosen to keep Reynolds inside the same Cd band so the "
             "gate fails on the force it measures rather than skipping on validity",
    ),
)
def drag(ctx: GateContext) -> Verdict:
    """F = 1/2 . rho . v^2 . Cd . A, with the Reynolds number and regime reported.

    Cd comes from the model when it states one, and otherwise from this pack's
    shape-class table. When it comes from the table, the gate checks the Reynolds
    number against the band that Cd was measured over and SKIPS if it is outside
    — quoting a Cd outside its regime is the classic silent error in this domain
    and it is worth a visible BLOCKED rather than a confident wrong number.

    The reference area must be the one the Cd was defined against. That is frontal
    area for every entry in this pack's table EXCEPT `cube_edge_on`, whose
    published value is referenced to the cube's plain face a^2 and not to the
    sqrt(2).a^2 a yawed cube presents — the row's own note says so, and following
    the blanket rule there overstates drag by 41%. A Cd taken from an automotive
    or aerofoil source is usually referenced to planform or wetted area instead,
    and using it with a frontal area is a factor-of-several error with no symptom.
    """
    got = fluid(ctx, "fluid.drag")
    if isinstance(got, Verdict):
        return got
    rho, nu, nu_src = got

    v = number(ctx, VEL_KEYS, None)
    if v is None or v <= 0:
        return skip("fluid.drag", f"model provides no flow velocity: {missing(VEL_KEYS)} (m/s)")
    area = number(ctx, AREA_KEYS, None)
    if area is None or area <= 0:
        return skip("fluid.drag", f"model provides no reference area: {missing(AREA_KEYS)} "
                                  f"(m2, frontal - the area the Cd is defined against)")
    lchar = number(ctx, LCHAR_KEYS, None)
    if lchar is None or lchar <= 0:
        return skip("fluid.drag",
                    f"model provides no characteristic length: {missing(LCHAR_KEYS)} (m). "
                    f"Without it there is no Reynolds number, and a Cd with no Reynolds "
                    f"number is a number with no regime")

    re = v * lchar / nu
    cd = number(ctx, CD_KEYS, None)
    shape = text(ctx, SHAPE_KEYS)
    if cd is not None and cd > 0:
        source = "model-supplied Cd (its regime is the model's to defend)"
    elif shape:
        entry = DRAG_TABLE.get(shape)
        if entry is None:
            return skip("fluid.drag",
                        f"drag_shape_class {shape!r} is not in this pack's table. Known: "
                        f"{', '.join(sorted(DRAG_TABLE))} — or state drag_coefficient directly")
        cd, re_lo, re_hi, ref_area, note = entry
        if not (re_lo <= re <= re_hi):
            return skip("fluid.drag",
                        f"Cd {cd} for {shape!r} is tabulated for Re {eng(re_lo)}-{eng(re_hi)} "
                        f"and this flow is at Re {eng(re)} ({regime_name(re)}) — using it "
                        f"here would be a silent error, so nothing is settled. Supply a "
                        f"drag_coefficient measured at this Re, or change the speed/size")
        source = f"table {shape} (Re {eng(re_lo)}-{eng(re_hi)}, ref {ref_area}; {note})"
    else:
        return skip("fluid.drag", f"model provides neither {missing(CD_KEYS)} nor "
                                  f"{missing(SHAPE_KEYS)} (one of: {', '.join(sorted(DRAG_TABLE))})")

    force = 0.5 * rho * v * v * cd * area
    budget = number(ctx, DRAG_BUDGET_KEYS, None)
    if budget is None or budget <= 0:
        return skip("fluid.drag",
                    f"drag computes to {force:.2f} N but there is nothing to compare it "
                    f"against: {missing(DRAG_BUDGET_KEYS)} (N). A measurement with no "
                    f"acceptance settles no claim")

    return Verdict(
        gate="fluid.drag",
        passed=force <= float(budget),
        measured=round(force, 4),
        limit=round(float(budget), 4),
        units="N",
        detail=f"drag {force:.2f} N at {v:.2f} m/s vs {float(budget):.2f} N budget "
               f"(Cd {cd:.2f}, A {area:.4f} m2, rho {rho:.0f}); Re {eng(re)} "
               f"{regime_name(re)} on L {lchar:.3f} m, nu from {nu_src}; {source}",
    )


@gate(
    id="fluid.pipe_pressure_drop",
    title="Pressure drop along the line, major plus minor, against the head available",
    claims=["pressure-drop", "head-loss", "pipe-flow"],
    tier=Tier.INSTANT,
    settles="pipe pressure drop",
    # Prerequisite fluid.flow_regime (P2.2-D12): it checks the Reynolds and roughness
    #    bands this gate's correlation was fitted in; outside them the arithmetic
    #    still returns a confident number, and it is wrong.
    #    Isolated: the guard passes this gate's own known-bad control
    #    (test_packs.ControlsAreIsolated).
    needs=["fluid.flow_regime"],
    negative_control=NegativeControl(
        fixture="selftest/bad_flow.py:pinched_bore",
        note="the same flow rate through a bore reduced to 60%: at fixed Q the drop goes "
             "roughly as 1/D^5, so this is about 12x, and nothing else in the line "
             "changes - same length, same roughness, same fittings, same head, same lift",
    ),
)
def pipe_pressure_drop(ctx: GateContext) -> Verdict:
    """Darcy-Weisbach with Colebrook-White, plus minor losses by K-factor.

    dp = ( f.L/D + sum(K) ) . rho.v^2/2

    f is 64/Re below Re 2300, where that is exact rather than fitted, and the
    iterated Colebrook-White solution above it, seeded from Swamee-Jain. The
    iteration trace goes to an evidence file — an iterative solve whose
    convergence is not recorded is a number with no provenance, even when the
    loop is ten lines long.

    Minor losses are whatever K total the model declares. If it declares none the
    gate proceeds with zero and SAYS SO in its one line, because bends, valves,
    strainers and entries routinely outweigh the straight run in a short system,
    and a silent zero there is the difference between a pump that works and one
    that does not.

    **The budget side has a sign convention and it is the half people get wrong.**
    Head SUPPLIED (`available_head_m`, `pump_head_m`) becomes rho.g.h of friction
    allowance. Head SPENT on elevation (`static_lift_m`, `elevation_head_m`,
    `static_head_m`) is subtracted from it first, because a pump lifting 8 m with
    a 10 m curve point has 2 m for the pipe and not 10. Stating only a lift
    settles nothing and the gate skips: an elevation is a demand, and a demand is
    not an allowance. What this gate still does NOT do is find the duty point —
    it takes the head you state as the head at this flow, and a real pump curve
    falls as flow rises. See references/what_this_pack_cannot_tell_you.md.
    """
    got = fluid(ctx, "fluid.pipe_pressure_drop")
    if isinstance(got, Verdict):
        return got
    rho, nu, _nu_src = got

    d = number(ctx, PIPE_D_KEYS, None)
    if d is None or d <= 0:
        return skip("fluid.pipe_pressure_drop",
                    f"model provides no bore: {missing(PIPE_D_KEYS)} (m, INTERNAL "
                    f"diameter - nominal pipe size is not a bore)")
    length = number(ctx, PIPE_L_KEYS, None)
    if length is None or length <= 0:
        return skip("fluid.pipe_pressure_drop",
                    f"model provides no run length: {missing(PIPE_L_KEYS)} (m)")
    eps = number(ctx, ROUGHNESS_KEYS, None)
    if eps is None or eps < 0:
        return skip("fluid.pipe_pressure_drop",
                    f"model provides no absolute roughness: {missing(ROUGHNESS_KEYS)} "
                    f"(m - NOT mm; 1.5e-6 drawn plastic, 4.5e-5 commercial steel; "
                    f"see references/pipe_roughness.md)")
    resolved = _pipe_velocity(ctx, d)
    if resolved is None:
        return skip("fluid.pipe_pressure_drop",
                    f"model provides no flow IN THIS LINE: {missing(FLOW_KEYS)} (m3/s) "
                    f"or {missing(PIPE_V_KEYS)} (m/s). A free-stream or cruise velocity "
                    f"is deliberately NOT accepted here — it is the speed of the body "
                    f"through the fluid, not the bulk velocity in the bore")
    v, v_src = resolved

    k_total = number(ctx, K_KEYS, None)
    k_note = ""
    if k_total is None:
        k_total, k_note = 0.0, " NO minor-loss K declared (minor_loss_k_total): bends, " \
                               "valves and entries are NOT counted"
    k_total = float(k_total)

    re = v * d / nu
    rel = eps / d
    f, law, trace = friction_factor(re, rel)
    dyn = 0.5 * rho * v * v
    dp_major = f * (length / d) * dyn
    dp_minor = k_total * dyn
    dp = dp_major + dp_minor

    # -- what is left for friction, after elevation has been paid ---------- #
    # An explicit pressure budget in Pa is taken as already being the FRICTION
    # allowance — it is stated against this line, so subtracting a lift from it
    # would charge the elevation twice. A head in metres is the head at the pump
    # or at the upper free surface, and the static lift comes out of it first.
    budget = number(ctx, DP_BUDGET_KEYS, None)
    head_basis = ""
    lift = number(ctx, STATIC_LIFT_KEYS, None)
    if budget is None:
        head = number(ctx, HEAD_KEYS, None)
        if head is not None and head > 0:
            net = float(head) - (float(lift) if lift is not None else 0.0)
            if net <= 0:
                return skip("fluid.pipe_pressure_drop",
                            f"the {float(head):.2f} m of head available is entirely spent "
                            f"lifting the liquid {float(lift):.2f} m ({missing(STATIC_LIFT_KEYS)}): "
                            f"{net:.2f} m is left for friction, so there is no budget to "
                            f"compare {dp / 1000:.2f} kPa of loss against. This is a duty-point "
                            f"failure, not a pipe-sizing result - raise the head or lower the lift")
            budget = rho * G * net
            head_basis = (f" [from {float(head):.2f} m of head"
                          + (f" less {float(lift):.2f} m static lift" if lift is not None else "")
                          + f" = {net:.2f} m for friction]")
    elif lift is not None:
        head_basis = (f" [stated Pa budget taken as the friction allowance; the "
                      f"{float(lift):.2f} m static lift is assumed already deducted]")
    if budget is None or budget <= 0:
        return skip("fluid.pipe_pressure_drop",
                    f"dp computes to {dp / 1000:.2f} kPa but there is nothing to compare "
                    f"it against: {missing(DP_BUDGET_KEYS)} (Pa) or {missing(HEAD_KEYS)} (m). "
                    f"An elevation lift ({missing(STATIC_LIFT_KEYS)}) is a DEMAND on head, "
                    f"not a supply of it, and on its own it settles nothing")

    path = ctx.out_path("fluid.pipe_pressure_drop.txt")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("Darcy-Weisbach with Colebrook-White\n")
        fh.write("===================================\n")
        fh.write(f"bore D            {d:.6f} m\n")
        fh.write(f"run length L      {length:.4f} m   (L/D = {length / d:.1f})\n")
        fh.write(f"roughness e       {eps:.3e} m   (e/D = {rel:.3e})\n")
        fh.write(f"density rho       {rho:.2f} kg/m3\n")
        fh.write(f"kinematic visc nu {nu:.4e} m2/s\n")
        fh.write(f"bulk velocity v   {v:.6f} m/s   ({v_src})\n")
        fh.write(f"Reynolds Re       {re:.1f}   ({regime_name(re)})\n")
        fh.write(f"friction factor f {f:.8f}   ({law})\n\n")
        fh.write("iteration trace\n---------------\n")
        fh.write("\n".join(trace) + "\n\n")
        fh.write("losses\n------\n")
        fh.write(f"dynamic pressure  {dyn:.3f} Pa\n")
        fh.write(f"major  f.L/D.q    {dp_major:.3f} Pa\n")
        fh.write(f"minor  sum(K).q   {dp_minor:.3f} Pa   (sum K = {k_total:.3f})\n")
        fh.write(f"TOTAL             {dp:.3f} Pa   ({dp / 1000:.4f} kPa, "
                 f"{dp / (rho * G):.4f} m of head)\n")
        if lift is not None:
            fh.write(f"static lift       {float(lift):.4f} m   (elevation DEMAND, "
                     f"subtracted from supplied head)\n")
        fh.write(f"budget            {float(budget):.3f} Pa   "
                 f"({float(budget) / (rho * G):.4f} m of head for friction)\n")

    return Verdict(
        gate="fluid.pipe_pressure_drop",
        passed=dp <= float(budget),
        measured=round(dp, 3),
        limit=round(float(budget), 3),
        units="Pa",
        detail=f"dp {dp / 1000:.2f} kPa = major {dp_major / 1000:.2f} + minor "
               f"{dp_minor / 1000:.2f}, vs {float(budget) / 1000:.2f} kPa budget"
               f"{head_basis}; f {f:.4f} {law}, Re {eng(re)} {regime_name(re)}, "
               f"e/D {rel:.1e}, L/D {length / d:.0f}, sumK {k_total:.2f}, "
               f"v {v:.2f} m/s ({v_src}).{k_note}",
        evidence=[path],
    )


@gate(
    id="fluid.flow_regime",
    title="Every correlation in this pack is being used inside its Reynolds range",
    # DELIBERATELY BROAD. This gate measures no design quantity; it decides
    # whether the OTHER gates' numbers mean anything, so when it trips it must
    # drag the whole flow story down with it. Same role a slenderness check plays
    # for beam theory.
    claims=["flow-regime", "drag", "resistance", "pressure-drop", "head-loss", "pipe-flow"],
    tier=Tier.INSTANT,
    settles="flow regime validity",
    negative_control=NegativeControl(
        fixture="selftest/bad_flow.py:transitional_line",
        note="the same line at a reduced flow rate that lands Re at the midpoint of the "
             "pack's own 2300-4000 transition band, where neither 64/Re nor Colebrook-White "
             "holds; geometry, fluid and fittings are untouched and the pressure-drop gate "
             "would happily return a confident number. The same fixture carries a positive "
             "regression the control cannot express: that this gate settles an external-only "
             "flow inside its Cd band instead of skipping on it",
    ),
)
def flow_regime(ctx: GateContext) -> Verdict:
    """The guard on the other two gates.

    Two failures are possible and both are silent without this gate:

    * **Internal flow in the transition band.** Between Re 2300 and 4000 the flow
      trips intermittently. 64/Re is no longer exact and Colebrook-White was
      never fitted there; the true friction factor can sit anywhere between the
      two curves, a spread of two to one. Darcy-Weisbach still returns a number.
    * **A table drag coefficient outside its band.** Cd is a function of Reynolds
      number, not a property of a shape. Past the drag crisis a sphere's Cd falls
      by a factor of three, and the arithmetic does not notice.

    A pass here does not make the other gates right. It only removes the one
    error that makes them confidently wrong.

    Three checks, not two. The third is on the *roughness* side of the same
    correlation: Colebrook-White, the Moody chart and Swamee-Jain are all bounded
    at about e/D = 0.05, and past that the equation still returns a confident
    friction factor for a bore whose "roughness" is a sizeable fraction of its
    own diameter. A roughness typed in millimetres puts a plastic line at
    e/D = 6e-2 and a 1.5 m roughness in a 25 mm bore at e/D = 60, and both used
    to sail through. The friction factor itself is then bounds-checked against
    the band a turbulent f cannot physically leave, which catches the same unit
    slips from the other side.

    **Reachability is the property to protect here.** This gate is the pack's
    validity guard: it binds to every flow claim, so when it cannot reach a
    verdict every drag and pressure-drop claim in the sweep goes BLOCKED with it.
    It used to SKIP on an external-only project (a strut, a float, a hull with no
    piping) whose Reynolds number was INSIDE its tabulated band — the valid case,
    and the pack's headline use case — because `measured` was only assigned on
    the failing branches. The Reynolds numbers below are therefore recorded the
    moment they exist, BEFORE any branch decides what to think of them, so no
    future branch can leave the gate unable to answer.
    """
    got = fluid(ctx, "fluid.flow_regime")
    if isinstance(got, Verdict):
        return got
    rho, nu, _nu_src = got

    failures: list[str] = []
    notes: list[str] = []
    measured: float | None = None
    limit: float | None = None
    #: What `measured` currently is. Three different quantities can be the thing
    #: that failed here, and a verdict that reported a relative roughness under
    #: the label "Re" would be a unit lie in the guard gate of all places.
    units = "Re"

    # -- internal flow ---------------------------------------------------- #
    d = number(ctx, PIPE_D_KEYS, None)
    if d and d > 0:
        resolved = _pipe_velocity(ctx, d)
        if resolved is not None:
            v_pipe, _src = resolved
            re_i = v_pipe * d / nu
            measured = re_i          # recorded before any branch. See the docstring.
            # Half-open on purpose: regime_name() calls exactly 4000 turbulent, and
            # a guard that disagreed with the name it prints at one exact value is a
            # bug report waiting to happen.
            if RE_LAMINAR_MAX <= re_i < RE_TURBULENT_MIN:
                limit = RE_TURBULENT_MIN
                failures.append(
                    f"internal Re {eng(re_i)} is in the {RE_LAMINAR_MAX:.0f}-"
                    f"{RE_TURBULENT_MIN:.0f} transition gap where neither 64/Re nor "
                    f"Colebrook-White holds; change the bore or the flow to leave it")
            else:
                notes.append(f"internal Re {eng(re_i)} {regime_name(re_i)}")

            # -- the roughness side of the same correlation ---------------- #
            eps = number(ctx, ROUGHNESS_KEYS, None)
            if eps is None or eps < 0:
                notes.append(f"no roughness stated ({missing(ROUGHNESS_KEYS)}), so e/D "
                             f"and the friction factor are unchecked")
            else:
                rel = eps / d
                if rel > REL_ROUGHNESS_MAX:
                    limit, measured, units = REL_ROUGHNESS_MAX, rel, "e/D"
                    failures.append(
                        f"relative roughness e/D {rel:.2e} is past the {REL_ROUGHNESS_MAX} "
                        f"ceiling of the Moody chart and of Colebrook-White ({eps:.3e} m "
                        f"of roughness in a {d:.4f} m bore) — the correlation was never "
                        f"fitted there and still returns a number. Check the roughness is "
                        f"in METRES, and the bore is a bore")
                elif re_i >= RE_TURBULENT_MIN:
                    f_t, law, _trace = friction_factor(re_i, rel)
                    if not (F_TURBULENT_MIN <= f_t <= F_TURBULENT_MAX):
                        limit = F_TURBULENT_MAX if f_t > F_TURBULENT_MAX else F_TURBULENT_MIN
                        measured, units = f_t, "Darcy f"
                        failures.append(
                            f"turbulent friction factor {f_t:.4f} ({law}) is outside the "
                            f"{F_TURBULENT_MIN}-{F_TURBULENT_MAX} band a Darcy f cannot "
                            f"physically leave; at Re {eng(re_i)} and e/D {rel:.2e} that "
                            f"means an input is wrong, not that the pipe is unusual")
                    else:
                        notes.append(f"e/D {rel:.1e}, f {f_t:.4f} inside "
                                     f"{F_TURBULENT_MIN}-{F_TURBULENT_MAX}")

    # -- external flow ----------------------------------------------------- #
    v = number(ctx, VEL_KEYS, None)
    lchar = number(ctx, LCHAR_KEYS, None)
    shape = text(ctx, SHAPE_KEYS)
    if v and lchar and v > 0 and lchar > 0:
        re_e = v * lchar / nu
        if measured is None:
            measured = re_e          # recorded before any branch. See the docstring.
        if shape and shape in DRAG_TABLE:
            cd, re_lo, re_hi, _ref, _note = DRAG_TABLE[shape]
            if not (re_lo <= re_e <= re_hi):
                limit = re_hi if re_e > re_hi else re_lo
                failures.append(
                    f"external Re {eng(re_e)} is outside the Re {eng(re_lo)}-{eng(re_hi)} "
                    f"band that Cd {cd} for {shape!r} was measured over")
            else:
                notes.append(f"external Re {eng(re_e)} {regime_name(re_e)} inside "
                             f"{shape} band {eng(re_lo)}-{eng(re_hi)}")
        else:
            notes.append(f"external Re {eng(re_e)} {regime_name(re_e)}, no table class "
                         f"declared - the model owns its Cd's valid range")

    if measured is None:
        return skip("fluid.flow_regime",
                    f"no flow to classify: give {missing(PIPE_D_KEYS)} with "
                    f"{missing(FLOW_KEYS)} for internal flow, or {missing(VEL_KEYS)} with "
                    f"{missing(LCHAR_KEYS)} for external flow")

    ok = not failures
    body = "; ".join(failures) if failures else "; ".join(notes)
    return Verdict(
        gate="fluid.flow_regime",
        passed=ok,
        measured=round(measured, 4),
        # No limit on a PASS. There is no threshold a valid Reynolds number must
        # stay under — the band edges sit on both sides of it — so reporting one
        # made a passing verdict read "16686 vs 4000" to every renderer that
        # applies the usual measured <= limit convention. On a FAIL the limit is
        # the edge that was actually offended, which is the number to act on.
        limit=round(float(limit), 4) if (limit is not None and failures) else None,
        units=units,
        detail=(("REGIME INVALID: " if failures else "regimes valid: ") + body
                + (f" | also {'; '.join(notes)}" if failures and notes else "")
                + f" | rho {rho:.0f} kg/m3, nu {nu:.2e} m2/s"),
    )
