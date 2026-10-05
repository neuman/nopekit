# The method

nopekit encodes a way of working that was proven on a real device before it was
generalised — a physical product taken from a napkin sketch to manufacturable
outputs and an honest readiness report, by a human and an agent working together
across dozens of revisions. (See [`docs/ORIGINS.md`](docs/ORIGINS.md).)

This file is the doctrine. It is short on purpose, because an agent has to hold it
in context while doing something else. The ten rules below are the whole thing.
Everything in `src/` exists to make one of them mechanical.

---

## 0. The shape

```
claims  ->  gates  ->  packs  ->  readiness report
```

A **claim** is something that must be true for the design to work. A **gate** is an
executable that settles a claim and is capable of failing. A **pack** supplies gates
for one physical domain. The **readiness report** is the ledger rendered: what is
proven, what is not, and why.

Everything else is commentary.

---

## 1. One parametric model is the only source of truth

Every output — geometry, boards, firmware, renders, bills of materials, docs — is
*generated* from a single model. Move one feature and every representation of it
moves too, because none of them is typed twice.

The corollary is a rule about files: **generated files are outputs, not sources.**
Never hand-edit something a generator produced. If it is wrong, the model is wrong.
A repo that tolerates hand-patched outputs has no source of truth, only a rumour of
one.

## 2. Derive, never duplicate

If a stack's top surface is `floor + standoff + board + clearance`, write that
expression. Do not write the number it currently evaluates to.

Any time you are about to write a value that already exists elsewhere, write the
expression instead. A duplicated constant is a future misalignment with a date on
it — and the misalignment will not show up in the file you edited.

## 3. Every constant carries its provenance — especially what lost

A number without a reason is a number nobody can defend, and the next agent will
change it. Record, inline and in the ledger:

- **why this value** — the physics, not a restatement of the value
- **what was tried and rejected, and why it failed** ← the highest-value field
- **what measurement or datasheet it came from**
- **which gate protects it**

The rejected alternatives are the part people skip and the part that pays. The shape
to aim for:

> *The wider trace was tried first and the router could not close that net through
> the congested corridor; the narrower one costs ~34 mΩ more and routes.*

> *A rigid catch was tried and rejected: it transferred the drop load into the
> print's weakest layer boundary.*

> *Do not fit the narrower ribbon. It floats in the wider housing with 4 mm of
> independent slop at each end.*

Each of those closes a question permanently. Without the record, every new agent
re-litigates every settled number, and the project walks in a circle at full speed.

`nopekit why <param>` exists so an agent can pull one parameter's whole history
instead of reading the entire decision log.

## 4. Validators are gates, not loggers

A validator that prints a problem and exits 0 is a decoration. It must **refuse**.

Put the refusal at the boundary that costs money. An export step that will not emit
a manufacturing package unless the rule check reports zero violations is worth more
than the hundred lines of checking above it, because it is the one that cannot be
ignored in a hurry.

The lesson is usually learned the expensive way. A cable-routing validator once
reported success for an entire revision while the cable was geometrically inside a
wall — it computed the right answer, returned a flag, and nothing read it.

> **A logger is not a gate.**

## 5. Every gate needs a falsification control

**A gate that cannot fail proves nothing.** Before you trust a validator, run it on
input you know is bad and watch it fail.

The pattern to copy: alongside the real run, run a control with the protective
element deliberately removed — the diode, the fillet, the cooling, the damper — and
require the gate to *fail* that one. The control is not a test of the design. It is
a test of the validator: proof it can detect the failure it claims to rule out.

nopekit makes this mechanical — the gate registry **refuses to register a gate with
no declared negative control**, and `nopekit gate selftest` runs every control and
fails any gate that passes its own known-bad fixture.

This is the single highest-leverage rule in the file. An agent that is good at
writing plausible code will write plausible validators, and plausible validators are
worse than none: they launder assumption into apparent proof.

## 6. Check agreement across representations

The same fact usually lives in several places produced by different programs. Check
that they still agree, mechanically.

Read back the *generated* artifact — another program's output — and compare it to the
model that produced it. They came from one source, so they should agree; the check
exists to catch frame errors, mirroring bugs, unit slips and regressions, all of
which are invisible until they are expensive.

The version of this that pays best: prove that several independent coordinate frames
— a model feature, a component anchor, an opening in one part, a pocket in another —
all land on the same axis, to a stated tolerance.

## 7. Wrap external solvers in converging loops

Autorouters, meshers, CFD and FEA do not simply succeed. They converge, or they do
not, and a single invocation is not a pipeline.

Three transferable parts:

- **Vary something between attempts.** Identical input to a deterministic solver
  gives an identical failure. Escalate the effort budget rather than retrying.
- **Restart from clean state.** Retrying on top of the previous attempt's output
  lets its partial result ride through as a fixed constraint, so the solver inherits
  the same dead end.
- **Fail loudly rather than degrade silently.** Exit non-zero with the count of what
  is still unresolved, instead of handing the next stage something that will be
  refused anyway.

