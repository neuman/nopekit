# SPDX-License-Identifier: Apache-2.0
"""The arithmetic of hand-ergonomics: the subject table, the layout, the reach rule.

One copy, three readers: the gate ``ergo.digit_reach`` (``gates/reach.py``), its
known-bad fixture (``selftest/bad_layouts.py``, which computes its own severity
with this arithmetic) and the proposer ``ergo.reach_envelope``. A fixture or a
proposer with a private copy of the rule is a second source of truth that drifts,
and it drifts silently, because nothing downstream reads it.

Standard library only. Every length is in mm, in the device frame: x to the right
as the user sees the face, y toward the top edge, z out of the face toward the
user.

**The reach rule** (``references/reach-model.md`` has it in full). For subject
``i`` and grip ``g`` the wrist point, the stylion with the wrist straight, is

    S_ig = G_g - (WCG_i + (D - DOWEL_MM) / 2) * u_g

where ``G_g`` is the grip centre, ``u_g`` the unit hand axis (wrist to hand),
``WCG_i`` the subject's wrist-centre of grip length and ``D`` the handle diameter.
A control at ``P`` is in reach of that subject's thumb when ``|P - S_ig| <=
WTT_i`` (wrist-thumbtip length), and of the index finger when ``|P - S_ig| <=
WIF_i`` (wrist-index finger length). A subject is accommodated only when every
judged control is in reach of one of the digits that presses it. A judged control
lies at or beyond the centre of each grip it is pressed from, along that grip's
hand axis (:data:`DISTAL_MIN_MM`); one behind it is refused.

**It counts subjects; it never adds percentiles.** What slipped through the
percentile habit, measured on this table: the women's P5 of wrist-thumbtip minus
their P5 of wrist-centre of grip is 48 mm, but the P5 of the difference, subject
by subject, is 42 mm. A limit built from two percentiles is 6 mm generous, in the
direction that hides the fault (Robinette and McConville 1981, SAE 810217).
"""
from __future__ import annotations

import bisect
import collections
import csv
import hashlib
import io
import json
import math
import numbers
import os

#: The dowel on which TR89-044 measured wrist-centre of grip length: 1-1/4 in,
#: 31.75 mm ((125), p. 322). A handle of diameter ``D`` puts the centre of the
#: grip ``(D - DOWEL_MM) / 2`` farther from the wrist than the dowel did: the
#: palm wraps the surface, and the centre is half a diameter inside it. That
#: term is this pack's, not the survey's; Kong and Lowe (2005) measured power
#: grips on 25-50 mm cylinders, which is where the operating context stops.
#: Rejected: no girth term (every handle read as the dowel: a 50 mm handle puts
#: each wrist point 9.1 mm too close, and the baseline reads 99.14% of women
#: where the term gives 83.92%); the full diameter difference (the centre moves
#: by the radius, not the diameter).
DOWEL_MM = 31.75

#: How far the declared hand axis may lean from perpendicular to the handle axis
#: before the layout is refused, in degrees. Wrist-centre of grip length was
#: measured with the dowel square to the fist and the fist in line with the
#: forearm, so the anchor holds only for a grip near that posture. A pack
#: constant with no outside source: no public survey measured the obliquity of a
#: power grip across the palm. Rejected: 0 (no real layout is exactly square, and
#: a refusal reads as errored, louder than a fail); 45 (a hand that far from
#: square is not the posture the survey measured, and the anchor would be a guess
#: presented as a measurement). An open question in PACK.md.
HAND_AXIS_TOLERANCE_DEG = 20.0

