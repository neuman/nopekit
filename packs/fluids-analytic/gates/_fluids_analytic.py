# SPDX-License-Identifier: Apache-2.0
"""Shared lookups and closed-form fluid arithmetic for the fluids-analytic pack.

Leading underscore: this is a helper, not a gate module, so the pack loader
skips it (see `packs._gate_files`). The gate modules import it as
`from gates._fluids_analytic import ...` — the pack directory is on `sys.path`
while the pack loads, and the name is pack-unique so two packs sharing a
`gates/` namespace cannot collide on it.

Three rules govern everything here.

**Look up generously, fail loudly.** A project may describe a hull as a mass and
a waterplane area, or as a mesh-derived volume; a duct as a flow rate or as a
velocity. Every quantity therefore has a list of accepted key spellings. What is
NOT allowed is a default: if none of the spellings is present, the caller SKIPS
and names the keys it wanted. A gate that invents sea-water density because the
model did not state it is reporting on a fluid nobody chose.

**Separate a model quantity from a policy limit.** Density, viscosity, geometry
and mass come from the model or the gate skips. Acceptance margins (minimum
freeboard, minimum GM) are the pack's opinion, carry a stated default, and are
overridable by a model key. The two are never mixed up, because one is physics
and the other is a judgement call somebody must be able to argue with.

**Say which approximation ran.** Several quantities have a fallback path — draft
from waterplane area assumes wall-sided geometry at the waterline, KB from draft
assumes a box section, waterplane inertia from area and beam assumes a rectangle
and is OPTIMISTIC for any finer shape. Every fallback appends a tag that lands in
the verdict's one line, so a reader can see which model produced the number
without opening anything.
"""
from __future__ import annotations

import json
import math
import os
from typing import Any, Iterable, Sequence

from nopekit.gates import GateContext
from nopekit.models import Verdict


#: Standard gravity. Not a design choice; the value is the SI definition.
G = 9.80665

# --------------------------------------------------------------------------- #
# small-angle stability: the ceiling, the load case, and the fallback margin
#
# These live in the shared module rather than in `gates/hydrostatics.py` because
# `views/curves.py` needs the same three numbers: a GZ chart must stop at exactly
# the angle the gate stops answering at, and draw exactly the line the gate
# compares against. A chart drawn to 40 deg from GZ = GM.sin(theta) would commit,
# in a picture, the precise over-prediction the gate refuses to commit in a
# number — and a picture is the more persuasive of the two.
# --------------------------------------------------------------------------- #
#: There is deliberately NO default heel angle.
#:
#: The default used to be 10.0 — which is also SMALL_ANGLE_MAX_DEG below, and the
#: guard is `heel > SMALL_ANGLE_MAX_DEG`. So a project that said nothing was
#: silently evaluated at the exact ceiling where GZ = GM.sin(theta) is least
#: valid AND where it returns the largest arm the gate will ever produce: on this
#: pack's own worked hull, 0.2707 m at the default against 0.2170 m at the 8 deg
#: the example actually uses — 25% more righting arm, out of a number nobody
#: stated. Defaulting to 5 or 8 deg was considered and rejected too: it is still
#: the gate inventing the load case, and this pack's rule is that a model
#: quantity is stated or the gate SKIPS and names the key. Only acceptance
#: MARGINS carry defaults, because those are the pack's opinion and this is not.
HEEL_KEYS = ("heel_angle_deg", "design_heel_deg", "heel_deg")

#: Beyond this heel, GZ = GM.sin(theta) is void — the waterplane has moved, the
#: deck edge may be immersed, and the small-angle line over-predicts the real
#: righting arm. This is the ceiling on the whole small-angle stability story,
#: and the righting-arm gate SKIPS rather than answering past it.
SMALL_ANGLE_MAX_DEG = 10.0

#: Minimum righting arm as a fraction of waterline beam, when neither an
#: absolute minimum nor a heeling moment is stated.
MIN_GZ_BEAM_FRACTION = 0.01

_MISSING = object()

#: This pack's own baseline projection, on disk next to its fixtures.
BASELINE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "selftest", "baseline.json")


