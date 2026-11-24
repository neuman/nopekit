# SPDX-License-Identifier: Apache-2.0
"""pcb-kicad — four rules for a board that charges or carries a cell, and one for
the ways its connectors can meet wrong.

    pcb.charge_temperature  tier 0  charging stops outside each cell's window, at the sensing network's tolerance corners
    pcb.charge_limits       tier 0  the charger's guaranteed maximum voltage and current against the cell's, and an overcharge stop beside it
    pcb.fault_current       tier 0  every conductor a cell can drive a fault through is protected across the fault range
    pcb.discharge_cutoff    tier 0  something opens the cell's path before any cell runs below its minimum
    pcb.mismate             tier 0  no stated mis-mating of a connector, cable or cell joins a source to its return or to another source

``pcb.mismate`` is model-only: its hazards are this pack's own rule on the
modes a person states, with no outside source for which wrong fits count (its
own docstring says what it reads and what it cannot see).

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
the record's word. One wide bound refuses a typo in the cell's record (a
per-cell voltage outside 1.5-4.6 V, a current outside 0.001-50 A) and moves no
verdict on a real cell. A charger's typed voltage and current are refused only
below it, and a value read off the board never: a charger above it is a real
overcharge, and it fails at its ratio.

**Firmware is named, never counted.** A cutoff, a window or a charge voltage set
by firmware or a register is not on the copper: the rule skips, naming it,
unless something it can see fails. Each function is judged on its own, so a
firmware current never hides a voltage the board sets.

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
import re

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
#:
#: Held whole on the cell's record only. On a charger it holds only its floor,
#: and only on a typed number (``v_reg_v``'s max, ``a_max``): a typo low there
#: is the one typo that reads as a pass. A charger above the band is a real
#: overcharge, and a value read off the board is the design, so each is judged
#: at its ratio. (What slipped through this rule's first cut: the whole band
#: was held on the charger's worst corner per cell, so a 2S charger on a 1S
#: cell, 8.464 V, and a PROG of "10" typed for "10k", 111 A, read "a typo?", an
#: error that told the person to fix a record that was right, not a fail at
#: 1.99 and 33. Rejected: the floor on a value read off the board, where a low
#: value is the board's own and charges no cell too hard.)
CELL_V_PLAUSIBLE = (1.5, 4.6)

#: A charge current outside this band is a typo: a milliamp is below any
#: charger's regulated current, and 50 A is past any handheld's. Held as
#: :data:`CELL_V_PLAUSIBLE` is: whole on the cell, its floor on a typed
#: ``a_max``.
CHARGE_A_PLAUSIBLE = (0.001, 50.0)

LITHIUM = ("li-ion", "li-polymer", "lifepo4", "lto")
RECHARGEABLE = LITHIUM + ("nimh",)
PRIMARY = ("li-primary", "alkaline")
CHEMISTRIES = LITHIUM + ("nimh",) + PRIMARY
KINDS = ("charger", "protector", "pack-pcm", "bms", "gauge", "fuse", "ptc", "efuse",
         "blocking")
WHERE = ("board", "in-pack", "firmware")
#: ``reverse_block`` is read by ``pcb.mismate`` only: a series element that
#: blocks a reversed cell (a diode, an ideal-diode FET), placed as a trip is.
FUNCTIONS = ("charge_v", "charge_a", "charge_temp", "overcharge_v", "overdischarge_v",
             "trip", "reverse_block", "safety_timer")

#: The trips ``pcb.mismate`` counts as limiting a reversed cell's current: a
#: fuse and a PTC open on current whichever way it flows. Rejected: protector
#: FETs (their controller runs from the cell, which is now backwards, and a
#: body diode conducts) and an eFuse (some block reverse current and some do
#: not: one that does is stated as a reverse_block).
REVERSE_LIMITERS = ("fuse", "ptc")

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
    judged. A charge voltage or current set by firmware is named and not
    judged, and the charger's other function still is: the rule skips only
    when nothing it can see fails. NiMH has no constant-voltage phase and is
    not judged; a charger on a primary cell fails."""
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
                v_cell, i_max, how, firmware = _charger(d, c, b)
                if firmware:
                    blind.append(f"{b.id}: {firmware}")
                seen = []
                if v_cell is not None:
                    seen.append((v_cell / ulcv, f"{v_cell:.4g} V of the cell's {ulcv:g} V "
                                                f"({v_cell / ulcv:.4f})"))
                if i_max is not None:
                    whose = "its" if seen else "the cell's"
                    seen.append((i_max / i_pack, f"{i_max:.4g} A of {whose} {i_pack:g} A "
                                                 f"({i_max / i_pack:.4f})"))
                if seen:
                    rows.append((max(r for r, _w in seen), b, c,
                                 f"{b.id}: {c.name} holds "
                                 f"{' and '.join(w for _r, w in seen)}{how}"))
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
    said = "; ".join([r[3] for r in rows] + notes
                     + ([f"set by firmware, not judged: {'; '.join(blind)}"] if blind else []))
    if faults:
        return Verdict(gate=gid, passed=False, measured=None, limit=1.0, comparator="<=",
                       units="ratio", evidence=ev,
                       detail="; ".join(faults) + (f"; {said}" if said else ""))
    value = round(max(r[0] for r in rows), 4) if rows else None
    # Firmware skips only beside nothing that fails: a skip would hide a fail
    # the rule can see. (What slipped through this rule's first cut: a charger
    # with a firmware current returned before its voltage was read, so a
    # 4.232 V charger on a 3.65 V LiFePO4 cell read Skipped, not Failing.)
    if blind and (value is None or value <= 1.0):
        return Verdict(gate=gid, passed=False, skipped=True, evidence=ev,
                       skip_reason=f"set by firmware, which this rule cannot see: "
                                   f"{'; '.join(blind)}")
    return Verdict(gate=gid, passed=value <= 1.0, measured=value, limit=1.0, comparator="<=",
                   units="ratio", detail=said, evidence=ev)


