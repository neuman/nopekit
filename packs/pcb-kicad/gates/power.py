# SPDX-License-Identifier: Apache-2.0
"""pcb-kicad — four rules for a board that charges or carries a cell.

    pcb.charge_temperature  tier 0  charging stops outside each cell's window, at the sensing network's tolerance corners
    pcb.charge_limits       tier 0  the charger's guaranteed maximum voltage and current against the cell's, and an overcharge stop beside it
    pcb.fault_current       tier 0  every conductor a cell can drive a fault through is protected across the fault range
    pcb.discharge_cutoff    tier 0  something opens the cell's path before any cell runs below its minimum

**Where each verdict comes from.** This pack's own code, on two kinds of input:
the boards' pad nets, read with the pack's reader (``_kicad.py``), and the
parts' datasheet facts, entered by a person as records (``pcb_batteries``,
``pcb_protectors``, ``pcb_conductors``, ``pcb_matings``; ``_power.py`` reads
them), each with its ``source``. The arithmetic is pad lookups and published
formulas: the NTC's R/T table or B equation, IEC 60949's adiabatic limit,
IPC-2221's heating curve, and IEC 60364-4-43's overload condition by stated
analogy. So the basis is sourced: the rules are only as true as the records,
and PACK.md says so.

**Chemistry picks rules, never numbers.** Every limit is the cell's own, from
its datasheet record: a LiFePO4 cell's 3.65 V and an HV cell's 4.45 V are both
the record's word. One wide bound refuses a typo (a per-cell voltage outside
1.5-4.6 V, a current outside 0.001-50 A) and moves no verdict on a real cell.

**Firmware is named, never counted.** A cutoff, a window or a charge voltage set
by firmware or a register is not on the copper: the rule skips, naming it,
unless something it can see fails.

**What slipped through** the three rules these replace (the review of the
branch's 0.2.0 cut): a charger with no safety timer read FAIL, though IEC
62368-1 M.4.2 measures voltage, current and temperature and asks for none; a
pack whose NTC is inside it read FAIL; a fixed 10 kOhm resistor where the NTC
belongs (the datasheets' own way to switch the input off) and an NTC of ten
times the value read PASS; nothing read the charge voltage, so a LiFePO4 cell on
a 4.2 V charger passed; low-side protector FETs read FAIL; the band between a
conductor's rating and its fuse's trip current was never judged; and the
known-good cutoff drove nothing at all and passed.
"""
from __future__ import annotations

import math
import os

from nopekit.gates import gate, GateContext
from nopekit.modelio import load_path as _load_path
from nopekit.models import NegativeControl, Tier, Verdict

_HERE = os.path.dirname(os.path.abspath(__file__))
K = _load_path(os.path.join(_HERE, "_kicad.py"))
P = _load_path(os.path.join(_HERE, "_power.py"))

RecordError, ReaderLimit = P.RecordError, P.ReaderLimit

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

#: IEC 60364-4-43 (2008) 433.1: the current that makes the protector operate
#: within its conventional time, I2, is at most 1.45 times the conductor's
#: continuous rating Iz. Written for building installations; applied here to a
#: handheld's conductors by stated analogy (PACK.md), as the one published bound
#: on the band between a conductor's rating and its protector's trip. Rejected:
#: 1.0 (no fuse trips at exactly its rating, so every sound design would read
#: FAIL); no bound (the branch's: a PTC that tripped at twice the wire's rating
#: passed).
OVERLOAD_FACTOR = 1.45

#: A per-cell voltage outside this band is a typo, not a cell: below the lowest
#: lithium chemistry's charge voltage (LTO, about 2.8 V) and above the highest
#: HV cell's (4.45 V), with room either side. It moves no verdict on a real
#: cell; it refuses "42.5" typed for "4.25". Rejected: a table per chemistry (it
#: read FAIL on HV cells at 4.35-4.45 V, which a 4.2 V table did not know).
CELL_V_PLAUSIBLE = (1.5, 4.6)

#: A charge current outside this band is a typo: a milliamp is below any
#: charger's regulated current, and 50 A is past any handheld's.
CHARGE_A_PLAUSIBLE = (0.001, 50.0)

LITHIUM = ("li-ion", "li-polymer", "lifepo4", "lto")
RECHARGEABLE = LITHIUM + ("nimh",)
PRIMARY = ("li-primary", "alkaline")
CHEMISTRIES = LITHIUM + ("nimh",) + PRIMARY
KINDS = ("charger", "protector", "pack-pcm", "bms", "gauge", "fuse", "ptc", "efuse",
         "blocking")
WHERE = ("board", "in-pack", "firmware")
FUNCTIONS = ("charge_v", "charge_a", "charge_temp", "overcharge_v", "overdischarge_v",
             "trip", "safety_timer")

#: KiCad's stock symbols for a fixed resistor (``Device.kicad_sym``, 9.0.9).
#: The datasheets' way to switch a charger's thermistor input off is a fixed
#: resistor where the NTC goes: MCP73833/4 (DS22005B 5.1.4, "place a standard
#: 10 kOhm resistor from THERM to VSS") and bq2407x ("10 kOhm resistor from TS
#: to VSS"). A negative test on purpose: a custom thermistor symbol need not
#: say "Thermistor". Rejected: a positive test for KiCad's NTC symbols (it read
#: FAIL on every project library's own thermistor).
FIXED_RESISTOR_SYMBOLS = ("Device:R", "Device:R_Small", "Device:R_US", "Device:R_Small_US")


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


# --------------------------------------------------------------------------- #
# the records, as each rule reads them
# --------------------------------------------------------------------------- #
class Battery:
    """One ``pcb_batteries`` record, bound to its connector."""

    def __init__(self, d, bid: str, rec: dict):
        label = f"pcb_batteries.{bid}"
        P.keys(rec, label, ("terminals", "chemistry", "series", "parallel", "cell", "source"),
               ("board", "ntc"))
        P.source(rec, label)
        self.id, self.label, self.rec, self.d = bid, label, rec, d
        self.chemistry = P.word(rec["chemistry"], f"{label}.chemistry", CHEMISTRIES)
        self.series = P.count(rec["series"], f"{label}.series")
        self.parallel = P.count(rec["parallel"], f"{label}.parallel")
        t = P.keys(rec["terminals"], f"{label}.terminals", ("ref", "value", "pos", "neg"),
                   ("therm", "taps"))
        self.pos = P.text(t["pos"], f"{label}.terminals.pos")
        self.neg = P.text(t["neg"], f"{label}.terminals.neg")
        self.therm = (None if t.get("therm") is None
                      else P.text(t["therm"], f"{label}.terminals.therm"))
        if self.series > 1:
            self.taps = P.pins(t.get("taps"), f"{label}.terminals.taps", self.series - 1)
        elif t.get("taps") is not None:
            raise RecordError(f"{label}.terminals.taps names taps of a one-cell battery")
        else:
            self.taps = []
        self.path = d.board(rec, label)
        need = [self.pos, self.neg, *([self.therm] if self.therm else []), *self.taps]
        self.fp = d.part(self.path, t["ref"], t["value"], f"{label}.terminals", need)
        self.cell = P.keys(rec["cell"], f"{label}.cell", (),
                           ("upper_charge_v", "discharge_v_min", "charge_a_max",
                            "charge_temp_c", "short_a", "internal_resistance_ohm"))
        self.ntc = None
        if rec.get("ntc") is not None:
            P.keys(rec["ntc"], f"{label}.ntc", ("r25_ohm", "r25_tol_pct"),
                   ("beta_k", "beta_pair_c", "beta_tol_pct", "rt_table_c_ohm"))
            self.ntc = P.NTC(rec["ntc"], f"{label}.ntc")

    def value(self, key: str, why: str):
        """The cell's ``key``, held to its kind; RecordError naming what reads it
        when the record lacks it."""
        if key not in self.cell:
            raise RecordError(f"{self.label}.cell has no {key}: {why}")
        where = f"{self.label}.cell.{key}"
        if key in ("charge_temp_c", "short_a", "internal_resistance_ohm"):
            return P.band(self.cell[key], where, positive=key != "charge_temp_c")
        return P.num(self.cell[key], where, gt=0.0)

    def net(self, pin):
        return self.d.net(self.path, self.fp, pin)

    @property
    def nodes(self) -> list:
        """The battery's nodes from its return up: neg, each tap, pos."""
        return [self.net(self.neg), *[self.net(t) for t in self.taps], self.net(self.pos)]


