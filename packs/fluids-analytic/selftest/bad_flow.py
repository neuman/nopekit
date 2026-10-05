# SPDX-License-Identifier: Apache-2.0
"""Known-bad flow fixtures. One physical change each, nothing else.

Same discipline as the hydrostatic fixtures next door: the baseline is the pack's
own `selftest/baseline.json` — the instrument raft, its sensor strut and its
sampling line, loaded from disk — and each function moves exactly one physically
meaningful quantity in the direction the gate under test cares about: speed up,
bore down, flow down. The fixture is SEALED: params are `{**BASE, **override}`
with the host project's projection deliberately not inherited, because the gates
read synonym families (`characteristic_length_m` outranks `diameter_m`,
`max_pressure_drop_pa` outranks `available_head_m`) and a project key the
baseline never mentions would otherwise land in the middle of the control and
quietly defuse it.

The speed fixture is the one worth reading twice. Tripling the velocity also
triples the Reynolds number, and if that carried the flow out of the tabulated Cd
band the drag gate would SKIP on validity instead of FAILING on force — which
would leave the gate's ability to detect excess drag unproven while the selftest
still looked busy. The multiplier here is bounded by that band, not by the
budget: 3x on the baseline's 0.55 m/s lands at Re 9.9e4, inside the cylinder's
1e4-2e5. Getting a negative control to fail for the right reason is most of the
work in writing one.
"""
from __future__ import annotations

import dataclasses
import math

from gates._fluids_analytic import (
    RE_LAMINAR_MAX, RE_TURBULENT_MIN, baseline,
)

#: A plausible, passing design: this pack's own baseline projection.
BASE: dict = baseline()


def _ctx(ctx, **override):
    """The baseline with one override, as a context the gate can read.

    The real context is reused for its root, ledger and out_dir; its params are
    replaced outright rather than merged, so nothing the host project declares
    can reach the gate and shift the quantity under test.
    """
    return dataclasses.replace(ctx, params={**BASE, **override})


def _external_only(**override):
    """The baseline with every internal-flow key removed.

    Used by the regression guard below. A strut, a float, a towed body or a hull
    with no piping at all is this pack's headline case, and a projection that
    carries no pipe is what it looks like.
    """
    pipe_keys = ("pipe_diameter_m", "pipe_length_m", "pipe_roughness_m",
                 "flow_rate_m3_s", "minor_loss_k_total", "available_head_m",
                 "static_lift_m")
    params = {k: v for k, v in BASE.items() if k not in pipe_keys}
    params.update(override)
    return params


def oversped(ctx):
    """fluid.drag — the same body at 3x the speed, so ~9x the force.

    Drag goes as v^2 and nothing else about the body changes: same shape class,
    same frontal area, same fluid. Reynolds rises to 9.9e4, still inside the
    1e4-2e5 band the cylinder's Cd was measured over, so the gate has to fail on
    the force it computed rather than skipping on validity.

    The multiplier is the one severity in this pack that is NOT solved back from
    the acceptance it must beat, and the reason is that the Cd band constrains it
    more tightly than the budget does: the fixture is only allowed to move the
    speed within a factor of ~6 before `fluid.drag` stops measuring force and
    starts refusing on regime, and inside that window the quadratic makes 3x the
    speed 9x the force against a budget the baseline passes with 2.6x of room.
    """
    return _ctx(ctx, flow_velocity_m_s=float(BASE["flow_velocity_m_s"]) * 3.0)


def pinched_bore(ctx):
    """fluid.pipe_pressure_drop — the same flow through 60% of the bore.

    One substituted fitting, one undersized hose, one line drawn at nominal size
    instead of internal diameter. At fixed volumetric flow the velocity goes as
    1/D^2 and the drop as roughly 1/D^5, so this is about twelve times the
    pressure loss with the same length, the same roughness and the same fittings,
    against an unchanged head. Reynolds rises but stays firmly turbulent and e/D
    stays far inside the Moody bound, so the gate fails on the loss it measures
    rather than on regime validity.
    """
    return _ctx(ctx, pipe_diameter_m=float(BASE["pipe_diameter_m"]) * 0.6)


