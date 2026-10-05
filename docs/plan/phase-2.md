<!-- Tier 3 of docs/PLAN.md: read docs/PLAN.md first (the map, the D-rows, the R-rules and G-criteria this file cites). Moved verbatim from PLAN.md §4.2; S-nn rows live in docs/plan/slipped.md. -->

# 4.2 Phase 2: `needs` and unknown, evidence semantics, `terminal`, export, REPORT.md

**Why this order.** This is where claim semantics change, so the oracle becomes
"blocking only grows" (R-8). Composition comes first (2.1), then the graph (2.2), then
admission's second half (2.3), then goalposts and the cross-check (2.4). Terminals,
signing, export and the report come last (2.5), because export's refusal predicate
consumes all four.

#### Target transcript

```text
$ nopekit check                     # on a copy with arm_length pushed so L/h < 5
[FAIL] bracket.model_validity : slenderness 4.2 (>= 5.0 for Euler-Bernoulli; …)
2 checks not run — prerequisite bracket.model_validity failed: bracket.deflection, bracket.bending_stress
6 gates: 4 executed, 0 cached — 3 ok, 1 FAIL, 2 unknown — tier 0
BLOCKING — 3 critical claim(s) must not be spent against:
[FAIL ] C1 Tip sags … — bracket.model_validity : slenderness 4.2 (…); bracket.deflection not run: prerequisite failed
[FAIL ] C2 Root bending stress … — bracket.model_validity : slenderness 4.2 (…); bracket.bending_stress not run: prerequisite failed
[gap  ] C7 …

$ nopekit export --dry-run          # on the shipped bracket; this is also /ready (D-15)
v0.1 is not ready: 1 failing (C1); 1 with nothing that can check it (C7).
It has not been tested on a real part (C5), and C6 is assumed with no one signed to it.
A passing check has shown it can refuse; that does not show it is right, or enough.
as of 3 min ago · 1e09113 · records <records12>
  ends in a closed-form calculation   C1 C2 C3 C4 C8
  ends in a physical measurement      C5
  nothing can check this yet          C6 C7
export: refused — C1 failing; C5 needs a signed measurement; C6, C7 nothing can check this yet
$ echo $?
1

$ head -3 REPORT.md
# wall-bracket v0.1 — readiness
**v0.1 is not ready: 1 failing (C1); 1 with nothing that can check it (C7).** It has not been tested on a real part (C5), and C6 is assumed with no one signed to it.
records <records12> · as of the newest result · nopekit <version>

$ nopekit claim physical C5 pass --detail "no cracking"     # from the agent's Bash
error: C5 says nothing a result could fail — write its test into claims/C5.json first
$ …after C5 gains an acceptance, the same call…
recorded C5 pass in results/C5.json — unsigned (agent session <id>): export still refuses C5
```

What changed for the human:

- A check that could not run because another failed says so, names the root, and does not
  say "install a tool" (today every BLOCKED reads *missing tooling*: `report.py:122`;
  `skills/nopekit/SKILL.md:187-191`; S-54).
- The headline names the assumption it used to omit (`report.py:429-498`; S-59).
- Each claim states what it ends in, in the paper's six phrasings.
- `export` is the refusal at the boundary that costs money (METHOD rule 4), and
  `--dry-run` is its preview.
- A result typed by the agent is recorded and labelled unsigned; it satisfies nothing.

**Checkpoint 2.0, `C:`.** Pin the full `resolve_status` table (every rung, every kind),
the current report section order, and that `SECTION_PROVEN` (P1.0) exists.

#### Checkpoint 2.1: honest composition (Kleene, D-01)

- In `resolve_status`, any covering verdict that is skipped or unknown (with none failed
  or errored) gives **BLOCKED**; any covering gate that never ran gives **PENDING**. FAIL
  is already conjunctive, so the ladder becomes three-valued AND, and
  `ClaimStatus.PASS`'s own comment becomes true (`models.py:62`).
- A **PHYSICAL** claim whose covering (modelled-half) gate FAILED or errored resolves
  **FAIL** before the result ladder is consulted (S-49). Today it reads UNVERIFIED, is not
  blocking, and the headline says it "clears every critical gate that is installed"
  (`report.py:473-476`).
- The report's PARTIAL row moves from PROVEN to NOT VERIFIED with the same wording;
  `site.py`'s `row["partial"]` becomes `bool(unproven)` regardless of status.
- **Strengthened test (R-6):**
  `test_site.test_a_partially_covered_claim_carries_the_partial_marker` asserts
  `status != "pass"` *and* that the unproven gate is named. (Landed in P2.1 as
  `test_a_partially_covered_claim_reads_skipped_and_names_the_gate`: D18 dropped
  `partial` itself, so the row reads Skipped and the old name would be false.)
- **Visible change**, stated in the phase summary and recorded in `claims.py` with the
  probe that exposed it: a project with a skipping tag-bound pack gate beside a passing one
  now blocks. That is the honest direction; the fix is to publish the key or drop the tag.
  The bracket is unaffected — none of its gates skip.

`V: SkipIsNotPass` gains: pass+skip is not PASS and is in `blocking()`; pass+unrun is not
PASS; the JUnit `claims.critical` suite has a `<failure>` for it; a physical claim with a
failing gate is FAIL and blocking.

#### P2.1's decision rows (`P2.1-Dn`)

