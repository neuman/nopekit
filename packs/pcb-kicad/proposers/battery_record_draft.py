# SPDX-License-Identifier: Apache-2.0
"""pcb.battery_record_draft — write a DRAFT of a design's power records from its boards.

    nopekit propose pcb.battery_record_draft -- --board hardware/power.kicad_pcb \\
        [--board hardware/main.kicad_pcb]

A proposer, never an evaluator: it writes a draft, and a draft settles nothing. The
power rules (``pcb.charge_temperature``, ``pcb.charge_limits``, ``pcb.fault_current``,
``pcb.discharge_cutoff``, ``pcb.mismate``, ``pcb.usbc_receptacle``) judge records a
person enters: which cell, which charger, which fuse, which connector meets which,
each with its datasheet numbers. Those records are the heaviest part of first use.
This writes their skeleton from what the boards already say, in the shapes the
rules read (``gates/_power.py``, ``references/battery.md``, ``mismate.md``,
``usb-c.md``): objects keyed by id under ``pcb_batteries``, ``pcb_protectors``,
``pcb_conductors``, ``pcb_matings``, ``pcb_usbc`` and ``pcb_rails``.

**What it fills, from the boards:** each record's board, ref and value, and the pads
a part's pin names or its copper identify (a charger's thermistor pin, the resistor
on its PROG pin, a protector's DO and CO pins and the switch each drives, a fuse's
two pads). **What it never fills:** every datasheet number, every flag and every
choice that is the person's word (a chemistry, a cell count, a role, a mating's far
side and modes). Each is a string that starts ``FILL:`` and says what to state and
what the board shows beside it. No number and no true or false is anywhere in the
draft: a guessed value would read as a fact the moment it is adopted. Every power
rule refuses a ``FILL:`` value where it reads a number, a flag or a word (an error
naming it), so an adopted draft passes nothing until every one is stated.

**Where candidates come from** (each with its evidence in the summary):

- a battery: a footprint from KiCad's ``Battery*`` libraries or with a ref BT and a
  number; its pads named ``+`` and ``-`` (KiCad's battery symbols) fill pos and neg;
- a charger: a part with a cell pin (:data:`CELL_PINS`) and a charge-current or a
  thermistor pin (:data:`CURRENT_PINS`, :data:`THERM_PINS`);
- a protector: a part with a cutoff pair (:data:`CUTOFF_PAIRS`);
- a fuse: a two-pad part from KiCad's ``Fuse*`` libraries or with a ref F and a
  number, one pad on a battery's net;
- a conductor: each pin group of a connector on a net a battery drives (its own
  nets, or the far side of a drafted fuse);
- a USB-C receptacle: as ``pcb.usbc_receptacle`` finds one;
- a rail: a part with a regulator's input and output pins (:data:`RAIL_IN`,
  :data:`RAIL_OUT`), on its output's net;
- a mating: every connector ``pcb.mismate``'s coverage rule will ask about (one on a
  net a battery, a VBUS or a drafted rail drives), and each battery's own.

**What it cannot see**, said in the summary: an element inside the pack (its
protection board), a stop that firmware makes, which connector meets which, a cell
on a board not named, and a part whose pins carry no name.

It refuses, exits 2 and writes nothing when ``$NOPEKIT_PROPOSAL_DIR`` is unset, a
board is missing, named twice, does not parse, is one the power rules refuse or
skip (``_kicad.board_problems``), or holds nothing to draft. **What it writes**
under ``$NOPEKIT_PROPOSAL_DIR``: ``<stem>.power_records.json``, the draft (only the
six keys), and ``<stem>.power_draft.json``, the summary: each candidate with the
pins and nets that found it, every ``FILL:`` value's path, what it cannot see, and
each board's sha256. Standard library and the pack's reader only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
_GATES = os.path.join(os.path.dirname(HERE), "gates")
sys.dont_write_bytecode = True        # never leave a __pycache__ in the pack
if _GATES not in sys.path:
    sys.path.insert(0, _GATES)
import _kicad as K                                                 # noqa: E402

#: How every value this draft leaves to the person starts. A string, never a
#: number, a flag or null: the power rules read a number with ``_power.num``, a
#: band with ``band``, a flag with ``flag`` and a word with ``word``, and each
#: refuses a string, quoting it, so the rule's error names what to fill.
#: Rejected: null (the design's first word): it says nothing in place, and the
#: rules' refusal of it ("None: give a number") says nothing either.
FILL = "FILL: "

#: The six records this writes, in the order PACK.md lists them.
RECORDS = ("pcb_batteries", "pcb_protectors", "pcb_conductors", "pcb_matings",
           "pcb_usbc", "pcb_rails")

#: A cell's pin on a charger, by its library pin name: the names KiCad 9.0.9's
#: ``Battery_Management`` library gives it (137 symbols surveyed: BAT 58, VBAT
#: 35, BATT 10, BATT+ 10). Rejected: SYS and OUT (a power path's output, which a
#: charger drives from the input as well as the cell).
CELL_PINS = ("BAT", "VBAT", "BATT", "BATT+")

#: A charger's charge-current setting pin, by the same survey (PROG 36, ISET 14,
#: PROG1 11). Rejected: ILIM (an input-current limit, not the cell's) and the
#: four PGM pins of one NiMH family (they select a cell count and a time).
CURRENT_PINS = ("PROG", "PROG1", "ISET", "ISET1")

#: A charger's thermistor pin, by the same survey (NTC 20, TS 16, THERM 13, TEMP
#: 11). Rejected: TS1, TS2 (a battery monitor's sensors, which stop nothing
#: themselves).
THERM_PINS = ("THERM", "TS", "NTC", "TEMP")

#: A protector's cutoff pins, ``(discharge, charge)``, by the same survey: a
#: one-cell protector's DO and CO, another family's DOUT and COUT, and a battery
#: monitor's DSG and CHG. Only the pair counts: CHG alone is a charger's status
#: pin on 24 symbols there.
CUTOFF_PAIRS = (("DO", "CO"), ("DOUT", "COUT"), ("DSG", "CHG"))

#: A protector's pin that senses the cell (VDD on the one-cell protectors, BAT
#: on another family, VCC on older parts).
SENSE_PINS = ("VDD", "BAT", "VCC")

#: A battery's terminals by pin name: KiCad's battery symbols name them + and -
#: (``Device:Battery``, ``Device:Battery_Cell``); a connector's pins named so.
POS_PINS = ("+", "BAT+", "BATT+", "B+")
NEG_PINS = ("-", "BAT-", "BATT-", "B-")

#: A battery's thermistor pin, by name.
BATTERY_THERM_PINS = ("T", "TH", "NTC", "THERM", "TEMP")

#: A regulator, by its pins: an output and an input, on a part that is no charger,
#: protector or connector. Its output's net is drafted as a rail, its return the
#: net of its one ground pin.
RAIL_OUT = ("VOUT", "VO", "OUT")
RAIL_IN = ("VIN", "VI", "IN")
RETURN_PINS = ("GND", "VSS", "PGND")

#: The connectors ``pcb.mismate``'s coverage rule asks a mating of, as
#: ``gates/power.py`` holds them (``CONNECTOR_LIBS``, ``_CONNECTOR_REF``); copied,
#: since a proposer imports no gate, and held equal by tests/test_pcb_kicad.py.
CONNECTOR_LIBS = ("Connector", "Battery")
_CONNECTOR_REF = re.compile(r"^(J|P|CN|BT)\d+$")

#: A battery's holder or connector, and a fuse: by library or ref.
_BATTERY_REF = re.compile(r"^BT\d+$")
_FUSE_REF = re.compile(r"^F\d+$")

#: ``gates/_power.py``'s USB-C receptacle test (``typec_receptacle``), copied
#: for the same reason and held equal by the same test.
TYPEC_VBUS = ("A4", "A9", "B4", "B9")
TYPEC_DATA = ("A6", "B6", "A7", "B7")
_TYPEC_NAME = re.compile(r"USB.?C|TYPE.?C", re.IGNORECASE)
_TYPEC_CONNECTOR_REF = re.compile(r"^(J|P|CN|X|USB)\d+$")

_MARKUP = re.compile(r"[~_^]\{([^}]*)\}")


def pin_name(name: str) -> str:
    """A library pin name as the tables here hold it: KiCad's overbar, subscript
    and superscript markup taken off (``V_{BAT}`` is VBAT, ``~{PG}`` is PG),
    spaces dropped, upper case."""
    return _MARKUP.sub(r"\1", name or "").replace(" ", "").upper()


def typec_receptacle(fp) -> bool:
    """``gates/_power.py``'s ``typec_receptacle``: a connector whose pads carry the
    pin names CC1 and CC2, or a footprint named USB-C or Type-C (not a plug) with
    pads A5 and B5."""
    lib = fp.lib_id.split(":")[-1]
    named_typec = bool(_TYPEC_NAME.search(lib)) and "PLUG" not in lib.upper()
    names = {p.pinfunction.strip().upper() for p in fp.pads}
    if {"CC1", "CC2"} <= names:
        return (named_typec or fp.lib_id.split(":")[0].startswith("Connector")
                or bool(_TYPEC_CONNECTOR_REF.match(fp.ref)))
    return named_typec and {"A5", "B5"} <= {p.number for p in fp.pads}


def connector(fp) -> bool:
    """Whether ``pcb.mismate``'s coverage rule reads ``fp`` as a connector."""
    return (fp.lib_id.split(":")[0].startswith(CONNECTOR_LIBS)
            or bool(_CONNECTOR_REF.match(fp.ref)))


