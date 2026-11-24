# nopekit: the architecture

Decided 2026-10-14, from an interview between the author and the agent, then
corrected the same day against the code (nine readers mapped every decision onto
`src/`, and the author answered what the mapping raised). This is the only plan. It
replaces `docs/PLAN.md`, `docs/PLAN-v0.14.md` and `docs/plan/`, retired at commit
`a00d0e5`; ids like `D-14`, `S-60`, `R-14` and `P2.5b` in code comments and contract
documents refer to that plan and resolve against git history at that commit.

Read it top to bottom once. Every decision has its reason beside it; a decision
without its reason should not survive a fresh context window. Section 8 lists the
details the agent decided during implementation, so the author can overturn any of
them in one place.

## 0. Settled by the author (2026-10-14)

1. **"Lenses" keeps its meaning.** The pack field `lenses` names the adversarial
   review prompts the agent uses to find claims it missed. The author's earlier
   "lenses" (a parts finder, a prior-art search, a generator) are **proposers**
   (decision 25). No rename.
2. **Return conditions are structured, with a text fallback.**
   `{"claim": "C1", "quantity": "margin" | "value" | "limit", "comparator": ">",
   "value": 0.02}` is evaluated by `check`. `{"text": "..."}` is shown and never
   evaluated, and reads as not machine-checkable.
3. **Round commits use an allow list.** The project's metadata lists its people.
   `nopekit round` commits only when git's configured identity is one of them;
   otherwise it warns and leaves the round uncommitted.
4. **Every proposer and evaluator declares its basis**: `established`, `sourced` or
   `model-only` (decision 38). The basis is shown; it changes no status and no
   qualification.
5. **The round record is written by `round` and `merge`, never by `check`**
   (decision 18). `check` keeps invariant 8 exactly as written. The live map still
   updates after every check, because `site serve --watch` rebuilds from the live
   resolution.
6. **Derivation is observed by perturbation** (decision 39): rebuild the model with
   each choice nudged, record what moved. Cached by the instructions' digest, run in
   the background at round end. It also gives amber: a pass that a ±10% error in an
   unconfirmed input would flip.
7. **Layout: the semantic work first.** Records move under `design/` (and
   `.nopekit/project.json` to `nopekit.json`) last, if budget remains. Project
   evaluators stay in `gates/` and `selftest/`, qualified against the project's own
   model; promoting one to a pack means writing a sealed baseline, not a move.
8. **METHOD.md may change in three places**: rule 9 (the representation comes first;
   the verdict sits in the map section right under it), section 0's chain
   (requirements come first; packs hold proposers and evaluators), and rule 1 (one
   set of instructions, whatever its layers, in place of one parametric model).
9. **People see the word "evaluator"** for anything that can settle a claim, on
   every human channel (CLI, report, site). "Proposer" and "falsifier" name the two
   kinds of pack contents in docs only; "gate" stays a code word (`GateSpec`,
   `gates/`).

Still open, not blocking: an electrical pack does not exist and the plugin
description promises PCBs; pack pins (decision 26) are deferred; records edited on
both sides of a candidate branch merge the ordinary git way.

## 1. What nopekit is

A system of record for a design's claims, the evaluators that have tested them, and
the history of both; plus packs that supply proposers and evaluators per domain;
plus renderers over the record. Five intentions, which are five interfaces to one
object:

1. Teach an agent the grounding-by-falsification method.
2. Give it a library of proposers and evaluators, extensible from other
   repositories, preferring established tools over LLM-built ones.
3. Interview the person, then iterate with them.
4. Keep the grounded design state in a form that reconstructs the same analysis and
   the same visualisation every time.
5. Build and keep a site that explains the state to the designer and to a newcomer.

The object is the grounded design state (paper §8): S = (T, F, L), the tradespace,
the falsification graph and the claim ledger. Everything else reads it or writes one
part of it.

## 2. Terms

| Term | Meaning |
|---|---|
| **choices** | the values a person or the agent picked: `spoke_count`, `arm_w`. In python, the model's `Config` fields. A value outside `Config` (a module table, a part number) is part of the instructions |
| **instructions** | how choices become the thing: the python, a parametric CAD file, a prompt, a Modelica model. Derived values (`duct_r_in = prop_r + tip_gap`) come from here |
| **outputs** | what the instructions produce: the mesh, the BOM, the build guide |
| **the design** | choices and instructions together. nopekit observes both; it owns neither |
| **record** | one fact in one file, read strictly, written by the agent or by a command |
| **verdict** | one evaluator's outcome on one set of inputs, keyed by the hash of what it read (ρ). Immutable |
| **resolution** | what the verdicts justify saying now: every claim's status, composed by the resolver. Computed live by every reader |
| **round record** | `.nopekit/round.json`: the resolution frozen at the end of a round, with the typed graph. Tracked. Written by `round` and `merge` only |
| **trace** | the observed derivation: which derived values move when each choice moves, and by how much. `.nopekit/trace/`, keyed by the instructions' digest |
| **grounded design state** | the checkout: records, the design, the verdict cache, the trace, the round record |
| **groundspace** | the sequence of states: the git history |
| **candidate** | a design that differs from HEAD by a set of choice overrides, with a disposition |
| **requirement** | what the person asked for, in their words. Ids `R1`, `R2`, ... |
| **claim** | a statement a mechanism can settle, derived from a requirement, with an acceptance condition |
| **mechanism** | what would settle a claim: how its evidence bottoms out, what would do it in words, and the evaluators the claim expects. Every claim names one |
| **evaluator** | anything that can settle a claim and has shown it can fail. The human-facing word (§0.9) |
| **proposer** | a tool the agent uses to produce options or evidence (a parts search, a layout optimiser). Its output is an input; it settles nothing |
| **basis** | where a proposer's or evaluator's answers come from: `established`, `sourced`, `model-only` |
| **round** | one unit of the agent's work: it ends when the agent's turn ends, with a commit |
| **pack** | one domain's proposers and evaluators, with skills, views and site partials |
| **board** | a named group of claims (any kind) the map draws as one board and the site explains as one section |
| **zone** | a named group of choices drawn as one band on the map's left |
| **station** | a derived value that several claim lines share on the map |
| **map** | the deterministic drawing of the resolution: the PETGasus transit pattern |
| **poster** | a board's section of the site: a generated floor plus the agent's chosen figures and prose |

## 3. What stays

1. **The invariants** in `CLAUDE.md` (numbered 1 to 16; 14 landed in phase 5 and 16 in
   phase 3b, see section 8) and the tests that hold them. Nothing below weakens one.
2. **Git is the groundspace.** A checkout is one state; commits are the history. No
   second history store, no snapshot per check. Reason: git already has parent
   pointers, authorship, signing and merges; a copy of that inside the tree is the
   duplication this design exists to avoid, and agents already speak git.
3. **One record per file, read strictly.** `results/` and `exports/` stay sealed and
   chained.
4. **The verdict cache** is the evidence ledger and the execution record. Nothing
   replaces it.
5. **Qualification**: controls, mutation testing for non-bundled evaluators,
   operating context, the human channel for results and ownership.
6. **Stdlib-only spine, no server, no database, no build step for the site.** A
   database does not diff in a pull request; `ledger.json` already is the query
   index.
7. **The site never computes truth.** Static, deployable as committed.
8. **Three-tier pack disclosure** and the pack admission bar.
9. **The model owns values and rationale** (METHOD rule 1). Records observe it
   (decision 10).

## 4. Decisions

### The state

10. **Records observe the design; they do not own its values.** The model owns every
    choice's value, units and rationale, and what lost to it (`PARAMS`).
    `params/<name>.json` keeps its role: the provenance the model cannot hold
    (`source`, `grounded_by`, `tags`). The observed view (value, units, rationale,
    what lost, choice or derived, derived from which choices, read by which
    evaluators) is computed from the model and the trace, shown live, and frozen in
    the round record. *Rejected:* values in records, the model reading them like
    Android string resources. Choices and instructions are not separable in practice
    (`plate_layout = "topopt"` is a choice that selects an instruction path), so
    records would hold the easy third of the state and claim to be its source, and a
    model reading files blinds the tracer. Each instruction layer needs an adapter
    that exposes its choices and provenance; python's `Config` docstrings and
    `PARAMS` are the first.
11. **Instructions are nodes.** Each file in the model's code closure carries its
    digest and a place on the map between choices and derived values; the trace
    gives the edges through it. A geometry edit reads as "this instruction changed,
    these stations moved." The granularity is a file.
12. **Requirements are records.** `requirements/<id>.json`: the person's words
    (`statement`) and where they came from (`source`: the interview, or an input
    id). Claims point at the requirements they serve with `requirements: [ids]`; a
    claim may serve several. A requirement's status is the worst status of its
    claims, in the composition's own order; a requirement no claim serves reads Gap.
    Readiness stays claims-only: milestones require claims. The map's left edge is
    this column.
13. **Every claim names its mechanism.**
    `"mechanism": {"by": "...", "how": "...", "evaluators": ["..."]}`.
    `by` is how the evidence bottoms out (today's `terminal` enum, validated against
    the kind, derived from the kind when absent); `how` says in words what would
    settle the claim (for a physical claim, the written test, today's `note`);
    `evaluators` names the evaluators the claim expects. That list is a cross-check,
    never coverage: coverage stays the registry's, because a claim file that held
    coverage was rewritten by `check` on every run (S-37). `check` and `doctor` warn
    when a named evaluator is not registered or does not cover the claim; no status
    moves. The sealed claim digest keeps its form (`terminal` is `by`, `note` is
    `how`), so every recorded physical pass stays bound. The old keys `terminal` and
    `note` are read as their mechanism equivalents. `gap` lists every claim whose
    mechanism has not run, physical and assumption claims included, and
    `gap --propose` searches with the `how` text. Reason: every claim is settleable;
    some are expensive. "Looks cool" is settled by a reddit post and a sentiment
    pass, latency one week. The status says the mechanism has not run, never that
    nothing can check it.
14. **Decisions get typed rejections and return conditions** (paper §7.4). Each
    `rejected` entry gains `type` (`infeasible`, `dominated`, `preferred_against`,
    `unevaluated`), `returns_if` (§0.2), and `input` when an input id is its source.
    `PARAMS` rejections in the model stay text. A `preferred_against` rejection is a
    person's preference: it counts only when that person records it with
    `nopekit prefer`, typed in their own shell (sealed, like an owner); until then it
    reads "preferred against, not recorded by <name>".
15. **Candidates are records.** `candidates/<id>.json`: `overrides` (choice values,
    or `"code"` when the candidate differs in instructions), `compared_against` (the
    round record digest at the merge), `disposition` (a rejection type, or
    `selected`), `why`, `returns_if`, and the tag `cand/<id>` by convention. The tree
    never holds a sha. `check` evaluates every return condition against the current
    resolution and prints a candidate that may return; it never changes the exit
    code. A condition on a claim that is not Checked and current reads "cannot be
    evaluated: C1 is <status>". `doctor` warns when a candidate's tag is missing,
    locally or on the remote. *Rejected:* a branch alone (says what, not why; cannot
    be acted on; one `git branch -D` from gone); a sha in the record (self-reference
    across the boundary, unstable under rebase).
16. **The state holds one candidate.** Several at once are branches. A sweep over
    fifty values is an evaluator's work and produces evidence on one claim, not fifty
    candidates. Heavy for set-based exploration of hundreds; fine for one agent and
    one person.
17. **Boards, zones and stations are records.** `groups/<id>.json`:
    `{"kind": "board" | "zone" | "station", "title", "line", "members": [...]}`. A
    board's members are claim ids of any kind; a zone's are choice names; a
    station's are derived-value names. A claim sits on at most one board; claims on
    none go to a board called "Other". Shared derived values without a station
    record become stations under their own names. Reason: the author's reference map
    groups claims, including assumptions and physical tests, onto boards.
18. **The round record is tracked.** `.nopekit/round.json`. Renamed from "the
    judgement", because the glossary's "judgment" is an expert's judgment. Written
    only by `nopekit round` and `nopekit merge`, never by `check`. It holds tokens,
    not words (status keys, ids, numbers, the typed graph), the digests of the
    records and the verdict cache it was computed from, and a digest of itself.
    Readers verify the self-digest and say "edited by hand" or "behind the records"
    when it fails. Never hand-merged: `merge` regenerates it. Not in the spine
    digest. Reason: a pull request that flips C1 from pass to fail shows as a
    one-line diff, and a rare history walk reads it with `git show` without
    re-judging under a newer nopekit.
19. **Edges are typed.** `reads` (evaluator to value, from verdict read sets),
    `settles` (evaluator to claim, registry coverage), `requires` (milestone to
    claim), `derives` (choice to derived value, from the trace), `sources` (input to
    choice, `params/<name>.json` `grounded_by`), `rests_on` (claim to assumption
    claim: its evaluators read a value derived from a choice an assumption sources),
    `resolves` (physical claim to the assumption it replaces: a `resolves: [ids]`
    field on the physical claim), `contradicts` (a physical fail to the evaluator
    that passed the claim) and `needs` (evaluator to prerequisite evaluator). The
    renderer never infers meaning from line style.

### The loop

20. **Only the agent edits records.** The human channel is the short list of actions
    that need a person: a physical result, ownership of an assumption, an expert
    judgment, a preference between candidates, going ahead over an unresolved claim.
    Records need to be readable by a person, not comfortable to edit.
21. **Interview first.** Product-manager style: extract requirements the person can
    state without technical vocabulary, ask for sketches, reference images, links,
    measurements, datasheets. Show the requirements for a yes; the person can always
    say skip. Then first light; claims appear beside the first verdicts, because most
    cannot be stated precisely before a model exists.
22. **Proposals are evidence.** A proposer's output goes through `ingest` and
    `extract`; the input records which proposer produced it. The options it found are
    kept or dropped in a decision record. No proposal record.
23. **`nopekit merge <branch>`.** Merges the branch through the writing half of
    `vcs.py`, tags each losing branch named with `--over` as `cand/<id>`, writes its
    candidate record (overrides from the two choice views, type and `returns_if`
    from the flags), regenerates the round record, commits, and pushes the tags when
    a remote exists (a failed push warns; the local merge stands). It refuses when
    `results/` or `exports/` changed on both sides: a sealed chain cannot be merged
    without re-sealing a person's entries. A `preferred_against` type is refused
    from an agent session; the person records it with `nopekit prefer`. A merge made
    without the command leaves the losers undisposed, not decided.
24. **Rounds are layered.** The map rebuilds after every `check` (through
    `site serve --watch`). The poster prose, the round record and the commit happen
    when the agent's turn ends: the plugin's Stop hook runs `nopekit round`, which
    runs a tier-0 check, starts the trace in the background when the instructions
    changed, builds the site when `site/` exists, writes the round record, stages the
    project's own paths (inside the project's directory only), and commits when the
    identity is allowed (§0.3), with the message `round N: <what changed>` and no
    attribution trailer. It prints its summary as the hook's `systemMessage` and
    exits 0: it never blocks the turn. The commit is history, not a quality gate: a
    round with failing claims still commits. *Rejected:* every write (half-states), a
    command the agent must remember, a state event (hours pass without one).

### Packs

25. **A pack holds proposers and evaluators** for one domain, plus skills, views and
    site partials. Project evaluators stay in the project's `gates/` and `selftest/`,
    qualified against the project's own model; a pack copied into a project stays in
    `.nopekit/packs/`; `NOPEKIT_PACK_PATH` keeps its place in the search order.
    Promoting a project evaluator to a pack means writing a sealed baseline.
26. **Extension repositories** add a pack or extend one (`"extends": "sourcing"` in
    `pack.json`). An extension's evaluator ids use its own namespace; it may `need`
    its base's evaluators; it is demonstrated with its base loaded; an id that is
    already registered is refused (the registry does this today). An extension is
    qualified at the non-bundled bar. A registry file at this repository's root
    (`registry.json`) indexes known pack repositories by what they settle;
    `gap --propose` shows them as "not installed". Pins by source and version are
    deferred.
27. **Track records stay derived** from `results/` on every read: never a file of
    their own, so deleting the verdict cache cannot erase them.
    `nopekit gate track --export` writes the portable, project-free form on demand,
    for a pull request to a pack's repository. Upstream flow beyond that is
    deferred. Reason: the paper (§7.3) says a track record attaches to its operating
    context and transfer cannot be assumed.
38. **Proposers and evaluators declare a basis**, in a fixed order of preference.
    `established`: wraps an existing non-AI tool with its own track record (KiCad's
    layout optimizer or DRC, OpenModelica, trimesh). `sourced`: built by an LLM, and
    its answers come from an outside source it reads (a parts search over a
    catalogue, a datasheet lookup, a cited textbook formula). `model-only`: built by
    an LLM with no basis outside the model. Declared per evaluator (`GateSpec`),
    defaulting to the pack's `basis` in `pack.json`; a bundled pack must declare it
    (`pack validate`); an undeclared basis shows as "undeclared". An input records
    the proposer that produced it; its basis is looked up from that proposer, never
    stated by the input (a self-stated trust property would launder itself, the
    reason `independence` is a forbidden key). The agent uses the highest basis
    available and says why when it goes lower. The basis appears in `gate list`, the
    map and the report; it changes no status and no qualification. Reason: the
    author's rule is "existing trusted systems first, then LLM-built systems that
    source themselves, then LLM-built systems with no outside basis", for both
    phases.
39. **The trace observes derivation by perturbation.** `nopekit model trace`
    rebuilds the model in a temporary copy of the project with each choice nudged by
    -10% and +10% (a boolean or a string is skipped and says so), and records which
    derived values moved and by how much (a scalar by relative change, a structure
    by its digest). Keyed by the instructions' digest and the choices' values,
    tracked under `.nopekit/trace/`, never rewritten. `round` starts it in the
    background when the instructions changed; the map shows each derivation edge as
    traced at a named round. Amber: for a Checked claim that rests on an assumption
    no one has confirmed, the trace re-runs that claim's evaluators on the nudged
    projection (writing nothing, as a mutation pass does); a pass that a ±10% move of
    an assumption-sourced choice flips is amber, "passes if the estimates hold".
    Amber is shown beside Checked; it changes no status. Cost: one model build per
    nudge (PETGasus: about an hour per instruction change).

### The site

28. **Fixed outline:** name, representation, description, map (with the verdict
    sentence right under the representation), one section per board, next steps,
    made with nopekit. Today's panels (every claim, every verdict with its locators,
    parameters with what lost, evidence, gaps, decisions) move to `technical.html`.
    `index.html` stays the project's own; `site build` warns when it predates the
    outline. From decision 40 the outline is the engineering page's
    (`engineering/index.html`), and the root `index.html` sends a browser there.
