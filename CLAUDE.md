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
PYTHONPATH=src python3 -m tests.fast                   # fast subset, ≤ 90 s on an idle machine: run it each iteration; the full suite runs before each checkpoint and commit
cd examples/bracket && PYTHONPATH=../../src python3 -m atompipe check
```

No install step and no dependencies. `trimesh` + `numpy` are needed only to exercise
the two mesh packs; without them those gates report SKIPPED, which is correct
behaviour and is itself tested.

`examples/bracket` commits its verdict cache, so that last command is six cache hits
and leaves `git status` clean. The entries are keyed on the spine that wrote them: a
code edit (not a comment or docstring) to `models.py`, `gates.py`, `modelio.py` or
`verdicts.py`, or to the bracket's model, gates or selftest, must regenerate them in
the same change. `tests/test_bracket_cache.py` goes red until it does and names the
commands; `examples/bracket/README.md` ("The committed cache") has them in full.
(What slipped through before the cache was committed: every verification run
rewrote the bracket's tracked ledger and added a run file, so `git status` was never
clean and nobody read it.)

## Invariants — do not break these

The entire value of this project is that its report refuses to claim anything it has
not checked. Four properties carry that, each with a test that actively tries to
violate it (`tests/test_invariants.py`):

1. **A skipped gate is never a pass.** A missing solver leaves the claim **Skipped**
   — beside a gate that passed, too.
2. **An errored gate is never a pass.** A crash settles nothing, and reads louder than
   a missing tool: the claim reads **Skipped**, its reason starts `errored:`, its row
   sorts above every skipped row in Failing's tone (`[SKIP ]`), every count splits it
   out (`N skipped (k errored)`), and it is a JUnit `<error>`, critical or not
   (`tests/test_louder.py`). And a gate cannot make its crash read quieter: the mark
   for an evaluator refused at its version (`Verdict.unqualified`, read as Gap) is
   the spine's, cleared on whatever a gate returns. (What slipped through the plan:
   a crash read FAIL until P2.1, loud only by the accident of the status it borrowed;
   moved to Skipped alone, it would have read "blocked on missing tooling" — a crash
   filed under the missing tool's word.)
3. **The registry refuses a gate with no negative control.** A validator that cannot
   be shown to fail is a logger. This is not negotiable — an agent that writes
   plausible code writes plausible validators, and those launder assumption into
   proof.
4. **The report never lists a claim as Checked — in its checked section
   (`report.SECTION_PROVEN`), a status row or a JUnit pass — while any of its gates
   is unrun, skipped, errored or unqualified, or while a value one of its evaluators
   passed does not meet the claim's acceptance condition;** that claim reads under
   its own status (Open, Skipped, Gap, Failing), naming the gate and the reason. A
   pass is held to the goalpost it read, too: a gate that read an acceptance
   condition (`ctx.acceptance`) and passed at a value it rejects, in other units, or
   with no value is errored. (What slipped through: a pass beside a skipped or unrun
   gate read PASS and was printed under PROVEN marked PARTIAL (S-03); a gate `check`
   refused at its first run had no cached verdict, so every reader after it read the
   gate "never run" and the claim beside it PROVEN, `ready: true` — GLOSSARY §3's
   composition, `claims.compose`, closes both; and an evaluator judging against a
   limit it computed read Checked at a value its claim's own condition rejected
   (S-35) — `claims.cross_check`, P2.4.)

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
   what its gate actually read — parameters, files, claim records, the acceptance
   conditions it read as goalposts, its own code, the spine. When any of those moves,
   its pass reads Stale until the gate runs again — and a goalpost edit re-keys exactly
   the gates that read that goalpost, and a statement edit none.
   (Observed before this: one hash of the whole model decided every gate, and a model
   that failed to import compared equal, so three claims read Checked for a design that
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

9. **A verdict counts only from an evaluator qualified at its current version.** Its
   known-good control must pass and its known-bad control must fail — both run at its
   current code, control inputs and spine, both retained and rerunnable (a pack's
   `selftest/baseline.json` or the gate's `good` fixture, and its `fixture`; a
   project's `selftest/known_good.py` and its fixture), and both reaching it through
   the same channel. An evaluator not from a bundled pack must also fail every
   conclusive mutation of its known-good control: one value it read, pushed until its
   own value lands 15% past its own limit — and its check run is handed nothing its
   qualification runs were not: no model, and a verdict that read a ledger value no
   qualification run read does not count — except a goalpost's limit, which is meant to
   differ between calibration and use, and only because its qualification moved each
   goalpost its known-good control read and its value did not move (the goalpost's
   claims, quantity, comparator and units stay held). Where its code lives decides
   which it is, never a `PACK_DIR` it sets itself. And it counts only on inputs inside
   the operating context it declares: outside, a pass does not count — the claim reads
   Gap, or Assumed under an owned fallback — and a fail still does; its known-good
   control must lie inside that context, and a mutation counts wherever it lands. `check` runs whatever of that is not on
   record; until then a pass from an evaluator qualified at an earlier version reads
   Stale, and one never qualified reads Gap. An evaluator that is not qualified reads
   Gap — `unqualified: <evaluator> : <what does not hold>`, beside a passing one too —
   and one with no known-good control is *known-bad shown*, not qualified (`known-good
   not run`). Qualified means it can fail, and nothing more. (What slipped through: a
   gate that returned `True` with a declared control produced Checked rows, because
   nothing ran the control; then every project gate counted on its known-bad half
   alone, so a gate that failed everything (S-04) was let through, and one keyed to its
   own control — failing the one input that control changes, passing everything else —
   read Checked; then, in review of P2.3, such an evaluator read Checked again by claiming a
   bundled pack's `PACK_DIR` on one line, or by passing whenever it was handed a model
   or a ledger with a claim in it — channels no control or mutation had; then, in the
   design of P2.4, by lying whenever its goalpost was not the calibrated one, or by a
   context ending short of its own mutation's landing.)

One more came with checkpoint P2.2, when gates gained prerequisites — a dependent
verdict is a new place to show a pass nothing earned. Enforced in
`tests/test_prerequisites.py`:

10. **A prerequisite that is not established is never a pass downstream.** A gate may
    name the gates it needs (`needs`): a validity guard before the analyses it guards.
    One whose prerequisite failed, skipped, errored, is unqualified or is not
    registered is not run and reads **Skipped** — `prerequisite failed: <root>` or
    `prerequisite not established: <root> (<why>)`, and as loud as a crash when the
    root crashed — and that verdict is never cached or remembered; the gate's own
    crash still stands, and its own refusal, self-skip or missing tool too unless the
    root crashed. One whose prerequisite is invalidated
    or unrun keeps its verdict and reads Stale. The registry refuses a `needs` cycle,
    and a prerequisite in a costlier tier than its dependent, like a build system.
    (What slipped through: a validity guard protected nothing — a claim tagged only
    `deflection` read Checked on a beam whose guard reported Euler-Bernoulli omitting
    32% of the deflection.)

One more came with checkpoint P2.3, numbered as PLAN §4.0.1 numbers it (11–14 land with
their mechanisms). Enforced in `tests/test_mutation.py`, over every mutation entry point
in `src/`:

15. **A mutation pass writes nothing, changes nothing it was handed, and never says more
    than it ran.** It runs in process on a copy of the known-good control: nothing is
    written under the project or a pack directory, and the known-good design is as it
    was afterwards. A mutation counts only when it is conclusive — its key was read and
    the run passed or failed — and one that skipped or errored is printed as
    inconclusive, never as a fail; with none conclusive the line says `mutation 0
    conclusive`. (What slipped through the harness before the code existed: a plan
    that set every key to None made every mutation inconclusive, and the line read
    `mutation 0 conclusive` over every evaluator; a runner that drew up its plan after
    seeing which mutations passed printed `mutation 1/1 fail` over an evaluator one of
    two had passed.)

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
| `tests/_projects.py` | test projects built once: a bracket copy (the legacy bracket by default, the committed one with `migrated=True`), and a pack's baseline wrapped as a project |
| `tests/bracket_legacy/` | a fixture: the bracket's `.atompipe/` as it stood before its 1.3 migration (ledger, run history, ignore file), which `_projects.bracket_copy` puts back around today's sources. Never edited to make a test pass |
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