#: How far along the hand axis, from the grip centre, a control pressed from that
#: grip must lie, mm: at the centre or beyond it, never behind. Behind the centre
#: is under the palm, where no thumb or index finger of that hand presses, and
#: the reach sphere counts it in reach of nearly everyone. What slipped through
#: before this rule (review of E1): the 20 deg test does not depend on the axis's
#: sign, so a hand axis declared hand to wrist, or swapped with the handle axis,
#: put every wrist point in front of its grip. The known-bad cluster, which fails
#: at 63%, read 100.0% with either slip, and btn_y moved 46 mm up the face read
#: 0% as declared and 100% reversed. On the baseline the nearest judged control
#: (stick_r) lies 4.2 mm beyond its grip centre, and the proposer's fit counts
#: already cut thumb positions at this plane. Rejected: a tolerance behind the
#: centre (a reversed axis would then pass a layout whose controls all sit near
#: the grip, and no survey says how far behind the centre a digit presses); thumb
#: pairs only (a reversed axis on an index-only layout puts the wrists in front
#: of the bumpers, and they read reached by everyone); a check of the axis's sign
#: against the device frame (a grip may be held from any side, so the frame gives
#: no sign: only the controls pressed from it do).
DISTAL_MIN_MM = 0.0

#: The digits a control may name. ``thumb`` and ``index`` are judged; ``other``
#: (a paddle under the middle finger) is counted and not judged, because no
#: survey here measured that reach. Rejected: erroring ``other`` (a sound design
#: with back paddles would read errored).
DIGITS = ("thumb", "index", "other")
JUDGED_DIGITS = ("thumb", "index")

#: The sexes as the table spells them, and the words a verdict uses.
SEXES = ("F", "M")
SEX_WORDS = {"F": "women", "M": "men"}

#: The shipped table's columns, in order (``data/PROVENANCE.json`` maps each
#: measurement to its survey definition and page).
TABLE_HEADER = ("sex", "subject", "hand_length_mm", "hand_breadth_mm", "thumb_breadth_mm",
                "wrist_thumbtip_mm", "wrist_index_mm", "wrist_grip_mm", "wrist_circ_mm")

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
TABLE_PATH = os.path.join(DATA_DIR, "ansur1988_hand.csv")
PROVENANCE_PATH = os.path.join(DATA_DIR, "PROVENANCE.json")

Subject = collections.namedtuple(
    "Subject", "hand_length hand_breadth thumb_breadth wtt wif wcg wrist_circ")
Table = collections.namedtuple("Table", "sha256 subjects")
Grip = collections.namedtuple("Grip", "id centre hand_axis handle_axis")
Control = collections.namedtuple("Control", "id at pairs mode reason named")


class LayoutError(ValueError):
    """The declared layout cannot be judged: a refusal, which the gate reports as
    an error, never as a fail."""


# --------------------------------------------------------------------------- #
# the table
# --------------------------------------------------------------------------- #
def read_subjects(path: str) -> Table:
    """The subject table at ``path``, checked against ``PROVENANCE.json`` beside it.

    A module-level loader for ``ctx.load_file``: it opens the table and its
    provenance, so a sweep records both as the gate's reads. Raises
    ``ValueError`` when the bytes do not hash to the provenance's sha256 (an
    edited table carries the survey's name over other numbers), when the header
    is not :data:`TABLE_HEADER`, or when a value is not a positive integer."""
    with open(path, "rb") as fh:
        raw = fh.read()
    digest = hashlib.sha256(raw).hexdigest()
    with open(os.path.join(os.path.dirname(path), "PROVENANCE.json"), encoding="utf-8") as fh:
        recorded = json.load(fh)["table"]["sha256"]
    if digest != recorded:
        raise ValueError(f"the subject table hashes to {digest}, not the {recorded} its "
                         f"PROVENANCE.json records: rebuild it with "
                         f"gates/data/derive_hand_table.py")
    rows = list(csv.reader(io.StringIO(raw.decode("utf-8"))))
    if not rows or tuple(rows[0]) != TABLE_HEADER:
        raise ValueError(f"the subject table's header is not {','.join(TABLE_HEADER)}")
    subjects: dict[str, list[Subject]] = {sex: [] for sex in SEXES}
    for n, row in enumerate(rows[1:], 2):
        if len(row) != len(TABLE_HEADER) or row[0] not in subjects:
            raise ValueError(f"the subject table's line {n} is not a subject row")
        values = [int(v) for v in row[2:]]
        if min(values) <= 0:
            raise ValueError(f"the subject table's line {n} holds a value that is not positive")
        subjects[row[0]].append(Subject(*values))
    return Table(digest, {sex: tuple(rows_) for sex, rows_ in subjects.items()})


