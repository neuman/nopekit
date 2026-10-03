# SPDX-License-Identifier: Apache-2.0
"""Gates for the reference bracket. Pure arithmetic, no dependencies, tier 0.

Every gate here reads its numbers from `ctx.params` — the projection of the model —
and never recomputes geometry. A gate that recomputes is a second source of truth,
and the two will disagree eventually (rule 2).

**Gate ids here are project-scoped (`bracket.*`), not domain-scoped (`beam.*`).**
A bare domain id collides with any installed pack that ships the same domain, and
two gates cannot share an id — verdicts, claim coverage and `--only` all key on it.
Packs own the domain namespace; a project owns its own.

**A gate lists every vocabulary it bears on; a CLAIM carries the narrowest
vocabulary that describes what it asserts.** `bracket.deflection` below lists
`structural` as well as `stiffness` because a sagging bracket genuinely bears on a
claim about structural adequacy. The mistake to avoid is on the claim side: tag
"root stress stays under half of yield" with `structural` and this deflection gate
covers it, so a deflection failure makes a stress claim read FAIL and the reader
goes hunting in the wrong place. The claims in this project's ledger are tagged
narrowly (`stiffness`, `strength`, `bearing`, `bed-fit`) for exactly that reason.

`bracket.model_validity` is the deliberate exception — see its docstring. It is
also the PREREQUISITE of the two gates whose numbers are Euler-Bernoulli's
(`needs=`): below its slenderness floor they are not run, and a claim bound only
to them reads Skipped naming it, where it used to read their confident pass. The
broad binding is kept beside the edge, so no claim it covers today moves.

Every gate declares a negative control, because the registry will not accept one
without it (rule 5). The fixtures live in `../selftest/bad_configs.py` and each one
changes exactly ONE physically meaningful thing, in the direction that gate cares
about — which is what makes them evidence rather than noise.
"""
from __future__ import annotations

from atompipe.gates import gate, GateContext
from atompipe.models import NegativeControl, Tier, Verdict

# Design limits that are properties of THIS PROJECT, not of the domain. A pack would
# carry the domain rules (minimum wall vs nozzle); a project carries its own targets.
DEFLECTION_LIMIT_MM = 0.5     # the arm may sag this much at rated load, no more.
                              # Set by feel, not by physics: past ~0.5mm on a 60mm
                              # arm the droop is visible against a level shelf edge.
MIN_SLENDERNESS = 5.0         # L/h below which Euler-Bernoulli under-predicts,
                              # because shear deflection stops being negligible.


@gate(
    id="bracket.deflection",
    title="Tip deflection under rated load",
    claims=["structural", "stiffness", "deflection"],
    tier=Tier.INSTANT,
    settles="tip deflection",
    # Prerequisite bracket.model_validity: F*L^3/(3*E*I) is Euler-Bernoulli's, which the guard says
    # stops applying below L/h 5. Isolated: its control, quarter_thickness,
    # quadruples L/h (the guard passes it).
    needs=["bracket.model_validity"],
    negative_control=NegativeControl(
        fixture="selftest/bad_configs.py:quarter_thickness",
        note="same bracket at 1/4 thickness; deflection goes as 1/t^3 so this is ~64x "
             "worse and nothing else about the part changes",
    ),
)
def deflection(ctx: GateContext) -> Verdict:
    """Cantilever tip deflection against the project limit.

    F*L^3 / (3*E*I). The limit is a product decision, not a physics one — see
    DEFLECTION_LIMIT_MM.
    """
    d = float(ctx.params["deflection"])
    return Verdict(
        gate="bracket.deflection",
        passed=d <= DEFLECTION_LIMIT_MM,
        measured=round(d, 4),
        limit=DEFLECTION_LIMIT_MM,
        units="mm",
        detail=f"{d:.3f} mm at {ctx.params['config']['load_n']:.0f} N "
               f"(limit {DEFLECTION_LIMIT_MM} mm)",
    )


@gate(
    id="bracket.bending_stress",
    title="Root bending stress within the design allowable",
    claims=["structural", "strength", "bending-stress"],
    tier=Tier.INSTANT,
    settles="bending stress",
    # Prerequisite bracket.model_validity: the root-stress arithmetic is the same beam theory.
    # Isolated: its control, overloaded, leaves the geometry untouched.
    needs=["bracket.model_validity"],
    negative_control=NegativeControl(
        fixture="selftest/bad_configs.py:overloaded",
        note="same bracket at 20x load; stress is linear in load so this lands far "
             "past yield while the geometry is untouched",
    ),
)
def bending_stress(ctx: GateContext) -> Verdict:
    """Root fibre stress vs yield/safety_factor.

    Reported as utilisation because a ratio is the number a person can act on: 0.25
    means three quarters of the section is spare, 1.1 means it is over.
    """
    u = float(ctx.params["utilisation"])
    s = float(ctx.params["stress_root"])
    allow = float(ctx.params["design_stress"])
    return Verdict(
        gate="bracket.bending_stress",
        passed=u <= 1.0,
        measured=round(u, 3),
        limit=1.0,
        units="utilisation",
        detail=f"{s:.1f} MPa vs {allow:.1f} MPa allowable "
               f"(util {u:.2f}, {ctx.params['material']})",
    )


