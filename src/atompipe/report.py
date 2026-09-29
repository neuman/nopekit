# SPDX-License-Identifier: Apache-2.0
"""atompipe.report — the ledger rendered: what is proven, what is not, and why.

This is the deliverable. Everything else in the spine exists so that this document
can be *generated* rather than written, and its credibility comes entirely from
what it refuses to claim. The shape it aims for reads:

    "Ready to manufacture: routed, rule-check clean, package exported, firmware fixed,
     mechanicals fit-checked. It is unverified in physical hardware."

The second sentence is the only reason anyone believed the first. Both sentences
are generated here out of claim statuses, and the order is not negotiable: if
anything critical is failing, stale, blocked, unrun or ungated, the verdict says
so before it says anything good. A report that leads with the good news and
buries the gap is a marketing document.

Five rules are made mechanical here, each because the corresponding mistake is
easy and has been made:

1. **The PROVEN table is built from `Verdict.ok`, never from `Verdict.passed`.**
   A skipped gate carries `passed=False` today — but a gate function that returns
   a dict missing the key, or a pack that sets `passed=True` next to
   `skipped=True`, would walk straight into the proof table. `ok` is the one
   predicate meaning "it ran, it did not crash, and it said yes". This is the
   report-side half of "a logger is not a gate": the gate registry refuses gates
   that cannot fail, and the report refuses to print gates that did not run.

2. **A claim can resolve PASS while a gate that covers it never ran.**
   `claims.resolve_status` sees only the verdicts that exist, and a
   registered-but-unrun gate produces none, so it cannot lower the status. That
   is exactly how a partial sweep comes to look like a complete one. The registry
   is a parameter of every function here precisely so the report can notice the
   unrun gate and mark the row.

3. **Staleness is carried all the way through to the table.** `stale_gates` —
   the gates `verdicts.resolve` found not current — turns each PASS they cover
   into STALE, and those rows leave the PROVEN table; `stale=True` does it for
   every gate at once. A missing row with a reason is the honest render; a full
   table of yesterday's numbers is a lie with a timestamp. What it replaced: one
   flag for the whole project, from one hash of the projection, so a comment
   edit in the model emptied the table and nothing said which result had moved.

4. **Absence is reported as loudly as failure.** A parameter with no rationale is
   a number nobody can defend; an ingested artifact with nothing extracted from it
   is evidence nobody read; a physical claim with no written test is one that will
   never be verified. All three get their own lines, because none of them shows up
   as a failing gate and all three sink builds.

5. **JUnit is never greener than the exit code.** CI renders the XML, not the
   exit code, and a test tab of "12 passed, 3 skipped" beside a job that exited 1
   invites somebody to fix the "flaky" exit code. So a testcase is childless only
   for `outcome == "pass"`, the critical-claims suite fails exactly where
   `claims.blocking` does, and an exit code the rendered verdicts do not explain
   is itself rendered as a failure (`render_junit`, `render_selftest_junit`).

Nothing in this module reads a clock or a module-level registry, and the
markdown report prints no time at all: a regenerated `docs/readiness.md` changes
only when the claims or the verdict outcomes do (S-89). It used to be titled
with the last sweep's time and to end with that sweep's model and inputs hashes,
so every re-run of an unchanged design rewrote a tracked file — and once
verdicts are served from the cache, "this run" would be false on every hit. The
registry is passed in — a report that reached for `gates.REGISTRY` could only be
tested against a project it had already imported.
"""
from __future__ import annotations

import math
import os
import re
from typing import Any, Collection, Iterable, Sequence

from . import __version__
from . import claims as claim_logic
from . import modelio
from . import store
from . import verdicts as verdict_logic
from .artifacts import unextracted
from .models import (
    BLOCKING_STATUSES,
    Claim,
    ClaimKind,
    ClaimStatus,
    Ledger,
    Need,
    NeedStatus,
    Tier,
    Verdict,
)
from .util import atomic_write_text, ensure_dir

# --------------------------------------------------------------------------- #
# vocabulary
# --------------------------------------------------------------------------- #
#: Fixed-width tag per claim status, for the terminal render and for `atompipe
#: status`. Deliberately NOT coloured: this output is read by agents at least as
#: often as by humans, and an ANSI escape is noise in a transcript, a log file and
#: a pipe to grep alike.
#:
#: Five characters wide, where `Verdict.render()` uses four — a gate verdict has
#: four states and a claim has ten, and squeezing "unverified in hardware" and
#: "no gate exists" into the same four-column slot as "skip" is how two very
#: different situations start reading as the same one. The four tags the method
#: names — ok / FAIL / skip / gap — keep their spelling.
STATUS_TAG: dict[ClaimStatus, str] = {
    ClaimStatus.PASS: "ok   ",
    ClaimStatus.FAIL: "FAIL ",
    ClaimStatus.REFUTED: "REFUT",      # a human tested it and it did not work
    ClaimStatus.STALE: "STALE",        # passed, but not against the current model
    ClaimStatus.BLOCKED: "skip ",      # the gate exists; its tooling does not
    ClaimStatus.PENDING: "unrun",      # the gate exists; nobody ran it
    ClaimStatus.UNCLAIMED: "gap  ",    # no gate exists at all -> capability gap
    ClaimStatus.UNVERIFIED: "phys ",   # only a real object can settle it
    ClaimStatus.VERIFIED: "ok-hw",     # a human recorded a real-world pass
    ClaimStatus.ASSERTED: "assum",     # standing assumption, unevidenced
}

#: The PROVEN section's heading, as the report emits it and as every test that
#: inspects that section finds it. The section's qualifier (`_PROVEN_QUALIFIER`)
#: follows it on the same line and is NOT part of it.
#:
#: What slipped through (S-15): invariant 4's tests located the section by a
#: literal copy of this text and read "no heading" as "an empty section", so
#: `assertNotIn(gate, section)` passed on nothing. A rename of the heading —
#: which P2's REPORT.md proposes (PLAN D-14, ask A-11) — would have kept the
#: invariant green while it tested no report at all. Now the report and the tests
#: share this one constant, and the tests fail when the report stops emitting it.
#:
#: Why this value: it is today's heading, so no rendered report changes; METHOD
#: rule 9 and invariant 4 both say PROVEN, and changing the word is a METHOD edit
#: (A-11), so a rename changes this constant's TEXT, never its name. *Rejected:*
#: the qualifier inside the constant — it is prose that changes on its own (P1.2
#: makes it "machine-verified, current"), and every such edit would move the key
#: the tests search for. *Rejected:* matching any heading containing "PROVEN" —
#: a second section that happened to use the word would be tested in its place.
SECTION_PROVEN = "## What is PROVEN"

#: What follows `SECTION_PROVEN` on its line. What slipped through: it read
#: "(machine-verified this run)", true only while every verdict came from the
#: sweep that wrote the report. From 1.2 a verdict is served from the cache when
#: its inputs have not moved — current, but not "this run" — so the old words
#: would have been false on every cache hit, in the one heading whose job is to
#: be believed. *Rejected:* "(machine-verified, cached)" — it describes where a
#: verdict came from, not why it counts; a verdict counts because it is current
#: (its inputs, code and control are the ones it was measured with), whether it
#: ran a second ago or was committed last week.
_PROVEN_QUALIFIER = "(machine-verified, current)"

#: Order used for the counts line. Good news first *in the counts only*, because
#: a count is arithmetic; the verdict sentence and the problem list below it are
#: ordered by severity, because those are judgements.
_COUNT_ORDER: tuple[ClaimStatus, ...] = (
    ClaimStatus.PASS, ClaimStatus.VERIFIED,
    ClaimStatus.FAIL, ClaimStatus.REFUTED, ClaimStatus.STALE,
    ClaimStatus.BLOCKED, ClaimStatus.PENDING, ClaimStatus.UNCLAIMED,
    ClaimStatus.UNVERIFIED, ClaimStatus.ASSERTED,
)

#: Severity order: what a reader must deal with first.
_SEVERITY: tuple[ClaimStatus, ...] = (
    ClaimStatus.FAIL, ClaimStatus.REFUTED, ClaimStatus.STALE,
    ClaimStatus.BLOCKED, ClaimStatus.PENDING, ClaimStatus.UNCLAIMED,
    ClaimStatus.UNVERIFIED, ClaimStatus.ASSERTED,
    ClaimStatus.VERIFIED, ClaimStatus.PASS,
)

#: How each blocking status reads inside the verdict sentence. Written as a
#: fragment that follows a count: "2 failing", "1 with no gate at all".
_STATUS_PHRASE: dict[ClaimStatus, str] = {
    ClaimStatus.FAIL: "failing",
    ClaimStatus.REFUTED: "refuted in hardware",
    ClaimStatus.STALE: "stale (passed, but not against the current inputs)",
    ClaimStatus.BLOCKED: "blocked on missing tooling",
    ClaimStatus.PENDING: "never run",
    ClaimStatus.UNCLAIMED: "with no gate at all",
}

#: Statuses that get a per-claim entry under "Failing / blocked". UNCLAIMED is
#: absent on purpose: a claim with no gate is a capability gap, not a defect, and
#: it is answered by installing a tool rather than by fixing the design. It gets
#: its own section.
_FAILING_SECTION: tuple[ClaimStatus, ...] = (
    ClaimStatus.FAIL, ClaimStatus.REFUTED, ClaimStatus.STALE,
    ClaimStatus.BLOCKED, ClaimStatus.PENDING,
)

#: Caps for the terminal render. The contract is "under ~40 lines for a healthy
#: project"; a *sick* project must still not scroll a terminal off its history,
#: so the lists truncate with an honest count of what was cut and a pointer to
#: the markdown report, which never truncates.
_MAX_TERMINAL_CLAIMS = 14
_MAX_TERMINAL_GAPS = 6
_MAX_REPRODUCE_GATES = 16


