# SPDX-License-Identifier: Apache-2.0
"""The fast tier: every invariant's violation tests, in at most 90 seconds.

The full suite is 10-17 minutes (measured 2026-10-03, one process per module:
~830 s summed, `test_admission` alone 120-150 s). A loop that waits that long
per edit stops running it, and an iteration that runs nothing between edits is
where an invariant breaks unseen. So this tier runs EACH ITERATION, and the full
suite (`discover -s tests`) still runs before EACH CHECKPOINT AND EACH COMMIT.
Nothing here replaces anything there: every entry in ``FAST`` is a test the full
suite also runs, and the only tests that live in this file check the list itself.
What this tier promises is narrower than "green means no invariant is red": at
least one violation test of every invariant runs every iteration, and a break
that only a test in ``LEFT_FOR_THE_GATE`` sees is caught by the escalation rule
below or by the full suite, not here.

The budget is 90 s as ONE process on an otherwise idle machine — and P2.3 is
OVER it, said here rather than hidden. The review of P2.4 added what it cost and
measured no move: two units scans in FAST (0.03 s), ten in-process tests in
AGoalpostIsNeverAKey, which runs whole (~0.6 s), and goalpost runs at four
scales where there were two — measured 2026-10-04 within the hour: 104.8 s wall
(79.1 s user) for 710 tests against 31b84a0's 106.2 s (78.0 s user) for 698
(load average 0.2-2.5). Flat, and still over 90 s; nothing was paid back: the
slowest entries (`--durations`) are the records sweeps and the bundled seal
tests, which this file keeps by choice (below), so the rule stands for the next
change. P2.4 paid back what it added and no
more: 104.7 s wall (79.0 s user) for 697 tests, against 104.9 s (77.6 s user)
for `f2d4754`'s list (660 tests) on the same machine within the same four minutes (load
average 0.3-1.9), measured 2026-10-03 — 107.1 s wall (80.6 s user) before the
pay-back. What P2.4 added: its in-process classes (~0.7 s: a pass held to the
goalpost it read, the comparison in `compose`, the breach rule and its mark,
the malformed contexts refused) and the goalpost runs in every control of a
gate that reads one — two more known-good runs of `bracket.deflection` per
control run, in every CLI test that runs the bracket's controls (+1.5-2 s
spread). Moved out to pay it: test_status_table's three bracket copies of
their own (~2.5 s; named below, with the rule each reads and where it runs in
process). Still over 90 s, so the rule stands: the next change that touches the
fast tier pays the rest back first. The review of P2.3 added its laundering
paths' in-process tests (~2 s: the PACK_DIR line, a known-good-only file and tier
read, a held known-bad pass, the words of an unusable control,
TheCheckRunTakesTheQualifiedPath) and pack mode and JUnit to
QualificationWordsComeFromOneTable's scan (~1 s): 104 s before it, 104-109 s after
over two runs, measured 2026-10-03 within the hour (load average 1.0-1.7; 77 s
user). Before: 101 s measured 2026-10-03 (load average
1.1-1.5; 74 s user) against 90 s for the P2.2 list on the same machine within
the hour (65 s user). What crossed, measured test by test: P2.3's own classes
(~4 s: QualificationIsPaired's in-process cases, the walk's cases, V3, V4, V8)
and qualification's cost in every CLI test that runs a control — a known-good
half and a walk beside each known-bad one — spread at 0.2-0.9 s over some
thirty tests (+6 s). Moved out, named in LEFT_FOR_THE_GATE with their reasons:
the walk judged on real evaluators and the tripwire's scan (~19 s), the
600-key budget case (9.5 s), the early-cutoff and walk-keying cases (2.4 s),
inconclusive across the channels (0.9 s), S-25's bulk readers (2.2 s), and V11
and C12 to the full suite; and the walk's module-closure scan is now made once
per walk (`modelio.closure_scope`: 4.2 -> 1.7 s on the first 60-gate sweep).
Not moved, though each would fit the budget: the records sweeps and the bundled
seal tests — each carries a "what got through without it" above, and a tier
that drops a guard against a slip it once missed to meet a clock is the trade
this file refuses; the next change that touches the fast tier pays this back
first. Before: 89 s measured
2026-10-03 (load average 0.8-1.5; 64 s user) after the review of P2.2 grew
AnErroredPrerequisiteStaysLouder (+1.2 s: the dependent's own skip and refusal
behind a crashed guard, and the ranking within Skipped) and added
ApplyingTheRuleTwiceMovesNothing (0.2 s) — one second under, so the next test
that joins moves one out; 87.5 s measured
2026-10-03 (load average 1.4-1.8; 64 s user) after P2.2 added invariant 10's
classes and its fast neighbours (~3 s, in process), with the matrix, the
transcript and the wrapped-beam cases left to the full suite (below) — at the
budget, so the next class that joins moves one out; 87 s measured
2026-10-03 (load average 1.9-2.3, the quietest this machine got that day; 59 s
user) after P2.1 added four `test_vocabulary` classes, `test_json_keys` and
`test_owner` (the sentence-branch scan 4.3 s, `claim physical` end to end 3.3 s
the slowest) and left V15's seven bracket copies to the full suite — near the
budget, so the next class that joins pays for itself or moves one out; 70-74 s
measured 2026-10-03 (load average 0.8-1.4) after the P2.0 review grew
``test_louder``, ``test_status_table`` (an end-to-end project) and
``test_mutation`` to 4.8 s, 2.7 s and 2.3 s alone, and 85 s for the same list
with other agents loading the machine (load average 1.6-4.3); 66-72 s (load
average 0.1-2.5) when those three joined at 5.0 s, 0.1 s and 1.0 s; 68-71 s
before them (load average 1.3), after the bundled-pack seal
tests and the migrated-project write sweep joined; 54-59 s before those, and
94 s for that shorter list with other agents loading the machine. Re-time it
whenever ``FAST`` changes; a run under load is not a measurement.

**The selection is an explicit list of test names.** Rejected:

* a timing threshold ("everything under 2 s") — the cut moves with the machine,
  and a slower runner silently drops an invariant test from the tier;
* a ``@slow`` mark on the tests left out — forty files edited, and a test marked
  slow by mistake leaves its invariant unguarded with nothing turning red;
* ``discover -p`` patterns — they select modules, and the invariant modules are
  the slow ones; the violation tests inside them are what this tier needs.

Two things keep the list honest:

* a name that no longer resolves is an ERROR, never a silent drop (unittest
  turns it into a failing test), so renaming a listed test turns this tier red;
* ``FastTierHoldsEveryInvariant`` holds the list to ``test_meta``'s map of
  CLAUDE.md's numbered invariants: every invariant class has at least one test
  here, and every test of such a class is in ``FAST`` or ``LEFT_FOR_THE_GATE``.
  A test added to an invariant class, or the class that carries a new
  invariant, turns this tier red until someone decides where it runs and says
  why. Its planted violators show it refusing each of those.

**When the fast tier is not enough.** Before it hands off, an iteration runs
whole the modules that guard what it touched — the CLI scenarios and sweeps left
out below are the channels that code carries:

* what a gate's read set records or how a verdict or a control is keyed (the
  tracer, `sweep` and `admission` in `verdicts.py`; fixture and control loading
  in `gates.py`; the recording loader in `modelio.py`; `FileDigests` in
  `util.py`) -> `test_admission`, `test_staleness`, `test_cache`,
  `test_check_cache` and `test_gate_context`;
* the qualification — `gates.mutation_walk`, `verdicts._run_control`,
  `_qualification` and the strict reader of a control entry (P2.3) ->
  `test_admission` and `test_mutation` whole, and `test_packs`;
* a command's code in `cli.py`, `store.py`, a record writer or a migration ->
  `test_records` (every command on a legacy project, and the shims);
* a pack's fixtures or baseline, the fixture context in `gates.py`, or
  `NegativeControl` -> `test_packs` (with `test_pack_mode`);
* the prerequisite rule — `gates.plan`/`prerequisite_root`/`run_all`,
  `verdicts.apply_prerequisites`/`_pruned_row`, `cli._swept`, `claims.compose`'s
  rung 2 — -> `test_prerequisites` whole (its matrix and transcript are left out
  below) and `test_packs.ControlsAreIsolated`;
* a renderer's code — `report.py`, `Verdict.render`, `cmd_check`,
  `cmd_status`, `cmd_doctor`, `claim list`/`show`, `decisions.why`,
  `site.state` or the site template — -> `test_louder` is in this tier; also
  run `test_renderers`, `test_junit`, `test_shapes` and `test_site` whole;
* a goalpost or an operating context (P2.4) — `GateContext.acceptance`,
  `gates._held_to_acceptances`/`_held_to_context`/`context_breach`/
  `goalpost_runs`, `verdicts._contexted` and the `acceptance:` ledger keys,
  `claims.cross_check`/`margin` and `compose`'s rungs 1, 3 and 7, a pack's
  `comparator=` — -> `test_goalposts` and `test_context` whole (their bracket
  copies run through the commands are left out below), and `test_packs` for a
  pack's.

Run:  PYTHONPATH=src python3 -m tests.fast        (or: python3 -m unittest tests.fast)
"""
from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

