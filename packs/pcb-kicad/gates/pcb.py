# SPDX-License-Identifier: Apache-2.0
"""pcb-kicad — gates for a printed circuit board drawn in KiCad.

    pcb.board_readable   tier 0  the board is a KiCad file this reader knows (the guard)
    pcb.pin_names        tier 0  pins the library gives one name carry one net, but stated series pairs
    pcb.land_pattern     tier 0  each named footprint's land fits its vendor's drawing, pads paired by function
    pcb.net_width        tier 0  every track of each current-carrying net, IPC-2221
    pcb.drc              tier 1  KiCad's design rule check, every severity, every custom rule probed
    pcb.unconnected      tier 1  KiCad's count of connections the copper never makes
    pcb.erc              tier 1  KiCad's electrical rules check of the schematic
    pcb.parity           tier 1  KiCad's comparison of the board with its schematic
    pcb.fab_rules        tier 1  KiCad's DRC with the fab's published minimums for the board's service
    pcb.spice            tier 1  one .meas of ngspice on the project's deck, against a band
    pcb.enclosure_fit    tier 1  the populated board's 3D models against the enclosure

    (gates/power.py holds the four battery rules: pcb.charge_temperature,
    pcb.charge_limits, pcb.fault_current, pcb.discharge_cutoff.)

**Where each verdict comes from.** The tier-1 gates are a tool's own answer:
``kicad-cli pcb drc``'s or ``sch erc``'s exit code and its report's count (basis
established; ``pcb.fab_rules`` sourced, for the fab's numbers it hands KiCad),
ngspice's ``.meas`` value, OpenCascade's shared volume and distance, each on a copy
staged under the gate's out directory. They read inside a subprocess the trace
cannot see into, so they are opaque and re-run on every check where their tool
exists, as openmodelica's ``omc`` gates do. The tier-0 gates read
the board with the pack's own reader (``_kicad.py``), so they run where KiCad is not
installed and every file they decide on is keyed. ``pcb.pin_names`` judges against
the library's pin names that KiCad copied onto the board from the schematic, and
``pcb.net_width`` against IPC-2221's heating curve: both sourced. ``pcb.land_pattern``'s
pairing, fit and limit are this pack's own: model-only.

**What slipped through, and which gate it is for** (from an autonomous board
pipeline this pack was extracted from; references/what-slipped-through.md):

- ``pcb.drc``: every gate in that pipeline ran at error severity, so a warning could
  never fail it. A pin-1 warning label was longer than the strip of board it sat
  on, and the fab would have clipped it. This gate runs every severity, and a
  waiver is KiCad's own exclusion, recorded in the project file.
- ``pcb.drc``, ``pcb.unconnected``: the fab export there ignored kicad-cli's exit code
  and read a report file another script also wrote, so a failed run could pass on a
  stale one. These read the exit code and the report this run wrote, and a
  disagreement between the two is an error.
- ``pcb.net_width``: a power width was set in code that never reached the board, and
  the battery path shipped at the default width for weeks of "0 violations, 0
  unconnected". No rule check can see a width nobody asked for.
- ``pcb.pin_names``: two pins that a part joins inside were wired to two different
  nets, a short through the part. The copper was legal; a person reading the
  datasheet found it.
- ``pcb.drc``, ``pcb.spice``: each passed over a tool's silence. KiCad takes a
  misspelled property in a custom rule, or a rules file it cannot parse, with exit
  0 and no word; ngspice takes an option name it does not know, and measures a
  window past the end of the run as 0 V. Each is now an error.
- ``pcb.land_pattern``: a footprint's pad numbers can agree with a datasheet's while
  its functions do not (an LED whose vendor numbers its cathode pad 2, where KiCad's
  footprint numbers it 1), so pads are paired by function, and a land drawn
  mirrored fails a fit that allows no mirror.
- ``pcb.enclosure_fit``: kicad-cli's STEP export drops a part whose 3D model it
  cannot find, with exit 0: a part the fit cannot see is a skip here. Not this
  gate: a part that stood proud of the face, through the opening cut for it, was
  found by a person on sight. The fit measures shared volume and clearance, so a
  part through an opening wide enough to keep ``pcb_enclosure_clearance_mm``
  reads clear however far it reaches, and a tighter opening fails on clearance,
  which still says nothing about how far it reaches (lenses.md, "A part taller
  than its cavity").

**Missing inputs skip; they never default.** Which board, which nets carry how much
current, how hot a trace may run, which fab, which deck and limit, which enclosure
and where the board sits in it are the project's to say.
"""
from __future__ import annotations

import dataclasses
import itertools
import json
import math
import os
import re
import shutil
import subprocess
import sys
from collections import Counter

from nopekit.gates import gate, GateContext
from nopekit.modelio import load_path as _load_path
from nopekit.models import NegativeControl, Tier, Verdict

_HERE = os.path.dirname(os.path.abspath(__file__))
K = _load_path(os.path.join(_HERE, "_kicad.py"))

#: kicad-cli pcb drc's severity flags, by the word a project writes.
SEVERITY_FLAGS = {"all": "--severity-all", "error": "--severity-error",
                  "warning": "--severity-warning"}

#: ``kicad-cli pcb drc --exit-code-violations`` exits 5 when it found anything, 0
#: when it found nothing. Any other code is kicad-cli failing, never a verdict.
EXIT_CLEAN, EXIT_VIOLATIONS = 0, 5

#: Two minutes for a DRC: a 4-layer board of about a hundred footprints takes about
#: 2 s, so only a hang reaches it. A timeout is a skip (nothing was shown), never a
#: fail.
DEFAULT_DRC_TIMEOUT_S = 120.0

#: Copper weight when the project states none: 1 oz outer, 0.5 oz inner, what a
#: fab quotes without being asked. The verdict says "[pack default]" when used.
DEFAULT_OZ_OUTER = 1.0
DEFAULT_OZ_INNER = 0.5

#: How many other stretches as hot as the worst ``pcb.net_width`` names in its
#: line; past that it says how many more, and its evidence (``as_hot``) lists
#: every one. Four keeps a power board's VBUS / VBAT / GND ties readable; naming
#: every one (a matrix of 20 equal signal nets) was rejected as a line nobody reads.
TIES_NAMED = 4


_skip, _evidence = K.skip, K.evidence


def _board_path(ctx: GateContext) -> str | None:
    rel = ctx.param("pcb_board")
    return K.project_path(ctx, rel) if rel else None


_NO_BOARD = ("no pcb_board: the .kicad_pcb this pack checks, relative to the project "
             "root — this pack will not guess which board you mean")


# --------------------------------------------------------------------------- #
# the guard
# --------------------------------------------------------------------------- #
@gate(
    id="pcb.board_readable",
    basis="model-only",
    title="The board is a KiCad file this pack's reader knows",
    claims=["pcb", "pcb-model-validity"],
    tier=Tier.INSTANT,
    settles="board file readability",
    negative_control=NegativeControl(
        fixture="selftest/bad_boards.py:orphaned_net",
        note="the baseline board with VBUS dropped from its net table, so the VBUS "
             "tracks name a net code the board no longer defines — what a script "
             "that rewrote the net table leaves behind",
    ),
)
def board_readable(ctx: GateContext) -> Verdict:
    """The validity guard: whether the tier-0 gates' numbers mean anything.

    It fails when the board is older than KiCad 6, does not parse, or has tracks or
    vias whose net code the net table does not define (formats before KiCad 10's
    20251028, which write codes). In each case ``pcb.net_width`` would read a
    net's tracks wrongly or not at all, and a net with no tracks found reads as
    nothing to judge, which is a pass by omission. ``pcb.pin_names``,
    ``pcb.net_width`` and ``pcb.land_pattern`` need it. KiCad's own gates do not:
    KiCad reads the file itself.

    It skips a file newer than the newest format this reader was checked through
    (``_kicad.reader_ceiling``): a newer format is a limit of the reader, not a
    defect in the board, so the gates that need this one read Skipped,
    "prerequisite not established", never Failing. (What slipped through: a newer
    file read FAIL, so every board saved by current KiCad read as a defective
    file, a claim on this guard as Failing and every gate that needs it as
    "prerequisite failed".) Nets are read by code and by name, as KiCad 10 writes
    them; the ceiling moves only when a KiCad 10 board passes the reader.
    """
    gid = "pcb.board_readable"
    path = _board_path(ctx)
    if path is None:
        return _skip(gid, _NO_BOARD)
    try:
        tree = ctx.load_file(path, loader=K.load_tree)
    except FileNotFoundError:
        return Verdict(gate=gid, passed=False, detail=f"no board at {path}")
    except (K.KicadFormatError, UnicodeDecodeError) as exc:
        return Verdict(gate=gid, passed=False, detail=f"the board does not parse: {exc}")
    version, problems = K.board_problems(tree)
    if version is None:
        return Verdict(gate=gid, passed=False, detail=problems[0])
    ceiling = K.reader_ceiling(version)
    if ceiling:
        return _skip(gid, f"{ceiling}: a newer format is a limit of this reader, not a "
                          f"defect in the board, so nothing on it is judged here")
    board = K.board_from_tree(tree)
    copper = sorted({t.layer for t in board.tracks})
    detail = (f"KiCad board version {version}: {len(board.net_names)} net(s), "
              f"{len(board.footprints)} footprint(s), {len(board.tracks)} track(s) on "
              f"{', '.join(copper) or 'no layer'}")
    return Verdict(gate=gid, passed=not problems,
                   detail=detail + ("; " + "; ".join(problems) if problems else ""))


# --------------------------------------------------------------------------- #
# library pin names
# --------------------------------------------------------------------------- #
def _pad_names(ctx: GateContext, board) -> tuple[dict[str, dict[str, str]], set[str]]:
    """``({ref: {pad number: pin name}}, refs named by the schematic)`` for every
    footprint on ``board``.

    Where a schematic sits beside the board (``_schematic_path``), it and every
    sheet under it are read through ``ctx.load_file`` (so each is keyed), and each
    part it places takes its pin names from the symbol the schematic caches in
    ``lib_symbols``, every unit and body style walked. Every other part keeps the
    ``pinfunction`` KiCad copied onto its pads. So a board a script generated,
    which carries no pinfunction, is named by the schematic written for it
    (``pcb.schematic_from_board``), and a board updated from its schematic reads
    the same names either way. Refs starting ``#`` (power symbols, flags) place no
    part. A placed symbol finds its cache entry by its ``lib_name`` when it has
    one, else by its ``lib_id``: KiCad caches a symbol edited away from its library
    as ``lib_name`` ("<symbol>_1"). A pad the symbol gives no name keeps its
    pinfunction: a name is never erased. (What slipped through, in review: looked
    up by ``lib_id`` alone, such a part read no names at all, and its shorted pin
    pair, which its pads' pinfunction had shown, passed.) Raises
    :class:`_kicad.KicadFormatError` or FileNotFoundError on a schematic or sheet
    that cannot be read."""
    names = {fp.ref: {p.number: p.pinfunction for p in fp.pads} for fp in board.footprints}
    sch = _schematic_path(ctx)
    if sch is None or not os.path.isfile(sch):
        return names, set()
    base = os.path.dirname(os.path.abspath(sch))
    sheets = [sch] + [os.path.join(base, rel) for rel in K.sheet_files(sch)]
    placed: dict[str, dict[str, str]] = {}
    for path in sheets:
        tree = ctx.load_file(path, loader=K.load_tree)
        cache: dict[str, dict[str, str]] = {}
        for sym in K.children(K.child(tree, "lib_symbols") or [], "symbol"):
            pins: dict[str, str] = {}

            def walk(node):
                for x in node:
                    if isinstance(x, list):
                        if K.head(x) == "pin":
                            pins[str(K.value(x, "number", default=""))] = str(
                                K.value(x, "name", default=""))
                        walk(x)
            walk(sym)
            cache[str(sym[1])] = pins
        for sym in K.children(tree, "symbol"):
            ref = next((str(p[2]) for p in K.children(sym, "property")
                        if len(p) >= 3 and p[1] == "Reference"), "")
            if not ref or ref.startswith("#"):
                continue
            key = str(K.value(sym, "lib_name", default="") or K.value(sym, "lib_id"))
            if key in cache:
                placed.setdefault(ref, {}).update(cache[key])
    for ref, pins in placed.items():
        if ref in names:
            names[ref] = {num: pins.get(num) or old for num, old in names[ref].items()}
    return names, set(placed) & set(names)


#: The keys of one entry of ``pcb_pin_names_series``.
_SERIES_KEYS = ("pairs", "source")


