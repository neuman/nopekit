# openmodelica

Modelica is an acausal, equation-based language for multi-domain physical
models: thermal, fluid, mechanical, electrical and control in one system of
equations, written as relations rather than as assignments. This pack is
therefore not a domain pack. It is a **general modelling capability** — it serves
any project whose physics is written as equations instead of closed forms, and it
does not care which physics.

It is also the textbook case for `docs/EXTENSION_PROTOCOL.md`: a heavy tier-2
solver (`omc`) wrapped so that it degrades to BLOCKED, visibly, when it is not
installed, with a tier-0 half underneath it that catches most real defects and
needs nothing but the standard library.

---

## What this pack settles

1. **Every parameter in the model source is defendable** — it carries a
   description, and if it is a physical quantity it carries a unit.
2. **Every Modelica variable a claim names still exists** — in the model source
   or in the recorded result columns. This is the drift check between what the
   project claims and what the model can say.
3. **A recorded run is valid** — it reached its stopTime, contains no NaN or Inf,
   contains the variables that were asked for, has a monotonic time column, and
   (when asked) had settled rather than being cut mid-transient.
4. **A number extracted from that run meets its claim's acceptance** — final,
   min, max, mean, or the value at a named time.
5. **The model and an independent implementation still agree**, variable by
   variable, within a stated tolerance.
6. **The model is structurally sound** — equations equal unknowns, it compiles,
   and it simulates to stopTime without firing an assert. (tier 2, needs `omc`.)

The architecture behind 3–5 is worth stating: the tier-0 gates read result
*files*, not models. Record a run once on a machine that has OpenModelica, commit
the result, and gate claims against it everywhere else. `modelica.solution_valid`
is what stops that from becoming a lie.

## What this pack CANNOT settle

This section matters more than the one above, because every item in it is
something a reader will otherwise assume.

- **It cannot tell you the model is right.** A balanced, compiling, converged
  model of the wrong physics is a balanced, compiling, converged model. Nothing
  here reads an equation and forms an opinion about it.
- **It cannot detect index reduction gone wrong.** A high-index DAE (two bodies
  rigidly connected, an ideal transformer, a position constraint differentiated
  twice) is solved by differentiating equations and substituting. omc does this
  silently. When it succeeds the result can still drift on the constraint
  manifold; when it fails you get a structurally singular error. `modelica.checks`
  sees the second. Nothing sees the first.
- **It cannot tell you initialisation was real.** `start` values are *guesses*
  unless `fixed = true`; the initialisation system is a separate nonlinear solve
  and it can land on a mathematically valid, physically absurd branch — a
  negative absolute pressure, a temperature below zero, a flow the wrong way
  round. The run then proceeds perfectly from a state that never existed.
- **It cannot see chattering.** A model with a state event (a friction stick-slip
  switch, a check valve, a hysteresis block) can cross the same threshold
  thousands of times per second. The run completes. The result file is enormous,
  the time column is full of duplicated instants, and the answer is an artefact
  of the event handling.
- **It cannot tell you the answer is tolerance-independent.** Change
  `modelica_tolerance` from 1e-6 to 1e-8 and a marginal event may resolve
  differently, a nonlinear loop may land on another root, and a claim with a
  narrow margin may flip. If a claim's margin is within a few multiples of the
  solver tolerance, that claim is measuring the solver. Run the sweep and say so.
- **Its unit checking is only as good as the declarations.** `modelica.source_hygiene`
  reports which parameters carry units. It does not check DIMENSIONAL
  CONSISTENCY of the equations — that is omc's job with `--unitChecking`, which
  is off by default and which most real models do not survive being switched on.
  A model of undeclared `Real`s has no unit checking at all, and adding a length
  to a temperature in it produces a plausible number.
- **It cannot settle a PHYSICAL claim.** No simulation makes a seal watertight.
- **The scanner is a scanner, not a compiler.** It resolves class nesting,
  components, `extends` and short class definitions. It does not evaluate
  expressions, open libraries it was not pointed at, or understand `redeclare`.
  Everything it cannot resolve is reported as unresolved, never as absent.

## The gates

| Gate | Tier | Measures | Fails on |
|---|---|---|---|
| `modelica.source_hygiene` | 0 | parameters with no description or no unit | `stripped_declarations`: the pack's own .mo with descriptions deleted and `unit=` renamed to `displayUnit` |
| `modelica.claims_addressable` | 0 | claim variables not resolvable in source or result | `renamed_variable`: one claim still names the pre-rename spelling |
| `modelica.solution_valid` | 0 | time reached vs stopTime, + five other assertions | `run_truncated`: the same run cut at half its stopTime. Second fixture `nan_injected`: one NaN cell |
| `modelica.result_claim` | 0 | worst bound variable vs its acceptance | `claim_exceeded`: the claimed column shifted past its own limit |
| `modelica.mirror_agrees` | 0 | worst disagreement, as a fraction of tolerance | `mirror_drifted`: the mirror offset just past tolerance |
| `modelica.checks` | 2 | equations vs unknowns from `checkModel` | `assets/bad/UnbalancedTank.mo`: one unknown too many |
| `modelica.compiles` | 2 | `buildModel` produced an executable | `assets/bad/WillNotCompile.mo`: a call to a function defined nowhere |
| `modelica.simulates` | 2 | end time actually reached | `assets/bad/AssertFires.mo`: an assert that fires at 3% of stopTime |

