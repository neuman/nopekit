# Adversarial lenses — solid geometry

Run these before anything is cut, printed or ordered. Each one is a separate pass
with a separate question; the point is to find the numbers that need to move
while moving them is still free.

A geometry check that runs green at nominal, at one pose, on parts considered one
at a time, has told you about a design that does not exist. These lenses are the
gap between that design and the one that gets built.

---

## 1. Assembly order — can it physically be built in that sequence?

A clash-free assembly is not the same as a buildable one. The parts have to arrive
at their final positions along paths, one at a time, with hands and tools attached.

- Write the assembly sequence down as a list. Now, for each step, what is the swept
  path of the part being added? Does that path pass through material that is
  already there?
- Which part goes in **last**? Can it? A part that is fully enclosed by two others
  fits perfectly in CAD and cannot be inserted.
- What holds each part while the next one goes on? "It drops into place" usually
  means "it falls out while you fit the next one".
- Is any fastener's driver access blocked by a part fitted earlier? Check the tool,
  not the fastener head — a driver needs a shaft, a socket needs swing.
- Does any step require flexing a part past its elastic limit? A snap that assembles
  by bending 4 mm is a snap that yields on the third unit.
- **Can it be disassembled?** If a part has to come out for service, run the whole
  lens backwards. Many designs assemble once and are destroyed by the first repair.

## 2. Tolerance stack — you checked the nominal; nobody builds the nominal

Every gate in this pack runs on one set of dimensions. Real parts arrive as a
distribution.

- List the dimensions that stack between the two features that must meet. Now add
  the tolerances in the direction that hurts. Is the clearance still positive?
- Which of those tolerances did you actually specify, and which did you inherit from
  a process default nobody has read?
- Process-specific growth: a moulded part shrinks anisotropically, a printed part
  grows on its first layer, a machined pocket is undersized by the tool's radius in
  every internal corner. Is the nominal you checked the nominal you will receive?
- Thermal and moisture: at what temperature was the nominal defined, and what is the
  service range? Two materials with different expansion coefficients clamped
  together at 20 °C are not clamped together at 70 °C.
- A **clash tolerance is not a fit tolerance.** The few mm³ this pack forgives is
  tessellation noise, not clearance. Do not use it as your gap.
- If the stack only closes when every part is at nominal, the design has no margin
  and the gate that passed it was measuring the wrong thing.

## 3. The pose you did not check

The single most common way a geometry check lies is by being true.

- What poses exist? Fully open, fully closed, mid-travel, over-travelled by the
  amount an impatient hand applies, and the pose the part is in while being
  assembled. Which of those did you export?
- For a rotating part: check the swept volume, not positions. A sampled sweep at 5°
  steps can step straight over an interference that occupies 2°.
- What about the pose it reaches when something fails — the lid dropped rather than
  lowered, the arm at its hard stop, the cable pulled taut?
- Which parts move relative to each other under *no* control at all: a cable, a
  wire loom, a compressed foam, a gasket that extrudes when compressed?
- If the answer is "we checked the CAD default pose", the answer is that you have
  checked the pose that was easiest to save.

## 4. What floats, and where does it settle under load?

Anything not fully constrained will find its own position, and it will find the
worst one.

- List every part that is not rigidly located. For each, what is its total freedom
  in each axis — and what stops it there?
- A part located by a clearance hole is not located. It sits anywhere in that
  clearance, and gravity plus assembly force pick which anywhere.
- Compliant parts: a gasket, a foam pad, a spring, a flexure. What is the geometry
  when it is compressed, not when it is drawn? A gasket drawn at free height is a
  clash in the assembled state, and is often the only "clash" that is fine — which
  is exactly why it needs an allowlist entry with a reason and not a raised global
  tolerance.
- Cables and flexible members: the model shows one route. Which routes are
  physically available, and does the worst one still clear the moving parts? Check
  the slack, not the drawn path.
- Under load, does a clearance close? A cantilevered feature loaded at its tip
  deflects toward whatever it was clearing. A clearance that is arithmetically
  positive at rest and zero under load is a clearance that does not exist.
- Under vibration, what walks? A part that floats within a clearance will migrate to
  one end of it and stay there.

## 5. What in this assembly is held in place by nothing?

The lens that a whole revision of a real boat needed and nobody ran. Every other
question here, and every gate in the pack, is about something being *wrong*: too
big, too thin, too close, not closed. This one is about something being *absent* —
and an absence is invisible to a check that looks for a presence, because the part
that is attached to nothing has a perfect mesh and interferes with no one.

- Go part by part, out loud, and name **what holds it**. Not "it sits in the
  housing" — which face, which screw, which shoulder, which bond line? A part whose
  answer is a sentence with no noun in it is floating.
- Trace every chain end to end and say the joints in order: servo, pushrod, tiller,
  stock, blade. Then find each joint in the geometry. On that boat the answer for
  two of them was *there is no such part* — no stock, no tiller arm — and the rudder
  blade hung 42.9 mm below its own bracket while 34 of 37 gates read green.
- For every pair you have just named, ask the reverse of the clash question: not
  "may these two share material" but **"must these two touch, and does the model say
  so anywhere a gate can read it?"** Half of an ordinary `clash_allow` list is
  already that list — a screw in its insert, a stock in its bearing, a shaft in its
  tube — written down for the other reason.
- Where does load actually *go*? Follow it from where it is applied to where it is
  reacted, naming each interface. A load path with a gap in it is a part that will
  move until it finds one.
- Which of these joints did a *fix* create, and is the fix in the model or only in
  the assembly instructions? "The stock goes through the bracket's boss" is not true
  of the geometry until there is a boss and a stock in the geometry.
- Then declare them — `role: "required"`, `mating_pairs`, `assembly_chains` — so the
  next revision cannot quietly take one away. An undeclared requirement is one that
  only exists in the head of whoever noticed it.

## 6. The kernel's tolerance budget

The check nobody runs, and the only one that catches a whole class of silently
wrong geometry.

- What tolerance did the modelling kernel use to close the last boolean? A healthy
  part sits orders of magnitude below the maximum; a part that needed the maximum to
  close is a part with a geometric error healed into it.
- Which features were created by an operation that reported a warning? Kernels heal
  quietly and record it in a place nobody looks.
- Did the export tessellation settings change between the version you validated and
  the version you sent? A tolerance change alters every measurement in this pack.
- If a face had to be "healed" to touch, ask what it was supposed to touch and by
  how much it missed. That gap is a real design error somewhere upstream, and the
  heal has hidden it in a file that now passes every check.

## 7. What the gates cannot see at all

Ask these out loud, because no gate here will:

- Is the part **manufacturable** by the process you named — draft, tool access,
  minimum internal radius, support geometry?
- Does it self-intersect while remaining closed and consistently wound? Nothing in
  this pack detects that.
- Do the declared contacts touch on the faces you *meant*? `cad.assembly_connected`
  proves a declared pair is not adrift, not that it is seated: a part that has come
  off its shoulder but is still near its neighbour somewhere else still measures as
  touching.
- Is the clearance you did not model — for a finish, a coating, a label, a
  tolerance on a bought-in part — accounted for anywhere?
- Is the bought-in part's model accurate? A vendor STEP file is a marketing
  artifact as often as a dimensional one; measure the real one.
