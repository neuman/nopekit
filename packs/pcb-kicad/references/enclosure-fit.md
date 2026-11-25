# Enclosure fit: what `pcb.enclosure_fit` runs

## The limit

The least gap a part keeps from the enclosure is the limit of the claim tagged
`enclosure-fit` (`ctx.acceptance`): `>=`, in mm. The baseline states it under
`_claims` as FIT1, at 1.0 mm. A claim in other units, or with another comparator,
makes the gate error. Until general-packs P7 the limit was a pack parameter,
`pcb_enclosure_clearance_mm`. A project that still states it is refused, and the
error names the move: dropped in silence, the project would think its number was
used.

## The frames

`kicad-cli pcb export step --subst-models --no-dnp --user-origin 0x0mm` writes the
populated board with the board file's (0, 0) at the origin, x right, **y up** (the
board file's y is down, so every y is negated), z up from the board's bottom face.
The baseline: board body x 0 to 30, y -15 to 0, z 0 to 1.51; the header pins z
-1.405 to 10.135.

`pcb_board_placement` = `{"x", "y", "z", "rot_x_deg", "rot_y_deg", "rot_z_deg"}`
turns that about x, then about y, then about z (each about the enclosure's own
axes, through the board's origin), and then moves it into the enclosure's frame.
The gate composes the three turns into one 3x3 rotation, so a board may sit in any
orientation: face down is `rot_x_deg` 180, on its edge is 90. x, y and z must be
stated; a turn not stated is 0. The enclosure's STEP is its material, in mm.

The baseline's enclosure has its origin on the cavity floor, under the board's
(0, 0) corner, and the board sits at z 4. A placement of all zeros gives the
mutation walk nothing to move (zero scaled is zero), so before P7 its only
landing was the limit parameter, which moved the limit, not the design. Now the
walk raises the board until the pins near the lid, and the gate must fail that.

## Several boards

`pcb_enclosure_fits` holds one entry per board, keyed by an id: `{board,
enclosure_step, placement, bodiless, contacts, heights}`, each field as the
singular key it stands for. It replaces the singular keys, and stating both is
refused. Each board is fitted against its enclosure. Boards whose enclosure
files hold the same bytes are in one enclosure, however each path is spelled
(`cad/enc.step`, `./cad/enc.step`, an absolute path, a link, a copy), and are
also fitted against each other: every shape of one keeps the limit from every
shape of the other, the bodies included, and a shared volume fails. Until the
review of P7 the group was the path as written, so `./cad/enc.step` was another
enclosure and two boards that collided read pass. The group is not the file that
a link names: a verdict's key holds the bytes at each path, not where a link
points. The verdict says when the boards are in more than one enclosure file.

The verdict is the worst over every board, and a board that could not be judged
(a skip or an error) is named beside a fail, never in place of it. A board with a
part missing from its export is judged on the shapes that arrived and named as
not judged: a shape that is there can only come nearer to something when a
missing one is added, so its fail is a fail and its pass is not. (Each pair of
boards is measured once, under the first; until the review of P7 a board with a
missing part was dropped with its pair rows, and the same records read fail or
skip by the order of their keys.)

## What kicad-cli leaves out

A part whose 3D model it cannot find is left out of the STEP, silently, with exit
0. KiCad's 3D library is a separate package on most Linux hosts (and is absent on
the machine this pack was built on), so a fit read from that export reads clear
with nothing on the board. The gate therefore:

1. lists every placed part and skips, naming them, if any names no 3D model and
   has no declared height. A placed part is any footprint not `dnp` that shows a
   3D model, whatever its type, and any `smd` or `through_hole` footprint not
   `board_only`. A display, a cell, a speaker or a buzzer fitted by hand is
   often drawn with no type and kept out of the BOM and the position file, and
   kicad-cli exports its model all the same. (What slipped through, a holdout
   run: such a display was read as no part, its model was not staged, and the
   pass said nothing collides with the window ledge cutting 22.75 mm^3 into it.)
   A footprint with no type and no model (a mounting hole, a fiducial, a logo) is
   not asked for, and the verdict names it ("not fitted"). The same skip names the
   parts whose named model is found nowhere on this host, so one run says all
   there is to state;
