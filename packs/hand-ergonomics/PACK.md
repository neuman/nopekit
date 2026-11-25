# hand-ergonomics

This pack judges **the device as the user holds it, in the device frame**: x to the
right as the user sees the face, y toward the top edge, z out of the face toward the
user, all in mm. The model declares where each hand grips the device, and how: round a
handle, or on a slab. It also declares where each control sits. On handle grips,
`ergo.digit_reach` counts the people in a measured population whose
thumb or index finger reaches every control pressed from a grip. The population is
ANSUR 1988: 2,208 women and 1,774 men of the US Army, aged 17 to 51, measured one by
one. The pack ships their hand lengths, subject by subject, in `gates/data/`.
`ergo.layout_agrees` holds that declared layout to the exported scene, so the count
is about the drawn design.

## What this pack settles

- **Reach from a handle.** Can the claim's share of each sex press every control
  without a regrip? The answer is a count of subjects, never a sum of percentiles.
- **The layout is the drawn one.** Every drawn control is declared and every
  declared control is drawn, each cap is where the layout says, no handle is
  thicker than the declared diameter, and each handle grip's centre is on its drawn
  handle's axis. The reach claim waits for this too.
- **Where a control can go** (the proposer). A map of each thumb's reach, how many
  keys of a pitch fit in reach, how wide the grips can spread before the centre
  controls drop out, and design ranges for hand size, thumb breadth and a wrist band.

## What it cannot settle

Say these out loud. A reader will assume the pack covers them, and it does not.

- **Comfort.** A pass says no subject is out of reach at full extension. It does not
  say the stretch is easy, that a control is not too close to the base of the thumb,
  or that a cap edge does not catch the skin. A person holding the print finds those.
- **Width, height and mass.** No survey gives a width limit for a two-handed device.
  Hold the design against a device the person uses every day: its width grounds
  cad-solid's `assembly_bbox_limit_mm` for `cad.bounding`, and a physical claim
  settles the feel. Mass and centre of mass are not modelled; ballast on a print
  shows them.
- **Slab grips.** A phone, an e-reader, a flat remote or a board held at its short
  ends is not a fist round a handle. No public survey measures where the wrist sits
  behind a slab, so the reach count has no source for it. Declare each such grip
  `kind: slab`. `ergo.digit_reach` then makes no count. It skips and names the slab
  grips, so the reach claim reads Skipped. A slab device gets a physical reach test
  instead: two testers press every control on the first full print (REACH-HELD in
  `scaffold/`, the protocol in `skills/reach-testing/SKILL.md`). It states no
  `hand_grip_diameter_mm`. `ergo.layout_agrees` still runs: it holds every control
  and cap to the scene, and measures no section at a slab grip. But REACH was the
  claim it covered. With REACH left out, the guard covers no claim and its fail moves
  no status, until you add a claim tagged `control-layout`. The proposer writes the
  design ranges and the testers' percentile only. Copy GRIP-POSTURE and REACH-HELD,
  not REACH, and add the layout claim (Claims and milestones, below).
- **Children.** The survey's youngest subject is 17. For younger users a pass does not
  count (Gap, or Assumed under an owned `fallback`); a fail still counts.
- **A wide cap.** A control is a point (Keys, below): a space bar is judged at its
  centre.
- **Older users' joint range, other populations, gloves, force, fatigue, drop risk.**
  The population is the US Army of 1988. A market with smaller hands sets a higher
  limit or brings another dataset.
- **A design with no exported scene, or no mesh library.** `ergo.layout_agrees`
  skips with no scene, with no part named `ctl-<id>`, and where trimesh, NumPy or
  manifold3d is missing. It covers the reach claim, so the claim reads Skipped until
  the scene is published and the libraries are installed (`pip install trimesh numpy
  manifold3d`). This is the cost of a reach pass that is about the drawn design.
- **Grip posture, or where the hand really holds.** The scene shows the handles, not
  the hand. `ergo.layout_agrees` holds each handle grip's centre to its drawn
  handle's axis, but where along the handle the hand holds, and the hand axis, stay
  a declaration, owned by GRIP-POSTURE and settled by the testers. So does the kind
  of each grip.
