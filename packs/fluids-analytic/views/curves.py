# SPDX-License-Identifier: Apache-2.0
"""The righting arm against heel angle, with the demand on it drawn across.

``fluid.righting_arm`` answers at one angle: *does the hull develop 0.217 m of arm
at 8 degrees, against the 0.164 m the heeling moment demands*. Yes. What the line
cannot say is how that margin behaves either side of the design angle — and
stability is a question about a curve, not a point. GZ climbs with heel here, so a
hull that clears its demand at 8 degrees may be under it at 4, and the verdict
about 8 degrees says nothing at all about 4.

**The curve stops at the small-angle ceiling, and the chart says so.** GZ =
GM·sin(θ) is the initial slope of the real righting-arm curve; past about ten
degrees the immersed shape has moved, the deck edge may be in the water, and the
closed form over-predicts the arm the hull actually develops. The gate SKIPS past
that angle rather than answering. A chart drawn to forty degrees from the same
formula would commit the identical over-prediction in a picture — and a picture is
the more persuasive of the two, so it would be the worse lie. The x-axis ends at
``SMALL_ANGLE_MAX_DEG``, the ceiling is in ``meta``, and what lies past it is a
full hydrostatic GZ curve at each angle, which is a tier-2 job this pack does not
do.

The arithmetic is the gates' own (``gates/_fluids_analytic``), never a second copy:
a chart that can disagree with the verdict beside it leaves a reader no way to tell
which is the project's status.
"""
from __future__ import annotations

import math
import os
import sys
from typing import Any

from nopekit.models import View, ViewKind
from nopekit.site import ViewContext, viewgen

# The gates import these as `gates._fluids_analytic` with the pack directory on
# sys.path; do the same here so both hold one module object rather than two copies
# of the hydrostatics.
_PACK_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PACK_DIR not in sys.path:
    sys.path.insert(0, _PACK_DIR)

from gates._fluids_analytic import (  # noqa: E402
    G, BEAM_KEYS, HEEL_KEYS, MIN_GZ_BEAM_FRACTION, SMALL_ANGLE_MAX_DEG,
    gm as resolve_gm, hydro, number,
)

#: Samples from upright to the ceiling. One every quarter degree: the curve is a
#: sine over ten degrees, so it is very nearly a straight line and forty points
#: draw it exactly. The payload stays small enough to sit inline in ``state.json``.
SAMPLES = 41


@viewgen(
    id="righting_arm_curve",
    kind=ViewKind.CHART,
    title="Righting arm vs heel angle",
    description="GZ from upright to the small-angle ceiling, against the arm the "
                "hull is required to develop, with the design heel marked.",
    order=20,
    gates=["fluid.righting_arm", "fluid.metacentric"],
)
def righting_arm_curve(ctx: ViewContext) -> View | None:
    """GZ = GM·sin(θ) from 0 to the small-angle ceiling, with the demand line."""
    h = hydro(ctx, "righting_arm_curve")
    if not hasattr(h, "mass"):
        # `hydro` returns a SKIP verdict when the projection has no hull in it.
        # Nothing to draw is the normal outcome for a pack whose domain this
        # project does not touch, and it is not an error.
        return None
    resolved = resolve_gm(ctx, h, "righting_arm_curve")
    if not isinstance(resolved, tuple):
        return None
    gm_m, bm, bg, notes = resolved
    if not math.isfinite(gm_m):
        return None

    weight = h.mass * G

    # The acceptance side, resolved exactly as `fluid.righting_arm` resolves it and
    # in the same order of preference — a chart drawing a different line from the
    # gate's would be a second opinion about the same hull.
    stated = number(ctx, ("min_righting_arm_m", "min_gz_m"), None)
    heeling_moment = number(ctx, ("heeling_moment_nm", "heeling_moment_n_m",
                                  "upsetting_moment_nm"), None)
    if stated is not None:
        limit, basis = float(stated), "stated min_righting_arm_m"
    elif heeling_moment is not None and float(heeling_moment) > 0 and weight > 0:
        limit = float(heeling_moment) / weight
        basis = f"arm demanded by a {float(heeling_moment):.0f} N.m heeling moment"
    elif h.beam:
        limit = MIN_GZ_BEAM_FRACTION * h.beam
        basis = f"{MIN_GZ_BEAM_FRACTION:.0%} of {h.beam:.3f} m beam (pack default)"
    else:
        # No limit is not a chart. A GZ curve with no line across it invites the
        # reader to decide for themselves whether the arm is enough, which is the
        # judgement the ledger is supposed to be carrying.
        return None

    step = SMALL_ANGLE_MAX_DEG / (SAMPLES - 1)
    points = [[round(i * step, 4), round(gm_m * math.sin(math.radians(i * step)), 6)]
              for i in range(SAMPLES)]

    heel = number(ctx, HEEL_KEYS, None)
    markers = []
    if heel is not None and 0 < float(heel) <= SMALL_ANGLE_MAX_DEG:
        heel = float(heel)
        gz = gm_m * math.sin(math.radians(heel))
        markers.append({
            "key": "design",
            "series": "gz",
            "x": round(heel, 4),
            "y": round(gz, 6),
            "label": f"design heel {heel:g} deg -> GZ {gz:.4f} m, righting moment "
                     f"{weight * gz:.0f} N.m ({gz / limit * 100:.0f}% of the demand)",
        })
    # No marker when the model states no heel, and none when it states one past the
    # ceiling. Both are deliberate: `fluid.righting_arm` SKIPS in either case, and
    # a marked design point on a chart whose gate settled nothing would show a
    # number the ledger does not carry.

    meta: dict[str, Any] = {
        "x": {"key": HEEL_KEYS[0], "label": "heel angle", "units": "deg"},
        "y": {"label": "righting arm GZ", "units": "m"},
        "gate": "fluid.righting_arm",
        "basis": f"GZ = GM.sin(theta), GM {gm_m:.4f} m = BM {bm:.4f} - BG {bg:.4f}",
        "valid_to_deg": SMALL_ANGLE_MAX_DEG,
        "validity": (f"small-angle only. Past {SMALL_ANGLE_MAX_DEG:.0f} deg the "
                     f"waterplane has moved and the deck edge may be immersed, so "
                     f"GM.sin(theta) over-predicts the real arm — that region needs a "
                     f"full hydrostatic GZ curve at each angle, not this formula, and "
                     f"the gate skips rather than answering there"),
    }
    if h.notes or notes:
        # Every approximation that produced this GM rides on the chart as well as
        # in the verdict: a rectangular waterplane is OPTIMISTIC for a finer hull,
        # and a curve is exactly where an optimistic assumption stops being visible.
        meta["approximations"] = list(h.notes) + list(notes)

    return View(
        id="righting_arm_curve",
        kind=ViewKind.CHART,
        title="Righting arm vs heel angle",
        data={
            "series": [{
                "key": "gz",
                "label": "GZ = GM.sin(theta)",
                "units": "m",
                "points": points,
            }],
            "limit": {
                "value": round(limit, 6),
                "label": f"{limit:.4f} m required [{basis}]",
                "comparator": ">=",
                "series": "gz",
            },
            "markers": markers,
        },
        meta=meta,
    )
