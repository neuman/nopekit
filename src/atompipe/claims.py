# SPDX-License-Identifier: Apache-2.0
"""atompipe.claims — status derivation, and the honesty property of the ledger.

Nothing in this module writes anything. It reads a `Ledger` (and, where it can,
a live gate registry) and derives: what is settled, what is not, what is not
even *checkable* yet, and which of those must stop a user from spending money.

The one rule that matters more than all the others:

    **A SKIP IS NEVER A PASS. AN ERROR IS NEVER A PASS.**

That single line is the difference between this system and a plausible-sounding
one. A cable-routing validator once shipped green for a whole revision while
returning a flag nobody read, with the cable geometrically inside a wall — a
report that had counted "the gate did not object" as "the claim is proven".
Every branch below is written so the *absence* of evidence can never be spelled
`PASS`; it gets its own status (`BLOCKED`, `PENDING`, `UNCLAIMED`, `UNVERIFIED`)
and stays visible in the readiness report until somebody does the work.

Three structural notes:

* **Tag binding.** A gate binds to a claim by claim *id* or by claim *tag*
  (`GateSpec.claims` holds either). Tag binding is what lets a pack author ship
  `cad.watertight` bound to `"manufacturable"` and have it cover claims that did
  not exist when the pack was written. The rule lives in exactly one function
  here — `covers()` — because two copies of a matching rule is two answers to
  "is this claim covered", and the optimistic copy always wins the argument.

* **`claim.gates` is a cached opinion; the registry is the truth.** The ledger
  field goes stale the moment a pack is installed, removed, or renamed. So the
  functions that are handed a registry (`coverage`, `find_gaps`, `blocking`,
  `summarise`) use it, and `statuses(ledger)` — which the contract gives no
  registry — falls back to `claim.gates`, and says so. Pass `registry=` to
  `statuses` when you have one.

* **Purity.** No clock, no randomness, no disk. `stale` comes in as a flag from
  whoever compared the model hash; these functions must not decide staleness for
  themselves, or the same ledger would resolve differently on two machines.
"""
from __future__ import annotations

import re
from collections.abc import Iterable as _IterableABC
from dataclasses import replace
from typing import Any, Iterable

from .models import (
    BLOCKING_STATUSES,
    Claim,
    ClaimKind,
    ClaimStatus,
    GateSpec,
    Ledger,
    Need,
    NeedStatus,
    Verdict,
    slugify,
)
from .util import AtompipeError, iter_suffix_unique


__all__ = [
    "covers",
    "covering_verdicts",
    "resolve_status",
    "explaining_verdict",
    "statuses",
    "coverage",
    "effective_gates",
    "find_gaps",
    "blocking",
    "summarise",
    "next_claim_id",
]


#: A claim id that can be embedded in a derived id (a Need id, a filename) with
#: no mangling. Anything else gets slugified first.
_PLAIN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")

#: `next_claim_id` only counts ASCII digits. `str.isdigit()` is True for "١٢"
#: and `int()` happily parses it, which would silently mint ids that no
#: `startswith(prefix)` scan on a later run can find again.
_ASCII_INT = re.compile(r"[0-9]+")


def _kind(claim: Claim) -> ClaimKind:
    """The claim's kind as an enum, whatever the ledger happened to hold.

    `Claim.from_dict` coerces, but a `Claim(kind="physical")` built by hand in a
    CLI path or a test keeps a plain `str`, and `str is ClaimKind.PHYSICAL` is
    False — which would have silently resolved a physical claim down the
    MEASURABLE ladder and printed PENDING for something no gate can ever settle.
    Comparing kinds in exactly one place removes that whole class of bug.

    An unrecognised kind raises `AtompipeError`: `.atompipe/ledger.json` is meant
    to be hand-editable, so a typo'd `"mesurable"` is a user mistake, and a bare
    ValueError three frames into a report tells them nothing.
    """
    kind = claim.kind
    if isinstance(kind, ClaimKind):
        return kind
    try:
        return ClaimKind(kind)
    except ValueError as exc:
        allowed = ", ".join(k.value for k in ClaimKind)
        raise AtompipeError(
            f"claim {claim.id!r} has unknown kind {kind!r}; expected one of: {allowed}"
        ) from exc


