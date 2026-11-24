# SPDX-License-Identifier: Apache-2.0
"""ergo.reach_envelope — where the hands reach, as a share of ANSUR 1988 subjects.

    nopekit propose ergo.reach_envelope -- --layout layout.json [--target 95]
        [--grid 2] [--plane x0,y0,z0,nx,ny,nz] [--pitch 15 --pitch 19]
        [--face=-75,75,-40,20] [--centre menu,view]
        [--tester-wtt-mm 112 --tester-wcg-mm 70]

(Join ``--face`` and ``--plane`` to their value with ``=`` when it starts with a
minus sign, or argparse reads it as a flag.)

A proposer, never an evaluator: it says where a control could go, and
``ergo.digit_reach`` judges where it went. Its files become inputs; the agent
records in a decision what it adopts. It uses the gate's own arithmetic
(``gates/_anthro.py``), so a position this map calls 95% is one the gate counts
the same way.

**The layout file** is a JSON object with the three keys the gate reads, under
their primary spelling (or scoped, ``ergo.<key>``): ``hand_grips``,
``hand_controls`` and ``hand_grip_diameter_mm``, as the pack's
``selftest/baseline.json`` spells them. Other keys are ignored.

**What it writes** under ``$NOPEKIT_PROPOSAL_DIR`` (or ``--out``), byte-stable
(sorted keys, no clock):

- ``reach_map.json``: for each grip and digit that presses a judged control, a
  grid on the face plane (``--grid`` mm, default 2; by default the plane z =
  the thumb controls' mean height, its s and t the device's x and y; ``--plane``
  for another) with the share of women and
  of men who reach each node; for each control its share and ``outward_mm``,
  how far it can move outward (away from the wrist) before the worst sex falls
  below ``--target`` (negative: how far it must move toward the wrist to reach
  it).
- ``fit_counts.json`` (with ``--pitch``): for each pitch, the most nodes of a
  square grid that fit in each thumb's target region, cut to the face's extent
  when ``--face`` gives it. Reach, not the printer, caps how many keys of a size
  a thumb can work.
- ``grip_spacing.json`` (with exactly two grips): the widest symmetric grip
  spacing at which the centre controls stay at the target. The reach part of
  the width question, never the width limit.
- ``design_range.json``: P1/P5/P50/P95/P99 of each hand measurement by sex, the
  wrist band range for a wearable, the handle diameter rated most comfortable
  for maximal grip (Kong and Lowe 2005, a tool-handle result), the reach-tester
  thresholds, and, with ``--tester-*``, the tester's percentile.
- ``reach_map.svg``: the target contours and the controls, drawn by matplotlib
  when it is importable; otherwise ``reach_map.json`` says it was not drawn.

A share on the map is **marginal**: the share that reaches that one point. The
gate counts the subjects who reach every control at once, which is never more.
Exit 2, writing nothing, on a layout the gate would refuse or a bad argument.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.dont_write_bytecode = True        # never leave a __pycache__ in the pack
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "gates"))
import _anthro as A                                                 # noqa: E402

#: The margin round the grips and controls that the map covers, mm. A thumb
#: reaches past the outermost control, and the map is where the next control
#: could go. Rejected: 0 (the map would end at the controls already placed);
#: 60 (the extra nodes are past every thumb's reach and only grow the file).
MAP_MARGIN_MM = 30.0

#: How far a control's outward search goes each way, mm, and its resolution.
#: Past 60 mm no hand in the survey is near the edge of its reach.
SEARCH_MM = 60.0
SEARCH_STEP_MM = 0.25

#: The grip-spacing search: at most 60 mm narrower and 120 mm wider.
SPACING_IN_MM = 60.0
SPACING_OUT_MM = 120.0

#: The default centre controls: those whose position along the line between the
#: two grips is within the middle third. Rejected: the middle half (it takes in
#: the face buttons of a 120 mm controller, which move with the hand when the
#: body widens).
CENTRE_FRACTION = 1.0 / 3.0

#: Kong and Lowe (2005), Int J Ind Ergon 35(6):495-507: the cylindrical handle
#: diameter rated most comfortable for maximal grip force, from a regression on
#: 24 subjects and 25-50 mm aluminium handles, is 19.7% of hand length. A tool
#: handle result: reported, never judged.
KONG_LOWE_FRACTION = 0.197

#: The percentiles a design range gives.
PERCENTILES = (1, 5, 50, 95, 99)

MEASURES = (("hand_length_mm", "hand_length"), ("hand_breadth_mm", "hand_breadth"),
            ("thumb_breadth_mm", "thumb_breadth"), ("wrist_thumbtip_mm", "wtt"),
            ("wrist_index_mm", "wif"), ("wrist_grip_mm", "wcg"),
            ("wrist_circ_mm", "wrist_circ"))


class Refused(Exception):
    """A bad argument or a layout the gate would refuse: exit 2, nothing written."""


# --------------------------------------------------------------------------- #
# vectors and the plane
# --------------------------------------------------------------------------- #
def _add(a, b, k=1.0):
    return tuple(x + k * y for x, y in zip(a, b))


def _dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _norm(v):
    n = math.sqrt(_dot(v, v))
    return tuple(x / n for x in v)


def _r(x, nd=3):
    return round(float(x), nd) + 0.0     # + 0.0: never write -0.0


def _share(pct):
    """A map node's share, to 0.1%: an integer when it is one (0 and 100 fill most
    of a map, and the file is a proposal an agent reads, not a table to diff)."""
    value = round(float(pct), 1)
    return int(value) if value == int(value) else value


class Plane:
    """A plane with an origin and two in-plane axes: ``s`` is x leaned into the
    plane, ``t`` completes the right-handed frame with the normal."""

    def __init__(self, origin, normal):
        self.origin = tuple(origin)
        self.normal = _norm(normal)
        x = (1.0, 0.0, 0.0)
        lean = _add(x, self.normal, -_dot(x, self.normal))
        if math.sqrt(_dot(lean, lean)) < 1e-6:
            y = (0.0, 1.0, 0.0)
            lean = _add(y, self.normal, -_dot(y, self.normal))
        self.s = _norm(lean)
        self.t = _cross(self.normal, self.s)

    def point(self, s, t):
        return tuple(o + s * a + t * b for o, a, b in zip(self.origin, self.s, self.t))

    def coords(self, p):
        d = _add(p, self.origin, -1.0)
        return _dot(d, self.s), _dot(d, self.t)

    def describe(self):
        return {"origin": [_r(c) for c in self.origin], "normal": [_r(c, 6) for c in self.normal],
                "s_axis": [_r(c, 6) for c in self.s], "t_axis": [_r(c, 6) for c in self.t]}


# --------------------------------------------------------------------------- #
# the counts
# --------------------------------------------------------------------------- #
class Population:
    def __init__(self, table):
        self.table = table
        self.n = {sex: len(table.subjects[sex]) for sex in A.SEXES}
        self.index = {sex: A.reach_index(table.subjects[sex]) for sex in A.SEXES}

    def counts(self, controls, grips, diameter):
        """``{sex: (accommodated, {control: count})}`` for the controls together."""
        return {sex: A.accommodation(self.table.subjects[sex], controls, grips, diameter)
                for sex in A.SEXES}

    def shares(self, controls, grips, diameter):
        """``{sex: (accommodated %, {control: %})}`` for the controls together,
        unrounded: what the searches compare with the target."""
        return {sex: (100.0 * count / self.n[sex],
                      {k: 100.0 * v / self.n[sex] for k, v in per.items()})
                for sex, (count, per) in self.counts(controls, grips, diameter).items()}

    def worst(self, controls, grips, diameter):
        return min(v[0] for v in self.shares(controls, grips, diameter).values())

    def node(self, at, grip, digit, diameter):
        return {sex: A.share_at(at, grip, digit, self.index[sex], self.n[sex], diameter)
                for sex in A.SEXES}


def _boundary(f, lo, hi, step):
    """The largest whole multiple of ``step`` in [lo, hi] with f true, f monotone
    (true, then false); None when f(lo) is false; hi when f(hi) is true. ``lo``
    and ``hi`` are whole multiples of ``step``.

    It bisects on the multiples themselves, so what it returns is a point f was
    seen true at, whatever its sign. What slipped through (review of E1): the
    bisection ran on the reals and rounded its last true x to a step, with ceil
    when it was negative: toward zero and past that x (-7.03 read -7.0), so an
    outward_mm or a spacing delta below zero was up to a step generous, at a
    position where the target may already fail. Rejected: floor at either sign
    on the real bisection (never generous, but up to two steps short, since the
    bisection stopped a step from the edge)."""
    k_lo, k_hi = round(lo / step), round(hi / step)
    if k_lo * step != lo or k_hi * step != hi:
        raise ValueError(f"_boundary: {lo:g} and {hi:g} are not whole steps of {step:g}")
    if not f(lo):
        return None
    if f(hi):
        return hi
    while k_hi - k_lo > 1:                    # f(k_lo * step) true, f(k_hi * step) false
        mid = (k_lo + k_hi) // 2
        if f(mid * step):
            k_lo = mid
        else:
            k_hi = mid
    return k_lo * step


# --------------------------------------------------------------------------- #
# the outputs
# --------------------------------------------------------------------------- #
def _layout(path):
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        raise Refused(f"--layout {path}: {exc}")
    if not isinstance(data, dict):
        raise Refused(f"--layout {path}: not a JSON object")

    def get(key):
        value = data.get(f"ergo.{key}", data.get(key))
        if value is None:
            raise Refused(f"--layout {path}: no {key}")
        return value
    try:
        controls = A.parse_controls(get("hand_controls"))
        grips = A.parse_grips(get("hand_grips"))
        A.check_grip_ids(controls, grips)
        A.check_distal(controls, grips)
    except A.LayoutError as exc:
        raise Refused(str(exc))
    diameter = get("hand_grip_diameter_mm")
    if not A._real(diameter) or not float(diameter) > 0:
        raise Refused(f"hand_grip_diameter_mm is {diameter!r}; it is a diameter in mm")
    return controls, grips, float(diameter)


def _default_plane(controls, grips):
    """The face plane: normal +z, through the mean height of the thumb controls,
    origin on the z axis, so its s and t are the device's x and y."""
    thumb = [c.at for c in controls if c.mode == "judged" and any(d == "thumb" for _g, d in c.pairs)]
    points = thumb or [c.at for c in controls] or [g.centre for g in grips.values()]
    return Plane((0.0, 0.0, sum(p[2] for p in points) / len(points)), (0.0, 0.0, 1.0))


