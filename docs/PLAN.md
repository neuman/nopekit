# PLAN — bringing atompipe up to "Grounded Refusal Scaling"

> **Amended by [`PLAN-v0.14.md`](PLAN-v0.14.md)** for paper v0.14 and the accepted walkthrough,
> with the phases re-sequenced (B0, B1, P2–P6). **Where the two disagree, the amendment wins;**
> a row it does not name stands as written here.

This is the Phase 0 output. It is a hand-written source, not a generated file, and it
is a plan, not a record of work done. Every `file:line` refers to commit `1e09113` on
branch `grounded-refusal`; the phase that moves a line updates the reference in the
same commit. It is read by the agents implementing phases 1–5 and by the user.

Two orders are baked in, because each one exists to stop a failure this repo has
already paid for:

- **Traceability first.** Anyone reviewing the preprint can start from a paper section
  in §3, follow it to a phase in §4, and find the test that fails if the repo stops
  doing what the paper says. A gap-map row changes to closed **in the commit that
  closes it**, never later: a plan that trails the code is one more document that
  disagrees with the tree (the committed `docs/readiness.md` already does, S-41).
- **Invariants first.** Every phase adds a new place where a PASS is minted, cached,
  indexed, rendered, compared or exported, and each is a new way to launder an
  assumption into apparent proof. So each change arrives with the test that tries to
  make it launder; the tests guarding a code path are strengthened *before* that path
  is refactored; and no checkpoint inside a phase leaves an invariant red. Where a
  slice of the phase-0 read proposed something that would have turned the bundled
  suite red, this plan says so and re-sequences it (§4.0.4).

**Tiers.** Like a pack, this plan discloses progressively: this file is the map (tier 2);
each phase's checkpoint detail is in `docs/plan/phase-N.md` and the slipped-through register
in `docs/plan/slipped.md` (tier 3), loaded only for the phase at hand. The split was made
after review, when the single file reached 3,171 lines — more than an implementing agent
should hold beside the code it is changing. Section numbers are unchanged.

**Conventions.** Row ids: `M§.n` gap-map rows, `D-nn` decisions, `R-n` sequencing
rules, `S-nn` slips, `Qp.n` open questions for phase *p*'s judge panel, `A-n`
ask-points, `Pp.k` checkpoints. `V:` marks a test that tries to violate a property;
`C:` a characterization test that pins today's honest behaviour before a refactor.
The paper's case-study device is never named in any repo file (`tests/test_packs.py:27`
FORBIDDEN, enforced by `NoLeakedProvenance`); it is always "the case study (paper
§14)". The user's untracked `docs/BENCHMARK_ENGINE.md` is never opened, edited,
committed, quoted, or scanned by any new test (A-10).

## Contents

| § | What | Read it when |
|---|---|---|
| 1 | Thesis: the paper as CI for physical claims; the three new things; what this plan does not claim | first |
| 2 | Decisions: the brief's (settled) and this plan's (D-rows, each with its reason) | before arguing with a phase |
| 3 | **The gap map**: paper § → repo today (file:line) → change → phase → proof | auditing fidelity |
| 4 | Phases: the safety case (invariants, sequencing rules, what "green" means), then 1–5 as summaries — checkpoints and done criteria — each pointing at its tier-3 file `docs/plan/phase-N.md` (target transcript, shapes, tests, panel questions); hand-offs | implementing: read this file, then only the phase you are building |
| 5 | Human-experience map: each brief bullet → phase → deliverable → test | building the agent channel or the site |
| 6 | E1–E8: built now, deferred, and why | the bench, and paper §17 |
| 7 | Slipped-through register → `docs/plan/slipped.md`: defects found during the read, with evidence | fixing, and writing commit messages |
| 8 | **Ask-points**: what needs the user's yes before it lands | before touching METHOD.md, removing a command, or shipping a focus rule |
| 9 | Non-goals | when tempted |
| 10 | Baseline state at `1e09113`, including today's human-facing output | before running anything |

---

## 1. Thesis

The paper's architecture is **continuous integration pointed at physical claims**.
Almost every mechanism it proposes already exists as a proven software practice (the
paper's own §5 makes this argument about software's refusal topology). The repo
borrows the shape of each practice and none of its dependencies:

| Paper | CI practice whose shape we borrow | atompipe, after this plan |
|---|---|---|
| R(x) → {accept, reject, unknown} (§2.1) | test outcome pass / fail / skipped / error; the JUnit schema | `Verdict.outcome`, the one definition; `unknown` encoded as a skip with `blocked_by`; `check --junit`, so skipped ≠ pass at the schema level (P1, P2) |
| admission of a validator (§2.1, fn 4) | mutation testing: a test earns credit only by killing a seeded fault | registry refusal (exists), plus a control demonstrated at the gate's *current version* before its verdict counts (P1), plus the known-good half (P1 packs, P2 all), plus boundary mutants of the model (P5) |
| refusal graph, D(R) (§3, §4) | job prerequisites (`needs:`), make targets | `GateSpec.needs`, a stable topological plan, cycles and tier inversions refused at registration, `unknown: prerequisite failed` (P2) |
| v = (pass, ρ), staleness (§11, fn 16) | a content-addressed build cache with verifying traces | per-gate ρ over traced reads, gate code, opened files and claim reads; affected-only `check`; E4 as a unit test (P1) |
| executable tradespace T, frontier (§9–§10) | branches, worktrees, pull requests | a candidate is a commit on `cand/*`; one generated disposition record per candidate; `objectives.json`; dominance as a pure function (P3) |
| resurrection (§11) | re-run the cache-missed jobs when the base moves | `trade` re-evaluates dormant branches whose return condition changed, and says why (P3) |
| L and C of a refusal source (§3, §15) | job timings | `duration_s` and `cpu_s` measured, never declared (P1 keeps them, P2 surfaces them) |
| terminal refuser, readiness (§14; METHOD rules 4, 9) | a release gate and a manual approval step | `Claim.terminal`, a signed `PhysicalResult`, `export` refusing at the money boundary, `REPORT.md` (P2) |
| taste (§12) | code-owner approval: who approved, and why | a preference decision with who / why / reconsider-when, captured only from a human channel (P3) |
| independence (§3) | none — this is the new measurement | a bracket derived from origin plus mutation score, never a field (P5) |
| E1–E8 (§17) | a benchmark suite | `bench/` for E1, E3, E4 (P5); the other five get their data recorded (P1–P3) |

**The three genuinely new things**, which the repo owns outright and must not dilute:

1. **Admission by negative control.** It exists (`gates.py:545-572`), but only as a
   *declaration*. Nothing couples the demonstration to use (`cli.py:901-1072` never
   reads a selftest); the result is not durable (`cli.py:1755-1760` writes it only to
   the run history this plan deletes); and the paper's known-good half is missing
   (`gates.py:1418-1582` tests rejection only). To protect it: P1 persists it and makes
   it count, P2 adds the second half, P5 adds seeded faults in the model.
2. **Human and physical terminal refusers as a first-class verdict state** (§14, §18).
   CI ends at a machine. A claim here ends at a calculation, a solver, a datasheet, a
   measurement, a named person, or at "no refusal source yet". Today there is
   `ClaimKind.PHYSICAL` and an unsigned `PhysicalResult` (`models.py:343-352`), no
   `terminal`, no state for a named authority's judgment, and no `export`. P2 builds it.
3. **Preferences recorded with who, why and reconsider-when, and never recomputed**
   (§12). CI has no taste. Today nothing records one (`models.py:670-693`: `Decision`
   has no who and no reconsider-when). P3 builds it, and the identity and time are
   stamped at the CLI edge from the channel, never typed as flags.

Everything else is plumbing that CI already has.

**What this plan does not claim.** The architecture enforces **non-vacuity only** (§2.2,
level 1). Validity and adequacy stay evidence-bearing assumptions: the P2 cross-check
and the P5 boundary mutants each catch one mechanical disagreement, and neither
establishes that a gate evaluates its predicate correctly or that the predicate
supports the claim. Every human channel says "verified by a calculation" (or a
simulation, a datasheet), never "validated" or "proven" (P3 vocabulary). The bench
measures the admission rule's sensitivity and specificity; it does not measure how
often a generative model writes vacuous validators. "Signed" means tamper-evident and
channel-attested, not cryptographically identified. Λ = G/B_R is never computed (§6
and §18 say it is not validated and may not be a scalar).

---

## 2. Decisions

### 2.1 Settled by the brief (not re-opened here)

Every phase treats these as binding.

- The architecture is CI for physical claims: borrow the shape, never the dependency.
  The spine stays standard-library only; the site stays free of any build step; all six
  invariants in CLAUDE.md stay green with their tests.
- The three new things above are owned outright; admission by negative control already
  exists and is to be *protected*.
- accept/reject/unknown maps to pass/fail/skipped/error, and `check` emits JUnit XML so
  skipped ≠ pass at the schema level.
- The refusal graph is a `needs` field on GateSpec with topological run order; a failed
  prerequisite marks downstream gates `unknown: prerequisite failed`; a `needs` cycle is
  refused at registration, like a build system.
- ρ becomes per-gate content-addressed verdicts built from what `_ParamReads` records,
  plus the gate source, plus consumed input artifacts. Staleness becomes per-gate and
  exact, `check` becomes affected-only, and E4 becomes a unit test.
- The tradespace is git: a candidate is a commit, the frontier is the set of open
  branches, a worktree per candidate, and one small disposition record per candidate
  `{infeasible | dominated | preferred_against | unevaluated, why, by, evidence,
  reconsider_when}`. Dominance is a pure function over an objectives file. Resurrection
  re-runs cache-missed gates on dormant branches when `claims/` changes.
- Latency and cost are measured, never declared. Independence is **never** a field the
  proposer fills in: it is derived from origin plus mutation score.
- Negative controls are extended by model auto-mutation, run in a temp worktree so the
  SEALED-fixture rule holds.
- `terminal` per claim ∈ {closed_form, solver, datasheet, measurement, human, none}.
  `export` refuses while any critical claim's terminal is none, or is measurement/human
  without a signed PhysicalResult.
- E1–E8 become a `bench/` suite that runs on this repo.
- Records become files. `ledger.json` becomes a **generated** index of `claims/`,
  `params/`, `decisions/` and `inputs/` — an output, never hand-edited — kept so an agent
  still reads the whole project in one read, and a test proves the index never disagrees
  with the records.
- Stop building: the append-only run history; global `model_hash`/`inputs_hash`
  staleness; CLI subcommands whose only job is to mutate records. Keep check, selftest,
  report, why, doctor, ask, and a new `trade`. No Bazel or Nx. No refusal scheduler
  before there is verdict history. No candidate generator in the spine.
- The human experience: facts from a tool, each with a source and an age; the human's
  own judgment is the only thing on screen that looks like a decision; no generated
  prose stands where a fact should be; a test runner and `git status` are the register.
- Phases 0–5 as listed, one workflow each, a commit and a five-line summary after each.
- **Ask first** before changing METHOD.md, adding any third-party import under `src/`,
  removing a CLI command a pack or the skill still references, or adopting a focus rule
  this plan is less than sure about (§8).

### 2.2 Decided by this plan (each with its reason)

The phase-0 read was split into nine slices of the paper against the repo, and three
competing drafts of this plan. They disagreed in places. Each disagreement is resolved
once here so no phase panel re-litigates it; a panel may overturn a D-row only by
writing down the evidence that breaks it.