TESTS = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(os.path.dirname(TESTS), "src")
# The full suite runs as `discover -s tests`: test modules import as top-level
# names (`_env`, `test_invariants`). The same here, so `test_meta`'s resolver and
# this list name the same module objects. `src` too, for `-m unittest tests.fast`
# typed without PYTHONPATH.
for _path in (SRC, TESTS):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import test_meta  # noqa: E402  (needs the path above)

# Seconds are single-run wall times on the dev box, 2026-10-03.
FAST: list[str] = [
    # -- invariants 1-4, and the guards beside them ----------------------- #
    # Every class tries to make a skip, an error, a NaN, a truthy non-bool or an
    # unrun gate read as a pass, or a logger register. Pure, in-process. 0.2 s.
    "test_invariants",
    # The tests about the tests: each numbered invariant maps to a class that
    # exists and never skips; no subprocess outside `_env.run`, no clock below
    # the edge, no git outside `vcs`. Weakening an invariant test is the cheapest
    # way to green, so this runs every iteration. 2.8 s.
    "test_meta",
    # House rule: the spine is standard library only. CI's own AST walk, run
    # over the spine and over a planted third-party import. 0.3 s.
    "test_stdlib_only",
    # The one place that decides a tool is missing: the line between SKIPPED
    # (BLOCKED) and a run. 0.2 s.
    "test_availability",
    # Every reader of a verdict (`ok`, `render`, the claim ladder, JUnit) says the
    # same thing about it, over every combination of passed/skipped/error: a
    # reader that calls a skip a pass is red here. 1.3 s.
    "test_renderers",
    # JUnit is never greener than the exit code: a skip or an error is never a
    # passing testcase to CI. 0.1 s.
    "test_junit",
    # Invariant 2's second sentence: a crash reads louder than a missing tool in
    # every renderer — in process on the report's own renderers, and through
    # one planted project that runs every command once (the commands cost the
    # time: ~5 s, the in-process half <1 s). With the ratchet that names where
    # today is quieter (ErrorNotYetLouder), and the command tripwire. P2.3's
    # V11 — an unqualified evaluator never wears an outcome tag, on every
    # channel of a project of its own (~2 s) — is left to the full suite: the
    # word routing it reads is held here by `QualificationWordsComeFromOneTable`.
    "test_louder.ErrorIsLouder",
    "test_louder.WorstIsTheMostUrgent",
    # Today's resolve_status table, the report's section order, and invariant 4
    # over a pass beside an evaluator that is not admitted — in process, and
    # end to end on a gate refused at its first check (eight commands, the
    # time: ~2.5 s), with the ratchet of what today's readers overclaim there.
    # P2.4's pay-back took this module apart (it was listed whole): its three
    # bracket copies of their own are left to the gate (below, named).
    "test_status_table.StatusTable",
    "test_status_table.StatusesMoveOnlyTowardUnresolved",
    "test_status_table.BlockingMembers",
    "test_status_table.ReportSectionOrder",
    "test_status_table.UnqualifiedBesideAPassIsNeverChecked",
    "test_status_table.UnqualifiedReadsGap.test_resolve_says_a_refusal_with_no_entry",
    "test_status_table.UnqualifiedReadsGap.test_the_refusal_checks_refuse_today",
    "test_status_table.UnqualifiedReadsGap.test_an_unqualified_claim_never_reads_errored",
    "test_status_table.UnqualifiedReadsGap.test_the_tallies_that_called_it_errored_or_unrun_are_caught",
    "test_status_table.UnqualifiedReadsGap.test_a_compose_that_reads_the_mark_as_a_crash_is_caught",
    # P2.1's vocabulary and JSON: GLOSSARY §3's words from one table on every
    # channel (the louder and refused worlds shared with the two modules above,
    # one process; a bracket, an empty project and an unevaluated one of its
    # own), *ready* as one predicate in process, the JSON keys kept beside the
    # words, and an owner written by hand counting for nothing (planned 11).
    # V15 through the commands — seven bracket copies at 8 mm, ~25 s — is left
    # to the full suite (below): the predicate it reads is held here, in process.
    "test_vocabulary.StatusWordsAreTheGlossarys",
    "test_vocabulary.StatusLinesSpeakTheTable",
    "test_vocabulary.StatusWordsComeFromOneTable",
    "test_vocabulary.ReadyIsThePredicate",
    # P2.3's words (V4): patching `HUMAN["qualification"]` moves every channel
    # that shows a qualification, and none says a §2 Never-say. One bracket copy,
    # every command twice, in process. ~3 s.
    "test_vocabulary.QualificationWordsComeFromOneTable",
    "test_json_keys",
    "test_owner",
    # Invariant 15 (P2.3): the mutation harness — the plan rule, the seal, the
    # planted runners each caught by the rule it breaks, the tripwire's own
    # negative control, and inconclusive never a fail on any channel. ~3.5 s.
    # The spine's walk judged over the bracket's six gates and a bundled pack's
    # eight, the tripwire's scan of the spine (which judges every subject on
    # those gates), and the sweep's own pass are left to the gate (below): ~19 s
    # together, where the whole module was 2.3 s before the walk existed.
    "test_mutation.MutationIsSealed.test_a_conclusive_pass_reported_as_a_fail_is_caught",
    "test_mutation.MutationIsSealed.test_a_line_that_miscounts_honest_results_is_caught",
    "test_mutation.MutationIsSealed.test_a_mutation_that_aliases_the_known_good_design_is_caught",
    "test_mutation.MutationIsSealed.test_a_mutation_that_writes_into_a_pack_is_caught",
    "test_mutation.MutationIsSealed.test_a_mutation_that_writes_into_the_tree_is_caught",
    "test_mutation.MutationIsSealed.test_a_plan_that_breaks_every_type_is_caught",
    "test_mutation.MutationIsSealed.test_a_plan_that_moves_between_calls_is_caught",
    "test_mutation.MutationIsSealed.test_a_runner_that_drops_an_inconclusive_mutation_is_caught",
    "test_mutation.MutationIsSealed.test_a_runner_that_hides_its_survivors_is_caught",
    "test_mutation.MutationIsSealed.test_a_runner_that_omits_the_survivor_from_its_own_plan_is_caught",
    "test_mutation.MutationIsSealed.test_a_runner_that_plans_after_seeing_is_caught",
    "test_mutation.MutationIsSealed.test_a_survivor_reported_as_a_fail_is_caught_whatever_the_gate_returns",
    "test_mutation.MutationIsSealed.test_a_tracer_that_under_records_is_caught",
    "test_mutation.MutationIsSealed.test_a_write_then_restore_is_caught_by_the_hook",
    "test_mutation.MutationIsSealed.test_a_write_then_restore_through_a_directory_fd_is_caught",
    "test_mutation.MutationIsSealed.test_an_honest_line_in_other_words_is_held_by_its_numbers",
    "test_mutation.MutationIsSealed.test_an_inconclusive_mutation_reported_as_a_fail_is_caught",
    "test_mutation.MutationIsSealed.test_bytecode_written_into_the_tree_is_caught",
    "test_mutation.MutationIsSealed.test_every_runner_the_plan_rule_exists_for_is_caught",
    "test_mutation.MutationIsSealed.test_ignored_scratch_under_the_project_is_caught",
    "test_mutation.MutationIsSealed.test_none_conclusive_says_so",
    "test_mutation.MutationIsSealed.test_the_honest_runner_passes_every_check",
    "test_mutation.MutationIsSealed.test_the_ladder_floor_refuses_what_it_forbids",
    "test_mutation.MutationIsSealed.test_the_oracle_agrees_with_the_ground_truth",
    "test_mutation.MutationIsSealed.test_the_oracle_reads_a_gate_as_run_gate_does",
    "test_mutation.MutationIsSealed.test_the_tripwire_refuses_what_it_forbids",
    # Freshness, admission state and the one resolver: what any reader may call
    # current. Invariants 7 and 9 are read through it. 2.1 s.
    "test_freshness",
    # The report and the site keep no staleness rule of their own. 1.7 s.
    "test_readers",
    # What a gate read is what its verdict is keyed by: invariant 7 is exactly
    # as honest as this record. 1.5 s.
    "test_trace",
    # Digests from bytes; the stat cache never serves a stale one (S-22). 0.1 s.
    "test_digests",
    # The recording loader: code a gate or fixture loads is keyed by its bytes.
    # 0.4 s.
    "test_codeload",
    # Every JSON file the spine writes is JSON (S-47). 0.1 s.
    "test_strict_json",

    # -- invariant 9: admission (test_admission is ~150 s whole) ---------- #
    # Every in-process scenario: an always-True gate, an identity fixture, a
    # no-op fixture the cache would hit, a model edit that defuses a control,
    # `--force` with a changed answer, each driven through the sweep. ~5 s.
    "test_admission.AdmissionIsDemonstrated.test_an_honest_gate_is_admitted_and_its_pass_counts",
    "test_admission.AdmissionIsDemonstrated.test_an_always_true_gate_never_yields_pass",
    "test_admission.AdmissionIsDemonstrated.test_the_second_sweep_reuses_the_control_entries",
    "test_admission.AdmissionIsDemonstrated.test_a_literal_identity_fixture_with_a_known_good_host_is_not_admitted",
    "test_admission.AdmissionIsDemonstrated.test_the_known_good_context_is_handed_nothing_of_the_live_design",
    "test_admission.AdmissionIsDemonstrated.test_a_file_the_known_good_design_reads_is_an_input_of_its_controls",
    "test_admission.AdmissionIsDemonstrated.test_force_reruns_every_control_and_a_changed_outcome_is_not_admitted",
    "test_admission.AdmissionIsDemonstrated.test_run_gate_still_returns_its_raw_verdict",
    "test_admission.AdmissionIsDemonstrated.test_a_fixture_edited_into_a_no_op_is_not_admitted_though_the_cache_would_hit",
    "test_admission.AdmissionIsDemonstrated.test_a_closure_move_with_equal_values_is_reverified_by_the_fixture_alone",
    "test_admission.AdmissionIsDemonstrated.test_a_control_value_change_reruns_the_control_and_writes_a_new_entry",
    "test_admission.AdmissionIsDemonstrated.test_a_model_edit_that_defuses_a_control_is_not_admitted",
    # S-05 and S-07 through the commands a human types: `check`, `status` and
    # the report are wired to admission (the hole was every piece existing in a
    # library and the CLI never asking). The four cheapest; ~8 s.
    "test_admission.AdmissionIsDemonstrated.test_cli_a_no_op_fixture_is_not_admitted_though_the_cache_would_hit",
    "test_admission.AdmissionIsDemonstrated.test_cli_an_always_true_gate_is_never_pass",
    "test_admission.AdmissionIsDemonstrated.test_cli_a_logger_with_no_honest_past_is_refused_by_check",
    "test_admission.AdmissionIsDemonstrated.test_cli_identity_fixtures_for_deflection_are_not_admitted",
    # A missing tool reads skipped, never "not admitted"; availability comes
    # before admission. 0.4 s.
    "test_admission.SweepOrder",
    # Invariant 9's paired rule (P2.3, V1 and V2): both controls, in process on
    # one planted project per case — an always-False gate, a known-bad-shown one,
    # a crashed half, channels, tiers, the early cutoff over both halves; and
    # every conclusive mutation a fail — a gate keyed to its own control, a
    # hidden limit at three distances, the walk aimed, inconclusive counted
    # neither way. ~5 s. The 600-key budget case (9.5 s) is left to the gate.
    "test_admission.QualificationIsPaired.test_an_always_false_gate_is_unqualified_and_its_claim_a_gap",
    "test_admission.QualificationIsPaired.test_an_always_raising_gate_declared_expect_error_is_unqualified",
    "test_admission.QualificationIsPaired.test_a_known_good_crash_is_remembered_never_cached",
    "test_admission.QualificationIsPaired.test_known_bad_shown_is_a_gap",
    "test_admission.QualificationIsPaired.test_a_pass_beside_a_known_bad_shown_evaluator_is_never_checked",
    "test_admission.QualificationIsPaired.test_an_incomplete_current_entry_is_a_miss",
    "test_admission.QualificationIsPaired.test_a_hand_placed_paired_entry_that_does_not_follow_is_refused",
    "test_admission.QualificationIsPaired.test_force_reruns_both_halves_and_the_walk_over_a_forged_paired_entry",
    "test_admission.QualificationIsPaired.test_controls_reaching_it_through_different_channels_are_unqualified",
    "test_admission.QualificationIsPaired.test_a_host_key_passed_through_is_a_channel",
    "test_admission.QualificationIsPaired.test_a_gate_that_skips_its_own_known_good_is_unqualified",
    "test_admission.QualificationIsPaired.test_a_missing_tool_reads_skipped_and_runs_neither_half",
    "test_admission.QualificationIsPaired.test_entries_that_disagree_across_tiers_are_unqualified",
    "test_admission.QualificationIsPaired.test_an_unqualified_reason_never_reads_pending_after_the_fixture_code_moves",
    "test_admission.EveryConclusiveMutationMustFail.test_a_hidden_limit_is_unqualified_at_three_distances",
    "test_admission.EveryConclusiveMutationMustFail.test_a_recorded_conclusive_pass_is_unqualified_wherever_the_pack_sits",
    "test_admission.EveryConclusiveMutationMustFail.test_a_temp_dir_inside_the_project_stops_the_walk_loudly",
    "test_admission.EveryConclusiveMutationMustFail.test_a_walk_error_that_does_not_repeat_is_held",
    "test_admission.EveryConclusiveMutationMustFail.test_a_walk_that_makes_no_mutation_says_why",
    "test_admission.EveryConclusiveMutationMustFail.test_an_evaluator_keyed_to_its_control_is_unqualified",
    "test_admission.EveryConclusiveMutationMustFail.test_an_evaluator_with_no_mutable_read_qualifies_on_its_controls",
    "test_admission.EveryConclusiveMutationMustFail.test_an_inconclusive_mutation_counts_neither_way",
    "test_admission.EveryConclusiveMutationMustFail.test_extra_channel_controls_outside_the_bundled_packs_are_unqualified",
    "test_admission.EveryConclusiveMutationMustFail.test_the_walk_is_aimed",
    # Review of P2.3's laundering paths, in process (~0.3 s each): the PACK_DIR
    # line, a known-good-only file read, a known-good-only tier read, a held
    # known-bad pass, the model and the ledger a check run alone was handed.
    "test_admission.EveryConclusiveMutationMustFail.test_a_project_gate_that_names_a_bundled_pack_dir_is_still_walked",
    "test_admission.QualificationIsPaired.test_a_file_only_the_known_good_half_reads_moves_the_qualification",
    "test_admission.QualificationIsPaired.test_a_tier_only_the_known_good_half_reads_picks_the_path",
    "test_admission.QualificationIsPaired.test_a_known_bad_pass_beside_a_crashed_known_good_half_is_remembered",
    "test_admission.QualificationIsPaired.test_each_unusable_or_crashed_control_is_named_in_its_own_words",
    "test_admission.TheCheckRunTakesTheQualifiedPath",
    # The control entry's paired shape and its strict reader (V8). 0.1 s.
    "test_cache.PairedEntries",

    # -- invariant 7: stale is not current (test_staleness is ~95 s whole) - #
    # Every in-process channel: same-size edits with mtime restored, the racy
    # tick, subprocess and spawned-worker reads, env vars, sqlite, linecache,
    # bulk readers, the sweep memo, hand-edited entries, crashes under --force,
    # cheap- and costlier-path crashes. Each asserts PASS before and not-PASS
    # after. ~9 s.
    "test_staleness.StaleIsNotCurrent.test_s20_the_first_filtered_sweep_then_an_input_change",
    "test_staleness.StaleIsNotCurrent.test_s22_a_data_file_edited_in_place_with_its_mtime_restored_and_backdated",
    "test_staleness.StaleIsNotCurrent.test_a_racy_tick_edit_is_rehashed",
    "test_staleness.StaleIsNotCurrent.test_a_subprocess_reading_an_unlisted_file_is_never_fresh",
    "test_staleness.StaleIsNotCurrent.test_a_spawned_worker_reading_a_project_file_is_never_fresh",
    "test_staleness.StaleIsNotCurrent.test_s23_a_claim_record_edit",
    "test_staleness.StaleIsNotCurrent.test_a_missing_key_that_appears",
    "test_staleness.StaleIsNotCurrent.test_a_named_file_that_appears_after_its_existence_check",
    "test_staleness.StaleIsNotCurrent.test_every_existence_and_kind_question_is_an_input",
    "test_staleness.StaleIsNotCurrent.test_a_file_whose_existence_was_checked_is_removed",
    "test_staleness.StaleIsNotCurrent.test_a_size_decided_without_opening_the_file",
    "test_staleness.StaleIsNotCurrent.test_existence_questions_outside_the_project_leave_a_gate_fresh",
    "test_staleness.StaleIsNotCurrent.test_an_environment_read_is_named_and_never_fresh",
    "test_staleness.StaleIsNotCurrent.test_a_database_read_in_c_is_an_input",
    "test_staleness.StaleIsNotCurrent.test_a_data_file_read_through_linecache_or_tokenize_is_an_input",
    "test_staleness.StaleIsNotCurrent.test_a_warm_linecache_hides_the_file",
    "test_staleness.StaleIsNotCurrent.test_s27_a_memo_shared_file_edit_stales_both_gates",
    "test_staleness.StaleIsNotCurrent.test_a_memo_shared_loader_that_follows_an_include_stales_both_gates",
    "test_staleness.StaleIsNotCurrent.test_a_gate_caching_in_the_sweep_memo_directly_is_never_a_stale_pass",
    "test_staleness.StaleIsNotCurrent.test_a_module_level_memo_keeps_its_file_an_input",
    "test_staleness.StaleIsNotCurrent.test_a_module_level_memo_left_warm_hides_the_file",
    "test_staleness.StaleIsNotCurrent.test_a_hand_edited_entry",
    "test_staleness.StaleIsNotCurrent.test_a_crash_under_force_then_a_plain_sweep_reads_errored",
    "test_staleness.StaleIsNotCurrent.test_a_measurement_at_other_inputs_never_clears_a_crash_here",
    "test_staleness.StaleIsNotCurrent.test_an_availability_skip_never_replaces_a_crash",
    "test_staleness.StaleIsNotCurrent.test_a_control_crash_is_not_erased_by_a_control_at_another_version",
    "test_staleness.StaleIsNotCurrent.test_a_pass_on_the_cheap_path_never_clears_a_crash_on_the_costlier_one",
    "test_staleness.StaleIsNotCurrent.test_a_crash_on_the_cheap_path_is_remembered_at_the_cheap_paths_inputs",
    "test_staleness.StaleIsNotCurrent.test_a_control_firing_on_the_cheap_path_never_clears_its_crash_on_the_costlier_one",
    "test_staleness.StaleIsNotCurrent.test_a_control_firing_on_the_costlier_path_never_clears_its_crash_on_the_cheap_one",
    "test_staleness.StaleIsNotCurrent.test_a_control_crash_on_a_path_this_check_does_not_run_is_its_answer",
    "test_staleness.StaleIsNotCurrent.test_a_helper_module_edit",
    "test_staleness.StaleIsNotCurrent.test_s26_a_same_second_gate_edit",
    # The three defects first read through the CLI: a filtered first check
    # (S-20), a model that does not load (S-21), a dry check (S-32). ~7 s.
    "test_staleness.StaleIsNotCurrent.test_s20_cli_a_filtered_first_check_then_an_input_change",
    "test_staleness.StaleIsNotCurrent.test_s21_cli_a_model_that_does_not_load_proves_nothing",
    "test_staleness.StaleIsNotCurrent.test_s32_cli_a_dry_check_leaves_a_moved_gate_stale",
    # (S-25's bulk readers, fourteen gates qualified on one sweep — 2.2 s since
    # P2.3 walks each — are left to the gate, below.)

    # -- invariant 10: a prerequisite not established is never a pass ----- #
    # The rule in the run loop, the resolver and the composition, with the
    # spine's mark and R-2; a cached pass behind a failed guard (V5) and a crash
    # behind one read as loud as a crash (invariant 2's class too), each through
    # a sweep in process; the graph refused at registration — every 3-node graph
    # in every load order and 1,000 seeded 6-node ones; the edge outside rho; the
    # declaration's exactness; the merged view `check` judges from (the critique's
    # unselected dependent); the run order. ~3 s together.
    "test_prerequisites.PrerequisiteFailureIsNeverAPass",
    "test_prerequisites.ACachedPassNeverSurvivesAFailedPrerequisite",
    "test_prerequisites.AnErroredPrerequisiteStaysLouder",
    "test_prerequisites.NeedsCycleRefused",
    "test_prerequisites.TierInversionRefused",
    "test_prerequisites.NeedsIsNotARhoEdge",
    "test_prerequisites.NeedsDeclarationsAreExact",
    "test_prerequisites.UnselectedDependentFollowsTheSweep",
    # The review of P2.2: applying the rule twice moves nothing (`cli._swept`
    # applies it over a view `resolve` already ruled on). In process, 0.2 s.
    "test_prerequisites.ApplyingTheRuleTwiceMovesNothing",
    "test_prerequisites.PlanIsStable.test_without_needs_run_order_is_registration_order",
    "test_prerequisites.PlanIsStable.test_needs_order_is_dfs_postorder",
    "test_prerequisites.PlanIsStable.test_a_plan_that_does_not_expand_is_caught",

    # -- P2.4: goalposts, the comparison, operating contexts (4, 7, 9) ----- #
    # In process, under a second together (measured 2026-10-03: 0.7 s): a pass
    # held to the goalpost it read (run_gate); a value outside its claim's
    # condition never Checked (compose, no registry); a goalpost edit
    # invalidating exactly the gate that read it (a bracket copy resolved in
    # process) and no limit left in a bracket gate; a goalpost never a key (the
    # limit-keyed and presence-keyed planted gates); the operating context's
    # breach rule, its mark, a guard outside it, a key never read, a fail never
    # laundered, a mutation counting wherever it lands. The bracket copies run
    # through the commands are left to the gate (below).
    "test_goalposts.APassMustMeetTheAcceptanceItRead",
    "test_goalposts.AValueOutsideTheAcceptanceIsNeverChecked.test_a_pass_outside_the_claims_condition_reads_failing",
    "test_goalposts.AValueOutsideTheAcceptanceIsNeverChecked.test_what_is_not_compared_and_what_is",
    "test_goalposts.AValueOutsideTheAcceptanceIsNeverChecked.test_a_stale_pass_counts_and_an_unqualified_one_never",
    "test_goalposts.AValueOutsideTheAcceptanceIsNeverChecked.test_a_pass_outside_its_operating_context_is_not_compared",
    "test_goalposts.AValueOutsideTheAcceptanceIsNeverChecked.test_no_registry_is_needed",
    "test_goalposts.AValueOutsideTheAcceptanceIsNeverChecked.test_a_composition_that_skips_the_comparison_reads_checked",
    "test_goalposts.AValueOutsideTheAcceptanceIsNeverChecked.test_the_explaining_verdict_is_the_one_compared",
    "test_goalposts.TheGoalpostLivesInClaims.test_no_limit_lives_in_a_bracket_gate_and_deflection_reads_c1",
    "test_goalposts.TheGoalpostLivesInClaims.test_only_a_limit_edit_invalidates_and_only_the_gate_that_reads_it",
    "test_goalposts.AGoalpostIsNeverAKey",
    # Review of P2.4 (4): no shipped or taught gate repeats the claim's units
    # back, and the scan finds the echo (0.03 s). The bracket copy with C1 in
    # `um` is left to the gate (below).
    "test_goalposts.AnEvaluatorStatesItsOwnUnits.test_no_shipped_or_taught_gate_echoes_the_claims_units",
    "test_goalposts.AnEvaluatorStatesItsOwnUnits.test_the_echo_is_what_the_scan_finds",
    "test_goalposts.Margins",
    "test_goalposts.AClaimReadIsTheWholeRecord",
    "test_context.OutsideTheContextAPassDoesNotCount.test_the_composition_reads_gap",
    "test_context.OutsideTheContextAPassDoesNotCount.test_the_breach_rule",
    "test_context.OutsideTheContextAPassDoesNotCount.test_the_mark_is_on_passes_alone_and_idempotent",
    "test_context.OutsideTheContextAPassDoesNotCount.test_a_declared_key_its_run_never_reads_is_an_error",
    "test_context.OutsideTheContextAPassDoesNotCount.test_a_guard_outside_its_context_establishes_nothing",
    "test_context.AFailOutsideStillCounts.test_the_mark_never_launders_a_fail",
    "test_context.KnownGoodOutsideIsUnqualified.test_the_judge_reads_known_good_outside",
    "test_context.AMutationPassingOutsideStillCounts",
    "test_context.AnOwnedFallbackReadsAssumed",
    "test_context.MalformedContextsAreRefused",
    "test_context.NoContextMovesNothing",

    # -- invariant 8: records are the source, the index an output --------- #
    # The whole class, the index-agrees test among it: a hand-edited index
    # loses, every record change reaches it, and every command run after a
    # hand edit leaves `agree()` empty (14 s of the 17). ~17 s.
    "test_records.IndexNeverDisagreesWithRecords",
    # Invariant 8's second half, its planted violator and the property on the
    # command that writes most: `check` writes only ignored paths and new
    # entries. ~4 s.
    "test_records.NoCommandWritesARecord.test_the_property_refuses_what_it_forbids",
    "test_records.NoCommandWritesARecord.test_check_writes_only_ignored_paths_and_new_entries",
    # Every command on a migrated project, swept for a record it was not asked
    # to write: the regression invariant 8 names (`gap`, which reads like a
    # query, filing every gap as a record). What got through without it (review
    # of this file, 2026-10-03): `gap` planted to rewrite every claim record left
    # this tier green, and IndexNeverDisagreesWithRecords cannot see it, because
    # the index is rebuilt after the command and agrees. 11-16 s.
    "test_records.NoCommandWritesARecord.test_no_command_writes_a_record_on_a_migrated_project",
    # The strict reader (S-40: a typo'd key erased from disk by a command that
    # reads like a query), and a legacy ledger carrying a forged PASS. <0.3 s.
    "test_records.StrictReader",
    "test_records.LegacyLedgerMigrates.test_an_old_shape_ledger_holding_a_forged_pass_changes_no_status",
    "test_records.LegacyLedgerMigrates.test_no_migrated_legacy_verdict_can_make_pass",

    # -- invariants 5 and 6: the gate on the gates ------------------------ #
    # Every bundled gate passes its baseline and every control fires or is
    # honestly blocked, plus the rule's planted violators. ~7 s.
    "test_packs.NegativeControlsFire",
    # The seal's planted violators: an unsealed fixture and an identity fixture
    # in a pack are caught. 0.4 s.
    "test_packs.ControlsAreSealed.test_planted_unsealed_fixture_is_caught",
    "test_packs.ControlsAreSealed.test_identity_fixture_in_a_pack_is_caught",
    # The seal over every BUNDLED pack: each bundled fixture's host reads traced,
    # and every control fired with an empty host. What got through without them
    # (review of this file, 2026-10-03): a bundled beam fixture layered over
    # ctx.params left this tier green, because NegativeControlsFire hands each
    # control the pack's own baseline as host, so a layered fixture still fires
    # there; and `pack validate`, which would refuse it, is not in this tier.
    # ~7 s and ~8 s.
    "test_packs.ControlsAreSealed.test_no_bundled_fixture_reads_host_params",
    "test_packs.ControlsAreSealed.test_controls_fire_without_a_host_projection",
    # The tests run against this checkout's packs and spine, not an installed
    # copy. 0.0 s.
    "test_packs.TestsTheBundledCopy",

    # -- house rule: no reference to the parent project ------------------- #
    # The whole walk and its planted violators. Note: it reads the working
    # tree, untracked files included, here as in the full suite. 0.1 s.
    "test_packs.NoLeakedProvenance",
]