def _series_pairs(ctx: GateContext, board, names: dict) -> dict | str:
    """``pcb_pin_names_series`` read as ``{(ref, name): (pin, pin)}``, or why it is
    refused.

    The record is ``{ref: {pairs: [[pin, pin], ...], source}}``: on that ref, each
    pair is the two ends of one series element (a filter array's channel), which
    the library names alike although the part does not join them. Each pair must
    name two pads of the ref that the part gives one name, and no other pad of the
    ref may carry that name: a pair names the two ends of one element, and a third
    pin of that name would be joined to one of them inside, which no statement
    can say. Anything else is "the record is for another design". (Rejected:
    ``pcb_pin_names_ignore``, which would hide every conflict on that name on every
    part; a pair stated for a name, not a ref, which would clear another part's
    real short.)"""
    record = ctx.param("pcb_pin_names_series", {})
    if not isinstance(record, dict):
        return "pcb_pin_names_series is not {ref: {pairs: [[pin, pin]], source}}"
    by_ref = {fp.ref: fp for fp in board.footprints}
    out: dict[tuple[str, str], tuple[str, str]] = {}
    for ref, entry in sorted(record.items()):
        label = f"pcb_pin_names_series[{ref!r}]"
        if not isinstance(entry, dict):
            return f"{label} is not a JSON object with pairs and a source"
        unknown = sorted(set(entry) - set(_SERIES_KEYS))
        if unknown:
            return f"{label} has unknown key(s) {unknown}: it takes {list(_SERIES_KEYS)}"
        if not str(entry.get("source") or "").strip():
            return f"{label}'s source is empty: say where the part's series pairs come from"
        pairs = entry.get("pairs")
        if not isinstance(pairs, list) or not pairs or not all(
                isinstance(p, list) and len(p) == 2 for p in pairs):
            return f"{label}'s pairs must be a list of [pin, pin]"
        if ref not in by_ref:
            return f"{label}: the record is for another design: the board has no {ref}"
        pads = names.get(ref, {})
        for a, b in (tuple(str(x) for x in p) for p in pairs):
            missing = [x for x in (a, b) if x not in pads]
            if missing or a == b:
                return (f"{label}: the record is for another design: {ref} has no pad "
                        f"{', '.join(missing) or a + ' paired with itself'}")
            na, nb = (pads[a] or "").strip(), (pads[b] or "").strip()
            if not na or na != nb:
                return (f"{label}: the record is for another design: {ref} pins {a} and "
                        f"{b} are named {na or '(no name)'} and {nb or '(no name)'}, not "
                        f"one name")
            same = sorted(n for n, name in pads.items() if (name or "").strip() == na)
            if same != sorted([a, b]):
                return (f"{label}: the record is for another design: {ref} names pins "
                        f"{', '.join(same)} {na}, and a series pair is the only two pins "
                        f"of its name")
            out[(ref, na)] = tuple(sorted([a, b]))
    return out


@gate(
    id="pcb.pin_names",
    basis="sourced",
    title="Pins the library gives one name carry one net, except stated series pairs",
    claims=["pcb", "pin-map", "netlist", "short-circuit"],
    tier=Tier.INSTANT,
    settles="library pin-name agreement",
    # Prerequisite pcb.board_readable: a board whose net table this reader cannot
    #    vouch for is one whose pad nets it may have read wrongly. Isolated: the guard
    #    passes this gate's own known-bad control (test_packs.ControlsAreIsolated).
    needs=["pcb.board_readable"],
    negative_control=NegativeControl(
        fixture="selftest/bad_boards.py:split_pin_pair",
        note="the baseline board with U1 pin 5 moved from GND to VBUS: the ESD array's "
             "two COM pins now carry the supply and ground, which the part joins inside",
    ),
)
def pin_names(ctx: GateContext) -> Verdict:
    """Every pin name a footprint repeats carries one net, except a series pair
    stated for its ref.

    A library symbol gives two pins one name when the part joins them inside: a
    TVS array's ``COM`` is pins 2 and 5, a regulator's ``GND`` is its tab and a
    pin. KiCad copies those names onto the board's pads as
    ``pinfunction`` when the board is updated from its schematic. Two different
    nets on one name is a short through the part, on copper every design rule
    accepts.

    The names are the library's, not this gate's: the gate only compares. Where a
    schematic sits beside the board, the names of every part it places are read
    from the symbols it caches (:func:`_pad_names`), so a board a script generated
    with no pinfunction is named by the schematic ``pcb.schematic_from_board``
    wrote for it. A board with neither carries no names, and the gate skips
    (nothing to compare against), never passes. ``NC``-style names are ignored
    (``pcb_pin_names_ignore``) because an unused pin may legitimately sit on any
    net; a generic connector's ``Pin_1``... are each one pin's, and never conflict.

    ``pcb_pin_names_series`` states, per ref, the pairs of pins that are the two
    ends of one series element although the library names them alike (an EMI
    filter array names both ends of a channel ``CH1``): each stated pair may carry
    two nets (:func:`_series_pairs`). A pair stated on one ref clears nothing on
    another.

    What slipped through: the source pipeline's board carried no pinfunction, so
    this gate skipped on it, and two nets on two pins its ESD array joins inside
    went to a person to find. And, in the review for handheld boards, every filter
    array whose symbol names both ends of a channel alike read FAIL, with nothing a
    person could state but an ignore that hid the name on every part. In the
    review of that change, the title still said "one net" while a stated pair
    passed with two, so every reader that shows the title said more than the gate
    checked: the title names the exception.
    """
    gid = "pcb.pin_names"
    path = _board_path(ctx)
    if path is None:
        return _skip(gid, _NO_BOARD)
    board = ctx.load_file(path, loader=K.load_board)
    try:
        names, from_schematic = _pad_names(ctx, board)
    except (K.KicadFormatError, UnicodeDecodeError, FileNotFoundError) as exc:
        return Verdict(gate=gid, passed=False,
                       error=f"the schematic beside the board cannot be read: {exc}")
    series = _series_pairs(ctx, board, names)
    if isinstance(series, str):
        return Verdict(gate=gid, passed=False, error=series)
    # A copy with the names in it: the board ``ctx.load_file`` hands out is shared
    # by every gate that loads it, and is never written to.
    named_board = dataclasses.replace(board, footprints=[
        dataclasses.replace(fp, pads=[dataclasses.replace(p, pinfunction=names[fp.ref].get(
            p.number, "")) for p in fp.pads]) for fp in board.footprints])
    ignore = ctx.param("pcb_pin_names_ignore", list(K.NO_FUNCTION_NAMES))
    found, named, fps_named = K.pin_name_conflicts(named_board, ignore)
    if named == 0:
        return _skip(gid, "no pin names: no pad carries a pinfunction and no schematic "
                          "places this board's parts, so there is nothing to compare its "
                          "nets against")
    conflicts = [c for c in found if (c[0], c[1]) not in series]
    stated = [c for c in found if (c[0], c[1]) in series]
    ev = _evidence(ctx, "pin_names", {
        "footprints": len(board.footprints), "footprints_with_pin_names": fps_named,
        "named_from_schematic": sorted(from_schematic),
        "named_pads": named, "ignored_names": list(ignore),
        "series_pairs": [{"ref": r, "name": n, "pins": list(p)}
                         for (r, n), p in sorted(series.items())],
        "series_source": {r: str(e["source"]) for r, e in sorted(
            ctx.param("pcb_pin_names_series", {}).items())},
        "two_nets_as_stated": [{"ref": r, "name": n, "nets": by} for r, n, by in stated],
        "conflicts": [{"ref": r, "name": n, "nets": by} for r, n, by in conflicts]})
    if conflicts:
        worst = "; ".join(f"{r} {n}: " + ", ".join(f"{net} on {'/'.join(p)}"
                                                   for net, p in by.items())
                          for r, n, by in conflicts[:3])
        detail = (f"{len(conflicts)} pin name(s) carry more than one net, a short "
                  f"through the part: {worst}")
    else:
        detail = (f"every {'other ' if stated else ''}repeated pin name carries one net "
                  f"({named} named pad(s) on {fps_named} of {len(board.footprints)} "
                  f"footprint(s))")
    if stated:
        detail += (f"; {len(stated)} stated series pair(s) carry two nets, as "
                   f"pcb_pin_names_series says: " + ", ".join(
                       f"{r} {n} ({'/'.join(series[(r, n)])})" for r, n, _by in stated))
    return Verdict(gate=gid, passed=not conflicts, measured=len(conflicts), limit=0,
                   comparator="<=", units="pin names", detail=detail, evidence=ev)


# --------------------------------------------------------------------------- #
# a footprint's land against its vendor's drawing
# --------------------------------------------------------------------------- #
#: The most pad assignments the land fit tries. Every assignment is tried, each
#: with its own fit, while the product, over each function several pads share
#: (MP, GND, COM), of its count factorial stays within it. Past it the fit is
#: anchored (:func:`_land_fit`), and the anchored search costs the SUM of those
#: factorials, held to the same number. 5040 is 7!, a ground row of seven pads; a
#: part past it either way errors rather than guess. (What slipped through: with
#: the product alone, KiCad's stock USB-C 16P receptacle, whose symbol repeats GND
#: x4, VBUS x4, D+ x2 and D- x2 and whose footprint repeats S1 x4, came to
#: 4!*4!*2*2*4! = 55,296 pairings, so every board with one errored; the anchored
#: search tries 4!+4!+2+2+4! = 76.) Rejected: a greedy nearest-pad pairing, which
#: a land drawn mirrored can satisfy; a higher cap (the product grows faster than
#: any cap a check run can afford).
MAX_ASSIGNMENTS = 5040

#: How :func:`_land_fit` may pair a board pad with a drawing's pad. ``function``,
#: the pin name, is the land's: ``pcb.land_pattern`` pairs by nothing else, since a
#: footprint may number a pad the other way from its vendor (an LED whose vendor
#: numbers its cathode 2) and still be right. ``number`` registers a drawing that
#: carries no functions onto a footprint (a keep-out drawn around a module's
#: numbered pads), never to judge a land. Rejected: number-first pairing for the
#: land (it reads FAIL when a footprint numbers a same-function row differently
#: from the vendor, which is harmless).
PAIRINGS = ("function", "number")

#: The keys of one pad in a drawing record.
_LAND_KEYS = ("number", "function", "x", "y", "w", "h")


def _drawing(record) -> list[dict] | str:
    """A drawing record's pads, in the board's frame (y down), or why it is refused."""
    if not isinstance(record, dict) or not isinstance(record.get("pads"), list):
        return "it is not a JSON object with a list of pads"
    if record.get("view") != "top":
        return f"its view is {record.get('view')!r}: a drawing is read as seen from the top"
    if record.get("y_axis") not in ("up", "down"):
        return ("it does not say which way its y axis runs (y_axis: \"up\" as a drawing "
                "is printed, \"down\" as a KiCad footprint is): read the wrong way, the "
                "land is mirrored")
    pads = []
    for p in record["pads"]:
        if not isinstance(p, dict) or any(k not in p for k in _LAND_KEYS):
            return f"a pad lacks one of {list(_LAND_KEYS)}: {p!r}"
        try:
            x, y, w, h = (float(p[k]) for k in ("x", "y", "w", "h"))
        except (TypeError, ValueError):
            return f"a pad's x, y, w or h is not a number: {p!r}"
        if w <= 0 or h <= 0:
            return f"a pad's w and h must be above 0: {p!r}"
        pads.append({"number": str(p["number"]), "function": str(p["function"]),
                     "x": x, "y": -y if record["y_axis"] == "up" else y, "w": w, "h": h})
    return pads


def _pairing(groups: list, fb: dict, perm) -> tuple[list, list]:
    """``(point pairs, meta)`` of one assignment: each group's board pads with
    the drawing pads ``perm`` gives it, in order. A pair carries the drawing
    pad's centre first, the board pad's second; meta is ``(key, board pad number,
    drawing pad number, limit mm)``, the limit half the smaller side of the
    smaller of the two pads."""
    pairs, meta = [], []
    for key, assigned in zip(groups, perm):
        for b, v in zip(fb[key], assigned):
            pairs.append(((v["x"], v["y"]), (b.x, b.y)))
            meta.append((key, b.number, v["number"], min(b.w, b.h, v["w"], v["h"]) / 2.0))
    return pairs, meta


def _judged(res: list, meta: list) -> tuple[float, int]:
    """``(worst residual over its limit, index of that pad)``."""
    worst = max(range(len(res)), key=lambda i: res[i] / meta[i][3])
    return res[worst] / meta[worst][3], worst


