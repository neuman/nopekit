# Adversarial review lenses — structural, closed form

Run these **before** anything is built or ordered. On a small bracket spec, a
pass along these lines can find a hole too close to an edge once its position
tolerance is applied, a stress check against yield where the load cycles and
fatigue governs, and a slot that lets a bolt slide so that the bolt is in shear.
On paper, each costs a line in the spec. After the build, each costs the part.

Each heading is one dimension. Attack from one at a time; a reviewer wearing all
seven lenses at once wears none.

---

## 1. Load path

Follow the force from where it enters to where it leaves, out loud, naming every
part it passes through.

- Where does the load actually enter — at a point, over a pad, through a lip?
- What is the **last** thing it goes through before ground? That is usually the
  part nobody analysed.
- Is there a second path that will carry load whether you designed for it or not
  (a cover that becomes a shear web, a cable that becomes a tie)?
- What is the **rated** load, and what is the load when someone leans on it,
  drops it, or uses it as a handle? Which one did you analyse?
- Is there an impact or inertial multiplier? A 3 kg mass caught at the end of a
  30 mm fall is not a 30 N static load.
- Does anything preload the structure before the service load arrives —
  interference fit, bolt torque, a warped panel pulled flat on assembly?

## 2. The real boundary condition

The single highest-yield lens, because the coefficient is where the factor-of-four
errors live.

- That end you called "fixed" — is it? A bolt in a **slot** is a pin. A single
  bolt through a thin ear is a pin with a spring. A cantilever bolted to a panel
  is only as fixed as the panel is stiff.
- If it is genuinely built in, what is it built into, and does *that* deflect?
  Root rotation adds directly to tip deflection and is invisible in the formula.
- Did the span get measured from the restraint or from the drawing edge? Where
  does contact really begin — at the fastener, or at the face?
- For a column: what is the unbraced length? Is there a brace that only exists
  when a removable panel is in place?
- What holds it laterally? A deep thin beam with no lateral restraint fails by
  rolling over sideways long before the bending number here says anything.
- Ask the uncomfortable version: **if this joint were a hinge instead, what would
  the answer be?** If the two answers differ by more than the safety factor, the
  joint is the design, not the beam.

## 3. Stress concentrations

Every stress this pack reports is nominal. The part fails at the feature.

- Where are the holes, and how close are they to the peak-moment station? A hole
  at the root of a cantilever is in the worst possible place.
- Inside corners: is there a radius, and is it a **real** radius or a modelled
  one that the tool cannot cut and the printer will bridge?
- Section changes: every step is a concentration. Is the transition tapered?
- Threads, keyways, snap-ring grooves, engraved text, part numbers stamped on a
  tension face.
- On a printed part: where does the infill change density, where do walls meet,
  and where does a support interface leave a rough surface — on the tension side?
- Rule of thumb to argue with: a well-radiused hole is ~2.2x, a sharp inside
  corner is 3x and upward without limit as the radius goes to zero. If the
  utilisation is 0.5 and there is a sharp corner at the peak fibre, you are at 1.5.

## 4. Fatigue vs static

Everything in this pack is static.

- How many cycles in the life of the product? 10^3, 10^6, 10^8?
- Is the load fully reversing, or pulsating from zero? Reversing is far worse.
- Aluminium and most polymers have **no endurance limit**: there is no stress low
  enough to be safe forever, only a stress low enough for the life you need.
- Where is the highest *alternating* stress, which is not always where the
  highest peak stress is?
- Is there fretting anywhere — a joint that micro-slips every cycle?
- Does anything relax? Bolt preload, a printed polymer under sustained load
  (creep), a spring detent.
- Surface finish and residual stress: a fatigue crack starts at the surface, so
  as-machined versus polished versus as-printed is a real multiplier on life.

## 5. Off-axis and eccentric loading

The formulae assume the load runs neatly through the section's principal plane.

- Does the load line pass through the **shear centre**? An open section (channel,
  angle, C) loaded off it twists as well as bends, and the twist is not in any
  number here.
- Is there an eccentricity that turns a pure axial load into bending? A strut
  bolted off-centre carries `P*e` of moment for free — and a column that is
  merely 1% bowed has that eccentricity built in.
- What happens if the load arrives at 30 degrees instead of straight down —
  someone pulling sideways on a shelf, a cable routed off-plan?
- Is there torsion? A closed tube takes torsion well; an open section of the same
  weight takes essentially none.
- For a printed part: which way do the layers run relative to the **principal
  tension**, and does that stay true if someone re-orients it to save support?

## 6. The joint, not the span

The span is the easy part. Nearly everything that breaks, breaks at a joint.

- Bearing, shear-out, tear-out, net section, bolt shear — this pack checks
  exactly one of those five.
- Edge distance: is it at least 2x the bolt diameter to the free edge? Below
  that, the material tears out and the bearing number is irrelevant.
- Does load really share between fasteners? A stiff plate on four bolts loads the
  outer two hardest, and "divide by four" is optimistic.
- Is there prying? A bolt near a flange edge can see several times the applied
  load once the flange lifts.
- Preload: torqued to what, checked how, and what does it do over temperature?
- Thread engagement in the parent material — especially a tapped hole in a
  polymer or a thin plate, where the threads strip long before the bolt cares.
- **What is the failure mode you would prefer?** A joint that yields visibly is
  better than a beam that snaps. Design where it breaks first, deliberately.

## 7. Units and sign convention

The cheapest lens and the one nobody runs, because the numbers all look fine.

- **Is everything in mm, N and MPa?** A model in metres and pascals passes every
  utilisation, L/delta and L/h check in this pack unchanged, because all three are
  scale-invariant. `beam.input_sanity` bands the modulus, the stresses, the span
  and the E/yield ratio for exactly this reason — but it cannot see a load in
  kilonewtons, and neither can you from the verdict line.
- **Does any load carry a sign?** Downward-negative is an ordinary convention. It
  is refused here rather than absolute-valued, because on an axial load a minus
  sign plausibly means tension instead, and buckling does not apply to a member in
  tension at all.
- **Is `height_mm` the depth in the bending direction?** Transposing it with
  `width_mm` changes I by (b/h)^2 and the arithmetic stays perfectly correct.
- **Is `load_n` the total or the intensity?** For the `*_udl` cases this pack
  wants the whole distributed load W = w*L. The error is exactly a factor of L,
  which is the hardest size of error to notice.
- **Where did the modulus come from — a cert, a published typical, or a table in
  a validator?** Only the first is evidence. See `sourcing.md`.

---

## Using these with the gates

A lens that changes a number is worth more than a gate that confirms one. Run the
review, write down what it moved and **what it rejected and why** (method rule 3
— the rejected alternative is the field that stops the next agent re-litigating
it), then run the tier-0 sweep. Both cost less than one print.
