# SPDX-License-Identifier: Apache-2.0
"""The extrusion arithmetic ``fdm.print_time_est`` runs on, as pure functions.

Why this is a module and not four lines inside the gate: the gate's **negative
control** has to make the part take longer than the project's own ceiling, and
the only honest way to size that fixture is to solve the gate's own model for
the speed that does it. A control that hardcodes "6 mm/s" is bad only for the
part the pack happens to ship — change the part's volume, the nozzle or the
layer height and the same fixture quietly becomes a passing one, which is the
same as having no control at all.

So the gate and the fixture read the *same* functions, and the fixture calls
:func:`speed_for_hours` against the limit it must beat. Rule 2: derive, never
duplicate. Rule 5: the control has to fire everywhere, not here.

Leading underscore on purpose — ``packs.load_gates`` treats ``gates/_*.py`` as
a shared helper and does not import it as a gate module. Standard library only,
and no ``nopekit`` imports, so a fixture can load it by path without touching
the gate registry.

Units: mm, mm/s, seconds unless the name says otherwise.
"""
from __future__ import annotations

import math

DUTY_FACTOR = 0.55
"""Fraction of wall-clock time the nozzle spends actually extruding at speed.

Acceleration limits, travel moves, retractions, the slow first layer and the
minimum-layer-time cooling hold all eat into it. 0.55 is a middling machine on a
middling part; a small, tall, many-island part is worse (0.3), a large flat one is
better (0.75). This is why the gate calls itself an estimate."""

STARTUP_OVERHEAD_S = 300.0
"""Heat-up, bed levelling, purge line. Five minutes, roughly, and it does not
scale with the part."""

SHELL_FRACTION_DEFAULT = 0.35
"""Fraction of a part's volume that is shell, when surface area is not projected.

A crude stand-in used only by the time estimate. A 60 cm^3 part with 1.2 mm of
wall is usually 30-45% shell; below ~10 cm^3 it is nearly all shell and this
under-estimates. The verdict says when it was used."""


def bead_area_mm2(layer_h: float, width: float) -> float:
    """Cross-section of one extruded bead, mm^2 — a **stadium**, not a rectangle.

    A bead leaving a round orifice and squished into the layer below is a
    rectangle of ``layer_h x (width - layer_h)`` with a half-cylinder on each
    side, so::

        A = h * (w - h) + pi * h^2 / 4

    Rejected: ``A = w * h``, the obvious approximation and the one this gate
    shipped first. It over-states the area of every bead by ``h/w * (1 - pi/4)``
    — 10.2% at the common 0.42 mm width on 0.20 mm layers — and an over-stated
    bead means fewer millimetres of path for the same volume, so the estimate
    came out short. A one-sided bias in the flattering direction is the worst
    kind to leave in a gate whose whole job is to catch the print that is too
    long, so it is gone even though both forms sit inside the declared +/-30%.

    Degenerate geometry (a "layer" thicker than the line is wide) cannot be
    squished into anything and is not modelled: the stadium collapses to a
    circle of diameter ``width``, which is the most material that orifice can
    lay down per millimetre. ``fdm.process_model_valid`` refuses that projection
    separately; this is only here so the arithmetic cannot go negative.
    """
    if layer_h <= 0 or width <= 0:
        raise ValueError(f"bead geometry must be positive, got h={layer_h}, w={width}")
    if layer_h >= width:
        return math.pi * width * width / 4.0
    return layer_h * (width - layer_h) + math.pi * layer_h * layer_h / 4.0


def extruded_volume_mm3(
    solid_mm3: float,
    *,
    infill: float,
    perimeters: int,
    width: float,
    surface_area_mm2: float | None = None,
) -> tuple[float, str]:
    """Volume of plastic actually laid down, mm^3, and a note on how it was got.

    Shell plus infill of whatever is left. With a projected surface area the
    shell is ``perimeters x width x area``, capped at the solid volume; without
    one it falls back to :data:`SHELL_FRACTION_DEFAULT`, and the note says so,
    because a reader has to be able to tell a measured shell from a guessed one.
    """
    if surface_area_mm2 is not None:
        shell = min(solid_mm3, float(surface_area_mm2) * perimeters * width)
        note = f"{perimeters}x{width:.2f} mm shell on {float(surface_area_mm2):.0f} mm^2"
    else:
        shell = SHELL_FRACTION_DEFAULT * solid_mm3
        note = f"shell assumed {SHELL_FRACTION_DEFAULT:.0%} (no surface_area_mm2)"
    return shell + infill * (solid_mm3 - shell), note


def path_length_mm(extruded_mm3: float, layer_h: float, width: float) -> float:
    """Millimetres of bead that carry ``extruded_mm3`` of plastic."""
    return extruded_mm3 / bead_area_mm2(layer_h, width)


def print_hours(extruded_mm3: float, speed: float, layer_h: float, width: float) -> float:
    """Wall-clock hours to lay ``extruded_mm3`` down at ``speed`` mm/s."""
    seconds = path_length_mm(extruded_mm3, layer_h, width) / (speed * DUTY_FACTOR)
    return (seconds + STARTUP_OVERHEAD_S) / 3600.0


def speed_for_hours(extruded_mm3: float, hours: float, layer_h: float, width: float) -> float:
    """The inverse of :func:`print_hours`: the speed that takes exactly ``hours``.

    This is what the negative control calls. Solving the model rather than
    guessing a number is what keeps the fixture bad for *every* projection: the
    speed it produces is by construction the one that puts this part past this
    project's ceiling, whatever the part and whatever the ceiling.

    Raises if the requested time is shorter than the fixed startup overhead —
    no speed achieves that, and a fixture that silently returned a negative or
    infinite speed would be bad in the wrong way.
    """
    moving_s = hours * 3600.0 - STARTUP_OVERHEAD_S
    if moving_s <= 0:
        raise ValueError(
            f"{hours:.3f} h is inside the {STARTUP_OVERHEAD_S:.0f} s startup overhead; "
            f"no printing speed reaches it"
        )
    return path_length_mm(extruded_mm3, layer_h, width) / (moving_s * DUTY_FACTOR)


def grams(extruded_mm3: float, density_g_cm3: float) -> float:
    """Filament mass, g. mm^3 -> cm^3 is /1000, then density."""
    return extruded_mm3 / 1000.0 * density_g_cm3
