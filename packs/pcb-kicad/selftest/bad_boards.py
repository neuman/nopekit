# SPDX-License-Identifier: Apache-2.0
"""Known-bad fixtures for pcb-kicad. One change each, to the baseline board or to
its schematic.

`nopekit gate selftest` requires every gate to FAIL here. A gate that passes its
own fixture is broken, and a green verdict from it means nothing.

**Sealed.** Every fixture starts from this pack's own ``selftest/baseline.json``
and ``selftest/assets/esd_guard.*`` or ``power_board.*``, never from the
host's projection or the host's board. It hands the gate an empty ledger, or,
for ``pcb.enclosure_fit``, which reads its limit from a claim, the baseline's own
claims (``_claims``): never the host's. A control whose severity depended on the
host's board or claims would fire in some projects and not in others.

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
from nopekit.models import Claim, Ledger

_PACK_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_BASELINE_PATH = os.path.join(_PACK_DIR, "selftest", "baseline.json")
_ASSETS = os.path.join(_PACK_DIR, "selftest", "assets")
K = load_path(os.path.join(_PACK_DIR, "gates", "_kicad.py"))

#: How far past its limit ``narrow_power`` lands: 15%, the margin the mutation
#: pass uses, so the control does not sit on the rounding of the verdict.
MARGIN = 1.15

_PATH_KEYS = ("pcb_board", "pcb_spice_deck", "pcb_enclosure_step")

#: The power rules' records, and the mis-mating and USB-C rules', ``{id:
#: record}`` each, every record naming the board it is for (``board``; a mating
#: names one per side, and a battery or external far side names none).
_POWER_KEYS = ("pcb_batteries", "pcb_protectors", "pcb_conductors", "pcb_matings",
               "pcb_usbc", "pcb_rails")


def _power_records(params: dict):
    """Every power record, and every mating side: each dict that names a board."""
    for key in _POWER_KEYS:
        for rec in params.get(key, {}).values():
            for item in ([rec["a"], rec["b"]] if key == "pcb_matings" else [rec]):
                if "board" in item:
                    yield item


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
    for rec in _power_records(params):
        rec["board"] = os.path.join(_PACK_DIR, rec["board"])
    return params


def _claims_ledger() -> Ledger:
    """The claims the baseline states under ``_claims``, as the known-good
    control's ledger holds them (``packs.baseline_context``): a control that
    reads its limit from a claim (``pcb.enclosure_fit``, tagged enclosure-fit)
    reads the pack's own, never the host's."""
    with open(_BASELINE_PATH, encoding="utf-8") as fh:
        rows = json.load(fh).get("_claims") or []
    return Ledger(claims=[Claim.from_dict(row) for row in rows])


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
    place["z"] = float(place["z"]) + RAISE_MM
    params["pcb_board_placement"] = place
    return dataclasses.replace(ctx, params=params, ledger=_claims_ledger())


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


def _power_variant(ctx, name: str, edit=None, records=None, *, schematic=None) -> object:
    """A context whose power records name a copy of ``power_board.kicad_pcb``
    with ``edit(tree)`` applied, and ``records(params)`` applied to the records.
    With ``schematic``, the board's schematic is copied beside it with
    ``schematic(tree)`` applied (:func:`_unchanged` for a copy as it is).

    A fixture passes ``schematic`` when it says how its change reads there: a
    part swapped (its Value on the symbol too) or the board as it was. A fixture
    that moves a net on the board writes none, rather than a schematic its board
    no longer matches, a second defect beside the one it stages; no gate it
    controls reads one. (What slipped through P5: ``ntc_swapped`` copied the
    board alone, so ``pcb.charge_temperature``, which reads the schematic beside
    a board, asked whether one sat beside the copy: a path under the control's
    out dir that nothing in its run wrote. That question is opaque, invariant 7,
    and the gate's qualification was never current:
    ``tests.test_cache.OutDirReads``.)"""
    params = _baseline()
    src = params["pcb_batteries"]["main"]["board"]
    board = ctx.out_path("pcb-kicad", "known-bad", name, os.path.basename(src))
    src_stem, stem = src[:-len(".kicad_pcb")], board[:-len(".kicad_pcb")]
    for ext, change in ((".kicad_pcb", edit or _unchanged), (".kicad_sch", schematic)):
        if change is None:
            continue
        tree = K.load_tree(src_stem + ext)
        change(tree)
        with open(stem + ext, "w", encoding="utf-8") as fh:
            fh.write(K.dump(tree) + "\n")
    for rec in _power_records(params):
        rec["board"] = board
    if records is not None:
        records(params)
    return dataclasses.replace(ctx, params=params, ledger=Ledger())


def _unchanged(tree) -> None:
    """The edit that changes nothing: a file copied as it is."""


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


def _set_value(tree, ref: str, value: str) -> None:
    """``ref``'s Value field on the board set to ``value``: a part swapped in
    the same footprint, as a BOM substitution swaps it."""
    for prop in K.children(_footprint(tree, ref), "property"):
        if len(prop) >= 3 and prop[1] == "Value":
            prop[2] = K.Q(value)
            return
    raise KeyError(f"{ref} has no Value")


def _set_symbol_value(tree, ref: str, value: str) -> None:
    """``ref``'s Value field on the schematic set to ``value``: the same swap
    where a BOM substitution starts, on the symbol."""
    for sym in K.children(tree, "symbol"):
        props = {str(p[1]): p for p in K.children(sym, "property") if len(p) >= 3}
        if "Reference" in props and str(props["Reference"][2]) == ref:
            props["Value"][2] = K.Q(value)
            return
    raise KeyError(f"the schematic places no {ref}")


#: ``ntc_swapped``'s NTC: ten times the baseline's R25, the same B. A 100 kOhm
#: part in a 10 kOhm footprint is the substitution a BOM tool offers as a
#: drop-in (same package, same B), and the commonest NTC value after 10 kOhm.
SWAPPED_R25_OHM = 100000
SWAPPED_VALUE = "NTC 100k B4250"


def ntc_swapped(ctx):
    """pcb.charge_temperature — TH1 swapped for a 100 kOhm NTC of the same B, on
    the board, on the schematic and in its record, as a BOM substitution swaps
    it: the footprint, the symbol, the nets and every other part as they were.
    DRC, ERC and parity all accept it. The charger's trip resistances are
    fixed, so its window moves about 50 K warmer: charging is allowed from
    about 55 C to about 110 C, past the cell's 45 C."""
    def records(params):
        ntc = params["pcb_protectors"]["charger"]["functions"]["charge_temp"]["ntc"]
        ntc["value"], ntc["r25_ohm"] = SWAPPED_VALUE, SWAPPED_R25_OHM
    return _power_variant(ctx, "ntc_swapped", lambda t: _set_value(t, "TH1", SWAPPED_VALUE),
                          records,
                          schematic=lambda t: _set_symbol_value(t, "TH1", SWAPPED_VALUE))


