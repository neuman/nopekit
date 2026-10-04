<!-- Tier 3 of docs/PLAN.md. Moved verbatim from PLAN.md §7. -->

# 7. The "slipped through" register

Defects found during the phase-0 read. They are the material for each phase's commit
message, in the modules' voice: what slipped through, and why. **Seen** records how each
was established: `run` — reproduced by executing the code on a scratch copy (the repo was
never written); `read` — established from the source at the cited lines. A `read` row is
reproduced by a failing test before its fix lands (R-12). **Ph** is the checkpoint that
closes it.

### Admission, verdicts, the honesty invariants

| # | What slipped through | Evidence | Seen | Ph |
|---|---|---|---|---|
| S-01 | A non-bool `passed` counts as a pass: `{"passed":"false"}`, `{"pass":"no"}` and `Verdict(passed="no")` all render `[ok]` with `ok=True`. | `gates.py:950` `bool(data["passed"])`; `:895` | run | 1.0 |
| S-02 | A non-numeric `measured` (`"n/a"`) is filed as a measurement: `_reject_non_finite` skips anything `float()` rejects. | `gates.py:1013-1016` | run | 1.0 |
| S-03 | One pass plus one skipped or never-run covering verdict resolves PASS and is not blocking — against `ClaimStatus.PASS`'s own comment and the resolver's docstring ("all ran and passed"). | `claims.py:231-248` vs `models.py:62`, `claims.py:195` | run | 2.1 — closed in P2.1 (`claims.compose`; pass beside a skip reads Skipped, beside an unrun gate Open) |
| S-04 | `gate selftest` tests only the reject half: an always-False validator passes, and so does an always-crash one with `expect="error"` (54/54 pack gates, 6/6 bracket gates, as wrapper mutants). | `gates.py:1418-1582`, `:1544-1556` | run | 1.1 (packs), 2.3 |
| S-05 | A logger with a declared control produces PROVEN rows; `gate selftest` exiting 1 changes nothing, because `check`, the resolver and the report never read a selftest. The PROVEN row showed 0.6997 mm against "≤ 0.5 mm". | `cli.py:901-1072`; `claims.py:162-248`; no `#selftest` read in claims, report or site | run | 1.2, 2.4 |
| S-06 | The control channel can be told apart: a gate that fails iff `ctx.extra` is non-empty passes its dict-fixture control and every real input. | `gates.py:1510-1513`; `cli.py:697` (`extra={}`) | run | 2.3 |
| S-07 | An identity fixture (`return ctx`) for `bracket.deflection` is reported "correctly failed … ~64x worse: 0.700 mm", because the fixture rebuilds from the host config and the live design already fails. The control demonstrates nothing about its one change, and its note is false. | `examples/bracket/selftest/bad_configs.py:25-36` | run | 1.2 (D-27) |
| S-08 | `gate show` always prints "last selftest: (never run)": it reads a ledger key `gate selftest` deliberately never writes. | `cli.py:1680, 1702` vs `1724-1726, 1755-1760` | run | 1.2 |
| S-09 | `gate selftest` at the repo root exits 2 although `CLAUDE.md:99` and `CONTRIBUTING.md:44` give it as the merge check; `pack validate` never runs a control and certifies a planted `return True` as publishable. | `cli.py:1728`; `packs.py:1139-1157` | run | 1.1 |
| S-10 | The docs and the pack-authoring skill name `pack new` and `pack export` ("with the selftest evidence attached"); argparse answers "invalid choice". | `skills/pack-authoring/SKILL.md:29, 215`; `docs/EXTENSION_PROTOCOL.md:153-155`; `docs/PACK_FORMAT.md:498-500` | run | 1.1 |
| S-11 | PACK_FORMAT says CI runs `gate selftest` over every pack. It does not. | `docs/PACK_FORMAT.md:492`; `.github/workflows/ci.yml` | read | 1.1 |
| S-12 | The pack baseline test `continue`s past skipped verdicts while PACK_FORMAT says CI asserts "nothing skips"; the control test likewise accepts a control that skips on its own known-bad input as "honestly blocked", so a fixture that deletes a needed key passes invariants 3 and 6. | `tests/test_packs.py:164-165, 203-205, 262-263`; `gates.py:1537-1542`; `docs/PACK_FORMAT.md:365-366` | read | 1.0 |
| S-13 | `_own_copy` says post-registration widening is closed, but `specs()` and `get()` hand out the stored spec, so `specs()[0].claims.append(…)` widens what the next verdict settles. | `gates.py:429-459, 600-615` | run | 1.0 |
| S-14 | The guards that carry admission have no violation tests: fixture-less refusal, `expect` validation, `_own_copy`, the `run_all` re-check, every selftest branch. Only the None-control refusal is tested. | `tests/test_invariants.py:179-220` | read | 1.0 |
| S-15 | The invariant-4 tests are latently vacuous: `_proven_section` returns `""` when the heading is missing, so `assertNotIn` passes on an empty string. | `tests/test_invariants.py:238-239` | read | 1.0 |
| S-16 | Errored verdicts fold into FAIL and no API separates "refuted" from "crashed": right for blocking, wrong for anything that derives "infeasible". | `claims.py:241-244` | read | 3.1 |
| S-17 | The reference project teaches the fixture margin a shipped pack records as REJECTED: the bracket's control sits 89.6× past its limit, so a limit 10× too loose is still admitted. | `examples/bracket/gates/structural.py:51` vs `packs/beam-analytic/selftest/bad_beams.py:63-65` | run | 2.3 |
| S-18 | A rounded `measured` can contradict `passed` at the boundary: a FAIL carrying measured == limit. | `examples/bracket/gates/structural.py:65` `round(d, 4)` | run | 2.4 (margin) — closed in P2.4 (`claims.margin`: a side that contradicts the pass flag is `disagrees`, the verdict wins; `bracket.deflection` judges the value it reports; `test_goalposts.Margins`) — and the review of P2.4 found the other five bracket gates still judging the unrounded value beside the rounded one they report (load_n 1155.03: `[FAIL] 15.00 MPa … allowable 15.0 MPa`): all six now judge the numbers they report (`TheBracketJudgesWhatItReports`), 5 (mutation) |
| S-19 | Project controls rebuild through the proposer's own model, so a model bug can defuse them: 11 of 348 control runs stopped firing under formula mutants. | `examples/bracket/selftest/bad_configs.py:18-31` | run | 1.2, 5 |

