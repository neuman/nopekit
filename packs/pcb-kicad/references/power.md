# The three LiPo rules: what `gates/power.py` reads and computes

These three are this pack's own code: pad-net lookups on the board, and one
published formula, on datasheet facts a person enters as records. Every number
that decides a verdict is in a record with its `source`, or is IEC 60949's.

## The records

Each is a param, and each names the board it is for (`board`, from the project
root; `pcb_board` when absent), and the ref and value it describes:

| Key | Fields | Read by |
|---|---|---|
| `pcb_cell` | `ref`, `value`, `pin` (the positive pin), `min_v`, `pcm_cutoff_min_v` (null: no PCM) | `pcb.fuse_conductor`, `pcb.discharge_cutoff` |
| `pcb_charger` | `ref`, `value`, `thermistor_pin` (null: none), `ntc_ref`, `safety_timer` (JSON true or false) | `pcb.charge_protection` |
| `pcb_fuse` | `ref`, `value`, `trip_max_s` (`[[A, s], ...]`, the guaranteed maximum time to trip) | `pcb.fuse_conductor` |
| `pcb_conductor` | `ref`, `value`, `pins`, `area_mm2` (one conductor), `start_c`, `limit_c` | `pcb.fuse_conductor` |
| `pcb_cutoffs` | `[{ref, value, sense_pin, series_ref (null: the sense pin is on the cell's net), threshold_min_v}]`, empty for none | `pcb.discharge_cutoff` |

A ref the board lacks, another value on that ref, or a named pin with no pad is an
error: "the record is for another design". The board file is keyed, so a
regenerated part stales the verdict, and a moved value refuses the record. A
record's own numbers are held to their kind: an area, a current or a trip time
above 0, `safety_timer` a JSON true or false (the string `"false"` would read as
true), and a `source` that says something, each cutoff's too.

Two more refusals come from the board. A board `pcb.board_readable` would fail
(newer than KiCad 9, older than KiCad 6, a track on an undefined net) is an error
here too: a record may name a board the guard never reads. And a part marked do
not populate or left out of the BOM (`dnp`, `exclude_from_bom`) is not on the built
board: no rule counts it, and a charger, an NTC, a fuse, a cutoff or a series part
marked so reads as missing.

## `pcb.charge_protection`

Counts what is missing, against 0: (a) a THERM pin whose net holds a pad of the
NTC, the NTC's other pad on a net some other part reaches (a pad on no net, or on
a net of its own, senses nothing), and no other pad of the charger itself on the
THERM net (a tie to its own VSS or VDD switches the input off, and a THERM on
ground would otherwise read as wired to the NTC's ground side); (b) a safety
timer. A small linear charger whose whole pinout is status, ground, battery,
supply and the current-setting pin has neither. The MCP73833 (DS22005) has both,
THERM on pin 8 of the MSOP-10.

## `pcb.fuse_conductor`

IEC 60949's adiabatic limit for copper:

    t = (K S / I)^2 ln((limit_c + 234.5) / (start_c + 234.5)),  K = 226 A s^0.5 / mm^2

At each guaranteed trip point the conductor's `t` must be at least the fuse's
`t_max`; the verdict reads the point with the least ratio. A `t_max` past 5 s is
refused: there, heat leaves the conductor and the formula no longer holds. The
formula gives IEC 60364-5-54's k factors (115 for 70 -> 160 C, 143 for 90 -> 250
C). The fuse's two pads must sit one on the cell pin's net and one on the
conductor's, or the gate fails: the fuse is not in that path.

Worked, on illustration values (no part's datasheet): one conductor of 0.03 mm^2
from 25 C, against a fuse guaranteed to trip in 0.1 s at 10 A. To an 85 C limit
it takes 95.6 ms, a fail by 4%; to 105 C, 123.5 ms, a pass. The verdict turns on
`limit_c`, so its source matters most. Two such conductors in parallel would pass
at an even split (382.5 ms to 85 C), and get no credit: a fault does not split
evenly.

## `pcb.discharge_cutoff`

The cutoffs are the cell's PCM (`pcm_cutoff_min_v`) and each part in `pcb_cutoffs`
whose sense pad is on the cell pin's net (`series_ref` null), or reaches it through
the part `series_ref` names (an AP9101C's VDD through its datasheet's 330 ohm
resistor): a two-pad part, not the cell's own connector, one pad on the cell's
net and the other on the sense pad's. A sense pad on the cell's return (a net
another pin of the cell sits on) never counts. The series part is named, never
inferred: inferred as any two-pad part on the cell's net, the cell's own
connector made ground "one part away", and a protector with its VDD on ground,
or a record that named a VSS pin, read as sensing the cell. The highest
`threshold_min_v` (the datasheet's lowest detection voltage) must be at or above
the cell's `min_v`. With none, the gate fails with no value: nothing cuts the cell
off. The FET path the cutoff drives, a cutoff in firmware and a cutoff on another
board are not seen.

## The baseline

`selftest/assets/lipo_charger.kicad_pcb`, made by `make_power_board.py` with
pcbnew 9.0.9 from KiCad's stock footprints, placed and netted, not routed: J1
VBUS in, U1 MCP73833 with THERM on TH1 (an NTC to ground) and R2 on PROG, J2 the
cell, F1 MF-MSMF075 to J3 (28 AWG, 0.0810 mm^2, PVC 70 -> 160 C: 1.356 s at 8 A),
U2 AP9101CK sensing the cell through R1 330 ohm, C1 on its VDD. The cell's
2.75 V minimum and the AP9101C option's 2.85 V lowest detection voltage are
fixture values; the MCP73833's timer and the AP9101C option table are recalled
from their datasheets, and say so in their records.