class Protector:
    """One ``pcb_protectors`` record: one element, every function it serves."""

    def __init__(self, d, pid: str, rec: dict, batteries: dict):
        label = f"pcb_protectors.{pid}"
        P.keys(rec, label, ("battery", "kind", "where", "functions", "source"),
               ("board", "ref", "value"))
        P.source(rec, label)
        self.id, self.label, self.rec, self.d = pid, label, rec, d
        bid = P.text(rec["battery"], f"{label}.battery")
        if bid not in batteries:
            raise RecordError(f"{label}.battery is {bid!r}, which pcb_batteries does not hold")
        self.battery = batteries[bid]
        self.kind = P.word(rec["kind"], f"{label}.kind", KINDS)
        self.where = P.word(rec["where"], f"{label}.where", WHERE)
        self.functions = P.keys(rec["functions"], f"{label}.functions", (), FUNCTIONS)
        if not [k for k in self.functions if k != "safety_timer"]:
            raise RecordError(f"{label}.functions states no function")
        if "safety_timer" in self.functions:
            P.flag(self.functions["safety_timer"], f"{label}.functions.safety_timer")
        self.path = self.fp = None
        if self.where == "in-pack":
            extra = [k for k in ("board", "ref", "value") if k in rec]
            if extra:
                raise RecordError(f"{label} is in the pack, on no board this pack reads: drop "
                                  f"{', '.join(extra)}")
        elif self.where == "board" or "ref" in rec:
            # On the board; or firmware that names the part it runs on, held to
            # it all the same.
            for key in ("ref", "value"):
                if key not in rec:
                    raise RecordError(f"{label} names a part and has no {key}")
            self.path = d.board(rec, label)
            self.fp = d.part(self.path, rec["ref"], rec["value"], label)

    @property
    def name(self) -> str:
        if self.fp is not None:
            return f"{self.fp.ref} {self.fp.value}"
        return f"{self.id} ({self.where})"

    def fn(self, key: str) -> str:
        return f"{self.label}.functions.{key}"

    def pin(self, pin, label: str):
        """The global net of this element's pad ``pin``, held to the pad being
        there."""
        self.d.part(self.path, self.fp.ref, self.fp.value, label, [pin])
        return self.d.net(self.path, self.fp, pin)


def _design(ctx, gid: str, *, conductors: bool = False):
    """``(Design, {id: Battery}, [Protector], conductor records)`` with every
    board a record names read and every mating joined, or a skip Verdict when
    there is no battery."""
    d = P.Design(ctx)
    bats = P.records(ctx, "pcb_batteries")
    if not bats:
        return _skip(gid, "no pcb_batteries: which cells the design carries, their datasheet "
                          "limits and their connectors are records a person enters")
    batteries = {bid: Battery(d, bid, rec) for bid, rec in sorted(bats.items())}
    protectors = [Protector(d, pid, rec, batteries)
                  for pid, rec in sorted((P.records(ctx, "pcb_protectors") or {}).items())]
    conds = P.records(ctx, "pcb_conductors") if conductors else None
    for cid, rec in sorted((conds or {}).items()):
        d.board(P.obj(rec, f"pcb_conductors.{cid}"), f"pcb_conductors.{cid}")
    d.matings(P.records(ctx, "pcb_matings"))
    return d, batteries, protectors, conds


def _charged_here(b: Battery, protectors: list) -> list:
    """The elements that charge ``b``: a charger, or anything stating a charge
    voltage or current."""
    return [p for p in protectors if p.battery is b
            and (p.kind == "charger" or "charge_v" in p.functions or "charge_a" in p.functions)]


def _resistor(d, path: str, ref, label: str) -> tuple[object, float]:
    """A resistor's footprint and its board value in ohms."""
    fp = d.part(path, ref, None, label)
    ohms = P.resistance(fp.value)
    if ohms is None or ohms <= 0:
        raise RecordError(f"{label}: {fp.ref}'s value {fp.value!r} is not a resistance this "
                          f"rule can read (give the bare value, such as 2k, 4k7 or 330)")
    return fp, ohms


def _others(d, path: str, declared: set, net, b: "Battery") -> list[str]:
    """The fitted parts on ``net`` that are not ``declared`` (refs on the board
    at ``path``), a mating's connector or the battery's own connector. A ref is
    held to its board: two boards of one design each have an R1."""
    return sorted({ref if where == path else f"{ref} ({os.path.basename(where)})"
                   for where, ref, _pad in d.pads_on(net)
                   if not (where == path and ref in declared)
                   and (where, ref) not in d.mating_refs
                   and not (where == b.path and ref == b.fp.ref)})


# --------------------------------------------------------------------------- #
# sensing and acting: the cell's own copper
# --------------------------------------------------------------------------- #
def _senses(p: Protector, b: Battery, sense: dict, per_cell: bool, label: str) -> str:
    """Why ``p`` does not sense ``b``, or "" when it does.

    One cell: the sense pin reaches the battery's positive net directly, or
    through the series chain ``through`` names (a fuse, then the datasheet's RC
    resistor), on this board or across a mating. A sense pin on the cell's
    return never counts. Two or more cells in series: only a per-cell element
    with a sense pin on every node, return to top, in order (``taps``), counts:
    a threshold across the whole pack bounds no single cell."""
    d = p.d
    avoid = {b.fp.ref}
    if b.series == 1:
        P.keys(sense, label, ("pin",), ("through",))
        net = p.pin(sense["pin"], label)
        through = [P.text(r, f"{label}.through") for r in sense.get("through") or []]
        if d.same(net, b.net(b.neg)):
            return f"{p.fp.ref} pad {sense['pin']} is on the cell's return"
        if not through:
            return ("" if d.same(net, b.net(b.pos)) else
                    f"{p.fp.ref} pad {sense['pin']} is on {d.label(net)}, not on the cell's "
                    f"{d.label(b.net(b.pos))} (through names no chain)")
        why = d.chain(p.path, b.net(b.pos), through, net, avoid)
        return (f"{p.fp.ref} does not sense the cell through {', '.join(through)}: {why}"
                if why else "")
    P.keys(sense, label, ("taps",))
    if not per_cell:
        return (f"{p.name} senses the pack, not each cell: a {b.series}-cell battery is "
                f"held per cell")
    taps = sense["taps"]
    if not isinstance(taps, list) or len(taps) != b.series + 1:
        raise RecordError(f"{label}.taps: give {b.series + 1} pins, one on each node of "
                          f"{b.id} from its return to its top")
    for i, (tap, node) in enumerate(zip(taps, b.nodes)):
        if isinstance(tap, dict):
            P.keys(tap, f"{label}.taps[{i}]", ("pin",), ("through",))
            pin = tap["pin"]
            through = [P.text(r, f"{label}.taps[{i}].through") for r in tap.get("through") or []]
        else:
            pin, through = P.text(tap, f"{label}.taps[{i}]"), []
        net = p.pin(pin, f"{label}.taps[{i}]")
        why = (d.chain(p.path, node, through, net, avoid) if through else
               "" if d.same(net, node) else f"pad {pin} is on {d.label(net)}")
        if why:
            return f"{p.name} does not sense node {i} of {b.id} ({d.label(node)}): {why}"
    return ""


def _acts(p: Protector, b: Battery, acts: dict, label: str) -> tuple[str, object, str]:
    """``(why p opens no switch in b's path, the switch's cell-side net, the
    switch's ref)``; why is "" when it does. The acting pin's net must be the
    switch's control pin's net, and the switch's two path pins must sit one on a
    battery terminal's net and one on a net some other fitted part reaches.
    (What slipped through: the branch's known-good protector drove nothing, its
    DO and CO on nets of their own, and the cutoff rule passed it.)"""
    d = p.d
    P.keys(acts, label, ("pin", "switch"))
    sw = P.keys(acts["switch"], f"{label}.switch", ("ref", "value", "control", "path"))
    out = p.pin(acts["pin"], label)
    path_pins = P.pins(sw["path"], f"{label}.switch.path", 2)
    fp = d.part(p.path, sw["ref"], sw["value"], f"{label}.switch",
                [sw["control"], *path_pins])
    if not fp.fitted:
        return P.not_fitted(fp), None, fp.ref
    control = d.net(p.path, fp, sw["control"])
    if not d.same(out, control):
        return (f"{p.fp.ref} pad {acts['pin']} is on {d.label(out)}, and {fp.ref}'s control "
                f"(pad {sw['control']}) on {d.label(control)}: it drives nothing", None, fp.ref)
    a, z = (d.net(p.path, fp, pin) for pin in path_pins)
    terminals = [b.net(b.pos), b.net(b.neg)]
    for here, there in ((a, z), (z, a)):
        if any(d.same(here, t) for t in terminals) and not d.same(here, there):
            if d.reached_by_another(there, {(p.path, fp.ref)}):
                return "", here, fp.ref
            return (f"{fp.ref}'s other path pin is on {d.label(there)}, which nothing else "
                    f"reaches", None, fp.ref)
    return (f"{fp.ref}'s path pins sit on {d.label(a)} and {d.label(z)}, neither the "
            f"cell's {d.label(terminals[0])} nor its {d.label(terminals[1])}", None, fp.ref)


def _guards(p: Protector, b: Battery, key: str, threshold: str) -> tuple[bool, str, object, str]:
    """``(counted, why not, the switch's cell-side net, its ref)`` for ``p``'s
    ``overcharge_v`` or ``overdischarge_v``: in the pack, on its datasheet's
    word; on the board only fitted, sensing the cell and opening a switch in its
    path. Firmware never counts."""
    f = P.keys(p.functions[key], p.fn(key), (threshold, "per_cell"), ("sense", "acts"))
    per_cell = P.flag(f["per_cell"], f"{p.fn(key)}.per_cell")
    if p.where == "in-pack":
        if b.series > 1 and not per_cell:
            return False, f"{p.name} holds the pack, not each cell", None, ""
        return True, "", None, ""
    if p.where == "firmware":
        return False, f"{p.name} is firmware, which this rule cannot see", None, ""
    for k in ("sense", "acts"):
        if k not in f:
            raise RecordError(f"{p.fn(key)} is on the board and has no {k}")
    if not p.fp.fitted:
        return False, P.not_fitted(p.fp), None, ""
    why = _senses(p, b, f["sense"], per_cell, f"{p.fn(key)}.sense")
    if why:
        return False, why, None, ""
    why, cell_side, switch = _acts(p, b, f["acts"], f"{p.fn(key)}.acts")
    return (not why), why, cell_side, switch


