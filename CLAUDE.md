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
not checked. Four properties carry that, each with tests that actively try to
violate it (`tests/test_invariants.py` first; the table at the end of this section names
every class that holds each invariant):

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
   pass is held to the acceptance condition it read, too: a gate that read one
   (`ctx.acceptance`) and passed at a value it rejects, in other units, or with no
   value is errored — and a gate states the units its own arithmetic computes in,
   never the claim's back. (What slipped through: a pass beside a skipped or unrun
   gate read PASS and was printed under PROVEN marked PARTIAL (S-03); a gate `check`
   refused at its first run had no cached verdict, so every reader after it read the
   gate "never run" and the claim beside it PROVEN, `ready: true` — GLOSSARY §3's
   composition, `claims.compose`, closes both; and an evaluator judging against a
   limit it computed read Checked at a value its claim's own condition rejected
   (S-35) — `claims.cross_check`, P2.4; and, in review of P2.4, the reference gate
   repeated `acc.units` back, so C1 restated in `um` read Checked at 700 um against
   600.) A physical claim is listed Checked only on a physical result that counts —
   a pass on its current article — and an expert-judgment claim only on its
   authority's own judgment of the claim as it reads now; one the resolver calls
   Checked without either is listed loud, as status and evidence disagreeing (P2.5a;
   `tests/test_physical.py`).

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
`tests/test_admission.py`, and — as Phase 2 extended them — in the classes the table
below names:

7. **A stale verdict is never served as current.** A verdict is keyed by the hash of
   what its gate actually read — parameters, files, claim records, the acceptance
   conditions it read, its own code, the spine. When any of those moves, its pass
   reads Stale until the gate runs again — and a limit edit re-keys exactly the gates
   that read that claim's acceptance condition, and a statement edit none. A physical
   pass is bound to its article — the design it was recorded on: when that design
   moves it reads Stale, naming the article to rebuild — a check run cannot restore
   it (P2.5a). An article `export` built is what its generator read, and every value
   its package hands the builder: it moves only when something its generator read
   moves, and the rebuild prediction names exactly those articles (P2.5b) — a file it
   linked into its package is a read, and one there that no write the trace saw put
   makes the article the whole design (review of P2.5b).
   (Observed before this: one hash of the whole model decided every gate, and a model
   that failed to import compared equal, so three claims read Checked for a design that
   could not be built.)

8. **The index never disagrees with the records, and no command writes a record
   it was not asked to write.** The records are files — `claims/`, `params/`,
   `decisions/`, `needs/`, `inputs/`, `results/`, `views/`, `milestones/`,
   `exports/` and `.atompipe/project.json` — and `.atompipe/ledger.json` is an index generated
   from them, rebuilt after every command (`doctor`, `init` and `--no-record` runs
   write none of it), so a hand edit made since the last command reaches it;
   nothing reads it for truth. `check` writes no record: every path it writes is
   ignored by git at the moment it is written, or is a new verdict entry. Each kept
   writer (`ingest`, `extract`, `decide`, `packs add`, `claim physical`) writes the
   one record it was asked for; `export` (P2.5b) writes its export record
   (`exports/<milestone>.json`), the new verdict and control entries its re-run
   files, as `check --force` files them, and the marked ignore blocks — every other
   path it writes (`out/<milestone>/`, its package) is ignored when written; and
   `report --write` writes `REPORT.md`, an output git ignores. The one carve-out is the one-time legacy migration,
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
   qualification run read does not count — except an acceptance condition's limit,
   which is meant to differ between calibration and use, and only where its
   qualification moved that limit where it was read (a known-good or a mutation run;
   x0.1, x0.5, x2, x10, each limit of a band alone) and the value there did not move;
   the claims, quantity, comparator and units, and which limits are stated, stay held.
   That is evidence at the limits the runs visit, not at every limit: a gate that lies
   only where no run looks is named in SPINE_CONTRACT's limits. Where its code lives
   decides which it is, never a `PACK_DIR` it sets itself. And it counts only on inputs inside
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
   design of P2.4, by lying whenever its limit was not the calibrated one, or by a
   context ending short of its own mutation's landing; then, in review of P2.4, by
   lying only below a limit no run visited, at a band's other ratio, on a one-sided
   claim's stray `limit_hi`, or past a branch only the walk took.)

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