### Staleness and ρ

| # | What slipped through | Evidence | Seen | Ph |
|---|---|---|---|---|
| S-20 | A first sweep run with `check --only` never sets `last_run`, so its verdicts can never go stale: C3 stayed PROVEN at 0.195 MPa after `load_n` became 20000, where a re-run gives 259.74 against 15. | `cli.py:403-405, 989-990` | run | 1.2 |
| S-21 | A model that fails to import disables staleness: status and report said "unchanged" and listed 3 PROVEN claims for a design that cannot be built. | `cli.py:407`, `813-817` | run | 1.2 |
| S-22 | Files a gate opens are in no staleness key; `inputs_hash` hashes digests stored at ingest, not bytes. A limit file changed under a project gate and C4 still read pass, where a re-run FAILs 19.6 g vs 1 g. | `artifacts.py:645-660`; `modelio.py:607-620` | run | 1.2, 1.3 |
| S-23 | Editing a claim's acceptance leaves verdicts fresh, although openmodelica reads that limit from `ctx.ledger`. | `packs/openmodelica/gates/modelica.py:181-187, 256` | read | 1.2 |
| S-24 | `ctx.params` is one mutable dict shared by every gate, so one gate can forge the next gate's inputs; `ctx.ledger` got a defensive copy for exactly this attack, and `params` did not. | `cli.py:674-698`; `gates.py:1080` | run | 1.2 |
| S-25 | `_ParamReads` records nothing for bulk access (`dict()`, `{**}`, `json.dumps`, `.items()`, `repr`, `deepcopy`); as a ρ read set, bulk readers would be permanently fresh. | `cli.py:523-525` | run | 1.2 |
| S-26 | After a same-size, same-second edit, gate modules and pack helpers run stale bytecode: source 8.0, verdict from 7.0. `_FreshLoader` fixed this for the model entry only. | `cli.py:271-281`; `packs.py:683-693`; `modelio.py:354` | run | 1.2 |
| S-27 | fdm-print's cross-gate mesh cache on the shared `ctx.extra` makes the second gate's file read invisible (a cache hit does only `os.stat`, which no audit event reports). | `packs/fdm-print/gates/fdm_print_fold.py:276-331` | read | 1.2 |
| S-28 | The staleness rule and `_flat_params` exist twice, in cli and site, kept in sync by a comment. | `cli.py:363-414` vs `site.py:1900-1958` | read | 1.2 |
| S-29 | 1e09113 changed verdict semantics (a NaN verdict that read `[ok]` now errors) without a version bump; a cache keyed on the version string would keep serving the old passes. | `src/atompipe/__init__.py:24`; `git show --stat 1e09113` | read | 1.2 |
| S-30 | An availability-skipped gate never calls `fn`, records no reads, and loses its `Param.gates` attribution on every full sweep, though the docstring says a skipped gate "still counts". | `cli.py:587-590` vs `gates.py:1066-1072` | run | 1.2 |
| S-31 | The run history mixes `<gate>#selftest` rows with sweep rows, and no latency reader filters them. | `examples/bracket/.atompipe/runs/0005-*.json` | read | 1.2 |
| S-32 | `check --no-record`, the documented dry sweep, reports fresh passes as STALE, because the global clock did not advance. | `cli.py:987-993` | run | 1.2 |
| S-33 | Ingesting one unrelated artifact made every measurable claim STALE, through the global inputs hash. | scratch: `ingest ds.txt` → C2–C4 STALE; `cli.py:411-413` | run | 1.2 |
| S-34 | openmodelica puts wall-clock time into `Verdict.detail`, so identical inputs give different bytes: 12 false "misses" in the E4 probe, and add/add conflicts under a content-addressed cache. | `packs/openmodelica/gates/modelica.py:1252, 1386` | run | 1.2 |