# --------------------------------------------------------------------------- #
# small render helpers
# --------------------------------------------------------------------------- #
def status_tag(status: ClaimStatus | str) -> str:
    """`[FAIL ]` — the bracketed, fixed-width tag for one claim status.

    Public because `atompipe status`, `atompipe claim list` and this module must
    all spell a status the same way. Two renderings of the same status is exactly
    the duplication rule 2 exists to kill: the day they drift, a reader has to
    learn which command is telling the truth.
    """
    st = _norm_status(status)
    return f"[{STATUS_TAG.get(st, str(st)[:5].ljust(5))}]"


def _norm_status(status: ClaimStatus | str) -> ClaimStatus:
    """Coerce whatever `claims.statuses` handed back into a ClaimStatus member.

    A sibling module that returns the raw string (or a value read back out of
    JSON) must not silently fall out of every `status is ClaimStatus.PASS` test
    in here — a claim that quietly matches nothing would vanish from the report
    entirely, which is the one failure mode this file cannot have.
    """
    if isinstance(status, ClaimStatus):
        return status
    return ClaimStatus(status)


def _num(value: Any) -> str:
    """Render a measured number without trailing-zero noise: 220.0 -> `220`."""
    if isinstance(value, bool):          # bool is an int; check it first
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return f"{value:g}"
    return str(value)


def _trunc(text: str, limit: int | None) -> str:
    """One line of `text`, cut to `limit` characters; `None` normalises and never cuts."""
    text = " ".join((text or "").split())
    return text if limit is None or len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _cell(text: str) -> str:
    """Make free text safe inside a markdown table cell.

    A claim statement is written by a human and regularly contains a pipe
    ("|V| <= 4.2 V"), which silently splits the row into extra columns and
    shifts every evidence path one cell left. A misaligned evidence column in a
    readiness report is worse than an ugly one.
    """
    text = " ".join((text or "").split())
    return text.replace("\\", "\\\\").replace("|", "\\|")


def _code(text: str) -> str:
    """Inline code span, with backticks stripped so the span cannot be broken."""
    return f"`{(text or '').replace('`', '')}`"


def _plural(n: int, one: str, many: str = "") -> str:
    return one if n == 1 else (many or one + "s")


def _tier_label(tier: Any) -> str:
    """`tier 1 (build)` — the number a user types plus the name they read."""
    try:
        t = Tier(int(tier))
    except (ValueError, TypeError):
        return f"tier {tier}"
    return f"tier {int(t)} ({t.name.lower()})"


def _claim_text(claim: Claim) -> str:
    return _trunc(claim.statement or claim.id, 120)


# --------------------------------------------------------------------------- #
# ledger / registry queries
# --------------------------------------------------------------------------- #
def _statuses(ledger: Ledger, registry: Any, stale: bool,
              stale_gates: Collection[str] = ()) -> dict[str, ClaimStatus]:
    """Every claim's status, judged against the LIVE registry, not the cache.

    `claims.statuses` accepts the registry as an additive keyword and the
    difference is not cosmetic: without it, coverage is read from `claim.gates`
    alone, and a claim whose gate arrived with a pack installed after the claim
    was written reads UNCLAIMED ("nobody can check this — go find a solver")
    instead of PENDING ("the gate is right there, run it"). Those two send an
    agent in opposite directions, and only one of them is true.
    """
    raw = claim_logic.statuses(ledger, stale=stale, registry=registry,
                               stale_gates=stale_gates)
    # Normalise at the boundary: see _norm_status.
    return {cid: _norm_status(st) for cid, st in raw.items()}


def _coverage(ledger: Ledger, registry: Any) -> dict[str, list[str]]:
    """claim id -> gate ids that claim to cover it. The UNION, from `claims`.

    Deliberately `claims.effective_gates` and not `claims.coverage`: the union of
    the claim's cached `gates` list with live registry coverage, which is the
    same rule `claims.statuses` resolves against. There is one definition of
    coverage and it does not live here.

    It used to. This function took the live coverage alone whenever a registry
    was passed, and *dropped* the cached half — so a claim covered by a gate from
    a pack that is not loaded in this process lost that gate from `cover`,
    `_unproven_for` found nothing missing, and the PROVEN row printed with no
    **PARTIAL** caveat. The caveat disappeared in exactly the case it exists for:
    the pack is gone, nobody can run the gate, and the report looked *more*
    certain for it. A view of coverage that gets rosier as gates go missing is
    the failure this file exists to prevent.

    With no registry the union degenerates to the claim's own cached list. A
    report must still render on a machine where the packs are not installed —
    that is precisely the machine where someone is reading it to decide whether
    to trust the build — it just cannot name gates it has never seen.
    """
    return {cid: list(gids or [])
            for cid, gids in claim_logic.effective_gates(ledger, registry).items()}


def _specs(registry: Any) -> list[Any]:
    if registry is None:
        return []
    return list(registry.specs())


def _unrun_specs(ledger: Ledger, registry: Any) -> list[Any]:
    """Gates that are registered but have no verdict at all: never run here.

    These are the quiet ones. They do not fail, they do not skip, they do not
    appear in `atompipe check` output at all — they are simply absent, and a
    claim they cover can read PASS on the strength of its *other* gates. Whole
    revisions ship in exactly this shape: a validator exists, is never invoked by
    the sweep, and its absence looks identical to success.
    """
    ran = {v.gate for v in ledger.verdicts}
    return [s for s in _specs(registry) if s.id not in ran]


def _needs(ledger: Ledger, registry: Any) -> list[Need]:
    """The capability gaps to report: what is live, plus what is still parked.

    `claims.find_gaps` is the live truth — MEASURABLE claims that no *registered*
    gate covers — and it already folds in each matching recorded Need, so the
    candidates and the user's "not yet" survive. What it cannot return is a Need
    whose gap is no longer live but whose work is not finished: one parked as
    DEFERRED, one half-way through INSTALLING, one PROPOSED and awaiting a yes.
    Those are appended here.

    SATISFIED and ABANDONED Needs are dropped: a closed gap in an open-gaps
    section is how a reader learns to skim the section.
    """
    live: list[Need] = []
    if registry is not None:
        live = list(claim_logic.find_gaps(ledger, registry))
    else:
        # Without a registry the only coverage visible is what each claim
        # remembers, which is a cached opinion — say so on the record rather
        # than reporting "no gaps" from a position of not having looked.
        for claim in ledger.claims:
            # `==`, not `is`: ClaimKind is a StrEnum, and a Claim built by hand
            # in a CLI path or a test keeps a plain `"measurable"` string that
            # `is` never matches. Identity here would drop that claim out of the
            # gap list entirely — a report that shows fewer open gaps because it
            # failed to recognise a claim is the exact direction of error this
            # file may not make. (`claims._kind` coerces for the same reason.)
            if claim.kind == ClaimKind.MEASURABLE and not claim.gates:
                live.append(Need(
                    id=f"gap-{claim.id}", claim_ids=[claim.id],
                    quantity=claim.acceptance.quantity or claim.statement,
                    note="derived without a registry; installed gates were not consulted",
                ))

    seen = {n.id for n in live}
    seen_claims: set[str] = set()
    for n in live:
        seen_claims.update(n.claim_ids or ())

    parked = {NeedStatus.PROPOSED, NeedStatus.DEFERRED, NeedStatus.INSTALLING}
    for need in ledger.needs:
        if need.id in seen or need.status not in parked:
            continue
        if any(cid in seen_claims for cid in (need.claim_ids or ())):
            continue
        live.append(need)
    return live


def _claim_verdicts(ledger: Ledger, claim: Claim) -> list[Verdict]:
    """Every verdict that speaks to this claim, by id **or tag**.

    Not `Ledger.verdicts_for`, which matches on claim id only. `run_gate` copies
    `claims` onto the verdict straight off the spec, so a pack gate bound to the
    tag "manufacturable" emits verdicts carrying that tag and no claim id at all.
    Matching on id alone silently drops exactly the gates a pack contributed —
    the report would show a claim as PENDING while the evidence that settled it
    sat two lines away in the same ledger.
    """
    return claim_logic.covering_verdicts(claim, ledger.verdicts)


def _ok_verdicts(ledger: Ledger, claim: Claim) -> list[Verdict]:
    """Verdicts that are *proof*: ran, did not crash, passed. See rule 1 above."""
    return [v for v in _claim_verdicts(ledger, claim) if v.ok]


def _unproven_for(claim_id: str, cover: dict[str, list[str]],
                  ledger: Ledger) -> list[tuple[str, str]]:
    """Covering gates that produced NO PROOF, each with the reason.

    The test is `Verdict.ok` — ran, passed, did not skip, did not error — and not
    merely "a verdict exists". Keying off existence was a live laundering hole: a
    claim covered by a cheap analytic gate and an expensive solver resolves PASS on
    the analytic one alone, and if the solver skipped for a missing binary it left
    a verdict behind, so the row printed no caveat and the gate that would actually
    have settled the claim vanished from the document entirely.

    That is the precise shape of the failure this whole project exists to prevent:
    the reader sees PROVEN, and the check that mattered never ran.
    """
    by_gate = {v.gate: v for v in ledger.verdicts}
    out: list[tuple[str, str]] = []
    for gid in cover.get(claim_id, []):
        verdict = by_gate.get(gid)
        if verdict is None:
            out.append((gid, "never run"))
        elif not verdict.ok:
            if verdict.skipped:
                out.append((gid, verdict.skip_reason or "skipped"))
            elif verdict.error:
                out.append((gid, "errored"))
            else:
                out.append((gid, "failed"))
    return out


def _counts(st: dict[str, ClaimStatus]) -> dict[ClaimStatus, int]:
    counts: dict[ClaimStatus, int] = {}
    for value in st.values():
        counts[value] = counts.get(value, 0) + 1
    return counts


def _claims_with(ledger: Ledger, st: dict[str, ClaimStatus],
                 wanted: Iterable[ClaimStatus]) -> list[Claim]:
    wanted = tuple(wanted)
    return [c for c in ledger.claims if st.get(c.id) in wanted]


def _ids(claims: Iterable[Claim], limit: int = 4) -> str:
    ids = [c.id for c in claims]
    if len(ids) <= limit:
        return ", ".join(ids)
    return ", ".join(ids[:limit]) + f", +{len(ids) - limit} more"


