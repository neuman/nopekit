# SPDX-License-Identifier: Apache-2.0
"""nopekit.milestones — the boundary that spends (P2.5b).

A **milestone** (GLOSSARY §4) is a named spend — a print, a board order, a field
test — and the claims it requires: ``milestones/<name>.json``. *Ready* for it is
one predicate, ``claims.unresolved`` with the milestone: every required claim
reads Checked. ``nopekit export <milestone>`` is where that predicate costs
money (METHOD rule 4: "the step that spends money refuses to run on a fail"),
and so where the cache is never trusted (R-9): the CLI re-runs every evaluator a
required claim rests on, with its controls and prerequisites, at the top tier,
and this module judges what the re-run found.

What lives here, each a pure-ish function the CLI drives (P2.5b-D5's steps 3-5):

* ``closure`` — the evaluators a milestone's required claims rest on;
* ``disagreements`` — what the forced re-run found that the records it
  overrode did not say (D7): two outcomes at one ρ, a qualification that held
  on the record and not when re-run, or two outcomes already on record;
* ``refusals`` and ``judge`` — the export's refusals, by kind, and whether it
  may write (D3, D8): only ``unresolved`` is covered by a person's recorded
  go-ahead (``--proceed``), never a disagreement, a missing id, an empty
  milestone, a broken generator or a package that is not what was recorded;
* ``test_card`` — what to measure on the article the export built (§4.6);
* ``scratch_dir``, ``build_package``, ``package_problems``, ``swap_package`` —
  the package in ``out/<milestone>/`` (D10), built aside and swapped in, never
  over a file a person put there;
* ``sealed_contradictions`` — what a fail recorded later on the article
  contradicts: the verdicts the export sealed on the article's own inputs
  (D13), never the evaluator's verdict after they moved.

Not a spine module (``verdicts.SPINE_MODULES``): an edit here keys no verdict.
Standard library only, like every module under ``src/nopekit``.

What slipped through before it: one implicit spend for the whole project, so the
page said ready whenever nothing stopped ``check`` (S-60); the committed
readiness report drifted from its ledger (S-41); and the tracked verdict cache
was forgeable in the inner loop — a hand-placed entry was served Checked until
something re-ran it, and nothing at a spend did.
"""
from __future__ import annotations

import errno
import hashlib
import json
import os
import shutil
from dataclasses import dataclass
from typing import Any, Callable, Collection, Iterable, Mapping, NamedTuple, Sequence

from . import claims as claim_logic
from . import report
from . import store
from . import verdicts
from .claims import ClaimCause
from .models import ClaimStatus, Ledger
from .util import NopekitError, seal

__all__ = [
    "Refusal", "COVERED", "Judgment", "Disagreement", "closure", "disagreements",
    "refusals", "judge", "test_card", "scratch_dir", "build_package", "Package",
    "package_problems", "swap_package", "sealed_contradictions", "sealed_on",
    "bound_export", "counted_on", "MANIFEST", "SPINE_FILES",
]

#: The refusal kinds a person's recorded go-ahead covers (D8): an unresolved
#: required claim, and nothing else. *Rejected:* covering a disagreement (the
#: person decided on what the cache showed, and the cache lied); a missing id or
#: an empty milestone (nothing to decide over); a generator or a package problem
#: (no spend can be made from a broken package); a model that does not load
#: (nothing re-ran: the statuses a go-ahead would name are the cache's).
COVERED = frozenset({"unresolved"})

#: The package's manifest (D10): sorted keys, no clock; outside the package
#: hash, which it names.
MANIFEST = "MANIFEST.json"

#: The files the spine writes into every package beside the generator's: the
#: milestone's readiness report and the values the article recorded. A
#: generator that wrote one of these names would have its bytes replaced, so it
#: is refused instead. One tuple with the judge's (`verdicts._built_seal`
#: leaves these out of what was printed).
SPINE_FILES = verdicts._PACKAGE_SPINE_FILES


@dataclass(frozen=True)
class Refusal:
    """One reason an export does not write: ``kind`` (``unresolved``,
    ``missing``, ``requires-nothing``, ``disagrees``, ``generator``,
    ``package``, ``precondition``, ``model`` — the model does not load, so
    nothing the milestone requires re-ran: the review of P2.5b), its
    ``subject`` (a claim id, a gate, the milestone, a path) and ``reason`` in
    `report.HUMAN`'s words."""

    kind: str
    subject: str
    reason: str

    def to_dict(self) -> dict[str, str]:
        return {"kind": self.kind, "subject": self.subject, "reason": self.reason}