| # | Decision | Why, and what was rejected | Ph |
|---|---|---|---|
| D-01 | **Claims compose three-valued (Kleene), in P2 — not P1.** One pass plus a skipped or unknown covering verdict is BLOCKED; one pass plus a never-run gate is PENDING. | `models.py:62` defines PASS as "every covering gate ran and passed" and `claims.py:195` says "all ran and passed", but `claims.py:231-248` resolves pass+skip to PASS (S-03). §13: "a skipped gate remains skipped". It lands in P2 behind the blocking-only-grows oracle (R-8), because P1 moves where every fact lives and must be measured to change no status. *Rejected:* Kleene in P1 next to the migration, where an exit-code change could not be told apart from a migration bug. | 2 |
| D-02 | **No new ClaimStatus for unknown.** A prerequisite-unknown verdict is `skipped=True, passed=False` plus `Verdict.blocked_by`; the claim is BLOCKED and its reason names the root prerequisite. | Every reader (`Verdict.ok`, rung 2, `_skip_digest`, JUnit `<skipped>`, the site) already refuses a skip as a pass, so invariants 1, 2, 4 hold with no new code path, and an older spine that drops `blocked_by` still reads not-pass (R-2). *Rejected:* `ClaimStatus.UNKNOWN`, which touches `summarise`, STATUS_TAG, site counts and every test table. | 2 |
| D-03 | **Any prerequisite that is not `ok` makes its dependents unknown**: fail, error, skip, unknown, not admitted, not registered, or neither run nor fresh. The message says which: the brief's `unknown: prerequisite failed: <root>` only when the root failed; otherwise `unknown: prerequisite not established: <root> (<its outcome>)`, and for a missing tool the `next:` line says `install <tool> for <root>`. | §4: D(R) is "what must already be *established*". *Rejected:* failed-only, which runs a print simulation on a part whose watertightness is unknown and wastes the same money as running it on one that failed; the brief's words for every outcome, which calls a root missing a tool "failed" and tells the human to fix a check with nothing wrong. | 2 |
| D-04 | **`needs` is a scheduling and resolution edge, not a ρ edge.** A dependent's cached measurement stays valid while its prerequisite is down; its *effective* verdict is unknown; synthesized unknowns are never cached. | The measurement is a function of its own inputs. *Rejected:* the prerequisite's ok-bit inside ρ, which re-runs a dependent whose inputs never moved every time a guard recovers. | 1 (decided), 2 |
| D-05 | **The verdict cache is tracked and content-addressed**: `.atompipe/verdicts/<gate>/<rho16>-<out8>.json`, written once (`O_EXCL`), deterministic bytes. `out8` digests the **outcome tuple** (passed, measured, limit, units); a `digest` field inside covers the whole entry for integrity. `when` and `duration_s` live in untracked `.atompipe/obs/`. | "git + the verdict cache is the history" (brief). Identical adds merge silently; two *outcomes* for one ρ become two files (an error, never a silent pick); a detail-only difference keeps the first entry and warns. *Rejected:* timestamps in tracked entries (add/add conflicts); an untracked cache (a fresh clone shows nothing); one file per gate (the conflict generator); `out8` over the whole block (text-only nondeterminism would masquerade as two outcomes). *Widths:* 16 hex of ρ (64 bits) keeps a collision among one gate's entries below 1e-9 up to 10⁵ entries; 8 hex of the outcome only has to separate outcomes of one ρ; `sha12` is a display id at git's short-hash scale, never a key. *Rejected:* full 64-hex filenames (unreadable paths against Windows' 260-character limit). | 1 |
| D-06 | **`ledger.json` is gitignored and a pure function of the record files**: no model import, no registry, no clock, no verdicts or statuses. It keeps store.py's one-read benefit: every command, and from P3.4 the Stop and PostToolUse(Edit\|Write) hooks, rebuild it when a record moved, so the agent's own edit is in it when it next reads; what it cannot hold — statuses and the model's parameter view — sits in one named sibling, `.atompipe/cache/last_check.json`, which the skill points the agent at beside it. | A tracked generated file that changes whenever any record changes conflicts on every merge of two candidate branches (the brief's inverted argument), and a tracked output invites a committed hand-edit that becomes a second source (METHOD rule 1). | 1 |
| D-07 | **Selftest results become control entries in the cache, keyed by ρ_control.** `gate selftest` always runs; `check` runs a gate's control on a cache miss within the same tier ceiling; a gate not admitted at its current version gets `error="not admitted: …"` and its function is never called. | Dropping `runs/` would otherwise delete the only record that controls fire (`cli.py:1755-1760`). Demonstration that does not govern use is optional, and the logger-with-a-declared-control probe shows optional demonstration counts for nothing (S-05). Mirrors `run_all`'s lost-control branch (`gates.py:1229-1245`). | 1 |
| D-08 | **A stale FAIL stays FAIL**, rendered `(stale: <reason>)`. | `METHOD.md:175` defines stale as "it passed, but inputs have changed since"; keeping that true means METHOD.md is not edited. Both statuses block. | 1 |
| D-09 | **Legacy verdicts are dropped at migration**; git keeps them. | A verdict with no ρ can never be shown current, and importing one creates a verdict class special-cased forever, in the generous direction. | 1 |
| D-10 | **Goalposts live in `claims/`, never in the model or the gate.** Gates read limits through `ctx.acceptance(claim)`, a traced input; `bracket.deflection` reads C1's limit and `DEFLECTION_LIMIT_MM` is deleted. Where a gate still computes its own limit, P2 cross-checks it. | §13: a candidate must not move its own goalposts; `model/` is auto-accepted and candidate-owned while `claims/` is ask-first and trunk-owned. It also makes E5 observable on the bracket: relaxing C1 now changes ρ (S-35). *Rejected:* `"limit": {"from": "<projection key>"}` in claims, which hands the proposer the goalposts. | 2 |
| D-11 | **Physical and human results live in `results/<claim-id>.json`**, not inside the claim. Written only by the signing channel; denied to agent edits by the permission hook; **not** trunk-owned in `trade`. | The claim is a trunk-owned requirement; the result is evidence about one built object with a different owner, lifetime and permission. A result binds to the object it measured through `built_from`, so a trunk result can never verify a candidate, and a candidate's own refutation stays with that candidate. *Rejected:* results inside `claims/<id>.json`, which the trade overlay would erase from a candidate — contradicting R-3. | 1 (layout), 2 |
| D-12 | **`claim physical` stays and becomes the signing channel.** `/tested` maps to it. `who` comes from the git identity (`git -c user.useConfigOnly=true var GIT_AUTHOR_IDENT`, timestamp dropped); `--who` and `--when` are removed; `--authority` names the institution when the terminal is human; the channel is derived. No new `tested` command. | The brief stops record-mutator growth; this command does what a file edit cannot do honestly (clock, hashes, ρ binding, channel). `report.py:617-619` already prints it. *Rejected:* a typed `--who`, which the proposer can fill (it defaults to `''` today, `cli.py:3375`); a new `tested` command, a second name for one channel. | 2 |
| D-13 | **"Signed" = sealed + attributed + bound + evidenced + consistent, with the channel derived.** It does not claim identity. | The stdlib has no asymmetric signing; HMAC with a local key is either forgeable by the same-user agent or verifiable only by the signer. The honest version is the one whose every property can be checked and whose limits are written in the docstring. | 2 |
| D-14 | **`REPORT.md` at the project root replaces `docs/readiness.md`, as a gitignored output like `ledger.json`;** the export package carries a copy. The heading's text is `report.SECTION_PROVEN`, which tests reference; whether REPORT.md's heading keeps the word is ask-point A-11. | The committed readiness.md has already drifted from its ledger (S-41). METHOD rule 9 and invariant 4 use the word PROVEN, and renaming it silently empties `_proven_section` (S-15) — so a rename changes the constant's text, never its name, and only on a yes. *Rejected:* a committed REPORT.md with a re-render test — one more file every candidate branch mutates (D-06's argument), so every merge of two candidates conflicts on it. | 2 |
| D-15 | **`/ready` is `atompipe export --dry-run`.** One code path, not a second predicate. | Two predicates for one verdict need an agreement test and still drift. *Rejected:* `report --ready`. | 2, 3 |
| D-16 | **One vocabulary table, `report.HUMAN`, created in P2.** First consumers: REPORT.md and `export`; then the P3 short outputs; then P4 `state.json`. No human channel prints a status word from anywhere else. | There are three vocabularies today (S-69). If the table waited for P3, P2 would ship ad hoc words in the report. *Rejected:* the table in `format.js` — a status word is truth and the site must not own truth. | 2 |
| D-17 | **`Verdict.comparator` and one `claims.margin(verdict)` land in P2**, used by the cross-check, the bullet bar and `status --short`'s closest-margin fact. The near-limit *predicate* built on it (`next_action` rule 10, focus rule 3) is not decided here: it ships only as A-9.3 answers. The margin is per verdict; when its side disagrees with `passed`, it is null and the verdict wins. | The claim's comparator is wrong for multi-gate claims (C4 is `<=` while `min_wall` passes on `t >= m`). Rounded `measured` can equal the limit on a FAIL (S-18). One function means the `next:` line and the page cannot disagree. | 2 |
| D-18 | **A preference is recorded only from a human channel**: the PostToolUse(AskUserQuestion) capture when the questions shown match atompipe's own payload (normalised, harness-added fields stripped) and the model did not pre-answer them (checked at PreToolUse, before the harness writes the human's `answers`), or `trade pick` on a TTY. From an agent session `trade pick` refuses and names `/pick`. `why` and `reconsider_when` are required; an empty answer records nothing. | §12 requires "who supplied it"; §13 says the proposer must not author the human's judgment; the harness, not the model, is the witness. *Rejected:* recording an empty `why` to be asked later, which weakens the settled {who, why, reconsider_when} record. | 3 |
| D-19 | **Slash commands are user-invoked skills** (`disable-model-invocation: true`); tool text reaches the human through the UserPromptExpansion hook's `systemMessage`; permission rules are a PreToolUse hook. | The Claude Code docs call plugin `commands/` the older format; a plugin `settings.json` cannot ship permission rules; the model cannot paraphrase a `systemMessage`. Verified live before P3 closes (P3.0). | 3 |
| D-20 | **`status --short` and the Stop hook never import `gates/`, and run no project code in-process.** They read `.atompipe/cache/last_check.json` (written by `check` in P1) plus a byte fingerprint of the watched sources. **The fingerprint only decides whether to print; it never grades a verdict.** | `cli.py:815-816` imports gates and builds the model on every call, and the hook fires every turn. What moved is localised against the per-entry read digests `last_check.json` stores: a record or data file by its digest; a model edit by building the projection in a subprocess with a 3 s timeout (model only, and only when a model file moved; 3 s sits inside the hook's 5 s), falling back on timeout or error to "model changed — k checks read it", those k PASSes out of "verified". Only a PASS whose own recorded reads moved stops reading "verified", so the short form and `status` agree. *Rejected:* one fingerprint deciding every PASS is out of date — the global staleness the brief stops building, where recording a `/pick` or ingesting an unread file downgraded every check (S-33). | 1 (file), 3 |
| D-21 | **`site/data/`, `site/assets/`, `site/vendor/` are gitignored; CI publishes.** | Same argument as D-06: every candidate branch would mutate them. Reverses `site.py:175-184`'s stated choice, so SITE_CONTRACT changes in the same commit. | 4 |
| D-22 | **The spine lays out the canvas** as sticky discrete `(column, slot)` pairs **derived from tracked first-appearance order**, with no stored layout: a node's slot is its rank by the commit that first added its record file (or, for a check, its `.atompipe/verdicts/<gate-id>/` directory, tracked since P1), ties broken by id; uncommitted nodes append by id; ids that have left the tree are tombstones whose slots are never reused. The page maps slots to pixels. | "Positions never move between builds" needs memory, and git is the only memory CI, a fresh clone and every branch share. Layout is not truth, so this does not breach the site rule. *Rejected:* d3-sankey relaxation (moves nodes); a stored `layout.json` — ignored, CI and fresh clones re-lay out and forget tombstones; tracked, every candidate branch mutates it (D-06) and a hand edit becomes a second source of positions. | 4 |
| D-23 | **One tree per process, in a temp *worktree*.** Candidate evaluation, mutation and resurrection run each tree in a fresh subprocess with the running spine on `PYTHONPATH`. One helper, `vcs.snapshot`, does `git worktree add --detach HEAD` under the git common dir and overlays the working-tree delta (tracked modifications and untracked, non-ignored files) so the snapshot is the state `check` just saw. A filtered copy is used only outside git. | In-process, the second tree reuses the first tree's `bracket` module (S-63); the global registry refuses duplicate ids (`gates.py:575-590`). *Rejected:* `git stash create`, which omits untracked files (a new `gates/*.py` would be missing); a filtered copy as the default, which drifts from the brief's "temp worktree". | 3, 5 |
| D-24 | **New top-level commands: `trade`, `export`, `start`. No others.** | `start` is the one-call backing for `/start`; `export` is the brief's refusal boundary. No `tested`, `migrate`, `pack new` or `pack export` — the phantom references to the last two are struck (S-10). | 2, 3 |
| D-25 | **The known-good half: packs in P1, every gate in P2.** P1 copies `test_packs`' baseline and control assertions into the spine as `packs.demonstrate`, because `gate selftest` at the repo root is a done criterion; the invariant tests keep their own copy as an independent oracle, held to `demonstrate` by an agreement test. P2 adds `NegativeControl.good` and runs both halves in `gates.selftest`. | Splitting costs nothing and makes P1's done criterion runnable. *Rejected:* the invariant tests delegating to `demonstrate` — a test that calls the code it guards is relaxed by relaxing that code, with no test file touched (R-6). | 1, 2 |
| D-26 | **Channel parity = the known-good and known-bad contexts carry the same `extra` key set**, not "extra must be empty". Ships with good fixtures for cad-solid and sourcing in the same checkpoint. | cad-solid (`packs/cad-solid/selftest/bad_meshes.py:63-74`) and sourcing (`packs/sourcing/selftest/bad_boms.py:76-78`) deliver their known-bad input through `ctx.extra`, and the gates read `extra` first (`packs/cad-solid/cad_solid_parts.py:76-90`; `packs/sourcing/bomlib.py:233-240`). *Rejected:* refusing any `extra` fixture, which would refuse nearly every control in two bundled packs and turn P2 red. | 2 |
| D-27 | **Project fixtures derive from the known-good design, not the live one** — in P1.2, with the control entries. | `bad_configs._with` rebuilds from the host's `ctx.params["config"]` (`examples/bracket/selftest/bad_configs.py:25-31`), so an identity fixture `return ctx` is reported "correctly failed … ~64x worse" because the live bracket already fails (S-07). A fixture built from a passing design and changed one way is diagnostic whatever the live design's state. It lands in P1, not P2, because that read is a whole-value dependency on the host config: every Config edit would change all six ρ_control, re-run all six controls and write six tracked files. | 1 |
| D-28 | **`modelica.result_claim` declares `GateSpec.refuser="solver"`**; it gets no `needs` edge to `modelica.simulates`. | It is `Tier.INSTANT` with no tools (`packs/openmodelica/gates/modelica.py:720`) and judges a solver's result file; `simulates` is `Tier.SOLVE` (`:1261`). An edge would be a tier inversion, which P2.2 refuses. P1's ρ already covers the result file's bytes. | 2 |
| D-29 | **openmodelica's wall-clock text leaves `Verdict.detail` in P1**, not P5. | `modelica.py:1252` and `:1386` put `{run.duration_s:.1f}s` in `detail`. Under a content-addressed cache that is non-deterministic bytes for one ρ: add/add merge conflicts across branches, and a false nondeterminism flag on every `check --force` (which CI runs, R-9). `duration_s` already carries the time. | 1 |
| D-30 | **The bracket's rejected alternatives go in a `PARAMS` list in `model/bracket.py`**; `params/*.json` holds only provenance the model cannot hold (source, grounded_by, tags). | `bracket.py:9` promises "see PARAMS" and none exists (S-42); `modelio` already reads `PARAMS` (`modelio.py:645-693`). The model is where the number lives and is candidate-owned, so a candidate records its own losers in its own commit. *Rejected:* copying the docstring prose into `params/*.json` — two homes for one fact (rule 2). The Config docstrings keep *why this value* and point to PARAMS for *what lost*. | 1 |
| D-31 | **E2 is not built.** P2 records what it would need: the DAG, measured L and C, the `pruned` set per sweep. | No scheduler before history (brief). Today every gate runs regardless of results (`gates.py:1225-1249`), so order has zero effect on spend; `needs`-pruning is the first mechanism by which structure changes spend. | 2 |
| D-32 | **Independence is never a scalar.** A Wilson interval per mutation class, under a named operator set. Project code — including project-local packs — has origin `agent-session`, and nothing upgrades it; so does any pack whose content digest does not match a shipped release, wherever it sits (a user or `ATOMPIPE_PACK_PATH` pack, or a copy shadowing a bundled name); git trailers are provenance text only. | §3: "bracketed under a stated failure distribution"; §18: independence is not guaranteed; the proposer writes the commits, so a trailer-based label is proposer-filled; the agent session can also write `~/.atompipe/packs` or set `ATOMPIPE_PACK_PATH`, which outranks bundled packs (`packs.py:178-236`), so an origin read from location is one the proposer chooses. | 5 |
| D-33 | **The bench makes no LLM or network call, and its numbers are never committed.** | A committed number goes stale silently and becomes a second source of truth. `bench/corpus/e1/` accepts offline-generated validators in a fixed shape. | 5 |

---

## 3. The gap map

One row per claim the paper makes that the repo can honour, contradicts, or knowingly
declines. **Ph** is the phase (and checkpoint, where it matters) that closes the row;
**—** means declined or deferred, and the Change cell says why. **Proof** names the test
that fails if the repo regresses; class names are proposals a phase panel may rename but
never drop. A row is closed only when its proof exists and is green, and it is marked
closed in the same commit, by writing `closed` before the phase in its **Ph** cell
(`closed 1.2`); renaming a proof updates its row in the same commit. `tests/test_contracts.py`
(R-14) holds this mechanically: every backticked test name in a closed row's Proof cell
resolves to a module, class or method under `tests/` — `V:` a planted closed row naming a
missing test turns it red.

### Abstract, §1: the object and the population of refusers

| # | Paper says | Repo today | Change | Ph | Proof |
|---|---|---|---|---|---|
| M0.1 | The system returns an *executable tradespace* — alternatives, claims, refusers, evidence, dependencies, assumptions, why alternatives disappeared — supporting incremental invalidation and resurrection. | One tracked ledger, no candidates (`models.py:811-866`; `store.py:9-11`). Staleness is one global bool (`cli.py:389-414`). | Records as files and per-gate ρ (P1); `trade` with dispositions and resurrection (P3). | closed 1, 3 | `test_records.IndexNeverDisagreesWithRecords`; `test_staleness.InvalidationIsLocalised`; `test_trade.E5Mechanism` |
| M0.2 | "We release the implementation and preregistered experimental program"; the end matter lists "experiment protocols". | No `bench/`, no protocol file (`git ls-files`). | `bench/PROTOCOL.md` preregisters **all eight** experiments (question, estimand, population, fields recorded, analysis, falsifier, and why it is not runnable yet); `tests/test_bench.py` asserts the runnable ones' expectations, each with a hook that flips it. **3 runnable (E1, E3, E4), 5 preregistered and not runnable**; `bench e2\|e5\|e6\|e7\|e8` prints SKIPPED naming the missing input. | 5 | `test_bench` exits 1 on a failed expectation; every E has a PROTOCOL entry |
| M1.1 | R is a *population* of refusers, including an expert and an institutional authority. | Seven packs, 54 gates, and `ClaimKind.PHYSICAL` (`models.py:41-56`). A named human or institution has no state. | `terminal: human`, with a named `authority` on the signed result. | 2 | `test_terminal.SignedMeansSomething.test_human_needs_authority` |
| M1.2 | Refusers differ in (I, L, C, S, D). | Only `tier` (declared) and `claims` exist on `GateSpec` (`models.py:459-487`). | See M3.*. | 2, 5 | see M3.* |

### §2.1–§2.2: refusal source, admission, refusal is not truth

| # | Paper says | Repo today | Change | Ph | Proof |
|---|---|---|---|---|---|
| M2.1a | R(x) → {accept, reject, unknown}; the third state is essential. | Three flags (`models.py:385-437`), re-derived separately by `ok`/`render` (`:412-427`) and `resolve_status` (`claims.py:225-244`). No JUnit anywhere (grep). | `Verdict.outcome` is the single derivation. `check --junit` and `gate selftest --junit`: a childless testcase iff the outcome is pass. | closed 1.0, closed 1.1 | `test_junit`; `RenderersAgree.table` |
| M2.1b | "Failure to reject is not proof." | A non-bool `passed` is a pass: `{"passed":"false"}` renders `[ok]` (`gates.py:950`, `:895`; S-01). A non-numeric `measured` is filed as a measurement (`gates.py:1013-1016`; S-02). | `passed` must be a real bool (or a 0-d bool-kind array); anything else is an error naming the type. A non-number measured or limit is an error. | closed 1.0 | `test_invariants.PassMustBeABool` |
| M2.1c | A validator is admitted only after it rejects a registered negative control. | Admission by *declaration*: `Registry.register` refuses a missing control (`gates.py:545-572`) and never runs one. `check` never reads a selftest (`cli.py:901-1072`). A logger with a declared control yields PROVEN rows (S-05). | Control entries keyed by ρ_control (D-07). `check` runs the control on a miss; a gate not admitted is an error and its function is never called. | closed 1.2 | `test_admission.AdmissionIsDemonstrated` |
| M2.1d | "…while accepting a corresponding known-good input." | Absent from the spine. For packs it lives only in `tests/test_packs.py:145-169`, which waves skips through (`:164-165`; S-12). Project gates have none. An always-False gate passes selftest (S-04). An identity fixture "fires" on the failing bracket (S-07). | Packs: `packs.demonstrate` (P1.1). Project fixtures derive from `selftest/known_good.py` (P1.2, D-27). All gates: `NegativeControl.good`, both halves in `gates.selftest` (P2.3, D-25); a third-party project gate with no known-good is admitted *reject-only* in every phase, loudly, and its FAIL refutes nothing (Q5.5, P3.1). The inside mutant supplies it automatically (P5). | closed 1.1, closed 1.2, 2.3, 5 | 1.1: `test_packs.DemonstrateAgrees`, `test_pack_mode.PackModeSelftest.test_a_planted_always_false_fails_its_own_baseline`; 1.2: `test_admission.AdmissionIsDemonstrated.test_a_literal_identity_fixture_with_a_known_good_host_is_not_admitted`; 2.3: `test_admission.AdmissionHasTwoHalves` |
| M2.1e | "A gate must demonstrate that it can refuse" — a registry invariant. | Outside a project `gate selftest` exits 2 (`cli.py:1728`), although `CLAUDE.md:99` prescribes it there. `pack validate` is static and certifies a planted `return True` as publishable (`packs.py:1139-1157`; S-09). SEALED is enforced only by a unit test (`tests/test_packs.py:217-271`). | Pack mode for `gate selftest`. `pack validate` demonstrates. SEALED at runtime: a pack fixture that reads host `ctx.params` is detected (P1.2, `doctor`), then refused once the detector has shown zero hits on the bundled corpus (R-4). | closed 1.1, closed 1.2 | `test_pack_mode.PackModeSelftest.test_a_planted_logger_exits_1_naming_the_gate` (planted logger → exit 1); `test_pack_mode.PackModeSelftest.test_the_repo_root_exits_0_and_persists_nothing` (repo-root selftest → exit 0); `test_packs.DemonstrateAgrees`; `test_packs.ControlsAreSealed.test_planted_unsealed_fixture_is_caught`; `test_doctor.DoctorNamesWhatRhoCannotSee.test_sealed_fixtures` |
| M2.1f | fn 4: this is mutation testing's admission criterion. | No mutation anywhere (grep). | Boundary mutants of the model at the claim's limit; the covering gate must flip or it is reported a logger for that claim. | 5 | `test_mutation` |
| M2.1g | fn 18: specification gaming — an optimiser satisfies a check without its intent. | The control channel can be told apart: dict fixtures arrive in `ctx.extra` (`gates.py:1510-1513`), which `check` always leaves empty (`cli.py:697`; S-06). | Channel parity (D-26): the good and bad contexts must carry the same `extra` key set; cad-solid and sourcing ship good fixtures on the same channel in the same checkpoint. A gate that fails iff `extra` is non-empty is then not admitted. | 2.3 | `test_admission.test_extra_sniffer_not_admitted`; `C:` cad-solid and sourcing stay admitted |
| M2.2a | Three levels; the architecture enforces non-vacuity only; validity and adequacy stay assumptions. | "PROVEN" is said to the human for a machine pass (`site_template/lib/format.js:24`; `README.md:48`; S-75). | §1 states the limit. Human channels say "verified by a calculation / simulation / datasheet"; "validated" and "proven" never appear in a human channel. The one exemption is REPORT.md's `SECTION_PROVEN` heading, pinned by exact string, until A-11 answers (D-14). | 3, 4 | `test_vocabulary` |
| M2.2b | Do not launder "the model wrote a test" into "the system verified the claim". | S-05. Nothing compares a PASS's own measurement with the claim's acceptance (`claims.py:241-248`). | Admission counts (P1.2). A PASS whose own measurement violates the claim — where `settles` matches the quantity and units match — is not PASS (P2.4). This catches one mechanical disagreement; it does not establish validity (§1). | closed 1.2, 2.4 | 1.2: `test_admission.AdmissionIsDemonstrated.test_an_always_true_gate_never_yields_pass`; 2.4: `AcceptanceCrossCheck` |

### §3: refusal has a topology, r = (I, L, C, S, D)

| # | Paper says | Repo today | Change | Ph | Proof |
|---|---|---|---|---|---|
| M3.I | I(G,R) ∈ [0,1], measured or bracketed for a source/protocol pair under a stated failure distribution; never intrinsic; a physical test is not I = 1 by being physical. | No field anywhere (grep). `PackManifest.origin` is required author prose "the next reader calibrates their trust on" (`packs.py:977-982`; S-85). The openmodelica mirror calls itself "an independent implementation" when nobody said so (`modelica.py:956`; S-86). | A derived `Independence` record: origin class plus per-class Wilson interval under `operator_set` (D-32). No `GateSpec`, `PackManifest`, `Verdict` or `Claim` field can carry it; a hand-edited record containing `independence` is refused loudly. | 2 (no-field test), 5 | `test_independence`; `LatencyIsMeasuredNeverDeclared` |
| M3.L | L(R), the time to obtain the refusal. | `duration_s` is measured wall-clock (`gates.py:1031-1121`). Multi-sample history exists only in `runs/` (`store.py:398-493`), which P1 deletes. | P1 keeps samples in untracked `obs/`; a cache hit never overwrites a measured duration. P2 reports median and max over `n` in `gate show`/`gate list --json`, "not measured yet" at n = 0, never falling back to the tier. A measurement or human terminal's L is `when − card_ready_when` on its signed result (P2.5), else "not measured". | closed 1.2, 2.2, 2.5 | `test_staleness.CostIsKept`; `test_terminal.PhysicalLatencyIsMeasured` |
| M3.C | C(R), the resource cost. | `Tier` is *declared* (`models.py:81-92`) and compared only with other declared values (`packs.py:1182-1197`; S-56). | `cpu_s` from `os.times()`, children included, so omc counts. `doctor` warns when the measured median breaks the declared tier. The C column is measured `cpu_s` with `n`, or "not measured yet" — never the tier, which sits in its own *declared budget* column. A measurement or human terminal's C is an optional cost the human records at `/tested` (stamped by the channel), else "not measured". | closed 1.2, 2.2, 2.5 | 1.2: `test_gate_context.CostIsMeasured` (`cpu_s`, children included); 2.2: `LatencyIsMeasuredNeverDeclared`; at n = 0 `gate list --json` shows C "not measured yet" |
| M3.S | S(R), which claims or regions the refusal discriminates. | `GateSpec.claims` holds ids or tags (`claims.py:114-134`) — author-written, so a tag overlap says what a gate is *filed under*, not what it can discriminate: `bracket.model_validity` is tagged onto C1 and C2 and discriminates neither. | S is a **set**, never a count, computed by `gates.coverage` (P2.4) and shown in `gate list --json`: a claim is labelled *demonstrated* only where the gate's paired control fails with a measurement that violates **that claim's own acceptance** (quantity and units matching, the cross-check's rule) and, from P5, its outside mutant at that claim's limit is killed; otherwise *declared only* (tag overlap). A guard shows the region it discriminates, derived from its `settles`, comparator and limit (L/h ≥ 5), not the claims it is filed under. | 2.4, 5 | `DescriptorsAreDerived.test_s_never_counts_an_undiscriminated_claim` |
| M3.D | D(R), what must already be established. | No prerequisite field (`models.py:459-487`). A validity guard reaches a claim only by tag overlap, so a narrowly tagged claim passes while its guard says the model is invalid (S-51). | `GateSpec.needs` (M4.*), including edges from guarded gates to their guard. | 2.2 | `RefusalGraph`; `test_packs.GuardsReachNarrowClaims` |
| M3.r | The descriptor "makes verification itself optimizable". | Nothing surfaces it (`cli.py:1622-1703`). | Descriptor fields in `gate list --json`: L, C, S, D (P2) and I (P5). No optimiser (D-31). | 2, 5 | `test_bench.DescriptorsAreDerived` |

### §4: the refusal graph

| # | Paper says | Repo today | Change | Ph | Proof |
|---|---|---|---|---|---|
| M4.1 | R = (V, E) with prerequisite edges (solid validity → bed fit → overhang → print sim → fabrication → measurement). | Run order is registration order, stated in prose (`gates.py:480-486`); 4 of 7 bundled packs (beam-analytic, fdm-print, fluids-analytic, thermal-analytic) break it — guards register after what they guard, fdm runs tier 1 before tier 0 (S-52). | `needs` plus a stable Kahn plan (registration index breaks ties, so order is unchanged with no `needs`). Cycles refused at registration with the whole cycle named; tier inversions refused; published packs may need only their own gates. `needs` covers the chain's digital prefix; its physical tail (fabrication → measurement) is enforced by `check`'s test-article predicate (`claims.blocking()` empty, `claims.py:490-496`): nothing asks for a real part while a check still blocks (P3.3). | 2.2, 3.3 | `NeedsCycleRefused`, `TopologicalOrder`, `TierInversionRefused`; `AskWaitsForTheChecks` |
| M4.2 | "No reason to print a part that is not watertight." | `run_all` runs every gate whatever earlier gates returned (`gates.py:1225-1249`). Three incompatible in-body conventions for "prerequisite not established" (S-53). | A prerequisite that is not ok means the dependent's `fn` is **never called**; its verdict is `unknown: prerequisite failed: <root>`, transitively. `check --json` lists `pruned`. | 2.2 | `PrerequisiteFailureIsNeverAPass` |
| M4.3 | π* = argmax E[elimination]/cost subject to E. | Nothing. | **Declined for now** (brief: no scheduler before history). The data it needs is recorded (D-31). | — | — |

### §5–§8: why software differs; Λ; the bottleneck hypothesis; the cycle-time race

| # | Paper says | Repo today | Change | Ph | Proof |
|---|---|---|---|---|---|
| M5.1 | Software's refusal infrastructure is cheap, fast, continuously executable and *incrementally invalidated*; CI is the model. | Global staleness. No JUnit. CI swallows the reference project's exit code and mutates tracked files (`.github/workflows/ci.yml:74-75, 78`; S-83). | Per-gate ρ, affected-only `check`, JUnit, and CI that asserts the exact expected failure set in a temp copy. | closed 1 | the CI step; `test_ci_config` |
| M6.1 | Λ = G/B_R, "not yet a validated universal metric". | Nothing. | **Never computed or displayed as a scalar.** P3 exposes the observables (distinct candidates per window, candidates reaching a terminal disposition), with times derived from the git history of `trade/`, for an offline E6. | 3 (data) | `test_trade.BacklogIsExposed` |
| M7.1 | G_r ≪ / ≈ / ≫ B_R; diminishing returns unless B_R grows. | Nothing. | E6 deferred (§6). | — | — |
| M8.1 | L, C ↓ ⇒ B_R ↑ ⇒ generation is worth more. | Nothing. | L and C measured (M3.L, M3.C). E7 deferred: it needs real downstream latency interventions. | 2 (data) | — |

### §9–§10: ship the space; four ways an alternative disappears

| # | Paper says | Repo today | Change | Ph | Proof |
|---|---|---|---|---|---|
| M9.1 | Persist T = (P, V, φ, C, u, L, G). | None of it as a unit. | P = model Config fields plus `params/`. V = each candidate commit's projection. φ = trunk's critical claims and their covering gates. C = `claims/`. u = `objectives.json`. L = the index, the verdict cache, `results/` and `trade/`. G = the agent, recorded only as derived `origin`, never as spine code. | closed 1, 3 | 1: `test_records.IndexNeverDisagreesWithRecords` (C and L's index), `test_param_view.ParamValueHasOneHome` (P), `test_bracket_cache.BracketIsMigrated` (the bracket persisted as records and a committed verdict cache); 3: `test_trade.GitTradespace.test_bracket_space_classifies` |
| M9.2 | A final point hides rejected alternatives and their reasons. | `Param.rejected` exists, but in the bracket all 12 are `[]`, and the model promises a `PARAMS` that does not exist (`examples/bracket/model/bracket.py:9`; S-42). `sync_params` drops a later `PARAMS` rejection (`modelio.py:846-856`; S-38). | `modelio.param_view` unions rejections from the model and the record. The bracket gains `PARAMS` (D-30). `why <param>` lists tradespace losses (P3). | closed 1.3, 3 | `test_param_view.ParamValueHasOneHome`; `why thickness` contains `REJECTED (1)` |
| M9.3 | Preferences that cannot be regenerated from engineering state. | Nothing. | M12.*. | 3 | — |
| M10.1 | Infeasible, dominated, preferred-against, unevaluated — each with its own return condition. | Nothing. `resolve_status` folds errors into FAIL, so no API separates "refuted" from "crashed" (`claims.py:241-244`; S-16). | A strict `Disposition` StrEnum (an unknown value raises). One kind per candidate: infeasible > unevaluated > dominated > preferred_against > open. `claims.refuted_by` works at claim level: verdicts that ran and failed from a gate admitted paired, a P2.4 cross-check FAIL on a paired-admitted verdict, plus REFUTED measurement or human results; a crash, a skip, a reject-only FAIL or (from P5) a LOGGER pair means unevaluated, never infeasible. | 3.1 | `TradeErrorIsNotRefusal`; `TradeSkipIsNotPass`; `TradeRefutedIsInfeasible`; `RenderersAgree.trade` |
| M10.2 | "A conventional design file collapses these distinctions." | `Decision` has no kind (`models.py:670-693`). | `Decision.kind` ∈ {change, preference}. Dispositions are generated; preferences are sources. | 3 | `Records.test_unknown_disposition_is_refused` |

### §11: staleness and resurrection

| # | Paper says | Repo today | Change | Ph | Proof |
|---|---|---|---|---|---|
| M11.1 | v = (pass, ρ). | `Verdict` has no ρ (`models.py:385-437`). | `Verdict.rho` (last field). Content-addressed entries (D-05). | closed 1.2 | `test_staleness`; `test_cache` |
| M11.2 | ρ hashes the parameter values consumed. | `_ParamReads` records bare names in one flat set; a nested read records `config` and `load_n`; bulk reads record nothing (`cli.py:494-574`, `:523-525`; S-25). | `verdicts.ParamTrace`: leaf paths with value digests; a missing key records `ABSENT`; any bulk access is a whole-value dependency; mutators raise (S-24). | closed 1.2 | `test_bulk_reads_are_whole_value_dependencies` |
| M11.3 | …and intermediates. | Derived keys read directly are hashed as values; derived→input edges are not recorded (`cli.py:596-603`), so the lever of the one failing claim is invisible (S-81). | Value hashing covers intermediates for ρ. Derived→input attribution comes from `modelio.influence` by perturbation, for `next:`, `why` and the canvas. | closed 1.2, 3.3 | 1.2: `test_staleness.InvalidationIsLocalised.test_the_early_cutoff` (intermediates hashed as values); 3.3: `test_graph.BracketLevers` |
| M11.4 | …external files. | `inputs_hash` hashes the digests *stored* at ingest (`artifacts.py:645-660`); a file a gate opens is in no key (S-22). | Digests computed from bytes behind a stat cache keyed on (size, mtime_ns, ctime_ns, ino, dev) with a racy-clean rule; an audit hook records files and listings opened during the gate run; subprocess internals are listed as `opaque`, and an entry with an opaque channel is never served Fresh. | closed 1.2, closed 1.3 | `test_a_file_the_gate_opened_is_part_of_rho`; `StaleIsNotCurrent` (subprocess read); 1.3: `test_records.IndexNeverDisagreesWithRecords.test_an_input_rewritten_in_place_drifts` |
| M11.5 | …gate version. | In no key. Stale `__pycache__` runs old gate code after a same-size, same-second edit (`cli.py:271-281`; `packs.py:683-693`; S-26). 1e09113 changed verdict semantics without a version bump (S-29). | Code digest **per gate** — its defining module plus its helper closure — hashed from the bytes a fresh-compiling loader actually compiled. A spine digest replaces the version string (Q1.2). **Declined:** the versions of the instruments a gate runs under (trimesh, numpy, omc). They are recorded per entry as `instruments`, never in ρ (Q1.3); a Fresh entry recorded under other versions carries a note in `freshness`, `status` and `doctor`, and stays Fresh. | closed 1.2 | `test_same_size_same_second_gate_edit_runs_new_code`; `InstrumentMismatchIsNoted` |
| M11.6 | …other registered inputs. | openmodelica reads claim acceptance from `ctx.ledger` untraced (`modelica.py:181-187, 256`; S-23). `ctx.extra` is a shared cross-gate channel (`fdm_print_fold.py:276-331`; S-27). `ctx.params` is writable (S-24). | A traced ledger view (claims become inputs); `ctx.acceptance(claim)` (P2); per-gate `extra` plus a `ctx.load_file` memo that records the file for every caller; read-only params. | closed 1.2, 2.4 | `test_reading_a_claim_makes_it_an_input` |
| M11.7 | "Change a parameter and determine exactly which knowledge has become stale." | One bool: changing `bed_xy` stales C2, C3 and C4 while only `bed_fit` reads it. | `verdicts.freshness()`, computed without running a gate; `claims.*` take `stale_gates`. "Exactly" means exactly relative to the declared ρ, which excludes instrument versions (M11.5). | closed 1.2 | **the E4 table** (P1.2): `test_staleness.InvalidationIsLocalised` |
| M11.8 | Invalidate the *reachable* verdicts in the dependency graph. | No graph. | Dependents of an unestablished prerequisite become unknown at resolution, without a re-run (D-04). | 2.2 | `test_prerequisite_fail_does_not_invalidate_downstream_cache` |
| M11.9 | Rerun the affected gates. | `check` runs every selected gate. | Affected-only `check`; `--force` proves from scratch; CI and `export` always re-execute (R-9). | closed 1.2 | E4: `executed` == expected-stale set, `test_staleness.InvalidationIsLocalised.test_each_config_field_stales_exactly_its_gates` |
| M11.10 | Reconsider dormant alternatives whose return conditions are now met. | Nothing. | The resurrection pass, run by `trade` and first by `trade compare` (so `/pick`); `ask --next` never refreshes and points at `/pick` when a stored record is out of date (P3.1). Emits `returned: [{candidate, was, because}]`; CI runs it when trunk's requirements change. | 3.1 | `test_trade.E5Mechanism` |
| M11.11 | fn 16: *Build systems à la carte*. | Nothing. | Verifying traces: group a gate's entries by read signature, recompute ρ from current digests, look it up. Cited as the shape only. | closed 1.2 | `test_status_never_runs_a_gate` |

### §12–§13: taste; the proposer is untrusted

| # | Paper says | Repo today | Change | Ph | Proof |
|---|---|---|---|---|---|
| M12.1 | Record x ≻_H y with who supplied it, why, and when to reconsider. | `Decision` has no `by`/`reconsider_when` (`models.py:670-693`); `decide --when` backdates (`cli.py:3430`; S-44). | A preference decision `{kind: preference, prefers, over[], by, via, why, reconsider_when, commits{}}`; `by` from the git identity, `via` from the channel, neither a flag (D-18). Standing preferences are kept as a strict partial order: a cycle-closing pick is refused unless it names what it supersedes. | 3.2 | `PreferencesAreRecorded.test_by_is_not_a_flag`; `test_preferences_stay_a_partial_order` |
| M12.2 | Not derivable by rerunning feasibility. | Nothing. | `objectives.json` refuses `weight` (a weight is a preference). Picking a winner that does not survive is refused, and the unmodelled reason is routed to a claim. | 3.1, 3.2 | `Objectives.test_weight_is_refused_not_silently_dropped` |
| M13.1 | The proposer may propose parameters, constraints, claims, gates, explanations. | Everything is writable by the agent, including the CLI mutators. | PreToolUse permission hook: `model/**` allowed; `claims/ gates/ selftest/ decisions/ objectives.json inputs/*.json params/ .atompipe/project.json .atompipe/packs/` ask first; generated outputs (the export package included), `results/**`, `.atompipe/obs/**` and the verdict cache denied. Bash and PowerShell writes bypass it (a speed bump; R-9 and git are the guarantee). | 3.4 | `test_agent.Permission` table |
| M13.2 | …but cannot establish a claim by asserting it. | `claim physical` accepts an empty `--who`, a nonexistent evidence file and a backdated `--when` (`cli.py:1466-1519`, `:3374-3375`; S-48); a VERIFIED result survives any model change (S-50). | A signed result (D-13) whose channel is derived, bound to the claim digest and to ρ of the object tested, in `results/` (D-11). | 2.5 | `SignedMeansSomething`; `test_verified_result_expires_when_object_changes` |
| M13.3 | A gate the proposer writes must pass its own negative control first. | M2.1c. Project controls rebuild through the proposer's own model, so a model bug can defuse them (`bad_configs.py:18-31`; S-19). | M2.1c; project fixtures from known-good (D-27); P5 measures project-gate dependence on the proposer. | closed 1.2, 2.3, 5 | 1.2: `test_admission.AdmissionIsDemonstrated.test_a_model_edit_that_defuses_a_control_is_not_admitted`; 5: `test_bench.E3Quick` |
| M13.4 | A skipped gate remains skipped. | pass + skip → PASS, not blocking (`claims.py:231-248`; S-03). | D-01 Kleene. | 2.1 | `SkipIsNotPass.test_pass_and_skip_is_not_pass` |
| M13.5 | A failed gate remains failed. | A PHYSICAL claim whose modelled-half gate FAILS resolves UNVERIFIED, is not blocking, and the headline says it "clears every critical gate that is installed" (`claims.py:208-212`; `report.py:473-476`; S-49). | For PHYSICAL claims a covering verdict that ran and failed or errored gives FAIL before the result ladder is consulted. | 2.1 | `test_physical_claim_with_failing_modelled_half_fails` |
| M13.6 | Wrongness may not "disappear into prose". | The only display path is the agent paraphrasing CLI output; the `next:` line is a constant (`report.py:1114-1115`; S-66). | Tool text reaches the human as a `systemMessage` the model cannot edit; `next:` is a tested rule table. | 3.3, 3.4 | `test_agent` exactly one `cli.main` per expansion; `test_shapes` |
| M13.7 | fn 18: proof-carrying code — a small trusted checker. | One gate can write the next gate's inputs (`cli.py:674-698`; `gates.py:1080`). A candidate could ship its own `atompipe` package. | Read-only `ctx.params` (P1). A candidate is judged by trunk's claims and gates overlaid (`TRUNK_OWNED`) under the *running* spine (P3). | closed 1.2, 3.1 | `test_a_gate_cannot_write_another_gates_inputs`; `Goalposts.*`; `test_candidate_cannot_replace_the_spine` |
| M13.8 | The generator stays untrusted, so no generator in the spine. | None exists. | Keep it so: `trade new` accepts no parameter values. | 3.1 | `NoGenerator.test_trade_new_accepts_no_parameter_values` |

### §14: the case study, and terminal refusers

| # | Paper says | Repo today | Change | Ph | Proof |
|---|---|---|---|---|---|
| M14.1 | A report can say each claim "ends in" a closed-form calculation, a solver, a vendor datasheet, a physical measurement, a human judgment, or "has no refusal source yet". | No terminal (`models.py:354-383`). | `Claim.terminal` (declared intent, constrained by kind) plus derived `TerminalState`, derived from **admitted** covering gates only — a claim whose gates are all inadmissible ends in `none`, with the reason; `REPORT.md` has an "Ends in" column in the paper's phrasings verbatim, except terminal `none`, which every human channel says one way — "nothing can check this yet", the paper's "has no refusal source yet" in the brief's human vocabulary; `report.HUMAN` holds the mapping. | 2.5 | `test_terminal`; `test_report_shape`; `test_vocabulary` (one phrase per terminal across REPORT.md, export, `status --short`, `state.json`) |
| M14.2 | "Has no refusal source yet" is information, not a failure. | C7 (no gate) blocks, correctly. The critical assumption C6 never appears in the headline (`report.py:429-498`; S-59). | Readiness sentence 2 always names claims ending in `none`; `export` refuses on them. | 2.5 | `ReadinessShape` property test |
| M14.3 | A cable-routing validator returned success with no discriminatory power (METHOD rule 4's logger). | Declaration-only admission (M2.1c); the bracket's control sits 89.6× past its limit, so a 10×-too-loose limit is still admitted (S-17). | M2.1c/d/f; the bracket's control is recalibrated near its limit (P2.3). | closed 1.2, 2.3, 5 | 1.2: `test_admission.AdmissionIsDemonstrated`; 5: `test_bench.E1Quick` |
| M14.4 | A ribbon passed electrical checks and failed mechanical fit: a disagreement across representations (METHOD rule 6). | Nothing asks whether a claim is refused from every domain it spans: a design passing every electrical gate, with no mechanical-fit refuser at all, stays green. | **Not mechanically addressed** — coverage is finite (M18.5). What the repo shows: S as a labelled set (M3.S) makes a domain with no *demonstrated* refuser visible, and the canvas keeps `nocheck` nodes. The claim-limit vs gate-limit copy fixed at P2.4 (D-10; S-35) is METHOD rule 2, not this incident: it would not have caught it. | — | — (S is tested under M3.S) |
| M14.5 | A sourcing lens caught radio-module availability volatility no engineering calculation captured. | The sourcing pack is tier-0 arithmetic over a vendor document (`packs/sourcing/PACK.md:9-40`), so any terminal derived from `GateSpec` alone would call it closed-form. | `GateSpec.refuser = "datasheet"` on `bom.*`; the datasheet terminal is satisfied only with verified, extracted evidence; focus rule 8 targets terminals that drift without an input change (A-9). | 2.5, 4 | `test_terminal` (bom gates → datasheet) |
| M14.6 | 69 revisions, operated longitudinally. | `runs/` history, being deleted. | git plus the verdict cache is the history (P1); the scrubber's frames are derived from the last N commits, plus uncommitted local builds (P4). | closed 1, 4 | 1: `test_status_stale.RunHistoryIsGone`; 4: `test_site.History` |

### §15–§16: refusal economy; industries

| # | Paper says | Repo today | Change | Ph | Proof |
|---|---|---|---|---|---|
| M15.1 | A gate table with Independence, Latency, Cost, Coverage columns. | `gate list` shows tier order only. | Descriptor columns: L, C, S (the labelled set, M3.S), D in P2 and I in P5; C is measured `cpu_s` or "not measured yet", and the tier sits in its own *declared budget* column. | 2, 5 | `DescriptorsAreDerived` |
| M15.2 | A research programme on refusal scheduling. | Nothing. | **Declined for now** (D-31). | — | — |
| M16.1 | Domains differ in grounding bandwidth. | Seven packs, all within the paper's single Hardware row. | No change; the descriptor export feeds a later E8 table, which cannot test cross-domain scaling from this repo. | 5 (data) | — |

### §17: open empirical questions → §6 of this plan

### §18: limits — each must be *visible*, not just true

| # | Limit | Where the repo shows it | Ph | Proof |
|---|---|---|---|---|
| M18.1 | Refusal is not truth. | The vocabulary ("verified by a calculation", never "proven" or "validated"; REPORT.md's heading is the one pinned exemption until A-11); REPORT.md and `/ready` carry one fixed **limits line** from `report.HUMAN` saying what a green check does not mean — "A passing check has shown it can refuse; that does not show it is right, or enough." (METHOD rule 9, `METHOD.md:177-179`: "Say so, in the report, every time"). | 2.5, 3 | `test_vocabulary`; `test_shapes` (the limits line, verbatim, on both) |
| M18.2 | Independence is not guaranteed. | Per-class intervals printed "under F = <operators>"; `agent-session` is the default origin; no scalar, no threshold. | 5 | `test_independence` |
| M18.3 | Non-vacuity is not validity. | **An acknowledged limit, not a solved one.** §1 says the architecture enforces non-vacuity only. The P2 cross-check and P5 boundary mutation each narrow one mechanical case (a gate passing against a limit the claim does not state; a gate that stays green past the claim's limit); neither is shown to the human as validity, and the human sees the limit itself in the limits line (M18.1). | closed 1 (statement), 2, 2.5 (the line), 5 | `test_vocabulary`; `test_mutation` never prints "valid" |
| M18.4 | Validity is not adequacy. | **An acknowledged limit, not a solved one.** Nothing here shows that a predicate is enough to support its claim, and the limits line (M18.1) says so. What P2.4 and P5 add is non-vacuity bookkeeping, not adequacy: which covering gates are scored is decided from the graph, never from `settles` (the proposer writes it) — a covering gate is exempt only as a `needs`-prerequisite of another scored covering gate of the claim; a claim with no scored covering gate reads "no check flips at C's limit", is treated as a gap, and `export` refuses it; the report counts "not cross-checked" pairs by the same rule. | 2.5 (statement), 2.4, 5 | `test_shapes` (the limits line); `test_mutation` (`model_validity` exempt only as a prerequisite; a mismatched-`settles` gate that stays green is LOGGER) |
| M18.5 | Coverage is finite. | Readiness sentence 2 says "Nothing outside these N claims has been checked" when all are satisfied; the canvas keeps `nocheck` nodes visible; ρ's `opaque` channels are named by `doctor`. | closed 1, 2, 4 | 1: `test_doctor.DoctorNamesWhatRhoCannotSee.test_opaque_inputs`; 2: `ReadinessShape`; `test_graph` |
| M18.6 | Physical reality is slow. | The measurement terminal; `export` refuses without a signed result; `check`, not `export`, gates building a test article (`claims.py:490-496`). | 2 | `test_export` |
| M18.7 | Some refusals belong to institutions. | `terminal: human` with a named `authority`. | 2 | `SignedMeansSomething` |
| M18.8 | Taste remains external. | Preferences recorded, never computed; no weights. | 3 | `Objectives.test_weight_is_refused…` |
| M18.9 | The scaling hypothesis is unproven. | Λ, G_r and B_R never computed in-repo; `PROTOCOL.md` states what the bench does not show. | 5 | `test_bench` |

### §19–§20: discussion; conclusion

| # | Paper says | Repo today | Change | Ph | Proof |
|---|---|---|---|---|---|
| M19.1 | G ≫ B_R versus B_R ≫ G. | Nothing. | No repo change; see E6. | — | — |
| M20.1 | The four disappearances are not equivalent events, and a trustworthy system preserves the distinction. | Nothing. | M10.*. Human words: "fails a must-be-true", "beaten by X on <objectives>, no better on the rest", "set aside by <by>, who preferred X", "not yet checked". | 3 | `test_vocabulary` (internal names never printed; a tied objective is never named as beaten; the preferred-against phrase names its `by` and never says "you") |
| M20.2 | "What can say no to this design … and what else becomes invalid when it does?" | `why <param>` lists direct reads only (S-81). | `why` gains an impact set from `modelio.influence`; the ≤12-node ego graph answers "what else" for any node. | 3, 4 | `test_why_param_lists_indirect_gates`; `test_graph.Ego` |
| M20.3 | "We have built a place to measure it." | No protocol, no bench. | `bench/`. | 5 | `test_bench` |

---

## 4. Phases

### 4.0 The safety case

#### 4.0.1 Invariants: the six, and the ones this plan adds

Invariants 1–6 are CLAUDE.md's. Invariants 7–15 are proposed here; each is added to
CLAUDE.md in the phase that makes it mechanical, together with its violation test.
CLAUDE.md is not on the ask list; METHOD.md is, and none of these requires touching it.

| # | Property | Violation tests (class) | Phases that put it at risk, and how |
|---|---|---|---|
| 1 | A skipped gate is never a pass | `test_invariants.SkipIsNotPass` | P1: a cache that stored a skip would replay it. P2: `unknown` is a new not-pass state; Kleene turns pass+skip from PASS to BLOCKED. P3: a candidate with a skipped gate must never dominate. P4: colour must never paint a skip verified. |
| 2 | An errored gate is never a pass | `ErrorIsNotPass` | P1: JUnit `<error>`; two outcomes for one ρ must resolve as an error. P3: a crash must not make a candidate *infeasible* either — an error is not a refusal. |
| 3 | The registry refuses a gate with no negative control | `RegistryRefusesLoggers` | P1: `specs()`/`get()` hand out the stored spec today (S-13); reject-half enforcement at sweep time. P2: paired admission. |
| 4 | The report never puts an unrun or skipped gate under PROVEN | `ReportNeverOverclaims` | P1.0 fixes a latent vacuity in the test itself (S-15). P2: REPORT.md replaces readiness.md and the heading must not move silently. |
| 5 | Pack fixtures are SEALED | `ControlsAreSealed` | P1.1 copies the probe into `packs.demonstrate` and the test keeps its own (D-25); P1.2 adds a planted unsealed fixture. P2: channel parity (D-26) and `ctx.acceptance` (a host claim limit must not defuse a pack control). P5: mutation must never write into a pack directory. |
| 6 | Every pack ships a baseline its gates all pass | `NegativeControlsFire` | P1.0: a gate that self-skips on its own baseline becomes a failure; today the test silently `continue`s (S-12). |
| 7 | **A stale verdict is never served as current** | `StaleIsNotCurrent`; `tests/test_staleness.py` | P1 replaces global staleness with per-gate ρ; under-recording gives a false-fresh PASS. P3: `status --short` must never re-grade a verdict. |
| 8 | **The index never disagrees with the records, and no command writes a record it was not asked to write** — the one carve-out is the one-time legacy migration, which only `check`, `start` and the shims perform; every other command, and every hook, migrates in memory and writes nothing | `test_records.IndexNeverDisagreesWithRecords`; `NoCommandWritesARecord` (migrated and legacy fixtures) | P1.3 |
| 9 | **A verdict counts only from a gate admitted at its current version** — paired where a known-good exists; a gate admitted reject-only is reported in every phase, and its FAIL blocks but refutes nothing | `AdmissionIsDemonstrated` (P1, reject half); `AdmissionHasTwoHalves` (P2) | P1.2, P2.3, P3.1 |
| 10 | **A prerequisite that is not established is never a pass downstream** | `PrerequisiteFailureIsNeverAPass` | P2.2 |
| 11 | **A human or physical terminal is satisfied only by a result entered through a channel the proposer cannot author. A result can lose its power to verify, never its power to refute.** | `SignedMeansSomething`; `HumanChannelOnly` | P2.5, P3.4 |
| 12 | **No renderer is more generous than `claims.resolve_status`** | `RenderersAgree.*` (JUnit P1; readiness/export P2; `status --short`/trade P3; graph/focus/site P4) | every phase that adds a reader |
| 13 | **A preference is recorded, never recomputed, and only a human channel writes one** | `PreferencesAreRecorded` | P3 |
| 14 | **The site never writes and never computes truth** | `test_site_template` static scans, each with a planted violator | P4 |
| 15 | **Mutation writes nothing in the real tree but its own new `mutation-*` cache entries (and ignored obs and cache); bench writes nothing at all; an inconclusive mutant is never reported as survived** | `MutationIsSealed` (the diff is exactly those paths); the `test_bench` tree-hash check | P5 |

#### 4.0.2 Sequencing rules: how no invariant goes red mid-phase

Each rule names the failure it exists to stop.

**R-1 — Harden before refactoring.** Every phase opens with checkpoint `N.0`, which adds
`C:` characterization tests pinning the current honest behaviour of every guard the phase
will touch; they must be green on the *unchanged* code before the refactor starts.
*Stops:* a refactor that drops a guard silently because the only test of that guard was
never written (S-14, S-15).

**R-2 — Degrade-closed encoding.** Every new not-pass state is also written into the
legacy flags in the not-pass direction: `unknown` sets `skipped=True, passed=False`
alongside `blocked_by`. No new field may be the thing whose *absence* upgrades a status.
*Stops:* `Record.from_dict` silently drops unknown keys (`models.py:224-237`), so an older
spine, or any path that does not know the new field, would read the record as a pass.

**R-3 — Asymmetric expiry.** A result can lose its power to *verify* — it goes stale, is
unsigned, or was measured on a different design. It never loses its power to *refute*: a
REFUTED physical result persists across parameter nudges; a VERIFIED one expires when the
tested object changes. *Stops:* laundering a refutation by nudging `thickness` from 7.0
to 7.01. It is also why `results/` is not trunk-owned in `trade` (D-11).

**R-4 — Staged refusal.** Every new refusal lands in two steps inside one phase. First a
detector reports over the bundled corpus (54 pack gates, 6 bracket gates, and every pack
baseline with trimesh, numpy and omc present), and is asserted to have **zero hits on
honest bundled code and at least one hit on a planted violator**. Only then does it
refuse. *Stops:* a refusal that turns the bundled suite red mid-phase; §4.0.4 lists the
proposals that would have done exactly that.

**R-5 — One definition of outcome, one of status.** `Verdict.outcome` (P1.0) and
`claims.resolve_status` are the only producers. Every new consumer — JUnit, the readiness
object, export, `status --short`, trade classification, graph.json, focus.json, the page
— calls them and ships an agreement test proving it is never more generous
(invariant 12). *Stops:* the pattern this repo has paid for twice: `report.py` grew an
optimistic second copy of coverage (`claims.py:305-310` says so), and `status` still
cites a skipped gate as the reason a claim fails (S-68).

**R-6 — Never edit an invariant test to make a change pass.** A test may change only to
become stronger, and only where §4.6 lists it. The phase commit names every modified
test.

**R-7 — No `@skip` or `@expectedFailure` in any invariant class.** `tests/test_meta.py`
enforces it with an AST check, and checks that every CLAUDE.md invariant number maps to
an existing class with at least one test. An expected-failure test is a red test wearing
green.

**R-8 — Phase 1 moves where facts live, not what they are.** P1 carries a differential
oracle, run at phase review in a subprocess against the parent commit's spine: on the
fixture corpus (the bracket at thickness 7.0 and 8.0, each pack's baseline wrapped as a
project), a fresh full sweep gives **identical claim statuses** under both spines. The
only permitted differences are enumerated in advance, and none is expected on the bundled
corpus: gates that returned a non-bool pass value, and gates that fail admission. P2 uses
a weaker oracle: statuses may move only in the blocking direction, and the phase commit
lists every changed claim with its reason.

**R-9 — The money boundary re-executes.** A cache hit may speed up the inner loop; it is
never the evidence at a boundary that costs money. `export` re-runs every gate covering a
critical claim, bypassing the cache, **and each such gate's control, both halves**, and
refuses if any outcome or admission differs from the cached one; a mutation result a
critical claim relies on (P5) is re-run or refused as unreproduced. `check --force` does
the same, and CI runs it. This is what makes a *tracked* verdict cache safe: a
hand-placed entry — verdict, control or mutation — can forge only the inner loop. *Stops:* the cache becoming the
cheapest place in the repo to forge a pass.

**R-10 — New refusals bind new declarations first.** A refusal that could fire on existing
third-party projects applies to the new field only (`needs` cycles, tier inversions,
`terminal` combinations). Otherwise it is migration-tested against the bracket and every
bundled pack before it lands.

**R-11 — Every human-facing output is a fixed shape pinned by a shape test with its own
negative control.** `tests/test_shapes.py` asserts line count, line order and a regex per
surface, and runs each matcher against a mutated transcript (a line added, the age
removed), which must fail. *Stops:* a shape nothing pins drifting into prose, which is
where a fact turns into an opinion. *Rejected:* golden files, which pin words a panel
should be free to tune — except the bracket's two readiness sentences.

**R-12 — A `read` slip is reproduced before it is fixed.** Every row of docs/plan/slipped.md marked `read` is
turned into a failing test before its fix lands; the test comes first and fails.

**R-13 — Every new constant lands with its provenance**: why this value, and what was
tried and rejected (CLAUDE.md's house rule), in the code comment and, where this plan sets
it, here. A number this plan states without one is a default the phase panel must justify
before it lands. *Stops:* every fresh context window re-litigating a settled number — or
worse, not noticing it was never settled.

**R-14 — Contracts move with the code.** The checkpoint that adds a public surface
documents it in the same commit: a spine module, its public functions and every record
kind and field in SPINE_CONTRACT; every file `site build` writes under `site/data/`, every
top-level key of `state.json`, `graph.json` and `focus.json`, and every page pane focus can
name (4.2) in SITE_CONTRACT; every field of `GateSpec`, `NegativeControl` and `Verdict` and
every `GateContext` method (`acceptance`, `load_file`) in PACK_FORMAT and the
pack-authoring skill. `tests/test_contracts.py`, landed in P1.0, checks each list against
the code (AST walk, and `site init` plus `site build` on a bracket copy) and the §3 closed
rows against `tests/`; `V:` a
planted undocumented module, `state.json` key or `GateSpec` field turns it red. R-4
applies: P1.0 first adds the missing module-map headings (`models.py` and `site.py` have
none today). *Stops:* the documents an agent reads first describing a spine that no
longer exists — the drift behind S-10, S-11 and S-41.

#### 4.0.3 What "green" means for the brief's four commands

The brief says a phase is not done while any of its four commands is red. Taken literally
at `1e09113`, three cannot go green (§10). Each is pinned here so no phase declares
victory on a different reading.

| # | Command | Green means | Why the pin |
|---|---|---|---|
| G1 | `PYTHONPATH=src python3 -m unittest discover -s tests` | exit 0 in a clean worktree of the phase commit. In the main checkout the only permitted red is `NoLeakedProvenance` naming the user's untracked draft, reported as "1 red, the user's untracked draft, unchanged" and never "fixed" (A-10). | The draft exists only in the main checkout. **Implementation worktrees must live outside the repo tree** until P1.0 prunes nested checkouts: a linked worktree inside the repo carries a second `docs/ORIGINS.md` and turns the test red. |
| G2 | `atompipe pack validate <p>` for every `packs/*/pack.json` | exit 0 for all seven. From P1.1 validate also **demonstrates** (baseline passes, control fires, seal probe holds) at tier ≤ 1 by default; a skip for missing tooling is reported, not failed. The invariant tests keep their own assertions at tier 3 (D-25). | Today validate is static and certifies a planted logger (S-09). *Tier ≤ 1:* tiers 0 and 1 call no external solver (`models.py:89-90`), so validate stays seconds long; *rejected:* every tier, which makes publishing a pack wait on omc. |
| G3 | `atompipe gate selftest` | Before P1.1: inside a copy of `examples/bracket`, 6/6 fired. From P1.1: also at the repo root with no project, where it enters pack mode and exits 0 over every bundled pack. It exits 1 when zero controls ran (unless `--allow-empty`). From P2.3: the bracket's 6 are admitted *paired*. | `cli.py:1728` needs a project today although `CLAUDE.md:99` says to run it at the root (S-09). A command that passes by running nothing is a logger (`cli.py:1772-1774`). |
| G4 | `cd examples/bracket && atompipe check` | **Exit exactly 1 with the pinned failure signature** in `tests/expected_bracket.json`: failing gates `{bracket.deflection}`; errored, skipped or not-admitted gates `{}`; blocking claims `{C1 fail, C7 no check}`; C5 needs a real part; C6 assumed. Exit 0 is red; exit 2 is red. | The bracket fails on purpose (`examples/bracket/model/bracket.py:69-75`; the brief's P4 note). One fixture holds the signature: a phase that changes it on purpose changes that file in the same commit and says why. CI asserts the same signature from JUnit, so the phase gate and CI cannot disagree. |
| G5 | (from P1) after the phase commit, G1–G4 leave `git status --porcelain` empty | Until P1.3 lands, G4 runs in a temp copy, because at baseline `check` rewrites the tracked `ledger.json` and adds `runs/NNNN-*.json` (baseline fact 4). | A verification suite that dirties the tree trains everyone to ignore `git status`, the one register the brief names. |
| G6 | the phase's shape tests (R-11) and the fresh-clone transcript | `tests/test_fresh_clone.py` copies `git ls-files --cached --others --exclude-standard examples/bracket`, filtered to paths that exist, into a git-initialised temp dir (the shared test environment, P1.0), replays the transcript steps tagged with a checkpoint at or before the current one (each step names the checkpoint that delivers it), and matches every shape. At each `git status` step it asserts the porcelain equals the transcript's expected lines; a clean status is required after the non-editing prefix and after an explicit revert at the end. In the copy the bracket is the repository root, so expected porcelain lines drop the `examples/bracket/` prefix the in-repo transcript shows, and its ignores come only from the bracket's own `.gitignore` (P1.3) — `V:` after `check` and `gate selftest` no `__pycache__/` appears; `PYTHONDONTWRITEBYTECODE` is never set, because a user's shell does not set it. The clean-status assertion and `cached` are asserted only when the committed spine digest equals the running one — otherwise identical outcomes — until the cross-version digest test (P1.2) has run green on the whole CI matrix. | A transcript nobody replays is a promise. Transcripts edit the tree on purpose, so "clean afterwards" could never hold as first written. Plain `ls-files` misses a checkpoint's uncommitted new files and lists deleted ones (`runs/`), and a step replayed before its checkpoint lands fails for nothing. |
| G7 | (from P3) `claude plugin validate .` | no errors when `claude` is on PATH (the warning for the deliberately absent `version`, P3.4, is accepted); otherwise the test reports SKIPPED, and a skip never counts as a pass. | Nothing validates `plugin.json` today (S-73). |
| G8 | (from P3) the skill evals (3.4) | `evals/run.py` runs each case as `claude -p --plugin-dir <repo> --output-format stream-json --model <pinned id>` in a temp bracket copy; `evals/grade.py` (stdlib) grades the assistant text only, never the hook's `systemMessage`; each eval passes 10 of 10. When `claude` is absent or unauthenticated the runner reports SKIPPED, and the phase summary says "evals not run" — never green. | Model-sampled checks with no named harness, model pin or grader are a done criterion nobody can run the same way twice. |

`check --json` rows keep the keys `gate`, `passed`, `skipped`, `ok` through every
phase; `error`, like `detail`, `units`, `skip_reason` and `evidence`, is **absent when
empty** (`cli.py:704-726`), so readers use `.get`. New keys are additive (`outcome`,
`cached`, `rho`, `fresh`, `stale_reason`, `cpu_s`, `blocked_by`, `counts.executed`;
`duration_s` and `cpu_s` only on a row that executed in that command), and no
existing key changes meaning — `counts.ran` keeps counting passing verdicts
(`cli.py:997, 1015`). The one exception, from P1.2: the top-level `run`, which named
the run file the sweep appended, is now `null` — there is no run history — and is
kept so a reader that looks it up finds the key and nothing in it. The phase-gate
script parses the stable four. Every phase also keeps the
stdlib AST walk green (`.github/workflows/ci.yml:33`), names every S-id it closes in its
commit message in the modules' voice, marks its §3 rows closed, and gives the user a
five-line summary: what the human now sees, what slipped through and why, what is still
refused, the next phase's first visible step, any ask pending.

Each phase runs the brief's loop: understand → a judge panel of ≥ 3 approaches, scored,
working through that phase's Q-list (whose defaults ship unless the panel writes down the
evidence against them) → implement in worktrees → adversarial review with ≥ 3 refuters
per finding, aimed at the phase's refuter targets → fix → G1–G7, and G8 at phase close from P3.

#### 4.0.4 Proposals from the read that would have gone red, and how they are re-sequenced

1. **Channel parity as "a fixture that delivers input through `ctx.extra` is refused"**
   would have refused nearly every control in cad-solid and sourcing: cad-solid's
   `_sealed` returns `extra=dict(ctx.extra or {}, meshes=meshes)`
   (`packs/cad-solid/selftest/bad_meshes.py:63-74`) and `mesh_sources` reads `extra`
   first (`packs/cad-solid/cad_solid_parts.py:76-90`); sourcing hands the mutated BOM
   through `extra={"bom": doc}` (`packs/sourcing/selftest/bad_boms.py:76-78`;
   `bomlib.py:233-240`). Redefined as equal `extra` key sets (D-26) and landed with good
   fixtures on the same channel (P2.3, R-4). The hole it closes — a gate that fails iff
   `extra` is non-empty — then needs good and bad inputs that differ in channel, and
   admission refuses exactly that.
2. **Unsealed-fixture refusal by recorded reads** ("a pack fixture that read any
   `ctx.params` key is unsealed; `check` refuses it") is a new refusal on bundled code
   whose false-positive rate is unmeasured. It lands as a `doctor` warning in P1.2 and
   refuses only after zero hits across all 54 bundled fixtures and one hit on a planted
   violator (R-4).
3. **`modelica.result_claim → modelica.simulates` as a `needs` edge** (to make its
   terminal read "solver") is an INSTANT → SOLVE tier inversion that P2.2 itself refuses
   (`modelica.py:720` vs `:1261`); the openmodelica pack would be refused at load.
   Replaced by a declared `refuser="solver"` (D-28).
4. **"Two outcomes for one ρ is an error" while openmodelica writes wall-clock time into
   `detail`** (`modelica.py:1252`, `:1386`) would flag omc gates nondeterministic on every
   `check --force` and create add/add conflicts across branches. The detail fix moves into
   P1 (D-29); `out8` digests the outcome tuple, not the text (D-05); and "two outcomes"
   becomes an error only after two cold runs of every bundled baseline, omc included,
   produce byte-identical entries (R-4).
5. **An identity fixture fires on the bracket.** Re-probed: `return ctx` for
   `bracket.deflection` is reported "correctly failed … ~64x worse: 0.700 mm", because the
   live design already fails. Paired admission alone would not catch it on a project whose
   default design fails; D-27 (fixtures from known-good) does.

Each phase below lists: the **target transcript** (what the human sees when the phase is
done — shapes, not golden text; R-11 pins the shapes); **checkpoints** in order, each of
which must pass G1–G7; per checkpoint, **deliverables**, **record and file shapes**, and a
**failure register** (the failure each change could introduce and the test that tries to
cause it); **refuter targets** for the adversarial review; **done criteria**; and the
**open questions** for the phase's judge panel, each with a recommended default and what
was rejected. Checkpoints are verified in the implementation worktree; the branch gets
one commit per phase.

---

### 4.1 Phase 1: records as files, per-gate ρ, admission that counts, no run history, JUnit

**Why this order.** This phase has the largest blast radius: it changes where every fact
lives. So it opens by hardening every guard its refactor passes through (1.0), then makes
the done criteria mean something — JUnit, pack-mode selftest, honest CI — *before*
moving storage (1.1). Verdicts move (1.2) before records (1.3), so verdicts never lose
their home mid-phase: at 1.2 claims are still in the old ledger while verdicts are
already in the cache. Claim-status semantics stay fixed throughout (R-8), which keeps the
differential oracle exact; Kleene waits for P2 (D-01).

Detail — target transcript, record shapes, tests, panel questions: [`docs/plan/phase-1.md`](plan/phase-1.md).

- Checkpoint 1.0: harden the guards (no behaviour change for honest code)
- Checkpoint 1.1: make the done criteria mean something
- Checkpoint 1.2: per-gate content-addressed verdicts, admission's home, no run history, no global staleness
- Checkpoint 1.3: records as files, generated index, migration

**Done criteria for Phase 1.** G1–G6, including the **clean tree after the four
commands** in the real `examples/bracket`; the R-8 oracle shows identical statuses; the E4
table is exact and the `--force` soundness oracle passes; `EntriesAreDeterministic` holds
on every bundled baseline; CLAUDE.md gains invariants 7, 8 and 9 (reject half); the commit
names S-01, S-02, S-04 (packs), S-05, S-07, S-08–S-15, S-19, S-20–S-34, S-36–S-40, S-42–S-44,
S-45 (digests from bytes), S-47,
S-64, S-65, S-68, S-69 (tags), S-76, S-83, S-84, S-87 (pack mode and tests), S-89.

### 4.2 Phase 2: `needs` and unknown, evidence semantics, `terminal`, export, REPORT.md

**Why this order.** This is where claim semantics change, so the oracle becomes
"blocking only grows" (R-8). Composition comes first (2.1), then the graph (2.2), then
admission's second half (2.3), then goalposts and the cross-check (2.4). Terminals,
signing, export and the report come last (2.5), because export's refusal predicate
consumes all four.

Detail — target transcript, record shapes, tests, panel questions: [`docs/plan/phase-2.md`](plan/phase-2.md).

- Checkpoint 2.1: honest composition (Kleene, D-01)
- Checkpoint 2.2: the refusal graph
- Checkpoint 2.3: paired admission
- Checkpoint 2.4: goalposts in one place, the per-verdict margin, the cross-check
- Checkpoint 2.5: `terminal`, signed results, export, REPORT.md

**Done criteria for Phase 2.** G1–G6; the blocking-only-grows oracle, with every status
change on the corpus listed with its reason; all 54 bundled gates and the 6 bracket gates
admitted *paired*; the bracket's exact failure signature (G4) unchanged; CLAUDE.md gains
invariants 9 (paired where a known-good exists, reject-only reported), 10, 11 and 12
(readiness/export); the commit names S-03, S-04, S-06, S-17, S-18 (margin half), S-35,
S-41, S-45, S-46, S-48–S-62.

### 4.3 Phase 3: `trade`, and the human experience through the agent

**Why this order.** `trade` consumes every PASS semantic from P1 and P2, so invariants 1
and 2 are carried *into* the tradespace before dominance exists (3.1). Taste follows
(3.2). The agent channel (3.3, 3.4) renders what P2's Readiness object and `export
--dry-run` already compute, so it adds no opinions of its own.

Detail — target transcript, record shapes, tests, panel questions: [`docs/plan/phase-3.md`](plan/phase-3.md).

- Checkpoint 3.0: live probe of the Claude Code mechanics, and `C:` goldens
- Checkpoint 3.1: the tradespace
- Checkpoint 3.2: taste (D-18)
- Checkpoint 3.3: human-channel outputs
- Checkpoint 3.4: plugin commands, hooks, permissions, skill rewrite

**Done criteria for Phase 3.** G1–G8; the 3.0 probes recorded; `atompipe trade` with zero
candidates is a CI smoke test on the bracket; the demo tradespace (petg-8, pla-7, alu-5,
plus the C9 creep claim) is built in a temp repo *by the tests only* — no `cand/*`
branches ship; G8 runs `status-verbatim` and `no-proven`; CLAUDE.md gains
invariants 11 (channel), 12 (short/trade) and 13; the commit names S-16, S-44 (preference
half), S-63, S-66, S-67, S-69 (words), S-70–S-75, S-81, S-88.

### 4.4 Phase 4: the site

**Why this order.** Everything the page shows is computed in the spine first (4.1, 4.2)
with Python tests; only then does the template render it (4.3, 4.4), and static scans
prove the template computes nothing (invariant 14). *Layout is not truth*, so the spine
computing slot positions does not breach "the site never computes truth" (D-22).
`examples/bracket` is the test project; thickness stays 7.0 so a fresh clone shows a real
failure lit on the part.

Detail — target transcript, record shapes, tests, panel questions: [`docs/plan/phase-4.md`](plan/phase-4.md).

- Checkpoint 4.1: `graph.py` and the layout
- Checkpoint 4.2: `focus.py`, history frames, suggestions
- Checkpoint 4.3: the template
- Checkpoint 4.4: the fold

**Done criteria for Phase 4.** G1–G8; `BracketFreshClone` green; CI builds the bracket
site and parses `graph.json`, `focus.json` and `history/index.json` strictly; CLAUDE.md
gains invariants 12 (site) and 14; the commit names S-60
(render), S-69 (site words), S-75, S-77–S-80, S-81 (ribbons), S-82.

### 4.5 Phase 5: model auto-mutation, derived independence, `bench/`

**Why last.** Mutation needs the cache (P1: `mutation-*` entries), paired admission (P2:
a survived mutant makes a gate inadmissible *for a claim*), `vcs.snapshot` (P3: the temp
worktree, D-23), and `modelio.influence` (P3: driver discovery).

Detail — target transcript, record shapes, tests, panel questions: [`docs/plan/phase-5.md`](plan/phase-5.md).

- Checkpoint 5.1: `src/atompipe/mutate.py`, exposed as `gate selftest --mutate`
- Checkpoint 5.2: derived independence (D-32)
- Checkpoint 5.3: `bench/`

**Done criteria for Phase 5.** G1–G8; `python -m bench --quick` green in CI with the tree
hash unchanged; CLAUDE.md gains invariant 15; the commit names S-18 (mutation half), S-19
(measured), S-85–S-87.

### 4.6 Hand-offs between phases

Each phase hands the next these things, or the next phase starts on records that do not
exist yet.

| From | To | Hand-off | If missing |
|---|---|---|---|
| P1 | P2 | `Verdict.outcome`; the cache (with `needs` decided as not a ρ edge, D-04); control entries with a `good` slot; `freshness()`; `SECTION_PROVEN`; the strict reader; `results/` layout | P2's unknown has no single outcome to extend; paired admission has nowhere to live; signed results have no home |
| P1 | P3 | the record layout and `verdicts.WATCHED` (Stop fingerprint, permission table); `last_check.json`; `claims.explaining_verdict`; `status_tag` unification; a cache every worktree can reach | the Stop hook fingerprints the wrong files or runs project code; `trade` re-runs everything per worktree |
| P2 | P3 | `Readiness`; `export --dry-run`; `export_refusals`; `terminal_state`; signed `PhysicalResult` and the channel derivation; `blocked_by` wording; `report.HUMAN`; `claims.margin` | `/ready` and `ask --next` rank 2 have no source; `/tested` has nothing to sign; `next:` grows its own margin arithmetic |
| P2 | P4 | `Readiness`, including `fold`, and `export_refusals`; `needs` depth (checks sub-lanes); terminal buckets (the outcome column); `Verdict.comparator` on every bundled gate | the outcome column cannot say "nothing can check this yet"; the page computes margins or the fold |
| P3 | P4 | `report.HUMAN` with dispositions; `next_action`'s margin predicate (focus rule 3); `asks()` (bring list); `trade.compare` (small multiples); `modelio.influence` (ribbons) | the site grows its own vocabulary and arithmetic — it computes truth |
| P3 | P5 | `vcs.snapshot`; `modelio.influence`; derived `origin` from commit trailers (provenance only) | mutation writes into the real tree or re-derives drivers |

**Tests this plan knowingly modifies (R-6)** — each a strengthening, or a reversal a
settled decision forces, named with the replacement that guards the same risk:

- `test_invariants.ReportNeverOverclaims._proven_section`: raises when the heading is
  missing (P1.0).
- `test_packs.NegativeControlsFire.test_every_gate_passes_its_own_baseline` and its
  control test: a self-skip on the baseline or the control input now fails unless
  `availability(spec)` fails (P1.0).
- `test_packs.NoLeakedProvenance`: prunes nested checkouts, with a negative half, and scans
  every UTF-8 file rather than an extension list (P1.0).
- `NegativeControlsFire` and `ControlsAreSealed`: keep their own assertions at tier 3 and
  gain an agreement case against `packs.demonstrate` (P1.1, D-25); `ControlsAreSealed`
  gains the planted unsealed fixture and the no-host-`ctx.params`-read assertion (P1.2) and
  the host-claim-limit case (P2.4).
- `test_terminal`'s legacy-result message (P2.5) names `claim physical`, the only signing
  channel in P2; P3.4 switches it to /tested — a reversal forced by the channel not
  existing before, guarding the same risk (a legacy result read as signed).
- `test_site`: the `RunMeta` import and `last_run` fixture are removed (P1.2); the mtime
  staleness test becomes a `records_digest` test (P1.3); the PARTIAL test asserts the claim
  is not PASS (P2.1).
- `test_packs.test_every_pack_ships_a_validity_guard`: the keyword heuristic becomes a
  structural `needs` check (P2.2).
- `test_invariants.StatusPrecedence.test_physical_with_result` (`:278-283`): an
  **unsigned** result now expects UNVERIFIED (P2.5), and a signed, bound VERIFIED case is
  added beside it, so the VERIFIED path stays covered and the test gains the case that
  mattered.
- `test_site._SiteCase._ledger` and `HonestyOnThePage._built_state` (`:110-116`,
  `:499-507`) put verdicts into `ledger.json`, which P1.3 stops reading, so as written
  they would pass on a page that shows nothing: they are rebuilt through the verdict cache,
  with a positive control that a passing gate *does* reach the page — in P1.2, the
  checkpoint that stops resolving ledger-held verdicts, so the partial-marker test
  (`test_site.py:539-557`) never goes red between checkpoints.
- `test_site.test_gitignore_covers_vendor_and_not_the_generated_data` (`:180-192`) is
  **reversed** by D-21. Its replacement asserts the scaffold ignores `data/`, `assets/`
  and `vendor/`; the risk the old test guarded — a clone that shows no site — is now held
  by CI building and publishing the site and by `BracketFreshClone` (P4.3).
- `test_site.VendorIsAllOrNothing` (`:711-742`) asserts `THREE_VERSION` in every vendor
  URL, which per-library d3 breaks: the pin assertion is generalised to every library —
  each import-map and `VENDOR_LIBS` URL pinned at an exact version, one per package (P4.3).

`StatusPrecedence.test_stale_is_not_pass` is kept byte-identical by keeping `stale=True`
as the all-gates alias (P1.2). `test_records.NoCommandWritesARecord` is written in P1.3 as
a property — every path a command writes is gitignored at the time or a new verdict
entry, except the one-time marked ignore blocks — so P2.5's `/REPORT.md` block, P4.3's
site rebuild (`data/**`, `build.json`, `importmap.js`) and P3.1's `ask --next` meet it
without editing it; adding `ask --next` to the commands it runs (P3.1) is a
strengthening.

---

## 5. The human-experience map

Each bullet of the brief, mapped to the phase, the deliverable, what it depends on, and
the test that proves it.

### 5.1 Through the agent (the existing `.claude-plugin`)

| Brief bullet | Phase | Deliverable | Needs | Guarding test |
|---|---|---|---|---|
| `/start /check /status /ask /pick /ready /tested`, one CLI call and one fixed output shape each | 3 (+2 for `/ready`) | `skills/<name>/SKILL.md` (`disable-model-invocation`), the UserPromptExpansion hook, `agent.COMMANDS`: `/start`→`start`; `/check`→`check --short`; `/atompipe:status`→`status --short`; `/ask`→`ask --next`; `/pick`→`trade compare --pick`; `/ready`→`export --dry-run` (D-15); `/tested`→`claim physical` via the hook | P2 export and signing; P3.0 probes | COMMANDS keys equal the user-invoked skill dirs; every argv parses; `test_shapes` line count and regex on the bracket, each with a negative control; exactly one `cli.main` per expansion |
| Stop hook prints `status --short` (counts + time, the failing thing, a computed `next:`) whenever the model or ledger changed | 3 | the Stop handler; `verdicts.WATCHED` fingerprint; `last_check.json` (P1); `shown.json`; `turn.json` | P1 `last_check.json` | silent twice; prints after a model edit that flips nothing; no dirty tree; no project code (sentinel); exactly 3 lines |
| verdict lines and status blocks quoted verbatim; verified / validated / proven only when the ledger says so | 3 | tool text as `systemMessage` (the model cannot paraphrase it); the skill rewrite; `report.HUMAN`; evals | P2 `report.HUMAN` | `test_vocabulary`; evals `status-verbatim`, `no-proven` |
| `ask --next` returns ONE ask with the why and the how | 3 (ranks 1–3 need P2/P3) | `ask --next`, `ask --tested`, shared `asks()` | P2 terminals; P3 trade | never more than one ask; `how:` never contains `atompipe `; one fixture per rank |
| choices via AskUserQuestion, one row per candidate showing only what differs; the pick + reason recorded as preferred_against automatically | 3 | `trade.compare`, `pending_pick.json`, the PostToolUse capture, `decisions.add_preference` | P3.1 dispositions | a genuine pick records; tampered, pre-answered, timed-out or why-less picks record nothing and say so; idempotent on `tool_use_id`; a 6-survivor pick is recorded |
| permission rules: `model/` and `site/` auto-accepted; `claims/`, `gates/`, `selftest/` ask first | 3 | PreToolUse `agent.permission`; `site/**` allowed as the brief says, its generated outputs (`data/`, `assets/`, `vendor/`, `importmap.js`) denied; `decisions/`, `objectives.json`, `inputs/*.json`, `params/**` added to ask; generated truth, `.atompipe/export/**` and `results/**` denied | P1 layout | table-driven path tests (symlinks, `..`, Windows paths, plan mode, the `site/` allow and deny rows, extractions and param records, the export package) |
| `/start` ends with a status block and a site URL in the first turn, or says why not | 3 (content from 4) | `atompipe start`, `site serve --background`/`--stop`, `turn.json` | P1 `last_check.json` | 4-line shape; HTTP 200; pid reuse; port fallback; "site: not served — <reason>" |
| human vocabulary in human channels (checks, must be true, verified / failing / needs a real part / assumed / not yet checked, why this number) | 2, 3, 4 | `report.HUMAN` (P2), disposition phrases (P3), exported into `state.json` (P4) | — | `test_vocabulary`; the JS/HTML literal scan against its lists |

### 5.2 The web UI (`site/`: plain HTML, ES modules, d3 by import map)

| Brief bullet | Phase | Deliverable | Needs | Guarding test |
|---|---|---|---|---|
| one question above the fold: can I build this? two sentences, counts, last-run age, commit; nothing else | 4 | `panels.headline`, `report._verdict_parts`, `vcs.git_head`, `readiness.fold` | P2 `Readiness`, `export_refusals` | `Fold`: lead + caveats rebuild the verdict; a no-git temp dir shows the reason; the answer is `readiness.fold_words`, printed as given; a project refused only for an unowned critical assumption never shows "Only a real-part test remains" |
| layered flow canvas, five fixed columns, Sankey-style ribbons, not force-directed | 4 | `graph.py`, `canvas.js` | P3 `influence` | `test_graph` (left to right; one outcome per claim; `nocheck` visible) |
| force layout only for the ≤12-node ego graph | 4 | `graph.ego`, `ego.js` | — | `Ego` (≤ 12; always holds the node; failing first) |
| revision scrubber; decisions as marks; last N `state.json` snapshots | 4 | frames derived from the last N commits plus uncommitted local builds, in `history/`; `scrubber.js` | P1 tracked cache; P3 `vcs.snapshot`, preferences | `History` (a fresh clone with ≥ 3 commits gives ≥ 3 frames) |
| fixed encodings: column = stage, colour = status only, width = claims weighted by criticality, opacity = age, shape = kind, bullet bar per claim | 4 | node and edge fields — `opacity` (stale, or a drifting terminal past `aged_after_s`), `shape` from the kind table, tombstones dashed; `claims.margin` per verdict | P2 `Verdict.comparator`, `claims.margin` | `Margins`; `Encodings`; a colour-only tone table; `min_wall` inside, not outside |
| overview first, semantic zoom, linked highlighting across canvas, viewer, details; details on demand with byline | 4 | `group` and `zoom` on nodes; the node id as the one selection id shared by canvas, Locator targets and panel rows; `byline` from `git log`; the diagram-locator fix | — | `Encodings` (every canvas id resolves to a panel row and, where located, a viewer target); `Bylines`; `DiagramTargets` |
| positions never move; transitions animate; change marks via `localStorage` in try/catch | 4 | `sticky_layout` from tracked first-appearance order (no `--relayout`), `motion.js` (tone, width and opacity tweened by id on a new build or a scrubber step, off under reduced motion), `prefs.js`, `changes_since` | — | `StickyLayout` (identical slots on a fresh clone and locally); the transition and `localStorage` scans |
| printable test card; bring checklist from the ask list; candidate small multiples with only differences at full opacity | 4 (sources from 2, 3) | `testcard.js` with the spine's `card_id`, `bring.js` from `asks()`, `multiples.js` from `trade.compare` | P2 ρ binding; P3 `asks()`, `trade.compare` | `card_id` in `state.json` changes with ρ; `ask --json` == `state.asks`; `AskWaitsForTheChecks`; `Multiples` differs sets |
| `site build` diffs the previous state and writes focus.json with plain-words reasons; deterministic rules tested one by one; the agent may add, never replace | 4 | `focus.py`, `suggestions.json` (byline stamped by the build), `focus.js` (opens the chosen view, ring emphasis, reasons, the suggested strip); `check`, `/tested` and `/pick` rebuild the site and the page polls `data/build.json` | A-9 for the widenings and rules 3, 7, 8 | `test_focus`; `SuggestionsCannotReplace`; `SiteFollowsCheck` |
| the site never computes truth and never writes | 4 | static scans | — | `test_site_template` |
| a fresh clone shows a real failure lit on the part (thickness 7.0) | 4 | `views/elevation.py`, the `arm_tip` locator | — | `BracketFreshClone` (G6) |

---

## 6. E1–E8 (paper §17)

| E | Question | Built now | Deferred, and why | Data recorded now so it can run later |
|---|---|---|---|---|
| E1 | Gate vacuity: how often does an unconstrained model produce validators that cannot reject known-bad inputs? | P5 `bench/e1_vacuity`. The corpus is every shipped gate (54 pack + 6 bracket) × wrapper operators (always_pass, always_fail, logger, invert, frozen_input, blind_to_key, loosen ×1.1 and ×10 with a no-op filter, nan_pass, returns_none, always_raise) plus AST `drop_compare` (coverage only), plus fixture operators (unsealed over a foreign host, returns-good-input). Ladder: A0 none → A1 declared → A2 reject-only → A3 paired → A4 boundary mutation. Preregistered: the five vacuous-by-construction operators admitted 0 at A3; always_fail admitted at A2, not A3; every real gate admitted at A3. | The paper's "unconstrained generative model" arm needs generated validators; `corpus/e1/` accepts offline drops and bench never calls a model (D-33). `PROTOCOL.md` labels this "the deterministic floor of E1": it measures the admission rule's sensitivity and specificity, not generators' vacuity rate. | Control entries (P1) carry the fixture's measured/limit — the control-margin distribution. |
| E2 | Refusal ordering: does cost- and latency-aware ordering cut verification spend while keeping coverage? | Nothing that schedules; preregistered in `PROTOCOL.md`, and `bench e2` prints SKIPPED naming the missing verdict history. | No scheduler before history (brief, D-31). Today every gate runs, so order has zero effect on spend (`gates.py:1225-1249`); `needs`-pruning is the first mechanism by which structure changes spend. A later E2 is an offline replay over the candidates × gates verdict matrix: registration order vs cost-ascending vs elimination-per-cost. | measured `duration_s`/`cpu_s` per entry and obs (P1); `pruned` per sweep (P2); cache history across branches (P3). |
| E3 | Dependence: how strongly do proposer-generated validators correlate in failure with the proposer? | P5 `bench/e3_dependence`. Population A, fixed and imported from `mutate.py`: formula v1 (operator swaps, exponent −1, constant ×2 inside `build()`), table v1 (module-level numeric dict entries ×2 and ×0.5), boundary v1. Population B, *assumed* independent — same-repo pack fixtures, so the independence is not established and `PROTOCOL.md` says so: beam-analytic's shipped fixtures mapped onto the bracket through the adapter where the mapping is exact. Sources on C1–C3 at thickness 7.0 and 8.0: the project gates; beam-analytic via the adapter in two arms (own table; the model's shared table); the openmodelica mirror when omc exists, else a SKIPPED row. Wilson 95% per class, never pooled; control dependence reported too. | **Caveat, stated in `PROTOCOL.md`:** for the bracket's project gates the formula-class result is *partly true by construction* — they are pass-throughs of the proposer's own derived values, so I ≈ 0 on formula mutants is the expected answer. The value is demonstrating the measurement and the shared-input effect (the two adapter arms), not a surprising number. The "proposer" is the bracket's author, not a generative model. | — |
| E4 | Staleness localization: after one parameter changes, does the graph invalidate exactly the gates whose inputs changed? | **P1 unit test** (`tests/test_staleness.py`: exact on the bracket's 12 fields plus an early-cutoff row, with a `--force` soundness oracle). P5 `bench/e4_staleness` measures recall and over-invalidation across every pack baseline (one key at a time; path-valued keys get their file *content* perturbed in the sandbox), bracket Config fields, and gate-version perturbations, against a brute-force output oracle excluding `detail`. Negative control: swapping in the name-level recorder produces misses on the bracket, so the metric can fail. | — | — |
| E5 | Resurrection: how often do rejected or dormant alternatives become relevant after changes? | The mechanism only (`test_trade.E5Mechanism`, not the experiment): P3 `trade --json` → `returned: [{candidate, was, because}]` per requirements change; `git log -p trade/` is the history. D-10 makes requirement-driven returns observable on the bracket (a relaxed C1 changes ρ). Preregistered in `PROTOCOL.md`; `bench e5` prints SKIPPED. | Rates need real time and real requirement changes. | machine-checkable `reconsider_when` (claim id + acceptance, objective, or decision id). |
| E6 | Refusal bandwidth: does the fraction reaching grounded status saturate as generation rises? | Preregistered in `PROTOCOL.md`; `bench e6` prints SKIPPED. | Needs G_r and B_R over real windows. No scalar Λ, ever (§6, §18). | `trade --json` backlog counts; first-seen and resolved times per candidate, derived from the git history of `trade/<name>.json` (P3.1), which carries no timestamps itself. "Materially distinct" is defined in `PROTOCOL.md` against the objectives' tolerances or the claim outcomes — a projection hash would count thickness 7.0 and 7.0000001 as two and inflate G_r. |
| E7 | Cycle-time intervention: does cutting a downstream refuser's latency raise useful throughput? | Preregistered in `PROTOCOL.md`; `bench e7` prints SKIPPED. | Needs real interventions on downstream physical refusal latency, which a repo cannot run. | physical-result `when` and `card_ready_when` — the time `check` first saw the card ready, stamped in obs and copied into the signed result (P2.5) — so L = `when − card_ready_when`; with no card-ready time, L of a human or physical terminal reads "not measured", never invented; its C is the optional cost the human records at `/tested` (P2.5), else "not measured". |
| E8 | Cross-domain scaling: can (I, L, C, S, D) characterise verification topology across domains? | P5: descriptor rows in `gate list --json` (I bracket, L and C measured with n or "not measured yet", the tier only in its own declared-budget column, S as a set labelled demonstrated or declared only, D) for the seven in-repo packs — all within the paper's single Hardware row. Preregistered; `bench e8` prints SKIPPED. | The real test needs external domains; this repo cannot test cross-domain E8, and `PROTOCOL.md` says so and makes no cross-domain claim. | — |

---

## 7. The "slipped through" register

Eighty-nine defects found during the phase-0 read, each with evidence, how it was seen
(`run` or `read`) and the checkpoint that closes it: [`docs/plan/slipped.md`](plan/slipped.md).
They are the material for each phase's commit message — what slipped through, and why.

---

## 8. Ask-points: the user's yes is needed before these land

The brief's "ask me before" list has four items: any METHOD.md change; any third-party
import under `src/`; removing a CLI command a pack or the skill still references; any
focus rule this plan is less than sure about. Nothing else is asked but A-12, a
conditional ask that exists because it would break a brief bullet ("positions never
move"): everything else is decided in §2.2 with its reason, and nothing here re-opens a decision the brief settled —
`export` stays exactly as strict as the brief states, and `check`, not `export`, already
gates building a test article (`claims.py:490-496`). **A phase never blocks on an ask**: the code lands on the conservative default
under "Until answered", and only the gated item waits.

| # | Ask | Why it needs you | Recommendation | Until answered |
|---|---|---|---|---|
| A-1 | **METHOD.md rule 5 — the second half of admission** (P2). Add one sentence: *"…and it must pass a known-good input, or it has only proven that it refuses everything; `atompipe check` will not count a verdict from a gate whose control has not demonstrated both at its current version."* | METHOD.md is ask-first. Rule 5 (`METHOD.md:92-108`) promises only the reject half, which §2.1 and fn 4 call insufficient. | Yes. | P2.3 enforces both halves in code, PACK_FORMAT and SPINE_CONTRACT; METHOD.md is untouched and stays true but incomplete. |
| A-2 | **METHOD.md rule 9 — the never-blur list** (P2). Reword "skipped — the gate did not run (its tool is missing)" to "skipped — the gate did not run: its tool is missing, a parameter it needs is absent, or a check it depends on did not pass. Nothing was proven." | METHOD.md. After P2 a skip can also mean *a prerequisite was not established* (D-02), and packs already self-skip for missing parameters (S-54). | Yes. | No edit. Unknown is encoded as a skip (R-2), so the rule's "skipped" already covers it; the vocabulary and report say which kind. |
| A-3 | **METHOD.md — a short section on the tradespace and taste** (§9–§12: ship the space; four disappearances; preferences recorded, never derived). | New doctrine in METHOD.md. | Yes, ~8 lines, *after* P3 exists, so the doctrine describes something real. | No edit; SPINE_CONTRACT and the skill carry it. |
| A-4 | **METHOD.md rule 3 and "the ledger rendered"** (P1). Rule 3 records rationale, rejections, source and the protecting gate "inline and in the ledger" (`METHOD.md:52-58`); the report and the site "render the ledger" (`:22`, `:182-186`). After D-06 and D-30 the ledger is a generated index holding no rejections, no gates and no verdicts. Reword to "inline and in the records", and "the ledger rendered" to "the records and the verdict cache rendered". | METHOD.md is ask-first, and D-06 and D-30 make these sentences false as written. | Yes. | No edit. Where each field lives meanwhile: *why this value* — the model's Config docstring; *what lost* — the model's `PARAMS`, or a param record's `rejected` where the model states none (D-30); *source* — `params/<name>.json` `source`/`grounded_by`; *which gate protects it* — derived from recorded reads and `influence`, printed by `why`; verdicts — `.atompipe/verdicts/`. The report and site read the records and the cache, never the index. |
| A-5 | **Remove `extract`** (after the P3 skill rewrite). References: skill 1 (`skills/atompipe/SKILL.md:79`); packs 0; METHOD 0; README 1 (`:192`); SPINE_CONTRACT 1 (`:264`); `examples/bracket/inputs/README.md` 2 (`:9, :13`); spine strings 7 (`cli.py:788, 888, 1156, 1202`; `store.py:113, 117`; `report.py:803`) plus 1 docstring (`cli.py:1168`). | The skill references it. | Remove: it appends an extraction to `inputs/<id>.json`, which the agent can edit and `check` validates. | A one-file shim (P1.3). |
| A-6 | **Remove `decide`**. References: skill 1 (`skills/atompipe/SKILL.md:132`); packs 0; SPINE_CONTRACT 1 (`:268`); spine strings 2 (`decisions.py:251, 282`). | The skill references it. | Remove: `decisions/<slug>.json` is edited directly, preferences go through `/pick` (D-18), and the reason-required check moves to the record reader. | A one-file shim writing `decisions/<slug>.json`, time stamped at the edge, no `--when` (S-44). |
| A-7 | **Remove `packs add`**. References: skill 1 (`skills/atompipe/SKILL.md:123`); packs 0; spine strings 1 (`cli.py:870`). | The skill references it. | Remove: `project.json` `packs[]` is edited directly and the strict registry validates it in `check`. | A shim editing `.atompipe/project.json`. |
| A-8 | *(For visibility — not required by the ask list, because no skill or pack references them. Say if you want any kept.)* Removed in P1.3 with their strings rewritten: **`claim add`** (spine strings 5: `cli.py:772, 790, 1060`, `report.py:427`, `site_template/lib/panels.js:165`; plus 1 docstring `cli.py:3533`; SPINE_CONTRACT `:262`); **`claim edit`** (spine strings 3, none inside `claim edit` itself: two in `cmd_claim_add`, `cli.py:1320, 1347`, and one in the kept `cmd_claim_physical`, `cli.py:1489`, whose refusal is rewritten to name the file edit; plus 1 docstring in `_acceptance_from`, `cli.py:1288`); **`packs remove`** (0); the **`model --set-entry` flag** (`cli.py` ×9: `773, 793, 863, 947, 2127, 2148, 2160, 3084, 3473`; `.github/workflows/ci.yml:75`). **Kept:** `ingest` (skill 1 `skills/atompipe/SKILL.md:78`; README 1; SPINE_CONTRACT 1; examples 3; spine 6 — it moves bytes, dedupes and pins sha256); `gap` (skill 1 `skills/atompipe/SKILL.md:128`; packs 2 `packs/cad-solid/PACK.md:894`, `packs/thermal-analytic/PACK.md:67`; README 2; docs 3; spine 8 — it becomes read-only); `claim physical` (spine 2 `cli.py:1498`, `report.py:618` — the signing channel, D-12). | Listed so no removal is a surprise. | — | Removed in P1.3. Counts are `atompipe (-C X )?<cmd>` at `1e09113`, excluding the user's untracked draft; the phase that removes recounts first. |
| A-9 | **Focus rules** this plan is less than sure about (P4; A-9.3 is asked before P3, whose `next_action` rule 10 shares its predicate). Rule 5 is decided, and rules 1, 2, 4 and 6 ship in their literal form (§4.4, `docs/plan/phase-4.md`). Each widening below, and rules 3, 7 and 8, ships only on a yes, with its session-naming provenance. | The brief: ask about any focus rule you are less than sure about. | per row | Rules 1, 2, 4, 5 and 6 fire in their literal forms; widenings and rules 3, 7, 8 wait. |
| A-9.1 | *Rule 1, claim flipped pass → fail.* Does it include verified → refuted (physical), stale → fail, and a failure on first appearance? | | Widened: previous ∈ {pass, verified, stale}, new ∈ {fail, refuted}. A first appearance does not fire — the base overlay already lights every failure, so the bracket's fresh-clone failure needs no rule. | |
| A-9.2 | *Rule 2, parameter moved and nothing flipped.* What counts as "moved", with several params at once? The bracket's documented fix (7 → 8) is a **fail → pass** flip, which matches neither rule 1 nor rule 2. | | "Moved" = any config value that differs, compared exactly; one item per moved param. A fail → pass flip gets no rule of its own; on the bracket rule 3 then fires (6.25%) and suggests the sweep. Revisit only with a named session. | |
| A-9.3 | *Rule 3, within ~10% of the limit.* The denominator, the side, the comparators, multi-gate claims. | | `claims.margin` (D-17): `rel = distance / \|limit\|`, passing verdicts only, 0 ≤ rel ≤ 0.10; BETWEEN uses the span; EQ, NE, a zero limit, and a margin whose side disagrees with `passed` never fire; it uses the claim's `headline_gate`. Fires at thickness 8.0 (6.25%), not at 7.0. | Neither focus rule 3 nor `next_action` rule 10 fires; `claims.margin` still feeds the cross-check, the bullet bar and line 2's closest-margin fact. |
| A-9.4 | *Rule 6, candidate added.* What identifies a candidate, and does a resurrection count? | | A new disposition record, not a bare branch (proposing is the untrusted proposer's job, so every scratch branch is not a candidate). A candidate returning from dormancy fires the same rule: "returned: <reconsider_when> now holds". | |
| A-9.5 | *Rule 7, many things changed → overview only.* The threshold, and whether it suppresses rule 1. | | ≥ 6 changed entities (status flips + param moves + claims added or removed); 3 was rejected because one ordinary edit moves 3 claims. The overview becomes the *view*, but a flip to failing is never hidden: its reason stays in `items` and its node keeps its change mark. | |
| A-9.6 | *Rule 8, nothing changed but results are old.* Once verdicts are content-addressed, a closed-form verdict with an unchanged ρ is exactly as valid as a fresh one. | | Fires only for checks whose result can drift without an input change — tier EXTERNAL, datasheet and sourcing terminals, human and measurement results — older than `AGED_SECONDS`, published by the spine, by the terminal's own age (P4.1), not the last re-run's. That is the case study's sourcing volatility (paper §14). | |
| A-9.7 | *Rule 4, new claim with no check.* The first draft fired when "a claim's check path is empty — measurable UNCLAIMED, or terminal `none`", and exempted assumptions — but a measurable claim cannot declare `none`, and an assumption defaults to it, so the second clause added only what the sentence exempted; and nothing compared with the previous snapshot, so a standing C7 fired on every build. Reading (a): fire for any claim with an empty check path, every build. Reading (b): fire once, when the claim first appears. | | (b): fires iff the claim id is absent from the previous snapshot **and** it is UNCLAIMED, or has terminal `none` and is not an assumption. A standing gap stays visible through the canvas's dashed node and sentence 2, not through focus. Tests: a new assumption does not fire; a standing C7 does not fire on the second build; a new measurable claim with no gate fires once. | |
| A-10 | **Your untracked `docs/BENCHMARK_ENGINE.md`** turns `NoLeakedProvenance` red in the main checkout. | It is your file; this plan never opens, edits, commits, quotes or excludes it. | Your call: change the word, or keep the file untracked. The test is **not** narrowed to tracked files — the untracked scan is what caught this before a commit, which is the test working. | Phase gates run in a clean worktree (G1); every phase summary reports "1 red, the user's untracked draft, unchanged". |
| A-11 | **REPORT.md's "What is PROVEN" heading** (P2). Rename it "What is verified", with the one-word edit in METHOD rule 9 ("Every PROVEN row" → "Every verified row", `METHOD.md:167`). `report.SECTION_PROVEN` stays the constant's name, so invariant 4's tests follow the new text. | REPORT.md is a human channel and "proven" is outside the human vocabulary (M2.2a, M18.1, S-75); rule 9 names the heading, so the rename is a METHOD edit. | Yes. | The heading keeps its text; `test_vocabulary` exempts exactly that string, in REPORT.md only. |
| A-12 | **A new layout epoch** (P4; conditional — asked only if it arises). If tombstones ever crowd a column, compacting the slots would move every surviving node once, marked as a visible break in the scrubber. | The brief says positions never move between builds; an epoch breaks that bullet, so the plan cannot decide it. | No epoch until a real project shows the crowding, named in the question. | Tombstones accumulate and nothing moves; there is no `--relayout` (4.1). |

**Decided, and flagged for visibility in the phase summaries** (not on the ask list):

- **d3 under `src/atompipe/site_template/`** (P4): the brief pre-approves "d3 from CDN by
  import map like three.js". What lands is exactly 11 pinned d3 micro-packages (selection,
  zoom, force, transition, interpolate, color, dispatch, drag, ease, timer, quadtree),
  loaded lazily, vendored with sha256 verification, and the canvas draws without them.
  No other library, and **no third-party Python import anywhere under `src/`** — the CI
  AST walk proves it and is extended to `scripts/` and `hooks/` in P3 and `bench/` in P5.
- **Kleene composition (D-01, P2.1)** changes `check`'s exit code for projects whose
  passing claim also has a skipping tag-bound pack gate. It is the honest direction.
- **Q2.1 / D-03** generalises the brief's "prerequisite failed" to "not established",
  keeping the brief's words in the message when the root actually failed.
- **D-21** reverses the site scaffold's "commit `data/`" choice (`site.py:175-184`).
- **D-26** redefines channel parity as equal `extra` key sets.
- **D-12** removes the `--who` and `--when` *flags* from `claim physical` (a flag change,
  not a command removal; neither flag is referenced by the skill or a pack).
- **METHOD.md needs no edit** for: `ask`, `inputs --unextracted` and `why` (all kept;
  `METHOD.md:74, 220-221`); *stale* (D-08 keeps `METHOD.md:175` true). The *PROVEN*
  report heading is asked, not decided (A-11).
- **CLAUDE.md edits** (not on the list): drop the typed test and module counts (P1.0); invariants 7–15 as their
  phases land; the layout table gains `verdicts.py`, `agent.py`, `trade.py`, `vcs.py`,
  `graph.py`, `focus.py`, `mutate.py`, `hooks/`, `scripts/`, `evals/`, `bench/`; the
  pack-author commands at `CLAUDE.md:96-99` name `gate selftest --pack`.

---

## 9. Non-goals

Each is a thing the brief says not to build, or a thing this plan refuses, with the reason.

- **No Bazel, Nx or other build-system dependency, and no build step for the site.** We
  borrow the shape (action keys, verifying traces, prerequisite graphs), never the
  dependency; the spine stays stdlib-only and the CI AST walk proves it.
- **No refusal scheduler.** Order is topological only. E2 waits for verdict history (§6); a
  scheduler without history would schedule on declared numbers, which the brief forbids.
- **No candidate generator in the spine.** `trade new <name> [--from REF]` takes no
  parameter values, and a parser-introspection test pins it. Proposing is the agent's job,
  and keeping it out is what keeps the proposer untrusted (§13).
- **No append-only run history and no global staleness.** git plus the verdict cache is
  the history; `obs/` is a bounded per-machine timing window, not a history.
- **No new record-mutating commands,** except where the command does what a file edit
  cannot do honestly: `claim physical` stamps clock, identity, channel and hashes; `trade
  pick` stamps identity and via; `ingest` moves bytes and pins the digest; `start`
  scaffolds and serves. No `tested`, `migrate`, `pack new` or `pack export` (D-24).
- **No proposer-filled independence, latency, cost or origin.** No scalar independence, no
  threshold, no Λ on screen (§3, §6, §18).
- **No claim of cryptographic identity.** "Signed" means tamper-evident and
  channel-attested (D-13), and the docstrings say so; HMAC with a local key is forgeable by
  the same-user agent or verifiable only by the signer.
- **No `--trust-cache`** at `export` or in CI (R-9).
- **No writing the user's `.claude/settings.json`** — a plugin reaching into user config.
  The permission rules are a hook, and a speed bump.
- **No site that computes or writes.** No JS diffing, margins, statuses or layouts; the page
  renders JSON the Python tests have already checked. No browser in CI: static scans plus
  Python-side tests.
- **No `@skip` or `@expectedFailure` in invariant classes** (R-7), and no weakening of a test
  to go green (R-6).
- **No LLM calls or network in `bench/`, and no committed bench numbers**: a committed
  number goes stale silently and becomes a second source of truth.
- **No `cand/*` branches in this repo.** Demo tradespaces are built by tests in temp repos;
  upstream candidate branches would pollute the namespace and leave trade records dangling
  in a clone.
- **No weights in `objectives.json`** — a weight is a preference, and preferences are
  recorded, not computed (§12). **No automatic flip of a preference**: the machine surfaces
  reconsideration and never edits a decision.
- **No cross-pack `needs` in published packs** — pack load order is arbitrary, and a missing
  pack is like a missing tool.
- **No edits to METHOD.md without A-1–A-3**, and nothing touches `docs/BENCHMARK_ENGINE.md`.

---

## 10. Baseline state at `1e09113`

Established by the orchestrator and re-seen for this plan; nothing below was produced by
writing to the repo.

- **Branch and tree.** `grounded-refusal` at `1e09113`; clean except the user's untracked
  `docs/BENCHMARK_ENGINE.md`.
- **Tests.** 92 test methods: `test_invariants` 24, `test_packs` 11, `test_pack_keys` 13,
  `test_site` 44. Exactly one is red — `test_packs.NoLeakedProvenance` — and only because
  of that untracked draft. `CLAUDE.md:19` still says "32 tests".
- **`pack validate`.** All 7 packs report publishable. The check is static: it never runs a
  control.
- **`gate selftest`.** In a copy of the bracket, 6/6 fired, exit 0. At the repo root it exits
  2, "no atompipe project found", although `CLAUDE.md:99` says to run it there.
- **Bracket `check`.** Exit 1: `bracket.deflection` FAIL at 0.6997 mm against 0.5 (thickness
  7.0; 8.0 gives 0.469 and passes — `examples/bracket/model/bracket.py:69-75`); C1 FAIL and
  C7 UNCLAIMED are blocking; C5 physical (no acceptance), C6 a critical assumption. It
  rewrites the tracked `.atompipe/ledger.json` and appends a tracked `runs/NNNN-*.json`.
- **Inventory.** 14 spine modules; 7 bundled packs with 54 gates; the bracket's 6 project
  gates, all tier 0, pure arithmetic, no `site/`, no views, no packs. trimesh, numpy and omc
  are installed here (the mesh and openmodelica controls run); CI installs none of them, so
  those controls skip there, honestly.
- **Missing entirely:** `export`, JUnit, `bench/`, `needs`, `terminal`, `trade`, known-good
  admission, the verdict cache, graph.json/focus.json, and the plugin's commands, hooks and
  scripts (`.claude-plugin/` holds `plugin.json`, with no version, and
  `marketplace.json`, `source: "./"`).
- **CLI subcommands:** init, status, check, ask, ingest, inputs, extract, claim {add, list,
  show, edit, physical}, gap, gate {list, show, selftest}, report, why, decide, packs|pack
  {list, show, validate, add, remove}, model, site {init, build, serve, vendor, status},
  doctor. `pack` is an alias of `packs`, so the brief's `atompipe pack validate` works.
- **CI** runs `init … || true`, `model --set-entry` and `check || true` in the tracked
  `examples/bracket` (`.github/workflows/ci.yml:74-78`): it accepts a crash, mutates tracked
  files, and never runs `gate selftest` over the packs through the CLI.
- **Re-probed in memory for this plan** (scratch only): `{"passed":"false"}`,
  `{"pass":"no"}`, `Verdict(passed="no")` and `measured="n/a"` all render `[ok]` with
  `ok=True`; pass + skip and pass + never-run resolve PASS with `blocking()` empty; a
  PHYSICAL claim whose covering gate FAILED resolves UNVERIFIED;
  `reg.specs()[0].claims.append("C_other")` widens the stored spec; an identity fixture for
  `bracket.deflection` is reported "correctly failed … ~64x worse: 0.700 mm".

**Today's human-facing output** (a scratch copy of the bracket at `1e09113`) — the *before*
against which each phase's target transcript is judged:

```text
$ atompipe check
[FAIL] bracket.deflection : 0.700 mm at 15 N (limit 0.5 mm)
[ok  ] bracket.bending_stress : 3.7 MPa vs 15.0 MPa allowable (util 0.24, petg)
[ok  ] bracket.bearing : 0.19 MPa on 77 mm^2 across 2 bolt(s), allowable 15.0 MPa
[ok  ] bracket.model_validity : slenderness 8.6 (>= 5.0 for Euler-Bernoulli; below this the deflection gate under-predicts)
[ok  ] bracket.bed_fit : 74 x 30 x 7 mm vs 204 mm usable (220 bed - 2x8 brim)
[ok  ] bracket.min_wall : thinnest section 7.0 mm vs 1.2 mm minimum (3 perimeters at a 0.4 mm nozzle)
6 gates, 5 ok, 1 FAIL in 0.0s — tier 0, model 321107b55616
BLOCKING — 2 critical claim(s) must not be spent against:
[fail] C1 Tip sags no more than 0.5 mm at rated load — bracket.deflection: 0.700 mm at 15 N (limit 0.5 mm)
[uncl] C7 First mode is clear of the pump that sits on the shelf — no gate covers it — `atompipe gap --propose`
(exit 1; .atompipe/ledger.json rewritten; .atompipe/runs/0006-….json added)

$ atompipe status            (14 lines; excerpt)
v0.1 is NOT ready: 2 of 7 critical claims are unsettled — 1 failing (C1); 1 with no gate at all (C7). 3 of 7 claims are machine-verified against the current model. It is unverified in physical hardware: 1 claim needs a real object (C5).
claims 7 — ok 3 | FAIL 1 | gap 1 | phys 1 | assum 1
[FAIL ] C1 …   [gap  ] C7 …   [phys ] C5 …   [assum] C6 …
next: atompipe check --tier 0 ; atompipe gap --propose ; atompipe report --write

$ atompipe ask               (6 numbered asks; the first is a napkin sketch)
then: atompipe ingest <file> --kind <kind> --desc '<what it shows>'

$ atompipe gate selftest
… 6 control(s) in 0.0s: 6 fired, 0 BROKEN, 0 skipped
$ atompipe gate show bracket.deflection | tail -1
  last selftest: (never run)
```

Read against the brief's register: two tags for one status (`[fail]` vs `[FAIL ]`, S-69); a
constant `next:` line (S-66); an ask list blind to the claims (S-70); a selftest `gate
show` cannot see (S-08); a check that dirties `git status` (S-89); a headline that never
says what C6 assumes (S-59); "3 proven" in the README for a machine pass (S-75). Each is a
row in §7, and each has a checkpoint in §4 that removes it.
