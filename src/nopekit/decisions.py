# SPDX-License-Identifier: Apache-2.0
"""nopekit.decisions — the log of what was chosen, and of everything that LOST.

This module is rule 3 of the method (*every constant carries provenance —
including the alternatives that were rejected and why*) made into a file you can
actually read.

A mature project's decision log runs to thousands of lines, newest first, and the lines
that earn their keep are the negative ones:

    a 4.0 mm plate was tried first and deflected 3.75 mm, 7.5x the limit
    PLA was tried and rejected — it creeps under a load it holds all day
    do not fit the shorter screw: it strips the boss at the first service

None of those can be recovered from the CAD, the git history, or anybody's
memory two months later. Without them every fresh context window re-litigates
every settled number, re-proposes the value that lost, and burns a day
discovering the same wall. A rejected alternative *with its reason* is the only
thing that stops it.

Two functions carry the weight:

* `render_log` writes the whole log, newest first, for humans and for git.
* `why` is the context-window win. Given one parameter name or claim id it
  returns a dense block — current value, whether it is derived and from what,
  the rationale, **every** rejected alternative, the gates that protect it and
  how they last ran, the input artifacts that ground it, and the decisions that
  moved it. An agent calls `why` instead of reading thousands of lines, which is
  the difference between one screen of context and a third of a window.

Nothing here reads the clock (contract rule 3): `add` takes `when` from the
caller. A log that stamped its own timestamps could not be tested and would
quietly disagree with the run history about the order events happened in.

Nothing here saves, either. `add` mutates the in-memory ledger and returns the
new `Decision`; persisting it is `store.save`'s job, so a command that fails
half way through does not leave a decision recorded for a change that never
landed.
"""
from __future__ import annotations

import difflib
import math
import os
import textwrap
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from . import modelio, store
from .models import (Claim, ClaimKind, ClaimStatus, Decision, Ledger, Param, Rejected,
                     Rejection, ReturnCondition, Verdict, slugify)
from .util import NopekitError, atomic_write_text, ensure_dir, iter_suffix_unique

__all__ = ["add", "changed_in", "render_log", "write_log", "why"]


#: Render width for `why`. 78 keeps the block inside an 80-column terminal and
#: inside a diff view with a gutter, which is where an agent actually reads it.
WIDTH = 78

#: How many "did you mean" suggestions an unmatched `why` offers. Three is the
#: point where the list still scans as a hint rather than as a dump of the
#: parameter table.
_SUGGESTIONS = 3


# --------------------------------------------------------------------------- #
# coercion helpers
# --------------------------------------------------------------------------- #
def _one_line(text: Any) -> str:
    """Collapse whitespace so a value cannot break the structure it sits in.

    A title with an embedded newline turns one markdown `##` heading into a
    heading plus a stray paragraph, and a summary with one breaks the entry's
    first line away from its heading. Long-form prose belongs in `body`, which
    is emitted verbatim.
    """
    return " ".join(str(text or "").split())


def _clean_names(values: Iterable[str] | None, *, field: str) -> list[str]:
    """Normalise a list of param names / claim ids: strip, drop blanks, dedupe.

    Order is preserved rather than sorted — the caller lists the parameter that
    actually moved first, and that ordering is information.

    Deliberately does NOT check that the names exist in the ledger. A decision
    frequently records a *removal* ("dropped `hook_thickness_mm`, the hook is
    gone"), and refusing to log the removal of a thing because the thing is no
    longer there would be exactly backwards. `why` compensates: a name that
    appears only in decisions still renders, flagged, so a typo surfaces the
    first time anyone looks the parameter up instead of never.
    """
    out: list[str] = []
    for value in values or ():
        if not isinstance(value, str):
            raise NopekitError(
                f"{field} takes names as strings, got {type(value).__name__}: {value!r}"
            )
        name = value.strip()
        if name and name not in out:
            out.append(name)
    return out


def _coerce_rejected(items: Iterable[Any] | None) -> list[Rejection]:
    """Accept `Rejected` (or a `Rejection`), a dict, or a `(value, why[, evidence])`
    pair; return each as a `Rejection` (SPEC 14).

    A dict may carry the phase-2 keys — `type`, `by`, `returns_if` (a dict or a
    `ReturnCondition`) and `input` — and each is held to the strict reader's
    rules (`store.rejection_problem`) before anything is recorded, so `decide`
    never writes what every command then refuses.

    The tuple and dict forms exist so the CLI and a pack can hand over what they
    parsed without importing the model, but every form goes through the same
    gate: **a rejected alternative with no reason is refused.** "a 4.0 mm plate
    was tried" is worthless; "a 4.0 mm plate was tried and deflected 3.75 mm, 7.5x
    the limit" is the whole reason this log exists. Letting the reasonless
    form through would fill the log with lines that look like provenance and
    answer nothing, and the next agent would re-try it anyway.
    """
    out: list[Rejection] = []
    for item in items or ():
        extra: dict[str, Any] = {}
        if isinstance(item, Rejected):
            value, reason, evidence = item.value, item.why, item.evidence
            if isinstance(item, Rejection):
                extra = {k: v for k, v in item.to_dict().items()
                         if k in ("type", "by", "returns_if", "input")}
        elif isinstance(item, dict):
            # Not Rejected.from_dict: that raises TypeError on a dict missing
            # `value`/`why`, and a malformed --rejected flag is a user error
            # that deserves the sentence below, not a traceback.
            value = item.get("value", "")
            reason = item.get("why", "")
            evidence = item.get("evidence", "")
            extra = {k: item[k] for k in ("type", "by", "returns_if", "input") if k in item}
            if isinstance(extra.get("returns_if"), ReturnCondition):
                extra["returns_if"] = extra["returns_if"].form()
        elif isinstance(item, str):
            raise NopekitError(
                f"rejected alternative {item!r} has no reason. Give a "
                f"(value, why) pair: what lost, and the concrete thing that "
                f"beat it — 'it deflected 3.75 mm, 7.5x the limit', not 'worse'"
            )
        elif isinstance(item, Sequence) and 2 <= len(item) <= 3:
            value, reason = item[0], item[1]
            evidence = item[2] if len(item) > 2 else ""
        else:
            raise NopekitError(
                f"cannot read a rejected alternative from {item!r} — expected "
                f"Rejected, a dict, or a (value, why) pair"
            )

        value, reason, evidence = _one_line(value), _one_line(reason), _one_line(evidence)
        if not value:
            raise NopekitError(
                "a rejected alternative needs a value: the thing that lost, "
                "rendered as it would be written ('4.0 mm', 'PLA')"
            )
        if not reason:
            raise NopekitError(
                f"rejected {value!r} has no reason. Say concretely what beat it; "
                f"a rejection without its reason gets re-proposed by the next "
                f"agent that reads the model"
            )
        if isinstance(extra.get("by"), str):
            extra["by"] = _one_line(extra["by"])
        problem = store.rejection_problem(extra)
        if problem:
            raise NopekitError(f"rejected {value!r}{problem}")
        out.append(Rejection.from_dict(dict(extra, value=value, why=reason,
                                            evidence=evidence)))
    return out


def _date_of(when: str) -> str:
    """The date half of an ISO timestamp, for a heading. Anything else passes through.

    `when` is whatever the caller stamped — `utcnow_iso()` gives
    `2026-09-11T14:46:00Z`, and a human editing the ledger may well write
    `2026-09-11`. Both should head the same way.
    """
    when = _one_line(when)
    return when.split("T", 1)[0] if "T" in when else when


# --------------------------------------------------------------------------- #
# add
# --------------------------------------------------------------------------- #
def add(
    ledger: Ledger,
    *,
    title: str,
    summary: str,
    when: str,
    rejected: Iterable[Any] = (),
    params_changed: Iterable[str] = (),
    claims_changed: Iterable[str] = (),
    body: str = "",
    evidence: Iterable[str] = (),
) -> Decision:
    """Record one decision at the FRONT of the ledger's log and return it.

    Newest first is the storage order as well as the render order. The log is
    read top-down by someone asking "what changed lately?", and an append-only
    tail means the answer is always at the bottom of a file that only grows —
    at a few thousand lines that is a scroll nobody does.

    The id is `slugify(title)`, deduped against ids already in the ledger
    (`iter_suffix_unique`). Slug rather than a number because a decision is
    referenced from `Param.changed_in` and from prose, and `trace-width-0-6-mm`
    survives a merge that renumbers while `D17` silently points at a different
    decision.

    `when` is required and comes from the caller — see contract rule 3. `title`
    and `summary` are required too: a decision with no summary is a timestamp,
    and the log is read by people who will not open `body`.

    No side effect on the parameters it names. It used to set each one's
    `changed_in` to this decision's id — a second record written by a command
    that was asked to write one, and a pointer that `modelio` refreshing params
    from the model quietly lost. From 1.3 a decision is one file
    (`decisions/<id>.json`), a param record may not carry `changed_in` at all
    (the strict reader names it derived), and `changed_in(ledger, name)` reads
    the pointer off the log whenever it is wanted, so it cannot fall behind.

    Does not save. Call `store.save(root, ledger)` when the whole command
    succeeded; a decision recorded for a change that then failed to land is a
    lie that outlives the session.
    """
    title = _one_line(title)
    if not title:
        raise NopekitError("a decision needs a title — it becomes the entry's heading and its id")
    summary = _one_line(summary)
    if not summary:
        raise NopekitError(
            f"decision {title!r} needs a summary: one line saying what changed and why. "
            f"A decision with no summary is a timestamp"
        )
    when = _one_line(when)
    if not when:
        raise NopekitError(
            f"decision {title!r} needs a `when` (ISO date or timestamp). The caller "
            f"supplies it; this module never reads the clock"
        )

    decision = Decision(
        id=iter_suffix_unique(slugify(title), {d.id for d in ledger.decisions}),
        title=title,
        when=when,
        summary=summary,
        rejected=_coerce_rejected(rejected),
        params_changed=_clean_names(params_changed, field="params_changed"),
        claims_changed=_clean_names(claims_changed, field="claims_changed"),
        evidence=_clean_names(evidence, field="evidence"),
        body=(body or "").strip(),
    )

    ledger.decisions.insert(0, decision)
    return decision


