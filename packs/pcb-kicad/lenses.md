# Lenses: attacking a board before anyone orders it

Each lens is a question a reviewer asks of THIS board, with the evidence it would
take to close it. A green DRC answers none of them.

## What a design rule check cannot see

- For each IC, open its datasheet's pinout table next to the board's pads. Which
  pins did someone type by number? A pin map typed by hand is a claim nobody
  checked; a symbol from KiCad's library is one KiCad's librarians checked.
- Where is the netlist from? A schematic run through ERC, or a dictionary in a
  script? A script's netlist has no electrical-rule check at all, and `pcb.erc` and
  `pcb.parity` skip on a board without a schematic.
- Was the board updated after the schematic's last edit? `pcb.parity` says where
  the two disagree; the fix is "Update PCB from Schematic", never editing the board
  to match by hand.
- Which nets carry power, and does each have a current in `pcb_net_currents_a`?
  A net that is not named is not judged.

## Pins the part joins inside

- Which parts repeat a pin name (ESD arrays, regulators with tab and pin, MCUs with
  several GND)? For each, do the repeated pins carry one net? If the part is not
  from the library, nothing here can tell.
- A flow-through protector protects only what flows through it. Does the data line
  enter on one side and leave on the other, or did a route bypass the part?

## A width nobody asked for

- Is the width you meant actually on the board? Grep the board file for the net's
  tracks. A width set in a script's constant, a net class that never reached the
  board, a rule the router was not given: each ships the default width silently.
- What does the battery or power loop's resistance add up to: tracks, connector,
  fuse, ribbon? A narrow track can be a third of a charge loop's resistance.

## Warnings the gate never read

- Was the DRC run at every severity? A check at error severity cannot see a silk
  label the fab will clip, a reference over a pad, or text too small to print.
- Is the report the one this run wrote? A gate that reads a report another script
  also writes can pass on yesterday's file.

## The waiver nobody named

- List every DRC exclusion in the `.kicad_pro` and every relaxed rule in the
  `.kicad_dru`. For each: what does it actually cover? A rule written for one
  connector's shield pads that matches every GND pad covers every GND pad.

## A connector that fits the wrong cable

- For each connector: what else fits it? A narrower ribbon in a wider housing, a
  reversed keyed plug forced in, a cable from the previous revision. Where does
  each wrong fit put power? Pin order is a safety decision. `pcb.mismate` judges
  the fits a mating's `modes` state, and nothing else: is every fit you found in
  a record?
- Is there a fuse between the cell and anything a wrong cable could reach?
- Which wrong fit puts a source on a signal pin? `pcb.mismate` lists each one in
  its evidence and does not judge it: read the part's absolute maximum on that
  pin.

## Parts not in the library

- For each custom footprint: who drew it, from which drawing, and was it measured
  against a real part? A pad pattern off by a mirror image is legal copper.
- Is the courtyard real? An optimistic courtyard hides a collision DRC would have
  found.

## A part taller than its cavity

- Does every placed part have a 3D model, and is it the part you will buy? A
  generic model of the wrong height fits on screen. `pcb.enclosure_fit` sees only
  the models, and a declared height only as the courtyard's box at that height:
  is the height the datasheet's seated maximum, and is the courtyard the part?
- What moves after assembly: a plug pushed in, a button pressed, a battery that
  swells, a lid that flexes? The fit is one placement of rigid solids.
- Which parts pass through an opening in the case: a knob, a button cap, a light
  pipe, a connector face? How far should each stand proud of the face, and how far
  does it? `pcb.enclosure_fit` measures shared volume and clearance. A part
  through an opening wide enough to keep the claim's clearance reads clear,
  however far it reaches. A tighter opening fails on clearance, and that fail
  still says nothing about how far the part reaches. A contact declared for the
  part against the opening excuses its closeness, so it reads clear then too.
- Is each declared contact still true? Each one names a part, one feature and a
  reason. When the part or the case moves, read the listed distance against the
  reason.

## A simulation of a different circuit

- Is the SPICE deck the schematic? Compare each element's nodes and value with the
  part it stands for. A deck that solves is not a deck that matches.
- Where did each value come from: a datasheet, a measurement, a guess? A cable's
  resistance and a regulator's output impedance are the usual guesses.

## A cell nobody charged hot or cold

- Is the NTC on the cell, or on the board? `pcb.charge_temperature` reads the
  network's numbers, never its place. A board NTC far from the cell senses the
  board: test the window's edges with the cell in a chamber, or state an expert
  judgment of the coupling.
- What ends a charge that never finishes? A charger with no timer and no
  termination on a cell that never reaches it charges forever. Read the
  charger's termination and the load it carries while it charges.
- Can another rail charge a primary cell? A lithium coin cell beside a supply,
  through a diode that leaks or a part that back-feeds it, is charged by
  accident (UL 1642 asks for two blocking elements). No rule here sees it.
- Is the protector's output driving anything? Follow DO and CO to a gate.
  `pcb.discharge_cutoff` follows the one the record names; a second protector
  nobody recorded is in no rule.

## Metal and a hand around the antenna

- What sits near the antenna once the board is in its case: a battery, a screw,
  a metal frame, a display's shield, a coin cell? `pcb.antenna_keepout` judges
  copper on the board, and nothing past it. Metal beside the antenna detunes it as
  surely as copper under it. Read the clearance the module's integration guide
  asks for, and measure it in the assembly.
- Where does the hand go, held or worn as the product is used? A palm over the
  antenna costs range that no board file shows.
- Settle both with a physical claim: a range test in the hand, the case closed,
  the final battery fitted, at the distance the requirement states, recorded with
  `nopekit claim physical`. A pass on the bench with the board bare says nothing
  about either.
- Is the antenna where the vendor asks: at the board edge, or past it, with the
  board cut away beside and under it? The gate judges what lies in the keep-out,
  not where the keep-out sits.
- Is there a part body over the antenna (a connector's housing, a shield can), or
  copper text or a logo on a copper layer in the keep-out? The gate allows a
  part with no copper in the area, and KiCad's keep-out does not report text or
  drawings on copper. Look at each layer.

## Before the money is spent

- Is `pcb_fab` the fab you will order from, and was its file read recently? A
  fab's capability page changes; the file says the day it was read.
- Has the fab's own DFM preview been read, rotations included? KiCad's rotation is
  not every fab's.
- Is any part's stock thin enough that the order can fail after the boards ship?