class Judgment(NamedTuple):
    """``judge``'s answer: ``ready`` — the milestone's *ready* on the view it was
    judged on (``claims.unresolved``); ``refusals``; ``writes`` — no refusal, or
    only covered ones and ``decided``; ``found`` — the predicate's fields."""

    ready: bool
    refusals: list
    writes: bool
    found: Any


class Disagreement(NamedTuple):
    """A re-run that did not say what the records said (D7): ``gate``,
    ``kind`` (``outcome``, ``records``, ``qualification``), and its row words."""

    gate: str
    kind: str
    line: str


# --------------------------------------------------------------------------- #
# what the boundary re-runs
# --------------------------------------------------------------------------- #
def closure(view: Ledger, registry: Any, milestone: Any) -> list[str]:
    """Every evaluator a required claim of ``milestone`` rests on — the coverage
    ``compose`` reads (``claims.effective_gates``) — sorted. The sweep adds each
    one's prerequisites (``gates.plan``: ``--only`` runs a named gate's guard
    too), so a guard that covers no required claim alone is re-run with what it
    guards. *Rejected:* re-running every registered evaluator (the boundary
    pays for what the spend requires; the rest is shown as last evaluated)."""
    cover = claim_logic.effective_gates(view, registry)
    found: set[str] = set()
    for cid in claim_logic.required_ids(view, milestone):
        found.update(cover.get(cid, ()))
    return sorted(found)


def _before_conflict(before: Any, gate: str) -> str:
    row = (getattr(before, "rows", None) or {}).get(gate)
    reason = str(getattr(row, "stale_reason", "") or "")
    return reason if reason.startswith(verdicts._TWO_OUTCOMES) else ""


def disagreements(before: Any, result: Any, gates: Collection[str] = ()) -> list[Disagreement]:
    """What the forced re-run found that the records did not say (D7), from the
    records as well as from the before/after pair (critique 3 of the P2.5b
    design): a row's own ``disagrees`` (two outcomes at one ρ, whatever the
    instruments; a qualification the records held that does not hold re-run);
    a row whose verdict IS the two-outcomes error (the refused export before
    this one filed its re-run beside the forgery); a row unqualified on two
    control outcomes; and a gate the records already held two outcomes for.
    Each is a refusal no decision covers, on every later export too — never an
    ordinary unresolved claim a go-ahead would cover. A run at another ρ is no
    disagreement: it is the evidence."""
    said = report.HUMAN["export"]
    out: list[Disagreement] = []
    for row in getattr(result, "rows", None) or ():
        verdict = row.verdict
        gate = verdict.gate
        if gates and gate not in gates and not row.executed:
            continue
        found = tuple(getattr(row, "disagrees", ()) or ())
        error = str(getattr(verdict, "error", "") or "")
        token = str(getattr(verdict, "unqualified", "") or "")
        if found and found[0] == "outcome":
            _kind, before8, was, now, rho_ = found
            detail = report._one(verdict.detail)
            out.append(Disagreement(gate, "outcome", said["two_outcomes"].format(
                gate=gate, before=was, rho=str(rho_)[:12],
                entry=f"{str(rho_)[:16]}-{before8}", after=now,
                detail=f" ({detail})" if detail else "")))
        elif found and found[0] == "qualification":
            out.append(Disagreement(gate, "qualification", said[
                "two_outcomes_qualification"].format(
                    gate=gate, why=report.qualification_reason(found[1]) if found[1] else "")))
        elif found or error.startswith(verdicts._TWO_OUTCOMES):
            text = found[1] if found and len(found) > 1 else error
            out.append(Disagreement(gate, "records", said["two_outcomes_records"].format(
                gate=gate, text=report._one(text))))
        elif token.split("|", 1)[0] in ("control:two-outcomes", "control:differs"):
            out.append(Disagreement(gate, "qualification", said[
                "two_outcomes_qualification"].format(
                    gate=gate, why=report.qualification_reason(token))))
        elif _before_conflict(before, gate):
            out.append(Disagreement(gate, "records", said["two_outcomes_records"].format(
                gate=gate, text=report._one(_before_conflict(before, gate)))))
    return out


