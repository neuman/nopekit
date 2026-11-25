# SPDX-License-Identifier: Apache-2.0
"""Closed-form heat-transfer arithmetic, with no opinions about design limits.

Everything here is a function of numbers only: no GateContext, no Verdict, no
registry. The gates in ``thermal.py`` do the reading, the comparing and the
reporting; this file does the physics. Keeping the split means a wrong number can
be reproduced in three lines at a REPL, and it means the property tables and the
correlation validity bands live in exactly one place (rule 2).

Units, without exception:

    length  m          temperature  K inside, degC only at the boundary
    area    m^2        conductivity k  W/(m*K)
    volume  m^3        coefficient  h  W/(m^2*K)
    power   W          resistance   K/W   (absolute)  or  m^2*K/W  (per area)
    time    s          density      kg/m^3      cp  J/(kg*K)

Two rules this module follows and the gates depend on:

1. **Every correlation states its validated range and reports whether it was
   honoured.** A Nusselt number computed outside the band the correlation was
   fitted in is a number, not a measurement, and the caller has to be able to
   tell the difference.
2. **Nothing here silently clamps.** Property lookups outside the table say so;
   they do not extrapolate and keep quiet about it.
"""
from __future__ import annotations

import math
from typing import Any

#: Stefan-Boltzmann constant, W/(m^2*K^4). CODATA exact value since the 2019 SI.
SIGMA = 5.670374419e-8

#: Absolute zero offset. Written once so no gate ever types 273.0 by accident.
KELVIN = 273.15


def c_to_k(celsius: float) -> float:
    return float(celsius) + KELVIN


def k_to_c(kelvin: float) -> float:
    return float(kelvin) - KELVIN


# --------------------------------------------------------------------------- #
# fluid properties
# --------------------------------------------------------------------------- #
#: Dry air at 1 atm: T(K) -> (k W/mK, nu m^2/s, Pr). Standard tabulated values,
#: linearly interpolated. beta = 1/T is exact for an ideal gas, so it is computed
#: rather than tabulated (rule 2: derive, never duplicate).
_AIR = [
    # Pr at 250 K is 0.720 per Incropera & DeWitt Table A.4, the source the whole
    # table is taken from. It carried 0.7344 here until a row-by-row check against
    # that table caught it: an isolated typo, not a second source (every other Pr
    # on this table matches A.4 exactly). It is worth 0.66% on Nu through
    # Pr^(1/3), and it only bites below about 0 degC film temperature — which is
    # precisely where an outdoor enclosure or an insulation study lives.
    (250.0, 0.02227, 1.1406e-5, 0.7200),
    (300.0, 0.02624, 1.5890e-5, 0.7071),
    (350.0, 0.03003, 2.0920e-5, 0.7000),
    (400.0, 0.03365, 2.6410e-5, 0.6900),
    (450.0, 0.03707, 3.2430e-5, 0.6860),
    (500.0, 0.04038, 3.8860e-5, 0.6840),
    (550.0, 0.04360, 4.5780e-5, 0.6830),
    (600.0, 0.04659, 5.3210e-5, 0.6850),
]

#: Saturated liquid water: T(K) -> (k W/mK, nu m^2/s, Pr, beta 1/K). beta is
#: tabulated here because water is nowhere near an ideal gas and 1/T would be
#: wrong by more than an order of magnitude near room temperature.
_WATER = [
    (280.0, 0.582, 1.422e-6, 10.26, 46e-6),
    (300.0, 0.613, 8.576e-7, 5.83, 276e-6),
    (320.0, 0.640, 5.834e-7, 3.77, 436e-6),
    (340.0, 0.660, 4.288e-7, 2.66, 566e-6),
    (360.0, 0.674, 3.351e-7, 2.02, 697e-6),
]

FLUIDS = ("air", "water")


def _interp(table: list[tuple], t_k: float) -> tuple[list[float], bool]:
    """Linear interpolation in a (T, *values) table. Returns (values, in_range).

    Out of range clamps to the end row AND reports ``in_range=False``. Clamping
    without reporting is the failure this whole repository is about: the caller
    would get a plausible number with no way to know it was invented.
    """
    lo, hi = table[0], table[-1]
    if t_k <= lo[0]:
        return list(lo[1:]), t_k == lo[0]
    if t_k >= hi[0]:
        return list(hi[1:]), t_k == hi[0]
    for a, b in zip(table, table[1:]):
        if a[0] <= t_k <= b[0]:
            f = (t_k - a[0]) / (b[0] - a[0])
            return [av + f * (bv - av) for av, bv in zip(a[1:], b[1:])], True
    return list(hi[1:]), False           # pragma: no cover - unreachable


