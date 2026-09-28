# SPDX-License-Identifier: Apache-2.0
"""atompipe.decisions — the log of what was chosen, and of everything that LOST.

This module is rule 3 of the method (*every constant carries provenance —
including the alternatives that were rejected and why*) made into a file you can
actually read.

A mature project's decision log runs to thousands of lines, newest first, and the lines
that earn their keep are the negative ones:

    the wider trace was tried first and the router could not close that net
    a rigid hook was tried and rejected — it snapped in the 3-point bend
    do not fit the narrower ribbon: it floats in the housing with 4 mm of slop

None of those can be recovered from the CAD, the git history, or anybody's
memory two months later. Without them every fresh context window re-litigates
every settled number, re-proposes 0.5 mm, and burns a day discovering the same
wall. A rejected alternative *with its reason* is the only thing that stops it.

Two functions carry the weight:

* `render_log` writes the whole log, newest first, for humans and for git.
* `why` is the context-window win. Given one parameter name or claim id it
  returns a dense block — current value, whether it is derived and from what,
  the rationale, **every** rejected alternative, the gates that protect it and
  how they last ran, the input artifacts that ground it, and the decisions that
  moved it. An agent calls `why` instead of reading the 1,672 lines, which is
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

from . import store
from .models import Claim, Decision, Ledger, Param, Rejected, Verdict, slugify
from .util import AtompipeError, atomic_write_text, ensure_dir, iter_suffix_unique

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
            raise AtompipeError(
                f"{field} takes names as strings, got {type(value).__name__}: {value!r}"
            )
        name = value.strip()
        if name and name not in out:
            out.append(name)
    return out


def _coerce_rejected(items: Iterable[Any] | None) -> list[Rejected]:
    """Accept `Rejected`, a dict, or a `(value, why[, evidence])` pair.

    The tuple and dict forms exist so the CLI and a pack can hand over what they
    parsed without importing the model, but every form goes through the same
    gate: **a rejected alternative with no reason is refused.** "the wider trace
    was tried" is worthless; "the wider trace was tried and the router could not
    close that net through the congested corridor" is the whole reason this log
    exists. Letting the reasonless form through would fill the log with lines that
    look like provenance and answer nothing, and the next agent would re-try it
    anyway.
    """
    out: list[Rejected] = []
    for item in items or ():
        if isinstance(item, Rejected):
            value, reason, evidence = item.value, item.why, item.evidence
        elif isinstance(item, dict):
            # Not Rejected.from_dict: that raises TypeError on a dict missing
            # `value`/`why`, and a malformed --rejected flag is a user error
            # that deserves the sentence below, not a traceback.
            value = item.get("value", "")
            reason = item.get("why", "")
            evidence = item.get("evidence", "")
        elif isinstance(item, str):
            raise AtompipeError(
                f"rejected alternative {item!r} has no reason. Give a "
                f"(value, why) pair: what lost, and the concrete thing that "
                f"beat it — 'the router could not close that net', not 'worse'"
            )
        elif isinstance(item, Sequence) and 2 <= len(item) <= 3:
            value, reason = item[0], item[1]
            evidence = item[2] if len(item) > 2 else ""
        else:
            raise AtompipeError(
                f"cannot read a rejected alternative from {item!r} — expected "
                f"Rejected, a dict, or a (value, why) pair"
            )

        value, reason, evidence = _one_line(value), _one_line(reason), _one_line(evidence)
        if not value:
            raise AtompipeError(
                "a rejected alternative needs a value: the thing that lost, "
                "rendered as it would be written ('0.5 mm', 'a rigid hook')"
            )
        if not reason:
            raise AtompipeError(
                f"rejected {value!r} has no reason. Say concretely what beat it; "
                f"a rejection without its reason gets re-proposed by the next "
                f"agent that reads the model"
            )
        out.append(Rejected(value=value, why=reason, evidence=evidence))
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
    at 1,672 lines that is a scroll nobody does.

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
        raise AtompipeError("a decision needs a title — it becomes the entry's heading and its id")
    summary = _one_line(summary)
    if not summary:
        raise AtompipeError(
            f"decision {title!r} needs a summary: one line saying what changed and why. "
            f"A decision with no summary is a timestamp"
        )
    when = _one_line(when)
    if not when:
        raise AtompipeError(
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
_LOG_PREAMBLE = """\
Generated by `atompipe decide` — edit the ledger, not this file.

