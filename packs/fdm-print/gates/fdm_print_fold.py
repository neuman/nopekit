# SPDX-License-Identifier: Apache-2.0
"""One verdict about a SET of printed parts, and it says which part it is about.

A real mechanical project prints a set. The gates in this pack were written for
one part, and a project that printed thirteen had to pick the worst one by hand
and hand that over — which worked, and cost two things that are written down in
the friction log it came from:

* **The report implied all thirteen were checked.** One was. It was the worst
  one, and nothing in the verdict said so.
* **The chosen part changed five times** as the geometry was fixed, so the gate
  silently moved to judging a different object between runs. *A verdict that
  changes subject without saying so is hard to reason about.*

Both are the same defect — a verdict whose subject is invisible — and both are
closed here rather than by asking every project to do the picking. A gate hands
this module one :class:`PartOutcome` per part and gets back one verdict whose

* ``measured`` is the **worst** value across the set, against that part's own
  limit,
* ``detail`` **names the worst part, the key that named the set, and the count**:
  ``worst of 13 in `mesh_paths`: deck_mid ... ; 3 of 13 past the limit``, so the
  subject is on the screen and a change of subject between runs shows up in the
  diff of the ledger instead of nowhere,
* ``locators`` carry **one pin per offending part**, so the page lights up every
  part that is wrong rather than the one that is worst,
* ``evidence`` carries the **per-part table**, which is the artifact that makes
  the headline auditable.

**A part that could not be read is a SKIP for that part, reported in the detail —
never a silent drop from the denominator.** ``worst of 12`` on a thirteen-part
project is the same lie as before, told by a different mechanism, so the count is
always the whole set and the unmeasured parts are named. And when nothing failed
but something went unmeasured, the verdict is SKIPPED rather than passed: the set
was not proven, and an unproven set resolving its claim to BLOCKED is the honest
outcome (rule 4, and invariant 1 in CLAUDE.md — a skipped gate is never a pass).

This module holds no thresholds, makes no measurement and keeps no state. Each
gate measures its own quantity per part and states the score; all the arithmetic
here is counting and choosing a maximum. The sweep's mesh cache used to live here
too, on ``ctx.extra``; it is the spine's memo now (``gates/mesh.py``,
``_read_mesh``), because a hit on a cache the spine cannot see opened nothing, and
the gate that got it was keyed as if it had read no file at all (S-27).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

from atompipe.models import Locator, Verdict

#: How many offending parts get a pin before the gate stops drawing them. Every
#: offender is in the evidence table and in the count either way — the cap trims
#: the drawing, never the measurement, exactly as ``MAX_FACE_LOCATORS`` does for
#: faces within one part. Twelve is a legible page; a project that prints forty
#: badly-oriented parts does not need forty red highlights to be told so.
MAX_PART_LOCATORS = 12

#: How many part names go inline in ``detail`` before it says "and N more". The
#: verdict is one line and the spine caps it at 500 characters; a thirteen-part
#: set whose names are all in the headline would spend the whole budget on them
#: and lose the numbers. The full list is in the evidence table.
MAX_NAMES_INLINE = 4

MEASURED = "measured"
SKIPPED = "skipped"
DEFECT = "defect"


@dataclass
class PartOutcome:
    """What one gate found on one part.

    ``score`` is the only field the fold compares: the measured quantity divided
    by the limit that applies to THAT part, so ``> 1.0`` is an offence whatever
    the quantity is and whatever per-part limit it was judged against. That
    matters inside one gate as well as across gates — ``fdm.bridge_span`` judges
    a bridge at 30 mm and a cantilever at 2 mm, and "the worst part" has to mean
    the worst *relative to what each ceiling is allowed*, not the longest span.
    """

    name: str
    status: str = MEASURED
    score: float = 0.0
    measured: float | None = None
    limit: float | None = None
    note: str = ""
    """One line naming the numbers for this part. Goes into the headline when this
    is the worst part, and into the table for all of them."""
    reason: str = ""
    """Why this part was not measured (skip) or why its numbers are fiction (defect)."""
    path: str = ""
    locators: list[Locator] = field(default_factory=list)
    row: str = ""
    """The gate's own tab-separated columns for this part's row in the table."""

    @property
    def offends(self) -> bool:
        return self.status == DEFECT or (self.status == MEASURED and self.score > 1.0)


def measured_part(name: str, *, score: float, measured: float, limit: float,
                  note: str, path: str = "", row: str = "",
                  locators: Sequence[Locator] = ()) -> PartOutcome:
    """A part this gate judged."""
    return PartOutcome(name=name, status=MEASURED, score=float(score),
                       measured=float(measured), limit=float(limit), note=note,
                       path=path, row=row, locators=list(locators))


def skipped_part(name: str, reason: str, *, path: str = "") -> PartOutcome:
    """A part this gate could not judge. It stays in the denominator."""
    return PartOutcome(name=name, status=SKIPPED, score=float("-inf"),
                       reason=reason, note=f"not measured — {reason}", path=path,
                       row="")


def defective_part(name: str, reason: str, *, path: str = "",
                   locators: Sequence[Locator] = ()) -> PartOutcome:
    """A part whose mesh is not a solid: a finding, not an absence of one.

    Kept apart from both other statuses because it is neither. It is not a skip —
    the artifact was read and it is broken, which is a real result about the
    project's build output. And it is not an ordinary failure — every number this
    pack would read off it is a fiction, so it has no score to rank against the
    parts that were measured. It sorts above all of them.
    """
    return PartOutcome(name=name, status=DEFECT, score=float("inf"),
                       reason=reason, note=reason, path=path,
                       locators=list(locators), row="")


def _names(outcomes: Sequence[PartOutcome], limit: int = MAX_NAMES_INLINE) -> str:
    shown = [o.name for o in outcomes[:limit]]
    if len(outcomes) > limit:
        shown.append(f"and {len(outcomes) - limit} more")
    return ", ".join(shown)


def write_table(ctx: Any, filename: str, gate_id: str, source: str,
                outcomes: Sequence[PartOutcome], header_lines: Sequence[str],
                columns: str) -> str:
    """The per-part table, worst first. This is the artifact the headline cites.

    Written for every multi-part run, passing or failing. A passing sweep's table
    is what somebody reads a month later to find out whether the part they are
    worried about was actually in the set — which is the question the single-part
    workaround could never answer.
    """
    path = ctx.out_path(filename)
    ranked = sorted(outcomes, key=lambda o: (o.status == SKIPPED, -o.score, o.name))
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(f"# {gate_id} — {len(outcomes)} part(s) from `{source}`\n")
        for line in header_lines:
            fh.write(f"# {line}\n")
        fh.write(f"# {sum(1 for o in outcomes if o.offends)} past the limit, "
                 f"{sum(1 for o in outcomes if o.status == SKIPPED)} not measured\n")
        fh.write(f"part\tstatus\tscore\t{columns}\tmesh\n")
        for o in ranked:
            # `inf` is printed, not blanked. An unanchored ceiling and a
            # non-solid mesh genuinely have no finite score, and a blank cell in
            # the WORST row reads as missing data — which is the one thing this
            # table exists not to look like.
            score = ("inf" if o.score == float("inf")
                     else "" if o.score == float("-inf") else f"{o.score:.4f}")
            row = o.row if o.status == MEASURED else o.reason
            fh.write(f"{o.name}\t{o.status}\t{score}\t{row}\t{o.path}\n")
    return path


def fold(gate_id: str, outcomes: Sequence[PartOutcome], *, source: str, units: str,
         quantity: str, evidence: Sequence[str] = (),
         extra_detail: str = "") -> Verdict:
    """One verdict over the whole set, honest about which part and how many.

    ``quantity`` is what the numbers mean in one or two words ("area fraction
    past the overhang limit"); it appears in the skip reason, where there is no
    measurement to speak for itself.

    The outcome ladder, worst first:

    1. **any part is not a solid** — FAIL. Its overhangs and spans are read off
       face normals that do not mean anything, so this is reported before any
       measurement and the measured parts' summary follows it.
    2. **any measured part is past its limit** — FAIL, named and counted.
    3. **nothing failed but something was not measured** — SKIPPED. The set is
       not proven; the claim resolves BLOCKED and stays visible.
    4. **everything measured and inside its limit** — PASS.
    """
    total = len(outcomes)
    if total == 0:                                    # callers check; belt and braces
        return Verdict(gate=gate_id, passed=False, skipped=True,
                       skip_reason=f"the projection names a part set (`{source}`) "
                                   f"with no parts in it — nothing was measured")

    defects = [o for o in outcomes if o.status == DEFECT]
    skips = [o for o in outcomes if o.status == SKIPPED]
    judged = [o for o in outcomes if o.status == MEASURED]
    past = [o for o in judged if o.score > 1.0]

    locators: list[Locator] = []
    for o in sorted([*defects, *past], key=lambda o: -o.score):
        if len(locators) >= MAX_PART_LOCATORS:
            break
        locators.extend(o.locators)

    skip_clause = ""
    if skips:
        skip_clause = (f"; {len(skips)} of {total} NOT measured: "
                       f"{'; '.join(f'{o.name} ({o.reason})' for o in skips[:2])}"
                       + (f" and {len(skips) - 2} more" if len(skips) > 2 else ""))

    best = max(judged, key=lambda o: o.score) if judged else None
    measured_clause = ""
    if best is not None:
        # The source key is in the headline and not only in the evidence, because
        # "which spelling won" is exactly what cost a user an afternoon the last
        # time this pack resolved a synonym family quietly. One reader glance
        # answers both "which part is this about" and "which key named the set".
        measured_clause = (f"worst of {total} in `{source}`: {best.name} {best.note}; "
                           f"{len(past)} of {total} past the limit"
                           + (f" ({_names(past)})" if past else ""))

    if defects:
        # The first sentence of the first defect only. The rest of that sentence
        # — how to repair it, and why the numbers would be fiction — is in the
        # per-part table, and a headline that spends its whole budget on one
        # part's repair instructions has stopped naming the other twelve.
        why = defects[0].reason.split(". ")[0]
        detail = (f"{len(defects)} of {total} parts in `{source}` are not solids: "
                  f"{_names(defects)} — {why}"
                  + (f". {measured_clause}" if measured_clause else "")
                  + skip_clause)
        return Verdict(gate=gate_id, passed=False, measured=float(len(defects)),
                       limit=0.0, units="parts that are not solids",
                       detail=_with(detail, extra_detail), evidence=list(evidence),
                       locators=locators)

    if past:
        return Verdict(gate=gate_id, passed=False,
                       measured=best.measured, limit=best.limit, units=units,
                       detail=_with(measured_clause + skip_clause, extra_detail),
                       evidence=list(evidence), locators=locators)

    if skips:
        # Nothing failed and not everything was checked, so nothing is claimed.
        # Passing here is the exact failure this project exists to prevent: a
        # green row over a set whose unread member is the one that was wrong.
        #
        # The unmeasured parts lead. The spine caps this string, and the names of
        # the parts nobody looked at are the part of it worth keeping — a reason
        # that runs out of room before it says which part is a reason that sent
        # the reader nowhere.
        unread = "; ".join(f"{o.name} ({o.reason})" for o in skips[:2])
        if len(skips) > 2:
            unread += f"; also {_names(skips[2:])}"
        rest = (f" The other {len(judged)} of {total} were measured and are inside "
                f"the limit." if judged else "")
        return Verdict(
            gate=gate_id, passed=False, skipped=True,
            skip_reason=(f"{len(skips)} of {total} parts in `{source}` could not be "
                         f"measured: {unread}.{rest} The set's {quantity} is not "
                         f"proven while a member of it is unread — the per-part "
                         f"table is in the evidence; fix or drop the part and re-run"),
            evidence=list(evidence))

    return Verdict(gate=gate_id, passed=True, measured=best.measured,
                   limit=best.limit, units=units,
                   detail=_with(measured_clause, extra_detail),
                   evidence=list(evidence))


def _with(detail: str, extra: str) -> str:
    return f"{detail}; {extra}" if extra else detail

