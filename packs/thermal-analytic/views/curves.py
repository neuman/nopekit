# SPDX-License-Identifier: Apache-2.0
"""The collector's useful output against how hot you feed it, on one line.

Hottel-Whillier is a straight line and everything a flat-plate collector does
sits on it::

    Q_u = F_R * A * (tau_alpha * G - U_L * dT)      dT = T_inlet - T_ambient

The intercept is the optics. The slope is the losses. And the point where the
line reaches zero is the **stagnation** temperature rise — the collector with no
flow, which is the condition that cooks seals and degrades glycol. Two gates read
this one line from opposite ends: ``solar.collector_output`` wants the design
point high enough, ``solar.stagnation`` wants the zero crossing low enough. A
table of numbers keeps that relationship invisible; the chart makes it the same
line, and the inversion the pack keeps warning about — *the better the collector,
the hotter it stagnates* — becomes something you can see, because better
insulation is a shallower slope and a shallower slope reaches zero further right.

The arithmetic is ``_thermal_physics.hottel_whillier``, the same function both
gates call. Never a second copy: a chart that can disagree with the verdict beside
it leaves the reader with two answers and no way to choose.

The curve stops at the zero crossing rather than running into negative output.
Past stagnation the formula describes a collector being fed fluid hotter than it
can reach, which is a real thing and a different question — and a chart with a
plunging negative tail invites a reader to read a loss rate off a model that was
fitted to gains.
"""
from __future__ import annotations

import math
import os
import sys
from typing import Any

from nopekit.models import View, ViewKind
from nopekit.site import ViewContext, viewgen

# The gates' shared physics. Same import route as gates/thermal.py, so both hold
# one module object rather than two copies of the same correlations.
_GATES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "gates")
if _GATES not in sys.path:
    sys.path.insert(0, _GATES)

import _thermal_physics as P  # noqa: E402

#: Samples along the line. It IS a line, so two points would draw it — forty-one
#: exist so the design point lands on a sample and so a renderer that interpolates
#: markers onto the series has something to interpolate against.
SAMPLES = 41

#: How far past the rightmost marked temperature rise the x-axis runs, as a
#: multiple. Enough that the stagnation marker is not sitting on the frame edge.
X_MARGIN = 1.08


