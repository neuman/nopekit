<!-- Tier 3 of docs/PLAN.md: read docs/PLAN.md first (the map, the D-rows, the R-rules and G-criteria this file cites). Moved verbatim from PLAN.md §4.4; S-nn rows live in docs/plan/slipped.md. -->

# 4.4 Phase 4: the site

**Why this order.** Everything the page shows is computed in the spine first (4.1, 4.2)
with Python tests; only then does the template render it (4.3, 4.4), and static scans
prove the template computes nothing (invariant 14). *Layout is not truth*, so the spine
computing slot positions does not breach "the site never computes truth" (D-22).
`examples/bracket` is the test project; thickness stays 7.0 so a fresh clone shows a real
failure lit on the part.

#### Target (open the URL from `/start`)

- **Above the fold, one question — *Can I build this?*** — answered *No*, *Only a
  real-part test remains* or *Yes* from `readiness.fold` (P2.5), which the spine derives
  from `claims.export_refusals` (the same predicate as `export`, Q4.5): *Only a real-part
  test remains* iff every refusal is an unsigned measurement or human terminal, so an
  unowned critical assumption can never hide behind it (S-59 again). The three phrases
  live in `report.HUMAN` and arrive as `readiness.fold_words`; the page prints them and
  computes nothing. Below it: the two sentences from `Readiness`; counts in human words;
  `last check 3 min ago`, computed from `when` with the viewer's clock; the commit as
  `1e09113`, `1e09113 + uncommitted changes`, or `not a git repository` — never blank.
  Nothing else sits above the fold.
- **The canvas.** Five fixed columns: inputs → numbers → must be true → checks → what it
  ends in. Ribbons carry claims. On the shipped bracket four red ribbons — arm_length,
  width, thickness and load_n, every number perturbation finds moving the deflection
  (material is not perturbable) — run into C1 → `bracket.deflection` → *a closed-form
  calculation*; C7 ends in a dashed *nothing can
  check this yet* node that is always visible (§14, §18).
- **The part.** The elevation drawing with `arm_tip` lit and labelled *0.70 mm vs 0.5 mm
  limit*.
- **Clicking C1** opens an ego graph of at most 12 nodes: what else becomes invalid when
  this says no (§20).
- **A scrubber** plays the last N revisions (commits, then uncommitted builds), with decisions as marks; the human's own
  preference marks are the only marks styled as decisions.
- **Change marks** on whatever changed since this browser last looked.
- **`focus.json`** chose the view and says why in one line of facts: *C1 went from verified
  to failing: 0.70 mm vs limit 0.5 mm (bracket.deflection).* The page opens that view and
  emphasises its items (4.3); the agent's suggested views sit in a strip with their bylines.
- **It stays current.** A `check` in the session rebuilds the site, and the open page
  picks the new build up within seconds, without a reload.

**Checkpoint 4.0, `C:`.** Pin the top-level keys of `state.json`; extend
`HonestyOnThePage` to every ClaimStatus.

#### Checkpoint 4.1: `graph.py` and the layout

```jsonc
// site/data/graph.json
{"schema": 1, "rev": {"seq": 14, "commit": "<sha>", "dirty": false},
 "columns": ["inputs", "parameters", "claims", "checks", "outcome"],
 "shapes": {"measurable": "rect", "physical": "hexagon", "assumption": "diamond"},
 "nodes": [{"id": "claim:C1", "column": "claims", "slot": 0, "depth": 0,
            "label": "Tip sags no more than 0.5 mm at rated load", "kind": "measurable",
            "critical": true, "weight": 2, "status": "fail", "display": "failing",
            "tone": "failing", "stale": false, "opacity": 1.0, "when": "…",
            "group": "stiffness", "zoom": 1, "tombstone": false,
            "bullets": [{"gate": "bracket.deflection", "measured": 0.6997, "limit": 0.5,
                         "comparator": "<=", "units": "mm",
                         "margin": {"rel": -0.399, "side": "outside"}}],
            "headline_gate": "bracket.deflection",
            "locates": [{"view": "elevation", "target": "arm_tip"}],
            "byline": {"origin": "project", "by": "…", "when": "…", "commit": "…"}}],
 "edges": [{"source": "param:thickness", "target": "claim:C1", "via": "influence",
            "claims": ["C1"], "weight": 2, "tone": "failing"}],
 "ego": {"claim:C1": ["check:bracket.deflection", "param:thickness", "…"]}}
```

