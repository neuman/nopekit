# SPDX-License-Identifier: Apache-2.0
"""Known-bad fixtures for pcb-kicad. One change each, to the baseline board or to
its schematic.

`nopekit gate selftest` requires every gate to FAIL here. A gate that passes its
own fixture is broken, and a green verdict from it means nothing.

**Sealed.** Every fixture starts from this pack's own ``selftest/baseline.json``
and ``selftest/assets/esd_guard.*``, never from the host's projection or the
host's board, and hands the gate an empty ledger. A control whose severity
depended on the host's board would fire in some projects and not in others.

**Generated, from the limit the gate judges by.** Each bad project is the baseline
board or schematic parsed by the pack's own reader, one thing changed, and written
back under the CALLER's ``out_dir`` (``<out_dir>/pcb-kicad/known-bad/<fixture>/``),
with the baseline's other files beside it so KiCad applies the same rules. Never into this
directory: a fixture that writes into the pack it is proving makes the proof
depend on whether the pack is writable. ``narrow_power`` derives its width from
the baseline's own rise limit and current with the gate's own formula, so lifting
the limit cannot quietly turn the control into a pass.
"""
from __future__ import annotations

import dataclasses
import json
import os
import shutil

from nopekit.modelio import load_path
from nopekit.models import Ledger

_PACK_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_BASELINE_PATH = os.path.join(_PACK_DIR, "selftest", "baseline.json")
_ASSETS = os.path.join(_PACK_DIR, "selftest", "assets")
K = load_path(os.path.join(_PACK_DIR, "gates", "_kicad.py"))

#: How far past its limit ``narrow_power`` lands: 15%, the margin the mutation
#: pass uses, so the control does not sit on the rounding of the verdict.
MARGIN = 1.15

_PATH_KEYS = ("pcb_board", "pcb_spice_deck", "pcb_enclosure_step")

#: The power rules' records, each naming the board it is for (``board``).
_POWER_KEYS = ("pcb_cell", "pcb_charger", "pcb_fuse", "pcb_conductor", "pcb_cutoffs")


def _baseline() -> dict:
    """The baseline projection, documentation keys stripped and every path made
    absolute against this pack."""
    with open(_BASELINE_PATH, encoding="utf-8") as fh:
        params = {k: v for k, v in json.load(fh).items() if not k.startswith("_")}
    for key in _PATH_KEYS:
        if key in params:
            params[key] = os.path.join(_PACK_DIR, str(params[key]))
    params["pcb_land_drawings"] = {ref: os.path.join(_PACK_DIR, rel)
                                   for ref, rel in params["pcb_land_drawings"].items()}
    for key in _POWER_KEYS:
        for rec in (params[key] if isinstance(params[key], list) else [params[key]]):
            rec["board"] = os.path.join(_PACK_DIR, rec["board"])
    return params


#: The baseline project's library tables, copied beside every variant with its
#: project file, so KiCad reads the same rules, exclusions and libraries.
_DIR_FILES = ("sym-lib-table", "fp-lib-table")


def _variant(ctx, name: str, edit=None, *, schematic=None) -> object:
    """A context whose project is the baseline's with ``edit(tree, params)``
    applied to its board and ``schematic(tree, params)`` to its schematic; every
    file neither touches is copied as it is."""
    params = _baseline()
    src = params["pcb_board"]
    src_stem = src[:-len(".kicad_pcb")]
    stem = os.path.basename(src_stem)
    board = ctx.out_path("pcb-kicad", "known-bad", name, stem + ".kicad_pcb")
    stage = os.path.dirname(board)
    for ext, change in ((".kicad_pcb", edit), (".kicad_sch", schematic)):
        if change is None:
            shutil.copyfile(src_stem + ext, os.path.join(stage, stem + ext))
            continue
        tree = K.load_tree(src_stem + ext)
        change(tree, params)
        with open(os.path.join(stage, stem + ext), "w", encoding="utf-8") as fh:
            fh.write(K.dump(tree) + "\n")
    for rel in [stem + ".kicad_pro", *_DIR_FILES]:
        shutil.copyfile(os.path.join(os.path.dirname(src), rel), os.path.join(stage, rel))
    if os.path.isfile(src_stem + ".kicad_dru"):
        shutil.copyfile(src_stem + ".kicad_dru", os.path.join(stage, stem + ".kicad_dru"))
    models = os.path.join(stage, "3d")
    if not os.path.isdir(models):
        shutil.copytree(os.path.join(os.path.dirname(src), "3d"), models)
    params["pcb_board"] = board
    return dataclasses.replace(ctx, params=params, ledger=Ledger())


