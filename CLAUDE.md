# Working on atompipe

atompipe takes a design from a sketch to a validated physical thing. Its chain is

```
claims  ->  gates  ->  packs  ->  readiness report
```

Read [`METHOD.md`](METHOD.md) first — ten rules, and everything in `src/` exists to
make one of them mechanical.

**Using** atompipe (rather than working on it)? The skill in `skills/atompipe/` is
the entry point, and `atompipe doctor` is the first command to run when confused.

## Run it

```sh
PYTHONPATH=src python3 -m atompipe --help
PYTHONPATH=src python3 -m unittest discover -s tests   # every test, must stay green
cd examples/bracket && PYTHONPATH=../../src python3 -m atompipe check
```

No install step and no dependencies. `trimesh` + `numpy` are needed only to exercise
the two mesh packs; without them those gates report SKIPPED, which is correct
behaviour and is itself tested.

## Invariants — do not break these

The entire value of this project is that its report refuses to claim anything it has
not proven. Four properties carry that, each with a test that actively tries to
violate it (`tests/test_invariants.py`):

1. **A skipped gate is never a pass.** A missing solver means the claim is BLOCKED.
2. **An errored gate is never a pass.** A crash proves nothing, and reads louder than
   a missing tool.
3. **The registry refuses a gate with no negative control.** A validator that cannot
   be shown to fail is a logger. This is not negotiable — an agent that writes
   plausible code writes plausible validators, and those launder assumption into
   proof.
4. **The report never puts an unrun or skipped gate under PROVEN.** Where a covering
   gate did not produce a pass, the row is marked PARTIAL and names it with the reason.

Two more, learned the hard way and enforced in `tests/test_packs.py`:

5. **Pack fixtures are SEALED.** A fixture builds its context from the pack's own
   `selftest/baseline.json`, never from `ctx.params`. A control whose severity depends
   on the host project passes in some repositories and fails in others. (Observed
   live: a host project's innocuous waterplane inertia defused a stability control and
   the gate passed its own known-bad input.)
6. **Every pack ships a baseline its gates all pass**, so its controls can actually be
   exercised in CI. A skipped control is not a passing control.

Checkpoint 1.2 serves verdicts from a per-gate cache, which opens two new ways to show
a PASS nothing earned; enforced in `tests/test_staleness.py` and
`tests/test_admission.py`:

7. **A stale verdict is never served as current.** A verdict is keyed by the hash of
   what its gate actually read — parameters, files, claim records, its own code, the
   spine. When any of those moves, its PASS reads STALE until the gate runs again.
   (Observed before this: one hash of the whole model decided every gate, and a model
   that failed to import compared equal, so three claims read PROVEN for a design that
   could not be built.)

Invariant 8 — the records index never disagrees with the records — arrives with the
records layout in checkpoint 1.3.

9. **A verdict counts only from a gate admitted at its current version.** Its negative
   control must have been run, and must have failed, at the gate's current code,
   fixture inputs and spine; `check` runs it whenever that is not on record. A PASS
   from an undemonstrated gate reads stale, and a gate whose control passed its own
   known-bad input is not admitted at all. This is the reject half: it shows the gate
   can refuse, not yet that it accepts a known-good design. (Observed: a gate that
   returned `True` with a declared control produced PROVEN rows, because nothing ever
   ran the control.)

## House rules

- **`src/atompipe/` is standard library only.** No third-party imports, ever — the
  spine must never be the reason an install fails. CI proves this by AST-walking every
  import, including function-local ones.
- **Generated files are outputs, not sources.** Never hand-edit one; fix the model.
- **Every constant carries its provenance** — why this value, and *what was tried and
  rejected*. The rejected alternatives are the field that pays: without them every
  fresh context window re-litigates every settled number.
- **The site renders the ledger; it never computes truth.** Nothing under `site/`
  re-derives a measurement or decides whether a claim passes — if a number on the
  page is wrong, the ledger is wrong. And no build step: plain HTML, CSS and ES
  modules, for the same reason the spine has no dependencies.
- **Comments say what slipped through.** Where a rule exists because something got
  past a check, name it. The modules are dense with this on purpose.
- **No reference to the parent project.** atompipe carries its method and none of its
  content; [`docs/ORIGINS.md`](docs/ORIGINS.md) is the only place it is named, and a
  test enforces that.

## Layout

| Path | What |
|---|---|
| `src/atompipe/` | the spine — stdlib-only modules; `models.py` is the type contract |
| `src/atompipe/verdicts.py` | what a gate read, so its verdict can be keyed by it: the trace (`ParamTrace`, `LedgerView`, the audit hook), portable digests, the spine digest — and, as Phase 1.2 lands, the per-gate verdict cache, freshness and admission |
| `src/atompipe/vcs.py` | the only git edge: argv form, a clean environment, a timeout, never raises |
| `site/` | a project's site — scaffolded by `atompipe site init` from `src/atompipe/site_template/`. Plain HTML/CSS/ES modules, no build step |
| `packs/` | domain packs. Ordinary directories: no build step, no registration |
| `skills/` | the Claude Code skills (`atompipe`, `pack-authoring`) |
| `examples/bracket/` | the reference project — zero dependencies, runs the whole loop |
| `tests/` | the honesty invariants and the pack gate-on-the-gates |
| `METHOD.md` | the doctrine |
| `docs/EXTENSION_PROTOCOL.md` | how an agent grows a capability nobody prebaked |
| `docs/PACK_FORMAT.md` | the pack contract |
| `docs/SITE_CONTRACT.md` | the site: view kinds, locators, `state.json` |
| `docs/SPINE_CONTRACT.md` | internal: each module's public surface |

## Adding a pack

Read `docs/PACK_FORMAT.md` and the `pack-authoring` skill. In short: `pack.json`
(tier 1, ~20 words), `PACK.md` (tier 2, ~150 lines), `references/` (tier 3, loaded
only for the task at hand), gates that each declare a negative control, and
`selftest/baseline.json`.

Then, and this is the part that decides whether it gets merged:

```sh
PYTHONPATH=src python3 -m atompipe pack validate <name>
PYTHONPATH=src python3 -m atompipe gate selftest --pack <name> --junit <file>.xml
```

`pack validate` checks the layout and demonstrates tiers 0–1; `gate selftest --pack`
runs every tier's control, and its JUnit file is the admission evidence the PR
carries. There is no `pack new` and no `pack export` (D-24). CI runs `gate selftest`
at the repo root, where it demonstrates every bundled pack.

A pack whose gates have never demonstrated failure does not get merged.
