---
name: atompipe
description: Design, validate and fabricate real physical things — hardware, enclosures, PCBs, mechanisms, RC vehicles, solar thermal, chemical processes, printed parts. Use when someone wants to turn a sketch, spec, photo or discussion into something buildable; asks to design, model, simulate, validate, gate, fabricate, 3D print, machine, order boards for, or check the feasibility of a physical object or system; or has an existing CAD/hardware project that needs validation. Also use for "is this design actually going to work", bills of materials, sourcing, manufacturability, tolerance stacks, and readiness reports for physical builds.
---

# atompipe

Take a sketch or a discussion to a physical thing you can actually build, with an
honest account of what has been verified and what has not.

**Read `METHOD.md` at the repo root before doing design work.** It is ten rules and
it is the whole system. Everything below is how to apply it.

## First: make `atompipe` runnable

The spine is **standard library only** — no dependencies, nothing to build. Get a
working command with the first of these that succeeds:

```sh
atompipe --version                                    # already on PATH?
python3 -m atompipe --version                         # already importable?
PYTHONPATH="$CLAUDE_PLUGIN_ROOT/src" python3 -m atompipe --version    # plugin install
pip install --user git+https://github.com/neuman/atompipe            # anything else
```

If you are running from the plugin, export it once for the session rather than
prefixing every call:

```sh
export PYTHONPATH="$CLAUDE_PLUGIN_ROOT/src:$PYTHONPATH"
alias atompipe='python3 -m atompipe'
```

`atompipe doctor` is the first thing to run whenever something is confusing — it
checks the environment, the model, determinism, the records and their index, pack
discovery, per-gate tool availability, and what the verdict cache cannot see, and
says which of those is wrong. It never writes anything.

Every read command takes `--json`. Prefer it when you are parsing rather than
reading: `atompipe check --json` is a few KB where the human output is prose.

## The project is files

**Read the whole project in two reads.** `.atompipe/ledger.json` is every record —
the claims, parameters' provenance, decisions, needs, inputs, physical results, views
and the project's meta — in one generated file. `.atompipe/cache/last_check.json` is
what the last full `atompipe check` concluded: every claim's status, the counts, the
worst blocking claim with the gate and the words that explain it, and every
parameter as the model states it, with what lost. Read those two before you open
anything else. Both are outputs, rebuilt by the commands: never edit either, and
never treat them as newer than the last command — `atompipe status` says what is
stale now.

**Change a record by editing its file**, then run `atompipe check`:

| File | Holds |
|---|---|
| `claims/<id>.json` | one claim — the file name is its id |
| `decisions/<slug>.json` | one decision, including what LOST |
| `inputs/<id>.json` | one piece of evidence and what was extracted from it (its bytes stay in `inputs/<bucket>/`) |
| `params/<name>.json` | only the provenance the model cannot hold: `source`, `grounded_by`, `tags` |
| `needs/<id>.json` | a gap someone enriched (candidates, a chosen tool) |
| `.atompipe/project.json` | the project: `"model_entry"` — the model file that is the single source of truth — and `"packs"`, the live packs |