#: ``lfp_cell``'s cell: a LiFePO4 cell's upper charge voltage. Battery
#: University BU-205 (read 2026-11-24): LiFePO4 is charged to 3.65 V, where the
#: cobalt and manganese lithium-ion cells take 4.20 V.
LFP_UPPER_CHARGE_V = 3.65


def lfp_cell(ctx):
    """pcb.charge_limits — the baseline's cell record changed to a LiFePO4 cell
    of the same size (chemistry lifepo4, upper charge voltage 3.65 V) on the same
    board and charger: a cell swap the connector accepts. The charger's 4.232 V
    at its worst corner is 16% past the cell's 3.65 V. The board and its
    schematic are the baseline's, unchanged."""
    def records(params):
        cell = params["pcb_batteries"]["main"]
        cell["chemistry"] = "lifepo4"
        cell["cell"]["upper_charge_v"] = LFP_UPPER_CHARGE_V
    return _power_variant(ctx, "lfp_cell", records=records, schematic=_unchanged)


#: ``fuse_trips_late``'s PTC: twice the baseline's hold and trip current, the
#: same trip time and the same interrupt rating, so only the overload band
#: moves.
LATE_VALUE = "PTC_FIXTURE_1A0"


def fuse_trips_late(ctx):
    """pcb.fault_current — F1 swapped for a PTC of twice the hold and trip
    current, on the board, on the schematic and in its record. Its trip time at
    8 A and its interrupt rating are the baseline's, so the short-circuit and
    interrupt checks do not move; the wire's 1.45 x 1 A is now below the PTC's
    2 A trip, so between 1.45 A and 2 A the wire heats past its rating and
    nothing trips."""
    def records(params):
        fuse = params["pcb_protectors"]["fuse"]
        fuse["value"] = LATE_VALUE
        trip = fuse["functions"]["trip"]
        trip["hold_a"], trip["trip_a"] = trip["hold_a"] * 2, trip["trip_a"] * 2
    return _power_variant(ctx, "fuse_trips_late", lambda t: _set_value(t, "F1", LATE_VALUE),
                          records,
                          schematic=lambda t: _set_symbol_value(t, "F1", LATE_VALUE))


