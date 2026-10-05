# SPDX-License-Identifier: Apache-2.0
"""Deflection against the dimension that drives it, with the limit drawn across it.

``beam.deflection`` answers one question at one point: *is 0.31 mm under the 0.5 mm
limit*. Yes. What the verdict cannot say, in a line, is whether that 0.31 mm is a
design sitting comfortably in the flat part of the curve or one perched where the
next half-millimetre of section change costs it everything. A curve says it in a
glance — and the glance is the whole justification for this view, because *how far
inside a limit* is the question every review asks and no single number answers.

The arithmetic is the gate's own. ``_beam_analytic_lib`` is imported here, not
reimplemented: a chart drawn from a second copy of ``delta = k*P*L^3/(E*I)`` is a
chart that can disagree with the verdict it sits next to, and a reader has no way
to tell which of the two is the project's actual status. Sweeping one dimension
through the shared formula is not the site computing truth — the truth is the
verdict, which is read straight off the ledger and marked on the curve. The curve
is the shape around it.

That marking is also a CHECK, and it is in the payload rather than only in a
comment. ``meta["agreement"]`` compares the curve's own value at the design point
against the measured value in the ledger's verdict. They came from one formula and
one projection, so they must agree; if they do not, the projection the site was
built from is not the projection the sweep was run against, and that is exactly the
mixed-age picture ``site build`` refuses to publish silently (method rule 6: check
agreement across representations).
"""
from __future__ import annotations

import math
import os
import sys
from typing import Any

from nopekit.models import View, ViewKind
from nopekit.site import ViewContext, viewgen

# The gates' shared arithmetic. Same import route as gates/beam.py, so both end up
# holding the same module object rather than two copies that can drift.
_GATES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "gates")
if _GATES not in sys.path:
    sys.path.insert(0, _GATES)

import _beam_analytic_lib as B  # noqa: E402

#: Samples across the sweep. Enough that the curve reads as a curve on a phone and
#: not as a polyline; few enough that the whole payload stays inline in
#: ``state.json`` rather than being spilled to ``data/views/``, which is what keeps
#: the one file a reader curls small enough to read.
SAMPLES = 41

#: The sweep bracket, as multiples of the current value. A factor of two either way
#: is the range a designer actually considers on a section dimension — past that it
#: is a different part, not a revision of this one.
BRACKET = (0.5, 2.0)

#: The bracket to widen to, once, if the limit is not crossed inside the first one.
#: A chart with no crossing on it cannot answer "how much margin is there", which
#: is the only question it exists to answer. Widening once rather than searching:
#: a design four times off its limit does not need the crossing located precisely,
#: it needs to see that the crossing is nowhere near.
WIDE_BRACKET = (0.25, 4.0)

#: Dimensions this view knows how to sweep, per section shape: the canonical key it
#: overrides, every spelling it will read the current value from, and what to call
#: the axis. The canonical key is FIRST in each alias list in
#: ``_beam_analytic_lib``, so overriding it wins over whatever spelling the model
#: happens to use — a project that writes ``h_mm`` still gets its swept height read.
DRIVERS: dict[str, tuple[str, tuple, str]] = {
    "rect": ("height_mm", ("height_mm", "h_mm", "depth_mm", "section_depth_mm"),
             "section depth h (in the bending direction)"),
    "circle": ("dia_mm", ("dia_mm", "diameter_mm"), "bar diameter"),
    "tube": ("dia_mm", ("dia_mm", "diameter_mm", "od_mm", "outer_dia_mm"),
             "tube outer diameter"),
}

#: The fallback driver, for a section this pack has no formula for (an explicit
#: ``I_mm4``). Span is always present and deflection goes as its cube, so it is
#: never a dishonest choice — but it is the second choice, because the span is
#: usually the thing the design cannot change and the section is the thing it can.
SPAN_DRIVER = ("span_mm",
               ("span_mm", "length_mm", "beam_length_mm", "free_span_mm", "arm_length_mm"),
               "free span L")

#: How far the curve's value at the design point may sit from the ledger's measured
#: value before the view says the two disagree. 0.5% is well outside float noise
#: and well inside any real parameter change, so a hit means the projection moved.
AGREEMENT_TOL = 0.005


