# Geometry rules of thumb, and the parameters this pack reads

Tier 3. Load this when you are actually drawing the part.

## The parameters the gates look for

Every one is read from the model's projection. A gate that cannot find what it needs
SKIPS and names the key; it never substitutes a value only the project can know.

| parameter | units | used by | notes |
|---|---|---|---|
| `part_bbox_mm` | mm, `[x, y, z]` | `bed_fit`, `process_model_valid` | the part's extent **in its print orientation** (also read as `bbox_mm`) |
| `bed_x_mm`, `bed_y_mm`, `bed_z_mm` | mm | `bed_fit` | the machine. `bed_xy_mm` accepted for a square bed |
| `brim_mm` | mm per side | `bed_fit` | domain default 5.0 |
| `part_min_wall_mm` | mm | `min_wall`, `process_model_valid` | the **thinnest wall across the layer plane** (perpendicular to the build axis, where perimeters print); held to three beads by `min_wall` and one bead by the guard. Also read as `min_wall_mm` |
| `part_min_floor_mm` | mm | `process_model_valid` | the **thinnest section along the build axis** (a floor, roof or web); held to one layer. Optional. A 0.4 mm web is three 0.12 mm layers, not a third of a bead |
| `bbox_by_part_mm`, `part_min_wall_by_part_mm`, `part_min_floor_by_part_mm`, `part_volume_by_part_mm3` | `{part: value}` | `bed_fit`, `min_wall`, `print_time_est`, `process_model_valid` | the same quantities for each part of a print set; each verdict names the worst part |
| `nozzle_d_mm` | mm | `min_wall`, `print_time_est` | |
| `extrusion_width_mm` | mm | `min_wall`, `print_time_est` | overrides the nozzle table when present |
| `perimeters` | count | `print_time_est` | default 3 |
| `layer_height_mm` | mm | `print_time_est` | |
| `print_speed_mm_s` | mm/s | `print_time_est` | nominal, not maximum |
| `volume_mm3`, `surface_area_mm2` | mm³, mm² | `print_time_est` | area is optional and sharpens the shell estimate |
| `infill_fraction` | 0–1 | `print_time_est` | default 0.20 |
| `filament_density_g_cm3` | g/cm³ | `print_time_est` | default 1.24 (PLA) |
| `max_print_time_h` | h | `print_time_est` | domain default 24 |
| `max_filament_g` | g | `print_time_est` | domain default 1000 — one spool; the gate fails on whichever ceiling is closer |
| `load_axis` | `[x, y, z]` | `layer_alignment` | primary load direction, in the print orientation |
| `build_axis` | `[x, y, z]` | all mesh gates, `layer_alignment` | default `[0, 0, 1]` |
| `utilisation` | ratio | `layer_alignment` | a structural gate's output; enables the derating |
| `utilisation_kind` | `stress` / `deflection` | `layer_alignment` | which knockdown applies; default `stress`. A key named `deflection_utilisation` is taken at its word. Anything else makes the gate SKIP rather than guess |
| `layer_normal_strength_ratio` | ratio | `layer_alignment` | default 0.50 — applied to a **stress** utilisation |
| `layer_normal_modulus_ratio` | ratio | `layer_alignment` | default 0.85 — applied to a **deflection** utilisation |
| `mesh_path` | path | `overhang`, `bridge_span` | relative to the project root |
| `overhang_limit_deg` | deg | `overhang` | default 45 |
| `overhang_tessellation_tol_deg` | deg | `overhang` | default 0.5 — a face counts past the limit only beyond it; a chamfer drawn at the rule reads a few hundredths to half a degree past it once exported |
| `overhang_area_allow_frac` | 0–1 | `overhang` | default 0.005 |
| `bridge_ceiling_deg` | deg | `overhang`, `bridge_span` | default 80; the angle at which a face stops being an overhang and becomes a bridge |
| `max_bridge_mm` | mm | `bridge_span` | default 30 — a ceiling anchored on **opposite** sides |
| `max_cantilever_mm` | mm | `bridge_span` | default 2 — a ceiling anchored on **one** side, which droops instead of pulling taut |
| `min_ceiling_area_mm2` | mm² | `bridge_span` | default one bead's footprint (line width squared) — a smaller ceiling region is listed and not judged |
| `supports_by_part` | `{part: {supports, reason}}` | `overhang`, `bridge_span` | a part stated to print on `tree` or `normal` support: only what no support reaches (a void sealed inside the part) is judged |

## Feature rules

All at a 0.4 mm nozzle and 0.2 mm layers unless stated. Scale with the nozzle.

