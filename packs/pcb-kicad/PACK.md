# pcb-kicad

Checks a printed circuit board drawn in KiCad 6 to 9, and its schematic: KiCad's
own design rule check, connectivity, electrical rules check and schematic parity,
the library's pin names against the board's nets, trace heating per IPC-2221, a
fab's published limits, a measurement ngspice makes on the project's circuit
deck, the populated board's fit in its enclosure, each footprint's land against
its vendor's drawing, and three rules for a board that carries a LiPo cell.

**Before you trust a Checked claim from this pack,** know what it is: a board that
breaks none of KiCad's rules, with every connection made, no part shorting two of
its own nets, no named track running hotter than you allowed, drawn from a
schematic that breaks none of KiCad's electrical rules and that it matches, inside
the fab's limits, with a simulated value inside its band and every part clear of
the enclosure. It is not a
circuit that works. Nothing here has seen the board powered.

## What this pack settles

| Gate | Tier | Basis | Settles |
|---|---|---|---|
| `pcb.board_readable` | 0 | model-only | the board is a KiCad 6-9 file whose every track names a defined net (the guard); nets read by code and by name; a newer file is skipped, never failed |
| `pcb.pin_names` | 0 | sourced | pins the library gives one name (a TVS array's two `COM`, a part's several `GND`) carry one net, except the ends of a series element stated per ref (`pcb_pin_names_series`); the names from the schematic's symbols where one sits beside the board, else the pads' `pinfunction` |
| `pcb.land_pattern` | 0 | model-only | each footprint `pcb_land_drawings` names fits its vendor's drawing: pads paired by pin function (anchored on the pads of a function of their own when the pairings are too many to try), one rigid fit with no mirror, every centre within half its smaller pad's smaller side |
| `pcb.net_width` | 0 | sourced | every track of each named net stays inside its temperature-rise limit, IPC-2221; the detail names the hottest stretch |
| `pcb.drc` | 1 | established | `kicad-cli pcb drc` finds nothing, at every severity by default, and every custom rule in the board's `.kicad_dru`, and every property its conditions read, is shown to select something |
| `pcb.unconnected` | 1 | established | KiCad finds no connection the netlist asks for and the copper does not make |
| `pcb.erc` | 1 | established | `kicad-cli sch erc` finds nothing in the schematic, at every severity by default |
| `pcb.parity` | 1 | established | KiCad finds no difference between the board and its schematic: parts, values, footprints, pad nets (an open pin on a pad with no net is agreement) |
| `pcb.fab_rules` | 1 | sourced | KiCad's DRC with the named fab's published minimums, for the service the board is ordered on, as the only custom rules finds nothing past them; a dielectric or via kind the service does not list skips |
| `pcb.spice` | 1 | established | a `.meas` result of `ngspice -b` on the project's deck stays inside `pcb_spice_min` / `pcb_spice_max`; an option name ngspice does not know (on a `.options` card or an `option` command), a measure two `.meas` cards define, or a `.meas tran` window outside the run, is refused first |
| `pcb.enclosure_fit` | 1 | established | the board, every part's 3D model in place, shares no volume with the enclosure, and each part keeps its clearance (OpenCascade) |
| `pcb.charge_protection` | 0 | sourced | the charger in `pcb_charger` has its THERM pin wired to the NTC (and not tied to its own pins), and a safety timer |
| `pcb.fuse_conductor` | 0 | sourced | the fuse in `pcb_fuse` sits between the cell and the conductor in `pcb_conductor`, which IEC 60949 says does not reach its limit before the fuse's guaranteed trip time |
| `pcb.discharge_cutoff` | 0 | sourced | the highest cutoff over the cell (its PCM, or a fitted part sensing the cell's net directly or through the series part its record names) is at or above the cell's minimum voltage |

The tier-0 gates read the board with this pack's own reader (`gates/_kicad.py`,
standard library only), so they run without KiCad, CI included. The tier-1 gates
run `kicad-cli` on a copy of the board or schematic (every sheet, the project file
and the library tables with it) under the gate's out directory: the verdict
is KiCad's exit code and its report's count, and if the two disagree the gate
errors. `pcb.spice` runs `ngspice -b` the same way, and `pcb.enclosure_fit` runs
`kicad-cli pcb export step` and then OpenCascade (through CadQuery) in a child
process. They read inside subprocesses the trace cannot see into, so they are
**opaque**: they re-run on every check where their tools exist (about a second
each on a small board; the fit about 4 s) and skip where they do not.

