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

## Module map and public surface

Every `src/atompipe/*.py` except `__init__` and `__main__` has a heading below, and
every name in its `__all__` is written in code form in that module's section.
`tests/test_contracts.py` holds that (PLAN R-14): a module with no heading, or an
exported name nobody wrote down, turns it red. `models.py` and `site.py` had no
heading at all until it landed, and 42 exported names were in no section — a map of
a smaller spine than the one an agent was about to edit. During Phase 1 the check
runs one way (code ⊆ docs), so a wave's doc owner may document a surface before the
unit that builds it merges.

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
class Ledger(Record):           # the whole project state: .atompipe/ledger.json
    meta: ProjectMeta; claims: list[Claim]; params: list[Param]
    inputs: list[InputArtifact]; needs: list[Need]; decisions: list[Decision]
    verdicts: list[Verdict]                                              # latest per gate
    views: list[View]
    def claim(cid) | param(name) | artifact(aid) | need(nid) | verdict(gate_id) -> record | None
    def verdicts_for(cid) -> list[Verdict];  def upsert_verdict(verdict) -> None
```
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
Persistence. The project lives in `<root>/.atompipe/`.
```python
ATOMPIPE_DIR = ".atompipe"
LEDGER_NAME  = "ledger.json"
INPUT_BUCKETS = ("sketches", "references", "cad", "screenshots",
                 "datasheets", "specs", "measurements", "data")
BUCKET_FOR_KIND: dict[ArtifactKind, str]                # which inputs/ bucket a kind lands in
def find_root(start: str | None = None) -> str | None   # walk up for a MARKER; stop at .git
def require_root(start=None) -> str                     # raises AtompipeError if none
def atompipe_dir(root) -> str                           # <root>/.atompipe
def ledger_path(root) -> str
def load(root) -> Ledger                                # missing file -> empty Ledger
def save(root, ledger: Ledger) -> None                  # atomic
def init(root, meta: ProjectMeta) -> Ledger             # creates dirs, refuses only on a marker
def inputs_dir(root) -> str                             # inputs/   (NOT hidden - users put files here)
def out_dir(root) -> str                                # .atompipe/out/  gate scratch + evidence
def docs_dir(root) -> str                               # docs/  generated report + decision log
def model_dir(root) -> str                              # model/ the single source of truth
def project_paths(root) -> dict[str, str]              # every well-known path, by name
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

`project_paths` is the layout in one call (`"ledger"`, `"readiness"`, `"decisions"`,
`"inputs_<bucket>"`, ...), whether or not the paths exist yet: this is the map, not an
inventory. No other module joins a well-known path by hand, so moving the layout is
one edit here instead of a grep across the spine.
Layout created by `init`:
```
.atompipe/ledger.json   .atompipe/.gitignore   .atompipe/out/
inputs/{sketches,references,cad,screenshots,datasheets,specs,measurements,data}/
docs/   model/
```
**No run history** (checkpoint 1.2). `init` makes no `.atompipe/runs/`, and the store
has no run API: every `check` and every `gate selftest` used to append a tracked run
file, so the suite dirtied the tree it verified (S-89), and `<gate>#selftest` rows sat
in the same series as the sweep's with no latency reader filtering them (S-31). Git and
the verdict cache are the history; what runs cost is `.atompipe/obs/`, gate runs and
control runs apart (`verdicts.record_obs`).

`.atompipe/.gitignore` as `init` writes it ignores `out/`, `*.tmp`, `*.lock`,
**`cache/` and `obs/`** — this checkout's memory (file digests, `last_check.json`,
remembered outcomes, re-verified controls) and what each run cost — and keeps
`!ledger.json`. What slipped through (cli:H2, S-76): 1.2 began writing both and nothing
ignored them, so the first `check` in a clean clone dirtied `git status`. The verdict
cache, `.atompipe/verdicts/`, is evidence and stays tracked. The 1.3 migration
recognises this text, like the 1e09113 template before it, as a prefix it replaces with
a marked block; the bracket's tracked file keeps the 1e09113 template and APPENDS the
two lines, so that recognition still holds for it.

