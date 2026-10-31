# SPDX-License-Identifier: Apache-2.0
"""pcb-kicad's reader for KiCad's own files, and the one formula it cites.

Standard library only, so the tier-0 gates run where KiCad is not installed (CI
among them) and every file they decide on is opened by Python, where the trace
sees it. ``kicad-cli`` reads its files in a subprocess the trace cannot see into,
which is why the gates that drive it are opaque and re-run on every check.

**The format.** ``.kicad_pcb``, ``.kicad_sch`` and ``.kicad_sym`` are KiCad's
S-expression files (dev-docs.kicad.org, "File Formats"): a list is ``(head
item...)``, an item is a list, a quoted string or a bare atom. :func:`parse`
keeps quoted strings apart from atoms (:class:`Q`) so :func:`dump` writes back a
file KiCad reads the same way: the known-bad fixtures are the baseline board with
one thing changed, written by this module at fixture time.

**What the board model keeps.** Only what a tier-0 gate decides on: the net table,
each footprint's reference and pads (number, net, the library pin name KiCad
calls ``pinfunction``, the pin type), and each copper track's net, width, layer
and length. Zones are counted, not modelled: a net carried by a pour as well as
by tracks is reported as such, never judged on the pour.

**The formula.** :func:`temp_rise_c` is IPC-2221's trace-heating curve fit,
cited in ``references/ipc-2221.md`` with the range it was fitted over.
"""
from __future__ import annotations

import math
import os
import re
from collections import Counter
from dataclasses import dataclass, field

# --------------------------------------------------------------------------- #
# S-expressions
# --------------------------------------------------------------------------- #


class Q(str):
    """A string that was quoted in the file. Written back quoted, always: KiCad
    reads ``"1"`` (a pad number) and ``1`` (a net code) differently."""

    __slots__ = ()


class KicadFormatError(ValueError):
    """The bytes are not a KiCad S-expression file this reader understands."""


_DELIMS = frozenset("() \t\r\n")


def parse(text: str) -> list:
    """The first top-level expression of ``text``, as nested lists of
    :class:`Q` and plain ``str`` atoms. Raises :class:`KicadFormatError` on an
    unbalanced or unterminated file rather than return part of one: a gate that
    judged a truncated board would judge a different board."""
    stack: list[list] = []
    root: list | None = None
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c in " \t\r\n":
            i += 1
        elif c == "(":
            node: list = []
            if stack:
                stack[-1].append(node)
            elif root is None:
                root = node
            else:
                raise KicadFormatError(f"a second top-level expression at offset {i}")
            stack.append(node)
            i += 1
        elif c == ")":
            if not stack:
                raise KicadFormatError(f"unbalanced ')' at offset {i}")
            stack.pop()
            i += 1
        elif c == '"':
            j, out = i + 1, []
            while True:
                if j >= n:
                    raise KicadFormatError(f"unterminated string at offset {i}")
                ch = text[j]
                if ch == "\\" and j + 1 < n:
                    nxt = text[j + 1]
                    out.append({"n": "\n", "t": "\t", "r": "\r"}.get(nxt, nxt))
                    j += 2
                elif ch == '"':
                    break
                else:
                    out.append(ch)
                    j += 1
            if not stack:
                raise KicadFormatError(f"a string outside any expression at offset {i}")
            stack[-1].append(Q("".join(out)))
            i = j + 1
        else:
            j = i
            while j < n and text[j] not in _DELIMS and text[j] != '"':
                j += 1
            if not stack:
                raise KicadFormatError(f"an atom outside any expression at offset {i}")
            stack[-1].append(text[i:j])
            i = j
    if stack or root is None:
        raise KicadFormatError("unbalanced: the file ends inside an expression"
                               if stack else "no expression in the file")
    return root


