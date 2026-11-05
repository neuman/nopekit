# SPDX-License-Identifier: Apache-2.0
"""The input guard stays available without the optional solver dependencies.

What slipped through: sharing the mesh helper made doctor name NumPy and
trimesh as undeclared imports of a guard that needs neither.
"""
import os
from nopekit.gates import gate
from nopekit.models import NegativeControl, Tier, Verdict
from nopekit.modelio import load_path

P = load_path(os.path.join(os.path.dirname(__file__), "_case.py"))


@gate(id="simp.case_valid", title="The static part case is well specified", claims=["simp-case"],
      tier=Tier.INSTANT, settles="static model inputs", basis="model-only",
      negative_control=NegativeControl(fixture="selftest/cases.py:invalid_material",
                                       note="negative Young's modulus: same part and load"))
def case_valid(ctx):
    try:
        P.case(ctx)
    except (ValueError, TypeError, KeyError) as exc:
        return Verdict(gate="simp.case_valid", passed=False, detail=str(exc))
    return Verdict(gate="simp.case_valid", passed=True, detail="finite isotropic material, patch loads, support and stress region")


