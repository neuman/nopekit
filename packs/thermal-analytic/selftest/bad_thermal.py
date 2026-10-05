# SPDX-License-Identifier: Apache-2.0
"""Known-bad fixtures for thermal-analytic. One physical change each.

Every function returns a GateContext carrying the pack's own baseline with
exactly one physically meaningful quantity moved, in the direction the gate
under test measures. That constraint is the whole value of the file: a fixture
that is bad in some other way — an empty dict, a missing key, a negative area —
proves the gate handles garbage, not that it measures heat.

`nopekit gate selftest` requires every gate to FAIL here. A gate that passes its
own fixture is reported as broken, and a green verdict from it means nothing.

---------------------------------------------------------------------------
SEALED FIXTURES
---------------------------------------------------------------------------
A control must fire in EVERY project, not just a friendly one. Layering the
known-bad values over the host project's projection looks safe — the override
wins on every key it states — but gates resolve synonym families and derived
quantities, so a key the fixture never mentions can still arrive from the
project and neutralise the control.

It was observed live in a sibling pack: a fixture raised a hull's centre of
gravity to make it unstable, the host project happened to state a waterplane
inertia (an honest thing to state, better than the pack's own fallback), and
the gate PASSED ITS OWN KNOWN-BAD FIXTURE. A control whose severity depends on
the host project's numbers passes in some repositories and fails in others,
which is the same as having none.

So the base here is the pack's OWN baseline.json and NOTHING is inherited —
including the quantity each fixture perturbs, which earlier versions of this
file still read off ``ctx.params`` before sealing everything around it.
``tests/test_packs.py::ControlsAreSealed`` runs every control against an empty
projection to keep it that way.

---------------------------------------------------------------------------
AND SEALED IS NOT THE SAME AS HARD-CODED
---------------------------------------------------------------------------
The second discipline, which is separate and was the real defect here. Sealing
fixes *which model* the control is computed against. It says nothing about
whether the perturbation is actually BIG ENOUGH to cross the threshold it is
being tested against, and a fixture that writes a literal — ``k = 0.17``,
``eps = 0.90``, ``+2.8 K/W`` — is only guaranteed to cross the limits the
baseline happened to ship with. Change one threshold in baseline.json, or lift
these fixtures into a project whose limits are looser, and the control quietly
stops falsifying anything while still reporting that it did.

So every fixture below computes the value it needs FROM THE LIMIT THE GATE WILL
COMPARE AGAINST, reads that limit out of the baseline, and takes the worse of
that and the physically named fault. Two things fall out of it:

* the named fault (an unsealed cavity, a polymer fin, a dry joint) is what
  actually runs on the shipped baseline, so the control still tells a physical
  story rather than an arithmetic one; and
* on any other set of limits the fixture escalates until the gate must refuse,
  so the control cannot be defused by a threshold edit.

``MARGIN`` is how far past the limit each fixture must land.
"""
from __future__ import annotations

import copy
import dataclasses
import json as _json
import os as _os
import sys as _sys

# The pack's physics module, so a fixture computes its severity with the SAME
# arithmetic the gate will judge it by (rule 2). A fixture with its own private
# copy of the Nusselt correlations is a second source of truth that drifts, and
# it drifts silently because nothing downstream reads it.
_GATES_DIR = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))),
                           "gates")
if _GATES_DIR not in _sys.path:
    _sys.path.insert(0, _GATES_DIR)

import _thermal_physics as P                                        # noqa: E402

#: How far past its limit each fixture must drive the measured quantity. 15% is
#: the margin: large enough that no rounding, property-table interpolation or
#: correlation-band edge can put the known-bad case back inside the limit, small
#: enough that the perturbation stays recognisably the physical fault it is named
#: after. 2% was tried and rejected — the conduction stack alone moves U by more
#: than that between two honest surface-film conventions, so a 2% control would
#: be falsifying the film assumption rather than the wall.
MARGIN = 1.15

#: Effective conductivity of an unsealed air cavity once internal convection is
#: counted, W/mK. The physical story behind `insulation_omitted`: the cavity was
#: detailed and then nobody filled it. A still-air value (~0.026) was rejected —
#: an empty wall cavity is never still air, and using it would overstate how much
#: the omission costs.
AIR_CAVITY_K = 0.17

