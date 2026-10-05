# nopekit

A framework for **grounding by falsification**: propose candidates as fast as you
like, and keep an honest account of which claims about them have survived an
evaluator that could have rejected them — and which have not.

nopekit implements the grounded design state described in the paper
[*Grounding by Falsification*](grounding_by_falsification.pdf): the claims a candidate
must satisfy, the evaluators that can falsify them, and the provenance and current
validity of every verdict. A generator, human or model, can propose anything, but it
cannot settle a claim by asserting it. A claim is checked only when an evaluator that
has shown it can fail has run against the current inputs, and a change to those inputs
makes exactly the verdicts that read them stale.

It works in any domain where a claim can be stated and an evaluator can be written: an
analysis, a model, a process, a codebase, a physical product. Physical design is where
it was first proven, and where its bundled packs live today: a product taken from a
napkin sketch to manufacturable outputs, working firmware and a published readiness
report, across dozens of revisions ([origins](docs/ORIGINS.md)). That is where
evaluators are slowest and most expensive, and where an honest line between evidence
and assumption pays the most.

The hard part is never the domain. It is knowing which of your claims have actually
been checked.

```
claims  ->  gates  ->  packs  ->  readiness report
```

A **claim** is something that must be true for a candidate to be accepted. A **gate**
is an evaluator that settles a claim *and is capable of failing*. A **pack** supplies
gates for one domain. The **readiness report** is the claims and their verdicts
rendered: what is checked, what is not, and why.

## Install

**In Claude Code** — the repo is a plugin marketplace:

```
/plugin marketplace add neuman/nopekit
/plugin install nopekit
```

**Anywhere else** — the spine is **standard library only**, so there is nothing to
build and nothing to resolve:

```sh
pip install git+https://github.com/neuman/nopekit
# or just:
git clone https://github.com/neuman/nopekit && cd nopekit
PYTHONPATH=src python3 -m nopekit --help
```

Try it immediately on the reference project, which has zero dependencies:

```sh
cd examples/bracket
nopekit status         # 7 claims · 3 checked · 1 failing · 2 gaps · 1 pending build
nopekit check          # 6 gates; one fails on purpose — fix `thickness` 7.0 -> 8.0
nopekit gate selftest  # every gate proves it can fail on known-bad input
nopekit why thickness  # one parameter's full history, including what was rejected
```

It ships with its records and its verdicts, because those *are* sources of truth —
not build artifacts: each claim is a file under `claims/`, and every gate's verdict
is committed under `.nopekit/verdicts/`, so a fresh clone's first `check` is served
from the cache and reads the same answer the author saw.

Nothing heavy installs until a claim needs it and you have said yes. The two packs
that want `trimesh` report their claims as **Skipped** — visibly — when it is absent,
rather than quietly passing.

## What using it looks like

An example from physical design, the domain the bundled packs cover. You describe a
thing. nopekit asks for the evidence a design conversation never produces on its own:
sketches, a photo of the closest product you'd buy instead, calipers on whatever it
has to fit, datasheets for parts you have already chosen. Then it extracts the claims:

```
C1  Floats with the full payload at <=60% draft          [measurable]
C2  Directionally stable at cruise (~4 m/s)              [measurable]
C3  Hull prints on a 220x220 bed without supports        [measurable]
C4  Runtime >=20 min at cruise on one pack               [measurable]
C5  Watertight at the hatch seam                         [physical only]
```

No evaluator here can settle C5. It stays **pending build**, visibly, until you build
one and record a result. Everything else gets gated:

```
$ nopekit check
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
$ nopekit gap --propose
C2  directional stability at cruise        no gate

  (a) analytic  metacentric height from the waterplane. Runs now.
                Catches gross instability; misses planing and yaw at speed.
  (b) solver    OpenFOAM ~2 GB, ~20 min install, ~15 min/run.
```