def cutoff_drives_nothing(ctx):
    """pcb.discharge_cutoff — U2's DO pad moved to a net of its own: U2 still
    senses the cell through F1 and R5, and its DO now drives nothing, so the
    only cutoff left is the pack's PCM, whose 2.30 V is below the cell's 2.5 V.
    The branch's own known-good board was this defect, and its cutoff rule
    passed it."""
    return _power_variant(ctx, "cutoff_drives_nothing",
                          lambda t: _move_pad(t, "U2", "4", "PROT_DO_OPEN"))


def _swap_pad_nets(tree, ref: str, a: str, b: str) -> None:
    """``ref``'s pads ``a`` and ``b`` trade nets: the pin order a generator's
    rotation flip writes, the copper otherwise as it was."""
    pads = {str(p[1]): p for p in K.children(_footprint(tree, ref), "pad")}
    na, nb = K.child(pads[a], "net"), K.child(pads[b], "net")
    na[1:], nb[1:] = list(nb[1:]), list(na[1:])


def ground_beside_vbus(ctx):
    """pcb.mismate — on both FFC housings, J10 and J11, the nets of positions 1
    and 7 trade places, so GND sits at 7, two positions from VBUS at 5, where
    the baseline keeps it at 1. One change, the pin order, as a generator's
    rotation flip makes it: the straight map still meets GND with GND, so DRC,
    ERC, parity and the nominal map all accept it. The flex record states that
    a 6-way cable also seats in the 8-way housings; seated two positions off,
    it puts J10's VBUS on J11's GND."""
    def edit(tree):
        for ref in ("J10", "J11"):
            _swap_pad_nets(tree, ref, "1", "7")
    return _power_variant(ctx, "ground_beside_vbus", edit)


def shared_cc(ctx):
    """pcb.usbc_receptacle — J1's CC2 pad (B5) moved onto CC1's net, so R1's
    5.1 kOhm serves both CC pins and R2 hangs on a net of its own. DRC, ERC and
    parity accept it, and so does a charger on a plain C-to-C cable; a source
    with an e-marked cable reads the cable's Ra beside the shared Rd and powers
    nothing (Type-C R2.0 Table 4-10). The defect a widely sold single-board
    computer shipped and fixed in a revision."""
    def edit(tree):
        cc1 = _net_code(tree, "CC1")
        for pad in K.children(_footprint(tree, "J1"), "pad"):
            if str(pad[1]) == "B5":
                K.child(pad, "net")[1:] = [str(cc1), K.Q("CC1")]
                return
        raise KeyError("J1 pad B5")
    return _power_variant(ctx, "shared_cc", edit)
