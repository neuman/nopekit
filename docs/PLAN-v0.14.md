# PLAN amendment — paper v0.14 ("Grounding by Falsification") and the walkthrough

This amends [`PLAN.md`](PLAN.md), which was written against paper v0.5. **Where the two
disagree, this file wins**; a PLAN.md row this file does not name stands as written, and
its ids (`M§.n`, `D-nn`, `R-n`, `S-nn`, `A-n`, `G-n`, `Pp.k`) keep their meaning. It is a
hand-written source and a plan, not a record of work done. File references are at
`0474f5c` (Phase 1 complete: records as files, per-gate ρ, admission at `check`, JUnit).

Three inputs, all binding: paper v0.14; the walkthrough the user accepted (one person
taking a new project from an empty folder to a first physical article, eleven steps); and
the user's answers to the bench plan's asks. Human-facing words are not decided here:
[`docs/GLOSSARY.md`](GLOSSARY.md) is the one source, one term per concept, taken from the
paper. This file uses GLOSSARY's words (operating context, not calibration range;
retained, not dormant); code identifiers keep theirs until the rename pass. The case study is always "the case study (paper §8)", never named (`NoLeakedProvenance`).

**What this file may say about the bench.** Every phase is scored by the private bench
(§3). The agents it measures run nopekit in a sandbox, from a release bundle, and they
must share no priors with the agents that build it: an agent that has read the test
answers the test, not the tool. So this file names the suites and what each measures,
and nothing else — no scenario, oracle, expected status, persona, revision id or
threshold. A repo test born from a bench finding uses neutral names and a generic shape.
*Added after the bench plan was approved*, when the user named the risk ("make sure the
agents running nopekit in the sandbox tests don't know all this info, they can't have
shared priors"): a plan that describes its own exam leaks it to whichever reader can
open `docs/`.

- **The bundle strips** (one list, the bench's and this file's): `docs/PLAN*`,
  `docs/plan/`, `docs/BENCHMARK_ENGINE.md`, `tests/`, `evals/` (cases and graders are
  tests), the paper, **`docs/GLOSSARY.md`** (it quotes the paper and maps the paper's
  status semantics onto code) and **`docs/ORIGINS.md`** (it names the parent project,
  a prior no sandboxed agent should hold). The links to ORIGINS from README, METHOD
  and CLAUDE.md dangle in the bundle; that is the cost, and it is the honest one. The
  bench's own strip list is held to this one.
- **What ships** (README, METHOD, CLAUDE.md, the contracts, skills, packs, examples,
  `src/`) mentions the bench nowhere, and the words it uses come from GLOSSARY through
  code (`report.HUMAN`) and the skills, never from a copy of the paper.
- **Shipped doctrine can still carry a prior.** METHOD's rules recount past incidents,
  and a sandboxed agent that has read one is not naive about it. So B1's self-test also
  searches the bundle for each scored scenario's distinctive terms, and a scenario whose
  incident appears in shipped text is scored as contaminated and reported apart. Removing
  the anecdotes from METHOD is an ask (A-13), not decided here.

---

## 1. What v0.14 changed that the plan must absorb

### 1.1 Words and anchors

| PLAN (v0.5) | v0.14 | Where it bites |
|---|---|---|
| refusal, refuser, refusal source, refusal graph | falsification, evaluator, falsification graph F (§3) | prose throughout; "refuse" stays as plain English for a command refusing |
| B_R, G_r | B_F (falsification capacity), G | M6.1–M8.1 |
| r = (I, L, C, S, D) | latency, cost, coverage, independence, **provenance** (§3); D is no longer a descriptor — prerequisite edges are part of F | M1.2, M3.*; `needs` stays, justified by §6.1's cascade |
| admission; negative control; known-good input | **qualification**; known-bad control; known-good control (§6.3) | D-07, D-25–D-27, M2.1c–d, invariant 9 |
| executable tradespace T = (P, V, φ, C, u, L, G) | grounded design state S = (T, F, L), T = (P, V, φ, u); the generator is outside S (§7) | M0.1, M9.1 |
| resurrection, dormant alternatives | **recovery** of rejected candidates by their return conditions (§6.4, §7) | M11.10, P3.1 |
| taste, x ≻_H y | preference; "preferred against" (§6.4) | M12.*, D-18 |
| "verified by a calculation" (D-16, M2.2a, M18.1, A-11) | **Checked** — "does not mean true"; *verified* and *validated* now mean V&V (§3, §6.3) | every human channel |
| terminal refuser, "ends in", "no refusal source yet" | where evidence "bottoms out" (§3); Table 1's Gap and Pending build | M14.1–M14.2; the `terminal` field stays |
| one word "decision" | split by GLOSSARY: a **milestone** is a declared spend (§2.3's decision scope); a **decision** is a design choice | `milestones/` vs `decisions/` |

v0.5 section anchors in every "Paper says" cell re-point as follows: §2.1–§2.2 → §6.3;
§3–§4 → §3, §6.1; §5–§8 → §2, §5; §9–§10 → §6.4, §7; §11 → §6.2 (resurrection → §7);
§12 → §6.4, §7; §13 → §7 ("propose"); §14 → §8; §15–§16 → §5; §17 → §9 and Table 2;
§18–§20 → §10–§11. Footnotes are gone; fn 4, 16 and 18 are references [5], [10], [12], [13].

### 1.2 Experiments, renumbered E0–E7 (paper §9, Table 2)

| v0.14 | Question | Was | What changes for the plan |
|---|---|---|---|
| E0 | Do falsification measures explain cross-domain leverage? | E8 | data-only; descriptor export is the repo's whole share |
| E1 | Are generated evaluators vacuous? With vs without controls; known-bad **and mutation-derived** inputs | E1 | wording only |
| E2 | Is invalidation accurate? Missed invalidations, unnecessary reruns, stale approvals, **rebuild predictions**; vs full reruns and a **dependency-graph baseline** | E4 | adds rebuild prediction (§1.5) and a coarser baseline arm; replays real history on the private bench |
| E3 | Does sequencing help? Run-all vs authored order vs cost-per-rejection; p_i fit on one portion, scored on held-out changes | E2 | was "not built" (D-31); now P4 |
| E4 | Do physical results contradict checked claims? | new | a physical result must be recordable against a claim an automated evaluator Checked (P2) |
| E5 | Does it transfer and beat a documented conventional workflow? Includes **candidates recovered** | new (absorbs old E5's resurrection rate) | old E5 mechanism test keeps its job under a new name |
| E6 | Does accepted throughput saturate when Λ > 1? Adds survival rate σ | E6 | σ observable from P4 trade history |
| E7 | Does raising B_F pay more when Λ > 1? Reported **by Λ regime** | E7 | needs Λ₀ (§1.3) |
| — | Proposer–validator dependence | E3 | dropped as a study; independence survives as an evaluator property |

`tests/test_staleness.E4Localisation` now names the wrong study. When P2 next touches it,
it is renamed without a number (a strengthening-neutral rename, listed under R-6), so the
next renumbering costs nothing. *Done in P2.1 (`dd80e2c`):* `test_staleness.InvalidationIsLocalised`.

### 1.3 Moved from declined to required: sequencing and Λ

**Sequencing.** PLAN declined it (M4.3, M15.2, D-31, §9 "No refusal scheduler") on the
brief's reason: no scheduler on declared numbers before there is history. v0.14 makes it
one of three required capabilities (§6) and says it is "under development" (§8); E3 needs
it. The reason survives as a constraint, not a veto: c_i comes from measured `duration_s`
and `cpu_s`, p_i from rejection rates recorded in the verdict cache's history, and a gate
with no history keeps topological order. The one declared input is the cost of a physical
evaluator (print hours, a board spin), shown as declared until an article measures it.
Run-all and authored order stay selectable, because E3 compares against them. Stopping
leaves unrun claims open, and proceeding with open claims is a recorded decision (§6.1).
Lands in P4.

**Λ.** PLAN said Λ is never computed (§1, M6.1, M18.9, §9 "no Λ on screen"), citing v0.5
sections that no longer exist. v0.14 §5.3: "Measure Λ for the decisions that matter."
So Λ₀ is computed **per milestone**: generation time measured, falsification latency
expected (declared on the physical claim) until measured. It is printed with both inputs
and which of them is declared, and never aggregated into one number for a project or put
on the site as a score. What stays from the old stance: no universal metric. Lands in P4.

### 1.4 Table 1 statuses against the repo's ClaimStatus

| On `0474f5c` | Table 1 (§3) | Change | Ph | Proof |
|---|---|---|---|---|
| PASS | Checked | every evaluator qualified (§1.5) and passed; a pass beside a skip, an error, an unqualified or an unrun evaluator no longer reads PASS (D-01; GLOSSARY §3's composition) | closed P2.1, closed P2.3 | `test_invariants.CheckedMeansEveryEvaluatorPassed`, `test_invariants.SkipIsNotPass`; P2.3's qualified: `test_admission.QualificationIsPaired` |
| FAIL, a gate ran and rejected | Failing | — | — | — |
| FAIL, a gate **errored** (`claims.py` rung 4) | Skipped / errored | leaves FAIL; still blocks; still never a pass; still louder than a missing tool (invariant 2, §1.5 row "Errored stays louder") | closed P2.0, closed P2.1 | `test_louder.ErrorIsLouder`, `test_invariants.ErrorIsNotPass`, `test_renderers.RenderersAgree` |
| FAIL, gate **not admitted** (`verdicts.py`, "not admitted: …") | Gap, reason "evaluator unqualified" | §7: "a claim with no qualified evaluator remains a gap"; also beside a passing evaluator | closed P2.1, closed P2.3 | `test_status_table.UnqualifiedReadsGap`, `test_status_table.UnqualifiedBesideAPassIsNeverChecked`, `test_status_table.KnownBadShownIsAGap` |
| STALE | Stale | an invalidated FAIL stays Failing (D-08) | — | — |
| ASSERTED (`kind=assumption`) | Assumed, **only with a reason and an owner** | no owner → Gap. The owner is set only through the signing channel (D-12, D-13, both P2); an owner written any other way — a hand or agent edit of the claim file — reads unattributed and the claim stays Gap. P3's permission rule (W9) then refuses the agent's edit outright: P2 records it, P3 enforces it. Assumed is unresolved (GLOSSARY §3) | closed P2.1, closed P2.5a | `test_owner.AnOwnerWrittenByHandNeverCounts`, `test_signing.AnOwnerOnlyThroughTheChannel`; P3 enforces (W9) |
| UNCLAIMED | Gap | word | closed P2.1 | `test_vocabulary.StatusWordsAreTheGlossarys` |
| BLOCKED (all skipped; prerequisite unknown, D-02) | Skipped / errored | word | closed P2.1, closed P2.2 | `test_vocabulary.StatusWordsAreTheGlossarys`, `test_prerequisites.PrerequisiteFailureIsNeverAPass` |
| PENDING (never run) | *no row* — paper feedback F2; GLOSSARY's **Open** | — | closed P2.1 | `test_invariants.SkipIsNotPass.test_a_pass_beside_an_unrun_gate_is_open_and_blocks` |
| UNVERIFIED (physical, no result) | Pending build | word | closed P2.1 | `test_vocabulary.StatusWordsAreTheGlossarys`; never built: `test_fig4.FalsificationGraphOfFigure4.test_the_base_case` |
| VERIFIED (physical pass) | Checked, bound to the article's hash; **Stale** when the article's read set moves, its reason naming the article to rebuild (a rerun cannot restore it) | S-50. *Not Pending build:* Table 1's Stale is exactly "previously checked evidence no longer matches current inputs", §8 says the change "should stale … fit claims", the walkthrough says the result "goes stale", and invariant 7 already says a moved PASS reads STALE. Fig. 4's physical claim "stays pending build": it had no result to invalidate, and P2's Fig. 4 test builds it that way | closed P2.5a, closed P2.5b | `test_physical.AMovedArticleReadsStale`, `test_physical.AResultBindsToAnExportedArticle`, `test_fig4.FalsificationGraphOfFigure4.test_a_physical_pass_on_a_moved_article` |
| REFUTED | Failing — and if an automated evaluator had Checked the same claim, a **contradiction** on that evaluator's record | E4 | closed P2.5a | `test_physical.APhysicalFailNeverLosesItsPowerToFail`, `test_physical.AContradictionGoesOnTheEvaluatorsTrackRecord` |

R-8's P2 oracle stands as PLAN.md words it: statuses move only in the blocking
direction, and the phase commit lists every changed claim with its reason. "Blocking"
here is GLOSSARY's *unresolved*, so ASSERTED → Assumed or Gap, and VERIFIED → Stale, both
move toward it. The bracket's C6 gains an owner (A-15, §4.2: until its owner records it
through the signing channel, C6 reads Gap and G4 holds it so), so G4's pinned signature
moves only in words — unless the mutation pass leaves a bracket gate unqualified (§1.5), in which case
`tests/expected_bracket.json` changes in the P2 commit with its reason.

*As P2 closed:* R-8's oracle, run against `860ffa6` (`tests/oracle/r8_statuses.py --base
860ffa6 --toward-unresolved`, and its in-suite copy
`test_status_table.StatusesMoveOnlyTowardUnresolved`), finds one move on the corpus: C6
ASSERTED → Gap (`no-owner`) on the bracket at 7.0 and at 8.0, BLOCKING gaining C6, exit
codes unchanged; the seven pack baselines are unchanged. That is A-15's move, and it is
why G4's signature moved in P2.1 (`dd80e2c`, with that reason): C6 joined `claims.critical`
as `unclaimed`, and C5's skip message took Pending build's words. No bracket gate is
unqualified (`test_bracket_cache.BracketIsQualified`).

### 1.5 New mechanisms the plan had no row for

| Mechanism (§) | Rule | Ph | Proof |
|---|---|---|---|
| Assumed needs an owner (§3) | `Claim` gains an owner; an assumption nobody owns is not accepted, so it reads Gap | closed P2.1, closed P2.5a | `test_owner.AnOwnerWrittenByHandNeverCounts`, `test_signing.AnOwnerOnlyThroughTheChannel` |
| Unqualified evaluator = Gap (§7) | see §1.4 | closed P2.1, closed P2.3 | `test_status_table.UnqualifiedReadsGap`, `test_status_table.KnownBadShownIsAGap` |
| Qualification (§6.3), per kind of evaluator | §6.3's rule is the two controls: known-good passes, known-bad fails, both retained and rerunnable. **Every** evaluator meets it: the 54 bundled pack gates through their baselines and fixtures, and project gates through a new known-good control. A **project** evaluator — one the generator wrote in the groundspace, where §6.3's correlated-failure risk lives — also passes a **mutation pass**: the walkthrough's addition, which §6.3 counts toward confidence ("controls, mutation-derived failures, independent review…"), not toward the rule. Mutation is old 5.1 redesigned so that P2 can hold it: in process, on the known-good control, changing the parameters its run read; no tree is written, so it needs neither `vcs.snapshot` (P4) nor `modelio.influence` (P3). Every conclusive mutation must fail. An inconclusive one (it misses the read set, or the run skips or errors) counts neither way and is printed as such, never as a fail; with no conclusive mutation the line says so (P2 panel default: qualified on the controls, the line showing `mutation 0 conclusive`). One line per evaluator, in GLOSSARY's outcome words — the walkthrough's line translated: `known-good pass · known-bad fail · mutation n/n fail → qualified` | closed P2.3 | `test_admission.QualificationIsPaired`, `test_admission.EveryConclusiveMutationMustFail`, `test_packs.KnownGoodControlsPass`, `test_bracket_cache.BracketIsQualified`, `test_mutation.MutationIsSealed`, `test_shapes.QualificationLineShape` |
| Operating context (§6.3; §8: a claim "may return to assumed" when its evaluator was calibrated only for the original context) | an evaluator may declare the range of its read set it was qualified on. Outside it a **pass** does not count: the claim reads Assumed when it has an owned fallback assumption, otherwise Gap. A **fail** outside it still counts and the claim stays Failing: a result never loses its power to fail (R-3). Inside the range: Stale and rerun, as now | closed P2.4 | `test_context.OutsideTheContextAPassDoesNotCount`, `test_context.AnOwnedFallbackReadsAssumed`, `test_context.AFailOutsideStillCounts`, `test_context.AChangeInsideReadsStale` |
| Errored stays louder (invariant 2) | once errored reads Skipped, "louder than a missing tool" can no longer ride on FAIL, so it is carried in four places: the reason starts `errored:`; the row sorts above every skipped row and takes Failing's tone; every count, sentence and heading splits it out (`2 skipped (1 errored)`); and the claim stays a JUnit `<error>`, critical or not | closed P2.0, closed P2.1 | `test_louder.ErrorIsLouder`, `test_prerequisites.AnErroredPrerequisiteStaysLouder` |
| Rebuild prediction (§8, E2) | when a change reaches the read set of evidence bound to a physical article, the report names that article as needing a rebuild, and names nothing else | closed P2.5a, closed P2.5b | `test_physical.RebuildPredictionIsExact`, `test_fig4.TwoArticlesExactly` |
| Physical evidence is article-bound (§6.2) | a result carries ρ and the article's hash; R-3 holds: it loses its power to check, never its power to fail | closed P2.5a | `test_physical.AMovedArticleReadsStale`, `test_physical.APhysicalFailNeverLosesItsPowerToFail` |
| Track record per evaluator and operating context (§6.3) | a contradiction is recorded against the evaluator at its code digest; anything cross-project is deferred | closed P2.5a | `test_physical.AContradictionGoesOnTheEvaluatorsTrackRecord` |
| Recovery with return conditions (§6.4, §7) | infeasible / dominated / preferred against / unevaluated, each with the return condition the paper states; `returned: {candidate, was, because}` when one holds | P4 | — |
| Groundspace (§7) | the versioned sequence ⟨S0 … Sn⟩ of grounded design states for one design problem = the project's git repo. Sn = a commit, reproducible because the verdict cache is tracked. T: P = the model's Config plus `params/`, V = candidate branches, φ = critical claims and their covering gates, u = `objectives.json`. F = read-set edges (P1) ∪ `needs` (P2) ∪ derived→input edges (P3). L = records, verdict cache, `results/`, dispositions. Replaces M9.1 | B0 (words), P3 (state dir), P4 (V) | — |

### 1.6 Implementation claims the paper makes, which must be true

| # | Paper (§) | On `0474f5c` | Made true by |
|---|---|---|---|
| C1 | "six packs … 46 evaluators" (§8) | 7 packs, 54 gates; the six named hold exactly 46, and openmodelica's 8 are left out | **paper feedback F1** — the repo does not drop a pack to match a sentence |
| C2 | "Evaluator qualification … implemented" (§8) | known-bad half enforced at use; known-good half for packs only; every project gate admitted reject-only | **closed P2.3** — true from P2.3: both halves at use, for every evaluator, and the mutation pass for every one not from a bundled pack (`test_admission.QualificationIsPaired`, `EveryConclusiveMutationMustFail`) |
| C3 | read-set staleness implemented | true (per-gate ρ, `freshness()`) | — |
| C4 | ledger rendering implemented | true | — |
| C5 | sequencing "under development" | declined | this amendment, then P4 |
| C6 | cross-project confidence "under development" | not planned | **closed P2.5a** for the per-evaluator record (`verdicts.track_record`, keyed by code digest: `test_physical.AContradictionGoesOnTheEvaluatorsTrackRecord`); beyond that deferred, said so in §3 |
| C7 | the ledger "records where judgment must remain with a named human or institution" (§1, present tense) | false | **closed P2.5a** for the record: recorded from P2.5a (`terminal` + `authority`, the authority's judgment entered through the signing channel, `test_physical.AnExpertJudgmentStaysWithItsAuthority`); P3 enforces it (W9's permission rule) |
| C8 | a claim with no qualified evaluator remains a gap (§7) | false (→ FAIL) | **closed P2.1, P2.3** — the not-admitted half true from P2.1 (an evaluator refused at its version reads Gap, beside a pass too); the known-bad-shown half true from P2.3 (`test_status_table.KnownBadShownIsAGap`, which replaced `RejectOnlyStillCounts` in the open) |
| C9 | a model may propose "reviews" (§8) | no review record exists | paper feedback F5, or a review record later |
| C10 | a ledger row shows its ρ (Fig. 9) | ρ per verdict; not printed on every row | P3 |

---

## 2. The walkthrough deltas

| # | Walkthrough (step) | On `0474f5c` | Ph | Done when | Proof |
|---|---|---|---|---|---|
| W0 | discovery: the README's first screen is the one-line promise, a 60-second bracket demo (clone, `check`, a real failure on the first run, lit on the part in the site) and the plugin install (1) | README opens on the promise and the chain; no demo block; the bracket has no `site/` | P3, P5 | P3: the first screen holds the promise, the demo's clone-and-`check` lines with the failure they print, and the install from a local path (A2: nothing is pushed); P5: the demo opens the bracket's site on the failing part | — |
| W1 | `.groundspace/` is the state directory (2) | `.nopekit/` | P3 | §2.1 | — |
| W2 | scaffold: `model/ claims/ inputs/ gates/ decisions/ milestones/ .groundspace/ site/ AGENTS.md CLAUDE.md` (2) | `init` makes part of it | P3 | `init` in an empty dir writes exactly this; in an existing project it adds and never overwrites | — |
| W3 | milestones: named spends, each with its required claims; physical claims carry an expected latency (3) | one boundary, planned (`export`, old 2.5) | closed P2.5b | `export <milestone>` refuses while a required claim is unresolved — anything but Checked, an owned Assumed claim included (GLOSSARY §3, where *ready* is the same predicate); going ahead anyway is a recorded decision that names the claim (§6.1); the latency is read from the claim until measured | `test_export.ReadyIsOnePredicate`, `test_export.GoingAheadIsAPersonsDecision`, `test_records.MilestonesAreRecords`, `test_physical.LatencyIsDeclaredUntilMeasured` |
| W4 | the three-line status block after every turn: counts, what changed or fails, `next:` (2, 4, 5) | `next:` is a constant (S-66) | P3 | Stop hook prints it whenever model or records moved; shape-tested with a negative control (R-11); `next:` computed | — |
| W5 | `ask --next`: one ask, why and how, from state (2) | `ask` lists by kind and ignores state | P3 | exactly one ask; one fixture per rank | — |
| W6 | ingest and extract; `inputs --unextracted` held empty (2) | both exist; nothing holds the agent to it | P3 | the status block counts inputs vs extracted, and an unextracted input becomes `next:` | — |
| W7 | `gap --propose` writes a project gate with known-good and known-bad controls and a mutation pass; Gap until qualified; the qualification line (6) | `--propose` lists matching packs | closed P2.3, P3 | the line in §1.5 appears in the status block, not prose; until it reads `qualified` the claim is Gap | P2.3: `test_admission.QualificationIsPaired`, `test_status_table.KnownBadShownIsAGap`, `test_shapes.QualificationLineShape` (the line on `check` and `gate selftest`); P3: `gap --propose` writing the evaluator, the line in the status block |
| W8 | `terminal` + `authority`; a human-terminal claim stays Assumed under its authority's name (6) | neither field | closed P2.5a | only the named authority settles it, through the signing channel (D-12, D-13) | `test_physical.AnExpertJudgmentStaysWithItsAuthority`, `test_signing.HumanChannelOnly` (Gap until the authority records it, then Assumed under their name: P2.5a-D17, on the check-in batch) |
| W9 | the permission rule refuses agent edits to a human-terminal claim file (6) | no hook | P3 | V: an agent edit to that file is refused | — |
| W10 | `trade`: a branch per candidate, gates on each, a table of **only the differences**; the pick is a preferred-against record with who, why, reconsider-when; the loser is retained as a rejected candidate, with its evidence (7) | none | P4 | §3, P4 | — |
| W11 | `/ready <milestone>`: required claims checked and stale, the Λ₀ line, export with its hash, a test card (8) | none | closed P2.5b, P3 (`/ready`), P4 (Λ₀) | one code path with `export --dry-run` (D-15) | P2.5b: `test_export.DryRunIsTheSamePath`, `test_shapes.ExportShape` (the milestone line, the article and package hashes, the test card), `test_export.ThePackageIsWhatWasRecorded` |
| W12 | `/tested`: a physical result with ρ and the article's hash; Stale on a geometry change, its reason naming the article to rebuild; a contradiction when a gate had passed the same claim (8) | unsigned, typed `who`, survives any change (S-48, S-50) | closed P2.5a, closed P2.5b, P3 (`/tested`) | §1.4 last two rows | `test_physical.AMovedArticleReadsStale`, `test_physical.AResultBindsToAnExportedArticle`, `test_physical.AContradictionGoesOnTheEvaluatorsTrackRecord` |
| W13 | the honest readiness sentence names what is untested in hardware (9) | readiness sentences exist | closed P2.5b | words from GLOSSARY; shape-tested | `test_vocabulary.TheHardwareClauseIsAlwaysSaid`, `test_shapes.ExportShape`, `test_shapes.PhysicalLinesShape` |
| W14 | `why <param>`: one parameter's whole history through derived reads, including the values that lost (10) | direct reads only (S-81) | P3 | names every gate the parameter reaches through a derived value, and its losers (D-30) | — |
| W15 | `pack extract`: a project-born pack promoted with its controls and record (6, 11) | none; D-24 forbade `pack export` | P3 | D-24 amended to allow it; the extracted pack passes `pack validate` and `gate selftest` unedited | — |
| W16 | site: flow canvas, scrubber, ego graph, test card, candidates side by side (4, 5, 10) | old P4 plan | P5 | §3, P5 | — |
| W17 | sharing: bucketed records to a pack's repo; publishing to an index (8, 11) | none | — | **deferred**: outward-facing, ask first | — |

### 2.1 The state directory: `.nopekit/` → `.groundspace/`

- **What moves:** the directory only. The package, module, CLI and environment names do
  not; those belong to the rename pass and its table in GLOSSARY, which leaves the
  directory out and points here. This reconciles the bench plan's reading of A4 (its
  "What it means" column: directory names are renamed in the later nopekit pass;
  the user's own words were "use language from the paper and choose just 1 term for each
  thing"): `groundspace` names the paper's object, not the product, so it is not a
  rename-table entry.
- **When:** P3, in the checkpoint that rewrites `init`, behind a **one-time migration**
  under invariant 8's existing carve-out as CLAUDE.md states it — only `check` and the
  kept writers (`ingest`, `extract`, `decide`, `packs add`, `claim physical`) perform it,
  and P3 adds `start`; every other command and every hook migrates in memory and writes
  nothing. `init` does not migrate. If P3 finds it must, that is a change to invariant 8,
  listed under R-6 with the `test_records.NoCommandWritesARecord` edit it needs.
- **First, one constant.** The state directory is spelled at least eight times under
  `src/` today: `store.NOPEKIT_DIR`, `verdicts._STATE_DIR`, a literal in `gates.py`
  (`GateContext`'s out dir), `report.JUNIT_DEFAULT`, `verdicts.CONTROL_OUT_DIR`,
  `verdicts.CONTROLS_CACHE`, and three entries of `verdicts.WATCHED` — the one export
  P3's Stop hook imports. One of them is the file-trace exclusion: a trace that stopped
  excluding the state directory would put the cache inside its own read set. R-1, before
  anything moves: a test that the state directory's name appears as a string constant
  under `src/` exactly once (AST, docstrings and comments excluded), with a planted second
  literal as its violator, and every other spelling routed through it. A missed
  `CONTROLS_CACHE` would recreate `.nopekit/` on the next `check`, and the
  both-directories rule would then refuse every command; a missed `WATCHED` would leave
  the status block blind to the cache. `packs.py` reuses `NOPEKIT_DIR` for the user's
  pack store, `~/.nopekit/packs`: that names the tool, not a groundspace, so it gets a
  constant of its own and does not move.
- **Done when:** after migration every entry's ρ is unchanged and `check` executes zero
  gates; git sees renames only; a project holding both directories is refused, naming both;
  running it twice is a no-op; the Stop-hook fingerprint (`verdicts.WATCHED`) sees a cache
  entry added after migration.
- *Rejected:* B0, because B0 changes nothing under `src/`; P2, because
  R-8 exists to stop one phase from changing claim semantics and every fact's address at
  once; waiting for the rename, because the layout is part of the experience the check-in
  judges.

### 2.2 Where the walkthrough meets a settled row (judge-panel questions, with defaults)

| Q | Conflict | Default |
|---|---|---|
| Q-W1 (P4) | the walkthrough's `trade add --set k=v` vs M13.8 and `V: NoGenerator` (`trade new` takes no parameter values) | keep NoGenerator: `trade new` makes the branch, the proposer edits the model on it. The experience judged is the branch and the table, not the flag. *Rejected:* `--set`, which makes the spine write a candidate's values |
| Q-W2 (P3) | the scaffold lists no `selftest/` or `params/` | keep both (PACK_FORMAT, D-30); the walkthrough's list is what a newcomer sees first, not an exhaustive one |
| Q-W3 (P3) | the walkthrough's terminals include "external tool" | the `terminal` set stays M14.1's; GLOSSARY maps the display word |

---

## 3. The phases, re-sequenced

Supersedes PLAN §2.1's "Phases 0–5 as listed" and §4.1–§4.5's order; each phase keeps
§4.0's safety case (R-1 to R-14, G1–G8) and its workflow — understand, judge panel,
worktrees, ≥ 3 refuters per finding, fix, G-gates — and adds, before the commit, **a
private-bench run and its scorecard diff**. Each phase states an agent budget and logs
what it drops.

| Phase | Scope (old checkpoints absorbed) | Done when |
|---|---|---|
| **B0** Housekeeping, re-baseline | branch = `0474f5c`; this amendment and PLAN.md's pointer; `docs/GLOSSARY.md`; `tests/fast.py` with `tests/test_stdlib_only.py`, which it imports (both untracked until B0's commit, and committed together); `test_invariants.ErrorIsNotPass`'s pass-beside-an-error test; the openmodelica slip fix (a library that does not load reads skipped, not failing, and `installing.md`'s `-minimal` claim corrected), with `tests/test_openmodelica_library.py` committed first (R-12) | nothing under `src/` changes; G1–G6 green in a clean worktree; `tests/fast` holds at least one violation test per CLAUDE.md invariant and runs every iteration, and the full suite gates every checkpoint and every phase commit — the fast loop can miss an invariant break that only a left-out test sees, so its module docstring names which iterations run which modules whole; `tests/fast` runs in ≤ 90 s as one process on an otherwise idle machine (under parallel load it has run 94 s), re-timed whenever `FAST` changes |
| **B1** The private bench | outside this repo; no repo change | every suite goes red against a null nopekit that answers "all checked"; every suite's known-good fixture passes its scorer; the sandbox self-test passes (what the bundle strips is absent, hidden canaries unfindable, a run-directory canary found); a shipped-prior check: the bundle is searched for each scored scenario's distinctive terms, and a scenario whose incident appears in shipped text is scored as contaminated and reported apart; a baseline scorecard keyed to `0474f5c` itself, so it measures Phase 1 unchanged (B0's openmodelica fix moves a tier-2 claim from failing to skipped on an omc without the library), and B0's commit scored beside it |
| **P2** Statuses, authority, milestones, the physical path, qualification | old 2.1–2.5; old 5.1's mutation, redesigned for qualification (§1.5: in process on the known-good control, no tree, no `vcs.snapshot`, no `modelio.influence`); §1.4, §1.5 (P2 rows); W3, W7–W8, W11–W13 | **first (P2.0, R-1), green before rung 4 or any renderer changes:** `ErrorIsLouder` — across every renderer an errored claim's reason starts `errored:`, sorts above every skipped row, takes Failing's tone, is counted apart and is a JUnit `<error>`, with a planted renderer that sorts it below as its violator; `MutationIsSealed` (the mutation half of PLAN invariant 15) — mutation writes nothing in the real tree or in a pack directory, and an inconclusive mutation is never reported as a fail, with a planted mutation that writes as its violator; `test_invariants.ErrorIsNotPass`'s pass-beside-an-error test (landed in B0) still green. **Then:** §1.4 is the outcome vocabulary, every human word drawn through one table (D-16) from GLOSSARY; errored and unqualified never read Failing or Checked, beside a pass or not; Assumed only with an owner set through the signing channel; `needs` with unknown; all 54 bundled gates qualified by their paired controls, and the bracket's 6 project gates by both controls and a mutation pass; physical results article-bound with contradictions on the evaluator's record; operating contexts; rebuild prediction; `export <milestone>` re-executing at the money boundary (R-9); the paper's Fig. 4 as a repo test with neutral names — its physical claim never built, so it stays Pending build — and controls for each rule (a change it must not reach, an unregistered read, operating context removed, owner removed, a change inside the context, a fail outside the context stays Failing, a physical pass on an article whose read set moved reads Stale and names that article for rebuild); R-8's oracle listing every moved status; G4's signature unchanged or changed in the P2 commit with its reason (§1.4); under R-6, `test_renderers`' expected table and its "an error beats a skip" case move from FAIL to Skipped/errored, beside the E4Localisation rename; CLAUDE.md invariants 2 (reworded: errored reads Skipped, and louder, GLOSSARY §9), 4 (no partial), 9 (paired), 10, 11, 12 (readiness, export) |
| **P3** The agent surface | old 3.0, 3.3, 3.4; W1, W2, W4–W6, W9, W14, W15; C10 | `init` scaffold and the `.groundspace/` migration (§2.1); the three-line block from the Stop and PostToolUse hooks; `ask --next`; extraction held; `gap --propose` running qualification; `why` through derived reads; `/start /check /status /ask /ready /tested`, one CLI call and one fixed shape each; the permission rules; the skill quoting verdict lines verbatim and saying "checked" only where the ledger does; one pack born in a project through EXTENSION_PROTOCOL and promoted with `pack extract`; G7, G8 (as amended in §4.1: the skill evals run in the sandbox); invariants 11 (channel) and 12 (`status --short`); the user's 30-minute session logged |
| **P4** Tradespace, recovery, sequencing, Λ | old 3.1, 3.2; §1.3; W10, W11 (Λ₀) | `trade` with a worktree branch per candidate (D-23); the four rejection types with their return conditions; dominance a pure function; recovery tested on a neutral history in which a constraint change returns an infeasible candidate; sequencing as §1.3, with run-all and authored order selectable; a recorded decision to proceed with open claims; Λ₀ per milestone in `/ready`; `/pick` recording the preference (D-18); NoGenerator intact; σ and the backlog observables (M6.1's `test_trade.BacklogIsExposed`); invariant 13 |
| **P5** The site | old 4.1–4.4; W16 | readiness above the fold; layered canvas (parameters → derived → evaluators → claims); ego graph, scrubber, bullet bars; each `focus.json` rule with its test; change marks, test card, bring list, candidate small multiples; invariants 12 (site) and 14; the screens suite's goldens; A-9 answered in the taste batch; the user's second session |
| **P6** Experiments | old 5.3 (moved out of the repo); §1.2 | E1–E5 run on the private bench and written up for the paper; E0, E6, E7 designed, not run (they need cross-domain data or a controlled G sweep). A finding that forces a repo change arrives as a slip with its failing test first (R-12) |

**P2 as closed** (the commit after `ce48996`). Each item of the P2 row's done criterion,
with the test that holds it; *partial* and *outside the repo* say what is left and where.

| Item | State | Held by |
|---|---|---|
| P2.0: errored louder across every renderer, a planted renderer its violator | done | `test_louder.ErrorIsLouder` |
| P2.0: mutation sealed, a planted writer its violator | done | `test_mutation.MutationIsSealed` (and its planted runners) |
| B0's pass-beside-an-error test still green | done | `test_invariants.ErrorIsNotPass.test_a_pass_beside_an_errored_gate_does_not_pass` |
| §1.4 is the vocabulary, through one table from GLOSSARY | done for statuses, outcomes and every word P2 added; *partial* for "every human word": GLOSSARY §9's other rows (`why`'s headings, `doctor`'s "checks", `check`'s "gates … executed, cached") and §7's full scanner and ratchet wait for the rename pass (P2.1-D22, P2.3-D18) | `test_vocabulary.StatusWordsAreTheGlossarys`, `test_vocabulary.StatusWordsComeFromOneTable`, `test_vocabulary.StatusLinesSpeakTheTable` |
| errored and unqualified never Failing or Checked, beside a pass or not | done | `test_invariants.ErrorIsNotPass`, `test_status_table.UnqualifiedBesideAPassIsNeverChecked`, `test_louder.UnqualifiedNeverWearsAnOutcome` |
| Assumed only with an owner set through the signing channel | done | `test_owner.AnOwnerWrittenByHandNeverCounts`, `test_signing.AnOwnerOnlyThroughTheChannel` |
| `needs` with unknown (now Skipped's words) | done | `test_prerequisites.PrerequisiteFailureIsNeverAPass` |
| all 54 bundled gates qualified by paired controls | done (54 of 54 where trimesh, numpy and omc are present) | `test_packs.KnownGoodControlsPass`, `test_packs.NegativeControlsFire`, `test_packs.ControlsArePaired` |
| the bracket's 6 by both controls and a mutation pass | done | `test_bracket_cache.BracketIsQualified` |
| physical results article-bound, contradictions on the evaluator's record | done | `test_physical.AMovedArticleReadsStale`, `test_physical.AContradictionGoesOnTheEvaluatorsTrackRecord` |
| operating contexts | done | `test_context.OutsideTheContextAPassDoesNotCount`, `test_context.AFailOutsideStillCounts` |
| rebuild prediction | done | `test_physical.RebuildPredictionIsExact`, `test_fig4.TwoArticlesExactly` |
| `export <milestone>` re-executing at the money boundary (R-9) | done | `test_export.TheBoundaryReExecutes`, `test_export.ACacheThatLiesIsCaughtAtTheBoundary`, `test_fig4.ExportAtTheBoundarySeesPastTheCache` |
| Fig. 4, neutral names, its physical claim Pending build | done | `test_fig4.FalsificationGraphOfFigure4.test_the_base_case` |
| — a change it must not reach | done | `test_fig4.FalsificationGraphOfFigure4.test_the_base_case` (K6, K7) |
| — an unregistered read | done | `test_fig4.FalsificationGraphOfFigure4.test_an_unregistered_read_never_reads_checked` |
| — operating context removed | done | `test_fig4.FalsificationGraphOfFigure4.test_the_operating_context_removed` |
| — owner removed | done | `test_fig4.FalsificationGraphOfFigure4.test_the_owner_removed` |
| — a change inside the context | done | `test_fig4.FalsificationGraphOfFigure4.test_a_change_inside_the_context` |
| — a fail outside the context stays Failing | done | `test_fig4.FalsificationGraphOfFigure4.test_a_fail_outside_the_context_stays_failing` |
| — a physical pass on a moved article reads Stale, names it | done | `test_fig4.FalsificationGraphOfFigure4.test_a_physical_pass_on_a_moved_article` |
| R-8's oracle listing every moved status | done: one move, C6 (§1.4) | `test_status_table.StatusesMoveOnlyTowardUnresolved`; `tests/oracle/r8_statuses.py --toward-unresolved` |
| G4 unchanged or changed with its reason | done: changed in P2.1 with its reason (§1.4) | `test_shims.CheckMigratesOnce.test_the_first_check_fails_as_pinned` against `tests/expected_bracket.json`; `test_ci_config.CiRunsWhatTheDocsSay` |
| R-6: `test_renderers`' table and "an error beats a skip" to Skipped/errored | done (P2.1) | `test_renderers.RenderersAgree` |
| the E4Localisation rename | done (P2.1) | `test_staleness.InvalidationIsLocalised` |
| CLAUDE.md invariants 2, 4, 9, 10, 11, 12 | done, with 15; each one's classes named in CLAUDE.md's table | `test_meta.EveryInvariantHasItsTest`, `test_meta.TheInvariantTableIsTheMap` |
| §1.4, §1.5 and W3, W7, W8, W11–W13 | done for P2's halves (their Proof cells); W7's, W11's and W12's commands are P3's, Λ₀ P4's | the rows above |
| a private-bench run and its scorecard diff | *outside the repo*: not run by this commit; the phase is not done by §3's rule until it is | — |

**Not this round:** derived independence (old 5.2; D-32 stands as a rule — independence
is never a scalar); the rename; sharing (W17); confidence across projects beyond the
per-evaluator record; review records (C9); a CAD kernel in the spine (never).

**Scored by the private bench.** From B1 on, every phase ends with a bench run, keyed by
the nopekit commit under test. Its suites are claims about nopekit, each with its own
known-bad control: **incident** (a past expensive discovery surfaced before the spend),
**replay** (invalidation and sequencing over real history), **adoption** (an existing
project yields an honest ledger), **experience** (the walkthrough's shapes), **simulated
user** (a persona plays the human; a low-independence evaluator, never the deciding one),
**screens** (the site against a rubric). The agents that run nopekit there are
sandboxed with no shared priors (top of this file). **A phase is done only when its
scorecard moves the numbers of the suites it targets and moves none backwards.** The
numbers stay in the bench; none is committed here (D-33's second half stands).

---

## 4. What in PLAN.md is now obsolete

### 4.1 Rows and sections

| Id | Status | Replaced by |
|---|---|---|
| Title; Conventions' "the case study (paper §14)" | superseded | v0.14 title; "(paper §8)" |
| §1 thesis table (R(x), D(R), B_R, π*, (I,L,C,S,D), E1–E8 rows); "three new things" #2's terminal framing; "Λ = G/B_R is never computed" | superseded | §1.1–§1.3 |
| §2.1 bullets "No refusal scheduler before there is verdict history", "E1–E8 become a `bench/` suite that runs on this repo", "Phases 0–5 as listed" | superseded | §1.3, §3 |
| D-14, D-16 (the words, not the one-table mechanism) | amended | GLOSSARY; A-11 below |
| D-12 | amended | `--authority` is new and names an expert-judgment claim's person or institution; `who` stays and is derived. PLAN.md §8 says the skill references neither flag, but `skills/nopekit/SKILL.md` tells agents to pass `--who`, so removing it is ask A-14 |
| D-24 | amended | adds `pack extract` (W15) |
| D-31 | obsolete | §1.3, P4 |
| D-33 | amended (second half stands) | no in-repo `bench/`; the private bench runs sandboxed agents, and its numbers are never committed here |
| M0.2 | obsolete | §1.2; no `PROTOCOL.md` here |
| M1.2, M3.I, M3.L, M3.C, M3.S, M3.D, M3.r, M15.1 | re-anchored | §1.1 descriptor names; M3.D survives as `needs` only |
| M2.2a, M18.1 | re-worded | "checked", never "verified" |
| M4.3, M15.2 | obsolete (declined) | P4 |
| M6.1 | amended | Λ₀ per milestone (§1.3) replaces "never computed"; the observables (distinct candidates per window, candidates reaching a terminal disposition) and `test_trade.BacklogIsExposed` move to P4 with σ, which E6 needs |
| M18.9 | re-anchored | v0.14 §10 still states the scaling hypothesis as unproven; the limit stays visible: Λ₀ is printed with its inputs and which of them is declared (§1.3), and nothing states the hypothesis as shown |
| M7.1, M8.1, M16.1 | re-numbered | E6, E7, E0 |
| M9.1 | obsolete | §1.5, groundspace row |
| M11.10 | renamed | recovery, P4 |
| M14.1, M14.2 | re-worded | Gap and Pending build |
| M14.3 | unanchored | the anecdote left the paper; it stays as METHOD rule 4's provenance |
| M20.3 | obsolete | the private bench |
| every "Paper says" § anchor | re-pointed | §1.1 map |
| §4.0.1 invariant 15 | split — the mutation half landed (P2.0; CLAUDE.md's 15 from P2.3) | the mutation half lands in P2 as `MutationIsSealed`, in P2.0: mutation writes nothing in the real tree or a pack directory (invariant 5's P5 row moves with it), and an inconclusive mutation is never reported as a fail; "bench writes nothing" leaves with the bench |
| §4.3–§4.5 summaries, §4.6 hand-offs, phase-5.md 5.2–5.3 | re-keyed | §3 (hand-offs follow the old checkpoints into their new phases). One hand-off is dropped, not re-keyed: "P3 → P5: `vcs.snapshot`; `modelio.influence`" fed tree-level mutation, and P2's mutation runs in process on the known-good control and needs neither |
| G8 (the skill evals) | amended | the evals run an agent on nopekit, so they run like every other such agent: through the private bench's sandbox, from the release bundle (the strip list at the top of this file), never as `claude -p --plugin-dir <repo>` on the host, where the whole repo, `~/.claude` and its memory are visible. `evals/` cases and graders live in the repo and are stripped from the bundle. Absent the sandbox, G8 reports SKIPPED, never green |
| §5.1 vocabulary bullet | superseded | Table 1 words via GLOSSARY |
| §6 (E1–E8) | obsolete | §1.2 |
| §9 "No refusal scheduler"; "no Λ on screen"; "No LLM calls or network in `bench/`" | obsolete | §1.3; the bench left the repo |
| §10 baseline at `1e09113` | obsolete | `0474f5c` and B1's baseline scorecard |
| `tests/test_staleness.E4Localisation` | rename — done in P2.1 | §1.2; `test_staleness.InvalidationIsLocalised` |

### 4.2 Ask-points

The user answered the bench plan's asks, not these. Read against those answers: paper
words, one term each, and METHOD's PROVEN/BLOCKED wording **goes to the check-in as a
proposed diff**; tier-2 asks are **batched into the check-in**, never sent per phase; the
untracked draft stays untracked. The walkthrough's own command lines answer two more.

| Ask | Now | Why |
|---|---|---|
| A-1 METHOD rule 5, known-good half | **open**, re-worded | "known-good control", "qualified", and the mutation pass; proposed diff in the check-in batch; P2 enforces in code meanwhile |
| A-2 METHOD rule 9, what "skipped" covers | **open**, re-worded | Table 1 groups errored with skipped ("did not produce a usable verdict"); same batch |
| A-3 METHOD section on the tradespace | **open**, after P4 | words "recover", "preferred against"; asked once P4 exists, so the doctrine describes something real |
| A-4 METHOD rule 3, "the ledger" | **likely obsolete** | v0.14's ledger L *is* the records plus the verdict cache (§6.4, §7). If GLOSSARY defines "ledger" as L, METHOD's sentences are true again and A-4 is withdrawn; otherwise it leads the batch, since P1 made them false |
| A-5 remove `extract` | **obsolete — kept** | the walkthrough ingests and extracts, held by `inputs --unextracted` (W6) |
| A-6 remove `decide` | **open**, folded into P4 | GLOSSARY splits milestone from decision; preferences go through `/pick`; the shim stands until then |
| A-7 remove `packs add` | **obsolete — kept** | the walkthrough adds packs with it (step 4) |
| A-8 removals, for visibility | **closed** | landed in P1.3; claims are files, as the walkthrough has them |
| A-9, A-9.1–A-9.7 focus rules | **open**, P5 | asked in P5's taste batch, not rule by rule |
| A-10 the untracked draft | **answered** | stays untracked and unopened; phase gates run in clean worktrees |
| A-11 "What is PROVEN" → "What is verified" | **obsolete as worded** | v0.14 reserves "verified" for V&V; the replacement is GLOSSARY's word for Checked, in the same METHOD diff as PROVEN/BLOCKED |
| A-12 layout epoch | unchanged | P5, conditional |
| A-13 METHOD's incident anecdotes (rules 3 and 4) | **new**, check-in batch | shipped doctrine that recounts a past incident is a prior every sandboxed agent shares; proposed as a neutral rewording beside the vocabulary diff. Until answered, B1 scores the affected scenarios as contaminated (top of this file) |
| A-14 `claim physical --who` (D-12) | **new**, P2 | the skill tells agents to pass `--who`; it changes in the same change that removes the flag |
| A-15 who owns the bracket's C6 (P2.1) | **new**, check-in batch | from P2.1 an assumption reads Gap until its owner records it through the signing channel, which lands later in P2, so the bracket's C6 blocks `check` and G4's pinned signature moves (C6 joins `claims.critical` as `unclaimed`, exit 1 at 7.0 and at 8.0). The ask: who owns C6, and will they record it once the channel exists — then C6 reads Assumed and G4 moves back in words only, as §1.4 expects — or does C6 stay a Gap as the example's demonstration of the rule, and G4 keeps it? Until answered, G4 holds C6 as a Gap |

### 4.3 Paper feedback (the user's call; the repo matches whatever the paper says)

| # | Item |
|---|---|
| F1 | "six packs … 46 evaluators" vs the repo's 7 and 54 (C1): say seven and add Modelica simulation, or give the reason openmodelica is excluded |
| F2 | Table 1 has no status for "not yet run", which §6.1 relies on ("unrun claims remain open") |
| F3 | E4 needs at least one physical result recorded against a claim an automated evaluator had Checked; the paper should say which article supplies it |
| F4 | §1 states the human-authority boundary in the present tense; P2 records it and P3 enforces it |
| F5 | §8's "reviews": no review record exists (C9) |