When you take the solver, the agent installs it pinned, smoke-tests it, wraps it as a
gate, **proves the gate fails on a hull it should reject**, and offers to export the
whole thing as a pack so nobody has to do it again.

## Why you should believe the output

Because of what it refuses to claim. Every row in the report's checked section cites
the gate that passed it and the evidence file it wrote, and a claim is listed there
only when every gate covering it ran and passed — *checked*, which does not mean true.
Everything else is listed under its own status, with the reason: failing, skipped, a
gap, open, stale, pending build or assumed.

None of these ever blurs into a pass, even beside a gate that passed:

- **Skipped** — a gate skipped: its tool is missing here. Nothing was evaluated.
- **Skipped, errored** — a gate crashed. Nothing was evaluated, and it reads louder
  than a missing tool: the gate itself is broken.
- **Open** — a gate exists and is unrun on the current inputs.
- **Gap** — no gate covers the claim, or one has not shown it can fail; or an
  assumption nobody owns.
- **Stale** — it passed, but inputs have changed since. Nothing is checked *now*.

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
  .nopekit/project.json    the project: its name, its model entry, its live packs
  .nopekit/verdicts/       every gate's verdict, keyed by the hash of what it read (tracked)
  .nopekit/ledger.json     an index of every record, generated (ignored: never edit it)
  .nopekit/cache/  obs/  out/     the last check's statuses, what runs cost, scratch (ignored)
```

You — or the agent — edit a record the way you edit code, and `nopekit check`
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
nopekit site init      # scaffold site/ — index.html is yours, never regenerated
nopekit site build     # run the viewgens, write site/data/ and site/assets/
nopekit site serve     # python3 -m http.server. No build step, no npm, ever.
```

`site build` never runs gates. It renders the verdicts already in the verdict
cache and stamps each with its own age, because a page that re-ran the cheap gates
and not the expensive ones would show a mixed-age picture under one timestamp. It also reports
every locator naming a view or a part that does not exist — a gate that thinks it is
highlighting something and is not looks exactly like a gate that found nothing.

Everything the page shows is in `site/data/state.json`, so `curl` answers "what is
the status of this project?" with no browser. And 3D is one view kind of six: a
project with no geometry still gets claims, verdicts, evidence, provenance and the
readiness sentence. `nopekit site vendor` pulls three.js local for offline use.

See [`docs/SITE_CONTRACT.md`](docs/SITE_CONTRACT.md).

## Commands

```
nopekit init                  nopekit status
nopekit ask                   what evidence to request from the human
nopekit ingest <files>        sketches, photos, CAD, datasheets, measurements
nopekit extract <artifact>    what was read out of it, and what that grounds
nopekit check [--tier N]      run gates; exits non-zero while anything critical blocks
nopekit gap [--propose]       claims with no gate, and packs that might cover them
nopekit why <param|claim>     one thing's full history, instead of the whole log
nopekit gate selftest         every negative control; fails any gate that can't fail
nopekit report [--write]      the readiness report (--write: REPORT.md, an ignored output)
nopekit export <milestone>    the spend: re-runs what it requires, then builds out/<milestone>/
nopekit site build|serve      the project site: the ledger, rendered and clickable
nopekit doctor                run this first when something is confusing
```

## Status

Early. The spine and the first extracted packs work; the interfaces will move. It is
Apache 2.0 — use it, fork it, or take the ten rules and ignore the code.

**Do not run an older nopekit on a project in this layout.** A version from before
records became files reads `.nopekit/ledger.json` as the project's records, and may
rewrite it; nothing a newer version writes can stop it, because the older one
predates every guard. (A project whose `.nopekit/project.json` says a newer `schema`
than your nopekit knows is refused — that check runs only from this layout forward.)
A project still in the old one-file layout migrates itself the first time `check`, or
a command that writes a record, runs: its `ledger.json` becomes one file per record and
is renamed `ledger.legacy.json`, never deleted.

## Licence

Apache License 2.0. See [LICENSE](LICENSE).