- `graph.build_graph` **calls** `claims.statuses`, `report._unproven_for`, `claims.margin`
  and `modelio.influence`; it never re-derives them (R-5).
- Edge weight is the sum of claim weights, critical = 2, non-critical = 1: 2:1 is the
  smallest ratio that reads as different by eye; 3:1 shrinks non-critical ribbons to
  hairlines on a 30-claim project. Edges are split per tone, so each ribbon carries one
  colour and colour stays status-only.
- Physical claims route through a `testcard:` node, assumptions through a grey `assumed`
  node, measurable claims with no gate through a dashed `nocheck:` node, so every claim
  reaches an outcome and "nothing can check this yet" is visible. P2's `needs` edges run
  inside the checks column, in sub-lanes by topological depth; a prerequisite-blocked check
  is tone unknown with display "not yet checked: <prereq> failed" (or "not established",
  D-03).
- **Fixed encodings are spine fields**, so the page computes none: `shape` comes from the
  top-level kind → shape table; `opacity` is below 1 only for a stale verdict, or for a
  terminal that can drift without an input change (tier EXTERNAL, datasheet, measurement,
  human) older than `aged_after_s` — never wall-clock age alone, which would imply a false
  staleness (A-9.6). That age is the terminal's own — a datasheet's from the ingest or
  extraction time of its evidence, a measurement's or human's from the signed result's
  `when` — never the verdict's `when`, which restarts on every re-run and would keep the
  case study's sourcing volatility (paper §14) from ever fading; `group` (the claim's first tag) and `zoom` (1 = overview, 2 = groups
  expanded, 3 = every node) drive semantic zoom — assigned by one rule in `graph.py`: 1, every
  claim and outcome node, and every check, parameter and input on a path to a claim that is
  not verified, so zooming out never hides a failure; 2, every other check; 3, the rest,
  tombstones included; and the node `id` is the one **selection
  id** shared by canvas nodes, Locator targets and panel rows, which is what linked
  highlighting keys on.
- **`sticky_layout(history, nodes)`** assigns discrete `(column, slot)` from tracked
  first-appearance order (D-22), with no stored layout: surviving nodes keep their slots,
  new nodes append, deleted nodes leave tombstones whose slots are never reused, drawn with
  a dashed outline and a strike glyph — never with opacity, which means age alone. A
  tombstone marks a removed record, not a disappeared alternative: candidates and why
  each disappeared are the small multiples' dispositions (4.3). CI checks out with full history
  (`fetch-depth: 0`); a shallow clone orders by id and says so. There is **no
  `--relayout`**: compaction would move surviving nodes. If tombstones ever crowd a column,
  a new layout epoch — a visible break in the scrubber — is put to the user as a question
  first, not built (A-12).
- `headline_gate` is chosen by the spine — worst status first, then smallest margin — so
  the panel's C1 row stops showing the passing guard's 8.57 L/h
  (`site_template/lib/panels.js:170-172` picks `proving[0]`; S-77). `chart.js`'s own margin
  arithmetic and its `<=` default are deleted (`site_template/lib/chart.js:237-240`; S-82).
- Bylines: `git log -1 --format=%h%x00%an%x00%aI -- <record file>` through `vcs` (P1.2),
  batched in one `git log --name-only` pass; agent origin from `Co-Authored-By`/`Claude-Session`
  trailers, shown beside the author as provenance text only (D-32); "no git history"
  rather than blank. A record whose file porcelain shows modified or untracked reads
  "uncommitted change" — the last committer's name never stands on bytes that commit does
  not hold.