def _land_fit(ref: str, board_pads: list, vendor: list,
              pair_by: str = "function") -> dict | str:
    """Pair ``board_pads`` ([(function, LandPad)]) with ``vendor`` pads by
    ``pair_by`` (:data:`PAIRINGS`: the function, else the pad number) and fit the
    drawing onto the land rigidly, no mirror. Returns the fit, or why it cannot be
    made.

    Where every assignment within the repeated keys costs no more than
    :data:`MAX_ASSIGNMENTS`, each is tried, each with its own fit, and the one
    whose worst pad is least past its limit is kept. Past it, the fit is anchored:
    the rigid transform is fitted on the pads whose key is on one pad alone; with
    that transform fixed, the repeated keys no longer depend on each other, so
    each one's pads are assigned on their own, the assignment whose worst pad is
    least past its limit; then the drawing is fitted once more on every pair and
    judged as before. The anchors fix the rotation and the offset only; the last
    fit, on every pair, is what finds a mirror, and only where those pads do not
    all sit on one line. Two anchors, or anchors on one line (a USB-C
    receptacle's CC1, CC2, SBU1 and SBU2 sit on one row), fit their own mirror
    image with no residual, since a mirror of points on one line is a half turn
    and an offset. (What slipped through, in the review of P4: this docstring
    said a land drawn mirrored misfits the anchors.) With fewer than
    two anchors, or a sum past the cap, it errors. Every assignment the
    full search could afford is still tried in full, so no land that fitted before
    the anchored search existed reads differently now.

    The fit's ``function`` fields hold the key it paired by, which
    ``paired_by`` names.
    """
    if pair_by not in PAIRINGS:
        raise ValueError(f"pair_by {pair_by!r}: pads pair by one of {list(PAIRINGS)}")
    word = "function" if pair_by == "function" else "number"
    fb: dict[str, list] = {}
    fv: dict[str, list] = {}
    for f, pad in board_pads:
        fb.setdefault(pad.number if pair_by == "number" else f, []).append(pad)
    for pad in vendor:
        fv.setdefault(pad[pair_by], []).append(pad)
    for f, pads in fv.items():
        if f not in fb:
            return f"{ref}: the drawing's {word} {f} is on no pad of the board's footprint"
        if len(fb[f]) != len(pads):
            return (f"{ref}: the drawing has {len(pads)} pad(s) of {word} {f}, the board "
                    f"{len(fb[f])}")
    groups = sorted(fv)
    if sum(len(fv[f]) for f in groups) < 2:
        return f"{ref}: fewer than two pads pair by {word}, and one pad fixes no land"
    tries = math.prod(math.factorial(len(fv[f])) for f in groups)
    search = f"every assignment ({tries})"
    if tries <= MAX_ASSIGNMENTS:
        best = None
        for perm in itertools.product(*(itertools.permutations(fv[f]) for f in groups)):
            pairs, meta = _pairing(groups, fb, perm)
            angle, res = K.rigid_fit(pairs)
            key, worst = _judged(res, meta)
            if best is None or key < best[0] - 1e-12:
                best = (key, angle, res, meta, worst)
        _key, angle, res, meta, worst = best
    else:
        anchors = [f for f in groups if len(fv[f]) == 1]
        repeated = [f for f in groups if len(fv[f]) > 1]
        cost = sum(math.factorial(len(fv[f])) for f in repeated)
        if len(anchors) < 2 or cost > MAX_ASSIGNMENTS:
            why = (f"fewer than two of its {word}s sit on one pad alone to anchor the fit"
                   if len(anchors) < 2 else
                   f"{cost} ways even with the fit anchored on its {len(anchors)} pad(s) "
                   f"of a {word} of their own")
            return (f"{ref}: {tries} ways to pair its repeated {word}s, past "
                    f"{MAX_ASSIGNMENTS}, and {why}: split a function the part repeats "
                    f"(GND_1, GND_2)")
        anchor_pairs = [((fv[f][0]["x"], fv[f][0]["y"]), (fb[f][0].x, fb[f][0].y))
                        for f in anchors]
        cx = sum(a[0] for a, _ in anchor_pairs) / len(anchors)
        cy = sum(a[1] for a, _ in anchor_pairs) / len(anchors)
        if max(math.hypot(a[0] - cx, a[1] - cy) for a, _ in anchor_pairs) < 1e-9:
            return (f"{ref}: its {len(anchors)} pad(s) of a {word} of their own sit on one "
                    f"point, which fixes no rotation to anchor the fit")
        _theta, carry = K.rigid_transform(anchor_pairs)
        chosen = []
        for f in groups:
            if len(fv[f]) == 1:
                chosen.append(tuple(fv[f]))
                continue
            pick = None
            for perm in itertools.permutations(fv[f]):
                res = [math.dist(carry((v["x"], v["y"])), (b.x, b.y))
                       for b, v in zip(fb[f], perm)]
                lims = [min(b.w, b.h, v["w"], v["h"]) / 2.0 for b, v in zip(fb[f], perm)]
                key = (max(r / lim for r, lim in zip(res, lims)), sum(r * r for r in res))
                if pick is None or key[0] < pick[0][0] - 1e-12 or (
                        abs(key[0] - pick[0][0]) <= 1e-12 and key[1] < pick[0][1] - 1e-12):
                    pick = (key, perm)
            chosen.append(pick[1])
        pairs, meta = _pairing(groups, fb, chosen)
        angle, res = K.rigid_fit(pairs)
        _key, worst = _judged(res, meta)
        search = (f"anchored on {len(anchors)} pad(s) of a {word} of their own, "
                  f"{cost} assignment(s) of the rest")
    return {"ref": ref, "paired_by": pair_by, "search": search,
            "rotation_deg": round(angle, 3),
            "rms_mm": round(math.sqrt(sum(r * r for r in res) / len(res)), 4),
            "worst": {"function": meta[worst][0], "board_pad": meta[worst][1],
                      "drawing_pad": meta[worst][2], "residual_mm": round(res[worst], 4),
                      "limit_mm": round(meta[worst][3], 4)},
            "pairs": [{"function": f, "board_pad": bn, "drawing_pad": vn,
                       "residual_mm": round(r, 4), "limit_mm": round(lim, 4)}
                      for (f, bn, vn, lim), r in zip(meta, res)]}


@gate(
    id="pcb.land_pattern",
    # model-only: the pairing by function, the fit and its limit are this pack's
    #    own; the vendor's drawing is an input, not the logic, and no standard or
    #    published method is cited for the criterion. (In review: labelled
    #    sourced, which PACK_FORMAT reserves for logic that follows a cited source.)
    basis="model-only",
    title="Each footprint's land agrees with its vendor's drawing, pad by pin function",
    claims=["pcb", "land-pattern"],
    tier=Tier.INSTANT,
    settles="footprint land agrees with the vendor drawing",
    # Prerequisite pcb.board_readable: a board this reader cannot vouch for may
    #    hold pads it read wrongly. Isolated: the guard passes this gate's own
    #    known-bad control.
    needs=["pcb.board_readable"],
    negative_control=NegativeControl(
        fixture="selftest/bad_boards.py:mirrored_land",
        note="the baseline board with U1's pad positions swapped 1<->3 and 4<->6, "
             "numbers and nets kept: the SOT-23-6 land drawn mirrored, which every "
             "design rule and the netlist accept",
    ),
)
def land_pattern(ctx: GateContext) -> Verdict:
    """Each footprint ``pcb_land_drawings`` names, against its vendor's drawing:
    pads paired by pin function, never by number, then one rigid fit (rotation
    and translation, no mirror) of the drawing onto the land. The verdict is the
    pad furthest past its limit: its centre residual (mm) against half the smaller
    side of the smaller of the two pads it pairs, a limit of this pack's own (no
    recalled tolerance is used). Only the centres are judged: a pad of the wrong
    size, or turned 90 degrees on the right centre, passes (PACK.md).

    What slipped through: a footprint whose pad numbers match the datasheet's can
    still put a function on the wrong pad (an LED whose vendor numbers its cathode
    pad 2, so a land paired by number fits upside down), and a switch's land drawn
    from another part's drawing put its middle pad well off where the vendor's
    sits. DRC and the netlist accept both. And a part that repeats many functions
    (a USB-C receptacle: GND, VBUS, D+, D-, the shield) had more pairings than the
    full search can try, and errored on every board: past that count the fit is
    anchored on the pads of a function of their own (:func:`_land_fit`), still
    never by number.

    ``pcb_land_drawings`` is ``{ref: drawing record}``, each a JSON file from the
    project root: ``{part, source, view: "top", y_axis: "up"|"down", pads:
    [{number, function, x, y, w, h}]}`` in mm, a person's transcription of the
    vendor's drawing (or their conversion of the vendor's footprint). The board's
    function for each pad is its pin name (:func:`_pad_names`: the schematic's
    symbol, else the pad's pinfunction). Board pads whose function the drawing
    omits are named and not judged; a board pad with no name whose number the
    drawing holds is an error, since only its number could pair it.
    """
    gid = "pcb.land_pattern"
    path = _board_path(ctx)
    if path is None:
        return _skip(gid, _NO_BOARD)
    drawings = ctx.param("pcb_land_drawings")
    if not isinstance(drawings, dict) or not drawings:
        return _skip(gid, "no pcb_land_drawings: which footprint to hold to which vendor "
                          "drawing ({\"U1\": \"hardware/lands/u1.json\"}) is the project's "
                          "to say, from the drawings it holds")
    tree = ctx.load_file(path, loader=K.load_tree)
    board = ctx.load_file(path, loader=K.load_board)
    try:
        names, _ = _pad_names(ctx, board)
    except (K.KicadFormatError, UnicodeDecodeError, FileNotFoundError) as exc:
        return Verdict(gate=gid, passed=False,
                       error=f"the schematic beside the board cannot be read: {exc}")
    fits, notes = [], []
    for ref, rel in sorted(drawings.items()):
        rel = str(rel)
        src = rel if os.path.isabs(rel) else os.path.join(ctx.root, rel)
        try:
            record = ctx.load_file(src, loader=_load_json)
        except (OSError, ValueError) as exc:
            return Verdict(gate=gid, passed=False,
                           error=f"{ref}: the drawing {rel} cannot be read: {exc}")
        vendor = _drawing(record)
        if isinstance(vendor, str):
            return Verdict(gate=gid, passed=False, error=f"{ref}: the drawing {rel}: {vendor}")
        try:
            pads, unnumbered = K.footprint_land(tree, ref)
        except KeyError:
            return Verdict(gate=gid, passed=False,
                           error=f"{ref}: the board has no footprint {ref}, and the drawing "
                                 f"{rel} is for it")
        drawn = {p["number"] for p in vendor}
        functions = {p["function"] for p in vendor}
        nameless = [p.number for p in pads if not names.get(ref, {}).get(p.number)]
        bad = sorted(set(nameless) & drawn)
        if bad:
            return Verdict(gate=gid, passed=False,
                           error=f"{ref}: board pad(s) {', '.join(bad)} carry no pin name, so "
                                 f"only their number could pair them, and this gate never "
                                 f"pairs by number: give the part a schematic symbol or "
                                 f"pinfunction")
        named = [(names[ref][p.number], p) for p in pads if p.number not in nameless]
        fit = _land_fit(ref, [(f, p) for f, p in named if f in functions], vendor)
        if isinstance(fit, str):
            return Verdict(gate=gid, passed=False, error=fit)
        left = Counter([f"{p.number} ({f})" for f, p in named if f not in functions]
                       + [f"{n} (no name)" for n in nameless])
        left = [k + (f" x{left[k]}" if left[k] > 1 else "") for k in sorted(left)]
        if left:
            notes.append(f"{ref}: not drawn, not judged: {', '.join(left)}")
        if unnumbered:
            notes.append(f"{ref}: {len(unnumbered)} unnumbered pad(s) ({', '.join(unnumbered)}) "
                         f"not judged")
        fit["drawing"] = rel
        fit["source"] = str(record.get("source", ""))
        fits.append(fit)
    worst = max(fits, key=lambda f: f["worst"]["residual_mm"] / f["worst"]["limit_mm"])
    w = worst["worst"]
    ev = _evidence(ctx, "land_pattern", {"fits": fits, "notes": notes})
    passed = w["residual_mm"] <= w["limit_mm"]
    anchored = (f" ({worst['search']})" if worst["search"].startswith("anchored") else "")
    detail = (f"{worst['ref']} pad {w['board_pad']} ({w['function']}) sits {w['residual_mm']:g} mm "
              f"from the drawing's pad {w['drawing_pad']} after the best rigid fit (no "
              f"mirror) vs {w['limit_mm']:g} mm (half its smaller pad's smaller side); RMS "
              f"{worst['rms_mm']:g} mm over {len(worst['pairs'])} pad(s) paired by function"
              f"{anchored}, rotation {worst['rotation_deg']:g} deg")
    if len(fits) > 1:
        detail += f"; {len(fits)} footprint(s) fitted: " + ", ".join(
            f"{f['ref']} {f['worst']['residual_mm']:g}/{f['worst']['limit_mm']:g} mm"
            for f in fits)
    if notes:
        detail += "; " + "; ".join(notes)
    return Verdict(gate=gid, passed=passed, measured=w["residual_mm"], limit=w["limit_mm"],
                   comparator="<=", units="mm", detail=detail, evidence=ev)


# --------------------------------------------------------------------------- #
# trace heating
# --------------------------------------------------------------------------- #
def _length_once(rows: list) -> float:
    """The length of a stretch's rows, each piece of copper (its layer and its
    ``Track.shape``) counted once."""
    seen, length = set(), 0.0
    for r in rows:
        if (r[4], r[7]) not in seen:
            seen.add((r[4], r[7]))
            length += r[6]
    return length