Solver *input munging* is part of the gate and must be recorded as such: if you
rewrite the exported problem before handing it over — marking a layer, pinning a
constraint, injecting a rule class — that rewrite is a design decision under rule 3.

## 8. Adversarial review moves the spec before anything is built

Not after. The most expensive mistakes are the ones that get built.

Run N independent reviews along *different* dimensions, each trying to break the
design on its own terms — structure, manufacturability, sourcing, thermal, usage,
cost, safety — and let the findings change the numbers before a single output is
generated. Packs ship their domain's lens list.

A review pass of this kind, run on one small bracket spec, changed four numbers: a
clearance that was arithmetically zero once a floating part settled under load; a
bearing edge whose fillet would have turned weight into lift; a cantilever loaded at
its tip whose root needed a real block rather than a drawn one; and a pre-existing
carve nobody had noticed. None of it had been built. All of it would have been scrap.

## 9. Separate what is proven from what is assumed — in public

The readiness report is the deliverable. Its credibility comes entirely from what it
refuses to claim. The shape:

> *Ready to manufacture: routed, rule-check clean, package exported, mechanicals
> fit-checked. **It is unverified in physical hardware.***

That second sentence is why anyone believes the first. Every PROVEN row cites the
gate and the evidence file that proved it. Everything else is listed as physical,
blocked, stale or assumed, with the reason.

Three statuses that must never blur into "pass":

- **skipped** — the gate did not run (its tool is missing). Nothing was proven.
- **errored** — the gate crashed. Nothing was proven.
- **stale** — it passed, but inputs have changed since. Nothing is proven *now*.

And the honesty extends to what a green build actually means. A clean compile is not
a working product; a clean rule check is not a correct circuit; a converged solve is
not a validated design. Say so, in the report, every time.

*In public* is where the project site comes in — `nopekit site build` renders the
same ledger as a page, and the separation stops being a section heading and becomes
a thing you look at: the headline verdict above the fold, the physical and assumed
claims never blurred into the proven ones, how old each result is, and the failing
part lit up in the viewer rather than described in coordinates. It renders the
ledger and never computes truth; if a number on the page is wrong, the ledger is
wrong. Contract in [`docs/SITE_CONTRACT.md`](docs/SITE_CONTRACT.md).

## 10. Cheap inner loops, or there is no loop

Iteration speed is a design requirement, not a nicety. If validating a change costs
twenty minutes, the change does not get validated.

Every pack must ship tier-0 gates: analytic, closed-form, seconds. Reserve the heavy
solver for when the cheap gate cannot answer the question *and* the design has
stopped moving. Running CFD on a hull you are still redrawing is a way of feeling
productive.

Going from a twenty-minute full rebuild to a five-second single-part build is not a
convenience. It is the difference between ten more revisions and none.

---

## Intake: evidence, not just conversation

A design conversation is the *thinnest* input a project has. The real ones are
artifacts, and users almost never volunteer them. **Ask, by name.**

Real projects are grounded in hand sketches, photographs of a competitor's insides,
digitised layouts, caliper measurements, and datasheet figures precise enough that no
conversation would ever have produced them — the depth of a sensing element below its
package's moulded face, say, which sets an air gap that sets the whole design.

So: ask for sketches, for a photo of the closest thing that already exists, for
whatever they have taken apart, for calipers on anything this must fit, for
datasheets of parts already chosen, for the standard it has to meet.

Then **extract**: every ingested artifact must produce a record of what was actually
read out of it and which parameters and claims that grounds. An artifact nobody
extracted from is decoration. `nopekit ask` lists what is still missing;
`nopekit inputs --unextracted` lists what arrived and was never read.

## Growing: the capability gap

When a claim has no gate, that is not a blocker — it is the system's growth
mechanism, and it has a procedure. Do not guess at a tool.

1. **Name the unvalidated physical quantity.** Not "make sure it's stable" —
   "directional stability at 4 m/s cruise".
2. **Classify it.** Closed-form? Then it is a tier-0 gate and you can write it now.
   Needs a solver? Continue.
3. **Choose the standard open tool for that claim class**, and record why that one.
4. **Install it pinned and reproducible**, with a smoke test that proves the install
   works before any project data touches it.
5. **Wrap it as a gate with a negative control** — and demonstrate the control.
   A CFD gate that cannot tell a hull from a brick is not a gate.
6. **Record provenance** for every solver setting (mesh density, turbulence model,
   convergence criterion) exactly as for any other constant.
7. **Emit a pack**, so the next person does not repeat steps 1–6.

Offer the cheap analytic gate first and defer the heavy solver until the design has
stopped moving. Say the real cost out loud — install size, run time — and let the
human choose.

Full procedure in [`docs/EXTENSION_PROTOCOL.md`](docs/EXTENSION_PROTOCOL.md).

---

## What this is not

It is not a guarantee that the thing will work. It is a guarantee that you know
which parts have been checked, by what, and what remains to be found out by building
one. That is a smaller claim, and it is the one that survives contact with reality.