def transitional_line(ctx):
    """fluid.flow_regime — the pump turned down until Re lands in the middle of the gap.

    The target is the MIDPOINT of the pack's own transition band rather than a
    typed 3000, so the fixture follows `RE_LAMINAR_MAX` and `RE_TURBULENT_MIN` if
    either is ever revised: squarely inside the range where 64/Re is no longer
    exact and Colebrook-White was never fitted, and where the true friction factor
    can sit anywhere between the two curves. Geometry, fluid and fittings are
    untouched. This is the exact case that makes the regime gate worth having:
    Darcy-Weisbach returns a perfectly confident number here and it may be wrong
    by two to one.
    """
    _assert_external_only_is_classified(ctx)
    d = float(BASE["pipe_diameter_m"])
    nu = float(BASE["kinematic_viscosity_m2_s"])
    area = math.pi * d * d / 4.0
    re_target = (RE_LAMINAR_MAX + RE_TURBULENT_MIN) / 2.0
    return _ctx(ctx, flow_rate_m3_s=re_target * nu * area / d)


# --------------------------------------------------------------------------- #
# positive regression, carried by the control it protects
# --------------------------------------------------------------------------- #
def _assert_external_only_is_classified(ctx):
    """`fluid.flow_regime` must reach a VERDICT on a projection with no pipe in it.

    This is not a negative control and it is not pretending to be one. It is the
    one property of this gate that no known-bad fixture can ever cover, because
    the case is a PASS: an external-only flow whose Reynolds number is INSIDE its
    tabulated Cd band.

    That case was broken. The gate assigned its `measured` value only on the
    branches where something was wrong, so a strut, a float or a hull with no
    piping — this pack's headline use case — fell through to
    `skip("no flow to classify: give pipe_diameter_m ... or flow_velocity_m_s
    with characteristic_length_m")`, naming keys the model had already supplied.
    `fluid.flow_regime` binds to every flow claim in the pack, so that skip took
    the drag claim BLOCKED with it, for a reason that was untrue. Neither the
    negative control nor the baseline could catch it: both always carry a pipe.

    It lives inside `transitional_line` because that is the fixture for the gate
    it protects, and because a fixture that raises reports the control as
    UNUSABLE — never as a pass. The gate function is found by file path rather
    than by module name: the pack loader imports gate modules under a namespaced
    name of its own, so `import gates.flow` is not available here.
    """
    import os
    import sys

    from nopekit.gates import run_gate

    flow_py = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "gates", "flow.py")
    module = next(
        (m for m in list(sys.modules.values())
         if getattr(m, "__file__", None)
         and os.path.abspath(m.__file__) == flow_py
         and hasattr(m, "flow_regime")),
        None)
    if module is None:
        raise RuntimeError(
            f"cannot locate the loaded {flow_py} to regression-check fluid.flow_regime "
            f"on an external-only projection — the check must not be skipped silently, "
            f"so the control reports itself unusable instead")

    spec, fn = module.flow_regime.gate_spec, module.flow_regime
    verdict = run_gate(spec, fn, dataclasses.replace(ctx, params=_external_only()))
    if verdict.skipped or not verdict.passed:
        raise AssertionError(
            f"fluid.flow_regime does not settle an external-only flow inside its own Cd "
            f"band — the pack's headline case. Baseline strut, every pipe key removed, "
            f"Re {BASE['flow_velocity_m_s'] * BASE['diameter_m'] / BASE['kinematic_viscosity_m2_s']:.0f} "
            f"inside the {BASE['drag_shape_class']} band. Got: "
            f"{verdict.skip_reason or verdict.detail or verdict.error}")