# --------------------------------------------------------------------------- #
# the coverage rule — one definition, used everywhere
# --------------------------------------------------------------------------- #
def covers(spec: GateSpec, claim: Claim) -> bool:
    """Does `spec` bind to `claim`?

    True when the claim's id appears in `spec.claims`, **or** when any of the
    claim's tags does. `GateSpec.claims` is deliberately a mixed list of ids and
    tags: id binding is how a project wires a gate to the one claim it was
    written for, and tag binding is how a *pack* ships gates that bind to claims
    the pack author never saw. A pack declaring `claims=["manufacturable"]`
    covers every claim the project later tags `manufacturable`, with no edit to
    the pack and no edit to the gate.

    An empty `spec.claims` covers **nothing**, not everything. A gate that
    declares no claims settles no claims; treating it as a wildcard would let a
    single misregistered gate mark an entire project proven.
    """
    binding = set(spec.claims or ())
    if not binding:
        return False
    if claim.id and claim.id in binding:
        return True
    return any(tag in binding for tag in (claim.tags or ()))


def covering_verdicts(claim: Claim, verdicts: Iterable[Verdict]) -> list[Verdict]:
    """The verdicts that speak to `claim`, by the same id-or-tag rule as `covers`.

    `run_gate` copies `claims` onto the verdict straight from the spec, so a
    verdict from a tag-bound gate carries the *tag* ("manufacturable"), not the
    claim id. `Ledger.verdicts_for(cid)` matches on id only and will therefore
    miss exactly those — use this when you need the evidence behind a claim
    (a report row, say), and hand it the whole `ledger.verdicts` list.

    Filtering is idempotent: passing an already-filtered list is harmless, which
    is why `resolve_status` can accept either.
    """
    out: list[Verdict] = []
    for v in verdicts or ():
        names = set(v.claims or ())
        if not names:
            continue
        if (claim.id and claim.id in names) or any(t in names for t in (claim.tags or ())):
            out.append(v)
    return out


# --------------------------------------------------------------------------- #
# the precedence ladder
# --------------------------------------------------------------------------- #
def resolve_status(
    claim: Claim,
    verdicts: Iterable[Verdict],
    *,
    stale: bool = False,
) -> ClaimStatus:
    """Resolve one claim to a single honest status.

    `verdicts` may be the whole `ledger.verdicts` list — this function filters it
    with `covering_verdicts`, so callers never have to reproduce the tag rule.

    `stale=True` means "the model moved since these verdicts were recorded"
    (the caller compared `model_hash`). A stale pass is not a pass: it is the
    exact shape of the failure where a report is green against a model nobody
    has re-checked. This function will not decide staleness for itself; that
    needs the model, and a claim resolver that reads the model would resolve the
    same ledger differently on two machines.

    Precedence, exactly as specified in the spine contract:

    * ASSUMPTION -> ASSERTED. Standing and unevidenced, by definition. A gate
      result would not change it; the honesty is in it being *visible*.
    * PHYSICAL   -> VERIFIED / REFUTED when a human recorded a `physical_result`,
      else UNVERIFIED. No simulation ever launders a physical claim into green —
      "the printed seam is watertight" is settled by water, or not at all.
    * MEASURABLE -> the ladder below.

    For MEASURABLE claims, in order:

    1. no covering gate at all                 -> UNCLAIMED  (a capability gap)
    2. every covering verdict was skipped      -> BLOCKED    (tooling missing)
    3. gates exist but none has a verdict      -> PENDING    (never run)
    4. any covering verdict failed or errored  -> FAIL
    5. all ran and passed, and `stale`         -> STALE
    6. otherwise                               -> PASS

    **A SKIP IS NEVER A PASS. AN ERROR IS NEVER A PASS.** Rungs 2 and 4 exist
    only to make that true, and every one of BLOCKED/PENDING/UNCLAIMED/FAIL/STALE
    is in `BLOCKING_STATUSES` — so no amount of not-having-checked can let a
    critical claim through `blocking()`.
    """
    kind = _kind(claim)

    if kind is ClaimKind.ASSUMPTION:
        return ClaimStatus.ASSERTED

    if kind is ClaimKind.PHYSICAL:
        result = claim.physical_result
        if result is None:
            return ClaimStatus.UNVERIFIED
        return ClaimStatus.VERIFIED if result.passed else ClaimStatus.REFUTED

    mine = covering_verdicts(claim, verdicts)

    # A gate is known to exist either because a verdict came back from one, or
    # because the claim records one. `claim.gates` is how a claim with a
    # registered-but-never-run gate reads PENDING instead of UNCLAIMED; the
    # registry-aware callers below top it up with live coverage before calling.
    known_gates = [g for g in (claim.gates or ()) if g]

    if not mine and not known_gates:
        return ClaimStatus.UNCLAIMED

    # Every rung reads `Verdict.outcome`, the one definition of what a gate run
    # was. The rungs used to re-derive it from the three flags, in a third copy
    # that agreed with `Verdict.ok` and `Verdict.render` only because nobody had
    # yet written the fourth.
    outcomes = [v.outcome for v in mine]

    # Rung 2. `skip_reason` is free text ("requires openfoam (not installed)"),
    # so there is no reliable way to tell an availability skip from any other
    # kind — and it does not matter, because neither is a pass. Every skip is
    # treated as "the tooling did not run", which is what BLOCKED means.
    # An errored verdict is deliberately NOT a skip: the gate ran and blew up,
    # which is a louder problem, and falls through to FAIL on rung 4.
    if mine and all(o == "skipped" for o in outcomes):
        return ClaimStatus.BLOCKED

    if not mine:
        return ClaimStatus.PENDING

    # Rung 4. An outcome is "pass" only for `passed is True` with no skip and no
    # error, so a gate that crashed cannot reach rungs 5-6 no matter what
    # `passed` says — a crashed gate that left `passed` at its default is the
    # plausible-sounding green this whole module exists to prevent — and neither
    # can a hand-edited `"passed": "yes"`.
    if any(o in ("error", "fail") for o in outcomes):
        return ClaimStatus.FAIL

    if stale:
        return ClaimStatus.STALE
    return ClaimStatus.PASS