def percentile(values, p: float) -> float:
    """The ``p``-th percentile of ``values`` by linear interpolation between order
    statistics (Hyndman and Fan type 7, numpy's default). Used for design ranges
    only: the gate never compares a percentile with anything."""
    xs = sorted(values)
    if not xs:
        raise ValueError("no values")
    h = (len(xs) - 1) * p / 100.0
    lo = math.floor(h)
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (h - lo) * (xs[hi] - xs[lo])


# --------------------------------------------------------------------------- #
# the layout
# --------------------------------------------------------------------------- #
def _real(value) -> bool:
    return (isinstance(value, numbers.Real) and not isinstance(value, bool)
            and math.isfinite(float(value)))


def point(value, what: str) -> tuple:
    """Three finite numbers, as floats; :class:`LayoutError` otherwise."""
    if (isinstance(value, (str, bytes)) or not hasattr(value, "__len__")
            or len(value) != 3 or not all(_real(c) for c in value)):
        raise LayoutError(f"{what} must be three finite numbers [x, y, z] in mm, "
                          f"not {value!r}")
    return tuple(float(c) for c in value)


def unit(value, what: str) -> tuple:
    """``value`` scaled to length 1; :class:`LayoutError` for a zero or non-finite
    vector."""
    v = point(value, what)
    norm = math.sqrt(sum(c * c for c in v))
    if not norm > 1e-9:
        raise LayoutError(f"{what} is a zero vector: it gives no direction")
    return tuple(c / norm for c in v)


def angle_deg(a: tuple, b: tuple) -> float:
    """The angle between two unit vectors, in degrees."""
    dot = max(-1.0, min(1.0, sum(x * y for x, y in zip(a, b))))
    return math.degrees(math.acos(dot))


def parse_controls(raw) -> list[Control]:
    """The controls as declared, before their grips are known.

    Each is ``{id, at, operated_by: [{grip, digit}], regrip_reason}``. A control
    with an empty ``operated_by`` is a regrip control and must give its reason; one
    pressed only by ``other`` is counted and not judged. Grip ids are checked
    against the grips by :func:`check_grip_ids`."""
    if not isinstance(raw, (list, tuple)):
        raise LayoutError(f"hand_controls must be a list of controls, not "
                          f"{type(raw).__name__}")
    out: list[Control] = []
    seen: set[str] = set()
    for n, entry in enumerate(raw):
        where = f"hand_controls[{n}]"
        if not isinstance(entry, dict):
            raise LayoutError(f"{where} must be an object with id, at and operated_by")
        cid = entry.get("id")
        if not isinstance(cid, str) or not cid.strip():
            raise LayoutError(f"{where} has no id")
        if cid in seen:
            raise LayoutError(f"control {cid!r} is declared twice")
        seen.add(cid)
        at = point(entry.get("at"), f"control {cid!r} at")
        ops = entry.get("operated_by")
        if not isinstance(ops, (list, tuple)):
            raise LayoutError(f"control {cid!r}: operated_by must be a list of "
                              f"{{grip, digit}} (empty for a regrip control)")
        reason = entry.get("regrip_reason")
        pairs: list[tuple[str, str]] = []
        for k, op in enumerate(ops):
            if not isinstance(op, dict):
                raise LayoutError(f"control {cid!r}: operated_by[{k}] must be "
                                  f"{{grip, digit}}")
            grip, digit = op.get("grip"), op.get("digit")
            if not isinstance(grip, str) or not grip:
                raise LayoutError(f"control {cid!r}: operated_by[{k}] names no grip")
            if digit not in DIGITS:
                raise LayoutError(f"control {cid!r}: operated_by[{k}] digit is {digit!r}; "
                                  f"it is one of {', '.join(DIGITS)}")
            pairs.append((grip, digit))
        if not ops:
            if not isinstance(reason, str) or not reason.strip():
                raise LayoutError(f"control {cid!r} is pressed from no grip and gives no "
                                  f"regrip_reason: say why it is pressed after a regrip")
            out.append(Control(cid, at, (), "regrip", reason.strip(), ()))
            continue
        if reason not in (None, ""):
            raise LayoutError(f"control {cid!r} names a grip and a regrip_reason: a "
                              f"control is pressed from a grip or after a regrip, not both")
        named = tuple(sorted({grip for grip, _digit in pairs}))
        judged = tuple(p for p in pairs if p[1] in JUDGED_DIGITS)
        if judged:
            out.append(Control(cid, at, judged, "judged", "", named))
        else:
            out.append(Control(cid, at, (), "other", "", named))
    return out


