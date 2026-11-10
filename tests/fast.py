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

Eddy milestone 0 (the validator and the pre-receive hook) adds one pure class,
test_receive.TheContractIsTheRuleTable (the doc table against receive.RULES and
NOTES, the test-method bijection, the ignore-block derivation, the empty tree;
measured 2026-11-09 at 0.1 s alone, no repository built), and leaves every push —
in process or through the real hook — to the full suite; the tier was over
budget before it (142.0 s wall for 726 tests at load 1-2, the same tree with the
git edge's change) and this adds nothing an iteration would notice.
The budget is 90 s as ONE process on an otherwise idle machine. Issue #15's P1.1 (the
evidence bound by content) added two rows (~3 s: the A, B, A sequence and one check)
and left its other seven to the gate. Measured on 2026-10-21 as two interleaved pairs
against d29421e, both as exported trees, on a machine two other sessions were
loading (load 1.1-2.6): 173.6 s and 179.5 s wall for its 701 tests against 190.2 s
and 164.3 s for d29421e's 699, flat within that noise and over the line as its base
is; no idle run was had. Its P1.3 (bound viewgens) added two rows and moved P1.2's
unbound row to the gate (~2.2 s net); one unpaired run at load 1.3-4.2: 176.3 s wall
(137.5 s user) for its 704 tests, against 229.4 s (181.5 s user) for 2f52893's 703,
run just before it while another session raised the load to 10; still no idle run.
Its P1.4 (the page's figures) added three rows (~5 s user: each one to two checks and a
build in process) and test_style's figure keys (in process, ~0.05 s). One pair at load
2.4-3.7, head then base: 217.5 s wall (164.6 s user) for its 708 tests, against 209.6 s
(159.5 s user) for b95b7f4's 704. Over the line as its base is; no idle run.
Its P1.5 (the template) added three rows to test_site_truth.TheBrowserComputesNoTruth,
which runs whole here (a figure's currency, the field mode and the baked chart spec:
source scans and one spec in process, ~0.05 s); its browser test
(test_field_browser, ~2.5 s, a headless Chromium) is the full suite's. One unpaired
run at load 2.1-2.9: 211.4 s wall (163.3 s user) for its 711 tests. Over the line as
its base is; no idle run.
Its P2.5 (the technical record's charts as baked specs) added one row to
TheBrowserComputesNoTruth (source scans and two specs in process, ~0.01 s); its build
class, test_figures.ChartViewsAreBakedSpecs (~1.7 s), is the full suite's.
The merge of phase 3b into phase 6 keeps both lists whole (both paid with test_meta.NoGitOutsideVcs;
test_meta.InvariantClassesNeverSkip, which phase 6's review made 0.6 s, is out of the
tier as phase 3b left it). Measured on 2026-10-15 as two interleaved pairs against
3d8eaaf (load average 1.4-2.4, not idle): 122.9 s and 119.5 s wall (89.9 s and 87.3 s
user) for its 685 tests, against 118.1 s and 114.4 s (85.9 s and 83.7 s user) for
3d8eaaf's 662 — about 4 s over its base, which is over the line: the next change that
touches the tier pays that first. The follow-up after phase 3b did not: it added
test_style.TheSiteSaysABuiltPart (~0.18 s warm) and paid ~0.1 s of it by reading the
template's strings once per process (test_style.template_strings). Measured once on
2026-10-15 at load 0.8-2.7, not paired against its base: 106.2 s wall (76.2 s user)
for its 692 tests. Its second commit (a recorded result coloured by its token) added
~0.15 s: test_site_truth's counted fail, recorded and baked in process (~0.1 s warm),
and TheBrowserComputesNoTruth's token row; 101.5 s wall (73.8 s user) for 694 tests at
load 0.2-1.2, unpaired. The follow-up after the acceptance run (a round stages every file
git does not ignore) gave test_round.TheRoundCommitStagesOnlyTheProjectsPaths three
in-process rows (the secret names, the plan, the words) and the acceptance's files in its
commit row, and moved the wording a third round had checked into the words row: the class
runs ~0.8 s, as at a6c5f22. One pair at load 0.9-1.3: 121.6 s wall (89.1 s user) for its
696 tests against 123.5 s (91.4 s user) for a6c5f22's 694; test_serve's real processes
are the gate's. The review of
architecture phase 6 added ~1.2 s in the tier (invariant 8's identity and `init`
clauses: three in-process identity rows, two `init` rows, and the nested repository
in the staging class) and paid ~1.2 s by parsing each test file once in
test_meta.InvariantClassesNeverSkip (1.8 s to 0.6 s with the modules loaded, the same
assertions). Measured on 2026-10-15 as two interleaved pairs against 63e9cef on a
machine other sessions were loading (load average 2.3-3.9, not idle): 127.2 s and
138.7 s wall (92.9 s and 101.7 s user) for its 662 tests, against 161.2 s and 149.4 s
(109.8 s and 106.2 s user) for 63e9cef's 656 — not slower than its base, and over the
line as its base is. Architecture phase
6 (rounds and the plugin) added its invariant 8 class,
test_round.TheRoundCommitStagesOnlyTheProjectsPaths (~0.4 s in the tier, warm; ~1.5 s
alone: a bracket in a subtree committed in process twice, the checker's planted rows
and the closed list), and doctor's `people` row in the records sweeps (a git question
or two per `doctor`). It paid ~1.25 s, none an invariant class, each named where it
was: test_meta.NoGitOutsideVcs (~0.9 s) and five small entries
(test_prerequisites.PlanIsStable's three rows, test_goalposts.Margins and
AClaimReadIsTheWholeRecord, test_context.MalformedContextsAreRefused and
NoContextMovesNothing). Measured on 2026-10-15 as two interleaved pairs against
e27e1a7 (load average 0.8-1.5, not idle): 123.8 s and 123.1 s wall (91.0 s and 91.7 s
user) for its 656 tests, against 120.0 s and 134.5 s (88.8 s and 97.9 s user) for
e27e1a7's 665 — flat within this machine's noise, and over the line here as its base
is; the next change that adds pays first. Architecture
phase 3b (the trace) added ~2.1 s of in-process rows (the trace's key, its writer,
amber's currency on the committed amber file, the map's checker, an amber
evaluation's writes, one CLI refusal) and paid ~3.6 s: test_meta.InvariantClassesNeverSkip
and test_meta.NoGitOutsideVcs (harness guards, no invariant) to the full suite; its
real workers and the bracket traced afresh are the gate's. Measured on 2026-10-15 as
interleaved pairs against e27e1a7 on a machine other sessions were loading (load
average 1.5-3.0, not idle): 134.4 s and 116.4 s wall (100.0 s and 86.4 s user) for
its 674 tests against 126.3 s and 121.1 s (93.7 s and 89.1 s user) for e27e1a7's 665
— +8 s in one pair and -5 s in the other, within that machine's noise and not shown
under the line; an earlier pair before the pay-back read 112.5 s against 106.4 s.
The review of
architecture phase 5 added ~0.3 s (its in-process rows: a Failing claim never worded
inside its limit, the project's group, a claim named from another board, the checker
over baked prose, the scan's units) and paid ~2.9 s: the two records-sweep rows
phase 5 added (`site stamp --all`, `site badge`, ~1.8 s) to tests of their own left
to the gate, and test_meta.NoSubprocessOutsideRun (~1.1 s, a harness guard) to the
full suite, so the ~2.5 s phase 5 left standing is paid. Measured on 2026-10-15 as
two interleaved pairs against c392078 (load average 0.9-2.2, not idle: the base
itself ran 113-114 s on that machine, where the data side had measured 96.5 s): 110.3
s and 113.9 s wall (83.2 s and 85.3 s user) for its 665 tests, against 113.0 s and
114.1 s (85.4 s and 86.4 s user) for c392078's 661. Under its base, not shown under
the line: no idle run was had, so the next change that adds pays first. Architecture
phase 5's template side added ~0.25 s (`TheBrowserComputesNoTruth` and the template's style
scan, both source scans) and paid ~1.5 s: test_meta.EnvIsFaithful and
test_meta.NoTestShadowsTheFramework to the full suite (harness guards, no invariant;
named where `test_meta` was). Measured on 2026-10-15 as interleaved pairs against the
data side's ea175d5 on a machine other sessions were loading (load average 1.0-10.6,
not idle): 103.7 s and 99.9 s wall (76.2 s and 74.3 s user) for its 661 tests against
115.1 s and 108.4 s (85.0 s and 80.1 s user) for ea175d5's 660 (a third pair, 197 s
against 116 s, ran beside another session's suite and is not counted). Not slower than
its base, and still over the line on that machine: the rest the data side left (below)
stands, and the next change that touches the tier pays first. Architecture phase 5
(the site) is OVER the line, said here rather than hidden: two interleaved pairs on
2026-10-15 (load average 0.6-1.6) measured 96.5 s and 97.7 s wall (71.5-72.2 s user)
for its 661 tests, against 89.4 s and 93.8 s (68.6-69.5 s user) for fe9f6e3's 647.
What it added, by `--durations` against the same base (~3.8 s of test time): its
invariant 14 and 12 rows and the groups record in process (~0.9 s, after sharing one
bracket copy and its resolution across the in-process rows: first written, they cost
~2.5 s), the two rows the design added to both records sweeps, `site stamp --all` and
`site badge` (~1.8 s, kept: a command that writes is what those sweeps exist to
catch), the site register's sentences in the hardware-clause rows (~0.3 s) and the
meta scans over four more test modules (~0.4 s). What it paid (~1.2 s): to the full
suite, test_cache.PairedEntries, test_packs.NoLeakedProvenance,
test_prerequisites.ApplyingTheRuleTwiceMovesNothing and the graph's four neighbours
(none an invariant class, each named where it was); to the gate, the number scan's
build row. The rest (~2.5 s) stands: the next change that touches the fast tier pays
it back first. The review of
phase 3a added ~1.3 s (the evaluator edit that reads behind, the hand-bumped schema,
the forgery read back through five more readers, the doctor row) and paid ~1.3 s:
the doctor row to the gate, and the graph's and the round record's bytes on another
path to the full suite. Measured as two interleaved pairs on 2026-10-15 under a load
of 1.9-2.6 (not idle): 109.0 s and 92.5 s wall (68.8-68.9 s user) for its 647 tests,
against 96.2 s and 92.5 s (68.1-68.3 s user) for 6440bad's 647 — flat on wall in the
quieter pair, +0.6 s user: at the line, so the next change that adds pays first.
Architecture phase 3a (the graph and the round record) paid back what it added and measured flat,
within the machine's noise, as two interleaved pairs in one session on 2026-10-14
(load average 0.8-1.4): 92.0 s and 92.6 s wall (68.0-68.3 s user) for its 647 tests,
against 92.5 s and 92.3 s (67.5-67.7 s user) for c26fcd5's 767 — at the line, so the
next change that adds pays first. What it added (~3.8 s): its invariant classes'
in-process rows (`round` beside every other command, the source scan with its planted
reader, a resealed forgery, the record against the composition, the behind states,
an edge moving no status), the graph's in-process neighbours, and `round`'s watched
run in the records sweep (~1.3 s). What it paid, measured first at +5 s wall and then
in three steps: test_vocabulary.QualificationWordsComeFromOneTable, test_freshness,
test_readers and test_trace to the full suite — none an invariant class, each named
where it was. Architecture
phase 2 (decisions keep their losers) paid back what it added and measured flat,
within the machine's noise, as two rounds of two interleaved pairs in one session on
2026-10-14 (load average 0.9-2.2): 91.3 s and 98.3 s, then 94.1 s and 94.4 s wall
(66.8-69.0 s user) for its 764 tests, against 89.4 s and 91.5 s, then 92.7 s and
95.2 s (65.5-69.6 s user) for e138fc8's 775 — at the line, not under it, so the next
change that adds pays first. What it added (~3.5 s): its invariant classes'
in-process rows (~1.9 s: `prefer` over every agent marker, a pipe, another identity
and a mistyped id, each with its planted violator; a nominee alone; the sealed file's
tamperings; the binding to what the person read; a return condition read only on a
Checked claim, `check`'s *may return* and every reader agreeing; basis never a
status, a key or a qualification route; a proposer never an evaluator), the
`why <decision>` row in both records sweeps (~0.9 s) and the bracket's decision in
every sweep's index. What it paid (~3.8 s): its own rows of no invariant class
(the typed rejection's reader, the earlier seals pinned, every bundled basis, an
input's looked-up basis, the packs line), the writer scan and its subprocess rows
to the gate; `propose`'s listing out of the records sweeps (it reads pack.json files
only; `propose`'s writes are held by test_proposers and the phase's writers row);
and six entries of no invariant class to the full suite, each named where it was:
test_stdlib_only (CI's first step runs the same walk), test_codeload,
test_vocabulary.StatusWordsComeFromOneTable, test_admission.SweepOrder,
test_prerequisites.UnselectedDependentFollowsTheSweep and
test_louder.WorstIsTheMostUrgent. Architecture phase 1 (requirements and mechanisms) paid back what it added, measured as two
interleaved pairs in one session on 2026-10-14 (load average 0.7-2.3): 92.3 s and
89.8 s wall (65.5 s user) for its 769 tests, against 90.4 s and 91.3 s wall (67.4-67.7 s
user) for 94b9963's 751. What it added (~4 s): its invariant classes' in-process rows
(~1.5 s: the seal pinned across both spellings, both spellings keying alike, a
mechanism moving no status, a requirement never above its worst claim, every reader
agreeing on one, *ready* staying the claims', the requirement record), `gap` now
resolving as `status` does in each records sweep's gap rows (~2 s), and `gap` and
`why R1` in the vocabulary world (~0.5 s). What it paid (~4.2 s):
`test_status_table.UnqualifiedReadsGap` (3.5 s; not an invariant class, the composition
it reads held in process by UnqualifiedBesideAPassIsNeverChecked and StatusTable) and
`test_renderers.BlockingTagIsStatusTag`'s `check` run (0.7 s) to the full suite. Still
at the line, not under it: the next change that adds pays first. The review of phase 1
added six in-process rows (~0.03 s: a requirement over a disagreeing claim, the capped
requirements line, the requirement-claim id collision) and measured flat: 89.4 s and
89.5 s wall for its 775 tests against 89.3 s and 92.2 s for b67e135's 769, interleaved
in one session on 2026-10-14 (load average 1.2-1.5). The review of
P2.5b added eight rows (the in-process halves of its laundering paths, ~0.3 s;
its end-to-end rows are left to the gate, below) and measured flat: 93.6 s wall
(67 s user) for 746 tests against 93.6 s for 7e71aac's 738 in the same session,
2026-10-04 — still over, so the rule below stands. P2.5b is OVER
it, said here rather than hidden: measured 2026-10-04 within the hour (load
average 0.7-1.3), 93.4 s wall (67.8 s user) for 738 tests, against 88.3 s wall
(63.6 s user) for `ecaad99`'s 713 on the same machine. What it added (~13 s,
measured test by test with --durations): invariant 12's in-process rows and
the in-process halves of 7, 8 and 11 over an exported article (~3 s, each with
its planted violator); four rows in each records sweep — `export`, its JSON,
its dry run, `report --milestone` (+3.8 s; kept, as every row of those sweeps
is: a command that writes is what they exist to catch); the export channel in
the vocabulary world and the imports of two more test modules. What it paid
back (~8 s): `claim list --status` reads a bracket copy of its own instead of
building that world (-4.7 s, same assertions); the forged export's end-to-end
row (re-run filed, read back through `status`) and the one-time migration's
index row left to the gate, named below; the Fig. 4 and bracket export rows
here run `check` and `export` in process. The remaining ~3.5 s stands: the
next change that touches the fast tier pays it back first. Before it, the review of
P2.5a added fifteen in-process rows (~2 s: its laundering paths, each with its
planted violator) and moved nothing out: measured 2026-10-04, 86.7 s wall (62.3 s
user) for 713 tests — under the budget, so the next change that adds pays for
itself. P2.5a paid back
what P2.3 left over it, as its rule required: measured 2026-10-04, 83.7 s wall
(59.9 s user, load average 0.9) for 698 tests, against 107.2 s wall (79.5 s
user) for d23ff8e's 710 the same morning. What P2.5a added: its invariant
classes' in-process rows (~1.5 s: the channel table, the sealed file's
tamperings, the judge's facts, a fail across every edit, an authority's rows,
the checked section, the article moved by a nudge, the AST scans) and one pty
row (~0.9 s). What it moved out, each named in LEFT_FOR_THE_GATE or below with
its reason (~30 s): invariant 2's planted project (test_louder.ErrorIsLouder's
command rows) with test_json_keys, which reads the same project; S-05/S-07 and
S-20/S-21/S-32 through the CLI; P2.1's owner projects; UnqualifiedBesideAPass's
end-to-end project; StatusLinesSpeakTheTable's three projects — each the
commands half of a rule an in-process row here still holds. The records sweeps
and the bundled seal tests stay, as below. Before it, P2.3 was OVER the budget,
said here rather than hidden. The review of P2.4 added what it cost and
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
* the site — its template, `site.py`, `page.py`, `prose.py` or `graph.py`
  (architecture phase 5) -> `test_map`, `test_site_truth` and `test_style`
  whole, and `test_site`;
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
    # way to green, so this runs every iteration. ~2 s. (Architecture phase 5's
    # template side paid with two of its classes, ~1.5 s, left to the full suite:
    # test_meta.EnvIsFaithful (the test environment `_env.run` builds, 0.9 s) and
    # test_meta.NoTestShadowsTheFramework (an AST walk over every test module,
    # 0.6 s). Neither holds an invariant; each guards the harness, which an
    # iteration on the spine or the site does not touch. The review of phase 5
    # paid with a third of the same kind, ~1.1 s: test_meta.NoSubprocessOutsideRun
    # (an AST walk over every test module for a process started outside
    # `_env.run`); an iteration that adds a test which starts a process runs
    # test_meta whole. Architecture phase 3b paid with two more of the kind,
    # ~3.6 s: test_meta.InvariantClassesNeverSkip (an AST walk over every
    # invariant class for a skip, 2.0 s) and test_meta.NoGitOutsideVcs (an AST
    # walk over the spine and the tests for git outside `vcs`, 1.6 s) — harness
    # guards, no invariant; an iteration that adds a test to an invariant class,
    # or a git call, runs test_meta whole.)
    "test_meta.EveryInvariantHasItsTest",
    "test_meta.TheInvariantTableIsTheMap",
    "test_meta.NoWallClockBelowTheEdge",
    # (Architecture phase 6's pay-back: test_meta.NoGitOutsideVcs, an AST walk
    # over every spine module for a git process outside `vcs`, ~0.9 s, to the
    # full suite — a harness guard, no invariant class. An iteration that touches
    # `vcs.py` or starts a git process runs test_meta and test_vcs whole.)
    # (House rule: the spine is standard library only. test_stdlib_only — CI's
    # own AST walk over the spine and a planted third-party import, 0.4 s — is
    # left to the full suite by architecture phase 2's pay-back: CI's first step
    # runs the same walk on every push, before any test.)
    # The one place that decides a tool is missing: the line between SKIPPED
    # (BLOCKED) and a run. 0.2 s.
    "test_availability",
    # Every reader of a verdict (`ok`, `render`, the claim ladder, JUnit) says the
    # same thing about it, over every combination of passed/skipped/error: a
    # reader that calls a skip a pass is red here. 1.3 s. Listed class by class
    # since architecture phase 1's pay-back: BlockingTagIsStatusTag's `check`
    # run (0.7 s) is the full suite's — its in-process row stays, and the tag a
    # blocker prints is `test_louder`'s and `test_shapes`' too.
    "test_renderers.RenderersAgree",
    "test_renderers.ExplainingVerdict",
    "test_renderers.ReasonsAgree",
    "test_renderers.VerdictRowSaysOutcome",
    "test_renderers.BlockingTagIsStatusTag.test_each_blocking_status",
    "test_renderers.TheWalkWordsComeFromTheWalksConstants",
    "test_renderers.QualificationLineAgrees",
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
    # P2.5a's pay-back: the planted project (every command once, ~6 s) is left to
    # the gate, named below; its in-process half stays here.
    "test_louder.ErrorIsLouder.test_a_fail_beside_an_error_reads_failing",
    "test_louder.ErrorIsLouder.test_a_gates_line_that_folds_a_crash_into_the_skip_is_caught",
    "test_louder.ErrorIsLouder.test_a_skipped_and_errored_verdict_is_a_crash_in_either_form",
    "test_louder.ErrorIsLouder.test_render_junit_required_or_not",
    "test_louder.ErrorIsLouder.test_render_markdown",
    "test_louder.ErrorIsLouder.test_render_terminal",
    "test_louder.ErrorIsLouder.test_every_command_is_rendered_or_says_why",
    "test_louder.ErrorIsLouder.test_the_checkers_refuse_what_they_forbid",
    # (WorstIsTheMostUrgent — `last_check.json`'s worst claim by
    # `claims.SEVERITY_ORDER`, 0.3 s — is left to the full suite by architecture
    # phase 2's pay-back: no invariant class; the order it reads is held by
    # test_status_table.StatusTable here.)
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
    # (P2.5a's pay-back: its end-to-end project and that project's checker, ~3 s,
    # are left to the gate below.)
    "test_status_table.UnqualifiedBesideAPassIsNeverChecked.test_a_pass_beside_an_unadmitted_evaluator_is_never_checked",
    "test_status_table.UnqualifiedBesideAPassIsNeverChecked.test_a_resolver_that_lets_the_pass_win_is_caught",
    # (Architecture phase 1's pay-back: UnqualifiedReadsGap, ~3.5 s, not an
    # invariant class, is the full suite's — the composition it reads is held in
    # process by UnqualifiedBesideAPassIsNeverChecked above and StatusTable.)
    # P2.1's vocabulary and JSON: GLOSSARY §3's words from one table on every
    # channel (the louder and refused worlds shared with the two modules above,
    # one process; a bracket, an empty project and an unevaluated one of its
    # own), *ready* as one predicate in process, the JSON keys kept beside the
    # words, and an owner written by hand counting for nothing (planned 11).
    # V15 through the commands — seven bracket copies at 8 mm, ~25 s — is left
    # to the full suite (below): the predicate it reads is held here, in process.
    # (P2.5a's pay-back: StatusLinesSpeakTheTable — three projects run through
    # every command, ~5 s — is left to the full suite: no invariant reads it, and
    # StatusWordsComeFromOneTable held its routing here until architecture phase
    # 2's pay-back left it to the full suite too, 0.8 s: no invariant reads it,
    # and StatusWordsAreTheGlossarys holds the words themselves here.)
    "test_vocabulary.StatusWordsAreTheGlossarys",
    "test_vocabulary.ReadyIsThePredicate",
    # (Architecture phase 3a's pay-back: QualificationWordsComeFromOneTable —
    # patching `HUMAN["qualification"]` moves every channel that shows a
    # qualification, one bracket copy, every command twice, ~3 s — is left to the
    # full suite: no invariant class reads it, and an iteration touching a
    # qualification word or its routing runs test_vocabulary whole.)
    # (P2.5a's pay-back: test_json_keys — the kept JSON keys, read through the
    # louder project's commands, ~7 s — is left to the full suite: no invariant
    # reads it; an iteration touching a JSON channel runs it whole.)
    # P2.1's owner (planned 11 until P2.5a, now invariant 11 with test_signing and
    # test_physical, above): its in-process rows. P2.5a's pay-back left its two
    # planted projects — every command run once on each (~5 s) — to the gate.
    "test_owner.AnOwnerWrittenByHandNeverCounts.test_an_owner_counts_only_as_recorded_and_only_as_it_still_reads",
    "test_owner.AnOwnerWrittenByHandNeverCounts.test_a_compose_that_trusts_the_file_is_caught",
    "test_owner.AnOwnerWrittenByHandNeverCounts.test_the_strict_reader_accepts_owner_and_names_a_misspelling",
    "test_owner.ARecordedResultNeverOutranksTheEvaluators.test_a_status_built_from_the_result_alone_is_caught",
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
    # (Architecture phase 3a's pay-back: test_freshness — freshness, admission
    # state and the one resolver, 2.1 s — is left to the full suite: no invariant
    # class reads it, invariants 7 and 9 are held here by their own classes, which
    # read it through `resolve`, and an iteration touching `verdicts.freshness`,
    # `admission_state` or `resolve` runs it whole.)
    # (Architecture phase 3a's pay-back, continued: test_readers — the report and
    # the site keep no staleness rule of their own, 1.7 s — is left to the full
    # suite: no invariant class reads it, the louder and status-table rows here
    # render through the same resolver, and an iteration touching a renderer's
    # staleness runs it whole.)
    # (Architecture phase 3a's pay-back, last: test_trace — what a gate read is
    # what its verdict is keyed by, 1.5 s — is left to the full suite: no
    # invariant class reads it, invariant 7's classes here key through it, and an
    # iteration touching `ParamTrace`, `LedgerView` or the audit hook runs it whole.)
    # Digests from bytes; the stat cache never serves a stale one (S-22). 0.1 s.
    "test_digests",
    # (The recording loader — code a gate or fixture loads is keyed by its bytes,
    # test_codeload, 0.4 s — is left to the full suite by architecture phase 2's
    # pay-back: test_trace held the record it keys by until phase 3a left it to
    # the full suite too, and an iteration touching `modelio`'s loader runs
    # test_codeload and test_trace whole, as the escalation rule below says for
    # the recording loader.)
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
    # (S-05 and S-07 through the commands a human types — the four cheapest
    # CLI rows, ~8 s — were left to the gate by P2.5a's pay-back, named below.)
    # (A missing tool reads skipped, never "not admitted"; availability comes
    # before admission: test_admission.SweepOrder, 0.7 s, left to the full suite
    # by architecture phase 2's pay-back — no invariant class, and invariant 1's
    # SkipIsNotPass holds the skip's reading here.)
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
    # (Architecture phase 5's pay-back: test_cache.PairedEntries, the control
    # entry's paired shape and its strict reader, ~0.3 s, to the full suite —
    # no invariant class; QualificationIsPaired above reads the same entries.)

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
    # (The three defects first read through the CLI — S-20, S-21, S-32, ~7.5 s —
    # were left to the gate by P2.5a's pay-back, named below; their in-process
    # twins stay above.)
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
    # (UnselectedDependentFollowsTheSweep, 0.3 s, left to the full suite by
    # architecture phase 2's pay-back: no invariant class, and the merged view
    # it reads is PrerequisiteFailureIsNeverAPass's above.)
    # (Architecture phase 5's pay-back: test_prerequisites.ApplyingTheRuleTwice-
    # MovesNothing, ~0.1-0.2 s, to the full suite — no invariant class; the rule
    # itself is PrerequisiteFailureIsNeverAPass's, above.)
    # (Architecture phase 6's pay-back: test_prerequisites.PlanIsStable's three
    # rows, ~0.05 s, to the full suite — the run order, no invariant class.)

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
    # (Architecture phase 6's pay-back: test_goalposts.Margins and
    # AClaimReadIsTheWholeRecord, ~0.2 s, to the full suite — no invariant
    # class; a limit edit re-keying exactly its readers is TheGoalpostLivesInClaims'.)
    "test_context.OutsideTheContextAPassDoesNotCount.test_the_composition_reads_gap",
    "test_context.OutsideTheContextAPassDoesNotCount.test_the_breach_rule",
    "test_context.OutsideTheContextAPassDoesNotCount.test_the_mark_is_on_passes_alone_and_idempotent",
    "test_context.OutsideTheContextAPassDoesNotCount.test_a_declared_key_its_run_never_reads_is_an_error",
    "test_context.OutsideTheContextAPassDoesNotCount.test_a_guard_outside_its_context_establishes_nothing",
    "test_context.AFailOutsideStillCounts.test_the_mark_never_launders_a_fail",
    "test_context.KnownGoodOutsideIsUnqualified.test_the_judge_reads_known_good_outside",
    "test_context.AMutationPassingOutsideStillCounts",
    "test_context.AnOwnedFallbackReadsAssumed",
    # (Architecture phase 6's pay-back: test_context.MalformedContextsAreRefused
    # and NoContextMovesNothing, ~0.1 s, to the full suite — no invariant class.)

    # -- invariant 8: records are the source, the index an output --------- #
    # The class but its legacy row, the index-agrees test among it: a
    # hand-edited index loses, every record change reaches it, and every
    # command run after a hand edit leaves `agree()` empty (13 s of the 15).
    # ~15 s. (P2.5b's pay-back: the one-time migration's row is left to the
    # gate, below, with the legacy sweeps.)
    "test_records.IndexNeverDisagreesWithRecords.test_a_hand_edited_index_loses_to_the_records",
    "test_records.IndexNeverDisagreesWithRecords.test_every_record_change_reaches_the_index",
    "test_records.IndexNeverDisagreesWithRecords.test_the_build_is_deterministic",
    "test_records.IndexNeverDisagreesWithRecords.test_the_index_is_ignored_through_fnmatch",
    "test_records.IndexNeverDisagreesWithRecords.test_an_input_rewritten_in_place_drifts",
    "test_records.IndexNeverDisagreesWithRecords.test_unregistered_inputs_are_hand_dropped_evidence",
    "test_records.IndexNeverDisagreesWithRecords.test_the_index_is_written_only_when_it_changes",
    "test_records.IndexNeverDisagreesWithRecords.test_the_index_holds_no_verdict_status_or_coverage",
    "test_records.IndexNeverDisagreesWithRecords.test_the_records_digest_moves_with_a_record_and_not_with_the_index",
    "test_records.IndexNeverDisagreesWithRecords.test_a_legacy_project_gets_no_index",
    "test_records.IndexNeverDisagreesWithRecords.test_every_command_leaves_the_index_agreeing",
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
    # Architecture phase 3a: `round`, watched, writes what `check` writes and
    # exactly the round record of the state after it, and nothing the second
    # time. ~1.3 s.
    "test_records.NoCommandWritesARecord.test_round_writes_what_check_writes_and_its_round_record",
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
    # (Architecture phase 5's pay-back: test_packs.NoLeakedProvenance, the
    # whole walk and its planted violators, ~0.15 s, to the full suite — a house
    # rule, no invariant class; CI runs the full suite before every merge.)
    # -- invariant 11 (P2.5a): the channel, the sealed results file, an owner
    # and an authority only as recorded, what a physical result can do — and 4,
    # 7 and planned 12 over a physical claim. Their in-process rows (~1.5 s): the
    # channel's table and its planted channels, the parser that has no channel
    # flag, the refused `--who` with its planted parser, the seal and the chain
    # over every tampering with their planted readers, the strict reader's
    # closed keys, `append_signed`, `save`'s refusal, the AST scans for an
    # `owners=` caller and a view builder that skips the judge, the judge's one
    # row per fact with its stubbed check, a fail across every channel and
    # edit, an authority's rows, the checked section's rows, the article moved
    # by a nudge; and one `run_tty` row from V-1 (a mistyped id or no answer
    # writes nothing, ~2 s). The rest — every channel end to end, Fig. 4 — are
    # left to the gate, below, each named.
    "test_signing.HumanChannelOnly.test_the_channel_table",
    "test_signing.HumanChannelOnly.test_planted_channels_are_caught",
    "test_signing.HumanChannelOnly.test_no_flag_sets_the_channel",
    "test_signing.HumanChannelOnly.test_a_mistyped_id_or_no_answer_writes_nothing",
    "test_signing.WhoAndWhenAreNeverTyped.test_a_parser_that_accepts_who_is_caught",
    "test_signing.WhoAndWhenAreNeverTyped.test_help_lists_neither_and_one_list_names_both",
    "test_signing.TheResultsFileIsSealedAndChained.test_a_hand_written_attribution_is_refused",
    "test_signing.TheResultsFileIsSealedAndChained.test_a_legacy_prefix_and_a_sealed_chain_read",
    "test_signing.TheResultsFileIsSealedAndChained.test_an_entry_copied_to_another_claim_does_not_verify",
    "test_signing.TheResultsFileIsSealedAndChained.test_append_keeps_every_earlier_entry_and_refuses_a_broken_file",
    "test_signing.TheResultsFileIsSealedAndChained.test_every_tampering_is_refused",
    "test_signing.TheResultsFileIsSealedAndChained.test_planted_readers_are_caught",
    "test_signing.TheResultsFileIsSealedAndChained.test_save_never_appends_an_unsealed_entry_after_a_sealed_one",
    "test_signing.TheResultsFileIsSealedAndChained.test_the_strict_reader_closes_every_key",
    "test_signing.AnOwnerOnlyThroughTheChannel.test_no_spine_module_passes_owners",
    "test_signing.AnOwnerOnlyThroughTheChannel.test_planted_owner_paths_are_caught",
    "test_physical.SignedMeansSomething.test_a_every_fact_holds",
    "test_physical.SignedMeansSomething.test_b_c_d_the_channel",
    "test_physical.SignedMeansSomething.test_e_evidence_changed_then_restored",
    "test_physical.SignedMeansSomething.test_f_no_one_recorded_it",
    "test_physical.SignedMeansSomething.test_g_a_raw_ledger_never_judges",
    "test_physical.SignedMeansSomething.test_i_the_claim_half_moved",
    "test_physical.SignedMeansSomething.test_j_a_measured_value_its_acceptance_refutes",
    "test_physical.APhysicalFailNeverLosesItsPowerToFail.test_in_process_every_fail_counts",
    "test_physical.AContradictionGoesOnTheEvaluatorsTrackRecord.test_in_process_only_a_counted_current_pass_is_contradicted",
    "test_physical.AnExpertJudgmentStaysWithItsAuthority.test_in_process_the_rows",
    "test_physical.TheCheckedSectionHoldsOnlyBoundResults.test_in_process_rows",
    "test_physical.AMovedArticleReadsStale.test_in_process_a_nudge_moves_the_article",
    "test_physical.RenderersAgreeOnPhysicalClaims.test_no_view_builder_skips_the_judge",
    "test_physical.RenderersAgreeOnPhysicalClaims.test_a_planted_view_builder_is_caught",
    # The review of P2.5a's laundering paths, each in process with its planted
    # violator (~2 s together, measured 2026-10-04 one by one, 0.1-0.4 s each with
    # the interpreter): the markers by exact name, the act and its flag, a value
    # that is not text, every entry a restore discards (against a real git
    # history), a name with spaces around it, an owner changed back, a limit
    # edited after a measured pass, a fail and a contradiction outliving their
    # claim file, an unregistered gate's files, a rewritten judgment, a
    # judgment's fail, the page's judgment group.
    "test_signing.HumanChannelOnly.test_the_marker_is_named",
    "test_signing.WhoAndWhenAreNeverTyped.test_the_act_and_its_flag_never_disagree",
    "test_signing.WhoAndWhenAreNeverTyped.test_a_value_that_is_not_text_is_refused",
    "test_signing.TheResultsFileIsSealedAndChained.test_every_entry_a_restore_discards_is_named",
    "test_signing.TheResultsFileIsSealedAndChained.test_planted_advice_is_caught",
    "test_signing.AnOwnerOnlyThroughTheChannel.test_a_name_with_spaces_around_it_is_the_name",
    "test_signing.AnOwnerOnlyThroughTheChannel.test_an_owner_changed_back_reads_assumed_again",
    "test_physical.SignedMeansSomething.test_k_a_limit_edited_after_a_measured_pass",
    "test_physical.APhysicalFailNeverLosesItsPowerToFail.test_in_process_a_fail_outlives_its_claim_file",
    "test_physical.AMovedArticleReadsStale.test_in_process_an_unregistered_gates_files_are_no_part_of_the_article",
    "test_physical.AContradictionGoesOnTheEvaluatorsTrackRecord.test_in_process_a_contradiction_outlives_its_claim_file",
    "test_physical.AnExpertJudgmentStaysWithItsAuthority.test_in_process_a_rewritten_judgment_is_no_judgment",
    "test_physical.AnExpertJudgmentStaysWithItsAuthority.test_in_process_a_judgments_fail_names_no_article",
    "test_physical.RenderersAgreeOnPhysicalClaims.test_the_page_groups_an_expert_judgment_apart",
    "test_priors.ShippedTextCarriesNoScenarioPrior.test_planted_terms_are_found",
    # -- invariant 12 (P2.5b): ready is one predicate, the boundary re-executes,
    # going ahead is a decision, an export is a sealed record — and 7, 8 and
    # 11 over an exported article. In process, each with its planted violator
    # (~4 s together, measured 2026-10-04 one by one): the predicate's rows and
    # every reader of it, the export record's tamperings and its one writer
    # (an AST scan), the strict milestone reader and the index's rows, the
    # supersession rows and their planted judges, the hardware clause in every
    # branch, the article prediction's planted predictions; and four export
    # runs in process on a bracket or Fig. 4 copy (0.3-0.9 s each): the
    # planted boundaries (cache served, tier 0, no prerequisite expansion), a
    # forged cache pass refused as a disagreement beside the planted finder
    # that misses it, the planted dry-run predicate, a fail on an exported
    # article charged to the verdicts the export sealed. The rest run the commands end to end — left to the gate,
    # below, each named.
    "test_export.ReadyIsOnePredicate",
    "test_export.TheBoundaryReExecutes.test_the_planted_boundaries_are_caught",
    "test_export.ACacheThatLiesIsCaughtAtTheBoundary.test_a_planted_disagreement_finder_is_caught",
    "test_export.DryRunIsTheSamePath.test_a_planted_dry_run_predicate_is_caught",
    "test_export.GoingAheadIsAPersonsDecision.test_a_record_that_drops_the_claims_is_caught",
    "test_records.TheExportRecordIsSealedAndChained",
    "test_records.MilestonesAreRecords.test_the_strict_reader",
    "test_records.MilestonesAreRecords.test_the_index_lists_milestones_and_exports",
    "test_physical.AFailIsSupersededOnlyOnAnotherExportedArticle",
    "test_physical.AResultBindsToAnExportedArticle.test_a_fail_is_charged_to_the_verdicts_sealed_at_export",
    "test_vocabulary.TheHardwareClauseIsAlwaysSaid",
    "test_fig4.TwoArticlesExactly.test_the_planted_predictions_are_caught",
    # The review of P2.5b's laundering paths, in process (~0.2 s together): one
    # article's every export record charged; a crash said errored in a
    # refusal; every cache reader's *ready* said as last evaluated, over the
    # seeded ledgers. (Its supersession rows — byte-identical reprints, a fail
    # whose bytes nobody holds — run in AFailIsSuperseded… above, whole.)
    "test_physical.AResultBindsToAnExportedArticle.test_in_process_every_record_of_one_article_is_read",
    "test_louder.ErrorIsLouder.test_an_export_refusal_says_errored",
    "test_export.EveryCacheReaderSaysAsLastEvaluated.test_a_cache_reader_says_ready_as_last_evaluated",
    # -- architecture phase 1 (SPEC decisions 12, 13): claims say how they
    # settle. In process, each with its planted violator (~1.5 s together,
    # measured 2026-10-14): the seal pinned across both spellings (11), both
    # spellings keying alike and an edit re-keying exactly its readers (7), a
    # mechanism moving no status and a requirement never more resolved than its
    # worst claim (4), every reader agreeing on a requirement and *ready*
    # staying the claims' (12), and the requirement record's strict reader,
    # pointers, index and migration (8). The pty row, the bracket through the
    # commands and doctor's row are left to the gate, below.
    "test_mechanism.TheSealSurvivesTheNewSpelling.test_the_seal_form_is_pinned",
    "test_mechanism.TheSealSurvivesTheNewSpelling.test_pointers_and_expectations_are_not_sealed",
    "test_mechanism.TheMechanismMovesNoKey",
    "test_mechanism.AMechanismNeverMovesAStatus",
    "test_requirements.ARequirementIsNeverMoreResolvedThanItsClaims",
    "test_requirements.EveryReaderAgreesOnARequirement.test_the_small_world_reads_as_typed",
    "test_requirements.EveryReaderAgreesOnARequirement.test_every_channel_agrees",
    "test_requirements.EveryReaderAgreesOnARequirement.test_the_words_are_humans",
    "test_requirements.EveryReaderAgreesOnARequirement.test_the_page_takes_its_rows_from_the_producer",
    "test_requirements.EveryReaderAgreesOnARequirement.test_a_section_that_folds_statuses_itself_is_caught",
    "test_requirements.EveryReaderAgreesOnARequirement.test_a_disagreeing_claim_is_named_under_its_requirement",
    "test_requirements.EveryReaderAgreesOnARequirement.test_a_requirement_reader_that_drops_the_disagreement_is_caught",
    "test_requirements.EveryReaderAgreesOnARequirement.test_a_capped_line_names_the_worst_first",
    "test_requirements.EveryReaderAgreesOnARequirement.test_a_line_cut_in_record_order_is_caught",
    # The two pages: a planted card producer, baked in process over the small world.
    "test_requirements.EveryReaderAgreesOnARequirement.test_a_marketing_card_that_says_more_is_caught",
    "test_requirements.ReadinessStaysTheClaims",
    "test_records.RequirementsAreRecords.test_the_strict_reader",
    "test_records.RequirementsAreRecords.test_a_claims_pointers",
    "test_records.RequirementsAreRecords.test_the_index_lists_them_and_names_the_problems",
    "test_records.RequirementsAreRecords.test_a_legacy_project_keeps_its_requirements",
    "test_records.RequirementsAreRecords.test_a_requirement_id_that_is_a_claim_id_is_named",
    # -- architecture phase 2 (SPEC 14, 22, 25, 38): decisions keep their
    # losers. In process, each with its planted violator: a preference only
    # through the person's channel — every agent marker, a pipe, another
    # identity, a mistyped id; `by` alone; a sealed, chained file; bound to what
    # the person read (13); a return condition read only on a Checked claim,
    # `check`'s *may return* moving no exit code, every reader agreeing (12);
    # basis never a status, a key or a qualification route (4, 7, 9); a proposer
    # never an evaluator, `propose` refusing with nothing written (3, 8); the
    # preference record kind (8). The pty, the real environment's markers, a
    # committed tamper's git walk, the writer scan and the proposer subprocess
    # rows are left to the gate, below; the classes of no invariant (the typed
    # rejection's reader, the earlier seals pinned, every bundled basis, an
    # input's looked-up basis, the packs line) to the full suite, as the phase's
    # own pay-back.
    "test_preferences.APreferenceIsAPersonsAct.test_the_person_records_it_in_their_own_shell",
    "test_preferences.APreferenceIsAPersonsAct.test_every_channel_but_the_persons_is_refused",
    "test_preferences.APreferenceIsAPersonsAct.test_each_refusal_says_why",
    "test_preferences.APreferenceIsAPersonsAct.test_who_and_when_are_refused_before_the_project_is_read",
    "test_preferences.APreferenceIsAPersonsAct.test_a_planted_prefer_that_skips_the_marker_is_caught",
    "test_preferences.APreferenceIsAPersonsAct.test_value_names_which_alternative",
    "test_preferences.ANomineeAloneNeverCounts",
    "test_preferences.ThePreferenceFileIsSealedAndChained.test_the_sealed_file_reads",
    "test_preferences.ThePreferenceFileIsSealedAndChained.test_a_hand_written_unsealed_file_is_refused",
    "test_preferences.ThePreferenceFileIsSealedAndChained.test_each_tampering_is_refused",
    "test_preferences.ThePreferenceFileIsSealedAndChained.test_the_writer_refuses_to_extend_a_broken_file",
    "test_preferences.APreferenceIsBoundToWhatThePersonRead",
    "test_returns.AConditionReadsOnlyACheckedClaim",
    "test_returns.MayReturnNeverMovesTheExitCode",
    "test_returns.EveryReaderAgreesOnAReturnCondition",
    "test_basis.ABasisNeverChangesAStatus",
    "test_basis.ABasisIsNeverAKey",
    "test_basis.ABasisNeverQualifies",
    # Review of the motorBike design's P3-P5 (invariant 4): no aero guard covers
    # the drag-area tag, so a drag-area claim is never Checked on guards alone.
    # The registry and one composition, in process (~0.05 s).
    "test_aero_openfoam.NoGuardSpeaksForTheDragArea",
    # The motorBike design's P6 (invariants 4, 9 and 12), through the stand-in
    # OpenFOAM: the drag area at the top of its band on the baseline's replayed
    # ladder (~1.3 s), its claim in cm^2 or as a lower limit refused before any
    # mesh (~0.2 s), its four goalpost moves (~1.7 s), and a record of other
    # inputs never read (~2.5 s). The rest of each class is the gate's.
    "test_aero_openfoam.TheDragAreaIsJudgedAtTheTopOfItsBand.test_the_baseline_passes_at_the_top_of_its_band",
    # Review of P6: the control note names the fixture's own scale (the
    # registry and bad_cases.py, in process, ~0.1 s).
    "test_aero_openfoam.TheDragAreaIsJudgedAtTheTopOfItsBand.test_the_control_says_the_fixtures_scale",
    "test_aero_openfoam.TheDragGateStatesItsOwnUnits",
    "test_aero_openfoam.TheDragAreasGoalpostMovesNothingElse",
    "test_aero_openfoam.ALadderRecordIsReadOnlyByItsProcess.test_another_input_byte_is_another_record",
    "test_proposers.AProposerIsNeverAnEvaluator",
    "test_proposers.ProposeWritesOnlyItsInputs.test_in_process_a_refusal_writes_nothing",
    "test_records.PreferencesAreRecords",
    # Architecture phase 3a (invariants 7, 8 and 12): only `round` writes the
    # round record, in process over every other command (~0.6 s); nothing reads it
    # for truth — the source scan, its planted reader and a resealed forgery in
    # process, read back through `report`, `claim list`, `why`, `gap` and the
    # page (~1.3 s); it says what the composition says; it says when the
    # records, the cache, the design or the resolution moved past it, and a
    # hand-bumped schema is an edit (~1.2 s); and no graph edge moves a status.
    # `round`'s watched run in the records sweep is in the NoCommandWritesARecord
    # entries above (~1.3 s). Its review added ~1 s here and paid it below.
    "test_round.OnlyRoundWritesTheRoundRecord",
    "test_round.NothingReadsTheRoundRecordForTruth.test_the_spine_reads_it_nowhere",
    "test_round.NothingReadsTheRoundRecordForTruth.test_a_planted_reader_is_caught",
    "test_round.NothingReadsTheRoundRecordForTruth.test_a_forged_record_moves_nothing",
    "test_round.TheRoundRecordSaysWhatTheCompositionSays",
    "test_round.ABehindRoundRecordSaysSo.test_status_says_current_then_what_moved",
    "test_round.ABehindRoundRecordSaysSo.test_each_input_is_seen",
    "test_round.ABehindRoundRecordSaysSo.test_a_state_blind_to_the_cache_is_caught",
    "test_round.ABehindRoundRecordSaysSo.test_edited_unreadable_and_newer",
    "test_round.ABehindRoundRecordSaysSo.test_an_evaluator_edit_reads_behind",
    "test_round.TheRoundRecordIsByteStable.test_it_is_tracked_in_an_init_project",
    # Architecture phase 6 (invariant 8): the round's commit stages only the
    # project's own paths inside its directory — the bracket in a subtree beside
    # the person's own work, committed in process twice (~1.5 s) — and the
    # checker's planted rows and the closed list (instant).
    "test_round.TheRoundCommitStagesOnlyTheProjectsPaths",
    # The review of phase 6 (invariant 8's identity and init clauses, ~0.9 s): a
    # round never commits as an identity `people` does not list — another
    # name, a listed name at another address, a committer from
    # `GIT_COMMITTER_*` — each in process over one bracket commit; and `init`
    # nests no repository, inside one or where git does not answer.
    "test_round.RoundsCommitOnlyAsAListedPerson.test_another_identity",
    "test_round.RoundsCommitOnlyAsAListedPerson.test_a_listed_name_at_another_address",
    "test_round.RoundsCommitOnlyAsAListedPerson.test_a_committer_people_does_not_list",
    "test_record_commands.InitStartsARepository.test_inside_a_repository_nothing_about_git_changes",
    "test_record_commands.InitStartsARepository.test_git_that_does_not_answer_starts_none",
    "test_graph.AnEdgeChangesNoStatus",
    # (Architecture phase 5's pay-back: the graph's neighbours — its bytes under
    # a reversed listing and other words, the observed view agreeing with it,
    # and the pointers it refuses to follow, ~0.4 s together, none an invariant
    # class — to the full suite, as the design for this phase named first. The
    # map's own determinism row below bakes the same graph under a reversed
    # listing, and an iteration touching `graph.py` runs test_graph whole.)
    # Architecture phase 5 (invariants 8, 12 and 14), in process: every baked
    # token, *ready*, margin and mark against the composition over the bracket,
    # its variants and every eighth seeded ledger, the checker naming each planted
    # lie, and a planted producer refused whole by `site build` (~0.4 s); the
    # stamp states, a moved claim, a regroup, unstamped text and a planted stamp
    # state (~0.2 s); the number scan, its planted matcher and one build (~0.4 s);
    # next steps over the seeds and the settle table (~0.1 s); the groups record
    # (~0.2 s); the style scans over the site's words, the baked keys and the
    # bracket's text (~0.3 s); and the map's order and determinism (~0.2 s).
    "test_site_truth.TheSiteNeverSaysMoreThanTheResolution.test_the_bracket_and_its_variants",
    "test_site_truth.TheSiteNeverSaysMoreThanTheResolution.test_a_forced_disagreement_is_marked",
    "test_site_truth.TheSiteNeverSaysMoreThanTheResolution.test_every_eighth_seeded_ledger",
    "test_site_truth.TheSiteNeverSaysMoreThanTheResolution.test_the_checker_names_each_planted_lie",
    "test_site_truth.TheSiteNeverSaysMoreThanTheResolution.test_a_planted_producer_is_refused_whole",
    # The review of phase 5, in process over the shared bracket (~0.3 s): a
    # Failing claim never worded inside its limit, the project's group held, a
    # claim named from another board in the stamp, the checker over the baked
    # prose, and the number scan's units, joined letters and structure keys.
    "test_site_truth.TheSiteNeverSaysMoreThanTheResolution.test_a_failing_claim_never_reads_inside_its_limit",
    "test_site_truth.TheSiteNeverSaysMoreThanTheResolution.test_the_project_group_is_held_too",
    # The follow-up after phase 3b (~0.1 s warm): a counted fail recorded on a
    # bracket copy in process bakes the `fail` token, and a planted pass is named.
    "test_site_truth.TheSiteNeverSaysMoreThanTheResolution.test_a_counted_fail_bakes_the_fail_token",
    "test_site_truth.StampedProseIsFlaggedWhenBehind.test_a_claim_named_from_another_board_moves_the_stamp",
    "test_site_truth.StampedProseIsFlaggedWhenBehind.test_the_checker_reads_the_baked_prose",
    "test_site_truth.ANumberNoBakedFileHoldsIsFlagged.test_joined_units_and_bare_fractions_are_read",
    "test_site_truth.ANumberNoBakedFileHoldsIsFlagged.test_a_unit_must_agree_and_structure_never_matches",
    "test_site_truth.StampedProseIsFlaggedWhenBehind.test_the_brackets_text_is_current",
    "test_site_truth.StampedProseIsFlaggedWhenBehind.test_a_moved_claim_flags_its_board_and_no_other",
    "test_site_truth.StampedProseIsFlaggedWhenBehind.test_a_regroup_flags_the_board",
    "test_site_truth.StampedProseIsFlaggedWhenBehind.test_unstamped_text_is_flagged",
    "test_site_truth.StampedProseIsFlaggedWhenBehind.test_a_planted_stamp_state_is_named",
    # The two pages: the story stamped like the intro, in process on the shared bracket.
    "test_site_truth.StampedProseIsFlaggedWhenBehind.test_the_story_is_stamped_and_flagged_like_the_intro",
    "test_site_truth.ANumberNoBakedFileHoldsIsFlagged.test_the_precision_rule",
    "test_site_truth.ANumberNoBakedFileHoldsIsFlagged.test_what_is_scanned_and_what_is_not",
    "test_site_truth.ANumberNoBakedFileHoldsIsFlagged.test_the_prose_never_matches_itself",
    "test_site_truth.ANumberNoBakedFileHoldsIsFlagged.test_a_planted_matcher_is_caught",
    # The template side of invariant 14: source scans, ~0.2 s.
    "test_site_truth.TheBrowserComputesNoTruth",
    "test_site_truth.NextStepsAreThePredicate",
    "test_records.GroupsAreRecords",
    "test_style",
    "test_map.TheMapIsBaked",
    # -- architecture phase 3b: the trace and amber, in process ------------ #
    # Every build through `derivation.in_process` on its own copy (~1.4 s; the
    # map's rows read test_site_truth's shared bracket, and nothing here traces
    # the bracket afresh but one `--choices` run): the key and what is never served (7), a second
    # run and other bytes at the path (8), amber moving nothing and claims.py
    # never importing it (12), the map's feeds, stops and amber against the
    # checker (14), an amber evaluation's writes (15), and what untraced, a
    # skipped choice and an unmoved value say (16).
    "test_derivation.TheTraceIsKeyedToItsDesign.test_the_key_moves_with_what_it_is_keyed_on",
    "test_derivation.TheTraceIsKeyedToItsDesign.test_the_reader_and_the_worker_digest_the_same_files",
    "test_derivation.TheTraceIsKeyedToItsDesign.test_the_committed_trace_is_served_and_another_designs_never",
    "test_derivation.TheTraceIsKeyedToItsDesign.test_a_file_that_does_not_read_is_never_served",
    "test_derivation.TheTraceIsWrittenOnce.test_a_second_run_writes_nothing",
    "test_derivation.TheTraceIsWrittenOnce.test_other_bytes_at_its_path_are_refused_and_untouched",
    "test_derivation.AnAmberRunWritesNothing",
    # (one CLI process that is refused before any build: a temp dir inside the
    # project, 0.2 s — invariant 15's subprocess half has this one row here)
    "test_derivation.TheTraceBuildsInACopy.test_a_temp_directory_inside_the_project_is_refused",
    "test_derivation.APartialTraceIsNeverWritten.test_choices_alone_write_no_tracked_file",
    "test_derivation.UntracedIsNeverIndependent",
    "test_derivation.AmberChangesNoStatus.test_claims_never_imports_the_trace",
    # Issue #15, D1: `unseen` moves no status (in process, ~0.01 s for six rows).
    "test_unseen.UnseenMovesNoStatus.test_its_own_cause_beside_the_same_status",
    "test_unseen.UnseenMovesNoStatus.test_ready_and_blocking_are_the_same",
    "test_unseen.UnseenMovesNoStatus.test_a_moved_gate_leads",
    "test_unseen.UnseenMovesNoStatus.test_the_all_stale_alias_is_never_unseen",
    "test_unseen.UnseenMovesNoStatus.test_a_current_gate_is_never_made_stale",
    "test_unseen.UnseenMovesNoStatus.test_a_fail_stays_failing",
    "test_derivation.AmberOnlyWhileCurrent.test_an_acceptance_edit_beside_the_committed_amber",
    "test_derivation.TheMapDrawsWhatTheTraceObserved",
    # The review of phase 3b, in process and cheap (~0.3 s): the reason amber
    # is not current read off the closest file, the order past a faint move,
    # and what the map says the trace did not measure, see or only infers.
    "test_derivation.AmberOnlyWhileCurrent.test_the_reason_comes_from_the_closest_file",
    "test_derivation.TheOrderIsObserved.test_a_move_the_rounding_hides_does_not_reverse_the_order",
    "test_derivation.TheMapSaysWhatTheTraceDidNotSee",
    "test_records.NoCommandWritesARecord.test_a_rewritten_or_unsealed_trace_file_is_named",
    # Issue #15, P1.1 (the evidence bound by content): the A, B, A sequence on
    # the figure project through the CLI (~2.5 s: three checks, `site init` and
    # one build, each a cold process), the row that saw the defect, and the
    # manifest's place under git's ignore rules (~0.6 s, one check).
    "test_figures.EvidenceIsBoundByContent.test_a_cache_hit_never_publishes_another_runs_bytes",
    "test_figures.TheEvidenceManifestIsScratch.test_check_writes_the_manifest_only_where_git_ignores",
    # Issue #15, P1.2 (nothing the build deletes is linked): one check, `site
    # init` and two builds of the figure project, the second without the
    # unbound viewgen's tool, each a cold process (~3 s idle, ~6 s measured on
    # a loaded machine). The row that saw the defect: every link of
    # state.json, map.json and page.json is on disk after each build.
    "test_figures.NothingTheBuildDeletesIsLinked.test_every_href_the_page_bakes_exists_after_the_build",
    # Issue #15, P1.3 (bound viewgens): the A, B, A sequence with the stand-in
    # renderer (three checks and `site init` as cold processes, two builds in
    # process: ~1.5 s at load 1.7), the row that sees a picture drawn from
    # another run's files; and AMissingRendererMovesNoStatus's design row, five
    # builds in process with the renderer absent, crashing, hanging past a 1 s
    # timeout and writing 2.1 MiB (~1.9 s, 1 s of it the timeout). The latter
    # replaces P1.2's unbound row here (~1.2 s), which moved to the gate as its
    # comment said: ~2.2 s added in all.
    "test_figures.AFigureIsItsEntrysEvidence.test_a_render_from_another_runs_inputs_is_never_drawn",
    "test_figures.AMissingRendererMovesNoStatus.test_statuses_equal_with_absent_crashing_hanging_oversize_renderer",
    # Issue #15, P1.4 (the page's figures): the design's three rows, each one to
    # two checks as cold processes and one or two builds in process (~1.5-2.5 s
    # each at load 2-4): a new entry with no renderer reads older beside the
    # new composition's chips; a disagreement and an amber flip planted on the
    # claim reach every figure's chip and the hero's, and the build's checker
    # (G1-G8) accepts the page; a limit doubled and the still drawn again move
    # no colour range while the chart's limit moves with the claim.
    "test_figures.AFigureSaysWhichResultItIs.test_a_new_entry_with_no_renderer_reads_older",
    "test_figures.TheClaimsStatusSitsBesideEveryFigure.test_every_figure_carries_its_claims_chips",
    "test_figures.TheScaleIsAPackConstant.test_the_scale_never_moves_with_the_limit",
    # The motorBike design's P5: every scale of aero-openfoam's `_scales.SCALES`
    # built from a summary alone, in process (~0.05 s: no build, no tool).
    "test_figures.TheScaleIsAPackConstant.test_every_aero_scale_is_a_pack_constant",
    # Eddy milestone 0: the rule table is one list — `receive.RULES`/`NOTES`, the
    # contract's table and the hostile tests — and this class holds the three
    # equal, in process, with no repository (~0.1 s). Every push through the
    # real hook, and every in-process push of the hostile classes, is the full
    # suite's: the tier is over budget and takes no e2e push.
    "test_receive.TheContractIsTheRuleTable",
]