def changed_in(ledger: Ledger, name: str) -> str:
    """The id of the newest decision that moved parameter `name`, else `""`.

    Derived, never stored (see `add`). Newest by `when`, and among decisions of
    the same `when` the first in storage order — `add` prepends, so that is the
    later one. `when` decides first because once decisions are files there is
    no storage order a writer controls: a directory listing is sorted by name,
    and a slug says nothing about time. ISO stamps compare as text, and a bare
    date sorts before a timestamp on the same day; an undated decision is the
    oldest.
    """
    wanted = (name or "").strip()
    best: tuple[str, int] | None = None
    found = ""
    for index, decision in enumerate(ledger.decisions):
        if wanted not in (decision.params_changed or ()):
            continue
        key = (_one_line(decision.when), -index)
        if best is None or key > best:
            best, found = key, decision.id
    return found


# --------------------------------------------------------------------------- #
# render_log  —  docs/decisions.md
# --------------------------------------------------------------------------- #
#: The log's header names its one writer and the records it renders. What
#: slipped through (review of phase 2): it said `nopekit decide` wrote it (no
#: longer, since 1.3) and to "edit the ledger" — the generated index, rebuilt
#: after the next command, so a reader who followed it lost the edit.
_LOG_PREAMBLE = """\
Generated by `nopekit report --write` from decisions/ — edit the decision records,
not this file.

Newest first. Every entry names what **lost** and why. That is the point: a
rejected alternative with its reason is the only thing that stops the next
person (or the next context window) re-proposing it and re-discovering the same
wall. `nopekit why <name>` pulls one decision's, parameter's or claim's slice of
this file.\
"""


def render_log(ledger: Ledger) -> str:
    """Render the whole decision log as markdown, newest first.

    Rendered in *storage* order rather than sorted by `when`. Sorting looks
    tidier and is wrong twice: several decisions on one day carry the same date
    and would be reordered arbitrarily on every regeneration (a diff that
    churns is a diff nobody reads), and an entry whose date was mistyped would
    be silently relocated instead of standing out next to its neighbours.
    `add` prepends, so storage order *is* newest first.

    `body` is emitted verbatim as markdown. Bodies should start their headings
    at `###`; an `##` inside one reads as a new decision entry.
    """
    out: list[str] = ["# Decision log", "", _LOG_PREAMBLE, ""]

    if not ledger.decisions:
        out += [
            "_No decisions recorded yet._",
            "",
            "Record one the moment something is ruled out — that is when the",
            "reason is still in your head:",
            "",
            "    nopekit decide --title ... --summary ...",
            "",
        ]
        return "\n".join(out)

    for decision in ledger.decisions:
        date = _date_of(decision.when)
        heading = f"## {date} — {decision.title}" if date else f"## {decision.title}"
        out += [heading, "", f"`{decision.id}`", ""]

        if decision.summary:
            out += [decision.summary, ""]

        if decision.rejected:
            out += ["**Rejected**", ""]
            from . import report                   # a reader's module: not at import
            for item in decision.rejected:
                # The type, the condition as written and the input (SPEC 14) —
                # never a live state: this is a generated document.
                line = (f"- **{item.value}** — {item.why}"
                        f"{report.rejection_suffix(item, decision=decision.id)}")
                if item.evidence:
                    line += f"  _(evidence: {item.evidence})_"
                out.append(line)
            out.append("")

        touched: list[str] = []
        if decision.params_changed:
            touched.append("**Params:** " + ", ".join(f"`{p}`" for p in decision.params_changed))
        if decision.claims_changed:
            touched.append("**Claims:** " + ", ".join(f"`{c}`" for c in decision.claims_changed))
        if decision.evidence:
            touched.append("**Evidence:** " + ", ".join(f"`{e}`" for e in decision.evidence))
        if touched:
            # Two trailing spaces = a markdown hard break, so the three stay on
            # their own lines instead of reflowing into one paragraph.
            out += ["  \n".join(touched), ""]

        if decision.body:
            out += [decision.body, ""]

        out += ["---", ""]

    return "\n".join(out).rstrip() + "\n"


def write_log(root: str, ledger: Ledger) -> str:
    """Write `docs/decisions.md` atomically and return the path.

    Atomic (temp file + `os.replace`) for the same reason the ledger is: this
    file is regenerated on every `report --write`, and a crash against a truncating
    open would leave the log — the one artifact that holds the rejected
    alternatives in human-readable form — empty.

    The path comes from `store.project_paths` so the layout stays defined in one
    module; nothing here joins `docs/decisions.md` by hand.
    """
    path = store.project_paths(root)["decisions"]
    ensure_dir(os.path.dirname(path))
    atomic_write_text(path, render_log(ledger))
    return path


# --------------------------------------------------------------------------- #
# why  —  one item's whole provenance, in one screen
# --------------------------------------------------------------------------- #
def _wrap(
    text: str, indent: str = "  ", *, hanging: str | None = None, collapse: bool = True
) -> list[str]:
    """Wrap prose to WIDTH. Returns lines (possibly empty), never a trailing blank.

    `collapse=False` keeps runs of spaces inside the text. Needed for anything
    pre-aligned: `Verdict.render()` pads its tag to `[ok  ]` so it columns up
    with `[FAIL]`, and collapsing that to `[ok ]` turns a scannable column of
    verdicts into a ragged list.
    """
    text = _one_line(text) if collapse else " ".join(str(text or "").splitlines()).rstrip()
    if not text:
        return []
    return textwrap.fill(
        text,
        width=WIDTH,
        initial_indent=indent,
        subsequent_indent=indent if hanging is None else hanging,
        break_long_words=False,
        break_on_hyphens=False,
    ).splitlines()


def _section(out: list[str], heading: str) -> None:
    out.append("")
    out.append(heading)



def _typed(item: Any) -> bool:
    """Whether `item` is a decision's rejection that says more than text (SPEC
    14): a type, a person, a return condition or an input."""
    return isinstance(item, Rejection) and bool(
        item.type or item.by or item.returns_if is not None or item.input)


def _dedupe(rows: Sequence[tuple]) -> list[tuple]:
    """`(item, origin, key)` rows, each loser once — deduped on (value, why)
    case-folded, since a decision usually repeats the rejection it also wrote
    where the param lives. When the duplicate is a typed decision rejection and
    the row kept is not, the typed one takes its place, with its decision
    origin: a type and a return condition are facts the text copy lacks."""
    seen: dict[tuple[str, str], int] = {}
    unique: list[tuple] = []
    for row in rows:
        item = row[0]
        key = (_one_line(item.value).casefold(), _one_line(item.why).casefold())
        if key not in seen:
            seen[key] = len(unique)
            unique.append(row)
        elif _typed(item) and not _typed(unique[seen[key]][0]):
            unique[seen[key]] = row
    return unique


def _facts_line(ledger: Ledger, key: Any, evidence: "_Evidence | None") -> list[str]:
    """The facts line under a decision's rejection (SPEC 14): its type, its
    return condition's state now and its input — `report.rejection_facts`
    over the `RejectionRow` the caller composed. None for a PARAMS or param
    record row, which prints exactly as before."""
    if key is None or evidence is None:
        return []
    row = evidence.row(ledger, key)
    if row is None:
        return []
    from . import report                           # a reader's module: not at import
    return [f"      {report.rejection_facts(row, ledger)}"]


def _rejected_lines(items: Sequence[tuple], ledger: Ledger | None = None,
                    evidence: "_Evidence | None" = None) -> list[str]:
    """Render `(rejected, source[, key])` rows as the densest honest form.

    These are the highest-value lines in the whole block, so they lead with the
    value that lost and the reason on the same line; the provenance tail (which
    decision recorded it, what evidence backs it) only costs a second line when
    it exists, and a decision's rejection adds its facts line (`_facts_line`).
    """
    lines: list[str] = []
    for row in items:
        item, source = row[0], row[1]
        key = row[2] if len(row) > 2 else None
        lines += _wrap(f"- {item.value} — {item.why}", indent="  ", hanging="    ")
        tail = []
        if item.evidence:
            tail.append(f"evidence: {item.evidence}")
        if source:
            tail.append(f"from: {source}")
        if tail:
            lines += _wrap("  ·  ".join(tail), indent="      ")
        if ledger is not None:
            lines += _facts_line(ledger, key, evidence)
    return lines