# Tests of an INVARIANT class that this tier does not run, each group with why
# leaving it to the full suite is safe. FastTierHoldsEveryInvariant reads this
# list: a test of an invariant class must be named in FAST or here.
LEFT_FOR_THE_GATE: list[str] = [
    # Invariant 15 on the real walk (P2.3): `gates.mutation_walk` judged by the
    # harness over the bracket's six gates and beam-analytic's eight, the
    # tripwire's scan (it judges every subject on those gates too), the hiding
    # and type-breaking plans on real evaluators, and the sweep's pass writing
    # nothing. ~19 s together. The rule each holds is held in process above, on
    # the planted runners; what these add is the spine's runner on real gates,
    # which is walk code: an iteration touching `gates.mutation_walk` or
    # `verdicts._run_control` runs test_mutation and test_admission whole.
    "test_mutation.MutationIsSealed.test_every_mutation_entry_point_is_a_subject",
    "test_mutation.MutationIsSealed.test_the_subject_harness_holds_real_evaluators",
    "test_mutation.MutationIsSealed.test_hiding_and_type_breaking_plans_are_caught_on_real_evaluators",
    "test_mutation.MutationIsSealed.test_the_sweeps_mutation_pass_writes_nothing",
    # The walk's budget on a 600-key evaluator (9.5 s): the value that moves its
    # value walked first, and the rest named not mutated. Same escalation.
    "test_admission.EveryConclusiveMutationMustFail.test_the_budget_walks_the_value_that_moves_first",
    # The early cutoff over both halves, and a value only a mutated run reads
    # keyed (V1i, V1j): ~1.2 s each, two `DRIVER` processes per step. The
    # cutoff is keying code: same escalation as the walk's.
    "test_admission.QualificationIsPaired.test_the_early_cutoff_compares_both_halves",
    "test_admission.QualificationIsPaired.test_a_value_only_a_mutated_run_reads_is_keyed",
    # Inconclusive never a fail, on `check`, `gate show` and `gate selftest` of
    # a bracket copy (0.9 s): the rule is held in process above by the planted
    # runners; the channels are renderer code (module docstring).
    "test_mutation.MutationIsSealed.test_an_inconclusive_mutation_is_never_a_fail_anywhere",
    # Admission through the CLI, one channel each: a pack asset, a fixture or
    # gate module read at import or loaded at run time, the costlier tier's path,
    # a claim edit under a live control, a fixture deriving its known-bad from
    # live values. ~100 s together (the pack-asset one alone 23 s). The rule they
    # hold is the sweep's, exercised in-process above; what they add is that
    # each channel reaches the read set, which is tracer and loader code: an
    # iteration touching that code runs test_admission whole (module docstring).
    "test_admission.AdmissionIsDemonstrated.test_cli_a_known_good_that_passes_the_live_design_through_is_not_admitted",
    "test_admission.AdmissionIsDemonstrated.test_cli_a_control_shown_on_the_cheap_path_does_not_admit_the_costlier_one",
    "test_admission.AdmissionIsDemonstrated.test_cli_a_control_undemonstrated_on_the_costlier_path_reads_stale_to_check_too",
    "test_admission.AdmissionIsDemonstrated.test_cli_a_claim_edit_that_defuses_a_live_control_is_not_admitted",
    "test_admission.AdmissionIsDemonstrated.test_cli_a_fixture_that_states_its_own_ledger_survives_a_claim_edit_unrun",
    "test_admission.AdmissionIsDemonstrated.test_cli_a_claim_edit_under_a_known_good_ledger_moves_the_file_it_came_from",
    "test_admission.AdmissionIsDemonstrated.test_cli_a_fixture_deriving_its_known_bad_from_live_params_is_rekeyed",
    "test_admission.AdmissionIsDemonstrated.test_cli_the_literal_identity_fixture_on_a_live_host_is_rekeyed",
    "test_admission.AdmissionIsDemonstrated.test_cli_a_fixture_deriving_its_known_bad_from_the_live_ledger_is_rekeyed",
    "test_admission.AdmissionIsDemonstrated.test_cli_a_fixture_that_swaps_the_model_or_the_memo_is_not_vouched_for",
    "test_admission.AdmissionIsDemonstrated.test_cli_a_pack_asset_edit_misses_the_control_entry_and_reruns_it",
    "test_admission.AdmissionIsDemonstrated.test_cli_bytecode_in_selftest_writes_no_new_control_entry",
    "test_admission.AdmissionIsDemonstrated.test_cli_a_config_default_edit_reverifies_every_control_by_its_fixture",
    "test_admission.AdmissionIsDemonstrated.test_cli_a_build_edit_that_moves_a_control_input_writes_a_new_control_entry",
    "test_admission.AdmissionIsDemonstrated.test_cli_a_module_fixture_edited_into_a_no_op_is_not_admitted",
    "test_admission.AdmissionIsDemonstrated.test_cli_a_module_fixture_no_loader_records_is_reverified_on_every_check",
    "test_admission.AdmissionIsDemonstrated.test_cli_a_file_a_fixture_module_reads_at_import_is_keyed",
    "test_admission.AdmissionIsDemonstrated.test_cli_a_limit_a_gate_module_reads_at_import_is_keyed",
    "test_admission.AdmissionIsDemonstrated.test_cli_a_known_good_that_loads_the_live_model_at_run_time_is_keyed",
    "test_admission.AdmissionIsDemonstrated.test_cli_a_helper_a_fixture_loads_at_run_time_is_keyed",
    "test_admission.AdmissionIsDemonstrated.test_cli_a_limit_a_gate_loads_at_run_time_is_keyed",
    # S-25's fourteen bulk readers on one sweep: 2.2 s once P2.3 walked each
    # gate's reads. Every reader shape is a tracer case; an iteration touching
    # the tracer runs test_staleness whole (module docstring).
    "test_staleness.StaleIsNotCurrent.test_s25_each_bulk_reader_top_level_and_nested",
    # Staleness through the CLI. Most replay an in-process scenario listed in
    # FAST (other-inputs crash, cheap vs costlier path, a file that appears, a
    # helper edit) through `check`; the rest are ingest, a factory-made gate and
    # the costlier-tier serve. ~21 s. Same rule as admission's: an iteration
    # touching the read set or the tier logic runs test_staleness whole.
    "test_staleness.StaleIsNotCurrent.test_s33_cli_an_unrelated_ingest_stales_nothing_and_a_read_input_its_gate",
    "test_staleness.StaleIsNotCurrent.test_cli_the_bundled_source_hygiene_sees_a_named_mo_that_appears",
    "test_staleness.StaleIsNotCurrent.test_cli_a_gate_helper_beside_the_project_is_code_not_an_instrument",
    "test_staleness.StaleIsNotCurrent.test_cli_a_gate_a_factory_makes_is_keyed_on_the_module_that_registered_it",
    "test_staleness.StaleIsNotCurrent.test_cli_a_pass_from_the_cheap_path_is_never_served_to_a_costlier_tier",
    "test_staleness.StaleIsNotCurrent.test_cli_a_forced_cheap_run_never_lays_its_pass_over_the_costlier_fail",
    "test_staleness.StaleIsNotCurrent.test_cli_a_pass_at_other_inputs_never_clears_a_crash_here",
    # Every command run on a LEGACY project, checked for a written record, and
    # the legacy-project `check` and shim (~12 s). The property, its planted
    # violator and the migrated-project sweep are in FAST; these add the
    # one-time migration's carve-out, which only a change to a command's write
    # path or to the migration can break: such an iteration runs test_records
    # whole (module docstring).
    "test_records.NoCommandWritesARecord.test_no_command_writes_a_record_on_a_legacy_project",
    "test_records.NoCommandWritesARecord.test_check_on_a_legacy_project_writes_only_the_migration",
    "test_records.NoCommandWritesARecord.test_a_shim_on_a_legacy_project_writes_the_migration_and_its_record",
    # P2.4's channels on bracket copies and planted projects, through the
    # commands (~1-3 s each): a goalpost moved and tightened (and its coupled
    # and whole-record violators), the control entry unmoved, every channel
    # Failing on C3 tightened, every channel Gap outside a context and Checked
    # with it removed, a model that does not load, a fail outside invalidated,
    # a known-good outside the context. The rule each holds is held in process
    # above; what these add is each command's rendering of it, which is
    # renderer code: an iteration touching `report.py`, `cli.py` or `site.py`
    # runs test_goalposts and test_context whole (module docstring).
    "test_goalposts.TheGoalpostLivesInClaims.test_a_relaxed_goalpost_reads_checked_and_runs_no_control",
    "test_goalposts.TheGoalpostLivesInClaims.test_a_tightened_goalpost_fails_and_stays_qualified",
    "test_goalposts.TheGoalpostLivesInClaims.test_the_control_entry_does_not_move_with_the_goalpost",
    "test_goalposts.AValueOutsideTheAcceptanceIsNeverChecked.test_every_channel_reads_failing_on_the_bracket",
    "test_context.OutsideTheContextAPassDoesNotCount.test_every_channel_reads_gap_and_without_the_context_checked",
    "test_context.OutsideTheContextAPassDoesNotCount.test_a_model_that_does_not_load_reads_stale",
    "test_context.AFailOutsideStillCounts.test_a_fail_outside_is_failing_and_stays_so_when_invalidated",
    "test_context.KnownGoodOutsideIsUnqualified.test_a_known_good_outside_is_unqualified_and_inside_qualified",
    # Review of P2.4 (4): C1 restated in `um`, its known-good copy too, never
    # Checked — and Checked with the gate echoing `acc.units` (two bracket check
    # runs, ~1-2 s). The scan that holds every shipped gate to it is in FAST.
    "test_goalposts.AnEvaluatorStatesItsOwnUnits.test_a_claim_in_other_units_is_never_checked",
    # Invariant 5 (P2.4, C2): every bundled control under host claims at 1e9,
    # each selftest run twice (~8 s, as its sibling with an empty host). A
    # characterization — green before P2.4 — of a seal pack fixtures already
    # keep; an iteration touching a pack's fixtures or `GateContext.acceptance`
    # runs test_packs whole (module docstring).
    "test_packs.ControlsAreSealed.test_host_claims_never_reach_a_pack_control",
]

