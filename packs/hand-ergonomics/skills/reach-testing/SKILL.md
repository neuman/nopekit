---
name: reach-testing
description: Run the physical reach test of a hand-held device and have a person record it. Use when the claim REACH-HELD (or any physical reach claim) is open, before a board order freezes the control positions, or when ergo.digit_reach reads Gap because the device is outside its operating context.
---

# The physical reach test

`ergo.digit_reach` counts the population on a screen. A person holding the first
full print has the final say. This skill is how to pick the two testers, run the test
and get the result recorded. The agent prepares; **a person records**. `nopekit claim
physical` is refused from an agent session or a pipe: tell the person the command, and
let them type it in their own shell.

## 1. Pick two testers by reach, not by who is in the room

The test needs one small hand and one large hand. Choose them by **reach beyond the
grip**: wrist-thumbtip length minus wrist-centre of grip length, both in mm, both
measured as the survey measured them (TR89-044):

- **Wrist-thumbtip length** (130). The little-finger side of the hand rests on a
  table. The thumb is straight, in line with the forearm, and rests on the first
  knuckle of the curved index finger. Measure from the stylion (the bony point on the
  thumb side of the wrist) to the tip of the thumb, with a caliper or a steel rule.
- **Wrist-centre of grip length** (125). Grip a 32 mm dowel (1-1/4 in; a broom handle
  is close). Put the bottom of the fist on the table with the end of the dowel flush
  with it, the fist in line with the forearm. Measure from the stylion to the centre
  of the dowel.

Tester 1: reach beyond the grip **at or below 44 mm** (the women's P10 in ANSUR 1988).
Tester 2: **at or above 62 mm** (the men's P90). The proposer says where a person
sits:

```
nopekit propose ergo.reach_envelope -- --layout layout.json --tester-wtt-mm 112 --tester-wcg-mm 70
```

Its `design_range.json` gives `tester.share_at_or_below_pct` and whether the person
qualifies as the small or the large tester. Record the testers' values, never their
names.

The builder is not a tester by default. A layout drawn by one person fits that
person's hand.

## 2. Prepare the article

- Print the **first full shell**, both handles and the whole face, with a dummy cap at
  every control: the right height, at the declared `at`. A handle alone does not test
  the width or the centre controls.
- Export it, so the result binds to what was printed: `nopekit export reach-first-print`.
  The export's test card prints the article's hash and the command that records a
  result on it.
- Mark the declared grip centre on each handle, where the middle finger wraps.

## 3. Run it

For each tester, with each hand:

1. Hold the handle as declared: middle finger at the mark, the hand along the
   declared axis, the wrist straight.
2. Press every judged control without a regrip. Do not slide the hand. A control
   marked regrip (`operated_by: []`) is not pressed in this pass.
3. Count the controls the tester could not press without moving the hand. That count
   is the measured value: the claim holds `controls missed without regrip <= 0`.
4. Tester 2 also checks that no cap edge catches the thumb as it moves between
   controls.

Take a photo of each tester holding the print at the worst control. Put it under the
project (for example `evidence/reach-held.jpg`).

If a tester holds the handle another way to reach, the grip assumption is wrong too.
Say so to the person: GRIP-POSTURE is theirs to own or to change.

## 4. The person records it

In their own shell, with the article hash the export printed:

```
nopekit claim physical REACH-HELD pass --measured 0 --evidence evidence/reach-held.jpg --detail "tester 1 reach beyond grip 43 mm, tester 2 64 mm, grips as declared, 2026-11-20" --article <hex>
```

A miss is a fail, and it keeps counting after every later edit:

```
nopekit claim physical REACH-HELD fail --measured 2 --evidence evidence/reach-held.jpg --detail "tester 1 missed btn_y and menu without a regrip" --article <hex>
```

The owner of the grip assumption records it once they have watched the test:

```
nopekit claim physical GRIP-POSTURE assume
```

`claim physical` takes who from git's identity and when from the clock; it refuses
`--who` and `--when`. A pass reads Stale when the article's design moves: print and
test again before the board order.

## 5. After a fail

- Read the verdict of `ergo.digit_reach` and `reach_map.json`: which control limits,
  and how far can it move toward the wrist (`outward_mm`)?
- Move the control in the model, not in the layout file alone. Check, then reprint.
- Never loosen the claim to fit the print. A lower limit is a decision about who the
  device is for; a person records it.