def _plausible_v(v: float, label: str, *, floor_only: bool = False) -> None:
    lo, hi = CELL_V_PLAUSIBLE
    if floor_only and v < lo:
        raise RecordError(f"{label} is {v:g} V, below {lo:g} V, where no lithium cell "
                          f"charges: a typo?")
    if not floor_only and not lo <= v <= hi:
        raise RecordError(f"{label} is {v:g} V per cell, outside {lo:g}-{hi:g} V, where no "
                          f"lithium cell charges: a typo?")


def _plausible_a(a: float, label: str, *, floor_only: bool = False) -> None:
    lo, hi = CHARGE_A_PLAUSIBLE
    if floor_only and a < lo:
        raise RecordError(f"{label} is {a:g} A, below {lo:g} A: a typo?")
    if not floor_only and not lo <= a <= hi:
        raise RecordError(f"{label} is {a:g} A, outside {lo:g}-{hi:g} A: a typo?")


def _set_by_firmware(f: dict, label: str, values: tuple) -> bool:
    """Whether a charge function says firmware sets it. One that says so and
    also gives a value is refused: which of the two the charger holds is not
    on the copper, and the value would be read or dropped without a word."""
    if f.get("set_by") is None:
        return False
    P.word(f["set_by"], f"{label}.set_by", ("firmware",))
    given = [k for k in values if k in f]
    if given:
        raise RecordError(f"{label} is set_by firmware and gives {', '.join(given)}: give "
                          f"one")
    return True


def _charger(d, c: Protector, b: Battery) -> tuple:
    """``(volts per cell, amperes, words, firmware)`` a charger can reach at its
    worst corner. Each function is read on its own: one set by firmware is None
    and named in ``firmware`` ("" when none is), and the other is still read
    off the record and the board. (What slipped through this rule's first cut:
    a firmware ``set_by`` on either function returned before the other was
    read, so a voltage the board set was never judged.)"""
    for key, what in (("charge_v", "voltage"), ("charge_a", "current")):
        if key not in c.functions:
            raise RecordError(f"{c.label} charges {b.id} and has no functions.{key}: its "
                              f"guaranteed maximum charge {what}")
    cv = P.keys(c.functions["charge_v"], c.fn("charge_v"), ("per_cell",),
                ("v_reg_v", "divider", "set_by"))
    ca = P.keys(c.functions["charge_a"], c.fn("charge_a"), (), ("a_max", "prog", "set_by"))
    per_cell = P.flag(cv["per_cell"], f"{c.fn('charge_v')}.per_cell")
    firmware, how, v_cell, i_max = [], "", None, None
    if _set_by_firmware(cv, c.fn("charge_v"), ("v_reg_v", "divider")):
        firmware.append("charge voltage")
    else:
        v_cell, said = _charge_v(d, c, cv, per_cell, b)
        how += said
    if _set_by_firmware(ca, c.fn("charge_a"), ("a_max", "prog")):
        firmware.append("charge current")
    else:
        i_max, said = _charge_a(d, c, ca)
        how += said
    return v_cell, i_max, how, (f"{c.name}'s {' and '.join(firmware)}" if firmware else "")


def _charge_v(d, c: Protector, cv: dict, per_cell: bool, b: Battery) -> tuple[float, str]:
    """``(volts per cell, words)``: the charger's guaranteed maximum
    regulation voltage, typed or read off its divider."""
    if ("v_reg_v" in cv) == ("divider" in cv):
        raise RecordError(f"{c.fn('charge_v')}: give v_reg_v [min, max] or a divider, one")
    how = ""
    if "v_reg_v" in cv:
        v_max = P.band(cv["v_reg_v"], f"{c.fn('charge_v')}.v_reg_v")[1]
        _plausible_v(v_max, f"{c.fn('charge_v')}.v_reg_v's max", floor_only=True)
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
    return (v_max if per_cell else v_max / b.series), how


def _charge_a(d, c: Protector, ca: dict) -> tuple[float, str]:
    """``(amperes, words)``: the charger's guaranteed maximum charge current,
    typed or read off its PROG resistor."""
    if ("a_max" in ca) == ("prog" in ca):
        raise RecordError(f"{c.fn('charge_a')}: give a_max or prog, one")
    how = ""
    if "a_max" in ca:
        i_max = P.num(ca["a_max"], f"{c.fn('charge_a')}.a_max", gt=0.0)
        _plausible_a(i_max, f"{c.fn('charge_a')}.a_max", floor_only=True)
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
    return i_max, how