#: The order `explaining_verdict` ranks outcomes in, most explanatory first. A
#: gate that RAN and failed carries the measured value and the limit, which is
#: what the reader is about to go and change; a crash is louder than a missing
#: tool; a skip explains only when nothing else does. A pass explains nothing.
_EXPLAINS = ("fail", "error", "skipped")


def explaining_verdict(claim: Claim, verdicts: Iterable[Verdict]) -> Verdict | None:
    """The one verdict that explains why `claim` is not settled, or None.

    Several gates can cover one claim, and the first non-passing one is not
    necessarily the one that set the status. What got through: `status` cited a
    pack gate that SKIPPED for a missing parameter as the reason a claim FAILED,
    while `check` — whose own copy of this ranking had already been fixed —
    cited the gate that ran and measured 0.7 mm against a 0.5 mm limit. Two
    commands, two stories, one ledger; the reader went looking for a missing
    parameter. So the choice lives here, once: ran-and-failed, then errored, then
    skipped, and within a rank the first in the order given (stable, so the same
    ledger always cites the same gate).

    `verdicts` may be the whole ledger's list; it is filtered with
    `covering_verdicts`, by the same id-or-tag rule as everything else here.
    """
    mine = covering_verdicts(claim, verdicts)
    for wanted in _EXPLAINS:
        for verdict in mine:
            if verdict.outcome == wanted:
                return verdict
    return None


# --------------------------------------------------------------------------- #
# registry plumbing
# --------------------------------------------------------------------------- #
def _specs(registry: Any) -> list[GateSpec]:
    """Normalise whatever was handed in as a "registry" into a list of specs.

    `gates.Registry` exposes `.specs()`, but `claims.py` may not import `gates`
    (it would make the dependency graph circular via `store`), so the registry
    is duck-typed. A bare list of `GateSpec` is accepted too, because that is
    what tests and one-off scripts actually have, and `None` means "no gates
    registered" rather than an error — `atompipe status` in a fresh project is a
    normal thing to run.

    A registry of the wrong shape raises `TypeError`, not `AtompipeError`: the
    user cannot cause it, so it is a bug in the caller and keeps its traceback.
    """
    if registry is None:
        return []
    getter = getattr(registry, "specs", None)
    if callable(getter):
        return list(getter())
    if isinstance(registry, GateSpec):
        return [registry]
    if isinstance(registry, _IterableABC) and not isinstance(registry, (str, bytes)):
        return list(registry)
    raise TypeError(
        f"registry must expose .specs() or be an iterable of GateSpec, got {type(registry).__name__}"
    )