# --------------------------------------------------------------------------- #
# the verdict sentence
# --------------------------------------------------------------------------- #
def _verdict_sentence(ledger: Ledger, st: dict[str, ClaimStatus], registry: Any,
                      *, stale: bool, markdown: bool) -> str:
    """One honest sentence, plus the hardware caveat. Bad news first, always.

    This function is the whole point of the report, so it is worth being explicit
    about the ordering rule it encodes: a blocking gap outranks any amount of
    good news. The moment a verdict is allowed to open with "8 of 9 claims pass"
    while one critical claim has no gate at all, the reader has been told the
    project is nearly done, and the one sentence that mattered is now a footnote.

    The second sentence — "it is unverified in physical hardware" — is emitted
    unconditionally whenever an UNVERIFIED physical claim exists, however good the
    first sentence is. That clause is what separates a manufacturable design from
    a working product, and no amount of green gates removes it. Only a human
    recording a real result does.
    """
    rev = ledger.meta.revision or "this revision"
    bold = (lambda s: f"**{s}**") if markdown else (lambda s: s)
    total = len(ledger.claims)

    if total == 0:
        return bold(f"{rev} has no claims recorded, so nothing has been proven.") + \
            " A project with no claims is not a clean bill of health — start with" \
            " one: write `claims/C1.json`, a statement and an acceptance."

    critical_bad = [c for c in ledger.claims
                    if c.critical and st.get(c.id) in BLOCKING_STATUSES]
    other_bad = [c for c in ledger.claims
                 if not c.critical and st.get(c.id) in BLOCKING_STATUSES]
    n_critical = sum(1 for c in ledger.claims if c.critical)
    proven = [c for c in ledger.claims if st.get(c.id) is ClaimStatus.PASS]
    unverified = [c for c in ledger.claims if st.get(c.id) is ClaimStatus.UNVERIFIED]
    verified = [c for c in ledger.claims if st.get(c.id) is ClaimStatus.VERIFIED]

    parts: list[str] = []
    # NB: "never gated" keys off whether any VERDICT exists, not off run metadata.
    # The sweep record this used to consult was bookkeeping a caller could
    # legitimately not have written; verdicts are the evidence. Keying the headline
    # off the former let a ledger holding real results announce "no verdict of any
    # kind is recorded" — a false statement in the one document whose entire value
    # is that it makes none.
    if not ledger.verdicts:
        parts.append(bold(f"{rev} has never been gated: no verdict of any kind is"
                          f" recorded against its {total} {_plural(total, 'claim')}."))
        parts.append("Nothing below is proven because nothing has been run —"
                     " `atompipe check --tier 0` is the first step.")
    elif critical_bad:
        # Group the blocking statuses so the sentence says *how* it is blocked,
        # not merely that it is. "2 unsettled" sends a reader hunting; "1 failing
        # (C3), 1 with no gate at all (C2)" tells them which tool to reach for.
        fragments: list[str] = []
        for status in _SEVERITY:
            group = [c for c in critical_bad if st.get(c.id) is status]
            if group:
                phrase = _STATUS_PHRASE.get(status, str(status))
                fragments.append(f"{len(group)} {phrase} ({_ids(group)})")
        parts.append(bold(
            f"{rev} is NOT ready: {len(critical_bad)} of {n_critical} critical"
            f" {_plural(n_critical, 'claim')} {_plural(len(critical_bad), 'is', 'are')}"
            f" unsettled — {'; '.join(fragments)}."))
        parts.append(f"{len(proven)} of {total} {_plural(total, 'claim')}"
                     f" {_plural(len(proven), 'is', 'are')} machine-verified against"
                     f" the current model.")
    elif stale:
        # Reachable when nothing is marked critical: staleness then blocks nothing
        # mechanically, and saying so plainly is better than a silent downgrade.
        parts.append(bold(f"{rev} has no current proof: every verdict is marked"
                          f" stale, so no previous pass counts."))
        parts.append("Re-run `atompipe check` before trusting anything below.")
    else:
        parts.append(bold(f"{rev} clears every critical gate that is installed:"
                          f" {len(proven)} of {total} {_plural(total, 'claim')}"
                          f" machine-verified."))
        if other_bad:
            parts.append(f"{len(other_bad)} non-critical"
                         f" {_plural(len(other_bad), 'claim')} still"
                         f" {_plural(len(other_bad), 'has', 'have')} no result"
                         f" ({_ids(other_bad)}).")

    unrun = _unrun_specs(ledger, registry)
    if unrun and ledger.verdicts:
        parts.append(f"{len(unrun)} registered {_plural(len(unrun), 'gate')}"
                     f" {_plural(len(unrun), 'has', 'have')} never run.")

    if unverified:
        parts.append(f"It is unverified in physical hardware:"
                     f" {len(unverified)} {_plural(len(unverified), 'claim')}"
                     f" {_plural(len(unverified), 'needs', 'need')} a real object"
                     f" ({_ids(unverified)}).")
    elif verified:
        parts.append(f"{len(verified)} physical {_plural(len(verified), 'claim')}"
                     f" {_plural(len(verified), 'has', 'have')} been confirmed on a"
                     f" built object.")

    return " ".join(parts)


# --------------------------------------------------------------------------- #
# markdown sections
# --------------------------------------------------------------------------- #
def _section_proven(ledger: Ledger, st: dict[str, ClaimStatus],
                    cover: dict[str, list[str]], *, stale: bool) -> list[str]:
    """The PROVEN table. Every row cites a gate that ran and the file it wrote.

    The heading starts with `SECTION_PROVEN`, never a literal: invariant 4's tests
    find the section by that constant, and fail when it is missing.
    """
    out = [f"{SECTION_PROVEN} {_PROVEN_QUALIFIER}", ""]
    rows: list[str] = []

    for claim in ledger.claims:
        if st.get(claim.id) is not ClaimStatus.PASS:
            continue
        verdicts = _ok_verdicts(ledger, claim)

        if verdicts:
            gates = ", ".join(_code(v.gate) for v in verdicts)
            measured_bits = []
            evidence: list[str] = []
            for v in verdicts:
                if v.measured is not None:
                    measured_bits.append(f"{_num(v.measured)} {v.units}".strip())
                elif v.detail:
                    # A boolean gate ("watertight: yes") has no number, and an
                    # empty Measured cell reads as missing evidence rather than as
                    # a pass with no scalar. Show the gate's one-line detail.
                    measured_bits.append(_trunc(v.detail, 56))
                evidence.extend(v.evidence or [])
            measured = "; ".join(measured_bits) or "(no value reported)"
        else:
            # Defensive: PASS with nothing behind it means claims.resolve_status
            # and the verdict list disagree. Print the contradiction rather than
            # a clean-looking row - a silently empty Gate column is how an
            # unproven claim gets read as proven.
            gates = "**no verdict recorded — status and evidence disagree**"
            measured = "—"
            evidence = []

        unproven = _unproven_for(claim.id, cover, ledger)
        if unproven:
            # Rule 2 in the module docstring, made visible on the row it affects.
            # Carry the REASON: "did not run" tells a reader nothing actionable,
            # while "requires simpleFoam (not on PATH)" tells them what to install
            # and what the row is currently missing.
            detail = "; ".join(f"{_code(gid)} {why}" for gid, why in unproven)
            # Count GATES on both sides of the fraction, not verdicts on one and
            # gates on the other: a gate that recorded two passing verdicts would
            # otherwise read "4 of 5 covering gates" for four gates, which
            # overstates the coverage in the one number meant to understate it.
            proved = len({v.gate for v in verdicts})
            gates += (f" — **PARTIAL**: {detail}. This row rests on "
                      f"{proved} of {proved + len(unproven)} covering gates.")

        if evidence:
            shown = evidence[:3]
            ev = ", ".join(_code(p) for p in shown)
            if len(evidence) > 3:
                ev += f" (+{len(evidence) - 3} more)"
        else:
            ev = "*none written*"

        rows.append("| " + " | ".join([
            f"**{_cell(claim.id)}** {_cell(_claim_text(claim))}",
            _cell(claim.acceptance.render()) or "—",
            _cell(measured),
            gates,
            ev,
        ]) + " |")

    if rows:
        out.append("| Claim | Acceptance | Measured | Gate | Evidence |")
        out.append("|---|---|---|---|---|")
        out.extend(rows)
        out.append("")
        out.append("Every row above is backed by at least one gate that ran and "
                   "returned a pass against the inputs, code and control it has now "
                   "— a skipped, errored, stale or never-run gate can never be the "
                   "evidence for a row. Where another gate also "
                   "covers the claim and did **not** produce a pass, the row is "
                   "marked **PARTIAL** and names it with the reason: the claim "
                   "stands on the gates that ran, and you can see which ones did not.")
    elif not ledger.claims:
        out.append("Nothing — there are no claims to prove.")
    elif stale:
        out.append("**Nothing.** Every verdict is marked stale, so every claim "
                   "that previously passed is now STALE. Passing yesterday is not "
                   "proof today. Re-run `atompipe check`.")
    elif not ledger.verdicts:
        out.append("**Nothing.** No gate has ever been run in this project.")
    else:
        out.append("**Nothing.** No claim currently resolves to PASS. The sections "
                   "below say why for each one.")
    out.append("")
    return out