def parse_grips(raw) -> dict[str, Grip]:
    """The grips as declared: ``{id, centre, hand_axis, handle_axis}``, the axes
    made unit, the hand axis within :data:`HAND_AXIS_TOLERANCE_DEG` of
    perpendicular to the handle axis."""
    if not isinstance(raw, (list, tuple)):
        raise LayoutError(f"hand_grips must be a list of grips, not {type(raw).__name__}")
    grips: dict[str, Grip] = {}
    for n, entry in enumerate(raw):
        where = f"hand_grips[{n}]"
        if not isinstance(entry, dict):
            raise LayoutError(f"{where} must be an object with id, centre, hand_axis and "
                              f"handle_axis")
        gid = entry.get("id")
        if not isinstance(gid, str) or not gid.strip():
            raise LayoutError(f"{where} has no id")
        if gid in grips:
            raise LayoutError(f"grip {gid!r} is declared twice")
        centre = point(entry.get("centre"), f"grip {gid!r} centre")
        hand = unit(entry.get("hand_axis"), f"grip {gid!r} hand_axis")
        handle = unit(entry.get("handle_axis"), f"grip {gid!r} handle_axis")
        lean = abs(90.0 - angle_deg(hand, handle))
        if lean > HAND_AXIS_TOLERANCE_DEG:
            raise LayoutError(
                f"grip {gid!r}: the hand axis is {lean:.1f} deg from perpendicular to the "
                f"handle axis (at most {HAND_AXIS_TOLERANCE_DEG:g}): wrist-centre of grip "
                f"length was measured with the handle square to the fist")
        grips[gid] = Grip(gid, centre, hand, handle)
    return grips


def check_grip_ids(controls: list[Control], grips: dict[str, Grip]) -> None:
    """:class:`LayoutError` when a control names a grip no grip declares."""
    for control in controls:
        for grip in control.named:
            if grip not in grips:
                raise LayoutError(f"control {control.id!r} names grip {grip!r}, which no "
                                  f"grip declares ({', '.join(sorted(grips)) or 'none'})")


def distal_mm(at: tuple, grip: Grip) -> float:
    """How far ``at`` lies beyond ``grip``'s centre along its hand axis, mm
    (negative: behind the centre, toward the wrist)."""
    return sum((p - c) * u for p, c, u in zip(at, grip.centre, grip.hand_axis))


def check_distal(controls: list[Control], grips: dict[str, Grip]) -> None:
    """:class:`LayoutError` when a judged control lies behind the centre of a grip
    it is judged from, along that grip's hand axis (:data:`DISTAL_MIN_MM`). Run
    after :func:`check_grip_ids`. The reach rule puts the wrist behind the grip,
    so a reversed hand axis puts the wrist in front of the controls: this is the
    refusal that catches it. A control pressed only by ``other`` or after a
    regrip is not judged, so it is not checked."""
    for control in controls:
        for grip_id, digit in control.pairs:
            beyond = distal_mm(control.at, grips[grip_id])
            # - 1e-9: floating noise on a control exactly in the centre's plane.
            if beyond < DISTAL_MIN_MM - 1e-9:
                raise LayoutError(
                    f"control {control.id!r} lies {-beyond:.1f} mm behind grip "
                    f"{grip_id!r}'s centre along its hand axis, under the palm, where no "
                    f"{digit} of that hand presses it: check that hand_axis points from "
                    f"the wrist to the hand and is not the handle axis")


# --------------------------------------------------------------------------- #
# the reach rule
# --------------------------------------------------------------------------- #
def girth_offset(diameter_mm: float) -> float:
    """How much farther from the wrist a handle of ``diameter_mm`` puts the centre
    of the grip than the survey's dowel did."""
    return (float(diameter_mm) - DOWEL_MM) / 2.0


