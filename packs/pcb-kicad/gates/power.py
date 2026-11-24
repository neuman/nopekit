# SPDX-License-Identifier: Apache-2.0
"""pcb-kicad — three rules for a board that carries a lithium cell.

    pcb.charge_protection  tier 0  the charger's thermistor input is wired to an NTC, and it has a safety timer
    pcb.fuse_conductor     tier 0  the fuse sits between the cell and the conductor, and trips before it melts
    pcb.discharge_cutoff   tier 0  something cuts the cell off above its minimum voltage

**Where each verdict comes from.** This pack's own code, on two kinds of input:
the board's pad nets, read with the pack's reader (``_kicad.py``), and the parts'
datasheet facts, entered by a person as records (``pcb_cell``, ``pcb_charger``,
``pcb_fuse``, ``pcb_conductor``, ``pcb_cutoffs``), each with its ``source``. The
arithmetic is a lookup of pads on nets and one published formula (IEC 60949's
adiabatic short-circuit limit), so the basis is sourced: the rules are only as true
as the records, and PACK.md says so.

**Bound to the design.** Each record names the board it is for (``board``, else
``pcb_board``), and the ref, value and pins it describes. A ref the board lacks, a
value the board does not give that ref, or a pin with no pad errors: "the record is
for another design". With the board file keyed, that is how a generator that moves
a part or changes a value stales the records instead of reading them on trust.

**Read as strictly as the board.** A record may name a board ``pcb.board_readable``
never reads, so each rule refuses a board this reader cannot vouch for itself
(``_kicad.board_problems``), as an error. A part marked do-not-populate or left out
of the BOM (``_kicad.NOT_FITTED``) is not on the built board, and no rule counts it.
A record's numbers are held to their kind: ``safety_timer`` a JSON true or false, an
area, a current or a trip time above 0, a ``source`` that says something. (What
slipped through, in review: an NTC, a cutoff and its series resistor marked dnp each
read as fitted; ``"safety_timer": "false"``, a string, read as a timer; a negative
area squared away; a cutoff with an empty source counted.)

**What slipped through** (a generated design with a 1-cell lithium battery, every
design rule and the netlist clean): the charger had no thermistor input and no
safety timer; the PPTC fuse was guaranteed to trip only at a current where the
thin conductor it protected reached its rating first; and nothing on the boards
cut the cell off when it ran down, so only the cell's own PCM stood under it, and
the PCM opened below the cell's rated minimum.
"""
from __future__ import annotations

import math
import os

from nopekit.gates import gate, GateContext
from nopekit.modelio import load_path as _load_path
from nopekit.models import NegativeControl, Tier, Verdict

_HERE = os.path.dirname(os.path.abspath(__file__))
K = _load_path(os.path.join(_HERE, "_kicad.py"))

#: IEC 60949 (1988), the adiabatic short-circuit temperature limit of a
#: conductor: I^2 t = K^2 S^2 ln((theta_f + beta) / (theta_i + beta)), for copper
#: K = 226 A s^0.5 / mm^2 and beta = 234.5 C (the reciprocal of copper's
#: temperature coefficient of resistance at 0 C, sign changed). It reproduces the
#: k factors IEC 60364-5-54 tabulates: 115 for PVC (70 -> 160 C), 143 for XLPE
#: (90 -> 250 C) (tests/test_pcb_kicad.py). Adiabatic: no heat leaves the
#: conductor, which holds for faults under about 5 s, so a trip time past
#: ADIABATIC_MAX_S is refused. Copper only. Rejected: IPC-2152's steady-state
#: curves (they say what a track carries for hours, not for a fault's fraction of
#: a second); a fusing formula such as Onderdonk's (it says when copper melts,
#: past every insulation's rating).
K_COPPER = 226.0
BETA_COPPER_C = 234.5

#: The longest fault the adiabatic formula is read for: IEC 60949's own bound
#: for neglecting heat loss, as IEC 60364-5-54 uses it.
ADIABATIC_MAX_S = 5.0


