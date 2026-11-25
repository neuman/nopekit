# SPDX-License-Identifier: Apache-2.0
"""Write ``power_board.kicad_sch``, the schematic of the power baseline. Not a gate
and not run by any test: ``make_power_board.py`` calls it after the board, and it
runs alone too (it needs no pcbnew):

    python3 packs/pcb-kicad/selftest/assets/make_power_schematic.py

**Why the board has one.** ``pcb.charge_temperature`` reads the schematic beside a
board to see the symbol its NTC is placed on (a fixed resistor there is the
datasheets' way to switch the input off). A real KiCad project always has one. The
power board first travelled alone, and that broke two things. The known-good never
reached the symbol check. And the known-bad fixture's copy of the board, under the
control's out dir, had no file beside it: the gate asked for one that no part of
its own run wrote. A question about a missing path under the out dir is opaque
(another gate's output, invariant 7), so the gate's qualification was never
current, and ``tests.test_cache.OutDirReads`` went red. Now the board and its
schematic travel together, and the fixture copies both (``bad_boards.py``).

**The parts and nets are make_power_board.py's**, ``PARTS`` and ``NETS``: each
symbol is the one the board took its pin names from, and each Footprint field is
the board's footprint name verbatim (``pcb.parity`` compares it exactly). Every
connected pin end carries a global label of its net, as
``pcb.schematic_from_board`` writes them. A multi-unit part (Q1, a dual
N-MOSFET) is placed once per unit, each unit with its own pins.

**The open pins are stated here** (``NO_CONNECT``), from make_power_board.py's
own sentence on what it leaves open; a pin with no net and no statement is
refused, so nothing open is laundered into a no-connect flag.

**The power flags** (``POWER_FLAGS``) say which nets something drives that the
schematic does not show: the USB host's VBUS and its return, the cell's negative
lead, and the protector's supply, which the charger's output feeds through R5 (a
resistor, which ERC does not count as a source).

Every UUID is a ``uuid5`` of a fixed name, so two runs write the same bytes.
Checked with KiCad 9.0.9 (``tests/test_pcb_kicad.py``,
``ThePowerBaselineHasItsSchematic``): ``pcb.parity`` passes it beside the board,
and ``pcb.erc`` finds nothing in it but ``lib_symbol_issues``: no project file or
library table travels with this board, so a host whose KiCad tables lack the
stock libraries cannot find them. Rejected: a ``power_board.kicad_pro`` and four
more rows in the shared ``sym-lib-table``, to make ERC pass too (nothing runs ERC
on this board; the defeat check reads one symbol, and parity is what says the
schematic is the board's).
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PACK = os.path.dirname(os.path.dirname(HERE))
sys.dont_write_bytecode = True        # never leave a __pycache__ in the pack it describes
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(PACK, "proposers"))
import _schematic as W                                             # noqa: E402
from make_power_board import NETS, PARTS, SYMBOLS                  # noqa: E402

K = W.K
OUT = os.path.join(HERE, "power_board.kicad_sch")
PROJECT = "power_board"
ROOT_UUID = W.uid(f"{PROJECT}:sheet:root")

#: ref -> the pins left open on purpose: make_power_board.py's NETS docstring,
#: "U1's STAT1, STAT2 and PG are left open (no LEDs here), and so are J1's SBU
#: pins and the FFC housings' positions 3 and 6".
NO_CONNECT = {"U1": ("3", "4", "7"), "J1": ("A8", "B8"), "J10": ("3", "6"),
              "J11": ("3", "6")}

#: The nets something off this schematic drives (see the module docstring). One
#: PWR_FLAG each. Rejected: a flag on every net with a power pin (VSYS has the
#: charger's own power output on it; a flag there says a second source exists).
POWER_FLAGS = ("VBUS", "GND", "CELL_N", "PROT_VDD")

#: The sheet's cells: parts left to right in PARTS' order, wrapping at ROW_MM.
ROW_MM = 380.0
GRID = 2.54


def _snap(v: float) -> float:
    return round(v / GRID) * GRID


def unit_pins(lib_sym: list) -> dict[int, dict[str, tuple[float, float]]]:
    """{unit: {pin number: (x, y)}} for each unit the symbol draws, unit 0's
    pins (common to every unit) in each; a single-unit symbol is unit 1."""
    common: dict[str, tuple[float, float]] = {}
    found: dict[int, dict[str, tuple[float, float]]] = {}
    for body in K.children(lib_sym, "symbol"):
        tail = str(body[1]).rsplit("_", 2)
        unit = int(tail[1]) if len(tail) == 3 and tail[1].isdigit() else 0
        pins = W.pins(body)
        if unit == 0:
            common.update(pins)
        else:
            found.setdefault(unit, {}).update(pins)
    if not found:
        return {1: common}
    return {u: {**common, **pins} for u, pins in sorted(found.items())}


def _unit(node: list, unit: int) -> list:
    """``node`` (a placed symbol) set to ``unit``, in its body and its instance."""
    K.child(node, "unit")[1] = str(unit)
    path = K.child(K.child(K.child(node, "instances"), "project"), "path")
    K.child(path, "unit")[1] = str(unit)
    return node


def build() -> list:
    libs: dict[str, list] = {}
    items: list = []
    problems: list[str] = []
    x0, y0, row_h = 25.4, 25.4, 0.0
    for ref, value, lib_id, footprint, _xy in PARTS:
        lib, _, name = lib_id.partition(":")
        sym = libs.setdefault(lib_id, W.library_symbol(SYMBOLS, lib, name))
        nets, open_ = NETS[ref], NO_CONNECT.get(ref, ())
        for unit, pins in unit_pins(sym).items():
            xs = [p[0] for p in pins.values()] or [0.0]
            ys = [p[1] for p in pins.values()] or [0.0]
            w, h = (max(xs) - min(xs)) + 40.0, (max(ys) - min(ys)) + 25.0
            if x0 + w > ROW_MM:
                x0, y0, row_h = 25.4, y0 + row_h, 0.0
            x, y = _snap(x0 - min(xs) + 15.0), _snap(y0 + max(ys) + 12.0)
            x0, row_h = x0 + w, max(row_h, h)
            key = f"{PROJECT}:symbol:{ref}" + ("" if unit == 1 else f":unit{unit}")
            node = W.placed(lib_id, ref, value, footprint, x, y, sorted(pins), key,
                            project=PROJECT, root_uuid=ROOT_UUID)
            items.append(_unit(node, unit) if unit != 1 else node)
            labelled: set[tuple[float, float, str]] = set()
            for number, (px, py) in sorted(pins.items()):
                ex, ey = x + px, y - py                  # the sheet's y runs down
                net = nets.get(number)
                if net is not None:
                    if (ex, ey, net) not in labelled:
                        labelled.add((ex, ey, net))
                        items.append(W.global_label(net, ex, ey, left=px < 0))
                elif number in open_:
                    items.append(W.no_connect(ex, ey, f"{PROJECT}:nc:{ref}:{number}"))
                else:
                    problems.append(f"{ref} pin {number} is on no net and NO_CONNECT "
                                    f"does not state it open")
    flag = libs.setdefault("power:PWR_FLAG", W.library_symbol(SYMBOLS, "power", "PWR_FLAG"))
    for i, net in enumerate(POWER_FLAGS):
        fx, fy = 25.4 + 25.4 * i, 12.7
        items.append(W.placed("power:PWR_FLAG", f"#FLG0{i + 1}", "PWR_FLAG", "", fx, fy,
                              sorted(W.pins(flag)), f"{PROJECT}:flag:{net}",
                              project=PROJECT, root_uuid=ROOT_UUID))
        items.append(W.global_label(net, fx, fy, left=False))
    if problems:
        raise SystemExit("make_power_schematic: " + "; ".join(problems))
    return W.sheet(ROOT_UUID, [libs[k] for k in sorted(libs)], items, paper="A3")


def write(path: str = OUT) -> str:
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(K.dump(build()) + "\n")
    return path


if __name__ == "__main__":
    print(f"wrote {write()}")