**Walls.** 1.2 mm minimum for anything structural (three perimeters). 0.8 mm is a
cosmetic shell. Below 0.45 mm the slicer will either skip it or print a single wobbly
bead. A wall that is a non-integer number of beads wide gets gap fill in the middle,
which is not structure — prefer wall thicknesses that are multiples of the extrusion
width.

**Vertical holes.** Print undersize by 0.1–0.4 mm because of the inward pull of each
bead on a concave curve, and they are polygonal at small diameters. For anything that
must fit, either model 0.2 mm oversize and test, or model undersize and drill. A
3 mm printed hole is not a 3 mm hole.

**Horizontal holes.** The top of the circle is an overhang that reaches 90° at the
crown, and the crown is a bridge of the hole's diameter. Under about 8 mm the slicer
gets away with it. Above that, either accept a droop at the top, replace the top of
the circle with a 45° chamfer (a "teardrop" or a hexagonal top), or turn the part.
A horizontal hole bigger than `max_bridge_mm` is a `fdm.bridge_span` failure in
every orientation where it stays horizontal. The crown of a small hole is scored as
a *cantilever*, not a bridge, and gets `max_cantilever_mm` — which is why the 8 mm
rule of thumb and the 30 mm bridge figure are not the same number.

**Clearances between printed parts.** 0.2 mm for a snug press fit, 0.3–0.4 mm for a
sliding fit, 0.5 mm for a loose or hinged fit, and add 0.1 mm on any surface that was
printed against support. These are per-side. A hole and a pin both drawn at 6 mm
will not go together.

**Overhangs.** 45° is the design rule. Between 45° and about 60° a well-cooled
machine will usually produce something acceptable but rough, and `fdm.overhang` will
flag it — which is correct, because "usually" is not a specification. Past 60° it
needs support. A chamfer drawn at exactly 45° is not flagged: the gate allows 0.5°
for tessellation, which is what an exported chamfer leans past its drawn angle.

**Bridges.** Under 20 mm reliably; to 30 mm with visible sag on the underside and a
lumpy first layer above; past that, expect to lose the face. Two bridges crossing
each other in the same layer are much worse than one.

**Cantilevers are not bridges.** A bridge has a landing at each end and the strand is
pulled taut between them. A flat ceiling anchored on one side only has nothing to pull
against: it droops as it leaves the nozzle and curls up into the next pass. Two or
three millimetres of unsupported projection — a hole crown, a small ledge — gets away
with it; 15 mm does not, whatever the bridge figure says. `fdm.bridge_span` classifies
each ceiling region by whether its anchors surround its worst point and applies
`max_bridge_mm` or `max_cantilever_mm` accordingly. The fix for a real cantilever is a
45° chamfer on the underside, a rib down to material below, or support.

**Chamfers instead of support.** The cheapest support is a 45° chamfer. Any feature
that overhangs — a boss underside, a horizontal hole crown, a lip, a shelf — can
usually be chamfered into printability for a fraction of a millimetre of material.
Do this before touching slicer support settings.

**First layer.** Expect an "elephant foot": the bottom 0.2–0.4 mm of the part bulges
outward by 0.1–0.3 mm. A 0.5 mm × 45° chamfer on the bottom edge removes it and also
removes the burr that stops a part sitting flat.

**Text and fine detail.** Embossed text needs 0.8 mm stroke width and 0.4 mm height
to survive; engraved text needs 0.6 mm width and 0.4 mm depth. Text on a vertical
wall reads far better than text on a top surface.

**Threads and inserts.** Printed threads work at M6 and coarser, for a few cycles.
Below that, use a heat-set insert (draw the boss hole to the insert's specified
diameter, with a 0.5 mm lead-in taper, and leave 1.5 mm of wall around it) or a
tapped hole in a solid boss. A self-tapping screw into a printed boss splits it along
a layer line unless the boss is thick and the pilot hole is right.

**Ribs instead of thickness.** A 2 mm wall with 2 mm ribs at 20 mm pitch is stiffer,
lighter, faster and less warp-prone than a 5 mm wall, and it does not sink. Keep ribs
under about three times the wall thickness or the outside surface shows them.

**Fillets on internal corners** everywhere the part is loaded. A sharp internal
corner in a printed part is a crack starter that happens to line up with a layer
boundary. 1–2 mm is usually free.

## Scaling with the nozzle

Minimum wall, minimum feature, bridge quality and hole accuracy all scale roughly
with the nozzle diameter; layer height scales with it too (0.25–0.75 of the nozzle
is the workable band). Print time scales roughly with the inverse square. A 0.6 mm
nozzle at 0.3 mm layers is about 2.5× faster than a 0.4 at 0.2 and cannot resolve a
1.2 mm wall — which is exactly the trade `fdm.min_wall` and `fdm.print_time_est` are
there to make visible at the same time.