Newest first. Every entry names what **lost** and why. That is the point: a
rejected alternative with its reason is the only thing that stops the next
person (or the next context window) re-proposing it and re-discovering the same
wall. `atompipe why <param-or-claim>` pulls one item's slice of this file.\
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
            "    atompipe decide --title ... --summary ...",
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
            for item in decision.rejected:
                line = f"- **{item.value}** — {item.why}"
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
    file is regenerated on every `decide`, and a crash against a truncating
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


def _rejected_lines(items: Sequence[tuple[Rejected, str]]) -> list[str]:
    """Render `(rejected, source)` pairs as the densest honest form.

    These are the highest-value lines in the whole block, so they lead with the
    value that lost and the reason on the same line; the provenance tail (which
    decision recorded it, what evidence backs it) only costs a second line when
    it exists.
    """
    lines: list[str] = []
    for item, source in items:
        lines += _wrap(f"- {item.value} — {item.why}", indent="  ", hanging="    ")
        tail = []
        if item.evidence:
            tail.append(f"evidence: {item.evidence}")
        if source:
            tail.append(f"from: {source}")
        if tail:
            lines += _wrap("  ·  ".join(tail), indent="      ")
    return lines


def _collect_rejected(
    ledger: Ledger, *, own: Sequence[Rejected], names: set[str]
) -> list[tuple[Rejected, str]]:
    """Every rejected alternative touching this item, from the record AND the log.

    A parameter's own `rejected` list holds what lost when the value was set;
    the decision log holds what lost every time it moved since. Reading only one
    of the two is how "we already tried that" gets forgotten — the reference
    project's rigid-hook rejection lives in a decision, not in a parameter
    record. Deduped on (value, why) case-folded, because a decision usually
    repeats the rejection it also wrote onto the parameter.
    """
    collected: list[tuple[Rejected, str]] = [(item, "") for item in own or ()]
    for decision in ledger.decisions:
        if names & (set(decision.params_changed) | set(decision.claims_changed)):
            collected += [(item, f"decision {decision.id}") for item in decision.rejected or ()]

    seen: set[tuple[str, str]] = set()
    unique: list[tuple[Rejected, str]] = []
    for item, source in collected:
        key = (item.value.strip().casefold(), item.why.strip().casefold())
        if key in seen:
            continue
        seen.add(key)
        unique.append((item, source))
    return unique