@gate(
    id="pcb.net_width",
    basis="sourced",
    title="Every track of each current-carrying net stays inside its heating limit",
    claims=["pcb", "trace-width", "current-capacity", "power-integrity"],
    tier=Tier.INSTANT,
    settles="trace temperature rise",
    # Prerequisite pcb.board_readable: a track whose net code the table does not
    #    define is a track this gate cannot attribute to a net, and a net it found
    #    no tracks for reads as nothing to judge. Isolated: the guard passes this
    #    gate's own known-bad control.
    needs=["pcb.board_readable"],
    negative_control=NegativeControl(
        fixture="selftest/bad_boards.py:narrow_power",
        note="the baseline board with every VBUS track narrowed to the width at which "
             "IPC-2221 puts the baseline current 15% past the baseline's own rise limit",
    ),
)
def net_width(ctx: GateContext) -> Verdict:
    """IPC-2221's temperature rise of every track of each named net; the hottest decides.

    dT = (I / (k * A^0.725))^(1/0.44), A the track's cross-section in square mils
    (width x copper thickness), k 0.048 outside the stack and 0.024 inside
    (references/ipc-2221.md). ``pcb_net_currents_a`` names the nets and their
    currents, ``pcb_temp_rise_limit_c`` the rise a track may reach. The verdict is
    the worst track of any named net.

    Every track of the net is judged, not only the one the current takes: the
    board does not say which path is which. A net carried by a pour as well as by
    tracks is judged on its tracks, which is conservative. Vias, pads and pours are
    not judged at all (PACK.md, "cannot settle").
    """
    gid = "pcb.net_width"
    path = _board_path(ctx)
    if path is None:
        return _skip(gid, _NO_BOARD)
    currents = ctx.param("pcb_net_currents_a")
    if not isinstance(currents, dict) or not currents:
        return _skip(gid, "no pcb_net_currents_a: which nets carry how much current "
                          "({\"VBUS\": 0.5}) is a design fact this pack will not invent")
    limit = ctx.param("pcb_temp_rise_limit_c")
    if limit is None:
        return _skip(gid, "no pcb_temp_rise_limit_c: how hot a track may run above "
                          "ambient is a product decision this pack will not invent")
    limit = float(limit)
    oz_out = ctx.param("pcb_copper_oz_outer")
    oz_in = ctx.param("pcb_copper_oz_inner")
    defaulted = [name for name, v in (("outer", oz_out), ("inner", oz_in)) if v is None]
    oz_out = DEFAULT_OZ_OUTER if oz_out is None else float(oz_out)
    oz_in = DEFAULT_OZ_INNER if oz_in is None else float(oz_in)
    board = ctx.load_file(path, loader=K.load_board)

    rows, unrouted = [], []
    for net, amps in sorted(currents.items()):
        tracks = [t for t in board.tracks if t.net == net]
        if not tracks:
            unrouted.append(net)
            continue
        for t in tracks:
            outer = t.layer in K.OUTER_LAYERS
            rise = K.temp_rise_c(float(amps), t.width_mm, oz_out if outer else oz_in, outer)
            rows.append((rise, net, float(amps), t.width_mm, t.layer, outer, t.length_mm,
                         t.shape))
    if not rows:
        return _skip(gid, f"none of {sorted(currents)} has a track on this board "
                          f"(carried by pours or pads alone?): nothing this gate can judge")
    rise, net, amps, width, _layer, outer, _length, _shape = max(rows)
    reported = round(rise, 1)
    # The hottest stretches: a net's tracks at one width on one side of the stack
    # heat alike, so each is named with the layers it runs on and its length, the
    # same copper counted once; and every other stretch that reads as hot at the
    # precision the line prints (another net's, or the same net's at another width
    # or side) is named after it, up to TIES_NAMED. (What slipped through:
    # the line named a net's narrowest track and one layer, where the same net
    # ran at that width on an outer layer too, and cooler, so the word gave a
    # reader cause to widen the outer stretch, which would not lower the rise;
    # an inner track can be the hottest beside a narrower outer one, where
    # "narrowest" is false; and in a tie, as on the baseline's
    # VBUS and GND, the line named one net and not the other.)
    stretches: dict[tuple, list] = {}
    for r in rows:
        if round(r[0], 1) == reported:
            stretches.setdefault((r[1], r[3], r[5], r[2]), []).append(r)
    first = (net, width, outer, amps)
    order = [first] + sorted(k for k in stretches if k != first)
    passed = reported <= limit
    lo, hi = K.FIT_RANGE["temp_rise_c"]
    # The fitted range is held over every stretch the line says reaches its rise,
    # each value named with its net. (What slipped through, in review: it read
    # only the first, so a tie at 36 A was named as hot with no caveat.)
    outside = []
    if not lo <= limit <= hi:
        outside.append(f"a {limit:g} C limit")
    for key in order:
        if key[1] / K.MM_PER_MIL > K.FIT_RANGE["width_mil"][1]:
            outside.append(f"a {key[1]:g} mm track ({key[0]})")
        if key[3] > K.FIT_RANGE["current_a"][1]:
            outside.append(f"{key[3]:g} A ({key[0]})")
    outside = list(dict.fromkeys(outside))
    ev = _evidence(ctx, "net_width", {
        "formula": "dT = (I / (k * A^0.725))^(1/0.44), A = width_mil * 1.378 * oz",
        "source": "IPC-2221 (2012) section 6.2; references/ipc-2221.md",
        "copper_oz": {"outer": oz_out, "inner": oz_in, "defaulted": defaulted},
        "limit_c": limit, "unrouted_nets": unrouted,
        "worst_per_net": {n: max((r for r in rows if r[1] == n), default=None)
                          for n in sorted({r[1] for r in rows})},
        "as_hot": [{"net": k[0], "width_mm": k[1], "side": "outer" if k[2] else "inner",
                    "current_a": k[3], "layers": sorted({r[4] for r in stretches[k]}),
                    "length_mm": round(_length_once(stretches[k]), 3)} for k in order]})

    def oz_of(is_outer: bool) -> str:
        side = "outer" if is_outer else "inner"
        return (f"{oz_out if is_outer else oz_in:g} oz"
                f"{' [pack default]' if side in defaulted else ''}")

    def where(key: tuple) -> str:
        layers = ", ".join(sorted({r[4] for r in stretches[key]}))
        return f"{key[1]:g} mm on {layers} ({_length_once(stretches[key]):.1f} mm)"

    detail = (f"{net}: hottest track {where(first)} rises {reported:g} C at {amps:g} A "
              f"(IPC-2221 {'external' if outer else 'internal'}, {oz_of(outer)}) "
              f"vs {limit:g} C")
    ties = order[1:]
    for key in ties[:TIES_NAMED]:
        detail += (f"; as hot: {key[0]} {where(key)} at {key[3]:g} A, "
                   f"{'external' if key[2] else 'internal'} {oz_of(key[2])}")
    if len(ties) > TIES_NAMED:
        detail += (f"; and {len(ties) - TIES_NAMED} more stretch(es) as hot "
                   f"(evidence: as_hot)")
    if unrouted:
        detail += f"; no tracks on {', '.join(unrouted)} (not judged)"
    if outside:
        detail += f"; outside IPC-2221's fitted range: {', '.join(outside)}"
    return Verdict(gate=gid, passed=passed, measured=reported, limit=limit,
                   comparator="<=", units="C", detail=detail, evidence=ev)


# --------------------------------------------------------------------------- #
# KiCad's own checks
# --------------------------------------------------------------------------- #
#: What kicad-cli reads beside a board or a schematic. By the project's name: its
#: project file (design rules, net classes, ERC and DRC exclusions, which is where
#: a waiver lives) and custom rules. By fixed name: the project's own library
#: tables, without which a symbol reads as from an unknown library on any host
#: whose KiCad was never configured.
STEM_FILES = (".kicad_pro", ".kicad_dru")
DIR_FILES = ("sym-lib-table", "fp-lib-table")


def _stem(path: str) -> str:
    for ext in (".kicad_pcb", ".kicad_sch", ".kicad_pro"):
        if path.endswith(ext):
            return path[:-len(ext)]
    return path


def _stage(ctx: GateContext, gid: str, path: str, *, board: bool, schematic: bool):
    """Copy what kicad-cli will read into ``<out>/pcb-kicad/<gate>/``: the board
    and/or the root schematic with every sheet under it, and the project files
    beside them. Returns ``(staged stem, stage dir)`` or an error Verdict.

    Staged so kicad-cli writes nothing in the project (it saves a .kicad_prl and
    rewrites the .kicad_pro beside any board it opens), and so the copy, a Python
    read, keys every file even though the subprocess's own reads are invisible.
    A file left behind by an earlier run is removed first: a run must never read
    a sheet the project no longer has.
    """
    stem = _stem(path)
    name = os.path.basename(stem)
    src_dir = os.path.dirname(stem)
    marker = ctx.out_path("pcb-kicad", gid.replace(".", "_"), ".stage")
    stage = os.path.dirname(marker)
    for old in os.listdir(stage):
        old_path = os.path.join(stage, old)
        if os.path.isdir(old_path):
            shutil.rmtree(old_path)
        else:
            os.remove(old_path)
    wanted = []
    if board:
        wanted.append(name + ".kicad_pcb")
    if schematic:
        sch = name + ".kicad_sch"
        if not os.path.isfile(os.path.join(src_dir, sch)):
            return None
        try:
            sheets = K.sheet_files(os.path.join(src_dir, sch))
        except (K.KicadFormatError, UnicodeDecodeError) as exc:
            return Verdict(gate=gid, passed=False, error=f"the schematic does not parse: {exc}")
        except FileNotFoundError as exc:
            return Verdict(gate=gid, passed=False,
                           error=f"a sheet the schematic names is missing: {exc.filename}")
        wanted += [sch, *sheets]
    for rel in wanted:
        src = os.path.join(src_dir, rel)
        if not os.path.isfile(src):
            return Verdict(gate=gid, passed=False, detail=f"no file at {src}")
        dst = os.path.join(stage, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copyfile(src, dst)
    for rel in [name + ext for ext in STEM_FILES] + list(DIR_FILES):
        if os.path.isfile(os.path.join(src_dir, rel)):
            shutil.copyfile(os.path.join(src_dir, rel), os.path.join(stage, rel))
    return os.path.join(stage, name), stage


def _kicad_cli(ctx: GateContext, gid: str, stage: str, argv: list[str], report: str):
    """Run ``kicad-cli argv`` writing its JSON report to ``<stage>/<report>``;
    ``(exit code, report, evidence)`` or a skip/error Verdict.

    Any exit but 0 (nothing found) or 5 (something found, with
    ``--exit-code-violations``) is kicad-cli failing to run, and errors. So does a
    run that wrote no report: the report must be this run's, never an old one.
    """
    out = os.path.join(stage, report)
    if os.path.exists(out):
        os.remove(out)
    timeout = float(ctx.param("pcb_drc_timeout_s", DEFAULT_DRC_TIMEOUT_S) or
                    DEFAULT_DRC_TIMEOUT_S)
    argv = ["kicad-cli", *argv[:-1], "--exit-code-violations", "--format", "json",
            "--units", "mm", "-o", out, argv[-1]]
    what = " ".join(argv[1:3])
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                              stdin=subprocess.DEVNULL, cwd=stage)
    except FileNotFoundError as exc:
        return _skip(gid, f"could not launch kicad-cli: {exc}")
    except subprocess.TimeoutExpired:
        return _skip(gid, f"kicad-cli {what} exceeded {timeout:g} s (pcb_drc_timeout_s) "
                          f"and was killed: nothing was shown")
    log = os.path.join(stage, "kicad-cli.log")
    with open(log, "w", encoding="utf-8") as fh:
        fh.write(" ".join(argv) + f"\nexit {proc.returncode}\n{proc.stdout}\n{proc.stderr}")
    if proc.returncode not in (EXIT_CLEAN, EXIT_VIOLATIONS) or not os.path.isfile(out):
        return Verdict(gate=gid, passed=False,
                       error=f"kicad-cli {what} exited {proc.returncode} without a "
                             f"report: {(proc.stderr or proc.stdout).strip()[:200]}",
                       evidence=[log])
    with open(out, encoding="utf-8") as fh:
        doc = json.load(fh)
    return proc.returncode, doc, [out, log]


def _drc(ctx: GateContext, gid: str, path: str, severity: str, *, parity: bool = False):
    """``kicad-cli pcb drc`` on a staged copy of the board (and, for parity, its
    schematic): ``(code, report, evidence)`` or a skip/error Verdict."""
    if not os.path.isfile(path):
        return Verdict(gate=gid, passed=False, detail=f"no board at {path}")
    staged = _stage(ctx, gid, path, board=True, schematic=parity)
    if staged is None:
        return _skip(gid, f"no schematic beside the board ({os.path.basename(_stem(path))}"
                          f".kicad_sch): KiCad compares a board only with the root "
                          f"schematic of the same name, and this board has none")
    if isinstance(staged, Verdict):
        return staged
    stem, stage = staged
    argv = ["pcb", "drc", SEVERITY_FLAGS[severity]]
    if parity:
        argv.append("--schematic-parity")
    return _kicad_cli(ctx, gid, stage, argv + [stem + ".kicad_pcb"], "drc.json")


def _found(doc: dict) -> list:
    """Every item a DRC report lists, of every kind."""
    return (list(doc.get("violations", [])) + list(doc.get("unconnected_items", []))
            + list(doc.get("schematic_parity", [])))


def _kinds(items) -> str:
    counts = Counter(f"{v.get('type', '?')} ({v.get('severity', '?')})" for v in items)
    return ", ".join(f"{n}x {k}" for k, n in counts.most_common(4))


@gate(
    id="pcb.drc",
    basis="established",
    title="KiCad's design rule check finds nothing, at every severity",
    claims=["pcb", "design-rules", "manufacturability", "clearance"],
    tier=Tier.BUILD,
    settles="design rule violations",
    requires_tools=["kicad-cli"],
    negative_control=NegativeControl(
        fixture="selftest/bad_boards.py:fat_track",
        note="the baseline board with U1's D+ track widened to 1.0 mm, which runs its "
             "copper into the ground pad beside it",
    ),
)
def drc(ctx: GateContext) -> Verdict:
    """``kicad-cli pcb drc --severity-all --exit-code-violations``: the verdict is
    KiCad's exit code. The report is read for the count and the kinds, and an exit
    code that disagrees with the report is an error, never a pass.

    ``pcb_drc_severity`` narrows it (``error``, ``warning``); the default is every
    severity, because a check at error severity cannot see a warning and the fab
    still prints it. To waive one finding, exclude it in KiCad: the exclusion is
    saved in the project file, by name, and this gate honours it.
    """
    gid = "pcb.drc"
    path = _board_path(ctx)
    if path is None:
        return _skip(gid, _NO_BOARD)
    severity = str(ctx.param("pcb_drc_severity", "all") or "all")
    if severity not in SEVERITY_FLAGS:
        return Verdict(gate=gid, passed=False,
                       error=f"pcb_drc_severity {severity!r} is not one of "
                             f"{sorted(SEVERITY_FLAGS)}")
    run = _drc(ctx, gid, path, severity)
    if isinstance(run, Verdict):
        return run
    code, doc, ev = run
    found = _found(doc)
    if (code == EXIT_CLEAN) != (not found):
        return Verdict(gate=gid, passed=False, evidence=ev,
                       error=f"kicad-cli exited {code} but its report lists "
                             f"{len(found)} item(s): the two disagree")
    detail = (f"KiCad DRC at {severity} severities: "
              + (f"{len(found)} finding(s): {_kinds(found)}" if found else "nothing found"))
    dead: list[str] = []
    if os.path.isfile(_stem(path) + ".kicad_dru"):
        probed = _probe_rules(ctx, gid, path)
        if isinstance(probed, Verdict):
            return probed
        dead, note, probe_ev = probed
        ev += probe_ev
        detail += f"; {note}"
    # A dead rule beside a clean run is an error: the run cannot show what the
    # rule was written to hold. Beside a finding it stays a fail, because a
    # finding KiCad made is real, and the detail names the dead rule too.
    if dead and code == EXIT_CLEAN:
        return Verdict(gate=gid, passed=False, evidence=ev,
                       error=f"a custom rule cannot fire, so the clean run shows nothing "
                             f"about it: {'; '.join(dead)}")
    return Verdict(gate=gid, passed=code == EXIT_CLEAN, measured=len(found), limit=0,
                   comparator="<=", units="findings", detail=detail, evidence=ev)