29. **The map follows the author's transit pattern** (`PETGasus Grounding Map.html`):
    zones of choices on the left, claim lines riding shared stations, ending on
    boards; green for a pass, red for a fail, amber for a pass the trace can flip,
    dashed for stale, dotted for not yet checked. **Order is baked, geometry is
    rendered:** the map data holds nodes with kind, lane, board, status token and a
    stable order index, and typed edges; no x or y. The renderer is plain SVG (no
    d3), uses a layered layout, never a force layout, never reorders by default (a
    "failing first" sort as a viewer control is fine), and measures text after
    placement, never to decide topology. *Rejected:* baked pixel positions (wrong on
    a phone, presentation in data); the reference's hand-placed layout and its
    in-browser what-if (both compute in the browser).
30. **Per-board sections are a poster.** A generated floor (measured against limit
    for every numeric verdict, the evidence the evaluator wrote, copied into
    `site/assets/`), collapsed by default; then the agent's chosen figures and prose
    in `site/content/<board>.md`, stamped `as_of` by `nopekit site stamp <board>`
    and flagged when the resolution of that board's claims has moved since. A
    missing `as_of` reads stale. Every number in prose must match a baked number at
    the prose's precision; a scan enforces it. The agent judges what is compelling,
    as for a conference poster; a figure made in the figuring-out is best, a figure
    made to explain is allowed when cheap. This is where the site persuades, with the
    marketing page's story (decision 40), so the stamp matters most here and there.
31. **History is rare and outside the daily build.** `site build` reads the
    checkout. A history walk (stills, frames, a video beside the representation) is
    one on-demand tool that reads each commit's round record with `git show`. It is
    the lowest priority, and anything it needs beyond the standard library stays out
    of `src/nopekit/`.
32. **Baked versus rendered.** Baked: written by `site build` under `site/data/` and
    `site/assets/`. Rendered: what the browser does with a baked file. Every number,
    status, word and order on the page exists in a baked file; the browser maps data
    to DOM and handles hover, selection, filters and theme. Never in the browser: a
    status, a margin, readiness, a sensitivity. The build time and each verdict's age
    go to `site/data/build.json`, which git ignores, so a rebuild that judged nothing
    new changes no tracked byte.
33. **Live versus published.** Local: `nopekit site serve --watch` watches the
    records, the model, the verdict cache, the trace and `site/content/`, rebuilds,
    and the page polls `data/build.json`. Published: Pages serves `site/` as
    committed, or CI builds it. Same baked files either way.
40. **Two pages: `/engineering/` (the default) and `/marketing/`** (decided
    2026-10-20, from the author's request). Each is a directory with an `index.html`
    whose `<head>` opens with `<base href="../">`, so both read the same baked files at
    the same paths; the root `index.html` is a redirect to `engineering/` that keeps
    the query and the fragment (so a `#claim=` link still opens its drawer), and that
    names `engineering/index.html` in full when opened from the disk, where a folder
    address lists the folder (review of this decision). The
    engineering page is the outline (28). The marketing page follows a reference
    product page: the product in a viewer the person can colour, explode, spin and
    isolate at the top, then the story behind it (`site/content/story.md`, stamped
    like an intro), what it must do (each requirement with its chip), and the choices
    that went into it (each decision with its claims' chips and what lost, newest
    first). A menu near the top of each page links to the other, and each ends with
    the Made-with-nopekit section. Every rule of the site holds on both: the verdict
    headline, the tally, the stale notice and the limits line sit above the fold at
    every width, the viewer under them on a phone (invariant 12), every chip carries
    its claims' marks (`amber` and `disagree`), a preference reads "Preferred against, not recorded by <name>"
    until the person records it (invariant 13), and every word, chip and order is
    baked by `page.bake_page` and held by `page.status_problems` (invariant 14). The
    customizer changes how the page shows the design, never the design: a colour is
    the person's browser's alone, and the page says so. *Rejected:* flat
    `engineering.html` and `marketing.html` (Pages serves `/engineering` from
    `engineering.html`, and `python3 -m http.server`, what `site serve` runs, does
    not, so the addresses the author asked for would answer 404 on the person's own
    machine); the engineering page at `/` with `/engineering` sending a browser back
    to it (the author asked for the page to live at `/engineering`); one page with a
    script router (a reload at `/marketing` is a 404 on a static host with no rewrite
    rules); a `../` baked into every path for the pages in a directory (it breaks
    every baked path and the evidence links, and a shell at the root would need a
    second set); the reference page's build (Vite and TypeScript are a build step,
    which the site may not have, 32); a free-form marketing shell with words of its
    own (words no scan holds, and a status no checker sees); a what-if customizer that
    moves a choice in the browser (29: it computes in the browser, and the verdict
    covers one candidate, 16); decisions oldest first (the page would open on tooling
    and superseded history before the design as it stands).

### Style

34. **Human-facing text on the site follows ASD-STE100** (Simplified Technical
    English): the site template, the words the build bakes, `site/content/` prose,
    the README block nopekit writes, and every skill's example output. Short
    sentences (about 20 words), one approved word for one thing, present tense,
    imperative for instructions, no contractions, no synonyms for variety. CLI output
    and `REPORT.md` keep the glossary and `test_vocabulary` as their rule. Record
    text (a claim's statement, a requirement's words, an evaluator's detail) is
    exempt. The PETGasus front page (commit `08c6783` in that repository) is the
    reference rewrite.
35. **No LLM register on the site.** Banned: em-dash asides, "not X but Y", lists of
    three for rhythm, rhetorical questions, "delve", "robust", "seamless",
    "crucial", "honest" as self-description, a closing line that restates the
    paragraph. "Checked does not mean true" appears once per page. A scan over the
    template and the generated content holds the list.
36. **The badge.** Every project nopekit creates shows the Made-with-nopekit badge,
    linked to `github.com/neuman/nopekit`: the last section of the site (the badge
    ships in the template) and a marked block at the bottom of the README, which
    `init` writes. The mascot (`docs/img/nopey.png`, Nopey the honey badger) belongs
    on this repository's README only.

### The plugin

37. **The skill shrinks** to the method plus a command table. A pack's skills are
    read, not installed: `nopekit packs skills` prints the skill files of the live
    packs that can run here, and the main skill tells the agent to read them. Paths
    use `${CLAUDE_PLUGIN_ROOT}` (braced; the unbraced form is not substituted, and
    the variable is not in the Bash tool's environment). The Stop hook lives in
    `hooks/hooks.json`. No `bin/`: hosts other than the CLI refuse a plugin that has
    one.

## 5. Layout

Now, through phase 7:

```
my-project/
  .nopekit/project.json     name, model entry, nopekit version, packs, people
  model/                    the instructions: owns every choice's value and rationale
  gates/  selftest/         the project's own evaluators and their controls
  views/  generators/       viewgens and the builders milestones name
  requirements/ claims/ decisions/ candidates/ inputs/ results/
  params/ needs/ milestones/ exports/ groups/ views/*.json     one record per file
  out/                      the outputs export builds; ignored
  .nopekit/
    verdicts/               tracked: every verdict by ρ, every control run
    trace/                  tracked: observed derivation, by instructions digest
    round.json              tracked: the round record
    packs/                  packs copied into this project
    ledger.json cache/ obs/ out/     ignored, rebuilt
  site/
    index.html              a redirect to engineering/; the project's own, never regenerated
    engineering/index.html  the outline (28); the project's own
    marketing/index.html    the product, its story and its choices (40); the project's own
    content/<board>.md      agent prose per board, stamped as_of (and intro, next, story)
    annotations.json        authored pins on the representation
    img/made-with-nopekit.png
    data/  assets/          baked by `site build`; data/build.json ignored
```

Last (phase 8, if budget remains): every record directory moves under `design/`
(`exports/`, `needs/` and the `views/*.json` records with them), and
`.nopekit/project.json` becomes `nopekit.json` at the root, through a second one-time
migration under invariant 8's carve-out. Evidence bytes in `inputs/<bucket>/` move
with them only if the migration can keep every verdict and article bound.

## 6. Known risks

Named here so the first person to hit one knows it was seen.

- **Overrides drift from their base.** A candidate was judged against HEAD as it
  stood then. The record carries what it was compared against; the return condition
  applies to that comparison.
- **Return conditions reference claims that get edited.** A condition on a claim that
  is not Checked and current says so; it never silently fails to fire.
- **Claim ids collide across branches.** Two candidates each add the next id; `merge`
  refuses and names both.
- **The round record will be read for truth.** It carries its self-digest and a
  header saying what it is; nothing in the spine reads it as input.
- **The round runs mid-thought.** It commits what is there; the next round commits
  the rest.
- **The agent can run `git merge` without the command.** The candidate record is what
  carries the decision; an unrecorded merge leaves candidates undisposed.
- **Stamped prose ages within minutes.** The stale flag fires on a change to that
  board's claims, not on any change.
- **The floor is a wall of identical bars.** Collapsed by default.
- **A compelling figure can be wrong.** The stamp guards age, not correctness; the
  number scan is the only guard.
- **A slow model makes the trace slow.** It runs in the background and the map labels
  each derivation edge with the round that traced it.
- **A choice outside `Config` is invisible to the trace.** It shows as part of the
  instructions until the adapter exposes it.
- **The documents and tests are the real cost.** The glossary, the spine contract and
  the site contract describe the old shape, and about 1,700 tests hold it. Each
  decision is a document rewrite and a test rewrite before it is code.

## 7. Sequence

Each phase runs on branch `architecture`: understand, design (a judge panel),
implement in a worktree, review (three refuters per finding), fix, all four CI checks
green, commit. Smallest and safest first.

1. **Claims say how they settle** (done: e138fc8). The plugin paths (37, part), the
   mechanism (13), requirements (12), `gap` over every unsettled claim, the vocabulary
   (§0.9), METHOD.md's three edits (§0.8).
2. **Decisions keep their losers** (done: 7a1338c). Typed rejections and return
   conditions (14), `nopekit prefer` (a sealed preference, invariant 13), basis on
   evaluators, proposers and inputs (38), proposers in packs (22, 25).
3. **The graph and the round record** (3a). The typed graph (19), the round record
   (18), `nopekit round` without its hook and commit.
4. **The site** (5). Boards, zones, stations (17); the map data and the transit
   renderer (29); the outline and `technical.html` (28); the poster and `site stamp`
   (30); `build.json` and `serve --watch` (32, 33); the STE and register scans (34,
   35); the README badge (36).
5. **Rounds and the plugin** (6). The Stop hook and the round's commit (24), the
   people allow list (§0.3), the skill rewrite and pack skills (37).
6. **The trace** (3b). Derivation by perturbation, derives edges, amber (39).
7. **Candidates and merge** (4). Candidate records (15, 16), return conditions in
   `check`, the writing half of `vcs.py`, `nopekit merge` (23).
8. **Packs in the world** (7). `extends`, the registry file (26), the track export (27).
9. **The design/ move** (section 5) and **a replay** (a sandboxed agent with no shared
   priors runs the PETGasus requests through the new nopekit), if budget remains.

The order changed after phase 2 (2026-10-15): the author capped this refactor at 40%
of a week's usage, and two phases cost about ten points, so the work that shows (the
graph, the site, the live round) comes before the work that refines it (the trace,
candidates, packs in the world). Phase labels in parentheses are the ones commits
and section 8 use.

**Done when** (the author's stop rule, 2026-10-15): a fresh agent with no shared
priors, in the bench's sandbox, using the plugin as a person would install it, (1)
installs and starts cleanly; (2) given a person's short request, interviews them,
writes requirements in their words, reaches first light and hands over a live site URL;
(3) builds a page that tells the story; and (4) commits every round by itself.

**The acceptance run** (2026-10-15, release 1925e98, the author's verbatim drone request,
a persona playing the person, opus at high effort): (1) met: the plugin loaded both skills
and ran from `${CLAUDE_PLUGIN_ROOT}`; (2) met: six interview questions before any design,
ten requirements in the person's words, 23 claims with verdicts, a live URL; (3) met: the
page named the thing, showed its 3D model, the verdict and a grounding map from the
requirements to six boards, with prose whose unmatched numbers were marked; (4) met in
part: three rounds committed themselves, but the round staged only nopekit's own
directories, so the agent's `bom/` and `build/` never reached git. Fixed after the run
(the round stages the project's whole directory less what git ignores, secret-looking
names and files over 50 MiB), with `site serve --detach` for a live site that outlives a
background task, and the skill's guidance for zones and plainly titled stations.

`examples/bracket` stays the reference project at every phase, its committed cache
regenerated whenever the spine moves.

## 8. Decided during implementation

The author did not answer these directly; the agent decided them within the
decisions above. Overturn any of them here.

- **Mechanism shape** (13): `by`, `how`, `evaluators`, beside the claim's `kind`; the
  sealed digest keeps its form so no person must re-test.
- **The evaluator list on a claim is a cross-check, never coverage** (13). An earlier
  reading made it add coverage; the code's lesson S-37 (a claim file holding coverage
  was rewritten by `check`) and the resolver's purity overruled it.
- **Requirement status and readiness** (12): worst of its claims; readiness stays
  claims-only; requirements get a report section and a map column, not a JUnit suite.
- **Amber changes no status** (39), as basis changes none (§0.4).
- **"Judgement" becomes "round record"** (18), to keep one word for one thing beside
  the glossary's "judgment".
- **Boards group claims, not evaluators** (17), as the reference map does.
- **A preference is recorded with `nopekit prefer`** (14, 23) and sealed like an
  owner, because a person's choice written into an agent-edited record is a nominee,
  not an attribution (invariant 11).
- **Track records stay derived** (27); the tracked `.nopekit/track/` of the first
  draft would have been a second home for the same facts.
- **Pins are deferred** (26): nothing reads a pack's version today, and the verdict
  cache already keys on each evaluator's code.
- **Style scope** (34, 35): the site and what nopekit writes into a README, not CLI
  output or `REPORT.md`.
- **The README badge** (36) is a marked block `init` appends at the bottom of
  `README.md`, with an absolute image URL, so it works before `site init`.
- **Invariant numbers**: 13 is "a preference counts only through the person's
  channel" (landed in phase 2) and 14 is "the site never says more than the
  resolution, and stamped prose is flagged when behind" (landed in phase 5, held by
  `tests/test_site_truth.py`). Each lands with its tests, its `INVARIANT_CLASSES` row
  and its `CLAUDE.md` row.
- **The fast tier** keeps its 90-second rule: a phase that adds time pays it back.

**Phase 1** (claims say how they settle), decided while building it:

- **The claim stays flat in memory.** `Claim.terminal` is `by`, `Claim.note` is `how`,
  `Claim.evaluators` is new; only the file spells one `"mechanism"` object
  (`models.CLAIM_MECHANISM`). A nested `Mechanism` dataclass was rejected: every reader
  and about 105 test constructions rewritten for nothing phase 3 needs.
- **One home per member.** `terminal` beside `mechanism.by`, or `note` beside
  `mechanism.how`, is refused even when equal; different members in different
  spellings read; a top-level `evaluators` is refused; `"mechanism": {}` reads as absent.
- **The writer folds** the mechanism at `note`'s slot, members in `by`/`how`/`evaluators`
  order, and writes `requirements` last; index rows show that form, `to_dict` stays flat.
- **Not sealed:** `evaluators` and `requirements` are out of `claims.claim_digest`, and
  out of a gate's read of a claim while empty. `requirements` is read whole and traced.
  `how` stays sealed (it is `note`): editing it moves a judged or tested claim to Gap or
  Stale, as editing `note` did.
- **Requirement ids** are `R` and a number from 1 (`store.REQUIREMENT_ID`), on the file
  stem and every pointer. Fields: `statement` (required) and `source`. `status` and
  `claims` are forbidden keys naming their homes.
- **A pointer to a missing requirement is read**, an index problem and a doctor FAIL
  (the milestone precedent), as is a requirement id equal to a claim id; a requirement
  no claim serves is a doctor warning.
- **Requirement status** is `RequirementStatus(id, status, errored, claims, worst)`: the
  worst claim by `claims.severity`, a crash above a plain skip, ties in record order;
  Gap with no new cause when no claim serves it. A crash rolled up reads `skipped,
  errored` (`report.status_word`), since a rolled-up line prints no tag.
- **Status prints one untagged line**, `requirements N — R1 failing (C1) · …`, after the
  claim rows, only when requirements exist, capped at eight.
- **The report's `## Requirements`** comes after Failing and before Reproduce, only when
  requirements exist; no JUnit suite.
- **JSON:** `summarise()["requirements"]` (tokens); `status --json`, `report --json` and
  `state.json` gain `requirements` rows (`report.requirement_json`); `claim_json` gains
  `mechanism` and drops the flat `evaluators`.
- **"Yet to run"** means Gap, Pending build or Assumed (`claims.MECHANISM_YET_TO_RUN`);
  `gap` resolves the cache exactly as `status` does.
- **`gap`'s output:** status-tagged rows in severity order, each with its mechanism
  line, mechanism warnings and its gap record; a footer counting them; `--json` adds
  `yet_to_run`. `--propose` weighs the `how` words at half the quantity's, searches a
  claim with no Need through a transient Need never written, and says a pack checks
  only a modelled part of a physical, judged or assumed claim.
- **Mechanism warnings** are `unregistered` and `uncovered`, on `check`'s stdout after
  the limit warnings and in `check --json`; `doctor` has a `mechanisms` row that never
  FAILs; `why` shows none.
- **`reason()` is unchanged:** the `how` text is not appended to Gap reasons; it shows in
  `gap`, `why`, the report's gap section and the page's "Mechanism" field.
- **`why`** resolves requirement ids after exact params and claims, takes the caller's
  compositions, and a claim's `why` gains `serves:` and a MECHANISM section.
- **The bracket's requirements** are R1 to R4 in invented interview words; C1–C4 and C7
  declare `by: closed_form`, C5 and C6 leave `by` to their kind; every claim gets a `how`;
  C1–C4 name their tag-covering evaluators.
- **Legacy bracket copies** in the tests exclude `requirements/`
  (`tests/_projects._ADDED_AFTER_MIGRATION`).
- **The chain** `requirements -> claims -> gates -> packs -> readiness report` is also in
  README, CLAUDE.md and the skill; README's rule 1 follows METHOD's.
- **CLAUDE.md** invariants 4, 7, 11 and 12 each gained a clause, invariant 8's record list
  `requirements/`; the new classes map to rows 4, 7, 8, 11 and 12.
- **GLOSSARY:** requirement, mechanism, proposer and basis in §1; round record and board
  in §4 with italic Never-says; *candidate* is an alternative design with a disposition,
  and the current design is never one.
- **Human strings:** Failing's hint and a live-design control say "the current design";
  the three `"note"` strings name `mechanism.how`; the tool-option fallback word is
  "option"; the page's "Note" label is "Mechanism".
