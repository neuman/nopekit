# SPDX-License-Identifier: Apache-2.0
"""atompipe.claims — status derivation, and the honesty property of the ledger.

Nothing in this module writes anything. It reads a `Ledger` (and, where it can,
a live gate registry) and derives: what is settled, what is not, what is not
even *checkable* yet, and which of those must stop a user from spending money.

The one rule that matters more than all the others:

    **A SKIP IS NEVER A PASS. AN ERROR IS NEVER A PASS. AN UNQUALIFIED OR UNRUN
    EVALUATOR IS NEVER A PASS, beside a pass or not.**

That single line is the difference between this system and a plausible-sounding
one. A cable-routing validator once shipped green for a whole revision while
returning a flag nobody read, with the cable geometrically inside a wall — a
report that had counted "the gate did not object" as "the claim is proven".
Every branch below is written so the *absence* of evidence can never be spelled
`PASS`; it gets its own status and stays visible in the readiness report until
somebody does the work.

From P2.1 a claim's status is GLOSSARY §3's composition, one ladder for every
kind (`compose`): Failing, Skipped (errored first), Gap, Open, Stale, Pending
build, Assumed, Checked — first match wins, and `compose` returns the fact that
set it (`ClaimCause`) beside the status. From P2.4 a value is compared with the
claim it is evidence for (`cross_check`): a covering evaluator's pass whose
value — same quantity, same units — does not meet the claim's acceptance
condition reads Failing (`acceptance`), and a pass outside its evaluator's
operating context does not count (Gap, `outside-context`, or Assumed under an
owned fallback). What slipped through without them (S-35, S-46): C3's
evaluator judged 0.195 MPa against a limit it computed while the claim said
0.1 MPa, and read Checked; the report put a guard's `8.57 L/h` in C2's value
cell, implying it had been compared with the claim. The margin of every verdict
is one function here (`margin`, D-17), so no renderer computes its own. What slipped through the ladder it
replaced: a pass beside a skip or an unrun evaluator read PASS (S-03), so did a
pass beside an evaluator `check` had just refused at its version, and a crash
read FAIL; a physical claim ignored a failing modelled half (S-49); and an
assumption nobody owned read ASSERTED. The visible changes — what now stops
`check` — are `blocking`'s docstring and SPINE_CONTRACT's "What P2.1 moved".

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

* **Purity.** No clock, no randomness, no disk. Staleness comes in from whoever
  judged it — `stale_gates`, the gates `verdicts.resolve` found stale, or
  `stale=True` for all of them; these functions must not decide staleness for
  themselves, or the same ledger would resolve differently on two machines.
"""
from __future__ import annotations

import math
import numbers
import re
from collections.abc import Iterable as _IterableABC
from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import Any, Collection, Iterable, Mapping, NamedTuple

from .models import (
    BLOCKING_STATUSES,
    CONTEXT_OUTSIDE,
    Claim,
    ClaimKind,
    ClaimStatus,
    GateSpec,
    Ledger,
    Need,
    NeedStatus,
    PrerequisiteKind,
    StrEnum,
    Terminal,
    TERMINALS_BY_KIND,
    Verdict,
    slugify,
)
from .util import AtompipeError, iter_suffix_unique, seal