def _section_not_verified(ledger: Ledger, st: dict[str, ClaimStatus]) -> list[str]:
    """Physical claims: what only a real object can settle, and how to settle it."""
    out = ["## What is NOT verified", ""]

    unverified = [c for c in ledger.claims if st.get(c.id) is ClaimStatus.UNVERIFIED]
    verified = [c for c in ledger.claims if st.get(c.id) is ClaimStatus.VERIFIED]
    # `==` rather than `is` — see the note in `_needs`. A PHYSICAL claim whose
    # kind is still a plain string would otherwise vanish from the
    # "physical claims with no written test" nag, which is the one line that
    # says a claim can never be settled by anything in this pipeline.
    physical = [c for c in ledger.claims if c.kind == ClaimKind.PHYSICAL]

    if unverified:
        out.append("These need the real object. No gate in any pack can settle them, "
                   "and no number of passing gates above changes that.")
        out.append("")
        for claim in unverified:
            crit = "" if claim.critical else " *(non-critical)*"
            out.append(f"- **{claim.id}** {_claim_text(claim)}{crit}")
            out.append(f"  - **Test that would settle it:** {_physical_test(claim)}")
            if claim.rationale:
                out.append(f"  - **Why it matters:** {_trunc(claim.rationale, 200)}")
            out.append(f"  - **Record the result:** "
                       f"`atompipe claim physical {claim.id} --pass|--fail "
                       f"--detail \"...\" --when <ISO date>`")
        out.append("")

    if verified:
        for claim in verified:
            res = claim.physical_result
            when = (res.when if res and res.when else "date not recorded")
            who = f" by {res.who}" if res and res.who else ""
            detail = f" — {_trunc(res.detail, 160)}" if res and res.detail else ""
            ev = ""
            if res and res.evidence:
                ev = " [" + ", ".join(_code(p) for p in res.evidence[:3]) + "]"
            out.append(f"- **Confirmed in hardware:** **{claim.id}** "
                       f"{_claim_text(claim)} — passed {when}{who}{detail}{ev}")
        out.append("")

    if not physical:
        # An honest absence. Almost every physical project has at least one claim
        # a tool cannot settle; a ledger with none usually means nobody asked the
        # question, not that the question has no answer.
        out.append("No claim in this project is marked `physical`. Either nothing "
                   "here depends on a property only a built object can show — or "
                   "nobody has asked which properties those are. The second is far "
                   "more common.")
        out.append("")
    elif not unverified and not verified:
        out.append("Every physical claim has a recorded real-world result.")
        out.append("")
    return out


def _physical_test(claim: Claim) -> str:
    """What experiment settles this claim — the claim's own note, or a derived one.

    A physical claim with no written procedure does not get verified; it gets
    remembered as "we should check that" until the build is finished and nobody
    can be bothered. When the note is empty the acceptance at least names the
    quantity and threshold, which is enough for somebody to design the test. When
    even that is missing, say so: the claim is currently unfalsifiable.
    """
    if claim.note:
        return _trunc(claim.note, 240)
    rendered = claim.acceptance.render()
    if rendered:
        return (f"measure {rendered} on the built object and compare against the "
                f"acceptance")
    return ("**no test has been written down.** As stated, this claim cannot be "
            "settled by any observation — give it an acceptance or a procedure in "
            "its note, or it will stay on this list forever")


def _section_gaps(ledger: Ledger, st: dict[str, ClaimStatus], registry: Any) -> list[str]:
    """Capability gaps: the claims no installed gate can settle, with candidates."""
    out = ["## Open gaps", ""]
    needs = _needs(ledger, registry)
    unclaimed = [c for c in ledger.claims if st.get(c.id) is ClaimStatus.UNCLAIMED]

    if not needs:
        if unclaimed:
            out.append(f"{len(unclaimed)} {_plural(len(unclaimed), 'claim')} "
                       f"{_plural(len(unclaimed), 'resolves', 'resolve')} to "
                       f"UNCLAIMED but no capability gap has been recorded for "
                       f"{_plural(len(unclaimed), 'it', 'them')}: "
                       f"{_ids(unclaimed, limit=12)}. Run `atompipe gap --propose`.")
        elif not ledger.claims:
            out.append("No claims, so nothing to gap. This is not good news.")
        else:
            out.append("None. Every measurable claim has at least one gate "
                       "registered against it.")
        out.append("")
        return out

    out.append("A claim with no gate is a capability gap, not a defect. It is "
               "closed by installing or writing a tool — with the cost said out "
               "loud before anyone agrees to it.")
    out.append("")

    for need in needs:
        quantity = need.quantity or "(quantity not named)"
        out.append(f"### {need.id} — {quantity}  *({need.status})*")
        for cid in need.claim_ids or []:
            claim = ledger.claim(cid)
            text = _claim_text(claim) if claim else "*(claim not in ledger)*"
            tag = str(st.get(cid, "?"))
            out.append(f"- **Claim:** {cid} — {text}  `{tag}`")
            if claim and claim.gates and st.get(cid) in (ClaimStatus.PENDING,
                                                         ClaimStatus.BLOCKED):
                # Not a contradiction, and worth one line so nobody reads it as
                # one: the claim remembers a gate id from a pack that is not
                # installed *here*. "Never run" and "no gate covers it" are both
                # true, and they want different fixes - install the pack, or run
                # the sweep.
                out.append(f"  - The claim names {', '.join(_code(g) for g in claim.gates)}"
                           f", which is not registered in this environment —"
                           f" install the pack that provides it, or write one.")
        if not need.claim_ids:
            out.append("- **Claim:** *(none linked — an unattached gap will never "
                       "be prioritised)*")
        if need.claim_class:
            out.append(f"- **Class:** {need.claim_class}")
        if need.note:
            out.append(f"- **Note:** {_trunc(need.note, 220)}")

        if need.candidates:
            out.append("- **Candidate tooling:**")
            for cand in need.candidates:
                bits = [f"**{cand.name}**"]
                if cand.kind:
                    bits.append(f"({cand.kind})")
                head = " ".join(bits)
                cost = cand.cost or ("**cost not stated** — an unstated cost is how "
                                     "a 20-minute install becomes a surprise")
                line = f"  - {head} — {cand.why or 'no rationale recorded'}"
                out.append(line)
                out.append(f"    - Cost: {cost}")
                if cand.licence:
                    out.append(f"    - Licence: {cand.licence}")
                if cand.install:
                    out.append(f"    - Install: {_code(cand.install)}")
            if need.chosen:
                out.append(f"  - **Chosen:** {need.chosen}")
        else:
            out.append("- **Candidate tooling:** none proposed yet — "
                       "`atompipe gap --propose`")
        out.append("")
    return out


#: What the standing constraints say when the parameters' rationales cannot be
#: judged, completed by the reason (`_rationale_unknown`). *Rejected:* saying
#: nothing — the section's all-clear sentence then reads "every parameter carries
#: a rationale" about a model nobody read; and listing the records' own lack of a
#: rationale, which is what it did (review, checkpoint 1.3: a record holding only
#: `"source"` was printed as undefended at value `None`).
RATIONALE_UNKNOWN = "Parameter rationales are not known"


def _rationale_unknown(params: Sequence[Any] | None, model_error: str) -> str:
    """Why no parameter can be called defended or undefended here, or `""`.

    `params` is the caller's `modelio.param_view` (None: the caller gave none);
    a view with a `model_error` is one the model did not answer for, and its
    error's first line is the reason ("no model was loaded" for a project that
    names none) — said as it is, so a project with no model is not told that
    its model does not load."""
    if model_error:
        return "the model does not load"
    errors = [str(getattr(view, "model_error", "") or "") for view in params or ()]
    first = next((error for error in errors if error.strip()), "")
    if first:
        return _trunc(first.strip().splitlines()[0], 160)
    if params is None:
        return "no parameter view was given to this report"
    return ""


def _section_constraints(ledger: Ledger, st: dict[str, ClaimStatus],
                         params: Sequence[Any] | None = None,
                         model_error: str = "") -> list[str]:
    """Assumptions, undefended numbers, and evidence nobody read.

    None of these is a failing gate, and that is exactly why they get their own
    section: they are the things that sink a build without ever turning a check
    red. An assumption nobody wrote down, a constant nobody can defend, and a
    datasheet nobody opened all behave identically at the moment they bite.

    The numbers are `params`, `modelio.param_view`'s views — the model's value,
    its rationale or the record's — judged by `modelio.undefended_params`, the
    rule `doctor` and `status` print. What slipped through (review, checkpoint
    1.3): this read `ledger.params`, the records, which from 1.3 hold only what
    the model cannot (a source, a grounding), so a field nobody explained never
    reached this section, and a record holding only `"source"` was listed as
    undefended at value `None` while `doctor` said every parameter carried a
    rationale.
    """
    out = ["## Standing constraints", ""]
    asserted = [c for c in ledger.claims if st.get(c.id) is ClaimStatus.ASSERTED]
    unknown = _rationale_unknown(params, model_error)
    names = set(modelio.undefended_params(params or ()))
    undefended = [view for view in params or () if view.name in names]
    unread = unextracted(ledger)

    if not (asserted or undefended or unread):
        if unknown:
            out.append(f"None recorded: no standing assumptions, and every ingested "
                       f"artifact has been read. {RATIONALE_UNKNOWN}: {unknown}.")
        else:
            out.append("None recorded: no standing assumptions, every parameter carries "
                       "a rationale, and every ingested artifact has been read.")
        out.append("")
        return out

    out.append("Carried on faith. None of this is proven; all of it is visible, "
               "which is the whole trade.")
    out.append("")

    if asserted:
        out.append("### Assumptions")
        out.append("")
        for claim in asserted:
            src = f" *(source: {_trunc(claim.source, 80)})*" if claim.source else ""
            out.append(f"- **{claim.id}** {_claim_text(claim)}{src}")
            if claim.rationale:
                out.append(f"  - {_trunc(claim.rationale, 220)}")
        out.append("")

    if unknown:
        out.append(f"{RATIONALE_UNKNOWN}: {unknown}, so no number here is called "
                   f"defended or undefended.")
        out.append("")

    if undefended:
        out.append("### Parameters with no recorded rationale")
        out.append("")
        out.append("A number nobody can defend is a number the next agent will "
                   "change — and then re-litigate, and then change back. "
                   "`atompipe why <param>` is empty for each of these.")
        out.append("")
        out.append("| Param | Value | Source | Protected by |")
        out.append("|---|---|---|---|")
        for param in undefended:
            value = f"{_num(param.value)} {param.units}".strip()
            gates = ", ".join(_code(g) for g in param.gates) if param.gates else "—"
            out.append("| " + " | ".join([
                _code(param.name), _cell(value),
                _cell(param.source) or "*unsourced*", gates,
            ]) + " |")
        out.append("")

    if unread:
        out.append("### Ingested evidence nobody read")
        out.append("")
        out.append("An artifact with no extraction is decoration: it is in the "
                   "repo, it looks like evidence, and it grounds nothing. "
                   "`atompipe extract <id> --what ... --grounds ...` fixes it.")
        out.append("")
        out.append("| Artifact | Kind | Path | Added |")
        out.append("|---|---|---|---|")
        for art in unread:
            where = art.path or art.url or "—"
            out.append("| " + " | ".join([
                _code(art.id), _cell(str(art.kind)),
                _code(where) if where != "—" else "—",
                _cell(art.added) or "—",
            ]) + " |")
        out.append("")
    return out


