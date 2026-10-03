# SPDX-License-Identifier: Apache-2.0
"""beam-analytic — closed-form structural gates. Stdlib only, every gate tier 0.

This is the analytic-first pack: it runs on a fresh install with nothing
installed, in microseconds, and it is what an agent should reach for before it
proposes downloading a solver. Eight gates, all pure arithmetic:

    beam.deflection        absolute sag against a stated limit
    beam.deflection_ratio  sag as a span fraction (L/180, L/360 ...)
    beam.bending_stress    extreme-fibre stress as a utilisation
    beam.shear_stress      transverse shear, which governs on low-shear-strength
                           materials and thin webs, NOT merely on short spans
    beam.buckling          Euler / Johnson critical load with an end factor K
    beam.bearing           fastener hole bearing stress
    beam.model_validity    the slenderness guard on the five beam-theory gates
    beam.input_sanity      the units and signs of the projection itself

Three commitments hold this pack together.

**It reads, it does not re-derive.** Every number comes from ``ctx.params`` — the
model's projection. A gate that recomputes a derived value is checking its own
arithmetic instead of the model's, and when the two disagree the gate is the one
that is wrong (method rule 1 and 2).

**It skips rather than guesses.** A physical quantity the projection does not
carry — modulus, yield, span, section — makes the gate return SKIPPED with the
keys it looked for. The claim then resolves BLOCKED and stays visible. Policy
numbers (safety factor, serviceability ratio, load case) have documented defaults
and every verdict that used one says so on its own detail line.

**It knows where it stops.** ``beam.model_validity`` exists because five of the
other gates are Euler-Bernoulli, which silently under-predicts deflection on a
stubby member, on a low-G material, and under a stiff support case. A pack that
ships closed-form analysis without a validity gate ships a number that quietly
becomes optimistic as the design moves, and nothing in the report would ever say
so. (``beam.bearing`` is the one gate it does not guard: P/(n*d*t) is not beam
theory and does not care how slender the member is.)

**It refuses a malformed projection outright.** ``beam.input_sanity`` is the
eighth gate and the only one that FAILS rather than skips on a bad input, because
a load carrying a minus sign or a modulus in pascals is not a missing number, it
is a wrong one, and a wrong number produces seven confident PASSes on a member
nobody analysed.

Units everywhere: mm, N, MPa (N/mm^2), mm^4. See ``references/formulae.md``.
"""
from __future__ import annotations

import json
import math
import os
import sys

from atompipe.gates import gate, GateContext
from atompipe.models import NegativeControl, Tier, Verdict

# The shared arithmetic lives in a leading-underscore module, which the pack
# loader deliberately does NOT import as a gate module. Reaching it needs this
# directory on sys.path because the loader imports gate files by file location,
# so there is no package for a relative import to climb.
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import _beam_analytic_lib as B  # noqa: E402


# --------------------------------------------------------------------------- #
# small shared plumbing
# --------------------------------------------------------------------------- #
def _skip(gate_id: str, miss: B.MissingParam) -> Verdict:
    """A missing physical input is a SKIP that names the key, never a pass."""
    return Verdict(gate=gate_id, passed=False, skipped=True, skip_reason=miss.reason)