def _quote(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


def dump(node, indent: int = 0) -> str:
    """``node`` as KiCad S-expression text, one child list per line."""
    if isinstance(node, Q):
        return _quote(node)
    if isinstance(node, str):
        return node
    pad = "\t" * indent
    if not any(isinstance(x, list) for x in node):
        return pad + "(" + " ".join(dump(x) for x in node) + ")"
    head = [dump(x) for x in node if not isinstance(x, list)]
    lines = [pad + "(" + " ".join(head)]
    for x in node:
        if isinstance(x, list):
            lines.append(dump(x, indent + 1))
    lines.append(pad + ")")
    return "\n".join(lines)


def head(node) -> str:
    return node[0] if isinstance(node, list) and node and isinstance(node[0], str) else ""


def children(node, name: str) -> list[list]:
    return [x for x in node if isinstance(x, list) and head(x) == name]


def child(node, name: str) -> list | None:
    for x in node:
        if isinstance(x, list) and head(x) == name:
            return x
    return None


def value(node, name: str, index: int = 1, default=None):
    """``(name v0 v1 ...)``'s item ``index`` (1 = the first after the head)."""
    c = child(node, name)
    if c is None or len(c) <= index:
        return default
    return c[index]


# --------------------------------------------------------------------------- #
# The board
# --------------------------------------------------------------------------- #

#: Layers whose copper is on the outside of the stack: IPC-2221's "external"
#: conductor. Every other ``*.Cu`` layer is internal.
OUTER_LAYERS = frozenset({"F.Cu", "B.Cu"})


@dataclass
class Pad:
    number: str
    net: str                    # "" when the pad is on no net
    pinfunction: str            # the library pin name, "" when the board has none
    pintype: str


@dataclass
class Footprint:
    ref: str
    value: str
    lib_id: str
    pads: list[Pad] = field(default_factory=list)


@dataclass
class Track:
    net: str
    width_mm: float
    layer: str
    length_mm: float            # along the copper: an arc's arc, never its chord
    kind: str                   # "segment" | "arc"
    #: Where the copper runs: the unordered ends (and an arc's mid), rounded to
    #: 0.1 um, so the same copper written twice is counted once in a length.
    #: (What slipped through: a script-generated board wrote 6 segments twice.)
    shape: tuple = ()


@dataclass
class Board:
    version: str
    nets: dict[int, str]
    footprints: list[Footprint]
    tracks: list[Track]
    vias: int
    zone_nets: list[str]        # one entry per copper zone, its net name


def _net_name(item, nets: dict[int, str]) -> str:
    """``(net 3 "DP")`` (a pad), ``(net 3)`` (a track, by code) or ``(net "DP")``."""
    if item is None or len(item) < 2:
        return ""
    first = item[1]
    if isinstance(first, Q):
        return str(first)
    if len(item) >= 3 and isinstance(item[2], Q):
        return str(item[2])
    try:
        return nets.get(int(first), "")
    except ValueError:
        return ""


def _xy(item) -> tuple[float, float]:
    return float(item[1]), float(item[2])


def arc_length(start, mid, end) -> float:
    """The length along a KiCad arc, from its start, mid and end points (mm).

    KiCad writes ``mid`` at the arc's midpoint, so each half spans at most 180
    degrees and is ``2 r asin(chord / 2r)`` on the circle through the three
    points. Three points in a line are a straight track. (What slipped through:
    the reader took an arc's chord as its length, and nothing read the length
    until ``pcb.net_width`` printed it.)"""
    a = math.dist(start, mid)
    b = math.dist(mid, end)
    c = math.dist(start, end)
    twice_area = abs((mid[0] - start[0]) * (end[1] - start[1])
                     - (end[0] - start[0]) * (mid[1] - start[1]))
    if twice_area < 1e-12:
        return a + b
    r = a * b * c / (2.0 * twice_area)
    return 2.0 * r * (math.asin(min(1.0, a / (2.0 * r))) + math.asin(min(1.0, b / (2.0 * r))))


def _shape(*points) -> tuple:
    """A track's place, for counting the same copper once: the ends unordered,
    then any mid, each rounded to 0.1 um."""
    rounded = [(round(x, 4), round(y, 4)) for x, y in points]
    return tuple(sorted(rounded[:2])) + tuple(rounded[2:])


def footprint_ref(fp: list) -> tuple[str, str]:
    ref = val = ""
    for prop in children(fp, "property"):          # KiCad 7 and later
        if len(prop) >= 3 and prop[1] == "Reference":
            ref = str(prop[2])
        elif len(prop) >= 3 and prop[1] == "Value":
            val = str(prop[2])
    for text in children(fp, "fp_text"):           # KiCad 6
        if len(text) >= 3 and text[1] == "reference" and not ref:
            ref = str(text[2])
        elif len(text) >= 3 and text[1] == "value" and not val:
            val = str(text[2])
    return ref, val


def board_from_tree(tree: list) -> Board:
    if head(tree) != "kicad_pcb":
        raise KicadFormatError(f"not a KiCad board: the file starts with ({head(tree)} ...)")
    nets: dict[int, str] = {}
    for item in children(tree, "net"):
        if len(item) >= 3:
            try:
                nets[int(item[1])] = str(item[2])
            except ValueError:
                pass
    footprints = []
    for fp in children(tree, "footprint"):
        ref, val = footprint_ref(fp)
        pads = [Pad(number=str(p[1]) if len(p) > 1 else "",
                    net=_net_name(child(p, "net"), nets),
                    pinfunction=str(value(p, "pinfunction", default="") or ""),
                    pintype=str(value(p, "pintype", default="") or ""))
                for p in children(fp, "pad")]
        footprints.append(Footprint(ref=ref, value=val,
                                    lib_id=str(fp[1]) if len(fp) > 1 else "", pads=pads))
    tracks = []
    for seg in children(tree, "segment"):
        (x0, y0), (x1, y1) = _xy(child(seg, "start")), _xy(child(seg, "end"))
        tracks.append(Track(net=_net_name(child(seg, "net"), nets),
                            width_mm=float(value(seg, "width")),
                            layer=str(value(seg, "layer")),
                            length_mm=math.hypot(x1 - x0, y1 - y0), kind="segment",
                            shape=_shape((x0, y0), (x1, y1))))
    for arc in children(tree, "arc"):
        start, end = _xy(child(arc, "start")), _xy(child(arc, "end"))
        mid = _xy(child(arc, "mid")) if child(arc, "mid") is not None else None
        tracks.append(Track(net=_net_name(child(arc, "net"), nets),
                            width_mm=float(value(arc, "width")),
                            layer=str(value(arc, "layer")),
                            length_mm=(arc_length(start, mid, end) if mid is not None
                                       else math.dist(start, end)),
                            kind="arc",
                            shape=_shape(start, end, *([mid] if mid is not None else []))))
    zone_nets = [_net_name(child(z, "net"), nets) or str(value(z, "net_name", default=""))
                 for z in children(tree, "zone")]
    return Board(version=str(value(tree, "version", default="")), nets=nets,
                 footprints=footprints, tracks=tracks,
                 vias=len(children(tree, "via")), zone_nets=zone_nets)


def load_board(path: str) -> Board:
    """The loader ``ctx.load_file`` calls: module-level, so every gate that asks
    for one board shares one parse and each records the file."""
    with open(path, "r", encoding="utf-8") as fh:
        return board_from_tree(parse(fh.read()))


def load_tree(path: str) -> list:
    with open(path, "r", encoding="utf-8") as fh:
        return parse(fh.read())


#: The property a hierarchical sheet names its file with: ``Sheetfile`` from
#: KiCad 7, ``Sheet file`` in KiCad 6.
SHEET_FILE_PROPERTIES = ("Sheetfile", "Sheet file")


def sheet_files(root_sch: str) -> list[str]:
    """Every sub-sheet file under the root schematic ``root_sch``, as paths
    relative to its directory, each once, in the order first reached.

    A sheet file used twice (one sheet placed twice) is listed once. A sheet that
    names a file outside the root's directory, or an absolute path, raises
    :class:`KicadFormatError`: it cannot be copied beside the root, and a check
    run on the root without it would read the sheet as empty.
    """
    base = os.path.dirname(os.path.abspath(root_sch))
    found: list[str] = []
    todo = [root_sch]
    while todo:
        tree = load_tree(todo.pop(0))
        for sheet in children(tree, "sheet"):
            for prop in children(sheet, "property"):
                if len(prop) >= 3 and str(prop[1]) in SHEET_FILE_PROPERTIES:
                    rel = str(prop[2]).replace("\\", "/")
                    norm = os.path.normpath(rel)
                    if os.path.isabs(rel) or norm.startswith(".."):
                        raise KicadFormatError(f"sheet file {rel!r} is outside the "
                                               f"schematic's directory")
                    if norm not in found:
                        found.append(norm)
                        todo.append(os.path.join(base, norm))
    return found


# --------------------------------------------------------------------------- #
# Library pin names
# --------------------------------------------------------------------------- #

#: Pin names that say "no function": a part's NC or DNC pins may legitimately sit
#: on different nets (a layout that routes through an unused pin), so sharing the
#: name says nothing. ``~`` is KiCad's hidden-name placeholder.
NO_FUNCTION_NAMES = ("NC", "DNC", "N.C.", "N/C", "~", "")


def pin_name_conflicts(board: Board, ignore=NO_FUNCTION_NAMES):
    """Every footprint pin name that two or more different nets share.

    A library symbol gives two pads one name when the part joins them inside:
    a flow-through ESD array's ``I/O1`` is pins 1 and 6, a regulator's ``GND``
    is its tab and a pin. Put two different nets on them and the part shorts the
    nets, which no design rule check can see, because the copper is legal.

    Returns ``(conflicts, named, footprints_named)``: conflicts as
    ``(ref, name, {net: [pad numbers]})``; ``named`` the count of pads that carry
    a name; ``footprints_named`` how many footprints carry at least one.
    """
    skip = {s.upper() for s in ignore}
    conflicts, named, fps_named = [], 0, 0
    for fp in board.footprints:
        groups: dict[str, dict[str, list[str]]] = {}
        any_name = False
        for pad in fp.pads:
            if pad.pinfunction:
                named += 1
                any_name = True
            name = pad.pinfunction.strip()
            if not name or name.upper() in skip or not pad.net:
                continue
            groups.setdefault(name, {}).setdefault(pad.net, []).append(pad.number)
        fps_named += any_name
        for name, by_net in sorted(groups.items()):
            if len(by_net) > 1:
                conflicts.append((fp.ref, name, {k: sorted(v) for k, v in sorted(by_net.items())}))
    return conflicts, named, fps_named


# --------------------------------------------------------------------------- #
# IPC-2221 trace heating
# --------------------------------------------------------------------------- #

#: IPC-2221 (2012), section 6.2, the curve fit to its conductor-sizing chart:
#: I = k * dT^0.44 * A^0.725, I in amperes, dT in degrees C, A the conductor's
#: cross-section in square mils. k = 0.048 for an external conductor, 0.024 for
#: an internal one (half: no free convection inside the stack). Fitted over
#: 0-35 A, widths to 400 mil, rises of 10-100 C, and 0.5-3 oz copper; outside
#: that the chart is extrapolation and the verdict says so. Rejected: IPC-2152
#: (2009), which is more accurate but is a family of charts with board-thickness,
#: plane and mounting corrections and no published closed form a reader can check
#: by hand. The 2221 fit is conservative against it for external layers.
K_EXTERNAL = 0.048
K_INTERNAL = 0.024
EXP_DT = 0.44
EXP_AREA = 0.725

#: One ounce of copper per square foot is 1.378 mil (35 um) thick: the IPC
#: convention every fab's "1 oz" means. Rejected: 1.4 mil, a common rounding.
MIL_PER_OZ = 1.378
MM_PER_MIL = 0.0254

#: The fitted range, for the verdict's warning (references/ipc-2221.md).
FIT_RANGE = {"current_a": (0.0, 35.0), "width_mil": (0.0, 400.0),
             "temp_rise_c": (10.0, 100.0), "copper_oz": (0.5, 3.0)}


def temp_rise_c(current_a: float, width_mm: float, copper_oz: float, outer: bool) -> float:
    """IPC-2221's predicted rise, degrees C, for ``current_a`` through one track."""
    if width_mm <= 0 or copper_oz <= 0:
        return math.inf
    area_mil2 = (width_mm / MM_PER_MIL) * (copper_oz * MIL_PER_OZ)
    k = K_EXTERNAL if outer else K_INTERNAL
    return (abs(current_a) / (k * area_mil2 ** EXP_AREA)) ** (1.0 / EXP_DT)


def width_for_rise_mm(current_a: float, temp_rise: float, copper_oz: float, outer: bool) -> float:
    """The width at which :func:`temp_rise_c` equals ``temp_rise``: its inverse."""
    k = K_EXTERNAL if outer else K_INTERNAL
    area_mil2 = (abs(current_a) / (k * temp_rise ** EXP_DT)) ** (1.0 / EXP_AREA)
    return area_mil2 / (copper_oz * MIL_PER_OZ) * MM_PER_MIL


# --------------------------------------------------------------------------- #
# A fab's published limits, as KiCad custom rules
# --------------------------------------------------------------------------- #

def copper_layer_count(tree: list) -> int:
    """How many copper layers the board's layer table declares (``*.Cu``)."""
    layers = child(tree, "layers") or []
    return sum(1 for item in layers if isinstance(item, list) and len(item) >= 2
               and str(item[1]).endswith(".Cu"))


#: The DRC violation type each custom-rule constraint reports under, read off
#: kicad-cli 9.0.9's JSON reports (one deliberate breach of each). A finding of
#: any other type in the fab run is not the fab's: it is ``pcb.drc``'s.
CONSTRAINT_TYPES = {
    "track_width": "track_width",
    "clearance": "clearance",
    "edge_clearance": "copper_edge_clearance",
    "hole_size": "drill_out_of_range",
    "via_diameter": "via_diameter",
    "annular_width": "annular_width",
    "hole_to_hole": "hole_to_hole",
    "text_height": "text_height",
    "text_thickness": "text_thickness",
    "silk_clearance": "silk_overlap",
}

#: The other types a constraint reports under, by the item or the case. KiCad
#: 9.0.9 reports a ``hole_size`` breach on a microvia as
#: ``microvia_drill_out_of_range``, even when the breach is of the fab's own
#: via-hole rule; and two holes on one centre as ``holes_co_located``, never
#: testing ``hole_to_hole`` at all. (What slipped through, in review: neither type
#: was the fab's, so a 0.12 mm microvia drill passed JLCPCB's 0.15 mm via hole, and
#: a via on top of another passed where two 0.3 mm apart failed; ``pcb.drc``, under
#: the project's own floors and severities, could pass both.)
ALSO_REPORTED_AS = {"hole_size": ("microvia_drill_out_of_range",),
                    "hole_to_hole": ("holes_co_located",)}


def reported_as(constraint: str) -> tuple[str, ...]:
    """Every DRC violation type a breach of ``constraint`` can be reported under."""
    return (CONSTRAINT_TYPES[constraint],) + ALSO_REPORTED_AS.get(constraint, ())

#: The condition every fab file's ``silk_clearance`` rule must carry: a fab
#: publishes silkscreen to pad, and :func:`dru_text` refuses any other silk rule.
#: KiCad reports silk on silk as ``silk_overlap`` too, from the board's own
#: silk minimum (zeroed in the fab run, so only real overlaps remain), and that
#: finding is ``pcb.drc``'s. So :func:`fab_findings` leaves a ``silk_overlap`` to
#: ``pcb.drc`` only when it can show that no item is a pad. (What slipped through:
#: on a real 4-layer board the fab run counted 18 silk-on-silk overlaps, the same
#: 18 ``pcb.drc`` reported, as past JLCPCB's limits: 190 findings where 172 were
#: the fab's. The baseline has no silk on silk, so its selftest could not see it.)
SILK_TO_PAD = "A.Type == 'Pad' || B.Type == 'Pad'"

_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def kiid(text) -> str | None:
    """A pad's id as kicad-cli writes it in a report, or None where KiCad gives
    the pad a fresh random one, which no report can be matched to.

    Read off kicad-cli 9.0.9, in KiCad's own order: a bare legacy stamp of up to
    8 hex digits comes back zero-filled as ``00000000-0000-0000-0000-0000`` and the
    stamp; otherwise an uppercase, undashed or braced UUID comes back lowercase
    and dashed. No id, a padded one, a braced stamp or any other text gets a
    random one. (In review: a braced id or a short stamp read as unknown, a padded
    one as kept, and a braced stamp as kept.)"""
    if text is None:
        return None
    s = str(text).lower()
    if re.fullmatch(r"[0-9a-f]{1,8}", s):
        return "00000000-0000-0000-0000-0000" + s.zfill(8)
    if len(s) >= 2 and s[0] == "{" and s[-1] == "}":
        s = s[1:-1]
    if re.fullmatch(r"[0-9a-f]{32}", s):
        s = f"{s[:8]}-{s[8:12]}-{s[12:16]}-{s[16:20]}-{s[20:]}"
    return s if _UUID.fullmatch(s) else None


def pad_ids(tree: list) -> tuple[set[str], int]:
    """The id of every footprint pad on the board as a DRC report's items carry
    it (:func:`kiid` of its ``uuid``, KiCad 8 and later, or ``tstamp``, KiCad 6
    and 7), and how many pads KiCad gives a random id. Matched by id, not by
    an item's description, because kicad-cli translates descriptions ("Pad 1
    [GND] of U4") into the host's language.

    (What slipped through, in review: matched on the file's text alone, a pad
    with no id, an uppercase one or a legacy stamp never matched the report, and
    the fab's silk-to-pad finding was handed to ``pcb.drc``: the fab gate passed
    a silk line drawn across a pad, which the count before this one had failed.)"""
    ids, unknown = set(), 0
    for fp in children(tree, "footprint"):
        for pad in children(fp, "pad"):
            found = None
            for key in ("uuid", "tstamp"):
                found = found or kiid(value(pad, key))
            if found is None:
                unknown += 1
            else:
                ids.add(found)
    return ids, unknown


def fab_findings(doc: dict, table: dict, pads: tuple[set[str], int],
                 prefix: str = "fab") -> tuple[list, int]:
    """The violations in a fab run's report that are the fab's, and how many of
    them are counted only because the board's pads could not be told apart.

    A violation of a type one of ``table``'s rules constrains is the fab's. A
    ``silk_overlap`` is left to ``pcb.drc`` only when it is shown to be silk on
    silk: no item's id is a pad's (``pads``, from :func:`pad_ids`), its words do
    not name the run's own silk rule (``<prefix>_<name>``, the file's string,
    never translated), and no pad on the board has an id KiCad replaces with a
    random one. Where one does, every ``silk_overlap`` is counted: a pass must
    never rest on a pad the count could not see."""
    ids, unknown = pads
    types = {t for r in table["rules"] for t in reported_as(r["constraint"])}
    silk = CONSTRAINT_TYPES["silk_clearance"]
    names = [re.compile(rf"(?<!\w){re.escape(prefix)}_{re.escape(r['name'])}(?!\w)")
             for r in table["rules"] if r["constraint"] == "silk_clearance"]
    found, guessed = [], 0
    for v in doc.get("violations", []):
        kind = v.get("type")
        if kind not in types:
            continue
        if kind == silk:
            on_pad = any(kiid(item.get("uuid")) in ids for item in v.get("items", []))
            named = any(n.search(str(v.get("description", ""))) for n in names)
            if not (on_pad or named):
                if not unknown:
                    continue
                guessed += 1
        found.append(v)
    return found, guessed


def fab_kinds(found: list, table: dict) -> str:
    """``found`` grouped by type, most first, each with the names of the fab
    rules that constrain it: ``144x silk_overlap (silk_to_pad), 14x text_height
    (silk_text_height)``. Every type is named: a person reads from this line
    whether anything past the fab's limits is copper."""
    names: dict[str, list[str]] = {}
    for rule in table["rules"]:
        for kind in reported_as(rule["constraint"]):
            names.setdefault(kind, []).append(rule["name"])
    counts = Counter(v.get("type", "?") for v in found)
    return ", ".join(f"{n}x {kind} ({'/'.join(names.get(kind, ['?']))})"
                     for kind, n in counts.most_common())


def fab_table(fab: dict, layers: int, oz: float) -> dict | None:
    """The row of a fab file (``gates/fab/<name>.json``) for a board of
    ``layers`` copper layers at ``oz`` ounces outer copper, with the file's
    common rules prepended, or None when the fab publishes no row for it. A
    row's ``layers`` is inclusive both ends; its ``oz`` is ``(lo, hi]``."""
    for row in fab.get("tables", []):
        lo_l, hi_l = row["layers"]
        lo_oz, hi_oz = row["oz"]
        if lo_l <= layers <= hi_l and lo_oz < oz <= hi_oz:
            return {"name": row["name"], "rules": list(fab.get("common", [])) + list(row["rules"])}
    return None


def fab_clearance_floor(table: dict) -> float | None:
    """The copper clearance the fab publishes, or None when its row has none: the
    floor the fab run sets as the board's own minimum clearance. KiCad lets a
    pad's, a footprint's or a zone's local clearance override every custom rule,
    and holds it only at the board minimum. (What slipped through, in review: the
    fab run zeroed that minimum, so ``(clearance 0.01)`` on one pad passed a
    0.047 mm gap under JLCPCB's 0.1 mm, and ``pcb.drc`` passed it too.)"""
    mins = [float(r["min_mm"]) for r in table["rules"] if r["constraint"] == "clearance"]
    return min(mins) if mins else None


def fab_rule_min(table: dict, name: str) -> float:
    """The ``min_mm`` of the rule called ``name`` in a chosen row."""
    for rule in table["rules"]:
        if rule["name"] == name:
            return float(rule["min_mm"])
    raise KeyError(name)


def dru_text(table: dict, prefix: str) -> str:
    """A KiCad custom-rules file (``.kicad_dru``) holding exactly ``table``'s
    rules, each named ``<prefix>_<rule>``. Later rules win in KiCad, so a rule
    with a condition follows the general one it narrows (the common rules are
    written in that order)."""
    lines = ["(version 1)"]
    clearances = [r for r in table["rules"] if r["constraint"] == "clearance"]
    if len(clearances) > 1 or any(r.get("condition") for r in clearances):
        raise ValueError("a fab row holds one clearance rule, with no condition: the fab "
                         "run floors every local clearance override at it "
                         "(fab_clearance_floor), and a second or narrower rule would sit "
                         "above that floor, where an override could relax it")
    for rule in table["rules"]:
        if rule["constraint"] not in CONSTRAINT_TYPES:
            raise ValueError(f"unknown constraint {rule['constraint']!r} in rule {rule['name']!r}")
        if rule["constraint"] == "silk_clearance" and rule.get("condition") != SILK_TO_PAD:
            raise ValueError(f"rule {rule['name']!r}: a silk_clearance rule is silkscreen to "
                             f"pad, with the condition {SILK_TO_PAD!r}; any other silk rule "
                             f"would put silk on silk, which is pcb.drc's, into the fab count")
        if rule["constraint"] == "silk_clearance" and float(rule["min_mm"]) <= 0:
            raise ValueError(f"rule {rule['name']!r}: a silk_clearance rule's minimum must be "
                             f"above 0: at 0 KiCad leaves the rule's name out of the finding, "
                             f"and the fab count reads that name")
        lines.append(f'(rule "{prefix}_{rule["name"]}"')
        if rule.get("layer"):
            lines.append(f'\t(layer "{rule["layer"]}")')
        if rule.get("condition"):
            lines.append(f'\t(condition "{rule["condition"]}")')
        lines.append(f'\t(constraint {rule["constraint"]} (min {float(rule["min_mm"]):g}mm)))')
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# A project's own custom rules, and the probes that show each one is live
# --------------------------------------------------------------------------- #

#: A property a rule's condition reads: ``A.NetName``, ``B.Type``. A name followed
#: by ``(`` is a function (``A.isPlated()``), which no probe of its own can show
#: is spelled right; its rule's selector probe is all it gets.
_PROPERTY = re.compile(r"(?<![\w.])[AB]\.(\w+)\b(?!\s*\()")
_READS_B = re.compile(r"(?<![\w.])B\.\w")

#: The name every probe rule starts with, so a probe's finding is told from the
#: project's own by the rule name KiCad prints in its words (never translated).
PROBE_PREFIX = "nopekit_"


def dru_rules(text: str) -> list[list]:
    """The ``(rule ...)`` nodes of a ``.kicad_dru`` file. ``#`` lines are KiCad's
    comments and are dropped; anything :func:`parse` cannot read raises
    :class:`KicadFormatError`."""
    body = "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))
    return children(parse("(" + body + ")"), "rule")


def probe_rules(rules: list[list]) -> list[dict]:
    """The probes that show each custom rule and each property it reads is live.

    One per rule, ``nopekit_probe_<i>``: the rule's own ``layer`` and
    ``condition`` with ``(constraint assertion "false")``, so it fires on every
    item the rule selects. One per property token any condition reads,
    ``nopekit_token_<j>``: ``A.<Prop> == A.<Prop>``, which fires only on an item
    KiCad knows that property on (14, 75 and 93 items on the baseline against 0 for
    a misspelled name: measured). A ``B.`` token is probed as ``A.``: KiCad
    evaluates a one-item assertion with no B, so ``B.NetName == B.NetName`` and
    ``A.NetName == 'DP' && B.NetName == 'GND'`` each fired 0 times (measured). So
    a rule whose condition reads B is ``reads_b``: its selector probe is shown live
    only when it fires, and is never called dead.

    Each probe is ``{"name", "kind" ("rule" | "property"), "what", "text",
    "reads_b"}``. Rejected: a probe that restates the rule's own constraint at an
    impossible bound (11.0 s at 1000 mm, and an item in the board's middle is
    missed); ``disallow`` probes (a later probe whose selection covers an earlier
    one masked it fully: measured, 12 against 0).
    """
    probes, tokens = [], []
    for i, rule in enumerate(rules):
        name = str(rule[1]) if len(rule) > 1 and not isinstance(rule[1], list) else f"#{i}"
        cond = value(rule, "condition")
        parts = [f"(rule {PROBE_PREFIX}probe_{i}"]
        if child(rule, "layer") is not None:
            parts.append(dump(child(rule, "layer")))
        if cond is not None:
            parts.append(f"(condition {_quote(str(cond))})")
        parts.append('(constraint assertion "false"))')
        probes.append({"name": f"{PROBE_PREFIX}probe_{i}", "kind": "rule", "what": name,
                       "text": " ".join(parts),
                       "reads_b": bool(cond is not None and _READS_B.search(str(cond)))})
        for token in _PROPERTY.findall(str(cond or "")):
            if token not in tokens:
                tokens.append(token)
    for j, token in enumerate(tokens):
        probes.append({"name": f"{PROBE_PREFIX}token_{j}", "kind": "property", "what": token,
                       "text": (f'(rule {PROBE_PREFIX}token_{j} (condition "A.{token} == '
                                f'A.{token}") (constraint assertion "false"))'),
                       "reads_b": False})
    return probes


def probe_counts(doc: dict, names: list[str]) -> dict[str, int]:
    """How many findings of a probe run's report name each probe rule in their
    words (``Assertion failure (rule 'nopekit_probe_3')``), matched as
    :func:`fab_findings` matches a fab rule's name."""
    pats = {n: re.compile(rf"(?<!\w){re.escape(n)}(?!\w)") for n in names}
    counts = dict.fromkeys(names, 0)
    for v in doc.get("violations", []):
        words = str(v.get("description", ""))
        for n, pat in pats.items():
            if pat.search(words):
                counts[n] += 1
    return counts


# --------------------------------------------------------------------------- #
# Pads by id, and a footprint's land
# --------------------------------------------------------------------------- #

def pad_nets_by_id(tree: list) -> dict[str, str]:
    """{the id a report gives a pad (:func:`kiid`): its net name, "" for none},
    beside :func:`pad_ids`, for every pad whose id KiCad keeps."""
    nets = {}
    for item in children(tree, "net"):
        if len(item) >= 3:
            try:
                nets[int(item[1])] = str(item[2])
            except ValueError:
                pass
    out = {}
    for fp in children(tree, "footprint"):
        for pad in children(fp, "pad"):
            found = None
            for key in ("uuid", "tstamp"):
                found = found or kiid(value(pad, key))
            if found is not None:
                out[found] = _net_name(child(pad, "net"), nets)
    return out


@dataclass
class LandPad:
    number: str
    x: float                    # mm, the footprint's own frame, seen from the top
    y: float
    w: float
    h: float


def footprint_land(tree: list, ref: str) -> tuple[list[LandPad], list[str], bool]:
    """``(numbered pads, unnumbered pads' kinds, on the back)`` of footprint ``ref``,
    each pad's centre in the footprint's own frame as seen from the top.

    KiCad 9 writes a pad's ``at`` local to its footprint, and on a footprint
    flipped to ``B.Cu`` with y negated (SW90's pad 2 is (-0.75, -1.95) on the
    board and (-0.75, 1.95) in its library: measured), so y is negated back. The
    size is read as written: the limit reads min(w, h), which no rotation moves.
    Raises KeyError for a ref the board lacks."""
    for fp in children(tree, "footprint"):
        if footprint_ref(fp)[0] != ref:
            continue
        back = str(value(fp, "layer", default="")) == "B.Cu"
        pads, unnumbered = [], []
        for p in children(fp, "pad"):
            number = str(p[1]) if len(p) > 1 else ""
            if not number:
                unnumbered.append(str(p[2]) if len(p) > 2 else "?")
                continue
            at, size = child(p, "at"), child(p, "size")
            x, y = float(at[1]), float(at[2])
            pads.append(LandPad(number, x, -y if back else y, float(size[1]), float(size[2])))
        return pads, unnumbered, back
    raise KeyError(ref)


def rigid_fit(pairs: list[tuple[tuple[float, float], tuple[float, float]]]):
    """The rotation and translation that best carry the first point of each pair
    onto the second, least squares, with no mirror (a mirror would hide a land
    drawn mirrored): ``(degrees, [residual mm per pair])``.

    Closed form in 2D (Kabsch): with both sets centred, the angle is
    ``atan2(sum(u x w), sum(u . w))``."""
    n = len(pairs)
    ax = sum(a[0] for a, _ in pairs) / n
    ay = sum(a[1] for a, _ in pairs) / n
    bx = sum(b[0] for _, b in pairs) / n
    by = sum(b[1] for _, b in pairs) / n
    dot = cross = 0.0
    for (px, py), (qx, qy) in pairs:
        ux, uy, wx, wy = px - ax, py - ay, qx - bx, qy - by
        dot += ux * wx + uy * wy
        cross += ux * wy - uy * wx
    theta = math.atan2(cross, dot)
    c, s = math.cos(theta), math.sin(theta)
    residuals = [math.hypot(c * (px - ax) - s * (py - ay) + bx - qx,
                            s * (px - ax) + c * (py - ay) + by - qy)
                 for (px, py), (qx, qy) in pairs]
    return math.degrees(theta), residuals