def _split_verdict(verdict: Verdict) -> tuple[str, str]:
    """`verdict.render()` split into its `[tag]` head and its body text.

    Taken apart rather than re-derived: the tag spelling (`[ok  ]`, `[skip]`,
    `[FAIL]`, `[ERR ]`, padded to column up) belongs to `Verdict.render`, and a
    second copy of that mapping here would be a second answer to "did this gate
    pass" living two modules from the first.

    A verdict with no body renders as `[tag] gate` with no `" : "`, in which case
    the whole line is the head and the body is empty.
    """
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
    """The gates whose recorded reads name parameter `name`, sorted by id.

    A read counts when `name` is one of the first two keys of its path —
    `ctx.params["thickness"]` and `ctx.params["config"]["thickness"]`, the two
    spellings gates use — and deeper keys do not: a gate walking a BOM would
    otherwise protect every line item that shares a parameter's name. The same
    rule as `cli._param_gates`, which fills `Param.gates` for the not-yet-switched
    readers; one of the two goes when `why` is the only consumer (U29).
    """
    found: list[str] = []
    for gate_id in sorted(read_sets):
        for path in read_sets[gate_id] or ():
            parts = (path,) if isinstance(path, str) else tuple(path)
            if name in [part for part in parts[:2] if isinstance(part, str)]:
                found.append(gate_id)
                break
    return found


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
    every gate id still appears. First-appearance order is kept so two runs over
    an unchanged ledger print identical text.
    """
    groups: dict[tuple[str, str], list[str]] = {}
    for gid in gate_ids:
        verdict = evidence.verdict(ledger, gid) if evidence else ledger.verdict(gid)
        if verdict is None:
            # Never run is an outcome like any other, and gates that never ran
            # group together the same way.
            key = ("[ -- ]", "never run")
        else:
            key = _split_verdict(verdict)
        groups.setdefault(key, []).append(gid)

    lines: list[str] = []
    for (head, body), ids in groups.items():
        if len(ids) == 1:
            # One gate, one line: the dense `[skip] gate.id : reason` form, which
            # is strictly better than a two-line form when there is nothing to
            # share the reason with.
            one = f"{head} {ids[0]}" + (f" : {body}" if body else "")
            lines += _wrap(one, indent="  ", hanging="         ", collapse=False)
            continue
        lines += _wrap(f"{head} " + ", ".join(ids), indent="  ",
                       hanging="         ", collapse=False)
        if body:
            # Indented to the same column the single-gate form wraps to, so the
            # reason reads as belonging to the ids above it.
            lines += _wrap(body, indent="         ", collapse=False)
    return lines


def _grounding_lines(ledger: Ledger, artifact_ids: Sequence[str], names: set[str]) -> list[str]:
    """Input artifacts that ground this item, plus the extraction that did it.

    An artifact id alone ("grounded by sk-hull-png") is not grounding; the
    sentence that was read out of it is. A generated layout is auditable two
    months later only because it records "digitised from inputs/sketches/panel.png,
    legends corrected against inputs/references/unit-front.png".
    """
    lines: list[str] = []
    for aid in artifact_ids:
        artifact = ledger.artifact(aid)
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
    out += _rejected_lines(rejected) if rejected else _wrap(
        "(none recorded — nothing here stops this being re-litigated)"
    )

    protecting = evidence.param_gates(param.name, param.gates)
    _section(out, f"GATES ({len(protecting)})")
    out += _gate_lines(ledger, protecting, evidence) if protecting else _wrap(
        "(none — no gate would notice if this value went wrong)"
    )

    _section(out, f"GROUNDED BY ({len(param.grounded_by)})")
    out += _grounding_lines(ledger, param.grounded_by, names) if param.grounded_by else _wrap(
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


def _tagged_rejections(ledger: Ledger, view: Any) -> list[tuple[Rejected, str]]:
    """The view's losers (the model's PARAMS, then the record's), then those of
    every decision that moved the param — each tagged with its home, each once.
    Deduped on (value, why) case-folded, as `_collect_rejected` does: a decision
    usually repeats the rejection it also wrote where the param lives."""
    rows = list(view.rejected or ())
    for decision in _decisions_touching(ledger, {view.name}):
        origin = _DECISION_HOME.format(id=decision.id)
        rows += [(item, origin) for item in decision.rejected or ()]
    seen: set[tuple[str, str]] = set()
    unique: list[tuple[Rejected, str]] = []
    for item, origin in rows:
        key = (_one_line(item.value).casefold(), _one_line(item.why).casefold())
        if key not in seen:
            seen.add(key)
            unique.append((item, origin))
    return unique


def _tagged_lines(rows: Sequence[tuple[Rejected, str]]) -> list[str]:
    """`  4.0 mm — 3.75 mm deflection, 7.5x the limit   (model/bracket.py PARAMS)`.

    One loser, one line, never wrapped: the row is a shape (`WHY_REJECTED_ROW`),
    and wrapping it would put the value on one line and the file it lives in on
    another, where no grep for either finds both. Evidence, when there is any,
    goes on the line after, indented past the row.
    """
    lines: list[str] = []
    for item, origin in rows:
        lines.append(f"  {_one_line(item.value)} — {_one_line(item.why)}   ({origin})")
        if item.evidence:
            lines.append(f"      evidence: {_one_line(item.evidence)}")
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
    out += _tagged_lines(rejected) if rejected else _wrap(
        "(none recorded — nothing here stops this being re-litigated)"
    )

    protecting = evidence.param_gates(view.name, record.gates if record else ())
    _section(out, f"GATES ({len(protecting)})")
    out += _gate_lines(ledger, protecting, evidence) if protecting else _wrap(
        "(none — no gate would notice if this value went wrong)"
    )

    grounded = list(view.grounded_by or ())
    _section(out, f"GROUNDED BY ({len(grounded)})")
    out += _grounding_lines(ledger, grounded, names) if grounded else _wrap(
        "(nothing — this value is asserted, not evidenced)"
    )

    moved = _decisions_touching(ledger, names)
    _section(out, f"DECISIONS ({len(moved)}, newest first)")
    out += _decision_lines(moved) if moved else _wrap(
        "(none — this value has never been revisited)"
    )

    return "\n".join(out).rstrip() + "\n"


def _why_claim(ledger: Ledger, claim: Claim, evidence: _Evidence) -> str:
    names = {claim.id}
    flags = [str(claim.kind)]
    flags.append("critical" if claim.critical else "nice-to-have")
    out: list[str] = [f"claim  {claim.id}  [{', '.join(flags)}]"]
    out += _wrap(claim.statement)

    acceptance = claim.acceptance.render() if claim.acceptance else ""
    out += _wrap(
        f"acceptance: {acceptance}" if acceptance
        else "acceptance: NONE — without a threshold this is a wish, not a claim",
        indent="  ",
    )
    if claim.source:
        out += _wrap(f"source: {claim.source}", indent="  ")
    if claim.tags:
        out += _wrap("tags: " + ", ".join(claim.tags), indent="  ")
    if claim.physical_result is not None:
        result = claim.physical_result
        verdict = "PASSED" if result.passed else "FAILED"
        stamp = " ".join(x for x in (result.when, result.who) if x)
        out += _wrap(
            f"real-world result: {verdict}" + (f" ({stamp})" if stamp else "")
            + (f" — {result.detail}" if result.detail else ""),
            indent="  ",
            hanging="    ",
        )

    _section(out, "WHY")
    out += _wrap(claim.rationale or "(no rationale recorded — what breaks if this is false?)")
    if claim.note:
        out += _wrap(claim.note)

    # A claim has no `rejected` field of its own; everything that lost while
    # settling it lives in the decisions that touched it.
    rejected = _collect_rejected(ledger, own=(), names=names)
    _section(out, f"REJECTED ({len(rejected)})")
    out += _rejected_lines(rejected) if rejected else _wrap("(none recorded)")

    covering = evidence.claim_gates(claim)
    _section(out, f"GATES ({len(covering)})")
    out += _gate_lines(ledger, covering, evidence) if covering else _wrap(
        "(none — this claim is UNCLAIMED: no gate can settle it)"
    )

    _section(out, f"GROUNDED BY ({len(claim.grounded_by)})")
    out += _grounding_lines(ledger, claim.grounded_by, names) if claim.grounded_by else _wrap(
        "(nothing — this claim came from a conversation, not from evidence)"
    )

    moved = _decisions_touching(ledger, names)
    _section(out, f"DECISIONS ({len(moved)}, newest first)")
    out += _decision_lines(moved) if moved else _wrap(
        "(none — this claim has never been revisited)"
    )

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
        verdicts: Iterable[Verdict] | None = None) -> str:
    """Everything known about one parameter or claim, as one dense plain-text block.

    This is the context-window win and the reason this module exists. An agent
    about to change `wall_mm` calls `why("wall_mm")` and gets the value, what it
    derives from, the rationale, **every** alternative that was rejected and the
    concrete reason each lost, which gates protect it and how they last ran,
    which input artifacts ground it, and the decisions that moved it — instead
    of reading a 1,672-line log and a ledger to reconstruct the same thing.

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

    Resolution order: exact param name, exact claim id, then the same two
    case-insensitively, then a name the decision log mentions but no record
    holds. A param and a claim sharing a name is pathological; the param wins
    and the claim is still findable by exact id.

    Plain text, no ANSI: this output goes into a context window and into pipes
    at least as often as it goes to a terminal, and escape codes there are noise
    that costs tokens.

    Raises `AtompipeError` listing close matches when nothing matches — a
    mistyped parameter name is a user error, and the fix is almost always three
    characters away.
    """
    wanted = (name or "").strip()
    if not wanted:
        raise AtompipeError("why needs a parameter name or a claim id")

    evidence = _Evidence(coverage=coverage, read_sets=read_sets,
                         verdicts=_first_by_gate(verdicts))
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

    folded = wanted.casefold()
    text = param_named(lambda candidate: candidate.casefold() == folded)
    if text is not None:
        return text
    for candidate in ledger.claims:
        if candidate.id.casefold() == folded:
            return _why_claim(ledger, candidate, evidence)

    param_names = ([v.name for v in views] if views is not None
                   else [p.name for p in ledger.params])
    known = param_names + [c.id for c in ledger.claims]
    close = difflib.get_close_matches(wanted, known, n=_SUGGESTIONS, cutoff=0.6)

    in_decisions = any(
        wanted in (d.params_changed or ()) or wanted in (d.claims_changed or ())
        for d in ledger.decisions
    )
    if in_decisions:
        return _why_decision_only(ledger, wanted, close)

    # A decision id is the thing people reach for next, so name the confusion
    # instead of offering "did you mean" on an unrelated parameter.
    if any(d.id == wanted for d in ledger.decisions):
        raise AtompipeError(
            f"{wanted!r} is a decision id, not a parameter or claim — see "
            f"docs/decisions.md for its entry"
        )

    if not known:
        raise AtompipeError(
            f"no parameter or claim named {wanted!r}: this ledger has no parameters "
            f"and no claims yet"
        )
    if close:
        raise AtompipeError(
            f"no parameter or claim named {wanted!r}. Close matches: " + ", ".join(close)
        )
    raise AtompipeError(
        f"no parameter or claim named {wanted!r}, and nothing close. "
        f"`atompipe status` lists what exists"
    )