- **An unevenly meshed or sharp-edged handle.** A tessellated handle slices short:
  n even steps on a round handle read n sin(pi/n)/pi of its diameter, 0.64% short
  at 16 segments, 2.55% at 8 and 4.51% at 6. The gate scales each section back by
  that share for the steps its loop turns like, so an export at one angular step
  reads its true diameter. A loop fine in one part and coarse in another is an
  estimate. A handle with sharp edges reads thicker than it is (a square one by
  11%): declare that larger value, which is conservative, or round the edges.
- **The handle's direction.** The reach count does not read `handle_axis`, and the
  guard checks it only through the section: a drawn handle tilted from the declared
  axis cuts an oval, which reads thicker, never thinner.

## Gates

| Gate | Tier | Measures | Limit from | Known-bad control |
|---|---|---|---|---|
| `ergo.digit_reach` | 0, stdlib | worst-sex share of subjects who reach every judged control, % | the claim tagged `digit-reach` (`>=` or `>`, in %) | `face_cluster_out_of_reach`: the four right-hand face buttons 10 mm up the face; 63.08% of women, 84.38% of men |
| `ergo.layout_agrees` | 1, trimesh + NumPy + manifold3d | the worst cap's distance from its declared position, mm; or, on a structural disagreement, the count of them | pack constants: 0.5 mm (caps and grip centres), and the declared diameter + 1.0 mm | `stale_scene`: the baseline layout against a scene with btn_y's cap 3 mm farther up the face; 3.00 mm |

The baseline (`selftest/baseline.json`) is a generic two-handled controller. It
passes at 97.96% of women (btn_y limits) and 99.71% of men, against `>= 95%`. Each
share is floored to 0.01% before it is judged, so a share one subject short of the
limit never rounds onto it: 1703 of 1774 men is 95.99%, a fail against `>= 96`.

**Operating context.** `hand_grip_diameter_mm` from 25 to 50 mm (the cylinders Kong
and Lowe 2005 tested) and `hand_user_age_min_years` from 17. Outside it a pass does
not count (the claim reads Gap, or Assumed under an owned `fallback`) and a fail
still counts. An absent age reads as outside. The context is the survey's validity
regime: the pack has no gate that guards the survey's range, because the spine already
turns "out of the data's range" into Gap. The guard of the reach gate's inputs is
`ergo.layout_agrees`, below. A slab grip is not outside this context: the rule has no
source for it at all, so the gate skips before it reads the diameter. Before a grip
stated its kind, a slab was read through its diameter, outside the context, and its
fail counted.

**It skips** when the projection has no layout, when no control is pressed from a
grip by a thumb or index finger, or when a judged control is pressed from a slab
grip. **It errors** on a layout it cannot judge: a grip with no `kind` or another
kind, a key the gates do not read, a grip id that no grip declares, a zero or
non-finite vector, a handle grip's hand axis more than 20 deg from perpendicular to
its handle, a judged control behind the centre of a grip it is pressed from, along
that grip's hand axis (under the palm; a hand axis declared from the hand to the
wrist, or swapped with the handle axis, puts the controls there), a regrip control
with no reason, or a claim not held as `>=` or `>` in %.

## The layout guard

`ergo.layout_agrees` is the validity guard of `ergo.digit_reach`'s inputs. The reach
gate counts subjects on the layout the model declares; this gate reads the exported
scene and checks four things:

1. **The controls, both ways.** Each declared control is drawn as a part `ctl-<id>`,
   and each drawn `ctl-*` part is declared. A drawn control the layout leaves out is
   never judged for reach.
2. **Each cap's position.** The top of each control's part along its declared
   `normal` lies within 0.5 mm of its declared `at`.
3. **No handle thicker than declared.** At each handle grip, the outer loop of the
   housing's section through the grip centre, square to the handle axis, is no more
   than the declared diameter + 1.0 mm across (perimeter / pi, scaled back for what
   a tessellated loop hides). One-sided: a device with two different handles
   declares the larger, and passes. A grip centre that no section surrounds is a
   disagreement too.
