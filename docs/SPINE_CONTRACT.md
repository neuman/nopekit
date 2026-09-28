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
class RunMeta(Record):          # bookkeeping for one sweep
    when: str = ""; tier: int = 0; model_hash: str = ""; inputs_hash: str = ""
    spine_version: str = ""; duration_s: float = 0.0
class Ledger(Record):           # the whole project state: .atompipe/ledger.json
    meta: ProjectMeta; claims: list[Claim]; params: list[Param]
    inputs: list[InputArtifact]; needs: list[Need]; decisions: list[Decision]
    verdicts: list[Verdict]                                              # latest per gate
    views: list[View]; last_run: RunMeta
    def claim(cid) | param(name) | artifact(aid) | need(nid) | verdict(gate_id) -> record | None
    def verdicts_for(cid) -> list[Verdict];  def upsert_verdict(verdict) -> None
```
`Verdict`, `NegativeControl` and `GateSpec` are records too. Their fields and
properties are listed once, in `docs/PACK_FORMAT.md` ("The surface a gate sees"),
because a pack author is the reader who sets them; two copies of one field list is
one copy that goes stale. `Verdict.outcome` is the single derivation of what a
verdict says — `"error"` if `error`, else `"skipped"` if `skipped`, else `"pass"` if
`passed is True`, else `"fail"` — and `ok`, `render()` and `claims.resolve_status`
all read it; every new consumer (JUnit, the index, the page) calls it and never
re-derives one (PLAN R-5). `GateSpec.requires_one_of` is the last field.

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
def runs_dir(root) -> str                               # .atompipe/runs/
def record_run(root, verdicts, run_meta) -> str         # append-only history; returns path
def load_runs(root, limit=20) -> list[dict]
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
.atompipe/ledger.json   .atompipe/runs/   .atompipe/out/
inputs/{sketches,references,cad,screenshots,datasheets,specs,measurements,data}/
docs/   model/
```

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
def model_hash(projection: dict) -> str      # stable; drives staleness
def write_projection(root, projection) -> str   # .atompipe/model.json  (the diffable view)
def params_from_model(model) -> list[Param]  # merge PARAMS with dataclass fields+defaults
def undocumented_params(model) -> list[str]  # no rationale: the report's nag list
def check_determinism(model, runs=2) -> tuple[bool, str]   # build twice; the projection must not move
```
Rule enforced here: a projection value that is a dataclass/enum is encoded via
`models._enc`. Non-JSON-safe values raise `AtompipeError` naming the field —
silent coercion is how a model and its projection drift apart.

### `gates.py`  (deps: models, util, store)
```python
@dataclass
class GateContext:                                      # the full surface: docs/PACK_FORMAT.md
    root: str; ledger: Ledger; model: Any | None; params: dict
    out_dir: str; tier: int; log: Callable[[str], None]; extra: dict
    pack: str; key_scope: str                           # stamped by run_gate from the spec
    def scopes(self) -> list[str]                       # ["fdm", "fdm-print"]: key namespaces, best first
    def param(self, name, default=None, *, scope=...) -> Any   # PACK-SCOPED first: see below
    def pack_param(self, name, default=None) -> Any
    def first_pack_param(self, names, default=None) -> Any
    def first_pack_param_named(self, names, default=None) -> tuple[Any, str]
    def require_param(self, name) -> Any                # raises rather than compare with None
    def out_path(self, *parts) -> str                   # an evidence path under out_dir, dir created
    def with_extra(self, extra) -> GateContext          # a copy with `extra` merged over