def adiabatic_s(current_a: float, area_mm2: float, start_c: float, limit_c: float) -> float:
    """Seconds for ``current_a`` to heat a copper conductor of ``area_mm2`` from
    ``start_c`` to ``limit_c``, no heat lost (IEC 60949)."""
    k = K_COPPER * math.sqrt(math.log((limit_c + BETA_COPPER_C) / (start_c + BETA_COPPER_C)))
    return (k * area_mm2 / current_a) ** 2


def area_for_s(seconds: float, current_a: float, start_c: float, limit_c: float) -> float:
    """The inverse of :func:`adiabatic_s`: the area that reaches ``limit_c`` in
    ``seconds``."""
    k = K_COPPER * math.sqrt(math.log((limit_c + BETA_COPPER_C) / (start_c + BETA_COPPER_C)))
    return math.sqrt(seconds) * current_a / k


_skip, _evidence = K.skip, K.evidence


def _error(gid: str, why: str) -> Verdict:
    return Verdict(gate=gid, passed=False, error=why)


class RecordError(ValueError):
    """A record the gate cannot read as one for this design."""


def _checked(rec, label: str, fields: tuple[str, ...]) -> dict:
    """``rec``, held to being a record with ``fields`` and a ``source``. Raises
    RecordError on anything else: a datasheet fact with no source is a number
    nobody can check."""
    if not isinstance(rec, dict):
        raise RecordError(f"{label} is not a record (a JSON object)")
    missing = [f for f in (*fields, "source") if f not in rec]
    if missing:
        raise RecordError(f"{label} has no {', '.join(missing)}")
    if not str(rec.get("source") or "").strip():
        raise RecordError(f"{label}'s source is empty: say where each number comes from")
    return rec


def _record(ctx: GateContext, key: str, fields: tuple[str, ...]) -> dict | None:
    """The record ``key`` (:func:`_checked`), None when the project states none."""
    rec = ctx.param(key)
    return None if rec is None else _checked(rec, key, fields)


def _board(ctx: GateContext, rec: dict) -> tuple[str, object]:
    """``(path, Board)`` of the board a record names (``board``, else
    ``pcb_board``), read through ``ctx.load_file`` so the file is keyed, and
    refused as ``pcb.board_readable`` would fail it."""
    rel = rec.get("board") or ctx.param("pcb_board")
    if not rel:
        raise RecordError("the record names no board, and there is no pcb_board")
    path = K.project_path(ctx, rel)
    if not os.path.isfile(path):
        raise RecordError(f"no board at {path}")
    try:
        _version, problems = K.board_problems(ctx.load_file(path, loader=K.load_tree))
    except (K.KicadFormatError, UnicodeDecodeError) as exc:
        problems = [f"it does not parse: {exc}"]
    if problems:
        raise RecordError(f"the board {os.path.basename(path)} is not one this reader can "
                          f"vouch for: {'; '.join(problems)}")
    return path, ctx.load_file(path, loader=K.load_board)


def _part(board, rec: dict, key: str, pins=()) -> object:
    """The footprint a record describes, held to its ref, value and pins. Raises
    RecordError naming what the board has instead."""
    ref, value = str(rec["ref"]), str(rec["value"])
    fp = next((f for f in board.footprints if f.ref == ref), None)
    if fp is None:
        raise RecordError(f"the {key} record is for another design: the board has no {ref}")
    if fp.value != value:
        raise RecordError(f"the {key} record is for another design: board's {ref} is "
                          f"{fp.value}, the record's {value}")
    numbers = {p.number for p in fp.pads}
    for pin in pins:
        if str(pin) not in numbers:
            raise RecordError(f"the {key} record is for another design: {ref} ({value}) has "
                              f"no pad {pin}")
    return fp


def _net(fp, pin) -> str:
    return next((p.net for p in fp.pads if p.number == str(pin)), "")


