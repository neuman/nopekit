# Reference project — wall bracket

The smallest project that still exercises every rule in `METHOD.md`, with **zero
dependencies**. Standard library only, like the spine. You can run the whole
pipeline on a fresh machine with nothing installed, which is the point of having a
reference project at all.

```bash
cd examples/bracket
nopekit check
```

## What you should see

On a fresh clone, before you change anything:

```
[FAIL] bracket.deflection : 0.700 mm at 15 N (limit 0.5 mm)                   cached
6 gates: 0 executed, 6 cached — 5 ok, 1 FAIL — tier 0
BLOCKING — 3 critical claim(s) must not be spent against:
[FAIL ] C1 Tip sags no more than 0.5 mm at rated load — bracket.deflection : 0.700 mm at 15 N (limit 0.5 mm)
[gap  ] C6 The load is static and centred on the arm — no owner recorded
[gap  ] C7 First mode is clear of the pump that sits on the shelf — no evaluator
```

Nothing ran. Every verdict came out of `.nopekit/verdicts/`, where it was recorded
against the exact bytes of the model, gates and selftest you just cloned; a cached
pass is not printed, a cached failure is, and `git status` stays empty afterwards.
The exit code is 1.

**The failure is deliberate.** The default thickness is 7 mm and the arm sags 0.70 mm
against a 0.5 mm limit. Open `model/bracket.py`, change `thickness` to `8.0`, and run
`nopekit check` again — `bracket.deflection` goes green at 0.47 mm (C7 still blocks:
no evaluator covers it yet; and C6 is an assumption nobody owns, a gap until its owner
records it — name them as `"owner"` in `claims/C6.json`, and they run `nopekit claim
physical C6 assume` in their own shell; the name in the file alone does not count). C5
waits on an article: `nopekit status` says *pending build*, and the project is not
*ready* until every required claim reads checked. Its test is written down in
`claims/C5.json` (its mechanism's `how`), so the person who tested a printed bracket
records the result in their own shell — `nopekit claim physical C5 pass --evidence <photo> --detail "..."`
— and it counts on that design only: change a value afterwards and C5 reads stale, the
article named for rebuild. Before you run anything, `nopekit status` names each gate the
edit reached and what moved for it; `check` re-runs exactly those and serves the rest
from the cache.

That is the entire loop: one parameter, one gate, one verdict. Everything else in
nopekit is this loop with more expensive gates attached.

## What it demonstrates

**Requirements first, and every claim says how it settles.** `requirements/R1.json` to
`R4.json` are what the person asked for, in their words ("Hold the pump on the shelf
without breaking, and without a sag anyone can see"); each claim names the ones it
serves, and each says in its `"mechanism"` what would settle it — where its evidence
bottoms out (`by`), in words (`how`), and the evaluators it expects. A requirement reads
the worst of its claims: R1 is failing while C1 is, R2 a gap while nothing evaluates
C7. *Ready* stays the claims': a requirement is shown, never required. `nopekit status`
says it in one line —

```
requirements 4 — R1 failing (C1) · R2 gap (C7) · R3 pending build (C5) · R4 checked
```

— and `nopekit gap` lists every claim whose mechanism has yet to run, with what would
settle it:

```
[gap  ] C7 First mode is clear of the pump that sits on the sh… — no evaluator
        mechanism: automated — A closed-form estimate of the arm's first natural frequency with the shelf and the pump at its tip, against the pump's 50 Hz with margin
```

**Rule 1 — one source of truth.** `model/bracket.py` holds every input. Section
properties, stresses, deflections and the print footprint are all computed by
`build()`. No gate recomputes geometry, and no record under `claims/` carries a
parameter's value: the model owns it.

**Rule 2 — derive, never duplicate.** `usable_bed = bed_xy - 2 * brim_mm`. The brim
allowance exists in one place, so it cannot drift.

**Rule 3 — provenance.** Every field in `Config` carries why it holds that value, and
`PARAMS` beside it says what was tried and lost, by how much: 4 mm of thickness sags
3.75 mm, 7.5x the limit; an 80 mm arm, 1.7 mm; a 5.0 mm hole is a line-to-line fit an
FDM hole will not hold. Run `nopekit why thickness`:

```
param thickness = 7.0 mm   (model/bracket.py Config.thickness)
...
REJECTED (1)
  4.0 mm — 3.75 mm deflection, 7.5x the limit   (model/bracket.py PARAMS)
```

**Decisions keep their losers.** `decisions/print-in-petg.json`, written by `nopekit
decide`, gives each material that lost its type and what would bring it back: PLA is
*infeasible* (it creeps under a sustained load), ABS *unevaluated* (the model holds no
ABS properties), and a machined aluminium bracket *preferred against* — Robin, who
prints their own parts (R4), is the person named. Run `nopekit why print-in-petg`:

```
REJECTED (3)
  PLA — creeps under sustained load at room temperature — …
      infeasible · not machine-checkable: when the load is no longer sustained: …
  ABS — the model holds no ABS properties (MATERIALS has petg, pla and alu6061), …
      unevaluated · not machine-checkable: when ABS is added to MATERIALS …
  a machined 6061 aluminium bracket — Robin prints their own parts, in PETG (R4); …
      preferred against, not recorded by Robin Okafor · does not hold: C3 margin < 0.1, now 0.987 (bracket.bearing)
```

The aluminium bracket's return condition is structured: `check` evaluates it against
C3's margin and would print `may return:` if the bearing stress came within 10% of its
limit (it never changes `check`'s exit code for it). The preference is Robin's to
record, typed in Robin's own shell: `nopekit prefer print-in-petg`. No agent records it
for them, so it reads *not recorded by Robin Okafor*, and `nopekit doctor` warns about
it — as it should.

**Rule 5 — falsification controls.** Six gates, six fixtures in
`selftest/bad_configs.py`, each changing exactly one physically meaningful thing in
the direction that gate cares about, starting from the known-good design in
`selftest/known_good.py` — never from whatever the model says today:

| Gate | Basis | Known-bad fixture |
|---|---|---|
| `bracket.deflection` | sourced | same bracket at 1/4 thickness (~64x worse) |
| `bracket.bending_stress` | sourced | same bracket at 20x load |
| `bracket.bearing` | sourced | one bolt in a thin plate, bearing area collapsed |
| `bracket.model_validity` | sourced | short deep arm where shear deflection stops being negligible |
| `bracket.bed_fit` | model-only | a 400 mm arm — nothing wrong but the footprint |
| `bracket.min_wall` | model-only | a 1.2 mm nozzle that cannot resolve the section |

The basis says where each gate's verdict logic comes from: the four structural gates
follow textbook beam formulae (sourced); the bed-fit and wall rules are the model's
own (model-only). It is shown in `nopekit gate list`, and it changes no status.

```bash
nopekit gate selftest
```

Every gate must **fail** its fixture. One that passes is reported as broken, and a
gate whose control has not fired at the current version never counts as a pass.
`check` demonstrates each control at the current version too, and the fired controls
are committed beside the verdicts (`control-*.json`).

**A gate that polices another gate.** `bracket.model_validity` checks that
Euler-Bernoulli is applicable at all — below a slenderness of about 5, shear
deflection stops being negligible and the deflection gate quietly becomes optimistic.
Any pack shipping closed-form analysis wants one of these. Without it, the cheap gate
silently becomes the wrong gate as the design moves.

**Rule 9 — honest reporting.** `nopekit report` separates what was proven from what
was not. The bracket's outdoor-durability claim is `physical`: no gate will ever
settle whether it survives two winters on a wall, and the report says so rather than
finding something adjacent to turn green.

## Files

Where each fact lives. Everything marked *tracked* is a source, reviewed like code;
everything *ignored* is an output a command rebuilds.

```
model/bracket.py              tracked   the model: Config, PARAMS, build(). Runnable on its own.
gates/structural.py           tracked   six tier-0 gates, all pure arithmetic
selftest/known_good.py        tracked   the design every control starts from
selftest/bad_configs.py       tracked   one known-bad fixture per gate
requirements/R1.json … R4.json tracked  what the person asked for, in their words
claims/C1.json … C7.json      tracked   what must be true: one record per claim, the
                                        filename is its id. Edit these by hand.
params/load_n.json            tracked   the one param record: load_n IS assumption C6
                                        (`"grounded_by": ["C6"]`), so C1 rests on C6
decisions/print-in-petg.json  tracked   a decision and what lost, typed, with return
                                        conditions (written by `nopekit decide`)
docs/decisions.md             tracked   the decision log, generated from decisions/ by
                                        `nopekit report --write`; never edit it
milestones/print-v1.json      tracked   a spend — the print — and the claims it
                                        requires (C1-C4), with its generator
groups/*.json                 tracked   the page's boards (claims), zones (choices)
                                        and the one named station (`allowable`)
site/content/*.md             tracked   the page's text, one file per board plus
                                        intro.md and next.md, each stamped `as_of`
generators/profile.py         tracked   writes the print's package: the plate's
                                        outline and the print settings
exports/print-v1.json         tracked   written by `nopekit export print-v1` only:
                                        each export, sealed and chained
.nopekit/project.json        tracked   name, revision, the model entry, installed packs
.nopekit/verdicts/<gate>/    tracked   the verdict cache: one entry per gate and one
                                        control entry per gate, keyed on the bytes
                                        each one read. Evidence, committed.
.nopekit/round.json          tracked   the round record: every status as a token and
                                        the typed graph, written only by `nopekit round`;
                                        an output — never edit it, never read it for truth
.nopekit/trace/              tracked   the trace and its amber file, written once per
                                        design by `nopekit model trace`; never edited
.gitignore, .gitattributes,   tracked   marked blocks nopekit maintains: bytecode is
.nopekit/.gitignore                    ignored, line endings are pinned (digests are
                                        over bytes), outputs are ignored
.nopekit/ledger.json         ignored   the generated index of the records — the whole
                                        project in one read; edit the records, never it
REPORT.md                     ignored   the readiness report, `nopekit report --write`
site/ (but site/content/)     ignored   the page, built on demand: `nopekit site init`,
                                        then `nopekit site build`
out/print-v1/                 ignored   the package `nopekit export print-v1` wrote
.nopekit/cache/, obs/, out/  ignored   this checkout's memory, costs and gate scratch
```

There is one param record, `params/load_n.json`, and it holds only what the model
cannot: that the rated load IS an assumption. C6 says the load is static and centred
on the arm; `load_n` is that static load, so its `grounded_by` names C6, and because
`bracket.deflection` reads `load_n`, C1's verdict rests on C6 (`nopekit why C1` says
`rests on: C6 (gap; bracket.deflection read load_n)`). It moves no status: C1 fails
on its own, and C6 reads Gap until its owner records it. Every value, rationale and
rejected alternative lives in `model/bracket.py`.

## Move a limit in the claim, never in the gate

C1's limit lives in `claims/C1.json`, and `bracket.deflection` reads it
(`ctx.acceptance("C1")`). Change the limit there and `nopekit check` re-runs that
gate alone, against the new number — no control runs, because the known-good design
states the C1 it was calibrated against (`selftest/known_good.py`'s `CLAIMS`). C3's
15 MPa and C4's 204 mm are numbers their evaluators also compute from the model
(`design_stress`, `usable_bed`); each value is compared with its claim's own condition,
and `check` prints a warning when the two limits part — set `bed_xy` to 250 and watch
it name `bracket.bed_fit`.

## The committed cache

The twelve files under `.nopekit/verdicts/` are what make a fresh clone's first
`check` six cache hits. Each is keyed on the spine that wrote it (a canonical reading
of nopekit's verdict-path modules, blind to their comments and docstrings), on the
bytes of the gate code and selftest it ran, and on the values it read from the model.
Edit the spine's code, a gate, a fixture, or a value a gate reads, and the entries
that depended on it go stale: `check` runs those gates again and leaves new entries
for `git status` to show. When that is the nopekit checkout's own doing, the
committed cache must follow in the same change — `tests/test_bracket_cache.py` goes
red until it does, and names these commands. To regenerate, from this directory:

```bash
rm -rf .nopekit/verdicts .nopekit/cache .nopekit/obs .nopekit/out
nopekit check              # exits 1: bracket.deflection fails on purpose
nopekit gate selftest      # writes nothing new: the controls were just recorded
nopekit model trace        # writes nothing when the committed trace is the design's
nopekit round              # the round record of the regenerated cache
nopekit site stamp --all   # only when a status moved: read the text again first
git add -A .
```

The bracket also commits its round record, `.nopekit/round.json` — the resolution at
the end of a round, as tokens, with the typed graph. A change that moves what it holds
without moving the cache (the composition, the graph, a bracket record) ends with
`nopekit round` alone; `tests/test_bracket_cache.py` (`TheCommittedRoundRecordIsCurrent`)
goes red until it does.

The bracket commits its trace too, under `.nopekit/trace/`: one file for its design
(which derived values each of its eleven numeric choices moved at -10% and +10%; the
material is a word, and is not moved) and one amber file (C2 and C3 re-run with the
load, which assumption C6 sources, moved: both hold). A change to the model is another
design: run `nopekit model trace`, then `nopekit round`, and commit both
(`TheCommittedTraceIsCurrent`). The trace never rewrites a file; the old design's
stays until you `git rm` it.

On a fresh clone `nopekit round` writes nothing and `git status`
stays clean.

`spec/packs.json` (`{"schema": 1, "packs": {}}`: the bracket pins no pack) is the
spec's one real file under Eddy (`docs/EDDY_CONTRACT.md`): the pre-receive hook
reads it to hold the pinned packs and the live list in `.nopekit/project.json` to
the same names. It is not a record — no digest moves with it, the index never
lists it, and committing it regenerated nothing above.

The bracket lists no `people` in `.nopekit/project.json`, on purpose: `nopekit round
--commit`, and the plugin's Stop hook that runs it, commits a round only as a person
listed there, so a round run here, inside nopekit's own repository, never commits.

The readiness report is not committed: `nopekit report --write` renders `REPORT.md`
here, an output git ignores — it was `docs/readiness.md`, tracked, until it drifted
from the records it was rendered from.

## The page

`nopekit site init` scaffolds `site/`, and `nopekit site build` bakes the page from the
records, the verdict cache and the text in `site/content/`; `nopekit site serve
--watch` serves it and builds it again when any of those change. The page reads like
a poster: the verdict sentence, the grounding map, then one section per board, then
the next steps. The map draws the four requirements on the left, the four zones of
choices (`groups/arm.json` and its neighbours), the shared values the evaluators read
— `allowable` is named in `groups/allowable.json`, and `slenderness`, which two
claims' evaluators read, becomes a station on its own — and the four boards on the
right, one line per claim in its status's colour, glyph and word.

The text beside each board is the only part of the page an agent writes, so it is the
part held most closely. Each file carries an `as_of` stamp, the digest of its claims'
statuses when it was written (`nopekit site stamp <board>`, or `--all`); when a status
moves, the page marks the text as written before the latest results. Every number in
the text must be a number the build bakes: a number nothing holds is marked on the
page and named by the build. The built page is not committed here (`.gitignore`
keeps only `site/content/`), so a clone builds its own.

## Spend: `nopekit export print-v1`

`milestones/print-v1.json` names the print and the claims it needs checked first
(C1-C4); C5 needs the print, so the print does not require it, and C6 and C7 are
shown as unresolved and never hidden. `export` is where a spend meets the evidence,
so it trusts nothing the cache says: it re-runs every evaluator those claims rest
on, with their controls, and refuses while any reads less than Checked.

```bash
nopekit export print-v1 --dry-run   # at thickness 7.0: would refuse — C1 failing
# set thickness = 8.0 in model/bracket.py, then
nopekit export print-v1             # writes out/print-v1/ and exports/print-v1.json
```

The package holds the plate's outline (`bracket-profile.svg`), the print settings,
the values the article was built from (`model.json`), the milestone's `REPORT.md` and
a `MANIFEST.json` naming every file's digest. The export prints a test card, and the
package's `REPORT.md` carries it with the article's hash: C5's measurement, and C1-C4
as cross-checks — a ruler on the tip of the printed arm is a result `nopekit claim
physical C1 --article <hash> --measured <mm>` records (the value decides pass or
fail), and a fail there is a contradiction on `bracket.deflection`'s track record.

## Try breaking it

- Set `material="alu6061"` — watch the deflection claim pass by a wide margin and the
  mass triple.
- Set `arm_length=120` — deflection goes as L³, so it fails by ~8x.
- Set `thickness=2.0` — now `bracket.min_wall` and `bracket.deflection` both trip, and
  `bracket.model_validity` warns that the remaining numbers are not trustworthy.
- Delete a gate's `negative_control` — the registry refuses to load it at all.

<!-- nopekit:begin badge -->
<a href="https://github.com/neuman/nopekit"><img src="https://raw.githubusercontent.com/neuman/nopekit/main/docs/img/made-with-nopekit.png" width="160" alt="Made with nopekit"></a>
<!-- nopekit:end badge -->