__all__ = [
    "ClaimCause",
    "Attribution",
    "Composed",
    "STATUS_KEY",
    "SEVERITY_ORDER",
    "KEY_ORDER",
    "OUTCOME_ORDER",
    "severity",
    "outcome_rank",
    "covers",
    "covering_verdicts",
    "compose",
    "compositions",
    "resolve_status",
    "explaining_verdict",
    "statuses",
    "coverage",
    "effective_gates",
    "find_gaps",
    "blocking",
    "summarise",
    "next_claim_id",
    "Margin",
    "margin",
    "Compared",
    "cross_check",
    "compared_gates",
    "not_compared",
    "LimitDisagreement",
    "limit_disagreements",
    "LIMIT_REL_TOL",
    "outside_context",
    "assumption_reason",
    "terminal_of",
    "identity_matches",
    "claim_digest",
    "AWAITS_A_PERSON",
    "blocks",
    "contradicted_by",
    "Rebuild",
    "rebuild",
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
# the composition: one ladder, every kind (GLOSSARY §3). `P2.1-Dn` below are the
# decision rows of `docs/plan/phase-2.md` ("P2.1's decision rows").
# --------------------------------------------------------------------------- #
class ClaimCause(StrEnum):
    """Which fact set a claim's status — an identifier, never a word.

    Each status covers more than one fact (Skipped: a missing tool OR a crash;
    Gap: no evaluator, an unqualified one, or an assumption nobody owns), and
    GLOSSARY §3 says "the reason line says which". This is that "which", made
    once, by `compose`, beside the status it explains. What slipped through
    without it (P2.0 F-8, S-68): each renderer re-derived the fact from the
    status, so rung 4 moved alone would have called a crash "blocked on missing
    tooling" in three private fallbacks, and `why` told an unowned assumption
    it was "UNCLAIMED: no gate can settle it".

    Here, not in `models`: `models` is a spine module (`verdicts.SPINE_MODULES`),
    so a cause added here would re-key every verdict cache entry in every
    project, as P2.2's two did not. The words for each are `report.HUMAN`'s.

    P2.4 adds three. `acceptance` — Failing: a counted evaluator's pass whose
    value, of the claim's quantity and in its units, does not meet the claim's
    acceptance condition (`cross_check`); what slipped through without it
    (S-35): an evaluator judging against a limit it computes read Checked at a
    value the claim's own condition rejects. `outside-context` — Gap: an
    evaluator's pass on inputs outside its declared operating context
    (`verdicts._contexted`'s token), and no owned fallback. `fallback` —
    Assumed: the same, carried by an owned fallback (`Claim.fallback`, its
    owner attributed through the signing channel — nothing can be until P2.5).

    `prerequisite` (P2.2-D10): Skipped because a prerequisite of the claim's
    evaluator is not established — the evaluator was not run. Its own cause,
    because "skipped" had come to mean "install a tool" everywhere (S-54), and
    installing changes nothing when a guard FAILED. `prerequisite-errored`:
    the same, where the root CRASHED — invariant 2's louder Skipped, reached
    through a prerequisite (`Composed.errored`). What slipped through the
    design: a guard is bound to its own claims, so its crash reached a claim
    bound only to the dependent as the dependent's skip — the missing tool's
    tone, its count and a JUnit `<failure>`. *Rejected:* a lead word of its own
    for a prerequisite skip ("blocked" and "unknown" are both GLOSSARY
    Never-says for Skipped); splitting the count by cause (`N skipped (k
    prerequisite)`: a number no decision reads; the crash is already split out).
    """

    FAILED = "failed"
    PHYSICAL_FAIL = "physical-fail"
    ERRORED = "errored"
    PREREQUISITE_ERRORED = "prerequisite-errored"
    SKIPPED = "skipped"
    PREREQUISITE = "prerequisite"
    UNQUALIFIED = "unqualified"
    NO_EVALUATOR = "no-evaluator"
    NO_OWNER = "no-owner"
    OWNER_UNATTRIBUTED = "owner-unattributed"
    NO_REASON = "no-reason"
    UNRUN = "unrun"
    INVALIDATED = "invalidated"
    NO_ARTICLE = "no-article"
    OWNED = "owned"
    PHYSICAL_PASS = "physical-pass"
    CHECKED = "checked"
    ACCEPTANCE = "acceptance"
    OUTSIDE_CONTEXT = "outside-context"
    FALLBACK = "fallback"
    # P2.5a (its D19): the physical path and expert judgment. Failing: a
    # physical fail on a claim a counted evaluator had passed (`contradiction`,
    # E4), or the authority's own no (`judged-fail`). Gap: an expert-judgment
    # claim naming no authority (`no-authority`), or naming one who has not
    # recorded it (`authority-unattributed`). Stale: the newest pass a person
    # made in their own shell, and its first half that no longer holds — the
    # article (`article-moved`; `judgment-moved` for a judgment), the claim as
    # they read it (`claim-moved`), or a model that does not load to judge the
    # article by (`article-unjudged`). Assumed: an expert-judgment claim its
    # authority recorded and has not judged (`awaiting-judgment`). Checked
    # (VERIFIED): a pass on the current article (`on-article`), the
    # authority's judgment (`judged`).
    CONTRADICTION = "contradiction"
    JUDGED_FAIL = "judged-fail"
    NO_AUTHORITY = "no-authority"
    AUTHORITY_UNATTRIBUTED = "authority-unattributed"
    ARTICLE_MOVED = "article-moved"
    CLAIM_MOVED = "claim-moved"
    JUDGMENT_MOVED = "judgment-moved"
    ARTICLE_UNJUDGED = "article-unjudged"
    AWAITING_JUDGMENT = "awaiting-judgment"
    ON_ARTICLE = "on-article"
    JUDGED = "judged"


#: The Stale causes that wait on a person's act on an article — a new article
#: built, the claim tested again, the authority judging again — which no check
#: run can answer (P2.5a-D27, critique 11 of its design). Like Pending build they
#: never stop `check` (`blocks`), and like it they are unresolved: *ready* stays
#: false. What slipped through the design as written: a recorded pass made
#: `check` exit 1 after ANY later design edit — and `check` gates the build of
#: the very article that would answer it, so recording a pass left a project
#: more blocked than not recording one, until a new article was tested that
#: `check` would not let anyone build. *Rejected:* blocking (that deadlock);
#: Pending build for a moved pass (§1.4: "Not Pending build" — Table 1's Stale
#: is "previously checked evidence no longer matches current inputs"). A claim
#: whose covering automated evaluator is invalidated too still blocks: the
#: evaluator's half is a check run's to answer (`Composed.cites` names it).
AWAITS_A_PERSON: frozenset = frozenset({ClaimCause.ARTICLE_MOVED, ClaimCause.CLAIM_MOVED,
                                         ClaimCause.JUDGMENT_MOVED,
                                         ClaimCause.ARTICLE_UNJUDGED})


class Attribution(NamedTuple):
    """An assumption's owner as the signing channel recorded it, with the
    reason they recorded it against — an in-memory seam, not a record.

    It reaches `compose` only through `owners`, the way staleness arrives
    (this module stays pure). Bound by value (P2.1-D8): it counts only while
    `owner` equals the claim file's `owner` and `reason` equals its non-empty
    `rationale`, so an edit to either after the attribution un-attributes it.
    Sealing it to the claim's digest is the signing channel's (D-13). Nothing
    in P2.1 produces one: every caller passes no `owners`, and every assumption
    reads Gap until the channel lands. From P2.4 `reason` is
    `assumption_reason(claim)` — the rationale for an assumption, the
    `fallback` for a claim carried outside an evaluator's operating context —
    so an attribution over one reason never counts for the other.
    """

    owner: str
    reason: str


@dataclass(frozen=True)
class Composed:
    """`compose`'s answer: the status, the fact that set it (`cause`), the
    evaluators that fact names (`cites`, errored first), and the verdict that
    explains it (`verdict`, or None). Renderers take this whole, never the bare
    status, so none of them has to guess the cause back from the word."""

    status: ClaimStatus
    cause: ClaimCause
    cites: tuple = ()
    verdict: Verdict | None = None

    @property
    def errored(self) -> bool:
        """Skipped by a crash — invariant 2's louder Skipped: the evaluator's
        own, or its prerequisite's (P2.2). Never true for an unqualified
        evaluator, which reads Gap (P2.0 D-8)."""
        return self.cause in (ClaimCause.ERRORED, ClaimCause.PREREQUISITE_ERRORED)


#: Each status's machine token: GLOSSARY §8's proposed rename-pass values, so the
#: rename pass deletes a map instead of re-deciding it. Identifiers, not words
#: (GLOSSARY §7: a JSON value is not a human channel), so they live here beside
#: the statuses `summarise` counts with them; `report.HUMAN` reads them from here.
STATUS_KEY: Mapping[ClaimStatus, str] = MappingProxyType({
    ClaimStatus.PASS: "checked", ClaimStatus.VERIFIED: "checked",
    ClaimStatus.FAIL: "failing", ClaimStatus.REFUTED: "failing",
    ClaimStatus.STALE: "stale", ClaimStatus.ASSERTED: "assumed",
    ClaimStatus.UNVERIFIED: "pending_build", ClaimStatus.UNCLAIMED: "gap",
    ClaimStatus.BLOCKED: "skipped", ClaimStatus.PENDING: "open",
})

#: The order a claim is listed in, most urgent first — one table for every
#: reader that ranks claims (`severity`): Failing · Skipped by a crash ·
#: Skipped · Gap · Open · Stale · Pending build · Assumed · Checked. Tokens,
#: with `errored` for a crash's Skipped (one status, louder: invariant 2).
#: Why this order: what an evaluator failed is the actionable fact, and a crash
#: is a broken evaluator, louder than a missing tool (GLOSSARY §3, *Skipped*);
#: then what no evaluator can settle (Gap) before what one could if it ran
#: (Open); a moved pass (Stale) before what waits on an article or an owner,
#: which no rerun answers; Checked last. *Rejected:* STALE above Skipped
#: (`report._SEVERITY` before P2.1 — an order no rule produced, which put a
#: missing tool under a moved pass); Open above Skipped (a missing tool hidden
#: behind "run check"); Pending build above Open or Stale (the cheap evaluator
#: first, §6.1, and Open and Stale stop `check` while Pending build does not);
#: the rank living in `report.HUMAN`'s rows (the P2.1 design: a rank is not a
#: word, and `verdicts.write_last_check` — a spine module, which must not reach
#: for the words — then picked its `worst` in record order, a skip above the
#: crash `check` printed first).
SEVERITY_ORDER: tuple[str, ...] = ("failing", "errored", "skipped", "gap", "open", "stale",
                                   "pending_build", "assumed", "checked")

#: The eight tokens in count order: Checked first, then `SEVERITY_ORDER` with
#: the crash folded into Skipped (a count splits it out in words: `N skipped (k
#: errored)`). `summarise`'s `counts` is keyed by exactly these, zero-filled,
#: and sums to `n_claims`. *Rejected:* severity order with Checked last (GLOSSARY
#: §9's count line leads with what is settled, so a reader sees the size of the
#: project before its problems); a separate key for errored (a key that is not a
#: status, in a map readers sum to `n_claims`).
KEY_ORDER: tuple[str, ...] = ("checked", *(key for key in SEVERITY_ORDER
                                           if key not in ("errored", "checked")))

#: The order one claim's evaluators are listed and explained in, by what each
#: did — one table for `explaining_verdict`, the report's bullets and `why`'s
#: groups (each kept its own copy until review: `_EXPLAINS`, `_BULLET_ORDER`,
#: `_OUTCOME_RANK` plus two inline ranks, three tables that had to agree): what
#: failed the candidate, what crashed, what was not run behind a crashed
#: prerequisite, what was not run behind any other, what skipped, an
#: unqualified evaluator (its verdict says `error` too, and it is never ranked
#: as a crash: P2.0 D-8), an unrun one, and what passed — `compose`'s rungs 1-3
#: in its own order. Outcome words are `Verdict.outcome`'s values;
#: `prerequisite-errored` and `prerequisite` name a skip the rule made
#: (`Verdict.blocked_by`, by its `blocked_kind`), `unqualified`, `outside-context`
#: (P2.4: a pass outside its evaluator's operating context, after an evaluator
#: unqualified at its version — the refuse-everything evaluator is the bigger
#: fact) and `unrun` what is not an outcome. What slipped through (review of P2.2): `compose`
#: ranked a skip behind a crashed prerequisite above a plain skip and this did
#: not, so `last_check.json`'s `worst` cited a missing tool's gate and words
#: beside the cause `prerequisite-errored`, and the report's bullets listed the
#: tool skip first. *Rejected:* gate-id order (P2.0 F-3: `[skip]` above
#: `[ERR ]`, the dull line read first); errored first (a fail carries the
#: measured value and the limit, what the reader goes to change, and Failing
#: outranks Skipped on the claim); a prerequisite-errored skip ranked with a
#: crash (a tie, broken by record order, would cite it before the crash
#: `compose` cites).
OUTCOME_ORDER: tuple[str, ...] = ("fail", "error", "prerequisite-errored", "prerequisite",
                                  "skipped", "unqualified", "outside-context", "unrun", "pass")


def severity(composed: "Composed") -> int:
    """`composed`'s place in `SEVERITY_ORDER`, most urgent 0 — the one rank
    `report.in_severity`, `check`'s BLOCKING list and `last_check.json`'s
    `worst` all sort by."""
    key = "errored" if composed.errored else STATUS_KEY[composed.status]
    return SEVERITY_ORDER.index(key)


def outcome_rank(verdict: Verdict | None) -> int:
    """`verdict`'s place in `OUTCOME_ORDER`; None is an unrun evaluator. A
    refused evaluator ranks by the spine's mark, never as the crash its
    `error` text reads as; a skip the prerequisite rule made ranks by its
    root's kind (`Verdict.blocked_by`, `blocked_kind`, the spine's marks)."""
    if verdict is None:
        key = "unrun"
    elif outside_context(verdict):
        key = "outside-context"
    elif getattr(verdict, "unqualified", ""):
        key = "unqualified"
    elif getattr(verdict, "blocked_by", None):
        key = ("prerequisite-errored"
               if str(getattr(verdict, "blocked_kind", "")) == PrerequisiteKind.ERRORED
               else "prerequisite")
    else:
        key = verdict.outcome
    return OUTCOME_ORDER.index(key) if key in OUTCOME_ORDER else len(OUTCOME_ORDER)


def _distinct(ids: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(i for i in ids if i))


def outside_context(verdict: Any) -> bool:
    """Whether ``verdict`` is a pass the spine marked outside its evaluator's
    operating context (``models.CONTEXT_OUTSIDE``, P2.4-D15) — a token kind of
    ``Verdict.unqualified``, read here by its kind and nowhere by its text."""
    token = str(getattr(verdict, "unqualified", "") or "")
    return token.partition("|")[0] == CONTEXT_OUTSIDE


def assumption_reason(claim: Claim) -> str:
    """The reason an owner attributes for ``claim`` (P2.4-D18): its
    ``rationale`` for an assumption — why it is carried — and its ``fallback``
    for any other claim — why it may be carried while its evaluators' passes
    lie outside their operating contexts. An ``Attribution`` is bound by value
    to the owner and THIS reason, so editing either after it un-attributes it."""
    if _kind(claim) is ClaimKind.ASSUMPTION:
        return str(claim.rationale or "")
    return str(getattr(claim, "fallback", "") or "")


def terminal_of(claim: Claim) -> str:
    """Where ``claim``'s evidence bottoms out (``models.Terminal``): its declared
    ``terminal``, else by kind — physical -> ``measurement``, assumption ->
    ``none``, measurable -> ``""`` (automated; P2.5a-D1: a declared
    closed_form, solver or datasheet composes exactly as undeclared). An
    unknown in-memory value raises, as ``_kind`` does for a kind: the strict
    reader refuses it on disk, and a hand-built claim that carries one is a
    caller's mistake worth naming."""
    kind = _kind(claim)
    declared = str(getattr(claim, "terminal", "") or "")
    if declared:
        try:
            value = Terminal(declared).value
        except ValueError:
            raise AtompipeError(f"claim {claim.id!r} has unknown terminal {declared!r}; "
                                f"expected one of: {', '.join(t.value for t in Terminal)}"
                                ) from None
        if value not in TERMINALS_BY_KIND[kind]:
            raise AtompipeError(f"claim {claim.id!r}: a {kind.value} claim cannot end in "
                                f"{value!r}")
        return "" if kind is ClaimKind.MEASURABLE else value
    return {ClaimKind.PHYSICAL: "measurement", ClaimKind.ASSUMPTION: "none"}.get(kind, "")


def name_of(value: Any) -> str:
    """A name as every comparison reads it — an owner, an authority, an
    attribution's ``name``, a result's ``authority`` — with the whitespace
    around it gone: the ONE place a name is normalised. What slipped through
    (review of P2.5a): the writers stripped the claim file's ``"owner": "Dana
    Reviewer "`` and the readers did not, so Dana's own ``assume`` was recorded
    and never counted, and the reason told her to run the command she had just
    run."""
    return str(value if value is not None else "").strip()


def identity_matches(who: Any, name: Any) -> bool:
    """Whether the git identity ``who`` (``Name <email>``) IS the person ``name``
    names: the whole identity, or its name part, exactly. Exact on purpose (R13
    of the P2.5a design): prefix or case-folded matching would let ``Dan`` stand
    for ``Dana``, and the refusal names both spellings and the fix."""
    who, name = name_of(who), name_of(name)
    return bool(name) and (who == name or who.split("<", 1)[0].strip() == name)


def claim_digest(claim: Claim) -> str:
    """The claim as a person read it when they recorded a result or an
    attribution (P2.5a-D8's claim half): its statement, kind, effective
    terminal, acceptance condition, note and authority, sealed. A result is
    bound to it, so an edit to any of those after a test reads Stale — "test it
    again" — and never Checked. Includes the statement on purpose (R14 of the
    design): a person attests the sentence; the cost is that a typo fix asks for
    a re-test."""
    acceptance = claim.acceptance
    comparator = getattr(acceptance, "comparator", "")
    try:
        terminal = terminal_of(claim)
    except AtompipeError:
        terminal = str(getattr(claim, "terminal", "") or "")
    return seal({"statement": str(claim.statement or ""), "kind": _kind(claim).value,
                 "terminal": terminal,
                 "acceptance": {"quantity": str(getattr(acceptance, "quantity", "") or ""),
                                "comparator": str(getattr(comparator, "value", comparator)
                                                  or ""),
                                "limit": getattr(acceptance, "limit", None),
                                "limit_hi": getattr(acceptance, "limit_hi", None),
                                "units": str(getattr(acceptance, "units", "") or "")},
                 "note": str(getattr(claim, "note", "") or ""),
                 "authority": str(getattr(claim, "authority", "") or "")})


def _attributed(claim: Claim, role: str) -> list[Any]:
    """``claim``'s recorded attributions for ``role``, newest first, each a
    person's own (``interactive``, recorded by the person it names) — the
    store assembles only sealed ``interactive`` ones; this re-checks the
    channel and the identity, so a hand-built claim cannot slip one past."""
    return [record for record in getattr(claim, "attributions", ()) or ()
            if getattr(record, "role", "") == role
            and getattr(record, "channel", "") == "interactive"
            and identity_matches(getattr(record, "who", ""), getattr(record, "name", ""))]


def _owners_of(claim: Claim) -> dict[str, Attribution]:
    """``{claim id: Attribution}`` from the owner attributions the channel
    recorded — what ``compose`` reads when no ``owners`` is passed (P2.5a-D11):
    the newest whose name and reason match the claim as it reads now, else the
    newest (which then reads unattributed). Bound by value exactly as before
    (P2.1-D8): to the owner and the reason. What slipped through (review of
    P2.5a): only the newest was read, so an owner changed to Bob and back to
    Dana read Gap though Dana's attribution, for this owner and this reason,
    was still sealed in the file — where a reverted article reads Checked again
    and an authority is matched against any attribution recorded."""
    found = _attributed(claim, "owner")
    if not found:
        return {}
    owner, reason = name_of(getattr(claim, "owner", "")), assumption_reason(claim)
    chosen = next((record for record in found if name_of(record.name) == owner
                   and str(record.reason or "") == reason), found[0])
    return {claim.id: Attribution(name_of(chosen.name), str(chosen.reason or ""))}


def _authority_attributed(claim: Claim) -> bool:
    """Whether the authority ``claim`` names recorded it, in their own shell,
    against the claim as it reads now (``claim_digest``): a judgment's own
    binding — an edited statement un-records the acceptance of it (P2.5a-D11).
    Never the file's ``authority`` alone: D17's laundering — an edit adding
    ``"terminal": "human", "authority": "<anyone>"`` would turn a Gap into a
    passing ``check``."""
    authority = name_of(getattr(claim, "authority", ""))
    if not authority:
        return False
    digest = claim_digest(claim)
    return any(name_of(record.name) == authority and str(record.claim_digest or "") == digest
               for record in _attributed(claim, "authority"))


def _ownership(claim: Claim, owners: Mapping[str, Any] | None) -> ClaimCause | None:
    """Why an assumption is not Assumed — no owner named, no reason, or an owner
    the channel never attributed — or None when it is (P2.1-D8). From P2.4 the
    reason is ``assumption_reason`` (a non-assumption's ``fallback``)."""
    owner = name_of(getattr(claim, "owner", ""))
    reason = assumption_reason(claim)
    if not owner:
        return ClaimCause.NO_OWNER
    if not reason.strip():
        return ClaimCause.NO_REASON
    found = (owners or {}).get(claim.id)
    if (found is None or name_of(getattr(found, "owner", "")) != owner
            or str(getattr(found, "reason", "")) != reason):
        return ClaimCause.OWNER_UNATTRIBUTED
    return None


def compose(
    claim: Claim,
    verdicts: Iterable[Verdict],
    *,
    stale: bool = False,
    stale_gates: Collection[str] = (),
    owners: Mapping[str, Any] | None = None,
) -> Composed:
    """Resolve one claim to its status AND the fact that set it — GLOSSARY §3's
    composition, one ladder for every kind, first match wins.

    `verdicts` may be the whole `ledger.verdicts` list (filtered here with
    `covering_verdicts`). `claim.gates` names the known covering gates — the
    registry-aware callers top it up with live coverage first — and a known
    gate with no covering verdict is *unrun*. A verdict whose `unqualified` is
    set is an evaluator refused at its version (only the spine sets the mark,
    `Verdict.unqualified`); an `error` outcome without it is a crash.
    `stale_gates`/`stale` are the resolver's, as before; `owners` maps a claim
    id to the `Attribution` the signing channel recorded (none in P2.1).

    1. **Failing** — a physical result failed (`refuted`), whatever the claim's
       kind now and whatever was recorded after it (`claim.physical_result` is
       the result that counts, `store`'s: the latest fail when any failed); any
       covering verdict FAILED, from an evaluator that is not unqualified
       (`fail`), whatever the kind and whether or not it is stale (D-08; R-3: a
       result never loses its power to fail); any covering PASS, from an
       evaluator that counts, whose value does not meet the claim's acceptance
       condition — of the claim's quantity, in its units (`cross_check`) —
       stale or not (`acceptance`, P2.4-D9). It only ever fails. A pass
       outside its evaluator's operating context is not compared: it does not
       count (critique 7 of the P2.4 design; GLOSSARY §2: outside, a pass reads
       Gap or Assumed, never a verdict on the design).
    2. **Skipped** — any covering evaluator ERRORED (cause `errored`, louder:
       invariant 2); else any not run behind a prerequisite that crashed
       (`prerequisite-errored`, as loud: `Verdict.blocked_kind`); else any
       not run behind any other prerequisite (`prerequisite`:
       `Verdict.blocked_by`, the spine's mark — ahead of a plain skip, review
       of P2.2); else any SKIPPED, even beside a pass (`skipped`). P2.2-D10;
       `OUTCOME_ORDER` holds the same order for `explaining_verdict`.
    3. **Gap** — any covering evaluator unqualified (`unqualified`), even
       beside a pass; then any covering pass outside its evaluator's operating
       context (`outside-context`, P2.4) — unless the claim carries an owned
       fallback (`Claim.fallback`, attributed through `owners`), which lets the
       ladder go on and reads Assumed at rung 7; a measurable claim with no
       evaluator (`no-evaluator`); an assumption with no owner named, no
       reason, or an owner the channel did not attribute (`no-owner`,
       `no-reason`, `owner-unattributed`).
    4. **Open** — a covering gate unrun (`unrun`), even beside a pass.
    5. **Stale** — `stale`, or a covering gate in `stale_gates` (`invalidated`).
    6. **Pending build** — a claim whose terminal is a measurement and no
       article settles: no result (`no-article`), or a pass that does not count
       (`physical-pass`: from an agent session, a pipe, before results were
       bound, its evidence changed — `Claim.standing` says which; with no
       standing, a raw ledger nobody judged, every pass, in P2.1's words).
    7. **Assumed** — an attributed, reasoned assumption (`owned`); a pass
       outside an operating context carried by an owned fallback (`fallback`);
       an expert-judgment claim its authority recorded and has not judged
       (`awaiting-judgment`). *Rejected:* Assumed at rung 3 (an owned fallback
       would hide an unrun or invalidated evaluator beside it).
    8. **Checked** — every covering evaluator ran, passed and is current
       (`checked`); a measurement on the current article (`on-article`) or the
       authority's judgment (`judged`), each only with `Claim.standing` judged
       `current` by the resolver (`verdicts.judge_results`), every covering
       automated evaluator composing first (S-49).

    P2.5a's rows (its D11, D12, D17; §4.4 of its design), and what slipped
    through without each: rung 1 reads a fail that carries a contradiction as
    `contradiction` (E4) and the authority's own no as `judged-fail`; rung 3
    reads an expert-judgment claim Gap until its authority records it
    (`no-authority`, `authority-unattributed`) — never Assumed from the file's
    `authority` alone, D17's laundering; rung 5 reads the newest pass a person
    made Stale, LEADING an invalidated covering evaluator and citing it (the
    fact a check run cannot clear first; `article-moved`, `claim-moved`,
    `judgment-moved`, `article-unjudged`), and with `stale=True` a counted pass
    reads Stale too (critique 8 of the design: the all-stale override promised
    nothing current, and a Checked physical claim with no covering gate slipped
    it); rung 8 mints VERIFIED. Owners come from `Claim.attributions` when no
    `owners` is passed (P2.5a-D11) — the keyword stays a test seam.

    What slipped through rungs 1 and 6 (review of P2.1): the result rung read
    only the LAST result and only for a physical claim, so a pass typed after a
    fail, or the claim's kind edited away from physical — the edit `claim
    physical`'s own refusal names — read the project ready while the fail sat
    in `results/`; and a typed pass read Checked on every channel but *ready*,
    so *checked* meant two things (GLOSSARY §3: a physical claim is Checked "on
    an article built from" the current inputs). *Rejected:* keeping the pass
    Checked and dropping *ready*'s carve-out for it — *ready* would then hold on
    a typed, unbound pass.

    **A SKIP IS NEVER A PASS. AN ERROR IS NEVER A PASS. AN UNQUALIFIED OR UNRUN
    EVALUATOR IS NEVER A PASS, beside a pass or not.** What slipped through the
    ladder this replaced (S-03): it read only the verdicts that existed, so a
    pass beside a skip read PASS, and a pass beside a gate that never ran —
    including one `check` had just refused at its first run — read PASS too,
    and `status --json` said `ready: true`. And its rung 4 read a crash as
    FAIL: a crash failed nothing, so the design took the blame for a broken
    evaluator (GLOSSARY §3, *Skipped*).

    An automated evaluator's pass never makes an assumption Checked: its kind
    says no evaluator settles it, and a tag-bound evaluator may test an
    adjacent property. Only the authority's judgment does, on a claim whose
    terminal is `human`. A
    covering fail, skip, refusal or unrun gate still counts for one (R-3).
    Physical claims compose their automated evaluators the same way (S-49): a
    failing modelled half reads Failing before any result is consulted, and an
    unrun or invalidated one ranks above Pending build (the cheap evaluator
    first, and Open and Stale stop `check` while Pending build does not).
    """
    kind = _kind(claim)
    terminal = terminal_of(claim)
    judgment = terminal == Terminal.HUMAN.value
    standing = getattr(claim, "standing", None)
    state = str(getattr(standing, "state", "") or "") if standing is not None else ""
    if owners is None:
        owners = _owners_of(claim)
    mine = covering_verdicts(claim, verdicts)
    known = _distinct(claim.gates or ())
    ran = {v.gate for v in mine}
    unrun = [g for g in known if g not in ran]
    refused = [v for v in mine if getattr(v, "unqualified", "")]
    counted = [v for v in mine if not getattr(v, "unqualified", "")]
    outside = [v for v in refused if outside_context(v)]
    unqualified = [v for v in refused if not outside_context(v)]
    failed = [v for v in counted if v.outcome == "fail"]
    errored = [v for v in counted if v.outcome == "error"]
    skipped = [v for v in counted if v.outcome == "skipped"]
    result = claim.physical_result

    def gates_of(*groups: list[Verdict]) -> tuple:
        return tuple(_distinct(v.gate for group in groups for v in group))

    # 1. Failing — a recorded fail whatever the kind (R-3); a recorded PASS
    # counts only where the terminal is a measurement or a judgment (rungs 6-8),
    # never for a claim an evaluator is meant to settle.
    if result is not None and result.passed is not True:
        contradicted = _distinct(str(item.get("gate") or "")
                                 for item in (getattr(result, "contradicts", None) or ())
                                 if isinstance(item, dict) and item.get("inside") is True)
        if contradicted and not judgment:
            return Composed(ClaimStatus.REFUTED, ClaimCause.CONTRADICTION, tuple(contradicted))
        if (judgment and name_of(getattr(result, "authority", ""))
                == name_of(getattr(claim, "authority", ""))
                and identity_matches(result.who, claim.authority)):
            return Composed(ClaimStatus.REFUTED, ClaimCause.JUDGED_FAIL)
        return Composed(ClaimStatus.REFUTED, ClaimCause.PHYSICAL_FAIL)
    if failed:
        return Composed(ClaimStatus.FAIL, ClaimCause.FAILED, gates_of(failed), failed[0])
    # The claim's own acceptance condition, compared with every counted pass of
    # its quantity (P2.4-D9): an evaluator judging against a limit of its own
    # (C3's 15 MPa) is fine against it, and the design misses the claim's.
    missed = [v for v in counted
              if v.outcome == "pass" and cross_check(claim, v).state == "fails"]
    if missed:
        return Composed(ClaimStatus.FAIL, ClaimCause.ACCEPTANCE, gates_of(missed), missed[0])
    # 2. Skipped: errored first, in the status and in what it cites — a crash
    # behind a prerequisite as loud as one in the evaluator (P2.2)
    if errored:
        return Composed(ClaimStatus.BLOCKED, ClaimCause.ERRORED, gates_of(errored, skipped),
                        errored[0])
    crashed_root = [v for v in skipped
                    if v.blocked_by and str(v.blocked_kind) == PrerequisiteKind.ERRORED]
    behind = [v for v in skipped if v.blocked_by]
    if crashed_root:
        return Composed(ClaimStatus.BLOCKED, ClaimCause.PREREQUISITE_ERRORED,
                        gates_of(crashed_root, behind, skipped), crashed_root[0])
    # Within Skipped a prerequisite skip leads a plain one (review of P2.2,
    # amending D10): the claim cited `skipped[0]`, record order, so a dependent
    # whose own tool is missing, registered first, hid the failed guard beside
    # it — `skipped: <gate> : requires <tool>`, the one advice that changes
    # nothing while the guard fails (S-54).
    if behind:
        return Composed(ClaimStatus.BLOCKED, ClaimCause.PREREQUISITE,
                        gates_of(behind, skipped), behind[0])
    if skipped:
        return Composed(ClaimStatus.BLOCKED, ClaimCause.SKIPPED, gates_of(skipped), skipped[0])
    # 3. Gap — an evaluator unqualified at its version first (the bigger fact),
    # then a pass outside an operating context, unless an owned fallback
    # carries it (then the ladder goes on, and rung 7 reads it Assumed).
    if unqualified:
        return Composed(ClaimStatus.UNCLAIMED, ClaimCause.UNQUALIFIED, gates_of(unqualified),
                        unqualified[0])
    carried = bool(outside) and _ownership(claim, owners) is None
    if outside and not carried:
        return Composed(ClaimStatus.UNCLAIMED, ClaimCause.OUTSIDE_CONTEXT, gates_of(outside),
                        outside[0])
    if kind is ClaimKind.MEASURABLE and not mine and not known:
        return Composed(ClaimStatus.UNCLAIMED, ClaimCause.NO_EVALUATOR)
    if kind is ClaimKind.ASSUMPTION and not judgment:
        unowned = _ownership(claim, owners)
        if unowned is not None:
            return Composed(ClaimStatus.UNCLAIMED, unowned)
    # An expert-judgment claim (P2.5a-D17): Gap until its authority records it
    # — unless the authority's judgment counts, or went stale on the article
    # (rungs 5 and 8). A judgment of other words is no judgment of these: when
    # the claim moved, the claim reads as one written fresh (`_JUDGED_STATES`).
    judged = state in _JUDGED_STATES
    if judgment and not judged:
        if not name_of(getattr(claim, "authority", "")):
            return Composed(ClaimStatus.UNCLAIMED, ClaimCause.NO_AUTHORITY)
        if not _authority_attributed(claim):
            return Composed(ClaimStatus.UNCLAIMED, ClaimCause.AUTHORITY_UNATTRIBUTED)
    # 4. Open
    if unrun:
        return Composed(ClaimStatus.PENDING, ClaimCause.UNRUN, tuple(unrun))
    # 5. Stale — the covering gates are the verdicts' and the known ones: a gate
    # the resolver named stale covers this claim either way. The newest pass a
    # person made leads, citing an invalidated evaluator beside it: the fact a
    # check run cannot clear first (P2.5a-D19). What slipped through the order
    # the other way round: the person runs `check`, and the claim still reads
    # Stale for the reason it hid.
    covering = _distinct([*(v.gate for v in mine), *known])
    stale_set = set(stale_gates or ())
    moved = covering if stale else [g for g in covering if g in stale_set]
    if carried and not stale:
        # A pass outside its evaluator's operating context counts for nothing,
        # current or not — the owned fallback carries the claim (P2.4-D18), as
        # its absence reads Gap at rung 3 whatever the pass's age. What slipped
        # through until the fallback's owner could be recorded (P2.5a, Fig. 4's
        # base case): the moved input that took the pass outside also
        # invalidated it, and the claim read Stale — "a check run settles it" —
        # where a check run would only confirm the pass lies outside.
        beyond = {v.gate for v in outside}
        moved = [g for g in moved if g not in beyond]
    first = next((v for v in mine if moved and v.gate == moved[0]), None)
    if state in _PERSON_STATES and (terminal == Terminal.MEASUREMENT.value
                                    or (judgment and judged)):
        return Composed(ClaimStatus.STALE, _PERSON_STATES[state], tuple(moved), first)
    if moved:
        return Composed(ClaimStatus.STALE, ClaimCause.INVALIDATED, tuple(moved), first)
    if stale and state == "current":
        # The all-stale override (critique 8 of the P2.5a design): a counted
        # physical pass with no covering gate is current by its article, and
        # `stale=True` promises that nothing reads current.
        return Composed(ClaimStatus.STALE, ClaimCause.INVALIDATED)
    # 6. Pending build — a measurement no article settles.
    if terminal == Terminal.MEASUREMENT.value and state != "current":
        if result is None:
            return Composed(ClaimStatus.UNVERIFIED, ClaimCause.NO_ARTICLE)
        return Composed(ClaimStatus.UNVERIFIED, ClaimCause.PHYSICAL_PASS, gates_of(mine))
    # 7. Assumed — an assumption by its own ownership (its reason IS its
    # fallback: `assumption_reason`), any other claim by an owned fallback, an
    # expert-judgment claim its authority recorded and has not judged.
    if kind is ClaimKind.ASSUMPTION and not judgment:
        return Composed(ClaimStatus.ASSERTED, ClaimCause.OWNED)
    if judgment and state != "current":
        return Composed(ClaimStatus.ASSERTED, ClaimCause.AWAITING_JUDGMENT)
    if carried:
        return Composed(ClaimStatus.ASSERTED, ClaimCause.FALLBACK, gates_of(outside),
                        outside[0])
    # 8. Checked
    if terminal == Terminal.MEASUREMENT.value:
        return Composed(ClaimStatus.VERIFIED, ClaimCause.ON_ARTICLE, gates_of(mine))
    if judgment:
        return Composed(ClaimStatus.VERIFIED, ClaimCause.JUDGED, gates_of(mine))
    return Composed(ClaimStatus.PASS, ClaimCause.CHECKED, gates_of(mine))


#: ``Standing.state`` -> the Stale cause it reads as (P2.5a-D19, rung 5).
_PERSON_STATES: Mapping[str, ClaimCause] = MappingProxyType({
    "article-moved": ClaimCause.ARTICLE_MOVED, "claim-moved": ClaimCause.CLAIM_MOVED,
    "judgment-moved": ClaimCause.JUDGMENT_MOVED,
    "article-unjudged": ClaimCause.ARTICLE_UNJUDGED})

#: The standings under which an expert-judgment claim's judgment exists — it
#: counts, or went stale on the design it judged — so rung 3 lets the ladder
#: reach rung 5 or 8. Not ``claim-moved``: what slipped through (review of
#: P2.5a), a judged claim whose statement was rewritten read Stale
#: `claim-moved`, which never stops `check` (P2.5a-D27), while the identical
#: claim written fresh read Gap and did — so any once-judged claim could carry
#: any new statement past `check`, D17's laundering by another route. A moved
#: claim reads as one written fresh: Gap until its authority records the claim
#: as it reads now, Assumed until they judge it. D27's reason does not reach it:
#: judging again needs no new article. *Rejected:* keeping Stale and taking
#: `claim-moved` out of ``AWAITS_A_PERSON`` (Stale would block where a fresh
#: claim's Assumed does not, and still read "test it again" for a judgment).
_JUDGED_STATES = frozenset({"current", "article-moved", "judgment-moved",
                            "article-unjudged"})


def resolve_status(
    claim: Claim,
    verdicts: Iterable[Verdict],
    *,
    stale: bool = False,
    stale_gates: Collection[str] = (),
    owners: Mapping[str, Any] | None = None,
) -> ClaimStatus:
    """`compose(...).status` — the contract's name for the one producer of a
    claim's status (R-5). Everything about the ladder is `compose`'s docstring;
    `stale=True` is the all-gates alias `StatusPrecedence.test_stale_is_not_pass`
    pins (R-6), and `owners` the additive keyword the signing channel will use."""
    return compose(claim, verdicts, stale=stale, stale_gates=stale_gates,
                   owners=owners).status


#: The verdicts `explaining_verdict` may cite, most explanatory first —
#: `OUTCOME_ORDER` up to the refusal, `compose`'s rungs 1-3: a gate that RAN and
#: failed carries the measured value and the limit, which is what the reader is
#: about to go and change; a crash is louder than a missing tool, and a skip
#: behind a crashed prerequisite as loud; a skip behind a prerequisite names
#: the root to fix before a plain skip; a skip explains before a refusal
#: (Skipped ranks above Gap); a pass explains nothing.
_EXPLAINS = OUTCOME_ORDER[:OUTCOME_ORDER.index("outside-context") + 1]


def _explains_as(verdict: Verdict) -> str:
    return OUTCOME_ORDER[min(outcome_rank(verdict), len(OUTCOME_ORDER) - 1)]


def explaining_verdict(claim: Claim, verdicts: Iterable[Verdict]) -> Verdict | None:
    """The one verdict that explains why `claim` is not settled, or None.

    Several gates can cover one claim, and the first non-passing one is not
    necessarily the one that set the status. What got through: `status` cited a
    pack gate that SKIPPED for a missing parameter as the reason a claim FAILED,
    while `check` — whose own copy of this ranking had already been fixed —
    cited the gate that ran and measured 0.7 mm against a 0.5 mm limit. Two
    commands, two stories, one ledger; the reader went looking for a missing
    parameter. So the choice lives here, once, in `compose`'s order: ran and
    failed, then errored, then not run behind a crashed prerequisite, then
    behind any other, then skipped, then unqualified — and within a rank
    the first in the order given (stable, so the same ledger always cites the
    same gate). An unqualified evaluator's verdict says `error` too; it is
    ranked by its mark, never as a crash (P2.0 D-8).

    `verdicts` may be the whole ledger's list; it is filtered with
    `covering_verdicts`, by the same id-or-tag rule as everything else here.
    """
    mine = covering_verdicts(claim, verdicts)
    for wanted in _EXPLAINS:
        for verdict in mine:
            if _explains_as(verdict) == wanted:
                return verdict
        if wanted == "fail":
            # A pass whose value misses the claim's acceptance condition explains
            # a Failing claim right after an evaluator's own fail (P2.4,
            # `compose` rung 1's order). What slipped through without it
            # (critique 5 of the P2.4 design): `last_check.json`'s `worst` named
            # no gate for such a claim — `{claim: C3, gate: null}` — while
            # `check` and the report named `bracket.bearing`.
            for verdict in mine:
                if verdict.ok and cross_check(claim, verdict).state == "fails":
                    return verdict
    return None


# --------------------------------------------------------------------------- #
# the margin, and the comparison of a value with its claim (P2.4, D-17)
# --------------------------------------------------------------------------- #
class Margin(NamedTuple):
    """One verdict's margin (D-17): ``fraction`` — signed, a fraction of the
    limit's size, > 0 inside and < 0 past — or ``None`` with ``why`` there is
    none: ``no-verdict`` (skipped, errored, or unqualified at its version),
    ``no-value``, ``no-limit``, ``no-comparator``, ``band`` (``between``: the
    verdict carries no upper bound), ``no-side`` (``==``, ``!=``),
    ``zero-limit``, or ``disagrees`` — its side contradicts the pass flag, and
    the verdict wins (S-18: a FAIL rounded to its limit drew as inside)."""

    fraction: float | None
    why: str = ""


def margin(verdict: Verdict | None) -> Margin:
    """THE margin of a verdict (D-17, P2.4-D8), by its OWN comparator and limit
    — the bar the evaluator judged; the claim's own limit is drawn from the
    claim. LE/LT ``(limit - value) / |limit|``, GE/GT ``(value - limit) /
    |limit|``. A pass marked outside its operating context is judged as the
    pass it is (its value is real; only its counting is refused). Carried on
    every JSON verdict row beside its value (``cli._verdict_row``,
    ``site.state``), so no renderer computes one — one function, so the
    ``next:`` line (P3) and the page's bullet bar (P5) cannot disagree.
    *Rejected:* in a spine module (retuning it would re-key every cache); a
    ``Verdict`` property (the same); inferring the side from ``(passed,
    measured, limit)`` (a guess, generous at equality)."""
    if verdict is None:
        return Margin(None, "no-verdict")
    if outside_context(verdict):
        passed = True
    elif getattr(verdict, "unqualified", "") or verdict.outcome in ("error", "skipped"):
        return Margin(None, "no-verdict")
    else:
        passed = verdict.outcome == "pass"
    value, limit = verdict.measured, verdict.limit
    if not _finite_real(value):
        return Margin(None, "no-value")
    if not _finite_real(limit):
        return Margin(None, "no-limit")
    comparator = str(getattr(verdict, "comparator", "") or "")
    if not comparator:
        return Margin(None, "no-comparator")
    if comparator == "between":
        return Margin(None, "band")
    if comparator in ("==", "!="):
        return Margin(None, "no-side")
    if float(limit) == 0:
        return Margin(None, "zero-limit")
    if comparator in ("<=", "<"):
        fraction = (float(limit) - float(value)) / abs(float(limit))
    elif comparator in (">=", ">"):
        fraction = (float(value) - float(limit)) / abs(float(limit))
    else:
        return Margin(None, "no-comparator")
    strict = comparator in ("<", ">")
    if passed and (fraction < 0 or (strict and fraction == 0)):
        return Margin(None, "disagrees")
    if not passed and (fraction > 0 or (not strict and fraction == 0)):
        return Margin(None, "disagrees")
    return Margin(fraction, "")


def _finite_real(value: Any) -> bool:
    return (isinstance(value, numbers.Real) and not isinstance(value, bool)
            and math.isfinite(float(value)))


def _band_hi_unusable(acceptance: Any) -> bool:
    """Whether ``acceptance`` is a band (``between``) whose ``limit_hi`` is there
    and is not a finite number — ``None`` is usable: an exact value."""
    comparator = getattr(acceptance, "comparator", None)
    if str(getattr(comparator, "value", comparator) or "") != "between":
        return False
    hi = getattr(acceptance, "limit_hi", None)
    return hi is not None and not _finite_real(hi)


def _quantity(text: Any) -> str:
    """A quantity as the comparison matches it: casefolded, ``-`` and ``_`` read
    as spaces, whitespace collapsed — "Tip-Deflection" is "tip deflection"."""
    return " ".join(str(text or "").casefold().replace("-", " ").replace("_", " ").split())


class Compared(NamedTuple):
    """``cross_check``'s answer: ``state`` — ``holds``, ``fails`` or
    ``not-compared`` — and, when not compared, ``why``: ``quantity``,
    ``units``, ``no-limit`` or ``no-value``."""

    state: str
    why: str = ""


def cross_check(claim: Claim, verdict: Verdict) -> Compared:
    """Is ``verdict``'s value evidence about ``claim``'s acceptance condition,
    and does it meet it? (P2.4-D9.) Compared only when the verdict measures the
    claim's quantity — ``settles`` against ``acceptance.quantity``, normalised
    (``_quantity``) — in its units (equal after ``strip``, case kept: an
    ``mPa`` is not an ``MPa``), the claim has a finite limit and the verdict a
    finite value; then ``Acceptance.holds`` decides. A pure function of
    ``(claim, verdict)``: ``settles`` travels on the verdict (D6), so a reader
    with no registry compares exactly as one with (invariant 12). It only ever
    fails a claim (``compose`` rung 1); a value not compared is listed
    (``not_compared``), never implied compared. *Rejected:* every covering
    verdict, whatever it measures (C4 would compare a 7 mm wall with a 204 mm
    bed fit, Q2.6); units casefolded; a status of its own."""
    acceptance = claim.acceptance
    quantity = _quantity(getattr(acceptance, "quantity", ""))
    if not quantity or _quantity(getattr(verdict, "settles", "")) != quantity:
        return Compared("not-compared", "quantity")
    if str(verdict.units or "").strip() != str(getattr(acceptance, "units", "") or "").strip():
        return Compared("not-compared", "units")
    if not _finite_real(getattr(acceptance, "limit", None)):
        return Compared("not-compared", "no-limit")
    if _band_hi_unusable(acceptance):
        # A band's upper limit that is there and is not a finite number — a hand
        # or agent edit typed `"limit_hi": "0.8"` — has nothing ``holds`` can
        # compare. What slipped through (review of P2.4): ``holds`` raised
        # TypeError out of compose, and `check`, `status` and `report` printed a
        # traceback in place of the claim. Not compared, as a missing limit is.
        return Compared("not-compared", "no-limit")
    if not _finite_real(verdict.measured):
        return Compared("not-compared", "no-value")
    return Compared("holds" if acceptance.holds(float(verdict.measured)) else "fails")


def compared_gates(claim: Claim, verdicts: Iterable[Verdict]) -> list[str]:
    """The covering evaluators whose value is compared with ``claim``'s
    acceptance condition (``cross_check`` holds or fails), in order — what a
    renderer shows as the claim's value, so it never chooses one itself
    (critique 10 of the P2.4 design: the page headlined the first passing
    verdict, a guard's `8.57 L/h` on failing C1)."""
    return _distinct(v.gate for v in covering_verdicts(claim, verdicts)
                     if v.outcome in ("pass", "fail") and not getattr(v, "unqualified", "")
                     and cross_check(claim, v).state in ("holds", "fails"))


def not_compared(claim: Claim, verdicts: Iterable[Verdict],
                 prerequisites: Mapping[str, Iterable[str]] | None = None
                 ) -> list[tuple[str, str]]:
    """``[(gate, why)]`` — the covering passes with a value that is NOT compared
    with ``claim``'s acceptance condition because they measure another quantity
    or in other units (P2.4-D10), a prerequisite of another covering evaluator
    of the claim left out (P5.1's graph rule: a guard's value is about the
    guarded analysis, not the claim). Empty for a claim with no limit, which
    nothing could be compared with. Listed in the report under the checked
    table and in ``summarise()["not_compared"]`` — never a status: most bundled
    evaluators measure quantities a project's claims phrase otherwise, and a
    blocking rule would stop every pack project."""
    if not _finite_real(getattr(claim.acceptance, "limit", None)):
        return []
    covering = covering_verdicts(claim, verdicts)
    # A prerequisite of ANY covering evaluator, whatever that one's outcome: a
    # guard's value is about the analysis it guards, failing or not (C1's
    # guard beside its failing deflection is no more C1's value than C2's).
    needed = {need for v in covering for need in (prerequisites or {}).get(v.gate, ()) or ()}
    mine = [v for v in covering if v.ok and _finite_real(v.measured)]
    out: list[tuple[str, str]] = []
    for verdict in mine:
        if verdict.gate in needed:
            continue
        found = cross_check(claim, verdict)
        if found.state == "not-compared" and found.why in ("quantity", "units"):
            out.append((verdict.gate, found.why))
    return out


class LimitDisagreement(NamedTuple):
    """A compared pair whose two limits part (P2.4-D13, S-35): the evaluator
    ``gate`` judged against ``limit`` (``units``), and ``claim``'s acceptance
    condition (``acceptance``, rendered) says another — one number in two
    places."""

    claim: str
    gate: str
    limit: float
    units: str
    acceptance: str


#: How close two limits must be to be one number (``limit_disagreements``):
#: relative 1e-9 — binary floating-point noise and nothing more, so a limit
#: computed as 0.1 + 0.2 agrees with a claim's 0.3. A gate that ROUNDS the limit
#: it reports (``bed_fit``'s ``round(usable, 1)``) parts from a claim holding the
#: unrounded value — C4 at 203.96 against a reported 204.0 — and is warned, on
#: purpose: two spellings of one number is the slip (S-35), and the fix is one
#: home. *Rejected:* a tolerance as loose as a gate's reporting precision
#: (relative 1e-3, or an absolute per-unit epsilon): it would hide exactly the
#: drift the warning exists for — a derived limit moved by less than its last
#: printed digit — and no single number fits a 0.5 mm deflection and a 204 mm
#: bed (review of P2.4: the docstring said a rounded limit "still agrees", which
#: 1e-9 never allowed).
LIMIT_REL_TOL = 1e-9


def limit_disagreements(ledger: Ledger, verdicts: Iterable[Verdict] | None = None, *,
                        stale_gates: Iterable[str] = ()) -> list[LimitDisagreement]:
    """Every compared pair — a counted, CURRENT pass or fail whose value
    ``cross_check`` compares with its claim — whose two finite limits differ
    (``math.isclose`` at ``LIMIT_REL_TOL``). In claim order, then verdict order. A
    ``check`` warning, a ``doctor`` row and a JSON key; never a status: the
    comparison already fails the generous case, and a claim looser than its
    evaluator is over-strict, not a lie. What slipped through before it (S-35):
    C3's and C4's limits were snapshots of what their evaluators compute, and a
    ``bed_xy`` edit moved the gate's 204 to 234 with nothing saying the claim
    still said 204. A verdict of a gate in ``stale_gates`` is left out: its
    limit is the one its old inputs gave, and the next check run settles it.
    What slipped through without that (review of P2.4): with C1 moved to 0.75
    before a check run, ``doctor`` blamed ``bracket.deflection`` — a gate that
    reads its limit FROM C1 — for "one number in two places", sending the
    reader to delete a constant that no longer exists."""
    stale = set(stale_gates or ())
    out: list[LimitDisagreement] = []
    for claim in ledger.claims:
        limit = getattr(claim.acceptance, "limit", None)
        if not _finite_real(limit):
            continue
        for verdict in covering_verdicts(claim, ledger.verdicts if verdicts is None
                                         else verdicts):
            if verdict.outcome not in ("pass", "fail") or getattr(verdict, "unqualified", ""):
                continue
            if verdict.gate in stale:
                continue
            if cross_check(claim, verdict).state not in ("holds", "fails"):
                continue
            if not _finite_real(verdict.limit) or math.isclose(
                    float(verdict.limit), float(limit), rel_tol=LIMIT_REL_TOL, abs_tol=0.0):
                continue
            out.append(LimitDisagreement(claim.id, verdict.gate, float(verdict.limit),
                                         str(verdict.units or ""), claim.acceptance.render()))
    return out


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
    PROVEN table's **PARTIAL** caveat (since P2.1 such a claim leaves that table
    and reads Skipped or Open, naming the gate) — the one naming the covering gate
    that produced no proof — silently vanished on precisely the machine where the
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
def compositions(
    ledger: Ledger,
    *,
    registry: Any = None,
    stale: bool = False,
    stale_gates: Collection[str] = (),
    owners: Mapping[str, Any] | None = None,
) -> dict[str, Composed]:
    """`compose` for every claim in the ledger: claim id -> `Composed`.

    The renderers' entry point (P2.1-D3): a reader that needs the words for a
    status needs its cause too, and asking for both here keeps one producer
    (R-5). Coverage as `statuses` judges it — with `registry`, the union of the
    claim's cached gates and live coverage (`effective_gates`); without one,
    `claim.gates` alone, a cached opinion. `owners` as for `compose`.
    """
    gates_for = effective_gates(ledger, registry) if registry is not None else None
    stale_set = frozenset(stale_gates or ())
    out: dict[str, Composed] = {}
    for claim in ledger.claims:
        if gates_for is not None:
            # `replace` copies; the ledger is never mutated by a derivation.
            claim = replace(claim, gates=gates_for.get(claim.id, []))
        out[claim.id] = compose(claim, ledger.verdicts, stale=stale, stale_gates=stale_set,
                                owners=owners)
    return out