#: Conductivity of a filled engineering polymer, W/mK. The story behind
#: `plastic_fin` and `moulded_housing`: the part was value-engineered into a
#: mould. Unfilled polymers run 0.15-0.25; a conductive-filled compound reaches
#: 1-3 and is a different (and much rarer) decision, so 0.25 is the honest
#: worst-realistic case rather than the most dramatic one.
POLYMER_K = 0.25

#: A small package bolted dry to a sink, K/W. Roughly what a flat-to-flat joint
#: with no TIM measures on a TO-220-sized footprint: real contact is a few
#: percent of apparent area and the rest is an air gap. The greased or padded
#: value is nearer 0.3, which is the 9x this file applies where a named
#: interface node exists.
DRY_JOINT_R = 3.0

_BASELINE_PATH = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)),
                               "baseline.json")


def _baseline() -> dict:
    """The pack's own plausible-good projection, documentation keys stripped."""
    try:
        with open(_BASELINE_PATH, "r", encoding="utf-8") as handle:
            loaded = _json.load(handle)
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in loaded.items() if not k.startswith("_")}


def _with(ctx, **overrides):
    """A copy of ctx whose params are the BASELINE plus these overrides."""
    params = _baseline()
    params.update(overrides)
    return dataclasses.replace(ctx, params=params)


def _num(params: dict, key: str, default: float) -> float:
    value = params.get(key, default)
    try:
        return float(default if value is None else value)
    except (TypeError, ValueError):
        return float(default)


# --------------------------------------------------------------------------- #
def insulation_omitted(ctx):
    """thermal.conduction — the insulation was left out of the cavity.

    Finds the layer carrying the LARGEST share of the stack's resistance (that is
    the insulation, by definition, and it is a better target than the lowest-k
    element: a 5%-area rib can be the lowest k in the stack while carrying almost
    none of the resistance) and raises the conductivity of every element in it by
    one common factor. Thicknesses, framing fractions, films and area are
    untouched, so the U-value moves for exactly one reason.

    The factor is the worse of two numbers: the one that turns the layer's lowest
    conductivity into an unsealed air cavity, and the one that lands the whole
    wall at MARGIN x its own declared U-limit. The first is the physical story
    and is what runs on this baseline; the second is what keeps the control alive
    on a wall this pack has never seen, including one whose limit is generous.
    """
    base = _baseline()
    layers = copy.deepcopy(list(base.get("wall_layers") or []))
    if not layers:
        return _with(ctx)

    try:
        resistances = [P.layer_resistance(layer)[0] for layer in layers]
    except (ValueError, TypeError, KeyError, AttributeError):
        return _with(ctx, wall_layers=layers)

    index = max(range(len(layers)), key=lambda i: resistances[i])
    layer = layers[index]
    r_layer = resistances[index]
    r_other = _num(base, "surface_film_r_m2k_w", 0.0) + sum(
        r for i, r in enumerate(resistances) if i != index)

    branches = layer.get("parallel")
    ks = ([float(b.get("k_w_mk", 1.0)) for b in branches] if branches
          else [float(layer.get("k_w_mk", 1.0))])
    k_min = min(ks) if ks else 1.0

    # (a) the physical story: that element becomes an unsealed air cavity.
    factor_physical = max(1.0, AIR_CAVITY_K / k_min) if k_min > 0 else 1.0

    # (b) the threshold: whatever it takes to put U at MARGIN x the declared
    #     limit. R scales as 1/factor for a series layer and for a parallel layer
    #     whose branches all scale together, so the algebra is the same for both.
    limit = _num(base, "wall_u_limit_w_m2k", 0.0)
    factor_needed = 1.0
    if limit > 0.0 and r_layer > 0.0:
        r_budget = 1.0 / (MARGIN * limit) - r_other
        factor_needed = (r_layer / r_budget) if r_budget > 0.0 else 1.0e4

    factor = max(1.0, factor_physical, factor_needed)
    if branches:
        for branch in branches:
            branch["k_w_mk"] = float(branch.get("k_w_mk", 1.0)) * factor
    else:
        layer["k_w_mk"] = float(layer.get("k_w_mk", 1.0)) * factor
    layer["name"] = f"{layer.get('name', 'layer')} (insulation omitted)"
    return _with(ctx, wall_layers=layers)