def fluid_properties(fluid: str, film_t_k: float) -> dict[str, Any]:
    """k, nu, Pr and beta for ``fluid`` at the film temperature.

    The film temperature (mean of surface and bulk) is the standard evaluation
    point for every correlation in this pack. Evaluating at the bulk temperature
    instead is a quiet ~10% error on a hot surface in air, and it always errs
    optimistic.
    """
    name = (fluid or "").strip().lower()
    if name == "air":
        (k, nu, pr), in_range = _interp(_AIR, film_t_k)
        beta = 1.0 / film_t_k                       # ideal gas, exact
        lo, hi = _AIR[0][0], _AIR[-1][0]
    elif name == "water":
        (k, nu, pr, beta), in_range = _interp(_WATER, film_t_k)
        lo, hi = _WATER[0][0], _WATER[-1][0]
    else:
        raise ValueError(f"unknown fluid {fluid!r}; this pack tables {', '.join(FLUIDS)}")
    return {"fluid": name, "k": k, "nu": nu, "pr": pr, "beta": beta,
            "film_t_k": film_t_k, "in_range": in_range, "table_lo_k": lo, "table_hi_k": hi}


# --------------------------------------------------------------------------- #
# conduction
# --------------------------------------------------------------------------- #
def layer_resistance(layer: dict[str, Any]) -> tuple[float, str]:
    """Per-area resistance of one stack layer, m^2*K/W, and a one-line label.

    Two accepted shapes:

    * series      ``{"name":..., "thickness_m":..., "k_w_mk":...}``      R = t/k
    * co-planar   ``{"name":..., "thickness_m":..., "parallel":[{...}]}``
      where each branch carries ``k_w_mk`` and ``area_fraction``:
      1/R = sum(f_i * k_i / t).

    The parallel form is the one that matters in practice. A framed wall is not
    its insulation — it is insulation in parallel with studs, and the studs are
    usually a third of the heat loss at a tenth of the area. Reporting the
    insulated-cavity U-value as the wall's U-value is the single most common
    self-deception in envelope work.

    Raises ValueError with a specific message on a malformed layer; the caller
    turns that into a SKIP naming the layer, never into a pass.
    """
    name = str(layer.get("name") or "layer")
    t = float(layer.get("thickness_m", 0.0))
    if t <= 0.0:
        raise ValueError(f"layer {name!r}: thickness_m must be > 0, got {t!r}")

    branches = layer.get("parallel")
    if branches:
        total_f = 0.0
        conductance = 0.0
        for b in branches:
            f = float(b.get("area_fraction", 0.0))
            k = float(b.get("k_w_mk", 0.0))
            if k <= 0.0:
                raise ValueError(f"layer {name!r} branch {b.get('name')!r}: k_w_mk must be > 0")
            total_f += f
            conductance += f * k / t
        if abs(total_f - 1.0) > 1e-6:
            raise ValueError(
                f"layer {name!r}: parallel area_fraction sums to {total_f:.4f}, not 1.0 — "
                f"the framing fraction is a measured property of the wall, not a rounding"
            )
        if conductance <= 0.0:
            raise ValueError(f"layer {name!r}: parallel conductance is zero")
        r = 1.0 / conductance
        label = f"{name} {t * 1000:g}mm ({len(branches)}-way parallel) R={r:.3f}"
        return r, label

    k = float(layer.get("k_w_mk", 0.0))
    if k <= 0.0:
        raise ValueError(f"layer {name!r}: k_w_mk must be > 0, got {k!r}")
    r = t / k
    return r, f"{name} {t * 1000:g}mm k={k:g} R={r:.3f}"


def stack_resistance(layers: list[dict[str, Any]], film_r: float = 0.0) -> dict[str, Any]:
    """Total per-area resistance of a wall stack, plus the per-layer breakdown."""
    rows: list[tuple[str, float]] = []
    total = float(film_r)
    if film_r:
        rows.append(("surface films (in + out)", float(film_r)))
    for layer in layers:
        r, label = layer_resistance(layer)
        total += r
        rows.append((label, r))
    if total <= 0.0:
        raise ValueError("stack resistance is zero — a wall with no resistance is not a wall")
    return {"r_total_m2k_w": total, "u_w_m2k": 1.0 / total, "rows": rows}


def series_resistance(path: list[dict[str, Any]]) -> dict[str, Any]:
    """Total ABSOLUTE resistance (K/W) of a series path, allowing parallel groups.

    A node is either ``{"name":..., "r_k_w":...}`` or
    ``{"name":..., "parallel":[{"name":..., "r_k_w":...}, ...]}``.
    """
    rows: list[tuple[str, float]] = []
    total = 0.0
    for node in path:
        name = str(node.get("name") or "node")
        branches = node.get("parallel")
        if branches:
            conductance = 0.0
            for b in branches:
                rb = float(b.get("r_k_w", 0.0))
                if rb <= 0.0:
                    raise ValueError(f"node {name!r} branch {b.get('name')!r}: r_k_w must be > 0")
                conductance += 1.0 / rb
            r = 1.0 / conductance
        else:
            r = float(node.get("r_k_w", 0.0))
            if r <= 0.0:
                raise ValueError(f"node {name!r}: r_k_w must be > 0, got {r!r}")
        total += r
        rows.append((name, r))
    if total <= 0.0:
        raise ValueError("resistance path is empty — there is no path to compute")
    return {"r_total_k_w": total, "rows": rows}