def _pairs(controls, grips):
    used = sorted({p for c in controls if c.mode == "judged" for p in c.pairs})
    return used or [(g, d) for g in sorted(grips) for d in A.JUDGED_DIGITS]


def reach_map(pop, controls, grips, diameter, plane, grid, target):
    pts = [plane.coords(c.at) for c in controls] + [plane.coords(g.centre) for g in grips.values()]
    s_lo = math.floor((min(p[0] for p in pts) - MAP_MARGIN_MM) / grid) * grid
    s_hi = math.ceil((max(p[0] for p in pts) + MAP_MARGIN_MM) / grid) * grid
    t_lo = math.floor((min(p[1] for p in pts) - MAP_MARGIN_MM) / grid) * grid
    t_hi = math.ceil((max(p[1] for p in pts) + MAP_MARGIN_MM) / grid) * grid
    ss = [s_lo + k * grid for k in range(int(round((s_hi - s_lo) / grid)) + 1)]
    ts = [t_lo + k * grid for k in range(int(round((t_hi - t_lo) / grid)) + 1)]
    maps = {}
    for grip_id, digit in _pairs(controls, grips):
        grip = grips[grip_id]
        women, men = [], []
        for t in ts:
            row_w, row_m = [], []
            for s in ss:
                share = pop.node(plane.point(s, t), grip, digit, diameter)
                row_w.append(_share(share["F"]))
                row_m.append(_share(share["M"]))
            women.append(row_w)
            men.append(row_m)
        maps[f"{grip_id} {digit}"] = {"grip": grip_id, "digit": digit,
                                      "women_pct": women, "men_pct": men}

    pooled = sorted(s.wcg for sex in A.SEXES for s in pop.table.subjects[sex])
    median_wcg = A.percentile(pooled, 50)
    rows = {}
    for c in controls:
        if c.mode != "judged":
            rows[c.id] = {"judged": False,
                          "why": c.reason if c.mode == "regrip" else "pressed by another digit"}
            continue
        counts = pop.counts([c], grips, diameter)
        wrist = A.wrist_point(grips[c.pairs[0][0]], median_wcg, diameter)
        direction = _norm(_add(c.at, wrist, -1.0))

        def holds(x, c=c, direction=direction):
            moved = c._replace(at=_add(c.at, direction, x))
            return pop.worst([moved], grips, diameter) >= target
        out = _boundary(holds, -SEARCH_MM, SEARCH_MM, SEARCH_STEP_MM)
        rows[c.id] = {"judged": True,       # floored as the gate reports it
                      "women_pct": A.share_pct(counts["F"][1][c.id], pop.n["F"]),
                      "men_pct": A.share_pct(counts["M"][1][c.id], pop.n["M"]),
                      "outward_mm": None if out is None else _r(out, 2),
                      "outward_direction": [_r(v, 4) for v in direction]}
    return {"s_mm": {"from": _r(s_lo), "to": _r(s_hi), "step": _r(grid)},
            "t_mm": {"from": _r(t_lo), "to": _r(t_hi), "step": _r(grid)},
            "maps": maps, "controls": rows}, ss, ts


