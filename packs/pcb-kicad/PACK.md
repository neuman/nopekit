# pcb-kicad

Checks a printed circuit board drawn in KiCad 6 to 9, and its schematic: KiCad's
own design rule check, connectivity, electrical rules check and schematic parity,
the library's pin names against the board's nets, trace heating per IPC-2221, a
fab's published limits, a measurement ngspice makes on the project's circuit
deck, the populated boards' fit in their enclosure, each footprint's land against
its vendor's drawing, four rules for a board that charges or carries a cell, the
wrong ways its connectors can meet, each USB-C receptacle's termination, and the
tracks, vias, pads and pours in each radio module's antenna keep-out.

**Before you trust a Checked claim from this pack,** know what it is: a board that
breaks none of KiCad's rules, with every connection made, no part shorting two of
its own nets, no named track running hotter than you allowed, drawn from a
schematic that breaks none of KiCad's electrical rules and that it matches, inside
the fab's limits, with a simulated value inside its band, every part clear of
the enclosure, and no track, via, other part's pad or pour in an antenna's
keep-out (a copper drawing or text there is not judged). It is not a
circuit that works. Nothing here has seen the board powered, and nothing here
has heard its radio.

## What this pack settles

| Gate | Tier | Basis | Settles |
|---|---|---|---|
| `pcb.board_readable` | 0 | model-only | the board is a KiCad 6-10 file whose every track names a defined net (the guard); nets read by code and by name; a newer file is skipped, never failed |
| `pcb.pin_names` | 0 | sourced | pins the library gives one name (a TVS array's two `COM`, a part's several `GND`) carry one net, except the ends of a series element stated per ref (`pcb_pin_names_series`); the names from the schematic's symbols where one sits beside the board, else the pads' `pinfunction` |
| `pcb.land_pattern` | 0 | model-only | each footprint `pcb_land_drawings` names fits its vendor's drawing: pads paired by pin function (anchored on the pads of a function of their own when the pairings are too many to try), one rigid fit with no mirror, every centre within half its smaller pad's smaller side |
| `pcb.net_width` | 0 | sourced | every track of each named net stays inside its temperature-rise limit, IPC-2221, at the net's current, or at a route's current on the tracks of its route between two pads and the rest's elsewhere; the detail names the hottest stretch |
| `pcb.drc` | 1 | established | `kicad-cli pcb drc` finds nothing a person did not exclude in KiCad, at every severity by default, and every custom rule in the board's `.kicad_dru`, and every property its conditions read, is shown to select something; the line names the errors first |
| `pcb.unconnected` | 1 | established | KiCad finds no connection the netlist asks for and the copper does not make |
| `pcb.erc` | 1 | established | `kicad-cli sch erc` finds nothing in the schematic a person did not exclude, at every severity by default |
| `pcb.parity` | 1 | established | KiCad finds no difference between the board and its schematic in what is built: parts, values, footprints, pad nets (an open pin on a pad with no net is agreement; symbol fields, footprint filters and board-only drawing are said apart) |
| `pcb.fab_rules` | 1 | sourced | KiCad's DRC with the named fab's published minimums, for the service the board is ordered on, as the only custom rules finds nothing past them; a dielectric or via kind the service does not list skips |
| `pcb.spice` | 1 | established | a `.meas` result of `ngspice -b` on the project's deck stays inside `pcb_spice_min` / `pcb_spice_max`; an option name ngspice does not know (on a `.options` card or an `option` command), a measure two `.meas` cards define, or a `.meas tran` window outside the run, is refused first |
| `pcb.enclosure_fit` | 1 | established | each board, every part's 3D model (any footprint that shows one, whatever its type), or its declared height where its model is not on this host, in place and turned to any orientation, shares no volume with its enclosure or with another board in it, and each part keeps the clearance of the claim tagged `enclosure-fit`, but against a feature a by-design contact names (OpenCascade) |
| `pcb.charge_temperature` | 0 | sourced | charging stops outside each cell's charge temperature window at the tolerance corners of its sensing network (the comparator's datasheet trips, the NTC's R/T table or B equation, every resistor), wherever the stop lives: the charger, a protector or BMS that acts on it, or the pack; headroom in K. A charger that senses nothing, or whose sense pin sees only a fixed divider, fails without the cell's window |
| `pcb.charge_limits` | 0 | sourced | the charger's guaranteed maximum charge voltage per cell and current, as one ratio to the cell's datasheet limits, and an overcharge stop independent of the charger that senses the cell and opens its path (per cell for two or more in series) |
| `pcb.fault_current` | 0 | sourced | every conductor in `pcb_conductors` is guarded across the fault range: its protector's interrupt rating, the overload band (I2 <= 1.45 Iz, by analogy with IEC 60364-4-43) and IEC 60949's adiabatic limit at every trip point the cell can reach; a fuse, PTC, eFuse, protector FETs on either side or an in-pack PCM. A conductor nothing protects (`protected_by: []`) passes on a source of at most 15 W (IEC 62368-1's PS1) or a rating that carries the whole fault |
| `pcb.discharge_cutoff` | 0 | sourced | the highest per-cell cutoff that senses the cell (directly, through a named chain, or across a mating) and opens a switch in its path, or sits in the pack, is at or above the cell's minimum voltage |
| `pcb.mismate` | 0 | model-only | no wrong way a mating's record states (a reversed plug, a narrower cable, an offset header, a rotated header, the other FFC type, a plug slid in, a plug that bridges contacts), nor its own map, joins a source to its return or to another source; a reversed cell is blocked (behind a stated chain of series parts, a power switch among them) or limited; one loose cell turned in its holder is counted; every connector that carries a source is named by a mating |
| `pcb.usbc_receptacle` | 0 | sourced | each USB-C receptacle's CC pins are terminated apart, as its role asks (one Rd or Rp each, in the Type-C specification's band, or a named controller), a source switches VBUS through a part with a control pin, each USB 2.0 data pin is on a net with D+ apart from D-, and, where `pcb.pin_names` cannot see them, the VBUS, GND, A6/B6 and A7/B7 pins are each bussed |
| `pcb.antenna_keepout` | 1 | sourced | no track, via, other part's pad or zone sits on any copper layer in a radio module's antenna keep-out, as the board is stored and as KiCad's next refill pours it (the module's own footprint area, a vendor drawing registered by its pads, or a rule area the designer drew, picked by name, id or the part it covers); an antenna's own tracks on its named nets are its own copper; a module with no keep-out and no record skips by name |