class _Swept:
    """``ctx`` with one parameter replaced. Read-only, one key deep.

    A shim rather than a mutated copy of ``ctx.params`` because the dotted-name
    resolution in ``ViewContext.param`` is the behaviour the library was written
    against, and reimplementing it here to build a dict would be a second copy of
    the one thing both contexts are supposed to agree about.
    """

    def __init__(self, ctx: Any, key: str, value: float) -> None:
        self._ctx, self._key, self._value = ctx, key, float(value)

    def param(self, name: str, default: Any = None) -> Any:
        if name == self._key or name.rsplit(".", 1)[-1] == self._key:
            return self._value
        return self._ctx.param(name, default)


def _driver(ctx: ViewContext) -> tuple[str, tuple, str] | None:
    """Which dimension to sweep: stated, else the section's own, else the span.

    A project may name one with ``deflection_sweep_param``. Nothing here guesses at
    a key it does not recognise — an unknown name comes back as the span rather
    than being overridden blindly, because overriding a key the formula never reads
    would draw a perfectly flat line and call it a sensitivity study.
    """
    stated = ctx.param("deflection_sweep_param")
    if stated:
        for driver in (*DRIVERS.values(), SPAN_DRIVER):
            if str(stated) in driver[1]:
                return driver
        return None
    try:
        shape = B.section(ctx)["shape"]
    except B.MissingParam:
        return None
    return DRIVERS.get(shape, SPAN_DRIVER)


def _limit(ctx: ViewContext, span: float) -> tuple[float, str] | None:
    """The acceptance limit in millimetres, and what it is called.

    ``deflection_limit_mm`` first, because that is the number ``beam.deflection``
    is actually judged against. Failing that, the serviceability ratio the other
    deflection gate uses, converted to millimetres at this span — including its
    pack default, disclosed in the label exactly as the gate discloses it. A chart
    with no limit line is a chart of a quantity nobody has an opinion about.
    """
    stated = B.get(ctx, ("deflection_limit_mm", "max_deflection_mm", "deflection_allow_mm"))
    if stated is not None:
        try:
            value = float(stated)
        except (TypeError, ValueError):
            return None
        if value > 0:
            return value, f"deflection_limit_mm = {value:g} mm"
    ratio, defaulted = B.opt(ctx, ("deflection_ratio_limit", "deflection_ratio",
                                   "span_over_deflection"), B.DEFAULT_RATIO_LIMIT)
    if ratio <= 0:
        return None
    return span / ratio, (f"L/{ratio:g} = {span / ratio:.3f} mm"
                          + (" [pack default]" if defaulted else ""))


def _sample(ctx: ViewContext, key: str, x: float, k_d: float, span_key: str,
            load: float, modulus: float, span: float) -> float | None:
    """Deflection at one value of the swept dimension, or None if it is unusable.

    A swept value that makes the section degenerate — a tube wall thicker than its
    new radius, a zero depth — raises ``MissingParam`` from the shared library, and
    that point is simply absent from the curve rather than being drawn as a zero.
    A zero on a deflection chart reads as an infinitely stiff beam.
    """
    swept = _Swept(ctx, key, x)
    try:
        section = B.section(swept)
        length = float(x) if key == span_key else span
        return B.deflection_mm(k_d, load, length, modulus, section["I"])
    except (B.MissingParam, ZeroDivisionError, ValueError):
        return None