def _net_code(tree, name: str) -> int:
    for n in K.children(tree, "net"):
        if len(n) >= 3 and str(n[2]) == name:
            return int(n[1])
    raise KeyError(name)


def _footprint(tree, ref: str):
    for fp in K.children(tree, "footprint"):
        for prop in K.children(fp, "property"):
            if len(prop) >= 3 and prop[1] == "Reference" and str(prop[2]) == ref:
                return fp
    raise KeyError(ref)


def _segments(tree, net: str):
    code = str(_net_code(tree, net))
    return [s for s in K.children(tree, "segment") if K.value(s, "net") == code]


def _segment_from(tree, net: str, x: float, y: float):
    """The ``net`` segment that starts or ends at (x, y) mm: picked by where it
    is, never by its place in the file, which KiCad may reorder on any save."""
    for seg in _segments(tree, net):
        for end in ("start", "end"):
            px, py = (float(v) for v in K.child(seg, end)[1:3])
            if abs(px - x) < 1e-6 and abs(py - y) < 1e-6:
                return seg
    raise KeyError(f"no {net} segment at ({x}, {y})")


#: Header J1's pin 1 (D+) and pin 3 (D-), mm: make_baseline.py's PARTS and NETS.
J1_DP, J1_DM = (4.0, 4.0), (4.0, 9.08)

#: Where U1 pin 6's connection point sits on the baseline schematic, mm:
#: make_schematic.py's U1 at (127, 88.9), its pin 6 at (5.08, 7.62) in the symbol
#: (y up there, down on the sheet).
U1_PIN6_SCH = (132.08, 81.28)


def _label_at(tree, x: float, y: float):
    """The global label whose connection point is (x, y) on the sheet: picked by
    where it is, never by its place in the file."""
    for label in K.children(tree, "global_label"):
        at = K.child(label, "at")
        if abs(float(at[1]) - x) < 1e-6 and abs(float(at[2]) - y) < 1e-6:
            return label
    raise KeyError(f"no global label at ({x}, {y})")


def split_pin_pair(ctx):
    """pcb.pin_names — U1 pin 5 moved from GND to VBUS.

    The ESDA6V1BC6 joins pins 2 and 5 inside (both ``COM``). With pin 2 on GND
    and pin 5 on VBUS the part shorts the supply to ground. The tracks are
    untouched: only the pad's net changes, as it would on a board whose netlist
    assigned pins by number from a misread datasheet.
    """
    def edit(tree, _params):
        vbus = _net_code(tree, "VBUS")
        for pad in K.children(_footprint(tree, "U1"), "pad"):
            if str(pad[1]) == "5":
                net = K.child(pad, "net")
                net[1:] = [str(vbus), K.Q("VBUS")]
    return _variant(ctx, "split_pin_pair", edit)


def narrow_power(ctx):
    """pcb.net_width — every VBUS track narrowed to the width at which IPC-2221
    puts the baseline's VBUS current ``MARGIN`` past the baseline's rise limit."""
    def edit(tree, params):
        amps = float(params["pcb_net_currents_a"]["VBUS"])
        target = float(params["pcb_temp_rise_limit_c"]) * MARGIN
        oz = float(params.get("pcb_copper_oz_outer", 1.0))
        width = K.width_for_rise_mm(amps, target, oz, True)
        width = round(width, 4)
        if K.temp_rise_c(amps, width, oz, True) <= target / MARGIN:
            raise AssertionError("narrow_power: the derived width does not cross the limit")
        for seg in _segments(tree, "VBUS"):
            K.child(seg, "width")[1] = f"{width:g}"
    return _variant(ctx, "narrow_power", edit)


def fat_track(ctx):
    """pcb.drc — the D+ track from J1 to U1 pin 1 widened to 1.0 mm. U1's pins sit
    0.95 mm apart, so the wide copper runs into the ground pad below pin 1."""
    def edit(tree, _params):
        seg = _segment_from(tree, "DP", *J1_DP)
        K.child(seg, "width")[1] = "1"
    return _variant(ctx, "fat_track", edit)