The tier-0 gates read the board with this pack's own reader (`gates/_kicad.py`,
standard library only), so they run without KiCad, CI included. The tier-1 gates
run `kicad-cli` on a copy of the board or schematic (every sheet, the project file,
the library tables and the project libraries they name with it) under the gate's
out directory: the verdict is its report's count, less what a person excluded in
KiCad, and if kicad-cli's exit code disagrees with the report the gate errors.
Each verdict names the kicad-cli release that ran, and the release is part of
what the gate read: a control run under KiCad 9 and one under KiCad 10 are two
qualifications, never two outcomes of one (`gates/kicad-cli/`, one record per
release this pack was measured on: 9.0.9 and 10.0.8). A file newer than the
running kicad-cli reads (a KiCad 10 board under kicad-cli 9) is a skip that
names the release, as a missing tool is. The line keeps the count and the
release within the spine's 500 characters, and cuts the findings' own words
first. `pcb.spice` runs `ngspice -b` the same way, `pcb.enclosure_fit` runs
`kicad-cli pcb export step` and then OpenCascade (through CadQuery) in a child
process, and `pcb.antenna_keepout` refills the board's zones with KiCad's own
`pcbnew` in a child process before its two DRC runs. They read inside
subprocesses the trace cannot see into, so they are **opaque**: they re-run on every check where their tools exist (about a second
each on a small board; the fit about 4 s, the keep-out about 2 s a board) and
skip where they do not.