# --------------------------------------------------------------------------- #
# the charge temperature
# --------------------------------------------------------------------------- #
@gate(
    id="pcb.charge_temperature",
    basis="sourced",
    title="Charging stops outside each cell's charge temperature window, at the tolerance "
          "corners of the sensing network",
    claims=["pcb", "battery-safety", "charge-protection", "charge-temperature"],
    tier=Tier.INSTANT,
    settles="charge temperature headroom",
    negative_control=NegativeControl(
        fixture="selftest/bad_boards.py:ntc_swapped",
        note="the power baseline with TH1 swapped for a 100 kOhm NTC of the same B, on the "
             "board and in its record: a BOM substitution DRC, ERC and parity all accept, "
             "and the charger then charges the cell far past its 45 C",
    ),
)
def charge_temperature(ctx: GateContext) -> Verdict:
    """For each rechargeable cell something here charges, the temperatures at
    which charging is still allowed, at the worst tolerance corners of every
    element that stops it, against the cell's ``charge_temp_c``.

    An element counts when it stops charging (it is the charger, its ``acts``
    pin reaches a pin that stops one, or it is in the pack) and senses the cell:
    a board NTC on its sense pin (the pin's net holds a pad of the NTC the record
    names, at the record's value; the NTC's far pad on a net another part
    reaches; no other pad of the element on the sense net), or the pack's NTC on
    the battery's thermistor pin. A part on the sense network the record does
    not name is refused: a resistor in parallel moves every trip. Where a
    schematic sits beside the board, an NTC whose symbol is a fixed resistor
    fails: that is the datasheets' own way to switch the input off.

    The trip resistances come from the datasheet's [min, max]: a current
    source's ``V / I`` at its corners, or a divider's ``R_pu f / (1 - f)``, R_pu
    read off the board. The network is ``(R_ntc || R_par) + R_ser``, every
    resistor at its tolerance corners; the NTC is its R/T table when the record
    gives one, else its B equation, at R25's and B's corners. At each corner the
    trip temperature is found by bisection; the lowest temperature any corner
    still charges at, and the highest, are the worst edges, and the most
    protective element sets each edge. measured is the least headroom, ``min(
    T_cold - lo, hi - T_hot)``, in K (a difference, so a claim stated in K is
    compared with it), against 0. An in-pack element counts at its datasheet
    window; firmware is named and skipped. The NTC's thermal coupling to the
    cell is not judged (PACK.md)."""
    gid = "pcb.charge_temperature"
    try:
        got = _design(ctx, gid)
        if isinstance(got, Verdict):
            return got
        d, batteries, protectors, _ = got
        judged = [b for b in batteries.values()
                  if b.chemistry in RECHARGEABLE and _charged_here(b, protectors)]
        if not judged:
            return _skip(gid, "nothing in pcb_protectors charges a rechargeable cell in "
                              "pcb_batteries: a primary cell, or a cell charged elsewhere, has "
                              "no charge temperature here")
        rows = [_battery_window(ctx, d, b, protectors) for b in judged]
    except ReaderLimit as exc:
        return _skip(gid, str(exc))
    except RecordError as exc:
        return _error(gid, str(exc))
    faults = [f"{b.id}: {why}" for b, row in zip(judged, rows) for why in row["faults"]]
    blind = [f"{b.id}: {', '.join(row['firmware'])}" for b, row in zip(judged, rows)
             if not row["faults"] and row["headroom"] is None and row["firmware"]]
    ev = _evidence(ctx, "charge_temperature", {
        "boards": sorted(d.boards),
        "batteries": {b.id: {k: v for k, v in row.items() if k != "line"}
                      for b, row in zip(judged, rows)}})
    if faults:
        return Verdict(gate=gid, passed=False, measured=None, limit=0.0, comparator=">=",
                       units="K", evidence=ev, detail="; ".join(faults))
    seen = [r["headroom"] for r in rows if r["headroom"] is not None]
    value = round(min(seen), 2) if seen else None
    # A cell held only by firmware skips, unless another cell here fails: a
    # skip beside a fail would hide the fail. (What slipped through this rule's
    # first cut: the skip was returned before the other cells were judged, so
    # a cell charged past its window read Skipped beside a firmware-only one.)
    if blind and (value is None or value >= 0.0):
        return Verdict(gate=gid, passed=False, skipped=True, evidence=ev,
                       skip_reason=f"only firmware stops charging outside the window ("
                                   f"{'; '.join(blind)}): this rule cannot see it; settle it "
                                   f"with a physical claim at the window's edges")
    detail = "; ".join(r["line"] for r in rows if r["line"])
    if blind:
        detail += f"; firmware only, not judged: {'; '.join(blind)}"
    return Verdict(gate=gid, passed=value >= 0.0, measured=value, limit=0.0,
                   comparator=">=", units="K", detail=detail, evidence=ev)


def _battery_window(ctx, d, b: Battery, protectors: list) -> dict:
    """One battery's window: its worst edges and the elements behind them, or
    why it has none."""
    lo, hi = b.value("charge_temp_c", "the widest window the cell's datasheet allows any "
                                      "charge in, in C")
    row = {"window_c": [lo, hi], "faults": [], "firmware": [], "elements": [],
           "headroom": None, "line": ""}
    cold, hot, flags = [], [], []
    for p in (p for p in protectors if p.battery is b and "charge_temp" in p.functions):
        if p.where == "firmware":
            row["firmware"].append(p.name)
            continue
        if p.where == "in-pack":
            label = p.fn("charge_temp")
            ct = P.keys(p.functions["charge_temp"], label, ("window_c",))
            w = P.keys(ct["window_c"], f"{label}.window_c", ("cold_c", "hot_c"))
            c = P.band(w["cold_c"], f"{label}.window_c.cold_c", positive=False)
            h = P.band(w["hot_c"], f"{label}.window_c.hot_c", positive=False)
            cold.append((c[0], f"{p.name}, on its datasheet's word"))
            hot.append((h[1], f"{p.name}, on its datasheet's word"))
            row["elements"].append({"element": p.id, "cold_c": c[0], "hot_c": h[1]})
            continue
        got = _board_ntc(ctx, d, p, b)
        if isinstance(got, str):
            row["faults"].append(got)
            continue
        cold.append((got["cold_c"], got["who"]))
        hot.append((got["hot_c"], got["who"]))
        flags += got["flags"]
        row["elements"].append({"element": p.id, **{k: v for k, v in got.items()
                                                    if k != "who"}})
    if row["faults"]:
        return row
    if not cold:
        if not row["firmware"]:
            row["faults"].append("nothing stops charging outside the cell's window: no "
                                 "element senses its temperature")
        return row
    t_cold, who_cold = max(cold)
    t_hot, who_hot = min(hot)
    row["headroom"] = min(t_cold - lo, hi - t_hot)
    side = "cold" if t_cold - lo <= hi - t_hot else "hot"
    edges = (f"both edges {who_cold}" if who_cold == who_hot
             else f"cold edge {who_cold}, hot edge {who_hot}")
    note = f"; {'; '.join(dict.fromkeys(flags))}" if flags else ""
    blind = f"; firmware not judged: {', '.join(row['firmware'])}" if row["firmware"] else ""
    row["line"] = (f"{b.id}: charging allowed {t_cold:.2f}..{t_hot:.2f} C at the corners "
                   f"({edges}) against the cell's {lo:g}..{hi:g} C: "
                   f"{round(row['headroom'], 2):.2f} K of headroom on the {side} side"
                   f"{note}{blind}")
    return row