def _collect_rejected(
    ledger: Ledger, *, own: Sequence[Rejected], names: set[str]
) -> list[tuple]:
    """Every rejected alternative touching this item, from the record AND the log.

    A parameter's own `rejected` list holds what lost when the value was set;
    the decision log holds what lost every time it moved since. Reading only one
    of the two is how "we already tried that" gets forgotten — the reference
    project's rigid-hook rejection lives in a decision, not in a parameter
    record. Deduped on (value, why) case-folded, because a decision usually
    repeats the rejection it also wrote onto the parameter (`_dedupe`: a typed
    decision row wins over its untyped copy). Each row is `(item, source,
    key)`, `key` its decision and index or None.
    """
    collected: list[tuple] = [(item, "", None) for item in own or ()]
    for decision in ledger.decisions:
        if names & (set(decision.params_changed) | set(decision.claims_changed)):
            collected += [(item, f"decision {decision.id}", (decision.id, i))
                          for i, item in enumerate(decision.rejected or ())]
    return _dedupe(collected)


def _split_verdict(verdict: Verdict) -> tuple[str, str]:
    """`verdict.render()` split into its `[tag]` head and its body text.

    Taken apart rather than re-derived: the tag spelling (`[ok  ]`, `[skip]`,
    `[FAIL]`, `[ERR ]`, padded to column up) belongs to `Verdict.render`, and a
    second copy of that mapping here would be a second answer to "did this gate
    pass" living two modules from the first.

    A verdict with no body renders as `[tag] gate` with no `" : "`, in which case
    the whole line is the head and the body is empty.

    A verdict that carries the spine's mark (`Verdict.unqualified`) has no tag:
    its head is empty and its body `report.unqualified_text` — `unqualified:
    <what does not hold>` or `outside operating context: <key> = <v>, qualified
    on [a, b]` — so its line reads `<gate> : <body>`, as `check` and `gate show`
    print it (`report.verdict_line`). What slipped through (review of P2.4): `why`
    and `claim show` printed `[ERR ] bracket.bearing : unqualified:
    context:outside|{…}` — a crash's tag and the spine's token on a human
    channel — for a pass outside its operating context.
    """
    token = getattr(verdict, "unqualified", "") or ""
    if token:
        from . import report                       # a reader's module: not at import
        return "", report.unqualified_text(token)
    rendered = verdict.render()
    head, sep, body = rendered.partition(f" {verdict.gate} : ")
    return (head, body) if sep else (rendered, "")


@dataclass(frozen=True)
class _Evidence:
    """What the caller knows that the ledger's stored copies do not.

    Each field left `None` means "use the ledger's own", which is today's
    behaviour and what a caller with no registry and no cache gets:

    * `coverage` — `{claim id: [gate ids]}`, registry coverage
      (`claims.effective_gates`), in place of `Claim.gates`;
    * `read_sets` — `{gate id: {param path, ...}}`, what each gate read when it
      last executed (`verdicts.last_read_sets`), in place of `Param.gates`;
    * `verdicts` — gate id to its effective verdict (`verdicts.resolve`), in
      place of `ledger.verdict`.

    Why `why` takes them rather than a ledger with the copies filled in: from
    1.3 a param the model owns entirely has no record to fill, and a record may
    not carry `gates` at all — the copies are gone, not stale.
    """

    coverage: Mapping[str, Sequence[str]] | None = None
    read_sets: Mapping[str, Iterable[Any]] | None = None
    verdicts: Mapping[str, Verdict] | None = None
    rows: Mapping[tuple, Any] | None = None
    """`{(decision id, index): claims.RejectionRow}` — the caller's (SPEC 14),
    composed on the view it judged; None composes `claims.compositions(ledger)`
    once, on first use (`row`)."""
    bases: Mapping[str, str] | None = None
    """`{input id: basis token}` of the proposer that produced each input
    (`packs.proposer_basis`); None reads `undeclared` for any proposer."""
    graph: Any = None
    """The typed graph (`graph.build`, architecture phase 3a): with it, `why`
    says what reads a value, what sources it, and what a claim rests on,
    resolves or is resolved by; None prints none of those lines."""
    composed: Mapping[str, Any] | None = None
    """The caller's compositions, for the status word beside an assumption."""
    entry: str = ""
    """The model's entry file, named where a derived value comes from."""
    prose: Mapping[str, str] | None = None
    """``{subject: stamp state}`` of ``site/content/`` (architecture phase 5),
    for a board's ``text:`` line."""
    trace: Any = None
    """The current design's trace (`derivation.TraceFile`, architecture phase
    3b): with it, a choice says what it moves and a derived value what it is
    derived from and where the trace orders it; None says neither."""
    amber: Mapping[str, Any] | None = None
    """``{claim id: derivation.Amber}`` — a Checked claim's amber line."""

    def word(self, ledger: Ledger, cid: str) -> str:
        """`cid`'s status word, from the caller's compositions."""
        from . import claims as claim_logic, report    # readers' modules: not at import
        composed = self.composed
        if composed is None:
            composed = claim_logic.compositions(ledger)
            object.__setattr__(self, "composed", composed)
        found = composed.get(cid)
        if found is None:
            return report.HUMAN["round"]["item_missing"].format(id=cid)
        return report.status_word(found.status, errored=found.errored)

    def row(self, ledger: Ledger, key: tuple) -> Any:
        rows = self.rows
        if rows is None:
            from . import claims as claim_logic        # a reader's module: not at import
            rows = {(r.decision, r.index): r for r in claim_logic.rejection_rows(
                ledger, claim_logic.compositions(ledger))}
            object.__setattr__(self, "rows", rows)
        return rows.get(tuple(key))

    def basis(self, artifact: Any) -> str:
        from .models import BASIS_UNDECLARED
        if self.bases is not None and artifact.id in self.bases:
            return self.bases[artifact.id] or BASIS_UNDECLARED
        return BASIS_UNDECLARED

    def verdict(self, ledger: Ledger, gate_id: str) -> Verdict | None:
        if self.verdicts is not None:
            return self.verdicts.get(gate_id)
        return ledger.verdict(gate_id)

    def claim_gates(self, claim: Claim) -> list[str]:
        if self.coverage is not None:
            return list(self.coverage.get(claim.id) or ())
        return list(claim.gates or ())

    def param_gates(self, name: str, stored: Sequence[str]) -> list[str]:
        if self.read_sets is not None:
            return _readers(name, self.read_sets)
        return list(stored or ())


def _readers(name: str, read_sets: Mapping[str, Iterable[Any]]) -> list[str]:
    """The gates whose recorded reads name parameter `name`, sorted by id —
    `modelio.param_readers`, the one copy of the rule. It was spelled here and
    again in `cli._param_gates` until the page and the report moved to the
    parameter view and would have needed a third (review, checkpoint 1.3)."""
    return modelio.param_readers(name, read_sets)


def _gate_lines(ledger: Ledger, gate_ids: Sequence[str],
                evidence: _Evidence | None = None) -> list[str]:
    """Gates that protect this item, each with how it LAST ran, deduped by reason.

    "Protected by pcb.drc" is reassuring and can be a lie — the gate may never
    have run, or may have errored, or may have been skipped for a missing tool.
    A skip is not a pass (see `Verdict.ok`), so the last verdict is printed
    right next to the gate id where it cannot be mistaken for coverage.

    Gates sharing an identical outcome line are grouped onto one entry. A pack
    whose gates all skip for the same reason ("no solid geometry in the
    projection: ...") used to print that same paragraph once per gate: six gates
    wrapped to four lines each turned `why` into a 49-line wall in which the one
    sentence that mattered appeared six times and the two gates with a
    *different* reason were invisible in the middle of it. `why` exists to be
    read instead of the whole decision log; a block nobody finishes reading is
    the same failure as a log nobody opens.

    Grouping never shortens a reason — the reason is wrapped, not truncated, and
    every gate id still appears. The groups are in outcome order — fail,
    errored, skipped, unqualified, unrun, pass (P2.1-D16) — and within one, in
    first-appearance order, so two runs over an unchanged ledger print identical
    text. What slipped through (P2.0 F-3): they were in gate-id order, so
    `[skip]` sat above `[ERR ]` and a reader read the dull line first.
    """
    from . import claims as claim_logic, report    # readers' modules: not at import
    from .claims import ClaimCause
    groups: dict[tuple[str, str], list[str]] = {}
    ranks: dict[tuple[str, str], int] = {}
    for gid in gate_ids:
        verdict = evidence.verdict(ledger, gid) if evidence else ledger.verdict(gid)
        if verdict is None:
            # An unrun gate is shown like any outcome (GLOSSARY §6: an evaluator
            # is *unrun*, never "never run", which is Open's Never-say), and gates
            # that are unrun group together the same way. The word is HUMAN's
            # (review of P2.1: it was a literal here, outside the one table).
            key = ("[ -- ]", report.HUMAN["lead"][ClaimCause.UNRUN])
        else:
            key = _split_verdict(verdict)
        # `claims.OUTCOME_ORDER` — fail, errored, not run behind a crashed
        # prerequisite, behind any other, skipped, unqualified (by the spine's
        # mark, never as a crash), unrun, pass — the one table the
        # report's bullets and `explaining_verdict` read too (review of P2.1:
        # this kept its own copy, and two inline ranks beside it).
        rank = claim_logic.outcome_rank(verdict)
        groups.setdefault(key, []).append(gid)
        ranks.setdefault(key, rank)

    lines: list[str] = []
    for (head, body), ids in sorted(groups.items(), key=lambda item: ranks[item[0]]):
        lead = f"{head} " if head else ""          # an untagged row: the spine's mark
        if len(ids) == 1:
            # One gate, one line: the dense `[skip] gate.id : reason` form, which
            # is strictly better than a two-line form when there is nothing to
            # share the reason with.
            one = f"{lead}{ids[0]}" + (f" : {body}" if body else "")
            lines += _wrap(one, indent="  ", hanging="         ", collapse=False)
            continue
        lines += _wrap(lead + ", ".join(ids), indent="  ",
                       hanging="         ", collapse=False)
        if body:
            # Indented to the same column the single-gate form wraps to, so the
            # reason reads as belonging to the ids above it.
            lines += _wrap(body, indent="         ", collapse=False)
    return lines