`V: test_graph`: the invariants on the canvas (a skip with `passed=True` is never toned
verified; an error is toned failing and its claim is never verified; a PARTIAL claim carries
`partial`); every edge runs left to right except `needs`, which stays in its column; the
graph is deterministic (byte-identical apart from `rev.built`); every claim reaches exactly
one outcome; no NaN or Infinity (strict `json.loads`); `StickyLayout` holds under insert,
delete, idempotence and no slot reuse, a CI-fresh clone and a local build give identical
slots per commit, and deleting a claim moves no surviving node on a fresh clone;
`Encodings` (opacity depends only on `stale`, the terminal class and the terminal's own
age against `aged_after_s`; C1, `bracket.deflection` and every parameter ribbon into C1 are
zoom 1 on the shipped bracket; `check --force` leaves a datasheet node's opacity unchanged; a
tombstone's opacity is 1; every canvas node id resolves to a panel row and, where locators
exist, a viewer target); `BracketLevers` (arm_length, width, thickness and load_n → C1
edges exist, material none; a model that ignores a parameter gets none); `Ego` (≤ 12,
always contains the node, failing neighbours first); `Bylines` (a record edited after its
last commit reads "uncommitted change", not that commit's author; an agent co-authored
commit shows its trailer beside the author); `Multiples` (two candidates differing only in
thickness have `differs == {param:thickness, claim:C1, check:bracket.deflection}` plus
influence neighbours, on identical slots, read from their stored `outcomes` — with the
snapshot launcher patched to raise, `site build` still draws them).

#### Checkpoint 4.2: `focus.py`, history frames, suggestions

- `Snapshot = slim(state)` (claim status/display/headline margin, check status/when/stale,
  param values, candidates, decisions; volatile fields excluded). A focus item is
  `{rule, views, emphasis, reason, next, rank}`. `RULES` is a tuple of `Rule(id, order, fn,
  provenance)`; `compose(prev, new, suggestions)` is the **only** precedence logic.
  `focus.json` is `{schema, rev, prev_rev, items: [item…], suggested: [{view, reason,
  byline}]}`. A **view id** is either a declared view's id from `state.views` (the bracket's
  `elevation`; the sensitivity series `site build` emits per claim as a `chart` view with id
  `sensitivity:<claim>`, 4.3) or a page pane from one tuple in `focus.py`, `PANES =
  ("overview", "canvas", "ego:<node id>", "testcard:<claim>", "bring", "multiples",
  "history")`, listed in SITE_CONTRACT (R-14); `emphasis` holds selection ids (4.1). A rule
  can name nothing else, and `BracketFreshClone`'s view-id check resolves against exactly
  these.
- Reasons are fact-only templates: every number in a reason must appear in the snapshot,
  and no reason contains "likely", "probably", "seems", "I think" or "should".
- **History** is derived from tracked state, so CI and a fresh clone see the same frames
  as the author. For each of the last `HISTORY_KEEP` commits that touched WATCHED paths,
  `site build` evaluates the commit in a `vcs.snapshot` subprocess (D-23) against the
  entries committed *at that revision*, resolved under the spine digest each records —
  never today's, which P2 and P3 change, so every older frame would miss — and labels the
  frame with that spine; a commit older than P1 has no cache and is labelled "before
  per-check records", not rendered all-unknown; only an entry genuinely absent at that
  revision renders "not checked at this revision", and no gate runs. CI builds the site
  from its full-history checkout (`fetch-depth: 0`), never the temp bracket copy, which
  has no history. It writes that frame's own nodes, edges, tones and slots to
  `site/data/history/<seq>-<sha12>.json`; the working tree is the head frame. A local ring
  keeps only uncommitted builds, and slim snapshots are only the focus diff's input (its
  "previous" is the newest earlier frame, so focus rules fire in CI too). `index.json
  [{seq, id, built, commit, dirty, counts, decision_marks[], changes_since{seq: [node
  ids]}}]`. `HISTORY_KEEP = 20`: it covers roughly the last 30% of the case study's 69
  revisions; 5 was rejected as too short to see a margin erode across edits; unbounded was
  rejected because it rebuilds the run history. A frame is added only when the slim
  content hash changes. `changes_since` is precomputed, so the page looks changes up and
  never diffs; the last-seen id lives in `localStorage` under
  `nopekit.seen.v1.<name>.<created>`. When that id has left the frames, every node carries
  a change mark and the page says "last seen before the oldest kept revision".