def coverage(ledger: Ledger, registry: Any) -> dict[str, list[str]]:
    """Map every claim id to the sorted ids of the gates that cover it.

    **Every** claim gets a key, including the uncovered ones (empty list). A
    caller asking "which gates cover C7" must be able to tell "none" (`[]`) from
    "there is no C7" (`KeyError`); a `.get(cid, [])` that conflates them is how a
    typo'd claim id reads as a covered claim.

    Non-MEASURABLE claims are included: a PHYSICAL claim can perfectly well have
    a tag-bound gate that checks the *modelled* half of it, and hiding that
    would make the report look emptier than the project is. Only `find_gaps`
    restricts itself to MEASURABLE.

    Ids are sorted so two runs of `atompipe report` over an unchanged project
    produce a byte-identical file; registration order is an implementation
    detail of import order and must not leak into a diff.
    """
    specs = _specs(registry)
    out: dict[str, list[str]] = {}
    for claim in ledger.claims:
        out[claim.id] = sorted({s.id for s in specs if s.id and covers(s, claim)})
    return out


def effective_gates(ledger: Ledger, registry: Any) -> dict[str, list[str]]:
    """Union of the ledger's cached `claim.gates` and live registry coverage.

    **This is the single definition of "which gates cover this claim".** Public
    for exactly that reason: `report.py` had grown a second, more optimistic
    copy that kept only the live half whenever a registry was passed, so the
    PROVEN table's **PARTIAL** caveat — the one naming the covering gate that
    produced no proof — silently vanished on precisely the machine where the
    pack was *not* installed. The report read cleaner the less it could see.
    Two answers to a coverage question is one answer too many, and the
    optimistic copy always wins the argument.

    Both halves are needed and neither is sufficient. The registry knows about a
    pack installed five minutes ago that no claim record mentions yet; the
    ledger remembers a gate id that produced a verdict from a pack that is not
    loaded in *this* process (`atompipe status` loads no packs). Dropping either
    one turns a PENDING claim into a false UNCLAIMED, which sends an agent off
    to install a solver it already has.

    `registry=None` is not an error: `coverage` then contributes nothing and the
    result is the cached opinion alone, which is the honest answer on a machine
    that cannot see the gates. A claim id that is absent from the ledger is
    absent from the result — a `KeyError` is the correct answer to "which gates
    cover C99" when there is no C99 (see `coverage`).

    Keyed per claim id rather than taking one `Claim`, because every caller —
    `statuses`, the report's two renders — wants the whole ledger's map and
    would otherwise loop, re-normalising the registry into specs once per claim.
    """
    live = coverage(ledger, registry)
    merged: dict[str, list[str]] = {}
    for claim in ledger.claims:
        cached = [g for g in (claim.gates or ()) if g]
        merged[claim.id] = sorted(set(cached) | set(live.get(claim.id, ())))
    return merged


# --------------------------------------------------------------------------- #
# whole-ledger views
# --------------------------------------------------------------------------- #
def statuses(
    ledger: Ledger,
    *,
    stale: bool = False,
    registry: Any = None,
) -> dict[str, ClaimStatus]:
    """Resolve every claim in the ledger. Claim id -> status.

    With no `registry`, coverage is judged from `claim.gates` alone, which is a
    cached opinion: a claim whose gate arrived with a pack installed after the
    claim was written will read UNCLAIMED instead of PENDING. Pass the registry
    whenever you have one — `blocking()` and `summarise()` always do. (The
    contract spells this function `statuses(ledger, *, stale=False)`; `registry`
    is an additive keyword, so every contract call site still works.)
    """
    gates_for = effective_gates(ledger, registry) if registry is not None else None
    out: dict[str, ClaimStatus] = {}
    for claim in ledger.claims:
        if gates_for is not None:
            # `replace` copies; the ledger is never mutated by a derivation.
            claim = replace(claim, gates=gates_for.get(claim.id, []))
        out[claim.id] = resolve_status(claim, ledger.verdicts, stale=stale)
    return out


def _gap_quantity(claim: Claim) -> str:
    """The unvalidated physical quantity a Need should name.

    `acceptance.quantity` first, because that is the sharpened form ("tip
    deflection", "draft fraction") and step 1 of the extension protocol is
    naming the quantity, not restating the wish. The statement is the fallback,
    and a fallback that reads like a wish ("hull is strong enough") is itself the
    signal that the *claim* needs sharpening before any tool gets installed.
    """
    acc = claim.acceptance
    quantity = (getattr(acc, "quantity", "") or "").strip()
    return quantity or (claim.statement or "").strip()