# --------------------------------------------------------------------------- #
# convection
# --------------------------------------------------------------------------- #
#: geometry -> (human name, valid-range test, range description)
#: Each entry's band is the range the correlation was FITTED over. Outside it the
#: arithmetic still runs; the result is not evidence.
NATURAL_GEOMETRIES = ("vertical_plate", "horizontal_plate_hot_up",
                      "horizontal_plate_hot_down", "horizontal_cylinder")
FORCED_GEOMETRIES = ("flat_plate", "cylinder_cross_flow")

#: The length each correlation was fitted with, per geometry. This lives here
#: and nowhere else (rule 2) so the gate can echo the definition into its own
#: verdict instead of the reader having to remember a table: a correlation fed
#: the wrong characteristic length is wrong by L^3 inside Ra, and nothing about
#: the answer looks wrong. The horizontal-plate rows are the ones that catch
#: people — A/P, not a side. For a square plate of side a that is a/4, so using
#: the side puts Ra out by 64 and h out by 4^0.25 = 1.41x.
CHAR_LENGTH = {
    "vertical_plate": "plate height",
    "horizontal_plate_hot_up": "plate area / perimeter",
    "horizontal_plate_hot_down": "plate area / perimeter",
    "horizontal_cylinder": "cylinder diameter",
    "flat_plate": "streamwise length along the flow",
    "cylinder_cross_flow": "cylinder diameter",
}

#: A horizontal plate's correlation is chosen by where the buoyant plume goes,
#: not by the word in the geometry name. Hot face up and cold face DOWN both let
#: the plume leave freely (0.54); hot face down and cold face UP both trap it
#: (0.27). So the sign of the surface-to-fluid difference flips the pair, and a
#: gate that dispatches on the name alone over-predicts h by exactly 2x on every
#: chilled plate, condenser surface and cold enclosure wall.
_PLUME_FLIP = {
    "horizontal_plate_hot_up": "horizontal_plate_hot_down",
    "horizontal_plate_hot_down": "horizontal_plate_hot_up",
}


def rayleigh(dt_k: float, length_m: float, props: dict[str, Any]) -> float:
    """Ra = g*beta*|dT|*L^3 / (nu^2) * Pr."""
    g = 9.80665
    return (g * props["beta"] * abs(dt_k) * length_m ** 3 / props["nu"] ** 2) * props["pr"]


def natural_nusselt(geometry: str, ra: float, pr: float,
                    dt_k: float | None = None) -> dict[str, Any]:
    """Nu for a natural-convection geometry, with the correlation named.

    ``valid`` is False when Ra sits outside the band the correlation was fitted
    over. The caller must not report an h derived from an invalid Ra as if it
    were measured.

    ``dt_k`` is the SIGNED surface-minus-fluid difference and it is not optional
    in spirit. ``rayleigh`` takes its absolute value — Ra is a magnitude — so if
    the correlation were also chosen from the geometry string alone, nothing in
    the chain would know which way buoyancy runs. A 5 degC plate facing up in
    25 degC air declared ``horizontal_plate_hot_up`` would get 0.54*Ra^0.25,
    h = 5.0 W/m2K, where the physics gives the trapped-plume form 0.27*Ra^0.25
    and h = 2.5: a clean factor of two, in the non-conservative direction, with
    the pack's own correlations. So when ``dt_k`` is negative the two horizontal
    plate forms are swapped and the substitution is REPORTED in ``substituted``,
    because a verdict that names the correlation the user asked for rather than
    the one that ran is a verdict nobody can check.

    Passing ``dt_k=None`` keeps the old name-only dispatch and is only correct
    for the sign-symmetric geometries (vertical plate, horizontal cylinder).
    """
    geo = (geometry or "").strip().lower()
    substituted = ""
    if dt_k is not None and float(dt_k) < 0.0 and geo in _PLUME_FLIP:
        flipped = _PLUME_FLIP[geo]
        substituted = (f"surface is {abs(float(dt_k)):.1f} K COLDER than the fluid, so "
                       f"{geo} was replaced by {flipped} (the plume runs the other way)")
        geo = flipped
    result = _natural_nusselt_named(geo, ra, pr)
    result["geometry"] = geo
    result["substituted"] = substituted
    if substituted:
        result["correlation"] = f"{result['correlation']} [cold-surface substitution]"
    return result