def _single_fault(b: Battery, protectors: list, chargers: list) -> str:
    """The overcharge elements independent of every charger, as words;
    ``"FIRMWARE:<names>"`` when only firmware stands there (named and skipped,
    as every rule here treats firmware); or ``"FAIL:<why>"`` when nothing does,
    or when an element that could have counted does not (not fitted, not
    sensing the cell, opening nothing, or holding the pack, not each cell).

    The charger's own overcharge stop is named and moves nothing: it is never
    independent of the charger, so it could never count. (What slipped through
    this rule's first cut: it was listed with the elements that failed to
    count, and a firmware-only stop that skipped read FAIL once a person
    recorded the charger's own battery OVP beside it.)"""
    charger_refs = {(c.path, c.fp.ref) for c in chargers if c.fp is not None}
    counted, why_not, firmware, itself = [], [], [], []
    for p in protectors:
        if p.battery is not b or "overcharge_v" not in p.functions:
            continue
        v_max = P.num(P.obj(p.functions["overcharge_v"], p.fn("overcharge_v")).get("v_max"),
                      f"{p.fn('overcharge_v')}.v_max", gt=0.0)
        if p in chargers or (p.fp is not None and (p.path, p.fp.ref) in charger_refs):
            itself.append(f"{p.name} is the charger itself")
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
        return ("FIRMWARE:" + ", ".join(firmware)
                + (f" (not counted: {'; '.join(itself)})" if itself else ""))
    why_not = itself + why_not + [f"{name} is firmware, which this rule cannot see"
                                  for name in firmware]
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


def _trip_place(d, p: Protector, b: Battery, what: str) -> dict | str:
    """A protector's trip parsed, with its place on the copper: the parsed trip
    (and, on a board, its footprint, its path pins, the terminal's net and the
    net it feeds, ``far``, through ``far_pin``), or why it guards nothing. An
    in-pack PCM is counted on its datasheet's word and has no place here.
    ``pcb.fault_current`` holds the result to a conductor; ``pcb.mismate``
    reads which nets lie beyond it."""
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
        far, far_pin = z, pins[1]
    elif d.same(z, term) and not d.same(a, term):
        far, far_pin = a, pins[0]
    else:
        return (f"{fp.ref} is not on {b.id}'s {terminal} terminal: its path pins sit on "
                f"{d.label(a)} and {d.label(z)}, the terminal on {d.label(term)}")
    out.update(record=t, label=label, fp=fp, terminal=terminal, term=term, far=far,
               far_pin=far_pin)
    return out


def _ahead(d, p: Protector, b: Battery, place: dict) -> list[str]:
    """The parts on the terminal's net ahead of the element ``place`` describes
    (a trip's or a reverse block's) that its record does not declare in
    ``ahead`` with why: each sees the cell before the element does."""
    label, fp = place["label"], place["fp"]
    ahead = place["record"].get("ahead") or []
    if not isinstance(ahead, list):
        raise RecordError(f"{label}.ahead: give [{{ref, value, why}}]")
    declared = {fp.ref}
    for i, rec in enumerate(ahead):
        P.keys(rec, f"{label}.ahead[{i}]", ("ref", "value", "why"))
        P.text(rec["why"], f"{label}.ahead[{i}].why")
        declared.add(d.part(p.path, rec["ref"], rec["value"], f"{label}.ahead[{i}]").ref)
    return _others(d, p.path, declared, place["term"], b)