def _grounding_lines(ledger: Ledger, artifact_ids: Sequence[str], names: set[str],
                     evidence: "_Evidence | None" = None) -> list[str]:
    """Input artifacts that ground this item, plus the extraction that did it.

    An artifact id alone ("grounded by sk-hull-png") is not grounding; the
    sentence that was read out of it is. A generated layout is auditable two
    months later only because it records "digitised from inputs/sketches/panel.png,
    legends corrected against inputs/references/unit-front.png".
    """
    lines: list[str] = []
    for aid in artifact_ids:
        artifact = ledger.artifact(aid)
        assumed = ledger.claim(aid) if artifact is None else None
        if assumed is not None and assumed.kind == ClaimKind.ASSUMPTION:
            # Architecture phase 3a (SPEC 19): an assumption's id in a value's
            # grounded_by — the value IS that assumption. Never MISSING: the
            # claim exists, and the graph draws it as the value's source.
            lines += _wrap(f"- {aid}  [assumption]  {_one_line(assumed.statement)}",
                           indent="  ", hanging="    ")
            continue
        if artifact is None:
            # Dangling reference: say so rather than skipping it. A silently
            # dropped id reads as "no grounding", which is a different problem
            # with a different fix.
            lines += _wrap(f"- {aid} — MISSING from the ledger's inputs", indent="  ",
                           hanging="    ")
            continue
        where = artifact.path or artifact.url or ""
        head = f"- {artifact.id}  [{artifact.kind}]"
        if artifact.description:
            head += f"  {artifact.description}"
        if getattr(artifact, "proposer", ""):
            # SPEC 38: the basis is looked up from the proposer, never stated
            # by the input.
            from . import report                   # a reader's module: not at import
            basis = evidence.basis(artifact) if evidence is not None else "undeclared"
            head += " — " + report.HUMAN["basis"]["proposed_by"].format(
                id=artifact.proposer, basis=basis)
        lines += _wrap(head, indent="  ", hanging="    ")
        if where:
            lines += _wrap(where, indent="      ")
        for extraction in artifact.extractions or ():
            if names & set(extraction.grounds or ()):
                lines += _wrap(f'"{extraction.what}" ({extraction.confidence})', indent="      ")
    return lines


def _decisions_touching(ledger: Ledger, names: set[str]) -> list[Decision]:
    """The decisions that named this param/claim, in storage order (newest first)."""
    return [d for d in ledger.decisions
            if names & (set(d.params_changed) | set(d.claims_changed))]


def _decision_lines(moved: Sequence[Decision]) -> list[str]:
    """One line per decision that moved this item, newest first.

    One line each on purpose. This section is a map into `docs/decisions.md`,
    not a copy of it: date, id, summary. If the summary is not enough, the id
    is how you find the body.
    """
    lines: list[str] = []
    for decision in moved:
        date = _date_of(decision.when) or "(undated)"
        lines += _wrap(f"- {date}  {decision.id} — {decision.summary or decision.title}",
                       indent="  ", hanging="    ")
    return lines


def _render_value(param: Param) -> str:
    value = param.value
    if isinstance(value, float):
        # repr() on a float is exact but ugly (0.30000000000000004); plain %g
        # rounds to 6 significant digits, which would print 148.123456789 as
        # 148.123 — a provenance tool must not quietly shorten a number. 12
        # digits hides the binary noise and nothing else.
        shown = f"{value:.12g}"
    elif isinstance(value, bool) or value is None:
        shown = str(value)
    elif isinstance(value, (list, tuple, dict)):
        shown = _one_line(str(value))
    else:
        shown = str(value)
    return f"{shown} {param.units}".strip()


def _why_param(ledger: Ledger, param: Param, evidence: _Evidence) -> str:
    """Today's block, from the ledger's record alone: the no-view path."""
    names = {param.name}
    out: list[str] = [f"param  {param.name} = {_render_value(param)}"]

    if param.is_derived:
        out += _wrap("derived from: " + ", ".join(param.derived_from), indent="  ")
        out += _wrap(
            "(a derived value is not edited directly — change what it derives from)",
            indent="  ",
        )
    else:
        out += _wrap("free parameter (not derived)", indent="  ")

    feeds = [p.name for p in ledger.params if param.name in (p.derived_from or ())]
    if feeds:
        out += _wrap("feeds: " + ", ".join(feeds), indent="  ")
    if param.source:
        out += _wrap(f"source: {param.source}", indent="  ")
    moved_in = param.changed_in or changed_in(ledger, param.name)
    if moved_in:
        out += _wrap(f"last moved in: {moved_in}", indent="  ")
    if param.tags:
        out += _wrap("tags: " + ", ".join(param.tags), indent="  ")

    _section(out, "WHY")
    out += _wrap(param.rationale or "(no rationale recorded — this number is unexplained)")

    rejected = _collect_rejected(ledger, own=param.rejected, names=names)
    _section(out, f"REJECTED ({len(rejected)})")
    out += _rejected_lines(rejected, ledger, evidence) if rejected else _wrap(
        "(none recorded — nothing here stops this being re-litigated)"
    )

    protecting = evidence.param_gates(param.name, param.gates)
    _section(out, f"GATES ({len(protecting)})")
    out += _gate_lines(ledger, protecting, evidence) if protecting else _wrap(
        "(none — no gate would notice if this value went wrong)"
    )

    _section(out, f"GROUNDED BY ({len(param.grounded_by)})")
    out += _grounding_lines(ledger, param.grounded_by, names, evidence) \
        if param.grounded_by else _wrap(
        "(nothing — this value is asserted, not evidenced)"
    )

    moved = _decisions_touching(ledger, names)
    _section(out, f"DECISIONS ({len(moved)}, newest first)")
    out += _decision_lines(moved) if moved else _wrap(
        "(none — this value has never been revisited)"
    )

    return "\n".join(out).rstrip() + "\n"


# --------------------------------------------------------------------------- #
# why, from a param view: the model's number, and what lost from both homes
# --------------------------------------------------------------------------- #
#: Where a decision lives once records are files, as a rejection's origin tag.
#: A tag is the file a reader opens, the way a PARAMS row names the model file.
_DECISION_HOME = "decisions/{id}.json"


def _view_value(value: Any, units: str) -> str:
    """A view's value with its units. A float stays visibly a float — `7.0 mm`,
    not `7 mm`: `status` prints `config.bed_xy 220.0 -> 250.0`, and one number
    spelt two ways in two commands reads as two numbers. Otherwise as
    `_render_value`: 12 significant digits, never a quietly shortened number."""
    if isinstance(value, float):
        shown = f"{value:.12g}"
        if math.isfinite(value) and not any(c in shown for c in ".e"):
            shown += ".0"
    elif isinstance(value, bool) or value is None:
        shown = str(value)
    elif isinstance(value, (list, tuple, dict)):
        shown = _one_line(str(value))
    else:
        shown = str(value)
    return f"{shown} {units}".strip()


def _view_head(view: Any) -> str:
    """`param thickness = 7.0 mm   (model/bracket.py Config.thickness)`.

    The value line names where the value LIVES, three spaces from it — the
    shape `tests/_transcript.py` greps (`WHY_PARAM`). A view the model does not
    hold (it did not load, or the record outlived its field) prints no `=` at
    all: no number where the model should answer, and the record's path so the
    reader knows what is left.
    """
    if view.home:
        return f"param {view.name} = {_view_value(view.value, view.units)}   ({view.home})"
    return f"param {view.name}" + (f"   ({view.record})" if view.record else "")


def _tagged_rejections(ledger: Ledger, view: Any) -> list[tuple]:
    """The view's losers (the model's PARAMS, then the record's), then those of
    every decision that moved the param — each tagged with its home, each once,
    as `(item, origin, key)`. Deduped as `_collect_rejected` does (`_dedupe`): a
    decision usually repeats the rejection it also wrote where the param lives,
    and its typed copy wins."""
    rows: list[tuple] = [(item, origin, None) for item, origin in view.rejected or ()]
    for decision in _decisions_touching(ledger, {view.name}):
        origin = _DECISION_HOME.format(id=decision.id)
        rows += [(item, origin, (decision.id, i))
                 for i, item in enumerate(decision.rejected or ())]
    return _dedupe(rows)


def _tagged_lines(rows: Sequence[tuple], ledger: Ledger | None = None,
                  evidence: "_Evidence | None" = None) -> list[str]:
    """`  4.0 mm — 3.75 mm deflection, 7.5x the limit   (model/bracket.py PARAMS)`.

    One loser, one line, never wrapped: the row is a shape (`WHY_REJECTED_ROW`),
    and wrapping it would put the value on one line and the file it lives in on
    another, where no grep for either finds both. Evidence, when there is any,
    goes on the line after, indented past the row; a decision's rejection then
    gets its facts line (SPEC 14), and a PARAMS row prints exactly as before.
    """
    lines: list[str] = []
    for row in rows:
        item, origin = row[0], row[1]
        key = row[2] if len(row) > 2 else None
        lines.append(f"  {_one_line(item.value)} — {_one_line(item.why)}   ({origin})")
        if item.evidence:
            lines.append(f"      evidence: {_one_line(item.evidence)}")
        if ledger is not None:
            lines += _facts_line(ledger, key, evidence)
    return lines


