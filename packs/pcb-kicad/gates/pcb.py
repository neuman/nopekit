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
    pcb.enclosure_fit    tier 1  the populated boards' 3D models against the enclosure and each other
    pcb.antenna_keepout  tier 1  no track, via, pad or pour on any layer in a radio module's antenna keep-out

    (gates/power.py holds the four battery rules: pcb.charge_temperature,
    pcb.charge_limits, pcb.fault_current, pcb.discharge_cutoff.)

**Where each verdict comes from.** The tier-1 gates are a tool's own answer:
``kicad-cli pcb drc``'s or ``sch erc``'s exit code and its report's count (basis
established; ``pcb.fab_rules`` sourced, for the fab's numbers it hands KiCad),
ngspice's ``.meas`` value, OpenCascade's shared volume and distance, and for
``pcb.antenna_keepout`` KiCad's DRC on the board as stored and after KiCad's own
zone refill (sourced: the keep-out is the module's, its every-layer reach the
vendor's guidance), each on a copy staged under the gate's out directory. They
read inside a subprocess the trace cannot see into, so they are opaque and re-run
on every check where their tool exists, as openmodelica's ``omc`` gates do. The tier-0 gates read
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
  cannot find, with exit 0: a part the fit cannot see is a skip here, unless the
  project states its height. Not this gate: a part that stood proud of the face,
  through the opening cut for it, was found by a person on sight. The fit
  measures shared volume and clearance, so a part through an opening wide enough
  to keep the claim's clearance reads clear however far it reaches, and a
  tighter opening fails on clearance, which still says nothing about how far it
  reaches. A contact declared for the part against that opening excuses only
  the closeness, so it reads clear too (lenses.md, "A part taller than its
  cavity").

- ``pcb.antenna_keepout``: a radio module's antenna keep-out was held by a
  script written for that one board. ``pcb.drc`` holds a footprint's keep-out
  only on the layers it names (KiCad 9.0.9's stock ESP32-S3-WROOM-1: F.Cu,
  In1.Cu and B.Cu), and judges a pour by its stored fill, so a ground pour on
  In2.Cu under the antenna, or one moved there and not refilled, reads clean.

**Missing inputs skip; they never default.** Which board, which nets carry how much
current, how hot a trace may run, which fab, which deck and limit, which enclosure
and where the board sits in it are the project's to say.
"""
from __future__ import annotations

import dataclasses
import hashlib
import itertools
import json
import math
import os
import re
import shutil
import subprocess
import sys
import uuid
from collections import Counter

from nopekit.gates import gate, GateContext
from nopekit.modelio import load_path as _load_path
from nopekit.models import NegativeControl, Tier, Verdict

_HERE = os.path.dirname(os.path.abspath(__file__))
K = _load_path(os.path.join(_HERE, "_kicad.py"))
#: The records' strict readers (``keys``, ``source``, ``RecordError``), shared with
#: the power rules; it registers no gate.
P = _load_path(os.path.join(_HERE, "_power.py"))

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

#: The claim tag ``pcb.enclosure_fit`` reads its limit from (``ctx.acceptance``):
#: the least gap a part keeps from the enclosure, in mm, ``>=``. A limit lives in
#: the claim, never in a pack parameter (PACK_FORMAT, "A claim's limit").
#: *Rejected:* ``pcb_enclosure_clearance_mm`` (the limit as a parameter, beside the
#: claim that states it: the two could disagree, and a claim's limit moved
#: nothing); the claim's id (a pack cannot know a project's ids).
FIT_TAG = "enclosure-fit"

#: Where the limit was until general-packs P7. A project that still states it is
#: refused, and the error names the move: dropped silently, the project would
#: think its number was used.
RETIRED_CLEARANCE_KEY = "pcb_enclosure_clearance_mm"

#: Where a board sits in the enclosure's frame: turned ``rot_x_deg`` about x,
#: then ``rot_y_deg`` about y, then ``rot_z_deg`` about z (each about the
#: enclosure's own axes, through the board's origin), then moved by x, y, z mm.
#: One 3x3 rotation, so a board may sit in any orientation: face down is
#: ``rot_x_deg`` 180, on its edge is 90. A rotation not stated is 0; x, y and z
#: must be stated. *Rejected:* ``rot_z_deg`` alone (the earlier rule: a board
#: mounted face down, or on its edge, could not be stated, and the reference
#: told the project to export its enclosure upside down instead); a ``flip``
#: flag (a flip is one rotation of many, and a board on its edge is not one);
#: a matrix (nine numbers a person can get wrong in ways three angles cannot).
PLACEMENT_KEYS = ("x", "y", "z", "rot_x_deg", "rot_y_deg", "rot_z_deg")
PLACEMENT_REQUIRED = ("x", "y", "z")

#: The keys of one entry of ``pcb_enclosure_fits``, and the singular key each
#: one stands for when a project fits one board. ``board``, ``enclosure_step``
#: and ``placement`` are required in an entry; the singular form takes the board
#: from ``pcb_board``.
FIT_KEYS = {"board": "pcb_board", "enclosure_step": "pcb_enclosure_step",
            "placement": "pcb_board_placement", "bodiless": "pcb_enclosure_bodiless",
            "contacts": "pcb_enclosure_contacts", "heights": "pcb_part_heights_mm"}
FIT_REQUIRED = ("board", "enclosure_step", "placement")

#: A fit's id: it names the board in the verdict (``<fit>:<ref>``), in a contact
#: against another board, and in a directory under the gate's out dir, so it is
#: held to letters, digits, ``-`` and ``_``.
_FIT_ID = re.compile(r"^[A-Za-z0-9_-]+$")

#: The name the fit gives a board's body: every shape of the export that is not
#: a placed part. The body may touch the enclosure (it sits on standoffs), but
#: it may not cut into it, and against another board it keeps the limit.
BODY = "board"

#: A footprint attribute that makes it a placed part with a body: what KiCad's
#: pick-and-place file lists. Mounting holes, fiducials and test pads carry
#: neither, and the fit does not ask for their bodies.
_PART_ATTRS = ("smd", "through_hole")

#: The courtyard layer of a footprint on each side. A declared height is the
#: courtyard's box on the footprint's own side.
_COURTYARD = {"F.Cu": "F.CrtYd", "B.Cu": "B.CrtYd"}
_COURTYARD_ITEMS = ("fp_line", "fp_rect", "fp_poly", "fp_circle", "fp_arc")

#: The directory, inside the stage, that holds the box models of the parts fitted
#: at a declared height. The staged board names each box as its part's model, so
#: kicad-cli places it as it places any model.
HEIGHT_DIR = "nopekit-heights"

#: How far past its part's own box (the part's bounding box as placed, in the
#: enclosure's frame) a contact region may take out enclosure material, in the
#: claim's limits. Material more than one limit past the box cannot come within
#: the limit of the part, so a region needs no reach there, and a region that
#: reaches there excuses the part against whatever is there. In the review of
#: P7, a region 0.1 mm short of the enclosure's whole box excused each header
#: against the walls and the floor as well as the lid it named: the review's run
#: read pass at 2.465 mm, against the 0.1 mm skin the region left. The second
#: limit is room to draw in: the material just past a region drawn to exactly
#: one limit sits at the limit, so a region would have to end on that line to
#: 0.001 mm (measured on the baseline's lid over J1 at z 5.8: a region 0.035 mm
#: short of it left the lid 0.965 mm from the pins, against 1.0). Two limits
#: give a window as wide as the limit. *Rejected:* the region
#: holding the enclosure's whole box (the P7 guard: a region 0.1 mm short of it
#: passed); a share of the enclosure's volume (a small region near the part can
#: still cover two features, and no share is a number anyone could defend); a
#: fixed reach in mm (a wall's thickness is the project's, and a fixed number
#: means a different thing beside a 0.2 mm limit and a 5 mm one); clipping the
#: region quietly to the reach (the record stays as wide as it was drawn, and a
#: part that moves toward a wall inside it is excused there).
REGION_REACH_LIMITS = 2.0

#: The tolerance on that reach, mm. The refusal prints the reach to 0.001 mm, so a
#: region copied from it can stand 0.0005 mm proud; OpenCascade's optimal box is
#: exact to its own tolerance, 1e-7 mm. *Rejected:* 0 (a region copied from the
#: refusal could be refused on the printed digit).
REGION_REACH_TOL_MM = 1e-3


class _FitError(ValueError):
    """A fit record the gate cannot read: the gate errors, naming it."""


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


def _arc_points(start, mid, end) -> list[tuple[float, float]]:
    """The points that bound an arc drawn through ``start``, ``mid`` and
    ``end``: the three, and each of its circle's four axis extremes that the arc
    passes. Three collinear points are a line, bounded by the three."""
    (ax, ay), (bx, by), (cx, cy) = start, mid, end
    d = 2.0 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    pts = [start, mid, end]
    if abs(d) < 1e-12:
        return pts
    a2, b2, c2 = ax * ax + ay * ay, bx * bx + by * by, cx * cx + cy * cy
    ux = (a2 * (by - cy) + b2 * (cy - ay) + c2 * (ay - by)) / d
    uy = (a2 * (cx - bx) + b2 * (ax - cx) + c2 * (bx - ax)) / d
    r = math.hypot(ax - ux, ay - uy)
    turn = 2.0 * math.pi

    def angle(p) -> float:
        return math.atan2(p[1] - uy, p[0] - ux)

    def between(a: float, b: float, c: float) -> bool:
        """Whether b lies on the counter-clockwise sweep from a to c."""
        return (b - a) % turn <= (c - a) % turn

    a0, am, a1 = angle(start), angle(mid), angle(end)
    ccw = between(a0, am, a1)
    for k in range(4):
        t = k * math.pi / 2.0
        if between(a0, t, a1) if ccw else between(a1, t, a0):
            pts.append((ux + r * math.cos(t), uy + r * math.sin(t)))
    return pts


def _courtyard_box(fp: list) -> tuple[float, float, float, float] | None:
    """``(x0, y0, x1, y1)``, mm, the box around the footprint's courtyard on its
    own side, in its library frame (KiCad's footprint frame: x right, y down;
    a footprint flipped to ``B.Cu`` has its stored y negated back, as
    ``_kicad.footprint_land`` reads its pads), or None when it has no courtyard.

    The box is in the footprint's own frame, not the board's: kicad-cli turns
    and flips it with the footprint, so a part at 45 degrees gets a box at 45
    degrees, not the larger box around it. A circle adds its centre plus and
    minus its radius; an arc adds the extremes it passes."""
    side = str(K.value(fp, "layer", default=""))
    layer = _COURTYARD.get(side)
    back = side == "B.Cu"
    pts: list[tuple[float, float]] = []

    def xy(node) -> tuple[float, float]:
        return float(node[1]), float(node[2])

    for item in fp:
        if not isinstance(item, list) or K.head(item) not in _COURTYARD_ITEMS:
            continue
        if layer is None or str(K.value(item, "layer", default="")) != layer:
            continue
        kind = K.head(item)
        if kind in ("fp_line", "fp_rect"):
            pts += [xy(K.child(item, "start")), xy(K.child(item, "end"))]
        elif kind == "fp_circle":
            (cx, cy), (ex, ey) = xy(K.child(item, "center")), xy(K.child(item, "end"))
            r = math.hypot(ex - cx, ey - cy)
            pts += [(cx - r, cy - r), (cx + r, cy + r)]
        elif kind == "fp_arc":
            pts += _arc_points(xy(K.child(item, "start")), xy(K.child(item, "mid")),
                               xy(K.child(item, "end")))
        else:
            for pt in (K.child(item, "pts") or [])[1:]:
                if K.head(pt) == "xy":
                    pts.append(xy(pt))
                elif K.head(pt) == "arc":
                    pts += _arc_points(xy(K.child(pt, "start")), xy(K.child(pt, "mid")),
                                       xy(K.child(pt, "end")))
    if not pts:
        return None
    xs = [p[0] for p in pts]
    ys = [-p[1] if back else p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def _rotation(place: dict) -> list[list[float]]:
    """``Rz @ Ry @ Rx`` of the placement's three angles: turned about x first,
    then y, then z, each about the fixed axes. Entries within 1e-12 of a whole
    number are made it, so a quarter turn writes 0 and 1, not 6e-17."""
    def turn(axis: int, deg: float) -> list[list[float]]:
        c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
        i, j = [(1, 2), (2, 0), (0, 1)][axis]
        m = [[1.0 if r == k else 0.0 for k in range(3)] for r in range(3)]
        m[i][i], m[i][j], m[j][i], m[j][j] = c, -s, s, c
        return m

    def mul(a, b):
        return [[sum(a[r][k] * b[k][c] for k in range(3)) for c in range(3)] for r in range(3)]

    m = mul(turn(2, place["rot_z_deg"]), mul(turn(1, place["rot_y_deg"]),
                                             turn(0, place["rot_x_deg"])))
    return [[float(round(v)) + 0.0 if abs(v - round(v)) < 1e-12 else v for v in row]
            for row in m]


def _number(value, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise _FitError(f"{label} is {value!r}: give a number")
    return float(value)


def _record(value, label: str, required: tuple, optional: tuple = ()) -> dict:
    """``value`` held to its keys: an object, every required key, nothing else.
    An unknown key names the nearest known one."""
    import difflib
    if not isinstance(value, dict):
        raise _FitError(f"{label} is not an object")
    known = (*required, *optional)
    for key in value:
        if key not in known:
            near = difflib.get_close_matches(str(key), known, n=1)
            raise _FitError(f"{label} has an unknown key {key!r}"
                            + (f" (did you mean {near[0]!r}?)" if near else "")
                            + f"; it takes {', '.join(known)}")
    missing = [k for k in required if k not in value]
    if missing:
        raise _FitError(f"{label} has no {', '.join(missing)}")
    return value


def _placement(raw, label: str) -> dict:
    """The six placement numbers, held to their keys: x, y and z stated, a
    rotation not stated is 0."""
    if not isinstance(raw, dict):
        raise _FitError(f"{label} is not an object: give {{{', '.join(PLACEMENT_KEYS)}}}")
    _record(raw, label, PLACEMENT_REQUIRED, PLACEMENT_KEYS[3:])
    return {k: _number(raw.get(k, 0.0), f"{label}.{k}") for k in PLACEMENT_KEYS}


def _refs(raw, label: str) -> set[str]:
    if raw is None:
        return set()
    if not isinstance(raw, list) or not all(isinstance(r, str) and r for r in raw):
        raise _FitError(f"{label} is {raw!r}: give a list of references")
    return set(raw)


def _box_mm(raw, label: str) -> list[list[float]]:
    """``[[x0, y0, z0], [x1, y1, z1]]``, each low below its high."""
    if (not isinstance(raw, list) or len(raw) != 2
            or not all(isinstance(c, list) and len(c) == 3 for c in raw)):
        raise _FitError(f"{label} is {raw!r}: give [[x0, y0, z0], [x1, y1, z1]] in mm, "
                        f"in the enclosure's frame")
    lo = [_number(v, f"{label}[0]") for v in raw[0]]
    hi = [_number(v, f"{label}[1]") for v in raw[1]]
    if not all(a < b for a, b in zip(lo, hi)):
        raise _FitError(f"{label} is {raw!r}: each low corner must be below its high one")
    return [lo, hi]


def _contacts(raw, label: str) -> dict:
    """``{ref: {feature: {reason, region_mm?}}}``, held to its shape.

    A by-design contact names one shape and one feature, and says why. A feature
    is a region of the enclosure (``region_mm``, a box in the enclosure's frame)
    or one shape of another board in the same enclosure (``<fit>:<ref>``, or
    ``<fit>:board`` for its body). (What slipped through elsewhere: an
    allow-list entry for "this part against anything" hid a real interference.
    A contact that names a ref alone is refused, so it cannot excuse that part
    against another feature.)"""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise _FitError(f"{label} is not an object: give {{ref: {{feature: {{reason, "
                        f"region_mm}}}}}}")
    out: dict = {}
    for ref, features in raw.items():
        where = f"{label}[{ref!r}]"
        if not isinstance(features, dict) or not features:
            raise _FitError(f"{where} names no feature: a contact is one part against one "
                            f"feature (a region of the enclosure, region_mm, or another "
                            f"board's part, '<fit>:<ref>'), never a part against anything")
        out[ref] = {}
        for feature, rec in features.items():
            at = f"{where}[{feature!r}]"
            if not isinstance(feature, str) or not feature.strip():
                raise _FitError(f"{at}: name the feature")
            board_feature = ":" in feature
            _record(rec, at, ("reason",), () if board_feature else ("region_mm",))
            reason = rec.get("reason")
            if not isinstance(reason, str) or not reason.strip():
                raise _FitError(f"{at} has no reason: a contact nobody explained is one "
                                f"nobody can check, delete or defend when the design moves")
            if board_feature:
                fid, _, oref = feature.partition(":")
                if not fid or not oref:
                    raise _FitError(f"{at}: a feature on another board is '<fit>:<ref>'")
                out[ref][feature] = {"reason": reason.strip(), "fit": fid, "ref": oref}
                continue
            if "region_mm" not in rec:
                raise _FitError(f"{at} has no region_mm: a feature of the enclosure is the "
                                f"box, in its frame, where this part may touch it")
            out[ref][feature] = {"reason": reason.strip(),
                                 "region_mm": _box_mm(rec["region_mm"], f"{at}.region_mm")}
    return out


def _heights(raw, label: str) -> dict:
    """``{ref: {h, source}}``: the height above the board of a part with no 3D
    model, and where the number comes from (a datasheet's seated height)."""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise _FitError(f"{label} is not an object: give {{ref: {{h, source}}}}")
    out = {}
    for ref, rec in raw.items():
        at = f"{label}[{ref!r}]"
        _record(rec, at, ("h", "source"))
        h = _number(rec["h"], f"{at}.h")
        if not h > 0.0:
            raise _FitError(f"{at}.h is {h:g}: a height is above 0 mm")
        if not isinstance(rec["source"], str) or not rec["source"].strip():
            raise _FitError(f"{at}.source is empty: say where the height comes from")
        out[ref] = {"h": h, "source": rec["source"].strip()}
    return out


@dataclasses.dataclass
class _Fit:
    """One board in one enclosure, as the records state it."""

    id: str                     # "" in the singular form
    board: str                  # absolute paths
    enclosure: str
    place: dict
    bodiless: set
    contacts: dict
    heights: dict
    parts: list = dataclasses.field(default_factory=list)
    models: dict = dataclasses.field(default_factory=dict)
    tree: list | None = None
    enclosure_id: str = ""      # the sha256 of the enclosure file's bytes

    def name(self, ref: str) -> str:
        return f"{self.id}:{ref}" if self.id else ref

    @property
    def key(self) -> str:
        """The board's id in the geometry's request and answer."""
        return self.id or "board"


def _fits(ctx: GateContext, gid: str) -> list[_Fit] | Verdict:
    """The fits the records state: ``pcb_enclosure_fits``, each entry one board
    in its enclosure, or the singular keys for one. A skip when nothing says
    which enclosure or where the board sits; ``_FitError`` for a record the gate
    cannot read."""
    raw = ctx.param("pcb_enclosure_fits")
    if raw is not None:
        if not isinstance(raw, dict) or not raw:
            raise _FitError("pcb_enclosure_fits is not an object keyed by fit id: give "
                            "{id: {board, enclosure_step, placement, bodiless, contacts, "
                            "heights}}")
        both = [k for k in FIT_KEYS.values()
                if k != "pcb_board" and ctx.param(k) is not None]
        if both:
            raise _FitError(f"pcb_enclosure_fits and {', '.join(both)} are both stated: "
                            f"state each fit once, in pcb_enclosure_fits")
        fits = []
        for fid, rec in raw.items():
            label = f"pcb_enclosure_fits[{fid!r}]"
            if not isinstance(fid, str) or not _FIT_ID.match(fid):
                raise _FitError(f"{label}: a fit's id is letters, digits, '-' and '_'")
            _record(rec, label, FIT_REQUIRED, tuple(k for k in FIT_KEYS if k not in FIT_REQUIRED))
            for key in ("board", "enclosure_step"):
                if not isinstance(rec[key], str) or not rec[key].strip():
                    raise _FitError(f"{label}.{key} is {rec[key]!r}: give a path from the "
                                    f"project root")
            fits.append(_Fit(fid, K.project_path(ctx, rec["board"]),
                             K.project_path(ctx, rec["enclosure_step"]),
                             _placement(rec["placement"], f"{label}.placement"),
                             _refs(rec.get("bodiless"), f"{label}.bodiless"),
                             _contacts(rec.get("contacts"), f"{label}.contacts"),
                             _heights(rec.get("heights"), f"{label}.heights")))
        return fits
    path = _board_path(ctx)
    if path is None:
        return _skip(gid, _NO_BOARD)
    rel = ctx.param("pcb_enclosure_step")
    if not rel:
        return _skip(gid, "no pcb_enclosure_step: the enclosure's solid, as a STEP file, "
                          "is the project's; this pack will not invent one")
    place = ctx.param("pcb_board_placement")
    if not isinstance(place, dict):
        return _skip(gid, "no pcb_board_placement: where the board sits in the enclosure "
                          f"({{{', '.join(PLACEMENT_KEYS)}}}) is the project's to say")
    return [_Fit("", path, K.project_path(ctx, rel),
                 _placement(place, "pcb_board_placement"),
                 _refs(ctx.param("pcb_enclosure_bodiless"), "pcb_enclosure_bodiless"),
                 _contacts(ctx.param("pcb_enclosure_contacts"), "pcb_enclosure_contacts"),
                 _heights(ctx.param("pcb_part_heights_mm"), "pcb_part_heights_mm"))]


def _read_board(ctx: GateContext, gid: str, fit: _Fit) -> str | None:
    """Read ``fit``'s board, hold it to the reader, and find its parts. Returns a
    skip reason, or None; raises ``_FitError`` for a board or record it cannot
    read as this design's.

    The board is held to the reader's ceiling before any 3D model is read: a
    board newer than the reader was checked through skips with the guard's own
    words. (What slipped through, noted in the review of P4: this gate read a
    newer file's footprints and models on trust while the guard skipped it.)"""
    label = f"fit {fit.id}: " if fit.id else ""
    try:
        tree = ctx.load_file(fit.board, loader=K.load_tree)
    except (K.KicadFormatError, UnicodeDecodeError) as exc:
        raise _FitError(f"{label}the board does not parse: {exc}") from exc
    version, problems = K.board_problems(tree)
    if version is None:
        raise _FitError(f"{label}{problems[0]}")
    ceiling = K.reader_ceiling(version)
    if ceiling:
        return (f"{label}{ceiling}: a newer format is a limit of this reader, not a "
                f"defect in the board, so nothing on it is judged here")
    if version < K.MIN_BOARD_VERSION:
        raise _FitError(f"{label}file version {version} is older than "
                        f"{K.MIN_BOARD_VERSION} (KiCad 6): this reader finds footprints "
                        f"from KiCad 6 on")
    parts, models = _parts_and_models(tree)
    on_board = {K.footprint_ref(fp)[0] for fp in K.children(tree, "footprint")}

    def placed(ref: str, what: str) -> None:
        if ref in parts:
            return
        why = ("the board does not place it (do not populate, board only, or neither "
               "SMD nor through-hole)" if ref in on_board else "the board has no such part")
        raise _FitError(f"{label}{what} names {ref}: {why}")

    for ref, rec in fit.heights.items():
        placed(ref, "a declared height")
        if ref in fit.bodiless:
            raise _FitError(f"{label}{ref} is listed as bodiless and has a declared height: "
                            f"it has a body or it does not")
        if models.get(ref):
            raise _FitError(f"{label}{ref} names a 3D model ({models[ref][0]}) and a declared "
                            f"height: one body each. Hide or remove the model to fit the "
                            f"height, or drop the height to fit the model")
        fp = next(f for f in K.children(tree, "footprint") if K.footprint_ref(f)[0] == ref)
        box = _courtyard_box(fp)
        if box is None:
            raise _FitError(f"{label}{ref} has a declared height and no courtyard on its own "
                            f"side: the box is the courtyard's, raised to the height")
        rec["box"] = box
    for ref in fit.contacts:
        if ref != BODY:
            placed(ref, "a contact")
            if ref in fit.bodiless:
                raise _FitError(f"{label}{ref} is listed as bodiless and has a contact: a "
                                f"part with no body touches nothing")
    fit.tree = tree
    fit.parts = [r for r in parts if r not in fit.bodiless]
    fit.models = models
    unmodelled = sorted(r for r in fit.parts if not models.get(r) and r not in fit.heights)
    if unmodelled:
        # Held to the 200 characters a skip reason keeps: at 207 the last choice,
        # pcb_enclosure_bodiless, was cut off (seen in the review of P7).
        return (f"{label}{len(unmodelled)} placed part(s) name no 3D model: "
                f"{', '.join(unmodelled[:8])}. Give each a model, state its height in "
                f"pcb_part_heights_mm, or list a part with no body in pcb_enclosure_bodiless")
    return None


def _board_contacts(fits: list[_Fit]) -> dict:
    """``{fit key: {ref: {other fit key: [refs]}}}``: every contact between two
    boards, made to hold both ways, and each held to a board in the same
    enclosure and a shape it places. Raises ``_FitError``."""
    by_id = {f.id: f for f in fits}
    pairs: dict = {}
    for fit in fits:
        for ref, features in fit.contacts.items():
            for feature, rec in features.items():
                if "fit" not in rec:
                    if ref == BODY:
                        raise _FitError(f"{fit.name(ref)} against {feature}: the board body "
                                        f"may already touch the enclosure, so a contact for "
                                        f"it excuses nothing")
                    continue
                other = by_id.get(rec["fit"]) if fit.id else None
                if other is None or other is fit:
                    raise _FitError(f"{fit.name(ref)} against {feature}: no other fit is "
                                    f"called {rec['fit']!r}"
                                    + ("" if fit.id else " (a contact with another board "
                                       "needs pcb_enclosure_fits)"))
                if other.enclosure_id != fit.enclosure_id:
                    raise _FitError(f"{fit.name(ref)} against {feature}: {other.id} is in "
                                    f"another enclosure")
                if other.tree is not None and rec["ref"] != BODY and rec["ref"] not in other.parts:
                    raise _FitError(f"{fit.name(ref)} against {feature}: {other.id} places "
                                    f"no {rec['ref']}")
                for a, p, b, q in ((fit, ref, other, rec["ref"]), (other, rec["ref"], fit, ref)):
                    pairs.setdefault(a.key, {}).setdefault(p, {}).setdefault(b.key, [])
                    if q not in pairs[a.key][p][b.key]:
                        pairs[a.key][p][b.key].append(q)
    return pairs


def _xyz(v) -> str:
    return "(" + ", ".join(f"{round(c, 3):g}" for c in v) + ")"


def _region_refusal(fit: _Fit, parts: dict, enclosure_bbox,
                    limit: float) -> tuple[str, str] | None:
    """Why ``fit``'s contact regions cannot stand as measured, as ``(error,
    detail)``, or None. The error is what to do, short enough for the 200
    characters a verdict's error keeps; the detail has the boxes it read.

    ``parts`` is the geometry's answer for the board (``_fit.py``: each part
    with a region carries its box as placed and, for each region, the box of the
    enclosure material it took out). A region is refused when it holds the
    enclosure's whole box; when a part's regions together take out all of the
    enclosure (its distance to the rest came back None, and before the review of
    P7 such a part was left out of the verdict: judged against nothing); and when
    the material it takes out reaches more than :data:`REGION_REACH_LIMITS` of the
    claim's limits past the part's own box. Inside that reach a region is the
    person's statement of the feature: one drawn over two features excuses both."""
    blo, bhi = enclosure_bbox
    reach = REGION_REACH_LIMITS * max(limit, 0.0)
    for ref, feats in sorted(fit.contacts.items()):
        regions = {f: rec["region_mm"] for f, rec in feats.items() if "region_mm" in rec}
        if not regions:
            continue
        who = fit.name(ref)
        for feat, (lo, hi) in sorted(regions.items()):
            if all(lo[i] <= blo[i] and hi[i] >= bhi[i] for i in range(3)):
                return (f"{who} against {feat}: its region holds the whole enclosure, so it "
                        f"excuses the part against anything. Draw the region around the "
                        f"feature it touches",
                        f"the region is {_xyz(lo)} to {_xyz(hi)} mm; the enclosure's box is "
                        f"{_xyz(blo)} to {_xyz(bhi)} mm")
        row = (parts.get(ref) or {}).get("enclosure")
        if row is None:
            continue                # not in the export: named as missing, not judged
        if row.get("clearance_mm") is None:
            return (f"{who}: its regions ({', '.join(sorted(regions))}) take out all of the "
                    f"enclosure, so they excuse it against anything. Draw each region "
                    f"around the feature it touches",
                    "; ".join(f"{feat} is {_xyz(lo)} to {_xyz(hi)} mm"
                              for feat, (lo, hi) in sorted(regions.items())))
        plo, phi = row["bbox"]
        rlo = [v - reach for v in plo]
        rhi = [v + reach for v in phi]
        for feat, (lo, hi) in sorted(regions.items()):
            got = row["excused"][feat].get("material_bbox")
            if got is None:
                continue            # no enclosure material in it: it excuses nothing
            mlo, mhi = got
            if all(mlo[i] >= rlo[i] - REGION_REACH_TOL_MM
                   and mhi[i] <= rhi[i] + REGION_REACH_TOL_MM for i in range(3)):
                continue
            detail = (f"{who} against {feat}: the region takes out enclosure material from "
                      f"{_xyz(mlo)} to {_xyz(mhi)} mm. {ref}'s own box, as placed, is "
                      f"{_xyz(plo)} to {_xyz(phi)} mm, and a region may reach {reach:g} mm "
                      f"past it ({REGION_REACH_LIMITS:g} times the claim's limit). Material "
                      f"farther away cannot come within the limit of {ref}: a region that "
                      f"holds it would excuse {ref} against any feature there")
            clo = [round(max(lo[i], rlo[i]), 3) for i in range(3)]
            chi = [round(min(hi[i], rhi[i]), 3) for i in range(3)]
            if not all(a < b for a, b in zip(clo, chi)):
                return (f"{who} against {feat}: the region holds no material within "
                        f"{reach:g} mm of {ref}'s own box ({REGION_REACH_LIMITS:g} times the "
                        f"claim's limit), so it excuses nothing. Remove it, or draw it where "
                        f"{ref} touches", detail)
            return (f"{who} against {feat}: the region reaches more than {reach:g} mm past "
                    f"{ref}'s own box ({REGION_REACH_LIMITS:g} times the claim's limit). "
                    f"Draw it within {json.dumps([clo, chi])}", detail)
    return None


def _helper(mode: str, request: str, cwd: str, timeout: float, log: str):
    """``_fit.py <mode> <request>``: ``(parsed answer, None)`` or ``(None, why)``
    with ``why`` ``("skip"|"error", text)``."""
    try:
        geo = subprocess.run([sys.executable, FIT_HELPER, mode, request], capture_output=True,
                             text=True, timeout=timeout, stdin=subprocess.DEVNULL, cwd=cwd)
    except subprocess.TimeoutExpired:
        return None, ("skip", f"the fit geometry exceeded {timeout:g} s (pcb_fit_timeout_s) "
                              f"and was killed: nothing was shown")
    with open(log, "a", encoding="utf-8") as fh:
        fh.write(f"\n_fit.py {mode} exit {geo.returncode}\n{geo.stdout}\n{geo.stderr}\n")
    lines = [ln for ln in geo.stdout.splitlines() if ln.startswith("{")]
    if geo.returncode != 0 or not lines:
        tail = (geo.stderr or geo.stdout).strip().splitlines()[-1:] or ["no output"]
        return None, ("error", f"the fit geometry failed (exit {geo.returncode}): "
                               f"{tail[0][:200]}")
    return json.loads(lines[-1]), None


def _export(ctx: GateContext, gid: str, fit: _Fit, timeout: float):
    """Stage ``fit``'s board, give each part fitted at a declared height its box
    as its model, and export the populated board as STEP. ``(step, log)`` or
    ``(None, ("skip"|"error", text, evidence))``."""
    staged = _stage(ctx, f"{gid}.{fit.id}" if fit.id else gid, fit.board,
                    board=True, schematic=False)
    if isinstance(staged, Verdict):
        return None, ("error", staged.error or staged.detail, staged.evidence)
    stem, stage = staged
    src_dir = os.path.dirname(os.path.abspath(fit.board))
    for ref in fit.parts:
        for model in fit.models.get(ref, []):
            if model.startswith("${KIPRJMOD}/"):
                rel_model = model[len("${KIPRJMOD}/"):]
                src = os.path.join(src_dir, rel_model)
                if os.path.isfile(src):
                    dst = os.path.join(stage, rel_model)
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    shutil.copyfile(src, dst)
    log = os.path.join(stage, "fit.log")
    with open(log, "w", encoding="utf-8") as fh:
        fh.write(f"fit {fit.key}: {fit.board}\n")
    if fit.heights:
        # The box is in the footprint's model frame: x right, y UP (KiCad's
        # footprint frame is y down), z up from the seating plane. kicad-cli
        # then seats, turns and flips it as it does any model. Measured on
        # KiCad 9.0.9: a part's model sits at z 1.595 on a 1.6 mm board (and
        # -0.085 under it), not on the board body's top face (1.51), so a box
        # placed by this gate's own arithmetic would sit 0.085 mm low. Named by
        # absolute path: kicad-cli resolves ${KIPRJMOD} only where a .kicad_pro
        # sits beside the board (measured: without one, every such model is
        # "File not found" and left out, with exit 0).
        os.makedirs(os.path.join(stage, HEIGHT_DIR), exist_ok=True)
        tree = K.parse(K.dump(fit.tree))           # a copy: the loaded tree stays as read
        boxes = []
        for n, (ref, rec) in enumerate(sorted(fit.heights.items())):
            x0, y0, x1, y1 = rec["box"]
            name = f"{n:03d}_{re.sub(r'[^A-Za-z0-9_-]', '_', ref)}.step"
            path = os.path.abspath(os.path.join(stage, HEIGHT_DIR, name))
            boxes.append({"ref": ref, "path": path,
                          "lo": [x0, -y1, 0.0], "hi": [x1, -y0, rec["h"]]})
            fp = next(f for f in K.children(tree, "footprint") if K.footprint_ref(f)[0] == ref)
            fp.append(["model", K.Q(path),
                       ["offset", ["xyz", "0", "0", "0"]], ["scale", ["xyz", "1", "1", "1"]],
                       ["rotate", ["xyz", "0", "0", "0"]]])
        with open(stem + ".kicad_pcb", "w", encoding="utf-8") as fh:
            fh.write(K.dump(tree) + "\n")
        request = os.path.join(stage, "boxes_request.json")
        with open(request, "w", encoding="utf-8") as fh:
            json.dump({"boxes": boxes}, fh, indent=2)
        _answer, why = _helper("boxes", request, stage, timeout, log)
        if why is not None:
            return None, (*why, [log])
    step = os.path.join(stage, "board.step")
    argv = ["kicad-cli", "pcb", "export", "step", "--subst-models", "--no-dnp",
            "--user-origin", "0x0mm", "-f", "-o", step, os.path.basename(stem) + ".kicad_pcb"]
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                              stdin=subprocess.DEVNULL, cwd=stage)
    except FileNotFoundError as exc:
        return None, ("skip", f"could not launch kicad-cli: {exc}", [])
    except subprocess.TimeoutExpired:
        return None, ("skip", f"kicad-cli pcb export step exceeded {timeout:g} s and was "
                              f"killed", [log])
    with open(log, "a", encoding="utf-8") as fh:
        fh.write(" ".join(argv) + f"\nexit {proc.returncode}\n{proc.stdout}\n{proc.stderr}\n")
    if proc.returncode != 0 or not os.path.isfile(step):
        return None, ("error", f"kicad-cli pcb export step exited {proc.returncode} without "
                               f"a STEP: {(proc.stderr or proc.stdout).strip()[-200:]}", [log])
    return (step, log), None


def _placed_at(place: dict) -> str:
    turned = [f"{a} {place[f'rot_{a}_deg']:g}" for a in "xyz" if place[f"rot_{a}_deg"]]
    return (f"x {place['x']:g}, y {place['y']:g}, z {place['z']:g} mm"
            + (f", turned {', '.join(turned)} deg" if turned else ""))


@gate(
    id="pcb.enclosure_fit",
    basis="established",
    title="The populated boards fit their enclosure: nothing collides, every part keeps the claim's clearance",
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
    """Each board as KiCad exports it, every part's 3D model in place, against
    its enclosure's solid and against the other boards in the same enclosure:
    OpenCascade's shared volume (nothing may collide, a board body included) and
    its distance from each part, against the limit of the claim tagged
    ``enclosure-fit`` (``ctx.acceptance``, mm, ``>=``). A board body may touch
    the enclosure, as it does on its standoffs; against another board it keeps
    the limit. The verdict is the worst over every board.

    The board's STEP comes from ``kicad-cli pcb export step --subst-models
    --no-dnp --user-origin 0x0mm``: the board file's (0, 0) at the origin, x
    right, y up (KiCad's y negated), z up from the board's bottom face. The
    placement (:data:`PLACEMENT_KEYS`) turns it about x, then y, then z, as one
    3x3 rotation, then moves it, into the enclosure's frame. One board is stated
    by ``pcb_board`` and the singular keys; several by ``pcb_enclosure_fits``,
    and boards whose enclosure files hold the same bytes, however each path is
    spelled, are fitted against each other too.

    **A part the fit cannot see is a skip, never a pass.** kicad-cli leaves out,
    silently and with exit 0, every part whose 3D model it cannot find (KiCad's
    3D library is a separate install on most hosts), and a fit run without the
    part reads clear. So every placed part (SMD or through-hole, not DNP) must
    name a model and arrive in the export under its reference, or have a
    declared height (``pcb_part_heights_mm``: its courtyard's box, raised to that
    height, stands in for its body), or the gate skips naming it. A part that
    truly has no body above the board (an antenna drawn in copper) goes in
    ``pcb_enclosure_bodiless``, by reference, a recorded statement.

    **A by-design contact is one part against one feature** (``pcb_enclosure_
    contacts``, :func:`_contacts`): a switch on its plunger, a receptacle in its
    cutout, a light pipe, a cell on foam. The part's distance to that feature is
    listed and left out of its clearance; against every other feature it keeps
    the limit, and shared volume still fails it.
    """
    gid = "pcb.enclosure_fit"
    if ctx.param(RETIRED_CLEARANCE_KEY) is not None:
        return Verdict(gate=gid, passed=False,
                       error=f"{RETIRED_CLEARANCE_KEY} is retired: the least gap a part keeps "
                             f"from the enclosure is the limit of the claim tagged {FIT_TAG} "
                             f"(acceptance >= the gap, in mm). State it there and remove "
                             f"{RETIRED_CLEARANCE_KEY}; this gate will not drop a number a "
                             f"project states")
    try:
        fits = _fits(ctx, gid)
    except _FitError as exc:
        return Verdict(gate=gid, passed=False, error=str(exc))
    if isinstance(fits, Verdict):
        return fits
    # After the skips: a project with no enclosure needs no enclosure claim.
    acc = ctx.acceptance(FIT_TAG)
    if str(acc.units or "").strip() != "mm" or acc.comparator.value != ">=":
        return Verdict(gate=gid, passed=False,
                       error=f"the claim tagged {FIT_TAG} states {acc.comparator.value} "
                             f"{acc.limit:g} {acc.units!r}; this gate judges the least gap a "
                             f"part keeps from the enclosure (>=) in mm: state the limit so")
    for fit in fits:
        if not os.path.isfile(fit.board):
            return Verdict(gate=gid, passed=False, detail=f"no board at {fit.board}")
        if not os.path.isfile(fit.enclosure):
            return Verdict(gate=gid, passed=False, detail=f"no enclosure at {fit.enclosure}")
    # Boards are in one enclosure when their enclosure files hold the same
    # bytes. (What slipped through in P7: the group was the path as written, so
    # cad/enc.step and ./cad/enc.step were two enclosures, each board was fitted
    # against the enclosure alone, and two boards that shared 11.3 mm^3 read
    # pass.) *Rejected:* realpath or os.path.samefile (a verdict's key holds the
    # bytes at each path, not which file a link names: a copy turned into a link
    # to its original would keep a pass that never fitted the boards together,
    # against invariant 7); normpath (a link still splits the group). The cost:
    # two copies of one file are one enclosure (enclosure-fit.md, "What it does
    # not see").
    digests = {path: hashlib.sha256(ctx.load_file(path)).hexdigest()
               for path in sorted({f.enclosure for f in fits})}
    for fit in fits:
        fit.enclosure_id = digests[fit.enclosure]
    skips: list[str] = []
    errors: list[str] = []
    try:
        ready = []
        for fit in fits:
            why = _read_board(ctx, gid, fit)
            if why:
                skips.append(why)
            else:
                ready.append(fit)
        pairs = _board_contacts(fits)
    except _FitError as exc:
        return Verdict(gate=gid, passed=False, error=str(exc))
    timeout = float(ctx.param("pcb_fit_timeout_s", DEFAULT_FIT_TIMEOUT_S) or
                    DEFAULT_FIT_TIMEOUT_S)
    ev: list[str] = []
    exported = {}
    for fit in ready:
        done, why = _export(ctx, gid, fit, timeout)
        if why is not None:
            kind, text, cited = why
            label = f"fit {fit.id}: " if fit.id else ""
            (skips if kind == "skip" else errors).append(label + text)
            ev += cited
            continue
        exported[fit.key] = (fit, *done)

    hits: list[tuple[float, str]] = []
    rows: list[tuple[float, str, str]] = []
    body_rows: list[tuple[float, str, str]] = []
    excused: list[str] = []
    judged_parts = 0
    groups: dict[str, list] = {}
    for key, (fit, step, log) in exported.items():
        groups.setdefault(fit.enclosure_id, []).append((fit, step, log))
    for members in sorted(groups.values(), key=lambda m: m[0][0].enclosure):
        enclosure = members[0][0].enclosure
        first_stage = os.path.dirname(members[0][1])
        boards = []
        for fit, step, _log in members:
            regions = {ref: {feat: rec["region_mm"] for feat, rec in feats.items()
                             if "region_mm" in rec}
                       for ref, feats in fit.contacts.items()}
            boards.append({"id": fit.key, "board_step": step, "refs": fit.parts,
                           "rotation": _rotation(fit.place),
                           "translation": [fit.place["x"], fit.place["y"], fit.place["z"]],
                           "regions": {r: f for r, f in regions.items() if f},
                           "excused": pairs.get(fit.key, {})})
        request = os.path.join(first_stage, "fit_request.json")
        with open(request, "w", encoding="utf-8") as fh:
            json.dump({"enclosure_step": os.path.abspath(enclosure), "boards": boards},
                      fh, indent=2)
        result, why = _helper("fit", request, first_stage, timeout, members[0][2])
        ev += [log for _f, _s, log in members]
        if why is not None:
            (skips if why[0] == "skip" else errors).append(why[1])
            continue
        report = os.path.join(first_stage, "fit.json")
        with open(report, "w", encoding="utf-8") as fh:
            json.dump(result, fh, indent=2, sort_keys=True)
        ev = [report, *ev, *(step for _f, step, _l in members)]
        names = {fit.key: fit for fit, _s, _l in members}
        for fit, _step, _log in members:
            label = f"fit {fit.id}: " if fit.id else ""
            measured_parts = result["boards"][fit.key]["parts"]
            refused = _region_refusal(fit, measured_parts, result["enclosure_bbox"], acc.limit)
            if refused:
                return Verdict(gate=gid, passed=False, evidence=ev, error=refused[0],
                               detail=refused[1])
            # A board with a part missing from its export is still judged on what
            # arrived, and named as not judged. A shape that is there can only come
            # nearer to something when a missing one is added, never farther, so
            # its collision or its gap under the limit is a fail; its pass is not
            # one. (What slipped through in P7: a pair of boards is measured once,
            # under the first, and a board with a missing part was dropped with its
            # pair rows, so the same records read fail or skip by the order of
            # their keys.)
            missing = sorted(r for r in fit.parts if r not in measured_parts)
            boxed = [r for r in missing if r in fit.heights]
            unfound = [r for r in missing if r not in fit.heights]
            if boxed:
                errors.append(f"{label}the declared-height box of {', '.join(boxed[:8])} did "
                              f"not reach kicad-cli's export")
            if unfound:
                # Every part here names a model: one with neither skipped before the
                # export, and one with both is refused (_read_board). So the advice
                # is to hide the model as well as to state a height. (What slipped
                # through in P7: the advice was to state a height alone, which the
                # next run refused; and at 241 characters, past the 200 a skip
                # reason keeps, that advice was cut off before it was shown.)
                skips.append(f"{label}kicad-cli left out {', '.join(unfound[:8])}: it did not "
                             f"find their 3D models. Install KiCad's 3D library, keep each "
                             f"model beside the board, or hide it and state a height in "
                             f"pcb_part_heights_mm")
            judged_parts += sum(1 for r in fit.parts if r in measured_parts)
            for name, row in sorted(measured_parts.items()):
                enc = row["enclosure"]
                who = fit.name(name)
                if enc["interference_mm3"] > INTERFERENCE_TOL_MM3:
                    hits.append((enc["interference_mm3"],
                                 f"{who} shares {enc['interference_mm3']:.3g} mm^3 with the "
                                 f"enclosure"))
                if enc["clearance_mm"] is not None:
                    (body_rows if name == BODY else rows).append(
                        (enc["clearance_mm"], who, "the enclosure"))
                for feat, got in sorted(enc.get("excused", {}).items()):
                    d = got["clearance_mm"]
                    excused.append(f"{who} to {feat} " + (f"{d:.3g} mm" if d is not None else
                                                          "(no enclosure material there)"))
                for other, brow in sorted(row["boards"].items()):
                    them = names[other]
                    if brow["interference_mm3"] > INTERFERENCE_TOL_MM3:
                        shared = ", ".join(f"{them.name(q)} {v:.3g} mm^3"
                                           for q, v in sorted(brow.get("with", {}).items())
                                           if v > INTERFERENCE_TOL_MM3)
                        hits.append((brow["interference_mm3"],
                                     f"{who} shares {brow['interference_mm3']:.3g} mm^3 with "
                                     f"board {other}" + (f" ({shared})" if shared else "")))
                    if brow["clearance_mm"] is not None:
                        rows.append((brow["clearance_mm"], who, f"board {other}"))
                    for q, got in sorted(brow.get("excused", {}).items()):
                        excused.append(f"{who} to {them.name(q)} {got['clearance_mm']:.3g} mm")
    # A fail counts beside a board that was not judged; a skip or an error
    # beside a fail would hide the fail (the power rules' lesson, P5).
    judged = rows or body_rows
    hits.sort(reverse=True)
    gap = round(min(d for d, _w, _a in judged), 3) if judged else 0.0
    failed = bool(hits) or (bool(judged) and not acc.holds(gap))
    if not failed and errors:
        return Verdict(gate=gid, passed=False, evidence=ev, error="; ".join(errors))
    if not failed and (skips or not judged):
        return _skip(gid, "; ".join(skips) or "nothing was fitted")
    if hits:
        detail = f"{len(hits)} collision(s): " + ", ".join(t for _v, t in hits[:4])
    else:
        _d, who, against = min(judged)
        detail = (f"nothing collides; nearest {who} is {gap:g} mm from {against} vs "
                  f"{acc.comparator.value} {acc.limit:g} mm (the claim tagged {FIT_TAG})")
    if len(fits) == 1 and not fits[0].id:
        detail += f" ({judged_parts} part(s) placed at {_placed_at(fits[0].place)})"
    else:
        apart = (f" in {len(groups)} enclosure files, each board fitted against the "
                 f"others in its own" if len(groups) > 1 else "")
        detail += (f" ({len(exported)} board(s){apart}: "
                   + "; ".join(f"{f.id} at {_placed_at(f.place)}" for f, _s, _l in
                               exported.values()) + ")")
    if excused:
        detail += f"; declared contact(s): {', '.join(excused[:6])}"
    if skips or errors:
        detail += f"; not judged: {'; '.join([*skips, *errors])}"
    return Verdict(gate=gid, passed=not failed, measured=gap, limit=acc.limit,
                   comparator=">=", units="mm", detail=detail, evidence=ev)


# --------------------------------------------------------------------------- #
# the antenna's keep-out
# --------------------------------------------------------------------------- #
KEEPOUT_FILL_HELPER = os.path.join(_HERE, "_keepout_fill.py")

#: The KiCad footprint libraries whose parts this gate finds by itself: a
#: footprint from one of them is a radio module with its antenna on it. KiCad
#: 9.0.9's ``RF_Module`` holds 57 footprints (BLE, Wi-Fi, sub-GHz, LoRa and UWB
#: modules), 31 of them with a keep-out rule area (surveyed with this pack's
#: reader). Any other part comes in by a ``pcb_antennas`` record: a module from a
#: project's own library, or a chip antenna on a bare SoC. Rejected:
#: ``RF_Antenna`` and ``RF_GPS`` as well (8 of RF_Antenna's 16 footprints draw
#: their areas per layer on purpose: a chip antenna's feed keeps a track on F.Cu
#: where the inner layers keep none, and a GNSS module's area under its body
#: keeps tracks off its pads; extended to every copper layer, they would fail a
#: sound board); every footprint with a rule area (a USB-C receptacle's or a card
#: socket's keep-out guards no antenna).
ANTENNA_LIBRARIES = ("RF_Module",)

#: What a ``pcb_antennas`` record's ``keepout`` may be, beside ``{"drawing":
#: path}`` (the vendor's drawing, registered on the footprint by its numbered
#: pads) and ``{"rule_area": name}`` (a rule area the designer drew on the board
#: and named: a chip antenna's clearance, from its datasheet). ``footprint``: the
#: part's own rule area, read as a found module's is. ``none``: the part has no
#: antenna on the board (one on a cable, a u.FL or an SMA): its ``source`` says
#: so, and it is listed, not judged. Rejected: a polygon in the footprint's frame
#: (the person would have to know where the footprint's origin sits; a drawing
#: registered by its pads needs only the vendor's figure).
ANTENNA_FORMS = ("footprint", "none")
ANTENNA_KEYS = ("ref", "value", "keepout", "source")
ANTENNA_OPTIONAL = ("board",)
ANTENNA_DRAWN = ("drawing", "rule_area")

#: The name of each rule area this gate adds, ``nopekit_antenna_<ref>``. KiCad's
#: DRC names the area in each finding ("Items not allowed (keepout area
#: 'nopekit_antenna_U1')"), so a finding is tied to its part by name.
ANTENNA_AREA = "nopekit_antenna_"

#: What the added area forbids, as KiCad's keep-out flags: every kind of copper
#: its keep-out judges (tracks and arcs, vias, pads, a zone's fill; it has no
#: flag for a copper drawing or text, and does not report one). ``footprints``
#: stays allowed: the rule judges copper, and a part's pads in the area are
#: reported anyway; a part body over the antenna with no copper in the area is
#: ``lenses.md``'s.
#: (Measured with kicad-cli 9.0.9: an area that forbids footprints reports a
#: capacitor in it as "Footprint C1" beside its two pads.) Rejected: the source
#: area's own flags (NINA-B111's stock area forbids only footprints; a weakened
#: flag is what this rule is for).
ANTENNA_FORBIDS = (("tracks", "not_allowed"), ("vias", "not_allowed"),
                   ("pads", "not_allowed"), ("copperpour", "not_allowed"),
                   ("footprints", "allowed"))

#: A footprint's own rule area is its antenna's keep-out when it forbids tracks
#: and copper pour: what a vendor's "no copper under the antenna" asks. An area
#: that allows either (KiCad 9.0.9: three in RF_Module, one of them a
#: depanelization strip) guards something else, and is not read. When one such
#: area is on every copper layer, it alone is read: the footprint's other areas,
#: on F.Cu beside a pad, keep a feature clear on their own layers, which
#: ``pcb.drc`` judges. (Measured on RF_Module: five footprints carry such a pad
#: area beside their antenna's every-layer area. Extended to every layer, each
#: would read an inner ground plane under the module as copper in the keep-out.)
_ANTENNA_FLAGS = ("tracks", "copperpour")

#: How far at least one registration pad must sit off the line through the two
#: pads furthest apart, in the fit's limits (half the smaller side of the
#: drawing's largest pad): a drawing whose pads sit on one line is its own
#: mirror image turned a half turn, so a mirror (``y_axis`` read the wrong way)
#: puts the keep-out on the other side and still fits. A pad one limit off the
#: line moves two limits under a mirror, so the fit cannot take it. Rejected:
#: two pads (a mirror is never seen); any three (three pads on one row are as
#: blind as two); a fixed distance in mm (a limit means one thing beside a 0.3
#: mm pad and another beside a 2 mm one).
REGISTER_OFF_LINE = 1.0


def _antenna_record(rid, rec) -> dict:
    """One ``pcb_antennas`` record, held to its keys; raises ``P.RecordError``."""
    label = f"pcb_antennas[{rid!r}]"
    P.keys(rec, label, ANTENNA_KEYS, ANTENNA_OPTIONAL)
    P.source(rec, label)
    out = {"id": str(rid), "label": label, "ref": P.text(rec["ref"], f"{label}.ref"),
           "value": P.text(rec["value"], f"{label}.value"), "board": rec.get("board"),
           "source": rec["source"]}
    form = rec["keepout"]
    if isinstance(form, dict):
        P.keys(form, f"{label}.keepout", (), ANTENNA_DRAWN)
        if len(form) != 1:
            raise P.RecordError(f"{label}.keepout names {len(form)} of {', '.join(ANTENNA_DRAWN)}: "
                                f"give one")
        (kind, where), = form.items()
        out["keepout"] = (kind, P.text(where, f"{label}.keepout.{kind}"))
    elif form in ANTENNA_FORMS:
        out["keepout"] = (form, "")
    else:
        raise P.RecordError(f"{label}.keepout is {form!r}: give \"footprint\", \"none\", "
                            f"{{\"drawing\": <path>}} or {{\"rule_area\": <name>}}")
    return out


def _antenna_board(ctx: GateContext, path: str, label: str):
    """``(tree, board)`` of the board at ``path``, held as ``pcb.board_readable``
    holds ``pcb_board``, since a record may name a board the guard never reads:
    ``P.RecordError`` for one it would fail, ``P.ReaderLimit`` for one newer than
    the reader was checked through (the guard's own words)."""
    name = os.path.basename(path)
    if not os.path.isfile(path):
        raise P.RecordError(f"{label}: no board at {path}")
    try:
        tree = ctx.load_file(path, loader=K.load_tree)
    except (K.KicadFormatError, UnicodeDecodeError) as exc:
        raise P.RecordError(f"the board {name} does not parse: {exc}") from None
    version, problems = K.board_problems(tree)
    if K.reader_ceiling(version):
        raise P.ReaderLimit(f"the board {name}: {K.reader_ceiling(version)}")
    if problems:
        raise P.RecordError(f"the board {name} is not one this reader can vouch for: "
                            f"{'; '.join(problems)}")
    return tree, K.board_from_tree(tree)


def _copper_layers(tree: list) -> list[str]:
    """The board's copper layers in its own order (``F.Cu``, ``In1.Cu``, ...,
    ``B.Cu``), from its layer table."""
    table = K.child(tree, "layers") or []
    return [str(row[1]) for row in table[1:]
            if isinstance(row, list) and len(row) > 1 and str(row[1]).endswith(".Cu")]


def _zone_layers(zone: list, copper: list[str]) -> set[str]:
    node = K.child(zone, "layers") or K.child(zone, "layer") or []
    out: set[str] = set()
    for name in (str(x) for x in node[1:]):
        if name == "*.Cu":
            out |= set(copper)
        elif name == "F&B.Cu":
            out |= {"F.Cu", "B.Cu"}
        else:
            out.add(name)
    return out


def _keepout_flags(zone: list) -> dict[str, str] | None:
    """A rule area's keep-out flags (``{"tracks": "not_allowed", ...}``), or None
    for a zone that is not a keep-out (a copper pour)."""
    node = K.child(zone, "keepout")
    if node is None:
        return None
    return {str(c[0]): str(c[1]) for c in node[1:] if isinstance(c, list) and len(c) > 1}


def _footprint_areas(fp: list, copper: list[str]) -> list[list]:
    """The rule areas of footprint ``fp`` read as its antenna's keep-out
    (:data:`_ANTENNA_FLAGS`). KiCad 9 saves a footprint's zones in the board's
    own frame (measured: the stock ESP32-S3-WROOM-1 at 90 degrees, and at 37
    degrees on B.Cu), so each polygon is used as written."""
    areas = [z for z in K.children(fp, "zone")
             if all((_keepout_flags(z) or {}).get(f) == "not_allowed" for f in _ANTENNA_FLAGS)]
    every = [z for z in areas if set(copper) <= _zone_layers(z, copper)]
    return every or areas


def _footprint_to_board(fp: list):
    """A function from footprint ``fp``'s own frame, seen from the top (as
    :func:`_kicad.footprint_land` gives a pad), to the board's.

    KiCad 9 writes a footprint's ``(at x y angle)`` and, for one on ``B.Cu``, its
    items with local y negated; it turns by ``angle`` counter-clockwise as seen
    with y down. Checked against pcbnew 9.0.9's own pad positions (every pad of
    the stock ESP32-S3-WROOM-1 at 90 degrees on F.Cu, and at 37 degrees flipped
    to B.Cu: within 1e-6 mm), and in the pack's tests against the footprint's own
    rule area, which KiCad saves in the board's frame."""
    at = K.child(fp, "at")
    x0, y0 = float(at[1]), float(at[2])
    turn = math.radians(float(at[3])) if len(at) > 3 else 0.0
    back = str(K.value(fp, "layer", default="")) == "B.Cu"
    c, s = math.cos(turn), math.sin(turn)

    def place(point):
        x, y = point
        if back:
            y = -y
        return x0 + x * c + y * s, y0 - x * s + y * c
    return place


def _mm(v: float) -> str:
    """A coordinate as KiCad writes one: mm, to 1 nm, no trailing zeros."""
    text = f"{round(v, 6):.6f}".rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


def _drawing_area(ctx: GateContext, tree: list, fp: list, ref: str, rel: str,
                  label: str) -> tuple[list, dict]:
    """The vendor's keep-out from the drawing ``rel``, carried onto the board:
    ``(polygon node in the board's frame, the registration)``. The drawing has
    the shape ``pcb.land_pattern`` reads (one file can serve both gates) and a
    ``keepout``: ``{"polygon": [[x, y], ...]}`` in its own frame and units (mm),
    its y running as its ``y_axis`` says.

    It is registered on the footprint by its numbered pads: :func:`_land_fit`
    with ``pair_by="number"`` (registration only; a land is judged by function),
    then the same rigid fit carries the polygon. A drawing that does not register
    (a pad past the fit's limit, a number the footprint lacks or repeats) is an
    error, never a guess: a keep-out placed wrong judges the wrong copper."""
    src = K.project_path(ctx, rel)
    try:
        record = ctx.load_file(src, loader=_load_json)
    except (OSError, ValueError) as exc:
        raise P.RecordError(f"{label}: the drawing {rel} cannot be read: {exc}") from None
    vendor = _drawing(record)
    if isinstance(vendor, str):
        raise P.RecordError(f"{label}: the drawing {rel}: {vendor}")
    area = record.get("keepout")
    if not isinstance(area, dict):
        raise P.RecordError(f"{label}: the drawing {rel} has no keepout ({{\"polygon\": [[x, y], "
                            f"...]}} in the drawing's frame): a land drawing alone places no "
                            f"keep-out")
    P.keys(area, f"{label}: the drawing {rel}'s keepout", ("polygon",))
    poly = area["polygon"]
    if not isinstance(poly, list) or len(poly) < 3:
        raise P.RecordError(f"{label}: the drawing {rel}'s keepout polygon takes three or more "
                            f"[x, y] points")
    up = record["y_axis"] == "up"
    points = []
    for i, pt in enumerate(poly):
        if not isinstance(pt, list) or len(pt) != 2:
            raise P.RecordError(f"{label}: the drawing {rel}'s keepout point {i} is {pt!r}: "
                                f"give [x, y] in mm")
        x, y = (P.num(v, f"{label}: the drawing {rel}'s keepout point {i}") for v in pt)
        points.append((x, -y if up else y))
    pads, _unnumbered = K.footprint_land(tree, ref)
    counts = Counter(p.number for p in pads)
    repeated = sorted({v["number"] for v in vendor if counts.get(v["number"], 0) > 1})
    if repeated:
        raise P.RecordError(f"{label}: the drawing {rel} registers on pad(s) "
                            f"{', '.join(repeated)}, which {ref}'s footprint repeats: register "
                            f"it on pads whose number is on one pad alone")
    if len(vendor) < 3:
        raise P.RecordError(f"{label}: the drawing {rel} gives {len(vendor)} pad(s): register it "
                            f"on three or more, not all on one line, or its mirror image fits too")
    a, b = max(itertools.combinations(vendor, 2),
               key=lambda pq: math.dist((pq[0]["x"], pq[0]["y"]), (pq[1]["x"], pq[1]["y"])))
    span = math.dist((a["x"], a["y"]), (b["x"], b["y"]))
    off = max(abs((b["x"] - a["x"]) * (v["y"] - a["y"]) - (b["y"] - a["y"]) * (v["x"] - a["x"]))
              / span for v in vendor) if span > 0 else 0.0
    limit = max(min(v["w"], v["h"]) / 2.0 for v in vendor)
    if off < REGISTER_OFF_LINE * limit:
        raise P.RecordError(f"{label}: the drawing {rel}'s pads sit on one line (the furthest "
                            f"{off:.3g} mm off it, under its {limit:.3g} mm limit), so its mirror "
                            f"image fits too: add a pad off that line")
    fit = _land_fit(ref, [("", p) for p in pads], vendor, pair_by="number")
    if isinstance(fit, str):
        raise P.RecordError(f"{label}: the drawing {rel} does not register: {fit}")
    w = fit["worst"]
    if w["residual_mm"] > w["limit_mm"]:
        raise P.RecordError(f"{label}: the drawing {rel} does not register on {ref}: pad "
                            f"{w['board_pad']} sits {w['residual_mm']:g} mm from the drawing's "
                            f"after the best rigid fit (no mirror), past {w['limit_mm']:g} mm")
    by_number = {p.number: p for p in pads}
    _theta, carry = K.rigid_transform(
        [((v["x"], v["y"]), (by_number[v["number"]].x, by_number[v["number"]].y)) for v in vendor])
    place = _footprint_to_board(fp)
    board_pts = [place(carry(pt)) for pt in points]
    fit.update(drawing=rel, source=str(record.get("source", "")),
               polygon_board_mm=[[round(x, 4), round(y, 4)] for x, y in board_pts])
    return ["polygon", ["pts", *[["xy", _mm(x), _mm(y)] for x, y in board_pts]]], fit


def _polygon_points(zone: list) -> list[list[float]]:
    """The first outline of a zone, for the evidence."""
    pts = K.child(K.child(zone, "polygon") or [], "pts") or []
    return [[float(p[1]), float(p[2])] for p in K.children(pts, "xy")]


def _zone_holes(zone: list) -> list[list[list[float]]]:
    """A zone's cutouts, for the evidence: each ``polygon`` after its first.
    KiCad saves a zone's outline as its first polygon and each cutout as a
    further one, and reads them back so (``ZONE::AddPolygon``: the first an
    outline, the rest holes). Measured with pcbnew 9.0.9: a rule area with one
    cutout saves as two polygons."""
    return [[[float(p[1]), float(p[2])] for p in K.children(K.child(poly, "pts") or [], "xy")]
            for poly in K.children(zone, "polygon")[1:]]


def _antenna_zone(ref: str, n: int, copper: list[str], polygons: list) -> list:
    """The rule area this gate adds for ``ref`` over one source keep-out: every
    copper layer, all the copper KiCad's keep-out judges forbidden
    (:data:`ANTENNA_FORBIDS`), named for the part, its id a ``uuid5`` so the
    bytes repeat. ``polygons`` are the source's own, in its order (the outline,
    then each cutout), so KiCad reads the added area as it reads the source: a
    cutout stays a cutout."""
    name = f"{ANTENNA_AREA}{ref}"
    return ["zone", ["net", "0"], ["net_name", K.Q("")], ["layers", *[K.Q(c) for c in copper]],
            ["uuid", K.Q(str(uuid.uuid5(uuid.NAMESPACE_URL, f"nopekit:{name}:{n}")))],
            ["name", K.Q(name)], ["hatch", "edge", "0.5"],
            ["connect_pads", ["clearance", "0"]], ["min_thickness", "0.25"],
            ["filled_areas_thickness", "no"],
            ["keepout", *[[k, v] for k, v in ANTENNA_FORBIDS]],
            ["placement", ["enabled", "no"], ["sheetname", K.Q("")]],
            ["fill", ["thermal_gap", "0.5"], ["thermal_bridge_width", "0.5"]],
            *polygons]


def _without_keepouts(tree: list) -> list:
    """A copy of ``tree`` with every keep-out rule area taken out, the board's
    and each footprint's. KiCad's DRC reports an item once, against the first
    area it falls in (measured with kicad-cli 9.0.9: a track on F.Cu in the stock
    ESP32-S3-WROOM-1's own area and in this gate's read "Items not allowed
    (keepout area)", naming no area, and the gate's count read 0). With the others
    gone, each item in an added area is reported against it. The others are rule
    areas, no copper, and ``pcb.drc`` judges them on the board as it is."""
    out = K.parse(K.dump(tree))

    def strip(node):
        node[:] = [c for c in node if not (isinstance(c, list) and K.head(c) == "zone"
                                           and _keepout_flags(c) is not None)]
    strip(out)
    for fp in K.children(out, "footprint"):
        strip(fp)
    return out


def _fills_judged(tree: list) -> None:
    """Each copper zone of ``tree`` (a staged copy) given an outline that holds
    its stored fill: the box around its outline and its fill, 1 mm out.

    KiCad's DRC judges a zone in a keep-out by its fill, but only where its
    outline meets the area. A fill the file still holds outside the zone's
    outline (an outline drawn back from the antenna, not refilled since) is not
    judged at all (measured with kicad-cli 9.0.9: such a fill on In2.Cu under
    the antenna read nothing; with the outline grown, it read "Zone [GND] on
    In2.Cu"). The fill is what a fab plots from the file, so it is held here as
    stored; a grown outline on a zone whose fill stays clear reads nothing (the
    same measurement: three pours grown into the area, their fills clear of it).
    Only the stored copy is changed: a refill puts each fill inside its outline."""
    zones = list(K.children(tree, "zone"))
    for fp in K.children(tree, "footprint"):
        zones += K.children(fp, "zone")
    for zone in zones:
        if _keepout_flags(zone) is not None or not K.children(zone, "filled_polygon"):
            continue
        pts = [(float(p[1]), float(p[2]))
               for node in K.children(zone, "polygon") + K.children(zone, "filled_polygon")
               for p in K.children(K.child(node, "pts") or [], "xy")]
        x0, y0 = min(x for x, _ in pts) - 1.0, min(y for _, y in pts) - 1.0
        x1, y1 = max(x for x, _ in pts) + 1.0, max(y for _, y in pts) + 1.0
        box = ["polygon", ["pts", *[["xy", _mm(x), _mm(y)]
                                    for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1))]]]
        first = next(i for i, c in enumerate(zone)
                     if isinstance(c, list) and K.head(c) == "polygon")
        zone[:] = [c for i, c in enumerate(zone)
                   if not (isinstance(c, list) and K.head(c) == "polygon") or i == first]
        zone[first] = box


def _judging_project(path: str) -> None:
    """The staged project file, set so a project cannot hide what the added areas
    find: ``items_not_allowed`` at error, and no DRC exclusions (an exclusion is
    the project's waiver of a finding ``pcb.drc`` made, never of this one). No
    project file is KiCad's defaults, which are the same."""
    if not os.path.isfile(path):
        return
    with open(path, encoding="utf-8") as fh:
        doc = json.load(fh)
    settings = doc.setdefault("board", {}).setdefault("design_settings", {})
    settings.setdefault("rule_severities", {})["items_not_allowed"] = "error"
    settings["drc_exclusions"] = []
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2, sort_keys=True)


def _refill(ctx: GateContext, gid: str, board: str, out: str, log: str):
    """``_keepout_fill.py`` on the staged ``board``, writing ``out``: the ids of
    the board's holes with no copper (``bare_holes``, as KiCad's pcbnew decides
    them), or a skip/error Verdict. A timeout is a skip (nothing was shown); a
    crash, a refill that did not finish or a run that wrote nothing is an error.
    A report with no list names no hole, so every item counts: never quieter."""
    timeout = float(ctx.param("pcb_drc_timeout_s", DEFAULT_DRC_TIMEOUT_S) or
                    DEFAULT_DRC_TIMEOUT_S)
    try:
        proc = subprocess.run([sys.executable, KEEPOUT_FILL_HELPER, board, out],
                              capture_output=True, text=True, timeout=timeout,
                              stdin=subprocess.DEVNULL, cwd=os.path.dirname(out))
    except subprocess.TimeoutExpired:
        return _skip(gid, f"the zone refill exceeded {timeout:g} s (pcb_drc_timeout_s) and was "
                          f"killed: nothing was shown")
    with open(log, "w", encoding="utf-8") as fh:
        fh.write(f"_keepout_fill.py exit {proc.returncode}\n{proc.stdout}\n{proc.stderr}\n")
    lines = [ln for ln in proc.stdout.splitlines() if ln.startswith("{")]
    if proc.returncode != 0 or not lines or not os.path.isfile(out):
        tail = (proc.stderr or proc.stdout).strip().splitlines()[-1:] or ["no output"]
        return Verdict(gate=gid, passed=False, evidence=[log],
                       error=f"the zone refill (KiCad's pcbnew, in a child) failed (exit "
                             f"{proc.returncode}): {tail[0][:200]}")
    report = json.loads(lines[-1])
    if not report.get("filled"):
        return Verdict(gate=gid, passed=False, evidence=[log],
                       error="the zone refill (KiCad's pcbnew) did not finish: no refilled "
                             "copy to judge")
    bare = report.get("bare_holes")
    return {u for u in bare if isinstance(u, str) and u} if isinstance(bare, list) else set()


def _antenna_findings(doc: dict, owners: dict[str, str],
                      bare: frozenset | set = frozenset()) -> tuple[list, list, list]:
    """``(findings, own items, bare holes)`` of one DRC report: each item KiCad
    reports in an added area, as ``((uuid, words), ref, words)``. An item of the
    area's own part (its pads: "Pad 1 [GND] of U1") is the part's own copper, and
    is listed apart, never counted. So is an item whose id is in ``bare``, a hole
    KiCad gives no copper (``_keepout_fill.py``): the added area forbids pads, so
    KiCad reports another part's mounting hole ("NPTH pad of H1"), and a hole
    holds no copper. What goes in it, a metal screw, is ``lenses.md``'s. (What
    slipped through, review of P8: it counted, and a stock M2 hole for a plastic
    standoff under the antenna failed a sound board. Rejected: counting it on
    purpose, since the rule is copper; matching KiCad's words, which do not say
    whether the pad has a ring.)"""
    found, own, holes = [], [], []
    for v in doc.get("violations", []):
        if v.get("type") != "items_not_allowed":
            continue
        named = re.search(r"keepout area '([^']*)'", str(v.get("description", "")))
        if not named or named.group(1) not in owners:
            continue
        ref = owners[named.group(1)]
        for item in v.get("items", []):
            words = str(item.get("description", ""))
            row = ((str(item.get("uuid", "")), words), ref, words)
            if re.search(rf"(?<!\w)of {re.escape(ref)}(?!\w)", words):
                own.append(row)
            elif row[0][0] in bare:
                holes.append(row)
            else:
                found.append(row)
    return found, own, holes


def _antenna_runs(ctx: GateContext, gid: str, n: int, path: str, tree: list,
                  copper: list[str], areas: dict[str, list]):
    """Judge one board: KiCad's DRC on two staged copies, each with every other
    keep-out taken out and the added areas in: the board as stored, and the board
    after KiCad's own refill (run on the staged board with its project and rules
    beside it, before any area is added: an area present during the refill knocks
    the pour out, and DRC then sees nothing, measured with pcbnew 9.0.9).
    ``({key: finding}, [own items], [bare holes], evidence)`` or a skip/error
    Verdict."""
    staged = _stage(ctx, f"{gid}.{n}", path, board=True, schematic=False)
    if isinstance(staged, Verdict):
        return staged
    stem, stage = staged
    name = os.path.basename(stem) + ".kicad_pcb"
    # Each copy takes the project file and the library tables. Not the custom
    # rules: they shape the refill, run on the staged board beside them, but a
    # rule's severity must not reach this DRC.
    side = [f for f in sorted(os.listdir(stage)) if f != name and not f.endswith(".kicad_dru")]
    refilled = os.path.join(stage, "refilled", name)
    os.makedirs(os.path.dirname(refilled), exist_ok=True)
    log = os.path.join(stage, "refill.log")
    bare = _refill(ctx, gid, os.path.join(stage, name), refilled, log)
    if isinstance(bare, Verdict):
        return bare
    owners = {f"{ANTENNA_AREA}{ref}": ref for ref in areas}
    found: dict = {}
    own: dict = {}
    holes: dict = {}
    ev = [log]
    # pcbnew wrote a project file and local settings beside its copy: the
    # copies take the staged project, set by _judging_project, never those.
    after = K.load_tree(refilled)
    for f in os.listdir(os.path.dirname(refilled)):
        os.remove(os.path.join(os.path.dirname(refilled), f))
    for copy, source in (("stored", tree), ("refilled", after)):
        where = os.path.join(stage, copy)
        os.makedirs(where, exist_ok=True)
        judged = _without_keepouts(source)
        if copy == "stored":
            _fills_judged(judged)
        for ref, zones in sorted(areas.items()):
            judged.extend(_antenna_zone(ref, i, copper, polys) for i, polys in enumerate(zones))
        with open(os.path.join(where, name), "w", encoding="utf-8") as fh:
            fh.write(K.dump(judged) + "\n")
        for f in side:
            shutil.copyfile(os.path.join(stage, f), os.path.join(where, f))
        _judging_project(os.path.join(where, os.path.basename(stem) + ".kicad_pro"))
        run = _kicad_cli(ctx, gid, where, ["pcb", "drc", "--severity-all",
                                           os.path.join(where, name)], "drc.json")
        if isinstance(run, Verdict):
            return run
        code, doc, run_ev = run
        ev += run_ev
        everything = _found(doc)
        if (code == EXIT_CLEAN) != (not everything):
            return Verdict(gate=gid, passed=False, evidence=ev,
                           error=f"kicad-cli exited {code} on the {copy} copy but its report "
                                 f"lists {len(everything)} item(s): the two disagree")
        hits, mine, open_holes = _antenna_findings(doc, owners, bare)
        for key, ref, words in hits:
            found.setdefault(key, {"ref": ref, "item": words, "copies": []})["copies"].append(copy)
        for key, ref, words in mine:
            own.setdefault(key, {"ref": ref, "item": words})
        for key, ref, words in open_holes:
            holes.setdefault(key, {"ref": ref, "item": words})
    return found, list(own.values()), list(holes.values()), ev


@gate(
    id="pcb.antenna_keepout",
    # sourced: the rule (no copper on any layer under a module's antenna) is the
    #    module vendors' integration guidance (references/antenna-keepout.md), the
    #    keep-out is the footprint's own or the vendor's drawing, and KiCad's DRC
    #    and zone filler decide what lies in it, as pcb.fab_rules hands KiCad a
    #    fab's numbers. The area selection and a drawing's registration are this
    #    pack's own glue.
    basis="sourced",
    # The title and the pass name what KiCad's keep-out reports, never "copper":
    # it does not report a copper drawing or text (measured with kicad-cli
    # 9.0.9), so a filled gr_poly on In2.Cu under the antenna passes. What
    # slipped through (review of P8): the title read "No copper on any layer" and
    # the pass "nothing on any copper layer" over that board.
    title=("No track, via, other part's pad or zone fill on any copper layer sits in a "
           "radio module's antenna keep-out, as the board is stored and as the next "
           "refill pours it"),
    claims=["pcb", "antenna-keepout", "rf-layout"],
    tier=Tier.BUILD,
    settles="copper in a radio module's antenna keep-out",
    requires_tools=["kicad-cli"],
    requires_python=["pcbnew"],
    negative_control=NegativeControl(
        fixture="selftest/bad_boards.py:pour_under_antenna",
        note="the radio board with its In2.Cu ground pour's outline widened to the board "
             "edge, under the module's antenna, and its stored fill left as it was: the "
             "footprint's own keep-out covers F.Cu, In1.Cu and B.Cu only, so kicad-cli "
             "pcb drc finds nothing in it (its findings are the baseline's, item for "
             "item), and the next refill pours ground on In2.Cu",
    ),
)
def antenna_keepout(ctx: GateContext) -> Verdict:
    """No track, via, other part's pad or zone fill on any copper layer in a
    radio module's antenna keep-out, as the board is stored and as KiCad's next
    refill pours them. KiCad's DRC decides each item, on a staged copy with a
    rule area this gate adds over the keep-out on every copper layer. Not judged:
    a copper drawing or text (KiCad's keep-out does not report one), and the pass
    says so. A hole KiCad gives no copper is listed, never counted.

    **Which parts.** Every footprint from KiCad's ``RF_Module`` library
    (:data:`ANTENNA_LIBRARIES`) on ``pcb_board`` or on a board a record names,
    and every part a ``pcb_antennas`` record names (``{id: {board?, ref, value,
    keepout, source}}``). A record is held to its board's ref and value, so a
    moved or swapped part stales it.

    **Which keep-out.** The footprint's own rule area (:func:`_footprint_areas`),
    a vendor drawing registered on the footprint by its pads
    (:func:`_drawing_area`), or a rule area the designer drew and named. A found
    module whose footprint carries none, with no record, skips by name, never
    passes: 26 of KiCad 9.0.9's 57 RF modules carry none, and three carry only
    an area that allows tracks or copper pour.

    What slipped through: ``pcb.drc`` holds a footprint's keep-out only on the
    layers the footprint names, and in KiCad 9.0.9 ten of the 31 RF modules with
    one name fewer than every copper layer (the stock ESP32-S3-WROOM-1 and
    ESP32-WROOM-32: F.Cu, In1.Cu and B.Cu), so a ground pour on In2.Cu runs under
    the antenna with DRC clean. And kicad-cli judges a pour by its stored fill: a
    pour moved under the antenna and not refilled reads clean, and the next refill
    puts the copper there. The source pipeline held its module's keep-out with a
    script written for that one board; nothing in this pack checked it.

    The verdict is the count of items found, each counted once over both copies,
    against 0. A fail counts beside a part not judged; a part not judged beside
    no fail is a skip.
    """
    gid = "pcb.antenna_keepout"
    try:
        raw = P.records(ctx, "pcb_antennas") or {}
        records = [_antenna_record(rid, rec) for rid, rec in sorted(raw.items())]
    except P.RecordError as exc:
        return Verdict(gate=gid, passed=False, error=str(exc))
    # Keyed by the file, however each path is spelled: one board named twice
    # (pcb_board relative, a record's absolute) is judged once, never counted twice.
    boards: dict[str, list] = {}
    if ctx.param("pcb_board"):
        boards[os.path.realpath(_board_path(ctx))] = []
    for rec in records:
        rel = rec["board"] or ctx.param("pcb_board")
        if not rel:
            return Verdict(gate=gid, passed=False,
                           error=f"{rec['label']} names no board, and there is no pcb_board")
        boards.setdefault(os.path.realpath(K.project_path(ctx, rel)), []).append(rec)
    if not boards:
        return _skip(gid, "no pcb_board and no pcb_antennas: the board a radio module sits "
                          "on is the project's to name")
    judged, listed, unjudged, unkept, plans = [], [], [], {}, []
    try:
        for n, (path, recs) in enumerate(boards.items()):
            bname = os.path.basename(path)
            try:
                tree, board = _antenna_board(ctx, path, recs[0]["label"] if recs else "pcb_board")
            except P.ReaderLimit as exc:
                unjudged.append(str(exc))
                continue
            copper = _copper_layers(tree)
            fps = {K.footprint_ref(f)[0]: f for f in K.children(tree, "footprint")}
            models = {f.ref: f for f in board.footprints}
            by_ref: dict[str, dict | None] = {
                f.ref: None for f in board.footprints
                if f.lib_id.split(":", 1)[0] in ANTENNA_LIBRARIES}
            for rec in recs:
                fp = models.get(rec["ref"])
                if fp is None:
                    raise P.RecordError(f"{rec['label']} is for another design: {bname} has no "
                                        f"{rec['ref']}")
                if fp.value != rec["value"]:
                    raise P.RecordError(f"{rec['label']} is for another design: {bname}'s "
                                        f"{rec['ref']} is {fp.value}, the record's {rec['value']}")
                if by_ref.get(rec["ref"]) is not None:
                    raise P.RecordError(f"{rec['label']} and {by_ref[rec['ref']]['label']} both "
                                        f"name {rec['ref']} on {bname}: state each part once")
                by_ref[rec["ref"]] = rec
            areas: dict[str, list] = {}
            for ref, rec in sorted(by_ref.items()):
                fp = models[ref]
                kind, where = rec["keepout"] if rec else ("footprint", "")
                if not fp.fitted:
                    listed.append({"board": bname, "ref": ref, "why": P.not_fitted(fp),
                                   "short": "not fitted"})
                    continue
                if kind == "none":
                    listed.append({"board": bname, "ref": ref, "why": rec["source"],
                                   "short": "no antenna on the board"})
                    continue
                if kind == "drawing":
                    poly, fit = _drawing_area(ctx, tree, fps[ref], ref, where, rec["label"])
                    areas[ref] = [[poly]]
                    judged.append({"board": bname, "ref": ref, "value": fp.value,
                                   "keepout": f"the drawing {where}, registered on "
                                              f"{len(fit['pairs'])} pads "
                                              f"(RMS {fit['rms_mm']:g} mm)",
                                   "short": f"drawing, RMS {fit['rms_mm']:g} mm",
                                   "registration": fit,
                                   "polygons_board_mm": [fit["polygon_board_mm"]],
                                   "holes_board_mm": [[]]})
                    continue
                if kind == "rule_area":
                    zones = [z for z in K.children(tree, "zone")
                             if _keepout_flags(z) is not None
                             and str(K.value(z, "name", default="")) == where]
                    if not zones:
                        raise P.RecordError(f"{rec['label']}: {bname} has no rule area named "
                                            f"{where!r}")
                    what, short = f"the board's rule area {where!r}", f"rule area {where!r}"
                else:
                    zones = _footprint_areas(fps[ref], copper)
                    what, short = "its footprint's rule area", "footprint area"
                    if not zones and rec is not None:
                        raise P.RecordError(f"{rec['label']}: {ref}'s footprint ({fp.lib_id}) "
                                            f"carries no keep-out rule area that forbids tracks "
                                            f"and copper pour: give the vendor's drawing")
                    if not zones:
                        unkept.setdefault(bname, []).append(f"{ref} ({fp.lib_id})")
                        continue
                # One added area per source zone, with all of its polygons: the
                # outline first, then each cutout, as KiCad wrote them. What
                # slipped through (review of P8): each polygon was made an area
                # of its own, so a cutout read as solid keep-out, and the feed
                # track a designer cut out of a chip antenna's clearance failed
                # a sound board (measured with KiCad 9.0.9: plain DRC clean, the
                # gate 1 item). The evidence showed the outline only.
                areas[ref] = [[K.parse(K.dump(p)) for p in K.children(z, "polygon")]
                              for z in zones]
                cutouts = [_zone_holes(z) for z in zones]
                cut = sum(len(h) for h in cutouts)
                judged.append({"board": bname, "ref": ref, "value": fp.value,
                               "keepout": what + (f" ({len(zones)} areas)"
                                                  if len(zones) > 1 else ""),
                               "short": short + (f", {cut} cutout{'s' if cut > 1 else ''}"
                                                 if cut else ""),
                               "polygons_board_mm": [_polygon_points(z) for z in zones],
                               "holes_board_mm": cutouts})
            if areas:
                plans.append((n, path, bname, tree, copper, areas))
    except P.RecordError as exc:
        return Verdict(gate=gid, passed=False, error=str(exc))
    findings: list[dict] = []
    own: list[dict] = []
    holes: list[dict] = []
    ev: list[str] = []
    errors: list[Verdict] = []
    layers: dict[str, list[str]] = {}
    for n, path, bname, tree, copper, areas in plans:
        run = _antenna_runs(ctx, gid, n, path, tree, copper, areas)
        # A board KiCad could not judge (a timeout, a crash) leaves the others'
        # findings standing: a skip or an error beside a fail would hide the
        # fail (the power rules' lesson, P5, and the fit's, P7).
        if isinstance(run, Verdict):
            if run.skipped:
                unjudged.append(f"{bname}: {run.skip_reason}")
            else:
                errors.append(run)
            continue
        found, mine, bare, run_ev = run
        ev += run_ev
        layers[bname] = copper
        findings += [{"board": bname, **row} for _key, row in sorted(
            found.items(), key=lambda kv: (kv[1]["ref"], kv[1]["item"], kv[0]))]
        own += [{"board": bname, **row} for row in mine]
        holes += [{"board": bname, **row} for row in bare]
    # A part with no keep-out is said once per board, with the remedy once, its
    # refs first: a skip reason is cut at 200 characters and a detail line at
    # 500, and a reason that names its parts past the cut names none of them.
    for bname, refs in unkept.items():
        unjudged.append(f"no antenna keep-out for {', '.join(refs)} on {bname}: the "
                        f"footprint has none, and no pcb_antennas record gives a drawing (or "
                        f"\"none\", for an antenna off the board)")
    ev = _evidence(ctx, "antenna_keepout", {
        "judged": judged, "findings": findings, "own_items_left_out": own,
        "holes_no_copper_left_out": holes, "listed": listed, "not_judged": unjudged,
        "copper_layers": layers}) + ev
    on_board: dict[str, list[str]] = {}
    for j in judged:
        on_board.setdefault(j["board"], []).append(f"{j['ref']} ({j['short']})")
    parts = "; ".join(f"{', '.join(refs)} on {b}, {len(layers.get(b, []))} copper layers"
                      for b, refs in on_board.items())
    notes = []
    if own:
        notes.append(f"the part's own pads in its keep-out, left out: {len(own)}")
    if holes:
        named = ", ".join(f"{h['item']} ({h['ref']})" for h in holes[:3])
        more = f" and {len(holes) - 3} more" if len(holes) > 3 else ""
        notes.append(f"holes with no copper in a keep-out, not counted: {named}{more} (a metal "
                     f"screw in one is lenses.md's)")
    if listed:
        notes.append("listed, not judged: " + ", ".join(f"{r['ref']} ({r['short']})"
                                                        for r in listed))
    if findings:
        def where(row):
            copies = row["copies"]
            return ("stored and refilled" if len(copies) == 2 else
                    "stored only; a refill takes it out" if copies == ["stored"] else
                    "refilled only; the stored fill holds none")
        head = [f"{len(findings)} item(s) in an antenna's keep-out"]
        if unjudged:
            head.append("not judged: " + "; ".join(unjudged))
        items = "; ".join(f"{r['item']} ({r['ref']}, {where(r)})" for r in findings[:3])
        if len(findings) > 3:
            items += f"; and {len(findings) - 3} more"
        if errors:
            head.append("not judged: " + "; ".join(e.error or e.detail for e in errors))
        return Verdict(gate=gid, passed=False, measured=len(findings), limit=0, comparator="<=",
                       units="findings", evidence=ev,
                       detail="; ".join([*head, items, f"judged {parts}", *notes]))
    if errors:
        return Verdict(gate=gid, passed=False,
                       evidence=ev + [e for v in errors for e in v.evidence],
                       error="; ".join(v.error or v.detail for v in errors))
    if unjudged:
        return _skip(gid, "; ".join(unjudged + ([f"judged, nothing found: {parts}"]
                                                 if judged else [])))
    if not judged and listed:
        return _skip(gid, f"no antenna keep-out to judge: {notes[-1]}")
    if not judged:
        return _skip(gid, "no radio module on "
                          + ", ".join(os.path.basename(p) for p in boards)
                          + ": no footprint from " + ", ".join(ANTENNA_LIBRARIES)
                          + " and no pcb_antennas record")
    detail = (f"no track, via, other part's pad or zone fill on any copper layer in an antenna's "
              f"keep-out, as stored and as refilled (copper drawings and text not judged): "
              f"{parts}")
    return Verdict(gate=gid, passed=True, measured=0, limit=0, comparator="<=",
                   units="findings", detail="; ".join([detail, *notes]), evidence=ev)
