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
vendor's guidance), each on a copy staged under the gate's out directory.
``pcb.drc`` and ``pcb.fab_rules`` judge a board with a zone as stored and as
KiCad refills it, each finding once (``REFILL_DIR``). They
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
            # The residual rounded up against its limit, and the unrounded ratio
            # the gate decides on (``_kicad.toward_fail``): both were rounded to
            # the nearest 0.1 um and then compared, so a residual a hair past its
            # limit read equal to it and passed (holdout round 2 found the
            # pattern in pcb.net_width).
            "worst": {"function": meta[worst][0], "board_pad": meta[worst][1],
                      "drawing_pad": meta[worst][2],
                      "residual_mm": K.toward_fail(res[worst], "<=", meta[worst][3], 4),
                      "limit_mm": round(meta[worst][3], 4),
                      "of_limit": res[worst] / meta[worst][3]},
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
    worst = max(fits, key=lambda f: f["worst"]["of_limit"])
    w = worst["worst"]
    ev = _evidence(ctx, "land_pattern", {"fits": fits, "notes": notes})
    passed = w["of_limit"] <= 1.0
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
#: The keys of a net's current stated per path in ``pcb_net_currents_a``:
#: ``{"amps": A, "from": "J1.1", "to": "U5.38", "rest_a": R}``. ``amps`` is judged
#: on every track that lies on a route of the net's copper between the two pads
#: (``REF.PAD``), and ``rest_a`` (default ``amps``) on every other track of the
#: net. What slipped through (a holdout run): one current judged every track of
#: a net, so a return net named at a cell's 1 A failed on the 0.2 mm stubs that
#: fan a QFN's ground pins out to their vias (19.5 C), which carry the
#: amplifier's 0.29 A and never the cell's. *Rejected:* a current per track
#: width or per layer (a person's word that nothing holds to the copper: a
#: thin track on the path would be judged at the thin class's current); the
#: shortest route alone (current shares every parallel route, so a second
#: route would be judged at the rest); judging only what the path covers and
#: saying nothing of the rest (the rest of the net carries current too).
NET_PATH_KEYS = ("amps", "from", "to", "rest_a")


def _net_current(net: str, raw) -> dict:
    """One ``pcb_net_currents_a`` entry: ``{amps, rest, path}``; raises
    ``P.RecordError`` for an entry this gate cannot read."""
    label = f"pcb_net_currents_a[{net!r}]"
    if not isinstance(raw, dict):
        amps = P.num(raw, label, gt=0.0)
        return {"amps": amps, "rest": amps, "path": None}
    P.keys(raw, label, ("amps", "from", "to"), ("rest_a",))
    amps = P.num(raw["amps"], f"{label}.amps", gt=0.0)
    rest = P.num(raw.get("rest_a", amps), f"{label}.rest_a", ge=0.0)
    if rest > amps:
        # A route is held to the net's copper by over-reading it (a pad's
        # circle, a pour's outline): a track wrongly read onto the route is
        # judged at the higher current, never the lower, only while the rest is
        # no more than the route's.
        raise P.RecordError(f"{label}.rest_a {rest:g} A is above its amps {amps:g} A: the "
                            f"route from {raw['from']} to {raw['to']} carries the net's "
                            f"most current")
    ends = []
    for key in ("from", "to"):
        text = P.text(raw[key], f"{label}.{key}")
        ref, dot, pad = text.partition(".")
        if not dot or not ref or not pad:
            raise P.RecordError(f"{label}.{key} is {text!r}: name a pad as REF.PAD (J1.1)")
        ends.append((ref, pad))
    if ends[0] == ends[1]:
        raise P.RecordError(f"{label}: from and to are one pad")
    return {"amps": amps, "rest": rest, "path": tuple(ends)}


def _seg_point(p, a, b) -> float:
    """The distance from point ``p`` to the segment ``a``-``b``."""
    (px, py), (ax, ay), (bx, by) = p, a, b
    dx, dy = bx - ax, by - ay
    span = dx * dx + dy * dy
    t = 0.0 if span == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / span))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def _seg_seg(a, b, c, d) -> float:
    """The distance between the segments ``a``-``b`` and ``c``-``d``."""
    def side(p, q, r):
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
    if (side(a, b, c) * side(a, b, d) < 0) and (side(c, d, a) * side(c, d, b) < 0):
        return 0.0
    return min(_seg_point(a, c, d), _seg_point(b, c, d), _seg_point(c, a, b),
               _seg_point(d, a, b))


def _inside(p, poly) -> bool:
    """Whether point ``p`` lies inside the polygon ``poly`` (even-odd)."""
    x, y = p
    hit = False
    for (x0, y0), (x1, y1) in zip(poly, poly[1:] + poly[:1]):
        if (y0 > y) != (y1 > y) and x < x0 + (y - y0) * (x1 - x0) / (y1 - y0):
            hit = not hit
    return hit


def _layers_of(names, copper: list[str]) -> set[str]:
    out: set[str] = set()
    for name in (str(n) for n in names):
        if name == "*.Cu":
            out |= set(copper)
        elif name == "F&B.Cu":
            out |= {"F.Cu", "B.Cu"}
        elif name in copper:
            out.add(name)
    return out