def _why_view(ledger: Ledger, view: Any, views: Sequence[Any], evidence: _Evidence) -> str:
    """One parameter from its view: the value from the model, never the record.

    What slipped through before (S-39): `why` rendered `ledger.params`, whose
    `value` was a copy the last `check` wrote — "thickness = 7" after the model
    said 8.0, and whatever a stale ledger said on a machine whose model did not
    load. Here the value line is the model's or it is absent.
    """
    names = {view.name}
    record = ledger.param(view.name)
    out: list[str] = [_view_head(view)]

    if view.model_error:
        # One line, unwrapped, like `status`'s "DOES NOT LOAD" line: the error is
        # a tool's fact, and a wrap would split the one phrase a reader greps for.
        out.append(f"  model does not load: {_one_line(view.model_error)}")
    elif not view.home:
        out += _wrap(
            f"the model does not define {view.name}: this record outlived its field — "
            f"retire it with a decision, or restore the field", indent="  ", hanging="    ")
    elif view.derived_from:
        out += _wrap("derived from: " + ", ".join(view.derived_from), indent="  ")
        out += _wrap(
            "(a derived value is not edited directly — change what it derives from)",
            indent="  ",
        )
    else:
        out += _wrap("free parameter (not derived)", indent="  ")

    feeds = [v.name for v in views if view.name in (v.derived_from or ())]
    if feeds:
        out += _wrap("feeds: " + ", ".join(feeds), indent="  ")
    if evidence.graph is not None:
        out += _value_graph_lines(ledger, view.read_by, view.sourced_from, evidence)
        out += _choice_trace_lines(view.name, evidence)
    if view.source:
        out += _wrap(f"source: {view.source}", indent="  ")
    moved_in = changed_in(ledger, view.name)
    if moved_in:
        out += _wrap(f"last moved in: {moved_in}", indent="  ")
    if view.tags:
        out += _wrap("tags: " + ", ".join(view.tags), indent="  ")

    _section(out, "WHY")
    out += _wrap(view.rationale or "(no rationale recorded — this number is unexplained)")

    rejected = _tagged_rejections(ledger, view)
    _section(out, f"REJECTED ({len(rejected)})")
    out += _tagged_lines(rejected, ledger, evidence) if rejected else _wrap(
        "(none recorded — nothing here stops this being re-litigated)"
    )

    protecting = evidence.param_gates(view.name, record.gates if record else ())
    _section(out, f"GATES ({len(protecting)})")
    out += _gate_lines(ledger, protecting, evidence) if protecting else _wrap(
        "(none — no gate would notice if this value went wrong)"
    )

    grounded = list(view.grounded_by or ())
    _section(out, f"GROUNDED BY ({len(grounded)})")
    out += _grounding_lines(ledger, grounded, names, evidence) if grounded else _wrap(
        "(nothing — this value is asserted, not evidenced)"
    )

    moved = _decisions_touching(ledger, names)
    _section(out, f"DECISIONS ({len(moved)}, newest first)")
    out += _decision_lines(moved) if moved else _wrap(
        "(none — this value has never been revisited)"
    )

    return "\n".join(out).rstrip() + "\n"


def _value_graph_lines(ledger: Ledger, read_by: Sequence[str], sourced_from: Sequence[str],
                       evidence: _Evidence) -> list[str]:
    """`read by:` and `sourced from:` (architecture phase 3a, SPEC 10, 19): the
    evaluators whose verdict on record read the value, and the inputs and
    assumptions that source it, each assumption with its status word."""
    from . import report                             # a reader's module: not at import
    said = report.HUMAN["round"]
    lines = _wrap(said["read_by"].format(ids=", ".join(read_by)) if read_by
                  else said["read_by_none"], indent="  ", hanging="    ")
    items = []
    for nid in sourced_from:
        kind, _, key = nid.partition(":")
        if kind == "claim":
            items.append(said["source_assumption"].format(id=key,
                                                          word=evidence.word(ledger, key)))
        else:
            items.append(said["source_input"].format(id=key))
    lines += _wrap(said["sourced_from"].format(items=", ".join(items)) if items
                   else said["sourced_from_none"], indent="  ", hanging="    ")
    return lines


def _round_of(trace: Any) -> str:
    """``traced at round N``, or ``traced`` when no round record named one."""
    from . import report                             # a reader's module: not at import
    return traced_words(getattr(trace, "round", None))


def traced_words(number: Any) -> str:
    from . import report                             # a reader's module: not at import
    said = report.HUMAN["round"]
    return said["traced_at"].format(r=number) if number is not None else said["traced"]


def _move_words(move: Any) -> str:
    from . import report                             # a reader's module: not at import
    if isinstance(move, (int, float)) and not isinstance(move, bool):
        pct = move * 100.0
        text = f"{pct:+.0f}%" if abs(pct) >= 1 else f"{pct:+.2g}%"
        return text.replace("-", "\u2212")
    return report.HUMAN["round"]["changed"]


def _pair_words(pair: Any) -> str:
    found = [_move_words(m) for m in (pair or ()) if m is not None]
    return ", ".join(found)


def _choice_trace_lines(name: str, evidence: _Evidence) -> list[str]:
    """A choice's ``moves:`` line (architecture phase 3b): the derived values
    the trace saw it move, the largest first, each with its two moves; or why
    the trace did not move it. Nothing without a trace."""
    from . import derivation, report                 # a reader's module: not at import
    trace = evidence.trace
    if trace is None:
        return []
    said = report.HUMAN["round"]
    if name in trace.skipped:
        word = report.HUMAN["trace"]["skip"].get(trace.skipped[name], trace.skipped[name])
        return _wrap(said["not_moved"].format(word=word), indent="  ", hanging="    ")
    if name not in trace.observed:
        return []
    # A raised build observed nothing: said as raised, never as "nothing
    # moved" (review of phase 3b: both builds raised, and `why` said "moves:
    # nothing at ±10%").
    row = trace.observed.get(name) or {}
    raised = list(row.get("raised") or [None, None]) + [None, None]
    change = row.get("change")
    out: list[str] = []
    if raised[0] and raised[1]:
        kinds = raised[0] if raised[0] == raised[1] else f"{raised[0]}, {raised[1]}"
        return _wrap(said["moves_raised"].format(type=kinds), indent="  ", hanging="    ")
    for direction in (0, 1):
        if raised[direction]:
            out += _wrap(said["moves_raised_one"].format(
                step=derivation.step_word(direction, change, minus="\u2212"),
                type=raised[direction]), indent="  ", hanging="    ")
    moves = {k: v for k, v in trace.moves(name).items() if k not in set(trace.unsteady)}
    if not moves:
        return _wrap(said["moves_none"], indent="  ", hanging="    ") + out
    ranked = sorted(moves, key=lambda k: (-max(derivation.magnitude(m) for m in moves[k]
                                                if m is not None), k))
    items = [f"{k} ({_pair_words(moves[k])})" for k in ranked[:8]]
    text = ", ".join(items) + (f", and {len(ranked) - 8} more" if len(ranked) > 8 else "")
    if isinstance(change, list):
        text += said["moves_size"].format(lo=derivation.step_word(0, change, minus="\u2212"),
                                          hi=derivation.step_word(1, change))
    return _wrap(said["moves"].format(items=text, traced=_round_of(trace)), indent="  ",
                 hanging="    ") + out


def _derived_trace_lines(name: str, evidence: _Evidence) -> list[str]:
    """A derived value's trace lines (architecture phase 3b): what moved it,
    where the trace orders it (inferred), the values it ties with, or that no
    move moved it — never that it is independent."""
    from . import report                             # a reader's module: not at import
    trace = evidence.trace
    said = report.HUMAN["round"]
    if trace is None:
        return _wrap(said["derived_from_untraced"], indent="  ")
    out: list[str] = []
    movers = sorted(trace.movers(name), key=_natural_key)
    if not movers:
        if name in trace.unmoved:
            return _wrap(said["unmoved"], indent="  ", hanging="    ")
        return []
    items = [f"{c} ({_pair_words((trace.observed.get(c) or {}).get('moves', {}).get(name))})"
             for c in movers]
    out += _wrap(said["derived_from"].format(items=", ".join(items), traced=_round_of(trace)),
                 indent="  ", hanging="    ")
    before = sorted({a for a, b in trace.after if b == name}, key=_natural_key)
    after = sorted({b for a, b in trace.after if a == name}, key=_natural_key)
    if before or after:
        out += _wrap(said["after_of"].format(before=", ".join(before) or "none",
                                             after=", ".join(after) or "none"),
                     indent="  ", hanging="    ")
    tie = next((t for t in trace.ties if name in t), None)
    if tie:
        out += _wrap(said["ties"].format(ids=", ".join(x for x in tie if x != name)),
                     indent="  ", hanging="    ")
    if name in trace.singles:
        out += _wrap(said["single"], indent="  ", hanging="    ")
    return out


def _natural_key(text: str) -> Any:
    from .store import _natural
    return _natural(text)