def fit_counts(maps, ss, ts, grips, plane, grid, target, pitches, face=None):
    """For each pitch and each thumb, the most nodes of a square lattice in the
    thumb's target region, over every lattice offset in grid steps.

    The region is the map's nodes at or above the target for the worst sex, cut
    two ways the sphere model cannot cut for itself: distal of the grip centre
    along the hand axis (behind it is under the palm), and nearer this grip than
    any other (the other hand works that side; a node equally near two grips is
    neither's), and, with ``--face``, inside the face's stated extent. Rejected: a wedge about the hand
    axis (no survey gives its angle); no cut (the left thumb's sphere reaches the
    right half of a 120 mm body, and the count said 27 keys at 15 mm)."""
    out = {}
    for pitch in pitches:
        per = {}
        for key, m in sorted(maps.items()):
            if m["digit"] != "thumb":
                continue
            grip = grips[m["grip"]]
            gs, gt = plane.coords(grip.centre)
            hs, ht = _dot(grip.hand_axis, plane.s), _dot(grip.hand_axis, plane.t)
            others = [plane.coords(g.centre) for k, g in sorted(grips.items()) if k != grip.id]

            def inside(s, t):
                i = int(round((t - ts[0]) / grid))
                j = int(round((s - ss[0]) / grid))
                if not (0 <= i < len(ts) and 0 <= j < len(ss)):
                    return False
                if (s - gs) * hs + (t - gt) * ht < 0:
                    return False                      # behind the grip: under the palm
                own = math.hypot(s - gs, t - gt)
                if any(math.hypot(s - os_, t - ot) <= own for os_, ot in others):
                    return False                      # the other hand's side, or the line
                if face and not (face[0] <= s <= face[1] and face[2] <= t <= face[3]):
                    return False                      # off the face the person stated
                return min(m["women_pct"][i][j], m["men_pct"][i][j]) >= target
            best = (-1, 0.0, 0.0, [])
            steps = max(1, int(math.ceil(pitch / grid)))
            for a in range(steps):
                for b in range(steps):
                    s0, t0 = ss[0] + a * grid, ts[0] + b * grid
                    nodes = []
                    t = t0
                    while t <= ts[-1] + 1e-9:
                        s = s0
                        while s <= ss[-1] + 1e-9:
                            if inside(s, t):
                                nodes.append((s, t))
                            s += pitch
                        t += pitch
                    if len(nodes) > best[0]:
                        best = (len(nodes), s0, t0, nodes)
            per[key] = {"fits": best[0],
                        "lattice_origin_st_mm": [_r(best[1]), _r(best[2])],
                        "positions_mm": [[_r(c) for c in plane.point(s, t)] for s, t in best[3]]}
        out[f"{pitch:g}"] = per
    return out