def _stale_suffix(claim: Claim, cover: dict[str, list[str]],
                  stale_gates: Collection[str]) -> str:
    """`: `g.one` is stale` — which covering gates made a claim STALE, when the
    caller said (``stale_gates``); ``""`` under the all-gates alias. The reason
    each gate is stale is the resolver's and lives in `atompipe status`; the
    report names the gate so the reader knows which result to re-check."""
    stale = [gid for gid in (cover.get(claim.id) or list(claim.gates or []))
             if gid in set(stale_gates)]
    if not stale:
        return ""
    return (": " + ", ".join(_code(g) for g in stale)
            + f" {_plural(len(stale), 'is', 'are')} stale")


def _section_failing(ledger: Ledger, st: dict[str, ClaimStatus],
                     cover: dict[str, list[str]], registry: Any, *,
                     stale_gates: Collection[str] = ()) -> list[str]:
    """Everything that is red, with the verdict line that made it red."""
    out = ["## Failing / blocked", ""]
    bad = _claims_with(ledger, st, _FAILING_SECTION)
    bad.sort(key=lambda c: (_SEVERITY.index(st[c.id]), not c.critical, c.id))

    cited: set[str] = set()

    if bad:
        for claim in bad:
            status = st[claim.id]
            flag = "critical" if claim.critical else "non-critical"
            out.append(f"### {status_tag(status)} {claim.id} — {_claim_text(claim)}  "
                       f"*({status}, {flag})*")
            rendered = claim.acceptance.render()
            if rendered:
                out.append(f"- **Acceptance:** {rendered}")
            verdicts = _claim_verdicts(ledger, claim)
            for v in verdicts:
                cited.add(v.gate)
                out.append(f"- `{v.render()}`")
                if v.evidence:
                    out.append("  - evidence: "
                               + ", ".join(_code(p) for p in v.evidence[:3]))
            if not verdicts:
                gates = cover.get(claim.id) or list(claim.gates or [])
                if gates:
                    out.append("- No verdict recorded. "
                               + _plural(len(gates), "The covering gate",
                                         "The covering gates")
                               + " never ran: "
                               + ", ".join(_code(g) for g in gates) + ".")
                else:
                    out.append("- No verdict and no covering gate recorded.")
            if status is ClaimStatus.STALE:
                out.append("- Passed, but not against the current inputs"
                           + _stale_suffix(claim, cover, stale_gates)
                           + ". Nothing here is proven *now* — `atompipe check` "
                             "re-runs what moved.")
            if claim.physical_result and not claim.physical_result.passed:
                res = claim.physical_result
                out.append(f"- Hardware result: FAILED {res.when} {res.who} — "
                           f"{_trunc(res.detail, 200)}")
            out.append("")
    else:
        out.append("No claim is failing, blocked, stale or pending.")
        out.append("")

    # Gate-level problems that no claim surfaced. A gate that skipped while
    # covering nothing, or one that crashed, still proved nothing — and a crash
    # is not a failure, it is an absence of information wearing a failure's
    # clothes.
    loose = [v for v in ledger.verdicts if not v.ok and v.gate not in cited]
    unrun = _unrun_specs(ledger, registry)
    if loose or unrun:
        out.append("### Gates that produced no proof")
        out.append("")
        for v in loose:
            why = "crashed" if v.error else ("skipped" if v.skipped else "failed")
            out.append(f"- `{v.render()}`  *({why}; claims: "
                       f"{', '.join(v.claims) or 'none linked'})*")
        for spec in unrun:
            covers = ", ".join(spec.claims) if spec.claims else "nothing recorded"
            out.append(f"- `{spec.id}` — registered ({_tier_label(spec.tier)}"
                       f"{', pack ' + spec.pack if spec.pack else ''}) but never "
                       f"run. Would cover: {covers}.")
        out.append("")
        out.append("A gate that did not run is not a gate that passed. Until each "
                   "of these produces a verdict, the claims they cover rest on "
                   "whatever else happened to run.")
        out.append("")
    return out


def _code_files(registry: Any, root: str) -> dict[str, list[str]]:
    """gate id -> the files its code is, spelled as the verdict cache spells
    them (``gates/structural.py``, ``<pack:cad-solid>/gates/solid.py``) — the
    files whose bytes key its verdict, so an edit to any of them re-runs that
    gate and no other.

    Only with a ``root`` to spell them against, and a registry that hands out
    its gate functions: without the root a project's gate would be spelled by
    this machine's absolute path, and a tracked report that changes per
    checkout is the churn S-89 names. A registry that is only a list of specs
    (a test's, or a machine without the packs) lists no files."""
    pairs = getattr(registry, "pairs", None) if registry is not None else None
    if not root or not callable(pairs):
        return {}
    anchors = verdict_logic.anchors_for(root, registry, out_dir=store.out_dir(root))
    files: dict[str, list[str]] = {}
    for spec, fn in pairs():
        code = verdict_logic.code_digest(spec, fn, anchors=anchors)
        files[spec.id] = list(code.files)
    return files


def _section_reproduce(ledger: Ledger, registry: Any, *, root: str = "") -> list[str]:
    """The exact commands, and the code behind each row. A readiness report
    nobody can re-derive is a press release.

    No time, no rho, no hash of the run: those change on every re-run of an
    unchanged design, and this file is tracked (S-89). What it names instead is
    what a re-run would re-derive — the command per gate, and the files that
    gate's verdict is keyed on.
    """
    out = ["## Reproduce", ""]
    specs = _specs(registry)
    max_tier = max((int(s.tier) for s in specs), default=0)

    out.append("This file is generated. Re-derive every row above with:")
    out.append("")
    out.append("```sh")
    out.append("atompipe check --tier 0"
               "          # the inner loop: analytic gates only, seconds")
    if max_tier > 0:
        out.append(f"atompipe check --tier {max_tier}"
                   f"          # everything registered"
                   f"{' — solvers included' if max_tier >= 2 else ''}")
    out.append("atompipe report --write"
               "          # regenerates docs/readiness.md")
    out.append("```")
    out.append("")
    out.append("`check` re-runs only the gates whose inputs, code or control moved; "
               "every other verdict is served from `.atompipe/verdicts/` as it was "
               "recorded. `atompipe check --force` re-runs all of them.")
    out.append("")

    ran = {v.gate for v in ledger.verdicts}
    code = _code_files(registry, root)
    per_gate: list[tuple[str, str]] = []
    for v in ledger.verdicts:
        per_gate.append((v.gate, ", ".join(v.claims) or "no claim linked"))
    for spec in specs:
        if spec.id not in ran:
            per_gate.append((spec.id,
                             (", ".join(spec.claims) or "no claim linked")
                             + "  [never run]"))
    if per_gate:
        out.append("One gate at a time — this is the command behind each row"
                   + (", and the code it runs:" if code else ":"))
        out.append("")
        out.append("```sh")
        width = max(len(g) for g, _ in per_gate[:_MAX_REPRODUCE_GATES])
        for gate_id, covers in per_gate[:_MAX_REPRODUCE_GATES]:
            files = code.get(gate_id)
            where = f" — {', '.join(files)}" if files else ""
            out.append(f"atompipe check --only {gate_id.ljust(width)}   # {covers}{where}")
        if len(per_gate) > _MAX_REPRODUCE_GATES:
            out.append(f"# ... and {len(per_gate) - _MAX_REPRODUCE_GATES} more;"
                       f" `atompipe gate list` prints them all")
        out.append("```")
        out.append("")

    out.append("And prove the gates above can actually fail, which is the only "
               "reason their passes mean anything:")
    out.append("")
    out.append("```sh")
    out.append("atompipe gate selftest"
               "           # runs every negative control; a gate that passes its")
    out.append("                                 # own known-bad fixture is a logger, not a gate")
    out.append("```")
    out.append("")
    return out


# --------------------------------------------------------------------------- #
# public surface
# --------------------------------------------------------------------------- #
def render_markdown(ledger: Ledger, registry: Any, *, stale: bool = False,
                    stale_gates: Collection[str] = (), model_error: str = "",
                    title: str = "", root: str = "",
                    params: Sequence[Any] | None = None) -> str:
    """The full readiness report as markdown — the project's public deliverable.

    Sections, in the order a sceptical reader needs them: the verdict, what is
    proven, what is not verified, the capability gaps, the standing constraints,
    what is failing, and the commands to check all of it. The verdict leads with
    the worst thing that is true.

    `registry` is a `gates.Registry` (or None). It is a parameter rather than the
    module global so this function can be tested against a registry built in the
    test, and so a report can be rendered on a machine where the packs that
    produced the verdicts are not installed. It is typed `Any` deliberately:
    importing `gates` merely to name the type would make the report depend on the
    gate *runtime*, and rendering a ledger must never require the ability to run
    one.

    `ledger` carries the verdicts to render — the caller's resolution laid over
    the records (`verdicts.resolve`), never a list this function re-judges.
    `stale_gates` is that resolution's: the gates whose verdict is not current
    (stale, unknown, or with a control not demonstrated at this version). Each
    PASS they cover reads STALE and leaves the proof table, and the failing
    section names the gate. `stale=True` marks every gate stale at once.
    `model_error` — the model does not load — is said under the verdict
    sentence: every verdict that reads the model is then not current, and the
    reader should know why before reading the tables.

    `root`, when given, spells each gate's code files in `## Reproduce`
    relative to the project (`write_report` passes it). Without it the files
    are left out rather than spelled by this machine's absolute paths.

    `params` is the caller's `modelio.param_view` — every parameter the model
    holds, its value from the model and its rationale from the model or its
    record — and the standing constraints judge it with
    `modelio.undefended_params`, the list `doctor` prints. Never `ledger.params`:
    from 1.3 those are sparse records (review). Without it, or with a model that
    does not load, the section says the rationales are not known
    (`RATIONALE_UNKNOWN`) instead of calling any number defended or not.

    The title is the project and its revision — no time: a regenerated report
    of an unchanged design must be byte-identical (S-89).
    """
    st = _statuses(ledger, registry, stale, stale_gates)
    cover = _coverage(ledger, registry)

    name = ledger.meta.name or "(unnamed project)"
    rev = ledger.meta.revision or "unversioned"
    heading = title or f"{name} — readiness ({rev})"

    out: list[str] = [f"# {heading}", ""]
    out.append(_verdict_sentence(ledger, st, registry, stale=stale, markdown=True))
    out.append("")
    if model_error:
        out.append(f"**The model does not load**, so no verdict that reads it is "
                   f"current: {_trunc(model_error, 300)}")
        out.append("")
    if ledger.meta.summary:
        out.append(f"> {_trunc(ledger.meta.summary, 400)}")
        out.append("")

    out += _section_proven(ledger, st, cover, stale=stale)
    out += _section_not_verified(ledger, st)
    out += _section_gaps(ledger, st, registry)
    out += _section_constraints(ledger, st, params, model_error)
    out += _section_failing(ledger, st, cover, registry, stale_gates=stale_gates)
    out += _section_reproduce(ledger, registry, root=root)

    out.append("---")
    out.append("")
    out.append("*Generated by `atompipe report` from the ledger and its verdict cache. "
               "Do not hand-edit: it is an output, not a source. If a line here is "
               "wrong, the ledger is wrong.*")
    return "\n".join(out).rstrip() + "\n"


