# `pcb.usbc_receptacle`: a USB-C receptacle terminated for its role

A USB-C receptacle that is wired wrong charges from one cable and not from
another, or carries data in one plug orientation only. DRC, ERC and parity pass
it, and a C-to-C test needs built boards. This rule holds each receptacle to the
USB Type-C specification from the board's pad nets.

## The source

Universal Serial Bus Type-C Cable and Connector Specification, Release 2.0,
August 2019 (USB 3.0 Promoter Group, published by USB-IF):
https://www.usb.org/sites/default/files/USB%20Type-C%20Spec%20R2.0%20-%20August%202019.pdf,
read 2026-11-24. Release 2.0 is the newest release usb.org serves as a plain
PDF. Later releases (2.3 and on) ship as zip archives and were not read here:
re-read Tables 4-24 and 4-25 there before a value in `gates/usbc.json` changes.

| What | Where in Release 2.0 |
|---|---|
| Rd, a sink's CC termination | 4.11.1, Table 4-25 |
| Rp, a source's CC termination | 4.11.1, Table 4-24 |
| what a source reads on CC1 and CC2 (Rd, Ra, open) | Table 4-10 |
| USB 2.0 data on both rows | Table 3-5, note 2 |
| VBUS, CC, D+ and D- pins by name | Tables 3-4 and 3-5 |
| a source's VBUS unpowered until a sink attaches | 4.4.2 |

Table 4-25, Rd (the values `gates/usbc.json` holds, with its words):

| Rd implementation | Nominal | Can detect power capability? |
|---|---|---|
| +-20 % voltage clamp | 1.1 V | no |
| +-20 % resistor to GND | 5.1 kOhm | no |
| +-10 % resistor to GND | 5.1 kOhm | yes |

Table 4-24, Rp as a resistor (a current source is a controller's, not judged):

| Advertisement | Pull-up to 4.75-5.5 V | Pull-up to 3.3 V +-5 % |
|---|---|---|
| Default USB power | 56 kOhm +-20 % | 36 kOhm +-20 % |
| 1.5 A at 5 V | 22 kOhm +-5 % | 12 kOhm +-5 % |
| 3.0 A at 5 V | 10 kOhm +-5 % | 4.7 kOhm +-5 % |

Table 4-24's note 1 (56 kOhm +-5 %) is for an Rp in the plug of a legacy cable
or adapter, not for a receptacle.

The band is the termination's, so the resistor's own tolerance must keep it
inside: `cc_resistor_tol_pct`, from the BOM, because a board's Value field gives
only the nominal. A 5.1k at 1 % is 5.049k..5.151k, inside both Rd bands. A 4.7k
at 5 % is 4.465k..4.935k: inside +-20 % (4.08k..6.12k), outside +-10 %
(4.59k..5.61k).

## The record

`pcb_usbc`, one record per receptacle, keyed by id: `board`, `ref`, `value`,
`role` (sink, source or drp), `usb2`, `source`, and by role:

- **sink**: `reads_rp` (true when the sink reads the source's advertisement to
  draw more than default power, so its Rd is held to +-10 %; false when it never
  does, +-20 %) and `cc_resistor_tol_pct`;
- **source**: `rp` {`current` default, 1.5A or 3A; `rail_v` 5 or 3.3; `rail`,
  the net it pulls up to}, `cc_resistor_tol_pct` and `vbus_switch` {ref, value,
  path [pin, pin], control}: `control` is the pin that turns it on and off (a
  FET's gate, a load switch's enable);
- **drp**: `cc_by` and `vbus_switch`;
- any role: `cc_by` {ref, value, pins {CC1, CC2}} for a controller whose pins
  terminate CC, and `others` [{ref, value, why}] for a resistor beside the
  termination that carries none: beside a controller (a dead-battery Rd the
  controller switches out), or beside an Rd or Rp (a series resistor into an
  ADC or a GPIO that reads CC). A resistor `others` names is kept out of the
  count. Beside an Rd or Rp, one that runs to GND, VBUS or the Rp rail is
  refused: it is in parallel with the termination or pulls against it.

`pins` maps a Type-C pin to a pad, {"A5": "C1"}, only for a footprint that does
not number its pads A1..B12. All 22 of KiCad 9.0.9's stock USB-C receptacle
footprints number them so. A key another role reads is refused, not ignored.

## What it checks

- **CC per pin.** CC1 (A5) and CC2 (B5) each on a net, and on two nets. A shared
  CC net is one termination for both pins: with an e-marked cable, the source
  reads the cable's Ra on the other pin beside it (Table 4-10's states) and may
  power nothing. A widely sold single-board computer shipped this and fixed it in
  a revision.
- **A sink**: one fitted resistor from each CC net to the receptacle's GND, at
  its tolerance corners inside the Rd band, and no resistor from a CC net to
  anything else (a pull-up on a sink).