def _pads_on(board, net: str) -> list[tuple[str, str]]:
    """``(ref, pad)`` of every fitted part's pad on ``net``."""
    return [(f.ref, p.number) for f in board.footprints if f.fitted
            for p in f.pads if net and p.net == net]


def _not_fitted(fp) -> str:
    return f"{fp.ref} ({fp.value}) is marked not fitted ({', '.join(fp.attrs)})"


CELL_FIELDS = ("ref", "value", "pin", "min_v", "pcm_cutoff_min_v")
CUTOFF_FIELDS = ("ref", "value", "sense_pin", "series_ref", "threshold_min_v")


def _through(series, cfp, cell_net: str, sense: str) -> str:
    """Why ``series`` is not the one part between the cell's net and ``sense``,
    or "" when it is: a fitted two-pad part, not the cell's own, one pad on each."""
    if series.ref == cfp.ref:
        return f"{series.ref} is the cell's own connector"
    if not series.fitted:
        return _not_fitted(series)
    nets = sorted(p.net for p in series.pads if p.number)
    if len(nets) != 2:
        return f"{series.ref} has {len(nets)} numbered pads, not two"
    if nets != sorted([cell_net, sense]) or cell_net == sense:
        return (f"its pads sit on {', '.join(n or 'no net' for n in nets)}, not on the "
                f"cell's {cell_net} and the sense pad's {sense or 'no net'}")
    return ""


# --------------------------------------------------------------------------- #
# the charger
# --------------------------------------------------------------------------- #
@gate(
    id="pcb.charge_protection",
    basis="sourced",
    title="The cell's charger has its thermistor input wired to an NTC and a safety timer",
    claims=["pcb", "battery-safety", "charge-protection"],
    tier=Tier.INSTANT,
    settles="charger thermal and timer protection",
    negative_control=NegativeControl(
        fixture="selftest/bad_boards.py:ntc_unwired",
        note="the power baseline with TH1's THERM-side pad moved to a net of its own: "
             "the charger's THERM pin then reads no thermistor, and charges a hot cell",
    ),
)
def charge_protection(ctx: GateContext) -> Verdict:
    """Two protections a 1-cell LiPo charger must have, from ``pcb_charger``:

    (a) a thermistor input wired to the cell's NTC. ``thermistor_pin`` names the
    charger's THERM pin (null: the part has none); its pad's net must hold a pad of
    ``ntc_ref``, the NTC's other pad must sit on another net that some other fitted
    part also reaches (a pad on no net, or on a net of its own, senses nothing), and
    no other pad of the charger itself may sit on the THERM net (THERM tied to its
    own VSS or VDD, the datasheet's way to switch the input off). The charger and the
    NTC must be fitted. (b) ``safety_timer``: the datasheet gives the part a charge
    timer that ends a charge that never finishes, a JSON true or false.

    measured is the count missing, against 0. A charger protected another way (a
    supervisor that floats PROG, a pack with its own NTC cutoff) reads FAIL here:
    PACK.md names it, and a person waives it with a reason."""
    gid = "pcb.charge_protection"
    try:
        rec = _record(ctx, "pcb_charger", ("ref", "value", "thermistor_pin", "ntc_ref",
                                            "safety_timer"))
        if rec is None:
            return _skip(gid, "no pcb_charger: which part charges the cell, its THERM pin "
                              "and whether its datasheet gives it a safety timer are a "
                              "person's reading of the datasheet, entered as a record")
        if not isinstance(rec["safety_timer"], bool):
            raise RecordError(f"pcb_charger's safety_timer is {rec['safety_timer']!r}: give "
                              f"true or false (a string such as \"false\" reads as true)")
        path, board = _board(ctx, rec)
        therm = rec["thermistor_pin"]
        fp = _part(board, rec, "pcb_charger", [therm] if therm is not None else [])
        ntc = None
        if therm is not None and rec["ntc_ref"] is not None:
            ntc = next((f for f in board.footprints if f.ref == str(rec["ntc_ref"])), None)
            if ntc is None:
                raise RecordError(f"the pcb_charger record is for another design: the "
                                  f"board has no {rec['ntc_ref']}")
    except RecordError as exc:
        return _error(gid, str(exc))
    missing = []
    if not fp.fitted:
        missing.append(_not_fitted(fp))
    elif therm is None:
        missing.append(f"{fp.ref} ({fp.value}) has no thermistor input")
    else:
        net = _net(fp, therm)
        on_net = _pads_on(board, net)
        own = [n for r, n in on_net if r == fp.ref and n != str(therm)]
        # The NTC's far end: a net some other fitted part reaches.
        other = [p.net for p in ntc.pads if p.number and p.net != net] if ntc else []
        far = [n for n in other if n and any(r != ntc.ref for r, _ in _pads_on(board, n))]
        if ntc is None:
            missing.append(f"{fp.ref} THERM (pad {therm}) is wired to no thermistor "
                           f"(ntc_ref is null)")
        elif not ntc.fitted:
            missing.append(_not_fitted(ntc))
        elif not any(r == ntc.ref for r, _ in on_net):
            missing.append(f"{fp.ref} THERM (pad {therm}, on {net or 'no net'}) reaches no "
                           f"pad of {ntc.ref}")
        elif not other:
            missing.append(f"{ntc.ref}'s two pads share one net, so it senses nothing")
        elif not far:
            missing.append(f"{ntc.ref}'s other pad is on {other[0] or 'no net'}, which no "
                           f"other part reaches, so it senses nothing")
        elif own:
            missing.append(f"{fp.ref} THERM is tied to its own pad(s) {', '.join(own)}, "
                           f"which turns the input off")
    if rec["safety_timer"] is not True:
        missing.append(f"{fp.ref} ({fp.value}) has no safety timer")
    ev = _evidence(ctx, "charge_protection", {"board": path, "record": rec,
                                              "missing": missing})
    detail = (f"{fp.ref} {fp.value}: " + ("; ".join(missing) if missing else
              f"THERM (pad {therm}) on {_net(fp, therm)} with {ntc.ref}, and a safety "
              f"timer"))
    return Verdict(gate=gid, passed=not missing, measured=len(missing), limit=0,
                   comparator="<=", units="protections missing", detail=detail, evidence=ev)