def baseline() -> dict[str, Any]:
    """`selftest/baseline.json` as a projection, with the `_` documentation keys dropped.

    One loader, used by the gates' own negative-control fixtures, so the pack has
    exactly ONE design in it. The fixtures used to carry hand-maintained BASE
    dicts of their own — a 180 kg float and a 25 mm line, neither of which was
    the raft the baseline and every reference file teach — so "every control
    fires against the baseline" was true only nominally: the verdicts CI printed
    were computed on a hull the reader had never seen. Two hand-maintained
    designs is one too many (method rule 2), and the one that ships is the one
    the reader was taught on.

    It is read from THIS FILE's own directory, never from the host project, which
    is what keeps the fixtures sealed: a control's severity must not depend on
    the repository it was installed into.
    """
    with open(BASELINE_PATH, "r", encoding="utf-8") as fh:
        loaded = json.load(fh)
    return {k: v for k, v in loaded.items() if not k.startswith("_")}


# --------------------------------------------------------------------------- #
# accepted key spellings  (the pack's half of the model contract)
# --------------------------------------------------------------------------- #
RHO_KEYS = (
    "fluid_density_kg_m3", "water_density_kg_m3", "rho_fluid_kg_m3",
    "density_kg_m3", "fluid_density", "rho",
)
NU_KEYS = (
    "kinematic_viscosity_m2_s", "nu_m2_s", "fluid_kinematic_viscosity_m2_s", "nu",
)
MU_KEYS = (
    "dynamic_viscosity_pa_s", "mu_pa_s", "fluid_dynamic_viscosity_pa_s", "mu",
)

MASS_KEYS = (
    "mass_total_kg", "total_mass_kg", "displacement_kg", "loaded_mass_kg",
    "all_up_mass_kg", "gross_mass_kg", "mass_kg",
)
HULL_VOLUME_KEYS = (
    "hull_volume_m3", "moulded_volume_m3", "buoyant_volume_m3",
    "displaceable_volume_m3", "watertight_volume_m3",
)
DISPLACED_VOLUME_KEYS = (
    "displaced_volume_m3", "submerged_volume_m3", "volume_displaced_m3",
)
AWP_KEYS = ("waterplane_area_m2", "a_wp_m2", "waterplane_m2", "waterplane_area")
DEPTH_KEYS = (
    "hull_depth_m", "depth_to_deck_m", "moulded_depth_m", "gunwale_height_m",
    "deck_edge_height_m",
)
DRAFT_KEYS = ("draft_m", "draught_m", "immersion_m")
BEAM_KEYS = ("waterline_beam_m", "beam_wl_m", "b_wl_m", "beam_m", "hull_beam_m")
IWP_KEYS = (
    "waterplane_inertia_m4", "i_waterplane_m4", "i_t_m4",
    "transverse_inertia_m4", "second_moment_waterplane_m4",
)
KG_KEYS = (
    "kg_m", "vcg_m", "cg_height_m", "centre_of_gravity_height_m",
    "center_of_gravity_height_m", "vertical_cg_m",
)
KB_KEYS = (
    "kb_m", "vcb_m", "centre_of_buoyancy_height_m",
    "center_of_buoyancy_height_m",
)
BG_KEYS = ("bg_m", "cg_above_cb_m")


