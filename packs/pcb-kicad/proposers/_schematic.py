# SPDX-License-Identifier: Apache-2.0
"""How this pack writes a KiCad schematic: the pieces ``selftest/assets/make_schematic.py``
(the baseline's own schematic) and ``schematic_from_board.py`` (a schematic from a
board's pad nets) share. A helper, not a proposer: its name starts with ``_``.

**Symbols come from KiCad's installed library**, copied into the file's
``lib_symbols`` the way eeschema caches them (a derived symbol such as USBLC6-2SC6,
which ``extends`` USBLC6-2P6, is flattened into one), so the schematic carries every
pin's name and electrical type without the library.

**Connections are global labels on pin ends**, no wires: a global label's net is its
own name, as a board's nets are.

Every UUID is a ``uuid5`` of a fixed name, so two runs write the same bytes.
Standard library and the pack's own reader only: imported as ``_kicad`` from the
pack's ``gates/`` (on ``sys.path``), so the reader's quoted-string type is one type
in every file that builds and dumps a tree.
"""
from __future__ import annotations

import os
import sys
import uuid

_GATES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "gates")
sys.dont_write_bytecode = True        # never leave a __pycache__ in the pack
if _GATES not in sys.path:
    sys.path.insert(0, _GATES)
import _kicad as K                                                 # noqa: E402

#: KiCad 9.0's schematic format, the version eeschema 9.0.9 writes.
SCH_VERSION = "20250114"

_NS = uuid.UUID("5f0c3a52-9b1e-4f43-8f0e-2a6b1d0c7e11")


def uid(name: str) -> str:
    return str(uuid.uuid5(_NS, name))


def library_symbol(symbols_dir: str, lib: str, name: str) -> list:
    """``lib:name`` from ``<symbols_dir>/<lib>.kicad_sym`` as eeschema caches it in a
    schematic: renamed with its library, and a derived symbol flattened onto its
    parent (the parent's body and pins, the child's properties, units renamed to
    the child). Raises KeyError for a symbol the library does not hold."""
    tree = K.load_tree(os.path.join(symbols_dir, lib + ".kicad_sym"))
    syms = {str(s[1]): s for s in K.children(tree, "symbol")}
    sym = syms[name]
    parent_name = K.value(sym, "extends")
    if parent_name:
        parent = syms[str(parent_name)]
        props = {str(p[1]): p for p in K.children(sym, "property")}
        body = []
        for x in parent[2:]:
            if isinstance(x, list) and K.head(x) == "property" and str(x[1]) in props:
                body.append(props.pop(str(x[1])))
            elif isinstance(x, list) and K.head(x) == "symbol":
                unit = list(x)
                unit[1] = K.Q(name + str(x[1])[len(str(parent_name)):])
                body.append(unit)
            else:
                body.append(x)
        sym = ["symbol", K.Q(name), *body, *props.values()]
        # Properties go before the unit bodies, as eeschema writes them.
        sym = ([x for x in sym if not (isinstance(x, list) and K.head(x) == "symbol")]
               + [x for x in sym if isinstance(x, list) and K.head(x) == "symbol"])
    out = ["symbol", K.Q(f"{lib}:{name}")]
    for x in sym[2:]:
        if isinstance(x, list) and K.head(x) == "in_bom":
            out.append(["exclude_from_sim", "no"])    # KiCad 8's key, before in_bom
        out.append(x)
    return out


def pins(lib_sym: list) -> dict[str, tuple[float, float]]:
    """{pin number: (x, y)} of each pin's connection point, in the symbol's own
    frame (y up)."""
    found: dict[str, tuple[float, float]] = {}

    def walk(node):
        for x in node:
            if isinstance(x, list):
                if K.head(x) == "pin":
                    at = K.child(x, "at")
                    found[str(K.value(x, "number"))] = (float(at[1]), float(at[2]))
                walk(x)
    walk(lib_sym)
    return found


def units(lib_sym: list) -> set[str]:
    """The unit numbers a symbol draws (``Name_<unit>_<style>``), 0 (common to every
    unit) left out: more than one is a multi-unit part."""
    found = set()
    for x in K.children(lib_sym, "symbol"):
        tail = str(x[1]).rsplit("_", 2)
        if len(tail) == 3 and tail[1].isdigit() and tail[1] != "0":
            found.add(tail[1])
    return found


def effects(hide: bool = False, justify: str | None = None) -> list:
    node = ["effects", ["font", ["size", "1.27", "1.27"]]]
    if justify:
        node.append(["justify", justify])
    if hide:
        node.append(["hide", "yes"])
    return node


def n(v: float) -> str:
    return f"{round(v, 4):g}"


def prop(key: str, val: str, x: float, y: float, hide: bool = False) -> list:
    return ["property", K.Q(key), K.Q(val), ["at", n(x), n(y), "0"], effects(hide)]


def placed(lib_id: str, ref: str, value: str, footprint: str, x: float, y: float,
           pin_numbers: list[str], key: str, *, project: str, root_uuid: str,
           angle: int = 0, in_bom: bool = True, dnp: bool = False) -> list:
    """One placed symbol, with the instance record that names it in ``project``."""
    return ["symbol",
            ["lib_id", K.Q(lib_id)],
            ["at", n(x), n(y), str(angle)],
            ["unit", "1"],
            ["exclude_from_sim", "no"], ["in_bom", "yes" if in_bom else "no"],
            ["on_board", "yes"],
            ["dnp", "yes" if dnp else "no"],
            ["uuid", K.Q(uid(key))],
            prop("Reference", ref, x, y - 7.62, hide=ref.startswith("#")),
            prop("Value", value, x, y + 7.62),
            prop("Footprint", footprint, x, y, hide=True),
            prop("Datasheet", "", x, y, hide=True),
            *[["pin", K.Q(p), ["uuid", K.Q(uid(f"{key}:pin:{p}"))]] for p in pin_numbers],
            ["instances", ["project", K.Q(project),
                           ["path", K.Q("/" + root_uuid),
                            ["reference", K.Q(ref)], ["unit", "1"]]]]]


def global_label(net: str, x: float, y: float, left: bool) -> list:
    return ["global_label", K.Q(net),
            ["shape", "bidirectional"],
            ["at", n(x), n(y), "180" if left else "0"],
            ["fields_autoplaced", "yes"],
            effects(justify="right" if left else "left"),
            ["uuid", K.Q(uid(f"label:{net}:{x}:{y}"))],
            ["property", K.Q("Intersheetrefs"), K.Q("${INTERSHEET_REFS}"),
             ["at", n(x), n(y), "0"], effects(hide=True)]]


def no_connect(x: float, y: float, key: str) -> list:
    return ["no_connect", ["at", n(x), n(y)], ["uuid", K.Q(uid(key))]]


def sheet(root_uuid: str, lib_symbols: list, items: list, paper: str = "A4") -> list:
    """The root sheet around ``lib_symbols`` and the placed ``items``."""
    return ["kicad_sch",
            ["version", SCH_VERSION],
            ["generator", K.Q("eeschema")],
            ["generator_version", K.Q("9.0")],
            ["uuid", K.Q(root_uuid)],
            ["paper", K.Q(paper)],
            ["lib_symbols", *lib_symbols],
            *items,
            ["sheet_instances", ["path", K.Q("/"), ["page", K.Q("1")]]],
            ["embedded_fonts", "no"]]
