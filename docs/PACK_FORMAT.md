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
  gates/*.py         # validators. Each declares a negative control.
  generators/*.py    # model -> domain artifact (optional)
  views/*.py         # viewgens: model -> a view the site renders (optional)
  lenses.md          # adversarial review dimensions for this domain
  sourcing.md        # real-world procurement and process constraints (optional)
  scaffold/          # starter model + claims for a new project in this domain (optional)
  selftest/          # baseline.json + the known-bad fixtures gates must fail on
```

## Three-tier progressive disclosure

This is the central design constraint, and everything else follows from it. **An
agent must be able to know about forty packs while loading two.**

| Tier | File | Size | When it enters context |
|---|---|---|---|
| 1 | `pack.json` | ~20 words | always — this is how packs are discovered and matched to gaps |
| 2 | `PACK.md` | ~150 lines | when the agent is working in this domain |
| 3 | `references/*.md` | whatever it takes | only for the specific task at hand |

`atompipe packs list` reads **only** tier 1 and never imports Python. If your
manifest's `description` needs three sentences, the pack is doing too much — split
it.

Tier 3 is where the depth goes. A CFD pack's `references/meshing.md` can be nine
hundred lines; it costs nothing until an agent is actually meshing.

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
  "origin": "extracted from a production FDM project"
}
```

`settles` is the **matching vocabulary**. When `atompipe gap` finds a claim with no
gate, it scores every manifest's `settles` and `claim_classes` against the claim's
quantity to suggest candidates. Write the phrases a person would actually use for
the quantity, not your internal gate names.

`provides_views` lists the view ids `views/*.py` registers. It is tier 1 for two
reasons: a reader deciding whether to install a pack wants to know it comes with a
viewer, and it is the string every `Locator.view` in the pack's verdicts will
address — declared here, the pair can be checked without importing anything.

`origin` matters. Say where the pack came from — extracted from a real project,
built by the extension protocol, ported from a paper. It tells the next reader how
much to trust it.

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
from atompipe.gates import gate, GateContext
from atompipe.models import Verdict, Tier, NegativeControl

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
  present is broken: `atompipe gate selftest` reports it not-ok (`skipped on its own
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

  The one gate that *should* bind broadly and narrowly at once is a **validity
  guard** — a slenderness check protecting beam theory, a Biot check protecting
  lumped capacitance, a Reynolds check protecting a correlation. When one of those
  trips, every other number in the domain really is untrustworthy, so dragging the
  whole domain down is the correct behaviour. Ship one; it is usually the most
  valuable gate in the pack.
- **Ship at least one tier-0 gate.** A pack of only expensive gates has not finished
  its job.

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

@dataclass
class NegativeControl:
    fixture: str                       # "selftest/steep_cone.py" or "module:function"; blank is refused
    expect: str = "fail"               # "fail" (must not pass) or "error" (must crash); else not ok
    note: str = ""                     # what is wrong with the fixture, and nothing else

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
    outcome -> str                     # property: "error" | "skipped" | "pass" | "fail"
    ok -> bool                         # property: outcome == "pass"; a skip is never ok
    def render(self) -> str            # "[FAIL] fdm.overhang : worst face 63.2deg vs 50deg limit"

@dataclass
class GateContext:                     # a gate's one argument
    root: str                          # the project root
    ledger: Ledger                     # a COPY of the project state: read it, never write it
    model: Any | None                  # the loaded model, or None
    params: dict                       # the projection, flattened: read numbers here
    out_dir: str                       # scratch and evidence
    tier: int                          # the sweep's tier; never a reason to lower the standard
    log: Callable[[str], None]         # one-line progress sink
    extra: dict                        # where a dict-returning fixture lands
    pack: str; key_scope: str          # stamped by run_gate: whose namespace param() reads
    def scopes(self) -> list[str]      # ["fdm", "fdm-print"]: this gate's scopes, best first
    def param(self, name, default=None, *, scope=...) -> Any   # scoped first: see below
    def pack_param(self, name, default=None) -> Any
    def first_pack_param(self, names, default=None) -> Any
    def first_pack_param_named(self, names, default=None) -> tuple[Any, str]
    def require_param(self, name) -> Any          # raises rather than compare with None
    def out_path(self, *parts) -> str             # an evidence path under out_dir, dir created
    def with_extra(self, extra) -> GateContext    # a copy with `extra` merged over
```

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

`atompipe doctor` reads those two maps from every **installed** pack and reports
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
`selftest/<file>.py` or `module:function`.

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
identically, an inheriting one skips or flips.

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
any of its code — and `atompipe doctor` can only diff two packs' vocabularies
against each other if both of them wrote one down. **State the key under the
spelling the pack teaches** (`part_bbox_mm`, not `bbox_mm`), so CI proves the
primary key is the one actually read.

CI asserts three things: every gate passes the baseline, every control fires against
it, and nothing skips. "Nothing" is exact: a skip on the baseline or on a control is
allowed only when `availability(spec)` fails — the gate's declared tooling is
genuinely absent on that machine, which is how a runner without trimesh reports a
mesh pack honestly instead of failing it. A gate that skips itself on either input
while its tools are present fails.

`atompipe gate selftest` runs every control and **fails any gate that passes its own
known-bad input**.

## Views and locators

A pack can also draw what its gates measure. `views/*.py` holds **viewgens**, which
are to the project site what gates are to the report: same context shape, same
`requires_*` declarations, same registry. A pack that emits a view in one of the
kinds the site already knows gets visualisation for free — and, more importantly,
its gates get somewhere to point.

```python
from atompipe.site import viewgen, ViewContext, derive_explode
from atompipe.models import View, ViewKind

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

Rules:

- **Returning `None` is normal.** A CAD viewgen in a project with no geometry has
  nothing to draw. That is not a failure; `atompipe site build` records it as
  `empty` and moves on. Do not raise, and do not emit an empty view that renders as
  a broken box.
- **Declare `requires_python` / `requires_tools` the way a gate does.** A viewgen
  whose exporter is missing is reported as *unavailable* with the module named. A
  view that is absent because trimesh is not installed otherwise looks exactly like
  a view the project never had.
- **Write assets through `ctx.write_asset`**, never by hand into `site/assets/`.
  Only what the context recorded is known to be live, so a file written behind its
  back either survives forever or gets swept the first time the cleaner is made
  stricter. `write_asset` returns the site-relative path that goes in `View.src`.
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
  because single ones are ordinary inside part names like `back_left`).

### Attaching locators to a verdict

A gate that knows *where* the problem is says so, and the site stops being a report
and starts being a debugger:

```python
return Verdict(
    gate="cad.clash", passed=False,
    detail="1 interfering pair: back_left / grip_lid_left 0.41 mm^3",
    locators=[
        Locator(view="assembly", target="back_left",
                label="0.41 mm^3 into grip_lid_left", value=0.41),
        Locator(view="assembly", target="grip_lid_left"),
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

Nothing is dropped for being undrawable, either. `atompipe site build` reports every
locator naming a view that does not exist or a node the view does not declare —
in its warnings and in `state.json` — because a gate that thinks it is drawing and
is not looks exactly like a gate that found nothing, and both ends of that mistake
are silent.

## `lenses.md`

The adversarial review dimensions for this domain — the angles a reviewer should
attack from before anything gets built. One heading each, a few pointed questions
under it. Rule 8 of the method lives here.

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
atompipe pack validate <name>
```

checks that the manifest parses and matches its directory, `PACK.md` exists and is
substantive, every declared gate actually registers, every gate has a negative
control, `max_tier` matches the gates, the description is one line, and every
file-based fixture exists — and then **demonstrates** the pack at tiers 0–1, gate by
gate, with the pack loaded alone: its own `selftest/baseline.json` passes, its
control fires, and the control still fires against an empty host (the seal probe).
A gate whose declared tooling is absent on this machine is printed as a `note:` and
not demonstrated — not a problem, and not a pass either. Tiers 0–1 call no external
solver, so this stays seconds long; the rest waits for

```
atompipe gate selftest --pack <name> --junit <file>.xml
```

which runs the same three checks at **every** tier and writes them as JUnit XML:
suite `controls` (childless only where the control fired) and suite `baselines`.
`--pack` takes a pack name or a directory; a pack being written inside a project
(`.atompipe/packs/<name>/`) is found by name there. Outside a project,
`atompipe gate selftest` with no `--pack` demonstrates every bundled pack. Either
way nothing is recorded, a run in which no control ran exits 1 (`--allow-empty`
accepts that), and `--user-packs` is needed before `$ATOMPIPE_PACK_PATH` or
`~/.atompipe/packs` are searched — the machine does not get to choose which copy is
tested.

CI runs `pack validate` on every pack and `gate selftest` at the repository root,
over every bundled pack. **A pack whose gates have never demonstrated failure does
not get merged.**

## Contributing back

A pack is an ordinary directory, laid out as above: no command creates or exports
one. One built inside a user's project, under `.atompipe/packs/<name>/`, is
already the directory a PR adds under `packs/`, with no edits. The evidence that
goes with it is the selftest's own report:

```
atompipe pack validate <name>                            # what CI runs on every pack
atompipe gate selftest --pack <name> --junit <file>.xml  # every tier: the evidence
```

What slipped through (S-10): this section, the extension protocol and the
pack-authoring skill all named a `pack new` and a `pack export` command, the second
"with selftest evidence attached". Neither ever existed, and neither will (D-24);
`tests/test_docs_commands.py` now parses every command these documents print.

That is the contribution loop: someone builds an RC boat, the agent follows the
extension protocol to grow a CFD gate, and the pack comes back for the next person
who needs one.