def _board_ntc(ctx, d, p: Protector, b: Battery) -> dict | str:
    """A board element's worst charge edges, or why it does not count."""
    label = p.fn("charge_temp")
    ct = P.keys(p.functions["charge_temp"], label, ("sense", "ntc"), ("network", "acts"))
    sense = P.keys(ct["sense"], f"{label}.sense", ("pin", "kind"),
                   ("i_a", "cold_v", "hot_v", "pullup_ref", "pullup_tol_pct", "cold_frac",
                    "hot_frac"))
    kind = P.word(sense["kind"], f"{label}.sense.kind", ("current", "ratio"))
    net_rec = P.keys(ct.get("network") or {}, f"{label}.network", (),
                     ("series_ref", "parallel_ref", "tol_pct", "others"))
    if not p.fp.fitted:
        return P.not_fitted(p.fp)
    # It stops charging: it is the charger, or its acts pin reaches a pin that
    # stops one.
    if p.kind != "charger":
        if "acts" not in ct:
            raise RecordError(f"{label}: {p.name} is not the charger, so say what it stops "
                              f"(acts: {{pin, to: {{ref, pin}}}})")
        acts = P.keys(ct["acts"], f"{label}.acts", ("pin", "to"))
        to = P.keys(acts["to"], f"{label}.acts.to", ("ref", "pin"))
        out = p.pin(acts["pin"], f"{label}.acts")
        target = d.part(p.path, to["ref"], None, f"{label}.acts.to", [to["pin"]])
        if not target.fitted:
            return P.not_fitted(target)
        if not d.same(out, d.net(p.path, target, to["pin"])):
            return (f"{p.fp.ref} pad {acts['pin']} is on {d.label(out)}, not on {target.ref} "
                    f"pad {to['pin']}: it stops nothing")
    s_net = p.pin(sense["pin"], f"{label}.sense")
    own = [pad.number for pad in p.fp.pads if pad.number and pad.number != str(sense["pin"])
           and d.same(d.net(p.path, p.fp, pad.number), s_net)]
    if own:
        return (f"{p.fp.ref}'s sense pin (pad {sense['pin']}) is tied to its own pad(s) "
                f"{', '.join(own)}, which turns the input off")
    tol = None
    if net_rec.get("series_ref") or net_rec.get("parallel_ref"):
        if "tol_pct" not in net_rec:
            raise RecordError(f"{label}.network has no tol_pct: the series and parallel "
                              f"resistors' tolerance")
        tol = P.pct(net_rec["tol_pct"], f"{label}.network.tol_pct")
    declared = {p.fp.ref}
    others = net_rec.get("others") or []
    if not isinstance(others, list):
        raise RecordError(f"{label}.network.others: give [{{ref, value, why}}]")
    for i, o in enumerate(others):
        P.keys(o, f"{label}.network.others[{i}]", ("ref", "value", "why"))
        P.text(o["why"], f"{label}.network.others[{i}].why")
        declared.add(d.part(p.path, o["ref"], o["value"], f"{label}.network.others[{i}]").ref)
    ser = par = None
    node = s_net
    if net_rec.get("series_ref"):
        sfp, ser = _resistor(d, p.path, net_rec["series_ref"], f"{label}.network.series_ref")
        declared.add(sfp.ref)
        if not sfp.fitted:
            return P.not_fitted(sfp)
        nets = [d.net(p.path, sfp, pad.number) for pad in sfp.pads if pad.number]
        far = [n for n in nets if not d.same(n, s_net)]
        if len(nets) != 2 or len(far) != 1 or far[0] is None:
            return (f"{sfp.ref} is not in series from the sense pin: its pads sit on "
                    + ", ".join(d.label(n) for n in nets))
        node = far[0]
    ntc_rec = P.obj(ct["ntc"], f"{label}.ntc")
    defeat = ""
    if ntc_rec.get("battery_therm"):
        P.keys(ntc_rec, f"{label}.ntc", ("battery_therm",))
        if b.therm is None:
            raise RecordError(f"{label}.ntc reads the pack's NTC, and {b.label}.terminals "
                              f"names no therm pin")
        if b.ntc is None:
            raise RecordError(f"{label}.ntc reads the pack's NTC, and {b.label} has no ntc "
                              f"record (its R25, B or table, and their tolerances)")
        if net_rec.get("parallel_ref"):
            raise RecordError(f"{label}.network.parallel_ref: a resistor across the pack's NTC "
                              f"is not read by this rule")
        ntc = b.ntc
        if not d.same(node, b.net(b.therm)):
            return (f"{p.fp.ref}'s sense pin reaches {d.label(node)}, not the pack's "
                    f"thermistor pin ({b.fp.ref} pad {b.therm}, on {d.label(b.net(b.therm))})")
        who = f"{p.name} on the pack's NTC"
    else:
        P.keys(ntc_rec, f"{label}.ntc", ("ref", "value", "r25_ohm", "r25_tol_pct"),
               ("beta_k", "beta_pair_c", "beta_tol_pct", "rt_table_c_ohm"))
        ntc = P.NTC(ntc_rec, f"{label}.ntc")
        nfp = d.part(p.path, ntc_rec["ref"], ntc_rec["value"], f"{label}.ntc")
        declared.add(nfp.ref)
        if not nfp.fitted:
            return P.not_fitted(nfp)
        nets = [d.net(p.path, nfp, pad.number) for pad in nfp.pads if pad.number]
        on = [n for n in nets if d.same(n, node)]
        far = [n for n in nets if not d.same(n, node)]
        if not on:
            return (f"{p.fp.ref}'s sense network ({d.label(node)}) reaches no pad of "
                    f"{nfp.ref}")
        if not far or far[0] is None:
            return f"{nfp.ref}'s other pad is on no net, so it senses nothing"
        mine = {(p.path, nfp.ref)}
        if net_rec.get("parallel_ref"):
            pfp, par = _resistor(d, p.path, net_rec["parallel_ref"],
                                 f"{label}.network.parallel_ref")
            declared.add(pfp.ref)
            mine.add((p.path, pfp.ref))
            if not pfp.fitted:
                return P.not_fitted(pfp)
            pnets = [d.net(p.path, pfp, pad.number) for pad in pfp.pads if pad.number]
            if not (len(pnets) == 2 and any(d.same(n, node) for n in pnets)
                    and any(d.same(n, far[0]) for n in pnets)):
                return f"{pfp.ref} is not across {nfp.ref}"
        if not d.reached_by_another(far[0], mine):
            return (f"{nfp.ref}'s other pad is on {d.label(far[0])}, which no other part "
                    f"reaches, so it senses nothing")
        defeat = _fixed_resistor(ctx, p.path, nfp.ref)
        if defeat.startswith("FAIL:"):
            return defeat[len("FAIL:"):]
        who = f"{p.name} on {nfp.ref} {nfp.value}"
    if kind == "current":
        for k in ("pullup_ref", "pullup_tol_pct", "cold_frac", "hot_frac"):
            if k in sense:
                raise RecordError(f"{label}.sense: a current source takes no {k}")
        i = P.band(sense.get("i_a"), f"{label}.sense.i_a")
        cv = P.band(sense.get("cold_v"), f"{label}.sense.cold_v")
        hv = P.band(sense.get("hot_v"), f"{label}.sense.hot_v")
        if sum(cv) <= sum(hv):
            raise RecordError(f"{label}.sense: cold_v {list(cv)} is not above hot_v "
                              f"{list(hv)} (an NTC reads high when cold): swapped?")
        r_cold = (cv[0] / i[1], cv[1] / i[0])
        r_hot = (hv[0] / i[1], hv[1] / i[0])
    else:
        for k in ("i_a", "cold_v", "hot_v"):
            if k in sense:
                raise RecordError(f"{label}.sense: a divider takes no {k}")
        for k in ("pullup_ref", "pullup_tol_pct", "cold_frac", "hot_frac"):
            if k not in sense:
                raise RecordError(f"{label}.sense: a divider has no {k}")
        pu, r_pu = _resistor(d, p.path, sense["pullup_ref"], f"{label}.sense.pullup_ref")
        if not pu.fitted:
            return P.not_fitted(pu)
        if not any(d.same(d.net(p.path, pu, pad.number), s_net) for pad in pu.pads):
            return f"{pu.ref} has no pad on the sense pin's {d.label(s_net)}"
        t_pu = P.pct(sense["pullup_tol_pct"], f"{label}.sense.pullup_tol_pct")
        fr = {}
        for k in ("cold_frac", "hot_frac"):
            fr[k] = P.band(sense[k], f"{label}.sense.{k}")
            if fr[k][1] >= 1.0:
                raise RecordError(f"{label}.sense.{k} is {sense[k]!r}: a fraction of the "
                                  f"divider's reference, below 1")
        if sum(fr["cold_frac"]) <= sum(fr["hot_frac"]):
            raise RecordError(f"{label}.sense: cold_frac is not above hot_frac (an NTC low "
                              f"in a divider reads high when cold): swapped?")

        def trip(f):
            return (r_pu * (1 - t_pu) * f[0] / (1 - f[0]),
                    r_pu * (1 + t_pu) * f[1] / (1 - f[1]))
        r_cold, r_hot = trip(fr["cold_frac"]), trip(fr["hot_frac"])
        declared.add(pu.ref)
    # Nothing the record does not name loads the sense network: a resistor
    # across the NTC moves every trip, and its value is in no record.
    for net in dict.fromkeys([s_net, node]):
        extra = _others(d, p.path, declared, net, b)
        if extra:
            raise RecordError(f"{label}: {', '.join(extra)} on the sense network "
                              f"({d.label(net)}) is not in the record; name each in network "
                              f"(series_ref, parallel_ref), or in network.others with why it "
                              f"carries no current there")
    return _edges(ntc, ser, par, tol, r_cold, r_hot, who, defeat)