`pcb.pin_names`, `pcb.net_width` and `pcb.land_pattern` need `pcb.board_readable`:
on a board this reader cannot vouch for they are not run, and read Skipped,
`prerequisite failed`. On a board newer than the reader was checked through (a
file newer than KiCad 10's) the guard skips, and they read Skipped, `prerequisite
not established`: a newer format is a limit of the reader, not a defect in the
board. The battery, mis-mating and USB-C rules read the boards their records
name, which the guard may never read, so each makes the guard's own checks on its
board: it errors on one that fails them, and skips a newer one with the guard's
words.

**Which verdicts are this pack's own code.** KiCad decides DRC, ERC, parity and
whether each custom rule is live; ngspice decides the number. This pack's code
decides five things: pin-name agreement (a comparison of names the library
gives), the land fit (`gates/_kicad.py`'s `rigid_fit`, and its limit: model-only),
the four battery rules (pad-net lookups and published formulas on records a
person enters), the mis-mating rule (its own rule on the modes a person states:
model-only) and the USB-C rule (pad nets against the Type-C specification's
tables). Each has a known-bad control it fails on: `split_pin_pair`,
`mirrored_land` (1.9 mm against 0.3 mm), `ntc_swapped` (a 100 kOhm NTC in the
10 kOhm's place: -64.83 K), `lfp_cell` (a LiFePO4 cell on the 4.2 V charger:
1.1595), `fuse_trips_late` (a PTC of twice the trip current: 0.725),
`cutoff_drives_nothing` (the protector's DO on a net of its own: 2.30 V against
2.5 V), `ground_beside_vbus` (GND moved beside VBUS on both FFC housings: 1
hazard against 0) and `shared_cc` (one CC net for both pins: 1 violation against
0). Each control makes one change, and moves only its own rule.
`pcb.antenna_keepout`'s findings are KiCad's (its DRC and its zone filler); this
pack's code picks which of a footprint's rule areas is the antenna's and
registers a vendor drawing on its pads. Its control is `pour_under_antenna` (1
item against 0).

**The battery rules read records, bound to the design.** `pcb_batteries`,
`pcb_protectors`, `pcb_conductors` and `pcb_matings` are each an object keyed by
id, so a design with a cell in each half, or one protector IC that does four
jobs, is stated once and read by every rule. Each record names its board
(`board`, else `pcb_board`), and the ref, value and pins it describes, with a
`source`. A ref the board lacks, another value on that ref, or a pin with no pad
is an error ("the record is for another design"), so a generator that moves a
part or a value stales them. An unknown key is refused, naming the nearest known
one. A datasheet range (a comparator's trips, a regulation voltage) given as one
typical number is refused: give [min, max]. A field that takes one number
(`v_max`, `v_min`, `trip_a`) takes the guaranteed bound, never the typical: the
rule cannot tell them apart. Chemistry picks which rules apply, never a number:
every limit is the cell's own, from its datasheet. `pcb_matings` joins two
connectors' nets pin by pin, so an element on another board counts; a mating's
far side may also be a battery's pigtail or something outside every board, and
its `modes` say the wrong ways it can meet. `pcb_usbc` gives each USB-C
receptacle's role, and `pcb_rails` the nets a regulator or an outside supply
drives. A `pcb_matings` record is read whole by every rule that reads it, so a
mating any of them refuses is refused by all. The other records are read by
each rule for what it uses: only `pcb.usbc_receptacle` holds a `pcb_usbc`
record to its role's keys (`pcb.mismate` reads its pins), and a
`pcb_protectors` function is read by the rules that judge it (a
`reverse_block` only by `pcb.mismate`, for a battery mating that states
`reversed`). The baseline's records name
`selftest/assets/power_board.kicad_pcb` (made by `make_power_board.py`: placed,
netted, not routed), which only these six rules read, and its schematic
`power_board.kicad_sch` sits beside it (`make_power_schematic.py`), where
`pcb.charge_temperature` reads the NTC's symbol. `references/battery.md`,
`references/mismate.md` and `references/usb-c.md` hold every field.

**The antenna's keep-out, on every copper layer.** `pcb.antenna_keepout` finds
each radio module by itself: every footprint from KiCad's `RF_Module` library on
`pcb_board`, or on a board a `pcb_antennas` record names (57 modules in KiCad
9.0.9: BLE, Wi-Fi, sub-GHz, LoRa and UWB). Its keep-out is the footprint's own
rule area, the one that forbids tracks and copper pour. KiCad's own DRC holds that
area only on the layers the footprint names, and 10 of the 31 stock modules that
carry one name fewer than every copper layer: the ESP32-S3-WROOM-1 and
ESP32-WROOM-32 areas cover F.Cu, In1.Cu and B.Cu, so a ground pour on In2.Cu runs under the
antenna with `pcb.drc` clean. This gate copies the area to every copper layer,
forbids tracks, vias, pads and pours in it, and runs KiCad's DRC twice: on the board as
stored, and after KiCad's own zone refill. kicad-cli judges a pour by its stored
fill, so a pour moved under the antenna and not refilled reads clean until the
next refill puts the copper there. The module's own pads are its own copper and
are listed, not counted. So is another part's hole with no copper (a mounting
hole no wider than its drill, as KiCad's pcbnew reads its pad): KiCad's keep-out
reports it, and a screw in it is `lenses.md`'s. A hole with a copper ring counts.
An area's cutouts stay cutouts: the added area carries the outline and each
cutout, as KiCad saved them, so a feed track cut out of a chip antenna's
clearance is not a finding. A project that sets `items_not_allowed` to ignore,
or excludes a finding, does not hide one: the staged copy sets it back. It runs
only where the kicad-cli on PATH and the pcbnew it imports are a pair it was
measured on (`gates/pcbnew/`: kicad-cli 9.0.9 with pcbnew 9.0.9), and skips
under any other pair, naming both: kicad-cli 10.0.8 names no keep-out area for a
track, and the gate ties a finding to its part by that name, so under it a board
with two tracks in the keep-out read pass (measured, beside pcbnew 9.0.9 and
10.0.8). A finding that names none of the gate's areas is an error, never
passed over.

A record (`{id: {board?, ref, value, keepout, own_nets?, source}}`, held to the
board's ref and value) brings in what the library does not: a module from a
project's own library (`"keepout": "footprint"`), a vendor drawing registered on
the footprint by three or more numbered pads (`{"drawing": path}`), a rule area
the designer drew, such as a chip antenna's clearance on a bare SoC, by its name
(`{"rule_area": name}`), its id (`{"rule_area": {"uuid": id}}`) or the part whose
copper it holds (`{"rule_area": {"covers": "AE1"}}`, every such area, named or
not), or a module whose antenna is off the board (`"none"`, listed, not judged).
`own_nets` names a printed antenna's own nets (its feed and its ground arm): their
tracks that reach the part's pads through tracks on the pads' layers, and a via on
those tracks, are its own copper, listed and not counted, as its pads are. A pour
is never its own, nor anything beyond a via or another part's pad. A found
module with no keep-out in its footprint, and no record, skips by name, never
passes: 26 of the 57 stock modules carry none (the LoRa modules among them,
whose antenna is on a pad or a connector), and three carry only an area that
allows tracks or copper pour. The baseline's record names
`selftest/assets/radio_board.kicad_pcb` (made by `make_radio_board.py`), which
only this gate reads. `references/antenna-keepout.md` holds every field, the
survey and the measurements.

## The proposer: `pcb.autoroute`

    nopekit propose pcb.autoroute -- --board hardware/main.kicad_pcb [--passes 20] [--rip-up]

Routes the board with Freerouting, headless and on one thread, on a copy outside
the project, and records two inputs: `<board>.routed.kicad_pcb` and
`<board>.autoroute.json` (the Freerouting version, passes, track counts before and
after, and what it reported unrouted). It settles nothing. Adopting the routed
board, by copying it over the project's board, is a decision; then `pcb.drc`,
`pcb.unconnected` and `pcb.parity` judge it like any other board. Nothing that draws
copper judges it: the source pipeline's own "gap closer" drew shorts.

It needs a Python that imports KiCad's `pcbnew` (`--kicad-python` or
`KICAD_PYTHON`; Linux's system Python, or KiCad's own on macOS and Windows), Java
21+ (`--java`, `JAVA`, `JAVA_HOME` or PATH) and the Freerouting jar (`--jar` or
`FREEROUTING_JAR`; checked with 2.2.4). Without one it exits 2, naming what to
set, and records nothing. Freerouting's analytics are switched off; it still asks
GitHub for a newer version when it starts.

Existing tracks go to the router as fixed wiring, so a hand-routed USB pair rides
through. `--rip-up` routes from nothing. A run that leaves nets open is still a
proposal: `pcb.unconnected` says which, and `--passes` is the knob (a retry of the
same input draws the same copper).

## The proposer: `pcb.schematic_from_board`

    nopekit propose pcb.schematic_from_board -- --board hardware/main.kicad_pcb \
        --symbols hardware/main.symbols.json

A board a script generated has no schematic, so `pcb.erc` and `pcb.parity` skip
and `pcb.pin_names` has no names. No kicad-cli command writes a schematic from a
board; this proposer does, on KiCad's stock symbols: each part's symbol in its own
cell, a global label of its pad's net on each connected pin end, its Footprint
field the board's footprint name verbatim, every id a `uuid5` (the bytes repeat).
It writes `<stem>.kicad_sch`, a `sym-lib-table` of the libraries used, and
`<stem>.schematic.json` (the symbol per ref, each multi-unit part's unit map, the
parts on generic symbols, the pins left open with no statement, the flagged nets,
the variable that names the stock libraries, each library's sha256).

The symbols file holds the person's statements, never inferred: `symbols` (`{ref
or glob: "Lib:Symbol"}`, an exact ref first, then globs in the order written),
`no_connect` (`{ref: [pads]}`, pins open on purpose), `power_flags` (nets fed from
off the board) and `library` (more `.kicad_sym` directories). A part with no
stock symbol is mapped to `Connector_Generic:Conn_01xNN` by the person. It exits 2,
writes nothing and names every problem when a part matches no symbol, a pad with a
net has no pin of its number in any unit, a pad carries two nets, a symbol is
missing, or a statement names a pad or net the board lacks.

A multi-unit symbol (a dual op-amp, a quad gate, a dual MOSFET) is placed whole:
each unit in its own cell under the part's one ref, each pad's net on the pin of
its number in the unit that draws it, and a pin that every unit shares labelled
on every unit. KiCad's ERC names a unit left unplaced, and parity names its pins,
so both judge this as well.

The stock symbols are read from the directory that `KICAD10_SYMBOL_DIR`,
`KICAD9_SYMBOL_DIR` or `KICAD_SYMBOL_DIR` names, else from where KiCad's installer
puts them: `/usr/share/kicad/symbols`, the macOS app bundle's
`Contents/SharedSupport/symbols`, or `share\kicad\symbols` under the Windows
install for KiCad 10 or 9. The `sym-lib-table` names each stock library by the
variable it was found through. For a fixed path it uses `KICAD<major>_SYMBOL_DIR`
for the release `kicad-cli version` reports, or KiCad 9's name when no kicad-cli
answers, and the summary says which.

Adopting it (copying the files beside the board, under its name) is a person's
decision. Then the existing `pcb.erc` and `pcb.parity` judge it, and `pcb.parity`
is the check on the writer. When the generator moves the board, parity reads a
difference until someone proposes again: tag such claims `netlist`, which binds
`pcb.erc`, `pcb.parity` and `pcb.pin_names` together.

## The proposer: `pcb.battery_record_draft`

    nopekit propose pcb.battery_record_draft -- --board hardware/power.kicad_pcb \
        [--board hardware/main.kicad_pcb] [--battery J1[:4,1]]

The power records are the heaviest part of first use. This proposer writes a
draft of them from the boards, in the shapes the rules read: `pcb_batteries`,
`pcb_protectors`, `pcb_conductors`, `pcb_matings`, `pcb_usbc` and `pcb_rails`,
each an object keyed by id. It fills each record's board, ref and value, `where:
board` for a part on a board, and the pads that a part's pin names or its copper
show: a charger's thermistor pin and the resistor on its PROG pin, a protector's
DO and CO pins and the switch each drives, a fuse's two pads, a connector's pins
on a net a cell drives. It finds a battery by `--battery REF` (or
`REF:POS,NEG`, its + and - pads), KiCad's `Battery*` libraries, a BT ref, or a
connector whose value or footprint names a cell (Battery, LiPo, cell, AAA,
CR2032, 18650) or whose pins are named + and - (each of the last two
listed under `keep_or_delete`); a charger by a cell pin (BAT, VBAT, BATT) beside a
PROG, ISET or thermistor pin; a protector by DO and CO (or DOUT and COUT, or DSG
and CHG); a fuse by the `Fuse*` libraries or an F ref; a USB-C receptacle as
`pcb.usbc_receptacle` does; another USB connector's VBUS (micro-B, mini-B) as a
rail on its VBUS pin's net; a rail by a regulator's output and input pins, each
name's pins on one net; and a mating for every connector on a net that a cell, a
VBUS or a drafted rail drives, as `pcb.mismate`'s coverage rule asks. A cell
drives the nets of its pads, but not its `-` pad's or its thermistor pad's. When
no pad is named `-`, a net where a drafted charger's or protector's ground pin
sits is the cell's return, and drives nothing.

**It never fills a datasheet number, a flag or a choice that is yours** (a
chemistry, a cell count, what kind of element a part is, a role, a mating's far
side and modes). Each is a string that starts `FILL:` and says what to state,
with what the board shows beside it. No number and no true or false is anywhere
in the draft. Every record's source is a list that holds one `FILL:`, which says
`DRAFT by pcb.battery_record_draft`. It writes `<stem>.power_records.json` and
`<stem>.power_draft.json`. The summary gives each candidate with the pins and
nets that found it, and the path of every `FILL:` value. Under `keep_or_delete`
it names the records it may have drafted wrong: each rail (a filter, a voltage
reference and a driver have the same pin names), and the records on a cell's
nets when nothing names or shows its `-`. It also says what it cannot see: an
element inside the pack, a stop that firmware makes, which connector meets
which, a part whose pads carry no pin name, and a regulator whose output or
input pins sit on several nets. With more than one board, each id starts with
its board's file name, and with its folders when two boards have the same name.

A draft settles nothing. Every power rule refuses a `FILL:` value where it reads
a number, a flag or a word, and names it. Every rule also refuses a source that
is not a string. So an adopted draft errors until each value is stated and each
source is written. Adopting the draft (copying its records into the project's
params) is a person's decision, and so is each value. Write each source, in
place of its list, as one sentence that says where its numbers come from: the
rules check that a source says something, not what it says.

## What this pack CANNOT settle

Read this before you write a claim. Every one of these has passed a "0 violations,
0 unconnected" board in practice.

- **Whether the circuit is right.** DRC checks copper against rules. A pin map
  read wrongly from a datasheet, a missing pull-up, a regulator that cannot supply
  the load, a reversed diode: all legal copper, and all a clean ERC too. ERC
  checks how pins are tied (an open pin, a label nothing else shares, a power
  input nothing drives, two outputs together), never what the circuit does.
  `pcb.pin_names` catches one class (two nets on pins the part joins inside), and
  only for parts whose library symbol names those pins alike.
- **A board with no schematic.** A netlist built in a script has no ERC and no
  parity to check: `pcb.erc` and `pcb.parity` skip, and the script's dictionary is
  the only record of what connects to what. Tag a claim that every connection is
  made `copper-connectivity`, which binds `pcb.unconnected` alone, not
  `net-connectivity`, which brings in `pcb.parity`, whose skip would hold it.
- **What a written schematic cannot add.** On a schematic `pcb.schematic_from_board`
  wrote, parity checks only the writer's copy of the board's nets, never the
  design's intent; ERC judges nothing for a part on a generic or passive stand-in
  symbol (a module mapped to `Conn_01x43`); and a symbol chosen wrongly but with
  the pads' pin numbers passes both.
- **Parts that are not in a library.** A footprint drawn without a schematic carries
  no pin names, and `pcb.pin_names` skips. Its pin map is a claim for a person (an
  expert judgment) or for a pin table recorded from the datasheet.
- **Current through vias, pads and pours.** `pcb.net_width` judges tracks only. A
  via carries its own current limit; a pour is wider than any track and is not
  judged at all, so a net carried mostly by a pour reads pessimistically.
- **Which track carries a shared net's current.** A net named with one current is
  judged at it on every track: a return net named at a cell's current is judged
  at that current on every stub, a fine-pitch part's fan-out to its vias
  included, and fails on a sound board. Give the net a route (`{"amps", "from",
  "to", "rest_a"}`, two pads): its tracks on a route of the copper between them
  are judged at `amps`, the rest at `rest_a`. The route is read from the copper
  generously (a pad's circle, a pour's outline), so a track is read onto it, never
  off it; a pulse (a radio's transmit burst) is not a steady current, and is no
  `pcb_net_currents_a` value (`references/ipc-2221.md`).
- **A wrong fit nobody stated.** `pcb.mismate` judges the wrong ways a
  mating's record states (a narrower ribbon in a wider housing that puts the
  cell on ground, a reversed pigtail, a rotated header), and the pinouts on the
  boards. It reads records of which connectors and cables meet and the boards'
  pad nets; it sees no mechanical part. It cannot see a mode nobody stated (the
  coverage rule forces a statement for every connector that carries a source,
  not the completeness of its modes), a keyed housing forced past its key, a
  cable from another product, a source that lands on a GPIO or a data pin
  (listed, not judged), a part the wrong voltage damages through a signal pin,
  or the arc as a plug goes in. A source that meets another source's return
  only through a part (a fuse, a FET) is read as two nets. Two copies of one
  board file read as one board. Settle a fit it cannot see with an
  expert-judgment claim, and `lenses.md`, "A connector that fits the wrong
  cable".
- **USB-C beyond its termination.** `pcb.usbc_receptacle` judges CC per pin and
  per role, a source's VBUS switch and the two rows of USB 2.0 data. It does not
  judge SuperSpeed lanes, USB Power Delivery, VCONN, SBU, a controller's own Rd
  or Rp (its datasheet's), whether the device draws more than the source
  advertises, an open VBUS or GND pad, or what drives the VBUS switch's control
  pin and when (firmware that turns VBUS on before a sink attaches, or a
  pull-up nothing pulls down). Same pin name, same net (the four VBUS, the four
  GND, A6 and B6) is `pcb.pin_names`' on `pcb_board`, where the pads name those
  pins alike; on any other board, or with the pads not so named, this rule
  checks it. A resistor `others` names beside a CC termination is taken on its
  why: what lies beyond it is not read.
- **RF, signal integrity, EMC.** Antenna detuning, impedance, crosstalk, return
  paths: not modelled.
- **The antenna beyond the copper on its board.** `pcb.antenna_keepout` judges
  tracks, vias, other parts' pads and zone fills in the keep-out, and nothing
  else. Metal near the antenna once the board is in its case (a battery, a
  screw, a frame, a display's shield) and a hand around it detune it as surely
  as copper under it: that is `lenses.md`, "Metal and a hand around the
  antenna", and a physical claim, a range test in the hand with the case closed
  and the final battery fitted. Not judged either: whether the antenna sits at
  the board edge as the vendor asks, the size of the ground plane a chip antenna
  needs, a part body over the antenna with no copper in the area, copper text or
  a drawing on a copper layer (KiCad's keep-out does not report graphics:
  measured with kicad-cli 9.0.9; the pass says so), and what goes in a hole with
  no copper in the keep-out (listed, not counted). A module from a project's own
  library is found only by a record. A footprint whose one copper keep-out
  guards something other than its antenna is read as the antenna's: give a
  record. And a chip antenna's feed must reach it: draw the rule area around the
  feed, or cut the feed out of it, as the vendor's drawing does, or the feed
  track is a finding.
- **Fit beyond the solids.** `pcb.enclosure_fit` checks the parts' 3D models
  against the enclosure's solid, and against the other boards in it, at one
  placement. A model drawn wrong, a cable's bend radius, a plug's mating
  clearance, a tolerance stack, assembly order, a lid that flexes: none of them
  is in two solids. A part with no model skips the gate rather than pass it,
  unless the project states its height; the courtyard's box at that height is
  then all the fit sees of it. And a part that passes through an opening cut
  wide enough to keep the claim's clearance (a knob, a button, a light pipe)
  reads clear, however far it stands proud of the face; so does one whose
  contact against the opening is declared. A tighter opening fails on
  clearance, and that fail still says nothing about how far the part reaches:
  the gate measures shared volume and clearance, not how far a part should
  reach. That is `lenses.md`, "A part taller than its cavity". Two models that
  overlap by design (a mated pair whose vendor models interpenetrate) read as a
  collision, and no contact excuses one: a wrong FAIL, read against the drawing.
  One condition judges every board: the claim tagged `enclosure-fit`. A footprint
  with no type and no model (a mounting hole, a fiducial, a logo) is not asked for,
  and the verdict names it: a part fitted by hand and drawn so is not fitted until
  its footprint shows a model or the project states its height. A declared height
  stands in for a model only where this host does not have the model: on a host
  that has it, the model is fitted and the height is named unused.
- **Whether the SPICE deck is the circuit.** ngspice solves the deck it is given.
  The deck is someone's model, typed by hand; `pcb.spice` refuses a run that
  dropped a line, an option name ngspice does not know, a command it does not
  have, a measure defined twice and a `.meas tran` window outside the run, and
  nothing more. A `set` of a variable ngspice never reads is a user variable to
  it, and is not checked. Option VALUES (`method=gearr`) and `.meas ac`
  and `.meas dc` windows are not checked. Read the deck against the schematic.
- **Every custom rule's every clause.** `pcb.drc` shows each rule selects
  something and each property it reads exists. A misspelled function inside a
  negated or OR'd clause is not caught; a rule whose condition reads B is probed
  on the `&&` terms that read A alone, and one with no such term (`A.Type ==
  'Pad' || B.Type == 'Pad'`) is shown live only when it fires on one item; live
  means the rule selects an item, not that its constraint can apply to it (a
  `track_width` rule that selects only vias is live); and a real property that
  sits on no item of this board (a via rule on a board with no vias) is a false
  error.
- **A board saved by a KiCad newer than 10.** The reader was checked against
  KiCad 9 and KiCad 10 boards: `selftest/assets/kicad10/` holds the baseline as
  KiCad 10.0.8 saved it (board format 20260206, nets by name, no net codes) and a
  second board with inner layers, a stated stackup, every via kind, a filled pour
  and a keep-out, and the reader reads them as their KiCad 9 originals
  (`make_kicad10.py` says how they were made). A file newer than that reads
  Skipped on the tier-0 gates, the power rules, `pcb.fab_rules`'s service guard,
  `pcb.enclosure_fit` and `pcb.antenna_keepout` ("this reader was checked through
  board format 20260206 (KiCad 10.0)"), never Checked and never Failing, until a
  board that KiCad saved is committed and read the same way. The five gates that
  run kicad-cli for its report (`pcb.drc`, `pcb.unconnected`, `pcb.erc`,
  `pcb.parity`, `pcb.fab_rules`) are KiCad's own answer, and run only with a
  kicad-cli that reads the file: kicad-cli 9.0.9 prints "Failed to load board" on
  a KiCad 10 file and exits 3, and the gate reads Skipped, naming the release and
  the format it reads through. The KiCad 10 schematic (format 20260306) is read
  the same way.
- **A KiCad 10 board in the fit and the keep-out.** `pcb.enclosure_fit` (its STEP
  export) and `pcb.antenna_keepout` (its zone refill, in `pcbnew`) hand the board
  to KiCad's own tool with no check of what the running release reads, and were
  never run on a KiCad 10 board. They skip one ("this gate was checked through
  board format 20241229 (KiCad 9.0)"), under any release. Without that skip,
  under KiCad 9's tools, both errored on a KiCad 10 board (measured: kicad-cli
  9.0.9 exits 3 in the export; pcbnew 9.0.9's refill raises an AttributeError).
  The move: run each on a KiCad 10 board under kicad-cli and pcbnew 10, send its
  tool failure through a tool-limit check, and then drop
  `_kicad.TOOL_RUN_VERSION`.
- **The keep-out under a pair of releases it was not measured on.**
  `pcb.antenna_keepout` skips, naming both releases, unless the kicad-cli on PATH
  and the pcbnew it imports are a pair a record in `gates/pcbnew/` names
  (kicad-cli 9.0.9 with pcbnew 9.0.9). kicad-cli 10.0.8 is in no pair: it writes
  "Items not allowed" for a track in a keep-out, naming no area, and the gate
  ties each finding to its part by that name. The move: tie such a finding to its
  part, run every keep-out test on the pair, and add it to its pcbnew record.
- **What KiCad's library checks compare with.** `lib_footprint_mismatch`,
  `lib_footprint_issues` (DRC) and `lib_symbol_issues`, `lib_symbol_mismatch`,
  `footprint_link_issues` (ERC) compare the design with the library each table
  entry names. An entry in the project (`${KIPRJMOD}/...`) is staged with the
  board and reads the same everywhere; an entry through KiCad's own variable
  (`${KICAD9_FOOTPRINT_DIR}`) is the host's library, whose copy moves with the
  KiCad installed: KiCad 10.0.8's footprints differ from the 7.0.11 copies this
  pack's baseline was drawn from, and it reports each one. Those findings
  count, at every severity, like any other: a footprint that has drifted from
  its library may be a real edit. Keep the libraries the design uses in the
  project, as the baseline does (`selftest/assets/lib/`), and its verdict reads
  the same on every host.
- **The land beyond its centres.** `pcb.land_pattern` judges pad centres in the
  top view, one rigid fit; pad sizes, paste, courtyard and pads the drawing omits
  (mounting pads) are not judged, so a pad of the wrong size, or turned 90 degrees
  on the right centre, passes. A land whose paired pads all sit on one line is
  its own mirror image turned a half turn, so its mirror is not seen: a drawing
  of a one-row part that omits its mounting pads is such a land. The drawing is
  a person's transcription. A part
  that repeats so many functions that both the full search and the anchored one
  pass 5040 pairings (a ground row of eight, a ball grid) errors: split a function
  the part repeats.
- **A series pair on a person's word.** `pcb_pin_names_series` clears a two-net
  pin name on the ref it names, as the person states it, with its source. The
  gate holds the pair to the part (the only two pins of that name), never to the
  datasheet: a statement written to clear a real short clears it.
- **The cell beyond its records.** The battery rules are as true as the records a
  person enters from the datasheets, and these stay out of reach:
  - the NTC's thermal coupling to the cell: a board NTC far from the cell senses
    the board. Settle it with a physical claim at the window's edges;
  - a window, a charge voltage or a cutoff set by firmware or a register: the
    rules name it and skip, unless something they can see fails;
  - JEITA's reduced-rate zones: only the outer window is judged;
  - the single-fault overcharge element's threshold: its presence and its copper
    are judged, its number is printed (no general source gives a cell's
    single-fault voltage limit);
  - an in-pack PCM's circuit, counted on its datasheet's word;
  - IEC 60364-4-43's I2 <= 1.45 Iz is written for building installations: here it
    is a stated analogy, and `pcb.fault_current` turns on `continuous_a` and
    `limit_c`, so their sources matter most;
  - a conductor nothing protects passes on a source IEC 62368-1 calls PS1 (at
    most 15 W into any load): the standard's word that such a source ignites
    nothing, not a measurement of the conductor;
  - one conductor carries the whole fault: contacts in parallel get no credit;
  - a lithium primary cell charged by another rail (UL 1642): a lens, not a rule.
- **Everything the fab checks.** `pcb.fab_rules` holds the minimums the fab
  publishes and KiCad can test: widths, spacing, drills, via pads, annular rings,
  edge clearance, silkscreen text, and silkscreen to pad (silk on silk is
  `pcb.drc`'s). Board size, mask webs, panelisation and the fab's
  own DFM review are not in it, and a fab's page changes: the file says the day
  it was read. A flex, rigid-flex or HDI board skips on the bundled JLCPCB file,
  whose one service is FR-4 with through vias: a fab file with a service that
  lists its dielectric and vias judges it. A board that states no stackup is read
  as KiCad's default FR4 core, as the job file KiCad hands the fab says.
- **Anything about a built board.** Assembly, solder, first power-up: physical
  claims, Pending build until a person records a result.

## Claim tags

Gates list everything they bear on; a claim carries the narrowest tag that says
what it asserts.

| Tag | Bound by |
|---|---|
| `pcb` | every gate here (too broad for a claim: use a narrower one) |
| `design-rules`, `copper-clearance` | `pcb.drc` |
| `pcb-manufacturability` | `pcb.drc`, `pcb.fab_rules` |
| `fab-limits` | `pcb.fab_rules` |
| `circuit-simulation` | `pcb.spice` |
| `enclosure-fit` | `pcb.enclosure_fit` |
| `net-connectivity` | `pcb.unconnected`, `pcb.parity` |
| `copper-connectivity` | `pcb.unconnected` |
| `netlist` | `pcb.unconnected`, `pcb.pin_names`, `pcb.erc`, `pcb.parity` |
| `land-pattern` | `pcb.land_pattern` |
| `battery-safety` | `pcb.charge_temperature`, `pcb.charge_limits`, `pcb.fault_current`, `pcb.discharge_cutoff`, `pcb.mismate` |
| `charge-protection`, `charge-temperature` | `pcb.charge_temperature` |
| `charge-limits`, `charge-voltage`, `charge-current` | `pcb.charge_limits` |
| `overcurrent-protection` | `pcb.fault_current` |
| `deep-discharge` | `pcb.discharge_cutoff` |
| `mismate`, `connector-keying` | `pcb.mismate` |
| `usb-c`, `charging-interface` | `pcb.usbc_receptacle` |
| `antenna-keepout`, `rf-layout` | `pcb.antenna_keepout` |
| `schematic`, `electrical-rules` | `pcb.erc` |
| `schematic-parity` | `pcb.parity` |
| `pin-map`, `short-circuit` | `pcb.pin_names` |
| `trace-width`, `current-capacity` | `pcb.net_width` |
| `power-integrity` | `pcb.net_width`, `pcb.spice` |
| `pcb-model-validity` | `pcb.board_readable` |

A claim "every connection is made" tagged `net-connectivity` stays Checked
through a silkscreen warning; tagged `pcb`, it would read Failing on it. On a
board with no schematic, tag it `copper-connectivity`, which binds
`pcb.unconnected` alone: `net-connectivity` brings in `pcb.parity`, which skips
there, so the claim would read Skipped beside a pass.
A claim that the board is also the schematic's carries `schematic-parity` as
well.

## Tags this pack no longer lists

**Every tag here is this pack's own.** Until general-packs H1 and H2, five words
this pack's gates listed were listed by other bundled packs' gates too, so a
claim that carried one was covered by the gates of both: a claim about a printed
part tagged `manufacturability` read a board's DRC finding. (What slipped
through, in the holdout run on outside designs: `connectivity` bound
`cad.assembly_connected` beside `pcb.unconnected` and `pcb.parity`; `clearance`
bound `cad.clearance` beside `pcb.drc`; `manufacturability` bound the cad-solid
mesh gates, every fdm-print gate and `bom.process_rules` beside `pcb.drc` and
`pcb.fab_rules`; `packaging` and `interference` bound `cad.bounding` and
`cad.clash` beside `pcb.enclosure_fit`.) Each moved; a claim that still carries
the old word binds no gate of this pack (it reads Gap here, or is bound to the
other pack's gates alone). Retag it:

- `clearance` (cad-solid's `cad.clearance`): `design-rules` or
  `copper-clearance` for `pcb.drc`;
- `manufacturability` (cad-solid's mesh gates, every fdm-print gate, sourcing's
  `bom.process_rules`): `pcb-manufacturability` for `pcb.drc` and
  `pcb.fab_rules`, or `fab-limits` for `pcb.fab_rules` alone;
- `connectivity` (cad-solid's `cad.assembly_connected`): `net-connectivity` for
  `pcb.unconnected` and `pcb.parity`, `copper-connectivity` for
  `pcb.unconnected` alone, or `schematic-parity` for `pcb.parity` alone (the
  board against its schematic);
- `packaging` (cad-solid's `cad.bounding`): `enclosure-fit` for `pcb.enclosure_fit`;
- `interference` (cad-solid's `cad.clash`): `enclosure-fit` for
  `pcb.enclosure_fit`.

## Units and frames

Millimetres for every length (KiCad's file unit), amperes, degrees C of rise above
ambient, ounces of copper per square foot (1 oz = 35 um = 1.378 mil). KiCad's
board frame is Y-down. `pcb.net_width` reports the rise in C against
`pcb_temp_rise_limit_c`. `pcb.spice` reports in `pcb_spice_units`, which the
project states: ngspice computes in SI (V, A, s) and the gate cannot tell which
quantity a `.meas` is.

**`pcb.land_pattern` judges one footprint in its own frame, seen from the top**, in
mm: each pad's centre as KiCad writes it, local to the footprint, y down, and a
footprint on `B.Cu` with its y negated back (KiCad 9 writes a flipped footprint's
local y negated). The drawing record states its own y axis (`y_axis`: `"up"` as a
drawing is printed, `"down"` as a footprint is): read the wrong way, a land is
mirrored, and the fit allows no mirror. The verdict is mm against half the
smaller side of the smaller pad of the worst pair.

**The battery rules judge the boards their records name**: kelvin of headroom
(`pcb.charge_temperature`, a difference of temperatures, so a claim stated in K
is compared with it, against 0), ratios against 1.0 (`pcb.charge_limits`, the
charger's worst corner over the cell's limit; `pcb.fault_current`, the least of
three protection margins), and volts (`pcb.discharge_cutoff`, the highest
counted cutoff against the cell's minimum). `pcb.mismate` counts hazards, each
pair of source nodes once however many wrong ways make it, and
`pcb.usbc_receptacle` counts violations, each against 0; resistances are in
ohms, written as a board writes them (5.1k). `pcb.antenna_keepout` counts the
items KiCad finds in a keep-out against 0, each once over the stored and the
refilled copy, in the board's own frame (a footprint's rule area is saved there;
a drawing is carried there by its pads).

**`pcb.enclosure_fit` judges the populated boards in the enclosure's frame.** A
board is first as `kicad-cli pcb export step --user-origin 0x0mm` writes it: the
board file's (0, 0) at the origin, x right, **y up (KiCad's y negated)**, z up from
the board's bottom face. `pcb_board_placement` turns it `rot_x_deg` about x, then
`rot_y_deg` about y, then `rot_z_deg` about z (one 3x3 rotation, about the
enclosure's own axes: face down is `rot_x_deg` 180), then moves it by `x`, `y`,
`z` mm. The enclosure's STEP is its material (walls and lid), in mm, in its own
frame. The verdict is the nearest distance, in mm, against the limit of the claim
tagged `enclosure-fit` (`>=`); any shared volume fails it, and a board body may
touch the enclosure (standoffs) but not cut in. `pcb_enclosure_fits` states
several boards, and boards in one enclosure (enclosure files with the same bytes,
however each path is spelled) keep the limit from each other, their bodies
included. `pcb_enclosure_contacts` names a part that touches one feature by
design: a region of the enclosure (`region_mm`, a box in its frame, that takes
out material no further than twice the claim's limit past the part's own box) or
a shape of another board (`<fit>:<ref>`). Its distance to that feature is listed
and left out; against the rest it keeps the limit, and shared volume still fails
it. The regions are held to that reach in a pass of their own, before the full
fit, and every region refused is listed in one run with the box to draw.
`pcb_part_heights_mm` gives a part whose model is not on this host (none named,
or one found nowhere here) its courtyard's box, or the outline its `box_mm`
states, raised to a stated height. `pcb_model_dirs` (`{variable: folder}`, from
the project root or absolute) names the folder a model path variable stands for:
a model naming `${KICAD9_3DMODEL_DIR}`, or a variable of the project's own, is
looked for there alone, and the staged board names the folder in its place, so
kicad-cli reads the same file. The gate reads no environment variable, so a
3D library outside KiCad's own install folders is stated here, in the key. Each
helper call gets `pcb_fit_timeout_s`, or 120 s and 5 s for each part it handles.
`references/enclosure-fit.md` has each in full.

## Pointing a project at its board

```json
"pcb_board": "hardware/main.kicad_pcb",
"pcb_net_currents_a": {"VBUS": 0.5, "VBAT": 1.2,
                       "GND": {"amps": 1.2, "from": "BT1.2", "to": "U5.3", "rest_a": 0.3}},
"pcb_temp_rise_limit_c": 10.0,
"pcb_copper_oz_outer": 1.0, "pcb_copper_oz_inner": 0.5,
"pcb_fab": "jlcpcb", "pcb_fab_service": "standard",
"pcb_spice_deck": "hardware/sim/vbus.cir",
"pcb_spice_measure": "vbus_min", "pcb_spice_min": 4.75, "pcb_spice_units": "V",
"pcb_enclosure_step": "cad/out/enclosure.step",
"pcb_board_placement": {"x": 2.0, "y": 17.0, "z": 4.0, "rot_x_deg": 0, "rot_z_deg": 90},
"pcb_enclosure_contacts": {"J5": {"cutout": {"region_mm": [[-1, 2, 4], [3, 12, 9]],
                                             "reason": "the receptacle's shell seats in its cutout"}}},
"pcb_model_dirs": {"KICAD9_3DMODEL_DIR": "hardware/3dmodels"},
"pcb_part_heights_mm": {"SW2": {"h": 3.5, "source": "the switch's datasheet, seated height"},
                        "LS1": {"h": 2.8, "box_mm": [[-6, -6], [6, 6]],
                                "source": "the speaker's drawing; its footprint has no courtyard"}},
"pcb_land_drawings": {"U3": "hardware/lands/u3.json"},
"pcb_pin_names_series": {"FL1": {"pairs": [["1", "8"], ["2", "7"]],
                                 "source": "the filter array's datasheet, channel table"}},
"pcb_batteries": {"cell": {"board": "hardware/main.kicad_pcb",
                            "terminals": {"ref": "BT1", "value": "BATT", "pos": "1", "neg": "2"},
                            "chemistry": "li-polymer", "series": 1, "parallel": 1,
                            "cell": {"upper_charge_v": 4.25, "discharge_v_min": 2.8,
                                     "charge_a_max": 0.8, "charge_temp_c": [0, 45],
                                     "short_a": [8, 30]},
                            "source": "the cell's datasheet, its revision and section"}},
"pcb_protectors": {"pcm": {"battery": "cell", "kind": "pack-pcm", "where": "in-pack",
                           "functions": {"overdischarge_v": {"v_min": 2.9, "per_cell": true}},
                           "source": "the pack's drawing"}},
"pcb_matings": {"cell": {"a": {"ref": "BT1", "value": "BATT"}, "b": {"battery": "cell"},
                         "modes": ["reversed"], "source": "an unkeyed two-pin pigtail"},
                "usb": {"a": {"ref": "J1", "value": "USB-C"}, "b": {"external": "a charger"},
                        "modes": [], "source": "Type-C: both orientations are correct"}},
"pcb_usbc": {"usb": {"ref": "J1", "value": "USB-C", "role": "sink", "usb2": true,
                     "reads_rp": false, "cc_resistor_tol_pct": 1,
                     "source": "5.1k 1 % on each CC pin; draws default power only"}},
"pcb_antennas": {"lora": {"ref": "U4", "value": "RADIO_MODULE",
                          "keepout": {"drawing": "hardware/lands/radio_module.json"},
                          "source": "the module's integration guide, its revision and figure"},
                 "chip": {"ref": "AE1", "value": "CHIP_ANTENNA",
                          "keepout": {"rule_area": "AE1 clearance"},
                          "source": "the antenna's datasheet, its layout figure"},
                 "printed": {"ref": "AE2", "value": "PIFA",
                             "keepout": {"rule_area": {"covers": "AE2"}},
                             "own_nets": ["ANT_FEED", "GND"],
                             "source": "the inverted-F antenna's design note, its figure"}}
```

`references/battery.md` ("Every field") gives each power record's fields, required
and optional, and `selftest/baseline.json`'s `_notes` give every key's fields; its records
show a charger, a protector, a PTC, a wire, four matings (a flex a narrower cable
also fits, the cell's keyed housing, the USB-C receptacle and the wire) and a
USB-C sink; `references/battery.md`, `references/mismate.md` and
`references/usb-c.md` say what each rule reads from them.

Each family skips without its keys: no fab named, no deck, no enclosure or no
placement is a skip that says which key, never a default. The enclosure's
clearance is the claim's (tagged `enclosure-fit`, `>=` in mm): with an enclosure
and no such claim the gate errors, and `pcb_enclosure_clearance_mm`, where the
limit was before, is refused.

The board's `.kicad_pro` (rules, net classes, ERC and DRC exclusions),
`.kicad_dru` (custom rules) and `.kicad_sch` (the root schematic, with every sheet
under it), beside it with the same name, go to `kicad-cli` with it, and so do the
project's `sym-lib-table` and `fp-lib-table`. Keep those two in the project: on a
machine whose KiCad was never configured, ERC otherwise warns about every symbol
("library not in the current configuration"). Each library a table names through
`${KIPRJMOD}` and that is inside the project goes too, at its place relative to
the board (`${KIPRJMOD}/../libs/...` included, so the board sits one directory
down in the stage). An entry the stage cannot take is named in the verdict: one
with nothing at its path ("nothing there"), and one outside the project root (or
outside the board's own directory, for a board outside the project). An entry
through any other variable is the host's (above). `pcb_schematic` names another
root schematic for `pcb.erc`; `pcb.parity` always compares with the board's own,
as KiCad does.
**To waive a DRC or ERC finding, exclude it in KiCad** and commit the `.kicad_pro`:
the exclusion is then a named, reviewable record, and KiCad's report marks the
finding excluded, with the exclusion's comment. `pcb.drc`, `pcb.unconnected`,
`pcb.erc` and `pcb.parity` count it apart and say it, by kind, with the first
comment; `pcb.fab_rules` still counts a fab finding the project excludes, since
the fab's published minimum is not the project's to waive. Never narrow
`pcb_drc_severity` or `pcb_erc_severity` to make a warning go away; that hides
every warning, including the next one. A design checked as someone else made it
(a checkout of an outside design) carries only its authors' exclusions: keep the
board, its schematic and its `.kicad_pro` in your project as your own copy,
exclude there in KiCad with a comment that says why (a PWR_FLAG on a pin the
library types bidirectional reads as `pin_to_pin` until a person says it is not
a fault), and point `pcb_board` at the copy. Each exclusion is then your
reviewable line, and the authors' file is untouched.

A board updated from its schematic ("Update PCB from Schematic") carries each pad's
library pin name. A board generated by a script must copy those names itself (the
baseline's `make_baseline.py` shows how), or `pcb.pin_names` has nothing to read.

## The physics, in one paragraph

A track's resistance heats it: R = rho L / (w t), and copper's rho is 1.7e-8 ohm m at
20 C, so 10 mm of 0.25 mm track in 1 oz copper is about 19 mOhm. IPC-2221 fits
the measured temperature rise of a track carrying steady current: I = k dT^0.44
A^0.725, A in square mils, k 0.048 on an outer layer and 0.024 inside the stack,
where nothing convects. The rise goes as I^2.27 and A^-1.65: halve the width and
it rises about 3.1 times, double the current and about 4.8 times. A 0.25 mm outer
track in 1 oz carries 0.5 A at 2.8 C and 1 A at about 13.5 C; the same track inside
the stack, same copper, runs about 4.8 times hotter at the same current. If a rise comes
back under 1 C or over 100 C, look at the units first.

## Where to look next

- `references/ipc-2221.md`: the formula, its fitted range, and why not IPC-2152.
- `references/kicad-cli.md`: the DRC and ERC flags this pack uses, exit codes, the
  reports, parity.
- `references/fab-limits.md`: how `pcb.fab_rules` turns a fab's page into rules,
  JLCPCB's numbers, and how to add a fab.
- `references/enclosure-fit.md`: the limit, the frames and the rotation, several
  boards, what kicad-cli leaves out, declared heights, contacts by design, and
  what the fit measures.
- `references/spice.md`: what `pcb.spice` runs, refuses and reads.
- `references/battery.md`: the four battery rules on the baseline's own parts,
  the records, the NTC arithmetic, IEC 60949 and where every number comes from.
- `references/mismate.md`: the modes, what counts as a source and a hazard, the
  coverage rule, a reversed cell, and what the rule cannot see.
- `references/usb-c.md`: the roles, the Rd and Rp bands with their tables, and
  what the EU's common-charger rule covers.
- `references/antenna-keepout.md`: which parts are found, which rule area is the
  antenna's, the records and drawings, the refill, and the stock library survey.
- `references/what-slipped-through.md`: the defects the source pipeline's checks
  passed, and which gate here answers each.
- `references/installing.md`: getting `kicad-cli` (KiCad 10 or 9), ngspice, CadQuery,
  `pcbnew` for the keep-out's refill, and the proposer's Java and Freerouting.
- `lenses.md`: how to attack a board before anyone orders it.
