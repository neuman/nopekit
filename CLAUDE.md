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

Three more came with Phase 1, which moved every verdict into a per-gate cache and
every record into its own file — each move a new place to show a PASS nothing
earned. Enforced in `tests/test_staleness.py`, `tests/test_records.py` and
`tests/test_admission.py`:

7. **A stale verdict is never served as current.** A verdict is keyed by the hash of
   what its gate actually read — parameters, files, claim records, its own code, the
   spine. When any of those moves, its PASS reads STALE until the gate runs again.
   (Observed before this: one hash of the whole model decided every gate, and a model
   that failed to import compared equal, so three claims read PROVEN for a design that
   could not be built.)

8. **The index never disagrees with the records, and no command writes a record
   it was not asked to write.** The records are files — `claims/`, `params/`,
   `decisions/`, `needs/`, `inputs/`, `results/`, `views/` and
   `.atompipe/project.json` — and `.atompipe/ledger.json` is an index generated
   from them, rebuilt after every command (`doctor`, `init` and `--no-record` runs
   write none of it), so a hand edit made since the last command reaches it;
   nothing reads it for truth. `check` writes no record: every path it writes is
   ignored by git at the moment it is written, or is a new verdict entry. Each kept
   writer (`ingest`, `extract`, `decide`, `packs add`, `claim physical`) writes the
   one record it was asked for. The one carve-out is the one-time legacy migration,
   which only `check` and those writers perform; every other command reads a legacy
   ledger in memory and writes nothing. (Observed before this: `check`, a sweep, saved the whole ledger on
   every run, so a claim a human edited between two commands was put back from the
   sweep's memory, and `gap`, which reads like a query, filed every gap it found as
   a record nobody wrote.)

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
- **The site renders the records and the verdict cache; it never computes truth.**
  Nothing under `site/` re-derives a measurement or decides whether a claim passes —
  if a number on the page is wrong, a record or the cache is wrong. And no build
  step: plain HTML, CSS and ES modules, for the same reason the spine has no
  dependencies.
- **Comments say what slipped through.** Where a rule exists because something got
  past a check, name it. The modules are dense with this on purpose.
- **No reference to the parent project.** atompipe carries its method and none of its
  content; [`docs/ORIGINS.md`](docs/ORIGINS.md) is the only place it is named, and a
  test enforces that.

## Where facts live

Each fact has one home; everything else that shows it is an output of that home.

- **Records** — tracked, one file per record, edited by hand or by the agent and read
  strictly (an unknown key is refused, with a suggestion): `claims/<id>.json`,
  `params/`, `decisions/`, `needs/`, `inputs/<id>.json`, `results/<claim-id>.json`,
  `views/*.json`, and `.atompipe/project.json` (meta, `model_entry`, the live `packs`).
- **The model** — `model/*.py` owns every parameter's value, units and rationale, and
  in `PARAMS` what lost to it; a param record holds only the provenance the model
  cannot (`source`, `grounded_by`, `tags`).
- **The verdict cache** — tracked, `.atompipe/verdicts/<gate id>/`: one file per
  rho (the hash of everything the gate read) and one per control outcome at a
  version, never rewritten. Git plus this cache is the history; there is no run history.
- **Outputs, ignored** — `.atompipe/ledger.json` (the index of every record, rebuilt
  after each command), `.atompipe/cache/last_check.json` (statuses, counts and the
  parameter view as the last full `check` saw them), `.atompipe/obs/` (what each run
  cost), `.atompipe/out/` (scratch and evidence). The index and `last_check.json` are
  the whole project in two reads; nothing reads either for truth.

`docs/SPINE_CONTRACT.md` ("Where facts live", then `store.py`'s section) has the
layout in full, with each writer.

## Layout

| Path | What |
|---|---|
| `src/atompipe/` | the spine — stdlib-only modules; `models.py` is the type contract |
| `src/atompipe/store.py` | the records: one file per record, the strict reader, the writer, the generated index, the one-time legacy migration |
| `src/atompipe/verdicts.py` | the per-gate verdict cache: what a gate read (`ParamTrace`, `LedgerView`, the audit hook), portable digests and the spine digest, rho, cache and control entries, freshness, admission at the gate's current version, the one resolver every reader uses, the sweep `check` runs, and `last_check.json` |
| `src/atompipe/vcs.py` | the only git edge: argv form, a clean environment, a timeout, never raises |
| `site/` | a project's site — scaffolded by `atompipe site init` from `src/atompipe/site_template/`. Plain HTML/CSS/ES modules, no build step |
| `packs/` | domain packs. Ordinary directories: no build step, no registration |
| `skills/` | the Claude Code skills (`atompipe`, `pack-authoring`) |
| `examples/bracket/` | the reference project — zero dependencies, runs the whole loop |
| `tests/` | the honesty invariants and the pack gate-on-the-gates |
| `tests/_env.py` | the one environment every test subprocess runs in (`_env.run`): a temp `HOME`, no user packs, git isolated from the machine, the tools still visible |
| `tests/_projects.py` | test projects built once: a bracket copy, and a pack's baseline wrapped as a project |
| `tests/_transcript.py` | Phase 1's target transcript as data: replayed on a fresh clone by `test_fresh_clone.py`, its lines held to their shapes by `test_shapes.py` |
| `tests/oracle/` | scripts, not tests: CI's bracket failure signature (`bracket_signature.py`) and the R-8 differential oracle run at phase review (`r8_statuses.py`) |
| `METHOD.md` | the doctrine |
| `docs/EXTENSION_PROTOCOL.md` | how an agent grows a capability nobody prebaked |
| `docs/PACK_FORMAT.md` | the pack contract |
| `docs/SITE_CONTRACT.md` | the site: view kinds, locators, `state.json` |
| `docs/SPINE_CONTRACT.md` | internal: where facts live, each module's public surface, and the limits — what the spine cannot see |
| `docs/PLAN.md` | the paper mapped onto the repo: gap-map rows (marked `closed` as their proofs land), invariants, sequencing rules; `docs/plan/` holds each phase's detail |

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
