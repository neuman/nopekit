# hand-ergonomics

This pack judges **the device as the user holds it, in the device frame**: x to the
right as the user sees the face, y toward the top edge, z out of the face toward the
user, all in mm. The model declares where each hand grips the device and where each
control sits. `ergo.digit_reach` counts the people in a measured population whose
thumb or index finger reaches every control pressed from a grip. The population is
ANSUR 1988: 2,208 women and 1,774 men of the US Army, aged 17 to 51, measured one by
one. The pack ships their hand lengths, subject by subject, in `gates/data/`.

## What this pack settles

- **Reach.** Can the claim's share of each sex press every control without a regrip?
  The answer is a count of subjects, never a sum of percentiles.
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
- **Slab grips.** A phone, an e-reader or a flat remote is not a power grip on a
  handle. Handles under 25 mm or over 50 mm read Gap, never a fail.
- **Children.** The survey's youngest subject is 17. Users younger than that read Gap.
- **Older users' joint range, other populations, gloves, force, fatigue, drop risk.**
  The population is the US Army of 1988. A market with smaller hands sets a higher
  limit or brings another dataset.
- **Whether the layout is the drawn design.** The gate judges the layout the model
  declares. A layout one revision behind the CAD is judged as declared.

## Gates

| Gate | Tier | Measures | Limit from | Known-bad control |
|---|---|---|---|---|
| `ergo.digit_reach` | 0, stdlib | worst-sex share of subjects who reach every judged control, % | the claim tagged `digit-reach` (`>=` or `>`, in %) | `face_cluster_out_of_reach`: the four right-hand face buttons 10 mm up the face; 63.09% of women, 84.39% of men |

The baseline (`selftest/baseline.json`) is a generic two-handled controller. It
passes at 97.96% of women (btn_y limits) and 99.72% of men, against `>= 95%`.

**Operating context.** `hand_grip_diameter_mm` from 25 to 50 mm (the cylinders Kong
and Lowe 2005 tested) and `hand_user_age_min_years` from 17. Outside it a pass does
not count (the claim reads Gap, or Assumed under an owned `fallback`) and a fail
still counts. An absent age reads as outside. The context is the survey's validity
regime: the pack has no separate guard gate, because the spine already turns "out of
the data's range" into Gap.

**It skips** when the projection has no layout, or when no control is pressed from a
grip by a thumb or index finger. **It errors** on a layout it cannot judge: a grip
id that no grip declares, a zero or non-finite vector, a hand axis more than 20 deg
from perpendicular to its handle, a regrip control with no reason, or a claim not
held as `>=` or `>` in %.

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
proposal settles nothing: record in a decision what you adopt.

## Keys, units and frames

Read under the scope `ergo` first (`ergo.hand_grips`), then the bare key. No key has
an alias: bare words such as `controls` or `grips` collide with other packs, and the
two context keys must be read under the spelling the spine judges.

| Key | What |
|---|---|
| `hand_grips` | `[{id, centre, hand_axis, handle_axis}]`. `centre`: the point on the handle axis where the middle finger wraps, mm. `hand_axis`: wrist to hand with the wrist straight. `handle_axis`: the handle's long axis. Within 20 deg of perpendicular. |
| `hand_controls` | `[{id, at, operated_by, regrip_reason}]`. `at`: the centre of the pressed top surface, mm. `operated_by`: `[{grip, digit}]`, digit `thumb`, `index` or `other`. |
| `hand_grip_diameter_mm` | the largest handle's perimeter / pi through its grip centre, mm. A larger value is conservative, so a device with two handles states the larger. |
| `hand_user_age_min_years` | the youngest intended user, years. |

A control pressed only after a regrip has `operated_by: []` and must say why in
`regrip_reason`; it is listed and not judged. A control pressed only by `other` (a
paddle under the middle finger) is counted and not judged: no survey here measured
that reach. Declare the mirrored `operated_by` for left-handed use, or it is not
judged.

## The physics in one paragraph

For each subject the wrist point (the stylion, wrist straight) sits behind the grip
centre along the hand axis, by the subject's wrist-centre of grip length plus half
the difference between the handle and the survey's 31.75 mm dowel. A control is in
reach of the thumb when it lies within the subject's wrist-thumbtip length of that
point, and of the index finger within the wrist-index finger length. This is an
approximate full-extension envelope. The thumb pad presses a few mm short of the tip,
which makes it generous. The thumb's base joint sits off the stylion's line, which can
make it 1 to 3 mm short. Both errors are smaller than the 5.6 to 5.8 mm spread of
reach beyond the grip in each sex. A control within a few mm of the edge is the
testers' to settle. Typical numbers: reach beyond the grip (wrist-thumbtip minus
wrist-centre of grip) is 42 mm at the women's P5 and 64 mm at the men's P95; a face
button 100 mm from the wrist is easy, one at 120 mm loses many women.

## Tag vocabulary

| Tag | Use it for |
|---|---|
| `digit-reach` | a claim that a share of a population reaches every control from its grip |

Tag the reach claim `digit-reach` and nothing broader. A claim tagged `ergonomics`
would collect evidence it does not assert.

## Claims and milestones to start from

`scaffold/` holds record files to copy into a project:

- `claims/REACH.json`, measurable: at least 95% of each sex reach every control.
- `claims/GRIP-POSTURE.json`, an assumption: the hands hold the handles as declared.
  Add an `owner`; it reads Gap until that person runs
  `nopekit claim physical GRIP-POSTURE assume` in their own shell.
- `claims/REACH-HELD.json`, physical, `resolves` GRIP-POSTURE: two testers press every
  control on the first full print without a regrip. The protocol is in
  `skills/reach-testing/SKILL.md`.
- `milestones/reach-first-print.json` requires REACH. `milestones/reach-board-order.json`
  requires REACH and REACH-HELD. They hold the reach claims only: add the board's own
  claims to the board order before you spend.

## Common failure modes

- **A pass on one builder's hand.** The control field fits the person who drew it.
  The verdict names the limiting control; the women's share is usually the low one.
- **Percentiles added.** "P5 thumb minus P5 grip" is 48 mm for women; the true P5 of
  the difference is 42 mm. The gate never does this. Do not do it in prose.
- **An understated handle.** A 50 mm handle declared as 36 mm reads 97.96% of women
  on the baseline; the truth is 83.92%. State the largest handle.
- **Errored, not failing.** A hand axis 30 deg off square is a refusal: fix the
  declared axes, do not loosen the claim.
- **Gap on a phone-shaped device.** Correct. Carry it with an owned `fallback` and the
  physical claim.

## Open questions

- Is 20 deg the right tolerance between the hand axis and the handle axis? A natural
  power grip lies oblique across the palm, and a refusal reads louder than a fail.
- Worst sex (as now) or a pooled share at a mix the person states?
- A slab-grip anchor for v0.2, from the 2014 functional-area model, once its own
  worked example is reproduced (`references/reach-model.md`).

## Where to look next

- `references/anthropometry.md`: the datasets, the definitions with their pages, the
  percentile pin, the cross-check with ANSUR II.
- `references/reach-model.md`: the anchor, the girth term, the error either way, the
  slab-grip gap.
- `references/what-slipped-through.md`: why this pack exists.
- `skills/reach-testing/SKILL.md`: how to measure a tester, run the test and record it.
- `gates/data/PROVENANCE.json`: URLs, hashes and the derivation of the shipped table.