def render_terminal(ledger: Ledger, registry: Any, *, stale: bool = False,
                    stale_gates: Collection[str] = (),
                    params: Sequence[Any] | None = None) -> str:
    """The same report compressed to something an agent can hold in context.

    Under ~40 lines for a healthy project, which is the point: this is what
    `atompipe status` prints on every loop, and a status command that costs a
    screenful stops being read. Passing claims are counted, never listed — the
    only thing worth a line each is what is *not* settled.

    No ANSI colour anywhere. This output goes into transcripts, logs and pipes at
    least as often as it goes to a terminal, and an escape sequence in a diff is
    noise in all three. Status is carried by the `[ok   ]` / `[FAIL ]` / `[skip ]`
    / `[gap  ]` tags instead.

    `stale_gates` and `stale` as for `render_markdown`: a claim whose covering
    gate is stale is listed STALE, naming the gate. The head line carries no
    sweep time — there is no sweep record to read one from, and `status` prints
    its own `stale:` and `last check:` lines, each with a source.

    `params` as for `render_markdown`: the `standing:` line counts
    `modelio.undefended_params` over it, and says nothing about parameters
    without it (a model that does not load is `status`'s `model:` line).
    """
    st = _statuses(ledger, registry, stale, stale_gates)
    cover = _coverage(ledger, registry)
    lines: list[str] = []

    name = ledger.meta.name or "(unnamed)"
    rev = ledger.meta.revision or "unversioned"
    head = f"atompipe readiness — {name} {rev}"
    if not ledger.verdicts:
        head += " — never run"
    if stale:
        head += " — STALE (every verdict)"
    lines.append(head)
    lines.append(_verdict_sentence(ledger, st, registry, stale=stale, markdown=False))

    counts = _counts(st)
    total = len(ledger.claims)
    bits = [f"{STATUS_TAG[s].strip()} {counts[s]}" for s in _COUNT_ORDER if counts.get(s)]
    lines.append(f"claims {total} — {' | '.join(bits)}" if bits else f"claims {total}")

    problems = [c for c in ledger.claims
                if st.get(c.id) not in (ClaimStatus.PASS, ClaimStatus.VERIFIED)]
    problems.sort(key=lambda c: (_SEVERITY.index(st[c.id]), not c.critical, c.id))
    for claim in problems[:_MAX_TERMINAL_CLAIMS]:
        status = st[claim.id]
        why = _terminal_reason(ledger, claim, status, cover, stale_gates=stale_gates)
        lines.append(f"{status_tag(status)} {claim.id} {_trunc(claim.statement, 52)}"
                     f" — {why}")
    if len(problems) > _MAX_TERMINAL_CLAIMS:
        lines.append(f"       ... and {len(problems) - _MAX_TERMINAL_CLAIMS} more "
                     f"unsettled claims — see docs/readiness.md")

    needs = _needs(ledger, registry)
    if needs:
        lines.append(f"gaps {len(needs)}:")
        for need in needs[:_MAX_TERMINAL_GAPS]:
            cands = ", ".join(
                f"{c.name}" + (f" ({_trunc(c.cost, 40)})" if c.cost else " (cost unstated)")
                for c in need.candidates[:2]
            ) or "no candidate proposed"
            lines.append(f"  {need.id} {_trunc(need.quantity, 44)} "
                         f"({need.status}) — {cands}")
        if len(needs) > _MAX_TERMINAL_GAPS:
            lines.append(f"  ... and {len(needs) - _MAX_TERMINAL_GAPS} more")

    # One line of gate bookkeeping. `unrun` is the number that matters and the one
    # nothing else prints: gates that exist, cost nothing to run, and did not.
    ran = [v for v in ledger.verdicts if not v.skipped and not v.error]
    skipped = [v for v in ledger.verdicts if v.skipped]
    errored = [v for v in ledger.verdicts if v.error]
    unrun = _unrun_specs(ledger, registry)
    gate_bits = [f"{len(ran)} ran"]
    not_current = sorted({v.gate for v in ledger.verdicts} & set(stale_gates))
    if not_current:
        gate_bits.append(f"{len(not_current)} not current")
    if skipped:
        gate_bits.append(f"{len(skipped)} skipped")
    if errored:
        gate_bits.append(f"{len(errored)} errored")
    if unrun:
        shown = ", ".join(s.id for s in unrun[:3])
        extra = f", +{len(unrun) - 3}" if len(unrun) > 3 else ""
        gate_bits.append(f"{len(unrun)} registered but never run ({shown}{extra})")
    if ledger.verdicts or unrun:
        lines.append("gates: " + ", ".join(gate_bits))

    constraints: list[str] = []
    undefended = modelio.undefended_params(params or ())
    if undefended:
        constraints.append(f"{len(undefended)} "
                           f"{_plural(len(undefended), 'param')} with no rationale")
    unread = unextracted(ledger)
    if unread:
        constraints.append(f"{len(unread)} ingested "
                           f"{_plural(len(unread), 'artifact')} nobody read")
    if constraints:
        lines.append("standing: " + ", ".join(constraints))

    lines.append("next: atompipe check --tier 0 ; atompipe gap --propose ; "
                 "atompipe report --write")
    return "\n".join(lines) + "\n"


def _terminal_reason(ledger: Ledger, claim: Claim, status: ClaimStatus,
                     cover: dict[str, list[str]], *, full: bool = False,
                     stale_gates: Collection[str] = ()) -> str:
    """The shortest true explanation of why this claim is not settled.

    The verdict cited is `claims.explaining_verdict`'s, the same one `atompipe
    check` cites under BLOCKING, in the same `gate : body` words. What slipped
    through (S-68): this function cited the FIRST covering verdict that did not
    pass, so a claim covered by a gate that ran and measured 0.7 mm against 0.5
    and a pack gate that skipped for a missing parameter read, in `status`, as
    failing for the missing parameter — while `check`, whose private copy of the
    ranking had been fixed, cited the 0.7 mm. The fix reached one caller of two;
    the ranking now lives in `claims`, where neither can keep its own copy.

    The body prefers `detail`, then `error`, then `skip_reason`: the same order as
    `cli._blocking_reason`, so the words agree as well as the gate. It is only
    truncated here, because this line shares a terminal row with the claim;
    `full=True` keeps the words and drops the cut, for the JUnit `message` — a
    third caller that reuses this reason rather than writing a third copy of it.
    """
    def cut(text: str, limit: int) -> str:
        return _trunc(text, None if full else limit)

    if status is ClaimStatus.UNCLAIMED:
        return "no gate covers it"
    if status is ClaimStatus.UNVERIFIED:
        return "needs the real object"
    if status is ClaimStatus.ASSERTED:
        return cut(claim.rationale or claim.source or "standing assumption", 52)
    if status is ClaimStatus.REFUTED:
        res = claim.physical_result
        return cut(res.detail if res and res.detail else "refuted in hardware", 60)
    v = claim_logic.explaining_verdict(claim, ledger.verdicts)
    if v is not None:
        body = v.detail or v.error or v.skip_reason
        return f"{v.gate} : {cut(body, 56)}" if body else f"{v.gate} did not pass"
    gates = cover.get(claim.id) or list(claim.gates or [])
    if status is ClaimStatus.STALE:
        # Name the stale gate when the caller said which (`stale_gates`); under
        # the all-gates alias every passing gate is. "An older model" was the
        # only reason one global hash could give, and it is false for a stale
        # control or a moved data file.
        stale = [g for g in gates if g in set(stale_gates)]
        if stale:
            return (f"passed, but {', '.join(stale[:3])}"
                    f" {_plural(len(stale), 'is', 'are')} not current")
        passed = [v.gate for v in _ok_verdicts(ledger, claim)]
        return (f"passed, but not against the current inputs"
                f" ({', '.join(passed) or 'gate not named'})")
    if gates:
        return f"{', '.join(gates[:3])} never ran"
    return "no verdict recorded"


