---
name: nopekit
description: Design, validate and fabricate real physical things — hardware, enclosures, PCBs, mechanisms, RC vehicles, solar thermal, chemical processes, printed parts. Use when someone wants to turn a sketch, spec, photo or discussion into something buildable; asks to design, model, simulate, validate, gate, fabricate, 3D print, machine, order boards for, or check the feasibility of a physical object or system; or has an existing CAD/hardware project that needs validation. Also use for "is this design actually going to work", bills of materials, sourcing, manufacturability, tolerance stacks, and readiness reports for physical builds.
---

# nopekit

nopekit takes what a person asks for to a physical thing they can build, and keeps an
honest account of what has been checked and what has not. Its chain is:

```
requirements  ->  claims  ->  evaluators  ->  packs  ->  readiness report
```

A **requirement** is what the person asked for, in their words. A **claim** is what must
be true for the design to work. An **evaluator** settles a claim and has shown it can
fail. A **pack** holds one domain's evaluators and proposers. The **readiness report**
is the claims and their verdicts, rendered. Your job is to keep that chain honest: the
failure to guard against is a design that looks validated and is not.

Read `${CLAUDE_PLUGIN_ROOT}/METHOD.md` before design work: ten rules (it says *gate*
where this skill says *evaluator*; `gates/` is where an evaluator's code lives).

## Make it runnable

The spine is standard library only. From the plugin, prefix every call — each shell call
starts fresh, so an `export` or an `alias` is gone by the next one. Where no host filled
`${CLAUDE_PLUGIN_ROOT}` in (Codex), it is the directory two above this file:

```sh
PYTHONPATH="${CLAUDE_PLUGIN_ROOT}/src" python3 -m nopekit --version
```

Below, `nopekit …` is that form. Confused? `nopekit doctor`. Read commands take `--json`.

## 1. Interview first

Before any model, interview the person the way a product manager would, in their own
words: what it is for, who uses it and where, what it must survive and fit, what it may
cost, and what done looks like. Ask three to six questions at a time, never a form.

Ask for evidence by name, early: a sketch (a napkin is fine), photos of the closest
thing they would buy instead and what it gets right, links to similar things,
measurements of everything it must fit with the tolerance they care about, datasheets or
part numbers already chosen, existing CAD. `nopekit ask` lists what is still missing.
Take each file in with `nopekit ingest`, then `nopekit extract` what was read out of it
— an artifact nobody extracted from is decoration.

Run `nopekit init` (it starts a git repository when there is none, and lists the person
from git's identity in `.nopekit/project.json`'s `people`). Write each thing the person
asked for as `requirements/R1.json`, `R2.json`, … in their words:

```json
{"statement": "It holds the pump without sagging", "source": "interview"}
```

Show the list back and ask for a yes. The person may say skip; then say which
requirements are your guesses. Do not stall: ask for the two most valuable artifacts, go
on, and record what you assumed as assumption claims so it stays visible.

## 2. First light and the live site

A person who has not seen an evaluator go green on their own numbers has no reason to
continue. Write the model (a dataclass `CONFIG` plus `build(config) -> dict`, recorded as
`"model_entry"` in `.nopekit/project.json`), `selftest/known_good.py`, and the two or
three cheapest evaluators, then run `nopekit check`.

Then start the live site as its own process (`--detach` returns at once; never run it as
a background task, which stops within hours and keeps `claude -p` from ending your turn):

```sh
nopekit site init
nopekit site serve --watch --detach   # run again: prints the running URL; --stop stops it
```

**Give the person both URLs it prints in the first few minutes**, a sentence each:
`<url>engineering/` (each claim, its evidence) and `<url>marketing/` (the product, its
story, its choices); both show your work as it moves. Claims come next, beside verdicts.

## 3. Claims and boards

One claim per file, `claims/<id>.json`, with a machine-checkable acceptance, the
mechanism that would settle it, and the requirements it serves:

```json
{"statement": "Tip sags no more than 0.5 mm at rated load", "kind": "measurable",
 "acceptance": {"quantity": "tip deflection", "comparator": "<=", "limit": 0.5, "units": "mm"},
 "rationale": "past ~0.5 mm the droop is visible against a level shelf edge",
 "tags": ["stiffness"],
 "mechanism": {"by": "closed_form",
               "how": "Euler-Bernoulli tip deflection of the arm under the rated load",
               "evaluators": ["bracket.deflection"]}, "requirements": ["R1"]}
```

| Kind | Settled by | `by` |
|---|---|---|
| `measurable` | an automated evaluator, from the model | `closed_form`, `solver`, `datasheet` |
| `physical` | a measurement on a built article, or its authority's judgment | `measurement`, `human` |
| `assumption` | accepted for now, with a reason and an owner who records it | `none`, `human` |

Give each claim the narrowest `tags`: an evaluator covers a claim by its id or a shared
tag. Say the physical ones out loud early: *"No evaluator here can settle C5 — it needs
a real hull in real water. It is pending build until you test it."*

Then draw the map in `groups/<id>.json`, one record each, and run `nopekit site build`.
`{"kind": "board", "title": "Holds the pump", "members": ["C1", "C2"]}`: a board holds
the claims of one thing the person cares about, its text in `site/content/<board>.md`; a
`zone` the choices of one part as the person names it, never all in one; a `station`
titles a shared value as the map shows it (`"Allowed stress"`, not `design_stress`).
`site/content/story.md` is the marketing page's story: what the design is and whom it is
for, the choices behind it and what lost, then its state in the verdict's words. Under
400 words, one to three figures, baked numbers only; `nopekit site stamp story`.

## 4. The loop

Change the design, run `nopekit check`, read the verdict lines. Tier 0 takes seconds.

- **Coverage.** `nopekit packs list` shows what exists; only packs marked `*` are live.
  `nopekit packs add <name>` makes one live, then **run `nopekit packs skills` and read
  what it prints** — each live pack's PACK.md, its lenses and its skills — before you
  work in that domain. `nopekit gate list` is what will actually run.
- **A claim with no evaluator** is a gap: `nopekit gap --propose`, then
  `${CLAUDE_PLUGIN_ROOT}/docs/EXTENSION_PROTOCOL.md`. Offer the cheap analytic evaluator
  now and the heavy solver when the geometry stops moving; say its real cost.
- **Basis.** Use an `established` evaluator (an existing non-AI tool's own answer) when
  one exists, then a `sourced` one, then `model-only`, and say why when you go lower.
- **Proposers.** `nopekit propose <id>` runs one (bare, it lists them) and files what it
  writes as inputs (`ingest --proposer <id>` for one you ran by hand). A proposal settles
  nothing: `extract` what you read; keep or drop each option in a `decide` record.
- **Before you change a number,** run `nopekit why <claim|value|decision>`: what reads
  it, what it rests on, and what lost to it.
- **Decisions keep their losers.** `nopekit decide` with each rejected alternative's
  `--type` and, where you can say it, `--returns-if`.
- **Pictures.** `site build` draws `cfd-openfoam`'s flow pictures only with `pvbatch62`;
  no status needs them. On "from another run", run `nopekit check --force --only <gate>`.
- **The trace.** `round` starts `nopekit model trace` in the background; run it yourself
  after a gate or acceptance edit, before your turn ends. Never edit `.nopekit/trace/`.
- **Review.** Before anything is built, review the design along each live pack's lenses
  (`nopekit packs skills` prints each `lenses.md`) and let the findings move the numbers.

**What only the person does**, typed in their own shell — ask them, and give them the
command; from your shell it records nothing that counts:

```sh
nopekit claim physical C5 pass --evidence photos/c5.jpg --detail "<what was seen>"
nopekit claim physical C6 assume                     # an assumption's owner records it
nopekit claim physical C7 pass --authority "<name>"  # an authority's judgment
nopekit prefer <decision>                            # a preference against an alternative
nopekit export <milestone> --proceed --why "..."     # going ahead over an unresolved claim
```

A physical pass counts only on the article it was tested on; a fail keeps counting.

## 5. Rounds

When your turn ends, the plugin's Stop hook runs `nopekit round --hook`: a tier-0 check,
the site rebuilt, the round record written, and a commit `round N: <what changed>` of
every file in the project that git does not ignore (one over 50 MiB or named like a
secret, such as `.env`, is named instead), only when git's identity is one of `people`.
It never pushes or blocks. Put scratch, downloads and environments in `.gitignore`.
Never commit rounds yourself; change `people` only when asked, to the name and email given.

**Before your turn ends:**

1. Run `nopekit check`, so the page shows the results the round will commit.
2. Read each `site/content/<board>.md`, and `intro.md`, `next.md` and `story.md` when
   they exist, against both pages; rewrite what the results no longer support, in
   ASD-STE100 with only baked numbers. `intro`, `next` and `story` read every claim.
3. Run `nopekit site stamp <board>` for each file you read (`--all` for every one).
4. Leave no record half-written: the round commits what is there.

## Rules that must not bend

**Never call a skipped or errored gate a pass.** A Skipped claim is not fine — even
beside a gate that passed. Say so — and then **clear it**: Skipped is a call to
action, not a resting state, and its reason says which action:

- `skipped: <gate> : requires <tool> (…)` — the tool is missing here. Run
  `nopekit packs show <pack>` for the install recipe, install the tool, and re-run.
  If it is heavy or needs root, say what it costs and ask.
- `skipped: <gate> : <its own words>` with the tool present — the gate skipped itself
  on its input, usually a parameter the model lacks. Add it; installing changes nothing.
- `skipped: <gate> : prerequisite failed: <evaluator>` — a validity guard the gate needs
  failed, so its number would mean nothing here; installing changes nothing. Fix what
  that evaluator failed (its own row says what), and the gate runs again.
- `skipped: <gate> : prerequisite not established: <evaluator> (<why>)` — nothing about
  the gate is wrong: clear that evaluator, by `<why>`. `(skipped)`: its own row says
  `requires <tool>` (install what it names) or that it skipped itself on its input
  (publish the parameter it names). `(unqualified)`: one of its controls or a mutation
  did not hold — `nopekit gate selftest` says which; fix the evaluator or its control,
  never the gate behind it. `(not registered)`: install the pack that provides it, or
  drop the edge, as `nopekit doctor` says. A crashed one reads `errored:`, below.

What you must not do is leave the claim Skipped and move on as though the design were
checked. A claim Skipped by a crash (its reason starts `errored:`, its tag is
`[SKIP ]`) is louder still — the evaluator, or a prerequisite of it, is broken.

**Never move a limit.** A limit is a claim's acceptance condition, in its claim file; an
evaluator reads it with `ctx.acceptance("<id>")`. Move it in the claim, said why in its
`rationale`, never in an evaluator's code. `acceptance condition not met` means the
design misses the claim; `outside operating context` means the evaluator was not
qualified where the design is — bring the input inside, or find one that is. **Amber**
on a Checked claim means "passes if the assumptions hold": a ±10% move of a choice an
unconfirmed assumption sources flips it. It moves no status; the owner's `assume` or a
physical claim that `resolves` the assumption clears it, never a number moved.

**Never record a person's act**: a physical result, an owner, a judgment, a preference,
going ahead. Never write or edit `results/`, `preferences/` or `exports/`; they are
sealed, and a hand edit is refused on every command.

**Every evaluator must be able to fail.** It declares a negative control and passes its
known-good one (`selftest/known_good.py`, written before your first evaluator); until
`nopekit gate selftest` shows both, its claim reads Gap.

**Every claim names its mechanism**: `by`, `how` in words, and the evaluators you expect.

**Never hand-edit a generated file**: `site/data/`, `.nopekit/ledger.json`,
`.nopekit/round.json`, `REPORT.md`. If an output is wrong, fix its source and regenerate.

**Never write a number without its reason and what lost** — in the model's docstring and
`PARAMS`, or a decision's rejected alternatives.

**Say ready only when `nopekit export <milestone> --dry-run` says `would write`.** Checked
does not mean true: it means every evaluator passed on the current inputs.
**Never simulate a physical claim.** No CFD run makes a printed seam watertight.

## Commands
```
nopekit init                    # the project layout, a git repository, the person in people
nopekit status                  # where the project is, in one screen
nopekit check                   # run the tier-0 evaluators; --tier 2 before any spend
nopekit round                   # end a round by hand; --commit commits it, --hook is the Stop hook's
nopekit ask                     # what evidence to ask the person for
nopekit ingest <files> --kind sketch --desc '...'   # take a file in as evidence
nopekit propose                 # a pack's proposers; run one and file what it wrote
nopekit inputs                  # the evidence, and --unextracted what nobody read yet
nopekit extract <id> --what '...' --grounds <param>   # what was read out of an input
nopekit claim list              # every claim and where its evidence bottoms out
nopekit gap --propose           # every claim whose mechanism has yet to run, and packs to run it
nopekit gate list               # what will actually run; gate show <id> for one
nopekit gate selftest           # each evaluator's controls: can it fail?
nopekit report --write          # REPORT.md, the readiness report
nopekit export <milestone> --dry-run   # re-runs everything a spend requires
nopekit why <name>              # one value's, claim's, requirement's or decision's history
nopekit decide --title '...'    # a decision, with what LOST and why
nopekit prefer <decision>       # the PERSON's: a preference against an alternative
nopekit packs skills            # the live packs' PACK.md, lenses and skills: read them
nopekit model [trace]           # the model as nopekit sees it; trace: what each choice moves
nopekit site serve --watch --detach   # the live site, its own process; --stop stops it
nopekit doctor                  # what is wrong, and what to do; writes nothing
nopekit validate <repo> <ref> <old> <new>   # what an Eddy server will say of a push; with one, `curl <its address>/agent.md` and follow it
```

## Reference

- `${CLAUDE_PLUGIN_ROOT}/METHOD.md` — the ten rules. Read this.
- `${CLAUDE_PLUGIN_ROOT}/docs/EXTENSION_PROTOCOL.md` — when a claim has no evaluator.
- `${CLAUDE_PLUGIN_ROOT}/docs/PACK_FORMAT.md` — the pack contract.
- `${CLAUDE_PLUGIN_ROOT}/docs/SITE_CONTRACT.md` — the site: groups, text, `state.json`.