def _sha256(path: str) -> str:
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def _order(number: str) -> tuple:
    return (not number.isdigit(), int(number) if number.isdigit() else 0, number)


def _ref_order(ref: str) -> tuple:
    """Refs in reading order: BT1, F1, J1, J6, J10, U1."""
    m = re.match(r"^(\D*)(\d*)(.*)$", ref)
    return (m.group(1), int(m.group(2)) if m.group(2) else -1, m.group(3))


class Board:
    """One board, read with the pack's reader: its fitted parts, and who sits on
    each net."""

    def __init__(self, given: str, root: str):
        path = os.path.abspath(given)
        self.path = path
        try:
            inside = os.path.commonpath([path, os.path.abspath(root)]) == os.path.abspath(root)
        except ValueError:                       # another drive, on Windows
            inside = False
        self.rel = (os.path.relpath(path, root) if inside else path).replace(os.sep, "/")
        self.stem = os.path.basename(path)[:-len(".kicad_pcb")]
        tree = K.load_tree(path)
        _version, self.problems = K.board_problems(tree)
        self.fps: list = []
        self.unfitted: list[str] = []
        self.on: dict[str, list[tuple]] = {}
        if self.problems:
            return
        board = K.board_from_tree(tree)
        self.fps = sorted((fp for fp in board.footprints if fp.fitted),
                          key=lambda fp: _ref_order(fp.ref))
        self.unfitted = [f"{fp.ref} ({fp.value})" for fp in board.footprints if not fp.fitted]
        for fp in self.fps:
            for pad in fp.pads:
                if pad.net:
                    self.on.setdefault(pad.net, []).append((fp, pad))

    def names(self, fp) -> dict[str, list[str]]:
        """``{pin name: [pad numbers]}`` of ``fp``'s numbered pads."""
        out: dict[str, list[str]] = {}
        for pad in fp.pads:
            if pad.number and pad.pinfunction:
                out.setdefault(pin_name(pad.pinfunction), [])
                if pad.number not in out[pin_name(pad.pinfunction)]:
                    out[pin_name(pad.pinfunction)].append(pad.number)
        return out

    @staticmethod
    def net(fp, number: str) -> str:
        return next((p.net for p in fp.pads if p.number == number and p.net), "")

    def parts_on(self, net: str, but: set[str]) -> list:
        """The fitted parts with a pad on ``net``, other than the refs ``but``."""
        seen, out = set(but), []
        for fp, _pad in self.on.get(net, []):
            if fp.ref not in seen:
                seen.add(fp.ref)
                out.append(fp)
        return out

    @staticmethod
    def pads_of(fp) -> list[str]:
        """``fp``'s distinct pad numbers, in order."""
        return sorted({p.number for p in fp.pads if p.number}, key=_order)

    def said(self, fp) -> str:
        """``fp``'s pads with their pin names and nets, for a FILL's evidence."""
        rows = []
        for number in self.pads_of(fp):
            pad = next(p for p in fp.pads if p.number == number)
            name = pad.pinfunction.strip()
            rows.append(f"{number}{' ' + name if name and name != '~' else ''} on "
                        f"{self.net(fp, number) or 'no net'}")
        return ", ".join(rows)

    def around(self, net: str, but: set[str]) -> str:
        """The other parts on ``net``, for a FILL's evidence."""
        parts = self.parts_on(net, but)
        return ", ".join(f"{fp.ref} {fp.value}" for fp in parts) or "nothing else"