### `modelio.py`  (deps: models, util, store)
The model contract. A project's model is a **Python module** exposing:
```python
CONFIG: dataclass instance          # or  Config: type  +  CONFIG = Config()
def build(config) -> dict           # the resolved geometry/state; pure, deterministic
PARAMS: list[Param] = [...]         # optional explicit provenance; else inferred from fields
```
```python
@dataclass
class LoadedModel:
    module: Any; config: Any; entry: str; params: list[Param]
    file -> str; directory -> str                # properties: the file executed, and its dir
def load_model(root, entry: str | None = None) -> LoadedModel   # entry from ledger.meta
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
    third_party  # static top-level imports resolving outside the roots (not stdlib, not atompipe)
    spine_extras # atompipe.* modules it imports that are not in verdicts.SPINE_MODULES
def load_source_module(path, *, name, roots, registry=None, attrs=None) -> ModuleType
                         # fresh bytes, recorded closure, content-keyed; registry: where its
                         # gates are recorded and re-adopted (the caller also wraps the call in
                         # gates.use_registry — modelio cannot import gates); attrs: globals set
                         # before it runs (a pack's PACK, PACK_DIR), part of the cache key
def load_path(path) -> ModuleType                        # a helper by path; module name salted by its abspath
def code_closure(obj) -> CodeClosure | None              # a module's, or a function's module's
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
executes, a recording finder attributes every import under `roots` to it; a walk of its
globals attributes helpers found already in `sys.modules`; a static pass adds lazy
imports inside function bodies. Only `exec` of computed source from a frame under
`roots`, or an import that cannot be mapped to a file, falls back to every `*.py` under
the owning directory, and `fallback` says so. The closure is stored on the module as
`__atompipe_code__`; a later load returns the cached module only while every file in
it still hashes the same, else purges the recorded helpers and re-executes (the gates
it registered are re-adopted into the caller's registry). `load_model` records the
model's closure the same way.

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
    opaque: set[str]                   # "subprocess:omc", "network", "param mesh: <Type> is not JSON"
    model_used: bool
    host_reads: dict[tuple, str]       # what a control read from the HOST context
    fixture_code: Any                  # the fixture's CodeClosure, set by gates.selftest
    anchors: Anchors | None            # makes path-valued param digests portable
    def self_modified(self) -> list[str]   # read, THEN written, in this window

class ParamTrace(dict):
    def __init__(self, data=None, trace=None, *, path=(), readonly=True, host=False)
class LedgerView(Ledger):
    def __init__(self, ledger=None, trace=None, **fields)   # **fields: dataclasses.replace's path
class ModelProxy:
    def __init__(self, target, trace)
class GateInputWriteError(AtompipeError): ...  # "a gate cannot write another gate's inputs: ctx.params['x']"

def traced_context(ctx, trace, *, readonly=True)   # -> the same dataclass type as ctx
def tracing(trace)                                 # `with tracing(t):` routes audit events to t
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
and `f(**p)` unless `tp_iter` is overridden, and those were exactly the silent reads
(S-25: `_ParamReads` recorded nothing for bulk access). Copies are plain dicts. Every
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
`None`.

**`digest_value`** is sha256 over `atompipe-v1:` + canonical JSON (`sort_keys`, compact,
`ensure_ascii=False`, `allow_nan=False`) of a tagged form: NaN and the infinities
become `{"$float": ...}` (the bracket's `build()` returns `inf`); a user key starting
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
`subprocess.Popen`, `os.system`, `os.exec*`, `os.spawn*`, `os.posix_spawn` and `os.fork`
as opaque `subprocess:<name>`, plus any argv element naming an existing file as a read;
`socket.connect`/`sendto`/`sendmsg` as opaque `network`. A read of a path this window
already wrote is not recorded (the gate's own output); a path read and then written is
listed by `self_modified()`. Excluded: events whose calling frame is import machinery,
`linecache`, `tokenize`, `warnings` or `traceback` (the first mesh gate opened 719
`.pyc` files on import alone); pseudo-filenames like `<unknown>` (3.13's traceback
parses line fragments for its carets, and the SyntaxError opens `<unknown>`); and paths
under the interpreter's prefixes and library directories, site-packages, the USER site
(trimesh and numpy live in `~/.local`), the installed atompipe package, `/proc`, `/sys`,
`/dev`, or ending `.pyc` — machine-specific reads that would make every entry stale on
every other machine. Not seen, and named: `os.stat` and every environment read fire no
event; a subprocess's own reads (hence opaque); an `os.open` relative to a `dir_fd`.
What slipped through while writing it: `sys._getframe` raises an audit event of its
own, so the hook re-entered itself until the recursion limit and aborted the `open` it
was auditing — it now carries a per-thread re-entrancy guard.

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
TWO_OUTCOMES_IS_ERROR = False # a warning (the gate reads stale) until U25 flips it

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
    files: dict           # {portable path: sha256 | None}   None: missing, itself an input
    dirs: dict            # {portable path: digest of sorted entry names | None}
    ledger: dict          # {"claim:<id>" | "claims" | ...: digest}
    model: str | None     # model_digest, when the gate used ctx.model
    opaque: list          # sorted channel names: an entry with any is never Fresh
    host: list            # a control's host-param reads: [[path, digest], ...]
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
def remembered(root) -> dict      # {key: {"input_rho", "kind", "verdict": Verdict, "when"}}
def forget(root, key) -> bool
def record_obs(root, gate_id, *, entry, when, duration_s, cpu_s, control=False) -> None
def read_obs(root, gate_id, *, control=False) -> list[dict]   # [{"entry", "when", "duration_s", "cpu_s"}]
def last_read_sets(root) -> dict[str, set[tuple]]    # {gate: {param path}}: Param.gates and why, never rho
```
**The code digest.** `code_digest(spec, fn)` is sha256 of canonical JSON of the
closure `modelio` recorded while the gate's module ran (`{portable path: sha}` — a
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
`<rel>` (bare, posix); 7. anything else — opaque `file-outside-project:<path>`. In a
**control** trace a read under the owner's `selftest/` is dropped: the static walk
keys it, and a fixture module's import-time read of `baseline.json` happens only on
its first load in a process. `static=` (the files the walk covered) narrows that to
exactly those files. Files are digested after the gate returns, through `digests`
(`util.FileDigests`); a listing is the digest of its sorted entry names without
`__pycache__` or bytecode. `anchors` defaults to `trace.anchors`, else to `<tmp>`/`~`
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
"opaque"}`. Canonical JSON everywhere in this module: `sort_keys`, compact separators,
`ensure_ascii=False`, `allow_nan=False`, no salt — anyone can recompute any digest
from this rule. Display values, instruments and a prerequisite's outcome are never in
it. **`out8`** is the first 8 hex of sha256 of `[passed, measured, limit, units]`: the
outcome, never the text.

**The entry file** is written once (`O_EXCL` semantics: the bytes go to a `*.tmp` file
in the same directory, which is hard-linked to the final name — `link(2)` fails when
the name exists, and the name only ever holds complete bytes; a filesystem without
hard links gets a plain `O_CREAT|O_EXCL` write). Keys in this order: `schema, gate,
rho, code, spine, reads, instruments, verdict, digest`; `reads` keys `params, files,
dirs, ledger, model, opaque`; bytes `json.dumps(indent=2, ensure_ascii=False,
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
`instruments_for`; writes the entry; and `forget`s the gate's remembered outcome.

**`instruments_for`** — provenance, never rho (Q1.3): for each module in
`requires_python`, the `python:` entries of `requires_one_of`, and the closure's
**static** third-party imports (`CodeRef.third_party`), its `importlib.metadata`
version, `"unknown"` when importable without metadata, `"absent"` when not importable.
It never imports anything. Never from `import` audit events: those fire once per
process, so only the first mesh gate saw trimesh, and `--only` and a full sweep wrote
different bytes for one rho (packs:H3).

**Control entries** — `control-<rhoC16>-<out8>.json`, written once, keys: `schema,
kind ("control"), gate, rho, static, static_parts {spine, code {digest, files},
selftest {digest, files}, nc {fixture, expect, note}}, host ("known-good" | "live"),
fixture {digest, files}, reads {params, files, dirs, ledger, host, opaque}, bad
("fail" | "pass"), good (null until P2), admitted ("reject-only" | "no"), detail,
measured, limit, units, digest`. `static` (`control_static`) = sha256 of the spine
digest, the gate's code digest, the owner's `selftest_walk` and the NegativeControl
fields. **`rho_control`** = sha256 of `{"schema", "gate", "static", "reads"}`. The
`fixture` block — the fixture's recorded code closure — is a lookup **hint, not an
input**: the bracket's fixtures import the model, so keyed on it every Config edit
would write six tracked control files. Bytes that differ only in `fixture` are the
same control (`write_control` answers `"exists"`, no warning). Host-param reads are
keyed only when the host was live (a known-good host is a design the fixture's own
`selftest/` files define).

**`selftest_walk(owner_dir)`** — the owner is the pack directory (`PACK_DIR` on the
gate's module) or the project root. `vcs.ls_files(owner_dir, ["selftest"])`, asked of
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
`forget`s the gate's remembered control failure.

**Remembered outcomes** — `.atompipe/cache/last_outcomes.json` (untracked): `{key:
{"input_rho", "kind", "verdict", "when"}}`, key a gate id or `control:<gate id>`,
`kind` `"error"`, `"self-skip"` or `"availability"`. Keyed by **`input_rho`** — the rho
computed from current digests just before the run (a Fresh entry's, the recomputed
rho of the latest entry's read signature, or `""`), or a control's current `static` —
never the failing run's own rho, which a crash at partial reads makes different from
the PASS it followed. Remembering a pass or a fail is refused: nothing remembered is
evidence. An unparseable file raises, naming it: read as empty it would hand the next
check the PASS a crash superseded.

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
class Fresh:   entry: Entry; notes: tuple; rho: str; current: frozenset       # state = "fresh"
@dataclass(frozen=True)
class Stale:   entry: Entry; reasons: tuple; rho: str; current: frozenset;
               conflict: tuple                                                # state = "stale"
@dataclass(frozen=True)
class Unknown: entry: Entry | None; reason: str; rho: str; current: frozenset # state = "unknown"
@dataclass(frozen=True)
class Never:   ...                    # state = "never"; entry None, rho "", current frozenset()

def freshness(root, registry, projection, ledger, *, digests=None, anchors=None,
              model=None) -> dict[str, Fresh | Stale | Unknown | Never]   # every registered gate

@dataclass(frozen=True)
class Admission:
    state: str            # "admitted" | "pending" | "not-admitted" | "undemonstrated"
    entry: ControlEntry | None
    reason: str           # why not admitted; for pending, the note
    executed: bool        # a fixture or control ran to decide it: never, from admission_state
    reverified: bool
def admission_state(root, spec, fn, *, projection, digests=None, anchors=None) -> Admission

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
is **Fresh** — its `rho` is what the sweep keys a remembered crash under — and an
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

**`admission_state`** — is the gate's control demonstrated at its current version, from
records alone (only `check` may spend a fixture's time). A candidate is a control entry
whose `static` equals the current one (`control_static`); it is *current* when it has
no opaque channel, its file and listing digests are current, and — when its fixture got
the LIVE host — its host-param reads match `projection`'s flat params. Among current
candidates, those whose fixture closure (`fixture.files`, the lookup hint) is unchanged
decide first: all fired → `"admitted"`; one PASSED its known-bad input →
`"not-admitted"`, `PASSED its own known-bad fixture <ref>`; they disagree →
`"not-admitted"`, `two control outcomes recorded for identical inputs`. With no
unchanged-closure candidate, the current ones decide the same way, but a fired control
is `"pending"` — `control inputs moved (<files>); the next check re-verifies` — and
counts: the bracket's fixtures load its model, so a `bed_xy` edit moves every closure
without moving a control value. A remembered control crash, unusable fixture or
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
   the entry's rho) or, with none Fresh, is displayed (`input_rho` `""`, or a rho
   recomputable now, or the gate has no entry at all — never "never run", S-68).
   Invariant 2: a crash proves nothing, and neither does the PASS it followed.
3. A Fresh entry under admission (PD-08, X14): PASS + admitted or pending counts;
   PASS + undemonstrated is stale, `control not demonstrated at this version — run
   atompipe check`; not admitted is an error `not admitted: <why>`; a FAIL stays FAIL
   (undemonstrated, it is also listed stale) — admission gates what may COUNT as a pass.
4. The latest entry, stale with its reasons, or Unknown with its reason (`model_error`
   joins "the model does not load"). Two outcomes: stale, an error once
   `TWO_OUTCOMES_IS_ERROR` is True.
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
CONTROLS_CACHE = ".atompipe/cache/controls.json"   # re-verified fixture closures; untracked, a hint
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
    notes: list[str]                # writer warnings, ignored entries
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
fixture closure is unchanged, or was re-verified under the closure as it is now
(`CONTROLS_CACHE`), settle it with nothing run — all fired: `admitted`; the latest
PASSED its known-bad input: `not-admitted`, `PASSED its own known-bad fixture <ref>`;
they disagree: `not-admitted`, `two control outcomes recorded for identical inputs`.
4. *Re-verify* (`may_run`): otherwise the fixture runs ALONE (`gates.run_fixture`),
traced, in the control `out_dir`, and what it built is compared with each current
candidate's recorded reads — params by digest (`ABSENT` for a miss), ledger reads. A
match settles it as 3 does with `reverified=True`: the gate is not called and no
tracked file is written; `controls.json` remembers the closure. The comparison is
sound only for what an entry keys, so a fixture that writes files, moves `ctx.root`,
`out_dir` or `tier`, hands its gate `ctx.extra`, reads a file the entry does not
key, or touches an opaque channel sends the control to a full run instead — a cost,
never a wrong admission (what would have slipped through: a fixture writing the
known-bad mesh its gate reads, which the gate's trace drops as its own output). This
is the early cutoff: a Config-default edit moves every bracket fixture's closure (they
build through the model) but no control value — six fixture runs, zero controls
executed, zero new files — while a `build()` edit that moves a control input misses
and re-runs it, and one that defuses it is not admitted (S-19's model-code half).
5. *Miss*: the control runs, fixture and gate, and is filed unless `record=False`:
`bad: "fail"` admitted reject-only; `bad: "pass"` not admitted. A crash, an unusable
fixture (a `known_good.py` that will not load included) or a self-skip with the tools
present is remembered under `control:<gate>` at the current static — `not-admitted`,
`control <kind>: <why>` — and a remembered one at this static is re-run, never served
(the control analogue of §3.9's supersede). A new outcome at the `rho_control` of a
cached entry with the other `bad` is `not-admitted`. 6. *`force`* skips 1-4, and an
outcome that differs from a cached entry at the same `rho_control` is `not-admitted`,
`control outcome differs from its cached entry` (R-9). With the gate's tools missing
nothing runs: the records answer.

**The known-good host (D-27, S-07).** A PROJECT gate's fixture is handed
`known_good_context(root, host)` — `selftest/known_good.py`'s `context(ctx)`, loaded
through `modelio.load_source_module`, called on a copy — and the entry says `host:
"known-good"` (its host reads are not keyed: they are reads of a design the owner's
`selftest/` defines). A pack's fixture gets the live host (`host: "live"`, host reads
keyed); SEALED is the seal detector's job. So the literal identity fixture `return ctx`
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
as `availability`; no control runs (CI has no trimesh: a skip, never "not admitted").
2. *admission* — not admitted: `error="not admitted: <why>"`, `fn` never called. 3.
unless `force`, a *Fresh* entry is served — unless a remembered crash or self-skip at
its rho superseded it (§3.9), which re-runs the gate. 4. *run*, traced with the sweep's
anchors: a pass or fail is keyed and cached (clearing any remembered outcome), anything
else remembered under the rho computed before the run; every run appends obs. The
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
`file:<path>`, `dir:<path>`, `ledger:<key>`, `model`, `opaque:<channel>`),
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
    def load_file(self, path, loader=None) -> Any       # once per sweep; a read of THIS gate, hit or miss
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
`trace` set — and `fn` runs inside `verdicts.tracing(trace)`. `trace=None` makes a
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
function), revalidated by the file's stat signature. No memo: it just loads (a hand-run
check script, packs:H15).

**Controls, traced.** `selftest(spec, fn, ctx, *, trace=None, out_dir=None)`: the
fixture gets a WRITABLE traced copy of `ctx` (`out_dir` replaced when given) — its
writes never reach the sweep, its reads of the host land in `trace.host_reads` — and
`make` runs inside `tracing(trace)`; `trace.fixture_code` is
`modelio.code_closure(make)`; the gate then runs through `run_gate` with the same trace.
`duration_s` and `cpu_s` cover both. `run_fixture(spec, fn, ctx, *, trace, out_dir)`
is the fixture half alone — for re-verifying a control whose fixture code moved without
re-running the gate — and raises `AtompipeError` when the control is unusable.

**Code is loaded fresh.** `load_project_gates`, the path form of `load_fixture` and
`packs.load_gates` all go through `modelio.load_source_module`: the bytes that run are
the bytes on disk, the code closure is recorded, and a cached module is served only
while its closure still hashes the same — re-adopting its gates into the caller's
registry. What slipped through: after a same-size, same-second edit, gate modules and
fixtures ran their old bytecode, and an in-process re-load skipped any module already
in `sys.modules` (S-26). `load_project_gates` moved here from the CLI so there is one
copy; it returns the ids each module registered, cached or not.

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
           licence="", note="") -> InputArtifact     # copies into inputs/<bucket>/, hashes
