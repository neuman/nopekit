---
name: pack-authoring
description: Build a new atompipe pack — a reusable validation capability for a physical domain (CFD, FEA, thermal, chemical, optics, machining, sourcing). Use when a claim has no gate and the extension protocol has been followed, when wrapping a solver or analysis tool as a gate, when extracting existing validation code into a shareable pack, or when someone wants to contribute a capability back to atompipe.
---

# Authoring an atompipe pack

A pack is how a validation capability stops being a one-off. You built a gate for
your project; a pack is that gate made reusable, self-testing and contributable.

Read `docs/PACK_FORMAT.md` for the contract. This skill is the *procedure*.

## Before you start

**Have you followed `docs/EXTENSION_PROTOCOL.md`?** A pack is step 7 of that
protocol, not step 1. If you have not yet named the physical quantity precisely,
tried the analytic answer, chosen the tool deliberately and run a negative control,
you are packaging something you do not trust yet.

**Is this one domain?** A pack that settles both hull drag and battery runtime is two
packs. The test: can you write the manifest `description` in one line that a person
would recognise? If not, split.

## Procedure

### 1. Lay out the directory

No command creates a pack: it is an ordinary directory. Make the layout in
`docs/PACK_FORMAT.md` — `pack.json`, `PACK.md`, `gates/`, `selftest/`, `references/` —
under `.atompipe/packs/<name>/` in the project that needs it (or `packs/<name>/` in
the atompipe repository). `pack validate` and `gate selftest --pack` find it there by
name before it is published anywhere; `atompipe packs add <name>` opts the project
into it, so its gates also run in `check` and in a project's `gate selftest`.

Names are lowercase, hyphenated, and say the domain and the tool when the tool
matters: `cfd-openfoam`, `fea-calculix`, `fdm-print`, `pcb-kicad`, `solar-thermal`.
Bare domain names (`cfd`) claim more territory than one pack can hold.

### 2. Write the manifest first

