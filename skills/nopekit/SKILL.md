---
name: nopekit
description: Design, validate and fabricate real physical things — hardware, enclosures, PCBs, mechanisms, RC vehicles, solar thermal, chemical processes, printed parts. Use when someone wants to turn a sketch, spec, photo or discussion into something buildable; asks to design, model, simulate, validate, gate, fabricate, 3D print, machine, order boards for, or check the feasibility of a physical object or system; or has an existing CAD/hardware project that needs validation. Also use for "is this design actually going to work", bills of materials, sourcing, manufacturability, tolerance stacks, and readiness reports for physical builds.
---

# nopekit

Take a sketch or a discussion to a physical thing you can actually build, with an
honest account of what has been verified and what has not.

**Read `METHOD.md` at the repo root before doing design work.** It is ten rules and
it is the whole system. Everything below is how to apply it.

## First: make `nopekit` runnable

The spine is **standard library only** — no dependencies, nothing to build. Get a
working command with the first of these that succeeds:

```sh
nopekit --version                                    # already on PATH?
python3 -m nopekit --version                         # already importable?
PYTHONPATH="$CLAUDE_PLUGIN_ROOT/src" python3 -m nopekit --version    # plugin install
pip install --user git+https://github.com/neuman/nopekit            # anything else
```

If you are running from the plugin, export it once for the session rather than
prefixing every call:

```sh
export PYTHONPATH="$CLAUDE_PLUGIN_ROOT/src:$PYTHONPATH"
alias nopekit='python3 -m nopekit'
```

`nopekit doctor` is the first thing to run whenever something is confusing — it
checks the environment, the model, determinism, the records and their index, pack
discovery, per-gate tool availability, and what the verdict cache cannot see, and
says which of those is wrong. It never writes anything.

Every read command takes `--json`. Prefer it when you are parsing rather than
reading: `nopekit check --json` is a few KB where the human output is prose.

## The project is files

**Read the whole project in two reads.** `.nopekit/ledger.json` is every record —
the claims, parameters' provenance, decisions, needs, inputs, physical results, views
and the project's meta — in one generated file. `.nopekit/cache/last_check.json` is
what the last full `nopekit check` concluded: every claim's status, the counts, the
worst blocking claim with the gate and the words that explain it, and every
parameter as the model states it, with what lost. Read those two before you open
anything else. Both are outputs, rebuilt by the commands: never edit either, and
never treat them as newer than the last command — `nopekit status` says what is
stale now.

**Change a record by editing its file**, then run `nopekit check`:

| File | Holds |
|---|---|
| `claims/<id>.json` | one claim — the file name is its id |
| `decisions/<slug>.json` | one decision, including what LOST |
| `inputs/<id>.json` | one piece of evidence and what was extracted from it (its bytes stay in `inputs/<bucket>/`) |
| `params/<name>.json` | only the provenance the model cannot hold: `source`, `grounded_by`, `tags` |
| `needs/<id>.json` | a gap someone enriched (candidates, a chosen tool) |
| `milestones/<name>.json` | one spend — a print, a board order, a field test: the claim ids it `requires` and the `generator` (`<path>.py:<function>`) that builds what it gets |
| `.nopekit/project.json` | the project: `"model_entry"` — the model file that is the single source of truth — and `"packs"`, the live packs |