2. reads the export with OpenCascade's XCAF reader, which keeps each component's
   name (KiCad names each part's model by its reference), and skips, naming them,
   if any placed part did not arrive. Such a part names a model, so the skip
   says to install the library, keep the model beside the board, or state a
   height.

`pcb_enclosure_bodiless` lists, by reference, parts that truly have no body to fit
(an antenna drawn in copper). A model kept with the project (`${KIPRJMOD}/...`,
KiCad 5's `$(KIPRJMOD)/...`, or a relative path) is copied into the stage with the
board, a VRML model's STEP twin with it; the baseline keeps KiCad's own models
for its two footprints in `selftest/assets/3d/` for that reason. kicad-cli
resolves `${KIPRJMOD}` only where a `.kicad_pro` sits beside the board.

## A part with no model: a declared height

`pcb_part_heights_mm` = `{ref: {h, source, box_mm?}}` gives a placed part whose
3D model is not on this host a body: the box around its courtyard, on its own
side, or the outline `box_mm` states (`[[x0, y0], [x1, y1]]`, mm, in the
footprint's own frame, x right and y down as KiCad's footprint editor shows it:
for a part with no courtyard), raised `h` mm (the datasheet's seated height;
`source` says which).

A part whose model is not on this host is one that names none, or one whose
every model is found nowhere here: `${KIPRJMOD}` is the board's folder, another
variable the environment's, and KiCad's own `KICAD<n>_3DMODEL_DIR`, unset, is
looked for where KiCad's installers put the library; a VRML model counts by the
STEP beside it. There the box stands in, the model left out of the staged copy,
and the pass names it ("at its declared height, its model not found here"). On a
host that has the model, the model is fitted and the pass names the height
unused. One body each, never both: a union of the two would fail a sound board
on one host and not another. (What slipped through, a holdout run: a part that
named a model was refused a height outright, so a board copied from a machine
with other libraries could be fitted only by editing the board.)

The box is written as a STEP in the footprint's own model frame and named as the
part's model in the staged copy of the board, so kicad-cli seats, turns and flips it as it does any model. That
matters: KiCad 9.0.9 seats a model at z 1.595 on a 1.6 mm board (and at -0.085
under it), not on the board body's top face at 1.51, so a box placed by the gate's
own arithmetic would sit 0.085 mm low. Measured with a 45 degree part on top and
a 30 degree part flipped underneath: each box lands on its courtyard as KiCad
places it.

The box is the courtyard's, in the footprint's frame: a part turned 45 degrees gets
a box turned 45 degrees, not the larger box around it. A courtyard is a keep-out,
usually a little wider than the body, so the box is a little wider than the part.
One listed as bodiless with a height, and one with neither a courtyard on its own
side nor a `box_mm`, are refused.

## Contacts by design

`pcb_enclosure_contacts` = `{ref: {feature: {reason, region_mm}}}` names a part
that touches one feature by design: a switch on its plunger, a receptacle in its
cutout, a light pipe, a cell on foam. A feature is:

- a region of the enclosure, `region_mm` = `[[x0, y0, z0], [x1, y1, z1]]` in the
  enclosure's frame: the part's distance is measured to the enclosure with that
  box taken out, and its distance to the material inside the box is listed;
- or, with `pcb_enclosure_fits`, one shape of another board in the same enclosure,
  `"<fit>:<ref>"` or `"<fit>:board"`: one contact holds both ways.

The part keeps the limit against every other feature, and any shared volume still
fails it: a contact excuses closeness, never a collision. A ref with no feature
and a feature with no reason are refused. An allow-list entry for one part
against anything once hid a real interference, so a region is held to what its
part can reach:

- the enclosure material it takes out must lie within twice the claim's limit of
  the part's own box (its bounding box as placed, in the enclosure's frame).
  Material farther away cannot come within the limit of the part, so a region
  needs no reach there; the second limit is room to draw in. A region that
  reaches farther is refused, and the error gives the region cut to that reach.
  A region with no material near the part excuses nothing and is refused too;
- a region that holds the enclosure's whole box is refused, and so are regions
  that together take out all of the enclosure (the part would be judged against
  nothing).

Each part's box as placed is not known before KiCad exports the board, so the
regions are held to it in a pass of their own, after the export and before the
full fit: the regions' pass reads only the parts that have regions, their boxes
and the material each region takes out, with no distance and no shared volume.
Every refusal is listed in one run: the error is the first refusal's words, the
detail names each part and region refused, and `regions_refused.json` holds each
one's error, with the box to draw, and its boxes. (What slipped through, a
holdout run: the regions were held only after the full fit, and the first refusal
returned, so each region drawn too wide cost a two to four minute run to learn of
the next. To read each part's box before drawing a region, run once with no
contacts: a pass or a fail lists the nearest part, and `fit.json` holds every
part's distance.)

What this holds, and no more: a region excuses its part only against material
near that part. Inside the reach the region is the person's statement of the
feature: a region drawn over two features near the part (the lid and the wall
beside it) excuses both, and only the record says which feature was meant. Until
the review of P7 only the whole-box test stood, and a region 0.1 mm short of the
enclosure's box excused each header against the walls and the floor as well as
the lid it named.

## What it measures

In a child process (an OpenCascade boolean can crash its interpreter), for each
part and for the board body: the volume it shares with the enclosure (a boolean
common) and its distance to it (BRepExtrema); for each pair of boards in one
enclosure, the same from every shape of the first to the second. Shared volume
above 0.001 mm^3 fails the gate, a board body included; the nearest distance is
the verdict, against the claim's limit. A board body may touch the enclosure: it
sits on standoffs. Against another board it keeps the limit.

Baseline: nearest part J1 (and J2), its pin tips 2.365 mm under the lid, against
1.0 mm. Raised 3 mm (`raised_board`), both headers share 0.931 mm^3 with the lid.

## What it does not see

A part that passes through an opening cut for it (a knob, a button cap, a light
pipe) shares no volume with the enclosure, and with the opening cut wide enough
it keeps its clearance too, so it reads clear however far it stands proud of the
face. A contact declared against the opening excuses its closeness, so it reads
clear then too. How far a part should reach is a design statement the solids do
not hold: a lens (`lenses.md`, "A part taller than its cavity"), or a physical
claim.

Two models that overlap by design (a mated connector pair whose vendor models
interpenetrate) read as a collision, and no contact excuses it: a wrong FAIL, said
here so a person can read the shared volume against the drawing.

One limit for every board: the claim tagged `enclosure-fit` states one condition,
and claims with that tag that state different ones make the gate error.

Two copies of one enclosure file are one enclosure. A project with two housings
of one design, a board in each, gets its boards fitted together in one frame: a
wrong FAIL where they overlap. Export each housing as its own solid (two files
with different bytes), or check them in two projects.

## Cost

About 1 s for the export and 3 s for the geometry on the baseline, most of it
importing CadQuery. A declared height adds one more child (about 1.5 s) to write
the boxes, and contact regions one more (the regions' pass). A board with many
detailed models takes longer: a populated handheld board of 74 modelled parts took
2 to 4 minutes (a holdout run). Each pair of boards in one enclosure adds one
distance and one boolean for each shape. Each child (the export, the regions'
pass, the geometry) gets `pcb_fit_timeout_s`, or, unstated, 120 s and 5 s for each
part it handles (490 s for 74 parts), and a run past it is a skip that says how
long it ran, over how many parts, and how the time was chosen.