# --------------------------------------------------------------------------- #
# the fuse and its conductor
# --------------------------------------------------------------------------- #
@gate(
    id="pcb.fuse_conductor",
    basis="sourced",
    title="The fuse sits between the cell and its conductor, and trips before the conductor overheats",
    claims=["pcb", "battery-safety", "overcurrent-protection"],
    tier=Tier.INSTANT,
    settles="fuse protects its conductor",
    negative_control=NegativeControl(
        fixture="selftest/bad_boards.py:thin_conductor",
        note="the power baseline's wire made thin enough that IEC 60949 puts it at its "
             "limit in the fuse's guaranteed trip time divided by 1.15",
    ),
)
def fuse_conductor(ctx: GateContext) -> Verdict:
    """The fuse in ``pcb_fuse`` must sit with one pad on the cell's net
    (``pcb_cell``'s pin) and the other on the net of every pin of the conductor in
    ``pcb_conductor``, and at each point of its guaranteed trip curve
    (``trip_max_s``: ``[[amperes, seconds]]``, the datasheet's maximum time to trip)
    the conductor must not reach its ``limit_c`` from ``start_c`` before the fuse
    has tripped: IEC 60949's adiabatic time (:func:`adiabatic_s`) >= ``t_max``. The
    verdict reads the point with the least ratio.

    One conductor carries the whole fault current: contacts in parallel get no
    credit (a split is never even in a fault), which is conservative. Only the
    short-circuit points are judged: an overload (IEC 60364-4-43's I2 <= 1.45 Iz)
    is not (PACK.md)."""
    gid = "pcb.fuse_conductor"
    try:
        cell = _record(ctx, "pcb_cell", CELL_FIELDS)
        fuse = _record(ctx, "pcb_fuse", ("ref", "value", "trip_max_s"))
        cond = _record(ctx, "pcb_conductor", ("ref", "value", "pins", "area_mm2", "start_c",
                                              "limit_c"))
        if cell is None or fuse is None or cond is None:
            absent = [k for k, v in (("pcb_cell", cell), ("pcb_fuse", fuse),
                                     ("pcb_conductor", cond)) if v is None]
            return _skip(gid, f"no {', '.join(absent)}: the cell, the fuse's guaranteed trip "
                              f"times and the conductor it protects are records a person "
                              f"enters from the datasheets")
        paths = {_board(ctx, r)[0] for r in (cell, fuse, cond)}
        if len(paths) > 1:
            raise RecordError(f"pcb_cell, pcb_fuse and pcb_conductor name {len(paths)} boards: "
                              f"the fuse is judged on the board that holds all three")
        path, board = _board(ctx, cell)
        cfp = _part(board, cell, "pcb_cell", [cell["pin"]])
        ffp = _part(board, fuse, "pcb_fuse")
        pins = [str(p) for p in cond["pins"]]
        kfp = _part(board, cond, "pcb_conductor", pins)
        curve = [(float(a), float(s)) for a, s in fuse["trip_max_s"]]
        area, start, limit = (float(cond[k]) for k in ("area_mm2", "start_c", "limit_c"))
        # A negative area squares away in the formula, and a zero divides by it.
        if any(not (a > 0 and s > 0) for a, s in curve):
            raise RecordError(f"pcb_fuse's trip_max_s {fuse['trip_max_s']!r}: every current "
                              f"and every time must be above 0")
        if not area > 0:
            raise RecordError(f"pcb_conductor's area_mm2 must be above 0: {cond['area_mm2']!r}")
    except RecordError as exc:
        return _error(gid, str(exc))
    except (TypeError, ValueError) as exc:
        return _error(gid, f"a record's number cannot be read: {exc}")
    if not curve:
        return _error(gid, "pcb_fuse's trip_max_s is empty: give the datasheet's guaranteed "
                           "[amperes, seconds] points")
    past = [s for _a, s in curve if s > ADIABATIC_MAX_S]
    if past:
        return _error(gid, f"pcb_fuse's trip time {past[0]:g} s is past {ADIABATIC_MAX_S:g} s, "
                           f"where IEC 60949's adiabatic limit no longer holds (heat leaves "
                           f"the conductor)")
    if not limit > start:
        return _error(gid, f"pcb_conductor's limit_c {limit:g} C is not above its start_c "
                           f"{start:g} C")
    cell_net = _net(cfp, cell["pin"])
    cond_nets = {_net(kfp, p) for p in pins}
    fuse_nets = sorted({p.net for p in ffp.pads if p.number})
    if len(cond_nets) != 1 or "" in cond_nets:
        return _error(gid, f"pcb_conductor's pins {pins} of {kfp.ref} sit on "
                           f"{sorted(cond_nets)}: they must carry one net, the one the fuse "
                           f"feeds")
    cond_net = cond_nets.pop()
    rows = [(adiabatic_s(a, area, start, limit) / s, a, s, adiabatic_s(a, area, start, limit))
            for a, s in curve]
    ratio, amps, t_max, t_ad = min(rows)
    ev = _evidence(ctx, "fuse_conductor", {
        "board": path, "formula": "t = (K S / I)^2 ln((limit + beta) / (start + beta)), "
                                  "K 226, beta 234.5 (IEC 60949, copper)",
        "records": {"cell": cell, "fuse": fuse, "conductor": cond},
        "cell_net": cell_net, "conductor_net": cond_net, "fuse_nets": fuse_nets,
        "points": [{"current_a": a, "trip_max_s": s, "adiabatic_s": t} for _r, a, s, t in rows]})
    if not ffp.fitted:
        return Verdict(gate=gid, passed=False, evidence=ev,
                       detail=f"{_not_fitted(ffp)}: nothing stands between the cell and "
                              f"{kfp.ref}")
    if sorted([cell_net, cond_net]) != fuse_nets:
        return Verdict(gate=gid, passed=False, evidence=ev,
                       detail=f"{ffp.ref} is not between the cell and {kfp.ref}: its pads sit on "
                              f"{', '.join(fuse_nets) or 'no net'}, the cell ({cfp.ref} pin "
                              f"{cell['pin']}) on {cell_net or 'no net'} and {kfp.ref} pin "
                              f"{'/'.join(pins)} on {cond_net}")
    detail = (f"{kfp.ref} pin {'/'.join(pins)} ({area:g} mm^2 copper, {start:g} -> {limit:g} C) "
              f"reaches {limit:g} C in {t_ad * 1000:.1f} ms at {amps:g} A; {ffp.ref} "
              f"{ffp.value} is guaranteed to trip by {t_max * 1000:.0f} ms there (IEC 60949 "
              f"adiabatic, copper; one conductor carries the whole fault, parallel contacts "
              f"get no credit; the records' sources are in the evidence)")
    return Verdict(gate=gid, passed=t_ad >= t_max, measured=round(t_ad, 4), limit=t_max,
                   comparator=">=", units="s", detail=detail, evidence=ev)


