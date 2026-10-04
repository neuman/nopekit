# The extension protocol

*How an agent grows atompipe a new validation capability, in the middle of a
project, without anyone having prebaked it.*

This is the procedure that replaces contingencies. atompipe does not ship a CFD
validator because someone might build a boat. It ships the knowledge of **how to go
get one**, and the discipline to make sure the one you got actually works.

---

## When this fires

`atompipe gap` reports a **MEASURABLE** claim with no covering gate. That is a
capability gap, and it is the system's growth mechanism, not a failure.

It does *not* fire for:
- **PHYSICAL** claims — no tool will ever settle "the printed seam is watertight".
  Those stay in the report as UNVERIFIED until a human builds one and records a
  result. Do not invent a simulation to launder them into green.
- **ASSUMPTION** claims — those are already honest about being unevidenced.

---

## The seven steps

### 1. Name the unvalidated physical quantity

Not "make sure the hull is stable". That is a wish. Write the quantity, the
condition, and the threshold:

> *Directional (yaw) stability at 4 m/s cruise, loaded, in flat water.*

If you cannot write it that precisely, you do not yet know what to validate, and
installing a solver will not tell you. Go back to the claim and sharpen its
`acceptance` first. **A gate can only be as good as the claim it settles.**

### 2. Classify it

Use the index below. You are deciding one thing: **is there a closed-form or
empirical answer that is good enough right now?**

Prefer analytic when any of these hold:
- the geometry is still moving (it always is, early)
- the question is "is this in the right order of magnitude" rather than "what
  exactly is the number"
- a textbook correlation exists for this shape class
- the solver run would take longer than the next design change

A tier-0 analytic gate you write in twenty minutes and run five hundred times beats
a tier-2 solver gate you run twice. Ship the analytic gate **first, always** — even
when you know you will need the solver later. It becomes the solver's sanity check.

### 3. Choose the tool, and record why

Pick the standard, open, scriptable, headless option for the claim class. Criteria,
in order:

1. **Scriptable and headless.** If it needs a GUI, it cannot be a gate.
2. **Open licence.** A gate behind a seat licence is a gate most users cannot run.
3. **Standard in its field.** Boring and widely used beats clever and obscure —
   you want the failure modes to be documented by someone other than you.
4. **Installable without heroics** on Linux/macOS/WSL.
5. **Deterministic, or seedable.** A gate that gives a different answer each run
   cannot gate anything.

Write down what you picked, what you rejected, and why — this is rule 3 of the
method and it applies to tooling exactly as it applies to constants.

### 4. Install it — pinned — and smoke-test before any project data touches it

**Just install it.** This step is not optional and it is not somebody else's job. An
agent that reaches a capability gap, writes a beautiful analysis of which solver would
settle it, and then leaves the claim BLOCKED has not closed the gap. Install the tool,
or hand the user the exact command and say what it costs.

Prefer, in order: a pinned container image (no root, reproducible, trivially
removable), the project's own installer or a distro package at a pinned version, then
a source build. Record the method that worked in the pack's `install` block so the
next person runs one command instead of rediscovering this.

Pin an exact version. Record the exact command. Then run the tool's own tutorial or
a trivial known case and confirm the answer.

This step is not ceremony. A mis-built solver produces *plausible numbers*, and a
plausible number is worse than an error — it is the failure mode this entire system
exists to prevent. Establish that the tool works on a case with a known answer
before you trust it on a case without one.

Record the smoke result as evidence. If the install is large or slow, **say the real
cost out loud and let the human decide**:

> *OpenFOAM: ~2 GB, ~20 min install, ~15 min per run at this mesh density. The
> analytic metacentric gate runs now and catches gross instability. I'd defer the
> solver until the hull stops changing shape.*

### 5. Wrap it as a gate — with a negative control you actually ran

The gate:
- takes `GateContext`, returns a `Verdict`
- declares `requires_tools` / `requires_python` so it reports **BLOCKED**, visibly,
  rather than vanishing when the solver is absent
- declares its real `tier` (be honest: a 15-minute gate is tier 2)
- writes its bulky output to `ctx.out_dir` and puts only ONE dense line in `detail`
- **declares a `negative_control`**