- **The plugin test** checks braces under `skills/`, `.claude-plugin/` and `hooks/`, that
  every METHOD.md or docs path a skill names is `${CLAUDE_PLUGIN_ROOT}`-rooted and
  exists, and that there is no `bin/`.
- **The fast tier's pay-back:** `test_status_table.UnqualifiedReadsGap` and one
  `check` run in `test_renderers` moved to the full suite.

**Phase 2** (decisions keep their losers), decided while building it:

- **Typed rejections live on `Rejection(Rejected)`**, used only by `Decision.rejected`.
  PARAMS and `params/` keep text `Rejected`; typed keys there are refused, naming
  `decide`.
- **`by` sits on each preferred_against rejection**, not on the decision: one decision
  can hold one person's preference beside an evaluator's infeasible, as each phase 4
  candidate will. preferred_against without `by`, and `by` on any other type, are
  refused.
- **Return conditions** compare with `<`, `<=`, `>` or `>=`. `"returns_if": {}` reads as
  absent. There are no units. A dangling claim or input is an index problem and a
  doctor warning; `decide` refuses both at write time. `write_record` holds a decision
  to the reader's rules before it writes.
- **The value the composition reports** for a Checked claim is the compared covering
  pass with the least margin (`claims.claim_value`). The margin is that verdict's own
  (`claims.margin`, now over a shared `_margin_of`). A Checked physical claim gives its
  counted result's measured value, in the acceptance units only. A judged claim has no
  value. `limit` on a band cannot be evaluated.
- **A preference binds** to the decision's id and title and the rejection's value, why
  and return condition (`claims.rejection_digest`, schema 1). Its file is
  `preferences/<decision id>.json`, a new sealed kind whose seal names the kind and the
  file, written through the exports branch of `append_sealed`, extracted unchanged. Its
  in-memory home is `Decision.preferences`, hidden from every gate.
- **`prefer` records one alternative per run** (`--value` when several name you). It
  is refused outright from an agent session or a pipe. A repeat writes nothing. It
  prints the rejection read back on the view `status` judges.
- **Basis** reaches a pack's gates through a `PACK_BASIS` module global. It is shown as
  a `basis <word>` segment after `gate list`'s bracket (the line collapses its spacing
  as before, so the shape is single-spaced), in `gate show`'s tier line, in the
  report's Evaluator cell and Reproduce comment, and on `state.json` verdict rows; not
  in JUnit. `pack validate` requires it only of bundled packs.
- **Bundled bases** are §5.4 of the phase design's table, unchanged after reading each
  gate: beam-analytic, fluids-analytic and thermal-analytic `sourced`
  (`beam.input_sanity` and `thermal.h_agreement` `model-only`); cad-solid, fdm-print,
  openmodelica and sourcing `model-only` (`cad.is_volume`, `modelica.checks`,
  `compiles`, `simulates` and `result_claim` `established`; `bom.process_rules`
  `sourced`); the bracket's four structural gates `sourced`, `bed_fit` and `min_wall`
  `model-only`.
- **Proposers** are scripts declared in `provides_proposers` (`id`, `basis`,
  `description`, `run`), found in any discoverable pack, and run as a subprocess with
  `NOPEKIT_PROPOSAL_DIR` (and `NOPEKIT_PROJECT`). A write anywhere in the project
  outside that directory (its ignored scratch aside), a non-zero exit or a timeout
  refuses the ingest. Human sentences say "proposed by". No bundled proposer ships.
- **`check` prints only "may return"**, after the mechanism warnings; its JSON gains
  `may_return`. `status` adds no line. `doctor` gains warn-only `decisions` and `basis`
  rows.
- **`state.json`'s rejection rows are a top-level `rejections` key**, beside
  `decisions`: the document has no `summary` key to carry them.
- **The bracket** gains `decisions/print-in-petg.json`, written by `decide` (title
  "Print in PETG", so the slug is the id), with three types and an unrecorded
  preference by an invented person, Robin Okafor (the phase design named another, a persona the private bench scores — `test_priors` refuses it in shipped text). Each sentence follows the model: PLA is
  infeasible on the model's own creep sentence; ABS is *unevaluated*, not dominated —
  the model holds no ABS properties, so "no stiffer than PETG" could not be said from
  it. No preference file is committed.
- **The status packs line** names up to eight available packs, then "and N more".
- **Invariant 13** lands in `tests/test_preferences.py`. Return-condition honesty maps
  to 12; basis maps to 4, 7 and 9; proposers map to 3 and 8.
- **The basis words, sharpened** (38): *established* when the verdict or proposal is
  an existing non-AI tool's own answer, *sourced* when its logic follows a cited
  outside source, *model-only* when the model wrote the logic, even when it calls a
  tool's primitives. Decision 38's "wraps an existing non-AI tool" would have made
  `cad.watertight` (its own edge count over a trimesh mesh) established; the
  glossary, PACK_FORMAT and the skills say the sharper rule.

**Review of phase 2**, decided while fixing what it found:

- **A physical claim's value is the counted pass's** (`Standing.counted`), never the
  newest entry: a later pass from an agent session is recorded, counts for nothing,
  and must not make a return condition hold.
- **A gate's copy of `decisions` holds no `preferences`** (`verdicts._HIDDEN_FROM_THE_COPY`):
  out of the digest was not enough. The claim's in-memory fields stay on the copy, as
  since P2.1.
- **`propose` watches the whole project**, by size and mtime (`cli._project_files`),
  not the records digest, and names every moved path; the limit is in SPINE_CONTRACT.
- **`propose`'s script arguments are `nargs="*"`**, and `main` hands everything after
  the first `--` to the script: nopekit's own flags stay nopekit's after the id, and
  a flag of the script's goes after `--` (an unrecognised argument before it is said
  aloud). `--json` prints JSON on a run that wrote no file.
- **A basis word is said beside "basis"**: `proposed by <id> (basis <word>)`, and
  `propose`'s listing row `<id>  basis <word>  <description>`.

**Phase 3a** (the graph and the round record), decided while building it:

- **The graph is structure; the statuses sit beside it.** `graph.build` returns nodes
  and typed edges with no status, `graph.tokens` the status tokens by id, and the round
  record holds both; the site joins them by id. Statuses inside the nodes were rejected:
  two homes in one file, and a flip of C1 diffing as a nested change instead of
  `"C1": "failing"`.
- **Node ids are `<kind>:<key>`;** an instruction's key is the article's portable code
  path with `<root>/` stripped (`model/bracket.py`). Nodes sort by kind, then
  naturally; edges by type, then source, then target. `to_dict` writes nodes as `{id:
  attrs}` and edges as `{type: {source: [targets]}}`, every type present, no edge
  attributes (`Graph.via` recomputes a `rests_on` edge's evaluator and values for `why`).
- **Each kind's attrs are always present:** a claim `{claim_kind, by, critical,
  removed}`, an evaluator `{pack, basis, tier, registered}`, a value `{role, digest}`
  and `value` only for a finite int, float or bool, an instruction `{digest}`, an input
  `{proposer, basis}`; a requirement, a milestone and a decision none.
- **Value nodes** are every choice and every other name an edge or a parameter view
  names; a derived value nothing reads, sources, decides or derives is no node (it
  keeps a 27 KB field out of the record). Roles are choice, derived or absent; a choice
  and a derived key of one name are one node, the choice.
- **An edge needs two nodes.** A pointer to a missing or wrong-kind record makes no
  edge (the index and doctor name it); an unregistered evaluator a `needs` or a
  `contradicts` names is a `registered: false` node.
- **`reads` come from the registered gates' served `Row.entry`**, never `obs/` or
  `Resolution.read_sets`, through one path rule (`graph.value_names`: `["config", k]`
  reads `k`, `["config"]` every choice, `[k]` `k`, `[]` every value — review of 3a).
  `settles` is `claims.effective_gates`; `sources` is the one grounding map, moved from
  `cli._grounding` to `artifacts.value_grounding`.
- **`rests_on`** is computed for claims of every kind, never to itself, and closes the
  read values upward over `derives` edges, so phase 3b's trace adds edges and no code
  (a stub trace in `test_graph` proves it). With no trace the graph says `trace:
  null`, `derives: {}`, and doctor "derivation not traced yet".
- **`Claim.resolves`** is declared after `requirements`, written only when non-empty,
  refused on a non-physical claim, on the claim itself, on a non-id and on a repeat; a
  dangling or non-assumption target is an index problem and a doctor `graph` FAIL. Not
  in the claim digest, absent-when-empty for a gate's read, and it moves no status.
- **An assumption's id in a param's `grounded_by`** says the value IS that assumption.
  An id that is both an input and a claim reads as the input and is an index problem; a
  non-assumption claim there is one too; `PARAMS` grounding gets the same check in
  doctor's `graph` row. `Claim.grounded_by` keeps meaning inputs only.
- **`FORBIDDEN_KEYS`** gains `Claim.rests_on`, `Param.read_by` and
  `Param.sourced_from`, each naming where the fact is derived.
- **The observed view** (`ParamView.read_by`, `sourced_from`, filled by
  `graph.observe`; `gates` kept as it was, so the two can differ) reaches
  `last_check.json`, `state.json` and `why`; `status` and the report carry the view for
  the undefended rule only and print neither field. `graph.observe` takes
  the model's choice names (`choices=`), the one thing a read of the whole `config`
  needs that a parameter view does not carry — a keyword the design did not list.
- **`why`** prints `read by:` and `sourced from:` for a parameter, `rests on:`,
  `resolves:` and `resolved by:` for a claim (each only when handed the graph, which
  the CLI always passes), an assumption in GROUNDED BY as `[assumption]`, and a short
  block for a derived value an evaluator read; `inputs --json` leaves claim ids out of
  its grounding map.
- **The round record is computed from three digests:** the records
  (`store.records_digest`), the verdict cache (every file's bytes under
  `.nopekit/verdicts/`) and the design (the seal of `verdicts.design_of`, the current
  article's `built_from`, extracted from `article_of` with every article hash
  unchanged). No spine or version digest: an upgrade is not "behind the records".
- **The file:** keys in a fixed order, indent 2, `ensure_ascii=False`, NaN refused, one
  newline; `self` is the seal of the rest; the one sentence is `HEADER`; a crash reads
  `errored`; `disagree` lists claims and requirements whose status and evidence
  disagree, so a contradiction never sits there as a silent `checked`.
- **Reader states:** absent, unreadable (strict JSON: duplicate keys and NaN refused;
  a merge conflict lands here), newer (schema above 1, never rewritten), edited (the
  seal does not match), current or behind (naming the records, the verdict cache or the
  design).
- **The write rule:** nothing when the record reads `ok` and holds the same on every
  key but its own four; otherwise the previous round + 1 (an edited record's
  included), else 1; never over a newer schema; bytes only when they differ.
  `rounds.sealed` is public, so invariant 8's test computes the record of the state
  after a run exactly as `write` does.
- **`round` resolves once, after its check,** with no sweep overlay (what `status`
  shows), and the site and the record share that resolution.
- **`round` never exits 2:** every `NopekitError` is said in one line with exit 0; a
  model that does not load refuses the check and the record is still written; a pack
  that does not import writes no record (`_check_run` takes the strict registry
  `round` loaded first, to tell that refusal from the model's — a keyword the design
  did not list); only a traceback exits 1.
- **`round`** migrates a legacy project as `check` does (invariant 8's carve-out
  names it), is not in `_INDEX_UNTOUCHED`, and prints one paragraph: flips capped at
  eight, new verdicts at four ids, status words from `report.status_token_word`,
  *ready* as last evaluated.
- **`status`** prints a `round record:` line after `last check run:` and `status
  --json` a `round` object (its state, never its statuses); `doctor` gains a warn-only
  `round` row and a `graph` row that FAILs on a pointer problem.
- **`graph.py` and `rounds.py` are not spine modules** and join `CLOCKLESS_MODULES`;
  only `cli` imports `rounds`, and the literal file name is a value only in `rounds.py`
  (a docstring may say where the record lives). Their invariant classes map to rows 7,
  8 and 12; no invariant number is new (14 stays reserved).
- **The bracket** gains `params/load_n.json` (`{"grounded_by": ["C6"]}`), so C1 rests
  on C6, and a committed `.nopekit/round.json` (round 1). No claim and no `resolves`
  were added, so the transcript's counts hold; the cache's regeneration ends with
  `nopekit round`.
- **Invariant 8's property test** explains a write of the round record only when its
  bytes moved and equal the record recomputed in process from the state after the run,
  never as a path allowance.
- **The louder project's after-edit round** runs after the files it reads are read:
  its tier-0 check re-runs what the edit invalidated and rebuilds the site, which every
  reader before it must see Stale.
- **The fast tier's pay-back:** `test_vocabulary.QualificationWordsComeFromOneTable`,
  `test_freshness`, `test_readers` and `test_trace` moved to the full suite, none an
  invariant class; the phase added ~3.8 s in process (its invariant rows and
  `round`'s watched run in the records sweep), and two interleaved pairs then measured
  flat against c26fcd5 (92.0 and 92.6 s wall against 92.5 and 92.3 s).
- **New public names:** `verdicts.design_of`, `store.pointer_problems` and
  `grounded_by_problem`, `artifacts.value_grounding` (`cli._grounding` deleted),
  `claims.outcome_key`, `report.status_token_word` (the private name kept),
  `report.round_paragraph` and `round_moved`. `site.judgement_digest` is not renamed
  until the site phase; no graph key goes into `state.json`, and no graph command is
  added.

**Review of phase 3a**, decided while fixing what it found:

- **A read of the whole `ctx.params` reads every value.** `.copy()`, `.items()` or
  `json.dumps(ctx.params)` record one bare path, `[]`; `graph.value_names` reads it as
  every choice and every derived key (`graph.derived_names`: the projection's, less an
  echoed `config`), so the evaluator draws its `reads` edges, its claim its `rests_on`,
  and `why` its `read by:`. `observe` and `_shown_params` take the projection's derived
  names for it.
- **`contradicts` starts at the claim**, not at a physical-result node (decision 19
  says "a physical fail"): `Resolution.track` names the claim whose sealed fail
  contradicts the evaluator, and a result is an entry of that claim's sealed file, not a
  record of its own. A result node would be a node per sealed entry, joined to nothing
  else; the claim is what the map draws.
- **The seal comes before the schema.** The self-digest rule is the record's format at
  every schema; a record whose seal fails reads `edited`, whatever its `schema`, and
  `round` writes it again. Only a sealed record with a greater schema reads `newer`.
- **Behind names the resolution** when none of the three digests moved and the record
  still differs from the body `round` would write now (`rounds.RESOLUTION`): an
  evaluator's code, its controls, a pack or a tool. `status` and `doctor` compute that
  body (`cli._round_state`). Digesting each evaluator's code and controls into
  `computed_from` was rejected: a second copy of the cache's own keying, blind to a pack
  installed or a tool gone. A forgery resealed by hand therefore reads behind.
- **A model that does not load builds no site in `round`**, as `site build` refuses it;
  the paragraph says "Site not built: <the model's error>".
- **Instructions are compared only when both records hold the model's code.** A model
  that stops loading says "instructions not recorded (the model does not load)" (and
  `--json`'s `instructions_unrecorded`), never every file moved.
- **`why <claim>`, untraced, says only direct reads were followed** for every claim an
  evaluator settles, with or without a `rests on:` line.
- **GLOSSARY §4** gains round, choice, derived value, instructions and read by; the
  decision row's italic *choice* now says the value a decision picked is a choice.
- **The fast tier's pay-back:** the review added ~1.3 s of invariant rows and moved
  `doctor`'s round row to the gate and the graph's and the round record's bytes on
  another path to the full suite; measured flat on wall against 6440bad, +0.6 s user.

**Phase 5** (the site), decided while building it (the data side):

- **Groups gain an optional integer `order`** (default 0, written only when non-zero;
  groups of one kind sort by `(order, natural id)`). Decision 17 lists kind, title,
  line and members only, but board order derived from member claim ids would put
  PETGasus's `measured` (A1) board first and `fits` (C5) before `prints` (C6); file-name
  order would force renaming prose files. Rows within a board follow `members` order,
  as the reference's do.
- **Group ids follow the milestone pattern** (lower case, `.`, `_`, `-`, at most 64),
  because an id is also a prose file name, an anchor and the `site stamp` argument;
  `intro`, `next` and `other` are reserved and refused by the strict reader for every
  kind. `title` is required, `line` optional, an empty `members` list is read (doctor
  warns); `status`, `x` and `y` are forbidden keys naming where each is derived.
- **Groups are hidden from every gate** (`verdicts._LEDGER_HIDDEN`), so a regroup
  re-keys no verdict; with the `models.py` edit this moved the spine digest, so the
  bracket's cache and round record were regenerated in the phase commit (round 2).
- **What the index judges, and what it cannot.** A board member with no claim file, a
  claim on two boards, a value in two zones or on two stations, and a name in both a
  zone and a station are index problems (doctor FAIL); whether a zone member is a
  choice or a station member a derived value needs the model, so doctor (FAIL when the
  model loads) and `site build` (a warning) judge it, never the index. A claim on two
  boards is drawn on the first by `(order, id)`; a choice in two zones likewise.
- **The generated board** for claims on no board has id `other`, titled "Other" beside
  board records and "Claims" with none; the generated zone likewise "Other choices" or
  "Choices", holding only the remaining choices a line touches or an assumption
  sources. `map.json`'s `unread` lists every choice no line touches (in a zone record
  or not); only those in no zone are left undrawn.
- **Generated stations**: a derived value no station record holds, read by a named path
  (never `[]` or `["config"]`) by the settling evaluators of two or more distinct
  claims; id `station:@<name>`, so it never collides with a record; the build and
  doctor warn past 8 (`page.IMPLICIT_STATIONS_WARN`).
- **A line's stops** are its requirements, the zones and stations holding the values
  its settling evaluators read (named reads closed upward over `derives` with
  `graph.upward`), then the claim; a whole read draws no stop and is baked as
  `reads_all`.
- **Requirement, zone and station nodes carry a baked `column`** from greedy interval
  packing over the rows their lines span, and each lane its `columns`; claim nodes a
  global `order`, a `row` within their board and `severity` (for the optional "failing
  first" toggle). No coordinate is baked anywhere.
- **A board's token is `claims.worst_of`** over its members (factored out of
  `claims.requirement_statuses`, which now calls it), Gap when empty; boards get no
  ready flag. **Marks** are a list of tokens beside the status (`disagree` today, `amber`
  from phase 3b) and never change a token; a requirement or board carries `disagree`
  when any member does.
- **Two front-page documents**: `page.json` (outline, verdict, prose, floor rows, next
  steps, words) and `map.json` (nodes, lines, values, evaluators, typed edges); claim
  detail lives once, in `page.json` `claims`, for the floor and the drawer. Both carry a
  `self` seal (`page.seal`) and the resolution and records digests.
- **Two new non-spine modules**, both clock-free: `page.py` (the layout, the bake, the
  evidence plan, the settle table, `status_problems`) and `prose.py` (front matter, the
  markdown subset, the number scan, the style scans, the stamp digest); `site.py` stays
  the orchestrator. `prose.py` imports nothing from nopekit, so `site stamp` and the
  style tests need no page machinery.
- **Prose markdown is parsed at build time** into typed blocks and runs; the browser
  receives no HTML from prose. A `num` run carries `t`, the number as written (`40%`,
  `12mm`), beside `num` and `unit` — a key the design did not list, so the template
  shows the author's own spelling.
- **The number pool** is every finite numeric leaf (never numbers inside strings) of
  `state.json`, the view files, `map.json`, `page.json` without its prose and
  `site/assets/**/*.json`, less `schema`, `outline`, `order`, `column`, `columns`,
  `severity` and `rank`; matching is sign-insensitive, within half a unit of the last
  written decimal, `%` also tries ×100, thousands separators are removed. A number
  joined to letters counts only with a unit from `prose.UNIT_WORDS`; **a number
  followed by a space and another word stands alone and is scanned** (`2 bolts`) — the
  design read any letters after a space as the number's unit or an identifier, which
  would have left every count in prose unscanned.
- **Stamp scope**: a board's file hashes `{claim id: token[:marks]}` for its claims;
  `intro.md` and `next.md` hash every claim's token plus `_readiness` and
  `_m:<milestone>`. The digest is the first 16 hex of sha256 with a `nopekit-stamp-v1`
  prefix. `unstamped` is baked apart from `stale`; both show the "written before the
  latest results" notice (in their own words).
- **`site stamp`** takes the lock, refuses when the model entry fails to load, never
  needs `site/index.html`, and writes only front matter; `site stamp --all` skips files
  that name no subject, and with no `site/content/` at all says there is nothing to
  stamp and exits 0 (a named subject there is refused) — so `--all` is safe to run in
  every project, as invariant 8's command list runs it.
- **Evidence** is copied to `site/assets/evidence/<gate>/<entry name>/<basename>`, only
  from `<out>/` or inside the root, at most 2 MiB a file; when the source is gone but
  the entry-keyed copy exists (a fresh clone) the copy is kept, so no manifest file is
  needed. A clashing basename in one entry gains `-2`, `-3`.
- **`state.json` is clock-free**: it loses `meta.built` and each verdict row's `when`
  and `age_s` (now in `build.json`), and gains verdict `order` (rank, then id),
  `evidence_assets`, claim `board`, and chart `margin_text`; its CLI-register words stay
  and are exempt from the STE scan as the technical record's. `evidence_assets` is left
  out of `resolution_digest` (where a copy is on the page moves with the disk, never
  with the judgement) — the design had `_UNJUDGED_ROW` empty.
- **`site.judgement_digest`/`judgement_moved` and `meta.judgement_digest`** are renamed
  `resolution_digest`/`resolution_moved`/`meta.resolution_digest` with no aliases; a
  page without the new key reads stale once.
- **`build.json`** carries the build time, `live`, an `id` over the tracked baked bytes,
  ok/error, per-verdict ages with the `aged` flag (a day, `site.AGED_SECONDS`, moved
  from format.js), and the mixed-age banner decision (moved from app.js); it is ignored
  through `site/.gitignore`, which `site build` amends (`site.ensure_site_ignore`).
- **Tracked baked files are written only when their bytes differ**
  (`util.write_json_if_changed`, `ViewContext.write_asset`, the evidence copies), so a
  rebuild that judged nothing new changes no tracked byte or mtime.
- **`site build` refuses**, writing nothing under `site/data/`, when
  `page.status_problems` finds a baked token that differs from `graph.tokens`,
  `claims.worst_of` or `claims.unresolved`; the same function judges on-disk pages in
  `status` and `doctor` (*edited by hand*, a doctor FAIL) and is the checker the tests
  hold to their own answers.
- **Page currency** in `status` and `doctor`, in order: the records and the
  `state.json` resolution digest, then missing `page.json`/`map.json`, a broken seal
  (edited), token disagreement with the live resolution while both documents' digests
  match now's (edited: a resealed forgery), and `page.json`'s `content_digest` against
  `site/content/` now. The seals are read whatever the order decides, so an edited
  page says so beside any stale reason.
- **A site is present** (round builds it, status shows it, site build and site serve
  accept it) only when `site/index.html` exists (`site.scaffolded`), so the bracket
  tracks `site/content/` without a built site.
- **`technical.html` is a shell like `index.html`**: written by `site init` and by
  `site build` when absent, never refreshed (PETGasus ships its own customised
  `technical.html`). The data side writes it only when the template carries one.
- **The outline marker** is `<meta name="nopekit-outline" content="1">`; `site build`
  warns when `index.html` lacks it or carries an older one, and still writes the front
  page's two documents.
- **The readiness sentence gains `register="site"`** inside `report._verdict_sentence`
  (and `_milestone_sentence`, `limits_line`), with STE templates in
  `HUMAN["site"]["readiness"]` mirroring every CLI branch; the CLI branch's bytes are
  untouched. The hardware clause and `HUMAN["physical"]["unbound"]` pass the style scan
  as they are, so they gained no site key.
- **`HUMAN["site"]`** holds STE hints per token, one STE sentence per `ClaimCause`, a
  settle text per cause (an exhaustive table; every command parses), lane, kind, `by`
  (with `automated` for a measurable claim that declares none), basis and outcome
  words, headings and notices; the checked hint omits "does not mean true" so that
  sentence appears once per page (`verdict.limits`).
- **Next steps**: per milestone, `claims.unresolved`'s unresolved claims in severity
  order with their mechanism's `how`, an STE text and the settling commands; a required
  id with no claim file is listed under `missing`; with no milestone the required claims
  form one `project` group; unresolved claims no milestone requires go under `open`;
  `next: true` marks the first not-ready milestone in natural order.
- **The STE scan** covers `HUMAN["site"]`, the word keys of `page.json`/`map.json`
  (`page.WORD_KEYS`, every string key classified against `page.RECORD_KEYS`), the README
  block and the bracket's content (template strings are the template side's); record
  text, `reason`/`stale_reason`/`limit_text` and `state.json`'s words are exempt.
  Sentence limit 25 words (STE rule 4.1), not counting ids, parentheses, code or URLs;
  lists of three and restating closers are review-only.
- **`site serve --watch`** rebuilds through a `nopekit site build --live` subprocess,
  debounced over two equal polls of `site.WATCH_DIRS`, waits while another command
  holds the lock (the lock file exists: `FileLock.held` says only whether the same
  object holds it, so the design's `held()` would never have waited), never runs
  `check`, and on failure writes `build.json` `ok: false` with the previous `id`.
- **`nopekit site badge`** writes or refreshes the README block; `init` calls the same
  `store.ensure_readme_badge`; doctor's `readme` row (warn only) suggests the command.
  Markers are `<!-- nopekit:begin badge -->`/`<!-- nopekit:end badge -->`; a missing
  `README.md` is created as `# name` plus the summary.
