# The battery rules: what `gates/power.py` reads and computes

These four rules are this pack's own code: pad lookups on the boards, and
published formulas, on datasheet facts a person enters as records. Every number
that decides a verdict is in a record with its `source`, or is a formula's. The
worked numbers below are the baseline's own parts (`selftest/baseline.json`,
`selftest/assets/power_board.kicad_pcb`).

## The records

Each is a param, an object keyed by id (not a list: other records point at the
ids, and the mutation walk reaches an object's leaves and treats a list as one
leaf). Each record names the board it is for (`board`, from the project root;
`pcb_board` when absent), and the ref, value and pins it describes.

| Key | Each record holds | Read by |
|---|---|---|
| `pcb_batteries` | `terminals` {ref, value, pos, neg, therm, taps}, `chemistry`, `series`, `parallel`, `cell` {upper_charge_v, open_circuit_v, discharge_v_min, charge_a_max, charge_temp_c, short_a or internal_resistance_ohm}, `ntc` (a pack NTC) | all four, and `pcb.mismate` (its terminals) |
| `pcb_protectors` | `battery`, `kind`, `where` (board, in-pack, firmware), ref and value on a board, `functions` {charge_v, charge_a, charge_temp, overcharge_v, overdischarge_v, trip, reverse_block, safety_timer} | all four; `pcb.mismate` reads trip and reverse_block |
| `pcb_conductors` | `battery`, `kind` (wire, ffc, contact, track), ref, value and pins, or a track's net; `area_mm2`, `start_c`, `limit_c`, `continuous_a` or a track's `continuous_rise_c`; `protected_by` | `pcb.fault_current` |
| `pcb_matings` | `a` {board, ref, value}; `b` another connector, `{battery: id}` or `{external: text}`; `map` (straight, reversed, or {a_pin: b_pin}) for two connectors; `modes` (references/mismate.md) | all four, `pcb.mismate` and `pcb.usbc_receptacle` |

`selftest/baseline.json`'s `_notes` list every field, and "Every field" below
marks which each record needs.

### Every field

**Required** keys must be there; an **optional** one may be left out. A missing
required key is refused with its name quoted (`pcb_batteries.cell has no key
'cell'`), so an id and a key of one name read apart.

**A cell value that is not stated is never an error.** A rule that needs a key of
`cell` that the record leaves out first judges all it can without it. Anything
it finds fails, as beside a full record: a defeat, no overcharge stop
independent of the charger, a cell that nothing cuts off, a protector out of
its place, a fuse that trips past 1.45 times the conductor's rating, a ratio
past 1.0 on a value that is there. If nothing fails, the rule skips and names
each key it lacks: `not stated, so not judged: pcb_batteries.cell.cell has no
key 'upper_charge_v': ...`. A value that is there and wrong (one number for a
range, a word for a number, `FILL:` from the draft, a typo past the plausible
band) is still an error. Each rule's section below says what it judges with no
cell value. (What slipped through, holdout round 2: two rules read the cell's
values first, so a cell stated `{}` made both error, and the fails they could
find without a datasheet were never reached.)