- **`site/suggestions.json`** holds `[{view, reason}]`, merged by `site build` only into
  `suggested[]`. The build stamps the byline itself — "suggested by agent", the build's
  rev, and the session from the file's last commit trailer (provenance text only, D-32) or
  "uncommitted" — and ignores, with a warning, any `by`, `session` or `rev` the file
  carries, because an agent-typed byline could read as the human's (D-18). Each reason
  passes the same fact-only check as rule reasons. A `replace`, `hide`, `order` or
  `priority` key is refused with a warning; an unknown view id is reported like a locator
  problem.
- **Rules.** Rule 5 (*physical result recorded*: test card plus claim, showing the
  channel) is decided. Rules 1, 2, 4 and 6 ship **in their literal form**, whose predicate
  is not in doubt: rule 1, a claim that was pass in the previous snapshot is fail; rule 2,
  a Config value differs, compared exactly, and no claim status changed; rule 4, a claim id
  absent from the previous snapshot that is UNCLAIMED (measurable, no check covering it);
  rule 6, a candidate record
  absent from the previous snapshot. Each **widening** (A-9.1, A-9.2, A-9.4, A-9.7) and
  rules 3, 7 and 8, which have genuine ambiguity — rule 4's first-draft predicate
  contradicted itself and never looked at the previous snapshot (A-9.7) — ship **only as
  answered in A-9**. Rule 3's predicate, built on `claims.margin` (D-17), is shared with `next_action` rule
  10, and both wait on A-9.3. Each
  rule's `provenance` names the session that justified it — which makes "grow the table
  only when a real session shows a wrong composition, and name that session" mechanical.

`V: test_focus`: per rule, the minimal diff that fires it, the nearest diff that does not,
and an adversarial diff; end to end on a bracket copy, thickness 8 → 7 → build gives
`items[0].rule == claim-flipped-to-fail` emphasising claim C1, check deflection and the
locator target — pinned unconditionally, since rule 1's literal form ships without an
answer; a second build with no change adds no snapshot; rule 4 does not fire for a new
assumption, nor on a second build for a standing C7; no widening fires before A-9 answers
it; `SuggestionsCannotReplace` (a `replace` key leaves rule items byte-identical;
a suggestion naming a rule's view appears only in `suggested[]`; one carrying
`by: "<human>"` renders "suggested by agent"; a reason with "probably" is refused); every
Rule has non-empty provenance. `V: History`: a fresh clone with ≥ 3 commits touching
`claims/` gives ≥ 3 frames, each with its own slots and tones, and no gate runs; N+5
distinct local builds leave exactly N uncommitted frames; an identical rebuild adds none;
`changes_since[k]` equals the set of node ids whose slim record differs; a decision lands
on the documented seq; a last-seen id older than every frame marks every node; the ring
survives a deleted `state.json`; a spine-changing commit between frames leaves the earlier
frames' tones unchanged and labelled with their own spine; a pre-P1 commit is labelled,
not all-unknown.

#### Checkpoint 4.3: the template