One more came with checkpoint P2.5a, when a physical result, an owner and an
expert judgment gained a channel — each a new place to settle a claim nothing
earned. Enforced in `tests/test_signing.py`, `tests/test_physical.py` and
`tests/test_owner.py`:

11. **A measurement or an expert judgment settles a claim, and an owner or an
    authority counts, only when entered through a channel the generator cannot
    author — typed by a person in their own shell, confirming the claim's id. A
    physical result can lose its power to check, never its power to fail.** `claim
    physical` records who from the git identity and when from the clock (`--who`
    and `--when` are refused, naming where each comes from); from an agent session
    or a pipe a pass is recorded and counts for nothing, and a fail counts. Every
    entry in `results/<id>.json` is sealed and chained, and a broken seal refuses
    the file, naming any fail the fix would discard. A pass counts only bound to
    its article — the design it was recorded on — and to the claim as the person
    read it, with its evidence's bytes unchanged; when the article's design moves it
    reads Stale and the rebuild prediction names that article. A fail counts across
    every edit — a claim file renamed or deleted included: a results file no claim
    file holds keeps its fail counting, and on its evaluator's track record. From
    P2.5b a fail loses its power to fail only on the object that failed: a fail on an
    article `export` built stops counting beside a pass a person records on another
    exported article that differs from it in what it was built from and in the bytes
    its generator wrote, while the design is no longer the failed one, and counts
    again the moment the design returns to it — a fail on any other article never
    stops counting. A result recorded with
    `--article` is bound to an exported article, and its pass counts only while
    `exports/` holds that export. An
    owner, and an expert-judgment claim's authority, count only as that person
    recorded them, as themselves: the claim is Gap until then, and an expert
    judgment Assumed under its authority's name until they judge it — and Gap again
    when the claim is edited after. A fail
    on a claim an automated evaluator had passed — qualified and current — is a
    contradiction, kept on that evaluator's track record at its version. (What
    slipped through: `claim physical --who` took a name the agent typed and
    defaulted to nobody (S-48); a pass typed one second after a fail read Checked
    (review of P2.1); a pass survived any change to the design it was tested on
    (S-50); an owner was whatever a file said; and, in review of the P2.5a design,
    an authority was whoever typed its name, and a hand-written `terminal: human`
    dropped a physical claim's evidence; in its review, a judged claim's rewritten
    statement read Stale and passed `check`, a claim file renamed away from its fail
    stopped it failing, and a fail flipped to a pass was left out of the refusal's
    advice, whose checkout then erased it; until P2.5b, no fix and reprint could
    ever read Checked; and, in review of P2.5b, a no-op line in the generator let the
    same print, reprinted byte for byte, release its own fail, and a fail bound to an
    article two milestones shared charged no evaluator.) What the seal does not stop — a process
    that recomputes it, or opens a pty with the agent markers unset — is in
    `docs/SPINE_CONTRACT.md`'s limits; P3's permission rule closes it.

One more came with checkpoint P2.5b, when a spend gained a name and a boundary — the
one place a pass nothing earned costs money. Enforced in `tests/test_export.py`,
`tests/test_physical.py` and `tests/test_vocabulary.py`:

12. **No reader says more than the composition, and the boundary that spends
    re-executes.** No renderer shows a claim more resolved than `claims.compose`
    composes it, nor says *ready* more generously than `claims.unresolved` — the one
    predicate the readiness sentence, `export`, the JSON summaries and the page read.
    A milestone (`milestones/<name>.json`) names the claims its spend requires; it is
    ready only when every one reads Checked — Assumed, Pending build and a Stale that
    waits on a person included in what is not. Every reader but `export` says *ready*
    as last evaluated, and says so. `export <milestone>` refuses while a required
    claim is unresolved, and re-runs every evaluator a required claim rests on, with
    its controls, its mutation pass and its prerequisites, at the top tier, refusing
    when a re-run disagrees with the entry the cache served or with what the records
    hold — on every later export too; `--dry-run` is the same code path and writes
    nothing of its own (a generator it runs that writes outside its directory is
    refused and named, not undone). Going ahead over an unresolved claim is a
    decision a person records in their own shell, naming each claim with its status,
    sealed into `exports/<milestone>.json` with the article the export was built
    from; no decision goes ahead over a disagreement. The readiness sentence says, in
    every branch, what is pending build, which articles need a rebuild and what is
    checked on an article. (What slipped through: the site's tab said ready whenever
    nothing stopped `check`, with a required physical claim untested and an
    assumption unowned (S-60); "5 checked" on the count line sat beside "NOT ready"
    because an unbound pass read Checked everywhere but there (review of P2.1); the
    committed readiness report had drifted from its ledger (S-41); and the tracked
    cache is forgeable in the inner loop — a hand-placed entry was served Checked
    until something re-ran it, and nothing at a spend did; and, in review of P2.5b,
    `report --milestone` said the boundary's *ready* over a forged cache and `status`
    listed no milestone at all.)

One more came with checkpoint P2.3, numbered as PLAN §4.0.1 numbers it (13 and 14 land
with their mechanisms: preferences in P4, the site in P5). Enforced in
`tests/test_mutation.py`, over every mutation entry point in `src/`:

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

**Which test holds each.** `tests/test_meta.py`'s `INVARIANT_CLASSES` maps every number
above to the classes that try to break it, and this table is that map, row for row:
`EveryInvariantHasItsTest` goes red on a number nothing maps, `TheInvariantTableIsTheMap`
on a row that names other classes than the map, and `InvariantClassesNeverSkip` on any
skip inside them. (What slipped through: each group above named its test files once,
the day it landed, and Phase 2 grew invariants 2, 4, 7 and 9 in files no sentence here
named.)