#: The rule a probe run adds when no probe fired at all, to tell a rules file
#: KiCad dropped whole from rules that each select nothing: it selects every
#: item. KiCad drops the whole file on one syntax error (an unknown constraint
#: ``track_widht``, a missing paren) without a word on stdout or stderr: measured.
CANARY = '(rule nopekit_canary (constraint assertion "false"))'


def _probe_rules(ctx: GateContext, gid: str, path: str):
    """Show that each of the board's own custom rules, and each property its
    conditions read, selects something on this board: ``(dead, note, evidence)``
    or an error Verdict.

    What slipped through: a rule KiCad cannot evaluate is silent. A misspelled
    property (``A.NetNmae``, ``A.Pin_Nmae``) gives 0 violations, exit 0 and empty
    stderr; ``!(A.Pin_Nmae == 'zz')`` reads true on all 93 baseline items; and one
    syntax error drops every rule in the file. In each case the DRC run reads
    clean. So the project's file is staged again, verbatim, with
    :func:`_kicad.probe_rules`' probes appended to it (a syntax error then drops
    the probes too, which shows), in one kicad-cli run. A probe that fired 0 times
    there is run again alone, the project's file and that probe: several
    assertion rules on one item can mask each other (on a measured board, probes
    fired fewer times together than alone), and only a probe still at 0 alone is
    dead. When no probe fires, a canary that
    selects everything tells a file KiCad dropped from rules that select nothing.
    A rule whose condition reads B is probed on its A side, the ``&&`` terms that
    read A alone (``_kicad.probe_rules``), and dead when that side selects nothing;
    one with no such term is never called dead: still at 0 alone, it is said to be
    not shown live. (Measured on a generated board: a custom rule on one net whose
    condition read B inside an OR fired 0 times beside its token probes and dozens
    of times alone.) Live means the rule selects an item, not that its
    constraint can apply to one: a ``track_width`` rule that selects only vias is
    live (PACK.md).
    Cost: one more kicad-cli run, about 1.3 s on a 4-layer board."""
    src = _stem(path) + ".kicad_dru"
    text = ctx.load_file(src, loader=_read_text)
    name = os.path.basename(src)
    try:
        rules = K.dru_rules(text)
    except K.KicadFormatError as exc:
        return ([f"{name} does not parse ({exc}), and KiCad drops a rules file it cannot "
                 f"parse, every rule with it"], f"{name} does not parse", [])
    probes = K.probe_rules(rules)
    if not probes:
        return [], f"{name} holds no rule to probe", []
    staged = _stage(ctx, "pcb.drc_probe", path, board=True, schematic=False)
    if isinstance(staged, Verdict):
        return staged
    stem, stage = staged
    evidence: list[str] = []

    def run(extra: list[str]) -> dict | Verdict:
        with open(stem + ".kicad_dru", "w", encoding="utf-8") as fh:
            fh.write(text.rstrip("\n") + "\n" + "\n".join(extra) + "\n")
        out = _kicad_cli(ctx, gid, stage, ["pcb", "drc", "--severity-all",
                                          stem + ".kicad_pcb"], "drc.json")
        if isinstance(out, Verdict):
            return out
        return out[1]

    doc = run([p["text"] for p in probes])
    if isinstance(doc, Verdict):
        return doc
    counts = K.probe_counts(doc, [p["name"] for p in probes])
    if not any(counts.values()):
        canary = run([CANARY])
        if isinstance(canary, Verdict):
            return canary
        if not K.probe_counts(canary, ["nopekit_canary"])["nopekit_canary"]:
            return ([f"KiCad dropped the whole rules file (a syntax error drops every "
                     f"rule): {name}"], f"KiCad dropped {name}", evidence)
    alone = {}
    for p in probes:
        if counts[p["name"]] == 0:
            single = run([p["text"]])
            if isinstance(single, Verdict):
                return single
            alone[p["name"]] = K.probe_counts(single, [p["name"]])[p["name"]]
            counts[p["name"]] = alone[p["name"]]
    dead, unshown = [], []
    for p in probes:
        if counts[p["name"]]:
            continue
        if p["reads_b"]:
            unshown.append(p["what"])
        elif p["kind"] == "rule":
            dead.append(f"rule {p['what']} selects nothing on this board"
                        + (f" (its A side, {p['a_side']}, selects no item)"
                           if p["a_side"] else ""))
        else:
            dead.append(f"property {p['what']}: no item has it (misspelled, or nothing "
                        f"on this board has it)")
    evidence += _evidence(ctx, "drc_probes", {
        "rules_file": src, "probes": [dict(p, fired=counts[p["name"]]) for p in probes],
        "run_alone": alone})
    rules_n = sum(p["kind"] == "rule" for p in probes)
    note = (f"{rules_n} custom rule(s) and {len(probes) - rules_n} propert(y/ies) of "
            f"{name} probed, {len(probes) - len(dead) - len(unshown)} shown live")
    if unshown:
        note += (f"; not shown live, its condition reads B, which a one-item probe "
                 f"cannot: {', '.join(unshown)}")
    if dead:
        note += f"; dead: {'; '.join(dead)}"
    return dead, note, evidence


@gate(
    id="pcb.unconnected",
    basis="established",
    title="Every connection the netlist asks for is made in copper",
    # `copper-connectivity` is this gate's alone. What slipped through: on a board a
    # script generated, with no schematic, `connectivity` also binds pcb.parity,
    # which skips there, so a claim "every connection is made" read Skipped beside
    # this gate's pass, and no tag let a person claim what the board supports.
    claims=["pcb", "connectivity", "netlist", "copper-connectivity"],
    tier=Tier.BUILD,
    settles="unconnected items",
    requires_tools=["kicad-cli"],
    negative_control=NegativeControl(
        fixture="selftest/bad_boards.py:open_net",
        note="the baseline board with the D- track from J1 to U1 deleted, leaving the "
             "data line open between the input header and the protector",
    ),
)
def unconnected(ctx: GateContext) -> Verdict:
    """KiCad's own count of unconnected items, from a DRC run at error severity
    (an unconnected item is always an error). Narrower than ``pcb.drc``: a claim
    that every net is complete binds here, and stays Checked through a silk
    warning that ``pcb.drc`` fails on. Tagged ``copper-connectivity``, it binds
    here alone, so it holds on a board with no schematic too."""
    gid = "pcb.unconnected"
    path = _board_path(ctx)
    if path is None:
        return _skip(gid, _NO_BOARD)
    run = _drc(ctx, gid, path, "error")
    if isinstance(run, Verdict):
        return run
    _code, doc, ev = run
    items = list(doc.get("unconnected_items", []))
    nets = sorted({i.get("description", "").split("[", 1)[-1].split("]", 1)[0]
                   for v in items for i in v.get("items", []) if "[" in i.get("description", "")})
    detail = (f"{len(items)} unconnected item(s) on {', '.join(nets[:6]) or '?'}"
              if items else "every connection the netlist asks for is made")
    return Verdict(gate=gid, passed=not items, measured=len(items), limit=0,
                   comparator="<=", units="items", detail=detail, evidence=ev)


# --------------------------------------------------------------------------- #
# the schematic
# --------------------------------------------------------------------------- #
def _schematic_path(ctx: GateContext) -> str | None:
    """``pcb_schematic`` when the project names one, else the root schematic of
    the board's project (KiCad's own rule: the board's name, ``.kicad_sch``)."""
    rel = ctx.param("pcb_schematic")
    if rel:
        rel = str(rel)
        return rel if os.path.isabs(rel) else os.path.join(ctx.root, rel)
    board = _board_path(ctx)
    return None if board is None else _stem(board) + ".kicad_sch"


@gate(
    id="pcb.erc",
    basis="established",
    title="KiCad's electrical rules check finds nothing in the schematic, at every severity",
    claims=["pcb", "schematic", "electrical-rules", "netlist"],
    tier=Tier.BUILD,
    settles="electrical rule violations",
    requires_tools=["kicad-cli"],
    negative_control=NegativeControl(
        fixture="selftest/bad_boards.py:label_typo",
        note="the baseline schematic with U1 pin 6's D+ label spelled 'D+' instead "
             "of 'DP': one net under two names, so pin 6 joins nothing",
    ),
)
def erc(ctx: GateContext) -> Verdict:
    """``kicad-cli sch erc --severity-all --exit-code-violations`` on a staged copy of
    the root schematic, every sheet under it and the project files: the verdict is
    KiCad's exit code, and an exit code that disagrees with the report is an error.

    ERC reads the schematic alone: pins left open, a label no other label shares
    (one net under two spellings), a power input nothing drives, two outputs
    tied together, a symbol whose library copy has moved. A board can be
    perfect copper for a wrong schematic, so a claim about the circuit binds
    here as well as to ``pcb.parity``. ``pcb_erc_severity`` narrows it
    (``error``, ``warning``); a waiver is KiCad's own ERC exclusion, saved in the
    project file.
    """
    gid = "pcb.erc"
    path = _schematic_path(ctx)
    if path is None:
        return _skip(gid, _NO_BOARD)
    if not os.path.isfile(path):
        return _skip(gid, f"no schematic at {path}: set pcb_schematic, or keep the root "
                          f"schematic beside the board under the board's name")
    severity = str(ctx.param("pcb_erc_severity", "all") or "all")
    if severity not in SEVERITY_FLAGS:
        return Verdict(gate=gid, passed=False,
                       error=f"pcb_erc_severity {severity!r} is not one of "
                             f"{sorted(SEVERITY_FLAGS)}")
    staged = _stage(ctx, gid, path, board=False, schematic=True)
    if isinstance(staged, Verdict):
        return staged
    stem, stage = staged
    run = _kicad_cli(ctx, gid, stage, ["sch", "erc", SEVERITY_FLAGS[severity],
                                       stem + ".kicad_sch"], "erc.json")
    if isinstance(run, Verdict):
        return run
    code, doc, ev = run
    found = [v for sheet in doc.get("sheets", []) for v in sheet.get("violations", [])]
    if (code == EXIT_CLEAN) != (not found):
        return Verdict(gate=gid, passed=False, evidence=ev,
                       error=f"kicad-cli exited {code} but its report lists "
                             f"{len(found)} item(s): the two disagree")
    where = "; ".join(f"{v.get('type', '?')}: "
                      + ", ".join(i.get("description", "?") for i in v.get("items", [])[:2])
                      for v in found[:3])
    detail = (f"KiCad ERC at {severity} severities over {len(doc.get('sheets', []))} "
              f"sheet(s): " + (f"{len(found)} finding(s): {_kinds(found)} ({where})"
                               if found else "nothing found"))
    return Verdict(gate=gid, passed=code == EXIT_CLEAN, measured=len(found), limit=0,
                   comparator="<=", units="findings", detail=detail, evidence=ev)


@gate(
    id="pcb.parity",
    basis="established",
    title="The board and its schematic agree: every part, value, footprint and net",
    claims=["pcb", "schematic-parity", "netlist", "connectivity"],
    tier=Tier.BUILD,
    settles="schematic to board parity",
    requires_tools=["kicad-cli"],
    negative_control=NegativeControl(
        fixture="selftest/bad_boards.py:schematic_drift",
        note="the baseline schematic with U1 pin 6 moved to D-, and the board not "
             "updated: the board still routes D+ there",
    ),
)
def parity(ctx: GateContext) -> Verdict:
    """``kicad-cli pcb drc --schematic-parity --severity-all``: KiCad's comparison of
    the board with the root schematic of its project, the board's name with
    ``.kicad_sch``. The verdict is the report's schematic-parity count: a part on
    one and not the other, a value or footprint that differs, a pad on a net the
    schematic does not give it. The run's exit code must agree that something was
    found whenever the report lists anything, or it errors.

    Copper findings in the same run are ``pcb.drc``'s, not this gate's: a board
    that matches its schematic exactly can still fail its design rules.
    Parity items are warnings in KiCad, so the run is at every severity always.
    """
    gid = "pcb.parity"
    path = _board_path(ctx)
    if path is None:
        return _skip(gid, _NO_BOARD)
    run = _drc(ctx, gid, path, "all", parity=True)
    if isinstance(run, Verdict):
        return run
    code, doc, ev = run
    items = list(doc.get("schematic_parity", []))
    if (code == EXIT_CLEAN) != (not _found(doc)):
        return Verdict(gate=gid, passed=False, evidence=ev,
                       error=f"kicad-cli exited {code} but its report lists "
                             f"{len(_found(doc))} item(s): the two disagree")
    # An open pin is agreement, not a difference: KiCad 9.0.9 names every
    # unconnected schematic pin's net ``unconnected-(<ref>-<pin>-Pad<n>)``, even
    # under a no-connect flag, and reports the pad it sits on, which has no net on
    # the board, as "Pad missing net given by schematic (unconnected-(...))". On a
    # generated board with a written schematic that was every difference parity
    # reported: measured. Counted as agreement only when every item is a pad the
    # board gives no net (matched by id, as the fab count is, never by the
    # translated words)
    # and the words hold KiCad's net name, which is not translated. A pad WITH a
    # net against an unconnected-( net is still a difference.
    no_net = {i for i, net in K.pad_nets_by_id(ctx.load_file(path, loader=K.load_tree)).items()
              if not net}
    agreed = [v for v in items if v.get("type") == "net_conflict" and v.get("items")
              and all(K.kiid(i.get("uuid")) in no_net for i in v.get("items", []))
              and "(unconnected-(" in str(v.get("description", ""))]
    items = [v for v in items if not any(v is a for a in agreed)]
    where = "; ".join(f"{v.get('description', '?')}" for v in items[:3])
    detail = (f"{len(items)} difference(s) between board and schematic: {where}"
              if items else "the board matches its schematic: every part, value, "
                            "footprint and pad net")
    if agreed:
        detail += f"; {len(agreed)} open pin(s) sit on pads with no net"
    return Verdict(gate=gid, passed=not items, measured=len(items), limit=0,
                   comparator="<=", units="differences", detail=detail, evidence=ev)


