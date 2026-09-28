# atompipe

Take a sketch or a discussion to a physical thing you can actually build — with an
honest account of what has been verified and what has not.

atompipe is a Claude Code plugin and a small Python spine. It generalises a pipeline
that was proven on a real device before it was extracted — a physical product taken
from a napkin sketch to manufacturable outputs, working firmware and a published
readiness report, across dozens of revisions ([origins](docs/ORIGINS.md)).

It is domain-agnostic on purpose. The same spine drives an RC boat, a solar thermal
collector, a chemical process or a printed bracket, because the hard part is never
the domain. The hard part is knowing which parts of your design have actually been
checked.

```
claims  ->  gates  ->  packs  ->  readiness report
```

A **claim** is something that must be true for the design to work. A **gate** is an
executable that settles a claim *and is capable of failing*. A **pack** supplies gates
for one physical domain. The **readiness report** is the claims and their verdicts
rendered: what is proven, what is not, and why.

## Install

**In Claude Code** — the repo is a plugin marketplace:

```
/plugin marketplace add neuman/atompipe
/plugin install atompipe
```

**Anywhere else** — the spine is **standard library only**, so there is nothing to
build and nothing to resolve:

```sh
pip install git+https://github.com/neuman/atompipe
# or just:
git clone https://github.com/neuman/atompipe && cd atompipe
PYTHONPATH=src python3 -m atompipe --help
```

Try it immediately on the reference project, which has zero dependencies:

```sh
cd examples/bracket
atompipe status         # 7 claims: 3 proven, 1 failing, 1 gap, 1 physical, 1 assumed
atompipe check          # 6 gates; one fails on purpose — fix `thickness` 7.0 -> 8.0
atompipe gate selftest  # every gate proves it can fail on known-bad input
atompipe why thickness  # one parameter's full history, including what was rejected
```

It ships with its records and its verdicts, because those *are* sources of truth —
not build artifacts: each claim is a file under `claims/`, and every gate's verdict
is committed under `.atompipe/verdicts/`, so a fresh clone's first `check` is served
from the cache and reads the same answer the author saw.

Nothing heavy installs until a claim needs it and you have said yes. The two packs
that want `trimesh` report their claims as **BLOCKED** — visibly — when it is absent,
rather than quietly skipping.

## What using it looks like

You describe a thing. atompipe asks for the evidence a design conversation never
produces on its own — sketches, a photo of the closest product you'd buy instead,
calipers on whatever it has to fit, datasheets for parts you have already chosen.
Then it extracts the claims:

```
C1  Floats with the full payload at <=60% draft          [measurable]
C2  Directionally stable at cruise (~4 m/s)              [measurable]
C3  Hull prints on a 220x220 bed without supports        [measurable]
C4  Runtime >=20 min at cruise on one pack               [measurable]
C5  Watertight at the hatch seam                         [physical only]
```

C5 cannot be validated by any tool. It stays UNVERIFIED, visibly, until you build one
and record a result. Everything else gets gated:

```
$ atompipe check
[ok  ] displacement  : 1.94 kg at 60% draft vs 1.2 kg payload
[ok  ] bed-fit       : 580 x 148 x 121 mm, splits into 2 parts
[FAIL] print-overhang: 14 faces past 52 deg on the bow flare
[skip] stability     : no gate installed for C2
```

That last line is the interesting one. A claim with no gate is a **capability gap**,
and it is how the system grows: the agent names the unvalidated quantity, tries the
analytic answer first, and only then proposes a solver — with the real cost said out
loud.

```
$ atompipe gap --propose
C2  directional stability at cruise        no gate

  (a) analytic  metacentric height from the waterplane. Runs now.
                Catches gross instability; misses planing and yaw at speed.
  (b) solver    OpenFOAM ~2 GB, ~20 min install, ~15 min/run.
```

When you take the solver, the agent installs it pinned, smoke-tests it, wraps it as a
gate, **proves the gate fails on a hull it should reject**, and offers to export the
whole thing as a pack so nobody has to do it again.

## Why you should believe the output

Because of what it refuses to claim. Every row in the PROVEN table cites the gate that
proved it and the evidence file it wrote. Everything else is listed as physical,
blocked, stale or assumed, with the reason.

Three statuses never blur into "pass":

- **skipped** — the gate did not run. Its tool is missing. Nothing was proven.
- **errored** — the gate crashed. Nothing was proven.
- **stale** — it passed, but inputs have changed since. Nothing is proven *now*.

And every gate must declare a **negative control**: known-bad input it has been shown
to fail on. The registry refuses to register a gate without one. This is not
bureaucracy — an agent that writes plausible code will write plausible validators,
and plausible validators are worse than none, because they launder assumption into
apparent proof.

## Where the facts live

Records are files — one fact, one file, in the project, under git:

```
my-project/
  model/bracket.py          the model: each parameter's value, why, and what lost (PARAMS)
  gates/   selftest/        the project's own gates, and the known-bad inputs they must fail on
  claims/C1.json            one claim per file: what must be true, and its limit
  params/  decisions/  needs/  inputs/  results/  views/     one record per file
  .atompipe/project.json    the project: its name, its model entry, its live packs
  .atompipe/verdicts/       every gate's verdict, keyed by the hash of what it read (tracked)
  .atompipe/ledger.json     an index of every record, generated (ignored: never edit it)
  .atompipe/cache/  obs/  out/     the last check's statuses, what runs cost, scratch (ignored)
```