def _h_of(params: dict) -> float | None:
    """The h thermal.convection would report for this projection, W/m2K.

    Same functions, same property table, same correlation choice as the gate, so
    a fixture cannot be severe by an arithmetic the gate does not share.
    """
    try:
        t_s = float(params["surface_c"])
        t_inf = float(params["ambient_c"])
        length = float(params["char_length_m"])
        geometry = str(params["convection_geometry"])
        props = P.fluid_properties(str(params.get("fluid", "air") or "air"),
                                   P.c_to_k((t_s + t_inf) / 2.0))
        if str(params.get("convection_mode", "forced")).strip().lower() == "natural":
            corr = P.natural_nusselt(geometry, P.rayleigh(t_s - t_inf, length, props),
                                     props["pr"], t_s - t_inf)
        else:
            corr = P.forced_nusselt(
                geometry, float(params["velocity_m_s"]) * length / props["nu"], props["pr"])
        return corr["nu"] * props["k"] / length
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return None


def _collapse_fraction(build, target_h: float) -> float:
    """Largest fraction of the driving quantity whose h is still <= ``target_h``.

    h is monotone in both drivers this is used on — the approach velocity for
    forced convection, the surface-to-fluid difference for natural — so there is
    exactly one crossing and a bisection finds it without caring which
    correlation branch the search passes through. 60 halvings is far past the
    precision of a float and costs microseconds.
    """
    if (_h_of(build(1.0)) or 0.0) <= target_h:
        return 1.0
    lo, hi = 0.0, 1.0
    for _ in range(60):
        mid = (lo + hi) / 2.0
        h = _h_of(build(mid))
        if h is None:
            return lo
        if h > target_h:
            hi = mid
        else:
            lo = mid
    return lo


def fan_stopped(ctx):
    """thermal.convection — the driving agency for convection collapses.

    Forced: the approach velocity falls to a tenth of design (the fan has stopped
    and only the residual draught is left). h goes as sqrt(V) in laminar flow, so
    a tenth of the velocity is about a third of the h and nothing else about the
    surface or the fluid changes.

    Natural: there is no velocity to remove, so the buoyancy driving the flow
    goes instead — the surface-to-fluid difference collapses by the same factor.
    h goes only as dT^(1/4) there, so this is a smaller move in h and a large one
    in Rayleigh, which usually lands below the band the correlation was fitted
    over; the gate must refuse that too, and does.

    Either branch is escalated past a tenth if a tenth would not carry h clear
    below the h the project declared it needs.
    """
    base = _baseline()
    h_required = _num(base, "h_required_w_m2k", 0.0)
    target = h_required / MARGIN if h_required > 0 else 0.0
    mode = str(base.get("convection_mode", "forced")).strip().lower()

    if mode == "natural":
        ambient = _num(base, "ambient_c", 20.0)
        dt = _num(base, "surface_c", ambient + 20.0) - ambient

        def build(f):
            return dict(base, surface_c=ambient + dt * f)

        fraction = min(0.1, _collapse_fraction(build, target))
        return _with(ctx, surface_c=ambient + dt * fraction)

    velocity = _num(base, "velocity_m_s", 1.0)

    def build(f):
        return dict(base, velocity_m_s=velocity * f)

    fraction = min(0.1, _collapse_fraction(build, target))
    return _with(ctx, velocity_m_s=velocity * fraction)


def coating_degraded(ctx):
    """thermal.radiation — the surface's emissivity moves the wrong way.

    Which way is wrong depends on what the surface is for, and the fixture reads
    ``rad_sense`` to find out. A solar absorber (sense 'max') is keeping radiative
    loss down, so its selective coating degrading to a plain black surface is the
    failure. A passively cooled enclosure (sense 'min') is trying to radiate heat
    away, so the failure is the opposite: a bare polished surface where the black
    anodising was left off. Temperatures, area and view factor are identical in
    both.

    The emissivity is then pushed further, in whichever of those two directions
    applies, until the radiated power is clear of ``rad_limit_w`` — bounded by
    the physics at eps 1.0 and eps 0.01, since no coating decision can rescue a
    limit that the surface cannot reach at either extreme.
    """
    base = _baseline()
    sense = str(base.get("rad_sense", "max")).strip().lower()
    limit = _num(base, "rad_limit_w", 0.0)

    def q_at(eps: float) -> float | None:
        try:
            area2 = base.get("area_surround_m2")
            result = P.radiation_exchange(
                P.c_to_k(_num(base, "rad_surface_c", 80.0)),
                P.c_to_k(_num(base, "rad_surround_c", 20.0)),
                _num(base, "rad_area_m2", 1.0), eps,
                _num(base, "view_factor", 1.0),
                _num(base, "emissivity_surround", 1.0),
                float(area2) if area2 else None)
            return result["q_w"]
        except (ValueError, TypeError):
            return None

    lo, hi = 0.01, 1.0                       # q is monotone increasing in eps1
    if sense == "min":
        eps = 0.05                           # bare polished metal
        target = limit / MARGIN              # rejection must fall clear BELOW it
        for _ in range(60):
            mid = (lo + hi) / 2.0
            q = q_at(mid)
            if q is None:
                break
            if q > target:
                hi = mid
            else:
                lo = mid
        return _with(ctx, emissivity=max(0.01, min(eps, lo)))

    eps = 0.90                               # plain black, the coating gone
    target = limit * MARGIN                  # loss must rise clear ABOVE it
    for _ in range(60):
        mid = (lo + hi) / 2.0
        q = q_at(mid)
        if q is None:
            break
        if q < target:
            lo = mid
        else:
            hi = mid
    return _with(ctx, emissivity=min(1.0, max(eps, hi)))