| # | Classes |
|---|---|
| 1 | `test_invariants.SkipIsNotPass`, `test_invariants.CheckedMeansEveryEvaluatorPassed` |
| 2 | `test_invariants.ErrorIsNotPass`, `test_louder.ErrorIsLouder`, `test_invariants.UnqualifiedIsTheSpinesWord`, `test_prerequisites.AnErroredPrerequisiteStaysLouder` |
| 3 | `test_invariants.RegistryRefusesLoggers` |
| 4 | `test_invariants.ReportNeverOverclaims`, `test_status_table.UnqualifiedBesideAPassIsNeverChecked`, `test_invariants.CheckedMeansEveryEvaluatorPassed`, `test_goalposts.APassMustMeetTheAcceptanceItRead`, `test_goalposts.AValueOutsideTheAcceptanceIsNeverChecked`, `test_goalposts.AnEvaluatorStatesItsOwnUnits`, `test_physical.TheCheckedSectionHoldsOnlyBoundResults` |
| 5 | `test_packs.ControlsAreSealed` |
| 6 | `test_packs.NegativeControlsFire` |
| 7 | `test_staleness.StaleIsNotCurrent`, `test_goalposts.TheGoalpostLivesInClaims`, `test_physical.AMovedArticleReadsStale`, `test_fig4.TwoArticlesExactly` |
| 8 | `test_records.IndexNeverDisagreesWithRecords`, `test_records.NoCommandWritesARecord`, `test_records.MilestonesAreRecords`, `test_records.TheExportRecordIsSealedAndChained` |
| 9 | `test_admission.AdmissionIsDemonstrated`, `test_admission.QualificationIsPaired`, `test_admission.EveryConclusiveMutationMustFail`, `test_admission.TheCheckRunTakesTheQualifiedPath`, `test_context.OutsideTheContextAPassDoesNotCount`, `test_context.AFailOutsideStillCounts`, `test_context.KnownGoodOutsideIsUnqualified`, `test_context.AMutationPassingOutsideStillCounts`, `test_goalposts.AGoalpostIsNeverAKey` |
| 10 | `test_prerequisites.PrerequisiteFailureIsNeverAPass`, `test_prerequisites.ACachedPassNeverSurvivesAFailedPrerequisite`, `test_prerequisites.NeedsCycleRefused`, `test_prerequisites.TierInversionRefused` |
| 11 | `test_owner.AnOwnerWrittenByHandNeverCounts`, `test_signing.HumanChannelOnly`, `test_signing.WhoAndWhenAreNeverTyped`, `test_signing.TheResultsFileIsSealedAndChained`, `test_signing.AnOwnerOnlyThroughTheChannel`, `test_physical.SignedMeansSomething`, `test_physical.APhysicalFailNeverLosesItsPowerToFail`, `test_physical.AContradictionGoesOnTheEvaluatorsTrackRecord`, `test_physical.AnExpertJudgmentStaysWithItsAuthority`, `test_physical.AResultBindsToAnExportedArticle`, `test_physical.AFailIsSupersededOnlyOnAnotherExportedArticle` |
| 12 | `test_physical.RenderersAgreeOnPhysicalClaims`, `test_export.ReadyIsOnePredicate`, `test_export.TheBoundaryReExecutes`, `test_export.ACacheThatLiesIsCaughtAtTheBoundary`, `test_export.DryRunIsTheSamePath`, `test_export.GoingAheadIsAPersonsDecision`, `test_vocabulary.TheHardwareClauseIsAlwaysSaid`, `test_export.EveryCacheReaderSaysAsLastEvaluated` |
| 15 | `test_mutation.MutationIsSealed` |

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
  `views/*.json`, `milestones/<name>.json` (a spend and the claims it requires),
  `exports/<name>.json` (that spend's exports, sealed and chained, appended only by
  `export`), and `.atompipe/project.json` (meta, `model_entry`, the live `packs`).
  `results/<claim-id>.json` holds a claim's physical results and its owner's or
  authority's attributions, sealed and chained, appended only by `claim physical`
  (P2.5a): never edit one by hand — a broken seal refuses the file.
- **The model** — `model/*.py` owns every parameter's value, units and rationale, and
  in `PARAMS` what lost to it; a param record holds only the provenance the model
  cannot (`source`, `grounded_by`, `tags`).
- **The verdict cache** — tracked, `.atompipe/verdicts/<gate id>/`: one file per
  rho (the hash of everything the gate read) and one per control outcome at a
  version, never rewritten. Git plus this cache is the history; there is no run history.
- **Outputs, ignored** — `.atompipe/ledger.json` (the index of every record, rebuilt
  after each command), `.atompipe/cache/last_check.json` (statuses, counts and the
  parameter view as the last full `check` saw them), `.atompipe/obs/` (what each run
  cost), `.atompipe/out/` (scratch and evidence), `REPORT.md` (the readiness report,
  `report --write`) and `out/<milestone>/` (the package `export` wrote). The index
  and `last_check.json` are the whole project in two reads; nothing reads either for
  truth.

`docs/SPINE_CONTRACT.md` ("Where facts live", then `store.py`'s section) has the
layout in full, with each writer.

## Layout

| Path | What |
|---|---|
| `src/atompipe/` | the spine — stdlib-only modules; `models.py` is the type contract |
| `src/atompipe/store.py` | the records: one file per record, the strict reader, the writer, the generated index, the one-time legacy migration |
| `src/atompipe/milestones.py` | the boundary that spends: a milestone's closure, the export's refusals and judgment, disagreements, the test card, the package |
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