The registry will refuse to register it otherwise. That is rule 5 made mechanical,
and it is not negotiable, because an agent that writes plausible code will write
plausible validators, and a plausible validator launders assumption into proof.

**Building a good negative control** is the hard part. The fixture must be bad in
*the specific way the gate claims to detect* — not merely broken:

| Gate claims | Bad fixture that proves it |
|---|---|
| mesh is watertight | a mesh with one face deleted |
| part fits the build volume | a part scaled 1.5× past the bed |
| no two bodies interpenetrate | two bodies deliberately overlapped 0.5 mm |
| hull is directionally stable | a hull with its centre of mass moved aft of the centre of pressure |
| beam deflection within limit | the same beam at 1/4 the section depth |
| matrix cannot ghost | the same matrix with the diodes removed |
| trace carries the current | the same trace at 1/4 the width |
| collector hits its output temperature | the same collector with the absorber coating emissivity set to a mirror |
| reaction stays below the runaway threshold | the same reaction with cooling removed |

Note the pattern: **change one physically meaningful thing, in the direction the
gate is supposed to care about.** A fixture that is bad in some *other* way (a
corrupt file, an empty mesh) proves the gate handles garbage, not that it measures
what it claims.

Then actually run it: `atompipe gate selftest --only <id>`. A gate that passes its
own known-bad fixture is reported as broken, loudly.

`atompipe check` holds you to it as well. A gate's verdicts count only while it is
*qualified* at its current code, fixtures and spine: its known-good control passed,
its known-bad control failed, both through the same channel, and — for a gate of your
own — every conclusive mutation of its known-good control failed (each value it read,
pushed until its own value lands 15% past its own limit). `check` runs whatever of
that is not on record; an unqualified gate's function is never called and its claim
reads Gap (the "Qualification" section of [`PACK_FORMAT.md`](PACK_FORMAT.md)). For a
gate that lives in the project, write `selftest/known_good.py` first — a
`context(ctx)` returning a design that passes every gate, every field stated (and,
for a gate that reads files, its own copy of them) — and build each fixture from it
with one thing changed: it is the known-good control, and the fixture is handed that
design, never the live one, so it stays diagnostic even while the live design already
fails. Without it every gate of the project reads `known-good not run`. Commit what `check` records
under `.atompipe/verdicts/` with the gate; the next clone reads the verdict and its
control from there instead of running them again.

### 6. Record provenance for every solver setting

Mesh density, turbulence model, timestep, convergence criterion, boundary
conditions, material properties — every one is a constant, and every one gets the
treatment from rule 3: why this value, what was tried, what it cost.

Solver settings are where quiet wrongness lives. "k-omega SST because the flow is
wall-bounded with adverse pressure gradient; k-epsilon was tried and under-predicted
separation by ~30% against the validation case" is a sentence that saves the next
person a week.

If a result is mesh-dependent, **say so in the verdict detail**, and add a
convergence check as a second gate.

### 7. Emit a pack

Once the gate works, it should never be rebuilt from scratch by anyone again. Lay it
out as [`PACK_FORMAT.md`](PACK_FORMAT.md) describes — an ordinary directory; no
command creates or exports one — then:

```
atompipe pack validate <name>                            # the same checks CI runs
atompipe gate selftest --pack <name> --junit <file>.xml  # every tier's control
```

The JUnit file is the proof that each gate is qualified — it passes its known-good
control and fails its known-bad one: it goes with the pack's PR. A pack whose gates have never demonstrated failure does not get
merged.

---

## Claim-class → tooling index

This index is the knowledge that replaces prebaked contingencies. It is deliberately
a *map*, not a set of implementations: it tells an agent where to look, and what the
cheap answer is before the expensive one.

**Always read the "analytic first" column before the solver column.**