def statuses(
    ledger: Ledger,
    *,
    stale: bool = False,
    registry: Any = None,
    stale_gates: Collection[str] = (),
    owners: Mapping[str, Any] | None = None,
) -> dict[str, ClaimStatus]:
    """Resolve every claim in the ledger. Claim id -> status.

    With no `registry`, coverage is judged from `claim.gates` alone, which is a
    cached opinion: a claim whose gate arrived with a pack installed after the
    claim was written will read UNCLAIMED instead of PENDING. Pass the registry
    whenever you have one — `blocking()` and `summarise()` always do. (The
    contract spells this function `statuses(ledger, *, stale=False)`;
    `registry`, `stale_gates` and `owners` are additive keywords, so every
    contract call site still works.) `stale_gates` is `resolve_status`'s: the
    gates whose verdict is not current, per gate rather than per project.
    """
    return {cid: composed.status for cid, composed in compositions(
        ledger, registry=registry, stale=stale, stale_gates=stale_gates,
        owners=owners).items()}


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
    stale_gates: Collection[str] = (),
    owners: Mapping[str, Any] | None = None,
) -> list[tuple[Claim, ClaimStatus]]:
    """Critical claims whose status must stop an irreversible spend, with the reason.

    This is the function an agent calls before letting a user order a board, cut
    stock, or pay a fab. It returns `(claim, status)` pairs because "C7 blocks"
    is not actionable and "C7 is BLOCKED: no gate ran, the solver is not
    installed" is — and because the caller would otherwise recompute the status
    it just discarded, with a second copy of the precedence rules.

    `critical=False` claims never appear: a claim no spend requires that failed
    is a note in the report, not a stop sign, and a stop sign that fires on
    claims nobody requires gets ignored the third time.

    Note what is *not* in `BLOCKING_STATUSES`: UNVERIFIED (Pending build) and
    ASSERTED (Assumed). A physical claim awaiting an article cannot block the
    spend that produces the article you would test it on, and an owned
    assumption is carried on purpose. Both are unresolved (GLOSSARY §3) and
    stop *ready* (`summarise`'s `all_required_checked`), never `check`. From
    P2.5a the same holds for a Stale that waits on a person's act on an
    article — a new article, a retest, a judgment again (`blocks`,
    `AWAITS_A_PERSON`, P2.5a-D27, which reopens P2.1-D9 for these causes
    alone): what slipped through without it, a recorded pass made `check` exit
    1 after any design edit, until an article `check` gates was built.

    From P2.1 the set stays and more facts read into it (its D9): an errored
    critical claim (Skipped), an assumption nobody owns (Gap), a pass beside an
    unrun or a skipping evaluator (Open, Skipped), a refused evaluator beside a
    pass (Gap). A passing critical claim covered by a gate in `stale_gates`
    becomes STALE and therefore blocks — with `stale=True`, every one does.
    """
    composed = compositions(ledger, registry=registry, stale=stale, stale_gates=stale_gates,
                            owners=owners)
    return [
        (claim, composed[claim.id].status)
        for claim in ledger.claims
        if claim.critical and claim.id in composed and blocks(composed[claim.id])
    ]