### Records, provenance, rule 2

| # | What slipped through | Evidence | Seen | Ph |
|---|---|---|---|---|
| S-35 | A claim's limit and its gate's limit are two copies nothing compares: `DEFLECTION_LIMIT_MM` duplicates C1; C3 and C4 are snapshots of derived values. With `bed_xy` at 250 the report printed "≤ 204.0 mm" beside a gate that used 234, and `doctor` showed 0 warnings. | `examples/bracket/gates/structural.py:34-38`; `claims.py:241-248` | run | 2.4 — closed in P2.4 (`ctx.acceptance`: deflection reads C1, `DEFLECTION_LIMIT_MM` gone; `claims.cross_check` fails a value outside its claim's condition; a limit that parts is a `check` warning and a `doctor` row; `TheGoalpostLivesInClaims`, `AValueOutsideTheAcceptanceIsNeverChecked`, `LimitsLiveInOnePlace`) |
| S-36 | Deleting an extraction leaves its grounding forever: `why arm_length` says "GROUNDED BY arm" while `inputs` says arm was "NEVER READ". | `cli.py:436-470` | run | 1.3 |
| S-37 | `claim edit --gates X` binds nothing, and the next `check`'s `_refresh_coverage` silently reverts it. | `cli.py:473-491`; `claims.py:114-134` | run | 1.3 |
| S-38 | A `rejected` entry added to the model's PARAMS after the param exists never reaches the ledger: rule 3's highest-value field dropped silently. | `modelio.py:846-856` | run | 1.3 |
| S-39 | `why` reports the ledger's duplicated value, not the model's: "thickness = 7" after the model says 8.0. | `decisions.py:556-605` | run | 1.3 |
| S-40 | A typo'd key in a hand-edited ledger is dropped on load, then **erased from disk** by the next `check`. | `models.py:224-237`; `cli.py:987-991` | run | 1.3 |
| S-41 | The committed readiness report is older than the committed ledger it claims to render, and nothing tests a generated document against its source. | `examples/bracket/docs/readiness.md:1` (2026-09-11T23:31) vs the ledger's `last_run` | read | 2.5 — closed in P2.5b (`report --write` renders `REPORT.md` at the root, ignored; the bracket's `docs/readiness.md` deleted and named by `doctor` where one is left; `ReportIsAnIgnoredOutput`) |
| S-42 | The reference model promises "every parameter carries … what was rejected (see PARAMS)"; no PARAMS exists and all 12 params have `rejected: []`. | `examples/bracket/model/bracket.py:9` | read | 1.3 |
| S-43 | `atompipe gap`, which reads like a query, rewrites the ledger on every run. | `cli.py:1536-1547` | read | 1.3 |
| S-44 | `decide --when` backdates a decision, and the log renders in storage order. | `cli.py:3430`; `decisions.py:260` | read | 1.3, 3.2 |
| S-45 | Ingested evidence bytes are never re-verified; after tampering, `doctor` still said `[ok] staleness unchanged`. | `artifacts.py:402-405, 645-660` | run | 1.3, 2.5 |
| S-46 | C2's quantity ("utilisation") does not match its gate's `settles` ("bending stress"); C4's PROVEN row mixes in `min_wall`'s unrelated 7 mm. | the bracket ledger C2, C4; `structural.py` `settles=` | read | 2.4 — closed in P2.4 (C2 speaks `bending stress` in `utilisation`; the checked table's *Value* holds only compared values and lists `min_wall`'s 7 mm as not compared; `ValuesAreComparedWithTheirClaim`) |
| S-47 | NaN and Infinity reach `state.json` and `ledger.json` through `atomic_write_json`'s default; `JSON.parse` rejects the file and the page advises `site build`, which cannot fix it. | `util.py:227` | run | 1.0 |

