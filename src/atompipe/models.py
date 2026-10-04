# SPDX-License-Identifier: Apache-2.0
"""atompipe.models — every shared type in the spine.

This module is the CONTRACT. Nothing here imports anything outside the standard
library, and nothing else in the spine defines a type that crosses a module
boundary. If two modules need to agree on a shape, it lives here.

The spine's central idea is a chain:

    claims  ->  gates  ->  packs  ->  readiness report

A CLAIM is something that must be true for the design to work ("floats with the
full payload at <=60% draft"). A GATE is an executable that settles a claim and
is able to FAIL. A PACK supplies gates for one physical domain. The READINESS
REPORT is the ledger rendered: what is proven, what is not, and why.

Everything round-trips through plain JSON dicts so the state on disk stays
diffable and an agent can read one record without loading the rest.
"""
from __future__ import annotations

import dataclasses
import enum
import hashlib
import os
import re
from dataclasses import dataclass, field
from typing import Any


# --------------------------------------------------------------------------- #
# enums
# --------------------------------------------------------------------------- #
class StrEnum(str, enum.Enum):
    """str-valued enum that serialises as its value on json.dumps."""

    def __str__(self) -> str:          # pragma: no cover - trivial
        return str(self.value)


class ClaimKind(StrEnum):
    """How a claim can ever be settled."""

    MEASURABLE = "measurable"
    """A gate can settle it from the model. This is the only kind a machine proves."""

    PHYSICAL = "physical"
    """Only a real object in the real world settles it. Watertightness of a printed
    seam, the feel of a mechanism under a finger, radio range with a hand over the
    antenna. These are
    never 'proven' by the pipeline; they are carried, visibly, until a human
    records a result."""

    ASSUMPTION = "assumption"
    """Accepted provisionally, with a reason and an owner, and recorded so it
    stays visible (Assumed, GLOSSARY §3; with no attributed owner it reads Gap).
    An assumption that nobody wrote down is the thing that sinks the build."""


class ClaimStatus(StrEnum):
    """Resolved state of one claim. Derived, never stored by hand.

    Ten members, read as Table 1's seven words plus Open (GLOSSARY §3; the
    words themselves live in ``report.HUMAN``, never here). The values are JSON
    and stay until the rename pass; from P2.1 each is the reading in its
    comment, composed by ``claims.compose``, whose ``cause`` says which fact
    set it. What slipped through before P2.1: ``fail`` also held a crash and an
    evaluator refused at its version, and ``pass`` held a pass beside a skip or
    an unrun evaluator (S-03)."""

    PASS = "pass"                 # Checked: every evaluator ran and passed, current
    FAIL = "fail"                 # Failing: an evaluator not unqualified failed
    STALE = "stale"               # Stale: a pass whose read set moved, or undemonstrated,
                                  # or whose prerequisite is invalidated or unrun (P2.2)
    UNCLAIMED = "unclaimed"       # Gap: no evaluator, one unqualified, or an unowned assumption
    BLOCKED = "blocked"           # Skipped: an evaluator skipped OR ERRORED, or a
                                  # prerequisite is not established; none failed
    PENDING = "pending"           # Open: an evaluator unrun on the current inputs
    UNVERIFIED = "unverified"     # Pending build: physical, no result
    VERIFIED = "verified"         # Checked: an `interactive` pass on the current article,
                                  # or the authority's judgment (P2.5a)
    REFUTED = "refuted"           # Failing: a physical fail recorded
    ASSERTED = "asserted"         # Assumed: a reason and an attributed owner


class Terminal(StrEnum):
    """Where a claim's evidence bottoms out (GLOSSARY §1 *terminal*; PLAN M14.1) —
    identifiers, never words: the words are ``report.HUMAN["terminal"]``'s, and in
    P2.5a every automated one prints *automated* (P2.5a-D1).

    Declared on a claim (``Claim.terminal``) or derived from its kind
    (``claims.terminal_of``). A declaration can raise the bar, never lower it,
    and never stands in for an evaluator: a measurable claim declared
    ``closed_form`` with no evaluator still reads Gap. *Rejected:* "external
    tool" as a terminal (Q-W3: the program that runs an evaluator is provenance);
    replacing ``ClaimKind`` now (the rename pass: R-8 could not tell a word move
    from a status move)."""

    CLOSED_FORM = "closed_form"
    SOLVER = "solver"                 # display word: simulation
    DATASHEET = "datasheet"
    MEASUREMENT = "measurement"
    HUMAN = "human"                   # display word: expert judgment
    NONE = "none"


#: The terminals each kind may declare (P2.5a §4.1). A pair outside this table
#: is refused by the strict reader, and only when ``terminal`` is present (R-10:
#: a refusal binds a new declaration). Here, in the type contract, because
#: ``store`` refuses by it and ``claims`` derives by it. *Rejected:* measurable
#: + measurement (that is a physical claim, and the kind already says so);
#: physical + closed_form (a calculation that settled a physical claim would
#: make it measurable); assumption + solver (an assumption a solver settles is
#: no longer an assumption).
TERMINALS_BY_KIND: dict[ClaimKind, frozenset[str]] = {
    ClaimKind.MEASURABLE: frozenset({"closed_form", "solver", "datasheet"}),
    ClaimKind.PHYSICAL: frozenset({"measurement", "human"}),
    ClaimKind.ASSUMPTION: frozenset({"none", "human"}),
}


#: statuses that must block an irreversible spend (ordering a board, buying stock).
#: Unchanged by P2.1 (its D9): what moved is which status a fact reads, so an
#: errored claim blocks because Skipped (``blocked``) is here, and an assumption
#: nobody owns because Gap (``unclaimed``) is. Pending build and Assumed are
#: unresolved (GLOSSARY §3) but do not stop ``check``: *ready* is the stricter
#: predicate, ``claims.summarise``'s ``all_required_checked``.
BLOCKING_STATUSES = frozenset(
    {ClaimStatus.FAIL, ClaimStatus.STALE, ClaimStatus.UNCLAIMED,
     ClaimStatus.BLOCKED, ClaimStatus.PENDING, ClaimStatus.REFUTED}
)


class Tier(enum.IntEnum):
    """Cost tier of a gate. The inner loop only ever runs tier 0.

    Going from a twenty-minute full rebuild to a five-second single-part build is
    what makes iteration possible at all. A pack that ships only expensive gates has
    not finished its job.
    """

    INSTANT = 0     # < ~2 s, analytic / closed form. Run on every edit.
    BUILD = 1       # seconds to minutes. Geometry builds, mesh gates, netlists.
    SOLVE = 2       # minutes to hours. External solvers: CFD, FEA, autorouters.
    EXTERNAL = 3    # CI, a fab house, an external lab service.
    # Not a physical result (S-61): this said "a human with calipers" — a second
    # route for what a physical claim and its result own (`claim physical`,
    # P2.5a). A tier is an evaluator's latency class; a person is recorded by.


class PrerequisiteKind(StrEnum):
    """Why a prerequisite is not established (P2.2-D5) — an identifier, never a
    word; the words are ``report.HUMAN``'s.

    Two classes. **Negative** — the prerequisite has a reading that is not a
    pass: ``errored``, ``failed``, ``skipped`` (its tool is missing, or it
    skipped itself), ``unqualified``, ``not-registered``. Its dependent is not
    run and reads Skipped, and the kind travels on that verdict
    (``Verdict.blocked_kind``). **Not current** — a pass that is not current
    now: ``invalidated``, ``unrun``. Its dependent keeps its verdict and reads
    Stale; nothing is stored, only a stale reason.

    The members are in rank order: when several needs are unmet, the first
    negative kind here names the root (``gates.prerequisite_root``). A crash
    before a fail: the dependent reads Skipped either way (D10), and within
    Skipped a crash leads (invariant 2, GLOSSARY §3: "errored first"). What
    slipped through the design's first order (failed first): a dependent of one
    failed and one crashed guard read the quiet missing-tool tone, while the
    same dependent of the crashed guard alone read errored — adding a failure
    made the crash quieter. *Rejected:* a kind per root on the verdict (a list
    of pairs; nothing reads more than the first, and ``blocked_by`` names them
    all); deriving the kind from the root's own verdict at ``compose`` (a root
    that is not registered can still have an orphan crash on disk, which would
    then read errored for a root the rule called not registered).

    Here, in the type contract, because ``Verdict.blocked_kind`` holds one, and
    ``claims`` (pure) and ``gates`` (the rule) must read the same identifiers."""

    ERRORED = "errored"
    FAILED = "failed"
    SKIPPED = "skipped"
    UNQUALIFIED = "unqualified"
    NOT_REGISTERED = "not-registered"
    INVALIDATED = "invalidated"
    UNRUN = "unrun"