def _project(p, a, b) -> float:
    """The parameter, 0 to 1, of the point of segment ``a``-``b`` nearest ``p``."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    span = dx * dx + dy * dy
    return 0.0 if span == 0 else max(0.0, min(1.0, ((p[0] - a[0]) * dx
                                                     + (p[1] - a[1]) * dy) / span))


def _net_copper(tree: list, board, net: str) -> tuple[dict, dict, dict]:
    """The copper of ``net`` as a graph: ``(adjacency {node: [(node, edge)]},
    {edge: the board's track index}, {(ref, pad number): [pad nodes]})``.

    A track (an arc as its two chords, each widened by its sagitta) is a run of
    edges, one per stretch between the places something touches it, each edge
    labelled with the track. A via, a pad and a pour are each a node, and every
    contact is an edge of its own, with no label: two tracks whose copper
    overlaps, a track end on another's body (a T, which splits the through
    track there), a track on a via, a pad or a pour, a via or a pad in a pour.
    Nothing is merged: a track lying over a pour stays an edge beside the pour,
    on a loop with it, never a loop of one.

    Read generously on purpose: a pad is the circle round its corners (and its
    custom shape's), a pour its outline, not its fill. An edge the copper does
    not make can only add routes, never take one away (a route through the
    copper is still a route in a graph with more edges), so a track read onto a
    route wrongly is judged at the route's current, the higher, never the lower.
    A contact the copper makes and this misses could take a track off; the
    circle holds every pad shape KiCad draws from its size."""
    copper = _copper_layers(tree) or ["F.Cu", "B.Cu"]
    order = {name: i for i, name in enumerate(copper)}
    nodes: list[dict] = []
    adj: dict[int, list] = {}
    label: dict[int, int] = {}
    count = [0]

    def node() -> int:
        adj[len(adj)] = []
        return len(adj) - 1

    def edge(a: int, b: int, track: int | None = None) -> None:
        e = count[0]
        count[0] += 1
        adj[a].append((b, e))
        adj[b].append((a, e))
        if track is not None:
            label[e] = track

    # Track pieces: their ends are nodes, and each keeps the stations the
    # contacts make along it.
    pieces: list[dict] = []
    items = list(K.children(tree, "segment")) + list(K.children(tree, "arc"))
    for i, (item, t) in enumerate(zip(items, board.tracks)):
        if t.net != net:
            continue
        start = (float(K.child(item, "start")[1]), float(K.child(item, "start")[2]))
        end = (float(K.child(item, "end")[1]), float(K.child(item, "end")[2]))
        half = t.width_mm / 2.0
        points = [start, end]
        mid = K.child(item, "mid")
        if K.head(item) == "arc" and mid is not None:
            m = (float(mid[1]), float(mid[2]))
            points = [start, m, end]
            a, b, c = math.dist(start, m), math.dist(m, end), math.dist(start, end)
            area2 = abs((m[0] - start[0]) * (end[1] - start[1])
                        - (end[0] - start[0]) * (m[1] - start[1]))
            if area2 > 1e-12:
                r = a * b * c / (2.0 * area2)
                half += max(r - math.sqrt(max(r * r - (x / 2.0) ** 2, 0.0)) for x in (a, b))
        ends = [node() for _ in points]
        for (pa, pb), (na, nb) in zip(zip(points, points[1:]), zip(ends, ends[1:])):
            pieces.append({"track": i, "layer": t.layer, "a": pa, "b": pb, "half": half,
                           "stations": [(0.0, na), (1.0, nb)]})
    for via in K.children(tree, "via"):
        if K._net_name(K.child(via, "net"), board.nets) != net:
            continue
        at = K.child(via, "at")
        span = [str(x) for x in (K.child(via, "layers") or [])[1:]]
        idx = sorted(order[n] for n in span if n in order) or [0, len(copper) - 1]
        nodes.append({"kind": "via", "id": node(), "layers": set(copper[idx[0]:idx[-1] + 1]),
                      "at": (float(at[1]), float(at[2])),
                      "r": float(K.value(via, "size", default=0.0)) / 2.0})
    pads: dict[tuple, list[int]] = {}
    for fp in K.children(tree, "footprint"):
        ref = K.footprint_ref(fp)[0]
        place = _footprint_to_board_raw(fp)
        for pad in K.children(fp, "pad"):
            if K._net_name(K.child(pad, "net"), board.nets) != net:
                continue
            at, size = K.child(pad, "at"), K.child(pad, "size")
            w, h = (float(size[1]), float(size[2])) if size is not None else (0.0, 0.0)
            r = math.hypot(w, h) / 2.0
            for prim in K.children(K.child(pad, "primitives") or [], "gr_poly"):
                for pt in K.children(K.child(prim, "pts") or [], "xy"):
                    r = max(r, math.hypot(float(pt[1]), float(pt[2])))
            n = node()
            pads.setdefault((ref, str(pad[1])), []).append(n)
            nodes.append({"kind": "pad", "id": n,
                          "layers": _layers_of((K.child(pad, "layers") or [])[1:], copper),
                          "at": place((float(at[1]), float(at[2]))), "r": r})
    for zone in K.children(tree, "zone"):
        if K.child(zone, "keepout") is not None:
            continue
        name = K._net_name(K.child(zone, "net"), board.nets) or str(
            K.value(zone, "net_name", default=""))
        outline = [tuple(p) for p in _polygon_points(zone)]
        if name == net and len(outline) >= 3:
            nodes.append({"kind": "pour", "id": node(), "layers": _zone_layers(zone, copper),
                          "poly": outline,
                          "edges": list(zip(outline, outline[1:] + outline[:1]))})

    def station(pc: dict, t: float) -> int:
        """The node at parameter ``t`` along a piece: its end at 0 or 1, else a
        new node, so the piece is split there."""
        if t <= 0.0:
            return pc["stations"][0][1]
        if t >= 1.0:
            return pc["stations"][1][1]
        n = node()
        pc["stations"].append((t, n))
        return n

    def box(pts, grow) -> tuple:
        return (min(p[0] for p in pts) - grow, min(p[1] for p in pts) - grow,
                max(p[0] for p in pts) + grow, max(p[1] for p in pts) + grow)

    def apart(p, q) -> bool:
        return p[0] > q[2] or q[0] > p[2] or p[1] > q[3] or q[1] > p[3]

    for pc in pieces:
        pc["box"] = box([pc["a"], pc["b"]], pc["half"])
    for n in nodes:
        n["box"] = box(n["poly"], 0.0) if n["kind"] == "pour" else box([n["at"]], n["r"])
    # Track against track.
    for i, p in enumerate(pieces):
        for q in pieces[i + 1:]:
            if p["layer"] != q["layer"] or apart(p["box"], q["box"]):
                continue
            reach = p["half"] + q["half"]
            if _seg_seg(p["a"], p["b"], q["a"], q["b"]) > reach:
                continue
            touched = False
            for one, other in ((p, q), (q, p)):
                for k, end in ((0, one["a"]), (1, one["b"])):
                    if _seg_point(end, other["a"], other["b"]) <= reach:
                        edge(one["stations"][k][1],
                             station(other, _project(end, other["a"], other["b"])))
                        touched = True
            if not touched:             # they cross mid-length
                (ax, ay), (bx, by), (cx, cy), (dx, dy) = p["a"], p["b"], q["a"], q["b"]
                den = (bx - ax) * (dy - cy) - (by - ay) * (dx - cx)
                if den:
                    tp = ((cx - ax) * (dy - cy) - (cy - ay) * (dx - cx)) / den
                    tq = ((cx - ax) * (by - ay) - (cy - ay) * (bx - ax)) / den
                    edge(station(p, min(max(tp, 0.0), 1.0)), station(q, min(max(tq, 0.0), 1.0)))
    # Track against a via, a pad or a pour.
    for pc in pieces:
        for n in nodes:
            if pc["layer"] not in n["layers"] or apart(pc["box"], n["box"]):
                continue
            if n["kind"] != "pour":
                if _seg_point(n["at"], pc["a"], pc["b"]) <= pc["half"] + n["r"]:
                    edge(n["id"], station(pc, _project(n["at"], pc["a"], pc["b"])))
                continue
            inside = [k for k, end in ((0, pc["a"]), (1, pc["b"])) if _inside(end, n["poly"])]
            for k in inside:
                edge(n["id"], pc["stations"][k][1])
            if not inside and any(_seg_seg(pc["a"], pc["b"], *e) <= pc["half"]
                                  for e in n["edges"]):
                edge(n["id"], station(pc, 0.5))
    # Via, pad and pour against each other.
    for i, a in enumerate(nodes):
        for b in nodes[i + 1:]:
            if not a["layers"] & b["layers"] or apart(a["box"], b["box"]):
                continue
            if a["kind"] == "pour" and b["kind"] == "pour":
                hit = (any(_inside(p, a["poly"]) for p in b["poly"])
                       or any(_inside(p, b["poly"]) for p in a["poly"]))
            elif "pour" in (a["kind"], b["kind"]):
                z, o = (a, b) if a["kind"] == "pour" else (b, a)
                hit = _inside(o["at"], z["poly"]) or any(
                    _seg_point(o["at"], *e) <= o["r"] for e in z["edges"])
            else:
                hit = math.dist(a["at"], b["at"]) <= a["r"] + b["r"]
            if hit:
                edge(a["id"], b["id"])
    # Each piece, cut at its stations, is a run of edges labelled with its track.
    for pc in pieces:
        run = sorted(pc["stations"])
        for (_t0, n0), (_t1, n1) in zip(run, run[1:]):
            edge(n0, n1, pc["track"])
    return adj, label, pads


def _on_routes(adj: dict, s: int, t: int) -> set[int] | None:
    """The edges on some simple route from ``s`` to ``t``, or None when no
    route joins them: the edges of the biconnected blocks on the block-cut
    tree's path between the two (Hopcroft and Tarjan's blocks, found without
    recursion, edges told apart by id so two contacts between one pair of nodes
    are a loop). A stub off a route, or a loop that meets it at one node, is a
    block of its own off that path, and carries none of the route's current."""
    index: dict[int, int] = {}
    low: dict[int, int] = {}
    blocks: list[set[int]] = []
    ends: dict[int, tuple[int, int]] = {}
    stack_e: list[int] = []
    counter = 0
    for root in sorted(adj):
        if root in index:
            continue
        index[root] = low[root] = counter
        counter += 1
        stack = [(root, -1, iter(adj[root]))]
        while stack:
            v, via, it = stack[-1]
            advanced = False
            for w, e in it:
                if e == via:
                    continue
                if w not in index:
                    ends[e] = (v, w)
                    stack_e.append(e)
                    index[w] = low[w] = counter
                    counter += 1
                    stack.append((w, e, iter(adj[w])))
                    advanced = True
                    break
                if index[w] < index[v] and e not in ends:
                    ends[e] = (v, w)
                    stack_e.append(e)
                    low[v] = min(low[v], index[w])
            if advanced:
                continue
            stack.pop()
            if stack:
                parent = stack[-1][0]
                low[parent] = min(low[parent], low[v])
                if low[v] >= index[parent]:
                    block: set[int] = set()
                    while stack_e:
                        e = stack_e.pop()
                        block.add(e)
                        if e == via:
                            break
                    blocks.append(block)
    holding: dict[int, list[int]] = {}
    for k, block in enumerate(blocks):
        for v in {x for e in block for x in ends[e]}:
            holding.setdefault(v, []).append(k)
    if s not in holding or t not in holding:
        return None
    came: dict[int, int | None] = {k: None for k in holding[s]}
    queue = list(holding[s])
    goal = set(holding[t])
    found = None
    while queue:
        k = queue.pop(0)
        if k in goal:
            found = k
            break
        for v in {x for e in blocks[k] for x in ends[e]}:
            for nk in holding[v]:
                if nk not in came:
                    came[nk] = k
                    queue.append(nk)
    if found is None:
        return None
    out: set[int] = set()
    while found is not None:
        out |= blocks[found]
        found = came[found]
    return out


def _footprint_to_board_raw(fp: list):
    """A function from a footprint's item coordinates as the file stores them
    (a flipped footprint's y already negated) to the board's frame."""
    at = K.child(fp, "at")
    x0, y0 = float(at[1]), float(at[2])
    turn = math.radians(float(at[3])) if len(at) > 3 else 0.0
    c, s = math.cos(turn), math.sin(turn)
    return lambda p: (x0 + p[0] * c + p[1] * s, y0 - p[0] * s + p[1] * c)


def _route_tracks(tree: list, board, net: str, path: tuple) -> set[int]:
    """The board's indexes of ``net``'s tracks on a route between the two pads
    ``path`` names; raises ``P.RecordError`` when a pad is not on the net or no
    copper joins them."""
    adj, tracks, pads = _net_copper(tree, board, net)
    label = f"pcb_net_currents_a[{net!r}]"
    ends = []
    for ref, pad in path:
        got = pads.get((ref, pad))
        if not got:
            fp = next((f for f in board.footprints if f.ref == ref), None)
            why = ("the board has no such part" if fp is None else
                   f"it has no pad {pad}" if all(p.number != pad for p in fp.pads) else
                   f"its pad {pad} is on {next(p.net for p in fp.pads if p.number == pad) or 'no net'}")
            raise P.RecordError(f"{label} names {ref}.{pad}, not on {net}: {why}")
        # Pads of one number (a part's two pads 1) are one end.
        end = len(adj)
        adj[end] = []
        for n, g in enumerate(got):
            e = -1 - len(ends) * 1000 - n        # ids no contact or track uses
            adj[end].append((g, e))
            adj[g].append((end, e))
        ends.append(end)
    on = _on_routes(adj, *ends)
    if on is None:
        raise P.RecordError(f"{label}: no copper of {net} joins {path[0][0]}.{path[0][1]} to "
                            f"{path[1][0]}.{path[1][1]} (its tracks, vias, pads and pours "
                            f"read): name two pads the net's copper joins")
    return {tracks[e] for e in on if e in tracks}


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
    the worst track of any named net, decided on its computed rise and reported
    rounded up to 0.1 C, so the figure is never under the rise
    (``_kicad.toward_fail``).

    A net named with one current has every track judged at it: the board does
    not say which path is which. A net named with a route (:data:`NET_PATH_KEYS`)
    has its route's tracks judged at ``amps`` and every other track at
    ``rest_a``: the route is every track on some simple route of the net's copper
    (tracks, vias, pads, pours) between the two pads (:func:`_on_routes`), so a
    stub that fans a fine-pitch part out to its vias, or a loop that meets the
    route at one point, is judged at the rest. A net carried by a pour as well as
    by tracks is judged on its tracks, which is conservative. Vias, pads and
    pours are not judged at all (PACK.md, "cannot settle").
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
    try:
        stated = {net: _net_current(net, raw) for net, raw in sorted(currents.items())}
        tree = (ctx.load_file(path, loader=K.load_tree)
                if any(c["path"] for c in stated.values()) else None)
        routes = {net: _route_tracks(tree, board, net, c["path"])
                  for net, c in stated.items() if c["path"] and
                  any(t.net == net for t in board.tracks)}
    except P.RecordError as exc:
        return Verdict(gate=gid, passed=False, error=str(exc))

    rows, unrouted, split = [], [], {}
    for net, c in stated.items():
        tracks = [(i, t) for i, t in enumerate(board.tracks) if t.net == net]
        if not tracks:
            unrouted.append(net)
            continue
        on = routes.get(net)
        for i, t in tracks:
            amps = c["amps"] if on is None or i in on else c["rest"]
            if on is not None:
                split.setdefault(net, [0, 0])[0 if i in on else 1] += 1
            outer = t.layer in K.OUTER_LAYERS
            rise = K.temp_rise_c(float(amps), t.width_mm, oz_out if outer else oz_in, outer)
            rows.append((rise, net, float(amps), t.width_mm, t.layer, outer, t.length_mm,
                         t.shape))
    if not rows:
        return _skip(gid, f"none of {sorted(currents)} has a track on this board "
                          f"(carried by pours or pads alone?): nothing this gate can judge")
    rise, net, amps, width, _layer, outer, _length, _shape = max(rows)
    # Decided on the computed rise, reported rounded up to 0.1 C
    # (``_kicad.toward_fail``). What slipped through (holdout round 2): the rise
    # was rounded to the nearest 0.1 C and then compared, so a computed 10.0207 C
    # read 10.0 and passed against 10 C, and the claim read Checked on a value its
    # own condition rejects.
    passed = rise <= limit

    def shown(value: float) -> float:
        return K.toward_fail(value, "<=", limit, 1)

    reported = shown(rise)
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
        if shown(r[0]) == reported:
            stretches.setdefault((r[1], r[3], r[5], r[2]), []).append(r)
    first = (net, width, outer, amps)
    order = [first] + sorted(k for k in stretches if k != first)
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
        "routes": {n: {"from": ".".join(stated[n]["path"][0]),
                       "to": ".".join(stated[n]["path"][1]), "amps": stated[n]["amps"],
                       "rest_a": stated[n]["rest"], "tracks_on_route": k[0],
                       "tracks_off_route": k[1]} for n, k in sorted(split.items())},
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
    for n, (on_route, off_route) in sorted(split.items()):
        (a, z), c = stated[n]["path"], stated[n]
        detail += (f"; {n}: {on_route} track(s) on its route {'.'.join(a)} to {'.'.join(z)} "
                   f"at {c['amps']:g} A, {off_route} off it at {c['rest']:g} A")
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
#: whose KiCad was never configured. The libraries a table names inside the
#: project go too (:func:`_project_libraries`).
STEM_FILES = (".kicad_pro", ".kicad_dru")
DIR_FILES = ("sym-lib-table", "fp-lib-table")

#: The path variable a project's library table names its own libraries by: KiCad
#: sets it to the directory of the project file, so a stage that keeps the
#: libraries at their place relative to the board keeps every such entry. Both
#: spellings KiCad expands. Any other variable (``${KICAD9_FOOTPRINT_DIR}``, one a
#: person set) names a library of the host, which is not staged and not said.
#: Rejected: expanding a person's own variable from the environment (the verdict
#: would rest on a path the project does not hold, and the environment is an
#: opaque channel the trace cannot key).
PROJECT_VARS = ("${KIPRJMOD}", "$(KIPRJMOD)")

#: How many libraries a verdict names when the stage could not take them; past
#: it, how many more. Two keep the line one line on a project whose whole library
#: checkout is missing (16 libraries at once in the holdout run). Rejected: every
#: one (a line of 16 paths crowds out the count it sits beside); one (cannot tell
#: one slip from a missing checkout).
LIBRARIES_NAMED = 2


def _stem(path: str) -> str:
    for ext in (".kicad_pcb", ".kicad_sch", ".kicad_pro"):
        if path.endswith(ext):
            return path[:-len(ext)]
    return path


def _under(path: str, base: str) -> bool:
    try:
        return os.path.commonpath([path, base]) == base
    except ValueError:                       # two drives, or one path relative
        return False


def _project_libraries(ctx: GateContext, src_dir: str, tables) -> tuple[list[str], list[str]]:
    """``(library paths to stage, notes)``: every library the project's ``tables``
    (beside the board, in ``src_dir``) name through ``${KIPRJMOD}`` that is inside
    the project, and a note for each such entry the stage cannot take.

    Inside the project is under ``ctx.root`` when the board is, else under the
    board's own directory. A note says which entry, which table, and why: nothing
    at that path, or outside the project. KiCad then reports the library as not
    found (``lib_symbol_issues``, ``lib_footprint_issues``), and the note says it
    was the project's and not the stage's doing.

    What slipped through, in the holdout run on outside designs: the stage copied
    the two tables and none of the libraries they named, so every entry
    ``${KIPRJMOD}/library/...`` or ``${KIPRJMOD}/../kicad-library/...`` resolved
    against the stage, and ERC reported the project's own libraries "not found"
    on a design that shipped them. Rejected: rewriting each entry to the source's
    absolute path (kicad-cli would then read the project's libraries in place,
    where the stage keys nothing); staging a library outside the project (the
    verdict would rest on a file no project holds)."""
    root = os.path.abspath(ctx.root or os.curdir)
    area = root if _under(src_dir, root) else src_dir
    found: list[str] = []
    notes: list[str] = []
    for table in tables:
        path = os.path.join(src_dir, table)
        if not os.path.isfile(path):
            continue
        try:
            rows = K.children(ctx.load_file(path, loader=K.load_tree), "lib")
        except (K.KicadFormatError, UnicodeDecodeError) as exc:
            notes.append(f"{table} does not parse ({exc}), so no library it names was staged")
            continue
        for row in rows:
            nick = str(K.value(row, "name", default="") or "?")
            uri = str(K.value(row, "uri", default="") or "").replace("\\", "/")
            var = next((v for v in PROJECT_VARS if uri.startswith(v)), None)
            if var is None:
                continue
            target = os.path.normpath(os.path.join(src_dir, uri[len(var):].lstrip("/")))
            if not _under(target, area) or _under(src_dir, target):
                notes.append(f"{table} {nick} ({uri}) is outside the project")
            elif not os.path.exists(target):
                notes.append(f"{table} {nick} ({uri}): nothing there")
            elif target not in found:
                found.append(target)
    return found, notes


def _libraries_note(notes: list[str]) -> str:
    """The libraries the stage could not take, for a verdict's detail, or ""."""
    if not notes:
        return ""
    more = len(notes) - LIBRARIES_NAMED
    return (f"; not staged with the board: {'; '.join(notes[:LIBRARIES_NAMED])}"
            + (f"; and {more} more" if more > 0 else ""))


def _stage(ctx: GateContext, gid: str, path: str, *, board: bool, schematic: bool,
           libraries: bool = False, notes: list | None = None):
    """Copy what kicad-cli will read into ``<out>/pcb-kicad/<gate>/``: the board
    and/or the root schematic with every sheet under it, the project files
    beside them, and, with ``libraries``, the project libraries their tables
    name (:func:`_project_libraries`: the footprint table's for a board, both for
    a schematic). Returns ``(staged stem, stage dir)`` or an error Verdict; a
    note for each library it could not take goes into ``notes``. Libraries go
    only to a run whose findings read them (KiCad's library checks: ``pcb.drc``,
    ``pcb.erc``, ``pcb.fab_rules``); a run that reads no library is staged as it
    always was, files only, beside one another.

    Staged so kicad-cli writes nothing in the project (it saves a .kicad_prl and
    rewrites the .kicad_pro beside any board it opens), and so the copy, a Python
    read, keys every file even though the subprocess's own reads are invisible.
    The tables are copied as they are and each library keeps its place relative
    to the board: a library one directory up puts the board one directory down
    in the stage. A file left behind by an earlier run is removed first: a run
    must never read a sheet the project no longer has.
    """
    stem = _stem(path)
    name = os.path.basename(stem)
    src_dir = os.path.dirname(os.path.abspath(stem))
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
    staged_libraries, said = (_project_libraries(
        ctx, src_dir, DIR_FILES if schematic else ("fp-lib-table",))
        if libraries else ([], []))
    if notes is not None:
        notes.extend(said)
    base = os.path.commonpath([src_dir, *staged_libraries])
    home = os.path.normpath(os.path.join(stage, os.path.relpath(src_dir, base)))
    for rel in wanted:
        src = os.path.join(src_dir, rel)
        if not os.path.isfile(src):
            return Verdict(gate=gid, passed=False, detail=f"no file at {src}")
        dst = os.path.join(home, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copyfile(src, dst)
    for rel in [name + ext for ext in STEM_FILES] + list(DIR_FILES):
        if os.path.isfile(os.path.join(src_dir, rel)):
            shutil.copyfile(os.path.join(src_dir, rel), os.path.join(home, rel))
    for lib in staged_libraries:
        dst = os.path.join(stage, os.path.relpath(lib, base))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        if os.path.isdir(lib):
            shutil.copytree(lib, dst, dirs_exist_ok=True)
        else:
            shutil.copyfile(lib, dst)
    return os.path.join(home, name), stage


#: What this pack measured each kicad-cli release on: one file per release,
#: ``gates/kicad-cli/<release>.json``, holding the newest board and schematic
#: format that release reads, the day and the host it was measured on, and what
#: was shown there.
#:
#: **The release is part of what a kicad-cli gate reads.** After each run the
#: gate asks for the record of the release its report names (``kicad_version``)
#: by that file's path, found or not, and the spine keys a path a gate asked
#: about, a missing one included (``verdicts.Reads``: "None: missing, which is
#: itself an input"). So a control run under kicad-cli 10.0.8 and one under 9.0.9
#: are two control entries, never two outcomes of one. What slipped through, in
#: the holdout run on outside designs: the spine keys a control on the gate's
#: code, its selftest and what it read, and a subprocess is one opaque channel
#: named by its program, so the known-good DRC control's pass under KiCad 9 and
#: its fail under KiCad 10.0.8 landed on one key, and the gate read
#: "unqualified: two control outcomes for identical inputs" on every KiCad 10
#: host. Rejected: keying every control of a gate that declares
#: ``requires_tools`` on the tool's version in the spine (``verdicts.py``; a
#: spine change that moves every committed verdict cache, for one channel a gate
#: can name itself); skipping every release not measured, as ``fdm.slice`` does
#: for OrcaSlicer (an Orca release can turn a reader's miss into a pass, which
#: is why it skips; here the exit code is KiCad's verdict and its report must
#: agree with it, and KiCad ships a patch release about every six weeks, each of
#: which would skip until measured); asking ``kicad-cli version`` before every
#: run (one more launch per run, about 0.1 s here and 0.9 s through a container,
#: where the report already names its release).
RELEASES_DIR = os.path.join(_HERE, "kicad-cli")

#: The keys of a release record, and the format keys the reader reads.
RELEASE_KEYS = ("release", "board_format", "schematic_format", "measured", "on", "shown")
_RELEASE = re.compile(r"(\d+)\.(\d+)\.(\d+)")

#: kicad-cli's own answer to ``version`` takes about 0.1 s here and 0.9 s through
#: a container; past 30 s it is not answering, and the run's error stands as it
#: was. Rejected: the DRC's own timeout (two minutes, for a question that has
#: already failed once).
VERSION_TIMEOUT_S = 30.0


def _release_of(text) -> str:
    """The first ``X.Y.Z`` in ``text`` (``"10.0.8"``, ``"9.0.9-1"``), or ""."""
    match = _RELEASE.search(str(text or ""))
    return ".".join(match.groups()) if match else ""


def _release_key(release: str) -> tuple[int, ...]:
    return tuple(int(x) for x in release.split("."))


def _release_record(ctx: GateContext, release: str, *, load: bool):
    """``(record or None, the release it is for, the releases this pack
    measured)``. The exact release's file is asked for first: that question is
    what keys the release (:data:`RELEASES_DIR`). When it is missing, the newest
    record of its series (``10.0.8`` for ``10.0.9``) stands for it, only when
    ``load`` (what a tool limit needs: the formats); a patch release reads the
    formats its series reads. Raises ValueError on a record that is not of the
    shape :data:`RELEASE_KEYS` names."""
    path = os.path.join(RELEASES_DIR, release + ".json")
    exists = os.path.isfile(path)
    try:
        measured = sorted((n[:-len(".json")] for n in os.listdir(RELEASES_DIR)
                           if n.endswith(".json") and _RELEASE.fullmatch(n[:-len(".json")])),
                          key=_release_key)
    except FileNotFoundError:
        measured = []
    if not load:
        return None, release if exists else "", measured
    if not exists:
        series = [r for r in measured if r.rsplit(".", 1)[0] == release.rsplit(".", 1)[0]]
        if not series:
            return None, "", measured
        path = os.path.join(RELEASES_DIR, series[-1] + ".json")
    record = ctx.load_file(path, loader=_load_json)
    if (not isinstance(record, dict) or sorted(record) != sorted(RELEASE_KEYS)
            or not all(isinstance(record[k], int) and not isinstance(record[k], bool)
                       for k in ("board_format", "schematic_format"))):
        raise ValueError(f"{os.path.basename(path)} is not a release record: it holds "
                         f"exactly {list(RELEASE_KEYS)}, the two formats as integers")
    return record, str(record["release"]), measured


def _cli_release() -> tuple[str, str]:
    """``(release, "")`` of the kicad-cli on PATH, from ``kicad-cli version
    --format plain`` (it printed ``9.0.9`` and ``10.0.8``), or ``("", why it
    named none)``: it could not be launched, ran past :data:`VERSION_TIMEOUT_S`,
    or printed no ``X.Y.Z``. :func:`_tool_limit` asks it after a run failed, and
    ``pcb.antenna_keepout`` before it runs anything (:func:`_keepout_pair`)."""
    try:
        proc = subprocess.run(["kicad-cli", "version", "--format", "plain"], capture_output=True,
                              text=True, timeout=VERSION_TIMEOUT_S, stdin=subprocess.DEVNULL)
    except OSError as exc:
        return "", f"could not launch kicad-cli: {exc}"
    except subprocess.TimeoutExpired:
        return "", f"kicad-cli version ran past {VERSION_TIMEOUT_S:g} s"
    release = _release_of(proc.stdout)
    if release:
        return release, ""
    said = (proc.stdout or proc.stderr).strip().splitlines()[-1:] or ["nothing"]
    return "", f"kicad-cli version exited {proc.returncode}, printing {said[0][:60]!r}"


def _release(ctx: GateContext, doc: dict) -> str:
    """The release of kicad-cli that wrote ``doc``, as a verdict's detail says
    it, after asking for its record (which keys it: :data:`RELEASES_DIR`)."""
    release = _release_of(doc.get("kicad_version") if isinstance(doc, dict) else "")
    if not release:
        return "kicad-cli named no release in its report"
    _record, found, measured = _release_record(ctx, release, load=False)
    if found:
        return f"kicad-cli {release}"
    return (f"kicad-cli {release} (no record of it in this pack: measured "
            f"{', '.join(measured) or 'none'})")


def _tool_limit(ctx: GateContext, path: str) -> str:
    """Why kicad-cli could not load ``path`` (a staged board or schematic, and a
    board's schematic beside it), when the reason is a limit of the tool: a file
    in a format newer than the running release reads. "" otherwise, and the run's
    error stands.

    Asked only after kicad-cli failed. What slipped through, in the holdout run
    on outside designs: kicad-cli 9.0.9 exits 3, "Failed to load board", on a
    board KiCad 10 saved (measured), and every tier-1 gate read errored on that
    one predictable cause, louder than the tier-0 gates' skip for the same board.
    The release comes from ``kicad-cli version --format plain`` (it printed
    ``9.0.9`` and ``10.0.8``), the formats it reads from its record."""
    files = [path]
    if path.endswith(".kicad_pcb") and os.path.isfile(_stem(path) + ".kicad_sch"):
        files.append(_stem(path) + ".kicad_sch")
    formats = [(f, K.header_version(f)) for f in files]
    if not any(fmt for _f, fmt in formats):
        return ""
    release, _why = _cli_release()
    if not release:
        return ""
    try:
        record, _found, _measured = _release_record(ctx, release, load=True)
    except ValueError:
        return ""
    if record is None:
        return ""
    for f, fmt in formats:
        kind = "schematic" if f.endswith(".kicad_sch") else "board"
        reads = record[f"{kind}_format"]
        if fmt and fmt > reads:
            return (f"kicad-cli {release} reads {kind} formats through {reads}; this {kind} is "
                    f"format {fmt}, saved by a newer KiCad: a tool limit, not a defect "
                    f"(references/installing.md)")
    return ""


def _kicad_cli(ctx: GateContext, gid: str, stage: str, argv: list[str], report: str,
               log_name: str = "kicad-cli.log"):
    """Run ``kicad-cli argv`` writing its JSON report to ``<stage>/<report>``;
    ``(exit code, report, evidence)`` or a skip/error Verdict.

    Any exit but 0 (nothing found) or 5 (something found, with
    ``--exit-code-violations``) is kicad-cli failing to run, and errors, unless
    the file is newer than the running release reads, which skips
    (:func:`_tool_limit`). So does a run that wrote no report: the report must be
    this run's, never an old one. The release that wrote the report is asked for
    by its record's path (:func:`_release`), so it is part of what the gate read.
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
    log = os.path.join(stage, log_name)
    with open(log, "w", encoding="utf-8") as fh:
        fh.write(" ".join(argv) + f"\nexit {proc.returncode}\n{proc.stdout}\n{proc.stderr}")
    if proc.returncode not in (EXIT_CLEAN, EXIT_VIOLATIONS) or not os.path.isfile(out):
        limit = _tool_limit(ctx, argv[-1])
        if limit:
            return _skip(gid, limit)
        return Verdict(gate=gid, passed=False,
                       error=f"kicad-cli {what} exited {proc.returncode} without a "
                             f"report: {(proc.stderr or proc.stdout).strip()[:200]}",
                       evidence=[log])
    with open(out, encoding="utf-8") as fh:
        doc = json.load(fh)
    _release(ctx, doc)          # asked for its record's path: keys the release
    return proc.returncode, doc, [out, log]


def _drc(ctx: GateContext, gid: str, path: str, severity: str, *, parity: bool = False,
         notes: list | None = None, staged_out: list | None = None):
    """``kicad-cli pcb drc`` on a staged copy of the board (and, for parity, its
    schematic): ``(code, report, evidence)`` or a skip/error Verdict. With
    ``notes`` the board's project libraries are staged too, and a note for each
    one the stage could not take goes into it. ``staged_out`` takes ``(staged
    stem, stage, argv)``, for a second run on the same stage (:func:`_refill_run`)."""
    if not os.path.isfile(path):
        return Verdict(gate=gid, passed=False, detail=f"no board at {path}")
    staged = _stage(ctx, gid, path, board=True, schematic=parity,
                    libraries=notes is not None, notes=notes)
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
    argv.append(stem + ".kicad_pcb")
    if staged_out is not None:
        staged_out.append((stem, stage, argv))
    return _kicad_cli(ctx, gid, stage, argv, "drc.json")


def _found(doc: dict) -> list:
    """Every item a DRC report lists, of every kind, an excluded one included:
    what kicad-cli's exit code speaks for."""
    return (list(doc.get("violations", [])) + list(doc.get("unconnected_items", []))
            + list(doc.get("schematic_parity", [])))


def _waived(items) -> list:
    """The items a person excluded in KiCad: the report lists each one with
    ``"excluded": true`` and the comment the exclusion carries."""
    return [v for v in items if v.get("excluded") is True]


def _counted(items) -> list:
    """The items no exclusion waives: what a KiCad gate counts.

    What slipped through, in the holdout run on outside designs: the gates said
    a waiver is KiCad's own exclusion, honoured, and counted every item the
    report listed, the excluded ones too, so a design's two waived courtyard
    overlaps would have failed it. kicad-cli 9.0.9 and 10.0.8 both list an
    excluded item, with ``"excluded": true`` and its comment, and both exit 5
    when it is the only item (measured: a dangling via, excluded in the project
    file): the exit code speaks for the report, an exclusion included."""
    return [v for v in items if v.get("excluded") is not True]


def _disagree(code: int, found: list, counted: list) -> bool:
    """Whether kicad-cli's exit code and its report disagree: a clean exit beside
    an item nothing waives, or "something found" beside a report that lists
    nothing at all. An exit of 5 beside excluded items alone agrees, and so would
    a clean one."""
    return (code == EXIT_CLEAN and bool(counted)) or (code != EXIT_CLEAN and not found)


#: The room a KiCad gate's detail line has: the spine keeps a detail to 500
#: characters (``gates._DETAIL_MAX``) and cuts the end past them, so what a line
#: says last is what it loses. :func:`_line` builds every kicad-cli gate's line
#: inside it. Not read from the spine: the spine's name is private, and a gate
#: that read it would move with an edit there that no test of this pack sees.
DETAIL_ROOM = 500

#: The fewest characters :func:`_line` cuts a part to, before its "...": a kind
#: with its count and severity ("120x power_pin_not_driven (error)" is 33), or
#: the first library and the table it is in. Rejected: dropping a part whole
#: when it cannot keep more (a cut that is not marked is quoted as if it were
#: complete); 20 (a library note then names only its table).
CUT_KEEPS = 40


def _flat(text: str) -> str:
    """``text`` as the spine flattens a detail: a line break is " | ", a run of
    blanks one space. A length counted before that is off by every break."""
    return re.sub(r"\s+", " ", re.sub(r"\s*\n\s*", " | ", str(text)))


def _line(*parts) -> str:
    """A detail line of at most :data:`DETAIL_ROOM` characters, from ``parts`` in
    order. A part is a string, kept whole, or ``(text, n)`` or ``(text, n, open,
    close)``: while the line is too long, the parts with an ``n`` are cut, the
    lowest ``n`` first, each from its end, marked "...", to no fewer than
    :data:`CUT_KEEPS` characters. ``open`` and ``close`` wrap the text and are
    not cut. A part with no text is left out, its wrap with it.

    The parts kept whole are the count and the release, short in any case; the
    room left goes to the rest, cut from the one least needed (the findings'
    own words) to the one most needed (the libraries the stage could not take).
    What slipped through, in the review of the holdout fixes: ``pcb.erc`` cut the
    findings it quotes to 200 characters and put the libraries and the release
    after them, and on a report of five kinds the line reached the spine's cap
    before either: the verdict named no library and no release, on the design
    whose missing libraries were the reason to name them. Rejected: a fixed cut
    per part (the 200 left room only when the other parts were short); the
    release first (it reads as what the line is about)."""
    rows = []
    for part in parts:
        if isinstance(part, str):
            rows.append([_flat(part), None, "", ""])
        else:
            text, order, *wrap = part
            rows.append([_flat(text), order, *(wrap or ["", ""])])

    def size() -> int:
        return sum(len(o) + len(t) + len(c) for t, _n, o, c in rows if t)

    for row in sorted((r for r in rows if r[1] is not None), key=lambda r: r[1]):
        over = size() - DETAIL_ROOM
        if over <= 0:
            break
        keep = max(len(row[0]) - over - 3, CUT_KEEPS)
        if keep < len(row[0]) - 3:
            row[0] = row[0][:keep].rstrip() + "..."
    return "".join(o + t + c for t, _n, o, c in rows if t)


#: The order a detail line names KiCad's severities in: errors first. The words
#: are the ones kicad-cli 9.0.9 and 10.0.8 write in a report's ``severity``; one
#: not here sorts after them.
SEVERITY_ORDER = ("error", "warning", "info", "exclusion")

#: How many kinds of finding a detail line names, errors first; past it, how
#: many more kinds there are. Five named every error kind of a holdout board
#: whose 16 errors were five kinds under 318 warnings, in one line. Rejected:
#: four, the most numerous (the line before, which named only warnings there);
#: every kind (a board with twenty kinds is a line nobody reads to the end, and
#: the report is the evidence).
KINDS_NAMED = 5


def _kinds(items) -> str:
    """``"16 error, 318 warning: 5x shorting_items (error), ..."``: the count of
    each severity, then each kind, errors first and the most numerous first
    within one severity.

    What slipped through, in the holdout run on outside designs: the line named
    the four most numerous kinds, so on a board with 318 warnings (199 of them
    solder_mask_bridge) its 16 errors (shorts, clearance) were in no word of
    it, and the verdict, right, read as silk noise."""
    def rank(severity: str) -> int:
        return SEVERITY_ORDER.index(severity) if severity in SEVERITY_ORDER else len(
            SEVERITY_ORDER)
    severities = Counter(str(v.get("severity", "?")) for v in items)
    kinds = Counter((str(v.get("type", "?")), str(v.get("severity", "?"))) for v in items)
    order = sorted(kinds.items(), key=lambda kv: (rank(kv[0][1]), -kv[1], kv[0][0]))
    head = ", ".join(f"{severities[s]} {s}" for s in sorted(severities, key=lambda s: (rank(s), s)))
    named = ", ".join(f"{n}x {t} ({s})" for (t, s), n in order[:KINDS_NAMED])
    more = len(order) - KINDS_NAMED
    return f"{head}: {named}" + (f", and {more} more kind(s)" if more > 0 else "")


def _waived_note(waived: list) -> str:
    """The excluded items, for a detail line: how many, of which kinds, and the
    first exclusion's comment."""
    if not waived:
        return ""
    kinds = Counter(str(v.get("type", "?")) for v in waived)
    comment = next((str(v.get("comment")).strip() for v in waived
                    if str(v.get("comment") or "").strip()), "")
    return (f"; {len(waived)} excluded in the project, not counted: "
            + ", ".join(f"{n}x {k}" for k, n in kinds.most_common())
            + (f" ({comment[:60]!r})" if comment else ""))


# --------------------------------------------------------------------------- #
# the zones as stored and as KiCad refills them
# --------------------------------------------------------------------------- #
#: Where a KiCad gate refills the board's zones: a folder of its stage, holding a
#: copy of the board beside the project's own ``.kicad_pro`` and ``.kicad_dru``,
#: so the refill is the one a person's KiCad makes, under the project's
#: clearances, never under the rules a gate writes into its stage (the fab's).
#:
#: **Why both.** kicad-cli judges a zone, a teardrop among them, by the fill the
#: file stores. A person's DRC run in KiCad refills first (its dialog's "Refill
#: all zones before performing DRC"), so a stale stored fill reads clean there
#: and full of findings here. What slipped through (holdout round 2): on a KiCad
#: 10 board 20 of pcb.drc's 21 counted errors, and 13 of pcb.fab_rules'
#: findings, were stored teardrops KiCad 10.0.8's own ``--refill-zones`` clears,
#: and the line sent a person to fix clearances that a refill removes. But the
#: stored fill is what a fab plots: ``kicad-cli pcb export gerbers`` 9.0.9 has no
#: refill, and 10.0.8's ``--check-zones`` is off unless asked for (measured on
#: that board: the two plots of B.Cu differ), and one of those stored teardrops
#: sat on another net's pad at 0.0000 mm. So each finding is counted once over
#: both copies, as ``pcb.antenna_keepout`` counts its own, and the line says
#: apart how many only the stored fill shows, with the remedy: refill the zones
#: and save the board. A finding only the refill shows (a pour moved and not
#: refilled since) is counted too: the next refill puts that copper there.
#: Measured on the baseline with a stored fill drawn over two pads of other nets
#: (tests/test_pcb_kicad.py): 9 findings stored, 0 refilled, under kicad-cli
#: 9.0.9 with pcbnew 9.0.9 and under 10.0.8; with a thermal-relief pad
#: connection, one starved thermal relief only the refill shows, under both.
#:
#: **How.** kicad-cli 10 refills and saves the copy itself (``pcb drc
#: --refill-zones --save-board``); kicad-cli 9.0.9 has no refill, so the copy is
#: refilled with KiCad's ``pcbnew`` in a child (``_keepout_fill.py``, as the
#: keep-out's). Where neither can (kicad-cli 9 and no ``pcbnew`` in nopekit's
#: Python), the stored fill alone is judged and the line says so.
#:
#: *Rejected:* the refilled copy alone (the stored fill a fab plots, a short among
#: it, would pass); the stored copy alone (the first cut: a sound design with a
#: stale fill read as clearance faults to fix one by one, and a pour moved under
#: a track read clean); leaving the stored-only findings out of the count (the
#: same short would pass, and KiCad's remedy, a refill and a save, is the
#: design's to make, as a parity mark is); refilling under the stage's rules
#: (``pcb.fab_rules`` writes the fab's: a refill there would pour to the fab's
#: clearance, not the project's, and hide a gap the person's refill leaves).
REFILL_DIR = "nopekit-refill"


def _zoned(tree: list) -> bool:
    """Whether the board holds a copper zone a refill may change: a pour or a
    teardrop, on the board or in a footprint; a rule area fills nothing."""
    zones = list(K.children(tree, "zone"))
    for fp in K.children(tree, "footprint"):
        zones += K.children(fp, "zone")
    return any(_keepout_flags(z) is None for z in zones)


def _refilled(ctx: GateContext, gid: str, path: str, stem: str, release: str):
    """The staged board ``stem`` refilled under the project's own settings
    (:data:`REFILL_DIR`): ``(refilled board, how)``, ``(None, "")`` for a board
    with no zone, ``(None, why it cannot here)``, or an error or skip Verdict. A
    refill that runs and fails is an error, never a quiet return to the stored
    fill."""
    tree = ctx.load_file(path, loader=K.load_tree)
    if not _zoned(tree):
        return None, ""
    folder = os.path.join(os.path.dirname(stem), REFILL_DIR)
    os.makedirs(folder, exist_ok=True)
    name = os.path.basename(stem)
    board = os.path.join(folder, name + ".kicad_pcb")
    shutil.copyfile(stem + ".kicad_pcb", board)
    for ext in STEM_FILES:
        if os.path.isfile(_stem(path) + ext):
            shutil.copyfile(_stem(path) + ext, os.path.join(folder, name + ext))
    log = os.path.join(folder, "refill.log")
    timeout = float(ctx.param("pcb_drc_timeout_s", DEFAULT_DRC_TIMEOUT_S) or
                    DEFAULT_DRC_TIMEOUT_S)
    if release and _release_key(release)[0] >= 10:
        argv = ["kicad-cli", "pcb", "drc", "--refill-zones", "--save-board", "--format",
                "json", "-o", os.path.join(folder, "refill.json"), name + ".kicad_pcb"]
        try:
            proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                                  stdin=subprocess.DEVNULL, cwd=folder)
        except FileNotFoundError as exc:
            return _skip(gid, f"could not launch kicad-cli for the zone refill: {exc}")
        except subprocess.TimeoutExpired:
            return _skip(gid, f"kicad-cli's zone refill exceeded {timeout:g} s "
                              f"(pcb_drc_timeout_s) and was killed: nothing was shown")
        with open(log, "w", encoding="utf-8") as fh:
            fh.write(" ".join(argv) + f"\nexit {proc.returncode}\n{proc.stdout}\n{proc.stderr}")
        if proc.returncode not in (EXIT_CLEAN, EXIT_VIOLATIONS):
            return Verdict(gate=gid, passed=False, evidence=[log],
                           error=f"kicad-cli {release}'s zone refill exited {proc.returncode}: "
                                 f"{(proc.stderr or proc.stdout).strip()[-200:]}")
        return board, f"kicad-cli {release}"
    pcb = _pcbnew_release(gid)
    if isinstance(pcb, Verdict) or not pcb[0]:
        return None, (f"kicad-cli {release or '(no release named)'} has no zone refill and "
                      f"KiCad's pcbnew does not import in nopekit's Python here")
    out = os.path.join(folder, "refilled", name + ".kicad_pcb")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    got = _refill(ctx, gid, board, out, log)
    if isinstance(got, Verdict):
        return got
    return out, f"pcbnew {pcb[0]}"


def _finding_key(v: dict) -> tuple:
    """A finding as both runs name it: its type and the ids of its items. A
    refill keeps every id (measured, kicad-cli 10.0.8 and pcbnew 9.0.9: the 12
    findings a refill left on a holdout board kept theirs); the words do not
    stay (the actual gap moves)."""
    return (str(v.get("type")), tuple(sorted(K.kiid(i.get("uuid")) for i in v.get("items", []))))


def _over_both(stored: list, refilled: list) -> tuple[list, list, list]:
    """``(each finding once over both runs, those only the stored fill shows,
    those only the refill shows)``. A finding both show is the refill's; a key
    the stored run lists more often than the refilled one counts its extra
    findings as the stored fill's."""
    left = Counter(_finding_key(v) for v in refilled)
    stored_only = []
    for v in stored:
        k = _finding_key(v)
        if left[k]:
            left[k] -= 1
        else:
            stored_only.append(v)
    seen = Counter(_finding_key(v) for v in stored)
    refill_only = []
    for v in refilled:
        k = _finding_key(v)
        if seen[k]:
            seen[k] -= 1
        else:
            refill_only.append(v)
    return refilled + stored_only, stored_only, refill_only


def _fill_note(total: int, stored_only: list, refill_only: list, how: str,
               refilled: bool) -> str:
    """The line's words on the two copies (:data:`REFILL_DIR`): ``how`` names
    what refilled them, or, with ``refilled`` false, why nothing could."""
    if not how:
        return ""
    if not refilled:
        return f"; the stored zone fill only: {how}, so what a refill pours is not judged"
    words = []
    if stored_only:
        kinds = ", ".join(f"{n}x {t}" for t, n in Counter(
            str(v.get("type", "?")) for v in stored_only).most_common())
        words.append(f"{len(stored_only)} of {total} only on the stored zone fill ({kinds}), "
                     f"which KiCad's refill clears: refill the zones and save the board, "
                     f"since a fab plots the stored fill")
    if refill_only:
        words.append(f"{len(refill_only)} of {total} only after KiCad's refill: the stored "
                     f"fill is out of date there")
    return "; " + ("; ".join(words) if words else
                   f"the zones judged as stored and as refilled ({how})")


def _refill_run(ctx: GateContext, gid: str, path: str, stem: str, stage: str,
                argv: list[str], doc: dict):
    """The same kicad-cli run again on the staged board refilled
    (:func:`_refilled`): ``(report, how, evidence)``, ``(None, how, [])`` where
    nothing was refilled (``how`` says why, or is "" for a board with no zone),
    or an error or skip Verdict. The refilled copy takes the staged board's place,
    so the run reads the same project, rules and libraries."""
    got = _refilled(ctx, gid, path, stem, _release_of(doc.get("kicad_version")))
    if isinstance(got, Verdict):
        return got
    board, how = got
    if board is None:
        return None, how, []
    shutil.copyfile(board, stem + ".kicad_pcb")
    run = _kicad_cli(ctx, gid, stage, argv, "drc_refilled.json", log_name="kicad-cli-refilled.log")
    if isinstance(run, Verdict):
        return run
    code, refilled, ev = run
    if _disagree(code, _found(refilled), _counted(_found(refilled))):
        return Verdict(gate=gid, passed=False, evidence=ev,
                       error=f"kicad-cli exited {code} on the refilled board but its report "
                             f"lists {len(_counted(_found(refilled)))} item(s): the two disagree")
    return refilled, how, ev


@gate(
    id="pcb.drc",
    basis="established",
    title="KiCad's design rule check finds nothing, at every severity",
    # Tags only this pack's gates bind (general-packs H1 and H2). `clearance`,
    # `manufacturability`, `connectivity`, `packaging` and `interference` are
    # cad-solid's and fdm-print's words too: a claim tagged `manufacturability`
    # was bound to pcb.drc, pcb.fab_rules, four cad mesh gates and every fdm
    # gate at once (holdout D04), and a board's DRC finding read on a claim about
    # a printed part. `design-rules` (H2) and `copper-clearance` (H1) are both
    # this pack's own words for what was `clearance`, and `pcb-manufacturability`
    # for what was `manufacturability` (PACK.md, "Tags this pack no longer lists").
    claims=["pcb", "design-rules", "pcb-manufacturability", "copper-clearance"],
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
    the count of the report's findings that no exclusion waives
    (:func:`_counted`). An exit code that disagrees with the report is an error,
    never a pass (:func:`_disagree`). On a board with a zone, the board as stored
    and as KiCad refills it, each finding once over both, and the line says apart
    what only the stored fill shows (:data:`REFILL_DIR`).

    ``pcb_drc_severity`` narrows it (``error``, ``warning``); the default is every
    severity, because a check at error severity cannot see a warning and the fab
    still prints it. To waive one finding, exclude it in KiCad: the exclusion is
    saved in the project file with its comment, KiCad's report marks the finding
    excluded, and this gate counts it apart, by kind, with the first comment. The
    line names each severity's count and the kinds, errors first (:func:`_kinds`),
    any library the stage could not take (:func:`_project_libraries`), and the
    release of kicad-cli that ran (:func:`_release`).
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
    libraries: list[str] = []
    where: list = []
    run = _drc(ctx, gid, path, severity, notes=libraries, staged_out=where)
    if isinstance(run, Verdict):
        return run
    code, doc, ev = run
    found = _found(doc)
    counted = _counted(found)
    if _disagree(code, found, counted):
        return Verdict(gate=gid, passed=False, evidence=ev,
                       error=f"kicad-cli exited {code} but its report lists "
                             f"{len(counted)} item(s): the two disagree")
    # The zones as stored and as KiCad refills them, each finding once over
    # both (:data:`REFILL_DIR`).
    stem, stage, argv = where[0]
    fill = _refill_run(ctx, gid, path, stem, stage, argv, doc)
    if isinstance(fill, Verdict):
        return fill
    refilled, how, fill_ev = fill
    ev += fill_ev
    stored_only: list = []
    refill_only: list = []
    waived = _waived(found)
    if refilled is not None:
        counted, stored_only, refill_only = _over_both(counted, _counted(_found(refilled)))
        waived = _over_both(waived, _waived(_found(refilled)))[0]
    dead: list[str] = []
    note = ""
    if os.path.isfile(_stem(path) + ".kicad_dru"):
        probed = _probe_rules(ctx, gid, path)
        if isinstance(probed, Verdict):
            return probed
        dead, note, probe_ev = probed
        ev += probe_ev
    # Within the spine's 500 characters (_line): the release kept, the kinds,
    # the rules' probe and the libraries cut last.
    found_parts = ([f"{len(counted)} finding(s), ", (_kinds(counted), 2)] if counted
                   else ["nothing found"])
    detail = _line(f"KiCad DRC at {severity} severities: ", *found_parts,
                   (_fill_note(len(counted), stored_only, refill_only, how,
                               refilled is not None), 5),
                   (_waived_note(waived), 1), (note, 3, "; ", ""),
                   (_libraries_note(libraries), 4), f"; {_release(ctx, doc)}")
    # A dead rule beside a clean run is an error: the run cannot show what the
    # rule was written to hold. Beside a finding it stays a fail, because a
    # finding KiCad made is real, and the detail names the dead rule too.
    if dead and not counted:
        return Verdict(gate=gid, passed=False, evidence=ev,
                       error=f"a custom rule cannot fire, so the clean run shows nothing "
                             f"about it: {'; '.join(dead)}")
    return Verdict(gate=gid, passed=not counted, measured=len(counted), limit=0,
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
    # script generated, with no schematic, `connectivity` also bound pcb.parity,
    # which skips there, so a claim "every connection is made" read Skipped beside
    # this gate's pass, and no tag let a person claim what the board supports.
    # `connectivity` itself is no longer listed: cad-solid's assembly check lists
    # it too, so a claim about a joined assembly read this gate's open net (holdout
    # run on outside designs; PACK.md, "Tags this pack no longer lists").
    # `net-connectivity` is this pack's word for it (general-packs H2), and binds
    # pcb.parity beside this gate, as `connectivity` did.
    claims=["pcb", "net-connectivity", "netlist", "copper-connectivity"],
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
    here alone, so it holds on a board with no schematic too. An item a person
    excluded in KiCad is counted apart, as ``pcb.drc`` counts it."""
    gid = "pcb.unconnected"
    path = _board_path(ctx)
    if path is None:
        return _skip(gid, _NO_BOARD)
    run = _drc(ctx, gid, path, "error")
    if isinstance(run, Verdict):
        return run
    _code, doc, ev = run
    listed = list(doc.get("unconnected_items", []))
    items = _counted(listed)
    nets = sorted({i.get("description", "").split("[", 1)[-1].split("]", 1)[0]
                   for v in items for i in v.get("items", []) if "[" in i.get("description", "")})
    found_parts = ([f"{len(items)} unconnected item(s) on ", (', '.join(nets[:6]) or '?', 2)]
                   if items else ["every connection the netlist asks for is made"])
    detail = _line(*found_parts, (_waived_note(_waived(listed)), 1),
                   f"; {_release(ctx, doc)}")
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
    the root schematic, every sheet under it, the project files and the project
    libraries its tables name: the verdict is the count of the report's findings
    no exclusion waives, and an exit code that disagrees with the report is an
    error. The line names the errors first, as ``pcb.drc``'s does.

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
    libraries: list[str] = []
    staged = _stage(ctx, gid, path, board=False, schematic=True, libraries=True,
                    notes=libraries)
    if isinstance(staged, Verdict):
        return staged
    stem, stage = staged
    run = _kicad_cli(ctx, gid, stage, ["sch", "erc", SEVERITY_FLAGS[severity],
                                       stem + ".kicad_sch"], "erc.json")
    if isinstance(run, Verdict):
        return run
    code, doc, ev = run
    found = [v for sheet in doc.get("sheets", []) for v in sheet.get("violations", [])]
    counted = _counted(found)
    if _disagree(code, found, counted):
        return Verdict(gate=gid, passed=False, evidence=ev,
                       error=f"kicad-cli exited {code} but its report lists "
                             f"{len(counted)} item(s): the two disagree")
    first = sorted(counted, key=lambda v: str(v.get("severity")) != "error")[:3]
    where = "; ".join(f"{v.get('type', '?')}: "
                      + ", ".join(i.get("description", "?") for i in v.get("items", [])[:2])
                      for v in first)
    # Within the spine's 500 characters, the release and the libraries kept and
    # the findings' own words cut first (_line).
    found_parts = ([f"{len(counted)} finding(s), ", (_kinds(counted), 3),
                    (where, 1, " (", ")")] if counted else ["nothing found"])
    detail = _line(f"KiCad ERC at {severity} severities over {len(doc.get('sheets', []))} "
                   f"sheet(s): ", *found_parts, (_waived_note(_waived(found)), 2),
                   (_libraries_note(libraries), 4), f"; {_release(ctx, doc)}")
    return Verdict(gate=gid, passed=not counted, measured=len(counted), limit=0,
                   comparator="<=", units="findings", detail=detail, evidence=ev)


#: The schematic-parity types ``pcb.parity`` counts, each a difference in what is
#: built: a part, a value, a footprint or a pad's net. The keys are the ones
#: KiCad 9.0.9's and 10.0.8's pcbnew report parity under (read off both builds,
#: and off parity reports on boards that differ from their schematics: a value,
#: a duplicated test point, a pad with no pin, a board with no part in the
#: schematic). ``footprint_symbol_mismatch`` carries a value, a footprint and a
#: fitting attribute ("Exclude from bill of materials" settings differ) alike,
#: in words KiCad translates, so all of it counts.
PARITY_COUNTED = {
    "missing_footprint": "a part in the schematic with no footprint",
    "extra_footprint": "a footprint with no part in the schematic",
    "duplicate_footprints": "two footprints for one part",
    "footprint_symbol_mismatch": "a value, footprint or fitting that differs",
    "net_conflict": "a pad whose net differs",
}

#: The parity types said apart and never counted: they compare data that moves
#: no part, value, footprint or net. ``footprint_symbol_field_mismatch`` is KiCad
#: 10's (a symbol field, "Missing symbol field 'manf#' in footprint", BOM data);
#: ``footprint_filters_mismatch`` is a footprint outside its symbol's filters, a
#: library assignment rule. What slipped through, in the holdout run on outside
#: designs: under KiCad 10.0.8, 32 of a board's 33 parity items were BOM fields,
#: and the gate, which counted every item, said the board and its schematic
#: differed 33 times, while its title said part, value, footprint and net.
#: Rejected: counting them (the gate's meaning moved with KiCad's release);
#: dropping them unsaid (a person reads the count apart).
PARITY_APART = {
    "footprint_symbol_field_mismatch": "symbol field(s) that differ",
    "footprint_filters_mismatch": "footprint(s) outside the symbol's filters",
}

#: Footprint kinds a parity item names whose footprints are all board-only
#: drawing (``_kicad.not_a_part``) are counted, and said apart in the line with
#: KiCad's remedy: KiCad lists a mounting hole or a logo not marked "Not in
#: schematic" as an extra footprint, and two such with no reference as
#: duplicates, and its own "Update PCB from Schematic" with "delete extra
#: footprints" would remove them. The mark is the design's to set, and it is one
#: click. What slipped through, in the holdout run: a board whose every part,
#: value, footprint and net agreed read 16 differences, all mounting holes and an
#: outline, in words ("Duplicate footprints; ...") that did not show no part
#: differed. The holdout's fix that followed said them apart and left them out
#: of the count, and round 2 of the holdout read that as a regression: round 1
#: had confirmed each such footprint a real, minor defect (three refuters of
#: three, on two boards) and asked only for the split line. Rejected: leaving
#: them out of the count (a defect KiCad and round 1 both name would pass, and
#: no record could make the gate count it again); counting them in the words
#: alone, unsplit (the first cut: a person read 16 differences and no part).
PARITY_FOOTPRINT_KINDS = ("extra_footprint", "duplicate_footprints")


@gate(
    id="pcb.parity",
    basis="established",
    title="The board and its schematic agree: every part, value, footprint and net",
    # `connectivity` is no longer listed: cad-solid's assembly check lists it too
    # (holdout run on outside designs; PACK.md, "Tags this pack no longer lists");
    # `net-connectivity` (general-packs H2) is this pack's word for it.
    # `schematic-parity` is this gate's alone.
    claims=["pcb", "schematic-parity", "netlist", "net-connectivity"],
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
    ``.kicad_sch``. The verdict is the count of the report's parity items that
    are a difference in what is built (:data:`PARITY_COUNTED`): a part on one and
    not the other, a value, footprint or fitting that differs, a pad on a net the
    schematic does not give it. Board-only drawing KiCad lists as extra or
    duplicate (:data:`PARITY_FOOTPRINT_KINDS`) is counted and said apart in the
    line, "k of n: board-only footprint(s) not marked 'Not in schematic' (...),
    mark them so in KiCad", with "no part, value, footprint or pad net differs"
    when it is every difference. Said apart and not counted: data that moves none
    of those (:data:`PARITY_APART`), an open pin on a pad with no net, and an item
    a person excluded in KiCad. A parity type this pack does not know is named;
    beside no counted difference it is an error, since the gate cannot say what
    it means. The run's exit code must agree with the report, or it errors.

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
    found = _found(doc)
    if _disagree(code, found, _counted(found)):
        return Verdict(gate=gid, passed=False, evidence=ev,
                       error=f"kicad-cli exited {code} but its report lists "
                             f"{len(_counted(found))} item(s): the two disagree")
    listed = list(doc.get("schematic_parity", []))
    items = _counted(listed)
    tree = ctx.load_file(path, loader=K.load_tree)
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
    no_net = {i for i, net in K.pad_nets_by_id(tree).items() if not net}
    agreed = [v for v in items if v.get("type") == "net_conflict" and v.get("items")
              and all(K.kiid(i.get("uuid")) in no_net for i in v.get("items", []))
              and "(unconnected-(" in str(v.get("description", ""))]
    footprints = K.footprints_by_id(tree)

    def drawing(v) -> bool:
        fps = [footprints.get(K.kiid(i.get("uuid"))) for i in v.get("items", [])]
        return bool(fps) and all(fp is not None and K.not_a_part(fp) for fp in fps)

    apart = Counter(str(v.get("type")) for v in items if v.get("type") in PARITY_APART)
    board_only = [v for v in items if v.get("type") in PARITY_FOOTPRINT_KINDS and drawing(v)]
    unknown = Counter(str(v.get("type", "?")) for v in items
                      if v.get("type") not in PARITY_COUNTED and v.get("type") not in PARITY_APART)
    left = {id(v) for v in agreed + board_only}
    built = [v for v in items if v.get("type") in PARITY_COUNTED and id(v) not in left]
    # Counted, the board-only drawing after what is built: the words quoted are
    # the built differences' first (:data:`PARITY_FOOTPRINT_KINDS`).
    items = built + board_only
    where = "; ".join(f"{v.get('description', '?')}" for v in built[:3])
    said = [f"{n}x {t} ({PARITY_APART[t]})" for t, n in apart.most_common()]
    drawn = ""
    if board_only:
        refs = sorted({K.footprint_ref(footprints[K.kiid(i.get("uuid"))])[0] or "no reference"
                       for v in board_only for i in v.get("items", [])})
        drawn = (f"{len(board_only)} of {len(items)}: board-only footprint(s) not marked 'Not "
                 f"in schematic' ({', '.join(refs[:3])}{', ...' if len(refs) > 3 else ''}), "
                 f"mark them so in KiCad")
        if not built:
            drawn += "; no part, value, footprint or pad net differs"
    kinds = ", ".join(f"{n}x {t}" for t, n in unknown.most_common())
    if unknown and not items:
        return Verdict(gate=gid, passed=False, evidence=ev,
                       error=f"{kinds}: parity of a type this pack does not know, so whether "
                             f"it is a difference in what is built is not known here "
                             f"({_release(ctx, doc)})")
    # Within the spine's 500 characters (_line): the count and the release kept,
    # the differences' own words cut first, the board-only split and a type this
    # pack does not know last.
    found_parts = ([f"{len(items)} difference(s) between board and schematic: ",
                    (where, 1, "", "; " if drawn else ""), (drawn, 5)]
                   if items else ["the board matches its schematic: every part, value, "
                                  "footprint and pad net"])
    detail = _line(*found_parts, (", ".join(said), 3, "; said apart, not counted: ", ""),
                   f"; {len(agreed)} open pin(s) sit on pads with no net" if agreed else "",
                   (_waived_note(_waived(listed)), 2),
                   (kinds, 4, "; of a type this pack does not know, not counted: ", ""),
                   f"; {_release(ctx, doc)}")
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
    # `pcb-manufacturability` in place of `manufacturability`, which cad-solid's,
    # fdm-print's and sourcing's gates list too (holdout run on outside designs;
    # PACK.md, "Tags this pack no longer lists").
    claims=["pcb", "pcb-manufacturability", "fab-limits"],
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
    clearance still binds and fails copper inside it (GitHub #13). On a board
    with a zone, the fab's DRC runs on the board as stored and on a copy KiCad
    refilled under the project's own settings, never the fab's, each finding once
    over both (:data:`REFILL_DIR`).
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
    # The notes too: a library the stage could not take is named here as at
    # pcb.drc. (What slipped through, in the review of the holdout fixes: the
    # stage made the notes and this gate dropped them, while kicad-cli.md said
    # each one is named in the verdict.)
    libraries: list[str] = []
    staged = _stage(ctx, gid, path, board=True, schematic=False, libraries=True,
                    notes=libraries)
    if isinstance(staged, Verdict):
        return staged
    stem, stage = staged
    with open(stem + ".kicad_dru", "w", encoding="utf-8") as fh:
        fh.write(K.dru_text(table, "fab"))
    try:
        raised = _zeroed_project(_stem(path) + ".kicad_pro", stem + ".kicad_pro", table)
    except ValueError as exc:
        return Verdict(gate=gid, passed=False, error=str(exc))
    argv = ["pcb", "drc", "--severity-all", stem + ".kicad_pcb"]
    run = _kicad_cli(ctx, gid, stage, argv, "drc.json")
    if isinstance(run, Verdict):
        return run
    code, doc, ev = run
    if _disagree(code, _found(doc), _counted(_found(doc))):
        return Verdict(gate=gid, passed=False, evidence=ev,
                       error=f"kicad-cli exited {code} but its report lists "
                             f"{len(_counted(_found(doc)))} item(s): the two disagree")
    # The zones as stored and as KiCad refills them under the project's own
    # settings, never the fab's this stage holds; each finding once over both
    # (:data:`REFILL_DIR`): a fab plots the stored fill.
    fill = _refill_run(ctx, gid, path, stem, stage, argv, doc)
    if isinstance(fill, Verdict):
        return fill
    refilled, how, fill_ev = fill
    ev += fill_ev
    pads = K.pad_ids(tree)
    fab_found, guessed = K.fab_findings(doc, table, pads, "fab")
    found = _found(doc)
    stored_only: list = []
    refill_only: list = []
    if refilled is not None:
        fab_refilled, guessed_refilled = K.fab_findings(refilled, table, pads, "fab")
        fab_found, stored_only, refill_only = _over_both(fab_found, fab_refilled)
        guessed = max(guessed, guessed_refilled)
        found = _over_both(found, _found(refilled))[0]
    # A finding of a type the project ignores, raised for this run and not the
    # fab's (silk on silk, when the project ignores silk_overlap), is no finding
    # pcb.drc sees: it runs under the project's own severities. Said apart, never
    # called pcb.drc's. (What slipped through, in review: it was.)
    counted = {_finding_key(v) for v in fab_found}
    rest = [v for v in found if _finding_key(v) not in counted]
    waived = Counter(v.get("type", "?") for v in rest if v.get("type") in raised)
    others = len(rest) - sum(waived.values())
    # Grouped by type with the fab rule's name, never the first descriptions: on a
    # real board those were "Silkscreen overlap" three times over, and a person
    # could not read from the line that nothing past the fab's limits was copper.
    found_parts = ([f"{len(fab_found)} finding(s) past its limits: ",
                    (K.fab_kinds(fab_found, table), 6)] if fab_found
                   else [f"inside every limit of {len(table['rules'])} rule(s)"])
    guessed_note = (f"; {guessed} silk overlap(s) counted although no pad is named in "
                    f"them: {pads[1]} pad(s) on the board get a random id from "
                    f"KiCad, so silk on silk cannot be told from silk on a pad"
                    if guessed else "")
    # The project's own custom rules are left out of this run, and one of them
    # may waive (severity ignore) what pcb.drc then never reports: with such a
    # file, the rest is said to be not the fab's, never pcb.drc's. (What slipped
    # through, in review: it was called pcb.drc's.)
    if others and os.path.isfile(_stem(path) + ".kicad_dru"):
        others_note = (f"; {others} other finding(s) in the same run are not the fab's: "
                       f"pcb.drc judges them under the project's own rules and severities")
    elif others:
        others_note = f"; {others} other finding(s) in the same run are pcb.drc's"
    else:
        others_note = ""
    waived_note = ""
    if waived:
        kinds = ", ".join(f"{n}x {k}" for k, n in waived.most_common())
        waived_note = (f"; {sum(waived.values())} other finding(s) of a type the project "
                       f"ignores ({kinds}), raised for this run only: pcb.drc does not "
                       f"see them")
    # A finding of the fab's the project excludes still counts: an exclusion is
    # a person's waiver of the project's own rule (pcb.drc counts it apart), and
    # the fab's published minimum is not the project's to waive. Said, so the
    # count is not read as the exclusion going unseen.
    excluded = len(_waived(fab_found))
    excluded_note = (f"; {excluded} of the fab's finding(s) the project excludes, counted: "
                     f"an exclusion waives the project's own rule, not the fab's"
                     if excluded else "")
    # Within the spine's 500 characters (_line): the release and what the board
    # is built of kept, the notes about other findings cut first, the fab's own
    # findings last. What the service was chosen against is said last: a pass on
    # a board read as KiCad's default stackup says so, never as if the board had
    # stated FR4.
    detail = _line(f"{who} {service['name']} ({table['name']}{oz_note}, read "
                   f"{fab.get('read', '?')}): ", *found_parts,
                   (_fill_note(len(fab_found), stored_only, refill_only, how,
                               refilled is not None), 7),
                   (guessed_note, 2), (others_note, 1), (waived_note, 3), (excluded_note, 5),
                   (_libraries_note(libraries), 4), f"; {_release(ctx, doc)}",
                   f"; the board: {', '.join(built)}")
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

#: The wall clock each helper call (the export, the regions' pass, the geometry)
#: gets when ``pcb_fit_timeout_s`` is not stated: this base, and
#: :data:`FIT_TIMEOUT_PER_PART_S` for each part the call handles. The baseline
#: takes about 1 s to export and 3 s to measure, with two parts.
DEFAULT_FIT_TIMEOUT_S = 120.0

#: The default's share for each part placed. Measured on a holdout run: a
#: populated handheld board of 74 modelled parts took 2 to 4 minutes to export
#: and fit (about 3.2 s a part at worst, both calls together), so 120 s flat
#: skipped a sound board of ordinary size, and each skip cost a full re-run.
#: 5 s a part is one and a half times that worst rate, for each call alone.
#: *Rejected:* 120 s flat (fitted to the baseline's two parts: what slipped
#: through); several minutes flat (a hung run on a two-part board waits as long
#: as a hundred-part board needs); no limit (a hang never ends, and a check run
#: never returns); a rate measured on this machine alone (another is slower).
FIT_TIMEOUT_PER_PART_S = 5.0

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
#: neither, and the fit does not ask for their bodies unless they show a model.
#:
#: A footprint that shows a 3D model is a part whatever its attributes say: a
#: display, a cell, a speaker or a buzzer fitted by hand is often drawn with no
#: type and kept out of the BOM and the position file, and kicad-cli exports its
#: model all the same (``--no-unspecified`` is not given). What slipped through
#: (a holdout run): an e-paper display's footprint, with ``exclude_from_bom``,
#: ``exclude_from_pos_files`` and no type, named its model. The fit asked for
#: SMD and through-hole parts only, so the display's model was not copied to the
#: stage, kicad-cli could not find it, and the pass said "nothing collides" of a
#: board whose window ledge cut 22.75 mm^3 into the display. *Rejected:* every
#: footprint with a courtyard (stock mounting holes, fiducials and test points
#: carry one and no model, and each would skip the fit); ``exclude_from_bom`` as
#: not fitted, as the power rules read it (a hand-fitted part is in no BOM and
#: is in the case). A footprint with no type and no model is not asked for, and
#: the verdict names it.
_PART_ATTRS = ("smd", "through_hole")

#: The variables a model path may name for KiCad's own 3D library, and where its
#: installers put that library, searched in that order whatever the variable is
#: set to (a gate reads no environment variable, ``_model_file``): the Linux
#: packages' path, the macOS bundle's, and the Windows
#: installer's for KiCad 10 and 9. ``_model_file`` reads them to say whether a
#: model is found on this host before the export: a declared height stands in
#: for a model found nowhere here. A variable the project names in
#: :data:`MODEL_DIRS_KEY` is searched in its folder alone. *Rejected:* asking
#: kicad-cli first (an export per question, a full run each); a search of the
#: disk (slow, and it finds old releases' libraries).
MODEL_DIR_VARS = re.compile(r"^KICAD\d*_3DMODEL_DIR$")
STOCK_MODEL_DIRS = (
    "/usr/share/kicad/3dmodels",
    "/Applications/KiCad/KiCad.app/Contents/SharedSupport/3dmodels",
    r"C:\Program Files\KiCad\10.0\share\kicad\3dmodels",
    r"C:\Program Files\KiCad\9.0\share\kicad\3dmodels",
)
_MODEL_VAR = re.compile(r"\$\{([^}]+)\}|\$\(([^)]+)\)")

#: The project's own folder for a model path variable: ``{variable: folder}``, the
#: folder from the project root unless it is absolute (``{"KICAD9_3DMODEL_DIR":
#: "hardware/3dmodels"}``). ``pcb.enclosure_fit`` reads it through ``ctx``, so it is
#: in the verdict's key (invariant 7). A model path that names the variable is
#: looked for in that folder alone (``_model_file``), and the staged board names
#: the folder in its place (``_named_dirs``), so kicad-cli reads the file the gate
#: found, by its absolute path, whatever its own environment or configuration
#: says. Any variable but ``KIPRJMOD`` may be named, KiCad's own and a project's
#: (``${MY_3D}``).
#:
#: What slipped through, in the merge of general-packs H2 after H3: the merge made
#: ``_model_file`` read no environment variable (rho keys none, and ``doctor``
#: holds the bundled gates to none), so a host whose ``KICAD<n>_3DMODEL_DIR``
#: points at a folder of its own got a declared height in place of a model
#: kicad-cli would find, or a skip for a part with none, and nothing the project
#: could state moved it. *Rejected:* reading the variable from the environment
#: (the read the merge took out: an input no key holds, and the doctor's
#: ``env-reads`` row); handing the folder to kicad-cli in its environment (the
#: gate would build that environment from ``os.environ``, the same read); copying
#: each model into the stage (megabytes a run for a populated board, where an
#: absolute path reads the same file); a list of folders per variable (KiCad
#: gives a variable one value).
MODEL_DIRS_KEY = "pcb_model_dirs"
_VAR_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

#: The model files ``--subst-models`` puts in place of a VRML model that is
#: there: a STEP of the same name beside it. The stage copies the twin with the
#: VRML file; it never makes a model found. Measured with KiCad 9.0.9 on the
#: baseline's U1 named as ``3d/SOT-23-6.wrl``: with the VRML file and its STEP
#: twin, the twin is fitted; with the VRML file alone, kicad-cli puts its mesh in
#: the export ("Adding component U1"); with the STEP twin alone, "Could not add
#: 3D model for U1. File not found: ${KIPRJMOD}/3d/SOT-23-6.wrl", and U1 is not
#: in the export. (What slipped through in the review of H2: a VRML name counted
#: as found by its twin alone, so a declared height for that part was named
#: unused, and the run skipped on the part kicad-cli left out.)
_SUBST_EXTS = (".step", ".stp", ".STEP", ".STP")

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


#: How a line names a footprint with an empty reference (KiCad leaves a logo or
#: a mounting hole so). What slipped through (holdout round 2): the fit's line
#: read "not fitted (no type, no 3D model): , G***", a blank name first.
NO_REFERENCE = "(no reference)"


def _ref_word(ref: str) -> str:
    """``ref`` as a line names it: :data:`NO_REFERENCE` for an empty one."""
    return ref if str(ref).strip() else NO_REFERENCE


class _FitError(ValueError):
    """A fit record the gate cannot read: the gate errors, naming it."""


def _shown_models(fp: list) -> list[str]:
    """The paths of the 3D models footprint ``fp`` shows (not hidden)."""
    shown = []
    for model in K.children(fp, "model"):
        hide = K.child(model, "hide")
        if (hide is not None and (len(hide) == 1 or str(hide[1]) == "yes")) or "hide" in model:
            continue
        shown.append(str(model[1]))
    return shown


def _parts_and_models(tree: list) -> tuple[list[str], dict[str, list[str]], list[str]]:
    """``(refs of placed parts, {ref: [visible 3D model paths]}, refs not asked
    for)``: the parts the fit must see, what the board says each one's body is,
    and the footprints it does not ask for (no type and no model). A part marked
    do-not-populate is not placed, and not asked for. A footprint that shows a
    model is a part whatever its type (:data:`_PART_ATTRS`); an SMD or
    through-hole footprint that shows none is one too, unless it is board-only.
    Two footprints of one ref (KiCad's unannotated ``REF**``) are one part,
    their models together: kicad-cli names both bodies by that ref."""
    parts: list[str] = []
    models: dict[str, list[str]] = {}
    unasked: list[str] = []
    for fp in K.children(tree, "footprint"):
        ref = K.footprint_ref(fp)[0]
        attr = [str(a) for a in (K.child(fp, "attr") or [])[1:]]
        if "dnp" in attr:
            continue
        shown = _shown_models(fp)
        typed = any(a in _PART_ATTRS for a in attr) and "board_only" not in attr
        if not shown and not typed:
            if ref not in unasked:
                unasked.append(ref)
            continue
        if ref not in models:
            parts.append(ref)
            models[ref] = []
        models[ref] += [m for m in shown if m not in models[ref]]
    return parts, models, [r for r in unasked if r not in models]


def _model_file(model: str, board_dir: str, dirs: dict | None = None) -> str | None:
    """The file a footprint's model path names on this host, or None when it is
    found nowhere here: ``${KIPRJMOD}`` is the board's folder, a variable the
    project names in ``dirs`` (:data:`MODEL_DIRS_KEY`, absolute folders) is that
    folder alone, KiCad's own 3D library variable is otherwise searched where its
    installers put it (:data:`STOCK_MODEL_DIRS`), and any other variable is found
    nowhere. A relative path is read from the board's folder. The file the path
    names is the one kicad-cli looks for, a VRML file too: a STEP beside a VRML
    name that is not there is never read (:data:`_SUBST_EXTS`).

    No variable's value is read from the environment: rho keys none, so a read
    makes the verdict opaque (``env:<NAME>``, never Fresh), and ``doctor``'s
    ``env-reads`` row holds the bundled packs to none (spec §3.17). What slipped
    through, in the merge of general-packs H2 after H3: H2 read ``os.environ``
    here for every variable but ``${KIPRJMOD}``, and test_doctor's zero-hits
    check, red on H2's base for firmware-ci's reads, went red again for this
    one. A host that sets ``KICAD<n>_3DMODEL_DIR`` to a folder of its own states
    that folder in ``pcb_model_dirs`` instead, where the key holds it; without
    it, kicad-cli may find a model this search does not, and a declared height
    stands in for it there (still one body). *Rejected:* ``os.path.expandvars``
    (the same read, out of the scan's sight)."""
    other: list[str] = []
    dirs = dirs or {}

    def var(m) -> str:
        name = m.group(1) or m.group(2)
        if name == "KIPRJMOD":
            return board_dir
        if name in dirs:
            return dirs[name]
        other.append(name)
        return "\0"

    expanded = _MODEL_VAR.sub(var, model)
    if other:
        if len(other) != 1 or not MODEL_DIR_VARS.match(other[0]):
            return None
        bases = [d for d in STOCK_MODEL_DIRS if os.path.isdir(d)]
        options = [expanded.replace("\0", base) for base in bases]
    else:
        options = [expanded]
    found = [p if os.path.isabs(p) else os.path.join(board_dir, p) for p in options]
    return next((p for p in found if os.path.isfile(p)), None)


def _model_dirs(ctx: GateContext) -> dict[str, str]:
    """``pcb_model_dirs`` (:data:`MODEL_DIRS_KEY`) as ``{variable: absolute
    folder}``, read through ``ctx``; {} when the project states none. Raises
    ``_FitError`` for a record the gate cannot read. A folder that is not on this
    host is not refused: a model there is found nowhere here, as one in a stock
    folder that is not installed."""
    raw = ctx.param(MODEL_DIRS_KEY)
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise _FitError(f"{MODEL_DIRS_KEY} is {raw!r}: give {{variable: folder}}, the folder "
                        f"from the project root or absolute, e.g. {{\"KICAD9_3DMODEL_DIR\": "
                        f"\"hardware/3dmodels\"}}")
    out: dict[str, str] = {}
    for name, folder in raw.items():
        at = f"{MODEL_DIRS_KEY}[{name!r}]"
        if not isinstance(name, str) or not _VAR_NAME.match(name):
            raise _FitError(f"{at}: a variable is named as a footprint's model path names it "
                            f"inside ${{...}}: letters, digits and '_'")
        if name == "KIPRJMOD":
            raise _FitError(f"{at}: KIPRJMOD is the board's own folder, which KiCad sets; it "
                            f"is not the project's to name")
        if not isinstance(folder, str) or not folder.strip():
            raise _FitError(f"{at} is {folder!r}: give the folder, from the project root or "
                            f"absolute")
        out[name] = os.path.normpath(os.path.abspath(K.project_path(ctx, folder)))
    return out


def _named_dirs(tree: list, dirs: dict[str, str]) -> bool:
    """Each model path in ``tree`` (a staged copy) that names a variable of
    ``dirs`` rewritten with that variable's folder, so kicad-cli reads the file
    :func:`_model_file` found, by its absolute path, and never resolves the
    variable from its own environment or configuration. Whether any path moved.
    Named by absolute path, as the declared-height boxes are: kicad-cli resolves
    ``${KIPRJMOD}`` only where a ``.kicad_pro`` sits beside the board."""
    moved = False

    def var(m) -> str:
        name = m.group(1) or m.group(2)
        return dirs[name] if name in dirs else m.group(0)

    for fp in K.children(tree, "footprint"):
        for model in K.children(fp, "model"):
            path = _MODEL_VAR.sub(var, str(model[1]))
            if path != str(model[1]):
                model[1] = K.Q(path)
                moved = True
    return moved


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
    """``{ref: {h, source, box_mm?}}``: the height above the board of a part
    whose 3D model is not on this host (none named, or none found), where the
    number comes from (a datasheet's seated height), and, for a part with no
    courtyard, its outline: ``box_mm`` ``[[x0, y0], [x1, y1]]`` in mm in the
    footprint's own frame (as KiCad's footprint editor shows it: x right, y
    down). (What slipped through, a holdout run: a part with no courtyard could
    take no height at all, so a board borrowed from another machine had to be
    edited to be fitted.)"""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise _FitError(f"{label} is not an object: give {{ref: {{h, source}}}}")
    out = {}
    for ref, rec in raw.items():
        at = f"{label}[{ref!r}]"
        _record(rec, at, ("h", "source"), ("box_mm",))
        h = _number(rec["h"], f"{at}.h")
        if not h > 0.0:
            raise _FitError(f"{at}.h is {h:g}: a height is above 0 mm")
        if not isinstance(rec["source"], str) or not rec["source"].strip():
            raise _FitError(f"{at}.source is empty: say where the height comes from")
        out[ref] = {"h": h, "source": rec["source"].strip()}
        if "box_mm" in rec:
            box = rec["box_mm"]
            if (not isinstance(box, list) or len(box) != 2
                    or not all(isinstance(c, list) and len(c) == 2 for c in box)):
                raise _FitError(f"{at}.box_mm is {box!r}: give [[x0, y0], [x1, y1]] in mm, "
                                f"in the footprint's own frame (x right, y down)")
            (x0, y0), (x1, y1) = ([_number(v, f"{at}.box_mm") for v in c] for c in box)
            if not (x0 < x1 and y0 < y1):
                raise _FitError(f"{at}.box_mm is {box!r}: each low corner must be below its "
                                f"high one")
            out[ref]["outline"] = (x0, y0, x1, y1)
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
    #: Footprints with no type and no model: not asked for, and named.
    unasked: list = dataclasses.field(default_factory=list)
    #: ``{ref: model path}`` for a part fitted at its declared height in place
    #: of a model found nowhere on this host.
    stand_in: dict = dataclasses.field(default_factory=dict)
    #: ``{ref: file}`` for a declared height not used: the model is found.
    unused: dict = dataclasses.field(default_factory=dict)
    #: ``{variable: absolute folder}``, the project's ``pcb_model_dirs``
    #: (:data:`MODEL_DIRS_KEY`): where a model path naming the variable is found.
    model_dirs: dict = dataclasses.field(default_factory=dict)

    def name(self, ref: str) -> str:
        ref = _ref_word(ref)
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
    ceiling = K.reader_ceiling(version) or K.tool_ceiling(version)
    if ceiling:
        return (f"{label}{ceiling}: a newer format is a limit of this reader, not a "
                f"defect in the board, so nothing on it is judged here")
    if version < K.MIN_BOARD_VERSION:
        raise _FitError(f"{label}file version {version} is older than "
                        f"{K.MIN_BOARD_VERSION} (KiCad 6): this reader finds footprints "
                        f"from KiCad 6 on")
    parts, models, unasked = _parts_and_models(tree)
    on_board = {K.footprint_ref(fp)[0] for fp in K.children(tree, "footprint")}
    board_dir = os.path.dirname(os.path.abspath(fit.board))

    def placed(ref: str, what: str, *, untyped: bool = False) -> None:
        if ref in parts or (untyped and ref in unasked):
            return
        why = ("the board does not place it (do not populate, or no type and no 3D model)"
               if ref in on_board else "the board has no such part")
        raise _FitError(f"{label}{what} names {ref}: {why}")

    for ref, rec in fit.heights.items():
        # A footprint with no type and no model takes a height too: a part
        # fitted by hand is often drawn so.
        placed(ref, "a declared height", untyped=True)
        if ref in fit.bodiless:
            raise _FitError(f"{label}{ref} is listed as bodiless and has a declared height: "
                            f"it has a body or it does not")
        # One body each: the model where this host has it, else the declared
        # height in its place. (What slipped through, a holdout run: a part
        # that named a model was refused a height, so a board copied from a
        # machine with other libraries could be fitted only by editing it.)
        # *Rejected:* the height over a found model (a stated number in place
        # of the part's drawn body); both at once (a union of the two, larger
        # than either, fails a sound board on one host and not another); the
        # old refusal (the only way past it was to edit the board).
        shown = models.get(ref, [])
        found = {m: _model_file(m, board_dir, fit.model_dirs) for m in shown}
        if any(found.values()):
            fit.unused[ref] = next(f for f in found.values() if f)
            continue
        if shown:
            fit.stand_in[ref] = shown[0]
        fp = next(f for f in K.children(tree, "footprint") if K.footprint_ref(f)[0] == ref)
        box = rec.get("outline") or _courtyard_box(fp)
        if box is None:
            raise _FitError(f"{label}{ref} has a declared height and no courtyard on its own "
                            f"side: give its outline as box_mm [[x0, y0], [x1, y1]] in the "
                            f"footprint's frame, or draw its courtyard")
        rec["box"] = box
    for ref in fit.contacts:
        if ref != BODY:
            # A footprint with no type and no model that takes a declared height
            # is fitted, so it may seat by design too. (What slipped through in
            # the review of H2: the height made it a part and the contact still
            # read "the board does not place it".)
            placed(ref, "a contact", untyped=ref in fit.heights)
            if ref in fit.bodiless:
                raise _FitError(f"{label}{ref} is listed as bodiless and has a contact: a "
                                f"part with no body touches nothing")
    fit.tree = tree
    boxed = [r for r in fit.heights if r not in fit.unused]
    fit.parts = [r for r in parts + [r for r in boxed if r not in parts]
                 if r not in fit.bodiless]
    fit.models = models
    fit.unasked = [r for r in unasked if r not in fit.parts and r not in fit.bodiless]
    unmodelled = sorted(r for r in fit.parts if not models.get(r) and r not in boxed)
    if unmodelled:
        # The parts whose named model is found nowhere here are named in the
        # same skip, so one run says all a person must state. (What slipped
        # through, a holdout run: the first skip named only the parts with no
        # model; a missing model surfaced after the export, a run later.) Held
        # to the 200 characters a skip reason keeps: at 207 the last choice,
        # pcb_enclosure_bodiless, was cut off (seen in the review of P7).
        lost = sorted(r for r in fit.parts if r not in boxed and models.get(r)
                      and not any(_model_file(m, board_dir, fit.model_dirs)
                                  for m in models[r]))
        # "name a model not found here", never "one not found here": the skip
        # read "19 one not found here" (holdout round 2).
        if lost:
            return (f"{label}{len(unmodelled)} part(s) name no 3D model "
                    f"({', '.join(map(_ref_word, unmodelled[:3]))}), {len(lost)} name a model "
                    f"not found here ({', '.join(map(_ref_word, lost[:3]))}): state each "
                    f"height in pcb_part_heights_mm, or list a bodiless part in "
                    f"pcb_enclosure_bodiless")
        return (f"{label}{len(unmodelled)} placed part(s) name no 3D model: "
                f"{', '.join(map(_ref_word, unmodelled[:8]))}. Give each a model, state its "
                f"height in pcb_part_heights_mm, or list a part with no body in "
                f"pcb_enclosure_bodiless")
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


def _region_refusals(fit: _Fit, parts: dict, enclosure_bbox,
                     limit: float) -> list[tuple[str, str, str]]:
    """Why each of ``fit``'s contact regions cannot stand as measured, as
    ``(error, detail, which)`` rows, none when every one stands. Each error is
    what to do, short enough for the 200 characters a verdict's error keeps; its
    detail has the boxes it read; ``which`` names the part and region.

    ``parts`` is the geometry's answer for the board (``_fit.py``, its ``fit``
    or its ``regions`` pass: each part with a region carries its box as placed,
    whether its regions leave any enclosure (``rest_empty``) and, for each
    region, the box of the enclosure material it took out). A region is refused
    when it holds the enclosure's whole box; when a part's regions together take
    out all of the enclosure (before the review of P7 such a part was left out
    of the verdict: judged against nothing); and when the material it takes out
    reaches more than :data:`REGION_REACH_LIMITS` of the claim's limits past the
    part's own box. Inside that reach a region is the person's statement of the
    feature: one drawn over two features excuses both.

    Every refusal is listed, not the first. (What slipped through, a holdout
    run: the check returned at the first refusal, so each region drawn too wide
    cost a full fit of two to four minutes to learn of the next.)"""
    out: list[tuple[str, str, str]] = []
    blo, bhi = enclosure_bbox
    reach = REGION_REACH_LIMITS * max(limit, 0.0)
    for ref, feats in sorted(fit.contacts.items()):
        regions = {f: rec["region_mm"] for f, rec in feats.items() if "region_mm" in rec}
        if not regions:
            continue
        who = fit.name(ref)
        whole = False
        for feat, (lo, hi) in sorted(regions.items()):
            if all(lo[i] <= blo[i] and hi[i] >= bhi[i] for i in range(3)):
                whole = True
                out.append((f"{who} against {feat}: its region holds the whole enclosure, so "
                            f"it excuses the part against anything. Draw the region around "
                            f"the feature it touches",
                            f"the region is {_xyz(lo)} to {_xyz(hi)} mm; the enclosure's box "
                            f"is {_xyz(blo)} to {_xyz(bhi)} mm", f"{who} against {feat}"))
        if whole:
            continue
        row = (parts.get(ref) or {}).get("enclosure")
        if row is None:
            continue                # not in the export: named as missing, not judged
        empty = row["rest_empty"] if "rest_empty" in row else row.get("clearance_mm") is None
        if empty:
            out.append((f"{who}: its regions ({', '.join(sorted(regions))}) take out all of "
                        f"the enclosure, so they excuse it against anything. Draw each region "
                        f"around the feature it touches",
                        "; ".join(f"{feat} is {_xyz(lo)} to {_xyz(hi)} mm"
                                  for feat, (lo, hi) in sorted(regions.items())),
                        f"{who}'s regions"))
            continue
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
                out.append((f"{who} against {feat}: the region holds no material within "
                            f"{reach:g} mm of {ref}'s own box ({REGION_REACH_LIMITS:g} times "
                            f"the claim's limit), so it excuses nothing. Remove it, or draw it "
                            f"where {ref} touches", detail, f"{who} against {feat}"))
                continue
            out.append((f"{who} against {feat}: the region reaches more than {reach:g} mm "
                        f"past {ref}'s own box ({REGION_REACH_LIMITS:g} times the claim's "
                        f"limit). Draw it within {json.dumps([clo, chi])}", detail,
                        f"{who} against {feat}"))
    return out


def _region_refusal(fit: _Fit, parts: dict, enclosure_bbox,
                    limit: float) -> tuple[str, str] | None:
    """The first of :func:`_region_refusals`, or None."""
    found = _region_refusals(fit, parts, enclosure_bbox, limit)
    return found[0][:2] if found else None


def _refused(gid: str, refusals: list[tuple[str, str, str]], ev: list[str],
             out_dir: str) -> Verdict:
    """The error for every region refused, in one verdict: the first refusal's
    words as the error and its boxes as the detail, then each other refusal
    named, and an evidence file with every refusal's error (the box to draw)
    and its boxes. Each fits what a verdict keeps: an error 200 characters, a
    detail 500."""
    path = os.path.join(out_dir, "regions_refused.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump([{"error": e, "detail": d} for e, d, _w in refusals], fh, indent=2)
    error, detail, _which = refusals[0]
    if len(refusals) > 1:
        detail = (f"{len(refusals)} contact regions refused, each with its box to draw in "
                  f"regions_refused.json: {', '.join(w for _e, _d, w in refusals)}; first: "
                  f"{detail}")
    return Verdict(gate=gid, passed=False, evidence=[path, *ev], error=error, detail=detail)


def _fit_timeout(ctx: GateContext, parts: int) -> tuple[float, str]:
    """``(seconds, how they were chosen)`` for one helper call over ``parts``
    parts: ``pcb_fit_timeout_s`` when the project states it, else
    :data:`DEFAULT_FIT_TIMEOUT_S` and :data:`FIT_TIMEOUT_PER_PART_S` a part."""
    stated = ctx.param("pcb_fit_timeout_s")
    if stated:
        return float(stated), f"{parts} part(s), pcb_fit_timeout_s"
    seconds = DEFAULT_FIT_TIMEOUT_S + FIT_TIMEOUT_PER_PART_S * parts
    return seconds, (f"{parts} part(s), the default {DEFAULT_FIT_TIMEOUT_S:g} s and "
                     f"{FIT_TIMEOUT_PER_PART_S:g} s a part: set pcb_fit_timeout_s")


def _helper(mode: str, request: str, cwd: str, timeout: float, log: str, how: str = ""):
    """``_fit.py <mode> <request>``: ``(parsed answer, None)`` or ``(None, why)``
    with ``why`` ``("skip"|"error", text)``. A timeout names the parts the call
    held and how its time was chosen, so a person can choose another. (What
    slipped through, a holdout run: the skip named the key and no count, and
    the default was fitted to the baseline's two parts.)"""
    try:
        geo = subprocess.run([sys.executable, FIT_HELPER, mode, request], capture_output=True,
                             text=True, timeout=timeout, stdin=subprocess.DEVNULL, cwd=cwd)
    except subprocess.TimeoutExpired:
        return None, ("skip", f"the fit geometry ({mode}) ran {timeout:g} s ("
                              f"{how or 'pcb_fit_timeout_s'}) and was killed: nothing was "
                              f"shown")
    with open(log, "a", encoding="utf-8") as fh:
        fh.write(f"\n_fit.py {mode} exit {geo.returncode}\n{geo.stdout}\n{geo.stderr}\n")
    lines = [ln for ln in geo.stdout.splitlines() if ln.startswith("{")]
    if geo.returncode != 0 or not lines:
        tail = (geo.stderr or geo.stdout).strip().splitlines()[-1:] or ["no output"]
        return None, ("error", f"the fit geometry failed (exit {geo.returncode}): "
                               f"{tail[0][:200]}")
    return json.loads(lines[-1]), None


def _project_model(model: str) -> str | None:
    """The path inside the board's folder that a model names, or None for one
    outside it: ``${KIPRJMOD}/x`` (or KiCad 5's ``$(KIPRJMOD)/x``) and a relative
    path, which KiCad reads from the project's folder."""
    for prefix in ("${KIPRJMOD}/", "$(KIPRJMOD)/"):
        if model.startswith(prefix):
            return model[len(prefix):]
    if "$" in model or os.path.isabs(model):
        return None
    return model


def _export(ctx: GateContext, gid: str, fit: _Fit, timeout: float, how: str = ""):
    """Stage ``fit``'s board, give each part fitted at a declared height its box
    as its model, and export the populated board as STEP. ``(step, log)`` or
    ``(None, ("skip"|"error", text, evidence))``."""
    staged = _stage(ctx, f"{gid}.{fit.id}" if fit.id else gid, fit.board,
                    board=True, schematic=False)
    if isinstance(staged, Verdict):
        return None, ("error", staged.error or staged.detail, staged.evidence)
    stem, stage = staged
    # Every part's model kept with the project goes to the stage, a part of
    # any type among them (_parts_and_models), and a VRML model's STEP twin
    # with it, which --subst-models reads in place of a VRML file that is there.
    src_dir = os.path.dirname(os.path.abspath(fit.board))
    for ref in fit.parts:
        for model in fit.models.get(ref, []):
            rel_model = _project_model(model)
            if rel_model is None:
                continue
            stem_m, ext = os.path.splitext(rel_model)
            for name in ([rel_model] + ([stem_m + e for e in _SUBST_EXTS]
                                        if ext.lower() == ".wrl" else [])):
                src = os.path.join(src_dir, name)
                if os.path.isfile(src):
                    dst = os.path.join(stage, name)
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    shutil.copyfile(src, dst)
    log = os.path.join(stage, "fit.log")
    with open(log, "w", encoding="utf-8") as fh:
        fh.write(f"fit {fit.key}: {fit.board}\n")
    boxed = {r: rec for r, rec in fit.heights.items() if r not in fit.unused}
    # A copy: the loaded tree stays as read. A model path that names a variable
    # the project gave a folder (pcb_model_dirs) names that folder here, so
    # kicad-cli reads the file _model_file found (_named_dirs).
    tree = K.parse(K.dump(fit.tree)) if boxed or fit.model_dirs else None
    if tree is not None and _named_dirs(tree, fit.model_dirs) and not boxed:
        with open(stem + ".kicad_pcb", "w", encoding="utf-8") as fh:
            fh.write(K.dump(tree) + "\n")
    if boxed:
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
        boxes = []
        for n, (ref, rec) in enumerate(sorted(boxed.items())):
            x0, y0, x1, y1 = rec["box"]
            name = f"{n:03d}_{re.sub(r'[^A-Za-z0-9_-]', '_', ref)}.step"
            path = os.path.abspath(os.path.join(stage, HEIGHT_DIR, name))
            boxes.append({"ref": ref, "path": path,
                          "lo": [x0, -y1, 0.0], "hi": [x1, -y0, rec["h"]]})
            fp = next(f for f in K.children(tree, "footprint") if K.footprint_ref(f)[0] == ref)
            # A model found nowhere here leaves the staged copy: the box is the
            # part's one body, on whatever host kicad-cli could find more.
            fp[:] = [x for x in fp if not (isinstance(x, list) and K.head(x) == "model")]
            fp.append(["model", K.Q(path),
                       ["offset", ["xyz", "0", "0", "0"]], ["scale", ["xyz", "1", "1", "1"]],
                       ["rotate", ["xyz", "0", "0", "0"]]])
        with open(stem + ".kicad_pcb", "w", encoding="utf-8") as fh:
            fh.write(K.dump(tree) + "\n")
        request = os.path.join(stage, "boxes_request.json")
        with open(request, "w", encoding="utf-8") as fh:
            json.dump({"boxes": boxes}, fh, indent=2)
        _answer, why = _helper("boxes", request, stage, timeout, log, how)
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
        return None, ("skip", f"kicad-cli pcb export step ran {timeout:g} s ("
                              f"{how or 'pcb_fit_timeout_s'}) and was killed", [log])
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
    claims=["pcb", "enclosure-fit"],
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
    part reads clear. So every placed part (SMD or through-hole, or any footprint
    that shows a model, whatever its type; not DNP) must name a model and arrive
    in the export under its reference, or have a declared height
    (``pcb_part_heights_mm``: its courtyard's box, or the outline it states,
    raised to that height, stands in for its body where its model is found
    nowhere on this host), or the gate skips naming it. A part that truly has
    no body above the board (an antenna drawn in copper) goes in
    ``pcb_enclosure_bodiless``, by reference, a recorded statement. A footprint
    with no type and no model is not asked for, and the verdict names it.

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
        if not isinstance(fits, Verdict):
            dirs = _model_dirs(ctx)
            for fit in fits:
                fit.model_dirs = dirs
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
    ev: list[str] = []
    exported = {}
    for fit in ready:
        done, why = _export(ctx, gid, fit, *_fit_timeout(ctx, len(fit.parts)))
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
        timeout, how = _fit_timeout(ctx, sum(len(f.parts) for f, _s, _l in members))
        ev += [log for _f, _s, log in members]
        # The regions first, on their own: each part's box as placed and the
        # material each region takes out, without the distances and shared
        # volumes of every part. A refused region then costs the export and
        # this pass, not the full fit. (What slipped through, a holdout run:
        # the regions were checked only after the full fit, two to four minutes
        # on a populated board, for each region drawn too wide.)
        if any(b["regions"] for b in boards):
            early, why = _helper("regions", request, first_stage, timeout, members[0][2], how)
            if why is not None:
                (skips if why[0] == "skip" else errors).append(why[1])
                continue
            refusals = [r for fit, _s, _l in members
                        for r in _region_refusals(fit, early["boards"][fit.key]["parts"],
                                                  early["enclosure_bbox"], acc.limit)]
            if refusals:
                return _refused(gid, refusals, ev, first_stage)
        result, why = _helper("fit", request, first_stage, timeout, members[0][2], how)
        if why is not None:
            (skips if why[0] == "skip" else errors).append(why[1])
            continue
        report = os.path.join(first_stage, "fit.json")
        with open(report, "w", encoding="utf-8") as fh:
            json.dump(result, fh, indent=2, sort_keys=True)
        ev = [report, *ev, *(step for _f, step, _l in members)]
        names = {fit.key: fit for fit, _s, _l in members}
        refusals = [r for fit, _s, _l in members
                    for r in _region_refusals(fit, result["boards"][fit.key]["parts"],
                                              result["enclosure_bbox"], acc.limit)]
        if refusals:
            return _refused(gid, refusals, ev, first_stage)
        for fit, _step, _log in members:
            label = f"fit {fit.id}: " if fit.id else ""
            measured_parts = result["boards"][fit.key]["parts"]
            # A board with a part missing from its export is still judged on what
            # arrived, and named as not judged. A shape that is there can only come
            # nearer to something when a missing one is added, never farther, so
            # its collision or its gap under the limit is a fail; its pass is not
            # one. (What slipped through in P7: a pair of boards is measured once,
            # under the first, and a board with a missing part was dropped with its
            # pair rows, so the same records read fail or skip by the order of
            # their keys.)
            missing = sorted(r for r in fit.parts if r not in measured_parts)
            boxed = [r for r in missing if r in fit.heights and r not in fit.unused]
            unfound = [r for r in missing if r not in boxed]
            if boxed:
                errors.append(f"{label}the declared-height box of {', '.join(boxed[:8])} did "
                              f"not reach kicad-cli's export")
            if unfound:
                # Every part here names a model: one with neither skipped before the
                # export. Where this host has no file its model names, a declared
                # height stands in for it (_read_board), so the advice is to state
                # one. Where it has the file and kicad-cli left the part out all the
                # same, a height is named unused beside the model, so the advice is
                # to hide the model. (What slipped through in P7: the advice was to
                # state a height alone, which the next run refused; and at 241
                # characters, past the 200 a skip reason keeps, that advice was cut
                # off before it was shown. In the review of H2: the advice still
                # said to hide a model after a height had come to stand in for a
                # missing one, and a part whose height was stated was told to state
                # one.)
                board_dir = os.path.dirname(os.path.abspath(fit.board))
                here = [r for r in unfound if r in fit.unused
                        or any(_model_file(m, board_dir, fit.model_dirs)
                               for m in fit.models.get(r, []))]
                gone = [r for r in unfound if r not in here]
                if gone:
                    skips.append(f"{label}kicad-cli left out {', '.join(gone[:8])}: it did "
                                 f"not find their 3D models. Install KiCad's 3D library, keep "
                                 f"each model beside the board, or state a height in "
                                 f"pcb_part_heights_mm")
                if here:
                    skips.append(f"{label}kicad-cli left out {', '.join(here[:8])}: their "
                                 f"model files are here and did not load. Hide each model, "
                                 f"and state its height in pcb_part_heights_mm if it has none")
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
    # Decided on the computed gap, reported rounded down to a micrometre
    # (``_kicad.toward_fail``). What slipped through (holdout round 2, in
    # pcb.net_width, and the same pattern here): the gap was rounded to the
    # nearest micrometre and then compared, so a 0.4996 mm gap read 0.5 and
    # passed ``>= 0.5 mm``.
    least = min(d for d, _w, _a in judged) if judged else 0.0
    gap = K.toward_fail(least, ">=", acc.limit, 3) if judged else 0.0
    failed = bool(hits) or (bool(judged) and not acc.holds(least))
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
    # What the pass covers is said beside it: a part fitted at its declared
    # height in place of a model this host lacks, a height not used because the
    # model is here, and a footprint the fit did not ask for (no type, no
    # model). (What slipped through, a holdout run: a display the fit never
    # asked for was neither counted nor named, under "nothing collides".)
    stood = [f.name(r) for f, _s, _l in exported.values() for r in sorted(f.stand_in)]
    if stood:
        detail += (f"; at its declared height, its model not found here: "
                   f"{', '.join(stood[:6])}")
    unused = [f.name(r) for f, _s, _l in exported.values() for r in sorted(f.unused)]
    if unused:
        detail += f"; declared height not used, its model found: {', '.join(unused[:6])}"
    unasked = [f.name(r) for f, _s, _l in exported.values() for r in f.unasked]
    if unasked:
        detail += (f"; not fitted (no type, no 3D model): {', '.join(unasked[:6])}"
                   + (f" and {len(unasked) - 6} more" if len(unasked) > 6 else ""))
    if skips or errors:
        detail += f"; not judged: {'; '.join([*skips, *errors])}"
    return Verdict(gate=gid, passed=not failed, measured=gap, limit=acc.limit,
                   comparator=">=", units="mm", detail=detail, evidence=ev)


# --------------------------------------------------------------------------- #
# the antenna's keep-out
# --------------------------------------------------------------------------- #
KEEPOUT_FILL_HELPER = os.path.join(_HERE, "_keepout_fill.py")

#: The pairs of releases ``pcb.antenna_keepout`` was measured on, by the pcbnew
#: release that refills: one file per release,
#: ``gates/pcbnew-pairs/<release>.json``, holding the kicad-cli releases the gate
#: was measured beside under it (``kicad_cli``), the day and the host, and what
#: was shown there. The gate runs only on a pair a record names
#: (:func:`_keepout_pair`), and asks for the record of the running pcbnew by its
#: path, found or not, so the spine keys the pcbnew release as
#: :data:`RELEASES_DIR` keys kicad-cli's.
#:
#: **A pair, not a release.** The gate hands one board to two KiCad programs:
#: pcbnew refills it, kicad-cli judges both copies, and the gate ties each finding
#: to its part by the name kicad-cli writes for the area. A release of either can
#: move what the gate reads. What slipped through, in the merge of general-packs H1
#: and H2: with kicad-cli 10.0.8 (the kicad/kicad:10.0 image) beside pcbnew 9.0.9,
#: a chip antenna's keep-out with a GND track and an RF track in it read pass at 0,
#: where kicad-cli 9.0.9 beside the same pcbnew reads fail at 2. Measured on the
#: staged copies: kicad-cli 10.0.8 writes "Items not allowed" for a track in the
#: added area and names no area (9.0.9: "Items not allowed (keepout area
#: 'nopekit_antenna_AE1')"); it names the area for a pad, a via and a zone fill. The
#: stored copy, which pcbnew never touches, read the same, and so did the image's
#: own pair, pcbnew 10.0.8 beside kicad-cli 10.0.8: the cause is kicad-cli 10.0.8's
#: words, and no pair with it was measured. The selftest under kicad-cli 10.0.8
#: still read qualified: its control is a zone fill, whose words name the area.
#: *Rejected:* any pair of one release (10.0.8 beside 10.0.8 read the same false
#: pass); a series (10.0.x), as a release record's formats stand for its series (a
#: patch release can move the words, and the words are what this gate reads:
#: ``fdm.slice`` skips every OrcaSlicer release it was not measured on for the same
#: reason); the pairs in the kicad-cli records (the pcbnew release would then be no
#: path the gate asks for, so a control under two pcbnew releases would land on one
#: key); the two releases read after the refill and the first DRC, from the child's
#: line and the report (no extra launch, but a pair never measured has then refilled
#: and judged the board, and its crash reads as the gate's error, louder than the
#: cause). The cost: one ``kicad-cli version`` (0.1 s here, 0.2 to 0.9 s through the
#: image) and one child that imports pcbnew (0.8 s) on each run that judges a board.
#:
#: **The folder's name** is no name Python can import. The refill's child runs
#: ``_keepout_fill.py`` as a script, so this folder's parent is the first entry of
#: its path. What slipped through, in review of general-packs R1: the folder was
#: ``gates/pcbnew/``, and where the child's own path held no pcbnew, ``import
#: pcbnew`` loaded the folder as a namespace package, so the gate's error read
#: "module 'pcbnew' has no attribute 'Version'" where the cause is that no pcbnew
#: is there. The child now also drops its own folder from its path
#: (``_keepout_fill.py``). *Rejected:* the guard in the child alone (another
#: script in ``gates/`` that imports pcbnew would still load the folder); a name
#: that is an identifier, such as ``keepout_pairs`` (nothing imports that name
#: today, but a folder an import can load is the same trap for the next module
#: given its name).
PCBNEW_DIR = os.path.join(_HERE, "pcbnew-pairs")

#: The keys of a pcbnew pair record.
PCBNEW_KEYS = ("release", "kicad_cli", "measured", "on", "shown")

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
ANTENNA_OPTIONAL = ("board", "own_nets")
ANTENNA_DRAWN = ("drawing", "rule_area")

#: How a ``rule_area`` keep-out picks the designer's areas, beside its name:
#: ``{"uuid": id}``, one area by its id, and ``{"covers": ref}``, every rule area
#: on the board whose outline holds a point of that part's copper (a pad's
#: centre, a corner of a copper drawing): a printed antenna's keep-outs, drawn
#: unnamed around it. What slipped through (a holdout run): a printed
#: inverted-F antenna on a bare SoC kept its board clear with five unnamed rule
#: areas, and a rule area was found by its name alone, so no record could name
#: them. *Rejected:* every rule area on the board (a connector's or a card
#: slot's keep-out guards no antenna); an area that merely comes near the part
#: (the strips that keep an inner pour off a feed line beside the antenna are
#: no keep-out of the antenna itself).
RULE_AREA_PICKS = ("uuid", "covers")

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
    own = rec.get("own_nets", [])
    if not isinstance(own, list):
        raise P.RecordError(f"{label}.own_nets is {own!r}: give the antenna's own nets, "
                            f"[\"FEED\", \"GND\"]")
    out["own_nets"] = [P.text(n, f"{label}.own_nets[{i}]") for i, n in enumerate(own)]
    form = rec["keepout"]
    if isinstance(form, dict):
        P.keys(form, f"{label}.keepout", (), ANTENNA_DRAWN)
        if len(form) != 1:
            raise P.RecordError(f"{label}.keepout names {len(form)} of {', '.join(ANTENNA_DRAWN)}: "
                                f"give one")
        (kind, where), = form.items()
        if kind == "rule_area" and isinstance(where, dict):
            P.keys(where, f"{label}.keepout.rule_area", (), RULE_AREA_PICKS)
            if len(where) != 1:
                raise P.RecordError(f"{label}.keepout.rule_area: give one of "
                                    f"{', '.join(RULE_AREA_PICKS)}")
            (pick, value), = where.items()
            out["keepout"] = (kind, (pick, P.text(value, f"{label}.keepout.rule_area.{pick}")))
        else:
            out["keepout"] = (kind, P.text(where, f"{label}.keepout.{kind}"))
    elif form in ANTENNA_FORMS:
        out["keepout"] = (form, "")
    else:
        raise P.RecordError(f"{label}.keepout is {form!r}: give \"footprint\", \"none\", "
                            f"{{\"drawing\": <path>}} or {{\"rule_area\": <name>}} "
                            f"({{\"rule_area\": {{\"uuid\": id}}}} or "
                            f"{{\"rule_area\": {{\"covers\": ref}}}})")
    return out


def _part_copper_points(fp: list) -> list[tuple[float, float]]:
    """Points of footprint ``fp``'s own copper in the board's frame: each pad's
    centre, and each corner of a copper drawing (a printed antenna's arms)."""
    place = _footprint_to_board_raw(fp)
    pts = [place((float(K.child(pad, "at")[1]), float(K.child(pad, "at")[2])))
           for pad in K.children(fp, "pad") if K.child(pad, "at") is not None]
    for item in fp:
        if not isinstance(item, list) or K.head(item) not in _COURTYARD_ITEMS:
            continue
        if not str(K.value(item, "layer", default="")).endswith(".Cu"):
            continue
        for key in ("start", "end", "center", "mid"):
            node = K.child(item, key)
            if node is not None:
                pts.append(place((float(node[1]), float(node[2]))))
        for pt in K.children(K.child(item, "pts") or [], "xy"):
            pts.append(place((float(pt[1]), float(pt[2]))))
    return pts


def _antenna_own_items(tree: list, board, fp: list, nets: list[str]) -> set[str]:
    """The ids of the tracks and vias that are the antenna's own copper: on a
    net the record names (``own_nets``), on a layer of the part's own pads, and
    joined to those pads through such tracks alone; and each via of those nets
    on such a track. A zone's fill is never the antenna's own, nor is anything
    beyond a via or beyond another part's pad: two tracks that both reach one
    via, or one pad of another part, are not joined through each other. What
    slipped through (a holdout run): a printed antenna's feed and ground arm
    are tracks, and the added area forbids every track, so a sound inverted-F
    antenna read as findings of itself. And in the review of H2: two tracks
    that end on one via touch each other there, so the search went on through
    the ground arm's via, and through a decoupling capacitor's ground pad, and
    excused a ground track that ran on, on the same layer, under the radiating
    arm. *Rejected:* every item of a named net (a ground track that crosses
    under the antenna, or a ground pour, would be excused by the ground arm's
    net); the net alone without the copper (the same); a stop at a via of the
    named nets only (a via of another net there is a short, which DRC names,
    and the search must not run on through it either)."""
    wanted = set(nets)
    if not wanted:
        return set()
    copper = _copper_layers(tree) or ["F.Cu", "B.Cu"]
    place = _footprint_to_board_raw(fp)
    pads = []
    for pad in K.children(fp, "pad"):
        if K._net_name(K.child(pad, "net"), board.nets) not in wanted:
            continue
        size = K.child(pad, "size")
        r = math.hypot(float(size[1]), float(size[2])) / 2.0 if size is not None else 0.0
        at = K.child(pad, "at")
        pads.append((place((float(at[1]), float(at[2]))), r,
                     _layers_of((K.child(pad, "layers") or [])[1:], copper)))
    layers = set().union(*(p[2] for p in pads)) if pads else set()
    tracks = []
    for item in list(K.children(tree, "segment")) + list(K.children(tree, "arc")):
        if (K._net_name(K.child(item, "net"), board.nets) in wanted
                and str(K.value(item, "layer", default="")) in layers):
            pts = [(float(K.child(item, k)[1]), float(K.child(item, k)[2]))
                   for k in ("start", "mid", "end") if K.child(item, k) is not None]
            tracks.append((str(K.value(item, "uuid") or K.value(item, "tstamp") or ""),
                           str(K.value(item, "layer")), list(zip(pts, pts[1:])),
                           float(K.value(item, "width", default=0.0)) / 2.0))
    # Where the search stops: every via, of any net, read on every layer (a
    # blind via spans fewer, and reading it on all is the side that excuses
    # less), and every pad of every other part, each as a circle of its half
    # diagonal, the reach this function gives the part's own pads.
    stops = []
    for via in K.children(tree, "via"):
        at = K.child(via, "at")
        if at is not None:
            stops.append(((float(at[1]), float(at[2])),
                          float(K.value(via, "size", default=0.0)) / 2.0, set(copper)))
    for other in K.children(tree, "footprint"):
        if other is fp:
            continue
        oplace = _footprint_to_board_raw(other)
        for pad in K.children(other, "pad"):
            at, size = K.child(pad, "at"), K.child(pad, "size")
            if at is None:
                continue
            r = math.hypot(float(size[1]), float(size[2])) / 2.0 if size is not None else 0.0
            stops.append((oplace((float(at[1]), float(at[2]))), r,
                          _layers_of((K.child(pad, "layers") or [])[1:], copper)))

    def joined(lines, half, olines, ohalf, layer) -> bool:
        if not any(_seg_seg(*a, *b) <= half + ohalf for a in lines for b in olines):
            return False
        return not any(layer in sl
                       and any(_seg_point(c, *ln) <= half + r for ln in lines)
                       and any(_seg_point(c, *ln) <= ohalf + r for ln in olines)
                       for c, r, sl in stops)

    found: set[int] = set()
    todo = [i for i, (_u, layer, lines, half) in enumerate(tracks)
            if any(layer in pl and _seg_point(c, *ln) <= half + r
                   for c, r, pl in pads for ln in lines)]
    while todo:
        i = todo.pop()
        if i in found:
            continue
        found.add(i)
        _u, layer, lines, half = tracks[i]
        for j, (_v, other, olines, ohalf) in enumerate(tracks):
            if j not in found and other == layer and joined(lines, half, olines, ohalf,
                                                            layer):
                todo.append(j)
    # As kicad-cli names each item in its report (_kicad.kiid).
    out = {K.kiid(tracks[i][0]) for i in found} - {None}
    for via in K.children(tree, "via"):
        if K._net_name(K.child(via, "net"), board.nets) not in wanted:
            continue
        at = K.child(via, "at")
        c = (float(at[1]), float(at[2]))
        r = float(K.value(via, "size", default=0.0)) / 2.0
        if any(_seg_point(c, *ln) <= tracks[i][3] + r for i in found for ln in tracks[i][2]):
            uid = K.kiid(K.value(via, "uuid") or K.value(via, "tstamp"))
            if uid:
                out.add(uid)
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
    ceiling = K.reader_ceiling(version) or K.tool_ceiling(version)
    if ceiling:
        raise P.ReaderLimit(f"the board {name}: {ceiling}")
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
    if w["of_limit"] > 1.0:
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


def _pcbnew_release(gid: str):
    """``(release, "")`` of the pcbnew the refill imports (``_keepout_fill.py
    --version``, in a child under the same Python, as the refill runs), or
    ``("", why it named none)``. A child that exits other than 0 is an error
    Verdict: a pcbnew that cannot be imported cannot refill, and that crash read
    as an error before this question was asked (:func:`_refill`)."""
    try:
        proc = subprocess.run([sys.executable, KEEPOUT_FILL_HELPER, "--version"],
                              capture_output=True, text=True, timeout=VERSION_TIMEOUT_S,
                              stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return "", f"pcbnew's version ran past {VERSION_TIMEOUT_S:g} s"
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout).strip().splitlines()[-1:] or ["no output"]
        return Verdict(gate=gid, passed=False,
                       error=f"pcbnew could not name its release (_keepout_fill.py --version "
                             f"exited {proc.returncode}): {tail[0][:120]}")
    release = _release_of(proc.stdout)
    return release, "" if release else f"it printed {proc.stdout.strip()[:40]!r}"


def _pair_records(ctx: GateContext) -> dict[str, dict]:
    """``{pcbnew release: record}`` of every record in :data:`PCBNEW_DIR`, each
    read through ``ctx`` (so a record edited moves the key). Raises ValueError on
    a record not of the shape :data:`PCBNEW_KEYS` names."""
    try:
        names = sorted(n for n in os.listdir(PCBNEW_DIR) if n.endswith(".json"))
    except FileNotFoundError:
        names = []
    out: dict[str, dict] = {}
    for name in names:
        record = ctx.load_file(os.path.join(PCBNEW_DIR, name), loader=_load_json)
        if (not isinstance(record, dict) or sorted(record) != sorted(PCBNEW_KEYS)
                or f"{record['release']}.json" != name
                or not isinstance(record["kicad_cli"], list)
                or not all(isinstance(r, str) and _RELEASE.fullmatch(r)
                           for r in record["kicad_cli"])):
            raise ValueError(f"gates/pcbnew-pairs/{name} is not a pair record: it holds "
                             f"exactly {list(PCBNEW_KEYS)}, its release as its name, "
                             f"kicad_cli a list of releases")
        out[record["release"]] = record
    return out


def _keepout_pair(ctx: GateContext, gid: str) -> Verdict | None:
    """None when the kicad-cli on PATH and the pcbnew the refill imports are a
    pair a record in :data:`PCBNEW_DIR` names. Otherwise a skip naming both
    releases and the pairs measured, never a run: a pair never measured may write
    words this gate does not read, and a finding it cannot read is a pass it did
    not earn (:data:`PCBNEW_DIR`). An error for a pcbnew that cannot be imported
    or a record not of its shape. Asked once, before any board is staged."""
    cli, cli_why = _cli_release()
    pcb = _pcbnew_release(gid)
    if isinstance(pcb, Verdict):
        return pcb
    pcbnew, pcb_why = pcb
    try:
        records = _pair_records(ctx)
    except ValueError as exc:
        return Verdict(gate=gid, passed=False, error=str(exc))
    # Each release asked for by its record's path, found or not: what keys it.
    if cli:
        _release_record(ctx, cli, load=False)
    if pcbnew:
        os.path.isfile(os.path.join(PCBNEW_DIR, pcbnew + ".json"))
    if cli and cli in records.get(pcbnew, {}).get("kicad_cli", ()):
        return None
    pairs = [f"kicad-cli {c} with pcbnew {p}"
             for p in sorted(records, key=_release_key) for c in records[p]["kicad_cli"]]
    return _skip(gid, f"kicad-cli {cli or f'(no release: {cli_why})'} beside pcbnew "
                      f"{pcbnew or f'(no release: {pcb_why})'}: not a pair this gate was "
                      f"measured on ({'; '.join(pairs) or 'none'}; gates/pcbnew-pairs/)")


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
                      bare: frozenset | set = frozenset(),
                      mine: dict[str, set] | None = None) -> tuple[list, list, list]:
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
    whether the pad has a ring.) An item among the part's own tracks and vias
    (``mine``, :func:`_antenna_own_items`) is its own copper too. On a staged
    copy no other area exists, so an item named by none of ``owners`` is an
    error there (:func:`_antenna_untied`), never passed over."""
    found, own, holes = [], [], []
    mine = mine or {}
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
            if (re.search(rf"(?<!\w)of {re.escape(ref)}(?!\w)", words)
                    or row[0][0] in mine.get(ref, ())):
                own.append(row)
            elif row[0][0] in bare:
                holes.append(row)
            else:
                found.append(row)
    return found, own, holes


def _antenna_untied(doc: dict, owners: dict[str, str]) -> list[str]:
    """The words of each item a DRC report finds not allowed in a keep-out whose
    violation names none of this gate's areas (``owners``). On a staged copy,
    which holds no keep-out but the added areas (:func:`_without_keepouts`),
    every such item is in one of them, and :func:`_antenna_findings` cannot say
    which part's. What slipped through, in the merge of general-packs H1 and H2:
    :func:`_antenna_findings` passed over such an item as another area's, and
    kicad-cli 10.0.8 names no area for a track ("Items not allowed"), so every
    track in a keep-out was dropped and a board with two read pass at 0. The pair
    check (:func:`_keepout_pair`) keeps 10.0.8 from running here at all; this
    keeps a measured pair that writes new words from passing on them."""
    out: list[str] = []
    for v in doc.get("violations", []):
        if v.get("type") != "items_not_allowed":
            continue
        named = re.search(r"keepout area '([^']*)'", str(v.get("description", "")))
        if named and named.group(1) in owners:
            continue
        out += ([str(item.get("description", "")) for item in v.get("items", [])]
                or [str(v.get("description", ""))])
    return out


def _antenna_runs(ctx: GateContext, gid: str, n: int, path: str, tree: list,
                  copper: list[str], areas: dict[str, list], mine: dict | None = None):
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
        untied = _antenna_untied(doc, owners)
        if untied:
            return Verdict(gate=gid, passed=False, evidence=ev,
                           error=f"{_release(ctx, doc)} named no keep-out area for "
                                 f"{len(untied)} item(s) not allowed ({untied[0][:40]!r}, "
                                 f"the {copy} copy): only this gate's areas are on the "
                                 f"copy, and no finding is dropped")
        hits, theirs, open_holes = _antenna_findings(doc, owners, bare, mine)
        for key, ref, words in hits:
            found.setdefault(key, {"ref": ref, "item": words, "copies": []})["copies"].append(copy)
        for key, ref, words in theirs:
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

    **Which releases.** Only a pair of kicad-cli and pcbnew releases a record in
    ``gates/pcbnew-pairs/`` names (:data:`PCBNEW_DIR`, :func:`_keepout_pair`), asked
    before any board is staged; any other pair skips, naming both. An item not
    allowed in a keep-out that names none of this gate's areas is an error
    (:func:`_antenna_untied`). What slipped through, in the merge of H1 and H2:
    kicad-cli 10.0.8 names no area for a track, and a board with two tracks in
    the keep-out read pass at 0.

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
                    rule_areas = [z for z in K.children(tree, "zone")
                                  if _keepout_flags(z) is not None]
                    if isinstance(where, tuple) and where[0] == "uuid":
                        wanted = K.kiid(where[1])
                        zones = [z for z in rule_areas if wanted is not None and K.kiid(
                            K.value(z, "uuid") or K.value(z, "tstamp")) == wanted]
                        said = f"no rule area with uuid {where[1]}"
                        what = short = f"rule area {where[1][:8]}"
                    elif isinstance(where, tuple):
                        covered = fps.get(where[1])
                        if covered is None:
                            raise P.RecordError(f"{rec['label']}: {bname} has no {where[1]} for "
                                                f"a rule area to cover")
                        points = _part_copper_points(covered)
                        zones = [z for z in rule_areas if any(
                            _inside(pt, [tuple(q) for q in _polygon_points(z)])
                            for pt in points)]
                        said = f"no rule area over {where[1]}'s copper"
                        what = f"the board's rule area(s) over {where[1]}"
                        short = f"rule area over {where[1]}"
                    else:
                        zones = [z for z in rule_areas
                                 if str(K.value(z, "name", default="")) == where]
                        said = f"no rule area named {where!r}"
                        what, short = f"the board's rule area {where!r}", f"rule area {where!r}"
                    if not zones:
                        raise P.RecordError(f"{rec['label']}: {bname} has {said}")
                else:
                    zones = _footprint_areas(fps[ref], copper)
                    what, short = "its footprint's rule area", "footprint area"
                    if not zones and rec is not None:
                        # Every form that fits is named. (What slipped through,
                        # a holdout run: "give the vendor's drawing" could not be
                        # followed for a two-pad printed antenna, whose
                        # keep-outs were rule areas the designer drew.)
                        raise P.RecordError(f"{rec['label']}: {ref}'s footprint carries no "
                                            f"keep-out rule area: give a drawing, or a board "
                                            f"rule area by name, {{\"uuid\": id}} or "
                                            f"{{\"covers\": \"{ref}\"}}")
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
                mine = {r: _antenna_own_items(tree, board, fps[r], by_ref[r]["own_nets"])
                        for r in areas if by_ref.get(r) and by_ref[r]["own_nets"]}
                plans.append((n, path, bname, tree, copper, areas, mine))
    except P.RecordError as exc:
        return Verdict(gate=gid, passed=False, error=str(exc))
    # The two releases, once, before any board is staged, and only when a board
    # is to be judged: a record's error and a board the reader skips are said in
    # their own words, with no tool run.
    if plans:
        refused = _keepout_pair(ctx, gid)
        if refused is not None:
            return refused
    findings: list[dict] = []
    own: list[dict] = []
    holes: list[dict] = []
    ev: list[str] = []
    errors: list[Verdict] = []
    layers: dict[str, list[str]] = {}
    for n, path, bname, tree, copper, areas, mine in plans:
        run = _antenna_runs(ctx, gid, n, path, tree, copper, areas, mine)
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
        notes.append(f"the part's own copper in its keep-out (its pads, and its own_nets' "
                     f"tracks), left out: {len(own)}")
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
