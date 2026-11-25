# The reach model

Tier 3. Read this before you trust a number near the edge, or before you change the
rule.

---

## The anchor

For subject `i` holding grip `g` of kind `handle`, the wrist point (the stylion, wrist
straight) is

    S_ig = G_g - (WCG_i + (D - 31.75) / 2) * u_g

- `G_g`: the grip centre, the point on the handle axis where the middle finger wraps.
- `u_g`: the unit hand axis, from wrist to hand with the wrist straight.
- `WCG_i`: the subject's wrist-centre of grip length, measured on a 31.75 mm dowel.
- `D`: the handle diameter (`hand_grip_diameter_mm`).

A control at `P` is in reach when `|P - S_ig| <= WTT_i` for a thumb, or
`|P - S_ig| <= WIF_i` for an index finger. A subject is accommodated when every judged
control is in reach of one of the digits that presses it. The result is the share of
the worse sex, floored to 0.01%: every claim holds it as `>=` or `>`, so the floor
rounds toward the fail, and a share one subject short of the limit never rounds onto
it.

The survey measured wrist-centre of grip length with the dowel square to the fist and
the fist in line with the forearm. So the gate refuses a handle grip whose hand axis is
more than 20 deg from perpendicular to its handle axis. No survey measured the
obliquity of a power grip, so 20 deg is the pack's, and an open question. A slab grip
is not asked it: the anchor is never applied to one (Slab grips, below).

## The girth term

On a thicker handle the palm wraps a larger surface, and the handle's centre is
farther from the wrist. The term `(D - 31.75) / 2` moves the centre of grip by the
radius difference. It is this pack's own model, not a survey result. Kong and Lowe
(2005) measured power grips on cylinders of 25 to 50 mm, so the operating context
holds the gate to that range.

It matters. On the baseline with 50 mm handles, the term gives 83.92% of women; with
no term (every handle read as the dowel) the count is 99.14%, and with the handles
declared as 36 mm it is 97.96%. A device with two different handles states the larger
diameter: that puts every wrist farther back, which is conservative.

`ergo.layout_agrees` holds the declared diameter to the drawn handles: at each handle
grip, the outer loop of the housing's section through the grip centre, square to the handle
axis, is at most the declared diameter + 1.0 mm across (perimeter / pi, scaled back
for what a tessellated loop hides). One millimetre of diameter moves every wrist point
0.5 mm, the same as the gate's tolerance on a cap. The check is one-sided, so a larger
declared diameter passes: it is the conservative side of this term. The same section
holds the grip centre to the drawn handle's axis (its area centroid) within 0.5 mm,
because the wrist sits behind the centre and moves with it.

## The error, either way

Wrist-thumbtip length is measured with the thumb straight and in line with the
forearm (TR89-044 p. 332). The envelope is a sphere of that radius about the stylion:
an approximate full-extension bound.

- **Generous** by a few mm: the pad of the thumb presses a cap, and the pad is short
  of the tip.
- **Short** by about 1 to 3 mm: the thumb's base joint is not on the stylion's line,
  so the true reach can pass the sphere.
- **Short** on a wide cap that actuates along its length: a control is a point, judged
  at the centre of its pressed surface, and the cap's nearest point is closer. An
  extent is not read (PACK.md, "A control is a point").
- **Generous** in direction: the sphere does not know the palm, the other fingers or
  the joint limits. A point behind the hand is "in reach" by the sphere and not by
  the thumb. So the gate refuses a judged control that lies behind the centre of a
  grip it is pressed from, along that grip's hand axis, and the proposer cuts its fit
  counts to the distal side of the grip. The same refusal catches a hand axis
  declared from the hand to the wrist, or swapped with the handle axis: the 20 deg
  test cannot see the axis's sign, and a reversed axis puts every wrist in front of
  its grip, where the known-bad cluster read 100% in reach.

Both length errors are smaller than the spread of reach beyond the grip in each sex
(SD 5.6 to 5.8 mm). The testers check a control within a few mm of the edge for the
limiting subjects: a miss in their hands fails REACH-HELD, and a press does not clear
a fail of the count. **Rejected:** a slack added to the radius, or a tighter rule from
a thumb-pad or base-joint model. No public survey measures either offset, and a
guessed correction launders an assumption into a number.

## Controls that are not judged

- **Regrip** (`operated_by: []`, with `regrip_reason`): pressed after the hand moves,
  such as a power key pressed once per session. Listed, not judged. Rejected: judging
  every control (a sound power key would fail the design).