| Claim class | Analytic first (tier 0) | Solver (tier 2) |
|---|---|---|
| **Buoyancy / stability** | Archimedes on the mesh; metacentric height from waterplane inertia; righting arm curve | free-surface CFD (OpenFOAM `interFoam`) |
| **External flow / drag** | empirical drag coefficients by shape class; Michell/Savitsky for hulls; flat-plate friction | OpenFOAM (`simpleFoam`, `interFoam`), SU2 |
| **Internal flow / piping** | Darcy–Weisbach + minor losses; Reynolds regime check | OpenFOAM, EPANET (networks) |
| **Structural, static** | Euler–Bernoulli beam; plate theory; Euler buckling; bolt preload | CalculiX, Code_Aster, FEniCSx |
| **Structural, dynamic / fatigue** | modal estimate from stiffness/mass; S–N curve at nominal stress | CalculiX modal + harmonic |
| **Thermal, conduction/convection** | lumped capacitance; fin efficiency; Nusselt correlations | Elmer, OpenFOAM conjugate heat transfer |
| **Thermal, radiation / solar** | view factors; Hottel–Whillier collector efficiency; `pvlib` solar position & irradiance | ray-traced radiation, EnergyPlus (buildings) |
| **Electromagnetic / RF** | link budget; near-field keep-out rules; transmission-line impedance | openEMS (FDTD), NEC2 (`necpp`) for antennas, FEMM (2D magnetostatics) |
| **Circuits** | operating point, divider, loop resistance, ripple, thermal derating | ngspice, Qucs-S |
| **PCB** | netlist/matrix reasoning; escape-routing feasibility; current-vs-width | KiCad DRC + Freerouting convergence loop |
| **Kinematics / mechanisms** | closed-form linkage; Grashof; gear ratios; workspace bounds | PyBullet, MuJoCo, Drake |
| **Chemical / thermodynamic** | mass & energy balance; equilibrium constants; adiabatic temperature rise | Cantera (kinetics, combustion), CoolProp (properties) |
| **Molecular / materials** | group-contribution estimates; RDKit descriptors | ASE, pymatgen, quantum codes |
| **Acoustics** | Helmholtz resonance; transmission loss mass law | Elmer, OpenFOAM (aeroacoustics) |
| **Optics** | thin-lens, étendue, f-number, spot size | ray tracers (Goptical, LuxCore) |
| **Control** | pole placement, phase margin from the plant model | `python-control`, `scipy.signal` |
| **Battery / power budget** | Peukert-corrected runtime; C-rate; charge time; loop resistance | PyBaMM |
| **Tolerance stack** | worst-case and RSS stack-up; Monte Carlo in pure Python | — (Monte Carlo *is* the answer) |
| **FDM printability** | overhang angle from face normals; wall thickness vs nozzle; bed fit; bridge spans | slicer CLI (PrusaSlicer/OrcaSlicer `--export-gcode`) |
| **CNC / machining** | feeds & speeds; tool reach & access; fixturing clearance | CAM post + toolpath simulation |
| **Sourcing / cost** | BOM roll-up; MOQ & lead-time check; stock query | vendor APIs |

**When the class is not in this table**, say so, and derive from first principles
instead of forcing a fit: name the governing equation or conservation law, decide
whether a closed-form solution exists for your geometry, and search for the standard
open code in that field by the *equation's* name, not the product's. Then add a row
here in the pack you emit.

---

## Anti-patterns

**Installing the solver first.** The gap is not "we don't have OpenFOAM". The gap is
a claim with no gate. Sharpen the claim, try analytic, *then* reach for the solver.

**Skipping the negative control because the gate "obviously works".** The validators
that ever shipped broken looked obviously working.

**A gate that returns a number without a threshold.** That is a report, not a gate.
It must compare against the claim's acceptance and refuse.

**Silently degrading when the tool is missing.** Declare `requires_tools` and let the
claim show as BLOCKED. An invisible skip is how a readiness report starts lying.

**Treating BLOCKED as a resting state.** It is a call to action, not a place to
settle. If a claim needs the solver, *install the solver* — or tell the user what to
run and why. Designing a pack to be comfortable without the tool it wraps is the tail
wagging the dog: you end up with a capability that never actually validates the thing
it exists to validate, and a report full of honest BLOCKED rows that nobody clears.
Ship the install recipe with the pack so the fix is one command.

**Simulating a physical claim.** No CFD run makes a hull watertight. If the claim can
only be settled by a real object, it stays UNVERIFIED — visibly — and the report says
what test would settle it.

**One giant tier-2 gate.** Split it: a tier-0 analytic bound, a tier-1 setup/mesh
validity check, and the tier-2 solve. Then the inner loop still works.

**Copying solver settings from a tutorial without recording why.** Those settings are
constants. Rule 3 applies.
