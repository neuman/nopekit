# SPDX-License-Identifier: Apache-2.0
"""Write ``lib/``, the copies of the stock library entries the baseline was drawn
from, and the two project library tables that name them. Not a gate and not run
by any test: a record of how the committed files were made, so a reader can make
them again and diff.

    python3 packs/pcb-kicad/selftest/assets/make_libraries.py

Needs the stock libraries the baseline board and schematic were drawn from, under
/usr/share/kicad: the kicad-footprints and kicad-symbols 7.0.11 packages on the
Ubuntu 24.04 host this pack was written on (KiCad 9.0.9 itself, its libraries
from the distribution's 7.0.11 packages).

**Why the pack keeps its own copies.** KiCad's DRC compares each footprint on a
board with its copy in the library the project's table names
(``lib_footprint_mismatch``), and ERC does the same for each symbol
(``lib_symbol_mismatch``). With tables that named the host's stock library
(``${KICAD9_FOOTPRINT_DIR}``), the known-good controls read whatever library the
host had installed. What slipped through, in the holdout run on outside designs:
under KiCad 10.0.8, whose own library is newer, the baseline board read "3x
lib_footprint_mismatch (warning)", so ``pcb.drc``'s known-good control failed on a
host whose library had moved, and no KiCad 10 design could get a DRC verdict
from this pack. A control sealed against the host reads the pack's own copies
(invariant 5): the tables name ``${KIPRJMOD}/lib/...``, and the gates stage what
a table names inside the project with the board (``pcb._stage``).

Each footprint is copied byte for byte. Each symbol library holds only the symbols
the baseline schematic places (a derived symbol with the one it ``extends``), in
the order the stock file holds them, under the stock file's own header.
Rejected: setting the library checks to "ignore" in the staged project (the gate
would then hide a real drift on every project's board too, and a person's
severity is the project's to state); the whole stock libraries (about 40 MB for
the two footprint libraries and three symbol libraries, for five entries).
"""
from __future__ import annotations

import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PACK = os.path.dirname(os.path.dirname(HERE))
sys.dont_write_bytecode = True        # never leave a __pycache__ in the pack it describes
sys.path.insert(0, os.path.join(PACK, "gates"))
import _kicad as K                                                 # noqa: E402

FOOTPRINTS = "/usr/share/kicad/footprints"
SYMBOLS = "/usr/share/kicad/symbols"
LIB = os.path.join(HERE, "lib")

#: Every footprint the baseline board places, by library.
FOOTPRINT_ENTRIES = {
    "Connector_PinHeader_2.54mm": ("PinHeader_1x04_P2.54mm_Vertical",),
    "Package_TO_SOT_SMD": ("SOT-23-6",),
}

#: Every symbol the baseline schematic places, by library.
SYMBOL_ENTRIES = {
    "power": ("GND", "PWR_FLAG", "VBUS"),
    "Connector_Generic": ("Conn_01x04",),
    "Power_Protection": ("ESDA6V1BC6",),
}


def _symbols(lib: str, names: tuple[str, ...]) -> str:
    """The stock ``lib`` cut down to ``names`` (and each one's parent)."""
    tree = K.load_tree(os.path.join(SYMBOLS, lib + ".kicad_sym"))
    by_name = {str(s[1]): s for s in K.children(tree, "symbol")}
    wanted = set(names)
    for name in names:
        parent = K.value(by_name[name], "extends")
        if parent:
            wanted.add(str(parent))
    kept = [x for x in tree if not (isinstance(x, list) and K.head(x) == "symbol")
            or str(x[1]) in wanted]
    return K.dump(kept) + "\n"


def write_tables(prefix: str = "${KIPRJMOD}/lib", where: str = HERE) -> None:
    """``sym-lib-table`` and ``fp-lib-table`` in ``where`` (beside the baseline by
    default), each library named at ``prefix``."""
    rows = {"sym-lib-table": ("sym_lib_table", SYMBOL_ENTRIES, ".kicad_sym"),
            "fp-lib-table": ("fp_lib_table", FOOTPRINT_ENTRIES, ".pretty")}
    for name, (head, libs, ext) in rows.items():
        lines = [f"({head}", "\t(version 7)"]
        lines += [f'\t(lib (name "{lib}")(type "KiCad")(uri "{prefix}/{lib}{ext}")'
                  f'(options "")(descr ""))' for lib in libs]
        with open(os.path.join(where, name), "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n)\n")


def write() -> None:
    if os.path.isdir(LIB):
        for entry in os.listdir(LIB):
            path = os.path.join(LIB, entry)
            if entry.endswith((".pretty", ".kicad_sym")):
                shutil.rmtree(path) if os.path.isdir(path) else os.remove(path)
    os.makedirs(LIB, exist_ok=True)
    for lib, names in FOOTPRINT_ENTRIES.items():
        out = os.path.join(LIB, lib + ".pretty")
        os.makedirs(out, exist_ok=True)
        for name in names:
            shutil.copyfile(os.path.join(FOOTPRINTS, lib + ".pretty", name + ".kicad_mod"),
                            os.path.join(out, name + ".kicad_mod"))
    for lib, names in SYMBOL_ENTRIES.items():
        with open(os.path.join(LIB, lib + ".kicad_sym"), "w", encoding="utf-8") as fh:
            fh.write(_symbols(lib, names))
    write_tables()


if __name__ == "__main__":
    write()
    print(f"wrote {LIB} and the two tables beside it")