def open_net(ctx):
    """pcb.unconnected — the D- track from J1 to U1 pin 3 deleted."""
    def edit(tree, _params):
        tree.remove(_segment_from(tree, "DM", *J1_DM))
    return _variant(ctx, "open_net", edit)


def orphaned_net(ctx):
    """pcb.board_readable — VBUS dropped from the board's net table, so its track
    and pads name a net code the board no longer defines."""
    def edit(tree, _params):
        code = str(_net_code(tree, "VBUS"))
        for n in K.children(tree, "net"):
            if n[1] == code:
                tree.remove(n)
                break
    return _variant(ctx, "orphaned_net", edit)


def label_typo(ctx):
    """pcb.erc — U1 pin 6's label spelled ``D+`` where every other D+ label says
    ``DP``: one net under two names, the slip of a schematic typed by hand or by
    a script. The new name joins nothing, so ERC finds a label connected to
    nothing else. The board is untouched."""
    def edit(tree, _params):
        _label_at(tree, *U1_PIN6_SCH)[1] = K.Q("D+")
    return _variant(ctx, "label_typo", schematic=edit)


def schematic_drift(ctx):
    """pcb.parity — U1 pin 6 moved from D+ to D- on the schematic, and the board
    not updated from it: the schematic is self-consistent (ERC passes it), the
    board is legal copper, and the two disagree about one pad."""
    def edit(tree, _params):
        _label_at(tree, *U1_PIN6_SCH)[1] = K.Q("DM")
    return _variant(ctx, "schematic_drift", schematic=edit)


def under_fab_width(ctx):
    """pcb.fab_rules — the D+ track from J1 to U1 pin 1 narrowed to 85% of the
    fab's minimum track width for the baseline's layer count and copper, read
    from the same fab file and service the gate reads. KiCad's default rules
    have no minimum width, so ``pcb.drc`` passes this board; the fab cannot etch
    it."""
    def edit(tree, params):
        with open(os.path.join(_PACK_DIR, "gates", "fab", f"{params['pcb_fab']}.json"),
                  encoding="utf-8") as fh:
            fab = json.load(fh)
        table = K.fab_table(fab, K.copper_layer_count(tree),
                            float(params.get("pcb_copper_oz_outer", 1.0)),
                            params.get("pcb_fab_service"))
        width = round(K.fab_rule_min(table, "track_width") * 0.85, 4)
        K.child(_segment_from(tree, "DP", *J1_DP), "width")[1] = f"{width:g}"
    return _variant(ctx, "under_fab_width", edit)


#: A 2 m cable of 28 AWG power pair, out and back: 4 m x 0.213 ohm/m.
THIN_CABLE_OHM = 0.85


def long_thin_cable(ctx):
    """pcb.spice — the baseline VBUS deck with the cable's out-and-back
    resistance raised from 0.25 to 0.85 ohm: 0.5 A then drops 0.47 V in the cable
    and the board, and VBUS at J2 dips to about 4.53 V, past USB 2.0's 4.75 V. The
    deck's every other line is the baseline's."""
    params = _baseline()
    with open(params["pcb_spice_deck"], encoding="utf-8") as fh:
        text = fh.read()
    if "rcable=0.25" not in text:
        raise AssertionError("long_thin_cable: the baseline deck no longer sets rcable=0.25")
    deck = ctx.out_path("pcb-kicad", "known-bad", "long_thin_cable", "vbus_droop.cir")
    with open(deck, "w", encoding="utf-8") as fh:
        fh.write(text.replace("rcable=0.25", f"rcable={THIN_CABLE_OHM:g}"))
    params["pcb_spice_deck"] = deck
    return dataclasses.replace(ctx, params=params, ledger=Ledger())


#: How much higher ``raised_board`` sets the board: the baseline headers' pin
#: tips are 2.365 mm under the lid, so 3 mm puts them 0.635 mm into it.
RAISE_MM = 3.0


def raised_board(ctx):
    """pcb.enclosure_fit — the baseline board placed 3 mm higher in the baseline
    enclosure, as taller standoffs would set it: both headers' pin tips run
    0.635 mm into the lid. Board and enclosure are the baseline's, unchanged; the
    part that collides is one whose 3D model came from KiCad's library."""
    params = _baseline()
    place = dict(params["pcb_board_placement"])
    place["z"] = float(place.get("z", 0.0)) + RAISE_MM
    params["pcb_board_placement"] = place
    return dataclasses.replace(ctx, params=params, ledger=Ledger())