def _edges(ntc, ser, par, tol, r_cold, r_hot, who: str, defeat: str) -> dict | str:
    """The lowest temperature any corner still charges at, and the highest:
    every corner of R25 and B, of each network resistor and of each trip
    resistance."""
    signs = (-1, 1)
    sers = [None] if ser is None else [ser * (1 + s * tol) for s in signs]
    pars = [None] if par is None else [par * (1 + s * tol) for s in signs]
    lo_c, hi_c = ntc.span_c
    edge = {}
    for side, trips in (("cold", r_cold), ("hot", r_hot)):
        found = []
        for rs in signs:
            for bs in signs:
                for rser in sers:
                    for rpar in pars:
                        def f(t, rs=rs, bs=bs, rser=rser, rpar=rpar):
                            return P.network(ntc.r(t, rs, bs), rpar, rser)
                        for target in trips:
                            t = P.crossing(f, target, lo_c, hi_c)
                            if t is None:
                                if ntc.table is not None:
                                    raise RecordError(
                                        f"{who}: its {side} trip ({target:.0f} ohm) lies "
                                        f"outside the NTC's R/T table, {lo_c:g}..{hi_c:g} C: "
                                        f"give rows that bracket it")
                                if side == "hot" and f(hi_c) > target:
                                    return (f"{who}: at one corner its hot cutoff never trips "
                                            f"below {hi_c:g} C, so it never stops charging a "
                                            f"hot cell")
                                if side == "cold" and f(lo_c) < target:
                                    return (f"{who}: at one corner its cold cutoff never trips "
                                            f"above {lo_c:g} C, so it never stops charging a "
                                            f"cold cell")
                                # It never charges past the range on this side.
                                t = hi_c if side == "cold" else lo_c
                            found.append(t)
        edge[side] = min(found) if side == "cold" else max(found)
    out = {"model": ntc.model, "cold_c": round(edge["cold"], 4),
           "hot_c": round(edge["hot"], 4), "flags": []}
    for side in ("cold", "hot"):
        if ntc.extrapolated(edge[side]):
            # B rises with temperature, so below its pair the B equation puts a
            # trip warmer than the part trips: on the cold side, the side that
            # protects less. Above it, the error falls the other way.
            below = edge[side] < ntc.pair[0]
            out["flags"].append(
                f"the {side} edge, {edge[side]:.2f} C, is {'below' if below else 'above'} the "
                f"B pair {ntc.pair[0]:g}/{ntc.pair[1]:g} C, where the B equation extrapolates"
                + (" and reads a cold trip warm (the maker's R/T table would not)"
                   if below else ""))
    if defeat:
        out["flags"].append(defeat)
    out["who"] = f"{who}, {ntc.model}"
    return out


def _fixed_resistor(ctx, board_path: str, ref: str) -> str:
    """``"FAIL:<why>"`` when the schematic beside the board places ``ref`` on a
    fixed resistor's symbol; a note when there is no schematic to read, or it
    places no ``ref``; "" when the symbol is something else."""
    sch = board_path[:-len(".kicad_pcb")] + ".kicad_sch"
    if not os.path.isfile(sch):
        return (f"no schematic beside {os.path.basename(board_path)}, so {ref}'s symbol "
                f"was not read")
    base = os.path.dirname(os.path.abspath(sch))
    try:
        sheets = [sch] + [os.path.join(base, rel) for rel in K.sheet_files(sch)]
        for path in sheets:
            tree = ctx.load_file(path, loader=K.load_tree)
            for sym in K.children(tree, "symbol"):
                found = next((str(q[2]) for q in K.children(sym, "property")
                              if len(q) >= 3 and q[1] == "Reference"), "")
                if found != ref:
                    continue
                lib_id = str(K.value(sym, "lib_id", default=""))
                if lib_id in FIXED_RESISTOR_SYMBOLS:
                    return (f"FAIL:{ref} is a fixed resistor ({lib_id}) on the schematic "
                            f"where the record names an NTC: the datasheets' way to switch "
                            f"the thermistor input off")
                return ""
    except (K.KicadFormatError, OSError, UnicodeDecodeError) as exc:
        raise RecordError(f"the schematic beside {os.path.basename(board_path)} cannot be "
                          f"read: {exc}")
    return f"the schematic beside {os.path.basename(board_path)} places no {ref}"


# --------------------------------------------------------------------------- #
# the charge voltage and current
# --------------------------------------------------------------------------- #
@gate(
    id="pcb.charge_limits",
    basis="sourced",
    title="The charger never exceeds each cell's datasheet charge voltage or current, and "
          "something independent of the charger stops an overcharge",
    claims=["pcb", "battery-safety", "charge-limits", "charge-voltage", "charge-current"],
    tier=Tier.INSTANT,
    settles="charge voltage and current within the cell's limits",
    negative_control=NegativeControl(
        fixture="selftest/bad_boards.py:lfp_cell",
        note="the power baseline's cell record changed to a LiFePO4 cell of the same size "
             "(3.65 V upper charge voltage) on the same 4.2 V charger: the charger's 4.232 V "
             "is 16% past it",
    ),
)
def charge_limits(ctx: GateContext) -> Verdict:
    """For each lithium cell and each charger that charges it, the worse of two
    ratios, against 1.0: the charger's guaranteed maximum charge voltage per
    cell over the cell's upper limit charging voltage (IEC 62133-2 A.3.2: the
    maker's, with its tolerance), and its guaranteed maximum charge current over
    the cell's maximum charge current times the cells in parallel.

    The voltage is ``v_reg_v``'s max, bound to the charger's board value (a
    variant letter stales it), or a divider's ``Vfb (1 + Rtop / Rbot)`` at its
    corners, the resistors read off the board; divided by the cells in series
    unless the charger regulates each cell. The current is ``a_max``, or the
    datasheet's ``k / R`` at the constant's max and the resistor's least, R read
    off the board. And IEC 62368-1 M.4.2.2 judges the charge under a single
    fault: at least one ``overcharge_v`` element independent of every charger
    (another part, or in the pack) must sense the cell and open its path, per
    cell for two or more in series; with none the rule fails with no value, and
    with only firmware there it skips, naming it. Its threshold is printed, not
    judged. A charger set by firmware is named and skipped; NiMH has no constant-voltage phase and is not judged; a charger on
    a primary cell fails."""
    gid = "pcb.charge_limits"
    try:
        got = _design(ctx, gid)
        if isinstance(got, Verdict):
            return got
        d, batteries, protectors, _ = got
        rows, faults, blind, notes = [], [], [], []
        for b in batteries.values():
            chargers = _charged_here(b, protectors)
            if not chargers:
                continue
            if b.chemistry in PRIMARY:
                faults.append(f"{b.id}: {', '.join(c.name for c in chargers)} charges a "
                              f"{b.chemistry} cell, which must never be charged")
                continue
            if b.chemistry not in LITHIUM:
                notes.append(f"{b.id} ({b.chemistry}) has no constant-voltage phase: not "
                             f"judged")
                continue
            ulcv = b.value("upper_charge_v", "the cell's upper limit charging voltage, "
                                             "with its tolerance")
            i_cell = b.value("charge_a_max", "the cell's maximum charge current")
            _plausible_v(ulcv, f"{b.label}.cell.upper_charge_v")
            _plausible_a(i_cell, f"{b.label}.cell.charge_a_max")
            i_pack = i_cell * b.parallel
            for c in chargers:
                got_c = _charger(d, c, b)
                if isinstance(got_c, str):
                    blind.append(f"{b.id}: {got_c}")
                    continue
                v_cell, i_max, how = got_c
                rows.append((max(v_cell / ulcv, i_max / i_pack), b, c,
                             f"{b.id}: {c.name} holds {v_cell:.4g} V of the cell's {ulcv:g} V "
                             f"({v_cell / ulcv:.4f}) and {i_max:.4g} A of its {i_pack:g} A "
                             f"({i_max / i_pack:.4f}){how}"))
            ov = _single_fault(b, protectors, chargers)
            if ov.startswith("FAIL:"):
                faults.append(f"{b.id}: {ov[len('FAIL:'):]}")
            elif ov.startswith("FIRMWARE:"):
                blind.append(f"{b.id}: the overcharge stop {ov[len('FIRMWARE:'):]}")
            else:
                notes.append(f"{b.id}'s overcharge under a single fault: {ov}")
        if not rows and not faults and not blind:
            return _skip(gid, "nothing in pcb_protectors charges a lithium cell in "
                              "pcb_batteries" + (f" ({'; '.join(notes)})" if notes else ""))
    except ReaderLimit as exc:
        return _skip(gid, str(exc))
    except RecordError as exc:
        return _error(gid, str(exc))
    ev = _evidence(ctx, "charge_limits", {
        "boards": sorted(d.boards),
        "chargers": [{"battery": b.id, "charger": c.id, "ratio": r, "said": line}
                     for r, b, c, line in rows],
        "faults": faults, "firmware": blind, "notes": notes})
    said = "; ".join([r[3] for r in rows] + notes)
    if faults:
        return Verdict(gate=gid, passed=False, measured=None, limit=1.0, comparator="<=",
                       units="ratio", evidence=ev,
                       detail="; ".join(faults) + (f"; {said}" if said else ""))
    value = round(max(r[0] for r in rows), 4) if rows else None
    if blind and (value is None or value <= 1.0):
        return Verdict(gate=gid, passed=False, skipped=True, evidence=ev,
                       skip_reason=f"set by firmware, which this rule cannot see: "
                                   f"{'; '.join(blind)}")
    return Verdict(gate=gid, passed=value <= 1.0, measured=value, limit=1.0, comparator="<=",
                   units="ratio", detail=said, evidence=ev)