# --------------------------------------------------------------------------- #
# the judgment
# --------------------------------------------------------------------------- #
def _reprint(view: Ledger, composed: Mapping[str, Any], claim: Any, name: str) -> str:
    """A required claim Failing on a physical fail whose article the design has
    since moved from: the reprint that could answer it is a decision a person
    records — `--proceed` — and the words say so (critique 13 of the P2.5b
    design: never a carve-out, never a dead end).

    Only a fail on an article an export built, traced, can be released by a
    pass on a reprint (`verdicts._supersedes`). A fail on any other article —
    recorded without `--article`, or on an untraced export — counts on every
    design from now on, and the words say that instead: what slipped through
    (review of P2.5b, finding 17) was the reprint offered for such a fail too,
    a path that ended where it began."""
    found = composed.get(claim.id)
    if found is None or found.cause not in (ClaimCause.PHYSICAL_FAIL, ClaimCause.CONTRADICTION):
        return ""
    result = getattr(claim, "physical_result", None)
    standing = getattr(claim, "standing", None)
    results = list(getattr(claim, "results", ()) or ())
    if result is None or standing is None or result not in results:
        return ""
    index = max(i for i, item in enumerate(results) if item == result)
    entry = next((e for e in getattr(standing, "entries", ()) or () if e.index == index),
                 None)
    if entry is None or entry.article_state != "moved":
        return ""
    article = getattr(result, "article", None) or {}
    said = report.HUMAN["export"]
    if not (isinstance(article, Mapping) and article.get("source") == "export"
            and article.get("traced") is True):
        which = said["reprint_untraced" if isinstance(article, Mapping)
                     and article.get("source") == "export" else "reprint_unexported"]
        return said["reprint_never"].format(id=claim.id, article=report.article12(entry.article),
                                            which=which, m=name)
    return said["reprint"].format(id=claim.id, article=report.article12(entry.article),
                                  m=name)


def _unresolved_words(status: Any) -> str:
    """An unresolved required claim's word in a refusal: its status word, and
    a crash said apart — `skipped (errored)` — as every count and sentence says
    it (invariant 2, GLOSSARY §3). What slipped through (review of P2.5b,
    finding 23): `report.word` gives Skipped's one word for both, so the line a
    person acts on said `C1 skipped` for a crash, a missing tool's words."""
    words = report.word(status.status, errored=status.errored)
    if status.errored:
        words += f" ({report.HUMAN['lead'][ClaimCause.ERRORED]})"
    return words


def refusals(view: Ledger, composed: Mapping[str, Any], milestone: Any, *,
             disagreements: Sequence[Disagreement] = (),
             extra: Iterable[Refusal] = ()) -> list[Refusal]:
    """The export's refusals over the view it judged on, in order: the
    milestone's own (``requires-nothing``, ``missing``), each unresolved
    required claim in severity order (``unresolved``, its word; a reprint's
    decision named where it answers a moved fail), each disagreement
    (``disagrees``), then ``extra`` (the generator's, the package's, the
    preconditions). ``claims.unresolved`` is the one predicate it reads."""
    said = report.HUMAN["export"]
    name = str(getattr(milestone, "id", "") or "")
    out: list[Refusal] = []
    if not list(getattr(milestone, "requires", None) or ()):
        out.append(Refusal("requires-nothing", name,
                           said["requires_nothing"].format(m=name)))
    found = claim_logic.unresolved(view, composed, milestone)
    for cid in found.missing:
        out.append(Refusal("missing", cid, said["missing"].format(m=name, id=cid)))
    for claim in report.in_severity(view, composed, found.unresolved):
        status = composed[claim.id]
        text = said["unresolved"].format(id=claim.id, word=_unresolved_words(status))
        hint = _reprint(view, composed, claim, name)
        out.append(Refusal("unresolved", claim.id, f"{text} ({hint})" if hint else text))
    for found_ in disagreements:
        out.append(Refusal("disagrees", found_.gate, said["disagrees"].format(gate=found_.gate)))
    out.extend(extra)
    return out


def judge(view: Ledger, composed: Mapping[str, Any], milestone: Any, *,
          disagreements: Sequence[Disagreement] = (), extra: Iterable[Refusal] = (),
          decided: bool = False) -> Judgment:
    """Whether the export writes (D3, D8): with no refusal, or with only
    ``unresolved`` ones and a person's recorded decision over them."""
    found = claim_logic.unresolved(view, composed, milestone)
    refused = refusals(view, composed, milestone, disagreements=disagreements, extra=extra)
    blocking = [r for r in refused if r.kind not in COVERED or not decided]
    return Judgment(found.ready, refused, not blocking, found)