def mirrored_land(ctx):
    """pcb.land_pattern — U1's pad positions swapped 1<->3 and 4<->6, each pad's
    number, net and pin name kept: the SOT-23-6 land drawn mirrored, as a
    footprint drawn from a bottom view is. The netlist and the copper rules
    cannot see it; paired by function and fitted with no mirror, pad 1 sits
    1.9 mm from where ST's drawing puts TVS1."""
    def edit(tree, _params):
        pads = {str(p[1]): p for p in K.children(_footprint(tree, "U1"), "pad")}
        for a, b in (("1", "3"), ("4", "6")):
            at_a, at_b = K.child(pads[a], "at"), K.child(pads[b], "at")
            at_a[1:3], at_b[1:3] = list(at_b[1:3]), list(at_a[1:3])
    return _variant(ctx, "mirrored_land", edit)


def _power_variant(ctx, name: str, edit=None, records=None) -> object:
    """A context whose power records name a copy of ``lipo_charger.kicad_pcb``
    with ``edit(tree)`` applied, and ``records(params)`` applied to the records."""
    params = _baseline()
    src = params["pcb_cell"]["board"]
    board = ctx.out_path("pcb-kicad", "known-bad", name, os.path.basename(src))
    tree = K.load_tree(src)
    if edit is not None:
        edit(tree)
    with open(board, "w", encoding="utf-8") as fh:
        fh.write(K.dump(tree) + "\n")
    for key in _POWER_KEYS:
        for rec in (params[key] if isinstance(params[key], list) else [params[key]]):
            rec["board"] = board
    if records is not None:
        records(params)
    return dataclasses.replace(ctx, params=params, ledger=Ledger())


def _new_net(tree, name: str) -> list:
    """A net added to the board's table, after its last: ``(net <code> "<name>")``."""
    nets = K.children(tree, "net")
    code = max(int(n[1]) for n in nets) + 1
    node = ["net", str(code), K.Q(name)]
    tree.insert(tree.index(nets[-1]) + 1, node)
    return node


def _move_pad(tree, ref: str, number: str, net_name: str) -> None:
    """Pad ``number`` of ``ref`` moved to a new net of its own, ``net_name``."""
    node = _new_net(tree, net_name)
    for pad in K.children(_footprint(tree, ref), "pad"):
        if str(pad[1]) == number:
            K.child(pad, "net")[1:] = [node[1], K.Q(net_name)]
            return
    raise KeyError(f"{ref} pad {number}")


def ntc_unwired(ctx):
    """pcb.charge_protection — TH1's THERM-side pad moved to a net of its own: the
    charger's THERM pin then sees no thermistor, as a board whose NTC footprint
    was placed and never wired would."""
    return _power_variant(ctx, "ntc_unwired", lambda t: _move_pad(t, "TH1", "1", "TH_OPEN"))


def thin_conductor(ctx):
    """pcb.fuse_conductor — the wire's area derived with the gate's own formula so
    that IEC 60949 puts it at its limit in the fuse's guaranteed trip time divided
    by ``MARGIN``: lifting the trip time or the limit cannot turn the control into
    a pass without moving the area with it."""
    power = load_path(os.path.join(_PACK_DIR, "gates", "power.py"))

    def records(params):
        cond, fuse = params["pcb_conductor"], params["pcb_fuse"]
        amps, t_max = (float(v) for v in fuse["trip_max_s"][0])
        area = power.area_for_s(t_max / MARGIN, amps, float(cond["start_c"]),
                                float(cond["limit_c"]))
        cond["area_mm2"] = round(area, 5)
        if power.adiabatic_s(amps, cond["area_mm2"], float(cond["start_c"]),
                             float(cond["limit_c"])) >= t_max:
            raise AssertionError("thin_conductor: the derived area does not cross the limit")
    return _power_variant(ctx, "thin_conductor", records=records)


def cutoff_unwired(ctx):
    """pcb.discharge_cutoff — R1's cell-side pad moved to a net of its own: the
    protector U2 senses a net the cell never drives, and nothing cuts the cell
    off (its record has no PCM)."""
    return _power_variant(ctx, "cutoff_unwired", lambda t: _move_pad(t, "R1", "1", "R1_OPEN"))
