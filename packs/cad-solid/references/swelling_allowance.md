# A pouch cell's swelling allowance, as a clearance pair

Tier 3. Read this before you put a pouch cell under a cover, a screen or a board.

---

## The problem in one sentence

A pouch cell gets thicker as it ages, and a cell with no room to grow pushes on the
part next to it.

The growth is slow and it is not a fault. Charge cycles, storage at a high state of
charge and heat all add to it. The cell maker states it in the datasheet: as a
thickness at end of life, or as a percentage of the thickness as delivered. When the
enclosure gives the cell less room than that, a cover bows, a screen lifts out of its
bezel or a seam opens. A swollen cell that is also crushed is a safety problem. The
failure is costly and the check is cheap.

## Why this is not a gate

`cad.clearance` already measures the least distance between two declared solids. A
swelling gate would measure the same distance against a number from the datasheet.
The only new thing is the number. The allowance is what the claim asserts, so it is
the claim's limit (claims/), read through `ctx.acceptance`, as `cad.clearance` already
reads it.

*Rejected:* a `cad.swelling` gate that reads a growth percentage from the projection.
It is `cad.clearance` again, with the claim's limit moved into a pack parameter, which
the pack format refuses: a claim's goalpost must be in the claim, where a reader sees
it and where the qualification moves it.

## The recipe

1. **Model the cell at its largest delivered thickness**, placed where it sits in the
   assembly, as a part in `meshes`. Use the nominal thickness plus the datasheet's
   tolerance, not the nominal alone: the tolerance is there on day one, before any
   growth.
2. **Exempt the cell from `cad.wall_thickness`.** It is a bought part, and its maker
   sets its thickness:

   ```json
   "wall_exempt_parts": {"cell": "bought pouch cell; its maker sets its thickness"}
   ```

3. **Get the allowance from the datasheet.** With an end-of-life thickness:
   `allowance = t_end_of_life_max - t_delivered_max`. With a growth percentage `p` of
   the delivered thickness: `allowance = p * t_delivered_max`. Write down which
   datasheet, which revision and which line you read, beside the claim.
4. **Decide which face grows.** When the cell is bonded to the frame on one face, all
   of the growth goes to the free face: declare the pair on that side with the full
   allowance. When the cell floats between two parts, the model does not know how the
   growth splits. Declare both sides with the full allowance, or bond the cell on one
   face.
5. **Declare the pair**, with the reason in words a reviewer can check:

   ```json
   "clearance_pairs": [
     {"pair": ["cell", "cover"],
      "reason": "the cell is bonded to the frame and swells toward the cover; the gap is its datasheet swelling allowance"}
   ]
   ```

6. **State the claim**, tagged `clearance`, with the allowance as its limit:

   ```json
   {"id": "C7",
    "statement": "The cell has its datasheet swelling allowance to the cover",
    "kind": "measurable",
    "tags": ["clearance"],
    "acceptance": {"quantity": "minimum clearance", "comparator": ">=",
                   "limit": 0.42, "units": "mm"}}
   ```

A worked example, with the numbers of an example datasheet (read your own): a cell
delivered at 5.0 mm ± 0.2 mm, which allows 8 % growth at end of life. Model it at
5.2 mm. The allowance is 0.08 × 5.2 mm = 0.42 mm, on the free face.

## What the measurement does and does not say

- `cad.clearance` measures the **nearest approach** between the two solids,
  anywhere. A cell edge near a boss reads that gap, not the gap over the face. The
  face has at least that much room, so a pass holds for the face too. A fail may be
  an edge where the cell grows little: read the evidence before you move the cover.
- It measures **one pose**, at rest. A cover that bows toward the cell under a load
  (a hand, a drop, a stack in a box) takes away room the measurement shows. Add that
  deflection to the allowance, or state it as a separate claim.
- A curved face reads a wider gap than the CAD has, by about twice the chord sag
  (PACK.md, "What this pack CANNOT settle"). On a small allowance, tessellate the
  cover finely.

## The limit you will hit: one clearance condition per project

`cad.clearance` reads its limit from the claims tagged `clearance`, and every claim
with that tag must state the same condition. Two different limits make the gate
error: `ctx.acceptance` refuses a tag whose claims disagree. The gate then judges
every declared pair against that one limit. So in one project the swelling allowance
cannot sit beside a 0.3 mm button gap.

What to do until that changes:

- When every declared pair can meet the larger limit, state that one limit.
- When a pair cannot, keep the swelling pair here and check the other pair another
  way. Or check the swelling allowance on a built unit: measure the gap with a feeler
  gauge, and record it with `nopekit claim physical`.
- The change that removes this limit is in the spine: an evaluator instance per claim,
  so that each claim's pairs are judged against its own limit. It is an open
  follow-up. This recipe does not depend on it.