def counted_on(view: Ledger, composed: Mapping[str, Any], milestone: Any, registry: Any,
               resolution: Any) -> dict[str, list]:
    """``{required claim id: [counted pass, ...]}`` — each covering evaluator
    whose pass counted on the RE-EXECUTED view (``claims.contradicted_by``, the
    rows a fail seals), sealed into the export record (D13): a fail recorded on
    this article later is charged to the verdicts on the article's own inputs,
    never to the evaluator's verdict after they moved. Required claims only
    (critique 18 of the P2.5b design: a claim the milestone does not require
    was not re-run, and a cache-only pass never takes a contradiction)."""
    needs = {spec.id: list(getattr(spec, "needs", None) or ())
             for spec in (registry.specs() if registry is not None else ())}
    codes: dict[str, str] = {}
    pairs = getattr(registry, "pairs", None)
    if callable(pairs):
        for spec, fn in pairs():
            codes[spec.id] = verdicts.code_digest(spec, fn, anchors=resolution.anchors).digest \
                if getattr(resolution, "anchors", None) is not None else ""
    out: dict[str, list] = {}
    by_id = {c.id: c for c in view.claims}
    for cid in claim_logic.required_ids(view, milestone):
        claim = by_id.get(cid)
        if claim is None:
            continue
        out[cid] = claim_logic.contradicted_by(claim, view.verdicts,
                                               stale_gates=resolution.stale_gates,
                                               needs=needs, codes=codes)
    return out


def sealed_contradictions(entry: Any, claim_id: str) -> list:
    """What a fail on ``claim_id`` recorded on ``entry``'s article contradicts:
    the export's sealed ``counted`` (D13). One function, so the channel and a
    planted reader (V-9) read one place."""
    found = (getattr(entry, "counted", None) or {}).get(claim_id)
    return [dict(item) for item in found or () if isinstance(item, Mapping)]


def sealed_on(entries: Iterable[Any], claim_id: str) -> list:
    """What a fail on ``claim_id`` contradicts when ``entries`` — every export
    record that holds one article — sealed it: each one's ``counted``
    (``sealed_contradictions``), together, one row per evaluator version
    (``gate``, ``code``), the newest export's row where two seal one version.

    One article, many records: its hash leaves out the milestone and the
    clock, so two milestones that share a generator, or one exported again at
    an unchanged design, record it twice. What slipped through (review of
    P2.5b, findings 7 and 15): the record kept was the first in name order, so
    a ruler's fail on the bracket bound to a milestone that did not require
    C1 — `contradicts: []` — and the evaluator's track record lost what the
    other milestone's export had sealed. *Rejected:* the newest record alone
    (an export of a milestone that does not require the claim, after one that
    does, would drop it the same way); asking the person to choose (both
    records describe one object)."""
    rows: dict[tuple, dict] = {}
    for entry in sorted(entries or (), key=lambda e: str(getattr(e, "when", "") or "")):
        for item in sealed_contradictions(entry, claim_id):
            rows[(str(item.get("gate") or ""), str(item.get("code") or ""))] = item
    return [rows[key] for key in sorted(rows)]


def bound_export(entries: Iterable[Any], claim_id: str) -> Any:
    """The export record a result on ``claim_id`` names among ``entries`` (one
    article's): the newest whose re-run sealed the claim, else the newest. Its
    milestone, clock and revision are what the prompt shows; ``sealed_on`` is
    what a fail charges, and ``claims.latency`` measures from the newest export
    of the article that is not after the result, whichever record this is."""
    ordered = sorted(entries or (), key=lambda e: str(getattr(e, "when", "") or ""))
    sealed = [e for e in ordered if claim_id in (getattr(e, "counted", None) or {})]
    return (sealed or ordered or [None])[-1]