def _need_id(claim: Claim) -> str:
    """Stable Need id derived from the claim id, e.g. C3 -> "N-C3".

    Derived from the claim rather than a counter on purpose: `find_gaps` runs on
    every `atompipe status`, and an id that depended on iteration order or on the
    quantity text would churn on every run, so the `chosen` tool and candidate
    costs an agent recorded against it could never be matched back.
    """
    raw = (claim.id or "").strip()
    tail = raw if _PLAIN_ID.fullmatch(raw) else slugify(raw or claim.statement)
    return f"N-{tail}"


def find_gaps(ledger: Ledger, registry: Any) -> list[Need]:
    """MEASURABLE claims that no registered gate covers, as `Need` records.

    This is the trigger for the extension protocol, and it is a *growth*
    signal, not a failure: the system is admitting there is a physical quantity
    it cannot currently check. PHYSICAL and ASSUMPTION claims are never gaps —
    no tool will ever settle "the seam is watertight", and an assumption is
    already honest about being unevidenced. Inventing a simulation for either is
    how a project launders an unknown into green.

    Coverage is judged from the **registry only**, not `claim.gates`: a claim
    remembering a gate id from a pack that is no longer installed is not covered,
    and quietly is the worst way to find that out.

    Existing Needs are reused, matched on `claim_ids`, and everything an agent
    put on them survives — `status`, `candidates`, `chosen`, `claim_class`,
    `note`, and a `quantity` that has been sharpened by hand. A fresh Need gets
    `claim_class=""` deliberately: classifying the gap is step 2 of the protocol
    and it is a judgement call, not something to guess from a tag.

    Returns copies. This module writes nothing, including into the ledger it was
    handed; persisting the result is the caller's decision.
    """
    live = coverage(ledger, registry)

    # First Need that mentions a claim wins it. Two Needs for one claim is a
    # data error we do not get to fix here (nothing in this module writes), so
    # the behaviour is at least deterministic: ledger order.
    by_claim: dict[str, Need] = {}
    for need in ledger.needs:
        for cid in need.claim_ids or ():
            by_claim.setdefault(cid, need)

    taken = {n.id for n in ledger.needs if n.id}
    emitted: set[str] = set()
    out: list[Need] = []

    for claim in ledger.claims:
        if _kind(claim) is not ClaimKind.MEASURABLE:
            continue
        if live.get(claim.id):
            continue

        existing = by_claim.get(claim.id)
        if existing is not None:
            if existing.id in emitted:
                continue            # one Need covering several gap claims: emit once
            emitted.add(existing.id)
            out.append(
                replace(
                    existing,
                    # Only fill a quantity that was never written. Clobbering a
                    # hand-sharpened "yaw stability at 4 m/s cruise, loaded" with
                    # the raw acceptance quantity would undo step 1 of the
                    # protocol on every status call.
                    quantity=existing.quantity or _gap_quantity(claim),
                    claim_ids=list(existing.claim_ids or []),
                    candidates=list(existing.candidates or []),
                )
            )
            continue

        nid = iter_suffix_unique(_need_id(claim), taken)
        taken.add(nid)
        emitted.add(nid)
        out.append(
            Need(
                id=nid,
                claim_ids=[claim.id],
                quantity=_gap_quantity(claim),
                claim_class="",          # step 2 of the protocol; the agent fills it
                status=NeedStatus.OPEN,
            )
        )
    return out


def blocking(
    ledger: Ledger,
    registry: Any,
    *,
    stale: bool = False,
) -> list[tuple[Claim, ClaimStatus]]:
    """Critical claims whose status must stop an irreversible spend, with the reason.

    This is the function an agent calls before letting a user order a board, cut
    stock, or pay a fab. It returns `(claim, status)` pairs because "C7 blocks"
    is not actionable and "C7 is BLOCKED: no gate ran, the solver is not
    installed" is — and because the caller would otherwise recompute the status
    it just discarded, with a second copy of the precedence rules.

    `critical=False` claims never appear: a nice-to-have that failed is a note in
    the report, not a stop sign, and a stop sign that fires on nice-to-haves gets
    ignored the third time.

    Note what is *not* in `BLOCKING_STATUSES`: UNVERIFIED. A physical claim
    awaiting a real-world result cannot block the spend that produces the object
    you would test it on. It stays loudly UNVERIFIED in the readiness report
    instead — that is the readiness ledger separating PROVEN from ASSUMED, not
    the spend gate.

    With `stale=True` every passing critical claim becomes STALE and therefore
    blocks. That is the point: a green run against a model that has since moved
    is precisely the evidence that is not evidence.
    """
    resolved = statuses(ledger, stale=stale, registry=registry)
    return [
        (claim, resolved[claim.id])
        for claim in ledger.claims
        if claim.critical and resolved.get(claim.id) in BLOCKING_STATUSES
    ]