def ingest_link(root, ledger, url, *, description="", kind=ArtifactKind.LINK, when="") -> InputArtifact
def add_extraction(ledger, artifact_id, extraction: Extraction) -> InputArtifact
def inputs_hash(ledger) -> str                       # over sorted (id, sha256); a display id
def unextracted(ledger) -> list[InputArtifact]       # evidence nobody read = decoration
def grounding(ledger) -> dict[str, list[str]]        # param/claim id -> artifact ids
def requests_by_kind(ledger, *, project_kind="", limit=6) -> list[tuple[str, str]]  # (kind, prompt)
def suggest_requests(ledger, *, project_kind="") -> list[str]
    # the ASK list, filtered to what is MISSING. This is what makes intake actively
    # solicit files instead of waiting for the user to think of them.
```
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
def installed(root, *, ledger=None) -> list[str]           # the project's opted-in packs, in order
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
`availability(spec)` fails, and is reported in `skipped`, never in `problems`; and
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
def write_log(root, ledger) -> str           # docs/decisions.md
def why(ledger, name: str) -> str            # one param or claim: value, rationale,
                                             # rejected alternatives, gates, grounding,
                                             # and the decisions that moved it
```
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

### `site.py`  (deps: models, util, claims, report, modelio, gates, verdicts)
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
an age of zero renders as "just now".

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
atompipe claim add|list|show|edit|physical  atompipe gap [--propose]
atompipe ingest <path...> [--kind] [--desc]   atompipe inputs [--unextracted]
atompipe extract <artifact> --what ... --grounds ...
atompipe ask [--kind]                     # what evidence to request from the user
atompipe check [--tier N] [--only GATE] [--force] [--no-record]   atompipe gate list|selftest|show
atompipe report [--write]                 atompipe why <param-or-claim>
atompipe decide --title ... --summary ...  atompipe packs [list|show|validate]
atompipe model [--write]                  atompipe doctor
atompipe check [--junit [PATH]]
atompipe gate selftest [GATE ...] [--pack NAME|DIR] [--user-packs] [--allow-empty] [--junit [PATH]]
```
Output is terse and machine-parseable by default (one line per verdict);
`--json` on every read command.

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
  for the resolver to find.
- **The view is never saved.** It holds cache verdicts, and coverage and read sets
  the records do not own. `tests/test_check_cache.py` walks this file's AST and
  refuses any `store.save` argument that flows from `_resolved`.

**`check`** runs `verdicts.sweep` — affected-only (D-05): per selected gate,
availability, then admission (the negative control runs on a control-entry miss;
S-05: nothing here ever ran a control, so a logger with a declared one produced PROVEN
rows), then the cache, then the run. The clock is stamped once per command (`now`):
obs, remembered outcomes, `last_check.json` and the JUnit report carry one instant.
- `--force` re-runs every selected gate AND its control (R-9); CI runs the bracket
  with it.
- `--no-record` is a dry sweep: nothing under `.atompipe/` but gate scratch in
  `out/` — no cache or control entry, obs, remembered outcome, `controls.json`,
  `digests.json`, `last_check.json`, and no ledger save (S-32: it used to save the
  ledger and read its own fresh passes as STALE, the one global clock not having
  moved).
- `--only`/`--tier` select as before; a filtered sweep writes its entries and obs but
  no `last_check.json`.
- Otherwise, after the sweep: `verdicts.write_last_check`, and — 1.2 only, while
  claims live in the ledger — the RECORDS ledger (parameters refreshed from the model,
  grounding linked, coverage refreshed) is saved with `verdicts=[]` and `Param.gates`
  from `verdicts.last_read_sets`. There is no run history (S-89: every recorded check
  rewrote the tracked ledger and appended a tracked run file).

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
`spine` and `junit`.

`check` text: executed rows stream as they land; a cached row prints only when it did
not pass, as `f"{line:<77} cached"`; then
`6 gates: 1 executed, 5 cached — 5 ok, 1 FAIL — tier 0` (`, S skipped` and
`, R errored` when non-zero; the time and the model hash left this line), then
`controls: E executed, C cached, R re-verified` when a control executed or was
re-verified, the skip digest, a note for gates outside the sweep, and the BLOCKING
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
environment rows (python, spine, project, ledger, layout, packs, gates, tools, model,
determinism, provenance, pack keys, ledger integrity, site, lock), one row per kind below,
each `ok` when there is nothing to say — a clean project shows that it looked:

| Row | Status when found | What it reads |
|---|---|---|
| `orphan-entries` | warn | verdicts of gates this project does not register (the resolver's orphan rows): stale, never counting, nothing here can re-run them. A warning, not a ledger-integrity FAIL — nothing is corrupt, something was uninstalled |
| `instruments` | warn | an entry recorded under another library version (`recorded under numpy 1.26.4; here 2.1.0`, and "outcome differs across instruments"): provenance, never staleness (Q1.3) |
| `opaque-inputs` | warn | an entry with an opaque channel (a subprocess's own reads, a file outside the project, `self-modified:`): never served from the cache |
| `cache-entries` | warn | every verdict or control entry the strict readers ignored, a `hand-edited entry` (digest mismatch) by name; any resolver note no other row claims lands here |
| `two-outcomes` | warn, FAIL once `verdicts.TWO_OUTCOMES_IS_ERROR` | two outcomes recorded for identical inputs, read at call time; **two control outcomes** at one rho_control FAIL always (the gate is not admitted). Controls are read for every gate on disk, not only for the gates whose verdict is Fresh |
| `code-digest` | warn | a gate keyed by its defining file (registered from Python, no recorded closure): a value its function closes over is not seen. The CLI never makes one |
| `pending-controls` | warn | controls whose fixture code moved (`<k> control(s) pending — inputs moved (<files>); the next check re-verifies`, the sentence `status`'s note prints) |
| `imports` | warn | a gate that imports a third-party module (`CodeRef.third_party`) it does not declare in `requires_python` or a `python:` entry of `requires_one_of`: where it is missing, the gate errors instead of reading SKIPPED. In the gate's own file, read by reach — module-level imports plus those inside the gate function and the module-level functions and classes it names, transitively; other closure files whole. A whole-file rule named `cad.bounding`, the one tier-0 gate of a module whose other gates import trimesh lazily |
| `env-reads` | warn | the static env-read detector (spec §3.17): an AST scan of every registered gate's closure files for `os.environ`, `os.getenv`, `os.putenv` (and the bytes twins), through any alias of `os` or a `from os import`. An environment read fires no audit event, so no entry keys on it (§8) |
| `sealed-fixtures` | FAIL | invariant 5 at runtime: every pack control run against this project's params through `packs.seal_findings`, into a temp `out_dir`; a control that reads its host is named with the paths it read |

The two static detectors measured zero hits on the 54 bundled gates before they landed
(R-4; `tests/test_doctor.py`). No staleness row: which gates are current is `status`'s
`stale:` block. No run-history row: there is none to read (S-31).
