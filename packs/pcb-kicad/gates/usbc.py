# SPDX-License-Identifier: Apache-2.0
"""pcb-kicad — each USB-C receptacle terminated and wired as the Type-C
specification requires for its role.

    pcb.usbc_receptacle  tier 0  CC terminated per pin for the role (Rd or Rp in the specification's band, or a named controller), VBUS switched on a source, USB 2.0 data on both rows

**Where the verdict comes from.** The boards' pad nets, read with this pack's
reader, held to the USB Type-C Cable and Connector Specification (Release 2.0,
August 2019, USB-IF): the Rd and Rp bands of its Tables 4-24 and 4-25, quoted
in ``gates/usbc.json`` with the day they were read; Table 3-5's note 2 for the
two rows of USB 2.0 data; 4.4.2 for a source's VBUS. The roles and the
resistors' tolerance are a person's records (``pcb_usbc``). So the basis is
sourced.

**One home per check.** That the four VBUS pins, the four GND pins, and A6 and
B6 or A7 and B7 each carry one net is ``pcb.pin_names``' check: the library
names those pins alike (Table 3-5's notes 4 and 5 ask for them bussed). This
rule keeps what pin names cannot say: CC terminated per pin and per role, D+
apart from D-, and a pin left on no net, which ``pcb.pin_names`` skips by
design (a B6 left open works in one plug orientation only).

**What slipped through.** Nothing here had judged a receptacle: a sink whose
two CC pins share one 5.1 kOhm passes DRC, ERC and parity, and a source with an
e-marked cable then reads the cable's Ra in parallel with it and powers nothing
(the defect a widely sold single-board computer shipped and fixed in a
revision; Table 4-10 says what a source reads on CC1 and CC2).

**Not judged:** SuperSpeed lanes, USB Power Delivery, VCONN, SBU, a
controller's own Rd or Rp (its datasheet's), and whether the device may draw
what the source advertises (references/usb-c.md).
"""
from __future__ import annotations

import json
import os
import re

from nopekit.gates import gate, GateContext
from nopekit.modelio import load_path as _load_path
from nopekit.models import NegativeControl, Tier, Verdict

_HERE = os.path.dirname(os.path.abspath(__file__))
K = _load_path(os.path.join(_HERE, "_kicad.py"))
P = _load_path(os.path.join(_HERE, "_power.py"))

RecordError, ReaderLimit = P.RecordError, P.ReaderLimit

#: The specification's Rd and Rp tables, each value quoted (``usbc.json``).
TABLE = os.path.join(_HERE, "usbc.json")

#: A part on a CC net is a resistor this rule judges when it has two numbered
#: pads and its ref is R and a number (IEEE 315's letter) or its footprint is
#: from KiCad's ``Resistor*`` libraries. A TVS or a capacitor on CC (ESD
#: protection) carries no DC termination and is not judged. Rejected: any
#: two-pad part whose value reads as ohms (a ferrite bead's "600R" would read
#: as a 600 ohm termination).
_RESISTOR_REF = re.compile(r"^R\d+$")

#: What each role may state beside its own keys. A key another role reads is
#: refused, not ignored: a sink's ``rp`` would say something no rule checks.
ROLE_KEYS = {
    "sink": ("cc_by", "reads_rp", "cc_resistor_tol_pct", "others"),
    "source": ("cc_by", "rp", "cc_resistor_tol_pct", "vbus_switch", "others"),
    "drp": ("cc_by", "vbus_switch", "others"),
}
RP_CURRENTS = ("default", "1.5A", "3A")