def blocks(composed: "Composed") -> bool:
    """Whether a REQUIRED claim reading ``composed`` stops ``check`` — the one
    predicate ``blocking``, JUnit's red and ``check``'s exit code share: its
    status is in ``BLOCKING_STATUSES``, except a Stale that waits on a
    person's act on an article (``AWAITS_A_PERSON``) with no invalidated
    covering evaluator beside it (P2.5a-D27: a check run cannot answer it, and
    `check` gates the build of the article that would). It is unresolved all
    the same: *ready* is ``summarise``'s ``all_required_checked``."""
    if composed.status not in BLOCKING_STATUSES:
        return False
    return not (composed.status is ClaimStatus.STALE and composed.cause in AWAITS_A_PERSON
                and not composed.cites)


def summarise(
    ledger: Ledger,
    registry: Any,
    *,
    stale: bool = False,
    stale_gates: Collection[str] = (),
    owners: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Counts for `atompipe status` — claims and gates, nothing else.

    Every `ClaimStatus` appears in `by_status` even at zero, and every
    `ClaimKind` in `by_kind`. A CLI table whose columns appear and disappear
    with the data is unreadable across runs and unparseable by anything
    downstream, and a missing key reads as "no problem here" when it means "no
    key here".

    From P2.1 the kept keys read per Table 1 (P2.1-D12): `by_status["blocked"]`
    holds errored claims beside skipped ones, `["unclaimed"]` unqualified and
    unowned ones. Added beside them, never in place of them:

    * `counts` — `{token: n}` over `KEY_ORDER`, zero-filled, summing to
      `n_claims` (the words' machine tokens, `STATUS_KEY`);
    * `errored` and `errored_ids` — the claims Skipped by a crash, counted apart
      (invariant 2: `N skipped (k errored)`);
    * `unresolved_ids` — the required (`critical`) claims that do not read
      Checked, Pending build and Assumed included (GLOSSARY §3);
    * `unbound_ids` — the required claims with a physical pass recorded that no
      article binds to the current inputs (every one, until article binding
      lands). Each reads Pending build, so each is in `unresolved_ids` too: the
      key says which of those already hold a result;
    * `all_required_checked` — *ready* (GLOSSARY §4, W3): at least one required
      claim, and every one reads Checked — `unresolved_ids` empty. Zero required
      claims is not ready;
    * `not_compared` (P2.4-D10, critique 10) — `{claim id: [evaluator ids]}`,
      every claim whatever its status with a covering pass whose value measures
      another quantity or units than its acceptance condition (`not_compared`),
      claims with none left out;
    * `rebuild` (P2.5a-D16) — the rebuild prediction, `rebuild(ledger)` as
      dicts: `[{article, claims, moved}]`, empty when no article moved;
    * `contradictions` (P2.5a-D14) — `{claim id: [evaluator ids]}`, each claim
      Failing on a contradiction and the evaluators it contradicts.

    `ready` keeps its meaning — `n_blocking == 0`, nothing stops `check` — for
    every reader that has it (`status --json`, the private bench, a page
    scaffolded before P2.1); P2.5's `Readiness` replaces it. *Rejected:*
    flipping it now, a key changing meaning under every reader at once.

    `n_gaps` counts gap RECORDS (`find_gaps`' Needs: an automated claim no
    registered evaluator covers), not claims reading Gap — `counts["gap"]` is
    those. Input-artifact counts are deliberately absent; `artifacts.unextracted()`
    owns those, and a summary assembled from two modules' views of the same
    ledger is how two numbers that must agree stop agreeing.

    `stale` in the result is True when any gate is stale — the alias, or a
    non-empty `stale_gates`.
    """
    composed = compositions(ledger, registry=registry, stale=stale,
                            stale_gates=stale_gates, owners=owners)
    resolved = {cid: c.status for cid, c in composed.items()}
    specs = _specs(registry)
    live = coverage(ledger, registry)
    gaps = find_gaps(ledger, registry)
    blockers = blocking(ledger, registry, stale=stale, stale_gates=stale_gates,
                        owners=owners)

    by_status = {s.value: 0 for s in ClaimStatus}
    for status in resolved.values():
        by_status[status.value] += 1

    counts = {key: 0 for key in KEY_ORDER}
    for status in resolved.values():
        counts[STATUS_KEY[status]] += 1

    by_kind = {k.value: 0 for k in ClaimKind}
    for claim in ledger.claims:
        by_kind[_kind(claim).value] += 1

    covering_specs = {s.id for s in specs if any(covers(s, c) for c in ledger.claims)}
    needs = {s.id: list(getattr(s, "needs", None) or ()) for s in specs}
    unlisted = {claim.id: [gate for gate, _why in not_compared(claim, ledger.verdicts, needs)]
                for claim in ledger.claims}
    required = [c for c in ledger.claims if c.critical]
    unresolved = [c.id for c in required
                  if resolved.get(c.id) not in (ClaimStatus.PASS, ClaimStatus.VERIFIED)]
    unbound = [c.id for c in required if composed[c.id].cause is ClaimCause.PHYSICAL_PASS]
    errored = [cid for cid, c in composed.items() if c.errored]
    contradictions = {cid: list(c.cites) for cid, c in composed.items()
                      if c.cause is ClaimCause.CONTRADICTION}

    return {
        "n_claims": len(ledger.claims),
        "n_critical": len(required),
        "by_status": by_status,
        "by_kind": by_kind,
        "counts": counts,
        "errored": len(errored),
        "errored_ids": errored,
        "n_gates": len(specs),
        "n_gates_binding": len(covering_specs),      # registered gates that reach a claim
        "n_covered_claims": sum(1 for ids in live.values() if ids),
        "n_verdicts": len(ledger.verdicts),
        "n_gaps": len(gaps),
        "n_blocking": len(blockers),
        "blocking_ids": [c.id for c, _ in blockers],
        "unresolved_ids": unresolved,
        "unbound_ids": unbound,
        "all_required_checked": bool(required) and not unresolved,
        "stale": bool(stale) or bool(stale_gates),
        "ready": not blockers,
        "not_compared": {cid: gates for cid, gates in unlisted.items() if gates},
        "rebuild": [r.to_dict() for r in rebuild(ledger)],
        "contradictions": contradictions,
    }


# --------------------------------------------------------------------------- #
# contradictions and the rebuild prediction (P2.5a-D14, D16)
# --------------------------------------------------------------------------- #
def _counted_pass(verdict: Verdict, claim: Claim, stale: Collection[str]) -> bool:
    """Whether ``verdict`` is a pass that COUNTED for ``claim`` when a fail was
    recorded: a pass, qualified (no ``unqualified`` mark), current (not in
    ``stale``), and not one whose value the claim's acceptance condition
    refutes. What a contradiction may be charged to; what slipped through the
    design's first recorder (critique of P2.5a's V-10): ``Verdict.passed`` —
    an invalidated or unqualified pass would have taken the hit."""
    return (verdict.outcome == "pass" and not getattr(verdict, "unqualified", "")
            and verdict.gate not in stale and cross_check(claim, verdict).state != "fails")


def contradicted_by(claim: Claim, verdicts: Iterable[Verdict], *,
                    stale_gates: Collection[str] = (),
                    needs: Mapping[str, Iterable[str]] | None = None,
                    codes: Mapping[str, str] | None = None) -> list[dict]:
    """The covering automated evaluators a physical FAIL on ``claim`` contradicts
    (P2.5a-D14, E4), as the channel seals them into the fail: ``[{gate, code,
    rho, value, units, inside}]`` — each covering verdict that counted
    (``_counted_pass``, ``inside: true``), plus each covering pass that lay
    outside its evaluator's operating context (``inside: false``: it never
    counted, and the record says where the context ends — P2.4's hand-off keys
    the track record by code digest AND inside/outside). Left out: a
    prerequisite of another covering evaluator (P2.4-D10: a guard vouches for
    its model, not for the claim). ``codes`` maps an evaluator to its serving
    entry's code digest — its version. Pure over what the channel resolved
    under its lock; called by the channel before it writes a fail, never by a
    reader (the verdict on the failed article's inputs is gone once they move).
    Never for an authority's no (the channel's: an expert judgment is not a
    physical result — GLOSSARY *contradiction*)."""
    stale = set(stale_gates or ())
    mine = covering_verdicts(claim, verdicts)
    guarded = {need for v in mine for need in (needs or {}).get(v.gate, ()) or ()}
    out: list[dict] = []
    for verdict in mine:
        if verdict.gate in guarded:
            continue
        if outside_context(verdict):
            if verdict.gate in stale or cross_check(claim, verdict).state == "fails":
                continue
            inside = False
        elif _counted_pass(verdict, claim, stale):
            inside = True
        else:
            continue
        value = verdict.measured if _finite_real(verdict.measured) else None
        out.append({"gate": verdict.gate, "code": str((codes or {}).get(verdict.gate) or ""),
                    "rho": str(verdict.rho or ""),
                    "value": float(value) if value is not None else None,
                    "units": str(verdict.units or ""), "inside": inside})
    return out


@dataclass(frozen=True)
class Rebuild:
    """One article the rebuild prediction names: its hash, the claims a counting
    result on it binds, and what moved (inputs first, capped)."""

    article: str
    claims: tuple
    moved: tuple

    def to_dict(self) -> dict[str, Any]:
        return {"article": self.article, "claims": list(self.claims),
                "moved": list(self.moved)}


def _rebuild_candidates(ledger: Ledger) -> list[tuple[str, Any]]:
    """``[(claim id, EntryStanding)]`` — the entries whose article the
    prediction may name: per claim, its counting fail (``physical_result``, any
    claim, any terminal but an expert judgment's — a person, not a print), and
    the newest pass a person made on a measurement terminal when its article
    moved. Never an agent's pass, a claim-moved pass, a judgment, or a pass
    beside an automated evaluator (P2.5a-D16)."""
    out: list[tuple[str, Any]] = []
    for claim in ledger.claims:
        standing = getattr(claim, "standing", None)
        entries = tuple(getattr(standing, "entries", ()) or ())
        if not entries:
            continue
        try:
            terminal = terminal_of(claim)
        except AtompipeError:
            continue
        if terminal == Terminal.HUMAN.value:
            continue
        results = list(getattr(claim, "results", ()) or ())
        counting = claim.physical_result
        if counting is not None and counting.passed is not True and counting in results:
            index = max(i for i, item in enumerate(results) if item == counting)
            found = next((e for e in entries if e.index == index), None)
            if found is not None:
                out.append((claim.id, found))
        if (terminal == Terminal.MEASUREMENT.value
                and getattr(standing, "state", "") == "article-moved"
                and standing.counted is not None):
            found = next((e for e in entries if e.index == standing.counted), None)
            if found is not None:
                out.append((claim.id, found))
    return out


def rebuild(ledger: Ledger) -> list[Rebuild]:
    """The rebuild prediction (P2.5a-D16; PLAN-v0.14 §1.5): the articles a
    counting result is bound to whose read set moved — each once, with every
    claim on it and what moved — sorted by article. Pure over a judged view
    (``Claim.standing``); a raw ledger names none. One producer for ``check``,
    ``status``, the report and every JSON channel.

    In P2.5a an article is the whole design at recording (P2.5a-D8), so this
    never under-predicts and over-predicts freely: any value change moves every
    recorded article. "Names nothing else" is P2.5b's, with ``export``'s traced
    articles (critique 12 of the P2.5a design). *Rejected:* per-claim lines (a
    person rebuilds an article; two claims on one print need one rebuild);
    naming the articles of passes that never counted (nothing rests on them)."""
    named: dict[str, tuple[list[str], tuple]] = {}
    for claim_id, entry in _rebuild_candidates(ledger):
        if entry.article_state != "moved" or not entry.article:
            continue
        claims_on, moved = named.setdefault(entry.article, ([], tuple(entry.moved)))
        if claim_id not in claims_on:
            claims_on.append(claim_id)
    return [Rebuild(article, tuple(claims_on), moved)
            for article, (claims_on, moved) in sorted(named.items())]


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
    # A claim whose file is gone and whose results file stays (`Ledger.removed`)
    # holds its id: a result is sealed to it, so a new claim under it would
    # inherit another claim's fail.
    for claim in [*ledger.claims, *(getattr(ledger, "removed", ()) or ())]:
        cid = claim.id or ""
        if not cid.startswith(prefix):
            continue
        tail = cid[len(prefix):]
        if _ASCII_INT.fullmatch(tail):
            highest = max(highest, int(tail))
    return f"{prefix}{highest + 1}"