def _natural_nusselt_named(geo: str, ra: float, pr: float) -> dict[str, Any]:
    """The correlation table itself, dispatched on an ALREADY sign-corrected name."""
    if geo == "vertical_plate":
        # Churchill & Chu, all-Ra form. The one correlation here with no upper
        # bound, which is why it is the default for an unknown vertical surface.
        nu = (0.825 + 0.387 * ra ** (1 / 6)
              / (1 + (0.492 / pr) ** (9 / 16)) ** (8 / 27)) ** 2
        return {"nu": nu, "correlation": "Churchill-Chu vertical plate",
                "valid": ra > 0.0, "band": "all Ra (best above 1e4)"}
    if geo == "horizontal_plate_hot_up":
        if 1e4 <= ra <= 1e7:
            return {"nu": 0.54 * ra ** 0.25, "correlation": "McAdams hot-face-up, laminar",
                    "valid": True, "band": "1e4 <= Ra <= 1e7"}
        if 1e7 < ra <= 1e11:
            return {"nu": 0.15 * ra ** (1 / 3), "correlation": "McAdams hot-face-up, turbulent",
                    "valid": True, "band": "1e7 < Ra <= 1e11"}
        return {"nu": 0.54 * max(ra, 0.0) ** 0.25, "correlation": "McAdams hot-face-up",
                "valid": False, "band": "1e4 <= Ra <= 1e11"}
    if geo == "horizontal_plate_hot_down":
        valid = 1e5 <= ra <= 1e10
        return {"nu": 0.27 * max(ra, 0.0) ** 0.25, "correlation": "McAdams hot-face-down",
                "valid": valid, "band": "1e5 <= Ra <= 1e10"}
    if geo == "horizontal_cylinder":
        nu = (0.60 + 0.387 * ra ** (1 / 6)
              / (1 + (0.559 / pr) ** (9 / 16)) ** (8 / 27)) ** 2
        return {"nu": nu, "correlation": "Churchill-Chu horizontal cylinder",
                "valid": 0.0 < ra <= 1e12, "band": "Ra <= 1e12"}
    raise ValueError(f"unknown natural-convection geometry {geometry!r}; "
                     f"known: {', '.join(NATURAL_GEOMETRIES)}")


def forced_nusselt(geometry: str, re: float, pr: float) -> dict[str, Any]:
    """Nu for a forced-convection geometry, with the correlation named.

    No sign guard is needed or wanted here: forced convection does not care
    which way the heat is going, only how fast the fluid is moving past.
    """
    geo = (geometry or "").strip().lower()
    result = _forced_nusselt_named(geo, re, pr)
    result["geometry"] = geo
    result["substituted"] = ""
    return result


def _forced_nusselt_named(geo: str, re: float, pr: float) -> dict[str, Any]:
    if geo == "flat_plate":
        if re < 1e3:
            # Below this the boundary layer is thick compared with the plate and
            # the similarity solution stops describing it; buoyancy usually wins
            # anyway, so the honest answer is "use the natural-convection path".
            return {"nu": 0.664 * max(re, 0.0) ** 0.5 * pr ** (1 / 3),
                    "correlation": "Blasius/Pohlhausen laminar flat plate",
                    "valid": False, "band": "1e3 <= Re <= 5e5 (laminar)",
                    "regime": "below the laminar band"}
        if re <= 5e5:
            return {"nu": 0.664 * re ** 0.5 * pr ** (1 / 3),
                    "correlation": "Blasius/Pohlhausen laminar flat plate",
                    "valid": pr >= 0.6, "band": "1e3 <= Re <= 5e5, Pr >= 0.6",
                    "regime": "laminar"}
        if re <= 1e8:
            return {"nu": (0.037 * re ** 0.8 - 871.0) * pr ** (1 / 3),
                    "correlation": "mixed laminar-turbulent flat plate (Re_x,c = 5e5)",
                    "valid": 0.6 <= pr <= 60.0, "band": "5e5 < Re <= 1e8, 0.6 <= Pr <= 60",
                    "regime": "mixed/turbulent"}
        return {"nu": (0.037 * re ** 0.8 - 871.0) * pr ** (1 / 3),
                "correlation": "mixed laminar-turbulent flat plate",
                "valid": False, "band": "Re <= 1e8", "regime": "above the fitted band"}
    if geo == "cylinder_cross_flow":
        nu = (0.3 + (0.62 * re ** 0.5 * pr ** (1 / 3))
              / (1 + (0.4 / pr) ** (2 / 3)) ** 0.25
              * (1 + (re / 282000.0) ** (5 / 8)) ** (4 / 5))
        return {"nu": nu, "correlation": "Churchill-Bernstein cylinder in cross flow",
                "valid": re * pr > 0.2, "band": "Re*Pr > 0.2",
                "regime": "laminar" if re < 2e5 else "turbulent"}
    raise ValueError(f"unknown forced-convection geometry {geometry!r}; "
                     f"known: {', '.join(FORCED_GEOMETRIES)}")


