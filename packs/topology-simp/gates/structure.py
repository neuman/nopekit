# SPDX-License-Identifier: Apache-2.0
"""Three measurements of an exported candidate. None imports the SIMP solver.

Their limits are ceilings. Without the comparator the report could not state
which side of a SIMP limit its measured result was on.
"""
import os
from nopekit.gates import gate
from nopekit.models import NegativeControl, Tier, Verdict
from nopekit.modelio import load_path

P = load_path(os.path.join(os.path.dirname(__file__), "_part.py"))


def control(name, note):
    return NegativeControl(fixture=f"selftest/cases.py:{name}", note=note)


@gate(id="simp.volume", title="The exported part fits the material budget", claims=["simp-volume"],
      tier=Tier.BUILD, settles="exported material volume", basis="established",
      requires_python=["numpy", "trimesh"],
      negative_control=control("too_much_material", "same part twice as wide, beyond the volume budget"))
def volume(ctx):
    # Deliberately independent of static inputs: a bad support cannot hide excess material.
    p = {"mesh": ctx.param("simp_case")["mesh"]}
    v = float(P.mesh(ctx, p).volume)
    limit = ctx.param("simp_volume_limit_mm3")
    if not P.number(limit) or limit <= 0:
        raise ValueError("simp_volume_limit_mm3 must be positive and finite")
    return Verdict(gate="simp.volume", passed=v <= limit, measured=v, limit=limit, units="mm^3", comparator="<=",
                   detail=f"exported connected solid: {v:.3f} mm^3 against {limit:g} mm^3")


def structural(ctx, gate_id, key, limit_key, units):
    p = P.case(ctx)
    limit = ctx.param(limit_key)
    if not P.number(limit) or limit <= 0:
        raise ValueError(f"{limit_key} must be positive and finite")
    result, evidence = P.solve(ctx, p, gate_id)
    value = result[key]
    return Verdict(gate=gate_id, passed=value <= limit, measured=value, limit=limit, units=units, comparator="<=",
                   detail=f"CalculiX on exported part: {value:.6g} {units} against {limit:g}; "
                          "two meshes agree within 5%; equilibrium residual within 0.5% of load",
                   evidence=evidence)


@gate(id="simp.displacement", title="The exported part does not deflect too far", claims=["simp-displacement"],
      tier=Tier.SOLVE, settles="static displacement", basis="established",
      requires_tools=["ccx"], requires_python=["numpy", "trimesh", "gmsh"],
      needs=["simp.case_valid"],  # Invalid material or loads make this solve meaningless.
      negative_control=control("too_soft", "same shape and force, half the elastic modulus"))
def displacement(ctx):
    return structural(ctx, "simp.displacement", "displacement_mm", "simp_displacement_limit_mm", "mm")


@gate(id="simp.stress", title="Stress in the stated region stays below its limit", claims=["simp-stress"],
      tier=Tier.SOLVE, settles="von Mises stress", basis="established",
      requires_tools=["ccx"], requires_python=["numpy", "trimesh", "gmsh"],
      needs=["simp.case_valid"],  # Same material/load validity as displacement, not its pass/fail.
      negative_control=control("overloaded", "same part carrying ten times the force"))
def stress(ctx):
    return structural(ctx, "simp.stress", "stress_mpa", "simp_stress_limit_mpa", "MPa")
