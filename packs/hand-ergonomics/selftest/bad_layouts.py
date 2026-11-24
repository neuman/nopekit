# SPDX-License-Identifier: Apache-2.0
"""Known-bad fixtures for hand-ergonomics. One physical change each.

* ``face_cluster_out_of_reach`` (``ergo.digit_reach``): the right thumb's four face
  buttons moved up the face until the worst sex is under the baseline's limit.
* ``stale_scene`` (``ergo.layout_agrees``): the baseline layout against a scene in
  which btn_y's cap is drawn 3 mm farther up the face than declared.

`nopekit gate selftest` requires every gate to FAIL here. A gate that passes its
own fixture is broken, and a green verdict from it means nothing.

**Sealed.** Each fixture starts from this pack's own ``selftest/baseline.json``
and hands the gate the baseline's own claims, never the host's projection or
ledger. A control whose severity depends on the host's numbers passes in some
repositories and fails in others, which is the same as having none.

**Sealed is not hard-coded.** The named fault (the face cluster moved 10 mm) is
what runs on the shipped baseline. But a fixture that writes only a literal is
guaranteed to cross only the limit the baseline shipped with: edit the claim in
``baseline.json`` looser and the control would quietly stop falsifying anything.
So the fixture reads that limit, computes the gate's own value with the gate's
own arithmetic (``gates/_anthro.py``), and moves the cluster on, 1 mm at a time,
until the worst sex lands at or below ``MARGIN`` times the limit (the discipline
of thermal-analytic's ``bad_thermal.py``).
"""
from __future__ import annotations

import copy
import dataclasses
import json
import math
import os

from nopekit import packs
from nopekit.modelio import load_path
from nopekit.models import Ledger

_PACK_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_BASELINE_PATH = os.path.join(_PACK_DIR, "selftest", "baseline.json")
A = load_path(os.path.join(_PACK_DIR, "gates", "_anthro.py"))

#: How far past the limit the control must land: at or below 0.85 times a
#: ``>=`` limit, the mutation walk's 15%. Large enough that no rounding moves it
#: back inside; small enough that the named 10 mm is what runs on the shipped
#: baseline (it lands at 63.08% against 80.75%).
MARGIN = 0.85

#: The named fault: the four face buttons of the right thumb moved 10 mm up the
#: face, along the right hand's axis projected onto the face. A layout one
#: revision behind a wider body, or a cluster placed for a long thumb.
CLUSTER = ("btn_a", "btn_b", "btn_x", "btn_y")
CLUSTER_GRIP = "right"
NAMED_SHIFT_MM = 10.0

#: Escalation: 1 mm steps, at most 80 mm (beyond any thumb in the survey).
STEP_MM = 1.0
MAX_SHIFT_MM = 80.0


def _read() -> dict:
    with open(_BASELINE_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def _worst_pct(params: dict, table) -> float:
    """The gate's measured value on ``params``: the worst sex's share, in %."""
    controls = A.parse_controls(params["hand_controls"])
    grips = A.parse_grips(params["hand_grips"])
    diameter = float(params["hand_grip_diameter_mm"])
    shares = []
    for sex in A.SEXES:
        subjects = table.subjects[sex]
        count, _per = A.accommodation(subjects, controls, grips, diameter)
        shares.append(A.share_pct(count, len(subjects)))
    return min(shares)


def _moved(params: dict, direction: tuple, shift: float) -> dict:
    out = copy.deepcopy(params)
    for control in out["hand_controls"]:
        if control["id"] in CLUSTER:
            control["at"] = [round(c + shift * d, 6) for c, d in zip(control["at"], direction)]
    return out


def face_cluster_out_of_reach(ctx):
    """ergo.digit_reach — the baseline with btn_a, btn_b, btn_x and btn_y moved
    10 mm up the face (63.08% of women, 84.38% of men), or farther when the
    baseline's limit is looser."""
    baseline = _read()
    params = {k: copy.deepcopy(v) for k, v in baseline.items() if not k.startswith("_")}
    claims = packs.baseline_claims(baseline)
    tagged = [c for c in claims if "digit-reach" in c.tags]
    if len(tagged) != 1 or tagged[0].acceptance.limit is None:
        raise AssertionError("baseline.json must state one claim tagged digit-reach, "
                             "with a limit")
    limit = float(tagged[0].acceptance.limit)
    grip = next(g for g in params["hand_grips"] if g["id"] == CLUSTER_GRIP)
    along = (float(grip["hand_axis"][0]), float(grip["hand_axis"][1]), 0.0)
    norm = math.hypot(along[0], along[1])
    direction = (along[0] / norm, along[1] / norm, 0.0)
    table = A.read_subjects(A.TABLE_PATH)
    shift = NAMED_SHIFT_MM
    while True:
        moved = _moved(params, direction, shift)
        if _worst_pct(moved, table) <= MARGIN * limit:
            break
        shift += STEP_MM
        if shift > MAX_SHIFT_MM:
            raise AssertionError(f"no shift up to {MAX_SHIFT_MM:g} mm takes the worst sex "
                                 f"to {MARGIN} x {limit:g}%: the claim's limit is too low "
                                 f"for a reach control to fail")
    return dataclasses.replace(ctx, params=moved, ledger=Ledger(claims=claims))


#: The known-bad scene, built with the known-good one by ``make_handheld_glb.py``
#: from this baseline. An absolute path into this pack: relative, it would resolve
#: against the host project's root in a check run, where no ``selftest/meshes/``
#: exists, and the control would skip instead of fail.
STALE_SCENE = os.path.join(_PACK_DIR, "selftest", "meshes", "handheld_stale.glb")


def stale_scene(ctx):
    """ergo.layout_agrees — the baseline's declared layout against a scene one
    revision ahead of it: btn_y's cap drawn 3 mm farther up the face, away from the
    right wrist (3.00 mm against the gate's 0.5 mm). Nothing structural is wrong:
    every control is drawn and declared, and both handles are 36 mm and on their
    declared grip centres. On the drawn
    positions the reach gate would count 94.33% of women, under the baseline
    claim's 95%; on the declared ones it counts 97.96%.

    The limits this fails are pack constants in ``gates/layout.py``, not a claim's,
    so the shift is a literal: a tolerance raised past 3 mm would make this
    control pass, and ``gate selftest`` goes red."""
    baseline = _read()
    params = {k: copy.deepcopy(v) for k, v in baseline.items() if not k.startswith("_")}
    params["hand_layout_scene"] = STALE_SCENE
    return dataclasses.replace(ctx, params=params,
                               ledger=Ledger(claims=packs.baseline_claims(baseline)))