- **Doctor** gains `groups` (FAIL on index problems and unknown zone/station members),
  `site text` (warn on stale or unstamped prose) and `readme` rows; `why` resolves group
  ids after decisions and shows a claim's board — only in a project with board records,
  so a project without groups prints the `why` it printed before.
- **The bracket** gains four boards (strength, prints, pump, outdoors, in requirement
  order R1, R4, R2, R3, with C6 on strength), four zones, one recorded station
  (`allowable`: `design_stress`) with `slenderness` left generated, six stamped STE
  prose files, a root `.gitignore` that tracks only `site/content/`, and the badge
  block; its regenerated cache ends at round 2.
- **Invariant 14's classes** live in `tests/test_site_truth.py` (three on the data side;
  `TheBrowserComputesNoTruth` is the template side's); `NextStepsAreThePredicate` joins
  row 12 and `test_records.GroupsAreRecords` row 8. The watcher's and the badge's tests
  sit in `tests/test_groups.py` beside the groups' readers, where the design placed them
  in `test_launch` and `test_record_commands`.
- **The fast tier is over the line after this phase**, said in `tests/fast.py`: two
  interleaved pairs measured 96.5 s and 97.7 s wall against 89.4 s and 93.8 s for
  fe9f6e3 (~3.8 s of test time added: the in-process invariant rows ~0.9 s, the two
  records-sweep rows ~1.8 s, the site register in the hardware-clause rows and the
  meta scans ~0.7 s); ~1.2 s was paid (seven non-invariant entries to the full suite,
  the number scan's build row to the gate), and the next change that touches the tier
  pays the rest first. The phase's design asked the fast tier to stay flat; the
  non-invariant entries left in it were too small to pay for the sweep rows it also
  asked for.
- **The watcher takes its snapshot before it builds** (and `site serve --watch` before
  its first build), so an edit that lands while a build runs is still seen; the first
  cut snapshotted after the build and, under load, read a model broken during it as
  built.
- **doctor's `site` row warns on a stale page, a dangling locator or a shell older than
  the outline**; text written before the latest results is the `site text` row's, so
  one stale piece of prose is one warning (the design put it on both rows).

**Phase 5** (the site), decided while building it (the template side):

- **The map's geometry is straight tracks at the board rows** (the design's baseline):
  each line runs level from its first stop to its row, capsules sit in their baked
  column over the rows their lines span, and a line that only crosses a capsule's span
  is drawn over it on a bridge, so it never reads as a stop. *Rejected:* the
  reference's bundled trunk with risers and fallers (it needs per-project routing, the
  hand-placed layout decision 29 rejects); a capsule per member line (a station shared
  by six lines would draw six). A node no line stops at (most of PETGasus's zones until
  the trace links choices to derived values) is listed under the map in its lane, not
  drawn on a track.
- **The viewer control is labelled "Worst first"**, where decision 29 says "failing
  first": the label is a template string, and `failing` is a status word the page may
  only print from the bake (`test_vocabulary`'s page scan). It sorts by the baked
  `severity`, then `row`, inside each board; columns stay the baked ones.
- **A capsule's title may take two columns of room**, moves up a line (twice at most)
  to clear an earlier title, and only then is shortened, with its full text as the
  tooltip. The first cut gave it one column, and the bracket's "Load and material"
  read "Load and mat…".
- **The drawer and the floor share one function** (`drawer.claimBlocks`), so the two
  places a claim is explained cannot drift. The drawer also opens from the URL
  (`#claim=C1`, `#zone=arm`), which is how a link from elsewhere, and the headless
  check of this phase, reach it.
- **A recorded result's word comes from a new key**, `physical_result.outcome`
  (`"pass"` | `"fail"`, baked by `site.state` beside `passed`): the template scan
  refuses any `.passed` read, a verdict row's flag and a result's field alike, and
  `counts` alone cannot tell a counted fail from a counted pass. The design's table
  mapped `result.passed` to `counts`, which loses the fail.
- **`TheBrowserComputesNoTruth` reads code with every string's text and every comment
  blanked** (`tests/_template.py`, one lexer for it and the style scan); `.error` is a
  flag only where it decides (`?`, `&&`, `!`), because `v.error || v.detail` prints the
  crash's message. Its rules: a verdict flag, arithmetic on a limit, `every`/`some`
  over a status, a sort over a status or a status table, an age compared.
- **The style scan reads a JavaScript literal as words when it holds two plain words
  side by side** outside a class, selector, attribute, storage-key or comparison slot;
  HTML text nodes and `alt`, `title` and `aria-label` are read whole. Comments are
  exempt. The heuristic's planted cases are in `test_style.TemplateWordsFollowTheStyle`.
- **The technical record's page lost its own judgements** with the rest: it defaults
  to showing every evaluator (the "problems only" default read the statuses), shows no
  failing count on a view tab, and gives the anchored list no tone of its own.
- **The drawer's open class is `is-open`**, and the front page names no status word in
  a literal: `open` is Open's word, and the page scan read `classList.add("open")` as
  the page saying it.

**Phase 5** (the site), decided in its review (each a confirmed finding; the bullets
above stand where these do not change them):

- **A compared value's words use the claim's margin.** `value_text` beside the claim's
  limit carries the margin against that limit (`claims._margin_of` over the
  acceptance condition, `cross_check` deciding the side); the baked `margin` stays
  `claims.margin`'s beside the verdict's own `limit`. C3 tightened to 0.1 MPa had read
  "99% inside the limit" under Failing. `status_problems` names a compared value
  whose words disagree with `cross_check`.
- **The checker holds words, groups, marks and prose.** Every baked status `word` is
  its token's; a milestone's and the project's `ready`, `token` and `word` are
  `claims.unresolved`'s; a step carries its claim's `marks`; each prose piece's
  `stamp` and `notice` are its digest's now. `site status`, `status` and `doctor` also
  hand it each file's `as_of` and the number pool (`site.baked_pool`), so a resealed
  page cannot show behind text as current.
- **A board's stamp holds the claims its text names** from other boards, keyed
  `@<id>` (`page.named_in`), in `site stamp` and in the build alike; a regroup still
  moves the stamp. *Rejected:* refusing a link to another board's claim (prose that
  explains a dependency names it).