def grip_spacing(pop, controls, grips, diameter, target, centre_ids):
    if len(grips) != 2:
        return {"not computed": f"{len(grips)} grips: the spacing search needs exactly two"}
    g1, g2 = (grips[k] for k in sorted(grips))
    span = math.dist(g1.centre, g2.centre)
    axis = _norm(_add(g2.centre, g1.centre, -1.0))
    mid = tuple((a + b) / 2.0 for a, b in zip(g1.centre, g2.centre))
    judged = [c for c in controls if c.mode == "judged"]
    if centre_ids:
        unknown = sorted(set(centre_ids) - {c.id for c in judged})
        if unknown:
            raise Refused(f"--centre names {', '.join(unknown)}, not judged controls")
        centre = [c for c in judged if c.id in centre_ids]
        how = "named with --centre"
    else:
        centre = [c for c in judged
                  if abs(_dot(_add(c.at, mid, -1.0), axis)) <= span * CENTRE_FRACTION / 2.0]
        how = "within the middle third of the line between the grips"
    if not centre:
        return {"not computed": "no centre control: name them with --centre", "centre": how}

    def moved(delta):
        return {g1.id: g1._replace(centre=_add(g1.centre, axis, -delta)),
                g2.id: g2._replace(centre=_add(g2.centre, axis, delta))}

    def holds(delta):
        return pop.worst(centre, moved(delta), diameter) >= target
    delta = _boundary(holds, -SPACING_IN_MM, SPACING_OUT_MM, SEARCH_STEP_MM)
    result = {"centre_controls": sorted(c.id for c in centre), "centre": how,
              "spacing_now_mm": _r(span, 2), "target_pct": target}
    if delta is None:
        result["widest_spacing_mm"] = None
        result["note"] = "the centre controls miss the target even with the grips 60 mm closer"
        return result
    shares = pop.shares(centre, moved(delta), diameter)
    per = {c.id: min(shares[s][1][c.id] for s in A.SEXES) for c in centre}
    result["widest_spacing_mm"] = _r(span + 2.0 * delta, 2)
    result["limiting_control"] = min(sorted(per), key=lambda k: per[k])
    return result