#: The kind of the ``Verdict.unqualified`` token the spine marks a pass with when
#: it lies outside its evaluator's declared operating context (P2.4-D15): a token
#: of its own kind, ``context:outside|<canonical json>``, so every reader that
#: already refuses an unqualified evaluator refuses this pass too — never a
#: pass, never a crash, Gap's tone — and only the words differ (``report``).
#: Here, in the type contract, because ``Verdict.__post_init__`` reads it (a mark
#: on a fail is dropped, D16) and ``claims``, ``gates``, ``report`` and ``site``
#: must read the same identifier. *Rejected:* a new ``Verdict`` field (P2.3 grew
#: some twenty readers of ``unqualified``; each would need a twin, and a missed
#: one would read a crash or a pass).
CONTEXT_OUTSIDE = "context:outside"


class ArtifactKind(StrEnum):
    """What a piece of ingested evidence IS.

    Intake is not only a conversation. Real projects are grounded in hand drawings,
    photographs of a competitor's insides, digitised layouts, caliper readings and
    datasheet figures. The pipeline must ask for these by name, because a user
    rarely volunteers them.
    """

    SKETCH = "sketch"                 # hand drawing, napkin diagram, whiteboard photo
    REFERENCE = "reference"           # photo of an existing product / teardown / prior art
    CAD = "cad"                       # STL, STEP, 3MF, f3d, KiCad, gerbers
    SCREENSHOT = "screenshot"         # a UI, a config, a vendor page, another tool
    DATASHEET = "datasheet"           # component or material datasheet (usually PDF)
    SPEC = "spec"                     # a written spec, brief, RFQ, requirements doc
    MEASUREMENT = "measurement"       # calipers, scale, meter readings from the real world
    STANDARD = "standard"             # a published standard or code (IPC, ASTM, NEC...)
    DATA = "data"                     # CSV / logs / test results
    LINK = "link"                     # a URL to prior art or a build log
    OTHER = "other"


#: file extensions -> a best-guess ArtifactKind, used by `atompipe ingest`
EXT_KIND_HINTS: dict[str, ArtifactKind] = {
    ".stl": ArtifactKind.CAD, ".step": ArtifactKind.CAD, ".stp": ArtifactKind.CAD,
    ".3mf": ArtifactKind.CAD, ".obj": ArtifactKind.CAD, ".glb": ArtifactKind.CAD,
    ".gltf": ArtifactKind.CAD, ".f3d": ArtifactKind.CAD, ".scad": ArtifactKind.CAD,
    ".dxf": ArtifactKind.CAD, ".iges": ArtifactKind.CAD, ".igs": ArtifactKind.CAD,
    ".kicad_pcb": ArtifactKind.CAD, ".kicad_sch": ArtifactKind.CAD,
    ".pdf": ArtifactKind.DATASHEET,
    ".csv": ArtifactKind.DATA, ".tsv": ArtifactKind.DATA, ".json": ArtifactKind.DATA,
    ".md": ArtifactKind.SPEC, ".txt": ArtifactKind.SPEC, ".docx": ArtifactKind.SPEC,
    ".png": ArtifactKind.SKETCH, ".jpg": ArtifactKind.SKETCH, ".jpeg": ArtifactKind.SKETCH,
    ".webp": ArtifactKind.SKETCH, ".heic": ArtifactKind.SKETCH, ".svg": ArtifactKind.SKETCH,
}


class ViewKind(StrEnum):
    """How a view is rendered. The site knows these and nothing else.

    Extensibility lives in the DATA, not in shipped code: a pack that emits one of
    these kinds gets visualisation for free, and the renderer stays fixed, small
    and auditable. A pack shipping its own JavaScript would make the site's
    behaviour depend on code nobody reviewed, in the one artifact whose whole job
    is to be trusted when a gate says something is wrong.
    """

    MODEL3D = "model3d"
    """A glTF/GLB assembly whose nodes are addressable by name, plus an explode
    manifest. This is the one that turns the site into a debugging tool: a gate
    that knows WHERE a problem is names the part, and the viewer shows you."""

    IMAGE = "image"
    """A raster or SVG render, optionally with hotspots in normalised coordinates
    (0..1 of width/height, so the overlay survives a re-render at another size)."""

    CHART = "chart"
    """Series the site plots, with the acceptance limit drawn as a line. A margin
    you can see is worth more than a margin you have to compute: the shape of the
    curve near the limit is what tells you whether a design is robust or lucky."""

    TABLE = "table"
    """Rows. A bill of materials, a stack-up, a per-part result sweep."""

    FIELD = "field"
    """A scalar field sampled over a mesh — pressure, temperature, stress, von
    Mises. Rendered as a colour map on the geometry. This is where a solver pack's
    output lands, and it is why MODEL3D carries node names rather than an opaque
    blob."""

    DIAGRAM = "diagram"
    """A schematic or graph (SVG, or mermaid source the site renders)."""


class NeedStatus(StrEnum):
    """Lifecycle of a capability gap."""

    OPEN = "open"               # identified, nothing chosen
    PROPOSED = "proposed"       # candidate tooling picked, awaiting the user's yes
    DEFERRED = "deferred"       # user said 'not yet' - stays visible
    INSTALLING = "installing"
    SATISFIED = "satisfied"     # a gate now covers the claim
    ABANDONED = "abandoned"


class Comparator(StrEnum):
    LE = "<="
    LT = "<"
    GE = ">="
    GT = ">"
    EQ = "=="
    NE = "!="
    BETWEEN = "between"         # lo <= x <= hi, uses `limit` and `limit_hi`

    def holds(self, measured: float, limit: float, limit_hi: float | None = None) -> bool:
        if self is Comparator.LE:
            return measured <= limit
        if self is Comparator.LT:
            return measured < limit
        if self is Comparator.GE:
            return measured >= limit
        if self is Comparator.GT:
            return measured > limit
        if self is Comparator.EQ:
            return measured == limit
        if self is Comparator.NE:
            return measured != limit
        if self is Comparator.BETWEEN:
            hi = limit if limit_hi is None else limit_hi
            return limit <= measured <= hi
        raise ValueError(f"unhandled comparator {self!r}")