def _plausible_v(v: float, label: str) -> None:
    if not CELL_V_PLAUSIBLE[0] <= v <= CELL_V_PLAUSIBLE[1]:
        raise RecordError(f"{label} is {v:g} V per cell, outside {CELL_V_PLAUSIBLE[0]:g}-"
                          f"{CELL_V_PLAUSIBLE[1]:g} V, where no lithium cell charges: a typo?")


def _plausible_a(a: float, label: str) -> None:
    if not CHARGE_A_PLAUSIBLE[0] <= a <= CHARGE_A_PLAUSIBLE[1]:
        raise RecordError(f"{label} is {a:g} A, outside {CHARGE_A_PLAUSIBLE[0]:g}-"
                          f"{CHARGE_A_PLAUSIBLE[1]:g} A: a typo?")


def _charger(d, c: Protector, b: Battery) -> tuple | str:
    """``(volts per cell, amperes, words)`` a charger can reach at its worst
    corner, or the words naming what firmware sets."""
    for key, what in (("charge_v", "voltage"), ("charge_a", "current")):
        if key not in c.functions:
            raise RecordError(f"{c.label} charges {b.id} and has no functions.{key}: its "
                              f"guaranteed maximum charge {what}")
    cv = P.keys(c.functions["charge_v"], c.fn("charge_v"), ("per_cell",),
                ("v_reg_v", "divider", "set_by"))
    ca = P.keys(c.functions["charge_a"], c.fn("charge_a"), (), ("a_max", "prog", "set_by"))
    firmware = [what for what, f, key in (("charge voltage", cv, "charge_v"),
                                          ("charge current", ca, "charge_a"))
                if f.get("set_by") is not None
                and P.word(f["set_by"], f"{c.fn(key)}.set_by", ("firmware",))]
    if firmware:
        return f"{c.name}'s {' and '.join(firmware)}"
    per_cell = P.flag(cv["per_cell"], f"{c.fn('charge_v')}.per_cell")
    if ("v_reg_v" in cv) == ("divider" in cv):
        raise RecordError(f"{c.fn('charge_v')}: give v_reg_v [min, max] or a divider, one")
    how = ""
    if "v_reg_v" in cv:
        v_max = P.band(cv["v_reg_v"], f"{c.fn('charge_v')}.v_reg_v")[1]
    else:
        if c.fp is None:
            raise RecordError(f"{c.fn('charge_v')}.divider: the divider is on the board, and "
                              f"{c.label} is not")
        label = f"{c.fn('charge_v')}.divider"
        dv = P.keys(cv["divider"], label, ("vfb_v", "top_ref", "bottom_ref", "tol_pct"))
        vfb = P.band(dv["vfb_v"], f"{label}.vfb_v")
        tol = P.pct(dv["tol_pct"], f"{label}.tol_pct")
        top, r_top = _resistor(d, c.path, dv["top_ref"], f"{label}.top_ref")
        bot, r_bot = _resistor(d, c.path, dv["bottom_ref"], f"{label}.bottom_ref")
        v_max = vfb[1] * (1 + r_top * (1 + tol) / (r_bot * (1 - tol)))
        how += f"; voltage set by {top.ref} {top.value} over {bot.ref} {bot.value}"
    v_cell = v_max if per_cell else v_max / b.series
    _plausible_v(v_cell, f"{c.fn('charge_v')}, per cell,")
    if ("a_max" in ca) == ("prog" in ca):
        raise RecordError(f"{c.fn('charge_a')}: give a_max or prog, one")
    if "a_max" in ca:
        i_max = P.num(ca["a_max"], f"{c.fn('charge_a')}.a_max", gt=0.0)
    else:
        if c.fp is None:
            raise RecordError(f"{c.fn('charge_a')}.prog: the resistor is on the board, and "
                              f"{c.label} is not")
        label = f"{c.fn('charge_a')}.prog"
        pg = P.keys(ca["prog"], label, ("ref", "k_v", "r_tol_pct"))
        k_v = P.band(pg["k_v"], f"{label}.k_v")
        tol = P.pct(pg["r_tol_pct"], f"{label}.r_tol_pct")
        rp, r = _resistor(d, c.path, pg["ref"], f"{label}.ref")
        i_max = k_v[1] / (r * (1 - tol))
        how += f"; current set by {rp.ref} {rp.value}"
    _plausible_a(i_max, f"{c.fn('charge_a')}")
    return v_cell, i_max, how


def _single_fault(b: Battery, protectors: list, chargers: list) -> str:
    """The overcharge elements independent of every charger, as words;
    ``"FIRMWARE:<names>"`` when only firmware stands there (named and skipped,
    as every rule here treats firmware); or ``"FAIL:<why>"`` when nothing does,
    or when an element on the board or in the pack does not count."""
    charger_refs = {(c.path, c.fp.ref) for c in chargers if c.fp is not None}
    counted, why_not, firmware = [], [], []
    for p in protectors:
        if p.battery is not b or "overcharge_v" not in p.functions:
            continue
        v_max = P.num(P.obj(p.functions["overcharge_v"], p.fn("overcharge_v")).get("v_max"),
                      f"{p.fn('overcharge_v')}.v_max", gt=0.0)
        if p in chargers or (p.fp is not None and (p.path, p.fp.ref) in charger_refs):
            why_not.append(f"{p.name} is the charger itself")
            continue
        if p.where == "firmware":
            firmware.append(p.name)
            continue
        ok, why, _side, _sw = _guards(p, b, "overcharge_v", "v_max")
        if ok:
            counted.append(f"{p.name} at {v_max:g} V")
        else:
            why_not.append(why)
    if counted:
        return ", ".join(counted) + " (thresholds printed, not judged)"
    if firmware and not why_not:
        return "FIRMWARE:" + ", ".join(firmware)
    why_not += [f"{name} is firmware, which this rule cannot see" for name in firmware]
    tail = f" (not counted: {'; '.join(why_not)})" if why_not else ""
    return ("FAIL:nothing independent of the charger stops an overcharge: no overcharge_v "
            "element senses the cell and opens its path" + tail)


# --------------------------------------------------------------------------- #
# the fault current
# --------------------------------------------------------------------------- #
@gate(
    id="pcb.fault_current",
    basis="sourced",
    title="Every conductor a cell can drive a fault through is protected across the whole "
          "fault range",
    claims=["pcb", "battery-safety", "overcurrent-protection"],
    tier=Tier.INSTANT,
    settles="fault protection of a conductor",
    negative_control=NegativeControl(
        fixture="selftest/bad_boards.py:fuse_trips_late",
        note="the power baseline's PTC swapped for one of twice the hold and trip current, "
             "on the board and in its record: between 1.45 A and 2 A the wire heats past "
             "its rating and nothing trips",
    ),
)
def fault_current(ctx: GateContext) -> Verdict:
    """For each conductor in ``pcb_conductors`` and the protectors its record
    names, the least of three ratios, against 1.0:

    (b) a fuse's or PTC's interrupt rating over the most current the cell can
    drive into a fault; (c) IEC 60364-4-43's overload condition, ``1.45 Iz /
    I2``, Iz the conductor's continuous rating (a track's from IPC-2221 at its
    stated rise, on its narrowest stretch) and I2 the protector's guaranteed
    trip current; (d) at every guaranteed trip point the cell can reach (its
    least fault current at or above it), IEC 60949's adiabatic time over the
    trip time. A conductor takes its best protector for (c) and for (d), and
    its worst fuse for (b). With no trip point the cell can reach, the rule
    fails with no value: nothing guarantees a trip at that current.

    A fuse, PTC or eFuse sits with one path pin on the battery terminal's net
    (positive, or negative when low-side) and the other on the protected net,
    and a FET protector's switch likewise; an in-pack PCM is counted on its
    datasheet's word. A part the record does not declare ``ahead`` that taps
    the terminal net before the element is refused, naming it. One conductor
    carries the whole fault: contacts in parallel get no credit."""
    gid = "pcb.fault_current"
    try:
        got = _design(ctx, gid, conductors=True)
        if isinstance(got, Verdict):
            return got
        d, batteries, protectors, conds = got
        if not conds:
            return _skip(gid, "no pcb_conductors: which wires, flex conductors, contacts and "
                              "tracks a cell can drive a fault through, and their ratings, are "
                              "records a person enters")
        by_id = {p.id: p for p in protectors}
        rows, faults = [], []
        for cid, rec in sorted(conds.items()):
            got_c = _conductor(ctx, d, cid, rec, batteries, by_id)
            if isinstance(got_c, str):
                faults.append(got_c)
            else:
                rows.append(got_c)
    except ReaderLimit as exc:
        return _skip(gid, str(exc))
    except RecordError as exc:
        return _error(gid, str(exc))
    ev = _evidence(ctx, "fault_current", {
        "boards": sorted(d.boards),
        "formulas": {"adiabatic": "t = (K S / I)^2 ln((limit + beta) / (start + beta)), K 226, "
                                  "beta 234.5 (IEC 60949, copper)",
                     "overload": "I2 <= 1.45 Iz (IEC 60364-4-43 433.1, by analogy)"},
        "conductors": [r[2] for r in rows], "faults": faults})
    if faults:
        return Verdict(gate=gid, passed=False, measured=None, limit=1.0, comparator=">=",
                       units="ratio", evidence=ev, detail="; ".join(faults))
    value = round(min(r[0] for r in rows), 4)
    return Verdict(gate=gid, passed=value >= 1.0, measured=value, limit=1.0, comparator=">=",
                   units="ratio", evidence=ev,
                   detail="; ".join(r[1] for r in rows)
                   + " (one conductor carries the whole fault; parallel contacts get no credit)")