# Left out, carrying no invariant class; seconds as measured with eight modules
# running at once. The full suite runs every one of them before every commit.
#   P2.4's pay-back (one process, idle machine, 2026-10-03): test_status_table's
#   three bracket copies of their own — UnqualifiedReadsGap's
#   test_a_refusal_outlives_a_model_edit 1.2 (rung 4 through a model edit and a
#   crashed control), KnownBadShownIsAGap 0.7 (known-bad shown reads Gap, the
#   bracket with its known-good context renamed away) and
#   UndemonstratedReadsStale 0.6 (a control's note edited reads Stale, never
#   Gap). The composition each reads is StatusTable's and
#   UnqualifiedBesideAPassIsNeverChecked's, run here in process; what they add
#   is one command's reading on a bracket, which the iteration that touches the
#   resolver or the sweep runs whole (module docstring: test_admission's
#   neighbours) and the full suite runs always.
#   test_determinism 97 (whole checks rerun and compared byte for byte) and
#   test_fresh_clone 47 (the fresh-clone transcript replayed): end-to-end
#   replays of what the tests above hold piece by piece.
#   test_packs.DemonstrateAgrees 37 and PacksValidate 1: `gate selftest` and
#   `pack validate` over every pack, which a pack author runs anyway.
#   test_staleness's other classes 46 (InvalidationIsLocalised, GateVersionRows, LastCheck,
#   LastCheckWatches, CostIsKept, InstrumentMismatchIsNoted): mostly how NARROW a
#   stale set is and what a hit costs, where too wide wastes a rerun and never
#   serves a lie. Not only that: InvalidationIsLocalised and GateVersionRows also fail in
#   the lie's direction, on real content — a stale set SMALLER than expected is
#   an under-recorded read set on the bracket's model, and GateVersionRows
#   (8 s) is the one test that edits a real bundled pack module (fdm-print) and
#   requires its gates to go stale. StaleIsNotCurrent above holds the same lie on
#   synthetic gates; on the bundled content it is the escalation rule (module
#   docstring: tracer and keying code runs test_staleness whole) and the full
#   suite that catch it.
#   test_records' EveryReaderMigratesAsCheckDoes and
#   EvidenceNeverSitsWhereARecordGoes 20, and the rest of LegacyLedgerMigrates,
#   RecordWriter, InitIsTheNewLayout, SaveWritesTheLayoutItFinds 3: the one-time
#   migration of a legacy project and the record writer's bytes.
#   The cache and the traced gate world: gate_context 30, check_cache 24,
#   cache 13, spine_digest 3, bracket_cache 2, fdm_memo 2. Keying machinery: the
#   iteration that touches it runs these with test_admission and test_staleness
#   (module docstring); the lie they guard against is StaleIsNotCurrent's, whose
#   in-process scenarios are above.
#   test_prerequisites' project-per-case classes 25 (P2.2): the §3 matrix,
#   54 projects (PrunedRowIsWhatStatusReads); the bracket at arm 30 through
#   `check` and JUnit (BracketGuardTranscript); the wrapped beam at two designs
#   (GuardsReachNarrowClaims); `check --only` expanding (PlanIsStable's CLI
#   test); the words, `doctor` included (PrerequisiteWords); the corpus resolved
#   with and without the rule (ResolveIsUnchangedWithoutNeeds); the cad mesh
#   guards (trimesh), the bracket header, JUnit's skip and `selftest --only`.
#   The rule they hold is the one FAST runs in process; a change to the sweep,
#   the resolver or `cli._swept` runs test_prerequisites whole. And
#   test_packs.ControlsAreIsolated 6: every bundled edge's isolation and the
#   planted pairs, which `pack validate` also checks; and
#   test_packs.AGuardFailureNeverHidesAnIndependentFail 1: an over-broad guard
#   (the review of P2.2's three sourcing edges), which isolation cannot see.
#   test_vocabulary.ReadyMeansEveryRequiredClaimChecked ~25: V15 through the
#   commands — what `check`, `status`, their JSON, JUnit and the page say about
#   *ready* on seven bracket copies; `ReadyIsThePredicate` holds the predicate
#   they all read, in process, every iteration.
#   The command and document surfaces: shapes 26, param_view 25, site 21,
#   pack_mode 20, record_commands 19, doctor 19, shims 15, status_stale 13,
#   fixture_hygiene 10, contracts 9, openmodelica_build 8, junit_cli 4, vcs 2,
#   ci_config 1, docs_commands 1, pack_keys 0.5, project_marker 0.4: what a
#   command prints, writes or documents, and the bracket's own controls.


