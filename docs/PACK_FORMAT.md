# Pack format

A **pack** teaches an agent one physical or manufacturing domain: how to generate
artifacts for it, how to validate claims in it, what the real-world constraints are,
and how to attack a design from its point of view.

Packs are the plugin layer. They are ordinary directories — no build step, no
registration, no central authority. Drop one in and it is found.

## Layout

```
packs/<name>/
  pack.json          # tier 1 — the manifest. ~20 words. Always affordable to read.
  PACK.md            # tier 2 — ~150 lines. Loaded when the domain is relevant.
  references/*.md    # tier 3 — loaded only for the specific task at hand.
  gates/*.py         # evaluators. Each declares a negative control.
  proposers/*.py     # tools that propose options or evidence; never evaluators (optional)
  generators/*.py    # model -> domain artifact (optional)
  views/*.py         # viewgens: model -> a view the site renders (optional)
  skills/<name>/SKILL.md   # what an agent needs for one task in the domain; read, never
                           #   installed: `nopekit packs skills` prints them (optional)
  lenses.md          # adversarial review dimensions for this domain
  sourcing.md        # real-world procurement and process constraints (optional)
  scaffold/          # starter model + claims for a new project in this domain (optional)
  selftest/          # baseline.json (the known-good design) + the known-bad fixtures
```

## Three-tier progressive disclosure

This is the central design constraint, and everything else follows from it. **An
agent must be able to know about forty packs while loading two.**

| Tier | File | Size | When it enters context |
|---|---|---|---|
| 1 | `pack.json` | ~20 words | always — this is how packs are discovered and matched to gaps |
| 2 | `PACK.md` | ~150 lines | when the agent is working in this domain |
| 3 | `references/*.md` | whatever it takes | only for the specific task at hand |

`nopekit packs list` reads **only** tier 1 and never imports Python. If your
manifest's `description` needs three sentences, the pack is doing too much — split
it.

Tier 3 is where the depth goes. A CFD pack's `references/meshing.md` can be nine
hundred lines; it costs nothing until an agent is actually meshing.

**Skills** (architecture phase 6, SPEC 37) sit beside tier 2: `skills/<name>/SKILL.md`,
one per task the domain needs — set up a solver case, read its log. They are read,
never installed as Claude Code skills: an installed skill would be there in every
project on the machine, live pack or not. `nopekit packs skills` prints each live pack's
`PACK.md` and then its skill files (`--pack <name>` for one that is not live), and the
nopekit skill tells the agent to read them before it works in that domain. `pack
validate` refuses a `skills/<name>/` directory that holds no `SKILL.md`.

## `pack.json`

```json
{
  "name": "fdm-print",
  "version": "0.1.0",
  "description": "Validates that a solid is printable on a filament machine: bed fit, wall thickness, overhang, bridging.",
  "settles": ["bed fit", "wall thickness", "overhang angle", "bridge span", "print time"],
  "claim_classes": ["manufacturability", "fdm", "additive"],
  "provides_gates": ["fdm.bed_fit", "fdm.wall_thickness", "fdm.overhang", "fdm.bridges"],
  "provides_views": ["print_part"],
  "provides_generators": [],
  "requires_tools": [],
  "requires_python": ["trimesh", "numpy"],
  "install": {
    "trimesh": { "version": "4.5", "check": "python3 -c 'import trimesh'",
                 "methods": [{ "how": "pip", "run": "pip install trimesh==4.5.*", "root": false }] }
  },
  "max_tier": 1,
  "lenses": ["printability", "part orientation", "assembly order", "material"],
  "licence": "Apache-2.0",
  "origin": "extracted from a production FDM project",
  "basis": "model-only",
  "provides_proposers": [
    {"id": "fdm.slicer_estimate", "basis": "established",
     "description": "runs the installed slicer on the part and keeps its time estimate",
     "run": "proposers/slicer_estimate.py"}
  ]
}
```

`settles` is the **matching vocabulary**. When `nopekit gap` finds a claim with no
gate, it scores every manifest's `settles` and `claim_classes` against the claim's
quantity and the words of its mechanism (`"mechanism": {"how": …}`, weighed below the
quantity) to suggest packs. Write the phrases a person would actually use for the
quantity, not your internal gate names.

`provides_views` lists the view ids `views/*.py` registers. It is tier 1 for two
reasons: a reader deciding whether to install a pack wants to know it comes with a
viewer, and it is the string every `Locator.view` in the pack's verdicts will
address — declared here, the pair can be checked without importing anything.

`origin` matters. Say where the pack came from — extracted from a real project,
built by the extension protocol, ported from a paper. It tells the next reader how
much to trust it.

`basis` is the default basis of the pack's evaluators (see "Basis" below);
`provides_proposers` declares its proposers (see "Proposers").

## Basis

Every evaluator says where its verdict's logic comes from (SPEC 38), in a fixed
order of preference:

| Basis | When |
|---|---|
| `established` | the verdict is an existing non-AI tool's own answer: trimesh's `is_volume`, omc's `checkModel` |
| `sourced` | the logic follows a cited outside source: a textbook formula, a standard, a datasheet |
| `model-only` | the model wrote the logic with no outside source — even when it calls a tool's primitives |