def _amber_lines(claim: Claim, evidence: _Evidence) -> list[str]:
    """A Checked claim's ``amber:`` line (architecture phase 3b): flips,
    holds, could not tell, not current or not traced — shown beside Checked,
    never instead of it (invariant 12)."""
    found = (evidence.amber or {}).get(claim.id)
    if found is None:
        return []
    return _wrap(amber_text(found), indent="  ", hanging="    ")


def amber_text(found: Any) -> str:
    """One claim's amber (`derivation.Amber`) in `why`'s words — the report's
    ``## Amber`` lines say the same."""
    from . import report                             # a reader's module: not at import
    said = report.HUMAN["round"]
    if found.state == "flips" and found.flips:
        aid, choice, direction, gate, measured, limit, units = found.flips[0]
        text = said["amber_flips"].format(
            choice=choice, step=found.step(choice, direction, minus="\u2212"), aid=aid,
            evaluator=gate or "its evaluator", measured=_view_value(measured, ""),
            limit=_view_value(limit, ""), units=units or "")
    elif found.state == "holds":
        text = said["amber_holds"].format(
            choices=", ".join(dict.fromkeys(c for _a, c in found.via)),
            aids=", ".join(dict.fromkeys(a for a, _c in found.via)))
    elif found.state == "unclear" and found.unclear:
        token, choice, direction = found.unclear[0]
        text = said["amber_unclear"].format(token=token, choice=choice,
                                            step=found.step(choice, direction, minus="\u2212"))
    elif found.state == "not-current":
        text = said["amber_not_current"].format(why=found.why)
    else:
        text = said["amber_untraced"].format(why=found.why)
    return " ".join(text.split())


def _claim_graph_lines(ledger: Ledger, claim: Claim, evidence: _Evidence) -> list[str]:
    """`rests on:`, `resolves:` and `resolved by:` (architecture phase 3a, SPEC
    19), each only when there is one — from the graph, never recomputed here —
    and, while derivation is not traced, the caveat that only direct reads
    were followed."""
    from . import report                             # a reader's module: not at import
    built = evidence.graph
    if built is None:
        return []
    said = report.HUMAN["round"]
    held = {c.id for c in ledger.claims}
    lines: list[str] = []
    rests = list(built.rests_on(claim.id))
    if rests:
        items = []
        for aid in rests:
            via = ", ".join(dict.fromkeys(
                said["rests_on_via"].format(evaluator=ev, value=up)
                for ev, _read, up in built.via(claim.id, aid)))
            items.append(said["rests_on_item"].format(id=aid, word=evidence.word(ledger, aid),
                                                      via=via))
        lines += _wrap(said["rests_on"].format(items="; ".join(items)), indent="  ",
                       hanging="    ")
    # Untraced, every claim an evaluator settles says that only direct reads
    # were followed — with or without a rests-on line above. What slipped
    # through (review of phase 3a): `why C2` printed nothing here, though its
    # evaluator read a stress derived from the load an assumption sources, so
    # a Checked claim read as standing on nothing unconfirmed.
    if built.trace is None and built.into(f"claim:{claim.id}", "settles"):
        lines += _wrap(said["rests_on_untraced"], indent="  ", hanging="    ")
    lines += _amber_lines(claim, evidence)
    resolves = list(getattr(claim, "resolves", None) or ())
    if resolves:
        items = [said["item"].format(id=aid, word=evidence.word(ledger, aid)) if aid in held
                 else said["item_missing"].format(id=aid) for aid in resolves]
        lines += _wrap(said["resolves"].format(items=", ".join(items)), indent="  ",
                       hanging="    ")
    by = [nid.partition(":")[2] for nid in built.into(f"claim:{claim.id}", "resolves")]
    if by:
        items = [said["item"].format(id=cid, word=evidence.word(ledger, cid)) for cid in by]
        lines += _wrap(said["resolved_by"].format(items=", ".join(items)), indent="  ",
                       hanging="    ")
    return lines


def _why_value_node(ledger: Ledger, node: Any, evidence: _Evidence) -> str:
    """A value the graph holds that no parameter view does — a derived value an
    evaluator read (architecture phase 3a): its value, where it is derived, what
    reads it and what sources it. Its derivation is the trace's (phase 3b)."""
    from . import report                             # a reader's module: not at import
    said = report.HUMAN["round"]
    name = node.id.partition(":")[2]
    attrs = node.attrs
    shown = f" = {_view_value(attrs['value'], '')}" if "value" in attrs else ""
    out = [said["value_head"].format(name=name, value=shown,
                                     entry=evidence.entry or "the model")]
    built = evidence.graph
    out += _value_graph_lines(ledger, built.read_by(name), built.sourced_from(name), evidence)
    if built.trace is None:
        out += _wrap(said["derived_from_untraced"], indent="  ")
    else:
        out += _derived_trace_lines(name, evidence)
    return "\n".join(out).rstrip() + "\n"


def _board_line(ledger: Ledger, claim: Claim) -> list[str]:
    """`board: strength (Holds the pump)` — the board a claim sits on, from the
    records (architecture phase 5, SPEC 17), or the page's own board when no
    record holds it; said only where the project has board records."""
    boards = [g for g in getattr(ledger, "groups", None) or () if g.kind == "board"]
    if not boards:
        return []
    from . import store                              # the one order of the groups
    for board in store.groups_in_order(boards, "board"):
        if claim.id in board.members:
            return _wrap(f"board: {board.id} ({board.title})", indent="  ")
    from . import report                             # a reader's module: not at import
    return _wrap(f"board: other ({report.HUMAN['site']['other_board']})", indent="  ")


_PROSE_WORDS = {"current": "current", "stale": "written before the latest results",
                "unstamped": "not stamped"}


def _why_group(ledger: Ledger, group: Any, views: list[Any] | None,
               evidence: _Evidence) -> str:
    """A board, a zone or a station (architecture phase 5, SPEC 17): its title
    and line, then a board's claims with their status words, a zone's choices
    with their values and what sources each, a station's values with what reads
    each — and, for a board, the stamp state of its text."""
    out = [f"{group.kind} {group.id}: {group.title}   (groups/{group.id}.json)"]
    if group.line:
        out += _wrap(group.line, indent="  ")
    by_view = {v.name: v for v in views or ()}
    built = evidence.graph
    if group.kind == "board":
        out += _wrap("claims: " + (" · ".join(
            f"{cid} {evidence.word(ledger, cid)}" for cid in group.members) or "(none)"),
            indent="")
        state = (evidence.prose or {}).get(group.id)
        shown = _PROSE_WORDS.get(state, "") if state else ""
        out += _wrap(f"text: site/content/{group.id}.md ({shown})" if shown else
                     "text: (none)", indent="")
        return "\n".join(out).rstrip() + "\n"
    head = "choices:" if group.kind == "zone" else "values:"
    out.append(head)
    for name in group.members:
        found = by_view.get(name)
        node = built.node(f"value:{name}") if built is not None else None
        value = (f" = {_view_value(found.value, getattr(found, 'units', '') or '')}"
                 if found is not None and found.value is not None else
                 f" = {_view_value(node.attrs['value'], '')}"
                 if node is not None and "value" in node.attrs else "")
        line = f"{name}{value}"
        if built is not None:
            if group.kind == "zone":
                sources = [s.partition(":")[2] for s in built.sourced_from(name)]
                line += f"   sourced by: {', '.join(sources) if sources else 'none'}"
            else:
                readers = list(built.read_by(name))
                line += f"   read by: {', '.join(readers) if readers else 'none'}"
        out += _wrap(line, indent="  ")
    return "\n".join(out).rstrip() + "\n"