# --------------------------------------------------------------------------- #
# radiation
# --------------------------------------------------------------------------- #
def radiation_exchange(t1_k: float, t2_k: float, area1_m2: float, eps1: float,
                       view_factor: float, eps2: float = 1.0,
                       area2_m2: float | None = None) -> dict[str, Any]:
    """Net radiant exchange from surface 1 to surface 2, W (positive = 1 loses).

    Two-surface grey enclosure:

        Q = sigma*(T1^4 - T2^4) / [ (1-e1)/(e1*A1) + 1/(A1*F12) + (1-e2)/(e2*A2) ]

    With ``eps2 = 1`` (or a surround so large that A2 >> A1) the last term
    vanishes and this collapses to the familiar e1*A1*F12*sigma*(T1^4 - T2^4).
    Both forms are here on one code path because the difference between them is
    a factor of two in a tight enclosure, and that is precisely where people use
    the simple form out of habit.

    Also returns ``h_rad``, the linearised radiative coefficient W/(m^2*K), so a
    radiative path can be added to a resistance network by an author who is
    thinking in h.
    """
    if not (0.0 < eps1 <= 1.0):
        raise ValueError(f"emissivity must be in (0, 1], got {eps1!r}")
    if not (0.0 <= view_factor <= 1.0):
        raise ValueError(f"view_factor must be in [0, 1], got {view_factor!r}")
    if view_factor == 0.0:
        return {"q_w": 0.0, "h_rad": 0.0, "resistance": math.inf,
                "note": "view factor 0 — the surfaces do not see each other"}
    denom = (1.0 - eps1) / (eps1 * area1_m2) + 1.0 / (area1_m2 * view_factor)
    if eps2 < 1.0:
        if not area2_m2:
            raise ValueError("eps2 < 1 needs area2_m2 — a grey surround has finite area")
        denom += (1.0 - eps2) / (eps2 * area2_m2)
    q = SIGMA * (t1_k ** 4 - t2_k ** 4) / denom
    dt = t1_k - t2_k
    h_rad = (q / (area1_m2 * dt)) if abs(dt) > 1e-9 else 0.0
    return {"q_w": q, "h_rad": h_rad, "resistance": denom / SIGMA, "note": ""}


# --------------------------------------------------------------------------- #
# a sealed shell in still air
# --------------------------------------------------------------------------- #
def linear_dissipation(vin_max_v: float, vout_min_v: float, i_max_a: float) -> float:
    """The loss of a linear stage (a linear charger, an LDO) at its worst corner, W:

        P = (vin_max_v - vout_min_v) * i_max_a

    The worst corner, by name, because the nominal one is about half of it. TI's
    SLUAAG9 (BQ2517x tips) says the IC's dissipation "is greatest during fast
    charge with a low battery voltage and a high input voltage", and USB 2.0 and
    3.1 let VBUS reach 5.5 V at the connector (TI TPS25821 datasheet, power supply
    recommendations). What slipped through without the corner: a charger's loss
    estimated at 5 V in and 3.7 V out, mid-charge, is 1.3 V of headroom against
    2.5 V at 5.5 V in and a 3.0 V fast-charge entry. *Rejected:* a nominal entry
    with a derating factor (a factor is a number nobody can trace to a datasheet).
    """
    if not vin_max_v > vout_min_v:
        raise ValueError(f"vin_max_v {vin_max_v!r} must be above vout_min_v {vout_min_v!r}: "
                         f"a linear stage drops voltage, it does not raise it")
    if not i_max_a > 0.0:
        raise ValueError(f"i_max_a must be > 0, got {i_max_a!r}")
    return (vin_max_v - vout_min_v) * i_max_a


def _shell_loss(t_s_k: float, ambient_k: float, area_m2: float, length_m: float,
                emissivity: float) -> dict[str, Any]:
    """Heat a shell at ``t_s_k`` sheds to still air and surroundings at
    ``ambient_k``: natural convection plus grey-body radiation, W and W/m^2K."""
    dt = t_s_k - ambient_k
    props = fluid_properties("air", (t_s_k + ambient_k) / 2.0)
    ra = rayleigh(dt, length_m, props)
    corr = natural_nusselt("vertical_plate", ra, props["pr"], dt)
    h_c = corr["nu"] * props["k"] / length_m
    h_r = radiation_exchange(t_s_k, ambient_k, area_m2, emissivity, 1.0)["h_rad"]
    return {"h_c": h_c, "h_r": h_r, "ra": ra, "q_conv_w": h_c * area_m2 * dt,
            "q_rad_w": h_r * area_m2 * dt}