- **d3.** `site/importmap.js` is a refreshed classic script loaded before `app.js` (the
  import map moves out of `index.html`, which is user-owned and never regenerated after
  init — `site.py:1321-1347`). It maps 11 pinned d3 micro-packages' `src/index.js` from
  jsdelivr — selection, zoom, force, transition, interpolate, color, dispatch, drag, ease,
  timer, quadtree — because d3 7.9.0 ships no single-file ESM. `app.js` loads them lazily
  by dynamic `import()` inside a try. **Without d3 the canvas still draws** — positions are
  precomputed and ribbons are plain SVG; zoom, animation and the ego force fall back to
  static rendering and a deterministic radial layout. `VENDOR_LIBS` makes vendoring
  per-library, verifies each file against jsdelivr's sha256 listing, and writes
  `vendor/manifest.json` last; `importmap.js` maps a library to `vendor/` only if the
  manifest lists it, which also ends the probe-vs-status disagreement about a partial
  `vendor/` (S-80). `site status`/`doctor` detect a shell lacking `importmap.js` and print
  the one-line fix. This is the brief's pre-approved "d3 from CDN by import map like
  three.js", flagged in §8 for visibility, not asked.
- New modules `lib/canvas.js`, `ego.js`, `scrubber.js`, `testcard.js`, `bring.js`,
  `multiples.js`, `focus.js`, `motion.js`. `lib/prefs.js` is the sole owner of `localStorage`, every accessor inside
  try/catch. Colour takes four tones — verified, failing, unknown, grey for assumed — and
  nothing else; opacity, shape, group and zoom come from the node fields (4.1). Every
  visible label — status words, headings, chips and hints ("Claims", "Gates", "nothing was
  proven", "Last gate sweep · tier", the `claim add` hint; `format.js:41-45, 50`,
  `panels.js:95, 162, 165, 190, 195, 199, 612`) — comes from `report.HUMAN` in
  `state.json`, not from `format.js:23-34` (S-69, S-75); the parameter panel is titled
  "why this number".
- **Focus** (`focus.js`) reads `data/focus.json`: it opens `items[0].views`, marks each
  emphasised node with an outline ring — a channel that is neither colour (status) nor
  opacity (age) — puts each item's reason line under the fold's block, and shows
  `suggested[]` as a strip, each with its byline. It chooses nothing: order and emphasis
  are `compose()`'s.
- **Sensitivity view.** For each claim with a margin, `site build` emits a series view:
  the headline gate's measured value with each top driver from `modelio.influence` at
  ×0.8, ×0.9, ×1.1 and ×1.2 (re-running `build()` as `influence` does), and the limit line;
  `chart.js`, which already draws series, renders it. It is what `next_action` rule 10 and
  focus rule 3 point at. *Why ±20%:* it brackets the 1.15 margin P5.1 uses; *rejected:*
  ×0.5–×2, a search range, not a neighbourhood anyone would build. `V: SensitivitySeries`:
  on the bracket at 7.0 the `sensitivity:C1` series' ×1.0 point equals
  `bracket.deflection`'s measured value, its drivers are arm_length and thickness (the tied
  top of `influence`), `material` has no series, and the page draws it with no arithmetic
  (the static scan).
- **Rebuild on every change.** `check`, and the `/tested` and `/pick` write paths, end with
  `site build --head` when `site/` exists with its ignore block (below; a failed build
  prints one line and never changes the exit code); the build writes `data/build.json {seq, built}` last, and the
  page polls it with a GET every 2 s while visible and re-renders when `seq` moves. *Why
  2 s:* shorter than a glance from the terminal to the browser; *rejected:* a file watcher
  or server push, a long-lived process the no-build site does not have.