Declare the pack's default in `pack.json` (`"basis": "sourced"`) and override one
evaluator with `@gate(..., basis="model-only")`. The bundled packs are the examples:
beam-analytic is `sourced` (closed-form formulae in `references/formulae.md`) with
`beam.input_sanity` `model-only` (the pack's own sign and unit bands); cad-solid is
`model-only` with `cad.is_volume` `established` — and `cad.watertight` stays
`model-only` on purpose, because its verdict is its own edge count and trimesh's
answer is only evidence. Use the highest basis you honestly can, and say why when it
is lower. The basis is shown in `gate list`, `gate show`, the report and the page; it
changes no status, no qualification and no rho. `pack validate` requires every
evaluator of a bundled pack to resolve to one; elsewhere an undeclared one is a note,
and reads `undeclared`. `grounded` is not a basis word (it is the paper's word for a
whole system).

## Proposers

A **proposer** is a tool a pack ships to propose options or evidence — a parts search,
a layout optimiser, a slicer's estimate. Its output is an input; it settles nothing.
So it is never registered as an evaluator and needs no negative control. Declare each
in `pack.json`:

```json
"provides_proposers": [
  {"id": "sourcing.parts_search", "basis": "sourced",
   "description": "one line", "run": "proposers/parts_search.py"}
]
```

`id` is dotted and distinct from every gate id in the pack; `basis` is the proposer's
own (there is no pack default for proposers); `description` is one line of at most 200
characters; `run` is a `.py` file under `proposers/`. `nopekit propose <id> [args]`
runs it as `python <run> [args]` with the project as its working directory (a flag of
the script's goes after `--`: `nopekit propose <id> -- --limit 5`): it writes
every file it produces under `$NOPEKIT_PROPOSAL_DIR` (`$NOPEKIT_PROJECT` names the
project) and exits 0. Each file becomes an input record with `proposer` set, and the
input's basis is looked up from this declaration — never stated by the input. A
non-zero exit, a run past `--timeout`, or a run that wrote anywhere in the project
outside that directory (its ignored scratch aside) ingests nothing, naming each path
it moved. `pack validate` checks every declared proposer exists and every
`proposers/*.py` is declared (files starting with `_` are helpers); it never imports
or runs one.

## `PACK.md`

Tier 2. Roughly 150 lines. Cover, in this order:

1. **What this pack settles** — the claims it can close, in plain language.
2. **What it cannot settle** — the adjacent things people will assume it covers and
   it does not. This section prevents more damage than the first one.
3. **Gates**, each with its tier, what it measures, its threshold source, and the
   known-bad fixture it fails on.
4. **Units and frames, and WHICH OBJECT each gate judges.** State them
   explicitly, in the first paragraph. Unit and frame confusion is the most common
   cross-pack defect and it is entirely preventable — "one part, in print
   orientation, bed at z=0" and "the assembly, placed, in the assembly frame" are
   different enough that a reader who is told neither will get it wrong. List the
   key families with their primary spelling, their fallbacks and their resolution
   order; see "Projection keys" below.
5. **The physics in one paragraph** — enough that an agent can tell whether a result
   is absurd.
6. **Common failure modes** in this domain, and what they look like in a verdict.
7. **Node names**, if the pack emits a `model3d` view — see "Views and locators"
   below. This is an interface, not an implementation detail, and it has to be
   published somewhere a gate author will read.
8. **Where to look next** — which `references/` file covers what.

Write it for an agent that is competent but has never used this domain's tooling.

## Gates

```python
from nopekit.gates import gate, GateContext
from nopekit.models import Verdict, Tier, NegativeControl

@gate(
    id="fdm.overhang",
    title="Unsupported overhangs within the printable angle",
    claims=["manufacturability", "fdm"],       # claim ids OR claim TAGS
    tier=Tier.BUILD,
    settles="overhang angle",
    requires_python=["trimesh", "numpy"],
    negative_control=NegativeControl(
        fixture="selftest/steep_cone.py",
        note="a 70-degree cone: every face past the 50-degree limit, nothing else wrong",
    ),
)
def overhang(ctx: GateContext) -> Verdict:
    ...
    return Verdict(gate="fdm.overhang", passed=worst <= limit,
                   measured=worst, limit=limit, units="deg",
                   detail=f"worst face {worst:.1f}deg vs {limit}deg limit ({n} faces past)",
                   evidence=[report_path])
```

Rules, all enforced:

- **`negative_control` is mandatory.** The registry raises without it. A gate that
  cannot demonstrate failure is a logger — see rule 5 in `METHOD.md`.
- **A gate id names a directory.** Its cached verdicts live in
  `.nopekit/verdicts/<gate id>/`, so the registry refuses an id containing `/`, `\`,
  `..` or `:`, and an id that differs from a registered one only in case (on macOS
  and Windows those are one directory). Dotted and pack-prefixed — `fdm.overhang` —
  is the convention, and every bundled id already follows it.
- **Declare `requires_*` honestly, and ship the install recipe.** A gate whose tool is
  missing reports SKIPPED and its claim goes BLOCKED, visibly — and the skip message
  names the command that fixes it, from the manifest's `install` block. BLOCKED is a
  call to action; a pack that leaves the reader to work out how to get its solver has
  done half a job. Do not design the pack to be comfortable without the tool it wraps.
- **When any one of several tools will do, say so with `requires_one_of`.** Entries
  are `"python:<module>"` or `"tool:<executable>"`, and `availability(spec)` is ok
  only when at least one is present (on top of every `requires_tools` and
  `requires_python` entry); with none it names them all: `requires one of python
  manifold3d, tool blender, tool openscad (none found)`. Do not probe for the engine
  inside the gate and skip: `cad.clash` did exactly that, and on a machine with
  trimesh but no boolean engine it skipped its own baseline and its own control while
  `availability` said it could run — a skip the registry could not see coming.
- **A pass is `True`, and nothing else is.** `passed` must be a `bool`, or a 0-d
  object whose `dtype.kind == "b"` such as `numpy.bool_`. `"false"`, `"no"`, `None`,
  `1.0` or `2` on a verdict that is neither skipped nor errored turns it into an
  error: `gate reported passed=<repr> (<type>); a verdict must say True or False`.
  `measured` and `limit` must be `None` or a real number that is not a bool — `"n/a"`
  or `True` is an error naming the type, and NaN or ±inf is an error mentioning
  `non-finite`, with the field cleared to `None`. numpy scalars are fine; `run_gate`
  converts them to plain `bool`, `int` and `float`. This rule exists because
  `{"passed": "false"}` once rendered `[ok]`: every non-empty string is truthy, and
  `bool()` was the whole check.
- **A skip means the declared tooling is absent — nothing else.** On the pack's own
  `selftest/baseline.json` and on its own known-bad fixture, a gate may skip only
  when `availability(spec)` fails. A gate that skips itself there while its tools are
  present is broken: `nopekit gate selftest` reports it not-ok (`skipped on its own
  known-bad input while its tools are present: <reason>`) and CI fails the baseline.
  A control that skips never fires, so it is not a control — a fixture that deleted a
  key its gate needed once passed the suite's invariants 3 and 6 as "honestly
  blocked". (In a host project, skipping because the project does not publish a key
  is still honest: the claim goes BLOCKED and says why.)
- **Declare the real tier.** A fifteen-minute gate is tier 2, however much you wish
  otherwise. Miscategorising it breaks everyone's inner loop.
- **One dense line in `detail`.** Bulk output goes to `ctx.out_dir` and is cited in
  `evidence`. A verdict is one line of context; a sweep of forty gates must stay
  readable.
- **Tag bindings are a two-sided contract, and the CLAIM side is where it goes
  wrong.** A gate's `claims` list is the set of vocabularies its result is relevant
  *evidence* for, so `beam.deflection` listing `["structural", "stiffness",
  "deflection"]` is correct: a sagging beam genuinely bears on a claim about
  structural adequacy. The mistake is on the claim side — tagging a narrow
  assertion with a broad vocabulary. A claim that says "root stress stays under
  half of yield" tagged `structural` gets covered by every structural gate in
  every installed pack, so a deflection failure makes a *stress* claim read FAIL
  and the reader goes hunting in the wrong place.

  So: **gates list everything they bear on; claims carry the narrowest vocabulary
  that describes what they actually assert.** Say so in `PACK.md` and give the
  vocabulary explicitly, because a pack that publishes its tag names is a pack
  whose claims can be written correctly by someone who has never read its code.
  Your tag table is read beside other packs' tables: a broad word another pack
  also lists (`connectivity`, `clearance`, `manufacturability`) covers a claim
  with the gates of both. `nopekit doctor` names each tag that gates of two or
  more installed packs bind (its `pack-tags` row), and `nopekit check` notes
  each one a claim carries.

  The one gate that *should* bind broadly and narrowly at once is a **validity
  guard** — a slenderness check protecting beam theory, a Biot check protecting
  lumped capacitance, a Reynolds check protecting a correlation. When one of those
  trips, every other number in the domain really is untrustworthy, so dragging the
  whole domain down is the correct behaviour. Ship one; it is usually the most
  valuable gate in the pack — and make it the **prerequisite** of the gates it
  guards (next section): a broad binding reaches only the claims that share its
  tags, and a claim bound narrowly to the analysis it guards read Checked while the
  guard failed (S-51).
- **Ship at least one tier-0 gate.** A pack of only expensive gates has not finished
  its job.

### Prerequisites (`needs`)

```python
@gate(id="beam.deflection", ..., needs=["beam.model_validity"])   # exact ids, this pack's
```

A gate names, in `needs`, the gates that must be **established** — a pass, current —
before its own verdict counts. Under a prerequisite that failed, errored, skipped, is
unqualified or is not registered, the gate is **not run** (neither it nor its control)
and reads Skipped: `prerequisite failed: <root>` when the root failed, `prerequisite
not established: <root> (<why>)` otherwise, naming the root through a chain. Its own
crash still stands — a crash is never made quieter — and its own missing tool,
self-skip or refusal too, unless the root crashed: then the claim reads as loud as a
crash. Under a
prerequisite invalidated or unrun it runs, and its verdict reads Stale. The verdict
cache is not touched: the edge is not part of rho, so a guard that recovers re-runs
nothing. `gates.plan` runs every gate after its prerequisites, and `check --only X`
runs X's.

Declare an edge only when all four hold (P2.2-D12), and say why at the decorator:

1. **The prerequisite is a validity guard**: every way it can fail means the
   dependent's number does not apply. An analysis is not a guard — a buoyancy fail
   says the design is wrong, not that freeboard is meaningless — and turning the
   second fail into a skip would hide a measurement. A guard that also measures
   (fails for a reason that leaves the dependent's number valid) is not one either.
2. **The edge is isolated**: the prerequisite PASSES the dependent's own known-bad
   control. Otherwise the guard pre-empts the control wherever both run, and the
   dependent's admission shows nothing about the inputs it judges. `pack validate`
   checks it (below) and `tests/test_packs.ControlsAreIsolated` checks every
   bundled edge.
3. **tier(prerequisite) <= tier(dependent)**: the registry refuses the inversion,
   which would drag a solver into a cheaper loop.
4. **Both gates are in this pack**: a pack's controls and baseline are sealed to it,
   and a prerequisite in another pack would make isolation depend on that pack's
   version. A project's `gates/` may name any id; one nothing registers reads "not
   registered", and `nopekit doctor` names it.

The registry also refuses a `needs` cycle (named `a -> b -> a`, each with its pack), a
need that names the gate itself, a glob or a duplicate. `Verdict.blocked_by` and
`blocked_kind` are the spine's mark for a gate not run behind a prerequisite;
`run_gate` clears both on whatever a gate returns.

**In the graph** (architecture phase 3a, `graph.py`): each evaluator is a node with
its pack, its basis and its tier; its `needs` are `needs` edges, the claims it covers
(its `claims` and tags, through the registry) are `settles` edges, and what its
served verdict entry read are `reads` edges to the values it read. Nothing a pack
declares changes for this: the graph reads what the registry and the verdict cache
already hold.

### Which side of the limit passes (`comparator`)

```python
return Verdict(gate="fdm.min_wall", passed=thin >= limit, measured=thin, limit=limit,
               comparator=">=", units="mm", detail=...)
```

Every verdict that reports a finite `limit` says which side of it passes:
`comparator` is one of `<=`, `<`, `>=`, `>`, `==`, `!=`, or `between` (a band, whose
upper bound a verdict does not carry). Set it where you set the limit, on every branch
that reports one; a branch with no one-sided reading reports no limit. `run_gate`
keeps a known one (a `Comparator` is normalised to its value) and turns any other on a
pass or a fail into an error naming the seven. It is the input of the verdict's
margin — `claims.margin`, a signed fraction of the limit, carried on every JSON row
beside the value — which nothing else computes; without one the margin is
`no-comparator`, never a guess (a pass and a fail read from `(passed, measured,
limit)` alone differ only in the generous direction at equality). Every bundled gate
sets it (`tests/test_goalposts.EveryLimitNamesItsSide`). **Compare what you
report**: round the value first, then judge it, or a verdict reads `0.5 mm (limit 0.5
mm)` beside a FAIL.

`settles` is the registration's word for the quantity a gate measures, and the spine
stamps it on every verdict. A claim whose acceptance condition names that quantity,
in the verdict's units, is **compared** with the verdict's value: a pass whose value
does not meet the claim's condition reads Failing (`acceptance condition not met`),
whatever limit the gate judged against. Name `settles` as a claim author would name
the quantity, and report the units a claim would state.

### A claim's limit (`ctx.acceptance`)

```python
@gate(id="bracket.deflection", claims=["stiffness"], settles="tip deflection", ...)
def deflection(ctx):
    acc = ctx.acceptance("C1")           # the claim's acceptance condition
    measured = round(float(ctx.params["deflection"]), 4)
    return Verdict(gate="bracket.deflection", passed=acc.holds(measured),
                   measured=measured, limit=acc.limit, units="mm",   # what YOU compute in
                   comparator=acc.comparator.value)
```

**A limit lives in `claims/`, never in a gate.** `ctx.acceptance(claim)` returns
the acceptance condition — quantity, comparator, limit, units — of the claim an exact
id names, else of the claims carrying it as a tag (one condition among them), as a
copy. The read is recorded as `acceptance:<claim>` — the condition alone, never the
claim record: moving the limit re-runs exactly the gates that read it, and editing the
statement re-runs none. It raises — the gate errors, honestly — when nothing is named
or tagged so, when a tag's claims carry different conditions, or when the condition
has no limit (or one that is not a number). A pass is then **held to the acceptance
condition it read**: it needs a finite value, in that condition's units, that the
condition admits, or `run_gate` errors it (`passed at 0.7 mm, which does not meet the
acceptance condition it read (C1: tip deflection <= 0.5 mm)`). A fail needs nothing.
**Report the units your arithmetic computes in, never `acc.units`**: the units check
holds only units the gate states for itself, and one that repeats the claim's back
agrees with any claim — C1 restated as `<= 600 um` read Checked at "0.6997 um", which
is 700 um.

On a control the acceptance condition is the known-good design's own. A project's
`selftest/known_good.py` states the claims it was calibrated against as `CLAIMS` and
hands them as its ledger, so moving the live claim never changes a control's severity
(invariant 5's failure, for a project gate) and re-runs no control. Between the two,
only a **limit** may differ — `limit`, and a band's `limit_hi`: the claims it names,
their quantity, comparator and units, which limits they state, and a one-sided
condition's stray `limit_hi` must be ones a qualification run read
(`acceptance-shape:<claim>`). And a limit may differ only where the qualification
moved it: each acceptance condition a qualification run read — the known-good
control's, or a mutation run's on a branch the known-good design never takes — is
re-run where it was read with each of its limits moved on its own (x0.1, x0.5, x2,
x10), and the gate's value and units must not move — a value is measured, never
chosen by the limit it is judged against (`its known-good value moves when the
acceptance condition it reads moves`). A gate may still lie at a limit no run visits;
only an independent evaluator catches that (SPINE_CONTRACT's Limits). A pack's
`selftest/baseline.json` states the claims its design was calibrated against under
`_claims`, a list of claim records with their `id`; the known-good control's ledger
is those claims (`packs.baseline_context`), and each known-bad fixture hands the same
ones (`Claim.from_dict` over the baseline's `_claims`), never the host's. A baseline that states
none gives an empty ledger, and a pack gate that reads a condition then errors on its
own control. cfd-openfoam's `cfd.pressure_drop` reads the claim tagged
`pressure-drop` this way (issue #15 D2): before, it judged against a parameter,
`cfd_dp_max_pa`, beside the claim's limit, and a claim at 0.5 Pa beside the parameter
at 0.6 Pa read a pass at 0.52 Pa that the claim rejected.

### Operating context

```python
@gate(id="t.span", claims=["c1"], settles="sag",
      operating_context={"load_n": (0.0, 40.0)},      # closed; None leaves an end open
      negative_control=...)
```

An evaluator may declare the **range of its read set it was qualified on**: a
correlation fitted between two Reynolds numbers, a model valid below a load. Each key
is spelled as the gate reads it (`ctx.param`'s rule, scoped first), and **every pass
must read every key** — `run_gate` errors one that never did: the context is part of
the read set. The spine judges the current value of each spelling the run read
against the range: **outside it a pass does not count** — its claim reads Gap,
`outside operating context: t.span : load_n = 60, qualified on [0, 40]`, or Assumed
when the claim carries an owned fallback (`Claim.fallback`, attributed through the
signing channel) — **and a fail still does** (Failing: a result never loses its power
to fail). Inside the range nothing changes: a moved input reads Stale until a check
run. The known-good control must lie inside the range, or the gate is unqualified
(`known-good outside its operating context`). A mutation counts wherever it lands —
the walk reads the gate's own value against its own limit, which no regime changes.
Registration refuses a malformed context (not a mapping, an empty key, a pair that is
not two finite numbers or `None`, both ends open, `lo > hi`). Declare a range on what
the gate READS: a fitted range on an intermediate quantity it computes (a Rayleigh
number from temperatures and a length) is a guard gate of its own, or a range on its
inputs.

## Amber runs your evaluators on moved designs

`nopekit model trace` (architecture phase 3b) rebuilds the project's model with each
choice an unconfirmed assumption sources moved -10% and +10%, and runs every covering
evaluator on each moved design, to show a Checked pass the assumption's error would
flip (amber). It runs in a temp copy of the project — `ctx.root` is the copy — through
the check run's own rules (`verdicts.sweep` with nothing recorded, then the one
composition), and it writes nothing in the project. An evaluator must therefore read
through `ctx`: a path built from the project's location rather than `ctx.root`, or a
file written beside the model, reads or writes the wrong tree. Amber changes no status.

## The surface a gate sees

Everything a pack can set, read or call, in one place. `tests/test_contracts.py`
fails when any of these classes gains a public field, property or method that is
not listed in its own block below (PLAN R-14): the page a pack author reads first
must not describe a smaller spine than the one their gate runs in.

```python
@dataclass
class GateSpec:                        # what @gate(...) builds and registers for you
    id: str                            # "fdm.overhang": dotted; the prefix is the key scope
    title: str = ""
    claims: list[str]                  # claim ids OR claim TAGS this gate is evidence for
    tier: Tier = Tier.INSTANT          # the REAL cost (see "Declare the real tier")
    pack: str = ""                     # stamped from the pack's directory name when blank
    requires_tools: list[str]          # EVERY executable must be on PATH
    requires_python: list[str]         # EVERY module must be importable
    negative_control: NegativeControl | None   # mandatory: the registry raises on None
    description: str = ""
    settles: str = ""                  # the quantity it measures, for gap matching
    entry: str = ""                    # "module:function", for out-of-process discovery
    requires_one_of: list[str]         # AT LEAST ONE "python:<module>" / "tool:<exe>"
    needs: list[str]                   # PREREQUISITES: exact ids of this pack's gates that
                                       #   must be established first (see "Prerequisites")
    operating_context: dict            # {key: (lo, hi)}: the range of its read set it was
                                       #   qualified on; outside, a pass does not count
    basis: str = ""                    # established | sourced | model-only; "" takes the
                                       #   pack.json default (PACK_BASIS), else "undeclared"

@dataclass
class NegativeControl:
    fixture: str                       # "selftest/steep_cone.py" or "module:function"; blank is refused
    expect: str = "fail"               # "fail" (must not pass) or "error" (must crash); else not ok
    note: str = ""                     # what is wrong with the fixture, and nothing else
    good: str = ""                     # the known-good control's fixture, spelled like `fixture`;
                                       #   "" = the pack's selftest/baseline.json (P2.3)

@dataclass
class Verdict:                         # what a gate returns (or a (bool, detail) tuple, or a dict)
    gate: str                          # overwritten from the spec: a gate cannot lie about its id
    passed: bool = False               # True or False and nothing else (see Rules above)
    claims: list[str]                  # stamped from the spec
    measured: float | None = None      # a real number or None: never a bool, never NaN
    limit: float | None = None         # likewise
    units: str = ""
    detail: str = ""                   # ONE dense line
    evidence: list[str]                # files under ctx.out_dir
    duration_s: float = 0.0            # measured by run_gate, never declared
    tier: Tier = Tier.INSTANT          # stamped from the spec
    skipped: bool = False              # could not run here; never a pass (see Rules above)
    skip_reason: str = ""              # user-facing: what is missing, and how to get it
    error: str = ""                    # the gate crashed: NOT the same as failing
    pack: str = ""                     # stamped from the spec
    locators: list[Locator]            # WHERE it applies, only when genuinely known
    rho: str = ""                      # the hash of everything it read: set by the sweep
                                       #   that records it, never by the gate
    cpu_s: float = 0.0                 # CPU seconds, child processes included: measured
    unqualified: str = ""              # the SPINE's mark for an evaluator refused at its
                                       #   version; run_gate clears whatever a gate sets
    blocked_by: list[str]              # the SPINE's mark: not run, these prerequisites not
                                       #   established; run_gate clears whatever a gate sets
    blocked_kind: str = ""             # the spine's too: the first root's kind ("failed",
                                       #   "errored", "skipped", "unqualified", "not-registered")
    comparator: str = ""               # which side of `limit` passes: <= < >= > == != between
    settles: str = ""                  # stamped from the spec: the quantity, for the claim's
                                       #   comparison
    outcome -> str                     # property: "error" | "skipped" | "pass" | "fail"
    ok -> bool                         # property: outcome == "pass"; a skip is never ok
    def render(self) -> str            # "[FAIL] fdm.overhang : worst face 63.2deg vs 50deg limit"

@dataclass
class GateContext:                     # a gate's one argument — a traced view, never the sweep's
    root: str                          # the project root
    ledger: Ledger                     # a COPY of the project state, with NO verdicts in it
    model: Any | None                  # the loaded model, or None
    params: dict                       # the projection, flattened: read numbers here. READ-ONLY
    out_dir: str                       # scratch and evidence
    tier: int                          # the sweep's tier; never a reason to lower the standard.
                                       #   A TierRead in your view: every use is a read (below)
    log: Callable[[str], None]         # one-line progress sink
    extra: dict                        # where a dict-returning fixture lands; THIS gate's own copy
    pack: str; key_scope: str          # stamped by run_gate: whose namespace param() reads
    memo: SweepMemo | None             # the sweep's file memo: load_file's alone; None outside a sweep
    trace: GateTrace | None            # what this view records into; yours to leave alone
    def scopes(self) -> list[str]      # ["fdm", "fdm-print"]: this gate's scopes, best first
    def param(self, name, default=None, *, scope=...) -> Any   # scoped first: see below
    def pack_param(self, name, default=None) -> Any
    def first_pack_param(self, names, default=None) -> Any
    def first_pack_param_named(self, names, default=None) -> tuple[Any, str]
    def require_param(self, name) -> Any          # raises rather than compare with None
    def acceptance(self, claim) -> Acceptance     # the acceptance condition of the claim an id or tag
                                                  #   names; recorded; a pass is held to it
    def out_path(self, *parts) -> str             # an evidence path under out_dir, dir created
    def with_extra(self, extra) -> GateContext    # a copy with `extra` merged over
    def load_file(self, path, loader=None) -> Any # a file several gates read: loaded once per
                                                  #   sweep; it and every file the loader
                                                  #   opened recorded for EVERY caller
```

**`Verdict.unqualified` is the spine's, never a gate's** (P2.1). The resolver and
the sweep set it — the reason as a token, with `error="unqualified: <token>"` — when
a gate is not qualified at its current version (a control that did not hold, a
mutation that passed, P2.3), and a claim with such an evaluator reads Gap,
`unqualified: <gate> : <why>`, even beside a pass.
`run_gate` clears it on whatever your gate returns, as it clears `rho`: a gate
that could set it could make its own crash read Gap, quieter than a missing tool.
So a gate that returns `unqualified="…"`, or wraps its own exception as
`error="unqualified: …"`, reads as the crash it is (Skipped, `errored:`), never
as a refusal. Raise, or return `error=`, when your gate cannot evaluate; never
claim a qualification state.

**The context a gate receives is a traced view of its own.** `run_gate` never hands a
gate the sweep's context. Every read of `ctx.params`, of a claim through `ctx.ledger`,
of a file the gate opens, and every question it asks of a path, is recorded, because a
verdict is only as current as the inputs it read — the verdict cache keys each verdict
by exactly those reads (`rho`). So:

- **`ctx.params` is read-only.** `ctx.params["load_n"] = 0` — or `update`, `pop`,
  `setdefault`, a write into a nested dict — raises `GateInputWriteError` ("a gate
  cannot write another gate's inputs"), and the gate reads as an error. Before, one
  mutable dict was handed to every gate in a sweep, so one gate's write was the next
  gate's input and no verdict could show it. A gate that wants a modified projection
  builds one: `ctx.params.copy()` and `copy.deepcopy(ctx.params)` hand back plain
  dicts, writable all the way down, and count as reading everything they copy.
  (`dict(ctx.params)` copies the top level only; a nested dict in it is still the
  read-only view.)
- **`ctx.extra` is the gate's own shallow copy.** What one gate stores there is gone
  for the next. fdm-print once kept a cross-gate mesh cache on it, and the second gate
  to want the part got a cache hit that opened nothing — so nothing recorded that its
  verdict depended on the file.
- **`ctx.memo` is `load_file`'s, and nobody else's.** It is one per sweep and every
  gate holds it, so it is a `SweepMemo`: a handle `load_file` opens and nothing else
  can. `ctx.memo.get(k)`, `ctx.memo[k]`, `ctx.memo[k] = v`, `k in ctx.memo`,
  iterating it, `dict(ctx.memo)` — each raises `GateMemoError`, and the gate reads
  as an error. `ctx.memo is None` is the one question to ask of it (outside a sweep
  there is none). What slipped through while it was the plain dict: two gates shared
  a parsed table through `ctx.memo["materials"]`; the second opened nothing, its
  verdict keyed no file, and an edit to the table re-ran the first gate alone while
  the second kept its PASS — the `ctx.extra` hole, one field over. A fixture's
  context holds the same handle, so a fixture that fills it is an unusable control.
- **`ctx.load_file(path, loader=None)`** is how several gates share one file. `path`
  resolves against `ctx.root`; `loader` (a module-level function such as
  `trimesh.load_mesh`, not a lambda made in the gate body, which never hits) defaults
  to reading the bytes. The result is memoised once per sweep, and **every call is
  recorded as a read of the calling gate**, hit or miss — the named file and every
  file, directory and subprocess the loader touched on the miss: a `.gltf`'s `.bin`
  buffers, an `.obj`'s `.mtl`. (A hit once recorded the named file alone, and
  `fdm.bridge_span` stayed Fresh after the buffers under its `.gltf` moved.) An edit
  to any of those between two gates of one sweep loads it again. A hit is the same
  object for every caller: copy it before you change it. Outside a sweep
  (`ctx.memo is None`, a hand-run script) it simply loads. It is the only way to
  cache anything across gates: a parsed table is a `loader` (a module-level
  function), never a value left in `ctx.memo`.
- **A module-level memo is emptied before every run.** A `functools.lru_cache` or
  `functools.cache` in your gate module or a helper it loads — a global, or a cached
  method of a class the module defines — is cleared before each gate, fixture and
  known-good run, so every run opens its files itself and they are its inputs. (What
  slipped through: the admission control runs the gate first, in the same process, so
  a memoised file read was a hit on the real run, the entry keyed no file, and a
  zeroed limit file left a Fresh PASS.) Any other memo — a module-level dict filled
  from a function, a `global` rebound from one, a mutable default argument — cannot be
  emptied from outside: `nopekit doctor`'s `memos` row names it. Share a file between
  gates with `ctx.load_file`.
- **Asking whether a file is there is reading it.** `os.path.isfile`, `exists`,
  `isdir`, `getsize`, `pathlib.Path.exists`/`is_file`, a `glob` of a literal path: a
  path under the project or your pack that the gate asked about is one of its inputs —
  a regular file by its bytes, a directory by its kind, a missing one as missing — so
  the verdict goes stale when a named file appears, vanishes or changes. (What slipped
  through before: `os.stat` raises no audit event, so `modelica.source_hygiene`
  skipped a named `.mo` that was not there yet, recorded nothing, and kept a Fresh PASS
  after it appeared.) A listing is keyed by each entry's name, kind and size, since
  `DirEntry.stat()` is invisible otherwise. Not keyed: questions about paths outside
  the project (every `shutil.which` asks dozens) and mtimes (they differ per
  checkout) — never decide a verdict on either.
- **`ctx.ledger` holds no verdicts.** A gate that read other gates' verdicts would put
  verdicts inside its own content address. Claims are readable; `ctx.ledger.claim(id)`
  makes that claim an input. `requirements` (what the person asked for) is readable
  and read whole (traced), like `claims`. A project's claims may name your evaluator
  ids in their mechanism's `evaluators` — a cross-check, never coverage — so a renamed
  id warns there (`check`, `doctor`); coverage stays your `claims=` ids and tags.
- **Reading `ctx.tier` makes the tier an input.** A gate may pick a cheaper path by
  it — `if ctx.tier < Tier.SOLVE`, `PATHS[ctx.tier]` — and then its verdict is keyed
  on the tier it read: a sweep at tier N serves an entry recorded at N or above (the
  costlier path's answer, the highest first), and re-runs one recorded below. (What
  slipped through: nothing recorded the read, and a PASS from the tier-0 path was
  served Fresh to `check --tier 2`, whose path failed the design.) Your control is
  keyed the same way — one shown on the tier-0 path does not qualify the tier-2 path,
  and a gate that passes its known-bad input on either path is qualified on none.
  A crash is remembered on its own path too: a tier-2 solver that crashes supersedes
  the tier-2 PASS — a plain `check` shows the crash and says `run nopekit check --tier
  2` — and a PASS on the cheap path never clears it; only a run on that path that
  measures does. (What slipped through: a tier-0 PASS forgot the tier-2 crash, and
  `check --tier 2` served the PASS the crash had superseded.) Your control's crash is
  remembered on its own path the same way: a control that fires on the cheap path
  never clears its crash on the costlier one, and a crash of the cheap path's control
  never refuses the costlier PASS whose own path's control fired.
  In your view `ctx.tier` is a `verdicts.TierRead`: it compares, indexes, hashes and
  formats as the int it is and `isinstance(ctx.tier, int)` holds, but it is not an
  `int` subclass (CPython reads an int subclass's index without asking it), so
  `json.dumps(ctx.tier)` raises — write `int(ctx.tier)`. A gate that never reads the
  tier is keyed exactly as before.
- **`Verdict.rho` and `Verdict.cpu_s` are not yours to set.** `run_gate` measures
  `duration_s` and `cpu_s` (the `os.times()` delta, child processes included — a gate
  that shells out to a solver is not free) and clears `rho`, which the sweep computes
  from the trace.
- **Load a helper by path with `nopekit.modelio.load_path(path)`**, not with
  `importlib.util.spec_from_file_location` under a fixed module name. It compiles the
  bytes on disk (a stale `__pycache__` once ran 7.0 after the source said 8.0), salts
  the module name with the path (two copies of one pack never run each other's
  helpers), and keys the helper — so editing it re-runs whatever depended on it. Loaded
  at module level, the helper is recorded in the code of the gate module that loaded
  it. Loaded inside a gate function, a fixture's `make` or `known_good.context` — at
  run time — its code and what it read at its import are reads of that run, whether
  it ran or was served to a second caller from the cache. (What slipped through: only
  the module-level load was recorded, so a limit loaded by path inside a gate kept its
  PASS Fresh after it moved, and a known-good design that loaded the live model by
  path was the live design with no key.) A helper imported by name from outside the
  pack or project — a monorepo's `shared/` put on `sys.path` — is recorded the same
  way: any Python file that is not installed (under the interpreter's own trees, or in
  a `site-packages` / `dist-packages` directory) is the gate's code, never a
  third-party instrument, and never runs from a stale `__pycache__`.
- **Name a module you import at run time with a string literal**, or load it with
  `load_path`. `importlib.import_module("rules")` — like an `import` inside a function
  body — is read into the gate's code; `importlib.import_module(name)` is not, and is
  keyed only by the first run in a process to load it (a later one is served it from
  `sys.modules` and opens nothing). `nopekit doctor` names every such call under
  `dynamic-imports`. Either way the stock import system loads it, from a
  `__pycache__` that still validates; `load_path` never does.

`outcome` is the one derivation of what a verdict says: `"error"` if `error` is set,
else `"skipped"` if `skipped`, else `"pass"` if `passed is True`, else `"fail"`.
`ok`, the `[ok  ]`/`[FAIL]`/`[skip]`/`[ERR ]` tag and the claim status all read it,
so an error or a skip can never be counted as a pass by one reader and not by
another.

## Projection keys: scope, meaning, and resolution order

A pack does not own a namespace. Every installed pack reads the same flat
projection — `{name: value}` — so two packs can want the same word for different
things, and nothing about either pack is wrong when they do.

It happened in the default set. `cad-solid` read `bbox_mm` as the **assembly**
envelope; `fdm-print` read it as **one part in print orientation**. A project with
both installed — an assembly and printed parts, which is every mechanical project —
could satisfy exactly one of them:

```
[FAIL] fdm.bed_fit : 480x186x87 mm vs 208x208 usable ...   # the assembly, on a printer bed
[skip] cad.bounding : bbox_mm is absent                    # the other choice
```

Three mechanisms keep that from happening again, and a pack should use all three.

### 1. Scope the key

`GateContext.param` resolves a **pack-scoped** spelling before the bare one. The
scope is the gate id's namespace — the part before the first dot, `fdm` for
`fdm.bed_fit` — so a project publishes `fdm.bbox_mm` and `cad.bbox_mm` and each
gate reads the one it means. A single-domain project keeps writing `bbox_mm` and
nothing changes.

**Resolution order, in full, highest priority first:**

1. `<scope>.<name>` — flat (`params["fdm.bbox_mm"]`) or nested
   (`params["fdm"]["bbox_mm"]`); a model may group its projection by domain
2. `<pack name>.<name>` — `fdm-print.bbox_mm`, because people type that too
3. `<name>` — the bare key
4. the last dotted segment of `<name>`, so a gate may ask for `config.beam_mm`
   against a flattened projection

For a family of synonyms (`ctx.first_pack_param`), **every scoped spelling in the
order declared, then every bare spelling in the order declared.** A scoped key
always beats an unscoped one, whatever its rank in the family, because it is the
only one of the two that is an explicit statement about *this* pack.

```python
bbox = ctx.first_pack_param(("part_bbox_mm", "bbox_mm", "bbox"))   # scoped first
limit = ctx.pack_param("bed_x_mm")                                 # one key, scoped
raw = ctx.param("budget_usd", scope=None)                          # deliberately unscoped
value, key = ctx.first_pack_param_named(FAMILY)                    # and WHICH spelling won
```

`ctx.param(name)` is scope-aware by default, so a pack written before this existed
gets the behaviour without an edit; pass `scope=None` for a key that genuinely
belongs to the project rather than to any pack, or `scope="other"` to read another
pack's namespace deliberately.

### 2. Name the object in the key

Scoping resolves a collision; it does not tell a reader what the number *is*. So
the primary spelling of a key says which object it describes, and the bare word
stays as a documented fallback:

| Pack | Primary | Fallback | Means |
|---|---|---|---|
| `cad-solid` | `assembly_bbox_mm` | `bbox_mm` | the assembled product's envelope |
| `fdm-print` | `part_bbox_mm` | `bbox_mm`, `bbox`, `footprint_mm` | one printed part, in print orientation |
| `cad-solid` | `process_min_wall_mm` | `min_wall_mm` | the thinnest wall a process ALLOWS (a limit) |
| `fdm-print` | `part_min_wall_mm` | `min_wall_mm`, … | the thinnest section MEASURED in a part |

**Say the frame and the object in `PACK.md`, in the first paragraph** — which
object each gate judges, and which coordinate frame it expects the geometry in. It
is not a detail: `fdm-print`'s gates want the mesh in print orientation with the
bed at z=0, a project exported parts in assembly coordinates, and a flat panel came
back as a 201.7 mm unsupported span. True of that pose, useless about the print,
and nothing in the verdict said which frame it had measured.

### 3. Declare the vocabulary so it can be diffed

`selftest/baseline.json` already carries **every key any gate in the pack reads**,
with a `_notes` line per key. Add `_aliases` — `{primary key: [other accepted
spellings]}` — and the declaration is complete, because a fallback spelling is
where two packs collide without either baseline showing it:

```json
"_aliases": {
  "part_bbox_mm": ["bbox_mm", "bbox", "footprint_mm"],
  "part_min_wall_mm": ["min_wall_mm", "thinnest_wall_mm"]
},
"_notes": {
  "part_bbox_mm": "[x, y, z] extent of ONE PRINTED PART, in its PRINT ORIENTATION with the bed at z=0, mm. ..."
}
```

`nopekit doctor` reads those two maps from every **installed** pack and reports
any key that two of them read with different declared meanings — naming both packs,
what each means by it, the scoped spellings that separate them, and whether the
project is publishing the ambiguous bare key right now:

```
[warn] pack-keys  'min_wall_mm' is read by cad-solid and fdm-print with different meanings
                  AND THIS PROJECT PUBLISHES IT — publish cad.min_wall_mm, fdm.min_wall_mm
                  or use each pack's own primary key (cad-solid: process_min_wall_mm,
                  fdm-print: part_min_wall_mm). cad-solid: Thinnest wall the PROCESS allows,
                  mm - a limit, not a measurement; fdm-print: Thinnest section MEASURED
                  anywhere in the part, mm
```

Two packs that describe a key identically are not reported. The comparison is on
the `_notes` line each pack wrote — a declaration, not an inference — so the
warning says the packs *declare it differently*, which is exactly what is known.

`pack.json` may also set `key_scope` explicitly, but should not: it is derived from
the dotted gate ids (`fdm.bed_fit` → `fdm`), and a scope stated twice is a scope
that goes out of step with the gates. Set it only when a pack's gate ids do not
share one prefix.

## Negative controls

The fixture must be bad **in the specific way the gate claims to detect**. Change one
physically meaningful thing, in the direction the gate cares about — a mesh with one
face deleted, a part scaled past the bed, a beam at a quarter of its section depth,
a matrix with the diodes removed.

A fixture that is bad in some *other* way (corrupt file, empty mesh) proves the gate
handles garbage, not that it measures what it claims.

Fixtures live in `selftest/` and expose `make(ctx)` returning either a new
`GateContext` or a dict merged into `ctx.extra`. Reference them as
`selftest/<file>.py` or `module:function`. Prefer the first: every byte under
`selftest/` is part of the control's key. A `module:function` fixture that is a plain `.py`
module of your own is loaded fresh and keyed by the code closure recorded as it loads;
a package's `__init__` or an installed module records none, and its control re-runs
its fixture on every `check` (admission review, round 1, C: the form once
recorded nothing, and a fixture edited into a no-op stayed admitted). A file a fixture
module reads at import — `selftest/baseline.json`, a table outside `selftest/` — is
recorded with its closure too, so an edit to it re-qualifies the gate (round 1, D:
a known-bad span read from `inputs/` at import was keyed nowhere). What a module only
asks about (`os.path.exists`), lists, or takes from the environment at import is not:
do that in `make`, inside the control's trace window.

**A fixture builds its own context.** The `ctx` a fixture receives is a writable copy
of the host's, traced: what it writes there stays there — it never reaches the sweep
the control ran inside — and every read it makes of the host's params is recorded as a
host read, which a SEALED fixture (below) never makes. Return
`dataclasses.replace(ctx, params={...})` with the known-bad projection, as every
bundled fixture does; the gate then runs on that, read-only like any gate. A fixture
module is loaded fresh from its bytes, and its code is recorded with the control.

### Fixtures must be SEALED

A fixture states **everything its gate reads**. It never layers its known-bad
values over the host project's projection.

This looks like an over-precaution and is not. The overriding form —
`{**ctx.params, **known_bad}` — wins on every key it names, so it reads as safe.
But gates resolve synonym families and derived quantities, so a key the fixture
never mentions can still arrive from the project and neutralise the control.

It was observed live while building these packs. A hull fixture raised the centre
of gravity to make a boat unstable. The host project happened to state a
waterplane inertia — a perfectly honest thing to state, and better than the pack's
own rectangular fallback. That one key replaced the inertia the fixture's severity
had been computed against, the metacentric height came out at **+4.33 m**, and the
gate **passed its own known-bad fixture**. Six more controls in the same pack had
the identical hole, each defusable by a different innocuous project value.

> **A control whose severity depends on the host project's numbers is a control
> that passes in some repositories and fails in others. That is the same as having
> none — and worse, because it looks like having one.**

So:

```python
def top_heavy(ctx):
    """fluid.metacentric — the same hull with its mass raised above the metacentre."""
    return dataclasses.replace(ctx, params={**_baseline(), "kg_m": 0.62})
    #                                       ^^^^^^^^^^^ the PACK's baseline,
    #                                       never ctx.params
```

Mesh fixtures need the same discipline: returning a bare `{"meshes": ...}` merges
only into `ctx.extra` and leaves the gate reading its *threshold* from the host
project. A wall gate handed a quarter-thickness plate and no minimum-wall figure
does not fail — it **skips**, and a skip is not a control.

`tests/test_packs.py::ControlsAreSealed` enforces this by running every control
against an empty projection as well as the baseline: a sealed fixture behaves
identically, an inheriting one skips or flips. The outcome alone cannot see a fixture
that layers the pack's *whole* baseline over the host (`{**ctx.params, **BASELINE,
<bad>}`) — it fires identically over both — so the control's trace is read too:
any key of the host's `ctx.params` the fixture read (or its gate read, through a
context the fixture handed back unchanged) is named, and `pack validate` refuses it.

Project-local fixtures are exempt. A fixture in your own repo deriving from your
own model is correct — it keeps the control one change away from *this* design.
The rule is for packs, which ship to strangers.

### `selftest/baseline.json` — required

A single JSON object: a plausible, physically coherent projection for this domain,
carrying **every key any gate in the pack reads**, describing a design that **every
gate passes**.

It exists because a gate with no parameters to read SKIPS, and a skipped control
never fires — so the gate ships unproven while the suite reads green. That is the
precise failure this project exists to prevent, and it has already happened here
once: a gate declared a negative-control fixture that was never written, and nobody
noticed because the gate was skipping for a missing dependency.

The baseline is also the pack's teaching example. Include a `_description` naming
the object, a `_notes` map of key → one line on what it is and its unit, and an
`_aliases` map of key → the other spellings its gates accept; an agent that reads
the baseline should be able to write a model this pack can gate, without reading
any of its code — and `nopekit doctor` can only diff two packs' vocabularies
against each other if both of them wrote one down. **State the key under the
spelling the pack teaches** (`part_bbox_mm`, not `bbox_mm`), so CI proves the
primary key is the one actually read.

CI asserts three things: every gate passes the baseline, every control fires against
it, and nothing skips. "Nothing" is exact: a skip on the baseline or on a control is
allowed only when `availability(spec)` fails — the gate's declared tooling is
genuinely absent on that machine, which is how a runner without trimesh reports a
mesh pack honestly instead of failing it. A gate that skips itself on either input
while its tools are present fails.

`nopekit gate selftest` runs every control and **fails any gate that passes its own
known-bad input**.

### Qualification: both controls, at the gate's current version

Declaring a control is what the registry checks. Whether the gate's verdicts COUNT
is decided in the project, every `check`, from the gate being **qualified at its
current version** (invariant 9, GLOSSARY §2): its known-good control passed and its
known-bad control failed, both run at that version, and — for a gate not loaded from
the bundled packs — every conclusive mutation of its known-good control failed.

- **The known-good control** is, first match: the fixture named by
  `NegativeControl.good`, handed exactly what the known-bad fixture is handed; else,
  for a pack's gate, the pack's own `selftest/baseline.json`; else, for a project's,
  `selftest/known_good.py`'s `context(ctx)`. A gate with none is *known-bad shown*,
  never qualified (`known-good not run`). A `good` fixture that reads the live design
  it is handed is unusable (`known-good control reads the current design`), and in a pack
  it is a seal finding too (invariant 5).
- **Both controls reach the gate through the same channel**: the `ctx.extra` keys
  each hands its gate (the spine's `pack_dir`, `pack_dirs` removed) must be equal,
  else the gate is unqualified (`channels differ`). A gate that answered `extra` and
  `params` differently could pass one half and fail the other while showing nothing
  — so a pack whose known-bad fixtures hand their input through `ctx.extra` declares
  `good=` fixtures that hand the baseline through `extra` too (cad-solid's
  `selftest/good_meshes.py`, sourcing's `selftest/good_boms.py`). And a check run
  never hands a gate `ctx.extra`, so a gate not from a bundled pack whose controls
  reach it through `extra` is unqualified as well (`known-good and known-bad via
  ctx.extra`): its controls test a path `check` never takes. For the same reason a
  gate not from a bundled pack is handed no `ctx.model` on any run, and a verdict of
  it that read a ledger value no qualification run read does not count (`check run
  reads another ledger`): a pack's baseline hands its gates an empty ledger, so a
  gate that reads a claim in `check` never qualifies outside the bundled packs.
- **The mutation pass** (gates outside the bundled packs: a project's own, and a pack
  under `.nopekit/packs/`, `~/.nopekit/packs` or `$NOPEKIT_PACK_PATH`): each value
  the known-good run read is pushed along a fixed ladder until the gate's own
  measured value lands 15% past its own limit, the push then aimed at the smallest
  change that lands; that run must fail. A run that skips or errors is
  *inconclusive*, counted neither way; a value that never lands is not mutated. It
  runs in process, at tier 0 only, on copies, with its scratch outside the project
  and every pack, and it writes nothing.
- **The version is everything the outcome could depend on**: the spine, the gate's
  own code (its module and every helper it loaded, by bytes), every source file
  under the owner's `selftest/` — the pack's, or the project's — the
  `NegativeControl` fields, and whatever either control's fixture and the gate read
  on its input, every mutation run's included. Each qualification is recorded beside
  the gate's verdicts, as `.nopekit/verdicts/<gate id>/control-<rhoC16>-<out8>.json`
  — the known-bad half, the known-good half (`good`), the walk (`mutation`) and
  `admitted` (`paired`, `reject-only`, `no`), which the reader re-derives from the
  recorded facts — tracked and never rewritten.
- **`check` runs the qualification whenever none is on record at that version** — a
  fresh clone of a project that committed its entries runs none; editing the gate, a
  fixture or a known-bad asset runs it again. When only a fixture's own code moved
  (the bracket's fixtures build through the project's model, so any model edit moves
  them), both builders alone are re-run and what each builds is compared with what
  its recorded control fed the gate: equal values re-qualify it with no gate call and
  no new file. A fixture that swaps `ctx.model` or `ctx.memo` is never re-qualified
  that way: the qualification runs.
- **Unqualified is a Gap, not a fail.** The gate's function is never called, its
  verdict carries the spine's mark (`Verdict.unqualified`, the reason as a token —
  `known-bad:pass`, `known-good:fail`, `mutation:1/2` — which `report.HUMAN` words),
  and its claim reads Gap: `unqualified: <gate> : <what does not hold>`, beside a
  passing gate too. A gate qualified at an earlier version and not at this one reads
  Stale — `not yet qualified at this version — the next check run qualifies it`,
  naming `nopekit check --tier <t>` when its verdict took a costlier path — and
  one never qualified at all reads Gap until a check run qualifies it. A missing
  tool is a skip, never an unqualified gate.
- **Qualified means it can fail, and nothing more.** The line says which facts held:
  `<gate> : known-good pass · known-bad fail · mutation 2/2 fail → qualified`.

**A project fixture is handed the known-good design, not the live one.** When the
project has `selftest/known_good.py` defining `context(ctx)` — the design every
control is one change away from, every field stated — a project gate's fixture
receives that context, and builds its known-bad input from a design that passes.
What slipped through before (S-07): handed the live design, which in the reference
project fails on purpose, the identity fixture `return ctx` was reported "correctly
failed … ~64x worse" and certified nothing; on the known-good design it passes its
own known-bad input and is unqualified. P2.3 runs the gate on the same context as its
known-good control: it must pass there. `context` is handed the host with no
params, an empty ledger, no `extra` and no model — `root`, `out_dir` and `tier` are
kept, and a read of the tier keys every control built on it — so it must state its design itself (the bracket's states every Config field),
and a file it reads is keyed as an input of every control built on it. What slipped
through before: it was handed a copy of the live host, and a `context` that kept
`ctx.params` passed the live design through unkeyed, so the identity fixture was
admitted on a design that later passed. A gate that reads an acceptance condition
(`ctx.acceptance`) reads the claims `context` hands as its ledger: state them in the
module (`CLAIMS`, as the bracket's does) — the claims the design was calibrated
against, never the live `claims/` files. A project with no `known_good.py` hands its
fixtures the live host, and the control entry says so (`"host": "live"`) — and, with
no known-good control unless a gate declares a `good` fixture, its gates are known-bad
shown and their claims read Gap. **Pack
fixtures always get the live host** — they must be SEALED (above), which the seal
detector checks by running them; a clean host is never substituted for a leaky
fixture.

**A control writes into its own scratch.** Every control runs with `ctx.out_dir` set
to `.nopekit/out/controls/<gate id>/`, emptied before each run — never the sweep's
`out_dir`. A known-bad fixture that writes a mesh or a report there can neither
overwrite the evidence a cached PASS cites nor leave a stale file for the next run to
read as an input. In pack mode, and in `pack validate`, every control gets a
temporary directory and nothing is written into the pack.

In a project, `nopekit gate selftest` re-runs every control — never served from the
record — and files exactly the entries `check` would (an unchanged control finds its
file already there and writes nothing); `--no-record` files nothing. What slipped
through before any of this (S-05): nothing ever ran a declared control during a
sweep, so a gate that returned `True` with a control on paper produced PROVEN rows.

## Views and locators

A pack's part of the project site is its views and the evidence its evaluators write:
a file a verdict names as evidence is copied into the board posters
(`site/assets/evidence/<gate>/…`). There is no other site partial format; the page's
words, statuses and layout are the build's, never a pack's.

A pack can also draw what its gates measure. `views/*.py` holds **viewgens**, which
are to the project site what gates are to the report: same context shape, same
`requires_*` declarations, same registry. A pack that emits a view in one of the
kinds the site already knows gets visualisation for free — and, more importantly,
its gates get somewhere to point.

```python
from nopekit.site import viewgen, ViewContext, derive_explode
from nopekit.models import View, ViewKind

@viewgen(id="assembly", kind=ViewKind.MODEL3D, title="Assembly",
         requires_python=["trimesh"], gates=["cad.clash"])
def assembly(ctx: ViewContext) -> View | None:
    """The assembled part, one node per body."""
    meshes = build_meshes(ctx.model)     # whatever this pack's generator produces
    if not meshes:
        return None                      # nothing to draw is not an error
    src = ctx.write_asset("assembly.glb", export_glb(meshes))
    return View(id="assembly", kind=ViewKind.MODEL3D, title="Assembly", src=src,
                meta={"nodes": sorted(meshes),
                      "explode": derive_explode(bounds_of(meshes))})
```

The six kinds — `model3d`, `image`, `chart`, `table`, `field`, `diagram` — and what
each is addressed by are in [`SITE_CONTRACT.md`](SITE_CONTRACT.md). Extensibility is
in the data, never in shipped code: a pack does not ship JavaScript, because the one
artifact whose job is to be trusted when a gate says something is wrong must not
depend on code nobody reviewed.

A chart view with no `evidence_of` states its own numbers. Its `series` takes the
bound chart's spellings (below), and a point can also be an `{x, y}` object or a bare
number (its x is then its index). Put its axes in `data.x` and `data.y`, or in
`meta.x` and `meta.y`. Its `limit` is `{value, comparator, label, axis}` (or a list);
the spine draws the first limit on each axis. Its `current` is `{x, y}` (or a list),
and the build writes the distance to the y limit beside it. `marker`, `now` and
`markers` (`[{x, y, label}]`, as the analytic packs write their current design) are
read as `current`, the first of them present; a marker's own `label` is not drawn,
as the build writes the distance beside the point. The spine bakes the Vega-Lite spec
of this chart too (`page.view_chart`). It does not draw a series' `style`. A verdict's locator on the chart draws its `target` series heavier,
or a line at its `position[0]`.

Rules:

- **Returning `None` is normal.** A CAD viewgen in a project with no geometry has
  nothing to draw. That is not a failure; `nopekit site build` records it as
  `empty` and moves on. Do not raise, and do not emit an empty view that renders as
  a broken box.
- **Declare `requires_python` / `requires_tools` the way a gate does.** A viewgen
  whose exporter is missing is reported as *unavailable* with the module named. A
  view that is absent because trimesh is not installed otherwise looks exactly like
  a view the project never had. List each tool in the manifest's `requires_tools`
  too: that list holds the tools of the gates and of the views.
- **A tool that PATH says too little about gets a check.** A view module may define
  `TOOL_CHECKS = {"<tool>": check}`, where `check(root)` returns `(ok, words)`.
  `nopekit doctor` prints `<tool>: <words>` on the tool's row, a warning when `ok`
  is false. Without a check the row says found, or not on PATH with the views it
  leaves out and that no status depends on them. cfd-openfoam's check
  (`views/_pv.py`) says which ParaView build `pvbatch62` is and whether its renderer
  control passed. A check must be quick and draw nothing: `doctor` calls it on every
  run.
- **Write assets through `ctx.write_asset`**, never by hand into `site/assets/`.
  Only what the context recorded is known to be live, so a file written behind its
  back either survives forever or gets swept the first time the cleaner is made
  stricter. `write_asset` returns the site-relative path that goes in `View.src`.
- **Write byte-stable assets** (architecture phase 5, SPEC 32). The site is
  committed, and `write_asset` writes only when the bytes differ, so a rebuild that
  judged nothing new changes no tracked byte. A viewgen that stamps a clock, a random
  id or an unordered dict into its GLB or SVG rewrites the asset on every build and
  dirties `git status` every time.
- **The representation is the first `model3d` view by order**, else the first image
  or diagram view (`page.representation`): it heads the front page, beside the
  verdict sentence. Give the view that best shows the whole design the lowest
  `order`.
- **Node names are an interface the pack must publish.** Whatever a `model3d`
  viewgen calls its nodes is what every gate in that domain must use in its
  locators, and `PACK.md` is where that scheme belongs. It is an interface between
  two files written months apart, and interfaces drift.
- **`derive_explode` is a first draft, not an answer.** It measures the bounding
  boxes and stacks the parts along the thinnest axis, which saves transcription and
  nothing else: it does not know that the lid comes off before the board, or that
  two bodies are one sub-assembly. Assembly order is intent, not geometry. Pass
  `overrides=` to correct it — they merge per mover, so fixing two parts costs two
  entries, and nodes group by the `__` in `lid__boss_a` (a double underscore,
  because single ones are ordinary inside part names like `guide_roller`).

### Figures bound to a verdict (`evidence_of`, `recipe`, `BoundContext`)

A picture of what a gate measured (the pressure field behind a pressure drop) is a
**bound viewgen** (issue #15): it names the gate whose served verdict entry it draws,
and the spine hands it that entry's evidence and nothing else, checks what it drew,
and only then publishes it beside the claims the gate covers. The page says which
result each picture is from, and a missing tool changes no status.
`docs/SITE_CONTRACT.md`, "Figures bound to verdicts", has the whole path.

```python
import json, subprocess, tempfile, os
from nopekit.site import viewgen, BoundContext
from nopekit.models import View, ViewKind

@viewgen(id="flow.speed", kind=ViewKind.IMAGE, title="Speed of the flow",
         description="OpenFOAM solved the field. ParaView drew it.",
         evidence_of="flow.pressure_drop",            # the gate whose served entry it draws
         recipe=("views/_scene.py", "views/_scales.py"),   # files whose bytes key the memo
         requires_tools=["pvbatch62"])
def speed(ctx: BoundContext) -> View | None:
    path = ctx.entry.path("summary.json")     # a verified evidence file, or None
    if path is None:
        # Cited but withheld: say the spine's reason, word for word. Never
        # cited (the gate stopped before it wrote one): nothing to draw, nothing to say.
        for cited, why in ctx.entry.withheld.items():
            if cited.endswith("summary.json"):
                ctx.note(f"summary.json: {why}")
        return None
    with open(path, encoding="utf-8") as fh:
        summary = json.load(fh)
    field = ctx.bind({"<out>/flow/U": summary["fields"]["U"]})   # {path: sha256}
    if field is None:
        return None                           # ctx.bind said why in the warnings
    with tempfile.TemporaryDirectory() as tmp:
        png = os.path.join(tmp, "speed.png")
        subprocess.run(["pvbatch62", "views/_scene.py", field["<out>/flow/U"], png],
                       check=True, capture_output=True, timeout=120)
        with open(png, "rb") as fh:
            src = ctx.write_asset("speed.png", fh.read())
    return View(id="flow.speed", kind=ViewKind.IMAGE, src=src,
                meta={"caption": "The speed in the channel, from the run of this result.",
                      "alt": "A channel coloured by speed",
                      "scale": speed_scale(summary),   # your pack's: constants x an evidence value
                      "marks": [{"at": 0.25, "text": "upstream plane"}]})
```

- **`evidence_of="<gate id>"`** binds the viewgen to one gate. Name a gate of your own
  pack. The kind is `image` (a PNG still), `chart` (its series as the payload), or
  `model3d` or `field` (a GLB, or a glTF that holds its buffers inside it as
  `data:` URIs: ParaView's own export, as it writes it); `table` is refused (a gate's rows are its evidence
  already). A bound viewgen is never called with a `ViewContext`.
- **`recipe=(...)`** names the files, beside the module, whose bytes key the memo
  (paths inside the pack or project). The spine draws a figure again only when the
  module, a recipe file or the served entry moves; a rebuild that judged nothing new
  calls no tool. The tool's identity is not part of the key.
- **The context** gives no model, no parameters and no claim's acceptance. The design
  now may be another design than the served entry's, and a picture drawn from it would
  be published as that entry's evidence; a limit is withheld because a colour range
  keyed to a limit is a second judgment of the claim.

```python
class BoundEntry:                            # ctx.entry: the verdict entry it draws
    gate: str                                # the gate id
    name: str                                # "<rho16>-<out8>", the entry's file name
    fresh: bool                              # the resolver's word for it
    files: dict                              # {portable path: absolute path}: ONLY the
                                             #   evidence files whose bytes the entry's
                                             #   manifest names
    withheld: dict                           # {portable path: why it is held back}
    def path(self, basename) -> str | None   # a verified file by its base name

class BoundContext(ViewContext):             # root; ledger, its claims' acceptance empty;
    entry: BoundEntry                        #   model None; params and extra {}
    scratch: str                             # .nopekit/out/figures/<gate>/<entry>/<view>/
    view_id: str
    def bind(self, inputs) -> dict | None    # {path: sha256} -> {path: absolute path}, or
                                             #   None, said in the warnings. A path is
                                             #   "<out>/..." or inside the project; each
                                             #   digest must be in a verified evidence file
                                             #   and each file must hash to it. Hashed again
                                             #   after the call: a move discards the picture
    def write_asset(self, name, data) -> str # into the scratch; returns the name View.src
                                             #   takes; over 2 MiB it raises, writes nothing
    def write_live(self, name, data) -> str  # a 3D file published only under the ignored
                                             #   assets/live/ (on this computer only)
    def note(self, text) -> None             # a build warning, under the view's id
    def asset_path(self, name)               # raises: nothing reaches site/ unchecked
```

- **What it returns.** A still or a 3D view: `src` is the name `write_asset` returned,
  and `data` is empty. A chart: no `src`, and its payload in `data`. `meta` may hold
  `caption` (ASD-STE100 site text, scanned as such), `alt`, `scale` (or `scales`),
  `marks`, `light` and `tool` (written into the sidecar as given). Write at most one PNG and one 3D file
  (`.glb`, or a `.gltf` whose every buffer and image is a `data:` URI: one that names
  a file beside it is refused, because the page would fetch bytes nothing checked). A
  still may carry its 3D file beside it: write both with `write_asset`, and the spine
  publishes the 3D file on this computer only until an export re-runs the entry, then
  commits it (cfd-openfoam's pressure still does this). Hand over your tool's file as
  it wrote it: the spine checks no accessor, so hold your files to the Khronos glTF
  validator in your tests.
- **The colour scale** is one object that gives both your pixels and the page's HTML
  legend, so the two cannot disagree. Draw no colour bar into the PNG.

  ```jsonc
  { "quantity": "speed", "units": "m/s", "lo": 0, "hi": 0.02,
    "stops": [ { "at": 0, "colour": "#440154" }, { "at": 1, "colour": "#fde725" } ],
    "over": "#ff00ff", "under": "#808080",          // past an end: never the end colour
    "over_word": "...", "under_word": "...",
    "ticks": [ { "at": 0.5, "value": 0.01, "text": "0.01 m/s" } ],
    "source": [ { "key": "u_upstream_m_s", "lo": 0, "hi": 2.0 } ],   // what the range is
    "source_text": "The scale is twice the measured upstream speed." }
  ```

  Its ends are a pack constant times a value the evidence names (`source`; in
  cfd-openfoam each end is the sum of its factor times each named value, and the
  stops are a colour map preset as the drawing tool exported it). The spine
  refuses an end equal to a limit of a claim the gate covers or to the entry's verdict
  limit, and a `source` key that holds `limit`. A doubled limit leaves the scale
  byte-identical.
- **Several scales on one picture.** A picture with two ramps (a body coloured by
  its surface pressure, its stream tubes by the speed of the air) states `scales`
  in place of `scale`: a list of scale objects as above, each with `on` set to
  `"surface"` or `"lines"`, at most one on each. The page draws one legend for each,
  its title led by a glyph (a filled swatch for a surface, a line for lines) and the
  words "On the surface" or "On the lines", side by side where there is room and
  stacked on a phone. Every rule of the one scale holds for each entry: an end equal
  to a limit, or a `source` key that holds `limit`, refuses the figure whole. A
  figure that states `scale` and `scales` both, an `on` of another word, or two
  scales on the same `on`, is refused.

  ```jsonc
  "scales": [ { "on": "surface", "quantity": "pressure, Cp", "lo": -1, "hi": 1, ... },
              { "on": "lines", "quantity": "air speed / bike speed", "lo": 0, "hi": 2, ... } ]
  ```
- **A lit picture.** `light: "scene"` says that the picture is drawn with lights and
  shade, so a pixel is darker or lighter than the key's colour; `"none"` says each
  pixel is a colour of the key. Say it in the caption too ("Light and shade make some
  colours darker than the colours in the key"). The page gives a lit still a border,
  and turns its 3D file lit, with a "Flat colours" toggle that draws each colour as
  the file holds it, the key's (`docs/SITE_CONTRACT.md`, "Lit field mode"). nopekit
  judges no pixel: hold your lit pictures to the key in your own tests, with an unlit
  twin at the same camera whose every pixel is a key colour within 2, and controls
  that fail (a lit twin, a tone-mapped twin). A figure that states neither `scales`
  nor `light` writes the sidecar it wrote before (schema 1).
- **A chart's payload** is the run's numbers, never yours:

  ```jsonc
  { "series": { "dp": [[1, 0.531], [2, 0.524], [3, 0.5222]] },   // or a list of
              // { "key", "label", "points": [[x, y], ...], "labels": [str], "hollow": bool }
    "x": { "label": "mesh level" },
    "y": { "label": "pressure drop", "units": "Pa", "scale": "log" },  // scale: optional;
              // "zero": false: a linear y that does not start at zero
    "band": [ { "x": 3, "lo": 0.52, "hi": 0.525, "label": "GCI band" } ],
    "rule": { "value": 0.1, "label": "tolerance" },               // never called a limit
    "estimates": [ { "x": 4, "y": 0.5219, "label": "extrapolated",
                     "series": "dp" } ],   // series: drawn in that series' colour
    "legend": false,      // optional: each point is named on the chart, so no legend
    "judged": false }     // optional: not of the judged quantity (a change in %)
  ```

  The judged point is a ring at the series point or the band end (`hi` or `lo`)
  whose y is the floor's value. A payload that says `"judged": false` gets no limit
  line and no ring, and the build says nothing of its units: say it for a chart of
  another quantity than the claim's, such as a settling history.

  Every number under `series`, `band`, `estimates` and `rule` must be a JSON leaf or a
  CSV cell of a verified evidence file of the entry: write the numbers you want to
  plot into the gate's evidence. A payload that holds `limit`, `limits`, `current`,
  `marker`, `now` or `markers` is refused: the limit line and the judged point are the claim's,
  which the spine takes from the floor. State `y.units` as the claim's units, or the
  page draws no limit line and the build says why. The spine bakes the whole Vega-Lite
  spec from this payload (`page.chart_spec`); the pack ships no chart code.
- **A caption says what the picture shows, never a status.** A caption with a status
  word ("checked", "failing", "inside the limit", ...) is refused; the chips beside the
  figure say the status.
- **Run your tool under your own timeout.** The spine does not stop a viewgen that
  hangs. A crash, a timeout, `None` or a refusal publishes nothing, keeps the figure the
  page had, and moves no status.
- **Byte-stable output.** The same entry draws the same bytes on every machine, or every
  teammate's build commits a new picture. Stamp no clock or random id into a PNG or a
  3D file.
- **List the view id in `provides_views`**, as for any viewgen.

### Attaching locators to a verdict

A gate that knows *where* the problem is says so, and the site stops being a report
and starts being a debugger:

```python
return Verdict(
    gate="cad.clash", passed=False,
    detail="1 interfering pair: carriage / guide_roller 0.41 mm^3",
    locators=[
        Locator(view="assembly", target="carriage",
                label="0.41 mm^3 into guide_roller", value=0.41),
        Locator(view="assembly", target="guide_roller"),
    ],
)
```

`view` is a view id; `target` is the node name (`model3d`, `field`), hotspot id
(`image`), series key (`chart`) or row id (`table`). An empty `target` means the
whole view. Severity defaults to the verdict's own outcome.

**Locate only what you genuinely know.** A confident red highlight on the wrong part
is worse than no highlight: it sends someone to inspect a part that is fine, and
once that has happened they stop trusting the overlay. An unlocatable failure
carries no locators, and the page marks the verdict as unanchored rather than
guessing.

Nothing is dropped for being undrawable, either. `nopekit site build` reports every
locator naming a view that does not exist or a node the view does not declare —
in its warnings and in `state.json` — because a gate that thinks it is drawing and
is not looks exactly like a gate that found nothing, and both ends of that mistake
are silent.

## `lenses.md`

The adversarial review dimensions for this domain — the angles a reviewer should
attack from before anything gets built. One heading each, a few pointed questions
under it. Rule 8 of the method lives here. `nopekit packs skills` prints it, after
PACK.md, for every live pack: it is how the agent reaches it.

Good lenses are specific and uncomfortable: *"the part is loaded at its tip and its
root is a cantilever — what is the free span, really, and did you measure it from
the weld or from the wish?"*

## `sourcing.md`

The real-world constraints that are not physics: vendor process rules, minimum order
quantities, lead times, finishes that are mandatory for a given contact type, stock
volatility, tiers that force a different assembly line, materials that are quietly
unavailable in your region.

This is the knowledge that makes a design orderable rather than merely correct, and
it is almost never in a datasheet.

## Validation

```
nopekit pack validate <name>
```

checks that the manifest parses and matches its directory, `PACK.md` exists and is
substantive, every `skills/<name>/` holds a `SKILL.md`, every declared gate actually registers, every gate has a negative
control, `max_tier` matches the gates, the description is one line, and every
file-based fixture exists — and then **demonstrates** the pack at tiers 0–1, gate by
gate, with the pack loaded alone: its known-good control passes (its own
`selftest/baseline.json`, or the `good` fixture it declares), its known-bad control
fails, both reach the gate through the same `ctx.extra` keys, the control still
fires against an empty host (the seal probe), it
read nothing of its host's `ctx.params` on the way (the seal, read off the trace), and
every prerequisite in its `needs` closure passes that same known-bad control
(isolation: `<gate>: control not isolated — its prerequisite <id> does not pass
<gate>'s known-bad control (…)`). A `needs` entry that is not one of the pack's own
gates is a problem too.
It loads `views/` through the loader a site build uses, and holds `provides_views`
to the view ids that register (both ways), every view's `requires_tools` and every
`TOOL_CHECKS` tool to the manifest's `requires_tools`, and every `evidence_of` to a
gate of the pack.
A gate whose declared tooling is absent on this machine is printed as a `note:` and
not demonstrated — not a problem, and not a pass either; so is an isolation check whose
prerequisite's tooling is absent (`<gate>: isolation not checked — its prerequisite
<id> is skipped here (<why>)`). Tiers 0–1 call no external
solver, so this stays seconds long; the rest waits for

```
nopekit gate selftest --pack <name> --junit <file>.xml
```

which runs the same checks at **every** tier and writes them as JUnit XML: suite
`controls` (childless only where the gate is qualified) and suite `baselines` (the
known-good half). It prints a row per pack (`beam-analytic (bundled) : 8 qualified`),
the line of every unqualified gate (every gate's under `-v`), and the summary
(`54 evaluators in 9.2s: 54 qualified, 0 unqualified, 0 skipped`).
`--pack` takes a pack name or a directory; a pack being written inside a project
(`.nopekit/packs/<name>/`) is found by name there. Outside a project,
`nopekit gate selftest` with no `--pack` demonstrates every bundled pack. Either
way nothing is recorded, a run with any unqualified gate exits 1, as does one in
which no control ran (`--allow-empty` accepts that), and `--user-packs` is needed before `$NOPEKIT_PACK_PATH` or
`~/.nopekit/packs` are searched — the machine does not get to choose which copy is
tested.

CI runs `pack validate` on every pack and `gate selftest` at the repository root,
over every bundled pack. **A pack whose gates have never demonstrated failure does
not get merged.**

## Contributing back

A pack is an ordinary directory, laid out as above: no command creates or exports
one. One built inside a user's project, under `.nopekit/packs/<name>/`, is
already the directory a PR adds under `packs/`, with no edits. The evidence that
goes with it is the selftest's own report:

```
nopekit pack validate <name>                            # what CI runs on every pack
nopekit gate selftest --pack <name> --junit <file>.xml  # every tier: the evidence
```

What slipped through (S-10): this section, the extension protocol and the
pack-authoring skill all named a `pack new` and a `pack export` command, the second
"with selftest evidence attached". Neither ever existed, and neither will (D-24);
`tests/test_docs_commands.py` now parses every command these documents print.

That is the contribution loop: someone builds an RC boat, the agent follows the
extension protocol to grow a CFD gate, and the pack comes back for the next person
who needs one.
