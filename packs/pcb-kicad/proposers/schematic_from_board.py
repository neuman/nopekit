# SPDX-License-Identifier: Apache-2.0
"""pcb.schematic_from_board — write a KiCad schematic from a board's pad nets.

    nopekit propose pcb.schematic_from_board -- --board hardware/main.kicad_pcb \\
        --symbols hardware/main.symbols.json

A proposer, never an evaluator: it writes a schematic, and nothing that writes the
schematic judges it. A board a script generated has no schematic, so ``pcb.erc`` and
``pcb.parity`` skip there, and the script's dictionary is the only record of what
connects to what. No kicad-cli command writes a schematic from a board, so this one
does, on KiCad's stock symbols: each part's symbol, placed in its own cell, with a
global label of its pad's net on each connected pin end. Once a person adopts it
(copies the files beside the board, under the board's name: a decision), the
EXISTING ``pcb.erc`` and ``pcb.parity`` judge it, and ``pcb.parity`` is the check on
this writer: a label it put on the wrong pin is a difference KiCad reports.

**Every unit of a multi-unit symbol is placed** (a dual op-amp's two halves and its
power unit, a quad gate's four gates and its supply, a dual MOSFET's two FETs), each
in its own cell under the part's one ref, and each pad's net goes on the pin of its
number in the unit that draws it; a pin every unit draws (unit 0) takes its label,
or its no-connect flag, on every unit. Body style 1 only (``_schematic.unit_pins``).
(What slipped through: the writer refused every part with more than one unit, so a
board with one had no schematic, and ``pcb.erc`` and ``pcb.parity`` skipped there.)

**What the person states, in the symbols file** (JSON, never inferred):

- ``symbols``: ``{ref or glob: "Lib:Symbol"}``. An exact ref wins, then the globs
  (``"R*"``) in the order written. A part with no stock symbol is mapped by a person
  to ``Connector_Generic:Conn_01xNN`` explicitly, never by this writer: ERC judges
  nothing for it, and the summary lists it.
- ``no_connect``: ``{ref: [pad numbers]}``, pins open on purpose; each gets a
  no-connect flag. No flag is ever placed from the board: that would launder every
  open pad into a statement.
- ``power_flags``: ``[net]``, the nets fed from off the board; each gets one
  ``power:PWR_FLAG``.
- ``library``: ``[directory]`` of more ``.kicad_sym`` files, from the project root,
  searched before KiCad's own (:data:`STOCK_VARS`, then :data:`STOCK_PATHS`).

**It refuses, exits 2, writes nothing and names every problem** when a footprint
matches no symbol, a pad with a net has no pin of its number in any unit, one pad
number carries two nets, a symbol is not found, or a statement names a pad or a net
the board does not have.

**What it writes** under ``$NOPEKIT_PROPOSAL_DIR``: ``<stem>.kicad_sch``, the
symbols flattened into ``lib_symbols`` as eeschema caches them, each Footprint field
the board's footprint name verbatim (parity compares it exactly: a nickname added,
or the field emptied, gave a footprint_symbol_mismatch for every part on a 4-layer
board, measured), in_bom and dnp from the footprint's attributes, every id a
``uuid5`` so the bytes repeat; ``sym-lib-table``, only the libraries used, a stock
one named by the path variable KiCad defines for it (:func:`stock_variable`); and
``<stem>.schematic.json``, the symbol per ref, each multi-unit part's unit map
(``{unit: [pins]}``), the refs on generic symbols, the pins left open with no
statement, the flagged nets, the variable the stock libraries are named by and the
sha256 of each ``.kicad_sym`` read. Standard library and the pack's reader only; no
pcbnew. It asks ``kicad-cli version`` for KiCad's release only when it read the
stock symbols from a fixed path.
"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.dont_write_bytecode = True        # never leave a __pycache__ in the pack
sys.path.insert(0, HERE)
import _schematic as W                                             # noqa: E402

K = W.K

#: The path variables that name KiCad's own symbol directory, searched in this
#: order after the symbols file's own ``library`` directories: KiCad 10's, KiCad
#: 9's (each installer sets its own, and KiCad defines it for its own run when the
#: environment does not), and KiCad 5's unversioned name, which an older setup
#: may still export. A directory found through one is written in the
#: sym-lib-table as ``${THAT_VARIABLE}``. Rejected: KICAD8_SYMBOL_DIR and older
#: (those releases cannot open what this writer writes, format 20250114, KiCad
#: 9's). (What slipped through: the writer read KICAD9_SYMBOL_DIR alone, and wrote
#: ``${KICAD9_SYMBOL_DIR}`` into every table, so a KiCad 10 install read nothing.)
STOCK_VARS = ("KICAD10_SYMBOL_DIR", "KICAD9_SYMBOL_DIR", "KICAD_SYMBOL_DIR")

#: Where an installer puts the stock symbols when no variable says, searched after
#: :data:`STOCK_VARS`: the Linux packages' path (Debian, Ubuntu, Fedora and Arch
#: all install there), the macOS app bundle's, and the Windows installer's for
#: KiCad 10 and 9. (What slipped through: only the Linux path was known, so on
#: macOS and Windows every symbol read "not found" until a person set a variable.)
#: Rejected: a search of the disk (slow, and it finds old releases' libraries).
STOCK_PATHS = (
    "/usr/share/kicad/symbols",
    "/Applications/KiCad/KiCad.app/Contents/SharedSupport/symbols",
    r"C:\Program Files\KiCad\10.0\share\kicad\symbols",
    r"C:\Program Files\KiCad\9.0\share\kicad\symbols",
)

#: Where kicad-cli is when it is not on PATH: inside the macOS app bundle, where
#: kicad.org's installer puts it and puts nothing on PATH
#: (``references/installing.md``; ``autoroute.py`` finds the bundle's Python the
#: same way). Rejected: PATH alone (a macOS install nobody edited PATH for would
#: answer no version, and a KiCad 10 bundle's symbols would be named by KiCad 9's
#: variable); a search of ``/Applications`` (an older bundle kept beside the new
#: one could answer first).
MAC_KICAD_CLI = "/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli"

#: KiCad 9's variable, written for a fixed directory when no kicad-cli answers
#: its version: the release whose schematic format this writer writes, so the
#: release that can open the file. The summary says it was not read. Rejected:
#: KICAD10_SYMBOL_DIR (KiCad 9 does not define it, so the oldest release that
#: opens the file would find no symbol); the directory's absolute path in the
#: table (it names one machine's install, in a file a project tracks); refusing
#: to write (a person with the symbols and no kicad-cli would get no schematic).
FALLBACK_VARIABLE = "KICAD9_SYMBOL_DIR"

_RELEASE_DIR = re.compile(r"KiCad[\\/](\d+)\.\d+[\\/]")
_VERSION = re.compile(r"^(\d+)\.\d+")

#: The keys a symbols file may hold. Any other is refused: a misspelled
#: ``no_conect`` would leave every open pin unstated, silently.
KEYS = ("symbols", "no_connect", "power_flags", "library")

#: The width of a row of cells on the sheet, mm: inside A0's 1189 mm.
ROW_MM = 1100.0
GRID = 2.54


def _snap(v: float) -> float:
    return round(v / GRID) * GRID


def _symbol_for(ref: str, symbols: dict) -> str | None:
    if ref in symbols:
        return symbols[ref]
    for pattern, lib_id in symbols.items():
        if any(c in pattern for c in "*?[") and fnmatch.fnmatchcase(ref, pattern):
            return lib_id
    return None


def _sha256(path: str) -> str:
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def _pin_order(number: str) -> tuple:
    """Pin numbers in reading order: 1, 2, 10, then A1, B2."""
    return (not number.isdigit(), int(number) if number.isdigit() else 0, number)


def stock_dirs(environ) -> list[tuple[str, str | None]]:
    """``(directory, the variable that names it, or None)`` of each place KiCad's
    own symbols may be, in search order."""
    found = [(environ[v], v) for v in STOCK_VARS if environ.get(v)]
    return found + [(p, None) for p in STOCK_PATHS]


def stock_variable(path: str) -> tuple[str, str]:
    """``(variable, how it was found)`` that names the fixed directory ``path`` in a
    sym-lib-table. KiCad defines ``KICAD<major>_SYMBOL_DIR`` as its own symbol
    directory for every run, so the major release decides the name: read off a
    Windows install's release folder, else from ``kicad-cli version``, else
    :data:`FALLBACK_VARIABLE`, said so."""
    m = _RELEASE_DIR.search(path)
    if m:
        return f"KICAD{m.group(1)}_SYMBOL_DIR", f"the release folder of {path}"
    for cli in (shutil.which("kicad-cli"), MAC_KICAD_CLI):
        if not cli or not os.path.isfile(cli):
            continue
        try:
            proc = subprocess.run([cli, "version"], capture_output=True, text=True,
                                  timeout=30, stdin=subprocess.DEVNULL)
        except (OSError, subprocess.TimeoutExpired):
            continue
        said = (proc.stdout or "").strip()
        m = _VERSION.match(said)
        if proc.returncode == 0 and m:
            return f"KICAD{m.group(1)}_SYMBOL_DIR", f"kicad-cli version {said}"
    return FALLBACK_VARIABLE, ("no kicad-cli answered its version, so KiCad 9's name, the "
                               "release whose format this file is; not read from KiCad")


def build(board_path: str, spec: dict, root: str, environ=None):
    """``(schematic tree, sym-lib-table text, summary, problems)`` for the board."""
    environ = os.environ if environ is None else environ
    problems: list[str] = []
    unknown = sorted(set(spec) - set(KEYS))
    if unknown:
        problems.append(f"the symbols file has unknown key(s) {unknown}: it takes {list(KEYS)}")
    symbols = dict(spec.get("symbols") or {})
    open_stated = {str(r): [str(p) for p in pads]
                   for r, pads in (spec.get("no_connect") or {}).items()}
    flags = [str(net) for net in (spec.get("power_flags") or [])]
    extra = [d if os.path.isabs(d) else os.path.join(root, d)
             for d in (spec.get("library") or [])]
    stock = stock_dirs(environ)
    dirs = [d for d in (*extra, *(d for d, _v in stock)) if d]
    variable_of = {d: v for d, v in reversed(stock)}

    tree = K.load_tree(board_path)
    board = K.board_from_tree(tree)
    stem = os.path.basename(board_path)[:-len(".kicad_pcb")]
    root_uuid = W.uid(f"{stem}:sheet:root")
    attrs = {}
    for fp in K.children(tree, "footprint"):
        attrs[K.footprint_ref(fp)[0]] = {str(a) for a in (K.child(fp, "attr") or [])[1:]}

    libs: dict[str, list] = {}
    read: dict[str, str] = {}
    lib_dirs: dict[str, str] = {}

    def library(lib_id: str) -> list | None:
        if lib_id in libs:
            return libs[lib_id]
        lib, _, name = lib_id.partition(":")
        for d in dirs:
            path = os.path.join(d, lib + ".kicad_sym")
            if os.path.isfile(path):
                try:
                    sym = W.library_symbol(d, lib, name)
                except KeyError:
                    problems.append(f"{lib_id}: {path} holds no symbol {name!r}")
                    return None
                read[path] = _sha256(path)
                lib_dirs.setdefault(lib, d)
                libs[lib_id] = sym
                return sym
        problems.append(f"{lib_id}: no {lib}.kicad_sym in {dirs}")
        return None

    items: list = []
    summary = {"symbols": {}, "units": {}, "generic": [], "open_unstated": {},
               "power_flags": flags}
    board_nets = {p.net for fp in board.footprints for p in fp.pads if p.net}
    for net in flags:
        if net not in board_nets:
            problems.append(f"power_flags names {net}, which no pad on the board carries")
    x0 = y0 = 25.4
    row_h = 0.0
    for fp in sorted(board.footprints, key=lambda f: f.ref):
        lib_id = _symbol_for(fp.ref, symbols)
        if lib_id is None:
            problems.append(f"{fp.ref} ({fp.value}, footprint {fp.lib_id}) matches no "
                            f"symbol: map it in `symbols`, by its ref or a glob")
            continue
        sym = library(lib_id)
        if sym is None:
            continue
        units = W.unit_pins(sym)
        pins = {number: xy for drawn in units.values() for number, xy in drawn.items()}
        by_pad: dict[str, set[str]] = {}
        for pad in fp.pads:
            if pad.number:
                by_pad.setdefault(pad.number, set()).add(pad.net)
        for number, nets in sorted(by_pad.items()):
            named = sorted(n for n in nets if n)
            if len(named) > 1:
                problems.append(f"{fp.ref} pad {number} carries two nets: {', '.join(named)}")
            if named and number not in pins:
                problems.append(f"{fp.ref} pad {number} carries {named[0]}, and {lib_id} has "
                                f"no pin {number}")
        for number in open_stated.get(fp.ref, []):
            nets = sorted(n for n in by_pad.get(number, ()) if n)
            if number not in by_pad:
                problems.append(f"no_connect names {fp.ref} pad {number}, which the board "
                                f"does not have")
            elif nets:
                problems.append(f"no_connect names {fp.ref} pad {number}, which carries "
                                f"{nets[0]}")
        summary["symbols"][fp.ref] = lib_id
        if len(units) > 1:
            summary["units"][fp.ref] = {u: sorted(drawn, key=_pin_order)
                                        for u, drawn in units.items()}
        if lib_id.split(":", 1)[0].startswith("Connector_Generic"):
            summary["generic"].append(fp.ref)
        a = attrs.get(fp.ref, set())
        unstated = summary["open_unstated"]
        first = next(iter(units))
        for unit, drawn in units.items():
            # Unit 1 keeps the ids a one-unit part had, so a board with no
            # multi-unit part writes the bytes it wrote before.
            tag = "" if unit == first else f":unit{unit}"
            xs = [p[0] for p in drawn.values()] or [0.0]
            ys = [p[1] for p in drawn.values()] or [0.0]
            w, h = (max(xs) - min(xs)) + 30.0, (max(ys) - min(ys)) + 20.0
            if x0 + w > ROW_MM:
                x0, y0, row_h = 25.4, y0 + row_h, 0.0
            x, y = _snap(x0 - min(xs) + 10.0), _snap(y0 + max(ys) + 10.0)
            x0, row_h = x0 + w, max(row_h, h)
            items.append(W.placed(lib_id, fp.ref, fp.value, fp.lib_id, x, y, sorted(pins),
                                  f"{stem}:symbol:{fp.ref}{tag}", project=stem,
                                  root_uuid=root_uuid, in_bom="exclude_from_bom" not in a,
                                  dnp="dnp" in a, unit=int(unit)))
            # Two pins a symbol stacks on one point (a TVS array's two COM) take
            # one label: KiCad joins them there, and a second label of the same
            # net there has the same uuid5. (What slipped through, when the
            # baseline's part changed to one with stacked pins: the file held one
            # id twice, and ERC and parity passed it.)
            labelled: set[tuple[float, float, str]] = set()
            for number, (px, py) in sorted(drawn.items()):
                ex, ey = x + px, y - py              # the sheet's y runs down
                nets = sorted(n for n in by_pad.get(number, ()) if n)
                if nets:
                    if (ex, ey, nets[0]) not in labelled:
                        labelled.add((ex, ey, nets[0]))
                        items.append(W.global_label(nets[0], ex, ey, left=px < 0))
                elif number in open_stated.get(fp.ref, []):
                    items.append(W.no_connect(ex, ey, f"{stem}:nc:{fp.ref}:{number}{tag}"))
                elif number not in unstated.get(fp.ref, []):
                    unstated.setdefault(fp.ref, []).append(number)

    if flags:
        flag = library("power:PWR_FLAG")
        for i, net in enumerate(flags):
            fx, fy = 25.4 + 12.7 * i, 12.7
            items.append(W.placed("power:PWR_FLAG", f"#FLG0{i + 1}", "PWR_FLAG", "", fx, fy,
                                  sorted(W.pins(flag)) if flag else ["1"],
                                  f"{stem}:flag:{net}", project=stem, root_uuid=root_uuid))
            items.append(W.global_label(net, fx, fy, left=False))

    rows, named_by, fixed = [], {}, {}
    for lib, d in sorted(lib_dirs.items()):
        if d in extra:
            uri = "${KIPRJMOD}/" + os.path.relpath(os.path.join(d, lib + ".kicad_sym"),
                                                   os.path.dirname(board_path)).replace(os.sep, "/")
        else:
            var = variable_of.get(d)
            if var is not None:
                named_by.setdefault(var, "set in the environment")
            else:
                if d not in fixed:           # one kicad-cli run per directory
                    fixed[d] = stock_variable(d)
                var, how = fixed[d]
                named_by.setdefault(var, how)
            uri = "${" + var + "}/" + lib + ".kicad_sym"
        rows.append(f'\t(lib (name "{lib}")(type "KiCad")(uri "{uri}")(options "")(descr ""))')
    table = "(sym_lib_table\n\t(version 7)\n" + "\n".join(rows) + "\n)\n"
    summary["libraries"] = {os.path.basename(p): digest for p, digest in sorted(read.items())}
    summary["stock_variables"] = named_by
    sch = W.sheet(root_uuid, [libs[k] for k in sorted(libs)], items, paper="A0")
    return sch, table, summary, problems


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="pcb.schematic_from_board",
                                 description=__doc__.splitlines()[0])
    ap.add_argument("--board", required=True, help="the .kicad_pcb, relative to the project")
    ap.add_argument("--symbols", required=True,
                    help="the JSON file of the person's statements, relative to the project")
    args = ap.parse_args(argv)

    out_dir = os.environ.get("NOPEKIT_PROPOSAL_DIR")
    if not out_dir:
        print("pcb.schematic_from_board: run me with `nopekit propose "
              "pcb.schematic_from_board -- --board ... --symbols ...` "
              "($NOPEKIT_PROPOSAL_DIR is unset)", file=sys.stderr)
        return 2
    board = os.path.abspath(args.board)
    if not board.endswith(".kicad_pcb") or not os.path.isfile(board):
        print(f"pcb.schematic_from_board: no KiCad board at {args.board}", file=sys.stderr)
        return 2
    try:
        with open(args.symbols, encoding="utf-8") as fh:
            spec = json.load(fh)
    except (OSError, ValueError) as exc:
        print(f"pcb.schematic_from_board: cannot read {args.symbols}: {exc}", file=sys.stderr)
        return 2
    if not isinstance(spec, dict):
        print(f"pcb.schematic_from_board: {args.symbols} is not a JSON object",
              file=sys.stderr)
        return 2
    try:
        sch, table, summary, problems = build(board, spec, os.getcwd())
    except (K.KicadFormatError, UnicodeDecodeError) as exc:
        print(f"pcb.schematic_from_board: the board does not parse: {exc}", file=sys.stderr)
        return 2
    if problems:
        print(f"pcb.schematic_from_board: {len(problems)} problem(s), nothing written:",
              file=sys.stderr)
        for p in problems:
            print(f"  {p}", file=sys.stderr)
        return 2
    stem = os.path.basename(board)[:-len(".kicad_pcb")]
    summary.update({"proposer": "pcb.schematic_from_board",
                    "board": os.path.relpath(board, os.getcwd()),
                    "board_sha256": _sha256(board),
                    "symbols_file": os.path.relpath(os.path.abspath(args.symbols), os.getcwd()),
                    "judged_by": "nothing yet: pcb.erc and pcb.parity judge the schematic "
                                 "once it is adopted beside the board under its name, and "
                                 "pcb.parity is the check on this writer"})
    with open(os.path.join(out_dir, stem + ".kicad_sch"), "w", encoding="utf-8") as fh:
        fh.write(K.dump(sch) + "\n")
    with open(os.path.join(out_dir, "sym-lib-table"), "w", encoding="utf-8") as fh:
        fh.write(table)
    with open(os.path.join(out_dir, stem + ".schematic.json"), "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2, sort_keys=True)
        fh.write("\n")
    left = sum(len(v) for v in summary["open_unstated"].values())
    multi = (f"; {len(summary['units'])} multi-unit part(s), every unit placed"
             if summary["units"] else "")
    print(f"pcb.schematic_from_board: {len(summary['symbols'])} part(s) on "
          f"{len({v for v in summary['symbols'].values()})} symbol(s){multi}; "
          f"{len(summary['generic'])} on a generic symbol; {left} pin(s) open with no "
          f"statement (ERC will name them)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