def write_report(root: str, ledger: Ledger, registry: Any, *,
                 stale: bool = False, stale_gates: Collection[str] = (),
                 model_error: str = "", params: Sequence[Any] | None = None) -> str:
    """Render the markdown report to `docs/readiness.md` and return its path.

    Written atomically: a half-truncated readiness report left behind by a crash
    would be a document that claims less than is true, which is a strange way to
    fail but still a wrong one.

    The destination comes from `store.project_paths`, not from a join spelled
    here. Layout is `store`'s job alone; a second module that knows where
    `docs/readiness.md` lives is a second module to edit when it moves.

    `stale_gates`, `model_error` and `params` as for `render_markdown`; `root`
    spells the gates' code files. The file holds no time and no rho, so
    rewriting it for an unchanged design and unchanged outcomes leaves the
    tracked bytes alone (S-89). `model_error` reaches the file as it reaches
    `report`'s stdout: without it, a broken model's parameters would be judged
    from whatever records exist.
    """
    path = store.project_paths(root)["readiness"]
    ensure_dir(os.path.dirname(path))
    atomic_write_text(path, render_markdown(ledger, registry, stale=stale,
                                            stale_gates=stale_gates, root=root,
                                            model_error=model_error, params=params))
    return path


# --------------------------------------------------------------------------- #
# JUnit: the same judgement, in the one format every CI already renders
# --------------------------------------------------------------------------- #
#: Where `check --junit` and `gate selftest --junit` write when given no path,
#: relative to the project root.
#:
#: Why this value: `.atompipe/out/` is gate scratch, ignored both by the
#: `.atompipe/.gitignore` that `init` writes and by this repository's own
#: `.gitignore`, so a `check --junit` in CI or on a laptop never dirties the tree.
#: *Rejected:* the project root — tracked, so every run would leave a modified
#: file behind and the clean-tree gate (G5) would read red for a report.
JUNIT_DEFAULT = ".atompipe/out/junit.xml"

#: The suites `render_junit` always writes, in this order, even when one is
#: empty: a CI step (and `tests/oracle/bracket_signature.py`) finds them by name.
#: The claims are split by criticality because only the critical suite is the
#: exit code's judgement — its red count IS `len(claims.blocking())`.
#: *Rejected:* writing a suite only when it has testcases — an absent suite reads
#: exactly like one that had nothing to say; one suite for every claim — a failing
#: nice-to-have would then sit in the count the exit code is held to.
_JUNIT_SUITES = ("gates", "claims.critical", "claims.not-critical")

#: The prefix a verdict's `error` carries when the gate was refused because its
#: negative control is not demonstrated at this version (Phase 1.2's admission).
#: It renders `<error type="not-admitted">` rather than `type="error"`, because
#: "the instrument is not trusted" and "the instrument crashed" send a reader to
#: different places. *Rejected:* a substring match — an exception whose text
#: merely quotes the phrase is still a crash, so only the start counts.
_NOT_ADMITTED = "not admitted:"

#: The code points XML 1.0 forbids (its `Char` production): C0 controls other
#: than tab, LF and CR; the surrogates, which a Python `str` can hold alone (bytes
#: decoded with `surrogateescape`) and UTF-8 cannot encode; and U+FFFE/U+FFFF.
#: What slipped through while designing this (the slice probe behind phase-1.md
#: 1.1): ElementTree writes every one of them raw — an ANSI colour escape from a
#: solver's log (`\x1b`), a NUL from a C string, a form feed from a pager — and the
#: file then fails to parse, so the CI step that reads it shows no failures at
#: all. *Rejected:* dropping them (the text a reader needs to recognise, an escape
#: sequence, vanishes); XML 1.1 (most CI parsers refuse it).
_XML_ILLEGAL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff￾￿]")


def junit_safe(text: Any) -> str:
    """`text` with every code point XML 1.0 forbids shown as visible `\\xNN` / `\\uNNNN`.

    `"\\x1b[31mred"` becomes the eleven visible characters `\\x1b[31mred`, so the
    escape a gate's log carried is still recognisable where CI shows it. Tab, LF,
    CR and every other character (accents, emoji, U+007F) are left alone.
    Backslashes are not escaped: a Windows path in a message must read as one.
    `None` is `""`; anything else is `str()`-ed first.
    """
    if text is None:
        return ""
    return _XML_ILLEGAL.sub(
        lambda m: (f"\\x{ord(m.group()):02x}" if ord(m.group()) < 0x100
                   else f"\\u{ord(m.group()):04x}"),
        str(text))


def _xml_sub(parent: Any, tag: str, **attrs: Any) -> Any:
    """A child element whose every attribute went through `junit_safe`.

    Built through this helper only, so no value reaches the tree unsanitised —
    a second, direct `SubElement` call is how one field would slip past.
    `makeelement` + `append` is what `SubElement` does, without an import here.
    """
    child = parent.makeelement(tag, {k: junit_safe(v) for k, v in attrs.items()})
    parent.append(child)
    return child


def _xml_text(element: Any, text: Any) -> None:
    safe = junit_safe(text)
    if safe:
        element.text = safe


def _xml_properties(parent: Any, pairs: Iterable[tuple[str, Any]]) -> None:
    props = _xml_sub(parent, "properties")
    for name, value in pairs:
        _xml_sub(props, "property", name=name, value=value)


def _junit_time(seconds: Any) -> str:
    """Seconds as a plain decimal (`0.0004`, never `4e-04`): JUnit's schema types
    `time` as xs:decimal, and a parser that follows it rejects an exponent."""
    try:
        value = float(seconds)
    except (TypeError, ValueError):
        return "0"
    if not math.isfinite(value) or value <= 0:
        return "0"
    return f"{value:.6f}".rstrip("0").rstrip(".") or "0"


def _junit_measured(verdict: Verdict) -> str:
    """`measured 0.699718 mm vs limit 0.5 mm`, then one line per evidence file."""
    units = f" {verdict.units}" if verdict.units else ""
    lines: list[str] = []
    if verdict.measured is not None:
        line = f"measured {_num(verdict.measured)}{units}"
        if verdict.limit is not None:
            line += f" vs limit {_num(verdict.limit)}{units}"
        lines.append(line)
    elif verdict.limit is not None:
        lines.append(f"limit {_num(verdict.limit)}{units}")
    lines += [f"evidence: {path}" for path in verdict.evidence or []]
    return "\n".join(lines)


def _junit_outcome(case: Any, verdict: Verdict) -> None:
    """The outcome child of one gate's testcase: nothing, iff `outcome == "pass"`.

    Keyed off `Verdict.outcome`, the one definition, and never off `passed`: a
    writer reading the flag renders a skip that also said `passed=True` as a
    green testcase — the generous direction phase-1.md names as this format's
    failure (R-5; `RenderersAgree.test_junit` holds all 8 flag combinations).
    """
    outcome = verdict.outcome
    if outcome == "pass":
        return
    if outcome == "fail":
        child = _xml_sub(case, "failure", type="fail",
                         message=verdict.detail or "the gate reported a failure")
        _xml_text(child, _junit_measured(verdict))
    elif outcome == "error":
        error = str(verdict.error)
        child = _xml_sub(case, "error",
                         type="not-admitted" if error.startswith(_NOT_ADMITTED) else "error",
                         message=error)
        _xml_text(child, verdict.detail)
    else:
        _xml_sub(case, "skipped", message=verdict.skip_reason or "skipped")


def _junit_gate_case(suite: Any, gate_id: str, pack: str, verdict: Verdict | None, *,
                     cached: bool = False, not_run: str = "") -> None:
    """One gate's testcase. No verdict is a skip — "not run: <why>" — never a pass.

    A cached testcase has `time="0"` (a cached row never replays a duration: the
    run took none) and carries `<properties><property name="cached"
    value="true"/></properties>`, which is metadata, not an outcome.
    """
    case = _xml_sub(suite, "testcase",
                    classname=f"pack.{pack}" if pack else "project", name=gate_id,
                    time="0" if cached or verdict is None
                    else _junit_time(verdict.duration_s))
    if verdict is None:
        # A registered gate with nothing to say is the quiet failure rule 2 in
        # this module's docstring describes; here it stays visible as a skip.
        _xml_sub(case, "skipped",
                 message=f"not run: {not_run}" if not_run
                 else "not run: no verdict in this run")
        return
    if cached:
        _xml_properties(case, [("cached", "true")])
    _junit_outcome(case, verdict)


def _junit_red(element: Any) -> int:
    """Testcases under `element` with a failure or an error: what CI paints red."""
    return sum(1 for case in element.iter("testcase")
               if case.find("failure") is not None or case.find("error") is not None)


def _junit_tally(element: Any, suites: Iterable[Any]) -> None:
    """Set `tests failures errors skipped time` on `element` from the testcases
    inside `suites` — counted, never carried: a CI summary reads these
    attributes, and a count kept beside the children drifts from them."""
    cases = [case for suite in suites for case in suite.iter("testcase")]
    element.set("tests", str(len(cases)))
    element.set("failures", str(sum(1 for c in cases if c.find("failure") is not None)))
    element.set("errors", str(sum(1 for c in cases if c.find("error") is not None)))
    element.set("skipped", str(sum(1 for c in cases if c.find("skipped") is not None)))
    element.set("time", _junit_time(sum(float(c.get("time") or 0) for c in cases)))


def _junit_serialise(root: Any, suites: list[Any]) -> str:
    import xml.etree.ElementTree as ET
    for suite in suites:
        _junit_tally(suite, [suite])
    _junit_tally(root, suites)
    ET.indent(root, space="  ")
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            + ET.tostring(root, encoding="unicode") + "\n")


