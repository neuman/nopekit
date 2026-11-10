# The layout contract with Eddy

Written for the agent that pushes and for the reviewer of the hook. It says what a
repository under Eddy looks like, what a push may carry, what the pre-receive hook
refuses, and what the hook cannot see. The rule table in section 8 is the one the code
holds (`receive.RULES`, `receive.NOTES`); a test keeps the two equal. Every `nopekit`
command printed here parses (`tests/test_docs_commands.py`).

## 1. What this is

A thing is one git repository. Eddy is its git server: the pre-receive hook is the
rule, the renderer regenerates the state file, and Eddy alone writes acts. The maker's
agent works in a full local clone with the nopekit plugin. It commits freely, runs
checks and solvers locally, and pushes. The push is the one place a move can be seen,
so the hook that judges the push is the log, and nothing escapes it.

Milestone 0 ships two things and nothing else:

- `nopekit validate`, a library (`src/nopekit/receive.py`) and a command that judge a
  push by its refs, its trailers, its trees, its digests and its objects;
- `server/pre-receive`, the hook that runs it.

No act, no renderer and no page change land in this milestone. Until acts land,
measurements, exports and preferences keep nopekit's sealed chains (`results/`,
`exports/`, `preferences/`).

What the validator does and does not do:

- It executes nothing from the tree. No model is loaded, no gate module is imported,
  no generator runs.
- It starts no git process itself. Every question is a `vcs` call
  (`test_meta.NoGitOutsideVcs`), and `vcs` answers `None` on any failure.
- It reads no clock and no environment variable. The command line and the hook script
  hand everything in.
- It writes only under a temporary directory outside the repository, removed in a
  `finally`. Nothing in the repository or in a clone moves.
- It fails closed. Any `None` from `vcs` is E07 and exit 2; the hook declines on any
  non-zero exit, its own crash included.
- It never says *checked*. Its vocabulary is well-formed, self-consistent, the
  tree's own. It loads a verdict entry for its shape, its digest and its rho only
  (invariant 12). A token in a pushed tree was run by the agent, and the page says so.