def plastic_fin(ctx):
    """thermal.fin_efficiency — the same fin moulded in a polymer.

    Geometry, h and count are untouched; only the conductivity drops.
    m = sqrt(hP/(kA_c)) explodes, the tip stops participating, and effectiveness
    collapses — on this baseline below 1, the case where the fin removes less
    heat than the bare base it stands on, which is the surprise this gate exists
    to report.

    The conductivity used is the lower of a moulded polymer and whatever value
    puts the effectiveness CEILING, sqrt(kP/(hA_c)), below the project's own
    effectiveness floor. Attacking the ceiling rather than the effectiveness is
    deliberate: effectiveness can never exceed it, so a ceiling under the floor
    is a failure no fin length, and no tuning of the other inputs, can undo.
    """
    base = _baseline()
    k = _num(base, "fin_k_w_mk", 200.0)
    floor = _num(base, "fin_effectiveness_min", 2.0)
    k_needed = k
    try:
        fin = P.straight_fin(k, _num(base, "fin_thickness_m", 0.001),
                             _num(base, "fin_length_m", 0.01),
                             _num(base, "fin_width_m", 0.1),
                             _num(base, "fin_h_w_m2k", 100.0))
        ceiling = fin["eps_ceiling"]
        if ceiling > 0.0:
            # ceiling scales as sqrt(k), so this is exact rather than iterative.
            k_needed = k * (floor / (MARGIN * ceiling)) ** 2
    except (ValueError, TypeError):
        pass
    return _with(ctx, fin_k_w_mk=max(1e-4, min(POLYMER_K, k_needed)))


def dry_joint(ctx):
    """thermal.steady_state_temp — the thermal interface material is omitted.

    When the path NAMES an interface node, that node goes up 9x: a greased or
    padded joint on a small package is roughly 0.3 K/W and the same joint bolted
    dry is roughly 3, which is the ratio this applies.

    When no node matches, the fixture INSERTS a new node called 'dry interface,
    no TIM' rather than inflating and relabelling whichever node happened to be
    smallest. Relabelling was the previous behaviour and it was wrong twice over:
    on a path of die / base / sink it attacked the die, which is not an interface
    and cannot be one, and the resulting verdict named a node the model does not
    contain. A missing interface ADDS resistance to a path; it does not multiply
    a node that was already there.

    Either way the added resistance is at least what it takes to carry the part
    past its own temperature limit with MARGIN to spare, so a generous limit or a
    long path cannot absorb it. Power, ambient, the limit and every other node
    are unchanged. The same applies to the ``total_resistance_k_w`` shape, where
    there is no node to attack and the missing interface is simply added on.
    """
    base = _baseline()
    power = _num(base, "power_w", 0.0)
    ambient = _num(base, "ambient_c", 20.0)
    temp_limit = _num(base, "temp_limit_c", 0.0)

    r_needed_total = 0.0
    if power > 0.0 and temp_limit > ambient:
        r_needed_total = MARGIN * (temp_limit - ambient) / power

    path = base.get("resistance_path_k_w")
    if path:
        nodes = copy.deepcopy(list(path))
        try:
            r_base = P.series_resistance(nodes)["r_total_k_w"]
        except (ValueError, TypeError, KeyError, AttributeError):
            r_base = 0.0
        extra_needed = max(0.0, r_needed_total - r_base)

        keywords = ("interface", "tim", "grease", "pad", "paste", "joint", "contact")
        index = next(
            (i for i, n in enumerate(nodes)
             if any(w in str(n.get("name", "")).lower() for w in keywords)),
            None,
        )
        if index is None:
            nodes.append({"name": "dry interface, no TIM",
                          "r_k_w": max(DRY_JOINT_R, extra_needed)})
        else:
            existing = float(nodes[index].get("r_k_w", 0.3) or 0.3)
            nodes[index] = dict(
                nodes[index],
                r_k_w=existing + max(8.0 * existing, extra_needed),
                name=f"{nodes[index].get('name', 'interface')} (dry, no TIM)")
        return _with(ctx, resistance_path_k_w=nodes)

    total = _num(base, "total_resistance_k_w", 1.0)
    return _with(ctx,
                 total_resistance_k_w=total + max(DRY_JOINT_R,
                                                  max(0.0, r_needed_total - total)))