def _trip(d, p: Protector, b: Battery, cond_net, what: str) -> dict | str:
    """A protector's trip with its place on the copper held: the parsed trip, or
    why it does not guard the conductor."""
    out = _trip_place(d, p, b, what)
    if isinstance(out, str) or out["kind"] == "pcm":
        return out
    fp, terminal, term, far = out["fp"], out["terminal"], out["term"], out["far"]
    if terminal == "pos" and not d.same(cond_net, far):
        return (f"{fp.ref} is not between {b.id} and {what}: it feeds {d.label(far)}, and "
                f"the conductor is on {d.label(cond_net)}")
    if terminal == "neg" and d.same(cond_net, term):
        return f"{what} is on {b.id}'s return ahead of {fp.ref}, which does not guard it"
    taps = _ahead(d, p, b, out)
    if taps:
        raise RecordError(f"{out['label']}: {', '.join(taps)} on {b.id}'s {terminal} net "
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


# --------------------------------------------------------------------------- #
# mis-mating
# --------------------------------------------------------------------------- #

#: A Type-C VBUS net's voltage, for the nominal map only: 5 V, USB's default
#: bus voltage (Type-C R2.0 4.4.2 holds the default VBUS to the USB 2.0 and 3.2
#: specifications'). Two receptacles' VBUS, or a VBUS and a rail stated at 5 V,
#: that the nominal map joins are one rail. A PD contract above 5 V is
#: negotiated, and not judged (references/mismate.md). Rejected: 5.5 V, the
#: range's top (a 5 V rail stated at its nominal would read as another
#: voltage).
VBUS_NOMINAL_V = 5.0

#: A footprint the coverage rule asks a mating of: its library is a connector's
#: or a battery's (KiCad's ``Connector*`` libraries and ``Battery``), or its ref
#: is J, P, CN or BT and a number (IEEE 315's letters for a jack, a plug and a
#: battery; CN is a common one for a connector). JP is left out by the number:
#: a solder jumper is no mating. A test point (``TestPoint``) is no mating.
CONNECTOR_LIBS = ("Connector", "Battery")
_CONNECTOR_REF = re.compile(r"^(J|P|CN|BT)\d+$")


class _Terminal:
    """One node a source drives: a battery's terminal or tap, a net beyond a
    fault element, a Type-C VBUS or GND, a rail or its return. ``level`` is its
    place in its source, 0 the return; ``vkey`` says which voltage a source
    node is, so the nominal map may join two at one voltage into one rail."""

    __slots__ = ("node", "group", "level", "protected", "vkey", "label")

    def __init__(self, node, group, level, protected, vkey, label):
        self.node, self.group, self.level = node, group, level
        self.protected, self.vkey, self.label = protected, vkey, label


class _Nets:
    """A union-find over board nets ``(board path, name)``: the nets one way
    of mating makes."""

    def __init__(self, pairs=()):
        self.parent: dict = {}
        for a, b in pairs:
            self.join(a, b)

    def find(self, node):
        self.parent.setdefault(node, node)
        while self.parent[node] != node:
            self.parent[node] = self.parent[self.parent[node]]
            node = self.parent[node]
        return node

    def join(self, a, b) -> None:
        if a is None or b is None:
            return
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[max(ra, rb)] = min(ra, rb)


class _Rail:
    """One ``pcb_rails`` record: a net a regulator or an outside supply drives,
    its return, and its nominal voltage. A net a cell drives is the battery's,
    carried by the matings, and is never stated again as a rail."""

    def __init__(self, d, rid: str, rec: dict):
        label = f"pcb_rails.{rid}"
        P.keys(rec, label, ("net", "nominal_v", "return", "source"), ("board",))
        P.source(rec, label)
        self.id = rid
        self.v = P.num(rec["nominal_v"], f"{label}.nominal_v", gt=0.0)
        path = d.board(rec, label)
        names = set(d.boards[path].net_names)
        for key in ("net", "return"):
            name = P.text(rec[key], f"{label}.{key}")
            if name not in names:
                raise RecordError(f"{label} is for another design: {os.path.basename(path)} has "
                                  f"no net {name}")
        if str(rec["net"]) == str(rec["return"]):
            raise RecordError(f"{label}: its net and its return are both {rec['net']}")
        self.node, self.ret = (path, str(rec["net"])), (path, str(rec["return"]))


def _terminals(d, batteries: dict, protectors: list, usbcs: list, rails: list):
    """Every node a source drives, and the notes on the elements that protect
    none (a fuse not fitted, a trip not on its terminal)."""
    terms, notes, packs = [], [], set()
    for p in protectors:
        if "trip" not in p.functions:
            continue
        place = _trip_place(d, p, p.battery, "its load")
        if isinstance(place, str):
            notes.append(place)
        elif place["kind"] == "pcm":
            packs.add(p.battery.id)
        else:
            b, fp = p.battery, place["fp"]
            node = P.Design.pad_node(p.path, fp, place["far_pin"])
            if node is not None:
                terms.append(_Terminal(node, ("battery", b.id),
                                       b.series if place["terminal"] == "pos" else 0, True,
                                       ("battery", b.id),
                                       f"{_named(d, node)} (beyond {fp.ref})"))
    for b in batteries.values():
        nodes = [b.neg, *b.taps, b.pos]
        for level, pin in enumerate(nodes):
            node = P.Design.pad_node(b.path, b.fp, pin)
            if node is None:
                continue
            what = ("+" if level == len(nodes) - 1 else "-" if level == 0 else f"tap {level}")
            # An in-pack PCM that trips guards every node the pack brings out.
            terms.append(_Terminal(node, ("battery", b.id), level, b.id in packs,
                                   ("battery", b.id), f"{b.id}'s {what}"))
    named = set()
    for u in usbcs:
        named.add((u.path, u.fp.ref))
        for spec in u.specs(P.TYPEC_VBUS):
            terms.append(_Terminal(u.node(spec), ("usbc", u.id), 1, False,
                                   ("v", VBUS_NOMINAL_V), f"{u.fp.ref}'s VBUS"))
        for spec in u.specs(P.TYPEC_GND):
            terms.append(_Terminal(u.node(spec), ("usbc", u.id), 0, False, None,
                                   f"{u.fp.ref}'s GND"))
    # A receptacle no record names still brings VBUS in: read by its pads'
    # names (Type-C's, or the pins' VBUS and GND), so a project that states
    # no pcb_usbc does not hide its VBUS from every mis-mating.
    for path, board in sorted(d.boards.items()):
        for fp in board.footprints:
            if not fp.fitted or (path, fp.ref) in named or not P.typec_receptacle(fp):
                continue
            group = ("usbc", f"{os.path.basename(path)}:{fp.ref}")
            for pad in fp.pads:
                fn = pad.pinfunction.strip().upper()
                level = (1 if pad.number in P.TYPEC_VBUS or fn == "VBUS" else
                         0 if pad.number in P.TYPEC_GND or fn == "GND" else None)
                if level is not None and pad.net:
                    terms.append(_Terminal((path, pad.net), group, level, False,
                                           ("v", VBUS_NOMINAL_V) if level else None,
                                           f"{fp.ref}'s {'VBUS' if level else 'GND'}"))
    for r in rails:
        terms.append(_Terminal(r.node, ("rail", r.id), 1, False, ("v", r.v), f"rail {r.id}"))
        terms.append(_Terminal(r.ret, ("rail", r.id), 0, False, None, f"rail {r.id}'s return"))
    seen, out = set(), []
    for t in terms:
        if t.node is not None and (t.node, t.group, t.level) not in seen:
            seen.add((t.node, t.group, t.level))
            out.append(t)
    return out, notes


def _hazards(terms: list, nets: _Nets, *, nominal: bool) -> dict:
    """``{frozenset(two nodes): (raw, terminal, terminal)}`` for each pair of
    nodes ``nets`` puts on one net that is a hazard (:func:`_loop`)."""
    by_root: dict = {}
    for t in terms:
        by_root.setdefault(nets.find(t.node), {}).setdefault(t.node, []).append(t)
    out: dict = {}
    for by_node in by_root.values():
        nodes = sorted(by_node)
        for i, n1 in enumerate(nodes):
            for n2 in nodes[i + 1:]:
                found = _loop(by_node[n1], by_node[n2], nominal=nominal)
                if found is not None:
                    out[frozenset((n1, n2))] = found
    return out


def _loop(here: list, there: list, *, nominal: bool):
    """``(raw, terminal, terminal)`` when two nodes on one net are a hazard,
    else None: ``here`` and ``there`` are the terminals on each node.

    Each source with a node on one side drives a loop. When the other side
    carries a terminal of that same source at another level, the source's own
    loop closes there, and that pair decides (a cell's + on its own return
    beyond a low-side switch is protected, whatever else shares that return;
    VBUS on its own receptacle's GND is raw). Only when it carries none does
    the source meet another source's node or return (two returns meeting is
    none; on the nominal map, two sources at one voltage are one rail). A
    pair is raw unless either of its nodes lies beyond a fault element; the
    node pair is raw when any source's loop is.

    What slipped through P6: protection was decided per pair of terminals and
    a raw pair won the node pair, so a fitted USB-C receptacle's GND beside the
    cell's return beyond a low-side FET turned the cell's + on that net raw: a
    short whose loop runs through the FET failed, and passed with the
    receptacle left off."""
    found = []
    for side, other in ((here, there), (there, here)):
        for group in sorted({t.group for t in side if t.level}):
            mine = [t for t in side if t.group == group]
            pairs = [(s, o) for s in mine for o in other
                     if o.group == group and o.level != s.level]
            if not pairs:
                pairs = [(s, o) for s in mine if s.level for o in other
                         if o.group != group
                         and not (nominal and o.level and s.vkey == o.vkey)]
            found += [(not (s.protected or o.protected), s, o) for s, o in pairs]
    if not found:
        return None
    return min(found, key=lambda f: (not f[0], f[1].label, f[2].label))


def _named(d, node) -> str:
    """A board net's own name, and its board's when the design has two: never
    the name the nominal joins gave its root, which a wrong mating is judged
    without. (What slipped through this rule's first cut: a pin-order flip
    whose straight map joined VBUS to GND printed both pins as GND.)"""
    if node is None:
        return "no net"
    path, name = node
    return f"{name} ({os.path.basename(path)})" if len(d.boards) > 1 else name


def _contact(d, m, side: str, pin: str) -> str:
    path, fp = m.a if side == "a" else m.b
    return f"{fp.ref} pad {pin} ({_named(d, P.Design.pad_node(path, fp, pin))})"


def _met(d, m, groups, base: _Nets, t1, t2) -> str:
    """Which contacts of a wrong mating put ``t1`` and ``t2`` on one net: the
    conductor that touches both, else the two terminals by name. The source
    node is said first."""
    t1, t2 = sorted((t1, t2), key=lambda t: (-t.level, t.label))
    r1, r2 = base.find(t1.node), base.find(t2.node)
    for g in groups:
        roots = []
        for side, pin in g:
            path, fp = m.a if side == "a" else m.b
            node = P.Design.pad_node(path, fp, pin)
            roots.append(base.find(node) if node else None)
        if r1 in roots and r2 in roots:
            c1, c2 = g[roots.index(r1)], g[roots.index(r2)]
            return f"{_contact(d, m, *c1)} meets {_contact(d, m, *c2)}"
    return f"{t1.label} meets {t2.label}"


def _reverse_block(d, p: Protector, b: Battery) -> tuple[bool, str]:
    """``(counts, who or why not)`` for ``p``'s ``reverse_block``: on the board,
    fitted, one path pin on the battery terminal's net and one off it, and no
    part the record does not declare ``ahead`` on that net before it."""
    label = p.fn("reverse_block")
    t = P.keys(p.functions["reverse_block"], label, ("terminal",),
               ("path_pins", "switch", "ahead"))
    if p.where != "board":
        raise RecordError(f"{label}: {p.name} is {p.where}; a reverse block sits on the "
                          f"board, behind the pigtail that reverses (where: board)")
    terminal = P.word(t["terminal"], f"{label}.terminal", ("pos", "neg"))
    if ("switch" in t) == ("path_pins" in t):
        raise RecordError(f"{label}: give its path_pins [p, q], or its switch {{ref, value, "
                          f"path}}, one of the two")
    if "switch" in t:
        sw = P.keys(t["switch"], f"{label}.switch", ("ref", "value", "path"))
        pins = P.pins(sw["path"], f"{label}.switch.path", 2)
        fp = d.part(p.path, sw["ref"], sw["value"], f"{label}.switch", pins)
    else:
        pins = P.pins(t["path_pins"], f"{label}.path_pins", 2)
        fp = d.part(p.path, p.fp.ref, p.fp.value, label, pins)
    if not fp.fitted:
        return False, P.not_fitted(fp)
    term = b.net(b.pos if terminal == "pos" else b.neg)
    a, z = (d.net(p.path, fp, pin) for pin in pins)
    if not (d.same(a, term) != d.same(z, term)):
        return False, (f"{fp.ref} is not on {b.id}'s {terminal} terminal: its path pins sit on "
                       f"{d.label(a)} and {d.label(z)}")
    taps = _ahead(d, p, b, {"label": label, "fp": fp, "record": t, "term": term})
    if taps:
        return False, (f"{', '.join(taps)} on {b.id}'s {terminal} net sees the reversed cell "
                       f"ahead of {fp.ref}")
    return True, f"{fp.ref} {fp.value}"


def _reversed(d, m, b: Battery, protectors: list) -> tuple[str, str]:
    """``(verdict, line)`` for a battery whose pigtail can be reversed:
    ``blocked`` by a reverse block, ``limited`` by a fuse or PTC at its
    terminal with nothing ahead of it, or ``open``. An element in the pack is
    no reverse protection: it sits before the pigtail that reverses."""
    blocks, limits, why_not = [], [], []
    for p in (p for p in protectors if p.battery is b):
        if "reverse_block" in p.functions:
            ok, said = _reverse_block(d, p, b)
            (blocks if ok else why_not).append(said)
        if "trip" not in p.functions:
            continue
        place = _trip_place(d, p, b, "its load")
        if isinstance(place, str):
            why_not.append(place)
        elif place["kind"] == "pcm":
            why_not.append(f"{p.name} is in the pack, before the pigtail that reverses")
        elif place["kind"] not in REVERSE_LIMITERS:
            why_not.append(f"{place['fp'].ref} is a {place['kind']}, which a reversed cell "
                           f"defeats")
        else:
            taps = _ahead(d, p, b, place)
            if taps:
                why_not.append(f"{', '.join(taps)} sees the reversed cell ahead of "
                               f"{place['fp'].ref}")
            else:
                limits.append(f"{place['fp'].ref} ({place['kind']})")
    head = f"{m.id}: {b.id} reversed in {m.a[1].ref}"
    tail = f" (not counted: {'; '.join(why_not)})" if why_not else ""
    if blocks:
        return "blocked", f"{head} is blocked by {', '.join(blocks)}"
    if limits:
        return "limited", (f"{head}: nothing blocks it, and {', '.join(limits)} limits its "
                           f"current (pcb.fault_current judges that){tail}")
    return "open", f"{head}: nothing blocks it or limits its current{tail}"


@gate(
    id="pcb.mismate",
    basis="model-only",
    title="No stated mis-mating of a connector, cable or cell joins a source to its return or "
          "to another source",
    claims=["pcb", "battery-safety", "mismate", "connector-keying"],
    tier=Tier.INSTANT,
    settles="connector mis-mating",
    negative_control=NegativeControl(
        fixture="selftest/bad_boards.py:ground_beside_vbus",
        note="the power baseline with the nets of positions 1 and 7 swapped on both FFC "
             "housings, as a generator's rotation flip reorders them: a 6-way cable two "
             "positions off in the 8-way housings puts VBUS on GND",
    ),
)
def mismate(ctx: GateContext) -> Verdict:
    """For each mating in ``pcb_matings``, every wrong way its record says it
    can meet, and the nominal map itself: the hazards, each a pair of nodes a
    source drives that the mating puts on one net.

    **Sources.** Each battery's terminals and taps; each net beyond a fault
    element (``pcb_protectors``' trip: a fuse's far side is a protected source,
    a low-side switch's far side a protected return); each Type-C receptacle's
    VBUS and GND (``pcb_usbc``, and any receptacle its pads name); each
    ``pcb_rails`` net and its return.

    **Hazards.** Two nodes of one source on one net (a cell's + on its -, VBUS
    on its GND), or a source's node on another source's node (two returns
    meeting is none). On the nominal map two sources at one voltage are one
    rail, never a hazard; it is checked first, so a regenerated board whose pin
    order no longer matches the cable is caught on the correct cable. A hazard
    whose source's own loop runs through a fault element (or, where the
    source meets another source, a pair of which either node lies beyond one)
    is named as protected, and pcb.fault_current judges that element; it does
    not fail here (:func:`_loop`). A source meeting a signal is listed in
    the evidence and not judged. A battery whose pigtail can be ``reversed`` is
    a hazard unless a ``reverse_block`` in series counts (on the board, nothing
    ahead of it that its record does not declare), and protected when a fuse
    or PTC at its terminal limits the current; an in-pack PCM is neither.

    **Coverage.** Every fitted connector (a ``Connector*`` or ``Battery``
    library, or a ref J, P, CN or BT and a number) with a pad on a source's net
    must be named by a mating, ``modes: []`` included: an unnamed one errors,
    naming it. A mating with no ``modes`` errors: the person says how else it
    can meet, or that nothing can.

    measured is the count of raw hazards, each pair of nodes once however many
    wrong matings make it, against 0. What it reads is the records' word: the
    modes a person states, the pinouts on the boards. It cannot see a mode
    nobody stated, a keyed housing forced, a source on a GPIO, or arcing as a
    plug goes in (PACK.md)."""
    gid = "pcb.mismate"
    try:
        recs = P.records(ctx, "pcb_matings")
        if not recs:
            return _skip(gid, "no pcb_matings: which connectors, cables and cells meet, and in "
                              "which wrong ways they can, are records a person enters")
        d = P.Design(ctx)
        batteries = {bid: Battery(d, bid, rec)
                     for bid, rec in sorted((P.records(ctx, "pcb_batteries") or {}).items())}
        protectors = [Protector(d, pid, rec, batteries)
                      for pid, rec in sorted((P.records(ctx, "pcb_protectors") or {}).items())]
        usbcs = [P.UsbC(d, uid, rec)
                 for uid, rec in sorted((P.records(ctx, "pcb_usbc") or {}).items())]
        rails = [_Rail(d, rid, rec)
                 for rid, rec in sorted((P.records(ctx, "pcb_rails") or {}).items())]
        d.matings(recs)
        for m in d.mates:
            if m.modes is None:
                raise RecordError(f"{m.label} has no modes: say in which wrong ways it can "
                                  f"meet, [] when it cannot, and why in source")
            if m.kind == "battery":
                bat = batteries.get(m.b)
                if bat is None:
                    raise RecordError(f"{m.label}.b.battery is {m.b!r}, which pcb_batteries "
                                      f"does not hold")
                if (m.a[0], m.a[1].ref) != (bat.path, bat.fp.ref):
                    raise RecordError(f"{m.label}: battery {m.b}'s terminals are on "
                                      f"{bat.fp.ref}, not {m.a[1].ref}")
        terms, notes = _terminals(d, batteries, protectors, usbcs, rails)
        if not any(t.level for t in terms):
            return _skip(gid, "no source: no battery, no USB-C receptacle and no rail in the "
                              "records or on the boards they name, so no mating can join one "
                              "to its return")
        result = _judge(d, terms, batteries, protectors)
    except ReaderLimit as exc:
        return _skip(gid, str(exc))
    except RecordError as exc:
        return _error(gid, str(exc))
    raw = [h for h in result["hazards"] if h["raw"]]
    kept = [h for h in result["hazards"] if not h["raw"]]
    ev = _evidence(ctx, "mismate", {
        "boards": sorted(d.boards),
        "matings": result["matings"],
        "sources": sorted({t.label for t in terms if t.level}),
        "hazards": [{k: v for k, v in h.items() if k != "key"} for h in raw],
        "protected": [{k: v for k, v in h.items() if k != "key"} for h in kept],
        "reversed_batteries": result["reversed"],
        "source_meets_signal": result["signal"],
        "fault_elements_not_counted": notes})
    judged = sum(len(m["ways"]) for m in result["matings"])
    head = (f"{len(raw)} hazard(s): " + "; ".join(h["said"] for h in raw[:3])
            + ("; ..." if len(raw) > 3 else "") if raw else
            f"no stated mis-mating joins a source to its return or another source "
            f"({judged} wrong way(s) of {len(result['matings'])} mating(s), and each nominal map)")
    tail = ""
    if kept:
        tail += (f"; {len(kept)} protected, judged by pcb.fault_current: "
                 + "; ".join(h["said"] for h in kept[:3]))
    blocked = [r["said"] for r in result["reversed"] if r["verdict"] == "blocked"]
    if blocked:
        tail += f"; {'; '.join(blocked)}"
    if result["signal"]:
        tail += (f"; {len(result['signal'])} source-to-signal join(s) listed in the evidence, "
                 f"not judged")
    return Verdict(gate=gid, passed=not raw, measured=len(raw), limit=0, comparator="<=",
                   units="hazards", detail=head + tail, evidence=ev)


def _ends(m) -> frozenset:
    """The connectors a mating joins, ``(board path, ref)`` each."""
    far = {(m.b[0], m.b[1].ref)} if m.kind == "board" else {(m.kind, m.b)}
    return frozenset({(m.a[0], m.a[1].ref)} | far)


def _judge(d, terms: list, batteries: dict, protectors: list) -> dict:
    """The nominal maps, the coverage, every stated wrong mating and every
    reversible battery, judged."""
    boards = [m for m in d.mates if m.kind == "board"]

    def pairs_of(m) -> list:
        (pa, fa), (pb, fb) = m.a, m.b
        return [(P.Design.pad_node(pa, fa, x), P.Design.pad_node(pb, fb, y))
                for x, y in m.pairs]

    found: dict = {}

    def keep(key, raw, said, where):
        h = found.get(key)
        if h is None:
            found[key] = {"key": key, "raw": raw, "said": said, "ways": [where]}
        else:
            h["ways"].append(where)
            if raw and not h["raw"]:
                h.update(raw=True, said=said)

    # The nominal maps, one mating at a time, so each hazard names the map that
    # made it.
    nominal = _Nets()
    seen = _hazards(terms, nominal, nominal=True)
    for key, (raw, t1, t2) in seen.items():
        t1, t2 = sorted((t1, t2), key=lambda t: (-t.level, t.label))
        keep(key, raw, f"{t1.label} meets {t2.label} on the boards as drawn", "as drawn")
    for m in boards:
        before = _Nets()
        before.parent = dict(nominal.parent)
        for a, b in pairs_of(m):
            nominal.join(a, b)
        for key, (raw, t1, t2) in _hazards(terms, nominal, nominal=True).items():
            if key not in seen:
                seen[key] = (raw, t1, t2)
                groups = [[("a", x), ("b", y)] for x, y in m.pairs]
                keep(key, raw, f"{m.id}'s own map: {_met(d, m, groups, before, t1, t2)}",
                     f"{m.id} as mapped")
    # Coverage: every connector that carries a source is named by a mating.
    source_roots = {nominal.find(t.node) for t in terms if t.level}
    declared = {(m.a[0], m.a[1].ref) for m in d.mates}
    declared |= {(m.b[0], m.b[1].ref) for m in boards}
    missing = []
    for path, board in sorted(d.boards.items()):
        for fp in board.footprints:
            if not fp.fitted or (path, fp.ref) in declared:
                continue
            if not (fp.lib_id.split(":")[0].startswith(CONNECTOR_LIBS)
                    or _CONNECTOR_REF.match(fp.ref)):
                continue
            carries = sorted({p.net for p in fp.pads
                              if p.net and nominal.find((path, p.net)) in source_roots})
            if carries:
                where = f", {os.path.basename(path)}" if len(d.boards) > 1 else ""
                missing.append(f"{fp.ref} ({fp.value}{where}) carries {', '.join(carries)}")
    if missing:
        raise RecordError(f"no pcb_matings record names {'; '.join(missing)}: each connector "
                          f"that carries a source needs one, saying in which wrong ways it can "
                          f"meet ([] when it cannot, and why in source)")
    # Every stated wrong way.
    matings, signal = [], set()
    terminal_nodes = {t.node for t in terms}
    for m in d.mates:
        ways = []
        # A mating stated again with its ends swapped (a mode that acts on b's
        # housing) is the same cable: its nominal map is no part of this one's
        # wrong ways, or a cable both seated and off would join every position.
        ends = _ends(m)
        others = [pair for o in boards if _ends(o) != ends for pair in pairs_of(o)]
        for mode in m.modes:
            for what, groups in m.instances(mode):
                ways.append(what)
                base = _Nets(others)
                nets = _Nets(others)
                for g in groups:
                    nodes = []
                    for side, pin in g:
                        path, fp = m.a if side == "a" else m.b
                        nodes.append(P.Design.pad_node(path, fp, pin))
                    nodes = [n for n in nodes if n is not None]
                    for n in nodes[1:]:
                        nets.join(nodes[0], n)
                where = f"{m.id}, {what}"
                for key, (raw, t1, t2) in _hazards(terms, nets, nominal=False).items():
                    if nominal.find(t1.node) == nominal.find(t2.node):
                        continue                # the nominal map's, judged above
                    keep(key, raw, f"{where}: {_met(d, m, groups, base, t1, t2)}", where)
                for t in terms:
                    if not t.level:
                        continue
                    root = nets.find(t.node)
                    for node in list(nets.parent):
                        if (node not in terminal_nodes and nets.find(node) == root
                                and nominal.find(node) != nominal.find(t.node)):
                            signal.add((t.label, _named(d, node)))
        if m.kind == "battery" and ("reversed",) in m.modes:
            ways.append("reversed")
        matings.append({"mating": m.id, "kind": m.kind, "a": m.a[1].ref,
                        "modes": [P.describe(x) for x in m.modes], "ways": ways,
                        "source": m.source})
    # A battery whose pigtail can be reversed.
    rev = []
    for m in d.mates:
        if m.kind != "battery" or ("reversed",) not in m.modes:
            continue
        verdict, said = _reversed(d, m, batteries[m.b], protectors)
        rev.append({"mating": m.id, "battery": m.b, "verdict": verdict, "said": said})
        if verdict != "blocked":
            keep(("reversed", m.b), verdict == "open", said, f"{m.id}, reversed")
    hazards = sorted(found.values(), key=lambda h: (not h["raw"], h["said"]))
    return {"hazards": [{**h, "ways": sorted(set(h["ways"]))} for h in hazards],
            "matings": matings, "reversed": rev,
            "signal": [{"source": s, "meets": n} for s, n in sorted(signal)]}