def wrist_point(grip: Grip, wcg: float, diameter_mm: float) -> tuple:
    """The stylion of a subject with wrist-centre of grip length ``wcg`` holding
    ``grip``: behind the grip centre, along the hand axis."""
    back = float(wcg) + girth_offset(diameter_mm)
    return tuple(c - back * u for c, u in zip(grip.centre, grip.hand_axis))


def radius(subject: Subject, digit: str) -> int:
    """The reach of ``digit`` from the stylion: wrist-thumbtip or wrist-index."""
    return subject.wtt if digit == "thumb" else subject.wif


def thresholds(control: Control, grips: dict[str, Grip], wcgs, diameter_mm: float) -> dict:
    """``{(grip, digit): {wcg: distance}}``: the distance from each distinct wrist
    point to the control. A subject reaches the control by that pair when its
    radius is at least the distance at its own wcg. Computed once per distinct
    wcg (the table holds integers), so a count over 3,982 subjects costs one
    comparison each."""
    out: dict = {}
    for grip_id, digit in control.pairs:
        grip = grips[grip_id]
        out[(grip_id, digit)] = {w: math.dist(control.at, wrist_point(grip, w, diameter_mm))
                                 for w in wcgs}
    return out


def accommodation(subjects, controls: list[Control], grips: dict[str, Grip],
                  diameter_mm: float) -> tuple[int, dict]:
    """``(accommodated, per_control)`` for one sex: how many subjects reach every
    judged control, and for each judged control how many reach it."""
    judged = [c for c in controls if c.mode == "judged"]
    wcgs = sorted({s.wcg for s in subjects})
    table = {c.id: thresholds(c, grips, wcgs, diameter_mm) for c in judged}
    per = {c.id: 0 for c in judged}
    accommodated = 0
    for s in subjects:
        every = True
        for c in judged:
            ok = any(radius(s, digit) >= dist[s.wcg]
                     for (_grip, digit), dist in table[c.id].items())
            if ok:
                per[c.id] += 1
            else:
                every = False
        if every:
            accommodated += 1
    return accommodated, per


def share_pct(count: int, n: int) -> float:
    """``count`` of ``n`` subjects as a percentage, floored to 0.01%: the value the
    gate reports and judges, and its known-bad fixture computes.

    Floored, because every claim the gate holds is ``>=`` or ``>``, so the floor
    rounds toward the failing side. Integer division, so a share of exactly a
    hundredth is never a float's hair under it. What slipped through (review of
    E1): half-up to 0.01% read 1703 of 1774 men (95.998%) as 96.00 and passed
    ``>= 96`` with 1704 needed, and 1943 of 2208 women (87.998%) as 88.00 against
    ``>= 88``: one subject short, read as a pass. Rejected: half-up to 0.01% (that
    slip) or to 0.1% (the design's: more room for the same slip); the exact float
    (the verdict would carry 14 digits, and a two-digit detail line rounds it
    half-up again for the reader); the count judged against ``ceil(limit * n /
    100)`` beside a floored share (two rules where one does)."""
    return (10000 * int(count) // int(n)) / 100.0


def reach_index(subjects) -> dict:
    """``{digit: {wcg: sorted radii}}`` for one sex: what :func:`share_at` counts
    with, so a share at one point costs a bisection per distinct wcg."""
    out: dict = {d: collections.defaultdict(list) for d in JUDGED_DIGITS}
    for s in subjects:
        for digit in JUDGED_DIGITS:
            out[digit][s.wcg].append(radius(s, digit))
    return {d: {w: sorted(r) for w, r in sorted(by.items())} for d, by in out.items()}


def share_at(at: tuple, grip: Grip, digit: str, index: dict, n: int,
             diameter_mm: float) -> float:
    """The percentage of one sex (``index`` from :func:`reach_index`, ``n``
    subjects) whose ``digit`` reaches ``at`` from ``grip``: the same comparison
    as :func:`accommodation`, for one point and one digit."""
    count = 0
    for w, radii in index[digit].items():
        d = math.dist(at, wrist_point(grip, w, diameter_mm))
        count += len(radii) - bisect.bisect_left(radii, d)
    return 100.0 * count / n
