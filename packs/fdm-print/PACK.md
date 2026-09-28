# fdm-print

Filament printability. Seven gates: five that cost nothing and run on every edit,
two that read the mesh. **Every one of them judges ONE PRINTED PART, and expects
that part IN ITS PRINT ORIENTATION WITH THE BED AT Z=0** — the pose the machine
will actually build it in, not the pose it occupies in the assembly. Hand a gate
here a part exported in assembly coordinates and you get an answer that is true of
that pose and useless about the print: a flat deck panel exported 70 mm up and
tilted in hull coordinates was reported as a 201.7 mm unsupported cantilever,
which is exactly what it is where it sits and not remotely what it is on a bed.
Export two sets if you need two frames — `build/*.stl` in assembly coordinates for
the clash check and the viewer, `build/print/*.stl` laid down as printed — and
point this pack at the second.

The same distinction runs through the parameters. This pack's `part_bbox_mm`,
`part_volume_mm3` and `part_min_wall_mm` describe **one part**; `cad-solid`'s
`assembly_bbox_mm`, `assembly_volume_mm3` and `process_min_wall_mm` describe **the
assembly** or **the process**. They were all once spelled `bbox_mm`, `volume_mm3`
and `min_wall_mm`, and on a project with both packs installed — an assembly and
printed parts, which is every mechanical project — one word had to mean two
things. See [Keys, frames and resolution order](#keys-frames-and-resolution-order).

**One part, or a whole plate.** `fdm.overhang`, `fdm.bridge_span` and
`fdm.bed_fit` also take a **set** of parts and return ONE verdict about it: the
worst part's number, that part named, how many were checked and how many are past
the limit, a locator per offending part, and a per-part table in the evidence. The
other four gates read one number each and judge one part, as they always have. See
[Many parts](#many-parts).

## What this pack settles

- **Does it fit the machine**, with the brim on it, in either XY orientation, and
  under the Z travel.
- **Is every section thick enough to print reliably** — three perimeters at the
  nozzle you are actually using.
- **Will it need support**, how much of the part is involved, and how bad the worst
  face is.
- **Will the unsupported horizontal spans bridge**, or do they need a chamfer, a
  split, or support — and **is it a bridge at all**, because a ceiling held on one
  side is a cantilever and gets a limit more than ten times smaller.
- **Is the primary load carried across the layer bonds** — the failure a structural
  pack cannot see, because a structural pack computes with bulk properties and a
  printed part is not a bulk material.
- **Roughly how long it will take and how much filament it will eat**, to an order
  of magnitude, against a time ceiling *and* a one-spool mass ceiling.
- **All of the above over a whole print set**, for the three gates that can — one
  verdict naming the worst part, counting the set, and naming every part it could
  not read. See [Many parts](#many-parts).
- **Is the mesh a solid at all** — both mesh gates refuse a mesh that is not
  watertight or is wound inside out, because every face normal in it is a fiction
  and so is every number read off one.
- **Whether the pack's own rules still apply** — `fdm.process_model_valid` decides
  whether the other six are entitled to an opinion at all.

## What it does NOT settle

Read this section before you trust a green sweep from this pack.

- **It cannot tell whether the mesh you handed it is in print orientation.** Every
  gate here measures the frame of the file, and a part exported in assembly
  coordinates produces confident, correct, useless numbers about a pose nobody
  prints. Nothing in the pack can detect that — a tilted panel is a legitimate
  print orientation for some other part — so it is on the model to export the
  print set, and it is stated at the top of this file for that reason.
- **It is not a slicer.** No gate here runs the actual toolpath. A part can pass
  every gate and still fail in the slicer on a thin-wall gap-fill artifact, a
  seam placement, or a cooling conflict between two islands.
- **It says nothing about dimensional accuracy.** Holes print undersize, outer
  dimensions print oversize, and both depend on the machine, the material and the
  flow calibration. If a feature has a tolerance, that is a *physical* claim
  settled with calipers on a real print, not here. See `sourcing.md` for what to
  expect per material.
- **It does not check that supports can be removed.** A steep face inside a closed
  pocket passes `fdm.overhang` and traps its support permanently. That is a review
  question — see `lenses.md`.
- **It does not check bed adhesion or warp.** A 200 mm flat ABS part fits the bed,
  clears every wall rule, and still lifts its corners. Warp is a function of
  material, footprint area, enclosure and cooling; the pack carries the knowledge
  in `sourcing.md` and the questions in `lenses.md`, but has no gate for it.
- **`fdm.layer_alignment` is a screen, not a strength calculation.** It derates a
  utilisation somebody else computed. It cannot tell you the part is strong enough;
  it can tell you the orientation makes that number meaningless.
- **`fdm.print_time_est` is an estimate.** Expect ±30% against a real slicer and
  worse on small, tall or many-island parts. It exists to catch the three-day print
  and the part that will not fit on one spool — it now has a threshold for both,
  but a mass that clears `max_filament_g` is still an estimate, not a weighed spool.
- **`fdm.layer_alignment` is optimistic through the middle of its range.** The
  ``1 - (1-r)·sin²φ`` interpolation agrees with Hankinson at 0° and 90° and sits
  9–13% above it between; a part that only just passes at φ ≈ 45° has not been
  proven. The reasoning and the numbers are in `_directional_knockdown`.
- **Mesh solidity is checked, mesh *quality* is not.** The gates refuse a mesh that
  is not watertight or is wound inside out. They say nothing about sliver triangles,
  a tessellation too coarse to represent the real chamfer angle, or a mesh that is
  watertight and still self-intersecting in a way `is_watertight` does not see.
- **It does not decide the print orientation.** Everything here measures the part
  *in the orientation it was handed*. Re-orienting changes the overhang, bridge,
  strength and time answers at once, and choosing between those is a design
  decision — `references/orientation.md` is the guidance, not a gate.
- **`min_wall` does not know whether a thin section matters.** It settles wall
  thickness, not minimum feature size: a 1 mm cosmetic fin and a 1 mm load path
  fail it identically.
- **A cantilever allowance is a rule of thumb, not a sag calculation.** The gate
  separates a cantilever from a bridge and compares it to `max_cantilever_mm`; it
  does not compute how far the strand actually droops for your material, cooling
  and layer time.
- **It does not lay out a plate.** Over a set, every gate judges each part **on
  its own**: thirteen parts that each fit the bed is not the same statement as
  thirteen parts that fit the bed *together*, and nothing here packs them, spaces
  them, or counts how many plates the job takes. `fdm.print_time_est` is likewise
  per part, so a set's total print time is not the sum of anything this pack
  reports.
- **It does not choose which parts belong in the set.** The set is whatever the
  projection names. A part the model forgot to list is not checked and not
  counted, and no gate here can tell that it exists — which is the one failure
  mode the multi-part mode cannot close, because it is upstream of it.
- **Nothing here covers resin, SLS, or any other additive process.** The physics of
  a layer bond is specific to extruded filament.

## Gates

| id | tier | measures | fails when | control |
|---|---|---|---|---|
| `fdm.process_model_valid` | 0 | units (absolute extent, volume vs its own bbox, thinnest section vs one bead), nozzle range, layer/nozzle and width/nozzle ratios | any assumption the other gates stand on has stopped holding | `metres_not_millimetres` — every length, area and volume in the projection divided by 1000 to the right power |
| `fdm.bed_fit` | 0 | footprint / (bed − 2× brim), and height / Z — **one part or the whole set** | utilisation > 1.0 on any part | `brim_overflow` — a footprint sized halfway between the brimmed area and the raw bed, from the projection's own brim |
| `fdm.min_wall` | 0 | thinnest section vs 3 perimeters at the line width | section < minimum | `two_perimeter_wall` — the section cut to two beads of the width the project actually uses |
| `fdm.layer_alignment` | 0 | utilisation derated by the layer-normal knockdown, strength or modulus per `utilisation_kind` | derated utilisation > 1.0 | `load_across_layers` — load rotated onto the build axis with the utilisation set 15% past the knockdown |
| `fdm.print_time_est` | 0 | estimated hours and grams | hours > `max_print_time_h` **or** grams > `max_filament_g` | `crawling_speed` — the speed, solved from the gate's own model, that lands the part 15% past the project's time ceiling |
| `fdm.overhang` | 1 | area past the overhang limit, as a fraction of surface — **one part or the whole set** | fraction > allowance; mesh not a solid; `overhang_limit_deg` ≥ `bridge_ceiling_deg` | `steep_cone` — a cone stood on its apex, 70° half angle |
| `fdm.bridge_span` | 1 | longest unsupported horizontal span, per ceiling region, classified bridge / cantilever / unanchored — **one part or the whole set** | span > `max_bridge_mm` (bridge), > `max_cantilever_mm` (cantilever), any span at all (unanchored); mesh not a solid | `long_bridge` — a PLATE of three parts whose middle one is an arch spanning 3× the limit between two real anchors |

Thresholds and their provenance live inline in `gates/printability.py` and
`gates/mesh.py` as module constants with docstrings — the brim allowance, the
three-perimeter rule, the 45° limit, the 0.5× layer-normal ratio, the duty factor.
Every one of them is overridable from the projection, and every verdict prints the
value it actually used.

`fdm.overhang` and `fdm.bridge_span` need `trimesh` and `numpy`. Both are declared,
both are imported lazily inside the gate bodies, and when they are missing the two
gates report SKIPPED with the module named and their claims resolve BLOCKED. They
never fall back to a cheaper approximation. A pack that quietly downgrades turns
"we did not check this" into "this is fine".

## Views, and the node name that is an interface

| View | Kind | Asset | Built from | Addressed by |
|---|---|---|---|---|
| `print_part` | `model3d` | `assets/print_part.glb` | the same `mesh_path` — or the same part SET — the mesh gates read | node name |

`views/part.py` exports the part **in its print orientation, untransformed** — no
recentring, no reorientation, no node matrices — so a face centroid measured by
`fdm.overhang` is that same point in the GLB. `meta` carries the build axis and,
when the projection states one, the bed envelope, so the viewer can draw the box
`fdm.bed_fit` is comparing against rather than only reporting a ratio. No
`mesh_path`, a path that does not resolve, or a file that will not load: the
viewgen returns `None` and says why in the build log. The pack's five tier-0 gates
still report.

### The node naming scheme

```
mesh_path "build/saddle_clamp.stl"  ->  mover "saddle_clamp"  ->  node "saddle_clamp__0"
```

- **A node is `<part>__<body>`**, numbered from 0 in sorted order. One part means
  one node; a part exported as a multi-body scene (a plate carrying the part and
  its test coupon) gets one node per body under one mover.
- **The mover name** is `part_name` from the projection when the model states one,
  and otherwise the mesh filename without its extension. Derived rather than
  required: a project that has already named its build output should not name it
  twice, and a pack that demanded the name would make the whole view optional on a
  key nobody thinks to set.
- Names are sanitised to letters, digits, `-`, `.` and `_`, with runs collapsed —
  `__` is the mover separator, so `saddle clamp` must not become a *body of*
  `saddle`.
- Both sides read `fdm_print_parts.py`. **This is an interface**: change it there
  or not at all.

### What each gate pins

| Gate | Locator | Why that and not more |
|---|---|---|
| `fdm.overhang` | one part: up to twelve `face` pins, each at a face centroid in model coordinates, carrying the angle and the area. A set: the steepest face of each offending part, one pin each | the steepest face first (how bad it gets — an 89° nub prints as a blob), then the largest by area (how much of the part is like this, which is what the gate fails on). Ranking by angle alone pins twelve slivers and misses the shelf; by area alone it misses the nub |
| `fdm.bed_fit` | one `part` pin per offending part, and only when that part has a mesh to draw | the bounding box is a property of the whole part. There is no face to blame, and this is a tier-0 gate that fires happily on a project that stated a box and exported nothing — a locator aimed at a view that does not exist reads as a broken pack |
| `fdm.bridge_span` | one part: none. A set: one `part` pin per offending part | over a set the question has become *which part*, and that it knows exactly. Where the span is is still not something it could defend: it is measured between anchors found by direction, not at a point |
| `fdm.min_wall`, `fdm.layer_alignment`, `fdm.print_time_est`, `fdm.process_model_valid` | none | none of them resolves to a position. A bridge span is measured between two anchors the gate finds by direction, not at a point it could defend; the rest are arithmetic on the projection |

Twelve pins is the cap, and the area fraction in `measured` is always the real
one: the cap trims the drawing, never the measurement. A part that needs support
has thousands of faces past the limit, and pinning them all paints the model red
without telling a reader anything the colour did not.

## Keys, frames and resolution order

Every key below describes **one printed part, in print orientation, bed at z=0**.
Where a project has more than one pack reading its projection, prefix the key with
this pack's scope — `fdm.` — and the gate will take that one first. A project with
only this pack installed writes the bare key and nothing changes.

**Resolution order, in full, highest first.** Nothing here is discovered by
experiment; the previous order (`bbox_mm` before `bbox`, undocumented) cost a user
an afternoon:

1. `fdm.<key>` — this pack's scoped spelling. Also accepted as a nested group:
   `"fdm": {"part_bbox_mm": [...]}`.
2. `fdm-print.<key>` — the pack's full name, for people who type that.
3. the bare key, trying each spelling in the order the table gives them.

So **a scoped key always beats an unscoped one**, whatever its rank in the family,
because a scoped key is the only one of the two that is a statement about *this*
pack.

| Quantity | Primary key | Also accepted, in this order | What it is |
|---|---|---|---|
| Part envelope | `part_bbox_mm` | `bbox_mm`, `bbox`, `footprint_mm` | `[x, y, z]` extent of ONE part as printed, mm. Not the assembly — that is `cad-solid`'s `assembly_bbox_mm` |
| Part volume | `part_volume_mm3` | `volume_mm3`, `solid_volume_mm3` | solid volume of that part, mm³. Not the assembly's material total |
| Thinnest section | `part_min_wall_mm` | `min_wall_mm`, `thinnest_wall_mm`, `min_feature_mm`, `min_section_mm` | MEASURED off the part, mm. `cad-solid`'s `min_wall_mm` is the opposite kind of number — the thinnest wall a *process* allows |
| Part mesh | `mesh_path` | `stl_path`, `part_mesh`, `geometry_path` | the exported mesh **in print orientation, bed at z=0** |
| Build direction | `build_axis` | `layer_normal`, `print_axis` | `[x, y, z]` vector **or an axis name**: `"z"`, `"+Z"`, `"-y"` |
| Load direction | `load_axis` | `primary_load_axis`, `load_direction` | same two shapes. `load_axis: "z"` used to report *"the projection has no load_axis"*, which reads as "you did not set it" to the one person who knows they did; a value that is present and unreadable now says so, and says what shape it wants |

The full list, with a unit and a sentence for each key, is
`selftest/baseline.json` — every key any gate here reads, on a design they all
pass, with the fallback spellings in its `_aliases` map. Read that file before
writing a model for this pack; it is the teaching example.

`atompipe doctor` diffs these vocabularies against every other installed pack's and
warns when two of them read one key differently, naming both packs and the scoped
spelling that separates them.

## Many parts

A real mechanical project prints a set. This pack judged one part, and a project
that printed thirteen had to compute the worst part in its own model and hand that
one over — which works, and costs two things worth naming:

- the readiness report implied all thirteen were checked; **one was**, and nothing
  in the verdict said so;
- the chosen part **changed five times** as the geometry was fixed, so the gate
  silently moved to judging a different object between runs. A verdict that
  changes subject without saying so is hard to reason about.

So state the set and let the gate do the picking, out loud.

### Stating the set

| Quantity | Primary key | Also accepted, in this order | What it is |
|---|---|---|---|
| The print set | `mesh_paths` | `parts`, `part_meshes`, `mesh_path_by_part` | `{name: path}`, one entry per part **in print orientation, bed at z=0**. A bare list of paths works too and takes each name from its filename |
| Per-part envelope | `bbox_by_part_mm` | `print_bbox_by_part_mm`, `bbox_mm_by_part`, `bboxes_mm` | `{name: [x, y, z]}`, each part's extent as printed, mm |

Both resolve pack-scoped first, exactly as every other key here does:
`fdm.mesh_paths` beats bare `mesh_paths`. An entry may also be a dict carrying
both — `{"deck_mid": {"mesh_path": "...", "bbox_mm": [...]}}` — and a set stated
in one key is merged with a set stated in the other, so a project can name its
meshes once and its boxes once.

```python
# in build(), from whatever the model already knows about its own parts
"mesh_paths":       {name: f"build/print/{name}.stl" for name in parts},
"bbox_by_part_mm":  {name: part.print_extent_mm for name, part in parts.items()},
```

**Precedence, stated because the last unstated one here cost a user an
afternoon:** a **set wins over the single `mesh_path`** when a project states
both, because the set is the more complete statement of what is being printed —
and every multi-part verdict prints the key the set came from, so nobody has to
run an experiment to find out which spelling won.

A set key that is stated in a shape this pack cannot read — a count where a
mapping was meant, say — is a **SKIP naming the key**, never a quiet fall-back to
the single part. Answering a question about one part when the reader asked about
thirteen is the original complaint with a new cause.

`fdm.bed_fit` is the exception and it says so in its own verdict: with a set that
carries no per-part extents it falls back to the single `part_bbox_mm` and adds
*"the 13 part(s) in `mesh_paths` state no per-part extent, so this verdict is
about the one box above and not about them"*. Thirteen skips would be a worse
answer than the one it can give, and a silent fallback would be the original
complaint all over again.

### What the verdict says

```
[FAIL] fdm.overhang : worst of 13 in `mesh_paths`: hull_bow at 0.68% of surface
       past the 50 deg limit vs 0.50% allowed (worst face 67.5 deg, 20 faces,
       375.8 mm^2); 1 of 13 past the limit (hull_bow)
       measured=0.00677 limit=0.005 area fraction
```

- **`measured` is the worst value across the set**, against *that part's own*
  limit. For `fdm.bridge_span` that matters inside one verdict as well as across
  it: a bridge is judged at `max_bridge_mm` and a cantilever at
  `max_cantilever_mm`, so the worst part is the one furthest past **its own**
  allowance, not the one with the longest span.
- **The worst part is named, and so is the count.** `worst of 13` is the whole
  set, and a change of subject between two runs is then a diff in the ledger
  instead of nothing at all.
- **One locator per offending part**, capped at twelve pins, so the page lights up
  every part that is wrong rather than only the worst. `fdm.overhang` pins the
  steepest face of each offender — a position, not just a part — and
  `fdm.bridge_span` pins the part, because it knows which part and not which
  point.
- **`evidence` carries the per-part table**, one row per part, worst first, with
  a `score` column that is the part's measurement over its own limit. Offending
  parts also get their own face or region file. The table is written whether the
  set passes or fails: a passing sweep's table is how a reader finds out, a month
  later, whether the part they are worried about was in the set at all.

```
# fdm.overhang — 13 part(s) from `mesh_paths`
# build axis (0.0, 0.0, 1.0), limit 50.0 deg, allowance 0.50% of surface; ...
# 1 past the limit, 0 not measured
part           status    score   area_frac worst_deg worst_downward_deg faces_past ...
hull_bow       measured  1.3545  0.00677   67.55     67.55              20
bulkhead_aft   measured  0.0000  0.00000   0.00      0.00               0
...
```

### A part that could not be read

**It is a SKIP for that part, reported in the verdict, and it stays in the
denominator.** `worst of 12` on a thirteen-part project is the same lie the
hand-picked-part workaround told, arrived at from the other side.

Which means the outcome ladder over a set is:

| What happened | Verdict | Why |
|---|---|---|
| a part's mesh is not a solid | **FAIL**, `measured` = how many | its face normals are fiction, so every angle and span read off it is too. Reported before any measurement, with the measured parts' summary after it |
| a measured part is past its limit | **FAIL**, worst named and counted | the ordinary case |
| nothing failed, something was unmeasured | **SKIPPED**, naming the parts | the set is not proven while a member of it is unread, so the claim resolves BLOCKED and stays visible. A pass here would be a green row over a set whose unread member is the one that was wrong |
| everything measured and inside its limit | **PASS**, worst named | the count and the worst part are still in the detail, because "13 checked" is the part a reader needs |

An `INERT CONFIGURATION` refusal from `fdm.overhang` is decided once for the
whole set and before any mesh is opened: it is a property of the thresholds, and
no geometry could change the answer.

### Cost

Each distinct file is loaded **once per sweep by the spine's memo** and recorded
for every gate that asks for it. `fdm.overhang` and `fdm.bridge_span` read the
same files back to back and both go through `ctx.load_file`, so thirteen real
parts through both gates is **13 loads, not 26**, and the second gate drops from
38 ms to 5 ms on this set. The memo re-loads a file whose bytes moved between two
gates of one sweep, so a re-export is a miss and never a stale hit — the whole
reason anybody re-runs a sweep is that they just rebuilt their meshes. And a hit
still counts as a read: each gate's verdict names the part among its inputs, so
it goes stale when the part changes. The cache this pack used to keep on
`ctx.extra` did not do that — its hits opened nothing, and the second gate's
verdict named no file at all.

That is the cheap half of the cost. The expensive half is upstream and this pack
cannot help with it: a tier-1 sweep is dominated by the model rebuilding and
re-exporting its geometry, not by reading it back.

### Which control proves which path

Both paths are proven able to fail, by different fixtures, and it is worth knowing
which of your greens rests on which:

- `steep_cone` is a **single part** — the control on the one-part path.
- `long_bridge` is a **plate of three**, the arch between two copies of the good
  baseline clamp. It is the control on the many-part fold: a fold that reported
  the first part, the last part, or an average instead of the worst would report a
  clamp at 0.6 of its allowance and PASS, while a 90 mm bridge sat on the plate.
  `fdm_print_fold.fold` is one function shared by every multi-part gate here, so
  this fixture stands behind `fdm.overhang`'s and `fdm.bed_fit`'s set mode too.
- `selftest/baseline.json` states a two-part `mesh_paths` set, so CI also proves
  the many-part path **passes** a good design — the controls only prove it can
  fail, and a fold that failed everything would otherwise ship green.

Each fixture removes the other's keys (`_stage` drops the set, `_stage_set` drops
`mesh_path`), because a fixture that states both is a fixture testing two things
and proving whichever one the resolution order happened to pick.

## Units and frames

Everything is **millimetres, degrees, grams, seconds**. Volume is mm³, area mm²,
density g/cm³, speed mm/s, time hours in the verdict and seconds internally.

The mesh is read **in its print orientation, with the bed at z = 0**: the model
exports the part the way it will sit on the machine, and every gate here measures
in the frame of the file it was handed. The build direction is `build_axis` in the
projection, defaults to `[0, 0, 1]`, and takes an axis name (`"z"`, `"-y"`) as well
as a vector. Every angle is measured against it, so a project that prints along
another axis gets the right answer without re-exporting.

`build_axis` alone is **not** enough to rescue an assembly-frame export. It rotates
the measurement; it does not tell the gate that the part is lying in a pose nobody
prints. A flat panel with `build_axis` of `z`, exported tilted and 70 mm up in
assembly coordinates, measured a 201.7 mm unsupported span — true of that pose, and
not a fact about the print. Export the print set separately.

**Overhang angle** is inclination away from the build direction: a vertical wall is
0°, a 45° chamfer underside is 45°, a flat ceiling is 90°. This is the number a
slicer's support threshold refers to. `fdm.layer_alignment` uses a different angle —
`phi`, between the load and the **layer plane** — where 0° is in-plane (good) and
90° is layer-normal (bad). The two are complements; do not mix them up.

## The physics in one paragraph

A filament part is a stack of welded beads, not a solid. Within a layer the
material is continuous and close to the coupon strength the datasheet quotes.
Between layers it is a weld made by a bead of hot plastic re-melting the top of the
one below, and that weld is worth roughly half the bead — less if the part is cold,
fast or well cooled. Geometry follows from the same fact: each layer must land on
something, so a face that leans more than about 45° from vertical has half a bead
hanging in air and needs support; a face that is horizontal is not leaning at all
and gets bridged instead, which works up to the span where the extruded strand sags
before it cools. Wall thickness is quantised by the nozzle, because a wall is a
whole number of beads and the slicer fills what is left over with something that is
not structure. And the part is glued to the bed by the first layer alone, so a large
flat footprint in a material that shrinks on cooling is a lever trying to peel
itself off.

If a result here looks absurd, check these: `fdm.overhang` reporting **0.0°** on a
part that visibly has overhangs means the mesh is inside out — an inverted mesh
produces *zero* overhang, not 90°, because every normal is mirrored (the gate now
refuses such a mesh outright rather than reporting the 0.0°); a bed utilisation
near 0.02 usually means the model is in metres or inches; a print time under a
minute usually means `volume_mm3` is actually cm³.

## Common failure modes and what they look like

- **"worst util 1.02 in XY"** from `fdm.bed_fit` — the part fits the bed and not the
  brim. Shrink it, drop the brim and accept the lift risk, or print it diagonally
  (which this gate does not model — it only tries the two square rotations).
- **"2.1 beads"** from `fdm.min_wall` — a wall that will be printed as two
  perimeters with gap fill between them. It prints; it splits in service.
- **"51% of surface"** from `fdm.overhang` — this is not a nub, it is the part. Do
  not reach for support settings; reorient it or add a chamfer.
- **"UNANCHORED"** from `fdm.bridge_span` — a ceiling with nothing under either end.
  No bridge length saves it; it needs support or a different orientation.
- **"CANTILEVER, 1 anchored edge(s) all to one side"** from `fdm.bridge_span` — a
  shelf projecting into air off a single wall. It is not a bridge of twice the
  length: there is no second landing to pull the strand taut, so it droops from the
  first pass. Chamfer the underside to 45°, add a rib down to material below, or
  support it.
- **"is not watertight" / "wound inside out"** from either mesh gate — the export is
  broken, not the design. Every face normal in that mesh is a fiction, so no
  overhang or bridge number could be produced from it. Repair and re-export.
- **"INERT CONFIGURATION"** from `fdm.overhang` — `overhang_limit_deg` was set at or
  above `bridge_ceiling_deg`, which hands every face the gate could fail on to
  `fdm.bridge_span` and leaves nothing for it to measure.
- **"utilisation 0.70 / knockdown 0.50 = 1.40"** from `fdm.layer_alignment` — the
  structural gate passed on bulk properties and the part will snap along a layer.
  Rotating the part is usually free; thickening it is not.
- **A SKIPPED mesh gate with `trimesh` in the reason** — the claim is BLOCKED, not
  passed. Install the dependency or leave the row visibly open.
- **"worst of 13 in `mesh_paths`: hull_bow ..."** — the verdict is about a SET, and
  `measured` is the worst part's number. The per-part table in the evidence has
  the other twelve rows; the count is the whole set, so if it reads `worst of 12`
  on a thirteen-part project the projection is naming twelve.
- **"3 of 16 parts in `mesh_paths` could not be measured: ..."**, SKIPPED — nothing
  failed and not everything was checked, so nothing is claimed. Export the missing
  parts or take them out of the set; do not read it as a pass.
- **"2 of 13 parts in `mesh_paths` are not solids"** — the export is broken for
  those parts, not the design. The measured summary for the rest follows it in the
  same line, and the per-part table has the repair message in full.

## Where to look next

- `gates/fdm_print_fold.py` — how one verdict is folded out of N parts: the
  outcome ladder, the locator budget and the per-part table. Read it before
  changing what a multi-part verdict says.
- `selftest/baseline.json` — a worked projection of a part that passes every gate
  here, with a one-line note on each key and its unit. It is the fastest way to
  learn what this pack expects a model to project, and it is what the negative
  controls are measured against: each fixture moves one number in it and the gate
  under test flips to FAIL. `selftest/baseline_mesh.py` is the source of the solid
  it points at.
- `references/orientation.md` — how to choose the print orientation, and why it is
  the single decision that moves strength, finish, support and time at once.
- `references/geometry_rules.md` — the numeric rules of thumb: holes, threads,
  clearances, text, chamfer-instead-of-support, and the parameters this pack reads.
- `sourcing.md` — filament by material: temperatures, warp, UV and moisture,
  achievable tolerance, and which ones actually survive outdoors.
- `lenses.md` — the adversarial review dimensions to attack the design from before
  anything is printed.