def moulded_housing(ctx):
    """thermal.time_constant — the same body in a polymer instead of a metal.

    Size, surface area, density, specific heat and h are untouched, so L_c is
    identical and Biot rises by exactly the conductivity ratio — past the licence,
    where the body stops having one temperature and the lumped time constant
    stops describing anything.

    The conductivity is the lower of a moulded polymer and the value that puts
    Bi = h*L_c/k at MARGIN x the project's own Biot ceiling, so a project that
    has relaxed ``biot_limit`` cannot defuse the control by doing so.
    """
    base = _baseline()
    volume = _num(base, "body_volume_m3", 1e-4)
    area = _num(base, "body_area_m2", 0.1)
    h = _num(base, "body_h_w_m2k", 10.0)
    bi_limit = _num(base, "biot_limit", 0.1)
    k_needed = POLYMER_K
    if area > 0.0 and bi_limit > 0.0:
        k_needed = h * (volume / area) / (MARGIN * bi_limit)
    return _with(ctx, body_k_w_mk=max(1e-4, min(POLYMER_K, k_needed)))


def stale_h(ctx):
    """thermal.h_agreement — the model's h was left behind by its own inputs.

    The one change is ``body_h_w_m2k``: the surface, the geometry, the fluid and
    the flow all stay exactly as the baseline states them, and only the
    transcribed coefficient moves. That is the defect in its real form — somebody
    edits the velocity or the characteristic length, the correlation re-derives,
    the hand-carried number does not, and every gate stays green because each one
    is internally consistent.

    The offset is taken from the gate's own tolerance rather than from a fixed
    percentage, so tightening or loosening ``h_agreement_tol`` cannot leave the
    control on the wrong side of it. The direction is upward: an h that is too
    HIGH is the optimistic error, and the one that produces a Biot number and a
    time constant that both look better than the part will manage.
    """
    base = _baseline()
    computed = _h_of(base)
    if computed is None or computed <= 0.0:
        return _with(ctx)
    tolerance = _num(base, "h_agreement_tol", 0.10)
    return _with(ctx, body_h_w_m2k=computed * (1.0 + MARGIN * tolerance))


def waterlogged_insulation(ctx):
    """solar.collector_output — the collector stops delivering its duty.

    Normal case: the back insulation has taken up water and U_L triples. Optics,
    area, F_R, inlet temperature and irradiance are unchanged, so only the loss
    term moves and useful heat falls below the required minimum. U_L is raised
    further than 3x if 3x would not clear the duty with MARGIN.

    There is one configuration where raising U_L is the WRONG direction and the
    fixture says so rather than quietly producing a harmless perturbation: when
    the inlet sits at or below ambient (an unglazed pool loop, a preheat running
    off mains water on a warm day) the loss term is a GAIN and a leakier
    collector delivers more, not less. There the physical fault is on the other
    side of the equation — the glazing has fouled or the absorber coating has
    chalked — so tau_alpha is what collapses. One change either way; the branch
    is chosen by the sign of (T_in - T_amb), not by taste.
    """
    base = _baseline()
    f_r = _num(base, "collector_fr", 0.8)
    area = _num(base, "collector_area_m2", 2.0)
    tau_alpha = _num(base, "collector_tau_alpha", 0.75)
    u_l = _num(base, "collector_ul_w_m2k", 4.0)
    g = _num(base, "irradiance_w_m2", _num(base, "poa_irradiance_w_m2", 900.0))
    dt = _num(base, "collector_inlet_c", 50.0) - _num(base, "collector_ambient_c", 20.0)
    q_min = _num(base, "collector_output_min_w", 0.0)

    # The useful heat the fixture has to get BELOW, per unit of F_R*A.
    q_target = q_min / MARGIN
    gain_budget = (q_target / (f_r * area)) if f_r * area > 0 else 0.0

    if dt > 0.0:
        u_needed = (tau_alpha * g - gain_budget) / dt
        return _with(ctx, collector_ul_w_m2k=max(3.0 * u_l, u_needed))

    ta_needed = ((gain_budget + u_l * dt) / g) if g > 0 else 0.0
    return _with(ctx,
                 collector_tau_alpha=max(1e-4, min(tau_alpha / 3.0, ta_needed)))


