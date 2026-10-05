# Glossary

One term for each thing, and each term taken from the paper — *Grounding by Falsification:
When Compute Is the Wrong Arms Race*, working preprint v0.14; § numbers below are its
sections. Where the paper uses one word for two things, the two are split here and the
split is said out loud (§6). Where the paper has no word, the row says so and gives the
reason for the word it uses instead.

The rule binds **human channels** (§7): whatever a person reads, or an agent repeats to
one. It does not rename code. Identifiers, record keys, directories and CLI subcommands
keep today's spelling until the rename pass, which works from §8; the *Code today* column
is the map between the two. The one exception is the state directory, which moves to
`.groundspace/` in P3 under its own migration ([`PLAN-v0.14.md`](PLAN-v0.14.md) §2.1).

**A development document.** The release bundle strips this file, as it strips
`docs/PLAN*`: it quotes the paper and maps it onto code, and the agents that test nopekit
in a sandbox must not share that prior (PLAN-v0.14, top). The words it fixes reach a user
through what the tool prints (`report.HUMAN`, planned) and through the skills.

What slipped through, and why this file exists: one word had come to mean four things.
`status --json` counts a claim `verified` when a human records a physical pass; the report
heads its machine passes "machine-verified"; `check`, `status`, `gate show` and `doctor`
say a control is "re-verified" when it is rerun to re-qualify its evaluator at a new
version; and the paper keeps *verified* for a property of the evaluator itself — that it
correctly implements its predicate (§6.3). A reader who saw "verified" could not tell
which had happened, in the one document whose value is that it never makes a reader guess.

**Term** is the only word a human channel uses; **Code today** lists the spellings in
`src/nopekit`, `packs`, `skills`, `site_template` and `.claude-plugin`; **Never say** lists
synonyms no human channel may use for it, and one in *italics* has an innocent second
sense, or needs a qualifier a scanner cannot apply, so review catches it rather than the
scanner (§7).

## 1. The chain