Every record file is read strictly: a misspelled key is refused, naming the file,
the key and the suggestion, instead of being dropped. A parameter's value, units and
rationale live in the model (its `Config` field and that field's docstring), and the
alternatives that lost in the model's `PARAMS` — never copy them into `params/`.

Five commands write one record for you, with the time stamped for you:
`atompipe ingest` (copies the file in and pins its hash), `atompipe extract`,
`atompipe decide`, `atompipe packs add` and `atompipe claim physical`. `check` writes
no record — only verdicts under `.atompipe/verdicts/`, which you commit with the
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

Run `atompipe ask` for the prioritised list of what is still missing. Ask for:

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

Then `atompipe ingest <files>` and — this is the half people drop —
`atompipe extract` each one. An artifact nobody extracted from is decoration.
Every extraction records *what was read out of it* and *which parameters and claims
that grounds*. `atompipe inputs --unextracted` lists what arrived and was never read.

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

| Kind | Meaning |
|---|---|
| `measurable` | An automated evaluator settles it from the model — the only kind one settles. |
| `physical` | Only an article settles it. Watertightness, feel, RF range, taste. |
| `assumption` | Accepted provisionally, with a reason and an owner. Recorded so it stays visible. |

Say the physical ones out loud early: *"No evaluator here can settle C5 — it needs a
real hull in real water. It is pending build until you test it."* This builds trust
and sets expectations correctly. When the test happens, record what was observed:
`atompipe claim physical C5 pass --who <name> --detail "<what was seen>"`. Until
article binding lands, a recorded pass still reads **Pending build** (its reason: "a
pass recorded by <name>, not bound to an article") and never makes the project
*ready*: nothing yet ties it to an article built from the current design. A recorded
**fail** keeps counting: no later pass, and no edit of the claim's kind, takes it
back.

**An assumption needs an owner who records it — and no command can record one yet.**
Writing `"owner": "<name>"` into `claims/<id>.json` does not make it Assumed: an owner
counts only through the signing channel, which has not landed, and a hand or agent
edit reads unattributed. So until it lands every assumption reads Gap, and `check`
exits 1 for each one that is required. Say it as it is: *"C6 is a gap, and nothing
here can record its owner yet: `check` exits 1 on it until the signing channel
lands."* Never edit the owner in to clear a gap, and never make a claim not required
to get `check` to pass — whether it is required is the user's call.

### 3. Reach first light fast

A user who has not seen a gate go green on their own numbers has no reason to
continue. Scaffold the model, write the two or three cheapest analytic gates, and
run `atompipe check` within the first few minutes. Four verdict lines against their
real numbers is the hook.

Do not build the whole validation suite before showing anything.

## The working loop

**A discoverable pack contributes nothing until you add it.** `atompipe packs list`
shows every pack it can see; only the ones marked `*` are live in this project. Reading
a pack's `PACK.md` does not install it. Check `atompipe gate list` before you believe
you have coverage — a tester spent twenty minutes designing against a gate set that was
empty, which is this tool's own headline failure reproduced one level up.

```
atompipe packs list            # what exists; * = live in this project
atompipe packs add <name>...   # make them live
atompipe gate list             # what will ACTUALLY run — check this early
atompipe status                # where is this project
atompipe check                 # tier-0 gates, seconds — run this constantly
atompipe check --tier 2        # the full sweep, before any spend decision
atompipe gap --propose         # claims with no gate, and packs that might cover them
atompipe why <param|claim>     # one thing's full history, instead of the whole log
atompipe report --write        # the readiness report
atompipe site build            # rebuild the page after a sweep
atompipe decide --title ...    # record a decision, including what LOST
```

Change a parameter, run `atompipe check`, read the verdict lines. That is the loop.
It must stay fast — if tier 0 is not seconds, something is miscategorised.

## The project site: how the human sees what you are doing

```sh
atompipe site init      # once, early
atompipe site build     # after every check sweep
atompipe site serve     # python3 -m http.server; no build step, no npm
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

`atompipe site status` lists **dangling locators** — verdicts pointing at a view or
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
one without it. Run `atompipe gate selftest` and believe the result.

**Never call a skipped or errored gate a pass.** A Skipped claim is not fine — even
beside a gate that passed. Say so — and then **clear it**: Skipped is a call to
action, not a resting state, and its reason says which action:

- `skipped: <gate> : requires <tool> (…)` — the tool is missing here. Run
  `atompipe packs show <pack>` for the install recipe, install the tool, and re-run.
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
  not qualified at its version (its known-bad control did not fail): fix the evaluator
  or its control — `atompipe gate selftest` says which — never the gate behind it.
  `(not registered)` — no evaluator has that id here: install the pack that provides
  it, or drop the edge, as `atompipe doctor` says. A prerequisite that crashed reads
  `errored:`, below.

What you must not do is leave the claim Skipped and move on as though the design were
checked. A claim Skipped by a crash (its reason starts `errored:`, its tag is
`[SKIP ]`) is louder still — the evaluator, or a prerequisite of it, is broken.

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

Ordering boards, buying stock, booking machine time, committing to a mould: run the
full sweep and read `atompipe report`. `atompipe check` exits non-zero while any
required claim is Failing, Skipped, a Gap, Open or Stale. Pending build and Assumed do
not stop it, and they are still unresolved: say *ready* only when the report's first
sentence does — every required claim checked against the current inputs.

Then say the honest sentence out loud, in the shape the report uses:

> *Fab-ready: routed, DRC-clean, fab package exported, mechanicals fit-checked.
> Pending build: 2 claims need an article.*

The second sentence is why anyone believes the first. And "checked" does not mean
true: it means every evaluator passed on the current inputs.

## Adversarial review before building

Run N independent reviews along **different** dimensions — structure,
manufacturability, sourcing, thermal, usage, cost, safety — each trying to break the
design on its own terms, and let the findings change the numbers **before** anything
is generated. Packs ship their domain's lens list; `atompipe packs show <name>` has
them. The most expensive mistakes are the ones that get built.

## Adopting an existing project

`atompipe init` in the repo, then set `"model_entry"` in `.atompipe/project.json` to
the model file that already exists. Infer claims from what the code already asserts,
ask about the rest, and write gates around the existing behaviour before changing
anything. Most people are not starting from zero.

## Reference

- `METHOD.md` — the ten rules. Read this.
- `docs/EXTENSION_PROTOCOL.md` — growing a new validation capability.
- `docs/PACK_FORMAT.md` — the pack contract.
- `docs/SITE_CONTRACT.md` — the project site: view kinds, locators, `state.json`.
- `atompipe packs list` — what is installed; `atompipe packs show <name>` for its
  PACK.md (tier 2). Load a pack's `references/` only for the specific task at hand.
- `atompipe doctor` — run this first when something is confusing.