@gate(
    id="bracket.bearing",
    title="Fastener bearing stress within the design allowable",
    claims=["structural", "fastener", "bearing"],
    tier=Tier.INSTANT,
    settles="bearing stress",
    # No prerequisite: bearing stress is F/(d*t*n), which does not depend on the
    # beam model. C3 (tagged `bearing`) is covered by this gate alone.
    negative_control=NegativeControl(
        fixture="selftest/bad_configs.py:thin_bearing",
        note="one bolt in a thin plate: bearing area collapses while the beam itself "
             "is unchanged, so only this gate should trip",
    ),
)
def bearing(ctx: GateContext) -> Verdict:
    """Hole-wall bearing stress. The claim that starts to dominate as a plate thickens,
    which is why thickening is not a free lunch."""
    b = float(ctx.params["bearing_stress"])
    allow = float(ctx.params["design_stress"])
    return Verdict(
        gate="bracket.bearing",
        passed=b <= allow,
        measured=round(b, 3),
        limit=round(allow, 3),
        units="MPa",
        detail=f"{b:.2f} MPa on {ctx.params['bearing_area']:.0f} mm^2 across "
               f"{ctx.params['config']['n_bolts']} bolt(s), allowable {allow:.1f} MPa",
    )


@gate(
    id="bracket.model_validity",
    title="Beam theory is applicable to this geometry",
    # DELIBERATELY BROAD, and the only gate here that should be. This one does not
    # measure a quantity — it decides whether the other gates' numbers mean anything
    # at all, so it binds to the whole domain and drags every structural claim down
    # with it when the model stops applying. See the note at the top of this file.
    claims=["structural", "stiffness", "strength", "fastener"],
    tier=Tier.INSTANT,
    settles="beam model validity",
    negative_control=NegativeControl(
        fixture="selftest/bad_configs.py:stubby",
        note="a short, deep arm (L/h below 5) where shear deflection stops being "
             "negligible and the other structural gates quietly become optimistic",
    ),
)
def model_validity(ctx: GateContext) -> Verdict:
    """Guard the OTHER gates' assumptions.

    Euler-Bernoulli ignores shear deflection, which is fine while the beam is
    slender and increasingly wrong as it is not. Below L/h ~ 5 the deflection gate
    under-predicts — it would report a pass it has not earned.

    A gate whose job is to police another gate's validity is worth having in any
    pack that ships closed-form analysis. Without it, the cheap gate silently
    becomes the wrong gate as the design moves.
    """
    s = float(ctx.params["slenderness"])
    return Verdict(
        gate="bracket.model_validity",
        passed=s >= MIN_SLENDERNESS,
        measured=round(s, 2),
        limit=MIN_SLENDERNESS,
        units="L/h",
        detail=f"slenderness {s:.1f} (>= {MIN_SLENDERNESS} for Euler-Bernoulli; "
               f"below this the deflection gate under-predicts)",
    )


@gate(
    id="bracket.bed_fit",
    title="Part fits the usable build area",
    claims=["manufacturability", "fdm", "bed-fit", "footprint"],
    tier=Tier.INSTANT,
    settles="bed fit",
    negative_control=NegativeControl(
        fixture="selftest/bad_configs.py:oversized",
        note="the same bracket with a 400 mm arm: nothing wrong but the footprint",
    ),
)
def bed_fit(ctx: GateContext) -> Verdict:
    """Footprint against the bed MINUS brim allowance.

    The brim is the part people forget. A part sized to the raw bed does not fit the
    bed once the brim is on it, and you find out at the end of a long print.
    """
    big = float(ctx.params["bbox_max"])
    usable = float(ctx.params["usable_bed"])
    bbox = ctx.params["bbox"]
    return Verdict(
        gate="bracket.bed_fit",
        passed=big <= usable,
        measured=round(big, 1),
        limit=round(usable, 1),
        units="mm",
        detail=f"{bbox[0]:.0f} x {bbox[1]:.0f} x {bbox[2]:.0f} mm vs {usable:.0f} mm "
               f"usable ({ctx.params['config']['bed_xy']:.0f} bed - 2x"
               f"{ctx.params['config']['brim_mm']:.0f} brim)",
    )


@gate(
    id="bracket.min_wall",
    title="Every wall is thick enough to print reliably",
    claims=["manufacturability", "fdm", "wall-thickness", "min-wall"],
    tier=Tier.INSTANT,
    settles="wall thickness",
    negative_control=NegativeControl(
        fixture="selftest/bad_configs.py:fat_nozzle",
        note="the same part on a 1.2 mm nozzle, where 3 perimeters no longer fit in "
             "the section — the geometry is untouched and the process is wrong",
    ),
)
def min_wall(ctx: GateContext) -> Verdict:
    """Thinnest section vs ~3 perimeters at the nozzle diameter.

    Two perimeters is achievable and unreliable: the bond is intermittent and the
    failure is the kind that shows up in service rather than on the bed.
    """
    t = float(ctx.params["config"]["thickness"])
    m = float(ctx.params["min_wall"])
    return Verdict(
        gate="bracket.min_wall",
        passed=t >= m,
        measured=round(t, 2),
        limit=round(m, 2),
        units="mm",
        detail=f"thinnest section {t:.1f} mm vs {m:.1f} mm minimum "
               f"(3 perimeters at a {ctx.params['config']['nozzle_d']} mm nozzle)",
    )