`pack.json` is tier 1 — it is what every agent sees, always. Get `settles` right: it
is the vocabulary that matches a capability gap to this pack. Write the phrases a
person would actually say for the quantity ("bed fit", "overhang angle", "righting
moment"), not your internal gate ids.

Set `origin` honestly. Extracted from a real project that shipped? Built by the
protocol against a validation case? Ported from a paper? The next reader calibrates
their trust on that line.

### 3. Write the cheapest gate first

**Ship at least one tier-0 gate, and write it before the expensive one.** Analytic,
closed-form, seconds. It is what people will actually run five hundred times, and it
becomes the sanity check on your solver gate later.

If you genuinely cannot find a tier-0 bound for the domain, say so in `PACK.md`
under "what this pack cannot settle" — but look hard first. Almost every physical
claim has an order-of-magnitude answer sitting in a textbook.

### 3b. Write the gate for the context it actually gets

A gate never sees the sweep's context. It gets a traced view of its own, and every
read it makes — a parameter, a claim, a file, a question about a path — is recorded,
because the verdict cache keys each verdict by exactly what it read. Write for that:

- **Name the gate like a directory.** `<scope>.<what>` — `fdm.overhang`. Its cached
  verdicts live in `.atompipe/verdicts/<gate id>/`, so the registry refuses `/`, `\`,
  `..` and `:` in an id, and an id that differs from another only in case.
- **Never write `ctx.params`.** It is read-only: an assignment, `update` or `pop`
  raises `GateInputWriteError` and the gate reads as an error. It used to be one dict
  shared by every gate in the sweep, so one gate's write was the next gate's input.
  Need a variant? `ctx.params.copy()` is a plain dict you own.
- **`ctx.extra` is yours alone.** Nothing you put there reaches the next gate. To share
  a file between gates — a mesh several gates measure — use
  `ctx.load_file(path, loader=trimesh.load_mesh)`: loaded once per sweep, recorded for
  every gate that asks, hit or miss — with every file the loader opened (a `.gltf`'s
  `.bin` buffers). Pass a module-level function as the loader, and
  copy the result before changing it (every caller gets the same object). A cache on
  `extra` once made the second gate's read of a part invisible.
- **`ctx.memo` and `ctx.trace` are the sweep's, not yours.** `ctx.memo` is
  `load_file`'s handle on the sweep's file memo: `ctx.memo[k]`, `.get`, a write, `in`
  or `dict(ctx.memo)` raises `GateMemoError` and the gate reads as an error, because a
  value one gate left there was the next gate's read of a file it never opened.
  `ctx.memo is None` (a hand-run script, outside a sweep) is the one question to ask
  of it. `ctx.trace` is what your reads are recorded into; never touch it.
- **Keep no memo of your own.** An `lru_cache` at module level is emptied before every
  run, so it saves nothing across gates; a module-level dict you fill from a function
  is never emptied, and the second gate to ask gets a value whose file it never opened
  — no verdict keys it (`atompipe doctor` names it under `memos`). `ctx.load_file` is
  the one shared cache that records the file for every caller.
- **Load helpers by path with `atompipe.modelio.load_path(path)`**, never with
  `spec_from_file_location` under a fixed name: it runs the bytes on disk, never a
  stale `.pyc`, keeps two copies of your pack from sharing one helper, and keys the
  helper, so editing it re-runs the gate. At module level it joins the gate's code;
  called inside a gate, a fixture or `known_good.context`, its code and what it read
  at import are reads of that run, served or not. A helper imported by name from a
  `shared/` beside the pack is the gate's code too; only an installed library
  (site-packages) is a third-party instrument.
- **Import a module at run time by a string literal** (`importlib.import_module("rules")`),
  or with `load_path`. A name held in a variable is keyed only by the first run in a
  process to load it; `atompipe doctor` names each under `dynamic-imports`.
- **Leave `rho`, `cpu_s` and `unqualified` alone.** `run_gate` measures `cpu_s`
  (child processes included: a solver subprocess is not free) and `duration_s`, and
  the sweep sets `rho`. Anything a gate puts in them is overwritten. `unqualified` is
  the spine's mark for an evaluator refused at its version — its known-bad control
  passed — and a claim with one reads Gap; `run_gate` clears it on whatever you
  return, so a gate that sets it, or wraps its own exception as `error="not admitted:
  …"`, reads as the crash it is (Skipped, `errored:`), never as a refusal.
- **Return `passed=True` or `passed=False`, and read a verdict by its `outcome`.**
  Any other pass value (`"false"`, `1`, `None`) is an error naming its type: `"false"`
  once read `[ok]`. `Verdict.outcome` — `"error"`, `"skipped"`, `"pass"` or `"fail"`,
  in that precedence — is the one derivation every renderer calls; a helper or a check
  script that re-derives it from the three flags is the fourth copy that drifts.
- **When any one of several back-ends will do, declare `requires_one_of`**
  (`["python:manifold3d", "tool:openscad"]`) next to `requires_python` and
  `requires_tools`, and never probe for the engine inside the gate and skip there.
  `availability` cannot see a skip decided in the body: `cad.clash` once skipped its own
  baseline and its own control on a machine with trimesh and no boolean engine, while
  availability said it could run.
- **Read claims through `ctx.ledger.claim(id)`**; the ledger a gate gets has no
  verdicts in it.
- **`os.path.isfile` is a read.** A path under the project or your pack that the gate
  asks about (`exists`, `isdir`, `getsize`, `pathlib`, a literal `glob`) is an input,
  missing or not: a named file that appears later stales the verdict. `os.stat` raises
  no audit event, so this once recorded nothing, and `modelica.source_hygiene` kept a
  Fresh PASS after a `.mo` it had skipped appeared. Never decide on an mtime, or on a
  path outside the project: neither is keyed.

### 4. Build the negative control, and run it

This is the step that separates a pack from a plausible-looking directory.

The fixture must be bad **in the specific way the gate claims to detect**. Change one
physically meaningful thing in the direction the gate cares about:

- watertightness gate → a mesh with one face deleted
- bed-fit gate → the same part scaled 1.5×
- clash gate → two bodies overlapped 0.5 mm
- deflection gate → the same beam at a quarter section depth
- stability gate → centre of mass moved aft of the centre of pressure
- interference gate → the same circuit with its protective element removed

A fixture that is bad in some *other* way — a corrupt file, an empty mesh — proves
your gate handles garbage, not that it measures what it claims.

**Seal the fixture.** State everything the gate reads; never layer your known-bad
values over the host project's projection. The overriding form looks safe — it wins
on every key it names — but gates resolve synonym families and derived quantities,
so a key you never mention can arrive from the project and neutralise the control.
Observed live: a fixture made a hull top-heavy, the project happened to state a
waterplane inertia, and the gate passed its own known-bad input. A control whose
severity depends on the host project is one that passes in some repositories and
fails in others.

Use the pack's own `selftest/baseline.json` as the base, and **return a context you
built** — never edit the one you were handed. It is a traced copy of the host's: your
writes to it never reach the project's sweep, and every host value you read is
recorded against the seal.

```python
return dataclasses.replace(ctx, params={**_baseline(), "kg_m": 0.62})
```

Mesh fixtures too: returning a bare `{"meshes": ...}` leaves the gate reading its
threshold from the project, so it skips instead of failing.

```
atompipe gate selftest --only <gate-id>
```

If the gate passes its known-bad fixture, it is broken. Do not proceed. Do not
rationalise. This is the moment the whole system either earns its credibility or
quietly loses it.

### 4b. Draw what the gate measured, and point at it

A verdict says *what* is wrong. A **view** plus a **locator** says *where*, and that
is the difference between a page someone reads and a page someone debugs.

Put viewgens in `views/*.py`. They mirror gates — one context argument, the same
`requires_*` declarations, registered by a decorator — so there is no second API to
learn:

```python
@viewgen(id="assembly", kind=ViewKind.MODEL3D, title="Assembly",
         requires_python=["trimesh"], gates=["cad.clash"])
def assembly(ctx: ViewContext) -> View | None:
    meshes = build_meshes(ctx.model)
    if not meshes:
        return None                  # nothing to draw is not an error
    src = ctx.write_asset("assembly.glb", export_glb(meshes))
    return View(id="assembly", kind=ViewKind.MODEL3D, src=src,
                meta={"nodes": sorted(meshes)})
```

Then give the gate somewhere to point:

```python
locators=[Locator(view="assembly", target="back_left",
                  label="0.41 mm^3 into grip_lid_left", value=0.41)]
```

Three things to get right:

- **Publish the node names in `PACK.md`.** They are the interface between your
  viewgen and every gate that will ever locate into it, including gates you did not
  write. An unpublished naming scheme is one somebody has to reverse-engineer from a
  GLB.
- **Locate only what you genuinely know.** A confident highlight on the wrong part
  is worse than none — it sends a reader to inspect a part that is fine, and after
  that they ignore the overlay. A failure you cannot place carries no locators and
  the site says so.
- **Run `atompipe site build` and read the warnings.** It reports every locator
  naming a view or a node that does not exist. That is the check that catches the
  rename you did on one side of the interface and not the other.

A pack with no view still works; the site degrades all the way down to claims,
verdicts and provenance. But if your domain has geometry and you skip this, every
failure it finds stays a sentence about coordinates. Contract:
`docs/SITE_CONTRACT.md`.

### 4c. Get the tag vocabulary right

A gate's `claims` list is the set of vocabularies its result is relevant evidence
for. `beam.deflection` listing `["structural", "stiffness", "deflection"]` is
correct — a sagging beam really does bear on a claim about structural adequacy.

The failure is on the *claim* side, and it is the one to warn about in `PACK.md`:
a narrow assertion tagged with a broad vocabulary. "Root stress stays under half of
yield" tagged `structural` gets covered by every structural gate installed, so a
deflection failure makes a stress claim read FAIL and whoever reads the report goes
looking in the wrong place.

**Gates list everything they bear on. Claims carry the narrowest vocabulary that
describes what they assert.** Publish your pack's tag vocabulary in `PACK.md` so
someone who has never read your code can write claims that bind correctly.

Ship one **validity guard** — a gate that binds across the whole domain because it
decides whether the domain's other numbers mean anything at all (slenderness for
beam theory, Biot for lumped capacitance, Reynolds for a correlation). When it
trips, dragging every claim in the domain down with it is correct behaviour, and it
is usually the most valuable gate you will write.

