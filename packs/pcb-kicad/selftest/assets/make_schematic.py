# SPDX-License-Identifier: Apache-2.0
"""Write ``esd_guard.kicad_sch``, the schematic the pack's known-good board was
drawn from. Not a gate and not run by any test: ``make_baseline.py`` calls it
first, then links each footprint on the board to its symbol here, so
``pcb.parity`` has a schematic and a board that agree and ``pcb.erc`` has a
schematic KiCad's ERC passes.

**Symbols come from KiCad's installed library**, copied into the file's
``lib_symbols`` the way eeschema caches them (a derived symbol, one that
``extends`` another, is flattened into one), so the schematic carries every pin's
name and electrical type without the library.

**Connections are labels and power symbols on pin ends**, no wires: the data
pair on global labels ``DP`` and ``DM`` (a global label's net is its own name,
as the board's nets are), GND and VBUS on KiCad's power symbols. Each label
points away from its part, out along its pin, so U1's four labels stand side by
side above it. Two pins stacked on one point (U1's two ``COM``) take one power
symbol. Each power net carries one PWR_FLAG, the schematic's statement that
something outside drives it (the USB host); without it ERC reads two power inputs
with no source.

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
    ("U1", "ESDA6V1BC6", "Power_Protection", "ESDA6V1BC6",
     "Package_TO_SOT_SMD:SOT-23-6", (127.0, 88.9)),
    ("J2", "USB_OUT", "Connector_Generic", "Conn_01x04",
     "Connector_PinHeader_2.54mm:PinHeader_1x04_P2.54mm_Vertical", (190.5, 88.9)),
]

#: ref -> {pin: net}: make_baseline.py's NETS, which the board is drawn from.
NETS = {
    "J1": {"1": "DP", "2": "GND", "3": "DM", "4": "VBUS"},
    "J2": {"1": "DP", "2": "GND", "3": "DM", "4": "VBUS"},
    "U1": {"1": "DP", "6": "DP", "3": "DM", "4": "DM", "2": "GND", "5": "GND"},
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

#: Which way a label points, from the angle KiCad gives the pin: the direction
#: from its connection point into the part's body. A label points the other way.
_AWAY = {0: "left", 90: "down", 180: "right", 270: "up"}


def _pin_angles(lib_sym: list) -> dict[str, int]:
    """{pin number: the angle in its ``at``}, 0, 90, 180 or 270."""
    found: dict[str, int] = {}

    def walk(node):
        for x in node:
            if isinstance(x, list):
                if K.head(x) == "pin":
                    at = K.child(x, "at")
                    found[str(K.value(x, "number"))] = int(float(at[3]) if len(at) > 3 else 0)
                walk(x)
    walk(lib_sym)
    return found


def _label(net: str, x: float, y: float, away: str) -> list:
    """A global label on a pin end, pointing ``away`` from the part."""
    node = _global_label(net, x, y, left=away == "left")
    if away in ("up", "down"):
        K.child(node, "at")[3] = "90" if away == "up" else "270"
        if away == "down":
            K.child(K.child(node, "effects"), "justify")[1] = "right"
    return node


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
    ends: dict[tuple[str, str], tuple[float, float, str]] = {}
    for ref, val, lib, sym, fp, (x, y) in PARTS:
        pins = _pins(libs[f"{lib}:{sym}"])
        angles = _pin_angles(libs[f"{lib}:{sym}"])
        node = _placed(f"{lib}:{sym}", ref, val, fp, x, y, sorted(pins), f"symbol:{ref}")
        assert K.value(node, "uuid") == sym_uuids[ref]
        items.append(node)
        for num, (px, py) in pins.items():
            # the sheet's y runs down
            ends[(ref, num)] = (x + px, y - py, _AWAY[angles[num] % 360])

    n_pwr = n_flg = 0
    done: set[tuple[float, float, str]] = set()
    for (ref, num), (x, y, away) in sorted(ends.items()):
        net = NETS[ref].get(num)
        if net is None or (x, y, net) in done:
            continue
        done.add((x, y, net))
        if net in POWER_NETS:
            n_pwr += 1
            items.append(_placed(f"power:{net}", f"#PWR0{n_pwr}", net, "", x, y, ["1"],
                                 f"power:{ref}:{num}", angle=0))
            if FLAGGED.get(net) == (ref, num):
                n_flg += 1
                items.append(_placed("power:PWR_FLAG", f"#FLG0{n_flg}", "PWR_FLAG", "",
                                     x, y, ["1"], f"flag:{net}"))
        else:
            items.append(_label(net, x, y, away))

    return W.sheet(ROOT_UUID, list(libs.values()), items)


def write(path: str = OUT) -> str:
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(K.dump(build()) + "\n")
    return path


if __name__ == "__main__":
    print(f"wrote {write()}")
