<!-- Tier 3 of docs/PLAN.md: read docs/PLAN.md first (the map, the D-rows, the R-rules and G-criteria this file cites). Moved verbatim from PLAN.md §4.2; S-nn rows live in docs/plan/slipped.md. -->

# 4.2 Phase 2: `needs` and unknown, evidence semantics, `terminal`, export, REPORT.md

**Why this order.** This is where claim semantics change, so the oracle becomes
"blocking only grows" (R-8). Composition comes first (2.1), then the graph (2.2), then
admission's second half (2.3), then goalposts and the cross-check (2.4). Terminals,
signing, export and the report come last (2.5), because export's refusal predicate
consumes all four.

#### Target transcript

```text
$ atompipe check                     # on a copy with arm_length pushed so L/h < 5
[FAIL] bracket.model_validity : slenderness 4.2 (>= 5.0 for Euler-Bernoulli; …)
2 checks not run — prerequisite bracket.model_validity failed: bracket.deflection, bracket.bending_stress
6 gates: 4 executed, 0 cached — 3 ok, 1 FAIL, 2 unknown — tier 0
BLOCKING — 3 critical claim(s) must not be spent against:
[FAIL ] C1 Tip sags … — bracket.model_validity : slenderness 4.2 (…); bracket.deflection not run: prerequisite failed
[FAIL ] C2 Root bending stress … — bracket.model_validity : slenderness 4.2 (…); bracket.bending_stress not run: prerequisite failed
[gap  ] C7 …

$ atompipe export --dry-run          # on the shipped bracket; this is also /ready (D-15)
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
records <records12> · as of the newest result · atompipe <version>

$ atompipe claim physical C5 pass --detail "no cracking"     # from the agent's Bash
error: C5 says nothing a result could fail — write its test into claims/C5.json first
$ …after C5 gains an acceptance, the same call…
recorded C5 pass in results/C5.json — unsigned (agent session <id>): export still refuses C5
```

What changed for the human:

- A check that could not run because another failed says so, names the root, and does not
  say "install a tool" (today every BLOCKED reads *missing tooling*: `report.py:122`;
  `skills/atompipe/SKILL.md:187-191`; S-54).
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
`skills/atompipe/SKILL.md:187-191` (S-54). `describe()` says "after P" for needs and
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
  be under `.atompipe/out/`. A pass on a claim with no acceptance and no procedure note
  (`Claim.note`, P1.3) is refused — which is how C5 got "verified" in the same second it was claimed. The
  report's "record the result" line prints the real command (`report.py:617-619`).
- **Physical L (M3.L, E7).** `check` stamps, in untracked `.atompipe/obs/cards.json`, the
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
- **`atompipe export [--dry-run] [--json] [--out DIR]`** (D-24). It **re-executes every
  covering gate and its control, both halves (R-9)**, and refuses on any difference from
  the cache or any gate the re-run does not admit. On refusal it exits 1
  and writes **nothing**, not even a temp directory. On success it builds in a temp
  directory and `os.replace`s into `.atompipe/export/<name>-<rev>-<records12>/` (ignored):

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
  including `atompipe export --dry-run`. REPORT.md is a **gitignored output** (D-14):
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
