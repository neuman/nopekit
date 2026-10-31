# SPDX-License-Identifier: Apache-2.0
"""Write ``esd_guard.kicad_sch``, the schematic the pack's known-good board was
drawn from. Not a gate and not run by any test: ``make_baseline.py`` calls it
first, then links each footprint on the board to its symbol here, so
``pcb.parity`` has a schematic and a board that agree and ``pcb.erc`` has a
schematic KiCad's ERC passes.

**Symbols come from KiCad's installed library**, copied into the file's
``lib_symbols`` the way eeschema caches them (a derived symbol such as
USBLC6-2SC6, which ``extends`` USBLC6-2P6, is flattened into one), so the
schematic carries every pin's name and electrical type without the library.

**Connections are labels and power symbols on pin ends**, no wires: the data
pair on global labels ``DP`` and ``DM`` (a global label's net is its own name,
as the board's nets are), GND and VBUS on KiCad's power symbols. Each power net
carries one PWR_FLAG, the schematic's statement that something outside drives
it (the USB host); without it ERC reads two power inputs with no source.

Every UUID is a ``uuid5`` of a fixed name, so two runs write the same bytes.
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PACK = os.path.dirname(os.path.dirname(HERE))
sys.dont_write_bytecode = True        # never leave a __pycache__ in the pack it describes
sys.path.insert(0, os.path.join(PACK, "gates"))
sys.path.insert(0, os.path.join(PACK, "proposers"))
import _kicad as K                                                 # noqa: E402
import _schematic as W                                             # noqa: E402

SYMBOLS = "/usr/share/kicad/symbols"
OUT = os.path.join(HERE, "esd_guard.kicad_sch")
PROJECT = "esd_guard"

uid = W.uid
ROOT_UUID = uid("sheet:root")

#: (ref, value, lib, symbol, footprint, (x, y) mm on the sheet). The footprints
#: are make_baseline.py's, as "library:name" (what the board's footprint says).
PARTS = [
    ("J1", "USB_IN", "Connector_Generic", "Conn_01x04",
     "Connector_PinHeader_2.54mm:PinHeader_1x04_P2.54mm_Vertical", (63.5, 88.9)),
    ("U1", "USBLC6-2SC6", "Power_Protection", "USBLC6-2SC6",
     "Package_TO_SOT_SMD:SOT-23-6", (127.0, 88.9)),
    ("J2", "USB_OUT", "Connector_Generic", "Conn_01x04",
     "Connector_PinHeader_2.54mm:PinHeader_1x04_P2.54mm_Vertical", (190.5, 88.9)),
]

#: ref -> {pin: net}: make_baseline.py's NETS, which the board is drawn from.
NETS = {
    "J1": {"1": "DP", "2": "GND", "3": "DM", "4": "VBUS"},
    "J2": {"1": "DP", "2": "GND", "3": "DM", "4": "VBUS"},
    "U1": {"1": "DP", "6": "DP", "3": "DM", "4": "DM", "2": "GND", "5": "VBUS"},
}

POWER_NETS = ("GND", "VBUS")
#: The pin each power net's PWR_FLAG sits on: the input header's, where the
#: host's supply arrives.
FLAGGED = {"GND": ("J1", "2"), "VBUS": ("J1", "4")}


def symbol_uuids() -> dict[str, str]:
    """{ref: the UUID of its symbol}, what a footprint's ``(path "/<uuid>")``
    names on the board."""
    return {ref: uid(f"symbol:{ref}") for ref, *_ in PARTS}


def _library_symbol(lib: str, name: str) -> list:
    return W.library_symbol(SYMBOLS, lib, name)


_pins = W.pins
_global_label = W.global_label


def _placed(lib_id, ref, value, footprint, x, y, pins, key, angle=0):
    return W.placed(lib_id, ref, value, footprint, x, y, pins, key, project=PROJECT,
                    root_uuid=ROOT_UUID, angle=angle)


def build() -> list:
    libs = {}
    for _ref, _val, lib, sym, _fp, _xy in PARTS:
        libs[f"{lib}:{sym}"] = _library_symbol(lib, sym)
    for name in (*POWER_NETS, "PWR_FLAG"):
        libs[f"power:{name}"] = _library_symbol("power", name)

    items: list = []
    sym_uuids = symbol_uuids()
    ends: dict[tuple[str, str], tuple[float, float]] = {}
    for ref, val, lib, sym, fp, (x, y) in PARTS:
        pins = _pins(libs[f"{lib}:{sym}"])
        node = _placed(f"{lib}:{sym}", ref, val, fp, x, y, sorted(pins), f"symbol:{ref}")
        assert K.value(node, "uuid") == sym_uuids[ref]
        items.append(node)
        for num, (px, py) in pins.items():
            ends[(ref, num)] = (x + px, y - py)       # the sheet's y runs down

    n_pwr = n_flg = 0
    for (ref, num), (x, y) in sorted(ends.items()):
        net = NETS[ref].get(num)
        if net is None:
            continue
        if net in POWER_NETS:
            n_pwr += 1
            items.append(_placed(f"power:{net}", f"#PWR0{n_pwr}", net, "", x, y, ["1"],
                                 f"power:{ref}:{num}", angle=0))
            if FLAGGED.get(net) == (ref, num):
                n_flg += 1
                items.append(_placed("power:PWR_FLAG", f"#FLG0{n_flg}", "PWR_FLAG", "",
                                     x, y, ["1"], f"flag:{net}"))
        else:
            centre_x = dict((r, xy[0]) for r, *_rest, xy in PARTS)[ref]
            items.append(_global_label(net, x, y, left=x < centre_x))

    return W.sheet(ROOT_UUID, list(libs.values()), items)


def write(path: str = OUT) -> str:
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(K.dump(build()) + "\n")
    return path


if __name__ == "__main__":
    print(f"wrote {write()}")