def _one(names: dict, wanted: tuple) -> tuple[str | None, str | None]:
    """``(name, pad)`` when exactly one pad carries exactly one of ``wanted``;
    ``(name, None)`` when that name sits on several pads; ``(None, None)`` when
    none or several of the names are there."""
    hits = [n for n in wanted if n in names]
    if len(hits) != 1:
        return None, None
    pads = names[hits[0]]
    return hits[0], (pads[0] if len(pads) == 1 else None)


class Draft:
    """The draft records and the summary's candidates, built board by board."""

    def __init__(self, boards: list[Board]):
        self.boards = boards
        self.records: dict[str, dict] = {k: {} for k in RECORDS}
        self.candidates: list[dict] = []
        self.unseen: list[str] = []

    # -- ids and words --------------------------------------------------- #
    def rid(self, b: Board, *parts: str) -> str:
        """A record id: the ref (and a net) lower-cased, the board's stem first
        when there is more than one board."""
        tail = "_".join(re.sub(r"[^A-Za-z0-9]+", "_", p).strip("_") for p in parts).lower()
        return f"{b.stem}_{tail}".lower() if len(self.boards) > 1 else tail

    def fresh(self, kind: str, rid: str) -> str:
        """``rid``, or ``rid_2``, ``rid_3`` when ``kind`` holds it already: two
        nets whose names differ only in their punctuation (+5V, 5V) make one
        id."""
        n, out = 1, rid
        while out in self.records[kind]:
            n += 1
            out = f"{rid}_{n}"
        return out

    def source(self, b: Board) -> str:
        return (f"DRAFT by pcb.battery_record_draft from {b.rel}'s pads and pin names: the "
                f"refs, values and pads are the board's, and each FILL is yours to state from "
                f"the part's datasheet or the design. Replace this sentence with where each "
                f"number comes from")

    def note(self, record: str, found_by: str, b: Board, fp=None) -> None:
        """A candidate for the summary: the record, what found it, and with a part
        its pads' pin names and nets."""
        row = {"record": record, "board": b.rel, "found_by": found_by}
        if fp is not None:
            row["pads"] = {n: [next(p.pinfunction for p in fp.pads if p.number == n),
                               b.net(fp, n)] for n in b.pads_of(fp)}
        self.candidates.append(row)

    # -- the passes ------------------------------------------------------ #
    def build(self) -> None:
        batteries = self._batteries()
        protectors = self._chargers() + self._protectors()
        fuses = self._fuses(batteries)
        if (protectors or fuses) and not batteries:
            batteries = [self._unknown_battery()]
        ids = [bid for bid, _b, _fp, _nets in batteries]
        battery = (ids[0] if len(ids) == 1 else
                   FILL + f"the pcb_batteries id this part serves (one of {', '.join(ids)})")
        for rec in self.records["pcb_protectors"].values():
            rec["battery"] = battery
        driven = self._conductors(batteries, fuses, battery)
        vbus = self._usbc()
        rails = self._rails()
        self._matings(batteries, driven | vbus | rails)

    def _batteries(self) -> list[tuple]:
        """``(id, board, footprint or None, the nets a cell drives)`` of each."""
        out = []
        for b in self.boards:
            for fp in b.fps:
                lib = fp.lib_id.split(":")[0]
                if not (lib.startswith("Battery") or _BATTERY_REF.match(fp.ref)):
                    continue
                names = b.names(fp)
                _p, pos = _one(names, POS_PINS)
                _n, neg = _one(names, NEG_PINS)
                _t, therm = _one(names, BATTERY_THERM_PINS)
                pads = b.said(fp)
                terminals = {
                    "ref": fp.ref, "value": fp.value,
                    "pos": pos or FILL + f"the pad of {fp.ref} on the cell's + terminal "
                                         f"(pads: {pads})",
                    "neg": neg or FILL + f"the pad of {fp.ref} on the cell's - terminal "
                                         f"(pads: {pads})"}
                if therm:
                    terminals["therm"] = therm
                bid = self.rid(b, fp.ref)
                rec = {"board": b.rel, "terminals": terminals, **self._cell_words()}
                if therm:
                    rec["ntc"] = self._ntc_numbers(f"the pack's NTC, on {fp.ref} pad {therm}")
                rec["source"] = self.source(b)
                self.records["pcb_batteries"][bid] = rec
                # The nets a cell drives: every pad's but its named - pad's.
                nets = {b.net(fp, n) for n in b.pads_of(fp) if n != neg} - {""}
                out.append((bid, b, fp, nets))
                self.note(f"pcb_batteries.{bid}",
                          f"library {lib}" if lib.startswith("Battery") else f"ref {fp.ref}",
                          b, fp)
        return out

    def _unknown_battery(self) -> tuple:
        b = self.boards[0]
        self.records["pcb_batteries"]["cell"] = {
            "board": b.rel,
            "terminals": {"ref": FILL + "the ref of the connector or holder the cell meets: "
                                        "no Battery-library part and no BT ref is on the "
                                        "boards named",
                          "value": FILL + "that part's value on its board",
                          "pos": FILL + "its pad on the cell's + terminal",
                          "neg": FILL + "its pad on the cell's - terminal"},
            **self._cell_words(), "source": self.source(b)}
        self.unseen.append("the cell's own connector: no part from a Battery library and no "
                           "BT ref is on the boards named, so pcb_batteries.cell names none "
                           "(name its board, if it is another, with --board)")
        return ("cell", b, None, set())

    @staticmethod
    def _cell_words() -> dict:
        return {
            "chemistry": FILL + "one of li-ion, li-polymer, lifepo4, lto, nimh, li-primary, "
                                "alkaline, from the cell's datasheet",
            "series": FILL + "the cells in series, a whole number (two or more: add "
                             "terminals.taps, the cell-junction pads from the lowest up)",
            "parallel": FILL + "the cells in parallel, a whole number",
            "cell": {
                "upper_charge_v": FILL + "the cell's upper limit charging voltage, with "
                                         "its tolerance, V per cell, from its datasheet",
                "discharge_v_min": FILL + "the cell's end (cut-off) voltage, V per cell, "
                                          "from its datasheet",
                "charge_a_max": FILL + "the cell's maximum charge current, A per cell, "
                                       "from its datasheet",
                "charge_temp_c": FILL + "[lo, hi], the widest window the cell's datasheet "
                                        "allows any charge in, C",
                "short_a": FILL + "[least, most] current the pack drives into a short, A, "
                                  "from the cell's and the pack's datasheets (or drop short_a "
                                  "and give internal_resistance_ohm [least, most])"}}

    @staticmethod
    def _ntc_numbers(what: str) -> dict:
        return {
            "r25_ohm": FILL + f"the resistance at 25 C of {what}, ohm, from its datasheet",
            "r25_tol_pct": FILL + "its R25 tolerance, %",
            "beta_k": FILL + "its B constant, K (or drop beta_k and beta_pair_c and give "
                             "rt_table_c_ohm, the maker's [[C, ohm], ...])",
            "beta_pair_c": FILL + "[t1, t2], the temperatures its B constant is for, C "
                                  "(B25/50 is [25, 50])",
            "beta_tol_pct": FILL + "its B constant's tolerance, % (a table's too)"}

    def _protector(self, b: Board, fp, kind: str, functions: dict) -> dict:
        return {"battery": None, "kind": kind, "where": "board", "board": b.rel,
                "ref": fp.ref, "value": fp.value, "functions": functions,
                "source": self.source(b)}

    def _chargers(self) -> list[str]:
        out = []
        for b in self.boards:
            for fp in b.fps:
                if connector(fp):
                    continue
                names = b.names(fp)
                cell = [n for n in CELL_PINS if n in names]
                cur = [n for n in CURRENT_PINS if n in names]
                therm = [n for n in THERM_PINS if n in names]
                if not cell or not (cur or therm):
                    continue
                functions = {"charge_v": {
                    "v_reg_v": FILL + "[min, max] V from the datasheet: the regulation voltage "
                                      "of this part's voltage option (or replace v_reg_v with "
                                      "divider {vfb_v, top_ref, bottom_ref, tol_pct} where "
                                      "resistors set it)",
                    "per_cell": FILL + "true when it regulates each cell, false when it "
                                       "regulates the whole pack"}}
                functions["charge_a"] = self._charge_a(b, fp, names, cur)
                if therm:
                    functions["charge_temp"] = self._charge_temp(b, fp, names, therm)
                pid = self.rid(b, fp.ref)
                self.records["pcb_protectors"][pid] = self._protector(b, fp, "charger",
                                                                      functions)
                said = ", ".join(f"{n} ({', '.join(names[n])})" for n in cell + cur + therm)
                self.note(f"pcb_protectors.{pid}", f"pins {said}", b, fp)
                if not therm:
                    self.unseen.append(f"{fp.ref}'s charge temperature: it has no "
                                       f"{'/'.join(THERM_PINS)} pin, so no charge_temp is "
                                       f"drafted; what stops charging outside the cell's "
                                       f"window is yours to name")
                out.append(pid)
        return out

    def _charge_a(self, b: Board, fp, names: dict, cur: list) -> dict:
        if len(cur) != 1 or len(names[cur[0]]) != 1:
            return {"a_max": FILL + "the most charge current the datasheet guarantees, A (or "
                                    "replace a_max with prog {ref, k_v, r_tol_pct} for the "
                                    "resistor that sets it)"}
        pad = names[cur[0]][0]
        net = b.net(fp, pad)
        parts = [p for p in b.parts_on(net, {fp.ref}) if len(b.pads_of(p)) == 2] if net else []
        ref = (parts[0].ref if len(parts) == 1 else
               FILL + f"the resistor that sets the current on {fp.ref} pad {pad} ({cur[0]}, on "
                      f"{net or 'no net'}: {b.around(net, {fp.ref}) if net else 'nothing'})")
        return {"prog": {"ref": ref,
                         "k_v": FILL + "[min, max] V from the datasheet: the current is k / R "
                                       "(a datasheet's I = 1000 V / R is k 1000, at its "
                                       "guaranteed corners)",
                         "r_tol_pct": FILL + "that resistor's tolerance from the BOM, %"}}

    def _charge_temp(self, b: Board, fp, names: dict, therm: list) -> dict:
        if len(therm) == 1 and len(names[therm[0]]) == 1:
            pad = names[therm[0]][0]
        else:
            pad = None
        net = b.net(fp, pad) if pad else ""
        sense = {
            "pin": pad or FILL + f"the pad of {fp.ref} that senses the thermistor (pins "
                                 f"{', '.join(therm)})",
            "kind": FILL + "current (the pin sources a bias current) or ratio (a divider "
                           "from a reference), from the datasheet",
            "i_a": FILL + "for current: the bias current [min, max], A (drop for ratio)",
            "cold_v": FILL + "for current: the cold trip voltage [min, max], V (drop for "
                             "ratio)",
            "hot_v": FILL + "for current: the hot trip voltage [min, max], V (drop for ratio)",
            "pullup_ref": FILL + "for ratio: the divider's pull-up resistor's ref (drop for "
                                 "current)",
            "pullup_tol_pct": FILL + "for ratio: its tolerance, % (drop for current)",
            "cold_frac": FILL + "for ratio: the cold trip as a fraction of the reference "
                                "[min, max] (drop for current)",
            "hot_frac": FILL + "for ratio: the hot trip as a fraction [min, max] (drop for "
                               "current)"}
        battery_pads = [(p, pad_) for p, pad_ in b.on.get(net, [])
                        if p.ref != fp.ref and (p.lib_id.split(":")[0].startswith("Battery")
                                                or _BATTERY_REF.match(p.ref))] if net else []
        parts = [p for p in b.parts_on(net, {fp.ref}) if len(b.pads_of(p)) == 2
                 and not any(p is q for q, _ in battery_pads)] if net else []
        if battery_pads:
            ntc = FILL + (f"{{\"battery_therm\": true}} when the pack's NTC, on "
                          f"{battery_pads[0][0].ref} pad {battery_pads[0][1].number}, is the "
                          f"sensor; else the board NTC's {{ref, value, r25_ohm, ...}}")
        elif len(parts) == 1:
            ntc = {"ref": parts[0].ref, "value": parts[0].value,
                   **self._ntc_numbers(f"{parts[0].ref} {parts[0].value}")}
        else:
            ntc = FILL + (f"the thermistor on {fp.ref}'s sense pin: {{ref, value, r25_ohm, "
                          f"r25_tol_pct, beta_k, beta_pair_c, beta_tol_pct}} (on "
                          f"{net or 'no net'}: {b.around(net, {fp.ref}) if net else 'nothing'})")
        return {"sense": sense, "ntc": ntc}

    def _protectors(self) -> list[str]:
        out = []
        for b in self.boards:
            for fp in b.fps:
                if connector(fp):
                    continue
                names = b.names(fp)
                pair = next(((d, c) for d, c in CUTOFF_PAIRS if d in names and c in names),
                            None)
                if pair is None:
                    continue
                dis, chg = pair
                sense = self._sense(b, fp, names)
                functions = {
                    "overcharge_v": {
                        "v_max": FILL + "the most overcharge detection voltage the datasheet "
                                        "guarantees, V",
                        "per_cell": FILL + "true when it holds each cell, false when it holds "
                                           "the pack",
                        "sense": sense, "acts": self._acts(b, fp, names, chg)},
                    "overdischarge_v": {
                        "v_min": FILL + "the least overdischarge detection voltage the "
                                        "datasheet guarantees, V",
                        "per_cell": FILL + "true when it holds each cell, false when it holds "
                                           "the pack",
                        "sense": dict(sense), "acts": self._acts(b, fp, names, dis)}}
                pid = self.rid(b, fp.ref)
                held = self.records["pcb_protectors"].get(pid)
                if held is not None:
                    # One element, every function it serves, in one record: a
                    # charger with its own cutoff pins is not drafted twice.
                    held["functions"].update(functions)
                else:
                    self.records["pcb_protectors"][pid] = self._protector(b, fp, "protector",
                                                                          functions)
                self.note(f"pcb_protectors.{pid}",
                          f"pins {dis} ({', '.join(names[dis])}) and {chg} "
                          f"({', '.join(names[chg])})", b, fp)
                out.append(pid)
        return out

    def _sense(self, b: Board, fp, names: dict) -> dict | str:
        name, pad = _one(names, SENSE_PINS)
        if pad is None:
            return FILL + (f"how {fp.ref} senses the cell: {{pin, through}} for one cell, "
                           f"{{taps}} for several (pads: {b.said(fp)})")
        net = b.net(fp, pad)
        return {"pin": pad,
                "through": [FILL + f"the refs in series from the cell's + to {fp.ref} pad "
                                   f"{pad} ({name}), in order; [] when it sits on the cell's + "
                                   f"(on {net or 'no net'}: "
                                   f"{b.around(net, {fp.ref}) if net else 'nothing'})"]}

    def _acts(self, b: Board, fp, names: dict, which: str) -> dict | str:
        pads = names[which]
        if len(pads) != 1:
            return FILL + f"the pad of {fp.ref} named {which} and the switch it drives"
        pad = pads[0]
        net = b.net(fp, pad)
        parts = b.parts_on(net, {fp.ref}) if net else []
        if len(parts) == 1:
            sw = parts[0]
            control = [p.number for p in sw.pads if p.net == net and p.number]
            control = sorted(set(control), key=_order)
            switch = {"ref": sw.ref, "value": sw.value,
                      "control": (control[0] if len(control) == 1 else
                                  FILL + f"the pad of {sw.ref} that {which} drives"),
                      "path": FILL + f"the two pads of {sw.ref} the cell's current flows "
                                     f"through, [p, q] (pads: {b.said(sw)})"}
        else:
            switch = FILL + (f"the switch {fp.ref}'s {which} drives: {{ref, value, control, "
                             f"path}} (on {net or 'no net'}: "
                             f"{b.around(net, {fp.ref}) if net else 'nothing'})")
        return {"pin": pad, "switch": switch}

    def _fuses(self, batteries: list) -> list[tuple]:
        """``(id, board, the net it feeds)`` of each fuse on a battery's net."""
        out = []
        cell_nets = {(bb.path, n) for _bid, bb, _fp, nets in batteries for n in nets}
        for b in self.boards:
            for fp in b.fps:
                if not (fp.lib_id.split(":")[0].startswith("Fuse") or _FUSE_REF.match(fp.ref)):
                    continue
                pads = b.pads_of(fp)
                if len(pads) != 2:
                    continue
                nets = [b.net(fp, n) for n in pads]
                on = [i for i in (0, 1) if nets[i] and (b.path, nets[i]) in cell_nets]
                if len(on) != 1:
                    continue
                near, far = on[0], 1 - on[0]
                pid = self.rid(b, fp.ref)
                kind = FILL + "fuse, ptc or efuse, from its datasheet"
                self.records["pcb_protectors"][pid] = self._protector(b, fp, kind, {"trip": {
                    "kind": kind,
                    "terminal": FILL + (f"pos or neg: the battery terminal whose net pad "
                                        f"{pads[near]} is on ({nets[near]})"),
                    "path_pins": [pads[near], pads[far]],
                    "hold_a": FILL + "the current it holds, A (drop for a fuse with none "
                                     "stated)",
                    "trip_a": FILL + "the current by which it is sure to trip, A",
                    "trip_max_s": FILL + "the datasheet's guaranteed trip points, [[amperes, "
                                         "maximum seconds], ...]",
                    "interrupt_a": FILL + "the most current it is rated to break, A"}})
                lib = fp.lib_id.split(":")[0]
                by = f"library {lib}" if lib.startswith("Fuse") else f"ref {fp.ref}"
                self.note(f"pcb_protectors.{pid}",
                          f"{by}, pad {pads[near]} on the battery's {nets[near]}", b, fp)
                out.append((pid, b, nets[far]))
        return out

    def _conductors(self, batteries: list, fuses: list, battery: str) -> set:
        """Draft a conductor per pin group of a connector on a net a battery drives;
        return those nets, ``(board path, net)``."""
        driven = {(bb.path, n) for _bid, bb, _fp, nets in batteries for n in nets}
        feeds = {}
        for pid, b, far in fuses:
            if far:
                driven.add((b.path, far))
                feeds.setdefault((b.path, far), []).append(pid)
        own = {(bb.path, fp.ref) for _bid, bb, fp, _n in batteries if fp is not None}
        for b in self.boards:
            for fp in b.fps:
                if not connector(fp) or (b.path, fp.ref) in own:
                    continue
                for net in sorted({b.net(fp, n) for n in b.pads_of(fp)} - {""}):
                    if (b.path, net) not in driven:
                        continue
                    pins = [n for n in b.pads_of(fp) if b.net(fp, n) == net]
                    cid = self.fresh("pcb_conductors", self.rid(b, fp.ref, net))
                    guards = feeds.get((b.path, net)) or [
                        FILL + f"the pcb_protectors ids that guard {fp.ref} on {net}: no "
                               f"drafted fuse feeds it"]
                    self.records["pcb_conductors"][cid] = {
                        "battery": battery, "board": b.rel,
                        "kind": FILL + "wire, ffc or contact",
                        "ref": fp.ref, "value": fp.value, "pins": pins,
                        "area_mm2": FILL + "one conductor's copper cross-section, mm^2 (its "
                                           "cable's or contact's datasheet)",
                        "start_c": FILL + "its temperature in service, C",
                        "limit_c": FILL + "the most its insulation or contact takes in a "
                                          "short, C",
                        "continuous_a": FILL + "the current it carries continuously, A",
                        "protected_by": guards, "source": self.source(b)}
                    self.note(f"pcb_conductors.{cid}",
                              f"connector {fp.ref} pin(s) {', '.join(pins)} on {net}, which a "
                              f"battery drives", b)
        return driven

    def _usbc(self) -> set:
        out = set()
        for b in self.boards:
            for fp in b.fps:
                if not typec_receptacle(fp):
                    continue
                numbers = set(b.pads_of(fp))
                names = b.names(fp)
                rec = {"board": b.rel, "ref": fp.ref, "value": fp.value}
                pins = {}
                for spec, name in (("A5", "CC1"), ("B5", "CC2")):
                    if spec not in numbers:
                        got = names.get(name, [])
                        pins[spec] = (got[0] if len(got) == 1 else
                                      FILL + f"the pad of {fp.ref} that is Type-C {spec} "
                                             f"({name})")
                if pins:
                    rec["pins"] = pins
                cc = []
                for spec, name in (("A5", "CC1"), ("B5", "CC2")):
                    pad = spec if spec in numbers else pins.get(spec)
                    net = b.net(fp, pad) if pad and not pad.startswith(FILL) else ""
                    cc.append(f"{name} on {net or 'no net'}"
                              + (f" meets {b.around(net, {fp.ref})}" if net else ""))
                data = [f"{s} on {b.net(fp, s) or 'no net'}" for s in TYPEC_DATA if s in numbers]
                rec.update({
                    "role": FILL + f"sink, source or drp ({'; '.join(cc)})",
                    "usb2": FILL + (f"true when it carries USB 2.0 data ({', '.join(data)}), "
                                    f"false when not" if data else
                                    "false: it has no data pads (A6, B6, A7, B7)"),
                    "reads_rp": FILL + "for a sink: true when it reads the source's "
                                       "advertisement to draw more than default power (Rd "
                                       "held to +-10 %), false when it never does (+-20 %); a "
                                       "source takes rp and vbus_switch instead, a drp cc_by "
                                       "and vbus_switch",
                    "cc_resistor_tol_pct": FILL + "from the BOM: the CC resistors' "
                                                  "tolerance, %",
                    "source": self.source(b)})
                uid = self.rid(b, fp.ref)
                self.records["pcb_usbc"][uid] = rec
                vbus = sorted({b.net(fp, s) for s in TYPEC_VBUS if s in numbers}
                              | {b.net(fp, n) for n in names.get("VBUS", [])} - {""})
                out |= {(b.path, n) for n in vbus}
                self.note(f"pcb_usbc.{uid}", "a USB-C receptacle, as pcb.usbc_receptacle "
                          "finds one", b, fp)
        return out

    def _rails(self) -> set:
        out = set()
        drafted = {(r["board"], r["ref"]) for r in self.records["pcb_protectors"].values()}
        for b in self.boards:
            for fp in b.fps:
                if connector(fp) or (b.rel, fp.ref) in drafted:
                    continue
                names = b.names(fp)
                _o, opad = _one(names, RAIL_OUT)
                _i, ipad = _one(names, RAIL_IN)
                if opad is None or ipad is None:
                    continue
                net = b.net(fp, opad)
                if not net:
                    continue
                grounds = sorted({b.net(fp, n) for g in RETURN_PINS for n in names.get(g, [])}
                                 - {""})
                rid = self.fresh("pcb_rails", self.rid(b, fp.ref, net))
                self.records["pcb_rails"][rid] = {
                    "board": b.rel, "net": net,
                    "nominal_v": FILL + f"the voltage {fp.ref} holds {net} at, V, from its "
                                        f"datasheet",
                    "return": (grounds[0] if len(grounds) == 1 else
                               FILL + f"the net {net} returns on (pads: {b.said(fp)})"),
                    "source": self.source(b)}
                out.add((b.path, net))
                self.note(f"pcb_rails.{rid}", f"pins {_o} ({opad}) and {_i} ({ipad})", b, fp)
        return out

    def _matings(self, batteries: list, sources: set) -> None:
        own = {(bb.path, fp.ref): bid for bid, bb, fp, _n in batteries if fp is not None}
        modes = (FILL + "the wrong ways it can meet, [] when it cannot (and why in source): "
                        "reversed, rotate, flipped_type, {narrower: N}, {offset: k}, "
                        "{slide: [pins]}, {bridges: [[p, q]]} (references/mismate.md)")
        for b in self.boards:
            for fp in b.fps:
                if not connector(fp):
                    continue
                side = {"board": b.rel, "ref": fp.ref, "value": fp.value}
                mid = self.rid(b, fp.ref)
                if (b.path, fp.ref) in own:
                    bid = own[(b.path, fp.ref)]
                    self.records["pcb_matings"][mid] = {
                        "a": side, "b": {"battery": bid},
                        "modes": FILL + "[] when the cell's housing is keyed, or [\"reversed\"] "
                                        "when its pigtail can meet it the other way (say which "
                                        "in source)",
                        "source": self.source(b)}
                    self.note(f"pcb_matings.{mid}", f"the battery {bid}'s own connector", b)
                    continue
                carries = sorted({b.net(fp, n) for n in b.pads_of(fp)
                                  if (b.path, b.net(fp, n)) in sources})
                if not carries:
                    continue
                self.records["pcb_matings"][mid] = {
                    "a": side,
                    "b": FILL + (f"what meets {fp.ref}: {{board, ref, value}} of the connector "
                                 f"at the other end, {{battery: id}} for a cell's pigtail, or "
                                 f"{{external: what}} for anything off every board (a "
                                 f"charger's plug, a soldered lead)"),
                    "map": FILL + "when b is a connector on a board: straight, reversed or "
                                  "{a_pin: b_pin}; drop map otherwise",
                    "modes": modes, "source": self.source(b)}
                self.note(f"pcb_matings.{mid}",
                          f"connector {fp.ref} carries {', '.join(carries)}, which "
                          f"pcb.mismate's coverage rule asks a mating of", b)

    # -- the summary ----------------------------------------------------- #
    def fills(self) -> list[str]:
        """The path of every FILL value in the draft."""
        out: list[str] = []

        def walk(node, path: str) -> None:
            if isinstance(node, dict):
                for k, v in node.items():
                    walk(v, f"{path}.{k}" if path else k)
            elif isinstance(node, list):
                for i, v in enumerate(node):
                    walk(v, f"{path}[{i}]")
            elif isinstance(node, str) and node.startswith(FILL):
                out.append(path)
        walk(self.records, "")
        return out