def design_range(table, tester):
    def stats(values):
        return {f"P{p}": _r(A.percentile(values, p), 1) for p in PERCENTILES}
    out = {}
    for name, field in MEASURES:
        out[name] = {A.SEX_WORDS[s]: stats([getattr(x, field) for x in table.subjects[s]])
                     for s in A.SEXES}
    comp = {s: [x.wtt - x.wcg for x in table.subjects[s]] for s in A.SEXES}
    out["reach_beyond_grip_mm"] = {A.SEX_WORDS[s]: stats(comp[s]) for s in A.SEXES}
    women_len = [x.hand_length for x in table.subjects["F"]]
    men_len = [x.hand_length for x in table.subjects["M"]]
    out["handle_diameter_rated_most_comfortable_mm"] = {
        "source": "Kong and Lowe 2005: 19.7% of hand length, maximal grip force on 25-50 mm "
                  "cylindrical tool handles, 24 subjects. A tool-handle result, not a device "
                  "result; reported, never judged.",
        "women_P5": _r(KONG_LOWE_FRACTION * A.percentile(women_len, 5), 1),
        "women_P50": _r(KONG_LOWE_FRACTION * A.percentile(women_len, 50), 1),
        "men_P50": _r(KONG_LOWE_FRACTION * A.percentile(men_len, 50), 1),
        "men_P95": _r(KONG_LOWE_FRACTION * A.percentile(men_len, 95), 1)}
    wrist_w = [x.wrist_circ for x in table.subjects["F"]]
    wrist_m = [x.wrist_circ for x in table.subjects["M"]]
    out["wrist_band_range_mm"] = {
        "what": "wrist circumference over the stylion: a band from the women's P5 to the "
                "men's P95 fits 95% of each sex at its small or large end",
        "women_P5": _r(A.percentile(wrist_w, 5), 1), "men_P95": _r(A.percentile(wrist_m, 95), 1),
        "women_P1": _r(A.percentile(wrist_w, 1), 1), "men_P99": _r(A.percentile(wrist_m, 99), 1)}
    out["reach_testers"] = {
        "what": "the two testers of the physical reach test, chosen by reach beyond the grip "
                "(wrist-thumbtip minus wrist-centre of grip, mm)",
        "small_at_or_below_women_P10": _r(A.percentile(comp["F"], 10), 1),
        "large_at_or_above_men_P90": _r(A.percentile(comp["M"], 90), 1)}
    if tester is not None:
        wtt, wcg = tester
        value = wtt - wcg
        out["tester"] = {
            "reach_beyond_grip_mm": _r(value, 1),
            "share_at_or_below_pct": {A.SEX_WORDS[s]: _r(100.0 * sum(1 for v in comp[s]
                                                                      if v <= value)
                                                         / len(comp[s]), 1)
                                      for s in A.SEXES},
            "small_tester": value <= out["reach_testers"]["small_at_or_below_women_P10"],
            "large_tester": value >= out["reach_testers"]["large_at_or_above_men_P90"]}
    return out


