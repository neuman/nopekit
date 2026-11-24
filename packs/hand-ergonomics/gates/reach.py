# SPDX-License-Identifier: Apache-2.0
"""hand-ergonomics — ``ergo.digit_reach``: are the controls within reach of the hands?

Tier 0, standard library only, a few milliseconds. It reads the layout a model
declares (grips and controls in the device frame, mm), puts each ANSUR 1988
subject's wrist behind each grip, and counts the subjects whose thumb or index
finger reaches every control pressed from a grip. The result is the worst sex's
share, in %, against the claim tagged ``digit-reach``.

**Which object it judges.** The device as the user holds it, in the device
frame: x to the right as the user sees the face, y toward the top edge, z out of
the face toward the user, mm. A grip is where the middle finger wraps the handle
(``centre``), the direction from wrist to hand with the wrist straight
(``hand_axis``) and the handle's long axis (``handle_axis``). A control is the
centre of its pressed top surface (``at``) and the digits that press it.

**What it is not.** Comfort, force, fatigue, width or a slab grip. A pass says no
subject is shown out of reach at full extension; a person holding the print
settles the rest (``skills/reach-testing/SKILL.md``).

What slipped through before the pack (its origin, told in
``references/what-slipped-through.md``): a printed handheld that passed every
geometry check was rejected in the first hand that held it, because the controls
had been placed for one builder's thumb. A print tests one hand; this gate
counts the population tail the testers do not reach.
"""
from __future__ import annotations

import json
import os

from nopekit.gates import gate, GateContext
from nopekit.modelio import load_path as _load_path
from nopekit.models import NegativeControl, Tier, Verdict

_HERE = os.path.dirname(os.path.abspath(__file__))
A = _load_path(os.path.join(_HERE, "_anthro.py"))

GID = "ergo.digit_reach"

#: The claim tag the gate reads its limit from (``ctx.acceptance``). A limit
#: lives in the claim, never here: a toy for children and a tool for adults do
#: not share a share.
REACH_TAG = "digit-reach"

#: Where the survey and the anchor apply. Handle diameter: Kong and Lowe (2005)
#: measured power grips on cylinders of 25 to 50 mm, and the girth term is this
#: pack's extrapolation from the survey's 31.75 mm dowel, so it is held to their
#: range. Rejected: an open top (a slab grip such as a phone or a flat remote is
#: not a power grip, and the anchor would place its wrist by a rule nobody
#: measured). Age: the survey's subjects are 17 to 51 (TR89-044 Table 1, p. 4);
#: a child's hand is not in it. Both keys are read under their primary spelling
#: only, with no alias, because the spine judges the context on the spelling the
#: run read. Outside, a pass does not count (Gap) and a fail still does.
CONTEXT = {"hand_grip_diameter_mm": (25.0, 50.0), "hand_user_age_min_years": (17.0, None)}

#: The comparators a share can be held to: at least, or more than. Rejected:
#: ``<=`` (a claim that at most 95% reach is not a reach claim), ``between``.
COMPARATORS = (">=", ">")


def _skip(reason: str) -> Verdict:
    return Verdict(gate=GID, passed=False, skipped=True, skip_reason=reason)


def _error(message: str) -> Verdict:
    return Verdict(gate=GID, passed=False, error=message)


