# What slipped through, and which gate answers it

This pack was extracted from an autonomous board pipeline: an agent generated two
4-layer boards from a parametric model, placed real footprints, built the netlist
in a script, autorouted with Freerouting, stitched ground, ran KiCad's DRC and
exported a fab package, all headless. Its history and its session logs record what
the checks caught and what they let through. The boards were never built, so none
of this was found by a failing board.

## What the checks caught

KiCad's DRC and its unconnected count caught a real defect about a dozen times:
shorts where a new part's pins landed in another part's pad field, courtyard
overlaps after a part was moved, a crossing of the USB data pair inside the
pipeline's own "fixed" copper, power nets left open after a connector widened, and
the agent's own clearance shortcut (288 violations, reverted). These are what
`pcb.drc` and `pcb.unconnected` keep doing.

## What the checks passed

| Defect | Found by | Gate here |
|---|---|---|
| An ESD array wired with D+ on pin 1 and D- on pin 6, which the part joins inside: the data pair shorted, on legal copper | a person reading the datasheet | `pcb.pin_names`, on the board's pin names or on the schematic `pcb.schematic_from_board` writes for a board with none |
| A 0.4-0.5 mm power width set in a script constant that never reached the board; the battery path shipped at 0.2 mm (about 336 mOhm, a third of the charge loop) under "0 violations, 0 unconnected" | an agent review lens, weeks later | `pcb.net_width` |
| A pin-1 warning label 21.4 mm long on an 8 mm strip, which the fab would clip; every check ran at error severity, where a warning cannot fail | a "fresh eyes" audit | `pcb.drc` at every severity |
| A route checker that printed failures and always exited 0, and sampled only the cable's centre line | the same audit | the spine: a gate's verdict is a value, and every gate is shown failing its own known-bad input |
| The fab export ignored kicad-cli's exit code and read a report another script also wrote, so a failed run could pass on a stale report | review of the pipeline | `pcb.drc` reads the exit code and the report this run wrote, and errors when they disagree |
| A custom rule meant for one connector's shield pads that relaxed edge clearance for every ground pad | review of the rule file | not a gate: `references/kicad-cli.md`, "Waivers", and `lenses.md` |
| A misspelled property in a custom rule (`A.Pin_Nmae`), or one syntax error in the rules file, which KiCad takes without a word: 0 violations, exit 0, and the rule never ran | measured, in this pack's 0.2.0 review | `pcb.drc` probes every rule and every property it reads |
| `.options tmep=127` (ngspice ran at 27 C, exit 0), and a `.meas` window past the end of the run, which reads 0 V with exit 0 | measured, in this pack's 0.2.0 review | `pcb.spice` refuses both before the run |
| A power switch's land drawn from another drawing: its middle pad 1.5 mm from where the vendor's footprint puts it (a recalled reading, to re-read), every design rule clean | the 2026-10 gap analysis of a later keyboard | `pcb.land_pattern`, pads paired by function |
| A charger with no thermistor input and no safety timer, a PPTC fuse guaranteed to trip in 0.2 s only at a current where its ribbon conductor reaches its rating in 129 ms, and nothing to cut the cell off at its minimum | the same analysis | `pcb.charge_protection`, `pcb.fuse_conductor`, `pcb.discharge_cutoff` |
| A narrower ribbon fits the wider connector with 4 mm of slop and can put the cell on ground | an agent's safety lens | not a gate: an expert-judgment claim (PACK.md, "cannot settle") |
| A joystick routed to 0/0 on both boards whose body stood 6 mm proud of the face | the person, on sight | `pcb.enclosure_fit` (needs the part's 3D model; one with none skips) |

## What the autorouter showed

Freerouting is a **proposer** here, never an evaluator. In the logs it gave the same
result for the same input within one run, and different results across runs for
reasons never isolated; two contradictory explanations of that reached the source
repository, and the agent retracted both. That does not matter for a proposer: a
proposal settles nothing, and DRC judges what it drew. An earlier post-routing
"gap closer" that drew its own connections did matter: it drew shorts, and was
removed. Nothing that writes copper is allowed to judge it.

## Physical evidence

None. No board from that pipeline was ordered, assembled or powered. Every claim
about the circuit working stays Pending build until a person records a result.
