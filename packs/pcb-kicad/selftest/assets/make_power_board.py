# SPDX-License-Identifier: Apache-2.0
"""Regenerate ``lipo_charger.kicad_pcb``, the board the pack's three LiPo power
rules (``gates/power.py``) pass on. Not a gate and not run by any test: a record of
how the committed board was made, so a reader can make it again and diff.

    python3 packs/pcb-kicad/selftest/assets/make_power_board.py

Needs KiCad 9's ``pcbnew`` module and its stock footprints (/usr/share/kicad).

**The circuit.** A one-cell LiPo charger and its cell path, placed and with every
pad's net set, NOT routed: the power rules read pad nets, and nothing else here
reads this board (``pcb_board`` stays ``esd_guard``, whose DRC, 3D and enclosure
controls would move if it grew). J1 brings VBUS in. U1, an MCP73833 in MSOP-10
(KiCad's MCP73833-xxx-UN symbol: VDD 1-2, STAT1 3, STAT2 4, VSS 5, PROG 6, PG 7,
THERM 8, VBAT 9-10), charges the cell at J2, with its THERM pin on TH1, an NTC to
ground, and R2 setting PROG. F1, an MF-MSMF075 in 1812, sits between the cell's
positive pin and J3, the wire out. U2, an AP9101CK protector in SOT-23-5 (VM 1,
VDD 2, VSS 3, DO 4, CO 5), senses the cell through R1 (330 ohm, its datasheet's
series resistor on VDD) with C1 from VDD to ground.

Pin names are copied onto the pads from KiCad's symbols, as ``make_baseline.py``
does, so the board reads the same with or without a schematic.
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
OUT = os.path.join(HERE, "lipo_charger.kicad_pcb")

#: (ref, value, symbol "lib:name" or None, footprint "lib:name", (x, y) mm)
PARTS = [
    ("J1", "VBUS_IN", "Connector_Generic:Conn_01x02",
     "Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical", (4.0, 6.0)),
    ("U1", "MCP73833", "Battery_Management:MCP73833-xxx-UN",
     "Package_SO:MSOP-10_3x3mm_P0.5mm", (14.0, 8.0)),
    ("TH1", "NTC_10k", "Device:Thermistor_NTC", "Resistor_SMD:R_0603_1608Metric",
     (14.0, 14.0)),
    ("R2", "2k", "Device:R", "Resistor_SMD:R_0603_1608Metric", (18.0, 14.0)),
    ("J2", "CELL", "Connector_Generic:Conn_01x02",
     "Connector_JST:JST_PH_B2B-PH-K_1x02_P2.00mm_Vertical", (26.0, 6.0)),
    ("F1", "MF-MSMF075", "Device:Polyfuse", "Fuse:Fuse_1812_4532Metric", (34.0, 6.0)),
    ("J3", "WIRE_OUT", "Connector_Generic:Conn_01x02",
     "Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical", (42.0, 6.0)),
    ("U2", "AP9101CK", "Battery_Management:AP9101CK", "Package_TO_SOT_SMD:SOT-23-5",
     (30.0, 14.0)),
    ("R1", "330", "Device:R", "Resistor_SMD:R_0603_1608Metric", (26.0, 14.0)),
    ("C1", "100nF", "Device:C", "Capacitor_SMD:C_0603_1608Metric", (34.0, 14.0)),
]

#: ref -> {pad: net}. U1's STAT1, STAT2 and PG are left open (no LEDs here); U2's
#: DO and CO drive the protection FETs a real pack carries, which this board
#: leaves out (the cutoff rule does not check the FET path: PACK.md).
NETS = {
    "J1": {"1": "VBUS", "2": "GND"},
    "U1": {"1": "VBUS", "2": "VBUS", "5": "GND", "6": "PROG", "8": "THERM",
           "9": "VCELL", "10": "VCELL"},
    "TH1": {"1": "THERM", "2": "GND"},
    "R2": {"1": "PROG", "2": "GND"},
    "J2": {"1": "VCELL", "2": "GND"},
    "F1": {"1": "VCELL", "2": "VBAT_OUT"},
    "J3": {"1": "VBAT_OUT", "2": "GND"},
    "R1": {"1": "VCELL", "2": "CUT_VDD"},
    "U2": {"1": "GND", "2": "CUT_VDD", "3": "GND", "4": "CUT_DO", "5": "CUT_CO"},
    "C1": {"1": "CUT_VDD", "2": "GND"},
}

OUTLINE = [(0.0, 0.0), (48.0, 0.0), (48.0, 20.0), (0.0, 20.0)]


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