# --------------------------------------------------------------------------- #
# deep discharge
# --------------------------------------------------------------------------- #
@gate(
    id="pcb.discharge_cutoff",
    basis="sourced",
    title="Something cuts the cell off before it runs below its minimum voltage",
    claims=["pcb", "battery-safety", "deep-discharge"],
    tier=Tier.INSTANT,
    settles="deep-discharge cutoff",
    negative_control=NegativeControl(
        fixture="selftest/bad_boards.py:cutoff_unwired",
        note="the power baseline with R1's cell-side pad moved to a net of its own: the "
             "protector U2 then senses nothing the cell drives, and nothing cuts it off",
    ),
)
def discharge_cutoff(ctx: GateContext) -> Verdict:
    """The highest cutoff over the cell must be at or above the cell's ``min_v``
    (``pcb_cell``). The cutoffs are the cell's own PCM (``pcm_cutoff_min_v``, when
    not null) and each fitted part in ``pcb_cutoffs`` on the cell's board whose
    sense pad (``sense_pin``) is on the cell pin's net (``series_ref`` null), or
    reaches it through the one part ``series_ref`` names (the datasheet's series
    resistor on VDD): a fitted two-pad part, not the cell's own, with one pad on the
    cell's net and the other on the sense pad's. A sense pad on a net another pin
    of the cell sits on (its return) never counts. Each counts at its
    ``threshold_min_v``, the datasheet's lowest detection voltage. A cutoff that
    senses neither is named and not counted. With none, the gate fails: nothing
    cuts the cell off.

    (What slipped through, in review: the series part was inferred as any two-pad
    part on the cell's net, the cell's own connector among them, so a protector
    whose VDD sat on ground read as sensing the cell "through J2", and so did a
    record that named a VSS pin.)

    Only the cell's board is read: a cutoff on another board of a two-board design
    reads as absent (a false fail), and the FET path a cutoff drives and a cutoff
    in firmware are not checked (PACK.md)."""
    gid = "pcb.discharge_cutoff"
    try:
        cell = _record(ctx, "pcb_cell", CELL_FIELDS)
        cutoffs = ctx.param("pcb_cutoffs")
        if cell is None or cutoffs is None:
            absent = [k for k, v in (("pcb_cell", cell), ("pcb_cutoffs", cutoffs)) if v is None]
            return _skip(gid, f"no {', '.join(absent)}: the cell's minimum voltage, its PCM and "
                              f"the parts that cut it off are records a person enters (an "
                              f"empty pcb_cutoffs is a statement that there are none)")
        if not isinstance(cutoffs, list):
            raise RecordError("pcb_cutoffs is not a list of records")
        path, board = _board(ctx, cell)
        cfp = _part(board, cell, "pcb_cell", [cell["pin"]])
        min_v = float(cell["min_v"])
        pcm = cell["pcm_cutoff_min_v"]
        parts = []
        for i, rec in enumerate(cutoffs):
            label = f"pcb_cutoffs[{i}]"
            _checked(rec, label, CUTOFF_FIELDS)
            other, _board_of = _board(ctx, rec)
            if other != path:
                parts.append((rec, None, None, f"{rec['ref']} is on another board "
                                               f"({os.path.basename(other)})"))
                continue
            series = None
            if rec["series_ref"] is not None:
                series = next((f for f in board.footprints if f.ref == str(rec["series_ref"])),
                              None)
                if series is None:
                    raise RecordError(f"the {label} record is for another design: the board "
                                      f"has no {rec['series_ref']}")
            parts.append((rec, _part(board, rec, label, [rec["sense_pin"]]), series, ""))
    except RecordError as exc:
        return _error(gid, str(exc))
    except (TypeError, ValueError) as exc:
        return _error(gid, f"a record's number cannot be read: {exc}")
    cell_net = _net(cfp, cell["pin"])
    # The cell's return: every net another pin of the cell sits on.
    returns = {p.net for p in cfp.pads if p.number and p.net and p.net != cell_net}
    elements, ignored = [], []
    if pcm is not None:
        elements.append((float(pcm), f"the cell's PCM ({cfp.ref})"))
    for rec, fp, series, why in parts:
        if fp is None:
            ignored.append(why)
            continue
        sense = _net(fp, rec["sense_pin"])
        through = _through(series, cfp, cell_net, sense) if series is not None else ""
        if not fp.fitted:
            ignored.append(_not_fitted(fp))
        elif sense in returns:
            ignored.append(f"{fp.ref} pad {rec['sense_pin']} is on {sense}, the cell's return")
        elif series is None and sense and sense == cell_net:
            elements.append((float(rec["threshold_min_v"]), f"{fp.ref} {fp.value}"))
        elif series is not None and not through:
            elements.append((float(rec["threshold_min_v"]),
                             f"{fp.ref} {fp.value} (through {series.ref})"))
        elif through:
            ignored.append(f"{fp.ref} does not sense the cell through {series.ref}: {through}")
        else:
            ignored.append(f"{fp.ref} senses {sense or 'no net'}, not the cell's {cell_net} "
                           f"(series_ref is null)")
    ev = _evidence(ctx, "discharge_cutoff", {"board": path, "cell": cell,
                                             "cutoffs": cutoffs, "cell_net": cell_net,
                                             "min_v": min_v, "elements": elements,
                                             "not_counted": ignored})
    tail = f"; not counted: {'; '.join(ignored)}" if ignored else ""
    if not elements:
        return Verdict(gate=gid, passed=False, measured=None, limit=min_v, comparator=">=",
                       units="V", evidence=ev,
                       detail=f"nothing cuts the cell off: no PCM is recorded and no part on "
                              f"the board senses {cell_net}{tail}")
    v, who = max(elements)
    if v >= min_v:
        detail = f"{who} cuts the cell off at {v:g} V, at or above its {min_v:g} V minimum"
    elif len(elements) == 1 and pcm is not None:
        detail = (f"the cell's PCM opens at {v:g} V, below its {min_v:g} V minimum; no part "
                  f"on the board cuts the cell off")
    else:
        detail = f"the highest cutoff, {who}, opens at {v:g} V, below the cell's {min_v:g} V minimum"
    return Verdict(gate=gid, passed=v >= min_v, measured=v, limit=min_v, comparator=">=",
                   units="V", detail=detail + tail,
                   evidence=ev)