Every record file is read strictly: a misspelled key is refused, naming the file,
the key and the suggestion, instead of being dropped. A parameter's value, units and
rationale live in the model (its `Config` field and that field's docstring), and the
alternatives that lost in the model's `PARAMS` — never copy them into `params/`.

Six commands write one record for you, with the time stamped for you:
`nopekit ingest` (copies the file in and pins its hash), `nopekit extract`,
`nopekit decide`, `nopekit packs add`, `nopekit claim physical` and
`nopekit export <milestone>` (appends to `exports/<milestone>.json`; never edit it —
it is sealed, like `results/`). `check` writes
no record — only verdicts under `.nopekit/verdicts/`, which you commit with the
records, and ignored scratch (the one exception: a project still in the old one-file
layout is migrated to record files by its first `check`).

## The shape

```
claims  ->  gates  ->  packs  ->  readiness report
```

A **claim** is something that must be true for the design to work. A **gate** is an
executable that settles a claim *and is capable of failing*. A **pack** supplies
gates for one physical domain. The **readiness report** is the claims and their
verdicts, rendered.

Your job is to keep that chain honest. The failure mode you are guarding against is
not "the design is wrong" — it is "the design looks validated and is not".

## Starting a project

### 1. Intake is evidence, not just conversation

**This is the step agents skip, and it is the one that decides the outcome.** A
design conversation is the thinnest input a project has. Ask for artifacts, by name,
early, and keep asking as the design sharpens.

Run `nopekit ask` for the prioritised list of what is still missing. Ask for:

- **A sketch.** Napkin, whiteboard photo, a rectangle with arrows. A bad sketch pins
  down intent that a paragraph cannot.
- **A photo of the closest existing product** they'd buy instead, even one they
  hate — and what it gets right, and the thing it does that they refuse to copy.
- **Anything they've taken apart** that solves part of this, photographed inside.
- **Calipers** on everything this must fit, mate with, or sit inside — with the
  tolerance they actually care about.
- **Datasheets or vendor part numbers** for parts already chosen. *A part you cannot
  order is a part you do not have.*
- **Existing CAD** in any format, even wrong or abandoned.
- **Screenshots** of the tool they were using, the vendor page, the spec they were
  reading.
- **The standard or code** it has to meet, if any.

Then `nopekit ingest <files>` and — this is the half people drop —
`nopekit extract` each one. An artifact nobody extracted from is decoration.
Every extraction records *what was read out of it* and *which parameters and claims
that grounds*. `nopekit inputs --unextracted` lists what arrived and was never read.

Do not stall the project waiting for evidence. Ask for the two highest-value
artifacts, proceed on stated assumptions, and record those assumptions as
ASSUMPTION claims so they stay visible.

### 2. Extract the claims

Turn the brief into claims that must be true, each with a **machine-checkable
acceptance**. "Strong enough" is not a claim. `≤0.5 mm tip deflection at 3 N` is.
Write each one as its own file, `claims/<id>.json`:

```json
{"statement": "Tip sags no more than 0.5 mm at rated load", "kind": "measurable",
 "acceptance": {"quantity": "tip deflection", "comparator": "<=", "limit": 0.5, "units": "mm"},
 "rationale": "past ~0.5 mm the droop is visible against a level shelf edge",
 "tags": ["stiffness"]}
```

Give each the narrowest `tags` that describe what it asserts: a gate covers a claim
by its id or by a shared tag, so a broad tag drags in gates that measure something
else.

Classify each one honestly:

| Kind | Meaning | Where its evidence bottoms out |
|---|---|---|
| `measurable` | An automated evaluator settles it from the model — the only kind one settles. | *automated* |
| `physical` | An article, or its authority's judgment, settles it. Watertightness, feel, RF range, taste. | *measurement*, or *expert judgment: <authority>* |
| `assumption` | Accepted provisionally, with a reason and an owner. Recorded so it stays visible. | *assumption*, or *expert judgment: <authority>* |

`claim list` prints that last column. A claim may declare it with `"terminal"` in its
file — `measurement` or `human` for a physical claim, `none` or `human` for an
assumption — and a declaration never lowers the bar: the reader refuses a pairing
outside those, and a measurable claim declared `closed_form`, `solver` or `datasheet`
reads exactly as an undeclared one.

Say the physical ones out loud early: *"No evaluator here can settle C5 — it needs a
real hull in real water. It is pending build until you test it."* This builds trust
and sets expectations correctly. A physical claim needs a test written down — an
acceptance condition, or a `note` saying what to do — before any pass can count.

**The person who tested it records the result, in their own shell — never you.**
Ask them to run `nopekit claim physical C5 pass --evidence photos/c5.jpg --detail
"<what was seen>"` (or `fail`); the command shows what it will record, and they type
the claim's id to confirm it. Who recorded it is read from git's identity; there is no
`--who` and no `--when` (both are refused — say when it was observed in `--detail`).
A pass on a physical claim needs at least one `--evidence` file under the project.
From your own shell — your Bash tool, or a `!`-prefixed command inside Claude Code,
which runs in your environment — a pass is recorded and **counts for nothing**: say
so, and ask the person to record it themselves. A fail counts wherever it is
recorded. A pass counts only on the article it was recorded on — the design as it
was then — so when the design moves, the claim reads **Stale** and `check` and
`status` name the article to rebuild (`rebuild: article <hash> (C5) — …`): a check run
cannot restore it, a new article can. A fail keeps counting through every later pass
and every edit — renaming or deleting the claim's file included: a result is sealed to
its claim's id, and a fail whose claim file is gone still stops `check`. `--measured
<value>` records the value in the claim's units, and decides pass or fail where the
claim has a limit. A result is recordable on an automated claim too: a fail there,
beside an evaluator that passed, is a **contradiction** on that evaluator's track
record (`gate show`); a pass there settles nothing.

**An assumption needs an owner who records it.** Name the owner in the claim file
(`"owner": "<name>"`, as git names them), and the owner runs `nopekit claim physical
C6 assume` in their own shell. An owner you write into the file counts for nothing on
its own: until the owner records it, C6 reads Gap, and `check` exits 1 for it if it is
required. Say it as it is: *"C6 is a gap until Dana records its owner: ask Dana to run
`nopekit claim physical C6 assume`."* Editing the owner or the rationale after it was
recorded un-records it. Never edit an owner in to clear a gap, and never make a claim
not required to get `check` to pass — whether it is required is the user's call.

**Some claims end in a person's judgment** — "meets the venue's fire-safety rules". Write
`"terminal": "human"` and `"authority": "<name>"` into its file. It reads Gap until
that person records it with `nopekit claim physical <id> assume --authority
"<name>"`, Assumed under their name until they judge it with `nopekit claim physical
<id> pass --authority "<name>"` (or `fail`), each in their own shell, as themselves.
Only the authority settles it; you never record it, and a physical claim judged this
way still needs its test written down and its evidence. Editing the claim after it was
judged un-records the judgment: it reads Gap again until the authority records the claim
as it now reads. `results/<id>.json` records each person's git name and email beside
what they recorded.

### 3. Reach first light fast

A user who has not seen a gate go green on their own numbers has no reason to
continue. Scaffold the model, write the two or three cheapest analytic gates, and
run `nopekit check` within the first few minutes. Four verdict lines against their
real numbers is the hook.

Do not build the whole validation suite before showing anything.

## The working loop

**A discoverable pack contributes nothing until you add it.** `nopekit packs list`
shows every pack it can see; only the ones marked `*` are live in this project. Reading
a pack's `PACK.md` does not install it. Check `nopekit gate list` before you believe
you have coverage — a tester spent twenty minutes designing against a gate set that was
empty, which is this tool's own headline failure reproduced one level up.

```
nopekit packs list            # what exists; * = live in this project
nopekit packs add <name>...   # make them live
nopekit gate list             # what will ACTUALLY run — check this early
nopekit status                # where is this project
nopekit check                 # tier-0 gates, seconds — run this constantly
nopekit check --tier 2        # the full sweep, before any spend decision
nopekit gap --propose         # claims with no gate, and packs that might cover them
nopekit why <param|claim>     # one thing's full history, instead of the whole log
nopekit report --write        # REPORT.md, the readiness report — an ignored output
nopekit export <m> --dry-run  # ready for milestone <m>? re-runs everything it requires
nopekit site build            # rebuild the page after a sweep
nopekit decide --title ...    # record a decision, including what LOST
```

Change a parameter, run `nopekit check`, read the verdict lines. That is the loop.
It must stay fast — if tier 0 is not seconds, something is miscategorised.

## The project site: how the human sees what you are doing

```sh
nopekit site init      # once, early
nopekit site build     # after every check sweep
nopekit site serve     # python3 -m http.server; no build step, no npm
```

**Build it early, not as a finishing touch.** You are working in a text channel on a
physical object, and a page with the claims, the verdicts and the geometry on it is
how the human sees what you have done without reading a transcript. The cost is one
command.

**Rebuild it after a check sweep.** `site build` does not run gates — it renders the
verdicts already in the verdict cache and marks how old each one is — so a page you
forgot to rebuild shows the last sweep, honestly labelled but not the one you just
ran.

**Use it to explain a failure instead of describing coordinates in prose.** When a
gate fails somewhere specific, attach `Locator`s to the verdict and say *"open the
site, the clash view, back_left is lit"*. Three sentences of coordinates are a
worse answer than a link to the thing, and you will be wrong about one of the
numbers. Attach a locator only where you genuinely know the position: a confident
highlight on the wrong part sends someone to inspect a part that is fine, and after
that they stop trusting the overlay.

`nopekit site status` lists **dangling locators** — verdicts pointing at a view or
a part name that no view publishes. Check it after renaming anything in the model.
A gate that thinks it is highlighting something and is not looks exactly like a gate
that found nothing, from both ends.

The site never computes truth; it renders the records and the verdict cache.
Everything on the page is in `site/data/state.json`, so read that rather than the
HTML when you want the state back. A project with no geometry still gets a useful
site — claims, verdicts, evidence, provenance, readiness. Contract:
`docs/SITE_CONTRACT.md`.

## Rules you will be tempted to break

**Never hand-edit a generated file.** If an output is wrong, the model is wrong.
Fix it there and regenerate. A repo with hand-patched outputs has no source of truth.

**Never write a number without its reason.** Record why this value, what was tried
and rejected, and what measurement or datasheet it came from. The rejected
alternatives are the part that pays: without them, every fresh context window
re-litigates every settled number.

**Never let a validator merely log.** It must refuse. And every gate declares a
negative control — a known-bad input it must fail on. The registry will not accept
one without it. And it must pass a known-good one: a gate is *qualified* only when it
passes its known-good control (a pack's `selftest/baseline.json`, a project's
`selftest/known_good.py`) and fails its known-bad one — and, for a gate of your own,
fails every conclusive mutation of the known-good design. Until then its claim reads
Gap: `unqualified: <gate> : <what does not hold>`. Write `selftest/known_good.py`
before your first gate. Run `nopekit gate selftest` and believe the result.

**Never call a skipped or errored gate a pass.** A Skipped claim is not fine — even
beside a gate that passed. Say so — and then **clear it**: Skipped is a call to
action, not a resting state, and its reason says which action:

- `skipped: <gate> : requires <tool> (…)` — the tool is missing here. Run
  `nopekit packs show <pack>` for the install recipe, install the tool, and re-run.
  If it is heavy or needs root, say what it costs and ask.
- `skipped: <gate> : <its own words>` with the tool present — the gate skipped itself
  on its input, usually a parameter the model does not publish. Add it to the model;
  installing changes nothing.
- `skipped: <gate> : prerequisite failed: <evaluator>` — the gate was not run because
  a validity guard that is its prerequisite failed: its number would not mean anything
  here. It is not a missing tool: installing changes nothing. Fix what that evaluator
  failed (its own row says what), and the gate runs again.
- `skipped: <gate> : prerequisite not established: <evaluator> (<why>)` — the gate was
  not run, and nothing about the gate itself is wrong: clear that evaluator, by `<why>`.
  `(skipped)` — read the evaluator's own row: if it says `requires <tool>`, install
  what it names; otherwise it skipped itself on its input, and the fix is to publish the
  parameter it names — installing changes nothing. `(unqualified)` — the evaluator is
  not qualified at its version (one of its controls did not hold, or a mutation of its
  known-good control passed): fix the evaluator or its control — `nopekit gate
  selftest` says which — never the gate behind it.
  `(not registered)` — no evaluator has that id here: install the pack that provides
  it, or drop the edge, as `nopekit doctor` says. A prerequisite that crashed reads
  `errored:`, below.

What you must not do is leave the claim Skipped and move on as though the design were
checked. A claim Skipped by a crash (its reason starts `errored:`, its tag is
`[SKIP ]`) is louder still — the evaluator, or a prerequisite of it, is broken.

**Never move a limit in a gate.** A limit is a claim's acceptance condition, in
`claims/<id>.json`; a gate reads it with `ctx.acceptance("<id>")` and reports it as
its limit. Move the limit in the claim — the gates that read it re-run, nothing else
does — and never edit a gate's number to make a claim pass. Two readings follow from it:

- `acceptance condition not met: <gate> : <value> against <claim>'s <condition> (its
  own limit <l>)` — Failing. The evaluator passed against a limit of its own, and its
  value misses the claim's condition. The design misses the claim: change the design,
  or — if the claim's number is wrong — the claim, said why in its `rationale`. A
  `warning: <gate> : its limit … is not <claim>'s acceptance condition` line from
  `check` is the same number in two places, the claim's and the evaluator's; it moves
  no status, and it means one of the two is stale.