# --------------------------------------------------------------------------- #
# FastTierHoldsEveryInvariant
# --------------------------------------------------------------------------- #
def _covers(entry: str, test_id: str) -> bool:
    return test_id == entry or test_id.startswith(entry + ".")


def coverage_problems(fast: list[str], left: list[str],
                      classes: dict[str, list[str] | None]) -> list[str]:
    """What is wrong with ``fast``/``left`` against ``classes``: invariant class
    ref -> its test method names (None when the class does not resolve)."""
    problems: list[str] = []
    known: set[str] = set()
    for ref, methods in sorted(classes.items()):
        if not methods:
            problems.append(f"{ref}: does not resolve to a TestCase holding a test")
            continue
        ids = [f"{ref}.{name}" for name in methods]
        known.update(ids)
        if not any(_covers(e, i) for e in fast for i in ids):
            problems.append(f"{ref}: carries an invariant and has no test in FAST")
        for test_id in ids:
            in_fast = any(_covers(e, test_id) for e in fast)
            in_left = any(_covers(e, test_id) for e in left)
            if in_fast and in_left:
                problems.append(f"{test_id}: in both FAST and LEFT_FOR_THE_GATE")
            elif not in_fast and not in_left:
                problems.append(f"{test_id}: in an invariant class and in neither FAST "
                                f"nor LEFT_FOR_THE_GATE — decide where it runs, and why")
    for entry in left:
        if entry not in known:
            problems.append(f"{entry}: in LEFT_FOR_THE_GATE but no test of an invariant "
                            f"class — renamed, deleted, or never one")
    return problems