def shell_skin_temperature(power_w: float, ambient_k: float, area_m2: float,
                           length_m: float, emissivity: float) -> dict[str, Any]:
    """The steady AVERAGE surface temperature of a sealed shell that sheds
    ``power_w`` to still air and to surroundings at ``ambient_k``:

        P = A * (h_c + h_r) * (T_s - T_a)

    solved for ``T_s`` by bisection (both h rise with ``T_s``, so the loss is
    monotone and the root is unique).

    * ``h_c`` is Churchill-Chu for a vertical plate at ``length_m`` (the shell's
      longest outside dimension), air properties at the film temperature. On this
      module's own correlations one vertical plate at that length gives 81-92% of
      the convection the top and bottom faces of a level flat device get (McAdams
      hot-face-up plus hot-face-down at L = A/P, for 160 x 80, 140 x 60 and
      40 x 40 mm faces at dT 5-25 K), so in that range it errs hot. Outside it
      the bias can reverse: 400 x 300 mm at dT 15-23 K, 500 x 350 mm at 25 K and
      20 x 20 mm at 2 K get 1.5-6% more than the level device's faces; and
      against a face that points down alone it gives 1.2-1.6x (review of T1,
      which found "it errs hot" said with no range). And it has no Rayleigh band,
      so it refuses no small face. *Rejected:* faces per posture with McAdams'
      horizontal plates (five more records, and McAdams' bands refuse the small
      faces of a watch).
    * ``h_r`` is grey-body exchange with a large surround at the air temperature
      (F = 1, eps2 = 1): the room's walls, not the sky, and not a hand or a body
      warmer than the room, which takes less (that errs cold, unmeasured).

    It is the average over ``area_m2`` and never the peak over a source: a 2 mm
    polymer wall spreads heat about 6 mm (sqrt(k t / h) at k 0.2 W/mK, h 10
    W/m^2K), so the peak over a charger sits well above this number.

    ``film_ok`` is False when the balance needs a film above the air table: the
    shell cannot shed ``power_w`` below an average of ``t_hi_k``, which is then
    returned as ``t_s_k`` (a lower bound, not a value). Raises ValueError when the
    ambient is outside the air table (no film can be computed) or a geometric input
    is out of range.
    """
    if not power_w >= 0.0:
        raise ValueError(f"power must be >= 0, got {power_w!r}")
    if not area_m2 > 0.0 or not length_m > 0.0:
        raise ValueError(f"area {area_m2!r} and length {length_m!r} must both be > 0")
    if not 0.0 < emissivity <= 1.0:
        raise ValueError(f"emissivity must be in (0, 1], got {emissivity!r}")
    props = fluid_properties("air", ambient_k)
    lo_k, hi_k = props["table_lo_k"], props["table_hi_k"]
    if not lo_k <= ambient_k < hi_k:
        raise ValueError(f"ambient {ambient_k:.2f} K is outside the air table "
                         f"({lo_k:.0f}-{hi_k:.0f} K): no film temperature can be computed")
    t_lo, t_hi = float(ambient_k), 2.0 * hi_k - float(ambient_k)
    top = _shell_loss(t_hi, ambient_k, area_m2, length_m, emissivity)
    if top["q_conv_w"] + top["q_rad_w"] < power_w:
        return {"t_s_k": t_hi, "t_hi_k": t_hi, "film_ok": False, **top}
    # 200 halvings is far past a float's precision over a 700 K bracket; the loop
    # stops as soon as the bracket stops shrinking.
    for _ in range(200):
        mid = (t_lo + t_hi) / 2.0
        if mid in (t_lo, t_hi):
            break
        got = _shell_loss(mid, ambient_k, area_m2, length_m, emissivity)
        if got["q_conv_w"] + got["q_rad_w"] < power_w:
            t_lo = mid
        else:
            t_hi = mid
    t_s = (t_lo + t_hi) / 2.0
    return {"t_s_k": t_s, "t_hi_k": 2.0 * hi_k - float(ambient_k), "film_ok": True,
            **_shell_loss(t_s, ambient_k, area_m2, length_m, emissivity)}


# --------------------------------------------------------------------------- #
# fins
# --------------------------------------------------------------------------- #
def straight_fin(k: float, thickness_m: float, length_m: float, width_m: float,
                 h: float) -> dict[str, Any]:
    """Efficiency and effectiveness of one straight rectangular fin, adiabatic tip
    handled by the corrected length L_c = L + t/2.

        m      = sqrt(h*P / (k*A_c))
        eta    = tanh(m*L_c) / (m*L_c)
        eps    = eta * A_fin / A_c          <- EFFECTIVENESS, not efficiency

    The two are routinely confused and they answer different questions.
    *Efficiency* asks how much of the fin is doing work; a short stubby fin scores
    beautifully on it. *Effectiveness* asks whether the fin beats the bare base it
    covered up, and it is the only one that can tell you not to fit the fin.

    ``eps_ceiling = sqrt(k*P/(h*A_c))`` is the effectiveness of an infinitely long
    fin of this section. It is the number to look at first: if it is below ~2 no
    length of fin will help, and if it is below 1 the fin is a liability whatever
    you do to it. That happens with a low-conductivity fin, a thick short fin, or a
    very high h (liquid) — which is exactly why moulded plastic "heatsinks" in
    water are worse than a flat plate.
    """
    if min(k, thickness_m, length_m, width_m, h) <= 0.0:
        raise ValueError("fin geometry, conductivity and h must all be > 0")
    a_c = thickness_m * width_m                       # conduction cross-section
    perimeter = 2.0 * (width_m + thickness_m)
    l_c = length_m + thickness_m / 2.0                # adiabatic-tip correction
    m = math.sqrt(h * perimeter / (k * a_c))
    ml = m * l_c
    eta = math.tanh(ml) / ml
    a_fin = perimeter * l_c
    eps = eta * a_fin / a_c
    return {
        "m_1_m": m, "mL": ml, "efficiency": eta, "effectiveness": eps,
        "a_c_m2": a_c, "a_fin_m2": a_fin, "l_c_m": l_c,
        "eps_ceiling": math.sqrt(k * perimeter / (h * a_c)),
        "q_fin_w_per_k": eta * h * a_fin,             # per kelvin of base excess
    }


