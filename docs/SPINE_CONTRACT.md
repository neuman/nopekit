# Spine contract (internal)

Every spine module builds against `src/nopekit/models.py`. **Read that file first.**
It defines every type that crosses a module boundary; no module may define its own.

Hard rules for the whole spine:

1. **Standard library only.** No third-party imports anywhere under `src/nopekit/`.
   The spine must never be the reason an install fails. Packs declare their own deps.
2. **Python 3.10+ syntax** (`X | None`, `match` ok). Target 3.12.
3. **No `Date.now()`-style hidden state in logic** — callers pass timestamps in. A
   function that stamps its own time is untestable. Use `utcnow_iso()` from `util.py`
   only at the CLI edge.
4. Every public function gets a real docstring saying *why*, not just *what*.
5. Comments carry provenance: when a rule exists because
   something slipped through, say what slipped through.
6. `from __future__ import annotations` at the top of every module.
7. Errors the user caused raise `NopekitError` (from `util.py`); bugs raise normally.

## Where facts live

Each fact has one home, and every other place that shows it is an output of that
home (METHOD rule 1). The layout, the writer, the strict reader and the index are
`store.py`'s section below; the verdict cache is `verdicts.py`'s; what `check` writes
is `cli.py`'s.

| Fact | Home | Git | Written by |
|---|---|---|---|
| a claim, a decision, an enriched need, an input's record, a param's provenance, a declared view | `claims/`, `decisions/`, `needs/`, `inputs/<id>.json`, `params/`, `views/*.json` — one file per record | tracked | a human or the agent, editing the file; the shims `ingest`, `extract`, `decide` |
| project meta, the model entry, the live packs, the people a round may commit as (phase 6) | `.nopekit/project.json` | tracked | the same; `init` (`people` from git's identity, when the strict reader reads it back); `packs add` |
| a physical result, and an owner's or an authority's attribution (P2.5a) | `results/<claim-id>.json`, append-only, every entry sealed and chained | tracked | `claim physical`, typed in a person's own shell — never by hand |
| a parameter's value, units, rationale and what lost to it | the model (`model/*.py`: `Config`, its docstrings, `PARAMS`) | tracked | the model's author |
| a gate's verdict, and its control's | `.nopekit/verdicts/<gate id>/`, one file per rho, never rewritten | tracked | `check`, `gate selftest` |
| every record, in one read | `.nopekit/ledger.json` — the **index**, generated | ignored | every command but `doctor`, `init` and `--no-record` runs |
| statuses, counts, the worst claim, the parameter view, as the last full check saw them | `.nopekit/cache/last_check.json` | ignored | a full recorded `check` |
| what a run cost, when it ran | `.nopekit/obs/` | ignored | every recorded run of a gate or a control |
| a crash or a self-skip, remembered — never evidence | `.nopekit/cache/last_outcomes.json` | ignored | `check`, `gate selftest` |
| time savers: file digests, re-verified fixtures | `.nopekit/cache/digests.json`, `controls.json` | ignored | `check`; losing either costs a re-hash or a fixture run, never a verdict |
| gate scratch and evidence | `.nopekit/out/` (a control's: `out/controls/<gate id>/`) | ignored | gates |
| which bytes a recorded run cited as its evidence, per entry it wrote (issue #15) — never a verdict | `.nopekit/cache/evidence/<gate id>/<rho16>-<out8>.json` | ignored | a recorded `check` (and `export`'s re-runs), beside each entry it writes; never `--no-record`, a control or a mutation |
| a milestone: a named spend and the claims it requires (P2.5b) | `milestones/<name>.json` | tracked | a human or the agent, editing the file |
| a requirement: what the person asked for, in their words (architecture phase 1) | `requirements/<id>.json` (`R1`, `R2`, …); a claim names the ones it serves (`"requirements"`) | tracked | a human or the agent, editing the file |
| an export: what was required, checked, re-run, decided and built (P2.5b) | `exports/<name>.json`, append-only, sealed and chained | tracked | `export <milestone>` |
| a person's preference against a rejected alternative (architecture phase 2) | `preferences/<decision-id>.json`, append-only, sealed and chained | tracked | `prefer <decision>`, typed in the person's own shell — never by hand |
| a rejected alternative's type, its person, its return condition, its input (architecture phase 2) | `decisions/<id>.json`'s `rejected` | tracked | the agent, or `decide`'s flags |
| which proposer produced an input (architecture phase 2) | `inputs/<id>.json`'s `proposer` — its basis is looked up from the proposer, never stated | tracked | `propose`, `ingest --proposer` |
| the readiness report (P2.5b; was `docs/readiness.md`) | `REPORT.md` at the root | ignored | `report --write` |
| a milestone's package: what the spend gets (P2.5b) | `out/<milestone>/` at the root | ignored | `export <milestone>` |
| the round record: the resolution at the end of a round, as tokens, with the typed graph and the digests it was computed from (architecture phase 3a) | `.nopekit/round.json` — an output, never read for truth | tracked | `round` (`rounds.write`); from phase 4 `merge`; never `check` |
| the trace: which derived values each choice moved at ±10% (architecture phase 3b) | `.nopekit/trace/<k16>.json`, one per traced design, keyed by its instructions, choices and the data its build read, never rewritten | tracked | `model trace` (`derivation.run`); `round` only starts it |
| amber: each automated claim's evaluators on designs an assumption-sourced choice moved | `.nopekit/trace/<k16>.amber.<s16>.json`, one per evaluation, never rewritten | tracked | `model trace` |
| a trace's progress, resumable; a background trace's log; its lock | `.nopekit/cache/trace/<k16>/`, `.nopekit/out/trace.log`, `.nopekit/trace.lock` | ignored | `model trace` |
| a figure a bound viewgen drew from one verdict entry, with its sidecar (`<view>.view.json`: each file's sha256, the recipe digest, the caption, the colour scale) (issue #15) | `site/assets/figures/<gate id>/<rho16>-<out8>/` | tracked | `site build` (`site._bound_pass`), once the spine checked it |
| a bound figure's 3D file for an entry no export re-ran, and a bound viewgen's scratch (issue #15) | `site/assets/live/<gate id>/<entry>/`, `.nopekit/out/figures/<gate id>/<entry>/<view>/` | ignored | `site build`; the live file is named only in `site/data/build.json`'s `live_figures` |
| a detached live server: its pid, address and URL; the lock it holds for its life; its log (the acceptance run after phase 3b) | `.nopekit/out/serve.json`, `.nopekit/out/serve.lock`, `.nopekit/out/serve.log` | ignored | the server `site serve --detach` starts, which removes its record as it stops; `site serve --stop` removes a stale one |
| the spec's pinned packs: each pack's repository and commit (Eddy milestone 0; `docs/EDDY_CONTRACT.md`) | `spec/packs.json` — `{"schema": 1, "packs": {"<name>": {"repo", "commit"}}}`, read strictly by `receive.packs_problems`; NOT a record: no digest moves with it and the index never lists it; the fourth entry of the spec tree beside `claims/`, `requirements/` and `milestones/` | tracked | the agent, editing the file; the hook holds it to `.nopekit/project.json`'s `packs` at every push (E34, E35) |
| Eddy's pre-receive hook: `nopekit validate --stdin` with the quarantine handed over, a crash read as a refusal (Eddy milestone 0) | `server/pre-receive` in this repository; installed by Eddy's image as `<bare>/hooks/pre-receive`; writes nothing in the repository it judges; refuses any push without a quarantine but a delete-only one (git builds none when it unpacked nothing), which it judges against the main store with `GIT_DIR` set | tracked | nopekit's authors; never a project's |

The index and `last_check.json` are the whole project in two reads (D-06); neither
is read for truth by any spine code, and the index never holds a status, a coverage
or a verdict. There is no run history: git and the verdict cache are the history.

## Module map and public surface

Every `src/nopekit/*.py` except `__init__` and `__main__` has a heading below, and
every name in its `__all__` is written in code form in that module's section.
`tests/test_contracts.py` holds that (PLAN R-14): a module with no heading, or an
exported name nobody wrote down, turns it red. `models.py` and `site.py` had no
heading at all until it landed, and 42 exported names were in no section — a map of
a smaller spine than the one an agent was about to edit.

It holds the other direction too, from the Phase 1 commit (`DocumentedNamesExist`):
every call form written in a module's section — `store.load(root)`,
`ctx.param(...)` — names something that module, a class it defines, a spine
module or the builtins has (a parameter called by name, `before(spec, fn)`, counts);
every name a fenced line defines at column 0 (`def`, `class`, `CONSTANT =`) is a
binding of the module; every member a `class` block lists is a member of that class.
And `RemovedNamesAreGone` holds that no document an agent reads still names what
Phase 1 removed. During the phase the check ran one way, so a wave's doc owner could
document a surface before the unit that built it merged; what that left open was
this page describing a spine that no longer existed — it said the legacy save wrote
no sweep record by the removed record's own name, and explained bulk reads through a
recorder that had been deleted.

### `models.py`  (no deps — the type contract)
Every type that crosses a module boundary, and nothing else. Records round-trip
through plain JSON dicts (`Record.to_dict` / `Record.from_dict`) so the state on disk
stays diffable and an agent can read one record without loading the rest.
`from_dict` **drops unknown keys** rather than raising, so a newer writer cannot
break an older reader — which is exactly why a new not-pass state must also be
written into the legacy flags in the not-pass direction (PLAN R-2): a reader that
does not know the new key must still read the record as a non-pass.
```python
class StrEnum(str, enum.Enum)                 # serialises as its value on json.dumps
class ClaimKind(StrEnum): MEASURABLE | PHYSICAL | ASSUMPTION
class ClaimStatus(StrEnum): PASS | FAIL | STALE | UNCLAIMED | BLOCKED | PENDING
                            | UNVERIFIED | VERIFIED | REFUTED | ASSERTED   # derived, never stored
BLOCKING_STATUSES: frozenset[ClaimStatus]     # FAIL STALE UNCLAIMED BLOCKED PENDING REFUTED (P2.1: unchanged)
class Tier(enum.IntEnum): INSTANT = 0 | BUILD = 1 | SOLVE = 2 | EXTERNAL = 3
class PrerequisiteKind(StrEnum): ERRORED | FAILED | SKIPPED | UNQUALIFIED | NOT_REGISTERED
                                 | INVALIDATED | UNRUN   # why a prerequisite is not established, rank order
CONTEXT_OUTSIDE = "context:outside"           # P2.4: the Verdict.unqualified token kind of a
                                              #   pass outside its evaluator's operating context
class Terminal(StrEnum): CLOSED_FORM | SOLVER | DATASHEET | MEASUREMENT | HUMAN | NONE
                                              # P2.5a: where a claim's evidence bottoms out
TERMINALS_BY_KIND: dict[ClaimKind, frozenset[str]]   # measurable {closed_form, solver, datasheet}
                                              #   physical {measurement, human}; assumption {none, human}
LATENCY_UNITS: dict[str, float]               # P2.5b: s min h day week year -> seconds (year
                                              #   Julian, 365.25 d); Claim.expected_latency's units
class ViewKind(StrEnum): MODEL3D | IMAGE | CHART | TABLE | FIELD | DIAGRAM
class ArtifactKind(StrEnum): SKETCH | REFERENCE | CAD | SCREENSHOT | DATASHEET | SPEC
                             | MEASUREMENT | STANDARD | DATA | LINK | OTHER
EXT_KIND_HINTS: dict[str, ArtifactKind]       # ".stl" -> CAD; `nopekit ingest`'s first guess
class NeedStatus(StrEnum): OPEN | PROPOSED | DEFERRED | INSTALLING | SATISFIED | ABANDONED
class Comparator(StrEnum): LE | LT | GE | GT | EQ | NE | BETWEEN
    def holds(self, measured, limit, limit_hi=None) -> bool
BASES = ("established", "sourced", "model-only")   # phase 2 (SPEC 38): where an evaluator's
                                              # or a proposer's verdict logic comes from
BASIS_UNDECLARED = "undeclared"               # the token and the word with none declared
REJECTION_TYPES = ("infeasible", "dominated", "preferred_against", "unevaluated")  # SPEC 14
REJECTION_UNTYPED = "untyped"                 # a rejection written before phase 2
RETURN_QUANTITIES = ("margin", "value", "limit")   # what a return condition reads
RETURN_COMPARATORS = ("<", "<=", ">", ">=")   # no ==/!= (never holds on a measured float)
class Record:                                 # mixin: the symmetric dict round trip
    def to_dict(self) -> dict
    @classmethod
    def from_dict(cls, data) -> Record        # unknown keys DROPPED (R-2 above)
def slugify(text, *, maxlen=48) -> str        # kebab id, filesystem-safe; callers dedupe
def sha256_file(path, *, chunk=1 << 20) -> str
```
The records, every field. `tests/test_contracts.py` reads each `class` block below and
fails when a record kind gains a field that is not in its own block — a field named
`id` or `note` appears somewhere in every document, so "somewhere" proves nothing.
```python
class Rejected(Record):         # an alternative that LOST: the highest-value field there is
    value: str; why: str; evidence: str = ""        # PARAMS and params/ keep this, text only
class ReturnCondition(Record):  # phase 2 (SPEC §0.2): what would bring a loser back
    claim: str = ""; quantity: str = ""; comparator: str = ""; value: float | None = None
    text: str = ""                  # {"text"} alone: shown, never evaluated
    def form(self) -> dict          # the members not at their default: digest, JSON, display
class Rejection(Rejected):      # phase 2 (SPEC 14): a decision's rejected alternative
    type: str = ""                  # REJECTION_TYPES; "" a legacy entry, read untyped
    by: str = ""                    # preferred_against only: a NOMINEE, like Claim.owner
    returns_if: ReturnCondition | None = None
    input: str = ""                 # an input id, when an input is its source
class Param(Record):            # one model parameter with its full provenance
    name: str; value: Any; units: str = ""; rationale: str = ""
    rejected: list[Rejected]; source: str = ""; grounded_by: list[str]   # InputArtifact ids, or
                                 # an assumption claim's id (phase 3a: the value IS it)
    gates: list[str]; derived_from: list[str]; changed_in: str = ""; tags: list[str]
    is_derived -> bool                                                   # property
class Acceptance(Record):       # the machine-checkable threshold behind a claim
    quantity: str = ""; comparator: Comparator = LE; limit: float | None = None
    limit_hi: float | None = None                                        # BETWEEN only
    units: str = ""
    def holds(self, measured) -> bool;  def render(self) -> str
class PhysicalResult(Record):   # a physical result: one entry of results/<id>.json's "results"
    passed: bool; when: str = ""; who: str = ""; detail: str = ""; evidence: list[str]
    # P2.5a, each LAST and empty by default (a legacy entry reads with them empty):
    channel: str = ""               # interactive | non-interactive | agent-session <id> | "" legacy
    authority: str = ""             # an expert judgment: the authority it was recorded for
    measured: float | None = None; units: str = ""      # --measured, in the claim's units
    article: dict                   # verdicts.article_of: {source, hash, built_from, revision, dirty}
    claim_digest: str = ""          # claims.claim_digest: the claim as the person read it
    rho: str = ""                   # seal of (article hash, claim digest)
    evidence_sha256: dict           # {path: sha256} of each evidence file when recorded
    contradicts: list               # [{gate, code, rho, value, units, inside}] (a fail's)
    contradiction_check: str = ""   # why no contradiction could be judged
    prev: str = ""; digest: str = ""   # the chain and the seal (store.append_signed)
class AttributionRecord(Record):   # P2.5a: one entry of results/<id>.json's "attributions"
    role: str; name: str            # owner | authority; the nominee the claim named
    reason: str = ""; claim_digest: str = ""; who: str = ""; when: str = ""
    channel: str = ""; prev: str = ""; digest: str = ""
class EntryStanding:            # frozen, in memory: the judge's facts about one result entry
    index: int; passed: bool; counts: bool = False; why: str = ""; article: str = ""
    article_state: str = ""; moved: tuple = ()
    stands: bool = False            # P2.5b: a person's pass still standing on its own
                                    #   article, counted or not (what may supersede a fail)
    built: tuple = ()               # review of P2.5b, LAST: the seal of the generator's files
                                    #   of each held export of this article (what was printed)
class Standing:                 # frozen, in memory: what a claim's results stand for NOW
    state: str = ""; counted: int | None = None; article: str = ""; moved: tuple = ()
    entries: tuple = ()
    fail: int | None = None         # P2.5b: the newest fail no later pass supersedes
    superseded: tuple = ()          # P2.5b, LAST: the fails a pass on another exported
                                    #   article superseded (verdicts._supersedes)
class Claim(Record):            # something that must be true for the design to work
    id: str; statement: str; kind: ClaimKind = MEASURABLE; acceptance: Acceptance
    rationale: str = ""; source: str = ""; grounded_by: list[str]; gates: list[str]
    tags: list[str]; critical: bool = True; physical_result: PhysicalResult | None = None
    note: str = ""                    # on disk mechanism.how (phase 1); "note" the older key
    owner: str = ""                   # P2.1: a NOMINEE for an assumption, never an attribution
    fallback: str = ""                # P2.4: why it may be carried outside an operating
                                      #   context; counts only through an attribution
    terminal: str = ""                # P2.5a: declared Terminal, "" = by kind; strict reader;
                                      #   on disk mechanism.by (phase 1), "terminal" the older key
    authority: str = ""               # P2.5a: an expert judgment's NOMINEE (terminal human)
    expected_latency: dict            # P2.5b: {value > 0, units in LATENCY_UNITS} or {}; a
                                      #   measurement-terminal claim's alone (strict reader)
    evaluators: list[str]             # phase 1: on disk mechanism.evaluators only — a
                                      #   cross-check, never coverage (S-37); not sealed
    requirements: list[str]           # phase 1: the requirement ids it serves; not sealed
    resolves: list[str]               # phase 3a: a PHYSICAL claim's — the assumption ids its
                                      #   test replaces (SPEC 19); a display edge, moves no
                                      #   status, not sealed; refused on another kind, on
                                      #   itself, on a non-id or a repeat
    results: tuple = ()               # in memory: every entry of results/<id>.json, oldest first
    attributions: tuple = ()          # in memory: interactive AttributionRecords, newest first
    standing: Standing | None = None  # in memory: set by verdicts.view only
class Need(Record):             # a claim with no gate: the extension protocol's trigger
    id: str; claim_ids: list[str]; quantity: str = ""; claim_class: str = ""
    status: NeedStatus = OPEN; candidates: list[ToolCandidate]; chosen: str = ""
    note: str = ""
class ToolCandidate(Record):    # one option for closing a Need
    name: str; kind: str = ""; why: str = ""; cost: str = ""; install: str = ""
    licence: str = ""; pack_would_be: str = ""
class Extraction(Record):       # what was read OUT of an artifact; unread evidence is decoration
    what: str; grounds: list[str]; confidence: str = "stated"; note: str = ""
class InputArtifact(Record):    # a piece of evidence the user supplied
    id: str; path: str = ""; url: str = ""; kind: ArtifactKind = OTHER
    description: str = ""; sha256: str = ""; bytes: int = 0; added: str = ""
    extractions: list[Extraction]; licence: str = ""; note: str = ""
    proposer: str = ""              # phase 2 (SPEC 22): the proposer that produced it, on a
                                    #   NEW record only; its basis is looked up, never stated
    extracted -> bool                                                    # property
class Locator(Record):          # WHERE a verdict applies (docs/SITE_CONTRACT.md)
    view: str; target: str = ""; kind: str = "part"; label: str = ""; severity: str = ""
    position: list[float] | None = None; value: float | None = None
class View(Record):             # one thing the site renders and verdicts address
    id: str; kind: ViewKind = IMAGE; title: str = ""; src: str = ""; description: str = ""
    gates: list[str]; data: dict; meta: dict; pack: str = ""; order: int = 100
class Decision(Record):         # one decision-log entry; names what LOST
    id: str; title: str = ""; when: str = ""; summary: str = ""
    rejected: list[Rejection]       # phase 2: typed; to_dict writes the new keys only when set
    params_changed: list[str]; claims_changed: list[str]; evidence: list[str]
    body: str = ""                                                       # long-form markdown
    preferences: tuple = ()         # in memory: interactive PreferenceRecords, newest first
class PreferenceRecord(Record): # phase 2 (invariant 13): one entry of preferences/<id>.json
    decision: str                   # the file's stem (phase 4 adds candidate)
    value: str = ""; name: str = "" # the alternative, and the nominee "by" named, as read
    rejection_digest: str = ""      # claims.rejection_digest: the rejection as read
    who: str = ""; when: str = ""; channel: str = ""   # git's identity, the clock, the channel
    prev: str = ""; digest: str = ""   # the chain and the seal (store.append_sealed)
class Milestone(Record):        # P2.5b: milestones/<name>.json — a named spend; the stem IS the id
    id: str; description: str = ""; requires: list[str]   # the claim ids it needs Checked
    generator: str = ""             # "<path>.py:<function>": what export builds the article with
class ExportRecord(Record):     # P2.5b: one entry of exports/<milestone>.json's "exports"
    milestone: str; when: str = ""; who: str = ""; channel: str = ""; revision: str = ""
    dirty: bool = False; requires: list   # the milestone's ids at export
    claims: dict                    # {id: {status, cause, reran}} — every claim, at export
    reran: list                     # [{gate, rho, out8, code, outcome, qualified}]
    counted: dict                   # {required id: [counting covering verdict rows]} (D13)
    article: dict                   # verdicts.export_article's: {source "export", hash, traced,
                                    #   milestone, when, revision, dirty, built_from}
    package: dict                   # {hash, files: {rel: sha256}, manifest: sha256}
    proceed: Any = None             # None, or {claims: [{id, status, cause}], why} (D8)
    prev: str = ""; digest: str = ""   # the chain and the seal (store.append_sealed)
class Requirement(Record):      # phase 1: requirements/<id>.json — the person's words
    id: str                         # its stem, store.REQUIREMENT_ID (R1, R2, …)
    statement: str = ""             # the words, verbatim; required, non-blank
    source: str = ""                # "interview", or an input id
GROUP_KINDS = ("board", "zone", "station")   # phase 5 (SPEC 17)
class Group(Record):            # phase 5: groups/<id>.json — a board, a zone or a station
    id: str                         # its stem, store.GROUP_ID; names site/content/<id>.md
    kind: str = ""                  # GROUP_KINDS; required
    title: str = ""                 # the words the page shows; required
    line: str = ""                  # one short sentence under the title
    members: list[str]              # claim ids (board), choices (zone), derived values
                                    #   (station), in the order the page draws them
    order: int = 0                  # groups of one kind sort by (order, id)
class PackManifest(Record):     # pack.json, tier 1 (docs/PACK_FORMAT.md)
    name: str; version: str = "0.1.0"; description: str = ""; settles: list[str]
    claim_classes: list[str]; provides_gates: list[str]; provides_views: list[str]
    provides_generators: list[str]; key_scope: str = ""; requires_tools: list[str]
    requires_python: list[str]; max_tier: Tier = INSTANT; lenses: list[str]
    authors: list[str]; licence: str = "Apache-2.0"; homepage: str = ""; origin: str = ""
    basis: str = ""                 # phase 2: its evaluators' default basis (PACK_BASIS)
    provides_proposers: list        # phase 2: [{id, basis, description, run}], raw (packs)
class KeyCollision(Record):     # one projection key two installed packs read differently
    key: str; packs: list[str]; meanings: dict[str, str]; scoped: dict[str, str]
    primary: dict[str, str]; live: bool = False
    def fix(self) -> str                                 # the one sentence that resolves it
class Person(Record):           # phase 6 (SPEC §0.3): one person a round may commit as
    name: str = ""; email: str = ""   # as `git config user.name` / `user.email` print them
class ProjectMeta(Record):
    name: str = ""; summary: str = ""; created: str = ""; revision: str = "v0.1"
    model_entry: str = ""; packs: list[str]; spine_version: str = ""
    people: list[Person]         # phase 6: LAST; absent reads []; never baked into the
                                 #   page (site._UNPUBLISHED_META); read whole by a gate with
                                 #   `meta`, which no bundled or bracket gate reads
class Ledger(Record):           # the whole project, in memory (store.load assembles it)
    meta: ProjectMeta; claims: list[Claim]; params: list[Param]
    inputs: list[InputArtifact]; needs: list[Need]; decisions: list[Decision]
    verdicts: list[Verdict]                                              # latest per gate
    views: list[View]
    removed: tuple[Claim, ...]   # review of P2.5a, in memory only: a results file no claim
                                 # file holds, as a claim (to_dict drops it; view composes it)
    milestones: list[Milestone]  # P2.5b
    exports: list[ExportRecord]  # P2.5b: every milestone's, file by file, oldest first
    requirements: list[Requirement]  # phase 1: natural id order; read whole by a gate, traced
    groups: list[Group]          # phase 5: natural id order; hidden from every gate
                                 #   (verdicts._LEDGER_HIDDEN): a regroup re-keys nothing
    def claim(cid) | param(name) | artifact(aid) | need(nid) | verdict(gate_id)
        | milestone(name) | requirement(rid) | group(gid) -> record | None
    def verdicts_for(cid) -> list[Verdict];  def upsert_verdict(verdict) -> None
```
**Records as files** (checkpoint 1.3). Three constants tell `store` how a record
becomes a file and back; they live here because they are facts about the record
types, not about the disk:
```python
RECORD_KINDS: dict[str, type]    # record dir -> kind: claims Claim, params Param,
                                 # decisions Decision, needs Need, inputs InputArtifact,
                                 # results PhysicalResult, views View, milestones Milestone,
                                 # exports ExportRecord, requirements Requirement,
                                 # preferences PreferenceRecord (store.RECORD_DIRS' order)
FORBIDDEN_KEYS: dict[str, dict[str, str]]   # class name -> {key: the home that owns it};
                                 # "*" applies at every depth of every kind
ALWAYS_WRITTEN: frozenset[tuple[str, str]]  # {("Claim", "kind"), ("Acceptance", "comparator")}
CLAIM_MECHANISM: tuple[tuple[str, str], ...]  # phase 1: (("by", "terminal"), ("how", "note"),
                                 # ("evaluators", "evaluators")) — the file's "mechanism"
                                 # member -> the flat in-memory field that holds it
```
`FORBIDDEN_KEYS` is what a record FILE may never carry, each with its owner named in the
refusal: `Claim.gates` (derived from gate coverage), `Claim.physical_result` (its home is
`results/<id>.json`), `Param.value` and `Param.derived_from` (the model — `{model}` is
filled with `meta.model_entry`), `Param.gates` and `Param.changed_in` (derived),
`InputArtifact.bytes` (computed from the file), `Requirement.status` (derived on every
read, `claims.requirement_statuses`) and `Requirement.claims` (a claim names the
requirements it serves), `Claim.rests_on` (computed by the graph from what its
evaluators read, `graph.build`), `Param.read_by` and `Param.sourced_from` (the observed
view, `graph.observe` — phase 3a), and `"*"`: `independence` anywhere —
derived from origin, never a field the proposer fills in. The **dataclass fields stay**:
`claims.resolve_status` still reads `Claim.gates` in memory, `Ledger.verdicts` is still
filled by readers, and `Ledger.to_dict()` still emits `verdicts` (tests:H4); only the
on-disk homes go. `Record.from_dict` stays lenient (unknown keys dropped) for dicts in
memory; the reader of a record file is `store.read_record`, which is strict — a dropped
`rejectd` was erased by the next save (S-40). `ALWAYS_WRITTEN` is what the writer keeps
at its default: a claim file without its `kind`, or a limit without its direction, is
unreadable to the human editing it.

The ledger keeps **no run record** (checkpoint 1.2). It used to carry the previous
sweep's model and inputs hashes, and staleness was one comparison against them: a model
that failed to import compared equal and three claims read PROVEN (S-21), and one
unread datasheet staled every measurable claim (S-33). Staleness is per gate now
(`verdicts.freshness`); when the last full check ran is `.nopekit/cache/last_check.json`.
`Ledger.from_dict` ignores the old key, so a ledger an older spine wrote still loads
(R-2), and the next save does not carry it.

`Verdict`, `NegativeControl` and `GateSpec` are records too. Their fields and
properties are listed once, in `docs/PACK_FORMAT.md` ("The surface a gate sees"),
because a pack author is the reader who sets them; two copies of one field list is
one copy that goes stale. `Verdict.outcome` is the single derivation of what a
verdict says — `"error"` if `error`, else `"skipped"` if `skipped`, else `"pass"` if
`passed is True`, else `"fail"` — and `ok`, `render()` and `claims.resolve_status`
all read it; every new consumer (JUnit, the index, the page) calls it and never
re-derives one (PLAN R-5). `GateSpec.needs` is the last field (P2.2, after
`requires_one_of`); `Verdict.blocked_by` and `Verdict.blocked_kind` are Verdict's last
two (P2.2, below), after `Verdict.rho`, `Verdict.cpu_s` and `Verdict.unqualified`
(R-2): the content address the sweep keys it by (a gate never sets it), the CPU
seconds it cost, children included, and — from P2.1 — the refusal's reason when the
evaluator is not admitted at its version. `unqualified` is the spine's mark alone:
`verdicts._unqualified` sets it (with `error="not admitted: <reason>"`), `run_gate`
clears it on whatever a gate returns, no stored verdict carries it (an entry is built
from an explicit field list, and `verdicts.remembered` drops it), and
`Verdict.__post_init__` writes `error` when it is set without one, so a marked verdict
is never ok. `claims.compose` reads a claim with a marked evaluator as Gap; the text
alone reads as a crash (degrade-closed). `Verdict.render()`'s body follows the
outcome: an error's first line, a skip's reason, a pass's or a fail's detail — never a
crash's traceback (P2.0 F-1), never a crash in its skip reason's words (F-10).

**P2.4's fields.** `Verdict.comparator` and `Verdict.settles` are Verdict's last two:
which side of its limit the gate passes (the gate's own, one of `<= < >= > == !=
between`, normalised and refused otherwise by `gates._stamp`) and the quantity it
measured (stamped from `GateSpec.settles` by the spine, never the gate's). Both are in
an entry's verdict block, so a cached verdict compares (`claims.cross_check`) and draws
(`claims.margin`) exactly as the run that wrote it; neither is in `out8`. An older spine
drops both and compares nothing: quieter, never a pass. `GateSpec.operating_context`
is GateSpec's last field: `{key: (lo, hi)}`, refused at registration when malformed,
not part of rho. `Claim.fallback` is Claim's last: the reason an owner attributes for a
claim carried outside an operating context (`claims.assumption_reason`). A
`context:outside` token on anything that is not a pass is dropped by
`Verdict.__post_init__` before it writes `error` (D16): the mark never launders a fail.

**The prerequisite mark (P2.2-D9).** `Verdict.blocked_by` — the roots, found
transitively, of the prerequisites that were not established when a gate was not run
— and `Verdict.blocked_kind`, the first root's `PrerequisiteKind` (`errored`,
`failed`, `skipped`, `unqualified`, `not-registered`). The spine's alone, on
`unqualified`'s terms: `gates.blocked` is the one producer, `run_gate` clears both on
whatever a gate returns (`_stamp`), no entry stores them (the whitelist), no remembered
outcome carries them (`verdicts._NEVER_REMEMBERED`, on read and on write). Non-empty,
`blocked_by` makes `__post_init__` write `skipped=True, passed=False` (R-2: an older
spine that drops both keys still reads a skip). `blocked_kind == "errored"` is what
makes a claim behind a crashed prerequisite read errored (`claims.compose`): a guard is
bound to its own claims, so nothing else carries its crash to a claim bound only to
the dependent. `GateSpec.needs` names the prerequisites — exact gate ids, refused at
registration when malformed, cyclic or tier-inverted — and is not part of rho
(`verdicts.SPEC_FIELDS_IN_RHO`, D-04).

**`ClaimStatus` from P2.1 is a reading of GLOSSARY §3** (Table 1's seven words plus
Open). The ten members and their values stay — JSON values move only in the rename
pass (GLOSSARY §7) — and each now holds the fact in this table; the word is
`report.HUMAN`'s, the token `claims.STATUS_KEY`'s:

| enum | word (token) | holds, from P2.1 |
|---|---|---|
| `pass`, `verified` | Checked (`checked`) | every evaluator ran, passed, current; `verified`: a physical pass recorded, bound to no article yet |
| `fail`, `refuted` | Failing (`failing`) | an evaluator not unqualified failed; `refuted`: a physical fail recorded |
| `stale` | Stale (`stale`) | a pass whose read set moved, or whose control is undemonstrated |
| `asserted` | Assumed (`assumed`) | an assumption with a reason and an owner the signing channel attributed (none in P2.1) |
| `unverified` | Pending build (`pending_build`) | a physical claim with no result, every automated evaluator passing and current |
| `unclaimed` | Gap (`gap`) | no evaluator; an unqualified one, beside a pass or not; an assumption with no attributed owner or no reason |
| `blocked` | Skipped (`skipped`) | an evaluator skipped **or errored**, or was not run because a prerequisite is not established (P2.2), none failed, beside a pass or not |
| `pending` | Open (`open`) | an evaluator unrun on the current inputs, nothing above applying |

### `util.py`  (no deps)
```python
class NopekitError(Exception): ...          # user-facing, CLI prints message not traceback
def utcnow_iso() -> str                      # "2026-09-11T14:46:00Z"
def ensure_dir(path) -> str                  # mkdir -p; returns the path as a str
def atomic_write_text(path, text) -> None    # write temp + os.replace; never truncate on crash
def atomic_write_json(path, obj) -> None     # sorted keys, indent=2, trailing newline; NaN refused
def write_json_if_changed(path, obj) -> bool # phase 5: atomic_write_json's bytes, only when
                                             #   they differ (a tracked baked file)
def read_json(path, default=None) -> Any
def sha256_text(text: str) -> str
def canonical_json(value) -> str             # P2.5a: THE canonical form: sorted, compact, no NaN
def seal(form) -> str                        # sha256 of canonical_json(form): a result's seal
def short_hash(text: str, n: int = 12) -> str
def printable(text) -> str           # review of P2.5a: one line, every control escaped as \xNN
def human_bytes(n: int) -> str
def human_duration(seconds: float) -> str
def rel(path, root) -> str                   # repo-relative posix path, for stable records
def iter_suffix_unique(base, taken) -> str   # base, else base-2, base-3 ... first not taken
class FileLock:                              # build lock; a concurrent run once overwrote a
    def __init__(self, path: str, *, stale_after: float = 3600.0)   # concurrent run
    def __enter__(self) / __exit__(...)      # writes pid; reaps stale locks of dead pids
```
`atomic_write_json` writes **strict JSON**: `allow_nan=False`, and a NaN or ±Infinity
anywhere in `obj` raises `NopekitError("<path>: refusing to write NaN or Infinity; a
number that could not be measured is not a number")`. What slipped through: the
json default let NaN reach `state.json` and `ledger.json`, `JSON.parse` rejected the
file, and the page advised `site build`, which cannot fix it (S-47). Every writer
above it — the ledger, the page's state, every Phase 1 cache and index file — inherits
the refusal.

```python
class FileDigests:                                   # sha256 of a file's BYTES, behind a stat cache
    def __init__(self, cache_path: str | None = None)   # .nopekit/cache/digests.json (untracked)
    def digest(self, path) -> str | None             # None: missing, not a regular file, unreadable
    def save(self) -> bool                           # best-effort; True when written
```
**Digests come from bytes.** `FileDigests` is how every Phase 1.2 digest of a file is
taken — a gate's opened files, the selftest walk, the verdict cache. The cache key is
`(size, mtime_ns, ctime_ns, ino, dev)`; an entry whose `max(mtime_ns, ctime_ns) >=
written_ns` — the cache file's own mtime after its last write — is re-hashed (git's
racy-clean rule, against its index mtime; no fixed window). ctime counts as well as
mtime because an edit whose mtime was restored to the past, landing in the tick the
cache was written, keeps an old mtime and a ctime in that tick. A racy entry that this
process never re-hashed is dropped on `save`, not written back under a newer write time
(which would launder it); nothing hashed before the first write of a cache file is
trusted until that write. A FIFO or directory is never opened. What slipped through before it:
`inputs_hash` hashed digests stored at ingest, never the bytes, so a limit file edited
under a project gate left the claim reading pass (S-22, S-45). *Rejected:* size and
mtime alone (a same-size edit with its mtime restored — `os.utime` cannot restore
ctime); a fixed 2 s window (misattributed to git in an earlier draft).

### `vcs.py`  (no deps)
The **only** git edge. Nothing else under `src/` starts a `git` process
(`test_meta.NoGitOutsideVcs`).
```python
VCS_TIMEOUT_S = 10
def is_repo(path) -> bool
def git_head(root) -> str | None                     # HEAD's sha; None outside git
def ls_files(root, relpaths, *, others=True) -> list[str] | None   # -z; relative to root
def ignored(root, relpaths) -> list[str] | None      # phase 5: untracked files git ignores
def ident(root) -> str | None                        # author identity, timestamp dropped
def committer_ident(root) -> str | None              # phase 6 review: the committer's
def in_repository(path) -> tuple[bool | None, str]   # phase 6 review: True; False only on
                                                     #   git's "not a git repository" with no
                                                     #   .git above; None, why: no answer
def show(root, relpath, rev="HEAD") -> bytes | None  # P2.5a: the bytes a commit holds at relpath
def history(root, relpath, limit) -> list[str]      # P2.5a review: commits that changed it, newest first
def model_dirty(root, relpaths) -> bool | None       # P2.5a: any of them differs from HEAD
def commit_times(root, relpaths) -> dict[str, str]   # path -> its last commit's time
# the writing half (architecture phase 6): round --commit, init's repository
VCS_WRITE_TIMEOUT_S = 120
class Wrote(NamedTuple): ok; oid; why    # why: "" | "no-git" | "timeout" |
                                         #   "inside a repository" | "git did not answer:
                                         #   <why>" | "refused: <git's line>"
def init_repo(root) -> Wrote                     # git init -q; only on in_repository's False
def changed_paths(root) -> list[str] | None      # read: diff, diff --cached, ls-files --others
                                                 #   --exclude-standard, each over "." only;
                                                 #   an untracked nested repository keeps "/"
def staged_paths(root) -> list[str] | None       # read: diff --cached --relative -- .
def operation(root) -> str | None                # read: "" | merge | rebase | cherry-pick |
                                                 #   revert | bisect | detached; None outside git
def add(root, relpaths) -> Wrote                 # --literal-pathspecs add --all, paths on stdin
def commit(root, relpaths, subject, body) -> Wrote   # -c user.useConfigOnly=true commit --only,
                                                 #   paths on stdin; oid = HEAD after
# reading a push (Eddy milestone 0): nopekit validate and the pre-receive hook. `repo` may be bare.
class ObjectDirs(NamedTuple): directory: str; alternates: str = ""   # GIT_OBJECT_DIRECTORY (the quarantine) and
                                                 #   GIT_ALTERNATE_OBJECT_DIRECTORIES (the main store), as the hook got them
class TreeEntry(NamedTuple): mode: str; type: str; oid: str; size: int | None; path: str   # ls-tree -l; size None for tree/commit
class Change(NamedTuple): status: str; path: str; old_mode: str; new_mode: str; old_oid: str; new_oid: str   # diff-tree: A M D T
class CommitObject(NamedTuple): sha: str; tree: str; parents: tuple[str, ...]; message: bytes   # raw bytes after the first blank line
def object_format(repo, *, objects=None) -> str | None          # rev-parse --show-object-format: "sha1" | "sha256"
def commit_exists(repo, sha, *, objects=None) -> bool | None    # rev-parse --verify --quiet <sha>^{commit}: True on exit 0
                                                 #   with the sha echoed back; False on exit 1; else None
def first_parents(repo, tip, *, limit, objects=None) -> list[str] | None   # rev-list --first-parent --max-count=<limit+1> <tip>:
                                                 #   newest first, tip at row 0; len > limit reads "over the bound"
def line_commits(repo, tips, *, limit, objects=None) -> list[str] | None   # the union of every tip's first-parent chain,
                                                 #   at most limit; [] for no tips, no process
def ref_tips(repo, prefix, *, objects=None) -> dict[str, str] | None   # for-each-ref %(refname)%00%(objectname) <prefix>;
                                                 #   {} for none; None for a prefix that reads as an option
def ref_exists(repo, ref) -> bool | None         # show-ref --verify --quiet <ref>: exit 0 / 1 / else None
def ref_trees(repo, ref, *, limit, objects=None) -> list[str] | None   # log --first-parent --format=%T --max-count=<limit> <ref> --;
                                                 #   [] when ref_exists is False (an absent spec is a start)
def commit_objects(repo, shas, *, objects=None) -> dict[str, CommitObject] | None   # cat-file --batch, shas on stdin; a
                                                 #   missing line or a non-commit makes the whole answer None; {} for none
def trailer_lines(repo, shas, *, objects=None) -> dict[str, list[tuple[str, str]]] | None   # log --no-walk=unsorted --stdin
                                                 #   --format=%x01%H%x0a%(trailers:only,unfold)%x02: git's own reading of each
                                                 #   block, each line split at its first ":" and stripped; [] for no block;
                                                 #   None when a block does not parse
def tree_of(repo, sha, *, objects=None) -> str | None           # rev-parse --verify --quiet <sha>^{tree}
def tree_entries(repo, tree, *, objects=None) -> list[TreeEntry] | None   # ls-tree -z -l <tree>: the top level
def tree_listing(repo, tree, *, objects=None) -> list[TreeEntry] | None   # ls-tree -r -z -l <tree>: every blob, symlink, gitlink
def tree_diff(repo, a, b, *, objects=None) -> list[Change] | None   # diff-tree -r -z --no-renames <a> <b>; a may be the empty tree
def blobs(repo, oids, *, objects=None) -> dict[str, bytes] | None   # cat-file --batch; a missing line or a non-blob -> None
def object_sizes(repo, oids, *, objects=None) -> dict[str, tuple[str, int]] | None   # cat-file --batch-check=%(objectname)
                                                 #   %(objecttype) %(objectsize); a missing line is left out (the caller names it)
def new_objects(repo, new, *, exclude, objects=None) -> list[tuple[str, str]] | None   # rev-list --objects <new> --not <exclude>...:
                                                 #   (oid, the name rev-list printed or ""), every object reachable from new
                                                 #   and from none of exclude
def tree_objects(repo, oids, *, objects=None) -> dict[str, list[tuple[str, str, str]]] | None   # cat-file --batch over trees,
                                                 #   each parsed here as <mode> <name>\0<raw oid> -> (mode, name, oid hex);
                                                 #   the mode as the tree stores it (40000, not 040000)
def empty_tree(repo) -> str | None               # hash-object -t tree --stdin fed b"" (no -w: nothing written)
```
Argv form with `-C root`, never a shell string. The environment strips `GIT_DIR`,
`GIT_WORK_TREE`, `GIT_INDEX_FILE`, `GIT_OBJECT_DIRECTORY`,
`GIT_ALTERNATE_OBJECT_DIRECTORIES`, `GIT_COMMON_DIR`, `GIT_NAMESPACE`,
`GIT_CEILING_DIRECTORIES` and `GIT_CONFIG_PARAMETERS` — a git hook exports them, and
every call would then answer for the OUTER repository — plus the rest of what the
installed git prints for `git rev-parse --local-env-vars` (`GIT_CONFIG_COUNT`,
`GIT_CONFIG`, `GIT_SHALLOW_FILE`, …) and `GIT_QUARANTINE_PATH`; `vcs._STRIPPED` is the
list, and a test holds it against the installed git's. It does not strip every `GIT_*`:
the user's `GIT_CONFIG_GLOBAL` and `GIT_AUTHOR_NAME` must still reach git. It sets
`GIT_OPTIONAL_LOCKS=0`, `GIT_TERMINAL_PROMPT=0` and `LC_ALL=C`. `ls_files` with
`others` is `--cached --others --exclude-standard`: tracked plus untracked-not-ignored.
`ident` is `git -c user.useConfigOnly=true var GIT_AUTHOR_IDENT`, `committer_ident` the
same for `GIT_COMMITTER_IDENT`. `in_repository` is the one read that tells "no" from "no
answer": `is_repo` reads every failure as `False`, and `init` that trusted it started a
nested repository inside one git refused to read ("dubious ownership") or did not
answer in time (review of phase 6). **Every function
returns `None` (or `{}`) on any failure and never raises**: a project outside git, a
missing `git`, a timeout. `VCS_TIMEOUT_S` is 10 because `git ls-files` on a cold, large
repository takes seconds; *rejected:* unbounded (a hung credential helper hangs
`check`) and 1–2 s (network filesystems). A `--dir` copy with no `.git` is the
common case (verify.sh), so every caller has a non-git path.

**The writing half** (architecture phase 6) keeps the read half's rules — argv form,
`-C root`, the same environment — and adds its own contract. Every `add` and `commit`
takes explicit literal paths relative to `root`, on stdin
(`--pathspec-from-file=- --pathspec-file-nul`, under `--literal-pathspecs`): never
`-A` over the repository, never a directory pathspec. `commit` is `--only`, so what the
person staged outside the list stays staged and uncommitted. It never pushes (push,
merge and tag come with phase 4, under this same section), never writes config (the
only `-c` is `user.useConfigOnly=true` on the commit, so git never invents an identity),
and never bypasses a hook (no `--no-verify`, no `core.hooksPath`: a refusing hook is an
answer, quoted in `why`). `GIT_EDITOR=:` so no hook opens an editor. A call that
outlives `VCS_WRITE_TIMEOUT_S` is sent SIGTERM, then SIGKILL after 5 s: git removes its
`index.lock` on SIGTERM. It never raises; `why` is the one place git's prose comes
back — its first stderr line, shown to a person, never parsed. `VCS_WRITE_TIMEOUT_S` is
120 because the Stop hook's budget is 300 s and an ordinary commit hook takes tens of
seconds; *rejected:* 10 s (the read timeout: an ordinary pre-commit hook exceeds it)
and 290 s (a hung gpg pinentry would spend the hook's budget, and Claude Code would kill
it mid-commit, leaving `index.lock`). `changed_paths` asks `diff` rather than
`ls-files --modified`, which names a file whose mtime alone moved, and never
`status --porcelain`, which prints paths relative to the repository's top level.
It keeps the trailing `/` with which `ls-files --others` names an untracked nested
repository, so `rounds.stage_plan` names it and never stages it: stripped,
`generators/cadlib` read as a file and a round committed a gitlink (review of phase 6).

**Reading a push** (Eddy milestone 0). `nopekit validate` and the pre-receive hook
(`receive.py`) judge a push by what git holds, through the reads above the writing
half's end, over a repository that may be bare: `-C <bare>` with `GIT_DIR` stripped
finds it, because a bare repository is its own git directory (verified from inside a
real pre-receive hook on git 2.34.1). None of them writes — `empty_tree` is
`hash-object` with no `-w` — and each keeps the read half's rules and adds two: every
object id is validated by `_HEX_OID` *before* it reaches argv, so a bad sha (`HEAD`, a
short id, a path, an option, `<sha>^{commit}`) is `None` with no process started and
never a revision expression git evaluates; and output is `-z` or length-prefixed
(`cat-file --batch`'s `<oid> <type> <size>` header, so a blob that looks like a header
is one blob) — except `rev-list --objects`, which has no `-z` on 2.34 and stops a name
at its first newline: `new_objects` returns that name as a label for a message, never a
path a rule judges (the rules read `tree_listing` and `tree_objects`). **The quarantine
carve-out:** inside pre-receive the pushed objects are visible only through
`GIT_OBJECT_DIRECTORY` (the quarantine) and `GIT_ALTERNATE_OBJECT_DIRECTORIES` (the
main store), and `_STRIPPED` removes both, with `GIT_QUARANTINE_PATH` — stripped,
`git -C <bare> cat-file -t <new>` answers "could not get object info". So every read
of a push takes `objects: ObjectDirs | None`, and `_ask` — which gains `stdin_bytes`
(git's stdin, else `DEVNULL`), `objects` and `timeout` (this call's, else
`VCS_TIMEOUT_S`), every older caller unchanged — sets exactly those two variables on
the stripped environment for that one call. Nothing in the spine reads the variables:
the hook script reads them and hands them to the CLI as flags
(`test_vcs.TheQuarantineIsAnExplicitPassthrough`). *Rejected:* loosening `_STRIPPED`
for the two (every other caller would then answer for the OUTER repository when run
from a hook — the trap this section opens with, reopened for the one caller that must
not fall into it); reading the variables inside `_ask` when `objects` is `None` (an
environment read in the spine that no test could tell from the leak, and `tests/_env`
strips them anyway, so no in-process row would ever have seen the quarantine).
`_STRIPPED` also gains `GIT_INTERNAL_SUPER_PREFIX`, which git 2.34 lists in
`rev-parse --local-env-vars` and 2.43 (the list's source) had dropped: the
clean-environment test was red on a 2.34 machine for exactly that name.
`trailer_lines` is git's OWN reading of a trailer block (`%(trailers:only,unfold)`,
the last paragraph when every line of it is a trailer); the validator scans the raw
`CommitObject.message` beside it, so a `Nope-` line git reads as prose is refused
rather than read two ways, and a block holding the format's `\x01`/`\x02` separators
does not parse — the answer is `None`, never a trailer misread. `new_objects` takes
`exclude` and never `--all`: inside pre-receive `--all` is the pre-push ref state, but
outside a hook (the CLI the agent runs before pushing, every in-process test) the ref
already points at `new` and `--all` would list nothing. Both `log` reads carry
`-c log.showSignature=false`, the pin `history` and `commit_times` carry: a gpg line
would read as no tree, or no trailer block.

### `store.py`  (deps: models, util)
Persistence: where a project lives on disk. **Never imports `modelio` or `verdicts`**
(spec §3.1): the migration's reader of what the model states is injected.
```python
NOPEKIT_DIR = ".nopekit"
LEDGER_NAME  = "ledger.json"          # the GENERATED index (records project); the records (legacy)
PROJECT_NAME = "project.json"         # .nopekit/project.json — meta, and the commit marker
LEGACY_LEDGER_NAME = "ledger.legacy.json"   # what a migrated ledger.json is renamed to
PROJECT_SCHEMA = 2                    # project.json's schema; a newer one is refused
RECORD_DIRS = ("claims", "params", "decisions", "needs", "inputs", "results", "views",
               "milestones", "exports",   # P2.5b's: never on a legacy project
               "requirements",            # phase 1 (SPEC decision 12)
               "preferences")             # phase 2 (SPEC 14, invariant 13), last
VERDICTS_NAME = "verdicts"; CACHE_NAME = "cache"; OBS_NAME = "obs"   # under .nopekit/
INDEX_BANNER: str                     # the index's "generated" value: edit the records, never this
LEGACY_GITIGNORE_TEMPLATES: tuple[str, str]  # init's .nopekit/.gitignore at 1e09113, and at 1.2
INPUT_BUCKETS = ("sketches", "references", "cad", "screenshots",
                 "datasheets", "specs", "measurements", "data")
BUCKET_FOR_KIND: dict[ArtifactKind, str]                # which inputs/ bucket a kind lands in
def find_root(start: str | None = None) -> str | None   # walk up for a MARKER; stop at .git
def require_root(start=None) -> str                     # raises NopekitError if none
def nopekit_dir(root) -> str                           # <root>/.nopekit
def ledger_path(root) -> str                            # <root>/.nopekit/ledger.json
def inputs_dir(root) -> str                             # inputs/   (NOT hidden - users put files here)
def out_dir(root) -> str                                # .nopekit/out/  gate scratch + evidence
def docs_dir(root) -> str                               # docs/  generated report + decision log
def model_dir(root) -> str                              # model/ the single source of truth
def project_paths(root) -> dict[str, str]              # every well-known path, by name

def read_project(root) -> ProjectMeta                   # .nopekit/project.json, strict
def write_project(root, meta) -> str | None             # the path, or None when unchanged
def people_problem(people, label) -> str | None         # phase 6: project.json's `people`,
                                                        #   each {name, email} as git prints them,
                                                        #   one per email ignoring case
def read_record(path, kind, *, model_entry="") -> Record | ResultsFile           # STRICT
RESULTS_LISTS = ("results", "attributions")   # P2.5a: a results file's two sealed lists
SEAL_SCHEMA = 1; ROLES = ("owner", "authority")
CONTRADICTS_KEYS = ("gate", "code", "rho", "value", "units", "inside")
class ResultsFile(list):        # P2.5a: list[PhysicalResult], plus .attributions and .raw
def append_signed(root, claim_id, list_name, entry: dict) -> dict  # a results entry: append_sealed's
def append_sealed(root, kind, stem, list_name, entry: dict) -> dict  # P2.5b: THE writer of a sealed
                                       #   entry — results/<id>.json's lists, exports/<m>.json's,
                                       #   and (phase 2) preferences/<decision>.json's
class ExportsFile(list):        # P2.5b: list[ExportRecord], plus .raw (the file as stored)
class PreferencesFile(list):    # phase 2: list[PreferenceRecord], plus .raw (the file as stored)
SEALED_KINDS = ("exports", "preferences")   # one sealed list per file; write_record refuses them
REJECTION_KEYS = ("type", "by", "returns_if", "input")   # phase 2: a decision's, refused on a
                                #   param record's loser (its losers stay text)
PROPOSER_ID: re.Pattern         # phase 2: a dotted proposer id, an input's "proposer"
def rejection_problem(item, stem="<decision id>") -> str   # phase 2: why a decision's rejection
                                #   breaks SPEC 14's rules, as the tail after rejected[i], or ""
def return_condition_problem(cond) -> str   # phase 2: why a returns_if is no condition, or ""
MILESTONE_NAME: re.Pattern      # P2.5b: ^[a-z0-9][a-z0-9._-]{0,63}$ — a milestone file's stem
REQUIREMENT_ID: re.Pattern      # phase 1: ^R[1-9][0-9]*$ — a requirement file's stem, and every
                                #   id a claim's "requirements" names
def generator_parts(ref) -> tuple[str, str]   # "<path>.py:<function>" -> (path, function); refuses
ARTICLE_KEYS = ("source", "hash", "built_from", "traced", "milestone", "when", "revision",
                "dirty")        # an article dict's closed keys, a result's and an export's
REPORT_NAME = "REPORT.md"; PACKAGES_NAME = "out"   # P2.5b: at the root, both ignored
def write_record(root, kind, record, *, record_id=None) -> str | None             # None: unchanged
def load(root, *, model_prose=None) -> Ledger   # the records; a legacy ledger migrated IN MEMORY; verdicts []
def save(root, ledger: Ledger) -> None  # tests and the migration only (see below)
def is_legacy(root) -> bool             # ledger.json present, project.json absent
def build_index(root, *, digests: FileDigests | None = None) -> dict   # pure
def write_index(root) -> bool           # True when .nopekit/ledger.json changed; best-effort
def agree(root) -> list[str]            # every way the index disagrees with the records
def records_digest(root, *, exclude=()) -> str   # sha256 over the record files and project.json;
                                        # exclude: record dirs left out (export's manifest: "exports")
def pointer_problems(ledger) -> list[str]   # phase 3a: the graph's record pointers it cannot
                                        # follow — a param's grounded_by naming a non-assumption
                                        # claim or an id both an input and a claim, a resolves
                                        # naming no claim or a non-assumption; the index's and
                                        # doctor's graph row's one producer
def grounded_by_problem(where, name, aid, input_ids, claim_kinds) -> str | None
                                        # one grounded_by id: an input, or an assumption the
                                        # value IS; else why not (doctor reads PARAMS' through it)
GROUP_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")   # phase 5: a group's stem
RESERVED_GROUP_IDS: dict[str, str]      # intro, next, story, other: the page's own names
def groups_in_order(groups, kind) -> list   # (order, natural id): the one order of a kind
def group_problems(ledger) -> list[str] # phase 5: a board member no claim holds, a claim on
                                        # two boards, a name in two zones or two stations, or
                                        # in a zone and on a station; the index's and doctor's
README_BADGE_BEGIN = "<!-- nopekit:begin badge -->"; README_BADGE_END = "<!-- nopekit:end badge -->"
README_BADGE_BLOCK: str                 # SPEC 36: the Made-with-nopekit block
def ensure_readme_badge(root, name="", summary="") -> bool   # the block at the bottom of
                                        # README.md (created when absent); idempotent; a
                                        # block is whole marker lines, the last one; the
                                        # file's line ending is kept
def readme_has_badge(text) -> bool      # the same matcher, for doctor's `readme` row
class MigrationPlan(NamedTuple):
    ledger: Ledger; files: dict[str, bytes]; notice: str
def migrate_legacy(root, *, apply: bool, when: str,
                   model_prose: Callable[[str, str], dict] | None = None) -> MigrationPlan
def ensure_ignore_blocks(root) -> list[str]             # the files changed; idempotent
def init(root, meta: ProjectMeta) -> Ledger             # the records layout; refuses only on a marker
```
**A project is where its marker is.** `find_root` walks up from `start` (default: the
cwd), and at each level checks **first** for a marker — `.nopekit/project.json`, or
the legacy `.nopekit/ledger.json` — returning that directory; **then** returns `None`
if the level holds a `.git` entry (a directory, or the *file* a linked worktree or
submodule has); else it walks up. A bare `.nopekit/` directory is not a marker.
What slipped through (S-64): any `.nopekit/` made a project, and `~/.nopekit/` is
the user-pack home, so on a pack author's machine every directory under `~` was
"inside a project" and pack-mode `gate selftest` could never be reached there (nor,
later, Phase 3's Stop-hook fast exit and `/start`'s `init`). The `.git` boundary is
why a nested worktree inside a project resolves to `None` rather than to the trunk's
ledger, while a project that is its own git root is still found (the marker is
checked before the boundary). `require_root`'s message names both markers and the git boundary. `init`
refuses **only when a marker exists**: a directory holding only `.nopekit/packs/`
is not a project, and `init` there succeeds.

`project_paths` is the layout in one call (`"ledger"`, `"project"`, `"legacy_ledger"`,
`"report"`, `"packages"`, `"milestones"`, `"exports"`, `"decisions"`, `"inputs_<bucket>"`,
...; `"readiness"` is where `report --write` wrote before P2.5b, kept so `doctor` can
name a leftover one), whether or not the paths exist
yet: this is the map, not an inventory. No other module joins a well-known path by
hand, so moving the layout is one edit here instead of a grep across the spine.

**The layout** (checkpoint 1.3; relative to the project root):
```
.nopekit/project.json     TRACKED  {"schema": 2, name, summary, created, revision,
                                     model_entry, packs, spine_version, people}
                                     — written LAST; people: [{"name", "email"}]
.nopekit/.gitignore       TRACKED  the marked deny-list block (below)
.gitignore, .gitattributes TRACKED  marked blocks: bytecode, REPORT.md, out/, the
                                   editor's .vscode/launch.json; LF, binary kinds -text
.nopekit/ledger.json      IGNORED  the generated index — an output, never read for truth
.nopekit/verdicts/**      TRACKED  cache and control entries (verdicts.py)
claims/<id>.json           TRACKED  one Claim; the stem IS the id; no "gates"
params/<name>.json         TRACKED  SPARSE: only what the model cannot hold
decisions/<slug>.json      TRACKED  one Decision
needs/<id>.json            TRACKED  SPARSE: only an enriched Need
inputs/<id>.json           TRACKED  one InputArtifact; its bytes stay in inputs/<bucket>/
results/<claim-id>.json    TRACKED  {"results": [PhysicalResult, ...], "attributions":
                                   [AttributionRecord, ...]}, append-only, sealed (P2.5a)
views/<id>.json            TRACKED  declared views, beside the viewgens views/*.py
milestones/<name>.json     TRACKED  one Milestone (P2.5b); the stem IS the id
exports/<name>.json        TRACKED  {"exports": [ExportRecord, ...]}, append-only, sealed
REPORT.md                  IGNORED  the readiness report (`report --write`)
out/<name>/                IGNORED  a milestone's package (`export`)
```
`init` writes the empty record directories, the input buckets and `inputs/README.md`
(only when missing), `docs/`, `model/`, the three blocks, and `project.json` **last** —
**never a `ledger.json`**: a new project born in the legacy layout would need a migration
on its first command. Then (architecture phase 6) it starts a git repository
(`vcs.init_repo`) only on git's positive answer that none holds the project
(`vcs.in_repository`; `--no-git` starts none), and says when an enclosing repository
ignores the project. The record reader and the index read only `views/*.json`; the
viewgen loader only `views/*.py`. Every top-level `inputs/*.json` IS a record; evidence
bytes live in the bucket subdirectories.

**The writer.** `write_record(root, kind, record)` writes `<root>/<kind>/<id>.json`:
dataclass field order; the id omitted (the stem is the id; `name` for a param); every
field at its default omitted except `models.ALWAYS_WRITTEN`; a nested record equal to
its whole default omitted; every `models.FORBIDDEN_KEYS` key omitted;
`json.dumps(indent=2, ensure_ascii=False, allow_nan=False) + "\n"`; written atomically
and **only when the bytes differ** — so the bracket's C1 is phase-1's example key for
key, and a second write changes no byte. `results` is the one list kind: pass the
`PhysicalResult`s with `record_id=<claim id>`. An id that cannot be a file name (empty,
a leading `.`, `/`, `\`, `:`) or that differs only in case from an existing record is
refused. `write_project` writes every `ProjectMeta` field after `"schema"`.

**The reader is strict** (Q1.9). `read_record(path, kind)` refuses, naming the file,
the key and the `difflib` suggestion: an unknown key at any depth
(`claims/C1.json: unknown key "limt" at acceptance.limt (did you mean "limit"?)`); a
key written twice; NaN or ±Infinity (and a literal like `1e999`); an empty file; a
non-object; an `id` that disagrees with the stem; a `FORBIDDEN_KEYS` key, naming its
owner (`model_entry` names the model file); `independence` anywhere; an input whose
`path` names a record file — a top-level `<record dir>/*.json`, its own or another's,
in any spelling (`./inputs/x.json`, `inputs\x.json`) — because `load` reads those bytes
as a record and the pin can only ever read as drift (what slipped through: a legacy
input ingested in place at `inputs/loads.json` migrated to a record at that path naming
itself as its evidence). A failing
top-level `inputs/*.json` is most likely stray evidence, and the message names
`nopekit ingest` and the buckets. A results file must be `{"results": [...]}` whose
items say `passed` as a bool, and a claim's `critical`, when present, is a bool too
(review of P2.1: `"critical": null` read as not required by truthiness, so a failing
claim left every required list and `check` said ready). `load` also refuses two record files whose stems differ
only in case.

**A claim's mechanism** (architecture phase 1, SPEC decision 13). A claim file says
how it settles in one object, `"mechanism": {"by", "how", "evaluators"}`, and the
reader unfolds it (`_unfold_mechanism`) into the flat fields every reader reads —
`terminal`, `note`, `evaluators` (`models.CLAIM_MECHANISM`) — before `_check_keys`
runs (which takes `suggest=("mechanism",)`, so `mechansim` is told `did you mean
"mechanism"`). Each rule binds only when its key is present (R-10): a top-level
`"evaluators"` is refused (it lives in the mechanism, a cross-check, never coverage —
S-37); a mechanism that is not an object, or holds a member it does not know, is
refused with a suggestion; one member said under both names — `"terminal"` beside
`mechanism.by`, `"note"` beside `mechanism.how` — is refused even when the two agree
(the dropped one would be erased by the next write, S-40), while different members in
different spellings read; `how` is text; `evaluators` is a list of distinct,
unpadded ids; `"mechanism": {}` reads as absent. `by` then goes through
`_check_terminal(..., spelled="mechanism.by")`, held to `TERMINALS_BY_KIND` exactly as
`terminal` is, each refusal naming the spelling the file used. The writer folds them
back (`_fold_mechanism`, from `_encode`): `"mechanism"` at `note`'s slot, members in
`by`/`how`/`evaluators` order, only those not at their default — a claim with no
mechanism writes exactly the bytes it wrote before. `Record.to_dict` stays flat (it
feeds `verdicts._record_form`, `report.claim_json` and the `LedgerView`'s copies).

**Requirements** (architecture phase 1, SPEC decision 12). `requirements/<id>.json`
holds `statement` (the person's words, non-blank text) and `source` (text: "interview",
or an input id); its stem is a `REQUIREMENT_ID` (`_check_requirement`), and a refusal
of a top-level `requirements/*.json` adds `_REQUIREMENTS_HINT` (a document of the
project's own goes in `inputs/specs/`, through `ingest`). A claim's `"requirements"` is
a list of distinct requirement ids (`_check_id_list`); one naming a requirement no file
holds is read, never refused (the milestone precedent), and named by the index and by
`doctor`. *Rejected:* quarantining a file that does not read as an index `problem` —
a typo'd real record would vanish (S-40's shape). `Record.from_dict` stays lenient.

**`load`** reads the records (each kind in natural id order — C2 before C10 —,
decisions newest first by `when`), sets each claim's in-memory `physical_result` to the
result that counts in `results/<id>.json` — the latest fail when any failed, otherwise
the latest (R-3: no later pass outranks an earlier fail) — each input's `bytes` from its file's size, and `verdicts`
to `[]` (they live in the verdict cache). On a legacy project it is
`migrate_legacy(root, apply=False, when="", model_prose=model_prose).ledger`: the same
Ledger the migration will write — **when it is given the reader the migration runs
with**, so a command answers the same before and after it and refuses exactly what the
migration refuses. Every spine caller (`cli._load`, `modelio`'s entry resolver,
`packs.installed`) passes `modelio.static_param_prose`, `check`'s reader; a call that
does not is a test failure (`tests/test_records.EveryReaderMigratesAsCheckDoes` walks
every spine module's AST, as `NoWholeLedgerWriterInCli` does for `save`). What slipped
through: `load` planned with none — lossless, every param keeping its rationale — while
`check` planned with the static reader, so on a model stating `D` and `d` every read
command refused "params/D.json and params/d.json would name ids that differ only in case
… rename one" about two files `check` never writes, and after a migration killed half
way every read command blamed a `params/thickness.json` that was byte for byte `check`'s
("Move those files aside"). *Rejected:* a default reader `modelio` registers into the
store at import (the plan would turn on which module a process imported first). Neither
marker: an empty `Ledger`. **No spine code reads `.nopekit/ledger.json` for truth** on
a records project.

**`save(root, ledger)`** stays for tests and the migration (an AST test,
`tests/test_shims.py`, forbids it in `cli.py`: a command writes exactly the record it
was asked to). On a records
project it writes `project.json` and one file per record, removes the file of a record
no longer in the ledger (a param with nothing to hold writes none), APPENDS a claim's
`physical_result` to `results/<id>.json` when it is not already the last one — a
result is never removed —, then rebuilds the index. On a legacy project it writes the
legacy `ledger.json` with `verdicts: []` and no sweep record, and does not migrate.

**The index** is `build_index(root)`, a pure function of the record files and the bytes
of the inputs they name — no clock, model, registry or listing order. Keys, in order:
`generated` (`INDEX_BANNER`), `schema`, `records_digest`, `meta`, `claims`, `params`,
`decisions`, `needs`, `inputs`, `results`, `attributions`, `views`, `milestones`,
`exports`, `requirements` (phase 1), `unregistered_inputs`, `problems`.
Each record row is its file's content plus its id, as the writer spells it (a claim
read from the older `terminal` and `note` keys shows them as `mechanism`). Each input row adds `sha256` (the
digest of the bytes NOW, through `util.FileDigests` and the untracked
`.nopekit/cache/digests.json`, read, never written), `pinned` (the record's sha256),
`drift` and `exists` (`null` for an input with no path). `unregistered_inputs` lists
evidence bytes under `inputs/` — not a top-level `*.json`, not `inputs/README.md`, not a
dot-file — that no record's `path` names. `problems`: missing bytes, drift, dangling
`grounded_by`/`claim_ids`/`claims_changed` links, results for a claim with no file, a
claim naming a requirement no file holds, a requirement id that is also a claim's. It
**never** holds a status, coverage or a verdict — those sit in `last_check.json`, and an
index that carried them could disagree with them. What slipped through without the
computed digest (S-45): evidence was hashed at ingest and never again, so tampered bytes
read as unchanged. `write_index` rewrites `.nopekit/ledger.json` only when its bytes
change, only on a records project (on a legacy one that file IS the records), and
best-effort (a read-only filesystem warns on stderr). `agree(root)` lists every way the
file on disk differs from a fresh build — records compared by id, so a hand-edited
limit reads `claims.C1.acceptance.limit: the index says 5.0, the records say 0.5` —
and is `[]` on a legacy project or before any index exists (invariant 8). `doctor`
never writes it. `records_digest` hashes the record files' paths and bytes plus
`project.json` (a legacy project: its `ledger.json`); it moves when a record moves,
never when the index is rewritten.

**The migration**, `migrate_legacy(root, *, apply, when, model_prose=None)`: a pure
function of the legacy JSON and of `model_prose(root, meta.model_entry)` —
`{name: {"rationale", "units"}}`, what the model STATES, read statically; the CLI passes
`modelio.static_param_prose`. Before a byte is written it refuses an unknown key at every
level (`rejectd` → `.nopekit/ledger.json: param "thickness": unknown key "rejectd" (did
you mean "rejected"?)`, S-40), `independence`, a NaN inside a record, an id that cannot
be a file name or collides (exactly or by case), a `ledger.json` with a `generated` key
and no `project.json`, a legacy input whose `path` is where a record goes, and files
where records go that disagree with the plan. The input is the old spine's `ingest`
recording a file inside the project in place: `inputs/loads.json` became input `loads`
with that path, the file its own record goes in now. It is refused on the path, not on
the file being there, naming the move and the edit that migrate it (`move them to
inputs/data/loads.json and set its "path" to …` — the bucket of its kind). What slipped
through: the plan wrote the record there and called the bench log a half-written record
("differs from what .nopekit/ledger.json migrates to … Move those files aside"), and
following that let the migration write a record naming itself — DRIFT forever;
`inputs/Bench Loads.json` (input `bench-loads`) was called "not in
.nopekit/ledger.json". *Rejected:* moving the bytes during the migration (it stays a
function of the ledger and the model, and a model opening `inputs/loads.json` by path
would break without a word); rewriting only the path (a record naming bytes that are not
there). Of the files where records go, one that reads as a record is a crashed
migration's and must equal the plan (both named, "has not finished migrating"); one that
does not read as a record is named as a foreign file — and, where the plan writes there,
as what it would collide with — never offered "make the ledger say the same".
Legacy `verdicts` and the old sweep record are dropped unread (D-09).
`apply=True` writes the record files, then `ensure_ignore_blocks`, then `project.json`
**last** (the commit marker: a crash before it leaves a legacy project whose next run
re-derives the same bytes and completes), then renames `ledger.json` to
`ledger.legacy.json` — never deleted. `apply=False` writes nothing; `load` and `doctor`
use it, with the same `model_prose` as `check` (see `load`). `MigrationPlan.ledger` is the plan read back through the strict reader;
`files` maps each root-relative path (including `.nopekit/project.json`) to its bytes;
`notice` is one stderr paragraph for the CLI — with `apply`, it ends with
`git rm --cached .nopekit/ledger.json` (the spine runs no git); without, it says the
project "will migrate … on the next check". On a project already migrated it returns
the loaded records, `{}` and `""`, and changes no byte (it only finishes a rename a crash
interrupted). It never takes the build lock and never reads the clock: `when` appears
only in the notice.

What reaches no record: `Claim.gates`, `Param.value`/`derived_from`/`gates`/`changed_in`,
`InputArtifact.bytes`; `physical_result` moves to `results/<id>.json`; a need `gap`
would derive by itself (one claim, OPEN, nothing chosen, the claim's own quantity)
writes no file. **The params rule:** a param keeps `source`, `grounded_by`, `tags`,
`rejected`; it keeps `rationale` unless the model states one, and `units` unless the
model states them; a param left with nothing writes no file. It loses nothing: the
legacy parameter sync overwrote a record's rationale with the model's whenever the model
stated one, so a hand-written rationale survives in a legacy ledger only where the model
is silent. With `model_prose=None` it is lossless. For the bracket every Config field
has a docstring and every legacy `units` is `""`, so no `params/*.json` is written — by
the rule. *Rejected:* dropping units/rationale whenever `model_entry` is set (erases a
hand-written rationale the model lacks); keeping everything and hand-deleting the
bracket's twelve duplicates (a generated output edited by hand).

**`ensure_ignore_blocks(root)`** keeps three marked blocks (`# nopekit:begin` …
`# nopekit:end`), each at the top of its file, idempotently:
`.nopekit/.gitignore` — the deny-list `ledger.json ledger.legacy.json obs/ cache/ out/
export/ runs/ *.tmp *.lock` (never an allow-list: `.nopekit/packs/` is source and
`model.json` is reviewed; `verdicts/` is evidence and stays tracked); the root
`.gitignore` — `__pycache__/`, `*.py[cod]`, and from P2.5b `/REPORT.md` and `/out/` (the
report and the packages, outputs: S-41's committed report drifted from its ledger;
anchored, so a `model/out/` stays a source); the root `.gitattributes` —
`* text=auto eol=lf` and `*.stl`, `*.step`, `*.glb`, `*.png`, `*.jpg` `-text`, one
pattern per line. A `.nopekit/.gitignore` that **begins with** a
`LEGACY_GITIGNORE_TEMPLATES` text has that prefix replaced by the block; a line outside
the block equal to `!ledger.json` or `!runs/` is removed with a stderr notice (it would
re-add the index on the next `git add -A`, undoing D-06); a line duplicating a block
line is removed, and a paragraph left holding only the comments that explained such
lines goes with them (the bracket's appended `cache/`/`obs/`); every other user line is
kept, in order, after the block. What slipped through (S-76): `init` wrote an ignore
file that allowed the ledger, and nothing ignored `cache/`.

**No run history** (checkpoint 1.2). `init` makes no `.nopekit/runs/`, and the store
has no run API: every `check` and every `gate selftest` used to append a tracked run
file, so the suite dirtied the tree it verified (S-89), and `<gate>#selftest` rows sat
in the same series as the sweep's with no latency reader filtering them (S-31). Git and
the verdict cache are the history; what runs cost is `.nopekit/obs/`, gate runs and
control runs apart (`verdicts.record_obs`).

### A project's model  (not a spine module: what `modelio.py` loads)
The model contract. A project's model is a **Python module** — the file
`"model_entry"` names in `.nopekit/project.json` — exposing:
```python
CONFIG: dataclass instance          # or  Config: type  +  CONFIG = Config()
def build(config) -> dict           # the resolved geometry/state; pure, deterministic
PARAMS: list[Param] = [...]         # optional explicit provenance; else inferred from fields
```
It is the one home of a parameter's value, units and rationale (a `Config` field and
its docstring) and of the alternatives that lost to it (`PARAMS`' `rejected`): see
"The model owns the value" under `modelio.py`. Its own heading, because none of these
names is `modelio`'s — the reverse contract check reads every fenced `def` in a
module's section as that module's, and filed here the three read as a spine API.

### `modelio.py`  (deps: models, util, store)
The spine's half of the model contract.
```python
@dataclass
class LoadedModel:
    module: Any; config: Any; entry: str; params: list[Param]
    file -> str; directory -> str                # properties: the file executed, and its dir
def load_model(root, entry: str | None = None) -> LoadedModel   # entry: project.json's model_entry
def project(model: LoadedModel) -> dict      # {"config": {...}, "derived": {...}} JSON-safe
def model_hash(projection: dict) -> str      # stable; a display id (staleness is per gate)
def write_projection(root, projection) -> str   # .nopekit/model.json  (the diffable view)
def params_from_model(model) -> list[Param]  # merge PARAMS with dataclass fields+defaults
def undefended_params(views) -> list[str]    # param_view's views the model holds that no home
                                             # defends: THE nag list (doctor, model, status, report, page)
def check_determinism(model, runs=2) -> tuple[bool, str]   # build twice; the projection must not move
```
Rule enforced here: a projection value that is a dataclass/enum is encoded via
`models._enc`. Non-JSON-safe values raise `NopekitError` naming the field —
silent coercion is how a model and its projection drift apart.

```python
def flat_params(projection) -> tuple[dict, list[str]]   # derived first, config on top; + conflicts
class CodeClosure:                                       # what a loaded module's code IS
    files        # ((abspath, sha256 of the bytes compiled), ...)
    fallback     # "", or why the closure is a whole directory: "computed source at <file>:<line>"
    third_party  # static top-level imports that are not code: installed, or not importable
                 # (not stdlib, not nopekit; a file beside the roots and not installed is code)
    spine_extras # nopekit.* modules it imports that are not in verdicts.SPINE_MODULES
    data         # ((abspath, sha256), ...): files that code opened to read while the module
                 # (or a helper of its closure) ran — a table read at import; NO_BYTES if none
NO_BYTES: str                                            # data digest of a file opened and found
                                                         #   missing (or a directory, unreadable)
def load_source_module(path, *, name, roots, registry=None, attrs=None) -> ModuleType
                         # fresh bytes, recorded closure, content-keyed; registry: where its
                         # gates are recorded and re-adopted (the caller also wraps the call in
                         # gates.use_registry — modelio cannot import gates); attrs: globals set
                         # before it runs (a pack's PACK, PACK_DIR), part of the cache key
def load_path(path) -> ModuleType                        # a helper by path; module name salted by its abspath
def on_unrecorded_load(listener) -> None                 # listener(closure) when a module is run or served
                                                         #   with no load being recorded (at RUN time)
def bookkeeping() -> bool                                # this thread is inside the loader's own digests
def recording() -> bool                                  # a module's execution is being recorded now
def dynamic_imports(tree) -> list[tuple[int, str]]       # (line, call): import_module/__import__ of a
                                                         #   module a VALUE names; doctor's dynamic-imports
def is_code(path, roots=()) -> bool                      # code (under roots, or beside them and not
                                                         #   installed), not an instrument; the finder's test
def registered_by(fn) -> tuple[ModuleType, ...]          # the modules whose __nopekit_gates__ hold fn
                                                         #   itself (by identity), sorted by name
def code_closure(obj) -> CodeClosure | None              # a module's; a function's: its defining
                                                         #   module's merged with each registered_by
def clear_caches(obj) -> tuple[str, ...]                 # empty the functools memos obj's code holds
                         # at module level (its module, each module that registered it, every
                         # module of its closure: globals and the attributes of classes they
                         # define); the dotted names emptied
```
**`flat_params` is the one copy** of "flatten a projection into `ctx.params`": derived
values first, config on top, and the list of keys where the two disagree. It was
written twice, in `cli` and in `site`, kept in sync by a comment (S-28).

**One loader for every piece of code a verdict depends on.** `load_source_module` loads
pack gate modules, project gate modules, fixtures, the known-good module, and helpers
loaded by path (`load_path`, which replaces fdm-print's `_sibling_module`). It reads
the bytes, records `(abspath, sha256)`, and compiles THOSE bytes — no `.pyc` is read or
written for these modules. What slipped through: after a same-size, same-second edit,
gate modules and pack helpers ran the old bytecode — source said 8.0, the verdict came
from 7.0 (S-26); `_FreshLoader` had fixed that for the model entry only. While a module
executes, a recording finder attributes every import of code to it; a walk of its
globals attributes helpers found already in `sys.modules`; a static pass adds lazy
imports inside function bodies. **Code** is a Python file under `roots`, or beside them
and not installed; installed means under one of the interpreter's trees (prefixes,
stdlib, site and user site, the spine) or under a directory named `site-packages` or
`dist-packages`, and only installed modules are third-party — instruments, provenance,
never rho (Q1.3). What slipped through with `roots` alone (review round 1): a monorepo's
`shared/beamlib.py`, put on `sys.path` by a gate module, ran from its `__pycache__`, was
left out of the closure and filed as an instrument `beamlib: unknown` — an edit to its
allowable kept the PASS Fresh, and a same-second edit ran the old bytecode. *Rejected:*
distribution metadata as the test of installed (an editable install has it, and its
source is a working copy); making such a gate opaque (re-run on every check, still from
the pyc). Only `exec` of computed source from a frame in code, or an import of code that
cannot be mapped to a file, falls back to every `*.py` under the owning directory, and
`fallback` says so. The closure is stored on the module as
`__nopekit_code__`; a later load returns the cached module only while every file in
it still hashes the same, else purges the recorded helpers and re-executes (the gates
it registered are re-adopted into the caller's registry). `load_model` records the
model's closure the same way.

**A gate's code is the module that registered it, too.** `code_closure(fn)` merges the
closure of the module that defines `fn` with that of every module whose
`__nopekit_gates__` holds `fn` (`registered_by`, read from `sys.modules` as it is now),
as a load folds in a helper: a file the two recorded at two digests is torn. For a gate
written with `@gate` the two are one module and its closure comes back unmerged.
`verdicts._pack_dir_of` (a control's owner, the default `<pack:NAME>` anchor) and
`gates._fixture_root` read `PACK_DIR` from the defining module, else from a registering
one. What slipped through (Phase 1 review, `p4`): a factory in `gates/_gatekit.py` made
the gate `gates/limits.py` registered with its `MASS_LIMIT_G`; the function's
`__module__` was the helper's, so `code.files` was `['gates/_gatekit.py']` — the limit
went 100 -> 10 and `check` served the PASS cached, `status` named nothing stale, `doctor`
said every gate's code was recorded, `--force` filed two outcomes for identical inputs;
and a pack's factory-made gate was owned by the project, whose `selftest/` its control
walked. *Rejected:* stamping the registering module on the function (one function
registered under two ids has two, and a cache hit re-adopts without running anything to
stamp); the loader's `_STACK` at decoration time (empty when a digest is asked for).

**What a module reads at import is in its closure.** While a module executes, the
loader's audit hook files every `open` that reads a file which is not an instrument's
(`_code_root`, so under the roots or beside them and not installed; bytecode and
`/proc`, `/sys`, `/dev` never) in `CodeClosure.data`, digested when the module finishes —
`NO_BYTES` for one it found missing, which holds while the file stays missing. An open
counts when, walking out from the frame that made it, the first frame of code is
reached through library frames alone (`pathlib`, `numpy`, `pkgutil.get_data` read on
their caller's behalf); an import in progress on the way means an installed library's
own module body opened it, which is dropped, because whether that body runs depends on
what imported the library first (packs:H3). A module file read by the import system or
a source reader (linecache, tokenize, warnings, traceback) is theirs, and the loader's
own digests (`_read`) are never a read. A helper's data folds into its importer's
closure as its code does, and a cached module whose data moved is re-executed.
`verdicts` keys data exactly as code: in a gate's code digest, the model digest, and a
control's fixture hint (the known-good module's included). What slipped through
(admission review, round 1, D): every module loads before any trace window, and the
closure held code only — a fixture module reading its known-bad span from
`inputs/data/bad_span.json` at import was keyed nowhere, and defused 400 -> 40 mm it was
served `cached` while `gate selftest` said PASSED its own known-bad fixture; the same
for a known-good module's design and a gate module's limit. *Rejected:* loading the
module inside the window (it runs once per process, so rho would depend on
module-cache state). Named residuals: what a module only asks about (`os.path.exists`)
or lists at import, a database opened in C, an environment variable (`env-reads`), and
a read by a thread it started, once it has finished.

**Code loaded at run time is a read of the run** (admission review, round 2). A
closure is recorded while a module is being LOADED; a module `load_path` (or any
`load_source_module` caller, `load_model` included) loads from a function body while a
gate, a fixture's `make` or `known_good.context` runs has no load to join. So
`load_source_module` hands the closure of every module it runs or serves with no
recording open to the `on_unrecorded_load` listeners, and `verdicts` registers the one
that notes every file of it — code and data — as a read of every trace window open; a
file the closure holds two versions of makes the windows opaque instead. Served from
the cache it is reported the same, so the second gate to ask keys it as the first did.
What slipped through (repros a–c): a `known_good.context` that loaded the live model by
path made the known-good design the live one with no key — the identity fixture
"fired" on a shelf failing at 150 mm, and after an edit to 80 mm `check` served that
control cached, exited 0 and put C1 under PROVEN (S-07 again); a fixture's helper
`lib/badgen.py` defused 400 -> 40 mm stayed admitted; a gate's
`load_path(gates/_tables.py).LIMIT` tightened 100 -> 50 mm kept its PASS Fresh, and
`--force` then filed "two outcomes recorded for identical inputs". *Rejected:*
folding such a load into the calling gate's code closure (a module's closure is
recorded once, as it loads; a helper loaded on one branch would make the code a gate
IS depend on its inputs). The static pass reads a call too:
`importlib.import_module("x")` — through any binding of `importlib` or of
`import_module` — and `__import__("x")` with a string literal (relative names against a
literal `package` or `__package__`, a literal `fromlist`) are resolved and recorded
like the import statement; a name a VALUE decides cannot be, and `dynamic_imports`
names it for `doctor`. While the loader digests for its own bookkeeping — `_read`, and
`_closure_current` or a fallback walk deciding whether a closure holds —
`bookkeeping()` is true and `verdicts` records nothing: a load at run time purges every
stale module under its roots first, and those digests had filed an unrelated module's
DATA as the running gate's read, keyed on whether it was the first to load anything.

**A module-level memo is emptied before every run.** `clear_caches(obj)` empties every
functools memo — an `lru_cache`, a `cache`, or a function carrying a `cache_clear` of
its own, as cachetools' `cached` does — held by the code `obj` runs: a
global, or a staticmethod, classmethod, method or property getter of a class the module
defines, followed through `__wrapped__` — in `obj`'s module, each module that registered
it (`registered_by`: a factory's caller can hand the gate a memoised callback), and every
module whose file is in its closure. `gates.run_gate` calls it on the gate function, `_build_control` on
the fixture and `verdicts._known_good` on `known_good.context`, each before the call, so
every run opens its files itself inside its own trace window. What slipped through
(review round 2): S-27 was closed for `ctx.extra` only, and an `lru_cache` around a file
read is the same channel — the admission control runs its gate first, in the same
process, so even a lone memoised gate was a hit on its real run: its entry keyed no
file, and after the file was zeroed a plain `check` served the PASS as current while a
forced run FAILed it. A memo that cannot empty itself (a dict filled from a function
body, a `global` rebound from one, a mutable default written into) is not reached;
`doctor`'s `memos` row names each one. *Rejected:* re-executing the module per run (it
re-registers its gates, and pays an expensive import per gate); calling any
`cache_clear` found by attribute lookup (a proxy's `__getattr__` runs code).

**The model owns the value** (checkpoint 1.3, U27). A param record holds only what the
model cannot (`source`, `grounded_by`, `tags`, and `rejected`/`units`/`rationale` only
where the model states none — `store.migrate_legacy`'s params rule); everything else is
read off the model when it is shown, never copied into a record:
```python
def static_param_prose(root, entry) -> dict[str, dict[str, str]]
    # {param: {"rationale": str, "units": str}} — what the model STATES, by parsing the
    # entry file: every class's AST attribute docstrings (field_docstrings' normalisation)
    # plus rationale/units constants of PARAMS items that are dict literals or Param(...)
    # calls with constant keywords. Parses, never imports or runs; {} when it cannot parse.
    # Every spine reader passes it to store.load and store.migrate_legacy as model_prose.
class ParamView:                 # one parameter as a reader shows it
    # value, units, rationale, derived_from — from the model (value None when it does not load);
    # rejected — the union of the model's PARAMS and the record's, each tagged with its
    #   origin ("model/bracket.py PARAMS" or "params/<name>.json");
    # source, grounded_by, tags — from the record; model_error — why there is no value;
    # home — where the value lives ("" when the model holds none); record — its file or "";
    # gates — derived from read sets when param_view was handed them, () otherwise;
    # read_by, sourced_from — phase 3a, filled by graph.observe only: the registered
    #   evaluators whose SERVED entry read it, and the node ids that source it
    #   (input:<id>, claim:<id> for an assumption). gates follows the last executed run
    #   (obs/), read_by the entry the resolution stands on: the two can differ
def param_view(ledger, model, *, model_error="", read_sets=None) -> list[ParamView]
def param_readers(name, read_sets) -> list[str]  # gates whose last executed reads name it
    # (first two keys of a path; deeper keys do not count) — the one copy of the rule:
    # decisions.why, cli's Param.gates and ParamView.gates all read it
```
```python
def orphan_params(ledger, model) -> list[str]   # param RECORDS whose field the model no longer defines
```
`param_view` replaces the mutating parameter sync, deleted with its last caller at
checkpoint 1.3 (it copied the model into the records on every check): the value on
screen is the model's current one (S-39), a `PARAMS` rejection added after the params
exist appears (S-38), and a model that does not load shows "model does not load: …"
and no number — never a cached one. Every reader that shows a parameter reads it
here — `why`, `doctor`, `model`, `status`, the report and the page — and judges
"undefended" with `undefended_params` over it: only a view with a `home` is judged
(an orphan holds no number; a model that does not load says nothing either way).
What slipped through (review, checkpoint 1.3): the page, the report and `status`
read `ledger.params`, the sparse records, while `doctor` and `model` read the model
alone (a model-only nag list, gone) — the bracket's page showed no parameter,
a record holding only `"source"` was undefended to `status` and the report (at value
`None`) and defended to `doctor`, and a field nobody explained was flagged by
`doctor` alone. `orphan_params` works off the records (a parameter
the model owns entirely has no record, and is no orphan); `model` and `doctor` list
them from it. `_explicit_params` refuses an unknown key in a `PARAMS` dict item with a
`difflib` suggestion (the model-side cousin of S-40).

### `verdicts.py`  (deps: models, util, store, modelio, vcs; gates, packs, claims and report only inside functions)
What a gate read, so its verdict can be keyed by it — the home of per-gate
content-addressed verdicts (PLAN D-05). Its first half is the primitives (below);
its second half turns a trace into entries you can commit (part two, further down);
freshness, admission state and the resolver read both (part three); admission's
runner, the sweep and `last_check.json` are what `check` runs (part four). It never
reads the wall clock (a `when` arrives from the CLI edge) and never takes the build
lock. `gates` imports it, so it
never imports `gates` at module level: a function that needs a gate type receives
the object.
```python
ABSENT: str                  # sha256(b"nopekit:absent\0") — a key the gate asked for, not there
PRESENT: str                 # sha256(b"nopekit:present\0") — `k in params`, presence only
DIRECTORY: str               # sha256(b"nopekit:directory\0") — a file input that is a directory
SPINE_MODULES = ("models.py", "gates.py", "modelio.py", "verdicts.py")
SMALL_VALUE_MAX_CHARS = 80   # a str this short (any bool, int, finite float) is shown beside its digest

def digest_value(value, anchors=None) -> str             # canonical, tagged, portable; never raises
def small_value(value, anchors=None) -> tuple[bool, Any]  # (True, display) | (False, None)
def portable(text, anchors) -> str                       # anchored absolute paths -> <root>/..., ~/...

@dataclass(frozen=True)
class Anchors:
    root: str; packs: {name: dir}; out: str; controls_out: str; tmp: str; home: str
    def pairs(self) -> tuple[tuple[str, str], ...]       # (absolute spelling, token), longest first
    def portable(self, text) -> str
    def portable_path(self, path) -> str | None          # None: absolute and under no anchor
    def evidence(self, paths) -> list[str]               # portable; outside-anchor paths dropped

@dataclass(eq=False)                                     # identity: two empty traces are two traces
class GateTrace:
    kind: str = "gate"                 # "gate" | "control"
    params: dict[tuple, str]           # path -> ABSENT | PRESENT | value digest | whole-level digest
    values: dict[tuple, Any]           # path -> small display value of a leaf read
    whole: set[tuple]                  # paths read in bulk; () is the top level
    ledger: dict[str, str]             # "claim:<id>" | "claims" | "params" | "meta" | ... -> digest;
                                       #   P2.4: "acceptance:<key>", "acceptance-shape:<key>"
    files_read: list[str]              # absolute, first-read order, never this window's own output
    files_written: set[str]
    dirs: set[str]                     # directories listed
    stats: list[str]                   # asked the existence, kind or size of; first-ask order
    opaque: set[str]                   # "subprocess:omc", "network", "param mesh: <Type> is not JSON"
    model_used: bool
    host_reads: dict[tuple, str]       # what a control read from the HOST context
    fixture_code: Any                  # the fixture's CodeClosure, set by gates.selftest
    anchors: Anchors | None            # makes path-valued param digests portable
    tier: int | None                   # the value read through ctx.tier (a TierRead); None: never
    sources: list[str]                 # module sources the STOCK import system read while no load
                                       #   was recorded; keyed under the project or a pack only
    acceptances: dict[str, Acceptance] # P2.4: what each ctx.acceptance(key) returned — in memory
                                       #   only: run_gate holds a pass to it
    acceptance_sites: dict[str, GoalpostSite]   # where each acceptance condition a qualification
                                       #   run read was first read with a verdict (the known-good
                                       #   run, or a walk run): gates.goalpost_runs moves it there
    def self_modified(self) -> list[str]   # read, THEN written, in this window
    def stat_existed(self, path) -> bool | None   # what the first question found; None: cannot say

class ParamTrace(dict):
    def __init__(self, data=None, trace=None, *, path=(), readonly=True, host=False)
class LedgerView(Ledger):
    def __init__(self, ledger=None, trace=None, **fields)   # **fields: dataclasses.replace's path
    def acceptance(self, key) -> Acceptance     # P2.4: records acceptance:/acceptance-shape:, raises
ACCEPTANCE_KEY = "acceptance:"                  # P2.4: an acceptance read's ledger key (the condition)
SHAPE_KEY = "acceptance-shape:"                 # ... and its shape: claims, quantity, comparator, units,
                                                #   which limits are stated, a one-sided limit_hi, and
                                                #   whether the read returns one condition
def moving_limits(form) -> tuple[str, ...]      # ("limit",) | ("limit", "limit_hi") for a stated band
def moved_goalposts(control) -> frozenset       # the keys its goalpost runs moved in full: only these
                                                #   may hold another limit on a check run
class AcceptanceRead(NamedTuple): condition; digest; shape; ids=(); problem=""
def claims_named(claims, key) -> list           # the claim whose id is key, else those tagged key
def acceptance_of(claims, key) -> AcceptanceRead   # THE goalpost rule: the read and every re-read
class ModelProxy:
    def __init__(self, target, trace)
class TierRead:                                 # ctx.tier in a gate's view; NOT an int subclass
    def __init__(self, value, trace)            # every use of the value records it on trace
class GateInputWriteError(NopekitError): ...  # "a gate cannot write another gate's inputs: ctx.params['x']"
class GateMemoError(NopekitError, AttributeError): ...   # ctx.memo used but through load_file
class SweepMemo:                                # ctx.memo in every view: a handle, not a mapping
    def __init__(self, entries=None)            # entries: the dict only load_file opens
def memo_entries(memo) -> dict | None           # a SweepMemo's entries | a plain dict | None
def sweep_memo(memo=None) -> SweepMemo          # a SweepMemo as is; a dict wrapped; else a new one

def traced_context(ctx, trace, *, readonly=True)   # -> the same dataclass type as ctx
def tracing(trace)                                 # `with tracing(t):` routes audit events, stats and env reads to t
def replay(recorded, trace=None)                   # recorded's files, sources, stats, dirs, opaque, tier -> every open trace and trace
def canonical_ast_digest(source) -> str            # "" when it does not parse
def spine_digest() -> str                          # "" when a SPINE_MODULES source is unreadable
```
**`SweepMemo` — the memo as a context holds it.** `traced_context` wraps a plain-dict
`memo` (entries shared) and keeps a `SweepMemo` as it is, so every view of a sweep
holds the same handle; `run_all`, `sweep` and `_control_host` make theirs one. Only
`GateContext.load_file` opens it (`memo_entries`). Every mapping use — `get`, `[k]`,
`[k] = v`, `del`, `in`, `len`, iteration, `reversed`, `|` either side, any other
attribute — raises `GateMemoError` naming `load_file` (`{**m}` becomes CPython's own
"not a mapping" TypeError); it answers `is None` and truth (always True); `copy` and
`deepcopy` return it; pickling hands back an empty one, so no entry leaves the sweep.
What slipped through (review, `ffr3/p2`): the view held the sweep's raw dict, so a
gate could cache a parsed table in it directly and the next gate's hit keyed no file —
S-27, closed for `extra` only. *Rejected:* an opaque channel per direct use (the gate
runs, is never Fresh, and still hands the next gate a value it computed); a dict
subclass refusing method by method (the S-25 lesson: a protocol nobody listed is a
silent read). Named residual: the private slot `_entries`.

**`ParamTrace` — what each access records.** A leaf read (`p[k]`, `p.get(k)`) records
`(path, digest_value(v))` and, when small, the value; a list leaf comes back as a copy.
A nested dict read records nothing and returns a nested view at the extended path,
identity-stable — a whole-value read there would stale `bracket.deflection` on every
Config edit. A miss records `ABSENT`; `k in p` records `PRESENT` or `ABSENT`, and a later
value read supersedes `PRESENT`. Every bulk access — `__iter__`, `keys`, `values`,
`items`, `len`, `bool`, `==`, `!=`, `repr`, `copy`, `copy.copy`, `copy.deepcopy`,
pickling, `|` on either side, `reversed` — records the digest of the whole level and adds
its path to `whole`. `__iter__` must be overridden for that to hold: CPython's
dict-merge fast path reads a dict subclass's storage directly for `dict(p)`, `{**p}`
and a `**p` call unless `tp_iter` is overridden, and those were exactly the silent reads
(S-25: the CLI's flat read recorder that this replaced saw nothing of bulk access).
Copies are plain dicts. Every
mutator raises `GateInputWriteError` on a read-only view: `ctx.params` was one mutable
dict shared by every gate, so one gate could forge the next gate's inputs (S-24). It
stays a `dict` subclass for `GateContext._exact`'s `isinstance`. Named residuals: an
explicit `dict.__getitem__(p, k)` is not recorded, and a mutable non-JSON leaf is
handed out by reference.

**Host views.** `traced_context(ctx, trace, readonly=False)` — what a control's
fixture receives — gives a writable PRIVATE copy (`host=True`): a fixture's edit never
reaches the sweep's context, and its reads land in `trace.host_reads`, not
`trace.params`. A view built over a host view — the gate on a context its fixture
returned unchanged, or on `dict(host.params)` with one value layered on — keeps
recording host reads too, for any reader; a path the fixture itself wrote is never a
host read. That is the seal detector's input: a SEALED fixture reads no host param.

**`LedgerView`** is a lazily copied ledger whose `verdicts` read
empty — a gate reading other gates' verdicts would put verdicts inside rho.
`claim(cid)` records `"claim:<cid>"` with the digest of that claim, its in-memory
`gates` and `physical_result` stripped (coverage and a bench result are not what a
gate read), or `ABSENT`. Reading `claims`, `params`, `inputs`, `needs`, `decisions`,
`views`, `meta`, `milestones` or — architecture phase 1 — `requirements` records the
whole list (`_LEDGER_WHOLE`: untraced, an edit to a requirement would leave the verdict
of a gate that read it Fresh). A field a gate's read of a record leaves out while it
holds its default (`_ABSENT_WHEN_EMPTY`) gains `evaluators` and `requirements` (empty
lists), so a claim that names neither digests as before phase 1, and a mechanism or a
pointer edit re-keys only the gates that read that claim (`ctx.ledger.claim(id)`, keyed
`claim:<id>`) or the claims whole — `how` is the record's `note`, always in the digest.
openmodelica reads a claim's limit through
`ctx.ledger` (S-23), which is why this is an input at all. **`ModelProxy`**: any real
use of `ctx.model` sets `model_used` (rho then carries the whole projection and the
model's code); copying the context or truth-testing the model does not. `None` stays
`None`. **`TierRead`**: `ctx.tier` in a gate's view (`traced_context` wraps an integer
tier on a gate's trace; a `TierRead` already there is kept, so a tier passed through a
fixture or to a nested gate records where it came from). Comparing, hashing, indexing
(`table[ctx.tier]`, `range`, a slice), arithmetic, truth, conversion, formatting,
pickling (to a plain `int`), any `int` attribute and `isinstance(t, int)` (True) set
`GateTrace.tier` — on the view's trace and on every trace opened inside its window
since, so a `load_file` loader that reads it keys the hit too (`replay` carries it);
two different values make the trace opaque. Copying and passing it around do not.
It is deliberately not an `int` subclass: CPython reads an int subclass's index from
its digits without calling a method, so `PATHS[ctx.tier]` — the natural way to pick a
path — would record nothing; the cost is that `json.dumps(ctx.tier)` raises (write
`int(ctx.tier)`). A control trace's views wrap nothing: `_control_host` hands the
sweep's tier to a control already wrapped on its trace, and a plain integer there is
one the fixture chose — a constant, not an input. What slipped through (review round
1, `probe.tier`): `GateContext` told a gate it may use the tier to pick a cheaper path,
nothing recorded that one had, and a tier-0 PASS was served Fresh to `check --tier 2`.

**`digest_value`** is sha256 over `nopekit-v1:` + canonical JSON (`sort_keys`, compact,
`ensure_ascii=False`, `allow_nan=False`) of a tagged form: NaN and the infinities
become `{"$float": ...}` (the bracket's model `build` returns `inf`); a user key starting
`$` is escaped to `$$`, so no model value can spell a tag; tuples become lists; a 0-d
numpy-like becomes its `.item()` (duck-typed, never imported); a non-string key becomes
`"$k:<repr>"`; an absolute path under an anchor becomes its portable form; anything
else non-JSON — a mesh object, a set, a self-containing list — becomes
`{"$opaque": "<module>.<qualname>"}`, a digest of its type only, and the view names the
opaque channel `param <path>: <Type> is not JSON` so the entry is never Fresh.

**`Anchors` and `portable`.** A verdict entry is a tracked file, so it must be the same
bytes in every checkout: omc's errors embed absolute `.mo` paths, 33 of 54 bundled gates
cite absolute evidence, and fixtures set absolute mesh paths in params. Tokens, longest
spelling first: `<root>`, `<pack:NAME>`, `<out>`, `<out:controls>`, `<tmp>`, `~`; each
anchor also matches its `realpath`; a filesystem root is never one; a match must start
and end on a path boundary (`/w/proj` never rewrites `/w/project2`). Evidence under no
anchor is dropped from the entry and kept in the live row.

**The audit hook.** `tracing(trace)` pushes `trace` onto one process-global stack and
installs one `sys.addaudithook` on the first push, at most once per process; every
event goes to every open trace (a fixture's nested `run_gate` feeds both, and worker
threads are routed too). The window pops on exit, before a caller's `except` formats a
traceback. The hook never raises and never opens a file; with no window open it returns
at once. It records `open` by mode — or by flags for `os.open` — as a read, a write
(`w`, `a`, `x`, `+`, `O_CREAT`...) or both (`r+`), ignoring int fds and directory fds;
`os.listdir`/`os.scandir` as a listed dir; `os.rename`/`os.replace` as writes;
`os.link`/`os.symlink` as a read of the source and a write of the link (review of P2.5b:
a linked project file put bytes where no `open` named them);
`subprocess.Popen`, `os.system`, `os.exec*`, `os.spawn*`, `os.posix_spawn`, `os.fork`,
`_winapi.CreateProcess` and `_posixsubprocess.fork_exec` (the process probe, below) as
opaque `subprocess:<name>`, plus any argv element naming an existing file as a read;
`socket.connect`/`sendto`/`sendmsg` as opaque `network` — except a request to a
multiprocessing forkserver (the first caller outside the hook and
`multiprocessing.reduction` is `multiprocessing.forkserver`), which is the child it asks
for: `subprocess:forkserver`; `sqlite3.connect` as a read of
the database file and of its `-wal` (SQLite opens both in C, so no `open` names them; in
WAL mode committed rows wait in the `-wal`), plus, unless the connection is read-only
(`file:<path>?mode=ro` or `immutable=1`, `uri=True`), the opaque channel
`sqlite-writable:<path>` — its writes are C-level too, and nothing can tell whether the
gate wrote what it read; a `:memory:`, temporary or `mode=memory` database reads
nothing. A read of a path this window already wrote is not recorded (the gate's own
output); a path read and then written is listed by `self_modified()`. Excluded: events
whose calling frame is `zipimport` or `importlib.metadata`; everything import machinery,
`linecache`, `tokenize`, `warnings` or `traceback` do (the first mesh gate opened 719
`.pyc` files on import alone) EXCEPT a read-only `open`, or a stat, of a path that is not
a module's source (`.py`, `.pyw`) — a gate's data read through `linecache.getline`,
`tokenize.open` or `pkgutil.get_data` is its read. What slipped through (review round 1,
`probe.linecache`): those modules were excluded whole, and a gate reading its limit with
`linecache.getline` recorded `files={}`. And a read by the import system itself
(`importlib._bootstrap*`, not the formatters) of a module's source — or of its
`__pycache__` file, mapped back to the source, because a valid pyc is read instead of
the source and is asked for first either way — while NO load is being recorded
(`modelio.recording()`) is the stock import system loading code for the running gate
(`importlib.import_module(name)`, a lazy `import`, `spec_from_file_location` by hand):
it goes to `GateTrace.sources`, which `Reads.from_trace` keys under the project or a
pack and drops — never opaque — elsewhere (admission review, round 2, c: a gate's
`import_module` of a local helper keyed nothing of it). Only the run that first loads
the module in a process opens it; one served from `sys.modules` keys nothing, which is
why a literal name is read statically into the code closure and `doctor` names the
rest. Nothing is recorded while `modelio.bookkeeping()` is true — the loader's own
digests. linecache is also a memo — the admission
control warmed it, in the same process, for its gate's real run — so each window's push
forgets linecache's lines for every file that is not a module's source
(`_forget_data_lines`; sources and pseudo-named entries stay for the formatter). Also
excluded: pseudo-filenames like `<unknown>` (3.13's traceback parses line fragments for
its carets, and the SyntaxError opens `<unknown>`); and paths under the interpreter's
prefixes and library directories, site-packages, the USER site (trimesh and numpy live
in `~/.local`), the installed nopekit package, `/proc`, `/sys`, `/dev`, or ending
`.pyc` — machine-specific reads that would make every entry stale on every other
machine. Not seen, and named: a subprocess's own reads (hence opaque); an `os.open` or
`os.stat` relative to a `dir_fd`; a database `ATTACH`-ed from SQL and any file a C
extension opens itself (no event names either); a data file named `*.py` read through
linecache (taken for source). What slipped through while writing it: `sys._getframe`
raises an audit event of its own, so the hook re-entered itself until the recursion
limit and aborted the `open` it was auditing — it now carries a per-thread re-entrancy
guard.

**The environment.** An environment read fires no audit event. With the hook,
`os.environ` and `os.environb` get a recording subclass of `os._Environ` — the CLASS of
the one object is changed, never the object replaced, so every binding of it (`from os
import environ`, `os.getenv`'s global) is covered, and the data and the `putenv` each
write makes are untouched: a subprocess a gate starts inherits exactly what it did.
While a window is open, a variable read (`[]`, `get`, `in`, `os.getenv`,
`os.path.expandvars`, a tilde `expanduser`) adds the opaque channel `env:<NAME>` to every
open trace, and a bulk read (iteration, `keys`/`items`/`values`, `copy`, `dict(...)`,
`len`) adds `env:*` — rho never keys a variable's value, which differs per machine and
per shell. A read counts when the first frame outside `os`, `_collections_abc`, the path
modules and `pathlib` is not library code (the library roots below, or a `<frozen ...>`
module): the `PATH` `shutil.which` and `subprocess` read, and `tempfile`'s `TMPDIR`, are
theirs. What slipped through (review round 1, `probe.env`): a gate passing on
`os.environ.get("PROBE_MODE", "ok") == "ok"` recorded `files={} opaque=[]`, and a plain
check served its PASS after the variable moved. Named residual: a value read at import
by a module-level `LIMIT = os.getenv(...)` of a module imported before the window
(`doctor`'s `env-reads` row names that), and an `environ` that is not `os._Environ`.

**The stat probes.** `os.stat` raises no audit event, and every existence, kind and
size question the standard library asks ends in it or in `os.lstat`: `os.path.exists`,
`isfile`, `isdir`, `getsize`, `getmtime`, `samefile`, `lexists`, `islink`, `realpath`,
`pathlib`'s `stat`/`exists`/`is_file`/`is_dir`, a `glob` of a literal path. What
slipped through (review round 1): `gather_mo_files` skips a `modelica_sources` entry
`os.path.isfile` says is not there, so bundled `modelica.source_hygiene` recorded
`{model/A.mo}`, kept a Fresh PASS after `model/B.mo` appeared, and failed `1/2
parameter(s) undefendable` when forced. So when `verdicts` is imported, `os.stat` and
`os.lstat` — and any `os.path` predicate that is C (Windows since 3.12) — are replaced
by probes that call the original and, while a window is open, note the absolute path
and whether it existed on every open trace (`GateTrace.stats`); the probes join the
`os.supports_*` sets the originals are in. At import, not on the first push: the
registry imports every gate module before a window opens, and a `from os import stat`
there binds whatever `os.stat` is then. Excluded like the hook's events: the hook's own
questions (the re-entrancy guard), paths under the library roots, questions whose
first caller outside `os`/`genericpath`/`posixpath`/`ntpath`/`pathlib`/`glob` is
`zipimport` or `importlib.metadata`, and questions about a module's source whose first
such caller is import machinery, `linecache` (which stats every source it caches),
`tokenize`, `warnings` or `traceback` — `linecache.checkcache` of a data file is the
gate's question. A path this window wrote first is its own output and is not
noted. Not seen, and named: a module that bound the original before the spine was
imported; `os.DirEntry.stat()`, which is C and calls nothing — a listing's digest
carries each entry's kind and size instead.

**The process probe.** On POSIX, `_posixsubprocess.fork_exec` execs a new program and
raises no audit event; `subprocess.Popen` audits itself first, multiprocessing does not.
What slipped through (review round 3, `probe.mp`): a gate that read its limit in a
spawn-context `ProcessPoolExecutor` worker — spawn is the default start method on macOS
and Windows — recorded `files={} opaque=[]`, a plain check served its PASS `cached`
after the file went to 0, `doctor` said `opaque-inputs ok`, and `check --force` failed
it at the same rho. So when `verdicts` is imported,
`_posixsubprocess.fork_exec` is replaced by a probe that, while a window is open, hands
its arguments to the hook as the event `_posixsubprocess.fork_exec` — a
`subprocess:<basename of the executable>` channel and the argv files — and then calls
the original unchanged. multiprocessing looks the function up at every call (each spawn
worker, a forkserver's server, the resource tracker); `subprocess` bound the original at
its own import and is reported by its own event. On Windows, where `_posixsubprocess`
does not exist, `_winapi.CreateProcess` audits itself and is handled. Not seen, and
named: a module that bound `fork_exec` before the spine was imported, and a C extension
that forks or execs in C.

**`replay(recorded, trace=None)`** records again what the hook and the probes routed to
`recorded` — `files_read`, `sources` and `stats` (first, each with whether it existed),
`files_written`, `dirs`, `opaque` — into every trace open
now and into `trace`, even with no window open around it; the views' channels (params,
ledger, model) are not replayed. It is how `GateContext.load_file` makes a memo hit
record every file the loader opened on the miss (a `.gltf`'s `.bin` buffers): the hit
once reported the named file only, and bundled `fdm.bridge_span` kept a Fresh PASS after
its buffers moved.

**`spine_digest`** is the spine's version for rho, taken from what `SPINE_MODULES` say:
`canonical_ast_digest` of each, read from the files beside `verdicts.py`, memoised per
process. The walk emits each node's type, then its fields in sorted name order;
`type_params`, `type_comment` and `kind` are skipped, and so are empty lists, `None`,
location attributes and every bare string statement at every depth (module, class and
function docstrings, and the attribute docstrings `models.py` writes after its fields);
constants are tagged with their type; an f-string's constant parts are merged. So a
comment or a docstring re-runs nothing, and `<` becoming `<=` re-runs everything.
What slipped through: 1e09113 changed verdict semantics (a NaN that read `[ok]` now
errors) with `__version__` still 0.1.0 (S-29). *Rejected:* `ast.dump` (3.12 adds
`type_params=[]` and 3.13 omits empty fields, so every committed entry would be stale on
two of CI's three Pythons); `tokenize` (layout-sensitive); the version string; the
bytes of the whole spine (every comment re-runs every gate). An unreadable source (a
wheel without `.py` files) gives `""`: every entry is then Unknown, never Fresh.
`tests/test_spine_digest.py` pins a fixture's digest — f-strings with nested specs,
`!r`, `{x=}`, `match`, walrus, decorators, async — and CI asserts it on 3.10, 3.12 and
3.13.

**Part two: rho, entries, controls, remembered outcomes, obs.**
```python
SCHEMA = 1                    # inside every entry, control entry, rho and code payload
RHO_CHARS = 16; OUT_CHARS = 8 # file-name widths: <rho16>-<out8>.json
OBS_KEEP = 20                 # runs kept per gate, per kind
SPEC_FIELDS_IN_RHO = ("id", "claims", "tier", "pack", "requires_tools",
                      "requires_python", "requires_one_of", "settles")
CONTROL_OUT_DIR = ".nopekit/out/controls"   # + "/<gate id>/": where a control runs
UNRECORDED_FIXTURE = "<unrecorded fixture code>"   # a control's fixture hint when no loader
                                                   # recorded the fixture's code; never holds
TWO_OUTCOMES_IS_ERROR = True  # an error; a warning (the gate read stale) until U25 measured the corpus

def anchors_for(root, registry, *, out_dir) -> Anchors   # <root>, <pack:NAME> from registry.pack_dirs, <out>, <out:controls>
def code_digest(spec, fn, *, anchors=None) -> CodeRef
def model_digest(projection, model=None, *, anchors=None) -> str   # "" when the model's code was not recorded
def rho(gate_id, spine, code, reads) -> str               # code: CodeRef | digest; reads: Reads | an entry's block
def rho_control(gate_id, static, reads) -> str
def out8(verdict) -> str                                  # Verdict or an entry's verdict block
def instruments_for(spec, code) -> dict[str, str]         # {module: version | "unknown" | "absent"}

@dataclass(frozen=True)
class CodeRef:
    digest: str; files: tuple; fallback: str; opaque: str; third_party: tuple
    @classmethod
    def unrecorded(cls, why=...) -> CodeRef               # opaque: a verdict planted with no gate
    def to_dict(self) -> dict                             # {"digest", "files", "fallback"}

@dataclass
class Reads:
    params: list          # [[path, digest], ...] or [[path, digest, small], ...]; path a JSON list
    files: dict           # {portable path: sha256 | DIRECTORY | None}   None: missing, itself an input
    dirs: dict            # {portable path: digest of sorted [name, kind or size] | None}
    ledger: dict          # {"claim:<id>" | "claims" | ...: digest}
    model: str | None     # model_digest, when the gate used ctx.model
    opaque: list          # sorted channel names: an entry with any is never Fresh
    host: list            # a control's host-param reads: [[path, digest], ...]
    tier: int | None      # the sweep tier it read through ctx.tier; None: never, and then
                          #   absent from the block and from rho (keyed as before)
    @classmethod
    def from_trace(cls, trace, *, anchors=None, digests=None, model=None, static=None) -> Reads
    @classmethod
    def from_dict(cls, data) -> Reads
    def to_dict(self, *, control=False) -> dict           # keys in file order
    def keyed(self, *, control=False) -> dict             # what rho sees: display values dropped
    def with_opaque(self, *names) -> Reads

@dataclass
class Entry:                                              # .nopekit/verdicts/<gate>/<rho16>-<out8>.json
    gate: str; rho: str; code: dict; spine: str; reads: dict; instruments: dict
    verdict: dict; digest: str; path: str
    name: str                                             # property: "<rho16>-<out8>"
    def body(self) -> dict;  def to_verdict(self) -> Verdict;  def read_set(self) -> Reads

@dataclass
class ControlEntry:                                       # .../control-<rhoC16>-<out8>.json
    gate: str; rho: str; static: str; static_parts: dict; host: str; fixture: dict
    reads: dict; bad: str; good: dict | None; admitted: str; detail: str
    measured: float | None; limit: float | None; units: str; digest: str; path: str
    bad_extra: list; mutation: dict | None; legacy: bool  # P2.3: the whole qualification
    name: str                                             # property: "control-<rhoC16>-<out8>"
    def body(self) -> dict;  def read_set(self) -> Reads

@dataclass(frozen=True)
class WriteResult:
    status: str           # "written" | "exists" | "kept-first"
    path: str; name: str; rho: str; warnings: tuple
    written: bool         # property

def write_entry(root, entry) -> WriteResult
def read_entries(root, gate_id, *, problems=None, instruments=None) -> list[Entry]
def record_verdict(root, spec, fn, verdict, *, trace=None, reads=None, anchors=None,
                   digests=None) -> WriteResult | None    # None: a skip or an error, never cached
def selftest_walk(owner_dir, *, digests=None) -> dict[str, str | None]
def control_static(spec, fn, root, *, digests=None, anchors=None) -> tuple[str, dict]
def write_control(root, entry) -> WriteResult
def entry_problem(entry) -> str                  # why the strict reader would refuse it ("" if none):
                                                 #   asked before filing; a refusal holds that gate
                                                 #   `control:unwritable`, never aborts the check run
def read_controls(root, gate_id, *, problems=None) -> list[ControlEntry]
def record_control(root, spec, fn, *, result=None, trace=None, host="live", bad=None,
                   detail="", digests=None, anchors=None, when="", good=None,
                   mutation=None) -> WriteResult | None
def remember(root, key, verdict, *, input_rho, kind, when) -> None
def remembered(root) -> dict      # {key: {input_rho: {"input_rho", "kind", "verdict": Verdict, "when"}}}
def forget(root, key, input_rhos) -> bool    # only the records at those rhos
def record_obs(root, gate_id, *, entry, when, duration_s, cpu_s, control=False) -> None
def read_obs(root, gate_id, *, control=False) -> list[dict]   # [{"entry", "when", "duration_s", "cpu_s"}]
# issue #15: which bytes a recorded run cited (untracked scratch)
EVIDENCE_MANIFEST_SCHEMA = 1
def write_evidence_manifest(root, gate_id, entry_name, evidence, anchors) -> str
                                             # the path; {portable path: {sha256, bytes}}
                                             #   of each regular file the run cited
def evidence_manifest(root, gate_id, entry_name) -> dict | None
                                             # {portable path: {sha256, bytes}}, or None:
                                             #   no recorded run here, junk, another gate/entry
def last_read_sets(root) -> dict[str, set[tuple]]    # {gate: {param path}}: Param.gates and why, never rho
```
**The code digest.** `code_digest(spec, fn)` is sha256 of canonical JSON of the
closure `modelio` recorded while the gate's module ran — the defining module's merged
with every registering module's (`modelio.code_closure`) — as `{portable path: sha}` (a
project gate's `gates/structural.py`, a pack's `<pack:NAME>/gates/mesh.py`; the bytes
that executed, S-26), the `canonical_ast_digest` of each `nopekit.*` module the
closure imports that `SPINE_MODULES` does not cover (cad and fdm import
`nopekit.site`: a page change re-runs their gates and nobody else's), the
`SPEC_FIELDS_IN_RHO` values, and the closure's `fallback`. Not `title`,
`description` (prose), `negative_control` (it is in `rho_control`'s static part) or
`entry` (discovery). A function with **no recorded closure** — a test's lambda, a gate
registered from Python — is digested as its **defining file**
(`fallback="defining-file"`; `doctor` names every one, and the CLI never makes one).
With no file at all (`<string>`, `exec`) it is `CodeRef(digest="", opaque="code not
loaded from a file")`: opaque, never a digest of nothing. A closure that recorded two
versions of one file, or an unreadable `nopekit.*` source, is opaque too. The sweep
passes its anchors; the default spells a pack gate's files under `<pack:NAME>` and
everything else under `<tmp>`/`~`.

**`Reads.from_trace` — the classification** (first match wins), for every path a
trace read (files and listed directories alike): 1. under the interpreter's prefixes,
site and user site, the nopekit package, `/proc`, `/sys`, `/dev`, or bytecode —
dropped; 2. read and then written in the window — opaque `self-modified:<path>`
(a path written first and read after never reaches the trace: the gate's own output);
3. under a pack — a read `<pack:NAME>/<rel>`; 4. under the sweep's or the control's
`out_dir` and not written in the window — opaque `out:<rel> (not written by this
gate)` (another gate's output is a cross-gate channel shaped like S-27); 5. under
`<root>/.nopekit/` — opaque `nopekit-state:<rel>`; 6. under the root — a read
`<rel>` (bare, posix); 7. anything else — opaque `file-outside-project:<path>`.
A path the trace only ASKED about (`stats`) and neither opened nor listed is a file
input under the root or a pack, like a read; asked while missing and made, asked while
there and removed, or asked and then written in the window, it is opaque
`self-modified:<path>` instead — the state the gate decided on is gone. Everywhere else
a question is not what a read is, because honest gates ask them constantly
(`shutil.which` stats every `PATH` entry, `realpath` every ancestor of the root,
`os.makedirs` every ancestor of the out dir): outside the root and the packs it is
dropped (named residual: a gate deciding on a file outside its project), and under the
out dir or `.nopekit/` a directory, or a path at or above what the window wrote, is
dropped while any other file or missing path is opaque like a read there. In a
**control** trace a read under the owner's `selftest/` is dropped: the static walk
keys it, and a fixture module's import-time read of `baseline.json` happens only on
its first load in a process. `static=` (the files the walk covered) narrows that to
exactly those files. Files are digested after the gate returns, through `digests`
(`util.FileDigests`), a directory as `DIRECTORY` and a missing path as `None` (a
directory was `None` too, so a named directory that appeared or vanished moved
nothing); a listing is the digest of its sorted `[name, "dir" | size in bytes |
"other"]` entries without `__pycache__` or bytecode — kinds and sizes because a gate
that iterates `os.scandir` decides on `os.DirEntry.is_file()` and
`os.DirEntry.stat()`, which the listing is the only record of. Mtimes are not in it
(they differ per checkout): a gate deciding on one is a named residual. `anchors`
defaults to `trace.anchors`, else to `<tmp>`/`~`
only — every project file then reads as outside, opaque: an unanchored trace is never
Fresh rather than wrongly portable. The trace's own channels pass through
(`subprocess:omc`, `network`, `param <path>: <Type> is not JSON`). A gate that used
`ctx.model` is keyed by `model=` (`model_digest(projection, loaded_model)`: the whole
projection and the model's closure) or, with none, opaque; a control that used it is
opaque. **Measured (R-4), every bundled baseline and control with trimesh, numpy and
omc present:** the only opaque channels are `subprocess:omc` on the three omc gates
(and their controls), plus `out:omc/ThermalTank.TankRun_res.csv` on
`modelica.simulates`, which reads the result omc wrote — already opaque through its
subprocess. What slipped through while measuring: `importlib.metadata`'s distribution
discovery (numpy.testing asks it at import) listed every `sys.path` entry for the
first mesh gate of each process; its frames are now excluded like the import
machinery's.

**`rho`** = sha256 of canonical JSON of `{"schema": 1, "gate", "spine", "code":
<code digest>, "params": [[path, digest], ...], "files", "dirs", "ledger", "model",
"opaque"}`, plus `"tier"` when the gate read `ctx.tier`. Canonical JSON everywhere in this module: `sort_keys`, compact separators,
`ensure_ascii=False`, `allow_nan=False`, no salt — anyone can recompute any digest
from this rule. Display values, instruments and a prerequisite's outcome are never in
it. **`out8`** is the first 8 hex of sha256 of `[passed, measured, limit, units]`: the
outcome, never the text.

**The entry file** is written once (`O_EXCL` semantics: the bytes go to a `*.tmp` file
in the same directory, which is hard-linked to the final name — a hard link fails when
the name exists, and the name only ever holds complete bytes; a filesystem without
hard links gets a plain `O_CREAT|O_EXCL` write). Keys in this order: `schema, gate,
rho, code, spine, reads, instruments, verdict, digest`; `reads` keys `params, files,
dirs, ledger, model, opaque` — with `tier` before `opaque` only when the gate read
`ctx.tier` (then rho carries it too); bytes `json.dumps(indent=2, ensure_ascii=False,
allow_nan=False) + "\n"`. The `verdict` block is a **whitelist**: `passed` (a bool),
`measured`, `limit`, `units`, `detail`, `evidence`, `locators`, `claims`, `tier`,
`pack` — `duration_s`, `cpu_s` and `rho` stay out (costs live in obs). `detail`,
`evidence` and locator strings are portable (`Anchors`), and evidence under no anchor
is dropped from the entry (the live row keeps it). `digest` = sha256 of canonical JSON
of every other field: integrity, not a forgery defence (R-9 is). Writer outcomes: the
same bytes — `"exists"`, nothing written; the same name with bytes differing only in
`instruments` — `"exists"` with a note (the same outcome under another library
version, §8); other bytes — `"kept-first"`, warning `"nondeterministic detail"`;
another outcome already recorded for this rho — written, warning `"two outcomes
recorded for identical inputs"` (equal instruments) or `"outcome differs across
instruments"`, and both files stay. `write_entry` computes `digest` itself and refuses
(`NopekitError`) anything its reader would refuse.

**The strict reader** (`read_entries`): not strict JSON (NaN, a duplicate key), a key
too many or too few, a non-bool `passed`, a non-number `measured`/`limit`, a name its
content does not produce, or a `digest` that does not match (`"hand-edited entry"`) —
the file is ignored (the gate is then a miss and runs) and one line naming it goes to
`problems`. Two outcomes for one rho are both returned, with a problem line; with
`instruments=` (this machine's), entries that differ only across instruments are
narrowed to the one recorded here, or none — a local re-run, never a pick.
`Entry.to_verdict()` is the verdict with its full `rho` and `duration_s = cpu_s = 0.0`.

**`record_verdict`** caches only a verdict that ran and passed or failed; a skip or an
error returns `None` and writes nothing (the caller `remember`s it). It computes the
code digest (`spec=None` or `fn=None`: `CodeRef.unrecorded()`, and the entry names
`code: ...` as an opaque channel — how `test_site` plants a verdict), the spine
digest, the reads (`reads=`, else `trace` through `Reads.from_trace`), rho and
`instruments_for`; writes the entry; and `forget`s the gate's remembered outcomes at
the entry's rho and at `""` — never at another rho (the sweep, which knows the rhos
current before its run at the tier it took, forgets those too).

**`instruments_for`** — provenance, never rho (Q1.3): for each module in
`requires_python`, the `python:` entries of `requires_one_of`, and the closure's
**static** third-party imports (`CodeRef.third_party`: installed or not importable —
a helper beside the project that is not installed is code, never an instrument), its `importlib.metadata`
version, `"unknown"` when importable without metadata, `"absent"` when not importable.
It never imports anything. Never from `import` audit events: those fire once per
process, so only the first mesh gate saw trimesh, and `--only` and a full sweep wrote
different bytes for one rho (packs:H3).

**Control entries** — `control-<rhoC16>-<out8>.json`, written once, keys: `schema,
kind ("control"), gate, rho, static, static_parts {spine, code {digest, files},
selftest {digest, files}, nc {fixture, expect, note}}, host ("known-good" | "live"),
fixture {digest, files}, reads {params, files, dirs, ledger, host, [tier,] opaque}, bad
("fail" | "pass"), good (null until P2), admitted ("reject-only" | "no"), detail,
measured, limit, units, digest`. `static` (`control_static`) = sha256 of the spine
digest, the gate's code digest, the owner's `selftest_walk` and the NegativeControl
fields. **`rho_control`** = sha256 of `{"schema", "gate", "static", "reads"}`. The
`fixture` block — the fixture's recorded code closure, its `data` included (spelled
with the code files; `NO_BYTES` holds while that file is still missing) — is a lookup
**hint, not an input**: the bracket's fixtures import the model, so keyed on it every
Config edit would write six tracked control files. Bytes that differ only in `fixture` are the
same control (`write_control` answers `"exists"`, no warning). A fixture whose code no
loader recorded — a `module:function` fixture the stock import served — files
`{UNRECORDED_FIXTURE: null}`; a `null` digest never matches, so that hint never holds
and every `check` re-runs the fixture alone (a cost, never a wrong admission). What
slipped through (admission review, round 1, C): it filed `{"files": {}}`, an empty
mapping never moves, and a fixture outside `selftest/` — so in no static part either —
defused 400 -> 40 mm was served `cached` while `gate selftest` said PASSED its own
known-bad fixture. Only the forged form (no trace) files `{}`. Host-param reads are
keyed only when the host was live (a known-good host is a design the fixture's own
`selftest/` files and code define — enforced, not assumed: `context` is handed nothing
of the live design, below).

**`selftest_walk(owner_dir)`** — the owner is the pack directory (`PACK_DIR` on the
gate's module, else on a module that registered it — `modelio.registered_by`) or the
project root. `vcs.ls_files(owner_dir, ["selftest"])`, asked of
the OWNER's repository (a bundled pack's is nopekit's), tracked plus
untracked-not-ignored; outside git, a walk. Both leave out `__pycache__/`,
`*.py[cod]` and dot-directories (openmodelica's `.generated/`), so the git and non-git
answers agree — verify.sh `--dir` copies have no `.git` (tests:H11), and a project with
no ignore rule for `selftest/__pycache__` would otherwise key its controls on
bytecode. Where git lists nothing and the walk finds files, the walk wins.

**`record_control`** — the sweep's form passes the `gates.selftest` result and its
trace: fired → `bad: "fail"`, admitted reject-only; the gate PASSED its known-bad
input → `bad: "pass"`, admitted `"no"` (both measurements, cached). A crash on the
fixture, an unusable fixture, a self-skip with the tools present or an availability
skip is `remember`-ed under `control:<gate id>`, keyed by the current `static` and the
path it failed on — `<static>@<tier>` when its run read `ctx.tier`, the bare `static`
(the path every tier shares) when it never looked (`_control_key`) — and returns `None`.
The forged form, `record_control(root, spec, fn, bad="fail", detail=...)` with no result
and no trace, writes an entry with empty reads — how a renderer test plants an
admission; it forges only the inner loop (R-9). Writing one `forget`s the gate's
remembered control failures at the entry's `static` on the shared path and on the path
its run read — the sweep's also on the tier it ran the control at — and at no other
static or path (`_answered_controls`). What slipped through (review, remembered control
failures by tier): keyed and forgotten by `static` alone, though rho_control keys the
tier — `check --tier 2 --force` crashed the costlier path's control, an edit sent the
next plain check down the cheap path, whose control fired and forgot that crash, and
with the edit reverted `check --tier 2` served the tier-2 control entry the crash had
superseded, admitted, and its PASS cached, while `--force` crashed again.

**Remembered outcomes** — `.nopekit/cache/last_outcomes.json` (untracked): `{key:
{input_rho: {"kind", "verdict", "when"}}}`, key a gate id or `control:<gate id>`,
`kind` `"error"`, `"self-skip"` or `"availability"`. Keyed by **`(key, input_rho)`** —
`input_rho` the rho computed from current digests just before the run, at the tier
the run took (the state's `input_rho`: a Fresh entry's, the recomputed rho of the latest
entry's read signature — of the latest one read at that tier when the state's own is
another's — or `""`), or a control's current `static` and the path it failed on
(`_control_key`) — never the failing run's own rho, which a crash at partial reads makes different from the PASS it followed, and
never another tier's entry's, which a crash on this path is not at the inputs of. One record per `(key, input_rho)`,
the newest, except that an `availability` record never replaces an `error` or
`self-skip` one: a check that could not run the gate answered nothing — and a key
keeps one `availability` record, the newest, since it supersedes nothing. A pass or fail
clears only the records at the inputs it was measured at (`forget(root, key,
input_rhos)`): the sweep's `_superseded` — the new entry's rho, `""`, and every rho
current before the run at the tier it took (`here`) — `record_verdict`'s the entry's
rho and `""`, a control entry's its `static` on the shared path and on its own
(`_answered_controls`). What slipped through (remembered outcomes, round 1): one record
per gate, forgotten by a pass or fail at ANY rho and overwritten by an availability
skip — a crash at A, then a PASS at B (or a check without the tool), and back at A
`check` read `0 executed, 6 cached` and served the PASS the crash had superseded; the
same for a control crash and a control entry at another `static`. No cap on crash records:
the one a cap evicts is that PASS again. Remembering a pass or a fail is refused:
nothing remembered is evidence. An unparseable file — the one-record-per-gate shape
included — raises, naming it: read as empty it would hand the next check the PASS a
crash superseded.

**Obs** — `.nopekit/obs/<gate>.json` and `<gate>.control.json` (untracked), each
`{"gate", "kind", "runs": [{"entry", "when", "duration_s", "cpu_s"}]}`, the last
`OBS_KEEP`. Split because the run history mixed `<gate>#selftest` rows with sweep rows
and no latency reader filtered them (S-31). A file naming another gate or kind — gate
`x`'s control and a gate called `x.control` share one name — reads as no runs and is
replaced on the next write: never a mixed series.

**The evidence manifest** — `.nopekit/cache/evidence/<gate>/<rho16>-<out8>.json`
(untracked, issue #15): `{"schema": 1, "gate", "entry", "files": {portable path:
{"sha256", "bytes"}}}`, written atomically by `_sweep_one`'s step 4 right after
`write_entry`, only when the sweep records and the outcome is measured. The keys are
the entry's own `evidence` spellings (`Anchors.portable_path`); a directory, a missing
file or a path under no anchor is left out. A later recorded run of the same entry
name (same rho, same outcome tuple) replaces it, since its files are as much that
entry's evidence. A cache hit, `--no-record`, an error, a skip, a control and a
mutation write none. `evidence_manifest` reads it back strictly: a file that does not
parse, has other keys, or names another gate or entry is `None`. The site copies a
file as an entry's evidence only when its bytes are the manifest's
(`page.evidence_plan`). *Rejected:* the digests in the tracked entry (`out8` hashes
the outcome only and the body is kept-first, so a timestamped log would warn on every
forced run); obs rows (`read_obs` refuses other keys, obs is never truth, and it
keeps 20 rows); writing it from `cli.check` after the sweep (a later gate's write
would land in an earlier gate's manifest).

**Part three: freshness, admission state, the one resolver.** None of it runs a gate
or a fixture; all of it reads the cache and what is on disk now.
```python
MAX_STALE_REASONS = 3         # moves named per stale gate, then "(+n more)": one gate, one line

@dataclass(frozen=True)
class Fresh:   entry: Entry; notes: tuple; rho: str; current: frozenset;      # state = "fresh"
               path: frozenset; here: frozenset; input_rho: str
@dataclass(frozen=True)
class Stale:   entry: Entry; reasons: tuple; rho: str; current: frozenset;
               conflict: tuple; here: frozenset; input_rho: str               # state = "stale"
@dataclass(frozen=True)
class Unknown: entry: Entry | None; reason: str; rho: str; current: frozenset;
               here: frozenset; input_rho: str                                # state = "unknown"
@dataclass(frozen=True)
class Never:   ...                    # state = "never"; entry None, rho "", input_rho "", current
                                      #   and here frozenset()

def freshness(root, registry, projection, ledger, *, digests=None, anchors=None,
              model=None, tier=None) -> dict[str, Fresh | Stale | Unknown | Never]   # every registered gate

@dataclass(frozen=True)
class Admission:
    state: str            # "admitted" | "pending" | "not-admitted" | "undemonstrated"
    entry: ControlEntry | None
    reason: str           # not-admitted: the judge's token (P2.3); else why, or ""
    executed: bool        # a fixture or control ran to decide it: never, from admission_state
    reverified: bool
    qualification: QualificationFacts | None   # P2.3: the facts it was decided on
    moved: tuple          # pending: the fixture-closure files that moved
def admission_state(root, spec, fn, *, projection, ledger=None, digests=None,
                    anchors=None) -> Admission

@dataclass(frozen=True)
class Row:
    gate: str; state: str           # "fresh" | "stale" | "unknown" | "never" | "legacy" | "orphan"
    cached: bool; fresh: bool; stale_reason: str; entry: Entry | None
    admission: Admission | None; when: str; notes: tuple
@dataclass
class Resolution:
    verdicts: list[Verdict]         # registered gates in registration order, then orphans by id
    stale_gates: frozenset[str]     # Stale, Unknown, undemonstrated, legacy, orphan
    rows: dict[str, Row]; notes: list[str]
    read_sets: dict[str, set[tuple]]   # last_read_sets: Param.gates and why (S-30)
    anchors: Anchors | None            # what the entries were judged against (watched_paths)
    standings: dict[str, Standing]     # P2.5a: judge_results; reaches compose through view
    track: dict[str, tuple[Contradiction, ...]]   # P2.5a: track_record, per evaluator
def resolve(root, registry, projection, ledger, *, model_error="", availability=None,
            digests=None, anchors=None, now="", model=None) -> Resolution
def apply_prerequisites(resolution, registry) -> Resolution   # rung 7: the rule over a resolution
# P2.5a: the article a physical result is bound to, the judge, the one view
def article_of(root, projection, model, *, anchors=None, digests=None,
               resolution=None) -> dict          # {} when the model does not load
def design_of(root, projection, model, *, anchors, resolution=None,
              digests=None) -> dict | None       # phase 3a: article_of's built_from — {params,
                                                 #   model, files} — extracted; None when the
                                                 #   model does not load; no git call
def judge_results(ledger, here) -> dict[str, Standing]   # resolve's rung 8, never cached
class Contradiction(NamedTuple): gate; code; claim; article; when; who; channel; value;
                                 measured; units; removed   # removed: review of P2.5a
def track_record(ledger) -> dict[str, tuple[Contradiction, ...]]   # every results file's fails' contradicts
def view(ledger, resolution) -> Ledger    # THE view builder: verdicts + each claim's standing,
                                          # + each Ledger.removed claim holding a fail
REMOVED = "removed"                       # the Standing.state of such a claim
# P2.5b: the exported article
class ExportedArticle(NamedTuple): article; written; outside; error; params; linked = ()
def export_article(root, projection, fn, *, out_dir, anchors, digests, model=None,
                   milestone="", when="", resolution=None) -> ExportedArticle
                                          # runs a milestone's generator traced: the article
                                          # is what it READ (traced) or the design (not)
```
**The exported article (P2.5b-D11, D12).** `export_article` runs a milestone's
generator as a gate is run — `GateContext` at tier 3, traced — and the article is what
it read: `built_from` the reads (`Reads.from_trace`), `traced: True` when nothing
opaque was read, its code is recorded and no model was used. Untraced, it is
`article_of`'s whole design (over-predicts, never under). A pass on an exported
article is judged by `_traced_moves`: only a moved read moves it, so a change no
generator read rebuilds nothing (`claims.rebuild` names that article alone). A pass
on one counts only while `exports/` holds it (`_fact_export`). What `outside` names
is a write outside the package's scratch (refused, named, never undone); `linked`, a
symlink or a hard link the generator put in its directory (refused: a package holds
bytes, never a second name for a project file). A file in the package that no write the
trace saw put there makes the article untraced — the whole design (review of P2.5b: the
backstop behind the link handlers). The scratch is `.nopekit/out/export-<m>/` in both
modes, under the trace's out directory, so the generator's own directory is never a
read (review of P2.5b: built in `out/.<m>.tmp-<pid>/`, a `makedirs` of it put the pid
into the article).

**Supersession (P2.5b-D14).** A fail stops counting when a later pass stands
(`EntryStanding.stands`) on an exported, traced article B other than the fail's A,
A moved, B differs from A on a row A recorded (`_differs_on_recorded`), and what was
printed from B is not what was printed from A — no package of B carries a package of
A's generator bytes (`EntryStanding.built`, `_built_seal`; review of P2.5b: a no-op
line in the generator moved its code row, and the same print reprinted byte for byte
released its fail) — `_supersedes`; never a judgment's (a person, not a print) or an
assumption's. The
fail is kept (`Standing.superseded`, its contradiction too) and named in every
channel; `view` releases a claim's `physical_result` only to the counting fail or to a
standing pass, so nothing else reaches it. What slipped through before it: a fail on
a print the design no longer described blocked its claim forever, so a reprint after a
fix could never clear it.
**`apply_prerequisites`** (P2.2-D7, D8) is `resolve`'s last rung and `check`'s again over
the view it merges with its sweep (`cli._swept`). It walks `gates.plan`'s order over
every registered gate, building a `gates.Reading` per gate from the resolution (current
unless in `stale_gates`), and asks `gates.prerequisite_root`, the decision the sweep
asks too. Under a NEGATIVE root the gate's own reading becomes `gates.blocked`'s skip,
unless it stands — its own crash or two outcomes, always (invariant 2: a crash keeps its
loudness); its tool missing here, a self-skip or its own refusal, unless the root crashed
(invariant 10: as loud as a crash); a prerequisite skip the rule already made, saying
what it says now (`_under_rule`, the one copy: `_pruned_row` and `run_all`'s default ask
it too) — with its row
`cached`/`fresh` False, its `entry` kept (D-04: still Fresh on disk, served the moment
the root recovers) and out of `stale_gates`. Under a NOT-CURRENT root a pass or a fail
is kept, marked (`_marked`: `fresh` False, `stale_reason` `prerequisite <root>
invalidated: <what moved>` / `prerequisite <root> unrun`) and added to `stale_gates`,
so its claim reads Stale or what ranks above it — **D-03 amended**: read literally,
every model edit between check runs would turn every guarded claim Skipped, when the
fact is "inputs moved; run check". Monotone (only toward not-pass) and idempotent (a
second application moves nothing; a row carries one `not run:` note, the current one);
with no `needs` registered the resolution comes back as it went in. It reads no
availability: a missing tool is in the gate's reading already.
**`freshness`** groups a gate's entries by read signature — the paths, files, listings,
claims and opaque channels an entry read, without their values; a presence-only read is
its own kind of address — and recomputes each group's rho from what is current: the
spine digest, the code closure of the LOADED module (a newly added import counts), the
params at each recorded path in `modelio.flat_params(projection)` walked by
`ParamTrace`'s own leaf/presence/bulk rules (a miss is `ABSENT`), file and listing
digests through `digests`, the claim records. An entry whose rho is the recomputed one
is **Fresh** — its `current` is every signature's rho recomputed now, its `path`
those on the served entry's path (the signatures read at its tier, or that never read
`ctx.tier`): what a remembered crash must be at to supersede it — and an
instruments difference is a note (`recorded under trimesh 4.0.0; here 5.1.0`), never
staleness (Q1.3). **Unknown**, never Fresh: the projection is `None` and the entry read
params (S-21: a model that failed to import used to turn staleness off — status said
"unchanged" and listed three PROVEN claims for a design that could not be built); any
opaque channel (`opaque inputs: <channels>`); a spine digest of `""`; code that cannot be
keyed; a file under a pack this checkout has not loaded; a `ctx.model` read with no
`model=` given. Otherwise **Stale**, on the latest entry (obs `when`, then the entry's
commit time, then the name) with its reasons — `config.bed_xy 220.0 -> 250.0` from the
recorded small value and the current one (`changed` when either is not small, `absent`
for a missing key), inputs (`config.*` and config fields) first, then derived values
**only when no input moved** (a derived value that moved with an input is that input's
consequence: `bracket.bed_fit` also reads `usable_bed`, and the transcript's line names
the one cause), then the model, claims, files, listings, `gate code changed`, `nopekit
spine changed`; at most `MAX_STALE_REASONS`, then `(+n more)`. Two outcomes at the
current rho with equal instruments are Stale with `conflict` set; with different
instruments the entry recorded under this machine's wins, else a local run decides.
Nothing global is compared any more: an unread datasheet ingested moves no gate (S-33).
**The tier a gate read** is part of its entry's address as a value, never re-read:
`freshness(..., tier=N)` — the sweep's `max_tier` — makes a group recorded below N
Stale (`ctx.tier 0 -> 2`; its rho now is the address a run at N would have) and serves
one recorded at N or above, the highest tier first (`_most_thorough`); a reader, with
no tier of its own (`tier=None`, `resolve`), serves the highest recorded. A gate "must
not use [the tier] to lower its own standard", so the costlier path is the more
thorough answer to the same question. *Rejected:* exact match only — a tier-0 `check`
would then serve the cheap PASS over the costlier FAIL that `status` shows. Each
signature's tier (the one it read, raised to the sweep's when below it) also says whose
inputs its rho is (`_by_tier`): every state carries `here`, the current rhos at the asking
sweep's tier or of a signature that never read it — what a pass or fail of that sweep
answers — and `input_rho`, what a crash of that sweep is remembered under: `rho` when the
state's own signature is at that tier, else the latest entry's among those that are (a
current one first), else `""`. A reader's `here` is `current` and its `input_rho` its
`rho`; for a gate that never reads the tier, `path` and `here` are `current` and
`input_rho` is `rho`. What slipped through (review, remembered outcomes by tier): a
tier-0 check serves a tier-2 entry, and the sweep remembered and forgot under that
entry's `rho` and every signature's `current` — a cheap-path PASS forgot the tier-2
crash and `check --tier 2` served, cached, the PASS it had superseded; a cheap-path
crash was filed under the tier-2 entry, so an edit to an input only that path reads
dropped it, and the next plain check served the tier-0 PASS, cached, for a path that
had just crashed at inputs that never moved.

**`admission_state`** — is the gate's control demonstrated at its current version, from
records alone (only `check` may spend a fixture's time). A candidate is a control entry
whose `static` equals the current one (`control_static`); it is *current* when it has
no opaque channel, its file and listing digests are current, and — when its fixture got
the LIVE host — its host-param reads match `projection`'s flat params and its ledger
reads are vouched for by `ledger` (the live one; `_ledger_moved`): every key re-read
equals what the entry recorded, or equals the live ledger a run or re-verification of
this control recorded in `controls.json` under the fixture closure still on disk. What
slipped through (admission review, round 1, A): `reads.ledger` was keyed in
rho_control and never compared — the admission `_Now` had no ledger — so a gate taking
its limit from `ctx.ledger.claim("C1")`, C1 relaxed from 100 to 500 mm, kept a control
admitted whose 400 mm fixture it now passed. Not a plain comparison with the live
ledger: a fixture may hand its gate a ledger of its own (openmodelica's `Ledger()`),
whose digests no live ledger with claims ever equals. With no `ledger` given, a
live-host control that read one is not current. A known-good host's ledger is not
compared: `context` is handed an empty one, so what its gate reads comes from its code
(the hint) and the files it opens in the control's window (keyed). Among current
candidates, those whose fixture closure (`fixture.files`, the lookup hint) is unchanged
decide first: all fired → `"admitted"`; one PASSED its known-bad input →
`"not-admitted"`, `PASSED its own known-bad fixture <ref>`; they disagree →
`"not-admitted"`, `two control outcomes recorded for identical inputs`. With no
unchanged-closure candidate, the current ones decide the same way, but a fired control
is `"pending"` — `control inputs moved (<files>); the next check re-verifies` — and
counts: the bracket's fixtures load its model, so a `bed_xy` edit moves every closure
without moving a control value. A control whose run read `ctx.tier` shows only the
path its tier picked: admission is asked AT the tier whose path the counted verdict
takes — the entry's recorded tier in `resolve`, the tier a sweep runs the gate at
(or a served entry's) in `check` — and with no current control at that tier, or one
that never read it, the gate is `"undemonstrated"` (`no control shown on the path
ctx.tier <t> picks`); `check` runs the control at its own tier on that miss, never
above its ceiling. Current controls of every tier decide together: when they
disagree the reason is `control outcomes differ by ctx.tier (<names>)` — a gate that
passes its known-bad input on one tier's path is a logger on every path (review round
1, `probe.tier`: rho_control never keyed the tier, and a control shown on the tier-0
path admitted a tier-2 path that passed a 400 mm span). A remembered control crash, unusable fixture or
self-skip at this static (`control:<gate>`), on the path every tier shares or on the
path `at` picks — on every path when `at` is `None`, a verdict that never read the tier
(`_control_failures`) → `"not-admitted"`, `control <kind>: <why>` (an availability skip
is not held against it). A control crash on the tier-2 path says nothing about the
tier-0 path's control, which fired: a tier-0 crash never refuses the tier-2 PASS whose
own path's control stands, nor a tier-2 crash the tier-0 one. No current candidate →
`"undemonstrated"`. A candidate whose closure moved but that a sweep re-verified by
its values under the closure as it is now (`CONTROLS_CACHE`, part four) counts as an
unchanged one: after a `check` the note goes, until the next edit.

**`resolve`** — the ONE effective-verdict producer every reader uses (R-5). Per
registered gate, in registration order, the first that applies:
1. `availability(spec)` (default `gates.availability`) fails: a skipped verdict with the
   reason, `cached pass exists; <reason> here` when a Fresh PASS exists (invariant 1: a
   PASS committed where trimesh is installed never reads PASS where it is not). A Fresh
   FAIL is still served (R-3).
2. A remembered crash or self-skip that supersedes the Fresh entry (its `input_rho` is
   a rho current now on the served entry's path, `Fresh.path`: the entry's or another
   signature's read at its tier or at none — a record at other inputs, or on a cheaper
   tier's path, never does) or, with none Fresh, is displayed (`input_rho` `""`, or a rho
   recomputable now, or the gate has no entry at all — never "never run", S-68).
   Invariant 2: a crash proves nothing, and neither does the PASS it followed.
3. A Fresh entry under admission (PD-08, X14): PASS + admitted or pending counts;
   PASS + undemonstrated is stale, `control not demonstrated at this version — run
   nopekit check` — `… run nopekit check --tier <t>` when the entry's run took the
   path `ctx.tier` t ≥ 1 picks, since a check below t judges that path's control by the
   records alone and never runs it (`_undemonstrated`; review, `repro_undemonstrated`:
   the plain words sent the reader to a check that served the same stale row forever);
   not admitted is an error `not admitted: <why>`; a FAIL stays FAIL
   (undemonstrated, it is also listed stale) — admission gates what may COUNT as a pass.
4. The latest entry, stale with its reasons, or Unknown with its reason (`model_error`
   joins "the model does not load"). Two outcomes: an error while
   `TWO_OUTCOMES_IS_ERROR` is True (since U25), stale were it False.
5. A `ledger.verdicts` row with no rho: stale, `recorded before per-gate tracing` (Q1.4).
6. Nothing: no row — the claim reads PENDING.

Then orphans — entries, remembered outcomes or legacy rows of gates not registered —
sorted by id, stale `gate not registered in this project` (tests:H2). A registered
gate's verdict carries its spec's claims, tier and pack (the spec is the authority on
identity). The ledger is read, never written: callers lay the resolution over it as a
view and never save it. `notes` gathers ignored (hand-edited) entries, instrument
mismatches, opaque channels, two outcomes, pending admissions and defining-file digests,
one line each (`<gate> — <note>`).

**Part four: admission at its current version, the sweep, `last_check.json`.** What
`check` runs. It may run a control (fixture and gate) or a fixture alone; it never
takes the lock, and a `when` arrives from the CLI.
```python
CONTROLS_CACHE = ".nopekit/cache/controls.json"   # re-verified fixture closures (+ the live
                                                   # ledger a live-host control was vouched under); untracked, a hint
WATCHED = ("claims/**", "params/**", "decisions/**", "needs/**", "inputs/**", "results/**",
           "views/**", ".nopekit/verdicts/**", "model/**", "gates/**", "selftest/**",
           ".nopekit/project.json", ".nopekit/packs/**", "objectives.json")

def known_good_context(root, ctx) -> GateContext | None   # <root>/selftest/known_good.py's context(ctx)
def admission(root, spec, fn, host_ctx, *, may_run=True, force=False, record=True,
              projection=None, digests=None, anchors=None, when="") -> Admission

@dataclass(frozen=True)
class SweepRow:
    verdict: Verdict; executed: bool; cached: bool; fresh: bool
    stale_reason: str; rho: str; admission: Admission | None
    disagrees: tuple = ()           # P2.5b, LAST: what a forced re-run found that the
                                    #   records it overrode did not say: ("outcome", out8,
                                    #   before, after, rho) | ("records", why) |
                                    #   ("qualification", why)
@dataclass
class SweepResult:
    rows: list[SweepRow]            # one per selected gate, registration order
    counts: dict                    # {"executed", "cached"}
    controls: dict                  # {"executed", "cached", "reverified", "not_admitted"}
    not_run: list[tuple[str, str]]  # (gate, "above the tier ceiling" | "excluded by --only")
    before: dict                    # freshness(), computed before anything ran (cli:H19)
    stale_before: dict[str, str]    # selected gates Stale or Unknown before the sweep: the reason
    notes: list[str]                # writer warnings, ignored entries; `check` prints them
    only; max_tier: int; force: bool; record: bool; when: str   # how it ran
    ledger; registry; anchors       # what write_last_check needs
    order: list[str]                # the ids in RUN order (gates.plan); rows keep registration order
def sweep(root, registry, ctx, *, projection, ledger, max_tier, only=None, force=False,
          record=True, now="", on_verdict=None, anchors=None, digests=None,
          on_row=None) -> SweepResult

def write_last_check(root, result, resolution, *, now, params=None,
                     digests=None) -> str | None   # None: a filtered or dry sweep writes nothing
def watched_paths(root, resolution=None) -> list[str]   # absolute, sorted
def fingerprint(root, paths, *, digests=None) -> str
```
**`admission`** is `admission_state`'s judgement, allowed to run what the records
cannot settle (§3.8). 1. *Static*: candidates are control entries at the current
`static`; none is a miss. 2. *Current*: no opaque channel, file and listing digests
current, and — the fixture having had the LIVE host — its host-param reads match
`projection`'s params (else the host context's). 3. *Hint*: current candidates whose
live-host ledger reads the host context's ledger vouches for (as `admission_state`'s)
and whose fixture closure is unchanged, or was re-verified under the closure as it is
now (`CONTROLS_CACHE`), settle it with nothing run — all fired: `admitted`; the latest
PASSED its known-bad input: `not-admitted`, `PASSED its own known-bad fixture <ref>`;
they disagree: `not-admitted`, `two control outcomes recorded for identical inputs`.
4. *Re-verify* (`may_run`): otherwise the fixture runs ALONE (`gates.run_fixture`),
traced, in the control `out_dir`, and what it built is compared with each current
candidate's recorded reads — params by digest (`ABSENT` for a miss), ledger reads. A
match settles it as 3 does with `reverified=True`: the gate is not called and no
tracked file is written; `controls.json` remembers the closure — and, for a live-host
control that read the ledger, the live ledger it matched under, so a live ledger that
differs from the recorded one costs one fixture run, not one per check (a full run
records it the same way). On a LIVE host a match also needs every read of the live
design the control would make now to be one the entry keys: each host param read —
the fixture's own, and its gate's through a host view the fixture handed back, which
the fixture-only run replays through the same views (`_host_reads_with_gate`) — in
`reads.host` at the same digest, and each ledger key the fixture read in
`reads.ledger`. What slipped through (admission review, round 2, `r3`): a sealed
fixture's entry keyed no host read; edited to five times the live span, to the
literal identity fixture, or to four times the live C1 limit — every value built
still equal — it was vouched for, and a later edit of the live design that defused it
was compared with nothing: `check` exit 0, C1 PROVEN, `gate selftest` PASSED its own
known-bad. The comparison is
sound only for what an entry keys, so a fixture that writes files, moves `ctx.root`,
`out_dir` or `tier`, hands its gate `ctx.extra`, a `ctx.model` or a `ctx.memo` other
than the one it was handed (or fills the memo it was handed — through its private
slot: a `SweepMemo` refuses the fill itself, and the control is unusable), reads a file the entry
does not key, or touches an opaque channel sends the control to a full run instead —
a cost, never a wrong admission (what would have slipped through: a fixture writing the
known-bad mesh its gate reads, which the gate's trace drops as its own output). This
is the early cutoff: a Config-default edit moves every bracket fixture's closure (they
build through the model) but no control value — six fixture runs, zero controls
executed, zero new files — while an edit to the model's `build` that moves a control input misses
and re-runs it, and one that defuses it is not admitted (S-19's model-code half).
5. *Miss*: the control runs, fixture and gate, and is filed unless `record=False`:
`bad: "fail"` admitted reject-only; `bad: "pass"` not admitted. A crash, an unusable
fixture (a `known_good.py` that will not load included) or a self-skip with the tools
present is remembered under `control:<gate>` at the current static and the path it
failed on — `not-admitted`, `control <kind>: <why>` — and a remembered one at this
static, on this path or the shared one, is re-run, never served (the control analogue
of §3.9's supersede); a control entry written at another static, or on another path,
leaves it standing. One on a path this run does not take — only at `at=None` — is the
answer, and nothing runs (a `note:` names `run nopekit check --tier <t>`); under
`force` the control still runs, and the answer is still that failure. A new outcome at the `rho_control` of a
cached entry with the other `bad` is `not-admitted`. 6. *`force`* skips 1-4, and an
outcome that differs from a cached entry at the same `rho_control` is `not-admitted`,
`control outcome differs from its cached entry` (R-9). With the gate's tools missing
nothing runs: the records answer.

**The known-good host (D-27, S-07).** A PROJECT gate's fixture is handed
`known_good_context(root, host)` — the `context` function of `selftest/known_good.py`,
loaded through `modelio.load_source_module` and called, inside the control's trace
window, on the host with no params, an empty ledger, no `extra` and no model
(`_KNOWN_GOOD_BLANK`: only `root`, `out_dir`, `tier` and the log sink come through) —
and the entry says `host: "known-good"` (its host reads are not keyed: they are reads
of a design the owner's `selftest/` and code define, and a file `context` opens is a
control read like any other). What slipped through (admission review, round 1):
`context` was called on a copy of the LIVE host, outside any window, so one that kept
`ctx.params` made the known-good design the live one with nothing keying it — the
identity fixture "fired" on a failing shelf, and after a model edit to a passing one
`check` served that control cached, exited 0 and `report` listed the claim PROVEN.
*Rejected:* filing such a control `live` whenever it read the host — every bracket
fixture reads the known-good params through its host, so every Config edit would re-run
all six controls (D-27's whole-value dependency). A pack's fixture gets the live host
(`host: "live"`, host reads keyed); SEALED is the seal detector's job. So the literal identity fixture `return ctx`
hands its gate the known-good design, passes, and is not admitted — on the live
bracket, which fails on purpose, it "fired" and certified nothing. The known-good
module's code closure joins the fixture's lookup hint. Each control gets its own memo
(a fresh `SweepMemo`) and its own emptied scratch, `<root>/.nopekit/out/controls/<gate>/` (never the
sweep's `out_dir`).

**`sweep`** is `check`'s loop, driven through `gates.run_all(..., before=)` so order,
the tier ceiling, `--only` and the lost-control refusal stay in one place; a gate
named above the ceiling runs, control included. `freshness` is computed first.
0. *Plan* (P2.2): the selection and its prerequisite closure run in `gates.plan`'s order
(`SweepResult.order`); `rows` keep registration order (D4: `check --json`, JUnit and
every tie-break unmoved). A gate whose prerequisite is not established is **pruned**
(`_pruned_row`): its row is what `resolve` reads of it under the rule —
`_resolve_gate`, `resolve`'s rungs 1-6 extracted, on the gate's records as a reader
(no tier) judges them, then `_under_rule` — and nothing below runs for it, its control
neither; it writes nothing (no entry, remembered outcome, obs or control), and its row
is neither executed nor cached. A gate whose prerequisite is not current (a costlier
tier's entry served stale) runs as below and its row is then marked stale (`fresh`
False). Per selected gate that is not pruned: 1. *availability* fails — a Fresh FAIL is served (R-3); otherwise
skipped, `cached pass exists; <why> here` over a Fresh PASS (invariant 1), remembered
as `availability` (never over a crash or self-skip at the same rho); no control runs (CI has no trimesh: a skip, never "not admitted").
1b. unless `force`, a remembered crash or self-skip standing over a Fresh entry of a
costlier tier, on that entry's path, with none standing at this sweep's own tier
(`here`): the row is that crash, as `resolve` reads it, with a `note:` naming the path
and `run nopekit check --tier <t>` — nothing runs, since no run at this tier is made at
that path's inputs or answers it. What slipped through (review, remembered outcomes by
tier): the cheap path ran instead, and its PASS was the row — `check` ready while every
reader showed the crash — and forgot the tier-2 crash, so `check --tier 2` then served
the PASS that crash had superseded.
2. *admission* — not admitted: `error="not admitted: <why>"`, `fn` never called; under
`force`, over a Fresh entry of a costlier tier whose own path's control stands, a
`note:` names that entry as what `status` and a plain check serve (`… its control ran
at tier 0 (…), on that path only; …`). A
Fresh entry of a costlier tier is judged at its own tier by the records alone;
undemonstrated there, the row is what `resolve` serves — the entry's verdict, cached,
`stale_reason` `control not demonstrated at this version — run nopekit check --tier
<t>` — and `check` keeps the gate in `stale_gates`. What slipped through (review,
`repro_undemonstrated`): the sweep made that a skipped row and `_swept` dropped the gate
from the stale set, so with a second gate passing on the claim it read PASS (partial) —
`check --junit` exit 0, `last_check.json` saying pass — while `status` read it STALE. 3.
unless `force`, a *Fresh* entry is served — unless a remembered crash or self-skip at
a rho current now superseded it (§3.9), which re-runs the gate; *two outcomes* at the current rho
(a Stale `conflict`) are served as `resolve`'s error, `two outcomes recorded for
identical inputs (<names>)`, and `fn` is not called — under the same supersede rule
(`_crash_applies`, one predicate for `resolve` and the sweep). What slipped through:
only Fresh was served here, so a conflict re-ran on every check and `check` showed that
run's answer — `[ok  ]`, ready, exit 0, a green JUnit, `last_check.json` saying pass —
for a claim `status` and `doctor` FAILed. 4. *run*, traced with the sweep's
anchors: a pass or fail is keyed and cached (clearing the remembered outcomes at the
inputs it ran on at the tier it took, `_superseded`, and no others), anything else
remembered under the rho computed before the run at that tier (the state's
`input_rho`); every run appends obs. A pass or
fail landing where the other outcome is recorded at its rho under the same instruments
(`--force` over a conflict, or a run that just made one) is filed, and its row is that
same error (`_contradicted`, asked of the writer's `_siblings`), recorded or not — an
entry with an opaque channel excepted, which `_judge` never matches either. 5. A run
over a current answer — `--force`, or a crash that superseded it — is one entry beside
that answer: the gate is re-judged at the sweep's tier with the run's entry filed (in
memory under `record=False`), and where the records resolve to another outcome — a
Fresh entry of a costlier tier (`_most_thorough`), or two outcomes, or the same outcome
from a costlier entry whose own tier's admission does not count (stale, or not
admitted) — that is the row, under its own tier's admission and with a `note:` saying
so (`… and it stands (PASS, not current)`; `_outranked`); and where a remembered crash
the run did not answer still stands over that entry (a costlier tier's path), the row is
that crash (`… supersedes the entry <name>, and it stands — run nopekit check --tier
<t>`). A run's own crash stays its row: never laid under a costlier PASS (invariant 2)
— the louder reading, filed at this path's inputs, which that PASS is not at — and a
`note:` names the costlier entry `status` and a plain check serve beside it (`… ran at
tier 0 (ERROR), on that path only; the tier-2 entry <name> at these inputs is the more
thorough answer, …`). What
slipped through: `check --force --junit`, CI's invocation at tier 0, re-ran a gate that
reads `ctx.tier` on its cheap path and laid that PASS over the tier-2 FAIL every reader
served — exit 0, a green JUnit, `last_check.json` saying pass. The
gate runs INSIDE `before`, not in `run_all`'s own loop: that loop's trace carries no
anchors, and a path-valued param digested without them (fdm's absolute mesh paths,
packs:H6) differs per checkout — never Fresh anywhere, rewritten per clone. A row is
`fresh` when its verdict is a pass or fail keyed at the current inputs. `record=False`
writes nothing under `.nopekit/` but scratch in `out/`: no entry, control entry,
remembered outcome, obs, `controls.json` or `digests.json` — and since nothing global
is compared, a dry sweep reads nothing stale (S-32). A first sweep filtered by `--only`
goes stale like any other (S-20): there is no clock to not advance. `digests` defaults
to the `.nopekit/cache/digests.json` stat cache, saved after a recorded sweep.
`on_verdict` streams each verdict, `on_row` its row.

**`last_check.json`** (`.nopekit/cache/`, untracked; `write_last_check`, after a FULL
RECORDED sweep only — a filtered or dry one returns `None` and writes nothing) holds,
in this order: `when` (the CLI's stamp), `spine`, `fingerprint` (of `watched_paths`),
`reads` (per gate, the reads of the entry the resolution used: `param:<json path>`,
`file:<path>`, `dir:<path>`, `ledger:<key>`, `model`, `tier`, `opaque:<channel>`),
`statuses` (every claim under the resolution), `counts` (the sweep's, with
`controls`), `worst` (the first blocking claim, its explaining gate and words; nulls
when nothing blocks), `params` (from 1.3) and `influence` (P3), empty until then.
Nothing in the sweep reads it. **`watched_paths`** is the `WATCHED` files (a single
file listed whether or not it exists: its appearing is a change), the files, listings
and code files of every entry the resolution used, and each loaded pack's `gates/`
and `pack.json`; `__pycache__`, bytecode, dot-files, `*.tmp` and `*.lock` are never
watched. **`fingerprint`** digests the spine digest and `{path: digest}` (a directory
by its listing, a missing file `null`): the one question P3's Stop hook asks without
importing a project's code.

### `gates.py`  (deps: models, util, modelio, verdicts)
```python
@dataclass
class GateContext:                                      # the full surface: docs/PACK_FORMAT.md
    root: str; ledger: Ledger; model: Any | None; params: dict
    out_dir: str; tier: int; log: Callable[[str], None]; extra: dict
    pack: str; key_scope: str                           # stamped by run_gate from the spec
    memo: SweepMemo | dict | None                       # the sweep's file memo (run_all): load_file's alone
    trace: GateTrace | None                             # the trace this view records into (run_gate)
    def scopes(self) -> list[str]                       # ["fdm", "fdm-print"]: key namespaces, best first
    def param(self, name, default=None, *, scope=...) -> Any   # PACK-SCOPED first: see below
    def pack_param(self, name, default=None) -> Any
    def first_pack_param(self, names, default=None) -> Any
    def first_pack_param_named(self, names, default=None) -> tuple[Any, str]
    def require_param(self, name) -> Any                # raises rather than compare with None
    def acceptance(self, claim) -> Acceptance           # P2.4: the goalpost of the claim an id or tag
                                                        #   names; recorded `acceptance:<claim>`
    def out_path(self, *parts) -> str                   # an evidence path under out_dir, dir created
    def with_extra(self, extra) -> GateContext          # a copy with `extra` merged over
    def load_file(self, path, loader=None) -> Any       # once per sweep; it and all the loader opened: reads of THIS gate, hit or miss
def scope_of(gate_id: str) -> str                       # "fdm.bed_fit" -> "fdm"
SCOPE_SEP = "."
class Registry:
    pack_dirs: dict[str, str]                           # {pack name: dir its gates loaded from}
    def register(self, spec: GateSpec, fn, *, replace=False) -> None   # raises if no negative_control
    def unregister(self, gate_id) -> bool
    def clear(self) -> None
    def get(self, gate_id) -> tuple[GateSpec, Callable] | None         # a copy of the stored spec
    def specs(self) -> list[GateSpec]                                  # copies, registration order
    def ids(self) -> list[str]
    def pairs(self) -> list[tuple[GateSpec, Callable]]                 # copies
    def for_claim(self, claim_id, tags=()) -> list[GateSpec]           # copies
    def by_tier(self, max_tier: int) -> list[GateSpec]                 # copies
    def set_pack(self, gate_id, pack) -> None                          # the one way to stamp a stored pack
    def needed_by(self, gate_id) -> list[str]                          # reverse `needs` edges, registration order
REGISTRY: Registry                                      # module-level default
def active_registry() -> Registry                       # the registry `@gate` decorates into right now
def use_registry(registry)                              # context manager: `@gate` inside registers there
def gate(*, id, claims=(), tier=Tier.INSTANT, settles="", requires_tools=(),
         requires_python=(), requires_one_of=(), negative_control=None, title="",
         description="", pack="", entry="", registry=None,
         needs=(), operating_context=None)              # decorator -> registers, returns fn
def availability(spec) -> tuple[bool, str]              # (ok, "requires openfoam (not on PATH)")
def run_gate(spec, fn, ctx, *, trace=None) -> Verdict   # a traced, read-only view; times it (wall, CPU);
                                                        #   catches exceptions -> error verdict
def run_all(registry, ctx, *, max_tier=0, only=None, on_verdict=None,
            before=None, after=None, current=None, pruned=None,
            marked=None) -> list[Verdict]               # before(spec, fn) -> Verdict | None;
                                                        #   after(spec, fn, verdict, trace);
                                                        #   plan order (run order)
def plan(registry, selected) -> list[GateSpec]          # selection + prerequisite closure, DFS postorder
class Reading(NamedTuple): verdict; current=True; root=""; root_kind=""; why=""
class Unmet(NamedTuple): root; kind; roots=(); why=""   # .negative: not run (Skipped) vs marked (Stale)
def prerequisite_root(spec, readings, registry) -> Unmet | None   # THE rule: sweep and resolver both
def blocked(spec, unmet) -> Verdict                     # the one producer of a prerequisite skip
def mark_reason(unmet) -> str                           # "prerequisite <root> invalidated: …" / "… unrun"
PREREQUISITE_FAILED = "prerequisite failed"             # skip_reason leads (spine text: rewording re-keys)
PREREQUISITE_NOT_ESTABLISHED = "prerequisite not established"
NEGATIVE_KINDS: tuple[str, ...]                         # errored failed skipped unqualified not-registered
NOT_CURRENT_KINDS: tuple[str, ...]                      # invalidated unrun
def selftest(spec, fn, ctx, *, trace=None, out_dir=None, fixture_root=None) -> Verdict
                                                        # runs the KNOWN-BAD control, traced
def run_fixture(spec, fn, ctx, *, trace, out_dir, fixture_root=None) -> GateContext
                                                        # the known-bad fixture ONLY; never calls fn
def run_good_fixture(spec, fn, ctx, *, trace, out_dir, fixture_root=None) -> GateContext
                                                        # the declared known-good fixture ONLY
def mutation_walk(spec, fn, good_ctx, good_verdict, *, trace=None, roots=()) -> MutationPass
MUTATION_MARGIN = 0.15                                  # how far past its own limit a value lands
MUTATION_RUNGS = (1.15, 1.5, 2.0, 3.0, 5.0, 10.0, 30.0, 100.0, 1000.0)   # x then ÷, nearest first
MUTATION_BISECT = 16                                    # halvings, in log-factor, to aim
MUTATION_RUNS_MAX = 1024                                # runs per walk, probes included
MUTATION_TIER_MAX = Tier.INSTANT                        # the costliest tier walked
class MutationResult: key, before, after, outcome, measured, limit, why
class MutationPass: results, inconclusive, not_mutated, boundary, runs, errors, flaky
COMPARATORS: tuple[str, ...]                            # P2.4: <= < >= > == != between (Comparator's values)
class ContextBreach(NamedTuple): key; value; lo; hi; why   # outside | absent | not-a-number | unread
def context_breach(spec, params, *, read=None) -> ContextBreach | None   # pure; on the spellings read
def context_token(breach) -> str                        # "context:outside|<canonical json>"
def context_of(token) -> ContextBreach | None           # its inverse
GOALPOST_FACTORS = (0.1, 0.5, 2.0, 10.0)                # where each limit a qualification run read moves
class GoalpostSite(NamedTuple): params; measured; units  # where it was first read with a verdict
def note_goalpost_sites(into, run, params, verdict) -> None   # the known-good half's, then each walk run's
class GoalpostRun: end, limit, outcome, measured, units  # end: "limit" | "limit_hi" (a band's, alone)
class GoalpostRuns: key, measured, units, runs           # one acceptance condition, at its site
def goalpost_runs(spec, fn, good_ctx, good_verdict, *, trace=None) -> tuple[GoalpostRuns, ...]
                                                        #   AFTER the walk, every site on the trace
def load_fixture(ref: str, root: str) -> Any            # "mod:fn" or "path/to/file.py"
def load_project_gates(root, registry) -> list[str]     # <root>/gates/*.py; the ids they register
def describe(spec) -> str                               # one dense line for `nopekit gate list`;
                                                        #   "basis <word>" right after the bracket
def registry_summary(registry) -> dict                  # JSON-safe: what can run here, and what cannot;
                                                        #   phase 2: by_basis, undeclared_basis
def basis_of(spec) -> str                               # phase 2 (SPEC 38): its basis, or "undeclared"
# gate(..., basis="") — the last keyword: established | sourced | model-only; empty takes
#   the module's PACK_BASIS (pack.json's "basis"); register refuses a malformed one only
#   when declared, and tells "grounded" its word. Never in rho, never a status.
```
`gate(registry=None)` decorates into `active_registry()`, which is `REGISTRY` unless a
`use_registry(...)` block says otherwise. That block is the seam a loader needs (both
`load_project_gates` and `packs.load_gates` hold it across each module they load): a
gate module cannot name the registry it should land in, and a loader that handed a
private registry to an import without it would get its gates in the global one and an
empty diff back — a project that looks like it ships no gates, with no error.
A gate function receives `GateContext` and returns `Verdict` **or** a plain
`(bool, detail)` / dict, which `run_gate` normalises. `run_gate` always fills in
`gate`, `tier`, `pack`, `claims`, `duration_s` and `cpu_s` from the spec and its own
clocks, and clears `rho` — a gate cannot lie about its own identity, its cost, or what
its verdict was computed from.

**The gate sees a traced, read-only world.** `run_gate(spec, fn, ctx, *, trace=None)`
never hands `fn` the caller's context. In order: availability (a skip never calls `fn`
and records nothing); `pack` and `key_scope` stamped; then `verdicts.traced_context` —
`params` a read-only `ParamTrace`, `ledger` a `LedgerView` with no verdicts, `extra` the
gate's own shallow copy, `model` a `ModelProxy` (`None` stays `None`), `memo` the
sweep's `SweepMemo` (only `load_file` opens it), `trace` set — and, once `modelio.clear_caches(fn)` has emptied the module-level memos
its code holds (a memo that will not empty is the run's error), `fn` runs inside
`verdicts.tracing(trace)`. `trace=None` makes a
throwaway trace, so the view is read-only on every path, not only in a sweep. It writes
no file, consults no cache and enforces no admission. The window closes before a
crash's traceback is formatted (linecache's reads are the formatter's). What slipped
through: `ctx.params` was one mutable dict shared by every gate, so gate A's
`ctx.params["load_n"] = 0` was gate B's input (S-24); fdm-print's mesh cache on the
shared `extra` made its second gate's read of the part invisible (S-27), and a gate
caching in the shared `memo` directly did the same one field over (review, `ffr3/p2`).
`cpu_s` is the
`os.times()` delta with children included — omc works in a subprocess. Measured on the
corpus before it landed (R-4): every bundled baseline and control, and cad-solid's
hand-run `selftest/check_*.py`, raise zero `GateInputWriteError`
(`tests/test_gate_context.py::ReadOnlyBlastRadius`). A caller that hands `run_gate` a
dict subclass of its own as `params` has the gate's read paths replayed through its
`get` afterwards (the first two levels): the CLI's `Param.gates` recorder sees nothing
of a copy being made, and `why` would otherwise have said no gate reads a parameter
three gates read.

**`run_all`'s hooks.** `before(spec, fn)` is asked after the lost-control re-check; a
`Verdict` it returns is that gate's verdict and `fn` never runs. `after(spec, fn,
verdict, trace)` gets every gate that ran here with a fresh `GateTrace` of its own; a
`Verdict` it returns replaces the one it got; it runs before `on_verdict`. The sweep
gets one `SweepMemo` (`verdicts.sweep_memo`: fresh when the caller brought none, a
caller's plain dict wrapped with its entries shared), never left on the caller's context.

**`GateContext.load_file(path, loader=None)`** resolves `path` against `root`, reports
the read as the `open` audit event it stands for — to the view's own trace and every
trace open around it — on EVERY call, hit or miss, and memoises `loader(abspath)` (the
bytes by default) in the entries behind `memo` (`verdicts.memo_entries`: nothing else
opens them) on `(abspath, id(loader))` (a bound method by its object and
function). The loader runs inside the view's window and, on a miss, under a `GateTrace`
of its own that the entry keeps; a hit `replay`s it, so every caller records every file,
question, directory and opaque channel the loader touched — a `.gltf`'s `.bin` buffers,
an `.obj`'s `.mtl`, an `os.path.exists` on an optional sidecar — not only the named file.
The entry is revalidated by the stat signature of the named file (taken before the load)
and of every other path the loader read, listed or asked about (after it; a path probed
and missing must still be missing). No memo: it just loads,
inside the same window (a hand-run check script, packs:H15).

**Controls, traced.** `selftest(spec, fn, ctx, *, trace=None, out_dir=None)`: the
fixture gets a WRITABLE traced copy of `ctx` (`out_dir` replaced when given) — its
writes never reach the sweep, its reads of the host land in `trace.host_reads` — and
`make` runs inside `tracing(trace)`, its module-level memos emptied first
(`modelio.clear_caches(make)`: re-verification runs a fixture and a miss runs it again,
in one process); `trace.fixture_code` is
`modelio.code_closure(<the module the reference names>)` — not `make`'s own module, so a
fixture file that re-exports a helper's `make` is keyed by the file an edit moves — or
`None` when the stock import served it; the gate then runs through `run_gate` with the same trace.
`duration_s` and `cpu_s` cover both. `run_fixture(spec, fn, ctx, *, trace, out_dir)`
is the fixture half alone — for re-qualifying a control whose fixture code moved without
re-running the gate — and raises `NopekitError` when the control is unusable.
`run_good_fixture` is its twin for a declared known-good fixture (`NegativeControl.good`,
P2.3): the same builder under the same guards, handed exactly what the known-bad one is.
`fixture_root` is where a project's relative fixture ref resolves when `ctx.root` may
not be the project's — the sweep names the project root, since a
`selftest/known_good.py` may point its context's root at the design's own files.

**The mutation pass (P2.3).** `mutation_walk(spec, fn, good_ctx, good_verdict, *,
trace, roots)` walks every value the known-good run read (a leaf it keyed, or one under
a level it read whole): along `MUTATION_RUNGS`, x then ÷, until the gate's OWN
`measured` lands `MUTATION_MARGIN` past its OWN `limit` on the side opposite its
known-good run — never the pass flag — then `MUTATION_BISECT` halvings aim at the
smallest landing factor, rounded outward to 3 significant figures; that run's outcome
is the key's `MutationResult`. A skip or error is inconclusive (a crash that does not
repeat sets `flaky`, and the walk is held); a value that never lands, a word, a zero or
a key the budget does not reach is `not_mutated`, with why. Influence probes order the
keys (a value equal to the reported one first). It runs in process on deep copies, a
fresh memo per run, at tier `<= MUTATION_TIER_MAX`, with its scratch in one temp dir
that must lie outside `roots` (the project and every pack directory — else
`MutationCannotRun`), and every run's reads are folded into `trace` at their
KNOWN-GOOD digests: a value only a mutated run reads is an input of the
qualification. It writes nothing.

**Prerequisites (`needs`, P2.2).** A gate may name the gates that must be
established before its verdict counts: a validity guard before the analyses it
guards. `Registry.register` refuses, on the new field only (R-10): a need that is not
exactly one gate id (empty, whitespace, `/ \ .. :`, a glob character), names the gate
itself, or repeats; a need that closes a **cycle** — an incremental DFS from the new
node over the registered gates' needs, with the new spec substituted (`replace=True`,
a re-import), complete under any load order, the message naming `a -> b -> a` and each
member's pack; and a **tier inversion** from either side of the edge. A need not
registered yet is allowed (a missing pack must not become a load crash) and reads "not
registered" at the sweep. `plan(registry, selected)` expands the selection by its
prerequisite closure (`check --only X` runs X's prerequisites; `gate selftest --only X`
stays pure selection, `_selected`) and orders it DFS-postorder — registration order
when nothing needs anything — re-asserting acyclicity and tier order (a spec reached
the private dict some other way: `NopekitError`). `run_all` runs in that order and
returns its verdicts in it; `prerequisite_root(spec, readings, registry)` is the one
decision, asked before each gate with `needs`. A prerequisite is established when its
effective verdict is a pass and current; otherwise its kind is, in rank order,
errored, failed (an invalidated fail too, D-08), skipped, unqualified, not registered
(**negative**), then invalidated, unrun (**not current**); a root is found
transitively (through `blocked_by`, or a reading's `root`). Under a negative root
`before`, `fn`, `after` and the control are never called: the verdict is
`pruned(spec, fn, unmet)`'s, by default the gate's own missing-tool skip, else
`blocked(spec, unmet)` — `skip_reason` `prerequisite failed: <root>` only when the root
failed, else `prerequisite not established: <root> (<why>)`, `blocked_by` the roots,
`blocked_kind` the first's kind, no cost, no rho. Under a not-current root the gate
runs as usual and `marked(spec, verdict, unmet)` is told. `current(gate_id)` says
whether a verdict this loop produced is current (default: all are). What slipped
through before the edge (S-51): a claim tagged only `deflection` read Checked on a beam
whose guard reported Euler-Bernoulli omitting 32% of the deflection; and run order was
registration order, alphabetical by module, so four of seven packs ran their guard
after the numbers it vouched for (S-52).

**Code is loaded fresh.** `load_project_gates`, `load_fixture` and
`packs.load_gates` all go through `modelio.load_source_module`: the bytes that run are
the bytes on disk, the code closure is recorded, and a cached module is served only
while its closure still hashes the same — re-adopting its gates into the caller's
registry. What slipped through: after a same-size, same-second edit, gate modules and
fixtures ran their old bytecode, and an in-process re-load skipped any module already
in `sys.modules` (S-26). `load_project_gates` moved here from the CLI so there is one
copy; it returns the ids each module registered, cached or not. `load_fixture`'s
`module:function` form resolves the name with `importlib.util.find_spec` and loads it
fresh under its own dotted name when it is a plain module of Python source that is code
(`modelio.is_code` against the fixture's root); an installed module, a package's
`__init__`, an extension or a namespace package goes through the stock import, records
no closure, and its control's hint never holds (`verdicts.UNRECORDED_FIXTURE`). What
slipped through (admission review, round 1, C): this form was `importlib.import_module`
alone — no closure, so a hint of nothing that always held, and a same-size edit's old
`.pyc` ran on the re-run.

**A gate id names a directory.** `register` refuses an id containing `/`, `\`, `..`
or `:` — `.nopekit/verdicts/<gate id>/` holds its cached verdicts — and an id that
differs from a registered one only in case, which a case-insensitive filesystem makes
one directory. Zero hits over the 54 bundled pack ids and the bracket's 6 before the
refusal landed (R-4).

**Parameter lookup is PACK-SCOPED.** `ctx.param("bbox_mm")` inside `fdm.bed_fit`
resolves `fdm.bbox_mm` (flat or nested), then `fdm-print.bbox_mm`, then the bare
`bbox_mm`, then the last dotted segment. For a synonym family
(`first_pack_param`): every scoped spelling in declared order, then every bare one.
That is what lets `cad-solid` and `fdm-print` both read a `bbox_mm` from one
projection and mean different objects — see `docs/PACK_FORMAT.md`. `run_gate`
stamps `pack` and `key_scope` from the spec, so a caller cannot hand a gate
somebody else's namespace.

**`Registry.register` raises `NopekitError` if `negative_control is None`.**
This is rule 5 of the method made mechanical: a gate that cannot demonstrate
failure is a logger, and one shipped green for a whole revision.

**Every exit hands out a copy.** `get`, `specs`, `pairs`, `for_claim`, `by_tier` and
iteration return copies of the stored spec, never the spec itself. What slipped
through: `specs()` and `get()` handed out the stored object, so
`reg.specs()[0].claims.append(...)` widened what the next verdict settled (S-13),
while the docstring said the window was closed. `set_pack(gate_id, pack)` is the one
sanctioned way to change a stored spec: `packs.load_gates` stamps a pack name through
it when the stored one is blank, instead of mutating the spec, and an idempotent
re-register keeps a stored pack when the incoming one is blank. `run_all`'s own
re-check of the stored control is defence in depth behind the copies.

**A pass is `True` — the strict pass rule.** `run_gate` accepts `passed` only as a
`bool`, or a 0-d object whose `dtype.kind == "b"` (numpy's bool, duck-typed; the
spine never imports numpy). Any other value on a verdict that is neither skipped nor
errored becomes `error="gate reported passed=<repr> (<type>); a verdict must say True
or False"`. `measured` and `limit` must be `None` or a real number that is not a
bool; anything else is an error naming the type, and NaN or ±inf is an error
containing `non-finite` **with the field set to `None`**, so no strict writer ever
sees it. What slipped through: `{"passed": "false"}` rendered `[ok]` with `ok=True`
(S-01), and a `measured` of `"n/a"` was filed as a measurement (S-02). *Rejected:*
`bool(x)`, which is the hole; `isinstance(x, int)`, which accepts `1` and `2` as
passes because `bool` subclasses `int`.

**`requires_one_of`: tooling that is a disjunction.** `availability(spec)` is ok only
when every `requires_tools` and `requires_python` entry is present **and**, when
`requires_one_of` is non-empty, at least one of its entries is — `"python:<module>"`
found by `importlib.util.find_spec`, `"tool:<executable>"` by `shutil.which`. None
present gives `(False, "requires one of python manifold3d, tool blender, tool
openscad (none found)")`. What slipped through: `cad.clash` needs any one of three
boolean engines; on a machine with trimesh and none of them it self-skipped its
own baseline and control while `availability` said yes. *Rejected:* two fields
(`requires_python_any`, `requires_tools_any` — two places to forget), and a
skip-reason heuristic (it lets the gate decide its own skip is "tooling").

**A skip is allowed only when `availability(spec)` fails.** `selftest` returns a
not-ok verdict, `error="skipped on its own known-bad input while its tools are
present: <reason>"`, for a gate that skips itself on its own control while its
declared tooling is present. A control that skips is not a control: a fixture that
deleted a key its gate needed passed invariants 3 and 6 as "honestly blocked" (S-12).

### `claims.py`  (deps: models, util)
The derivation logic. Nothing here writes.
```python
class ClaimCause(StrEnum): FAILED | PHYSICAL_FAIL | ERRORED | PREREQUISITE_ERRORED
                         | SKIPPED | PREREQUISITE | UNQUALIFIED
                         | NO_EVALUATOR | NO_OWNER | OWNER_UNATTRIBUTED | NO_REASON
                         | UNRUN | INVALIDATED | NO_ARTICLE | OWNED | PHYSICAL_PASS | CHECKED
                         | ACCEPTANCE | OUTSIDE_CONTEXT | FALLBACK      # P2.4
                         | CONTRADICTION | JUDGED_FAIL | NO_AUTHORITY | AUTHORITY_UNATTRIBUTED
                         | ARTICLE_MOVED | CLAIM_MOVED | JUDGMENT_MOVED | ARTICLE_UNJUDGED
                         | AWAITING_JUDGMENT | ON_ARTICLE | JUDGED      # P2.5a
                         | UNSEEN       # issue #15 D1: Stale for an opaque channel alone
OPAQUE_REASON = "opaque inputs: "    # the head of the resolver's reason for an opaque entry
def unseen_gates(resolution) -> frozenset    # stale ONLY through a channel nopekit cannot see
                                             #   (the row's reason and its entry's opaque list,
                                             #   or a prerequisite mark that ends in one)
def unseen_reason(why) -> bool               # the words-only half: OPAQUE_REASON, alone or
                                             #   after prerequisite marks (`report.reason`'s
                                             #   Failing suffix: "from the last check run")
class Attribution(NamedTuple): owner: str; reason: str     # the signing channel's record of an owner
@dataclass(frozen=True)
class Composed: status: ClaimStatus; cause: ClaimCause; cites: tuple; verdict: Verdict | None
    errored -> bool                                          # property: ERRORED or PREREQUISITE_ERRORED
STATUS_KEY: Mapping[ClaimStatus, str]                        # pass -> "checked", unverified -> "pending_build", ...
SEVERITY_ORDER: tuple[str, ...]                              # tokens + "errored", most urgent first
KEY_ORDER: tuple[str, ...]                                   # the eight tokens, count order
OUTCOME_ORDER: tuple[str, ...]                               # fail, error, prerequisite-errored, prerequisite, skipped, unqualified, outside-context, unrun, pass
def severity(composed) -> int                                # its place in SEVERITY_ORDER
def outcome_rank(verdict) -> int                             # its place in OUTCOME_ORDER; None = unrun
def outcome_key(verdict) -> str                              # phase 3a: its OUTCOME_ORDER token; "unrun"
                                                             #   for None — the round record's
def covers(spec, claim) -> bool                              # claim id OR any claim tag in spec.claims
def covering_verdicts(claim, verdicts) -> list[Verdict]      # the same id-or-tag rule, on verdicts
def compose(claim, verdicts, *, stale=False, stale_gates=(), owners=None,
            unseen_gates=()) -> Composed     # unseen_gates: unseen_gates(resolution); words only
def compositions(ledger, *, registry=None, stale=False, stale_gates=(), owners=None,
                 unseen_gates=()) -> dict[str, Composed]
def resolve_status(claim, verdicts, *, stale: bool = False,
                   stale_gates: Collection[str] = (), owners=None,
                   unseen_gates=()) -> ClaimStatus  # compose(...).status
def explaining_verdict(claim, verdicts) -> Verdict | None    # failed > errored > skipped > unqualified
def statuses(ledger, *, stale=False, registry=None, stale_gates=(), owners=None) -> dict[str, ClaimStatus]
def coverage(ledger, registry) -> dict[str, list[str]]       # claim id -> LIVE gate ids
def effective_gates(ledger, registry) -> dict[str, list[str]]  # cached `claim.gates` UNION live
def find_gaps(ledger, registry) -> list[Need]                # MEASURABLE claims with no gate
def blocking(ledger, registry, *, stale=False, stale_gates=(), owners=None) -> list[tuple[Claim, ClaimStatus]]
def summarise(ledger, registry, *, stale=False, stale_gates=(), owners=None) -> dict   # counts, for the CLI
def next_claim_id(ledger, prefix="C") -> str                 # C1, C2, ...
# P2.4: the margin, the comparison of a value with its claim, the context mark
class Margin(NamedTuple): fraction: float | None; why: str = ""
def margin(verdict) -> Margin                                # D-17: > 0 inside, < 0 past, or why none
class Compared(NamedTuple): state: str; why: str = ""        # holds | fails | not-compared
def cross_check(claim, verdict) -> Compared                  # settles == quantity, units equal, both finite
def compared_gates(claim, verdicts) -> list[str]             # the evaluators whose value IS the claim's
def not_compared(claim, verdicts, prerequisites=None) -> list[tuple[str, str]]   # (gate, quantity|units)
class LimitDisagreement(NamedTuple): claim; gate; limit; units; acceptance
LIMIT_REL_TOL = 1e-9                                         # two limits are one number: float noise only
def limit_disagreements(ledger, verdicts=None, *, stale_gates=()) -> list[LimitDisagreement]
                                                             #   current verdicts only (review of P2.4)
def outside_context(verdict) -> bool                         # its unqualified token is CONTEXT_OUTSIDE's
def assumption_reason(claim) -> str                          # rationale (assumption) | fallback (otherwise)
# P2.5a: terminal, authority and the physical path
def terminal_of(claim) -> str                 # declared, or by kind; "" for every automated claim
def identity_matches(who, name) -> bool       # the whole git identity, or its name part, exactly
def claim_digest(claim) -> str                # statement, kind, terminal, acceptance, note, authority
AWAITS_A_PERSON: frozenset[ClaimCause]        # the Stale causes a check run cannot answer
def blocks(composed) -> bool                  # BLOCKING_STATUSES, but not a Stale AWAITS_A_PERSON alone
def contradicted_by(claim, verdicts, *, stale_gates=(), needs=None, codes=None) -> list[dict]
@dataclass(frozen=True)
class Rebuild: article: str; claims: tuple; moved: tuple    # to_dict()
def rebuild(ledger) -> list[Rebuild]          # the rebuild prediction, over a judged view
# P2.5b: ready is one predicate; a milestone; latency
class Unresolved(NamedTuple): required; unresolved; missing; unbound; ready
def required_ids(ledger, milestone=None) -> list[str]   # the milestone's `requires`, else every critical claim
def unresolved(ledger, composed, milestone=None) -> Unresolved   # THE predicate: ready iff every
                                              #   required id exists and reads Checked
class Latency(NamedTuple): seconds; source; article; declared   # source: measured | declared | none
def declared_seconds(claim) -> float | None   # expected_latency in seconds, or None
def latency(claim, exports) -> Latency        # a measurement's: from the newest export of its
                                              #   article not after the newest result a person
                                              #   typed on it; else declared; an automated
                                              #   claim's is none (its evaluator's run's)
# Architecture phase 1: the mechanism (SPEC decision 13) and requirements (decision 12)
class MechanismProblem(NamedTuple): claim; evaluator; kind; filed = ()  # kind: unregistered | uncovered
def mechanism_problems(ledger, registry) -> list[MechanismProblem]   # a cross-check: moves no status;
                                              #   registry None -> []
MECHANISM_YET_TO_RUN: frozenset[ClaimStatus]  # Gap, Pending build, Assumed
def yet_to_run(ledger, composed) -> list[Claim]   # every claim whose mechanism has yet to run
def need_how(ledger, need) -> str             # the how of every claim a Need names, joined
def proposal_query(claim, gaps, ledger=None) -> tuple[Need, str]   # gap --propose's search: the
                                              #   claim's Need with need_how (one search per
                                              #   record), or a transient one never written
                                              #   with the claim's how
class RequirementStatus(NamedTuple): id; status; errored; claims; worst; disagree = ()
                                              # key -> str (property); disagree is
                                              #   report.requirement_statuses' to fill
def requirement_statuses(ledger, composed) -> dict[str, RequirementStatus]   # THE producer:
                                              #   the worst serving claim by severity; Gap with none
def worst_of(ids, composed) -> tuple[ClaimStatus, bool, tuple]   # phase 5: the fold a
                                              #   requirement and a board both ask; Gap with none
# Architecture phase 2 (SPEC 14, §0.2): what lost, and what would bring it back
RETURN_STATES = ("holds", "does-not-hold", "cannot-evaluate", "not-machine-checkable", "none")
class ReturnState(NamedTuple): state; why = ""; claim = ""; status = ""; observed = None
                               gate = ""; detail = ""   # why: no-claim | status | no-value |
                                              #   no-margin | no-limit | band; tokens only
def claim_value(claim, composed, verdicts) -> Verdict | None   # the verdict the composition
                                              #   reports: the compared covering pass with the
                                              #   least margin; None unless Checked by evaluators
def return_state(condition, ledger, composed) -> ReturnState   # THE evaluation, pure; only
                                              #   on a claim reading Checked (phase 4 reuses it)
PREFERENCE_SCHEMA = 1
def rejection_digest(decision, rejection) -> str   # sealed: decision id and title, the
                                              #   rejection's value, why and returns_if
class PreferenceState(NamedTuple): state; name; who = ""; when = ""   # recorded | not-recorded
def preference_state(decision, rejection) -> PreferenceState | None   # None unless
                                              #   preferred_against; never by alone (inv. 13)
class RejectionRow(NamedTuple): decision; index; value; type; by; input; condition; returns
                                preference    # def to_dict(self) -> dict: tokens only
def rejection_rows(ledger, composed) -> list[RejectionRow]   # every decision's every loser
def may_return(rows) -> list[RejectionRow]    # returns.state == "holds": check's lines
```
**What lost, and what would bring it back (architecture phase 2).** `return_state` is
the one evaluation of a return condition, for `check`'s *may return* lines, `why`, the
JSON summaries (`summarise()["rejections"]`) and `state.json` — and phase 4's
candidates. It reads the composition the caller judged and nothing else: a condition on
a claim that is not Checked reads `cannot-evaluate`/`status` (a Checked composition is
current by construction: Stale precedes Checked on the ladder); `margin` is the binding
verdict's own (`margin`, now over a shared `_margin_of`, unchanged), `value` its
measured value, `limit` the claim's acceptance limit (a band cannot be evaluated). A
claim Checked on an article gives its counted result's measured value when its units
are the acceptance's; a judged claim gives none. `preference_state` re-checks the
channel and the identity, as `_attributed` does for owners, and binds a preference to
`rejection_digest`: an edit to the title, or to the rejection's value, why or condition,
un-records it; reverting records it again.

**A claim's mechanism and its requirements (architecture phase 1).** The evaluators
a claim's mechanism names are a cross-check, never coverage: `mechanism_problems` is
the one place that compares them with the registry, for `check`'s and `doctor`'s
warnings, and nothing it returns reaches `compose`, `compositions`, `effective_gates`
or `coverage` (S-37: a claim file that held coverage was rewritten by `check` on every
run). `yet_to_run` reads the composition a caller already holds — `gap`'s list — and
`find_gaps` keeps its Need half. `requirement_statuses` is the one producer of a
requirement's status: the worst composition among the claims that name it, in
`SEVERITY_ORDER` (a crash's Skipped above a plain one), Checked only when every one is,
Gap with none and no cause of its own; status, `why`, the report, `summarise`, the JSON
rows and `state.json` ask it and never fold. *Ready* never reads it: `unresolved` is
unchanged. `claim_digest` keeps its P2.5a form: `terminal` is `mechanism.by`, `note` is
`mechanism.how`, and `evaluators` and `requirements` are not sealed, so a claim file
moved to the mechanism form stays bound.

**Ready is one predicate (P2.5b-D1, invariant 12).** `unresolved` is the one place that
decides *ready*: every reader — `check`'s line, `status`, the report's sentence,
JUnit's properties, `summarise`'s `milestones`, `state.json` and `export` — asks it,
with the milestone or without (the project's spend: every `critical` claim). A required
id with no claim file is `missing` and never ready. What slipped through before it
(S-60): the page said ready whenever nothing stopped `check`, and each reader had its
own copy of the test.
`covers` binds by id **or** by tag, and an empty `spec.claims` covers nothing, never
everything: a wildcard would let one misregistered gate mark a project proven.

**The composition (P2.1, GLOSSARY §3).** `compose` resolves one claim to its status
AND the fact that set it, one ladder for every kind, first match wins. Over the
covering verdicts (`covering_verdicts`) and the known covering gates (`claim.gates`;
a known gate with no verdict is *unrun*), an evaluator is *unqualified* when its
verdict carries `Verdict.unqualified`, and *errored* when its outcome is `error`
without it:

| # | status | when | cause |
|---|---|---|---|
| 1 | Failing | a physical result failed (`refuted`), whatever the claim's kind and whatever was recorded after it; any covering verdict failed (`fail`), any kind, stale or not (D-08, R-3) | `physical-fail`, `failed` |
| 2 | Skipped (`blocked`) | any errored, else any not run behind a prerequisite that crashed (`blocked_kind` `errored`: as loud), else any not run behind any other prerequisite (`blocked_by`; ahead of a plain skip), else any skipped — beside a pass or not | `errored`, `prerequisite-errored`, `prerequisite`, `skipped` |
| 1′ | Failing (P2.4) | any counted covering PASS whose value, of the claim's quantity and in its units, does not meet the claim's acceptance condition (`cross_check`), stale or not | `acceptance` |
| 3 | Gap (`unclaimed`) | any unqualified, beside a pass or not; then (P2.4) any pass outside its evaluator's operating context, unless an owned fallback carries it; a measurable claim with no evaluator; an assumption with no owner named, no reason, or an owner `owners` does not attribute | `unqualified`, `outside-context`, `no-evaluator`, `no-owner`, `no-reason`, `owner-unattributed` |
| 4 | Open (`pending`) | any covering gate unrun | `unrun` |
| 5 | Stale | `stale`, or a covering gate in `stale_gates` | `invalidated` |
| 6 | Pending build (`unverified`) | a physical claim with no result; or a pass recorded that no article binds to the current inputs (every pass, until article binding) | `no-article`, `physical-pass` |
| 7 | Assumed (`asserted`) | an attributed, reasoned assumption; (P2.4) a pass outside an operating context carried by an owned fallback | `owned`, `fallback` |
| 8 | Checked | every covering evaluator ran, passed and is current (`pass`); a physical claim only on a pass bound to an article (`verified`), which nothing records yet | `checked` |

**The result that counts** is `Claim.physical_result`, as `store` assembles it from
`results/<id>.json`: the latest **fail** when any result failed, otherwise the latest
(R-3). Rung 1 reads a fail whatever the claim's kind; a recorded pass counts only for a
physical claim, at rung 6. What slipped through (review of P2.1): the latest result
counted, and only for a physical claim — a pass typed after a fail, or the claim's kind
edited away from physical, read the project ready with the fail still on disk; and a
typed pass read Checked everywhere but *ready*, so *checked* meant two things.

**A skip is never a pass; an error is never a pass; an unqualified or unrun evaluator
is never a pass, beside a pass or not.** A pass never makes an assumption Checked (its
kind says no evaluator settles it); a covering fail, skip, refusal or unrun gate still
counts against one (R-3). A physical claim composes its automated evaluators before it
reads its result (S-49), so a failing modelled half reads Failing, and an unrun or
invalidated one Open or Stale, above Pending build — the cheap evaluator first, and
Open and Stale stop `check` while Pending build does not. `cites` is the gate ids the
cause names, errored first; `verdict` is the one that explains it.

What it replaced, and what slipped through it: rung 2 read only *all*-skipped as
BLOCKED and rung 4 read a crash as FAIL ("something is wrong here" for a broken
evaluator, so the design took the blame); a pass beside a skip or an unrun gate read
PASS and the report marked it PARTIAL under PROVEN (S-03); a gate refused at its first
check had no verdict entry, so it read unrun and the pass beside it PASS; a physical
claim ignored a failing modelled half (S-49); every assumption read ASSERTED, owned or
not. **Folding a crash into Skipped files it under the missing tool's word** — so the
crash is carried louder on purpose, in four places (PLAN-v0.14 §1.5): its reason leads
`errored:`, its row sorts above every skipped row in Failing's tone (`[SKIP ]`), every
count splits it out (`N skipped (k errored)`), and its JUnit case is an `<error>`,
critical or not. `Composed.errored` is the one place a renderer learns which; an
unqualified evaluator's claim is never errored (P2.0 D-8).

**Owners** (P2.1-D8). `Claim.owner` names a nominee; it never counts by itself. An
assumption reads Assumed only when `owners[claim.id]` exists, its `owner` equals the
file's and its `reason` equals the file's non-empty `rationale` — bound by value, so an
edit to either after the attribution un-attributes it. `owners` reaches `compose`
only as a mapping of `Attribution`s, the way staleness arrives; the signing channel
(later in Phase 2) is its only producer, and every P2.1 caller passes none — so every
assumption reads Gap: `no owner recorded`, `owner X is named in claims/<id>.json and
has not recorded it`, or `no reason recorded`. PLAN-v0.14 §1.4: an owner written any
other way "reads unattributed and the claim stays Gap".

`explaining_verdict` is the single ranked choice of which verdict explains a claim's
status, in `compose`'s order: one that ran and failed, else one that errored, else one
not run behind a crashed prerequisite, else one behind any other, else one that
skipped, else one unqualified, stable within a rank — `OUTCOME_ORDER`, the one
table the report's bullets and `why`'s groups read too (`outcome_rank`). **One severity
order**, `SEVERITY_ORDER` (`severity`): Failing · Skipped, errored · Skipped · Gap ·
Open · Stale · Pending build · Assumed · Checked — the order `status`, `check`'s
BLOCKING list, the report and `last_check.json`'s `worst` rank claims in. It lives here,
not in `report.HUMAN`, because a rank is not a word and `verdicts.write_last_check`
reads it. What slipped through: `status`
cited a skipped pack gate as the reason a claim failed while `check` cited the real
failure, because a fix to one caller's private ranking never reached the other's (S-68).

`blocking` returns **(claim, status) pairs** over `BLOCKING_STATUSES`, which P2.1 does
not change: what moved is which status a fact reads. So from P2.1 these stop `check`
too — **the visible changes**: an errored critical claim (Skipped), an assumption
nobody owns (Gap), a pass beside an unrun evaluator (Open), a pass beside a skipping
tag-bound evaluator (Skipped), a refused evaluator beside a pass (Gap), and `check
--tier 0` on a claim whose tier-2 evaluator never ran (Open). Pending build and Assumed
are unresolved and do not stop `check`; *ready* is the stricter predicate.

`summarise`'s keys: `n_claims`, `n_critical`, `by_status` (every enum value, sum =
`n_claims`; from P2.1 `blocked` holds errored claims, `unclaimed` unqualified and
unowned ones), `by_kind`, `counts` (P2.1: the eight tokens of `KEY_ORDER`, zero-filled,
sum = `n_claims`), `errored` and `errored_ids` (claims Skipped by a crash),
`n_gates`, `n_gates_binding`, `n_covered_claims`, `n_verdicts`, `n_gaps` (gap
RECORDS — `find_gaps`' Needs, an automated claim no registered evaluator covers — not
claims reading Gap: `counts["gap"]` is those), `n_blocking`, `blocking_ids`,
`unresolved_ids` (required claims not Checked, Pending build and Assumed included),
`unbound_ids` (required claims with a physical pass recorded that no article binds:
each reads Pending build, so each is in `unresolved_ids` too),
`all_required_checked` (*ready*, GLOSSARY §4: at least one required claim, every one
Checked — `unresolved_ids` empty), `stale`, `ready`, and four that Phase 2 added after
P2.1: `not_compared` (P2.4: `{claim id: [evaluator ids]}`, the covering passes whose
value measures another quantity or units than the claim's acceptance condition, claims
with none left out), `rebuild` (P2.5a-D16: the rebuild prediction as `[{article, claims,
moved}]`, empty when no article moved), `contradictions` (P2.5a-D14: `{claim id:
[evaluator ids]}` for each claim Failing on a contradiction) and `milestones` (P2.5b:
`{name: {ready, required, unresolved, missing, exports, last_export}}` — each
milestone's *ready* from `unresolved`, as last evaluated; `last_export` is `null` or
`{article, when, who}`). **`ready` keeps its meaning** — `n_blocking == 0`, nothing
stops `check` — for every reader that has it, until the rename pass; *ready* is
`all_required_checked` and, per milestone, `milestones[<m>].ready`, both from
`unresolved`, the one predicate (P2.5b-D3: no `Readiness` object replaced it, as this
paragraph promised until Phase 2 closed). *Rejected:* flipping `ready` in place, a key
changing meaning under every reader at once.

`effective_gates` is the **single** definition of which gates cover a claim, and
anything rendering coverage calls it rather than re-deriving one. `coverage` alone
is the live half; the union with the ledger's cached `claim.gates` is what keeps a
gate visible when the pack supplying it is not loaded in this process. A second,
live-only copy of the rule in `report.py` dropped exactly those gates, so the report's
caveat naming the unproven gate vanished whenever a pack went missing — the report
read *more* certain the less it could see.

**Staleness is per gate.** `stale_gates` is what `verdicts.resolve` found Stale, Unknown
or undemonstrated; a claim reads Stale (rung 5) when one of its covering gates is in it
and nothing above applies, and a stale fail stays Failing (D-08). `stale=True` is the
all-gates alias, kept so `StatusPrecedence.test_stale_is_not_pass` stays byte-identical
(R-6). What it replaced: one flag for the whole project, from one hash of the
projection and one of every input — a comment edit in the model staled every claim, a
model that failed to import staled none (S-21), and ingesting one unread file staled
every measurable one (S-33). `summarise`'s `stale` is True when any gate is.

**`ClaimCause` lives here, not in `models`**: `models` is a spine module
(`verdicts.SPINE_MODULES`), so a cause added later (P2.2's prerequisite) would re-key
every verdict cache entry in every project.

### `artifacts.py`  (deps: models, util, store)
Intake of real evidence — sketches, teardown photos, CAD, datasheets, measurements.
```python
ASK_FOR: dict[str, list[str]]        # artifact-kind -> concrete prompts the agent should use
ASK_PRIORITY: tuple[str, ...]        # sketch, reference first (they shape the design) ...
ENOUGH: dict[str, int]               # kind -> how many make asking again noise
DIR_KIND_HINTS: dict[str, ArtifactKind]   # "sketches" -> SKETCH: the directory is a hint too
def prompts_for(kind) -> list[str]   # ASK_FOR for one kind; [] for a kind with none
def kind_for(path) -> ArtifactKind   # by extension + directory hint
def bucket_for(kind) -> str          # the inputs/ subdirectory a kind belongs in
def find_by_hash(ledger, sha) -> InputArtifact | None   # already ingested? by digest
def ingest(root, ledger, src, *, kind=None, description="", when="", copy=True,
           licence="", note="") -> InputArtifact     # copies into inputs/<bucket>/, hashes;
                                                     # returns the record to write
def ingest_link(root, ledger, url, *, description="", kind=ArtifactKind.LINK, when="") -> InputArtifact
def add_extraction(ledger, artifact_id, extraction: Extraction) -> InputArtifact
                                                     # the artifact's record, extraction appended
def inputs_hash(ledger) -> str                       # over sorted (id, sha256); a display id
def unextracted(ledger) -> list[InputArtifact]       # evidence nobody read = decoration
def grounding(ledger, *, include_declared=True) -> dict[str, list[str]]
                                                     # param/claim id -> artifact ids, DERIVED
def value_grounding(ledger, views) -> dict[str, list[str]]
                                                     # phase 3a (was cli._grounding): grounding,
                                                     # then each ParamView's PARAMS grounded_by —
                                                     # the ONE map why, inputs and the graph read;
                                                     # an id may be an assumption claim's
def requests_by_kind(ledger, *, project_kind="", limit=6) -> list[tuple[str, str]]  # (kind, prompt)
def suggest_requests(ledger, *, project_kind="") -> list[str]
    # the ASK list, filtered to what is MISSING. This is what makes intake actively
    # solicit files instead of waiting for the user to think of them.
```
`ingest`, `ingest_link` and `add_extraction` write nothing: each returns the
`InputArtifact` the CLI's shim writes as `inputs/<id>.json`, the one record it
touches, and updates the in-memory `ledger` so a command handling several files
dedupes against its own earlier ones. `ingest` refuses a file where a record goes (a
top-level `<record dir>/*.json`, which it would record in place). Bytes already ingested
come back as their record untouched — unless the record's `path` no longer holds a
file: then they moved, and the same record (id and extractions kept) comes back pointing
where they are, copied into the bucket of the record's kind when they come from outside
the project, unless another record already names that file. What slipped through: the
record came back untouched, so moving evidence into a bucket and ingesting it — a
refusal's own hint — left it MISSING under "1 artifact(s) registered". `grounding` inverts the extractions every time
it is read and adds the `grounded_by` a human declared on a param or claim record; the
edge an extraction implies is never copied into the record it grounds. What slipped
through (S-36): `extract` used to copy it (`cli._link_grounding`), so a deleted
extraction's grounding lived on — `why arm_length` said "GROUNDED BY arm" while
`inputs` said `arm` was "NEVER READ".

`ASK_FOR` must cover at minimum: sketch, reference, cad, screenshot, datasheet,
spec, measurement, standard, data — each with 2-4 concrete, domain-neutral prompts
phrased as things to ask a human ("a photo of the closest existing product you'd
buy instead, even a bad one").

### `packs.py`  (deps: models, util, store; gates and modelio only inside functions)
```python
MANIFEST_NAME = "pack.json"; DOC_NAME = "PACK.md"; REFERENCES_DIR = "references"
GATES_DIR = "gates"; GENERATORS_DIR = "generators"; SELFTEST_DIR = "selftest"
BASELINE_NAME = "baseline.json"; LENSES_NAME = "lenses.md"; SOURCING_NAME = "sourcing.md"
PACK_PATH_ENV = "NOPEKIT_PACK_PATH"         # extra search roots, os.pathsep-separated
BUNDLED_PACKS: str                           # where the shipped packs live (checkout or wheel)
def search_paths(root=None, *, existing_only=True, include_env=True,
                 include_user=True) -> list[str]
    # $NOPEKIT_PACK_PATH, project .nopekit/packs, ~/.nopekit/packs, bundled packs/
def discover(root=None) -> list[PackManifest]              # reads pack.json only (tier 1)
def discover_dirs(root=None) -> list[tuple[str, PackManifest]]   # (pack_dir, manifest), precedence order
def find(name, root=None, *, include_env=True, include_user=True) -> str | None   # directory
def origin_of(pack_dir, root=None) -> str                  # "project" | "user" | "bundled" | "path"
def read_manifest(pack_dir) -> PackManifest
def load_gates(name, registry, root=None, *, include_env=True,
               include_user=True) -> list[GateSpec]        # imports pack gates/*.py
def load_all_gates(names, registry, root=None) -> list[GateSpec]
def pack_doc(name, root=None) -> str                       # PACK.md  (tier 2)
def reference_doc(name, ref, root=None) -> str             # references/<ref>.md (tier 3)
def references(name, root=None) -> list[str]               # tier-3 names, none loaded
SKILLS_DIR = "skills"; SKILL_NAME = "SKILL.md"             # phase 6 (SPEC 37): read, never installed
def skill_files(pack_dir) -> list[tuple[str, str]]         # (name, path) of skills/<name>/SKILL.md
def validate(pack_dir, *, tier=Tier.BUILD, notes=None) -> list[str]   # problems; empty = ok
def baseline_context(pack_dir, *, out_dir) -> GateContext  # the ONE sealed baseline context
BASELINE_CLAIMS = "_claims"                                # issue #15 D2: the claims a baseline states
def baseline_claims(baseline) -> list[Claim]               # ... read, strictly: the known-good ledger
def demonstrate(pack_dir, *, tier=Tier.BUILD, out_dir=None) -> Demonstration
@dataclass
class Demonstration:                         # what `demonstrate` saw, gate by gate
    problems: list[str]                      # baseline failed, control did not fire, unsealed
    skipped: list[str]                       # availability skips: reported, never a problem
    ran: int                                 # controls actually exercised
    unchecked: list[str]                     # isolation checks a prerequisite's absent tools left unrun
def seal_findings(registry, host_ctx, *, tier=Tier.EXTERNAL,
                  out_dir=None) -> list[SealFinding]        # controls whose fixture read the HOST's params
@dataclass(frozen=True)
class SealFinding:                           # one unsealed control (invariant 5, checked at runtime)
    gate: str                                # the gate whose control read the host
    fixture: str                             # its NegativeControl.fixture
    host_paths: tuple[str, ...]              # the host-param paths read, sorted, dotted; "(all params)" = the top level
def match(need: Need, manifests, *, how="") -> list[PackManifest]  # gap -> candidate packs, by `settles`
def score(need: Need, manifest, *, how="") -> float        # 0 = no signal; what `match` ranks by;
                                                           #   how: the claim's mechanism words (phase 1),
                                                           #   half the quantity's weight; "" = as before
def installed(root, *, ledger=None) -> list[str]           # the project's opted-in packs, in order:
                                                             # project.json's `packs`, via store.load
                                                             # with check's model_prose (a legacy project)
def available(root=None) -> list[str]                      # every pack name discovery can see
def key_scope(manifest) -> str                             # "fdm-print" -> "fdm" (from its gate ids)
def key_vocabulary(name, root=None) -> dict[str, dict]     # every projection key the pack reads
def key_collisions(names, root=None, *, projection_keys=()) -> list[KeyCollision]
# Architecture phase 2 (SPEC 22, 25, 38): proposers — tools that propose, never settle
PROPOSERS_DIR = "proposers"
PROPOSER_KEYS = ("id", "basis", "description", "run")   # one provides_proposers entry
class Proposer(NamedTuple): id; basis; description; run; pack; pack_dir   # run absolute
def proposers(root=None) -> dict[str, Proposer]     # every discoverable pack's, first in
                                                    #   search order wins; no `packs add` needed
def find_proposer(root, pid) -> Proposer            # or NopekitError: no pack declares it
def proposer_basis(found, pid) -> str               # "" with none, its basis, or "undeclared"
def proposer_problems(pack_dir, manifest, gate_ids) -> list[str]   # validate's: never imports
```
A proposer is a script run as `python <run> [args]` by `nopekit propose`, with the
project as its working directory and `$NOPEKIT_PROPOSAL_DIR` a fresh directory under
`.nopekit/out/propose/`; it is never imported by the spine, never registered as an
evaluator, and needs no control (invariant 3: a proposal settles nothing).
`read_manifest` refuses a `basis` that is not one of `BASES` (the `max_tier`
precedent), `_load_dir` sets it on each gate module as `PACK_BASIS` beside `PACK` and
`PACK_DIR` — part of `load_source_module`'s cache key, never of the code digest — and
`validate` requires every evaluator of a bundled pack to resolve to a basis (a note
elsewhere) and runs `proposer_problems`.
`key_collisions` is what makes two packs wanting one word DETECTABLE rather than
discoverable: it diffs the installed packs' vocabularies (from each
`selftest/baseline.json`'s keys, `_notes` and `_aliases`) and reports any key two
of them declare differently. `nopekit doctor` renders it as a warning naming both
packs. See `docs/PACK_FORMAT.md`.
Pack layout (also documented in the pack-authoring skill):
```
packs/<name>/pack.json  PACK.md  references/*.md  gates/*.py  generators/*.py
             lenses.md  sourcing.md  scaffold/  selftest/
```
`load_gates` imports each `gates/*.py` with the pack directory on `sys.path` and a
module-level `PACK = "<name>"` and `PACK_DIR`, through `modelio.load_source_module`
(`roots=[pack_dir]`) inside `gates.use_registry(registry)`: fresh bytes, a recorded
closure, and a module reused only while it still hashes the same — re-adopting its gates
into the registry being filled (S-26). It records `registry.pack_dirs[name] = pack_dir`,
which a verdict entry's `<pack:NAME>` anchor reads. A second directory under a pack name
already loaded is still refused. Gate modules use the `@gate` decorator. A gate
registered with a blank `pack` is stamped through `Registry.set_pack`, never by
mutating the spec the registry stores.

**The host machine cannot change what is tested.** `search_paths`, `find` and
`load_gates` take `include_env=` and `include_user=`: `False` drops
`$NOPEKIT_PACK_PATH` and `~/.nopekit/packs` respectively, so a pack is loaded alone,
from where it was named, and a stray copy on the author's machine cannot shadow it
(S-87). The defaults keep today's precedence.

**`demonstrate` is admission, run.** `demonstrate(pack_dir, *, tier=Tier.BUILD,
out_dir=None)` loads the pack alone, from `pack_dir` itself with no lookup by name
(so env and user packs cannot stand in for it), into a fresh `Registry` and, per
gate within `tier`: the gate must pass its own
`selftest/baseline.json`; its negative control must fire; a skip is allowed only when
`gates.availability(spec)` fails, and is reported in `skipped`, never in `problems`; and
the **seal probe** — the control must also fire against an empty host (`params={}`,
`extra={}`, an empty `Ledger`), which is invariant 5 (SEALED) checked by running it.
Every run gets an explicit temp `out_dir` when none is given, so nothing is written
into the pack — including a wheel's site-packages; a given `out_dir` holds
`<gate id>/{baseline,control,sealed}`, each emptied before its run. `baseline_context(pack_dir, *,
out_dir)` is the one sealed baseline context everything builds from: the raw
`baseline.json` **including `_notes` and `_aliases`**, exactly as the test oracle
builds it, `root=pack_dir`, a `Ledger` of the claims the baseline states under
`_claims` (`baseline_claims`, which `validate` also runs) and nothing of the
host's, `extra={}`, the given `out_dir`, tier `EXTERNAL`. A pack gate reads its limit
from those through `ctx.acceptance` on its controls (issue #15 D2: cfd.pressure_drop,
whose limit had been a parameter beside the claim's). `validate(pack_dir, *, tier=Tier.BUILD, notes=None)` calls `demonstrate`
once the gates load; a missing-tool skip, and the gates above `tier` that were not
run, are appended to `notes` when a list is given and **never** to the returned
problems, so `validate(dir) == []` on a CI runner
with no solvers. What slipped through: `pack validate` never ran a control and
certified a planted `return True` as publishable (S-09); `gate selftest` tests only
the reject half, so an always-False gate passed it — the baseline run is the accept
half (S-04, packs); and the controls wrote into the pack directory on every run
(phase-1.md Q1.8).

**`seal_findings` is SEALED, checked by running it** (invariant 5, M2.1e). It runs
every **pack** control in `registry` (`spec.pack` set: a project's fixture deriving from
its own model is correct there) up to `tier` against `host_ctx` — a rich host, the pack's
own baseline in the gate-on-the-gates — and reads `trace.host_reads`: every param a
fixture (or a gate on a context its fixture returned unchanged) read from the HOST
rather than from what the fixture built. A sealed fixture reads none. The spine's own
`_fixture_root` lookups are `extra`, not params, and are not findings; nor, in P1, are a
fixture's reads of the host's `extra` or `ledger` — the trace cannot yet tell them from
its gate's (a named residual, P2.3 / D-26). `demonstrate` runs the same check as its
fourth step, over step 2's traced control run with the pack's baseline as the host,
and turns each finding into a `control` problem, so `pack validate` and
`gate selftest --pack` refuse an unsealed fixture. What slipped through before it: a fixture that layered its bad value over
the host's `ctx.params` fired in the pack's CI and was defused in a project whose
host happened to state the key it forgot — invariant 5 held only by review.

`origin_of` is printed everywhere a pack is listed, because a pack that is not the
one you are editing looks exactly like one that is: a tester pulled a fix, watched
the gate fail to appear, and lost ten minutes before finding the live copy was the
one inside the installed wheel.

### `decisions.py`  (deps: models, util, store, modelio)
```python
def add(ledger, *, title, summary, when, rejected=(), params_changed=(),
        claims_changed=(), body="", evidence=()) -> Decision
def render_log(ledger) -> str                # markdown, NEWEST FIRST
def write_log(root, ledger) -> str           # docs/decisions.md — `report --write`'s output
def why(ledger, name: str, *, view=None, coverage=None, read_sets=None,
        verdicts=None, composed=None, rows=None, bases=None, graph=None,
        entry="") -> str
                                             # one param, claim, requirement or decision:
                                             # value, rationale, rejected alternatives,
                                             # gates, grounding, and the decisions that moved it;
                                             # with `graph` (a `graph.Graph`, phase 3a) also
                                             # "read by:", "sourced from:", "rests on:" (and,
                                             # untraced, that only direct reads were followed),
                                             # "resolves:", "resolved by:", and a derived value
                                             # an evaluator read, by name (`entry` names the
                                             # model file its block cites); without it none of
                                             # those, and a derived name is "no such name"
def changed_in(ledger, name: str) -> str     # the decision that last moved a param: derived
```
From checkpoint 1.3 (U27) `add` no longer sets `Param.changed_in` — a copy in the param
record would be a second home for "which decision moved it" — and `changed_in` derives
it from the decisions' `params_changed`. `why`'s keywords are all
optional and every default keeps the old rendering: with a `modelio.ParamView` as `view`
it prints the model's value and where it lives (`param thickness = 7.0 mm
(model/bracket.py Config.thickness)`) and each rejection with its origin; a model that
does not load prints `model does not load: …` and no number.
From architecture phase 1 `why` resolves a requirement id after an exact param and
claim (then the three case-insensitively): `_why_requirement` prints its words, its
source, its status from `claims.requirement_statuses` over the caller's `composed`
(`claims.compositions(ledger)` without one), each serving claim in severity order with
its reason, and the decisions that name it. A claim's block gains `serves: R1, R3`
and a MECHANISM section (`report.mechanism_line`, and the evaluators it expects); the
`how` text moved there from WHY. It prints no registry warning: those are `check`'s and
`doctor`'s.
From architecture phase 2 `why` resolves a decision id after an exact requirement (and
case-insensitively after the folded requirements): `_why_decision` prints the decision
and each rejected alternative with a facts line — its type (a preference said recorded
or not by whom), its return condition's state now, its input
(`report.rejection_facts`) — from `rows` (the caller's `claims.rejection_rows`, on the
view it judged; composed from `claims.compositions(ledger)` without them). A param's and
a claim's REJECTED rows gain the same line under a decision's rejection, never under a
PARAMS or param-record row; a typed decision row replaces its untyped copy when they
dedupe. `bases` maps an input id to its proposer's basis (`proposed by <id> (basis <basis>)`
under GROUNDED BY). `add` takes rejections as dicts with `type`, `by`, `returns_if` and
`input`, held to `store.rejection_problem`; `render_log` adds each loser's type and
condition as written, never a live state.
`why` is the context-window win: an agent pulls one parameter's full history
instead of reading a 1,672-line decision log.

### `report.py`  (deps: models, util, store, claims, artifacts, verdicts, modelio)
```python
class StatusWords(NamedTuple): key; word; term; plural; tag; hint; rank   # one GLOSSARY §3 row
HUMAN: Mapping[str, Any]   # THE table (PLAN D-16): "status" {ClaimStatus: StatusWords},
                           # "errored" (Skipped's loud row, tag "SKIP "), "lead" {ClaimCause: str},
                           # "recorded" {named, unattributed}, "need" {NeedStatus: word},
                           # "outcome" {outcome: word}, "outcome_hint" {outcome: line},
                           # "outcome_tag" (models._RENDER_TAG itself), "heading", "refusal",
                           # "prerequisite" {failed, not-established, not-registered} (P2.2),
                           # "mechanism" and "requirement" (architecture phase 1)
STATUS_TAG: Mapping[ClaimStatus, str]        # HUMAN's tags, a view: PASS -> "ok   ", ...
SECTION_PROVEN = "## What is PROVEN"         # the checked section's heading (text: A-11)
JUNIT_DEFAULT = ".nopekit/out/junit.xml"    # `--junit` with no path; ignored scratch, never tracked
def words(status, *, errored=False) -> StatusWords;  def word(status, *, errored=False, n=1) -> str
def status_tag(status, *, errored=False) -> str      # "[FAIL ]", "[SKIP ]" for a crash
def severity(composed) -> int                # claims.severity; in_severity(ledger, composed, claims=None)
def count_line(composed) -> str              # "7 claims · 3 checked · 1 failing · 2 gaps · ..."
def count_bits(composed) -> list[dict]       # its items: {key, status, n, errored, label}
def reason(composed, ledger, claim, *, full=False, cut=None, stale_reasons=None) -> str
def status_view(composed, ledger, claim, *, stale_reasons=None) -> dict  # {key, word, cause, reason, errored}
def words_table() -> dict;  def outcome_words() -> dict;  def need_word(status) -> str
def page_phrases() -> dict                   # state.json `phrases`: {invalidated, need, outcome_hint}
def limit_words(acceptance) -> str            # a claim's limit beside a value: "0.5 mm", "0.2..0.8 mm", ""
def recorded_by(who) -> str                  # "recorded by dana" | "recorded, unattributed" — one line
def prerequisite_phrase(verdict) -> str      # "prerequisite failed: <root>" | "… not established:
                                             #   <root> (<kind>)", from HUMAN and the spine's mark
def readiness(ledger, composed, milestone=None) -> dict   # claims.unresolved's rows, + missing
def milestone_line(ledger, composed, milestone) -> str     # "<m>: k of n required claims checked · …"
def limits_line(ledger) -> str               # what Checked does not mean, said where ready is (P2.5b)
def site_headline(ledger, composed) -> str   # SPEC 40: the readiness sentence's first sentence
                                             #   ("v0.1 is NOT ready."), from its own builder
def site_rebuild_text(found) -> str          # a part to build again, in the site's words: the
                                             #   page's readiness clause and next steps' row
RESULT_TOKENS = ("pass", "fail", "not-counted")  # a recorded result's token: what a page
                                             #   colours it by (format.js `outcomeLook`)
def result_facts(claim, entry, *, register="cli") -> dict  # {counts, token, why, recorded,
                                             #   heading}; "site" adds `word`, the drawer's
def latency_words(claim, exports) -> str     # "measured 26 h on article <a12> (expected 1 day)" |
                                             #   "expected 1 day (declared)" | that none is declared
def not_ready_line(ledger, composed) -> str  # `check`'s line when nothing blocks it
def unseen_words(key, resolution, unseen, *, registry=None, rows="", spend=False) -> str
                                            # issue #15 D1: HUMAN["unseen"][key] for the gates
                                            #   claims.unseen_gates found: a count, one example;
                                            #   spend: name `export` (a milestone exists)
OPAQUE_CONTROL_REASON = "control inputs moved: opaque control inputs: "
def unseen_qualification(admission) -> bool  # qualified at this version, its controls opaque:
                                             #   `gate show` and `doctor` say so, never "the
                                             #   next check run qualifies it"
RATIONALE_UNKNOWN = "Parameter rationales are not known"  # + ": <why>" — no view, or no model
def render_terminal(ledger, registry, *, stale=False, stale_gates=(), params=None,
                    stale_reasons=None, unseen_gates=()) -> str
def render_markdown(ledger, registry, *, stale=False, stale_gates=(), model_error="",
                    title="", root="", params=None, stale_reasons=None,
                    milestone=None, unseen_gates=()) -> str  # milestone: that spend's (P2.5b)
def write_report(root, ledger, registry, *, stale=False, stale_gates=(), model_error="",
                 params=None, stale_reasons=None, unseen_gates=()) -> str  # REPORT.md (P2.5b)
def render_junit(ledger, verdicts, registry, *, tier, ready, exit_code, when,
                 not_run=None, cached=frozenset(), stale=False, spine="",
                 stale_gates=(), stale_reasons=None, unseen_gates=(),
                 ready_note="") -> str
    # `check --junit`: suites gates / claims.critical / claims.not-critical;
    #   ready_note: the property that says a ready holds for this check run only
def render_selftest_junit(results, *, exit_code, when, baselines=None) -> str
    # `gate selftest --junit`: suite controls, plus baselines in pack mode
def junit_safe(text) -> str                  # XML-1.0-illegal code points -> visible "\xNN" / "\uNNNN"
# Architecture phase 1: the mechanism (SPEC decision 13) and requirements (decision 12)
def mechanism_body(claim) -> str             # "<by's display word> — <how>", or where to say it
def mechanism_line(claim) -> str             # mechanism_body, labelled "mechanism: " (gap's line)
def mechanism_problem_words(problem) -> str  # one claims.MechanismProblem, in words: no status moves
def status_word(status, *, errored=False) -> str   # word, with ", errored" for a crash's Skipped
status_token_word = _status_token_word       # phase 3a: a STATUS_KEY token (or "errored") in words
def round_paragraph(shape, number, changes, *, view, composed, check, site, wrote, schema,
                    path, trace=None) -> str # phase 3a: `round`'s one paragraph — what changed,
                                             #   the count line, ready as last evaluated, the
                                             #   check, the site; HUMAN["round"]'s words
def round_trace_sentence(trace) -> str       # phase 3b: the background trace's sentence (" ..."),
                                             #   or ""; round_paragraph's tail, and appended by
                                             #   cli after phase 6's commit sentence
def round_moved(moved) -> str                # "the records, the verdict cache and the design"
COMMIT_SUBJECT_MAX = 72                      # phase 6: a round commit's subject, git's convention
def round_commit_sentence(commit) -> str     # phase 6: what `round --commit` did, then the
                                             #   changed paths it named instead of staging: a
                                             #   secret's name, over 50 MiB, not a file
def round_prose_sentence(prose) -> str       # phase 6: board text stamped before the latest
                                             #   results, named; "" when none is behind
def round_commit_message(number, shape, changes, staged, not_files, too_large, secret,
                         earlier, paragraph, *, round_rel="") -> tuple[str, str]
                                             # phase 6: "round N: <what changed>" (compacted,
                                             #   then cut, past 72) and its body; no trailer
def round_hook_message(outcome) -> str       # phase 6: the Stop hook's systemMessage
def round_hook_crash(exc) -> str             # phase 6: its message for a crash
def requirement_statuses(ledger, composed, cover) -> dict[str, RequirementStatus]
                                             # claims.requirement_statuses, each row's disagree
                                             #   filled: serving claims _disagreement names (D18)
def requirement_line(ledger, composed, cover=None) -> str | None   # status's one line, or None;
                                             #   past eight, worst first and "+N more (k unresolved)"
def requirement_json(requirement, found) -> dict       # the one JSON row: status --json, report --json,
                                             #   state.json — {id, statement, source, status, key, word,
                                             #   errored, claims, worst, disagree}
# Architecture phase 2 (SPEC 14): a rejection's words, from claims.rejection_rows' tokens
def condition_text(cond) -> str              # "C3 margin < 0.1", or the text
def return_words(row, ledger=None, *, register="cli") -> str   # the condition's state now:
                                             #   "holds: …, now 0.987 (bracket.bearing)",
                                             #   "cannot be evaluated: C1 is failing"; the site
                                             #   register's sentence holds no record text
def rejection_words(row, ledger=None, *, register="cli") -> tuple[str, str]   # (type phrase,
                                             #   condition phrase); with register="site",
                                             #   "Preferred against, not recorded by <name>"
                                             #   (the CLI's invariant-13 phrase, capitalised)
def rejection_facts(row, ledger=None) -> str # why's facts line: "{type} · {condition}[ · source: input …]"
def may_return_line(row, ledger=None) -> str # check's line for a condition that holds
def rejection_suffix(rejection) -> str       # decide's row and the log's: static, never a live state
```
**Requirements and mechanisms in the renderers (architecture phase 1).** Every status a
requirement shows is `claims.requirement_statuses`', worded here: `status` prints one
untagged line after the claim rows and the rebuild lines (`requirement_line`, so no
claim-row parser reads R1 as a claim, and only when requirements are recorded); the
report's `## Requirements` (`_section_requirements`) comes after the Failing section and
before Reproduce, so `SECTION_PROVEN` stays first, and is empty when none is recorded;
there is no JUnit suite. Every one of those, `why` and the JSON rows reads
`requirement_statuses` here, never the bare producer: a requirement over a claim the
resolver calls Checked while its evidence disagrees (D18) names that claim — `R1
checked (status and evidence disagree: C2)` — never a bare "checked" one line under
the claim row that lists it as a contradiction (review of phase 1). Past eight
requirements the line keeps the worst first (`claims.SEVERITY_ORDER`, a disagreement
first) and says how many of the rest are unresolved. `claim_json` gains `mechanism` (`{by, how, evaluators}` as
declared, all three keys always present) and drops the flat `evaluators`. `reason` is
unchanged: the `how` shows in `gap`, `why`, the report's gap section (a `Mechanism:`
sub-bullet under each claim) and JSON, never in a Gap's reason.
**`HUMAN` is the one table** every human word for a status, a cause's lead and an
outcome comes from (PLAN D-16; GLOSSARY §7), equal to GLOSSARY §3 (Table 1's seven
words plus Open: Checked, Failing, Stale, Assumed, Pending build, Gap, Skipped, Open)
and held there by `tests/test_vocabulary.py`, which also patches it to sentinels and
requires every channel to move with it. Its readers are the helpers above, and every
channel — `status`, `check`, `claim`, `why`, the report, JUnit, `state.json` and the
page's labels — goes through them. Tags are five wide; the loud ones (upper case) are
exactly Failing, a crash's Skipped and Stale. It lives here, not in `models` (a word is
not a type, and a spine module's edit re-keys every cache entry), and not in the page
(a status word is truth, and the site never owns one). What slipped through before it
(S-69): `[uncl]` in `check`, `[gap  ]` in `status`, NO GATE on the page.

**`reason`** is the one producer of the sentence that explains a claim's status, led by
the fact (`HUMAN["lead"]`): a fail cites `<gate> : <detail>` (` (invalidated: <why>)`
when its inputs moved, D-08); a crash `errored: <gate> : <exception's first line>`,
never the traceback; a skip `skipped: <gate> : <reason>`; a refusal `unqualified:
<gate> : <why>` (its words in GLOSSARY §2's, `HUMAN["refusal"]`); `no evaluator`;
`no owner recorded`; `owner X is named in claims/<id>.json and has not recorded it —
no command can record it yet`; `no reason recorded`; `unrun: <gates>`; `invalidated:
<gate> : <what moved>`; `needs an article` (`; no test written down` with neither
acceptance nor `mechanism.how`); `assumed by <owner>: <rationale>`; `a pass recorded by <who>, not
bound to an article` (`recorded, unattributed` with no one named, `recorded_by`).
`cli._blocking_reason` and `report._terminal_reason` wrap it (S-68: two copies once
told two stories). **Every value a record supplies — an owner, a result's `who` and
`when` — is one line** before it reaches a line-oriented channel (whitespace runs,
newlines included, collapsed). What slipped through (review of P2.1): an owner or a
`who` holding a newline went in raw and wrote its own lines — a second `## What is
PROVEN` heading with a forged row in the report, an `[ok   ]` row and a `ready:` line
under the real `[FAIL ]` in `check` and `status`.

**The readiness sentence** says *ready* only when every required claim reads Checked
on the current inputs (GLOSSARY §4: `readiness`), else `NOT ready: u of n required
claims are unresolved — <groups>` with every unresolved required claim in severity
order (`1 failing (C1); 2 gaps (C6, C7); 1 pending build (C5)`, Skipped as `N skipped
(k errored)`), then `C of T claims are checked against the current inputs.`; claims
not required that are unresolved, grouped by word the same way (a crash counted apart);
unrun gates; and **in every branch** the hardware sentence — `Pending build: N claims
need an article (ids)`, naming apart those with a pass recorded that no article binds
(W13). Every branch that is not ready says `NOT ready`, the never-evaluated one too
(`v0.1 is NOT ready and has never been evaluated: …`). A required claim whose only
evidence is a typed physical pass reads Pending build and keeps the project NOT ready:
nothing binds a typed pass to what is being built (until article binding).

The markdown report has this shape, generated: the readiness sentence / `SECTION_PROVEN`
(the checked claims, each row citing the evaluators that passed) / **Pending build**
(what waits on an article, and passes recorded that no article binds) / **Gaps** (every claim reading
Gap, by cause: gap records and tool options, unqualified evaluators, assumptions nobody
owns — P2.1-D19) / **Assumed** (Assumed claims, undefended numbers, unread evidence) /
**Failing, stale, skipped or open** (each claim's head in its word, its reason, its
evaluators in outcome order) / **Reproduce**. Every claim is listed in exactly one
section, its status's (`test_invariants.EveryUnresolvedClaimIsListed`). It must never
list a claim as Checked while an evaluator of it is unrun, skipped, errored or
unqualified; a `pass` its evidence contradicts is printed, loudly, in the failing
section as `status and evidence disagree — <gate> <why>`, never in the checked one, and
its JUnit case is a red `status-and-evidence-disagree` failure (P2.1-D18). PARTIAL is
gone: under the composition there is nothing left to mark.

**Staleness is per gate** (Phase 1.2). The renderers take the resolution's
`stale_gates` (`verdicts.resolve`): a PASS whose covering gate is in it reads STALE and
is never under PROVEN; a stale FAIL stays FAIL (`claims.resolve_status`). `stale=True`
stays the all-gates override. The report reads no sweep time and no rho: its title is
`(<rev>)`, and `## Reproduce` lists `nopekit check` and each gate's code files, so a
regenerated `REPORT.md` changes only when the claims or the verdict outcomes
do. The code files are spelled as the verdict cache spells them (`gates/structural.py`,
`<pack:NAME>/gates/…`), which needs the project: `render_markdown` lists them only when
handed `root=` (`write_report` passes it) and otherwise leaves them out, never spelling
them by this machine's absolute paths (S-89). `render_junit` takes `stale_gates` too, so
`check`'s claim suites are judged from the same resolution as its exit code.

The checked section's heading line **starts with `SECTION_PROVEN`** (its qualifier
follows on the same line and is not part of the constant: "(checked: every evaluator
passed on the current inputs — checked does not mean true)" from P2.1;
"(machine-verified, current)" before, a GLOSSARY Never-say). Invariant 4's tests find the section by that constant and fail when it is
absent, duplicated or empty. What slipped through (S-15): they searched for a literal
copy of the heading and read "no heading" as an empty section, so `assertNotIn` passed
on nothing, and a rename would have kept the invariant green while it tested no
report. A rename changes the constant's text, never its name (PLAN D-14, A-11).

`render_terminal` prints the head, the readiness sentence, `count_line`, one line per
unresolved claim in severity order — `status_tag(status, errored=)`, the claim, and
`reason` — then the gap records with their tool options (`gap records N:`, never
"gaps": the count line counts the claims that read Gap), the gate tally keyed on
`Verdict.outcome` (`N ran, k invalidated, s skipped, e errored, q unqualified, u
registered but unrun`: a skipped-and-errored verdict is one crash, P2.0 F-10), and
`next:`. A claim whose status its evidence contradicts is listed first. What slipped
through (S-68): `status` cited the first non-passing verdict, a skip, while `check`
cited the gate that ran and failed.

`params` is the caller's `modelio.param_view`: the standing constraints list
`modelio.undefended_params` over it (value from the model, source from the record,
the gates that read it) and `render_terminal`'s `standing:` line counts the same
list — never `ledger.params`, the sparse records (review, checkpoint 1.3). With no
`params`, or with `model_error` or a view carrying one, the section says
`RATIONALE_UNKNOWN: <why>` and never "every parameter carries a rationale".

`store` is in the deps for two reasons: `write_report` takes its destination from
`store.project_paths(root)["report"]`, and the Reproduce file list anchors its
paths with `store.out_dir(root)`. `verdicts` is in them for `anchors_for` and
`code_digest`, the one spelling of a gate's code files. Layout is `store`'s job alone, and a
second module that knows where the report lives is a second module to
edit when it moves — as it did in P2.5b, from `docs/readiness.md` to an ignored
`REPORT.md` at the root: committed, it drifted from its ledger (S-41).

A covering gate that produced no pass that counts (`_unproven_for`) is named with its
reason led by the fact — `unrun`, `errored: <exception>`, `skipped: <reason>`,
`unqualified: <why>`, `fail: <detail>`. Coverage for it comes from
`claims.effective_gates` — the union — so it survives the pack going missing, which is
when it matters most. Beside a Checked status it is a contradiction (above).

**JUnit is never greener than the exit code.** CI renders the XML, not the exit
code, so `render_junit` carries the judgement the exit code was made from and may
only ever be redder. Built with `xml.etree.ElementTree` (imported inside the two
renderers: about 6 ms that only `--junit` should pay). The shape (PLAN §3 row M2.1a,
phase-1.md 1.1):
```
<testsuites name="nopekit check" tests= failures= errors= skipped= time=>
  <properties> spine_version exit_code tier ready all_required_checked when [spine] </properties>
  <testsuite name="gates">            one testcase per REGISTERED gate, registry order
    <testcase classname="project"|"pack.<pack>" name="<gate id>" time="<duration_s>"/>
  <testsuite name="claims.critical">  one per critical claim: "does not block the spend"
  <testsuite name="claims.not-critical">
```
- **`gates`.** A testcase is childless **iff** its verdict's `outcome == "pass"` —
  `Verdict.outcome`, never `passed`, so a skip that also says `passed=True` is a
  `<skipped>` (R-5). fail → `<failure type="fail" message=detail>measured … vs limit
  …</failure>`; error → `<error type="error">`, or `type="not-admitted"` for a refused
  evaluator — keyed on `Verdict.unqualified`, never the text (P2.1: a gate whose own
  crash says "not admitted: …" is a crash); skipped → `<skipped message=skip_reason>`. A
  registered gate with no row in `verdicts` → `<skipped message="not run: <why>">`,
  the why from `not_run` (`(gate, reason)` pairs or a mapping: `above the tier
  ceiling`, `excluded by --only`), else `no verdict in this run`. A gate in `cached`
  has `time="0"` and `<properties><property name="cached" value="true"/></properties>`
  — metadata, not an outcome.
- **`claims.critical`** is recomputed from `ledger` with `stale`, never from
  `verdicts`: its red testcases are exactly `claims.blocking(ledger, registry,
  stale=stale)`, so **failures + errors == `len(blocking())`**, and `check` exits 1
  iff that count is positive. A claim Skipped by a crash is `<error type="error">`,
  critical or not (PLAN-v0.14 §1.5); any other red claim is `<failure type="<enum
  value>" message="<reason>">` — `report.reason`, the words `status` and `check` print.
  A `pass` its evidence contradicts is `<failure type="status-and-evidence-disagree">`
  (P2.1-D18; PARTIAL went). Pending build → `<skipped message="pending build: needs an
  article…">`; Assumed → `<skipped message="assumed: assumed by <owner>: …">`. `type`
  keeps the enum value (machine, GLOSSARY §7); messages are words. Zero claims → one
  failing testcase `no claims recorded`. The root property `ready` is the caller's
  (nothing stops `check`); `all_required_checked` is *ready* (GLOSSARY §4).
- **`claims.not-critical`**: Failing red, a crash an `<error>`; every other claim short
  of Checked `<skipped message="<word>: <reason>">`.
- **An exit code nothing explains is itself red.** If `exit_code != 0` and
  `claims.critical` has nothing red — a caller that judged a stale project stale and
  rendered it with `stale=False` — a failing testcase `exit code` is added. The
  caller's disagreement shows as red, never as a green file beside a red job. Pass
  the ledger, and the `stale` flag, the exit code was judged from.
- **`render_selftest_junit`**: suite `controls`, one testcase per `gates.selftest`
  result named for the gate (`#selftest` dropped), childless iff the control fired;
  a control that did not fire or crashed is red; a tooling skip is `<skipped>`. In
  pack mode (`baselines` not None) a `baselines` suite holds each gate's verdict on
  its own `selftest/baseline.json`. Exit non-zero with nothing red adds a failing
  `no controls ran` (no results) or `exit code` testcase: zero controls is never an
  empty, green file.
- **`junit_safe`** replaces the code points XML 1.0 forbids — `\x00`–`\x08`,
  `\x0b`, `\x0c`, `\x0e`–`\x1f`, every surrogate, U+FFFE, U+FFFF — with visible
  `\xNN`/`\uNNNN` text; tab, LF, CR and everything else are kept. Every attribute
  and text value goes through it. What slipped through while designing it: ElementTree
  writes each of them raw — an ANSI escape from a solver log, a NUL, a lone surrogate
  from `surrogateescape` bytes — and the file fails to parse, so CI shows no
  failures at all.
- `when` is the caller's timestamp (contract rule 3); `spine` is the spine digest,
  passed by the CLI from Phase 1.2. The CLI edge — unlink the target first, one exit
  code, write atomically at the single exit, the `.xml` suffix rule — is `cli.py`'s.

### `graph.py`  (deps: models, util, store, artifacts, claims, gates, verdicts; report only inside `tokens`)
The typed falsification graph (SPEC decision 19, architecture phase 3a): one graph
from the records, the registry, the resolution and the verdict entries it served —
their read sets, never `obs/`. Not in `verdicts.SPINE_MODULES`: an edit here keys no
verdict. No clock, no git, no file writes, no words. The site's map (the next phase)
calls `build` and `tokens` — or `of` — directly, and joins the two by id.
```python
SCHEMA = 2                                            # phase 3b: `after`, trace an object
NODE_KINDS = ("requirement", "claim", "evaluator", "value", "instruction", "input",
              "milestone", "decision")                 # the order nodes sort in
EDGE_TYPES = ("serves", "settles", "reads", "derives", "after", "sources", "rests_on",
              "resolves", "contradicts", "needs", "requires", "decides")   # the order edges sort in
VALUE_ROLES = ("choice", "derived", "absent")
class Node(NamedTuple): id; kind; attrs               # id "<kind>:<key>"; attrs JSON scalars,
                                                      #   one schema per kind (below)
class Edge(NamedTuple): type; source; target
class Trace(Protocol): key; round; derives; after     # derivation.TraceFile: (choice, derived)
                                                      #   and (earlier, later) pairs; round
                                                      #   and after read with getattr
class Graph:                                          # frozen: nodes, edges, trace, instructions,
                                                      #   trace_round
    def node(self, nid) -> Node | None
    def out(self, nid, type=None) -> tuple[str, ...]  # targets, edge order
    def into(self, nid, type=None) -> tuple[str, ...] # sources, edge order
    def values(self, role=None) -> tuple[Node, ...]
    def read_by(self, name) -> tuple[str, ...]        # evaluator ids, natural order
    def sourced_from(self, name) -> tuple[str, ...]   # node ids, node order
    def rests_on(self, cid) -> tuple[str, ...]        # assumption claim ids
    def via(self, cid, aid) -> tuple[tuple[str, str, str], ...]   # (evaluator, read, sourced)
    def moves(self, choice) -> tuple[str, ...]        # the derives targets
    def moved_by(self, name) -> tuple[str, ...]       # the derives sources
    def to_dict(self) -> dict                         # {schema, trace: null | {key, round},
                                                      #   instructions,
                                                      #   nodes: {id: attrs}, edges: {type: {src: [dst]}}}
class Tokens(NamedTuple): claims; requirements; evaluators; milestones; readiness; counts; disagree
def node_id(kind, key) -> str
def value_names(path, choices, derived=()) -> tuple[str, ...]   # THE path rule:
                                                      #   ["config", k] -> k, ["config"] -> every
                                                      #   choice, [k] -> k, [] (the whole of
                                                      #   ctx.params) -> every choice and derived
def derived_names(projection) -> tuple[str, ...]      # the projection's derived keys, less an
                                                      #   echoed `config`
def served_reads(resolution, registry) -> dict[str, tuple[tuple, ...]]   # registered gates'
                                                      #   Row.entry param paths
def value_sources(ledger, views) -> dict[str, tuple[str, ...]]   # name -> input:/claim: ids,
                                                      #   from artifacts.value_grounding
def observe(views, *, ledger, resolution, registry, choices=(), derived=()) -> list[ParamView]
                                                      # each view's read_by and sourced_from
def build(view, resolution, registry, *, projection, design, views=(), bases=None,
          trace=None) -> Graph
def tokens(view, resolution, registry, *, composed=None) -> Tokens
def of(root, view, resolution, registry, *, projection, model, views=(), bases=None,
       trace=None) -> Graph                           # build, design = verdicts.design_of
def upward(graph, value) -> list[str]                 # phase 5: a value node id and every
                                                      #   value it derives from
def upstream(graph, nid) -> list[str]                 # phase 3b: `after` ancestors, nearest first
def direct_derives(graph) -> set[tuple[str, str]]     # (choice, value) no earlier value explains
def depths(graph) -> dict[str, int]                   # longest `after` path to each value
def ties_of(graph) -> list[tuple[str, ...]]           # values the same choices move
```
**Nodes.** A requirement, a milestone and a decision carry no attrs; a claim
`{claim_kind, by, critical, removed}` (every `view.claims`, a `Ledger.removed`
fail-holder included); an evaluator `{pack, basis, tier, registered}` (every registered
spec, then any gate a `needs` or a `contradicts` names that is not registered, with
`null` pack, basis and tier); a value `{role, digest[, value]}` — every choice, and
every other name an edge or a parameter view names (a derived value nothing reads,
sources, decides or derives is no node), its `value` written only for a finite int,
float or bool; an instruction `{digest}` per file of the model's code closure as an
article digests it (`model/bracket.py`, `<root>/` stripped); an input `{proposer,
basis}`.

**Edges, each from one source.** `serves` (`Claim.requirements`), `settles`
(`claims.effective_gates`: coverage, never the mechanism's list), `reads` (the served
entry's param paths through `value_names`; a never-run evaluator has none), `derives`
(the trace's; `{}` and `trace: null` without one), `sources` (an input id as
`input:<id>`, an assumption's as `claim:<id>`), `after` (phase 3b: the order the
trace infers between derived values, `derivation.after_order` — for drawing and `why`
only, never an edge of meaning), `rests_on` (computed: a claim rests
on assumption A when an evaluator that settles it read a value A sources, closed
upward over `derives`), `resolves` (`Claim.resolves`, physical to assumption only),
`contradicts` (`Resolution.track`), `needs` (`GateSpec.needs`), `requires`
(`Milestone.requires`), `decides` (`params_changed`, `claims_changed`). An edge is made
only when both ends are nodes: a pointer to a missing or wrong-kind record makes none,
and `store.pointer_problems` names it. **No edge changes a status:** `claims.compose`
never reads this module (`test_graph.AnEdgeChangesNoStatus`). **Tokens** are each from
its one producer — `claims.STATUS_KEY` of `compositions` (or `errored`),
`report.requirement_statuses`, `claims.unresolved`, `claims.summarise`'s counts,
`report._disagreement` — over the view `status` judges, never a sweep's overlay. An
evaluator's `outcome` is `claims.outcome_key`'s, in `OUTCOME_ORDER`'s words: a crash is
`error` there (and `prerequisite-errored` behind a crashed prerequisite), where a
claim's and a requirement's token says `errored` — one vocabulary per kind of node.

### `rounds.py`  (deps: models, util, store)
The round record, `.nopekit/round.json` (SPEC decision 18, architecture phase 3a): an
output, tracked, never read for truth. Imported only by `cli`
(`test_round.NothingReadsTheRoundRecordForTruth`). No clock, no git: from phase 6 it
also decides which paths a round commits and whose identity may commit them, and `cli`
asks git (`vcs`) and hands the answers in.
```python
ROUND_REL = ".nopekit/round.json"; ROUND_NAME = "round.json"; SCHEMA = 1
HEADER: str                                           # the one sentence it holds
MOVED = ("records", "verdicts", "design", "trace")    # what it is computed from
TRACE = "trace"                                       # phase 3b: derivation.trace_digest
AMBER_KEYS = ("flips", "holds", "unclear", "not_current", "untraced")
RESOLUTION = "resolution"                             # what `state` names when none of MOVED
                                                      #   moved and what it records differs
KEYS = ("generated", "schema", "round", "computed_from", "readiness", "milestones",
        "counts", "claims", "requirements", "disagree", "amber", "evaluators", "graph",
        "self")
class Previous(NamedTuple): state; doc; round; schema; why   # absent | ok | edited |
                                                      #   unreadable | newer
class RoundState(NamedTuple): state; round; moved; schema; why   # absent | current | behind |
                                                      #   edited | unreadable | newer
class Flip(NamedTuple): id; before; after
class Changes(NamedTuple): compared; replaced; claims; requirements; verdicts; instructions;
                           readiness; moved; unrecorded=False   # instructions compared only
                                                      #   when both records hold the model's code
class Written(NamedTuple): written; round; previous; changes; refused
def path_of(root) -> str
def verdicts_digest(root) -> str                      # every file under .nopekit/verdicts/
def computed_from(root, design, trace_digest="") -> dict   # {records, verdicts, design, trace}
def body(computed, tokens, graph, amber=None) -> dict # the content, round and self unset;
                                                      #   amber {state: [claim ids]}
def self_digest(doc) -> str                           # util.seal of doc minus "self"
def sealed(content, round_) -> dict                   # KEYS order, round set, self set
def render(doc) -> bytes                              # indent 2, UTF-8 as itself, one newline
def read(root) -> Previous                            # strict JSON; never raises on content
def state(root, computed_now, content=None) -> RoundState   # content: what round would
                                                      #   write now; differing -> behind (RESOLUTION)
def changes(previous, body, computed_now) -> Changes
def write(root, body) -> Written                      # only when what it holds moved
# architecture phase 6: the round's commit — which paths, and whose identity (pure).
# Every file git does not ignore inside the project's directory, since the acceptance
# run after phase 3b (the closed list ROUND_DIRS, ROUND_NOPEKIT and ROUND_ROOT_FILES is
# gone: the agent's own bom/bom.json was never committed)
ROUND_MAX_BYTES = 50 * 1024 * 1024                    # a larger file is named, not staged
ROUND_SECRET_NAMES = (".env", ".env.*", "*.pem", "*.key", "*.p12", "*.pfx", "id_rsa*",
                      "id_dsa*", "id_ecdsa*", "id_ed25519*", "*credentials*", "*secret*")
ROUND_NAMED_BY_NOPEKIT = (".nopekit/verdicts/", ".nopekit/trace/")   # never read for a name
class StagePlan(NamedTuple): staged; not_files; too_large; secret   # too_large: ((path, bytes), ...)
def secret_name(rel) -> str | None                    # the pattern a part of rel matches, case
                                                      #   ignored; None under ROUND_NAMED_BY_NOPEKIT
def stage_plan(root, changed) -> StagePlan            # os.lstat; a deletion is staged whatever
                                                      #   its name; a directory, a secret's name and
                                                      #   a file over the cap are named
def person_of(ident) -> Person | None                 # "Name <email>" split at its last "<"
def listed(people, ident) -> Person | None            # name equal stripped, email ignoring case
def last_committed_round(raw) -> int | None           # HEAD's round.json bytes -> its round
```
**The seal comes first.** A record whose self-digest does not hold reads `edited`
whatever its `schema` says — the seal rule is the format's at every schema — so a hand
edit that bumps `schema` is rewritten by the next round; only a sealed record with a
greater schema reads `newer`. **Behind** names the records, the verdict cache or the
design when one of their digests moved, else the resolution when what the record holds
differs from what `round` would write now (`cli._round_state` hands `state` that body):
an evaluator's code, its controls, a pack or a tool moved it. **The write rule.**
Nothing over a newer schema (a newer nopekit's record is never downgraded); nothing when the record on disk reads `ok` and holds the same on every key
but `generated`, `schema`, `round` and `self`; otherwise round = the previous round + 1
(an edited record's included), else 1, and bytes only where they differ. **The design
digest** is `util.seal(verdicts.design_of(...))` — the current article's `built_from` —
so a model or data-file edit that files no verdict still reads behind; there is no spine
or version digest, so an upgrade is not "behind the records".

### `site.py`  (deps: models, util, store, claims, decisions, graph, page, prose, report, modelio, gates, verdicts)
The project site's spine half: viewgens, the technical record's document
(`state.json`), and the orchestration of a build — the two pages' documents are
`page`'s, the prose `prose`'s. The page's half is plain HTML, CSS and ES modules in
`site_template/`, copied by `scaffold`; the data contract between the two is
`docs/SITE_CONTRACT.md`.
```python
SITE_DIR = "site"; DATA_DIR = "data"; ASSETS_DIR = "assets"; VENDOR_DIR = "vendor"
VIEWS_DIR = "data/views"                     # per-view payloads too big to inline
STATE_NAME = "state.json"                    # site/data/state.json: the technical record
PAGE_NAME = "page.json"; MAP_NAME = "map.json"   # the two pages' documents (phase 5, SPEC 40)
BUILD_NAME = "build.json"                    # the clock: ignored, never tracked (SPEC 32)
CONTENT_DIR = "content"                      # site/content/: the agent's prose and figures
EVIDENCE_DIR = "assets/evidence"             # evidence copied from .nopekit/out/
SHELLS = ("index.html", "engineering/index.html", "marketing/index.html",
          "technical.html")                  # the project's own: never refreshed (SPEC 40)
PAGES = (("engineering", "engineering/"), ("marketing", "marketing/"))   # the menu's order
OUTLINE_MARKER = '<meta name="nopekit-outline" content="1">'; OUTLINE_MARKER_RE: str
VIEWS_MARKER = '<meta name="nopekit-views" content="1">'; VIEWS_MARKER_RE: str   # the root
                                             #   redirect's: without it, an old front page
OLD_LAYOUT_WARNING: str                      # move site/index.html aside, then site init
AGED_SECONDS = 86400                         # a result this old is aged (build.json)
WATCH_DIRS: tuple[str, ...]                  # what `site serve --watch` polls
@dataclass
class ViewContext:                           # a viewgen's one argument, mirroring GateContext
    root; ledger; model; params; assets_dir; log; extra; written   # written: assets recorded
    def param(self, name, default=None) -> Any
    def asset_path(self, name) -> str        # where an asset would land; creates nothing
    def write_asset(self, name, data) -> str # writes it; returns the site-relative path for View.src
@dataclass
class BoundEntry:                            # the verdict entry a bound viewgen draws
    gate; name; fresh; files; withheld       # files: {portable: absolute}, ONLY the bytes
    def path(self, basename) -> str | None   #   the entry's manifest names; withheld: why not
@dataclass
class BoundContext(ViewContext):             # a bound viewgen's argument: no model, no params,
    entry: BoundEntry; scratch; view_id      #   no claim's acceptance (issue #15)
    def bind(self, inputs) -> dict | None    # {path: sha256} -> {path: absolute}; each digest
                                             #   held by a verified evidence file, each file
                                             #   at it; re-hashed after the call
    def write_asset(self, name, data) -> str # into .nopekit/out/figures/<g>/<E>/<view>/; a PNG
    def write_live(self, name, data) -> str  #   or a 3D file, at most 2 MiB (raises over it)
    def note(self, text) -> None             # a build warning, under the view's id
    def asset_path(self, name)               # raises: nothing reaches site/ unchecked
@dataclass
class ViewSpec:                              # registration record for one viewgen
    id; kind: ViewKind; title; description; requires_python; requires_tools; pack; order; gates
    evidence_of: str = ""                    # the gate whose served entry it draws (issue #15)
    recipe: tuple = ()                       # files whose bytes key the memo, with the module's
FIGURE_SCRATCH = ".nopekit/out/figures"; FIGURE_RECIPE_SALT = "nopekit-figure-v1"
BOUND_KINDS = (IMAGE, CHART, MODEL3D, FIELD) # what a bound viewgen draws
CHART_FORBIDDEN_KEYS = {"limit", "limits", "current", "marker", "now"}   # a bound chart's
CHART_NUMBER_KEYS = ("series", "band", "estimates", "rule")  # every number: in the evidence
CAPTION_STATUS_PHRASES: tuple                # with every status word: never in a caption
class ViewRegistry:                          # register / unregister / clear / get / specs / ids / pairs
    def add_tool_check(self, tool, fn, *, pack="") -> None   # a module's TOOL_CHECKS entry
    def tool_checks(self) -> dict            # {tool: (pack, check)}
    def view_tools(self) -> list[str]        # every requires_tools of a view, then each checked tool
VIEW_REGISTRY: ViewRegistry                  # module-level default
def active_view_registry() -> ViewRegistry   # the registry `@viewgen` decorates into right now
def use_view_registry(registry)              # context manager, as gates.use_registry
def load_viewgens(directory, registry, *, pack, root) -> list[ViewSpec]   # import views/*.py:
                                             #   the one loader of a build, `site status`,
                                             #   `doctor` and `pack validate` (P2.2)
TOOL_CHECKS_NAME = "TOOL_CHECKS"             # a view module's {tool: check(root) -> (ok, words)}
NO_STATUS_WORDS = "No status depends on them."
def view_tool_rows(registry, root, *, skip=()) -> list[tuple[bool, str]]   # doctor's rows for
                                             #   the tools the views draw with
def viewgen(*, id, kind, title="", description="", requires_python=(), requires_tools=(),
            pack="", order=100, gates=(), registry=None, evidence_of="", recipe=())
                                                            # decorator -> registers, returns fn
def availability(spec: ViewSpec) -> tuple[bool, str]       # as gates.availability, for a viewgen
def run_viewgen(spec, fn, ctx) -> tuple[View | None, str]  # never raises; None is "nothing to draw"
def run_all_viewgens(registry, ctx, *, only=None) -> tuple[list, list]   # (views, notes);
                                                            #   unbound viewgens only
def derive_explode(bounds, *, overrides=None) -> dict      # a FIRST DRAFT explode manifest
def locator_problems(views, verdicts) -> list[dict]         # every locator that cannot be drawn
def scaffold(root, *, force=False) -> list                  # copy the template; the shells only
                                                            #   when absent, or with force (all four)
def refresh_renderer(root) -> list                          # every template file but SHELLS
def scaffolded(root) -> bool                                # site/index.html is a file (the root,
                                                            #   never engineering/: invariant 8)
def outline_file(root) -> str                               # engineering/index.html when it exists,
                                                            #   else an old project's index.html
def outline_of(root) -> int | None                          # outline_file's OUTLINE_MARKER
def outline_problem(root) -> str                            # the older-outline warning, naming the
                                                            #   file it read; "" when none
def old_layout(root) -> bool                                # the root index.html lacks VIEWS_MARKER
def page_urls(url) -> dict                                  # {name: address} of the two pages
def pages_line(url) -> str                                  # `site serve`'s line after the ready
                                                            #   line; never matches SERVE_READY_RE
def ensure_site_ignore(root, *, live=False) -> bool        # data/build.json in site/.gitignore,
                                                            #   created with it alone when absent;
                                                            #   with live, assets/live/ too
def prose_pool(docs, assets=()) -> Pool                      # what prose numbers are matched to
def baked_pool(root, map_doc, page_doc) -> Pool              # the same, over a built site on disk
def build(root, ledger, registry, view_registry, *, model=None, projection=None, now="",
          resolution=None, params=None, graph=None, live=False) -> dict
def state(root, ledger, registry, *, now="", stale=None, resolution=None,
          params=None, judged=None) -> dict   # state.json: clock-free (now is ignored)
def resolution_digest(payload) -> str                       # sha256 of what a state document judged
def resolution_moved(shown, now) -> list[str]               # "C1 pass -> fail": what a rebuild would change
def ages(resolution, now) -> dict                           # {gate: {when, age_s, aged}}
def write_build_json(root, *, now, id, ok=True, error="", live=False, verdicts=None,
                     mixed_age=None, tracing=False,
                     live_figures=None) -> str              # site/data/build.json, ignored
def watch_snapshot(root) -> dict                            # {relpath: (size, mtime_ns)} over WATCH_DIRS
def clean_assets(root, keep) -> list                        # delete unreferenced site/assets/ files
def vendor_urls() -> dict                                   # {site-relative path: URL} for `site vendor`;
                                                            #   three's meshopt decoder too (SPEC 40),
                                                            #   and vega/ (issue #15)
VEGA_VERSIONS: dict                                         # vega, vega-lite, vega-embed, pinned
```
**Two pages** (SPEC 40). `build` writes an absent `engineering/index.html`,
`marketing/index.html` or `technical.html` (making its directory), and never the root
`index.html`: an old project's root page is its own front page, so the build writes the
two pages beside it and warns while it lacks `VIEWS_MARKER` (`old_layout`; `site status` and
`doctor`'s `site` row say it too) with `OLD_LAYOUT_WARNING`, naming the steps: move it
aside, then `site init`. The warning says what `build` does ("writes engineering/ and
marketing/ beside it, when absent"), never what it did: `site status` and `doctor` print
it before any build (review of SPEC 40). A shell `build` writes is listed under the
summary's `pages` ("wrote the missing pages"), never under `refreshed`, which is the
renderer's. *Rejected:* `--force` in that advice (it replaces all four shells);
moving the page itself (a file the person wrote, moved by a build). It warns, too, when
`site/content/story.md` and `intro.md` are both missing: the marketing page then has no
story; with `intro.md` alone the summary's `notices` (printed, never counted as a
warning) says the page shows it as the story (review of SPEC 40: the warning read on
every round of a project whose page had a story). `baked_pool`'s
bare page carries every `page.PROSE_SUBJECTS` subject as null, as `bake_page` does.

**`build` never runs gates**, and never writes the ledger back. It renders a
`verdicts.Resolution` — the caller's `resolution=`, or, when none is handed, one it
resolves for itself with the projection it already builds. *Rejected:* a library
default that lists the recorded entries with no stale gates — a caller that forgot
to pass a resolution would serve every cached PASS as current and skip admission
(invariant 7 by omission). `stale=True` stays an all-stale override. It stamps each
verdict with its own age — obs `when`, else the entry's commit time, else `null`; a
build that re-ran the cheap gates on the way past would publish ten-second-old tier-0
numbers beside week-old tier-2 numbers under one "built at" stamp. From architecture
phase 5 that age is in `data/build.json` (`ages`), ignored by git, and `state.json`
holds no clock, so a rebuild that judged nothing new changes no tracked byte; every
tracked file is written only when its bytes differ (`util.write_json_if_changed`,
`ViewContext.write_asset`, the evidence copies). `build` bakes `map.json` and `page.json`
(`page`), refuses — writing nothing under `site/data/` — when `page.status_problems`
finds a token the resolution does not say, copies the evidence `page.evidence_plan`
plans, fills the prose (`page.bake_prose`) against a pool of every baked number, seals
both documents and writes `build.json` last. `state` borrows every judgement —
statuses from `claims`, the readiness sentence and the PARTIAL logic from `report` —
because a second implementation would give the page and the readiness document two
opinions about one ledger (the site renders the ledger; it never computes truth).
From architecture phase 1 the document gains `requirements` — each row
`report.requirement_json` over `claims.requirement_statuses`, the one producer — and
each claim row a `mechanism` and its `requirements` (through `report.claim_json`);
`judgement_digest` covers them, so a moved requirement status marks the page behind.
The page draws no requirement until the map (phase 5).
`meta.records_digest` is `store.records_digest(root)`
at build time — which records the page was built from; the CLI's site staleness compares
it with the records now (see `cli.py`), never an mtime. `meta.resolution_digest` is
`resolution_digest` of the finished document, set last by `state` itself: a sha256 over
everything but the two digests, each row's `evidence_assets`, `views` and
`locator_problems` — a deny-list, so a key a later `state` adds is judged by default
(phase 3a's `judgement_digest`, renamed in phase 5: "judgement" names nothing on the
page, and the glossary's *judgment* is an expert's). It is the other half of what the page was built from: the resolver's judgement
of the verdict cache against the live model, which no record holds (review,
`repro_site`: a `check` that FAILed C1 moved no record, and the page's C1 PASS read
current). `resolution_moved(shown, now)` names what a rebuild would change — claims whose
status moved, else verdict rows whose outcome or measurement did, else parameters whose
value (`thickness 7 mm -> 8 mm`) or, the value standing, another field (`thickness
rationale`) did — for the reason line; it reads both documents and judges neither.
`params` is the caller's `modelio.param_view` (the CLI's `_shown_params`); with none,
`state` views the live model it loads for the resolution, and `build` the model it
holds. Each row is `modelio.ParamView`'s `to_dict` plus `changed_in` (`decisions.changed_in`),
`derived`, and `defended` — `modelio.undefended_params`'s answer, `null` where the
model holds no number (SITE_CONTRACT). Never `ledger.params` (review, checkpoint 1.3).

A locator is never dropped for being undrawable: it stays on its verdict, and
`locator_problems` publishes the problem beside it in `state.json`, because a gate
that believes it is drawing and is not looks exactly like a gate that found nothing. `clean_assets` deletes only what
it can prove is unreferenced, which is why every asset arrives through `write_asset`.
A viewgen that is unavailable or crashes keeps the row its id had in the last
`state.json`, with its `src` and its `data_url` payload, while every file the row names
is on disk; the build warns `view <id>: kept from the last build: <reason>`, and adds
nothing to the row, so a clone without the tool bakes the same `state.json` (issue #15:
such a viewgen returned no view, so the cleaner deleted the picture it drew last time).
The row is kept only at the bytes the last build wrote: its digest with its view file
(`site.view_digest`) must be the one the last `page.json` holds under its seal
(`meta.views`); otherwise the build warns `view <id>: ... is not what the last build
wrote, so it is not kept`, and `site status` reads the row as edited by hand (review of
P1: a number added by hand to `data/views/<id>.json` was baked into the next page and
vouched for prose). A view no viewgen declares, or one that has nothing to draw, is not
kept.

**Bound viewgens** (issue #15). A viewgen with `evidence_of` draws its gate's served
verdict entry. `build` resolves and plans the evidence first, then runs the unbound
viewgens, then the bound pass (`_bound_pass`), in registry order: no served entry draws
nothing; a valid sidecar under the served entry at the current recipe digest
(`_recipe_digest`: the module's bytes, each `recipe` file's, `FIGURE_RECIPE_SALT`; never
the tool's identity) is the memo, and the viewgen is not called; else, when it is
available, it is called with a `BoundContext` whose `entry.files` are only the evidence
files whose bytes the entry's manifest names (`verdicts.evidence_manifest`). What it
returns is refused, with a warning and nothing published, when (`_figure_refusal`): an
output is over 2 MiB; a still is no PNG (its signature and IHDR), a `.glb` no GLB, or a
`.gltf` no glTF 2.0 JSON whose every buffer and image is a `data:` URI (`_solid_problem`,
`SOLID_SUFFIXES`; the spine checks no accessor: the Khronos validator does, in the
tests); a
bound picture carries a payload, or its `src` names no file it wrote; a chart holds a
key of `CHART_FORBIDDEN_KEYS`, or a number under `CHART_NUMBER_KEYS` that no verified
JSON leaf or CSV cell of the entry holds; a bound input or a verified file moved during
the call; a colour range's end equals a limit of a claim the gate covers or the entry's
verdict limit (relative 1e-9), or a `scale.source` key names a limit; a caption holds a
status word. Else it is published (`_publish_figure`) under
`assets/figures/<gate>/<entry>/` as `<view>.<sha12>.<ext>` beside the tracked sidecar
`<view>.view.json` (`page.read_sidecar`), and a 3D file of an entry no export re-ran (or
written with `write_live`) under the ignored `assets/live/<gate>/<entry>/`, named only
in `build.json`'s `live_figures`; once an export re-ran the entry, the next build copies
it into the committed folder. The figure the page shows is then read from the disk
(`page.figure_for`): current or stale under the served entry, else the one older folder
of the gate (several: none), and its files, sidecar and the served entry's live file
are the only bound files the cleaner keeps; an older folder goes once the served one is
drawn. Its `state.json` row has `src` (the still, or the 3D file of a 3D view),
`data_url` (the chart's series file), `field_src` (a committed 3D file beside a still),
`binding`
(`{gate, entry, currency, currency_word}`) and `data` empty. A bound viewgen that is
unavailable, crashes, hangs past its own timeout, returns nothing or is refused moves
no status (no view reaches the resolution) and deletes no picture; a picture of the
served entry kept from an older recipe is said in the warnings. `_kept_json` leaves
`assets/figures/` and `assets/live/` out of the prose pool, and `prose` excludes a
row's `scale`, `marks` and `binding`. *Rejected:* rendering in `check` (a 2.7 GB
optional tool on the judging path); a render on every build (seconds per scene per
edit); `write_asset` into `site/` (published before checked).

### `page.py`  (deps: models, util, store, claims, report, graph, prose)
The two pages' baked data (architecture phase 5, SPEC 28-33, 40): `map.json` and
`page.json`. Not a spine module (an edit here keys no verdict); clock-free; it reads
`site/content/` and the presence and size of evidence files, and never imports `site`.
Every status, word, count, margin and *ready* it bakes is from its one producer:
`graph.tokens`, `claims.worst_of`, `claims.unresolved`, `claims.margin`.
```python
OUTLINE = 1; SCHEMA = 1
MAP_SCHEMA = 2                               # map.json's own (phase 3b)
LANES = ("requirements", "zones", "stations", "boards")
MAP_EDGE_TYPES: tuple[str, ...]              # graph.EDGE_TYPES less requires, decides,
                                             #   derives, after (the feeds carry those)
SOURCE_OF_KIND: Mapping[str, str]            # an input's kind -> datasheet | estimate
SOURCES = ("chosen", "datasheet", "estimate", "assumption")
OTHER = "other"                              # the generated board and zone
IMPLICIT_STATIONS_WARN = 8                   # generated stations past this warn
EVIDENCE_MAX_BYTES = 2 * 1024 * 1024         # the largest evidence file copied
EVIDENCE_DIR = "assets/evidence"; PROSE_SUBJECTS = ("intro", "next", "story")
PROSE_FIGURE_DIR = "content/img"; ASSETS_PREFIX = "assets/"   # where prose figures go,
                                             #   and the folder the build cleans
DECISIONS_SHOWN = 5                          # the marketing page's featured decisions on show
VIEWER_KINDS = ("model3d", "field")          # the view kinds an alternate may be
WORD_KEYS: frozenset; RECORD_KEYS: frozenset # every string key of page.json and map.json:
                                             #   scanned for the register, or exempt
class Board(NamedTuple): id; title; line; generated; members
class Zone(NamedTuple): id; title; line; generated; members
class Station(NamedTuple): id; title; line; generated; members   # id "@<name>" when generated
class Layout(NamedTuple): boards; zones; stations; board_of; zone_of; station_of; lines;
                          unread; warnings; feeds; layers; station_layer; station_column;
                          station_depth; traced; ties; zone_rank; station_rank;
                          requirement_rank; idle; crossings; trace_key
class Evidence(NamedTuple): gate; entry; path; name; source; dest; href; missing; action;
                            reason;  # why it is not on the page: the build's warning, never baked
                            sha256   # a `copy`'s verified digest; the build checks it again
def token_of(composed) -> str                # STATUS_KEY, or errored
def boards_of(view) -> tuple                 # board records, then the generated `other`
def layout(view, built, *, projection, params=(), registry=None, resolution=None) -> Layout
def value_text(measured, units, limit_text, margin) -> str   # "0.6997 mm against a limit of
                                             #   0.5 mm, 40% past the limit"
def words_block() -> dict                    # page.json's words
def evidence_plan(root, view, resolution, registry=None, *, ignored=None,
                  manifest=None) -> list     # [Evidence]; plans only; `ignored(relpath)`
                                             #   is git's ignore rule (site build's);
                                             #   `manifest(gate, entry)` the bytes the
                                             #   entry's run cited (issue #15): `copy` only
                                             #   at those bytes, else `keep` an earlier
                                             #   copy or `none`; no reader copies nothing
def evidence_assets(plan) -> dict            # {gate: [href or "", ...]} for state.json
def evidence_source(root, text, *, ignored=None) -> tuple   # (source, outside, reason):
                                             #   one evidence path, as evidence_plan resolves it
FIGURES_DIR = "assets/figures"; LIVE_DIR = "assets/live"     # a bound figure's folders
SIDECAR_SUFFIX = ".view.json"; SIDECAR_SCHEMA = 2; SIDECAR_KEYS: tuple
SIDECAR_KEYS_BY_SCHEMA: dict                 # {1: the keys, 2: + scales, light} (design P2):
                                             #   both read, each with its exact key set
SCALE_ON = ("surface", "lines"); LIGHTS = ("scene", "none")
def sidecar_schema(meta) -> int              # 2 when meta states scales or light, else 1:
                                             #   a one-scale figure's sidecar keeps its bytes
def scales_problem(scale, scales, light) -> str   # why the colour keys are not of their
                                             #   shape, or "": the build's refusal and the
                                             #   reader's, one rule
FIGURE_ROLES = ("image", "field", "chart")
FIGURE_CURRENCIES = ("current", "stale", "unseen", "older", "none")
class Figure(NamedTuple): view; gate; served; entry; currency; sidecar; problems
def figure_folder(gate, entry) -> str        # assets/figures/<gate>/<entry>
def live_folder(gate, entry) -> str          # assets/live/<gate>/<entry>
def figure_currency_word(currency) -> str    # HUMAN["site"]["figure"][currency]
def read_sidecar(root, gate, entry, view) -> tuple   # (sidecar | None, why): its folder's,
                                             #   an entry the cache holds, each file's bytes
def figure_for(root, gate, view, served, fresh, unseen=False) -> Figure
                                             # current | stale | unseen | older | none
CAPTION_STATUS_PHRASES: tuple; FIGURE_CONTROLS = ("spin",)
def caption_status(caption) -> str           # the status word a caption says, or ""
class BoundFigure(NamedTuple): view; kind; gate; title; provenance; figure
def entry_verdict(root, gate, entry) -> dict | None   # a cache entry's verdict
def entry_outcome(verdict) -> str            # error | pass | fail | unrun
def drawn_text(verdict) -> str               # an older figure's line about its result
def binding_text(figures) -> str             # which result each gate's figures are of
def margin_text(margin) -> str               # the margin's words alone
def chart_block(claim, row, payload) -> tuple   # ({limit, current, spec, table}, warning): the floor's
def chart_spec(payload, limit, current, *, locators=()) -> dict   # P1.5: a chart's whole Vega-Lite spec
def chart_table(payload) -> dict | None      # review of P2: {head, rows}, the chart's numbers as text
def view_chart(row, payload, anchored=()) -> dict   # P2.5: {limit, current, spec, table} of a chart view on the record
CHART_CURRENT_KEYS = ("current", "marker", "now", "markers")   # an unbound view's point, first present
CHART_PAD = 14                               # px a continuous axis keeps inside each end
CHART_TABLE_ROWS = 8                         # rows of one series in a chart's table
VEGA_FORBIDDEN_KEYS: frozenset               # keys a baked spec never holds (Vega computes)
def vega_forbidden(spec) -> list             # the forbidden keys a spec holds, sorted
CHART_HEIGHT = 260                           # the chart's CSS height; its width is the card's
def figure_chips(cids, toks, marks) -> list  # the chips beside a figure
def figure_row(root, bound, *, claim, floor_row, chips, outcome, warnings=None) -> dict
def gate_cover(cover) -> dict                # {gate: [claim ids]}: effective_gates inverted
class FigureFacts(NamedTuple): root; served; outcomes; floor; cover; claims; shown; kept
def figure_facts(root, resolution, payload, view, composed, registry, *, kept=None,
                 shown=None) -> FigureFacts  # what _figure_problems holds a figure against
def settle(claim, composed, *, milestone=None, cover=(), tiers=None) -> dict  # {text, commands}
def subject_tokens(subject, boards, toks, named=()) -> dict   # what a piece of prose is
                                             #   stamped against: a board's claims, and
                                             #   "@<id>" for each named claim off the board
def named_in(blocks) -> list                 # the claim ids a piece of prose names
def prose_named(body, claim_ids) -> list     # named_in over a file's body
def prose_files(root) -> list                # [(subject, path)] under site/content/
def subjects(boards) -> list                 # board ids, then PROSE_SUBJECTS
def stamp_state(as_of, current) -> str       # current | stale | unstamped
def stamp_states(root, view, toks) -> list   # [{file, subject, stamp, as_of, current_as_of}]
def content_digest(root) -> str              # a seal over site/content/
def seal(doc) -> str                         # map.json's and page.json's `self`
def representation(views, root="", figures=()) -> dict   # the first model3d, else a
                                             #   figure with a committed 3D file, else a
                                             #   bound still, else image or diagram
def alternates(views, root="", first=None) -> list   # [{view, title, bytes}]: model3d and
                                             #   field views, first one first, one per asset
def requirement_nodes(view, lay, toks, marks) -> list   # the map's requirement nodes: the one
                                             #   producer of a requirement row
def requirement_row(node) -> dict            # page.json's row: the node renamed, never recomputed
def decision_rows(view, composed, claims_out, params) -> tuple   # (decisions, decisions_more,
                                             #   losers): newest first, rejections from
                                             #   claims.rejection_rows in the site's words
def source_of(built, name, kinds) -> str     # a value's source category (SOURCES)
def trace_block(lay, built, amber, trace) -> dict   # map.json's `trace`
def amber_word(state) -> str                 # amber's site word
def live_feeds(lay, built, toks) -> list     # the feeds as bake_map bakes them
def bake_map(view, lay, built, toks, composed, *, projection, params=(), registry=None,
             payload=None, name="", records_digest="", resolution_digest="", amber=None,
             trace=None) -> dict
def bake_page(root, view, lay, built, toks, composed, *, registry=None, payload=None,
              plan=(), records_digest="", resolution_digest="", amber=None, figures=(),
              warnings=None) -> dict
def bake_prose(root, page_doc, lay, toks, pool) -> list   # fills the prose objects
def figure_misplaced(path) -> str            # why a prose figure's path is no place for
                                             #   one ("" if it is): under assets/, it reads
                                             #   missing, by its path alone
def status_problems(map_doc, page_doc, view, composed, toks, *, prose_as_of=None,
                    pool=None, amber=None, feeds=None,
                    figures=None) -> list[str]   # invariant 14: tokens,
                                             #   words, marks, ready, amber, feeds,
                                             #   a compared value's words, prose stamps;
                                             #   with prose_as_of and pool, each as_of
                                             #   and each number's match; the two pages'
                                             #   rows (requirements, decision chips and
                                             #   rejection words, the headline, required,
                                             #   the tally, the limits line, and amber on
                                             #   requirement, section, chip and step rows)
                                             #   and, with figures (FigureFacts), G1-G8
```
**Order is baked, geometry is rendered**: nodes carry a lane, an `order`, a `row` and a
`column` (greedy interval packing over the rows their lines span), never a position, and
a status flip moves none of them. `status_problems` is invariant 14's one checker: the
build refuses a page it names, and `cli._site_state` reads an on-disk page with it — a
token the resolution does not say, while both documents' `resolution_digest` equal
now's, reads *edited by hand* (a resealed forgery). A line's values are its settling
evaluators' named reads (`graph.served_reads`), direct. **Map schema 2** (phase 3b):
requirements and zones are no longer stops; every zone record is drawn with its count,
its sources and its unconfirmed assumptions (`estimate`); a feed joins a zone to a
station when one of its choices moves one of the station's members directly
(`graph.direct_derives`); a line stops at the stations holding what it reads (`read`)
and those the trace puts before them (`upstream`), in layer, column and station order;
a station's depth is the upper median of the `graph.depths` of the members its lines
read (of every moved member when none is read), its layer the dense rank, a station
nothing moved last; zones, requirements and the stations of a layer carry a `rank`, one
barycentre pass against the claims' rows. Untraced, the layer is phase 5's column. A
claim whose amber flips carries the `amber` mark beside its token.

**The two pages** (SPEC 40) read one `page.json`, so every key the marketing page needs
is baked here, inside `bake_page`, where the seal, the number pool, `status_problems`
and the style scans already look: `story` (a prose subject stamped against intro's
tokens), `verdict.headline` (`report.site_headline`, the readiness sentence's own
first sentence), `representation.alternates`, `requirements` (`requirement_row` over
the very nodes `bake_map` draws, so the page and the map cannot disagree about a
requirement), and `decisions`, `decisions_more` and `losers` (`decision_rows`: a
rejection's words are `report.rejection_words` with the site register, matched to
`claims.rejection_rows` on decision and index). `status_problems` recomputes the
rejection rows itself, so both callers (`site.build` and `cli._site_state`) hold a
forged `type_word` or `returns` token. *What slipped through the plan:* the rows were
first built in `site.build` after `bake_page`, where the checker had already run; and a
rejection's reason sat under `why`, a word key, so the scan read record text (the
bracket's PLA reason has an em-dash). It is `reason` now, a record key.

**Figures on the page** (issue #15, P1.4): `site.build` hands `bake_page` the bound
figures its bound pass resolved (`BoundFigure`, one per bound viewgen, `none` included),
and each claim bakes `figures` (`figure_row`, one per figure whose gate covers it) and
`binding_text`. An older figure's outcome and `drawn_text` are its own cache entry's
(`entry_verdict`); a chart's limit and point are the floor row's (`chart_block`); the
chips are `graph.tokens`' with `_marks`. `status_problems` with `figures` (built by
`figure_facts`) holds each figure (`_figure_problems`, G1-G8): the build passes its keep
set and the figures it resolved; `cli._site_state` passes neither, so the checker reads
the disk (`figure_for`, `read_sidecar`) and a swapped file, a forged currency or a
planted chip reads *edited by hand*. The build's caption refusal and G7 read one rule,
`caption_status` (moved here from `site`). *What slipped through before it:* a picture
beside a status said nothing of the result it showed (F10: and a field was never the
hero).

**A chart is a baked spec** (issue #15, P1.5; rule 0.1 of its design): `chart_block`
also returns `spec`, `chart_spec`'s whole Vega-Lite spec over the chart file's series
and the floor's limit and point, which `lib/figures.js` hands to `vegaEmbed` unchanged.
It holds none of `VEGA_FORBIDDEN_KEYS`, so Vega computes nothing but the axes; G6
compares the whole block, spec included, and names a forbidden key a hand edit
planted. The page's theme gives each mark `style` its look; the spec says where a
label sits (Vega-Lite writes a text mark's alignment into the mark, over a theme's).
*Rejected:* growing `lib/chart.js` (log y, bands, rules, estimates, hollow points),
which the author's rule to build as little as possible and draw with a trusted
library replaces. From P2.5 every chart view on the technical record is a baked spec
too: `view_chart` reads an unbound view's own limit (y, or x as a vertical line) and
points in each spelling the hand-drawn chart read, a bound view's chart file's series
alone, and the verdicts' anchors (a located series drawn heavier, a located x a rule
in its severity's look), and `chart_spec` takes lists of limits and points and the
anchors as `locators`. `site.build` puts it in each chart row as `chart`, split into
`data/views/<id>~chart.json` (`site.CHART_FILE_SUFFIX`, `chart_url`) over
`VIEW_INLINE_MAX` and sealed with the row (`view_digest`). A layer that places only
constants draws from one empty row of its own. *What slipped through:* such a layer
inherited the chart's rows and was drawn once per row, so at 42 rows the shaded side
of a limit read solid and each label was 42 copies. `lib/chart.js` is deleted.
In the review of P2: an `older` figure's chart gets no floor row (`figure_row`, and
G6 the same), so it draws its own run's series alone; the judged point is a ring,
found at a series point or a band's end (`_series_x`), drawn over the band, whose two
ends are short bars; a payload's `"legend": false` drops the legend and `"judged":
false` the limit, the point and the units warning; an estimate that names its
`series` takes that series' colour; a log y writes one notation (`~e`); and every
chart carries `table` (`chart_table`), which the card shows where Vega does not
load.
*Rejected:* keeping it for the record's unbound charts and their locators, as P1.5
did (the anchors are a spine fact the bake can carry, and the hand-drawn text was
about 5 px high on a phone).

### `prose.py`  (deps: none)
The agent's text on the site and the scans that hold it (SPEC 30, 34, 35): pure
functions over strings and JSON values, importable by `site stamp` and the style tests
without the page machinery. No clock, no file reads.
```python
SENTENCE_MAX_WORDS = 25                      # ASD-STE100 rule 4.1
UNIT_WORDS: frozenset; BANNED_WORDS = ("delve", "robust", "seamless", "crucial", "honest")
IDENTIFIER_SUFFIXES: frozenset               # 3D, 1st: letters that make a number an id
POOL_EXCLUDED_KEYS: frozenset                # numbers that are structure, never a result
POOL_COUNT_KEYS: frozenset; POOL_FRACTION_KEYS: frozenset   # counts and fractions
COUNT = "#count"; FRACTION = "#fraction"; UNSTATED = ""     # the pool's tags beside units
class FrontMatter(NamedTuple): as_of; keys; body; problems
class Pool(NamedTuple): values; tagged       # every baked number's absolute value, sorted,
                                             #   and by tag (a unit, COUNT, FRACTION, UNSTATED)
    def near(self, x, tolerance, tag=None) -> bool
def parse_front_matter(text) -> FrontMatter
def stamp_text(text, as_of) -> str           # writes only the as_of line
def stamp_digest(subject, tokens) -> str     # 16 hex: sha256 over nopekit-stamp-v1
def parse_markdown(body, *, exists=..., claims=(), misplaced=...) -> tuple[list[dict], list[str]]
                                             # misplaced(path): a reason makes the figure
                                             #   missing, the reason its problem
def number_pool(docs) -> Pool
def scan_numbers(blocks, pool) -> tuple[list[dict], list[str]]   # number runs, unmatched
def matches(text, decimals, percent, pool, unit="") -> bool   # a unit must agree
def unit_key(unit) -> str                    # a unit as the pool compares it
def all_runs(blocks) -> list[dict]; def number_runs(blocks) -> list[dict]
def run_matches(run, pool) -> bool           # a baked number run, by the scan's rule
def runs_text(runs, *, code=False) -> str; def blocks_text(blocks) -> list[str]
def register_problems(text) -> list[str]     # em-dash, not X but Y, question, banned word,
                                             #   contraction
def long_sentences(text, limit=SENTENCE_MAX_WORDS) -> list[str]
```
The markdown subset is parsed here into typed blocks and runs; the browser receives no
HTML from prose. A number matches a pooled value within half a unit of its last written
decimal, sign-insensitive; a unit must agree with the value's (or the value states
none), a count matches only a bare number, and `%` a fraction times 100; letters
joined to a number are its unit unless they are an `IDENTIFIER_SUFFIXES` suffix. A
stamp guards age, never correctness.

### `milestones.py`  (deps: models, util, store, claims, report, verdicts)
The boundary that spends (P2.5b): what `export <milestone>` judges, and the package
it builds. Pure functions the CLI drives; the sweep, the lock, the prompt and the
record's write are `cli.cmd_export`'s. Not in `verdicts.SPINE_MODULES`: an edit here
keys no verdict. Standard library only, like every module under `src/nopekit/`.
```python
@dataclass(frozen=True)
class Refusal: kind; subject; reason          # kind: unresolved | missing | requires-nothing |
    def to_dict(self) -> dict                 #   disagrees | generator | package | precondition |
                                              #   model (the model does not load: nothing re-ran)
COVERED = frozenset({"unresolved"})           # the only kind a person's --proceed covers
class Judgment(NamedTuple): ready; refusals; writes; found   # found: claims.Unresolved
class Disagreement(NamedTuple): gate; kind; line   # kind: outcome | records | qualification
def closure(view, registry, milestone) -> list[str]   # the evaluators its required claims rest on
def disagreements(before, result, gates=()) -> list[Disagreement]   # the re-run vs the records (D7)
def refusals(view, composed, milestone, *, disagreements=(), extra=()) -> list[Refusal]
def judge(view, composed, milestone, *, disagreements=(), extra=(), decided=False) -> Judgment
def counted_on(view, composed, milestone, registry, resolution) -> dict[str, list]
                                              # per required claim, its counting covering rows
def sealed_contradictions(entry, claim_id) -> list   # a later fail's contradicts: the rows sealed
                                              #   at export, never the evaluator's verdict now
def sealed_on(entries, claim_id) -> list      # every record of one article: their rows together
def bound_export(entries, claim_id) -> ExportRecord  # the newest that re-ran the claim, else newest
def test_card(view, composed, milestone, article, exports) -> list[str]   # what to measure (§4.6)
MANIFEST = "MANIFEST.json"; SPINE_FILES = verdicts._PACKAGE_SPINE_FILES  # REPORT.md, model.json, MANIFEST
class Package(NamedTuple): hash; files; manifest
def scratch_dir(root, name, dry_run) -> str   # .nopekit/out/export-<m>, both modes, under the lock
def build_package(scratch, *, report_md, carried, manifest) -> Package
def package_problems(root, name, exports) -> list[tuple[str, str]]   # (foreign | edited, rel)
def swap_package(root, name, scratch, record=None) -> str  # out/<m>/ replaced whole, then
                                              #   record() (the export record's append); undone
                                              #   when it raises, the older package put back
```
**One path, two modes.** `--dry-run` runs everything `export` runs — the forced re-run
at tier 3 with controls and prerequisites, the judgment on the re-executed view, the
generator, the package built aside — and stops before the decision, the swap and the
record (P2.5b-D6, PLAN D-15: it is `/ready`). The text of the two differs in the outcome
line alone; `tests/test_export.py` (`DryRunIsTheSamePath`) holds that. A refusal under
`--dry-run` reads `would refuse`, and the preconditions a written export refuses on (no
git identity, a legacy project) are refusals it says it would make.

**What a go-ahead covers.** `judge` writes when there is no refusal, or only
`unresolved` ones and the person decided (`--proceed --why`, typed in their own shell:
the milestone's name, as `claim physical` takes the id). Never a disagreement, a missing
id, an empty milestone, a broken generator or a package that is not what was recorded.
The decision is sealed into the export record with each claim's status and cause.

**The package** is `out/<m>/`: `REPORT.md` (the milestone's report as `report
--milestone` renders it), `model.json` (exactly the article's param rows: what was
built, not the project), `MANIFEST.json` (the article, the records digest without
`exports/`, each file's sha256) and whatever the generator wrote. Built in a scratch
directory and swapped in whole; a file in `out/<m>/` that no export recorded is a
person's (`foreign`), and one whose bytes moved since its export is `edited` — either
refuses, named. Two exports of one state write byte-identical packages.

### `derivation.py`  (deps: models, util, store, modelio, verdicts, gates, claims, graph; cli only inside the worker; report only for words)
The trace (SPEC 39, architecture phase 3b): each numeric `Config` field moved -10% and
+10% in a fresh copy of the project, each build in a subprocess
(`python -m nopekit.derivation <job>`) that imports the model from its copy, under an
audit hook that names a write into the project. Not a spine module (it keys no
verdict); clock-free (`time.perf_counter` for durations only, never in a file).
```python
SCHEMA = 1; HARNESS = 2; STEP = 0.1; MOVE_FLOOR = 1e-9; SIGNIFICANT = 4; AFTER_FLOOR = 2
FAINT = 1e-3                                  # a smaller move does not count toward the order
BUILD_TIMEOUT_FLOOR = 900.0; TIMEOUT_FACTOR = 10.0; SNAPSHOT_MAX_BYTES = 1 << 30
THREAD_ENV: Mapping[str, str]                 # one BLAS/OpenMP thread per build
BACKGROUND_ENV = "NOPEKIT_TRACE_BACKGROUND"   # "0": round starts no trace
TRACE_DIR = "trace"; SCRATCH_REL; LOCK_REL; LOG_REL
SKIP = ("yes-no", "word", "none", "structure", "zero", "not-finite", "fixed")
FLIP_TOKENS = ("failing", "gap"); AMBER_STATES = ("untraced", "not-current", "flips",
                                                  "unclear", "holds")
HEADER; AMBER_HEADER; TRACE_KEYS; AMBER_KEYS
class Nudge(NamedTuple): name; value; steps; skipped    # .change: each step's factor less one
class Order(NamedTuple): after; ties; singles
class TraceFile: key; round; entry; path; keyed_on; skipped; observed; unmoved; unsteady;
                 derives; after; ties; singles; raised; moved   # implements graph.Trace
    def movers(self, name) -> frozenset
    def moves(self, choice) -> dict
class AmberFile(NamedTuple): path; trace; round; choices; claims
class TraceRead(NamedTuple): state; key; trace; problems; ambers   # current | none |
                                              #   unrecorded | no-model
class Launched(NamedTuple): code; timed_out; tail
class Ran(NamedTuple): state; key; round; written; observed; total; skipped; unmoved;
                       unsteady; raised; incomplete; amber; builds; problems; order; timeout
class Amber(NamedTuple): state; flips; unclear; via; why; rerun; changes
    def step(self, choice, direction, *, minus="-") -> str   # "-10%", or an integer's size
def default_workers() -> int                  # min(8, cpus - 1)
def nudges(name, value, *, settable=True, real=False) -> Nudge   # real: a float field
def plan_of(config) -> list[Nudge]            # real from each field's annotation
def step_word(direction, change=None, *, minus="-") -> str
def apply(model, name, step) -> LoadedModel   # dataclasses.replace; CONFIG rebound
def instructions_of(root, model, anchors) -> dict | None   # _model_code + *.py under model/
                                              #   that a snapshot copies
def choice_digests(config, anchors) -> dict   # every field, skipped ones too
def keyed_on(instructions, choices, data=None) -> dict
def key_of(keyed) -> str                      # seal({harness, step, instructions, choices, data})
def current_keyed(root, model, anchors) -> dict | None   # data {}: a reader cannot know it
def current_key(root, model, projection, anchors) -> str | None   # where the scratch lives
def data_current(root, data) -> bool          # a trace's data files hold the same bytes
def anchors_for(root, resolution=None) -> Anchors
def observe_values(projection, anchors) -> dict   # {derived: [digest, scalar]}; files,
                                              #   meshes and arrays by their bytes
def move_of(base, new); def moves_between(base, new) -> dict; def magnitude(move) -> float
def render(doc) -> bytes                      # indent 1, scalar lists on one line
def self_digest(doc) -> str
def after_order(movers, strong=None) -> Order # strict subset of strong movers, two-mover
                                              #   floor, Hasse
def trace_dir(root); def trace_path(root, key); def amber_paths(root, key)
def read_trace(path) -> (state, TraceFile | None, line)   # absent | unreadable | edited |
                                              #   newer | mismatch | ok
def read_amber(path) -> (state, AmberFile | None, line)
def trace_for(root, model, projection, anchors) -> TraceRead   # only the current design's:
                                              #   its inputs, and its data on disk
def trace_digest(root, key) -> str            # the round record's computed_from.trace
def other_designs(root, key) -> (files, bytes)
def worker_main(argv) -> int; def worker_env(environ=None) -> dict
def subprocess_launch(job, job_path, timeout) -> Launched; def in_process(job, job_path, timeout)
def lock_path(root); def live_holder(root) -> int | None
def scratch_dir(root, key); def scratch_state(root, key) -> dict
def snapshot_size(root) -> (bytes, [(bytes, dir)])
def amber_current(ambers, subjects, amber_choices) -> bool
def amber_subjects(root, ledger, registry) -> dict   # {cid: {acceptance, evaluators}}
def run(root, *, plan, entry, amber_choices={}, choices=None, workers=None, timeout=None,
        round_=None, now="", quiet_if_running=False, background=False, on_line=None,
        launch=None, project_lock=None, pack_dirs=()) -> Ran
def unconfirmed(built, tokens, aid) -> bool
def amber_states(read, *, built, tokens, subjects) -> dict[str, Amber]
def amber_now(root, view, registry, built, tokens, read) -> dict[str, Amber]
def state_payload(read, built, amber) -> dict # state.json's trace and amber
def needed(read, states) -> str               # "trace" | "amber" | ""
def amber_words(amber) -> str
def start_background(root, *, round_, key, popen=None) -> (state, detail)
```
**The key** is computed after a build, wherever it is computed, and covers every
`Config` field's value and the data files the base build read; a trace whose key is
not the current design's is never served (`trace_for`). **A trace file is written only whole** — every numeric choice moved both
ways — under the project lock and only when its path is absent; a timeout, a dead
worker, a raise that differs when run alone or a write into the project leaves none,
and the scratch keeps what finished. **Amber** runs `verdicts.sweep(record=False)` and
`claims.compositions` in the copy — the check run's rules, never a second composition —
and is read back per claim against the current acceptance and evaluator digests; it
moves no status (`claims.py` never imports this module).

### `receive.py`  (deps: vcs, store, rounds, verdicts, derivation, packs, modelio, site, util)
What Eddy's pre-receive hook says of a push (Eddy milestone 0; `docs/EDDY_CONTRACT.md`
is the contract, with the rule table this module's `RULES` and `NOTES` are the one
source of): the validator behind `nopekit validate` and `server/pre-receive`. It judges
a push by what git holds — refs, commits, trailers, trees, blobs, the objects new to
the push — through `vcs`'s push reads alone, and never says *checked*: a verdict
entry is loaded for its shape, its digest and its rho only (invariant 12). It executes
nothing from the tree, starts no git process itself (`test_meta.NoGitOutsideVcs`),
reads no clock (`test_meta.CLOCKLESS_MODULES`) and no environment variable (the CLI
edge and the hook script hand the quarantine in as a `vcs.ObjectDirs`), writes only
under a `tempfile.mkdtemp` directory prefixed `nopekit-validate-` outside the
repository, removed in a `finally`, and fails closed: any `None` from `vcs` is E07 and
exit 2, and the hook declines on any non-zero exit, its own crash included. It imports
`rounds` to hold the state file a pushed tree carries — `rounds.read` on a materialised
copy of that tree, from `state_file_problems` alone, never for a status
(`test_round.NothingReadsTheRoundRecordForTruth`). Not a `SPINE_MODULES` member: no
verdict keys on it. It reaches three private `vcs` names — `_ask` (E07's diagnostic
`rev-parse`), `_HEX_OID` and `_ref_arg` (the argument checks) — beside four private
`verdicts` names (`_load_entry`, `_load_control`, `_digest_of`, `_ledger_now`) and
`store`'s two ignore blocks; milestone 1 promotes them together, with the cache
regeneration CLAUDE.md names, so a rename inside an edge cannot break the validator
with no contract line to catch it.
```python
SCHEMA = 1; KINDS = ("candidate", "evidence", "act"); AGENTS = ("designer", "reviewer")
ACTS = ("accept-gates", "approve-print", "measure", "prefer", "publish")
TRAILER_KEYS: tuple[str, ...]; ACT_ONLY_KEYS: tuple[str, ...]   # the closed Nope-* schema
REQUIRED_KEYS: dict[str, tuple[str, ...]]                  # per kind: candidate, evidence
MAX_VALUE_CHARS = 200; NOPE_LINE: re.Pattern              # the raw-message scan behind E16
CAND_PREFIX = "refs/heads/cand/"; DRAFT_PREFIX = "refs/heads/draft/"
MAIN = "refs/heads/main"; SPEC = "refs/heads/spec"; PUBLISHED = "refs/heads/published"
LINE_NAME: re.Pattern; TRIGGER: re.Pattern                # a line's name; <gate id>/<rho16>-<out8>
STATE_REL = rounds.ROUND_REL; PROJECT_REL = f"{store.NOPEKIT_DIR}/{store.PROJECT_NAME}"   # every .nopekit/ path from its home
VERDICTS_PREFIX = f"{store.NOPEKIT_DIR}/{store.VERDICTS_NAME}/"; TRACE_PREFIX = f"{store.NOPEKIT_DIR}/{derivation.TRACE_DIR}/"
PACKS_REL = "spec/packs.json"; SPEC_DIR = "spec/"
SPEC_ENTRIES = ("claims", "requirements", "milestones")   # + packs.json: the spec tree
NOPEKIT_KEPT: tuple[str, ...]                             # the closed list under .nopekit/
SEALED_DIRS = ("results",) + store.SEALED_KINDS           # results: sealed on write_record's own branch
TRACE_NAME: re.Pattern                                    # derivation's two file names, mirrored for E29
EVIDENCE_ADDS = rounds.ROUND_NAMED_BY_NOPEKIT             # == (VERDICTS_PREFIX, TRACE_PREFIX), a pure test holds it:
                                                          #   what an evidence commit may add, the scan's exempt subtrees
ATTRIBUTES_REFUSED = ("export-ignore", "export-subst", "ident", "eol=crlf")
OUTPUT_PATTERNS: tuple[str, ...]      # the three ignore blocks' lines, rooted (E28)
MAX_BLOB_BYTES = rounds.ROUND_MAX_BYTES; MAX_PUSH_BYTES = 512 * 1024 * 1024
MATERIALISE_MAX_FILES = 20_000; MATERIALISE_MAX_BYTES = rounds.ROUND_MAX_BYTES
WALK_LIMIT = 10_000; PREVIOUS_CLAIMING_LIMIT = 10_000; BATCH = 2_000
MAX_JSON_DEPTH = 32                   # a materialised JSON file nested deeper is refused before any reader opens it
EMPTY_TREE: dict[str, str]                                # {"sha1": ..., "sha256": ...}
class Rule(NamedTuple): id; applies; refuses; message
RULES: tuple[Rule, ...]; NOTES: tuple[Rule, ...]          # E01..E44, N01..N04: the one list
class Refusal(NamedTuple): rule; where; message
class Note(NamedTuple): rule; where; message
class Commit(NamedTuple): sha; kind; name; trailers; tree; parents
class RefReport(NamedTuple): ref; old; new; refusals; notes; commits; spec_tree; accepted_spec
class Report(NamedTuple): refs; unanswered; pusher       # .accepted: no E07, no refusal
def validate_push(repo, updates, *, objects=None, pusher="") -> Report
def classify_ref(ref) -> tuple[str, str]                 # (cand|draft|main|spec|published|other, name)
def is_zero(sha) -> bool
def message_problem(message) -> str | None               # E12's tail on the raw bytes: a control byte, or not UTF-8;
                                                         #   such a message is never handed to git's trailer reader
def trailer_problems(sha, message, parsed) -> tuple[str, dict[str, str], list[Refusal]]  # E12..E21
def tree_id(entries, algo) -> str                        # git's tree oid, pure (oracle: git mktree)
def spec_tree_id(repo, sha, *, objects=None) -> tuple[str | None, list[str]]
def packs_problems(raw) -> tuple[list[str], dict[str, dict[str, str]]]   # spec/packs.json, strictly
def nesting_problem(raw) -> str | None                   # "not strict JSON (nested deeper than N levels)", else None:
                                                         #   run on every materialised JSON file before its reader
def tree_problems(listing, top, attributes) -> list[str]  # E25..E29, "<id> <tail>" each
def diff_problems(kind, changes) -> list[str]             # E36, E37
def materialise(repo, listing, *, objects=None) -> tuple[str, list[str]]   # (tmpdir, E30)
def record_problems(tmp, *, listing=()) -> list[str]      # E32: store.load's words verbatim
def sealed_problems(kind, before, after) -> list[str]     # E33: the old chain a prefix of the new
def entry_problems(tmp, added, *, ledger=None, listing=(), hashes=None) -> tuple[list[str], list[str]]
def state_file_problems(tmp, previous_round, state_changed) -> list[str]   # E40..E44 (E41 too for a round below 1)
def secret_name(path, *, is_tree=False) -> str | None    # rounds.secret_name, its cache exemption narrowed to the
                                                         #   paths nopekit writes there (a gate's directory, an entry, a trace file)
def object_problems(repo, new, *, exclude, objects=None) -> list[Refusal]  # E08..E11
def format_line(item) -> str                             # "<id> <where>: <message>", printable
```
**The walk.** Each ref in git's order: the layout (E01–E05; a case twin among the
repository's lines or earlier in the same push), deletion (E03; a line of two zero
ids is the deletion of a ref that does not exist, accepted on `draft/*`), existence
(E24 on a line whose `new` is a tag or a tree), the object scan before any blob is
read (E08–E11: sizes by `batch-check`, names from every new tree's entries, the cache
subtrees exempt only where the path is one nopekit writes), then for `cand/*` the
first-parent chain (`old` on it or E04; at most `WALK_LIMIT` new commits or E06), the
new commits read once (E12–E21; a message E12 refuses on its raw bytes is read no
further and never handed to git's trailer reader), the tip (E24), the previous
claiming commit found below the push, and each new claiming commit oldest first: its
tree (E25–E29), the materialised copy bounded first (E30), its JSON files' nesting
(`MAX_JSON_DEPTH`, each too-deep file under its own rule, the copy read no further)
and read strictly (E31–E33), the spec (E34, E35, N01), the diff against the
previous claiming commit's tree — never its parent's (E36, E37) — every added entry's
own reader, rho and static (E38, E39, N02–N04), and the state file (E40–E44). Helpers
return `"<id> <tail>"` strings; the walk turns them into `Refusal`s in rule order.
**The half of the state file it holds**: present, `rounds.read` says `ok`, computed from
this tree's records and verdict cache (`store.records_digest`, `rounds.verdicts_digest`
over exactly the files they walk), the bytes `rounds.render` of `rounds.sealed` would
write, and a round above the previous claiming commit's when the blob moved.
`computed_from.design` and `.trace` need the model loaded and are the renderer's
(milestone 1), as is the body regenerated whole with `check --force` at tier 0. What
the hook cannot see is in the limits below.

### `cli.py`  (deps: everything)
`argparse`, subcommands, `main(argv=None) -> int`, and `build_parser()` — the whole
command surface as one `argparse.ArgumentParser`, so a test can check a command
printed in a document against the parser instead of against memory. `main` catches
`NopekitError`, prints `error: <msg>` to stderr, and returns 2.
```
nopekit init [--name] [--summary]        nopekit status
nopekit claim list|show|physical         nopekit gap [--propose]
nopekit ingest <path...> [--kind] [--desc]   nopekit inputs [--unextracted]
nopekit extract <artifact> --what ... --grounds ...
nopekit ask [--kind]                     # what evidence to request from the user
nopekit check [--tier N] [--only GATE] [--force] [--no-record]   nopekit gate list|selftest|show
nopekit report [--write] [--milestone M]   nopekit why <param-or-claim>
nopekit export [M [--dry-run] [--proceed --why TEXT]]   # P2.5b: the boundary that spends
nopekit decide --title ... --summary ... [--rejected V|W [--type T] [--by NAME]
               [--returns-if "C1 margin > 0.02" | --returns-text TEXT] [--input ID]]...
nopekit prefer <decision> [--value V]    # phase 2: the person's channel, never the agent's
nopekit round                            # phase 3a: tier-0 check, site, the round record; exits 0
nopekit propose [<proposer> [ARGS...]] [--kind] [--desc] [--timeout S]   # phase 2
nopekit ingest <path...> [--proposer ID] nopekit packs [list|show|validate|add]
nopekit model [--write]                  nopekit doctor
nopekit check [--junit [PATH]]
nopekit gate selftest [GATE ...] [--pack NAME|DIR] [--user-packs] [--allow-empty] [--junit [PATH]]
```
Output is terse and machine-parseable by default (one line per verdict);
`--json` on every read command.

**Requirements and mechanisms at the edge** (architecture phase 1). `gap` lists every
claim whose mechanism has yet to run (`claims.yet_to_run`: Gap, Pending build, Assumed),
resolved exactly as `status` resolves — the registry, the projection, the verdict cache
— each row with its mechanism line, a warning per evaluator its mechanism names that
does not settle it here, and the gap record that names it; `--propose` searches a gap
record with its quantity and the `how` of the claims it names (`claims.need_how`), and a
listed claim with its record's search or, with none, a transient Need (never written)
and its own `how` (`claims.proposal_query`, `packs.match(..., how=)`) — one need, one
pack list. For a physical, judged or assumed claim it says a pack's evaluator checks
only a modelled part, and with no pack that its mechanism settles it: the extension
protocol's trigger, and the footer's "propose a solver", are an automated claim's only. It stays a read: no
lock, no record (S-43). `--json` keeps `gaps`/`n_gaps` (the Need records) and adds
`yet_to_run`/`n_yet_to_run`. `check` prints the mechanism warnings on stdout after the
limit warnings (`_mechanism_lines`) and adds `mechanism_problems` to `--json`; the exit
code never moves. `status --json` and `report --json` add `requirements`
(`_requirement_rows`); `why` takes the view's compositions (`_why_text`); `doctor` has a
`requirements` and a `mechanisms` row (`_doctor_requirement_rows`).

**Decisions, preferences and proposers at the edge** (architecture phase 2). `decide`'s
part-flags (`_RejectedFlag`, `_RejectionPart`) fill one ordered list, so each `--type`,
`--by`, `--returns-if`, `--returns-text` and `--input` applies to the `--rejected`
before it (`_rejection_items`); a return condition on no claim or an input that is no
record is refused under the lock (`_refuse_dangling_rejections`), nothing written.
`prefer` (`cmd_prefer`) is `claim physical … assume`'s order: the refused flags before
the project is read, then under the lock the decision, a preferred_against in it,
git's identity, the channel — an agent marker or a pipe refused outright — the
rejections whose `by` is this identity, the prompt and the decision's id typed, and one
sealed entry (`store.append_sealed`); it reads the rejection back on `status`'s view.
`round` (`cmd_round`, architecture phase 3a, SPEC 18 and 24 without the hook and the
commit) takes the lock, migrates a legacy project as `check` does, loads the strict
registry (a pack that does not import: `round not run`, no record), runs `_check_run` at
tier 0 (`check`'s locked body, extracted: what `check` writes, and never the round
record; a model that does not load refuses the check, builds no site — `site build`
refuses that model too — and the record is still written), resolves once with no sweep
overlay — what `status` shows — builds the site from that resolution when `site/` exists
(`_site_build`, `site build`'s locked body), and writes
the round record (`_round_inputs`: `graph.build` over `verdicts.design_of`, and
`graph.tokens`; `rounds.write`). It prints one paragraph (`report.round_paragraph`) and
exits 0 whatever the project says; every `NopekitError` is said in one line, and only a
traceback exits 1 — a hook's exit 2 would block the turn. `status` prints a `round
record:` line after `last check run:` and `status --json` a `round` object (`_round_state`:
the record's state — its digests against the project's, and what it holds against the
body `round` would write now — never what it holds); `doctor` has a warn-only `round` row and a
`graph` row that FAILs on `store.pointer_problems` and on `PARAMS` grounding naming a
non-assumption claim.
```python
def _check_run(root, ledger, *, tier, only, force, record, now, on_row, quiet,
               registry=None) -> _Checked   # check's locked body after the migration
def _site_build(root, ledger, registry, model, projection, view, resolution, *,
                now) -> dict                # site build's locked work, over the caller's resolution
def _round_inputs(root, ledger, registry, model, projection, view, resolution, *,
                  model_error="", shown=None) -> tuple[dict, dict]   # (rounds.body, computed_from)
def _round_state(root, ledger, model, projection, resolution, registry, view, *,
                 model_error="", shown=None, traced=None) -> rounds.RoundState
def _trace_for(root, model, projection, resolution=None) -> derivation.TraceRead
                                            # phase 3b: the trace every graph.of/build takes
def _amber_for(root, view, registry, built, tokens_, found) -> dict   # derivation.amber_now
def _round_label(root) -> int | None        # the round record's number alone, for a trace
def cmd_model_trace(args) -> int            # `nopekit model trace`
# architecture phase 6: the round's commit and the Stop hook
ROUND_LOCK_WAIT_S = 60.0                    # --hook only: how long it waits for the build lock
class CommitOutcome(NamedTuple): made; oid; who; why; detail; staged; not_files; too_large;
                                 secret; earlier; subject
class RoundOutcome(NamedTuple): not_run; done; content; check; site; view; composed; prose;
                                commit; trace; summary; trace_state
def _round_run(root, *, commit, wait_s, now) -> RoundOutcome   # cmd_round's body; never raises
                                            #   a NopekitError (not_run instead)
def _round_commit(root, ledger, done, paragraph) -> CommitOutcome   # under the build lock
def _round_start_trace(root, done, *, traced, view, resolution, registry, built, composed,
                       model_error) -> dict   # the trace's seam, phase 3b's background start
                                            #   after the lock: {state, key, log, why}
def _round_hook(args) -> int                # Claude Code's Stop hook; always 0
```
**`round --commit`** (architecture phase 6, SPEC 24 with §0.3) runs the round, then,
still under the build lock, `_round_commit`: refusals first, each leaving the index as
it was — a round record a newer nopekit wrote, no repository, a merge, rebase,
cherry-pick, revert or bisect in progress, a detached HEAD, no git identity, no
`people`, an author `people` does not list (`rounds.listed`: name and email), a
committer it does not list (`vcs.committer_ident`, review of phase 6) — then
`vcs.changed_paths` (the project's subtree, ignored files left out) split by
`rounds.stage_plan` — every file it lists is staged but a directory (an untracked
nested repository), a file over 50 MiB and a name that looks like a secret, each named
instead (the acceptance run after phase 3b: the closed list before it left the agent's
own `bom/bom.json` out of every round) — `vcs.add` of what it stages, their
intersection with `vcs.staged_paths` (an mtime-only change drops out), and
`vcs.commit` with `report.round_commit_message`. Nothing changed makes no commit, and
nothing staged under a repository that ignores the project's file says so
(`_round_ignored`, why `ignored`) instead of "nothing changed"; a
commit git refuses leaves the files staged and says so; it never pushes. Its paragraph
appends `report.round_commit_sentence` and `report.round_prose_sentence`; `--json` adds
a `commit` object (`staged`, `not_files`, `too_large`, `secret`, `earlier`, beside
`made`, `oid`, `subject`, `who`, `why`, `detail`). A round whose check could not run,
or whose claims fail, still commits: the commit is history, not a quality gate.
**`round --hook`** reads the hook's JSON on stdin, finds the project from `-C`, its
`cwd`, Cursor's `workspace_roots`, `$CLAUDE_PROJECT_DIR`, `$CURSOR_PROJECT_DIR` or the process's cwd, runs `_round_run(commit=True, wait_s=ROUND_LOCK_WAIT_S)` with stdout (the
Python stream and fd 1) and stderr sent nowhere, rebuilds the index itself
(`args._index_done`, so `_after` writes nothing after it), and prints one JSON object:
`{"systemMessage": report.round_hook_message(...)}`, or `{}` outside a project or when
the record stood, nothing changed, no board's text is behind and the trace has no
sentence (`report.round_trace_sentence`: none started, running, stopped short or
refused). It
always exits 0 and never prints `"decision": "block"`; a crash is a one-line message.
The plugin runs it through `hooks/stop.py`, a script, never `python3 -m`: `-m` puts the
session's cwd first on `sys.path`, and a `difflib.py` there ran as code at every turn's
end and could exit 2 (review of phase 6); the launcher keeps every empty or relative
entry off the path, puts the plugin's `src` first, and turns a failed import into the
one-line message. **`init`** lists git's identity as the first person
(`rounds.person_of(vcs.ident)`) when `store.people_problem` accepts it (git takes an
email with a space; the strict reader does not, so listing it bricked the project),
and starts a repository after `store.init` only on `vcs.in_repository`'s positive "no
repository" (`vcs.init_repo`; `--no-git` starts none; git that does not answer —
"dubious ownership", a timeout — starts none and says why); inside a repository that
ignores the project it says so. Its next steps follow decision 21.
**`packs skills`** (`cmd_packs_skills`) prints each live, found pack's PACK.md, its
`lenses.md` and its `skills/<name>/SKILL.md` files (or `--pack` names'); a live pack not found is a FAIL
line and exit 1. **`doctor`** gains a warn-only `people` row (`_doctor_people_row`).
`model trace` (`cmd_model_trace`, architecture phase 3b) refuses a legacy project, no
model entry, a model that does not load, an unknown `--choices` name (with a
suggestion) and `--workers` below 1; plans from the model as loaded (never built in the
project); names the amber choices from `graph.value_sources`; and hands
`derivation.run` the project lock to snapshot and to write under. Exit 0 written,
current or partial; 1 incomplete; 2 refused. `--from-round N` (hidden) is the
background run `round` starts with `NOPEKIT_TRACE_BACKGROUND` unset or not `"0"`
(`derivation.start_background`, from phase 6's seam `_round_start_trace`, after the
round's lock and its commit, so a trace still writing lands in the next round's
commit): a live trace makes it exit 0 silently. It starts in nopekit's own directory
with the project named by `-C`, and each worker starts there too and moves into its copy
itself (`worker_main`): `-m` puts the starting directory first on `sys.path`, and
started in the project's root (or a copy of it) a `difflib.py` there was imported in
place of the library's (the follow-up after phase 3b closed this limit). `status` prints
a `trace:` line only when the trace is not the current design's, and an `amber:` line
only when a Checked claim's amber is not `holds`; `status --json`, `report --json` and
`state.json` gain `trace`/`amber`; `report` a `## Amber` section for flips and
not-current; `why` a choice's `moves:`, a derived value's `derived from:`, `after:` and
ties, and a claim's `amber:`; `doctor` a warn-only `trace` row; `round --json` a `trace`
object. JUnit, `check`, `export` and exit codes never read amber.
`propose` (`cmd_propose`) runs a proposer as a subprocess outside the lock, refuses a
non-zero exit, a timeout or any project file outside its directory that moved
(`_project_files`, a stat of the project but its ignored scratch, each path named),
then ingests each file under the lock (the script's arguments are the plain ones after
the id and everything after the first `--`, which `main` splits off), `proposer` set
on a new record only; `ingest --proposer` does the same for a file
the agent got by hand. `check` prints a *may return* line for each rejection whose
condition holds after its code is fixed and adds `may_return` to `--json`; `doctor`
gains warn-only `decisions` and `basis` rows (`_doctor_decision_rows`,
`_doctor_basis_rows`); `inputs` shows each input's proposer and looked-up basis
(`_input_bases`); `status`'s packs line names up to eight packs, then how many more
(`_names`).

**The detached live server** (`site serve --detach`, `--stop`; the acceptance run after
phase 3b). `--detach` (`_serve_detach`) starts
`python -m nopekit site serve --detached-server` as a process of its own —
`start_new_session` (no controlling terminal, its own process group), started in
nopekit's own directory, stdin `/dev/null`, stdout and stderr appended to
`SERVE_LOG_REL`, no other descriptor — waits until it records its address (`SERVE_DETACH_WAIT_S`), prints `site.SERVE_READY_LINE` and
exits 0; never a browser. What slipped through: the agent ran `site serve --watch` as a
background task, which there lives at most two hours, and under `claude -p` a running one
keeps the turn from ending. The server holds `SERVE_LOCK_REL` (`flock`, exclusive) for
its life, writes `SERVE_STATE_REL` once bound (so `-p 0` records the port it got), logs
only failed requests, stops on SIGTERM between requests (`server.shutdown` from a
thread: a first build in flight finishes), stops by itself within `SERVE_LEASE_S` once
its record no longer names it, its lock file is another's or the site is gone
(`_serve_lease`), and removes its record as it stops. A reader (`_serve_state`:
`site status`, `doctor`'s `site` row, a second `--detach`) probes the lock without
taking it for more than an instant and never creates the file: `running` needs the lock held AND
a live pid in the record that leads its own session (`_session_leader`), so a record
whose server died — its pid since given to another process — reads `stale` (whatever
machine it names: a container's host name changes at every start), and `--stop`
(`_serve_stop`: SIGTERM, then SIGKILL after `SERVE_STOP_WAIT_S`) never signals it. A
second `--detach` prints the running URL and starts nothing; off POSIX both flags refuse
with `SERVE_DETACH_UNSUPPORTED`. Here and not in `site.py`: fdm-print's and cad-solid's
gates import `nopekit.site`, so a code edit there re-keys their verdicts in every
project.
```python
SERVE_DETACH_WAIT_S = 30.0; SERVE_STOP_WAIT_S = 15.0; SERVE_LEASE_S = 2.0
SERVE_STATE_REL = ".nopekit/out/serve.json"; SERVE_LOCK_REL = ".nopekit/out/serve.lock"
SERVE_LOG_REL = ".nopekit/out/serve.log"; SERVE_LOCK_WAIT_S = 1.0
SERVE_DETACH_UNSUPPORTED: str                # the one sentence off POSIX
class Served(NamedTuple): state; pid; url; watch; started; node   # none | running | starting |
                                             #   stale | elsewhere | unsupported
def _serve_state(root) -> Served             # read-only
def _serve_lock_held(root) -> bool | None    # a shared, non-blocking flock probe
def _session_leader(pid) -> bool             # os.getsid(pid) == pid; True when not told
def _hold_serve_lock(root, *, wait_s=SERVE_LOCK_WAIT_S) -> int | None   # the server's own
def _write_serve_state(root, *, pid, host, port, url, watch, started) -> None
def _clear_serve_state(root, pid) -> bool    # only the record naming pid (None: any)
def _serve_lease(root, pid, lock_fd) -> bool
def _serve_detach(root, args) -> int
def _serve_stop(root, args) -> int
```

**Records at the edge** (checkpoint 1.3, spec §3.15). A record is a file, and a
command writes exactly the one record it was asked to write — never the whole
project. What slipped through before: every writing command loaded the whole ledger
and saved the whole ledger, so `decide` rewrote every claim to add one decision, and
`check`, a sweep, rewrote the records on every run (parameters re-synced from the
model, grounding back-references copied into parameters, coverage into
`claim.gates`): a claim edited by hand between two commands was put back by the
second, a claim's hand-set gate list was reverted by the next check (S-37), and a deleted
extraction's grounding lived on in the parameter it had been copied into (S-36).
`tests/test_shims.py` (`NoWholeLedgerWriterInCli`) walks `cli.py`'s AST and refuses
any reference to `store.save` — a call, a bare reference, `getattr`, an import under
another name.
```python
def _migrate(root, *, apply, now) -> Ledger
    # store.migrate_legacy(root, apply=apply, when=now,
    #                      model_prose=modelio.static_param_prose); its notice to stderr
def _load(root) -> Ledger
    # store.load(root, model_prose=modelio.static_param_prose): every command that only
    # reads, so a legacy project reads as the plan _migrate will write
def _touch_index(root, *, quiet=False) -> None
    # store.write_index on a MIGRATED project, best-effort; re-run while records_digest moves
```
- **The migration's triggers** are `check` and the shims (Q1.5): under the held lock
  and before anything reads the project, `_migrate` runs the legacy migration with
  `apply` — once, with ONE stderr notice ending `git rm --cached
  .nopekit/ledger.json` (the spine runs no git). `check --no-record` runs it in
  memory only and says the project "will migrate … on the next check". Every other
  command reads a legacy project through `_load` — `store.load` with `_migrate`'s own
  reader, so it refuses what the migration refuses and nothing else — in memory, and
  writes nothing of it.
- **The shims**, each under the lock with `now` stamped at the edge, each migrating
  first, each writing **exactly one record file** (plus the ignored index):
  `ingest` — the bytes into `inputs/<bucket>/` (the payload, not a record) and
  `inputs/<id>.json`, content-deduped, sha256 pinned (permanent: it moves bytes);
  `extract` — rewrites `inputs/<id>.json`, and nothing it grounds (grounding is
  derived, `artifacts.grounding`); `decide` — `decisions/<slug>.json`, `when` the
  edge's stamp (**no `--when`**: it backdated a decision, S-44), and no
  `docs/decisions.md` (an output of `report --write` whenever a decision exists);
  `packs add` — `.nopekit/project.json`'s `packs`, after loading the pack into a
  fresh `Registry` so a broken one fails before anything is written; `claim physical`
  — APPENDS one `PhysicalResult` to `results/<claim-id>.json` (append-only, D-11;
  `--who`/`--when` stay until P2.5, D-12). Its refusal on a non-physical claim names
  the file edit, `"kind": "physical"` in `claims/<id>.json`.
- **Commands that only mutated a record are gone** (PLAN A-8), and the file edit is
  the command: a claim is written and changed as `claims/<id>.json` (read strictly),
  so `claim` keeps only `list`, `show` and `physical`; a pack leaves the project when
  its name leaves `packs` in `.nopekit/project.json`, so `packs` adds and never
  removes; and the model entry is `"model_entry"` in the same file, so `model` takes
  no flag that sets it. argparse refuses each old spelling (`invalid choice`,
  `unrecognized arguments`), and every message that named one names the file edit
  instead (`_entry_edit`, below); `RemovedNamesAreGone` keeps them out of every
  document an agent reads.
- **`gap` is a read**: no lock, no write (S-43: it persisted every gap it derived,
  rewriting the ledger on every run). A Need is a record, `needs/<id>.json`, only
  when someone enriched it. **`model`** writes no record either: `--write` writes the
  one output it names, `.nopekit/model.json`, and nothing primes the parameter
  records from the model any more. Its orphans (`--json` `orphans`) are param records
  whose field the model no longer defines (`modelio.orphan_params`).
- **The index after every command.** `main` calls `_touch_index` after the command
  returns or is refused (not when interrupted), on a MIGRATED project only — never on
  a legacy one, where `ledger.json` is still the records — and never after `doctor`
  (it writes nothing), `init` (it never writes a `ledger.json`), or a `--no-record`
  run (nothing under `.nopekit/` but scratch). `store.write_index` rewrites the file
  only when its bytes change, so a read command that finds it current writes nothing.
  The rebuild takes no lock, so `status` stays usable while a sweep holds it; a record
  written by another command between this one's build and its write is caught by
  re-reading `records_digest` (up to `_INDEX_ROUNDS = 3`). A record that does not read,
  or a read-only checkout, leaves the index as it was with one stderr line and never
  changes the exit code.

**The readers on records** (checkpoint 1.3, spec U29). Each reads a fact where it
lives, and none writes a record:
```python
def _entry_edit(root) -> str        # '"model_entry" in .nopekit/project.json' (legacy: under
                                    # "meta" in .nopekit/ledger.json, until a check migrates it)
def _param_views(root, ledger, model, model_error, *, read_sets=None) -> list[modelio.ParamView]
    # param_view, plus — when the model does not load — a bare view (no value, model_error
    # set) for each name modelio.static_param_prose finds in the model's TEXT and no record holds
def _shown_params(root, ledger, model, model_error, resolution, registry, *,
                  projection=None) -> list[modelio.ParamView]
    # _param_views with gates from the resolution's read sets (registered gates only): what
    # status, report, site build, the page-staleness judgement and doctor show and judge;
    # read_by and sourced_from from graph.observe (projection: the derived names a read
    # of the whole ctx.params reads)
def _registered_reads(read_sets, registry) -> dict   # read sets of the registered gates only
def _why_text(root, ledger, registry, model, model_error, view, resolution, name, *,
              projection=None) -> str
    # decisions.why with view=_param_views (grounded_by from artifacts.value_grounding,
    # read_by and sourced_from from graph.observe), coverage=claims.effective_gates,
    # read_sets=verdicts.last_read_sets, verdicts=resolved, graph=graph.of(...)
    # (phase 3a: read by, sourced from, rests on, resolves, resolved by)
def _input_bytes(root, artifact, digests) -> dict
    # {"record", "sha256" (now), "pinned", "drift", "exists"} — the index's input-row rule
```
- **`init`** is born migrated: `store.init` writes the records layout, the three
  marked ignore/attribute blocks, and `project.json` last — never a `ledger.json`, so
  `init` then `status` prints no migration notice and leaves no `ledger.legacy.json`
  (what slipped through: every new project was born legacy and migrated by its first
  `check`, with a `git rm --cached` notice about a file git never tracked). Its next
  steps name the file edit that records the model (`"model_entry"` in
  `.nopekit/project.json`); `--json` names `project` (the file it wrote), `meta` and
  `next`.
- **`why`** (and `claim show`, through the same `_why_text`) prints the model's value
  where it lives — `param thickness = 8.0 mm   (model/bracket.py Config.thickness)`
  the moment the model says 8.0, with no check (S-39) — every loser tagged with its
  home, the gates from registry coverage and last executed reads, and grounding from
  the extractions. A model that does not load prints `param <name>` and `  model does
  not load: …`, never a number; a record carrying `value` is refused naming the model
  file.
- **`inputs`** shows, per artifact, its record, the digest of its bytes now against
  the pinned one (`DRIFT` when they differ, `MISSING` when the bytes are gone), and what
  it grounds — the `_grounding` map `why` reads, so deleting an extraction moves both
  (S-36: `why arm_length` said "GROUNDED BY arm" while `inputs` said `arm` was "NEVER
  READ"; then, with the copy gone and nothing derived, the other way round). `--json`
  rows carry `record`, `sha256`, `pinned`, `drift`, `exists` and `grounds`, plus the
  whole `grounding` map. The digest cache is consulted, never saved.
- **`doctor`** writes nothing on a legacy project or a migrated one (a byte snapshot of
  the tree, `tests/test_record_commands.py`), and adds three rows: `records` — the
  record files, each one the strict reader refuses a FAIL row of its own (never an exit
  2; the load stops at the first), or, on a legacy project, the ledger read in memory
  and what it "will migrate on next command" into (`store.migrate_legacy(apply=False)`);
  `run-history` — a leftover `runs/` directory, which it names and never opens;
  `index` — `store.agree`, a warning when the index is behind the records (what a
  hand edit leaves until the next command; `doctor` is the one command that never
  rebuilds it).
- **The site's own staleness** (`_site_state`, shared by `site status`, `status` and
  `doctor`) compares the page's `meta.records_digest` with `store.records_digest(root)`
  now (cli:H16) — never mtimes, which the index rewrite on every command made
  meaningless: a page of unchanged records read stale after any `status`, and a record
  edited by hand before the index caught up read current. Then its
  `meta.judgement_digest` with the digest of the document a rebuild would write now
  (`_site_judgement`: `site.state` over `_resolved`'s view and `_shown_params` — the
  caller's `resolved=(view, registry, resolution, params)` from `status` and `doctor`,
  or resolved there
  as `status` resolves, strict=False and `_projection_safe`; no gate, fixture or
  viewgen runs). What slipped through with the records alone (review, `repro_site`): a
  check that FAILed C1 moved no record, and all three called a page still showing C1
  PASS "current with the records". A moved judgement reads "what the page shows has changed
  since the site was built (C1 pass -> fail) — `nopekit site build`"
  (`site.judgement_moved`, at most `verdicts.MAX_STALE_REASONS` named); `status`'s
  `site:` line carries that reason, never a fixed sentence. A page with either digest
  missing (an older build) reads stale.
- **`last_check.json`'s `params`** is `{name: modelio.ParamView.to_dict()}` from
  `modelio.param_view` at the end of a recorded full `check`: the model's values and
  what lost, and the registered gates that read each (`read_sets`), beside the
  statuses — the agent's second read.

**`--junit [PATH]` at the edge** (`check`, `gate selftest`; the XML itself is
`report.render_junit` / `render_selftest_junit`). Three rules, each because of what
slipped through while designing it (cli:H7):
- **Unlinked first.** The target is removed at the top of the command — before
  `_registry` and `_projection`, which exit 2 on a broken pack or model, and before
  the lock. A run that ends early leaves no file: yesterday's all-green `junit.xml`
  beside a job that exited 2 is what a CI system would otherwise render.
- **One exit code, written last.** `check` returned from four places; the code is
  now computed once and the XML rendered from it, atomically, at the single exit —
  a report can never carry a judgement the return value did not make.
- **A PATH ends in `.xml`**, else exit 2 with `--junit takes a path ending in .xml;
  put gate ids before it`. The value is optional, so `gate selftest --junit
  bracket.deflection` would otherwise read the gate id as the path.
A bare `--junit` writes `report.JUNIT_DEFAULT` under the project root (ignored
scratch); a PATH resolves against the directory the command started in (`-C`, else
the cwd). `check --json` names the file written in `junit`.

**`gate selftest` has two modes.** Inside a project it runs the project's controls
against the project's model. With no project (`store.find_root()` is None — the
repository root, where `CLAUDE.md` tells a pack author to run it) or with `--pack`, it
runs **pack mode**, and branches before `_root`, `_lock`, `store.load` and
`_projection`, each of which assumes a project (cli:H8). What slipped through (S-09):
the command needed a project, so the prescribed merge check exited 2 where it was
prescribed, and CI only ran it inside the bracket, which loads no pack.
- **Targets.** `--pack NAME|DIR` (repeatable; a DIR that is not a pack means every
  pack directly inside it), else every **bundled** pack. `$NOPEKIT_PACK_PATH` and
  `~/.nopekit/packs` are searched only under `--user-packs`: both outrank the
  bundled packs, so without the switch the machine would choose which copy the merge
  check tests (S-87). Inside a project, `--pack NAME` also searches its
  `.nopekit/packs/`. Positional gate ids filter, by `gates._selected`'s rule, in
  both modes.
- **Each target goes through `packs.demonstrate(dir, tier=…)`** — the ceiling is
  `Tier.EXTERNAL` unless `--tier` is given, raised to a gate named explicitly — with
  a temp `out_dir` removed afterwards. Nothing is persisted: no ledger, no run
  history, nothing in the pack (Q1.8). `demonstrate` returns counts and problem
  lines, so the command first loads each pack through the same loader
  (`packs._load_dir`, modules reused, never re-executed) for its gate list, reads the
  result back per gate, and counts no control as fired unless `ran` adds up.
- **Exit 1** on any BROKEN control, on a failed baseline (pack mode), and on **zero
  controls exercised** — text and JSON paths both, in both modes — unless
  `--allow-empty`. A tooling skip is not exercise. Before (cli:H8): a selftest with
  nothing to run printed a sentence and exited 0, and `--json` said `"ok": true`.
- **Output.** The summary, both modes: `<n> control(s) in <t>: <f> fired, <b>
  BROKEN, <k> skipped (tooling)` — the last line of a clean run. Pack mode prints one
  line per pack before it (`[ok  ] beam-analytic (bundled) : 8 fired`) and, when
  any, `<m> baseline(s) failed:` rows after it. `--json` carries `mode`
  (`"project" | "pack"`), `packs`, `counts`, `baselines` (pack mode; `null` in
  project mode) and `ok`, which is `exit code == 0`. `--junit` writes suite
  `controls`, plus `baselines` in pack mode.

`packs validate` prints `demonstrate`'s notes — gates not demonstrated because their
tools are absent, and gates above the tier — as `note:` lines (`notes` in `--json`).
They are never problems, and never silent either.

`check` and `status` say the same thing about a blocking claim, through three
private helpers (named here because tests hold them to it):
```python
def _blocking_line(claim, status, reason) -> str   # "[FAIL ] C1 <statement> — <reason>"
def _blocking_reason(ledger, claim, status, *, stale=None) -> str
    # "gate : body" from the explaining verdict (a stale FAIL adds " (stale: <why>)"),
    # else a status sentence; `stale` is {gate: why} from the resolution
def _verdict_row(verdict, *, cached=None, fresh=None, stale_reason="", executed=True) -> dict
    # one verdict as JSON: its fields plus `ok` and `outcome`
```
- The BLOCKING tag is `report.status_tag`, never a status truncated to four letters:
  `check` printed `[fail]`/`[uncl]` where `status` printed `[FAIL ]`/`[gap  ]` for the
  same claims (S-69).
- The reason is `claims.explaining_verdict`'s, formatted exactly as
  `render_terminal` formats it (S-68): ran-and-failed, then errored, then skipped.
  A Gap with no covering gate reads `no evaluator` (`no gate covers it` until P2.1)
  and stops there (the `gap --propose` suffix was advice in a column that states
  facts); an unowned assumption `no owner recorded`; a refused evaluator
  `unqualified: <gate> …`; a Stale one names its invalidated gates and why.
- A verdict row in `--json` carries `outcome` (`"pass" | "fail" | "error" |
  "skipped"`, `Verdict.outcome`) next to `ok`. Both are set explicitly: `to_dict`
  serialises dataclass fields only, and both are properties. From 1.2 it also
  carries `cached` and `fresh` where the command knows them, `stale_reason` and
  `rho` when non-empty, and `duration_s`/`cpu_s` only for a row that EXECUTED in
  this command — a cached row replaying the cost of the run that wrote it would be a
  measurement of nothing (cli:H12; the cost lives in obs).

**Every reader resolves once** (spec §3.10, R-5, cli:H3). What slipped through
before: the CLI kept its own staleness rule (one hash of the projection against the
last sweep's) and its own flattening of the projection, each kept in sync with a twin
in `site.py` by a comment (S-28), and nine readers took the ledger's verdict list as
the truth — as current as the last `check` had left it.
```python
def _registry(root, ledger, *, strict=True) -> (gates.Registry, list[str])
    # a FRESH Registry per command (§3.5, cli:H6): packs in meta.packs order, then gates/;
    # loaders re-adopt a cached module's gates, so a second command in one process is
    # never "already registered" and never inherits another project's gates
def _context(root, ledger, model, projection, tier, *, quiet=False) -> GateContext
    # params = modelio.flat_params (the one flattening), a ledger COPY, extra = {}
def _resolved(root, ledger, registry, projection, model_error, *, now, model=None,
              sweep=None) -> (Ledger, verdicts.Resolution)
    # the resolver's answer and the VIEW every reader renders
```
- `_resolved` calls `verdicts.resolve` (never runs a gate or a fixture) and lays the
  result over the ledger as an in-memory **view**: `verdicts` from the resolution,
  `Claim.gates` from `claims.effective_gates` (registry coverage over the records'
  cached opinion — `claim show` used to resolve the bare record with no registry,
  cli:H5), `Param.gates` from `verdicts.last_read_sets` (a gate that did not execute
  keeps the parameters it read when it last ran, S-30). `status`, `claim list/show`,
  `report`, `site build` (passes `resolution=`), `gate show`, `why`, `doctor` and its
  ledger-integrity row, and `check` with its JUnit all render it. `sweep=` (from
  `check` only) lays that sweep's rows over the gates it selected: a row the sweep
  produced is current by construction, and under `--no-record` nothing reached disk
  for the resolver to find — except a row the sweep served stale
  (`SweepRow.stale_reason`, a costlier tier's undemonstrated entry), which stays in
  `stale_gates` with its reason on the resolution row.
- **The view is never saved.** It holds cache verdicts, and coverage and read sets
  the records do not own. `tests/test_check_cache.py` walks this file's AST and
  refuses any `store.save` argument that flows from `_resolved`.

**`check`** runs `verdicts.sweep` — affected-only (D-05): per selected gate,
availability, then admission (the negative control runs on a control-entry miss;
S-05: nothing here ever ran a control, so a logger with a declared one produced PROVEN
rows), then the cache, then the run. The clock is stamped once per command (`now`):
obs, remembered outcomes, `last_check.json` and the JUnit report carry one instant.
- `--force` re-runs every selected gate AND its control (R-9); CI runs the bracket
  with it. A forced run re-proves its own path and files what it said; it does not
  outrank a more thorough answer at the same inputs — the row is what the records
  resolve to (`sweep`'s step 5).
- `--no-record` is a dry sweep: nothing under `.nopekit/` but gate scratch in
  `out/` — no cache or control entry, obs, remembered outcome, `controls.json`,
  `digests.json`, `last_check.json` or index, and a legacy ledger migrates in memory
  only (S-32: it used to save the ledger and read its own fresh passes as STALE, the
  one global clock not having moved).
- `--only`/`--tier` select as before; a filtered sweep writes its entries and obs but
  no `last_check.json`.
- Otherwise, after the sweep: `verdicts.write_last_check`, then the index, under the
  lock. There is no run history (S-89: every recorded check rewrote the tracked
  ledger and appended a tracked run file).
- **`check` writes no record** (checkpoint 1.3): no parameter sync, no grounding or
  coverage copied into records, no `store.save`. The one record write it may make is
  the one-time migration of a legacy ledger, first, under the held lock (`_migrate`
  above); after it, a check writes verdict entries and ignored scratch only, and a
  second check on unchanged inputs changes no byte outside `.nopekit/{out,cache,obs}/`.

`check --json` (§3.13; verify.sh and CI parse `verdicts[]`): `verdicts[]` holds a row
for **every selected gate** — executed, cached or refused — in registration order, so
a fresh clone whose first check is all cache hits still lists `bracket.deflection`
(cli:H1). Row keys `gate, passed, skipped, ok` (stable); `outcome, cached, fresh`
(always); `rho`, `stale_reason`, and from P2.2 `blocked_by` and `blocked_kind` (when
non-empty — a gate not run behind a prerequisite: its roots, and the first's kind);
`duration_s, cpu_s` (executed rows only). Rows stay in registration order whatever
order the gates RAN in (P2.2-D4); `--only X` selects X's prerequisites too, so their
rows are present (D15). No `pruned` key: the rows carrying `blocked_by` are that list
(D16). `gate list --json` rows gain `needs` and `needed_by` (always lists; `--full`
adds `needed_by` beside the spec), and `gate show --json` gains `needed_by`.
`last_check.json` carries no verdict rows, so nothing there moved. `carried_over[]` holds the rows (with `cached`, `fresh`, `stale_reason`) of
registered gates this sweep did not select that have an effective verdict. `counts` =
`{ran (#ok over executed plus cached), failed, skipped, errored, executed, cached,
controls: {executed, cached, reverified}}`. `stale` means some selected gate was stale
BEFORE the sweep (cli:H19), `stale_reason` names each; `run` is `null` (no run history;
kept so a reader finds the key and nothing in it); `model_hash` is a display id; adds
`spine`, `junit` and `notes` (`SweepResult.notes`, each once: the writer's warnings and
ignored entries).

`check` text: executed rows stream as they land; a cached row prints only when it did
not pass, as `f"{line:<77} cached"`; then
`6 gates: 1 executed, 5 cached — 5 ok, 1 FAIL — tier 0` (`, S skipped`, `, R errored`
and `, U unqualified` when non-zero — an evaluator refused at its version is
`unqualified`, never errored, here and in `--json`'s `counts`; the time and the model
hash left this line), then a qualification line for every unqualified evaluator and for
every walked one whose qualification ran (P2.3-D16: `<id> : known-good pass · known-bad
fail · mutation n/m fail → qualified`), then
`controls: N run, N preserved, N re-qualified` (GLOSSARY §9's words from P2.3; it read
`E executed, C cached, R re-verified` until then) when a control ran or re-qualified,
a `rebuild:` line per article to rebuild (P2.5a), a `note: <note>` line per
`SweepResult.notes` entry (the writer's
`two outcomes recorded for identical inputs` among them — collected and printed nowhere
until the review), the skip digest, a note for gates outside the sweep, and the BLOCKING
list.

**`status`** text: `render_terminal`'s block (the readiness sentence with its hardware
clause, the count line, a row per unresolved claim, a `rebuild:` line per article to
rebuild, the gap records, the gates line, `next:`), then — from the review of
P2.5b, when the project declares one — the milestones head and one line per milestone,
as last evaluated (`report.milestone_line`), then in this order — `invalidated: <gate> —
<reasons>` per invalidated gate (continuations indented under `invalidated: `) with
`   (N verdicts current[, n unrun])` on the last, or `invalidated: none   (N verdicts
current)`; `last check run: <when> (<age> ago)` from `last_check.json`, or `last check
run: never`; `note:` per instrument mismatch and at most one `note: <k> evaluator(s) to
re-qualify — control inputs moved (<files>); the next check run re-qualifies them`
(GLOSSARY §9's words from P2.1: "pending", "stale" and "never run" are other statuses'
words beside the claim rows); `model: <entry> DOES NOT LOAD — <error>` only
when it does not load. A check is current when its row is Fresh with its control
admitted or pending. `status --json` drops the sweep record and its age, keeps
`stale`/`stale_reason`, and adds `stale_gates`, `freshness` (`{gate: {"state",
"reasons", "admission", "notes"}}`) and `last_check` (`{"when", "age_s"}`, nulls
before the first). It never runs a gate or a fixture and never writes.

`status --json` as Phase 2 closed it — every key additive (P2.1-D12), none renamed or
re-valued: top level `root`, `meta`, `summary` (`claims.summarise`'s keys, above),
`claims` (`{id: enum}`, the enum values kept until the rename pass), `statuses`
(P2.1: `{id: {key, word, cause, reason, errored}}` — GLOSSARY's word is `word`),
`errored` (P2.1: the ids Skipped by a crash), `gaps` (gap records), `stale`,
`stale_reason`, `stale_gates`, `freshness`, `last_check`, `model` (its entry, whether
it loaded, its error, its hash and the parameters no record defends), `packs` (`{installed, available}`), `inputs`
(`{total, unextracted}`), `site`, `problems` and `rebuild` (P2.5a: `summary.rebuild`
again, at the top). Against `860ffa6`, Phase 2 added `statuses`, `errored`, `rebuild`
and, under `summary`, `counts`, `errored`, `errored_ids`, `unresolved_ids`,
`unbound_ids`, `all_required_checked`, `not_compared`, `rebuild`, `contradictions` and
`milestones`; `tests/test_json_keys.py` (`JsonKeysAreKept`) holds that no older key
went.

**`gate show`**: the header and docstring, `tier`/`pack`/`entry`, `claims:`,
`runnable here:`, `prerequisites:` and `prerequisite of:` (P2.2), `control:` with its
note, `last verdict` from the view (with `(stale: <why>)` when it is not current), then
the evaluator's standing at this version, read by `verdicts.admission_state` (which
runs nothing): from P2.3 `qualification: <the qualification line> (control <rho12>)`
and its detail rows (`report.qualification_detail`: `known-good`, `limit moved`,
`known-bad`, `mutation`, `not mutated`); a pending one's re-qualification and its last
line, or the reason it is not qualified (`report.qualification_reason`, not yet
qualified included) in its place; and from P2.5a `track record: N contradictions at
this version, M at earlier versions`. Until P2.3 that standing was one `last selftest:` line in
`fired`/`PASSED its own known-bad`/`not admitted` words (GLOSSARY §9). JSON keys:
`gate`, `availability`, `available`, `last_verdict`, `needed_by` (P2.2),
`qualification` (P2.3: `{state, token, reason, line, facts, moved}`), `track_record`
(P2.5a) and `last_selftest` — kept, `{"outcome", "control", "at_this_version",
"admission", "detail"}` or `null`, its `outcome` `"unqualified"` from P2.3. What
slipped through (S-08): it read a ledger key `gate selftest` never wrote, so every gate
read "(never run)" forever.

**`gate selftest`, project mode**, runs every selected control — never served from
the cache — through `verdicts.admission(..., force=True)` with the sweep's anchors,
so it files byte for byte the control entry `check` would (O_EXCL: an unchanged
control re-creates the same name and writes nothing), plus a control obs row; a crash
or an unusable fixture is remembered, never cached. Project fixtures get the
known-good design (D-27). `--no-record` files nothing.

**`doctor`** names what the verdict cache cannot key on and a status line will not say
(checkpoint 1.2, spec §4 U23). It never writes, and runs no project gate. Besides the
environment rows (python, spine, project, records, run-history, index, layout, packs,
gates, tools, model, determinism, provenance, pack keys, ledger integrity, site, lock),
one row per kind below,
each `ok` when there is nothing to say — a clean project shows that it looked:

| Row | Status when found | What it reads |
|---|---|---|
| `orphan-entries` | warn | verdicts of gates this project does not register (the resolver's orphan rows): stale, never counting, nothing here can re-run them. A warning, not a ledger-integrity FAIL — nothing is corrupt, something was uninstalled |
| `instruments` | warn | an entry recorded under another library version (`recorded under numpy 1.26.4; here 2.1.0`, and "outcome differs across instruments"): provenance, never staleness (Q1.3) |
| `opaque-inputs` | warn | an entry with an opaque channel (a subprocess's own reads, a file outside the project, `self-modified:`, `env:<NAME>`, `sqlite-writable:`): never served from the cache |
| `cache-entries` | warn | every verdict or control entry the strict readers ignored, a `hand-edited entry` (digest mismatch) by name; any resolver note no other row claims lands here |
| `two-outcomes` | FAIL while `verdicts.TWO_OUTCOMES_IS_ERROR` (True since U25), else warn | two outcomes recorded for identical inputs, read at call time; **two control outcomes** at one rho_control FAIL always (the gate is not admitted). Controls are read for every gate on disk, not only for the gates whose verdict is Fresh |
| `code-digest` | warn | a gate keyed by its defining file (registered from Python, no recorded closure): a value its function closes over is not seen. The CLI never makes one |
| `pending-controls` | warn | controls whose fixture code moved (`<k> evaluator(s) to re-qualify — control inputs moved (<files>); the next check run re-qualifies them`, the sentence `status`'s note prints) |
| `imports` | warn | a gate that imports a third-party module (`CodeRef.third_party`) it does not declare in `requires_python` or a `python:` entry of `requires_one_of`: where it is missing, the gate errors instead of reading SKIPPED. In the gate's own file, read by reach — module-level imports plus those inside the gate function and the module-level functions and classes it names, transitively; other closure files whole. A whole-file rule named `cad.bounding`, the one tier-0 gate of a module whose other gates import trimesh lazily |
| `env-reads` | warn | the static env-read detector (spec §3.17): an AST scan of every registered gate's closure files for `os.environ`, `os.getenv`, `os.putenv` (and the bytes twins), through any alias of `os` or a `from os import`. rho never keys a variable (§8): a read inside a window is named opaque (`env:<NAME>`) and costs a re-run on every check, and a module-level read made at import, before any window, is seen by nothing else |
| `memos` | warn | the static memo detector: an AST scan of every registered gate's closure files for a module-global memo `modelio.clear_caches` cannot empty — a module-level container (a `{}`, `dict()`, `defaultdict` and the like) written into from a function body that does not bind the name itself, a module global a function rebinds under `global`, a mutable default argument its function writes into. The first gate to fill one opens the file; every later one opens nothing, and no entry keys it (review round 2) |
| `dynamic-imports` | warn | the static dynamic-import detector (`modelio.dynamic_imports`): an AST scan of every registered gate's closure files and of its owner's `selftest/*.py` (the fixtures and the known-good module its control runs) for `importlib.import_module(...)` or `__import__(...)` whose module a VALUE names. Only the run that first loads such a module in a process keys its source; a later one is served it from `sys.modules` and keys nothing. A literal name is resolved into the code closure and is not named (admission review, round 2, c) |
| `requirements` | FAIL / warn | architecture phase 1: a claim naming a requirement no file holds, or a requirement id that is also a claim's (FAIL, as a milestone requiring a missing claim is); else a requirement no claim serves (warn: it reads Gap) |
| `mechanisms` | warn | architecture phase 1: an evaluator a claim's mechanism names that is not registered here, or does not cover that claim (`claims.mechanism_problems`). Never a FAIL: a cross-check is not an integrity fault, and it moves no status |
| `sealed-fixtures` | FAIL | invariant 5 at runtime: every pack control run against this project's params through `packs.seal_findings`, into a temp `out_dir`; a control that reads its host is named with the paths it read |

The four static detectors measured zero hits on the 54 bundled gates before they landed
(R-4; `tests/test_doctor.py`). No staleness row: which gates are current is `status`'s
`stale:` block. No run-history row: there is none to read (S-31).

## What P2.1 moved

Claim statuses compose per GLOSSARY §3 (`claims.compose`), and every human word for one
comes from `report.HUMAN`. No record changes; the spine digest moved (models, gates,
verdicts), so every cached entry re-runs once.

**What now stops `check`** — `BLOCKING_STATUSES` is unchanged; more facts read into it.
Each with its remedy:

| a critical claim that | reads | stops `check` until |
|---|---|---|
| has an evaluator that crashed | Skipped, `errored:` | the evaluator is fixed (`nopekit gate show <id>`) |
| has a passing evaluator beside one whose tool is missing | Skipped, `skipped:` | the tool is installed, or the tag that binds the evaluator is dropped |
| has a passing evaluator beside one never run (e.g. a costlier tier under `check --tier 0`) | Open, `unrun:` | `nopekit check --tier <its tier>` runs it |
| has an evaluator refused at its version (it passed its own known-bad input) | Gap, `unqualified:` | the evaluator is fixed so its control fails |
| is an assumption | Gap, `no owner recorded` | its owner records it through the signing channel (later in Phase 2) — or the claim stops being required |
| is physical, with a covering evaluator that fails | Failing | the design passes it |

A physical claim with no result still reads Pending build and does not stop `check`
(nor does Assumed); both keep a project from *ready* (`all_required_checked`).

**What P2.1's review moved** (each a slip a refuter reproduced):

- a physical pass typed with `claim physical` reads **Pending build** (`a pass recorded
  by <who>, not bound to an article`), no longer Checked: GLOSSARY §3 checks a physical
  claim only on an article built from the current inputs, and nothing binds one yet.
  It never stopped `check` and never made a project *ready*; now the count line, the
  tag, JUnit and the page say the same;
- a recorded physical **fail** counts whatever was recorded after it and whatever the
  claim's kind becomes (R-3): `check` stops on it until the claim changes;
- `"critical"` that is not a bool is refused by the strict reader, naming the file;
- `check`'s summary line and `check --json`'s `counts` count an evaluator refused at its
  version as `unqualified`, never `errored`; `status`'s `invalidated:` line no longer
  counts it unrun;
- `claim list --status` refuses a spelling that is no status, naming the words;
- `last_check.json`'s `worst` is the most urgent blocker (`claims.severity`), not the
  first in record order;
- the readiness sentence says `NOT ready` in its never-evaluated branch too, and groups
  the claims not required by word, a crash counted apart.

**JSON: every existing key keeps its spelling and value domain; the words arrive under
new keys** (P2.1-D12):

| where | kept (now read per Table 1) | added |
|---|---|---|
| `status --json`, `report --json` | `claims` {id: enum}; `summary.by_status` (ten values, sum = `n_claims`); `summary.ready` (nothing stops `check`); `summary.n_gaps` (gap records) | `statuses` {id: {`key`, `word`, `cause`, `reason`, `errored`}}; `errored` [ids]; `summary.counts` {token: n}, sum = `n_claims`; `summary.errored`, `errored_ids`, `unresolved_ids`, `unbound_ids`, `all_required_checked` |
| `claim list --json` rows, `claim show --json`, `check --json` `blocking[]`, `claim physical --json` | `status` (enum) | `key`, `word`, `cause`, `reason`, `errored` |
| `check --json` | `ready` (nothing stops `check`); `counts.errored` (from review: crashes only) | `all_required_checked`; `counts.unqualified` (evaluators refused at their version) |
| JUnit root properties | `ready` | `all_required_checked` |
| `last_check.json` | `statuses` (enum) | `errored` [ids]; `worst.cause`; `worst.detail` by outcome |
| `state.json` | see `docs/SITE_CONTRACT.md` | |

From P2.1 the kept keys hold Table 1's readings: `blocked` holds errored claims beside
skipped ones (the `errored` mark tells them apart), `unclaimed` unqualified evaluators'
and unowned assumptions' claims, `pending` a pass beside an unrun evaluator.
**Deviation from the phase's brief, said here:** the brief asked for a `status` field
carrying the new word; GLOSSARY §7 keeps JSON values out of the human channels until
the rename pass, so `status` keeps the enum and the word is `word` (and the token
`key`) beside it — a reader that wants GLOSSARY's word reads `statuses[id].word`, and
one that wants *ready* reads `summary.all_required_checked`, never `summary.ready`.
*Rejected:* re-valuing `status` in place (a deny-list reader would go generous on
"failing"); a top-level `status` map (two meanings of one key in one document).

## What P2.2 moved

Evaluators gained prerequisites (invariant 10). Written here when Phase 2 closed: P2.2
documented every surface in its module's section (`gates.py`'s "Prerequisites",
`models.py`'s prerequisite mark, `cli.py`'s `check --json`) and left this list out,
so it was the one checkpoint a reader of these sections could not find. Decision rows:
`P2.2-Dn` in `docs/plan/phase-2.md`.

- **`GateSpec.needs`** (the last field then; `@gate(needs=[...])`): exact gate ids,
  refused at registration when malformed, when they close a cycle or when a
  prerequisite sits in a costlier tier than its dependent. Not in rho (D-04): declaring
  an edge re-keys only the declaring file, through its code digest.
- **The plan and the rule**: `gates.plan` runs each evaluator after its prerequisites,
  registration order otherwise, and lists rows in registration order
  (`SweepResult.order` records the run order); `gates.prerequisite_root` decides, and
  `verdicts.apply_prerequisites` applies it to a resolution. Under a prerequisite that
  failed, skipped, errored, is unqualified or is not registered, the dependent is not run
  and reads `gates.blocked`'s skip, never cached or remembered; under one invalidated or
  unrun it keeps its verdict and reads Stale.
- **`Verdict.blocked_by` and `Verdict.blocked_kind`** (`PrerequisiteKind`), spine-only;
  `blocked_by` writes `skipped=True, passed=False` (R-2). The claim's cause is
  `prerequisite` or, behind a crash, `prerequisite-errored` — as loud as the crash.
- **Channels**: `check --json` rows carry `blocked_by`/`blocked_kind`; `gate list --json`
  rows `needs` and `needed_by`; `gate show` `prerequisites:` and `prerequisite of:`;
  `pack validate` and `gate selftest --pack` check an edge's isolation (a bundled pack
  may need only its own gates); `doctor` names an unregistered need. No `pruned` key and
  no measured latency or cost descriptor (S-56 to P4).

## What P2.3 moved

An evaluator's verdict counts only when it is **qualified** at its current version
(GLOSSARY §2, invariant 9): its known-good control passed, its known-bad control
failed, both reached it through the same `ctx.extra` keys, and — for one not loaded
from the bundled packs — every conclusive mutation of its known-good control failed
(`gates.mutation_walk`, invariant 15). The spine digest moved; every project entry
re-qualifies once.

- **One entry carries the whole qualification**: `bad_extra` after `bad`, `good`
  (`{outcome, reads, extra, measured, limit, units, detail}`; `outcome` `"not-run"`
  where no known-good control exists — known-bad shown is a fact about the project,
  filed, never `null`), `mutation` after it (`{runs, boundary, results, inconclusive,
  not_mutated}`; tallies are derived, never stored), and `admitted`, which the strict
  reader re-derives from those facts and refuses when it does not follow. `SCHEMA`
  stays 1. An entry whose writer ran no known-good half (`good` null where one now
  resolves, or `mutation` null where the walk applies and both controls held) is
  *incomplete*: never served, and a miss for `check`. `rho_control` keys both halves'
  reads and every walk run's; the static part's `nc` gains `good` and whether the walk
  applies.
- **One judge**: `verdicts._qualification(QualificationFacts) -> token` — `""` when
  qualified, else the first fact that does not hold, in the rule's order:
  `known-bad:pass`, `known-good:fail`, `known-good:not-run`, `channels:differ|…`,
  `mutation:1/2`, `control:two-outcomes|…`, `qualification:not-yet|<tier>`, … The
  writer, the strict reader, `_decide`, `_admission` and `_run_control` all call it.
  `Verdict.unqualified` holds the token, `verdicts.parse_token` is its one inverse,
  and `report.HUMAN["qualification"]` words every token, every segment of the line
  (`report.qualification_line`, `report.verdict_line`), the Gap reason
  (`report.qualification_reason`) and `gate show`'s rows (`report.qualification_detail`).
- **Not yet qualified** (`verdicts.not_yet`): an evaluator never qualified at any
  version reads Gap until a check run qualifies it — no pass of it counts, beside a
  passing one too; one qualified at an earlier version reads Stale (`not yet qualified
  at this version — the next check run qualifies it`). A gate with nothing recorded
  at all reads so too (`verdicts.never_run`: no entry behind it), and the
  prerequisite rule reads it as an UNRUN root, never a negative one — a project never
  checked reads every claim Gap, not Skipped behind its guards. *Moved from the
  design's Q5 default* (Open/Stale until the first check run), by its critique: a
  claim left Gap before any control had run.
- **The project fixture root**: a project's relative fixture ref resolves against the
  project root the sweep names (`gates.selftest(..., fixture_root=)`), not the
  context's — a `selftest/known_good.py` may point its root at the design's own files.
- **A held qualification keeps its refusal**: a half that crashed, skipped itself or
  was unusable is remembered under `control:<gate>` with the facts and the refusal's
  own detail.

**What now stops `check`**, added to P2.1's table:

| a critical claim that | reads | stops `check` until |
|---|---|---|
| has a project evaluator and the project has no `selftest/known_good.py` (nor a `good=` fixture) | Gap, `unqualified: <id> : known-good not run` | a known-good design is written down |
| has an evaluator that fails its known-good control | Gap, `known-good fail` | the evaluator or the design is fixed |
| has an evaluator outside the bundled packs that passes a conclusive mutation | Gap, `mutation n/m fail` | the evaluator judges the value it reports |
| has an evaluator whose two controls reach it through different `ctx.extra` keys | Gap, `known-good and known-bad reach it through different channels` | a `good=` fixture on the same channel |
| has an evaluator never qualified | Gap, `not yet qualified at this version` | `nopekit check` |

**Channels.** `check` prints a line for every unqualified evaluator and for every walked
one whose qualification ran (`bracket.deflection : known-good pass · known-bad fail ·
mutation 1/1 fail → qualified`), never an `[ERR ]` row for an unqualified one, and the
controls line as `controls: N run, N preserved, N re-qualified`. `gate show` prints
`qualification:` and its detail rows where `last selftest:` was. `gate selftest` prints
every evaluator's line, sums them (`6 evaluators in 0.4s: 6 qualified, 0 unqualified,
0 skipped`) and exits 1 on any unqualified; pack mode prints a row per pack
(`cad-solid (bundled) : 7 qualified`), the line of every unqualified evaluator (every
one under `-v`) and its `problem:` lines. `doctor` gains `qualification` and
`known-good`. JSON is additive: `check --json` `qualifications`; `gate show --json`
`qualification` (and `last_selftest.outcome` `"unqualified"`); `gate selftest --json`
`qualifications` and `counts.qualified`/`unqualified`; `state.json` verdict rows read
`status: "unqualified"` with `qualification: {token, reason, text}`.

## What P2.4 moved

A goalpost lives in one place, a pass meets the goalpost it read, a value is compared
with the claim it is evidence for, and a pass counts only inside its evaluator's
operating context. The spine digest moved; every project entry and control re-keys
once (a P2.3-shaped verdict block is read, so it reads Stale for the spine that moved,
never "ignored").

- **`GateContext.acceptance(claim)`** returns the acceptance condition of the claim an
  id or a tag names and records `acceptance:<key>` (the condition alone) and
  `acceptance-shape:<key>` (its claims, quantity, comparator and units);
  `verdicts.acceptance_of` is the one rule, shared with `_ledger_now`. A pass that read
  one is held to it by `run_gate` — no value, other units, or a value it rejects is
  **errored**. `bracket.deflection` reads C1's; `DEFLECTION_LIMIT_MM` is gone (S-35).
- **Controls read their own acceptance conditions.** A project's
  `selftest/known_good.py` states `CLAIMS` (the bracket's C1 as calibrated).
  `_ledger_unseen` exempts an `acceptance:` key — and only it — from `channels:ledger`,
  and only where the qualification moved that condition in full (`moved_goalposts`):
  after the walk, every acceptance condition a qualification run read is re-run where it
  was first read with a verdict — the known-good run, or a walk run past a branch the
  known-good design never takes — with each of its limits moved on its own by x0.1, x0.5,
  x2 and x10 (`gates.goalpost_runs`), and the value and units there must not move
  (`goalpost:moves|<key>` otherwise). The shape key stays under `channels:ledger` (critique
  1 of the design: a gate keyed to its calibrated limit, or to whether its claim exists,
  read Checked under a blanket exemption) and holds which limits are stated, a one-sided
  condition's `limit_hi`, and whether the read returns one condition. *Review of P2.4:*
  x0.5 and x2 alone let a gate keyed below 0.2 (calibrated at 0.5) read Checked at 0.1;
  scaling both ends of a band kept its ratio; a stray `limit_hi` on a `<=` claim was in
  no digest a reader held; and the strict reader REQUIRED runs for every goalpost the
  folded trace held, so a walk run's own read made the writer refuse its entry and
  `check` stop at exit 2 — an entry that moved nothing now simply exempts nothing, and a
  writer/reader disagreement holds its one evaluator (`control:unwritable`). The
  control entry's `good.goalpost` holds one row per acceptance condition moved —
  `{key, measured, units, runs: [{end, limit, outcome, measured, units}]}`, `measured`
  and `units` its site's — and P2.4's first cut's flat rows (`{key, limit, outcome,
  measured, units}`) are read, never written, and move nothing.
- **`Verdict.comparator`** is set by every bundled gate with a finite limit (R-4: the
  detector over every bundled gate on both controls and the bracket's six — zero
  without one, and every margin on its pass flag's side but `bom.availability`'s
  end-of-life known-bad, named). **`claims.margin`** is carried on every JSON verdict
  row (`margin` or `margin_why`), and on every `state.json` verdict row.
- **The comparison** (`claims.cross_check`): a counted pass whose value, of the claim's
  quantity in its units, misses the claim's acceptance condition reads **Failing**
  (`acceptance condition not met`); a value of another quantity is listed as not
  compared (the report's note under the checked table, `summary.not_compared`,
  `state.json`'s `compared`/`not_compared`), never a status. Two limits that part are a
  `check` warning line, a `doctor` `limits` row and `check --json`'s
  `limit_disagreements` (S-35). The report's column is *Value* and its evaluator column
  *Evaluator* (GLOSSARY §9, S-46).
- **Operating context** (`GateSpec.operating_context`): a pass whose run did not read a
  declared key is errored; outside the range a pass carries the `context:outside` token
  (`verdicts._contexted`, in `_resolve_gate` and the sweep) and its claim reads **Gap**
  (`outside-context`) or **Assumed** under an owned `Claim.fallback`; a fail still
  counts. A known-good control outside the range is `known-good:outside`. A mutation
  counts wherever it lands (critique 2 of the design).
- **Visible changes for a third party**: a claim whose acceptance quantity and units
  equal a covering evaluator's `settles` and units, that evaluator passing at a value the
  claim's condition rejects, now reads Failing (it read Checked); the checked table
  drops values of other quantities into a note; `check` may print limit warnings.
  Nothing else moves without a new declaration (`ctx.acceptance`,
  `operating_context`, `Claim.fallback`).

## What P2.5a moved

A physical result, an owner and an expert judgment gained a channel; a physical pass
is bound to its article and to the claim as the person read it; a fail on a claim an
evaluator had passed is a contradiction on that evaluator's track record. The spine
digest moved (models, verdicts); every entry re-keys once. Decision rows: `P2.5a-Dn`
in `docs/plan/phase-2.md`.

- **The channel** (`cli._channel`, `claim physical <id> pass|fail|assume`):
  `interactive` from a TTY with no agent marker (`cli.AGENT_MARKERS`, by exact name —
  the review of P2.5a dropped "any `CLAUDE_CODE_*`") once the person typed the claim's id; `agent-session <id>` under a
  marker; `non-interactive` from a pipe. Only `interactive` makes a pass count or an
  attribution exist; `assume` is refused off it. `who` is `vcs.ident`, `when` the clock;
  `--who`/`--when` are refused before the project is read (`cli.REFUSED_FLAGS`). With
  no git identity nothing is recorded.
- **`results/<id>.json`** is `{"results": [...], "attributions": [...]}` (the second
  absent when empty): every entry written by `store.append_signed` is sealed —
  `digest = util.seal({"schema", "claim_id", "list", ...every stored key but digest})`
  — and chained (`prev`); a legacy prefix gets virtual digests, so the first sealed
  entry covers it whole. A broken seal or link refuses the file on every command,
  naming the file, the list, the entry and the fix, and every fail a `git checkout`
  would discard (`vcs.show`); `doctor` names it as a `results` row and still answers.
  `store.save` never appends after a sealed entry; `write_record("results")` never
  writes one. The index carries each file's `attributions`.
- **The article** (`verdicts.article_of`): the design as the model holds it when a
  result is recorded — every input and derived value, the model's code closure
  (canonical AST), and the bytes of every project file a path-valued parameter names or
  a current verdict read — hashed; `revision`/`dirty` are shown, never hashed.
- **The judge** (`verdicts.judge_results`, in `resolve`; `Resolution.standings`): a
  pass counts when it is `interactive`, recorded by someone, on a measurement or a
  judgment, for the claim's authority by them, at a value its acceptance admits, on
  the current article, for the claim as it reads now, with its evidence's bytes as
  recorded — the article judged before the evidence. Never cached. It reaches `compose`
  through `verdicts.view`, the one view builder (`cli._resolved`, `site.state`,
  `write_last_check`); a raw `store.load` ledger (`standing is None`) reads every pass
  Pending build in P2.1's words.
- **The ladder** (`claims.compose`): Failing `contradiction` / `judged-fail`; Gap
  `no-authority` / `authority-unattributed` (an expert-judgment claim until its
  authority records it, as themselves); Stale `article-moved` / `claim-moved` /
  `judgment-moved` / `article-unjudged`, leading an invalidated evaluator and citing it;
  Assumed `awaiting-judgment`; Checked (VERIFIED) `on-article` / `judged`. Owners come
  from `Claim.attributions` (`store` assembles them), so no spine module passes
  `owners=`. A Stale that awaits a person's act never stops `check` (`claims.blocks`,
  P2.5a-D27); it is unresolved. An owned fallback carries a pass outside its context
  whether or not that pass is invalidated.
- **The rebuild prediction** (`claims.rebuild`): the articles a counting result is bound
  to whose design moved — `rebuild:` lines on `check` and `status`, `### Articles to
  rebuild` in the report, `rebuild` in every JSON channel, `last_check.json` and
  `state.json`. In P2.5a it never under-predicts and over-predicts freely (the article
  is the whole design); "names nothing else" is P2.5b's, with `export`'s articles.
- **The track record** (`verdicts.track_record`, `Resolution.track`): from every fail's
  sealed `contradicts`, keyed by the evaluator's code digest; `gate show`'s `track
  record:` row and `--json` `track_record`. It moves no status but the claim's own.
- **Words** (`report.HUMAN`): `terminal`, the new leads, `not_counted`, `signing`,
  `physical`; `claim list` prints the terminal's word (`[automated]`, `[measurement]`,
  `[assumption]`, `[expert judgment: <authority>]`); `why` gains `PHYSICAL RESULTS`.
  JSON claim rows (`report.claim_json`) gain `terminal`, `terminal_word`, `authority`,
  `article`, `standing`, `contradicts`; `physical_result` gains `counts`, `why`,
  `recorded`. The page's "contradict" title is `phrases.disagree`, and a result's tone
  keys on `counts`.

## What the review of P2.5a moved

Each a slip a refuter reproduced; decision rows `P2.5a-Rn` in `docs/plan/phase-2.md`.

- **The restore advice** (`store._restore_advice`): what a restore brings back is the
  last commit's version when it verifies, else the newest commit's that does
  (`vcs.history`, `vcs.show(..., rev)`); against it, EVERY entry it does not hold is
  named — a broken seal as unverifiable, whatever its `passed` says, a sealed fail with
  the command that records it again carrying its measured value, evidence and
  authority — and an entry removed that nobody holds is said.
- **A results file no claim file holds** (`Ledger.removed`): kept, composed by
  `verdicts.view` beside the claims when it holds a fail (standing `removed`), so the
  fail reads Failing, stops `check` and is a JUnit failure; `track_record` reads it
  (`Contradiction.removed`); `doctor` names it as a problem; `next_claim_id` never
  reissues its id.
- **The judge** (`verdicts._judge_entry`): the value is compared with the acceptance
  condition only while the claim has not moved — a moved claim reads `claim-moved`
  whichever way its limit went; a judgment of other words reads `claim-moved` before
  any moved article, and `claims._JUDGED_STATES` leaves `claim-moved` out: such a
  claim reads as one written fresh — Gap until its authority records it, Assumed after.
- **Names** (`claims.name_of`): an owner, an authority, an attribution's name and a
  result's authority are compared with the whitespace around them gone. An owner's
  attribution counts if any recorded one matches the claim's owner and reason now.
- **The channel**: the agent markers by exact name, the one matched named in the
  recorded line and the refusal; a positional act and `--pass`/`--fail` that disagree,
  or an outcome's flag with `assume`, refused; `--json`'s `recorded.act` the act
  written; a value that is not UTF-8 refused naming its flag (`store._dumps` refuses it
  too); a pass refused while a registered evaluator has never run here.
- **The article's files** (`verdicts._read_files`): a registered evaluator's newest
  entry only — never an unregistered gate's or a legacy verdict's.
- **Words**: every value printed to a terminal through `util.printable` (`report._trunc`):
  controls escaped as `\xNN`. A judgment is worded as one (`JUDGMENTS` in `why`, "A
  judgment was recorded" on the page, its own Stale and fail lines, no article, its
  own page group). The readiness tally counts Checked on an article; the Stale advice
  is its own sentence.

## What P2.5b moved

A spend is named, and the place it costs money re-executes. The spine digest moved
(models, verdicts); every entry re-keys once. Decision rows: `P2.5b-Dn` in
`docs/plan/phase-2.md`.

- **Milestones** (`milestones/<name>.json`, `Milestone`): a description, the claim ids
  it `requires`, and a `generator`. Records, read strictly, in the index; a required id
  with no claim file is named (`missing`), never ready.
- **Ready is one predicate** (`claims.unresolved`, invariant 12): every reader asks it,
  with a milestone or without. `status` and the report list every milestone on one line
  each (`<m>: k of n required claims checked · s stale …`), "as last evaluated" — from
  the cache, which only `export` re-executes.
- **`export <m>`** (`cli.cmd_export`, `milestones.py`): the forced re-run of the
  closure at tier 3 with controls and prerequisites, filed as `check --force` files it;
  the judgment on the re-executed view; the generator run traced
  (`verdicts.export_article`); the package in `out/<m>/`; the record appended to
  `exports/<m>.json` by `store.append_sealed`, sealed and chained. `--dry-run` is the
  same path, stopping before the record. A disagreement between the re-run and the
  records it overrode (`SweepRow.disagrees`) refuses it, and no go-ahead covers it.
- **The exported article** carries what the generator read (`traced`); a pass on it is
  stale only when one of those reads moved, and counts only while its export is on
  record. `claim physical --article <hash>` binds a result to it; a fail recorded on it
  contradicts the verdicts the export sealed (`counted`), not the evaluator's verdict
  after its inputs moved.
- **Supersession**: a fail stops counting when a later pass stands on another exported
  article that moved where the fail's did and whose export printed other bytes
  (`verdicts._supersedes`); kept and named.
- **The hardware clause and the limits line**: the readiness sentence says, in every
  branch, what is checked on an article or that nothing is, and what needs one; the
  report and `export` say what Checked does not mean (`report.limits_line`).
- **The report is an output**: `report --write` writes `REPORT.md` at the root, ignored
  (`/REPORT.md` in the root block); `doctor` names a leftover `docs/readiness.md`.
  `report --milestone <m>` is the milestone's report, the one the package carries.
- **Latency** (`Claim.expected_latency`, `claims.latency`): declared on a
  measurement-terminal claim, measured from its exported article to the newest result
  recorded on it; `why` and `claim show` (when there is one to say), every JSON claim
  row (`report.claim_json`) and the test card carry it. A superseded fail reads "does
  not count" in `why`, on the page and in JSON, named with the pass that superseded it.
- **Words** (`report.HUMAN`): `milestone`, `readiness`, `latency`, `export`; the
  test card lists every required automated claim with a limit as a cross-check.

## What the review of P2.5b moved

Each a slip a refuter reproduced; decision rows `P2.5b-Rn` in `docs/plan/phase-2.md`.
Written here when Phase 2 closed, as the review's surfaces were scattered over the
sections above.

- **Supersession needs other bytes printed**: a fail on article A stops counting
  beside a pass on B only when no export of B carries the generator's bytes an export
  of A carried (`EntryStanding.built`, `verdicts._built_seal` — the package's files
  but `REPORT.md`, `model.json`, `MANIFEST.json`) and A's own export is on record. A
  no-op line in the generator no longer releases the same print's fail.
- **A link is a read**: `os.link`/`os.symlink` in a generator are traced (source read,
  link written); a link inside the package is refused (`generator_linked`), and a file
  there that no traced write put makes the article untraced — the whole design.
- **The scratch** is `.nopekit/out/export-<m>/` in both modes (`milestones.scratch_dir`),
  created and removed under the lock, inside the trace's out anchor.
- **Every export record of an article is charged**: `claim physical --article` reads
  them all (`milestones.sealed_on`, `milestones.bound_export`); latency is a
  measurement's, from the person channel only.
- **As last evaluated, everywhere but the boundary**: `report --milestone` and its JSON
  (`milestone`, `last_evaluated`), the project's sentence and `status`'s milestone
  lines say so; the package's `REPORT.md` is the boundary's (`render_markdown` with
  `boundary=True`).
- **Smaller**: a disagreement's refusal names the entry served and the way out, a
  model that does not load is its own refusal kind (`model`); the swap appends the
  record inside it and undoes itself when the append raises; a broken `exports/` seal's
  restore walks git (`store._export_restore_advice`); the Reproduce block's comments
  never glue to a word (`report._REPRODUCE_COLUMN`); `doctor` resolves a generator
  bound any way at module level (`cli._bound_at_module`).

## What phase 1 of the architecture moved

Claims say how they settle (`docs/SPEC.md` §7 step 1: decisions 12, 13, 37's paths, the
vocabulary of §0.9, METHOD's three edits). The spine digest moved (models, verdicts);
every entry re-keys once, and the bracket's committed cache was regenerated with it.

- **The mechanism** (`"mechanism": {"by", "how", "evaluators"}` in a claim file,
  `models.CLAIM_MECHANISM`): `by` is the terminal, held to `TERMINALS_BY_KIND` as
  `terminal` was; `how` is the older `note`; `evaluators` a cross-check that warns in
  `check` and `doctor` and never covers. The older keys still read; one member under
  both names is refused; the writer writes the new form. In memory the claim stays
  flat, so `claims.claim_digest` — the seal under every recorded physical pass and
  judgment — keeps its value for an unchanged claim in either spelling.
- **Requirements** (`requirements/<id>.json`, `Requirement`): the person's words and
  where they came from; claims point at them with `requirements`. A requirement's
  status is the worst of its claims (`claims.requirement_statuses`), Gap with none,
  shown in `status`, `why`, the report's `## Requirements`, the JSON summaries and
  `state.json`; *ready* never reads it.
- **`gap`** lists every claim whose mechanism has yet to run, physical and assumption
  claims included, and `--propose` searches with the `how` text.
- **Words.** Failing's hint and a control handed the live design say *the current
  design* (GLOSSARY §1: a *candidate* is an alternative design with a disposition);
  the three strings that named `"note"` name `"mechanism": {"how": …}`; a C5 with its
  test written down no longer reads `; no test written down`.

## What phase 2 of the architecture moved

Decisions keep their losers (`docs/SPEC.md` §7 step 2: decisions 14, 22, 25, 38). The
spine digest moved (models, gates, verdicts); every entry re-keys once, and the
bracket's committed cache was regenerated with it. Nothing here moves a status, a
qualification, a rho or `check`'s exit code.

- **Typed rejections** (`Rejection(Rejected)`, `decisions/` only): `type`, `by`,
  `returns_if` (`ReturnCondition`) and `input`, held by the strict reader
  (`store.rejection_problem`) and by `write_record` before a decision is written; a
  legacy entry reads untyped; PARAMS and `params/` keep text `Rejected`.
- **Return conditions** are evaluated by one function (`claims.return_state`), only on
  a claim reading Checked; `check` prints *may return* lines after its code is fixed,
  and its JSON gains `may_return`; `summarise()["rejections"]` carries every row.
- **Preferences** (`preferences/<decision>.json`, `PreferenceRecord`): the third
  sealed kind, through `append_sealed`'s list writer (extracted from the exports
  branch, unchanged for exports); `Decision.preferences` in memory, hidden from every
  gate (out of the digest by `verdicts._IN_MEMORY_FIELDS["decisions"]`, off the
  gate's copy by `verdicts._HIDDEN_FROM_THE_COPY`); `claims.preference_state` judges
  one; `nopekit prefer` is the channel (invariant 13).
- **Basis** (`GateSpec.basis`, `pack.json` `basis`, `PACK_BASIS`): shown in `gate
  list`, `gate show`, the report and `state.json`; never in rho
  (`verdicts.SPEC_FIELDS_IN_RHO` unchanged). The bundled gate files with an override
  re-key once in every project.
- **Proposers** (`provides_proposers`, `proposers/`, `InputArtifact.proposer`,
  `nopekit propose`, `ingest --proposer`): scripts, never imported, never registered.
- **`why <decision>`** shows a decision and every loser's facts line; the refusal
  "is a decision id" is gone.

## What phase 3a of the architecture moved

The graph and the round record (`docs/SPEC.md` §7 step 3a: decisions 18 and 19). The
spine digest moved (models: `Claim.resolves`, `FORBIDDEN_KEYS`; modelio: `ParamView`'s
two fields; verdicts: `design_of`, `_ABSENT_WHEN_EMPTY`); every entry re-keys once, and
the bracket's committed cache was regenerated with it. Article hashes are unchanged
(`design_of` is `article_of`'s body). Nothing here moves a status, a qualification, a
rho or `check`'s exit code, and `check` writes no new path.

- **`graph.py`** builds the typed graph and its tokens; **`rounds.py`** the round
  record. Neither is a spine module; only `cli` imports `rounds`.
- **`nopekit round`** — a tier-0 check, the site, the round record, one paragraph,
  exit 0. No hook and no commit yet (phase 6 of the sequence).
- **`Claim.resolves`** (physical claims; strict reader) and an assumption's id in
  `params/<name>.json` `grounded_by` (the value IS the assumption), both pointers the
  index and `doctor` check (`store.pointer_problems`).
- **The observed view** (`ParamView.read_by`, `sourced_from`, filled by
  `graph.observe`) in `last_check.json`, `state.json` and `why`. `status` and the
  report carry the view for the undefended rule only, and print neither field.
- **`why`** says what reads a value and what sources it, what a claim rests on,
  resolves or is resolved by, and resolves a derived value an evaluator read by name.
- **`cli._grounding`** moved to `artifacts.value_grounding` (the graph cannot import
  the CLI); `claims.outcome_key` and `report.status_token_word` are new public names.

## What phase 5 of the architecture moved

The site (`docs/SPEC.md` §7 step 5: decisions 17 and 28 to 36). The spine digest moved
(models: `Group`, `GROUP_KINDS`, `Ledger.groups`, `FORBIDDEN_KEYS["Group"]`; verdicts:
`_LEDGER_HIDDEN` gains `groups`); every entry re-keys once, and the bracket's committed
cache was regenerated with it (its round record reads round 2). Nothing here moves a
status, a qualification, a rho or `check`'s exit code.

- **`groups/<id>.json`** (`store.GROUP_ID`, `_check_group`, `group_problems`,
  `groups_in_order`): boards, zones and stations, hidden from every gate.
- **`page.py`** bakes `map.json` and `page.json`; **`prose.py`** reads the agent's text
  and scans it. Neither is a spine module.
- **`site.py`**: `state.json` is clock-free (`ages` and `write_build_json` hold the
  clock in the ignored `build.json`); `judgement_digest`/`judgement_moved` are
  `resolution_digest`/`resolution_moved`; `build` refuses a page `page.status_problems`
  names, copies evidence, writes tracked files only when their bytes differ;
  `technical.html` is a shell.
- **`claims.worst_of`** (a requirement's fold, now a board's too), **`graph.upward`**,
  **`util.write_json_if_changed`**, and `report._verdict_sentence`/`limits_line`'s
  `register="site"` with `HUMAN["site"]`.
- **Commands**: `site stamp`, `site badge`, `site serve --watch`, `site build --live`;
  `init` writes the README's badge block; `doctor` gains `groups`, `site text` and
  `readme`; `why` resolves a group and names a claim's board; a site is present only
  with `site/index.html` (`site.scaffolded`).

## What phase 6 of the architecture moved

Rounds and the plugin (SPEC decision 24 with §0.3, decision 37, the interview half of
decision 21). `.nopekit/project.json` gains `people` (`models.Person`, read by
`store.people_problem`); `vcs.py` gains its writing half (`init_repo`, `add`, `commit`,
and the reads `changed_paths`, `staged_paths`, `operation`) under a contract of its
own; `rounds.py` decides which paths a round stages and whose identity may commit them
(`ROUND_DIRS`, `ROUND_NOPEKIT`, `ROUND_ROOT_FILES`, `ROUND_MAX_BYTES`, `stage_plan`,
`listed`; the closed list went in the follow-up after the acceptance run, below);
`round` gains `--commit` and `--hook` (`_round_run`, `_round_commit`,
`_round_hook`, and the trace's seam `_round_start_trace`); `init` lists the person and
starts a repository; `doctor` gains a `people` row; `packs skills` prints the live
packs' PACK.md and skill files (`packs.skill_files`; `pack validate` refuses a
`skills/<name>/` with no SKILL.md). The plugin ships `hooks/hooks.json`, a Stop hook
running `round --hook` through `hooks/stop.py` (the session's cwd kept off the import
path). `state.json`'s `meta` never carries `people` (`site._UNPUBLISHED_META`): the
page is published. Invariant 8 gained the round's commit
(`test_round.TheRoundCommitStagesOnlyTheProjectsPaths`), the identity it commits under
(`test_round.RoundsCommitOnlyAsAListedPerson`) and the repository `init` starts
(`test_record_commands.InitStartsARepository`). Its review added
`vcs.committer_ident` and `vcs.in_repository`, `cli._round_ignored`, and the root
ignore block's `/.vscode/launch.json`.

## What phase 3b of the architecture moved

The trace and amber (`docs/SPEC.md` §7 step 3b, decision 39), and the map's middle.
No spine module moved, so no verdict re-keys. The bracket gains a `section` station, a
committed trace and amber file, and round 3. Merged after phase 6, `round` starts the
trace from phase 6's seam (`cli._round_start_trace`, after the commit and outside the
lock, its words `report.round_trace_sentence`), and the bracket's round record was
regenerated at the merge (round 5).

- **`derivation.py`** (new): the key, the moves, the strict readers, the order
  (`after_order`), the subprocess worker and its audit, the run, amber, the background
  start.
- **`graph.py`**: schema 2, the `after` edge type, `trace: {key, round}`, `upstream`,
  `direct_derives`, `depths`, `ties_of`, `moves`, `moved_by`; the trace's unmoved values
  are value nodes, so `why` can say they were not seen to move.
- **`rounds.py`**: `MOVED` gains `trace`, `KEYS` gains `amber`.
- **`page.py`**: map schema 2 (`MAP_SCHEMA`), feeds, taps, layers, ranks, sources, amber
  marks; `status_problems` holds amber and feeds. **`site.py`**: `state.json` gains
  `trace` and `amber` (inside its resolution digest); `build.json` gains `tracing`.
- **Commands**: `model trace`; `round` starts it in the background; `status`, `why`,
  `report`, `doctor` and `site build` say what the trace observed and amber.
- **In its review** (`HARNESS` 2, the bracket's trace re-recorded): the key holds the
  data the base build read (`keyed_on.data`), and `trace_for` serves a trace only while
  those files hold the same bytes; the reader's code walk skips what a snapshot skips;
  an integer moves toward zero and away by its own size, said at that size (`change`,
  `step_word`); two equal numbers are no move; a file, a mesh and an array compare by
  their bytes (`_content`); a move under `FAINT` does not count toward the order; a
  scratch row is written only for a pair whose raises were confirmed alone and that
  wrote nothing into the project, and carries its full key and, for amber, the subjects
  it ran against; a worker watches every thread; an amber run carries the claim's
  acceptance, and a flip the composition decided is said against it; the reason amber
  is not current is read off the closest file. The map bakes `unmoved`, a zone's
  `unmeasured` choices (idle only when every one was measured), a line's `sets` (an
  assumption's springs) and `unseen`, the `unrecorded` source, a scalar station head
  and units an evaluator reported.

## What the follow-up after the acceptance run moved

A sandboxed agent used the plugin for a person's request (`docs/SPEC.md` §8, "Follow-up
after the acceptance run"). No spine module moved, and `site.py`'s code did not either
(fdm-print's and cad-solid's gates import it), so no verdict re-keys.

- **`rounds.py`**: a round stages every file git does not ignore inside the project's
  directory (`stage_plan`), naming instead a directory, a file over `ROUND_MAX_BYTES` and
  a name in `ROUND_SECRET_NAMES` (`secret_name`); `ROUND_DIRS`, `ROUND_NOPEKIT`,
  `ROUND_ROOT_FILES` and `in_round_paths` are gone, and `StagePlan` is `staged`,
  `not_files`, `too_large`, `secret`. **`report.py`**: the paragraph's and the commit
  body's words for each, and `commit_parts`' `files` for a directory nopekit does not
  name. **`cli.py`**: `CommitOutcome` and `round --json`'s `commit` carry `not_files`
  and `secret` where they carried `outside`.
- **`cli.py`**: `site serve --detach` and `--stop` (`_serve_detach`, `_serve_stop`, the
  server's lock, record, log and lease); `site status` gains a `live:` line and `--json`
  a `server` object; `doctor`'s `site` row names a running server.

## Limits: what the spine cannot see, named

- **What the hook cannot see (Eddy milestone 0; `docs/EDDY_CONTRACT.md` says each in
  full).** `receive` judges a push without executing anything from the tree, so: a
  **forged PASS at the honest rho** — `check` run, its FAIL's reads copied, the outcome
  flipped, digest and name recomputed, `round` run — proves malformed, misnamed,
  wrong-digest, rho-inconsistent and tree-disagreeing entries wrong and lets this one
  through, since `reads.params`, `reads.model` and `code.digest` need the model and the
  gate modules executed; regenerating the state file reproduces the forged tokens byte
  for byte. Closed only by `export`'s re-execution (invariant 12) and by a renderer that
  runs `check --force` at tier 0 in a sandbox (milestone 1); until then the UI labels
  every token "run by the agent". A **resealed round record with true digests** passes
  the hook (seal and both digests hold); the renderer's whole-bytes regeneration catches
  it. The **sealed chains** (`results/`, `exports/`, `preferences/`) extended by a
  process that recomputes the seal pass the prefix rule, as this page's own limit says;
  nothing binds the pusher to the `who` of an entry (`NOPE_PUSHER` is reserved and
  recorded, no rule). A **project's own evaluators are not in the contract**: `gates/`,
  `selftest/`, `params/` and `model_entry` sit outside the four spec entries, so a
  candidate rewrites the gate that judges a claim without the spec moving; in milestone
  0 only pack evaluators pinned by `spec/packs.json` are accepted evaluators, and folding
  `gates/` and `selftest/` into `SPEC_ENTRIES` is a one-constant change. The state file
  is **machine-dependent where the resolution is** (a solver present on the agent's
  machine and absent from the image); the renderer must mark, never refuse. **Two
  outcomes at one rho** are noted (N04), never refused. **Secrets by content** under an
  innocent name pass (names only, `rounds.py`'s documented choice). **Growth across
  pushes** is bounded per push (`MAX_BLOB_BYTES`, `MAX_PUSH_BYTES`), never per
  repository. **Trace and amber files** are admitted by name shape and their reader's
  strict parse; their key needs the model. **Acts' own path** (`update-ref` inside the
  bare repository) has no hook and no test in milestone 0. **A line that predates the
  hook** has unvalidated commits below its tip. **Trailer heuristics drift** between git
  versions surfaces as E16 refusals of honest commits — loud, not silent. **A push of
  several refs** is all-or-nothing and an object new to two refs is scanned twice: over-
  refusing or over-scanning, never an acceptance of something unscanned.

- **`people` guards against surprise, not against the agent (architecture phase 6).**
  It is a key of `.nopekit/project.json`, which the agent edits like every record: it
  stops a round committing under an identity the project never named (a CI runner, a
  colleague's checkout, an unset or invented identity), and the skill tells the agent
  to change it only when the person asks, with the name and email they give.
- **A round's commit carries the person's identity though the agent made the change.**
  Nothing reads commit authorship as a person's act: results, owners, judgments and
  preferences take `vcs.ident` at the moment of the act, typed in the person's own
  shell (invariant 11), never a commit's author.
- **A commit hook that runs a nopekit command in the project meets the build lock the
  round holds**, and fails; git's refusal is quoted and the round's files are left
  staged. Releasing the lock before the commit was rejected: a writer could land
  between the staging and the commit.
- **gpg signing that waits for a pinentry runs into `vcs.VCS_WRITE_TIMEOUT_S`**, and
  the round reads "did not finish"; the Stop hook has no terminal to ask from.
- **A project directory its outer repository ignores reads "nothing to commit"**: git
  lists no change there, and a round cannot tell that from a round that moved nothing.
- **`changed_paths` sees what git sees, and the project's ignore files decide.** A file
  git ignores is never staged and never named; one it does not ignore is committed at
  the next turn's end — an environment (`.venv/`, `node_modules/`), downloads or scratch
  the agent left unignored among them (`init`'s blocks name none of them; the skill tells
  the agent to ignore them). The cap and the names are the only other guards.
- **A secret is known by its name, never by its content.** A credential in a file named
  otherwise (`config.yaml`, a CSV) is committed like any file; a design file whose name
  matches (`secret-sauce.md`, a Keynote `.key`) is named, never staged, and the person
  commits it by hand.
- **A file listed and gone before the round stages it fails the whole `git add`**
  (git refuses a pathspec that matches nothing, and the round reads "did not stage"): a
  process outside nopekit writing temporary files in the project at a turn's end is the
  one source, since every nopekit writer takes the build lock the round holds.
- **The detached server's witness is `flock`.** On a network filesystem whose `flock`
  is emulated or absent the probe cannot tell, and a server on another machine reads
  `elsewhere` from its record alone. `--stop` signals the server's pid alone, so a
  `site build` its watch had started finishes as an orphan.

- **The trace sees one point at ±10% (architecture phase 3b).** A nonlinear model is
  seen locally only: a `max()` hides a dependence there (the bracket's
  `thickness -> bbox_max`). Not seen to move never means independent. `after` is
  inferred: the two-mover floor drops single-mover evidence, and values the same
  choices move tie and cannot be ordered; two values that share their inputs without
  one feeding the other are ordered too (the bracket's slenderness before its bounding
  box), so the map draws a stop the order puts before a claim's reads faded, and the
  phone list names it apart. A move under 0.1% (`FAINT`) draws its edge and does not
  count toward the order. Choices outside `Config`, and yes/no and word choices, are not
  moved; a structure is compared by digest; a file a value names and a mesh or array are
  compared by their bytes, any other object by its type. The key holds the files the
  base build opened to read through Python (`_data_read`): a C extension's read, an
  environment variable and the network are not in it. Writes by a C extension or a
  subprocess of the build are not audited (a worker watches every thread of its own
  process).
  Amber's currency leaves out the spine digest, and a tool missing on one machine makes
  amber unclear there, so two machines can write different amber files for one design
  (an add/add conflict, resolved by keeping either); two people tracing one design at
  different rounds conflict on its `round`. A snapshot leaves out `.git`, `out/`,
  `site/`, `.nopekit/{out,obs,cache,trace}`, bytecode and environments. A background
  trace outlives the agent's turn; its lock is pid-checked.

- **The site's number scan is a floor, not a proof (architecture phase 5).** It is
  sign-insensitive (prose says "40% past the limit" over a margin of −0.3994); blind to
  number words, unit conversions and code spans; and small integers match counts. A
  stamp guards age, never correctness, and qualitative prose ("the arm is stiff") is
  never scanned: the floor sits under the prose so a reader sees the data beside it.
- **Evidence over 2 MiB is not copied to the page**, and a viewgen that writes a clock
  into its asset breaks the page's byte stability: every build then rewrites it.
- **A forged evidence manifest is the forged-cache class (issue #15).** The manifest is
  scratch under `.nopekit/cache/`, a plain sha256 per file: a process that writes one
  naming the bytes now in `.nopekit/out/` makes the build copy them as that entry's
  evidence. `export` re-runs the gates a spend requires, which rewrites the manifest
  from the re-run; nothing re-checks a copy an earlier build published. A copy made
  before the manifest existed, or on another computer, is kept as it is: the build
  binds what it copies, never what it keeps.
- **A bound figure is bound by bytes, never by what it draws (issue #15).** The spine
  checks a figure's evidence, its inputs, its format, its size, its chart's numbers,
  its colour range's ends and its caption's words. It cannot see whether a scene draws
  what its caption says, or whether the pixels follow the scale; the pack's renderer
  control and the real-tool tests hold that. A viewgen runs in process and is trusted
  to read only `ctx.entry.files` and what it bound: one that opens `.nopekit/out/`
  itself reads whatever run is there. A sidecar and its files forged with matching
  bytes, under an entry the cache holds, read as drawn (the forged-cache class);
  `export` re-runs the gates, never the renders. The caption check reads whole words,
  so a caption that says "gap" or "open" in their plain sense is refused too.
- **The watcher polls size and mtime over a fixed set of directories**
  (`site.WATCH_DIRS`): an edit outside them, or one that keeps both, is not seen until
  the next build.
- **Zone and station members are judged by doctor and the build, never by the index**:
  whether a name is a choice or a derived value needs the model.
- **A forger who reseals a page and keeps its tokens can still change its words.**
  `page.status_problems` compares tokens, *ready*, steps and marks with the
  resolution; a word, a hint or a value text edited by hand and resealed reads
  current until the next build writes it again. A view file edited by hand, its new
  digest written into `meta.views` and the page resealed, reads current too, and a
  build without that viewgen's tool keeps it, where its numbers vouch for prose.

- **A resealed round record reads as no hand edit (architecture phase 3a).** Its
  self-digest is tamper evidence against a hand edit, not a lock: anyone who can write
  the file can recompute it. Resealed, a forged token still reads behind the records,
  naming the resolution (what it holds is not what `round` would write now); one
  resealed with a greater `schema` reads `newer` and is neither compared nor rewritten.
  Nothing reads it for truth — every status is the resolver's — so a forgery changes
  what `git log -p` shows about a past round, never what any command decides now.
- **The round record is machine-independent only where the resolution is.** A missing
  tool (an evaluator Skipped here and not there), a remembered crash in the ignored
  cache, and an input's basis looked up over the user's packs each move its tokens by
  machine. They are shown, never normalised away (invariants 1 and 2).
- **A Stale evaluator's `reads` edges come from the entry the resolver stands on** —
  what it read last time, not what it would read now.
- **Instruction nodes inherit the code closure's known misses** (SPEC decision 11): a
  sibling loaded by `spec_from_file_location` and imported lazily (PETGasus's
  `topopt.py`) is no node, and a file `build()` reads is none.
- **`rests_on` is a direct read until the trace (phase 3b).** C1 rests on C6 because
  `bracket.deflection` read `load_n`; a claim whose evaluator reads a derived value
  computed from an assumption-sourced choice rests on nothing until `derives` edges
  exist.
- **"Behind" sees inputs, not nopekit.** An upgrade that changes a composition moves
  no digest the record holds, so the record reads current until `round` writes it
  again; `TheCommittedRoundRecordIsCurrent` is what catches it for the bracket.
- **`gates` and `read_by` can differ.** `ParamView.gates` follows the last executed
  run (`obs/`), `read_by` the entry the resolution stands on; after a fresh clone the
  first is empty and the second is not.

- **A re-sealed preference is a forgery the seal does not catch (architecture phase
  2),** as a results file's (below): `preferences/<id>.json` is sealed and chained
  with a plain sha256, and a process that recomputes it, calls `store.append_sealed`
  in process, or opens a pty with the agent markers unset mints a person's preference.
  P3's permission rule is the lock.
- **`ingest --proposer` is the agent's word (architecture phase 2).** It records which
  proposer produced a file the agent ran by hand; nothing ran it, so the basis it
  shows can be claimed for a file no proposer wrote. Display only: a basis moves no
  status.
- **`propose`'s guard is a stat of the project (architecture phase 2).** A proposer
  that writes outside `$NOPEKIT_PROPOSAL_DIR` is refused, each moved path named, when
  any file in the project but its ignored outputs and scratch (`cli._project_files`:
  `.git/`, `.nopekit/out/`, `cache/`, `obs/`, the index, `/out/`, `/REPORT.md`,
  bytecode) changes size or mtime, appears or goes — records, `model/`, `gates/`,
  evidence bytes and the verdict cache included. Detected, never undone. A write
  outside the project, into the ignored scratch, or one that puts a file's size and
  mtime back is not seen. (Review of phase 2: the guard was the records digest, and a
  proposer that edited `model/` was ingested silently.)
- **A basis is declared, never verified (architecture phase 2).** `sourced` says the
  logic follows a cited source; nothing reads the citation. Review reads it.
- **A `value` or `limit` return condition reads the claim as it is now (architecture
  phase 2).** It has no units of its own: an acceptance condition restated in other
  units changes what it compares, silently but visibly (`why` prints the units).

- **A mechanism's `how` is words, and nothing checks it (architecture phase 1).** It
  says what would settle a claim, and for a physical claim it is the written test a
  pass needs; nothing reads it for sense, so "looks fine" is a test as far as the spine
  can tell. Review reads it.
- **A mechanism's `evaluators` is a cross-check (architecture phase 1).** Coverage
  stays the registry's id-or-tag rule, so a broad tag still files an adjacent evaluator
  under a claim whatever its mechanism names; the list only warns about the evaluators
  it names.
- **A requirement's words are the person's only as the agent wrote them (architecture
  phase 1).** Nothing records that the person said them until the interview (phase 6);
  a `source` of `"interview"` is the agent's word.

- **Ready on `status` and the page is as last evaluated (P2.5b).** It reads the
  verdict cache, which the inner loop never re-executes and can be forged (below);
  only `export` re-runs what a spend requires. Every milestone line says so, and so
  does every *ready* sentence but the boundary's (review of P2.5b): `report`,
  `report --milestone` and its JSON (`milestone.last_evaluated`), `status`. The page
  is P5's.
- **A generator whose bytes move on every run (review of P2.5b).** Supersession
  compares what two exports printed (`_built_seal`); a generator that writes a clock
  or a random id differs from itself, so a reprint of the object that failed would
  read as another object. The bracket's is deterministic by design (its docstring
  says so); nothing enforces it.
- **An export records an intent to build, not a build (P2.5b).** The article is what
  the generator read and the package what it wrote; nothing sees the slicer, the
  printer or which package a person printed. A result binds to an article by its hash
  (`--article`), which the person types from the package.
- **An untraced generator's article over-predicts (P2.5b).** A generator that reads
  something opaque, has no recorded code or uses the model gets the whole design as its
  article, so any change predicts its rebuild. A traced one names only what it read —
  and a read the tracer cannot see (the environment, a module-level memo; the edges
  below) is not in it.
- **A milestone names its own requirements (P2.5b).** Anyone who edits
  `milestones/<m>.json` can shrink what a spend requires, and an export after the edit
  is ready by the new list; the edit is in git and in the export record's `requires`.
  P3's permission rule on `milestones/` is the lock.
- **A re-sealed export is a forgery the seal does not catch (P2.5b),** as a results
  file's (below): `exports/<m>.json` is sealed and chained with a plain sha256.
- **A generator's writes during `--dry-run` are refused and named, not undone
  (P2.5b).** It runs in the scratch directory; a write outside it is reported as the
  refusal and left where it landed, since undoing a write is a second writer.

- **A re-sealed forgery counts (P2.5a).** A seal is a plain sha256: a process that
  writes `results/<id>.json` with `channel: interactive` and recomputes the seal, calls
  `store.append_signed` in process, or opens a pty with the agent markers unset mints
  an entry indistinguishable from a person's. The seal catches drift and a helpful
  agent's shortcut — a hand edit, a fail flipped, an entry removed — nothing more
  (D-13); P3's permission rule on `results/` (W9) is the lock. Tail truncation is not
  detectable by a chain; git is (the file is tracked).
- **The article is the design at recording, not the object (P2.5a).** A person who
  records a result on a part printed from an older revision binds it to the current
  design; the prompt shows the revision and any uncommitted model edits for that reason.
  And it is the whole design: any value change moves every recorded article (the
  rebuild prediction over-predicts). P2.5b's `export` records build-time articles.
- **An evaluator a prerequisite keeps from running (review of P2.5a).** A pass waits
  until every registered evaluator has run here once, so the files each reads are in
  its article — except one pruned behind a prerequisite that is not established: it
  cannot run before the print is tested, and the files only it reads join an article
  recorded from its first run on.
- **A new agent marker (review of P2.5a).** The markers are matched by exact name;
  a session marker a later Claude Code, Codex or Cursor adds under a new name reads as
  a person's shell until it is added to `cli.AGENT_MARKERS`. Codex's markers
  (`CODEX_CI`, `CODEX_TOOL_CALL_ID`) are read from its source, not a documented
  contract, and Cursor's `CURSOR_AGENT` has gone missing from one of its CLI builds
  before; a host that drops one reads as a person's shell until a pipe says otherwise.
- **An institution as an authority (P2.5a).** The authority's git identity must be
  theirs; an institution with no person's identity cannot record its judgment until
  named delegates exist (on the check-in batch).

- **A limit no run visits (P2.4, as its review restated it).** The qualification moves
  each limit a qualification run read — x0.1, x0.5, x2, x10, each limit of a band alone
  — where it was read, and requires the gate's value unmoved. A gate may still lie at
  EVERY limit the runs do not visit: between those points or past them — below 0.04
  where the runs looked at 0.05, at the live 0.37 exactly — as a gate keyed to a
  parameter value the walk never visits does; and a limit read only on a branch no
  qualification run takes is never moved, so it is never exempt (it reads
  `channels:ledger` whenever it differs from the known-good design's). The earlier
  wording here, "a gate that lies only at one exact goalpost", understated it: the
  review planted gates lying over an open range of limits (`acc.limit < 0.2`), over
  every band of another ratio, and at every `limit_hi` on a one-sided claim, and each
  passed x0.5 and x2. No tool catches a lying value but an independent evaluator.
  *Rejected:* substituting the live limit into the known-good design (a record per live
  value, and a control that re-keys on every limit edit); random points (a qualification
  must be reproducible); more fixed points (each a run, and none closes the residual).

A verdict is keyed by what its gate was SEEN to read (rho), a control counts only when
it was SEEN to fail, and a record is read strictly from its file. Each of those has an
edge, and an edge nobody wrote down is where the next false PASS comes from (PLAN §18:
a limit must be visible, not just true). Phase 1 closes with these, each stated where
a reader of the output meets it:

- **Environment reads.** A gate that reads `os.environ` or `os.getenv` fires no audit
  event, and rho does not key a variable's value (it differs per machine and per
  shell). A read inside a window is NAMED instead — the opaque channel `env:<NAME>`
  (`env:*` for the whole environment), recorded by the class `os.environ` gets with
  the hook — so the entry is never Fresh and `status` says why. What slipped through
  (review round 1, `probe.env`): the read was recorded nowhere, and a plain check
  served the PASS after the variable moved. A module-level `LIMIT = os.getenv(...)`
  read at import, before any window, is still seen by nothing; `doctor`'s `env-reads`
  row is a static detector over every registered gate's closure (zero hits on the
  bundled corpus when it landed). *Rejected:* a proxy OBJECT put in place of
  `os.environ` — it changes what a subprocess started inside a gate inherits; the
  class change keeps the object, its data and its `putenv`s.
- **Module-level memos.** A memo hit opens nothing, so no trace sees what it served.
  functools' memos are emptied before every gate, fixture and known-good run
  (`modelio.clear_caches`); any other memo held at module level cannot be emptied from
  outside, and `doctor`'s `memos` row is a static detector for it (zero bundled hits
  when it landed). Not reached: a memo held on a module-level INSTANCE (a
  `cached_property`, a dict attribute), and one inside a third-party library the gate
  calls.
- **Modules loaded at run time.** A helper `load_path` loads while a gate, a fixture or
  `known_good.context` runs is keyed — its code and what it read at import are reads of
  that run, whether it ran or was served. A module the STOCK import system loads then
  (`importlib.import_module(name)`, a lazy `import`) is keyed only by the run that
  first loads it in a process, and runs from a `__pycache__` that still validates (a
  same-size, same-second edit runs the old bytecode, S-26 by the stock loader): its
  literal name is in the code closure by the static pass, and `doctor`'s
  `dynamic-imports` row names every name a value decides. Load a helper with
  `modelio.load_path`, which compiles the bytes on disk and keys it on every run. Not
  keyed: the `nopekit.*` modules outside the spine digest that such a helper imports.
- **`ctx.model is None` is not recorded.** A gate that branches on whether a model is
  loaded at all is invisible to rho on that branch; `ModelProxy` records a real use of
  the model, never its absence. No bundled gate reads `ctx.model`, and an evaluator not
  from the bundled packs is handed none on any run — its check run, its controls, its
  walk (`verdicts._no_model`) — so the branch is the same branch everywhere for it
  (review of P2.3: `if ctx.model is not None: passed = True` read Checked).
- **A qualification run is told apart from a check run by what nothing keys.** A
  project evaluator's check run is handed nothing its qualification runs were not —
  no model, and a verdict that read ledger values no qualification run read does not
  count (`verdicts._ledger_unseen`). What stays visible to an evaluator that looks: the
  path of its `ctx.out_dir` (a control's is under `.nopekit/out/controls/`, a walk
  run's is a temp directory outside the project, as invariant 15 requires; a check
  run's is `.nopekit/out`, where the evidence a cached pass cites lives), the call
  stack (`inspect.stack()` names `mutation_walk`), the clock. A gate that branches on
  them is a forger the controls cannot see. *Rejected:* one path for every run (the
  walk must write outside the project, the check run's evidence inside it); recording
  a read of the `out_dir` string as opaque (`os.path.join(ctx.out_dir, …)` reads it,
  so every gate that writes evidence would never be Fresh). On the check-in list
  (review of P2.3, `br8`).
- **Admission outside `check` is static.** `status`, `report` and `site build` judge a
  control from its entry's static part, its recorded files and host reads, and its
  fixture closure's digests — none of them runs a fixture. When a fixture's code moved
  they count the control **pending**, with a note (`<k> evaluator(s) to re-qualify —
  control inputs moved …`), until the next `check`, `gate selftest` or `check --force` re-verifies it
  by the values it feeds its gate. So a model edit that defuses a bracket control is
  caught at the next `check` and is never counted as demonstrated in the meantime
  without that note; CI and P2's `export` re-run it (R-9).
- **Opaque gates are never Fresh.** openmodelica's `checks`, `compiles` and `simulates`
  do their reading inside `omc`, a subprocess the audit hook cannot see into
  (`subprocess:omc`). They re-run on every `check` where omc exists (about 0.6–1.0 s
  each, their controls too), skip where it does not, and read STALE (`opaque inputs`)
  in `status` between checks — the honest price of an input the tracer cannot see. A
  project that points `modelica_result_csv` at another gate's output under `out/`
  makes `modelica.result_claim` opaque too (`out:<rel>`). `doctor`'s `opaque-inputs`
  row lists them.
- **Instruments are not in rho, but are in the entry's bytes.** Two machines with
  different trimesh or numpy versions that record the same rho and outcome write one
  path with different bytes: an add/add merge conflict where either copy is correct
  (equal outcomes). `doctor`'s `instruments` row notes the mismatch; staleness never
  depends on it (Q1.3, M11.5).
- **A gate registered from Python is keyed by its defining file.** Values its function
  closes over (a lambda over a local variable) are not in that digest. The CLI never
  registers one; `doctor`'s `code-digest` row names every gate keyed this way.
- **A pack fixture's host reads are keyed only when the host is live.** SEALED
  (invariant 5) is enforced by the seal detector (`packs.seal_findings`, `doctor`'s
  `sealed-fixtures`, `pack validate`), not by substituting a clean host.
- **The committed bracket cache is held to the running spine unconditionally.**
  `BracketCacheIsCurrent` compares every committed entry's spine digest with this
  interpreter's; if the canonical AST walk is not portable to 3.10 or 3.13, CI goes red
  there at that test and at `SpineDigestIsPortable` together — the true state, since
  the committed cache would then be stale for those users. Only 3.12 was available
  where this phase was built.
- **Spines older than this layout are not guarded.** An nopekit from before the
  records layout reads `.nopekit/ledger.json` as the records and may rewrite it;
  nothing written now can stop it. `project.json`'s `schema` refuses NEWER layouts,
  going forward only. The README says so.
- **The tracked cache is forgeable in the inner loop.** An entry's `digest` is
  integrity (a hand edit is ignored as `hand-edited entry`), not authentication:
  a hand-made entry with a matching digest is served until something re-runs it. R-9
  is the defence — `check --force` in CI, and from P2.5b `export`, which re-runs every
  evaluator a milestone requires and refuses when the re-run and the records disagree —
  never the cache.
- **A concurrent writer during a gate run is not detected.** File digests are taken
  after the gate returns, so a file rewritten while the gate read it keys the rewrite.
- **Smaller edges, named in their sections:** an explicit `dict.__getitem__` call on
  `ctx.params` is not recorded, and a mutable non-JSON leaf is handed out by reference
  (`ParamTrace`); an `os.open` or `os.stat` relative to a `dir_fd` is not resolved,
  and a name bound to `os.stat` before the spine was imported is not probed (the audit
  hook, the stat probes); an existence question outside the project is not an input,
  and a decision on an mtime is not keyed (`Reads.from_trace`); a subprocess's own
  reads are opaque, never covered.
