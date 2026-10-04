# SPDX-License-Identifier: Apache-2.0
"""thermal-analytic — closed-form heat transfer and solar collection, tier 0.

Ten gates, no dependencies beyond the standard library, all of them sub-second.
Each reads its numbers from ``ctx.params`` — the model's projection — and never
re-derives a quantity the model already owns (rule 2). The physics lives in
``_thermal_physics.py`` so that a disputed number can be reproduced at a REPL
without a GateContext.

**Two habits this module keeps, and why.**

*Missing parameters SKIP, they do not guess.* A thermal model that has not
declared ``stagnation_limit_c`` has not thought about stagnation, and inventing
200 degC on its behalf would produce a green tick for a question nobody asked.
Every gate reads what it needs through ``_need`` and returns a SKIP naming the
missing keys; the claim then resolves BLOCKED and stays visible (rule 4).

*Out of the correlation's band is a FAIL, not a pass with a footnote.* A Nusselt
number evaluated outside the range its correlation was fitted over is arithmetic,
not measurement. The gate that produced it has not settled anything, and saying
so loudly is the only behaviour that keeps the rest of the sweep worth reading.

**Claim binding.** Gates bind to the narrow quantity they measure, not to
"thermal" — a gate that claims the whole domain drags every thermal claim to FAIL
when one number moves, and a convection problem then reads as an insulation
problem. The one deliberate exception is ``thermal.time_constant``: the Biot
check does not measure a design quantity, it decides whether the single-node
temperature the other lumped gates report exists anywhere in the part, so it
binds to those gates' tags on purpose.
"""
from __future__ import annotations

import os
import sys

# The pack directory is on sys.path when the spine loads a pack; the gates/
# directory is not. Adding it here keeps the shared physics module importable
# whether this file is loaded by the pack loader, by a test, or by hand.
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import _thermal_physics as P                                        # noqa: E402

from atompipe.gates import gate, GateContext                        # noqa: E402
from atompipe.models import NegativeControl, Tier, Verdict          # noqa: E402

_MISSING = object()

#: Above this Biot number the lumped (single-temperature) model stops describing
#: the body. The 0.1 is the textbook licence and it is not arbitrary: it holds the
#: internal temperature difference to roughly 10% of the surface-to-fluid one,
#: which is about where "the part has a temperature" stops being a sentence.
BIOT_LIMIT = 0.1

#: Default rule-of-thumb floor for fin effectiveness. Below 2 the fin is not worth
#: the material or the tooling; below 1 it is actively worse than the bare base it
#: covered. Overridable per project with `fin_effectiveness_min`.
FIN_EFFECTIVENESS_MIN = 2.0

#: Ground reflectance assumed when the model does not declare one. Weakly
#: sensitive (a few percent of plane-of-array on a tilted surface) which is why a
#: default is tolerable here and nowhere else — and the detail line always says
#: when it was assumed rather than declared.
DEFAULT_ALBEDO = 0.20


# --------------------------------------------------------------------------- #
# parameter access
# --------------------------------------------------------------------------- #
def _need(ctx: GateContext, *keys: str) -> tuple[dict, list[str]]:
    """Read required params. Returns ``(values, missing)`` — never raises.

    A gate calling this and returning ``_skip(...)`` on a non-empty ``missing``
    is the whole of rule 7: the pack cannot know which quantities a project's
    model happens to project, so it names what it wanted and refuses to run,
    rather than inventing a default that would be reported as measured.
    """
    values: dict = {}
    missing: list[str] = []
    for key in keys:
        value = ctx.param(key, _MISSING)
        if value is _MISSING or value is None:
            missing.append(key)
        else:
            values[key] = value
    return values, missing


def _skip(gate_id: str, missing: list[str], hint: str = "") -> Verdict:
    """A visible refusal to run. The claim goes BLOCKED, not PASS."""
    return Verdict(
        gate=gate_id, passed=False, skipped=True,
        skip_reason=f"model does not project {', '.join(missing)}"
                    + (f" — {hint}" if hint else ""),
    )


