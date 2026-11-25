# SPDX-License-Identifier: Apache-2.0
"""Regenerate ``radio_board.kicad_pcb``, the board ``pcb.antenna_keepout`` passes
on. Not a gate and not run by any test: a record of how the committed board was
made, so a reader can make it again and diff.

    python3 packs/pcb-kicad/selftest/assets/make_radio_board.py

Needs KiCad 9's ``pcbnew`` module and its stock footprints and symbols
(/usr/share/kicad). Made with pcbnew 9.0.9.

**The board.** 45 x 30 mm, four copper layers (F.Cu, In1.Cu, In2.Cu, B.Cu), every
part a KiCad stock footprint with its pin names copied from KiCad's stock symbol:

- U1, an ESP32-S3-WROOM-1 Wi-Fi and Bluetooth LE module
  (``RF_Module:ESP32-S3-WROOM-1``), turned 90 degrees so its antenna end lies on
  the board's left edge (x 0). Its footprint carries KiCad's keep-out rule area,
  48 x 21 mm beyond the module's pads, on F.Cu, In1.Cu and B.Cu only: In2.Cu is
  not in it. On this board the area covers the strip x 0 to 6.15 mm, the whole
  board's height. Pads 1, 40 and 41 (the thermal pad's vias) are GND, pad 2 is
  3V3, pad 3 is EN.
- C1, a 10 uF 0603 capacitor on 3V3, 2.6 mm outside the keep-out, with a 0.4 mm
  track from U1's pad 2 and a ground via beside its pad 2.
- J1, a four-pin 2.54 mm header at the far edge (3V3, GND, EN, one pin open).
- A GND pour on each copper layer, its outline from x 7.0 mm (0.85 mm clear of
  the keep-out) to 44.5 mm, filled by KiCad's own zone filler. The inner pours
  reach ground through J1's pin 2 and U1's thermal vias, so a refill keeps them.

Placed, netted and routed only as far as the gate needs: the gate reads copper
in the keep-out, nothing else here reads this board (``pcb_board`` stays
``esd_guard``), and its other DRC findings (the module's 0.2 mm thermal vias, its
silkscreen at the board edge, the header's open nets) are not this gate's.

The known-bad control (``selftest/bad_boards.py:pour_under_antenna``) is this
board with the In2.Cu pour's outline moved to x 0.5 mm, under the antenna, and
its stored fill left alone: KiCad's DRC reads nothing on the file, and the next
refill pours ground on In2.Cu, the layer the footprint's keep-out leaves out.

The layout, refs and values are this file's own; no part of the parent device's
radio is here (docs/ORIGINS.md).
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
OUT = os.path.join(HERE, "radio_board.kicad_pcb")

#: (ref, value, symbol "lib:name", footprint "lib:name", (x, y) mm, degrees)
PARTS = [
    ("U1", "ESP32-S3-WROOM-1", "RF_Module:ESP32-S3-WROOM-1",
     "RF_Module:ESP32-S3-WROOM-1", (12.9, 15.0), 90.0),
    ("C1", "10uF", "Device:C", "Capacitor_SMD:C_0603_1608Metric", (10.0, 27.5), 0.0),
    ("J1", "CONN_4", "Connector_Generic:Conn_01x04",
     "Connector_PinHeader_2.54mm:PinHeader_1x04_P2.54mm_Vertical", (41.5, 11.19), 0.0),
]

#: ref -> {pad: net}. U1's other pins and J1's pin 4 are left open.
NETS = {
    "U1": {"1": "GND", "2": "3V3", "3": "EN", "40": "GND", "41": "GND"},
    "C1": {"1": "3V3", "2": "GND"},
    "J1": {"1": "3V3", "2": "GND", "3": "EN"},
}

#: (net, layer, width mm, [(x, y) ...]): U1's pad 2 to C1's pad 1, and C1's pad 2
#: to the ground via.
TRACKS = [
    ("3V3", "F.Cu", 0.4, [(8.91, 23.75), (9.225, 25.0), (9.225, 27.5)]),
    ("GND", "F.Cu", 0.4, [(10.775, 27.5), (12.0, 27.5)]),
]
VIAS = [("GND", (12.0, 27.5), 0.6, 0.3)]

WIDTH, HEIGHT = 45.0, 30.0
#: Every pour's outline: x from 7.0 mm, clear of the keep-out's 6.15 mm edge.
POUR = [(7.0, 0.5), (44.5, 0.5), (44.5, 29.5), (7.0, 29.5)]
POUR_LAYERS = ("F.Cu", "In1.Cu", "In2.Cu", "B.Cu")


def mm(v: float) -> int:
    return pcbnew.FromMM(v)


def xy(p) -> pcbnew.VECTOR2I:
    return pcbnew.VECTOR2I(mm(p[0]), mm(p[1]))


def _pin_names(sym: str) -> dict:
    lib, name = sym.split(":")
    names = {}

    def walk(node):
        for item in node:
            if isinstance(item, list):
                if W.K.head(item) == "pin":
                    names.setdefault(str(W.K.value(item, "number")),
                                     (str(W.K.value(item, "name")), str(item[1])))
                walk(item)
    walk(W.library_symbol(SYMBOLS, lib, name))
    return names


def build() -> pcbnew.BOARD:
    board = pcbnew.BOARD()
    board.SetCopperLayerCount(len(POUR_LAYERS))
    nets = {}
    for name in sorted({n for m in NETS.values() for n in m.values()}):
        net = pcbnew.NETINFO_ITEM(board, name)
        board.Add(net)
        nets[name] = net
    for ref, val, sym, footprint, at, turn in PARTS:
        flib, fpname = footprint.split(":")
        fp = pcbnew.FootprintLoad(os.path.join(FOOTPRINTS, flib + ".pretty"), fpname)
        fp.SetFPID(pcbnew.LIB_ID(flib, fpname))
        fp.SetReference(ref)
        fp.SetValue(val)
        fp.SetPosition(xy(at))
        fp.SetOrientationDegrees(turn)
        names = _pin_names(sym)
        for pad in fp.Pads():
            num = pad.GetNumber()
            if num in NETS[ref]:
                pad.SetNet(nets[NETS[ref][num]])
            if num in names:
                pad.SetPinFunction(names[num][0])
                pad.SetPinType(names[num][1])
        board.Add(fp)
    for net, layer, width, points in TRACKS:
        for a, b in zip(points, points[1:]):
            t = pcbnew.PCB_TRACK(board)
            t.SetStart(xy(a))
            t.SetEnd(xy(b))
            t.SetWidth(mm(width))
            t.SetLayer(board.GetLayerID(layer))
            t.SetNet(nets[net])
            board.Add(t)
    for net, at, size, drill in VIAS:
        v = pcbnew.PCB_VIA(board)
        v.SetPosition(xy(at))
        v.SetWidth(mm(size))
        v.SetDrill(mm(drill))
        v.SetNet(nets[net])
        board.Add(v)
    corners = [(0.0, 0.0), (WIDTH, 0.0), (WIDTH, HEIGHT), (0.0, HEIGHT)]
    for a, b in zip(corners, corners[1:] + corners[:1]):
        e = pcbnew.PCB_SHAPE(board)
        e.SetShape(pcbnew.SHAPE_T_SEGMENT)
        e.SetStart(xy(a))
        e.SetEnd(xy(b))
        e.SetLayer(pcbnew.Edge_Cuts)
        e.SetWidth(mm(0.1))
        board.Add(e)
    for layer in POUR_LAYERS:
        z = pcbnew.ZONE(board)
        z.SetLayer(board.GetLayerID(layer))
        z.SetNet(nets["GND"])
        outline = z.Outline()
        outline.NewOutline()
        for p in POUR:
            outline.Append(mm(p[0]), mm(p[1]))
        board.Add(z)
    return board


def main() -> int:
    pcbnew.SaveBoard(OUT, build())
    # The filler runs on the board as loaded from its file: pcbnew 9.0.9 killed
    # the interpreter (SIGSEGV) filling a board made in memory and never loaded.
    board = pcbnew.LoadBoard(OUT)
    if not pcbnew.ZONE_FILLER(board).Fill(board.Zones()):
        print("the zone fill did not finish", file=sys.stderr)
        return 1
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
