# Adversarial lenses — hands on a device

Run these before the shell is printed and before the board freezes the control
positions. Each is a separate pass with its own question. A reach check that runs
green on the declared layout has told you about the hands the survey measured, in
the grip the model declared. These lenses are the gap between that and the hands
that will hold the thing.

---

## 1. Whose hand held the print?

- Who placed the controls, and who first held the print? If it was the same person,
  the layout fits one hand. What is that person's reach beyond the grip, and where
  does it sit in the survey (`ergo.reach_envelope --tester-wtt-mm ... --tester-wcg-mm ...`)?
- A builder at the men's P70 cannot feel a woman's P5 reach. Who in the room is small?

## 2. The smallest hand in the range

- Which control limits, and for whom? The verdict names it. How far can it move away
  from the wrist before the target fails (`reach_map.json`, `outward_mm`)? A negative
  value is how far it must move toward the wrist to reach the target.
- Is the market the US Army of 1988? A market with smaller hands needs a higher limit
  or another dataset, not the same 95%.

## 3. The grip you assumed

- Where does the middle finger wrap, really? Hold a print of the handle and mark it.
  The declared grip centre moves every wrist point with it.
- Is the hand axis the one a relaxed hand takes, or the one that makes the numbers
  pass? GRIP-POSTURE is an assumption with an owner for this reason.
- Is the declared handle diameter the largest handle, measured as perimeter / pi
  through the grip centre?
- Is it a handle at all? A device held flat or at its edges is `kind: slab`: no count
  is made, and the testers settle its reach. Is any grip declared a slab to escape a
  fail on a real handle?

## 4. Centre controls on a two-handed device

- Which controls sit between the hands? They move out of reach first when the body
  gets wider. What spacing keeps them at the target (`grip_spacing.json`)?
- Is any of them pressed once per session? Then declare it a regrip control, with the
  reason, rather than force it into reach.

## 5. The width nobody questioned

- What device does the person hold every day? Is this one wider, taller or heavier?
  No survey gives a width limit; that reference device does. Put its width in a
  record and let `cad.bounding` hold the design to it.
- Has anyone held a print of the full width yet, or only the handles?

## 6. What stands proud of the face

- What stands above the face where the thumb travels: a cap, a stick, or the module
  under it? A part through an opening cut for it reads clear in a clearance check
  however far it stands proud.
- Look at a render from the user's eye, and from the side, before any spend.

## 7. The largest insert in its open pose

- Does the device take something in: a dock, cradle or clamp that holds a removable
  item? Find the largest item it must take. Open the capture feature to its widest,
  put the item at the capture feature and tilt it along its insertion path. Did you
  export that pose (cad-solid `references/travel_sweeps.md`)?
- Where does the hand go while it loads the item? Is there room for the fingers?

## 8. Two shipped products first

- Before you add a mechanism, a grip shape or a control position, what do two
  shipped products in this category do? If both do the same thing, what do they know?
- If yours differs, what is the reason, and who has held it?

## 9. Which controls were marked regrip, and why?

- Read each `regrip_reason`. Is it true that the control is pressed rarely, or was it
  marked regrip because it failed reach?
- A control that is pressed often and marked regrip hides a reach fault behind a
  declaration. Ask the person who wrote it.