- **A source**: one resistor from each CC net to its Rp rail, inside the Rp band
  for its advertisement and rail, and no Rd. Its VBUS is switched (4.4.2: a
  source keeps VBUS unpowered until a sink attaches): one path pin of
  `vbus_switch` on VBUS and one off it, VBUS not the Rp rail itself, and the
  switch a part something can turn off. A part with two pads (a fuse, a PTC, a
  resistor, a ferrite) cannot switch: a source wired to VBUS through one has
  VBUS on whenever its rail is, and fails. The switch has three pads or more,
  and its `control` pin sits on a net of its own: not VBUS, not the supply on
  its other path pin, not the receptacle's GND, not the Rp rail. An open
  control pin fails too.
- **A DRP**: its controller's pins on CC, and VBUS switched.
- **Data, with `usb2` true**: A6, B6, A7 and B7 each on a net (Table 3-5, note 2:
  the receptacle supports the pair on both rows, and the plug's orientation
  picks one), and no D+ pin on a D- pin's net.
- **Bussing, where `pcb.pin_names` cannot see it**: every VBUS pin on a net on
  one net, every GND pin likewise, and with `usb2` A6 with B6 and A7 with B7.

A resistor is a two-pad part whose ref is R and a number, or whose footprint is
from a `Resistor*` library. A TVS or a capacitor on CC carries no termination,
and is not judged.

**One home per check, where it can see.** The four VBUS pins, the four GND
pins, A6 with B6 and A7 with B7 each on one net is `pcb.pin_names`' check on
`pcb_board`, the one board it reads, where the library names those pins alike
(Table 3-5's notes 4 and 5 ask for them bussed). This rule leaves it there only
for a receptacle on `pcb_board` whose pads carry one name per group. For any
other receptacle (on another board, as the baseline's J1 is, or with its pads
not so named) this rule checks the bussing itself, by the specification's pin
names, and the detail says which rule checked it. `pcb.pin_names` skips a pad
on no net by design, so an open B6 is this rule's. An open VBUS or GND pad is
judged by neither.

## Finding a receptacle

A footprint is a receptacle when it is a connector and two of its pads carry
the pin names CC1 and CC2 (KiCad's receptacle symbols name them so; its plug
symbol names them CC and VCONN), or when its name says USB-C or Type-C, not
Plug, and it has pads A5 and B5. A connector is a footprint from KiCad's
`Connector*` libraries, one whose name says USB-C or Type-C, or one whose ref
is J, P, CN, X or USB and a number. The pin names alone are not enough: KiCad
9.0.9's own `Interface_USB` symbols give CC1 and CC2 to USB-C controllers too
(FUSB302BMPX, STUSB4500QTR, TUSB320 and others), and a controller is no
receptacle. Pads A5 and B5 alone are not enough either: a ball grid numbers its
balls so. A receptacle no record names is an error; with no `pcb_usbc` at all
the rule skips and names it. A receptacle drawn in a project library, with
another ref and its pads numbered otherwise, is not found: name it in
`pcb_usbc`.

## Not judged

SuperSpeed lanes, USB Power Delivery, VCONN, SBU, a controller's own Rd or Rp
(its datasheet's), whether the device draws more than the source advertises,
an open VBUS or GND pad, what lies beyond a resistor `others` names (it is
taken as a high-impedance input on its why), and what drives the VBUS switch's
control pin and when: firmware that turns VBUS on before a sink attaches, or a
pull-up to a fixed rail that nothing pulls down.

## Why it earns its place

A device that will not charge from a C-to-C charger or an e-marked cable, or
whose data works in one plug orientation, is a board spin and a field return.
Since 28 December 2024, the EU's common-charger rule (Directive (EU) 2022/2380,
amending the Radio Equipment Directive 2014/53/EU) requires a USB-C receptacle
on phones, tablets, cameras, headphones, headsets, handheld consoles, speakers,
e-readers, keyboards, mice, navigation units and earbuds, and on laptops from 28
April 2026. It does not cover smartwatches, fitness trackers, wristbands,
remotes or tags. The rule applies to every USB-C receptacle all the same.

## Sources

- USB Type-C Cable and Connector Specification, Release 2.0 (above).
- European Commission, one common charging solution for all (the categories, the
  dates, and those the Commission's review lists as not covered), read
  2026-11-24:
  https://single-market-economy.ec.europa.eu/sectors/electrical-and-electronic-engineering-industries-eei/radio-equipment-directive-red/one-common-charging-solution-all_en
- Directive (EU) 2022/2380: https://eur-lex.europa.eu/eli/dir/2022/2380/oj/eng
- The shared-CC defect, explained: https://people.kernel.org/bleung/how-to-design-a-proper-usb-c-power-sink-hint-not-the-way-raspberry-pi-4
  and https://hackaday.com/2019/07/16/exploring-the-raspberry-pi-4-usb-c-issue-in-depth/