def _fault_range(b: Battery) -> tuple[float, float, str]:
    """``(least, most)`` amperes ``b`` can drive into a dead short, and how."""
    if "short_a" in b.cell:
        lo, hi = b.value("short_a", "")
        return lo * b.parallel, hi * b.parallel, "the record's short-circuit current"
    if "internal_resistance_ohm" in b.cell:
        r = b.value("internal_resistance_ohm", "")
        v_lo = b.value("discharge_v_min", "the least voltage a fault current is driven by")
        v_hi = b.value("upper_charge_v", "the most voltage a fault current is driven by")
        return (v_lo * b.parallel / r[1], v_hi * b.parallel / r[0],
                "the cell's voltage over its internal resistance")
    raise RecordError(f"{b.label}.cell has neither short_a nor internal_resistance_ohm: the "
                      f"current the cell drives into a fault, as [least, most]")


def _conductor(ctx, d, cid: str, rec: dict, batteries: dict, by_id: dict) -> tuple | str:
    """``(least ratio, words, evidence)`` for one conductor, or the words of a
    fail with no value."""
    label = f"pcb_conductors.{cid}"
    P.keys(rec, label, ("battery", "kind", "start_c", "limit_c", "protected_by", "source"),
           ("board", "ref", "value", "pins", "net", "area_mm2", "continuous_a",
            "continuous_rise_c"))
    P.source(rec, label)
    bid = P.text(rec["battery"], f"{label}.battery")
    if bid not in batteries:
        raise RecordError(f"{label}.battery is {bid!r}, which pcb_batteries does not hold")
    b = batteries[bid]
    kind = P.word(rec["kind"], f"{label}.kind", ("wire", "ffc", "contact", "track"))
    start = P.num(rec["start_c"], f"{label}.start_c")
    limit = P.num(rec["limit_c"], f"{label}.limit_c")
    if not limit > start:
        raise RecordError(f"{label}.limit_c {limit:g} C is not above its start_c {start:g} C")
    path = d.board(rec, label)
    if kind == "track":
        for k in ("ref", "value", "pins", "area_mm2", "continuous_a"):
            if k in rec:
                raise RecordError(f"{label} is a track: it takes net and continuous_rise_c, "
                                  f"not {k}")
        name = P.text(rec.get("net"), f"{label}.net")
        rise = P.num(rec.get("continuous_rise_c"), f"{label}.continuous_rise_c", gt=0.0)
        tracks = [t for t in d.boards[path].tracks if t.net == name]
        if not tracks:
            raise RecordError(f"{label} is for another design: no track on {name} in "
                              f"{os.path.basename(path)}")
        cap, area, where = min(_track_capacity(ctx, t, rise) for t in tracks)
        net = d.node(path, name)
        what = f"{name}'s {where}"
    else:
        for k in ("net", "continuous_rise_c"):
            if k in rec:
                raise RecordError(f"{label} is a {kind}: it takes ref, value, pins, area_mm2 "
                                  f"and continuous_a, not {k}")
        for k in ("ref", "value", "pins", "area_mm2", "continuous_a"):
            if k not in rec:
                raise RecordError(f"{label} has no {k}")
        pins = P.pins(rec["pins"], f"{label}.pins")
        fp = d.part(path, rec["ref"], rec["value"], label, pins)
        nets = list(dict.fromkeys(d.net(path, fp, p) for p in pins))
        if len(nets) != 1 or nets[0] is None:
            raise RecordError(f"{label}: {fp.ref}'s pins {pins} sit on "
                              f"{', '.join(d.label(n) for n in nets)}: give the pins of one net")
        net = nets[0]
        area = P.num(rec["area_mm2"], f"{label}.area_mm2", gt=0.0)
        cap = P.num(rec["continuous_a"], f"{label}.continuous_a", gt=0.0)
        what = f"{fp.ref} pin {'/'.join(pins)} ({kind}, {area:g} mm^2)"
    ids = rec["protected_by"]
    if not isinstance(ids, list) or not ids:
        raise RecordError(f"{label}.protected_by: give the ids of the protectors that guard it")
    i_lo, i_hi, how = _fault_range(b)
    interrupt, overload, short, unreached = [], [], [], []
    for pid in ids:
        if pid not in by_id:
            raise RecordError(f"{label}.protected_by names {pid!r}, which pcb_protectors "
                              f"does not hold")
        p = by_id[pid]
        if p.battery is not b:
            raise RecordError(f"{label}.protected_by names {pid}, which guards "
                              f"{p.battery.id}, not {b.id}")
        if "trip" not in p.functions:
            raise RecordError(f"{label}.protected_by names {pid}, which states no trip")
        t = _trip(d, p, b, net, what)
        if isinstance(t, str):
            return f"{cid}: {t}"
        if t["interrupt_a"] is not None:
            interrupt.append((t["interrupt_a"] / i_hi, f"{p.name} interrupts "
                              f"{t['interrupt_a']:g} A of {i_hi:.4g} A"))
        overload.append((OVERLOAD_FACTOR * cap / t["trip_a"],
                         f"{p.name} trips by {t['trip_a']:g} A, against 1.45 x {cap:.4g} A"))
        points = [(adiabatic_s(a, area, start, limit) / s, a, s) for a, s in t["curve"]
                  if a <= i_lo]
        if points:
            r, a, s = min(points)
            short.append((r, f"{p.name} trips by {s * 1000:g} ms at {a:g} A, where the "
                             f"conductor takes {adiabatic_s(a, area, start, limit) * 1000:.1f} "
                             f"ms to reach {limit:g} C"))
        else:
            unreached.append(f"{p.name}'s least guaranteed trip point is above {i_lo:.4g} A")
    if not short:
        return (f"{cid}: nothing guarantees a trip at the current {b.id} can drive "
                f"({i_lo:.4g} A at the least, {how}): {'; '.join(unreached)}")
    parts = ([min(interrupt)] if interrupt else []) + [max(overload), max(short)]
    value = min(r for r, _w in parts)
    line = (f"{cid}: {what} from {b.id} ({i_lo:.4g}..{i_hi:.4g} A, {how}): "
            + "; ".join(f"{w} ({r:.4f})" for r, w in parts))
    return value, line, {"conductor": cid, "fault_a": [i_lo, i_hi], "least": value,
                         "checks": [{"ratio": r, "said": w} for r, w in parts]}


def _track_capacity(ctx, t, rise: float) -> tuple[float, float, str]:
    """``(IPC-2221 amperes at rise, cross-section mm^2, where)`` of one track,
    in the copper weight the project states (``pcb.net_width``'s defaults
    otherwise)."""
    outer = t.layer in K.OUTER_LAYERS
    oz = float(ctx.param("pcb_copper_oz_outer" if outer else "pcb_copper_oz_inner",
                         1.0 if outer else 0.5))
    thick_mil = oz * K.MIL_PER_OZ
    area_mil2 = (t.width_mm / K.MM_PER_MIL) * thick_mil
    k = K.K_EXTERNAL if outer else K.K_INTERNAL
    amps = k * rise ** K.EXP_DT * area_mil2 ** K.EXP_AREA
    return amps, t.width_mm * thick_mil * K.MM_PER_MIL, f"{t.width_mm:g} mm track on {t.layer}"