**`modelica.solution_valid` is the validity guard** and it binds broadly on
purpose (`modelica`, `simulation-result`, `result-extraction`,
`cross-representation`, `model-agreement`, `mirror-agreement`, `steady-state`,
`dynamic-response`, `transient`, `equation-model`). When it trips, every number
anybody extracted from that run is untrustworthy and should read that way, so
dragging the whole domain down is the correct behaviour. Ship one gate like this
in every pack.

The three tier-2 gates SKIP wherever `omc` is absent, and their claims resolve
BLOCKED. Their fixtures are real `.mo` files that omc genuinely rejects, so those
controls fire wherever omc is installed — see `references/installing.md`.

They also SKIP when a library named in `modelica_load_libraries` does not load.
omc is present but the library is not, so nothing was learned about the model.
**This pack's selftest cannot tell you whether a library is installed.** Its
fixtures load no library, on purpose, so that the controls run, and fail as they
must, on a bare omc.
For the same reason they pass on an omc with no Modelica Standard Library, and
that is what most installs produce: the `-minimal` docker image ships none.
`references/installing.md` gives the one-time install and a check for it.

## Claim tags

Gates list everything they bear on; **claims carry the narrowest vocabulary that
describes what they actually assert.** Tag a claim `modelica` and every gate here
covers it, so a source-hygiene failure makes a *temperature* claim read FAIL and
the reader goes hunting in the wrong place.

| Tag | Use it for a claim about |
|---|---|
| `modelica-source` | the model text itself: declarations, units, descriptions |
| `parameter-provenance` | a parameter being defendable |
| `claim-coverage`, `model-drift` | whether claims still point at things that exist |
| `simulation-result` | the validity of a recorded run |
| `result-extraction` | a number read off a run |
| `cross-representation`, `model-agreement`, `mirror-agreement` | the model versus an independent implementation |
| `equation-balance`, `model-structure` | equations equalling unknowns |
| `model-compiles` | the model building to an executable |
| `simulation-runs` | the run completing |
| `steady-state`, `dynamic-response`, `transient` | what the run got to, and how |

## Units and frames

**SI, and absolute.** Modelica's own SI types are absolute: `Temperature` is
kelvin, `Pressure` is pascals absolute, `Angle` is radians. A celsius number in a
kelvin slot is 273.15 out and entirely plausible; a gauge pressure in an absolute
slot is one atmosphere out and also plausible. **Time is seconds everywhere** —
`stopTime`, reducer times, steady-state tolerances, timeouts.

This pack never converts. It compares the number in the result column against the
number in the claim, so the two must already be in the same unit, and the pack
cannot tell you when they are not. That is exactly why
`modelica.source_hygiene` insists the declarations carry units: they are the only
place the information exists.

A genuinely dimensionless parameter is `Real x(unit = "1")`, not a bare `Real`.

## Pointing a project at its model

Everything is keys in the model's projection; there is no config file. The
minimum:

```json
{
  "modelica_sources": ["model/MyPackage"],
  "modelica_class": "MyPackage.Experiments.NominalRun",
  "modelica_load_libraries": ["Modelica"],
  "modelica_result_csv": "runs/NominalRun_res.csv",
  "modelica_stop_time_s": 3600.0,
  "modelica_required_variables": ["tank.T", "pump.m_flow"],
  "modelica_claim_variables": [
    {"claim": "C4", "variable": "tank.T", "reducer": "max"}
  ]
}
```

`modelica_sources` wants the **package root**, not one file: the scanner resolves
`tank.T` by finding what `tank` is an instance of, and it cannot do that from a
file it was never shown. Paths resolve against the project root; a directory
containing `package.mo` yields its whole tree, `package.mo` first.

With a claim in the ledger carrying an acceptance, the binding needs only
`{claim, variable, reducer}` — the limit then lives in one place. Add
`comparator` and `limit` to the binding only when there is no claim yet.

`selftest/baseline.json` is the full worked example, with a `_notes` entry per
key explaining what it is, its unit, and why its value is what it is. Read that
before reading any code here.

## The physics, in one paragraph

Modelica models are differential-algebraic systems. You write relations —
`C*der(T) = Q - qLoss`, `qLoss = UA*(T - T_amb)` — and the tool decides what
solves for what. The system is solvable only when the number of equations equals
the number of unknowns, which is why `checkModel`'s one-line balance report is
the single most valuable structural check in the language and why an extra
declared-but-unconstrained variable is the classic defect. From there the tool
flattens the hierarchy, eliminates aliases, reduces the index where a constraint
forced it above 1, matches equations to variables, tears the algebraic loops,
generates C, compiles it and integrates with a variable-step stiff solver
(DASSL by default). A sane result has the right order of magnitude, conserves
what it should conserve, and does not depend on the tolerance. Check all three.

## Where to look next

- `references/installing.md` — apt, the official installer, docker, a pinned
  version, installing the Modelica Standard Library (the `-minimal` image has
  none), and the smoke test to run before any project data touches it.
- `references/driving-omc.md` — the `.mos` scripting API, why a script file
  beats a ZMQ session, and the exact scripts these gates generate.
- `references/result-formats.md` — the CSV format in detail, `.mat`, duplicate
  event times, `variableFilter`, and how to record a run for this pack.
- `references/failure-modes.md` — the catalogue: what each Modelica failure looks
  like on the terminal, which gate sees it, and what the verdict says.
- `lenses.md` — the adversarial review dimensions for equation-based modelling.