def _num(ctx: ViewContext, *keys: str) -> float | None:
    """First numeric parameter among ``keys``, or None. Booleans are not numbers."""
    for key in keys:
        value = ctx.param(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
    return None


@viewgen(
    id="collector_curve",
    kind=ViewKind.CHART,
    title="Collector output vs inlet temperature rise",
    description="Hottel-Whillier useful heat against T_inlet - T_ambient, with the "
                "required output as the limit, the design point marked, and the "
                "stagnation crossing where the line reaches zero.",
    order=20,
    gates=["solar.collector_output", "solar.stagnation"],
)
def collector_curve(ctx: ViewContext) -> View | None:
    """Q_u against dT, from the design point out to stagnation."""
    area = _num(ctx, "collector_area_m2")
    f_r = _num(ctx, "collector_fr")
    tau_alpha = _num(ctx, "collector_tau_alpha")
    u_l = _num(ctx, "collector_ul_w_m2k")
    ambient = _num(ctx, "collector_ambient_c")
    irradiance = _num(ctx, "irradiance_w_m2", "poa_irradiance_w_m2")
    if None in (area, f_r, tau_alpha, u_l, ambient, irradiance):
        # A project with no collector in it. Normal, and not an error — this pack
        # also does enclosure walls, heatsinks and fins, and most of its users will
        # never touch the solar half.
        return None
    if min(area, f_r, tau_alpha, u_l) <= 0 or irradiance <= 0:
        # The same refusal `hottel_whillier` makes, made before it is called: a
        # zero loss coefficient puts stagnation at infinity and there is no chart
        # with an infinite axis on it.
        return None

    inlet = _num(ctx, "collector_inlet_c")
    required = _num(ctx, "collector_output_min_w")
    ceiling_c = _num(ctx, "stagnation_limit_c")

    # The zero crossing IS the stagnation rise: Q_u = 0 at dT = tau_alpha*G/U_L,
    # and T_stag = T_ambient + that. Derived from the same expression rather than
    # read from a second place, so the chart cannot put the crossing somewhere the
    # stagnation gate does not agree with.
    dt_stagnation = tau_alpha * irradiance / u_l
    marks = [dt_stagnation]
    if inlet is not None:
        marks.append(inlet - ambient)
    if ceiling_c is not None:
        marks.append(ceiling_c - ambient)
    x_max = max(marks) * X_MARGIN
    if not math.isfinite(x_max) or x_max <= 0:
        return None

    def q_at(d_t: float) -> float:
        return f_r * area * (tau_alpha * irradiance - u_l * d_t)

    # The series stops at stagnation, where output reaches zero. See the module
    # docstring: past it the line describes a loss regime nobody fitted it to.
    span = min(dt_stagnation, x_max)
    step = span / (SAMPLES - 1)
    xs = {round(i * step, 6) for i in range(SAMPLES)}
    if inlet is not None and 0 <= inlet - ambient <= span:
        # Force the design point onto the sample set so the marker sits ON the
        # line rather than a pixel off it.
        xs.add(round(inlet - ambient, 6))
    points = [[x, round(q_at(x), 3)] for x in sorted(xs)]

    markers: list[dict[str, Any]] = []
    design = None
    if inlet is not None:
        d_t = inlet - ambient
        design = P.hottel_whillier(area, f_r, tau_alpha, u_l, irradiance, inlet, ambient)
        markers.append({
            "key": "design",
            "series": "useful",
            "x": round(d_t, 4),
            "y": round(design["q_useful_w"], 3),
            "label": f"design point: inlet {inlet:g} C over {ambient:g} C ambient "
                     f"-> {design['q_useful_w']:.0f} W at eta {design['efficiency']:.3f}"
                     + (f" ({design['q_useful_w'] / required * 100:.0f}% of required)"
                        if required else ""),
        })
    markers.append({
        "key": "stagnation",
        "series": "useful",
        "x": round(dt_stagnation, 4),
        "y": 0.0,
        "label": f"stagnation: no flow, {ambient + dt_stagnation:.0f} C absorber "
                 f"({dt_stagnation:.0f} K over ambient) — output is zero because all "
                 f"of it is going out as loss",
    })
    if ceiling_c is not None:
        markers.append({
            "key": "survivable",
            "series": "useful",
            "x": round(ceiling_c - ambient, 4),
            "y": 0.0,
            "label": f"stagnation_limit_c {ceiling_c:g} C — what the seals, the heat "
                     f"transfer fluid and the absorber coating survive with no flow"
                     + (". The stagnation crossing is PAST it"
                        if ambient + dt_stagnation > ceiling_c else ""),
        })

    meta: dict[str, Any] = {
        "x": {"key": "collector_inlet_c", "label": "inlet minus ambient, dT", "units": "K"},
        "y": {"label": "useful output Q_u", "units": "W"},
        "gate": "solar.collector_output",
        "basis": (f"Q_u = F_R*A*(tau_alpha*G - U_L*dT) at F_R {f_r:g}, A {area:g} m2, "
                  f"tau_alpha {tau_alpha:g}, U_L {u_l:g} W/m2K, G {irradiance:.0f} W/m2"),
        "x_max": round(x_max, 4),
        "validity": ("one irradiance and one constant U_L. Real U_L climbs with "
                     "absorber temperature, so the line is optimistic at its right-hand "
                     "end and the stagnation crossing it shows is a CONSERVATIVE bound "
                     "— the real collector reaches zero output a little sooner"),
        "inversion": ("the slope is U_L and the crossing is tau_alpha*G/U_L, so better "
                      "insulation both raises this line and pushes stagnation further "
                      "right. A better collector is a hotter one with the pump off"),
    }
    if design is not None:
        meta["design_efficiency"] = round(design["efficiency"], 4)

    data: dict[str, Any] = {
        "series": [{
            "key": "useful",
            "label": "useful output",
            "units": "W",
            "points": points,
        }],
        "markers": markers,
    }
    if required is not None:
        data["limit"] = {
            "value": round(required, 3),
            "label": f"collector_output_min_w = {required:g} W",
            "comparator": ">=",
            "series": "useful",
        }
    # No limit key at all when the model states no required output, rather than a
    # zero one: a line drawn at zero would read as "anything positive passes",
    # which is an acceptance nobody wrote down.

    return View(
        id="collector_curve",
        kind=ViewKind.CHART,
        title="Collector output vs inlet temperature rise",
        data=data,
        meta=meta,
    )
