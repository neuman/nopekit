# The project site

Every nopekit project can build a **static site that serves two jobs at once**:

1. **Explain the thing** to someone who did not build it.
2. **Debug the thing** — spin it, pull it apart, and see the latest gate results
   *anchored to the geometry they are about*.

The second job is the one that justifies the code. A readiness report tells you
`cad.clash: 1 interfering pair — carriage / guide_roller, 0.41 mm³`. The site
shows you the two parts, lit up, at the pose where it happens. One of those is a
sentence you have to go and act on; the other is the problem, on screen.

## Non-negotiables

**No build step.** `site/` is plain HTML, CSS and ES modules. `nopekit site build`
writes JSON; `nopekit site serve` runs `python3 -m http.server`. A project site
that needs npm is a project site that rots — and the spine is standard-library
only for exactly this reason. three.js loads from a CDN by import map, with
`nopekit site vendor` to pull it local for offline or archival use. A `vendor/` that
holds only some of `site.vendor_urls()` reads *partly vendored* in `site status` and
`doctor`, each missing path named, never *from the CDN*: the page finds `vendor/` and
loads from it (review of SPEC 40, when the meshopt decoder joined the list).

**The site never computes truth.** It renders the project's records — `claims/`,
`params/`, `inputs/`, `decisions/`, `groups/`, `.nopekit/project.json` and the model —
and each gate's verdict as `verdicts.resolve` finds it in the verdict cache, the one
resolver every reader uses. If a number on the page is wrong, a record or the cache is
wrong. It is an output, not a source (rule 1).

**Baked versus rendered** (architecture phase 5, SPEC 32; invariant 14). *Baked*:
written by `site build` under `site/data/` and `site/assets/`. *Rendered*: what the
browser does with a baked file. Every number, status, word and order on the page is
in a baked file; the browser maps data to DOM and handles hover, selection, the
"worst first" toggle, pan, zoom, the drawer, the theme and 3D. Never in the browser:
a status, a margin, readiness, a sensitivity, a verdict's reading order, an age or
whether it is aged, the mixed-age banner's decision. The build refuses a page whose
token for a claim, a requirement, a board, a milestone or the project differs from
the resolution's (`page.status_problems`), and `site status`, `status` and `doctor`
read a baked file edited by hand as *edited by hand*. Tracked baked files are
clock-free and written only when their bytes differ, so a rebuild that judged nothing
new changes no tracked byte; the build time and each verdict's age go to
`data/build.json`, which `site/.gitignore` ignores.

**It degrades all the way down.** A project with no geometry — a chemical process,
a supply chain — still gets claims, verdicts, evidence, provenance and the
readiness report. 3D is one view kind among several, not the point.

**Extensibility is in the data, not in shipped code.** Packs emit *views* in kinds
the site already knows. A pack shipping its own JavaScript would make the one
artifact whose job is to be trusted depend on code nobody reviewed — and it would put
the interesting work in a place an agent cannot easily inspect or revise. Data an
agent can author, read back and correct is the whole point.

## Layout

```
site/
  index.html          the address `/`: a redirect to engineering/ (the `nopekit-views`
                      marker), with a link to each page for a browser with no script.
                      Its script goes to `engineering/` over http and to
                      `engineering/index.html` from the disk (`file:`, where a folder
                      address lists the folder), keeping the query and the hash; the
                      refresh tag and both links name each page's `index.html`.
                      Yours: written by `site init` when absent, never refreshed and
                      never written by `site build`. `site init --force` replaces
                      all four shells, and `site init`'s refusal names the four.
  engineering/index.html   the engineering page's shell (the outline). Yours: written by
                      `site init` or `site build` when absent, never refreshed.
  marketing/index.html     the marketing page's shell. Yours, as engineering/ is.
  technical.html      the technical record's shell (today's panels). Yours too: written
                      by `site init` or `site build` when absent, never refreshed.
  page.js, lib/       the renderer (map, drawer, sections, live mode, panels, viewer,
  marketing.js        verdict, theme, overlay) — refreshed by `site build`; app.js is
  app.js              technical.html's, marketing.js the marketing page's
  page.css, map.css, marketing.css, style.css
  img/made-with-nopekit.png   the badge each page's last section shows
  content/            the agent's prose: <board>.md, intro.md, next.md, story.md,
                      stamped `as_of`
    img/              figures the prose shows. Tracked, not records, never scanned
  vendor/             three.js etc. after `nopekit site vendor` (gitignored by default)
  data/               GENERATED by `nopekit site build` — do not hand-edit
    state.json        claims, verdicts, views, provenance, readiness (technical.html)
    map.json          the grounding map (the engineering page)
    page.json         the outline around it: verdict, sections, floor, next steps, and
                      the marketing page's story, requirements and decisions
    views/<id>.json   per-view payloads too big for state.json
    build.json        IGNORED: the build time, each verdict's age, the live flag
  assets/             GENERATED — what viewgens wrote (GLB, glTF, PNG, SVG, CSV)
    evidence/<gate>/<entry>/<file>   evidence copied from .nopekit/out/, by content
    figures/<gate>/<entry>/          a bound figure and its sidecar <view>.view.json
                      (issue #15; "Figures bound to verdicts")
    live/<gate>/<entry>/             IGNORED: a 3D file of an entry no export re-ran
.vscode/launch.json   Run and Debug: `site build`, then `site serve -p 0`, browser opened
                      on the URL the server prints. Written by `init`, `site init` or
                      `site build` when absent; never edited once it exists.
```

**Two pages, one set of baked files** (`site.SHELLS`: the root `index.html`,
`engineering/index.html`, `marketing/index.html`, `technical.html`). The engineering page
is the outline below, at `/engineering/`, and the address `/` sends a browser there; the
marketing page, at `/marketing/`, tells the story of the design and the choices behind
it from the same `page.json`. Each page is a directory with an `index.html`, because
`python3 -m http.server` (what `site serve` runs) answers `/engineering` with a redirect
to `/engineering/` and never maps it to an `engineering.html`. Each shell's `<head>`
opens with `<base href="../">`, before the import map and the stylesheets, so
`data/…`, `assets/…`, `img/…`, `vendor/…` and every script resolve against `site/` as
they do from the root; a same-page link is written with its page (`marketing/#story`,
`engineering/#claim=C1`), because a bare `#story` resolves against the base and leaves
the page. **The menu** is the first element of each page's body, the same markup on both
but for `aria-current`: `<nav class="viewnav" aria-label="Pages">`, the project's name
(`page.json` `meta.name`, filled by the script), a link to `engineering/` labelled
Engineering and one to `marketing/` labelled Marketing, and the theme button
(`lib/theme.js`, the key `nopekit-theme`). It stays at the top of the screen, works at
320 px wide, and in both themes. In site strings the two are *pages*: a *view* is a view
kind. `technical.html` stays at the root; its back link and `app.js`'s go to
`engineering/`.

**A project from before the two pages** keeps its root `site/index.html`, its own front
page: `site build` never writes the root shell, writes `engineering/` and `marketing/`
beside it when they are absent, and warns while the root shell lacks the
`<meta name="nopekit-views" content="1">` marker (`site status` and `doctor`'s `site` row
say it too): move `site/index.html` aside, then run `nopekit site init` so the address
`/` opens the engineering page. Not `--force`: it replaces all four shells.
`page.js` draws an old root shell as before, with or without the menu.

**Every groundspace opens its own site from the editor** (`site.ensure_launch`). What
slipped through: a groundspace built end to end had a site and nothing to press to
open it. The entry is VS Code's `node-terminal` launch with a `serverReadyAction`; its
pattern is `site.SERVE_READY_RE`, kept beside `SERVE_READY_LINE` (what `site serve`
prints once it is listening), and `tests/test_launch.py` runs the real server against
it. The command uses `nopekit` from PATH, else `python3 -m nopekit` with the
`PYTHONPATH` of the nopekit that wrote it — machine-specific, so on another machine
delete the file and run `nopekit site build`. The bracket's is committed with the
checkout's `src/` as a relative path.

## Commands

```sh
nopekit site init          # scaffold site/: the four shells (refuses to clobber index.html)
nopekit site build         # refresh the renderer, run viewgens, resolve the verdicts,
                            # write data/ + assets/; --live marks build.json live
nopekit site serve [-p]    # python3 -m http.server, no dependencies; names both pages
nopekit site serve --watch # ... and rebuild on a change; the page reloads its data
nopekit site serve --watch --detach   # ... as a process of its own; returns at once
nopekit site serve --stop  # stop the server --detach started
nopekit site status        # what is built, how stale, and whether a live server runs
nopekit site stamp <board> # write as_of into site/content/<board>.md (intro, next,
                            # story too; or --all)
nopekit site badge         # the Made-with-nopekit block at the bottom of README.md
nopekit site vendor        # pull three.js local so the site works offline
```

A site is present only when `site/index.html` exists (`site.scaffolded`): `round`
builds it, `status` and `doctor` show it, `site build` and `site serve` accept it. A
project that tracks only `site/content/` (the bracket) has text for a page and no
page. The root shell decides, never `engineering/index.html`: `round` writes what
`site build` writes when `site/index.html` exists (invariant 8), and a second test
would widen what a round writes. `site init` always writes the root redirect, and a
project from before the two pages already has a root page.

`site build` refreshes the renderer — every template file but the shells
(`site.SHELLS`) — to the installed nopekit's whenever it differs, and says which files it rewrote
(`site.refresh_renderer`). What slipped through (review of P2.1): a page scaffolded
before P2.1 kept a `format.js` that read `blocked` as "its tooling is missing" in a
missing tool's tone, and P2.1 files a crash under `blocked` — so that page read a crash
exactly like a missing tool, and took READY from `readiness.ready`. The shell stays
the project's; nothing else under `site/` is. (From the two pages there are four
shells, and a refresh that overwrote `marketing/index.html` would put back a page its
project had made its own.) `site serve` prints its ready line as before
(`site.SERVE_READY_RE` matches it), then one line that it never matches, naming the
two pages: `pages: <url>engineering/ (engineering) and <url>marketing/ (marketing)`,
on the foreground, the detached and the already-running paths alike.

**`nopekit round` builds the site too** (architecture phase 3a): when
`site/index.html` exists, the round builds it after its tier-0 check, from the same
resolution and the same graph its round record (`.nopekit/round.json`) is computed
from, so the page, the map and the record cannot disagree about a claim. The map
bakes its data from `graph.build` and `graph.tokens` — the typed graph and the status
tokens beside it, joined by id (`map.json`, below); `state.json` carries no graph.

`site build` never runs gates. It renders what `verdicts.resolve` makes of the
verdict cache (`.nopekit/verdicts/`) — which never runs a gate either — and says how
old each result is and whether it is current, because a site that silently re-ran
the cheap gates and not the expensive ones would show a mixed-age picture with one
timestamp on it.

**The page resolves for itself.** `site.state(root, ledger, registry, *, now="",
stale=None, resolution=None)` and `site.build(…, resolution=None)` render the
`resolution` their caller hands them; handed none, they call `verdicts.resolve`
themselves, against the projection they build (the live model, loaded when the
ledger names one; a model that does not load makes every verdict that read it not
current). There is no generous default. What slipped through before 1.2: the site
kept its own copy of the staleness rule — one hash of the whole projection against
the last sweep's — and of the parameter flattening, "byte-for-byte" the CLI's and
kept in sync by a comment (S-28). A default that listed recorded entries as current
would serve every cached PASS whether or not its control was ever shown to fail.
`stale=True` marks every verdict stale; `False` is no override — a caller cannot
declare the cache current.

## View kinds

The site renders exactly these. A pack that emits one gets visualisation for free.

| Kind | Payload | Addressed by a locator via |
|---|---|---|
| `model3d` | GLB + explode manifest | node name |
| `image` | PNG/SVG + optional hotspots | hotspot id, or normalised x/y |
| `chart` | series + acceptance limit line | series key, or x value |
| `table` | rows + columns | row id |
| `field` | scalar field over a mesh | node name + sample index |
| `diagram` | SVG or mermaid source | element id |

## The debugging link: locators

A gate that knows **where** a problem is attaches `Locator`s to its verdict:

```python
return Verdict(
    gate="cad.clash", passed=False,
    detail="1 interfering pair: carriage / guide_roller 0.41 mm^3",
    locators=[
        Locator(view="assembly", target="carriage",
                label="0.41 mm^3 into guide_roller", value=0.41),
        Locator(view="assembly", target="guide_roller"),
    ],
)
```

The site groups verdicts by view, and clicking one isolates and highlights its
targets. Severity defaults to the verdict's own outcome.

**Attach a locator only when you genuinely know the position.** A confident red
highlight on the wrong part is worse than no highlight: it sends someone to
inspect a part that is fine, and they stop trusting the overlay. An unlocatable
failure carries none, and the site says so rather than guessing.

## Viewgens

A pack produces views from `packs/<name>/views/*.py`, symmetrically with gates:

```python
from nopekit.site import viewgen, ViewContext
from nopekit.models import View, ViewKind

@viewgen(id="assembly", kind=ViewKind.MODEL3D, title="Assembly",
         requires_python=["trimesh"])
def assembly(ctx: ViewContext) -> View | None:
    meshes = ctx.params.get("meshes")
    if not meshes:
        return None            # nothing to draw is not an error
    path = ctx.write_asset("assembly.glb", export_glb(meshes))
    return View(id="assembly", kind=ViewKind.MODEL3D, title="Assembly",
                src=path, meta={"explode": derive_explode(meshes)})
```

Rules:

- **Returning `None` is normal.** A CAD viewgen in a project with no geometry has
  nothing to draw, and that is not a failure. It must not raise and must not emit
  an empty view that renders as a broken box.
- **Declare `requires_python` / `requires_tools`** the way a gate does. A viewgen
  whose dependency is missing is reported as unavailable, not skipped in silence.
- **Node names are an interface.** Whatever a `model3d` viewgen names its nodes is
  what every gate in that domain must use in its locators. Publish the naming
  scheme in `PACK.md`.
- **Assets are outputs.** Write them through `ctx.write_asset`, never by hand into
  `site/assets/`, so `site build` can clean stale ones.
