# SPDX-License-Identifier: Apache-2.0
"""Regenerate ``power_board.kicad_pcb``, the board the pack's four battery rules
(``gates/power.py``) pass on. Not a gate and not run by any test: a record of
how the committed board was made, so a reader can make it again and diff.

    python3 packs/pcb-kicad/selftest/assets/make_power_board.py

Needs KiCad 9's ``pcbnew`` module and its stock footprints and symbols
(/usr/share/kicad). Made with pcbnew 9.0.9.

**The circuit.** A one-cell lithium pack's charger and protection, placed and
with every pad's net set, NOT routed: the power rules read pad nets, and nothing
else here reads this board (``pcb_board`` stays ``esd_guard``, whose DRC, 3D and
enclosure controls would move if it grew). Every part is a KiCad stock
footprint, its pin names copied from KiCad's stock symbol:

- J1, a USB-C receptacle (GCT USB4105, ``Connector:USB_C_Receptacle_USB2.0_16P``),
  brings VBUS in, with R1 and R2 (5.1k) from CC1 and CC2 to ground, and both
  rows of D+ and D- joined.
- U1, an MCP73833-AMI/UN linear charger in MSOP-10 (``MCP73833-xxx-UN``: VDD 1-2,
  STAT1 3, STAT2 4, VSS 5, PROG 6, PG 7, THERM 8, VBAT 9-10), charges the cell
  from VBUS onto VSYS. R3 (3k) sets PROG; TH1, an NTC, sits from THERM to
  ground; C2 and C3 (10 uF) bypass its input and output.
- BT1 is the cell's connector, a keyed two-pin housing (Molex Micro-Latch,
  ``Conn_01x02``): pin 1 the cell's positive, pin 2 its negative.
- F1, a PTC (fixture value), sits between BT1 pin 1 and VSYS, so the cell's
  positive net holds BT1's pad and F1's and nothing else.
- U2, a one-cell protector on ``AP9101CK``'s pins (VM 1, VDD 2, VSS 3, DO 4,
  CO 5; fixture value), senses the cell through F1 and R5 (330, its RC
  resistor on VDD), with C1 from VDD to the cell's negative and R4 on VM. Its
  DO and CO drive Q1, a dual N-MOSFET (``FDS6890A``: S1 1, G1 2, S2 3, G2 4,
  D2 5-6, D1 7-8), its two drains joined, in the cell's negative lead: S1 on
  the cell's negative, S2 on ground. (What slipped through the branch's board:
  its protector's DO and CO sat on nets of their own, so it drove nothing, and
  the cutoff rule passed it.)
- J6 is the wire out, on VSYS.
- J10 and J11 are 8-position FFC housings (Hirose FH12, ``Conn_01x08``),
  positions 1 GND, 2 SIG_A, 4 SIG_B, 5 VBUS, 7 SIG_C and 8 SIG_D, 3 and 6
  open. A 6-way cable seated in them up to two positions off never puts VBUS on
  GND (``pcb.mismate``), even through the loop the two housings make on one
  board: an open position breaks every chain from 1 to 5. (What slipped
  through the board's first cut: the USB pair sat on 2 and 3, and a cable two
  positions off joined J10's GND to J11's D- and J10's D- to J11's VBUS, one
  copper net on this board: VBUS on GND.)

The refs, values and nets are this file's own; no part of the parent device's
battery path is here (docs/ORIGINS.md).
"""
from __future__ import annotations

import os
import sys

import pcbnew

HERE = os.path.dirname(os.path.abspath(__file__))
PACK = os.path.dirname(os.path.dirname(HERE))
sys.dont_write_bytecode = True        # never leave a __pycache__ in the pack it describes
sys.path.insert(0, os.path.join(PACK, "proposers"))
import _schematic as W                                             # noqa: E402

FOOTPRINTS = "/usr/share/kicad/footprints"
SYMBOLS = "/usr/share/kicad/symbols"
OUT = os.path.join(HERE, "power_board.kicad_pcb")