# --------------------------------------------------------------------------- #
# the test card (§4.6)
# --------------------------------------------------------------------------- #
def test_card(view: Ledger, composed: Mapping[str, Any], milestone: Any, article: str,
              exports: Iterable[Any]) -> list[str]:
    """What to measure on the article an export built: every claim whose
    terminal is a measurement and that does not read Checked — Pending build,
    or Stale on a moved article — each with its test, its latency and the exact
    command that records it on this article; then, as cross-checks, every
    required automated claim with a limit (critique 9 of the P2.5b design: E4's
    first article is the bracket's C1 measured with a ruler, and a fail there is
    a contradiction on its evaluator's track record — so the card offers it,
    marked a cross-check). Expert judgments are not on it: a person, not an
    article. ``[]`` when there is nothing to measure."""
    said = report.HUMAN["export"]
    a12 = report.article12(article)
    exports = list(exports or ())
    physical = []
    for claim in view.claims:
        found = composed.get(claim.id)
        standing = getattr(claim, "standing", None)
        if found is None or getattr(standing, "state", "") == verdicts.REMOVED:
            continue
        try:
            terminal = claim_logic.terminal_of(claim)
        except NopekitError:
            continue
        if terminal == "measurement" and found.status not in (ClaimStatus.PASS,
                                                              ClaimStatus.VERIFIED):
            physical.append(claim)
    requires = set(claim_logic.required_ids(view, milestone))
    cross = []
    for claim in view.claims:
        if claim.id not in requires:
            continue
        try:
            terminal = claim_logic.terminal_of(claim)
        except NopekitError:
            continue
        if terminal == "" and getattr(claim.acceptance, "limit", None) is not None:
            cross.append(claim)
    if not physical and not cross:
        return []
    lines = [said["card_head"].format(article=a12, n=len(physical),
                                      results=report._plural(len(physical), "result"),
                                      k=len(cross),
                                      checks=report._plural(len(cross), "cross-check"))]
    for claim in physical:
        test = (report._trunc(report._one(claim.note), 120) if claim.note
                else claim.acceptance.render() or said["no_test"].format(id=claim.id))
        lines.append("  " + said["card_row"].format(
            id=claim.id, statement=report._trunc(report._one(claim.statement), 70),
            test=test, latency=report.latency_words(claim, exports)))
        lines.append("  " + said["card_command"].format(id=claim.id, article=a12))
    for claim in cross:
        lines.append("  " + said["card_cross"].format(
            id=claim.id, statement=report._trunc(report._one(claim.statement), 70),
            condition=claim.acceptance.render(),
            gates=", ".join(claim.gates or ()) or "none"))
    if cross:
        lines.append("  " + said["card_cross_command"].format(article=a12))
    return lines


# --------------------------------------------------------------------------- #
# the package (D10)
# --------------------------------------------------------------------------- #
class Package(NamedTuple):
    """A package built in scratch: ``hash`` — the seal of ``{"files": files}``,
    the manifest excluded; ``files`` — ``{rel: sha256}``; ``manifest`` — the
    manifest's sha256."""

    hash: str
    files: dict
    manifest: str


def scratch_dir(root: str, name: str, dry_run: bool) -> str:
    """Where an export builds its package: ``.nopekit/out/export-<m>/``, in
    BOTH modes, created and removed under the build lock — under the out
    directory the generator's trace knows (``verdicts.anchors_for``'s
    ``out_dir``), so what the generator does to its own directory is never a
    read of the project, and the dry run builds exactly what the written export
    builds (D-15). The written swap moves it to ``out/<m>/`` on the project's
    filesystem. Never ``out/<m>/`` itself: a build that failed half way would
    leave half a package where a person looks for one (V-7's planted writer).

    What slipped through (review of P2.5b, finding 6): the written export built
    in ``out/.<m>.tmp-<pid>/``, a project path to the trace — so a generator's
    ``os.makedirs(ctx.out_dir)`` put the scratch, pid and all, into the article
    (removed by the swap, it read moved the moment it was recorded), a listing
    of it refused every written export as "inputs moved", and a copy into it
    made the written article untraced where the dry run's was traced.
    *Rejected:* a pid suffix (the lock serialises exports; the pid made the two
    modes' paths differ, and a generator that writes its directory's name into
    a file then differed between them); adding the scratch as a second out
    anchor (two rules for one directory)."""
    del dry_run                                  # one path for both modes (D-15)
    return os.path.join(store.out_dir(root), f"export-{name}")


