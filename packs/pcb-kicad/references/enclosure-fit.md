# Enclosure fit: what `pcb.enclosure_fit` runs

## The frames

`kicad-cli pcb export step --subst-models --no-dnp --user-origin 0x0mm` writes the
populated board with the board file's (0, 0) at the origin, x right, **y up** (the
board file's y is down, so every y is negated), z up from the board's bottom face.
The baseline: board body x 0 to 30, y -15 to 0, z 0 to 1.51; the header pins z
-1.405 to 10.135.

`pcb_board_placement` = `{"x", "y", "z", "rot_z_deg"}` turns that about z and then
moves it into the enclosure's frame. The enclosure's STEP is its material, in mm.
Flipping a board upside down is not a placement this gate takes: export the
enclosure in a frame where the board's top faces +z.

## What kicad-cli leaves out

A part whose 3D model it cannot find is left out of the STEP, silently, with exit
0. KiCad's 3D library is a separate package on most Linux hosts (and is absent on
the machine this pack was built on), so a fit read from that export reads clear
with nothing on the board. The gate therefore:

1. lists every placed part (footprint attribute `smd` or `through_hole`, not
   `dnp`, not `board_only`) and skips, naming them, if any names no 3D model;
2. reads the export with OpenCascade's XCAF reader, which keeps each component's
   name (KiCad names each part's model by its reference), and skips, naming them,
   if any placed part did not arrive.

`pcb_enclosure_bodiless` lists, by reference, parts that truly have no body to fit
(an antenna drawn in copper). A model referenced as `${KIPRJMOD}/...` is copied
into the stage with the board; the baseline keeps KiCad's own models for its two
footprints in `selftest/assets/3d/` for that reason.

## What it measures

In a child process (an OpenCascade boolean can crash its interpreter), for each
part and for the board body: the volume it shares with the enclosure (a boolean
common) and its distance to it (BRepExtrema). Shared volume above 0.001 mm^3 fails
the gate, board body included; the nearest part's distance is the verdict, against
`pcb_enclosure_clearance_mm`. The board body may touch: it sits on standoffs.

Baseline: nearest part J1 (and J2), its pin tips 2.365 mm under the lid, against 1
mm. Raised 3 mm (`raised_board`), both headers share 0.931 mm^3 with the lid.

## What it does not see

A part that passes through an opening cut for it (a knob, a button cap, a light
pipe) shares no volume with the enclosure, and with the opening cut wide enough
it keeps its clearance too, so it reads clear however far it stands proud of the
face. How far a part should reach is a design statement the solids do not hold:
a lens (`lenses.md`, "A part taller than its cavity"), or a physical claim.

## Cost

About 1 s for the export and 3 s for the geometry on the baseline, most of it
importing CadQuery. A board with many detailed models takes longer;
`pcb_fit_timeout_s` (120 s each) turns a hang into a skip.