# --------------------------------------------------------------------------- #
# lumped capacitance
# --------------------------------------------------------------------------- #
def lumped(volume_m3: float, area_m2: float, rho: float, cp: float, k: float,
           h: float) -> dict[str, Any]:
    """Biot number and time constant for a lumped-capacitance body.

        L_c  = V / A_s
        Bi   = h*L_c / k
        tau  = rho*V*cp / (h*A_s)

    ``Bi <= 0.1`` is the whole licence for the lumped model: it says internal
    conduction is at least ten times easier than getting the heat off the surface,
    so the body may be treated as one temperature. Above it the surface cools
    while the core has not noticed, tau under-predicts, and any single-node
    network built on the same body is telling you about a temperature that does
    not exist anywhere in the part.
    """
    if min(volume_m3, area_m2, rho, cp, k, h) <= 0.0:
        raise ValueError("lumped-capacitance inputs must all be > 0")
    l_c = volume_m3 / area_m2
    return {
        "l_c_m": l_c,
        "biot": h * l_c / k,
        "tau_s": rho * volume_m3 * cp / (h * area_m2),
        "capacitance_j_k": rho * volume_m3 * cp,
    }


# --------------------------------------------------------------------------- #
# solar geometry and clear-sky irradiance
# --------------------------------------------------------------------------- #
#: ASHRAE clear-day coefficients by month (1-12): A the apparent extraterrestrial
#: irradiance W/m^2, B the atmospheric extinction coefficient, C the diffuse
#: fraction of the beam. The classic 1972 table, converted from Btu/(hr*ft^2).
#: Fitted to mid-latitude, sea-level, moderately dusty air in the northern
#: hemisphere; see references/solar-model.md for the error band and what it does
#: NOT cover (altitude, humidity, aerosol, and weather of any kind).
_ASHRAE = {
    1: (1230.0, 0.142, 0.058), 2: (1215.0, 0.144, 0.060), 3: (1186.0, 0.156, 0.071),
    4: (1136.0, 0.180, 0.097), 5: (1104.0, 0.196, 0.121), 6: (1088.0, 0.205, 0.134),
    7: (1085.0, 0.207, 0.136), 8: (1107.0, 0.201, 0.122), 9: (1152.0, 0.177, 0.092),
    10: (1193.0, 0.160, 0.073), 11: (1221.0, 0.149, 0.063), 12: (1234.0, 0.142, 0.057),
}

#: Cumulative day-of-year at the end of each month, non-leap. A leap year shifts
#: the month boundary by one day, which moves A/B/C by less than a percent; the
#: declination is computed from the day number directly and is unaffected.
_MONTH_END = (31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334, 365)


def month_of_year(day_of_year: int) -> int:
    n = max(1, min(365, int(day_of_year)))
    for i, end in enumerate(_MONTH_END, start=1):
        if n <= end:
            return i
    return 12                                   # pragma: no cover - unreachable


def declination_deg(day_of_year: int) -> float:
    """Cooper's equation. +-0.5 deg against the ephemeris, which is far inside
    every other error in this model."""
    return 23.45 * math.sin(math.radians(360.0 * (284 + int(day_of_year)) / 365.0))