def better_insulated_collector(ctx):
    """solar.stagnation — the collector was improved, and that is the failure.

    U_L is cut: the back insulation doubled, the glazing gap sealed, the edge
    losses detailed out. Optics, area, F_R, flow and irradiance are identical,
    and the collector's useful output goes UP. The absorber temperature with no
    flow goes up with it — T_stag = T_amb + tau_alpha*G/U_L — clear past what the
    seals and the heat transfer fluid survive.

    This is the control that the output branch of the old combined gate could not
    provide and actively inverted: the fixture that falsifies output raises U_L,
    which makes stagnation SAFER. One parameter, two inequalities, opposite
    directions — which is why they are now two gates with a fixture each and
    neither one rescues the other.

    The cut is the deeper of halving U_L and whatever puts the stagnation
    temperature MARGIN of the way past the declared limit's headroom over
    ambient, so a project with a high-temperature fluid cannot defuse it.
    """
    base = _baseline()
    u_l = _num(base, "collector_ul_w_m2k", 4.0)
    tau_alpha = _num(base, "collector_tau_alpha", 0.75)
    g = _num(base, "irradiance_w_m2", _num(base, "poa_irradiance_w_m2", 900.0))
    ambient = _num(base, "collector_ambient_c", 20.0)
    limit = _num(base, "stagnation_limit_c", 0.0)

    headroom = limit - ambient
    u_needed = (tau_alpha * g / (MARGIN * headroom)) if headroom > 0.0 else u_l / 2.0
    return _with(ctx, collector_ul_w_m2k=max(1e-4, min(u_l / 2.0, u_needed)))


def north_facing(ctx):
    """solar.irradiance — the array was mounted facing away from the sun.

    The surface azimuth is rotated 180 degrees. Site, day, hour and albedo are
    identical; only the orientation moves, and the beam term collapses with it —
    to exactly zero once the incidence angle passes 90 degrees, which is every
    hour of a winter design day and most hours of any day, and to a fraction of
    itself at a high summer noon where a back-tilted plane still catches the sun
    obliquely.

    A rotation alone is not enough on a NEARLY HORIZONTAL surface, and this is
    the case the fixture used to miss entirely: at tilt 0 the azimuth cancels out
    of cos(theta), out of the sky-diffuse term and out of the ground term alike,
    so the perturbed plane is bit-identical to the real one and the control is a
    mathematical no-op that still reports success. So the fixture checks what it
    actually did to the plane-of-array number, and if the rotation has not put
    POA clear below the design minimum it tips the plane further over — 90
    degrees, then past vertical, then face down — until it has. A flat roof
    cannot be mis-aimed in azimuth; it can be mounted upside down, and that is
    the honest falsification of the same claim.
    """
    base = _baseline()
    tilt = _num(base, "surface_tilt_deg", 0.0)
    azimuth = _num(base, "surface_azimuth_deg", 0.0) + 180.0
    while azimuth > 180.0:
        azimuth -= 360.0
    target = _num(base, "design_irradiance_min_w_m2", 0.0) / MARGIN

    def poa_at(tilt_deg: float) -> float:
        try:
            return P.clear_sky_poa(
                _num(base, "latitude_deg", 0.0), int(_num(base, "day_of_year", 172)),
                _num(base, "solar_hour", 12.0), tilt_deg, azimuth,
                _num(base, "ground_albedo", 0.20),
                _num(base, "clearness_number", 1.0))["poa_w_m2"]
        except (ValueError, TypeError, KeyError):
            return float("inf")

    candidates = [tilt, max(tilt, 90.0), max(tilt, 135.0), 180.0]
    for candidate in candidates:
        if poa_at(candidate) <= target:
            return _with(ctx, surface_azimuth_deg=azimuth, surface_tilt_deg=candidate)
    worst = min(candidates, key=poa_at)
    return _with(ctx, surface_azimuth_deg=azimuth, surface_tilt_deg=worst)