def summarise(
    ledger: Ledger,
    registry: Any,
    *,
    stale: bool = False,
) -> dict[str, Any]:
    """Counts for `atompipe status` — claims and gates, nothing else.

    Every `ClaimStatus` appears in `by_status` even at zero, and every
    `ClaimKind` in `by_kind`. A CLI table whose columns appear and disappear
    with the data is unreadable across runs and unparseable by anything
    downstream, and a missing key reads as "no problem here" when it means "no
    key here".

    `ready` is `n_blocking == 0`: nothing critical is in a blocking status *as
    far as the gates go*. It is not "everything is proven" — UNVERIFIED physical
    claims and standing assumptions are both compatible with `ready`, by design,
    and the readiness report is where that distinction is spelled out in words.

    Input-artifact counts are deliberately absent; `artifacts.unextracted()`
    owns those, and a summary assembled from two modules' views of the same
    ledger is how two numbers that must agree stop agreeing.
    """
    resolved = statuses(ledger, stale=stale, registry=registry)
    specs = _specs(registry)
    live = coverage(ledger, registry)
    gaps = find_gaps(ledger, registry)
    blockers = blocking(ledger, registry, stale=stale)

    by_status = {s.value: 0 for s in ClaimStatus}
    for status in resolved.values():
        by_status[status.value] += 1

    by_kind = {k.value: 0 for k in ClaimKind}
    for claim in ledger.claims:
        by_kind[_kind(claim).value] += 1

    covering_specs = {s.id for s in specs if any(covers(s, c) for c in ledger.claims)}

    return {
        "n_claims": len(ledger.claims),
        "n_critical": sum(1 for c in ledger.claims if c.critical),
        "by_status": by_status,
        "by_kind": by_kind,
        "n_gates": len(specs),
        "n_gates_binding": len(covering_specs),      # registered gates that reach a claim
        "n_covered_claims": sum(1 for ids in live.values() if ids),
        "n_verdicts": len(ledger.verdicts),
        "n_gaps": len(gaps),
        "n_blocking": len(blockers),
        "blocking_ids": [c.id for c, _ in blockers],
        "stale": bool(stale),
        "ready": not blockers,
    }


def next_claim_id(ledger: Ledger, prefix: str = "C") -> str:
    """The next free claim id: `prefix` + (highest existing number + 1).

    Highest **+1**, never count+1: if C3 is deleted, C3 must never be issued
    again. Claim ids are quoted from decision log entries, gate specs, commit
    messages and the readiness report, and a recycled id silently repoints all
    of those at a different claim — a lie with no error message anywhere.

    Ids that do not match `prefix` + digits are ignored (C7b, CLAIM-3), so a
    project that hand-writes some ids still gets a usable next one.

    Raises `AtompipeError` for an empty prefix or a prefix ending in a digit:
    with `prefix="C1"`, the existing id "C12" would scan as number 2 and the
    next id would be "C13" — which is also what `prefix="C1"` on "C12" would
    produce next time. Ambiguous id schemes are a user mistake worth naming at
    the point of the mistake.
    """
    prefix = prefix if prefix is not None else ""
    if not prefix.strip():
        raise AtompipeError("claim id prefix must not be empty (e.g. --prefix C)")
    if prefix[-1].isdigit():
        raise AtompipeError(
            f"claim id prefix {prefix!r} ends in a digit, which makes ids ambiguous "
            f"({prefix}2 could be {prefix[:-1]} number {prefix[-1]}2) — use a letter suffix"
        )

    highest = 0
    for claim in ledger.claims:
        cid = claim.id or ""
        if not cid.startswith(prefix):
            continue
        tail = cid[len(prefix):]
        if _ASCII_INT.fullmatch(tail):
            highest = max(highest, int(tail))
    return f"{prefix}{highest + 1}"