- **The number scan reads units.** Letters joined to a number are its unit unless an
  identifier suffix (`3D`, `1st`); `.9` and `1e3` are read; `row`, `index` and `tier`
  are structure; counts (`n`, tallies, ground and readiness counts) match a bare
  number only, a margin a percentage, a value its own unit or none stated. The pool
  reads only the assets the build keeps. *Rejected:* leaving counts out (a true "3 of
  7 claims" would be marked).
- **Evidence stays where it was written.** `<out>/` paths must resolve inside
  `.nopekit/out/` (no `..`, no link out); a bare path inside the root with no hidden
  part and not git-ignored (`vcs.ignored`); clashing names are free ignoring case.
  The words baked beside evidence not on the page depend on the path alone ("outside
  the project" or "not on the page"); the disk's reason is a build warning, so a
  fresh clone bakes the same bytes.
- **`site build` writes `site/.gitignore` with the clock's lines when there is none**,
  and the scaffolded comment says to delete only the `vendor/` line.
- **The README block is whole marker lines** (`store.readme_has_badge`, which doctor
  reads too); anything else gets a block appended, never a splice across a person's
  text, and the file's CRLF or LF is kept.
- **A shown pass is the pass `claim physical` takes**: `--evidence <file>`, and a
  judgment's `--authority`. A built article with no milestone gets the
  `article_free` text, which says to name the claim in a milestone file.
- **The template**: every chip passes the bake's `marks` (a "!" and the notice); the
  map's rows and headers grow with the text below 1400 px (`metricsFor`, redrawn on
  `onRescale`); with the drawer open the map scales into its column; a rested-on line
  keeps its status colour; the phone caption speaks of cards; an unmatched number
  wears a dagger, stays on one line and is explained under the text; the technical
  record fits a 390 px phone.
- **The fast tier**: the two records-sweep rows phase 5 added (`site stamp --all`,
  `site badge`) moved to tests of their own in each sweep, left to the gate, and
  `test_meta.NoSubprocessOutsideRun` (a harness guard) to the full suite: ~2.9 s paid
  against ~0.3 s added, which covers the ~2.5 s phase 5 left standing. Two
  interleaved pairs measured 110.3 s and 113.9 s wall against 113.0 s and 114.1 s for
  c392078 on a loaded machine (`tests/fast.py`); no idle run was had, so the 90 s line
  itself is not shown met.

**Phase 6** (rounds and the plugin), decided while building it:

- **`people` entries are `{"name", "email"}` objects** (`models.Person`), not
  `Name <email>` strings: the reader names the half that is wrong, and git prints the
  two separately. A git identity matches when the name is equal after stripping and the
  email equal ignoring case; `claims.identity_matches` (a name alone) is not reused,
  because §0.3 needs both. Duplicate emails are refused, compared ignoring case.
- **`PROJECT_SCHEMA` stays 2.** `people` is optional on read (absent is `[]`) and always
  written, last, so an earlier phase's file keeps its key order. An older nopekit
  refuses the key, naming it; the plugin ships the spine and the skill together.
- **`people` is not hidden from a gate's read of `meta`** (read whole), so
  `verdicts.py` is untouched; no bundled or bracket gate reads `meta`. **It is never
  baked into `state.json`** (`site._UNPUBLISHED_META`): the page is published, and the
  design had `meta` copied whole, which would have put every listed email on Pages.
- **The staging list is closed** (`rounds.ROUND_DIRS`, `ROUND_NOPEKIT`,
  `ROUND_ROOT_FILES`; superseded by the follow-up after the acceptance run, below,
  where a round stages every file git does not ignore): the record directories, `model/`,
  `gates/`, `selftest/`, `generators/`, `site/`, `.nopekit/project.json`, the round
  record, `.nopekit/.gitignore`, `.nopekit/verdicts/`, `.nopekit/trace/`,
  `.nopekit/packs/`, and the root's `.gitignore`, `.gitattributes` and `README.md`.
  `docs/` and `.vscode/` are out (`launch.json` holds a machine path), so a new
  project's paragraph names `.vscode/launch.json` as not staged on each round that
  commits. A file over 50 MiB (`ROUND_MAX_BYTES`) is named, not staged; an untracked
  nested repository (`name/`) is never staged.
- **`vcs.changed_paths` asks `git diff`, `diff --cached` and `ls-files --others`**, each
  over `.`: the design's `ls-files --modified --deleted` names a file whose mtime alone
  moved, and a copied project would have listed every such file as "not staged".
- **The commit is `git commit --only` with the paths on stdin**, after `git add --all`
  of the same list; what the person staged elsewhere stays staged. Every refusal (a
  newer round record, no repository, an operation in progress, a detached HEAD, no
  identity, no people, an identity not listed) is decided before the index is touched;
  a commit git refuses leaves the round's files staged and says so. A failing round
  commits; a round that is "not run" does not. `why` gained `unlisted` (git did not
  list the changes), which the design folded into `refused`, whose sentence says the
  files were left staged when none were.
- **The commit number is the round record's.** A round whose record stood but whose
  files changed reuses it, with a `<kinds> changed` subject; past 72 characters the
  subject counts the flips and new verdicts, then is cut. The body is the paragraph,
  the files by directory, the earlier rounds it holds (from HEAD's round record, or
  all before it when HEAD has none) and what it left out; it is wrapped so no line
  starts with one token and a colon, and `git interpret-trailers --parse` finds nothing.
- **The writing half's timeout is 120 s**, SIGTERM then SIGKILL after 5 s, with
  `GIT_EDITOR=:` and only `-c user.useConfigOnly=true`; `why` is git's first stderr
  line, prefixed `refused:`.
- **The Stop hook** finds the project from `-C`, the payload's `cwd`,
  `$CLAUDE_PROJECT_DIR`, then the process's cwd; sends stdout nowhere at the Python
  level **and at fd 1** (a solver or a commit hook writes to the inherited descriptor
  below any `redirect_stdout`), rebuilds the index itself, and prints `{}` when the
  record stood, nothing changed, no board's text is behind and no trace started. It
  waits up to 60 s for the build lock (`ROUND_LOCK_WAIT_S`, hook only), ignores
  `stop_hook_active`, and turns any exception but `KeyboardInterrupt` (`SystemExit`
  included) into one line. Plain `round` is byte-identical to phase 3a; `--commit`
  is explicit and `--hook` implies it.
- **The board-text sentence is said with `--commit` only**, from `page.stamp_states`
  over the round's tokens, naming every board, `intro` and `next` whose stamp is not
  current. The hook's `systemMessage` reaches the person, not the model, so the skill
  has the agent restamp before its turn ends.
- **`init`** lists git's identity as the first person, starts a repository after
  `store.init` (a refused init starts none) and only outside one, gains `--no-git`, and
  prints decision 21's four steps; `--json` gains `git` and its steps carry their
  commands in code spans, so the docs scan reads them.
- **doctor's `people` row** is warn-only, with five states; it is not in
  `test_doctor.ROWS`, because the bracket lists no one on purpose.
- **`packs skills`** prints PACK.md, then each `skills/<name>/SKILL.md` in natural
  order, for the live packs found here or the `--pack` names; `--json` adds `missing`;
  a live pack not found is a FAIL line and exit 1. `pack validate` refuses a
  `skills/<name>/` with no SKILL.md. "Site partials" are a pack's views and the
  evidence its evaluators write; no new format.
- **The skill** keeps its two anchor headings and the cause bullets verbatim
  (`test_prerequisites.PrerequisiteWords`), puts the person's own commands in a fenced
  block (a code span across a line break pairs the wrong backticks), and is held to
  its section order, its command table (the parser's top-level subcommands, both ways)
  and under 260 lines (`test_plugin`).
- **The trace's seam** is `cli._round_start_trace(root, changes) -> str`, called after
  the commit and outside the lock, returning `""` until phase 3b.
- **Only the staging class maps to invariant 8**
  (`test_round.TheRoundCommitStagesOnlyTheProjectsPaths`); the identity, history and
  hook classes are ordinary, and no invariant number is new. The refactor moved
  `cmd_round`'s body to `_round_run`, so `test_round`'s record-access allow list and
  escape scan and `test_check_cache`'s one-helper set name it.
- **The bracket lists no people**, so a round inside nopekit's own repository never
  commits; its cache and round record were regenerated (round 3).
- **The fast tier's pay-back:** the invariant 8 class added ~0.4 s in the tier (~1.5 s
  alone); test_meta.NoGitOutsideVcs and five small non-invariant entries (~1.25 s)
  went to the full suite. Two interleaved pairs measured 123.8 s and 123.1 s wall
  (91.0 and 91.7 s user) against 120.0 s and 134.5 s (88.8 and 97.9 s user) for
  e27e1a7, at load 0.8-1.5: flat within the noise, over the 90 s line as the base is.

**Review of phase 6**, decided while fixing what it found:

- **The hook runs a script, never `python3 -m`.** `hooks.json` runs
  `hooks/stop.py`: `-m` put the session's cwd first on `sys.path`, so a `difflib.py`
  (or `types.py`, `queue.py`, a `nopekit/`) in any repository ran at every turn's end
  and could exit 2, which blocks the turn. The launcher drops every empty or relative
  path entry, puts the plugin's `src` first and turns a failed import into the
  one-line message. `PYTHONSAFEPATH`/`-P` were rejected (3.11+; nopekit supports
  3.10), and `-I` too (it drops user site-packages, so a `pip --user` tool would read
  skipped in the hook and present in the shell).
- **A repository is started only on git's positive "no".** `vcs.in_repository`
  answers True, False, or None with why: False only on git's own "not a git
  repository" with no `.git` in the directory or above it. `init` and `init_repo`
  used `is_repo`, which reads every failure as "no", and under "dubious ownership"
  (WSL's `/mnt/c`, shared drives) or a slow git they nested a repository inside one.
- **The committer is held to `people` too** (`vcs.committer_ident`, why
  `committer-not-listed`), and doctor's `people` row reads it: `committer.*` config or
  `GIT_COMMITTER_*` put an unlisted identity on round commits.
- **`init` lists git's identity only when `store.people_problem` accepts it**: git
  takes an email with a space, the strict reader does not, and the project init wrote
  could not be read. `--json` gains `people_refused`.
- **A directory is never staged**: `vcs.changed_paths` keeps the `/` with which git
  lists an untracked nested repository, and `rounds.stage_plan` names any directory
  as not staged; a clone under `generators/` had been committed as a gitlink.
- **A repository that ignores the project says so**: `init` prints it, and a round
  with nothing staged whose `.nopekit/project.json` git ignores reads why `ignored`,
  not "nothing changed". Only the project file is asked, so a person's own ignore
  rule over a big scan does not turn every quiet round into a message.
- **`/.vscode/launch.json` joins the root ignore block**, so the launch entry `init`
  writes is neither staged nor named on every round, and `git status` comes clean
  after a round (superseding "names `.vscode/launch.json` as not staged" above; the
  bracket's tracked one is unaffected, and its block was regenerated).
- **`unlisted`'s sentence carries no detail** ("git did not answer when asked which
  files changed"): it had printed empty parentheses.
- **`packs skills` prints each pack's `lenses.md`** after its PACK.md (`--json`
  `lenses`); the skill had sent the agent to `packs show`, which prints PACK.md alone.
- **The skill's end of turn runs `nopekit check` before reading and stamping** the
  boards, `intro` and `next`: a stamp digests the tokens as they read, Stale after a
  model edit, and the hook's check moved them past the text. The loop gains
  proposers and `why`.
- **Invariant 8 maps `test_round.RoundsCommitOnlyAsAListedPerson` and
  `test_record_commands.InitStartsARepository`** (superseding "only the staging class"
  above): its text names both clauses. `packs skills`, `round --commit` and the hook
  joined the records sweeps in tests of their own, the hook's index row with a model
  that does not load (the check's own rebuild then never runs).
- **The fast tier's pay-back:** the review added ~1.2 s in the tier (three identity
  rows, two `init` rows, the nested repository) and paid ~1.2 s by parsing each test
  file once in `test_meta.InvariantClassesNeverSkip` (1.8 s to 0.6 s with the modules
  loaded, the same assertions). Two interleaved pairs on a loaded machine measured
  127.2 s and 138.7 s wall against 161.2 s and 149.4 s for 63e9cef (`tests/fast.py`).

**Phase 3b** (the trace and a transit layout), decided while building it (the data
side):

- **Overturns phase 5's straight tracks.** After the author's verdict on the phase 5
  map ("so simplistic looking"), the map gains a middle: every zone drawn, feeds from
  zones to stations, stations in layers, and lines that ride stations in the trace's
  order. Per-project routing is avoided: the order is baked from observed data and a
  fixed rule, and the geometry is the renderer's (the template side's list).
- **`derivation.py`, not `trace.py`.** "Trace" already names `GateTrace`/`ParamTrace`
  in code. Non-spine and clock-free; no spine edit, so no verdict re-keys.
- **The key.** `seal({harness, step, instructions, choices})`: the instructions are
  `_model_code` plus every `*.py` under the model's directory, spelled as
  `_model_code` spells them (bare under the root: the first cut spelled the extra
  files `<root>/…` and doubled the entry); every `Config` field's digest, skipped
  ones included; computed after a build wherever it is computed.
- **Moves.** Ints move by `max(1, round(0.1·|v|))`; the skip tokens are `yes-no,
  word, none, structure, zero, not-finite, fixed`; relative changes to 4 s.f., a zero
  base as `abs`, a structure by 16 hex of its digest; a raise as its type only. The
  base is built twice; a value that differs is unsteady and draws no edge. One
  BLAS/OpenMP thread per build.
- **Written only whole.** A trace file only when every numeric choice moved both ways;
  scratch under `.nopekit/cache/trace/<k16>/` resumes a run; a timeout, a dead
  worker, a raise that differs when run alone, or a write into the project leaves no
  tracked file and exits 1 — a write by the base build too (the design refused it
  with exit 2; it is the same refusal, so it reads the same). Base-1 runs alone first
  (its key decides the scratch and whether anything is to do), then base-2 and the
  moves in the pool.
- **Copies.** One snapshot per run under the project lock (`.git`, `out/`, `site/`,
  `.nopekit/{out,obs,cache,trace}`, `*.lock` there, `__pycache__`, `node_modules` and
  venvs left out; ignored files kept), then a fresh copy per job; an audit hook,
  active per thread, names a write into the project.
- **Defaults.** Workers `min(8, cpu−1)`; timeout `max(900 s, 10× the base build)`.
- **The `after` rule** is a strict subset of movers with a two-mover floor,
  Hasse-reduced on classes, computed on read and never stored. Not tuned per project.
- **A station's depth is the upper median of its read members' depths** (of every
  moved member when no line reads one). The design said the lower median of every
  moved member; on PETGasus that put Total thrust (members at depths 0, 0, 2, 6, 6)
  in layer 2, before Printed frame and Total weight, though the value C1 reads there
  (thrust to weight) comes after both. With the read members' upper median the
  layers are Layout 0, Assembly and Hover 1, Printed frame 2, Total weight and Frame
  vibration 3, Total thrust 4.
- **A trace is served only for the current key.** The map is untraced until the
  background trace lands, and says so. The trace's unmoved values are value nodes, so
  `why` can say "not seen to move" of them.
- **Amber** is evaluated in the worker builds of the assumption-sourced choices'
  moves (and base-1 evaluates the unmoved design, so `model trace`'s summary names
  as flipping only a claim Checked there), for every automated claim with a covering
  evaluator, by `verdicts.sweep(record=False)` in the copy and the one composition;
  flips are `failing` or `gap`. Its file is never rewritten; each claim carries its
  acceptance and evaluator digests, read back per claim. **Amber does not move a
  board's stamp** (the design said it would): stamps are computed by `site stamp`,
  `status`, `why` and `doctor` from statuses alone, and threading amber into all of
  them for a display token was not worth a second home for it.
- **Graph schema 2**: the `after` edge type, `trace: {key, round}`, derives and after
  inline in the round record; `rests_on` closes over `derives` only. The round record
  gains `computed_from.trace` and `amber`; its schema stays 1.
- **map.json `MAP_SCHEMA` 2** (the data the renderer reads): requirements and zones
  are no longer stops; every zone record is drawn with `count`, `moved`, `ground`
  (phase 5's three) and `sources` (`chosen`, `datasheet`, `estimate`, `assumption`,
  from the records: an assumption claim, an input of a published or measured kind,
  any other input, nothing — `page.SOURCE_OF_KIND`), `estimate` (its unconfirmed
  assumptions), `idle`, `feeds` and `taps`; feeds are the direct derives grouped zone
  × station with their `estimate`; a line carries `stops` (read and `upstream`
  stations in layer, column and station order), `stop_kind`, `taps`, `fed_by`,
  `serves`, `board` and `amber`; a station `layer`, `column`, `depth`, `head`,
  `fed_by`, `stop_kinds`. **Crossings**: zones, requirements and each layer's
  stations carry a `rank` from one barycentre pass against the claims' rows (ties in
  record order); on PETGasus the zones' pass cuts the crossings between feeds (zone
  rank against each station's mean row) from 281 in record order to 206, and the build
  reports both counts (`site build --json` `map.crossings`).
- **Status** prints a `trace:` line only when the trace is not current, and an
  `amber:` line only for flips, not-current and unclear claims (an untraced claim is
  named only when the design has a trace: otherwise the `trace:` line says it). The
  report's `## Amber` only for flips and not-current. JUnit, `check`, `export` and exit
  codes never read amber.
- **The background start.** `NOPEKIT_TRACE_BACKGROUND=0` turns it off; `tests/_env`
  sets it in its own process too, so an in-process `round` in a test starts nothing.
  `round` does not start again after an incomplete background run, and never kills a
  running trace.
- **Invariant 16** is "The trace says what it saw, and no more", with clauses in 7, 8,
  12 and 15.
- **The bracket** gains the `section` station, a committed trace (11 numeric choices
  moved, `material` skipped as a word, `E`, `material` and `yield` not seen to move;
  55 derives, 27 after, 5 ties, 2 singles) and an amber file (C2 and C3 hold: the
  load moved ±10% gives a utilisation of 0.22/0.269 and a bearing stress of
  0.175/0.214 MPa), labelled round 2, and round 3. All its stations sit in one layer
  (each reads at depth 0): the bracket shows an upstream stop (Arm section on C1, C2,
  C4), not layers.
- **The fast tier**: the phase added ~2.1 s of in-process rows and paid ~3.6 s
  (test_meta.InvariantClassesNeverSkip and test_meta.NoGitOutsideVcs, harness guards,
  to the full suite); interleaved pairs against e27e1a7 under a load of 1.5-3.0 read
  +8 s and -5 s wall — within the noise, and no idle run was had, so the 90 s line is
  not shown met (`tests/fast.py`).
- **PETGasus** (a scratch copy, test data only, with its six assumptions written as
  `params/<name>.json` `grounded_by` records — the copy had none, so nothing rested on
  an assumption): 106 of 110 choices moved (three yes/no and `plate_layout`, a word,
  skipped), 214 builds on 8 workers in 19 min 38 s; 56 derived values moved, 9 not
  seen to move; 892 derives, 160 after, 7 ties, 4 singles; a 68 KB trace and a 39 KB
  amber file. The map: 10 zones (Screws and Parts list idle), 39 feeds, 10 stations in
  5 layers; C1 rides Layout, Assembly, Printed frame, Total weight, Total thrust — the
  reference shows Layout, Frame, Mass, Thrust, so Assembly is the one stop it does not
  draw (an upstream stop: the frame's volume moves with the interference the assembly
  computes). Amber: C1 flips at `duct_thrust_factor` -10% (A1: thrust to weight 2.72
  against 3), C4 at `fc_board_side` +10% (A4: first mode 199.3 Hz against 200), C5 at
  `motor_h` -10% (A3: an interference), C6 at `motor_body_d` -10% (A3: the mesh is no
  longer a solid); C2, C3 and C12 hold.

**Phase 3b**, decided while building it (the template side: `lib/map.js`, `map.css`,
`lib/drawer.js`, `page.js`):

