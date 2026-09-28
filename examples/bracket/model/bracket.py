# SPDX-License-Identifier: Apache-2.0
"""bracket.py — the atompipe reference model.

A wall-mounted L-bracket carrying a static load on its arm. Deliberately the
simplest thing that still exercises every rule in METHOD.md:

  rule 1  everything downstream is generated from Config + build()
  rule 2  section properties, stresses and deflections are DERIVED, never typed twice
  rule 3  every parameter carries its rationale; what was tried and lost is in PARAMS
  rule 10 build() is pure arithmetic, so the whole gate sweep runs in milliseconds

It has zero dependencies. Standard library only, like the spine — you can run the
entire pipeline on a fresh machine with nothing installed, which is the point of
having a reference project at all.

Frame: millimetres, newtons, megapascals. +x out along the arm from the wall,
+y across the width, +z up. Origin at the wall face on the arm's centreline.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict


# --- material table ---------------------------------------------------------
# Young's modulus and yield are the two numbers every gate here leans on. PETG is
# the default because the reference project prints; the aluminium row exists so the
# example shows a material swap changing which claims pass.
#
# PETG values are the CONSERVATIVE end of the published FDM range, not the datasheet
# coupon number, and that gap is deliberate: a printed part is anisotropic and its
# layer-normal strength is roughly 50-70% of its in-plane strength. A bracket loaded
# in bending across its layers is the bad case. Using the coupon number here is the
# single most common way an FDM part passes analysis and snaps in service.
MATERIALS: dict[str, dict[str, float]] = {
    "petg": {
        "E": 1800.0,        # MPa, conservative FDM (coupon is ~2000-2200)
        "yield": 30.0,      # MPa, conservative FDM tensile (coupon is ~45-50)
        "density": 1.27e-3, # g/mm^3
    },
    "pla": {
        "E": 2800.0,
        "yield": 35.0,
        "density": 1.24e-3,
    },
    "alu6061": {
        "E": 68900.0,
        "yield": 276.0,     # T6 temper
        "density": 2.70e-3,
    },
}


@dataclass
class Config:
    """Every input to the bracket. Nothing outside this dataclass is an input."""

    # --- geometry ---
    arm_length: float = 60.0
    """mm, wall face to the load point. The design's driving dimension: deflection
    goes as L^3, so a 25% longer arm is very nearly a doubling, and no thickness
    inside the print time budget buys a longer arm back. What lost, and by how
    much, is in PARAMS."""

    width: float = 30.0
    """mm, across the bracket. Stiffness is linear in width and cubic in thickness,
    so widening is the expensive way to buy stiffness and thickening is the cheap
    one — but see `thickness`, which has its own ceiling."""

    thickness: float = 7.0
    """mm, and DELIBERATELY still failing the deflection claim in this reference
    project — it comes out at ~0.70mm against a 0.5mm limit. 8.0 passes at 0.47mm.
    The default is left marginal so `atompipe check` on a fresh clone shows a real
    gate catching a real problem, and so the fix is one parameter you can watch turn
    green. Deflection goes as 1/t^3, which is why thickness is the cheap lever;
    4.0 was tried: 3.75 mm, 7.5x the limit — see PARAMS."""

    # --- mounting ---
    hole_d: float = 5.5
    """mm, clearance for an M5 fastener: 5.5 is M5 free-fit. The tighter hole that
    lost is in PARAMS."""

    n_bolts: int = 2
    """Two bolts, not one: a single fastener lets the bracket rotate about it under
    an off-centre load, and nothing in this model would catch that."""

    edge_margin: float = 8.0
    """mm, from the hole EDGE to the nearest free edge (not the hole centre — the
    distinction is worth stating because getting it wrong silently halves your
    tear-out margin). The usual minimum against tear-out is one hole diameter of
    material beyond the hole, and 8.0 against a 5.5mm hole is comfortably past it."""

    # --- loading ---
    load_n: float = 15.0
    """N at the arm tip, static. ~1.5 kg. Declared static: this model has no fatigue
    or impact term, and a claim that says otherwise would be lying."""

    safety_factor: float = 2.0
    """Design stress is yield / safety_factor. 2.0 is the conventional floor for a
    static, non-life-safety load in a ductile material. An FDM part arguably wants
    3.0; the example keeps 2.0 so the default configuration is interesting rather
    than trivially over-built."""

    material: str = "petg"
    """Key into MATERIALS. PETG over PLA because this part lives on a wall and PLA
    creeps under sustained load at room temperature — a PLA bracket that passes
    every gate here will still sag over months, and nothing in a static analysis
    sees that. Swap to alu6061 and the deflection claim passes by a wide margin at
    triple the mass."""

    # --- manufacturing ---
    nozzle_d: float = 0.4
    """mm. Sets the minimum printable wall (see derived `min_wall`)."""

    bed_xy: float = 220.0
    """mm, printer bed. The usable figure is smaller — see `usable_bed` below."""

    brim_mm: float = 8.0
    """mm of brim allowance per side. A part sized to the raw bed does not fit a bed
    once the brim is on it, which is the kind of thing you discover at 11pm."""


CONFIG = Config()


# --- what lost ----------------------------------------------------------------
# Rule 3's other half: the alternatives that were tried and did not win, next to
# the values they lost to. A Config docstring says why THIS value; an entry here
# says what lost and by how much, once, where `atompipe why <param>` finds it.
#
# Only the three fields with a real loser are listed. Every other field's
# rationale is its docstring, and an entry per field would be a parallel table
# of the field list — a second source for it, which drifts the first time a
# field is added (see `modelio.field_docstrings`). No entry states a value: the
# dataclass owns every value, and a number repeated here would be a second copy.
#
# Plain dicts, not `atompipe.models.Param`/`Rejected` records, so the model keeps
# zero dependencies and still runs by hand (`python3 model/bracket.py`); the spine
# reads a dict item exactly as it reads a Param, and refuses a misspelt key.
#
# The numbers are the model's own, at the default config with one field changed,
# against C1's 0.5 mm limit: build(Config(thickness=4.0)) deflects 3.75 mm, and
# build(Config(arm_length=80.0)) 1.66 mm. What slipped through before: the module
# docstring promised "see PARAMS" and there was none — every parameter carried
# `rejected: []` — and the thickness docstring said 4.0 missed by "~5x", which no
# run of this model ever said (S-42).
PARAMS = [
    {"name": "thickness", "units": "mm", "rejected": [
        {"value": "4.0 mm", "why": "3.75 mm deflection, 7.5x the limit"}]},
    {"name": "arm_length", "units": "mm", "rejected": [
        {"value": "80 mm", "why": "1.7 mm deflection, over 3x the limit"}]},
    {"name": "hole_d", "units": "mm", "rejected": [
        {"value": "5.0 mm", "why": "line-to-line fit an FDM hole will not hold"}]},
]


def build(config: Config | None = None) -> dict:
    """Resolve every derived quantity. Pure, deterministic, no I/O.

    Returns a flat-ish dict of derived values. Every number a gate needs comes from
    here — a gate that recomputes geometry is a second source of truth (rule 2), and
    the two will disagree eventually.
    """
    c = config or CONFIG
    if c.material not in MATERIALS:
        raise ValueError(
            f"unknown material {c.material!r}; have {sorted(MATERIALS)}"
        )
    mat = MATERIALS[c.material]

    # --- section properties (rectangular, bending about the width axis) ---
    # I = b*h^3/12 for a rectangle. h is the THICKNESS because the load is vertical
    # and the plate stands on edge relative to that load.
    area = c.width * c.thickness                         # mm^2
    inertia = c.width * c.thickness ** 3 / 12.0          # mm^4
    section_mod = c.width * c.thickness ** 2 / 6.0       # mm^3, = I / (h/2)

    # --- cantilever response at the tip ---
    # Euler-Bernoulli, point load at the free end of a fixed-free beam:
    #   deflection = F L^3 / (3 E I)      moment at root = F L
    # Valid while L/h is large enough that shear deflection is negligible; below
    # about 5 this under-predicts. Reported so a gate can refuse a stubby bracket
    # rather than silently trusting the wrong model.
    moment_root = c.load_n * c.arm_length                # N*mm
    stress_root = moment_root / section_mod if section_mod else float("inf")   # MPa
    deflection = (
        c.load_n * c.arm_length ** 3 / (3.0 * mat["E"] * inertia)
        if inertia else float("inf")
    )                                                    # mm
    slenderness = c.arm_length / c.thickness if c.thickness else float("inf")

    # --- allowables ---
    design_stress = mat["yield"] / c.safety_factor        # MPa
    stress_margin = design_stress - stress_root           # MPa, positive = ok
    utilisation = stress_root / design_stress if design_stress else float("inf")

    # --- fastener bearing ---
    # Load shared across n_bolts, bearing on the projected area of each hole wall.
    # This is the claim that starts to dominate once the plate is thick, which is
    # why thickening is not a free lunch.
    bearing_area = c.hole_d * c.thickness * max(c.n_bolts, 1)   # mm^2
    bearing_stress = c.load_n / bearing_area if bearing_area else float("inf")

    # --- manufacturing ---
    # Minimum reliably printable wall is ~3 perimeters at the nozzle diameter. Two
    # is achievable and unreliable: at ~0.5mm a PETG wall will not bond to itself
    # consistently, and the failure is intermittent rather than obvious.
    min_wall = 3.0 * c.nozzle_d                           # mm
    usable_bed = c.bed_xy - 2.0 * c.brim_mm               # mm
    plate_len = c.arm_length + c.edge_margin + c.hole_d   # mm, rough footprint
    bbox = (plate_len, c.width, c.thickness)
    mass = area * plate_len * mat["density"]              # g, solid approximation

    return {
        "material": c.material,
        "E": mat["E"],
        "yield": mat["yield"],
        "area": area,
        "inertia": inertia,
        "section_mod": section_mod,
        "moment_root": moment_root,
        "stress_root": stress_root,
        "design_stress": design_stress,
        "stress_margin": stress_margin,
        "utilisation": utilisation,
        "deflection": deflection,
        "slenderness": slenderness,
        "bearing_area": bearing_area,
        "bearing_stress": bearing_stress,
        "min_wall": min_wall,
        "usable_bed": usable_bed,
        "plate_len": plate_len,
        "bbox": list(bbox),
        "bbox_max": max(bbox),
        "mass_g": mass,
        "config": asdict(c),
    }


if __name__ == "__main__":       # a model you cannot run by hand is a model nobody checks
    import json
    print(json.dumps(build(), indent=2, sort_keys=True))