#: What the draft cannot see on any board, said in every summary.
UNSEEN = (
    "an element inside the pack (its protection board, its NTC's numbers): add it to "
    "pcb_protectors with where: in-pack, from the pack's drawing",
    "a stop that firmware makes (a gauge's window, an ADC check): add it with where: "
    "firmware; the rules name it and never count it",
    "which connector meets which, and how else it can meet: each pcb_matings record's b, "
    "map and modes are yours",
    "a part whose pads carry no pin name: a board drawn with no library symbols names none, "
    "and nothing here finds it by its pins",
)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="pcb.battery_record_draft",
                                 description=__doc__.splitlines()[0])
    ap.add_argument("--board", action="append", required=True,
                    help="a .kicad_pcb of the design, relative to the project (repeat for "
                         "each board)")
    args = ap.parse_args(argv)

    out_dir = os.environ.get("NOPEKIT_PROPOSAL_DIR")
    if not out_dir:
        print("pcb.battery_record_draft: run me with `nopekit propose "
              "pcb.battery_record_draft -- --board ...` ($NOPEKIT_PROPOSAL_DIR is unset)",
              file=sys.stderr)
        return 2
    root = os.getcwd()
    problems, boards, seen = [], [], set()
    for given in args.board:
        path = os.path.abspath(given)
        if not path.endswith(".kicad_pcb") or not os.path.isfile(path):
            problems.append(f"no KiCad board at {given}")
            continue
        if path in seen:
            problems.append(f"{given} is named twice")
            continue
        seen.add(path)
        try:
            b = Board(given, root)
        except (K.KicadFormatError, UnicodeDecodeError, ValueError) as exc:
            problems.append(f"{given} does not parse: {exc}")
            continue
        if b.problems:
            problems.append(f"{given} is not a board the power rules read ("
                            f"{'; '.join(b.problems)}): they would refuse or skip every "
                            f"record drafted from it")
            continue
        boards.append(b)
    if problems:
        print(f"pcb.battery_record_draft: {len(problems)} problem(s), nothing written:",
              file=sys.stderr)
        for p in problems:
            print(f"  {p}", file=sys.stderr)
        return 2
    draft = Draft(boards)
    draft.build()
    if not any(draft.records[k] for k in RECORDS):
        print(f"pcb.battery_record_draft: nothing to draft on {', '.join(b.rel for b in boards)}: "
              f"no battery, charger, protector, fuse, USB-C receptacle or regulator was "
              f"found by its library, ref or pin names; nothing written", file=sys.stderr)
        return 2
    fills = draft.fills()
    stem = boards[0].stem
    summary = {
        "proposer": "pcb.battery_record_draft",
        "draft": f"{stem}.power_records.json",
        "boards": {b.rel: _sha256(b.path) for b in boards},
        "candidates": draft.candidates,
        "fill": fills,
        "not_fitted": {b.rel: b.unfitted for b in boards if b.unfitted},
        "cannot_see": [*draft.unseen, *UNSEEN],
        "judged_by": "nothing: a draft settles nothing. Every power rule refuses a FILL value "
                     "where it reads a number, a flag or a word, so an adopted draft errors "
                     "until a person states each one; then the rules bind each record to "
                     "the boards and judge it",
        "adopt": "copy the records you keep into the project's params, state every FILL from "
                 "the part's datasheet or the design, add what this cannot see, and say in "
                 "each source where its numbers come from"}
    with open(os.path.join(out_dir, f"{stem}.power_records.json"), "w",
              encoding="utf-8") as fh:
        json.dump(draft.records, fh, indent=2)
        fh.write("\n")
    with open(os.path.join(out_dir, f"{stem}.power_draft.json"), "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2, sort_keys=True)
        fh.write("\n")
    counts = ", ".join(f"{k} {len(draft.records[k])}" for k in RECORDS)
    print(f"pcb.battery_record_draft: drafted {counts}; {len(fills)} FILL value(s) are yours "
          f"to state; a draft settles nothing")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
