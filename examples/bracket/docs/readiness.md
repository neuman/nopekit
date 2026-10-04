# wall-bracket — readiness (v0.1)

**v0.1 is NOT ready: 4 of 7 required claims are unresolved — 1 failing (C1); 2 gaps (C6, C7); 1 pending build (C5).** 3 of 7 claims are checked against the current inputs. Pending build: 1 claim needs an article (C5).

> Shelf bracket: 1.5 kg static load on a 60 mm arm, printed in PETG

## What is PROVEN (checked: every evaluator passed on the current inputs — checked does not mean true)

| Claim | Acceptance | Value | Evaluator | Evidence |
|---|---|---|---|---|
| **C2** Root bending stress stays within half of yield | bending stress <= 1.0 utilisation | 0.245 utilisation | `bracket.bending_stress`, `bracket.model_validity` | *none written* |
| **C3** Fastener bearing stress within the design allowable | bearing stress <= 15.0 MPa | 0.195 MPa | `bracket.bearing` | *none written* |
| **C4** Prints on a 220 mm bed without supports | bed fit <= 204.0 mm | 73.5 mm | `bracket.bed_fit`, `bracket.min_wall` | *none written* |

Not compared with its claim's acceptance condition — each of these values is judged against its evaluator's own limit only:
- **C4** `bracket.min_wall` : 7 mm (wall thickness, not bed fit)

Every row above is checked: each of its evaluators ran and passed against the inputs, code and controls it has now, inside its operating context, and every value compared with the claim's acceptance condition meets it. A skipped, errored, unqualified, invalidated or unrun evaluator — or one outside its operating context, or a value its claim's acceptance condition does not admit — puts its claim in another section with the reason, never here. And checked does not mean true.

## Pending build

These need an article. No evaluator in any pack can settle them, and no number of passing evaluators above changes that.

- **C5** Survives two winters outdoors without UV embrittlement
  - **Test that would settle it:** **no test has been written down.** As stated, this claim cannot be settled by any observation — give it an acceptance or a procedure in its note, or it will stay on this list forever
  - **Why it matters:** no analysis settles polymer weathering; it needs a real part on a real wall for two years
  - **Record the result:** `atompipe claim physical C5 --pass|--fail --detail "..." --when <ISO date>`

## Gaps

A claim with no evaluator is a gap, not a defect. It is closed by installing or writing a tool — with the cost said out loud before anyone agrees to it.

### N-C7 — first mode  *(identified)*
- **Claim:** C7 — First mode is clear of the pump that sits on the shelf  `gap`
- **Tool options:** none proposed yet — `atompipe gap --propose`

### Assumptions nobody owns

An assumption reads Assumed only with a reason and an owner who recorded it; until then it is a gap.

- **C6** The load is static and centred on the arm — no owner recorded — an assumption reads Assumed only once its owner records it; nothing can record one yet

## Assumed

None recorded: no claim reads Assumed, every parameter carries a rationale, and every ingested artifact has been read.

## Failing, stale, skipped or open

### [FAIL ] C1 — Tip sags no more than 0.5 mm at rated load  *(failing, critical)*
- **Why:** bracket.deflection : 0.700 mm at 15 N (limit 0.5 mm)
- **Acceptance:** tip deflection <= 0.5 mm
- `[FAIL] bracket.deflection : 0.700 mm at 15 N (limit 0.5 mm)`
- `[ok  ] bracket.model_validity : slenderness 8.6 (>= 5.0 for Euler-Bernoulli; below this the deflection gate under-predicts)`

## Reproduce

This file is generated. Re-derive every row above with:

```sh
atompipe check --tier 0          # the inner loop: analytic gates only, seconds
atompipe report --write          # regenerates docs/readiness.md
```

`check` re-runs only the gates whose inputs, code or control moved; every other verdict is served from `.atompipe/verdicts/` as it was recorded. `atompipe check --force` re-runs all of them.

One gate at a time — this is the command behind each row, and the code it runs:

```sh
atompipe check --only bracket.deflection       # structural, stiffness, deflection — gates/structural.py
atompipe check --only bracket.bending_stress   # structural, strength, bending-stress — gates/structural.py
atompipe check --only bracket.bearing          # structural, fastener, bearing — gates/structural.py
atompipe check --only bracket.model_validity   # structural, stiffness, strength, fastener — gates/structural.py
atompipe check --only bracket.bed_fit          # manufacturability, fdm, bed-fit, footprint — gates/structural.py
atompipe check --only bracket.min_wall         # manufacturability, fdm, wall-thickness, min-wall — gates/structural.py
```

And show the gates above can tell a good design from a bad one, which is the only reason their passes mean anything:

```sh
atompipe gate selftest           # run each evaluator's known-good and known-bad controls, and
                                 # the mutation pass for one not from a bundled pack; exits 1
                                 # on any unqualified evaluator
```

---

*Generated by `atompipe report` from the ledger and its verdict cache. Do not hand-edit: it is an output, not a source. If a line here is wrong, the ledger is wrong.*