def _load_json(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _ohm(x: float) -> str:
    return f"{x / 1000:.4g}k" if x >= 1000 else f"{x:.4g}"


def _is_resistor(fp) -> bool:
    numbered = [p for p in fp.pads if p.number]
    return len(numbered) == 2 and (bool(_RESISTOR_REF.match(fp.ref))
                                   or fp.lib_id.split(":")[0].startswith("Resistor"))


def _resistors_on(d, net) -> list[tuple]:
    """``(board path, footprint, the net its other pad is on)`` of each fitted
    resistor with one pad on ``net``, on any board the matings join."""
    out, seen = [], set()
    for path, ref, _pad in d.pads_on(net):
        if (path, ref) in seen:
            continue
        seen.add((path, ref))
        fp = next(f for f in d.boards[path].footprints if f.ref == ref)
        if not _is_resistor(fp):
            continue
        nets = [d.net(path, fp, p.number) for p in fp.pads if p.number]
        other = [n for n in nets if not d.same(n, net)]
        if len(other) == 1:
            out.append((path, fp, other[0]))
    return out


def _value(fp) -> float:
    ohms = P.resistance(fp.value)
    if ohms is None or ohms <= 0:
        raise RecordError(f"{fp.ref}'s value {fp.value!r} is not a resistance this rule can "
                          f"read (give the bare value, such as 5.1k or 5k1)")
    return ohms


@gate(
    id="pcb.usbc_receptacle",
    basis="sourced",
    title="Each USB-C receptacle is terminated and wired as the Type-C specification "
          "requires for its role",
    claims=["pcb", "usb-c", "charging-interface"],
    tier=Tier.INSTANT,
    settles="usb-c cc termination",
    negative_control=NegativeControl(
        fixture="selftest/bad_boards.py:shared_cc",
        note="the power baseline with J1's B5 moved onto CC1's net, so one 5.1 kOhm serves "
             "both CC pins: DRC, ERC and parity accept it, and a source with an e-marked "
             "cable reads Ra beside it",
    ),
)
def usbc_receptacle(ctx: GateContext) -> Verdict:
    """For each receptacle ``pcb_usbc`` names, the count of what its role's
    rules find, against 0.

    **Every role.** CC1 (A5) and CC2 (B5) each on a net, and on two nets. With
    ``usb2`` true, A6, B6, A7 and B7 each on a net, and no D+ pin on a D- pin's
    net.

    **A sink** terminates each CC pin with one fitted resistor to the
    receptacle's GND, its value at the resistor's tolerance corners inside
    Table 4-25's Rd band: 5.1 kOhm +-20 %, or +-10 % when it reads the source's
    advertisement (``reads_rp``) to draw more than default power. No resistor
    runs from a CC net to anything else (a pull-up on a sink).

    **A source** terminates each CC pin with one resistor to its Rp rail
    (``rp``: the advertisement and the rail's voltage), inside Table 4-24's
    band, and switches VBUS (``vbus_switch``): 4.4.2 keeps a source's VBUS
    unpowered until a sink attaches, so a VBUS on the rail itself is
    hard-wired. **A DRP** toggles through a controller (``cc_by``), and
    switches VBUS too.

    With ``cc_by``, the controller's pins terminate CC: each CC net must reach
    its pin, and a resistor beside it is counted unless ``others`` names it
    with why. The controller's own Rd or Rp is its datasheet's, not judged.

    A receptacle is found by its pads (A5 and B5 on a footprint named USB-C or
    Type-C, not a plug) or its pin names (CC1 and CC2); one no record names is
    an error, and with no ``pcb_usbc`` the rule skips, naming it."""
    gid = "pcb.usbc_receptacle"
    try:
        recs = P.records(ctx, "pcb_usbc")
        d = P.Design(ctx)
        if not recs:
            if not ctx.param("pcb_board"):
                return K.skip(gid, "no pcb_usbc: each USB-C receptacle's role (sink, source or "
                                   "DRP) is a record a person enters")
            path = d.board({}, "pcb_board")
            found = [fp.ref for fp in d.boards[path].footprints
                     if fp.fitted and P.typec_receptacle(fp)]
            if found:
                return K.skip(gid, f"no pcb_usbc, and {', '.join(found)} on "
                                   f"{os.path.basename(path)} is a USB-C receptacle: state "
                                   f"its role (sink, source or DRP) in pcb_usbc")
            return K.skip(gid, f"no USB-C receptacle on {os.path.basename(path)}, and no "
                               f"pcb_usbc")
        table = ctx.load_file(TABLE, loader=_load_json)
        usbcs = [P.UsbC(d, uid, rec) for uid, rec in sorted(recs.items())]
        d.matings(P.records(ctx, "pcb_matings"))
        # pcb_board is read for a receptacle no record names, in a design of
        # its own: a board no record names is no part of the nets, and its
        # name would stand beside every net in the detail.
        boards = dict(d.boards)
        if ctx.param("pcb_board"):
            scan = P.Design(ctx)
            path = scan.board({}, "pcb_board")
            boards.setdefault(path, scan.boards[path])
        named = {(u.path, u.fp.ref) for u in usbcs}
        missing = [f"{fp.ref} ({fp.value}) on {os.path.basename(path)}"
                   for path, board in sorted(boards.items()) for fp in board.footprints
                   if fp.fitted and P.typec_receptacle(fp) and (path, fp.ref) not in named]
        if missing:
            raise RecordError(f"{', '.join(missing)} is a USB-C receptacle that pcb_usbc does "
                              f"not name: state its role")
        rows = [_receptacle(d, u, table) for u in usbcs]
    except ReaderLimit as exc:
        return K.skip(gid, str(exc))
    except RecordError as exc:
        return Verdict(gate=gid, passed=False, error=str(exc))
    found = [v for r in rows for v in r["violations"]]
    ev = K.evidence(ctx, "usbc_receptacle", {
        "boards": sorted(d.boards),
        "specification": f"{table['specification']}, {table['section']}, read {table['read']}",
        "receptacles": rows})
    if found:
        detail = (f"{len(found)} violation(s): " + "; ".join(found[:4])
                  + ("; ..." if len(found) > 4 else ""))
    else:
        detail = "; ".join(r["line"] for r in rows)
    return Verdict(gate=gid, passed=not found, measured=len(found), limit=0, comparator="<=",
                   units="violations", detail=detail, evidence=ev)


def _receptacle(d, u, table: dict) -> dict:
    """One receptacle's violations, and what passed, for the detail."""
    rec, label, ref = u.rec, u.label, u.fp.ref
    extra = [k for k in ROLE_KEYS["source"] + ("reads_rp",)
             if k in rec and k not in ROLE_KEYS[u.role]]
    if extra:
        raise RecordError(f"{label} is a {u.role}: it takes no {', '.join(dict.fromkeys(extra))}")
    if "cc_by" in rec:
        for k in ("reads_rp", "rp", "cc_resistor_tol_pct"):
            if k in rec:
                raise RecordError(f"{label} names cc_by, whose pins terminate CC: drop {k}")
    elif "others" in rec:
        raise RecordError(f"{label}.others names parts beside a controller, and the record "
                          f"names no cc_by")
    elif u.role == "drp":
        raise RecordError(f"{label} is a DRP: name the controller that toggles CC (cc_by)")
    v, said = [], []
    cc = {"CC1": u.net("A5"), "CC2": u.net("B5")}
    for name, spec in (("CC1", "A5"), ("CC2", "B5")):
        if cc[name] is None:
            v.append(f"{ref} {name} (pad {u.pad[spec]}) is open: in one plug orientation the "
                     f"other end sees no termination")
    if cc["CC1"] is not None and d.same(cc["CC1"], cc["CC2"]):
        v.append(f"{ref} CC1 and CC2 share one net ({d.label(cc['CC1'])}): one termination "
                 f"serves both pins, and a source reads what an e-marked cable puts on the "
                 f"other pin beside it (Table 4-10)")
    nets = []
    for name in ("CC1", "CC2"):
        if cc[name] is not None and not any(d.same(cc[name], n) for _w, n in nets):
            both = cc["CC1"] is not None and d.same(cc["CC1"], cc["CC2"])
            nets.append(("CC1 and CC2" if both else name, cc[name]))
    gnd = [n for n in (u.net(s) for s in u.specs(P.TYPEC_GND)) if n is not None]
    vbus = [n for n in (u.net(s) for s in u.specs(P.TYPEC_VBUS)) if n is not None]
    rail = None
    if "cc_by" in rec:
        said.append(_controller(d, u, nets, v))
    else:
        if "cc_resistor_tol_pct" not in rec:
            raise RecordError(f"{label} has no cc_resistor_tol_pct: the CC resistors' tolerance "
                              f"from the BOM (a board's Value gives only the nominal)")
        tol = P.pct(rec["cc_resistor_tol_pct"], f"{label}.cc_resistor_tol_pct")
        if u.role == "sink":
            if "reads_rp" not in rec:
                raise RecordError(f"{label} has no reads_rp: true when the sink reads the "
                                  f"source's advertisement to draw more than default power "
                                  f"(Rd +-10 %), false when it never does (Rd +-20 %)")
            reads = P.flag(rec["reads_rp"], f"{label}.reads_rp")
            band_pct = table["rd"]["tol_pct"]["reads_rp" if reads else "default_power"]
            nominal, to, what = float(table["rd"]["nominal_ohm"]), gnd, "Rd"
            where, to_name = "Table 4-25", "GND"
        else:
            rp = P.keys(rec.get("rp") or {}, f"{label}.rp", ("current", "rail_v", "rail"))
            current = P.word(rp["current"], f"{label}.rp.current", RP_CURRENTS)
            volts = P.num(rp["rail_v"], f"{label}.rp.rail_v", gt=0.0)
            key = next((k for k in table["rp"]["rails"] if float(k) == volts), None)
            if key is None:
                raise RecordError(f"{label}.rp.rail_v is {volts:g}: Table 4-24 gives resistor "
                                  f"pull-ups to 5 V (4.75-5.5 V) and to 3.3 V only")
            name = P.text(rp["rail"], f"{label}.rp.rail")
            if name not in d.boards[u.path].net_names:
                raise RecordError(f"{label}.rp is for another design: "
                                  f"{os.path.basename(u.path)} has no net {name}")
            rail = d.node(u.path, name)
            row = table["rp"]["rails"][key][current]
            nominal, band_pct, to, what = float(row["ohm"]), row["tol_pct"], [rail], "Rp"
            where, to_name = "Table 4-24", f"the {key} V rail {name}"
        lo_band, hi_band = nominal * (1 - band_pct / 100), nominal * (1 + band_pct / 100)
        seen = []
        for name, net in nets:
            rs = _resistors_on(d, net)
            good = [r for r in rs if any(d.same(r[2], t) for t in to)]
            for _path, fp, other in (r for r in rs if r not in good):
                why = ("a sink terminates CC only to GND" if u.role == "sink" else
                       "a source terminates CC only to its Rp rail")
                v.append(f"{fp.ref} {fp.value} runs from {ref} {name} to {d.label(other)}: "
                         f"{why}")
            if not good:
                v.append(f"{ref} {name} has no {what} to {to_name}")
                continue
            if len(good) > 1:
                v.append(f"{ref} {name} has {len(good)} resistors to {to_name} ("
                         f"{', '.join(r[1].ref for r in good)}): one {what} per pin")
                continue
            fp = good[0][1]
            ohms = _value(fp)
            lo, hi = ohms * (1 - tol), ohms * (1 + tol)
            if lo < lo_band or hi > hi_band:
                v.append(f"{ref} {name}'s {what} {fp.ref} {fp.value} is {_ohm(lo)}..{_ohm(hi)} "
                         f"at its tolerance, outside {_ohm(lo_band)}..{_ohm(hi_band)} "
                         f"({_ohm(nominal)} +-{band_pct:g} %, {where})")
            else:
                seen.append((name, fp, lo, hi))
        if seen:
            said.append(" and ".join(f"{n} {fp.ref} {fp.value}" for n, fp, _l, _h in seen)
                        + f" to {to_name}, at {_ohm(min(s[2] for s in seen))}.."
                          f"{_ohm(max(s[3] for s in seen))} inside {what} {_ohm(lo_band)}.."
                          f"{_ohm(hi_band)} ({_ohm(nominal)} +-{band_pct:g} %, {where})")
    if u.role in ("source", "drp"):
        said.append(_vbus_switch(d, u, vbus, rail, v))
    if u.usb2:
        dp = {s: u.net(s) for s in P.TYPEC_DP}
        dm = {s: u.net(s) for s in P.TYPEC_DM}
        for spec, net in {**dp, **dm}.items():
            if net is None:
                v.append(f"{ref} {spec} ({'D+' if spec in dp else 'D-'}, pad {u.pad[spec]}) is "
                         f"open: USB data works in one plug orientation only (Table 3-5, "
                         f"note 2)")
        shared = sorted({d.label(a) for a in dp.values() for b in dm.values()
                         if a is not None and d.same(a, b)})
        if shared:
            v.append(f"{ref} D+ and D- share {', '.join(shared)}")
        if not any(n is None for n in (*dp.values(), *dm.values())) and not shared:
            said.append("D+ and D- on both rows")
    line = (f"{ref} ({u.role}{', USB 2.0' if u.usb2 else ''}): "
            + "; ".join(s for s in said if s))
    return {"receptacle": u.id, "ref": ref, "role": u.role, "usb2": u.usb2,
            "violations": v, "line": line}


def _controller(d, u, nets: list, v: list) -> str:
    """CC terminated by a controller's pins: each CC net reaches its pin, and no
    resistor the record does not name in ``others`` sits beside it."""
    label = f"{u.label}.cc_by"
    ctl = P.keys(u.rec["cc_by"], label, ("ref", "value", "pins"), ("board",))
    pins = P.keys(ctl["pins"], f"{label}.pins", ("CC1", "CC2"))
    path = d.board(ctl, label) if "board" in ctl else u.path
    fp = d.part(path, ctl["ref"], ctl["value"], label, [str(pins["CC1"]), str(pins["CC2"])])
    declared = set()
    others = u.rec.get("others") or []
    if not isinstance(others, list):
        raise RecordError(f"{u.label}.others: give [{{ref, value, why}}]")
    for i, o in enumerate(others):
        P.keys(o, f"{u.label}.others[{i}]", ("ref", "value", "why"), ("board",))
        P.text(o["why"], f"{u.label}.others[{i}].why")
        opath = d.board(o, f"{u.label}.others[{i}]") if "board" in o else u.path
        declared.add((opath, d.part(opath, o["ref"], o["value"], f"{u.label}.others[{i}]").ref))
    if not fp.fitted:
        v.append(f"{P.not_fitted(fp)}: nothing terminates {u.fp.ref}'s CC")
        return ""
    for name in ("CC1", "CC2"):
        net = u.net("A5" if name == "CC1" else "B5")
        pin = str(pins[name])
        if net is not None and not d.same(net, d.net(path, fp, pin)):
            v.append(f"{u.fp.ref} {name} is on {d.label(net)}, not on {fp.ref} pad {pin}")
    for name, net in nets:
        for rpath, rfp, other in _resistors_on(d, net):
            if (rpath, rfp.ref) not in declared:
                v.append(f"{rfp.ref} {rfp.value} runs from {u.fp.ref} {name} to "
                         f"{d.label(other)} beside {fp.ref}, which terminates CC: it moves "
                         f"the termination (name it in others, with why, if it carries none)")
    return f"CC on {fp.ref} {fp.value} (its own termination is its datasheet's, not judged)"


def _vbus_switch(d, u, vbus: list, rail, v: list) -> str:
    """A source's VBUS switched: 4.4.2 keeps it unpowered until a sink
    attaches, so the switch named must sit with one path pin on VBUS and one
    off it, and VBUS must not be the Rp rail itself."""
    ref = u.fp.ref
    if rail is not None and any(d.same(rail, n) for n in vbus):
        v.append(f"{ref} VBUS is the Rp rail itself ({d.label(rail)}): hard-wired, powered "
                 f"before any sink attaches (4.4.2)")
    if "vbus_switch" not in u.rec:
        v.append(f"{ref} VBUS has no switch: a {u.role} keeps VBUS unpowered until a sink "
                 f"attaches (4.4.2); name it in vbus_switch")
        return ""
    label = f"{u.label}.vbus_switch"
    sw = P.keys(u.rec["vbus_switch"], label, ("ref", "value", "path"), ("board",))
    pins = P.pins(sw["path"], f"{label}.path", 2)
    path = d.board(sw, label) if "board" in sw else u.path
    fp = d.part(path, sw["ref"], sw["value"], label, pins)
    if not fp.fitted:
        v.append(f"{P.not_fitted(fp)}: {ref}'s VBUS is not switched")
        return ""
    a, z = (d.net(path, fp, pin) for pin in pins)
    on = [n for n in (a, z) if any(d.same(n, b) for b in vbus)]
    if len(on) != 1:
        v.append(f"{fp.ref}'s path pins sit on {d.label(a)} and {d.label(z)}: it does not "
                 f"switch {ref}'s VBUS ({', '.join(sorted({d.label(n) for n in vbus}))})")
        return ""
    return f"VBUS switched by {fp.ref} {fp.value}"
