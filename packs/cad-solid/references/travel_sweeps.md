# Sweeping a mechanism through its travel

Tier 3. Read this before running `cad.clash` on anything that moves.

---

## The problem in one sentence

`cad.clash` checks the pose you exported, and the pose you exported is the one the
CAD system happened to be in — which is almost never the one that jams.

A hinge that clears at 0° and clears at 90° can foul at 40°, where a boss passes a
rib. A slide that clears at rest can bottom out on a screw head 3 mm before its hard
stop. A single-pose clash check on a mechanism is not a weaker check than a sweep;
it is a different check that answers a question nobody asked.

## Export poses, not a pose

The model owns the mechanism's degrees of freedom, so the model owns the sweep. What
the geometry pack needs is a set of placed exports, one per pose, each a complete
assembly in the assembly frame.

A minimum set for one degree of freedom:

| Pose | Why |
|---|---|
| Both hard stops | The extremes; what the design claims to reach |
| **Past** each hard stop, by the over-travel a hand can apply | What it actually reaches. Hard stops are made of plastic |
| Even steps across the range | The interference in the middle that neither extreme shows |
| The assembly pose | Often outside the operating range entirely, and the one that has to work exactly once |
| The loading pose | A dock, cradle or clamp at its widest opening, with the largest item it must take at its capture feature, tilted along its insertion path. The item fits when drawn in place; this is where it hits the frame on the way in |
| Any latched, folded or stowed state | A separate configuration, not a pose on the same axis |

Two degrees of freedom need the **grid**, not the two axes separately. A part that
clears at every rotation with the slide in and at every slide position with the
rotation at zero can still foul at 30° and half-extended. Combinatorics hurt fast;
choose the step sizes deliberately and say which combinations were never checked.

## How fine is fine enough?

A sampled sweep is a sampled proof. The interference it can hide is bounded by the
step: at 5° steps you can step straight over a 2° window where a corner passes a
corner.

Three ways to choose the step, in increasing confidence:

1. **By feature size.** The smallest feature that can interfere, divided by the
   distance a point on the moving part travels per step. If a 1 mm rib is on a
   50 mm radius, 5° moves 4.4 mm — it can jump the rib entirely. Step down until a
   step moves less than the smallest feature.
2. **By curvature of the contact.** Where two faces approach nearly parallel, the
   gap changes slowly and coarse steps are fine. Where a corner passes a corner, the
   gap changes fast and the window is narrow. Refine locally around the poses where
   the measured gap is smallest.
3. **By swept volume.** Sweep the moving part into a single solid and clash *that*
   against the static parts. One check, no sampling gap, and it answers "can this
   motion ever interfere" rather than "does it interfere at these 19 poses". It is
   the only version that is a proof. It costs more to build and it over-reports:
   the swept volume includes space the part occupies at different times from the
   part it is being compared against, so a genuine clearance between two parts that
   move together will show as interference. Use it to find candidates, then confirm
   at poses.

## Recording the result honestly

A sweep verdict must say what it swept. Three numbers and they all matter:

- the **range** covered, and whether it includes over-travel;
- the **step**, and therefore the size of window that could have been missed;
- the **poses not checked at all** — the second degree of freedom held fixed, the
  stowed configuration, the assembly pose.

"Clash-free across 0–90° in 2° steps, over-travel to 95°, second axis held at
nominal" is a claim. "No clashes" is not.

## What moves that you did not list

The degrees of freedom in the CAD model are the ones somebody built. Also moving:

- **Anything floating in a clearance.** A part located by clearance holes occupies
  the worst corner of that clearance, and a different corner after vibration. Check
  the extremes of the float, not the drawn position.
- **Compliant parts.** Springs, foams, gaskets, flexures, adhesive gaps. Their
  as-drawn geometry is usually their free state, which is a state they are never in.
- **Cables and looms.** The drawn route is one of many. Check the slack, and check
  what happens when someone pushes the excess back into the enclosure.
- **Parts under load.** A cantilever deflects toward whatever it was clearing. A
  clearance that is arithmetically positive at rest and zero under load does not
  exist, and no sweep at nominal geometry will ever find it.

## Never allowlist a sliding fit

It bears repeating here because this is the file about motion: `cad.clash` refuses
an allowlist entry for any pair listed in `sliding_fits`, and the refusal fails the
gate rather than being ignored.

An intended *contact* — a press fit, a crushed gasket, a snap in its engaged state
— is a static, described, one-off condition, and allowlisting it with a stated
reason is reasonable. An intended *motion* is not a condition, it is a range, and a
boolean at one pose cannot tell "these surfaces slide on each other" from "these
surfaces jam at 40°". Allowlisting the sliding pair switches off the check on the
exact pair whose failure mode is the one this whole file is about.