4. **Each handle grip on its handle.** In that section, the declared grip centre
   lies within 0.5 mm of the drawn handle's axis: the area centroid of the outer
   loop. A centre off by 0.5 mm moves every wrist by 0.5 mm, as a cap off by 0.5 mm
   moves its control. Both handles drawn 10 mm back from the controls, with the
   layout not following, read 97.96% of women on the declared grips; the drawn
   design reaches 68.52%.

A slab grip has no handle to measure, and no reach is counted from it. The guard
names it in its detail and runs checks 1 and 2 only. On a slab held at its short
ends the section square to the held edge is the whole device, and its centroid is
the middle of the face: each end grip read 66 mm off "its handle's axis", and no
end grip could pass.

Any missing, extra, too-thick or off-axis item is a structural disagreement: the
verdict is their count against 0. Otherwise it is the worst cap, in mm, against
0.5 mm. On the baseline every cap is 0.00 mm off, both handles read 36.00 mm, and
both grip centres are 0.00 mm off their handles' axes.

Why it matters, on the baseline: with btn_y drawn 3 mm farther up the face than
declared, the reach gate counts 97.96% of women on the declared layout, and the drawn
design reaches 94.33%, under the claim's 95%. A 50 mm handle declared as 36 mm
reads 97.96% where the truth is 83.92%. The registry refuses this gate as a `needs`
prerequisite of the reach gate (a tier-1 guard above a tier-0 analysis), so it
covers the tag `digit-reach` instead: the reach claim waits for both. The inner
loop's `nopekit check` runs tier 0 only, so the reach claim waits until
`nopekit check --tier 1` has run the guard; a tier-0 check then keeps its verdict,
and an edit to the layout or the scene makes it Stale until tier 1 runs again.

**The scene, first match:**

- `hand_layout_scene`: one GLB or glTF file in the device frame, mm. Node transforms
  apply.
- otherwise cad-solid's mesh map (`meshes`, or its aliases `solids` and `parts`, read
  under the scope `ergo.`, then `cad.`, then bare), read and named as cad-solid's
  `assembly` view reads and names it. A project that already publishes cad-solid's
  assembly gets this gate with no extra export. fdm-print reads a bare `parts` as its
  print set, each part in print orientation: with both packs live, publish
  `cad.meshes` or `hand_layout_scene`, or this gate reads the print set as the
  device. `nopekit doctor` names the clash.

**The node names are cad-solid's interface** (`packs/cad-solid/cad_solid_parts.py`).
`gates/_names.py` copies its functions, and a test holds the copy to cad-solid's on
the same names: loaded from cad-solid, its path would enter this gate's code digest
as one machine's path wherever cad-solid is not live. A node is `<part>__<body>`; its
part is the text before `__`, as the site reads it. Each control is the part `ctl-<id>`, so its
first body is the node `ctl-<id>__0`. Every other part is the housing. A part name is
sanitised as cad-solid sanitises it (`ctl-btn y` reads as `ctl-btn_y`).

```
control "btn_y"  ->  part "ctl-btn_y"  ->  node "ctl-btn_y__0"
```

**It skips** with no `hand_controls`, no scene, no part named `ctl-<id>`, or a declared
handle grip and no `hand_grip_diameter_mm`: each is an export not yet made, not a
judgment of the design. **It errors** on a list of controls or grips it cannot read
(for example a missing or repeated id, a grip with no `kind`, a key the gates do not
read, a point that is not three finite numbers, a zero vector, an unknown digit, a
regrip control with no reason, or a handle grip's hand axis more than 20 deg from
perpendicular to its handle), on a control with no `normal`, on two control ids that
name one part after sanitising, and on a housing part that is not a solid and crosses
the plane of a grip's section (cad-solid's `cad.is_volume` says why). The plane is
unbounded, so a broken part far from the handle is refused too: its section could be
part of the loop. The reach gate's other refusals, a control that names a grip no grip
declares and a judged control behind its grip centre, are not this gate's: on those
`ergo.digit_reach` errors, so the reach claim is not Checked.

