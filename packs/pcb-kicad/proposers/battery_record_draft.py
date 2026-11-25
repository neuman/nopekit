# SPDX-License-Identifier: Apache-2.0
"""pcb.battery_record_draft — write a DRAFT of a design's power records from its boards.

    nopekit propose pcb.battery_record_draft -- --board hardware/power.kicad_pcb \\
        [--board hardware/main.kicad_pcb] [--battery J1[:4,1]]

A proposer, never an evaluator: it writes a draft, and a draft settles nothing. The
power rules (``pcb.charge_temperature``, ``pcb.charge_limits``, ``pcb.fault_current``,
``pcb.discharge_cutoff``, ``pcb.mismate``, ``pcb.usbc_receptacle``) judge records a
person enters: which cell, which charger, which fuse, which connector meets which,
each with its datasheet numbers. Those records are the heaviest part of first use.
This writes their skeleton from what the boards already say, in the shapes the
rules read (``gates/_power.py``, ``references/battery.md``, ``mismate.md``,
``usb-c.md``): objects keyed by id under ``pcb_batteries``, ``pcb_protectors``,
``pcb_conductors``, ``pcb_matings``, ``pcb_usbc`` and ``pcb_rails``.

**What it fills, from the boards:** each record's board, ref and value, ``where:
board`` for a part on a board, and the pads a part's pin names or its copper
identify (a charger's thermistor pin, the resistor on its PROG pin, a protector's
DO and CO pins and the switch each drives, a fuse's two pads). **What it never
fills:** every datasheet number, every flag and every choice that is the person's
word (a chemistry, a cell count, what kind of element a part is, a role, a
mating's far side and modes). Each is a string that starts ``FILL:`` and says what
to state and what the board shows beside it. No number and no true or false is
anywhere in the draft: a guessed value would read as a fact the moment it is
adopted. Every power rule refuses a ``FILL:`` value where it reads a number, a
flag or a word (an error naming it), so an adopted draft passes nothing until
every one is stated.

**Every record's source is a list** holding one ``FILL:`` that says it is a draft
(:meth:`Draft.source`). Every power rule refuses a source that is not a string, so
a record passes only once a person writes, in place of the list, where its
numbers come from.

**Where candidates come from** (each with its evidence in the summary):

- a battery: a part named with ``--battery REF`` (``REF:POS,NEG`` gives its pads),
  a footprint from KiCad's ``Battery*`` libraries or with a ref BT and a number, or
  a connector whose value or footprint name says battery (:data:`BATTERY_WORDS`:
  a LiPo's housing valued "Battery", a battery holder) or whose pins are named +
  and -, listed under ``keep_or_delete``; its pads named ``+`` and ``-`` (KiCad's
  battery symbols) fill pos and neg.
  With no pad named ``-``, a net of its pads that a drafted charger's or
  protector's ground pin sits on is its return;
- a charger: a part with a cell pin (:data:`CELL_PINS`) and a charge-current or a
  thermistor pin (:data:`CURRENT_PINS`, :data:`THERM_PINS`), or a charge input
  alone (:data:`CHARGE_INPUT_PINS`, a PMIC's way), listed under
  ``keep_or_delete``; on a KiCad 10 board each pin name is read without the pad
  number KiCad 10 appends (``_kicad.numbered_pin_names``);
- a protector: a part with a cutoff pair (:data:`CUTOFF_PAIRS`) and not the
  inputs that pair with it on a data line (:data:`CUTOFF_INPUTS`);
- a fuse: a two-pad part from KiCad's ``Fuse*`` libraries or with a ref F and a
  number, one pad on a battery's net;
- a conductor: each pin group of a connector on a net a battery drives: its pads'
  nets but its ``-``, its thermistor and its return, and the far side of a drafted
  fuse on one of those;
- a USB-C receptacle: as ``pcb.usbc_receptacle`` finds one;
- another USB connector's VBUS (micro-B, mini-B, a USB-A plug): a rail on the
  net of its pin named VBUS, returning on its pin named GND;
- a rail: a part with a regulator's output and input pins (:data:`RAIL_OUT`,
  :data:`RAIL_IN`), every output pin on one net and every input pin on another,
  drafted on its output's net and listed under ``keep_or_delete``: a filter, a
  voltage reference or a driver carries the same names;
- a mating: every connector on a net a battery, a VBUS or a drafted rail drives
  (``pcb.mismate``'s coverage rule asks a mating of each), and each battery's own.

**What it cannot see**, said in the summary: an element inside the pack (its
protection board), a stop that firmware makes, which connector meets which, a cell
on a board not named, a part whose pins carry no name, and each regulator whose
output or input pins sit on several nets (named). **What it may draft wrong**,
listed under ``keep_or_delete``: each rail, and the conductors and matings on a
cell's nets when nothing names or shows which pad is its ``-``.

With more than one board, each id starts with its board's file name, and with the
folders above it where two boards share a name (:func:`prefixes`).

It refuses, exits 2 and writes nothing when ``$NOPEKIT_PROPOSAL_DIR`` is unset, a
board is missing, named twice, does not parse, is one the power rules refuse or
skip (``_kicad.board_problems``), or holds nothing to draft. **What it writes**
under ``$NOPEKIT_PROPOSAL_DIR``: ``<stem>.power_records.json``, the draft (only the
six keys), and ``<stem>.power_draft.json``, the summary: each candidate with the
pins and nets that found it, every ``FILL:`` value's path, the records to keep or
delete, what it cannot see, and each board's sha256. Standard library and the
pack's reader only.
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
#: refuses a string, quoting it, so the rule's error names what to fill. A
#: record's ``source`` holds its FILL in a list (:meth:`Draft.source`): the rule
#: that reads a source takes any string.
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

#: A charger's input, by pin name: a part with a cell pin and one of these, and
#: neither a charge-current nor a thermistor pin, is drafted as a charger too,
#: and listed under ``keep_or_delete``. A PMIC or a charger whose current and
#: window its registers set has neither pin (holdout round 2: a watch's PMIC,
#: whose current is set over I2C). Survey, KiCad 9.0.9's stock symbols: of the 58
#: in ``Battery_Management`` with a cell pin, 23 carry no PROG or thermistor pin,
#: and 8 of those carry one of these (VIN 3, energy harvesters that charge a
#: cell; DCIN 4 and IN 1, chargers); none of its gauges or protectors does.
#: Outside it, 11 symbols carry a cell pin beside one of these and charge
#: nothing (USB PHYs and MSP430s with VBAT and VBUS, MCU modules with VBAT and
#: VIN, a touch controller with VBAT and IN): the VBAT there is a backup supply,
#: which is why a part found this way alone is one to keep or delete.
#: *Rejected:* SYS (a power path's output, which a charger drives); VCC (a gauge's
#: supply, BQ78350); CHG (a charger's status pin on 24 symbols, and a protector's
#: charge switch).
CHARGE_INPUT_PINS = ("VBUS", "VIN", "IN", "DCIN")

#: A protector's cutoff pins, ``(discharge, charge)``, by the same survey: 8
#: symbols carry a pair, a one-cell protector's DO and CO (2), another family's
#: DOUT and COUT (1), and DSG and CHG (5: a fuel gauge, a FET driver and three
#: multi-cell monitors, so what kind of element it is stays a FILL). Only the
#: pair counts: CHG alone is a charger's status pin on 24 symbols there.
CUTOFF_PAIRS = (("DO", "CO"), ("DOUT", "COUT"), ("DSG", "CHG"))

#: The inputs beside a cutoff pair's names on a part that is no protector: an
#: addressable LED (``LED:APA102``), eight line drivers (``Interface:TB5D*``,
#: ``TB5R*``) and a level shifter (``4xxx:4504``) name their data and clock
#: outputs DO and CO, or DOUT and COUT, beside inputs DI and CI, or DIN and CIN.
#: Those are all 10 stock symbols outside ``Battery_Management`` with a cutoff
#: pair, and none of the 8 inside it has the inputs. A part with both inputs of
#: its pair is drafted as no protector. (What slipped through, found in review of
#: P9 while this table's survey was written: a board of addressable LEDs drafted
#: a protector per LED.)
CUTOFF_INPUTS = {("DO", "CO"): ("DI", "CI"), ("DOUT", "COUT"): ("DIN", "CIN")}

#: A protector's pin that senses the cell, by the same library: of its 8
#: symbols with a cutoff pair, BAT on 6 and VDD on 2. Rejected: VC1 to VC15 (a
#: multi-cell monitor's taps, several pins, which the draft asks for as
#: ``taps``), VSS (its return, on all 8) and VCC (on no protector there: it is
#: the supply of the LED and the line drivers :data:`CUTOFF_INPUTS` turns away).
SENSE_PINS = ("VDD", "BAT")

#: A battery's terminals by pin name. + and -: the pins of the two symbols KiCad
#: 9.0.9 gives a BT reference (``Device:Battery``, ``Device:Battery_Cell``), the
#: only stock symbols that draw a cell. BAT+, BATT+, B+ and their - forms: a
#: pack's connector as a person draws it (no stock battery symbol uses them;
#: BATT+ and BATT- are the cell pins of 10 chargers in ``Battery_Management``,
#: which this never reads as a battery). Read on a battery's footprint only.
#: Rejected: P+ and P- (a protection module's pack side, beyond its switch, which
#: is not the cell's terminal).
POS_PINS = ("+", "BAT+", "BATT+", "B+")
NEG_PINS = ("-", "BAT-", "BATT-", "B-")

#: A battery's thermistor pin, by name. No stock symbol draws a cell with one
#: (the two above have + and - only), so this reads a pack's connector as a
#: person draws it: :data:`THERM_PINS`'s names, and T and TH, the short forms a
#: person writes for a three-wire pack's thermistor lead. Read on a battery's
#: footprint only, so a T on any other part is never read here. Rejected: the
#: pad that is neither + nor - (a pack's third pin may be a cell tap or an
#: identification pin, not a thermistor).
BATTERY_THERM_PINS = ("T", "TH") + THERM_PINS

#: A regulator, by its pins: an output and an input, on a part that is no
#: connector and that no other pass drafted. Survey, KiCad 9.0.9's stock symbols:
#: 2,325 carry a name of each (OUT and IN 1040, VOUT and VIN 638, VO and VI 499,
#: OUT and VIN 117, others 14, and 17 with two output names). 1,882 of them are in
#: the regulator libraries (Regulator_Linear 1447, Regulator_Switching 262,
#: Power_Management 135, Regulator_SwitchedCapacitor 18, Converter_DCDC 17,
#: Regulator_Controller 3). The rest are no regulator: RF_Filter 199 (IN, OUT,
#: GND, as an L7805 is), Reference_Voltage 147, Sensor_Current 20, Driver_FET 8,
#: Timer 4 and others; and Battery_Management 25 and Power_Protection 22, a
#: charger (drafted as one first) or a switch whose output is a rail. A board
#: names the footprint's library, never the symbol's, and an LDO shares its
#: packages with a filter and a reference, so nothing on the board tells them
#: apart: each rail drafted is listed under ``keep_or_delete``.
#: An output or an input may span pins (TPS74801: IN on 1 and 2, OUT on 9 and
#: 10) when they all sit on one net. (What slipped through: the rule took one pin
#: of each, so it skipped 114 Regulator_Linear, 25 Regulator_Switching and 50
#: Power_Management symbols, and said nothing.) A part whose output or input pins
#: sit on several nets (a current sensor's OUT and VOUT) is named in
#: ``cannot_see``. Rejected: SW (a switching regulator's switch node, which
#: swings from its input to ground: its rail is beyond the inductor, on no pin of
#: its own; 289 regulator symbols name SW and no output), VCC and VDD (a supply
#: input on nearly every part), FB (a feedback input).
RAIL_OUT = ("VOUT", "VO", "OUT")
RAIL_IN = ("VIN", "VI", "IN")

#: A part's ground, its return: a rail's return when its pins sit on one net, and
#: the cell's return when a drafted charger's or protector's sits on a net of the
#: cell's pads. Survey: of the 1,882 regulator symbols above, GND on 1728, PGND
#: on 50, VSS on 4 (117 have none: an adjustable regulator's ADJ is no return,
#: and their return is left to fill); of the 88 ``Battery_Management`` symbols
#: the draft takes for a charger or a protector, VSS on 50, GND on 33, PGND on 8.
#: Rejected: SGND and AGND (a quiet ground for a reference, on 28 and 10
#: regulators: where it sits on a net of its own, the current returns on PGND),
#: EP and PAD (an exposed pad sits on ground on most parts, on the input or on
#: nothing on others).
RETURN_PINS = ("GND", "VSS", "PGND")

#: The connectors ``pcb.mismate``'s coverage rule asks a mating of, as
#: ``gates/power.py`` holds them (``CONNECTOR_LIBS``, ``_CONNECTOR_REF``); copied,
#: since a proposer imports no gate, and held equal by tests/test_pcb_kicad.py.
CONNECTOR_LIBS = ("Connector", "Battery")
_CONNECTOR_REF = re.compile(r"^(J|P|CN|BT)\d+$")

#: A battery's holder or connector, and a fuse, by ref: BT and F, the references
#: KiCad 9.0.9 gives its stock symbols (BT: ``Device:Battery`` and
#: ``Device:Battery_Cell``, the two that draw a cell; F: all 31 fuse, polyfuse
#: and TBU symbols, in ``Device`` and ``Power_Protection``), and IEEE 315's
#: letters for both. Rejected: B and G (each the reference of one SPICE source in
#: the stock libraries, and of no cell), FU (of no stock symbol).
_BATTERY_REF = re.compile(r"^BT\d+$")
_FUSE_REF = re.compile(r"^F\d+$")

#: Words in a connector's value or footprint name that say it meets a cell: a
#: hobby LiPo's two-pin housing valued "Battery", a holder from a local library
#: valued "1020_battery_holder", a coin-cell or AA holder. Read on a connector
#: only (:func:`connector`), so a BAT54 diode or a cell-phone part is never
#: read. What slipped through (two holdout runs): a battery was found by
#: KiCad's ``Battery*`` libraries or a BT ref alone, so a J1 valued "Battery"
#: and a J1 holder from a local library were each missed, and one board drafted
#: nothing at all. *Rejected:* any part whose value holds BAT (BAT54, BAT760
#: and other diodes); "holder" alone (a card or SIM holder is no cell's); a
#: vendor's part numbers (a list no one keeps whole).
BATTERY_WORDS = re.compile(r"(?i)batt|lipo|li-po|li-ion|\bbat\b|\bcell\b|"
                           r"\baaa?\b|\bcr\d{4}\b|\b1[48]650\b|\b21700\b")

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


def _slug(text: str) -> str:
    """``text`` as a piece of a record id: lower case, each run of other
    characters one underscore."""
    return re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_").lower()


def _nets(names) -> str:
    return ", ".join(names) if names else "no net"


def prefixes(rels: list[str]) -> list[str]:
    """Each board's id prefix, from its path relative to the project: its file
    name, and where two boards' names make one prefix, the folders above it as
    well, nearest first, until each is its own; a number after any that still
    meet (two paths that differ only in case or punctuation). (What slipped
    through: the prefix was the file name alone, so ``a/main.kicad_pcb`` and
    ``b/main.kicad_pcb`` drafted one set of ids, and the second board's battery,
    protectors, receptacle and matings replaced the first's with no word.)"""
    parts = []
    for rel in rels:
        stem = rel[:-len(".kicad_pcb")] if rel.endswith(".kicad_pcb") else rel
        parts.append([s for s in (_slug(p) for p in stem.split("/")) if s] or ["board"])
    depth = [1] * len(rels)
    while True:
        names = ["_".join(p[-d:]) for p, d in zip(parts, depth)]
        grow = [i for i, n in enumerate(names)
                if names.count(n) > 1 and depth[i] < len(parts[i])]
        if not grow:
            break
        for i in grow:
            depth[i] += 1
    out: list[str] = []
    for name in names:
        got, k = name, 1
        while got in out:
            k += 1
            got = f"{name}_{k}"
        out.append(got)
    return out


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
        self.prefix = ""                         # set by Draft when there are several
        tree = K.load_tree(path)
        version, self.problems = K.board_problems(tree)
        self.fps: list = []
        self.unfitted: list[str] = []
        self.on: dict[str, list[tuple]] = {}
        #: Whether the pads carry KiCad 10's names, each ending in its pad's
        #: number (``_kicad.numbered_pin_names``): read without it.
        self.numbered = False
        if self.problems:
            return
        board = K.board_from_tree(tree)
        self.numbered = K.numbered_pin_names(version, board)
        self.fps = sorted((fp for fp in board.footprints if fp.fitted),
                          key=lambda fp: _ref_order(fp.ref))
        self.unfitted = [f"{fp.ref} ({fp.value})" for fp in board.footprints if not fp.fitted]
        for fp in self.fps:
            for pad in fp.pads:
                if pad.net:
                    self.on.setdefault(pad.net, []).append((fp, pad))

    def function(self, pad) -> str:
        """``pad``'s library pin name, without KiCad 10's ``_<pad number>`` on a
        board that carries it (:attr:`numbered`). What slipped through (holdout
        round 2): on a KiCad 10 board every name ended in its pad's number
        (``TS_11``, ``BAT_13``), so no table here matched and no charger was
        drafted, on a charger with a thermistor pin and on a PMIC alike."""
        return K.plain_pin_name(pad.pinfunction, pad.number) if self.numbered else pad.pinfunction

    def names(self, fp) -> dict[str, list[str]]:
        """``{pin name: [pad numbers]}`` of ``fp``'s numbered pads."""
        out: dict[str, list[str]] = {}
        for pad in fp.pads:
            if pad.number and pad.pinfunction:
                name = pin_name(self.function(pad))
                out.setdefault(name, [])
                if pad.number not in out[name]:
                    out[name].append(pad.number)
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
            name = self.function(pad).strip()
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


class Cell:
    """A battery the draft found: its record id, its board and footprint (None for
    a cell no board shows), its pads by role (None where no pin name says), every
    net its pads are on, and what :meth:`Draft._roles` decides from the copper:
    the nets it drives, the nets shown as its return (with the pins that show
    each), and the nets whose role nothing shows."""

    __slots__ = ("id", "b", "fp", "pos", "neg", "therm", "nets", "drives", "returns",
                 "unknown")

    def __init__(self, bid: str, b: Board, fp=None, pos=None, neg=None, therm=None):
        self.id, self.b, self.fp = bid, b, fp
        self.pos, self.neg, self.therm = pos, neg, therm
        self.nets = ({b.net(fp, n) for n in b.pads_of(fp)} - {""}) if fp is not None else set()
        self.drives: set[str] = set()
        self.returns: dict[str, list[str]] = {}
        self.unknown: set[str] = set()


class Draft:
    """The draft records and the summary's candidates, built board by board."""

    def __init__(self, boards: list[Board], named: dict | None = None):
        self.boards = boards
        #: ``{ref: (pos pad or None, neg pad or None)}``: the parts a person named
        #: as a battery with --battery.
        self.named = named or {}
        if len(boards) > 1:
            for b, prefix in zip(boards, prefixes([b.rel for b in boards])):
                b.prefix = prefix
        self.records: dict[str, dict] = {k: {} for k in RECORDS}
        self.candidates: list[dict] = []
        self.unseen: list[str] = []
        self.doubt: list[str] = []
        self._parts: dict[str, tuple] = {}       # charger or protector id: (board, fp)
        self._on: dict[str, tuple] = {}          # conductor id: (board path, net)
        self._carries: dict[str, set] = {}       # mating id: {(board path, net)}

    # -- ids and words --------------------------------------------------- #
    def rid(self, b: Board, *parts: str) -> str:
        """A record id: the ref (and a net) lower-cased, the board's prefix first
        when there is more than one board (:func:`prefixes`)."""
        tail = "_".join(_slug(p) for p in parts)
        return f"{b.prefix}_{tail}" if b.prefix else tail

    def fresh(self, kind: str, rid: str) -> str:
        """``rid``, or ``rid_2``, ``rid_3`` when ``kind`` holds it already: two
        nets whose names differ only in their punctuation (+5V, 5V) make one
        id."""
        n, out = 1, rid
        while out in self.records[kind]:
            n += 1
            out = f"{rid}_{n}"
        return out

    def source(self, b: Board) -> list[str]:
        """A record's source as drafted: a list, which every power rule refuses
        (``_power.source`` takes a non-empty string), holding the one FILL that
        says it is a draft. (What slipped through: the source was that sentence
        as a string, which ``_power.source`` takes, since it checks that a source
        says something and not what; so a draft with every other FILL stated
        passed all six rules, with the proposer's words for where its numbers
        come from.)"""
        return [FILL + f"DRAFT by pcb.battery_record_draft from {b.rel}'s pads and pin "
                       f"names: the refs, values and pads are the board's, and each FILL is "
                       f"yours to state from the part's datasheet or the design. Write in "
                       f"place of this list one sentence that says where each number comes "
                       f"from: every power rule refuses a source that is not a string"]

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
        cells = self._batteries()
        protectors = self._chargers() + self._protectors()
        self._roles(cells)
        fuses = self._fuses(cells)
        if (protectors or fuses) and not cells:
            cells = [self._unknown_battery()]
        ids = [c.id for c in cells]
        battery = (ids[0] if len(ids) == 1 else
                   FILL + f"the pcb_batteries id this part serves (one of {', '.join(ids)})")
        for rec in self.records["pcb_protectors"].values():
            rec["battery"] = battery
        driven = self._conductors(cells, fuses, battery)
        vbus = self._usbc() | self._vbus()
        rails = self._rails()
        self._matings(cells, driven | vbus | set(rails))
        self._doubts(cells, fuses, rails, driven | vbus)

    def found_as_battery(self, b: Board, fp) -> str | None:
        """How ``fp`` was found as a battery's connector or holder, or None: a
        part named with --battery, KiCad's ``Battery*`` libraries, a BT ref, or a
        connector whose value or footprint name says so (:data:`BATTERY_WORDS`)
        or whose pins are named + and -."""
        lib = fp.lib_id.split(":")[0]
        if fp.ref in self.named:
            return "named with --battery"
        if lib.startswith("Battery"):
            return f"library {lib}"
        if _BATTERY_REF.match(fp.ref):
            return f"ref {fp.ref}"
        if connector(fp):
            if BATTERY_WORDS.search(f"{fp.value} {fp.lib_id.split(':')[-1]}"):
                return (f"connector {fp.ref}, its value or footprint naming a cell or holder "
                        f"({fp.value}, {fp.lib_id})")
            names = b.names(fp)
            if _one(names, POS_PINS)[1] and _one(names, NEG_PINS)[1]:
                return f"connector {fp.ref}, its pins named + and -"
        return None

    def _batteries(self) -> list[Cell]:
        out = []
        for b in self.boards:
            for fp in b.fps:
                lib = fp.lib_id.split(":")[0]
                found_by = self.found_as_battery(b, fp)
                if found_by is None:
                    continue
                names = b.names(fp)
                _p, pos = _one(names, POS_PINS)
                _n, neg = _one(names, NEG_PINS)
                _t, therm = _one(names, BATTERY_THERM_PINS)
                if fp.ref in self.named:
                    pos = self.named[fp.ref][0] or pos
                    neg = self.named[fp.ref][1] or neg
                if not (lib.startswith("Battery") or _BATTERY_REF.match(fp.ref)
                        or fp.ref in self.named):
                    self.doubt.append(
                        f"pcb_batteries.{self.rid(b, fp.ref)}: {fp.ref} ({fp.value}) was taken "
                        f"for the cell's connector for {found_by.split(', ', 1)[-1]}: keep it "
                        f"when {fp.ref} meets the cell, else delete it and its mating, and "
                        f"name the part with --battery")
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
                out.append(Cell(bid, b, fp, pos, neg, therm))
                self.note(f"pcb_batteries.{bid}", found_by, b, fp)
        return out

    def _roles(self, cells: list[Cell]) -> None:
        """The nets each cell drives: its pads' nets but its named - pad's and its
        thermistor pad's, and, where no pad is named -, but a net that a drafted
        charger's or protector's ground pin sits on: such a pin on the cell's +
        would short it, so that net is the cell's return. (What slipped through: a
        cell whose pads carry no + or - name, a two-pin housing's Pin_1 and Pin_2,
        drove every net of its pads, so with its - on GND the receptacle's and
        every other connector's GND was drafted as a fault conductor, each with
        its numbers to state, and a connector carrying GND alone as a mating
        ``pcb.mismate`` never asks for: its coverage reads a battery's + and taps,
        never its -.) Where nothing names or shows the -, each net but a named
        +'s is kept, and ``keep_or_delete`` says so: a conductor drafted on the
        return is one to delete, while one left out is one no rule asks for."""
        for c in cells:
            b, fp = c.b, c.fp
            if fp is None:
                continue
            pos_net = b.net(fp, c.pos) if c.pos else ""
            if c.neg is None:
                for pb, pfp in self._parts.values():
                    if pb is not b:
                        continue
                    names = b.names(pfp)
                    for g in RETURN_PINS:
                        for n in names.get(g, []):
                            net = b.net(pfp, n)
                            if net in c.nets and net != pos_net:
                                by = c.returns.setdefault(net, [])
                                if f"{pfp.ref}'s {g}" not in by:
                                    by.append(f"{pfp.ref}'s {g}")
            skip = {b.net(fp, p) for p in (c.neg, c.therm) if p} | set(c.returns)
            c.drives = c.nets - skip
            if c.neg is not None:
                continue
            if c.returns:
                shown = "; ".join(f"{net}, where {' and '.join(w)} "
                                  f"{'sits' if len(w) == 1 else 'sit'}"
                                  for net, w in sorted(c.returns.items()))
                self.records["pcb_batteries"][c.id]["terminals"]["neg"] = FILL + (
                    f"the pad of {fp.ref} on the cell's - terminal (pads: {b.said(fp)}; the "
                    f"copper shows its return on {shown})")
            else:
                c.unknown = c.drives - {pos_net}

    def _unknown_battery(self) -> Cell:
        b = self.boards[0]
        self.records["pcb_batteries"]["cell"] = {
            "board": b.rel,
            "terminals": {"ref": FILL + "the ref of the connector or holder the cell meets: "
                                        "no Battery-library part, no BT ref and no connector "
                                        "valued or drawn as a battery is on the boards named "
                                        "(name it with --battery REF and draft again)",
                          "value": FILL + "that part's value on its board",
                          "pos": FILL + "its pad on the cell's + terminal",
                          "neg": FILL + "its pad on the cell's - terminal"},
            **self._cell_words(), "source": self.source(b)}
        self.unseen.append("the cell's own connector: no part from a Battery library, no BT "
                           "ref and no connector valued or drawn as a battery is on the boards "
                           "named, so pcb_batteries.cell names none (name the part with "
                           "--battery REF, and its board, if it is another, with --board)")
        return Cell("cell", b)

    @staticmethod
    def _cell_words() -> dict:
        """A cell's words, before its chemistry is known: the chemistry is the
        person's pick, and each cell key says which kind of cell takes it and to
        drop it for the other kind. What slipped through (holdout round 2): the
        cell was drafted with a rechargeable cell's keys alone (its charge
        voltage, current and window) and no open-circuit voltage, so a person
        drafting an alkaline cell was pointed at a charge voltage that
        ``pcb.fault_current`` then refuses for a primary cell. *Rejected:* a
        draft per chemistry guessed from the board (no charger on a board says
        no more than "charged elsewhere"); the rechargeable keys alone with a
        note (the keys a person fills are the ones the draft holds)."""
        rechargeable = "for a rechargeable cell (li-ion, li-polymer, lifepo4, lto, nimh)"
        primary = "for a primary cell (li-primary, alkaline)"
        return {
            "chemistry": FILL + "one of li-ion, li-polymer, lifepo4, lto, nimh (rechargeable), "
                                "li-primary, alkaline (primary), from the cell's datasheet; "
                                "then drop each cell key marked for the other kind",
            "series": FILL + "the cells in series, a whole number (two or more: add "
                             "terminals.taps, the cell-junction pads from the lowest up)",
            "parallel": FILL + "the cells in parallel, a whole number",
            "cell": {
                "upper_charge_v": FILL + f"{rechargeable}: its upper limit charging voltage, "
                                         f"with its tolerance, V per cell, from its datasheet "
                                         f"(drop for a primary cell)",
                "open_circuit_v": FILL + f"{primary}: its fresh open-circuit voltage, V per "
                                         f"cell, from its datasheet (drop for a rechargeable "
                                         f"cell, unless its datasheet gives one above its "
                                         f"charge voltage)",
                "discharge_v_min": FILL + f"{rechargeable}: its end (cut-off) voltage, V per "
                                          f"cell, from its datasheet (drop for a primary cell)",
                "charge_a_max": FILL + f"{rechargeable}: its maximum charge current, A per "
                                       f"cell, from its datasheet (drop for a primary cell)",
                "charge_temp_c": FILL + f"{rechargeable}: [lo, hi], the widest window its "
                                        f"datasheet allows any charge in, C (drop for a "
                                        f"primary cell)",
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
                inputs = [n for n in CHARGE_INPUT_PINS if n in names]
                if not cell or not (cur or therm or inputs):
                    continue
                functions = {"charge_v": {
                    "v_reg_v": FILL + "[min, max] V from the datasheet: the regulation voltage "
                                      "of this part's voltage option (or replace v_reg_v with "
                                      "divider {vfb_v, top_ref, bottom_ref, tol_pct} where "
                                      "resistors set it, or with set_by \"firmware\" where its "
                                      "registers set it, as a PMIC's do)",
                    "per_cell": FILL + "true when it regulates each cell, false when it "
                                       "regulates the whole pack"}}
                functions["charge_a"] = self._charge_a(b, fp, names, cur)
                if therm:
                    functions["charge_temp"] = self._charge_temp(b, fp, names, therm)
                pid = self.rid(b, fp.ref)
                said = ", ".join(f"{n} ({', '.join(names[n])})"
                                 for n in cell + cur + therm + ([] if cur or therm else inputs))
                if not (cur or therm):
                    self.doubt.append(
                        f"pcb_protectors.{pid}: {fp.ref} ({fp.value}) was taken for a charger "
                        f"by its {'/'.join(cell)} and {'/'.join(inputs)} pins alone, which a "
                        f"USB PHY or a microcontroller carries too (a backup supply's VBAT): "
                        f"keep it when {fp.ref} charges the cell, else delete it")
                # What a part is, is the person's word: "charger" lets its
                # charge_temp stand with no acts (power.py), and a gauge with a
                # BAT and a TS pin is no charger. (What slipped through: the draft
                # wrote "charger" here, and "protector" for every cutoff pair, a
                # fuel gauge and a multi-cell monitor included.)
                kind = FILL + (f"charger when {fp.ref} is the part that charges the cell "
                               f"(its pins {said} suggest one); else protector, bms or "
                               f"gauge, from its datasheet")
                self.records["pcb_protectors"][pid] = self._protector(b, fp, kind, functions)
                self._parts[pid] = (b, fp)
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
                                    "resistor that sets it, or with set_by \"firmware\" where "
                                    "its registers set it)"}
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
                inputs = CUTOFF_INPUTS.get(pair, ()) if pair else ()
                if pair is None or (inputs and all(i in names for i in inputs)):
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
                pins = f"its {dis} and {chg} pins drive the cell's switches"
                held = self.records["pcb_protectors"].get(pid)
                if held is not None:
                    # One element, every function it serves, in one record: a
                    # charger with its own cutoff pins is not drafted twice.
                    held["functions"].update(functions)
                    held["kind"] += f"; {pins} as well"
                else:
                    kind = FILL + (f"protector, bms or gauge: what {fp.ref} is, from its "
                                   f"datasheet ({pins})")
                    self.records["pcb_protectors"][pid] = self._protector(b, fp, kind,
                                                                          functions)
                    self._parts[pid] = (b, fp)
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

    def _fuses(self, cells: list[Cell]) -> list[tuple]:
        """``(id, board, the cell's net it sits on, the net it feeds)`` of each
        fuse with one pad on a net of a cell's pads (its return's too: a low-side
        fuse guards as a high-side one does)."""
        out = []
        cell_nets = {(c.b.path, n) for c in cells for n in c.nets}
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
                out.append((pid, b, nets[near], nets[far]))
        return out

    def _conductors(self, cells: list[Cell], fuses: list, battery: str) -> set:
        """Draft a conductor per pin group of a connector on a net a battery drives
        (a cell's own, or the far side of a drafted fuse on one); return those
        nets, ``(board path, net)``."""
        driven = {(c.b.path, n) for c in cells for n in c.drives}
        feeds = {}
        for pid, b, near, far in fuses:
            if far and (b.path, near) in driven:
                feeds.setdefault((b.path, far), []).append(pid)
        driven |= set(feeds)
        own = {(c.b.path, c.fp.ref) for c in cells if c.fp is not None}
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
                    self._on[cid] = (b.path, net)
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
                # The union first, then the empty name out: ``-`` binds tighter
                # than ``|``. (What slipped through: written without the
                # brackets, a VBUS pad on no net put "" among the sources, and
                # every connector with a netless pad, a mounting pad included,
                # was drafted a mating that "carries ," nothing.)
                vbus = sorted(({b.net(fp, s) for s in TYPEC_VBUS if s in numbers}
                               | {b.net(fp, n) for n in names.get("VBUS", [])}) - {""})
                out |= {(b.path, n) for n in vbus}
                self.note(f"pcb_usbc.{uid}", "a USB-C receptacle, as pcb.usbc_receptacle "
                          "finds one", b, fp)
        return out

    def _vbus(self) -> set:
        """A USB connector that is no Type-C receptacle (micro-B, mini-B, a USB-A
        plug) brings VBUS in on a pad named VBUS: a ``pcb_rails`` record is
        drafted for its net, so ``pcb.mismate`` reads it as a source, and every
        connector on it is asked a mating. What slipped through (a holdout run):
        VBUS was read on Type-C receptacles alone, so a micro-B's was missed and
        not named in ``cannot_see``. Returns ``{(board path, net)}``."""
        out = set()
        for b in self.boards:
            for fp in b.fps:
                if not connector(fp) or typec_receptacle(fp):
                    continue
                names = b.names(fp)
                nets = sorted({b.net(fp, n) for n in names.get("VBUS", [])} - {""})
                grounds = sorted({b.net(fp, n) for n in names.get("GND", [])} - {""})
                for net in nets:
                    rid = self.fresh("pcb_rails", self.rid(b, fp.ref, net))
                    self.records["pcb_rails"][rid] = {
                        "board": b.rel, "net": net,
                        "nominal_v": FILL + f"the voltage {fp.ref}'s VBUS carries, V (USB's "
                                            f"default bus voltage unless a contract raises it)",
                        "return": (grounds[0] if len(grounds) == 1 else
                                   FILL + f"the net VBUS returns on (pads: {b.said(fp)})"),
                        "source": self.source(b)}
                    out.add((b.path, net))
                    self.note(f"pcb_rails.{rid}", f"connector {fp.ref}'s pin VBUS on {net}, "
                              f"a USB connector that is no Type-C receptacle", b, fp)
        return out

    def _rails(self) -> dict:
        """Draft a rail on the output net of each part with a regulator's output
        and input pins (:data:`RAIL_OUT`); return ``{(board path, net): (rail
        id, its part)}``."""
        out: dict = {}
        drafted = {(r["board"], r["ref"]) for r in self.records["pcb_protectors"].values()}
        for b in self.boards:
            for fp in b.fps:
                if connector(fp) or (b.rel, fp.ref) in drafted:
                    continue
                names = b.names(fp)
                outs = [n for n in RAIL_OUT if n in names]
                ins = [n for n in RAIL_IN if n in names]
                if not outs or not ins:
                    continue
                opads = sorted({p for n in outs for p in names[n]}, key=_order)
                ipads = sorted({p for n in ins for p in names[n]}, key=_order)
                onets = sorted({b.net(fp, p) for p in opads} - {""})
                inets = sorted({b.net(fp, p) for p in ipads} - {""})
                said = (f"pins {'/'.join(outs)} ({', '.join(opads)}) and {'/'.join(ins)} "
                        f"({', '.join(ipads)})")
                why = (f"its {'/'.join(outs)} pins sit on {_nets(onets)}" if len(onets) != 1
                       else f"its {'/'.join(ins)} pins sit on {_nets(inets)}" if len(inets) != 1
                       else f"its output and its input sit on one net, {onets[0]}"
                       if onets == inets else None)
                if why:
                    self.unseen.append(f"{fp.ref} ({fp.value}) on {b.rel}, a regulator by its "
                                       f"{said}: {why}, so no pcb_rails record is drafted for "
                                       f"it; state one for each net it supplies")
                    continue
                net = onets[0]
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
                out[(b.path, net)] = (rid, fp)
                self.note(f"pcb_rails.{rid}", said, b, fp)
        return out

    def _matings(self, cells: list[Cell], sources: set) -> None:
        own = {(c.b.path, c.fp.ref): c.id for c in cells if c.fp is not None}
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
                # A pad on no net carries nothing.
                carries = sorted({b.net(fp, n) for n in b.pads_of(fp)
                                  if b.net(fp, n) and (b.path, b.net(fp, n)) in sources})
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
                self._carries[mid] = {(b.path, n) for n in carries}
                self.note(f"pcb_matings.{mid}",
                          f"connector {fp.ref} carries {', '.join(carries)}, which "
                          f"pcb.mismate's coverage rule asks a mating of", b)

    def _doubts(self, cells: list[Cell], fuses: list, rails: dict, other: set) -> None:
        """``keep_or_delete``: the records drafted on evidence that fits something
        else as well. Each rail (its pin names are a filter's, a reference's and a
        driver's too), with the matings drafted only for its net; and, for a cell
        whose - nothing names or shows, the conductors and matings on its nets."""
        for (path, net), (rid, fp) in rails.items():
            mates = [f"pcb_matings.{m}" for m, nodes in self._carries.items()
                     if (path, net) in nodes and not nodes & other]
            self.doubt.append(
                f"pcb_rails.{rid}: {fp.ref} ({fp.value}) was taken for a regulator by its pin "
                f"names alone, which a filter, a voltage reference or a driver carries too: "
                f"keep it when {fp.ref} supplies {net}, else delete it"
                + (f" and the matings drafted only for {net} ({', '.join(mates)})"
                   if mates else ""))
        for c in cells:
            if not c.unknown:
                continue
            nodes = {(c.b.path, n) for n in c.unknown}
            nodes |= {(b.path, far) for _pid, b, near, far in fuses
                      if far and (b.path, near) in nodes}
            drafted = ([f"pcb_conductors.{cid}" for cid, node in self._on.items()
                        if node in nodes]
                       + [f"pcb_matings.{m}" for m, carried in self._carries.items()
                          if carried <= nodes])
            self.doubt.append(
                f"which of {c.fp.ref}'s pads is the cell's -: no pad is named - and no "
                f"drafted charger's or protector's {'/'.join(RETURN_PINS)} pin sits on "
                f"{', '.join(sorted(c.unknown))}, so each is drafted as a net the cell "
                f"drives. Of what is drafted on them and beyond a fuse on them, keep what is "
                f"on the cell's + and delete what is on its return: "
                + (", ".join(drafted) if drafted else "nothing is drafted there"))

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
    ap.add_argument("--battery", action="append", default=[],
                    help="REF or REF:POS,NEG: the part the cell meets, when nothing finds it "
                         "(its + and - pads, when no pin name says; repeat for each cell)")
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
    named: dict[str, tuple] = {}
    for given in args.battery:
        ref, _, pads = given.partition(":")
        pos, _, neg = pads.partition(",")
        hits = [b for b in boards for fp in b.fps if fp.ref == ref]
        if not ref or (pads and not (pos and neg)):
            problems.append(f"--battery {given}: give REF or REF:POS,NEG")
        elif len(hits) != 1:
            problems.append(f"--battery {given}: {'no fitted part' if not hits else 'a part'} "
                            f"{ref} on {'the boards named' if not hits else 'two boards'}")
        else:
            fp = next(f for f in hits[0].fps if f.ref == ref)
            missing = [p for p in (pos, neg) if p and p not in Board.pads_of(fp)]
            if missing:
                problems.append(f"--battery {given}: {ref} has no pad {', '.join(missing)}")
            named[ref] = (pos or None, neg or None)
    if problems:
        print(f"pcb.battery_record_draft: {len(problems)} problem(s), nothing written:",
              file=sys.stderr)
        for p in problems:
            print(f"  {p}", file=sys.stderr)
        return 2
    draft = Draft(boards, named)
    draft.build()
    if not any(draft.records[k] for k in RECORDS):
        print(f"pcb.battery_record_draft: nothing to draft on {', '.join(b.rel for b in boards)}: "
              f"no battery, charger, protector, fuse, USB connector or regulator was "
              f"found by its library, ref, value or pin names; name the cell's connector with "
              f"--battery REF (or REF:POS,NEG); nothing written", file=sys.stderr)
        return 2
    fills = draft.fills()
    stem = boards[0].stem
    summary = {
        "proposer": "pcb.battery_record_draft",
        "draft": f"{stem}.power_records.json",
        "boards": {b.rel: _sha256(b.path) for b in boards},
        "candidates": draft.candidates,
        "fill": fills,
        "keep_or_delete": draft.doubt,
        "not_fitted": {b.rel: b.unfitted for b in boards if b.unfitted},
        "cannot_see": [*draft.unseen, *UNSEEN],
        "judged_by": "nothing: a draft settles nothing. Every power rule refuses a FILL value "
                     "where it reads a number, a flag or a word, and a source that is not a "
                     "string, so an adopted draft errors until a person states each value "
                     "and writes each source; then the rules bind each record to the boards "
                     "and judge it",
        "adopt": "copy the records you keep into the project's params, keep or delete each "
                 "record keep_or_delete names, state every FILL from the part's datasheet or "
                 "the design, add what this cannot see, and write each source, in place of "
                 "its list, as one sentence that says where its numbers come from"}
    with open(os.path.join(out_dir, f"{stem}.power_records.json"), "w",
              encoding="utf-8") as fh:
        json.dump(draft.records, fh, indent=2)
        fh.write("\n")
    with open(os.path.join(out_dir, f"{stem}.power_draft.json"), "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2, sort_keys=True)
        fh.write("\n")
    counts = ", ".join(f"{k} {len(draft.records[k])}" for k in RECORDS)
    doubt = (f"; {len(draft.doubt)} to keep or delete (keep_or_delete)" if draft.doubt else "")
    print(f"pcb.battery_record_draft: drafted {counts}; {len(fills)} FILL value(s) are yours "
          f"to state{doubt}; a draft settles nothing")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