- `outside operating context: <gate> : <key> = <v>, qualified on [a, b]` — Gap. The
  evaluator was qualified on that range of its inputs, and this design is outside it:
  its pass counts nothing here (a fail outside would). Bring the input inside, find an
  evaluator qualified where the design is, or — the human's call, never yours —
  carry the claim on an owned fallback (`owner` and `fallback` in its claim file,
  and the owner runs `nopekit claim physical <id> assume` in their own shell).

**Never simulate a physical claim.** No CFD run makes a printed seam watertight.

## When a claim has no gate

That is a capability gap, and it is how the system grows. **Do not guess at a tool.**
Read `docs/EXTENSION_PROTOCOL.md` and follow it: name the quantity precisely,
classify it, try the analytic answer first, choose the standard open tool and record
why, install pinned with a smoke test, wrap it as a gate **with a negative control
you actually ran**, record every solver setting as a constant, and emit a pack.

Offer the cheap analytic gate now and defer the heavy solver until the geometry stops
moving. Say the real cost out loud — install size, run time — and let the human
choose. Running CFD on a hull that is still being redrawn is a way of feeling
productive.

## Before an irreversible spend

Ordering boards, buying stock, booking machine time, committing to a mould: that is a
**milestone**. Declare it — `milestones/<name>.json` with the claim ids the spend
requires and the generator that builds its files — and ask:

```sh
nopekit export <name> --dry-run   # re-runs every evaluator it requires, controls included
```

`status` and the report say what each milestone is missing *as last evaluated*, from
the verdict cache, which the inner loop never re-executes. `export` is where the cache
is not trusted: it re-runs what the milestone requires at the top tier and refuses when
any required claim is not Checked, or when the re-run disagrees with the records. Say
*ready* only when it says `would write` — and when the person runs `nopekit export
<name>`, the package is `out/<name>/`, with the article hash its results bind to
(`nopekit claim physical <id> --article <hash>`) and a test card of what to measure.

Going ahead over an unresolved required claim is the person's decision, never yours:
`nopekit export <name> --proceed --why "…"`, typed in their own shell. From an agent
session it is refused; tell them the command and why.

`nopekit check` exits non-zero while any required claim is Failing, Skipped, a Gap,
Open or Stale. Pending build and Assumed do not stop it, and they are still unresolved.

Then say the honest sentence out loud, in the shape the report uses:

> *Fab-ready: routed, DRC-clean, fab package exported, mechanicals fit-checked.
> Pending build: 2 claims need an article.*

The second sentence is why anyone believes the first. And "checked" does not mean
true: it means every evaluator passed on the current inputs.

## Adversarial review before building

Run N independent reviews along **different** dimensions — structure,
manufacturability, sourcing, thermal, usage, cost, safety — each trying to break the
design on its own terms, and let the findings change the numbers **before** anything
is generated. Packs ship their domain's lens list; `nopekit packs show <name>` has
them. The most expensive mistakes are the ones that get built.

## Adopting an existing project

`nopekit init` in the repo, then set `"model_entry"` in `.nopekit/project.json` to
the model file that already exists. Infer claims from what the code already asserts,
ask about the rest, and write gates around the existing behaviour before changing
anything. Most people are not starting from zero.

## Reference

- `METHOD.md` — the ten rules. Read this.
- `docs/EXTENSION_PROTOCOL.md` — growing a new validation capability.
- `docs/PACK_FORMAT.md` — the pack contract.
- `docs/SITE_CONTRACT.md` — the project site: view kinds, locators, `state.json`.
- `nopekit packs list` — what is installed; `nopekit packs show <name>` for its
  PACK.md (tier 2). Load a pack's `references/` only for the specific task at hand.
- `nopekit doctor` — run this first when something is confusing.