# --------------------------------------------------------------------------- #
# the fab's limits
# --------------------------------------------------------------------------- #
FAB_DIR = os.path.join(_HERE, "fab")


def _load_json(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _zeroed_project(src: str | None, dst: str, table: dict) -> set[str]:
    """The project file for the fab run, and the types it raised from "ignore".

    Every board-setup minimum is set to 0, so each finding of a constrained type
    in the fab run is the fab rule's and never the project's own floor (which
    ``pcb.drc`` already judges), except silk on silk, which KiCad still reports
    as ``silk_overlap`` at a floor of 0 and ``_kicad.fab_findings`` leaves to
    ``pcb.drc``. The one exception is the minimum clearance, set to the fab's own
    (``_kicad.fab_clearance_floor``): it is the only floor KiCad holds a pad's,
    footprint's or zone's local clearance override to, so an override below the
    fab's number is raised to it. An override above it still binds in this run,
    and copper inside it is counted as the fab's: a false fail, never a false
    pass (GitHub #13). A constrained type the project set to
    "ignore" is raised to "error": KiCad does not test an ignored type, even with
    ``--severity-all``. Exclusions (each a person's waiver of one finding), net
    classes and everything else stay as they are.

    (What slipped through, in review: a project that set ``track_width`` or
    ``silk_overlap`` to "ignore" hid the fab's own rule of that type, and a pad's
    ``(clearance 0.01)`` overrode the fab's clearance rule; the fab gate passed a
    track under the fab's minimum and a 0.047 mm gap under its 0.1 mm, and
    ``pcb.drc``, under the project's own settings, passed both.)"""
    doc = _load_json(src) if src and os.path.isfile(src) else {"meta": {"version": 1}}
    settings = doc.setdefault("board", {}).setdefault("design_settings", {})
    rules = settings.setdefault("rules", {})
    for key, val in list(rules.items()):
        if key.startswith("min_") and key != "min_resolved_spokes" \
                and isinstance(val, (int, float)) and not isinstance(val, bool):
            rules[key] = 0.0
    floor = K.fab_clearance_floor(table)
    if floor is not None:
        rules["min_clearance"] = floor
    severities = settings.setdefault("rule_severities", {})
    raised = set()
    for kind in {t for c in K.CONSTRAINT_TYPES for t in K.reported_as(c)}:
        if severities.get(kind) == "ignore":
            severities[kind] = "error"
            raised.add(kind)
    # Refused, never written: KiCad drops a project file it cannot parse without
    # a word and runs on defaults, the clearance floor gone with it. (What slipped
    # through, in review: a NaN or a 1e999 in the project, which Python reads and
    # writes back as NaN or Infinity, did exactly that, and a pad's override
    # relaxed the fab again.)
    try:
        text = json.dumps(doc, indent=2, allow_nan=False, ensure_ascii=False)
        text.encode("utf-8")
    except (ValueError, UnicodeEncodeError) as exc:
        raise ValueError(f"the project file holds a value KiCad cannot read ({exc}): it "
                         f"would ignore the whole project, and the fab's clearance floor "
                         f"with it") from None
    with open(dst, "w", encoding="utf-8") as fh:
        fh.write(text)
    return raised


def _service_gaps(tree: list, service: dict) -> tuple[list[str], list[str]]:
    """``(gaps, what the board is built of)``: the gaps are each dielectric the
    board's stackup names and each via kind it holds that ``service`` does not
    list (``_kicad.stackup_dielectrics``, ``_kicad.via_kinds``).

    A board that states no stackup is read as KiCad's default dielectric, and
    both lists say so. (What slipped through, in the review of P4: only the pass
    said it, so a polyimide flex board whose stackup was never opened skipped on
    "the board has dielectric FR4", a material its file does not hold.)"""
    materials, stated = K.stackup_dielectrics(tree)
    kinds = K.via_kinds(tree)
    offered = {K.material_key(m) for m in service["materials"]}
    default = "" if stated else " (KiCad's default: the board states no stackup)"
    gaps = [f"dielectric {m or '(unnamed)'}{default}" for m in materials
            if K.material_key(m) not in offered]
    gaps += [f"{kinds[k]} {k} via(s)" for k in K.VIA_KINDS
             if kinds[k] and k not in service["via_types"]]
    built = [f"dielectric {', '.join(m or '(unnamed)' for m in materials) or '(none named)'}"
             + default]
    built += [f"{kinds[k]} {k} via(s)" for k in K.VIA_KINDS if kinds[k]]
    return gaps, built


@gate(
    id="pcb.fab_rules",
    basis="sourced",
    title=("The board stays inside the named fab's published limits for the service it "
           "is ordered on"),
    claims=["pcb", "manufacturability", "fab-limits"],
    tier=Tier.BUILD,
    settles="fab capability limits",
    requires_tools=["kicad-cli"],
    negative_control=NegativeControl(
        fixture="selftest/bad_boards.py:under_fab_width",
        note="the baseline board with U1's D+ track from J1 narrowed to 85% of the "
             "fab's minimum width for its layer count and copper: legal by KiCad's "
             "default rules, unbuildable at that fab",
    ),
)
def fab_rules(ctx: GateContext) -> Verdict:
    """KiCad's DRC run with the fab's published minimums as the only custom rules:
    track width and spacing, via and plated-hole drills, via pad, annular ring,
    hole-to-hole, copper to the routed edge, silkscreen text and pad clearance.

    ``pcb_fab`` names the fab: ``jlcpcb`` (``gates/fab/jlcpcb.json``, each number
    quoted from the fab's capability page with the day it was read), or a path,
    from the project root, to a file of the same shape. A fab file lists the
    services it publishes limits for, each with the dielectrics and via kinds it
    builds; ``pcb_fab_service`` names the one the board is ordered on, and may be
    left out when the file has one. A board whose stackup names a dielectric, or
    that holds a via kind, the service does not list skips, naming the gap: a
    polyimide flex or a microvia board is never judged on a rigid FR-4 service's
    rows. (What slipped through, in the review for handheld boards: a flex or
    rigid-flex wearable and an HDI board with microvias were held to the standard
    FR-4 rows, and could read PASS on rules that are not their process.) The
    guard reads the stackup and vias with this pack's reader, so a board newer
    than the reader was checked through skips (``_kicad.reader_ceiling``). The row
    is picked by the board's copper layer count and ``pcb_copper_oz_outer``. The
    rules are written
    as the staged board's ``.kicad_dru`` and the staged project's own minimums are
    zeroed (its minimum clearance set to the fab's), so a finding of a
    constrained type is the fab's, except silk on silk: KiCad reports it as
    ``silk_overlap`` too, the type the fab's silk-to-pad rule raises, and it is
    ``pcb.drc``'s (``_kicad.fab_findings``; where the board's pads cannot be told
    apart, every silk overlap is counted here). Every other finding in the run
    (silk clipped by the mask, an unconnected item) is ``pcb.drc``'s, judged there
    under the project's own severities, not here; one of a type the project
    ignores is said apart, since ``pcb.drc`` does not see it. The project's own
    custom rules do not apply in this run (and where it has some, the rest are
    said to be not the fab's, since a rule of its own may waive them in
    ``pcb.drc``), a constrained type it set to "ignore" is raised to "error", and a
    local clearance override below the fab's clearance is held at it: one written
    to relax a constraint must not relax the fab. An override above the fab's
    clearance still binds and fails copper inside it (GitHub #13).
    """
    gid = "pcb.fab_rules"
    path = _board_path(ctx)
    if path is None:
        return _skip(gid, _NO_BOARD)
    fab_name = ctx.param("pcb_fab")
    if not fab_name:
        return _skip(gid, "no pcb_fab: which fab builds this board (jlcpcb, or a path to "
                          "a fab file) is a purchasing decision this pack will not invent")
    fab_name = str(fab_name)
    if fab_name.endswith(".json"):
        fab_path = fab_name if os.path.isabs(fab_name) else os.path.join(ctx.root, fab_name)
    else:
        fab_path = os.path.join(FAB_DIR, fab_name + ".json")
    if not os.path.isfile(fab_path):
        known = sorted(n[:-5] for n in os.listdir(FAB_DIR) if n.endswith(".json"))
        return Verdict(gate=gid, passed=False,
                       error=f"pcb_fab {fab_name!r}: no fab file at {fab_path} "
                             f"(bundled: {', '.join(known)})")
    fab = ctx.load_file(fab_path, loader=_load_json)
    who = fab.get("fab", fab_name)
    wanted = ctx.param("pcb_fab_service")
    try:
        service = K.fab_service(fab, None if wanted is None else str(wanted))
    except ValueError as exc:
        return Verdict(gate=gid, passed=False, error=f"pcb_fab {fab_name!r}: {exc}")
    if service is None:
        listed = ", ".join(str(s["name"]) for s in K.fab_services(fab))
        return _skip(gid, f"{who} publishes limits for several services ({listed}) and "
                          f"there is no pcb_fab_service: which one this board is ordered "
                          f"on is a purchasing decision this pack will not guess")
    if not os.path.isfile(path):
        return Verdict(gate=gid, passed=False, detail=f"no board at {path}")
    tree = ctx.load_file(path, loader=K.load_tree)
    # The service guard reads the stackup and the vias with this pack's reader, so
    # a file newer than the reader was checked through skips here as it does at
    # pcb.board_readable: a newer format may have moved the stackup, and a reader
    # that finds none reads KiCad's default FR4 and holds a flex board to rigid
    # rows. (What slipped through, in the review of P4: the guard read a KiCad 10
    # file's stackup and vias on trust, while the reader's own docstring said a
    # newer file is never read on trust.) Rejected: reading a newer file by the
    # words KiCad 10's source writes (the design's first reading; the source says
    # what KiCad writes, not that this reader reads it, as MAX_BOARD_VERSION's own
    # rejection says); skipping only where no gap is found (the gap named would
    # come from the same unchecked reader).
    ceiling = K.reader_ceiling(K.board_problems(tree)[0])
    if ceiling:
        return _skip(gid, f"{ceiling}: the service guard reads the board's stackup and "
                          f"vias with this pack's reader, so the board is not judged on "
                          f"{who}'s limits")
    gaps, built = _service_gaps(tree, service)
    if gaps:
        return _skip(gid, f"the board has {', '.join(gaps)}, which {who}'s "
                          f"{service['name']} service does not list (it builds "
                          f"{', '.join(service['materials'])}; "
                          f"{', '.join(service['via_types'])} vias): its limits are for "
                          f"another process, so the board is not judged on them. Name a "
                          f"service that lists them (pcb_fab_service), or a fab file "
                          f"that does (pcb_fab)")
    layers = K.copper_layer_count(tree)
    oz = ctx.param("pcb_copper_oz_outer")
    oz_note = "" if oz is not None else " [pack default]"
    oz = DEFAULT_OZ_OUTER if oz is None else float(oz)
    table = K.fab_table(fab, layers, oz, str(service["name"]))
    if table is None:
        return _skip(gid, f"{who}'s {service['name']} service publishes no limits for a "
                          f"{layers}-layer board at {oz:g} oz in {fab_path}")
    staged = _stage(ctx, gid, path, board=True, schematic=False)
    if isinstance(staged, Verdict):
        return staged
    stem, stage = staged
    with open(stem + ".kicad_dru", "w", encoding="utf-8") as fh:
        fh.write(K.dru_text(table, "fab"))
    try:
        raised = _zeroed_project(_stem(path) + ".kicad_pro", stem + ".kicad_pro", table)
    except ValueError as exc:
        return Verdict(gate=gid, passed=False, error=str(exc))
    run = _kicad_cli(ctx, gid, stage, ["pcb", "drc", "--severity-all", stem + ".kicad_pcb"],
                     "drc.json")
    if isinstance(run, Verdict):
        return run
    code, doc, ev = run
    if (code == EXIT_CLEAN) != (not _found(doc)):
        return Verdict(gate=gid, passed=False, evidence=ev,
                       error=f"kicad-cli exited {code} but its report lists "
                             f"{len(_found(doc))} item(s): the two disagree")
    pads = K.pad_ids(tree)
    fab_found, guessed = K.fab_findings(doc, table, pads, "fab")
    # A finding of a type the project ignores, raised for this run and not the
    # fab's (silk on silk, when the project ignores silk_overlap), is no finding
    # pcb.drc sees: it runs under the project's own severities. Said apart, never
    # called pcb.drc's. (What slipped through, in review: it was.)
    counted = {id(v) for v in fab_found}
    rest = [v for v in _found(doc) if id(v) not in counted]
    waived = Counter(v.get("type", "?") for v in rest if v.get("type") in raised)
    others = len(rest) - sum(waived.values())
    # Grouped by type with the fab rule's name, never the first descriptions: on a
    # real board those were "Silkscreen overlap" three times over, and a person
    # could not read from the line that nothing past the fab's limits was copper.
    detail = (f"{who} {service['name']} ({table['name']}{oz_note}, read "
              f"{fab.get('read', '?')}): "
              + (f"{len(fab_found)} finding(s) past its limits: "
                 f"{K.fab_kinds(fab_found, table)}" if fab_found
                 else f"inside every limit of {len(table['rules'])} rule(s)"))
    if guessed:
        detail += (f"; {guessed} silk overlap(s) counted although no pad is named in "
                   f"them: {pads[1]} pad(s) on the board get a random id from "
                   f"KiCad, so silk on silk cannot be told from silk on a pad")
    # The project's own custom rules are left out of this run, and one of them
    # may waive (severity ignore) what pcb.drc then never reports: with such a
    # file, the rest is said to be not the fab's, never pcb.drc's. (What slipped
    # through, in review: it was called pcb.drc's.)
    if others and os.path.isfile(_stem(path) + ".kicad_dru"):
        detail += (f"; {others} other finding(s) in the same run are not the fab's: "
                   f"pcb.drc judges them under the project's own rules and severities")
    elif others:
        detail += f"; {others} other finding(s) in the same run are pcb.drc's"
    if waived:
        kinds = ", ".join(f"{n}x {k}" for k, n in waived.most_common())
        detail += (f"; {sum(waived.values())} other finding(s) of a type the project "
                   f"ignores ({kinds}), raised for this run only: pcb.drc does not "
                   f"see them")
    # What the service was chosen against, said last: a pass on a board read as
    # KiCad's default stackup says so, never as if the board had stated FR4.
    detail += f"; the board: {', '.join(built)}"
    return Verdict(gate=gid, passed=not fab_found, measured=len(fab_found), limit=0,
                   comparator="<=", units="findings", detail=detail, evidence=ev)


# --------------------------------------------------------------------------- #
# a circuit simulation
# --------------------------------------------------------------------------- #
#: ``.include`` and ``.lib`` lines, whose path the staged deck must name
#: absolutely: the deck runs from the stage, and a relative include would then
#: read nothing, or a file of the same name the stage happens to hold.
_INCLUDE = re.compile(r"^(\s*\.(?:include|inc|lib)\s+)(\"[^\"]+\"|'[^']+'|\S+)(.*)$",
                      re.IGNORECASE)

#: One minute for one batch run: the baseline deck solves in 0.01 s. A timeout is
#: a skip (nothing was shown), never a fail.
DEFAULT_SPICE_TIMEOUT_S = 60.0


def _read_text(path: str) -> str:
    with open(path, encoding="utf-8", errors="replace") as fh:
        return fh.read()


def _staged_deck(text: str, deck_dir: str) -> str:
    out = []
    for line in text.splitlines():
        m = _INCLUDE.match(line)
        if m:
            target = m.group(2).strip("\"'")
            if not os.path.isabs(target):
                target = os.path.join(deck_dir, target)
            line = f'{m.group(1)}"{target}"{m.group(3)}'
        out.append(line)
    return "\n".join(out) + "\n"


#: Every option name ngspice-42 knows on a ``.options`` card. ngspice takes any
#: other name without a word: ``.options tmep=127`` runs at the default 27 C and
#: exits 0, and the baseline deck passed at 4.8325 V with it. And it reads EVERY
#: card that begins ``.opt`` as ``.options`` (``.optoins``, ``.optxyz`` and
#: ``.opts temp=127`` each moved a deck with tc1 on its track resistor from
#: 4.8325 V to 4.790 V: measured), so the guard reads them all.
#: Where the names come from: the simulator's own table (``OPTtbl``,
#: ``cktsopt.c``) read off the ngspice-42 binary's string table, the names a
#: linker merged into a longer string (``trtol`` in ``xtrtol``, ``gmin`` in
#: ``dyngmin``) found by suffix; and the front-end variables of the manual's
#: chapter "Simulator Variables (.options)" (noacct, noinit, norefvalue, interp,
#: autostop, savecurrents, seed, seedinfo, warn, maxwarns, scale, node),
#: recalled, not read off a copy of the manual here. A valid name left off reads
#: as an error that names it: loud, never a pass. Rejected: ngspice's own
#: ``option`` listing (it leaves out valid front-end options: measured); and a
#: list of the dot cards ngspice knows (an unknown card, ``.tarn`` or ``.icc``,
#: already prints ``Error:`` and exits 1: measured).
NGSPICE_OPTIONS = frozenset("""
    noopiter gmin gshunt reltol abstol vntol trtol chgtol pivtol pivrel tnom temp
    itl1 itl2 itl3 itl4 itl5 itl6 srcsteps gminsteps gminfactor acct list nomod nopage
    opts oldlimit numdgt cptime limtim limpts lvlcod lvltim method maxord indverbosity
    xmu defm defl defw minbreak defad defas bypass trytocompact badmos3 keepopinfo
    copynodesets nodedamping absdv reldv noopac epsmin sparse klu klu_memgrow_factor
    rshunt cshunt maxopalter maxevtiter noopalter ramptime convlimit convstep
    convabsstep autopartial
    noacct noinit norefvalue interp autostop savecurrents seed seedinfo warn maxwarns
    scale node
""".split())

#: SPICE's scale suffixes, as ngspice reads a number: ``meg`` before ``m``
#: (milli), case ignored, and any letters after the suffix are units it drops
#: (``4ms`` is 4e-3, ``4s`` is 4). Review R-15 named the trap: ``1M`` is a
#: milli, not a mega.
_SPICE_SCALE = {"t": 1e12, "g": 1e9, "meg": 1e6, "k": 1e3, "mil": 25.4e-6, "m": 1e-3,
                "u": 1e-6, "n": 1e-9, "p": 1e-12, "f": 1e-15}
_SPICE_NUMBER = re.compile(r"^([-+]?(?:\d+\.?\d*|\.\d+)(?:e[-+]?\d+)?)(meg|mil|[tgkmunpf])?"
                           r"[a-z]*$", re.IGNORECASE)


def spice_number(text: str) -> float | None:
    """A literal SPICE number as ngspice reads it, or None (a ``{param}``, an
    expression, a word): ``0.5m`` 5e-4, ``4MEG`` 4e6, ``4ms`` 4e-3."""
    m = _SPICE_NUMBER.match(text.strip())
    if m is None:
        return None
    scale = _SPICE_SCALE[m.group(2).lower()] if m.group(2) else 1.0
    return float(m.group(1)) * scale


def _cards(text: str) -> list[str]:
    """The deck's cards, each with its ``+`` continuation lines joined on, and
    comments (``*`` lines, ``;`` and ``$ `` tails) dropped."""
    cards: list[str] = []
    for line in text.splitlines():
        line = re.split(r";|\s\$\s", line, maxsplit=1)[0].rstrip()
        stripped = line.strip()
        if not stripped or stripped.startswith("*"):
            continue
        if stripped.startswith("+") and cards:
            cards[-1] += " " + stripped[1:]
        else:
            cards.append(stripped)
    return cards


def _option_names(card: str) -> list[str]:
    """The option names on one ``.opt*`` card: each word before an ``=``, or a
    bare flag."""
    body = re.sub(r"\s*=\s*", "=", card.split(None, 1)[1] if " " in card else "")
    return [w.split("=", 1)[0].lower() for w in body.split() if w.split("=", 1)[0]]


def _deck_guard(ctx: GateContext, deck: str, text: str, measure: str) -> str | None:
    """Why the deck cannot be read as written, or None. Run on the deck's text
    before ngspice is launched.

    (a) Every option name on every ``.opt*`` card, and on every ``option`` or
    ``options`` command inside a ``.control`` block (the same table, the same
    silence: ``option tmep=127`` ran at 27 C, exit 0), in the deck and in each file
    it ``.include``s (read through ``ctx.load_file``, so each is keyed), is one
    ngspice knows (:data:`NGSPICE_OPTIONS`). ``.lib`` files hold models and are
    not read. (b) One ``.meas`` card defines ``measure``, in the deck and its
    includes together: ngspice prints a value per card, the gate reads the first,
    and a second card with the same name would be the one checked here (in review:
    a window past the run, put before a good one, read 0 V past this guard). (c)
    That ``.meas tran`` is in the deck itself, and its literal FROM, TO and AT lie
    inside the run: 0 <= FROM < TO <= tstop and 0 <= AT <= tstop, tstop the deck's
    literal ``.tran`` stop time.
    ngspice measures a window past the run without a word (MIN over 5-6 ms of a
    4 ms run gave 0.000000e+00, exit 0; a window from 2 ms to 1 ms gave 0; ``to=4``,
    in seconds, gave the right value over a cut window), and a gate under
    ``pcb_spice_max`` would pass at 0 V. ``.meas ac`` and ``.meas dc`` windows
    are not checked (PACK.md)."""
    def defines(card: str) -> bool:
        return (card.split(None, 1)[0].lower() in (".meas", ".measure")
                and len(card.split()) > 2 and card.split()[2].lower() == measure.lower())

    todo, seen = [(deck, text)], {os.path.abspath(deck)}
    defined_in: list[str] = []
    while todo:
        path, body = todo.pop(0)
        base = os.path.dirname(os.path.abspath(path))
        control = False
        for card in _cards(body):
            word = card.split(None, 1)[0].lower()
            if defines(card):
                defined_in.append(os.path.basename(path))
            if word in (".control", ".endc"):
                control = word == ".control"
            elif word.startswith(".opt") or (control and word in ("option", "options")):
                unknown = [n for n in _option_names(card) if n not in NGSPICE_OPTIONS]
                if unknown:
                    return (f"ngspice ignores an option name it does not know, without a "
                            f"word: {unknown[0]} (in {os.path.basename(path)}: "
                            f"{card[:80]})")
            elif word in (".include", ".inc"):
                m = _INCLUDE.match(card)
                target = m.group(2).strip("\"'") if m else ""
                target = target if os.path.isabs(target) else os.path.join(base, target)
                if os.path.abspath(target) in seen:
                    continue
                seen.add(os.path.abspath(target))
                try:
                    todo.append((target, ctx.load_file(target, loader=_read_text)))
                except FileNotFoundError:
                    return f"the deck includes {target}, and there is no such file"
    if len(defined_in) > 1:
        return (f"{len(defined_in)} .meas cards define {measure!r} (in "
                f"{', '.join(dict.fromkeys(defined_in))}): ngspice prints a value for each, "
                f"and the gate reads one; give each measure its own name")
    cards = _cards(text)
    defining = [c for c in cards if defines(c)]
    if not defining:
        return (f"cannot show the window was simulated: the .meas that defines "
                f"{measure!r} is not in {os.path.basename(deck)} itself")
    card = re.sub(r"\s*=\s*", "=", defining[0])
    if card.split()[1].lower() != "tran":
        return None
    trans = [c.split() for c in cards if c.split(None, 1)[0].lower() == ".tran"]
    tstop = spice_number(trans[-1][2]) if trans and len(trans[-1]) > 2 else None
    if tstop is None:
        return ("cannot show the window was simulated: the deck has no .tran with a "
                "literal stop time")
    bounds = {}
    for word in card.split()[3:]:
        key, _, val = word.partition("=")
        if key.lower() in ("from", "to", "at") and _:
            num = spice_number(val)
            if num is None:
                return (f"cannot show the window was simulated: {measure} {key.upper()}="
                        f"{val} is not a literal number")
            bounds[key.lower()] = num
    lo, hi = bounds.get("from", 0.0), bounds.get("to", tstop)
    if not 0.0 <= lo < hi <= tstop:
        return (f"{measure}'s window FROM={lo:g} TO={hi:g} s is not inside the "
                f"{tstop:g} s run (.tran): ngspice measures outside it without a word")
    if "at" in bounds and not 0.0 <= bounds["at"] <= tstop:
        return (f"{measure}'s AT={bounds['at']:g} s is outside the {tstop:g} s run "
                f"(.tran)")
    return None


@gate(
    id="pcb.spice",
    basis="established",
    title="A measurement ngspice makes on the project's circuit deck stays inside its limits",
    claims=["pcb", "circuit-simulation", "power-integrity"],
    tier=Tier.BUILD,
    settles="simulated circuit measurement",
    requires_tools=["ngspice"],
    negative_control=NegativeControl(
        fixture="selftest/bad_boards.py:long_thin_cable",
        note="the baseline VBUS deck with the cable's out-and-back resistance raised "
             "from 0.25 to 0.85 ohm (2 m of 28 AWG): the header's VBUS dips below "
             "USB's 4.75 V under the 0.5 A step",
    ),
)
def spice(ctx: GateContext) -> Verdict:
    """``ngspice -b`` on the project's deck; the verdict is the ``.meas`` result
    ``pcb_spice_measure`` names, against ``pcb_spice_min`` and/or
    ``pcb_spice_max`` in ``pcb_spice_units``.

    ngspice solves the deck; it does not know whether the deck is the circuit.
    The deck is the project's model, written by someone, and a claim that rests
    on it rests on that (PACK.md, "cannot settle"). So the gate refuses to read a
    run that dropped a line: ngspice warns that it ignored an instance it could
    not parse and goes on, solving a different circuit, and that is an error
    here, as is any ``Error:`` line. A measurement ngspice could not make
    (``... failed!``) is an error, never a fail: no value was shown.

    Three refusals come before ngspice runs, because ngspice is silent on each
    (:func:`_deck_guard`): an option name it does not know, on a ``.opt*`` card or
    an ``option`` command (``tmep=127`` runs at 27 C, exit 0), a measure two
    ``.meas`` cards define, and a ``.meas tran`` window outside the run (MIN past
    the end reads 0 V, exit 0). Each is an error, never a fail, as is a command
    ngspice says it does not have.
    """
    gid = "pcb.spice"
    rel = ctx.param("pcb_spice_deck")
    if not rel:
        return _skip(gid, "no pcb_spice_deck: the SPICE deck (.cir) that models the "
                          "circuit is the project's to write; this pack will not invent one")
    deck = str(rel) if os.path.isabs(str(rel)) else os.path.join(ctx.root, str(rel))
    measure = str(ctx.param("pcb_spice_measure") or "").strip()
    lo, hi = ctx.param("pcb_spice_min"), ctx.param("pcb_spice_max")
    if not measure:
        return _skip(gid, "no pcb_spice_measure: name the deck's .meas result to judge")
    if lo is None and hi is None:
        return _skip(gid, "no pcb_spice_min or pcb_spice_max: the limit is the "
                          "project's decision, not this pack's")
    units = str(ctx.param("pcb_spice_units", "") or "")
    if not os.path.isfile(deck):
        return Verdict(gate=gid, passed=False, detail=f"no deck at {deck}")
    text = ctx.load_file(deck, loader=_read_text)
    refused = _deck_guard(ctx, deck, text, measure)
    if refused:
        return Verdict(gate=gid, passed=False, error=refused)
    marker = ctx.out_path("pcb-kicad", "pcb_spice", ".stage")
    stage = os.path.dirname(marker)
    staged = os.path.join(stage, os.path.basename(deck))
    with open(staged, "w", encoding="utf-8") as fh:
        fh.write(_staged_deck(text, os.path.dirname(os.path.abspath(deck))))
    timeout = float(ctx.param("pcb_spice_timeout_s", DEFAULT_SPICE_TIMEOUT_S) or
                    DEFAULT_SPICE_TIMEOUT_S)
    argv = ["ngspice", "-b", staged]
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                              stdin=subprocess.DEVNULL, cwd=stage)
    except FileNotFoundError as exc:
        return _skip(gid, f"could not launch ngspice: {exc}")
    except subprocess.TimeoutExpired:
        return _skip(gid, f"ngspice exceeded {timeout:g} s (pcb_spice_timeout_s) and was "
                          f"killed: nothing was shown")
    log = os.path.join(stage, "ngspice.log")
    output = proc.stdout + "\n" + proc.stderr
    with open(log, "w", encoding="utf-8") as fh:
        fh.write(" ".join(argv) + f"\nexit {proc.returncode}\n{output}")
    ev = [staged, log]
    dropped = [ln.strip() for ln in output.splitlines()
               if ln.lower().startswith("warning") and "ignored" in ln.lower()]
    # ``no such command``: a misspelled command in a .control block (``opt``,
    # ``optoins``) is printed and skipped, exit 0 (measured on ngspice-42).
    errors = [ln.strip() for ln in output.splitlines()
              if ln.strip().lower().startswith("error")
              or "no such command available" in ln.lower()]
    if proc.returncode != 0 or dropped or errors:
        why = (dropped or errors or [f"exit {proc.returncode}"])[0]
        return Verdict(gate=gid, passed=False, evidence=ev,
                       error=f"ngspice did not solve the deck as written: {why[:200]}")
    found = re.search(rf"^\s*{re.escape(measure.lower())}\s*=\s*([-+0-9.eE]+)",
                      proc.stdout, re.MULTILINE)
    if found is None:
        failed = [ln.strip() for ln in output.splitlines() if "failed" in ln.lower()]
        return Verdict(gate=gid, passed=False, evidence=ev,
                       error=f"no .meas result {measure!r} in ngspice's output"
                             + (f": {failed[0][:160]}" if failed else ""))
    value = float(found.group(1))
    lo = None if lo is None else float(lo)
    hi = None if hi is None else float(hi)
    passed = (lo is None or value >= lo) and (hi is None or value <= hi)
    # The side the verdict reports: the one crossed, else the nearer, so the
    # margin a reader sees is the one that binds.
    if hi is None or (lo is not None and abs(value - lo) <= abs(hi - value)):
        limit, comparator = lo, ">="
    else:
        limit, comparator = hi, "<="
    band = " and ".join(s for s in ((f">= {lo:g}" if lo is not None else ""),
                                    (f"<= {hi:g}" if hi is not None else "")) if s)
    detail = (f"{measure} = {value:.6g} {units}".rstrip() + f" (ngspice, "
              f"{os.path.basename(deck)}), limit {band} {units}".rstrip())
    return Verdict(gate=gid, passed=passed, measured=value, limit=limit,
                   comparator=comparator, units=units, detail=detail, evidence=ev)