# --------------------------------------------------------------------------- #
# serialisation helpers
# --------------------------------------------------------------------------- #
def _enc(value: Any) -> Any:
    """Recursively turn dataclasses / enums into JSON-safe primitives."""
    if isinstance(value, enum.Enum):
        return value.value
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {k: _enc(v) for k, v in dataclasses.asdict(value).items()}
    if isinstance(value, dict):
        return {k: _enc(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_enc(v) for v in value]
    return value


class Record:
    """Mixin giving every model a symmetric dict round trip.

    Unknown keys on the way in are DROPPED rather than raising, so a newer pack
    writing an extra field cannot break an older spine reading the file.

    That leniency is for dicts in memory and for files a pack writes. A record
    FILE a human edits (`claims/C1.json`, …) is read by `store.read_record`,
    which refuses an unknown key with a suggestion instead: dropped here, a
    typo'd `rejectd` vanished on load and was erased from disk by the next save
    (S-40).
    """

    def to_dict(self) -> dict[str, Any]:
        return {k: _enc(v) for k, v in dataclasses.asdict(self).items()}

    @classmethod
    def from_dict(cls, data: dict[str, Any]):
        names = {f.name for f in dataclasses.fields(cls)}          # type: ignore[arg-type]
        return cls(**{k: v for k, v in (data or {}).items() if k in names})


def slugify(text: str, *, maxlen: int = 48) -> str:
    """Lowercase kebab id from free text. Stable, filesystem-safe, no collisions
    handled here - callers dedupe."""
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").strip().lower()).strip("-")
    return (s[:maxlen].rstrip("-")) or "item"


def sha256_file(path: str | os.PathLike[str], *, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while block := fh.read(chunk):
            h.update(block)
    return h.hexdigest()


# --------------------------------------------------------------------------- #
# provenance:  why is this number this number?
# --------------------------------------------------------------------------- #
@dataclass
class Rejected(Record):
    """An alternative that was tried or considered and did NOT win.

    This is the single highest-value field in the whole system. A record like 'the
    wider trace was tried first and the router could not close that net through the
    congested corridor' means no future context window ever wastes a cycle
    re-proposing it. Without this, every fresh agent re-litigates every settled
    number.
    """

    value: str                       # rendered value, e.g. "0.5 mm"
    why: str                         # why it lost, concretely and measurably
    evidence: str = ""               # path / run id / datasheet section, if any


@dataclass
class Param(Record):
    """One model parameter with its full provenance."""

    name: str
    value: Any
    units: str = ""
    rationale: str = ""              # why THIS value; the physics, not the restatement
    rejected: list[Rejected] = field(default_factory=list)
    source: str = ""                 # "datasheet: TI SLYS045C Fig 6-2" / "inputs/sketches/hull.png"
    grounded_by: list[str] = field(default_factory=list)   # InputArtifact ids
    gates: list[str] = field(default_factory=list)         # gate ids that protect it
    derived_from: list[str] = field(default_factory=list)  # other param names
    changed_in: str = ""             # decision id / revision where it last moved
    tags: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Param":
        names = {f.name for f in dataclasses.fields(cls)}
        kw = {k: v for k, v in (data or {}).items() if k in names}
        kw["rejected"] = [Rejected.from_dict(r) if isinstance(r, dict) else r
                          for r in (kw.get("rejected") or [])]
        return cls(**kw)

    @property
    def is_derived(self) -> bool:
        return bool(self.derived_from)


# --------------------------------------------------------------------------- #
# claims
# --------------------------------------------------------------------------- #
@dataclass
class Acceptance(Record):
    """The machine-checkable threshold behind a claim.

    A claim without an acceptance is a wish. 'Strong enough' is not a claim;
    '<=0.5 mm tip deflection at 3 N' is.
    """

    quantity: str = ""                     # "draft fraction", "tip deflection"
    comparator: Comparator = Comparator.LE
    limit: float | None = None
    limit_hi: float | None = None          # only for BETWEEN
    units: str = ""

    def holds(self, measured: float) -> bool:
        if self.limit is None:
            raise ValueError("acceptance has no limit to compare against")
        return self.comparator.holds(measured, self.limit, self.limit_hi)

    def render(self) -> str:
        if self.limit is None:
            return self.quantity or ""
        if self.comparator is Comparator.BETWEEN:
            hi = self.limit if self.limit_hi is None else self.limit_hi
            return f"{self.quantity} in {self.limit}..{hi} {self.units}".strip()
        return f"{self.quantity} {self.comparator.value} {self.limit} {self.units}".strip()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Acceptance":
        names = {f.name for f in dataclasses.fields(cls)}
        kw = {k: v for k, v in (data or {}).items() if k in names}
        if "comparator" in kw and kw["comparator"] is not None:
            kw["comparator"] = Comparator(kw["comparator"])
        return cls(**kw)


@dataclass
class PhysicalResult(Record):
    """A physical result: a physical evaluator's verdict on one article, with who
    recorded it (GLOSSARY §1). One entry of ``results/<claim id>.json``'s
    ``results`` list, append-only, written only by ``claim physical`` through
    ``store.append_signed`` (P2.5a).

    What slipped through before P2.5a (S-48, S-50): ``who`` was a name anyone
    typed (``--who``) and defaulted to nobody; ``when`` was typed too; a pass
    bound to nothing survived any change to the design it was tested on; and a
    pass typed by the agent counted as one a person made.

    The fields after ``evidence`` are P2.5a's, each LAST (R-2), each defaulting
    to "not recorded", so a P2.1-shape entry — a **legacy** entry — reads with
    them empty, and an empty one never makes a pass count:

    * ``channel`` — how it was entered (``cli._channel``): ``interactive`` (a
      person's own shell, the claim's id typed), ``agent-session <id>``,
      ``non-interactive``, or ``""`` (legacy). Only ``interactive`` lets a pass
      count; every fail counts (R-3).
    * ``authority`` — for an expert-judgment claim, the authority it was
      recorded for (equal to the claim's, typed as a confirmation).
    * ``measured``, ``units`` — the value measured, in the claim's acceptance
      units (D-13 *consistent*: it decides the outcome where the claim has a
      limit).
    * ``article`` — what it was tested on: ``{"source": "design", "hash",
      "built_from": {"params", "model", "files"}, "revision", "dirty"}``
      (``verdicts.article_of``), or ``{}`` when the model did not load.
    * ``claim_digest`` — the claim as the person read it (``claims.claim_digest``).
    * ``rho`` — the digest of (article hash, claim digest): the physical
      evaluator's read-set hash (§6.2).
    * ``evidence_sha256`` — each evidence file's bytes when recorded.
    * ``contradicts``, ``contradiction_check`` — for a fail, the covering
      evaluators whose counted, current pass it contradicts
      (``claims.contradicted_by``), or why none could be judged.
    * ``prev``, ``digest`` — the chain and the seal (``store``, P2.5a-D6).

    What a seal does not stop is in ``store.append_signed``'s docstring and
    SPINE_CONTRACT's limits: anyone who can write the file can recompute it."""

    passed: bool
    when: str = ""                   # the command's clock (never typed, from P2.5a)
    who: str = ""                    # git's identity (never typed, from P2.5a)
    detail: str = ""
    evidence: list[str] = field(default_factory=list)   # photo / log paths
    channel: str = ""
    authority: str = ""
    measured: float | None = None
    units: str = ""
    article: dict = field(default_factory=dict)
    claim_digest: str = ""
    rho: str = ""
    evidence_sha256: dict = field(default_factory=dict)
    contradicts: list = field(default_factory=list)
    contradiction_check: str = ""
    prev: str = ""
    digest: str = ""


@dataclass
class AttributionRecord(Record):
    """An owner's or an authority's attribution, as ``claim physical <id>
    assume`` recorded it — one entry of ``results/<claim id>.json``'s
    ``attributions`` list, sealed and chained like a result (P2.5a-D6).

    ``role`` is ``owner`` (an assumption's owner, or a fallback's — P2.4-D18's
    hand-off) or ``authority`` (an expert-judgment claim's). ``name`` is the
    nominee the claim file named when it was recorded, ``reason`` the reason it
    was recorded against (``claims.assumption_reason``), ``claim_digest`` the
    claim as read then. It counts only while the claim still names ``name`` for
    that role and — for an owner — still gives ``reason``; for an authority,
    while the claim as a whole is unchanged (an edited statement un-records an
    acceptance of it). What slipped through before it: an owner was whatever a
    claim file said (P2.1-D8 made that read Gap; nothing could record one)."""

    role: str
    name: str
    reason: str = ""
    claim_digest: str = ""
    who: str = ""
    when: str = ""
    channel: str = ""
    prev: str = ""
    digest: str = ""


@dataclass(frozen=True)
class EntryStanding:
    """The judge's facts about ONE result entry (``verdicts.judge_results``):
    ``index`` in the results list; ``passed``; ``counts`` — a pass that settles
    the claim now; ``why`` — why not, an identifier (``agent-session``,
    ``non-interactive``, ``legacy``, ``who``, ``authority``, ``measured``,
    ``evidence:<path>``, ``claim-moved``, ``article-moved``, ``judgment-moved``,
    ``article-unjudged``, ``beside`` — a pass beside an automated evaluator —
    or ``none`` — on an assumption), ``""`` when it counts; ``article`` — its
    article's hash; ``article_state`` — ``current``, ``moved``, ``unjudged`` or
    ``""`` (no article); ``moved`` — what moved, in words, inputs first."""

    index: int
    passed: bool
    counts: bool = False
    why: str = ""
    article: str = ""
    article_state: str = ""
    moved: tuple = ()


@dataclass(frozen=True)
class Standing:
    """What a claim's physical results stand for NOW — judged on every read by
    ``verdicts.judge_results`` inside the one resolver, never cached, never
    written (P2.5a-D13). It reaches ``claims.compose`` on the claim
    (``Claim.standing``), set by ``verdicts.view`` only.

    ``state`` — ``current`` (a pass counts), ``article-moved``,
    ``judgment-moved``, ``claim-moved``, ``article-unjudged`` (the newest pass
    a person made in their own shell, and its first half that no longer holds),
    ``not-counted:<why>`` (the newest pass, and why it never counted), or ``""``
    (no pass). ``counted`` — the deciding entry's index (the counting pass, or
    the one ``state`` is about), or None. ``article``, ``moved`` — its article
    and what moved. ``entries`` — every entry's ``EntryStanding``."""

    state: str = ""
    counted: int | None = None
    article: str = ""
    moved: tuple = ()
    entries: tuple = ()


@dataclass
class Claim(Record):
    """Something that must be true for the design to work."""

    id: str
    statement: str
    kind: ClaimKind = ClaimKind.MEASURABLE
    acceptance: Acceptance = field(default_factory=Acceptance)
    rationale: str = ""              # why this matters; what breaks if it is false
    source: str = ""                 # "intake discussion" / artifact id / standard
    grounded_by: list[str] = field(default_factory=list)   # InputArtifact ids
    gates: list[str] = field(default_factory=list)         # gate ids that cover it
    tags: list[str] = field(default_factory=list)
    critical: bool = True            # false = not required, never blocks a spend
    physical_result: PhysicalResult | None = None
    note: str = ""
    owner: str = ""
    """Who an ASSUMPTION's owner is NAMED to be — a nominee, never an
    attribution. GLOSSARY §3: Assumed needs a reason and an owner; PLAN-v0.14
    §1.4: an owner counts only when recorded through the signing channel, and
    one written any other way — a hand or agent edit of this file — reads
    unattributed, so the claim stays Gap. ``claims.compose`` reads this field
    only against ``owners``, the attributions the channel produced, and nothing
    in P2.1 produces one. *Rejected:* a forbidden key (the strict reader would
    refuse every command on a file an agent plausibly writes, and refusing the
    edit is P3's permission rule); trusting the file until the channel exists
    (the exact edit §1.4 says must not count). The LAST field (R-2), so a
    positional reader is unmoved. What an older spine does with it — corrected
    in review, where this said "an older spine drops it": a spine before P2.1
    REFUSES every command on a claim file that names an owner (`store.read_record`
    refuses an unknown key, S-40; only the lenient `Record.from_dict`, which no
    record file goes through, drops one). That is the forbidden key's cost,
    rejected above, paid in the other direction — and it holds only while
    nothing but a hand edit writes this field: no command writes it (V7), so a
    project an older atompipe still reads never holds it unless someone typed
    it."""
    fallback: str = ""
    """Why this claim may be carried, untested, when the passes of its
    evaluators lie outside their operating contexts (P2.4-D18, PLAN-v0.14 §1.5:
    "the claim reads Assumed when it has an owned fallback assumption,
    otherwise Gap"). Its owner is ``owner`` — the one nominee field — and it
    counts only through ``owners``: an ``Attribution`` bound by value to the
    owner and to THIS reason (``claims.assumption_reason``), which nothing
    produces until the signing channel (P2.5). So in P2.4 every such claim
    reads Gap, with the hint that names what would carry it. What it is NOT:
    an attribution (a hand or agent edit names a reason; it never records an
    owner), and not ``rationale`` (that says why the claim matters, not why it
    may be carried untested — an owner would sign the wrong sentence).
    *Rejected:* a pointer to an assumption claim (two records for one
    judgement, and a status that depends on another claim's status); GLOSSARY
    §8's ``assumed: {reason, owner}`` now (a second owner field before the
    rename pass, which folds both). A spine before P2.4 refuses a claim file
    carrying it (as P2.1's ``owner``); no command writes it, and an empty one
    digests as before (``verdicts._ABSENT_WHEN_EMPTY``). The LAST field (R-2)."""
    terminal: str = ""
    """Where this claim's evidence bottoms out, DECLARED (``Terminal``), or
    ``""`` — derived from the kind (``claims.terminal_of``: physical ->
    measurement, assumption -> none, measurable -> automated). Validated by the
    strict reader only when present (R-10), against ``TERMINALS_BY_KIND``; a
    declaration never makes a claim read more than it would without it
    (P2.5a-D1). Empty, it digests as before (``_ABSENT_WHEN_EMPTY``)."""
    authority: str = ""
    """The person or institution an expert-judgment claim (terminal ``human``)
    stays with — a NOMINEE, like ``owner`` (P2.5a-D2, GLOSSARY §1 *authority*).
    It counts only as ``claim physical <id> assume --authority`` recorded it,
    typed by the authority in their own shell; named here and nowhere recorded,
    the claim reads Gap. What slipped through the design read literally: an edit
    adding ``"terminal": "human", "authority": "<anyone>"`` would have turned any
    Gap into a passing ``check`` (Assumed does not block). Refused by the strict
    reader on a claim whose terminal is not ``human``."""
    results: tuple = ()
    """In memory only (``FORBIDDEN_KEYS``): every entry of
    ``results/<id>.json``'s ``results``, oldest first, assembled by ``store``;
    ``physical_result`` is the one that counts for rung 1. What the judge reads."""
    attributions: tuple = ()
    """In memory only: the owner and authority attributions recorded through
    the channel — sealed, ``interactive`` — newest first, assembled by
    ``store`` so a raw ``store.load`` reader sees the same owners as the view
    (P2.5a-D11)."""
    standing: Any = None
    """In memory only: the judge's ``Standing``, set by ``verdicts.view`` and
    nothing else. ``None`` — a raw ledger nobody judged — reads every pass
    Pending build in P2.1's words (degrade-closed, R-2)."""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Claim":
        names = {f.name for f in dataclasses.fields(cls)}
        kw = {k: v for k, v in (data or {}).items() if k in names}
        kw["kind"] = ClaimKind(kw.get("kind") or ClaimKind.MEASURABLE)
        kw["acceptance"] = Acceptance.from_dict(kw.get("acceptance") or {})
        pr = kw.get("physical_result")
        kw["physical_result"] = PhysicalResult.from_dict(pr) if isinstance(pr, dict) else pr
        # In-memory fields never come from a dict: a record never holds them
        # (FORBIDDEN_KEYS), and an index row that did would be a second home.
        for name in ("results", "attributions", "standing"):
            kw.pop(name, None)
        return cls(**kw)


# --------------------------------------------------------------------------- #
# gates and verdicts
# --------------------------------------------------------------------------- #
#: ``Verdict.outcome`` -> the four-character tag ``Verdict.render`` prints. Four
#: characters so a sweep's lines align; the strings are the ones every grep and
#: every reader already knows (``[FAIL]``), so they do not change with the rule
#: that picks them. The ONE table of outcome tags: ``report.HUMAN["outcome_tag"]``
#: is this object, re-exported, not a copy held equal by a test (D-16: a second
#: table drifts, and an equality test only says so by failing). It lives here
#: because ``models`` imports nothing from ``report``.
_RENDER_TAG = {"error": "ERR ", "skipped": "skip", "pass": "ok  ", "fail": "FAIL"}


@dataclass
class Verdict(Record):
    """The result of running one gate. ONE LINE of context when rendered.

    A verdict rendered as `{"check":"geometry","pass":true,"detail":"..."}` is why
    a full sweep costs tens of lines instead of thousands. Keep
    `detail` to a single dense line; put the volume in `evidence` files.
    """

    gate: str
    passed: bool = False
    claims: list[str] = field(default_factory=list)
    measured: float | None = None
    limit: float | None = None
    units: str = ""
    detail: str = ""
    evidence: list[str] = field(default_factory=list)
    duration_s: float = 0.0
    tier: Tier = Tier.INSTANT
    skipped: bool = False
    skip_reason: str = ""            # "requires openfoam (not installed)"
    error: str = ""                  # gate crashed; NOT the same as failing
    pack: str = ""
    locators: list[Locator] = field(default_factory=list)
    """WHERE this verdict applies, for the site to highlight. Optional and often
    empty: a gate attaches one only when it genuinely knows the position."""
    rho: str = ""
    """The content address of what this verdict was computed from — the hash of
    the gate's code, the spine, and every input the gate read (PLAN D-05). Set by
    the sweep that recorded it, never by the gate: ``run_gate`` clears whatever a
    gate returned here, because a verdict that could name its own inputs could
    name inputs it never read. Empty on a verdict nobody recorded."""
    cpu_s: float = 0.0
    """CPU seconds the gate cost, user and system, its child processes INCLUDED —
    the ``os.times()`` delta ``run_gate`` measures. ``duration_s`` alone called the
    most expensive gates free: omc does its work in a subprocess, and a gate
    waiting on a solver spends wall time, not its own CPU. Measured, never
    declared; 0.0 for a skip, which did no work. Both new fields are last (PLAN
    R-2), so positional construction still means what it meant."""
    unqualified: str = ""
    """Why the evaluator is not qualified at its version — a spine TOKEN
    (``verdicts.parse_token`` reads it; ``report.HUMAN["qualification"]`` words
    it: ``known-good:fail``, ``known-good:not-run``, ``known-bad:errored|<line>``
    …), never prose — or ``""``. **Set by the spine only**: the resolver and the
    sweep set it, through ``verdicts._unqualified``, beside ``error="unqualified:
    <token>"``; ``gates.run_gate`` clears it on whatever a gate returns, as it
    clears ``rho``; no stored verdict carries it (an entry is built from an
    explicit field list, and the remembered-outcome reader drops it).
    ``claims.compose`` reads a claim with an unqualified evaluator as Gap,
    ``unqualified:`` (PLAN-v0.14 §1.4), and never as errored (P2.0 D-8). Why a
    token and not the reason's words (P2.3-D13): the words would be frozen at
    mint time inside a verdict that travels — sweep rows, held verdicts — and
    the spine would hold a human channel; the error text an older reader shows
    is the token, and reads as the crash it falls back to (degrade-closed).

    What it replaced: the ``not admitted:`` prefix of ``error`` as the predicate.
    *Rejected,* because a gate could then make its own crash read the quieter
    Gap by wording it so, and because P2.3's rewording of the text would
    silently turn every Gap into errored. So the text alone reads errored —
    degrade-closed — and only this field reads Gap. The LAST field (R-2); and
    ``__post_init__`` writes ``error`` when it is set without one, so an
    unqualified verdict is never ``ok``, whoever builds it."""
    blocked_by: list[str] = field(default_factory=list)
    """The prerequisites that are not established — the ROOTS, found
    transitively — when this gate was not run because of them (P2.2-D9), or
    ``[]``. Set by the spine only, through ``gates.blocked``, the one producer
    of a prerequisite skip; ``gates.run_gate`` clears it on whatever a gate
    returns (``_stamp``), no entry stores it (``verdicts._VERDICT_FIELDS`` is a
    whitelist) and no remembered outcome carries it (``_NEVER_REMEMBERED``) — so
    a gate can neither make its own skip read as a prerequisite's, nor name a
    root. Non-empty, ``__post_init__`` writes the legacy flags in the not-pass
    direction (R-2): an older spine that drops the key still reads a skip.
    *Rejected:* the ``skip_reason`` text as the predicate (a gate could word
    its own skip into it, the reason P2.1-D4 rejected the same for
    ``unqualified``). One of the LAST two fields."""
    blocked_kind: str = ""
    """The first root's ``PrerequisiteKind`` — ``"errored"``, ``"failed"``,
    ``"skipped"``, ``"unqualified"`` or ``"not-registered"`` — beside
    ``blocked_by``, or ``""``. Spine-only on the same terms. What it carries
    that nothing else does: whether the root CRASHED. A guard is bound to its
    own claims, so its crash reached a claim bound only to the dependent as the
    dependent's skip — the missing tool's tone, its count, a JUnit
    ``<failure>`` — and invariant 2 says a crash reads louder than that
    (critique of the P2.2 design). ``claims.compose`` reads a prerequisite skip
    whose root errored as errored. The P2.2 design had rejected this field,
    reasoning that the kind can be derived from the root's reading — but a
    claim's composition sees the verdicts that cover it, and the root's does
    not cover a narrowly tagged claim. An older spine that drops it reads a
    plain skip: quieter, never a pass."""
    comparator: str = ""
    """Which side of ``limit`` passes, as the gate judged it: one of ``<=``,
    ``<``, ``>=``, ``>``, ``==``, ``!=`` or ``between`` (a band, whose upper
    bound the verdict does not carry), or ``""`` (P2.4-D5). The gate's own,
    written where it writes the limit — every bundled gate with a finite limit
    sets it (R-4: ``test_goalposts.EveryLimitNamesItsSide``), and a branch with
    no one-sided reading reports no limit. ``gates._stamp`` keeps a known one
    (an enum normalised to its value) and refuses anything else as an error
    naming the seven. Read by ``claims.margin`` (D-17): what slipped through
    before it, a margin inferred from ``(passed, measured, limit)`` guesses
    generously at equality, and ``min_wall``'s 7 mm against its 1.2 mm read
    -483% where it is 483% inside. Not a refusal for a third party's gate: with
    none the margin is ``no-comparator`` (R-10). *Rejected:* a
    ``GateSpec.comparator`` (two homes, and flow_regime's branches judge
    different limits); refusing ``between`` (a band binding of a modelica
    result would error where it read Checked — critique 9). An older spine drops
    it and reads a verdict with no margin: quieter, never a pass."""
    settles: str = ""
    """The quantity the gate measures, stamped from ``GateSpec.settles`` by the
    spine (``gates._stamp``, ``gates.blocked``, ``verdicts._as_spec``) like
    ``claims``, ``tier`` and ``pack`` — never the gate's own word. It makes the
    claim comparison a pure function of ``(claim, verdict)``
    (``claims.cross_check``, P2.4-D6): what slipped through the alternative, a
    ``{gate: settles}`` map handed to ``compose``, is a renderer with no
    registry skipping the comparison — more generous than one with a registry
    (invariant 12). An older spine drops it and compares nothing: never a
    pass it would not have read before. With ``comparator``, the LAST fields."""

    def __post_init__(self) -> None:
        # The operating-context mark never launders a fail (P2.4-D16): a token
        # of that kind on anything that is not a pass is dropped HERE, before
        # the line below writes `error` — whoever built the verdict (an older
        # path, a hand-built one, a test). A fail outside the context still
        # counts (R-3), and a skip or a crash keeps its own, louder, reading.
        # The mark's own R-2 error (written below, or by the spine beside it)
        # is not a crash of the verdict's.
        if self.unqualified and str(self.unqualified).startswith(CONTEXT_OUTSIDE):
            own = f"unqualified: {self.unqualified}"
            crashed = bool(self.error) and self.error != own
            if self.passed is not True or self.skipped or crashed or self.blocked_by:
                self.unqualified = ""
                if self.error == own:
                    self.error = ""
        # Degrade-closed (R-2): a verdict marked unqualified with no error would
        # read `outcome` from its pass flag, and a refusal must never be a pass.
        if self.unqualified and not self.error:
            self.error = f"unqualified: {self.unqualified}"
        # The same for a prerequisite skip: the flags say "skipped, not passed"
        # wherever the mark is set, so a reader that knows nothing of it still
        # reads the not-pass it means.
        if self.blocked_by:
            self.skipped = True
            self.passed = False

    @property
    def outcome(self) -> str:
        """What happened, as ONE word: ``"error"``, ``"skipped"``, ``"pass"`` or ``"fail"``.

        This is the only definition. ``ok``, :meth:`render` and the claim ladder in
        ``claims.resolve_status`` all read it, and every new reader (JUnit, the
        page, ``status --short``) must too. The same fact used to be re-derived in
        three places that agreed only because nobody had written the fourth.

        Precedence is error, then skipped, then the pass flag: a crash that also
        says skipped is still a crash, and a skip that also says passed is still
        not a pass.

        ``"pass"`` needs ``passed is True`` — the builtin, not anything truthy.
        What got through: ``ok`` was ``passed and not skipped and not error``, so
        ``Verdict(passed="no")`` was ok, and a gate returning ``{"passed":
        "false"}`` rendered ``[ok]``. :func:`atompipe.gates.run_gate` refuses a
        non-bool pass value as an error; this is the same rule for a verdict that
        never went through it (a hand-edited file, a third-party caller), where
        the honest reading of a pass flag nobody wrote as a bool is: not a pass.
        """
        if self.error:
            return "error"
        if self.skipped:
            return "skipped"
        if self.passed is True:
            return "pass"
        return "fail"

    @property
    def ok(self) -> bool:
        """True only if the gate actually ran and passed. A skip is not a pass."""
        return self.outcome == "pass"

    def render(self) -> str:
        """``[tag] gate : body`` — the body follows the outcome, never the first
        non-empty flag: an error's first line, a skip's reason, a pass's or a
        fail's detail. What slipped through (P2.0 F-1, F-10): the body was
        ``detail or skip_reason or error``, so a crash rendered its traceback
        (``run_gate`` keeps the stack's tail in ``detail``) and a verdict that
        said skipped AND errored rendered ``[ERR ] … : requires … (not
        installed)``, a crash in a missing tool's words."""
        outcome = self.outcome
        tag = _RENDER_TAG[outcome]
        if outcome == "error":
            body = (str(self.error).splitlines() or [""])[0]
        elif outcome == "skipped":
            body = self.skip_reason or self.detail or ""
        else:
            body = self.detail or ""
        return f"[{tag}] {self.gate}{(' : ' + body) if body else ''}"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Verdict":
        names = {f.name for f in dataclasses.fields(cls)}
        kw = {k: v for k, v in (data or {}).items() if k in names}
        if "tier" in kw and kw["tier"] is not None:
            kw["tier"] = Tier(int(kw["tier"]))
        kw["locators"] = [Locator.from_dict(l) if isinstance(l, dict) else l
                          for l in (kw.get("locators") or [])]
        return cls(**kw)


@dataclass
class NegativeControl(Record):
    """Proof that a gate is able to fail.

    A gate that cannot fail is a logger. A cable-routing validator once shipped
    green for a whole revision while returning a flag nobody read, with the cable
    geometrically inside a wall. The spine will not register a gate that does not
    declare one of these.

    `fixture` names a callable or a file that produces KNOWN-BAD input. Running
    the gate against it MUST produce a failing verdict; `atompipe gate selftest`
    enforces exactly that.
    """

    fixture: str                       # "selftest/holed_mesh.py" or "pack.mod:make_brick"
    expect: str = "fail"               # the gate must NOT pass on this input
    note: str = ""
    good: str = ""
    """The KNOWN-GOOD control (GLOSSARY §2), when the default is not it: a
    fixture reference spelled like ``fixture`` and handed exactly what the
    known-bad fixture is handed, returning the input the gate must PASS. Empty
    (the default) resolves to the pack's ``selftest/baseline.json`` for a pack's
    gate and to the project's ``selftest/known_good.py`` ``context(ctx)`` for a
    project's (``verdicts._good_host``); a project gate with neither is
    *known-bad shown*, not qualified. Declare one when the known-bad input
    reaches the gate through ``ctx.extra`` (cad-solid's meshes, sourcing's
    BOM): the two controls must hand the gate the same channel (PLAN D-26), or a
    gate that fails exactly when ``extra`` is not empty passes the baseline and
    fails its fixture and has shown nothing. What slipped through before it:
    only the known-bad half was ever run for a project gate, so one that failed
    everything (S-04) qualified by failing its own known-bad control. An older
    spine that drops the field reads the gate's entries as written by another
    version — undemonstrated, never a pass (R-2). The LAST field."""


@dataclass
class GateSpec(Record):
    """Registration record for one gate. The callable itself is not serialised."""

    id: str                             # "cad.watertight" - dotted, pack-prefixed
    title: str = ""
    claims: list[str] = field(default_factory=list)   # claim ids or claim TAGS it can serve
    tier: Tier = Tier.INSTANT
    pack: str = ""
    requires_tools: list[str] = field(default_factory=list)    # executables on PATH
    requires_python: list[str] = field(default_factory=list)   # importable modules
    negative_control: NegativeControl | None = None
    description: str = ""
    settles: str = ""                   # the quantity it measures, for gap matching
    entry: str = ""                     # "module:function" for out-of-process discovery
    requires_one_of: list[str] = field(default_factory=list)
    """ANY ONE of these is enough: ``"python:<module>"`` or ``"tool:<executable>"``.

    ``requires_tools`` and ``requires_python`` are ANDed, so a gate that needs any
    one of several unrelated back-ends could only probe for them in its own body
    and skip there — where ``gates.availability`` cannot see it. That is what got
    through: a boolean-engine probe inside a mesh gate skipped the gate's own
    baseline AND its own negative control on a machine with the mesh library but
    no engine, while availability said the tooling was present, and the test of
    the controls filed it as honestly blocked. Declared here, the disjunction is
    availability's to judge, and a skip means only what it says.

    One field with a kind prefix, not two lists: two fields are two places to
    forget. The LAST field, so every positional ``GateSpec(...)`` still works and
    an older spine that drops it reads a spec that requires less, never one that
    passes more."""
    needs: list[str] = field(default_factory=list)
    """The gates that must be established before this one's verdict counts —
    its **prerequisites** (P2.2-D1), declared ``@gate(needs=[...])``: a
    validity guard before the analyses it guards. Exact gate ids (no glob, no
    tag: a tag-bound set moves when a pack is installed, and the graph and its
    cycles with it); for a pack, the pack's own gates only (``packs.validate``,
    D14); refused at registration when it is malformed, closes a cycle, or names
    a costlier tier than this gate's (``gates.Registry.register``). A need not
    registered yet is allowed (a missing pack must not become a load crash) and
    reads "not registered" at the sweep.

    What it does (``gates.prerequisite_root``, D5-D7): under a prerequisite that
    failed, errored, skipped, is unqualified or is not registered, this gate is
    not run and reads Skipped; under one invalidated or unrun, its verdict is
    kept and reads Stale. What slipped through before it (S-51): a claim tagged
    only ``deflection`` read Checked on a beam whose guard reported
    Euler-Bernoulli omitting 32% of the deflection.

    NOT part of rho (``verdicts.SPEC_FIELDS_IN_RHO``, D-04): the measurement is
    a function of its own inputs, so a guard that recovers re-runs nothing.
    *Rejected:* a separate edges file (two homes for one fact); ``after=``
    (sequencing's word — P4 reorders; a prerequisite is semantic)."""
    operating_context: dict = field(default_factory=dict)
    """The range of its read set this evaluator was qualified on (PLAN-v0.14
    §1.5, GLOSSARY §2 *operating context*; P2.4-D14): ``{key: (lo, hi)}``,
    closed intervals, ``None`` for an open end, declared
    ``@gate(operating_context={"load_n": (0.0, 40.0)})``. A key is spelled as
    the gate reads it — ``GateContext.param``'s rule, scoped first — and must
    be READ by every passing run (``gates.run_gate`` errors a pass that never
    read one: the context is part of the read set, so a change inside the range
    re-keys the verdict, critique 3 of the design). Judged by the spine on the
    CURRENT values, over passes only (``gates.context_breach``,
    ``verdicts._contexted``), on the spelling the run read (critique 4):
    outside it a pass does not count — its claim reads Gap, or Assumed under
    an owned ``Claim.fallback`` — and a fail still does (R-3). Its known-good
    control must lie inside it, or the evaluator is unqualified (D19).
    Registration refuses a malformed one (R-10: the new field only). NOT part
    of rho (``verdicts.SPEC_FIELDS_IN_RHO``, P2.2-D1's argument for ``needs``):
    it decides whether a verdict counts, not what was measured; an edit re-keys
    the declaring file once through its code digest. *Rejected:* categorical
    contexts (no bundled need); a context derived from the walk (the paper's
    word is *declared*); per-claim contexts (the range is the evaluator's);
    ``inf`` as a bound (``None`` says it). The LAST field."""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "GateSpec":
        names = {f.name for f in dataclasses.fields(cls)}
        kw = {k: v for k, v in (data or {}).items() if k in names}
        if "tier" in kw and kw["tier"] is not None:
            kw["tier"] = Tier(int(kw["tier"]))
        nc = kw.get("negative_control")
        kw["negative_control"] = NegativeControl.from_dict(nc) if isinstance(nc, dict) else nc
        context = kw.get("operating_context")
        if isinstance(context, dict):
            # JSON carries a pair as a list; in memory it is the tuple the
            # registry stores, so a round trip compares equal.
            kw["operating_context"] = {
                str(k): tuple(None if b is None else float(b) for b in v)
                if isinstance(v, (list, tuple)) else v for k, v in context.items()}
        return cls(**kw)


# --------------------------------------------------------------------------- #
# capability gaps
# --------------------------------------------------------------------------- #
@dataclass
class ToolCandidate(Record):
    """One option for closing a capability gap."""

    name: str
    kind: str = ""                   # "analytic" | "solver" | "service" | "manual"
    why: str = ""
    cost: str = ""                   # "~2 GB, ~20 min install, ~15 min/run"
    install: str = ""                # the actual command, pinned
    licence: str = ""
    pack_would_be: str = ""          # the pack name this becomes


@dataclass
class Need(Record):
    """A claim with no gate: the trigger for the extension protocol.

    This is the object that lets the system grow without prebaked contingencies.
    The agent does not guess a tool; it names the unvalidated physical quantity,
    classifies it, and proposes candidates with honest costs.
    """

    id: str
    claim_ids: list[str] = field(default_factory=list)
    quantity: str = ""               # "directional stability at cruise"
    claim_class: str = ""            # "fluid-dynamics" | "structural" | "thermal" ...
    status: NeedStatus = NeedStatus.OPEN
    candidates: list[ToolCandidate] = field(default_factory=list)
    chosen: str = ""
    note: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Need":
        names = {f.name for f in dataclasses.fields(cls)}
        kw = {k: v for k, v in (data or {}).items() if k in names}
        kw["status"] = NeedStatus(kw.get("status") or NeedStatus.OPEN)
        kw["candidates"] = [ToolCandidate.from_dict(c) if isinstance(c, dict) else c
                            for c in (kw.get("candidates") or [])]
        return cls(**kw)


# --------------------------------------------------------------------------- #
# input artifacts:  intake is not only a conversation
# --------------------------------------------------------------------------- #
@dataclass
class Extraction(Record):
    """What was actually read OUT of an ingested artifact.

    An artifact nobody extracted from is decoration. This record is what makes a
    sketch traceable: a generated layout whose header reads 'digitised from
    inputs/sketches/panel.png, legends corrected against inputs/references/unit.png'
    carries the only sentence that lets anyone audit it two months later.
    """

    what: str                        # "hull beam at midship reads 148 mm"
    grounds: list[str] = field(default_factory=list)   # param names and/or claim ids
    confidence: str = "stated"       # "measured" | "scaled" | "stated" | "inferred"
    note: str = ""


@dataclass
class InputArtifact(Record):
    """A piece of evidence the user supplied."""

    id: str
    path: str = ""                   # repo-relative; empty for LINK
    url: str = ""
    kind: ArtifactKind = ArtifactKind.OTHER
    description: str = ""
    sha256: str = ""
    bytes: int = 0
    added: str = ""                  # ISO date, supplied by the caller
    extractions: list[Extraction] = field(default_factory=list)
    licence: str = ""                # provenance matters: ingested media gets redistributed
    note: str = ""

    @property
    def extracted(self) -> bool:
        return bool(self.extractions)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "InputArtifact":
        names = {f.name for f in dataclasses.fields(cls)}
        kw = {k: v for k, v in (data or {}).items() if k in names}
        kw["kind"] = ArtifactKind(kw.get("kind") or ArtifactKind.OTHER)
        kw["extractions"] = [Extraction.from_dict(e) if isinstance(e, dict) else e
                             for e in (kw.get("extractions") or [])]
        return cls(**kw)


# --------------------------------------------------------------------------- #
# views: the project site, and the debugging surface
# --------------------------------------------------------------------------- #
@dataclass
class Locator(Record):
    """WHERE a verdict applies, so the site can show you instead of telling you.

    This is the mechanism that turns a project page into a debugging tool. A clash
    gate that reports "back_left interferes with grip_lid_left by 0.41 mm^3" is a
    sentence you have to go and act on by hand. The same verdict carrying two
    locators lights both parts up in the assembly viewer, at the pose where it
    happens, and the reader is looking at the problem a second later.

    A gate attaches locators only when it genuinely knows the position. Inventing
    one puts a confident red highlight on the wrong part, which is worse than no
    highlight at all, so an unlocatable failure carries none and the site says the
    verdict has no anchor.
    """

    view: str
    """View id this addresses. A locator naming a view that does not exist is
    reported by `atompipe site build` rather than silently dropped — a gate that
    thinks it is drawing and is not looks identical to a gate that found nothing."""

    target: str = ""
    """Node name (model3d), hotspot id (image), series key (chart), row id (table).
    Empty means the whole view."""

    kind: str = "part"
    """part | point | region | series | row | face"""

    label: str = ""
    """What to show on the pin. One line — the measured value and the limit."""

    severity: str = ""
    """Defaults to the verdict's own outcome. Set it only to mark one locator of a
    passing verdict as a warning, or the worst offender among many."""

    position: list[float] | None = None
    """Explicit xyz in the view's own space, when the target is not a named node
    (a contact point, a hot spot, a stress peak)."""

    value: float | None = None
    """The measured quantity at this location, when it varies per-locator — the
    per-pair overlap volume, the per-face overhang angle."""


@dataclass
class View(Record):
    """One visual artifact the site can render, and address verdicts onto.

    Views are produced by packs (a CAD pack emits the assembly; an analytic pack
    emits the curve its gate measures a point on) and by the project itself. The
    site composes whatever it is given — it has no idea what domain it is looking
    at, which is what lets the same site serve a boat, a bracket and a collector.
    """

    id: str
    kind: ViewKind = ViewKind.IMAGE
    title: str = ""
    src: str = ""
    """Asset path relative to the site's data directory. Empty for a view whose
    content is entirely in `data` (a small chart or table)."""

    description: str = ""
    gates: list[str] = field(default_factory=list)
    """Gates whose locators address this view. Lets the site offer 'show me the
    gates that touch this' without scanning every verdict."""

    data: dict[str, Any] = field(default_factory=dict)
    """Inline content for small views: chart series, table rows, hotspot lists."""

    meta: dict[str, Any] = field(default_factory=dict)
    """Kind-specific: explode manifest path and node list for model3d; axis labels
    and units for chart; natural size for image."""

    pack: str = ""
    order: int = 100
    """Display order. Lower first; the assembly usually wants to be first."""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "View":
        names = {f.name for f in dataclasses.fields(cls)}
        kw = {k: v for k, v in (data or {}).items() if k in names}
        kw["kind"] = ViewKind(kw.get("kind") or ViewKind.IMAGE)
        return cls(**kw)


# --------------------------------------------------------------------------- #
# decisions
# --------------------------------------------------------------------------- #
@dataclass
class Decision(Record):
    """One entry in the decision log. Newest first, names what LOST."""

    id: str
    title: str = ""
    when: str = ""
    summary: str = ""
    rejected: list[Rejected] = field(default_factory=list)
    params_changed: list[str] = field(default_factory=list)
    claims_changed: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    body: str = ""                   # long-form markdown

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Decision":
        names = {f.name for f in dataclasses.fields(cls)}
        kw = {k: v for k, v in (data or {}).items() if k in names}
        kw["rejected"] = [Rejected.from_dict(r) if isinstance(r, dict) else r
                          for r in (kw.get("rejected") or [])]
        return cls(**kw)


# --------------------------------------------------------------------------- #
# packs
# --------------------------------------------------------------------------- #
@dataclass
class PackManifest(Record):
    """pack.json - tier 1 disclosure. Small enough to hold 40 of these in context.

    Three-tier progressive disclosure:
      tier 1  this manifest        ~20 words, always loadable
      tier 2  PACK.md              ~150 lines, loaded when the domain is relevant
      tier 3  references/*.md      loaded only for the specific task at hand
    """

    name: str
    version: str = "0.1.0"
    description: str = ""            # ONE line. What claims it can settle.
    settles: list[str] = field(default_factory=list)     # quantity vocabulary for gap matching
    claim_classes: list[str] = field(default_factory=list)
    provides_gates: list[str] = field(default_factory=list)
    provides_views: list[str] = field(default_factory=list)
    """View ids this pack's `views/*.py` register. Tier 1 because a reader
    deciding whether to install a pack wants to know it comes with a viewer, and
    because it is what `Locator.view` in this pack's verdicts will address:
    declared here, the pair can be checked without importing anything."""
    provides_generators: list[str] = field(default_factory=list)
    key_scope: str = ""
    """Prefix this pack's projection keys may carry, so two packs that want the
    same word can both be satisfied: a project publishes `fdm.bbox_mm` and
    `cad.bbox_mm` and each gate reads the one it means. Usually EMPTY — it is
    derived from the dotted gate ids (`fdm.bed_fit` -> `fdm`) by
    `packs.key_scope`, and stating it twice is how the two go out of step. Set it
    only when a pack's gate ids do not share one prefix."""
    requires_tools: list[str] = field(default_factory=list)
    requires_python: list[str] = field(default_factory=list)
    max_tier: Tier = Tier.INSTANT
    lenses: list[str] = field(default_factory=list)      # adversarial review dimensions
    authors: list[str] = field(default_factory=list)
    licence: str = "Apache-2.0"
    homepage: str = ""
    origin: str = ""                 # "extracted from a production project" / "built by protocol"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PackManifest":
        names = {f.name for f in dataclasses.fields(cls)}
        kw = {k: v for k, v in (data or {}).items() if k in names}
        if "max_tier" in kw and kw["max_tier"] is not None:
            kw["max_tier"] = Tier(int(kw["max_tier"]))
        return cls(**kw)


@dataclass
class KeyCollision(Record):
    """One projection key that two installed packs read as different quantities.

    Packs share one flat namespace — the model's projection — and nothing stops
    two of them from wanting the same word for different things. The observed
    case: `cad-solid` reads `bbox_mm` as the ASSEMBLY envelope, `fdm-print` reads
    it as ONE PART in print orientation, and a project with both (every
    mechanical project) can only satisfy one. Published as the assembly it
    measured a 480 mm boat against a 220 mm printer bed and FAILED; published as
    the part, the envelope gate skipped and the claim went unheld. Neither pack
    was wrong and neither could detect the other.

    This record is what makes that detectable instead of discoverable: `atompipe
    doctor` diffs the installed packs' key vocabularies and reports the overlaps,
    naming both packs, what each means by the key, and the unambiguous spelling
    to publish instead.

    `live` is the difference between a latent clash and one that is happening
    here: it is True when the project's own projection actually publishes the
    bare key, so one of these two packs is reading a number that was written for
    the other.
    """

    key: str
    packs: list[str] = field(default_factory=list)
    meanings: dict[str, str] = field(default_factory=dict)   # pack -> its own one-liner
    scoped: dict[str, str] = field(default_factory=dict)     # pack -> "fdm.bbox_mm"
    primary: dict[str, str] = field(default_factory=dict)    # pack -> "part_bbox_mm"
    live: bool = False

    def fix(self) -> str:
        """The one sentence that resolves it, naming both spellings."""
        scoped = ", ".join(self.scoped[p] for p in self.packs if p in self.scoped)
        primary = ", ".join(f"{p}: {self.primary[p]}" for p in self.packs
                            if self.primary.get(p) and self.primary[p] != self.key)
        parts = [f"publish {scoped}"] if scoped else []
        if primary:
            parts.append(f"or use each pack's own primary key ({primary})")
        return " ".join(parts) or "scope the key per pack"


# --------------------------------------------------------------------------- #
# the project
# --------------------------------------------------------------------------- #
@dataclass
class ProjectMeta(Record):
    name: str = ""
    summary: str = ""
    created: str = ""
    revision: str = "v0.1"
    model_entry: str = ""            # "model/boat.py" - the single source of truth
    packs: list[str] = field(default_factory=list)
    spine_version: str = ""


@dataclass
class Ledger(Record):
    """The whole project state, in memory.

    On disk it is one file per record (`RECORD_KINDS`) plus
    `.atompipe/project.json` for `meta`; `store.load` assembles this from them.
    `.atompipe/ledger.json` is the GENERATED index of those files — the whole
    project in one read for an agent, as the single ledger file was — and is
    never read back for truth. `verdicts`, `Claim.gates` and `Param.gates` are
    filled in memory by readers and never written to a record.
    """

    meta: ProjectMeta = field(default_factory=ProjectMeta)
    claims: list[Claim] = field(default_factory=list)
    params: list[Param] = field(default_factory=list)
    inputs: list[InputArtifact] = field(default_factory=list)
    needs: list[Need] = field(default_factory=list)
    decisions: list[Decision] = field(default_factory=list)
    verdicts: list[Verdict] = field(default_factory=list)   # latest per gate
    views: list[View] = field(default_factory=list)
    # No run record. The ledger used to carry the previous sweep's model and
    # inputs hashes, and staleness was ONE comparison against them: a model that
    # failed to import compared equal, and three claims read PROVEN for a design
    # that could not be built (S-21); ingesting one unread datasheet staled every
    # measurable claim (S-33). A verdict is current by its own inputs now
    # (`verdicts.freshness`), and when a check last ran is
    # `.atompipe/cache/last_check.json`, untracked. `from_dict` ignores the old
    # key, so a ledger an older spine wrote still loads (R-2), and the next save
    # simply does not carry it.
    removed: tuple = ()
    """In memory only, never written (``to_dict`` drops it): a ``Claim`` per
    ``results/<id>.json`` that no ``claims/<id>.json`` holds — assembled by
    ``store`` with the file's results, ``kind`` physical, required, and no
    statement. ``verdicts.view`` puts each one holding a fail among the claims
    every reader composes, so its fail reads Failing and stops ``check`` (R-3:
    a fail counts across every edit, a claim file's rename or deletion
    included); ``verdicts.track_record`` and ``doctor`` read every one. What
    slipped through (review of P2.5a): a claim file renamed away from its
    results left a sealed fail no reader looked at — the claim stopped failing,
    ``check`` passed, and the evaluator's track record lost its contradiction.
    *Rejected:* a claim synthesized into ``claims`` by ``store`` (``save``
    would write it back as a claim file nobody wrote); refusing every command
    (a dropped requirement is an ordinary edit, and the fail it leaves is a
    fact to show, not a file to repair)."""

    def to_dict(self) -> dict[str, Any]:
        out = super().to_dict()
        out.pop("removed", None)
        return out

    # -- lookups ---------------------------------------------------------- #
    def claim(self, cid: str) -> Claim | None:
        return next((c for c in self.claims if c.id == cid), None)

    def param(self, name: str) -> Param | None:
        return next((p for p in self.params if p.name == name), None)

    def artifact(self, aid: str) -> InputArtifact | None:
        return next((a for a in self.inputs if a.id == aid), None)

    def need(self, nid: str) -> Need | None:
        return next((n for n in self.needs if n.id == nid), None)

    def verdict(self, gate_id: str) -> Verdict | None:
        return next((v for v in self.verdicts if v.gate == gate_id), None)

    def verdicts_for(self, cid: str) -> list[Verdict]:
        return [v for v in self.verdicts if cid in (v.claims or [])]

    def upsert_verdict(self, verdict: Verdict) -> None:
        for i, existing in enumerate(self.verdicts):
            if existing.gate == verdict.gate:
                self.verdicts[i] = verdict
                return
        self.verdicts.append(verdict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Ledger":
        data = data or {}
        return cls(
            meta=ProjectMeta.from_dict(data.get("meta") or {}),
            claims=[Claim.from_dict(c) for c in data.get("claims") or []],
            params=[Param.from_dict(p) for p in data.get("params") or []],
            inputs=[InputArtifact.from_dict(a) for a in data.get("inputs") or []],
            needs=[Need.from_dict(n) for n in data.get("needs") or []],
            decisions=[Decision.from_dict(d) for d in data.get("decisions") or []],
            verdicts=[Verdict.from_dict(v) for v in data.get("verdicts") or []],
            views=[View.from_dict(v) for v in data.get("views") or []],
        )


# --------------------------------------------------------------------------- #
# records as files (checkpoint 1.3)
# --------------------------------------------------------------------------- #
#: Each record directory under the project root -> the kind a file in it holds.
#: One file per record, the stem is the id (`claims/C1.json` is claim C1), so a
#: branch that edits one claim conflicts with nothing but an edit of that claim.
#: What slipped through with one `ledger.json`: every command rewrote the whole
#: file, so a candidate branch that touched one number conflicted with every
#: other branch on the same file — the merge-conflict argument for one file
#: inverts once a branch is a candidate (brief). `results/<claim-id>.json` holds
#: a claim's PhysicalResults as `{"results": [...]}`, append-only (D-11), apart
#: from the claim so a candidate's refutation survives a trade overlay (Q1.10).
#: `views/` holds viewgens too; only its `*.json` files are records.
#: The order is `store.RECORD_DIRS`' and a test holds the two equal. Rejected: a
#: `ledger.json` sharded by kind (`claims.json`, …) — the same conflict per kind.
RECORD_KINDS: dict[str, type] = {
    "claims": Claim,
    "params": Param,
    "decisions": Decision,
    "needs": Need,
    "inputs": InputArtifact,
    "results": PhysicalResult,
    "views": View,
}

#: Keys a record FILE may never carry, per kind (the class name), each with the
#: home that owns the fact instead; `"*"` applies at every level of every kind.
#: The dataclass fields stay (tests:H4: `claims.resolve_status` still reads
#: `Claim.gates` in memory) — only their on-disk home goes. A copy on disk is a
#: second source that goes stale the moment the owner moves: `check` rewrote
#: `claim.gates` into the ledger on every run (S-37), and a param record's
#: `value` kept saying 7 after the model said 8 (S-39). `{model}` is filled with
#: the project's `model_entry`, because "the model owns it" is only useful when it
#: says which file. `Claim.physical_result` is here because its home moved to
#: `results/<id>.json`; two homes for a result is how one of them gets believed.
#: `independence` is NEVER a field the proposer fills in (brief): it is derived
#: from origin, and a record that states its own would launder a claim of it.
#: Rejected: dropping these keys silently on read — the lenient reader is exactly
#: how a typo'd key vanished and was then erased from disk (S-40).
FORBIDDEN_KEYS: dict[str, dict[str, str]] = {
    "Claim": {
        "gates": "derived from gate coverage — the registry says which gates cover a claim",
        "physical_result": "a claim's results live in results/<id>.json, append-only",
        "results": "a claim's results live in results/<id>.json, append-only",
        "attributions": "an owner or an authority is recorded in results/<id>.json by "
                        "`atompipe claim physical <id> assume`, typed in their own shell",
        "standing": "the resolver judges a result's standing on every read",
    },
    "Param": {
        "value": "the model ({model}) owns it",
        "derived_from": "the model ({model}) owns it",
        "gates": "derived — the gates that read it when they last ran",
        "changed_in": "derived — the decisions that name it in params_changed",
    },
    "InputArtifact": {
        "bytes": "computed from the file",
    },
    "*": {
        "independence": "derived from origin (pack, human, agent session, external "
                        "solver), never a field the proposer fills in",
    },
}

#: `(class, field)` pairs the record writer writes even at their defaults. The
#: writer omits every other default so a record says only what was decided — but
#: a claim file without its `kind`, or a limit without its direction, is
#: unreadable to the human editing it, and phase-1's C1 example writes both.
#: Rejected: omitting every default (C1 would lose `kind` and `comparator`); writing
#: every default (a claim file would carry `"physical_result": null`, `"note": ""`
#: and seven more keys that decide nothing).
ALWAYS_WRITTEN: frozenset[tuple[str, str]] = frozenset({
    ("Claim", "kind"),
    ("Acceptance", "comparator"),
})


__all__ = [
    "StrEnum", "ClaimKind", "ClaimStatus", "BLOCKING_STATUSES", "Tier", "ViewKind",
    "ArtifactKind", "EXT_KIND_HINTS", "NeedStatus", "Comparator",
    "Record", "slugify", "sha256_file",
    "Rejected", "Param", "Acceptance", "PhysicalResult", "AttributionRecord",
    "EntryStanding", "Standing", "Terminal", "TERMINALS_BY_KIND", "Claim",
    "Verdict", "NegativeControl", "GateSpec", "PrerequisiteKind", "CONTEXT_OUTSIDE",
    "ToolCandidate", "Need", "Extraction", "InputArtifact", "Decision",
    "Locator", "View", "PackManifest", "KeyCollision", "ProjectMeta", "Ledger",
    "RECORD_KINDS", "FORBIDDEN_KEYS", "ALWAYS_WRITTEN",
]