def _evidence(ctx: GateContext, payload: dict) -> list[str]:
    """Write the count per control and per sex, and cite it. The verdict is one
    line; this file is what a reader opens to see which control limits and by
    how much."""
    path = ctx.out_path("hand-ergonomics", "digit_reach.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True)
        fh.write("\n")
    return [path]


@gate(
    id=GID,
    basis="sourced",
    title="Every control pressed from a grip is within reach of the claim's share of hands",
    claims=[REACH_TAG],
    tier=Tier.INSTANT,
    settles="digit reach accommodation",
    description=(
        "The worst sex's share of ANSUR 1988 subjects whose thumb or index finger "
        "reaches every control pressed from its grip, counted subject by subject. "
        "The survey's validity regime is the operating context: handles 25-50 mm "
        "across and users 17 and older; outside it a pass does not count and a fail "
        "still does."),
    operating_context=CONTEXT,
    negative_control=NegativeControl(
        fixture="selftest/bad_layouts.py:face_cluster_out_of_reach",
        note="the baseline with the four right-hand face buttons moved 10 mm up the "
             "face along the right hand's axis: 63.08% of women and 84.38% of men "
             "reach every control, against the baseline claim's >= 95%",
    ),
)
def digit_reach(ctx: GateContext) -> Verdict:
    """The worst-sex share of subjects who reach every judged control.

    Skips when the projection carries no layout, or when no control is pressed
    from a grip by a thumb or an index finger (checked before the claim is
    read). Errors on a layout it cannot judge: a grip id no grip declares, a zero
    or non-finite vector, a hand axis more than 20 deg from perpendicular to its
    handle, a judged control behind the centre of its grip along the hand axis (a
    reversed or swapped axis), a regrip control with no reason, a claim not held
    as ``>=`` or ``>`` in %. Each share is floored to 0.01% before it is judged
    (``_anthro.share_pct``)."""
    raw_controls = ctx.param("hand_controls")
    if raw_controls is None:
        return _skip("no hand_controls: the controls and the digits that press them "
                     "[{id, at [x,y,z] mm, operated_by [{grip, digit}]}] in the device frame "
                     "(see the pack's selftest/baseline.json)")
    try:
        controls = A.parse_controls(raw_controls)
    except A.LayoutError as exc:
        return _error(str(exc))
    judged = [c for c in controls if c.mode == "judged"]
    if not judged:
        return _skip("no control is pressed from a grip by a thumb or an index finger: "
                     "the reach rule has nothing to judge (regrip and other-digit "
                     "controls are listed, not judged)")
    raw_grips = ctx.param("hand_grips")
    diameter = ctx.param("hand_grip_diameter_mm")
    age = ctx.param("hand_user_age_min_years")
    if raw_grips is None:
        return _skip("no hand_grips: where each hand holds the device "
                     "[{id, centre, hand_axis, handle_axis}] in the device frame")
    if diameter is None:
        return _skip("no hand_grip_diameter_mm: the largest handle's perimeter / pi "
                     "through its grip centre, mm")
    if not A._real(diameter) or not float(diameter) > 0:
        return _error(f"hand_grip_diameter_mm is {diameter!r}; it is a handle diameter "
                      f"in mm, more than 0")
    try:
        grips = A.parse_grips(raw_grips)
        A.check_grip_ids(controls, grips)
        A.check_distal(controls, grips)
    except A.LayoutError as exc:
        return _error(str(exc))
    acc = ctx.acceptance(REACH_TAG)
    if acc.comparator.value not in COMPARATORS or acc.units != "%":
        return _error(f"the claim tagged {REACH_TAG} holds {acc.quantity} "
                      f"{acc.comparator.value} {acc.limit:g} {acc.units}; this gate "
                      f"measures a share of subjects in %, held as >= or >")
    table = ctx.load_file(A.TABLE_PATH, loader=A.read_subjects)

    d_mm = float(diameter)
    counts, per = {}, {}
    for sex in A.SEXES:
        counts[sex], per[sex] = A.accommodation(table.subjects[sex], controls, grips, d_mm)
    n = {sex: len(table.subjects[sex]) for sex in A.SEXES}
    pct = {sex: A.share_pct(counts[sex], n[sex]) for sex in A.SEXES}
    measured = min(pct.values())
    worst = [sex for sex in A.SEXES if pct[sex] == measured]
    worst_words = " and ".join(A.SEX_WORDS[s] for s in worst)
    others = [s for s in A.SEXES if s not in worst]
    beside = "".join(f"; {A.SEX_WORDS[s]} {pct[s]:.2f}%" for s in others)

    per_pct = {c.id: {sex: A.share_pct(per[sex][c.id], n[sex]) for sex in A.SEXES}
               for c in judged}
    limiting = min(judged, key=lambda c: min(per_pct[c.id].values()))
    limiting_pct = min(per_pct[limiting.id].values())
    regrip = [c for c in controls if c.mode == "regrip"]
    other = [c for c in controls if c.mode == "other"]
    passed = acc.holds(measured)
    age_words = (f"users {age:g}+" if A._real(age) else
                 "users' youngest age not stated" if age is None else f"users {age!r}")
    regrip_words = f" ({', '.join(c.id for c in regrip)})" if regrip else ""
    detail = (f"worst-sex reach {measured:.2f}% ({worst_words}{beside}) vs "
              f"{acc.comparator.value} {acc.limit:g}%: {limiting.id} limits at "
              f"{limiting_pct:.2f}%; {len(judged)} judged, {len(regrip)} regrip"
              f"{regrip_words}, {len(other)} other; {len(grips)} grips, D {d_mm:g} mm, "
              f"{age_words}; ANSUR 1988 {n['F']} F / {n['M']} M")
    evidence = _evidence(ctx, {
        "measured_pct": measured,
        "worst": worst_words,
        "accommodated": {A.SEX_WORDS[s]: {"subjects": n[s], "reach_every_control": counts[s],
                                          "pct": pct[s]} for s in A.SEXES},
        "acceptance": {"comparator": acc.comparator.value, "limit_pct": acc.limit},
        "limiting_control": limiting.id,
        "controls": {
            **{c.id: {"judged": True, "pressed_by": [f"{g} {d}" for g, d in c.pairs],
                      "women_pct": per_pct[c.id]["F"], "men_pct": per_pct[c.id]["M"]}
               for c in judged},
            **{c.id: {"judged": False, "regrip_reason": c.reason} for c in regrip},
            **{c.id: {"judged": False, "pressed_by": "other digit only"} for c in other},
        },
        "handle_diameter_mm": d_mm,
        "girth_offset_mm": round(A.girth_offset(d_mm), 3),
        "table_sha256": table.sha256,
    })
    return Verdict(gate=GID, passed=passed, measured=measured, limit=acc.limit,
                   comparator=acc.comparator.value, units="%", detail=detail,
                   evidence=evidence)