### The refusal graph, terminals, the report

| # | What slipped through | Evidence | Seen | Ph |
|---|---|---|---|---|
| S-48 | `claim physical` recorded an unattributed pass with a nonexistent evidence file on C5, a claim the report calls unsettleable — two winters outdoors, "verified" in the same second. `--when` backdates. The report's "record the result" line omits any attribution. | `cli.py:1466-1519, 3374-3375`; `report.py:617-619` | run | 2.5 — closed in P2.5a (`who` is git's identity, `--who`/`--when` refused before the project is read; a pass on a physical claim needs a test written down and existing evidence, and counts only typed in a person's own shell; the report prints the real command; `test_signing.WhoAndWhenAreNeverTyped`, `HumanChannelOnly`, `test_physical.SignedMeansSomething`) |
| S-49 | A PHYSICAL claim whose covering gate FAILED resolves UNVERIFIED; `blocking()` is empty; the headline says it "clears every critical gate that is installed". | `claims.py:208-212`; `report.py:473-476` | run | 2.1 — closed in P2.1 (a physical claim composes its automated evaluators first: a covering fail reads Failing and blocks) |
| S-50 | A VERIFIED physical result survives any model change, and the acceptance can be moved after signing with the claim staying VERIFIED. | `claims.py:208-212`; `models.py:343-352` | run | 2.5 — closed in P2.5a (a pass is bound to its article — the design at recording, files it names included — and to the claim as read; either moving reads Stale, the article named for rebuild; `test_physical.AMovedArticleReadsStale`, `RebuildPredictionIsExact`) |
| S-51 | A validity guard does not protect a narrowly tagged claim: a claim tagged only `deflection` resolves PASS on a beam whose guard reports Euler-Bernoulli omitting 32% of the deflection. | `packs/beam-analytic/gates/beam.py:728-735`; `docs/PACK_FORMAT.md:164-170` | run | 2.2 — closed in P2.2 (`GateSpec.needs`; the claim reads Skipped, `prerequisite failed: beam.model_validity`; `GuardsReachNarrowClaims`) |
| S-52 | The registry's "cheapest-and-most-fundamental first" order is prose, and 4 of 7 bundled packs (beam-analytic, fdm-print, fluids-analytic, thermal-analytic) break it. | `gates.py:480-486`; `packs.py:539` (`_gate_files` sorts alphabetically) | run | 2.2 — closed in P2.2 (run order is `gates.plan`'s: each guard before what it guards; `PlanIsStable`) |
| S-53 | Three incompatible in-body conventions for "prerequisite not established": `cad.wall_thickness` SKIPs (so its claim reads "missing tooling"), `cad.clash` FAILs, the fdm mesh gates FAIL. | `packs/cad-solid/gates/solid.py:996-1001, 1445-1451`; `packs/fdm-print/gates/mesh.py:368-372` | read | 2.2 — closed in P2.2 for cad (`wall_thickness`, `clash`, `assembly_connected` read the prerequisite skip; `MeshGuardsPrecedeTheirDependents`, trimesh); fdm's in-body mesh FAILs remain — no mesh-validity guard in fdm-print, and a pack may not need another's gate (P2.2-D14) |
| S-54 | BLOCKED is explained everywhere as missing tooling, and the skill says "install the tool" — while packs self-skip for missing *parameters*. | `models.py:66`; `report.py:122`; `cli.py:1105-1106`; `skills/atompipe/SKILL.md:187-191` | read | 2.2 — closed in P2.2 (Skipped's hint and the skipped chip name the tool, a self-skip and a prerequisite; the skill says what to do for each, and — after the review of P2.2, which found "install its tool if it skipped" on a guard that skipped itself on a missing bbox — for each kind a prerequisite skip names; a claim names a skip behind a failed guard before a missing tool beside it; `PrerequisiteWords`, `AnErroredPrerequisiteStaysLouder`) |
| S-55 | `test_every_pack_ships_a_validity_guard` passes on a keyword ("valid", "guard", "is_volume") and never checks that a guard guards anything. | `tests/test_packs.py:86-91` | read | 2.2 — closed in P2.2 (structural: some gate is another's prerequisite; `test_every_analysis_pack_declares_a_prerequisite`, with its planted keyword-only guard) |
| S-56 | Tier is declared and never checked against measured duration; `by_tier`'s docstring admits a mislabelled tier cannot be caught. | `packs.py:1182-1197`; `gates.py:642-652` | read | P4 (moved from 2.2: measured latency and cost are what sequencing consumes) |
| S-57 | The bracket's gate docstring names `beam.deflection` and `beam.model_validity` for gates whose ids are `bracket.*`. | `examples/bracket/gates/structural.py:14, 22` | read | 2.2 — closed in P2.2 (`BracketHeaderNamesItsGates`) |
| S-58 | `fdm_print_fold.py` is a helper without the leading underscore, so it is imported as a gate module — harmless today, and it breaks the documented rule. | `packs/fdm-print/gates/fdm_print_fold.py`; `packs.py:539` | read | 2.3 (moved from 2.2: the rename re-keys every fdm gate that loads the helper and moves `test_staleness.GateVersionRows`' localisation table, a pinned table P2.2 did not otherwise touch; P2.3 re-qualifies every bundled gate with its paired controls, so the row moves there with its reason, R-6) |
| S-59 | The headline never mentions the critical assumption C6; with only physical evidence it contradicts itself ("never been gated … 1 physical claim has been confirmed"). | `report.py:404-498` | run | 2.5 — closed in P2.5b (the sentence's head pinned by `TheProjectSentenceHeadIsUnmoved`; *ready* one predicate, `ReadyIsOnePredicate`; the hardware clause in every branch, `TheHardwareClauseIsAlwaysSaid`) |
| S-60 | The site's tab says "ready" whenever nothing blocks, including when critical physical claims are unverified and critical assumptions unowned. | `site_template/app.js:82`; `claims.py:560-562` | read | 2.5 (fields) — the field half closed in P2.5b (`state.json` `readiness.milestones`, from `claims.unresolved`); 4 (the page) |
| S-61 | The `Tier.EXTERNAL` comment lists "a human with calipers", a second route for what PHYSICAL plus a result already owns. | `models.py:92` | read | 2.5 — closed in P2.5a (`test_physical.APhysicalResultIsNoTier`, red first) |
| S-62 | `modelica.result_claim` is tier 0 with no tools yet judges solver output, so a terminal derived from `GateSpec` alone would call it closed-form. | `packs/openmodelica/gates/modelica.py:716-733` | read | 3 (moved from 2.5 with `GateSpec.terminal` and `terminal-unmet`, P2.5b-D24) |

### Tradespace, the agent channel, the site

| # | What slipped through | Evidence | Seen | Ph |
|---|---|---|---|---|
| S-63 | Evaluating a second tree in one process reuses the first tree's `bracket` module: a tree whose model can never fail reports "correctly failed", while its own process reports BROKEN. | `examples/bracket/selftest/bad_configs.py:18-22` | run | 3.1 |
| S-64 | `find_root` has no repository boundary: from a worktree nested inside a project it resolves to trunk; and any `.atompipe/` directory is a marker, so `~/.atompipe/` (the user-pack home) makes every directory under `~` a project. | `store.py:149-169` | read | 1.1 |
| S-65 | `NoLeakedProvenance` walks into nested checkouts (any worktree inside the repo turns it red) and never scans `.js .html .css .yml .sh`. | `tests/test_packs.py:281-285` | read | 1.0 |
| S-66 | The `next:` line is a constant: the same three commands right after a clean check. | `report.py:1114-1115` | read | 3.3 |
| S-67 | The skill's plugin recipe uses unbraced `$CLAUDE_PLUGIN_ROOT`; Claude Code substitutes only the braced form and the Bash tool lacks the variable, so `PYTHONPATH` becomes `/src`. | `skills/atompipe/SKILL.md:22, 30` | run | 3.4 |
| S-68 | `status` cites a skipped gate as the reason a claim FAILS while `check` cites the failing gate; the fix in `_blocking_reason`'s docstring never reached `report._terminal_reason`. | `report.py:1131-1134` vs `cli.py:1075-1112` | run | 1.0 |
| S-69 | Claim statuses are spelled three ways: `[fail]`/`[uncl]` by `check`, `[FAIL ]`/`[gap  ]` by `status`, PROVEN/NO GATE on the site. | `cli.py:1071`; `report.py:85-96, 148-157`; `site_template/lib/format.js:23-34` | run | 1.0 (tags), P2.1 (every status word on the CLI, the report, JUnit and the page from `report.HUMAN`, GLOSSARY §3), 3, 4 |
| S-70 | `ask` is claim-blind: on the bracket it asks for a napkin sketch while C1 fails and C5 needs a real part, and prints CLI syntax in lines its docstring says are for a human. | `artifacts.py:666-712`; `cli.py:1115-1157` | run | 3.3 |
| S-71 | No non-blocking way to get a site URL: `site serve` blocks forever and the page cannot load over `file://`. | `cli.py:2717`; `site_template/app.js:48-52` | read | 3.4 |
| S-72 | `status` imports project gates and builds the model on every call — arbitrary project code a Stop hook would run every turn. | `cli.py:813-817` | read | 3.3 |
| S-73 | Nothing runs `claude plugin validate` on `plugin.json`. (Its missing `version` is right, and stays: a version string pins installed users to their cached copy until it changes.) | `.claude-plugin/plugin.json` | run | 3.4 |
| S-74 | The skill's list of statuses that make `check` exit non-zero omits REFUTED and the zero-claims case. | `skills/atompipe/SKILL.md:212`; `models.py:74-78`; `cli.py:996-1010` | read | 3.4 |
| S-75 | "proven" is said to the human for a machine pass. | `site_template/lib/format.js:24`; `README.md:48` | read | 3.3, 4 |
| S-76 | Nothing under `.atompipe/cache/` is gitignored, so any state stored there dirties the tree. | `store.py:86-102` | run | 1.3 |
| S-77 | On a FAILING claim the claims panel shows a *passing* gate's measurement: C1 reads 8.57 L/h instead of 0.70 mm. | `site_template/lib/panels.js:170-172` | run | 4.1 |
| S-78 | Diagram locators never highlight anything, and `locator_problems` reports them fine whenever `meta.elements` declares the target — a gate that thinks it is drawing and is not. | `site_template/lib/stage.js:128, 370-377`; `site.py:1251-1252` | read | 4.3 |
| S-79 | Verdict ages are frozen at build time: a page opened a week later still says "0s ago". | `site.py:1737`; `site_template/lib/format.js` `age` | run | 4.3 |
| S-80 | The import-map probe and `site status` disagree about a partial `vendor/`. | `site_template/index.html:47-51`; `cli.py:2415-2423` | read | 4.3 |
| S-81 | `Param.gates` records direct reads only, so the failing claim's lever (thickness → deflection → C1) is linked only to `min_wall`, and `why arm_length` lists nothing. | `cli.py:596-603`; the bracket ledger | read | 3.3 (`influence`), 4 |
| S-82 | `chart.js` defaults a missing comparator to `<=`: a `>=` check would read "outside the limit" while passing. | `site_template/lib/chart.js:237-240` | read | 4.1 |

### CI, the environment, independence

| # | What slipped through | Evidence | Seen | Ph |
|---|---|---|---|---|
| S-83 | CI swallows the reference project's exit codes (`init … \|\| true`, `check \|\| true`) and runs `model --set-entry` in the tracked directory: a crash and a second failure both look like the one intended failure. | `.github/workflows/ci.yml:74, 75, 78` | read | 1.1 |
| S-84 | CLAUDE.md types the suite size as a constant, "32 tests"; there are 92. | `CLAUDE.md:19` | run | 1.0 (the count is removed, not re-typed) |
| S-85 | `PackManifest.origin` is required, author-typed prose that "the next reader calibrates their trust on" — a proposer-filled trust signal of exactly the kind the brief forbids for independence. | `packs.py:977-982`; `models.py:731` | read | 5.2 (excluded from the derivation) |
| S-86 | The mirror-agreement verdict asserts independence nobody established: with no source stated it reads "mirror is: an independent implementation". | `packs/openmodelica/gates/modelica.py:956` | read | 5.2 |
| S-87 | Latent: `~/.atompipe/packs` outranks the bundled packs, so on a machine with a same-named user pack the suite would test the wrong copy. Not observed here. | `packs.py:178-236` | read | 1.1 (pack mode, tests), 5.3 (sandbox) |
| S-88 | Research hazard: model summaries of the Claude Code hooks docs had two facts backwards (where Stop stdout goes; who sees `systemMessage`). A hook built on the summary would print to nobody. | `hooks.md` "Exit code 0" and the JSON output table | run | 3.0 (process rule) |
| S-89 | The verification suite dirties the tracked tree: in `examples/bracket`, `check` rewrites `.atompipe/ledger.json` and appends a run (`cli.py:986-991`); `gate selftest` appends a run (`cli.py:1755-1760`) and leaves `ledger.json` byte-identical (a scratch copy gained 0006 and 0007). | `store.py:398-445`; `cli.py:986-991, 1755-1760` | run | 1.2, 1.3 (G5) |