- **Other digit** (`digit: other`): a paddle under the middle finger. Counted, not
  judged: no survey here measured that reach. Rejected: an error (a sound design with
  back paddles would read errored).

## What the operating context does

`hand_grip_diameter_mm` in [25, 50] and `hand_user_age_min_years` at 17 or more. The
spine judges the context on the spelling the gate read, so neither key has an alias.
Outside it a pass does not count: the claim reads Gap, or Assumed when it carries an
owned `fallback`. A fail still counts. Rejected: a guard gate (the spine already does
this, with no extra gate); a fail outside (it would say the design is bad when only
the data does not apply).

The context is for a handle whose rule applies and whose data is extrapolated, as on
a 60 mm handle. A slab grip is not outside it: the rule does not apply at all, and
the gate skips before it reads the diameter (Slab grips, below).

## Slab grips

A phone, an e-reader, a flat remote or a board held at its short ends is held flat or
at its edges, not in a fist round a handle. The anchor above does not apply: the
survey measured wrist-centre of grip length on a dowel in a closed fist, and no public
survey measures where the wrist sits behind a slab. So each grip states its kind,
`kind: handle` or `kind: slab`. On a control pressed from a slab grip,
`ergo.digit_reach` makes no count. It skips, names the slab grips and names the
physical claim, and the reach claim reads Skipped.

Why a skip, and not the operating context. Outside the context a pass does not count,
and a fail still counts. That is right where the rule applies. On a slab the rule does
not apply, so a fail is no more the design's than a pass. What slipped through while
the context stood in for a kind: a handheld terminal and a game console board, each a
slab held at its short ends, stated the slab's perimeter / pi as the handle diameter
(95 to 134 mm), as PACK.md asked. The girth term put each wrist 32 to 51 mm behind its
grip. The count read 0 to 80%, the fail counted, and the reach claim read Failing,
where this page had said the count does not apply. `ergo.layout_agrees` failed both
devices too:
the section square to the held edge is the whole device, and each end grip read 66 mm
off its centroid.

What a slab device gets:

- **The physical test.** REACH-HELD in `scaffold/`: two testers, chosen by reach
  beyond the grip, press every control on the first full print without a regrip
  (`skills/reach-testing/SKILL.md`). A person records it. This is the reach claim of
  a slab device; REACH, the count, is left out.
- **The layout guard, through a claim of its own.** `ergo.layout_agrees` covers the
  tags `digit-reach` and `control-layout`, and REACH was the claim that carried one.
  With REACH left out, the guard runs as a row, covers no claim, and its fail moves
  no status. A claim tagged `control-layout` (PACK.md gives it) makes it count: it
  holds every control the testers press, and every cap, to the scene. It measures no
  section at a slab grip.
- **The testers' numbers.** The proposer writes `design_range.json` alone: the design
  ranges, the testers' thresholds and a tester's percentile.

A slab device states no `hand_grip_diameter_mm`. Rejected: a slab detected from the
scene (the reach gate is tier 0 and reads no scene, and a threshold on a section's
shape guesses what the person meant); a default of `handle` (a person who never read
the kinds gets the false fail back).

The candidate slab model is the functional-area model of Bergstrom-Lehtovirta and
Oulasvirta (2014), a parabola fitted to the thumb's reach on a tablet. It is not
shipped, for three reasons found when it was read:

- The paper's text puts the vertex at -16 mm, and its own vertex form puts it at
  +16 mm. Its 57.9 mm worked example could not be reproduced with either sign.
- It was fitted on 20 people and one 7.2 mm tablet, with the index finger flat on the
  back. A thickness rule for other devices is one the authors say needs user data.
- No public survey measures the span it needs, so its population would be the 20
  people or a scaling of ANSUR that nobody measured.

Until its worked example is reproduced, a slab grip gets no count, and the testers
settle its reach.

## Sources

- Gordon CC et al. 1989, NATICK/TR-89/044 (definitions (125), (129), (130), Table 1).
- Robinette KM, McConville JT 1981, *An Alternative to Percentile Models*, SAE 810217.
- Kong YK, Lowe BD 2005, *Optimal cylindrical handle diameter for grip force tasks*,
  Int J Ind Ergon 35(6):495-507 (25-50 mm handles, 24 subjects).
- Bergstrom-Lehtovirta J, Oulasvirta A 2014, *Modeling the functional area of the
  thumb on mobile touchscreen surfaces*, CHI 2014 (the v0.2 candidate, not used).