def _write(ctx: GateContext, name: str, text: str) -> list[str]:
    """Write an evidence file and return it as a one-element citation list.

    Evidence is best-effort: a read-only out_dir must not turn a working gate
    into an ERROR verdict, because the measurement is still valid and the
    verdict still has to be reported.
    """
    try:
        path = ctx.out_path(name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return [path]
    except OSError:
        return []


# --------------------------------------------------------------------------- #
# conduction
# --------------------------------------------------------------------------- #
@gate(
    id="thermal.conduction",
    title="Wall-stack U-value from a series/parallel resistance network",
    claims=["conduction", "u-value", "insulation", "envelope"],
    tier=Tier.INSTANT,
    settles="u-value",
    negative_control=NegativeControl(
        fixture="selftest/bad_thermal.py:insulation_omitted",
        note="the insulation is left out of the layer carrying the most resistance: "
             "its conductivity rises to that of an unsealed air cavity, and further if "
             "that alone would not clear the wall's own declared U-limit. Thicknesses, "
             "framing fractions, films and area are untouched",
    ),
)
def conduction(ctx: GateContext) -> Verdict:
    """1D steady conduction through a layered wall, framing included.

    R per unit area is additive in series; co-planar branches (insulation beside
    studs, foam beside fasteners) add in CONDUCTANCE weighted by area fraction.
    Reporting the clear-field U-value as the wall's U-value is the standard way
    to be 30% wrong in the optimistic direction, so the parallel form is a
    first-class layer shape here rather than an afterthought.
    """
    gid = "thermal.conduction"
    vals, missing = _need(ctx, "wall_layers", "wall_area_m2", "wall_u_limit_w_m2k")
    if missing:
        return _skip(gid, missing,
                     "wall_layers is a list of {name, thickness_m, k_w_mk} or "
                     "{name, thickness_m, parallel:[{name, k_w_mk, area_fraction}]}")

    layers = list(vals["wall_layers"])
    area = float(vals["wall_area_m2"])
    limit = float(vals["wall_u_limit_w_m2k"])
    film_r = float(ctx.param("surface_film_r_m2k_w", 0.0) or 0.0)

    try:
        stack = P.stack_resistance(layers, film_r)
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        return _skip(gid, ["wall_layers"], f"layer is malformed: {exc}")

    u = stack["u_w_m2k"]
    r_total = stack["r_total_m2k_w"]
    loss_per_k = u * area
    rows = "\n".join(f"{r:9.4f} m2K/W   {label}" for label, r in stack["rows"])
    evidence = _write(ctx, "conduction_stack.txt",
                      f"wall stack, {area:.3f} m2, films {film_r:.3f} m2K/W\n"
                      f"{rows}\n{r_total:9.4f} m2K/W   TOTAL  ->  U = {u:.4f} W/m2K\n")
    films = "films included" if film_r else "NO surface films declared"
    return Verdict(
        gate=gid, passed=u <= limit, measured=round(u, 4), limit=round(limit, 4), comparator="<=",
        units="W/m2K",
        detail=f"U={u:.3f} W/m2K vs {limit:.3f} limit (R={r_total:.2f} m2K/W, "
               f"{len(layers)} layers, {films}; {loss_per_k:.2f} W/K over {area:.2f} m2)",
        evidence=evidence,
    )


# --------------------------------------------------------------------------- #
# convection
# --------------------------------------------------------------------------- #
@gate(
    id="thermal.convection",
    title="Convective coefficient from a Nusselt correlation, with its regime",
    claims=["convection", "heat-transfer-coefficient", "airflow"],
    tier=Tier.INSTANT,
    settles="convective heat transfer coefficient",
    # No edge to thermal.h_agreement: it compares representations rather than
    # guarding validity, and this gate's own control, fan_stopped, fails it
    # (17.7 vs 5.6 W/m^2K).
    negative_control=NegativeControl(
        fixture="selftest/bad_thermal.py:fan_stopped",
        note="the agency driving the convection collapses — forced: the velocity falls "
             "to a tenth of design or lower, h going as sqrt(V); natural: the "
             "surface-to-fluid difference collapses by the same factor and Rayleigh "
             "falls out of the correlation's band. Either way h lands clear below the h "
             "the design declared it needs",
    ),
)
def convection(ctx: GateContext) -> Verdict:
    """Natural or forced convection h, against the h the design needs.

    Properties are evaluated at the film temperature, which is the point every
    correlation here was fitted at. The verdict reports Ra or Re and the named
    correlation, because h alone is unfalsifiable: two people can get 8 and 40
    W/m2K for the same surface and both be quoting a real correlation.

    A Ra/Re outside the correlation's fitted band FAILS. The arithmetic still
    produces a number; that number is not evidence, and a gate that reports it as
    a pass has laundered an assumption into a proof.
    """
    gid = "thermal.convection"
    vals, missing = _need(ctx, "convection_mode", "convection_geometry",
                          "char_length_m", "surface_c", "ambient_c",
                          "h_required_w_m2k")
    if missing:
        return _skip(gid, missing,
                     "convection_mode is 'natural' or 'forced'; convection_geometry is one of "
                     + ", ".join(P.NATURAL_GEOMETRIES + P.FORCED_GEOMETRIES))

    mode = str(vals["convection_mode"]).strip().lower()
    geometry = str(vals["convection_geometry"]).strip().lower()
    length = float(vals["char_length_m"])
    t_s = float(vals["surface_c"])
    t_inf = float(vals["ambient_c"])
    h_req = float(vals["h_required_w_m2k"])
    fluid = str(ctx.param("fluid", "air") or "air")

    if mode not in ("natural", "forced"):
        return _skip(gid, ["convection_mode"], f"got {mode!r}; expected 'natural' or 'forced'")
    if length <= 0.0:
        return _skip(gid, ["char_length_m"], f"must be > 0, got {length}")

    film_k = P.c_to_k((t_s + t_inf) / 2.0)
    try:
        props = P.fluid_properties(fluid, film_k)
    except ValueError as exc:
        return _skip(gid, ["fluid"], str(exc))

    if not props["in_range"]:
        return Verdict(
            gate=gid, passed=False, measured=round(film_k, 1),
            limit=round(props["table_hi_k"], 1), comparator="<=", units="K",
            detail=f"film temperature {film_k:.1f} K is outside the tabulated {fluid} "
                   f"properties ({props['table_lo_k']:.0f}-{props['table_hi_k']:.0f} K) — "
                   f"h here would be extrapolated, not measured",
        )

    try:
        if mode == "natural":
            ra = P.rayleigh(t_s - t_inf, length, props)
            # The SIGNED difference, deliberately: Ra is a magnitude and takes
            # abs(), so this is the only place the direction of buoyancy can
            # still be known. Without it a cold plate declared hot-face-up gets
            # the free-plume correlation and h comes out 2x high.
            corr = P.natural_nusselt(geometry, ra, props["pr"], t_s - t_inf)
            number, label = ra, "Ra"
        else:
            v_vals, v_missing = _need(ctx, "velocity_m_s")
            if v_missing:
                return _skip(gid, v_missing, "forced convection needs a flow velocity")
            re = float(v_vals["velocity_m_s"]) * length / props["nu"]
            corr = P.forced_nusselt(geometry, re, props["pr"])
            number, label = re, "Re"
    except ValueError as exc:
        return _skip(gid, ["convection_geometry"], str(exc))

    h = corr["nu"] * props["k"] / length
    regime = corr.get("regime", "")
    # Echo what char_length_m was SUPPOSED to be for the geometry that actually
    # ran. A correlation fed the wrong length is wrong by L^3 inside Ra and the
    # answer still looks reasonable, so the verdict states the definition rather
    # than trusting the reader to hold correlations.md in their head.
    l_rule = P.CHAR_LENGTH.get(corr.get("geometry", geometry), "see correlations.md")
    common = (f"{corr['correlation']}, {label}={number:.3g}"
              + (f" ({regime})" if regime else "")
              + f", Pr={props['pr']:.3f}, Tfilm={film_k:.1f} K, "
                f"L={length:.3f} m = {l_rule}")
    swap = corr.get("substituted", "")

    if not corr["valid"]:
        return Verdict(
            gate=gid, passed=False, measured=round(number, 4), limit=None, units=label,
            detail=f"{label}={number:.3g} is outside the validated band [{corr['band']}] for "
                   f"{corr['correlation']} — the h it returns ({h:.1f} W/m2K) is arithmetic, "
                   f"not a measurement" + (f"; {swap}" if swap else ""),
        )

    return Verdict(
        gate=gid, passed=h >= h_req, measured=round(h, 3), limit=round(h_req, 3), comparator=">=",
        units="W/m2K",
        detail=f"h={h:.1f} W/m2K vs {h_req:.1f} required ({mode} {geometry}; "
               f"Nu={corr['nu']:.1f}; {common})" + (f"; {swap}" if swap else ""),
    )


# --------------------------------------------------------------------------- #
# radiation
# --------------------------------------------------------------------------- #
@gate(
    id="thermal.radiation",
    title="Net radiant exchange with emissivity and a view factor",
    claims=["radiation", "emissivity", "radiative-loss"],
    tier=Tier.INSTANT,
    settles="radiative heat exchange",
    negative_control=NegativeControl(
        fixture="selftest/bad_thermal.py:coating_degraded",
        note="the surface's emissivity moves the way that hurts, which depends on what "
             "the surface is for: an absorber (rad_sense 'max') degrades to a plain black "
             "surface and loses too much, a passively cooled enclosure (rad_sense 'min') "
             "ships bare and polished and rejects too little. Temperatures, area and view "
             "factor are identical",
    ),
)
def radiation(ctx: GateContext) -> Verdict:
    """Two-surface grey-body exchange, T^4 and all.

    ``rad_sense`` says which way the inequality runs, because both directions are
    real work: a solar absorber must keep radiative LOSS under a cap ("max"),
    while a passively cooled enclosure must achieve a minimum radiative
    REJECTION ("min"). A pack that silently assumed one of them would be wrong
    for half its users and give them a green tick for it.

    The T^4 is the reason this gate exists as a separate number. At 30 degC over
    ambient, radiation from a high-emissivity surface is comparable with natural
    convection; at 300 degC it is most of the heat flow, and any analysis that
    lumped it into an h calibrated at low temperature is badly wrong in the
    dangerous direction.
    """
    gid = "thermal.radiation"
    vals, missing = _need(ctx, "rad_surface_c", "rad_surround_c", "rad_area_m2",
                          "emissivity", "view_factor", "rad_limit_w")
    if missing:
        return _skip(gid, missing,
                     "view_factor has no safe default — 1.0 means the surface sees only "
                     "the surround, and assuming it is how a shaded radiator passes")

    sense = str(ctx.param("rad_sense", "max") or "max").strip().lower()
    if sense not in ("max", "min"):
        return _skip(gid, ["rad_sense"],
                     f"got {sense!r}; 'max' = loss must stay under the limit, "
                     f"'min' = rejection must exceed it")

    t1 = P.c_to_k(float(vals["rad_surface_c"]))
    t2 = P.c_to_k(float(vals["rad_surround_c"]))
    area = float(vals["rad_area_m2"])
    eps1 = float(vals["emissivity"])
    view = float(vals["view_factor"])
    limit = float(vals["rad_limit_w"])
    eps2 = float(ctx.param("emissivity_surround", 1.0) or 1.0)
    area2 = ctx.param("area_surround_m2", None)

    try:
        result = P.radiation_exchange(t1, t2, area, eps1, view, eps2,
                                      float(area2) if area2 else None)
    except ValueError as exc:
        return _skip(gid, ["emissivity", "view_factor", "area_surround_m2"], str(exc))

    q = result["q_w"]
    passed = (q <= limit) if sense == "max" else (q >= limit)
    enclosure = "large surround (eps2=1)" if eps2 >= 1.0 else f"eps2={eps2:g} over {area2} m2"
    return Verdict(
        gate=gid, passed=passed, measured=round(q, 2), limit=round(limit, 2), comparator=("<=" if sense == "max" else ">="), units="W",
        detail=f"{q:.1f} W net radiant ({'<=' if sense == 'max' else '>='} {limit:.1f} W) "
               f"from {area:.3f} m2 at {P.k_to_c(t1):.1f}C to {P.k_to_c(t2):.1f}C, "
               f"eps={eps1:g} F={view:g}, {enclosure}; h_rad={result['h_rad']:.2f} W/m2K",
    )


# --------------------------------------------------------------------------- #
# fins
# --------------------------------------------------------------------------- #
@gate(
    id="thermal.fin_efficiency",
    title="Fin effectiveness, including the case where the fin makes it worse",
    claims=["fin", "heatsink", "fin-effectiveness", "extended-surface"],
    tier=Tier.INSTANT,
    settles="fin effectiveness",
    negative_control=NegativeControl(
        fixture="selftest/bad_thermal.py:plastic_fin",
        note="the same fin geometry moulded in a polymer instead of metal: conductivity "
             "collapses at unchanged section, length, span and h, so the effectiveness "
             "CEILING sqrt(kP/(hA_c)) drops below the project's own floor and no length "
             "of fin can recover it",
    ),
)
def fin_efficiency(ctx: GateContext) -> Verdict:
    """Straight rectangular fin: efficiency, effectiveness, and the ceiling.

    Efficiency is the number people quote and effectiveness is the number that
    decides anything. A fin with effectiveness below 1 removes LESS heat than the
    bare base area it stands on, because the base-to-fin conduction resistance
    costs more than the added surface earns. It is not a rare pathology: a thick
    short fin, a low-conductivity material, or a high h (anything liquid-cooled)
    all push toward it, and the three combine.

    ``eps_ceiling = sqrt(k*P/(h*A_c))`` is the effectiveness of an infinitely
    long fin of this section — the best this material and section can ever do.
    When it is the binding number, no amount of extra fin length helps and the
    verdict says so, because 'make it taller' is the reflex and it is wasted.
    """
    gid = "thermal.fin_efficiency"
    vals, missing = _need(ctx, "fin_k_w_mk", "fin_thickness_m", "fin_length_m",
                          "fin_width_m", "fin_h_w_m2k")
    if missing:
        return _skip(gid, missing,
                     "fin_width_m is the span along the base (the fin's third dimension), "
                     "fin_length_m is how far it protrudes")

    limit = float(ctx.param("fin_effectiveness_min", FIN_EFFECTIVENESS_MIN))
    try:
        fin = P.straight_fin(float(vals["fin_k_w_mk"]), float(vals["fin_thickness_m"]),
                             float(vals["fin_length_m"]), float(vals["fin_width_m"]),
                             float(vals["fin_h_w_m2k"]))
    except ValueError as exc:
        return _skip(gid, ["fin_k_w_mk"], str(exc))

    eps = fin["effectiveness"]
    ceiling = fin["eps_ceiling"]
    if eps < 1.0:
        verdict_note = ("BELOW 1: this fin removes less heat than the bare base it covers — "
                        "delete it")
    elif ceiling < limit:
        verdict_note = (f"ceiling {ceiling:.2f} is itself below the {limit:g} target: length "
                        f"cannot fix this, only a higher k or a thicker section")
    else:
        verdict_note = f"eta={fin['efficiency'] * 100:.0f}%, mL={fin['mL']:.2f}"
    return Verdict(
        gate=gid, passed=eps >= limit, measured=round(eps, 3), limit=round(limit, 3), comparator=">=",
        units="effectiveness",
        detail=f"effectiveness {eps:.2f} vs {limit:g} minimum (ceiling {ceiling:.2f}); "
               f"{verdict_note}; q={fin['q_fin_w_per_k']:.3f} W/K per fin",
    )


# --------------------------------------------------------------------------- #
# steady-state temperature
# --------------------------------------------------------------------------- #
@gate(
    id="thermal.steady_state_temp",
    title="Component temperature from power and the resistance network",
    claims=["steady-state-temperature", "component-temperature", "cooling"],
    tier=Tier.INSTANT,
    settles="steady-state temperature",
    # No edge to thermal.time_constant (P2.2-D12, "a guard that also
    # measures"): it also fails on tau over time_constant_limit_s, which says
    # nothing about the steady-state node, so a fail there would hide this
    # measurement. It can land once the Biot half of time_constant is its own
    # evaluator.
    negative_control=NegativeControl(
        fixture="selftest/bad_thermal.py:dry_joint",
        note="the thermal interface material is omitted and the part is bolted dry: the "
             "named interface node rises 9x, or — when the path names no interface — a new "
             "'dry interface, no TIM' node is INSERTED rather than an innocent node "
             "relabelled. Power, ambient, the limit and every other node are unchanged",
    ),
)
def steady_state_temp(ctx: GateContext) -> Verdict:
    """T = T_ambient + P * sum(R), against the part's limit.

    The arithmetic is trivial and the value is entirely in the network being
    written down. Two things the network catches that intuition does not: the
    interface resistances (a dry joint is often the largest single node, and it
    is invisible in a CAD model), and the fact that the ambient is the ambient
    INSIDE the enclosure, which is not room temperature once the enclosure has
    been closed for an hour.

    Ambient here is the number people get wrong most often, so the detail line
    always states it explicitly rather than folding it into the rise.
    """
    gid = "thermal.steady_state_temp"
    vals, missing = _need(ctx, "power_w", "ambient_c", "temp_limit_c")
    if missing:
        return _skip(gid, missing, "ambient_c is the air the part actually sees, not the room")

    path = ctx.param("resistance_path_k_w", None)
    total_r = ctx.param("total_resistance_k_w", None)
    if path is None and total_r is None:
        return _skip(gid, ["resistance_path_k_w"],
                     "a list of {name, r_k_w} nodes (or total_resistance_k_w) — an unnamed "
                     "total hides which node to attack")

    rows: list[tuple[str, float]] = []
    if path is not None:
        try:
            net = P.series_resistance(list(path))
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            return _skip(gid, ["resistance_path_k_w"], f"node is malformed: {exc}")
        r_total, rows = net["r_total_k_w"], net["rows"]
    else:
        r_total = float(total_r)
        if r_total <= 0.0:
            return _skip(gid, ["total_resistance_k_w"], f"must be > 0, got {r_total}")

    power = float(vals["power_w"])
    ambient = float(vals["ambient_c"])
    limit = float(vals["temp_limit_c"])
    rise = power * r_total
    temp = ambient + rise
    worst = max(rows, key=lambda r: r[1]) if rows else None
    dominant = (f"; largest node '{worst[0]}' {worst[1]:.2f} K/W "
                f"({worst[1] / r_total * 100:.0f}%)") if worst else ""
    evidence = _write(ctx, "resistance_path.txt",
                      "\n".join(f"{r:8.3f} K/W  {name}" for name, r in rows)
                      + f"\n{r_total:8.3f} K/W  TOTAL\n"
                      + f"{power:.2f} W -> {rise:.1f} K rise over {ambient:.1f} C\n"
                      ) if rows else []
    return Verdict(
        gate=gid, passed=temp <= limit, measured=round(temp, 2), limit=round(limit, 2), comparator="<=",
        units="degC",
        detail=f"{temp:.1f} C vs {limit:.1f} C limit ({power:.2f} W x {r_total:.2f} K/W "
               f"= {rise:.1f} K rise over {ambient:.1f} C ambient{dominant})",
        evidence=evidence,
    )


# --------------------------------------------------------------------------- #
# transient — and the gate that polices the lumped model
# --------------------------------------------------------------------------- #
@gate(
    id="thermal.time_constant",
    title="Lumped time constant, and whether lumped capacitance is legitimate at all",
    # DELIBERATELY BROAD, and the only gate here that is. It measures no design
    # quantity; it decides whether the single-node temperature the other lumped
    # gates report exists anywhere in the part. When Biot exceeds 0.1 the
    # steady-state node and the time constant are both describing a body that does
    # not have one temperature, so this gate drags those claims down with it.
    claims=["transient", "time-constant", "thermal-response", "lumped-capacitance",
            "steady-state-temperature", "component-temperature"],
    tier=Tier.INSTANT,
    settles="thermal time constant",
    negative_control=NegativeControl(
        fixture="selftest/bad_thermal.py:moulded_housing",
        note="the same body, same size, same h, moulded in a polymer instead of metal: "
             "the conductivity drop carries Biot clear past the lumped-model licence and "
             "the body stops having one temperature at all",
    ),
)
def time_constant(ctx: GateContext) -> Verdict:
    """Biot first, tau second. The order is the point.

        L_c = V/A_s      Bi = h*L_c/k      tau = rho*V*cp / (h*A_s)

    tau is the number the model wants and Bi is the number that says whether tau
    means anything. Above Bi ~ 0.1 the surface responds while the core has not
    started, the single exponential under-predicts the time to reach the core, and
    every lumped result computed from the same body — including a steady-state
    component temperature — is describing an average that exists nowhere in the
    part. Reporting tau without Bi is the classic way to be confidently wrong
    about a heatsink, a battery pack or a thick enclosure wall.

    When ``time_constant_limit_s`` is projected the gate also holds tau to it, and
    the detail line names which of the two conditions failed; ``measured`` and
    ``limit`` then carry tau and its limit in seconds, not Biot, so the reported
    pair always describes the inequality that actually decided the verdict.

    FALSIFICATION GAP, stated rather than hidden. The shipped control
    (``moulded_housing``) attacks the BIOT branch only. tau does not depend on
    the body's conductivity at all — ``tau = rho*V*cp/(h*A_s)`` — so no change to
    k can exercise the optional ``time_constant_limit_s`` inequality, and one
    fixture cannot honestly move two independent quantities. A project that
    leans on the tau limit should add a local fixture raising ``body_h_w_m2k`` or
    cutting ``rho*V*cp`` until tau passes its ceiling, and watch this gate fail
    on it. The stagnation branch of the collector had the same shape and was
    split out into ``solar.stagnation`` for exactly this reason; tau was left
    here because Biot is a validity guard on tau rather than an independent
    question, and separating them would ship a tau verdict that is meaningless
    on its own.
    """
    gid = "thermal.time_constant"
    vals, missing = _need(ctx, "body_volume_m3", "body_area_m2", "body_density_kg_m3",
                          "body_cp_j_kgk", "body_k_w_mk", "body_h_w_m2k")
    if missing:
        return _skip(gid, missing,
                     "body_area_m2 is the whole wetted surface; body_k_w_mk is the BODY's "
                     "conductivity (the Biot denominator), not the fluid's")

    try:
        lump = P.lumped(float(vals["body_volume_m3"]), float(vals["body_area_m2"]),
                        float(vals["body_density_kg_m3"]), float(vals["body_cp_j_kgk"]),
                        float(vals["body_k_w_mk"]), float(vals["body_h_w_m2k"]))
    except ValueError as exc:
        return _skip(gid, ["body_volume_m3"], str(exc))

    bi_limit = float(ctx.param("biot_limit", BIOT_LIMIT))
    bi = lump["biot"]
    tau = lump["tau_s"]
    lumped_ok = bi <= bi_limit

    tau_limit = ctx.param("time_constant_limit_s", None)
    tau_ok = True
    tau_clause = ""
    if tau_limit is not None:
        tau_ok = tau <= float(tau_limit)
        tau_clause = f"; tau {'<=' if tau_ok else '>'} {float(tau_limit):.0f} s limit"

    if not lumped_ok:
        reason = (f"Bi={bi:.3f} > {bi_limit:g}: lumped capacitance does NOT apply — the "
                  f"surface and the core are not the same temperature, so tau={tau:.0f} s "
                  f"under-predicts and any single-node temperature for this body is fiction")
    else:
        reason = (f"Bi={bi:.4f} <= {bi_limit:g}, lumped model valid; tau={tau:.0f} s "
                  f"(63% in {tau:.0f} s, 95% in {3 * tau:.0f} s, "
                  f"C={lump['capacitance_j_k']:.0f} J/K, Lc={lump['l_c_m'] * 1000:.1f} mm)"
                  + tau_clause)

    # measured/limit/units must describe the quantity that DECIDED the verdict.
    # Leaving them on Biot when the tau branch is the failing one produced a row
    # reading "measured 0.0004 against a limit of 0.1 — FAIL", which is a value
    # 200x inside its limit next to a refusal: unreadable in a report and
    # actively misleading to anything downstream that compares the two numbers.
    if lumped_ok and not tau_ok:
        measured, limit, units = round(tau, 1), round(float(tau_limit), 1), "s"
    else:
        measured, limit, units = round(bi, 5), bi_limit, "Biot"
    return Verdict(
        gate=gid, passed=lumped_ok and tau_ok, measured=measured, limit=limit, comparator="<=",
        units=units,
        detail=reason,
    )


# --------------------------------------------------------------------------- #
# solar
# --------------------------------------------------------------------------- #
@gate(
    id="solar.collector_output",
    title="Hottel-Whillier useful heat from a flat-plate collector",
    claims=["solar-thermal", "collector-output", "useful-heat"],
    tier=Tier.INSTANT,
    settles="solar collector useful heat",
    negative_control=NegativeControl(
        fixture="selftest/bad_thermal.py:waterlogged_insulation",
        note="the collector's back insulation has taken up water, so the loss "
             "coefficient U_L rises at unchanged optics, area, flow and irradiance "
             "until useful output falls clear of the required minimum",
    ),
)
def collector_output(ctx: GateContext) -> Verdict:
    """Useful heat from a glazed flat plate.

        Q_u = F_R * A * (tau_alpha*G - U_L*(T_in - T_amb))
        eta = Q_u / (A*G)

    The loss term is driven by the INLET temperature, not the outlet and not the
    mean: heat the collector has already given the fluid is heat it is no longer
    losing at the inlet end. Running a store return 10 K hotter costs
    ``F_R*A*U_L*10`` watts whatever the sun is doing, which is why a low-return
    stratified store is worth more than another square metre of aperture.

    Stagnation used to be enforced here too, on the same verdict. It is now
    ``solar.stagnation``, its own gate with its own control. The split is not
    cosmetic: the two inequalities move in OPPOSITE directions under the one
    parameter either of them cares about. Raising ``U_L`` fails output and makes
    stagnation safer; halving it does the reverse. Sharing a verdict meant one of
    the two branches could only ever be falsified by a fixture that rescued the
    other, and a control that repairs the failure mode it is supposed to plant is
    worse than no control at all. It also meant `measured` and `limit` reported
    watts while the refusal was about degrees.

    The detail line still REPORTS the stagnation temperature, because a reader
    looking at output should see it, and names the gate that enforces it.
    """
    gid = "solar.collector_output"
    irradiance = ctx.param("irradiance_w_m2", ctx.param("poa_irradiance_w_m2", None))
    vals, missing = _need(ctx, "collector_area_m2", "collector_fr", "collector_tau_alpha",
                          "collector_ul_w_m2k", "collector_inlet_c", "collector_ambient_c",
                          "collector_output_min_w")
    if irradiance is None:
        missing.append("irradiance_w_m2")
    if missing:
        return _skip(gid, missing,
                     "collector_ambient_c is OUTDOOR air and is deliberately not the same key "
                     "as ambient_c (enclosure air) — sharing one 'ambient' across a building "
                     "and its outside is a units-grade error that reads as a plausible number")

    try:
        col = P.hottel_whillier(
            float(vals["collector_area_m2"]), float(vals["collector_fr"]),
            float(vals["collector_tau_alpha"]), float(vals["collector_ul_w_m2k"]),
            float(irradiance), float(vals["collector_inlet_c"]),
            float(vals["collector_ambient_c"]))
    except ValueError as exc:
        return _skip(gid, ["collector_area_m2"], str(exc))

    q = col["q_useful_w"]
    q_min = float(vals["collector_output_min_w"])

    # The collector's efficiency curve: eta(dT) = F_R*(tau_alpha - U_L*dT/G).
    # One line each, because the slope is the number a reader compares against a
    # datasheet and the intercept is the one they compare against the optics.
    area = float(vals["collector_area_m2"])
    f_r = float(vals["collector_fr"])
    ta = float(vals["collector_tau_alpha"])
    u_l = float(vals["collector_ul_w_m2k"])
    g = float(irradiance)
    sweep = "\n".join(
        f"dT={dt:5.0f} K  eta={f_r * (ta - u_l * dt / g):6.3f}  "
        f"Q={f_r * area * (ta * g - u_l * dt):8.1f} W"
        for dt in (0, 10, 20, 30, 40, 50, 60, 80))
    evidence = _write(
        ctx, "collector_curve.txt",
        f"Hottel-Whillier at G={g:.0f} W/m2, A={area:.2f} m2\n"
        f"F_R={f_r:g} tau_alpha={ta:g} U_L={u_l:g} W/m2K\n{sweep}\n"
        f"critical irradiance (zero output at dT={col['dt_k']:.0f} K): "
        f"{col['critical_irradiance_w_m2']:.0f} W/m2\n"
        f"stagnation (enforced by solar.stagnation): {col['stagnation_c']:.1f} C\n")

    return Verdict(
        gate=gid, passed=q >= q_min, measured=round(q, 1),
        limit=round(q_min, 1), comparator=">=", units="W",
        detail=f"{q:.0f} W useful vs {q_min:.0f} W required (eta={col['efficiency']:.3f}); "
               f"G={g:.0f} W/m2, dT={col['dt_k']:.0f} K, zero output below "
               f"G={col['critical_irradiance_w_m2']:.0f} W/m2; stagnation would be "
               f"{col['stagnation_c']:.0f} C — enforced by solar.stagnation",
        evidence=evidence,
    )


@gate(
    id="solar.stagnation",
    title="Absorber temperature with no flow and full sun, against what the loop survives",
    claims=["solar-thermal", "collector-stagnation", "stagnation-temperature",
            "material-limit"],
    tier=Tier.INSTANT,
    settles="collector stagnation temperature",
    negative_control=NegativeControl(
        fixture="selftest/bad_thermal.py:better_insulated_collector",
        note="the same collector with its loss coefficient U_L cut — the back "
             "insulation doubled, the glazing gap sealed — at identical optics, area "
             "and flow: a BETTER collector, and it stagnates clear past what the "
             "seals and the heat transfer fluid survive",
    ),
)
def stagnation(ctx: GateContext) -> Verdict:
    """The temperature the absorber reaches with the pump off.

        T_stag = T_amb + tau_alpha*G / U_L

    It is its own gate because it is its own question. It depends on the optical
    gain over the loss coefficient and on NOTHING else — not area, not F_R, not
    flow rate, not the store — so a collector can pass every performance check it
    was given and destroy itself on the first hot afternoon the pump is off.

    And the inversion is the reason the pack insists on it: *the better the
    collector, the hotter it stagnates*. Better insulation lowers U_L, which
    raises output and raises stagnation together. Better optics raise tau_alpha,
    which does the same. Only a bigger collector is free. A leaky collector is a
    safe one, which is the opposite of every instinct a designer brings.

    ``stagnation_limit_c`` is REQUIRED and the gate skips without it. That is
    this pack having an opinion: the limit is what the SEALS, the heat transfer
    fluid and the absorber coating survive with no flow, it is nowhere in a
    performance datasheet, and a model that has not written it down has not been
    asked the question. Defaulting a number on its behalf would answer it wrongly
    and silently. Inhibited propylene glycol degrades irreversibly somewhere
    around 120-140 degC into acidic products that then attack the loop, so the
    fluid is usually the binding limit rather than the gasket.
    """
    gid = "solar.stagnation"
    irradiance = ctx.param("irradiance_w_m2", ctx.param("poa_irradiance_w_m2", None))
    vals, missing = _need(ctx, "collector_tau_alpha", "collector_ul_w_m2k",
                          "collector_ambient_c", "stagnation_limit_c")
    if irradiance is None:
        missing.append("irradiance_w_m2")
    if missing:
        return _skip(gid, missing,
                     "stagnation_limit_c is what the seals, heat transfer fluid and absorber "
                     "coating survive with NO flow, degC — there is no safe default for it, "
                     "and it is not the same number as the collector's operating temperature")

    ta = float(vals["collector_tau_alpha"])
    u_l = float(vals["collector_ul_w_m2k"])
    amb = float(vals["collector_ambient_c"])
    limit = float(vals["stagnation_limit_c"])
    g = float(irradiance)
    if u_l <= 0.0 or ta <= 0.0:
        return _skip(gid, ["collector_ul_w_m2k"],
                     f"tau_alpha and U_L must both be > 0, got {ta} and {u_l}")

    stag = amb + ta * g / u_l
    headroom = limit - stag
    return Verdict(
        gate=gid, passed=stag <= limit, measured=round(stag, 1), limit=round(limit, 1), comparator="<=",
        units="degC",
        detail=f"stagnation {stag:.0f} C vs {limit:.0f} C survivable ({headroom:+.0f} K "
               f"headroom) = {amb:.0f} C air + {ta:g}*{g:.0f}/{u_l:g}; independent of area "
               f"and of F_R, and U_L is held constant, so this is a "
               f"CONSERVATIVE bound — real U_L climbs with absorber temperature",
    )


@gate(
    id="solar.irradiance",
    title="Clear-sky plane-of-array irradiance from site, date, hour and orientation",
    claims=["irradiance", "solar-resource", "siting", "orientation"],
    tier=Tier.INSTANT,
    settles="plane-of-array irradiance",
    negative_control=NegativeControl(
        fixture="selftest/bad_thermal.py:north_facing",
        note="the array is mounted facing away from the sun: the surface azimuth swings "
             "180 deg at the same site, day, hour and albedo, and the plane is tipped "
             "further over if a near-horizontal tilt leaves the azimuth unable to move "
             "POA at all. The beam term collapses — to exactly zero once the incidence "
             "angle passes 90 deg — and POA lands clear below the design minimum",
    ),
)
def irradiance(ctx: GateContext) -> Verdict:
    """ASHRAE clear-day beam + isotropic sky diffuse + ground reflection on a tilt.

    MODEL AND ERROR BAND, stated because a number without one is not evidence.
    This is the ASHRAE clear-day (Hottel-form) model: beam normal
    ``I_bn = CN * A * exp(-B / sin(altitude))`` with monthly A, B, C coefficients,
    an isotropic sky-diffuse transposition, and an isotropic ground reflection.
    Against measured clear-day data at mid-latitudes near sea level it runs
    roughly +-10% on beam and worse than that on diffuse, which is itself a small
    term on a tilted plane. It under-predicts at altitude (the coefficients assume
    a sea-level, moderately dusty atmosphere) and over-predicts in humid or hazy
    air.

    It is a CLEAR-SKY model. It has no opinion at all about cloud, and therefore
    about actual energy yield: this gate answers "is the geometry right?", never
    "how much will it collect this year?". For a yield number you need a measured
    or satellite-derived weather series - see references/limits.md.

    FRAME: ``solar_hour`` is solar time, not clock time. ``surface_azimuth_deg``
    is 0 = due south, +90 = west, -90 = east; in the southern hemisphere the
    equator-facing orientation is 180.
    """
    gid = "solar.irradiance"
    vals, missing = _need(ctx, "latitude_deg", "day_of_year", "solar_hour",
                          "surface_tilt_deg", "surface_azimuth_deg",
                          "design_irradiance_min_w_m2")
    if missing:
        return _skip(gid, missing,
                     "solar_hour is SOLAR time (noon = sun on the meridian), not clock time; "
                     "surface_azimuth_deg is 0 due south, +90 west")

    albedo_param = ctx.param("ground_albedo", None)
    albedo = DEFAULT_ALBEDO if albedo_param is None else float(albedo_param)
    clearness = float(ctx.param("clearness_number", 1.0) or 1.0)
    lat = float(vals["latitude_deg"])
    if not (-90.0 <= lat <= 90.0):
        return _skip(gid, ["latitude_deg"], f"must be within +-90, got {lat}")

    sky = P.clear_sky_poa(lat, int(vals["day_of_year"]), float(vals["solar_hour"]),
                          float(vals["surface_tilt_deg"]),
                          float(vals["surface_azimuth_deg"]), albedo, clearness)
    poa = sky["poa_w_m2"]
    limit = float(vals["design_irradiance_min_w_m2"])

    profile = []
    for hour in range(4, 21):
        h_sky = P.clear_sky_poa(lat, int(vals["day_of_year"]), float(hour),
                                float(vals["surface_tilt_deg"]),
                                float(vals["surface_azimuth_deg"]), albedo, clearness)
        profile.append(f"{hour:02d}:00 solar  alt={h_sky['altitude_deg']:6.1f} deg  "
                       f"POA={h_sky['poa_w_m2']:7.1f} W/m2  "
                       f"(beam {h_sky['beam_w_m2']:6.1f}, diff {h_sky['diffuse_w_m2']:5.1f}, "
                       f"grnd {h_sky['ground_w_m2']:5.1f})")
    evidence = _write(
        ctx, "clear_sky_profile.txt",
        f"ASHRAE clear-day model, lat {lat:.2f}, day {int(vals['day_of_year'])} "
        f"(month {sky['month']}), tilt {float(vals['surface_tilt_deg']):.0f} deg, "
        f"azimuth {float(vals['surface_azimuth_deg']):.0f} deg, albedo {albedo:.2f}, "
        f"CN {clearness:g}\ndeclination {sky['declination_deg']:.2f} deg\n"
        + "\n".join(profile) + "\nCLEAR SKY ONLY - says nothing about weather.\n")

    albedo_note = "albedo declared" if albedo_param is not None else \
        f"albedo {DEFAULT_ALBEDO:.2f} ASSUMED (not declared)"
    if sky["note"]:
        detail = (f"{poa:.0f} W/m2 vs {limit:.0f} required — {sky['note']} at "
                  f"{float(vals['solar_hour']):.2f} solar h, day {int(vals['day_of_year'])}, "
                  f"altitude {sky['altitude_deg']:.1f} deg")
    else:
        detail = (f"POA {poa:.0f} W/m2 vs {limit:.0f} required "
                  f"(beam {sky['beam_w_m2']:.0f} + diffuse {sky['diffuse_w_m2']:.0f} + "
                  f"ground {sky['ground_w_m2']:.0f}); sun alt {sky['altitude_deg']:.1f} deg "
                  f"az {sky['solar_azimuth_deg']:.1f} deg, incidence "
                  f"{sky['incidence_deg']:.1f} deg, AM {sky['air_mass']:.2f}; "
                  f"ASHRAE clear-day +-10%, {albedo_note}")
    return Verdict(
        gate=gid, passed=poa >= limit, measured=round(poa, 1), limit=round(limit, 1), comparator=">=",
        units="W/m2", detail=detail, evidence=evidence,
    )


# --------------------------------------------------------------------------- #
# agreement across representations (method rule 6)
# --------------------------------------------------------------------------- #
#: How far the h a model hands the lumped gates may drift from the h the
#: correlation computes for the same surface before the two stop describing one
#: object. 10% is the number because it is comfortably inside the correlations'
#: own accuracy (Churchill-Chu and the flat-plate forms are quoted at +-15-20%
#: on h) while being far tighter than any real staleness: the cases this catches
#: — a velocity edited without re-deriving h, a surface temperature moved, a
#: geometry swapped — move h by 30% and upward. 1% was tried and rejected: it
#: fails on honest rounding of a hand-transcribed h, which trains people to raise
#: the tolerance rather than fix the coupling.
H_AGREEMENT_TOL = 0.10


@gate(
    id="thermal.h_agreement",
    title="The h handed to the lumped gates is the h this pack's correlation computes",
    claims=["cross-representation", "heat-transfer-coefficient", "convection",
            "thermal-response"],
    tier=Tier.INSTANT,
    settles="convective coefficient agreement",
    negative_control=NegativeControl(
        fixture="selftest/bad_thermal.py:stale_h",
        note="the airflow over the body was changed in the model and body_h_w_m2k was "
             "left at its old value: the surface, the geometry and the correlation are "
             "untouched, and the declared h simply no longer matches the one they give",
    ),
)
def h_agreement(ctx: GateContext) -> Verdict:
    """Rule 6, for the one number this pack states twice.

    ``thermal.convection`` computes an h from a geometry, a length, a fluid and a
    driving flow. ``thermal.time_constant`` is HANDED an h — ``body_h_w_m2k`` —
    and uses it for both the Biot number and the time constant. Nothing forces
    those to be the same number, and in every real model they start out the same
    and then drift: somebody raises the fan speed, or moves the surface
    temperature, or changes the characteristic length, and the derived h is
    re-derived while the transcribed one is not. Every downstream verdict stays
    green because each gate is internally consistent; the two are just no longer
    describing one object.

    This gate recomputes h from the convection inputs and refuses when the
    declared one disagrees by more than ``h_agreement_tol`` (default 10%). It is
    deliberately a SEPARATE gate rather than a check inside ``thermal.convection``
    — the two questions fail for different reasons and want different fixtures,
    and folding this in would mean a correlation gate that goes red because of a
    number the correlation never touched.

    WHAT IT DOES NOT RECONCILE, since an unstated exclusion is the whole problem
    this gate exists to fix:

    * ``fin_h_w_m2k`` — on a liquid cold plate or a two-stream exchanger that is
      a different fluid on a different surface, so agreement with the air-side
      correlation would be WRONG, not merely unchecked. If your fin sits in the
      same stream as the body, state the same number and compare them by eye.
    * ``surface_c`` against ``thermal.steady_state_temp``'s network. Reconciling
      them needs to know which node of ``resistance_path_k_w`` is the
      surface-to-fluid one, and guessing that from node order or node names is
      the kind of inference that is right until it silently is not.
    * the sink node's resistance against ``1/(h*A)``, for the same reason: the
      area that node was computed over is nowhere in the projection.
    """
    gid = "thermal.h_agreement"
    vals, missing = _need(ctx, "convection_mode", "convection_geometry",
                          "char_length_m", "surface_c", "ambient_c", "body_h_w_m2k")
    if missing:
        return _skip(gid, missing,
                     "this gate reconciles body_h_w_m2k against the h thermal.convection "
                     "computes, so it needs both sides; a model that states only one of "
                     "them has nothing to reconcile")

    mode = str(vals["convection_mode"]).strip().lower()
    geometry = str(vals["convection_geometry"]).strip().lower()
    length = float(vals["char_length_m"])
    t_s = float(vals["surface_c"])
    t_inf = float(vals["ambient_c"])
    declared = float(vals["body_h_w_m2k"])
    fluid = str(ctx.param("fluid", "air") or "air")
    tol = float(ctx.param("h_agreement_tol", H_AGREEMENT_TOL))

    if mode not in ("natural", "forced") or length <= 0.0 or declared <= 0.0:
        return _skip(gid, ["convection_mode", "char_length_m", "body_h_w_m2k"],
                     f"mode {mode!r}, L {length}, declared h {declared} — all must be "
                     f"usable before the two representations can be compared")

    film_k = P.c_to_k((t_s + t_inf) / 2.0)
    try:
        props = P.fluid_properties(fluid, film_k)
        if mode == "natural":
            ra = P.rayleigh(t_s - t_inf, length, props)
            corr = P.natural_nusselt(geometry, ra, props["pr"], t_s - t_inf)
        else:
            v_vals, v_missing = _need(ctx, "velocity_m_s")
            if v_missing:
                return _skip(gid, v_missing, "forced convection needs a flow velocity")
            corr = P.forced_nusselt(
                geometry, float(v_vals["velocity_m_s"]) * length / props["nu"], props["pr"])
    except ValueError as exc:
        return _skip(gid, ["convection_geometry", "fluid"], str(exc))

    if not props["in_range"]:
        return _skip(gid, ["surface_c", "ambient_c"],
                     f"film temperature {film_k:.1f} K is outside the tabulated {fluid} "
                     f"properties — thermal.convection refuses this case, so there is no "
                     f"computed h to reconcile against")

    computed = corr["nu"] * props["k"] / length
    drift = abs(declared - computed) / computed if computed > 0 else float("inf")
    direction = "optimistic" if declared > computed else "pessimistic"
    band = "" if corr["valid"] else (" — note the correlation is OUTSIDE its band here, "
                                     "so thermal.convection is also refusing this case")
    return Verdict(
        gate=gid, passed=drift <= tol, measured=round(drift, 4), limit=round(tol, 4), comparator="<=",
        units="relative drift",
        detail=f"body_h_w_m2k={declared:.1f} vs {computed:.1f} W/m2K from "
               f"{corr['correlation']} ({mode} {geometry}, L={length:.3f} m, "
               f"Tfilm={film_k:.1f} K): {drift * 100:.1f}% {direction} against a "
               f"{tol * 100:.0f}% tolerance{band}",
    )