- **A picture of a result is a bound viewgen** (`evidence_of="<gate>"`, issue #15): it
  draws one verdict entry's evidence and is checked before it is published. See
  "Figures bound to verdicts".

## Explode: derived first draft, authored where it matters

An explode manifest is `{axes, extent, center, step, movers: {name: {offset, nodes}}}`
with offsets in the GLB's own coordinate space.

`nopekit site build` **derives a working one** so nobody starts from a blank file:

- the **thickness axis** is the smallest global extent
- **stack order** is each mover's centroid along that axis, sorted
- **step** is `max(thickness_extent × 1.15, 14 mm)`
- movers left or right of centre **fan** outward on the widest axis

That is a first draft, not an answer. It gets the twenty bounding boxes measured for
you; it does not know that the lid comes off before the board, that two parts are one
sub-assembly, or that the cable should swing out rather than lift. **Authoring
`site/explode.json` is the normal path, not an escape hatch** — an agent that has just
designed the thing knows the assembly order, and assembly order is intent, not
geometry. Overrides merge per mover, so correcting two parts costs two entries.

The same holds everywhere in this subsystem. Derivation exists to remove
transcription, never to replace judgment: it should leave the author holding a
sensible draft and every lever still in reach.

## Annotations: the explaining half, and it is authored

Locators answer *where is the problem*. **Annotations answer *what is this and why is
it like that*** — and nothing derives them. They are the difference between a spinning
model and a model that teaches.

`site/annotations.json` is authored:

```jsonc
[
  { "id": "carriage",
    "title": "Carriage stopped by the guide roller",
    "oneLiner": "The carriage slides between the housing walls and stops against the roller.",
    "blurb": "Two sentences on why it is this way and what it costs. The thing a reader wants at the moment they are looking straight at the part.",
    "anchors": ["carriage__0", "guide_roller__0"],
    "view": "assembly",
    "href": "docs/decisions.md#roller-end-stop",
    "linkLabel": "decisions.md",
    "claims": ["C1"],
    "params": ["roller_d_mm"] }
]
```

Every field is a judgment call: which parts deserve a pin at all, what a
non-specialist needs to hear, which decision record explains it. That is editorial
work, it is exactly what an agent that just did the design is positioned to do, and a
project whose annotations are thin has a site that spins prettily and says nothing.

`claims` and `params` are the hooks back into the ledger: an annotation can pull its
own evidence, so the pin that explains the carriage also shows the claim that governs it
and the parameter's rejected alternatives.

`nopekit site build` reports annotations whose anchors do not resolve, the same way
it reports dangling locators, and lists parts carrying no annotation so the author can
see what is still unexplained.

## `state.json`

One file, read once by the page. Every top-level key is below;
`tests/test_contracts.py` runs `site init` and `site build` on a copy of
`examples/bracket` and fails on a top-level key this section does not name, or on a
file under `site/data/` this document does not name (PLAN R-14). What slipped
through without it: `locator_problems` was published on every build and missing
from this example, so the one key that says "a gate thinks it is drawing and is not"
was the one a reader of the contract would never look for.

```jsonc
{
  "meta":     { "name": …, "summary": …, "created": …, "revision": …, "model_entry": …,
                "packs": [ … ], "spine_version": …,            // ProjectMeta, verbatim
                "stale": true,                          // no clock: build.json has it
                "stale_reason": "bracket.bed_fit: config.bed_xy 220.0 -> 250.0",
                "records_digest": "<64 hex>",           // the records it was built from
                "resolution_digest": "<64 hex>",        // what it judged from them
                "generated": "nopekit site build — an output … not a source …" },
  "readiness": { "verdict": "…the readiness sentence…", "counts": {…}, "kinds": {…},
                 "by_key": { "checked": 3, "failing": 1, … }, "errored": 0,
                 "tally": [ { "key": "checked", "status": "pass", "n": 3, "errored": 0,
                              "label": "3 checked" }, … ],
                 "ready": false, "all_required_checked": false,
                 "unresolved": ["C1", "C5", "C6", "C7"], "blocking": ["C1", "C6", "C7"],
                 "n_claims": 7, "n_critical": 7, "n_gates": 6, "n_gaps": 1,
                 "milestones": { "print-v1": { "ready": false,             // P2.5b
                     "required": ["C1", "C2", "C3", "C4"], "unresolved": ["C1"],
                     "missing": [], "exports": 0, "last_export": null } } },
                     // last_export: null, or { "article": "<64 hex>", "when", "who" }
  "claims":   [ { "id": "C1", "statement": …, "status": "fail", "key": "failing",
                  "word": "failing", "cause": "failed",
                  "reason": "bracket.deflection : 0.700 mm at 15 N (limit 0.5 mm)",
                  "errored": false, "disagree": false, "acceptance": …,
                  "acceptance_render": "tip deflection <= 0.5 mm",
                  "gates": ["bracket.deflection"], "grounded_by": ["sk"],
                  "verdicts": [ … ], "evidence": [ … ],
                  "unproven": [ { "gate": …, "why": "fail: 0.700 mm …" } ],
                  "compared": ["bracket.deflection"], "not_compared": [ ],
                  "terminal": "closed_form", "terminal_word": "automated", "authority": "",
                  "article": "", "standing": "", "contradicts": [ ],          // P2.5a
                  "mechanism": { "by": "closed_form", "how": "Euler-Bernoulli tip …",
                                 "evaluators": ["bracket.deflection", …] },  // phase 1
                  "requirements": ["R1"],                                    // phase 1
                  "resolves": [ ],                    // phase 3a: a physical claim's assumptions
                  "board": "strength",                // phase 5: its board, or "other"
                  "physical_result": { "passed": true, …, "counts": false,
                                       "token": "not-counted",  // pass | fail | not-counted
                                       "why": "a pass recorded from an agent session does not count",
                                       "recorded": "recorded by Dana, from an agent session",
                                       "heading": "A physical result was recorded:",
                                       "outcome": "pass" },   // phase 5: "pass" | "fail", the word key the page reads in place of `passed`
                  … } ],
  "requirements": [ { "id": "R1", "statement": "…", "source": "interview",   // phase 1
                      "status": "fail", "key": "failing", "word": "failing",
                      "errored": false, "claims": ["C1", "C2", "C3", "C6"],
                      "worst": ["C1"], "disagree": [ ] } ],
  "verdicts": [ { "gate": …, "passed": …, "ok": false, "status": "fail", "detail": …,
                  "measured": …, "limit": …, "comparator": "<=", "settles": "tip deflection",
                  "margin": -0.3994, "margin_why": "", "locators": [ … ], "views": [ … ],
                  "unanchored": true, "evidence": [ … ], "rho": …,
                  "basis": "sourced",                                        // phase 2
                  "cached": true, "fresh": true, "stale_reason": "",
                  "order": 0,                 // phase 5: reading order, baked (errored first)
                  "evidence_assets": [ "assets/evidence/…/deflection.json" ], … } ],
  "views":    [ { "id": "assembly", "kind": "model3d", "src": "assets/assembly.glb", … },
                { "id": "sweep", "kind": "table", "data": {}, "data_url": "data/views/sweep.json", … } ],
  "locator_problems": [ { "gate": "cad.clash", "view": "assembly", "target": "carriage",
                          "severity": "unknown-target",
                          "problem": "view 'assembly' declares no node 'carriage'" } ],
  "params":   [ { "name": "thickness", "value": 7.0, "units": "mm",
                  "rationale": …, "derived_from": [ ], "source": "", "grounded_by": [ ],
                  "tags": [ ], "home": "model/bracket.py Config.thickness", "record": "",
                  "model_error": "", "gates": ["bracket.min_wall"],
                  "read_by": ["bracket.min_wall"], "sourced_from": [ ],      // phase 3a
                  "rejected": [ { "value": "4.0 mm", "why": …, "evidence": "",
                                  "origin": "model/bracket.py PARAMS" } ],
                  "changed_in": "", "defended": true, "derived": false } ],
  "inputs":   [ { "id": "sk", "kind": "sketch", "path": …, "extractions": [ … ],
                  "extracted": true, "proposer": "", "basis": "", … } ],    // phase 2
  "gaps":     [ … ],
  "decisions":[ … ],
  "rejections": [ { "decision": "print-in-petg", "index": 2,               // phase 2
                    "value": "a machined 6061 aluminium bracket",
                    "type": "preferred_against", "by": "Robin Okafor", "input": null,
                    "returns_if": { "claim": "C3", "quantity": "margin",
                                    "comparator": "<", "value": 0.1 },
                    "returns": { "state": "does-not-hold", "why": "", "claim": "C3",
                                 "status": "", "observed": 0.987, "gate": "bracket.bearing",
                                 "detail": "" },
                    "preference": { "state": "not-recorded", "name": "Robin Okafor",
                                    "who": "", "when": "" } } ],
  "rebuild":  [ { "article": "<64 hex>", "claims": ["C5"],
                  "moved": ["config.thickness 7.0 -> 7.5"] } ],             // P2.5a
  "trace":    { "key": "fb145d48c5a8", "round": 2, "state": "traced" },  // phase 3b;
                                     // null when the caller has none
  "amber":    { "C2": "holds", "C3": "holds" },   // phase 3b: each Checked claim on an
                                     // unconfirmed assumption — both inside the
                                     // resolution digest, so an amber move stales the page
  "words":    { "pass": { "key": "checked", "word": "checked", "term": "Checked",
                          "plural": "checked", "hint": "every evaluator passed on the current
                          inputs — checked does not mean true" }, …,
                "errored": { "key": "skipped", "word": "skipped", "term": "Skipped", … } },
  "outcome_words": { "pass": "pass", "fail": "fail", "skipped": "skipped",
                     "errored": "errored" },
  "phrases":  { "invalidated": "invalidated",
                "disagree": "the resolver and the verdicts disagree — a defect to report",
                "result_recorded": …, "result_counts": …, "result_not_counted": …,
                "judgment_title": "Expert judgments", "judgment_blurb": …,
                "chart_table": …, "chart_empty": …,
                "need": { "open": "identified", "proposed": "proposed", … },
                "outcome_hint": { "pass": "the evaluator ran and passed", … } }
}
```

Everything the page shows is in here, so the page is inspectable with `curl`, and
an agent can read the site's state without a browser.

- **Statuses and their words (P2.1).** A claim row's `status` keeps the enum value
  (`ClaimStatus`), so a page scaffolded before P2.1 still renders; beside it are
  GLOSSARY §3's reading — `key` (the token), `word`, `cause` (`claims.ClaimCause`),
  `reason` (`report.reason`, in full) and `errored` (Skipped by a crash: the page
  paints it in Failing's tone, invariant 2). `words` maps every enum value, and
  `errored`, to `{key, word, term, plural, hint}` from `report.HUMAN`, and
  `outcome_words` each verdict row's status to its outcome word: `format.js` takes
  every label and hint from these two — the site owns no status or outcome word
  (PLAN D-16). `phrases` carries the rest of what the page says in the ledger's
  words (`report.page_phrases`): `invalidated`, the flag on a verdict whose read set
  moved and on the top bar when `meta.stale` (a verdict invalidated, which is not a
  claim reading Stale: an invalidated fail stays Failing), and `need`, each gap
  record's state (`identified` for `open`, GLOSSARY §6), `outcome_hint`, each
  verdict chip's title, and `chart_table` and `chart_empty`, what a chart view's box
  says where the chart library did not load and where the view has no series (the
  engineering page's `words.figure.table` and `chart_empty`, issue #15 P2.5). A
  claim row's `reason` is why
  it reads what it reads — no evaluator, unrun, no owner recorded, needs an article —
  and the page prints it, never a sentence of its own; a Checked row (`cause:
  "checked"`) prints none. What slipped through (review of P2.1): the page said "No
  gate covers this claim" itself for an unowned assumption and a claim waiting on an
  article alike, `≈ STALE` on its top bar over a project where no claim read Stale,
  and a lone `—` under every Checked claim. The claims are in severity order (Failing · Skipped, errored ·
  Skipped · Gap · Open · Stale · Pending build · Assumed · Checked; ties critical
  first, then record order), and the page keeps it: what slipped through (P2.0 F-5),
  record order put a skip above the crash it should sit under.
- **The physical path (P2.5a).** A claim row is `report.claim_json`: beside its
  record fields, `terminal` (as declared), `terminal_word` (`automated`,
  `measurement`, `assumption`, `expert judgment: <authority>`), `authority`, `article`
  and `standing` (the judge's: `current`, `article-moved`, `not-counted:<why>` …) and
  `contradicts` (the evaluators a Failing contradiction names). `physical_result`
  carries `counts` — the judge counted it (a pass on its current article, or any
  fail) — `why` in words when it does not, `recorded` (who, and from where) and
  `heading` (the block's first words: a physical result's, or a judgment's), and
  `token` (`report.RESULT_TOKENS`: `pass` a pass that counts, `fail` a fail that
  counts, `not-counted` any other), the one key either page colours a result by,
  through format.js's outcome table: the page paints a result's ok tone ONLY on a
  pass that counts, never on `passed` (what slipped through, critique 3 of the P2.5a
  design: an agent's pass on a Pending build claim read green on the page alone), and
  a counted fail in Failing's tone (the follow-up after phase 3b: the drawer coloured
  the block on `counts` alone, and a counted fail read green). No page script reads
  `counts` (`test_site_truth.BROWSER_TRUTH`). `rebuild` is the rebuild prediction
  (`claims.rebuild`). `phrases.disagree` is the title of a claim whose status its
  evidence does not back — the page's own "contradict" was a second sense for the
  ledger's *contradiction*. A claim whose `terminal` is `human` is grouped apart,
  under `phrases.judgment_title` and `judgment_blurb`, and tagged with its
  `terminal_word` (review of P2.5a: it sat under the assumptions' "owner" blurb with
  no word naming its authority). A claim whose file is gone and whose results file
  holds a fail is a row too (`standing: "removed"`, `store`'s `Ledger.removed`): its
  fail still counts.
- **What lost, and where an evaluator's logic comes from (architecture phase 2).**
  `rejections` is `summarise()["rejections"]` (`claims.rejection_rows`), tokens only:
  every decision's every rejected alternative with its `type` (`untyped` for one
  written before phase 2), its person (`by`, a nominee), its input, its return
  condition as written, that condition's state now (`returns.state`: `holds`,
  `does-not-hold`, `cannot-evaluate` — `why` says which: the claim is not Checked
  (`status` its token), no value, no margin, no limit, a band, no claim —
  `not-machine-checkable` or `none`) and its preference's standing (`recorded` only as
  its person recorded it with `nopekit prefer`, invariant 13). A verdict row's `basis`
  is its evaluator's (`established`, `sourced`, `model-only`, or `undeclared` with
  none or no spec); an input row's `proposer` is the one that produced it and `basis`
  that proposer's, looked up (`""` with none, `undeclared` when no discoverable pack
  declares it). None of it is a status. The page draws none of it until the map
  (phase 5).
- **Requirements and mechanisms (architecture phase 1).** `requirements` is every
  `requirements/<id>.json` in record order, each row `report.requirement_json` over
  `claims.requirement_statuses` — the worst of the claims that serve it (`worst`, by
  severity, a crash's Skipped above a plain one), Gap with none — so the page never
  folds a status itself; `word` carries `skipped, errored` for a crash. *Ready* never
  reads it. The page draws no requirement until the map (phase 5). A claim row gains
  `mechanism` (`{by, how, evaluators}` as declared: `by` the terminal, `how` what would
  settle it, `evaluators` a cross-check, never coverage) and `requirements` (the ids it
  serves); the flat `note` stays and is the same text as `mechanism.how`, which the
  claim panel labels "Mechanism" (it was "Note"), and the flat `terminal` is always
  `mechanism.by` — one field, two spellings, never two answers. A requirement row's
  `disagree` names the serving claims whose `disagree` is true (D18 carried up:
  empty under the composition, and a requirement over one is never a clean
  Checked).
- **No `partial` (P2.1).** Under GLOSSARY §3's composition a Checked claim has no
  covering evaluator that did not pass, so PARTIAL went. `unproven` names each
  covering gate that produced no pass that counts, its reason led by the fact
  (`unrun`, `errored: …`, `skipped: …`, `unqualified: …`, `fail: …`); `disagree` is
  `true` only when the resolver says `pass` while one exists — a contradiction, a
  defect, which the page paints loud, never as a lesser success.
- `readiness`: `verdict` is the readiness sentence; `counts` (by enum value) and
  `ready` (nothing stops `check`) are kept for older pages; the page's READY headline
  reads `all_required_checked` (GLOSSARY §4: every required claim Checked on the
  current inputs) and its count strip `tally` (`report.count_bits`: HUMAN's words, a
  crash counted apart, `7 skipped (6 errored)`); `by_key` and `errored` are the same
  counts by token; `unresolved` the required claims not Checked; `n_gaps` counts gap
  records, not claims reading Gap (`by_key.gap` is those). `milestones` (P2.5b) is
  `claims.unresolved` per milestone, as last evaluated: `ready`, its `required` ids,
  the `unresolved` ones, the `missing` ones (no claim file: never ready), how many
  `exports` are on record and the newest one's article hash, `when` and `who`. The page renders it; it never
  re-derives one, and only `nopekit export` re-executes what a spend requires.
- **Which value is the claim's (P2.4).** A claim row's `compared` lists the
  evaluators whose value is compared with its acceptance condition
  (`claims.compared_gates`: the verdict measures the claim's quantity, in its units),
  and `not_compared` the passes whose value measures something else
  (`claims.not_compared`, a guard of another of its evaluators left out). The page
  shows the first of `compared` as the claim's value, and none when it is empty: it
  never picks one itself. What slipped through: it headlined the first passing
  verdict, so failing C1 showed its guard's passing `8.57 L/h`. Beside that value it
  shows the CLAIM's limit — `limit_text`, written by `site.state` from the claim's
  acceptance condition — never the verdict's: a compared pair is the one kind whose
  two limits can part (review of P2.4: Failing C3 summarised as `0.195 MPa / 15 MPa`
  against a claim of `<= 0.1 MPa`, S-35's slip on the page).
- **The margin (P2.4, D-17).** Every verdict row carries `margin` — `claims.margin`,
  a signed fraction of the limit, > 0 inside and < 0 past, 6 significant figures — and
  `margin_why`, why there is none (`no-value`, `no-limit`, `no-comparator`, `band`,
  `no-side`, `zero-limit`, `disagrees`, `no-verdict`); `margin` is null exactly when
  `margin_why` is not empty. `comparator` (which side of `limit` passes) and `settles`
  (the quantity) sit beside them. The page draws the margin and computes none.
- `verdicts` holds one row per gate the resolver has a verdict for — registered
  gates in registration order, then gates this project does not register, by id — so
  a verdict for an unregistered gate still reaches the page, as a stale row.
- A verdict row's `ok` is the only predicate the page may paint green; `status` is
  `pass`, `fail`, `skipped` or `errored`, from `Verdict.outcome` (P2.1: it read the
  flags, so a junk truthy `passed` showed `pass` beside `ok: false`) — or, from P2.3,
  `unqualified` for an evaluator not qualified at its version, beside a
  `qualification: {token, reason, text}` (the judge's token, `report.qualification_reason`'s
  words, and `text`, the row's sentence `unqualified: <reason>` the page shows — never
  `error`, which carries the token as R-2's fallback): never `errored`, a crash's word for
  something that crashed nothing, and Gap's
  tone on the page (`lib/format.js`'s `VERDICT_STATUS`). A gate never run reads so too
  (`not yet qualified at this version`), its row with no entry behind it. From the
  review of P2.4, a pass outside its evaluator's operating context reads
  `outside-context` — never `unqualified`, since its evaluator still is qualified —
  with its `qualification.text` `outside operating context: …`, Gap's glyph and tone,
  and its word and chip title from `outcome_words` and `phrases.outcome_hint` (what
  slipped through: the page painted `? UNQUALIFIED` beside that row text, while
  `check` and `status` counted it apart). `unanchored` says out loud that a failure
  carries no locator, instead of leaving an overlay that looks broken.
- How the resolver reached each row, said on the row: `cached` — it is a cache
  entry's verdict as recorded (its `tier` and `pack` are the ones it ran under);
  `fresh` — and it is current and counts (its inputs, code and control are the ones
  it was measured with); `stale_reason` — why it is not current, in the resolver's
  words (`config.bed_xy 220.0 -> 250.0`, `not yet qualified at this version — the next
  check run qualifies it`, or `… nopekit check --tier 2 qualifies it` for a costlier
  path's entry, `gate not
  registered in this project`, or — from P2.2 — `prerequisite <root> invalidated: …`
  / `prerequisite <root> unrun` for a verdict kept under a prerequisite that is not
  current), `""` when it is. A
  claim covered by a row with a `stale_reason` is never `pass`. `unseen` (issue
  #15) is true on a row whose gate is not current only because it reads inputs
  nopekit cannot see: the technical page flags it `≈ unseen inputs` (the phrase
  `unseen`), never `≈ invalidated`, and the engineering page's Failing cause text
  says that its fail is the last check run's.
- A row carries `blocked_by` and `blocked_kind` (P2.2) — `[]` and `""`, or the roots
  of the prerequisites that were not established when its gate was not run, and the
  first one's kind. Such a row is `skipped`, never `ok`; its `skip_reason` says
  `prerequisite failed: <root>` or `prerequisite not established: <root> (<why>)`, and
  its claim's `reason` says the same in the table's words. The page reads neither key:
  `status`, `ok` and the claim's own `reason` already say it.
- A row's age is not here (architecture phase 5, SPEC 32): `build.json`'s `verdicts`
  hold each result's `when` — the obs run that last wrote or hit its entry, else the
  entry's git commit time, else `""` — and `age_s`, `null`, never 0, when either end is
  unknown (an age of zero renders as "just now", the exact lie a staleness display
  exists to prevent). `order` is the row's reading order (errored, fail, unqualified,
  outside its context, skipped, pass, then the gate id), which the page reads.
- `locator_problems` lists every locator that cannot be drawn — `missing-view` (it
  names a view that does not exist) or `unknown-target` (a node the view does not
  declare). The locator itself stays on its verdict; nothing is dropped.
- A view whose inline `data` is too big to sit in the summary is split into
  `data/views/<id>.json`: its row keeps `"data": {}` and gains `data_url`. The build
  seals the digest of each row with its view file into `page.json`'s `meta.views`
  (`site.view_digest`). A viewgen that is unavailable or crashed keeps its row from
  the last build only at those bytes, and `site status` reads a row or a view file
  with other bytes as edited by hand.
- A chart view's row (issue #15, P2.5) gains `chart`: `{limit, current, spec}` from
  `page.view_chart`, where `spec` is the whole Vega-Lite spec `page.chart_spec`
  bakes (its series, its limit and points, and the verdicts' anchors on it: a
  located series drawn heavier, a located x a rule in its severity's look), with none
  of `page.VEGA_FORBIDDEN_KEYS`, or null for a view with nothing to draw. The
  technical record hands it to `lib/figures.js`'s `drawChart`, as the engineering
  page's cards do, and reads no chart's payload; where the library does not load it
  shows the spec's numbers as a table. An unbound view keeps the limit and the points
  it states itself (the first limit on each axis, its `current`, `marker` or `now`
  with the `margin_text` the build bakes beside it in `data`); its axes are its
  `data.x` / `data.y`, else its `meta.x` / `meta.y`, else `meta.x_label` and the
  rest. A spec over the same 8 KB is split into `data/views/<id>~chart.json`: the
  row keeps `"chart": {}` and gains `chart_url`, and the file is sealed with the row
  as a view file is. *Rejected:* `lib/chart.js`, the hand-drawn SVG it replaced (the
  author's rule of 2026-10-21: draw with a trusted library; its text fell to about
  5 px on a phone, and it read no `markers` and no axis in `meta.x`).
- A bound view (issue #15: a viewgen with `evidence_of`, drawn from its gate's served
  verdict entry) keeps `"data": {}` too. Its `src` is its still (a PNG) or, for a 3D
  view, its 3D file (a GLB or a glTF); `data_url` is a chart's series file;
  `field_src` a committed 3D file beside a still; every one of them is under `assets/figures/<gate>/<entry>/`, beside
  the tracked sidecar `<view>.view.json`. It gains `binding`: `{gate, entry,
  currency, currency_word}`, where `entry` is the result the picture was drawn from
  and `currency` is `current` (the served entry, fresh), `stale` (the served entry,
  not current), `unseen` (the served entry, not current only because its evaluator
  reads inputs nopekit cannot see: `claims.unseen_gates`, issue #15 D1), or `older`
  (another entry of the gate, not always an earlier one:
  after A, B, A the picture beside A is B's, the later run; the served one has no
  picture yet), with its fixed sentence. A bound view with no picture has no row. A
  bound view is never the page's representation or a marketing alternate as a plain
  view: it shows there only as its figure (`representation.figure`), with its chips
  and its currency (G8). The
  technical record prints the sentence and the gate and entry under the view
  (`stage.js`); it draws a bound chart's series alone, from the row's `chart.spec`,
  with no limit (the chart file holds none, and the limit is the claim's, on the
  engineering page).
  Its `meta` holds the pack's `caption`, `alt`, colour `scale` (or `scales`, design
  P2), `marks` and `light`, each only where the sidecar states it, none of which
  vouches for a number in prose; a field whose `light` is `scene` is turned lit, with
  a "Flat colours" button.
- `params` is the parameter view (`modelio.param_view`, `ParamView.to_dict`), never
  the param records: every parameter the model holds, in its field order, then any
  record the model no longer defines. `value`, `units`, `rationale` and
  `derived_from` are the model's (a record's `units` or `rationale` only where the
  model states none); `home` is where the value lives; `source`, `grounded_by` and
  `tags` are the record's (`record` its path, `""` when there is none); `rejected`
  is both homes' losers, each with its `origin`; `gates` are the registered gates
  whose last executed reads name it; `read_by` (architecture phase 3a) the
  registered evaluators whose served verdict entry read it — the graph's `reads`
  edges, which a fresh clone has too, so the two can differ — and `sourced_from` the
  graph node ids that source it (`input:<id>`, and `claim:<id>` for an assumption the
  value is); `changed_in` is the newest decision that moved it. `defended` is the one undefended rule `doctor`, `status` and the report print
  (`modelio.undefended_params`): `false` for a number the model holds that no home
  defends, `true` otherwise, and `null` where the model holds no number — `value`
  is then `null` too, and `model_error` says why (the model does not load), or it
  is `""` and `home` is `""` (a record the model no longer defines). A cached number
  never stands in. What slipped through (review, checkpoint 1.3): the page read the
  param records — sparse from 1.3, holding only what the model cannot — so the
  bracket's page showed no parameter at all, and a record holding only `"source"`
  was shown at `null` and flagged undefended while `doctor` said every parameter
  carried a rationale.

### `meta`

Every key of `meta`, and where it comes from. `tests/test_contracts.py`
(`SiteMetaKeysAreDocumented`) builds the bracket's page and fails on a key this table
does not name — the top-level check passes a `meta` whatever it holds, and `meta` is
where the page's staleness lives.

| Key | From | What it says |
|---|---|---|
| `name`, `summary`, `created`, `revision`, `model_entry`, `packs`, `spine_version` | `.nopekit/project.json`, verbatim (`ProjectMeta`) | the project, as its own record states it; a field `ProjectMeta` gains arrives here and in this table together — except `people` (architecture phase 6), never baked (`site._UNPUBLISHED_META`): the page is published, and the names and addresses a round may commit as are not its to publish |
| `stale` | the resolution: any row not current | whether the VERDICTS are current |
| `stale_reason` | the resolution | the stale gates with their reasons (the first three, then `(+n more)`) |
| `stale_unseen` | the resolution and `claims.unseen_gates` | every stale gate is stale only because it reads inputs nopekit cannot see (issue #15): the page's notice, the technical page's flag and its banner read it, never the Stale claims |
| `stale_check` | `report._tier_flag` over the stale gates | the command that reaches them (`nopekit check --tier 2`), `""` when nothing is stale; `site status` and the technical page's banner print it |
| `records_digest` | `store.records_digest(root)` at build time | which records the page was built from — whether the PAGE is current, half one |
| `resolution_digest` | `site.resolution_digest` of this document, set last by `site.state` | what the page judged from the records and the verdict cache — whether the PAGE is current, half two (phase 3a's `judgement_digest`, renamed) |
| `generated` | a constant | that this file is an output of the records and the verdict cache, not a source |

- `stale` and `stale_reason` answer for the verdicts, per gate. There is no sweep
  timestamp: the run history is gone (the verdict cache and git are the history), and
  one sweep's time and one model hash could say THAT something moved, never which
  result it touched.
- `records_digest` is a sha256 over the record files' paths and bytes and
  `.nopekit/project.json` (on a legacy project, its `ledger.json`). It is the first
  half of how the PAGE's own staleness is decided — a different question from `stale`:
  `nopekit site status`, `status` and `doctor` compare it with the records now, and a
  page whose records moved reads "the records have changed since the site was built",
  with the fix, `nopekit site build` (a page with no digest, from an older build,
  reads stale too). What it replaced compared the mtimes of `ledger.json` and
  `state.json`; from checkpoint 1.3 `ledger.json` is a generated index every command
  rewrites, so a page built from unchanged records read stale after any `status`, and
  a record edited by hand read current until a command caught the index up (cli:H16).
  The page itself computes nothing from it.
- `resolution_digest` is a sha256 over this document less the two digests, each
  verdict row's `evidence_assets`, `views` and `locator_problems` (the document holds
  no clock from phase 5: `build.json` does):
  the claim statuses and their words, the readiness, every verdict row with
  `cached`, `fresh` and `stale_reason`, the parameters with the gates that read them,
  the gaps and the records shown. The page renders the resolver's judgement of the
  verdict cache against the live model, which no record holds: a `check` that FAILs C1
  moves no record, and with `records_digest` alone `site status`, `status` and `doctor`
  called a page still showing C1 PASS "current with the records" (review,
  `repro_site`). So, once the records match, the three build the document a rebuild
  would write now — `site.state` over the same resolution `status` prints, never
  running a gate, fixture or viewgen — and compare its digest. A page whose judgement
  moved reads "what the page shows has changed since the site was built", naming up to
  three claims whose status moved (`C1 pass -> fail`), else the verdict rows that did,
  else the parameters (`thickness 7 mm -> 8 mm`, or `thickness rationale` when only a
  docstring moved), with the fix, `nopekit site build`; a page with no `resolution_digest` (an older build)
  reads stale too. A model edited and not yet checked moves it as well: a rebuild would
  show those rows stale. The clock is left out because a `check` that changed nothing
  moves every `when` (a cache hit is an obs run); the views because they are drawn by
  viewgens the question never runs, and no verdict rests on a view — a model edit no
  gate reads leaves the judgement current and the drawing old until `site build`.
  Anything a later `state` adds is judged by default. The page computes nothing from
  it either.

## The outline

The engineering page, `engineering/index.html` (architecture phase 5, SPEC 28; from
SPEC 40 it is one of two pages, and an old project's root `index.html` is read in its
place), reads top to bottom like the PETGasus front page, with the grounding map in place of its go/no-go section: the name; the
representation (the first `model3d` view in the viewer with its explode control, else
the first image or diagram view, else a box saying what view to add —
`page.json`'s `representation`); the verdict sentence right under it, with the count
strip and the limits line; the description (`content/intro.md`, else the project's
summary); the map; one section per board; next steps; made with nopekit, with the
badge. The shell carries the outline's marker,
`<meta name="nopekit-outline" content="1">` (`site.OUTLINE_MARKER`); `site build`
warns when the shell lacks it or carries an older one, naming the file it read
(`site.outline_of`: `engineering/index.html` when it exists, else `index.html`), and
still writes `page.json` and `map.json`. Today's panels (every claim, every verdict with its
locators, parameters with what lost, evidence, gaps, decisions) are
`technical.html`'s, rendered by `app.js` from `state.json`, unchanged in substance.

## The renderer

What the template does with the two documents (architecture phase 5, the template
side). `page.js` draws the outline from `page.json` and `map.json`, reads
`state.json` for its `views` alone (the representation) and `build.json` when it is
there; `technical.html` is drawn by `app.js` from `state.json`, as before. The
verdict block (`lib/verdict.js`), the theme button (`lib/theme.js`) and the
build-failed banner with the built line (`lib/banner.js`) are lib modules both pages
import (SPEC 40):
one renderer of the verdict, so the two pages cannot read it two ways.

- **The hero** follows the PETGasus front page: on a screen 980 px wide or more, the
  name, the verdict and the description on the left and the representation on the
  right; on a phone, the outline's own order. The verdict sentence's lead, up to the
  first full stop followed by a space, is bold; the count strip is `verdict.tally`,
  each chip a glyph, the baked label and a colour; `verdict.notice` shows only while
  `verdict.stale` is true. A `model3d` representation runs `lib/viewer3d.js` with the
  PETGasus explode pill (hidden when the view has nothing to explode).
- **The map** (`lib/map.js`, `renderMap(host, map, page, {onSelect, onBoard,
  onRescale, worstFirst})`) is plain SVG at a native width of 1400 units, drawn as a
  transit map (architecture phase 3b, the template side; it overturns phase 5's
  straight tracks). Drawn narrower, its text grows to 12 px on screen at least, and
  its row, header and zone-box heights grow with the text (`metricsFor` at the drawn
  scale); a resize to another size calls `onRescale`, and the page draws the map
  again. Three columns:
  - **Left** (x 12-248): a card per requirement (the token's glyph and colour bar,
    the bold id, the statement in three lines at most, its full text as the
    tooltip), then **every zone** as a box: its name and count, a 96-unit bar of
    `sources` (design choice, datasheet, estimate, assumption, no source
    recorded), and an amber diamond with its id for each `estimate` id. A box sits at the mean height of
    the stations and line starts it feeds, in its baked `rank`, pushed down when
    it meets the box above; an `idle` box is faded with `words.trace.zone_idle`
    as its tooltip. Under the boxes, `words.trace.zones_summary`, a key of the
    sources some bar draws and `words.trace.feeds_key`.
  - **Middle** (x 286-800): the stations, one equal column per (layer, column)
    of `map.layers`, so a station's x is its trace order. A station's lines sit
    side by side at a pitch of 7, in the order of their rows, centred on the mean
    of those rows; two capsules in one column never meet (a gap of 34 holds the
    lower title). A line rides its `stops` in their baked order with a filled dot
    where it reads (`stop_kind` `read`) and a hollow one where the trace puts the
    value first (`upstream`); it starts 22 units left of its first station (a
    tap circle when a zone feeds it), bends once just after each station it
    leaves, and fans into its row late, in a band right of the last column. In
    each bend zone the lines that rise turn top-first and the lines that fall
    turn bottom-first, with 45-degree runs and rounded corners (radius 10), so
    lines that share a bundle never cross. The part of a line before its first
    `read` stop is faded under `words.trace.upstream_stop` (an inferred order).
    A line with no station is a stub on its row (a longer one, with a halt, when a
    zone taps it; a dashed circle titled `words.trace.unseen_line` when `unseen`),
    and its `sets` are springs: thin dotted curves from the stub back to each
    station, amber for an assumption, titled `words.trace.sets`, and pending blue
    for a physical claim, titled `words.trace.settles`. A line that only
    passes a capsule is drawn over it on a bridge, and every line has a halo
    under its stroke, so where two cross the later one reads over. **Feeds** are
    thin curves from a zone's right edge to the left edge of each station in
    `map.feeds` (amber and dashed when its `estimate` is not empty), and taps
    from a zone to the start of each line it feeds directly (`lines[].taps`).
    A station's title and `value_text` go where they cross the fewest tracks
    and no other title or capsule (above, below, then beside), shortened with
    a tooltip only when no spot holds them.
  - **Right** (x 872-1392): the boards, a fixed 520 units: a board is a header
    (title, line, the token's glyph and word, its tally) and one two-line row per
    claim (glyph, id, statement; word, an amber diamond when the line carries
    the `amber` mark, the requirements it `serves`, and `value_text`, or the
    claim's `cause_text` when it has no value). The rows fix each line's end.
  A station no line stops at is listed under the map ("No line stops here
  yet."). Text is measured after placement (`getComputedTextLength`); it never
  moves a capsule, a box or a track. `amber` draws as a band under the line from
  the station its `enters` names (the whole line when null); `disagree` as a red
  casing. The caption above the drawing is `words.trace.caption` (written by
  `page.js`, as `engineering/index.html` is the project's own), and the line under it is
  `map.trace.notice`, `map.trace.text` and, while `build.json`'s `tracing` is
  true, `words.trace.running`. The legend gains an amber chip
  (`words.trace.amber_legend`, the count of marked lines) only when a line
  carries the mark; it lights those lines.
- **Every token's look is one table**, `TOKEN_LOOK` in `lib/format.js`: checked ✓
  green solid, failing ✕ red heavy, errored ! red heavy dashed, stale ≈ dim dashed,
  skipped ⊘, gap ?, open ◌, pending build ◻ and assumed ≡ dotted; an unknown token a
  neutral dot and its raw token as the word, never a pass's look. The word beside a
  glyph is always the baked `word`. Marks: `amber` is a casing under the line,
  `disagree` a red casing and a "!" beside the id, on a board's header and on a
  requirement's capsule; every chip (`tokenChip(token, word, {marks, notices, trace})`:
  the phone list, a board's section heading, next steps, the drawer, the marketing
  page's requirement and decision cards) shows `disagree` as a "!" labelled with
  `words.notices.disagree`, and `amber` as a "◆" (`role="img"`) labelled with
  `words.trace.amber_flips` (`words.trace.amber_group` on a row that stands for
  several claims: a requirement, a board, passed as `group`), the chip's tone unchanged
  (the red `disagree` casing wins);
  the phone list, the section and the drawer say those notices in words, and the
  marketing page's requirement card says them under its chip, as it has no drawer.
  What slipped through (review of SPEC 40): the marketing page drew a requirement or
  decision whose claim an unconfirmed assumption flips as a plain green chip, and
  `page.status_problems` held the `amber` mark on map lines only.
- **Interaction.** Hovering a line, a row or a capsule lights its lines (and the
  claims a line `rests_on`, from `edges.rests_on`, wider and in their OWN status
  colour: the accent is near the pending-build blue), the feeds into its stations
  and the zones that feed it, and dims the rest; a station lights every line
  through it and its feeds; a zone its feeds and taps and every line it feeds
  (`fed_by`); an assumption's diamond (or its row) the feeds it sets, the stations
  those feeds enter, and every claim that `rests_on` it; a board header lights its board and scrolls to its section on a click; a
  click on a line, a row or a capsule opens the drawer and keeps its lines lit. The
  legend lights every line of a token. Pan is a drag, zoom a pinch, ctrl or cmd and
  the wheel, or the +, − and fit buttons; `0` resets. "Worst first" sorts the rows
  inside each board by the baked `severity` (then `row`) and draws again; it is off
  by default. Below 760 px the map is `mapList`: the requirements as chips, then each
  board with its rows, each row with its requirements, amber in words when marked,
  `Rides:` the stations it reads in stop order, the upstream ones apart under
  `words.trace.upstream_rides` (review of phase 3b: they read as rides), its `sets`
  after a diamond, `words.trace.unseen_line` when `unseen`, and `Fed by:` its zones
  (`words.trace.rides`, `words.trace.fed_by`); the caption above it speaks of
  cards (`.cap-phone`), the one above the drawing of lines (`.cap-desk`). With the
  drawer open on a wide screen the map scales into the column beside it, so the
  claims lane, with every glyph and word, stays in view.
- **The drawer** (`lib/drawer.js`) shows a claim's blocks in the floor's order (the
  same function, `claimBlocks`): the chip, `cause_text`, `reason`, each value's `value_text`
  (the binding one bold), the acceptance, the mechanism, each evaluator with its
  outcome, detail, basis and evidence links, a recorded result, what it rests on,
  and what settles it, then a link to `technical.html#claim=<id>`; a claim with a
  baked `amber` adds its word and each run that flipped it (the floor too). A
  requirement or a board lists its claims; a zone its feeds (each station with the
  choices that move it, and each line it taps) and its values; a station what moves
  it (each zone with its choices) and its values, with `value_text` and the
  evaluators that read each (`edges.reads`), and the claims for which it is an
  upstream stop with `words.trace.upstream_stop`. `#claim=C1` (and `#requirement=…`,
  `#zone=…` and so on) in the URL opens it; the marketing page links here as
  `engineering/#claim=C1` and `engineering/#requirement=R1`.
- **A board's section** (`lib/sections.js`) is its title, line, chip and tally, then
  a **claim card** (`lib/figures.js`, issue #15) for each of its claims that has
  `figures`, open, in the board's order: the claim and its chip; the `value_text` of its
  `binding` value in large type with `words.figure.judged`; a tile for each other
  value; the currency sentence of each figure that is not `current` or `none` (with
  `drawn_text`) above the pictures, in a dashed frame; the charts (the baked `spec`
  handed whole to `vegaEmbed`, or, where the library does not load, its data rows,
  limit label and margin words as a table under `words.figure.table`, never a drawing
  of our own); the stills, side by side from 720 px and stacked under it, each with its
  title and the chips of every claim its gate covers (`tokenChip` with marks and the
  trace's words), the PNG linked to itself, each mark as an HTML label at its baked
  `at`, the legend (a CSS gradient of the scale's `stops`, ticks at their baked `at`
  with their `text`, the `under` and `over` swatches with their words, `source_text`)
  and the caption; a figure with `scales` (design P2) has one legend for each, in a
  grid (`.fig-legends`) that sets them side by side where each has 300 px and stacks
  them where it does not, each title led by its glyph (a filled swatch for `on:
  "surface"`, a line for `on: "lines"`) and its baked `on_word`; a still with `light:
  "scene"` has a border (`--fig-border`, `--fig-radius`); "Turn it in 3D" (`turn.word`)
  only where a 3D file is there to turn
  (`field_src`, or `build.json`'s `live_figures[<figure id>]` with `turn.live_word`),
  which mounts the viewer in field mode when pressed, lit with a "Flat colours" toggle
  (`turn.flat_word`) when the figure's `light` is `scene`; then `binding_text` and each
  figure's `provenance`. A figure with no file shows its sentence alone. Vega, Vega-Lite
  and vega-embed load on demand from `vendor/vega/` when it is there, else from the
  CDN, at the versions `site.VEGA_VERSIONS` pins (`site vendor` copies them too). The
  browser looks each currency word up (`format.currencyLook`) and compares none.
  Then the prose (its `notice` in a callout above it when `stamp` is not `current`; each
  `num` run with `match: false` in a `<mark class="nk-unmatched">` titled
  `words.notices.unmatched`, kept on one line with a dagger, never the gap's "?", and
  `words.notices.unmatched_note` in words under a piece that has one; claim runs as
  buttons that open the drawer; never `innerHTML`), then the floor in a `<details>`
  closed by default. **Next steps** are a
  card per milestone (the dot, `word`, a "Next" tag where `next`, the sentence, each
  step's chip, statement, text and commands), then `project`, `open`, `rebuild` and
  the text of `next.md`.
- **Live** (`lib/live.js`): the page reads `build.json` once; while it says `live:
  true` and the page is visible it reads it every 2 s, and when `id` moves it fetches
  the three documents again and draws them in place, keeping the scroll, the drawer,
  the zoom and the selection. `ok: false` shows `words.notices.build_failed` and the
  error in a banner; the data stays.
- **What the browser stopped computing** (SPEC 32): a chart draws each current
  point's baked `margin_text` (`lib/chart.js` did, until P2.5 retired it for the
  baked Vega-Lite spec); the technical record sorts verdict rows and anchored
  locators by the baked `order`; the mixed-age banner is `build.json`'s
  `mixed_age`; each row's age and its *aged* mark are `build.json`'s `verdicts`; a
  recorded result's word is its `outcome` (baked beside `passed`, phase 5) and its
  tone its `counts`. `tests/test_site_truth.TheBrowserComputesNoTruth` scans every
  template script for a verdict flag read, arithmetic on a limit, a worst-of over a
  status, a sort over a status and an age compared, with a planted violator of each.
  From issue #15 it also refuses a figure's `currency` or `entry` compared or branched
  on, or a digest compared (`a figure's currency decided`), and
  `test_the_field_mode_never_tints` holds the field mode below.
- **Field mode** (`lib/viewer3d.js`, `createViewer({..., field: true})` or a view of
  kind `field`): the colours on a field are a scale, so each mesh is drawn with
  three.js's `MeshBasicMaterial` and its vertex colours, `toneMapped: false`, no
  lights, and the renderer writes them out unconverted (`LinearSRGBColorSpace`,
  `NoToneMapping`): a COLOR_0 that holds the colour map's sRGB bytes, as ParaView 6.2
  writes it, reads back as those bytes within 2 (`tests/test_field_browser.py`, in a
  headless Chromium when one is there). A mesh with no COLOR_0 is white. Tint,
  highlight, isolate and explode do nothing on a field.
- **Lit field mode** (design P2; `createViewer({..., field: true, light: "scene"})`,
  or a field whose `view.meta.light` is `scene`): a solved field drawn as a scene,
  the way its still was drawn. Each mesh is drawn with `MeshStandardMaterial`
  (`roughness` 0.6, `metalness` 0, `toneMapped: false`, both sides) and its vertex
  colours, under a rig of its own that turns with the camera, as ParaView's light kit
  does: an ambient light of 1.0, a key light of 2.2 above and to the left of the
  reader and a fill of 0.5 from below and to the right (three.js divides each by
  pi). The model mode's three lights are fixed in the world, and from ParaView's
  camera they lit the motorBike from behind, every colour near black (measured), so
  the side the reader sees is always the lit side here, also as the view turns.
  Normals are computed where the file has none (ParaView's glTF holds none). The
  first view is the file's first perspective camera, else the box's diagonal; the
  view turns about the world axis nearest that camera's up (+Z for ParaView's), so a
  drag or a spin never turns the body end over end, and through the point of the
  camera's line of sight nearest the middle of the smallest mesh box that line
  passes through (when it is less than half the size of the whole box; else the
  whole box's), so the motorBike stays in the middle of the view and its 15 m of
  stream tracks do not swing it off. A back face is lit as its front: a file with
  every face turned over draws the same pixels, from the file's camera and from
  behind, and no face is drawn black (`test_field_browser.BothSidesOfTheHeroAreLit`). The output stays
  `LinearSRGBColorSpace` with `NoToneMapping`. Light and shade make a colour darker
  or lighter than the key's, so the viewer keeps each mesh's unlit material, and
  `setFlat(true)` swaps it in: then each pixel is the file's byte within 2, as in the
  field mode. The "Flat colours" toggle (`turn.flat_word`) calls it on the claim
  card, on the engineering hero and on the marketing hero; the technical record shows
  a "Flat colours" button beside Wireframe. `tests/test_field_browser.TheBrowserDrawsALitField`
  holds the flat colours to the bytes, and requires the lit drawing to differ from
  them, each hue to stay the file's and faces at other angles to be drawn at other
  brightnesses; `TheCardTurnsALitField` does the same through the claim card, and
  `ALitFieldTurnsAboutItsUp` holds the turn. A plain field (no `light`, or `none`)
  is the field mode above.

## The marketing page

`marketing/index.html`, drawn by `marketing.js` (SPEC 40): the product first, then the
story behind it and the choices that went into it. It reads `page.json`, `state.json`
for its `views` alone (each alternate's view object, resolved as `page.js` resolves the
representation's: one way of resolving a view) and `build.json` when it is there, and
never `map.json`. It computes nothing `page.js` may not: every status, word, mark,
*ready*, order and filter on it is baked, and what lost to a value is chosen and ordered
in Python. Top to bottom:

1. **The menu** (above).
2. **The hero.** The representation full width, `min(86svh, 820px)` tall and 520 px at
   least, with the copy over it on the left from 980 px: `meta.revision` as the eyebrow,
   `meta.name`, `meta.summary`, `verdict.headline` large, the tally chips as `page.js`
   draws them (`lookOf(t.key)`; a tally item has no marks), a `<details>` whose summary
   is `words.headings.unsettled` and which holds `verdict.sentence`, then a link to
   `marketing/#story` (only when there is a story or an intro) and one to `engineering/`
   labelled `words.headings.evidence`. The headline, the chips,
   `verdict.notice_public` (while `verdict.stale` is true; `words.notices.stale_public`
   for a page.json from before it) and `verdict.limits`
   (small) are one block, `#verdict`; the fold and the links come after it. The
   build-failed banner sits under the menu, above the hero. Narrower than 980 px the
   name and that block come FIRST, then the viewer at 50svh at most, then the summary
   and the fold: the verdict, the stale notice and the limits line are above the fold
   at every width. What slipped through (review of SPEC 40): the stale notice and the
   limits line sat in the fold's block, under the viewer on a phone, so at 390x844
   "Checked does not mean true" started at y=906, off the first screen. A `model3d`
   or `field` representation runs `lib/viewer3d.js` with the customizer (a figure as
   the hero, issue #15: its committed GLB in field mode with only the baked `controls`,
   spin; under it, as under a figure's still, the figure's currency sentence, its chips
   and its legend); an image is
   an `<img>`, a diagram `stage.renderDiagram`; with no
   representation the hero is text only (no box, no notice). The model loads behind a
   spinner, "Loading the model", with a progress bar (`createViewer`'s `onProgress`,
   else the alternate's `bytes`) and its size; the viewer stops drawing while the hero
   is off the screen or the document is hidden. No WebGL or no CDN gives the viewer's
   named error, never a blank box.
3. **The story** (`#story`): `words.headings.story`, then `story` with its notice when
   its stamp is not current, else `intro`, else no section (never the tagline twice).
   A claim run goes to `engineering/#claim=<id>`; a prose link that starts with `#` is
   read against the page's own path (`lib/sections.js`), so it stays on the page.
4. **What it must do** (`#asked`): `words.headings.asked`, a card per `requirements`
   row: the statement (the person's words, record text), its chip (`tokenChip` with
   `marks` and `errored`), under it the `disagree` notice and the amber word when the
   row carries those marks, and one link, `words.headings.evidence`, to
   `engineering/#requirement=<id>`. No claim ids on the card. No rows, no section.
5. **The choices behind it** (`#choices`): `words.headings.decisions`, a card per
   `decisions` row whose `shown` is true: the title, the date, its claims' chips, the
   summary held to four lines by CSS (`line-clamp`) with the whole of it in a
   `<details>` (`words.headings.whole_decision`; a string is never cut in the browser,
   as a cut can split a number), then a closed `<details>` labelled `lost_label` with
   each rejected option: `value`, `type_word` small and muted, `reason`, `returns_word`
   and `returns_text` small; then the decision's `values`. The rows not shown sit in one
   closed `<details>` labelled `decisions_more`, and `losers` in one labelled
   `words.headings.losers`. A part with nothing in it is left out.
6. **Made with nopekit**, the same block as the engineering page (`id="made-with-nopekit"`,
   `img/made-with-nopekit.png`, linked to `https://github.com/neuman/nopekit`), then a
   footer with the built line and links to the engineering page and the technical
   record.

**The floor of the marketing page**: the headline, the tally chips, the limits line and
the requirements' chips are on it, and each claim and its evidence is one link away on
the engineering page. Author notices (`no_intro`, `no_story`, `no_representation`) are
never on it: an absent story or representation leaves its part out. With neither
`story.md` nor `intro.md`, `site build` warns `site/content/story.md is missing: the
marketing page has no story`; with `intro.md` alone it says, as a notice that is no
warning, that the page shows `intro.md` as its story until `story.md` is written. It says no
CLI instruction: `verdict.notice` is the engineering page's, `verdict.notice_public` this
one's (`stale_public`, or `unseen_public` when every Stale claim reads Stale only for
evaluators nopekit cannot see into — issue #15 D1, the same choice as `notice`'s
`invalidated` or `unseen`).

**The customizer** is presentation only (SPEC 29's in-browser what-if stays rejected).
A glass toolbar (`role="toolbar"`) at the bottom of the viewer: Explode (Assembled to
Exploded, only with two movers or more), Spin (on unless the person asks for reduced
motion; a press on the model stops it), Parts (a row per `view.meta.groups` entry,
else per `view.meta.parts` entry, else per explode mover: Show only, Show all and
colour swatches), Views (only when `representation.alternates` holds more than one),
Wireframe, Reset and Full screen (hidden when the browser has no full screen). The
swatches leave out every status hue and the viewer's highlight blue, and a tint never
goes over a highlight. A colour is kept for the person's browser alone
(`localStorage`, per view id, inside try/catch). One sentence under the parts says what
it is: "The colours show on this page only. The checks read the design in the records."
It changes no design value and no baked file, and says nothing of fit or strength.

**Live**: `lib/live.js` `watchBuild`, as `page.js` uses it: when the build `id` moves the
page fetches `page.json` (and `state.json` for the views) again and draws in place,
keeping the viewer while the representation's view id holds. After its first draw it
scrolls to `location.hash`'s element, as it draws after the browser's own scroll.
`document.title` is `meta.name` · Marketing (the engineering page's is `meta.name` ·
Engineering when its shell has the page menu, and `meta.name` alone when it does not).

**What its script may not say** (invariant 14, `TheBrowserComputesNoTruth`, and
`tests/test_vocabulary.py`'s page sources, which list `marketing.js` and the new lib
modules): no `.sort(`, no `.some(`/`.every(` over a key, status, token or outcome, no
read of `.counts`, `.passed` or `.skipped`, no arithmetic beside a name that holds
`limit`, and no literal "ready", "checked", "open", "gap", "stale", "verified", "not
current", "article" or "does not mean true". Its control labels are template text:
Explode, Assembled, Exploded, Spin, Parts, Show only, Show all, Colour, Original,
Views, Wireframe, Reset, Full screen, Close, Loading the model.

## `map.json`

The grounding map (SPEC 29), baked by `page.bake_map` from the live graph
(`graph.build`, over the current design's trace) and the groups. **Order is baked,
geometry is rendered**: no key named `x`, `y`, `cx`, `cy`, `left`, `top`, `width` or
`height` appears anywhere. The renderer lays the nodes out in their lanes by `order`,
`rank`, `row`, `layer` and `column`, never by a force layout, and never reorders by
default. Map schema 2 (architecture phase 3b): requirements and zones are no longer
stops; the trace gives the middle its feeds, its taps and its station layers.

```jsonc
{
  "schema": 2,                                   // page.MAP_SCHEMA
  "generated": "<HUMAN.site.generated>",
  "meta": { "name": "...", "records_digest": "<64>", "resolution_digest": "<64>" },
  "trace": { "key": "fb145d48c5a8",              // 12 hex, or null untraced
             "round": 2,                         // the round that traced it, or null
             "state": "traced",                  // traced | untraced
             "skipped": { "material": "word" },  // choices the trace did not move
             "unmoved": ["E", "material", "yield"],  // values it did not see move
             "ties": 5, "singles": 2,            // values it cannot order
             "amber": "current",                 // current | partial | none
             "notice": "<HUMAN.site.trace.notice_traced>",
             "text": "<HUMAN.site.trace.skipped, or skipped_one; then unmoved, or unmoved_one>" },
  "lanes": ["requirements", "zones", "stations", "boards"],  // empty lanes omitted
  "columns": { "requirements": 0 },              // kept for an older reader
  "layers": [3],                                 // columns per station layer
  "legend": [ { "token": "failing", "n": 1 }, ... ],          // SEVERITY_ORDER, n > 0
  "nodes": [ <node>, ... ],                      // lane order, then order
  "lines": [ { "claim": "C1", "order": 0, "board": "strength", "token": "failing",
               "errored": false, "marks": [],
               "stops": ["station:section", "station:@slenderness", "claim:C1"],
               "stop_kind": { "station:section": "upstream",
                              "station:@slenderness": "read" },
               "via": { "station:section": ["area", "inertia", "section_mod"],
                        "station:@slenderness": ["slenderness"] },
               "taps": [ { "zone": "zone:load", "choices": ["load_n"] } ],
               "fed_by": ["zone:arm", "zone:load"], "serves": ["R1"],
               "values": ["deflection", "load_n", "slenderness"], "reads_all": null,
               "sets": [],                       // an assumption's or a physical claim's stations
               "unseen": false,                  // joins nothing, reads an unmoved value
               "amber": null } ],                // or { state, word, enters }
  "feeds": [ { "zone": "zone:arm", "to": "station:section",
               "choices": ["width", "thickness"], "estimate": [] } ],
  "values": { "load_n": { "role": "choice", "value_text": "15", "units": "",
                          "zone": "zone:load", "station": null, "ground": "assumption",
                          "source": "assumption", "read": true } },
  "evaluators": { "bracket.deflection": { "basis": "sourced", "pack": "", "tier": 0,
                  "registered": true, "outcome": "fail", "outcome_word": "fail",
                  "current": true, "claims": ["C1"] } },
  "edges": { "<type>": { "<source id>": ["<target id>"] } },  // graph edges, MAP_EDGE_TYPES
  "unread": ["bed_xy"],                          // choices no line reads, no feed carries
  "self": "<page.seal>"
}
```

Nodes, each with every key of its kind, `id` `<kind>:<key>`:

```jsonc
{ "id": "requirement:R1", "kind": "requirement", "key": "R1", "lane": "requirements",
  "order": 0, "rank": 0, "title": "<statement>", "token": "failing", "word": "failing",
  "errored": false, "marks": [], "claims": ["C1", "C2", "C3", "C6"] }
{ "id": "zone:arm", "kind": "zone", "key": "arm", "lane": "zones", "order": 0, "rank": 1,
  "title": "Arm shape", "line": "", "generated": false,
  "members": ["arm_length", "width", "thickness"], "count": 3,
  "moved": ["arm_length", "width", "thickness"],
  "ground": { "chosen": 3, "input": 0, "assumption": 0 },
  "sources": { "chosen": 3, "datasheet": 0, "estimate": 0, "assumption": 0 },
  "assumptions": [], "estimate": [], "idle": false,
  "feeds": ["station:section", "station:@slenderness"], "taps": ["C3", "C4"],
  "claims": ["C1", "C2", "C3", "C4"] }
{ "id": "station:section", "kind": "station", "key": "section", "lane": "stations",
  "order": 1, "layer": 0, "column": 1, "rank": 1, "depth": 0, "title": "Arm section",
  "line": "...", "generated": false, "members": ["area", "inertia", "section_mod"],
  "head": "area", "value_text": "210 mm^2", "fed_by": ["zone:arm"],
  "claims": ["C1", "C2", "C4"], "stop_kinds": { "C1": "upstream", "C2": "upstream" } }
{ "id": "board:strength", "kind": "board", "key": "strength", "lane": "boards",
  "order": 0, "title": "Holds the pump", "line": "...", "generated": false,
  "token": "failing", "word": "failing", "errored": false, "marks": [],
  "claims": ["C1", "C2", "C3", "C6"],
  "tally": [ { "key": "failing", "label": "1 failing", "n": 1, "status": "fail",
               "errored": 0 } ] }                // report.count_bits
{ "id": "claim:C1", "kind": "claim", "key": "C1", "lane": "boards", "board": "strength",
  "order": 0, "row": 0, "severity": 0, "title": "<statement>",
  "claim_kind": "measurable", "critical": true, "token": "failing", "word": "failing",
  "errored": false, "marks": [],
  "value_text": "0.6997 mm against a limit of 0.5 mm, 40% past the limit",
  "amber": null }                                // or { "state", "word", "enters" }
```

- A claim's `token` is `graph.tokens`' (`claims.STATUS_KEY` of its composition,
  `errored` for a crash); a requirement's is `graph.tokens`'; a board's is
  `claims.worst_of` over its claims (Gap with none). `word` is `report.words`' for the
  token. `marks` are shown beside the status and never change it: `disagree` (the
  claim's status and its evidence disagree) and `amber` (architecture phase 3b: a
  Checked pass that a ±10% move of an unconfirmed assumption's choice flips). A board
  or a requirement carries a mark when a member does. A claim's `amber` is its live
  amber state (`untraced`, `not-current`, `flips`, `unclear` or `holds`), its word,
  and `enters`: the first stop of its line holding a value a flipping choice moves
  (null: the whole line). `page.status_problems` names a mark or a state that is not
  the live one, and a feed whose `estimate` is not.
- `order`, `row` (within the board), `rank`, `layer`, `column` and `severity` (the
  token's index in `SEVERITY_ORDER`, for the optional "failing first" toggle) come from
  structure only. A status flip moves none of them.
- **The layout** (`page.layout`): boards by `(order, id)`, rows in each board's
  `members` order, a claim on a second board drawn on the first, then the generated
  board `other` (every claim on no board, natural order, a removed claim's fail
  included), titled "Other" beside board records and "Claims" with none. Every zone
  record by `(order, id)`, then the generated zone `other` of the choices no zone holds
  that a line reads, that feed a station or that an assumption sources, in `Config`
  order ("Other choices", or "Choices"); no zones with no model. `count` is its
  members, `moved` those the trace moved, `sources` its members by source (`page.SOURCES`:
  an assumption claim sources it; an input of a published or measured kind; any other
  input; a loser its records name (`chosen`); none of these, `unrecorded` —
  `page.SOURCE_OF_KIND`), `estimate` the unconfirmed assumptions sourcing its members,
  `idle` true when it feeds no station, taps no line, and the trace measured every
  member (moved both ways, no raise), and `unmeasured` the members it did not measure
  when it feeds and taps nothing (every member, untraced): such a box is not faded and
  says `words.trace.zone_unmeasured` (review of phase 3b: an untraced map called six
  zones the claims ride unused).
  Stations: the records, then `station:@<name>` for each derived value no record holds
  that the settling evaluators of two or more claims read by a named path (a whole
  read never makes one; past `page.IMPLICIT_STATIONS_WARN` the build warns). `head` is
  the member most lines read that is a finite number (else the most read), and its
  `value_text` carries units — its param's, or those a served verdict whose evaluator
  read it reported it in as its measured value or limit — or is empty: a station never
  shows a bare number. A value's `units` follow the same rule.
- **The trace's structure.** A feed joins zone Z to station S when a choice of Z moves
  a member of S directly (`graph.direct_derives`: no value the trace puts before it is
  moved by the same choice). A line's `values` are what its settling evaluators read
  by a named path, direct; it stops at the stations holding them (`read`) and at those
  holding a value the trace puts before them (`upstream`, `graph.upstream`) and that
  sit before its last read stop, ordered by layer, column and station order, then at
  its claim. An assumption's `sets` are the stations its sourced choices move directly,
  and a physical claim's the stations of the assumptions it `resolves`; `unseen` is
  true for a line that joins nothing and reads a value in `trace.unmoved`. Its `taps` are the zones of
  the choices it reads directly, and of the choices that move a derived value it reads
  that no station holds; `fed_by` the zones of its taps and of the feeds into its
  stops; `serves` its requirements. A station's `depth` is the upper median of the
  `graph.depths` of the members its lines read (of every moved member when none is
  read; null when the trace moved none), its `layer` the dense rank of the depths (a
  null depth last), its `column` the interval packing within its layer. `rank` is one
  barycentre pass against the claims' rows: zones by the mean row of what they feed and
  tap, the stations of a layer by the mean row of their lines, requirements by their
  claims' — ties in record order. **Untraced**, the layer is phase 5's column, every
  `column` 0, no stop is `upstream`, there are no feeds, and `trace.notice` says the
  links are not measured yet.

## `page.json`

The outline's data (SPEC 28, 30), baked by `page.bake_page` and `page.bake_prose`.

```jsonc
{
  "schema": 1, "outline": 1, "generated": "<HUMAN.site.generated>",
  "meta": { "name": "...", "summary": "...", "revision": "v0.1",
            "records_digest": "<64>", "resolution_digest": "<64>",
            "content_digest": "<64>",
            "views": { "<view id>": "<64: site.view_digest of its state.json row and
                                     view file>" } },
  "representation": { "view": "assembly", "kind": "model3d", "title": "Assembly",
                      "notice": "",
                      "alternates": [ { "view": "assembly", "title": "Assembly",
                                        "bytes": 6155468 } ],
                      "figure": null, "controls": null },   // issue #15: a figure hero
                      // bakes its figure object and "controls": ["spin"]
  "verdict": { "token": "not-ready", "ready": false,
               "headline": "v0.1 is NOT ready.",
               "sentence": "<the readiness sentence, register='site'>",
               "limits": "<the limits line, register='site'>",
               "tally": [ { "key": "checked", "label": "3 checked", "n": 3,
                            "status": "pass", "errored": 0 } ],   // report.count_bits
               "stale": false, "notice": "", "notice_public": "" },
  "intro": <prose> | null,
  "story": <prose> | null,                     // site/content/story.md (SPEC 40)
  "description": { "summary": "...", "notice": "" },
  "sections": [ { "board": "strength", "title": "...", "line": "...",
                  "generated": false, "token": "failing", "word": "failing",
                  "errored": false, "marks": [], "tally": [...],
                  "claims": ["C1", "C2", "C3", "C6"], "prose": <prose> | null } ],
  "claims": { "C1": <floor row> },
  "next": { "milestones": [ <step group> ], "project": <step group> | null,
            "open": [ <step> ], "rebuild": [ { "article": "<12 hex>", "claims": ["C5"],
            "text": "..." } ], "prose": <prose> | null },
  "requirements": [ { "id": "R1", "statement": "...", "token": "failing",
                      "word": "failing", "errored": false, "marks": [],
                      "claims": ["C1", "C2", "C3", "C6"] } ],
  "decisions": [ <decision row> ], "decisions_more": "",
  "losers": [ <value row> ],
  "words": { "tokens": { "failing": { "word", "term", "hint" } }, "lanes": {...},
             "kinds": {...}, "by": {...}, "basis": {...}, "outcome": {...},
             "headings": {...}, "notices": {...}, "ready": {...},
             "trace": { "notice_traced", "notice_traced_no_round", "notice_untraced",
                        "running", "skipped", "skipped_one", "caption", "amber_legend",
                        "amber_flips", "amber_group", "amber_holds", "amber_unclear",
                        "amber_not_current", "amber_untraced", "amber_item", "rides",
                        "fed_by", "upstream_stop", "upstream_rides", "unseen_line",
                        "sets", "settles", "unmoved", "unmoved_one", "zones_summary",
                        "zone_idle", "zone_unmeasured", "feeds_key",
                        "sources": { "chosen", "datasheet", "estimate", "assumption",
                        "unrecorded" } } },
  "problems": [ "site/content/foo.md names no board: ..." ],
  "self": "<page.seal>"
}
```

A floor row (`claims[id]`, one home for the floor and the drawer):

```jsonc
{ "id": "C1", "statement": "...", "claim_kind": "measurable", "kind_word": "Automated",
  "critical": true, "board": "strength", "requirements": ["R1"],
  "token": "failing", "word": "failing", "errored": false, "marks": [],
  "hint": "...", "cause": "failed", "cause_text": "...",
  "reason": "bracket.deflection: tip deflection 0.6997 mm ...",
  "mechanism": {"by": "closed_form", "by_word": "...", "how": "...",
                 "evaluators": ["bracket.deflection"] },
  "acceptance": "tip deflection <= 0.5 mm", "limit_text": "0.5 mm",
  "values": [ { "gate": "bracket.deflection", "measured": 0.6997, "units": "mm",
                "limit": 0.5, "limit_hi": null, "comparator": "<=", "margin": -0.3994,
                "compared": true, "binding": true,
                "value_text": "0.6997 mm against a limit of 0.5 mm, 40% past the limit",
                "margin_text": "40% past the limit" } ],
  "evaluators": [ { "id": "bracket.deflection", "basis": "sourced", "basis_word": "...",
                    "outcome": "fail", "outcome_word": "fail", "order": 0,
                    "current": true, "stale_reason": "", "detail": "...",
                    "evidence": [ { "name": "deflection.json",
                                    "source": "<out>/deflection.json",
                                    "href": "assets/evidence/…/deflection.json",
                                    "missing": "" } ] } ],
  "physical": null,   // a recorded result: { "counts": true, "token": "fail",
                      //   "word": "fail", "why": "", "recorded": "Recorded by …",
                      //   "heading": "Test on a built part" } — the site's words
  "rests_on": ["C6"], "resolves": [], "resolved_by": [],
  "settle": { "text": "...", "commands": ["nopekit why C1", "nopekit check"] },
  "amber": null,   // architecture phase 3b, a Checked claim resting on an unconfirmed
                   // assumption: { "state": "flips", "word": "...", "items": [
                   //   { "assumption": "C6", "choice": "load_n", "sign": "+",
                   //     "evaluator": "bracket.bearing", "text": "..." } ] }
  "figures": [ <figure> ],   // issue #15: one per bound figure whose gate covers it
  "binding_text": "The pictures of cfd.pressure_drop come from its result ... " }
```

A figure (issue #15, `page.figure_row`): a bound viewgen's picture, chart or 3D file,
as the disk resolved it (`page.figure_for`), under each claim its gate covers:

```jsonc
{ "id": "cfd.pressure_drop/9c1e04d2b7a35f60-3a7b19e2/cfd-openfoam.speed",
  "view": "cfd-openfoam.speed", "kind": "image", "gate": "cfd.pressure_drop",
  "entry": "9c1e04d2b7a35f60-3a7b19e2",      // the entry it was drawn from ("" for none)
  "currency": "current",                     // current | stale | unseen | older | none
  "currency_word": "This picture is from the result on this page.",
  "drawn_text": "",                          // older: "That other result: pass, 2 Pa."
  "outcome": "pass", "outcome_word": "pass", // the evaluator row's; older: its own entry's
  "claims": [ { "id": "C1", "token": "checked", "word": "checked", "errored": false,
                "marks": [] } ],             // every claim the gate covers
  "title": "...", "caption": "...", "alt": "...", "provenance": "...", "tool_word": "",
  "src": "assets/figures/<gate>/<entry>/<view>.<sha12>.png", "sha256": "<64>",
  "bytes": 10800, "field_src": "", "data_src": "",
  "marks": [ { "at": 0.25, "text": "upstream plane" } ],
  "scale": { ... },                          // the sidecar's, a pack constant
  "scales": [ { "on": "surface", "on_word": "On the surface", ... } ],
                                             // only where the sidecar states scales: each
                                             //   a scale, with the words of its glyph
  "light": "scene",                          // only where the sidecar states a light
  "chart": {},                               // a chart: { "limit": { "value", "comparator",
                                             //   "units", "label" } | null, "current":
                                             //   { "x", "y", "margin_text" } | null,
                                             //   "spec": <the whole Vega-Lite spec> }
  "turn": {} }                               // a 3D file: { "word", "live_word" }, and
                                             //   "flat_word" where `light` is "scene"
```

A step group (one per milestone, natural order; `project` with no milestone, over the
required claims) and a step:

```jsonc
{ "id": "print-v1", "description": "...", "token": "not-ready", "ready": false,
  "word": "not ready", "next": true, "sentence": "...",
  "steps": [ { "claim": "C1", "token": "failing", "word": "failing", "errored": false,
               "marks": [], "statement": "...", "how": "...", "text": "...",
               "commands": ["nopekit why C1", "nopekit check"] } ],
  "missing": [ { "claim": "C9", "text": "..." } ] }
```

A decision row (the marketing page's choices, SPEC 40) and a value row (what lost to a
value; a decision's `values` and the top-level `losers`):

```jsonc
{ "id": "print-in-petg", "title": "Print in PETG", "when": "2026-10-15T00:20:42Z",
  "summary": "...", "featured": true, "shown": true,
  "claims": [ { "claim": "C2", "token": "checked", "word": "checked",
                "errored": false, "marks": [] } ],
  "params": ["material"],
  "rejected": [ { "value": "a machined 6061 aluminium bracket", "reason": "...",
                  "type": "preferred_against",
                  "type_word": "Preferred against, not recorded by Robin Okafor",
                  "preference": "not-recorded", "returns": "does-not-hold",
                  "returns_word": "The return condition does not hold: ...",
                  "returns_text": "C3 margin < 0.1", "input": null } ],
  "lost_label": "3 options lost",
  "values": [ <value row> ] }

{ "name": "thickness", "value_text": "7 mm", "rationale": "...",
  "lost": [ { "value_text": "4.0 mm", "reason": "..." } ] }
```

- `values`: one row for each covering verdict with a finite value. `binding` is the
  value the composition reports (`claims.claim_value`, or a non-Checked claim's own
  verdict when compared). `margin` is `claims.margin`'s, at six significant figures,
  beside the verdict's own `limit` and `comparator`. `value_text` puts a compared
  value beside the CLAIM's limit (S-35) with the margin against that limit
  (`claims._margin_of` over the claim's acceptance condition, as `cross_check` judges
  it), and a value of another quantity beside its own limit and its own margin; the
  percentage is whole at 1% or more. So a Failing claim never reads "inside the
  limit": `page.status_problems` names a compared value whose words disagree with
  `cross_check` (review of phase 5: C3 tightened to 0.1 MPa read "99% inside the
  limit", the evaluator's margin against its own 15 MPa).
- `steps` are `claims.unresolved`'s unresolved claims in severity order; `ready` is
  its `ready`; `next: true` marks the first milestone, in natural order, that is not
  ready — the browser never picks it. `open` holds the unresolved claims no milestone
  requires (with no milestone, the claims that are not required). `settle` comes
  from `page.settle`, one entry per `ClaimCause`; every command parses, and a pass
  is shown as `claim physical` takes it (`--evidence <file>`, and a judgment's
  `--authority <name>`). A test on a built part with no milestone has no `export`
  command: its text says how to build the part and to name the claim in a milestone
  file. A claim skipped behind a prerequisite names its root from the spine's mark
  (`Verdict.blocked_by`, `blocked_kind`) and the claim that root covers: "C7 fails
  first: ... Change the design until C7 passes." with `nopekit why C7`, a crashed
  root's `nopekit gate show <root>`, never the missing tool's `nopekit doctor` (P8:
  C1, skipped because C7's mesh ladder failed, read "Install it"). A step carries its
  claim's `marks`, as the floor row does.
- A floor row's `reason` (`page.card_reason`) is the evaluator's own words for a
  claim that fails, crashed or skipped: `<gate>: <detail>` as `check` prints it, a
  crash's first line, a skip's reason; for a skip behind a prerequisite, the root
  and the claims it covers (`words.figure.reason_prerequisite`, `_named`); `""`
  otherwise. The card and the drawer print it under `cause_text` (P8: a failing
  card with no value said only "An evaluator failed the design").
- `figures` (issue #15): the words depend on the records, the cache and the committed
  figure files alone, never on this computer's tools, so a clone bakes the same object.
  A chart's `limit` and `current` are the floor row's for (claim, gate), the claim's
  limit where the row is compared with it, set by the spine (`page.chart_block`); a
  chart whose y units are not the limit's bakes no limit, and the build says so. Its
  `spec` (P1.5, `page.chart_spec`) is the whole Vega-Lite spec that draws it: the series
  of its chart file as data rows (`series`, `label`, `x`, `y`, and `text` and `hollow`
  where the payload gives them), the limit as a shaded side and a dashed rule with its
  label, the judged point with its margin words, and the payload's `band` (error bars),
  `rule` (a tolerance line, never called a limit) and `estimates` (diamonds), on a log y
  axis where the payload's `y.scale` is `"log"`. Every number in it is the chart file's
  or the floor's, and it holds none of `page.VEGA_FORBIDDEN_KEYS` (`transform`,
  `calculate`, `filter`, `aggregate`, `params`, `expr`, `signal`, `bin`, `timeUnit`,
  `impute`, `regression`, `loess`, `density`, `fold`, `stack`, `test`), so Vega places
  numbers and computes only the axes. Its marks name a `style` (`nk-limit`, `nk-past`,
  `nk-current`, ...) whose look the page's theme gives; where a label sits is in the
  spec. A live
  3D file is never named here (`build.json`'s `live_figures`). The representation is a
  `model3d` view, else a figure whose 3D file is committed (as `field`), else the first
  bound still, else an `image` or `diagram` view. `page.status_problems` holds each
  figure (G1-G8 of the design): its claims exactly the gate's cover and each chip the
  resolution's; its entry and currency the disk's and the served entry's; its
  sentences and outcome; its files under its own folder, the build's keep set or the
  bytes its sidecar names; a chart's limit, point and spec (`chart_block`'s whole, with
  no forbidden key); a caption with no status word;
  and the representation's figure the claim's own copy.
- **The checker** (`page.status_problems`, invariant 14) holds every token, *ready*,
  word and mark above to the resolution: a claim's, a section's and a step's `word` is
  its token's; a milestone's and the project's `ready`, `token` and `word` are
  `claims.unresolved`'s; each prose piece's `stamp` and `notice` are what the digest
  of its claims says now. `site status`, `status` and `doctor` also hand it each
  file's `as_of` and the number pool (`site.baked_pool`), so a resealed page cannot
  show behind text as current or an unmatched number as matched.
- `words` holds every word the page labels with; `problems` the prose files that name
  no board. From SPEC 40 `words.headings` holds the marketing page's headings too
  (`story`, `asked`, `decisions`, `losers`, `evidence`, `unsettled`,
  `whole_decision`), and `words.notices` holds `stale_public` ("Some results are older
  than the design.") and `no_story`: the page prints no heading of its own. From issue
  #15 `words.figure` holds `report.HUMAN["site"]["figure"]` whole: each currency's
  sentence (`current`, `stale`, `older`, `none`), `drawn`, `drawn_bare`, `shown`,
  `other`, `limit_label`, `turn`, `live` and `tool_unchecked` (the bake's), `reason`,
  `reason_prerequisite` and `reason_prerequisite_named` (`page.card_reason`'s), and the
  claim card's own words: `judged` ("Value from {gate}"), `others`, `pictures` (the
  drawer's line, "Pictures: {n}. Show them."), `table` (where the chart library did
  not load, above the chart's baked `table`), `table_more` and `table_band` (the words
  `page.chart_table` bakes into that table: the rows of a long series left out, and a
  band's two ends; review of P2), `chart_empty` (a chart
  view with no series, on the technical record, from P2.5) and `turn_failed` (where
  the 3D view did not load). The card prints no error text of its own.

**The marketing page's keys** (SPEC 40), all baked by `page.bake_page` or a helper it
calls, so `status_problems` and the style scans see them:

- `verdict.headline` is the readiness sentence's first sentence, from the builder that
  writes `verdict.sentence` ("v0.1 is NOT ready." or "v0.1 is ready, as last
  evaluated."): a word, never a second judgment.
- `representation.alternates`: the `model3d` and `field` views in the baked views'
  order, the representation's own first, one per asset (two views whose `src` files
  hold the same bytes are one; the first stays). `bytes` is the asset's size on disk
  or null; it is structure, kept out of the number pool. The browser resolves each
  `view` in `state.json` `views`.
- `requirements`: one row per requirement in the map's order (`view.requirements`,
  never `rank`), made by the one helper that makes the map's requirement node: the
  node's key is `id`, its title `statement`, and `token`, `word`, `errored`, `marks`
  and `claims` are the node's own.
- `decisions`: every decision record, newest first by `when` (ties by id).
  `featured` is true when it changed a claim or a value (`claims_changed` or
  `params_changed`); `shown` when it is featured and among the first five featured.
  `decisions_more` labels the rest ("9 more decisions"; "" when none). `claims` is
  each changed claim's chip, copied from `claims[id]`; `params` the changed names.
  `rejected` is `claims.rejection_rows` (the one producer), one row per rejected
  alternative in the record's order: `value`; `reason`, the record's `why` (record
  text: a `why` key is a word key and is scanned for the register); `type` and
  `preference` (`recorded`, `not-recorded` or null) as tokens; `type_word`, the site's
  phrase (`report.rejection_words` with the site register): for a preference exactly
  "Preferred against, not recorded by <name>" until the person records it with
  `nopekit prefer`, then "Preferred against, recorded by <name> (<when>)" (invariant
  13: the CLI's phrase with a capital), "Not possible", "Another option is better on
  every count", "Not evaluated", or "" for an untyped one; `returns`, the return
  condition's `claims.RETURN_STATES` token; `returns_word`, its state in the site's
  words, with no record text in it (a condition that cannot be evaluated because of a
  claim's status names the claim and the status word); `returns_text`, the condition
  as written; `input`, the input it cites or null. `lost_label` is "1 option lost",
  "3 options lost" or "": the browser does no plural. `values` holds the value rows
  of the decision's `params_changed`.
- `losers`: a value row for each value with at least one loser that no decision's
  `values` holds, in `state.json` `params` order. `value_text` is the value and its
  units as text ("" for a value that is no number or word), and so is each loser's:
  a losing value is text, so it never joins the number pool and story prose cannot
  quote it as a result. `reason` is the loser's `why`.
- **The checker** holds these too: a requirement row's `token`, `word` and `marks`
  are the resolution's (and the map node's, whose `disagree` mark it now checks);
  a decision's claim chips are the claims' own; each rejection's `type_word`,
  `returns_word`, `preference` and `returns` are what `claims.rejection_rows` and the
  site's words give for that decision and index now; `headline` is required, is
  `report.site_headline`'s and is the start of `sentence`; `tally` is
  `report.count_bits`' and `limits` is `report.limits_line(register="site")`; and a
  requirement row's, a section's, a decision chip's and a step's `amber` mark sits
  there exactly when one of its claims flips now. What slipped through (review of
  SPEC 40): the headline was held only when baked, and the tally, the limits line and
  those `amber` marks not at all, so a resealed page with no headline and a sentence
  that said *ready*, a one-chip tally, an empty limits line or a plain green chip over
  a flipping pass read current. Both callers (`site build` and `cli._site_state`) hand it what it needs.

## Groups: boards, zones, stations

`groups/<id>.json` (SPEC 17), one record per file, read strictly:

```jsonc
{ "kind": "board", "title": "Holds the pump",
  "line": "The arm carries the pump with no crack and no sag that a person can see.",
  "members": ["C1", "C2", "C3", "C6"], "order": 1 }
```

A board's members are claim ids of any kind, a zone's choice names, a station's
derived-value names, in the order the page draws them; `order` sorts groups of one
kind (then the id). The id is the file's stem — lower case, the milestone pattern —
and names `site/content/<id>.md`; `intro`, `next`, `story` and `other` are the page's
(`store.RESERVED_GROUP_IDS`). A board
member no claim holds, a claim on two boards, a name in two zones or on two stations,
or in a zone and on a station, is an index problem and a doctor FAIL; whether a zone
member is a choice or a station member a derived value needs the model, so doctor
(FAIL) and `site build` (a warning) judge it. A group holds no status, and no
position: a board's status is derived on every build, and the page lays out the map.

## Prose and the stamp

The agent's text is `site/content/<board>.md` (the board's section), `intro.md` (the
description), `next.md` (after the generated next steps) and `story.md` (the marketing
page's story, SPEC 40), with figures under `site/content/img/`. The subjects that are
no board are `page.PROSE_SUBJECTS`: `intro`, `next`, `story`. A story opens with what
the product is and who it is for, tells the choices that shaped it and what lost, and
ends with the state of the design in the verdict's words; it is the text the site
persuades with most, so its stamp and its number scan matter most there. Front matter is optional and exactly a `---` line, `key: value`
lines, a `---` line; only `as_of` is read.

**The stamp.** `nopekit site stamp <board>` (or `intro`, `next`, `story`, `--all`) writes
`as_of`: the first 16 hex of sha256 over `nopekit-stamp-v1`, the subject and its
tokens — a board's `{claim id: token[:marks]}`, with `{"@<id>": token[:marks]}` for
each claim on another board that its text names (`page.named_in`: a claim id the text
links), and for intro, next and story every claim's plus `_readiness` and
`_m:<milestone>` (a story names the whole product, as an intro does).
It writes only that line. (Review of phase 5: a board's stamp held its own claims
only, so "C1 fails" in prints.md read current after C1 passed.) The build bakes each
piece's `stamp`: `current` while `as_of` equals the digest now, `stale` when it
differs, `unstamped` with none; the page shows "This text was written before the
latest results" (or that it has no stamp) above any piece that is not current. A
regroup moves the keys, so it re-flags the text. A stamp guards age, never
correctness.

**The markdown subset**, parsed at build time into typed blocks and runs (the browser
receives no HTML from prose): `#` and `##` headings (as `h3`), `###` (`h4`),
paragraphs, one-level `-`/`*` and `1.` lists, `>` quotes, a paragraph that is only an
image (a figure, its path under `site/`), `**bold**`, `*italic*`, `` `code` `` and
links (`https:`, `http:`, a site path, `#anchor`). A claim id that exists becomes a
link to the drawer. Raw HTML, tables and fenced code stay literal text, each with a
problem. A figure whose file is not under `site/` reads `"missing": true`. A figure
under `site/assets/` reads `"missing": true` also, whether its file exists or not, and
the build says to put figures in `site/content/img/`: the build deletes the files in
`assets/` that no view uses, so such a file would be gone after the build that linked
it (issue #15).

```jsonc
{ "file": "content/strength.md", "subject": "strength", "as_of": "3f2a9c1e0b7d4a55",
  "current_as_of": "3f2a9c1e0b7d4a55", "stamp": "current", "notice": "",
  "blocks": [ { "type": "h3", "runs": [ { "t": "Sag at the tip" } ] },
              { "type": "p", "runs": [ { "t": "C1", "claim": "C1" },
                  { "t": " fails by " },
                  { "num": "40", "unit": "%", "match": true, "t": "40%" } ] },
              { "type": "ul", "items": [ [ { "t": "..." } ] ] },
              { "type": "figure", "src": "content/img/arm.png", "alt": "...",
                "caption": [ ... ], "missing": false } ],
  "unmatched": [], "register": [], "problems": [] }
```

**The number scan.** Every number in the prose must match a number the build bakes, at
the prose's precision: within half a unit of its last written decimal (`1e3` is
written to the thousand), sign-insensitive (prose says "40% past the limit" over a
margin of −0.3994), thousands separators removed. The pool (`prose.number_pool`) is
every finite numeric leaf of `state.json`, the view files, `map.json`, `page.json`
without its prose and the JSON files under `site/assets/` that the build KEEPS (an
asset the cleaner removes vouches for nothing), less keys that are structure
(`prose.POOL_EXCLUDED_KEYS`: `schema`, `outline`, `order`, `column`, `columns`,
`severity`, `rank`, `row`, `index`, `tier`). Each value is tagged: a count under
`prose.POOL_COUNT_KEYS` (`n`, `tally`, `ground`, the readiness counts), a fraction
under `margin`, else the `units` of the object that holds it, or no stated units. A
number with no unit matches any value; one with a unit matches a value in that unit
or with no stated units, never a count or a fraction; a `%` matches a fraction times
100, a value with no stated units as written or times 100, or a value in `%`. A
number joined to letters is read with those letters as its unit (`12L/h`, `40Nm`),
unless they are an identifier suffix (`prose.IDENTIFIER_SUFFIXES`: `3D`, `1st`); a
letter before the digits (`M3`, `C1`, `v0.1`) keeps it from being read; a word after
a space is its unit only when `prose.UNIT_WORDS` holds it (or its first part, as in
`USD/unit`); `.9` is read. Code spans, link targets, figure paths and front matter
are never scanned. An unmatched number is a `num` run with `match: false`, which the
page marks with a dagger and explains in words under the text
(`notices.unmatched_note`), and a build warning. Its limits: it is blind to number
words, to unit conversions and to code spans; a value whose units no key states
matches a number in any unit.

## Evidence on the page

`page.evidence_plan` walks every verdict whose resolver row stands on a cache entry.
`<out>/rest` resolves under `.nopekit/out/` and must stay there, links followed; a
bare relative path resolves under the root and must stay inside it, with no hidden
part (`.env`, `.git/`) and not ignored by git (`vcs.ignored`, asked by `site build`);
`<out:controls>/`, `<tmp>/`, `~/`, `<pack:…>/`, absolute paths and any `..` part are
never published. A regular file of at most 2 MiB (`page.EVIDENCE_MAX_BYTES`) is
copied to `assets/evidence/<gate>/<entry name>/<file>` only when its sha256 is the one
the entry's evidence manifest names (`verdicts.evidence_manifest`, below), and only
when the bytes differ; `site build` hashes the file again just before it copies.
Names that clash in one entry, ignoring case and after unsafe characters become `_`,
gain `-2`, `-3`. A file with other bytes than the manifest's, or with no manifest,
is another run's (or a run nothing recorded): the build keeps the entry-keyed copy
when one exists, plans none when not, and says why in its warnings ("the file on this
computer is from another run: `nopekit check --force --only <gate>` ..."). When the
source is gone but the entry-keyed copy exists (a fresh clone: `.nopekit/out/` is
ignored) the copy is kept and linked. A copy already on the site is kept and never
checked again: the build binds what it copies, not what it keeps. Anything else reads
`missing` with words that
depend on the path alone: outside the project, or not on the page
(`HUMAN["site"]["evidence"]`); why a file in the project was not copied (its size, a
directory, gone, hidden, ignored, a link out) reads this machine's disk, so it is a
build warning and never baked, and a fresh clone bakes the same bytes. `state.json`'s
verdict rows carry `evidence_assets`, aligned with `evidence`.

**The evidence manifest** (issue #15). When `check` records a run whose outcome is
measured (pass or fail), it writes `.nopekit/cache/evidence/<gate>/<entry>.json`
beside the entry it files: `{"schema": 1, "gate", "entry", "files": {<portable
evidence path>: {"sha256", "bytes"}}}`, for each regular file the verdict cites under
an anchor. It is scratch (git ignores `.nopekit/cache/`), so a fresh clone has none. A
cache hit, `--no-record`, an error, a skip, a control, a mutation and a trace run write
none; `export`'s re-runs do. What slipped through before it: the copy was bound by
name, so a design that went A, B, A (the last a cache hit, with B's files still in
`.nopekit/out/`) published B's files under A's entry.

## Figures bound to verdicts

Issue #15. A **figure** is a still (a PNG), a chart or a 3D field (a GLB, or a glTF
with its buffers inside it) that a pack
draws from the evidence of one verdict entry: the pressure field of the CFD run that
judged a pressure drop, beside the claim it judged. A picture is persuasive, so it is
held as the numbers are (invariant 14): it says which result it is, it sits beside the
status of every claim its gate covers, and it never says more than the evidence of
that result.

**What the spine holds.**

1. A figure is the evidence of one named verdict entry, bound by content. The run's
   files are bound by the entry's evidence manifest ("Evidence on the page"); the
   inputs a picture reads beyond them (a solved field) by the digests the verified
   evidence lists (`ctx.bind`); the picture by its entry-keyed folder and a tracked
   sidecar that holds its sha256.
2. The spine says whether a figure is current, stale, another result's or absent. The
   browser never decides it: the page bakes the word, the drawn result's outcome and
   the chips of every claim the gate covers.
3. Figures are drawn at `site build` only, never in a gate or in `check`. A renderer
   that is missing, crashes, hangs or is refused moves no status and deletes no
   committed figure: views never reach the resolution.
4. A colour range is a pack constant times a named evidence value, never a claim's
   limit. One `scale` object gives the pixels and the HTML legend; a picture with
   several ramps (design P2: the pressure on a body, the speed on its stream lines)
   states a `scales` list instead, each entry such an object with `on` set to
   `surface` or `lines`, and each held to the same rule.
5. A chart draws only numbers its entry's verified evidence holds. Its limit and its
   judged point are the floor row's, set by the spine, and only on a chart of the
   served entry: an `older` chart draws its own run's series with no limit and no
   judged point (review of P2: the served value was drawn as a line across the other
   run's series, with the served margin words).
6. No figure file is over 2 MiB (`page.EVIDENCE_MAX_BYTES`).

**The viewgen** declares the gate whose served entry it draws, `evidence_of`, and the
files besides its module whose bytes key its memo, `recipe` (`docs/PACK_FORMAT.md`,
"Figures bound to a verdict"). It is handed a `BoundContext`: no model, no parameters
and no claim's acceptance, because the design now may be another design than the
served entry's, and a limit handed to a viewgen is how a colour range gets keyed to a
limit. `ctx.entry.files` holds only the evidence files whose bytes the manifest names;
`ctx.entry.withheld` says why each other path is held back. It writes into scratch git
ignores (`.nopekit/out/figures/<gate>/<entry>/<view>/`), never into `site/`.

**When it draws.** `site build` resolves, plans the evidence, runs the unbound
viewgens, then the bound pass, in registry order:

| The served entry of its gate | What happens |
|---|---|
| none recorded | nothing is drawn; the figure reads `none` |
| a valid sidecar under it at the current recipe digest | the memo: the viewgen is not called |
| a valid sidecar at an older recipe, and the draw is unavailable, fails or is refused | kept, and the build says it was drawn by an older scene |
| otherwise, the viewgen available | called with a `BoundContext`, checked, then published |
| otherwise | nothing is drawn; the page shows what the disk holds |

The recipe digest is the sha256 of the module's bytes, each `recipe` file's bytes and
`site.FIGURE_RECIPE_SALT`. The tool's identity is not in it: with it, one teammate's
install would draw every figure again. A rebuild that judged nothing new reads one
sidecar per bound view and calls no tool. `site serve --watch` also polls
`.nopekit/cache/evidence`, so a `check --force` that solves the same entry again draws
it.

**What the spine refuses** (`site._figure_refusal`; a build warning, nothing published):

| Check | Refusal |
|---|---|
| each output at most 2 MiB (`write_asset` raises over it) | over the 2 MiB cap |
| a still starts with the PNG signature and its IHDR parses; a `.glb` starts with `glTF`; a `.gltf` is JSON with `asset.version` `2.0` and every buffer and image a `data:` URI (`site._solid_problem`) | not a PNG, not a GLB, not a glTF 2.0, names a file outside it |
| one PNG and one 3D file at most, nothing else; a still is never written with `write_live` | a bound figure writes at most one PNG and one 3D file |
| a picture carries no payload, and its `src` names a file it wrote | its numbers are its evidence's; its src names no file it wrote |
| a chart names no file and holds none of `site.CHART_FORBIDDEN_KEYS` (`limit`, `limits`, `current`, `marker`, `now`, `markers`) | a chart's limit and its point are the claim's |
| every number under `series`, `band`, `estimates` and `rule` is a JSON leaf or a CSV cell of a verified evidence file | the chart holds a number no evidence file holds |
| every bound input and verified evidence file hashes the same after the call | the inputs moved while the picture was drawn |
| `scale.lo` and `scale.hi`, and each `scales[].lo` and `.hi`, equal no `limit` or `limit_hi` of a claim the gate covers, nor the entry's verdict limit (relative 1e-9); no `source[].key` of any of them holds `limit` | a colour range taken from a limit |
| `scale` is an object; `scales` is a list of objects, each `on` `surface` or `lines`, no two on the same, never beside a `scale`; `light` is `scene` or `none` (`page.scales_problem`) | its colour scale is not an object; a colour scale and a list of scales; is on …; two colour scales on …; its light … |
| the caption holds no status word (`page.CAPTION_STATUS_PHRASES`) | a caption may not say a status |

**Where it lands** (`site._publish_figure`):

```
site/assets/figures/<gate>/<entry>/<view>.<sha12>.png         tracked: a still
site/assets/figures/<gate>/<entry>/<view>.<sha12>.chart.json  tracked: a chart's payload
site/assets/figures/<gate>/<entry>/<view>.<sha12>.gltf        tracked: a 3D file (.glb or
                                                              .gltf, as the viewgen wrote
                                                              it), once an export re-ran
                                                              the entry
site/assets/figures/<gate>/<entry>/<view>.view.json           tracked: the sidecar
site/assets/live/<gate>/<entry>/<view>.<sha12>.gltf           IGNORED: any other 3D file
```

```jsonc
{ "schema": 1, "view": "cfd-openfoam.speed", "gate": "cfd.pressure_drop",
  "entry": "9c1e04d2b7a35f60-3a7b19e2", "kind": "image", "recipe": "<sha256>",
  "files": { "image": { "name": "cfd-openfoam.speed.1a2b3c4d5e6f.png",
                        "sha256": "<64>", "bytes": 10800 },
             "field": { "name": "...gltf", "sha256": "<64>", "bytes": 940212,
                        "live": false },
             "chart": { "name": "...chart.json", "sha256": "<64>", "bytes": 2210 } },
  "title": "...", "caption": "...", "alt": "...",
  "scale": { ... }, "marks": [ { "at": 0.25, "text": "upstream plane" } ],
  "tool": { ... } }                       // the viewgen's meta.tool, as it gave it
```

Schema 2 (design P2) is schema 1 with two more keys, `"scales": [ { "on": "surface",
... }, { "on": "lines", ... } ]` and `"light": "scene"` (or `"none"`, or `""`). The
build writes schema 2 only for a figure that states `scales` or `light`, so a
one-scale figure's sidecar keeps its bytes; the reader takes both schemas, each with
its exact key set (`page.SIDECAR_KEYS_BY_SCHEMA`), and refuses one whose colour keys
are not of their shape (`page.scales_problem`).

The sidecar holds no clock and no machine path. It is shown only when its folder's
gate and entry are its own, the verdict cache holds that entry
(`.nopekit/verdicts/<gate>/<entry>.json`), and each file it names that is not live is
on disk, at most 2 MiB, at the sha256 and size it names (`page.read_sidecar`). A forged
folder, a swapped PNG or a file over the cap placed by hand is not shown, and a page
that shows one anyway (a resealed hand edit) reads *edited by hand* in `site status`,
`status` and `doctor`, which read the disk again. `site._kept_json`
leaves `assets/figures/` and `assets/live/` out of the number pool: no scale end,
tick, mark or series point vouches for a number in prose.

**Which figure the page shows** (`page.figure_for`), and the sentence it bakes from
`report.HUMAN["site"]["figure"]`:

| On disk | `currency` | Sentence |
|---|---|---|
| a valid sidecar under the served entry, which is fresh | `current` | This picture is from the result on this page. |
| the same, the served entry not fresh | `stale` | This picture is from the result on this page. That result is not current. |
| none there, and exactly one other entry's folder holds one | `older` | This picture is from another result. The result on this page has no picture yet. |
| none, or several other folders (after a merge) | `none` | No picture of this result is on the page. |

An `older` figure also bakes that result's own outcome and value (`drawn_text`, "That
other result: pass, 2 Pa.") from its cache entry. Each claim bakes one `binding_text`
sentence per gate and entry ("The pictures of <gate> come from its result <entry>. This
page shows that result.", or "... another result of <gate>."). Why a figure is absent
on this computer goes to the build warnings only, so a fresh clone bakes the same
`page.json`. What slipped through in review of the bake: the `older` sentence said
"an earlier result", and after A, B, A the picture beside A is B's, the later run.

**Retention** (`clean_assets`' keep set). The cleaner keeps the files of the figure each
bound view resolved to (current, stale or older) and the served entry's live file.
While the served folder has no valid sidecar, the one older folder stays; once the
served entry is drawn, the older folder goes. A 3D file is committed under `figures/`
only when an `exports/*.json` `reran` row names the entry; until then it lies under the
ignored `assets/live/` and only `build.json`'s `live_figures` names it, and the next
build after an export copies it into the committed folder. What slipped through
before this: an unbound viewgen whose tool was missing returned no view, so the
cleaner deleted the picture it drew last time; it keeps its last row now (`site.py`,
the kept-row paragraph in `docs/SPINE_CONTRACT.md`). A prose figure belongs under
`site/content/img/`: one under `assets/` reads `missing`, because the cleaner owns that
folder.

**A, B, A, walked.** A check of design A, then of B, then of A again (a cache hit, with
B's files still in `.nopekit/out/`): A's manifest refuses B's bytes, so the viewgen gets
no summary and draws nothing, and its note says to run `nopekit check --force --only
<gate>`. The page shows A's committed figure if A was ever drawn (the memo, `current`),
else B's as `older`, else `none`.

**Where the page reads it.** `page.json`'s claim `figures` and `binding_text`, and
`representation.figure` and `controls` for a figure hero ("`page.json`"); `state.json`'s
bound row `binding`, `data_url` and `field_src` (`state.json`, "views"); `build.json`'s
`live_figures` (below); the claim card in `lib/figures.js` ("The renderer"). The chart is
a Vega-Lite spec the spine bakes whole (`page.chart_spec`), with a table of its numbers
(`page.chart_table`: a long series cut to its first and last rows, numbers as the page
writes them) that the card shows where the chart library does not load; and a field is
drawn as the file holds its colours (`lib/viewer3d.js` field mode, its first view
looking down on the cut). A still's marks are its plane words in a row above the
picture, each over its baked position with a short line down to the picture's edge:
nothing is drawn over the picture (review of P2: on a phone the words hid most of a
10 to 1 strip, and a white line hid the tool's own black line). A figure still as the
hero of either page is drawn the same way, with its caption. A legend's tick mark is
always at its baked position; its words run inward near an end of the ramp. The card
says the claim's cause sentence (`cause_text`) under its value. `page.status_problems` holds
every figure (G1-G8, listed under `page.json`).

## `build.json`, live and published

```jsonc
{ "schema": 1, "built": "2026-10-14T21:03:00Z", "live": false,
  "id": "<16 hex: a seal over state.json, map.json, page.json, views/*.json>",
  "ok": true, "error": "",
  "verdicts": { "bracket.deflection": { "when": "...", "age_s": 412.0, "aged": false } },
  "mixed_age": { "shown": false, "oldest_s": 412.0, "newest_s": 3.0, "gates": [] },
  "tracing": false,    // architecture phase 3b: a trace holds its lock now
  "live_figures": { "cfd.pressure_drop/9c1e04d2b7a35f60-3a7b19e2/cfd-openfoam.pressure":
                    "assets/live/cfd.pressure_drop/9c1e04d2b7a35f60-3a7b19e2/cfd-openfoam.pressure.1a2b3c4d5e6f.gltf" } }
                       // issue #15: each live 3D file of a bound figure on this computer
```

`live_figures` (issue #15) names, by figure id (`<gate>/<entry>/<view>`), the 3D file
a bound viewgen drew for the served entry when no export re-ran that entry: it lies
under `assets/live/`, which git ignores (`site build` adds `assets/live/` to
`site/.gitignore` once a bound viewgen is registered), and no tracked file names it,
so a static host never shows a 3D view that leads nowhere.

`data/build.json` is the clock (SPEC 32), ignored through `site/.gitignore` (`site
build` appends the line to an older one, and writes a `site/.gitignore` of those
lines alone when there is none). `verdicts` holds each result's `when` (the
resolver's), `age_s` (`null` when unknown, never 0) and `aged` (`site.AGED_SECONDS`, a
day); `mixed_age.shown` is the banner's decision (two ages or more, the oldest aged
and more than four times the newest). **Live** (SPEC 33): `nopekit site serve --watch`
polls `site.WATCH_DIRS` — the records, the project file, the model, the project's
evaluators, views and generators, the packs copied in, the verdict cache, the trace,
the evidence manifests (`.nopekit/cache/evidence`: a `check --force` that solves an
entry again files no new entry, and its new manifest is what lets a bound figure draw),
`site/content/`, `site/annotations.json` and `site/explode.json` — by size and mtime;
when the set changes and holds for two polls it runs `nopekit site build --live` in a
fresh process, waits while another command holds the lock, never runs `check`, and on
a failed build writes `ok: false` with the error and the previous `id`. The page
polls `build.json` every 2 s while it reads `live: true`, and reloads its data when
`id` moves. **Published**: Pages serves `site/` as committed, or CI builds it; a 404
or `live: false` stops the polling, so the page needs only static files.

**Detached** (the acceptance run after phase 3b). `nopekit site serve --watch --detach`
starts the same server as a process of its own and exits 0 once it listens, printing
the ready line (`serving site at http://…`, `site.SERVE_READY_RE`): a new session, so
it has no controlling terminal and its own process group, stdin `/dev/null`, its
output appended to `.nopekit/out/serve.log`, and no other descriptor of the shell that
started it. It opens no browser. What slipped through: the agent started
`site serve --watch` as a background task, and there a background task lives at most
two hours, so the person's page died mid-session; under headless `claude -p` a
background task that still runs keeps the turn from ending. The server records its
pid, address and URL in
`.nopekit/out/serve.json` and holds `.nopekit/out/serve.lock` (`flock`) for its life:
the lock, never the pid alone, says it runs, so a record whose server died reads stale
and `--stop` never signals a pid the OS gave to another process. A second `--detach`
prints the running URL and starts nothing; `nopekit site serve --stop` stops it
(SIGTERM, between requests); `nopekit site status` prints a `live:` line (and `--json`
a `server` object) saying whether one runs and where, and `doctor`'s `site` row names a
running one. It stops by itself within 2 s when its record no longer names it or
`site/index.html` is gone. What it writes of its own is under the ignored
`.nopekit/out/`; its rebuilds write what `site build --live` writes. POSIX only (Linux
and macOS): on Windows `--detach` says so plainly; there, run `site serve --watch` in a
terminal of its own.

**Rounds** (architecture phase 6, SPEC 24). When the agent's turn ends, the plugin's
Stop hook runs `nopekit round --hook`: it rebuilds the site when `site/index.html`
exists (the same build as `site build`, over the round's resolution), and commits
`site/` as its ignore files allow — `data/build.json`, the clock, never reaches git, so
a round that judged nothing new commits no byte of the page. The hook's message, which
the person sees, names each file in `site/content/` stamped before the latest results —
each board's, and `intro`, `next` and `story`, which are stamped against every claim
and the readiness, so any status that moves anywhere names them. The nopekit skill has the
agent, before its turn ends, run `nopekit check` first (a stamp digests the tokens as
they read when it runs, and after a model edit those are Stale until a check; the hook's
check would then move them past the text it just stamped — review of phase 6), then read
each of those files against the page, rewrite it, and run `nopekit site stamp <board>`
for each (`--all` for every one).
`state.json`'s `meta` never carries `people` (the page is published).

## Style

The site's words follow ASD-STE100 (SPEC 34): short sentences (25 words at most, rule
4.1; ids, parentheses, code and URLs not counted), one word for one thing, present
tense, the imperative for an instruction, no contraction. And no LLM register (SPEC 35):
no em-dash aside, no "not X but Y", no rhetorical question, none of "delve", "robust",
"seamless", "crucial", "honest". `tests/test_style.py` scans, with
`prose.register_problems` and `prose.long_sentences`: every `HUMAN["site"]` string, the
word keys of a built `page.json` and `map.json` (`page.WORD_KEYS`; every string key is
in it or in `page.RECORD_KEYS`), every template string, the README block and the
bracket's `site/content/`. Exempt: record text (statements, a group's title and line,
`mechanism.how`, an evaluator's detail, a rationale), the CLI's shared producers
(`reason`, `stale_reason`, `limit_text`) and `state.json`'s words, phrases and readiness
sentence (the technical record, in the glossary's register). A recorded result in the
drawer (`claims[id].physical`: `heading`, `why`, `recorded`) is the site's words
(`report.result_facts(register="site")`), scanned. Neither page says a word a
newcomer cannot know: "a built part" where the CLI says "an article", "showed that it
can fail" for qualified, "the current design" for the current inputs, and
`TheSiteSaysABuiltPart` fails on "article" in any site string, baked word or template
string. Two rules are review-only, because no pattern catches them without false
positives: a list of three for rhythm, and a closing line that restates the paragraph.
"Checked does not mean true" appears once per page: `verdict.limits`, on each of the
two pages, and never as a literal in a shell, `page.js`, `marketing.js` or `lib/`.

## The badge

`init` writes, and `nopekit site badge` writes or refreshes, this block at the bottom
of `README.md` (creating `README.md` as `# <name>` and the summary when absent); a
second run changes nothing, and every other byte stays as it was. HTML comments mark
it, because a `#` line is a heading in Markdown. A block is the begin marker on a
line of its own, at most four lines of HTML, then the end marker on a line of its
own (`store.readme_has_badge`); a marker quoted in a sentence, or a begin whose end
was deleted, is no block, so the block is appended and the person's text is never
cut. The file's line ending (CRLF or LF) is kept. `doctor`'s `readme` row warns when
there is no block.

```html
<!-- nopekit:begin badge -->
<a href="https://github.com/neuman/nopekit"><img src="https://raw.githubusercontent.com/neuman/nopekit/main/docs/img/made-with-nopekit.png" width="160" alt="Made with nopekit"></a>
<!-- nopekit:end badge -->
```

## What the page must show

- **The headline verdict, first and unsoftened**, on both pages. If something
  critical is unsettled, the page says so above the fold. A page that leads with a
  render and hides a failing claim is marketing in the bad sense: the marketing page
  leads with the render and puts `verdict.headline`, the tally chips and the limits
  line beside it, above the fold at every width, and each requirement's chip below.
- **On the engineering page, every claim with its status in GLOSSARY §3's word** (from
  `state.json`'s `words`),
  and for a Checked claim the gate and measured value that earned it; for any other,
  its reason and each covering gate that produced no pass that counts.
- **The viewer**, with explode, isolate, wireframe, and the verdict overlay.
- **Physical claims and assumptions**, never blurred into the proven ones.
- **Provenance on demand**: a parameter's rationale and rejected alternatives, and
  the ingested evidence that grounds it.
- **How old the results are, and which are not current.** A stale result must look
  stale, on its own row and in the banner. The marketing page says
  `verdict.notice_public` while any result is not current, and links to
  the engineering page, where each claim and its evidence are one link away.