def draw(path, maps, ss, ts, controls, plane, target):
    """reach_map.svg, or the reason it was not drawn."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as exc:                       # noqa: BLE001 - optional
        return f"not drawn: matplotlib is not importable ({type(exc).__name__})"
    matplotlib.rcParams["svg.hashsalt"] = "nopekit-reach-envelope"
    matplotlib.rcParams["svg.fonttype"] = "none"
    fig, ax = plt.subplots(figsize=(7, 5))
    for k, (key, m) in enumerate(sorted(maps.items())):
        worst = [[min(a, b) for a, b in zip(rw, rm)]
                 for rw, rm in zip(m["women_pct"], m["men_pct"])]
        ax.contour(ss, ts, worst, levels=[target], colors=[f"C{k % 10}"])
        ax.plot([], [], color=f"C{k % 10}", label=f"{key}: {target:g}% of the worst sex")
    for c in controls:
        s, t = plane.coords(c.at)
        ax.plot([s], [t], marker="o" if c.mode == "judged" else "x", color="black", ms=4)
        ax.annotate(c.id, (s, t), fontsize=6, xytext=(3, 3), textcoords="offset points")
    ax.set_aspect("equal")
    ax.set_xlabel("s, mm (in the plane)")
    ax.set_ylabel("t, mm (in the plane)")
    ax.legend(fontsize=6, loc="lower center")
    fig.savefig(path, format="svg", metadata={"Date": None})
    plt.close(fig)
    return os.path.basename(path)


def _dump(path, payload, *, compact=False):
    with open(path, "w", encoding="utf-8") as fh:
        if compact:
            json.dump(payload, fh, separators=(",", ":"), sort_keys=True)
        else:
            json.dump(payload, fh, indent=1, sort_keys=True)
        fh.write("\n")


def main(argv):
    ap = argparse.ArgumentParser(prog="ergo.reach_envelope", description=__doc__.splitlines()[0])
    ap.add_argument("--layout", required=True)
    ap.add_argument("--target", type=float, default=95.0)
    ap.add_argument("--grid", type=float, default=2.0)
    ap.add_argument("--plane", default="")
    ap.add_argument("--pitch", type=float, action="append", default=[])
    ap.add_argument("--centre", default="")
    ap.add_argument("--face", default="")
    ap.add_argument("--tester-wtt-mm", type=float)
    ap.add_argument("--tester-wcg-mm", type=float)
    ap.add_argument("--out", default=os.environ.get("NOPEKIT_PROPOSAL_DIR", ""))
    args = ap.parse_args(argv)
    try:
        if not args.out:
            raise Refused("no output directory: run it with nopekit propose, or pass --out")
        if not 0.0 < args.target <= 100.0:
            raise Refused(f"--target {args.target:g}: a share in % above 0")
        if not 0.5 <= args.grid <= 10.0:
            raise Refused(f"--grid {args.grid:g}: between 0.5 and 10 mm")
        if any(not 2.0 <= p <= 60.0 for p in args.pitch):
            raise Refused("--pitch: between 2 and 60 mm")
        if (args.tester_wtt_mm is None) != (args.tester_wcg_mm is None):
            raise Refused("--tester-wtt-mm and --tester-wcg-mm go together")
        controls, grips, diameter = _layout(args.layout)
        if args.plane:
            try:
                nums = [float(x) for x in args.plane.split(",")]
                assert len(nums) == 6
                plane = Plane(nums[:3], A.unit(nums[3:], "--plane normal"))
            except (ValueError, AssertionError, A.LayoutError):
                raise Refused("--plane x0,y0,z0,nx,ny,nz: six numbers, a non-zero normal")
        else:
            plane = _default_plane(controls, grips)
        face = None
        if args.face:
            try:
                face = [float(x) for x in args.face.split(",")]
                assert len(face) == 4 and face[0] < face[1] and face[2] < face[3]
            except (ValueError, AssertionError):
                raise Refused("--face s0,s1,t0,t1: the face's extent in the plane, mm, "
                              "each pair low then high")
        table = A.read_subjects(A.TABLE_PATH)
    except Refused as exc:
        print(f"ergo.reach_envelope: {exc}", file=sys.stderr)
        return 2

    pop = Population(table)
    common = {"table_sha256": table.sha256, "handle_diameter_mm": _r(diameter),
              "target_pct": args.target, "proposer": "ergo.reach_envelope"}
    try:
        mapped, ss, ts = reach_map(pop, controls, grips, diameter, plane, args.grid, args.target)
        spacing = grip_spacing(pop, controls, grips, diameter, args.target,
                               [x for x in args.centre.split(",") if x])
    except Refused as exc:
        print(f"ergo.reach_envelope: {exc}", file=sys.stderr)
        return 2
    tester = None if args.tester_wtt_mm is None else (args.tester_wtt_mm, args.tester_wcg_mm)
    ranges = design_range(table, tester)
    os.makedirs(args.out, exist_ok=True)
    figure = draw(os.path.join(args.out, "reach_map.svg"), mapped["maps"], ss, ts, controls,
                  plane, args.target)
    _dump(os.path.join(args.out, "reach_map.json"), {
        "_what": "the share of each sex whose digit reaches each node from each grip "
                 "(marginal: one point at a time; ergo.digit_reach counts every control at "
                 "once, which is never more), and for each control how far it can move "
                 "outward, away from the wrist, before the worst sex falls below the target "
                 "(negative: how far it must move toward the wrist to reach the target)",
        **common, "plane": plane.describe(), "figure": figure, **mapped}, compact=True)
    _dump(os.path.join(args.out, "grip_spacing.json"), {
        "_what": "the widest symmetric grip spacing at which the centre controls, held where "
                 "they are, stay at the target for the worst sex: the reach part of the width "
                 "question, never the width limit",
        **common, **spacing})
    _dump(os.path.join(args.out, "design_range.json"), {
        "_what": "ANSUR 1988 design ranges by sex, mm (type-7 percentiles)", **common,
        **ranges})
    if args.pitch:
        _dump(os.path.join(args.out, "fit_counts.json"), {
            "_what": "for each pitch, the most nodes of a square grid, aligned with the plane's "
                     "axes, that lie in each thumb's target region: marginal share at or above "
                     "the target, distal of the grip centre, and nearer this grip than any "
                     "other; each node judged at the nearest map node. An upper bound: the "
                     "sphere model knows neither the palm nor the body's outline",
            **common, "face_st_mm": face,
            "pitch_mm": fit_counts(mapped["maps"], ss, ts, grips, plane, args.grid,
                                   args.target, args.pitch, face)})
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