| Term | Paper | Means | Code today | Never say |
|---|---|---|---|---|
| **generator** | §7 "any generator, human or model"; it "cannot settle a claim by asserting it" | whoever proposes candidates, claims, parameters and evaluators | — (the agent, the user) | proposer · *the AI* |
| **candidate** | §7 "one configuration of the design, together with the claims made about it" | one point in the tradespace | the current design point; values in `Rejected`; **not** `ToolCandidate` (§6) | design point · *variant* · *alternative* · *option* |
| **claim** | §3 "a verdict about a scoped claim" | a scoped statement that must be true for the design to work | `Claim`, `claims/<id>.json`, `ClaimKind` | *requirement* · *must be true* (as a noun) · *spec* |
| **acceptance condition** | §2.3 "Acceptance conditions must be explicit" | quantity, comparator, limit and units a claim is judged by; its **limit** is the number (a band's are `limit` and `limit_hi`) | `Acceptance`, `claim.acceptance`, `limit`, `limit_hi`; from P2.4 `GateContext.acceptance`, `verdicts.acceptance_of`, the ledger keys `acceptance:` / `acceptance-shape:`, `gates.goalpost_runs`, `GOALPOST_FACTORS`, the token `goalpost:moves\|<key>`, the words in `report.HUMAN["acceptance"]`; identifiers keep *goalpost* until the rename pass | pass criteria · goalpost · goalposts · *threshold* · *criterion* |
| **falsification** | §1 "an evaluator produces evidence that a candidate fails a declared claim … A falsification is scoped" | one evaluator failing one candidate on one claim | refusal (the plans' older word); the verb for an evaluator failing: site FAIL hint "the gate ran and refused", `skills/nopekit` "It must refuse", `skills/pack-authoring` "anything that already refuses", "compares … and refuses" | refusal · refuser · refuter. The verb *refuse* stays only for a boundary that declines to act — the registry refuses an evaluator, an export refuses to emit, a permission rule refuses an edit — never for an evaluator failing a candidate (§6) |
| **evaluator** | §3 "An evaluator consumes registered inputs and produces a verdict about a scoped claim" | anything that settles a claim and has shown it can fail; domain names such as *design-rule check* stay | `GateSpec`, `gates.py`, `Verdict.gate`, `Claim.gates`, `Param.gates`, `gates/`, CLI `gate`; prose: gate, validator, check | gate · validator · verifier · refuser · *check* (noun) · *test* (noun, for an automated evaluator) |
| **automated evaluator** | §1 "verification is performed largely by automated and physical evaluators" | an evaluator that needs neither an article nor a person: a closed-form calculation, a simulation, a design-rule check. The counterpart of physical evaluator | `ClaimKind.MEASURABLE` ("a gate can settle it from the model"); prose "machine", "the model" | model evaluator · machine evaluator (*model-written* is §6.3's word for who wrote an evaluator, not what kind it is) |
| **physical evaluator** | Fig. 4 "physical evaluator"; Fig. 7 "consumes an article" | an evaluator that needs an article: a print, a fit, a load test, a field trial | `ClaimKind.PHYSICAL`, `claim physical --pass/--fail` | hardware test · real-world test |
| **terminal** | §3 "Some claims terminate in closed-form calculations; others in simulations, datasheets, measurements, expert judgments, or physical outcomes" | where a claim's evidence bottoms out. Display words: closed-form calculation · simulation · datasheet · measurement (a physical outcome is a measurement with a pass/fail acceptance) · expert judgment. The program that runs it is provenance, not terminal: a design-rule check run by an external tool terminates in a closed-form calculation. A claim with no terminal is a Gap. An assumption's terminal is `none`, and its display word is *assumption* (added with P2.5a: the claim is a Gap until its owner records it, and Assumed after — its status says which). Prose prefers the display words to the bare noun (§6) | from P2.5a: `Claim.terminal` ∈ `models.Terminal` closed_form, solver (→ simulation), datasheet, measurement, human (→ expert judgment), none (→ assumption); `""` by kind (`claims.terminal_of`); `TERMINALS_BY_KIND`; the words in `report.HUMAN["terminal"]` — every automated claim prints *automated* until P2.5b judges a declared terminal; `ClaimKind` measurable (= model-computed, not measurement, §6) / physical / assumption | terminal refuser · refusal source · *ends in* · external tool (as a terminal) · *measurable* (as a claim kind) |
| **authority** | §1 "remain with a named human or institution"; §6.3 "decisions by named human or institutional authorities" | the named person or institution an expert-judgment claim stays with. Only the authority settles it; until then it reads Assumed under the authority's name | from P2.5a: `Claim.authority` (a nominee), `claim physical <id> assume\|pass\|fail --authority`, `AttributionRecord(role="authority")`, `ClaimCause.NO_AUTHORITY`/`AUTHORITY_UNATTRIBUTED`/`AWAITING_JUDGMENT`/`JUDGED`/`JUDGMENT_MOVED`/`JUDGED_FAIL`; the authority's git identity must be theirs. Not `PhysicalResult.who`: that is **recorded by** | signer · approver · *owner* (that is Assumed's) |
| **recorded by** | not a paper word: the paper names no one for a physical result, and *authority* is kept for judgments that cannot be delegated | who entered a physical result: derived from the git identity, never typed (D-12). It attributes the result; it does not make the recorder an authority | `PhysicalResult.who` — from P2.5a `vcs.ident`, never typed (`--who` refused: `cli.REFUSED_FLAGS`); `PhysicalResult.channel`; `report.recorded_words`, `HUMAN["recorded"]` | attested by · *authority* (that is expert judgment's) |
| **pack** | §8 "domain-specific packs of evaluators" | a directory of evaluators, controls and references for one domain | `packs/<name>/`, `pack.json`, `PackManifest`, CLI `packs` | *plugin* (that is the Claude Code plugin) |
| **verdict** | §6.2 "v_i = (o_i, ρ_i), where o_i is the outcome and ρ_i is a hash of the registered inputs" | one evaluator run: outcome, read-set hash, a one-line detail, evidence | `Verdict`, `Verdict.render()`, `.nopekit/verdicts/<gate>/<rho16>-<out8>.json`; `Verdict.measured` | verdict sentence (that is the readiness sentence) · *result* · *finding* · *measured* (for a computed value: say *value*) |
| **outcome** | §6.2 "o_i is the outcome" | **pass**, **fail**, **skipped** or **errored** — nothing else, and only of an evaluator run. A crash is *errored*, never *failed*. A diagnostic or a qualification is not an outcome and prints its own word (§6) | `Verdict.outcome` pass/fail/skipped/error; tags `ok  ` `FAIL` `skip` `ERR ` (the same tags also mark `doctor` and `pack validate` rows, which are not outcomes); site PASS/FAIL/SKIPPED/ERRORED | refuted · flipped · *ok* · *rejected* (a candidate's, §4) · *refuse(s/d)* (of an evaluator) · *crashed* · *killed* |
| **read set** | §6.2 "the parameters, intermediate results, external files, and evaluator version it consumed" | everything one verdict read | `verdicts.Reads`, `GateTrace`, `ParamTrace`, `read_set`; `opaque` = a read the tracer could not see | *trace* (a PCB's is fine) · inputs hash · *dependencies* |
| **read-set hash (ρ)** | Fig. 9 "read-set hash ρ" | names a verdict's read set; an unchanged ρ means the verdict still applies | `Verdict.rho`, `verdicts.rho()`, `rho16` | content address · cache key |
| **input** | Table 1 "the current registered inputs" | a registered thing an evaluator may read: a parameter, sketch, datasheet, measurement | `InputArtifact`, `inputs/`, CLI `ingest`, `inputs`, `extract`; help text "list ingested evidence" | *evidence* (for an ingested file) |
| **sourced from** | not a paper word: §1's *grounded* names a system whose outputs evaluators can independently reject, so a link from an input cannot borrow it | the link from a registered input to a value or a claim extracted from it | `Param.grounded_by`, `--grounds`; `why` "GROUNDED BY (n)"; site field "Grounded by"; `extract` "grounds: …" | *grounded by* · *grounds* (both for this link) |
| **evidence** | §3 "produced evidence for that claim" | what a verdict produced to support itself: files, logs, photos | `Verdict.evidence`, `PhysicalResult.evidence`, `Decision.evidence` | proof |
| **article** | Table 1 "a physical article"; Fig. 4 "until a new article is printed"; Fig. 7 "consumes an article" | one built physical object, named by the hash of what it was built from | from P2.5a: `PhysicalResult.article` (`verdicts.article_of`: in P2.5a the design at recording, `source: "design"`; P2.5b's `export` records build-time articles), printed as its first 12 hex; `ClaimCause.ARTICLE_MOVED`, `claims.rebuild` | real object · real part · built object · real device · physical artifact · *device* · *prototype* · *hardware* (as a noun) |
| **physical result** | not a paper word outside its studies; §3's "physical outcomes" names the terminal, and *outcome* is a verdict's o_i | a physical evaluator's verdict on one article, with who recorded it | `PhysicalResult`, `results/<claim>.json` (sealed and chained from P2.5a: `store.append_signed`), `ClaimStatus.VERIFIED`/`REFUTED`, `ClaimCause.ON_ARTICLE`/`PHYSICAL_PASS`/`PHYSICAL_FAIL`; the report's "Checked on an article" rows | hardware result · real-world result · ok-hw · *confirmed* (in hardware) |
| **contradiction** | not a paper word outside its studies | a physical result that fails a claim an automated evaluator had Checked; it goes on that evaluator's track record | from P2.5a: `PhysicalResult.contradicts` (sealed into the fail: each covering evaluator whose pass counted — qualified, current — on the check-in batch as this row's rewording), `claims.contradicted_by`, `ClaimCause.CONTRADICTION`, `verdicts.Contradiction`; the page's "contradict" title moved to `phrases.disagree` | — |

## 2. Qualification

| Term | Paper | Means | Code today | Never say |
|---|---|---|---|---|
| **qualification** · **qualified** | §6.3 "an evaluator must demonstrate that it can fail. It should pass a known-good control and reject a known-bad control, with both controls retained and rerunnable" | qualified at its current version: its known-good control passes and its known-bad control fails, which is §6.3's rule. An evaluator the generator wrote in a groundspace must also fail every conclusive mutation: that is the walkthrough's addition, and §6.3 counts mutation toward confidence ("controls, mutation-derived failures, independent review…"), not toward the rule. Until qualified, its verdicts do not count. Rerunning a control because its inputs moved **re-qualifies** the evaluator | from P2.3: `verdicts._qualification` (the one judge), `QualificationFacts`, the token in `Verdict.unqualified`, the words in `report.HUMAN["qualification"]`; the line `<id> : known-good pass · known-bad fail · mutation n/m fail → qualified`; identifiers keep their names until the rename pass: `verdicts.Admission`, `admission()`, states admitted/pending/not-admitted/undemonstrated, record values `"paired"`/`"reject-only"`/`"no"`, `packs.demonstrate`, CLI `gate selftest` | admitted · admission · undemonstrated · selftest passed · re-verify · re-verifies · re-verified · re-verification · *demonstrated* |
| **known-bad shown** | not a paper word: §6.3's rule has two halves, and this is the one today's code enforces | an evaluator whose known-bad control fails at its current version and whose known-good control has not been run: every project gate on `0474f5c`, and from P2 any project evaluator with no known-good control. It is not qualified: its pass does not count, so its claim reads Gap, reason `unqualified: known-good not run` | from P2.3: a control entry with `good.outcome` `"not-run"`, `admitted: "reject-only"`, the token `known-good:not-run` | *qualified* (for this) |
| **known-good control** | §6.3 "pass a known-good control" | a retained input the evaluator must pass | packs `selftest/baseline.json`; bracket `selftest/known_good.py`; from P2.3 `NegativeControl.good` (a declared fixture: cad-solid's and sourcing's, on the `ctx.extra` channel), the entry's `good` | positive control · known-good input · *baseline* |
| **known-bad control** | §6.3 "reject a known-bad control" | a retained input the evaluator must fail: the design with its protective element removed | `NegativeControl`, `GateSpec.negative_control`, `selftest/bad_configs.py`; METHOD "falsification control"; the JSON keys `fired`, `broken` (P2.1-D12; the words left the screens in P2.3) | negative control · falsification control · bad fixture · known-bad input · *fire · fires · fired* (of a control) · *BROKEN* (for unqualified) |
| **mutation** | §6.3 "mutation-derived failures" | a small deliberate change to the known-good control, made in process on the parameters its run read, that the evaluator must fail. *Conclusive* when the change reaches the evaluator's read set and the run passes or fails; an inconclusive mutation counts neither way and is never reported as a fail | from P2.3: `gates.mutation_walk`, `MutationPass`, `MUTATION_MARGIN` (15% past the evaluator's own limit), the entry's `mutation`; walked for every evaluator not from the bundled packs | mutant · *flip* · *perturbation* · *killed* · *survived* |
| **non-vacuity** | §6.3 "It establishes non-vacuity, not that the evaluator is verified … or validated" | what qualification shows: the evaluator can fail. Nothing more | — | verified · validated · verify · validate · validation (see below) |
| **operating context** · **track record** | §6.3 "attached to the evaluator and its operating context"; §8 "calibrated only for the original context" | the input range an evaluator was qualified on, and its contradictions within it. Outside the range a **pass** does not count — its claim reads Assumed (with an owner) or Gap — but a **fail** still does: the claim stays Failing, because a result never loses its power to fail (R-3) | from P2.4: `GateSpec.operating_context`, `@gate(operating_context=…)`, `gates.context_breach`, the token `context:outside\|…` in `Verdict.unqualified` (`models.CONTEXT_OUTSIDE`), `ClaimCause.OUTSIDE_CONTEXT` / `FALLBACK`, `Claim.fallback`, the words in `report.HUMAN["context"]`; from P2.5a the track record: `verdicts.track_record`, `Resolution.track`, keyed by the evaluator's code digest, `gate show`'s `track record:` row | calibration range · validity domain · *envelope* |

**Verified and validated** are §6.3's verification-and-validation words for an evaluator:
*verified*, it correctly implements its predicate; *validated*, the predicate actually
supports the claim. Nothing in this tool earns either yet — they accrue from independent
review, comparison with established tools and physical outcomes — so no human channel
uses them, of an evaluator, a claim, a run or a design. Today the code uses them the other
way round: `VERIFIED` is a physical pass, `UNVERIFIED` awaits an article, the report heads
its passes "machine-verified", a rerun control is "re-verified", and README and the plugin
description promise "what has been verified" and "a validated physical thing". Their
spellings are in non-vacuity's *Never say*; definitional sentences like this one are
allowlisted; and neither word is a term, so neither is in `report.HUMAN`. `pack validate`
is a command name and stays (§8); its help text says it checks a pack's layout.

## 3. Claim statuses

Table 1's seven, one word each, plus **Open**. A claim's status composes its evaluators'
verdicts (D-01, landing in P2), first match wins: **Failing** if a qualified evaluator
failed, or one passed at a value that does not meet the claim's acceptance condition (P2.4);
otherwise **Skipped** if any skipped or errored; otherwise **Gap** if none is
qualified, any is unqualified, or a pass lies outside its evaluator's operating context
with no owned fallback (P2.4); otherwise **Open** if any is unrun; otherwise **Stale** if
any pass's read set has moved; otherwise **Assumed** if a pass outside its evaluator's
operating context is carried by an owned fallback (P2.4); otherwise **Checked**. A physical
evaluator with no result reads **Pending build** where an automated one would read Open;
an assumption, or an expert-judgment claim its authority has not settled, reads **Assumed**
— or **Gap** with no owner. Controls qualify automated evaluators; a physical result stands on its article and
who recorded it, and an expert judgment on its authority. So a passing evaluator never
hides one that skipped, errored, is unqualified or is unrun. On `0474f5c`, `claims.resolve_status` differs in four places, each a P2 change under
R-8's oracle: an error reads FAIL (rung 4); an unqualified evaluator reads FAIL; pass beside
skip reads PASS; pass beside unrun reads PASS (S-03).

| Status | Paper (Table 1 unless noted) | Means | Code today | Never say |
|---|---|---|---|---|
| **Checked** | "A qualified evaluator passed for the current registered inputs"; §3 "'Checked' does not mean 'true.'" | every evaluator of the claim is qualified and passed for the current registered inputs, inside its operating context, and every value compared with the claim's acceptance condition meets it (P2.4); a physical one on an article built from them | `PASS`, `VERIFIED`; tags `ok`, `ok-hw`; report "What is PROVEN (machine-verified, current)", "**Confirmed in hardware:**"; site PROVEN, VERIFIED, "the only kind a machine proves" | proven · prove · proves · proved · machine-verified · ok-hw · *confirmed* · *pass* (an outcome) · *green* |
| **Failing** | "An evaluator rejected the current candidate" | a qualified evaluator failed the current candidate, or a physical result failed, or an evaluator's value does not meet the claim's acceptance condition (P2.4). An invalidated fail stays Failing (D-08), and a physical fail keeps that power across every change (R-3) | `FAIL`, `REFUTED`; tags `FAIL`, `REFUT`; "refuted in hardware" | refuted · *broken* · *red* |
| **Stale** | "Previously checked evidence no longer matches current inputs" | a pass whose read set has changed since, including a physical pass on an article built from older inputs: its reason says a new article is needed, not a rerun; or a pass whose **prerequisite** is invalidated or unrun — the change travels F's prerequisite edge, and the reason names the prerequisite (P2.2, on the check-in list). Nothing is checked *now* | `STALE`; `verdicts.freshness` → `Stale`, `Unknown`; `stale_gates`; site "≈ not current" | not current · out of date · outdated · expired |
| **Assumed** | "Accepted provisionally with a named reason and owner" | carried on the record, needing both a reason and an owner; with no owner the claim reads Gap. Also a claim carried, under an owned fallback, outside an evaluator's operating context (P2.4). An expert-judgment claim reads Assumed under its authority's name until the authority settles it. Unresolved (below) | `ASSERTED`, `ClaimKind.ASSUMPTION`, tag `assum`; report "Standing constraints … Carried on faith"; site hint "standing assumption, carried in the open and unevidenced"; no owner field | asserted · standing constraint · on faith · unevidenced |
| **Pending build** | "Requires a physical article or other unavailable evaluation" | waits on an article: a physical evaluator with no result yet | `UNVERIFIED`, `ClaimKind.PHYSICAL`, tag `phys`; report "What is NOT verified", "needs the real object" | unverified · not verified · untested · phys · *physical* (as a status) |
| **Gap** | "No suitable evaluator is currently available"; §7 "a claim with no qualified evaluator remains a gap" | no evaluator, none qualified, or one unqualified beside others; or one outside its operating context with no owned fallback (P2.4); or an assumption nobody owns. The reason line says which | `UNCLAIMED`, `Need`, `needs/`, tag `gap`, "capability gap", site NO GATE; an unqualified evaluator reads `FAIL` today | capability gap · unclaimed · *need* (noun) |
| **Skipped** | "Skipped / errored: Evaluation did not produce a usable verdict" | an evaluator of the claim skipped (tool missing, or it skipped itself on its input) or errored (crashed), or was not run because a **prerequisite** is not established (P2.2), and none failed, even beside a pass. The reason line leads with which, errored first — a crashed prerequisite's included | `BLOCKED` (all skipped); an error reads `FAIL` today (`resolve_status` rung 4), pass beside skip reads `PASS`; `Verdict.skipped`, `skip_reason`, `error`, `blocked_by`, `blocked_kind`; `ClaimCause.PREREQUISITE`, `PREREQUISITE_ERRORED`; site BLOCKED | blocked · *errored* (the reason, not the status) · *unknown* |
| **Open** | §6.1 "Stopping evaluation does not resolve an unrun claim. It leaves the claim open" | a qualified evaluator of the claim is unrun on the current inputs — never run, or sequencing stopped before it — and nothing above applies | `PENDING`, tag `unrun`, "never run", site NOT RUN | pending (Pending build's word) · not run · never run |

**Skipped, for the paper's "Skipped / errored".** One word, because at the claim it is one
fact: no usable verdict. *Skipped*, because the common case is honest and dull — an optional
tool absent on this machine — and calling that an error would teach readers to ignore the
word that must mean a crash. Invariant 2 must still hold where only a status word or a
count is printed, so errored is louder in four places, not one: the reason line leads with
the outcome — `errored: <first line>` or `skipped: requires trimesh (not importable)` — and
an errored row sorts above every skipped row and takes Failing's tone; every count,
sentence and heading splits it out, `2 skipped (1 errored)`, errored first; and a claim
Skipped by an error stays a JUnit `<error>`, critical or not, as a crash is today.
*Rejected:* *errored* for both (a missing library would read as a crash); the compound (two
words for one status); a crash as Failing, today's behaviour (Failing says an evaluator
failed the candidate; a crash failed nothing, so the design takes the blame for a broken
evaluator).

**Open, which Table 1 lacks.** §6.1 and Fig. 7 depend on it ("unrun claims remain open"),
and the code has it (`PENDING`). As Skipped it would say a tool is missing when none is; as
Gap, that no evaluator exists when one does. *Pending* goes to Table 1's *Pending build*.
An evaluator not yet run on the current inputs is *unrun*, the paper's own word (§6.1,
Fig. 7); its claim is **Open**.

**Resolved, unresolved.** A claim is **resolved** when Checked, and **unresolved**
otherwise — Assumed included: the paper's phrase is "unresolved assumptions" (§4, §6.4),
and §1 sets "checked properties" against "unresolved claims". Never *unsettled* or
*unproven*. Going ahead with an unresolved required claim is a recorded decision (§6.1:
stopping "makes the decision to proceed explicit"), never a status: the claim keeps its
status and the decision names it (from P2.5b: `export <milestone> --proceed --why …`,
typed in the person's own shell, sealed into the export record as `proceed`). **Partial** goes: under the composition above no Checked
claim has an evaluator that did not pass, so nothing is left to mark. Today's PARTIAL rows
(a pass beside a skip or an unrun evaluator) read Skipped or Open from P2.

## 4. The state and what acts on it

| Term | Paper | Means | Code today | Never say |
|---|---|---|---|---|
| **falsification graph** | §3 "F = (V, E), where nodes V include design parameters, claims, evaluators, evidence artifacts, and decisions; edges E record dependencies" | the graph a change travels through; the site draws it parameters → derived values → evaluators → claims | per-verdict read sets, `Param.derived_from` (implicit); `GateSpec.needs`, the prerequisite edges (explicit, P2.2) | refusal graph · dependency graph · *flow canvas* (a view of it) |
| **prerequisite** | §3 "edges E record dependencies"; §6.1's cascade (PLAN-v0.14 §1.1: "prerequisite edges are part of F") | an evaluator whose pass must be established — a pass, current — before another's verdict counts: a validity guard before the analyses it guards. Under one not established its dependent is not run and its claim reads Skipped; under one invalidated or unrun, Stale. Added in P2.2, on the check-in list | `GateSpec.needs`, `Verdict.blocked_by`, `blocked_kind`; `gate list --json` `needs`/`needed_by`; `gate show` "prerequisites", "prerequisite of"; `prerequisite failed: <root>` | *need* (Gap's) · *dependency* · *after* · *upstream* |
| **claim ledger** (short: **ledger**) | §6.4 "The ledger maps each claim to its status, evidence, provenance, evaluator, and unresolved assumptions" | the record files, `results/` and the verdict cache | `Ledger`, the record dirs, `.nopekit/verdicts/`; `.nopekit/ledger.json` is a generated index of it | state file · *database* |
| **readiness report** | not a paper word; §8's phrase is "ledger rendering" (reason below) | the ledger rendered for one reader: CLI `report`, `REPORT.md`, the site | `report.py`; `REPORT.md` at the root, an ignored output (P2.5b; was the tracked `docs/readiness.md`); `report --milestone <m>`, the copy an export's package carries | *verdict* (its first sentence is the readiness sentence) |
| **tradespace** | §7 "T = (P, V, φ, u) holds the parameters P, the values V of explored candidates and their dependencies, the known feasibility constraints φ, and the objective valuation u" | the explored candidates and why each won or lost | — ; the model's `Config` is one point, `Rejected` values the losers | executable tradespace · option space · trade study · *design space* |
| **grounded design state** | §7 "a triple S = (T, F, L)"; "the groundspace at one version" | tradespace, falsification graph and ledger at one revision | — ; a commit with its tracked verdict cache | *snapshot* |
| **groundspace** | §7 "the versioned sequence ⟨S0, S1, …, Sn⟩ … much as a repository holds a sequence of commits" | the git repository (or subdirectory) holding one design problem | "project": `init` "create the project layout", `ProjectMeta`, `.nopekit/project.json`, "no nopekit project found" | workspace · *project* (for the store; a human's project is fine) |
| **revision** (**rev**) | Fig. 7 "Candidate (revision k)" | one version Sn of the groundspace: one commit | git commits; `ProjectMeta.revision` is a free label ("v0.1") printed as if it were one; `Param.changed_in` | *version* (kept for evaluators and packs) · *iteration* |
| **preserve** | §7 "preserves every verdict whose read set is unaffected" | serve a verdict again because its ρ matches; it is not paid for twice | cache hit; `check` prints "cached"; `verdicts.Fresh` | cached · *reused* · *fresh* |
| **invalidation** · **invalidated** | §6.2 "the system marks stale the verdicts whose registered read sets changed" | the change-driven operation, and what it leaves: a verdict whose read set moved is *invalidated*, and a claim whose pass was invalidated reads Stale (§6) | `verdicts.freshness`, `resolve`; `Stale`, `Unknown`, `Never`; `status` "stale:" block; a Failing row's "(stale: …)" | expiry · *dirty* · *stale* (of a verdict) |
| **recovery** | §7 "recovers rejected candidates whose return conditions now hold" | a rejected candidate returns, its evidence attached | — | resurrection · revival · dormant |
| **sequencing** | §6.1 "running evaluators in increasing order of c_i/p_i" | evaluator order; c_i is execution cost, p_i the probability of a fail outcome on the current candidate | — ; `gates.plan`: each evaluator after its prerequisites, registration order otherwise (P2.2), `check --tier` the only stop | refusal scheduler · π\* · *scheduling* |
| **infeasible** | §6.4 "violates a declared constraint; returns if that constraint changes" | a rejection type | — ; `Rejected{value, why, evidence}` has no type | *impossible* · *invalid* |
| **dominated** | §6.4 "another feasible candidate is no worse on all declared objectives and better on at least one" | a rejection type; returns if the objectives or the competing candidates change | — | beaten · *worse* |
| **preferred against** | §6.4 "a human or institution chose another candidate under stated preferences"; §7 "names who supplied the preference, why, and when it should be reconsidered" | a rejection type; carries who, why and when to reconsider | — ; `decide --rejected VALUE\|WHY` records no who | taste · vetoed · overruled |
| **unevaluated** | §6.4 "insufficient evidence exists to compare it; returns when evaluation capacity becomes available" | a rejection type | — | parked |
| **return condition** | §6.4 "These are the return conditions for each rejection type" | the change that brings a rejected candidate back; for preferred against, when to reconsider | — | — |
| **decision** | §6.4 "It also records design decisions and the candidates that were rejected, including why they lost" | a design choice: what was chosen, each loser with its rejection type, who preferred, the return condition. One **decision record** per decision; `docs/decisions.md` renders them all, newest first | `Decision`, `decisions/`, CLI `decide`, `Param.changed_in`; `docs/decisions.md` headed "# Decision log"; `init` "generated readiness report and decision log"; METHOD "decision log" | ADR · decision log · *choice* · *decision* (for a milestone) |
| **milestone** | the paper's word is *decision*: §2.1 "a declared decision"; §2.3 "A concept review, a prototype decision, and a safety release may require different evidence" | a named spend — a print, a board order, a field test — and the claims it requires; Λ, t_f and readiness are each per milestone. Not a paper word (§6) | `Milestone`, `milestones/<name>.json`, `export <milestone>`, `report --milestone` (P2.5b); the groundspace's own implicit spend stays: `Claim.critical`, `BLOCKING_STATUSES`, `claims.blocking()`, "BLOCKING — … must not be spent against" | spend point · stage gate · *decision* · *release* |
| **required claim** | §2.1 "the claims that decision requires" | a claim a milestone needs Checked | `Milestone.requires`, `claims.required_ids`; without a milestone `Claim.critical` (required by the groundspace's own spend), `n_critical` | critical claim · blocker · blocking · unsettled · unproven |
| **not required** (by a milestone) | not a paper word: the complement of §2.1's required claims | a claim no milestone requires; still shown with its status, never hidden | `claim list` "(nice-to-have)"; report "non-critical"; site tag "non-blocking" | nice-to-have · non-critical · non-blocking |
| **ready** (for a milestone) | not a paper word; Fig. 1's phrase is "conditional acceptance for a declared decision" (reason below) | every required claim Checked. The report's first sentence, the **readiness sentence**, says so or names what is not. A candidate ready for a milestone is one that accepted throughput (A, §5) counts for it | `claims.unresolved(ledger, composed, milestone).ready` — the one predicate (P2.5b); `Summary.ready`; "is NOT ready", "clears every critical gate"; `report.py` "verdict sentence"; site READY / NOT READY; `export <m> --dry-run` "would write" | headline verdict · clean bill of health · *green* · *accepted* (as a status) |

**Ready and readiness report, which are not the paper's.** Both are the product's own words
and are binding: the walkthrough's `/ready <milestone>` and the chain `claims -> … ->
readiness report` in README, METHOD and CLAUDE.md. The paper's phrases were weighed and
rejected for a human channel: *conditional acceptance* reads as final to a reader who skips
the adjective, and *accepted* is already A's word for a rate (§5); *ledger rendering* names
the operation, not the page a person reads. *Ready* is the per-milestone predicate that
puts a candidate into A for that milestone, so the two never name different things.

## 5. Measures

| Term | Paper | Means | Code today | Never say |
|---|---|---|---|---|
| **Λ** (**Λ₀** before acceleration) | §2.1 "Λ = G/B_F"; §2.2 "Λ₀ = t_f/t_g" | generation rate over falsification capacity, per milestone; above 1 the work is falsification-limited | — | G/B_R |
| **generation rate** (G) | §2.1 "the rate at which a workflow could generate materially distinct candidates for a declared decision" | | — | proposal rate |
| **falsification capacity** (B_F) | §2.1 "the rate at which its evaluators could settle the claims that decision requires" | | — ; per-verdict latency and cost only | B_R · verification bandwidth |
| **generation time** (t_g) · **falsification time** (t_f) | §2.3 "t_f is … the time required to settle the claims necessary for a particular decision" | per milestone | — | *cycle time* (that is t_g + t_f) |
| **survival rate** (σ) | §2.1 "the fraction of evaluated candidates that pass" | | — | pass rate · *yield* |
| **accepted throughput** (A) | §2.1 "the rate at which candidates survive the falsification a decision requires" | A ≤ σ·min(G, B_F) | — | *throughput* (bare) |
| **latency** | §3 "elapsed time to obtain a result" | measured per run; declared ahead only for a physical evaluator, until an article measures it | `Verdict.duration_s`; `verdicts.record_obs`/`read_obs`; `Claim.expected_latency` (declared, a measurement's), `claims.latency` (a measurement's: measured from the newest export of an article to the newest result a person typed on it; an automated claim's is its run's — review of P2.5b) (P2.5b) | duration · run time · turnaround |
| **cost** | §3 "monetary and resource cost of running it" | | `Verdict.cpu_s` (resource part only); `ToolCandidate.cost` (install cost) | *price* · *effort* |
| **coverage** | §3 "the conditions or failure classes it can detect" | a property of an evaluator, not of a claim (P2.4 defers coverage S to P5 on this definition: per evaluator) | — ; `GateSpec.claims` is where an evaluator is filed, `settles` what it measures; `covering_verdicts` / "covering gate" mean *attached* | *covers* · *covering* (for an evaluator attached to a claim: say "the claim's evaluators") |
| **independence** | §3 "the extent to which its failure modes differ from those of the generator and other evaluators" | | — | diversity |
| **provenance** | §3 "the evaluator version, inputs, protocol, and evidence supporting its verdict" | where a verdict or a value came from. A value's *why* is its **rationale** (§7 "the rationale and conditions governing selection") | `code_digest`, `spine_digest`, `instruments_for`, `Verdict.rho`, `Verdict.evidence`; `Param.source`, `grounded_by`; `rationale` | lineage · audit trail · pedigree |
| **tier** | not a paper word: the paper describes latency on a continuous scale (Fig. 3), and a declared class is needed before any run has measured one | a declared latency class, 0–3 (seconds, minutes, hours, external), fixed before any run; latency and cost are measured | `Tier` INSTANT/BUILD/SOLVE/EXTERNAL, `check --tier`; also, in a second sense, pack disclosure levels (§6) | *level* · *stage* |

## 6. One word, two things

The tables already split the easy ones (*verdict*, *covering*, *baseline*, *blocked*,
*provenance*; *verified* has its own paragraph in §2). These need a sentence each.

- **decision.** The paper uses it for a declared spend (§2.1, §2.3, §5.3, Fig. 7) and for a
  recorded design choice (§6.4, §8). Here *decision* is the choice — the sense `Decision`,
  `decide` and METHOD rule 3 already carry — and the spend is a **milestone**. "Λ for this
  decision" and "decision D-4" must never share a word.
- **pending.** A claim never run (`PENDING`), an admission waiting for a rerun
  (`Admission.state == "pending"`), and Table 1's *Pending build*. Only the last survives;
  the first is **Open**, the second "re-qualifies on the next check run".
- **open.** A claim status only. `NeedStatus.OPEN` prints `(open)` beside a gap; that state
  becomes *identified*. An evaluator is never open: it is *unrun* (§6.1).
- **candidate.** `ToolCandidate` and the report's "Candidate tooling" become **tool
  options**; a **candidate** is a design configuration only.
- **check.** `nopekit check` is a verb and stays; one invocation of it is a **check run**
  ("last check run: never", "between check runs"), never a *sweep*. **Checked** is a status
  and nothing else: an evaluator that ran has *evaluated* its claim, whatever the outcome
  ("nothing was evaluated", "has never been evaluated", "no evaluator here can settle
  it"), because a scanner reads *checked* as the term, and a Failing claim called
  "checked" would pass it. doctor's "25 checks" are its **diagnostics**, `status`'s "6
  checks current" are **verdicts**, and *check* as a noun for an evaluator is never used.
- **reject.** The paper's evaluators *reject* (Table 1, §6.3) and its candidates are
  *rejected* with a type (§6.4) — but a dominated candidate was rejected with no evaluator
  failing it. The outcome is **fail** (its claim **Failing**); **rejected** belongs to the
  candidate, so a qualification line names each control's outcome in outcome words:
  `known-good pass · known-bad fail · mutation 4/4 fail → qualified`.
- **refuse.** For a boundary that declines to act: the registry refuses an evaluator with no
  known-bad control, an export refuses to emit, a permission rule refuses an edit. An
  evaluator never refuses; it fails a candidate.
- **fail.** An outcome, and nothing else. A diagnostic that finds something wrong prints
  **problem** (`doctor`, `pack validate`); a qualification that does not hold prints
  **unqualified** (`gate show`, `gate selftest`). Today the same `FAIL` tag marks all
  three, and `gate selftest --help` "fails any gate that cannot fail" uses two senses in
  one sentence.
- **stale.** The claim status. A verdict whose read set moved is **invalidated** (§6.2's
  operation, Table 1's "no longer matches"), so "an invalidated fail stays Failing", and
  `status`'s "stale:" block and a Failing row's "(stale: …)" suffix say *invalidated*.
- **measurement.** A terminal, and only the physical or measured-data one (a caliper, a
  load test, a datasheet's measured curve). `ClaimKind.MEASURABLE` means the opposite —
  computed from the model — and the report's "Measured" column holds a computed value; the
  kind's human words are the terminal's (closed-form calculation, simulation) or
  **automated**, and the column's is **value**.
- **authority.** The person or institution an expert-judgment claim stays with (§6.3).
  Whoever types a physical result in is **recorded by**, not an authority: logging a fit
  check is not a judgment the paper says cannot be delegated.
- **grounded.** The paper's (§1): a system whose outputs evaluators can independently
  reject. Today "Grounded by <input>" names the link from an input to a value; that link is
  **sourced from**.
- **tier.** A declared latency class (§5) and nothing else. The pack disclosure levels that
  CLAUDE.md and `pack show --help` call tiers 1–3 are the pack's **summary** (`pack.json`),
  **guide** (`PACK.md`) and **references**; a subset of the test suite is a **subset**.
- **terminal.** Also the console ("On the terminal" headings in
  `packs/openmodelica/references/failure-modes.md`) and an electrical terminal
  (`packs/cad-solid`). As a term it is never scanned, so prose prefers the display words —
  closed-form calculation, simulation, datasheet, measurement, expert judgment — to the
  bare noun.

## 7. Human channels

| Channel | Comes from |
|---|---|
| CLI output of `status`, `check`, `report`, `why`, `ask`, `doctor`, `export`; and of `claim`, `gate`, `gap`, `inputs`, `ingest`, `extract`, `decide`, `packs`, `site`, `init`; every `--help` and error message | `cli.py`, `report.py`, `Verdict.render`, `NopekitError` text |
| report files | `report --write` → `REPORT.md`; an export's package `out/<milestone>/REPORT.md` and its test card; `docs/decisions.md`; the `message` text of `check --junit` |
| site text | static strings in `site_template/` (`lib/format.js`, `lib/panels.js`, `index.html`) and every sentence `site.py` writes into `state.json` |
| skill and doctrine text an agent quotes | `skills/*/SKILL.md`, `METHOD.md`, `README.md`, `CLAUDE.md`, `packs/*/PACK.md`, `packs/*/references/*.md` |
| plugin and marketplace descriptions | the `description` fields of `.claude-plugin/plugin.json` and `.claude-plugin/marketplace.json`: what a user and Claude Code read before installing |
| what an evaluator writes | each verdict's `detail`, `skip_reason` and `error` |
| slash-command output | `.claude-plugin` commands (none yet) |
| hook output | the Stop and PostToolUse status block (none yet) |

`CLAUDE.md` is a channel because an agent working in the repo quotes it and it ships in the
bundle. Not human channels: JSON keys and enum values (`--json`, `state.json`, record
files), identifiers, file and directory names, comments, test names, and the developer
documents in `docs/`, this file included. They move only in the rename pass. In code, every
status and outcome word comes from one table (`report.HUMAN`, planned); the site never
owns a word.

**Proposed test** (not implemented): `tests/test_vocabulary.py`, `HumanChannelsSpeakTheGlossary`.

- **This file is the list.** The test parses the *Term* and *Never say* columns of §1–§5
  (italic terms are not scanned), and `report.HUMAN` must equal the terms, so a new row is
  enforced with no test edit. *Rejected:* a list in the test — two places to forget.
- **Every channel, rendered**, on the bracket (failing, gap, pending build, assumed) and a
  synthetic groundspace reaching every status (stale, open, a gap from an unqualified
  evaluator, skipped by a missing tool and by an error), plus the static texts above and
  the two plugin descriptions.
- **Prose only.** Code, command lines, dotted evaluator ids, paths and JSON are stripped; a
  term inside a glossary term (*pending* in *Pending build*) is masked; matching is
  case-insensitive on word boundaries; definitional *verified* / *validated* is allowlisted.
- **Controls.** A forbidden word planted in a rendered status block turns it red; a
  hand-written block in glossary terms passes; a floor on channels and bytes scanned keeps a
  scanner that reads nothing from passing (as in `NoLeakedProvenance`).
- **A ratchet.** Every channel violates today, so it lands with an allowlist of today's hits
  that may only shrink: CI stays green while the rename pass empties it, and a new hit is red.

## 8. Rename table for the nopekit pass

Not applied now. CLI subcommands stay: removing one a pack, skill or doc references is an
ask-list item, so the pass adds aliases and changes output text, never removes a command.
The state directory is not in this table: it moves in P3 (PLAN-v0.14 §2.1).

| Today | Glossary term | Proposed |
|---|---|---|
| `GateSpec`, `gates.py`, `Verdict.gate`, `Claim.gates`, `Param.gates` | evaluator | `EvaluatorSpec`, `evaluators.py`, `.evaluator`, `.evaluators` (record keys read through a shim) |
| `gates/` (project and pack) | evaluator | `evaluators/` |
| CLI `gate list\|show\|selftest`; `check` | evaluator, qualification | keep; add `evaluator list\|show\|qualify` aliases; `check` stays (a verb) |
| `NegativeControl`, `GateSpec.negative_control`, `selftest/bad_configs.py` | known-bad control | `KnownBadControl`, `.known_bad`, `controls/known_bad.py` |
| pack `selftest/baseline.json`, project `selftest/known_good.py` | known-good control | `controls/known_good.json` / `.py`, and a `known_good` field beside `known_bad` |
| `verdicts.Admission`, `admission()`, admitted / not-admitted / undemonstrated, `"reject-only"`, `not admitted:` | qualification; known-bad shown | `Qualification`, `qualification()`, qualified / unqualified / no-controls; `"reject-only"` → `"known-bad-shown"`, which is **not** qualified: its claim reads Gap, reason `unqualified: known-good not run`. `qualified` names only both controls passing as required (plus the mutation pass for a project evaluator) |
| `ClaimStatus` PASS, VERIFIED · FAIL, REFUTED · STALE · ASSERTED · UNVERIFIED · UNCLAIMED · BLOCKED (+ errored) · PENDING | the statuses | CHECKED · FAILING · STALE · ASSUMED · PENDING_BUILD · GAP · SKIPPED · OPEN — machine or physical is the terminal's business, not the status's |
| `BLOCKING_STATUSES`, `claims.blocking()`, `Claim.critical` | unresolved, required claim | `UNRESOLVED` (every status but CHECKED — Assumed and Pending build included, unlike today's set), `claims.unresolved(milestone)`, `milestones/<name>.json` listing required claims |
| `ClaimKind` measurable / physical / assumption | terminal; Assumed | `Claim.terminal`; Assumed through `assumed: {reason, owner}` |
| `PhysicalResult.who`, `claim physical --who` | recorded by | `who` stays, derived from the git identity and never a flag (D-12); `--who` is removed in the same change that updates `skills/nopekit/SKILL.md`, which tells agents to pass it (an ask-list item) |
| — | authority | `--authority`, new (D-12): the person or institution an expert-judgment claim's terminal names. Not a rename of `--who` |
| — | article | the article's hash on every physical result |
| `Need`, `needs/`, `NeedStatus.OPEN`, `ToolCandidate` | gap; tool option | `Gap`, `gaps/`, `identified`, `ToolOption`; CLI `gap` stays |
| `Param.grounded_by`, `--grounds` | sourced from | `sourced_from`, `--from` (alias kept) |
| `Rejected{value, why, evidence}` | rejected candidate | `Rejection` + `type`, `returns_when`, `by` |
| `Decision`, `decisions/`, `decide` | decision | keep; add `by`, `reconsider_when` |
| — (P2.5b: `milestones/`, `Milestone`, `export <milestone>`) | milestone | `milestones/`; `export <milestone>` — landed in P2.5b |
| `verdicts.freshness`, `Fresh`, `Unknown`, `Never`; `GateTrace`, `Reads` | invalidation, preserve, read set | `invalidation`, `Preserved`; `Unknown` → invalidated with reason `unregistered read` (its claim reads Stale); `Never` → unrun (its claim reads Open); `ReadSet` |
| `Verdict.rho`, `duration_s`, `cpu_s` | ρ, latency, cost | keys stay; rendered as ρ, latency, cost |
| `project.json`, `ProjectMeta`; `ProjectMeta.revision` | groundspace; revision | `groundspace.json`, `GroundspaceMeta`; `label` (a revision is a commit) |
| `report.SECTION_PROVEN`, `_PROVEN_QUALIFIER`, `STATUS_TAG`, `_STATUS_PHRASE`; `models._RENDER_TAG`; and the raw enum values interpolated past them: `report.py` `*({status}, {flag})*` (renders "*(fail, critical)*" on the bracket), `*({need.status})*` ("*(open)*"), `cli.py` `claim list` `[{claim.kind}]` (measurable/physical/assumption) | statuses, outcome, not required, terminal | text from `report.HUMAN`, every interpolation site included; `SECTION_PROVEN` keeps its name, as its comment requires, once METHOD changes |
| `site_template/lib/format.js` labels, `panels.js` counts; doctor's "checks" | statuses; diagnostics | Table 1 words and Open; "diagnostics" |

## 9. Conflicts

**METHOD.md** is on the ask-list. Every row below is proposed as one unified diff for its
owner and is not applied. Each *Proposed* cell has been read against every *Never say*
column above.

| Where | Says today | Proposed |
|---|---|---|
| intro, §0 | `claims -> gates -> packs -> readiness report`; "what is proven"; "proven on a real device" | `claims -> evaluators -> packs -> readiness report`; "what is checked"; "tested on built articles" |
| rule 3 | "carries its provenance"; "which gate protects it"; "decision log" | "its rationale and provenance — especially what lost"; "evaluator"; "every decision record" |
| rule 4 | "Validators are gates, not loggers"; "It must **refuse**"; "Put the refusal at the boundary"; "A logger is not a gate." | "Evaluators fail candidates; loggers report"; "A fail must stop something"; "Make the step that spends money refuse to run on a fail" (a boundary refusing); "A logger is not an evaluator." |
| rule 5 | "falsification control", "negative control", one control; "A gate that cannot fail proves nothing" | known-bad and known-good controls; **qualified** = both, as §6.3 states, plus the mutation pass for an evaluator written in the project; "settles nothing"; qualification is non-vacuity, not *verified* or *validated* |
| rule 9 | PROVEN rows; "It is unverified in physical hardware"; "physical, blocked, stale or assumed"; three statuses; "headline verdict"; "a validated design" | Checked rows; "Nothing has been built: its physical claims are pending build"; the seven words; four things (adds open, folds errored into Skipped by reason and counts it apart); "readiness sentence"; "Checked does not mean true" |
| rules 6, 7, 10; Intake; Growing | "prove that several independent coordinate frames"; "the check exists"; "part of the gate"; "tier-0 gates"; "If validating a change … does not get validated"; "install size, run time"; "evidence, not just conversation"; "grounded in hand sketches", "which parameters and claims that grounds"; "capability gap", "no gate", "negative control"; "a smoke test that proves the install works" | "show that"; evaluator throughout; "evaluating … evaluated"; "install size, latency"; "inputs, not just conversation"; "start from hand sketches", "it is the source of"; "Gap"; both controls; "shows the install works" |

**CLAUDE.md** (a channel, §7; proposed wording, not applied):

| # | Says today | Proposed |
|---|---|---|
| intro | "takes a design from a sketch to a validated physical thing"; `claims -> gates -> packs -> readiness report` | "… to a physical thing, with an honest account of what is checked"; `claims -> evaluators -> packs -> readiness report` |
| preamble | "refuses to claim anything it has not proven" | "… it has not checked" |
| 1 | "A skipped gate is never a pass. A missing solver means the claim is BLOCKED." | "A skipped evaluator is never a pass. A missing solver leaves the claim **Skipped**." |
| 2 | "An errored gate is never a pass. A crash proves nothing, and reads louder than a missing tool." | "An errored evaluator is never a pass. A crash settles nothing, and reads louder than a missing tool: the claim reads **Skipped**, its reason starts `errored:`, it sorts above every skipped row and takes Failing's tone, every count splits it out (`N skipped (k errored)`), and it is a JUnit `<error>`, critical or not." A behaviour change (P2): today it reads FAIL |
| 3 | "refuses a gate with no negative control. A validator that cannot be shown to fail is a logger"; "launder assumption into proof" | "refuses an evaluator with no known-bad control. An evaluator that cannot be shown to fail is a logger"; "launder assumption into apparent evidence" |
| 4 | "never puts an unrun or skipped gate under PROVEN … marked PARTIAL" | "never puts a claim under **Checked** while any of its evaluators is unrun, skipped, errored or unqualified; that claim reads under its own status, naming the evaluator and the reason." A behaviour change (P2, D-01): partial goes |
| 5, 6 | "`selftest/baseline.json`"; "ships a baseline its gates all pass" | "its known-good control"; "ships a known-good control its evaluators all pass" |
| 7 | "its PASS reads STALE"; "three claims read PROVEN" | "its pass reads Stale"; "read Checked" |
| 9 | "admitted"; "negative control"; "undemonstrated"; "the reject half"; "PROVEN rows" | until P2: "known-bad shown", never "qualified"; from P2: "qualified" (both controls, and the mutation pass for a project evaluator); "known-bad control"; "not yet qualified at this version"; "the known-bad half"; "Checked rows" |
| house rule | "Every constant carries its provenance — why this value" | "its rationale and provenance — why this value, and where it came from" |
| Run it | "fast tier, … gates each iteration" | "fast subset, … run it each iteration" (applied) |
| Adding a pack | "`pack.json` (tier 1 …), `PACK.md` (tier 2 …), `references/` (tier 3 …)"; "gates that each declare a negative control" | "summary", "guide", "references"; "evaluators that each declare a known-bad control" |

**Report and output vocabulary today**, on the bracket:

| Output | Says today | Becomes |
|---|---|---|
| `status` counts | `claims 7 — ok 3 \| FAIL 1 \| gap 1 \| phys 1 \| assum 1`; "stale: none (6 checks current)" | `7 claims · 3 checked · 1 failing · 1 gap · 1 pending build · 1 assumed` (Skipped, when present, as `2 skipped (1 errored)`); "invalidated: none (6 verdicts current)" |
| `status`, other lines | "last check: never"; "N control(s) pending — inputs moved (…); the next check re-verifies"; a Failing row's "(stale: …)" | "last check run: never"; "N evaluator(s) to re-qualify — control inputs moved (…); the next check run re-qualifies them"; "(invalidated: …)" |
| `check` | "6 gates: 0 executed, 6 cached — 5 ok, 1 FAIL"; "controls: N executed, N cached, N re-verified"; "— N of them stale"; "outside this sweep"; "nothing was checked"; "BLOCKING — 2 critical claim(s) must not be spent against" | "6 evaluators: 0 run, 6 preserved — 5 pass, 1 fail"; "controls: N run, N preserved, N re-qualified"; "— N of them invalidated"; "outside this check run"; "nothing was evaluated"; "Unresolved — 2 required claims" |
| readiness sentence | "machine-verified against the current model"; "1 with no gate at all"; "blocked on missing tooling"; "never run"; "It is unverified in physical hardware: 1 claim needs a real object"; "has never been gated" | "checked"; "1 gap"; "skipped" (errored counted apart); "open"; "Pending build: 1 claim needs an article"; "has never been evaluated" |
| report headings and rows | What is PROVEN (machine-verified, current) · What is NOT verified · Open gaps · Standing constraints · Failing / blocked; "**Confirmed in hardware:**", "has been confirmed on a built object"; column "Measured" (applied in P2.4: "Value", and "Gate" → "Evaluator"), "measured 0.7 mm vs limit"; "Every measurable claim has at least one gate"; "*(non-critical)*" | Checked · Pending build · Gaps · Assumed · Failing, stale, skipped or open; "Checked on an article:", "has been checked on an article"; column "Value", "0.7 mm against a limit of …"; "Every automated claim has at least one evaluator"; "*(not required)*" |
| gaps | "Candidate tooling: none proposed yet"; `N-C7 first mode (open)` | "Tool options: none proposed yet"; `(identified)` |
| `why` | `GATES (1)`; `GROUNDED BY (n)`; "this value is asserted, not evidenced" | `EVALUATORS (1)`; `SOURCED FROM (n)`; "no input is this value's source" |
| `extract`, `claim list`, `init` | "grounds: …"; "its parameters were not checked"; "(nice-to-have)"; "generated readiness report and decision log" | "source of: …"; "its parameters were not evaluated"; "(not required)"; "generated readiness report and decision records" |
| `gate selftest` | "correctly failed on …"; "6 fired, 0 BROKEN, 0 skipped (tooling)"; help "fails any gate that cannot fail" | "fail on its known-bad control, as required"; "6 fail as required, 0 pass (unqualified), 0 skipped"; help "exits 1 when an evaluator passes its own known-bad control" |
| `gate show` | "last selftest: [ok  ] fired at this version"; "last selftest: pending — control inputs moved; the next check re-verifies"; "[FAIL] PASSED its own known-bad"; "[FAIL] not admitted" | "known-bad shown at this version" (from P2: "qualified at this version"); "due to re-qualify — control inputs moved; the next check run re-qualifies"; "unqualified: passed its own known-bad control"; "unqualified" |
| `doctor`; `pack validate`; `claim physical` | "25 checks — 0 failing"; rows `[FAIL]` / `[ok  ]`; "no control is waiting to be re-verified"; "stale between checks"; `pack validate` "[FAIL ] <problem>"; `[ok-hw] C5 … (unattributed …)` | "25 diagnostics — 0 problems"; rows `[problem]` / `[ok]`; "no evaluator is waiting to re-qualify"; "invalidated between check runs"; "[problem] <problem>"; Checked, recorded by, with the article's hash |
| site | PROVEN · FAILING · NO GATE · BLOCKED · NOT RUN · UNVERIFIED · VERIFIED · REFUTED · ASSUMED; "capability gap"; group "Measurable — A gate settles these from the model"; "the only kind a machine proves"; PASS hint "the gate ran and the measurement holds"; FAIL hint "the gate ran and refused"; Assumed hint "carried in the open and unevidenced"; field "Grounded by"; tag "non-blocking"; "Nothing about it has been checked" | Table 1's words and Open; "Gap"; group "Automated — an automated evaluator settles these from the model"; "the only kind an automated evaluator settles"; "the evaluator ran and passed"; "the evaluator ran and failed"; "accepted provisionally, with a reason and an owner"; "Sourced from"; "not required"; "Nothing about it has been evaluated" |
| `status --json`, `state.json` | `by_status.verified` = a physical pass | the rename pass; until then no channel prints the key |
| README, `skills/nopekit/SKILL.md` | "verified", "proven", BLOCKED, UNVERIFIED, "capability gap", "negative control", "can't be validated by any tool"; "Never let a validator merely log. It must refuse." | glossary words: "no evaluator here can settle it — it is pending build"; "Never let an evaluator merely log. Its fail must stop something." |
| `skills/pack-authoring/SKILL.md` | "anything that already refuses"; "compares against the claim's acceptance and refuses" | "anything that already fails a bad design"; "… and fails" |
| `plugin.json` description | "Design and validate real physical things"; "gates each claim with a validator proven able to fail, and reports honestly what is verified versus assumed" | "Design and check real physical things"; "settles each claim with an evaluator shown able to fail, and reports honestly what is checked and what is assumed" |
| `marketplace.json` descriptions | "a validated physical thing"; "gate every claim with a validator that can actually fail"; "separates what is proven from what is assumed"; "Grows new validation capabilities" | "a physical thing, with an honest account of what is checked"; "settle every claim with an evaluator that can actually fail"; "separates what is checked from what is assumed"; "Grows new evaluators" |