def _evidence(ctx: GateContext, name: str, payload: dict) -> list[str]:
    """Write the full working to ``out_dir`` and cite it.

    The verdict is one dense line by contract; the formula, every input and every
    intermediate go here, so a PROVEN row in the readiness report can be audited
    two months later without re-running anything. A filesystem that refuses the
    write costs the citation, not the verdict — a gate that crashes because it
    could not write a log has converted a measurement into an outage.
    """
    try:
        path = ctx.out_path("beam-analytic", f"{name}.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, sort_keys=True, default=str)
        return [path]
    except OSError:
        return []


def _sf(ctx: GateContext) -> tuple[float, str]:
    sf, defaulted = B.opt(ctx, ("safety_factor", "sf", "fos"), B.DEFAULT_SAFETY_FACTOR)
    if sf <= 0:
        raise B.MissingParam(["safety_factor"], f"a positive safety factor (got {sf!r})")
    return sf, (" [pack default]" if defaulted else "")


# --------------------------------------------------------------------------- #
# 1. deflection
# --------------------------------------------------------------------------- #
@gate(
    id="beam.deflection",
    title="Deflection under load, against an absolute limit",
    claims=["structural", "stiffness", "deflection"],
    tier=Tier.INSTANT,
    settles="tip deflection",
    # Prerequisite beam.model_validity (P2.2-D12): this number is Euler-Bernoulli's,
    #    and every way the guard fails — L/h under its floor, or the shear share
    #    the theory omits past its limit — is a beam where it does not apply.
    #    Isolated: the guard passes this gate's own known-bad control
    #    (test_packs.ControlsAreIsolated).
    needs=["beam.model_validity"],
    negative_control=NegativeControl(
        fixture="selftest/bad_beams.py:shallow_section",
        note="the same beam with its section depth derived to land 15% past the "
             "baseline's own deflection limit; I goes as h^3 and nothing else moves",
    ),
)
def deflection(ctx: GateContext) -> Verdict:
    """Maximum deflection vs a stated millimetre limit.

    delta = k_d * P * L^3 / (E*I), with k_d fixed by the support case
    (1/3 cantilever end load, 1/48 simply supported centre, 1/192 fixed-fixed
    centre, and so on — see references/formulae.md for the whole table).

    The limit is a product decision, not a physical one, so the pack refuses to
    invent it: with no ``deflection_limit_mm`` in the projection this gate skips
    and says so, and ``beam.deflection_ratio`` covers the span-fraction form that
    building and shelving conventions actually use.
    """
    try:
        L = B.span(ctx)
        P = B.load(ctx)
        sec = B.section(ctx)
        E, e_src = B.modulus(ctx)
        case, (k_d, _k_m, _k_v, _k_s, _note), case_default = B.case_of(ctx)
        limit = B.num(
            ctx, ("deflection_limit_mm", "max_deflection_mm", "deflection_allow_mm"),
            "an absolute deflection limit (deflection_limit_mm) — how much sag this "
            "part may have is a product decision this pack will not invent; "
            "beam.deflection_ratio checks the span-fraction form instead",
        )
    except B.MissingParam as miss:
        return _skip("beam.deflection", miss)

    d = B.deflection_mm(k_d, P, L, E, sec["I"])
    ev = _evidence(ctx, "deflection", {
        "formula": "delta = k_d * P * L^3 / (E*I)",
        "case": case, "k_d": k_d, "P_n": P, "span_mm": L,
        "E_mpa": E, "E_source": e_src, "I_mm4": sec["I"], "section": sec["desc"],
        "deflection_mm": d, "limit_mm": limit,
        "assumptions": [
            "Euler-Bernoulli: shear deflection ignored (see beam.model_validity)",
            "small deflections, linear elastic, prismatic section, load in a principal plane",
            "the support case is REALLY the declared one — see lenses.md",
        ],
    })
    return Verdict(
        gate="beam.deflection",
        passed=d <= limit,
        measured=round(d, 4),
        limit=round(limit, 4),
        units="mm",
        detail=f"{case}{' [pack default]' if case_default else ''}: {d:.3f} mm at "
               f"{P:g} N over {L:g} mm ({sec['desc']}, E {E:.0f} MPa [{e_src}]) "
               f"vs {limit:g} mm limit",
        evidence=ev,
    )


# --------------------------------------------------------------------------- #
# 2. deflection as a span fraction
# --------------------------------------------------------------------------- #
@gate(
    id="beam.deflection_ratio",
    title="Deflection as a span fraction (L/180, L/360 style serviceability)",
    claims=["structural", "stiffness", "serviceability", "deflection"],
    tier=Tier.INSTANT,
    settles="deflection ratio",
    # Prerequisite beam.model_validity (P2.2-D12): this number is Euler-Bernoulli's,
    #    and every way the guard fails — L/h under its floor, or the shear share
    #    the theory omits past its limit — is a beam where it does not apply.
    #    Isolated: the guard passes this gate's own known-bad control
    #    (test_packs.ControlsAreIsolated).
    needs=["beam.model_validity"],
    negative_control=NegativeControl(
        fixture="selftest/bad_beams.py:long_span",
        note="the same beam, load and section on the span that puts the deflection "
             "ratio 15% past the baseline's own L/N limit and trips no other gate",
    ),
)
def deflection_ratio(ctx: GateContext) -> Verdict:
    """Deflection expressed as L/N, against an L/N limit.

    This is the form shelving, framing and enclosure-panel conventions actually
    use, and it is the one that survives a change of span: L/180 is roughly where
    sag becomes visible against a straight edge, L/240 is a common general limit,
    L/360 is what brittle finishes and long visual reference lines want.

    The denominator is policy. With no ``deflection_ratio_limit`` in the
    projection the pack uses L/180 — the loosest limit in common use — and says
    on the verdict line that it did.
    """
    try:
        L = B.span(ctx)
        P = B.load(ctx)
        sec = B.section(ctx)
        E, e_src = B.modulus(ctx)
        case, (k_d, _k_m, _k_v, _k_s, _note), case_default = B.case_of(ctx)
        denom, denom_default = B.opt(
            ctx, ("deflection_ratio_limit", "span_over_deflection_limit", "l_over_d_limit"),
            B.DEFAULT_RATIO_LIMIT,
        )
    except B.MissingParam as miss:
        return _skip("beam.deflection_ratio", miss)

    d = B.deflection_mm(k_d, P, L, E, sec["I"])
    # A rigid-body-perfect beam divides by zero; report it as an enormous ratio
    # rather than crashing, because "infinitely stiff" is a pass, not an error.
    ratio = (L / d) if d > 0 else float("inf")
    shown = f"{ratio:.0f}" if math.isfinite(ratio) else "inf"
    ev = _evidence(ctx, "deflection_ratio", {
        "formula": "N = L / delta,  delta = k_d * P * L^3 / (E*I)",
        "case": case, "k_d": k_d, "P_n": P, "span_mm": L, "E_mpa": E, "E_source": e_src,
        "I_mm4": sec["I"], "deflection_mm": d, "span_over_deflection": ratio,
        "limit_denominator": denom, "limit_defaulted": denom_default,
    })
    return Verdict(
        gate="beam.deflection_ratio",
        passed=ratio >= denom,
        measured=round(ratio, 1) if math.isfinite(ratio) else None,
        limit=round(denom, 1),
        units="span/deflection",
        detail=f"{case}: {d:.3f} mm over {L:g} mm = L/{shown} vs L/{denom:g} limit"
               f"{' [pack default]' if denom_default else ''}",
        evidence=ev,
    )


# --------------------------------------------------------------------------- #
# 3. bending stress
# --------------------------------------------------------------------------- #
@gate(
    id="beam.bending_stress",
    title="Extreme-fibre bending stress within the design allowable",
    claims=["structural", "strength", "stress"],
    tier=Tier.INSTANT,
    settles="bending stress",
    # Prerequisite beam.model_validity (P2.2-D12): this number is Euler-Bernoulli's,
    #    and every way the guard fails — L/h under its floor, or the shear share
    #    the theory omits past its limit — is a beam where it does not apply.
    #    Isolated: the guard passes this gate's own known-bad control
    #    (test_packs.ControlsAreIsolated).
    needs=["beam.model_validity"],
    negative_control=NegativeControl(
        fixture="selftest/bad_beams.py:overloaded",
        note="the same beam at the load that puts bending stress 15% past the "
             "allowable; stress is linear in load and the geometry is untouched",
    ),
)
def bending_stress(ctx: GateContext) -> Verdict:
    """Peak fibre stress vs yield/SF, reported as utilisation.

    sigma = M*c / I, with M = k_m * P * L at whichever station the case puts the
    peak moment (the root of a cantilever, mid-span of a simple beam, the ends of
    a fixed-fixed beam under a distributed load).

    Utilisation rather than MPa is the headline number because a ratio is what a
    person can act on: 0.25 means three quarters of the section is spare, 1.1
    means it is over and the amount of over is visible at a glance.
    """
    try:
        L = B.span(ctx)
        P = B.load(ctx)
        sec = B.section(ctx)
        case, (_k_d, k_m, _k_v, _k_s, note), case_default = B.case_of(ctx)
        sf, sf_note = _sf(ctx)
        allow, allow_src = B.allowable_direct(ctx, sf)
    except B.MissingParam as miss:
        return _skip("beam.bending_stress", miss)

    M = k_m * P * L
    sigma = M * sec["c"] / sec["I"]
    util = sigma / allow if allow > 0 else float("inf")
    ev = _evidence(ctx, "bending_stress", {
        "formula": "sigma = M*c/I,  M = k_m * P * L",
        "case": case, "case_note": note, "k_m": k_m, "P_n": P, "span_mm": L,
        "moment_nmm": M, "c_mm": sec["c"], "I_mm4": sec["I"], "section": sec["desc"],
        "stress_mpa": sigma, "allowable_mpa": allow, "allowable_source": allow_src,
        "safety_factor": sf, "utilisation": util,
        "assumptions": [
            "no stress concentration: holes, fillets, notches and steps are NOT modelled",
            "static load only — see lenses.md on fatigue",
            "bending about a principal axis, load through the shear centre",
        ],
    })
    return Verdict(
        gate="beam.bending_stress",
        passed=util <= 1.0,
        measured=round(util, 3),
        limit=1.0,
        units="utilisation",
        detail=f"{case}{' [pack default]' if case_default else ''}: {sigma:.1f} MPa vs "
               f"{allow:.1f} MPa allowable ({allow_src}{sf_note}) — util {util:.2f}; "
               f"nominal, no stress concentration",
        evidence=ev,
    )


# --------------------------------------------------------------------------- #
# 4. transverse shear
# --------------------------------------------------------------------------- #
@gate(
    id="beam.shear_stress",
    title="Transverse shear stress within the design allowable",
    claims=["structural", "strength", "shear"],
    tier=Tier.INSTANT,
    settles="transverse shear stress",
    # Prerequisite beam.input_sanity (P2.2-D12): a load with the wrong sign or a
    #    length in metres makes this arithmetic meaningless, and the sanity guard
    #    fails only on exactly those.
    #    Isolated: the guard passes this gate's own known-bad control
    #    (test_packs.ControlsAreIsolated).
    needs=["beam.input_sanity"],
    # No edge to beam.model_validity: this gate's own control, shear_governed
    # (L/h 6.0, about 32% of the deflection omitted), fails the slenderness guard,
    # which would pre-empt the control wherever both run; and VQ/Ib does not rest
    # on Euler-Bernoulli's no-shear assumption.
    negative_control=NegativeControl(
        fixture="selftest/bad_beams.py:shear_governed",
        note="the same member in plywood under a distributed load — the regime where "
             "shear genuinely governs, at L/h 6, with bending still inside its allowable",
    ),
)
def shear_stress(ctx: GateContext) -> Verdict:
    """Peak transverse shear vs the shear allowable.

    tau = V*Q / (I*t), V = k_v * P. For a rectangle that collapses to 1.5*V/A,
    for a solid round to 1.33*V/A, for a thin tube to about 2*V/A.

    **Where this gate actually earns its place.** For a rectangle the two
    stresses are in the ratio

        sigma/tau = 4 * (k_m/k_v) * (L/h)

    so shear governs below L/h = R/(4*k_m/k_v), where R is the ratio of the
    normal allowable to the shear allowable. With the von Mises estimate for a
    ductile metal R = 1/0.577 = 1.73 and that crossover is L/h = 0.43 for
    ``cantilever_end``, 0.87 for ``cantilever_udl`` and
    ``simply_supported_centre``, 1.73 for ``simply_supported_udl`` and
    ``fixed_fixed_centre``, 2.17 for ``propped_cantilever_udl`` and 2.60 for
    ``fixed_fixed_udl``. Every one of those is far below the L/h = 5 this pack
    declares as its own validity floor, so **on a solid metal section this gate
    cannot be the binding constraint anywhere inside the pack's valid regime.**
    An earlier version of this docstring claimed the crossover was "around
    L/h ~ 5, the same boundary as beam.model_validity"; that was wrong by about
    an order of magnitude for a cantilever and the unity built on it was
    fabricated.

    It earns its place on the three cases where R is large or Q/t is:

    * **Wood.** R is about 8.6 for birch plywood and 8.2 for softwood, because
      shear strength along the grain is roughly a tenth of bending strength. A
      simply supported plywood beam under a distributed load is shear-governed
      below L/h = 8.6, which is inside the regime the pack calls valid. This is
      why timber beams split horizontally near supports.
    * **Thin webs.** ``tau = V*Q/(I*t)`` carries the web width directly, so an
      explicit section with a thin web can be shear-governed at any slenderness.
    * **Bond lines.** The stress is right and the allowable is not: along a glue
      line, a weld or a printed layer the material's shear strength is a
      different and much lower number. See ``references/materials.md``.

    Printed polymers are NOT on that list: the table's derated shear values are
    0.6-0.64 of the design stress, so R is about 1.6 and shear governs even later
    than it does for a metal.

    There is a real relationship with ``beam.model_validity``, but it is not the
    one the old docstring claimed, and it runs the other way: for every material
    and support case in this pack, the region where shear STRESS governs lies
    entirely inside the region where shear DEFLECTION is more than 10% of the
    answer. Wherever this gate binds, the deflection numbers are already
    untrustworthy. ``references/formulae.md`` section 3 works it out.

    The allowable is stated if the model states one, and otherwise estimated as
    0.577*yield (von Mises). That estimate is for ductile METALS. Applied to
    plywood it overstates the allowable by roughly five times and applied across
    the layers of a printed part it is worse, so the pack's material table
    carries real shear numbers for those and the verdict line always says which
    route it took.
    """
    try:
        L = B.span(ctx)
        P = B.load(ctx)
        sec = B.section(ctx)
        case, (_k_d, _k_m, k_v, _k_s, _note), case_default = B.case_of(ctx)
        sf, sf_note = _sf(ctx)
        if sec["Q"] is None or sec["t_web"] is None:
            raise B.MissingParam(
                ["first_moment_mm3", "web_width_mm"],
                "the first moment Q and the web width t at the neutral axis, which an "
                "explicit I_mm4 section does not imply (a rect/circle/tube section "
                "gives this pack both for free)",
            )
        allow, allow_src = B.allowable_shear(ctx, sf)
    except B.MissingParam as miss:
        return _skip("beam.shear_stress", miss)

    V = k_v * P
    tau = V * sec["Q"] / (sec["I"] * sec["t_web"])
    util = tau / allow if allow > 0 else float("inf")
    slender = L / sec["depth"] if sec["depth"] > 0 else float("inf")
    ev = _evidence(ctx, "shear_stress", {
        "formula": "tau = V*Q/(I*t),  V = k_v * P",
        "case": case, "k_v": k_v, "P_n": P, "shear_n": V,
        "Q_mm3": sec["Q"], "I_mm4": sec["I"], "t_web_mm": sec["t_web"],
        "section": sec["desc"], "shear_stress_mpa": tau,
        "allowable_mpa": allow, "allowable_source": allow_src,
        "safety_factor": sf, "utilisation": util, "slenderness_l_over_h": slender,
        "assumptions": [
            "the section is prismatic and the load is not applied within ~h of a support",
            "shear allowable is across the section, NOT along a bond line or print layer",
        ],
    })
    return Verdict(
        gate="beam.shear_stress",
        passed=util <= 1.0,
        measured=round(util, 3),
        limit=1.0,
        units="utilisation",
        detail=f"{case}: V {V:g} N -> tau {tau:.2f} MPa vs {allow:.2f} MPa allowable "
               f"({allow_src}{sf_note}) — util {util:.2f} at L/h {slender:.1f}",
        evidence=ev,
    )


# --------------------------------------------------------------------------- #
# 5. buckling
# --------------------------------------------------------------------------- #
@gate(
    id="beam.buckling",
    title="Axial compression against the critical buckling load",
    claims=["structural", "stability", "buckling"],
    tier=Tier.INSTANT,
    settles="critical buckling load",
    # Prerequisite beam.model_validity (P2.2-D12): this number is Euler-Bernoulli's,
    #    and every way the guard fails — L/h under its floor, or the shear share
    #    the theory omits past its limit — is a beam where it does not apply.
    #    Isolated: the guard passes this gate's own known-bad control
    #    (test_packs.ControlsAreIsolated).
    needs=["beam.model_validity"],
    negative_control=NegativeControl(
        fixture="selftest/bad_beams.py:slender_strut",
        note="the identical strut at the unbraced length that puts the factored axial "
             "load 15% past P_cr; only the length the strut holds that load over moves",
    ),
)
def buckling(ctx: GateContext) -> Verdict:
    """Euler critical load, with a Johnson cut-off for stubby columns.

    P_cr = pi^2 * E * I_min / (K*L)^2, K set by the END CONDITION and not by
    hope. K is the one input people get wrong: a "fixed" end that is a bolt in a
    slot is pinned, and taking K = 0.5 for a joint that is really K = 1.0
    overstates the critical load by four.

    Euler is only true while the column is slender enough to buckle elastically.
    Below the transition slenderness lambda_1 = sqrt(2*pi^2*E/sigma_y) the member
    yields before it buckles and Euler over-predicts, sometimes wildly, so this
    gate switches to the Johnson parabola there and says which branch it used.

    I_min is the WEAK axis. A rectangle bent the strong way buckles the other
    way, and a gate that used the bending I would report a strut four times
    stronger than it is — so on an explicit ``I_mm4`` section this gate SKIPS for
    want of ``i_min_mm4`` rather than quietly substituting the bending I. That
    fallback used to be here; it was silent, unconservative, and it was on the
    one route a caller reaches for a shape the pack has no formula for, which is
    exactly where I_min/I is least likely to be 1.
    """
    try:
        sec = B.section(ctx)
        E, e_src = B.modulus(ctx)
        sy, y_src = B.yield_stress(ctx)
        sf, sf_note = _sf(ctx)
        axial_names, axial_sense = B.LOAD_KEYS["axial_load_n"]
        axial = B.unsigned_load(
            B.num(ctx, axial_names,
                  "an axial compressive load (axial_load_n) — this pack will not read "
                  "the absence of a key as the absence of a load"),
            axial_names, axial_sense,
        )
        if sec["I_min"] is None:
            raise B.MissingParam(
                ["i_min_mm4"],
                "the WEAK-axis second moment I_min, which an explicit I_mm4 section "
                "does not imply (a rect/circle/tube section gives this pack I_min for "
                "free). Buckling happens about the weak axis, so substituting the "
                "bending I here would overstate the capacity of a 2:1 section by four "
                "— if the section really is symmetric, say so with i_min_mm4 = I_mm4",
            )
        length = B.get(ctx, ("column_length_mm", "unbraced_length_mm", "buckling_length_mm"))
        if length is None:
            Lc, lc_src = B.span(ctx), "= span_mm, no column_length_mm given"
        else:
            Lc, lc_src = B.positive(float(length), ["column_length_mm"], "column length"), "column_length_mm"
        k_raw = B.get(ctx, ("column_k", "buckling_k", "k_factor", "effective_length_factor"))
        if k_raw is None:
            cond = str(B.get(ctx, ("column_end_condition", "end_condition", "column_ends"), "") or "")
            cond = cond.strip().lower().replace("-", "_").replace(" ", "_")
            if cond not in B.END_CONDITION_K:
                raise B.MissingParam(
                    ["column_k", "column_end_condition"],
                    "the effective-length factor K, or an end condition to look it up from "
                    f"({', '.join(sorted(B.END_CONDITION_K))}) — K is the input people get "
                    "wrong and a default here would be the pack choosing a boundary "
                    "condition on the designer's behalf",
                )
            K, k_src = B.END_CONDITION_K[cond], f"K from end_condition {cond}"
        else:
            K, k_src = float(k_raw), "column_k"
        K = B.positive(K, ["column_k"], "effective-length factor K")
    except B.MissingParam as miss:
        return _skip("beam.buckling", miss)

    A = sec["A"]
    r = math.sqrt(sec["I_min"] / A)
    i_min_note = f", I_min {sec['I_min']:.0f} mm^4 [{sec.get('I_min_source')}]"
    lam = K * Lc / r
    lam_1 = math.sqrt(2.0 * math.pi ** 2 * E / sy)
    if lam >= lam_1:
        p_cr = math.pi ** 2 * E * sec["I_min"] / (K * Lc) ** 2
        regime = "Euler (elastic)"
    else:
        sigma_cr = sy - (sy * sy * lam * lam) / (4.0 * math.pi ** 2 * E)
        p_cr = max(sigma_cr, 0.0) * A
        regime = "Johnson (inelastic)"
    util = (axial * sf) / p_cr if p_cr > 0 else float("inf")
    ev = _evidence(ctx, "buckling", {
        "formula_euler": "P_cr = pi^2*E*I_min/(K*L)^2",
        "formula_johnson": "sigma_cr = sy - sy^2*lambda^2/(4*pi^2*E),  P_cr = sigma_cr*A",
        "regime": regime, "lambda": lam, "lambda_transition": lam_1,
        "K": K, "K_source": k_src, "column_length_mm": Lc, "length_source": lc_src,
        "E_mpa": E, "E_source": e_src, "yield_mpa": sy, "yield_source": y_src,
        "I_min_mm4": sec["I_min"], "I_min_source": sec.get("I_min_source"),
        "area_mm2": A, "radius_gyration_mm": r,
        "p_critical_n": p_cr, "axial_load_n": axial, "safety_factor": sf,
        "utilisation": util,
        "assumptions": [
            "perfectly straight, concentrically loaded, no initial bow",
            "no lateral-torsional buckling, no local/plate buckling of a thin wall",
            "K describes the REAL joint, not the one on the drawing",
        ],
    })
    return Verdict(
        gate="beam.buckling",
        passed=util <= 1.0,
        measured=round(util, 3),
        limit=1.0,
        units="utilisation",
        detail=f"{regime}: lambda {lam:.0f} (transition {lam_1:.0f}), K {K:g} [{k_src}], "
               f"L {Lc:g} mm [{lc_src}]{i_min_note} -> P_cr {p_cr:.0f} N vs "
               f"{axial:g} N x SF {sf:g}{sf_note} — util {util:.2f}",
        evidence=ev,
    )


# --------------------------------------------------------------------------- #
# 6. fastener bearing
# --------------------------------------------------------------------------- #
@gate(
    id="beam.bearing",
    title="Fastener hole bearing stress within the design allowable",
    claims=["structural", "fastener", "joint"],
    tier=Tier.INSTANT,
    settles="bearing stress",
    # Prerequisite beam.input_sanity (P2.2-D12): a load with the wrong sign or a
    #    length in metres makes this arithmetic meaningless, and the sanity guard
    #    fails only on exactly those.
    #    Isolated: the guard passes this gate's own known-bad control
    #    (test_packs.ControlsAreIsolated).
    needs=["beam.input_sanity"],
    # No edge to beam.model_validity: bearing stress is F/(d*t*n) and does not
    # depend on the beam model.
    negative_control=NegativeControl(
        fixture="selftest/bad_beams.py:thin_flange",
        note="the same bolt and load through the bearing thickness that puts hole-wall "
             "stress 15% past the allowable; nothing about the beam itself changes",
    ),
)
def bearing(ctx: GateContext) -> Verdict:
    """Hole-wall bearing stress: P / (n * d * t).

    The projected bearing area is the bolt SHANK diameter times the plate
    thickness — not the hole diameter, which would overstate the area in the
    unconservative direction, and not the washer.

    Bearing is the check that starts to dominate as a part gets stiffer. Making a
    bracket thicker fixes deflection and bending and does nothing whatever for a
    joint whose load is going through a 4 mm bolt into a thin ear, which is why
    this gate lives in a structural pack rather than a fastener one.

    Shear-out and tear-out at the free edge are NOT checked here: those need an
    edge distance this pack does not have. Keep e/d >= 2 and treat that as an
    assumption in the ledger, not as something proven.
    """
    try:
        sec_thickness = B.get(ctx, ("plate_thickness_mm", "bearing_thickness_mm", "lug_thickness_mm"))
        if sec_thickness is None:
            raise B.MissingParam(
                ["plate_thickness_mm"],
                "the thickness of the material the fastener bears against "
                "(plate_thickness_mm) — it is rarely the beam's own section depth",
            )
        t = B.positive(float(sec_thickness), ["plate_thickness_mm"], "bearing thickness")
        d = B.positive(
            B.num(ctx, ("bolt_dia_mm", "fastener_dia_mm", "bolt_diameter_mm"),
                  "the bolt SHANK diameter (bolt_dia_mm) — the hole diameter would "
                  "overstate the bearing area"),
            ["bolt_dia_mm"], "bolt diameter",
        )
        n, n_default = B.opt(ctx, ("n_bolts", "bolt_count", "fastener_count"), 1.0)
        n = B.positive(n, ["n_bolts"], "fastener count")
        br_names, br_sense = B.LOAD_KEYS["bearing_load_n"]
        p_raw = B.get(ctx, br_names)
        if p_raw is None:
            P, p_src = B.load(ctx), "= load_n, the whole transverse load assumed into the joint"
        else:
            P, p_src = B.unsigned_load(float(p_raw), br_names, br_sense), "bearing_load_n"
        sf, sf_note = _sf(ctx)
        allow, allow_src = B.allowable_direct(ctx, sf)
    except B.MissingParam as miss:
        return _skip("beam.bearing", miss)

    area = n * d * t
    sigma = P / area
    util = sigma / allow if allow > 0 else float("inf")
    ev = _evidence(ctx, "bearing", {
        "formula": "sigma_br = P / (n * d * t)",
        "load_n": P, "load_source": p_src, "n_bolts": n, "n_defaulted": n_default,
        "bolt_dia_mm": d, "thickness_mm": t, "bearing_area_mm2": area,
        "bearing_stress_mpa": sigma, "allowable_mpa": allow,
        "allowable_source": allow_src, "safety_factor": sf, "utilisation": util,
        "assumptions": [
            "load shares EQUALLY between fasteners — false for a stiff plate on many bolts",
            "edge distance is adequate: shear-out and tear-out are NOT checked",
            "yield/SF is a conservative bearing allowable; ductile metals with good edge "
            "distance are often allowed 1.5x that, brittle and layered materials less",
        ],
    })
    return Verdict(
        gate="beam.bearing",
        passed=util <= 1.0,
        measured=round(util, 3),
        limit=1.0,
        units="utilisation",
        detail=f"{sigma:.1f} MPa on {area:.1f} mm^2 ({n:g} x dia {d:g} x {t:g} mm"
               f"{' [n_bolts pack default]' if n_default else ''}) from {P:g} N "
               f"[{p_src}] vs {allow:.1f} MPa ({allow_src}{sf_note}) — util {util:.2f}",
        evidence=ev,
    )


# --------------------------------------------------------------------------- #
# 7. the gate that polices the other six
# --------------------------------------------------------------------------- #
@gate(
    id="beam.model_validity",
    title="Euler-Bernoulli beam theory is applicable to this geometry",
    claims=["structural", "stiffness", "strength", "model-validity"],
    tier=Tier.INSTANT,
    settles="beam model validity",
    # Prerequisite beam.input_sanity (P2.2-D12): the guard's own L/h and shear share
    #    are computed from the same lengths and loads; in the wrong units or signs
    #    its verdict is about a different beam.
    #    Isolated: the guard passes this gate's own known-bad control
    #    (test_packs.ControlsAreIsolated).
    needs=["beam.input_sanity"],
    negative_control=NegativeControl(
        fixture="selftest/bad_beams.py:stubby",
        note="the same section on the span that puts L/h 15% below the pack's own "
             "slenderness floor, where the deflection gate quietly under-predicts",
    ),
)
def model_validity(ctx: GateContext) -> Verdict:
    """The slenderness guard. This is the most valuable gate in the pack.

    Every other gate here is Euler-Bernoulli, which assumes plane sections stay
    plane and therefore ignores shear deflection entirely. That is a very good
    assumption for a slender member and an increasingly bad one as the member
    gets stubby — and the failure is silent and in the optimistic direction:
    ``beam.deflection`` keeps returning a number, the number keeps getting
    smaller than reality, and nothing in the report says the model stopped
    applying.

    It applies TWO criteria and reports the worse of them as a utilisation:

    1. **Slenderness.** L/h must be at least 5.

    2. **The size of the omission itself.**

           delta_shear/delta_bending = (k_s/k_d) * alpha * (E/G) * I / (A*L^2)

       must be at most 10%.

    The second criterion exists because the first is not sufficient, and shipping
    only the first was a real defect. For a rectangular METAL cantilever the
    omission is ~0.8% at L/h 10, ~3% at L/h 5, ~20% at L/h 2 and ~55% at L/h 1.2
    (``references/formulae.md`` section 6 carries the table) — so L/h = 5 is the
    GENEROUS end of the range, chosen so the gate refuses only where the omission
    is unambiguous; many texts want L/h >= 10 for slender-beam accuracy. But that
    curve is for one case and one material. The multiplier ``k_s/k_d`` runs from 3
    for a cantilever to 48 for a fixed-fixed beam, and ``E/G`` runs from 2.6 for a
    metal to 12-16 for wood. A birch plywood beam under a distributed load at
    L/h 6 clears the slenderness test and omits about 32% of its deflection. A
    gate that printed that number and passed would be a logger.

    Note what this gate does NOT need: no modulus, no yield, no material at all —
    only the RATIO E/G, which cancels E. Where the material is unknown or not a
    known metal and no ``e_over_g`` is available, the gate assumes isotropy,
    SAYS SO on its verdict line, and its number is then a lower bound. The same
    goes for the shear shape factor ``alpha`` on an explicit section, where 1.2
    (the rectangle's) is assumed and a thin-webbed shape's true value is 3-6x it.
    A validity check must keep working on the half-specified models where it is
    most needed, and the price of that is disclosing every assumption it made to
    do so.
    """
    try:
        L = B.span(ctx)
        sec = B.section(ctx)
        case, (k_d, _k_m, _k_v, k_s, _note), case_default = B.case_of(ctx)
        eg, eg_src, eg_assumed = B.e_over_g(ctx)
        limit, limit_default = B.opt(ctx, ("min_slenderness", "slenderness_limit"),
                                     B.MIN_SLENDERNESS)
        limit = B.positive(limit, ["min_slenderness"], "slenderness limit")
        frac_limit, frac_default = B.opt(
            ctx, ("max_shear_deflection_fraction", "shear_deflection_limit"),
            B.MAX_SHEAR_DEFLECTION_FRACTION)
        frac_limit = B.positive(frac_limit, ["max_shear_deflection_fraction"],
                                "shear-deflection fraction limit")
    except B.MissingParam as miss:
        return _skip("beam.model_validity", miss)

    depth = B.positive(sec["depth"], ["height_mm"], "section depth")
    slender = L / depth
    frac = B.shear_fraction(k_s, k_d, sec["alpha"], eg, sec["I"], sec["A"], L)
    # Both criteria as one utilisation, so the headline number says how far out
    # the model is regardless of which criterion bit. >1 means the other gates'
    # numbers are not trustworthy.
    util_slender = limit / slender if slender > 0 else float("inf")
    util_frac = frac / frac_limit
    util = max(util_slender, util_frac)
    binds = "slenderness" if util_slender >= util_frac else "shear-deflection share"
    # Every assumption that could make `frac` a LOWER bound is named on the line,
    # because an under-reported omission is the one failure this gate exists to
    # prevent and it would otherwise be invisible.
    caveats = []
    if eg_assumed:
        caveats.append(f"E/G {eg:.1f} ASSUMED ISOTROPIC [{eg_src}] — a lower bound for "
                       f"wood (E/G 12-16), a printed or any layered material")
    if sec.get("alpha_assumed"):
        caveats.append("alpha assumed 1.2 (rectangular) on an explicit section — a "
                       "thin-webbed shape's true factor is 3-6x that, so the omission "
                       "below is a lower bound; state alpha_shear or shear_area_mm2")
    ev = _evidence(ctx, "model_validity", {
        "formula": "L/h, and delta_s/delta_b = (k_s/k_d)*alpha*(E/G)*I/(A*L^2)",
        "case": case, "span_mm": L, "depth_mm": depth, "slenderness": slender,
        "slenderness_limit": limit, "slenderness_limit_defaulted": limit_default,
        "shear_deflection_fraction": frac,
        "shear_deflection_fraction_limit": frac_limit,
        "shear_deflection_limit_defaulted": frac_default,
        "alpha": sec["alpha"], "alpha_assumed": bool(sec.get("alpha_assumed")),
        "e_over_g": eg, "e_over_g_source": eg_src, "isotropy_assumed": eg_assumed,
        "utilisation": util, "binding_criterion": binds,
        "section": sec["desc"],
        "guards": ["beam.deflection", "beam.deflection_ratio", "beam.bending_stress",
                   "beam.shear_stress", "beam.buckling"],
        "not_guarded": {
            "beam.bearing": "P/(n*d*t) is not beam theory — a hole in a stubby lug "
                            "bears exactly as the formula says",
            "beam.input_sanity": "checks the projection, not the theory",
        },
        "caveats": caveats,
        "note": "a FAIL here does not mean the part is weak; it means the other gates' "
                "numbers are not trustworthy and the question needs FEA or a "
                "Timoshenko formulation",
    })
    return Verdict(
        gate="beam.model_validity",
        passed=util <= 1.0,
        measured=round(util, 3),
        limit=1.0,
        units="utilisation",
        detail=f"L/h {slender:.1f} vs {limit:g} min"
               f"{' [pack default]' if limit_default else ''}; Euler-Bernoulli omits "
               f"~{frac * 100:.1f}% of the deflection vs {frac_limit * 100:g}% max"
               f"{' [pack default]' if frac_default else ''} ({L:g} mm span, "
               f"{depth:g} mm deep, {case}) — util {util:.2f} ({binds} binds)"
               + ("; " + "; ".join(caveats) if caveats else ""),
        evidence=ev,
    )


# --------------------------------------------------------------------------- #
# 8. the gate that polices the projection itself
# --------------------------------------------------------------------------- #
@gate(
    id="beam.input_sanity",
    title="The projection is in this pack's units and sign convention",
    claims=["structural", "stiffness", "strength", "stability", "fastener",
            "model-validity"],
    tier=Tier.INSTANT,
    settles="input units and signs",
    negative_control=NegativeControl(
        fixture="selftest/bad_beams.py:negated_load",
        note="the same beam under a downward-negative sign convention: every load "
             "key flips sign, every magnitude and every dimension is untouched",
    ),
)
def input_sanity(ctx: GateContext) -> Verdict:
    """The one gate here that FAILS rather than skips, because its inputs are
    wrong rather than missing.

    Two failure modes, both of which produce a full sweep of confident PASSes on
    a member nobody analysed.

    **Signs.** Every geometric dimension in this pack goes through a positivity
    guard; the load family did not. With ``load_n = -300``,
    ``axial_load_n = -400`` and ``bearing_load_n = -300`` — the ordinary
    downward-negative convention — all seven analysis gates returned PASS, with
    `util -0.65`, `tau -3.75 MPa` and a deflection ratio of `L/inf` out of the
    "infinitely stiff is a pass" branch. Those gates now SKIP on a signed load,
    which resolves their claims BLOCKED; this one FAILS, which puts a red line in
    the report rather than six quiet blanks.

    **Units.** PACK.md names a metre/pascal slip as the number one risk in the
    domain and the pack shipped no check for it: the whole baseline restated in
    SI passed every gate, because utilisation, L/delta and L/h are all
    scale-invariant and read completely normal. Only the two quantities that
    CANNOT be scale-free give it away. So this gate bands them:

    * modulus 1 .. 1e6 MPa — catches pascals (1e11 for a metal)
    * stress 0.1 .. 5000 MPa — catches pascals
    * E/yield 30 .. 5000 — catches GIGApascals beside megapascal stresses,
      which no absolute band can see (68.9 beside 276 gives 0.25)
    * span 1 .. 1e5 mm — catches metres (0.06)

    The bands are unit detectors, not design limits, and every one of them is set
    wide enough that no real material or member is refused. Section dimensions
    and loads are deliberately NOT banded: a 0.4 mm wall and a 0.3 N load are
    both ordinary, and a load in kN looks exactly like a small load in N. This
    gate cannot catch that one and says so rather than implying coverage.
    """
    faults: list[str] = []
    checked: list[str] = []

    def band(names, label, lo, hi, unit):
        raw = B.get(ctx, names)
        if raw is None:
            return None
        try:
            value = float(raw)
        except (TypeError, ValueError):
            faults.append(f"{names[0]} is not a number ({raw!r})")
            return None
        checked.append(label)
        if not math.isfinite(value) or not (lo <= value <= hi):
            faults.append(f"{label} {value:g} {unit} is outside {lo:g}..{hi:g} {unit} "
                          f"— check the unit, not the design")
        return value

    for _key, (names, sense) in B.LOAD_KEYS.items():
        raw = B.get(ctx, names)
        if raw is None:
            continue
        try:
            value = float(raw)
        except (TypeError, ValueError):
            faults.append(f"{names[0]} is not a number ({raw!r})")
            continue
        checked.append(names[0])
        if not math.isfinite(value):
            faults.append(f"{names[0]} is {value!r}")
        elif value < 0.0:
            faults.append(f"{names[0]} {value:g} N is NEGATIVE — this pack wants "
                          f"{sense}; a minus sign here may be a downward-negative "
                          f"convention or may mean tension, and the pack will not guess")

    e_lo, e_hi = B.MODULUS_BAND_MPA
    s_lo, s_hi = B.STRESS_BAND_MPA
    E = band(("modulus_mpa", "youngs_modulus_mpa", "e_mpa", "E_mpa"),
             "modulus", e_lo, e_hi, "MPa")
    sy = band(("yield_mpa", "yield_strength_mpa", "sigma_y_mpa"),
              "yield", s_lo, s_hi, "MPa")
    band(("allowable_stress_mpa", "design_stress_mpa"), "allowable stress",
         s_lo, s_hi, "MPa")
    band(("allowable_shear_mpa",), "allowable shear", s_lo, s_hi, "MPa")
    band(("shear_strength_mpa", "shear_yield_mpa"), "shear strength", s_lo, s_hi, "MPa")
    sp_lo, sp_hi = B.SPAN_BAND_MM
    band(("span_mm", "length_mm", "beam_length_mm", "free_span_mm", "arm_length_mm"),
         "span", sp_lo, sp_hi, "mm")
    band(("column_length_mm", "unbraced_length_mm", "buckling_length_mm"),
         "column length", sp_lo, sp_hi, "mm")

    # The ratio check needs BOTH numbers, from wherever they came - including the
    # material table, which is always self-consistent, so this only ever bites on
    # numbers the model supplied.
    r_lo, r_hi = B.MODULUS_OVER_YIELD_BAND
    if E is None or sy is None:
        try:
            E_r, _ = B.modulus(ctx)
            sy_r, _ = B.yield_stress(ctx)
        except B.MissingParam:
            E_r = sy_r = None
    else:
        E_r, sy_r = E, sy
    if E_r is not None and sy_r is not None and sy_r > 0 and math.isfinite(E_r):
        checked.append("E/yield")
        ratio = E_r / sy_r
        if not (r_lo <= ratio <= r_hi):
            faults.append(
                f"E/yield {ratio:.2f} is outside {r_lo:g}..{r_hi:g} — real structural "
                f"solids run 100-700 (6061-T6 250, PLA 132, pine 643). A modulus in "
                f"GPa beside a stress in MPa lands near 0.25")

    if not checked:
        return _skip("beam.input_sanity", B.MissingParam(
            ["span_mm", "load_n", "modulus_mpa", "material"],
            "any of the quantities whose units or sign it can check (span, a load, "
            "a modulus, a stress) — nothing in this projection is checkable, so "
            "nothing was checked"))

    ev = _evidence(ctx, "input_sanity", {
        "checked": checked, "faults": faults,
        "bands": {"modulus_mpa": list(B.MODULUS_BAND_MPA),
                  "stress_mpa": list(B.STRESS_BAND_MPA),
                  "e_over_yield": list(B.MODULUS_OVER_YIELD_BAND),
                  "span_mm": list(B.SPAN_BAND_MM)},
        "not_checked": [
            "load MAGNITUDE — newtons and kilonewtons are indistinguishable here, "
            "and the load is the input PACK.md already names as the commonest way "
            "an analysis passes and a part breaks",
            "section dimensions — a 0.4 mm wall and a 0.5 mm deflection limit are "
            "ordinary, so a band would refuse real designs",
            "whether the numbers describe the part you think they describe",
        ],
    })
    return Verdict(
        gate="beam.input_sanity",
        passed=not faults,
        measured=len(faults),
        limit=0,
        units="faults",
        detail=(f"{len(checked)} quantities checked, clean ({', '.join(checked)})"
                if not faults else
                f"{len(faults)} fault(s): " + "; ".join(faults)),
        evidence=ev,
    )