def _why_claim(ledger: Ledger, claim: Claim, evidence: _Evidence) -> str:
    from . import report                             # a reader's module: not at import
    names = {claim.id}
    # Where the claim's evidence bottoms out, in its display word, and
    # *required* (P2.5a-D20; GLOSSARY §8: the kind's code word was a raw enum
    # leak, and "critical" is *required*'s Never-say).
    flags = [report.terminal_word(claim), "required" if claim.critical else "not required"]
    out: list[str] = [f"claim  {claim.id}  [{' · '.join(flags)}]"]
    out += _wrap(claim.statement)

    # With none written, by what settles the claim instead (review of P2.5a: a
    # physical claim's note — its written test, P2.5a-D9 — and an expert
    # judgment, which takes none, were both told "without a threshold this is a
    # wish", in a Never-say of the acceptance condition).
    acceptance = claim.acceptance.render() if claim.acceptance else ""
    said = report.HUMAN["acceptance"]
    if acceptance:
        line = f"acceptance: {acceptance}"
    elif report._judgment(claim):
        line = said["why_judgment"].format(
            authority=report._one(getattr(claim, "authority", "")) or "its authority")
    elif claim.kind == ClaimKind.PHYSICAL and str(claim.note or "").strip():
        line = said["why_note"]
    else:
        line = said["why_none"]
    out += _wrap(line, indent="  ")
    if claim.source:
        out += _wrap(f"source: {claim.source}", indent="  ")
    if claim.tags:
        out += _wrap("tags: " + ", ".join(claim.tags), indent="  ")
    # SPEC decision 12: the requirements this claim serves, each one a file
    # names — or said missing, never dropped.
    serves = list(getattr(claim, "requirements", None) or ())
    if serves:
        said_req = report.HUMAN["requirement"]
        held = {r.id for r in getattr(ledger, "requirements", None) or ()}
        out += _wrap(said_req["serves"].format(ids=", ".join(
            rid if rid in held else said_req["serves_missing"].format(id=rid)
            for rid in serves)), indent="  ")
    out += _board_line(ledger, claim)
    out += _claim_graph_lines(ledger, claim, evidence)
    if str(getattr(claim, "authority", "") or "").strip():
        out += _wrap(f"authority: {claim.authority}", indent="  ")
    # P2.5b-D19: a measurement's latency, declared until an article an export
    # built measures it — only where there is one to say.
    from . import claims as claim_logic              # a reader's module: not at import
    exports = tuple(getattr(ledger, "exports", None) or ())
    if claim_logic.latency(claim, exports).source != "none":
        out += _wrap(report.HUMAN["latency"]["row"].format(
            words=report.latency_words(claim, exports)), indent="  ")
    for record in getattr(claim, "attributions", ()) or ():
        out += _wrap(f"{record.role} recorded: {record.name} ({record.when}, "
                     f"{report.recorded_words(record)})", indent="  ", hanging="    ")

    _section(out, "WHY")
    out += _wrap(claim.rationale or "(no rationale recorded — what breaks if this is false?)")

    # SPEC decision 13: what would settle it, where its evidence bottoms out,
    # and the evaluators it expects — the `how` moved here from WHY. No
    # registry warning here: those are `check`'s and `doctor`'s.
    _section(out, "MECHANISM")
    out += _wrap(report.mechanism_body(claim))
    if getattr(claim, "evaluators", None):
        out += _wrap(report.HUMAN["mechanism"]["names"].format(
            evaluators=", ".join(claim.evaluators)))

    # A claim has no `rejected` field of its own; everything that lost while
    # settling it lives in the decisions that touched it.
    rejected = _collect_rejected(ledger, own=(), names=names)
    _section(out, f"REJECTED ({len(rejected)})")
    out += _rejected_lines(rejected, ledger, evidence) if rejected else _wrap("(none recorded)")

    covering = evidence.claim_gates(claim)
    _section(out, f"GATES ({len(covering)})")
    out += _gate_lines(ledger, covering, evidence) if covering else _wrap(
        f"(none — {_no_evaluator(ledger, claim, evidence)})"
    )

    results = list(getattr(claim, "results", ()) or ())
    if not results and claim.physical_result is not None:
        results = [claim.physical_result]
    if results:
        # Every recorded physical result, oldest first, each with whether it
        # counts now and why not (P2.5a-D20) — so a reader who recorded one
        # learns from the claim itself why it settles nothing.
        # A judgment heads as one (review of P2.5a: GLOSSARY §1's physical
        # result is a physical evaluator's verdict on an article).
        head = "judgments_head" if report._judgment(claim) else "results_head"
        _section(out, report.HUMAN["physical"][head].format(n=len(results)))
        for entry in results:
            out += _wrap(_result_line(claim, entry), indent="  ", hanging="    ")

    _section(out, f"GROUNDED BY ({len(claim.grounded_by)})")
    out += _grounding_lines(ledger, claim.grounded_by, names, evidence) \
        if claim.grounded_by else _wrap(
        "(nothing — this claim came from a conversation, not from evidence)"
    )

    moved = _decisions_touching(ledger, names)
    _section(out, f"DECISIONS ({len(moved)}, newest first)")
    out += _decision_lines(moved) if moved else _wrap(
        "(none — this claim has never been revisited)"
    )

    return "\n".join(out).rstrip() + "\n"


def _result_line(claim: Claim, entry: Any) -> str:
    """One physical result in `why`: its outcome, article, who recorded it and
    from where, when, what was observed and measured, whether it counts now
    (`report.result_facts`), and the evaluators a fail contradicts — every word
    `report.HUMAN`'s (D-16)."""
    from . import report                             # a reader's module: not at import
    said = report.HUMAN["physical"]
    facts = report.result_facts(claim, entry)
    outcome = report.HUMAN["outcome"]["pass" if entry.passed is True else "fail"]
    # A judgment names no article: a person judged it, nothing was printed.
    article = "" if report._judgment(claim) else report.article12(
        getattr(entry, "article", None) or {})
    text = (f"{outcome}" + (f" on article {article}" if article else "")
            + f" ({report.recorded_words(entry)}, {entry.when or 'date not recorded'})")
    if getattr(entry, "measured", None) is not None:
        text += f": {report._num(entry.measured)} {entry.units or ''}".rstrip()
    if entry.detail:
        text += f" — {entry.detail}"
    text += " — " + (said["counts"] if facts["counts"]
                     else said["does_not_count"].format(why=facts["why"]))
    for item in getattr(entry, "contradicts", None) or ():
        if isinstance(item, dict) and item.get("inside") is True:
            unit = f" {item.get('units')}" if item.get("units") else ""
            text += " — " + said["contradiction_of"].format(
                gate=item.get("gate"), code=report.article12(item.get("code")),
                value=f"{report._num(item.get('value'))}{unit}")
    return text


def _no_evaluator(ledger: Ledger, claim: Claim, evidence: _Evidence) -> str:
    """Why nothing evaluates `claim`, by its kind and in its status's word —
    `report`'s, through `claims.compose`, never a status spelled here. What
    slipped through (P2.1): this said "this claim is UNCLAIMED: no gate can
    settle it" for every claim with no evaluator, an assumption included, so
    `why C6` told its owner to go find a gate."""
    from . import claims as claim_logic, report     # readers' modules: not at import
    from .models import ClaimKind
    verdicts = list((evidence.verdicts or {}).values()) if evidence.verdicts is not None \
        else list(ledger.verdicts)
    found = claim_logic.compose(claim, verdicts)
    status = report.word(found.status, errored=found.errored)
    if getattr(claim, "terminal", "") == "human":
        return (f"settled by expert judgment: {status}, "
                f"{report.reason(found, ledger, claim)}")
    if claim.kind == ClaimKind.ASSUMPTION:
        return (f"an assumption is carried by its owner, not settled by an evaluator: "
                f"{status}, {report.reason(found, ledger, claim)}")
    if claim.kind == ClaimKind.PHYSICAL:
        return f"settled on an article: {status}"
    return f"no evaluator covers this claim: {status}"


def _why_requirement(ledger: Ledger, requirement: Any,
                     composed: Mapping[str, Any],
                     coverage: Mapping[str, list[str]] | None = None) -> str:
    """One requirement (SPEC decision 12): its words, where they came from, its
    status from `claims.requirement_statuses` (the one producer, through
    `report.requirement_statuses`), each claim that serves it in severity order
    with its reason, and the decisions that name it. A serving claim the
    resolver calls Checked while its evidence disagrees (D18) is said under the
    head and on its row, never a bare Checked row. ``coverage`` is the caller's
    (`claims.effective_gates`); None reads each claim's cached list."""
    from . import report                            # a reader's module: not at import
    said = report.HUMAN["requirement"]
    cover = dict(coverage) if coverage is not None else report._coverage(ledger, None)
    found = report.requirement_statuses(ledger, composed, cover)[requirement.id]
    out: list[str] = [said["why_head"].format(
        id=requirement.id, word=report.status_word(found.status, errored=found.errored))]
    if found.disagree:
        out += _wrap(said["why_disagree"].format(ids=", ".join(found.disagree)),
                     indent="  ")
    out += _wrap(requirement.statement)
    if str(requirement.source or "").strip():
        out += _wrap(said["why_source"].format(source=requirement.source), indent="  ")

    serving = [c for c in ledger.claims if c.id in found.claims]
    _section(out, said["why_claims"].format(n=len(serving)))
    if not serving:
        out += _wrap(said["why_none"].format(id=requirement.id,
                                             gap=report.word(found.status)))
    for claim in report.in_severity(ledger, composed, serving):
        one = composed[claim.id]
        row = (f"  {report.status_tag(one.status, errored=one.errored)} {claim.id} "
               f"{report._trunc(report._one(claim.statement), 52)}")
        disagreeing = report._disagreement(ledger, claim, one, cover)
        if disagreeing:
            row += f" — status and evidence disagree — {disagreeing}"
        elif one.status not in (ClaimStatus.PASS, ClaimStatus.VERIFIED):
            row += " — " + report.reason(one, ledger, claim)
        out.append(row)

    moved = _decisions_touching(ledger, {requirement.id})
    _section(out, f"DECISIONS ({len(moved)}, newest first)")
    out += _decision_lines(moved) if moved else _wrap(
        "(none — this requirement has never been revisited)"
    )
    return "\n".join(out).rstrip() + "\n"


def _why_decision(ledger: Ledger, decision: Decision, evidence: _Evidence) -> str:
    """One decision (SPEC 14): its title, summary and what it moved, then every
    alternative it rejected with the concrete reason, and under each its facts
    line — its type (a preference said recorded or not by whom), its return
    condition's state now (`claims.return_state`, through the caller's rows)
    and its input. What slipped through before it: `why <decision>` was refused
    ("is a decision id"), so the one record that names what lost could not be
    read in the one command that exists to show what lost."""
    out: list[str] = [f"decision  {decision.id}" + (f"  ({decision.when})"
                                                     if decision.when else "")]
    if decision.title:
        out += _wrap(decision.title)
    if decision.summary:
        out += _wrap(decision.summary)
    moved = []
    if decision.params_changed:
        moved.append("params: " + ", ".join(decision.params_changed))
    if decision.claims_changed:
        moved.append("claims: " + ", ".join(decision.claims_changed))
    if moved:
        out += _wrap(" · ".join(moved))
    if decision.evidence:
        out += _wrap("evidence: " + ", ".join(decision.evidence))

    items = list(decision.rejected or ())
    _section(out, f"REJECTED ({len(items)})")
    if not items:
        out += _wrap("(none recorded — nothing here stops this being re-litigated)")
    for i, item in enumerate(items):
        out.append(f"  {_one_line(item.value)} — {_one_line(item.why)}")
        if item.evidence:
            out.append(f"      evidence: {_one_line(item.evidence)}")
        out += _facts_line(ledger, (decision.id, i), evidence)
    if decision.body:
        _section(out, "BODY")
        out += [f"  {line}" if line.strip() else "" for line in decision.body.splitlines()]
    return "\n".join(out).rstrip() + "\n"