- **The transit drawing.** Three columns: requirement cards and every zone box on
  the left (x 12-248), the stations in the middle (x 286-800), the boards on the right
  (x 872-1392, unchanged). A station's x is its (layer, column) of `map.layers`, one
  equal column each (64 units on PETGasus's 8). Its lines sit side by side at a pitch
  of 7 in the order of their rows, centred on the mean of those rows; capsules in one
  column keep 34 apart. A line starts 22 left of its first station, bends once just
  after each station it leaves, and fans into its row in a band from the last column
  to x 854; in each bend zone risers turn top-first and fallers bottom-first (pitch 7,
  shrinking to 3 when crowded), with 45-degree runs of 9 and corners of radius 10 (the
  reference's `trunkPath` and `rounded`). *Rejected:* the design's 300-840 middle (it
  left 33 units for the final fan of ten lines), and bending just before the next
  station (lines then ran at their old height through the stations between, and
  crossed more capsules).
- **Zone boxes sit where they feed.** A box (236 x 46, grown with its text below 1400
  px) sits at the mean height of the stations and line starts it feeds, in its baked
  `rank`, pushed down 10 below the box above; idle boxes last, faded. *Rejected:* the
  design's even pitch down the column (Parts list sat 900 units from anything it
  feeds). Below the boxes: the summary, a key of the four `sources` and the feeds key,
  stacked as wrapped.
- **Halos and amber.** Every line has a surface-coloured halo under its stroke, drawn
  line by line in row order, so a crossing reads over and under. The amber band (11
  wide, 0.7) sits between a line's own halo and its stroke: in the casing layer under
  all tracks, the halos cut each band into two thin yellow edges.
- **Station titles go where they cross least.** A title tries above, below, then
  beside its capsule (fourteen spots) and takes the one crossing the fewest
  tracks and no other title or capsule; it is shortened (room 3 columns, 180 at
  least) only when no spot holds it. *Rejected:* phase 5's "above, nudged up twice":
  on the bundled map a line passing just above a capsule struck its title through.
- **Requirement cards** are three lines tall whatever their text (a measured width
  never moves a box); a row's second line names the requirements it serves, and an
  amber diamond when the line carries the mark.
- **Hover** lights, for a line, its stations' feeds and its zones; for a station,
  every line through it and its feeds; for a zone, its feeds, taps and every line it
  feeds; for an assumption's diamond or row, the feeds it sets. A click opens the
  drawer, which keeps the same set lit.
- **The caption** over the drawing is `words.trace.caption`, written by `page.js`
  (`index.html` is the project's own); the note under it is `map.trace.notice`,
  `map.trace.text` and, while a trace runs, `words.trace.running`. The legend's amber
  chip shows only when a line carries the mark.
- **Not drawn:** the station drawer's "comes after / comes before" — map.json carries
  no `after` edges (`page.MAP_EDGE_TYPES` drops them), and the renderer computes no
  order; it lists the claims for which the station is an upstream stop instead.
- **What the screenshots show** (PETGasus copy with its trace, 1400 wide): all 10
  zones with counts, bars and the six assumption diamonds; 39 feeds and 5 taps; 10
  stations in 8 columns; C1 rides Layout, Assembly, Printed frame, Total weight, Total
  thrust (Assembly is the stop the reference does not draw); the trunk bundles seven
  lines from Layout through Assembly and fans to three boards; C1, C4, C5 and C6 wear
  the amber band from the station their flip enters (C1 from Total thrust). The
  bracket: four cards, four zones, three stations in one layer, C1, C2 and C4 riding
  Arm section as an upstream (hollow) stop. Ties 7 and singles 4 on PETGasus, 5 and 2
  on the bracket (the data side's counts).

**Phase 3b, its review** (twenty-five confirmed findings, fixed before the merge):

- **The key holds the data `build()` read.** The worker records every file its base
  build opened to read inside its copy (not `.nopekit/`, not Python, not a file the
  build also wrote) as `keyed_on.data`, and `trace_for` serves a trace only while its
  instructions and choices are the current ones and those files hold the same bytes on
  disk. A reader cannot name such a key, so it scans `.nopekit/trace/`; the scratch
  lives under the key with no data (`current_key`), each row naming its full key.
  *Rejected:* the base build's derived values in the key (the worker builds with one
  BLAS thread, a reader with many: a last-bit difference would serve no trace).
- **`HARNESS` 2.** An integer moves toward zero and away by its own size, direction 0
  the factor below one for both signs; a field annotated `float` moves as a real
  whatever its value's type; the trace stores a non-10% move's `change`, and every
  label says it (`step_word`: `n_bolts -50%`). Two equal numbers are no move. A file a
  value names (absolute, or relative with a separator), a mesh and an array compare by
  their bytes (`_content`): on PETGasus `mesh_path`, `meshes` and `assembly_parts` now
  move, and the not-seen-to-move values fell from nine to five.
- **The order.** A move under `FAINT` (0.1%) does not count toward `after`; Y comes
  before X when Y's strong movers (two at least) all move X and X has more movers,
  strong or of any size; a cycle is dropped, the rest reduced to its covering edges.
  Both "more" readings are needed on PETGasus: a rounded hover power drops the
  clearances' 0.022% moves of the weight, and the weight dilutes the frame's strong
  moves into faint ones. C3 now rides Layout, Assembly, Printed frame, Total weight,
  Hover power; C1 Layout, Assembly, Printed frame, Total weight, Total thrust. An
  upstream stop after a line's last read stop is not drawn.
- **Scratch rows** are written only for a pair that wrote nothing into the project and
  whose raises were confirmed alone, and an amber row only reused at the subjects
  (`amber_for`, sealed) it ran against. While an in-process job runs, the run's other
  threads open no file (`_IN_PROCESS`, re-entrant: `modelio`'s import recording is
  process-global, and a test row flaked on it). A subprocess worker watches every
  thread; a process the build spawns is a named limit.
- **Amber** stores the claim's acceptance in each run (`accept`), and a flip the
  cross-check decided is said against it ("0.214 against 0.2 MPa"); the reason amber
  is not current is read off the closest file.
- **The map.** `trace.unmoved` and its sentence; a zone is `idle` only when the trace
  measured every member, else it lists them as `unmeasured`; an assumption's row
  draws springs to the stations its choices move (`sets`; a physical claim's, to those
  of the assumptions it resolves — PETGasus records none, so its five P rows stay
  stubs); a line that joins nothing and reads an unmoved value is `unseen`; the source
  `unrecorded` replaces "design choice" for a value whose records name no loser
  (PETGasus records few: most of its bars read *no source recorded*), and the key shows
  only the sources some bar draws; a station's head is its most-read finite number,
  shown only with units its param or an evaluator's verdict gave; the inferred part of
  a line is faded; hovering an assumption lights every claim resting on it; the phone
  list names upstream stops apart. **Not changed:** the `after` rule still orders two
  values that share their inputs (the bracket's slenderness before its bounding box):
  no observation at one point tells them apart, so it is said as inferred and drawn
  faded.
- **Re-measured.** The bracket: same counts (55 derives, 27 after, 5 ties, 2 singles),
  re-recorded as `e3c84dbb3b68ac62`, labelled round 3, and round 4. PETGasus: 214 builds
  on 8 workers in 14 min 49 s; 60 derived values moved, 5 not seen to move; 1067
  derives, 134 after, 6 ties, 4 singles; 31 data files keyed (its meshes and topology
  cache); the map has 30 feeds, 10 stations in 7 layers, no idle zone; amber flips C1,
  C4, C5, C6 and C7.

**Phase 3b merged into phase 6** (built in parallel from e27e1a7), decided while
joining them:

- **The trace's seam takes the round's resolution.** `cli._round_start_trace(root,
  done, *, traced, view, resolution, registry, built, composed, model_error) -> dict`,
  where phase 6 left `(root, changes) -> str`: phase 3b's start asks
  `derivation.needed` over the round's own trace read, graph and statuses (amber needs
  them), and recomputing them after the lock would resolve the project twice. It still
  runs after the lock and the commit, and starts on phase 3b's rule (no trace for the
  current key, or amber not current), not on `changes.instructions`, which a choice's
  or a data file's edit never moves. A refusal inside it reads `failed`: a round never
  exits 2. `test_round.TheTraceStartsAfterTheCommit` holds the order and the hook's
  message; the trace's own `-m` start from the project's root is a named limit.
- **One home for the trace's words.** `report.round_trace_sentence`, factored out of
  `round_paragraph` (which keeps `trace=`), is appended after the commit's and the
  board text's sentences; `round --json` keeps phase 3b's `trace` object
  (`RoundOutcome.trace_state`) beside phase 6's `commit`. The hook prints `{}` only
  when there is no trace sentence: a trace started, re-running amber, running, stopped
  short or refused reaches the person.
- **The skill stays under 260 lines.** Phase 3b's paragraph became a loop bullet
  (`round` starts the trace; run it yourself after a gate or acceptance edit; never
  edit `.nopekit/trace/`), amber joined "Never move a limit", and the table names
  `model [trace]`. To pay for it, the table's `packs list` and `site stamp` rows went
  (the loop and the turn's end spell both out), the claim example's `requirements`
  joined the line above it, the review bullet takes two lines, and the two one-line
  rules share a paragraph. *Rejected:* raising the limit (phase 6 set it so the skill
  is read whole).
- **The bracket's round record is round 5**, regenerated from phase 3b's (round 4):
  one past the higher of the two, so no new round number names two records in the
  history. Its verdict cache is phase 6's (no spine module moved on phase 3b's side),
  and its trace and amber stayed current (`model trace` found nothing to observe);
  they keep their label, round 3 of phase 3b's line, since a trace file is never
  rewritten.
- **The fast tier** is the union of both lists: both phases paid with
  test_meta.NoGitOutsideVcs, and phase 3b's move of test_meta.InvariantClassesNeverSkip
  to the full suite leaves phase 6's speed-up of it outside the tier. Two interleaved
  pairs measured about 4 s over 3d8eaaf (122.9 s and 119.5 s against 118.1 s and
  114.4 s wall, load 1.4-2.4): the next change that touches the tier pays that first
  (`tests/fast.py`).

**Follow-up after phase 3b** (three small fixes), decided while making them:

- **The site says a built part.** `HUMAN["site"]` says *a built part* where the CLI
  says *article* (*part <a12>* for a hash, *Parts to build again*); GLOSSARY §1 forbids
  *real part* and *built object*, not this (`trace.settles` said *the real part*). With
  it: "showed that it can fail" for *qualified*; "does not count" for an unqualified
  evaluator's outcome (*Rejected:* "not shown to fail", which reads as a pass); "the
  current design" for "the current inputs"; "the results in .nopekit/verdicts/" for
  "the verdict cache"; "shows that each one can fail" for "their controls"; no
  "registered". The status words and *evaluator* stay, and the CLI, REPORT.md and
  JUnit keep their words.
- **The hardware clause has a site register**
  (`report._hardware_clause(register="site")`): phase 5 shared it because it passed
  the style scan, and the page said "1 claim needs an article". Its rebuild part and
  next steps' row share `report.site_rebuild_text` (`rebuild_bare` when nothing is
  named as moved: the row had read "These values moved: the design").
  `TheHardwareClauseIsAlwaysSaid` reads each register's needles, and its planted drop
  removes both forms.
- **A recorded result in the drawer is the site's words**:
  `report.result_facts(register="site")`, the CLI's branches and the same `counts`;
  `state.json` keeps the CLI's. `heading`, `why` and `recorded` moved to
  `page.WORD_KEYS`, so the style scan reads them. *Rejected:* exempt as a shared
  producer (the drawer said "a new article is needed, not a rerun" after an em-dash).