# --------------------------------------------------------------------------- #
# defensive parameter access
# --------------------------------------------------------------------------- #
def _numeric(value: Any) -> float | None:
    """A float, or None for anything that is not honestly a number.

    `bool` is excluded on purpose: `True` is an int in Python, and a model that
    set `draft_m: True` by accident must not be read as one metre.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        f = float(value)
        return f if math.isfinite(f) else None
    if isinstance(value, str):
        try:
            f = float(value.strip())
        except (TypeError, ValueError):
            return None
        return f if math.isfinite(f) else None
    return None


def number(ctx: GateContext, keys: Sequence[str], default: Any = _MISSING) -> Any:
    """First numeric value among `keys`, else `default` (else `_MISSING`).

    `GateContext.param` already tolerates the dotted spelling (`config.beam_m`),
    so this only has to walk the synonym list.
    """
    for key in keys:
        found = _numeric(ctx.param(key, None))
        if found is not None:
            return found
    return default


def text(ctx: GateContext, keys: Sequence[str], default: str = "") -> str:
    for key in keys:
        value = ctx.param(key, None)
        if isinstance(value, str) and value.strip():
            return value.strip().lower().replace("-", "_").replace(" ", "_")
    return default


def missing(*groups: Iterable[str]) -> str:
    """Render 'one of a, b, c; and one of d, e' for a skip reason."""
    parts = []
    for group in groups:
        names = [g for g in group if g]
        if not names:
            continue
        parts.append(names[0] if len(names) == 1 else f"one of {', '.join(names)}")
    return "; and ".join(parts)


def skip(gate_id: str, reason: str) -> Verdict:
    """A SKIPPED verdict. The claim goes BLOCKED and stays visible.

    Never a pass, never a silent zero. The reason names the key the model must
    add, because "the gate skipped" without that is a dead end for whoever reads
    the readiness report at 11pm.
    """
    return Verdict(gate=gate_id, passed=False, skipped=True, skip_reason=reason)


def fluid(ctx: GateContext, gate_id: str) -> tuple[float, float, str] | Verdict:
    """(density, kinematic viscosity, how nu was obtained) or a SKIP verdict.

    Viscosity is accepted as kinematic directly or as dynamic over the density,
    because half of the tables in circulation quote one and half the other, and
    a factor of a thousand between them is the most common unit slip in this
    domain.
    """
    rho = number(ctx, RHO_KEYS, None)
    if rho is None or rho <= 0:
        return skip(gate_id, f"model provides no fluid density: {missing(RHO_KEYS)} "
                             f"(kg/m3; e.g. 998 fresh water at 20C, 1025 sea water)")
    nu = number(ctx, NU_KEYS, None)
    if nu is not None and nu > 0:
        return rho, nu, "nu"
    mu = number(ctx, MU_KEYS, None)
    if mu is not None and mu > 0:
        return rho, mu / rho, "mu/rho"
    return skip(gate_id, f"model provides no viscosity: {missing(NU_KEYS)} "
                         f"or {missing(MU_KEYS)} (1.0e-6 m2/s for water at 20C)")


# --------------------------------------------------------------------------- #
# hydrostatics
# --------------------------------------------------------------------------- #
class Hydro:
    """The hydrostatic quantities four gates share, resolved once.

    Resolved together rather than per gate because they are not independent:
    displaced volume comes from mass and density, draft comes from displaced
    volume and waterplane area, KB comes from draft. Deriving them separately in
    four gates is four chances for the four gates to disagree (method rule 2).
    """

    def __init__(self) -> None:
        self.rho = 0.0
        self.mass = 0.0
        self.v_req = 0.0          # volume the mass must displace to float
        self.v_hull = None        # total displaceable (watertight) volume
        self.a_wp = None          # waterplane area at the loaded waterline
        self.depth = None         # keel/bottom to deck edge, same datum as KG
        self.draft = None
        self.beam = None
        self.notes: list[str] = []

    @property
    def tag(self) -> str:
        return ("[" + ", ".join(self.notes) + "]") if self.notes else ""


def hydro(ctx: GateContext, gate_id: str) -> Hydro | Verdict:
    """Resolve the shared hydrostatic set, or SKIP naming what is absent."""
    rho = number(ctx, RHO_KEYS, None)
    if rho is None or rho <= 0:
        return skip(gate_id, f"model provides no fluid density: {missing(RHO_KEYS)} "
                             f"(kg/m3; 998 fresh water at 20C, 1025 sea water)")
    mass = number(ctx, MASS_KEYS, None)
    if mass is None or mass <= 0:
        return skip(gate_id, f"model provides no loaded mass: {missing(MASS_KEYS)} (kg, "
                             f"everything that goes in the water: structure, payload, ballast, crew)")

    h = Hydro()
    h.rho = rho
    h.mass = mass
    h.v_req = mass / rho
    h.a_wp = number(ctx, AWP_KEYS, None)
    h.depth = number(ctx, DEPTH_KEYS, None)
    h.beam = number(ctx, BEAM_KEYS, None)

    h.v_hull = number(ctx, HULL_VOLUME_KEYS, None)
    if h.v_hull is None and h.a_wp and h.depth:
        h.v_hull = h.a_wp * h.depth
        h.notes.append("V=Awp*depth, prismatic")

    draft = number(ctx, DRAFT_KEYS, None)
    if draft is not None and draft > 0:
        h.draft = draft
    elif h.a_wp:
        h.draft = h.v_req / h.a_wp
        h.notes.append("T=V/Awp, wall-sided")
    return h


def gm(ctx: GateContext, h: Hydro, gate_id: str) -> tuple[float, float, float, list[str]] | Verdict:
    """(GM, BM, BG, notes) in metres, or a SKIP verdict naming what is missing.

    GM = BM - BG with BM = I_waterplane / displaced volume. Every vertical
    distance is measured from the same datum (the keel or the hull's lowest
    point); mixing datums here is the error that produces a confidently positive
    GM for a boat that is about to roll over.
    """
    notes: list[str] = []
    volume = number(ctx, DISPLACED_VOLUME_KEYS, None) or h.v_req

    i_wp = number(ctx, IWP_KEYS, None)
    if i_wp is None:
        if h.a_wp and h.beam:
            i_wp = h.a_wp * h.beam ** 2 / 12.0
            notes.append("I=Awp*B^2/12, rectangular waterplane, OPTIMISTIC for a finer shape")
        else:
            return skip(gate_id, f"model provides no transverse waterplane inertia: "
                                 f"{missing(IWP_KEYS)}, or {missing(AWP_KEYS)} with "
                                 f"{missing(BEAM_KEYS)} for the rectangular approximation")
    if volume <= 0:
        return skip(gate_id, "displaced volume resolves to zero — check mass and density")
    bm = i_wp / volume

    bg = number(ctx, BG_KEYS, None)
    if bg is None:
        kg_ = number(ctx, KG_KEYS, None)
        if kg_ is None:
            return skip(gate_id, f"model provides no vertical centre of gravity: "
                                 f"{missing(KG_KEYS)} (m above the same datum as KB), "
                                 f"or {missing(BG_KEYS)} directly")
        kb = number(ctx, KB_KEYS, None)
        if kb is None:
            if h.draft is None:
                return skip(gate_id, f"model provides no centre of buoyancy: {missing(KB_KEYS)}, "
                                     f"and no draft to approximate it from ({missing(DRAFT_KEYS)} "
                                     f"or {missing(AWP_KEYS)})")
            kb = h.draft / 2.0
            notes.append("KB=T/2, box section")
        bg = kg_ - kb
    return bm - bg, bm, bg, notes


# --------------------------------------------------------------------------- #
# pipe friction
# --------------------------------------------------------------------------- #
#: Below this Reynolds number the flow in a round pipe is laminar and f = 64/Re
#: is exact, not a correlation.
RE_LAMINAR_MAX = 2300.0

#: Above this it is reliably turbulent and Colebrook-White applies. BETWEEN the
#: two, neither holds: the flow trips intermittently and the real friction
#: factor can sit anywhere between the two curves. That band is what
#: `fluid.flow_regime` refuses to certify.
RE_TURBULENT_MIN = 4000.0


#: Relative roughness e/D above which Colebrook-White, the Moody chart and
#: Swamee-Jain all stop being statements about anything measured. The Moody
#: chart's coarsest plotted line is e/D = 0.05 and Colebrook's data never went
#: beyond it; past that the "roughness" is a significant fraction of the bore and
#: the flow is an obstructed passage, not a rough pipe. The equation still
#: returns a float — that is the whole problem.
#:
#: Why 0.05 and not something looser: it is the published chart bound, so it is
#: defensible to a reader with a Moody chart in front of them. 0.01 (Swamee-Jain's
#: own stated fitted ceiling, see below) was considered and rejected as the gate
#: limit — it would refuse galvanised iron in a 15 mm bore, which is ordinary and
#: which Colebrook does describe; the seed being slightly off there costs nothing
#: because the gate iterates Colebrook to 1e-10 anyway.
#: This pack's own references/pipe_roughness.md lists materials (riveted steel at
#: 9e-3 m, concrete at 3e-3 m) that cross 0.05 in any bore under 180 mm and
#: 60 mm respectively, so this is reachable from the pack's own data.
REL_ROUGHNESS_MAX = 0.05

#: The turbulent Darcy friction factor cannot physically leave this band inside
#: the correlation's range, so a value outside it means an input is wrong — a
#: roughness in millimetres, a bore in millimetres, a Reynolds number that is not
#: a Reynolds number. The bounds are read off Colebrook itself rather than
#: guessed:
#:   upper — fully-rough Colebrook at the e/D ceiling above,
#:           f = [-2.log10(0.05/3.7)]^-2 = 0.0716, rounded up to 0.08.
#:   lower — smooth-pipe Colebrook at Re 1e8, f = 0.0082, rounded down to 0.008.
#: PACK.md quotes 0.01-0.08 as the range an engineer should expect; these are the
#: slightly wider bounds the gate refuses outside, so the gate never argues with a
#: legitimately smooth, very fast line.
F_TURBULENT_MIN = 0.008
F_TURBULENT_MAX = 0.08


def swamee_jain(re: float, rel_roughness: float) -> float:
    """Explicit friction-factor approximation, used as the Colebrook seed.

    Within about 1% of Colebrook over 5e3 < Re < 1e8 and 1e-6 < e/D < 1e-2, which
    is why it is a legitimate answer on its own and an excellent starting point
    when an exact one is wanted.

    It is called here unconditionally, including outside that fitted range, and
    that is safe ONLY because it is a seed: `colebrook` iterates to 1e-10 from
    wherever it starts, so a poor seed costs iterations and not accuracy. What is
    NOT safe is using either function past `REL_ROUGHNESS_MAX`, and policing that
    is `fluid.flow_regime`'s job, not this function's.
    """
    denom = math.log10(rel_roughness / 3.7 + 5.74 / re ** 0.9)
    return 0.25 / (denom * denom)


def colebrook(re: float, rel_roughness: float, *, tol: float = 1e-10,
              max_iter: int = 60) -> tuple[float, int, list[str]]:
    """Darcy friction factor by fixed-point iteration on Colebrook-White.

    1/sqrt(f) = -2 log10( e/(3.7 D) + 2.51 / (Re sqrt(f)) )

    Iterating on x = 1/sqrt(f) rather than on f makes the map a contraction over
    the whole engineering range, so it converges in a handful of steps from the
    Swamee-Jain seed. The trace is returned so the gate can write it to evidence
    — method rule 7: an iterative solve that does not record its convergence is
    a number with no provenance, even when the loop is ten lines long.
    """
    trace = []
    x = 1.0 / math.sqrt(swamee_jain(re, rel_roughness))
    trace.append(f"seed  x=1/sqrt(f)={x:.8f}  f={1.0 / (x * x):.8f}  (Swamee-Jain)")
    for i in range(1, max_iter + 1):
        nxt = -2.0 * math.log10(rel_roughness / 3.7 + 2.51 * x / re)
        delta = abs(nxt - x)
        x = nxt
        trace.append(f"iter {i:<3d} x={x:.8f}  f={1.0 / (x * x):.8f}  |dx|={delta:.3e}")
        if delta < tol:
            return 1.0 / (x * x), i, trace
    trace.append(f"NOT CONVERGED after {max_iter} iterations")
    return 1.0 / (x * x), max_iter, trace


def friction_factor(re: float, rel_roughness: float) -> tuple[float, str, list[str]]:
    """(f, which law was used, trace). Laminar below 2300, Colebrook above."""
    if re <= 0:
        return 0.0, "none", ["Re<=0: no flow"]
    if re < RE_LAMINAR_MAX:
        return 64.0 / re, "laminar 64/Re", [f"laminar: f=64/{re:.1f}={64.0 / re:.6f}"]
    f, iters, trace = colebrook(re, rel_roughness)
    return f, f"Colebrook ({iters} it)", trace


def regime_name(re: float) -> str:
    if re < RE_LAMINAR_MAX:
        return "laminar"
    if re < RE_TURBULENT_MIN:
        return "TRANSITIONAL"
    return "turbulent"


def eng(value: float) -> str:
    """Compact scientific rendering for a one-line verdict: 6.0e4, not 60000.0."""
    if value == 0:
        return "0"
    return f"{value:.1e}".replace("e+0", "e").replace("e-0", "e-").replace("e+", "e")