# Tests of an INVARIANT class that this tier does not run, each group with why
# leaving it to the full suite is safe. FastTierHoldsEveryInvariant reads this
# list: a test of an invariant class must be named in FAST or here.
LEFT_FOR_THE_GATE: list[str] = [
    # The motorBike design's P6: the rest of the drag gate's classes, each one to
    # three ladders through the stand-in (~1.3-3 s each, ~9 s in all): a bigger
    # body past its margin, a ladder that gives no band, the same solves behind
    # aero.mesh_independence, and a record of another process or with a moved
    # byte. An iteration touching `_ladder`, `_Record` or `_judge_ladder` in
    # aero-openfoam runs test_aero_openfoam whole.
    "test_aero_openfoam.TheDragAreaIsJudgedAtTheTopOfItsBand.test_a_bigger_body_lands_past_its_margin",
    "test_aero_openfoam.TheDragAreaIsJudgedAtTheTopOfItsBand.test_a_ladder_that_gives_no_band_here_is_an_error",
    # Review of P6: every pack default the drag gate judges on is said (three
    # ladders through the stand-in, ~3.3 s). The same `marks` path runs in
    # test_the_baseline_passes_at_the_top_of_its_band, with none to say.
    "test_aero_openfoam.TheDragAreaIsJudgedAtTheTopOfItsBand.test_every_pack_default_it_judges_on_is_said",
    "test_aero_openfoam.ALadderRecordIsReadOnlyByItsProcess.test_behind_mesh_independence_it_judges_the_same_solves",
    "test_aero_openfoam.ALadderRecordIsReadOnlyByItsProcess.test_a_record_of_another_process_or_another_byte_is_never_read",
    # Issue #15, P1.1: the rest of the evidence binding, each a figure project
    # driven through two to nine cold CLI processes (~1-4 s each, ~12 s in all).
    # The A, B, A row in FAST runs the same plan through the same build, with
    # the manifest written, missing and mismatched; these hold the edges: a
    # `--no-record` run, a crash's file over the served entry, a clone with no
    # scratch, a copy made before the manifest, `--no-record` and the controls
    # writing none, the build's own writes, and an earlier copy that another
    # run's bytes never overwrite (~4 s; the FAST row's plan and build again,
    # with a copy already on the site). An iteration touching
    # `page.evidence_plan`, `site._evidence_plan` or the manifest in
    # `verdicts.py` runs test_figures and test_map whole.
    "test_figures.EvidenceIsBoundByContent.test_an_earlier_copy_is_never_overwritten_by_another_runs_bytes",
    "test_figures.EvidenceIsBoundByContent.test_a_no_record_run_binds_nothing",
    "test_figures.EvidenceIsBoundByContent.test_an_errored_runs_evidence_never_lands_under_the_stale_entry",
    "test_figures.EvidenceIsBoundByContent.test_a_fresh_clone_keeps_the_committed_copy",
    "test_figures.EvidenceIsBoundByContent.test_a_copy_made_before_the_manifest_is_kept",
    "test_figures.TheEvidenceManifestIsScratch.test_no_record_and_controls_write_none",
    "test_figures.TheEvidenceManifestIsScratch.test_site_build_writes_only_site_and_ignored_scratch",
    # Issue #15, P1.2: the prose figure under `assets/` (one check and two
    # builds, ~4 s) and the unbound viewgen crashing, then with its tool gone
    # (one check and three builds, ~5 s). The FAST rows above build the same
    # project without the tool, ask that every link is on disk and that the
    # row and its files stay; these hold the crash and the prose figure's
    # word. An iteration touching `site._kept_from_last_build`,
    # `page.figure_misplaced` or `prose.parse_markdown` runs test_figures and
    # test_site whole.
    "test_figures.NothingTheBuildDeletesIsLinked.test_a_prose_figure_under_assets_reads_missing",
    "test_figures.AMissingRendererMovesNoStatus.test_an_unbound_viewgen_that_crashes_keeps_its_asset",
    # Issue #15, P1.3: the unbound viewgen's row with its tool gone (one check,
    # `site init`, two builds; ~3 s), FAST in P1.2 until this step's design
    # row for its class landed; the bound picture kept through a build without
    # the tool (memo, scene edit, clone: one check, four builds, ~4 s); and the
    # rest of AFigureIsItsEntrysEvidence (a forced run drawing its own entry
    # again, inputs moved during the draw, every path naming its entry, a
    # forged folder, a swapped file, a file over 2 MiB: one to four checks and
    # two to four builds each, ~20 s in all). The FAST rows above run the
    # same bound pass with the manifest bound, missing, and a renderer that
    # fails four ways. An iteration touching `site._bound_pass`,
    # `_figure_refusal`, `_publish_figure`, `BoundContext`,
    # `page.read_sidecar` or `page.figure_for` runs test_figures whole.
    "test_figures.AMissingRendererMovesNoStatus.test_an_unbound_viewgen_that_is_unavailable_keeps_its_asset",
    "test_figures.AMissingRendererMovesNoStatus.test_a_committed_render_survives_a_build_without_the_tool",
    "test_figures.AFigureIsItsEntrysEvidence.test_a_forced_run_draws_its_own_entry_again",
    "test_figures.AFigureIsItsEntrysEvidence.test_inputs_that_move_during_the_render_are_discarded",
    "test_figures.AFigureIsItsEntrysEvidence.test_every_figure_path_names_its_entry",
    "test_figures.AFigureIsItsEntrysEvidence.test_a_render_naming_an_entry_the_cache_lacks_is_never_shown",
    "test_figures.AFigureIsItsEntrysEvidence.test_a_swapped_file_is_never_shown",
    "test_figures.AFigureIsItsEntrysEvidence.test_a_file_over_two_mib_is_never_written_or_linked",
    # Issue #15, P1.4: the rest of the page's figures, each one to three checks
    # as cold processes and one to three builds and `site status` runs in
    # process (~2-5 s each, ~40 s in all): a design edit reading stale, a
    # planted currency (a lying bake refused, a resealed page read as edited
    # by hand by `site status`, `status` and `doctor`), a check that moved
    # only the entry, two older pictures after a merge, the technical page's
    # word, planted chips, a missing or extra claim, the chart's limit and
    # margin, a pack limit, a status in a caption, a scale keyed to a limit, a
    # number no evidence holds; and the pool's figure numbers (one check, one
    # build). The FAST rows above run the same bake, the same checker in the
    # build and the same refusals' producer. An iteration touching
    # `page.figure_row`, `chart_block`, `figure_facts`, `_figure_problems`,
    # `representation` or `site._bound_pass` runs test_figures, test_site_truth
    # and test_style whole.
    "test_figures.AFigureSaysWhichResultItIs.test_a_design_edit_reads_stale",
    "test_figures.AFigureSaysWhichResultItIs.test_a_planted_currency_is_refused",
    "test_figures.AFigureSaysWhichResultItIs.test_site_status_reads_behind_after_a_check_that_moved_only_the_entry",
    "test_figures.AFigureSaysWhichResultItIs.test_two_older_renders_after_a_merge_show_none",
    "test_figures.AFigureSaysWhichResultItIs.test_the_technical_page_says_the_same_word",
    # The review of P2: an older chart draws its own run alone, never the
    # served row's point and limit (two checks and two builds, ~1.4 s; the
    # FAST row above runs the same bake of an older figure).
    "test_figures.AFigureSaysWhichResultItIs.test_an_older_chart_draws_its_own_run_alone",
    "test_figures.TheClaimsStatusSitsBesideEveryFigure.test_a_planted_chip_is_refused",
    "test_figures.TheClaimsStatusSitsBesideEveryFigure.test_a_missing_or_extra_claim_is_refused",
    "test_figures.TheClaimsStatusSitsBesideEveryFigure.test_a_chart_limit_and_margin_are_the_floors",
    "test_figures.TheClaimsStatusSitsBesideEveryFigure.test_a_pack_limit_in_a_chart_is_refused",
    "test_figures.TheClaimsStatusSitsBesideEveryFigure.test_a_caption_with_a_status_word_is_refused",
    "test_figures.TheScaleIsAPackConstant.test_a_scale_keyed_to_a_limit_is_refused",
    "test_figures.TheScaleIsAPackConstant.test_every_chart_number_is_in_the_evidence",
    "test_site_truth.ANumberNoBakedFileHoldsIsFlagged.test_figure_numbers_vouch_for_nothing",
    # The review of P1: a digest no evidence file holds, refused by `bind` (one
    # check, one build); a gate id the site's folder spells otherwise, read
    # under the raw id (one check, two builds); each of an older figure's
    # sentences, outcome and digest planted alone, and a lying outcome bake
    # (two checks, two builds, six `site status` runs and one refused build in
    # process, ~4 s); a bound solid kept off the hero and the alternates, and
    # planted there (one check, one build, three `site status` runs); a bound
    # viewgen handed no limit (one check, one build); and a kept row edited by
    # hand (one check, two builds, two `site status` runs). ~12 s in all. The
    # FAST rows above run the same bound pass, bake and checker. An iteration
    # touching `BoundContext.bind`, `page.read_sidecar`, `entry_verdict`,
    # `representation`, `alternates`, `_figure_problems`,
    # `_kept_from_last_build` or `view_digest` runs test_figures whole.
    "test_figures.AFigureIsItsEntrysEvidence.test_a_digest_no_evidence_file_holds_is_never_bound",
    "test_figures.AFigureIsItsEntrysEvidence.test_a_gate_id_the_site_spells_otherwise_shows_its_figure",
    "test_figures.AFigureSaysWhichResultItIs.test_each_planted_sentence_outcome_and_digest_is_refused",
    "test_figures.AFigureSaysWhichResultItIs.test_a_bound_solid_is_never_the_hero_as_a_plain_view",
    "test_figures.TheScaleIsAPackConstant.test_a_bound_viewgen_reads_no_limit",
    "test_figures.AMissingRendererMovesNoStatus.test_a_kept_row_is_kept_only_at_the_bytes_the_build_wrote",
    # Design P2: the same two rules over each scale of a figure's `scales`
    # (two builds after one check, ~2 s; three refused builds, ~3 s). The FAST
    # row above runs the one-scale twin of the first through the same
    # `_figure_refusal` loop and sidecar write. An iteration touching
    # `page.scales_problem`, `sidecar_schema`, `figure_row`'s scales or
    # `site._figure_refusal` runs test_figures whole.
    "test_figures.TheScaleIsAPackConstant.test_no_scale_of_several_moves_with_the_limit",
    "test_figures.TheScaleIsAPackConstant.test_a_scale_of_several_keyed_to_a_limit_is_refused",
    # The review of phase 6: the rest of the identity rows (a listed person
    # commits, an email in another case, no identity, no people, a detached
    # HEAD, a merge, a newer record, no repository, and the CLI's words for git
    # not listing, not staging and not finishing), `init`'s other rows, and the
    # round's commit and the Stop hook in the records sweeps (~4.3 s, each a
    # bracket or an `init` in a fresh process). The rule they share runs above:
    # `rounds.listed` over another name, another address and the committer, and
    # `vcs.in_repository` under git's refusal. An iteration touching
    # `_round_commit`, `_round_hook`, `cmd_init`, `vcs.py` or `rounds.py` runs
    # test_round, test_vcs, test_record_commands and test_records whole.
    "test_round.RoundsCommitOnlyAsAListedPerson.test_a_listed_person_commits_as_themselves",
    "test_round.RoundsCommitOnlyAsAListedPerson.test_an_email_in_another_case_is_the_same_person",
    "test_round.RoundsCommitOnlyAsAListedPerson.test_git_does_not_list_the_changes",
    "test_round.RoundsCommitOnlyAsAListedPerson.test_git_does_not_stage",
    "test_round.RoundsCommitOnlyAsAListedPerson.test_a_commit_that_does_not_finish",
    "test_round.RoundsCommitOnlyAsAListedPerson.test_no_identity",
    "test_round.RoundsCommitOnlyAsAListedPerson.test_no_people",
    "test_round.RoundsCommitOnlyAsAListedPerson.test_detached_head",
    "test_round.RoundsCommitOnlyAsAListedPerson.test_a_merge_in_progress",
    "test_round.RoundsCommitOnlyAsAListedPerson.test_a_newer_round_record",
    "test_round.RoundsCommitOnlyAsAListedPerson.test_no_repository",
    "test_record_commands.InitStartsARepository.test_outside_a_repository_it_starts_one",
    "test_record_commands.InitStartsARepository.test_no_git_starts_none",
    "test_record_commands.InitStartsARepository.test_no_identity_lists_no_one_and_says_so",
    "test_record_commands.InitStartsARepository.test_json_carries_git_and_people",
    "test_record_commands.InitStartsARepository.test_a_refused_init_starts_no_repository",
    "test_record_commands.InitStartsARepository.test_an_identity_the_reader_refuses_is_not_listed",
    "test_records.IndexNeverDisagreesWithRecords.test_the_stop_hook_leaves_the_index_agreeing",
    "test_records.IndexNeverDisagreesWithRecords.test_packs_skills_and_the_rounds_commit_leave_the_index_agreeing",
    "test_records.NoCommandWritesARecord.test_packs_skills_writes_no_record",
    "test_records.NoCommandWritesARecord.test_the_rounds_commit_writes_what_round_writes",
    # Architecture phase 3b's real workers and its costlier in-process rows
    # (~9 s): `model trace` through real subprocesses (a build writing beside
    # its model, one writing into the project, the model run from its copy, a
    # temp dir inside — "spawns real workers; the in-process row of the same
    # invariant is in FAST"), the watched `model trace` run of the records sweep,
    # one worker against three byte for byte, the timeout's resume, the raise
    # that differs alone, round's refusal to start again, and the bracket traced
    # with C3 tightened (a flip, every surface with and without the amber file,
    # a confirmed assumption, and an acceptance, a gate and a choice edit read
    # back). The
    # rules they hold run in FAST above: the key, the writer's refusal, the
    # partial run, amber's currency, the map's checker.
    "test_derivation.TheTraceBuildsInACopy.test_a_build_that_writes_beside_its_model_writes_in_its_copy",
    "test_derivation.TheTraceBuildsInACopy.test_a_build_that_writes_into_the_project_is_refused_and_named",
    "test_derivation.TheTraceBuildsInACopy.test_the_model_runs_from_its_copy",
    "test_derivation.TheTraceIsWrittenOnce.test_one_worker_and_three_give_the_committed_bytes",
    "test_derivation.APartialTraceIsNeverWritten.test_a_timeout_leaves_no_file_and_a_resume_completes",
    "test_derivation.APartialTraceIsNeverWritten.test_a_raise_that_differs_alone_is_incomplete",
    "test_derivation.APartialTraceIsNeverWritten.test_round_does_not_start_again_after_an_incomplete_background_run",
    "test_derivation.AmberChangesNoStatus.test_nothing_moves_without_it",
    "test_derivation.AmberChangesNoStatus.test_it_flips",
    "test_derivation.AmberChangesNoStatus.test_a_confirmed_assumption_carries_no_amber",
    "test_derivation.AmberOnlyWhileCurrent.test_an_acceptance_edit",
    "test_derivation.AmberOnlyWhileCurrent.test_a_gate_edit",
    "test_derivation.AmberOnlyWhileCurrent.test_a_choice_edit",
    # The review of phase 3b's rows that trace the bracket in process (~6 s):
    # data build() reads, traced twice; a refused build's row never reused
    # (environmental, confirmed, a nudge's write, a dead worker); amber re-run
    # after an acceptance edit. The key's other rows and the closest file run
    # in FAST above.
    "test_derivation.TheTraceIsKeyedToItsDesign.test_data_build_reads_is_in_the_key",
    "test_derivation.APartialTraceIsNeverWritten.test_an_environmental_raise_is_never_kept",
    "test_derivation.APartialTraceIsNeverWritten.test_a_raise_confirmed_alone_is_kept",
    "test_derivation.APartialTraceIsNeverWritten.test_a_nudge_that_writes_into_the_project_leaves_no_row",
    "test_derivation.APartialTraceIsNeverWritten.test_a_worker_that_dies_leaves_no_file",
    "test_derivation.AmberOnlyWhileCurrent.test_a_retrace_after_an_acceptance_edit_runs_again",
    "test_records.NoCommandWritesARecord.test_model_trace_writes_only_new_whole_trace_files",
    # Architecture phase 5's subprocess halves: a hand-edited map read back
    # through `site status`, `status` and `doctor` (~2.8 s), the map against the
    # round record after `nopekit round` (~0.6 s), and `site stamp` run as a
    # person runs it (~2 s). Their rules run in process above: the checker over
    # every planted lie, the stamp states, the stamp's one-line write. An
    # iteration touching the site template, `site.py`, `page.py`, `prose.py` or
    # `graph.py` runs test_map, test_site_truth and test_style whole.
    "test_site_truth.TheSiteNeverSaysMoreThanTheResolution.test_a_hand_edit_reads_edited_by_hand",
    "test_site_truth.TheSiteNeverSaysMoreThanTheResolution.test_the_map_and_the_round_record_agree",
    "test_site_truth.StampedProseIsFlaggedWhenBehind.test_site_stamp_writes_only_the_as_of_line",
    # And the number scan through a whole build (~0.4 s, a bracket copy of its
    # own): the scan, its precision and its planted matcher run in process above.
    "test_site_truth.ANumberNoBakedFileHoldsIsFlagged.test_through_a_build",
    # The review of phase 5's builds and commands (~2.5 s): two builds over an
    # evidence copy the first one removes, and `site stamp` over text that names
    # a claim of another board. Their rules run in process above: the pool's
    # keys and units, and the stamp over a named claim.
    "test_site_truth.ANumberNoBakedFileHoldsIsFlagged.test_an_asset_the_build_removes_vouches_for_nothing",
    "test_site_truth.StampedProseIsFlaggedWhenBehind.test_site_stamp_stamps_a_named_claim_too",
    # Architecture phase 3a's subprocess halves: `round` with a site (what `site
    # build` writes is held by the records sweep's site rows), the index after a
    # round (every writer's index row is the full suite's), the louder project's
    # two rounds and the forged cache's round paragraph (each a world of commands
    # run once; `round_paragraph`'s words are held in process by test_round), and
    # `check` and `export --dry-run` over a forged round record (the in-process
    # forgery row above holds the same readers). An iteration touching
    # `cmd_round`, `rounds.py` or `graph.py` runs test_round, test_graph and
    # test_records whole.
    "test_records.NoCommandWritesARecord.test_round_with_a_site_writes_no_record",
    "test_records.IndexNeverDisagreesWithRecords.test_round_leaves_the_index_agreeing",
    "test_louder.ErrorIsLouder.test_round",
    "test_export.EveryCacheReaderSaysAsLastEvaluated.test_the_round_paragraph",
    "test_round.NothingReadsTheRoundRecordForTruth.test_check_and_export_ignore_a_forged_record",
    # The review of phase 3a's `doctor` row (~0.5 s, three in-process doctors):
    # it prints `_round_state_line`, the sentence `status`'s line in the rows
    # above holds over the same `_round_state`; an iteration touching
    # `_doctor_round_row` runs test_round whole.
    "test_round.ABehindRoundRecordSaysSo.test_doctor_says_what_status_says",
    # Architecture phase 2's end-to-end halves: `prefer` in a real pty and under
    # the real environment's markers (pty ~1 s each; the in-process rows above
    # hold the rule over every marker), a committed tamper walking git for the
    # restore (the advice's walk is results/' and exports', held in their own
    # classes), and `propose`/`ingest --proposer`/typed `decide` through
    # subprocesses (each refusal's rule is held in process above). An iteration
    # touching `cmd_prefer`, `cmd_propose`, the preference file's reader or
    # writer, or `artifacts.ingest` runs test_preferences, test_proposers and
    # test_records whole.
    "test_preferences.APreferenceIsAPersonsAct.test_a_pty_records_it_end_to_end",
    "test_preferences.APreferenceIsAPersonsAct.test_the_markers_end_to_end",
    "test_preferences.ThePreferenceFileIsSealedAndChained.test_a_committed_tamper_names_the_commit_to_restore",
    # The planted twin of the one-record check (~0.35 s): a `prefer` that also
    # rewrote its decision. The real `prefer`'s before-and-after runs every
    # iteration in test_the_person_records_it_in_their_own_shell above.
    "test_preferences.APreferenceIsAPersonsAct.test_a_planted_prefer_that_writes_more_is_caught",
    # The AST scan for a second writer of `preferences/` (0.3 s: it parses
    # cli.py and verdicts.py) is a static property of the source; the export
    # twin above holds the same rule over the same writer every iteration.
    "test_preferences.ThePreferenceFileIsSealedAndChained.test_the_only_writer_is_append_sealed",
    "test_proposers.ProposeWritesOnlyItsInputs.test_two_files_two_records",
    "test_proposers.ProposeWritesOnlyItsInputs.test_each_failure_ingests_nothing",
    "test_proposers.ProposeWritesOnlyItsInputs.test_an_unknown_proposer_is_refused",
    "test_proposers.ProposeWritesOnlyItsInputs.test_ingest_proposer_records_it_on_a_new_record_only",
    # The review of phase 2's propose rows, through subprocesses like the rest
    # of the class: the ignored scratch a proposer may write, and nopekit's own
    # flags after the id with `--json` on a run that wrote no file.
    "test_proposers.ProposeWritesOnlyItsInputs.test_ignored_scratch_is_not_outside",
    "test_proposers.ProposeWritesOnlyItsInputs.test_its_own_flags_never_reach_the_script",
    "test_records.NoCommandWritesARecord.test_the_phase_2_writers_write_their_one_record",
    # Architecture phase 1's end-to-end halves (~2 s): a pass typed in a pty on
    # an older-spelled claim, read Checked after the file is respelled (11);
    # the bracket's requirements through `status --json`, `report --json` and
    # `why` (12); doctor's FAIL on a dangling pointer (8). Each rule is held in
    # process above; an iteration touching `store`'s claim reader or writer,
    # `claims.requirement_statuses`, a requirement renderer or `cmd_doctor` runs
    # test_mechanism, test_requirements and test_records whole.
    "test_mechanism.TheSealSurvivesTheNewSpelling.test_a_recorded_pass_stays_bound_across_the_rewrite",
    "test_requirements.EveryReaderAgreesOnARequirement.test_the_bracket_end_to_end",
    "test_records.RequirementsAreRecords.test_doctor_fails_on_a_dangling_pointer",
    # P2.5a's pay-back (~27 s, measured 2026-10-04 with --durations): the CLI
    # rows whose rule an in-process row above already holds. Invariant 2's
    # planted project — every command run once over an errored evaluator (~6 s);
    # its in-process renderers stay in FAST. S-05/S-07 (invariant 9) and
    # S-20/S-21/S-32 (invariant 7) through the commands (~15.5 s); admission's
    # and staleness's in-process rows stay. P2.1's owner on its two planted
    # projects (~5 s; not every row an invariant class's). An iteration that
    # touches a renderer runs test_louder whole; one that touches `check`'s or
    # `status`'s wiring to the resolver or to admission runs test_admission and
    # test_staleness whole (the rule above, for what a gate's read set records).
    "test_louder.ErrorIsLouder.test_check_json",
    "test_louder.ErrorIsLouder.test_check_junit",
    "test_louder.ErrorIsLouder.test_check_output",
    "test_louder.ErrorIsLouder.test_claim_list",
    "test_louder.ErrorIsLouder.test_claim_physical",
    "test_louder.ErrorIsLouder.test_counts_say_skipped_with_errored_apart",
    "test_louder.ErrorIsLouder.test_doctor",
    "test_louder.ErrorIsLouder.test_gate_show",
    "test_louder.ErrorIsLouder.test_reason_leads_with_errored",
    "test_louder.ErrorIsLouder.test_report",
    "test_louder.ErrorIsLouder.test_site_state",
    "test_louder.ErrorIsLouder.test_status",
    "test_louder.ErrorIsLouder.test_the_fixture_is_what_it_says",
    "test_louder.ErrorIsLouder.test_why_and_claim_show",
    "test_admission.AdmissionIsDemonstrated.test_cli_a_no_op_fixture_is_not_admitted_though_the_cache_would_hit",
    "test_admission.AdmissionIsDemonstrated.test_cli_an_always_true_gate_is_never_pass",
    "test_admission.AdmissionIsDemonstrated.test_cli_a_logger_with_no_honest_past_is_refused_by_check",
    "test_admission.AdmissionIsDemonstrated.test_cli_identity_fixtures_for_deflection_are_not_admitted",
    "test_staleness.StaleIsNotCurrent.test_s20_cli_a_filtered_first_check_then_an_input_change",
    "test_staleness.StaleIsNotCurrent.test_s21_cli_a_model_that_does_not_load_proves_nothing",
    "test_staleness.StaleIsNotCurrent.test_s32_cli_a_dry_check_leaves_a_moved_gate_stale",
    "test_owner.AnOwnerWrittenByHandNeverCounts.test_every_channel_reads_gap_with_the_unattributed_reason",
    "test_owner.AnOwnerWrittenByHandNeverCounts.test_no_command_writes_the_owner",
    "test_owner.AnOwnerWrittenByHandNeverCounts.test_the_channel_checks_refuse_what_they_forbid",
    "test_status_table.UnqualifiedBesideAPassIsNeverChecked.test_a_refused_evaluator_beside_a_pass_end_to_end",
    "test_status_table.UnqualifiedBesideAPassIsNeverChecked.test_the_end_to_end_checks_refuse_what_they_forbid",
    # Invariant 11's end-to-end rows (P2.5a): each runs the bracket through the
    # commands — a pass typed in a pty and read on every channel (`channels`:
    # check, JUnit, status, report, claim show and list, the site), an owner
    # recorded then edited, every channel and every edit over a fail (4 x 7
    # copies), the contradiction recorded and shown, an authority's judgment,
    # the seal's refusal through `status` and `doctor` and in git — 3-30 s each.
    # The rule each holds is held in process above; an iteration touching
    # `cli.cmd_claim_physical`, `store`'s results reader or writer,
    # `verdicts.judge_results`/`article_of`, `claims.compose`'s P2.5a rows or a
    # renderer's physical words runs test_signing, test_physical and test_fig4
    # whole.
    "test_signing.HumanChannelOnly.test_a_pass_typed_in_a_persons_shell_counts",
    "test_signing.HumanChannelOnly.test_an_agents_pass_is_recorded_and_settles_nothing",
    "test_signing.HumanChannelOnly.test_assume_off_the_terminal_is_refused",
    "test_signing.WhoAndWhenAreNeverTyped.test_every_value_is_refused_before_the_project_is_read",
    "test_signing.WhoAndWhenAreNeverTyped.test_no_identity_records_nothing",
    "test_signing.WhoAndWhenAreNeverTyped.test_who_is_the_git_identity",
    "test_signing.TheResultsFileIsSealedAndChained.test_a_fix_that_would_discard_an_uncommitted_fail_names_it",
    "test_signing.TheResultsFileIsSealedAndChained.test_doctor_names_a_refused_file_and_answers",
    "test_signing.AnOwnerOnlyThroughTheChannel.test_a_fallback_owner_reads_assumed_outside_the_context",
    "test_signing.AnOwnerOnlyThroughTheChannel.test_every_edit_after_unattributes_it",
    "test_signing.AnOwnerOnlyThroughTheChannel.test_someone_else_cannot_record_the_owner",
    "test_signing.AnOwnerOnlyThroughTheChannel.test_the_owner_records_it_and_every_channel_reads_assumed",
    "test_physical.SignedMeansSomething.test_end_to_end_a_typed_pass_reads_checked_everywhere",
    "test_physical.SignedMeansSomething.test_h_a_covering_fail_outranks_a_counting_pass",
    "test_physical.APhysicalFailNeverLosesItsPowerToFail.test_a_nudge_and_a_pass_on_the_new_article_leave_it_failing",
    "test_physical.APhysicalFailNeverLosesItsPowerToFail.test_every_channel_and_every_edit",
    "test_physical.APhysicalFailNeverLosesItsPowerToFail.test_planted_laundering_is_caught",
    "test_physical.AContradictionGoesOnTheEvaluatorsTrackRecord.test_a_contradiction_is_recorded_and_shown",
    "test_physical.AContradictionGoesOnTheEvaluatorsTrackRecord.test_an_agents_fail_is_a_contradiction_shown_with_its_channel",
    "test_physical.AContradictionGoesOnTheEvaluatorsTrackRecord.test_controls_record_no_contradiction",
    "test_physical.AnExpertJudgmentStaysWithItsAuthority.test_a_physical_judgment_needs_its_evidence",
    "test_physical.AnExpertJudgmentStaysWithItsAuthority.test_an_agents_judgment_does_not_count_and_a_fail_does",
    "test_physical.AnExpertJudgmentStaysWithItsAuthority.test_only_the_authority_settles_it",
    "test_physical.AnExpertJudgmentStaysWithItsAuthority.test_planted_compositions_are_caught",
    "test_physical.AnExpertJudgmentStaysWithItsAuthority.test_the_rows",
    "test_physical.TheCheckedSectionHoldsOnlyBoundResults.test_a_section_that_lists_every_pass_is_caught",
    "test_physical.TheCheckedSectionHoldsOnlyBoundResults.test_checked_rows_and_exactly_one_section",
    "test_physical.AMovedArticleReadsStale.test_a_derived_value_moved_by_the_model_reads_stale",
    "test_physical.AMovedArticleReadsStale.test_a_file_the_design_names_is_part_of_the_article",
    "test_physical.AMovedArticleReadsStale.test_a_nudge_reads_stale_and_names_the_article",
    "test_physical.AMovedArticleReadsStale.test_moved_evidence_never_hides_a_moved_article",
    "test_physical.AMovedArticleReadsStale.test_planted_judges_are_caught",
    "test_physical.AMovedArticleReadsStale.test_the_all_stale_override_stales_a_counted_pass",
    "test_physical.AMovedArticleReadsStale.test_the_article_leads_an_invalidated_evaluator",
    "test_physical.AMovedArticleReadsStale.test_the_claim_moved_and_the_model_not_loading",
    "test_physical.AMovedArticleReadsStale.test_what_the_article_was_not_built_from_moves_nothing",
    # The review of P2.5a's end-to-end rows (3-30 s each, the bracket through
    # the commands): each rule is held in process in FAST above, with its
    # planted violator — the named marker on the recorded line and the refusal,
    # the act and its flag refused by the command, a committed hand edit
    # restored by the advice, an owner's and an authority's name with spaces, a
    # fail and a contradiction outliving their claim file through check, JUnit
    # and doctor, a fail's changed photo in doctor's words, a pass refused
    # until every evaluator has run, a rewritten judgment stopping check, a
    # judgment worded as one on every channel.
    "test_signing.HumanChannelOnly.test_the_marker_is_named_end_to_end",
    "test_signing.WhoAndWhenAreNeverTyped.test_the_act_and_its_flag_end_to_end",
    "test_signing.TheResultsFileIsSealedAndChained.test_a_committed_hand_edit_is_restored_by_the_advice",
    "test_signing.AnOwnerOnlyThroughTheChannel.test_a_name_with_spaces_around_it_end_to_end",
    "test_physical.APhysicalFailNeverLosesItsPowerToFail.test_a_fail_outlives_its_claim_file_end_to_end",
    "test_physical.APhysicalFailNeverLosesItsPowerToFail.test_a_fails_changed_evidence_is_worded_as_a_fail",
    "test_physical.AMovedArticleReadsStale.test_a_pass_waits_until_every_evaluator_has_run",
    "test_physical.AContradictionGoesOnTheEvaluatorsTrackRecord.test_a_contradiction_outlives_its_claim_file_end_to_end",
    "test_physical.AnExpertJudgmentStaysWithItsAuthority.test_a_rewritten_judgment_stops_check",
    "test_physical.AnExpertJudgmentStaysWithItsAuthority.test_a_judgment_is_worded_as_one",
    # Planned 12 (P2.5a): one copy holding every standing, read on every channel
    # (~25 s to build); its AST scans run above.
    "test_physical.RenderersAgreeOnPhysicalClaims.test_every_channel_reads_the_composition",
    "test_physical.RenderersAgreeOnPhysicalClaims.test_the_page_paints_only_a_counted_pass_ok",
    "test_physical.RenderersAgreeOnPhysicalClaims.test_a_verified_claim_its_standing_does_not_back_is_loud",
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
    # the legacy-project `check`, `round` (review of phase 3a, ~4 s) and shim
    # (~12 s). The property, its planted
    # violator and the migrated-project sweep are in FAST; these add the
    # one-time migration's carve-out, which only a change to a command's write
    # path or to the migration can break: such an iteration runs test_records
    # whole (module docstring).
    "test_records.NoCommandWritesARecord.test_no_command_writes_a_record_on_a_legacy_project",
    "test_records.NoCommandWritesARecord.test_check_on_a_legacy_project_writes_only_the_migration",
    "test_records.NoCommandWritesARecord.test_round_on_a_legacy_project_writes_only_the_migration",
    "test_records.NoCommandWritesARecord.test_a_shim_on_a_legacy_project_writes_the_migration_and_its_record",
    # P2.5b's pay-back (~2 s): the index arriving with that one-time migration,
    # `check` and each shim on a legacy copy — the same carve-out, the same
    # escalation.
    "test_records.IndexNeverDisagreesWithRecords.test_a_migration_leaves_the_index_agreeing",
    # The review of phase 5's pay-back (~1.8 s): architecture phase 5's two rows of
    # both sweeps, `site stamp --all` and `site badge`, in tests of their own. What
    # each writes is held row by row beside its rule: the stamp's one `as_of` line
    # (test_site_truth's `site stamp` row, gate) and the badge block alone, with a
    # quoted marker and a CRLF file (test_groups.TheBadgeIsWritten); an iteration
    # touching `cmd_site_stamp`, `cmd_site_badge` or `store.ensure_readme_badge`
    # runs test_records, test_groups and test_site_truth whole.
    "test_records.NoCommandWritesARecord.test_the_site_text_commands_write_no_record",
    "test_records.IndexNeverDisagreesWithRecords.test_the_site_text_commands_leave_the_index_agreeing",
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
    # Invariant 12's end-to-end rows (P2.5b): each runs `export` (or `claim
    # physical --article`) through the commands on a bracket or Fig. 4 copy —
    # the bracket at 8 re-executed, a prerequisite covering no required claim
    # re-run, a forgery's re-run filed and read back through `status` (its
    # disagreement is in FAST, in process), a forgery after its first refusal
    # and under foreign instruments,
    # dry run at 7, 8 and forged and its text against the written export's,
    # the preconditions dry run reports, a generator writing under dry run,
    # the go-ahead's channels in a pty (agent, pipe, no reason, a mistyped
    # name, the person) and a ready milestone's, export writing its record and
    # nothing else and a refused one filing only its re-run, the two Fig. 4
    # articles each moved by its own change, a short, unknown or ambiguous
    # `--article`, the planted capture from the current resolution. 0.3-5.7 s
    # each, ~25 s together. The rule each holds runs in process above with
    # its planted violator; an iteration touching `cli.cmd_export`,
    # `milestones.py`, `verdicts.export_article`/`_supersedes`/`judge_results`,
    # `claims.unresolved` or `store.append_sealed` runs test_export,
    # test_physical, test_fig4 and test_records whole.
    "test_export.TheBoundaryReExecutes.test_the_bracket_at_eight",
    "test_export.ACacheThatLiesIsCaughtAtTheBoundary.test_a_forged_pass_is_refused_and_the_re_run_filed",
    "test_export.TheBoundaryReExecutes.test_a_prerequisite_covering_no_required_claim_is_re_run",
    "test_export.ACacheThatLiesIsCaughtAtTheBoundary.test_no_go_ahead_covers_it_after_the_first_refusal",
    "test_export.ACacheThatLiesIsCaughtAtTheBoundary.test_a_forgery_under_foreign_instruments_is_refused",
    "test_export.DryRunIsTheSamePath.test_at_seven_eight_and_forged",
    "test_export.DryRunIsTheSamePath.test_the_text_differs_only_in_the_outcome_line",
    "test_export.DryRunIsTheSamePath.test_dry_run_says_what_the_written_export_would_refuse",
    "test_export.DryRunIsTheSamePath.test_a_generator_that_writes_under_dry_run_is_refused_and_named",
    "test_export.GoingAheadIsAPersonsDecision.test_the_channels",
    "test_export.GoingAheadIsAPersonsDecision.test_ready_needs_no_decision",
    "test_records.MilestonesAreRecords.test_export_writes_its_record_and_nothing_else",
    "test_records.MilestonesAreRecords.test_a_refused_export_files_its_re_run_and_no_record",
    "test_fig4.TwoArticlesExactly.test_both_count_at_seventy",
    "test_fig4.TwoArticlesExactly.test_the_cell_moves_the_board_alone",
    "test_fig4.TwoArticlesExactly.test_the_cavity_moves_the_enclosure_alone",
    "test_fig4.TwoArticlesExactly.test_a_change_no_generator_read_moves_neither",
    "test_fig4.TwoArticlesExactly.test_an_unregistered_read_never_under_predicts",
    "test_fig4.TwoArticlesExactly.test_each_package_carries_only_what_its_article_records",
    "test_physical.AResultBindsToAnExportedArticle.test_a_short_unknown_or_ambiguous_article_is_refused",
    "test_physical.AResultBindsToAnExportedArticle.test_a_planted_capture_from_the_current_resolution_is_caught",
    # The review of P2.5b's end-to-end rows (~20 s together): each runs `export`
    # or `claim physical --article` through the commands on a bracket copy — a
    # fail on an article two milestones share, a model that does not load at
    # the boundary, the disagreement's way out followed, a generator handling
    # its own directory written and dry-run alike, and every cache reader
    # (`report`, `report --milestone` and its JSON, `status`) over a forged
    # cache. The rule each holds runs in process in FAST (the records'
    # union, the seeded as-last-evaluated row); an iteration
    # touching `cli.cmd_export`, `cmd_report`, `cmd_status` or `milestones.py`
    # runs test_export and test_physical whole (above).
    "test_physical.AResultBindsToAnExportedArticle.test_a_fail_on_an_article_two_milestones_share_is_a_contradiction",
    "test_export.TheBoundaryReExecutes.test_a_model_that_does_not_load_is_said_and_never_covered",
    "test_export.ACacheThatLiesIsCaughtAtTheBoundary.test_the_refusal_names_the_way_out_that_works",
    "test_export.DryRunIsTheSamePath.test_a_generator_that_handles_its_own_directory_builds_one_article",
    "test_export.EveryCacheReaderSaysAsLastEvaluated.test_the_milestone_report",
    "test_export.EveryCacheReaderSaysAsLastEvaluated.test_the_milestone_reports_json_is_the_milestones",
    "test_export.EveryCacheReaderSaysAsLastEvaluated.test_status_lists_each_milestone_as_last_evaluated",
    "test_export.EveryCacheReaderSaysAsLastEvaluated.test_the_project_report",
    "test_export.EveryCacheReaderSaysAsLastEvaluated.test_the_boundary_says_it_plainly",
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