- **`test_style.TheSiteSaysABuiltPart`** fails on *article* in any `HUMAN["site"]`
  string, in the words baked for the bracket and three seeded ledgers chosen to reach
  every clause (`ARTICLE_SEEDS`), and in any template string (`lib/format.js`'s blurb).
- **The background trace and its workers start in nopekit's own directory**
  (`derivation._package_parent`, already first on their `PYTHONPATH`), the project
  named by `-C`; a worker moves into its copy itself (`worker_main`). The request named
  the start alone, but a planted `difflib.py` was imported by the workers too, from
  the copy's root, so the limit is gone only with both. *Rejected:* `-I` and `-P`
  (`hooks/stop.py`'s reasons); a temporary directory (anyone may write a module
  there). Its test runs a real trace, so it is the full suite's.
- **doctor's `people` row with no one listed is formatted**, like every `HUMAN`
  template, and prints `{"name": "...", "email": "..."}`.
- **A recorded result is coloured by its token** (the follow-up's second commit): the
  drawer painted the block in the ok tone on `counts` alone, so a counted fail read
  green beside its Failing chip, the site saying more than the resolution (invariant
  14). `report.result_facts` gains `token` (`RESULT_TOKENS`: `pass` a pass that
  counts, `fail` a fail that counts, `not-counted` any other) in both registers, so
  `page.json`'s block, `state.json`'s `physical_result` and the JSON channels carry
  one producer's answer; the site register adds its `word`. The drawer (so the floor)
  and the technical record take glyph and tone from format.js's outcome table
  (`outcomeLook`), the one place a result's tone is said, and no page script reads
  `counts` (a `BROWSER_TRUTH` rule). `page.status_problems` holds the block's token
  and word to `result_facts`. *Rejected:* the bare outcome, `pass` or `fail` (a pass
  that does not count would read green, critique 3 of the P2.5a design); two flags
  the browser combines (SPEC 32).
- **No spine module moved**: the bracket's cache, round record and trace stand. The
  new class costs ~0.18 s in the fast tier, ~0.1 s of it paid by reading the
  template's strings once per process; the ~4 s the merge left is not paid here. One
  unpaired run at load 0.8-2.7: 106.2 s wall, 76.2 s user, 692 tests (`tests/fast.py`).
  The second commit adds ~0.15 s (a counted fail recorded and baked, ~0.1 s warm, and
  the template's token row): 101.5 s wall, 73.8 s user, 694 tests at load 0.2-1.2.

**Follow-up after the acceptance run** (a fresh sandboxed agent used the plugin for a
person's request; three findings), decided while fixing them:

- **A round stages every file git does not ignore inside the project's directory**
  (superseding phase 6's closed list). What slipped through: the agent made its own
  `bom/bom.json`, the parts list its sourcing evaluators read, and the meshes its
  generator wrote under `build/`, and every round said "Not staged, outside the
  project's paths: bom/bom.json, …", so the history held verdicts on a parts list it
  never held. Still the subtree only (`vcs.changed_paths` asks git about `.`), still
  `git commit --only` with the pathspec list, every refusal as before; the project's
  ignore files decide what is a source (`init`'s marked blocks keep nopekit's outputs
  out). Named instead of staged: a directory (an untracked nested repository), a file
  over 50 MiB, and a name that looks like a secret (`rounds.ROUND_SECRET_NAMES`: `.env`,
  `.env.*`, `*.pem`, `*.key`, `*.p12`, `*.pfx`, OpenSSH's four default key names,
  `*credentials*`, `*secret*`, matched against each part of the path, case ignored,
  never under `.nopekit/verdicts/` or `.nopekit/trace/`, whose names nopekit makes from
  evaluator ids and digests: a gate called `cad.key` keeps its verdicts); a deletion is
  staged whatever its name (it publishes no byte). `StagePlan` and `CommitOutcome` carry
  `not_files` and `secret` where they carried `outside`, and so does `round --json`'s
  `commit`; a subject calls a directory nopekit does not name "other files"; nothing
  staged beside something named reads "Nothing to commit but what a round never stages",
  never "no file changed". *Rejected:* widening the list (`bom/`, `build/`: the next
  project's directory has another name); staging only what a record names (no record
  names the parts list: an evaluator reads it); a content scan for secrets (every
  changed byte read at every turn's end, and a key in a CSV still missed); `*token*` and
  `*password*` (design words as often as secrets). Its known risk is in SPINE_CONTRACT's
  limits: an environment left unignored (`.venv/`, `node_modules/`) is now committed;
  `init`'s block names none, and the skill tells the agent to ignore scratch, downloads
  and environments.
- **`site serve --detach` and `--stop`**: the live site outlives the shell that started
  it. What slipped through: the agent started `site serve --watch` as a background task,
  which there lives at most two hours, and under headless `claude -p` a running
  background task keeps the turn from ending. `--detach` starts the hidden
  `site serve --detached-server` in a new session (`start_new_session`), stdin
  `/dev/null`, its output appended to `.nopekit/out/serve.log`, from nopekit's own
  directory (the background trace's rule), and exits 0 once the server has recorded its
  pid, address and URL in `.nopekit/out/serve.json`; it prints the ready line and opens
  no browser (one started from here would hold the caller's descriptors). The server
  holds `.nopekit/out/serve.lock` (`flock`) for its life, and `running` needs the lock
  and a live pid that leads its own session, so a stale record (from any host name: a
  container's changes at every start) never makes `--stop` signal a pid the OS gave to
  another process. A second `--detach` prints the running URL and starts nothing;
  `--stop` sends SIGTERM (the server stops between requests; a first build in flight
  finishes), then SIGKILL after 15 s; `site status` prints a `live:` line and `--json` a
  `server` object; `doctor`'s `site` row names a running server. The server stops itself
  within 2 s once its record no longer names it or `site/index.html` is gone (a deleted
  test project, a `git clean -X`), and logs only failed requests (a live page polls
  every 2 s). POSIX only; on Windows both flags say so in one sentence. The code is in
  `cli.py`, not `site.py`: fdm-print's and cad-solid's gates import `nopekit.site` (its
  `MOVER_SEPARATOR`), so a code edit there re-keys every one of their verdicts in every
  project (the first draft put it there, and the acceptance project's copy re-ran six
  evaluators for a live-server helper). *Rejected:* the pid alone as the witness (the OS
  reuses it, after a reboot at the latest); an HTTP probe of the port (a busy server
  reads dead, and any listener there reads alive); `nohup … &` in the skill
  (shell-specific, and it keeps the caller's pipes unless every descriptor is
  redirected); a double fork (`os.fork` in a process that may run threads).
- **The skill's map guidance**, in section 3's paragraph on groups, within the line
  budget: draw the map in `groups/<id>.json` records, then `site build` — a board per
  thing the person cares about, holding its claims; a zone per part of the thing as the
  person names it, never every choice in one; a station that gives a shared value its
  plain title, which is what the map shows. What slipped through: the agent wrote boards
  only, so every choice drew in one generated zone and every shared value under its raw
  name. The examples are the bracket's (a whole board record, "Holds the pump", and
  `"Allowed stress"` for `design_stress`), not the acceptance run's, so the shipped
  skill carries no prior of that request. Paid for, to 258 of the 260 lines: the rules'
  basis line (the loop's Basis bullet says it), the blank line between the last two
  rules, the table's `claim physical` row (the person's block shows it), two reference
  lines and a shorter URL paragraph.
  `test_plugin.TheSkillTeachesTheFirstSession` holds the detached start, the map's
  three kinds and what a round commits, each in its section.
- **No spine module moved**, and `site.py`'s code did not either: the bracket's cache,
  round record and trace stand. `test_round.TheRoundCommitStagesOnlyTheProjectsPaths`
  keeps its name, its invariant 8 row and its place in the fast tier, at its old cost
  (~0.8 s: the secret names, the plan and the words need no git, and the wording a third
  round had checked moved into the words row); `test_serve` runs real processes (~9 s)
  and is the full suite's. One pair at load 0.9-1.3: 121.6 s wall against 123.5 s for
  a6c5f22 (`tests/fast.py`).

**The pcb-kicad pack** (2026-10-16, the eighth bundled pack), decided while building
it:

- **Bases**: `pcb.drc` and `pcb.unconnected` `established` (the verdict is
  `kicad-cli pcb drc`'s exit code and its report's count); `pcb.pin_names` and
  `pcb.net_width` `sourced` (KiCad's library pin names, copied onto the board's pads as
  `pinfunction`; IPC-2221's heating fit); `pcb.board_readable` `model-only` (the pack's
  own reader judging its own reach).
- **The tier-0 half reads KiCad's files with a standard-library reader**
  (`gates/_kicad.py`), not with KiCad's `pcbnew` module: `pcbnew` opens the board in
  C++, where the trace sees no read, so a verdict would be keyed on nothing. Its gates
  run in CI. *Rejected:* `pcb.net_width` as a `.kicad_dru` width rule that DRC
  enforces (established, but opaque, tier 1 and absent from CI).
- **The tier-1 half is opaque** (`subprocess:kicad-cli`): it runs on a copy staged under
  the gate's out directory, so kicad-cli writes nothing in the project and the copy
  keys the board, its `.kicad_pro` and its `.kicad_dru`. `pcb.drc` runs every severity
  by default; a waiver is KiCad's own exclusion in the `.kicad_pro`. An exit code that
  disagrees with the report is an error.
- **`pcb.board_readable` is the guard** of the two tier-0 analyses: a file older than
  KiCad 6 or newer than KiCad 9's format, or a track naming a net code the table does
  not define, is a board whose nets the reader may misattribute. `test_packs.EDGES`
  gains the two edges (25), and the tool-free floor rises to 19.
- **The known-bad boards are generated at fixture time** from the committed baseline,
  one change each, by the pack's own reader, under the caller's `out_dir`;
  `narrow_power` derives its width from the baseline's own limit. The baseline board
  itself is committed, with `selftest/assets/make_baseline.py` recording how `pcbnew`
  made it from KiCad's stock libraries.
- **The bundled mutation walk** gains five gates (59): `pcb.net_width` with a conclusive
  mutation (35 in all), and the four that report 0 against 0, or no value, with no
  boundary (21). No mutation passes any of them.
- **Phase 2, the schematic** (2026-10-17): `pcb.erc` (`kicad-cli sch erc`, its exit code)
  and `pcb.parity` (`pcb drc --schematic-parity`, the report's parity count; the exit
  code must agree) are `established` and opaque, staged with every sheet the root
  names, the `.kicad_pro` and the project's `sym-lib-table` and `fp-lib-table`. The
  baseline gains its schematic, written by `make_schematic.py` from the stock symbols
  with fixed UUIDs, and the board's footprints link to it. Neither needs the guard:
  KiCad reads both files itself. *What slipped through while building it:* ERC on a
  host whose KiCad was never configured warned about every symbol's library, so the
  baseline would have passed on one machine and failed on another; the project's
  own library tables seal it. And `pcbnew.FootprintLoad` leaves a footprint with no
  library name, which parity reads as a footprint the symbol did not ask for. The
  walk gains two no-boundary gates (61; 23).
- **Phase 3, the router** (2026-10-17): `pcb.autoroute`, the first bundled proposer,
  basis `established` (Freerouting's routes, KiCad's DSN export and session import).
  It runs `pcbnew` in a separate Python that has it (`_pcbnew_steps.py`), never in
  nopekit's, on a copy outside the project, and writes only the routed board and a
  summary into the proposal directory: `pcbnew.SaveBoard` writes a `.kicad_pro` and a
  `.kicad_prl` beside what it saves, and the first run recorded both as inputs. One
  thread; two runs on the baseline drew the same copper in a different file order.
  Freerouting's analytics are off; its update check cannot be. *Rejected:* routing
  as an evaluator (copper drawn by the thing judging it), and a gap closer after the
  router (the source pipeline's drew shorts). `test_proposers` holds its refusal
  without its tools, which is every CI host.
- **Phase 4, beyond the copper** (2026-10-17): three opaque tier-1 gates.
  `pcb.fab_rules` (`sourced`): a fab's published minimums (`gates/fab/jlcpcb.json`,
  each number quoted with the day it was read) written as the staged board's only
  custom rules, the project's own minimums zeroed in the staged project file, and
  findings counted by the types those rules raise. *What slipped through while
  building it:* KiCad 9.0.9 reported the fab rule's 0.1 mm as "board setup
  constraints", so a count by rule name would have read zero; and an unconditioned
  silkscreen clearance flagged silk over every track. `pcb.spice` (`established`):
  `ngspice -b` on the project's deck, one `.meas` against a band; a line ngspice
  ignored (it warns and solves the rest) is an error, as is a measurement it could
  not make. `pcb.enclosure_fit` (`established`): kicad-cli's STEP export against the
  enclosure's solid, OpenCascade's shared volume and distance, in a child process.
  *What slipped through while building it:* `kicad-cli pcb export step` drops a part
  whose 3D model it cannot find, silently and with exit 0, and this machine has no
  3D library; a fit read from that export would have read clear with no parts. So
  every placed part must name a model and arrive in the export under its
  reference, or the gate skips, and the baseline keeps KiCad's two models beside
  it (CC-BY-SA 4.0 with the library exception, `selftest/assets/3d/README.md`).
  The walk gains the three (64): two conclusive (37), one with no boundary (24).
- **Follow-up after two real boards** (2026-10-17, GitHub #9 to #11): the pack's
  first run on real boards (script-generated, 4 layers, no schematic) found three
  faults its baseline could not show, and two rounds of review of their fixes
  found more, three of them false passes older than the fixes. (a) `pcb.fab_rules` counts findings by the types its rules raise *except
  silk on silk*, which KiCad reports as `silk_overlap` too: such a finding is left
  to `pcb.drc` only when it is shown to be silk on silk (no item is a pad by id,
  its words do not name the run's `fab_silk_to_pad` rule, and every pad on the
  board has an id KiCad keeps; otherwise every silk overlap counts), and a fab
  file's silk rule must be silk to pad. In the fab run, a constrained type the
  project set to "ignore" is raised to "error", the board's minimum clearance is
  the fab's own (the only floor KiCad holds a local clearance override to, so a
  fab row has one unconditioned clearance rule; an override above the fab's
  minimum still binds, a false fail filed as GitHub #13), and a microvia's hole
  breach (`microvia_drill_out_of_range`) and two holes on one centre
  (`holes_co_located`) are the fab's. A project file KiCad could not read, which
  it would drop with the floor, is an error. The detail line groups
  findings by type with the fab rule behind each. *What slipped through:* the 18
  silk-on-silk overlaps `pcb.drc` found were counted as past JLCPCB's limits (190
  findings where 172 were the fab's); the first fix matched pads on the file's
  text alone, and in review a silk line across a pad with no id, an uppercase id
  or a legacy stamp passed; and, older than these fixes, an "ignore" severity hid
  the fab's own rule, a pad's `(clearance 0.01)` passed a 0.047 mm gap under the
  fab's 0.1 mm, a 0.12 mm microvia drill passed its 0.15 mm via hole, and a via
  on top of another passed.
  (b) `copper-connectivity` binds `pcb.unconnected` alone. *What slipped
  through:* `connectivity` also binds `pcb.parity`, which skips with no
  schematic, so "every connection is made" read Skipped beside a pass.
  (c) `pcb.net_width`'s line names the hottest stretch (a net's tracks at that
  width on that side of the stack, their layers and length, the same copper once,
  an arc along its arc), up to four stretches as hot after it and then how many
  more, and holds IPC-2221's fitted range over each. *What slipped through:* it
  called the hottest track "narrowest", named one net of a tie, and the reader
  took an arc's chord as its length.

**The cfd-openfoam pack** (2026-10-18, the ninth bundled pack, phase 1 of GitHub
#15), decided while building it:

- **The tool is ESI OpenFOAM v2606, one API, run as an outside tool.** An install is
  found from the `openfoam2606` wrapper on PATH alone (its `exec "<install>"/etc/openfoam`
  line), the same thing `requires_tools` asks for, and must carry
  `META-INFO/api-info` with `api=2606`; another ESI release and every openfoam.org
  release skip, naming why. *Rejected:* the Foundation's v13 (its own dialect:
  `foamRun`, no `simpleFoam`); any ESI release (a function-object option that moved
  between releases turns a reader's miss into a pass); a `simpleFoam` on PATH as a
  second way in (the first draft's: availability then passed on any release while
  the gate skipped itself, the pattern PACK_FORMAT forbids); the wrapper per call
  (0.4 s each; `etc/bashrc` is sourced from a bare environment, cached with
  functools, which the spine empties before each run).
- **Every run is the pack's, on a staged copy.** The copy holds the case's `0/`,
  `constant/` but its mesh, and `system/`. The pack replaces every `functions` entry
  with function objects of its own and `residualControl` with its target: two
  `surfaceFieldValue` planes, and in `cfd.pressure_drop`'s ladder four, with
  OpenFOAM's `mag` of U and `pressure` (static, times the density) for the site's
  pictures (GitHub #15, Phase 2; *rejected:* the two field objects in every ladder,
  where the density would re-key the guards for a picture). Before the run,
  OpenFOAM's own reading of the copy (`foamDictionary -expand`) must hold exactly
  those, and the nu, laminar model, budget and schemes
  the guard read; simpleFoam's own convergence-criteria block must name the pack's
  target; checkMesh's direction count must be the case's dimension. Each tool runs
  with a private HOME whose user controlDict turns off `allowSystemOperations`, and
  with `FOAM_SIGFPE=true`; a banner without both lines is an error. *What slipped
  through while building it:* checkMesh exits 0 on `Failed 4 mesh checks.`;
  simpleFoam exits 0 and prints `End` out of iterations; a user's
  `~/.OpenFOAM/2606/controlDict` turns trapping off with no warning; the
  `-info-switch allowSystemOperations=0` option is "unregistered" on v2606; checkMesh's
  JSON disagrees with its text; the wrapper's quote sits before `/etc`, and the first
  reader of it found no install.
- **The guard reads a strict subset of OpenFOAM's cases** (`cfd.case_readable`, tier 0,
  `model-only`): exactly eight files (and an inert `decomposeParDict`), no other file
  or directory, no directive but the `#includeEtc "caseDicts/setConstraintTypes"` of ESI's 0/
  files, macros only in `blockMeshDict` and never a dictionary expanded in place,
  quoted keywords only under patch and field names, no code, boundary conditions
  whose value is written in the case, laminar `Stokes`, `momentumPredictor` on,
  planar 2D or 3D. *What slipped through its first draft,*
  found by an adversarial review on the real v2606: the reader judged literal text
  while OpenFOAM expands and MERGES, so a `#sinclude` (or a `$dict;`) merged
  `scaleFactor 0.5` into the pack's function objects and all six gates passed at half
  the pressure drop; an `#if` and an in-case include set the nu the solver ran (Re
  4000 judged as Re 20); an include through a symlinked `$FOAM_CASE` read a file
  outside the case; `"typ(e)" codedFixedValue` and `type $:bcType` passed as
  "nothing compiled"; a constant/fvOptions source halved the pressure drop; a
  power-law laminar model passed as Newtonian; and an isfile probe of a missing
  snappyHexMeshDict made the tier-0 controls opaque, so their claims read Stale
  after every check. A second review found `exprFixedValue` and a
  `uniformFixedValue` table (values evaluated as the run goes) let through, and a
  refusal of symbolic links decided on `is_symlink()`, which no key holds (a cached
  pass was served after a file became a link); links are now read through, and no
  include can follow one out. *Rejected:* a list of files and directives to refuse (each
  release adds one); evaluating OpenFOAM's directives in the reader.
- **A guard chain, six edges** (`test_packs.EDGES`, 31): `cfd.case_readable` before
  `cfd.regime` (tier 0, `sourced`: Re below 2300 on the hydraulic diameter, Mach below
  0.3, reported as the worse as a fraction of its limit) and `cfd.mesh_quality` (tier
  1, `established`: checkMesh's `Mesh OK.` and the project's limits, 70 to 90 degrees
  of non-orthogonality being only a warning to checkMesh); both before
  `cfd.converged` (tier 2, `established`: the converged line, p and U solved at
  target, the stated velocity carried within 2% along the upstream normal, and the
  pressure drop's estimated remaining change under 0.1%: Eca and Hoekstra's 2009
  iterative-error estimate, read pessimistically, in `_iter.py` since GitHub #15
  Phase 0); then `cfd.mesh_independence` (tier 2,
  `sourced`: Celik et al. 2008) and `cfd.pressure_drop` (tier 2, `established`; a
  pressure rise is an error). The tool-free floor rises to 20. *What slipped
  through:* the first stillness test (the change over 10 iterations) passed a run
  relaxed to U 0.03 at 9.7% from converged; its first rewrite read a 2-iteration
  oscillation as still; the second review found its rate fooled by an earlier
  oscillation (a run 0.22% from converged passed) and its round-off floor applied
  before the rate (a creep relaxed to U 1e-4 passed with 0.2% to come); the
  estimate that came after read real v2606 histories settled up to 23.4% from
  converged (a laminar pitzDaily under slow relaxation), and Phase 0 replaced it.
  The estimate now in place cannot see an overshoot near its turning point, a slow
  mode under a fast oscillation, a rate that becomes slower in the last few
  iterations, a drift under 1e-8 of the value per iteration or a change after the
  last iteration (`references/running-openfoam.md` has the measured errors). On
  MARIN's Table 1 it reads 0.71 to 2.00 times the authors' closed tool, whose
  correction (SNH 2006) is still unread. Each gate that runs simpleFoam writes the
  history as CSV and the estimate as `iterative_error_pct` in its summary.
- **Mesh independence refines every cell.** Each level multiplies every block's cell
  counts by the ratio (1.3 to 3, default 2), half up and at least one cell more than
  the level below; only a 2D case's one-cell direction, found from the empty
  patches' geometry, stays one, and Celik's dimension is the mesh's. The GCI is
  believed only on monotone convergence (classified before the order is, since Eq.
  (3a) gives diverging data a positive order), a unique order in [0.95, formal +
  0.05], and an iterative error ten times below eps21. The asymptotic-range ratio is
  never used: from the same three meshes it equals phi1/phi2. Each plane must fall
  on a cell face or centre at every level: OpenFOAM's sampled value is about 1% off
  a quarter of a cell away, and the second review's ladder at r = 1.7 moved the
  planes through the cells, read that error as convergence at order 1.77 and
  passed a band 0.34% under the exact pressure drop. `cfd.pressure_drop` judges
  the top of the band, dp x (1 + GCI). *Rejected:* `refineMesh` (it splits
  cells without moving them onto an arc edge); a refinement of one region (a
  wall-only ladder "converged" with a GCI of 0.013% at a drag 0.37% off the
  benchmark; the first draft froze every count of 1, and a one-cell wall layer
  passed 3% below the exact answer); patches as measurement stations (a
  `zeroGradient` inlet's pressure is the first cell's, first order and carrying the
  entrance loss); a run memo shared between gates (a module-level dict the spine
  cannot empty, which `doctor` names).
- **The baseline is plane Poiseuille flow**, with an exact answer (12 nu U dx / h^2):
  the three meshes read it divided by 1 + 2 / Ny^2 to 3e-6, extrapolate to it within
  0.003%, and the whole selftest takes about 10 s. Its six controls are one dictionary
  entry or one stated value each, made by the pack's own writer, and each passes every
  guard before its gate. `cut_short`'s number is 0.69% high at iteration 5 and 0.001%
  low at 10: only the converged line can refuse it.
- **The bundled mutation walk** gains the six (70): four conclusive (41), one walked
  with none (`cfd.converged`, 4), one with no boundary (`cfd.case_readable`, 25). No
  mutation passes any of them.
- **Phases 2 and 3** (external flow with snappyHexMesh and forces; conjugate heat
  transfer) stay open on #15.

**The two pages** (2026-10-20, decision 40), decided while specifying it, after three
critics read the plan against the code:

- **One `page.json`, no third document.** Every new key (`story`,
  `verdict.headline`, `representation.alternates`, `requirements`, `decisions`,
  `decisions_more`, `losers`) is baked inside `page.bake_page`, so the seal, the
  number pool, `status_problems` and the style scans see it with no new path.
  *Rejected:* building the rows in `site.build` after `bake_page` (they would land
  after the checker ran); reading `state.json`'s params in the browser (filtering
  and ordering what lost is a judgment, and the browser makes none).
- **`site.scaffolded` stays the root `site/index.html`.** `init` always writes the
  root redirect, and an old project already has a root page, so invariant 8's "when
  `site/index.html` exists" keeps its meaning. *Rejected:* "or
  `engineering/index.html`" (it would widen what `round` writes).
- **An old project's root page is never moved or replaced by a build.** `site build`
  writes `engineering/` and `marketing/` beside it and warns, naming the steps (move
  it aside, then `site init`). *Rejected:* `site init --force` in the warning (it
  replaces the engineering, marketing and technical shells too); moving the page
  aside automatically (a site file the person wrote, moved by a build).
- **The two are "pages" in site strings,** labelled Engineering and Marketing (the
  author's words). A *view* is a view kind (`views/*.json`). Whether a visitor-facing
  label should read "Product" or "Overview" is the author's call, left open.
- **The story is a prose subject, `story`, stamped against intro's tokens** (every
  claim's, the readiness and each milestone's), because a story names the whole
  product. `story` joins the reserved group ids. With no story the page shows the
  intro, and with neither it leaves the section out: author notices are build
  warnings, never text on the marketing page.
- **Decisions newest first, five featured on show.** A decision is featured when it
  changed a claim or a value; the first five featured are shown and the rest sit in
  one closed block. Both are filters on the record's structure, never on a status.
- **A rejection's words are the site register's, from the one producer.** `type_word`
  for a preference is the CLI's invariant-13 phrase with a capital, pinned equal by a
  test; `returns_word` carries no record text (the record's `why` has an em-dash in
  the bracket's PLA row), and the record's text goes under `reason` and
  `returns_text`, record keys the style scan leaves alone.
- **What lost to a value is text** (`value_text`), so a losing number never joins the
  number pool and the story cannot quote a loser as if it were a result.
- **The tally chips stay as `page.js` draws them** (a tally item has no marks), moved
  with the verdict block into `lib/verdict.js`, so both pages read the verdict through
  one renderer.
- **The meshopt decoder is vendored** with three.js (`site.vendor_urls`), so a
  compressed GLB loads offline too.

**Figures bound to verdicts** (2026-10-21, phase 1 of GitHub #15's pictures: the
spine and the site, no ParaView), decided after a three-way design panel and a judge,
and while building it:

- **The evidence copy is bound by content, through an ignored per-entry manifest**
  (`.nopekit/cache/evidence/<gate>/<entry>.json`, written in `_sweep_one` step 4,
  after `write_entry`, for a recorded pass or fail). *What slipped through:*
  `evidence_plan` bound by name, so a design that went A, B, A published B's files
  under A. *Rejected:* digests in the tracked entry (kept-first warnings on every
  forced run, from log timestamps); obs rows (pruned at 20, and obs is "never
  truth"); writing the manifest from `cli.check` after the sweep (no bracket
  regeneration, but a later gate's write lands in an earlier gate's manifest);
  `.nopekit/out/entries/` (a gate can write there through `ctx.out_path`).
- **A figure binds through `ViewSpec.evidence_of`, not `View`.** *Rejected:* a
  `models.View` field (a second spine-digest edit, and a strict-reader change for one
  use). A bound record view (a gate's own PNG shown as a bound figure) waits on that
  change.
- **Rendering runs at `site build`, memoised per (gate, entry, view, recipe) by a
  tracked sidecar.** The tool's identity is recorded in the sidecar, never in the key.
  *Rejected:* rendering in `check` (a 2.7 GB optional tool on the judging path, and a
  crash reads like a check failure); an explicit `nopekit render` (forgotten, so the
  page is silently behind); a plain viewgen on every build (seconds per scene on every
  edit); the identity in the key (one teammate's install draws everything again).
- **A bound viewgen gets no model, no parameters and no claim's acceptance.** The
  design said only the acceptance. The model went too: the design now may be another
  design than the served entry's, and a picture drawn from it would be published as
  that entry's evidence.
- **`bind` takes only a digest a verified evidence file holds**, and each file must
  hash to it. The design said only the second. *Rejected:* a digest the viewgen
  computed itself (it would bind whatever is on the disk).
- **The spine renders into ignored scratch, hashes the inputs again after the call,
  and publishes only checked files.** *Rejected:* `write_asset` straight into `site/`
  (a half-checked or oversize file is published until the check runs, and an input
  that moved is not seen).
- **The format checks are the stdlib minimum:** the PNG signature and IHDR, the GLB
  magic, and, for a glTF (from P2.4), JSON whose every buffer and image is a `data:`
  URI, so the page fetches no file the spine did not see. *Rejected:* a PNG codec or a glTF accessor check of our own (the author's
  rule of 2026-10-21: build as little as possible, use trusted libraries); the Khronos
  validator stays a skipIf test for phase 2.
- **A chart's numbers are its evidence's; its limit and point are the floor's.** *What
  slipped through:* `_chart_margins` took the view's own limit. *Rejected:* stripping a
  pack limit silently (a pack bug would read as a working chart).
- **A chart is a Vega-Lite spec the spine bakes whole** (`page.chart_spec`), with none
  of `page.VEGA_FORBIDDEN_KEYS`, so Vega computes only the axes. Vega 6.2.0, Vega-Lite
  6.4.1 and vega-embed 7.1.0 (`site.VEGA_VERSIONS`) load as their pinned UMD files, on
  demand, from `vendor/vega/` or the CDN; without them the card shows the numbers as a
  table. *Rejected:* growing `lib/chart.js` (the author's rule); the ESM builds through
  the import map (`vega.module.js` imports 27 bare names, jsdelivr's `+esm` of
  vega-embed 7.1.0 pulls vega-lite 6.4.3, which nobody pinned, and an existing site's
  shells are never regenerated, so they would never get the line). From P2.5 the
  technical record's chart views draw from a baked spec too, and `lib/chart.js` is
  gone.
- **A field draws its colours as the file holds them** (`lib/viewer3d.js` field mode:
  `MeshBasicMaterial` with vertex colours, `toneMapped` false, no lights,
  `LinearSRGBColorSpace` output, `NoToneMapping`). Measured with pvbatch: ParaView
  6.2's glTF exporter writes COLOR_0 as the colour map's own sRGB bytes. *Rejected:*
  `KHR_materials_unlit` with the default sRGB output (64 drew as 137, measured);
  re-encoding the colours ourselves.
- **Colour ranges are pack factors times a named evidence value; one scale object
  gives the pixels and the HTML legend.** *Rejected:* absolute constants,
  auto-rescale, the claim's limit; a colour bar inside the PNG (two legends that can
  disagree, unreadable on a phone).
- **D6:** one PNG per scene per served entry; a 3D file (GLB or glTF) is committed
  only for an entry an export re-ran; any other is ignored, under `assets/live/`, and named only in
  `build.json`'s `live_figures`. *Rejected:* a `HEAD` probe for the live file (a static
  host shows a button that leads nowhere); a `.live.glb` name in a tracked folder.
- **At most one older folder per view** while the served entry is undrawn; several
  (after a merge) read `none`, with a warning. *Rejected:* choosing by time (obs and
  commit times differ between machines).
- **Why a figure is absent is never baked.** The baked words are fixed per currency,
  and the reason goes to the build warnings. *Rejected:* "pvbatch is not on this
  computer" on the page (a fresh clone would bake other bytes). A figure that reads
  `none` is baked in `page.json` with its chips and sentence; `state.json` has no row
  for it.
- **An older figure is "another result", not "an earlier result".** After A, B, A
  the picture beside A is B's, the later run. `binding_text` is one sentence per
  (gate, entry), naming the full entry, because one claim can show two gates' figures.
- **A figure's provenance line is its `ViewSpec.description`**, page text already, so
  the sidecar schema needs no key for it.
- **The caption check refuses every status word as a whole word**, "gap", "open" and
  "stale" in their plain sense included, and the build and G7 read one rule
  (`page.caption_status`). Narrowing it is the author's call.
- **Placement:** one card per claim with figures, before the board's prose; the drawer
  gets one link line, never the pictures. *Rejected:* cards between the prose and the
  floor (the thirty-second questions come first); cards in the drawer (duplicated
  images).
- **No example CFD project is committed.** Its verdict cache would need regenerating on
  every spine edit with a real v2606, which CI lacks.
- **Invariant 14 grows** the figure sentence, held by the `test_figures.py` classes in
  its row.
- **Phase 2, P2.1 (built):** `cfd.pressure_drop`'s summary holds every number the
  pictures draw, each the gate's or OpenFOAM's: per mesh `h_rel` and `dp_pa`,
  `h_rel_ext` (0, where the Richardson value sits), `u_upstream_m_s`, `p_up_pa`,
  `p_down_pa`, the `planes`, and `fields` (the finest level's time and the sha256 of
  its `U`, `p`, `mag(U)`, `static(p)` and mesh files, which a picture binds and the
  site never copies). Two charts need no tool: `cfd-openfoam.ladder` and
  `cfd-openfoam.settle` (`views/flow.py`), payloads of those numbers that the spine
  bakes into Vega-Lite. A chart payload may ask for a y axis that does not start at
  zero (`"zero": false`): the ladder's meshes differ by 0.4% of the drop and drew as
  one point (*rejected:* a log y, whose domain rounds to powers of ten). The settling
  chart leaves out a change of zero (no place on a log axis) and the first row
  (*rejected:* a floor value, a number no evidence file holds).
- **Phase 2, P2.2 (built):** cfd-openfoam finds ParaView only through a `pvbatch62`
  launcher on PATH, whose exec line names the prefix (`views/_pv.py`;
  `references/rendering.md` has the launcher and the install). Its identity is its
  conda records: the paraview record must list the `bin/pvbatch` on disk by its sha256,
  the sha256 of the paraview, Mesa and Pillow packages must be an entry of
  `views/paraview.lock.json`, and `pvbatch --version` must agree. Another build is
  unavailable, named; `NOPEKIT_PVBATCH_EXTRA_LOCK` adds a build, loudly. A run gets an
  environment built from nothing (a new empty HOME outside the project each time,
  llvmpipe pinned), a timeout, and its output only when its `result.json` is this job's
  and names llvmpipe. A renderer control, once per build, draws `views/fixture/strip.vtk`
  (15 cells, -0.2 to 1.2 of the scale) with ParaView's own `Viridis` preset; Pillow
  inside pvbatch reads each cell's centre pixel, and nopekit judges them: within 1 of
  the lookup table's colour inside the scale, exactly magenta above and grey below, and
  viridis's (33, 145, 140) at 0.5. *Rejected:* the banner alone, which fits another
  build; the launcher's 28 KB digest alone; a PNG decoder of our own (the author's rule:
  Pillow reads the pixels where ParaView drew them); judging inside pvbatch only (a
  fault in that script would pass any renderer); a HOME under the project (`doctor`
  would write into it). `doctor` has a row for each tool the views draw with, from the
  pack's `TOOL_CHECKS`, and runs no control; `pack validate` loads `views/` through the
  build's loader (`site.load_viewgens`) and holds `provides_views` to the views that
  register, their tools to `requires_tools` (which now lists `pvbatch62`), and
  `evidence_of` to a gate of the pack.
- **Phase 2, P2.3 (built):** two stills bound to `cfd.pressure_drop`,
  `cfd-openfoam.speed` (OpenFOAM's `mag(U)`) and `cfd-openfoam.pressure` (its
  `static(p)`). Each binds the finest level's field and mesh by the sha256 the verified
  summary names, copies those bytes into a case of its own outside the project (with a
  controlDict of its own, so ParaView reads no other file of the run), and runs
  `views/_scene.py` in pvbatch: a cut at the middle of the thickness, each cell its own
  flat colour from ParaView's own `Viridis` preset, a continuous lookup table, unlit,
  magenta above the scale and grey below, each measuring plane's trace as a 6 px line,
  a parallel camera on the whole cut plus 5%, 1600 px wide, and the PNG written again
  by Pillow with no chunk but the image's. Scales (`views/_scales.py`): speed on [0,
  2 x the upstream plane's mean speed], static p on [p_down - 1.0 dp, p_down + 2.0 dp];
  each end is factors times summary values, named in the scale's `source`. The
  legend's stops come from `views/viridis.json`, the preset as ParaView exports it
  (`views/_export_preset.py`), and a still is drawn only when the scene says it applied
  that preset; its ticks are matplotlib's (`MaxNLocator`, in pvbatch). Measured on the
  real tools: every one of 7840 cells away from the lines is the preset's colour for
  its value to the byte, two draws are byte-identical, and each line is within a pixel
  of its plane. On the baseline 6 cells at the inlet's corners pass the pressure
  scale's top (to p_down + 2.62 dp) and are magenta: that rise grows as the mesh is
  refined, so no fixed top holds it. The renderer control now draws with a continuous
  table as the stills do (ParaView's default quantises the map to 256 colours).
  *Rejected:* colour stops typed by hand (the design's draft), matplotlib's own viridis
  table for the legend (another program's copy of the map), the export's stops as the
  lookup table (an edited file would colour the picture to match itself), [-0.25, 1.25]
  dp (paints the baseline's ends out of range), [-1, 3] dp (half the scale for the
  rest, and a finer mesh passes it again), a line moved toward the camera (measured:
  the same bytes), and a tick picker of the pack's own.
- **Phase 2, P2.4 (built):** the 3D view. The pressure still's pvbatch run also writes
  ParaView's own glTF of the same cut (`ExportView`, `InlineData` 1, no normals, batch
  ids or NaN texture, the planes' lines hidden), and the page turns that file as
  ParaView wrote it, in the viewer's field mode, with the still's legend. The spine
  now takes a `.gltf` whose buffers are inside it beside a `.glb`
  (`site.SOLID_SUFFIXES`, `_solid_problem`), and publishes it as it publishes a GLB:
  on this computer only (`assets/live/`, named in `build.json`) until an export
  re-runs the entry, then committed. A glTF over the 2 MiB cap is left out with a
  warning, and the still is drawn. Measured on the real tools: 940 KB for the
  baseline's 8000 cells, no error and no warning from the Khronos validator
  2.0.0-dev.3.10, every cell's `COLOR_0` the preset's colour for its value, two
  exports byte-identical; ParaView's default export of the cut (coloured by cells)
  fails the validator (`MESH_PRIMITIVE_UNEQUAL_ACCESSOR_COUNT`), and is the tests'
  control. In headless Chromium, ParaView's file in field mode draws each band of a
  planted field in its preset colour within 2, and the lit mode draws none of them.
  `tests/cfd_fields/` holds two small exports ParaView made (500 cells, seven bands
  of known values) and the script that made them; a real-tool test makes them again
  and compares bytes. *Rejected:* the design's `views/_gltf.py` (a GLB repacker of
  our own: the author's rule of 2026-10-21); trimesh to convert to GLB (another tool
  that rewrites ParaView's colours, for 25% fewer bytes the cap did not need); a
  check of accessors in the spine or the pack (the validator is a trusted tool, run
  in the tests where node is); the planes' lines in the 3D file (WebGL draws a glTF
  line 1 px wide); a 3D file for the speed still too (a second file of up to 2 MiB
  for the same geometry).
- **Phase 2, P2.5 (built):** the hand-drawn chart is retired. Every chart view on
  the technical record draws from a Vega-Lite spec the spine bakes
  (`page.view_chart` over `chart_spec`), through the `drawChart` the engineering
  page's cards use, and `lib/chart.js` is deleted. An unbound view keeps its own
  limit (on y, or on x as a vertical line) and its points with the margin words the
  build bakes; a bound view on the record draws its chart file's series alone; the
  verdicts' anchors on a chart are baked into its spec (a located series heavier, a
  located x a rule in its severity's colour). A spec over 8 KB is its own file,
  `data/views/<id>~chart.json`, sealed with its row. Without the library the box
  shows the spec's numbers as a table. A layer of constants (a limit, a rule, a
  label, the judged point) now draws from one empty row of its own: before, it was
  drawn once per row of the chart, so on the first chart of 42 points the shaded
  side of the limit read solid red and each label was 42 copies (the cards' charts
  had it too, at three rows, unseen). On a box under about 520 px the legend stacks
  and an axis title ends in an ellipsis. *Rejected:* keeping `lib/chart.js` for the
  record's unbound charts and their locators (P1.5's choice: the anchors are a fact
  the bake can carry, and its text was about 5 px high on a phone); composing the
  anchors' layers in the browser; reading an analytic pack's `markers` (the
  hand-drawn chart never drew them, and their labels hold the pack's own margin
  words).
- **Phase 2, P2.6 (built):** the docs and the README's picture of the pack.
  `PACK.md` has "Pictures on the site" (each view, the result it is of, the scales
  and why, what git keeps, "from another run" and its command, and what a missing
  `pvbatch62` does); `references/rendering.md` has every measured fact; the skill
  has two lines. `docs/img/packs/cfd-openfoam.png` is the two stills a real
  `site build` published for the pack's baseline, with their scales, put
  together by `views/_gallery.py` in ParaView's Python: the stills' pixels with
  no change, and matplotlib's colour bars from the sidecars' stops and ticks.
  *Rejected:* a screenshot of the page (its chips are one build's statuses, and
  go out of date in the README); a new run of the scene for the README (a second
  drawing of the field, which can differ from the published one).
- **Phase 2, review (built):** the stills look along the case's thin axis
  (the gate's `thin_normal`, the normal of its `empty` patches) with the flow
  from left to right, refuse a cut that is not the whole finest mesh and plane
  lines out of order, and are at most as tall as wide; a 3D case gets the
  charts and no still. An older chart draws its own run alone. The ladder's
  judged point is a ring over a band with end bars, named on the chart and in
  its caption; the settling chart's diamonds take their mesh's colour, its log
  axis one notation, and it says `"judged": false`, so the build no longer
  warns six times about its units. Every chart carries a baked table for a page
  where Vega does not load. On the page, a still's plane words sit above the
  picture, a legend's tick marks sit on their values, a hero still shows its
  words and caption, the card says its claim's cause sentence, and the 3D view
  looks down on the cut. The analytic packs' `markers` are drawn as their point.
  *Rejected:* a cut of a 3D case (no one middle stands for a duct); the thin
  axis found in the scene from the mesh's bounds (the thinnest side is not
  always the empty one).
- **Phase 2, built in P0.2 and P2.1:** a solver history is CSV evidence,
  never JSON (a JSON history puts thousands of unit-less numbers into the prose pool).

**Packs for handheld electronics, made general** (2026-11-23, branch
`general-packs`). The handheld cut of 2026-11-05 (fdm-print 0.2.0, cad-solid 0.2.0,
pcb-kicad 0.2.0, firmware-zmk 0.1.0) was written for one device. The author's rule:
a pack on main works across a broad category of designs, here handheld and portable
electronics (controllers, wearables, keyboards, remotes, sensor tags, trackers,
e-readers). One firmware, one charger topology, one fab or one module is a profile,
a baseline or a record inside a general pack, never the pack. "A gate earns its
place" still holds: fewer, sharper gates. The design came from two independent
designs per area, a judge per area and a critic across the five; the full designs
are kept outside the repository with the branch's history.

- **firmware-zmk is deleted; `firmware-ci` replaces it** (scope `fw`).
  `fw.ci_job` (tier 0) reads the CI workflow and fails a CI that can read green on
  a failed build (`continue-on-error`, `|| true`, `set +e`, a pipe under a shell
  with no pipefail). `fw.build` (tier 1, established) replays that workflow with
  act in its own containers, on a copy of the tree. `fw.image_fits` compares the
  bytes the image writes with a region from a cited record (bootloader layout,
  datasheet, partition table), never the build's own linker script. `fw.sleep_wake`
  checks that an image that can enter deep sleep arms a wake source (ESP-IDF and
  ZMK first; every other stack skips by name). ZMK becomes one wake profile, one
  design line and two CI templates. *Rejected:* per-stack build recipes (they skip
  PlatformIO, Arduino, QMK, the Pico SDK and STM32Cube); pinning, watchdog,
  brown-out and sleep-current gates (accepted risks or lenses).
- **No radio pack.** Transmit power becomes `fw.tx_power` in firmware-ci, read from
  the `.config` or `sdkconfig` the replay returns, against the setting the module's
  grant was tested at. The antenna keep-out becomes `pcb.antenna_keepout` in
  pcb-kicad. Antenna to enclosure metal is a lens and a physical claim (a range test
  in the hand). *Rejected:* a one-gate `radio-firmware` pack with a configure
  proposer (a pack with no prerequisite edge, and a second home for the build).
- **`hand-ergonomics` is a new pack** (scope `ergo`). `ergo.digit_reach` (tier 0,
  sourced, stdlib) computes, for each control, the worst-sex share of subjects who
  reach it from its grip, from per-subject ANSUR 1988 hand data (public domain),
  shipped as a derived table with its hashes. `ergo.layout_agrees` (tier 1,
  model-only) checks the declared layout against the exported scene, on cad-solid's
  node names. The proposer `ergo.reach_envelope` writes reach maps and design
  ranges. A person holding a print has the final say: the scaffold ends reach in a
  physical claim tested by a small-reach and a large-reach tester. *Rejected:* a
  thumb functional-area model fitted on 20 people and one tablet (not a sourced
  screen yet); a holding-moment gate (its only limit is the print it would replace).
- **thermal-analytic gains `thermal.skin_temperature`**: the average temperature of
  a sealed, fanless handheld's surface from its worst-case dissipation, against a
  touch limit. *Rejected:* `thermal.charge_window` (a hand-typed record of what
  `pcb.charge_temperature` reads off the board, so a part swap would not re-run it).
- **pcb-kicad's power rules take plural records and any protection topology.**
  `pcb.charge_temperature` replaces `pcb.charge_protection`: charging stops outside
  each cell's window at the tolerance corners of its sensing network, wherever the
  cutoff lives (charger, pack protection board, BMS, fuel gauge).
  `pcb.charge_limits`, `pcb.fault_current` (replaces `pcb.fuse_conductor`) and a
  general `pcb.discharge_cutoff` follow. New: `pcb.mismate` (a foreseeable
  mis-mating that joins a source to its return), `pcb.usbc_receptacle` (CC
  termination and role rules; same-name pins stay with `pcb.pin_names`) and
  `pcb.antenna_keepout`. The readers grow: KiCad 10 boards, land patterns paired
  by function, and a fab-service guard. `cad.wall_thickness` reads a minimum per
  part and process; fdm-print slices with presets per part.
- **The ORIGINS scrub.** The cut named the parent's parts, refdes, rule names and
  stories in packs, docs, spine docstrings and README pictures. They go; each lesson
  stays as a generic story. `FORBIDDEN` grows last, with tokens unique to the
  parent only.
- **Packaging.** The wheel's globs missed the KiCad, SPICE and OpenFOAM baselines;
  a test now checks that every tracked file under `packs/` is bundled.
- **The holdout.** Before merge, every new or changed pack runs against outside
  open designs that played no part in writing it (an ESP-IDF handheld, a Zephyr
  watch, an Arduino watch). A wrong FAIL or SKIP is overfit and is fixed. Their
  files are never committed (some are GPL).
- **Order**, one commit each: P1 the docs and docstring scrub; P2 packaging; P3 the
  pcb-kicad scrub; T1 skin temperature; C1 the wall per part; D1 slicer presets per
  part; E1 hand-ergonomics' reach; P4 the readers; F1 firmware-ci; F2
  `fw.tx_power`; P5 the power rules; P6 mismate and USB-C; E2 `ergo.layout_agrees`;
  P7 enclosure fit; P8 the antenna keep-out; P9 the proposers; Z1 README pictures
  from each pack's own baseline and `FORBIDDEN`; Z2 the holdout.

## 9. Retired

`docs/PLAN.md`, `docs/PLAN-v0.14.md`, `docs/plan/phase-1.md` to `phase-5.md`,
`docs/plan/slipped.md`, and the two test classes that read the plan's gap-map rows
(`ClosedRowsResolve`, `ClosedRowsStayClosed` in `tests/test_contracts.py`). All at
commit `a00d0e5`. Phase 1's canonical `claims/C1.json` moved into
`tests/test_records.py`.