def _planned_but_unwritten(ref: str) -> bool:
    """A planned class whose file or class does not exist yet — the state
    `test_meta.test_planned_classes_never_skip_once_they_exist` allows. One
    whose class is written but does not import is NOT this: it resolves to
    None below and is a problem."""
    found = test_meta._class_source(ref)
    if found is None:
        return True
    source, class_name, path = found
    return test_meta._skip_findings(source, class_name, os.path.relpath(path)) is None


def _invariant_classes() -> dict[str, list[str] | None]:
    """Every class of a numbered invariant AND of a planned one: a test added to
    a planned class (MutationIsSealed, P2.0) must be placed too, or it would run
    in neither tier until someone noticed — the class is planned so that the
    rules bite from its first line, and this is one of them. A planned class not
    written yet is left out until it is, as test_meta allows (what slipped
    through the first version: a planned ref named a checkpoint ahead turned
    this tier red while test_meta, by design, stayed green). *Rejected:*
    listing a planned class in FAST by name only, which a fourth test added to
    it later slips past; requiring planned classes to exist, which makes
    planning a class a checkpoint ahead impossible."""
    loader = unittest.TestLoader()
    out: dict[str, list[str] | None] = {}
    planned = [ref for ref in test_meta._refs(test_meta.PLANNED_INVARIANT_CLASSES)
               if not _planned_but_unwritten(ref)]
    for ref in test_meta._refs(test_meta.INVARIANT_CLASSES) + planned:
        cls = test_meta._resolve(ref)
        out[ref] = list(loader.getTestCaseNames(cls)) if cls is not None else None
    return out