_FFC = "Connector_FFC-FPC:Hirose_FH12-8S-0.5SH_1x08-1MP_P0.50mm_Horizontal"

#: (ref, value, symbol "lib:name", footprint "lib:name", (x, y) mm)
PARTS = [
    ("J1", "USB4105-GF-A", "Connector:USB_C_Receptacle_USB2.0_16P",
     "Connector_USB:USB_C_Receptacle_GCT_USB4105-xx-A_16P_TopMnt_Horizontal", (8.0, 7.0)),
    ("R1", "5.1k", "Device:R", "Resistor_SMD:R_0603_1608Metric", (4.0, 16.0)),
    ("R2", "5.1k", "Device:R", "Resistor_SMD:R_0603_1608Metric", (8.0, 16.0)),
    ("C2", "10uF", "Device:C", "Capacitor_SMD:C_0603_1608Metric", (14.0, 16.0)),
    ("U1", "MCP73833-AMI/UN", "Battery_Management:MCP73833-xxx-UN",
     "Package_SO:MSOP-10_3x3mm_P0.5mm", (22.0, 9.0)),
    ("R3", "3k", "Device:R", "Resistor_SMD:R_0603_1608Metric", (19.0, 16.0)),
    ("TH1", "NTC 10k B4250", "Device:Thermistor_NTC", "Resistor_SMD:R_0603_1608Metric",
     (23.0, 16.0)),
    ("C3", "10uF", "Device:C", "Capacitor_SMD:C_0603_1608Metric", (27.0, 16.0)),
    ("F1", "PTC_FIXTURE_0A5", "Device:Polyfuse", "Fuse:Fuse_1206_3216Metric", (33.0, 8.0)),
    ("BT1", "CELL", "Connector_Generic:Conn_01x02",
     "Connector_Molex:Molex_Micro-Latch_53253-0270_1x02_P2.00mm_Vertical", (41.0, 8.0)),
    ("U2", "PROT_1S_FIXTURE", "Battery_Management:AP9101CK", "Package_TO_SOT_SMD:SOT-23-5",
     (40.0, 18.0)),
    ("R5", "330", "Device:R", "Resistor_SMD:R_0603_1608Metric", (34.0, 18.0)),
    ("C1", "100nF", "Device:C", "Capacitor_SMD:C_0603_1608Metric", (45.0, 18.0)),
    ("R4", "2.7k", "Device:R", "Resistor_SMD:R_0603_1608Metric", (40.0, 24.0)),
    ("Q1", "FDS6890A", "Transistor_FET:FDS6890A", "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm",
     (49.0, 24.0)),
    ("J6", "WIRE_OUT", "Connector_Generic:Conn_01x02",
     "Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical", (55.0, 8.0)),
    ("J10", "FFC_8P", "Connector_Generic:Conn_01x08", _FFC, (14.0, 33.0)),
    ("J11", "FFC_8P", "Connector_Generic:Conn_01x08", _FFC, (34.0, 33.0)),
]

_FFC_NETS = {"1": "GND", "2": "SIG_A", "4": "SIG_B", "5": "VBUS", "7": "SIG_C",
             "8": "SIG_D"}

