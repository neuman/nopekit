# Pushing a nopekit project to Eddy

You are a coding agent with the nopekit plugin. This Eddy is at `{{EDDY}}`.

Eddy is the git server a nopekit project is pushed to. It keeps only what follows the
nopekit structure, and it shows the person every candidate of the project: a family
tree, an engineering view, a marketing view and the files. You work in your own local
clone, commit as often as you like, and push. **The push is where the rules hold.** A
push that breaks one is refused with lines that say what to change. Fix it and push
again without asking the person: refusals are your inner loop, not their problem.

The person sees the project at `{{EDDY}}/#/p/<thing>`.

## 1. Once per project

Pick the thing's name: lower-case letters, digits and `-` (for example `whistle`).
The person gave you a token. Then:

```sh
git init -b main                                   # if the folder is not a repository yet
curl -s -X POST {{EDDY}}/api/things \
     -H "Authorization: Bearer <token>" -d '{"name": "<thing>"}'
git remote add eddy {{EDDY}}/<thing>.git
git config http.{{EDDY}}/.extraHeader "Authorization: Bearer <token>"
```

The token goes in the clone's git config only. Never write it into a tracked file.

Check that your nopekit has the push validator: `nopekit validate --help` must print
usage. If it does not, your plugin is older than Eddy; tell the person and stop.

## 2. What a candidate is

A **candidate** is one commit that says "this is a version of the design worth
looking at". Everything else you commit is a working commit: kept, never judged.
A candidate's commit message ends with this block as its last paragraph, with
nothing else in that paragraph:

```text
Nope-Schema: 1
Nope-Kind: candidate
Nope-Name: v0.2
Nope-Agent: designer
Nope-Model: <your model id>
```

- `Nope-Name` is the candidate's name: letters, digits, `.`, `_`, `-`. Each name is
  used once on a line.
- The commit's subject is shown under the candidate in the tree. Write one line that
  says what changed from its parent and why: "Arm thickened from 7 mm to 8 mm so the
  tip sag passes".

A candidate is pushed to a **line**, `refs/heads/cand/<line>`: one design direction.
A line only grows: each new candidate on it descends from the last one. A different
direction is a new line with its own name.

## 3. What a candidate's commit must hold

Do these before each candidate commit. The hook checks the first four; Eddy's screens
need the rest.

1. **A nopekit project**: `.nopekit/project.json`, records in their folders. Set
   `revision` in `.nopekit/project.json` to the candidate's name, so the page's
   readiness sentence names it.
2. **`spec/packs.json`**: `{"schema": 1, "packs": {}}` when the project uses only
   nopekit's bundled packs.
3. **A current state file**: run `nopekit round` last, after every other change, and
   commit what it writes (`.nopekit/round.json`, new files under
   `.nopekit/verdicts/`). A candidate committed before its `round` is refused.
4. **No ignored outputs**: nothing from `.nopekit/cache/`, `.nopekit/out/`,
   `__pycache__/`, `REPORT.md`, `out/`, `site/data/build.json`.
5. **The baked page data**: run `nopekit site init` once in the project. After that
   `nopekit round` rebuilds `site/data/`. Track `site/data/page.json`, `map.json` and
   `state.json`, plus `site/assets/` and `site/content/`. Eddy draws the engineering
   and marketing views from these files in the candidate's own commit. Run
   `git ls-files site/data` to confirm they are tracked; if the list is empty, the
   project's `.gitignore` is hiding them.
6. **`site/thumb.png`**: one static picture of this candidate, about 800 px wide,
   under 1 MB. A render of the model is best. The tree draws it, and it is what shows
   when a live view cannot load.
7. **The story**: `site/content/story.md` is the marketing view's text, and
   `site/content/intro.md` opens the engineering view. Stamp them with
   `nopekit site stamp` after the last check so they read as current.

## 4. Marks: selected, proposal, not selected

The tree colours each candidate by a mark. You record the marks; Eddy only shows
them. They live in one file on `main`:

```json
{"schema": 1, "marks": {"v0.1": "not-selected", "v0.2": "selected", "v0.3-light": "proposal"}}
```