def _trip(d, p: Protector, b: Battery, cond_net, what: str) -> dict | str:
    """A protector's trip with its place on the copper held: the parsed trip, or
    why it does not guard the conductor."""
    label = p.fn("trip")
    t = P.keys(p.functions["trip"], label, ("kind", "terminal", "trip_a", "trip_max_s"),
               ("path_pins", "switch", "hold_a", "interrupt_a", "ahead"))
    kind = P.word(t["kind"], f"{label}.kind", ("fuse", "ptc", "fet", "efuse", "pcm"))
    terminal = P.word(t["terminal"], f"{label}.terminal", ("pos", "neg"))
    trip_a = P.num(t["trip_a"], f"{label}.trip_a", gt=0.0)
    if "hold_a" in t and P.num(t["hold_a"], f"{label}.hold_a", gt=0.0) > trip_a:
        raise RecordError(f"{label}.hold_a is above its trip_a")
    curve = t["trip_max_s"]
    if not isinstance(curve, list) or not curve:
        raise RecordError(f"{label}.trip_max_s: give the datasheet's guaranteed "
                          f"[[amperes, maximum seconds], ...]")
    pts = []
    for i, row in enumerate(curve):
        if not isinstance(row, list) or len(row) != 2:
            raise RecordError(f"{label}.trip_max_s[{i}] is {row!r}: give [amperes, seconds]")
        a = P.num(row[0], f"{label}.trip_max_s[{i}][0]", gt=0.0)
        s = P.num(row[1], f"{label}.trip_max_s[{i}][1]", gt=0.0)
        if s > ADIABATIC_MAX_S:
            raise RecordError(f"{label}: a trip time of {s:g} s is past {ADIABATIC_MAX_S:g} s, "
                              f"where IEC 60949's adiabatic limit no longer holds (heat leaves "
                              f"the conductor)")
        pts.append((a, s))
    interrupt = None
    if kind in ("fuse", "ptc"):
        if "interrupt_a" not in t:
            raise RecordError(f"{label} is a {kind} and has no interrupt_a: the most current "
                              f"it is rated to break")
        interrupt = P.num(t["interrupt_a"], f"{label}.interrupt_a", gt=0.0)
    out = {"kind": kind, "trip_a": trip_a, "curve": pts, "interrupt_a": interrupt}
    if kind == "pcm":
        if p.where != "in-pack":
            raise RecordError(f"{label} is a pcm, which is in the pack (where: in-pack)")
        return out
    if p.where != "board":
        raise RecordError(f"{label} is a {kind}, and {p.label} is not on the board")
    if kind == "fet":
        if "path_pins" in t or "switch" not in t:
            raise RecordError(f"{label} is a fet: give its switch {{ref, value, path}}, not "
                              f"path_pins")
        sw = P.keys(t["switch"], f"{label}.switch", ("ref", "value", "path"))
        pins = P.pins(sw["path"], f"{label}.switch.path", 2)
        fp = d.part(p.path, sw["ref"], sw["value"], f"{label}.switch", pins)
    else:
        if "switch" in t or "path_pins" not in t:
            raise RecordError(f"{label} is a {kind}: give its path_pins [p, q], not a switch")
        pins = P.pins(t["path_pins"], f"{label}.path_pins", 2)
        fp = d.part(p.path, p.fp.ref, p.fp.value, label, pins)
    if not fp.fitted:
        return f"{P.not_fitted(fp)}: nothing stands between {b.id} and {what}"
    term = b.net(b.pos if terminal == "pos" else b.neg)
    a, z = (d.net(p.path, fp, pin) for pin in pins)
    if d.same(a, term) and not d.same(z, term):
        far = z
    elif d.same(z, term) and not d.same(a, term):
        far = a
    else:
        return (f"{fp.ref} is not on {b.id}'s {terminal} terminal: its path pins sit on "
                f"{d.label(a)} and {d.label(z)}, the terminal on {d.label(term)}")
    if terminal == "pos" and not d.same(cond_net, far):
        return (f"{fp.ref} is not between {b.id} and {what}: it feeds {d.label(far)}, and "
                f"the conductor is on {d.label(cond_net)}")
    if terminal == "neg" and d.same(cond_net, term):
        return f"{what} is on {b.id}'s return ahead of {fp.ref}, which does not guard it"
    ahead = t.get("ahead") or []
    if not isinstance(ahead, list):
        raise RecordError(f"{label}.ahead: give [{{ref, value, why}}]")
    declared = {fp.ref}
    for i, rec in enumerate(ahead):
        P.keys(rec, f"{label}.ahead[{i}]", ("ref", "value", "why"))
        P.text(rec["why"], f"{label}.ahead[{i}].why")
        declared.add(d.part(p.path, rec["ref"], rec["value"], f"{label}.ahead[{i}]").ref)
    taps = _others(d, p.path, declared, term, b)
    if taps:
        raise RecordError(f"{label}: {', '.join(taps)} on {b.id}'s {terminal} net "
                          f"({d.label(term)}) taps the cell ahead of {fp.ref}; declare each "
                          f"in ahead with why, or move it behind the protector")
    return out


# --------------------------------------------------------------------------- #
# deep discharge
# --------------------------------------------------------------------------- #
@gate(
    id="pcb.discharge_cutoff",
    basis="sourced",
    title="Something opens the cell's path before any cell runs below its minimum voltage",
    claims=["pcb", "battery-safety", "deep-discharge"],
    tier=Tier.INSTANT,
    settles="deep-discharge cutoff",
    negative_control=NegativeControl(
        fixture="selftest/bad_boards.py:cutoff_drives_nothing",
        note="the power baseline with U2's DO pad moved to a net of its own: U2 still "
             "senses the cell and opens nothing, so only the pack's PCM is left, below "
             "the cell's minimum",
    ),
)
def discharge_cutoff(ctx: GateContext) -> Verdict:
    """For each lithium cell, the highest per-cell threshold among the elements
    that cut it off, against the cell's ``discharge_v_min``, on the worst cell.

    An ``overdischarge_v`` element counts when it is in the pack (on its
    datasheet's word), or when it is fitted, senses the cell (its sense pin on
    the cell's positive net, directly or through the chain ``through`` names, on
    this board or across a mating; per cell, on every node, for two or more in
    series) and opens a switch in the cell's path (its acting pin on the
    switch's control net, the switch's path pins one on a battery terminal and
    one on a net another part reaches). A cutoff that drives nothing protects
    nothing. Each counts at ``v_min``, its datasheet's least detection voltage.
    With none, the rule fails with no value; with only firmware, it skips. The
    parts left on the cell side of the switch, which keep drawing after the
    cutoff, are listed. Primary and NiMH cells are named and not judged."""
    gid = "pcb.discharge_cutoff"
    try:
        got = _design(ctx, gid)
        if isinstance(got, Verdict):
            return got
        d, batteries, protectors, _ = got
        judged = [b for b in batteries.values() if b.chemistry in LITHIUM]
        notes = [f"{b.id} ({b.chemistry}) not judged" for b in batteries.values()
                 if b.chemistry not in LITHIUM]
        if not judged:
            return _skip(gid, "no lithium cell in pcb_batteries: " + "; ".join(notes))
        rows = []
        for b in judged:
            min_v = b.value("discharge_v_min", "the cell's discharge cut-off voltage")
            counted, ignored, firmware, drain = [], [], [], set()
            for p in protectors:
                if p.battery is not b or "overdischarge_v" not in p.functions:
                    continue
                f = P.obj(p.functions["overdischarge_v"], p.fn("overdischarge_v"))
                v = P.num(f.get("v_min"), f"{p.fn('overdischarge_v')}.v_min", gt=0.0)
                if p.where == "firmware":
                    firmware.append(p.name)
                    continue
                ok, why, cell_side, switch = _guards(p, b, "overdischarge_v", "v_min")
                if not ok:
                    ignored.append(why)
                    continue
                counted.append((v, f"{p.id} (in the pack, on its datasheet's word)"
                                if p.where == "in-pack" else p.name))
                if cell_side is not None:
                    drain |= set(_others(d, p.path, {p.fp.ref, switch}, cell_side, b))
            rows.append({"battery": b, "min_v": min_v, "counted": counted,
                         "ignored": ignored, "firmware": firmware, "drain": sorted(drain)})
    except ReaderLimit as exc:
        return _skip(gid, str(exc))
    except RecordError as exc:
        return _error(gid, str(exc))
    ev = _evidence(ctx, "discharge_cutoff", {
        "boards": sorted(d.boards),
        "batteries": [{"battery": r["battery"].id, "min_v": r["min_v"],
                       "counted": r["counted"], "not_counted": r["ignored"],
                       "firmware": r["firmware"], "cell_side": r["drain"]} for r in rows],
        "notes": notes})
    lines, fails, blind, worst = [], [], [], None
    for r in rows:
        b, min_v = r["battery"], r["min_v"]
        tail = f"; not counted: {'; '.join(r['ignored'])}" if r["ignored"] else ""
        tail += f"; firmware not judged: {', '.join(r['firmware'])}" if r["firmware"] else ""
        if not r["counted"]:
            if r["firmware"] and not r["ignored"]:
                blind.append(f"{b.id}: {', '.join(r['firmware'])}")
            else:
                fails.append(f"{b.id}: nothing cuts the cell off{tail}")
            continue
        v, who = max(r["counted"])
        drain = (f"; still on the cell side of the switch: {', '.join(r['drain'])}"
                 if r["drain"] else "")
        lines.append(f"{b.id}: {who} cuts it off at {v:g} V per cell, against its "
                     f"{min_v:g} V minimum{drain}{tail}")
        if worst is None or v - min_v < worst[0] - worst[1]:
            worst = (v, min_v)
    tail = f"; {'; '.join(notes)}" if notes else ""
    if fails:
        return Verdict(gate=gid, passed=False, measured=None,
                       limit=min(r["min_v"] for r in rows), comparator=">=", units="V",
                       evidence=ev, detail="; ".join(fails + lines) + tail)
    if blind and (worst is None or worst[0] >= worst[1]):
        return Verdict(gate=gid, passed=False, skipped=True, evidence=ev,
                       skip_reason=f"only firmware cuts the cell off ({'; '.join(blind)}): "
                                   f"this rule cannot see it")
    return Verdict(gate=gid, passed=worst[0] >= worst[1], measured=worst[0], limit=worst[1],
                   comparator=">=", units="V", detail="; ".join(lines) + tail, evidence=ev)