def _why_decision_only(ledger: Ledger, name: str, close: Sequence[str]) -> str:
    """A name that only the decision log knows about.

    Rendered rather than refused, because the two ways it happens both deserve
    to be seen: a parameter that was *deleted* (its decisions are its epitaph
    and are the only record it ever existed), and a typo in `--params` on some
    earlier `decide` (which would otherwise stay invisible forever — the
    decision would just never show up under the real parameter's `why`).
    """
    out: list[str] = [
        f"{name}  — NO param or claim record",
        "",
    ]
    out += _wrap(
        "This name appears only in the decision log. Either it was removed from "
        "the model, or it was mistyped when the decision was recorded — in which "
        "case the decisions below are not reaching the real parameter.",
    )
    if close:
        out += _wrap("close names that DO exist: " + ", ".join(close))
    moved = _decisions_touching(ledger, {name})
    _section(out, f"DECISIONS ({len(moved)}, newest first)")
    out += _decision_lines(moved)
    return "\n".join(out).rstrip() + "\n"


def _as_views(view: Any) -> list[Any] | None:
    """`view` as a list: None stays None; one view is a list of one."""
    if view is None:
        return None
    if isinstance(view, (list, tuple)):
        return list(view)
    if hasattr(view, "name") and hasattr(view, "home"):
        return [view]
    return list(view)


def _first_by_gate(verdicts: Iterable[Verdict] | None) -> dict[str, Verdict] | None:
    """`{gate: verdict}`, the first per gate — what `Ledger.verdict` returns."""
    if verdicts is None:
        return None
    out: dict[str, Verdict] = {}
    for verdict in verdicts:
        out.setdefault(verdict.gate, verdict)
    return out


def why(ledger: Ledger, name: str, *, view: Any = None,
        coverage: Mapping[str, Sequence[str]] | None = None,
        read_sets: Mapping[str, Iterable[Any]] | None = None,
        verdicts: Iterable[Verdict] | None = None,
        composed: Mapping[str, Any] | None = None,
        rows: Iterable[Any] | None = None,
        bases: Mapping[str, str] | None = None,
        graph: Any = None, entry: str = "",
        prose: Mapping[str, str] | None = None, trace: Any = None,
        amber: Mapping[str, Any] | None = None) -> str:
    """Everything known about one parameter, claim, requirement or decision, as
    one dense plain-text block.

    This is the context-window win and the reason this module exists. An agent
    about to change `wall_mm` calls `why("wall_mm")` and gets the value, what it
    derives from, the rationale, **every** alternative that was rejected and the
    concrete reason each lost, which gates protect it and how they last ran,
    which input artifacts ground it, and the decisions that moved it — instead
    of reading a log of thousands of lines and a ledger to reconstruct the same
    thing.

    `view` is `modelio.param_view`'s output (the list, or one `ParamView`). With
    it, the parameters ARE the views — `param_view` has already folded every
    record in — and the block opens with the model's value and where it lives:

        param thickness = 7.0 mm   (model/bracket.py Config.thickness)
        ...
        REJECTED (1)
          4.0 mm — 3.75 mm deflection, 7.5x the limit   (model/bracket.py PARAMS)

    each loser on one line, tagged with the file it lives in; a model that does
    not load prints `model does not load: …` and no number. Without it, the
    block is today's, rendered from `ledger.params` alone (the stored value).
    `coverage` (`{claim: [gates]}`), `read_sets` (`{gate: {param path}}`, a
    parameter counting as read by its first two keys) and `verdicts` (the
    effective ones) replace, one by one, `Claim.gates`, `Param.gates` and
    `ledger.verdict`; each left `None` keeps the stored copy. The CLI passes all
    four from 1.3 (U29); until then `cmd_why` passes none and gets today's text.

    Resolution order: exact param name, exact claim id, exact requirement id
    (SPEC decision 12), exact decision id (SPEC 14), then the same four
    case-insensitively, then a name the decision log mentions but no record
    holds — with `graph` (architecture phase 3a), a value the graph holds that
    no parameter view does is found by its exact name after the four, and each
    parameter and claim gains its graph lines. `rows` are the caller's
    `claims.rejection_rows` (on the view it
    judged) and `bases` each input's proposer basis; with none, the rows are
    composed from `claims.compositions(ledger)`. A param and a claim sharing a
    name is pathological; the param wins and the claim is still findable by
    exact id. `composed` is the caller's compositions (`cmd_why` passes the
    view it judged); a requirement's status is read from it through
    `claims.requirement_statuses`, and with none given from
    `claims.compositions(ledger)` — the raw opinion, for callers outside the CLI.

    Plain text, no ANSI: this output goes into a context window and into pipes
    at least as often as it goes to a terminal, and escape codes there are noise
    that costs tokens.

    Raises `NopekitError` listing close matches when nothing matches — a
    mistyped parameter name is a user error, and the fix is almost always three
    characters away.
    """
    wanted = (name or "").strip()
    if not wanted:
        raise NopekitError("why needs a parameter name, a claim id, a requirement id or a "
                           "decision id")

    def requirement_text(requirement: Any) -> str:
        from . import claims as claim_logic          # a reader's module: not at import
        found = composed if composed is not None else claim_logic.compositions(ledger)
        return _why_requirement(ledger, requirement, found, coverage)

    if rows is None and composed is not None:
        from . import claims as claim_logic          # a reader's module: not at import
        rows = claim_logic.rejection_rows(ledger, composed)
    evidence = _Evidence(coverage=coverage, read_sets=read_sets,
                         verdicts=_first_by_gate(verdicts),
                         rows=None if rows is None else {(r.decision, r.index): r
                                                         for r in rows},
                         bases=bases, graph=graph, composed=composed, entry=entry,
                         prose=prose, trace=trace, amber=amber)
    views = _as_views(view)

    def param_named(match) -> str | None:
        if views is not None:
            found = next((v for v in views if match(v.name)), None)
            return _why_view(ledger, found, views, evidence) if found is not None else None
        found = next((p for p in ledger.params if match(p.name)), None)
        return _why_param(ledger, found, evidence) if found is not None else None

    text = param_named(lambda candidate: candidate == wanted)
    if text is not None:
        return text
    claim = ledger.claim(wanted)
    if claim is not None:
        return _why_claim(ledger, claim, evidence)
    requirements = list(getattr(ledger, "requirements", None) or ())
    requirement = next((r for r in requirements if r.id == wanted), None)
    if requirement is not None:
        return requirement_text(requirement)
    decision = next((d for d in ledger.decisions if d.id == wanted), None)
    if decision is not None:
        return _why_decision(ledger, decision, evidence)
    # Architecture phase 5 (SPEC 17): a board, a zone or a station, after the
    # decisions — a group id is lower case, so it never equals a claim's.
    groups = list(getattr(ledger, "groups", None) or ())
    group = next((g for g in groups if g.id == wanted), None)
    if group is not None:
        return _why_group(ledger, group, views, evidence)
    # Architecture phase 3a: a value the graph holds that no parameter view does
    # — a derived value an evaluator read — by its exact name only.
    node = graph.node(f"value:{wanted}") if graph is not None else None
    if node is not None:
        return _why_value_node(ledger, node, evidence)

    folded = wanted.casefold()
    text = param_named(lambda candidate: candidate.casefold() == folded)
    if text is not None:
        return text
    for candidate in ledger.claims:
        if candidate.id.casefold() == folded:
            return _why_claim(ledger, candidate, evidence)
    for requirement in requirements:
        if requirement.id.casefold() == folded:
            return requirement_text(requirement)
    for decision in ledger.decisions:
        if decision.id.casefold() == folded:
            return _why_decision(ledger, decision, evidence)
    for group in groups:
        if group.id.casefold() == folded:
            return _why_group(ledger, group, views, evidence)

    param_names = ([v.name for v in views] if views is not None
                   else [p.name for p in ledger.params])
    known = (param_names + [c.id for c in ledger.claims] + [r.id for r in requirements]
             + [d.id for d in ledger.decisions] + [g.id for g in groups])
    close = difflib.get_close_matches(wanted, known, n=_SUGGESTIONS, cutoff=0.6)

    in_decisions = any(
        wanted in (d.params_changed or ()) or wanted in (d.claims_changed or ())
        for d in ledger.decisions
    )
    if in_decisions:
        return _why_decision_only(ledger, wanted, close)

    if not known:
        raise NopekitError(
            f"no parameter, claim, requirement or decision named {wanted!r}: this ledger "
            f"has no parameters, no claims, no requirements and no decisions yet"
        )
    if close:
        raise NopekitError(
            f"no parameter, claim, requirement or decision named {wanted!r}. Close "
            f"matches: " + ", ".join(close)
        )
    raise NopekitError(
        f"no parameter, claim, requirement or decision named {wanted!r}, and nothing "
        f"close. `nopekit status` lists what exists"
    )