at `eddy/marks.json`. The marks are `selected`, `proposal` and `not-selected`. Mark
exactly one candidate `selected`: the one the person chose to go on with. Mark a
candidate you offer as an alternative `proposal`. When the person says "go with
v0.3", change the file, commit it (a working commit is enough) and push `main`.
Eddy opens the project on the selected candidate.

## 5. Boxes: your own pictures and visualizations

A view is drawn by Eddy's standard renderer from the baked data. Where the design
needs something the standard renderer does not draw, you supply a **box**. A box is
one folder, `site/boxes/<id>/` (`<id>`: lower-case letters, digits, `-`), holding
`box.json` and its files:

```json
{"schema": 1, "kind": "custom", "title": "Sag against thickness",
 "caption": "The tip sag goes as one over the cube of the thickness.",
 "src": "index.html", "thumbnail": "thumb.png",
 "view": "both", "section": "implications", "about": "C1", "aspect": "16/9"}
```

| key | value |
|---|---|
| `kind` | `image` (`src` is a `.png`, `.jpg`, `.gif` or `.webp`), `video` (`.mp4`, `.webm`, `.mov`) or `custom` (`src` is one `.html` file) |
| `thumbnail` | required: a static image in the same folder. It shows while the box loads, and stays if the box fails |
| `view` | `engineering`, `marketing` or `both` |
| `section` | where it sits: `choices`, `implications` or `claims` on the engineering view; `story` or `specs` on the marketing view |
| `about` | optional: a claim id, a board's key or a choice group's key, to sit beside it |
| `aspect` | optional, `"<w>/<h>"`; `4/3` when left out |

A **custom** box is one self-contained HTML document: inline its script, style and
data. It runs in a sandboxed frame with no network and no access to the page around
it. When it has drawn, it must say so, or its thumbnail stays in front:

```js
parent.postMessage({eddy: "ready"}, "*");
```

The frame's URL ends in `#dark` or `#light`: read `location.hash` to match the theme.
Use a box for what is specific to this design (a chart of a sweep, an assembly
sequence, a photo of a print). Do not use one to restate a claim's status: the
standard renderer already shows every status, from the data.

## 6. Push

```sh
nopekit round
git add -A && git commit                           # subject + the trailer block above
nopekit validate . refs/heads/cand/<line> 0 HEAD   # what the hook will say, before pushing
git push eddy HEAD:refs/heads/cand/<line> HEAD:refs/heads/main
```

`nopekit validate` judges the push exactly as the server will. For a line that
already exists, give its current tip instead of `0`
(`git ls-remote eddy refs/heads/cand/<line>`).

Keep `main` as your trunk and push it with each candidate: `eddy/marks.json` is read
from it. To explore an alternative from an older candidate, branch from that commit,
push the branch to its own line, and return to `main`. Scratch work that is not a
candidate can go to `refs/heads/draft/<name>`, which takes anything.

## 7. When a push is refused

Each refusal is one line: `remote: E## <where>: <what is wrong>; <what to do>`. Read
it, make the change, commit, validate, push. The usual first ones:

| rule | what it means |
|---|---|
| E24 | the commit you pushed to a line has no trailer block: it is a working commit |
| E16 | a `Nope-` line is not in the last paragraph of the message |
| E34 | `spec/packs.json` is missing |
| E40, E42, E44 | the state file is missing or behind: run `nopekit round` last and commit it |
| E28 | an ignored output is tracked: `git rm --cached` it |
| E23 | that `Nope-Name` is already used on this line |
| E04 | the line's tip is not an ancestor of what you pushed: a line only grows |

The whole rule table is section 8 of `docs/EDDY_CONTRACT.md` in the nopekit
repository. A note (`N01` to `N04`) is printed and refuses nothing; `N01 … awaits
acceptance` is expected on every candidate today.

## 8. Tell the person

After a candidate lands, give the person its link, `{{EDDY}}/#/p/<thing>`, and one
sentence on what changed. Do not report refusals you already fixed.