# --------------------------------------------------------------------------- #
# the board in its enclosure
# --------------------------------------------------------------------------- #
FIT_HELPER = os.path.join(_HERE, "_fit.py")

#: A shared volume below this is OpenCascade's boolean noise on two faces that
#: touch, not a collision: 0.001 mm^3 is a 0.1 mm cube. The baseline's raised
#: headers share 0.93 mm^3 with the lid, three orders above it.
INTERFERENCE_TOL_MM3 = 1e-3

#: Two minutes for the export and the geometry together, each: the baseline takes
#: about 1 s to export and 3 s to measure.
DEFAULT_FIT_TIMEOUT_S = 120.0

PLACEMENT_KEYS = ("x", "y", "z", "rot_z_deg")

#: A footprint attribute that makes it a placed part with a body: what KiCad's
#: pick-and-place file lists. Mounting holes, fiducials and test pads carry
#: neither, and the fit does not ask for their bodies.
_PART_ATTRS = ("smd", "through_hole")


def _parts_and_models(tree: list) -> tuple[list[str], dict[str, list[str]]]:
    """``(refs of placed parts, {ref: [visible 3D model paths]})``: the parts
    the fit must see, and what the board says each one's body is. A part marked
    do-not-populate or board-only is not placed, and not asked for."""
    parts, models = [], {}
    for fp in K.children(tree, "footprint"):
        ref = K.footprint_ref(fp)[0]
        attr = [str(a) for a in (K.child(fp, "attr") or [])[1:]]
        if not any(a in _PART_ATTRS for a in attr) or "dnp" in attr or "board_only" in attr:
            continue
        parts.append(ref)
        shown = []
        for model in K.children(fp, "model"):
            hide = K.child(model, "hide")
            if (hide is not None and (len(hide) == 1 or str(hide[1]) == "yes")) or "hide" in model:
                continue
            shown.append(str(model[1]))
        models[ref] = shown
    return parts, models