class FastTierHoldsEveryInvariant(unittest.TestCase):
    def test_every_invariant_test_is_placed(self):
        self.assertEqual(coverage_problems(FAST, LEFT_FOR_THE_GATE, _invariant_classes()), [])

    def test_the_map_is_not_vacuous(self):
        classes = _invariant_classes()
        self.assertGreaterEqual(len(classes), 10, sorted(classes))
        self.assertGreater(sum(len(m or []) for m in classes.values()), 100)

    # -- planted violators ------------------------------------------------- #
    CLASSES = {"test_a.Inv": ["test_x", "test_y"], "test_b.Other": ["test_z"]}

    def test_a_planted_clean_layout_is_clean(self):
        self.assertEqual(coverage_problems(["test_a.Inv.test_x", "test_b"],
                                           ["test_a.Inv.test_y"], self.CLASSES), [])

    def test_a_new_test_in_an_invariant_class_is_caught(self):
        problems = coverage_problems(["test_a.Inv.test_x", "test_b"], [], self.CLASSES)
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("test_a.Inv.test_y: in an invariant class and in neither", problems[0])

    def test_an_invariant_with_nothing_fast_is_caught(self):
        problems = coverage_problems(["test_a"], ["test_b.Other.test_z"], self.CLASSES)
        self.assertEqual(problems, ["test_b.Other: carries an invariant and has no test in FAST"])

    def test_a_new_invariant_class_is_caught(self):
        classes = dict(self.CLASSES, **{"test_c.New": ["test_w"]})
        problems = coverage_problems(["test_a", "test_b"], [], classes)
        self.assertIn("test_c.New: carries an invariant and has no test in FAST", problems)

    def test_a_stale_or_doubled_entry_is_caught(self):
        problems = coverage_problems(["test_a", "test_b"],
                                     ["test_a.Inv.test_x", "test_a.Inv.test_gone"], self.CLASSES)
        self.assertIn("test_a.Inv.test_x: in both FAST and LEFT_FOR_THE_GATE", problems)
        self.assertTrue(any(p.startswith("test_a.Inv.test_gone: in LEFT_FOR_THE_GATE")
                            for p in problems), problems)

    def test_an_unresolvable_class_is_caught(self):
        problems = coverage_problems(["test_a"], [], {"test_a.Gone": None})
        self.assertEqual(problems, ["test_a.Gone: does not resolve to a TestCase holding a test"])

    def test_a_planned_class_not_yet_written_waits_until_it_is(self):
        """test_meta allows a planned class that does not exist yet, so this tier
        does too; a NUMBERED one that does not resolve stays a problem."""
        before = _invariant_classes()
        with mock.patch.dict(test_meta.PLANNED_INVARIANT_CLASSES,
                             {99: "test_no_such_module.NotWrittenYet",
                              98: "test_invariants.NotWrittenYet"}):
            self.assertEqual(_invariant_classes(), before)
            self.assertEqual(coverage_problems(FAST, LEFT_FOR_THE_GATE, _invariant_classes()),
                             [])
        with mock.patch.dict(test_meta.INVARIANT_CLASSES,
                             {99: "test_no_such_module.NotWrittenYet"}):
            self.assertIn("test_no_such_module.NotWrittenYet: does not resolve to a TestCase "
                          "holding a test",
                          coverage_problems(FAST, LEFT_FOR_THE_GATE, _invariant_classes()))


def load_tests(loader: unittest.TestLoader, standard_tests: unittest.TestSuite,
               pattern: str | None) -> unittest.TestSuite:
    """The unittest protocol: this module's own checks, then every name in FAST."""
    standard_tests.addTests(loader.loadTestsFromNames(FAST))
    return standard_tests


if __name__ == "__main__":
    unittest.main()