You — or the agent — edit a record the way you edit code, and `atompipe check`
validates it: a misspelled key in `claims/C1.json` is refused with a suggestion, never
silently dropped. A verdict goes stale when something its gate was seen to read
changes — a parameter, a file, a claim, its own code — so an edit to one parameter
re-runs only the gates that read it; what the tracer cannot see is named in
[`docs/SPINE_CONTRACT.md`](docs/SPINE_CONTRACT.md)'s limits. There is no run history
to keep: git and the verdict cache are the history.

## The method

Ten rules, in [`METHOD.md`](METHOD.md). The short version:

1. One parametric model is the only source of truth; everything else is generated.
2. Derive, never duplicate.
3. Every constant carries its provenance — especially the alternatives that lost.
4. Validators are gates, not loggers. *A logger is not a gate.*
5. Every gate needs a falsification control.
6. Check agreement across representations.
7. Wrap external solvers in converging loops.
8. Adversarial review moves the spec before anything is built.
9. Separate what is proven from what is assumed, in public.
10. Cheap inner loops, or there is no loop.

## Packs

| Pack | Gates | Needs | Settles |
|---|---|---|---|
| `beam-analytic` | 8 (8 tier-0) | none | tip deflection, mid-span deflection, deflection ratio, span over deflection limit, stiffness |
| `cad-solid` | 7 (1 tier-0) | trimesh, numpy | watertightness, solid validity, degenerate faces, duplicate vertices, part interference |
| `fdm-print` | 7 (5 tier-0) | trimesh, numpy | bed fit, bridge span, build volume, cantilever overhang, filament mass |
| `fluids-analytic` | 7 (7 tier-0) | none | buoyancy, displaced volume fraction, freeboard at load, reserve buoyancy, metacentric height |
| `sourcing` | 7 (7 tier-0) | none | bill of materials completeness, unpriced line, build cost per unit, rolled-up cost, budget |
| `thermal-analytic` | 10 (10 tier-0) | none | u-value, wall heat loss, insulation thickness, convective heat transfer coefficient, natural convection |

Packs are ordinary directories. No build step, no registration, no central authority
— drop one in and it is found. Three-tier disclosure keeps them cheap: a ~20-word
manifest is always in context, `PACK.md` loads when the domain is relevant, and
`references/` loads only for the task at hand.

See [`docs/PACK_FORMAT.md`](docs/PACK_FORMAT.md) to write one, and
[`docs/EXTENSION_PROTOCOL.md`](docs/EXTENSION_PROTOCOL.md) for how an agent grows a
capability that nobody prebaked.

## The project site

Every project can build a static site, and the reason to build one is not the
presentation. It is that **gate verdicts are anchored to the geometry they are
about.** `cad.clash: back_left interferes with grip_lid_left by 0.41 mm³` is a
sentence you have to go and act on. The same verdict carrying locators lights both
parts up in the viewer, at the pose where it happens, and you are looking at the
problem instead of reading about it.

```sh
atompipe site init      # scaffold site/ — index.html is yours, never regenerated
atompipe site build     # run the viewgens, write site/data/ and site/assets/
atompipe site serve     # python3 -m http.server. No build step, no npm, ever.
```

`site build` never runs gates. It renders the verdicts already in the verdict
cache and stamps each with its own age, because a page that re-ran the cheap gates
and not the expensive ones would show a mixed-age picture under one timestamp. It also reports
every locator naming a view or a part that does not exist — a gate that thinks it is
highlighting something and is not looks exactly like a gate that found nothing.

Everything the page shows is in `site/data/state.json`, so `curl` answers "what is
the status of this project?" with no browser. And 3D is one view kind of six: a
project with no geometry still gets claims, verdicts, evidence, provenance and the
readiness sentence. `atompipe site vendor` pulls three.js local for offline use.

See [`docs/SITE_CONTRACT.md`](docs/SITE_CONTRACT.md).

## Commands

```
atompipe init                  atompipe status
atompipe ask                   what evidence to request from the human
atompipe ingest <files>        sketches, photos, CAD, datasheets, measurements
atompipe extract <artifact>    what was read out of it, and what that grounds
atompipe check [--tier N]      run gates; exits non-zero while anything critical blocks
atompipe gap [--propose]       claims with no gate, and packs that might cover them
atompipe why <param|claim>     one thing's full history, instead of the whole log
atompipe gate selftest         every negative control; fails any gate that can't fail
atompipe report [--write]      the readiness report
atompipe site build|serve      the project site: the ledger, rendered and clickable
atompipe doctor                run this first when something is confusing
```

## Status

Early. The spine and the first extracted packs work; the interfaces will move. It is
Apache 2.0 — use it, fork it, or take the ten rules and ignore the code.

**Do not run an older atompipe on a project in this layout.** A version from before
records became files reads `.atompipe/ledger.json` as the project's records, and may
rewrite it; nothing a newer version writes can stop it, because the older one
predates every guard. (A project whose `.atompipe/project.json` says a newer `schema`
than your atompipe knows is refused — that check runs only from this layout forward.)
A project still in the old one-file layout migrates itself the first time `check`, or
a command that writes a record, runs: its `ledger.json` becomes one file per record and
is renamed `ledger.legacy.json`, never deleted.

## Licence

Apache License 2.0. See [LICENSE](LICENSE).
