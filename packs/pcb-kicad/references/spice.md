# SPICE: what `pcb.spice` runs

    ngspice -b <deck>.cir

on a staged copy of the deck, with every relative `.include` and `.lib` path made
absolute from the deck's directory. The verdict is one `.meas` result, named by
`pcb_spice_measure`, against `pcb_spice_min` and/or `pcb_spice_max`.

## What it refuses

Before ngspice runs, on the deck's text:

- **An option name ngspice does not know.** ngspice-42 takes `.options tmep=127`
  without a word and runs at 27 C, exit 0; and it reads every card that begins
  `.opt` as `.options` (`.optoins`, `.opts`). Every name on every such card, in
  the deck and each file it `.include`s, must be in `NGSPICE_OPTIONS`
  (`gates/pcb.py`: the simulator's own table, read off the ngspice-42 binary, and
  the front-end variables of the manual's `.options` chapter). A valid name left
  off reads as an error naming it, never a pass. Option values are not checked.
- **A `.meas tran` window outside the run.** The `.meas` that defines
  `pcb_spice_measure` must be in the deck itself, and its literal FROM, TO and AT
  must lie inside 0 .. tstop of the deck's literal `.tran`
  (0 <= FROM < TO <= tstop). MIN over 5-6 ms of a 4 ms run reads 0.000000e+00 with
  exit 0; `to=4` is 4 seconds. Numbers are read as ngspice reads them (`m` milli,
  `meg` mega, letters after the suffix dropped). A bound in `{braces}` cannot be
  shown and errors. `.meas ac` and `.meas dc` windows are not checked.

After it runs:

- **A line ngspice ignored.** ngspice warns (`Warning: 'r1 a 0' is not a valid
  resistor instance line, ignored!`) and solves the rest: a different circuit.
  Any `Warning: ... ignored` is an error here.
- **Any `Error:` line, or an exit other than 0.**
- **A measurement ngspice could not make** (`.meas tran x when v(out)=3 failed!`:
  the waveform never crossed). ngspice exits 0 and prints no value; the gate
  errors, because nothing was shown, rather than fail.

## What it reads

ngspice prints `name = value` for each `.meas`, the name lowercased:

    vbus_min            =  4.832500e+00 at=  1.095200e-03

The value is in SI (V, A, s); `pcb_spice_units` says which, because the gate
cannot tell. With both limits, the verdict reports the side the value crossed, or
the nearer side when it passed, so the margin a reader sees is the one that binds.

## The baseline deck

`selftest/assets/vbus_droop.cir`: the host's 5.0 V through 0.25 ohm of cable and
0.085 ohm of board copper to J2, a 10 uF bulk capacitor there, and a 0.5 A step.
VBUS at J2 dips to 4.8325 V against USB 2.0's 4.75 V. The known-bad control
raises the cable to 0.85 ohm (2 m of 28 AWG out and back): 4.5325 V.

## What it cannot see

Whether the deck is the circuit. The deck is the project's model, written by
someone; ngspice solves what it says. A claim that rests on `pcb.spice` rests on
the deck: read it against the schematic, and give the parts' values their
sources.