### 5. Write `PACK.md`

~150 lines, tier 2. The section people skip and shouldn't is **"what this pack
cannot settle"** — the adjacent things a reader will assume are covered. That
section prevents more damage than the gate list.

State **units and frames** explicitly. Unit confusion is the most common cross-pack
defect and it is entirely preventable.

Include enough physics that an agent can smell an absurd result. A gate that returns
a number the agent cannot sanity-check is a gate the agent will trust when it should
not.

### 6. Push depth into `references/`

Tier 3 costs nothing until it is needed. Meshing guidance, solver settings and their
rationale, failure-mode catalogues, vendor process tables, worked examples — all of
it belongs here, not in `PACK.md`.

Every solver setting gets rule-3 treatment: why this value, what was tried, what it
cost. *"k-omega SST because the flow is wall-bounded with adverse pressure gradient;
k-epsilon under-predicted separation by ~30% against the validation case"* is the
sentence that saves the next person a week.

### 7. Write `lenses.md`

The angles an adversarial reviewer should attack this domain from, before anything is
built. Specific and uncomfortable beats general and polite. Compare:

- *weak:* "consider manufacturability"
- *strong:* "this part is a cantilever loaded at its tip — what is the free span,
  measured from the actual weld and not from the wish? And is the root block real
  material or a fillet you drew?"