@viewgen(
    id="deflection_curve",
    kind=ViewKind.CHART,
    title="Deflection vs the driving dimension",
    description="How the beam's deflection moves with the one dimension that "
                "drives it, against the acceptance limit, with the current design "
                "marked.",
    order=20,
    gates=["beam.deflection", "beam.deflection_ratio"],
)
def deflection_curve(ctx: ViewContext) -> View | None:
    """Deflection swept across the driving dimension, with the limit and the design point."""
    try:
        span = B.span(ctx)
        load = B.load(ctx)
        modulus, e_source = B.modulus(ctx)
        case, (k_d, _k_m, _k_v, _k_s, _note), case_defaulted = B.case_of(ctx)
        B.section(ctx)                      # must resolve, or there is no beam here
    except B.MissingParam:
        # Not a failure. This pack's viewgen in a project with no beam in it has
        # nothing to draw, and a project with no beam is an ordinary project.
        return None

    driver = _driver(ctx)
    if driver is None:
        return None
    key, aliases, axis_label = driver

    current = B.get(ctx, aliases)
    try:
        current = float(current)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(current) or current <= 0:
        return None

    limit = _limit(ctx, span)
    if limit is None:
        return None
    limit_mm, limit_label = limit

    design = _sample(ctx, key, current, k_d, SPAN_DRIVER[0], load, modulus, span)
    if design is None:
        return None

    def curve(bracket: tuple[float, float]) -> list[list[float]]:
        lo, hi = current * bracket[0], current * bracket[1]
        step = (hi - lo) / (SAMPLES - 1)
        # The design's own value is forced into the sample set rather than left to
        # land between two samples: the marked point has to sit ON the curve, and a
        # marker floating a pixel off the line is the kind of detail that makes a
        # reader stop believing the rest of the page.
        xs = sorted({round(lo + i * step, 9) for i in range(SAMPLES)} | {round(current, 9)})
        points = []
        for x in xs:
            if x <= 0:
                continue
            y = _sample(ctx, key, x, k_d, SPAN_DRIVER[0], load, modulus, span)
            if y is not None and math.isfinite(y):
                points.append([round(x, 6), round(y, 6)])
        return points

    points = curve(BRACKET)
    bracket = BRACKET
    if points and not (min(p[1] for p in points) <= limit_mm <= max(p[1] for p in points)):
        wider = curve(WIDE_BRACKET)
        if wider and (min(p[1] for p in wider) <= limit_mm <= max(p[1] for p in wider)):
            points, bracket = wider, WIDE_BRACKET
    if len(points) < 2:
        return None

    meta: dict[str, Any] = {
        "x": {"key": key, "label": axis_label, "units": "mm"},
        "y": {"label": "maximum deflection", "units": "mm"},
        "gate": "beam.deflection",
        "case": case + (" [pack default]" if case_defaulted else ""),
        "basis": (f"delta = {k_d:.4g} * P * L^3 / (E*I) at P {load:g} N, "
                  f"L {span:g} mm, E {modulus:.0f} MPa [{e_source}]"),
        "bracket": [f"{bracket[0]:g}x", f"{bracket[1]:g}x"],
    }

    # Rule 6, mechanised: the curve and the verdict came from one formula and one
    # projection, so they must agree at the design point. A disagreement means the
    # site was built from a projection the sweep never saw, and it rides in the
    # payload rather than in a log line nobody reads.
    recorded = next((v for v in (ctx.ledger.verdicts or [])
                     if v.gate == "beam.deflection" and not v.skipped and not v.error), None)
    if recorded is not None and isinstance(recorded.measured, (int, float)):
        measured = float(recorded.measured)
        scale = max(abs(measured), abs(design), 1e-9)
        agrees = abs(measured - design) / scale <= AGREEMENT_TOL
        meta["agreement"] = {
            "gate_measured_mm": round(measured, 6),
            "curve_at_design_mm": round(design, 6),
            "agrees": agrees,
        }
        if not agrees:
            meta["agreement"]["note"] = (
                "the curve and the recorded verdict disagree at the design point — "
                "the model has moved since the sweep ran, so this chart describes a "
                "beam the verdict beside it did not measure. Re-run `nopekit check`")

    return View(
        id="deflection_curve",
        kind=ViewKind.CHART,
        title="Deflection vs the driving dimension",
        data={
            "series": [{
                "key": "deflection",
                "label": f"deflection ({case})",
                "units": "mm",
                "points": points,
            }],
            "limit": {
                "value": round(limit_mm, 6),
                "label": limit_label,
                "comparator": "<=",
                "series": "deflection",
            },
            "markers": [{
                "key": "design",
                "series": "deflection",
                "x": round(current, 6),
                "y": round(design, 6),
                "label": f"current design: {key} {current:g} mm -> {design:.3f} mm "
                         f"({design / limit_mm * 100:.0f}% of limit)",
            }],
        },
        meta=meta,
    )