#: ref -> {pad: net}. U1's STAT1, STAT2 and PG are left open (no LEDs here), and
#: so are J1's SBU pins and the FFC housings' positions 3 and 6.
NETS = {
    "J1": {**{p: "VBUS" for p in ("A4", "A9", "B4", "B9")},
           **{p: "GND" for p in ("A1", "A12", "B1", "B12", "S1")},
           "A5": "CC1", "B5": "CC2", "A6": "USB_DP", "B6": "USB_DP",
           "A7": "USB_DM", "B7": "USB_DM"},
    "R1": {"1": "CC1", "2": "GND"},
    "R2": {"1": "CC2", "2": "GND"},
    "C2": {"1": "VBUS", "2": "GND"},
    "U1": {"1": "VBUS", "2": "VBUS", "5": "GND", "6": "PROG", "8": "THERM",
           "9": "VSYS", "10": "VSYS"},
    "R3": {"1": "PROG", "2": "GND"},
    "TH1": {"1": "THERM", "2": "GND"},
    "C3": {"1": "VSYS", "2": "GND"},
    "F1": {"1": "CELL_P", "2": "VSYS"},
    "BT1": {"1": "CELL_P", "2": "CELL_N"},
    "U2": {"1": "PROT_VM", "2": "PROT_VDD", "3": "CELL_N", "4": "PROT_DO", "5": "PROT_CO"},
    "R5": {"1": "VSYS", "2": "PROT_VDD"},
    "C1": {"1": "PROT_VDD", "2": "CELL_N"},
    "R4": {"1": "PROT_VM", "2": "GND"},
    "Q1": {"1": "CELL_N", "2": "PROT_DO", "3": "GND", "4": "PROT_CO",
           "5": "FET_D", "6": "FET_D", "7": "FET_D", "8": "FET_D"},
    "J6": {"1": "VSYS", "2": "GND"},
    "J10": dict(_FFC_NETS),
    "J11": dict(_FFC_NETS),
}

OUTLINE = [(0.0, 0.0), (60.0, 0.0), (60.0, 40.0), (0.0, 40.0)]


def mm(v: float) -> int:
    return pcbnew.FromMM(v)


def main() -> int:
    board = pcbnew.BOARD()
    board.SetCopperLayerCount(2)
    nets = {}
    for name in sorted({n for m in NETS.values() for n in m.values()}):
        net = pcbnew.NETINFO_ITEM(board, name)
        board.Add(net)
        nets[name] = net
    for ref, val, sym, footprint, (x, y) in PARTS:
        flib, fpname = footprint.split(":")
        fp = pcbnew.FootprintLoad(os.path.join(FOOTPRINTS, flib + ".pretty"), fpname)
        fp.SetFPID(pcbnew.LIB_ID(flib, fpname))
        fp.SetReference(ref)
        fp.SetValue(val)
        fp.SetPosition(pcbnew.VECTOR2I(mm(x), mm(y)))
        lib, name = sym.split(":")
        names = {}
        lib_sym = W.library_symbol(SYMBOLS, lib, name)

        def walk(node):
            for item in node:
                if isinstance(item, list):
                    if W.K.head(item) == "pin":
                        names.setdefault(str(W.K.value(item, "number")),
                                         (str(W.K.value(item, "name")), str(item[1])))
                    walk(item)
        walk(lib_sym)
        for pad in fp.Pads():
            num = pad.GetNumber()
            if num in NETS[ref]:
                pad.SetNet(nets[NETS[ref][num]])
            if num in names:
                pad.SetPinFunction(names[num][0])
                pad.SetPinType(names[num][1])
        board.Add(fp)
    for a, b in zip(OUTLINE, OUTLINE[1:] + OUTLINE[:1]):
        e = pcbnew.PCB_SHAPE(board)
        e.SetShape(pcbnew.SHAPE_T_SEGMENT)
        e.SetStart(pcbnew.VECTOR2I(mm(a[0]), mm(a[1])))
        e.SetEnd(pcbnew.VECTOR2I(mm(b[0]), mm(b[1])))
        e.SetLayer(pcbnew.Edge_Cuts)
        e.SetWidth(mm(0.1))
        board.Add(e)
    pcbnew.SaveBoard(OUT, board)
    # pcbnew.SaveBoard leaves a project file and local settings beside the board:
    # this board travels alone, and nothing reads them.
    for ext in (".kicad_pro", ".kicad_prl"):
        side = OUT[:-len(".kicad_pcb")] + ext
        if os.path.exists(side):
            os.remove(side)
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