def _sha(path: str) -> str:
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def _files(base: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for dirpath, _dirnames, filenames in os.walk(base):
        for name in filenames:
            full = os.path.join(dirpath, name)
            out[os.path.relpath(full, base).replace(os.sep, "/")] = _sha(full)
    return dict(sorted(out.items()))


def _canonical(data: Any) -> str:
    return json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n"


def build_package(scratch: str, *, report_md: str, carried: Mapping[str, Any],
                  manifest: Mapping[str, Any]) -> Package:
    """Write the spine's files into ``scratch`` beside the generator's — the
    milestone's `REPORT.md`, `model.json` (exactly the values the article
    recorded: critique 1 of the P2.5b design, a value the package handed the
    builder that the article never recorded would be a change that moves no
    article) — hash every file, and write `MANIFEST.json` naming them, the
    article and the package hash. No clock in any of it: two exports of one
    design are byte-identical."""
    with open(os.path.join(scratch, "REPORT.md"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(report_md)
    with open(os.path.join(scratch, "model.json"), "w", encoding="utf-8",
              newline="\n") as fh:
        fh.write(_canonical(dict(carried)))
    files = {rel: sha for rel, sha in _files(scratch).items() if rel != MANIFEST}
    package = seal({"files": files})
    with open(os.path.join(scratch, MANIFEST), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(_canonical({**dict(manifest), "files": files, "package": package}))
    return Package(package, files, _sha(os.path.join(scratch, MANIFEST)))


def package_problems(root: str, name: str, exports: Iterable[Any]) -> list[tuple[str, str]]:
    """``[(kind, rel)]`` — each file in ``out/<name>/`` that no recorded export of
    ``name`` wrote (``foreign``) or that changed since the newest one did
    (``edited``). A package a person sliced, annotated or added a file to is
    never replaced (D10; risk 6: ``/out/`` is ignored, so git would not show the
    loss)."""
    base = os.path.join(root, store.PACKAGES_NAME, name)
    if not os.path.isdir(base):
        return []
    entries = [e for e in exports or () if getattr(e, "milestone", "") == name]
    newest = dict(getattr(entries[-1], "package", None) or {}) if entries else {}
    recorded = dict(newest.get("files") or {})
    out: list[tuple[str, str]] = []
    for rel, sha in _files(base).items():
        if rel == MANIFEST:
            if not entries or sha != newest.get("manifest"):
                out.append(("edited" if entries else "foreign", rel))
            continue
        if rel not in recorded:
            out.append(("foreign", rel))
        elif recorded[rel] != sha:
            out.append(("edited", rel))
    return out


def _move(source: str, target: str) -> None:
    """``source`` renamed to ``target``; across filesystems (a ``.nopekit/``
    linked elsewhere) copied beside ``target`` first, then renamed, so a
    reader never finds half a package."""
    try:
        os.replace(source, target)
    except OSError as exc:
        if exc.errno != errno.EXDEV:
            raise
        beside = os.path.join(os.path.dirname(target),
                              f".{os.path.basename(target)}.copy-{os.getpid()}")
        shutil.copytree(source, beside, symlinks=True)
        os.replace(beside, target)
        shutil.rmtree(source, ignore_errors=True)


def swap_package(root: str, name: str, scratch: str,
                 record: Callable[[], Any] | None = None) -> str:
    """``scratch`` becomes ``out/<name>/``: the older package aside, the new one
    in, then ``record`` — the export record's append — and only then the older
    one removed. When ``record`` raises, the new package is taken back out and
    the older one put back before the error goes on: a package no record names
    is never left where the next export would refuse it as a file "not written
    by an export" — nopekit's own bytes, while `doctor` said every package was
    as written (review of P2.5b, finding 9: the swap ran first and the append
    after it, so an unwritable `exports/` left exactly that). Returns the
    package's path. *Rejected:* appending before the swap (a swap that then
    failed left a record naming a package that is not there)."""
    base = os.path.join(root, store.PACKAGES_NAME)
    os.makedirs(base, exist_ok=True)
    final = os.path.join(base, name)
    aside = os.path.join(base, f".{name}.old-{os.getpid()}")
    had = os.path.exists(final)
    if had:
        os.replace(final, aside)
    try:
        _move(scratch, final)
        if record is not None:
            record()
    except BaseException:
        if os.path.exists(final):
            taken = os.path.join(base, f".{name}.new-{os.getpid()}")
            os.replace(final, taken)
            shutil.rmtree(taken, ignore_errors=True)
        if had:
            os.replace(aside, final)
        raise
    if had:
        shutil.rmtree(aside, ignore_errors=True)
    return final