Checkpoint 2.1 as it landed (PLAN-v0.14 §1.4, §1.5; GLOSSARY §3). The code and tests
cite these ids; each row is the decision and what was tried and rejected, so no fresh
context window re-litigates it. Committed with P2.1's review, where a refuter found the
ids cited in ~30 places and defined in none (they lived in a session's scratch spec).
Rows the review moved say so.

| Id | Decision | Rejected |
|---|---|---|
| D1 | `ClaimStatus` keeps its ten members and values; Table 1 is a reading of them (`claims.STATUS_KEY`, GLOSSARY §8's tokens) | renaming the enum now (R-8 could not tell a word move from a status move; ~80 test lines); `ERRORED`/`UNQUALIFIED` members (one fact, one status: the reason says which); aliasing members |
| D2 | one ladder for every kind, first match wins: Failing · Skipped (errored first) · Gap · Open · Stale · Pending build · Assumed · Checked (`claims.compose`) | today's ladder (S-03); Skipped above Failing; Open above Skipped; Pending build above Open or Stale on a physical claim; assumptions ignoring their covering verdicts; one ladder per kind |
| D3 | the cause travels with the status from one producer (`Composed`, `ClaimCause`, in `claims`, not the spine) | each renderer re-deriving the cause (S-68, P2.0 F-8); the cause in the enum |
| D4 | "unqualified" is `Verdict.unqualified`, set only by the spine (`verdicts._unqualified`), cleared by `run_gate`, never remembered; the `not admitted:` text unchanged until P2.3 | the text prefix as the predicate (a gate could make its crash read the quieter Gap); rewording the text now; a fifth outcome |
| D5 | `verdicts.resolve` states a refusal on rung 4 (stale entry) and rung 6 (no entry), not only on a Fresh one | filing the refusal as a verdict entry; rung 6 only; neither |
| D6 | an undemonstrated control still reads Stale, not Gap | Gap (every claim Gap after any spine edit, until `check`) |
| D7 | known-bad shown (reject-only) still counts until P2.3; pinned by `RejectOnlyStillCounts` | flipping it now (every bracket claim Gap until P2.3, then moved twice) |
| D8 | `Claim.owner` names a nominee; an owner counts only through `owners` (`Attribution`, bound by value to the owner and the rationale), which nothing produces until the signing channel — every assumption reads Gap | a forbidden key (an older or a strict reader refuses the file; see `Claim.owner`'s note); trusting the file; git authorship; an interim TTY command; deferring the rule |
| D9 | `BLOCKING_STATUSES` unchanged; more facts read into it (an errored claim, an unowned assumption, pass beside unrun or skip) | cause-dependent blocking; demoting C6 to not required |
| D10 | **moved by the review:** a recorded physical pass reads Pending build, cause `physical-pass`, reason `a pass recorded by <who>, not bound to an article`, until article binding; as landed it read Checked, and only *ready* left it out, so *checked* meant two things | Checked until binding (as landed); keeping Checked and dropping *ready*'s carve-out (a typed pass would make a project ready) |
| D11 | *ready* only when every required claim reads Checked (`readiness`, `all_required_checked`); zero required is not ready; `summary.ready` keeps "nothing stops `check`" | flipping `summary.ready` in place; `check`'s old `ready:` line |
| D12 | JSON keeps every key and value domain; the words arrive under new keys (`statuses`, `key`, `word`, `cause`, `reason`, `errored`, `counts`, …) | changing `status` values in place; `by_status["errored"]`; `state.json` re-keyed to tokens; words without a token; a top-level `status` map |
| D13 | `report.HUMAN`, one read-only table in `report.py`; `STATUS_TAG` a view of it; outcome tags `models._RENDER_TAG` re-exported | words in `models` (a word is not a type; a spine edit re-keys every cache); a `vocab.py`; `format.js`; a JSON data file |
| D14 | five-wide tags: `ok   ` `FAIL ` `STALE` `assum` `build` `gap  ` `skip ` `SKIP ` (errored) `open `; upper case = loud | full-term tags (P2.0's `\[.{5}\]` parsers); `REFUT`, `phys `, `unrun`, `ok-hw` (Never-says); `[ERR  ]` on a claim row; `skip!`; `chkd `, `check` |
| D15 | one reason producer, `report.reason`, led by the fact then the evaluator (`errored: <gate> : <first line>`) | no lead; the status word as the lead; the traceback |
| D16 | one severity order, Failing · Skipped errored · Skipped · Gap · Open · Stale · Pending build · Assumed · Checked; ties critical first, then record order; `claim list`, `check --json` and JUnit keep record order. **Moved by the review:** the order lives in `claims.SEVERITY_ORDER` (a rank is not a word), and `last_check.json`'s `worst` reads it | today's `_SEVERITY` (STALE above BLOCKED); the rank in `HUMAN`'s rows (the spine's `write_last_check` then picked by record order) |
| D17 | counts `N claims · a checked · …` (Checked first, zeros dropped, `N skipped (k errored)`); the readiness sentence lists every unresolved required claim by word; headings Pending build · Gaps · Assumed · Failing, stale, skipped or open; collision lines reworded (`invalidated:`, `verdicts current`, `unrun`, `last check run:`, `(identified)`) | `Unresolved — N required claims` now (P2.5 moves it again); "unsettled" over blocking claims only; a separate Pending build sentence |
| D18 | no PARTIAL anywhere; a Checked claim its evidence contradicts is "status and evidence disagree", loud, outside the checked section | PARTIAL as the word for a contradiction |
| D19 | every unresolved claim in exactly one report section; Gaps by cause (no evaluator, unqualified, unowned) | the gaps section reading `find_gaps` only |
| D20 | R-8's P2 oracle is a rank plus a cause, written twice (`StatusesMoveOnlyTowardUnresolved`, `tests/oracle/r8_statuses.py --toward-unresolved`) | one shared helper (an oracle that imports the module under test relaxes with it) |
| D21 | the page keeps glyph and tone per enum value and takes every word from `state.json` (`words`, `outcome_words`, and from the review `phrases`) | a page re-keyed on words or tokens |
| D22 | P2.1's vocabulary tests are GLOSSARY §7's status subset plus one-table routing (sentinels) | the full prose scanner and ratchet now; an AST scan of source strings (the review widened the PAGE scan to every string literal: the page has no rendered form a test can read) |
| D23 | CLAUDE.md invariants 1, 2, 4, 7 and the preamble in GLOSSARY's words | — |
| D24 | D2–D21 land as one change | semantics first (a crash read "blocked on missing tooling"); words first (Checked minted over S-03) |

#### P2.2's decision rows (`P2.2-Dn`)

Checkpoint 2.2 as it landed (PLAN-v0.14 §1.4 BLOCKED row, §1.5 groundspace row, §3 row
P2; GLOSSARY §3, §4). Where it disagrees with the checkpoint text below, these rows win:
*unknown* is gone (a Never-say for Skipped), BLOCKED is Skipped, and the measured
descriptor (S-56) moved to P4. Rows the design's critique moved say so.

| Id | Decision | Rejected |
|---|---|---|
| D1 | `GateSpec.needs: list[str]`, the LAST field, `@gate(needs=[...])` the last keyword; exact gate ids; `_own_copy` copies it; NOT in `SPEC_FIELDS_IN_RHO` (D-04) — declaring an edge re-keys the declaring file once, through its code digest | globs or tags (a tag-bound set moves when a pack is installed, and the graph and its cycles with it); an edges file (two homes); `after=` (sequencing's word); the prerequisite's outcome inside rho (a recovered guard would re-run every dependent) |
| D2 | registration refuses a need that is empty, holds whitespace, `/ \ .. :` or `* ? [`, repeats, or names the gate itself; a **cycle** (incremental DFS from the new node, the new spec substituted — complete under any load order); a **tier inversion** from either side of the edge. Forward references allowed (Q2.3) | refusing an unregistered need (a missing pack would become a load crash); checking only at sweep time (`pack validate` would pass a cyclic pack); falling back to registration order on a cycle |
| D3 | `gates.plan(registry, selected)`: the selection's prerequisite closure in DFS postorder (registration order outer, `needs` order inner) — exactly registration order with no edges; re-asserts acyclicity and tier order (defence in depth) | Kahn's sort, ties by index (unrelated gates jump a guard); alphabetical (S-52's cause); no expansion under `--only` |
| D4 | gates RUN in plan order and are LISTED in registration order: `SweepResult.rows`, `check --json`, JUnit, `resolve`, `_swept`; only the stream shows run order, and `SweepResult.order` records it | listing in run order (every project with an edge would move its JSON and JUnit order) |
| D5 | established = a pass, current. Negative kinds errored, failed, skipped, unqualified, not registered; not current invalidated, unrun; negative wins; within a class rank order, then `needs` order; roots transitive (`blocked_by`, a reading's `root`). **Moved by the critique:** errored ranks ABOVE failed — the dependent reads Skipped either way, and within Skipped a crash leads (invariant 2); with failed first, adding a failed guard to a crashed one made the dependent's claim quieter | passes only, ignoring currency (invariant 10); every unmet need alike (D7) |
| D6 | under a negative root the dependent is not run (nor its control) and reads `gates.blocked`'s skip — never cached, remembered or logged; the rule replaces a pass, a fail (current or not, **a Fresh FAIL served where the tool is missing included**) or nothing; the dependent's own crash stands, and its own missing tool, self-skip or refusal stands **unless the root crashed** (`verdicts._under_rule`, the one copy: `_pruned_row` and `run_all`'s default ask it). **Moved by the review:** the first rule let every skip or refusal of the dependent's own stand, so a crashed guard went quiet behind a missing tool (`[skip ]`, JUnit `<failure>`, absent from `last_check.json`'s `errored`), and returned whatever was read wherever the tool was missing, so a Fresh FAIL behind a failed guard read Failing on one machine and Skipped on another | replacing every reading (a crash read quieter for as long as the root is down); letting the dependent's FAIL stand (D-03, D-04); a skip of its own giving way under every negative root (as loud as the prerequisite skip, and a missing tool here is as true a reason; D10's order names the root first instead); `ClaimStatus.UNKNOWN` (D-02); the `unknown:` lead |
| D7 | under a not-current root the dependent keeps its verdict, marked: `prerequisite <root> invalidated: <what moved>` / `prerequisite <root> unrun`, `fresh` False (**moved by the critique**: the mark set only the reason, and every JSON channel served `fresh: true` beside it), in `stale_gates` — its claim reads Stale. **Amends D-03** ("neither run nor fresh -> unknown"): every model edit between check runs invalidates a guard and its dependents together, and D-03 read literally turns every guarded claim Skipped when the fact is "inputs moved; run check"; Skipped also outranks Stale and Open, so the count would report skips after every edit; and D-03's argument (do not spend on a dependent whose root cannot be judged) is the sweep's, where closure expansion leaves no not-current root but a costlier tier's entry served stale | D-03 read literally; dropping the verdict so the claim reads Open (a second mechanism; the reason loses the root); leaving the dependent alone (invariant 10) |
| D8 | one rule, three callers: `gates.prerequisite_root` decides; `run_all` prunes (hooks `pruned`, `current`, `marked`); `verdicts.apply_prerequisites` applies it to a resolution — `resolve`'s rung 7 and `cli._swept` again over the merged view (monotone, idempotent). The sweep's pruned row IS `resolve`'s reading under the rule (`_resolve_gate`, rungs 1-6 extracted, on a reader's `_Now`) | each producer keeping a copy (they drifted at every copy in P1 and P2.0); the rule in `resolve` only (`check`'s stream and JUnit would disagree with its claim view) |
| D9 | `Verdict.blocked_by` (the roots) and **`Verdict.blocked_kind`** (the first root's kind), the LAST two fields; `__post_init__` writes `skipped=True, passed=False` when `blocked_by` is set (R-2); spine-only (`gates.blocked`), cleared by `_stamp`, never stored or remembered (`_NEVER_REMEMBERED`, read and write). **Moved by the critique:** the design rejected `blocked_kind` as derivable from the root's reading — but a claim's composition sees the verdicts that cover it, and a guard bound to its own tags does not cover a narrowly tagged claim, so a crashed guard reached that claim in a missing tool's tone | the `skip_reason` text as the predicate (a gate could word its own skip into it); deriving the kind at `compose` from the root's verdict (a not-registered root may still have an orphan crash on disk) |
| D10 | `ClaimCause.PREREQUISITE` ("skipped" lead, Skipped's rank) and **`PREREQUISITE_ERRORED`** ("errored" lead; `Composed.errored` true: `[SKIP ]`, `(k errored)`, JUnit `<error>`, `last_check.json` `errored`) — added by the critique, invariant 2 through a prerequisite; in `claims`, not the spine. **Moved by the review:** within Skipped a prerequisite skip leads a plain one, and `OUTCOME_ORDER` holds `compose`'s order (`prerequisite-errored`, `prerequisite`, then `skipped`) — the claim cited its first skip in record order, so a missing tool beside a dependent of a failed guard read "install the tool", the one advice that changes nothing (S-54), and `last_check.json`'s `worst` named a missing tool's gate beside the cause `prerequisite-errored` | a lead word of its own ("blocked", "unknown": Never-says); splitting the count by cause; the record order within Skipped (the review's evidence above) |
| D11 | the edge is a **prerequisite** wherever a person reads it (`describe` "prerequisites", `gate show` "prerequisites:"/"prerequisite of:", `--only`'s help, `doctor`, `pack validate`, the pack guides and the skill — **moved by the review**: they said "needs", "need nothing" and "depends on"); tools now say "requires" in `describe`, and a gate that cannot run here says `not runnable here: <why>` right after its id (**moved by the review**: the tail's `BLOCKED: <why>`, a §3 Never-say beside the prerequisite list, which the new segment also pushed past the 240-character cap). The claim channel words a prerequisite skip from `report.HUMAN["prerequisite"]` and the mark (`report.prerequisite_phrase`) — **moved by the critique**: the kind words lived only in `gates`, a second outcome-word table no sentinel reached; the spine keeps `PREREQUISITE_FAILED`/`_NOT_ESTABLISHED` for the gate channel (skip reasons, the digest) | "needs" (*need*, the noun, is Gap's Never-say, and `describe` said "needs <tools>"); "dependency"; "after" |
| D12 | a bundled edge needs all four: the prerequisite is a validity guard (every fail means the number does not apply); the edge is isolated (it passes the dependent's own known-bad control); tier(P) <= tier(D); same pack. 23 edges over six packs plus the bracket's two; each edge and each rejected one has its reason at the decorator. **Moved by the review:** `bom.availability`, `bom.process_rules` and `bom.single_source` lost their edge to `bom.complete` — it also fails on any unpriced line, so one blank price cell hid a real end-of-life fail behind a skip naming the wrong root (`test_packs.AGuardFailureNeverHidesAnIndependentFail`); D13's isolation check cannot see an over-broad guard, since the guard passes each dependent's control. `bom.availability` refuses an unreadable quantity in its body | analysis-to-analysis edges (a fail would hide a measurement); a guard that also measures (`thermal.time_constant`); a guard broader than what its dependent reads (`bom.complete` before the three above) |
| D13 | isolation in R-4's two steps: `test_packs.ControlsAreIsolated` (zero hits on the declared edges, one on `beam.shear_stress -> beam.model_validity`), then `packs.demonstrate` step 5 behind `pack validate` and `gate selftest --pack`: `<D>: control not isolated — its prerequisite <P> does not pass <D>'s known-bad control (<outcome word>: <one line>)` (**moved by the critique**: "known-bad input" is a GLOSSARY §2 Never-say; **by the review**: the raw token `error` and a whole traceback). A prerequisite whose tools are absent leaves the check unrun, and says so: `Demonstration.unchecked`, `<D>: isolation not checked — its prerequisite <P> is skipped here (<why>)`, a `note:` of `pack validate` and `gate selftest --pack` (**moved by the review**: it was dropped without a trace, so `pack validate` passed a non-isolated edge on a machine without the guard's tool). Project gates: P2.3, as a diagnostic BESIDE the qualification line, never inside it (**moved by the critique**: the line and *qualified* stay GLOSSARY §2's) | refusing at admission now (a third control run per dependent); not checking (a later fixture edit trips its guard unseen); listing an unchecked isolation in `skipped` (that list is the gates whose own control did not run, and `gate selftest --pack` counts it) |
| D14 | a pack needs only its own gates (`pack validate`: `<gate>: prerequisite <id> is not in this pack`); a project may need any id; an unregistered need reads "not registered" at the sweep and is a `doctor` problem with its fix | cross-pack needs (isolation would depend on another pack's version) |
| D15 | `check --only X` runs X's closure; `gate selftest --only X` stays pure selection | — |
| D16 | no `pruned` key in `check --json` (rows carry `blocked_by`); no measured L/C descriptor (S-56 to P4) | a second copy of what the rows say |
| D17 | CLAUDE.md invariant 10; `test_meta.INVARIANT_CLASSES[10]`: `PrerequisiteFailureIsNeverAPass`, `ACachedPassNeverSurvivesAFailedPrerequisite`, `NeedsCycleRefused`, `TierInversionRefused`, all in `tests.fast`; invariant 2 gains `AnErroredPrerequisiteStaysLouder` | folding cycle refusal into invariant 3 |
| D18 | G4 does not move: at 7.0 mm the guard passes; the bracket's cache is regenerated (spine and `gates/structural.py` moved) and `docs/readiness.md` does not change | — |

**Check-in batch (P2.2):** GLOSSARY §4's new *prerequisite* row; *Skipped*'s Means
gaining "or a prerequisite is not established" and *Stale*'s gaining "or whose
prerequisite is invalidated or unrun" (the critique: D7 stretches *invalidated*, which
is defined over a verdict's own read set — the reason line names the prerequisite, so
no reader is misled, and an Open reading for an unrun root was weighed and rejected in
D7); D-03's amendment (D7) and D-02's "no new code path" (D9-D10 added two fields and
two causes, each with its evidence).

**Hand-offs:** P2.3 — project-gate control isolation as a diagnostic beside the
qualification line (the line stays `known-good pass · known-bad fail · mutation n/n
fail → qualified`), the bracket's two edges first. P2.5 — a required claim Skipped by
a prerequisite is unresolved; `export` refuses it with no new code. P3 — the `next:`
line: "fix <root>" for a failed root, "install <tool> for <root>" for one skipped on a
missing tool; `ask --next` ranks a failed root above its dependents. P4 — sequencing
over `plan()`, `SweepResult.order`, measured latency and cost (S-56). P5 — evaluator
lanes from `needs`/`needed_by`. thermal-analytic — the Biot half of `time_constant` as
its own guard, then `steady_state_temp -> <guard>`.

#### P2.3's decision rows (`P2.3-Dn`)

Checkpoint 2.3 as it landed (PLAN-v0.14 §1.4, §1.5, §2 rows W3/W7/W8/W11-W13, §3 row
P2; GLOSSARY §2). Where it disagrees with the checkpoint text below, these rows win:
admission is *qualification*, known-bad shown is a Gap (never a warning), the bracket's
control is not recalibrated, and the mutation pass is part of the rule for every
evaluator not from the bundled packs. Rows the design's critique moved say so.

| Id | Decision | Rejected |
|---|---|---|
| D1 | one control entry carries the whole qualification: `bad`, `bad_extra`, `good` (`{outcome, reads, extra, measured, limit, units, detail}`), `mutation` (`{runs, boundary, results, inconclusive, not_mutated}`, tallies derived), `admitted` re-derived by the strict reader. **Moved by the build:** no known-good control is `good.outcome: "not-run"` — known-bad shown is a fact about the project, filed — and `null` means only "the writer ran no good half" (D19) | an entry kind per half (nine readers would each qualify on half the rule); `good` as a flat word (no home for its reads); results in untracked `obs/` |
| D2 | one judge, `verdicts._qualification(QualificationFacts) -> token` (`""` = qualified): writer, strict reader, `_decide`, `_admission`, `_run_control`, `_disagree` and `_other_tiers` (**moved by the critique**: those last two compared `bad` alone) | deciding in each caller; trusting `admitted` from the file |
| D3 | known-bad shown is unqualified: Gap, `known-good not run`, beside a pass too; `RejectOnlyStillCounts` became `KnownBadShownIsAGap` in the open | a warning in every phase; Stale; a fifth `Admission.state` |
| D4 | which known-good control, first match: `NegativeControl.good` (the LAST field, handed what the known-bad fixture is handed); a pack's `selftest/baseline.json`; a project's `selftest/known_good.py`; else known-bad shown. A `good` fixture that reads the live design it was handed — itself, or the gate through a context it handed back — is `known-good control reads the candidate` | `known_good` as the field name; falling back to the live design |
| D5 | channel parity over the present `ctx.extra` keys, the spine's removed; both sets recorded. R-4 inside the checkpoint: C6 read exactly 13 (cad-solid 6, sourcing 7), then `good=` fixtures for those 13 (`selftest/good_meshes.py:baseline_meshes`, absolute paths so a host project loads them; `selftest/good_boms.py:baseline_bom`, its own `_control` note), then 0 hits. **Added by the critique:** an evaluator the walk applies to whose controls reach it through `extra` at all is unqualified (`channels:check`) — a check run hands `extra={}`, so both controls tested a path `check` never takes | keys each builder added; moving the 13 to params (on the check-in list) |
| D6 | mutation applies by LOCATION: every evaluator whose pack directory is not under the bundled `packs/` (project `gates/`, `.nopekit/packs/`, user and path packs). **Moved by the critique:** applicability is recorded in the static part (`nc.mutation`), so a promotion into `packs/` re-qualifies | project `gates/` only; every evaluator; a git-clean content check (critique: a bundled copy edited in place would escape it, and the check needs git — on the check-in list) |
| D7 | the walk, aimed (§4 of the design): ladder `MUTATION_RUNGS`, x then ÷, nearest first, until the evaluator's OWN value lands `MUTATION_MARGIN` past its OWN limit — never the pass flag — then 16 halvings in log-factor and outward rounding to 3 significant figures. **Moved by the measurement:** "past" within `_LAND_TOLERANCE` (1e-9) — 2.3 − 2.0 is 0.29999999999999982 in binary, and the bracket's 0.575 mutation read 0.576 | straight-through only; every read key changed; the ladder unaimed; stopping at the first fail |
| D8 | conclusive is GLOSSARY §2's: a pass or a fail. Skip and error are inconclusive, `(k inconclusive)`. None conclusive is qualified on the controls, its line saying why: `(none made: tier 1)`, `(none made: no value against a limit)` (**moved by the critique**: one word for two facts) | unqualified when none is conclusive (critique 8, rejected: §1.5's panel default, and 17 bundled gates could never qualify); `0/0` |
| D9 | where it runs: in process after both controls held, tier 0, `MUTATION_RUNS_MAX` 1024 runs, one influence probe per key (a value equal to the reported one first); keys the budget does not reach are *not mutated* (**critique**: not inconclusive) and, where one still moves the value, the walk could not finish — unqualified; a crash while aiming makes that key inconclusive, never "does not land" (**critique**); a crash that does not repeat holds the walk (`mutation:errored`); scratch in one temp dir outside the project and every pack, else `mutation:could-not-run`; every run's reads folded into the known-good trace at known-good digests | a temp worktree; scratch under `.nopekit/out`; a throwaway trace (V1j); a clock budget; no budget |
| D10 | re-qualify by values, both halves; the good half's host reads included, the gate's on what a `good` fixture handed back too (**critique**) | the known-bad half alone (V1i) |
| D11 | `rho_control` over both halves' reads and every walk run's; `out8` over the outcomes, numbers and tallies, never an `after` | known-good params out of rho; `after` in `out8` |
| D12 | a half that crashed, skipped itself, was unusable or read the candidate is remembered under `control:<gate>` with its facts and the refusal's own detail (**moved by the build**: the held verdict first kept only the token) | caching a half's crash |
| D13 | facts in the spine, words in `report.HUMAN["qualification"]`; `Verdict.unqualified` holds a token, `verdicts.parse_token` its inverse; `HUMAN["refusal"]` and `_refusal_words` gone; the spine's stale and pending reasons are worded from the same table at call time | the reason minted into the mark |
| D14 | the line `<id> : known-good <o> · known-bad <o>[ · mutation n/m fail …] → qualified|unqualified`, with a segment for each control-level fact (**critique**: channels differ, could not run/finish, errored, two outcomes, outcomes differ by tier, outcome differs from its cached entry) | `→ unqualified: <reason>`; ` · k inconclusive` |
| D15 | the Gap reason names the first fact that does not hold | the whole line |
| D16 | `check` prints a line for every unqualified evaluator, and for every walked one whose qualification ran; `controls: N run, N preserved, N re-qualified` | a line per qualification run |
| D17 | an unqualified evaluator never wears `[ERR ]` or *errored*; the page's row reads `unqualified` in Gap's tone | — |
| D18 | channels: `check`, `gate show` (`qualification:` and its rows), `gate selftest` (lines, summary, exit 1 on any unqualified; pack mode's rows, `-v` for every line, `qualifications` in JSON — **critique**), `doctor` (`qualification`, `known-good`), `pack validate`, the stale and pending reasons, the Checked hint, help text from the table (**critique**), `last_selftest.outcome` `"unqualified"` (**critique**: not `"error"`); `Admission.moved` replaces the parse of spine text, and pending never applies to an unqualified entry (**critique**) | moving every GLOSSARY §9 row now |
| D19 | an incomplete entry reads not yet qualified, and is a miss for `check` | refusing or upgrading the old shape; serving it |
| D20 | project-edge isolation is a note under the line in `gate selftest` | inside the line |
| D21 | S-17 closes through the aimed mutation (0.575 mm against 0.5), not by recalibrating `quarter_thickness` (critique 18: rejected again; the fixture's comment names the walk as the at-the-limit test) | recalibrating the fixture |
| D22 | CLAUDE.md 9 reworded, 15 added; `INVARIANT_CLASSES[9]` gains `QualificationIsPaired`, `EveryConclusiveMutationMustFail`, `[15]` is `MutationIsSealed` | folding the seal into 9 |
| D23 | identifiers keep their names until the rename pass (`Admission`, `admitted`, `paired`/`reject-only`/`no`, `NegativeControl`) | renaming record values now |
| D24 | `gates.selftest`'s outcome text stays spine-internal | a structured `ControlRun` |
| D25 | R-8 on the corpus; the visible change: a project with project evaluators and no known-good control reads them Gap, and so does one passing a conclusive mutation | a grace period |
| D26 | `SCHEMA` stays 1 | `SCHEMA = 2` |
| D27 | **Added by the critique (10): an evaluator never qualified at any version reads Gap** — not yet qualified, `qualification:not-yet|<tier>` — no pass of it counts; one qualified at an earlier version reads Stale. A gate with nothing recorded reads so too, and the prerequisite rule reads it as an UNRUN root, never a negative one (**moved by the build**: read as unqualified, a never-checked project read every guarded claim Skipped). *Moves Q5's default* (Open/Stale until the first check run) | Q5's default (a claim left Gap before any control had run); Gap after any spine edit (P2.1-D6's own rejection) |
| D28 | a project's relative fixture ref resolves against the project root the sweep names (`fixture_root=`), never the context's: a `known_good.py` may point its root at the design's own files (**found by the build**: every control of such a project was unusable) | resolving against whatever root the known-good design returned |

**Check-in batch (P2.3):** Q5 moved by D27 (the panel's default was Open/Stale);
`good.outcome: "not-run"` where the design said pass/fail (D1); the git-clean
applicability rule rejected (D6) and D-32's residual — a bundled copy edited in place
under `packs/` is never walked; critique 8's "0 conclusive is unqualified" rejected
(D8); the wider scope of mutation than GLOSSARY §2's wording ("an evaluator the
generator wrote in a groundspace": here every evaluator not from the bundled packs,
D6) — a GLOSSARY edit for the panel; the selftest summary's departure from the
design's words and *candidate* for "live design" (GLOSSARY §1); `doctor`'s tag column
(`[problem]`/`[ok]`, §9's pass); S-17's teaching half (`slipped.md`: the walk, not the
fixture, is the at-the-limit evidence); moving the 13 `extra` fixtures to the params
channel (D5); test_mutation's stated relaxation — a read that never lands is no longer
required to change (V12's trade); file-location parity — fdm's known-bad fixtures
reach their gates by path, the channel `check` takes, so D5's key check holds them, and
a check of file-location parity is not built (critique 19: deferred).

**Check-in batch (P2.3 review):** the check run is handed nothing its qualification
runs were not — no model for an evaluator outside the bundled packs, on any run
(`_no_model`), and a verdict that read ledger values no qualification run read does not
count (`channels:ledger`): a project gate that reads its limit off a claim now needs a
known-good design that hands it the same claim record (the shelf and staleness
fixtures were moved so, R-6); what stays visible is named in SPINE_CONTRACT's limits —
`ctx.out_dir`'s path, the call stack, the clock (`br8`: out_dir parity rejected, the
walk must write outside the project); a walked pack whose baseline hands no ledger can
never qualify a gate that reads a claim in `check` (the baseline holds params only);
the crash re-check counted in the walk's budget, once per value that crashed;
`gate selftest --json`'s `broken`/`skipped` back to 860ffa6's suffixed ids and
membership (P2.1-D12).

**Hand-offs:** P2.4 — the claim's acceptance as the walk's limit once goalposts live
in one place. P2.5 — `export` refuses a critical claim resting on an unqualified
evaluator with no new code. P3 — `init` scaffolds a `known_good.py` stub that
`doctor` flags until it passes; `ask --next` ranks "write selftest/known_good.py".
The rename pass — `Admission` → qualification, `NegativeControl` → `KnownBadControl`
with `good` → `known_good` beside it, record values to `qualified`/`known-bad-shown`.

#### P2.4's decision rows (`P2.4-Dn`)

Checkpoint 2.4 as it landed (PLAN-v0.14 §1.4, §1.5 rows *Operating context*,
*Qualification*; §3 row P2; GLOSSARY §1-§3, §9). Where it disagrees with checkpoint
2.4's text below, these rows win: the C8 split is rejected (D11), coverage S is
deferred (D24), and `between` is accepted on a verdict. Rows the design's critique
moved say so (the critique's numbers in parentheses).

| Id | Decision | Rejected |
|---|---|---|
| D1 | `GateContext.acceptance(claim)` returns a copy of the acceptance condition of the claim an exact id names, else the claims carrying it as a tag; records `acceptance:<key>` (digest of the sorted `[id, condition]` pairs, membership included) and **`acceptance-shape:<key>`** (the same, limits left out — **moved by the critique (1)**); raises — the gate errors — on no claim, two conditions, or no limit, the read recorded first. `verdicts.acceptance_of` is the one rule | the whole claim record as the read (a statement edit re-keys; every goalpost edit read `channels:ledger`, measured); a pointer from a claim into the projection; the spine deciding the pass from the condition |
| D2 | controls read their own goalposts: a project's `selftest/known_good.py` states `CLAIMS` and hands them; a pack's baseline holds none (a pack gate reading one errors on its control) | the live claim on controls (measured: C1 at 0.4 read `known-good fail`, Gap where 0.70 > 0.4 is Failing); a spine-supplied goalpost |
| D3 | **moved by the critique (1):** `_ledger_unseen` exempts `acceptance:` keys only — the limit — while `acceptance-shape:` stays under `channels:ledger`, and the exemption stands on the goalpost runs (D25) | every acceptance read exempt (the design: a gate keyed to its calibrated goalpost, or to whether its claim exists, read Checked); exempting every ledger read |
| D4 | a pass that read an acceptance condition needs a finite value, in its units (exact after strip), that it admits — else **errored** (`run_gate`, `_held_to_acceptances`); a fail needs nothing | not counting it (a malformed answer is crash-class); Failing |
| D5 | `Verdict.comparator` (after `blocked_kind`), the gate's own: `<= < >= > == !=` and — **moved by the critique (9)** — `between` (margin `band`); anything else on a pass or fail is errored by `_stamp`. Every bundled gate with a finite limit sets it (R-4: 117 limit-bearing verdicts inspected, zero without; one planted hit) | refusing `between` (a modelica band binding would error where it read Checked); `Verdict.limit_hi` now (no consumer before P5's bullet bar); `GateSpec.comparator`; inferring the side |
| D6 | `Verdict.settles` stamped from the spec (`_stamp`, `blocked`, `_as_spec`, `_synthesized`), so the comparison is a pure function of `(claim, verdict)` | a `{gate: settles}` map to `compose` (a registry-less reader would skip the comparison) |
| D7 | the entry's verdict block gains `comparator` and `settles`; the reader accepts the P2.3 block too (it reads Stale for the spine, never "ignored"); `out8` unchanged | exact shape only; `SCHEMA = 2` |
| D8 | `claims.margin(verdict) -> Margin(fraction, why)` by the verdict's own comparator and limit; carried on every JSON verdict row — **moved by the critique (15):** `margin_why` on every row without a margin, `no-value` included, and on every `state.json` row | the margin in a spine module; words now (P3, P5); omitting `margin_why` |
| D9 | the comparison (`cross_check`): normalised `settles` = normalised quantity, units equal (case kept), both finite → `holds`/`fails`; a counted pass that `fails` → **Failing**, cause `acceptance`, after a fail — **moved by the critique (7):** a pass outside its operating context is never compared (outside, a pass does not count) | comparing outside passes (a reader would change the design over a number the evaluator was never qualified to produce); Gap or errored for a miss; every covering verdict regardless of quantity |
| D10 | not compared is listed, never a status: `claims.not_compared`, a prerequisite of ANY covering evaluator excluded (**found by the build**: a guard beside a failing analysis was listed); `summarise()["not_compared"]` over every claim, and — **moved by the critique (10)** — `state.json` claim rows carry `compared` and `not_compared`, and the page headlines `compared[0]` | blocking on it; the page choosing its headline (it showed the guard's L/h on failing C1) |
| D11 | the checked table's columns from `HUMAN`: *Value* and — **moved by the critique (13)** — *Evaluator*; a guard's value leaves the cell, another quantity's goes to the note | the C8 split (moves every pinned count); keeping "Measured" or "Gate" (Never-says) |
| D12 | bracket data: C2 `bending stress <= 1.0 utilisation`; C3, C4 rationales naming where their limits come from | `settles="utilisation"` on the gate |
| D13 | limit disagreement: a compared pair whose finite limits part (`isclose`, rel 1e-9) → a `check` warning line on stdout, a `doctor` `limits` warn row, `check --json` `limit_disagreements`; never a status | a status; stderr |
| D14 | `GateSpec.operating_context` (LAST), `@gate(operating_context=…)`; refused at registration when malformed (R-10); deep-copied; not in rho; **moved by the critique (3):** every pass must READ each declared key (`run_gate` errors one that never did: the context is part of the read set) | categorical contexts; a context derived from the walk; `inf` bounds; a key the gate never reads (a model that does not load left it Fresh and counted while `check` read Gap) |
| D15 | judged on current values, over passes, by the spine (`gates.context_breach`, `verdicts._contexted` in `_resolve_gate` and the sweep's `before_hook`) — **moved by the critique (4):** on the spellings the run READ (an entry's reads, the run's trace), every one holding a value judged; a pass outside → the `context:outside\|<json>` token of `Verdict.unqualified` | judging recorded values; the gate's default-scope spelling (a scope override read bare 60 while 20 was judged); a new Verdict field; a set to `compose` |
| D16 | the mark never launders a fail: `Verdict.__post_init__` drops it on anything that is not a pass (its own R-2 error aside) | trusting the producer |
| D17 | `compose`: rung 1 gains `acceptance`; rung 3 unqualified first, then `outside-context` unless an owned fallback carries it (then rung 7 `fallback`); `OUTCOME_ORDER` gains `outside-context`; — **moved by the critique (5):** `explaining_verdict` ranks a pass that misses the comparison right after a fail, so `last_check.json`'s `worst` names it | Assumed at rung 3; outside-context ahead of unqualified |
| D18 | `Claim.fallback` (LAST); owner `Claim.owner`; counts only through `owners`, bound to `assumption_reason(claim)` | `rationale` as the fallback; a pointer to an assumption claim |
| D19 | the known-good control must lie inside the context: `good.outcome "outside"`, token `known-good:outside` | a warning; judging the known-bad control |
| D20 | **moved by the critique (2, 8):** the walk is unchanged — a passing landing is a conclusive pass wherever it lands | filing a passing landing outside the context inconclusive (the design: a context ending short of the landing qualified a gate keyed to its own control, and narrowing a declaration by one unit turned an unqualified evaluator qualified) |
| D21 | a guard outside its context is a negative prerequisite root of kind `unqualified` | a sixth `PrerequisiteKind` |
| D22 | words only in `report.HUMAN` (`context`, `acceptance`, the leads, the qualification table's `outside`, `goalpost:moves`); `check`'s tally and `--json` count `outside-context` apart; `status`'s gates line too | counting it `unqualified` |
| D23 | CLAUDE.md invariants 4, 7, 9 extended; `test_meta.INVARIANT_CLASSES` gains `APassMustMeetTheAcceptanceItRead`, `AValueOutsideTheAcceptanceIsNeverChecked` (4), `TheGoalpostLivesInClaims` (7), `OutsideTheContextAPassDoesNotCount`, `AFailOutsideStillCounts`, `KnownGoodOutsideIsUnqualified`, `AMutationPassingOutsideStillCounts`, `AGoalpostIsNeverAKey` (9) | a new invariant number |
| D24 | coverage S deferred to P5 with M18.4, on GLOSSARY §5's definition (a property of an evaluator, not of a claim) — **moved by the critique (16):** named on the check-in batch as a P5 scope addition | landing it now |
| D25 | **moved by the critique (1):** the goalpost runs — the known-good control re-run with each goalpost it read halved and doubled (`gates.goalpost_runs`, `GOALPOST_FACTORS`), its value and units required unmoved (`goalpost:moves\|<key>`), recorded in the control entry's `good.goalpost` and required by the strict reader | no run (the design: D4 checks only that a reported value meets the condition read, which a keyed gate arranges); the live goalpost substituted (a record per live value; a control re-keyed on every goalpost edit) |
| D26 | `SCHEMA` stays 1; `out8` unchanged; D-05 stands | — |
| D27 | R-8's two copies (`StatusesMoveOnlyTowardUnresolved`, `tests/oracle/r8_statuses.py --toward-unresolved`) admit P2.4's causes on the new side, each only where its fact is held: `acceptance` (rank 2) on a covering pass that settles the claim's quantity in its units and misses its condition — re-typed in the oracle (`_misses`), never imported; `outside-context` (rank 2) and `fallback` (rank 1) on a covering pass outside its context. On the corpus no claim moves | admitting a cause on any row; the oracle calling `claims.cross_check` (it would relax with the module under test, P2.1-D20) |
| D28 | **found by the build:** a gate that passes whatever it measures now errors on its known-bad input (D4: 30 mm against the 0.5 it read), so its control is held — `known-bad:errored`, remembered, never cached (P2.3-D12) — and runs again on every check, where the known-bad PASS before P2.4 was a filed entry, reused. Louder and refused as before; one control run per check for a broken evaluator | reading a D4 error on a known-bad half as `known-bad:pass` (a text predicate on `error`, P2.1-D4's rejection); filing an errored half |

**Check-in batch (P2.4):** GLOSSARY §3's Means for Checked, Failing, Gap and Assumed,
and its composition paragraph, gain the comparison and the operating context (applied
as additions; critique 11); §2's *operating context* has its *Code today*; §9's
"Measured" → "Value" and "Gate" → "Evaluator" applied in the checked table. D-10
amended: the goalpost read is the acceptance condition alone, a channel of its own
whose LIMIT alone is exempt from `channels:ledger` on the evidence of the goalpost
runs, and controls read the known-good design's `CLAIMS`. D-17 amended: margin carried
on every JSON row with a `why`; `between` accepted on a verdict, its margin `band`
(`Verdict.limit_hi` to P5 with the bullet bar, its first consumer). Old 2.4: the C8
split rejected (D11); coverage S deferred to P5 — **a P5 scope addition** beside
PLAN-v0.14 §3's "old 4.1-4.4; W16" — on GLOSSARY §5's per-evaluator definition; the
critique's conclusive-mutation reading kept as GLOSSARY §2 states it (D20 rejected,
so no definition moves). `modelica.result_claim`'s detail now words its margin as
inside or past the limit (critique 14).

**Hand-offs:** P2.5 — the signing channel produces `owners` for `fallback` as for an
assumption (`Attribution(owner, assumption_reason(claim))`); `export` refuses a
required claim Assumed by fallback with no new code; the track record keys a
contradiction by the evaluator's code digest and whether its inputs were inside its
operating context. P3 — `status --short`'s closest margin from `last_check.json`;
`next:` for an outside context. P5 — the bullet bar from `margin`, never from
`measured - limit`; `Verdict.limit_hi` and a band's margin; `chart.js`'s `marginText`
judged by invariant 14's scan; coverage S. Packs — declare operating contexts on the
inputs a correlation was fitted over (thermal convection's temperatures and length,
fluids' Reynolds inputs) or keep the guard gates, each with provenance; a baseline's
`claims` key so a pack gate can read `ctx.acceptance` on its controls;
`modelica.result_claim` moving to `ctx.acceptance`. The rename pass — `rationale`
(for an assumption) and `fallback` fold into GLOSSARY §8's `assumed: {reason, owner}`.

**Review of P2.4 (`P2.4-Rn`).** Each finding confirmed by at least two of three
refuters, applied test first where it was a laundering path.

| Id | Decision | Rejected |
|---|---|---|
| R1 | the goalpost runs move each limit by x0.1, x0.5, x2 and x10, a band's two limits one at a time (`verdicts.moving_limits`); the shape holds which limits are stated, a one-sided condition's `limit_hi`, and whether the read returns one condition (`_shape_form`) | x0.5 and x2 alone (a gate keyed below 0.2 at a calibrated 0.5 read Checked at 0.1); both ends scaled together (a ratio-keyed gate read Checked); random points (not reproducible); the other sign; an additive offset; more fixed points (none closes the residual, now worded as "every limit the runs do not visit") |
| R2 | the goalpost runs run AFTER the walk, at each limit's site — the known-good run's params, or the first walk run that read it with a verdict (`GoalpostSite`, `note_goalpost_sites`); a limit is exempt from `channels:ledger` only where its runs are complete (`moved_goalposts`); the strict reader requires none | requiring runs for every `acceptance:` key the folded trace held (the writer refused its own entry; `check` exited 2 for the whole project); moving a walk-only limit at the known-good params (it is not read there: the runs would prove nothing about the branch that reads it); exempting a limit no run moved |
| R3 | a writer/reader disagreement holds that one evaluator, `control:unwritable` (`verdicts.entry_problem`, asked before filing, recorded or not) | an exception out of the check run (nothing recorded for any evaluator, while status and doctor promised the next check run) |
| R4 | a claim file's `acceptance.limit`/`limit_hi` is a number or null, named against the file by the strict reader; in memory, `cross_check` leaves a band with an unusable `limit_hi` not compared and `acceptance_of` makes it a problem | coercing `"0.8"` (it rewrites what the file says); a traceback |
| R5 | a gate reports the units its own arithmetic computes in — the bracket's deflection, PACK_FORMAT and the `GateContext.acceptance` docstring; a scan holds every shipped and taught gate to it (`AnEvaluatorStatesItsOwnUnits`, invariant 4) | units moved by a goalpost run (an honest gate's pass would error under D4 there and read unqualified); openmodelica's binding units (the pack's stated no-conversion stance: a pack change, not this review's) |
| R6 | every bracket gate judges the numbers it reports (S-18 closed for all six) | narrowing S-18's "closed" |
| R7 | limit disagreements over current verdicts only; `doctor`'s `limits` row always printed; `LIMIT_REL_TOL` with its real reason (float noise; a rounded limit is a disagreement on purpose) | blaming a stale verdict of a gate that reads its limit from the claim; a tolerance as loose as a reporting precision |
| R8 | a pass outside its operating context: the page's row reads `outside-context` (Gap's glyph and tone, the tally's word, its own chip title); `check --json`'s qualification token is the qualification's alone; `why` and `claim show` print a marked verdict as `<gate> : <lead>: <words>`, never `[ERR ]` and never the token, and `claim show` prints the claim's reason under its header; `pack validate` judges the known-good control's context as `check` does | `unqualified` on the page (D22's rejection) |
| R9 | the page shows the claim's own limit beside its value (`limit_text`, written by `site.state` through `report.limit_words`) | the headline verdict's limit (S-35 on the page) |
| R10 | *goalpost* leaves every human channel for *acceptance condition* or *limit*, and joins the acceptance condition row's Never-say (`TheAcceptanceConditionHasOneName`); the shape keys get words of their own (`acceptance_ledger`) that keep the calibrated limit | the raw keys; "hand its known-good design the same claims" (D2's coupling, taken literally) |
| R11 | the outside-context paragraph and the checked table's P2.4 clause in `HUMAN` (`context.gaps_intro`, `acceptance.closing_counts`); GLOSSARY §3's chain reads Assumed for an owned fallback before Checked | literals beside the table |

#### P2.5a's decision rows (`P2.5a-Dn`)

Checkpoint 2.5's first half — terminal, authority and the physical path — as it landed
(PLAN-v0.14 §1.4 rows ASSERTED, VERIFIED, REFUTED; §1.5 rows *Assumed needs an owner*,
*Rebuild prediction*, *Physical evidence is article-bound*, *Track record*; §2 W8,
W12; §1.6 C7; GLOSSARY §1, §3, §6). Where it disagrees with checkpoint 2.5's text
below, these rows win: no supersession of a fail yet (D12, P2.5b's), the channel value
is `interactive` (D4), and `GateSpec.refuser`/`terminal-unmet` wait for P2.5b (D1).
Rows the design's critique moved say so (its numbers in parentheses, as the build
received them).

| Id | Decision | Rejected |
|---|---|---|
| D1 | `Claim.terminal` (after `fallback`): a `models.Terminal` value, `""` = by kind (physical → measurement, assumption → none, measurable → automated); allowed by kind `TERMINALS_BY_KIND`; the strict reader refuses a value outside the set (a display word typed as a value is named: `"simulation"` → `solver`) and a pair outside the table, only when the field is present (R-10). Human channels print `report.HUMAN["terminal"]`'s word: *measurement*, *expert judgment: <authority>*, *assumption* (for `none`, now a GLOSSARY display word — **moved by the critique (14)**), and *automated* for every measurable claim, declared or not | deriving closed_form vs solver now (S-62); printing a declared word over evidence nobody judged (P2.5b's `terminal-unmet`); replacing `ClaimKind` (the rename pass) |
| D2 | `Claim.authority`: the person or institution an expert-judgment claim stays with, a nominee like `owner`; refused on a non-human terminal, and `owner` refused on a human one (one name per role) | the authority as `owner`; refusing human-without-authority (a status says it: Gap `no-authority`) |
| D3 | one channel, three acts: `claim physical <id> pass\|fail\|assume`; `assume` records an owner (an assumption's or a fallback's — P2.4-D18's hand-off) or an expert-judgment claim's authority | `claim assume` (D-12: one channel; a rename-pass alias on the check-in batch) |
| D4 | the channel is derived (`cli._channel`): `interactive` (a TTY, no agent marker, the claim's id typed), `agent-session <id>` (a marker — **moved by review (R8):** by exact name, `cli.AGENT_MARKERS`, no longer any `CLAUDE_CODE_*`), `non-interactive` (a pipe); only `interactive` lets a pass count or an attribution exist | the value `terminal` (GLOSSARY: one word for two fields); a flag or variable; the TTY alone; the markers alone; a y/n confirmation; the whole `CLAUDE` prefix; the whole `CLAUDE_CODE_` prefix (R8) |
| D5 | `who` = `vcs.ident`, `when` = the clock; `--who`/`--when` parsed only to be refused, before the project is read (`cli.REFUSED_FLAGS`, hidden from `--help`); no identity → nothing recorded | removing the flags now (A-14 stays open); accepting and ignoring |
| D6 | `results/<id>.json` = `{"results", "attributions"}`, each list sealed (claim id and list inside the seal) and chained, a legacy prefix chained whole by virtual digests; `store.append_signed` the one writer, never re-encoding an earlier entry | one list with a discriminator; a file of their own; an HMAC; chaining from the last legacy entry only |
| D7 | a broken seal or chain refuses the file on every command, naming file, list, entry and fix; **moved by the critique (6):** the fix compares the file with what git holds (`vcs.show`) and names every fail a `git checkout` would discard, with the command that records it again | degrading a broken entry to "does not count" (two laundering paths); "git checkout" alone (it discarded an uncommitted fail silently) |
| D8 | the article (in P2.5a) is the design at recording: every input and derived value (`config.<k>`, `<k>`), the model's code closure (canonical AST), and — **moved by the critique (1)** — the bytes of every project file a path-valued parameter names and every project file a current verdict read; hashed; `revision`/`dirty` shown, never hashed. The claim half (`claims.claim_digest`): statement, kind, terminal, acceptance, note, authority | build-time articles now (no command could record one before P2.5b); the covering evaluators' read sets; a declared `depends_on`; file bytes of the model (a docstring would demand a re-test); the path string alone (a re-exported mesh left a pass Checked) |
| D9 | what a record requires, in order, nothing written otherwise: a refused flag; the claim; an identity; the act fitting the claim; for any pass on a physical claim a written test and evidence (**moved by the critique (5):** whatever its terminal — a hand-written `terminal: human` no longer drops either), and a model that loads; `--measured` consistent; the typed id. `assume`: off `interactive` refused; the owner — and, **moved by the critiques (4, 10)**, the authority too — must be the git identity's name part or whole identity, exactly; an expert-judgment pass the same | matching an authority by the typed `--authority` alone (whoever typed the name settled a judgment the claim named someone else for — W8's "only the named authority settles it" did not hold); evidence optional; recording a pass that can never count |
| D10 | a result on any claim (E4, F3): a fail fails it; a pass settles a measurement or a judgment; beside an automated evaluator a pass settles nothing (`why` says so — **moved by the critique (15):** not called *agreement*, which paired with the defect's *disagree*); on terminal `none` a pass is refused | refusing a measurable claim (E4 needs it) |
| D11 | a pass counts (VERIFIED: `on-article`, `judged`) at rung 8 only, every covering automated evaluator composing first; the judge (`verdicts.judge_results`) checks the terminal, channel, who, authority, measured value, then — **moved by the critique (7)** — the article before the claim before the evidence, so a moved article reads Stale and is named whatever happened to the photo | the evidence first (a moved article read non-blocking Pending build once its photo changed) |
| D12 | R-3 at its strictest: every fail counts, any channel, legacy or sealed, across every edit; no supersession in P2.5a; a moved fail's row says `(invalidated: article …'s design moved: … — a new article is needed)` and joins the rebuild prediction | supersession on a recording-time article (a nudge plus a typed pass would clear a no) — P2.5b's, on recorded articles |
| D13 | one judge in the one resolver (`Resolution.standings`), never cached; one view builder (`verdicts.view`) for `cli._resolved`, `site.state` and `write_last_check`; a raw ledger (`standing is None`) reads every pass Pending build in P2.1's words | `owners=`/`results=` keywords through every call site; judging in `claims`; caching standings |
| D14 | a contradiction is captured when the fail is recorded (`claims.contradicted_by`): each covering automated evaluator whose pass counted — qualified, current, admitted by the claim's acceptance — excluding a prerequisite of another; a pass outside its operating context recorded `inside: false`, never counted; never for an authority's no; the track record (`verdicts.track_record`) keyed by code digest | a track-record file of its own; deriving at read time; unqualifying a contradicted evaluator |
| D15 | `--measured VALUE`, finite, in the claim's units: it decides the outcome where the claim has a limit, a disagreeing typed outcome refused | `--value --units`; a typed outcome only |
| D16 | the rebuild prediction (`claims.rebuild`): the articles a counting result (the counting fail, or the newest moved pass a person made on a measurement) is bound to whose design moved, one line each with its claims; **moved by the critique (12):** in P2.5a it never under-predicts and over-predicts freely — "names nothing else" is P2.5b's, with `export`'s traced articles, and the Fig. 4 test records one article for that reason | per-claim lines; naming passes that never counted, judgments, claim-moved passes |
| D17 | an expert-judgment claim: Gap `no-authority`; Gap `authority-unattributed` until its authority records it with `assume`; Assumed `awaiting-judgment`; Checked `judged`; Stale `judgment-moved`; any fail Failing (`judged-fail` when the authority's). **Kept against the critique (13), and put on the check-in batch:** the walkthrough shows it Assumed from the moment it is written; read literally, an agent's edit `"terminal": "human", "authority": "<anyone>"` would turn any Gap into a passing `check` | Assumed from the file's `authority` alone (that laundering); "judged for X, recorded by Y" (**dropped by the critiques (4, 10)**) |
| D18 | words from one table (`report.HUMAN`: `terminal`, the leads, `recorded`, `not_counted`, `signing`, `physical`); no *signed*, *confirmed*, *verified*; the console is "their own shell"; the page's "contradict" title is `phrases.disagree`; **moved by the critiques:** the Stale line says the result "was recorded on a design that has since moved" (18 — never "was built from" before P2.5b's export articles), the report's Stale advice follows its cause (9 — a moved article is never told `nopekit check` restores it), `claim physical --help` draws from `HUMAN` and names the three acts (16) | *signed*/*unsigned*; "confirmed by hand"; "at a terminal" |
| D19 | `ClaimCause` gains eleven; within rung 5 the person's cause leads an invalidated evaluator and cites it; **moved by the critique (8):** under `stale=True` a counted pass reads Stale (`invalidated`) | invalidated-first; an enum member per fact |
| D20 | terminal words on `claim list` (`[automated]`, `[measurement]`, `[assumption]`, `[expert judgment: Dana]`), `why`'s header (`[measurement · required]`), `why`'s `PHYSICAL RESULTS` block; JSON claim rows gain `terminal`, `terminal_word`, `authority`, `article`, `standing`, `contradicts`; `physical_result` gains `counts`, `why`, `recorded` — **moved by the critique (3):** the page paints a result's ok tone only on `counts` | the kind's code word; the tone on `passed` |
| D21 | `doctor`'s `results` rows: a refused file named with its fix, legacy passes counted, evidence changed or missing named | doctor crashing on the refusal |
| D22 | CLAUDE.md invariant 11; 4 and 7 extended; `RenderersAgreeOnPhysicalClaims` planned for 12 | a new number for the article |
| D23 | the paper's Fig. 4 as `tests/test_fig4.py`, its physical-article control recording one article; **moved by the critique (19):** the owner-removed control records K4's owner first, then removes it | a second article now (an over-prediction pinned as correct) |
| D24 | S-61: `Tier.EXTERNAL`'s comment loses "a human with calipers" | leaving it to the rename pass |
| D25 | the bracket's records do not change: C6 stays Gap (A-15), C5 Pending build; the cache regenerated, `docs/readiness.md` re-rendered | recording C6's owner in a commit |
| D26 | `util.canonical_json` is the one canonical form; `verdicts._canonical_json` delegates byte for byte | a second canonical form; `SCHEMA = 2` |
| D27 | **added by the critique (11), reopening P2.1-D9 for these causes alone:** a Stale that waits on a person's act on an article — `article-moved`, `claim-moved`, `judgment-moved`, `article-unjudged` — with no invalidated covering evaluator beside it never stops `check` (`claims.blocks`), exactly as Pending build does; it is unresolved (*ready* false, P2.5b's `export` refuses it) | blocking (a recorded pass made `check` exit 1 after any design edit, until an article was tested that `check` itself would not let anyone build — recording a pass left a project more blocked than not recording one) |
| D28 | **found by the build:** an owned fallback carries a pass outside its operating context whether or not that pass is invalidated (rung 5 skips the outside evaluator when carried) | Stale (Fig. 4's base case read "a check run settles it" where a check run would only confirm the pass lies outside) |

#### P2.5a's review rows (`P2.5a-Rn`)

The adversarial review of P2.5a, each finding confirmed by at least two of three
refuters and fixed with its test (red first against `d981397` where it was a
laundering path). Where a row disagrees with a D-row above, the R-row wins.

| Id | Decision | Rejected |
|---|---|---|
| R1 | a refused results file's advice restores the newest commit whose version verifies (HEAD's when it does; `vcs.history`, 50 commits) and names EVERY entry that version does not hold — a broken seal as unverifiable whatever its `passed` says, a sealed fail with the command that records it again carrying its measured value, evidence and authority, a removed entry nobody holds — amends D7 | the fails by their `passed` flag (a flipped fail was left out, and the checkout erased it); HEAD alone (a committed edit's checkout changed nothing); the tampered copy's detail in the command |
| R2 | a results file no claim file holds is kept (`Ledger.removed`): holding a fail it is composed beside the claims — Failing, required, blocking `check`, a JUnit failure — on the track record (`Contradiction.removed`), a `doctor` problem, its id never reissued; amends D12's "across every edit" to include a claim file's rename or deletion | refusing every command (a dropped requirement is an ordinary edit); a claim synthesized into `claims` (`save` would write it back) |
| R3 | a judgment of other words is no judgment: a judged claim whose claim half moved reads as one written fresh — Gap until its authority records it as it reads now, Assumed after — and the judge reads a judgment's claim half before its article; amends D17 and D27 for judgments | Stale `claim-moved` (it never stops `check`, while the same claim written fresh is Gap: D17's laundering by another route); taking `claim-moved` out of `AWAITS_A_PERSON` (Stale would block where a fresh claim's Assumed does not) |
| R4 | a measured value is compared with the acceptance condition only while the claim has not moved: a moved limit, either way, reads `claim-moved` ("test it again"); amends D11's fact order | the value first (a tightened limit read Pending build, a loosened one Stale); Failing for a value the new limit refutes (the person tested against the old one) |
| R5 | a pass waits until every registered evaluator has run here once (a `never` row, not pruned behind a prerequisite), and an article's files come from registered evaluators' rows only; amends D8 | recording the cache state in the article (one design would still carry two ids); requiring every evaluator current (a check run before every pass) |
| R6 | names compared stripped (`claims.name_of`); an owner attribution counts when any recorded one matches the claim's owner and reason now | refusing a name with spaces (every command refused for a stray space); the newest attribution alone (an owner changed back read Gap) |
| R7 | a positional act and `--pass`/`--fail` that disagree, or an outcome's flag or `--measured` with `assume`, are refused; `--json`'s `recorded.act` is the act written; a value that is not UTF-8 is refused naming its flag | the flag winning, silently; a traceback |
| R8 | the agent markers by exact name — those observed in this harness's Bash tool — the one matched named in the recorded line and the refusal; amends D4 | the `CLAUDE_CODE_` prefix (Claude Code's user-set configuration lives there: a person's exported `CLAUDE_CODE_USE_BEDROCK` made their own shell an agent's) |
| R9 | every value printed to a terminal goes through `util.printable`: whitespace collapsed, controls escaped as `\xNN` | refusing control characters in the strict reader alone (R-10; git's identity and an evidence path reach the terminal too) |
| R10 | a judgment is worded as one on every channel — `JUDGMENTS` in `why`, the page's group and heading, its own Stale and fail lines naming no article, `why`'s acceptance line saying who settles it; amends D18, D20 | an article's words for a person's judgment |
| R11 | the readiness tally counts Checked on an article; the Stale advice is a sentence of its own; `doctor` words a fail's changed evidence as a fail | — |
| R12 | shipped text uses neutral names; `tests/test_priors.py` scans what ships for the scored scenarios' terms, held as digests | the terms as words in the test |

**Hand-offs.** P2.5b — milestones and `export`; build-time articles (`source:
"export"`, traced `built_from`) and `--article`; supersession of a fail by a pass on a
different recorded article; Fig. 4's two-article exactness row; `GateSpec.terminal`,
`terminal-unmet` and the closed-form calculation, simulation and datasheet words on
human channels; the readiness sentence's rebuild clause; invariant 12. P3 — `/tested`;
the permission rule on `results/**` and human-terminal claim files (W9: the seal's
lock); `next:` naming who records what. P4 — a merged results chain in `trade`.

**Check-in batch (P2.5a).** A-14 (delete `--who`/`--when` — refused here); A-15 with
its new fact (C6's owner can now record it: `nopekit claim physical C6 assume`); an
expert-judgment claim reads Gap until its authority records it with `assume`
(walkthrough step 6 shows Assumed from the start; D17's laundering is why); an
institution as an authority — its git identity must be a person's, so named delegates
in the claim file would be the way (D9); GLOSSARY — a *channel* row (`interactive`,
`agent-session`, `non-interactive`; "in their own shell", "from an agent session", "from
a pipe or a script"), *contradiction* reworded "a physical result that fails a claim
one of whose automated evaluators had passed, qualified and current" (W12's reading
over §1.4's "Checked"), and the physical-pass-beside-an-automated-evaluator fact (D10's
words, no *agreement*); the claim half including the statement (a typo fix asks for a
re-test); P2.5a-D27 (a person-awaiting Stale never stops `check`); paper F3 (the
bracket at 8.0, C1 measured with a ruler, as E4's first article) and F4 (P2.5a records
the human-authority boundary; P3 enforces it).

#### P2.5b's decision rows (`P2.5b-Dn`)

Checkpoint 2.5's second half — milestones, the money boundary and the readiness
sentence — as it landed (PLAN-v0.14 §1.4, §1.5; §2 W3, W7, W11, W13; §3 row P2; GLOSSARY
§4, §5). Where it disagrees with checkpoint 2.5's text below, these rows win: the
package is `out/<m>/` at the root (not `.nopekit/export/…`), REPORT.md replaces
`docs/readiness.md`, and `fold` is gone (D18). Rows the design's critique moved say so
(its numbers in parentheses, as the build received them; every one was applied, 7
in part).

| Id | Decision | Rejected |
|---|---|---|
| D1 | a milestone is a record: `milestones/<name>.json` = `{description, requires, generator}`; the stem is the name (`store.MILESTONE_NAME`); read strictly (`requires` distinct plain ids, `generator` a project-relative `<path>.py:<function>`); a record kind (`RECORD_DIRS`, the index, `records_digest`) | one `milestones.json`; a `milestone` field per claim; a declared `tests` list; a `name` key |
| D2 | a milestone requires exactly its `requires`; `critical` keeps meaning what `check` blocks on and what the project-level sentence counts. **Moved in part by the critique (7):** the milestone's own report and its package flag each claim *required*/*not required* by `requires`, never by `critical`; the project-level words stay (C-6, G4, `CheckShape` pin them) and *critical*'s GLOSSARY term goes on the check-in batch | the union with `critical` (no first article could export without a decision); `critical` as an implicit milestone |
| D3 | *ready* is one predicate: `claims.unresolved(ledger, composed, milestone=None)`; at least one required claim and every one Checked; a required id with no claim file is `missing`. **Moved by the critique (6):** V-1's equalities include `state.json`'s `readiness.milestones` and `milestones.judge` (export's JSON `ready` comes from it) | a copy per reader (`summarise` held one; deleted); `missing` as a strict-reader refusal |
| D4 | `Claim.expected_latency` `{value, units}`, `LATENCY_UNITS` s…year (Julian); measured from an exported article's `when` to the newest result on it; out of the claim digest. **Moved by the critique (16):** allowed only on a measurement terminal — a judgment has no article to measure it | in the milestone; bare seconds; free text; `month` |
| D5 | `export <m>` is one path for both modes (`cli.cmd_export` driving `milestones.py`): load; under the build lock the re-run (D6); the judgment on the re-executed view; the generator traced and the package built aside; then dry run stops, written swaps and appends. **Moved by the critiques:** the inputs the judgment stood on are re-hashed before the swap and a move refuses it, kind `package` (4); under `--dry-run` a missing git identity or a legacy project is a `precondition` refusal it says it would make, exit 1 (21) | a second predicate for `/ready`; short-circuiting the re-run; skipping the generator in a dry run |
| D6 | the re-run is `check --force --only <closure>`'s sweep at tier 3, controls and prerequisites included, filed as `check --force` files it (never in a dry run); it writes no `last_check.json`. **Moved by the critiques:** the base case re-runs too and reads Checked where the cache read Stale (8); the export record's `claims` map marks each claim `reran`, `counted` is sealed only for required claims, and the package's REPORT.md says the rest are as last evaluated (18) | refusing whatever is not Fresh; never filing the re-run; re-running every evaluator |
| D7 | a disagreement refuses, and no go-ahead covers it: a forced run at the ρ the cache served Fresh with another `out8` (`SweepRow.disagrees`, whatever the instruments). **Moved by the critique (3):** also from the records — two outcomes already on record (the error row, a control's two-outcomes token, a conflict in the resolution before the run) and a qualification that held on the record and not when re-run; **(14):** its rows read `[two outcomes]`, never *disagree* (the status-and-evidence defect's word) | by gate id; by the claim's status; a tolerance |
| D8 | going ahead over unresolved required claims is a person's decision: `--proceed --why`, `interactive` only, the milestone's name typed; sealed as the entry's `proceed`; covers `unresolved` alone. **Decided by the critique (13):** it is the reprint path after a physical fail on a required claim, and the refusal's words name it | a `decisions/` record; `--force`; an agent's go-ahead; a standing waiver; a carve-out letting the new article answer its own fail |
| D9 | `exports/<m>.json`, append-only, each entry sealed and chained by `store.append_sealed` (P2.5a's chain by kind; the `results` form byte-identical, C-1). **Found by the build (V-6):** the seal form names the file's stem as `file` — under `milestone` the entry's own key overrode it and an entry copied to another milestone's file verified — and `_export_milestone_problem` refuses an entry naming another milestone | inside `milestones/`; under `.nopekit/`; one file per export |
| D10 | the package `out/<m>/` (root, ignored): the generator's files, `REPORT.md` (= `report --milestone <m>`), `model.json`, `MANIFEST.json`; built aside, swapped whole, refused over a file no export recorded or one edited since. **Moved by the critique (1, option 2):** `model.json` holds exactly the article's param rows, so the generator is the only channel by which a value reaches the builder. **Found by the build:** the manifest's records digest leaves `exports/` out (`records_digest(exclude=…)`), or two exports of one state differ | `.nopekit/out/<m>/`; a path per export; tracking it; the package hash as the article |
| D11 | the generator `fn(ctx)` runs traced as a gate is, in the package's scratch; refused when it raises, writes nothing, or writes outside its directory. **Moved by the critique (12):** a write outside it under `--dry-run` is refused and named — not undone (undoing is a second writer) — and invariant 12 says so | a model object; `fn(params, out_dir)`; a list of generators |
| D12 | the exported article `{source: export, hash, built_from, traced, milestone, when, revision, dirty}`: traced, what the generator read; untraced, P2.5a's whole design (over-predicts) | always the whole design; declared inputs; the evaluators' read sets |
| D13 | `claim physical <id> pass\|fail --article <hex>` (≥ 12 hex, unique among exports); a fail's `contradicts` is the export's sealed `counted[<id>]` (`milestones.sealed_contradictions`) | the newest export by default; `--milestone`; re-deriving from the current resolution |
| D14 | supersession (Q2.11): **moved by the critique (2):** a fail F on article A stops counting only when A and B are both exported and traced, A moved, a later pass P stands on B (`EntryStanding.stands`), B ≠ A, and B differs from A on a row A recorded (`_differs_on_recorded`); never a judgment's or an assumption's; F counts again the moment the design returns to A; kept and named. `view` releases a claim's `physical_result` only to the counting fail or a standing pass | none (step 8 dead-ends); hash-only B ≠ A (the same geometry reprinted until it passes) |
| D15 | a result on an exported article counts only while `exports/` holds it (`export-missing`) | trusting the copied article; refusing every command |
| D16 | the rebuild prediction over judged standings: an exported article moves only with its reads | per-claim lines |
| D17 | `report --write` writes `REPORT.md` at the root, ignored (`/REPORT.md`, `/out/` in the root block, written by `report --write` and `export` first); `docs/readiness.md` deleted, `doctor`'s `report` row names a leftover. **Moved by the critique (17):** every cache-based *ready* — `export`'s list, `status`, REPORT.md's milestone lines — says "as last evaluated" | a committed REPORT.md; keeping both |
| D18 | the readiness sentence in every branch: Pending build, `Rebuild article …`, then `Checked on an article: N (ids).` or `No claim is checked on any article.`; one renderer with `milestone=`; the project REPORT.md lists each milestone's line under the sentence. **Moved by the critiques:** the limits line is printed by `export` in every mode as well as REPORT.md (10); one milestone line, `<m>: k of n required claims checked · s stale[ (ids)][ · …]`, keeps the walkthrough's stale count (20) | a second sentence builder; `fold` |
| D19 | channels: `export` (list), `export <m>`, `--dry-run`, `--json`; `report --milestone`; `summary.milestones`; `state.json` `readiness.milestones`; `why`/`claim show` a `latency:` row where one is declared or measured, and a superseded fail's row "does not count", naming the pass; `doctor` rows `milestones`, `exports`, `report`. **Moved by the critique (11):** `export`, its dry run and `report --milestone` join `StatusLinesSpeakTheTable`'s GLOSSARY-parsed scans and the sentinel run | a `milestone` command; `check`'s exit over milestones |
| D20 | words only in `report.HUMAN`: `milestone`, `readiness`, `latency`, `export`; the status words in the hardware clause, the limits line and the milestone line through `words()` (the sentinel run found them literal) | literals beside the table |
| D21 | CLAUDE.md 12 new; 11, 8 and 7 amended; `INVARIANT_CLASSES[12]`; `PLANNED` empties. **Moved by the critiques:** 12's headline is PLAN §4.0.1's general one, no renderer more generous (15); 8 names what `export` writes — its record, new verdict and control entries, the ignore blocks (5, 12) | a 16th number |
| D22 | `Ledger.milestones`, `Ledger.exports`; `exports` hidden from gates (`_LEDGER_HIDDEN`); `WATCHED` gains `milestones/**`, `exports/**`, `generators/**` | hiding milestones; exports visible |
| D23 | the bracket: `milestones/print-v1.json` (C1–C4) with `generators/profile.py:side_profile` — the side profile SVG and, **moved by the critique (1)**, `print-settings.txt` carrying the material it reads (never `load_n` or `bed_xy`); `docs/readiness.md` deleted; README; the cache regenerated. C5 gains no `expected_latency` (C-2) | a milestone requiring every claim; a generator in the model |
| D24 | deferred: `GateSpec.terminal`, `terminal-unmet`, the closed-form/simulation/datasheet words → P3; Λ₀ → P4; the page's milestones and test card → P5. **(23):** PLAN-v0.14 §3's P3 row amendment goes on the check-in batch | landing `terminal-unmet` beside the money boundary |
| D25 | edges: no identity or a legacy project refuses a written export (exit 2) and is a refusal a dry run reports (D5); exit 0 written, would write or listed; 1 refused; 2 usage or record error | an anonymous export; migrating on export |
| D26 | what an export seals: every claim's `{status, cause, reran}`, each re-run's `{gate, rho, out8, code, outcome, qualified}`, `counted` per required claim, the article, the package, `proceed` | the sentence's text |
| D27 | **the test card (critique 9):** every claim whose terminal is a measurement and that is not Checked, required or not (C5 is on print-v1's card), then every required automated claim with a limit, as a cross-check — E4's first article (a ruler on C1) is on it; each with its test, its latency and the command that records it on this article | the physical claims alone; a per-claim opt-in |
| D28 | **Fig. 4 (critiques 19, 22):** V-2's prerequisite row on a guarded copy (`fig4.guard`, covering no required claim), its plant `gates.plan` without expansion; K8 through `fig4(extra_claims=…)`, so P2.5a's rows over the shared claims stay unedited | the bracket's `model_validity` (it already covers C2); K8 in the shared claims |

#### P2.5b's review rows (`P2.5b-Rn`)

The adversarial review of P2.5b, each finding confirmed by at least two of three
refuters and fixed with its test (red first where it was a laundering path). Where a
row disagrees with a D-row above, the R-row wins. Found while fixing: V-10's class named
its fail-entry builder `fail`, shadowing `TestCase.fail`, so every assertion in it had
been a no-op (`test_meta.NoTestShadowsTheFramework` now refuses the name).

| Id | Decision | Rejected |
|---|---|---|
| R1 | supersession also needs what was PRINTED to differ: no export of B carries the generator's bytes an export of A carried (`EntryStanding.built`, `verdicts._built_seal` — the package's files but REPORT.md, model.json, MANIFEST.json), and A's own export on record; amends D14 | the recorded rows alone (a no-op `unused = None` moved the code row, and the same print reprinted byte for byte released its fail); dropping the code rows (a fix in the generator's code could then never release a fail); the package hash (REPORT.md's words move with any status) |
| R2 | `os.link`/`os.symlink` are traced (source read, link written); a link in the package is refused (`generator_linked`); a file in the package no traced write put there makes the article untraced | the link's target alone (a hard link's bytes change with the project's next in-place edit) |
| R3 | the scratch is `.nopekit/out/export-<m>/` in both modes, created and removed under the lock, under the trace's out anchor; amends D10's path | `out/.<m>.tmp-<pid>/` (a project path to the trace: `makedirs` put the pid in the article, a listing refused every written export, a copy made it untraced); a pid suffix (the modes' paths differed) |
| R4 | `--article` reads EVERY export record of the article: `milestones.sealed_on` charges what any sealed (one row per evaluator version), `bound_export` names the newest that re-ran the claim; latency runs from the newest export of the article not after the result; amends D13 | the first record in name order (a milestone that did not require C1 bound its fail: `contradicts: []`); the newest alone |
| R5 | latency is a measurement's, measured only from an entry of the person channel; amends D4 | any entry on an exported article (an agent's pass read "measured 0 min"); an automated claim's (a ruler measured C1's "latency") |
| R6 | every reader but the boundary says *ready* "as last evaluated" in its sentence — the project's, `report --milestone`'s (whose line now says nothing was re-run), its JSON (`milestone`, `last_evaluated`) — and `status` lists each milestone's line; the package's REPORT.md is the boundary's (`render_markdown(boundary=True)`); amends D17, D18 | the package's words on the cache's view; `status` silent (SPINE_CONTRACT and the skill said it listed them) |
| R7 | the package's REPORT.md records on its article (`--article`) and carries the test card; the card's cross-check lets `--measured` decide; amends D10, D27 | a record line with no article (a builder's fail landed on a design article no reprint answers); a hard-coded `fail` (an agreeing value was refused) |
| R8 | the reprint is offered only for a fail on an exported, traced article; any other fail's refusal says no reprint releases it; amends D8 | offering it for every moved fail (a path back to where it began) |
| R9 | a disagreement's refusal names the entry served and the way out — remove the entry that is not the evaluator's output; a model that does not load is a refusal of its own (`model`), never covered; an errored claim's refusal says `skipped (errored)`; amends D7 | "`nopekit check --force` records the re-run" (done already, and it clears nothing); "re-run: none — no evaluator settles a claim" over six that could not run |
| R10 | the swap appends the record inside it and undoes itself when the append raises; `doctor` judges a package with no export record; a dry run the lock refuses touches no scratch | swap then append (an unwritable `exports/` left a package every later export refused) |
| R11 | a broken `exports/` seal's restore walks git as `results/`' does (`store._export_restore_advice`), naming every export it drops | HEAD alone (P2.5a-R1's rejected advice, again) |
| R12 | the Reproduce block never glues `#` to a word (`_REPRODUCE_COLUMN`, `_commented`) and lists every milestone; `doctor` resolves a generator bound any way at module level (`cli._bound_at_module`), a star import a warning | `ljust(33)` (`--dry-run#` failed in a shell for `print-v1`); a cap of four; a top-level `def` alone |

**Hand-offs.** P3 — `/ready <m>` = `export <m> --dry-run` (D-15); `/tested` = `claim
physical --article` from the test card; the permission rule refusing agent edits to
`exports/**` and making `milestones/**` ask-first (a milestone names its own
requirements: SPINE_CONTRACT's limits); `GateSpec.terminal`, `terminal-unmet` and the
display words (D24); `next:` naming the export a milestone awaits. P4 — Λ₀ per milestone
from `claims.latency`; the proceed decision beside decisions; a merged `exports/` chain
in `trade`. P5 — the page's milestone lines and test card (`readiness.milestones` is in
`state.json` now).

**Check-in batch (P2.5b).** Q-1's reading of GLOSSARY *required claim* and a term for
`critical` (D2: required by `check` and the project sentence; a milestone requires its
`requires`); PLAN-v0.14 §3's P3 row gaining the terminal words and `terminal-unmet`
(D24, critique 23); invariant 11's amended wording (supersession, Q2.11's "for every
object but the one that failed"); `/out/` at the project root (the walkthrough's path);
`year` in `LATENCY_UNITS`; the hardware clause's words (`No claim is checked on any
article.`, `Rebuild article …`) and the limits line; the go-ahead's home in the export
record (GLOSSARY *decision*); the test card derived, not declared, automated claims as
cross-checks (D27); GLOSSARY *terminal*'s "until P2.5b" → "until P3"; GLOSSARY §7's
channel row naming `export` (done here, for review); paper F3: the bracket at 8.0
exported as `print-v1` is E4's first article.

**Slips closed.** S-41 (REPORT.md generated and ignored); S-59 (pinned by C-6 and V-1);
S-60's field half (`readiness.milestones`). S-62 moves with D24 to P3.

#### Checkpoint 2.2: the refusal graph

```python
@gate(id="beam.deflection", …, needs=["beam.model_validity"])   # exact ids, no globs
GateSpec.needs: list[str] = []          # LAST field; copied by _own_copy
Verdict.blocked_by: list[str] = []      # LAST field; always with skipped=True, passed=False (R-2)
# skip_reason = "unknown: prerequisite failed: beam.model_validity"             (the root failed)
#             | "unknown: prerequisite not established: <root> (<outcome>)"   (anything else, D-03)
```

**Registration refuses** a self-need; a duplicate or whitespace id; a **cycle** — an
incremental DFS from the new node names the whole cycle `a -> b -> a` with each member's
pack, and is complete under any load order (slice: 20,000 random graphs × random load
orders, 0 mismatches against full-graph detection); and a **tier inversion**, where a need
has a higher tier than its dependent. `plan()` re-asserts acyclicity and tier monotonicity
at sweep time for forward references; `packs.validate` refuses a `needs` id the pack does
not itself provide (project `gates/` may name any id; a need still unresolved at sweep
time gives unknown, "not registered").

**Running the graph.** `gates.plan(registry, selected, *, prior)` expands the selection by
its prerequisite closure and sorts it with a stable Kahn sort, ties broken by registration
index, so with no `needs` the order is identical to today's. `_selected` stays pure
selection, so `gate selftest --only X` still runs only X's control. A prerequisite is
established only if it produced an `ok` verdict in this sweep or has a fresh `ok` entry;
anything else is unmet (D-03), and the dependent gets a synthesized unknown whose `fn` is
**never called**. It propagates transitively and names the *root* in `skip_reason`, so
`_skip_digest` groups everything one root blocks into one line.

**Bundled edges** are added only where the control stays isolated — the prerequisite
passes on the dependent's own sealed fixture:

- beam: {deflection, deflection_ratio, bending_stress, buckling} → `model_validity`;
  `model_validity` → `input_sanity`;
- fdm: {overhang, bridge_span, bed_fit, min_wall, layer_alignment, print_time_est} →
  `process_model_valid`;
- fluid: {drag, pipe_pressure_drop} → `flow_regime`;
- modelica: {result_claim, mirror_agrees} → `solution_valid` (all three `Tier.INSTANT`,
  `modelica.py:530, 720, 855`); `simulates` → `compiles` (both `Tier.SOLVE`);
- bom: {cost, availability, moq, process_rules, single_source} → `complete`;
- thermal: `steady_state_temp` → `time_constant`;
- cad: `wall_thickness` → `watertight`; {clash, assembly_connected} → `is_volume`;
- bracket: {deflection, bending_stress} → `model_validity`.

**No edge** where the dependent's own fixture trips the prerequisite (slice isolation
matrix): `beam.shear_stress → model_validity`, `modelica.compiles → checks`,
`bom.currency → complete`, `thermal.convection → h_agreement`,
`cad.degenerate_faces → watertight`. Each gate's comment records which fixture trips which
guard; the edge can land later with a new isolated fixture. **No edge from
`modelica.result_claim` to `modelica.simulates`**: INSTANT → SOLVE is a tier inversion this
checkpoint refuses (D-28). Guards keep their broad claim binding (no status churn); in-body
checks stay, because a gate must stay correct when called directly.

**Wording and surfaces.** BLOCKED splits into "blocked by a failed prerequisite (<root>)",
"blocked: prerequisite not established (<root>, <outcome>)" and "blocked on missing
tooling", read from `blocked_by` and the root's outcome, in `models.py:66`,
`report.py:90, 122`, `cli.py:1105-1106`, SPINE_CONTRACT, `format.js:28` and
`skills/nopekit/SKILL.md:187-191` (S-54). `describe()` says "after P" for needs and
"requires X" for tools (the word "needs" already meant tools and capability gaps);
`doctor` says "N capability gaps"; `gate list --json` gains `needs` and `needed_by`;
`check --json` gains `pruned` — gates not run because a prerequisite did not pass, with
their measured medians: a fact, not a scheduler (D-31). The measured descriptor row lands
here: `gates.measured(obs) -> {n, median_s, max_s, median_cpu_s}` over executed runs only;
`gate show`/`gate list --json` show L and C with `n`, "not measured yet" at n = 0 — C is
`cpu_s`, never the tier, which has its own *declared budget* column; `doctor`
warns when the median breaks the declared tier ("declared tier 0, measured median 5.1 s
over n=4"; S-56). Housekeeping: the bracket header's stale `beam.*` names
(`examples/bracket/gates/structural.py:14, 22`; S-57); `fdm_print_fold.py` gains its
leading underscore (S-58).

| Change | Failure it could introduce | Test |
|---|---|---|
| Unknown state | A dependent that never ran is read as a pass by some consumer. | `V: PrerequisiteFailureIsNeverAPass` with a sentinel `fn` that returns `passed=True` and records whether it was called: the prerequisite failing, erroring, being availability-skipped or unregistered each leave the dependent not called, `ok` False, `skipped` True, `blocked_by` naming it; a chain P→Q→R names P on R; the message says "failed" only when P failed (D-03); a claim covered only by the dependent is BLOCKED and `check` exits 1. `V: R-2`: a ledger dict with `blocked_by`, loaded by a Verdict class *without* the field, is still not ok. |
| `needs` vs ρ | A recovered guard re-runs dependents for nothing, or a down guard serves a dependent's cached PASS. | `V: test_prerequisite_fail_does_not_invalidate_downstream_cache`: the guard flips to FAIL; the dependent's entry stays Fresh; its claim reads unknown (D-04). |
| Cycle | A cycle deadlocks or silently falls back to registration order. | `V: NeedsCycleRefused`: self-need; a two-node cycle closed from either side; a three-node cycle across two registries in both load orders; a property test (random graphs × orders) that refuses iff the graph has a cycle; a cycle closed after registration through a copy is caught by `plan()`. |
| Tier inversion | A solver dragged into the tier-0 loop (rule 10). | `V: TierInversionRefused`, both registration orders. `V:` loading openmodelica is not refused (no result_claim→simulates edge). |
| Topological order | Changed run order for existing projects. | `C:` with no `needs`, verdict order equals registration order. |
| Bundled edges | A dependent's control is pre-empted by its guard, so admission shows nothing about where the gate actually runs (§2.1). | `V: test_packs.ControlsAreIsolated`: every prerequisite passes on each dependent's own sealed fixture; `gates.selftest` itself fails "control not isolated: P also rejects <fixture>". |
| Guard coverage | A narrowly tagged claim still passes on an invalid model (S-51). | `V: GuardsReachNarrowClaims`: beam `shear_governed` and `stubby` against a claim tagged only `deflection` give BLOCKED naming `model_validity` (PASS today). The keyword heuristic `test_every_pack_ships_a_validity_guard` (`tests/test_packs.py:86-91`; S-55) becomes structural — some gate in the pack is needed by another — a strengthening under R-6. |
| Latency never declared | A declared number leaks in as L or C. | `V: LatencyIsMeasuredNeverDeclared`: no `GateSpec`/`PackManifest` field named independence/latency/cost; `gate(…, independence=0.9)` raises TypeError; `pack.json` carrying `latency` is an unknown key; unknown and availability-skipped verdicts have `duration_s == 0` and are excluded from `measured()`; at n = 0 `gate list --json` shows C "not measured yet", never the tier. |
| Mesh-only fdm projects | `fdm.overhang` goes from a measured verdict to unknown when `process_model_valid` skips on missing bbox keys. | Honest and fail-closed; stated in `packs/fdm-print/PACK.md` in the same change. |

#### Checkpoint 2.3: paired admission

```python
NegativeControl.good: str = ""   # LAST field. Resolution:
                                 #   explicit ref;
                                 #   else pack    -> selftest/baseline.json (sealed);
                                 #   else project -> selftest/known_good.py:make;
                                 #   else admission = "reject-only" (a warning in every phase, Q5.5;
                                 #   its FAIL blocks but refutes nothing, P3.1)
# control entry: {"bad": "fail", "good": "pass", "admitted": "paired" | "reject-only" | "no", ...}
```

- A gate is **admitted** when its bad outcome is `fail` (or `error` when `expect="error"`)
  **and** its good outcome is `pass`. An always-False gate and an always-raise gate with
  `expect="error"` are **not admitted**: "refuses everything — it cannot accept its own
  known-good input" (S-04). `expect="error"` is kept, gated by the known-good half: a
  parser guard legitimately refuses by raising.
- **Channel parity (D-26):** after spine-owned keys (`pack_dir`, `pack_dirs`, the
  `load_file` memo) are removed, `set(bad_ctx.extra) == set(good_ctx.extra)`. **The same
  checkpoint ships** good fixtures for cad-solid (baseline meshes through
  `extra["meshes"]`) and sourcing (the unmutated baseline BOM through `extra["bom"]`).
- **Control isolation:** after the control fires, every prerequisite in `spec.needs` must
  pass on the same sealed fixture (2.2).
- **Project fixtures already derive from the known-good design** (D-27, landed in P1.2);
  `selftest/known_good.py` now also supplies the good half. The default `Config` stays at
  7.0 and failing.
- **The bracket's control is recalibrated** (S-17): `quarter_thickness`, 89.6× past the
  limit — the "narrative margin" beam-analytic records as REJECTED
  (`packs/beam-analytic/selftest/bad_beams.py:63-65`) — becomes a known-good-derived
  fixture about 1.15× past the limit (the pack's own `_MARGIN`). The 64× version is kept in
  the fixture's comment as a rejected alternative, with the pack's reason. The control
  still fires; no pinned result changes.

| Change | Failure it could introduce | Test |
|---|---|---|
| Known-good half | Honest bundled gates become "not admitted" if a baseline is subtly wrong. | R-4: the bundled corpus is measured first (all 54 pass their baseline in the slice probe). |
| Parity | cad-solid and sourcing go red (§4.0.4 item 1). | `C:` both stay admitted *with* their new good fixtures. `V:` `return not ctx.extra` with a dict fixture is not admitted; the same gate with a params-channel fixture is not admitted either, because it passes both halves. |
| Known-good as the good half | The bracket's controls change severity. | `C:` the six real fixtures are admitted, paired. (The identity-fixture `V:` moved to P1.2 with D-27.) |
| Recalibration | The margin drops into verdict rounding. | `V:` the 1.15× fixture fails with a measured value strictly past the limit at the gate's rounding. |
| Reject-only projects | Third-party projects lose their passes. | Reject-only is a *warning*, in every phase, naming why their FAILs are not yet diagnostic; `export` refuses critical claims resting on reject-only gates (2.5), and `trade` never counts their FAIL as a refutation (3.1). |

**Ask-point A-1** (METHOD.md rule 5): the code, PACK_FORMAT and SPINE_CONTRACT carry both
halves; METHOD.md is untouched until the user answers.

#### Checkpoint 2.4: goalposts in one place, the per-verdict margin, the cross-check

- **`GateContext.acceptance(claim_id_or_tag)`** returns the covering claim's
  `Acceptance` and records the claim record's digest as a consumed input in ρ (D-10).
  `bracket.deflection` reads C1's limit through it and `DEFLECTION_LIMIT_MM` is deleted
  (`examples/bracket/gates/structural.py:36`); thickness 7.0 still fails (0.6997 > 0.5), so
  the R-8 oracle sees no change. **SEALED:** during **every** control, pack or project,
  `ctx.acceptance` resolves only from the fixture's own context — a pack's from its
  `baseline.json`, a project's from the acceptance `selftest/known_good.py` carries beside
  its calibrated design — so moving a live claim limit never changes a control's severity
  (invariant 5's failure mode, for project gates: live, C1's limit admits
  `bracket.deflection` paired only while it sits in [0.469, 0.575)). `ControlsAreSealed`
  gains a case that sets every host claim limit to 1e9 and asserts every pack control
  still fires. `V:` with C1 at 0.75 and at 0.3, `bracket.deflection` stays admitted paired.
- **`Verdict.comparator`** (optional, last field) — set in this checkpoint by **every
  bundled gate with a finite limit**, the bracket's 6 and the packs', via R-4: a detector
  lists verdicts carrying a finite limit and no comparator, then a test asserts none
  remain; without it every consumer below has no data on shipped gates — and
  **`claims.margin(verdict) -> Margin | None`** (D-17): LE/LT `(limit − m)/|limit|`, GE/GT `(m − limit)/|limit|`,
  BETWEEN the smaller side over the span; EQ, NE, a zero limit or a missing value give
  None; a margin whose side disagrees with `passed` is None with a note — the verdict wins
  (S-18). Consumers: the cross-check (here), `next_action` (P3), focus rule 3 and the
  bullet bar (P4).
- **Static cross-check** in `claims.resolve_status`: for a covering verdict with a finite
  `measured`, where normalised `settles` equals the normalised acceptance quantity **and**
  normalised units are equal, `v.ok and not acceptance.holds(v.measured)` gives **FAIL**,
  with a reason naming both limits ("gate passed against 0.5 mm; the claim says 0.3 mm").
  It only ever fails, never passes. Pairs it does not compare are counted "not
  cross-checked" in the report and never implied checked, by P5.1's graph rule: every
  covering gate that is not a `needs`-prerequisite of another covering gate of the claim
  counts, so a `settles` the proposer wrote cannot take a gate out of the count (M18.4).
- **Limit disagreement.** Where a gate still computes its own limit (C3's 15 MPa is
  `design_stress`, C4's 204 mm is `usable_bed`), `check` prints one warning line and
  `doctor` one warn row when a covering verdict's `limit` differs from its claim's
  `acceptance.limit` with matching quantity and units (METHOD rule 2 — one home per
  number; S-35).
- **Coverage S** (M3.S): `gates.coverage(spec, claims, control_entry) -> {claim_id:
  "demonstrated" | "declared only"}`, per claim, never per gate: a claim is
  *demonstrated* only when the paired control's bad-half measurement violates **that
  claim's** acceptance under the cross-check's matching rule (quantity and units); P5 adds
  the killed outside mutant at that claim's limit. A gate that is another's
  `needs`-prerequisite also gets `region`, derived from its `settles`, comparator and limit
  (`L/h >= 5`). Shown as S in `gate list --json` and `gate show`.
- **Bracket data alignment** (content change, stated in the summary; S-46):
  `bracket.bending_stress` `settles` becomes `"utilisation"` to match C2; `bracket.min_wall`
  is retagged off C4 (C4 drops the `wall-thickness` tag) onto a new claim C8 ("walls are
  printable at this nozzle"), because C4's PROVEN row prints `73.5 mm; 7 mm`, mixing an
  unrelated measurement in; C8 passes, so the pinned blocking set is unchanged; C3's and
  C4's limits gain provenance notes ("= design_stress at SF 2", "= usable_bed").

`V: AcceptanceCrossCheck`: a bracket copy with C3's limit tightened to 0.1 MPa —
`bracket.bearing` still computes its own 15 MPa and passes at 0.19 — gives C3 FAIL with
both limits named (PASS today). C1 cannot exercise the cross-check: after D-10
`bracket.deflection` reads C1's limit itself, so C1 at 0.3 with thickness 8 is a plain
gate FAIL, kept as its own D-10 test. `model_validity` and `min_wall` produce no hits;
`"mm"` against `""` is not compared; negative control — 0.47 against 0.5 still resolves
PASS. `V: Limits.test_verdict_limit_disagreeing_with_claim_is_reported`: `bed_xy` 250 →
C4's 204 disagrees with `bed_fit`'s 234, and `doctor` shows 1 warning (0 today).
`V: Margins`: `min_wall` 7.0 vs 1.2 (GE) is +483% inside, not −483%; `passed=True` at 0.55
against `<=` 0.5 gives None; limit 0 raises nothing. `V: test_reading_a_claim_makes_it_an_input`:
editing `claims/C1.json` stales `bracket.deflection` and nothing else.
`V: DescriptorsAreDerived.test_s_never_counts_an_undiscriminated_claim`:
`bracket.model_validity` labels C1 and C2 *declared only* and shows region `L/h >= 5`;
`bracket.deflection` labels C1 *demonstrated*; a gate tagged onto two claims whose
control's measurement violates only one labels the other *declared only*; S is never
printed as a number.

#### Checkpoint 2.5: `terminal`, signed results, export, REPORT.md

```python
class Terminal(StrEnum): CLOSED_FORM, SOLVER, DATASHEET, MEASUREMENT, HUMAN, NONE
Claim.terminal: str = ""   # declared INTENT; "" = derive. Allowed by kind:
                           #   measurable {closed_form, solver, datasheet}
                           #   physical   {measurement, human}
                           #   assumption {none, human}
                           # a declaration can RAISE the evidence bar, never satisfy anything
GateSpec.refuser: str = "" # optional; only closed_form|solver|datasheet; validated at register;
                           # may raise the derived class, never lower it
```

**Resolution once a result exists**, for every (kind, terminal) pair — the result rung of
`resolve_status`. A measurement or human **fail, signed or not, resolves REFUTED and
blocks**, for PHYSICAL and ASSUMPTION claims alike: R-3, a result never loses its power to
refute, and an unsigned no is still a no. A signed pass bound to the current object
resolves VERIFIED. An unsigned or expired pass, or no result, leaves PHYSICAL at
UNVERIFIED and ASSUMPTION at ASSERTED. MEASURABLE claims and terminal `none` take no
result (`claim physical` refuses them). Today every assumption resolves ASSERTED whatever
is recorded (`claims.py:205-206`), and ASSERTED does not block, so a named authority's no
blocked nothing.

- **Derived refuser class** (`gates.refuser_class(spec, registry)`): solver if the spec, or
  anything in its `needs` closure, has `requires_tools` or tier ≥ SOLVE; otherwise
  closed_form. `bom.*` declares `refuser="datasheet"` (the sourcing pack is tier-0
  arithmetic over a vendor document, `packs/sourcing/PACK.md:9-40`).
  **`modelica.result_claim` declares `refuser="solver"`** (D-28): it is tier 0 with no tools
  and judges a solver's result file (`modelica.py:716-733`); an edge to `simulates` would be
  the tier inversion 2.2 refuses. The Q2.9 panel decides whether `solution_valid` and
  `mirror_agrees`, which read the same recorded run, declare it too. `Tier.EXTERNAL`'s
  comment loses "a human with calipers" (`models.py:92`; S-61) — that is a result, not a
  gate.
- **`claims.terminal_state(claim, …) -> TerminalState {claim, terminal, declared, derived,
  satisfied, reason, by[], as_of, channel}`** is pure; live byte checks are passed in by the
  edge. A MEASURABLE claim's default is the max refuser class over its covering gates
  **admitted at their current version** (from P5, also not LOGGER for that claim), or none
  when none is — which every human channel says as "nothing can check this yet —
  <gate> has not shown it can fail: <why>", never "failing" or "crashed", although the
  claim still blocks; PHYSICAL defaults to measurement; ASSUMPTION to none. A
  datasheet terminal is satisfied only with every covering gate ok **and** a verified,
  extracted DATASHEET or STANDARD artifact grounding the claim or a param its gates read.

```jsonc
// results/C5.json = {"results": [<entry>, …]}, append-only (Q2.11); one entry shown.
// Written only by the signing channel; the P3 hook denies agent edits.
{"passed": true, "when": "<edge clock>",
 "who": "<git identity>", "authority": "",               // authority required when terminal == human
 "method": "outdoor rack, two winters, visual + 15 N re-test",
 "measured": null, "units": "",
 "evidence": ["<input-artifact id>"],                     // ingested as MEASUREMENT: sha256 for free
 "cost": null,                                            // optional {value, units}, typed by the human at /tested,
                                                          // stamped by the channel; else C reads "not measured" (M3.C)
 "card_ready_when": null,                                 // copied from obs by the channel; L = when − card_ready_when (M3.L)
 "built_from": "<rho over depends_on params; default: the whole projection>", "depends_on": [],
 "claim_digest": "<sha256 over statement, kind, acceptance, terminal at signing>",
 "acceptance_digest": "<sha256 over kind, acceptance, terminal>",  // what a fail stays bound to (Q2.11)
 "channel": "terminal",                                   // derived: terminal | slash | agent-session <id>
 "prev": "",                                              // the previous entry's digest; "" for the first (Q2.11)
 "digest": "<sha256 over util.canonical_json of every other field>"}
```

- **Signed (D-13)** means: **sealed** — the digest verifies; **attributed** — `who` is set,
  and `authority` when the terminal is human; **bound** — `claim_digest` matches (the
  goalposts cannot move after signing) and `built_from` matches the current ρ, subject to
  R-3; **evidenced** — at least one ingested file for a measurement, bytes re-verified by
  `artifacts.verify`; **consistent** — a finite `measured` agrees with `passed` under the
  acceptance. `util.canonical_json` is moved out of `modelio._canonical` and both use it.
  The docstring says plainly: tamper-evidence against drift and a helpful agent's
  shortcut, not a lock against a hostile same-user process.
- **R-3 expiry:** a VERIFIED result on a different `built_from` resolves UNVERIFIED ("last
  verified on <rho12>"); a REFUTED result survives any change.
- **`claim physical` becomes the signing channel (D-12).** It accepts any claim whose
  terminal is measurement or human. `who` comes from the git identity; with no identity it
  refuses. `--who` and `--when` are removed (`cli.py:3374-3375`; S-48); `--authority`
  names the institution; `--cost VALUE UNITS` is added (M3.C); evidence stays the
  repeated `--evidence PATH` it is today (`cli.py:3377`). The **channel is derived, never a flag**: `terminal` when stdin is
  a TTY, the human typed the claim id to confirm, and `CLAUDE_CODE_CHILD_SESSION` is unset;
  `agent-session <CLAUDE_CODE_SESSION_ID>` otherwise, recorded **unsigned**. From P3 the
  `/tested` expansion hook is a human channel (`slash`). Evidence must exist and must not
  be under `.nopekit/out/`. A pass on a claim with no acceptance and no procedure note
  (`Claim.note`, P1.3) is refused — which is how C5 got "verified" in the same second it was claimed. The
  report's "record the result" line prints the real command (`report.py:617-619`).
- **Physical L (M3.L, E7).** `check` stamps, in untracked `.nopekit/obs/cards.json`, the
  first edge-clock time at which each measurement or human terminal's card became ready
  at the current ρ — `claims.blocking()` empty with that terminal awaiting a result; the
  signing channel copies it into the result as `card_ready_when`. L is then `when −
  card_ready_when`, measured, and "not measured" when this machine never saw the card
  ready.
- **`report.HUMAN`** (D-16): one table mapping each ClaimStatus, terminal and (from P3)
  disposition to exactly one phrase — verified / failing / needs a real part / assumed /
  not yet checked — <why>; the terminal phrases are the paper's, with `none` said one way,
  "nothing can check this yet" (M14.1). Its first consumers are REPORT.md and `export`.
- **`report.readiness(...) -> Readiness {sentence_1, sentence_2, ready_to_build,
  ready_to_export, fold, fold_words, counts, by_terminal, as_of, records_digest, refusals}`** is the
  one object REPORT.md, `render_terminal`, the site, `export` and (P3) `status --short`
  read; `_verdict_sentence` becomes a thin wrapper. `as_of` is the newest stored
  timestamp, never the clock. `fold` ∈ {`no`, `real_part_only`, `yes`} answers P4's "Can I
  build this?": `real_part_only` iff every export refusal is an unsigned measurement or
  human terminal awaiting a result. `fold_words` carries its phrase from `report.HUMAN`, so
  the page neither maps nor computes.
  Sentence 1 is the first match of a fixed branch table (no claims / never checked / not
  ready — grouped by fixed severity, ids capped at 4 plus "+N more" / passes every check
  that can run here, not exportable / ready to export). *Why 4:* past four ids a sentence
  becomes a list nobody reads as a sentence, and the terminal table below carries every
  id; *rejected:* no cap. Sentence 2 always states what is not established: "It is
  unverified in physical hardware (C5)"; "awaits <authority>'s judgment (…)"; "nothing can
  check C6 yet"; or, when everything is satisfied, "Nothing outside these N claims has
  been checked" (§18).
- **`claims.export_refusals(index, registry, states) -> list[Refusal]`** is pure and covers
  critical claims only. It refuses on the union of: a blocking status (after D-01 this
  includes the old PARTIAL, and a REFUTED result of any signature); **any claim whose
  `TerminalState.satisfied` is false** — terminal `none`, a measurement or human terminal
  without a valid signature, a datasheet terminal without verified, extracted evidence; a
  stale verdict; a gate admitted only reject-only; and, from P5, a claim with no scored
  covering gate (M18.4) — a LOGGER or INSENSITIVE pair already refuses through admission
  (P5.1), and an inconclusive, crashed or not-mutation-tested pair is reported, never
  refused (invariant 15). `Refusal = {claim, kind, reason}`, `kind` ∈ {`blocking`,
  `terminal_none`, `unsigned_measurement`, `unsigned_human`, `datasheet_unverified`,
  `stale`, `reject_only`, `no_scored_gate`}, plus `reexecution_differs` and
  `unreproduced_mutation`, which only the `export` command adds after re-running (R-9),
  never the pure function. `fold` is `real_part_only` iff every kind is
  `unsigned_measurement` or `unsigned_human`; each kind's words live in `report.HUMAN`.
- **`nopekit export [--dry-run] [--json] [--out DIR]`** (D-24). It **re-executes every
  covering gate and its control, both halves (R-9)**, and refuses on any difference from
  the cache or any gate the re-run does not admit. On refusal it exits 1
  and writes **nothing**, not even a temp directory. On success it builds in a temp
  directory and `os.replace`s into `.nopekit/export/<name>-<rev>-<records12>/` (ignored):

  ```
  MANIFEST.json   sorted keys, no clock: spine version, commit (git if present), records_digest,
                  per critical claim {status, terminal, by, digest}, sha256 of every file
  REPORT.md       byte-identical to render_markdown at export time
  model.json      the projection
  evidence/       files cited by satisfied terminals and signed results
  outputs/        generator products (generators/*.py, run only after the refusal check passes)
  ```

  A minimal `generators/*.py` hook; the bracket ships one stdlib generator (a side-profile
  SVG) so the refusal withholds a real artifact. `export --dry-run` is `/ready` (D-15).
  `doctor` re-verifies every package's MANIFEST sha256s and names a file edited after
  export.
- **`check`** prints one informational line — `export: would refuse — C5 needs a signed
  measurement; nothing can check C6 yet` — and **its exit code is unchanged**: `check`
  gates the build of a test article (`claims.py:490-496` documents why UNVERIFIED does not
  block), `export` gates the release. JUnit adds one testcase per critical measurement or
  human terminal: `<skipped>` while awaiting, `<failure>` when refuted or badly signed,
  childless only with a valid signature.
- **REPORT.md** at the project root replaces `docs/readiness.md` (D-14), same generator,
  new shape: the two sentences; the fixed limits line from `report.HUMAN` (M18.1), also
  printed by `export --dry-run` under the sentences; counts, `as_of`, `records_digest` (no clock text, no hash
  of the commit that contains it); a **terminal table** — Must be true | Status | Ends in |
  Refused by | As of — with "Ends in" in the paper's phrasings (M14.1); the
  `SECTION_PROVEN` section (heading text per A-11, with an Ends-in column); NOT VERIFIED
  with signed bylines and the channel; gaps, standing constraints, failing; reproduce,
  including `nopekit export --dry-run`. REPORT.md is a **gitignored output** (D-14):
  `init`, and an idempotent ensure-ignore-blocks step that every writing command runs
  before it writes (not only the migration, which never runs again for a project
  migrated in P1), add `/REPORT.md` to the project root's `.gitignore` inside the marked
  block — `V:` a project migrated at P1 gains the line on its next `report --write` — and
  the export package carries a copy. `examples/bracket/docs/readiness.md` is deleted and
  nothing generated replaces it in git; the bracket's test renders REPORT.md into a temp
  dir from the committed records and pins its shape (R-11) — a generated file that is never
  committed cannot drift from its source, which removes S-41's failure mode instead of
  testing for it. `doctor` warns on a leftover `docs/readiness.md`.
- `claims.summarise` gains `ready_to_export` and `by_terminal`; the site title stops saying
  "ready" when only `ready_to_build` holds (`site_template/app.js:82`; S-60; P4 renders).

| Change | Failure it could introduce | Test |
|---|---|---|
| `terminal` | The proposer lowers the bar by declaring a terminal. | `V: DeclaredTerminalCannotLowerTheBar`: physical+closed_form, measurable+human and assumption+measurement raise; a declared solver whose only gate is closed-form is unsatisfied with a reason; `none` is never satisfied; `bom.*` derive datasheet; `modelica.result_claim` derives solver; a claim whose only covering gate is not admitted derives `none`, still blocks, and reads "nothing can check this yet" with the reason — never "failing" or "crashed". |
| Signature | "Signed" read as "identified"; a hand-edited record verifies. | `V: SignedMeansSomething`, one test per violation with everything else valid: no git identity; human terminal with no authority; measurement with no evidence; evidence bytes changed after signing; acceptance edited after signing; a record hand-edited without re-signing; `passed=True` with measured 0.7 against ≤ 0.5; NaN measured; channel edited from agent to terminal. |
| Channel | The agent signs a physical result. | `V: HumanChannelOnly`: `claim physical C5 pass` with `CLAUDE_CODE_CHILD_SESSION=1` is unsigned and export still refuses C5; argparse exposes no `--who`, `--when` or `--via`. |
| R-3 expiry | A refutation laundered by a parameter nudge. | `V:` thickness 7.0 → 7.01 leaves a REFUTED claim REFUTED and blocking; a VERIFIED claim expires to UNVERIFIED; a param outside `depends_on` does not expire it. |
| A person's no | A named authority's refusal blocks nothing because the claim is an assumption. | `V: RefusalByAPersonBlocks`: an assumption with terminal human and a signed human fail is REFUTED, makes `check` exit 1 and is refused by `export`; the same fail recorded unsigned is REFUTED too; a signed pass resolves VERIFIED. |
| Legacy results | Migrated unsigned results keep VERIFIED. | `V:` a migrated result with an empty `who` resolves UNVERIFIED, "recorded before signing existed; re-record it at a terminal with `claim physical`" — the P2 channel; P3.4 switches the words to /tested (§4.6). Check exit codes are unchanged — neither status blocks. |
| Physical L | A human or physical terminal's latency invented, or never measured. | `V: test_terminal.PhysicalLatencyIsMeasured`: a result signed after a `check` that made C5's card ready carries `card_ready_when ≤ when`; one signed with no such check carries null, and C5's L reads "not measured". |
| Export | It writes a partial package on refusal, trusts a forged cache entry, or disagrees with REPORT.md. | `V: test_export.ExportRefuses`: one project per reason, in which only that thing is wrong → exit 1, no export dir, no temp dir left. A forged cache entry (flipped outcome with a self-consistent `out8` and `digest`) is refused by re-execution; so is a logger behind a hand-placed control entry saying `admitted: paired`, whose re-run control does not fire, and (P5) a hand-placed mutation entry claiming a kill the re-run does not reproduce. A refused run leaves an older package untouched, and a generator that writes a sentinel is never called on it. Two exports are byte-identical; `REPORT.md == render_markdown`; editing one file in a package makes `doctor` name it. |
| Second result | A retest overwrites a refutation: `/tested C5 pass` after a fail, on the same part, erases the no (R-3, invariant 11). | `V: ResultsAreAppendOnly`: a fail, then a signed pass on the same `built_from` → REFUTED; a fail, a thickness nudge, an unsigned pass → REFUTED; a fail, then a signed pass on a different object → VERIFIED there, and REFUTED again when the design returns to the failed object; a statement-only edit of C5 leaves a fail REFUTED, and an acceptance edit shows it "refuted under the previous acceptance" with trade agreeing; removing or editing an earlier entry breaks the `prev` chain and is refused naming the file; the channel never rewrites an entry. |
| Readiness | "Ready" said while export refuses. | `V: RenderersAgree.readiness`: "ready to export" appears iff `export_refusals == []`, over ~500 seeded random small ledgers; every critical `none` claim is named in a sentence; "unverified in physical hardware" appears iff an unsatisfied measurement terminal exists; `fold` is `real_part_only` iff every refusal is an unsigned measurement or human terminal, so a project refused only for an unowned critical assumption has `fold == "no"`; the bracket's two sentences are a golden. `V: ReadinessShape`: with every critical claim satisfied, sentence 2 is "Nothing outside these N claims has been checked" with N the claim count, and it appears in no other case. |
| REPORT.md | Invariant 4 goes silently vacuous; a generated file gets committed again. | Guarded since P1.0 (`SECTION_PROVEN`). The bracket's REPORT.md, rendered from committed records into a temp dir, matches its shape, its two sentences and the limits line, which `export --dry-run` prints verbatim too (R-11); `git ls-files` lists no `REPORT.md` and no `docs/readiness.md`. |

**Refuter targets for Phase 2.** Get a claim to PASS with a covering gate that did not run.
Get a dependent's verdict counted when its prerequisite failed. Close a `needs` cycle
without refusal. Get an always-fail or always-crash gate admitted. Use the `extra`
channel to tell a control from real input. Move a goalpost from a candidate-owned file.
Export with an unsigned, stale or moved-goalpost result. Launder a refutation. Make the
readiness sentence say "ready" while export refuses.

**Done criteria for Phase 2.** G1–G6; the blocking-only-grows oracle, with every status
change on the corpus listed with its reason; all 54 bundled gates and the 6 bracket gates
admitted *paired*; the bracket's exact failure signature (G4) unchanged; CLAUDE.md gains
invariants 9 (paired where a known-good exists, reject-only reported), 10, 11 and 12
(readiness/export); the commit names S-03, S-04, S-06, S-17, S-18 (margin half), S-35,
S-41, S-45, S-46, S-48–S-62.

**Open questions for the Phase 2 judge panel.**

| Q | Question | Recommended default | Rejected, and why |
|---|---|---|---|
| Q2.1 | Does a *skipped* prerequisite, not only a failed one, make dependents unknown? | Yes (D-03); the brief's "prerequisite failed" only when the root failed, "not established" otherwise. | Failed only (wastes the same money). |
| Q2.2 | A new ClaimStatus UNKNOWN? | No (D-02). | Touches every status table. |
| Q2.3 | May a gate need a gate from another pack? | Packs: own gates only (validate); project `gates/`: any; unresolved at sweep → unknown "not registered". | Cross-pack everywhere makes pack load order semantic; refusing at load turns a missing pack into a crash. |
| Q2.4 | New `tested` command, or extend `claim physical`? | Extend `claim physical` (D-12). | A second name for one channel, and one more record writer. |
| Q2.5 | Does REPORT.md replace `docs/readiness.md`? | Replace; the heading's text per A-11 (D-14). | Both: two answers to one question, one already drifted. |
| Q2.6 | Does the static cross-check require quantity **and** units to match? | Yes; it only ever fails. | Every covering gate's measurement: C4 would compare a 7 mm wall with 204 mm of bed. |
| Q2.7 | Should export refuse human sign-offs recorded through the agent channel? | Unsigned already refuses (the agent channel records unsigned). Answered in P3.4: a `slash` result counts for `terminal: human` when it names `by <authority>` and the session is not nested. | Treating agent-channel results as signed: the proposer would sign its own terminal. |
| Q2.8 | May a claim limit reference a projection key (`{"from": "usable_bed"}`)? | No (D-10); disagreements are reported instead. | It hands the goalposts to the candidate-owned model. |
| Q2.9 | Do `modelica.solution_valid` and `mirror_agrees` also declare `refuser="solver"`? | Yes if the claims they cover are about the simulated system; the panel checks each claim tag. | Deriving it from tier alone calls every result reader closed-form (S-62). |
| Q2.10 | Should an unestablished prerequisite *also* be reported in JUnit as a distinct type? | `<skipped message="unknown: prerequisite failed: …">`, no `type` attribute (Surefire accepts only `message`). | A custom attribute some consumers reject. |
| Q2.11 | What happens when a claim that already has a result gets another? | **Append, never replace.** `results/<claim-id>.json` is `{"results": [entry…]}`; the channel appends, never rewrites or removes an entry, and each entry's `prev` carries the previous entry's digest inside its own, so a deleted or edited entry is refused naming the file. A fail entry, signed or not, refutes while its `acceptance_digest` matches the current claim — a statement edit never clears a no; only a later **signed** pass on a different `built_from` supersedes it, and for every object but the one that failed. After an ask-first edit of the acceptance, kind or terminal the fail is kept and shown as "refuted under the previous acceptance", and the claim awaits a result — 3.1's return condition, which also gains "a later signed pass supersedes it", so trade and `resolve_status` agree (invariant 12). VERIFIED needs the newest signed pass bound to the current object (R-3). A migrated `physical_result` becomes the first entry. | Overwrite in place: a retest of the same part until it passes launders R-3 and invariant 11. One file per result under `results/<claim-id>/`: merges more cleanly, but moves the path D-11, the permission table and the tests name; the panel may take it if every property above holds. |