def _claim_case(suite: Any, ledger: Ledger, claim: Claim, status: ClaimStatus,
                cover: dict[str, list[str]], *, red: bool,
                stale_gates: Collection[str] = ()) -> None:
    """One claim's testcase, asserting "this claim does not block the spend".

    `red` is the caller's: blocking (critical) or FAIL/REFUTED (not critical).
    A red claim whose status came from a gate that crashed is an `error` — a crash
    reads louder than a failure (invariant 2) — else a `failure` typed with the
    status. Everything short of settled is skipped with its words: `partial: …`
    for a PASS that rests on fewer gates than cover it, `needs a real part`,
    `assumed`. Childless only for PASS with every covering gate passed, and for
    VERIFIED.
    """
    case = _xml_sub(suite, "testcase", classname=suite.get("name"), name=claim.id,
                    time="0")
    rendered = claim.acceptance.render() if claim.acceptance else ""
    words = (claim.statement or "") + (f"\nacceptance: {rendered}" if rendered else "")
    if red:
        reason = _terminal_reason(ledger, claim, status, cover, full=True,
                                  stale_gates=stale_gates)
        explaining = claim_logic.explaining_verdict(claim, ledger.verdicts)
        # Only a FAIL can come from a crash. A REFUTED claim with a crashed
        # modelled-half gate beside it is refuted by the real object, and saying
        # "error" there would send the reader to the gate instead of the part.
        if (status is ClaimStatus.FAIL and explaining is not None
                and explaining.outcome == "error"):
            child = _xml_sub(case, "error", type="error", message=reason)
        else:
            child = _xml_sub(case, "failure", type=status.value, message=reason)
        _xml_text(child, words)
        return
    if status is ClaimStatus.PASS:
        unproven = _unproven_for(claim.id, cover, ledger)
        if unproven:
            _xml_sub(case, "skipped", message="partial: " + "; ".join(
                f"{gid} {why}" for gid, why in unproven))
        return
    if status is ClaimStatus.VERIFIED:
        return
    if status is ClaimStatus.UNVERIFIED:
        _xml_sub(case, "skipped", message="needs a real part")
    elif status is ClaimStatus.ASSERTED:
        _xml_sub(case, "skipped", message="assumed")
    else:
        why = _terminal_reason(ledger, claim, status, cover, full=True,
                               stale_gates=stale_gates)
        _xml_sub(case, "skipped", message=f"{status.value}: {why}")


def render_junit(ledger: Ledger, verdicts: Iterable[Verdict], registry: Any, *,
                 tier: Any, ready: bool, exit_code: int, when: str,
                 not_run: Any = None, cached: Iterable[str] = frozenset(),
                 stale: bool = False, spine: str = "",
                 stale_gates: Collection[str] = ()) -> str:
    """This command's run as JUnit XML — never greener than its exit code.

    A CI system renders this file, not the exit code, so the file carries the
    same judgement the exit code was made from and may only ever be redder:

    * **`gates`** — one testcase per REGISTERED gate, in registry order, so the
      count is stable between runs. Its outcome is the gate's verdict in
      `verdicts` (this command's rows): childless iff `outcome == "pass"`; fail
      -> `<failure type="fail">`; error -> `<error type="error">`, or
      `type="not-admitted"` for an error that starts `not admitted:`; skipped ->
      `<skipped>`. A gate with no row is `<skipped message="not run: <why>">`,
      the why from `not_run` (`(gate, reason)` pairs or a mapping, e.g. "above
      the tier ceiling", "excluded by --only"). Gates in `cached` get `time="0"`
      and a `cached` property.
    * **`claims.critical`** — one testcase per critical claim, each asserting
      "does not block the spend". Its red testcases are exactly
      `claims.blocking(ledger, registry, stale=stale)`, so failures plus errors
      equal `len(blocking())`; zero claims adds one failing `no claims recorded`
      (zero blocking claims out of zero is not readiness, and `check` exits 1).
    * **`claims.not-critical`** — FAIL and REFUTED red; every other non-pass
      skipped with its reason.

    `ledger` must be the ledger the exit code was judged from, and `stale` and
    `stale_gates` what it was judged with (the resolution's stale gates, from
    1.2): the claim suites are recomputed from them, never from `verdicts`.
    Should a caller hand over an exit code the claims do not
    explain anyway — a non-zero code with nothing blocking, as a stale project
    rendered without `stale=True` would give — `claims.critical` gains one
    failing `exit code` testcase saying so. A caller's disagreement surfaces as
    red, never as a green file beside a red job.

    Root `<properties>`: `spine_version`, `exit_code`, `tier`, `ready`, `when`,
    and `spine` when given (the spine digest, from Phase 1.2). `when` is the
    caller's timestamp; this function reads no clock. Every attribute and text
    value goes through `junit_safe`, so the file parses whatever a gate wrote.
    """
    import xml.etree.ElementTree as ET        # ~6 ms; only `--junit` pays it

    code = int(exit_code)
    root = ET.Element("testsuites", {"name": "atompipe check"})
    props = [("spine_version", __version__), ("exit_code", str(code)),
             ("tier", str(_int_or(tier))), ("ready", "true" if ready else "false"),
             ("when", when)]
    if spine:
        props.append(("spine", spine))
    _xml_properties(root, props)
    suites = {name: _xml_sub(root, "testsuite", name=name) for name in _JUNIT_SUITES}

    rows = list(verdicts or ())
    by_gate = {v.gate: v for v in rows}
    reasons = dict(not_run or ())
    cached = frozenset(cached or ())
    if registry is not None:
        gates = [(spec.id, spec.pack) for spec in _specs(registry)]
    else:
        # No registry, no list of what should have run: fall back to the rows
        # themselves, in order, rather than render an empty and green suite.
        gates = [(gate_id, "") for gate_id in dict.fromkeys(v.gate for v in rows)]
    for gate_id, pack in gates:
        verdict = by_gate.get(gate_id)
        _junit_gate_case(suites["gates"], gate_id, pack or (verdict.pack if verdict else ""),
                         verdict, cached=gate_id in cached,
                         not_run=str(reasons.get(gate_id, "")))

    st = _statuses(ledger, registry, stale, stale_gates)
    cover = _coverage(ledger, registry)
    blocking = {c.id for c, _ in claim_logic.blocking(ledger, registry, stale=stale,
                                                      stale_gates=stale_gates)}
    for claim in ledger.claims:
        status = st[claim.id]
        if claim.critical:
            _claim_case(suites["claims.critical"], ledger, claim, status, cover,
                        red=claim.id in blocking, stale_gates=stale_gates)
        else:
            _claim_case(suites["claims.not-critical"], ledger, claim, status, cover,
                        red=status in (ClaimStatus.FAIL, ClaimStatus.REFUTED),
                        stale_gates=stale_gates)

    critical = suites["claims.critical"]
    if not ledger.claims:
        case = _xml_sub(critical, "testcase", classname="claims.critical",
                        name="no claims recorded", time="0")
        _xml_sub(case, "failure", type="no-claims",
                 message="no claims recorded, so nothing was checked — an empty "
                         "ledger is not a clean bill of health")
    if code != 0 and _junit_red(critical) == 0:
        case = _xml_sub(critical, "testcase", classname="claims.critical",
                        name="exit code", time="0")
        _xml_sub(case, "failure", type="exit-code",
                 message=f"the command exits {code} and no claim here blocks: the "
                         f"exit code and these verdicts disagree, and the exit code "
                         f"is the one CI obeys")
    return _junit_serialise(root, list(suites.values()))


#: The suffix `gates.selftest` files a control's verdict under (`f"{spec.id}#selftest"`,
#: spelled inline there). The `controls` testcase drops it: the suite already
#: says these are controls, and the bare gate id is what a reader searches the
#: code for. A result without the suffix keeps its name as it is. *Rejected:*
#: keeping it — `bracket.deflection#selftest` beside a suite named `controls` is
#: the same fact twice, and a CI that splits `classname.name` on dots reads it oddly.
_SELFTEST_SUFFIX = "#selftest"


def render_selftest_junit(results: Iterable[Verdict], *, exit_code: int, when: str,
                          baselines: Iterable[Verdict] | None = None) -> str:
    """`gate selftest` as JUnit XML — never greener than its exit code.

    * **`controls`** — one testcase per result of `gates.selftest`, named for
      the gate (the `#selftest` suffix dropped; the suite says what it is).
      Childless iff the control fired (`outcome == "pass"`); a control that did
      not fire, or crashed, is red; a tooling skip is `<skipped>`.
    * **`baselines`** — pack mode only (`baselines` not None): each gate's
      verdict on its pack's own `selftest/baseline.json`, childless iff it passed.

    A selftest that ran nothing and exits 1 (no `--allow-empty`) gains one
    failing `no controls ran` testcase; any other non-zero exit with nothing red
    gains a failing `exit code` testcase. An empty file beside a red job is the
    "ran zero controls and reported success" failure, in XML.
    """
    import xml.etree.ElementTree as ET        # ~6 ms; only `--junit` pays it

    code = int(exit_code)
    results = list(results or ())
    root = ET.Element("testsuites", {"name": "atompipe gate selftest"})
    _xml_properties(root, [("spine_version", __version__), ("exit_code", str(code)),
                           ("when", when)])
    controls = _xml_sub(root, "testsuite", name="controls")
    suites = [controls]
    for verdict in results:
        gate_id = verdict.gate
        if gate_id.endswith(_SELFTEST_SUFFIX):
            gate_id = gate_id[: -len(_SELFTEST_SUFFIX)]
        _junit_gate_case(controls, gate_id, verdict.pack, verdict)
    if baselines is not None:
        base = _xml_sub(root, "testsuite", name="baselines")
        suites.append(base)
        for verdict in baselines:
            _junit_gate_case(base, verdict.gate, verdict.pack, verdict)

    if code != 0 and _junit_red(root) == 0:
        if not results:
            case = _xml_sub(controls, "testcase", classname="controls",
                            name="no controls ran", time="0")
            _xml_sub(case, "failure", type="empty",
                     message=f"no control ran and the command exits {code}: a "
                             f"selftest that exercised nothing has shown no gate "
                             f"can fail")
        else:
            case = _xml_sub(controls, "testcase", classname="controls",
                            name="exit code", time="0")
            _xml_sub(case, "failure", type="exit-code",
                     message=f"the command exits {code} and no control or "
                             f"baseline here failed: the exit code and these "
                             f"results disagree, and the exit code is the one CI "
                             f"obeys")
    return _junit_serialise(root, suites)


def _int_or(value: Any) -> Any:
    """`int(value)` when it has one (a `Tier` renders as `0`, not `Tier.INSTANT`)."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return value


__all__ = [
    "STATUS_TAG",
    "SECTION_PROVEN",
    "JUNIT_DEFAULT",
    "RATIONALE_UNKNOWN",
    "status_tag",
    "render_terminal",
    "render_markdown",
    "write_report",
    "render_junit",
    "render_selftest_junit",
    "junit_safe",
]