- `pcb_batteries.<id>`: required `terminals`, `chemistry`, `series`, `parallel`,
  `cell`, `source`; optional `board`, `ntc`.
  - `terminals`: required `ref`, `value`, `pos`, `neg`; optional `therm`, and
    `taps` (required for two or more cells in series: the junction pads from the
    lowest up).
  - `cell`: every key optional, so a cell nobody has chosen is stated `"cell":
    {}`: `pcb.mismate` still reads its terminals, and each other rule judges
    what it can and skips, naming each key it lacks. `upper_charge_v` (a
    rechargeable cell's upper charge voltage, with its tolerance),
    `open_circuit_v` (a primary cell's fresh open-circuit voltage: the most it
    drives a fault with; beside `upper_charge_v`, the higher counts),
    `discharge_v_min`, `charge_a_max`, `charge_temp_c` [lo, hi], `short_a`
    [least, most] or `internal_resistance_ohm` [least, most].
  - `ntc`: required `r25_ohm`, `r25_tol_pct`; optional `beta_k`, `beta_pair_c`,
    `beta_tol_pct`, `rt_table_c_ohm`.
- `pcb_protectors.<id>`: required `battery`, `kind`, `where`, `functions`,
  `source`; `ref` and `value` on a board (and optional `board`).
  - `charge_temp`: required `sense` and `ntc`, optional `network`, `acts`; in
    the pack, `window_c` {`cold_c` [min, max], `hot_c` [min, max]} alone. A
    charger may name its `sense` {`pin`} alone, with no `ntc`: the rule then
    judges only whether its input is switched off (`pcb.charge_temperature`
    below).
    - `sense`: required `pin` and `kind`. A `current` source takes `i_a`,
      `cold_v` and `hot_v`, each [min, max]; a `ratio` input (a divider) takes
      `pullup_ref`, `pullup_tol_pct`, `cold_frac` and `hot_frac`, the two
      fractions each [min, max]. Each kind refuses the other's keys.
    - `ntc`: the board's thermistor, required `ref`, `value`, `r25_ohm`,
      `r25_tol_pct`, optional `beta_k`, `beta_pair_c`, `beta_tol_pct`,
      `rt_table_c_ohm`; or `{"battery_therm": true}`, the pack's own (its
      battery's `ntc` record, on its `therm` pin).
    - `network`: optional `series_ref`, `parallel_ref` (not across the pack's
      own NTC), `tol_pct` (required with either), and `others` [{`ref`,
      `value`, `why`}] (any other part on the sense net, declared).
    - `acts` (required when the element is not the charger): {`pin`, `to`
      {`ref`, `pin`}}: the element's output pin, and the pin it drives on the
      part that stops the charge.
  - `overcharge_v`, `overdischarge_v`: required `v_max` or `v_min`, `per_cell`;
    on a board `sense` ({pin, through: [refs]}, or {taps} for two or more cells)
    and `acts` {pin, switch {ref, value, control, path}}.
  - `trip`: required `kind` (fuse, ptc, fet, efuse, pcm), `terminal` (pos, neg),
    `trip_a`, `trip_max_s` [[A, s]]; `path_pins` [p, q] (fuse, ptc, efuse) or
    `switch` {ref, value, path} (fet); `interrupt_a` (required for a fuse or a
    PTC); optional `hold_a`, `ahead` [{ref, value, why}].
  - `reverse_block` (read by `pcb.mismate` only): required `terminal` (pos,
    neg); `path_pins` [p, q] (the element's own, a diode's anode and cathode) or
    `switch` {ref, value, path} (an ideal-diode FET), one of the two; optional
    `through` [{ref, value, path: [p, q], why}] (the series parts from the
    terminal to the block, in order: a power switch, a fuse), and `ahead` [{ref,
    value, why}] (a part on a net before the block, declared).
  - `charge_v` (required of a charger): required `per_cell` (true when the
    voltage is each cell's, false when it is the whole stack's); then one of
    `v_reg_v` [min, max] (the datasheet's regulation voltage), `divider`
    {`vfb_v` [min, max], `top_ref`, `bottom_ref`, `tol_pct`} (the feedback
    divider on the board, read off its two resistors: a charger on the board
    only), or `set_by`: `"firmware"` (named, never judged; refused beside a
    value).
  - `charge_a` (required of a charger): one of `a_max` (the most charge
    current, A), `prog` {`ref`, `k_v` [min, max], `r_tol_pct`} (the PROG
    resistor on the board: the current is `k_v` over its resistance; a charger
    on the board only), or `set_by`: `"firmware"` (as above).
  - `safety_timer`: a flag, kept as evidence. No rule reads it, and it is not a
    function on its own: a record that states only it is refused.
- `pcb_conductors.<id>`: required `battery`, `kind`, `start_c`, `limit_c`,
  `protected_by`, `source`; a track's `net` and `continuous_rise_c`, any other
  kind's `ref`, `value`, `pins`, `area_mm2`, `continuous_a`; optional `board`.
  `protected_by` is a list of `pcb_protectors` ids that state a `trip`, or `[]`:
  nothing protects it, a statement the rule judges.
- `pcb_matings.<id>`: required `a`, `b`, `source`; `map` for two connectors;
  `modes` (required by `pcb.mismate`).

**One protector record holds every function of one element.** The baseline's
protector IC, U2, has an overcharge and an overdischarge function in one record;
its pack PCM has both in another. A rule reads the function it judges.

**Chemistry picks rules, never numbers.** li-ion, li-polymer, lifepo4 and lto
take every rule; nimh takes the charge temperature and the fault current;
li-primary and alkaline take the fault current, and a charger that names one
fails. Every limit is the cell's own datasheet value. One wide bound refuses a
typo in the cell's record: a per-cell charge voltage outside 1.5-4.6 V, a charge
current outside 0.001-50 A. On a charger it refuses only a typed value below it
(`v_reg_v`'s max, `a_max`): low, a typo would read as a pass. A charger above it
is a real overcharge (a 2S charger on a 1S cell, a PROG of "10" typed for
"10k"), and a value read off the board is the design: each fails at its ratio.

**Bound and strict.** A ref the board lacks, another value on that ref, or a
named pin with no pad is an error: "the record is for another design". The board
file is keyed, so a regenerated part stales the verdict, and a moved value
refuses the record. An unknown key is refused, naming the nearest known one. A
field that takes a datasheet [min, max] refuses one number: the comparator's
`i_a`, `cold_v`, `hot_v`, `cold_frac` and `hot_frac`, `v_reg_v`, `vfb_v`, `k_v`,
an in-pack `window_c`, and the cell's `charge_temp_c`, `short_a` and
`internal_resistance_ohm`. For a value the datasheet gives only as typical,
state [typ-x, typ+x] and say in `source` that x is your assumption. A field that
takes one number takes the datasheet's guaranteed bound on the side that
protects less, never its typical. The rule cannot tell the two apart, so this is
your word: `overcharge_v.v_max` is the most detection voltage,
`overdischarge_v.v_min` the least, `a_max` the most charge current, `trip_a` the
current by which the part is sure to trip, `trip_max_s` the longest time at each
current, and `interrupt_a` the current it is rated to break. A flag is a JSON
true or false (the string "false" reads as true). A `source` must say something.

Two more refusals come from the board. A board `pcb.board_readable` would fail
(older than KiCad 6, a track on an undefined net) is an error here: a record may
name a board the guard never reads. A board newer than the reader was checked
through (a KiCad 10 file) skips, in the guard's words. A part marked do not
populate or left out of the BOM (`dnp`, `exclude_from_bom`) is not on the built
board, and no rule counts it.

**Across boards.** `pcb_matings` joins each pin of `a` to its pin on `b`: their
two nets become one. So a protector on a daughterboard that senses the cell
through a flex counts, and a fuse on the far side of a board-to-board link
guards the conductor beyond it. A mating's connector is a conductor, never a
part that taps a net. Only a mating between two connectors joins nets: a
battery's pigtail meets the connector its record already names, and an external
side has no pads here, so its connector is a part like any other (what lies
beyond it may tap the net). Every rule reads a mating whole, `modes` included,
so a record one rule refuses, every rule refuses.

**Firmware is named, never counted.** An element `where: firmware` (a gauge's
register window, an MCU's ADC check) is not on the copper. A rule whose only
element is firmware skips and names it; beside an element the rule can see, the
seen element decides.

**A draft to start from.** The proposer `pcb.battery_record_draft` writes these
records' skeleton from the boards:
`nopekit propose pcb.battery_record_draft -- --board <board>`. It fills each
record's board, ref and value, and the pads that a pin name or the copper shows.
Every datasheet number, flag and choice is a string that starts `FILL:`, which
every rule here refuses where it reads a value. Every source is a list, which
every rule here refuses, since a source must be a string. So the draft passes
nothing until a person states each value and writes each source. PACK.md ("The
proposer: `pcb.battery_record_draft`") says what it finds, what it may draft
wrong and what it cannot see.

## `pcb.charge_temperature`

For each rechargeable cell something here charges: the temperatures at which
charging is still allowed, at the worst tolerance corners, against the cell's
`charge_temp_c`.

**What counts.** An element counts when it stops charging and senses the cell.
It stops charging when it is the charger (kind `charger`: its own pass element),
when its `acts` pin sits on the net of a pin that stops one (a BMS on a
charger's enable pin), or when it is in the pack. It senses the cell through a
board NTC (the sense pin's net holds a pad of the NTC the record names, at the
record's value; the NTC's far pad sits on a net another part reaches; no other
pad of the element is on the sense net) or through the pack's NTC (the sense
pin's net is the battery's `therm` pin, directly or through one series
resistor). A part on the sense network that the record does not name is
refused: a resistor across the NTC moves every trip. Name a filter capacitor in
`network.others`, with why it carries no current there.

**The defeat.** MCP73833/4 (DS22005B 5.1.4: "If temperature monitoring is not
required, place a standard 10 kOhm resistor from THERM to VSS") and bq2407x both
document a fixed resistor where the NTC goes as the way to switch the input off.
Its value reads as an NTC's to a BOM tool; only its symbol says what it is. So
where a schematic sits beside the board, an NTC whose symbol is one of KiCad's
fixed resistors (`Device:R`, `R_Small`, `R_US`, `R_Small_US`) fails with no
value. The test is negative on purpose: a project's own thermistor symbol need
not say "Thermistor". With no schematic, the detail says the symbol was not
read.

**A defeat needs no window and no NTC numbers.** A charger that nothing senses
the cell for fails without the cell's `charge_temp_c`: the window is read only
for the headroom, after the elements. With no defeat and no `charge_temp_c`,
the rule skips, naming it. A charger whose record names only its sense
pin (`charge_temp: {"sense": {"pin": "11"}}`, no `ntc`) is judged on its copper
and its schematic alone. It fails when its sense pin is tied to its own pad,
reaches no other part, or sees only fixed resistors (by their schematic symbol)
whose other pads each sit on a net one of the charger's own pins or one of the
cell's terminals is on: two 10 kOhm resistors from TS to REGN and to GND hold TS
near half the charger's reference, inside its normal-charge zone at any
temperature, so no window is enforced. A part on the sense net that may sense
the cell (a thermistor's symbol, no schematic to read, a resistor to some other
net, a part with more pads) is refused, asking for the full record. The NTC's
coupling to the cell is still out of reach (PACK.md).

**The trip resistances.** From the comparator's datasheet [min, max]:

- a current source (`kind: current`): R = V / I, so a trip lies in [V_min /
  I_max, V_max / I_min];
- a divider (`kind: ratio`, the NTC low, the trips as fractions f of the
  reference): R = R_pu f / (1 - f), at the corners of f and of R_pu, which is
  read off the board. The divider is assumed fed from the reference its
  fractions are of.

**The network and the NTC.** R_net(T) = (R_ntc(T) || R_par) + R_ser, each
resistor read off the board and taken at both ends of `network.tol_pct`. The
NTC is its maker's R/T table when the record gives one (ln R against 1/T
between the two bracketing rows), else its B equation, R25 exp(B (1/T - 1/T25)).
R25 and B each go to both ends of their tolerance; with a table, the B
tolerance scales the table's own exponent. `beta_tol_pct` is required with a
table too, because a maker's curve spreads away from 25 C: an omitted one is an
error, never 0. For an exact table, state 0 and say why in `source`. A B
constant is fitted on two temperatures (B25/50, B25/85): away from them the
equation drifts, and B rises with temperature, so below its pair the equation
puts a cold trip warmer than the part trips. The detail says when an edge lies
outside the B pair; a table comes first for that reason. A trip outside a
table's rows is an error, not a guess.

**The edges.** At every corner the trip temperature is found by bisection on
-40..150 C (a table's own range with a table), to 0.0001 K. The lowest
temperature any corner still charges at is the cold edge; the highest is the
hot edge. With several elements, the most protective sets each edge: charging
needs every element's leave. measured = min(T_cold - lo, hi - T_hot), in K: a
difference of temperatures, so a claim of headroom in K is compared with it.
The limit is 0. A cutoff that never trips inside the range fails with no value.

**On the baseline.** U1, an MCP73833-AMI/UN, biases THERM (pin 8) with 47-53
uA and compares it with VT1 1.20-1.26 V and VT2 0.235-0.265 V (DS22005B,
electrical characteristics). So it stops charging cold above 22.64-26.81 kOhm
and hot below 4.43-5.64 kOhm. TH1 is a fixture NTC, 10 kOhm +- 1 %, B25/85
4250 K +- 1 %. At the corners charging is allowed from 5.34 C to 43.47 C,
against the cell's 0..45 C: 1.53 K of headroom, on the hot side. The cold edge
lies below the B pair, and the detail says so. A coarser grade of the same NTC
does not move its nominal curve: R25 5 % and B 3 % leave 0.17 K, and R25 10 %
charges the cell to 45.97 C (-0.97 K). `ntc_swapped` puts a 100 kOhm NTC of
the same B in TH1's place, on the board, the schematic and its record: charging
is then allowed from 54.75 C to 109.83 C, -64.83 K.

The AMI option's safety timer is disabled (DS22005B, "Timer Period 0 hours").
It passes: IEC 62368-1 M.4.2 measures the charge voltage, current and cell
temperature, and asks for no timer. `safety_timer` is evidence only.

## `pcb.charge_limits`

For each lithium cell and each charger that charges it, the worse of two ratios,
against 1.0:

- **Voltage.** The charger's guaranteed maximum regulation voltage per cell,
  over the cell's `upper_charge_v` (IEC 62133-2 A.3.2's upper limit charging
  voltage: the maker's, with its tolerance). It is `v_reg_v`'s max, bound to
  the charger's board value, so a variant letter stales it; or a divider's Vfb
  (1 + Rtop / Rbot) at Vfb's max and the resistors' corners, read off the
  board. It is divided by the cells in series unless `per_cell` says the
  charger regulates each cell.
- **Current.** `a_max`, or k / R for a PROG, ISET or sense resistor read off the
  board, at k's max and R's least. The MCP73833 is IREG = 1000 V / RPROG,
  900-1100 mA at 1.0 kOhm (DS22005B), so k is 900-1100 V. The ratio is over
  `charge_a_max` times the cells in parallel.

**A single fault.** IEC 62368-1 M.4.2.2 judges the charge under single fault
conditions. So at least one `overcharge_v` element must be independent of every
charger (another part, or in the pack), sense the cell (as
`pcb.discharge_cutoff` reads sensing) and open a switch in its path. For two or
more cells in series, only an element that senses every cell counts: a
pack-level stop bounds no single cell. With none, the rule fails with no value.
With only a firmware stop there, the rule names it and skips. The element's
threshold is printed, not judged: no general source gives a cell's
single-fault voltage limit, and a protector's threshold sits above the charge
voltage by design. The charger's own overcharge stop is named and never
counted, and it moves no verdict: it is not independent of the charger.

A charge voltage or current set by firmware is named and not judged. The
charger's other function still is, so the rule skips only when nothing it can
see fails: a firmware current beside a 4.232 V charger on a 3.65 V cell fails.
NiMH has no constant-voltage phase and is not judged.

**With no cell value.** A charger on a primary cell and the single fault need
none. Each ratio reads only its own value: the voltage `upper_charge_v`, the
current `charge_a_max`; a function set by firmware reads none. So a cell stated
`{}` beside a charger whose functions firmware sets, with nothing independent of
the charger to stop an overcharge, fails on the single fault. A ratio past 1.0
fails beside a value that is not stated (a 3.65 V cell with no current, on a
4.232 V charger). Otherwise the rule skips, naming each key a ratio needs.

**On the baseline.** U1's 4.20 V option regulates at 4.168-4.232 V; the cell, an
LG INR18650 MJ1, takes 4.2 +- 0.05 V (its specification, 2.4): 4.232 / 4.25 =
0.9958. R3 is 3k at 1 %: 1100 V / 2970 ohm = 0.3704 A of the cell's 3.35 A
(2.5: 1.0 C), 0.1106. The pack's PCM (4.30 V) and U2 (4.35 V, through its CO on
Q1) are both independent of U1. `lfp_cell` puts a LiFePO4 cell of the same size
on the same charger: LiFePO4 charges to 3.65 V (Battery University BU-205), and
4.232 / 3.65 = 1.1595.

## `pcb.fault_current`

For each conductor and the protectors its record names, the least of three
ratios, against 1.0:

- **(b) Interrupt.** A fuse's or PTC's `interrupt_a` over the most current the
  cell can drive into a fault. A fuse that breaks less than that can rupture.
- **(c) The overload band.** IEC 60364-4-43 433.1: the current I2 that makes
  the protector operate is at most 1.45 times the conductor's continuous rating
  Iz. Written for building installations; applied here by stated analogy, as the
  one published bound on the band between a conductor's rating and its
  protector's trip. Iz is `continuous_a`, or for a track IPC-2221 at its
  `continuous_rise_c`, on its narrowest stretch. I2 is `trip_a`.
- **(d) The short circuit.** IEC 60949's adiabatic limit for copper:

      t = (K S / I)^2 ln((limit_c + 234.5) / (start_c + 234.5)),  K = 226 A s^0.5 / mm^2

  At each guaranteed trip point [A, t_max] that the cell can reach (its least
  fault current at or above A), t over t_max. A t_max past 5 s is refused:
  there heat leaves the conductor. With no point the cell can reach, the rule
  fails with no value: nothing guarantees a trip at the current this cell can
  drive. The formula gives IEC 60364-5-54's k factors (115 for 70 -> 160 C, 143
  for 90 -> 250 C).

A conductor takes its best protector for (c) and for (d), and its worst fuse
for (b): a PTC can cover the overload band while protector FETs cover the short
circuit. One conductor carries the whole fault: contacts in parallel get no
credit, because a fault does not split evenly.

**Where the protector sits.** A fuse, PTC or eFuse has `path_pins` one on the
battery terminal's net (`terminal: pos`, or `neg` when low-side) and one on the
protected net; protector FETs name their `switch` the same way; an in-pack PCM
is counted on its datasheet's word. On the positive side, the conductor must be
on the net the protector feeds. A part that taps the terminal net ahead of the
element is refused until the record declares it in `ahead`, with why: a sound
design may put a protector's own supply there, so it is never a fail.

**The fault current** is `short_a` [least, most] times the cells in parallel,
or `discharge_v_min` and the cell's most voltage over `internal_resistance_ohm`
[most, least]. The most voltage is a rechargeable cell's `upper_charge_v` (or a
higher `open_circuit_v` stated beside it), and a primary cell's `open_circuit_v`,
its fresh open-circuit voltage: a primary cell has no charge voltage, and one
that states only `upper_charge_v` is refused, naming `open_circuit_v` and
`short_a`. The least decides which trip points count; the most decides the
interrupt rating.

**With no cell value.** Each protector's place on the copper, and the overload
band (c), need none, and are judged first: a fuse beside the path fails, and so
does one whose best trip is past 1.45 Iz, "whatever the cell drives". The
interrupt (b), the short circuit (d) and a conductor nothing protects need the
fault range or the most power (`short_a`, or `internal_resistance_ohm` with
`discharge_v_min` and the most voltage). A conductor whose cell does not state
them is named, and the rule skips, naming the keys, unless another conductor
fails. A primary cell that states neither `open_circuit_v` nor `short_a` skips
the same way; one that states `upper_charge_v` in their place errors, as above.

**A conductor nothing protects** (`protected_by: []`) is judged, never refused:
the better of two ratios, against 1.0. IEC 62368-1 (2018) 6.2.2.4's PS1, 15 W,
over the most power the source can deliver into any load (its open-circuit
voltage, every cell in series counted, times its short-circuit current, every
cell in parallel counted, over four: the matched load's power), and the
conductor's continuous rating over the most fault current (a conductor that
carries every fault for good needs no protector). Below 1.0 the detail says
nothing limits the fault. Two alkaline AAA cells, 1.65 V fresh and 0.15 ohm at
the least each, deliver at most 9.1 W: PS1, a pass. The baseline's 18650 pack
behind no protector delivers 42.5 W (4.25 V, 40 A) into a 1 A wire: a fail. A
part named in `protected_by` that states no trip (a blocking diode) is refused,
naming `[]`: it trips at nothing.

**On the baseline.** F1, a fixture PTC, holds 0.5 A, trips by 1.0 A, trips
within 0.1 s at 8 A and breaks 80 A. It sits between BT1 pin 1 and VSYS, and J6
pin 1 carries VSYS out on a 26 AWG wire: 0.129 mm^2, PVC, 70 -> 160 C, rated
1.0 A (a fixture rating). The pack drives 10-40 A into a short (a fixture
estimate). Interrupt 80 / 40 = 2.0; overload 1.45 x 1.0 / 1.0 = 1.45; short
circuit 3.439 s / 0.1 s = 34.39. The least is 1.45. `fuse_trips_late` swaps F1
for a PTC of twice the hold and trip current: 1.45 / 2.0 = 0.725, and nothing
else moves.

Worked, on illustration values (no part's datasheet): one conductor of 0.03
mm^2 from 25 C, against a fuse guaranteed to trip in 0.1 s at 10 A. To an 85 C
limit it takes 95.6 ms, a fail by 4 %; to 125 C, 149.8 ms, a pass. The verdict
turns on `limit_c`, so its source matters most.

## `pcb.discharge_cutoff`

For each lithium cell, the highest per-cell threshold among the elements that
cut it off, against the cell's `discharge_v_min`, on the worst cell. An
`overdischarge_v` element counts when it is in the pack, or when it is fitted,
senses the cell and opens a switch in its path:

- **It senses the cell.** For one cell, its sense pin is on the cell's positive
  net, directly or through the chain `through` names, in order: each part a
  fitted two-pad part with one pad on the net before it. A fuse and then the
  datasheet's RC resistor is such a chain. A sense pin on the cell's return
  never counts, and the battery's own connector is never in a chain. The chain
  is named, never inferred: inferred, the cell's own connector made ground "one
  part away". For two or more cells in series, only a `per_cell` element whose
  `taps` sit on every node, return to top, in order, counts.
- **It opens a switch in the path.** Its `acts.pin` is on the net of the
  switch's control pin, and the switch's two path pins sit one on a battery
  terminal's net and one on a net another part reaches. A protector that drives
  nothing protects nothing.

Each counts at `v_min`, its datasheet's least detection voltage. With none, the
rule fails with no value. The parts left on the cell side of the switch, which
keep drawing after the cutoff, are listed.

**With no cell value.** Whether anything cuts the cell off needs none: "nothing
cuts the cell off" fails without `discharge_v_min`. A cutoff that counts is held
to it, so a cell with a counted cutoff and no `discharge_v_min` skips, naming
it, unless another cell fails.

**On the baseline.** U2, a fixture one-cell protector on KiCad's AP9101CK pins,
senses the cell through F1 and R5 (330 ohm), and its DO drives Q1's G1 (an
FDS6890A, its two drains joined), whose S1 sits on the cell's negative and S2 on
ground. It counts at 2.85 V, against the MJ1's 2.5 V end voltage (its
specification, 2.6): a pass. C1 stays on the cell side. `cutoff_drives_nothing`
moves U2's DO to a net of its own: U2 still senses the cell and opens nothing,
so only the pack's PCM counts, at 2.30 V, below 2.5 V.

## The baseline

`selftest/assets/power_board.kicad_pcb`, made by `make_power_board.py` with
pcbnew 9.0.9 from KiCad's stock footprints, placed and netted, not routed: J1 a
USB-C receptacle with 5.1k on each CC pin; U1 the charger, R3 on PROG, TH1 on
THERM, C2 and C3 its bypass capacitors; BT1 the cell's keyed two-pin housing; F1
the PTC; U2 the protector, R5 and C1 its RC, R4 on VM, Q1 its FETs; J6 the wire
out; J10 and J11 two 8-position FFC housings, joined by the `flex` mating (GND
on 1, VBUS on 5, and 3 and 6 open, so a 6-way cable two positions off never puts
VBUS on GND: `references/mismate.md`). Its schematic,
`power_board.kicad_sch` (`make_power_schematic.py`), places every part on the
symbol the board took its pin names from, TH1 on `Device:Thermistor_NTC`, so the
known-good reads the defeat check's symbol. A known-bad copy of the board that
swaps a part, or changes only a record, carries the schematic beside it, the
part swapped there too; one that moves a net carries none. The
cell's numbers come from LG Chem's MJ1 specification (LRB-PS-CY3500_MJ1, rev 1,
2016-06-30), U1's from Microchip's DS22005B (2009); the protector, the PCM, the
PTC, the NTC, the wire's rating and the short-circuit current are fixture
values, and their records say so.

## Where the numbers come from

- MCP73833/4: https://ww1.microchip.com/downloads/en/DeviceDoc/22005b.pdf
  (DS22005B, read 2026-11-24).
- LG INR18650 MJ1: product specification LRB-PS-CY3500_MJ1, rev 1, 2016-06-30
  (read 2026-11-24).
- LiFePO4's 3.65 V: https://batteryuniversity.com/article/bu-205-types-of-lithium-ion
  (read 2026-11-24).
- IEC 60949 and IEC 60364-5-54's k factors: held by `tests/test_pcb_kicad.py`.
- IEC 60364-4-43 (2008) 433.1, I2 <= 1.45 Iz; IEC 62133-2 A.3.2, the upper limit
  charging voltage; IEC 62368-1 M.4.2.2, charging measured under single fault
  conditions with no timer: as the design review read them (2026-11-23).
