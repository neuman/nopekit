# Failure-mode catalogue

What goes wrong in an equation-based model, what it looks like on the terminal,
which gate sees it, and what the verdict says. The last column is the one to read
first: several of these are **caught by nothing here**, and knowing which is the
point of the table.

---

## 1. Equation/unknown imbalance

**What it is.** The system has more unknowns than equations (no unique solution)
or more equations than unknowns (over-determined). The classic cause is a
variable declared for documentation and never constrained; the other classic is a
`connect` left out of a loop.

**On the terminal.**
```
Class MyPackage.Run has 2 equation(s) and 3 variable(s).
Warning: The initial conditions are not fully specified.
Error: Too few equations, under-determined system.
```

**Gate.** `modelica.checks`. **Verdict.** `checkModel(MyPackage.Run): 2
equation(s), 3 variable(s) — 1 unknown(s) too many, the system has no unique
solution`. Control: `assets/bad/UnbalancedTank.mo`.

**Note.** A balanced count is necessary, not sufficient. Two equations that state
the same relation differently balance the count and leave the system singular —
see §2.

---

## 2. Structurally singular system

**What it is.** The counts match but the equations cannot be matched to the
variables: some subset of *n* equations involves fewer than *n* variables. Two
ideal voltage sources in parallel, two rigidly connected inertias each with their
own initial velocity, a duplicated constitutive relation.

**On the terminal.**
```
Error: Model is structurally singular, error found sorting equations
  {1, 4, 7}
  for variables
  x, y
```

**Gate.** `modelica.compiles` (it fails in the backend, after checkModel is
happy). **Verdict.** `buildModel(...) returned no executable ... Error: Model is
structurally singular`.

---

## 3. Index reduction

**What it is.** A constraint between states makes the DAE higher than index 1
(two masses rigidly coupled, an ideal transformer, a position constraint). The
tool differentiates equations and substitutes — Pantelides plus dummy
derivatives — and usually succeeds silently.

**On the terminal.** Nothing at default verbosity. With `-d=bltdump` you see
differentiated equations and selected dummy states.

**Gate.** **None.** When reduction fails you get §2. When it succeeds, the
solution can still drift off the constraint manifold over a long run, and no gate
here will notice. Watch it yourself: add the constraint residual as an output
variable and put a claim on it. That turns an invisible risk into a
`modelica.result_claim` row.

---

## 4. Initialisation failure

**What it is.** The initialisation system — the algebraic equations plus whatever
`fixed = true` states pin down — has no solution, or several. A `start` value
without `fixed` is a *guess* handed to a nonlinear solver, not an initial
condition.

**On the terminal.**
```
Error: Failed to solve the initialization problem.
  The initial residual is 3.4e+02
Warning: The initial conditions are not fully specified. Use +d=initialization
```

Or, worse, no error at all and a first sample that is physically absurd.

**Gate.** `modelica.simulates` catches the hard failure (no result file, non-zero
exit). **The soft failure — converging to an absurd branch — is caught by
nothing.** A negative absolute pressure, a temperature below zero, a flow the
wrong way through a check valve: the run proceeds perfectly from a state that
never existed. Put a claim on the *initial* value (`{"reducer": "initial"}`), and
read `lenses.md` §2.

---

## 5. The run stops early

**What it is.** An assert fires, a `terminate()` is reached, the solver gives up,
or the process is killed. **A result file is still written**, full of real numbers
from the part of the run that happened.

**On the terminal.**
```
assert | debug | tank contents crossed T_max: the run is not valid past this point
Simulation execution failed for model: AssertFires.Run
```

**Gates.** `modelica.simulates` (the run itself) AND `modelica.solution_valid`
(anyone reading the file afterwards). The second is the one that matters, because
the file outlives the run: `reached t=738/25000 s ... stopped at t=738 s, 3.0% of
the 0..25000 s run it was asked for`. Control: `assets/bad/AssertFires.mo` and
`selftest/bad_modelica.py:run_truncated`.

**This is the failure the whole pack is arranged around.** Any pipeline that
checks for the file's *existence* rather than its *final time* will read a number
off it and be wrong in a way that survives review.

---

## 6. Divergence — NaN and Inf

**What it is.** An unguarded division, a `sqrt` of a negative, a Newton iteration
that gave up, an unstable explicit step. One variable goes non-finite and poisons
everything algebraically downstream of it in the same run.

**On the terminal.**
```
Warning: The corrector could not converge ... nonlinear system 42
Error: Simulation terminated: nan in the residual
```
— or nothing, and a column of `nan` in the CSV.

**Gate.** `modelica.solution_valid`: `1 non-finite cell(s), first at line 15 in
'tank.T' (nan)`. Control: `selftest/bad_modelica.py:nan_injected`.

---

## 7. Chattering

**What it is.** A state event whose condition is re-triggered immediately after
handling — friction stick-slip, a check valve at zero flow, hysteresis with too
narrow a band. The solver fires thousands of events per second of simulated time.

**On the terminal.** The run takes minutes for a model of ten equations. With
`-lv=LOG_EVENTS`, a wall of event messages. The result file is enormous and its
time column is mostly duplicated instants.