### 8. `sourcing.md`, if the domain has real-world procurement

Vendor process rules, MOQs, lead times, finishes that are mandatory for a given
contact type, stock volatility, assembly tiers, regional availability. This is what
makes a design orderable rather than merely correct, and it is almost never in a
datasheet.

### 9. Validate, and produce the evidence

```
atompipe pack validate <name>
atompipe gate selftest --pack <name> --junit <file>.xml
```

`pack validate` checks the layout and demonstrates tiers 0–1: each gate passes the
pack's own baseline, its control fires, the control still fires against an empty
host (the seal probe), and it read nothing of its host's `ctx.params` on the way (the
seal, read off the trace). `gate selftest --pack` runs the same checks at every tier and
writes them as JUnit XML — that file is the admission evidence, and it goes with the
PR. A control that could not run for want of a tool is reported as a skip, never as
fired; say so in the PR rather than letting the file speak for it. The directory
itself is the contribution: there is no export step. A pack whose gates have never
demonstrated failure does not get merged.

## Extracting a pack from an existing project

Most good packs are extractions, not inventions — code that already survived contact
with reality.

1. **Find the assertions.** Search for `assert`, threshold comparisons, `sys.exit`
   on a check, DRC gates, anything that already refuses.
2. **Ask what claim each one was defending.** That is the gate's `claims` and
   `settles`. Often the original comment tells you, because it was written the day
   something slipped through.
3. **Separate project constants from domain constants.** A bed size is a project
   parameter; a minimum printable wall at a given nozzle is domain knowledge. Only
   the second belongs in the pack, and it belongs as a documented default the project
   can override.
4. **Build the negative control the original never had.** This is where extractions
   usually break, and it is worth knowing before you ship it to someone else.
5. **Keep the war stories.** The comment explaining *why* a check exists — the
   measurement that surprised someone, the assumption that turned out false — is
   worth more than the check it sits above. Carry it into `PACK.md` or `references/`.

## Anti-patterns

**A pack with no tier-0 gate.** Nobody will run it in the loop, so it will not catch
anything early, which is where catching things is cheap.

**A gate that returns a number without a threshold.** That is a report. A gate
compares against the claim's acceptance and refuses.

**A `description` that needs three sentences.** The pack is doing too much.

**Hard-coding one project's constants.** Bed size, material, fab house, mesh density
— all project parameters with pack-supplied defaults.

**Skipping the negative control because the gate obviously works.** Every validator
that shipped broken looked obviously working.

**A 900-line `PACK.md`.** That is tier 3 wearing a tier 2 hat, and it will blow a
context window on a task that never needed it.