def scope_of(gate_id: str) -> str                       # "fdm.bed_fit" -> "fdm"
SCOPE_SEP = "."
class Registry:
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
def run_gate(spec, fn, ctx) -> Verdict                  # times it; catches exceptions -> error verdict
def run_all(registry, ctx, *, max_tier=0, only=None, on_verdict=None) -> list[Verdict]
def selftest(spec, fn, ctx) -> Verdict                  # runs the NEGATIVE CONTROL
def load_fixture(ref: str, root: str) -> Any            # "mod:fn" or "path/to/file.py"
def describe(spec) -> str                               # one dense line for `atompipe gate list`
def registry_summary(registry) -> dict                  # JSON-safe: what can run here, and what cannot
```
`gate(registry=None)` decorates into `active_registry()`, which is `REGISTRY` unless a
`use_registry(...)` block says otherwise. That block is the seam a loader needs (the
CLI's project-gate loader holds it across each import): a gate module cannot name the
registry it should land in, and a loader that handed a private registry to an import
without it would get its gates in the global one and an empty diff back — a project
that looks like it ships no gates, with no error.
A gate function receives `GateContext` and returns `Verdict` **or** a plain
`(bool, detail)` / dict, which `run_gate` normalises. `run_gate` always fills in
`gate`, `tier`, `pack`, `claims`, `duration_s` from the spec — a gate cannot lie
about its own identity.

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
def resolve_status(claim, verdicts, *, stale: bool = False) -> ClaimStatus
def explaining_verdict(claim, verdicts) -> Verdict | None    # THE reason: failed > errored > skipped
def statuses(ledger, *, stale=False, registry=None) -> dict[str, ClaimStatus]
def coverage(ledger, registry) -> dict[str, list[str]]       # claim id -> LIVE gate ids
def effective_gates(ledger, registry) -> dict[str, list[str]]  # cached `claim.gates` UNION live
def find_gaps(ledger, registry) -> list[Need]                # MEASURABLE claims with no gate
def blocking(ledger, registry, *, stale=False) -> list[tuple[Claim, ClaimStatus]]
def summarise(ledger, registry, *, stale=False) -> dict      # counts by status, for the CLI
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
all ran and passed and `stale`: STALE; else PASS. **A skip is never a pass.**

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
def inputs_hash(ledger) -> str                       # over sorted (id, sha256)
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

### `packs.py`  (deps: models, util, store)
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
module-level `PACK = "<name>"`; gate modules use the `@gate` decorator. A gate
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

### `report.py`  (deps: models, util, store, claims, artifacts)
```python
STATUS_TAG: dict[ClaimStatus, str]           # PASS -> "ok   ", FAIL -> "FAIL ", ...
SECTION_PROVEN = "## What is PROVEN"         # the PROVEN heading, as emitted and as tests find it
JUNIT_DEFAULT = ".atompipe/out/junit.xml"    # `--junit` with no path; ignored scratch, never tracked
def status_tag(status) -> str                # "[FAIL ]": the one fixed-width spelling of a status
def render_terminal(ledger, registry, *, stale=False) -> str
def render_markdown(ledger, registry, *, stale=False, title="") -> str
def write_report(root, ledger, registry, *, stale=False) -> str   # docs/readiness.md
def render_junit(ledger, verdicts, registry, *, tier, ready, exit_code, when,
                 not_run=None, cached=frozenset(), stale=False, spine="") -> str
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

The PROVEN section's heading line **starts with `SECTION_PROVEN`** (its qualifier,
"(machine-verified this run)", follows on the same line and is not part of the
constant). Invariant 4's tests find the section by that constant and fail when it is
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

`store` is in the deps for one reason: `write_report` takes its destination from
`store.project_paths(root)["readiness"]`. Layout is `store`'s job alone, and a
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

### `site.py`  (deps: models, util, store, claims, report, artifacts, modelio, gates)
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
def build(root, ledger, registry, view_registry, *, model=None, projection=None, now="") -> dict
def state(root, ledger, registry, *, now="", stale=None) -> dict   # the state.json payload
def clean_assets(root, keep) -> list                        # delete unreferenced site/assets/ files
def vendor_urls() -> dict                                   # {site-relative path: URL} for `site vendor`
```
**`build` never runs gates**, and never writes the ledger back. It reads the verdicts
already recorded and stamps each with its own age; a build that re-ran the cheap
gates on the way past would publish ten-second-old tier-0 numbers beside week-old
tier-2 numbers under one "built at" stamp. `state` borrows every judgement —
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
atompipe check [--tier N] [--only GATE]   atompipe gate list|selftest|show
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
def _blocking_reason(ledger, claim, status) -> str  # "gate : body" from the explaining verdict, else a status sentence
def _verdict_row(verdict) -> dict                   # one verdict as JSON: its fields plus `ok` and `outcome`
```
- The BLOCKING tag is `report.status_tag`, never a status truncated to four letters:
  `check` printed `[fail]`/`[uncl]` where `status` printed `[FAIL ]`/`[gap  ]` for the
  same claims (S-69).
- The reason is `claims.explaining_verdict`'s, formatted exactly as
  `render_terminal` formats it (S-68): ran-and-failed, then errored, then skipped.
- A verdict row in `--json` carries `outcome` (`"pass" | "fail" | "error" |
  "skipped"`, `Verdict.outcome`) next to `ok`. Both are set explicitly: `to_dict`
  serialises dataclass fields only, and both are properties.