**Gate.** **None directly.** `modelica.simulates` catches it only if it exceeds
the timeout, and that is reported as a SKIP (nothing was proven), not a FAIL.
Symptoms to look for by hand: a result file far larger than
`numberOfIntervals` rows implies, and a `timeTotal` out of proportion to the
model's size.

---

## 8. Tolerance-dependent results

**What it is.** The answer changes with `tolerance`. A marginal event resolves on
the other side; a nonlinear loop lands on a different root; an accumulating
quantity integrates to a visibly different total.

**On the terminal.** Nothing. Every run looks clean. This failure has no error
message, which is why it survives so long.

**Gate.** **None.** It cannot be gated from one run. Run the same experiment at
1e-4, 1e-6 and 1e-8, and compare the claimed quantity across the three. If it
moves by anything comparable to the claim's margin, the claim is measuring the
solver. `lenses.md` §3 asks this directly; the honest answer belongs in the
readiness report as an assumption, not as a proven row.

---

## 9. Unit errors

**What it is.** A celsius number in a kelvin slot, a gauge pressure in an
absolute slot, degrees in a radians slot, kW where W was meant. Every one of them
produces a plausible number.

**On the terminal.** Nothing, unless every declaration carries a unit AND
`--unitChecking` is on — in which case, warnings by the hundred the first time.

**Gate.** `modelica.source_hygiene` reports which parameters carry units at all,
which is the *precondition* for ever catching this. It does not check the
equations' dimensional consistency. `modelica.mirror_agrees` catches a unit slip
that reaches a compared variable, because a factor of 1000 is not subtle once
something is actually comparing.

---

## 10. Claim/model drift

**What it is.** A variable is renamed in the model; a claim still names the old
spelling. The claim silently stops being checked — the extraction gate skips for
a missing column, the skip resolves the claim to BLOCKED, and BLOCKED in a long
report reads as "waiting on tooling".

**On the terminal.** Nothing at all. The model compiles, simulates and is
correct.

**Gate.** `modelica.claims_addressable`: `1/2 claim variable(s) addressable ...
MISSING: C1:tank.T_v2`. Control: `selftest/bad_modelica.py:renamed_variable`.

---

## 11. The mirror that drifted

**What it is.** A second implementation of the same equations — a spreadsheet, a
notebook, a script that produces "the numbers" — written because the formal model
could not be run where the numbers were needed. It works, it gets used, it gets
extended, and from that day the two drift with nothing watching.

**On the terminal.** Nothing. Both halves produce numbers that look right.

**Gate.** `modelica.mirror_agrees`: `worst tank.T at 105.0% of its tolerance
(relative error 1.21e-03)`. Control: `selftest/bad_modelica.py:mirror_drifted`.

**The mirror is not the risk. The mirror with no comparison gate is the risk.**

---

## 12. A stale result file

**What it is.** The committed result no longer corresponds to the committed
source. Every tier-0 verdict in this pack is then describing a model that no
longer exists.

**On the terminal.** Nothing.

**Gate.** **None in this pack** — it is the ledger's job. Hash the sources into
the projection so a changed model marks the verdicts STALE, and remember that
stale is not passed. The cheap discipline: regenerate the result in the same
commit that changes the model, and never hand-edit a result file.

---

## 13. A library omc cannot see

**What it is.** The model uses the Modelica Standard Library, or another
library, and the omc running the gates has no copy of it. This is the default
state of the `-minimal` docker image and of a fresh compiler-only install.

**On the terminal.**
```
loadModel(Modelica) = false
Error: Failed to open file for writing: //.openmodelica/libraries/index.json.tmp1
Error: Failed to load package Modelica (default) using MODELICAPATH //.openmodelica/libraries/.
```

**Gate.** `modelica.checks`, `modelica.compiles` and `modelica.simulates`.
**Verdict.** If the library is listed in `modelica_load_libraries`, the gates
SKIP: `library not loaded, nothing checked: loadModel(Modelica) = false; omc:
Failed to load package Modelica ...`. The claim reads BLOCKED. If it is not
listed, the gates FAIL on `Class Modelica.… not found in scope`, which cannot be
told apart from a misspelt class. So list every library the model uses.

**Note.** The pack's own selftest never exercises this. Its fixtures load no
library, so they pass on an omc that has none. `references/installing.md` has
the install and the check.

---

## Quick triage

| Symptom | Look at |
|---|---|
| `checkModel` counts differ | §1 |
| builds fails after a clean check | §2, sometimes §3 |
| "failed to solve the initialization problem" | §4 |
| result file exists but is short | §5 |
| `nan` in a column | §6 |
| run takes minutes for a tiny model | §7 |
| answer moved after a tolerance change | §8 |
| answer is off by 273.15, 1000, or 57.3 | §9 |
| a claim has been BLOCKED for weeks | §10 |
| two documents disagree about one number | §11 |
| everything is green and nobody believes it | §12 |
| `loadModel(...) = false`, or tier-2 BLOCKED with omc installed | §13 |