- **Test card** (`testcard.js`, `@media print`): per claim whose terminal is measurement or
  human — id, statement, acceptance (or "acceptance not quantified"), rationale, the
  authored procedure if one exists (never generated), the commit and ρ it applies to, and
  the `card_id` the spine emits per card in `state.json` — sha12(claim id + acceptance +
  ρ); the page never hashes; blank measured/who/date/pass-fail fields. P2's signed result
  binds to the same ρ, so a result for rev 3's part cannot verify rev 9's claim. Each card
  carries spine fields `ready` and `after` (`["C1"]`), from `check`'s test-article
  predicate (P3.3), with their words from `report.HUMAN` ("ready to test", "after C1
  passes"); the page prints them and never re-derives `blocking()`.
- **Bring list** (`bring.js`) renders `state.asks` from P3's `asks()`, so its real-part
  items wait on the same predicate; a bring item comes from an authored field or a fixed
  table, never generated prose; checkboxes are per-viewer `localStorage`, never written
  back.
- **Small multiples** (`multiples.js`) draw each candidate from `trade.compare` over the
  stored `trade/<name>.json` — its `differs` and the `outcomes` written at evaluation
  (3.1), so `site build` spawns nothing for them — on the same sticky layout, `differs` at
  full opacity and everything else at 0.25; the disposition is a word and glyph, never a
  colour.
- **Transitions** (`lib/motion.js`, the one helper): when `build.json`'s `seq` moves or the
  scrubber steps, it tweens each node's and ribbon's tone, width and opacity from the old
  frame to the new, matched by selection id, over 300 ms, and does nothing under
  `prefers-reduced-motion`; positions never tween, because they never move. *Why 300 ms:*
  long enough to follow a ribbon changing colour, short enough to finish well inside the
  2 s poll; *rejected:* 1 s, which overlaps the next poll on a busy session.
- **The bracket** gains `views/elevation.py`, a stdlib SVG viewgen with element ids
  `wall_plate`, `arm`, `arm_tip`, `bolt_1`, `bolt_2`, `load`, published in `meta.elements`;
  `bracket.deflection` attaches `Locator(view="elevation", target="arm_tip")`.
- **The diagram-locator renderer is fixed**: `renderDiagram` gets the locators and marks
  `getElementById(target)` inside the inlined SVG (`site_template/lib/stage.js:128`, and
  `markFlat` at `:370-377` looks only for `data-target`; S-78). `site.py` parses the SVG's
  ids with `xml.etree` and reports declared elements or locator targets missing from it
  (`site.py:1251-1252` trusts `meta.elements` today).
- **Ages** come from `when` with the viewer's clock, not a build-time `age_s`
  (`site.py:1737`; S-79). `AGED_SECONDS` moves into the spine as `meta.aged_after_s`.
- Scaffold `.gitignore` adds `data/`, `assets/` and `importmap.js` (D-21); publishing is a
  CI step. A site scaffolded before this checkpoint keeps its own `.gitignore`
  (`site.py:175-184` writes one only when absent), so the ensure-ignore step (P2.5) also
  maintains a marked block in `site/.gitignore`, printing the one `git rm --cached` line
  when those paths are tracked, and `check`'s rebuild runs only once that block is present
  (otherwise one line says why it did not rebuild).
  `examples/bracket` **commits its site shell** — the files `site init` copies:
  `index.html`, `app.js`, `style.css`, `lib/`, `.gitignore` — so neither a fresh clone nor
  `/start` finds `site/` missing, and only ignored outputs change (`data/`, `assets/`,
  `vendor/`, and `importmap.js` wherever a build rewrites it). A test keeps the committed
  renderer files equal to the template.

#### Checkpoint 4.4: the fold

One block of `min-height: 100svh`: the question, the answer in three states from
`readiness.fold_words` (the page maps nothing), the two sentences, the counts in human words, the age, the commit via
`vcs.git_head` (P1.2; never raises; "not a git repository" or "git not found" rather than a
blank). The stale banner lives inside the block. `report._verdict_parts() -> (lead,
caveats[])` feeds it, and `lead + " " + " ".join(caveats) == readiness.verdict` is tested,
so the page and the report cannot drift.

`V: tests/test_site_template.py` — static scans, each with a planted violator the scanner
must flag: no `fetch` other than GET or HEAD, no `sendBeacon`, no WebSocket, no XHR POST or
PUT; `localStorage` only in `prefs.js`, inside try; no JS or HTML string literal that
`test_vocabulary` forbids (internal names, `proven`, `validated`) or that is one of
`report.HUMAN`'s phrases (the fold words, card readiness), once state carries display
words; `app.js` fetches `data/focus.json` and polls `data/build.json`; every bare import specifier is in
the import map, and no static `d3-*` import; every import-map and `VENDOR_LIBS` URL is
`https://cdn.jsdelivr.net/npm/<pkg>@<x.y.z>/…`, one version per package, and every
transitive d3 import resolves to a pinned entry (a `d3-selection@3` range fails); no hash
or digest computed in JS (the card id is the spine's); every transition goes through
`motion.js`, which checks reduced motion — a planted `.transition(` elsewhere is flagged.

`V: BracketFreshClone` (also G6): copy the bracket as G6 does, then `check` and
`site build` (the shell is committed, so `site init` is not needed and would refuse):
`locator_problems == []`; C1 toned failing and its check node locates `arm_tip`; the SVG
contains every `meta.elements` id; `card_id` in `state.json` changes when ρ does; every
view id in `focus.json` and `suggested[]` resolves to a view the page has; `git
status --porcelain` shows nothing under `site/`; selftest still 6/6. `V:
SiteFollowsCheck`: on that copy, thickness 7 → 8 then `check` alone — no `site build` —
moves `build.json`'s `seq` and C1's tone in `state.json`. `V: OldSiteIsBroughtUnderIgnore`:
a project whose site was scaffolded before P4 (tracked `data/`, no `importmap.js` rule)
gains the marked block on its next `check`, prints the `git rm --cached` line, and is not
rebuilt until the block is present; after it, `check` leaves porcelain empty under
`site/`.

**Refuter targets for Phase 4.** Make the page show a colour or word more generous than
`resolve_status`. Make positions move between builds. Make a focus rule hide a pass→fail
flip. Make a suggestion replace a rule. Make the page write anything. Make NaN kill the
page. Make an age read 0 when unknown. Make the canvas lose C7's "nothing can check this
yet".

**Done criteria for Phase 4.** G1–G8; `BracketFreshClone` green; CI builds the bracket
site and parses `graph.json`, `focus.json` and `history/index.json` strictly; CLAUDE.md
gains invariants 12 (site) and 14; the commit names S-60
(render), S-69 (site words), S-75, S-77–S-80, S-81 (ribbons), S-82.

**Open questions for the Phase 4 judge panel.**

| Q | Question | Recommended default | Rejected, and why |
|---|---|---|---|
| Q4.1 | Who computes layout? | The spine: discrete, sticky slots in tracked first-appearance order, no stored layout (D-22). | d3-sankey relaxation moves nodes; a stored `layout.json`, ignored (forgets on CI) or tracked (conflicts on every candidate). |
| Q4.2 | Where do snapshots live? | Frames derived from the last N = 20 commits touching WATCHED paths, from records and the committed cache, plus a local ring of uncommitted builds; written to ignored `site/data/history/` and published by CI (D-21). | A gitignored ring alone: CI and every fresh clone get one frame, and no diff-based focus rule ever fires. A committed ring: conflicts on every candidate merge. Restoring the last published `site/data` from the Pages artifact: CI-only, and a third history store. |
| Q4.3 | How does d3 load? | 11 micro-packages via the import map, lazily; vendoring mirrors their `src/` trees with sha256 checks. | The `+esm` endpoint rewrites imports root-relative and breaks vendoring — a bundler someone else controls; UMD `d3.min.js` is not an import-map module. |
| Q4.4 | Which view does the bracket get? | A stdlib SVG elevation (cost S); a stdlib GLB is an optional follow-up. | — |
| Q4.5 | What predicate answers "Can I build this?" | `export_refusals`, rendered in three states. | `readiness.ready` says "ready" while a critical measurement is unsigned (S-60). |
| Q4.6 | Should suggestions be read by the page, as `annotations.json` is? | No: `site build` merges them, so composition has one tested implementation. | Page-side composition is the page deciding emphasis. |