def clear_sky_poa(latitude_deg: float, day_of_year: int, solar_hour: float,
                  tilt_deg: float, surface_azimuth_deg: float,
                  albedo: float = 0.20, clearness: float = 1.0) -> dict[str, Any]:
    """ASHRAE clear-sky irradiance on a tilted plane, W/m^2.

    FRAME, stated once and meant literally:

    * ``solar_hour`` is SOLAR time, 0-24, noon = sun on the local meridian. It is
      not clock time. The conversion (longitude offset + equation of time) is in
      references/solar-model.md and belongs in the model, not in this gate.
    * ``surface_azimuth_deg``: 0 = due south, +90 = due west, -90 = due east.
      In the southern hemisphere the equator-facing orientation is 180, not 0.
    * ``latitude_deg``: north positive.

    Returns beam / sky-diffuse / ground-reflected components separately, because
    when a number looks wrong the split is what tells you which half of the model
    to distrust.
    """
    phi = math.radians(latitude_deg)
    delta = math.radians(declination_deg(day_of_year))
    omega = math.radians(15.0 * (float(solar_hour) - 12.0))
    beta = math.radians(tilt_deg)
    gamma = math.radians(surface_azimuth_deg)

    sin_alt = math.sin(delta) * math.sin(phi) + math.cos(delta) * math.cos(phi) * math.cos(omega)
    sin_alt = max(-1.0, min(1.0, sin_alt))
    altitude = math.degrees(math.asin(sin_alt))

    cos_alt = math.sqrt(max(0.0, 1.0 - sin_alt ** 2))
    if cos_alt > 1e-9:
        sin_az = math.cos(delta) * math.sin(omega) / cos_alt
        cos_az = (sin_alt * math.sin(phi) - math.sin(delta)) / (cos_alt * math.cos(phi)) \
            if abs(math.cos(phi)) > 1e-9 else 1.0
        solar_azimuth = math.degrees(math.atan2(
            max(-1.0, min(1.0, sin_az)), max(-1.0, min(1.0, cos_az))))
    else:
        solar_azimuth = 0.0

    a, b, c = _ASHRAE[month_of_year(day_of_year)]

    if sin_alt <= 0.0:                       # sun below the horizon: nothing, honestly
        return {"poa_w_m2": 0.0, "beam_w_m2": 0.0, "diffuse_w_m2": 0.0, "ground_w_m2": 0.0,
                "dni_w_m2": 0.0, "altitude_deg": altitude, "solar_azimuth_deg": solar_azimuth,
                "incidence_deg": 180.0, "declination_deg": math.degrees(delta),
                "air_mass": math.inf, "month": month_of_year(day_of_year),
                "note": "sun below the horizon"}

    dni = clearness * a * math.exp(-b / sin_alt)      # ASHRAE beam normal

    # Duffie & Beckman 1.6.2, expanded so no azimuth quadrant logic is needed.
    cos_theta = (math.sin(delta) * math.sin(phi) * math.cos(beta)
                 - math.sin(delta) * math.cos(phi) * math.sin(beta) * math.cos(gamma)
                 + math.cos(delta) * math.cos(phi) * math.cos(beta) * math.cos(omega)
                 + math.cos(delta) * math.sin(phi) * math.sin(beta) * math.cos(gamma) * math.cos(omega)
                 + math.cos(delta) * math.sin(beta) * math.sin(gamma) * math.sin(omega))
    cos_theta = max(-1.0, min(1.0, cos_theta))

    beam = dni * max(0.0, cos_theta)
    diffuse = c * dni * (1.0 + math.cos(beta)) / 2.0
    ground = albedo * dni * (c + sin_alt) * (1.0 - math.cos(beta)) / 2.0
    return {
        "poa_w_m2": beam + diffuse + ground,
        "beam_w_m2": beam, "diffuse_w_m2": diffuse, "ground_w_m2": ground,
        "dni_w_m2": dni, "altitude_deg": altitude, "solar_azimuth_deg": solar_azimuth,
        "incidence_deg": math.degrees(math.acos(cos_theta)),
        "declination_deg": math.degrees(delta), "air_mass": 1.0 / sin_alt,
        "month": month_of_year(day_of_year), "note": "",
    }


# --------------------------------------------------------------------------- #
# flat-plate collector
# --------------------------------------------------------------------------- #
def hottel_whillier(area_m2: float, f_r: float, tau_alpha: float, u_l: float,
                    irradiance_w_m2: float, inlet_c: float,
                    ambient_c: float) -> dict[str, Any]:
    """Useful heat from a flat-plate collector, and its stagnation temperature.

        Q_u    = F_R * A * (tau_alpha * G - U_L * (T_in - T_amb))
        eta    = Q_u / (A * G)
        T_stag = T_amb + tau_alpha * G / U_L

    Stagnation is the number the equation gives away for free and nobody asks
    for: the absorber temperature with no flow at all. It does not depend on F_R
    or on area, only on the optical gain over the loss coefficient — so *the
    better the collector, the hotter it stagnates*. A well-insulated plate is
    more likely to cook its own seals on the day the pump stops than a mediocre
    one. That is the inversion this function exists to surface.
    """
    if min(area_m2, f_r, tau_alpha, u_l) <= 0.0:
        raise ValueError("collector area, F_R, tau_alpha and U_L must all be > 0")
    g = float(irradiance_w_m2)
    dt = float(inlet_c) - float(ambient_c)
    q_u = f_r * area_m2 * (tau_alpha * g - u_l * dt)
    return {
        "q_useful_w": q_u,
        "efficiency": (q_u / (area_m2 * g)) if g > 0 else 0.0,
        "stagnation_c": float(ambient_c) + tau_alpha * g / u_l,
        "critical_irradiance_w_m2": u_l * dt / tau_alpha,   # G below which output is zero
        "dt_k": dt,
    }
