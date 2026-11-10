# Working on nopekit

nopekit takes a design from a sketch to a validated physical thing. Its chain is

```
requirements  ->  claims  ->  gates  ->  packs  ->  readiness report
```

Read [`METHOD.md`](METHOD.md) first — ten rules, and everything in `src/` exists to
make one of them mechanical.

**Using** nopekit (rather than working on it)? The skill in `skills/nopekit/` is
the entry point, and `nopekit doctor` is the first command to run when confused.

## Run it

```sh
PYTHONPATH=src python3 -m nopekit --help
PYTHONPATH=src python3 -m unittest discover -s tests   # every test, must stay green
PYTHONPATH=src python3 -m tests.fast                   # fast subset, ≤ 90 s on an idle machine: run it each iteration; the full suite runs before each checkpoint and commit
cd examples/bracket && PYTHONPATH=../../src python3 -m nopekit check
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

The bracket also commits its round record, `.nopekit/round.json`. A change that
moves what it holds — the cache regeneration above, the composition (`claims.py`),
`graph.py`, `rounds.py`, `report.requirement_statuses` or `_disagreement`, or one of
the bracket's records — ends with `PYTHONPATH=../../src python3 -m nopekit round` in
`examples/bracket`, in the same change; `tests/test_bracket_cache.py`
(`TheCommittedRoundRecordIsCurrent`) goes red until it does and names the command.
And it commits its trace, `.nopekit/trace/` (architecture phase 3b): a change to the
bracket's model, to how the trace observes (`derivation.HARNESS`), to a gate that
covers C2 or C3, or to their acceptance condition runs `nopekit model trace` and then
`nopekit round`, in that order, in the foreground (`round` alone only starts the trace
in the background; a gate or acceptance edit leaves the trace current and its amber
file not) (`TheCommittedTraceIsCurrent`).

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
   proof. A proposer is never registered: a proposal settles nothing, so it needs no
   control, and a pack declares it apart (`provides_proposers`).
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
   `tests/test_physical.py`). A mechanism's evaluator list is a cross-check, never
   coverage: a name there adds no evaluator to a claim and moves no status (SPEC
   decision 13; S-37).

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
   that read that claim's acceptance condition, and a statement edit none; a mechanism
   or requirement-pointer edit re-keys only the gates that read that claim or the claims
   whole, and a requirement edit only those that read the requirements. A physical
   pass is bound to its article — the design it was recorded on: when that design
   moves it reads Stale, naming the article to rebuild — a check run cannot restore
   it (P2.5a). An article `export` built is what its generator read, and every value
   its package hands the builder: it moves only when something its generator read
   moves, and the rebuild prediction names exactly those articles (P2.5b) — a file it
   linked into its package is a read, and one there that no write the trace saw put
   makes the article the whole design (review of P2.5b). The round record is never
   shown as current once the records, the verdict cache or the design it was computed
   from moved, or once what it holds is not what `round` would write now (an
   evaluator's code, its controls or a pack moved the resolution): `status` and
   `doctor` say it is behind, naming what moved (architecture phase 3a). A trace is
   keyed by the digests of the instructions, of every choice's value and of the data
   files its base build read, with its method's version, and a derivation edge is
   drawn only from the trace whose key is the current design's — its data files
   holding the same bytes on disk — never from one for another (phase 3b; in its
   review, a model whose `build()` read `model/e.json` kept its old trace and amber
   after the file changed).
   (Observed before this: one hash of the whole model decided every gate, and a model
   that failed to import compared equal, so three claims read Checked for a design that
   could not be built.)

8. **The index never disagrees with the records, and no command writes a record
   it was not asked to write.** The records are files — `claims/`, `params/`,
   `decisions/`, `needs/`, `inputs/`, `results/`, `views/`, `milestones/`,
   `exports/`, `requirements/`, `preferences/`, `groups/` and `.nopekit/project.json` — and `.nopekit/ledger.json` is an index generated
   from them, rebuilt after every command (`doctor`, `init` and `--no-record` runs
   write none of it), so a hand edit made since the last command reaches it;
   nothing reads it for truth. `check` writes no record: every path it writes is
   ignored by git at the moment it is written, or is a new verdict entry. Each kept
   writer (`ingest`, `extract`, `decide`, `packs add`, `claim physical`) writes the
   one record it was asked for; `prefer` writes one sealed entry in
   `preferences/<decision>.json`; `propose` writes, as `ingest` does, the input
   record and its bytes for each file its proposer wrote, and every other path it
   writes (`.nopekit/out/propose/`) is ignored when written — a proposer that wrote
   anywhere else in the project is refused, each path named, and nothing is
   ingested; `export` (P2.5b) writes its export record
   (`exports/<milestone>.json`), the new verdict and control entries its re-run
   files, as `check --force` files them, and the marked ignore blocks — every other
   path it writes (`out/<milestone>/`, its package) is ignored when written;
   `report --write` writes `REPORT.md`, an output git ignores; `round` writes
   what `check` writes, what `site build` writes when `site/index.html` exists, and
   the round record; `round --commit` (the Stop hook's `round --hook`) writes no
   record: it writes what `round` writes, then commits every changed file inside the
   project's directory that git does not ignore, and nothing outside it, naming
   instead of staging a directory (an untracked nested repository among them, never),
   a file over 50 MiB and a file whose name looks like a secret
   (`rounds.ROUND_SECRET_NAMES`), only when `people` lists both its author and its
   committer, and never pushes (designed against in phase 6: a hook that ran
   `git add -A` would commit a person's unrelated work as "round N"; in its review, a
   committer from `GIT_COMMITTER_*` that `people` never named reached a round's commit,
   and an untracked clone under `generators/` was committed as a gitlink; in the
   acceptance run after phase 3b, the closed list of nopekit's own paths left the
   agent's `bom/bom.json`, which its sourcing evaluators read, out of every round);
   `init` starts a git repository only where git says positively that the project
   sits in none (in review, a "dubious ownership" refusal read as "none" and nested
   one inside an existing repository); `model trace` (phase 3b) writes, under
   `.nopekit/trace/`, a trace file per design it observed and an amber file per
   evaluation — each a new
   path, written only whole and never rewritten — and its scratch under the ignored
   `.nopekit/cache/trace/`; `round` starts it in the background and writes no trace
   file itself; `site stamp` writes only the `as_of` line of the `site/content/`
   files it names, which are not records; and `site badge` and `init` write only the
   README block. `.nopekit/round.json`, the round record, is a tracked output and not a
   record: only `round` writes it (and, from phase 4, `merge`), at most once a run,
   only when what it holds moved, and as the round record of the state after the
   run; `check` and every other command never write it, and nothing reads it for
   truth — a reader that finds its self-digest broken says it was edited by hand,
   whatever schema it claims, and one that finds it moved past says it is behind
   (invariant 7). The one carve-out is the one-time legacy migration,
   which only `check`, `round` and those writers perform; every other command reads a legacy
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
   decides which it is, never a `PACK_DIR` it sets itself or a basis it declares. And it counts only on inputs inside
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
    recorded them, as themselves (the seal keeps its P2.5a form — `terminal` is
    `mechanism.by`, `note` is `mechanism.how` — so a claim file moved to the mechanism
    form stays bound): the claim is Gap until then, and an expert
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
    predicate the readiness sentence, `export`, the JSON summaries, the page and the
    round record read —
    nor a requirement more resolved than the worst claim that serves it
    (`claims.requirement_statuses`, the one producer), and *ready* never reads a
    requirement; nor evaluates a return condition on a claim the composition does not
    read Checked — it reads *cannot be evaluated: <id> is <status>* — and `check`
    prints each condition that holds without moving its exit code.
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
    listed no milestone at all.) Amber — a Checked claim that a ±10% move of an
    unconfirmed assumption's choice flips — is said beside Checked and moves no status,
    exit code, readiness, JUnit outcome or export (phase 3b). A claim Stale only
    because its evaluators read inputs nopekit cannot see (a solver's subprocess) has
    its own cause, `unseen` — never "the design changed" — which moves no status or
    readiness, and `check` says on the line after its *ready* that this ready is its
    own (issue #15, D1: `claims.unseen_gates`).

One more came with architecture phase 2, when a decision's losers gained types and a
person's preference a channel — a new place to pass the agent's choice off as a
person's. Enforced in `tests/test_preferences.py`:

13. **A preference counts only through the person's channel.** A `preferred_against`
    rejection names its person in `by` — a nominee, like a claim's owner — and counts
    only when that person records it with `nopekit prefer <decision>`, typed in their
    own shell, confirming the decision's id: refused from an agent session or a pipe,
    who read from the git identity, which must be the person `by` names, and when
    from the clock (`--who` and `--when` are refused). Each preference is one sealed,
    chained entry in `preferences/<decision>.json`, appended by the writer that seals
    `results/` and `exports/`; a broken seal refuses the file, naming what a restore
    discards. It counts only while the rejection still names that person and says
    what it said when they recorded it — its value, its why, its return condition and
    the decision's title; until then, and again after such an edit, every reader says
    *preferred against, not recorded by <name>*. A preference moves no status and no
    exit code. (What it closes before it could slip through: `decide --rejected
    VALUE|WHY` recorded no who, so a preference the agent typed into a decision would
    have read as the person's — invariant 11's owner laundering, through another
    file. What the seal does not stop is `results/`'s limit, in
    `docs/SPINE_CONTRACT.md`.)

One more came with architecture phase 5, when the site gained a map and the agent's
prose: where the site persuades, with the marketing page beside it from SPEC 40, and
pictures of results with issue #15. Enforced in `tests/test_site_truth.py` and
`tests/test_figures.py`:

14. **The site never says more than the resolution, and stamped prose is flagged when
    behind.** Every status, word, count, order, margin and *ready* that `site build` bakes
    into `site/data/` comes from `claims.compose` through `graph.tokens`,
    `claims.unresolved` and `claims.margin`, on the resolution every reader uses: a
    claim's token on the map, on its board, in next steps and on the marketing page is
    its composition's, a board's is the worst of its claims', a milestone's steps are
    `claims.unresolved`'s.
    The build refuses to write a page whose token, word or mark for a claim, a
    requirement, a board, a milestone or the project differs, or whose words put a value
    inside a limit its claim's condition rejects (`page.status_problems`), and the browser
    computes no status, margin, readiness, reading order, age or sensitivity: no page
    script reads a verdict's flags or a recorded result's `counts`, does arithmetic on a
    limit, takes a worst-of or sorts by a status; a recorded result's tone comes from its
    baked token. A baked file edited by hand reads *edited by hand* in `site status`,
    `status` and `doctor`, a resealed page that shows behind text as current included. The
    agent's text in `site/content/` reads current only while its `as_of` stamp equals the
    digest of the tokens of its board's claims and of every claim it names now; otherwise
    the page says it was written before the latest results, unstamped text included; and
    every number in it that no number the page keeps matches at its precision, in its
    unit, is marked. A figure on the page (issue #15) is the evidence of one named
    verdict entry, bound by content: it reads current only beside the fresh served
    entry, and otherwise says it is stale or another result's, with that result's own
    outcome; it sits beside the chips of every claim its gate covers; its colour scale
    is a pack constant, never a limit; a chart's limit and margin are the floor's and
    its other numbers its evidence's; a renderer that is missing or fails moves no
    status; and no file the page links is deleted by the build that links it. (What
    slipped through: the reference page decided its go/no-go in the browser from a
    hand-kept claim list, so a claim added to a board never reached it; the
    template ranked anchored verdicts by their `passed` flag beside the resolver's word,
    sorted verdict rows by a status table of its own and subtracted a chart's limit from
    its value; and nothing kept prose from saying a claim passes after it failed; and, in
    review of phase 5, a Failing claim read "99% inside the limit" (the evaluator's margin
    beside the claim's limit), a stamp missed a claim its text named from another board,
    "3 mm" matched a count of claims, and an evidence copy the same build deleted vouched
    for a number; and, before issue #15, the evidence copy was bound by name, so a design
    that went A, B, A published B's files under A, a viewgen whose tool was missing
    deleted its committed picture, and a prose figure under `assets/` read present and
    was deleted; and, in review of its figures, the sentence beside an older picture
    called it "an earlier result" when after A, B, A it was B's, the later run.)

One more came with checkpoint P2.3, numbered 15 before 14 landed (14 was reserved for
the site, which landed in architecture phase 5). Enforced in `tests/test_mutation.py`,
over every mutation entry point in `src/`:

15. **A mutation pass, a trace build and an amber run write nothing in the project,
    change nothing they were handed, and never say more than they ran.** A mutation
    pass runs in process on a copy of the known-good control: nothing is
    written under the project or a pack directory, and the known-good design is as it
    was afterwards. A trace build and an amber run happen in a subprocess on a copy
    outside the project, watched on every thread; one that writes inside the project
    leaves no trace file and no row a later run could use, and is named. A mutation counts only when it is conclusive — its key was read and
    the run passed or failed — and one that skipped or errored is printed as
    inconclusive, never as a fail; with none conclusive the line says `mutation 0
    conclusive`. (What slipped through the harness before the code existed: a plan
    that set every key to None made every mutation inconclusive, and the line read
    `mutation 0 conclusive` over every evaluator; a runner that drew up its plan after
    seeing which mutations passed printed `mutation 1/1 fail` over an evaluator one of
    two had passed.)

One more came with architecture phase 3b, when the map gained a middle. Enforced in
`tests/test_derivation.py`:

16. **The trace says what it saw, and no more.** A choice it did not move (a yes/no, a
    word, a structure, a zero, a field not settable) and a value no move changed are
    said as not moved or not seen to move — never as independent; an order between
    values is said as inferred from the trace; a trace is written only whole, every
    numeric choice moved both ways, and a build that timed out, died, wrote into the
    project, or raised differently when run alone leaves none, now or at a later run;
    a move is said at its size (an integer's may be more than 10%); amber is shown only
    while the trace is the current design's and the evaluators' code and the
    acceptance conditions it ran under are the current ones — its runs too, never a
    row run under others — and otherwise says it is not current. (What slipped
    through: before the trace, the map drew each line from the values its evaluators
    read, so 94 of PETGasus's 110 choices reached no claim and drew as if nothing
    depended on them; and a pass at 0.8% margin on three unconfirmed assumptions read
    as green as one at 80%. In the review of phase 3b: a refused build's scratch row
    was written into the next run's trace, an untraced map called six zones unused,
    and `why` said "nothing at ±10%" of a choice whose builds raised.)

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
| 3 | `test_invariants.RegistryRefusesLoggers`, `test_proposers.AProposerIsNeverAnEvaluator` |
| 4 | `test_invariants.ReportNeverOverclaims`, `test_status_table.UnqualifiedBesideAPassIsNeverChecked`, `test_invariants.CheckedMeansEveryEvaluatorPassed`, `test_goalposts.APassMustMeetTheAcceptanceItRead`, `test_goalposts.AValueOutsideTheAcceptanceIsNeverChecked`, `test_goalposts.AnEvaluatorStatesItsOwnUnits`, `test_physical.TheCheckedSectionHoldsOnlyBoundResults`, `test_requirements.ARequirementIsNeverMoreResolvedThanItsClaims`, `test_mechanism.AMechanismNeverMovesAStatus`, `test_basis.ABasisNeverChangesAStatus`, `test_aero_openfoam.NoGuardSpeaksForTheDragArea`, `test_aero_openfoam.TheDragAreaIsJudgedAtTheTopOfItsBand`, `test_aero_openfoam.TheDragGateStatesItsOwnUnits` |
| 5 | `test_packs.ControlsAreSealed` |
| 6 | `test_packs.NegativeControlsFire` |
| 7 | `test_staleness.StaleIsNotCurrent`, `test_goalposts.TheGoalpostLivesInClaims`, `test_physical.AMovedArticleReadsStale`, `test_fig4.TwoArticlesExactly`, `test_mechanism.TheMechanismMovesNoKey`, `test_basis.ABasisIsNeverAKey`, `test_round.ABehindRoundRecordSaysSo`, `test_derivation.TheTraceIsKeyedToItsDesign` |
| 8 | `test_records.IndexNeverDisagreesWithRecords`, `test_records.NoCommandWritesARecord`, `test_records.MilestonesAreRecords`, `test_records.TheExportRecordIsSealedAndChained`, `test_records.RequirementsAreRecords`, `test_records.PreferencesAreRecords`, `test_proposers.ProposeWritesOnlyItsInputs`, `test_round.OnlyRoundWritesTheRoundRecord`, `test_round.NothingReadsTheRoundRecordForTruth`, `test_records.GroupsAreRecords`, `test_round.TheRoundCommitStagesOnlyTheProjectsPaths`, `test_round.RoundsCommitOnlyAsAListedPerson`, `test_record_commands.InitStartsARepository`, `test_derivation.TheTraceIsWrittenOnce`, `test_figures.TheEvidenceManifestIsScratch` |
| 9 | `test_admission.AdmissionIsDemonstrated`, `test_admission.QualificationIsPaired`, `test_admission.EveryConclusiveMutationMustFail`, `test_admission.TheCheckRunTakesTheQualifiedPath`, `test_context.OutsideTheContextAPassDoesNotCount`, `test_context.AFailOutsideStillCounts`, `test_context.KnownGoodOutsideIsUnqualified`, `test_context.AMutationPassingOutsideStillCounts`, `test_goalposts.AGoalpostIsNeverAKey`, `test_basis.ABasisNeverQualifies`, `test_aero_openfoam.TheDragAreasGoalpostMovesNothingElse` |
| 10 | `test_prerequisites.PrerequisiteFailureIsNeverAPass`, `test_prerequisites.ACachedPassNeverSurvivesAFailedPrerequisite`, `test_prerequisites.NeedsCycleRefused`, `test_prerequisites.TierInversionRefused` |
| 11 | `test_owner.AnOwnerWrittenByHandNeverCounts`, `test_signing.HumanChannelOnly`, `test_signing.WhoAndWhenAreNeverTyped`, `test_signing.TheResultsFileIsSealedAndChained`, `test_signing.AnOwnerOnlyThroughTheChannel`, `test_physical.SignedMeansSomething`, `test_physical.APhysicalFailNeverLosesItsPowerToFail`, `test_physical.AContradictionGoesOnTheEvaluatorsTrackRecord`, `test_physical.AnExpertJudgmentStaysWithItsAuthority`, `test_physical.AResultBindsToAnExportedArticle`, `test_physical.AFailIsSupersededOnlyOnAnotherExportedArticle`, `test_mechanism.TheSealSurvivesTheNewSpelling` |
| 12 | `test_physical.RenderersAgreeOnPhysicalClaims`, `test_export.ReadyIsOnePredicate`, `test_export.TheBoundaryReExecutes`, `test_export.ACacheThatLiesIsCaughtAtTheBoundary`, `test_export.DryRunIsTheSamePath`, `test_export.GoingAheadIsAPersonsDecision`, `test_vocabulary.TheHardwareClauseIsAlwaysSaid`, `test_export.EveryCacheReaderSaysAsLastEvaluated`, `test_requirements.EveryReaderAgreesOnARequirement`, `test_requirements.ReadinessStaysTheClaims`, `test_returns.AConditionReadsOnlyACheckedClaim`, `test_returns.MayReturnNeverMovesTheExitCode`, `test_returns.EveryReaderAgreesOnAReturnCondition`, `test_round.TheRoundRecordSaysWhatTheCompositionSays`, `test_round.NothingReadsTheRoundRecordForTruth`, `test_graph.AnEdgeChangesNoStatus`, `test_site_truth.NextStepsAreThePredicate`, `test_derivation.AmberChangesNoStatus`, `test_unseen.UnseenMovesNoStatus`, `test_aero_openfoam.ALadderRecordIsReadOnlyByItsProcess` |
| 13 | `test_preferences.APreferenceIsAPersonsAct`, `test_preferences.ANomineeAloneNeverCounts`, `test_preferences.ThePreferenceFileIsSealedAndChained`, `test_preferences.APreferenceIsBoundToWhatThePersonRead` |
| 14 | `test_site_truth.TheSiteNeverSaysMoreThanTheResolution`, `test_site_truth.StampedProseIsFlaggedWhenBehind`, `test_site_truth.ANumberNoBakedFileHoldsIsFlagged`, `test_site_truth.TheBrowserComputesNoTruth`, `test_derivation.TheMapDrawsWhatTheTraceObserved`, `test_figures.EvidenceIsBoundByContent`, `test_figures.NothingTheBuildDeletesIsLinked`, `test_figures.AMissingRendererMovesNoStatus`, `test_figures.AFigureIsItsEntrysEvidence`, `test_figures.AFigureSaysWhichResultItIs`, `test_figures.TheClaimsStatusSitsBesideEveryFigure`, `test_figures.TheScaleIsAPackConstant` |
| 15 | `test_mutation.MutationIsSealed`, `test_derivation.TheTraceBuildsInACopy`, `test_derivation.AnAmberRunWritesNothing` |
| 16 | `test_derivation.UntracedIsNeverIndependent`, `test_derivation.APartialTraceIsNeverWritten`, `test_derivation.AmberOnlyWhileCurrent` |

## House rules

- **`src/nopekit/` is standard library only.** No third-party imports, ever — the
  spine must never be the reason an install fails. CI proves this by AST-walking every
  import, including function-local ones.
- **Generated files are outputs, not sources.** Never hand-edit one; fix the model.
- **Every constant carries its provenance** — why this value, and *what was tried and
  rejected*. The rejected alternatives are the field that pays: without them every
  fresh context window re-litigates every settled number.
- **The site renders the records and the verdict cache; it never computes truth.**
  Nothing under `site/` re-derives a measurement or decides whether a claim passes —
  if a number on the page is wrong, a record or the cache is wrong. The browser
  computes no status, margin, readiness or sensitivity: every number, status, word
  and order is in a baked file. And no build step: plain HTML, CSS and ES modules,
  for the same reason the spine has no dependencies.
- **Comments say what slipped through.** Where a rule exists because something got
  past a check, name it. The modules are dense with this on purpose.
- **No reference to the parent project.** nopekit carries its method and none of its
  content; [`docs/ORIGINS.md`](docs/ORIGINS.md) is the only place it is named, and a
  test enforces that.

## Where facts live

Each fact has one home; everything else that shows it is an output of that home.

- **Records** — tracked, one file per record, edited by hand or by the agent and read
  strictly (an unknown key is refused, with a suggestion): `requirements/<id>.json`
  (the person's words; claims point at them with `requirements`), `claims/<id>.json`,
  `params/`, `decisions/`, `needs/`, `inputs/<id>.json`, `results/<claim-id>.json`,
  `views/*.json`, `milestones/<name>.json` (a spend and the claims it requires),
  `exports/<name>.json` (that spend's exports, sealed and chained, appended only by
  `export`), `preferences/<decision-id>.json` (a decision's recorded preferences,
  sealed and chained, appended only by `prefer`: never edit one by hand),
  `groups/<id>.json` (a board of claims, a zone of choices or a station of shared
  values: how the page groups the map, with no status and no position), and
  `.nopekit/project.json` (meta, `model_entry`, the live `packs`, the `people` a round
  may commit as).
  `results/<claim-id>.json` holds a claim's physical results and its owner's or
  authority's attributions, sealed and chained, appended only by `claim physical`
  (P2.5a): never edit one by hand — a broken seal refuses the file.
- **The site's text** — `site/content/` is the agent's prose and figures: tracked,
  stamped by `site stamp`, and not a record. The page flags a piece whose stamp its
  claims' statuses have moved past.
- **The model** — `model/*.py` owns every parameter's value, units and rationale, and
  in `PARAMS` what lost to it; a param record holds only the provenance the model
  cannot (`source`, `grounded_by`, `tags`). An assumption claim's id in
  `grounded_by` says the value IS that assumption (the graph's `sources` edge).
- **The verdict cache** — tracked, `.nopekit/verdicts/<gate id>/`: one file per
  rho (the hash of everything the gate read) and one per control outcome at a
  version, never rewritten. Git plus this cache is the history; there is no run history.
- **The trace** — tracked, `.nopekit/trace/`: one file per traced design, keyed by its
  instructions, choices and the data its build read, and one amber file per evaluation; written only by
  `model trace`, never rewritten; nothing reads it for a status.
- **Outputs, ignored** — `.nopekit/ledger.json` (the index of every record, rebuilt
  after each command), `.nopekit/cache/last_check.json` (statuses, counts and the
  parameter view as the last full `check` saw them), `.nopekit/obs/` (what each run
  cost), `.nopekit/out/` (scratch and evidence), `.nopekit/cache/evidence/` (the
  bytes each recorded run cited, so the page copies evidence by content),
  `REPORT.md` (the readiness report, `report --write`), `out/<milestone>/` (the
  package `export` wrote),
  `.nopekit/cache/trace/` (a trace's resumable scratch), `.nopekit/out/trace.log`
  (a background trace's log), `.nopekit/out/serve.json`, `serve.lock` and `serve.log`
  (a detached live server's address, the lock it holds for its life, and its log),
  `site/data/build.json` (the page's build time, each result's age and the live 3D
  files it names), `site/assets/live/` (a bound figure's 3D file for a result no
  export re-ran) and `.nopekit/out/figures/` (a bound viewgen's scratch). The index
  and `last_check.json` are the whole project in two reads; nothing reads either for
  truth.
- **Outputs, tracked** — `.nopekit/round.json`, the round record: the resolution at
  the end of a round as tokens (every claim's and requirement's status key, the
  counts, readiness, each evaluator's outcome) and the typed graph, with the digests
  of the records, the verdict cache and the design it was computed from, a round
  number and its own digest. Written only by `nopekit round` (and, from phase 4,
  `merge`), never by `check`; nothing reads it for truth — `status` and `doctor`
  say only whether it is current, behind or edited by hand. And `site/data/` and
  `site/assets/`, baked by `site build` (clock-free, written only when their bytes
  differ), where a project publishes its page.
- **The pinned packs** — `spec/packs.json` (Eddy milestone 0): tracked, written by the
  agent, read by Eddy's hook as the fourth entry of a candidate's spec tree beside its
  claims, requirements and milestones, and not a record — no digest reads it and no
  committed output moves when it changes (`docs/EDDY_CONTRACT.md`, section 5).

`docs/SPINE_CONTRACT.md` ("Where facts live", then `store.py`'s section) has the
layout in full, with each writer.

## Layout

| Path | What |
|---|---|
| `src/nopekit/` | the spine — stdlib-only modules; `models.py` is the type contract |
| `src/nopekit/store.py` | the records: one file per record, the strict reader, the writer, the generated index, the one-time legacy migration |
| `src/nopekit/milestones.py` | the boundary that spends: a milestone's closure, the export's refusals and judgment, disagreements, the test card, the package |
| `src/nopekit/verdicts.py` | the per-gate verdict cache: what a gate read (`ParamTrace`, `LedgerView`, the audit hook), portable digests and the spine digest, rho, cache and control entries, freshness, admission at the gate's current version, the one resolver every reader uses, the sweep `check` runs, and `last_check.json` |
| `src/nopekit/vcs.py` | the only git edge: a read half (argv form, a clean environment, a timeout, never raises) and a writing half (`init_repo`, `add`, `commit`) under its own contract |
| `src/nopekit/receive.py` | Eddy's push validator (`nopekit validate`, milestone 0): the ref layout, the trailer schema, each claiming commit's tree and its diff against the previous claiming commit, the state file's five checks, the object scan, and the rule table (`RULES`, `NOTES`) the contract mirrors; executes nothing from the tree, starts no git process itself, writes nothing in the repository, fails closed |
| `src/nopekit/derivation.py` | the trace: each choice moved ±10% in copies, what moved, the order it infers, amber, the background start (non-spine, clock-free) |
| `src/nopekit/graph.py` | the typed falsification graph (SPEC 19): nodes and typed edges from the records, the registry and the served verdict entries, and the status tokens beside it — what the round record holds and the site's map draws |
| `src/nopekit/page.py` | the two pages' baked data (SPEC 28-33, 40): the map's layout from the graph and the groups, `map.json` and `page.json`, the floor, next steps, the evidence plan, the stamps, and invariant 14's checker |
| `src/nopekit/prose.py` | the agent's text on the site: front matter and the stamp, the markdown subset, the number scan, and the style scans (SPEC 30, 34, 35) |
| `src/nopekit/rounds.py` | the round record, `.nopekit/round.json` (SPEC 18): its digests, its bytes, its reader states; imported only by `cli` |
| `site/` | a project's site — scaffolded by `nopekit site init` from `src/nopekit/site_template/`: `index.html` (a redirect to `engineering/`), `engineering/index.html` (the outline: the representation, the verdict, the map, a section per board, next steps), `marketing/index.html` (the product in a viewer, its story, what it must do, the choices behind it; SPEC 40) and `technical.html` (today's panels), all four the project's own; `content/` the agent's prose (`story.md` the marketing page's), stamped; `data/` and `assets/` baked by `site build`. Plain HTML/CSS/ES modules, no build step |
| `packs/` | domain packs: evaluators (`gates/`) and proposers (`proposers/`). Ordinary directories: no build step, no registration |
| `.claude-plugin/`, `.codex-plugin/`, `.cursor-plugin/` | one plugin, three hosts: a manifest each, held to one name, version (`VERSION`) and description by `tests/test_plugin.py` (`OnePluginThreeHosts`); `marketplace.json` serves Claude Code and Codex |
| `skills/` | the skills every host loads (`nopekit`, `pack-authoring`); `agents/openai.yaml` is each one's Codex listing |
| `hooks/` | the plugin's Stop hook: `hooks.json` (Claude Code's format, which Codex reads too) and `cursor-hooks.json` (Cursor's, `stop.py --cursor`); `stop.py` runs `nopekit round --hook`, which commits each round, with the session's cwd kept off the import path |
| `server/` | Eddy's pre-receive hook, `pre-receive`: `nopekit validate --stdin` with the quarantine handed over as flags and a crash read as a refusal — the inverse of the Stop hook's exit contract. Installed in the bare repository by Eddy's image; never a plugin host's |
| `AGENTS.md` | Codex's and Cursor's way in: it points here |
| `examples/bracket/` | the reference project — zero dependencies, runs the whole loop |
| `tests/` | the honesty invariants and the pack gate-on-the-gates |
| `tests/_env.py` | the one environment every test subprocess runs in (`_env.run`): a temp `HOME`, no user packs, git isolated from the machine, the tools still visible |
| `tests/_projects.py` | test projects built once: a bracket copy (the legacy bracket by default, the committed one with `migrated=True`), and a pack's baseline wrapped as a project |
| `tests/_draw_standin.py` | a stand-in renderer for bound figures: installed as `nopekit-test-draw` on a temporary PATH, it writes committed fixture bytes (a PNG Pillow made, a GLB trimesh made) and logs each call; it plants a crash, a hang, an oversize file and inputs moved mid-draw |
| `tests/test_field_browser.py` | the field viewer in a headless Chromium (issue #15): a trimesh GLB's vertex colours read back within 2; skipped, saying not walked, where the browser, trimesh, Pillow or three.js is absent |
| `tests/bracket_legacy/` | a fixture: the bracket's `.nopekit/` as it stood before its 1.3 migration (ledger, run history, ignore file), which `_projects.bracket_copy` puts back around today's sources. Never edited to make a test pass |
| `tests/_transcript.py` | Phase 1's target transcript as data: replayed on a fresh clone by `test_fresh_clone.py`, its lines held to their shapes by `test_shapes.py` |
| `tests/oracle/` | scripts, not tests: CI's bracket failure signature (`bracket_signature.py`) and the R-8 differential oracle run at phase review (`r8_statuses.py`) |
| `METHOD.md` | the doctrine |
| `docs/EXTENSION_PROTOCOL.md` | how an agent grows a capability nobody prebaked |
| `docs/PACK_FORMAT.md` | the pack contract |
| `docs/SITE_CONTRACT.md` | the site: view kinds, locators, `state.json` |
| `docs/EDDY_CONTRACT.md` | the layout contract with Eddy: refs, trailers, the candidate tree, the spec tree, evidence, the state file, the rule table (`receive.RULES`, held equal by a test), the object scan, the hook, the file browser's routes, and what the hook cannot see |
| `docs/SPINE_CONTRACT.md` | internal: where facts live, each module's public surface, and the limits — what the spine cannot see |
| `docs/SPEC.md` | the architecture as decided on 2026-10-14: terms, what stays, each decision with its reason, the layout, the site, the sequence of work. The only plan. Ids like `D-14`, `S-60`, `P2.5b` in code comments refer to the retired plan, readable at commit `a00d0e5` |

## Adding a pack

Read `docs/PACK_FORMAT.md` and the `pack-authoring` skill. In short: `pack.json`
(tier 1, ~20 words), `PACK.md` (tier 2, ~150 lines), `references/` (tier 3, loaded
only for the task at hand), gates that each declare a negative control, and
`selftest/baseline.json`.

Then, and this is the part that decides whether it gets merged:

```sh
PYTHONPATH=src python3 -m nopekit pack validate <name>
PYTHONPATH=src python3 -m nopekit gate selftest --pack <name> --junit <file>.xml
```

`pack validate` checks the layout and demonstrates tiers 0–1; `gate selftest --pack`
runs every tier's control, and its JUnit file is the admission evidence the PR
carries. There is no `pack new` and no `pack export` (D-24). CI runs `gate selftest`
at the repo root, where it demonstrates every bundled pack.

A pack whose gates have never demonstrated failure does not get merged.
