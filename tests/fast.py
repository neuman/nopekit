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

The budget is 90 s as ONE process on an otherwise idle machine: 68-71 s
measured 2026-10-03 (load average 1.3), after the bundled-pack seal tests and
the migrated-project write sweep joined; 54-59 s before they did, and 94 s for
that shorter list with other agents loading the machine. Re-time it whenever
``FAST`` changes; a run under load is not a measurement.

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
* a command's code in `cli.py`, `store.py`, a record writer or a migration ->
  `test_records` (every command on a legacy project, and the shims);
* a pack's fixtures or baseline, the fixture context in `gates.py`, or
  `NegativeControl` -> `test_packs` (with `test_pack_mode`).

Run:  PYTHONPATH=src python3 -m tests.fast        (or: python3 -m unittest tests.fast)
"""
from __future__ import annotations

import os
import sys
import unittest

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
    "test_staleness.StaleIsNotCurrent.test_s25_each_bulk_reader_top_level_and_nested",
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
]

# Left out, carrying no invariant class; seconds as measured with eight modules
# running at once. The full suite runs every one of them before every commit.
#   test_determinism 97 (whole checks rerun and compared byte for byte) and
#   test_fresh_clone 47 (the fresh-clone transcript replayed): end-to-end
#   replays of what the tests above hold piece by piece.
#   test_packs.DemonstrateAgrees 37 and PacksValidate 1: `gate selftest` and
#   `pack validate` over every pack, which a pack author runs anyway.
#   test_staleness's other classes 46 (E4Localisation, GateVersionRows, LastCheck,
#   LastCheckWatches, CostIsKept, InstrumentMismatchIsNoted): mostly how NARROW a
#   stale set is and what a hit costs, where too wide wastes a rerun and never
#   serves a lie. Not only that: E4Localisation and GateVersionRows also fail in
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


def _invariant_classes() -> dict[str, list[str] | None]:
    loader = unittest.TestLoader()
    out: dict[str, list[str] | None] = {}
    for ref in test_meta._refs(test_meta.INVARIANT_CLASSES):
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


def load_tests(loader: unittest.TestLoader, standard_tests: unittest.TestSuite,
               pattern: str | None) -> unittest.TestSuite:
    """The unittest protocol: this module's own checks, then every name in FAST."""
    standard_tests.addTests(loader.loadTestsFromNames(FAST))
    return standard_tests


if __name__ == "__main__":
    unittest.main()
