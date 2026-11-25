# SPDX-License-Identifier: Apache-2.0
"""Regenerate ``esd_guard.kicad_pcb``, the pack's known-good board. Not a gate and
not run by any test: a record of how the committed board was made, so a reader can
make it again and diff.

    python3 packs/pcb-kicad/selftest/assets/make_baseline.py

Needs KiCad 9's ``pcbnew`` module and its stock libraries (/usr/share/kicad).

**The circuit.** A USB data-line protector between two 4-pin headers: J1 in, U1
(ESDA6V1BC6, a four-channel TVS array: the KiCad library's own symbol and SOT-23-6
footprint), J2 out. Each data line enters on one side of U1 and leaves on the
other, D+ on pins 1 and 6, D- on pins 3 and 4, a TVS channel to ground at each pin.
The part is chosen for the defect it lets a control plant: its library symbol
names pins 2 and 5 ``COM`` and stacks them, because inside the part the two are one
node. Put pin 2 on GND and pin 5 on VBUS, and the part shorts the supply to
ground, on copper that passes every design rule. Rejected, from the same stock
library and package: SRV05-4 and ESDA6V1-5SC6 (no pin name repeats, so the control
could plant nothing), SN65220 (its two GND pins are drawn apart, not stacked as one
node) and SP0505BAHT (it names five separate cathodes ``K``, a repeat that is not
one node). No gate here judges a line's capacitance: the part is a fixture's
choice, not advice for a USB 2.0 port.

**Pin names come from the library, not from this file.** Each pad's
``pinfunction`` is read out of KiCad's installed ``.kicad_sym`` for the symbol
(what "Update PCB from Schematic" copies onto a board), through the pack's own
reader. A board drawn without a schematic carries none, and ``pcb.pin_names``
then skips: it has nothing to compare against.

**The layout** is hand-placed so a reader can check it: both headers on the 2.54 mm
grid at x 4 and 26, U1 centred between header pins 2 and 3, the data pair and the
left ground on F.Cu, VBUS and the ground between the headers on B.Cu through the
headers' plated holes, and one ground via beside U1 pin 5 down to it. Tracks
0.25 mm on KiCad's default rules (0.2 mm clearance). It must pass ``kicad-cli pcb
drc --severity-all`` with no violation and nothing unconnected; the script runs
that check and exits 1 if not.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

import pcbnew

HERE = os.path.dirname(os.path.abspath(__file__))
PACK = os.path.dirname(os.path.dirname(HERE))
sys.dont_write_bytecode = True        # never leave a __pycache__ in the pack it describes
sys.path.insert(0, os.path.join(PACK, "gates"))
import _kicad as K                                                 # noqa: E402

sys.path.insert(0, HERE)
import make_libraries as L                                         # noqa: E402
import make_schematic as S                                         # noqa: E402

FOOTPRINTS = "/usr/share/kicad/footprints"
SYMBOLS = "/usr/share/kicad/symbols"
OUT = os.path.join(HERE, "esd_guard.kicad_pcb")

TRACK_MM = 0.25
VIA_MM, VIA_DRILL_MM = 0.6, 0.3

#: (ref, value, symbol lib, symbol, footprint lib, footprint, (x, y) mm)
PARTS = [
    ("J1", "USB_IN", "Connector_Generic", "Conn_01x04",
     "Connector_PinHeader_2.54mm", "PinHeader_1x04_P2.54mm_Vertical", (4.0, 4.0)),
    ("U1", "ESDA6V1BC6", "Power_Protection", "ESDA6V1BC6",
     "Package_TO_SOT_SMD", "SOT-23-6", (15.0, 7.81)),
    ("J2", "USB_OUT", "Connector_Generic", "Conn_01x04",
     "Connector_PinHeader_2.54mm", "PinHeader_1x04_P2.54mm_Vertical", (26.0, 4.0)),
]

#: ref -> {pad: net}. Both headers: 1 D+, 2 GND, 3 D-, 4 VBUS. U1 flows each line
#: through: D+ in on pin 1 and out on pin 6, D- on 3 and 4, a TVS channel at each;
#: both COM pins (2 and 5) on GND.
NETS = {
    "J1": {"1": "DP", "2": "GND", "3": "DM", "4": "VBUS"},
    "J2": {"1": "DP", "2": "GND", "3": "DM", "4": "VBUS"},
    "U1": {"1": "DP", "6": "DP", "3": "DM", "4": "DM", "2": "GND", "5": "GND"},
}

#: The ground via beside U1 pin 5, and where its stub meets the back ground track.
VIA = (17.6, 7.81)
GND_TEE = (VIA[0], 6.54)

#: (net, layer, [(ref, pad) or (x, y), ...]) polylines between pad centres.
ROUTES = [
    ("DP", "F.Cu", [("J1", "1"), ("U1", "1")]),
    ("DP", "F.Cu", [("U1", "6"), ("J2", "1")]),
    # Pins 1 and 6 (and 3 and 4) are two TVS channels on one line, not one
    # conductor, so the line itself must cross: a tie under the body, between the
    # pad rows, as a real layout draws it. It clears pins 2 and 5 by 0.5 mm.
    ("DP", "F.Cu", [("U1", "1"), ("U1", "6")]),
    ("DM", "F.Cu", [("U1", "3"), ("U1", "4")]),
    ("GND", "F.Cu", [("J1", "2"), ("U1", "2")]),
    ("DM", "F.Cu", [("J1", "3"), ("U1", "3")]),
    ("DM", "F.Cu", [("U1", "4"), ("J2", "3")]),
    # The two COM pins are one node inside the part, and KiCad 9.0's footprint has
    # no way to say so from Python (jumper pad groups are not exposed), so DRC
    # reads them as two islands of GND: pin 5 takes its own via to the back ground.
    ("GND", "F.Cu", [("U1", "5"), VIA]),
    ("GND", "B.Cu", [VIA, GND_TEE]),
    ("GND", "B.Cu", [("J1", "2"), GND_TEE, ("J2", "2")]),
    ("VBUS", "B.Cu", [("J1", "4"), ("J2", "4")]),
]

OUTLINE = [(0.0, 0.0), (30.0, 0.0), (30.0, 15.0), (0.0, 15.0)]


def library_pin_names(lib: str, symbol: str) -> dict[str, tuple[str, str]]:
    """{pin number: (name, electrical type)} from KiCad's installed symbol,
    following one ``extends`` (a derived symbol keeps its parent's pins: the
    stock library's NCP349MNAE extends NCP349MN)."""
    tree = K.load_tree(os.path.join(SYMBOLS, lib + ".kicad_sym"))
    syms = {str(s[1]): s for s in K.children(tree, "symbol")}
    sym = syms[symbol]
    parent = K.value(sym, "extends")
    if parent:
        sym = syms[str(parent)]
    pins: dict[str, tuple[str, str]] = {}

    def walk(node):
        for x in node:
            if isinstance(x, list):
                if K.head(x) == "pin":
                    num = str(K.value(x, "number"))
                    pins.setdefault(num, (str(K.value(x, "name")), str(x[1])))
                walk(x)
    walk(sym)
    return pins


#: The project's own library tables and the copies of the stock entries this
#: board and its schematic use, which they name through ``${KIPRJMOD}/lib``:
#: ``make_libraries.py`` writes both, and says why the pack keeps its own copies.
#: (Before the holdout run, the tables named the host's stock libraries through
#: ``${KICAD9_FOOTPRINT_DIR}``, and the known-good controls read whatever library
#: the host had installed.)


def mm(v: float) -> int:
    return pcbnew.FromMM(v)


def main() -> int:
    S.write()
    L.write()
    sym_uuids = S.symbol_uuids()
    board = pcbnew.BOARD()
    board.SetCopperLayerCount(2)
    nets = {}
    for name in sorted({n for m in NETS.values() for n in m.values()}):
        net = pcbnew.NETINFO_ITEM(board, name)
        board.Add(net)
        nets[name] = net

    placed = {}
    for ref, val, slib, sym, flib, fpname, (x, y) in PARTS:
        fp = pcbnew.FootprintLoad(os.path.join(FOOTPRINTS, flib + ".pretty"), fpname)
        fp.SetFPID(pcbnew.LIB_ID(flib, fpname))
        models = fp.Models()
        for i in range(len(models)):
            # KiCad's own STEP model for the footprint, kept beside the board
            # (selftest/assets/3d/) so pcb.enclosure_fit sees the same parts on
            # every host, KiCad's 3D library installed or not. By index: iterating
            # the vector yields copies, and a copy's new name is never saved.
            step = os.path.splitext(os.path.basename(models[i].m_Filename))[0] + ".step"
            if not os.path.isfile(os.path.join(HERE, "3d", step)):
                raise SystemExit(f"no 3d/{step}: fetch it from kicad-packages3D (3d/README.md)")
            models[i].m_Filename = "${KIPRJMOD}/3d/" + step
        fp.SetPath(pcbnew.KIID_PATH("/" + sym_uuids[ref]))
        fp.SetSheetname("/")
        fp.SetSheetfile(os.path.basename(S.OUT))
        fp.SetReference(ref)
        fp.SetValue(val)
        fp.SetPosition(pcbnew.VECTOR2I(mm(x), mm(y)))
        names = library_pin_names(slib, sym)
        for pad in fp.Pads():
            num = pad.GetNumber()
            if num in NETS[ref]:
                pad.SetNet(nets[NETS[ref][num]])
            name, ptype = names[num]
            pad.SetPinFunction(name)
            pad.SetPinType(ptype)
        board.Add(fp)
        placed[ref] = fp

    def point(p):
        if isinstance(p[0], str):
            pad = [q for q in placed[p[0]].Pads() if q.GetNumber() == p[1]][0]
            return pad.GetPosition()
        return pcbnew.VECTOR2I(mm(p[0]), mm(p[1]))

    layer = {"F.Cu": pcbnew.F_Cu, "B.Cu": pcbnew.B_Cu}
    for net, lay, pts in ROUTES:
        for a, b in zip(pts, pts[1:]):
            t = pcbnew.PCB_TRACK(board)
            t.SetStart(point(a))
            t.SetEnd(point(b))
            t.SetWidth(mm(TRACK_MM))
            t.SetLayer(layer[lay])
            t.SetNet(nets[net])
            board.Add(t)
    via = pcbnew.PCB_VIA(board)
    via.SetPosition(pcbnew.VECTOR2I(mm(VIA[0]), mm(VIA[1])))
    via.SetWidth(mm(VIA_MM))
    via.SetDrill(mm(VIA_DRILL_MM))
    via.SetNet(nets["GND"])
    board.Add(via)

    for a, b in zip(OUTLINE, OUTLINE[1:] + OUTLINE[:1]):
        e = pcbnew.PCB_SHAPE(board)
        e.SetShape(pcbnew.SHAPE_T_SEGMENT)
        e.SetStart(pcbnew.VECTOR2I(mm(a[0]), mm(a[1])))
        e.SetEnd(pcbnew.VECTOR2I(mm(b[0]), mm(b[1])))
        e.SetLayer(pcbnew.Edge_Cuts)
        e.SetWidth(mm(0.1))
        board.Add(e)

    pcbnew.SaveBoard(OUT, board)
    pro = OUT[:-len(".kicad_pcb")] + ".kicad_pro"
    if not os.path.isfile(pro):
        with open(pro, "w", encoding="utf-8") as fh:
            json.dump({"meta": {"filename": os.path.basename(pro), "version": 1}}, fh, indent=2)

    report = os.path.abspath(os.path.join(os.environ.get("TMPDIR", "/tmp"), "esd_guard_drc.json"))
    proc = subprocess.run(["kicad-cli", "pcb", "drc", "--severity-all", "--exit-code-violations",
                           "--schematic-parity",
                           "--format", "json", "-o", report, OUT],
                          capture_output=True, text=True)
    with open(report, encoding="utf-8") as fh:
        doc = json.load(fh)
    print(f"wrote {OUT}")
    print(f"drc exit {proc.returncode}: {len(doc['violations'])} violation(s), "
          f"{len(doc['unconnected_items'])} unconnected, "
          f"{len(doc['schematic_parity'])} schematic parity")
    for v in doc["violations"] + doc["unconnected_items"] + doc["schematic_parity"]:
        print(f"  {v['severity']:8} {v['type']}: {v['description']} — "
              + "; ".join(i["description"] for i in v["items"]))
    erc = os.path.abspath(os.path.join(os.environ.get("TMPDIR", "/tmp"), "esd_guard_erc.json"))
    sch = subprocess.run(["kicad-cli", "sch", "erc", "--severity-all", "--exit-code-violations",
                          "--format", "json", "-o", erc, S.OUT], capture_output=True, text=True)
    with open(erc, encoding="utf-8") as fh:
        found = [v for sheet in json.load(fh)["sheets"] for v in sheet["violations"]]
    print(f"erc exit {sch.returncode}: {len(found)} violation(s)")
    for v in found:
        print(f"  {v['severity']:8} {v['type']}: {v['description']}")
    # kicad-cli leaves the board's local settings (.kicad_prl) beside a board it
    # opened: per-user view state, never part of the fixture.
    prl = OUT[:-len(".kicad_pcb")] + ".kicad_prl"
    if os.path.exists(prl):
        os.remove(prl)
    clean = proc.returncode == 0 and not doc["schematic_parity"] and sch.returncode == 0
    return 0 if clean else 1


if __name__ == "__main__":
    sys.exit(main())
