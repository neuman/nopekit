# Spine contract (internal)

Every spine module builds against `src/atompipe/models.py`. **Read that file first.**
It defines every type that crosses a module boundary; no module may define its own.

Hard rules for the whole spine:

1. **Standard library only.** No third-party imports anywhere under `src/atompipe/`.
   The spine must never be the reason an install fails. Packs declare their own deps.
2. **Python 3.10+ syntax** (`X | None`, `match` ok). Target 3.12.
3. **No `Date.now()`-style hidden state in logic** — callers pass timestamps in. A
   function that stamps its own time is untestable. Use `utcnow_iso()` from `util.py`
   only at the CLI edge.
4. Every public function gets a real docstring saying *why*, not just *what*.
5. Comments carry provenance: when a rule exists because
   something slipped through, say what slipped through.
6. `from __future__ import annotations` at the top of every module.
7. Errors the user caused raise `AtompipeError` (from `util.py`); bugs raise normally.

## Where facts live

Each fact has one home, and every other place that shows it is an output of that
home (METHOD rule 1). The layout, the writer, the strict reader and the index are
`store.py`'s section below; the verdict cache is `verdicts.py`'s; what `check` writes
is `cli.py`'s.

| Fact | Home | Git | Written by |
|---|---|---|---|
| a claim, a decision, an enriched need, an input's record, a param's provenance, a declared view | `claims/`, `decisions/`, `needs/`, `inputs/<id>.json`, `params/`, `views/*.json` — one file per record | tracked | a human or the agent, editing the file; the shims `ingest`, `extract`, `decide` |
| project meta, the model entry, the live packs | `.atompipe/project.json` | tracked | the same; `init`; `packs add` |
| a physical result | `results/<claim-id>.json`, append-only | tracked | `claim physical` |
| a parameter's value, units, rationale and what lost to it | the model (`model/*.py`: `Config`, its docstrings, `PARAMS`) | tracked | the model's author |
| a gate's verdict, and its control's | `.atompipe/verdicts/<gate id>/`, one file per rho, never rewritten | tracked | `check`, `gate selftest` |
| every record, in one read | `.atompipe/ledger.json` — the **index**, generated | ignored | every command but `doctor`, `init` and `--no-record` runs |
| statuses, counts, the worst claim, the parameter view, as the last full check saw them | `.atompipe/cache/last_check.json` | ignored | a full recorded `check` |
| what a run cost, when it ran | `.atompipe/obs/` | ignored | every recorded run of a gate or a control |
| a crash or a self-skip, remembered — never evidence | `.atompipe/cache/last_outcomes.json` | ignored | `check`, `gate selftest` |
| time savers: file digests, re-verified fixtures | `.atompipe/cache/digests.json`, `controls.json` | ignored | `check`; losing either costs a re-hash or a fixture run, never a verdict |
| gate scratch and evidence | `.atompipe/out/` (a control's: `out/controls/<gate id>/`) | ignored | gates |

The index and `last_check.json` are the whole project in two reads (D-06); neither
is read for truth by any spine code, and the index never holds a status, a coverage
or a verdict. There is no run history: git and the verdict cache are the history.

## Module map and public surface

Every `src/atompipe/*.py` except `__init__` and `__main__` has a heading below, and
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
BLOCKING_STATUSES: frozenset[ClaimStatus]     # FAIL STALE UNCLAIMED BLOCKED PENDING REFUTED
class Tier(enum.IntEnum): INSTANT = 0 | BUILD = 1 | SOLVE = 2 | EXTERNAL = 3
class ViewKind(StrEnum): MODEL3D | IMAGE | CHART | TABLE | FIELD | DIAGRAM
class ArtifactKind(StrEnum): SKETCH | REFERENCE | CAD | SCREENSHOT | DATASHEET | SPEC
                             | MEASUREMENT | STANDARD | DATA | LINK | OTHER
EXT_KIND_HINTS: dict[str, ArtifactKind]       # ".stl" -> CAD; `atompipe ingest`'s first guess
class NeedStatus(StrEnum): OPEN | PROPOSED | DEFERRED | INSTALLING | SATISFIED | ABANDONED
class Comparator(StrEnum): LE | LT | GE | GT | EQ | NE | BETWEEN
    def holds(self, measured, limit, limit_hi=None) -> bool
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
    value: str; why: str; evidence: str = ""
class Param(Record):            # one model parameter with its full provenance
    name: str; value: Any; units: str = ""; rationale: str = ""
    rejected: list[Rejected]; source: str = ""; grounded_by: list[str]   # InputArtifact ids
    gates: list[str]; derived_from: list[str]; changed_in: str = ""; tags: list[str]
    is_derived -> bool                                                   # property
class Acceptance(Record):       # the machine-checkable threshold behind a claim
    quantity: str = ""; comparator: Comparator = LE; limit: float | None = None
    limit_hi: float | None = None                                        # BETWEEN only
    units: str = ""
    def holds(self, measured) -> bool;  def render(self) -> str
class PhysicalResult(Record):   # a real-world result a human recorded against a claim
    passed: bool; when: str = ""; who: str = ""; detail: str = ""; evidence: list[str]
class Claim(Record):            # something that must be true for the design to work
    id: str; statement: str; kind: ClaimKind = MEASURABLE; acceptance: Acceptance
    rationale: str = ""; source: str = ""; grounded_by: list[str]; gates: list[str]
    tags: list[str]; critical: bool = True; physical_result: PhysicalResult | None = None
    note: str = ""
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
    extracted -> bool                                                    # property
class Locator(Record):          # WHERE a verdict applies (docs/SITE_CONTRACT.md)
    view: str; target: str = ""; kind: str = "part"; label: str = ""; severity: str = ""
    position: list[float] | None = None; value: float | None = None
class View(Record):             # one thing the site renders and verdicts address
    id: str; kind: ViewKind = IMAGE; title: str = ""; src: str = ""; description: str = ""
    gates: list[str]; data: dict; meta: dict; pack: str = ""; order: int = 100
class Decision(Record):         # one decision-log entry; names what LOST
    id: str; title: str = ""; when: str = ""; summary: str = ""; rejected: list[Rejected]
    params_changed: list[str]; claims_changed: list[str]; evidence: list[str]
    body: str = ""                                                       # long-form markdown
class PackManifest(Record):     # pack.json, tier 1 (docs/PACK_FORMAT.md)
    name: str; version: str = "0.1.0"; description: str = ""; settles: list[str]
    claim_classes: list[str]; provides_gates: list[str]; provides_views: list[str]
    provides_generators: list[str]; key_scope: str = ""; requires_tools: list[str]
    requires_python: list[str]; max_tier: Tier = INSTANT; lenses: list[str]
    authors: list[str]; licence: str = "Apache-2.0"; homepage: str = ""; origin: str = ""
class KeyCollision(Record):     # one projection key two installed packs read differently
    key: str; packs: list[str]; meanings: dict[str, str]; scoped: dict[str, str]
    primary: dict[str, str]; live: bool = False
    def fix(self) -> str                                 # the one sentence that resolves it
class ProjectMeta(Record):
    name: str = ""; summary: str = ""; created: str = ""; revision: str = "v0.1"
    model_entry: str = ""; packs: list[str]; spine_version: str = ""
class Ledger(Record):           # the whole project, in memory (store.load assembles it)
    meta: ProjectMeta; claims: list[Claim]; params: list[Param]
    inputs: list[InputArtifact]; needs: list[Need]; decisions: list[Decision]
    verdicts: list[Verdict]                                              # latest per gate
    views: list[View]
    def claim(cid) | param(name) | artifact(aid) | need(nid) | verdict(gate_id) -> record | None
    def verdicts_for(cid) -> list[Verdict];  def upsert_verdict(verdict) -> None
```
**Records as files** (checkpoint 1.3). Three constants tell `store` how a record
becomes a file and back; they live here because they are facts about the record
types, not about the disk:
```python
RECORD_KINDS: dict[str, type]    # record dir -> kind: claims Claim, params Param,
                                 # decisions Decision, needs Need, inputs InputArtifact,
                                 # results PhysicalResult, views View (store.RECORD_DIRS' order)
FORBIDDEN_KEYS: dict[str, dict[str, str]]   # class name -> {key: the home that owns it};
                                 # "*" applies at every depth of every kind
ALWAYS_WRITTEN: frozenset[tuple[str, str]]  # {("Claim", "kind"), ("Acceptance", "comparator")}
```
`FORBIDDEN_KEYS` is what a record FILE may never carry, each with its owner named in the
refusal: `Claim.gates` (derived from gate coverage), `Claim.physical_result` (its home is
`results/<id>.json`), `Param.value` and `Param.derived_from` (the model — `{model}` is
filled with `meta.model_entry`), `Param.gates` and `Param.changed_in` (derived),
`InputArtifact.bytes` (computed from the file), and `"*"`: `independence` anywhere —
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
(`verdicts.freshness`); when the last full check ran is `.atompipe/cache/last_check.json`.
`Ledger.from_dict` ignores the old key, so a ledger an older spine wrote still loads
(R-2), and the next save does not carry it.

`Verdict`, `NegativeControl` and `GateSpec` are records too. Their fields and
properties are listed once, in `docs/PACK_FORMAT.md` ("The surface a gate sees"),
because a pack author is the reader who sets them; two copies of one field list is
one copy that goes stale. `Verdict.outcome` is the single derivation of what a
verdict says — `"error"` if `error`, else `"skipped"` if `skipped`, else `"pass"` if
`passed is True`, else `"fail"` — and `ok`, `render()` and `claims.resolve_status`
all read it; every new consumer (JUnit, the index, the page) calls it and never
re-derives one (PLAN R-5). `GateSpec.requires_one_of` is the last field;
`Verdict.rho` and `Verdict.cpu_s` are Verdict's last two (R-2): the content address the
sweep keys it by (a gate never sets it) and the CPU seconds it cost, children included.

### `util.py`  (no deps)
```python
class AtompipeError(Exception): ...          # user-facing, CLI prints message not traceback
def utcnow_iso() -> str                      # "2026-09-11T14:46:00Z"
def ensure_dir(path) -> str                  # mkdir -p; returns the path as a str
def atomic_write_text(path, text) -> None    # write temp + os.replace; never truncate on crash
def atomic_write_json(path, obj) -> None     # sorted keys, indent=2, trailing newline; NaN refused
def read_json(path, default=None) -> Any
def sha256_text(text: str) -> str
def short_hash(text: str, n: int = 12) -> str
def human_bytes(n: int) -> str
def human_duration(seconds: float) -> str
def rel(path, root) -> str                   # repo-relative posix path, for stable records
def iter_suffix_unique(base, taken) -> str   # base, else base-2, base-3 ... first not taken
class FileLock:                              # build lock; a concurrent run once overwrote a
    def __init__(self, path: str, *, stale_after: float = 3600.0)   # concurrent run
    def __enter__(self) / __exit__(...)      # writes pid; reaps stale locks of dead pids
```
`atomic_write_json` writes **strict JSON**: `allow_nan=False`, and a NaN or ±Infinity
anywhere in `obj` raises `AtompipeError("<path>: refusing to write NaN or Infinity; a
number that could not be measured is not a number")`. What slipped through: the
json default let NaN reach `state.json` and `ledger.json`, `JSON.parse` rejected the
file, and the page advised `site build`, which cannot fix it (S-47). Every writer
above it — the ledger, the page's state, every Phase 1 cache and index file — inherits
the refusal.

```python
class FileDigests:                                   # sha256 of a file's BYTES, behind a stat cache
    def __init__(self, cache_path: str | None = None)   # .atompipe/cache/digests.json (untracked)
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
def ident(root) -> str | None                        # author identity, timestamp dropped
def commit_times(root, relpaths) -> dict[str, str]   # path -> its last commit's time
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
`ident` is `git -c user.useConfigOnly=true var GIT_AUTHOR_IDENT`. **Every function
returns `None` (or `{}`) on any failure and never raises**: a project outside git, a
missing `git`, a timeout. `VCS_TIMEOUT_S` is 10 because `git ls-files` on a cold, large
repository takes seconds; *rejected:* unbounded (a hung credential helper hangs
`check`) and 1–2 s (network filesystems). A `--dir` copy with no `.git` is the
common case (verify.sh), so every caller has a non-git path.

### `store.py`  (deps: models, util)
Persistence: where a project lives on disk. **Never imports `modelio` or `verdicts`**
(spec §3.1): the migration's reader of what the model states is injected.
```python
ATOMPIPE_DIR = ".atompipe"
LEDGER_NAME  = "ledger.json"          # the GENERATED index (records project); the records (legacy)
PROJECT_NAME = "project.json"         # .atompipe/project.json — meta, and the commit marker
LEGACY_LEDGER_NAME = "ledger.legacy.json"   # what a migrated ledger.json is renamed to
PROJECT_SCHEMA = 2                    # project.json's schema; a newer one is refused
RECORD_DIRS = ("claims", "params", "decisions", "needs", "inputs", "results", "views")
VERDICTS_NAME = "verdicts"; CACHE_NAME = "cache"; OBS_NAME = "obs"   # under .atompipe/
INDEX_BANNER: str                     # the index's "generated" value: edit the records, never this
LEGACY_GITIGNORE_TEMPLATES: tuple[str, str]  # init's .atompipe/.gitignore at 1e09113, and at 1.2
INPUT_BUCKETS = ("sketches", "references", "cad", "screenshots",
                 "datasheets", "specs", "measurements", "data")
BUCKET_FOR_KIND: dict[ArtifactKind, str]                # which inputs/ bucket a kind lands in
def find_root(start: str | None = None) -> str | None   # walk up for a MARKER; stop at .git
def require_root(start=None) -> str                     # raises AtompipeError if none
def atompipe_dir(root) -> str                           # <root>/.atompipe
def ledger_path(root) -> str                            # <root>/.atompipe/ledger.json
def inputs_dir(root) -> str                             # inputs/   (NOT hidden - users put files here)
def out_dir(root) -> str                                # .atompipe/out/  gate scratch + evidence
def docs_dir(root) -> str                               # docs/  generated report + decision log
def model_dir(root) -> str                              # model/ the single source of truth
def project_paths(root) -> dict[str, str]              # every well-known path, by name

def read_project(root) -> ProjectMeta                   # .atompipe/project.json, strict
def write_project(root, meta) -> str | None             # the path, or None when unchanged
def read_record(path, kind, *, model_entry="") -> Record | list[PhysicalResult]   # STRICT
def write_record(root, kind, record, *, record_id=None) -> str | None             # None: unchanged
def load(root) -> Ledger                # the records; a legacy ledger migrated IN MEMORY; verdicts []
def save(root, ledger: Ledger) -> None  # tests and the migration only (see below)
def is_legacy(root) -> bool             # ledger.json present, project.json absent
def build_index(root, *, digests: FileDigests | None = None) -> dict   # pure
def write_index(root) -> bool           # True when .atompipe/ledger.json changed; best-effort
def agree(root) -> list[str]            # every way the index disagrees with the records
def records_digest(root) -> str         # sha256 over the record files and project.json
class MigrationPlan(NamedTuple):
    ledger: Ledger; files: dict[str, bytes]; notice: str
def migrate_legacy(root, *, apply: bool, when: str,
                   model_prose: Callable[[str, str], dict] | None = None) -> MigrationPlan
def ensure_ignore_blocks(root) -> list[str]             # the files changed; idempotent
def init(root, meta: ProjectMeta) -> Ledger             # the records layout; refuses only on a marker
```
**A project is where its marker is.** `find_root` walks up from `start` (default: the
cwd), and at each level checks **first** for a marker — `.atompipe/project.json`, or
the legacy `.atompipe/ledger.json` — returning that directory; **then** returns `None`
if the level holds a `.git` entry (a directory, or the *file* a linked worktree or
submodule has); else it walks up. A bare `.atompipe/` directory is not a marker.
What slipped through (S-64): any `.atompipe/` made a project, and `~/.atompipe/` is
the user-pack home, so on a pack author's machine every directory under `~` was
"inside a project" and pack-mode `gate selftest` could never be reached there (nor,
later, Phase 3's Stop-hook fast exit and `/start`'s `init`). The `.git` boundary is
why a nested worktree inside a project resolves to `None` rather than to the trunk's
ledger, while a project that is its own git root is still found (the marker is
checked before the boundary). `require_root`'s message names both markers and the git boundary. `init`
refuses **only when a marker exists**: a directory holding only `.atompipe/packs/`
is not a project, and `init` there succeeds.

`project_paths` is the layout in one call (`"ledger"`, `"project"`, `"legacy_ledger"`,
`"readiness"`, `"decisions"`, `"inputs_<bucket>"`, ...), whether or not the paths exist
yet: this is the map, not an inventory. No other module joins a well-known path by
hand, so moving the layout is one edit here instead of a grep across the spine.

**The layout** (checkpoint 1.3; relative to the project root):
```
.atompipe/project.json     TRACKED  {"schema": 2, name, summary, created, revision,
                                     model_entry, packs, spine_version} — written LAST
.atompipe/.gitignore       TRACKED  the marked deny-list block (below)
.gitignore, .gitattributes TRACKED  marked blocks: bytecode; LF, binary kinds -text
.atompipe/ledger.json      IGNORED  the generated index — an output, never read for truth
.atompipe/verdicts/**      TRACKED  cache and control entries (verdicts.py)
claims/<id>.json           TRACKED  one Claim; the stem IS the id; no "gates"
params/<name>.json         TRACKED  SPARSE: only what the model cannot hold
decisions/<slug>.json      TRACKED  one Decision
needs/<id>.json            TRACKED  SPARSE: only an enriched Need
inputs/<id>.json           TRACKED  one InputArtifact; its bytes stay in inputs/<bucket>/
results/<claim-id>.json    TRACKED  {"results": [PhysicalResult, ...]}, append-only
views/<id>.json            TRACKED  declared views, beside the viewgens views/*.py
```
`init` writes the empty record directories, the input buckets and `inputs/README.md`
(only when missing), `docs/`, `model/`, the three blocks, and `project.json` **last** —
**never a `ledger.json`**: a new project born in the legacy layout would need a migration
on its first command. The record reader and the index read only `views/*.json`; the
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
owner (`model_entry` names the model file); `independence` anywhere. A failing
top-level `inputs/*.json` is most likely stray evidence, and the message names
`atompipe ingest` and the buckets. A results file must be `{"results": [...]}` whose
items say `passed` as a bool. `load` also refuses two record files whose stems differ
only in case. *Rejected:* quarantining a file that does not read as an index `problem` —
a typo'd real record would vanish (S-40's shape). `Record.from_dict` stays lenient.

**`load`** reads the records (each kind in natural id order — C2 before C10 —,
decisions newest first by `when`), sets each claim's in-memory `physical_result` to the
LAST of `results/<id>.json`, each input's `bytes` from its file's size, and `verdicts`
to `[]` (they live in the verdict cache). On a legacy project it is
`migrate_legacy(root, apply=False, when="").ledger`: the same Ledger the migration will
write, so a command answers the same before and after it. Neither marker: an empty
`Ledger`. **No spine code reads `.atompipe/ledger.json` for truth** on a records project.

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
`decisions`, `needs`, `inputs`, `results`, `views`, `unregistered_inputs`, `problems`.
Each record row is its file's content plus its id. Each input row adds `sha256` (the
digest of the bytes NOW, through `util.FileDigests` and the untracked
`.atompipe/cache/digests.json`, read, never written), `pinned` (the record's sha256),
`drift` and `exists` (`null` for an input with no path). `unregistered_inputs` lists
evidence bytes under `inputs/` — not a top-level `*.json`, not `inputs/README.md`, not a
dot-file — that no record's `path` names. `problems`: missing bytes, drift, dangling
`grounded_by`/`claim_ids`/`claims_changed` links, results for a claim with no file. It
**never** holds a status, coverage or a verdict — those sit in `last_check.json`, and an
index that carried them could disagree with them. What slipped through without the
computed digest (S-45): evidence was hashed at ingest and never again, so tampered bytes
read as unchanged. `write_index` rewrites `.atompipe/ledger.json` only when its bytes
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
level (`rejectd` → `.atompipe/ledger.json: param "thickness": unknown key "rejectd" (did
you mean "rejected"?)`, S-40), `independence`, a NaN inside a record, an id that cannot
be a file name or collides (exactly or by case), a `ledger.json` with a `generated` key
and no `project.json`, and record files a crashed migration left that differ from the
plan (both named). Legacy `verdicts` and the old sweep record are dropped unread (D-09).
`apply=True` writes the record files, then `ensure_ignore_blocks`, then `project.json`
**last** (the commit marker: a crash before it leaves a legacy project whose next run
re-derives the same bytes and completes), then renames `ledger.json` to
`ledger.legacy.json` — never deleted. `apply=False` writes nothing; `load` and `doctor`
use it. `MigrationPlan.ledger` is the plan read back through the strict reader;
`files` maps each root-relative path (including `.atompipe/project.json`) to its bytes;
`notice` is one stderr paragraph for the CLI — with `apply`, it ends with
`git rm --cached .atompipe/ledger.json` (the spine runs no git); without, it says the
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

**`ensure_ignore_blocks(root)`** keeps three marked blocks (`# atompipe:begin` …
`# atompipe:end`), each at the top of its file, idempotently:
`.atompipe/.gitignore` — the deny-list `ledger.json ledger.legacy.json obs/ cache/ out/
export/ runs/ *.tmp *.lock` (never an allow-list: `.atompipe/packs/` is source and
`model.json` is reviewed; `verdicts/` is evidence and stays tracked); the root
`.gitignore` — `__pycache__/`, `*.py[cod]`; the root `.gitattributes` —
`* text=auto eol=lf` and `*.stl`, `*.step`, `*.glb`, `*.png`, `*.jpg` `-text`, one
pattern per line. A `.atompipe/.gitignore` that **begins with** a
`LEGACY_GITIGNORE_TEMPLATES` text has that prefix replaced by the block; a line outside
the block equal to `!ledger.json` or `!runs/` is removed with a stderr notice (it would
re-add the index on the next `git add -A`, undoing D-06); a line duplicating a block
line is removed, and a paragraph left holding only the comments that explained such
lines goes with them (the bracket's appended `cache/`/`obs/`); every other user line is
kept, in order, after the block. What slipped through (S-76): `init` wrote an ignore
file that allowed the ledger, and nothing ignored `cache/`.

**No run history** (checkpoint 1.2). `init` makes no `.atompipe/runs/`, and the store
has no run API: every `check` and every `gate selftest` used to append a tracked run
file, so the suite dirtied the tree it verified (S-89), and `<gate>#selftest` rows sat
in the same series as the sweep's with no latency reader filtering them (S-31). Git and
the verdict cache are the history; what runs cost is `.atompipe/obs/`, gate runs and
control runs apart (`verdicts.record_obs`).

### A project's model  (not a spine module: what `modelio.py` loads)
The model contract. A project's model is a **Python module** — the file
`"model_entry"` names in `.atompipe/project.json` — exposing:
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
def write_projection(root, projection) -> str   # .atompipe/model.json  (the diffable view)
def params_from_model(model) -> list[Param]  # merge PARAMS with dataclass fields+defaults
def undocumented_params(model) -> list[str]  # no rationale: the report's nag list
def check_determinism(model, runs=2) -> tuple[bool, str]   # build twice; the projection must not move
```
Rule enforced here: a projection value that is a dataclass/enum is encoded via
`models._enc`. Non-JSON-safe values raise `AtompipeError` naming the field —
silent coercion is how a model and its projection drift apart.

```python
def flat_params(projection) -> tuple[dict, list[str]]   # derived first, config on top; + conflicts
class CodeClosure:                                       # what a loaded module's code IS
    files        # ((abspath, sha256 of the bytes compiled), ...)
    fallback     # "", or why the closure is a whole directory: "computed source at <file>:<line>"
    third_party  # static top-level imports that are not code: installed, or not importable
                 # (not stdlib, not atompipe; a file beside the roots and not installed is code)
    spine_extras # atompipe.* modules it imports that are not in verdicts.SPINE_MODULES
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
def registered_by(fn) -> tuple[ModuleType, ...]          # the modules whose __atompipe_gates__ hold fn
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
`__atompipe_code__`; a later load returns the cached module only while every file in
it still hashes the same, else purges the recorded helpers and re-executes (the gates
it registered are re-adopted into the caller's registry). `load_model` records the
model's closure the same way.

**A gate's code is the module that registered it, too.** `code_closure(fn)` merges the
closure of the module that defines `fn` with that of every module whose
`__atompipe_gates__` holds `fn` (`registered_by`, read from `sys.modules` as it is now),
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
    # The CLI passes it to store.migrate_legacy as model_prose.
class ParamView:                 # one parameter as a reader shows it
    # value, units, rationale, derived_from — from the model (value None when it does not load);
    # rejected — the union of the model's PARAMS and the record's, each tagged with its
    #   origin ("model/bracket.py PARAMS" or "params/<name>.json");
    # source, grounded_by, tags — from the record; model_error — why there is no value
def param_view(ledger, model, *, model_error="") -> list[ParamView]
```
```python
def orphan_params(ledger, model) -> list[str]   # param RECORDS whose field the model no longer defines
```
`param_view` replaces the mutating parameter sync, deleted with its last caller at
checkpoint 1.3 (it copied the model into the records on every check): the value on
screen is the model's current one (S-39), a `PARAMS` rejection added after the params
exist appears (S-38), and a model that does not load shows "model does not load: …"
and no number — never a cached one. `orphan_params` works off the records (a parameter
the model owns entirely has no record, and is no orphan); `model` and `doctor` list
them from it. `_explicit_params` refuses an unknown key in a `PARAMS` dict item with a
`difflib` suggestion (the model-side cousin of S-40).

### `verdicts.py`  (deps: models, util, store, modelio, vcs; gates, packs and claims only inside functions)
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
ABSENT: str                  # sha256(b"atompipe:absent\0") — a key the gate asked for, not there
PRESENT: str                 # sha256(b"atompipe:present\0") — `k in params`, presence only
DIRECTORY: str               # sha256(b"atompipe:directory\0") — a file input that is a directory
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
    ledger: dict[str, str]             # "claim:<id>" | "claims" | "params" | "meta" | ... -> digest
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
    def self_modified(self) -> list[str]   # read, THEN written, in this window
    def stat_existed(self, path) -> bool | None   # what the first question found; None: cannot say

class ParamTrace(dict):
    def __init__(self, data=None, trace=None, *, path=(), readonly=True, host=False)
class LedgerView(Ledger):
    def __init__(self, ledger=None, trace=None, **fields)   # **fields: dataclasses.replace's path
class ModelProxy:
    def __init__(self, target, trace)
class TierRead:                                 # ctx.tier in a gate's view; NOT an int subclass
    def __init__(self, value, trace)            # every use of the value records it on trace
class GateInputWriteError(AtompipeError): ...  # "a gate cannot write another gate's inputs: ctx.params['x']"

def traced_context(ctx, trace, *, readonly=True)   # -> the same dataclass type as ctx
def tracing(trace)                                 # `with tracing(t):` routes audit events, stats and env reads to t
def replay(recorded, trace=None)                   # recorded's files, sources, stats, dirs, opaque, tier -> every open trace and trace
def canonical_ast_digest(source) -> str            # "" when it does not parse
def spine_digest() -> str                          # "" when a SPINE_MODULES source is unreadable
```
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
`views` or `meta` records the whole list. openmodelica reads a claim's limit through
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

**`digest_value`** is sha256 over `atompipe-v1:` + canonical JSON (`sort_keys`, compact,
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
in `~/.local`), the installed atompipe package, `/proc`, `/sys`, `/dev`, or ending
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
CONTROL_OUT_DIR = ".atompipe/out/controls"   # + "/<gate id>/": where a control runs
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
class Entry:                                              # .atompipe/verdicts/<gate>/<rho16>-<out8>.json
    gate: str; rho: str; code: dict; spine: str; reads: dict; instruments: dict
    verdict: dict; digest: str; path: str
    name: str                                             # property: "<rho16>-<out8>"
    def body(self) -> dict;  def to_verdict(self) -> Verdict;  def read_set(self) -> Reads

@dataclass
class ControlEntry:                                       # .../control-<rhoC16>-<out8>.json
    gate: str; rho: str; static: str; static_parts: dict; host: str; fixture: dict
    reads: dict; bad: str; good: None; admitted: str; detail: str
    measured: float | None; limit: float | None; units: str; digest: str; path: str
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
def read_controls(root, gate_id, *, problems=None) -> list[ControlEntry]
def record_control(root, spec, fn, *, result=None, trace=None, host="live", bad=None,
                   detail="", digests=None, anchors=None, when="") -> WriteResult | None
def remember(root, key, verdict, *, input_rho, kind, when) -> None
def remembered(root) -> dict      # {key: {input_rho: {"input_rho", "kind", "verdict": Verdict, "when"}}}
def forget(root, key, input_rhos) -> bool    # only the records at those rhos
def record_obs(root, gate_id, *, entry, when, duration_s, cpu_s, control=False) -> None
def read_obs(root, gate_id, *, control=False) -> list[dict]   # [{"entry", "when", "duration_s", "cpu_s"}]
def last_read_sets(root) -> dict[str, set[tuple]]    # {gate: {param path}}: Param.gates and why, never rho
```
**The code digest.** `code_digest(spec, fn)` is sha256 of canonical JSON of the
closure `modelio` recorded while the gate's module ran — the defining module's merged
with every registering module's (`modelio.code_closure`) — as `{portable path: sha}` (a
project gate's `gates/structural.py`, a pack's `<pack:NAME>/gates/mesh.py`; the bytes
that executed, S-26), the `canonical_ast_digest` of each `atompipe.*` module the
closure imports that `SPINE_MODULES` does not cover (cad and fdm import
`atompipe.site`: a page change re-runs their gates and nobody else's), the
`SPEC_FIELDS_IN_RHO` values, and the closure's `fallback`. Not `title`,
`description` (prose), `negative_control` (it is in `rho_control`'s static part) or
`entry` (discovery). A function with **no recorded closure** — a test's lambda, a gate
registered from Python — is digested as its **defining file**
(`fallback="defining-file"`; `doctor` names every one, and the CLI never makes one).
With no file at all (`<string>`, `exec`) it is `CodeRef(digest="", opaque="code not
loaded from a file")`: opaque, never a digest of nothing. A closure that recorded two
versions of one file, or an unreadable `atompipe.*` source, is opaque too. The sweep
passes its anchors; the default spells a pack gate's files under `<pack:NAME>` and
everything else under `<tmp>`/`~`.

**`Reads.from_trace` — the classification** (first match wins), for every path a
trace read (files and listed directories alike): 1. under the interpreter's prefixes,
site and user site, the atompipe package, `/proc`, `/sys`, `/dev`, or bytecode —
dropped; 2. read and then written in the window — opaque `self-modified:<path>`
(a path written first and read after never reaches the trace: the gate's own output);
3. under a pack — a read `<pack:NAME>/<rel>`; 4. under the sweep's or the control's
`out_dir` and not written in the window — opaque `out:<rel> (not written by this
gate)` (another gate's output is a cross-gate channel shaped like S-27); 5. under
`<root>/.atompipe/` — opaque `atompipe-state:<rel>`; 6. under the root — a read
`<rel>` (bare, posix); 7. anything else — opaque `file-outside-project:<path>`.
A path the trace only ASKED about (`stats`) and neither opened nor listed is a file
input under the root or a pack, like a read; asked while missing and made, asked while
there and removed, or asked and then written in the window, it is opaque
`self-modified:<path>` instead — the state the gate decided on is gone. Everywhere else
a question is not what a read is, because honest gates ask them constantly
(`shutil.which` stats every `PATH` entry, `realpath` every ancestor of the root,
`os.makedirs` every ancestor of the out dir): outside the root and the packs it is
dropped (named residual: a gate deciding on a file outside its project), and under the
out dir or `.atompipe/` a directory, or a path at or above what the window wrote, is
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
(`AtompipeError`) anything its reader would refuse.

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
the OWNER's repository (a bundled pack's is atompipe's), tracked plus
untracked-not-ignored; outside git, a walk. Both leave out `__pycache__/`,
`*.py[cod]` and dot-directories (openmodelica's `.generated/`), so the git and non-git
answers agree — verify.sh `--dir` copies have no `.git` (tests:H11), and a project with
no ignore rule for `selftest/__pycache__` would otherwise key its controls on
bytecode. Where git lists nothing and the walk finds files, the walk wins.

**`record_control`** — the sweep's form passes the `gates.selftest` result and its
trace: fired → `bad: "fail"`, admitted reject-only; the gate PASSED its known-bad
input → `bad: "pass"`, admitted `"no"` (both measurements, cached). A crash on the
fixture, an unusable fixture, a self-skip with the tools present or an availability
skip is `remember`-ed under `control:<gate id>`, keyed by the current `static`, and
returns `None`. The forged form, `record_control(root, spec, fn, bad="fail",
detail=...)` with no result and no trace, writes an entry with empty reads — how a
renderer test plants an admission; it forges only the inner loop (R-9). Writing one
`forget`s the gate's remembered control failure at the entry's `static`, and at no other.

**Remembered outcomes** — `.atompipe/cache/last_outcomes.json` (untracked): `{key:
{input_rho: {"kind", "verdict", "when"}}}`, key a gate id or `control:<gate id>`,
`kind` `"error"`, `"self-skip"` or `"availability"`. Keyed by **`(key, input_rho)`** —
`input_rho` the rho computed from current digests just before the run, at the tier
the run took (the state's `input_rho`: a Fresh entry's, the recomputed rho of the latest
entry's read signature — of the latest one read at that tier when the state's own is
another's — or `""`), or a control's current `static` — never the failing run's own
rho, which a crash at partial reads makes different from the PASS it followed, and
never another tier's entry's, which a crash on this path is not at the inputs of. One record per `(key, input_rho)`,
the newest, except that an `availability` record never replaces an `error` or
`self-skip` one: a check that could not run the gate answered nothing — and a key
keeps one `availability` record, the newest, since it supersedes nothing. A pass or fail
clears only the records at the inputs it was measured at (`forget(root, key,
input_rhos)`): the sweep's `_superseded` — the new entry's rho, `""`, and every rho
current before the run at the tier it took (`here`) — `record_verdict`'s the entry's
rho and `""`, a control entry's its `static`. What slipped through (remembered outcomes, round 1): one record
per gate, forgotten by a pass or fail at ANY rho and overwritten by an availability
skip — a crash at A, then a PASS at B (or a check without the tool), and back at A
`check` read `0 executed, 6 cached` and served the PASS the crash had superseded; the
same for a control crash and a control entry at another `static`. No cap on crash records:
the one a cap evicts is that PASS again. Remembering a pass or a fail is refused:
nothing remembered is evidence. An unparseable file — the one-record-per-gate shape
included — raises, naming it: read as empty it would hand the next check the PASS a
crash superseded.

**Obs** — `.atompipe/obs/<gate>.json` and `<gate>.control.json` (untracked), each
`{"gate", "kind", "runs": [{"entry", "when", "duration_s", "cpu_s"}]}`, the last
`OBS_KEEP`. Split because the run history mixed `<gate>#selftest` rows with sweep rows
and no latency reader filtered them (S-31). A file naming another gate or kind — gate
`x`'s control and a gate called `x.control` share one name — reads as no runs and is
replaced on the next write: never a mixed series.

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
    reason: str           # why not admitted; for pending, the note
    executed: bool        # a fixture or control ran to decide it: never, from admission_state
    reverified: bool
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
def resolve(root, registry, projection, ledger, *, model_error="", availability=None,
            digests=None, anchors=None, now="", model=None) -> Resolution
```
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
the one cause), then the model, claims, files, listings, `gate code changed`, `atompipe
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
self-skip at this static (`control:<gate>`) → `"not-admitted"`, `control <kind>: <why>`
(an availability skip is not held against it). No current candidate →
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
   atompipe check` — `… run atompipe check --tier <t>` when the entry's run took the
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
CONTROLS_CACHE = ".atompipe/cache/controls.json"   # re-verified fixture closures (+ the live
                                                   # ledger a live-host control was vouched under); untracked, a hint
WATCHED = ("claims/**", "params/**", "decisions/**", "needs/**", "inputs/**", "results/**",
           "views/**", ".atompipe/verdicts/**", "model/**", "gates/**", "selftest/**",
           ".atompipe/project.json", ".atompipe/packs/**", "objectives.json")

def known_good_context(root, ctx) -> GateContext | None   # <root>/selftest/known_good.py's context(ctx)
def admission(root, spec, fn, host_ctx, *, may_run=True, force=False, record=True,
              projection=None, digests=None, anchors=None, when="") -> Admission

@dataclass(frozen=True)
class SweepRow:
    verdict: Verdict; executed: bool; cached: bool; fresh: bool
    stale_reason: str; rho: str; admission: Admission | None
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
than the one it was handed (or fills the memo it was handed), reads a file the entry
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
present is remembered under `control:<gate>` at the current static — `not-admitted`,
`control <kind>: <why>` — and a remembered one at this static is re-run, never served
(the control analogue of §3.9's supersede); a control entry written at another static
leaves it standing. A new outcome at the `rho_control` of a
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
and its own emptied scratch, `<root>/.atompipe/out/controls/<gate>/` (never the
sweep's `out_dir`).

**`sweep`** is `check`'s loop, driven through `gates.run_all(..., before=)` so order,
the tier ceiling, `--only` and the lost-control refusal stay in one place; a gate
named above the ceiling runs, control included. `freshness` is computed first. Per
selected gate: 1. *availability* fails — a Fresh FAIL is served (R-3); otherwise
skipped, `cached pass exists; <why> here` over a Fresh PASS (invariant 1), remembered
as `availability` (never over a crash or self-skip at the same rho); no control runs (CI has no trimesh: a skip, never "not admitted").
1b. unless `force`, a remembered crash or self-skip standing over a Fresh entry of a
costlier tier, on that entry's path, with none standing at this sweep's own tier
(`here`): the row is that crash, as `resolve` reads it, with a `note:` naming the path
and `run atompipe check --tier <t>` — nothing runs, since no run at this tier is made at
that path's inputs or answers it. What slipped through (review, remembered outcomes by
tier): the cheap path ran instead, and its PASS was the row — `check` ready while every
reader showed the crash — and forgot the tier-2 crash, so `check --tier 2` then served
the PASS that crash had superseded.
2. *admission* — not admitted: `error="not admitted: <why>"`, `fn` never called. A
Fresh entry of a costlier tier is judged at its own tier by the records alone;
undemonstrated there, the row is what `resolve` serves — the entry's verdict, cached,
`stale_reason` `control not demonstrated at this version — run atompipe check --tier
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
that crash (`… supersedes the entry <name>, and it stands — run atompipe check --tier
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
writes nothing under `.atompipe/` but scratch in `out/`: no entry, control entry,
remembered outcome, obs, `controls.json` or `digests.json` — and since nothing global
is compared, a dry sweep reads nothing stale (S-32). A first sweep filtered by `--only`
goes stale like any other (S-20): there is no clock to not advance. `digests` defaults
to the `.atompipe/cache/digests.json` stat cache, saved after a recorded sweep.
`on_verdict` streams each verdict, `on_row` its row.

**`last_check.json`** (`.atompipe/cache/`, untracked; `write_last_check`, after a FULL
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
    memo: dict | None                                   # the sweep's file memo (run_all); None outside one
    trace: GateTrace | None                             # the trace this view records into (run_gate)
    def scopes(self) -> list[str]                       # ["fdm", "fdm-print"]: key namespaces, best first
    def param(self, name, default=None, *, scope=...) -> Any   # PACK-SCOPED first: see below
    def pack_param(self, name, default=None) -> Any
    def first_pack_param(self, names, default=None) -> Any
    def first_pack_param_named(self, names, default=None) -> tuple[Any, str]
    def require_param(self, name) -> Any                # raises rather than compare with None
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
REGISTRY: Registry                                      # module-level default
def active_registry() -> Registry                       # the registry `@gate` decorates into right now
def use_registry(registry)                              # context manager: `@gate` inside registers there
def gate(*, id, claims=(), tier=Tier.INSTANT, settles="", requires_tools=(),
         requires_python=(), requires_one_of=(), negative_control=None, title="",
         description="", pack="", entry="", registry=None)   # decorator -> registers, returns fn
def availability(spec) -> tuple[bool, str]              # (ok, "requires openfoam (not on PATH)")
def run_gate(spec, fn, ctx, *, trace=None) -> Verdict   # a traced, read-only view; times it (wall, CPU);
                                                        #   catches exceptions -> error verdict
def run_all(registry, ctx, *, max_tier=0, only=None, on_verdict=None,
            before=None, after=None) -> list[Verdict]   # before(spec, fn) -> Verdict | None;
                                                        #   after(spec, fn, verdict, trace)
def selftest(spec, fn, ctx, *, trace=None, out_dir=None) -> Verdict   # runs the NEGATIVE CONTROL, traced
def run_fixture(spec, fn, ctx, *, trace, out_dir) -> GateContext      # the fixture ONLY; never calls fn
def load_fixture(ref: str, root: str) -> Any            # "mod:fn" or "path/to/file.py"
def load_project_gates(root, registry) -> list[str]     # <root>/gates/*.py; the ids they register
def describe(spec) -> str                               # one dense line for `atompipe gate list`
def registry_summary(registry) -> dict                  # JSON-safe: what can run here, and what cannot
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
gate's own shallow copy, `model` a `ModelProxy` (`None` stays `None`), `memo` shared,
`trace` set — and, once `modelio.clear_caches(fn)` has emptied the module-level memos
its code holds (a memo that will not empty is the run's error), `fn` runs inside
`verdicts.tracing(trace)`. `trace=None` makes a
throwaway trace, so the view is read-only on every path, not only in a sweep. It writes
no file, consults no cache and enforces no admission. The window closes before a
crash's traceback is formatted (linecache's reads are the formatter's). What slipped
through: `ctx.params` was one mutable dict shared by every gate, so gate A's
`ctx.params["load_n"] = 0` was gate B's input (S-24); fdm-print's mesh cache on the
shared `extra` made its second gate's read of the part invisible (S-27). `cpu_s` is the
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
gets one `memo={}` when the caller brought none, never left on the caller's context.

**`GateContext.load_file(path, loader=None)`** resolves `path` against `root`, reports
the read as the `open` audit event it stands for — to the view's own trace and every
trace open around it — on EVERY call, hit or miss, and memoises `loader(abspath)` (the
bytes by default) in `memo` on `(abspath, id(loader))` (a bound method by its object and
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
is the fixture half alone — for re-verifying a control whose fixture code moved without
re-running the gate — and raises `AtompipeError` when the control is unusable.

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
or `:` — `.atompipe/verdicts/<gate id>/` holds its cached verdicts — and an id that
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

**`Registry.register` raises `AtompipeError` if `negative_control is None`.**
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
def covers(spec, claim) -> bool                              # claim id OR any claim tag in spec.claims
def covering_verdicts(claim, verdicts) -> list[Verdict]      # the same id-or-tag rule, on verdicts
def resolve_status(claim, verdicts, *, stale: bool = False,
                   stale_gates: Collection[str] = ()) -> ClaimStatus
def explaining_verdict(claim, verdicts) -> Verdict | None    # THE reason: failed > errored > skipped
def statuses(ledger, *, stale=False, registry=None, stale_gates=()) -> dict[str, ClaimStatus]
def coverage(ledger, registry) -> dict[str, list[str]]       # claim id -> LIVE gate ids
def effective_gates(ledger, registry) -> dict[str, list[str]]  # cached `claim.gates` UNION live
def find_gaps(ledger, registry) -> list[Need]                # MEASURABLE claims with no gate
def blocking(ledger, registry, *, stale=False, stale_gates=()) -> list[tuple[Claim, ClaimStatus]]
def summarise(ledger, registry, *, stale=False, stale_gates=()) -> dict   # counts by status, for the CLI
def next_claim_id(ledger, prefix="C") -> str                 # C1, C2, ...
```
`covers` binds by id **or** by tag, and an empty `spec.claims` covers nothing, never
everything: a wildcard would let one misregistered gate mark a project proven.

`explaining_verdict` is the single ranked choice of which verdict explains a claim's
status: one that ran and failed, else one that errored, else one that skipped,
stable within a rank. It is the one place that ranking lives — a caller that names
why a claim has its status asks it, and never ranks verdicts itself. What slipped
through: `status` cited a skipped pack gate as the reason a claim failed while
`check` cited the real failure, because a fix to one caller's private ranking never
reached the other's (S-68).

`blocking` returns **(claim, status) pairs**, not bare claims. The caller is about
to approve an irreversible spend and needs the reason — "C7 blocks" sends them
hunting, "C7 is BLOCKED: no gate ran, the solver is not installed" tells them what
to do. Returning bare claims would also force every caller to re-derive the status
it just discarded, and a second copy of the precedence ladder is a second answer to
the only question this module exists to answer.

`effective_gates` is the **single** definition of which gates cover a claim, and
anything rendering coverage calls it rather than re-deriving one. `coverage` alone
is the live half; the union with the ledger's cached `claim.gates` is what keeps a
gate visible when the pack supplying it is not loaded in this process. A second,
live-only copy of the rule in `report.py` dropped exactly those gates, so the PROVEN
table's **PARTIAL** caveat vanished whenever a pack went missing — the report read
*more* certain the less it could see.

`resolve_status` reads each covering verdict's `outcome` (one derivation, PLAN R-5).
Precedence (deliberate):
ASSUMPTION -> ASSERTED. PHYSICAL -> VERIFIED/REFUTED if a result exists, else UNVERIFIED.
MEASURABLE -> no covering gate: UNCLAIMED; **every** covering verdict skipped (and none
errored): BLOCKED; none run: PENDING; any covering verdict errored **or** failed: FAIL;
all ran and passed and `stale`, or any covering gate in `stale_gates`: STALE; else PASS.
**A skip is never a pass.**

**Staleness is per gate.** `stale_gates` is what `verdicts.resolve` found Stale, Unknown
or undemonstrated; a claim reads STALE when every covering verdict passed and one of its
covering gates is in it, and a stale FAIL stays FAIL (D-08). `stale=True` is the
all-gates alias, kept so `StatusPrecedence.test_stale_is_not_pass` stays byte-identical
(R-6). What it replaced: one flag for the whole project, from one hash of the
projection and one of every input — a comment edit in the model staled every claim, a
model that failed to import staled none (S-21), and ingesting one unread file staled
every measurable claim (S-33). `summarise`'s `stale` is True when any gate is.

An error outranks a skip on purpose: BLOCKED reads "your toolbox is incomplete" and
FAIL reads "something is wrong here". A gate that crashed is not a gate that was
absent — it ran, it was given this project's data, and it came apart on it, which is
the louder signal and may itself be the defect. Folding a crash into BLOCKED would
file it under "install something", which is the one instruction that will not help.

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
def requests_by_kind(ledger, *, project_kind="", limit=6) -> list[tuple[str, str]]  # (kind, prompt)
def suggest_requests(ledger, *, project_kind="") -> list[str]
    # the ASK list, filtered to what is MISSING. This is what makes intake actively
    # solicit files instead of waiting for the user to think of them.
```
`ingest`, `ingest_link` and `add_extraction` write nothing: each returns the
`InputArtifact` the CLI's shim writes as `inputs/<id>.json`, the one record it
touches, and updates the in-memory `ledger` so a command handling several files
dedupes against its own earlier ones. `grounding` inverts the extractions every time
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
PACK_PATH_ENV = "ATOMPIPE_PACK_PATH"         # extra search roots, os.pathsep-separated
BUNDLED_PACKS: str                           # where the shipped packs live (checkout or wheel)
def search_paths(root=None, *, existing_only=True, include_env=True,
                 include_user=True) -> list[str]
    # $ATOMPIPE_PACK_PATH, project .atompipe/packs, ~/.atompipe/packs, bundled packs/
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
def validate(pack_dir, *, tier=Tier.BUILD, notes=None) -> list[str]   # problems; empty = ok
def baseline_context(pack_dir, *, out_dir) -> GateContext  # the ONE sealed baseline context
def demonstrate(pack_dir, *, tier=Tier.BUILD, out_dir=None) -> Demonstration
@dataclass
class Demonstration:                         # what `demonstrate` saw, gate by gate
    problems: list[str]                      # baseline failed, control did not fire, unsealed
    skipped: list[str]                       # availability skips: reported, never a problem
    ran: int                                 # controls actually exercised
def seal_findings(registry, host_ctx, *, tier=Tier.EXTERNAL,
                  out_dir=None) -> list[SealFinding]        # controls whose fixture read the HOST's params
@dataclass(frozen=True)
class SealFinding:                           # one unsealed control (invariant 5, checked at runtime)
    gate: str                                # the gate whose control read the host
    fixture: str                             # its NegativeControl.fixture
    host_paths: tuple[str, ...]              # the host-param paths read, sorted, dotted; "(all params)" = the top level
def match(need: Need, manifests) -> list[PackManifest]      # gap -> candidate packs, by `settles`
def score(need: Need, manifest) -> float                   # 0 = no signal; what `match` ranks by
def installed(root, *, ledger=None) -> list[str]           # the project's opted-in packs, in order:
                                                             # project.json's `packs`, via store.load
def available(root=None) -> list[str]                      # every pack name discovery can see
def key_scope(manifest) -> str                             # "fdm-print" -> "fdm" (from its gate ids)
def key_vocabulary(name, root=None) -> dict[str, dict]     # every projection key the pack reads
def key_collisions(names, root=None, *, projection_keys=()) -> list[KeyCollision]
```
`key_collisions` is what makes two packs wanting one word DETECTABLE rather than
discoverable: it diffs the installed packs' vocabularies (from each
`selftest/baseline.json`'s keys, `_notes` and `_aliases`) and reports any key two
of them declare differently. `atompipe doctor` renders it as a warning naming both
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
`$ATOMPIPE_PACK_PATH` and `~/.atompipe/packs` respectively, so a pack is loaded alone,
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
builds it, `root=pack_dir`, an empty `Ledger`, `extra={}`, the given `out_dir`, tier
`EXTERNAL`. `validate(pack_dir, *, tier=Tier.BUILD, notes=None)` calls `demonstrate`
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

### `decisions.py`  (deps: models, util, store)
```python
def add(ledger, *, title, summary, when, rejected=(), params_changed=(),
        claims_changed=(), body="", evidence=()) -> Decision
def render_log(ledger) -> str                # markdown, NEWEST FIRST
def write_log(root, ledger) -> str           # docs/decisions.md — `report --write`'s output
def why(ledger, name: str, *, view=None, coverage=None, read_sets=None,
        verdicts=None) -> str                # one param or claim: value, rationale,
                                             # rejected alternatives, gates, grounding,
                                             # and the decisions that moved it
def changed_in(ledger, name: str) -> str     # the decision that last moved a param: derived
```
From checkpoint 1.3 (U27) `add` no longer sets `Param.changed_in` — a copy in the param
record would be a second home for "which decision moved it" — and `changed_in` derives
it from the decisions' `params_changed`. `why`'s keywords are all
optional and every default keeps the old rendering: with a `modelio.ParamView` as `view`
it prints the model's value and where it lives (`param thickness = 7.0 mm
(model/bracket.py Config.thickness)`) and each rejection with its origin; a model that
does not load prints `model does not load: …` and no number.
`why` is the context-window win: an agent pulls one parameter's full history
instead of reading a 1,672-line decision log.

### `report.py`  (deps: models, util, store, claims, artifacts, verdicts)
```python
STATUS_TAG: dict[ClaimStatus, str]           # PASS -> "ok   ", FAIL -> "FAIL ", ...
SECTION_PROVEN = "## What is PROVEN"         # the PROVEN heading, as emitted and as tests find it
JUNIT_DEFAULT = ".atompipe/out/junit.xml"    # `--junit` with no path; ignored scratch, never tracked
def status_tag(status) -> str                # "[FAIL ]": the one fixed-width spelling of a status
def render_terminal(ledger, registry, *, stale=False, stale_gates=()) -> str
def render_markdown(ledger, registry, *, stale=False, stale_gates=(), model_error="",
                    title="", root="") -> str
def write_report(root, ledger, registry, *, stale=False, stale_gates=()) -> str   # docs/readiness.md
def render_junit(ledger, verdicts, registry, *, tier, ready, exit_code, when,
                 not_run=None, cached=frozenset(), stale=False, spine="",
                 stale_gates=()) -> str
    # `check --junit`: suites gates / claims.critical / claims.not-critical
def render_selftest_junit(results, *, exit_code, when, baselines=None) -> str
    # `gate selftest --junit`: suite controls, plus baselines in pack mode
def junit_safe(text) -> str                  # XML-1.0-illegal code points -> visible "\xNN" / "\uNNNN"
```
The markdown report has this shape, generated:
**Verdict** (one honest sentence) / **PROVEN** table with evidence per row /
**NOT VERIFIED** list with why / **OPEN GAPS** (Needs) / **STANDING CONSTRAINTS**
(assumptions) / **Reproduce** (the exact commands). It must never call a skipped
or unrun gate "proven".

**Staleness is per gate** (Phase 1.2). The renderers take the resolution's
`stale_gates` (`verdicts.resolve`): a PASS whose covering gate is in it reads STALE and
is never under PROVEN; a stale FAIL stays FAIL (`claims.resolve_status`). `stale=True`
stays the all-gates override. The report reads no sweep time and no rho: its title is
`(<rev>)`, and `## Reproduce` lists `atompipe check` and each gate's code files, so a
regenerated `docs/readiness.md` changes only when the claims or the verdict outcomes
do. The code files are spelled as the verdict cache spells them (`gates/structural.py`,
`<pack:NAME>/gates/…`), which needs the project: `render_markdown` lists them only when
handed `root=` (`write_report` passes it) and otherwise leaves them out, never spelling
them by this machine's absolute paths (S-89). `render_junit` takes `stale_gates` too, so
`check`'s claim suites are judged from the same resolution as its exit code.

The PROVEN section's heading line **starts with `SECTION_PROVEN`** (its qualifier,
"(machine-verified, current)" from Phase 1.2 — a cached verdict is current but not
"this run" — follows on the same line and is not part of the constant). Invariant 4's tests find the section by that constant and fail when it is
absent, duplicated or empty. What slipped through (S-15): they searched for a literal
copy of the heading and read "no heading" as an empty section, so `assertNotIn` passed
on nothing, and a rename would have kept the invariant green while it tested no
report. A rename changes the constant's text, never its name (PLAN D-14, A-11).

`render_terminal`'s one line per unsettled claim is `status_tag(status)`, the claim,
and a reason. Where a verdict explains the status, the reason is
`claims.explaining_verdict`'s gate, formatted `gate : body` (body = the verdict's
`detail`, else `error`, else `skip_reason`; `gate did not pass` when all are empty) —
the same verdict and the same words `atompipe check` prints under BLOCKING. What
slipped through (S-68): `status` cited the first non-passing verdict, a skip, while
`check` cited the gate that ran and failed.

`store` is in the deps for two reasons: `write_report` takes its destination from
`store.project_paths(root)["readiness"]`, and the Reproduce file list anchors its
paths with `store.out_dir(root)`. `verdicts` is in them for `anchors_for` and
`code_digest`, the one spelling of a gate's code files. Layout is `store`'s job alone, and a
second module that knows where `docs/readiness.md` lives is a second module to
edit when it moves.

A PROVEN row whose claim is *also* covered by a gate that produced no proof is
marked **PARTIAL**, names that gate and the reason (`never run`, the skip reason,
`errored`, `failed`), and says how many of the covering gates the row rests on.
Coverage for that check comes from `claims.effective_gates` — the union — so the
caveat survives the pack going missing, which is when it matters most.

**JUnit is never greener than the exit code.** CI renders the XML, not the exit
code, so `render_junit` carries the judgement the exit code was made from and may
only ever be redder. Built with `xml.etree.ElementTree` (imported inside the two
renderers: about 6 ms that only `--junit` should pay). The shape (PLAN §3 row M2.1a,
phase-1.md 1.1):
```
<testsuites name="atompipe check" tests= failures= errors= skipped= time=>
  <properties> spine_version exit_code tier ready when [spine] </properties>
  <testsuite name="gates">            one testcase per REGISTERED gate, registry order
    <testcase classname="project"|"pack.<pack>" name="<gate id>" time="<duration_s>"/>
  <testsuite name="claims.critical">  one per critical claim: "does not block the spend"
  <testsuite name="claims.not-critical">
```
- **`gates`.** A testcase is childless **iff** its verdict's `outcome == "pass"` —
  `Verdict.outcome`, never `passed`, so a skip that also says `passed=True` is a
  `<skipped>` (R-5). fail → `<failure type="fail" message=detail>measured … vs limit
  …</failure>`; error → `<error type="error">`, or `type="not-admitted"` when the error
  **starts with** `not admitted:`; skipped → `<skipped message=skip_reason>`. A
  registered gate with no row in `verdicts` → `<skipped message="not run: <why>">`,
  the why from `not_run` (`(gate, reason)` pairs or a mapping: `above the tier
  ceiling`, `excluded by --only`), else `no verdict in this run`. A gate in `cached`
  has `time="0"` and `<properties><property name="cached" value="true"/></properties>`
  — metadata, not an outcome.
- **`claims.critical`** is recomputed from `ledger` with `stale`, never from
  `verdicts`: its red testcases are exactly `claims.blocking(ledger, registry,
  stale=stale)`, so **failures + errors == `len(blocking())`**, and `check` exits 1
  iff that count is positive. A blocking claim whose FAIL came from a crashed gate
  (its explaining verdict errored) is `<error type="error">`; any other is
  `<failure type="<status>" message="<gate> : <body>">` — the reason `status` and
  `check` print. A PASS that rests on fewer gates than cover it → `<skipped
  message="partial: <gate> <why>; …">`, never childless (invariant 4's PARTIAL);
  UNVERIFIED → `<skipped message="needs a real part">`; ASSERTED → `<skipped
  message="assumed">`. Zero claims → one failing testcase `no claims recorded`.
- **`claims.not-critical`**: FAIL and REFUTED red (the same error-vs-failure rule);
  every other non-pass `<skipped message="<status>: <reason>">`.
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

### `site.py`  (deps: models, util, store, claims, report, modelio, gates, verdicts)
The project site's spine half: viewgens, and the one JSON document the page reads.
The page's half is plain HTML, CSS and ES modules in `site_template/`, copied by
`scaffold`; the data contract between the two is `docs/SITE_CONTRACT.md`.
```python
SITE_DIR = "site"; DATA_DIR = "data"; ASSETS_DIR = "assets"; VENDOR_DIR = "vendor"
VIEWS_DIR = "data/views"                     # per-view payloads too big to inline
STATE_NAME = "state.json"                    # site/data/state.json: everything the page shows
@dataclass
class ViewContext:                           # a viewgen's one argument, mirroring GateContext
    root; ledger; model; params; assets_dir; log; extra; written   # written: assets recorded
    def param(self, name, default=None) -> Any
    def asset_path(self, name) -> str        # where an asset would land; creates nothing
    def write_asset(self, name, data) -> str # writes it; returns the site-relative path for View.src
@dataclass
class ViewSpec:                              # registration record for one viewgen
    id; kind: ViewKind; title; description; requires_python; requires_tools; pack; order; gates
class ViewRegistry:                          # register / unregister / clear / get / specs / ids / pairs
VIEW_REGISTRY: ViewRegistry                  # module-level default
def active_view_registry() -> ViewRegistry   # the registry `@viewgen` decorates into right now
def use_view_registry(registry)              # context manager, as gates.use_registry
def viewgen(*, id, kind, title="", description="", requires_python=(), requires_tools=(),
            pack="", order=100, gates=(), registry=None)   # decorator -> registers, returns fn
def availability(spec: ViewSpec) -> tuple[bool, str]       # as gates.availability, for a viewgen
def run_viewgen(spec, fn, ctx) -> tuple[View | None, str]  # never raises; None is "nothing to draw"
def run_all_viewgens(registry, ctx, *, only=None) -> tuple[list, list]   # (views, notes)
def derive_explode(bounds, *, overrides=None) -> dict      # a FIRST DRAFT explode manifest
def locator_problems(views, verdicts) -> list[dict]         # every locator that cannot be drawn
def scaffold(root, *, force=False) -> list                  # copy the template; index.html only with force
def build(root, ledger, registry, view_registry, *, model=None, projection=None, now="",
          resolution=None) -> dict
def state(root, ledger, registry, *, now="", stale=None, resolution=None) -> dict   # state.json
def judgement_digest(payload) -> str                        # sha256 of what a state document judged
def judgement_moved(shown, now) -> list[str]                # "C1 pass -> fail": what a rebuild would change
def clean_assets(root, keep) -> list                        # delete unreferenced site/assets/ files
def vendor_urls() -> dict                                   # {site-relative path: URL} for `site vendor`
```
**`build` never runs gates**, and never writes the ledger back. It renders a
`verdicts.Resolution` — the caller's `resolution=`, or, when none is handed, one it
resolves for itself with the projection it already builds. *Rejected:* a library
default that lists the recorded entries with no stale gates — a caller that forgot
to pass a resolution would serve every cached PASS as current and skip admission
(invariant 7 by omission). `stale=True` stays an all-stale override. It stamps each
verdict with its own age — obs `when`, else the entry's commit time, else `null`; a
build that re-ran the cheap gates on the way past would publish ten-second-old tier-0
numbers beside week-old tier-2 numbers under one "built at" stamp. `state` borrows every judgement —
statuses from `claims`, the readiness sentence and the PARTIAL logic from `report` —
because a second implementation would give the page and the readiness document two
opinions about one ledger (the site renders the ledger; it never computes truth).
`now` is the caller's timestamp: without it every `age_s` is `null`, never 0, since
an age of zero renders as "just now". `meta.records_digest` is `store.records_digest(root)`
at build time — which records the page was built from; the CLI's site staleness compares
it with the records now (see `cli.py`), never an mtime. `meta.judgement_digest` is
`judgement_digest` of the finished document, set last by `state` itself: a sha256 over
everything but the clock (`built`, each row's `when` and `age_s`), the two digests,
`views` and `locator_problems` — a deny-list, so a key a later `state` adds is judged by
default. It is the other half of what the page was built from: the resolver's judgement
of the verdict cache against the live model, which no record holds (review,
`repro_site`: a `check` that FAILed C1 moved no record, and the page's C1 PASS read
current). `judgement_moved(shown, now)` names what a rebuild would change — claims whose
status moved, else verdict rows whose outcome or measurement did — for the reason line;
it reads both documents and judges neither.

A locator is never dropped for being undrawable: it stays on its verdict, and
`locator_problems` publishes the problem beside it in `state.json`, because a gate
that believes it is drawing and is not looks exactly like a gate that found nothing. `clean_assets` deletes only what
it can prove is unreferenced, which is why every asset arrives through `write_asset`.

### `cli.py`  (deps: everything)
`argparse`, subcommands, `main(argv=None) -> int`, and `build_parser()` — the whole
command surface as one `argparse.ArgumentParser`, so a test can check a command
printed in a document against the parser instead of against memory. `main` catches
`AtompipeError`, prints `error: <msg>` to stderr, and returns 2.
```
atompipe init [--name] [--summary]        atompipe status
atompipe claim list|show|physical         atompipe gap [--propose]
atompipe ingest <path...> [--kind] [--desc]   atompipe inputs [--unextracted]
atompipe extract <artifact> --what ... --grounds ...
atompipe ask [--kind]                     # what evidence to request from the user
atompipe check [--tier N] [--only GATE] [--force] [--no-record]   atompipe gate list|selftest|show
atompipe report [--write]                 atompipe why <param-or-claim>
atompipe decide --title ... --summary ...  atompipe packs [list|show|validate|add]
atompipe model [--write]                  atompipe doctor
atompipe check [--junit [PATH]]
atompipe gate selftest [GATE ...] [--pack NAME|DIR] [--user-packs] [--allow-empty] [--junit [PATH]]
```
Output is terse and machine-parseable by default (one line per verdict);
`--json` on every read command.

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
def _touch_index(root, *, quiet=False) -> None
    # store.write_index on a MIGRATED project, best-effort; re-run while records_digest moves
```
- **The migration's triggers** are `check` and the shims (Q1.5): under the held lock
  and before anything reads the project, `_migrate` runs the legacy migration with
  `apply` — once, with ONE stderr notice ending `git rm --cached
  .atompipe/ledger.json` (the spine runs no git). `check --no-record` runs it in
  memory only and says the project "will migrate … on the next check". Every other
  command reads a legacy project through `store.load`, in memory, and writes nothing
  of it.
- **The shims**, each under the lock with `now` stamped at the edge, each migrating
  first, each writing **exactly one record file** (plus the ignored index):
  `ingest` — the bytes into `inputs/<bucket>/` (the payload, not a record) and
  `inputs/<id>.json`, content-deduped, sha256 pinned (permanent: it moves bytes);
  `extract` — rewrites `inputs/<id>.json`, and nothing it grounds (grounding is
  derived, `artifacts.grounding`); `decide` — `decisions/<slug>.json`, `when` the
  edge's stamp (**no `--when`**: it backdated a decision, S-44), and no
  `docs/decisions.md` (an output of `report --write` whenever a decision exists);
  `packs add` — `.atompipe/project.json`'s `packs`, after loading the pack into a
  fresh `Registry` so a broken one fails before anything is written; `claim physical`
  — APPENDS one `PhysicalResult` to `results/<claim-id>.json` (append-only, D-11;
  `--who`/`--when` stay until P2.5, D-12). Its refusal on a non-physical claim names
  the file edit, `"kind": "physical"` in `claims/<id>.json`.
- **Commands that only mutated a record are gone** (PLAN A-8), and the file edit is
  the command: a claim is written and changed as `claims/<id>.json` (read strictly),
  so `claim` keeps only `list`, `show` and `physical`; a pack leaves the project when
  its name leaves `packs` in `.atompipe/project.json`, so `packs` adds and never
  removes; and the model entry is `"model_entry"` in the same file, so `model` takes
  no flag that sets it. argparse refuses each old spelling (`invalid choice`,
  `unrecognized arguments`), and every message that named one names the file edit
  instead (`_entry_edit`, below); `RemovedNamesAreGone` keeps them out of every
  document an agent reads.
- **`gap` is a read**: no lock, no write (S-43: it persisted every gap it derived,
  rewriting the ledger on every run). A Need is a record, `needs/<id>.json`, only
  when someone enriched it. **`model`** writes no record either: `--write` writes the
  one output it names, `.atompipe/model.json`, and nothing primes the parameter
  records from the model any more. Its orphans (`--json` `orphans`) are param records
  whose field the model no longer defines (`modelio.orphan_params`).
- **The index after every command.** `main` calls `_touch_index` after the command
  returns or is refused (not when interrupted), on a MIGRATED project only — never on
  a legacy one, where `ledger.json` is still the records — and never after `doctor`
  (it writes nothing), `init` (it never writes a `ledger.json`), or a `--no-record`
  run (nothing under `.atompipe/` but scratch). `store.write_index` rewrites the file
  only when its bytes change, so a read command that finds it current writes nothing.
  The rebuild takes no lock, so `status` stays usable while a sweep holds it; a record
  written by another command between this one's build and its write is caught by
  re-reading `records_digest` (up to `_INDEX_ROUNDS = 3`). A record that does not read,
  or a read-only checkout, leaves the index as it was with one stderr line and never
  changes the exit code.

**The readers on records** (checkpoint 1.3, spec U29). Each reads a fact where it
lives, and none writes a record:
```python
def _entry_edit(root) -> str        # '"model_entry" in .atompipe/project.json' (legacy: under
                                    # "meta" in .atompipe/ledger.json, until a check migrates it)
def _param_views(root, ledger, model, model_error) -> list[modelio.ParamView]
    # param_view, plus — when the model does not load — a bare view (no value, model_error
    # set) for each name modelio.static_param_prose finds in the model's TEXT and no record holds
def _grounding(ledger, views) -> dict[str, list[str]]
    # {param or claim: [artifact ids]}: extractions, then records' grounded_by
    # (artifacts.grounding), then PARAMS' grounded_by — derived on every read
def _why_text(root, ledger, registry, model, model_error, view, resolution, name) -> str
    # decisions.why with view=_param_views (grounded_by from _grounding),
    # coverage=claims.effective_gates, read_sets=verdicts.last_read_sets, verdicts=resolved
def _input_bytes(root, artifact, digests) -> dict
    # {"record", "sha256" (now), "pinned", "drift", "exists"} — the index's input-row rule
```
- **`init`** is born migrated: `store.init` writes the records layout, the three
  marked ignore/attribute blocks, and `project.json` last — never a `ledger.json`, so
  `init` then `status` prints no migration notice and leaves no `ledger.legacy.json`
  (what slipped through: every new project was born legacy and migrated by its first
  `check`, with a `git rm --cached` notice about a file git never tracked). Its next
  steps name the file edit that records the model (`"model_entry"` in
  `.atompipe/project.json`); `--json` names `project` (the file it wrote), `meta` and
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
  (`_site_judgement`: `site.state` over `_resolved`'s view — the caller's
  `resolved=(view, registry, resolution)` from `status` and `doctor`, or resolved there
  as `status` resolves, strict=False and `_projection_safe`; no gate, fixture or
  viewgen runs). What slipped through with the records alone (review, `repro_site`): a
  check that FAILed C1 moved no record, and all three called a page still showing C1
  PASS "current with the records". A moved judgement reads "the verdicts have changed
  since the site was built (C1 pass -> fail) — `atompipe site build`"
  (`site.judgement_moved`, at most `verdicts.MAX_STALE_REASONS` named); `status`'s
  `site:` line carries that reason, never a fixed sentence. A page with either digest
  missing (an older build) reads stale.
- **`last_check.json`'s `params`** is `{name: modelio.ParamView.to_dict()}` from
  `modelio.param_view` at the end of a recorded full `check`: the model's values and
  what lost, beside the statuses — the agent's second read.

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
  pack directly inside it), else every **bundled** pack. `$ATOMPIPE_PACK_PATH` and
  `~/.atompipe/packs` are searched only under `--user-packs`: both outrank the
  bundled packs, so without the switch the machine would choose which copy the merge
  check tests (S-87). Inside a project, `--pack NAME` also searches its
  `.atompipe/packs/`. Positional gate ids filter, by `gates._selected`'s rule, in
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
  An UNCLAIMED claim reads `no gate covers it` and stops there (the `gap --propose`
  suffix was advice in a column that states facts); a STALE one names its stale
  gates and why.
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
- `--no-record` is a dry sweep: nothing under `.atompipe/` but gate scratch in
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
  second check on unchanged inputs changes no byte outside `.atompipe/{out,cache,obs}/`.

`check --json` (§3.13; verify.sh and CI parse `verdicts[]`): `verdicts[]` holds a row
for **every selected gate** — executed, cached or refused — in registration order, so
a fresh clone whose first check is all cache hits still lists `bracket.deflection`
(cli:H1). Row keys `gate, passed, skipped, ok` (stable); `outcome, cached, fresh`
(always); `rho`, `stale_reason` (when non-empty); `duration_s, cpu_s` (executed rows
only). `carried_over[]` holds the rows (with `cached`, `fresh`, `stale_reason`) of
registered gates this sweep did not select that have an effective verdict. `counts` =
`{ran (#ok over executed plus cached), failed, skipped, errored, executed, cached,
controls: {executed, cached, reverified}}`. `stale` means some selected gate was stale
BEFORE the sweep (cli:H19), `stale_reason` names each; `run` is `null` (no run history;
kept so a reader finds the key and nothing in it); `model_hash` is a display id; adds
`spine`, `junit` and `notes` (`SweepResult.notes`, each once: the writer's warnings and
ignored entries).

`check` text: executed rows stream as they land; a cached row prints only when it did
not pass, as `f"{line:<77} cached"`; then
`6 gates: 1 executed, 5 cached — 5 ok, 1 FAIL — tier 0` (`, S skipped` and
`, R errored` when non-zero; the time and the model hash left this line), then
`controls: E executed, C cached, R re-verified` when a control executed or was
re-verified, a `note: <note>` line per `SweepResult.notes` entry (the writer's
`two outcomes recorded for identical inputs` among them — collected and printed nowhere
until the review), the skip digest, a note for gates outside the sweep, and the BLOCKING
list.

**`status`** text: `render_terminal`'s block, then in this order — `stale: <gate> —
<reasons>` per stale gate (continuations indented under `stale: `) with `   (N checks
current[, n never run])` on the last, or `stale: none   (N checks current)`; `last
check: <when> (<age> ago)` from `last_check.json`, or `last check: never`; `note:` per
instrument mismatch and at most one `note: <k> control(s) pending — inputs moved
(<files>); the next check re-verifies`; `model: <entry> DOES NOT LOAD — <error>` only
when it does not load. A check is current when its row is Fresh with its control
admitted or pending. `status --json` drops the sweep record and its age, keeps
`stale`/`stale_reason`, and adds `stale_gates`, `freshness` (`{gate: {"state",
"reasons", "admission", "notes"}}`) and `last_check` (`{"when", "age_s"}`, nulls
before the first). It never runs a gate or a fixture and never writes.

**`gate show`**: `last verdict` from the view (with `(stale: <why>)` when it is not
current); the last line is the control's standing at this version, read by
`verdicts.admission_state` (which runs nothing): `  last selftest: [ok  ] fired at this
version (control <rho12>)`, `… [FAIL] PASSED its own known-bad at this version (control
<rho12>)`, `… pending — control inputs moved; the next check re-verifies (control
<rho12>)`, `… [FAIL] not admitted at this version — <why>` (a remembered crash, two
disagreeing controls), or `… not demonstrated at this version`. JSON `last_selftest` =
`{"outcome", "control", "at_this_version", "admission", "detail"}` or `null`. What
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
| `pending-controls` | warn | controls whose fixture code moved (`<k> control(s) pending — inputs moved (<files>); the next check re-verifies`, the sentence `status`'s note prints) |
| `imports` | warn | a gate that imports a third-party module (`CodeRef.third_party`) it does not declare in `requires_python` or a `python:` entry of `requires_one_of`: where it is missing, the gate errors instead of reading SKIPPED. In the gate's own file, read by reach — module-level imports plus those inside the gate function and the module-level functions and classes it names, transitively; other closure files whole. A whole-file rule named `cad.bounding`, the one tier-0 gate of a module whose other gates import trimesh lazily |
| `env-reads` | warn | the static env-read detector (spec §3.17): an AST scan of every registered gate's closure files for `os.environ`, `os.getenv`, `os.putenv` (and the bytes twins), through any alias of `os` or a `from os import`. rho never keys a variable (§8): a read inside a window is named opaque (`env:<NAME>`) and costs a re-run on every check, and a module-level read made at import, before any window, is seen by nothing else |
| `memos` | warn | the static memo detector: an AST scan of every registered gate's closure files for a module-global memo `modelio.clear_caches` cannot empty — a module-level container (a `{}`, `dict()`, `defaultdict` and the like) written into from a function body that does not bind the name itself, a module global a function rebinds under `global`, a mutable default argument its function writes into. The first gate to fill one opens the file; every later one opens nothing, and no entry keys it (review round 2) |
| `dynamic-imports` | warn | the static dynamic-import detector (`modelio.dynamic_imports`): an AST scan of every registered gate's closure files and of its owner's `selftest/*.py` (the fixtures and the known-good module its control runs) for `importlib.import_module(...)` or `__import__(...)` whose module a VALUE names. Only the run that first loads such a module in a process keys its source; a later one is served it from `sys.modules` and keys nothing. A literal name is resolved into the code closure and is not named (admission review, round 2, c) |
| `sealed-fixtures` | FAIL | invariant 5 at runtime: every pack control run against this project's params through `packs.seal_findings`, into a temp `out_dir`; a control that reads its host is named with the paths it read |

The four static detectors measured zero hits on the 54 bundled gates before they landed
(R-4; `tests/test_doctor.py`). No staleness row: which gates are current is `status`'s
`stale:` block. No run-history row: there is none to read (S-31).

## Limits: what the spine cannot see, named

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
  keyed: the `atompipe.*` modules outside the spine digest that such a helper imports.
- **`ctx.model is None` is not recorded.** A gate that branches on whether a model is
  loaded at all is invisible to rho on that branch; `ModelProxy` records a real use of
  the model, never its absence. No bundled gate reads `ctx.model`.
- **Admission outside `check` is static.** `status`, `report` and `site build` judge a
  control from its entry's static part, its recorded files and host reads, and its
  fixture closure's digests — none of them runs a fixture. When a fixture's code moved
  they count the control **pending**, with a note (`<k> control(s) pending — inputs
  moved …`), until the next `check`, `gate selftest` or `check --force` re-verifies it
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
- **Spines older than this layout are not guarded.** An atompipe from before the
  records layout reads `.atompipe/ledger.json` as the records and may rewrite it;
  nothing written now can stop it. `project.json`'s `schema` refuses NEWER layouts,
  going forward only. The README says so.
- **The tracked cache is forgeable in the inner loop.** An entry's `digest` is
  integrity (a hand edit is ignored as `hand-edited entry`), not authentication:
  a hand-made entry with a matching digest is served until something re-runs it. R-9
  is the defence — `check --force` in CI, and P2's `export` — never the cache.
- **A concurrent writer during a gate run is not detected.** File digests are taken
  after the gate returns, so a file rewritten while the gate read it keys the rewrite.
- **Smaller edges, named in their sections:** an explicit `dict.__getitem__` call on
  `ctx.params` is not recorded, and a mutable non-JSON leaf is handed out by reference
  (`ParamTrace`); an `os.open` or `os.stat` relative to a `dir_fd` is not resolved,
  and a name bound to `os.stat` before the spine was imported is not probed (the audit
  hook, the stat probes); an existence question outside the project is not an input,
  and a decision on an mtime is not keyed (`Reads.from_trace`); a subprocess's own
  reads are opaque, never covered.