What slipped through in the design, and what each rule below closes: a validator that
walked `old..new` from the main object store saw no pushed object inside the hook, so
it would have refused everything or, walking nothing, accepted everything (section 10's
quarantine); one that diffed each commit against its parent let a working commit
smuggle anything (section 6's previous-claiming-commit rule); one that asked
`merge-base --is-ancestor` let a merge whose first parent was a foreign chain rewrite a
line's history as a fast-forward (E04).

## 2. Refs

| ref | what it is | what the hook holds |
|---|---|---|
| `refs/heads/cand/<name>` | a **line**: one chain of candidates. Its commits are its first-parent commits; a merge's second parent is never on it | fast-forward along the first parent only (E04); never deleted (E03); `<name>` is `[A-Za-z0-9][A-Za-z0-9._-]{0,63}` and no other line's name — in the repository, or earlier in the same push — differs from it only in case (E05); its tip is a commit (never a tag or a tree) and a candidate or an evidence commit (E24); every new first-parent commit is judged (sections 3 to 7); every new object is scanned (section 9) |
| `refs/heads/draft/<name>` | scratch: anything | scanned (section 9), nothing else. Deletion and a non-fast-forward are allowed. Kinds are never read and nothing on it is rendered |
| `refs/heads/main` | the README and the Pages workflow | never deleted (E03); scanned. A claiming commit on `main` means nothing and is not read |
| `refs/heads/spec` | Eddy's: each commit's tree is a spec tree (section 5) | no push (E02). Written only by the accept-gates act, from inside the bare repository |
| `refs/heads/published` | Eddy's: the candidate the public site shows | no push (E02). Moved only by a publish act |
| anything else | a tag, `refs/notes/*`, `refs/heads/feature`, `refs/pull/*` | refused (E01) |

A pre-receive hook refuses the **whole push** when any ref in it is refused. Every
line's refusals are printed, in the order git gives the refs, and the summary line says
nothing landed. A push of one ref that is accepted lands; `old == new` never reaches
the hook, with one exception: the deletion of a ref that does not exist
(`git push origin :refs/heads/draft/x`) arrives as two zero ids, and is judged as a
deletion — accepted on `draft/*`, E03 on a line or `main`. A `new` that is not a
commit (a tag object, a tree) is E24 on a line; on `draft/*` and `main` the scan runs
and git itself then refuses the ref write (*trying to write non-commit object*).

A repository under Eddy is created under the hook. A repository that predates it is
pushed through the hook from empty, so no line holds a commit nobody judged.

## 3. Commits and trailers

A commit carries a closed trailer schema, `Nope-Schema: 1`. The keys:

| key | shape | candidate | evidence | act |
|---|---|---|---|---|
| `Nope-Schema` | `1` | required | required | required |
| `Nope-Kind` | `candidate`, `evidence` or `act` | required | required | required |
| `Nope-Name` | `[A-Za-z0-9][A-Za-z0-9._-]{0,63}`; given once per line | required | optional; when given, its candidate's | — |
| `Nope-Trigger` | `<gate id>/<16 hex>-<8 hex>`: an entry the tree holds under `.nopekit/verdicts/` | optional | optional | — |
| `Nope-Agent` | `designer` or `reviewer` | required | required | — |
| `Nope-Model` | a model id: not empty, at most 200 characters | required | required | — |
| `Nope-Act` | `accept-gates`, `approve-print`, `measure`, `prefer` or `publish` | forbidden | forbidden | required |
| `Nope-By`, `Nope-Role`, `Nope-Candidate`, `Nope-Spec` | an act's | forbidden | forbidden | required |

Every value is at most 200 characters: a trailer holds a model id or a name, never
prose. A key not in the table is refused with a suggestion (E13); a known key spelled
in another case is refused (E14); a key given twice is refused (E15). `Nope-Schema` is
spelled exactly `1`: a larger ASCII number is newer than this server reads, anything
else (`01`, `²`, `١`) is not 1 (E17). Trailers that are not `Nope-*` (`Signed-off-by`,
`Co-authored-by`) are ignored.

**The message is UTF-8 text** (E12). A control byte — anything below 0x20 but newline
and tab, or 0x7f — or a byte that is not UTF-8 refuses the commit before anything in
it is read: nothing of such a message is trusted, and git's own trailer reader is not
asked about it. A commit made with `i18n.commitEncoding` set to another encoding is
refused with that word; commit it as UTF-8.

**Working commits.** A commit with no `Nope-*` key anywhere in its message is a working
commit. It is kept, it is never classified, and no rule reads it: its changes land in
the next claiming commit's diff (section 6). A commit that holds a `Nope-` line git does
not read as a trailer is not a working commit: it is refused (E16), so the two readers
never disagree.

**The block rule is git's.** The trailer block is what git reads as one
(`%(trailers:only)`): the last paragraph of the message, when git takes it for a
block — every line a trailer, or, beside a trailer git itself generates such as
`Signed-off-by`, at least a quarter of its lines. A `Nope-` line git does not return as
a trailer, wherever it sits — in a middle paragraph, or in a last paragraph git reads
as prose — is refused (E16); a last paragraph git reads as a block is accepted
whatever else it holds, because the two readers then agree. The sure way is the rule's
advice: put every `Nope-` line in the last paragraph with nothing else in it.

**Kinds.** `candidate` and `evidence` are the two kinds a push carries, and together
they are the **claiming commits**. `act` is a kind the hook knows and never accepts
from a push (E19): an act is recorded by Eddy on a signed-in person's click, from
inside the bare repository.

**Names.** A `Nope-Name` is given once per line (E23). An evidence commit's
`Nope-Name`, when it has one, is its candidate's — the nearest candidate below it on
the line. A `Nope-Trigger` names an entry the same tree holds (E22).

**Schema 1 is frozen.** A new line is validated from its first commit, and a line that
forks from another is validated against the commit it forks from; a rule that grew
stricter under the same schema number would refuse an honest line's history. A
stricter rule is `Nope-Schema: 2`. A commit with a newer schema than the server reads
is refused (E17), never guessed at.

## 4. The candidate tree

A claiming commit's tree is read whole. It may hold:

- files and directories only: no symbolic link, no gitlink (E25);
- one name per path: no two paths equal after case folding or Unicode normalisation,
  no path part that is `.git` (in any case), `.`, `..` or empty, no `\` and no control
  byte in a path (E26);
- no `.gitattributes` that makes a checkout differ from the tree: `export-ignore`,
  `export-subst`, `filter=`, `ident`, `eol=crlf` and `working-tree-encoding=` are
  refused (E27). nopekit's own block (`* text=auto eol=lf`, the binary kinds `-text`)
  is fine;
- no ignored output (E28): nothing that matches a pattern of the three ignore blocks
  nopekit writes (`.nopekit/.gitignore`'s, the project root's, the site's), each
  rooted where its block lives. Committed, an output would be read as truth on every
  clone;
- under `.nopekit/`, only the closed list: `project.json`, `.gitignore`, `round.json`,
  `verdicts/`, `trace/` (E28). A `.nopekit/packs/` would shadow a bundled pack by name;
  a pack is pinned in `spec/packs.json` instead;
- under `.nopekit/verdicts/`, only `<gate>/<16 hex>-<8 hex>.json` and
  `<gate>/control-<16 hex>-<8 hex>.json`, with `<gate>` a name `verdicts` accepts as a
  directory; under `.nopekit/trace/`, only `<16 hex>.json` and
  `<16 hex>.amber.<16 hex>.json` (E29);
- at most 20000 files, and at most 50 MiB, among what the hook reads (the records,
  `.nopekit/project.json`, `.nopekit/round.json`, `.nopekit/verdicts/`,
  `.nopekit/trace/`, every `.gitattributes`, `spec/packs.json`) (E30);
- `.nopekit/project.json` (E31). A legacy layout is refused, never migrated;
- records the strict reader accepts (E32): `store.load` runs on a copy of the tree,
  and its refusal is printed verbatim — an unknown key with its suggestion, a
  duplicate key, NaN, a stem that is not its id, a case variant, a forbidden key, a
  broken seal in `results/`, `exports/` or `preferences/`. Under `claims/`,
  `requirements/` and `milestones/` every file is a top-level `<id>.json`, so a stray
  file cannot move the spec tree without moving a claim.

Every JSON file the hook reads is scanned for its nesting before any reader opens it:
one nested deeper than 32 levels (`receive.MAX_JSON_DEPTH`; the deepest file nopekit
writes is 8) is refused under its own rule's words — E32 for a record, E34 for
`spec/packs.json`, E38 for an entry or a trace file, E41 for the state file — and the
copy is read no further. A reader would otherwise recurse to the interpreter's limit,
which is not a refusal but a crash, and a bound the interpreter sets is not the same
in every process.

**Where facts live under Eddy.** The homes are `CLAUDE.md`'s ("Where facts live"),
unchanged: the records in their directories at the project root (`claims/`,
`requirements/`, `params/`, `decisions/`, `needs/`, `inputs/`, `results/`, `views/`,
`milestones/`, `exports/`, `preferences/`, `groups/`, `.nopekit/project.json`), the
model in `model/`, the verdict cache in `.nopekit/verdicts/`, the trace in
`.nopekit/trace/`, the round record in `.nopekit/round.json`. One file is added:
`spec/packs.json`, the pinned packs. It is tracked, the agent writes it, and it is not
a record: no digest reads it and no committed output moves when it changes.

**`spec/` holds `packs.json` alone** (E34). `claims/`, `requirements/` and
`milestones/` stay at the root; the spec tree of section 5 is derived from them. The
file's shape is strict:

```json
{"schema": 1, "packs": {"<name>": {"repo": "<https URL>", "commit": "<40 or 64 hex>"}}}
```

`schema` is `1`; `packs` is a mapping; each name is `[A-Za-z0-9][A-Za-z0-9._-]{0,63}`;
each value holds exactly `repo` and `commit`; `commit` is 40 or 64 hex; `repo` is an
`https://` URL with a DNS host name — no userinfo, no `..` path part, no `file:`,
`ssh:` or `git:`, no IP literal, no `localhost`. An unknown key is refused with a
suggestion. A project with no pinned pack commits `{"schema": 1, "packs": {}}`.

The pins and the live list agree (E35): every pinned name is in `.nopekit/project.json`'s
`packs`, and every listed name that is not a bundled pack is pinned. Add a pack with
`nopekit packs add <name>` and pin it; or drop the pin.

## 5. The spec tree and acceptance

The **spec tree** of a candidate commit is a tree derived from its tree: the entries
`claims`, `milestones` and `requirements` (each the candidate's own tree of that name,
omitted when absent) and `packs.json` (the blob at `spec/packs.json`, required). It is
the contract — the requirements, the claims with their acceptance conditions, the
milestones, the pinned packs — as one git object.

Its id is git's own tree id, computed in Python (`receive.tree_id`) with git's tree
format:

- the body is one record per entry, `<mode> <name>\0<raw oid>`: the mode in ASCII
  octal with no leading zero (`40000` for a tree, `100644` for the blob), the name, a
  NUL, then the oid as 20 raw bytes (sha1) or 32 (sha256);
- the entries are in git's order: by name, bytewise, a tree's name compared as if a
  `/` followed it. For the four entries that is `claims`, `milestones`, `packs.json`,
  `requirements`;
- the object is `tree <body length>\0` followed by the body, hashed with the
  repository's object format (`vcs.object_format`).

`git mktree`, fed the four `ls-tree` lines, is the oracle; a test holds the two equal,
and holds the empty tree (`tree 0\0`) equal to `vcs.empty_tree`. A spec-branch commit's
tree *is* that tree, so a checkout of `spec` shows `claims/ milestones/ packs.json
requirements/`. A later milestone that moves the three directories under a real `spec/`
in nopekit changes no spec-branch history.

**Acceptance.** A candidate's spec tree id is looked for on every commit of
`refs/heads/spec` (`vcs.ref_trees`). Found, the candidate's gates are accepted. Not
found, the candidate **lands and waits**: the push is accepted with note N01, exit 0.
An evidence commit inherits its candidate's standing and gets no note. An absent `spec`
branch is a valid start: every candidate waits. The accept-gates act (milestone 1)
writes a commit on `spec` whose tree is the accepted spec tree id, from inside the bare
repository, with no second push.

In milestone 0 only the pack evaluators pinned by `spec/packs.json` are accepted
evaluators. A project's own `gates/` and `selftest/` sit outside the four entries
(section 13, point 4).

## 6. Evidence

**What a candidate may add.** Entries under `.nopekit/verdicts/<gate>/` and files under
`.nopekit/trace/`, beside any other change. It may not modify, retype or delete one
(E37): a verdict entry is written once, and evidence is added, never withdrawn. An
entry regenerated after a spine upgrade is a new file beside the old one, which reads
Stale. The bracket README's `rm -rf .nopekit/verdicts` recipe is this repository's
housekeeping, not a line's.

**What an evidence commit may change** (E36): add under `.nopekit/verdicts/` and
`.nopekit/trace/`, and add or rewrite `.nopekit/round.json`. Nothing else. `round`
starts the trace in the background, and the trace files are tracked, write-once,
clock-free and carry no status; without them in the set, every evidence push after a
traced round would be refused.

**The previous claiming commit.** A claiming commit is judged by the diff between its
tree and the previous claiming commit's tree on the line — the nearest candidate or
evidence commit below it on the first-parent chain, found by walking down from the
pushed base (E36, E37, E33, E44 read this diff). A working commit between the two is
not diffed: its changes land in the next claiming commit's diff, and so a working
commit can smuggle nothing. With no claiming commit below, the previous tree is the
empty tree, and the candidate is judged whole. On a line created under the hook the
previous claiming commit is the old tip itself, since every tip is a claiming commit
(E24); the walk exists for the general case and is why the diff cannot be fooled by
splitting a change across pushes.

**Sealed kinds are append-only** (E33). A `results/`, `exports/` or `preferences/` file
is never deleted, and a modified one keeps the previous file's entries as a prefix: the
new list begins with the old list's `raw` entries, byte for byte. The seal itself is
the strict reader's business (E32).

**What the hook verifies of an added entry**, on a copy of the tree with no model
loaded: its reader accepts it (E38, `verdicts._load_entry` or `_load_control` by name
shape — the key order, the digest that seals the body, the name that is its own
`<rho16>-<out8>`, the gate that is its directory's); its `rho` follows from its own
gate, spine, code digest and reads, and a control's `static` from its `static_parts`
and its `rho` from `rho_control` (E39). A trace or amber file is read by
`derivation.read_trace` / `read_amber` (E38).

**What it does not verify.** Whether `reads.params`, `reads.model` and `code.digest`
are this tree's: each needs the model and the gate modules executed, and the hook
executes nothing. An outcome is the agent's word. Tree-visible reads that disagree with
the tree are a note, not a refusal (N03): an agent that ran `check` twice at two limits
between commits commits two honest entries, one of them Stale on arrival; and a forger
who can compute rho can compute those digests too.

**Notes.** N02: an added entry was written under another spine; it reads Stale under
this nopekit. N03: a root-relative key in `reads.files` (or a control's
`fixture.files`, `static_parts.selftest.files`) names a digest the tree's blob does not
hash to, or a `reads.ledger` `claim:<id>` / `acceptance:<key>` differs from what the
tree's records say; the entry arrives Stale. N04: two entries under one gate share a
rho with different outcomes; the resolver reads that as an error and the page shows the
contradiction, not either outcome.

Every token in a pushed tree was **run by the agent**. No reader under Eddy says
*checked by Eddy*: Eddy executed nothing (section 13, point 1).

## 7. The state file

The state file is `.nopekit/round.json`, the round record as `rounds.py` writes it
today: schema 1, fixed key order, fixed header, a `self` seal, indent 2, one trailing
newline. No new file, no format change, no new writer. It is tracked, clock-free,
path-free, free of the spine digest, byte-stable and written only by `nopekit round`
(invariant 8). It carries `computed_from.records` and `computed_from.verdicts`, two
digests that are pure functions of file bytes (`store.records_digest`,
`rounds.verdicts_digest`). A second state document was rejected: a second producer of
every status is S-41's drift again.

**Regeneration.** The `round` counter is an input, taken from the committed file:
regenerate(tree, N) = `rounds.render(rounds.sealed(body, N))`, with `body` computed
from the tree. The hook never compares `round` to a tree-derived number; it only holds
it monotone along the line.

**The hook's five checks**, on every claiming commit, nothing executed:

| rule | holds |
|---|---|
| E40 | the file is present |
| E41 | `rounds.read` says `ok`: not `edited` (the self-digest holds), not `unreadable`, not `newer` than schema 1; and its `round` is an integer of 1 or more (`Previous.round`), since a counter E44 can hold starts at 1 |
| E42 | `computed_from.records` equals `store.records_digest` and `computed_from.verdicts` equals `rounds.verdicts_digest`, each over a copy of exactly the files it walks |
| E43 | the committed bytes equal `rounds.render(rounds.sealed(doc, doc["round"]))`: indent, key order and newline |
| E44 | when the file's blob differs from the previous claiming commit's, its `round` is above that commit's |

`computed_from.design` and `computed_from.trace` need the model loaded and are not
held by the hook.

**The renderer's check** (milestone 1, sandboxed, out of scope here) regenerates the
whole body in a plain checkout — no `.git` history, no `.nopekit/obs/`, no
`.nopekit/cache/`, an empty `HOME`, `NOPEKIT_PACK_PATH` unset,
`NOPEKIT_TRACE_BACKGROUND=0`, the packs materialised from `spec/packs.json` — and
compares whole bytes with the committed `round` as input. It must run
`nopekit check --force` at tier 0, not only the resolver: a forged PASS at the honest
rho is served Fresh by the resolver, the sweep does not re-run a Fresh gate, and
regeneration then reproduces the forged tokens byte for byte (section 13, point 1).
No ignored output may sit in a claiming commit's tree (E28), so neither side has a
remembered crash, an obs ordering or an evidence manifest the other lacks.

**Residues.** A solver present on the agent's machine and absent from the image makes
regeneration read *behind (resolution)*; the renderer marks, never refuses, and the
image must carry every bundled and pinned pack's `requires_tools`. An entry written
under another spine reads Stale (N02). Two outcomes at one rho are shown as a
contradiction (N04), not as either outcome.

## 8. The rule table

`<sha12>` is the commit's first 12 hex; `<oid12>` an object's; the kind and `Nope-Name`
follow once known. Every message is one line, names the rule by its id and says the
fix, and never names another project or a person. Values shown in braces are
substituted; alternatives for one rule are separated by ` / `. The table is
`receive.RULES`, byte for byte, in evaluation order; `applies to` says where the rule
reads: a ref, the push, an object, a commit before its kind is known, a claiming commit
(its trailers, its tree, its diff against the previous claiming commit), the tip, a
candidate.

| id | applies to | refuses | message |
|---|---|---|---|
| E01 | ref | a ref outside the layout | `E01 {ref}: not a ref Eddy keeps; push to cand/<name> (a line), draft/<name> (scratch) or main` |
| E02 | ref | a push to Eddy's refs | `E02 spec: written only by Eddy when the gates are accepted; push a candidate to cand/<name> and accept it there` / `E02 published: moved only by a publish act; push a candidate to cand/<name>` |
| E03 | ref | deleting a line or main | `E03 cand/{name}: a line is never deleted; leave it and push a new line` / `E03 main: never deleted; it carries the README and the Pages workflow` |
| E04 | ref | old not on new's first-parent chain | `E04 cand/{name}: {new12} is not a fast-forward of {old12} along the first parent; a line only grows — rebase onto cand/{name} or push a new cand/<name>` |
| E05 | ref | a line name out of shape, or a case twin | `E05 cand/{name}: a line's name is [A-Za-z0-9][A-Za-z0-9._-]{0,63}; rename the ref` / `E05 cand/{name}: differs only in case from cand/{other}; pick another name` |
| E06 | ref | more than WALK_LIMIT commits to validate | `E06 cand/{name}: more than 10000 first-parent commits to validate; start the line from a candidate commit nearer its tip` |
| E07 | push | git did not answer (exit 2) | `E07 push: git did not answer ({call}: {first stderr line}); nothing landed — push again, and if it repeats the server's keeper reads the hook's log` |
| E08 | object | a blob over MAX_BLOB_BYTES | `E08 push: {path} ({oid12}) is {size} MiB, over the 50 MiB a pushed file may be; nothing pushed is ever removed, so keep it out of the push or shrink it` |
| E09 | push | new blobs over MAX_PUSH_BYTES | `E09 push: {size} MiB of new files, over the 512 MiB one push may carry; push in smaller steps` |
| E10 | object | a secret-looking name in any new tree | `E10 push: {path} looks like a secret ({pattern}); nothing pushed is ever removed, so rename it or keep it out of the push` |
| E11 | object | an object git cannot find | `E11 push: object {oid12} is in neither the push nor the repository; push again` |
| E12 | commit | a control byte, or a byte that is not UTF-8, in the message | `E12 {sha12}: the commit message holds a control byte (0x{nn}); rewrite the message` / `E12 {sha12}: the commit message is not UTF-8 (byte 0x{nn} at offset {n}); commit it as UTF-8` |
| E13 | commit | an unknown Nope-* key | `E13 {sha12}: {key} is not a trailer the schema knows (did you mean {suggestion}?); the keys are Nope-Schema, Nope-Kind, Nope-Name, Nope-Trigger, Nope-Agent, Nope-Model` |
| E14 | commit | a key spelled in another case | `E14 {sha12}: {key} is spelled {Key}; trailer keys are spelled exactly` |
| E15 | commit | a key given twice | `E15 {sha12}: {key} is given twice; give each trailer once` |
| E16 | commit | a Nope- line git does not read as a trailer | `E16 {sha12}: "{line}" sits outside the trailer block, where git reads it as prose; put every Nope- line in the last paragraph with nothing else in it` |
| E17 | commit | Nope-Schema missing, not 1, or newer | `E17 {sha12}: Nope-Schema is missing; a commit that claims a kind carries Nope-Schema: 1` / `E17 {sha12}: Nope-Schema: {n} is newer than this server reads (1); push with Nope-Schema: 1` / `E17 {sha12}: Nope-Schema: "{value}" is not 1; a commit that claims a kind carries Nope-Schema: 1` |
| E18 | commit | Nope-Kind missing beside Nope- keys, or not a kind | `E18 {sha12}: {key} without Nope-Kind; a commit that claims nothing carries no Nope- trailer, one that claims carries Nope-Kind: candidate or evidence` / `E18 {sha12}: Nope-Kind: "{value}" is not one of candidate, evidence, act` |
| E19 | commit | an act in a push | `E19 {sha12}: Nope-Kind: act is never pushed; an act is recorded by Eddy on a signed-in person's click` |
| E20 | claiming | a required key missing | `E20 {sha12} (candidate): {key} is missing; a candidate commit carries Nope-Schema, Nope-Kind, Nope-Name, Nope-Agent, Nope-Model` / `E20 {sha12} (evidence): {key} is missing; an evidence commit carries Nope-Schema, Nope-Kind, Nope-Agent, Nope-Model` |
| E21 | claiming | a value out of shape, or an act key | `E21 {sha12} ({kind} {name}): Nope-Agent: "{value}" is not one of designer, reviewer` / `… Nope-Name: "{value}" is not [A-Za-z0-9][A-Za-z0-9._-]{0,63}` / `… Nope-Model is empty or over 200 characters` / `… Nope-Trigger: "{value}" is not <gate id>/<16 hex>-<8 hex>` / `… {key} belongs to an act; drop it` |
| E22 | claiming | a trigger naming no entry in this tree | `E22 {sha12} ({kind} {name}): Nope-Trigger names {value}, which this tree does not hold under .nopekit/verdicts/; name an entry the tree holds` |
| E23 | claiming | a name reused on the line; an evidence name not its candidate's | `E23 {sha12} (candidate {name}): Nope-Name {name} is already the name of {sha12b} on this line; a name is given once per line` / `E23 {sha12} (evidence): Nope-Name {name} is not its candidate's ({cname}); drop it or match it` |
| E24 | tip | a tip that claims no kind, or is not a commit | `E24 cand/{name}: the tip {sha12} is a working commit; a push ends on a candidate or an evidence commit` / `E24 cand/{name}: the tip {sha12} is a {type}, not a commit; a push ends on a candidate or an evidence commit` |
| E25 | claiming tree | a symlink or gitlink | `E25 {sha12} ({kind} {name}): {path} is a symbolic link; a tree Eddy reads holds files and directories only` / `… {path} is a gitlink (a nested repository); vendor its files or leave them out` |
| E26 | claiming tree | a collision or a forbidden path part | `E26 {sha12} ({kind} {name}): {a} and {b} differ only in case; one file on a case-insensitive checkout — rename one` / `… differ only in Unicode normalisation; rename one` / `… {path} has a path part that is .git, ., .. or empty, or a control byte; rename it` |
| E27 | claiming tree | an attribute that makes a checkout differ | `E27 {sha12} ({kind} {name}): {file} sets {attr} on {pattern}; a checkout must be the tree — export-ignore, export-subst, filter, ident, eol=crlf and working-tree-encoding are refused` |
| E28 | claiming tree | an ignored output, or a path under .nopekit/ nopekit does not keep | `E28 {sha12} ({kind} {name}): {path} is an output nopekit ignores; committed, it would be read as truth on every clone — take it out of the commit` / `… {path} is not a path nopekit keeps under .nopekit/ (project.json, .gitignore, round.json, verdicts/, trace/); pin a pack in spec/packs.json instead of committing it` |
| E29 | claiming tree | a path under verdicts/ or trace/ that is not an entry name | `E29 {sha12} ({kind} {name}): {path} is not a verdict entry name (<gate>/<16 hex>-<8 hex>.json or <gate>/control-<16 hex>-<8 hex>.json); take it out` / `… is not a trace file name (<16 hex>.json or <16 hex>.amber.<16 hex>.json); take it out` / `… {gate}: {verdicts._check_gate_id's words}` |
| E30 | claiming tree | more than the hook may read | `E30 {sha12} ({kind} {name}): {n} files under the records, .nopekit/verdicts/ and .nopekit/trace/, over the 20000 one tree may ask the hook to read; start a new line from a slimmer candidate` / `… {size} MiB …, over the 50 MiB …` |
| E31 | claiming tree | not a nopekit project | `E31 {sha12} ({kind} {name}): not a nopekit project (.nopekit/project.json is missing); run nopekit init and commit what it writes` |
| E32 | claiming tree | a record the strict reader refuses; a non-record under a spec directory | `E32 {sha12} ({kind} {name}): {store's message verbatim}` / `… {path} is not a record; claims/, requirements/ and milestones/ hold <id>.json files only — move it` |
| E33 | claiming diff | a sealed record shortened, rewritten or deleted | `E33 {sha12} ({kind} {name}): {path} drops or rewrites entry {n}; a sealed record is appended, never shortened — restore it from the previous candidate and append` / `… {path} is deleted; a sealed record is never removed` |
| E34 | candidate | the spec out of shape | `E34 {sha12} (candidate {name}): spec/packs.json is missing; the spec pins its packs — commit {"schema": 1, "packs": {}} when there are none` / `… spec/{path} is not spec/packs.json; claims/, requirements/ and milestones/ live at the root and spec/ holds packs.json alone` / `… spec/packs.json: {reader's words, e.g. packs.mesh.repo must be an https:// URL with a host name}` |
| E35 | candidate | pins and the live list disagree | `E35 {sha12} (candidate {name}): spec/packs.json pins {pack}, which .nopekit/project.json's packs does not list; add it with nopekit packs add or drop the pin` / `… .nopekit/project.json lists {pack}, which is neither bundled nor pinned in spec/packs.json; pin it` |
| E36 | evidence diff | a path outside the evidence set | `E36 {sha12} (evidence): changes {path} ({status}); an evidence commit adds under .nopekit/verdicts/ and .nopekit/trace/ and rewrites .nopekit/round.json, nothing else — put the change in a candidate commit` |
| E37 | claiming diff | an entry or trace file modified, retyped or deleted | `E37 {sha12} ({kind} {name}): {path} is modified; a verdict entry is written once — file a new entry at its new rho` / `… {path} is deleted; evidence is added, never withdrawn` |
| E38 | claiming diff | an added entry or trace file its reader refuses | `E38 {sha12} ({kind} {name}): {path}: {loader's words, e.g. hand-edited entry: its digest does not match its content}; file the entry nopekit check writes` |
| E39 | claiming diff | an entry whose rho or static does not follow | `E39 {sha12} ({kind} {name}): {path}: rho does not follow from its own gate, spine, code and reads; file the entry nopekit check writes` / `… static does not follow from its own static_parts …` |
| E40 | claiming tree | no state file | `E40 {sha12} ({kind} {name}): .nopekit/round.json is missing; run nopekit round and commit it` |
| E41 | claiming tree | a state file edited, unreadable or newer | `E41 {sha12} ({kind} {name}): .nopekit/round.json reads edited (its self-digest does not match its content); run nopekit round and commit what it writes` / `… reads unreadable ({why}); …` / `… was written by a newer nopekit (schema {n}); this server reads 1` |
| E42 | claiming tree | a state file computed from other records or verdicts | `E42 {sha12} ({kind} {name}): .nopekit/round.json was computed from other records (computed_from.records {a12}, this tree's records digest {b12}); run nopekit round and commit it` / `… from another verdict cache (computed_from.verdicts …)` |
| E43 | claiming tree | a state file not in rendered form | `E43 {sha12} ({kind} {name}): .nopekit/round.json is not the bytes nopekit round renders (indent, key order or newline differ); run nopekit round and commit what it writes` |
| E44 | claiming diff | a round counter not above the previous claiming commit's | `E44 {sha12} ({kind} {name}): .nopekit/round.json says round {n}, not above the previous candidate's {m}; run nopekit round and commit what it writes` |

Notes (printed, never a refusal, exit code unmoved):

| id | message |
|---|---|
| N01 | `N01 {sha12} (candidate {name}): awaits acceptance; its gates (spec tree {id12}) are on no commit of spec` |
| N02 | `N02 {sha12} ({kind} {name}): {n} added entries were written under another spine ({spine12}); they read Stale under this nopekit` |
| N03 | `N03 {sha12} ({kind} {name}): {path} read {what} at {a12}; this tree holds {b12}, so it arrives Stale` |
| N04 | `N04 {sha12} ({kind} {name}): .nopekit/verdicts/{gate}/ holds two outcomes at rho {rho12}; the resolver reads that as an error and the page shows the contradiction, not either outcome` |

The `where` of a printed line is `cand/<name>` for a ref-level rule, `push` for the
object scan, `<sha12>` for a commit before its kind is known, and
`<sha12> (<kind> <Nope-Name>)` after (`(evidence)` when it has no name). Every path,
trailer value and loader message passes through `util.printable` before it is printed:
a tree-entry name may hold a newline, a trailer value an escape sequence, and the line
reaches the pusher's terminal.

## 9. The object scan

The scan runs for `cand/*`, `draft/*` and `main`, before any blob is read, so no later
rule hashes an oversize object and no blob is streamed before its size is known.

1. Every commit, tree and blob reachable from `new` and from no pre-push ref tip —
   working commits and second parents included — with the path each was first seen at
   (`vcs.new_objects`; `git rev-list --objects` with every other tip, and `old`,
   excluded).
2. The type and size of each (`vcs.object_sizes`, `cat-file --batch-check`), 2000 oids
   per process. An oid absent from the answer is E11.
3. E08 for every blob over 50 MiB, named by its path; a commit or tree over the cap is
   refused the same way, naming the oid.
4. E09 when the new blobs together exceed 512 MiB.
5. Every **new tree object** is parsed (`vcs.tree_objects`) and every entry's path,
   with the tree's own path prepended, is matched against the secret-name patterns
   (`rounds.secret_name`): E10. Trees, not rev-list's blob paths, because rev-list
   names a blob once, at the first path it was seen under, and lists only new objects:
   a blob already in the repository placed at `.aws/credentials` by a new tree is not a
   new blob, but the tree is, so its entry is seen here (what slipped through in the
   first draft of this design).

The scan reads no blob bytes: sizes come from `batch-check`, names from tree objects.

**The two caps, and why.**

- A blob: 50 MiB, `receive.MAX_BLOB_BYTES = rounds.ROUND_MAX_BYTES`. One number for
  the round's commit and the push's acceptance, so a round the Stop hook commits is a
  push the hook accepts. Its provenance stands in `rounds.py`: GitHub warns at 50 MB
  and refuses a push over 100 MB; rejected there, no cap, and the 100 MB hard limit
  that fails long after the commit. Added here: on a line nothing is ever deleted, so
  the warning threshold is the right cap.
- A push: 512 MiB of new blobs, `receive.MAX_PUSH_BYTES`. Ten files at the cap with
  room. Rejected: no total (ten 49 MiB blobs pass the per-file cap); GitHub's 2 GiB
  pack limit (a clone already pays it before anyone notices). It bounds one push's
  cost, not the repository's growth (section 13, point 8).

**The twelve name patterns** are `rounds.ROUND_SECRET_NAMES`, unchanged: `.env`,
`.env.*`, `*.pem`, `*.key`, `*.p12`, `*.pfx`, `id_rsa*`, `id_dsa*`, `id_ecdsa*`,
`id_ed25519*`, `*credentials*`, `*secret*`. Each is matched with `fnmatch` against each
part of a path, case folded, a directory's name included. `.env.example` is refused
too, as the round names it; the agent renames it. The rejected alternatives are
recorded beside the list: a content scan, `*token*`, `*password*`.

**The exempt subtrees** are `rounds.ROUND_NAMED_BY_NOPEKIT`: `.nopekit/verdicts/` and
`.nopekit/trace/`. nopekit names every path in them from an evaluator's id and digests,
so an evaluator called `cad.key` keeps its evidence. The exemption holds only for the
paths nopekit writes there (`receive.secret_name`): under the cache, a gate's directory
and the entries in it (`<gate>/<16 hex>-<8 hex>.json`, `<gate>/control-…`); under the
trace, the two file names of E29. Anything else under them —
`.nopekit/verdicts/aws/credentials`, `.nopekit/trace/id_rsa` — is matched like any
other path. What slipped through: both landed on `draft/*` and on `main`, where no
tree rule runs, and nothing pushed is ever removed.

Names, not contents: a credential in a file named otherwise passes (section 13,
point 7).

## 10. The hook

**Install.** Eddy's image installs `server/pre-receive` as `<bare>/hooks/pre-receive`,
mode 0755, by symlink or by a two-line shell shim:

```sh
#!/bin/sh
exec python3 /opt/nopekit/server/pre-receive "$@"
```

It is run as a script, never with `-m`: `-m` puts the current directory first on
`sys.path`, and the current directory is a bare repository anyone pushed to
(`hooks/stop.py`'s lesson). The script puts nopekit's own `src` first and drops every
relative entry before it imports anything.

The repository is configured with `receive.fsckObjects=true` and
`transfer.fsckObjects=true`: git's own refusal of malformed objects. The hook refuses
`..` path parts itself (E26); the setting keeps them out of the store.
`receive.denyDeletes` and `receive.denyNonFastForwards` are **not** set: `draft/*`
takes both a deletion and a non-fast-forward. The hook holds them for `cand/*` and
`main` (E03, E04).

**What git gives the script** (verified on git 2.34.1): stdin lines `<old> <new>
<ref>`; `GIT_DIR=.` with the repository as the working directory;
`GIT_QUARANTINE_PATH`; `GIT_OBJECT_DIRECTORY` (the quarantine, `objects/incoming-*`,
where the pushed objects are); `GIT_ALTERNATE_OBJECT_DIRECTORIES` (the main store,
`objects/`). Stdout and stderr both reach the client prefixed `remote: `, and a
non-zero exit shows `! [remote rejected] … (pre-receive hook declined)`. For a
**delete-only push** git builds no quarantine, because it unpacked nothing: the hook
then has `GIT_DIR` and neither object variable (the deletion of a ref that does not
exist included, which arrives as two zero ids). A push whose objects the server already
holds still gets a quarantine.

**The quarantine carve-out.** `vcs` strips `GIT_OBJECT_DIRECTORY`,
`GIT_ALTERNATE_OBJECT_DIRECTORIES` and `GIT_QUARANTINE_PATH` from every git call it
makes, with the other variables that pick a repository; with them stripped, a pushed
object is invisible inside the hook (`git cat-file -t <new>` answers *could not get
object info*). So every read the validator makes takes an explicit `objects` argument
(`vcs.ObjectDirs`) that sets exactly those two variables for that call only. Nothing in
the spine reads the variables: the script reads them and hands them to the command as
flags, `--objects` and `--alternates`. Loosening the strip was rejected (every other
`vcs` caller would then answer for the outer repository when run from a hook); reading
the variables inside `vcs` was rejected (an environment read in the spine, and the
test environment strips them anyway).

**What the script reads.** `GIT_DIR` (the repository; `.` when unset);
`GIT_OBJECT_DIRECTORY` and `GIT_ALTERNATE_OBJECT_DIRECTORIES` (handed over as flags);
`NOPE_PUSHER` (reserved: Eddy's front end sets it to the signed-in login; passed as
`--by` and recorded, with no rule in milestone 0). The script refuses when
`GIT_OBJECT_DIRECTORY` is absent: run outside a hook it must not validate against the
main store and accept. The one carve-out is the delete-only push above: with `GIT_DIR`
set and every stdin line a deletion, it is judged against the main store, which reads
no object and is all there is; a delete-only line with neither variable set, or any
other line without a quarantine, is refused. What slipped through before the
carve-out: every deletion of a draft was refused as *not a pre-receive hook*
(`tests/test_receive.py`'s E03-through-git case and
`test_draft_takes_anything_but_a_secret` pin both sides). Stdin passes through
untouched.

**The exit contract.** The hook runs

```sh
nopekit validate <repo> --stdin --objects <quarantine> --alternates <main store>
```

and exits with its code: 0 accepted (notes or not), 1 refused, 2 could not validate
(E07, a malformed line, no such repository). The hook declines on 1 and 2. A crash —
an import that fails, a `SystemExit`, any exception — is caught by the script and read
as a refusal, exit 1, with one line saying what crashed: a crash is never an
acceptance. The inverse of `hooks/stop.py`'s exit contract, where a crash must never
block the turn.

**The line grammar.** Every refusal and every note is one line, `<id> <where>:
<message>`, as section 8 prints them; on the client each reads `remote: E13
<sha12>: …`. Under `--stdin` the `accepted:` summary line is withheld, so no line a
hostile tree could forge (a path that holds `accepted:`) imitates it; every other
line passes through `util.printable`.

**The agent's pre-flight.** Before pushing, in the clone:

```sh
nopekit validate . refs/heads/cand/<name> 0 HEAD
```

`new` may be `HEAD` and `old` may be `0`, so the whole line is validated from the empty
tree exactly as the hook will see a new ref; with `old` the tip already on the server,
only the new commits are. Outside the hook the command reads the clone's own object
store and needs no `--objects`. `--json` prints the same report as data: each ref's
refusals, notes, commits with their kinds, the spec tree id and whether it is accepted,
and `ok`. `-C` is accepted and ignored: `validate` takes a repository path, bare or
not, and never walks up to a project.

**Timeouts.** Every git call has `vcs.VCS_TIMEOUT_S` (10 s); `cat-file` batches are
2000 oids per process so each stays under it. A push costs two to four processes for
the ref and the chain, three for the scan, and per claiming commit a tree listing, a
diff and one `cat-file` per 2000 blobs read. A push that cannot be walked within those
bounds reads E07 or E30 and is refused with that word; the agent pushes in smaller
steps. When E07 repeats on a push nothing in it caused — git did not answer twice —
the server's keeper reads the hook's log; nothing landed either time.

## 11. The UI's file browser

Milestone 1's surface, fixed now so the hook's reads serve it. Every route is read-only
over `vcs` reads, and none computes a status: what a route says of a commit is what
`nopekit validate` would print for it, and a token is a baked one or none.

- `GET /r/<thing>/tree/<sha>[/<path>]` → `ls-tree -l` of that tree: mode, type, oid,
  size, name; the commit's kind and `Nope-Name`; whether its spec tree is on `spec`.
- `GET /r/<thing>/blob/<sha>/<path>` → a rendered view: text (`*.py`, `*.json`, `*.md`,
  `*.log`, `*.txt`, `*.jsonl`), images, audio and video players, `*.stl` and `*.glb`
  into the viewer; the content type from an extension table; HTML and SVG served as
  `text/plain`; `X-Content-Type-Options: nosniff`; a CSP with no inline script; a size
  cap at `MAX_BLOB_BYTES`.
- `GET /r/<thing>/raw/<sha>/<path>` → the blob's bytes, `Content-Disposition:
  attachment; filename="<thing>-<line>-<basename>"`, the same headers.
- `GET /r/<thing>/diff/<shaA>..<shaB>[/<path>]` → `diff-tree -r --no-renames` of the
  two trees: text diffs for text blobs, *changed* for binaries; *parts changed* = the
  `print-*.stl` blobs whose oid differs; the spec-tree diff (`claims/`,
  `requirements/`, `milestones/`, `spec/packs.json`) shown first.
- `GET /r/<thing>/history/<ref>[/<path>]` → the first-parent log of the ref, restricted
  to `path` when given: sha, kind, `Nope-Name`, `Nope-Trigger`, subject; working
  commits folded.
- `GET /r/<thing>/commit/<sha>` → the trailers as parsed, kind, parents, the spec tree
  id and its acceptance, the verdict and trace files it added, and the refusals and
  notes `nopekit validate` would print for it today.
- `GET /r/<thing>/lines` → every `cand/*` tip with its kind, name, acceptance, and
  whether the renderer has regenerated its state file (until milestone 1: *not yet
  rendered*, never a token).

## 12. Vocabulary

- **candidate.** `docs/GLOSSARY.md` §1: an alternative design with a disposition, and
  the design in the checkout is the current design (SPEC decision 15, *candidates are
  records*). Under Eddy a candidate is a commit with `Nope-Kind: candidate` on a
  `cand/<name>` line: the same object seen from git. The record form of decision 15,
  if it comes, is a file a candidate commit may hold (`candidates/<id>.json`), not a
  second notion.
- **line.** A `cand/<name>` ref: the first-parent chain of its commits. Fast-forward
  only, never deleted. Never *branch of candidates*, never *variant line*.
- **claiming commit.** A candidate or an evidence commit: one that carries `Nope-Kind`.
  A **working commit** carries no `Nope-*` key.
- **refuse.** The boundary's word, and the hook is a boundary: the hook *refuses* a
  push. An evaluator *fails* a claim. Neither word is used for the other. A note is
  printed and refuses nothing.
- **spec tree.** The derived tree of section 5; **accepted** means it is the tree of a
  commit on `spec`.
- **run by the agent.** What every token in a pushed tree is. Never *checked by
  Eddy*.

## 13. Limits: what the hook cannot see

Each limit is named here and in `docs/SPINE_CONTRACT.md`'s limits, and pinned by a
test where one can be written.

1. **A forged PASS at the honest rho.** Run `nopekit check` (a FAIL, never committed),
   copy its reads, flip the outcome, recompute the digest and the name, run
   `nopekit round` (the resolver serves the entry as Fresh, and the tier-0 sweep does
   not re-run a Fresh gate), push. The hook proves malformed, misnamed, wrong-digest,
   rho-inconsistent and tree-disagreeing entries; it cannot prove an outcome was
   produced by the tree (`reads.params`, `reads.model` and `code.digest` need the model
   and the gate modules executed). Regenerating the state file from the tree reproduces
   the forged tokens byte for byte, because regeneration never executes a gate. Closed
   only by `export`'s re-execution (invariant 12) and by a renderer that runs
   `nopekit check --force` at tier 0 in a sandbox; until then the UI labels every token
   *run by the agent*. Pinned by `test_what_the_hook_cannot_see`.
2. **A resealed round record with true digests** passes the hook: the seal and both
   digests hold. The renderer's whole-bytes regeneration catches it. Pinned by the same
   test.
3. **The sealed chains** (`results/`, `exports/`, `preferences/`) extended by a process
   that recomputes the seal pass the prefix rule and the reader — `SPINE_CONTRACT`'s
   existing limit. The append-only rule stops shortening and deletion, nothing more,
   until acts replace the chains. And nothing binds the pusher to the `who` of an
   entry: `NOPE_PUSHER` is reserved and recorded, with no rule.
4. **A project's own evaluators are not in the contract.** `gates/`, `selftest/`,
   `params/` and `model_entry` sit outside the four spec entries, so a candidate
   rewrites the gate that judges a claim, and its controls, without the spec moving;
   only forgeable control entries and invariant 9's local qualification stand between
   that and Checked. In milestone 0 only pack evaluators pinned by `spec/packs.json`
   are accepted evaluators; folding `gates/` and `selftest/` into `SPEC_ENTRIES` is a
   one-constant change, named under Later.
5. **Machine-dependence of the state file.** A solver present on the agent's machine
   and absent from the image makes regeneration read *behind (resolution)* (the
   committed motorbike record already does here without OpenFOAM). The renderer must
   mark, never refuse; the image must carry every bundled and pinned pack's
   `requires_tools`. Invariant 1 keeps its meaning on the server by design.
6. **Two outcomes at one rho across machines** (`read_entries` keeps this machine's by
   `instruments`) make the agent's and the render's resolution disagree on a Stale
   gate's served entry. The hook notes (N04), never refuses; the UI shows the
   contradiction, not either outcome.
7. **Secrets by content** under an innocent name pass: names only, `rounds.py`'s
   documented choice.
8. **Growth across pushes.** Nothing on a line is ever deleted; the per-object and
   per-push caps bound one push, not the repository. Eddy needs a per-repository quota;
   the hook does not provide one.
9. **Trace and amber files** are admitted by name shape and their reader's strict
   parse; their key needs the model, so a tree may carry any number of them under the
   file cap. Invariant 16's currency check keeps a stale one from drawing.
10. **Acts' own path** (`update-ref` inside the bare repository, bypassing the hook by
    construction) has no test in milestone 0; the first act is validated by nobody
    until milestone 1 gives that path its own hostile tests and the same trailer rules
    with `from_push=False`.
11. **A line that predates the hook** has unvalidated commits below its tip. A
    repository under Eddy is created under the hook, and a migrated one is pushed
    through it from empty.
12. **Trailer heuristics drift** between git versions surfaces as E16 refusals of
    honest commits until nopekit's raw-line scan follows — loud, not silent.
13. **A push of several refs** is all-or-nothing, and an object new to two refs is
    scanned twice; both are over-refusing or over-scanning, never an acceptance of
    something unscanned.
