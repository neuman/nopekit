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
    """Taken on faith and recorded so it stays visible. An assumption that nobody
    wrote down is the thing that sinks the build."""


class ClaimStatus(StrEnum):
    """Resolved state of one claim. Derived, never stored by hand."""

    PASS = "pass"                 # every covering gate ran and passed
    FAIL = "fail"                 # at least one covering gate failed
    STALE = "stale"               # gates passed, but inputs changed since
    UNCLAIMED = "unclaimed"       # no gate covers it  -> a capability gap
    BLOCKED = "blocked"           # a gate covers it but its tooling is missing
    PENDING = "pending"           # gates exist, never run
    UNVERIFIED = "unverified"     # physical: awaiting a real-world result
    VERIFIED = "verified"         # physical: a human recorded a real-world pass
    REFUTED = "refuted"           # physical: a human recorded a real-world fail
    ASSERTED = "asserted"         # assumption: standing, unevidenced


#: statuses that must block an irreversible spend (ordering a board, buying stock)
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
    EXTERNAL = 3    # CI, a fab house, a lab, a human with calipers.


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
    """A real-world test result a human recorded against a PHYSICAL claim."""

    passed: bool
    when: str = ""                   # ISO date, supplied by the caller
    who: str = ""
    detail: str = ""
    evidence: list[str] = field(default_factory=list)   # photo / log paths


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
    critical: bool = True            # false = nice-to-have, never blocks a spend
    physical_result: PhysicalResult | None = None
    note: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Claim":
        names = {f.name for f in dataclasses.fields(cls)}
        kw = {k: v for k, v in (data or {}).items() if k in names}
        kw["kind"] = ClaimKind(kw.get("kind") or ClaimKind.MEASURABLE)
        kw["acceptance"] = Acceptance.from_dict(kw.get("acceptance") or {})
        pr = kw.get("physical_result")
        kw["physical_result"] = PhysicalResult.from_dict(pr) if isinstance(pr, dict) else pr
        return cls(**kw)


# --------------------------------------------------------------------------- #
# gates and verdicts
# --------------------------------------------------------------------------- #
#: ``Verdict.outcome`` -> the four-character tag ``Verdict.render`` prints. Four
#: characters so a sweep's lines align; the strings are the ones every grep and
#: every reader already knows (``[FAIL]``), so they do not change with the rule
#: that picks them.
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
        tag = _RENDER_TAG[self.outcome]
        body = self.detail or self.skip_reason or self.error or ""
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

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "GateSpec":
        names = {f.name for f in dataclasses.fields(cls)}
        kw = {k: v for k, v in (data or {}).items() if k in names}
        if "tier" in kw and kw["tier"] is not None:
            kw["tier"] = Tier(int(kw["tier"]))
        nc = kw.get("negative_control")
        kw["negative_control"] = NegativeControl.from_dict(nc) if isinstance(nc, dict) else nc
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
class RunMeta(Record):
    """Bookkeeping for one gate sweep."""

    when: str = ""
    tier: int = 0
    model_hash: str = ""             # hash of the resolved parameter projection
    inputs_hash: str = ""            # hash over ingested artifact digests
    spine_version: str = ""
    duration_s: float = 0.0


@dataclass
class Ledger(Record):
    """The whole project state. Persisted as .atompipe/ledger.json.

    Deliberately one file: an agent reads it once and holds the entire shape of
    the project - claims, evidence, gaps, decisions - in a few hundred lines.
    """

    meta: ProjectMeta = field(default_factory=ProjectMeta)
    claims: list[Claim] = field(default_factory=list)
    params: list[Param] = field(default_factory=list)
    inputs: list[InputArtifact] = field(default_factory=list)
    needs: list[Need] = field(default_factory=list)
    decisions: list[Decision] = field(default_factory=list)
    verdicts: list[Verdict] = field(default_factory=list)   # latest per gate
    views: list[View] = field(default_factory=list)
    last_run: RunMeta = field(default_factory=RunMeta)

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
            last_run=RunMeta.from_dict(data.get("last_run") or {}),
        )


__all__ = [
    "StrEnum", "ClaimKind", "ClaimStatus", "BLOCKING_STATUSES", "Tier", "ViewKind",
    "ArtifactKind", "EXT_KIND_HINTS", "NeedStatus", "Comparator",
    "Record", "slugify", "sha256_file",
    "Rejected", "Param", "Acceptance", "PhysicalResult", "Claim",
    "Verdict", "NegativeControl", "GateSpec",
    "ToolCandidate", "Need", "Extraction", "InputArtifact", "Decision",
    "Locator", "View", "PackManifest", "KeyCollision", "ProjectMeta",
    "RunMeta", "Ledger",
]