`pcb.pin_names`, `pcb.net_width` and `pcb.land_pattern` need `pcb.board_readable`:
on a board this reader cannot vouch for they are not run, and read Skipped,
`prerequisite failed`. On a board newer than the reader was checked through (a
KiCad 10 file) the guard skips, and they read Skipped, `prerequisite not
established`: a newer format is a limit of the reader, not a defect in the
board. The power rules read the boards their records name, which the guard may
never read, so each makes the guard's own checks on its board: it errors on one
that fails them, and skips a newer one with the guard's words.

**Which verdicts are this pack's own code.** KiCad decides DRC, ERC, parity and
whether each custom rule is live; ngspice decides the number. This pack's code
decides three things: pin-name agreement (a comparison of names the library
gives), the land fit (`gates/_kicad.py`'s `rigid_fit`, and its limit: model-only),
and the three power rules
(pad-net lookups and IEC 60949's formula on records a person enters). Each has a
known-bad control it fails on: `split_pin_pair`, `mirrored_land` (1.9 mm against
0.3 mm), `ntc_unwired`, `thin_conductor` (the area at which the formula puts the
conductor at its limit in the trip time / 1.15) and `cutoff_unwired`.

**The power rules read records, bound to the design.** `pcb_cell`, `pcb_charger`,
`pcb_fuse`, `pcb_conductor` and `pcb_cutoffs` each name the board (`board`, else
`pcb_board`), and the ref, value and pins they describe, with a `source`. A ref the
board lacks, another value on that ref, or a pin with no pad is an error ("the
record is for another design"), so a generator that moves a part or a value
stales them. The baseline's records name `selftest/assets/lipo_charger.kicad_pcb`
(made by `make_power_board.py`: placed, netted, not routed), which only these
three rules read.

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
`<stem>.schematic.json` (the symbol per ref, the parts on generic symbols, the
pins left open with no statement, the flagged nets, each library's sha256).

The symbols file holds the person's statements, never inferred: `symbols` (`{ref
or glob: "Lib:Symbol"}`, an exact ref first, then globs in the order written),
`no_connect` (`{ref: [pads]}`, pins open on purpose), `power_flags` (nets fed from
off the board) and `library` (more `.kicad_sym` directories). A part with no
stock symbol is mapped to `Connector_Generic:Conn_01xNN` by the person. It exits 2,
writes nothing and names every problem when a part matches no symbol, a pad with a
net has no pin of its number, a pad carries two nets, a symbol is missing or has
more than one unit, or a statement names a pad or net the board lacks.

Adopting it (copying the files beside the board, under its name) is a person's
decision. Then the existing `pcb.erc` and `pcb.parity` judge it, and `pcb.parity`
is the check on the writer. When the generator moves the board, parity reads a
difference until someone proposes again: tag such claims `netlist`, which binds
`pcb.erc`, `pcb.parity` and `pcb.pin_names` together.

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
  made `copper-connectivity`, not `connectivity`, or `pcb.parity`'s skip holds it.
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
- **What happens when someone plugs in the wrong thing.** A narrower ribbon can
  seat in a wider housing and put the battery on ground. That is a mechanical fact
  with an electrical result, and no check here sees it: an expert-judgment claim,
  and `lenses.md`, "A connector that fits the wrong cable".
- **RF, signal integrity, EMC.** Antenna detuning, impedance, crosstalk, return
  paths: not modelled.
- **Fit beyond the solids.** `pcb.enclosure_fit` checks the parts' 3D models
  against the enclosure's solid at one placement. A model drawn wrong, a cable's
  bend radius, a plug's mating clearance, a tolerance stack, assembly order, a
  lid that flexes: none of them is in two solids. A part with no model skips
  the gate rather than pass it. And a part that passes through an opening cut
  wide enough to keep `pcb_enclosure_clearance_mm` (a knob, a button, a light
  pipe) reads clear, however far it stands proud of the face. A tighter opening
  fails on clearance, and that fail still says nothing about how far the part
  reaches: the gate measures shared volume and clearance, not how far a part
  should reach. That is `lenses.md`, "A part taller than its cavity".
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
- **A board saved by KiCad 10.** KiCad 10 writes board format 20260206, with
  nets by name and no net codes. The reader reads nets by name, but it was
  checked against KiCad 9 boards only: this pack was written where only KiCad
  9.0.9 was installed, and no board saved by KiCad 10 is committed as a fixture.
  Until one is, a KiCad 10 board reads Skipped on the tier-0 gates and the power
  rules ("this reader was checked through board format 20241229 (KiCad 9.0)"),
  never Checked and never Failing. The tier-1 gates are KiCad's own answer and
  run on it; `pcb.fab_rules` reads its stackup and vias with this reader, by the
  words KiCad 10's source writes (`blind`, `buried`, `micro`).
- **The land beyond its centres.** `pcb.land_pattern` judges pad centres in the
  top view, one rigid fit; pad sizes, paste, courtyard and pads the drawing omits
  (mounting pads) are not judged, so a pad of the wrong size, or turned 90 degrees
  on the right centre, passes. The drawing is a person's transcription. A part
  that repeats so many functions that both the full search and the anchored one
  pass 5040 pairings (a ground row of eight, a ball grid) errors: split a function
  the part repeats.
- **A series pair on a person's word.** `pcb_pin_names_series` clears a two-net
  pin name on the ref it names, as the person states it, with its source. The
  gate holds the pair to the part (the only two pins of that name), never to the
  datasheet: a statement written to clear a real short clears it.
- **The cell beyond its records.** The power rules are as true as the records a
  person enters from the datasheets. `pcb.fuse_conductor` judges the adiabatic
  short-circuit points only, no overload (I2 <= 1.45 Iz), one conductor carrying
  the whole fault; its verdict turns on `limit_c`. `pcb.discharge_cutoff` reads
  the cell's board alone (a cutoff on another board reads as absent), and never
  the FET path a cutoff drives or a cutoff in firmware. `pcb.charge_protection`
  accepts only a THERM pin wired to an NTC with a safety timer: a charger
  protected another way (a supervisor that floats PROG) reads FAIL.
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
| `design-rules`, `clearance` | `pcb.drc` |
| `manufacturability` | `pcb.drc`, `pcb.fab_rules` |
| `fab-limits` | `pcb.fab_rules` |
| `circuit-simulation` | `pcb.spice` |
| `enclosure-fit`, `packaging`, `interference` | `pcb.enclosure_fit` |
| `connectivity` | `pcb.unconnected`, `pcb.parity` |
| `copper-connectivity` | `pcb.unconnected` |
| `netlist` | `pcb.unconnected`, `pcb.pin_names`, `pcb.erc`, `pcb.parity` |
| `land-pattern` | `pcb.land_pattern` |
| `battery-safety` | `pcb.charge_protection`, `pcb.fuse_conductor`, `pcb.discharge_cutoff` |
| `charge-protection` | `pcb.charge_protection` |
| `overcurrent-protection` | `pcb.fuse_conductor` |
| `deep-discharge` | `pcb.discharge_cutoff` |
| `schematic`, `electrical-rules` | `pcb.erc` |
| `schematic-parity` | `pcb.parity` |
| `pin-map`, `short-circuit` | `pcb.pin_names` |
| `trace-width`, `current-capacity` | `pcb.net_width` |
| `power-integrity` | `pcb.net_width`, `pcb.spice` |
| `pcb-model-validity` | `pcb.board_readable` |

A claim "every connection is made" tagged `connectivity` stays Checked through a
silkscreen warning; tagged `pcb`, it would read Failing on it. On a board with no
schematic, tag it `copper-connectivity`: `connectivity` brings in `pcb.parity`,
which skips there, so the claim would read Skipped beside a pass.

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

**The power rules judge the boards their records name**: counts of missing
protections against 0, seconds (IEC 60949's adiabatic time against the fuse's
guaranteed trip time), volts (the highest cutoff against the cell's minimum).

**`pcb.enclosure_fit` judges the populated board in the enclosure's frame.** The
board is first as `kicad-cli pcb export step --user-origin 0x0mm` writes it: the
board file's (0, 0) at the origin, x right, **y up (KiCad's y negated)**, z up from
the board's bottom face. `pcb_board_placement` turns it `rot_z_deg` about z, then
moves it by `x`, `y`, `z` mm. The enclosure's STEP is its material (walls and lid),
in mm, in its own frame. The verdict is the nearest part's distance, in mm,
against `pcb_enclosure_clearance_mm`; any shared volume fails it, and the board
body may touch (standoffs) but not cut in.

## Pointing a project at its board

```json
"pcb_board": "hardware/main.kicad_pcb",
"pcb_net_currents_a": {"VBUS": 0.5, "VBAT": 1.2, "GND": 1.2},
"pcb_temp_rise_limit_c": 10.0,
"pcb_copper_oz_outer": 1.0,
"pcb_fab": "jlcpcb", "pcb_fab_service": "standard",
"pcb_spice_deck": "hardware/sim/vbus.cir",
"pcb_spice_measure": "vbus_min", "pcb_spice_min": 4.75, "pcb_spice_units": "V",
"pcb_enclosure_step": "cad/out/enclosure.step",
"pcb_board_placement": {"x": 2.0, "y": 17.0, "z": 4.0, "rot_z_deg": 0},
"pcb_enclosure_clearance_mm": 1.0,
"pcb_land_drawings": {"U3": "hardware/lands/u3.json"},
"pcb_pin_names_series": {"FL1": {"pairs": [["1", "8"], ["2", "7"]],
                                 "source": "the filter array's datasheet, channel table"}},
"pcb_cell": {"board": "hardware/main.kicad_pcb", "ref": "BT1", "value": "CELL", "pin": "1",
             "min_v": 3.0, "pcm_cutoff_min_v": 2.5, "source": "the cell's datasheet"},
"pcb_cutoffs": []
```

`selftest/baseline.json`'s `_notes` give every key's fields; `pcb_charger`,
`pcb_fuse` and `pcb_conductor` are records of the same kind.

Each family skips without its keys: no fab named, no deck, no enclosure, no
placement or no clearance is a skip that says which key, never a default.

The board's `.kicad_pro` (rules, net classes, ERC and DRC exclusions),
`.kicad_dru` (custom rules) and `.kicad_sch` (the root schematic, with every sheet
under it), beside it with the same name, go to `kicad-cli` with it, and so do the
project's `sym-lib-table` and `fp-lib-table`. Keep those two in the project: on a
machine whose KiCad was never configured, ERC otherwise warns about every symbol
("library not in the current configuration"). `pcb_schematic` names another root
schematic for `pcb.erc`; `pcb.parity` always compares with the board's own, as
KiCad does.
**To waive a DRC or ERC finding, exclude it in KiCad** and commit the `.kicad_pro`:
the exclusion is then a named, reviewable record. Never narrow `pcb_drc_severity`
or `pcb_erc_severity` to make a warning go away; that hides every warning,
including the next one.

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
- `references/enclosure-fit.md`: the frames, what kicad-cli leaves out, and what
  the fit measures.
- `references/spice.md`: what `pcb.spice` runs, refuses and reads.
- `references/power.md`: the three LiPo rules, IEC 60949, the records and where
  their numbers come from.
- `references/what-slipped-through.md`: the defects the source pipeline's checks
  passed, and which gate here answers each.
- `references/installing.md`: getting `kicad-cli` (KiCad 9), ngspice, CadQuery,
  and the proposer's Java and Freerouting.
- `lenses.md`: how to attack a board before anyone orders it.