@gate(
    id="pcb.enclosure_fit",
    basis="established",
    title="The populated board fits its enclosure: nothing collides, every part keeps its clearance",
    claims=["pcb", "enclosure-fit", "packaging", "interference"],
    tier=Tier.BUILD,
    settles="board fit in its enclosure",
    requires_tools=["kicad-cli"],
    requires_python=["cadquery"],
    negative_control=NegativeControl(
        fixture="selftest/bad_boards.py:raised_board",
        note="the baseline board placed 3 mm higher in the baseline enclosure, on "
             "taller standoffs: both headers' pin tips run 0.64 mm into the lid",
    ),
)
def enclosure_fit(ctx: GateContext) -> Verdict:
    """The board as KiCad exports it, every part's 3D model in place, against
    the enclosure's solid: OpenCascade's shared volume (nothing may collide, the
    board body included) and its distance from each part (each must keep
    ``pcb_enclosure_clearance_mm``; the board body may touch, as it does on its
    standoffs).

    The board's STEP comes from ``kicad-cli pcb export step --subst-models
    --no-dnp --user-origin 0x0mm``: the board file's (0, 0) at the origin, x
    right, y up (KiCad's y negated), z up from the board's bottom face.
    ``pcb_board_placement`` (``{"x", "y", "z", "rot_z_deg"}``, mm and degrees)
    turns it about z, then moves it, into the enclosure's frame.

    **A part the fit cannot see is a skip, never a pass.** kicad-cli leaves out,
    silently and with exit 0, every part whose 3D model it cannot find (KiCad's
    3D library is a separate install on most hosts), and a fit run without the
    part reads clear. So every placed part (SMD or through-hole, not DNP) must
    name a model and arrive in the export under its reference, or the gate skips
    naming it. A part that truly has no body above the board (an antenna drawn
    in copper) goes in ``pcb_enclosure_bodiless``, by reference, a recorded
    statement.
    """
    gid = "pcb.enclosure_fit"
    path = _board_path(ctx)
    if path is None:
        return _skip(gid, _NO_BOARD)
    rel = ctx.param("pcb_enclosure_step")
    if not rel:
        return _skip(gid, "no pcb_enclosure_step: the enclosure's solid, as a STEP file, "
                          "is the project's; this pack will not invent one")
    enclosure = str(rel) if os.path.isabs(str(rel)) else os.path.join(ctx.root, str(rel))
    clearance = ctx.param("pcb_enclosure_clearance_mm")
    if clearance is None:
        return _skip(gid, "no pcb_enclosure_clearance_mm: how close a part may come to "
                          "the enclosure is a design decision this pack will not invent")
    clearance = float(clearance)
    place = ctx.param("pcb_board_placement")
    if not isinstance(place, dict):
        return _skip(gid, "no pcb_board_placement: where the board sits in the enclosure "
                          "({\"x\", \"y\", \"z\", \"rot_z_deg\"}) is the project's to say")
    unknown = sorted(set(place) - set(PLACEMENT_KEYS))
    if unknown:
        return Verdict(gate=gid, passed=False,
                       error=f"pcb_board_placement has unknown key(s) {unknown}: it takes "
                             f"{list(PLACEMENT_KEYS)}")
    place = {k: float(place.get(k, 0.0)) for k in PLACEMENT_KEYS}
    bodiless = {str(r) for r in (ctx.param("pcb_enclosure_bodiless") or [])}
    if not os.path.isfile(path):
        return Verdict(gate=gid, passed=False, detail=f"no board at {path}")
    if not os.path.isfile(enclosure):
        return Verdict(gate=gid, passed=False, detail=f"no enclosure at {enclosure}")
    ctx.load_file(enclosure)
    tree = ctx.load_file(path, loader=K.load_tree)
    parts, models = _parts_and_models(tree)
    parts = [r for r in parts if r not in bodiless]
    unmodelled = sorted(r for r in parts if not models.get(r))
    if unmodelled:
        return _skip(gid, f"{len(unmodelled)} placed part(s) name no 3D model: "
                          f"{', '.join(unmodelled[:8])}. The fit cannot see a body the "
                          f"board does not describe: give each a model, or list a part "
                          f"with no body in pcb_enclosure_bodiless")

    staged = _stage(ctx, gid, path, board=True, schematic=False)
    if isinstance(staged, Verdict):
        return staged
    stem, stage = staged
    src_dir = os.path.dirname(os.path.abspath(path))
    for ref in parts:
        for model in models[ref]:
            if model.startswith("${KIPRJMOD}/"):
                rel_model = model[len("${KIPRJMOD}/"):]
                src = os.path.join(src_dir, rel_model)
                if os.path.isfile(src):
                    dst = os.path.join(stage, rel_model)
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    shutil.copyfile(src, dst)
    step = os.path.join(stage, "board.step")
    timeout = float(ctx.param("pcb_fit_timeout_s", DEFAULT_FIT_TIMEOUT_S) or
                    DEFAULT_FIT_TIMEOUT_S)
    argv = ["kicad-cli", "pcb", "export", "step", "--subst-models", "--no-dnp",
            "--user-origin", "0x0mm", "-f", "-o", step, stem + ".kicad_pcb"]
    log = os.path.join(stage, "fit.log")
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                              stdin=subprocess.DEVNULL, cwd=stage)
    except FileNotFoundError as exc:
        return _skip(gid, f"could not launch kicad-cli: {exc}")
    except subprocess.TimeoutExpired:
        return _skip(gid, f"kicad-cli pcb export step exceeded {timeout:g} s and was killed")
    with open(log, "w", encoding="utf-8") as fh:
        fh.write(" ".join(argv) + f"\nexit {proc.returncode}\n{proc.stdout}\n{proc.stderr}\n")
    if proc.returncode != 0 or not os.path.isfile(step):
        return Verdict(gate=gid, passed=False, evidence=[log],
                       error=f"kicad-cli pcb export step exited {proc.returncode} without "
                             f"a STEP: {(proc.stderr or proc.stdout).strip()[-200:]}")
    request = os.path.join(stage, "fit_request.json")
    with open(request, "w", encoding="utf-8") as fh:
        json.dump({"board_step": step, "enclosure_step": os.path.abspath(enclosure),
                   "placement": place, "refs": parts}, fh, indent=2)
    try:
        geo = subprocess.run([sys.executable, FIT_HELPER, request], capture_output=True,
                             text=True, timeout=timeout, stdin=subprocess.DEVNULL, cwd=stage)
    except subprocess.TimeoutExpired:
        return _skip(gid, f"the fit geometry exceeded {timeout:g} s (pcb_fit_timeout_s) "
                          f"and was killed: nothing was shown")
    with open(log, "a", encoding="utf-8") as fh:
        fh.write(f"\n_fit.py exit {geo.returncode}\n{geo.stdout}\n{geo.stderr}\n")
    lines = [ln for ln in geo.stdout.splitlines() if ln.startswith("{")]
    if geo.returncode != 0 or not lines:
        tail = (geo.stderr or geo.stdout).strip().splitlines()[-1:] or ["no output"]
        return Verdict(gate=gid, passed=False, evidence=[log],
                       error=f"the fit geometry failed (exit {geo.returncode}): {tail[0][:200]}")
    result = json.loads(lines[-1])
    report = os.path.join(stage, "fit.json")
    with open(report, "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=2, sort_keys=True)
    ev = [report, log, step]
    measured_parts = result["parts"]
    missing = sorted(r for r in parts if r not in measured_parts)
    if missing:
        return _skip(gid, f"kicad-cli exported no body for {', '.join(missing[:8])}: "
                          f"their 3D models were not found (it leaves a missing model out "
                          f"with exit 0). Install KiCad's 3D library, or keep the models "
                          f"beside the board as ${{KIPRJMOD}}/...")
    hits = sorted(((v["interference_mm3"], n) for n, v in measured_parts.items()
                   if v["interference_mm3"] > INTERFERENCE_TOL_MM3), reverse=True)
    judged = {n: v for n, v in measured_parts.items() if n != "board"} or measured_parts
    nearest = min(judged, key=lambda n: judged[n]["clearance_mm"])
    gap = round(judged[nearest]["clearance_mm"], 3)
    passed = not hits and gap >= clearance
    if hits:
        detail = (f"{len(hits)} shape(s) collide with the enclosure: "
                  + ", ".join(f"{n} shares {v:.3g} mm^3" for v, n in hits[:4]))
    else:
        detail = (f"nothing collides; nearest part {nearest} is {gap:g} mm from the "
                  f"enclosure vs {clearance:g} mm required")
    detail += (f" ({len(parts)} part(s) placed at x {place['x']:g}, y {place['y']:g}, "
               f"z {place['z']:g} mm, {place['rot_z_deg']:g} deg)")
    return Verdict(gate=gid, passed=passed, measured=gap, limit=clearance, comparator=">=",
                   units="mm", detail=detail, evidence=ev)
