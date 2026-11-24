# The reach model

Tier 3. Read this before you trust a number near the edge, or before you change the
rule.

---

## The anchor

For subject `i` holding grip `g`, the wrist point (the stylion, wrist straight) is

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
the fist in line with the forearm. So the gate refuses a grip whose hand axis is more
than 20 deg from perpendicular to its handle axis. No survey measured the obliquity of
a power grip, so 20 deg is the pack's, and an open question.

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

`ergo.layout_agrees` holds the declared diameter to the drawn handles: at each grip,
the outer loop of the housing's section through the grip centre, square to the handle
axis, is at most the declared diameter + 1.0 mm across (perimeter / pi). One
millimetre of diameter moves every wrist point 0.5 mm, the same as the gate's
tolerance on a cap. The check is one-sided, so a larger declared diameter passes: it
is the conservative side of this term.

## The error, either way

Wrist-thumbtip length is measured with the thumb straight and in line with the
forearm (TR89-044 p. 332). The envelope is a sphere of that radius about the stylion:
an approximate full-extension bound.

- **Generous** by a few mm: the pad of the thumb presses a cap, and the pad is short
  of the tip.
- **Short** by about 1 to 3 mm: the thumb's base joint is not on the stylion's line,
  so the true reach can pass the sphere.
- **Generous** in direction: the sphere does not know the palm, the other fingers or
  the joint limits. A point behind the hand is "in reach" by the sphere and not by
  the thumb. So the gate refuses a judged control that lies behind the centre of a
  grip it is pressed from, along that grip's hand axis, and the proposer cuts its fit
  counts to the distal side of the grip. The same refusal catches a hand axis
  declared from the hand to the wrist, or swapped with the handle axis: the 20 deg
  test cannot see the axis's sign, and a reversed axis puts every wrist in front of
  its grip, where the known-bad cluster read 100% in reach.

Both length errors are smaller than the spread of reach beyond the grip in each sex
(SD 5.6 to 5.8 mm). A control within a few mm of the edge for the limiting subjects is
the testers' to settle, not the screen's. **Rejected:** a slack added to the radius,
or a tighter rule from a thumb-pad or base-joint model. No public survey measures
either offset, and a guessed correction launders an assumption into a number.

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

## The slab-grip gap

A phone, an e-reader or a flat remote is held flat in the palm, not around a handle.
The anchor above does not apply, and the gate reads Gap. The candidate for v0.2 is the
functional-area model of Bergstrom-Lehtovirta and Oulasvirta (2014), a parabola fitted
to the thumb's reach on a tablet. It is not shipped, for three reasons found when it
was read:

- The paper's text puts the vertex at -16 mm, and its own vertex form puts it at
  +16 mm. Its 57.9 mm worked example could not be reproduced with either sign.
- It was fitted on 20 people and one 7.2 mm tablet, with the index finger flat on the
  back. A thickness rule for other devices is one the authors say needs user data.
- No public survey measures the span it needs, so its population would be the 20
  people or a scaling of ANSUR that nobody measured.

Until its worked example is reproduced, the honest answer for a slab grip is Gap, an
owned fallback, and the physical claim.

## Sources

- Gordon CC et al. 1989, NATICK/TR-89/044 (definitions (125), (129), (130), Table 1).
- Robinette KM, McConville JT 1981, *An Alternative to Percentile Models*, SAE 810217.
- Kong YK, Lowe BD 2005, *Optimal cylindrical handle diameter for grip force tasks*,
  Int J Ind Ergon 35(6):495-507 (25-50 mm handles, 24 subjects).
- Bergstrom-Lehtovirta J, Oulasvirta A 2014, *Modeling the functional area of the
  thumb on mobile touchscreen surfaces*, CHI 2014 (the v0.2 candidate, not used).
