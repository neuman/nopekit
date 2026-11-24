# Fab limits: what `pcb.fab_rules` runs

`pcb.fab_rules` holds a board to the minimums its fab publishes, through KiCad's
own DRC. The numbers live in a fab file, `gates/fab/<name>.json`; the gate turns the
row for the board into a `.kicad_dru` and runs `kicad-cli pcb drc --severity-all` on
a staged copy.

## The run

1. Pick the row: the board's copper layer count (its layer table's `*.Cu` entries)
   and `pcb_copper_oz_outer` (1 oz when absent, said as `[pack default]`). A row's
   `layers` is inclusive; its `oz` is `(lo, hi]`. No row is a skip.
2. Write the row, after the file's `common` rules, as the staged board's
   `.kicad_dru`, each rule named `fab_<name>`. The project's own `.kicad_dru` is
   left out of this run: a rule written to relax a constraint for one net would
   relax the fab too.
3. Copy the `.kicad_pro` with every board-setup minimum (`min_*`) set to 0 but
   the minimum clearance, set to the fab's own; and every constrained type the
   project set to `ignore` raised to `error`. KiCad lets a pad's, footprint's or
   zone's local clearance override every custom rule and holds it only at the
   board minimum, and it does not test an ignored type even with
   `--severity-all`: either would relax the fab as a custom rule would. Net
   classes, exclusions (each a person's waiver of one finding) and everything
   else stay.
4. Count the findings whose type is one the rules constrain, except silk on silk
   (below). The detail line groups the count by type, with the fab rule behind
   each. Everything else in the run (silk clipped by the mask, unconnected items)
   is `pcb.drc`'s, judged there under the project's own rules and severities: one
   of a type the project ignores, raised for this run only, is said apart,
   because `pcb.drc` does not see it, and where the project has custom rules of
   its own (which may waive a finding) the rest are said to be not the fab's.
   The staged project is written only if KiCad can read it: a NaN or an
   overflowing number would make KiCad drop the whole file, the clearance floor
   with it, so the gate errors instead.

**Silk on silk.** KiCad reports silk on silk as `silk_overlap` too, from the
board's own silk minimum, and a fab's silkscreen rule is silk to pad. So a
`silk_overlap` is left to `pcb.drc` only when it is shown to be silk on silk: no
item is a pad (matched by the pad's id, never by the item's description, which
kicad-cli translates), its words do not name the run's own `fab_silk_to_pad`
rule, and no pad on the board has an id KiCad replaces. A pad's id matches as
kicad-cli writes it: a bare legacy stamp of up to 8 hex digits comes back
zero-filled after `00000000-0000-0000-0000-0000`, and an uppercase, undashed or
braced UUID lowercase and dashed. A pad with no id, a padded one, a braced stamp
or any other text gets a random one; then every silk overlap is counted, and the
line says why.

What slipped through: on a real 4-layer board the fab run counted every
silk-on-silk overlap that `pcb.drc` also found as past JLCPCB's limits, beside the
findings that were the fab's, and the detail line read "Silkscreen overlap"
three times, so nobody could see that no copper was past a limit. The first fix
matched pads on the file's text alone, and in review a silk line drawn across a
pad with no id, an uppercase id or a legacy stamp passed. A project that set
`track_width` to `ignore` hid the fab's own track rule; a pad's `(clearance
0.01)` passed a 0.047 mm gap under JLCPCB's 0.1 mm; a microvia drilled 0.12 mm,
reported as `microvia_drill_out_of_range`, passed its 0.15 mm via hole; and a via
on top of another, reported as `holes_co_located`, passed where two 0.3 mm apart
failed.

Why zero the project's minimums: a finding's text does not reliably say which
rule raised it. KiCad 9.0.9 reported a 0.085 mm track as `board setup constraints
min width 0.1000 mm`, the fab rule's number under the board's name. With the
board's own floors at 0 (and the clearance floor at the fab's own number), a
finding of a constrained type can only be the fab's, except a `silk_overlap`
shown to be silk on silk, which KiCad still reports at a floor of 0, and a
clearance finding inside a local override above the fab's number: that override
still binds, so the gate fails copper the fab would build (a false fail, never a
false pass; GitHub #13).

| Constraint | Reported as |
|---|---|
| `track_width` | `track_width` |
| `clearance` | `clearance` |
| `edge_clearance` | `copper_edge_clearance` |
| `hole_size` | `drill_out_of_range`; on a microvia, `microvia_drill_out_of_range` |
| `via_diameter` | `via_diameter` |
| `annular_width` | `annular_width` |
| `hole_to_hole` | `hole_to_hole`; two holes on one centre, `holes_co_located` |
| `text_height`, `text_thickness` | `text_height`, `text_thickness` |
| `silk_clearance` | `silk_overlap` |

Each was read off a report from one deliberate breach (`gates/_kicad.py`,
`CONSTRAINT_TYPES`).

## JLCPCB (`jlcpcb.json`)

From jlcpcb.com/capabilities/pcb-capabilities, read 2026-10-17, each number quoted
in the file beside it. Where the page gives a recommended and an absolute
minimum, the file uses the absolute: this is the fab's floor, not good practice.

| | 1-2 layers, 1 oz | 1-2 layers, 2 oz | multilayer, 1 oz | multilayer, 2 oz |
|---|---|---|---|---|
| track, spacing | 0.10 mm | 0.16 mm | 0.09 mm | 0.15 mm |
| plated-hole annular ring | 0.18 mm | 0.254 mm | 0.15 mm | 0.254 mm |

Every row: via drill 0.15 mm, via pad 0.25 mm, plated hole 0.15 mm, non-plated
hole 0.50 mm, hole to hole 0.45 mm (via to via 0.20 mm), copper to a routed edge
0.20 mm, silkscreen text 1.0 mm high and 0.15 mm stroke, silkscreen 0.15 mm from a
pad. Not checked: board size (KiCad has no constraint), soldermask web and BGA pads
(they depend on the mask colour and finish ordered), the 0.1 mm drills sold as an
option on boards 1 mm thick or less.

## Adding a fab

Copy `jlcpcb.json`, keep its shape, and quote every number from the fab's page
with the day you read it. A `silk_clearance` rule must carry the condition
`A.Type == 'Pad' || B.Type == 'Pad'` (silkscreen to pad): the count takes every
finding that names the run's silk rule as the fab's, so any other silk rule would
put silk on silk, which is `pcb.drc`'s, into it; and its minimum must be above
0, where KiCad leaves the rule's name out of the finding. A row holds one `clearance`
rule, with no condition: the run floors local clearance overrides at it, and a
second or narrower rule would sit above that floor. The gate refuses either.
Point `pcb_fab` at the file (a path from the project root), or add it to `gates/fab/` in a pull request. The gate's known-bad control
reads the baseline's own fab, so a new file needs no new fixture; it does need a
row for a 2-layer 1 oz board if it is to be the baseline's.