## The proposer

```
nopekit propose ergo.reach_envelope -- --layout layout.json --pitch 15 --face=-75,75,-40,20
```

`layout.json` holds `hand_grips`, `hand_controls` and `hand_grip_diameter_mm` as the
model publishes them. It writes `reach_map.json`, `design_range.json`,
`grip_spacing.json` (two grips), `fit_counts.json` (with `--pitch`) and
`reach_map.svg` (when matplotlib is importable). Each share on the map is marginal:
the share that reaches that one point. The gate counts every control at once, which
is never more. `--tester-wtt-mm W --tester-wcg-mm G` gives a tester's percentile. A
layout with a slab grip gets `design_range.json` alone, which says why, and needs no
`hand_grip_diameter_mm`: the map, the fit counts and the grip spacing each put a
wrist behind a handle. A proposal settles nothing: record in a decision what you
adopt.

## Keys, units and frames

Read under the scope `ergo` first (`ergo.hand_grips`), then the bare key. Only
`meshes` has aliases, cad-solid's own (`solids`, `parts`), with cad-solid's meaning:
bare words such as `controls` or `grips` collide with other packs, and the two context
keys must be read under the spelling the spine judges.

| Key | What |
|---|---|
| `hand_grips` | `[{id, kind, centre, hand_axis, handle_axis}]`. `kind`, required: `handle` (a fist round a handle, the one grip the reach count has a source for) or `slab` (held flat or at its edges: no count, see Slab grips). `centre`: the point on the handle axis where the middle finger wraps, mm (at a handle grip, `ergo.layout_agrees` holds it within 0.5 mm of the drawn handle's axis). `hand_axis`: wrist to hand with the wrist straight; every judged control pressed from a handle grip lies at or beyond the centre along it. `handle_axis`: the handle's long axis, within 20 deg of perpendicular to the hand axis; a slab grip may leave it out. |
| `hand_controls` | `[{id, at, normal, operated_by, regrip_reason}]`. `at`: the centre of the pressed top surface, mm. `normal`: the direction it is pressed, out of the surface it stands on (only `ergo.layout_agrees` reads it). `operated_by`: `[{grip, digit}]`, digit `thumb`, `index` or `other`. |
| `hand_grip_diameter_mm` | the largest handle's perimeter / pi through its grip centre, mm, over the handle grips. A larger value is conservative, so a device with two handles states the larger. A device with only slab grips states none. |
| `hand_user_age_min_years` | the youngest person you expect to use the device, years. Take the age on its label or listing when it has one. Otherwise take the youngest age of the people it is made for. Say the source in the reach claim's `rationale`. The survey has no one under 17: below 17 a pass does not count and a fail still does. A product that a family shares states its youngest user, not 17. Do not state 17 to get a count. |
| `hand_layout_scene` | the exported scene, a GLB or glTF path in the device frame, nodes `<part>__<body>`. Optional: cad-solid's mesh map serves without it. |
| `meshes` | cad-solid's mesh map, `{part: path}` in the device frame, read when `hand_layout_scene` is absent. Aliases `solids`, `parts`. Empty in the baseline, whose scene is `hand_layout_scene`. |

A control pressed only after a regrip has `operated_by: []` and must say why in
`regrip_reason`; it is listed and not judged. A control pressed only by `other` (a
paddle under the middle finger) is counted and not judged: no survey here measured
that reach. Declare the mirrored `operated_by` for left-handed use, or it is not
judged. A grip or a control with a key not in the table is refused, with the nearest
key named: a key that no gate reads changes nothing, and its author thinks it does.

**A control is a point.** `at` is the centre of the pressed top surface, and the
count judges the control there. A wide cap (a space bar, a long rocker) is judged at
its centre. If it actuates anywhere along its length, that is conservative: its
nearest point is never farther from the wrist than its centre. If it fails at its
centre, the fail stands, even where its near end actuates. The count cannot see the
near end, and REACH-HELD does not clear the fail: it resolves GRIP-POSTURE, and only
REACH's evaluators settle REACH. Move the cap toward the wrist until its centre is in
reach. Or go ahead with the cap where it is: a person records that decision at the
spend, in their own shell (`nopekit export <milestone> --proceed --why "..."`), and the
export record names REACH and its status. An extent is not read, and `extent` is
refused: whether a wide cap actuates at its end is a fact of its switch and its
stabiliser, which no scene shows, and a stated extent would pass on a number that
nothing holds.

## The physics in one paragraph

For each subject the wrist point (the stylion, wrist straight) sits behind the grip
centre along the hand axis, by the subject's wrist-centre of grip length plus half
the difference between the handle and the survey's 31.75 mm dowel. A control is in
reach of the thumb when it lies within the subject's wrist-thumbtip length of that
point, and of the index finger within the wrist-index finger length. This is an
approximate full-extension envelope. The thumb pad presses a few mm short of the tip,
which makes it generous. The thumb's base joint sits off the stylion's line, which can
make it 1 to 3 mm short. Both errors are smaller than the 5.6 to 5.8 mm spread of
reach beyond the grip in each sex. The testers check a control within a few mm of
the edge: a miss in their hands fails REACH-HELD, and a press does not clear a fail of
the count. Typical numbers: reach beyond the grip (wrist-thumbtip minus wrist-centre
of grip) is 42 mm at the women's P5 and 64 mm at the men's P95; a face button 100 mm
from the wrist is easy, one at 120 mm loses many women.

## Tag vocabulary

| Tag | Use it for |
|---|---|
| `digit-reach` | a claim that a share of a population reaches every control from its grip; both gates cover it |
| `control-layout` | a claim that the declared layout is the drawn design; `ergo.layout_agrees` covers it |

Tag the reach claim `digit-reach` and nothing broader. A claim tagged `ergonomics`
would collect evidence it does not assert.

## Claims and milestones to start from

`scaffold/` holds record files to copy into a project. `nopekit packs list --verbose`
prints each pack's directory on the line under its name; call this one `<pack>`.
A record's id is its file name, and a plain `cp` overwrites a file of that name
without a word. So list both sides first, and copy with `-i`, which asks before it
overwrites:

```sh
ls <pack>/scaffold/claims/ <pack>/scaffold/milestones/ claims/ milestones/
cp -i <pack>/scaffold/claims/*.json claims/
cp -i <pack>/scaffold/milestones/*.json milestones/
```

Answer no to each clash. Copy that file under a new name, and use the new id where
another record names it: a milestone's `requires` and REACH-HELD's `resolves`.

- `claims/REACH.json`, measurable: at least 95% of each sex reach every control.
- `claims/GRIP-POSTURE.json`, an assumption: the hands hold the grips as declared.
  Add an `owner`, the person who holds the assumption. It reads Gap until that person
  runs `nopekit claim physical GRIP-POSTURE assume` in their own shell. Leave
  `mechanism.by` out (or `none`): `by: human` makes the claim an expert judgment, whose
  person is its `authority`, and the store then refuses an `owner`.
- `claims/REACH-HELD.json`, physical, `resolves` GRIP-POSTURE: two testers press every
  control on the first full print without a regrip. The protocol is in
  `skills/reach-testing/SKILL.md`.
- `milestones/reach-first-print.json` requires REACH. `milestones/reach-board-order.json`
  requires REACH and REACH-HELD. They hold the reach claims only: add the board's own
  claims to the board order before you spend.

**On a slab device**, copy GRIP-POSTURE and REACH-HELD and leave REACH out: no count
is made from a slab grip, so REACH would read Skipped for as long as it stands. In
GRIP-POSTURE's statement, write "edge" for "handle". Write its `rationale` again too,
before its owner records it (the record binds to the rationale it read): no wrist sits
behind a grip in a count, so say what the marks are for. For example: "the testers
hold the print at the marked grips; a hand that holds the edge elsewhere moves every
reach with it". Take REACH out of both milestones. The board order then requires
REACH-HELD. The first print requires no claim of this pack: put the shell's own claims
in it, or leave it out and record REACH-HELD on the design
(`skills/reach-testing/SKILL.md`, step 2).

With REACH left out, no claim carries a tag that `ergo.layout_agrees` covers, so its
fail moves no status. The testers press the controls that the layout declares. To hold
those to the drawn ones, add this claim as `claims/LAYOUT.json`, and put it in the
board order beside REACH-HELD. Tag it `control-layout` only: `digit-reach` brings in
the reach count, which skips on a slab. The guard runs at tier 1, so the claim waits
until `nopekit check --tier 1` has run it.

```json
{"statement": "Every drawn control is declared, every declared control is drawn, and each cap is within 0.5 mm of its declared position",
 "kind": "measurable",
 "acceptance": {"quantity": "declared layout agreement", "comparator": "<=", "limit": 0.5, "units": "mm"},
 "rationale": "the testers press the controls that the layout declares, so a drawn control that the layout leaves out is never pressed",
 "tags": ["control-layout"],
 "mechanism": {"how": "ergo.layout_agrees reads the exported scene: each declared control is drawn as part ctl-<id>, each drawn ctl-* part is declared, and each cap top lies within 0.5 mm of its declared at",
               "evaluators": ["ergo.layout_agrees"]}}
```

## Common failure modes

- **A pass on one builder's hand.** The control field fits the person who drew it.
  The verdict names the limiting control; the women's share is usually the low one.
- **Percentiles added.** "P5 thumb minus P5 grip" is 48 mm for women; the true P5 of
  the difference is 42 mm. The gate never does this. Do not do it in prose.
- **An understated handle.** A 50 mm handle declared as 36 mm reads 97.96% of women
  on the baseline; the truth is 83.92%. State the largest handle.
- **Errored, not failing.** A hand axis 30 deg off square is a refusal: fix the
  declared axes, do not loosen the claim. So is a hand axis that points from the hand
  to the wrist: it puts every wrist in front of its grip, and the gate refuses it
  rather than count every subject in reach.
- **A slab declared as a handle.** A device held flat or at its edges, declared
  `kind: handle` with its own perimeter / pi as the diameter, puts every wrist far
  behind its grip, and the count fails. Outside 25 to 50 mm that fail still counts.
  `ergo.layout_agrees` shows it too: each end grip lies far off its section's
  centroid. Declare `kind: slab` and leave REACH out: REACH-HELD settles reach. Do
  not declare a real handle a slab to escape a fail: REACH then reads Skipped for as
  long as it stands. REACH-HELD does not settle it, and a milestone that requires
  REACH is not ready.
- **A stale layout.** The layout file moved and the CAD did not, or the reverse.
  `ergo.layout_agrees` names the control and its node, or the grip whose handle
  moved. Move the control in the model,
  export again, and let both gates run; do not edit the layout file alone.
- **A reach claim that reads Skipped.** Look at each gate's reason. From
  `ergo.layout_agrees`: no scene, no `ctl-<id>` part, or no mesh library. Name the
  control parts, publish the scene or install the libraries. Do not drop the tag.
  From `ergo.digit_reach`, "no count on a slab grip": correct for a slab device. Its
  reach is REACH-HELD's, and REACH does not belong in it.

## Open questions

- Is 20 deg the right tolerance between the hand axis and the handle axis? A natural
  power grip lies oblique across the palm, and a refusal reads louder than a fail.
- Worst sex (as now) or a pooled share at a mix the person states?
- A slab-grip anchor, from the 2014 functional-area model, once its own worked
  example is reproduced (`references/reach-model.md`).
- Should `ergo.layout_agrees` light up the disagreeing control on cad-solid's
  `assembly` view? A locator to another pack's view dangles where cad-solid is not
  live, so it names the node in its detail and attaches none.

## Where to look next

- `references/anthropometry.md`: the datasets, the definitions with their pages, the
  percentile pin, the cross-check with ANSUR II.
- `references/reach-model.md`: the anchor, the girth term, the error either way, slab
  grips.
- `references/what-slipped-through.md`: why this pack exists.
- `skills/reach-testing/SKILL.md`: how to measure a tester, run the test and record it.
- `gates/data/PROVENANCE.json`: URLs, hashes and the derivation of the shipped table.
