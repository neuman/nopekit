# SPDX-License-Identifier: Apache-2.0
"""atompipe.cli — the command surface, and the only place that reads the clock.

Everything below is an edge. The spine's logic lives in the other modules and is
pure; this file resolves a project root, stamps a timestamp, takes a lock, calls
one or two functions, and renders the result. When a command here starts
computing something, that computation belongs in `claims`, `report` or `gates`
instead — a rule that has a cost and is worth it: a status line derived in the
CLI is a status line that cannot be tested and that will eventually disagree
with the report.

Four conventions hold across every command:

* **Terse and greppable by default.** One line per verdict, one line per claim,
  one line per gate. `[ok  ] beam.deflection : 0.700 mm at 15 N` is a line you
  can `grep FAIL` and a line an agent can hold fifty of. Anything longer is
  behind `--json` or in `docs/readiness.md`.
* **`--json` on every read command.** Not a pretty-printer switch: the JSON is
  the same data the human output renders, so an agent never has to parse
  columns. When `--json` is given, nothing but JSON goes to stdout.
* **`AtompipeError` -> `error: <msg>` on stderr, exit 2.** No traceback: a stack
  trace teaches a user nothing about a missing file. Anything else keeps its
  traceback, because by construction it is a bug in the spine.
* **The clock is read here and nowhere else** (contract rule 3). `utcnow_iso()`
  is called in this file and the string is passed down. Every function it calls
  takes `when`/`added` as a plain argument precisely so a run can be replayed.

Exit codes, which are the actual product for the two commands anyone puts in
CI:

    0   fine
    1   a gate-level verdict says stop  — `check` with a blocking critical claim,
        `gate selftest` with a control that did not fire, a pack gate that
        failed its own baseline, or no control exercised at all (unless
        `--allow-empty`), `doctor` with a hard failure. This is what makes
        `atompipe check` usable as a pre-spend gate: it exits non-zero *while
        anything critical is unproven*, not only when something failed.
    2   the user did something the tool cannot act on (AtompipeError, bad args)
    130 interrupted

One design note worth stating because it is load-bearing and not obvious: gates
reach a project from two places. Packs the ledger opted into (`meta.packs`) come
first, and then `<root>/gates/*.py` — the project's OWN gates, which is how the
reference project in `examples/bracket` ships six gates with no pack at all. The
spine contract describes pack loading only; project-local gates are implemented
here because a project that cannot write a gate without publishing a pack would
never write the first one.
"""
from __future__ import annotations

import argparse
import dataclasses
import importlib.util
import json
import os
import platform
import posixpath
import re
import shutil
import sys
import tempfile
import textwrap
import time
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Mapping

from . import __version__
from . import artifacts, claims, decisions, gates, modelio, packs, report, site, store, verdicts
from .models import (
    Acceptance,
    ArtifactKind,
    Claim,
    ClaimKind,
    ClaimStatus,
    Comparator,
    Extraction,
    Ledger,
    PhysicalResult,
    ProjectMeta,
    Tier,
    Verdict,
)
from .util import (
    AtompipeError,
    FileDigests,
    FileLock,
    atomic_write_text,
    human_bytes,
    human_duration,
    read_json,
    rel,
    short_hash,
    utcnow_iso,
)

__all__ = ["main", "build_parser"]

#: Project-local gates: `<root>/gates/*.py`, imported exactly like a pack's, but
#: with no manifest and no pack name. The reference project uses this and so does
#: every project before it has a pack worth extracting.
PROJECT_GATES_DIR = "gates"

#: A pack's viewgens and a project's own, symmetrically with `gates/`:
#: `packs/<name>/views/*.py` and `<root>/views/*.py`. Same name in both places
#: for the same reason `PROJECT_GATES_DIR` exists — a project that had to
#: publish a pack before it could draw its own assembly would never draw it.
VIEWGEN_DIR = "views"

#: Default bind address for `site serve`. LOOPBACK, deliberately: the page
#: carries an unreleased design, its rejected alternatives and its failing
#: claims, and a default of 0.0.0.0 hands all of that to whatever network the
#: laptop happens to be on. `--host 0.0.0.0` is one flag away when it is wanted.
SERVE_HOST = "127.0.0.1"

#: `python3 -m http.server`'s own default, because `site serve` IS that server
#: and a different number would be a gratuitous thing to have to remember.
SERVE_PORT = 8000

#: The lock every write-side command takes, for the whole operation. Two
#: `atompipe check` runs in one project would otherwise interleave their
#: read-modify-write of the ledger and the second would silently drop the first's
#: verdicts.
LOCK_NAME = "build.lock"

#: Tier ceiling meaning "everything". Tier is an IntEnum topping out at 3
#: (EXTERNAL); this is deliberately past it so a pack that invents a higher
#: number is still swept rather than silently skipped.
ALL_TIERS = 99


# --------------------------------------------------------------------------- #
# output
# --------------------------------------------------------------------------- #
def _say(text: str = "") -> None:
    """Print one line to stdout, flushed.

    Flushed because `check` streams a line per gate and a tier-2 sweep can sit
    for minutes between them; a buffered stdout turns "watch the gates land" into
    "watch nothing, then get everything at once", which is the same experience as
    no streaming at all.
    """
    print(text, flush=True)


def _warn(text: str) -> None:
    """A caveat that must not pollute stdout.

    Warnings go to stderr so `atompipe gate list | ...` and `--json` stay clean.
    A warning printed into a JSON document is a parse error at the other end.
    """
    print(text, file=sys.stderr, flush=True)


def _dump(obj: Any) -> None:
    """Write one JSON document to stdout: sorted keys, indent 2.

    Sorted and indented for the same reason the ledger is — this output gets
    diffed and eyeballed as often as it gets parsed.
    """
    print(json.dumps(obj, sort_keys=True, indent=2, ensure_ascii=False, default=str),
          flush=True)


def _tag(kind: str) -> str:
    """`[ok  ]`-style tag, padded to the width `Verdict.render` uses.

    Same four-character field as a verdict tag so a `doctor` line and a `check`
    line align in the same terminal and the same grep.
    """
    return f"[{kind:<4}]"


def _split_csv(value: str | None) -> list[str]:
    """`"a, b ,c"` -> `["a", "b", "c"]`. Empty pieces dropped.

    Used for every comma-separated flag (`--grounds`, `--tags`, `--gates`).
    Repeating the flag works too; this is for the spelling people actually type.
    """
    return [piece.strip() for piece in (value or "").split(",") if piece.strip()]


def _collect(values: Iterable[str] | None) -> list[str]:
    """Flatten a repeatable flag whose values may themselves be comma-separated."""
    out: list[str] = []
    for value in values or ():
        out.extend(_split_csv(value))
    return out


def _age(when: str, *, now: float | None = None) -> str:
    """`"4m 12s ago"` for an ISO stamp, or `""` if it cannot be read.

    Ages are rendered here rather than stored because a stored age is wrong one
    second later. An unparseable stamp returns empty instead of raising: a
    hand-edited `when` is a cosmetic problem, and refusing to print `status` over
    it would be a wildly disproportionate response.
    """
    try:
        stamp = datetime.strptime(when, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return ""
    reference = datetime.fromtimestamp(now, tz=timezone.utc) if now is not None \
        else datetime.now(timezone.utc)
    seconds = (reference - stamp).total_seconds()
    if seconds < 0:
        # A clock that moved, or a stamp from another machine. Saying "in the
        # future" is more useful than a negative duration nobody can act on.
        return "in the future"
    return f"{human_duration(seconds)} ago"


# --------------------------------------------------------------------------- #
# project plumbing
# --------------------------------------------------------------------------- #
def _root(args: argparse.Namespace) -> str:
    """The project root for this invocation, or an AtompipeError saying there is none.

    `-C/--dir` sets where the search STARTS, not where it ends: like git, the
    walk goes up until it finds a project marker (`.atompipe/project.json`, or a
    legacy `.atompipe/ledger.json`) and stops at the repository's `.git`, so
    running from `model/` or `inputs/cad/` hits the same project (S-64: any
    `.atompipe/` used to count, and `~/.atompipe/packs/` made all of `~` one).
    """
    return store.require_root(getattr(args, "dir", None))


def _lock(root: str) -> FileLock:
    """The build lock for `root`, as a context manager.

    Held around every command that writes. The failure it prevents is concrete:
    two runs that both load the ledger, both add verdicts and both save will lose
    one set entirely, with nothing anywhere saying so.
    """
    return FileLock(os.path.join(store.atompipe_dir(root), LOCK_NAME))


def _registry(root: str, ledger: Ledger, *,
              strict: bool = True) -> tuple[gates.Registry, list[str]]:
    """Load every gate this project can see. Returns `(registry, problems)`.

    Packs first (in `meta.packs` order, which is gate-id precedence), then the
    project's own `gates/`.

    **A fresh `gates.Registry` per command** (spec §3.5, cli:H6). This used to
    load into the module-level `gates.REGISTRY` on the argument that "a CLI
    process serves exactly one project" — which stopped being true the first
    time a test, a `site build` after a `check`, or an agent called `main` twice
    in one process: the second load found the first one's gates already
    registered and refused them ("already registered"), or handed the second
    project the first one's. The loaders serve a module whose bytes have not
    moved from their content-keyed cache and re-adopt its gates into whichever
    registry asks, so a fresh one costs a lookup, not a re-import.
    `gates.REGISTRY` stays what `@gate` decorates into outside a load.

    `strict=False` turns a broken pack into a *reported* problem instead of an
    exception, and only the readers use it. The distinction matters: `doctor`
    exists to tell you your pack is broken, so it must survive a broken pack;
    `check` must not, because a sweep that silently ran two packs out of three
    would publish an UNCLAIMED section that is an artefact of an import error
    rather than a statement about the design.
    """
    registry = gates.Registry()
    problems: list[str] = []
    for label, load in (
        ("packs", lambda: packs.load_all_gates(packs.installed(root, ledger=ledger),
                                               registry, root)),
        ("project gates", lambda: gates.load_project_gates(root, registry)),
    ):
        try:
            load()
        except AtompipeError as exc:
            if strict:
                raise
            problems.append(f"{label} did not load: {exc}")
    return registry, problems


def _projection(root: str, ledger: Ledger, *, entry: str | None = None) -> tuple[Any, dict | None]:
    """Load the model and build its projection, or `(None, None)` if there is none.

    A project with no `meta.model_entry` is a normal, early state — claims and
    evidence come before the model — so that case returns None rather than
    raising. A model entry that IS recorded and does not load is a different
    thing entirely and propagates: the model is the single source of truth, and a
    check run against a model that failed to import proves nothing about
    anything.
    """
    target = (entry or ledger.meta.model_entry or "").strip()
    if not target:
        return None, None
    model = modelio.load_model(root, target)
    return model, modelio.project(model)


def _projection_safe(root: str, ledger: Ledger) -> tuple[Any, dict | None, str]:
    """`_projection`, with the failure returned instead of raised.

    For `status` and `doctor`, which have to keep working on a project whose
    model is mid-edit — that is precisely when someone runs them.
    """
    try:
        model, projection = _projection(root, ledger)
    except AtompipeError as exc:
        return None, None, str(exc)
    return model, projection, ""


# --------------------------------------------------------------------------- #
# the one resolver, at the edge
# --------------------------------------------------------------------------- #
#: What slipped through before this section existed (S-28, cli:H3): the CLI kept
#: its own copy of "is this verdict current?" — one hash of the whole projection
#: against the last sweep's, in `_staleness` — and its own `_flat_params`, each
#: "kept byte-for-byte in sync" with a twin in `site.py` by a comment. Nine
#: readers then took `ledger.verdicts` as the truth, so a verdict was as current
#: as the last `check` had left the ledger, `--only` could freeze a PASS forever
#: (S-20), and `--no-record` read a fresh pass as STALE (S-32). Now there is one
#: judgement, `verdicts.resolve`, one flattening, `modelio.flat_params`, and one
#: place every command meets them: `_resolved`.


def _param_gates(ledger: Ledger, read_sets: Mapping[str, Iterable[tuple]],
                 registry: gates.Registry | None) -> dict[str, list[str]]:
    """`{param name: [gate ids]}` — which registered gates read each parameter
    when they last executed (`verdicts.last_read_sets`). Feeds `Param.gates` and
    `why`, never rho.

    Why the field is filled at all: `Param.gates` — "which gate protects this
    number", the fourth thing rule 3 asks a constant to carry — was declared and
    never assigned, so `atompipe why <param>` told every reader "GATES (0) —
    none: no gate would notice if this value went wrong" about parameters three
    gates read on every sweep; an agent that believed it went off to write a gate
    the project already had.

    A parameter counts as read when its name is one of the first two keys of a
    recorded path: `ctx.params["thickness"]` and `ctx.params["config"]
    ["thickness"]` are the two spellings gates use (the reference project uses
    the second for seven of its reads). Deeper keys are not followed: a sourcing
    gate walking a BOM would otherwise attribute itself to every line item that
    shares a parameter's name — a false positive in the generous direction.
    Names that are not ledger parameters are dropped: a gate asking for
    `span_mm` on a model with no such field says the gate wants it, not that the
    project has it.

    What slipped through before (S-30): the attribution was recorded by a wrapper
    on the sweep's `ctx.params`, so a gate that did not execute — its tool
    missing here — recorded nothing, and every full sweep erased the parameters
    it protects. A read set comes from the gate's last EXECUTED entry now, and a
    skip leaves it where it was. Only registered gates are named: a gate this
    project cannot load protects nothing here.

    The honest limit, stated because the field reads stronger than it is: this is
    a DIRECT read. `bracket.deflection` reads the derived `deflection`, which
    protects `arm_length` in physical fact, but a read set names the key read,
    not what `build()` computed it from — so `arm_length` lists no gate. An empty
    `Param.gates` means "no gate reads this value by name", weaker than "nothing
    would notice if it changed" (which gates go stale when it moves is what
    `status` says, from rho).
    """
    registered = set(registry.ids()) if registry is not None else set(read_sets)
    known = {param.name for param in ledger.params}
    found: dict[str, list[str]] = {}
    for gate_id in sorted(read_sets):
        if gate_id not in registered:
            continue
        names = {part for path in read_sets[gate_id] for part in tuple(path)[:2]
                 if isinstance(part, str)}
        for name in sorted(names & known):
            found.setdefault(name, []).append(gate_id)
    return found


def _swept(resolution: verdicts.Resolution, result: verdicts.SweepResult,
           registry: gates.Registry) -> verdicts.Resolution:
    """`resolution` with this sweep's rows standing in for the gates it selected.

    A row the sweep produced is current by construction — it ran, was served
    from a Fresh entry, or was refused — while `resolve` after the sweep would
    re-judge it from disk: under `--no-record` nothing reached disk, so the run's
    own results would be invisible, and an entry with an opaque channel (omc's
    subprocess) reads Unknown the instant it is written. So `check` judges what
    it just did from what it did, and every gate it did not select — above the
    ceiling, outside `--only` — from the resolver, stale or not (S-20: those can
    go stale now, because nothing but their inputs decides it).
    """
    rows = {row.verdict.gate: row for row in result.rows}
    resolved = {verdict.gate: verdict for verdict in resolution.verdicts}
    order = [spec.id for spec in registry.specs()]
    merged: list[Verdict] = []
    for gate_id in order:
        if gate_id in rows:
            merged.append(rows[gate_id].verdict)
        elif gate_id in resolved:
            merged.append(resolved[gate_id])
    registered = set(order)
    merged += [v for v in resolution.verdicts if v.gate not in registered]
    return dataclasses.replace(
        resolution, verdicts=merged,
        stale_gates=frozenset(g for g in resolution.stale_gates if g not in rows))


def _resolved(root: str, ledger: Ledger, registry: gates.Registry | None,
              projection: dict | None, model_error: str, *, now: str,
              model: Any = None, sweep: verdicts.SweepResult | None = None
              ) -> tuple[Ledger, verdicts.Resolution]:
    """`(view, resolution)`: the ledger as every reader must show it.

    `resolution` is `verdicts.resolve`'s — the ONE effective-verdict producer
    (R-5): per registered gate its effective verdict (served from a Fresh,
    admitted entry; skipped where its tool is missing; errored where a crash
    superseded it; stale with its reasons otherwise), then orphans. It never runs
    a gate or a fixture. `sweep`, from `check` only, lays that sweep's own rows
    over the gates it selected (`_swept`).

    `view` is `ledger` with three in-memory fields filled, and nothing else:
    `verdicts` from the resolution; `Claim.gates` from `claims.effective_gates`
    (registry coverage over the records' cached opinion, so `claim show` stops
    reading UNCLAIMED for a claim three gates cover, cli:H5); `Param.gates` from
    `last_read_sets` (S-30). `claims`, `report`, `site` and `decisions` stay pure
    functions of a `Ledger`, and every reader — `status`, `claim list/show`,
    `report`, `site build`, `gate show`, `why`, `doctor`, `check` and its JUnit —
    renders this one.

    **The view is never saved.** It holds cache verdicts, and coverage and read
    sets the records do not own; a whole-ledger save of it would write them into
    the tracked ledger (cli:H3). `tests/test_check_cache.py` walks this file's AST
    and refuses any `store.save(` argument that flows from here. `model_error`
    joins the resolver's "the model does not load" reason; `now` is the
    command's one clock stamp.
    """
    resolution = verdicts.resolve(root, registry, projection, ledger,
                                  model_error=model_error, now=now, model=model)
    if sweep is not None and registry is not None:
        resolution = _swept(resolution, sweep, registry)
    cover = claims.effective_gates(ledger, registry)
    reads = _param_gates(ledger, resolution.read_sets, registry)
    view = dataclasses.replace(
        ledger,
        verdicts=list(resolution.verdicts),
        claims=[dataclasses.replace(claim, gates=list(cover.get(claim.id, [])))
                for claim in ledger.claims],
        params=[dataclasses.replace(param, gates=list(reads.get(param.name, [])))
                for param in ledger.params],
    )
    return view, resolution


def _stale_summary(resolution: verdicts.Resolution) -> str:
    """`gate: reason; gate: reason` for every stale gate, in the resolution's
    order — the `stale_reason` a JSON reader gets beside `stale_gates`. `""` when
    nothing is stale: what it replaced said "unchanged since the last sweep",
    which was one project-wide hash's opinion, not a fact about any gate."""
    named = [(gid, row.stale_reason) for gid, row in resolution.rows.items()
             if gid in resolution.stale_gates]
    named += [(gid, "") for gid in sorted(resolution.stale_gates - {g for g, _ in named})]
    return "; ".join(f"{gid}: {why}" if why else gid for gid, why in named)


def _stale_gate_list(resolution: verdicts.Resolution) -> list[str]:
    """The stale gates, in the resolution's order (then any without a row)."""
    listed = [gid for gid in resolution.rows if gid in resolution.stale_gates]
    return listed + sorted(resolution.stale_gates - set(listed))


def _seconds_between(earlier: str, later: str) -> float | None:
    """Seconds from one atompipe timestamp to another, or None if either does
    not parse — an age nobody can compute is unknown, never 0."""
    try:
        a = datetime.strptime(earlier, "%Y-%m-%dT%H:%M:%SZ")
        b = datetime.strptime(later, "%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError):
        return None
    return (b - a).total_seconds()


def _last_check(root: str) -> dict:
    """`.atompipe/cache/last_check.json` as `verdicts.write_last_check` left it,
    or `{}` when there is none or it cannot be read. For `status`'s `last check:`
    line only: `check` never reads it (it would be trusting its own summary of a
    previous run), and nothing decides a verdict from it. The path is spelled
    from the writer's own constants, so the two can never disagree about where
    the file lives."""
    path = os.path.join(store.atompipe_dir(root), verdicts._CACHE_DIR, verdicts._LAST_CHECK)
    try:
        data = read_json(path, None)
    except AtompipeError:
        return {}
    return data if isinstance(data, dict) else {}


#: Parameter sync lives in `modelio.sync_params` and NOWHERE ELSE.
#:
#: There used to be a second implementation right here, and `cmd_check` called
#: both of them twelve lines apart. This one rebuilt `ledger.params` from the
#: model and carried over only `grounded_by`, `gates`, `tags` and `changed_in`,
#: so every `atompipe check` silently deleted `Param.rejected` — the record of
#: what was TRIED AND LOST, which rule 3 calls the highest-value field in the
#: system — and `Param.source` along with it. A project could accumulate a year
#: of rejected alternatives in the ledger and have the inner-loop command wipe
#: them on the next run, with nothing printed and nothing to diff against.
#:
#: `modelio.sync_params` merges the other way round — it keeps the ledger's
#: whole record and replaces only value/units/rationale/derived_from, the four
#: fields the model actually owns — so it is the only one that survives. If a
#: merge is ever needed again, extend that function; do not write a second one
#: here, because two merges in one process will disagree and the destructive one
#: always runs last.


def _link_grounding(ledger: Ledger) -> list[str]:
    """Write the back-reference an extraction implies. Returns names that match nothing.

    `Extraction.grounds` points outward ("this photo grounds `hull_beam`"), and
    `artifacts.grounding` inverts that on demand — but `Param.grounded_by` and
    `Claim.grounded_by` are the *stored* form of the same edge, and
    `decisions.why` reads only the stored one. Without this, an agent that
    ingested a datasheet, extracted the figure and grounded a parameter on it
    would still be told by `atompipe why` that the parameter is "asserted, not
    evidenced" — the exact opposite of what happened.

    Idempotent (ids are added once) and additive only: an edge a human declared
    by hand is never removed here, because this function cannot tell a
    hand-declared edge from a stale one and guessing wrong deletes provenance.

    Names that match no parameter and no claim are returned rather than dropped.
    They are usually an ordering artefact — the extraction was recorded before
    the model declared the parameter — and they resolve themselves on the next
    `check`. A typo looks the same from here, so the caller says so out loud.
    """
    from_extractions = artifacts.grounding(ledger, include_declared=False)
    unmatched: list[str] = []
    for name, artifact_ids in from_extractions.items():
        param = ledger.param(name)
        claim = ledger.claim(name)
        target = param or claim
        if target is None:
            unmatched.append(name)
            continue
        current = list(target.grounded_by or [])
        for artifact_id in artifact_ids:
            if artifact_id not in current:
                current.append(artifact_id)
        target.grounded_by = current
    return sorted(unmatched)


def _refresh_coverage(ledger: Ledger, registry: gates.Registry) -> None:
    """Write live gate coverage into `claim.gates`. Only `check` may call this.

    `claim.gates` is a cache of a fact the registry owns, and `claims.py` says so
    in as many words. The cache still has to exist, because the things that read
    a ledger WITHOUT a registry — `decisions.why` above all — otherwise report
    "no gate can settle it" for a claim three gates are covering. An agent that
    trusts that sentence goes off to install a solver the project already has.

    Refreshed only in `check`, and refreshed by REPLACEMENT rather than union,
    because `check` is the one command that has loaded every pack the ledger
    declares (`_registry(strict=True)` refuses to proceed otherwise). So the
    coverage it computes is the project's complete gate set at that moment, and
    a stale id left over from a pack that was removed should disappear rather
    than keep a dead gate's name on a claim forever.
    """
    live = claims.coverage(ledger, registry)
    for claim in ledger.claims:
        claim.gates = live.get(claim.id, [])


def _skip_digest(skipped: list[Verdict], *, width: int = 96) -> list[str]:
    """Collapse a wall of `[skip]` lines into one line per distinct REASON.

    `atompipe check` is the inner-loop command, and once a few packs are
    installed it was measured at 37 lines carrying seven informative verdicts:
    26 skip lines repeating five reasons verbatim, because every gate in a pack
    skips for the same missing field. An agent reading that scrolls past the one
    FAIL it ran the command for. The reason is the actionable half ("add
    `volume_mm3` to the model"), and it is identical across the group, so it is
    printed once with the gate ids behind it.

    Sorted by group size descending, then by reason, so the biggest single thing
    to fix is the first line you read. The reason text is NOT truncated: it names
    the field to add, and a digest that drops that is a digest that sends the
    reader back to the un-collapsed output.

    A reason with exactly one gate behind it renders as the ordinary verdict
    line. Grouping a group of one cost a second line to say "1 gate" and made
    the digest LONGER than the wall it replaced — measured at 43 lines against
    the original 37 before this branch existed.
    """
    groups: dict[str, list[str]] = {}
    for verdict in skipped:
        reason = (verdict.skip_reason or verdict.detail
                  or verdict.error or "no reason recorded").strip()
        groups.setdefault(reason, []).append(verdict.gate)

    lines: list[str] = []
    for reason, gate_ids in sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        if len(gate_ids) == 1:
            lines.append(f"{_tag('skip')} {gate_ids[0]} : {reason}")
            continue
        lines.append(f"{_tag('skip')} {len(gate_ids)} gates — {reason}")
        lines.append(textwrap.fill(", ".join(gate_ids), width=width,
                                   initial_indent="       ", subsequent_indent="       ",
                                   break_long_words=False, break_on_hyphens=False))
    return lines


def _context(root: str, ledger: Ledger, model: Any, projection: dict | None,
             tier: int, *, quiet: bool = False) -> gates.GateContext:
    """Build the one argument every gate receives.

    `model` is the `modelio.LoadedModel`, not the bare module: it carries the
    module, the resolved config, the entry path and the params, and a gate that
    only wants the module reaches it as `ctx.model.module`. Handing over the
    richer object costs nothing and saves the gate from re-deriving what the
    loader already resolved.

    `log` goes to stderr so a gate's progress chatter never lands inside `--json`
    output, and is silenced entirely under `--json` because a caller parsing
    stdout is usually not reading stderr either.

    `params` is `modelio.flat_params(projection)` — the one flattening, the same
    one `verdicts.freshness` recomputes a gate's reads against, so a value is
    compared in exactly the shape a gate was handed it (S-28). A plain dict:
    `gates.run_gate` hands each gate its own traced, read-only view of it, and
    that trace — not a wrapper here — is what records which parameters a gate
    read. `extra` starts empty; each gate gets its own copy.
    """
    params, conflicts = modelio.flat_params(projection)
    for conflict in conflicts:
        _warn(f"warning: model and build() disagree on {conflict} — "
              f"gates read the config value")
    # A DEFENSIVE COPY, not the live ledger. Gate code is third-party: a pack is an
    # ordinary directory anyone can drop in, and `cmd_check` saves the ledger after
    # the sweep. Handing over the real object let a gate append its own Verdict —
    # for a gate that never ran — or clear a critical claim, and the save wrote it
    # straight to disk. The only writes that reach the ledger are the verdicts
    # `run_all` RETURNS, which `run_gate` has already stamped with the true gate id,
    # tier and claims, so a gate cannot forge a row for anything but itself.
    #
    # Gates legitimately need to READ the ledger (a sourcing gate reads the BOM
    # claims, a report gate reads the decisions), so it stays available — just not
    # as a writable handle on the thing about to be persisted.
    return gates.GateContext(
        root=root,
        ledger=Ledger.from_dict(ledger.to_dict()),
        model=model,
        params=params,
        out_dir=store.out_dir(root),
        tier=int(tier),
        log=(lambda message: None) if quiet else (lambda message: _warn(f"    {message}")),
        extra={},
    )


def _verdict_row(verdict: Verdict, *, cached: bool | None = None, fresh: bool | None = None,
                 stale_reason: str = "", executed: bool = True) -> dict[str, Any]:
    """One verdict as JSON. `ok` is included because `passed` alone is not the answer.

    `passed` is True on a verdict that was skipped or errored only if a gate set
    it that way, and consumers reach for the obvious field. `ok` is
    `passed and not skipped and not error` — the single field that answers "did
    this prove anything", spelled out so nobody downstream has to reimplement the
    three-way distinction and get it wrong in the generous direction.

    Optional fields that are empty are OMITTED. `atompipe check --json` on a
    project with five packs installed measured 20,207 characters, most of it
    `"detail": "", "error": "", "evidence": [], "skip_reason": "", "units": ""`
    repeated once per gate — thousands of tokens of an agent's context spent
    carrying the absence of information. An absent key and an empty string say
    the same thing to a reader and one of them is free.

    `measured` and `limit` are dropped only when they are None, never when they
    are falsy. A gate that measured exactly 0.0 measured something, and dropping
    that row would turn a real reading into "this gate reported no number" —
    the generous-direction misread this file spends most of its comments
    refusing.

    From 1.2 a row says where it came from (spec §3.13): `cached` (a cache
    entry's verdict, as recorded) and `fresh` (a pass or fail keyed at the
    current inputs) when the caller knows, `stale_reason` when it is not
    current, `rho` when the verdict has one. `duration_s` and `cpu_s` only when
    `executed`: a cached row replaying the cost of the run that wrote it would be
    a measurement of nothing, and a latency reader would average it in
    (cli:H12, `CostIsKept`) — the cost lives in obs.
    """
    row = verdict.to_dict()
    for key in ("detail", "error", "evidence", "skip_reason", "units", "rho"):
        if not row.get(key):
            row.pop(key, None)
    for key in ("measured", "limit"):
        if row.get(key) is None:
            row.pop(key, None)
    if executed:
        # 4dp is ~0.1 ms. A tier-0 gate reports `1.6689300537109375e-05` otherwise,
        # which is 22 characters saying "instant" in the least readable way available.
        row["duration_s"] = round(float(row.get("duration_s") or 0.0), 4)
        row["cpu_s"] = round(float(row.get("cpu_s") or 0.0), 4)
    else:
        row.pop("duration_s", None)
        row.pop("cpu_s", None)
    if cached is not None:
        row["cached"] = bool(cached)
    if fresh is not None:
        row["fresh"] = bool(fresh)
    if stale_reason:
        row["stale_reason"] = stale_reason
    row["ok"] = verdict.ok
    # Set by hand, like `ok`: `to_dict` serialises dataclass fields and `outcome`
    # is a property, so without this line it is silently missing. It is the one
    # four-way answer — "pass" | "fail" | "error" | "skipped" — that the tag and
    # the claim status are read from, so a consumer never re-derives it from the
    # three flags and gets the precedence wrong (PLAN R-5).
    row["outcome"] = verdict.outcome
    return row


def _resolved_row(verdict: Verdict, resolution: verdicts.Resolution) -> dict[str, Any]:
    """A reader's row: `_verdict_row` with the resolution's `cached`, `fresh`
    and `stale_reason` for that gate. A reader executed nothing, so no row it
    prints carries a duration."""
    found = resolution.rows.get(verdict.gate)
    return _verdict_row(verdict, cached=bool(found and found.cached),
                        fresh=bool(found and found.fresh),
                        stale_reason=found.stale_reason if found else "", executed=False)


# --------------------------------------------------------------------------- #
# init
# --------------------------------------------------------------------------- #
def cmd_init(args: argparse.Namespace) -> int:
    """Create the project layout, the starter ledger, and the next three steps.

    The last part is not decoration. `init` that prints "initialised." leaves a
    new user staring at eight empty directories with no idea which one is theirs,
    and the most common outcome is a project whose model gets written before any
    evidence is gathered — which is how numbers arrive with no provenance and
    stay that way. The three steps are printed in the order that produces a
    project with grounds: ask for evidence, write the claims it supports, then
    the model.
    """
    root = os.path.abspath(getattr(args, "dir", None) or os.getcwd())
    name = (args.name or os.path.basename(root.rstrip(os.sep)) or "project").strip()
    meta = ProjectMeta(
        name=name,
        summary=(args.summary or "").strip(),
        created=utcnow_iso(),
        revision=(args.revision or "v0.1").strip(),
        model_entry=(args.model or "").strip(),
        packs=_collect(args.pack),
        spine_version=__version__,
    )
    ledger = store.init(root, meta)
    paths = store.project_paths(root)

    if args.json:
        _dump({
            "root": root,
            "ledger": rel(paths["ledger"], root),
            "meta": ledger.meta.to_dict(),
            "next": [
                "atompipe ask",
                "atompipe claim add --statement ... --quantity ... --cmp '<=' --limit ...",
                "atompipe model --set-entry model/<thing>.py",
            ],
        })
        return 0

    _say(f"initialised atompipe project {name!r} at {root}")
    _say(f"  {rel(paths['ledger'], root):<24} the whole project state — claims, evidence,"
         f" decisions")
    _say(f"  {'inputs/':<24} evidence you put in by hand ({len(store.INPUT_BUCKETS)} buckets)")
    _say(f"  {'model/':<24} the parametric model: the only source of truth")
    _say(f"  {'docs/':<24} generated readiness report and decision log")
    _say("")
    _say("next, in this order:")
    _say("  1. atompipe ask                 — what evidence to ask for, then")
    _say("     atompipe ingest <files> --kind sketch --desc '...'")
    _say("     atompipe extract <id> --what '...' --grounds <param>   (an artifact with")
    _say("     no extraction is decoration)")
    _say("  2. atompipe claim add --statement 'floats with the full payload at <=60% draft' \\")
    _say("       --quantity 'draft fraction' --cmp '<=' --limit 0.6 --units ''")
    _say("  3. write model/<thing>.py — a dataclass CONFIG plus build(config) -> dict —")
    _say("     then: atompipe model --set-entry model/<thing>.py && atompipe check")
    return 0


# --------------------------------------------------------------------------- #
# status
# --------------------------------------------------------------------------- #
#: An instrument note as `verdicts.resolve` words it (`<gate> — recorded under
#: <module> <a>; here <b>`): the one kind of resolution note `status` prints one
#: line each. The others — opaque channels, defining-file digests, hand-edited
#: entries — are `doctor`'s rows, where each says what to do about it.
_INSTRUMENT_NOTE = re.compile(r"^\S+ — recorded under \S+ \S+; here \S+$")

#: A pending admission's reason as `verdicts.admission_state` words it; the
#: files are read back out so several pending controls fold into one `note:`.
_PENDING_REASON = re.compile(r"^control inputs moved \((?P<files>.*)\); the next check re-verifies$")


def _never_run(resolution: verdicts.Resolution, registry: gates.Registry | None) -> list[str]:
    """Registered gates with nothing to show: no row, or only an availability
    skip over no entry. A remembered crash is not "never run" (S-68)."""
    ids = registry.ids() if registry is not None else []
    out = []
    for gate_id in ids:
        row = resolution.rows.get(gate_id)
        if row is None or (row.state == "never" and not any(
                str(note).startswith("remembered ") for note in row.notes)):
            out.append(gate_id)
    return out


def _stale_lines(resolution: verdicts.Resolution, registry: gates.Registry | None, *,
                 model_error: str = "") -> list[str]:
    """`status`'s `stale:` block (spec §3.13): one line per stale gate with its
    reasons, continuation lines indented under `stale: `, and the counts on the
    last — `(N checks current[, n never run])`, a check being current when its
    verdict is a Fresh entry whose control is admitted or pending. `stale: none`
    with the counts when nothing is stale.

    One line per gate, always. What slipped through while wiring it: with the
    model broken, the resolver's reason for every gate that reads it carries the
    whole import traceback (`the model does not load: <error>`), and the block
    printed it once per gate — twenty lines of the same traceback between the
    reader and the counts. The error is `model:`'s line, printed once below."""
    current = sum(1 for row in resolution.rows.values() if row.fresh)
    never = _never_run(resolution, registry)
    counts = f"   ({current} checks current" + (f", {len(never)} never run" if never else "") + ")"
    stale = _stale_gate_list(resolution)
    if not stale:
        return [f"stale: none{counts}"]
    lines = []
    for index, gate_id in enumerate(stale):
        row = resolution.rows.get(gate_id)
        why = (row.stale_reason if row is not None else "") or "not current"
        if model_error:
            why = why.replace(f": {model_error}", "")
        why = (why.splitlines() or ["not current"])[0]
        lines.append(f"{'stale: ' if index == 0 else '       '}{gate_id} — {why}")
    lines[-1] += counts
    return lines


def _note_lines(resolution: verdicts.Resolution) -> list[str]:
    """`status`'s `note:` lines: one per instrument mismatch, then at most one
    for every control pending re-verification, its moved files merged."""
    lines = [f"note: {note}" for note in resolution.notes if _INSTRUMENT_NOTE.fullmatch(note)]
    pending = [row for row in resolution.rows.values()
               if row.admission is not None and row.admission.state == "pending"]
    if pending:
        files: list[str] = []
        for row in pending:
            match = _PENDING_REASON.fullmatch(row.admission.reason or "")
            for name in (match.group("files").split(", ") if match else ()):
                if name and name not in files:
                    files.append(name)
        moved = ", ".join(sorted(files)) or "fixture code"
        lines.append(f"note: {len(pending)} control(s) pending — inputs moved ({moved}); "
                     f"the next check re-verifies")
    return lines


def _freshness_rows(resolution: verdicts.Resolution,
                    registry: gates.Registry | None) -> dict[str, dict[str, Any]]:
    """`status --json`'s `freshness`: per registered gate (then any orphan with a
    row), its cache state, why it is not current, how its control stands and the
    resolver's notes. `state` is `"never"` for a gate with no row."""
    out: dict[str, dict[str, Any]] = {}
    ids = list(registry.ids()) if registry is not None else []
    ids += [gid for gid in resolution.rows if gid not in set(ids)]
    for gate_id in ids:
        row = resolution.rows.get(gate_id)
        if row is None:
            out[gate_id] = {"state": "never", "reasons": [], "admission": None, "notes": []}
            continue
        out[gate_id] = {
            "state": row.state,
            "reasons": [row.stale_reason] if row.stale_reason else [],
            "admission": row.admission.state if row.admission is not None else None,
            "notes": [str(note) for note in row.notes],
        }
    return out


def cmd_status(args: argparse.Namespace) -> int:
    """The one-screen answer to "where is this project".

    `report.render_terminal` does the claim/gap/gate half, from `_resolved`'s
    view. Then, in a fixed order (spec §3.13), the facts that live outside the
    readiness report, each with its source:

    * `stale:` — each gate whose verdict is not current, with what moved
      (`config.bed_xy 220.0 -> 250.0`), and on the last line how many checks are
      current and how many never ran; `stale: none` when nothing is stale. What
      it replaced said "model <hash> -> <hash>": that SOMETHING moved, never
      which check it touched (M11.7).
    * `last check:` — when `check` last swept the whole project
      (`last_check.json`), with its age; `never` before the first.
    * `note:` — an entry recorded under another library version (provenance,
      never staleness, Q1.3), and at most one line for controls whose fixture
      code moved since they were demonstrated (they count; the next check
      re-verifies them).
    * `model:` — only when the model does not load, because then no verdict
      that reads it is current and the reader must know why first.

    Then the packs, the site, the names (not counts) of unread evidence and
    undefended parameters, and load problems. It never runs a gate or a fixture,
    and never writes: it reads the cache (M11.11).

    Never fails on a broken pack or an unloadable model — both are reported as
    lines. `status` is what you run when something is wrong.
    """
    root = _root(args)
    ledger = store.load(root)
    registry, problems = _registry(root, ledger, strict=False)
    model, projection, model_error = _projection_safe(root, ledger)
    now = utcnow_iso()
    view, resolution = _resolved(root, ledger, registry, projection, model_error,
                                 now=now, model=model)
    stale_gates = resolution.stale_gates

    installed = packs.installed(root, ledger=ledger)
    available = packs.available(root)
    unread = artifacts.unextracted(view)
    undefended = [p.name for p in view.params if not (p.rationale or "").strip()]
    summary = claims.summarise(view, registry, stale_gates=stale_gates)
    resolved = claims.statuses(view, registry=registry, stale_gates=stale_gates)
    site_info = _site_state(root)
    last = _last_check(root)
    last_when = str(last.get("when") or "")
    last_age = _seconds_between(last_when, now) if last_when else None

    if args.json:
        _dump({
            "root": root,
            "meta": view.meta.to_dict(),
            "summary": summary,
            "claims": {cid: str(status) for cid, status in resolved.items()},
            "gaps": [need.to_dict() for need in claims.find_gaps(view, registry)],
            "stale": bool(stale_gates),
            "stale_reason": _stale_summary(resolution),
            "stale_gates": _stale_gate_list(resolution),
            "freshness": _freshness_rows(resolution, registry),
            "last_check": {"when": last_when or None, "age_s": last_age},
            "model": {
                "entry": view.meta.model_entry,
                "loaded": projection is not None,
                "error": model_error,
                "hash": modelio.model_hash(projection) if projection else "",
                "undocumented_params": undefended,
            },
            "packs": {"installed": installed, "available": available},
            "inputs": {
                "total": len(view.inputs),
                "unextracted": [a.id for a in unread],
            },
            "site": _site_brief(site_info),
            "problems": problems,
        })
        return 0

    sys.stdout.write(report.render_terminal(view, registry, stale_gates=stale_gates))
    for line in _stale_lines(resolution, registry, model_error=model_error):
        _say(line)
    if last_when:
        age = (f" ({human_duration(last_age)} ago)" if last_age is not None and last_age >= 0
               else " (in the future)" if last_age is not None else "")
        _say(f"last check: {last_when}{age}")
    else:
        _say("last check: never")
    for line in _note_lines(resolution):
        _say(line)
    if model_error:
        entry = view.meta.model_entry or "(none recorded)"
        _say(f"model: {entry} DOES NOT LOAD — {model_error.splitlines()[0]}")

    if installed:
        _say(f"packs: {', '.join(installed)} "
             f"({len(available)} available: {', '.join(available[:6]) or 'none'})")
    elif available:
        _say(f"packs: none installed; {len(available)} available "
             f"({', '.join(available[:6])}) — `atompipe packs add <name>`")

    # Mentioned only when the project has one: a line telling every project
    # without a site that it does not have a site is noise in the one command
    # that has to stay readable at a glance.
    if site_info["present"]:
        _say(_site_line(site_info))

    if unread:
        names = ", ".join(a.id for a in unread[:5])
        more = f", +{len(unread) - 5}" if len(unread) > 5 else ""
        _say(f"unread evidence: {names}{more} — `atompipe extract <id> --what ...`")
    if undefended:
        names = ", ".join(undefended[:5])
        more = f", +{len(undefended) - 5}" if len(undefended) > 5 else ""
        _say(f"undefended params: {names}{more} — no rationale recorded")
    for problem in problems:
        _say(f"{_tag('FAIL')} {problem}")
    return 0


# --------------------------------------------------------------------------- #
# --junit: unlinked first, written last
# --------------------------------------------------------------------------- #
class _JUnitDefault(str):
    """`--junit` given with no PATH: `report.JUNIT_DEFAULT`, under the project root.

    A `str` subclass so argparse can store it as the flag's `const` and the
    command can still tell it from a PATH the user typed, which resolves against
    the directory the command started in (`-C`, else the cwd) like any path
    typed at a shell — git's `-C` rule. *Rejected:* comparing the value to
    `JUNIT_DEFAULT` — a user who typed that exact string from a subdirectory
    meant the subdirectory; a second `--junit-path` flag (spec §3.14) — two flags
    for one file, and the bare one would still swallow a gate id.
    """


_JUNIT_DEFAULT = _JUnitDefault(report.JUNIT_DEFAULT)

#: The suffix every `--junit PATH` must carry, and the message when it does not.
#: What slipped through while designing the flag (cli:H7): `--junit` takes an
#: OPTIONAL value and `gate selftest` takes gate ids positionally, so
#: `gate selftest --junit bracket.deflection` parsed the gate id as the report's
#: path, ran every control instead of the one named, and wrote XML to a file
#: called `bracket.deflection`. No gate id ends in `.xml`; every JUnit consumer
#: expects it to. *Rejected:* `--junit=PATH` only (argparse cannot require the
#: `=`); a separate flag for the path (above).
_JUNIT_SUFFIX = ".xml"
_JUNIT_RULE = "--junit takes a path ending in .xml; put gate ids before it"


def _junit_arg(args: argparse.Namespace) -> str | None:
    """The `--junit` value, refused unless it ends in `.xml`. Called FIRST.

    First, before anything reads the project: a refused value must stop the
    command before it has run a gate or removed a file.
    """
    value = getattr(args, "junit", None)
    if value is None:
        return None
    if not str(value).endswith(_JUNIT_SUFFIX):
        raise AtompipeError(_JUNIT_RULE)
    return value


def _start(args: argparse.Namespace) -> str:
    """The directory this command started in: `-C`, else the cwd."""
    return os.path.abspath(getattr(args, "dir", None) or os.getcwd())


def _junit_unlink(args: argparse.Namespace, value: str | None, *,
                  root: str | None) -> str | None:
    """Resolve the `--junit` target and remove whatever is there. Returns its path.

    Called at the TOP of the command, before `_registry` and `_projection`, both
    of which exit 2 on a broken pack or model. What slipped through while
    designing this (cli:H7): "unlinked when the sweep starts" put the unlink after
    them, so a crash left the previous run's all-green `junit.xml` on disk beside
    a job that exited 2 — and a CI system renders the file, not the exit code. A
    run that ends early now leaves no report at all, which every JUnit consumer
    reads as missing, never as green.

    The default resolves against `root` (the project, when there is one), an
    explicit PATH against `_start`. Something at the path that cannot be removed
    — a directory, a read-only parent — is refused before anything runs, rather
    than discovered after the sweep, beside results nobody can then read.
    """
    if value is None:
        return None
    base = (root or _start(args)) if isinstance(value, _JUnitDefault) else _start(args)
    path = os.path.abspath(os.path.join(base, value))
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise AtompipeError(
            f"--junit {value}: cannot remove what is at {path} "
            f"({exc.strerror or exc}); a report that cannot be replaced would be "
            f"read as this run's") from exc
    return path


def _junit_write(path: str | None, render: Callable[[], str]) -> str | None:
    """Write the report at the command's single exit; returns the path written.

    `render` is called here, after the exit code exists, so the XML can only be
    made from the code the command returns (spec §3.14: one exit code, one write).
    Atomic (`atomic_write_text`): a reader never sees half a file.
    """
    if path is None:
        return None
    atomic_write_text(path, render())
    return path


# --------------------------------------------------------------------------- #
# check
# --------------------------------------------------------------------------- #
#: Where a cached row's `cached` mark starts: `f"{line:<77} cached"` puts it at
#: column 79, the transcript's column (spec §7). Readable in 80 columns, and the
#: shape test pins `\s+cached$`, never the padding, so a row longer than the
#: column still reads as cached. *Rejected:* a tab (renders per terminal); a
#: changed tag such as `[fail]` for a cached FAIL (breaks every grep for `[FAIL]`).
_CACHED_COLUMN = 77


def _check_row_line(row: verdicts.SweepRow) -> str | None:
    """How one sweep row streams, or None when it does not.

    An executed row, a refusal (`not admitted`) and a cached row that did not
    pass each print; a cached pass does not — the inner loop is for what moved
    or what is wrong, and five unchanged `[ok  ]` lines between you and the FAIL
    you came for is the wall `_skip_digest` exists to collapse. Skips are that
    digest's, after the summary.
    """
    verdict = row.verdict
    if verdict.skipped:
        return None
    if row.cached:
        if verdict.ok:
            return None
        return f"{verdict.render():<{_CACHED_COLUMN}} cached"
    return verdict.render()


def _check_summary(rows: list[verdicts.SweepRow], counts: Mapping[str, int], tier: int) -> str:
    """`6 gates: 1 executed, 5 cached — 5 ok, 1 FAIL — tier 0` (spec §3.13).

    What left the line, and why: the elapsed time (a mostly-cached sweep takes
    no time worth reading, and `--json` keeps `duration_s`), and the model hash
    (one hash of the projection said THAT something moved; which check it
    touched is `status`'s `stale:` line now). FAIL, skipped and errored appear
    only when non-zero, as they always did."""
    outcomes = [row.verdict.outcome for row in rows]
    line = (f"{len(rows)} gates: {counts.get('executed', 0)} executed, "
            f"{counts.get('cached', 0)} cached — {outcomes.count('pass')} ok")
    for outcome, word in (("fail", "FAIL"), ("skipped", "skipped"), ("error", "errored")):
        if outcomes.count(outcome):
            line += f", {outcomes.count(outcome)} {word}"
    return f"{line} — tier {tier}"


def cmd_check(args: argparse.Namespace) -> int:
    """Run what moved, serve what did not, and exit non-zero while anything critical blocks.

    **Affected-only** (D-05). `verdicts.sweep` is the loop: per selected gate,
    in registration order, availability, then admission (the gate's negative
    control, run on a control-entry miss — S-05: a logger with a declared
    control produced PROVEN rows because nothing here ever ran a control), then
    the verdict cache (a Fresh entry is served, unless a crash at its inputs
    superseded it), then the run. Every selected gate gets a row — executed,
    cached, or refused — so `check --json` still lists `bracket.deflection` on a
    fresh clone whose first check is all cache hits (cli:H1).

    Exit 1 on a blocking critical claim is the point of the command. It is not
    "exit 1 if a gate failed" — an UNCLAIMED, PENDING or BLOCKED critical claim
    blocks too, because none of them is evidence and all of them are routinely
    read as "no news is good news". That is what makes this usable as a pre-spend
    gate and in CI. The claims are judged from `_resolved`'s view with this
    sweep's rows laid over the gates it selected (`_swept`).

    Flags, and what each writes:

    * `--force` re-runs every selected gate AND its control, cache or no cache
      (R-9): the inner loop may trust the committed cache, a money boundary
      re-proves it, and CI runs the bracket this way.
    * `--no-record` is a dry sweep: nothing under `.atompipe/` but gate scratch in
      `out/` — no cache or control entry, no obs, no remembered outcome, no
      `last_check.json`, and the ledger is not saved (S-32: it used to write the
      ledger anyway and read its own fresh passes as STALE, because the one
      global clock had not moved; there is no global clock now).
    * `--only` and `--tier` select as they always did; a filtered sweep writes
      its entries but no `last_check.json` — a partial sweep's summary would
      stand for the whole project's.
    * Otherwise, after the sweep: `verdicts.write_last_check`, then (1.2 only,
      while claims still live in the ledger) the RECORDS ledger is saved with
      `verdicts=[]` and `Param.gates` from `verdicts.last_read_sets` — never the
      view (PD-31, cli:H3). No run history, no `last_run` (S-89: every recorded
      check rewrote the tracked ledger and appended a tracked run file).

    The clock is stamped ONCE (`now`): obs, remembered outcomes,
    `last_check.json` and the JUnit report carry the same instant.

    `--junit [PATH]` writes the same judgement as JUnit XML (`report.render_junit`).
    The target is removed before anything can fail and written at the one exit,
    from the one exit code. What slipped through while designing it (cli:H7):
    this function returned from four places, and a report written at one of them
    carries a judgement the others never made — so the code is computed once,
    below, and every path out prints from it.
    """
    junit_arg = _junit_arg(args)
    junit = _junit_unlink(args, junit_arg,
                          root=store.find_root(getattr(args, "dir", None)))
    root = _root(args)
    tier = int(args.tier)
    only = list(args.only) if args.only else None
    record = not args.no_record
    force = bool(getattr(args, "force", False))
    now = utcnow_iso()

    with _lock(root):
        ledger = store.load(root)
        registry, _ = _registry(root, ledger, strict=True)
        model, projection = _projection(root, ledger)
        # Refresh the ledger's parameter records from the model before sweeping.
        # The model owns every value; the ledger owns the provenance accumulated
        # around it (rejected alternatives, grounding, which gates protect it).
        # In memory here; saved below only when this run records (1.2 — the
        # records move to files in 1.3 and check stops writing them).
        # ONE call, to `modelio.sync_params`: see the note above `_link_grounding`
        # for the second implementation that used to run here and the field it ate.
        if model is not None:
            modelio.sync_params(ledger, model)
        # After the sync, not before: a parameter that only just appeared in the
        # ledger is exactly the one whose evidence has been waiting to attach.
        _link_grounding(ledger)
        _refresh_coverage(ledger, registry)
        ctx = _context(root, ledger, model, projection, tier, quiet=args.json)

        if projection is None and registry.specs():
            _warn("warning: no model entry recorded — gates that read ctx.params "
                  "will error. `atompipe model --set-entry model/<thing>.py`")

        def _landed(row: verdicts.SweepRow) -> None:
            # `sweep` calls this the instant a gate's row exists, before the next
            # gate starts: a tier-2 sweep streams, it does not go quiet for minutes.
            line = None if args.json else _check_row_line(row)
            if line is not None:
                _say(line)

        started = time.perf_counter()
        result = verdicts.sweep(root, registry, ctx, projection=projection, ledger=ledger,
                                max_tier=tier, only=only, force=force, record=record,
                                now=now, on_row=_landed)
        elapsed = time.perf_counter() - started

        view, resolution = _resolved(root, ledger, registry, projection, "", now=now,
                                     model=model, sweep=result)
        if record:
            verdicts.write_last_check(root, result, resolution, now=now)
            # 1.2 only: the records still live in the ledger. Saved with no verdict
            # (they live in the cache) and with `Param.gates` from the entries'
            # read sets — read straight off the cache, never off the view, which is
            # a reader's and is never written (the AST rule in test_check_cache).
            read_sets = verdicts.last_read_sets(root)
            attributed = _param_gates(ledger, read_sets, registry)
            store.save(root, dataclasses.replace(
                ledger, verdicts=[],
                params=[dataclasses.replace(param, gates=attributed.get(param.name, []))
                        for param in ledger.params]))

        stale_gates = resolution.stale_gates
        blockers = claims.blocking(view, registry, stale_gates=stale_gates)
        summary = claims.summarise(view, registry, stale_gates=stale_gates)

    rows = list(result.rows)
    selected = {row.verdict.gate for row in rows}
    # Registered gates this sweep did not select (above the ceiling, outside
    # `--only`) that still have an effective verdict. An unregistered gate's row
    # is not "carried over" by anything: it is an orphan, stale by definition,
    # and `doctor` names it.
    registered = set(registry.ids())
    carried = [v for v in view.verdicts if v.gate not in selected and v.gate in registered]
    stale_reasons = {gid: row.stale_reason for gid, row in resolution.rows.items()
                     if gid in stale_gates}
    counts = {
        "ran": sum(1 for row in rows if row.verdict.ok),
        "failed": sum(1 for row in rows if row.verdict.outcome == "fail"),
        "skipped": sum(1 for row in rows if row.verdict.outcome == "skipped"),
        "errored": sum(1 for row in rows if row.verdict.outcome == "error"),
        "executed": int(result.counts.get("executed", 0)),
        "cached": int(result.counts.get("cached", 0)),
        "controls": {key: int(result.controls.get(key, 0))
                     for key in ("executed", "cached", "reverified")},
    }

    # A project with no claims has proven nothing, and this command's exit code is
    # the only part of it CI reads. It used to print "an empty ledger is not a
    # clean bill of health" and then return 0 — a message and a return code
    # disagreeing, with the machine believing the one that laundered. Zero
    # blocking claims out of zero claims is not readiness, so it is not a zero.
    ready = bool(view.claims) and not blockers
    # THE exit code. Every line below prints from it and the JUnit report is
    # rendered from it; nothing after this point decides anything.
    code = 0 if ready else 1

    spine = verdicts.spine_digest()
    written = _junit_write(junit, lambda: report.render_junit(
        view, [row.verdict for row in rows], registry, tier=tier, ready=ready,
        exit_code=code, when=now, not_run=result.not_run,
        cached={row.verdict.gate for row in rows if row.cached}, spine=spine,
        stale_gates=stale_gates))

    if args.json:
        _dump({
            "tier": tier,
            "only": only,
            "verdicts": [_verdict_row(row.verdict, cached=row.cached, fresh=row.fresh,
                                      stale_reason=row.stale_reason, executed=row.executed)
                         for row in rows],
            "counts": counts,
            "carried_over": [_resolved_row(v, resolution) for v in carried],
            "summary": summary,
            "blocking": [{"claim": claim.id, "status": str(status),
                          "statement": claim.statement} for claim, status in blockers],
            "ready": ready,
            "claims_recorded": len(view.claims),
            # Stale BEFORE the sweep (cli:H19): after a recorded full sweep
            # everything it touched is current by construction, so "stale" read
            # afterwards could only ever say False.
            "stale": bool(result.stale_before),
            "stale_reason": "; ".join(f"{gid}: {why}"
                                      for gid, why in result.stale_before.items()),
            # No run history to point at (S-89); kept as null so a reader that
            # looked for the key finds it, and finds nothing there (SF PD-13).
            "run": None,
            "model_hash": modelio.model_hash(projection) if projection else "",
            "duration_s": round(elapsed, 4),
            "spine": spine,
            "junit": written,
        })
        return code

    _say(_check_summary(rows, result.counts, tier))
    if counts["controls"]["executed"] or counts["controls"]["reverified"]:
        controls = counts["controls"]
        _say(f"controls: {controls['executed']} executed, {controls['cached']} cached, "
             f"{controls['reverified']} re-verified")

    # The skips, one line per distinct reason instead of one per gate. They come
    # after the summary and before the blockers on purpose: the summary already
    # carries the count, and the thing you must act on has to stay at the bottom
    # of the screen where the eye lands.
    for line in _skip_digest([row.verdict for row in rows if row.verdict.skipped]):
        _say(line)

    if carried:
        shown = ", ".join(v.gate for v in carried[:4])
        more = f", +{len(carried) - 4}" if len(carried) > 4 else ""
        stale_n = sum(1 for v in carried if v.gate in stale_gates)
        _say(f"note: {len(carried)} gate(s) outside this sweep keep their last verdict "
             f"({shown}{more})" + (f" — {stale_n} of them stale" if stale_n else ""))

    if not view.claims:
        # "ready" on a project that has never stated what must be true is the
        # laundering this whole tool exists to refuse: zero blocking claims
        # out of zero claims is not evidence of anything. Non-zero, so that the
        # exit code says the same thing this line says.
        _say("no claims recorded, so nothing was checked — an empty ledger is not a "
             "clean bill of health. `atompipe claim add --statement ...`")
    elif not blockers:
        _say("ready: no critical claim is blocking "
             "(physical and assumed claims are still listed in `atompipe report`)")
    else:
        _say(f"BLOCKING — {len(blockers)} critical claim(s) must not be spent against:")
        for claim, status in blockers:
            _say(_blocking_line(claim, status,
                                _blocking_reason(view, claim, status, stale=stale_reasons)))
    return code


def _blocking_line(claim: Claim, status: ClaimStatus, reason: str) -> str:
    """`[FAIL ] C1 <statement> — <reason>`: one blocking claim, as `check` prints it.

    The tag is `report.status_tag`, the one spelling `status`, `claim list` and the
    readiness report already use. What slipped through (S-69): this line built its
    tag from the first four letters of the status, so the same claim read
    `[fail]` here and `[FAIL ]` in `status`, and a claim with no gate read `[uncl]`
    here and `[gap  ]` there — two vocabularies one screen apart, and a reader who
    had to learn that they meant the same thing. *Rejected:* keeping `_tag`'s
    four-wide field so `check`'s lines align with its verdict lines above them:
    those are GATE outcomes (four states, `Verdict.render`), these are CLAIM
    statuses (ten), and squeezing ten into four is how `uncl` got invented.
    """
    return f"{report.status_tag(status)} {claim.id} {claim.statement} — {reason}"


def _blocking_reason(ledger: Ledger, claim: Claim, status: ClaimStatus, *,
                     stale: Mapping[str, str] | None = None) -> str:
    """The shortest true sentence about why one claim blocks.

    A failing gate's own detail beats any phrasing invented here: it carries the
    measured value and the limit, which is what the reader is about to go and
    change. Only when no verdict speaks does this fall back to naming the status.

    THE REASON MUST MATCH THE STATUS. Several gates can cover one claim, and the
    first non-passing one is not necessarily the one that set the status: a claim
    covered by a project gate that FAILED and a pack gate that SKIPPED for a
    missing parameter was reporting `[fail] C1 ... — pack.gate: the projection
    does not provide ...`, which sends the reader to look for a missing parameter
    when the real answer is that their part sags 0.7 mm. So a FAIL cites a gate
    that actually ran and failed, in preference to one that skipped or errored.

    That ranking used to live here, privately, and the fix above never reached
    `report._terminal_reason`, which kept citing the skip in `status` (S-68). It
    is `claims.explaining_verdict` now — ran-and-failed, then errored, then
    skipped — and both callers format it the same way, `gate : body`, the
    separator `Verdict.render` and `status` already use.

    `stale` is `{gate: why}` for the gates whose verdict is not current (the
    resolution's). A FAIL that is stale stays FAIL (D-08) and says so —
    `gate : body (stale: why)` — because the refutation was measured against
    inputs that have since moved, and the reader should know which before
    arguing with it. A STALE claim names its stale gates: what it replaced,
    "it passed against a model that has since moved", was the one sentence one
    project-wide hash could say, and false for a moved data file or an
    undemonstrated control. The UNCLAIMED reason is "no gate covers it" and stops
    there: the `gap --propose` suffix was advice in a column that states facts.
    """
    stale = dict(stale or {})
    verdict = claims.explaining_verdict(claim, ledger.verdicts)
    if verdict is not None:
        body = verdict.detail or verdict.error or verdict.skip_reason
        text = f"{verdict.gate} : {body}" if body else f"{verdict.gate} did not pass"
        if verdict.outcome == "fail" and stale.get(verdict.gate):
            text += f" (stale: {stale[verdict.gate]})"
        return text
    if status is ClaimStatus.UNCLAIMED:
        return "no gate covers it"
    if status is ClaimStatus.PENDING:
        return "its gates have never run"
    if status is ClaimStatus.BLOCKED:
        return "its gates could not run here (missing tooling)"
    if status is ClaimStatus.STALE:
        named = [f"{gate}: {stale[gate]}" for gate in (claim.gates or ()) if stale.get(gate)]
        if named:
            return "passed, but not current — " + "; ".join(named)
        return "passed, but not against the current inputs"
    return str(status)


# --------------------------------------------------------------------------- #
# ask / ingest / inputs / extract
# --------------------------------------------------------------------------- #
def cmd_ask(args: argparse.Namespace) -> int:
    """Print what evidence to request from the human, as questions to paste.

    This is the command an agent runs before it starts guessing numbers. The
    ordering and the cap come from `artifacts.requests_by_kind` — kinds with
    nothing at all first, breadth before depth, six by default because that is
    what a person answers in one sitting.

    The output is deliberately plain prose with a number in front: these lines
    get said to a human, and a taxonomy prefix ("sketch: ...") makes them read
    like a form. The bucket each answer lands in is printed after the question,
    for the agent, not for the person being asked.
    """
    root = _root(args)
    ledger = store.load(root)
    if args.kind:
        pairs = [(args.kind, prompt) for prompt in artifacts.prompts_for(args.kind)]
    else:
        pairs = artifacts.requests_by_kind(
            ledger, project_kind=args.about or ledger.meta.summary, limit=args.limit)

    if args.json:
        _dump({
            "requests": [{"kind": kind, "prompt": prompt,
                          "bucket": artifacts.bucket_for(kind)} for kind, prompt in pairs],
            "have": sorted({str(a.kind) for a in ledger.inputs}),
        })
        return 0

    if not pairs:
        _say("nothing to ask for: every artifact kind has enough evidence. "
             "`atompipe inputs --unextracted` for what arrived and was never read.")
        return 0

    _say("ask for these, in this order:")
    for index, (kind, prompt) in enumerate(pairs, start=1):
        bucket = artifacts.bucket_for(kind)
        where = f"  -> inputs/{bucket}/" if bucket else ""
        _say(f" {index}. {prompt}{where}")
    _say("")
    _say("then: atompipe ingest <file> --kind <kind> --desc '<what it shows>'")
    _say("and:  atompipe extract <artifact-id> --what '<what it says>' --grounds <param>")
    return 0


def cmd_ingest(args: argparse.Namespace) -> int:
    """Take one or more real files (or URLs) into the project as evidence.

    Every path is filed, hashed and registered; a `http(s)://` argument is
    registered as a link instead, with no fetch — the spine never touches the
    network.

    The reminder at the end is not filler. Ingesting is the half people do; the
    half they drop is `extract`, and an artifact with no extraction grounds
    nothing, proves nothing, and makes the project *look* evidenced in the file
    listing while not one parameter traces back to it.
    """
    root = _root(args)
    when = utcnow_iso()
    landed: list[Any] = []

    with _lock(root):
        ledger = store.load(root)
        for source in args.paths:
            if source.startswith(("http://", "https://")):
                artifact = artifacts.ingest_link(
                    root, ledger, source, description=args.desc,
                    kind=args.kind or ArtifactKind.LINK, when=when)
            else:
                artifact = artifacts.ingest(
                    root, ledger, source, kind=args.kind, description=args.desc,
                    when=when, copy=not args.no_copy, licence=args.licence,
                    note=args.note)
            landed.append(artifact)
        store.save(root, ledger)

    if args.json:
        _dump({"ingested": [a.to_dict() for a in landed],
               "inputs_hash": artifacts.inputs_hash(ledger)})
        return 0

    for artifact in landed:
        where = artifact.path or artifact.url
        size = f"  {human_bytes(artifact.bytes)}" if artifact.bytes else ""
        _say(f"{artifact.id:<20} {str(artifact.kind):<12} {where}{size}")
    _say(f"{len(landed)} artifact(s) registered. An artifact with no extraction is decoration:")
    for artifact in landed[:3]:
        _say(f"  atompipe extract {artifact.id} --what '<what it tells you>' "
             f"--grounds <param-or-claim>")
    return 0


def cmd_inputs(args: argparse.Namespace) -> int:
    """List ingested evidence; `--unextracted` lists only the decorations.

    The `[!]` marker on an unread artifact is the whole reason this listing
    exists as its own command rather than a line in `status`: a project with
    twelve files and two extractions looks grounded from the outside and is not.
    """
    root = _root(args)
    ledger = store.load(root)
    rows = artifacts.unextracted(ledger) if args.unextracted else list(ledger.inputs)
    if args.kind:
        rows = [a for a in rows if str(a.kind) == args.kind]

    if args.json:
        _dump({"inputs": [a.to_dict() for a in rows],
               "total": len(ledger.inputs),
               "unextracted": len(artifacts.unextracted(ledger)),
               "grounding": artifacts.grounding(ledger)})
        return 0

    if not rows:
        _say("no ingested artifacts match" if ledger.inputs
             else "no evidence ingested yet — `atompipe ask` for what to request")
        return 0

    for artifact in rows:
        mark = "   " if artifact.extractions else "[!]"
        where = artifact.path or artifact.url
        note = (f"{len(artifact.extractions)} extraction(s)" if artifact.extractions
                else "NEVER READ")
        _say(f"{mark} {artifact.id:<20} {str(artifact.kind):<12} {where:<40} {note}")
    unread = [a for a in rows if not a.extractions]
    if unread:
        _say(f"{len(unread)} artifact(s) nobody read anything out of. Evidence nobody "
             f"extracted from is decoration.")
    return 0


def cmd_extract(args: argparse.Namespace) -> int:
    """Record what was actually read out of an artifact, and what that grounds.

    This is the step that converts a photograph into provenance: after it,
    `atompipe why <param>` can answer "what is this number standing on?" with a
    file, a sentence and a confidence, instead of silence.
    """
    root = _root(args)
    with _lock(root):
        ledger = store.load(root)
        extraction = Extraction(
            what=args.what,
            grounds=_collect(args.grounds),
            confidence=args.confidence,
            note=args.note or "",
        )
        artifact = artifacts.add_extraction(ledger, args.artifact, extraction)
        unmatched = _link_grounding(ledger)
        store.save(root, ledger)

    if args.json:
        _dump(dict(artifact.to_dict(), unmatched_grounds=unmatched))
        return 0
    _say(f"{artifact.id}: {extraction.what}  [{extraction.confidence}]")
    if extraction.grounds:
        _say(f"  grounds: {', '.join(extraction.grounds)}")
        pending = [name for name in extraction.grounds if name in unmatched]
        if pending:
            _warn(f"warning: nothing in the ledger is named {', '.join(pending)} yet — "
                  f"if that is a model parameter it links on the next `atompipe check`; "
                  f"if it is a typo, nothing will ever stand on this evidence")
    else:
        _say("  grounds nothing yet — `--grounds <param-or-claim>` is what makes this "
             "traceable from the other end")
    return 0


# --------------------------------------------------------------------------- #
# claims
# --------------------------------------------------------------------------- #
def _acceptance_from(args: argparse.Namespace, existing: Acceptance | None = None) -> Acceptance:
    """Build (or patch) an `Acceptance` from the threshold flags.

    Patching rather than replacing so `claim edit --limit 0.4` does not silently
    wipe the quantity and units someone wrote last week.
    """
    base = existing or Acceptance()
    comparator = base.comparator
    if getattr(args, "cmp", None):
        comparator = Comparator(args.cmp)
    return Acceptance(
        quantity=args.quantity if args.quantity is not None else base.quantity,
        comparator=comparator,
        limit=args.limit if args.limit is not None else base.limit,
        limit_hi=args.limit_hi if args.limit_hi is not None else base.limit_hi,
        units=args.units if args.units is not None else base.units,
    )


def cmd_claim_add(args: argparse.Namespace) -> int:
    """Add one claim. Warns — loudly — when it has no machine-checkable threshold.

    The warning rather than a refusal is deliberate. "A claim without an
    acceptance is a wish" (models.Acceptance), and a wish must not be allowed to
    look settled — but claims are captured during intake, often before anyone
    knows the number, and a CLI that refuses the first half of the thought gets
    replaced by a text file. So it is recorded, and it nags every time it is
    printed.
    """
    root = _root(args)
    with _lock(root):
        ledger = store.load(root)
        claim_id = (args.id or "").strip() or claims.next_claim_id(ledger, args.prefix)
        if ledger.claim(claim_id):
            raise AtompipeError(
                f"claim {claim_id!r} already exists — use `atompipe claim edit {claim_id}`")
        claim = Claim(
            id=claim_id,
            statement=args.statement.strip(),
            kind=ClaimKind(args.kind),
            acceptance=_acceptance_from(args),
            rationale=args.rationale or "",
            source=args.source or "",
            grounded_by=_collect(args.grounds),
            gates=_collect(args.gates),
            tags=_collect(args.tags),
            critical=not args.nice_to_have,
            note=args.note or "",
        )
        ledger.claims.append(claim)
        store.save(root, ledger)

    if args.json:
        _dump(claim.to_dict())
        return 0
    _say(f"{claim.id}  {claim.statement}  [{claim.kind}]"
         f"{'' if claim.critical else '  (nice-to-have)'}")
    rendered = claim.acceptance.render()
    if rendered:
        _say(f"  accepts: {rendered}")
    if claim.acceptance.limit is None and claim.kind is ClaimKind.MEASURABLE:
        _warn(f"warning: {claim.id} has no acceptance threshold — 'strong enough' is not "
              f"a claim. Add one: atompipe claim edit {claim.id} "
              f"--quantity '<what is measured>' --cmp '<=' --limit <number> --units mm")
    return 0


def cmd_claim_list(args: argparse.Namespace) -> int:
    """One line per claim: status, id, statement, acceptance.

    Sorted by ledger order, not by status: an agent reading this twenty minutes
    apart needs the same line in the same place, and a list that reorders itself
    as things pass is unreadable as a diff.
    """
    root = _root(args)
    ledger = store.load(root)
    registry, _ = _registry(root, ledger, strict=False)
    model, projection, model_error = _projection_safe(root, ledger)
    view, resolution = _resolved(root, ledger, registry, projection, model_error,
                                 now=utcnow_iso(), model=model)
    resolved = claims.statuses(view, registry=registry, stale_gates=resolution.stale_gates)
    cover = claims.coverage(view, registry)

    rows = list(view.claims)
    if args.status:
        rows = [c for c in rows if str(resolved.get(c.id)) == args.status]
    if args.kind:
        rows = [c for c in rows if str(c.kind) == args.kind]
    if args.tag:
        rows = [c for c in rows if args.tag in (c.tags or ())]

    if args.json:
        _dump({"claims": [dict(c.to_dict(), status=str(resolved.get(c.id)),
                               covered_by=cover.get(c.id, [])) for c in rows],
               "stale": bool(resolution.stale_gates),
               "stale_gates": _stale_gate_list(resolution)})
        return 0

    if not rows:
        _say("no claims recorded" if not view.claims else "no claims match that filter")
        return 0
    for claim in rows:
        status = resolved.get(claim.id, ClaimStatus.UNCLAIMED)
        accepts = claim.acceptance.render() or "NO THRESHOLD"
        flag = "" if claim.critical else " (nice-to-have)"
        _say(f"{report.status_tag(status)} {claim.id:<6} {claim.statement}{flag}"
             f"  [{claim.kind}] {accepts}")
    return 0


def cmd_claim_show(args: argparse.Namespace) -> int:
    """One claim's whole provenance — the same view `atompipe why` gives.

    Routed through `decisions.why` rather than re-rendered here, because two
    renderings of one claim's history is two places to forget the rejected
    alternatives.
    """
    root = _root(args)
    ledger = store.load(root)
    if ledger.claim(args.id) is None:
        raise AtompipeError(f"no claim {args.id!r} — `atompipe claim list` shows what exists")
    registry, _ = _registry(root, ledger, strict=False)
    model, projection, model_error = _projection_safe(root, ledger)
    view, resolution = _resolved(root, ledger, registry, projection, model_error,
                                 now=utcnow_iso(), model=model)
    # The view's claim: its `gates` are the registry's coverage over the records'
    # cached opinion (cli:H5). This command used to resolve the bare record with
    # no registry at all — `claim list` said PENDING while `claim show` said
    # UNCLAIMED for the same claim, one command apart.
    claim = view.claim(args.id)
    status = claims.resolve_status(claim, view.verdicts, stale_gates=resolution.stale_gates)

    if args.json:
        _dump(dict(claim.to_dict(), status=str(status),
                   covered_by=claims.coverage(view, registry).get(claim.id, []),
                   verdicts=[_resolved_row(v, resolution)
                             for v in claims.covering_verdicts(claim, view.verdicts)],
                   why=decisions.why(view, claim.id)))
        return 0
    _say(f"{report.status_tag(status)} {claim.id}")
    sys.stdout.write(decisions.why(view, claim.id))
    return 0


def cmd_claim_edit(args: argparse.Namespace) -> int:
    """Change one claim in place. Only the flags you pass are touched.

    Every field is optional and `None` means "leave it": an edit command that
    reset unmentioned fields to defaults would quietly delete a rationale every
    time someone retagged a claim.
    """
    root = _root(args)
    with _lock(root):
        ledger = store.load(root)
        claim = ledger.claim(args.id)
        if claim is None:
            raise AtompipeError(f"no claim {args.id!r}")
        if args.statement is not None:
            claim.statement = args.statement.strip()
        if args.kind is not None:
            claim.kind = ClaimKind(args.kind)
        if args.rationale is not None:
            claim.rationale = args.rationale
        if args.source is not None:
            claim.source = args.source
        if args.note is not None:
            claim.note = args.note
        if args.tags is not None:
            claim.tags = _collect(args.tags)
        if args.gates is not None:
            claim.gates = _collect(args.gates)
        if args.grounds is not None:
            claim.grounded_by = _collect(args.grounds)
        if args.critical:
            claim.critical = True
        if args.nice_to_have:
            claim.critical = False
        claim.acceptance = _acceptance_from(args, claim.acceptance)
        store.save(root, ledger)

    if args.json:
        _dump(claim.to_dict())
        return 0
    _say(f"{claim.id}  {claim.statement}  [{claim.kind}]  "
         f"{claim.acceptance.render() or 'NO THRESHOLD'}")
    return 0


def cmd_claim_physical(args: argparse.Namespace) -> int:
    """Record a real-world result against a PHYSICAL claim.

    The only way a physical claim ever leaves UNVERIFIED. No simulation launders
    one into green — "the printed seam is watertight" is settled by water — so
    this command exists to let the one thing that CAN settle it, a human with the
    object, say so on the record, with a date, a name and the evidence files.

    Refuses on a MEASURABLE claim on purpose: hand-recording a pass for something
    a gate is supposed to prove is exactly how a readiness report stops meaning
    anything.
    """
    root = _root(args)
    with _lock(root):
        ledger = store.load(root)
        claim = ledger.claim(args.id)
        if claim is None:
            raise AtompipeError(f"no claim {args.id!r}")
        if claim.kind is not ClaimKind.PHYSICAL:
            raise AtompipeError(
                f"claim {claim.id!r} is {claim.kind}, not physical. A hand-recorded "
                f"result on a measurable claim is an unchecked assertion wearing a "
                f"gate's clothes — run the gate, or change the claim's kind on purpose "
                f"with `atompipe claim edit {claim.id} --kind physical`")
        # Two spellings because two exist in the wild: `--pass`/`--fail` is what
        # the generated readiness report tells the user to run, and a bare
        # `pass`/`fail` is what people type. Accepting only one of them would
        # make a command this project prints itself fail on paste.
        passed = args.passed
        if passed is None:
            if not args.result:
                raise AtompipeError(
                    f"say what happened: `atompipe claim physical {args.id} pass` "
                    f"or `--fail`, with --detail describing what was actually observed")
            passed = args.result == "pass"
        claim.physical_result = PhysicalResult(
            passed=passed,
            when=(args.when or utcnow_iso()),
            who=args.who or "",
            detail=args.detail or "",
            evidence=_collect(args.evidence),
        )
        store.save(root, ledger)

    status = ClaimStatus.VERIFIED if claim.physical_result.passed else ClaimStatus.REFUTED
    if args.json:
        _dump(dict(claim.to_dict(), status=str(status)))
        return 0
    _say(f"{report.status_tag(status)} {claim.id} {claim.statement} — "
         f"{claim.physical_result.detail or ('pass' if passed else 'fail')} "
         f"({claim.physical_result.who or 'unattributed'}, {claim.physical_result.when})")
    return 0


# --------------------------------------------------------------------------- #
# gaps
# --------------------------------------------------------------------------- #
def cmd_gap(args: argparse.Namespace) -> int:
    """Measurable claims no gate covers — and, with `--propose`, packs that might.

    A gap is a growth signal, not a failure: the system is admitting there is a
    physical quantity it cannot currently check. The Needs are PERSISTED so that
    the classification and the candidate costs an agent records against one
    survive to the next session — `find_gaps` matches existing Needs by claim id
    and keeps everything that was written on them.

    Needs that are no longer gaps are left in the ledger untouched rather than
    deleted. A closed gap is a fact about the project's history, and deciding it
    is `SATISFIED` is a judgement this command is not entitled to make.
    """
    root = _root(args)
    with _lock(root):
        ledger = store.load(root)
        registry, _ = _registry(root, ledger, strict=False)
        gaps = claims.find_gaps(ledger, registry)
        by_id = {need.id: index for index, need in enumerate(ledger.needs)}
        for need in gaps:
            if need.id in by_id:
                ledger.needs[by_id[need.id]] = need
            else:
                ledger.needs.append(need)
        store.save(root, ledger)

    manifests = packs.discover(root) if args.propose else []
    proposals: dict[str, list[Any]] = {
        need.id: packs.match(need, manifests) for need in gaps} if args.propose else {}

    if args.json:
        _dump({
            "gaps": [dict(need.to_dict(),
                          candidates_from_packs=[m.to_dict() for m in proposals.get(need.id, [])])
                     for need in gaps],
            "n_gaps": len(gaps),
        })
        return 0

    if not gaps:
        _say("no gaps: every measurable claim has a registered gate covering it")
        return 0

    for need in gaps:
        cids = ", ".join(need.claim_ids)
        _say(f"{_tag('gap')} {need.id:<10} {need.quantity or '(unnamed quantity)'} "
             f"— claims {cids} ({need.status})")
        if need.claim_class:
            _say(f"            class: {need.claim_class}")
        for candidate in need.candidates:
            _say(f"            {candidate.kind or 'candidate':<9} {candidate.name} "
                 f"— {candidate.cost or 'cost unstated'}")
        if args.propose:
            matched = proposals.get(need.id, [])
            if matched:
                for manifest in matched[:3]:
                    _say(f"            pack      {manifest.name} — {manifest.description}")
            else:
                _say("            no installed pack settles this vocabulary — this is the "
                     "extension protocol's trigger (docs/EXTENSION_PROTOCOL.md)")
    _say(f"{len(gaps)} gap(s). Name the quantity, try the analytic answer first, and only "
         f"then propose a solver — with its real cost said out loud.")
    return 0


# --------------------------------------------------------------------------- #
# gates
# --------------------------------------------------------------------------- #
def _gate_row(spec: Any) -> dict[str, Any]:
    """One gate as JSON, without its prose. The default shape of `gate list --json`.

    Everything here answers a question a caller can act on: what it is, what it
    costs, where it came from, what it settles, what it needs installed, and —
    non-negotiably — the fixture of its negative control, because a gate with no
    demonstrable failure is a logger and the listing must not hide that. What is
    left out is `description` and the control's `note`, which are paragraphs of
    English. `--full` brings both back; `atompipe gate show <id>` prints them for
    the one gate you are actually reading.
    """
    control = getattr(spec, "negative_control", None)
    row: dict[str, Any] = {
        "id": spec.id,
        "title": spec.title,
        "tier": int(spec.tier),
        "pack": spec.pack,
        "settles": spec.settles,
        "claims": list(spec.claims or []),
        "negative_control": {"fixture": control.fixture,
                             "expect": control.expect} if control else None,
    }
    # Empty requirement lists are the common case and say nothing; a present one
    # is the reason a gate skips, so it is never dropped when it has content.
    if spec.requires_tools:
        row["requires_tools"] = list(spec.requires_tools)
    if spec.requires_python:
        row["requires_python"] = list(spec.requires_python)
    return row


def cmd_gate_list(args: argparse.Namespace) -> int:
    """Every registered gate, one dense line each, via `gates.describe`.

    The line includes the negative control, because the control is the fact that
    makes the gate's green tick mean anything and it belongs where the gate is
    listed rather than three commands away.

    `--json` emits a SUMMARY projection, not the whole spec. The full dump
    measured 61,721 characters — roughly 15k tokens — on a project with five
    packs installed, and almost all of it was `description`: every gate's
    multi-paragraph docstring, plus the prose note on its negative control. An
    agent runs this command to find out which gate settles which claim, and
    paying fifteen thousand tokens for the prose it did not ask for is the
    opposite of a cheap inner loop (rule 10). `--full` still emits everything,
    for the one caller in a hundred that wants it, and `atompipe gate show <id>`
    has always been the way to read one gate's prose.
    """
    root = _root(args)
    ledger = store.load(root)
    registry, problems = _registry(root, ledger, strict=False)
    specs = registry.by_tier(args.tier if args.tier is not None else ALL_TIERS)

    if args.json:
        _dump({"gates": [spec.to_dict() if args.full else _gate_row(spec)
                         for spec in specs],
               "full": bool(args.full),
               "summary": gates.registry_summary(registry),
               "problems": problems})
        return 0

    if not specs:
        _say("no gates registered. Install a pack (`atompipe packs`) or write "
             "gates/<name>.py in this project.")
        for problem in problems:
            _say(f"{_tag('FAIL')} {problem}")
        return 0
    for spec in specs:
        _say(gates.describe(spec))
    summary = gates.registry_summary(registry)
    _say(f"{summary['gates']} gates, {summary['tier0']} at tier 0, "
         f"{summary['available']} runnable here")
    for problem in problems:
        _say(f"{_tag('FAIL')} {problem}")
    return 0


def _last_selftest(admission: verdicts.Admission) -> dict[str, Any] | None:
    """`gate show --json`'s `last_selftest`, from how the gate's control stands
    at its current version (`verdicts.admission_state`), or None when no control
    was ever demonstrated for it.

    `outcome` is the selftest's own (the verdict on the INSTRUMENT): `"pass"` —
    it fired, admitted or pending; `"fail"` — it PASSED its own known-bad input;
    `"error"` — it crashed, its fixture was unusable, or two recorded outcomes
    disagree. `control` is the control entry's rho (the text prints its first
    12), `at_this_version` whether that entry is at the gate's current static
    part, `admission` the state, `detail` the reason or the entry's words.

    What slipped through (S-08): this read a ledger key `<gate>#selftest` that
    `gate selftest` deliberately never wrote, so every gate read "(never run)"
    forever, including the six the selftest had just demonstrated.
    """
    state, entry = admission.state, admission.entry
    if entry is None and state == "undemonstrated":
        return None
    if state in ("admitted", "pending"):
        outcome = "pass"
    elif state == "not-admitted":
        outcome = "fail" if entry is not None and entry.bad == "pass" else "error"
    else:                                     # an entry at another version
        outcome = "pass" if entry.bad == "fail" else "fail"
    return {
        "outcome": outcome,
        "control": entry.rho if entry is not None else None,
        "at_this_version": state != "undemonstrated",
        "admission": state,
        "detail": admission.reason or (entry.detail if entry is not None else ""),
    }


def _last_selftest_line(admission: verdicts.Admission) -> str:
    """`gate show`'s last line (spec §3.13): fired, PASSED its own known-bad,
    pending re-verification, or not demonstrated — each at this version, with
    the control entry's rho to 12 places where there is one."""
    state, entry = admission.state, admission.entry
    control = f" (control {entry.rho[:12]})" if entry is not None else ""
    if state == "admitted":
        return f"  last selftest: {_tag('ok')} fired at this version{control}"
    if state == "pending":
        return (f"  last selftest: pending — control inputs moved; the next check "
                f"re-verifies{control}")
    if state == "not-admitted" and entry is not None and entry.bad == "pass":
        return f"  last selftest: {_tag('FAIL')} PASSED its own known-bad at this version{control}"
    if state == "not-admitted":
        return f"  last selftest: {_tag('FAIL')} not admitted at this version — {admission.reason}"
    return "  last selftest: not demonstrated at this version"


def cmd_gate_show(args: argparse.Namespace) -> int:
    """Everything about one gate: what it settles, what it needs, how it last ran,
    and whether its control is demonstrated at this version.

    `last verdict` is the resolver's (`_resolved`), with why it is not current
    when it is not; `last selftest` is read off the control entries by
    `verdicts.admission_state`, which never runs a fixture — `check` and `gate
    selftest` are the commands that spend that time.
    """
    root = _root(args)
    ledger = store.load(root)
    registry, _ = _registry(root, ledger, strict=False)
    entry = registry.get(args.id)
    if entry is None:
        known = ", ".join(registry.ids()[:12]) or "(none registered)"
        raise AtompipeError(f"no gate {args.id!r}. Registered: {known}")
    spec, fn = entry
    ok, reason = gates.availability(spec)
    model, projection, model_error = _projection_safe(root, ledger)
    view, resolution = _resolved(root, ledger, registry, projection, model_error,
                                 now=utcnow_iso(), model=model)
    verdict = view.verdict(spec.id)
    row = resolution.rows.get(spec.id)
    admission = verdicts.admission_state(root, spec, fn, projection=projection,
                                         anchors=resolution.anchors)

    if args.json:
        _dump({"gate": spec.to_dict(), "available": ok, "availability": reason,
               "last_verdict": _resolved_row(verdict, resolution) if verdict else None,
               "last_selftest": _last_selftest(admission)})
        return 0

    _say(gates.describe(spec))
    if spec.description:
        _say(f"  {spec.description}")
    _say(f"  tier {int(spec.tier)}  pack {spec.pack or '(project)'}  entry {spec.entry}")
    _say(f"  claims: {', '.join(spec.claims) or '(none — this gate settles nothing)'}")
    _say(f"  runnable here: {'yes' if ok else 'NO — ' + reason}")
    if spec.negative_control:
        _say(f"  control: {spec.negative_control.fixture} "
             f"(must {spec.negative_control.expect})")
        if spec.negative_control.note:
            _say(f"           {spec.negative_control.note}")
    else:
        _say("  control: NONE — this gate cannot be shown to fail, so it is a logger")
    stale = f" (stale: {row.stale_reason})" if row is not None and row.stale_reason else ""
    _say(f"  last verdict: {verdict.render() + stale if verdict else '(never run)'}")
    _say(_last_selftest_line(admission))
    return 0


def _selftest_code(*, broken: int, baselines_failed: int, exercised: int,
                   allow_empty: bool) -> int:
    """`gate selftest`'s one exit code, in both modes.

    1 on a control that did not fire (or crashed, or is gone), on a pack gate
    that failed its own baseline, and on a run that exercised no control at all.
    That last one is `--allow-empty`'s to waive and nobody else's. What slipped
    through (cli:H8): with nothing registered the text path printed "no controls
    to run" and returned 0, and the JSON path said `"ok": true` — a selftest that
    passes by running nothing is a logger, the thing this command exists to
    catch (PLAN G3). A tooling skip is not exercise: it tested nothing.
    """
    if broken or baselines_failed:
        return 1
    if exercised == 0 and not allow_empty:
        return 1
    return 0


def _selftest_summary(controls: int, elapsed: float, fired: int, broken: int,
                      skipped: int) -> str:
    """`<n> control(s) in <t>: <f> fired, <b> BROKEN, <k> skipped (tooling)`.

    One spelling for both modes (spec §3.13), and the last line of a clean run —
    the fresh-clone transcript matches it there. `(tooling)` because a control can
    only skip for missing tooling: `gates.selftest` turns any other skip into an
    error (S-12), so the word says which skips these are.
    """
    return (f"{controls} control(s) in {human_duration(elapsed)}: {fired} fired, "
            f"{broken} BROKEN, {skipped} skipped (tooling)")


def _say_empty(allow_empty: bool, why: str) -> None:
    """The line under a summary that counted zero controls exercised."""
    if allow_empty:
        _say(f"note: no control ran ({why}) — allowed by --allow-empty")
    else:
        _say(f"no control ran ({why}), so no gate was shown able to fail — a "
             f"selftest that exercises nothing is not a pass (--allow-empty if "
             f"nothing is expected here)")


def _selftest_one(root: str, spec: Any, fn: Any, ctx: gates.GateContext, *,
                  projection: dict | None, record: bool, now: str,
                  anchors: verdicts.Anchors, digests: FileDigests) -> Verdict:
    """One control, run and filed as `check` files it; the row `gate selftest` prints.

    Where the gate's tools are missing nothing runs and nothing is filed: the
    row is `gates.selftest`'s own skip. Otherwise `verdicts.admission` with
    `force=True` runs fixture and gate and files the outcome (`record`), and the
    row is read back from what it decided: fired (admitted) is a passing row
    carrying the entry's words and numbers; PASSED its own known-bad input, a
    crash, an unusable fixture or an outcome that contradicts a cached entry is a
    failing row with the reason; tools that vanished mid-run a skip. Its time is
    the wall time of that one call.
    """
    ok, _why = gates.availability(spec)
    if not ok:
        return gates.selftest(spec, fn, ctx)
    clock = time.perf_counter()
    judged = verdicts.admission(root, spec, fn, ctx, force=True, record=record,
                                projection=projection, digests=digests, anchors=anchors,
                                when=now)
    elapsed = round(time.perf_counter() - clock, 6)
    base: dict[str, Any] = {"gate": f"{spec.id}#selftest", "tier": Tier(int(spec.tier)),
                            "pack": spec.pack or "", "claims": [], "duration_s": elapsed}
    entry = judged.entry
    numbers: dict[str, Any] = ({"measured": entry.measured, "limit": entry.limit,
                                "units": entry.units} if entry is not None else {})
    if judged.state == "admitted" and entry is not None:
        return Verdict(passed=True, detail=entry.detail, **numbers, **base)
    if judged.state == "undemonstrated":
        return Verdict(passed=False, skipped=True,
                       skip_reason=judged.reason or "its tooling is not available", **base)
    passed_bad = entry is not None and entry.bad == "pass" and \
        (judged.reason or "").startswith("PASSED its own known-bad")
    detail = entry.detail if passed_bad else (judged.reason or "control not demonstrated")
    return Verdict(passed=False, detail=detail, **numbers, **base)


def cmd_gate_selftest(args: argparse.Namespace) -> int:
    """Run every gate against its own known-bad input, and fail if one does not fail.

    This is rule 5 with a return code. `passed` on one of these lines means *the
    gate correctly rejected input that is known to be bad* — it measures the
    instrument, not the design.

    Exit 1 covers three outcomes, not one: a gate that PASSED its known-bad
    fixture (it is not measuring what it claims), a gate that CRASHED on it
    (refusing by exploding is not detecting), and a fixture that is missing or
    broken (the control is gone, so the gate is unproven). A gate whose tooling
    is absent SKIPS and does not fail the command — nothing was tested, and that
    is already visible as BLOCKED in the readiness report. A run in which no
    control ran at all exits 1 too, unless `--allow-empty` says that is expected
    (`_selftest_code`).

    Every tier runs by default. Capping the default at tier 0 would leave the
    expensive gates — the ones nobody re-reads — permanently unproven, which is
    the exact shape of the failure this command exists to catch.

    **Two modes.** Inside a project this runs the project's controls against the
    project's model. With no project — the repository root, where `CLAUDE.md`
    tells a pack author to run it — or with `--pack`, it runs **pack mode**
    (`_selftest_packs`). What slipped through (S-09): the command needed a
    project and exited 2 at the root, so the merge check `CLAUDE.md` and
    `CONTRIBUTING.md` prescribe could not run where they prescribe it, and CI ran
    it only inside the bracket, which loads no pack at all. The branch comes
    before `_root`, `_lock`, `store.load` and `_projection` (cli:H8): each of them
    assumes a project, and a broken model must not stop a pack's controls.

    **Project mode files what it demonstrates** (D-07, S-08). Every selected
    control runs — fixture and gate, never served from the cache — through
    `verdicts.admission(..., force=True)`, the same code `check` runs a control
    with, so the entry it files is byte for byte the one `check` would: a
    project fixture is handed the known-good design (`selftest/known_good.py`,
    D-27), a pack's the live host; each gets its own emptied scratch under
    `.atompipe/out/controls/<gate>/`. A measurement becomes a control entry
    (`.atompipe/verdicts/<gate>/control-<rho16>-<out8>.json`, O_EXCL: an
    unchanged control re-creates the same name and writes nothing) and a control
    obs row; a crash or an unusable fixture is remembered and never cached.
    `--no-record` writes none of it. What slipped through before: the verdicts
    went to the run history only, so `check` never learned a control had been
    shown to fire and `gate show` read "(never run)" forever (S-08). The rows
    carry no claims: proof that the instrument works must never resolve one.
    """
    junit_arg = _junit_arg(args)
    root = store.find_root(getattr(args, "dir", None))
    junit = _junit_unlink(args, junit_arg, root=root)
    if root is None or args.pack:
        return _selftest_packs(args, root, junit)

    max_tier = ALL_TIERS if args.tier is None else int(args.tier)
    selection = list(args.gates or []) + list(args.only or [])
    record = not args.no_record
    now = utcnow_iso()

    with _lock(root):
        ledger = store.load(root)
        registry, _ = _registry(root, ledger, strict=True)
        model, projection = _projection(root, ledger)
        ctx = _context(root, ledger, model, projection, max_tier, quiet=args.json)
        # Private, on purpose: `--only` must mean exactly what it means for
        # `check`, and a second copy of the id/pack/glob matching rule would
        # eventually disagree with the first — always in the permissive
        # direction, which here would silently test fewer controls than asked.
        specs = gates._selected(registry, max_tier, selection or None)
        # The sweep's anchors, not each control's defaults: an entry spells its
        # paths against them, and a different spelling is a different
        # rho_control — `gate selftest` would file a second entry beside the one
        # `check` filed for the same demonstration.
        anchors = verdicts.anchors_for(root, registry, out_dir=ctx.out_dir)
        digests = FileDigests()

        started = time.perf_counter()
        results: list[Verdict] = []
        for spec in specs:
            pair = registry.get(spec.id)
            if pair is None:                        # pragma: no cover - defensive
                continue
            verdict = _selftest_one(root, spec, pair[1], ctx, projection=projection,
                                    record=record, now=now, anchors=anchors,
                                    digests=digests)
            results.append(verdict)
            if not args.json:
                _say(verdict.render())
        elapsed = time.perf_counter() - started
        installed = packs.installed(root, ledger=ledger)

    broken = [v for v in results if not v.ok and not v.skipped]
    skipped = [v for v in results if v.skipped]
    fired = len(results) - len(broken) - len(skipped)
    code = _selftest_code(broken=len(broken), baselines_failed=0,
                          exercised=fired + len(broken), allow_empty=args.allow_empty)
    written = _junit_write(junit, lambda: report.render_selftest_junit(
        results, exit_code=code, when=now))

    if args.json:
        _dump({"mode": "project",
               "packs": [_pack_row(name, root) for name in installed],
               "selftests": [_verdict_row(v) for v in results],
               "baselines": None,
               "broken": [v.gate for v in broken],
               "skipped": [v.gate for v in skipped],
               "counts": {"controls": len(results), "fired": fired,
                          "broken": len(broken), "skipped": len(skipped)},
               "allow_empty": bool(args.allow_empty),
               "junit": written,
               "ok": code == 0})
        return code

    _say(_selftest_summary(len(results), elapsed, fired, len(broken), len(skipped)))
    if fired + len(broken) == 0:
        _say_empty(args.allow_empty,
                   "no gates registered" if not results else "every control skipped")
    if broken:
        _say("these gates cannot be trusted — each one failed to reject its own "
             "known-bad input, or lost its control:")
        for verdict in broken:
            _say(f"{_tag('FAIL')} {verdict.gate} — "
                 f"{verdict.detail or verdict.error or 'no detail'}")
    return code


# --------------------------------------------------------------------------- #
# gate selftest, pack mode:  the gate on the gates, where the packs are
# --------------------------------------------------------------------------- #
def _pack_row(name: str, root: str) -> dict[str, str]:
    """One installed pack as `gate selftest --json` lists it in project mode:
    its name, where it resolved from, and which search root that was."""
    pack_dir = packs.find(name, root) or ""
    return {"name": name, "dir": pack_dir,
            "origin": packs.origin_of(pack_dir, root) if pack_dir else "missing"}


def _packs_under(base: str) -> list[str]:
    """Pack directories directly inside `base`, sorted — by `packs.discover_dirs`'
    rules: a `pack.json` makes a directory a pack, and names starting `.` or `_`
    are never one (`packs/__init__.py` sits beside the bundled packs)."""
    try:
        entries = sorted(os.listdir(base))
    except OSError:
        return []
    return [os.path.join(base, entry) for entry in entries
            if not entry.startswith((".", "_"))
            and os.path.isfile(os.path.join(base, entry, packs.MANIFEST_NAME))]


#: The `skip_reason` prefix of a control `_read_back` could not count: its pack's
#: demonstration stopped before it, or did not add up. Not a tooling skip, so it
#: is left out of the `skipped (tooling)` count; the pack's own problem row is
#: what fails the run.
_NOT_RUN = "not run: "


def _bare(gate_id: str) -> str:
    """A control's gate id without `gates.selftest`'s `#selftest` suffix."""
    return gate_id[: -len("#selftest")] if gate_id.endswith("#selftest") else gate_id


def _pack_targets(args: argparse.Namespace, root: str | None) -> tuple[list[tuple[str, str]], list[str]]:
    """`([(name, pack dir)], notes)`: what pack mode demonstrates, in order.

    * `--pack DIR` — that pack, or, for a directory that is not one, every pack
      directly inside it (`--pack packs/`, `--pack ~/.atompipe/packs`). A
      directory holding none is a note and zero targets, which the exit code then
      refuses unless `--allow-empty`.
    * `--pack NAME` — resolved like a project resolves it, but with
      `$ATOMPIPE_PACK_PATH` and `~/.atompipe/packs` searched only under
      `--user-packs`; inside a project its `.atompipe/packs/` is searched too.
    * neither — every bundled pack, plus the env and user packs under
      `--user-packs`, first-found-wins by directory name as discovery resolves it.

    What slipped through without the switch (S-87): the env and user entries
    outrank the bundled packs, so on a pack author's machine a same-named copy in
    `~/.atompipe/packs` would have been the one demonstrated while every line
    named the bundled one. The machine does not get to choose what the merge
    check tests. *Rejected:* refusing to run while a user pack shadows a bundled
    one — the author's copy is often the point, and `--user-packs` names it.
    """
    include = bool(getattr(args, "user_packs", False))
    project = root or ""          # "" keeps search_paths from finding one itself
    out: list[tuple[str, str]] = []
    notes: list[str] = []
    seen: set[str] = set()

    def add(pack_dir: str) -> None:
        pack_dir = os.path.abspath(pack_dir)
        key = os.path.normcase(pack_dir)
        if key not in seen:
            seen.add(key)
            out.append((os.path.basename(pack_dir.rstrip(os.sep)), pack_dir))

    if args.pack:
        for value in args.pack:
            if os.path.isdir(value):
                if os.path.isfile(os.path.join(value, packs.MANIFEST_NAME)):
                    add(value)
                    continue
                inside = _packs_under(value)
                if not inside:
                    notes.append(f"{os.path.abspath(value)} holds no pack: no "
                                 f"{packs.MANIFEST_NAME} in it or directly below it")
                for pack_dir in inside:
                    add(pack_dir)
                continue
            if os.sep in value or (os.altsep and os.altsep in value):
                raise AtompipeError(f"--pack {value}: no such directory")
            found = packs.find(value, project, include_env=include, include_user=include)
            if found is None:
                looked = packs.search_paths(project, existing_only=False,
                                            include_env=include, include_user=include)
                where = "\n  ".join(looked) or "(no search paths)"
                extra = "" if include else (
                    f"\n($ATOMPIPE_PACK_PATH and ~/.atompipe/packs are searched only "
                    f"with --user-packs)")
                raise AtompipeError(f"no pack named {value!r} — searched:\n  {where}{extra}")
            add(found)
        return out, notes

    claimed: set[str] = set()
    for base in packs.search_paths(project, include_env=include, include_user=include):
        for pack_dir in _packs_under(base):
            name = os.path.normcase(os.path.basename(pack_dir))
            if name in claimed:
                continue                  # shadowed, exactly as a project would see it
            claimed.add(name)
            add(pack_dir)
    return out, notes


def _selftest_packs(args: argparse.Namespace, root: str | None, junit: str | None) -> int:
    """`gate selftest` in pack mode: each target through `packs.demonstrate`.

    Per gate, the same three runs `pack validate` makes: the pack's own baseline
    must pass, the control must fire, and it must still fire against an empty
    host (the seal probe). `demonstrate` is the one implementation of those rules;
    this function only picks the packs, reads the result back per gate, and
    renders it. Every run gets a temp `out_dir` that is removed afterwards, and
    nothing is recorded — no project, no ledger, no run history (Q1.8).

    The tier ceiling defaults to EXTERNAL: every control, as in a project. A gate
    named explicitly (positionally or `--only`) runs above an explicit `--tier`,
    because naming it is the opt-in to its cost — `gates._selected`'s rule, used
    here per pack so the matching can never disagree with a project's.

    Reading `Demonstration` back per gate needs each pack's gate list, which it
    does not carry: counts and problem lines only. So each pack is loaded first
    through `packs._load_dir` — the loader `demonstrate` itself uses, from the
    same directory — into a registry of its own. A module is executed once per
    process and reused after that, so the second load costs a lookup and cannot
    see different code. The counts must then add up: if `demonstrate` ran a
    different number of controls from the gates it was given, none of that
    pack's controls is counted as fired. *Rejected:* re-running the baseline and
    control here per gate — a second copy of the rules, which drifts from
    `demonstrate` in whichever direction nobody tests (D-25).
    """
    ceiling = int(Tier.EXTERNAL) if args.tier is None else int(args.tier)
    selection = [p for p in list(args.gates or []) + list(args.only or []) if str(p).strip()]
    targets, notes = _pack_targets(args, root)
    started = time.perf_counter()

    # Pass 1: what each pack registers, and which of its gates the selection picks.
    plans: list[dict[str, Any]] = []
    hit: set[str] = set()
    every_pack_loaded = True
    for name, pack_dir in targets:
        plan: dict[str, Any] = {
            "name": name, "dir": pack_dir, "origin": packs.origin_of(pack_dir, root or ""),
            "specs": [], "chosen": [], "problems": [],
        }
        plans.append(plan)
        registry = gates.Registry()
        try:
            packs._load_dir(name, pack_dir, registry)
        except AtompipeError as exc:
            plan["problems"].append(f"{packs.GATES_DIR}/: {exc}")
            every_pack_loaded = False
            continue
        plan["specs"] = registry.specs()
        if not selection:
            plan["chosen"] = [s.id for s in registry.by_tier(ceiling)]
            continue
        chosen: set[str] = set()
        for pattern in selection:
            try:
                picked = gates._selected(registry, ceiling, [pattern])
            except AtompipeError:
                continue              # no hit in THIS pack; another may have it
            if picked:
                hit.add(pattern)
                chosen.update(s.id for s in picked)
        plan["chosen"] = [s.id for s in plan["specs"] if s.id in chosen]

    unmatched = [p for p in selection if p not in hit]
    if unmatched and every_pack_loaded:
        # A pack that did not load might have held the gate, so only a complete
        # picture may refuse the name; otherwise the load failure is reported.
        raise AtompipeError(
            f"no gate in {', '.join(n for n, _ in targets) or 'no pack'} matches "
            f"{', '.join(repr(u) for u in unmatched)} — running zero controls and "
            f"calling it a clean selftest is the failure this command exists to prevent")

    # Pass 2: demonstrate, then read the result back per chosen gate.
    controls: list[Verdict] = []
    baselines: list[Verdict] = []
    failed_rows: list[tuple[str, str]] = []      # (label, why): each failed baseline
    with tempfile.TemporaryDirectory(prefix="atompipe-selftest-",
                                     ignore_cleanup_errors=True) as scratch:
        for index, plan in enumerate(plans):
            if plan["chosen"]:
                # Up to the dearest gate named: naming it opted into its cost.
                tier = max([ceiling] + [int(s.tier) for s in plan["specs"]
                                        if s.id in plan["chosen"]])
                shown = packs.demonstrate(
                    plan["dir"], tier=tier,
                    out_dir=os.path.join(scratch, f"{index:03d}-{plan['name']}"))
                _read_back(plan, shown, tier)
            for problem in plan["problems"]:
                baselines.append(Verdict(gate=plan["name"], pack=plan["name"],
                                         passed=False, detail=problem))
                failed_rows.append((plan["name"], problem))
            controls.extend(plan.get("controls", []))
            for verdict in plan.get("baselines", []):
                baselines.append(verdict)
                if verdict.outcome in ("fail", "error"):
                    failed_rows.append((f"{verdict.gate} ({plan['name']})",
                                        verdict.detail or verdict.error))
    elapsed = time.perf_counter() - started
    now = utcnow_iso()

    broken = [v for v in controls if v.outcome in ("fail", "error")]
    skipped = [v for v in controls if v.outcome == "skipped"
               and not v.skip_reason.startswith(_NOT_RUN)]
    fired = [v for v in controls if v.outcome == "pass"]
    code = _selftest_code(broken=len(broken), baselines_failed=len(failed_rows),
                          exercised=len(fired) + len(broken),
                          allow_empty=args.allow_empty)
    written = _junit_write(junit, lambda: report.render_selftest_junit(
        controls, exit_code=code, when=now, baselines=baselines))

    if args.json:
        _dump({"mode": "pack",
               "tier": ceiling,
               "packs": [{"name": p["name"], "dir": p["dir"], "origin": p["origin"],
                          "gates": list(p["chosen"]),
                          "fired": sum(1 for v in p.get("controls", []) if v.ok),
                          "broken": [_bare(v.gate) for v in p.get("controls", [])
                                     if v.outcome in ("fail", "error")],
                          "skipped": [_bare(v.gate) for v in p.get("controls", [])
                                      if v.outcome == "skipped"
                                      and not v.skip_reason.startswith(_NOT_RUN)],
                          "baselines_failed": [v.gate for v in p.get("baselines", [])
                                               if v.outcome in ("fail", "error")],
                          "problems": list(p["problems"])} for p in plans],
               "notes": notes,
               "selftests": [_verdict_row(v) for v in controls],
               "baselines": [_verdict_row(v) for v in baselines],
               "broken": [_bare(v.gate) for v in broken],
               "skipped": [_bare(v.gate) for v in skipped],
               "baselines_failed": [label for label, _ in failed_rows],
               "counts": {"controls": len(fired) + len(broken) + len(skipped),
                          "fired": len(fired), "broken": len(broken),
                          "skipped": len(skipped), "baselines_failed": len(failed_rows)},
               "allow_empty": bool(args.allow_empty),
               "junit": written,
               "ok": code == 0})
        return code

    for note in notes:
        _say(f"note: {note}")
    for plan in plans:
        line = _pack_line(plan, filtered=bool(selection), ceiling=ceiling)
        if line:
            _say(line)
    _say(_selftest_summary(len(fired) + len(broken) + len(skipped), elapsed,
                           len(fired), len(broken), len(skipped)))
    if not fired and not broken:
        _say_empty(args.allow_empty, "no pack to demonstrate" if not targets
                   else "every control skipped" if skipped else "no gate selected")
    if broken:
        _say("these gates cannot be trusted — each one failed to reject its own "
             "known-bad input, or lost its control:")
        for verdict in broken:
            _say(f"{_tag('FAIL')} {_bare(verdict.gate)} ({verdict.pack}) — "
                 f"{verdict.detail or verdict.error or 'no detail'}")
    if failed_rows:
        _say(f"{len(failed_rows)} baseline(s) failed:")
        for label, why in failed_rows:
            _say(f"{_tag('FAIL')} {label} — {why}")
    return code


def _read_back(plan: dict[str, Any], shown: Any, tier: int) -> None:
    """Turn one `packs.demonstrate` result into per-gate control and baseline verdicts.

    `demonstrate` reports `"<gate id>: <why>"` per defect — `control …`
    (`packs._CONTROL_PREFIX`) for the control and the seal probe, anything else for
    the baseline — `"<gate id> (<reason>)"` per tooling skip, and a count of
    controls run. A problem that names no gate of this pack is the pack's own (no
    baseline file, a gate file that will not import). Sets `plan["controls"]`,
    `plan["baselines"]`, and appends to `plan["problems"]`.
    """
    name = plan["name"]
    in_scope = [s for s in plan["specs"] if int(s.tier) <= tier]
    ids = {s.id for s in in_scope}
    control_bad: dict[str, str] = {}
    baseline_bad: dict[str, str] = {}
    for line in shown.problems:
        gate_id, sep, why = line.partition(": ")
        if sep and gate_id in ids:
            bucket = control_bad if why.startswith(packs._CONTROL_PREFIX) else baseline_bad
            bucket.setdefault(gate_id, why)
        else:
            plan["problems"].append(line)
    tooling: dict[str, str] = {}
    for entry in shown.skipped:
        gate_id, sep, reason = entry.partition(" (")
        if sep and gate_id in ids:
            tooling[gate_id] = reason[:-1] if reason.endswith(")") else reason
        else:
            plan["problems"].append(f"skipped an unknown gate: {entry}")

    # The counts must add up before a single control is called fired. Every gate
    # in scope either skipped for tooling or ran its control; a demonstration
    # that stopped early (no baseline file) or dropped a gate ran fewer.
    expected = len(ids) - len(tooling)
    complete = shown.ran == expected
    if not complete and not plan["problems"]:
        plan["problems"].append(
            f"the demonstration ran {shown.ran} control(s) of the {expected} this "
            f"pack's gates call for — none of its controls is counted as fired")

    controls: list[Verdict] = []
    baselines: list[Verdict] = []
    for spec in in_scope:
        if spec.id not in plan["chosen"]:
            continue
        common = {"pack": spec.pack or name, "tier": Tier(int(spec.tier))}
        if spec.id in control_bad:
            control = Verdict(gate=f"{spec.id}#selftest", passed=False,
                              detail=control_bad[spec.id], **common)
        elif spec.id in tooling:
            control = Verdict(gate=f"{spec.id}#selftest", skipped=True,
                              skip_reason=tooling[spec.id], **common)
        elif not complete:
            control = Verdict(gate=f"{spec.id}#selftest", skipped=True,
                              skip_reason=f"{_NOT_RUN}{name} could not be demonstrated",
                              **common)
        else:
            control = Verdict(gate=f"{spec.id}#selftest", passed=True,
                              detail="fired on its known-bad input, and again with an "
                                     "empty host", **common)
        controls.append(control)

        if spec.id in baseline_bad:
            baseline = Verdict(gate=spec.id, passed=False, detail=baseline_bad[spec.id],
                               **common)
        elif spec.id in tooling:
            baseline = Verdict(gate=spec.id, skipped=True, skip_reason=tooling[spec.id],
                               **common)
        elif not complete:
            baseline = Verdict(gate=spec.id, skipped=True,
                               skip_reason=f"{_NOT_RUN}{name} could not be demonstrated",
                               **common)
        else:
            baseline = Verdict(gate=spec.id, passed=True,
                               detail=f"passed {packs.SELFTEST_DIR}/{packs.BASELINE_NAME}",
                               **common)
        baselines.append(baseline)
    plan["controls"] = controls
    plan["baselines"] = baselines


def _pack_line(plan: dict[str, Any], *, filtered: bool, ceiling: int) -> str:
    """One pack's line: `[ok  ] beam-analytic (bundled) : 8 fired`.

    `(origin)` because a pack that is not the one you are editing looks exactly
    like one that is (`packs.origin_of`). Nothing for a pack a gate filter left
    untouched: it was not part of this run.
    """
    controls = plan.get("controls", [])
    problems = plan["problems"]
    if not controls and not problems:
        if filtered:
            return ""
        what = "no gates" if not plan["specs"] else f"no gate at or below tier {ceiling}"
        return f"{_tag('skip')} {plan['name']} ({plan['origin']}) : {what}"
    fired = sum(1 for v in controls if v.outcome == "pass")
    broken = sum(1 for v in controls if v.outcome in ("fail", "error"))
    tooling = sum(1 for v in controls if v.outcome == "skipped"
                  and not v.skip_reason.startswith(_NOT_RUN))
    failed = sum(1 for v in plan.get("baselines", []) if v.outcome in ("fail", "error"))
    failed += len(problems)
    bits = [f"{fired} fired"]
    if broken:
        bits.append(f"{broken} BROKEN")
    if tooling:
        bits.append(f"{tooling} skipped (tooling)")
    if failed:
        bits.append(f"{failed} baseline(s) failed")
    tag = "FAIL" if broken or failed else ("ok" if fired else "skip")
    return f"{_tag(tag)} {plan['name']} ({plan['origin']}) : {', '.join(bits)}"


# --------------------------------------------------------------------------- #
# report / why / decide
# --------------------------------------------------------------------------- #
def cmd_report(args: argparse.Namespace) -> int:
    """The readiness report: what is proven, what is not, and why.

    Prints the markdown by default because the markdown IS the deliverable — the
    thing you hand someone before they spend money. `--write` puts it at
    `docs/readiness.md`; `atompipe status` is the compressed terminal view of the
    same ledger.

    `strict=False` here is deliberate — a report must render on a machine where
    the packs are not installed — but the *failures it swallows* were being
    thrown away with the underscore, and so was the model-load error. The result
    was the worst artefact this tool can produce: a green-looking deliverable
    generated from a registry that is missing a pack, where every claim that
    pack covered reads UNCLAIMED because of an import error rather than because
    nobody wrote the gate. That is a lie about coverage, in the one document
    whose entire value is refusing to claim what it has not proven (rule 9), so
    both failures are now named in a banner directly under the headline.
    """
    root = _root(args)
    ledger = store.load(root)
    registry, problems = _registry(root, ledger, strict=False)
    model, projection, model_error = _projection_safe(root, ledger)
    view, resolution = _resolved(root, ledger, registry, projection, model_error,
                                 now=utcnow_iso(), model=model)
    stale_gates = resolution.stale_gates
    banner = _load_failure_banner(problems, model_error)

    if args.json:
        resolved = claims.statuses(view, registry=registry, stale_gates=stale_gates)
        _dump({
            "summary": claims.summarise(view, registry, stale_gates=stale_gates),
            "claims": {cid: str(status) for cid, status in resolved.items()},
            "coverage": claims.coverage(view, registry),
            "gaps": [need.to_dict() for need in claims.find_gaps(view, registry)],
            "verdicts": [_resolved_row(v, resolution) for v in view.verdicts],
            "stale": bool(stale_gates),
            "stale_reason": _stale_summary(resolution),
            "stale_gates": _stale_gate_list(resolution),
            "problems": problems,
            "model_error": model_error,
            "coverage_understated": bool(banner),
        })
        return 0

    if args.write:
        with _lock(root):
            path = report.write_report(root, view, registry, stale_gates=stale_gates)
        _say(rel(path, root))
        # To stderr, because the one line on stdout is the path and scripts read
        # it. A caveat that breaks `report --write` as a shell substitution would
        # get silenced by whoever hit it, which is the opposite of the point.
        for line in banner:
            _warn(line)
        return 0
    sys.stdout.write(_with_banner(
        report.render_markdown(view, registry, stale_gates=stale_gates,
                               model_error=model_error, root=root), banner))
    return 0


def _load_failure_banner(problems: list[str], model_error: str) -> list[str]:
    """The "this report understates coverage" banner, or `[]` when nothing failed.

    Counted rather than merely listed: "2 gate sources failed to load" is the
    sentence that makes a reader distrust the UNCLAIMED rows, and a reader who
    sees only a pack name at the bottom of a wall of markdown will not connect
    the two.
    """
    if not problems and not model_error:
        return []
    lines = ["> **This report is incomplete.** "
             f"{len(problems)} gate source(s) failed to load"
             + (" and the model did not load" if model_error else "")
             + ", so coverage below is UNDERSTATED: a claim may read UNCLAIMED "
               "because its gate never registered, not because no gate exists."]
    lines += [f"> - {problem}" for problem in problems]
    if model_error:
        lines.append(f"> - model: {model_error.splitlines()[0]}")
    return lines


def _with_banner(markdown: str, banner: list[str]) -> str:
    """Insert `banner` immediately after the report's `# ` headline.

    After rather than before, so the document still opens with its title and
    stays a valid readiness report; immediately after rather than at the end,
    because a caveat below the PROVEN table is a caveat nobody reads before
    quoting the PROVEN table.
    """
    if not banner:
        return markdown
    lines = markdown.splitlines()
    cut = next((i + 1 for i, line in enumerate(lines) if line.startswith("# ")), 0)
    merged = lines[:cut] + ["", *banner] + lines[cut:]
    return "\n".join(merged) + "\n"


def cmd_why(args: argparse.Namespace) -> int:
    """One parameter or claim's whole history, instead of the whole decision log.

    The context-window win: value, what it derives from, the rationale, every
    rejected alternative with the concrete reason it lost, the gates that protect
    it, the evidence that grounds it, and the decisions that moved it.

    Rendered from `_resolved`'s view (cli:H3): the gate lines are the effective
    verdicts, the claim's gates its registry coverage, a parameter's gates the
    ones that read it when they last ran. What slipped through before: this
    loaded the ledger alone — no registry, no cache — so it quoted whatever
    verdict the last `check` had written into the ledger, current or not.
    """
    root = _root(args)
    ledger = store.load(root)
    registry, _problems = _registry(root, ledger, strict=False)
    model, projection, model_error = _projection_safe(root, ledger)
    view, _resolution = _resolved(root, ledger, registry, projection, model_error,
                                  now=utcnow_iso(), model=model)
    text = decisions.why(view, args.name)
    if args.json:
        _dump({"name": args.name, "why": text})
        return 0
    sys.stdout.write(text if text.endswith("\n") else text + "\n")
    return 0


def cmd_decide(args: argparse.Namespace) -> int:
    """Record a decision — including, above all, what LOST and why.

    `--rejected "0.5 mm|the router could not close that net through the congested
    corridor"` is the highest-value thing this whole system stores. Without it
    every fresh context window re-proposes every settled number. A rejection with
    no reason is refused by `decisions.add`, which is why the flag takes a pair.
    """
    root = _root(args)
    when = args.when or utcnow_iso()
    rejected: list[Any] = []
    for raw in args.rejected or ():
        parts = [piece.strip() for piece in raw.split("|")]
        # A single-piece value is passed through as a string so `decisions.add`
        # raises its own sentence about reasonless rejections, which says more
        # than anything this parser could.
        rejected.append(tuple(parts) if len(parts) >= 2 else raw)

    with _lock(root):
        ledger = store.load(root)
        decision = decisions.add(
            ledger,
            title=args.title,
            summary=args.summary,
            when=when,
            rejected=rejected,
            params_changed=_collect(args.param),
            claims_changed=_collect(args.claim),
            body=args.body or "",
            evidence=_collect(args.evidence),
        )
        store.save(root, ledger)
        log_path = decisions.write_log(root, ledger)

    if args.json:
        _dump(dict(decision.to_dict(), log=rel(log_path, root)))
        return 0
    _say(f"{decision.id}  {decision.title}  ({decision.when})")
    for item in decision.rejected:
        _say(f"  rejected {item.value}: {item.why}")
    _say(f"  log: {rel(log_path, root)}")
    return 0


# --------------------------------------------------------------------------- #
# packs
# --------------------------------------------------------------------------- #
def cmd_packs_list(args: argparse.Namespace) -> int:
    """Every discoverable pack, with where it came from and whether it is installed.

    Installed is a PROJECT fact (`meta.packs`), available is a filesystem fact.
    Keeping the two visibly apart answers the two questions people actually have:
    "why is this gate not running" (available, not installed) and "where did this
    gate come from" (installed, and shadowed by a copy earlier on the path).
    """
    root = _root(args)
    ledger = store.load(root)
    installed = packs.installed(root, ledger=ledger)
    found = packs.discover_dirs(root)

    if args.json:
        _dump({
            "installed": installed,
            "available": [{"name": manifest.name, "dir": directory,
                           "installed": manifest.name in installed,
                           **manifest.to_dict()}
                          for directory, manifest in found],
            "search_paths": packs.search_paths(root, existing_only=False),
        })
        return 0

    if not found:
        _say("no packs found. Searched:")
        for path in packs.search_paths(root, existing_only=False):
            _say(f"  {path}")
        return 0
    for directory, manifest in found:
        mark = "*" if manifest.name in installed else " "
        # WHERE a pack came from, always. A pack that is not the one you are editing
        # looks exactly like a pack that is: a tester pulled a fix, watched the gate
        # fail to appear, and lost ten minutes before finding that the live copy was
        # the one inside the installed wheel. Both this command and `doctor` showed
        # the stale pack without a hint that a second copy existed.
        origin = packs.origin_of(directory, root)
        _say(f"{mark} {manifest.name:<18} {manifest.version:<8} "
             f"t{int(manifest.max_tier)}  [{origin}]  {manifest.description}")
        if args.verbose:
            _say(f"    {directory}")
            _say(f"    settles: {', '.join(manifest.settles) or '(nothing declared)'}")
    missing = [name for name in installed if not packs.find(name, root)]
    _say(f"* = installed in this project ({len(installed)} of {len(found)} discoverable)")
    origins = sorted({packs.origin_of(d, root) for d, _m in found})
    if origins:
        _say(f"  loaded from: {', '.join(origins)}"
             + ("   (`--verbose` for the exact path of each)" if not args.verbose else ""))
    if "bundled" in origins:
        _say(f"  bundled packs live in {packs.BUNDLED_PACKS} — editing a checkout "
             f"elsewhere will not change them until you reinstall")
    for name in missing:
        _say(f"{_tag('FAIL')} {name} is in meta.packs but was not found on any search path")
    return 0


def cmd_packs_show(args: argparse.Namespace) -> int:
    """Tier 2 (`PACK.md`) for one pack, or tier 3 with `--ref <name>`.

    Separate commands for separate tiers on purpose: reading every installed
    pack's prose would cost more context than the entire rest of the project
    state, which is what the three-tier split exists to prevent.
    """
    root = _root(args)
    if args.ref:
        text = packs.reference_doc(args.name, args.ref, root)
    else:
        text = packs.pack_doc(args.name, root)
    manifest = packs.read_manifest(packs.find(args.name, root) or "")

    if args.json:
        _dump({"manifest": manifest.to_dict(),
               "references": packs.references(args.name, root),
               "doc": text})
        return 0
    if not args.ref:
        _say(f"# {manifest.name} {manifest.version} — {manifest.description}")
        refs = packs.references(args.name, root)
        if refs:
            _say(f"references (tier 3): {', '.join(refs)}  "
                 f"— `atompipe packs show {args.name} --ref <name>`")
        _say("")
    sys.stdout.write(text if text.endswith("\n") else text + "\n")
    return 0


def cmd_packs_validate(args: argparse.Namespace) -> int:
    """Run the same checks CI runs on a pack. Exit 1 if anything is wrong.

    Accepts a pack name or a directory, so a pack being written in place can be
    validated before it is anywhere a search path will find it.
    """
    root = store.find_root(getattr(args, "dir", None))
    target = args.name
    pack_dir = target if os.path.isdir(target) else (packs.find(target, root) or "")
    if not pack_dir:
        raise AtompipeError(f"no pack {target!r} on any search path, and no such directory")
    # What `demonstrate` could not show here, printed rather than dropped: a gate
    # whose tools are absent was not demonstrated, and one above the tier was not
    # run. Neither is a problem (CI has no solvers), and neither may pass for a
    # demonstration either — silence would let it (S-12, tests:H8).
    notes: list[str] = []
    problems = packs.validate(pack_dir, notes=notes)

    if args.json:
        _dump({"pack": pack_dir, "problems": problems, "notes": notes,
               "ok": not problems})
        return 1 if problems else 0
    for problem in problems:
        _say(f"{_tag('FAIL')} {problem}")
    for note in notes:
        _say(f"note: {note}")
    if not problems:
        _say(f"{_tag('ok')} {pack_dir}: publishable")
        return 0
    _say(f"{len(problems)} problem(s) in {pack_dir}")
    return 1


def cmd_packs_add(args: argparse.Namespace) -> int:
    """Opt this project into a pack: append it to `meta.packs`.

    Installed is a project decision, not a filesystem accident — a pack sitting
    in `~/.atompipe/packs` is available to every project on the machine and must
    not start contributing gates to this one until the ledger says so. The pack
    is loaded immediately so a broken one fails here, where the user is looking,
    rather than in the middle of the next `check`.
    """
    root = _root(args)
    with _lock(root):
        ledger = store.load(root)
        added: list[str] = []
        for name in args.names:
            if not packs.find(name, root):
                looked = "\n  ".join(packs.search_paths(root, existing_only=False))
                raise AtompipeError(f"no pack {name!r} found. Searched:\n  {looked}")
            if name in (ledger.meta.packs or []):
                continue
            ledger.meta.packs = list(ledger.meta.packs or []) + [name]
            added.append(name)
        registry = gates.Registry()
        specs = packs.load_all_gates(ledger.meta.packs, registry, root)
        store.save(root, ledger)

    if args.json:
        _dump({"added": added, "packs": ledger.meta.packs,
               "gates": [spec.id for spec in specs]})
        return 0
    _say(f"packs: {', '.join(ledger.meta.packs) or '(none)'}")
    _say(f"{len(specs)} gate(s) now available: {', '.join(s.id for s in specs[:8])}")
    return 0


def cmd_packs_remove(args: argparse.Namespace) -> int:
    """Drop a pack from `meta.packs`. Its past verdicts stay in the ledger.

    Deliberately: a verdict is a record of something that was true when it ran,
    and deleting the evidence because the tooling was uninstalled would make the
    history lie. The claims those gates covered simply stop being covered, which
    `status` will report as UNCLAIMED — the honest answer.
    """
    root = _root(args)
    with _lock(root):
        ledger = store.load(root)
        before = list(ledger.meta.packs or [])
        ledger.meta.packs = [name for name in before if name not in set(args.names)]
        store.save(root, ledger)
    dropped = [name for name in before if name not in ledger.meta.packs]

    if args.json:
        _dump({"removed": dropped, "packs": ledger.meta.packs})
        return 0
    _say(f"removed {', '.join(dropped) or '(nothing)'}; "
         f"packs now: {', '.join(ledger.meta.packs) or '(none)'}")
    _say("past verdicts from those gates are kept — `atompipe status` will now show "
         "the claims they covered as uncovered")
    return 0


# --------------------------------------------------------------------------- #
# model
# --------------------------------------------------------------------------- #
def cmd_model(args: argparse.Namespace) -> int:
    """Load the model, project it, and say what that projection looks like.

    `--write` produces `.atompipe/model.json`, the diffable view: sorted keys,
    one value per line, so a parameter change reads as `deflection 0.70 -> 0.47`
    in `git diff` instead of as one enormous line. `--set-entry` records which
    file is the model — the loader deliberately never guesses, because "the only
    .py in model/" works right up until there are two.
    """
    root = _root(args)

    if args.set_entry:
        with _lock(root):
            ledger = store.load(root)
            candidate = os.path.join(root, args.set_entry)
            if not os.path.exists(candidate) and not os.path.isabs(args.set_entry):
                raise AtompipeError(
                    f"{args.set_entry} does not exist (looked at {candidate}) — "
                    f"create the model first, then record it")
            ledger.meta.model_entry = args.set_entry
            store.save(root, ledger)
    ledger = store.load(root)

    model, projection = _projection(root, ledger, entry=args.entry)
    if projection is None:
        raise AtompipeError(
            "no model entry recorded — `atompipe model --set-entry model/<thing>.py` "
            "(the model is a dataclass CONFIG plus build(config) -> dict)")

    digest = modelio.model_hash(projection)
    undocumented = modelio.undocumented_params(model)

    # Plain `atompipe model` is a READ. It used to take the build lock and save
    # the ledger unconditionally, three lines below the comment promising it did
    # not — so printing the projection while a sweep was in flight contended for
    # the lock, and a `model` in a loop rewrote the ledger's mtime forever. The
    # write now happens only for the two spellings that are already writes.
    #
    # `--set-entry` and `--write` both prime the ledger's parameter table from
    # the model, so `atompipe why <param>` answers straight after either one
    # rather than only after the first gate sweep.
    written = ""
    if args.set_entry or args.write:
        with _lock(root):
            fresh = store.load(root)
            modelio.sync_params(fresh, model)
            orphans = modelio.orphan_params(fresh, model)
            store.save(root, fresh)
            if args.write:
                written = modelio.write_projection(root, projection)
    else:
        # Lock-free: `orphan_params` only compares two in-memory lists, and the
        # ledger already in hand is the one we would have re-read anyway.
        orphans = modelio.orphan_params(ledger, model)

    if args.json:
        _dump({"entry": model.entry, "hash": digest, "projection": projection,
               "params": [param.to_dict() for param in model.params],
               "undocumented": undocumented,
               "written": rel(written, root) if written else ""})
        return 0

    config = projection.get("config") or {}
    derived = projection.get("derived") or {}
    _say(f"model: {model.entry}  hash {digest}")
    if orphans:
        _say(f"  {len(orphans)} ledger param(s) the model no longer defines: "
             f"{', '.join(orphans[:6])}"
             + ("..." if len(orphans) > 6 else "")
             + "  — renamed, or dropped without a decision entry?")
    _say(f"  {len(config)} config value(s), {len(derived)} derived value(s), "
         f"{len(model.params)} param record(s)")
    if undocumented:
        _say(f"  no rationale: {', '.join(undocumented)} — a number with no rationale "
             f"gets re-litigated by every fresh reader")
    if written:
        _say(f"  wrote {rel(written, root)}")
    return 0


# --------------------------------------------------------------------------- #
# site
# --------------------------------------------------------------------------- #
def _load_viewgens(directory: str, registry: Any, *,
                   pack: str, root: str) -> list[Any]:
    """Import `<directory>/*.py` into a ViewRegistry; return the specs it added.

    Deliberately the same shape as `gates.load_project_gates`, because a viewgen is a
    gate's twin: same directory convention, same `sys.path` handling, same
    `PACK`/`PACK_DIR` globals, same "an exception here is the pack author's
    problem and must not print as a spine traceback". A pack author who has
    written `gates/clash.py` writes `views/assembly.py` with nothing new to
    learn, which is the whole reason `site.py` mirrors `gates.py` field for
    field.

    Two details carry weight:

    * **Module names are salted with a hash of the file's absolute path**, so a
      pack's `views/assembly.py` and a project's `views/assembly.py` are
      different modules. Without the salt the second one to load silently reuses
      the first one's code while every message names the second file.
    * **Already-imported modules are re-registered from `fn.view_spec`**, not
      re-executed. Python will not run a module twice, so a second build in one
      process (a test, an agent that builds after every edit, `site status`
      after `site build`) would decorate nothing and find an EMPTY registry —
      reporting "this project has no views" about a project with six. The
      decorator attaches `view_spec` to the function precisely so a loader can
      recover the registration without re-running the module.

    Registering the same function under the same id twice is a no-op in
    `ViewRegistry`, so the walk is safe on a freshly executed module too, and
    the two paths stay one path rather than two that can drift.
    """
    if not os.path.isdir(directory):
        return []
    try:
        names = sorted(os.listdir(directory))
    except OSError as exc:
        raise AtompipeError(f"cannot read {rel(directory, root)}: {exc}") from exc
    files = [os.path.join(directory, name) for name in names
             if name.endswith(".py") and not name.startswith("_")]
    if not files:
        return []

    where = f"pack {pack!r}: " if pack else ""
    before = set(registry.ids())
    # The directory ABOVE views/ — the pack root, or the project root. On
    # sys.path so a viewgen can `import _geom` and share the helper the gate
    # next door already uses, and set as PACK_DIR so pack-relative fixtures
    # resolve the same way they do for gates.
    base = os.path.dirname(directory)
    sys.path.insert(0, base)
    try:
        with site.use_view_registry(registry):
            for path in files:
                module = _import_viewgen_module(
                    path, pack=pack, base=base, root=root, where=where)
                # The attribute walk, always — see the docstring. On a fresh
                # import it re-registers what the decorator just registered
                # (a no-op); on a reused one it is the only thing that registers
                # anything at all.
                for value in list(vars(module).values()):
                    spec = getattr(value, "view_spec", None)
                    if not isinstance(spec, site.ViewSpec) or not callable(value):
                        continue
                    try:
                        registry.register(spec, value)
                    except AtompipeError as exc:
                        raise AtompipeError(
                            f"{where}{rel(path, root)}: {exc}") from exc
    finally:
        try:
            sys.path.remove(base)       # removes OUR insertion (the first match)
        except ValueError:              # pragma: no cover - a viewgen mangled sys.path
            pass
    return [spec for spec in registry.specs() if spec.id not in before]


def _import_viewgen_module(path: str, *, pack: str, base: str,
                           root: str, where: str) -> Any:
    """Import one `views/*.py`, or raise an AtompipeError naming the file."""
    stem = os.path.splitext(os.path.basename(path))[0]
    module_name = f"atompipe_views_{stem}_{short_hash(os.path.abspath(path), 8)}"
    existing = sys.modules.get(module_name)
    if existing is not None:
        return existing                 # ordinary import semantics; see _load_viewgens

    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:         # pragma: no cover - defensive
        raise AtompipeError(f"{where}{rel(path, root)}: no import machinery accepted this file")
    module = importlib.util.module_from_spec(spec)
    # Set BEFORE execution, which `module_from_spec` makes possible by handing
    # the module over before the loader runs it. `@viewgen` reads `PACK` off the
    # defining module at decoration time so twenty decorators do not each retype
    # the pack name — a mistyped one orphans a view in the site's grouping with
    # nothing reporting it. `PACK_DIR` is the pack's own directory, for a viewgen
    # that has to find a template or a material table shipped beside it; for a
    # project's `views/` it is the project root, which is where those things are.
    module.PACK = pack
    module.PACK_DIR = base
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except AtompipeError as exc:
        # A registry refusal (duplicate view id, unknown kind) is already phrased
        # for a human. Keep the phrasing and add the location.
        sys.modules.pop(module_name, None)
        raise AtompipeError(f"{where}{rel(path, root)}: {exc}") from exc
    except KeyboardInterrupt:
        sys.modules.pop(module_name, None)
        raise
    except BaseException as exc:
        # BaseException on purpose, exactly as `packs.load_gates` does it: a view
        # module whose exporter guards its own absence with `sys.exit()` at
        # import time must not be able to end the command with that exit code.
        sys.modules.pop(module_name, None)
        raise AtompipeError(
            f"{where}{rel(path, root)} failed to import: {type(exc).__name__}: {exc}"
        ) from exc
    return module


def _view_registry(root: str, ledger: Ledger, *,
                   strict: bool = True) -> tuple[Any, list[str]]:
    """Load every viewgen this project can see. Returns `(registry, problems)`.

    A FRESH registry per call, where `_registry` reuses the module-level gate
    one. The asymmetry is deliberate: gates are swept once per process, but a
    site gets built, then inspected by `site status`, then built again after an
    edit, and a shared registry would carry one project's views into the next
    build in the same process. A view leaking across projects is not a tidiness
    problem — every `Locator` addresses a view by a bare string, so an inherited
    `assembly` silently answers another project's locators.

    `strict=False` turns a broken view module into a reported problem instead of
    an exception, for `site status`, which is what you run when the site is
    already wrong.
    """
    registry = site.ViewRegistry()
    problems: list[str] = []
    sources: list[tuple[str, str, str]] = []
    for name in packs.installed(root, ledger=ledger):
        pack_dir = packs.find(name, root)
        if pack_dir:
            sources.append((f"pack {name}", os.path.join(pack_dir, VIEWGEN_DIR), name))
        # A pack in meta.packs that is not on any search path is already a FAIL
        # row in `status` and `doctor`; repeating it here would be a second
        # opinion about the same fact.
    sources.append(("project views", os.path.join(root, VIEWGEN_DIR), ""))

    for label, directory, pack in sources:
        try:
            _load_viewgens(directory, registry, pack=pack, root=root)
        except AtompipeError as exc:
            if strict:
                raise
            problems.append(f"{label} did not load: {exc}")
    return registry, problems


def _site_state(root: str) -> dict:
    """What is on disk under `site/`, read-only and running nothing.

    Shared by `site status`, `status` and `doctor` so the three cannot drift
    into three opinions about whether the site is current — which is the
    property the site itself exists to have.

    Staleness here is the SITE's staleness (was the ledger written after
    `state.json`?), which is a different question from the verdicts' staleness
    (has the model moved since the sweep?). Both are reported, separately,
    because the fixes are different commands: `atompipe site build` for the
    first and `atompipe check` for the second. Collapsing them into one "stale"
    flag sends half the readers to the wrong one.
    """
    site_dir = os.path.join(root, site.SITE_DIR)
    state_path = os.path.join(site_dir, site.DATA_DIR, site.STATE_NAME)
    vendor_dir = os.path.join(site_dir, site.VENDOR_DIR)
    assets_dir = os.path.join(site_dir, site.ASSETS_DIR)

    info: dict[str, Any] = {
        "present": os.path.isdir(site_dir),
        "dir": site.SITE_DIR,
        "built": False,
        "state": rel(state_path, root),
        "built_at": "",
        "age": "",
        "error": "",
        "views": [],
        "locator_problems": [],
        "stale": False,
        "stale_reason": "",
        "verdicts_stale": False,
        "verdicts_stale_reason": "",
        "assets": 0,
        "asset_bytes": 0,
        "vendored": False,
        "vendor_files": 0,
        "three_version": site.THREE_VERSION,
    }
    if not info["present"]:
        info["stale_reason"] = "no site/ directory — `atompipe site init`"
        return info

    total = size = 0
    for walk_dir, _dirnames, filenames in os.walk(assets_dir):
        for name in filenames:
            total += 1
            try:
                size += os.path.getsize(os.path.join(walk_dir, name))
            except OSError:             # raced a rebuild; a byte count is not worth failing
                pass
    info["assets"], info["asset_bytes"] = total, size

    if os.path.isdir(vendor_dir):
        vendored = [p for p in site.vendor_urls()
                    if os.path.isfile(os.path.join(site_dir, p.replace("/", os.sep)))]
        info["vendor_files"] = len(vendored)
        # Partially vendored is NOT vendored. A vendor/ holding three of four
        # files shadows the CDN import map and 404s on the fourth, offline and
        # online alike, which reads as "three.js is broken" rather than as "the
        # download was interrupted".
        info["vendored"] = len(vendored) == len(site.vendor_urls())

    if not os.path.isfile(state_path):
        info["stale_reason"] = "never built — `atompipe site build`"
        return info

    try:
        payload = read_json(state_path, None)
    except AtompipeError as exc:
        info["error"] = str(exc)
        return info
    if not isinstance(payload, dict):
        info["error"] = f"{rel(state_path, root)} is not a state document"
        return info

    meta = payload.get("meta") or {}
    info["built"] = True
    info["built_at"] = str(meta.get("built") or "")
    info["age"] = _age(info["built_at"])
    info["views"] = [{"id": v.get("id", ""), "kind": v.get("kind", ""),
                      "src": v.get("src", ""), "pack": v.get("pack", "")}
                     for v in (payload.get("views") or [])]
    info["locator_problems"] = list(payload.get("locator_problems") or [])
    info["verdicts_stale"] = bool(meta.get("stale"))
    info["verdicts_stale_reason"] = str(meta.get("stale_reason") or "")
    info["claims"] = len(payload.get("claims") or [])
    info["verdicts"] = len(payload.get("verdicts") or [])

    # mtime, not the two timestamps: `built` is when the build ran and the
    # ledger carries no "written at" field at all, so comparing the files
    # themselves is the only comparison that is a fact rather than an inference.
    ledger_path = store.ledger_path(root)
    try:
        if os.path.getmtime(ledger_path) > os.path.getmtime(state_path):
            info["stale"] = True
            info["stale_reason"] = ("the ledger has changed since the site was built "
                                    "— `atompipe site build`")
    except OSError:                     # pragma: no cover - the ledger was just read
        pass
    if not info["stale"]:
        info["stale_reason"] = "current with the ledger"
    return info


def _site_brief(info: dict) -> dict:
    """The handful of site fields `status --json` and `doctor` carry.

    Trimmed rather than embedded whole: `atompipe status --json` is read by
    agents with a context budget, and a site's full view list and locator
    problem records belong to `atompipe site status`, which is the command that
    was asked about them.
    """
    return {
        "present": info["present"], "built": info["built"],
        "built_at": info["built_at"], "stale": info["stale"],
        "views": len(info["views"]), "dangling_locators": len(info["locator_problems"]),
        "vendored": info["vendored"], "error": info["error"],
    }


def _site_line(info: dict) -> str:
    """One line about the site for `atompipe status`. Leads with what is wrong."""
    if info["error"]:
        return f"site: {info['state']} is unreadable — {info['error']}"
    if not info["built"]:
        return "site: scaffolded, never built — `atompipe site build`"
    dangling = len(info["locator_problems"])
    bits = [f"{len(info['views'])} view(s)"]
    if dangling:
        bits.append(f"{dangling} dangling locator(s)")
    if info["stale"]:
        bits.append("STALE: the ledger has moved — `atompipe site build`")
    elif info["age"]:
        bits.append(f"built {info['age']}")
    return f"site: {', '.join(bits)}"


def cmd_site_init(args: argparse.Namespace) -> int:
    """Scaffold `site/`, refusing to overwrite the shell.

    `index.html` is the one file this will not replace without `--force`: the
    contract calls it *hackable, yours, never regenerated after init*, and
    someone who spent an afternoon on their project's page must not lose it to
    the command they ran to pick up a renderer fix. Everything else in the
    template IS refreshed, because `app.js` and `style.css` are ours and a
    project that could never receive a fix would be stuck with the bugs of the
    day it was created.
    """
    root = _root(args)
    with _lock(root):
        written = site.scaffold(root, force=bool(args.force))

    if args.json:
        _dump({"root": root, "site": site.SITE_DIR, "wrote": written,
               "next": ["atompipe site build", "atompipe site serve"]})
        return 0
    _say(f"scaffolded {site.SITE_DIR}/ in {root}")
    for path in written:
        _say(f"  {path}")
    _say("")
    _say(f"  {site.SITE_DIR}/index.html is yours — edit it; "
         f"`site init` will not overwrite it again")
    _say("next: atompipe site build   (writes data/ and assets/), then "
         "atompipe site serve")
    return 0


def cmd_site_build(args: argparse.Namespace) -> int:
    """Run the viewgens, collect the ledger, and write `site/data/` + `site/assets/`.

    **This never runs gates.** It renders the verdict cache as `_resolved`
    judges it and stamps each verdict with its own age. A build that re-ran the cheap gates on the way past
    would publish a page whose tier-0 numbers are ten seconds old beside tier-2
    numbers from last week, under one "built at" stamp, with nothing on the page
    saying which is which. If the results are stale the honest fix is
    `atompipe check`, and the page's job is to make that impossible to miss.

    The gate registry is loaded STRICTLY, unlike `status` and `doctor`. Claim
    coverage and the readiness sentence are resolved against it, so a pack that
    failed to import produces a page full of UNCLAIMED rows that are an artefact
    of an import error rather than a statement about the design — published, in
    the one artifact whose entire job is to be trusted when a gate says
    something is wrong.

    Dangling locators never fail the build and are never silent. A verdict
    addressing a view that does not exist is a gate that believes it is drawing
    and is not, which from the outside looks exactly like a gate that found
    nothing; it is a warning line here and a flag in `--json`, and it rides in
    `state.json` so the page can say it too.
    """
    root = _root(args)
    # Everything under one lock, the way `check` sweeps under one lock, and for
    # two reasons. `clean_assets` deletes every file under site/assets/ that THIS
    # run did not write or reference, so two concurrent builds would each delete
    # the other's output and both would report success. And the ledger is read
    # inside it, so the verdicts that reach the page are the ones on disk at the
    # moment of the build rather than the ones from before a `check` that landed
    # while the viewgens were still importing.
    with _lock(root):
        ledger = store.load(root)
        registry, _ = _registry(root, ledger)
        view_registry, _ = _view_registry(root, ledger)
        model, projection = _projection(root, ledger)
        now = utcnow_iso()
        # The page renders the resolver's answer, the one every other reader
        # prints (`_resolved`, R-5), handed over rather than recomputed: the page
        # and `status` built from one resolution cannot disagree about a gate.
        view, resolution = _resolved(root, ledger, registry, projection, "", now=now,
                                     model=model)
        summary = site.build(root, view, registry, view_registry, model=model,
                             projection=projection, now=now, resolution=resolution)

    problems = summary.get("locator_problems") or []
    counts = summary.get("counts") or {}
    if args.json:
        _dump({**summary, "has_dangling_locators": bool(problems)})
        return 0

    for row in summary["views"]:
        where = row.get("src") or row.get("data_url") or "(inline)"
        _say(f"{_tag('ok')} {row['id']:<20} {row['kind']:<9} {where}")
    for note in summary["viewgens"]:
        # `empty` is not a warning and never appears in summary["warnings"]: a
        # CAD viewgen in a project with no geometry has nothing to draw, and
        # that is a normal outcome rather than a fault. It is still printed,
        # because "why is there no assembly view?" must be answerable here.
        if note["status"] == "empty":
            _say(f"{_tag('none')} {note['view']:<20} {note['kind']:<9} {note['note']}")
    for warning in summary["warnings"]:
        # Unavailable dependencies (named), crashed viewgens, ledger/viewgen id
        # collisions and every dangling locator, in one place. Never filtered:
        # the whole failure mode this list covers is silence.
        _say(f"{_tag('warn')} {warning}")

    if not summary["views"]:
        if not summary["viewgens"]:
            _say("no viewgens are registered — nothing in this project draws anything "
                 "yet. Add one at views/<name>.py, or install a pack that ships them.")
        else:
            _say("no views: every viewgen ran and had nothing to draw.")
        _say("That is not a broken build. The page still carries the claims, the "
             "verdicts, the evidence, the provenance and the readiness sentence — "
             "3D is one view kind, not the point.")
    _say(f"{counts.get('claims', 0)} claim(s), {counts.get('verdicts', 0)} verdict(s), "
         f"{counts.get('views', 0)} view(s), {counts.get('assets', 0)} asset(s) from "
         f"viewgens")
    _say(f"wrote {len(summary['wrote'])} file(s), removed {len(summary['removed'])} "
         f"stale file(s) -> {summary['state']}")
    if problems:
        _say(f"{len(problems)} locator(s) point at something that does not exist. A gate "
             f"that thinks it is drawing and is not looks exactly like a gate that found "
             f"nothing — fix the gate's locator or the view's node names.")
    if summary["stale"]:
        _say(f"verdicts are STALE: {summary['stale_reason']} — `atompipe check`")
    return 0


def cmd_site_serve(args: argparse.Namespace) -> int:
    """Serve `site/` with the standard library and nothing else.

    This is `python3 -m http.server` with three fixes it does not have, each for
    a failure that otherwise looks like a bug in the page:

    * **`Cache-Control: no-store`** — without it a rebuilt `state.json` is served
      from the browser cache and the page shows yesterday's verdicts with
      today's confidence. A site whose entire job is to be current must not have
      a caching layer that makes it silently not be.
    * **an explicit MIME map for `.js` / `.mjs`** — `mimetypes` reads the Windows
      registry, where `.js` is frequently `text/plain`, and a module script
      served as `text/plain` is refused by every browser. The page then renders
      as an unstyled "Loading data/state.json…" with one console line.
    * **threading**, so one slow asset fetch does not block the page that is
      waiting to draw it.

    Binds loopback by default: the page carries an unreleased design, its
    rejected alternatives and its failing claims.
    """
    # Deferred: `http.server` pulls in socketserver, email, html and mimetypes —
    # ~40 ms measured — and every `atompipe check` in an inner loop would pay it
    # to not start a server.
    import errno
    import http.server
    import webbrowser

    root = _root(args)
    site_dir = os.path.join(root, site.SITE_DIR)
    if not os.path.isdir(site_dir):
        raise AtompipeError(
            f"no {site.SITE_DIR}/ directory at {root} — `atompipe site init` first")
    state_path = os.path.join(site_dir, site.DATA_DIR, site.STATE_NAME)
    built = os.path.isfile(state_path)

    class Handler(http.server.SimpleHTTPRequestHandler):
        # A class attribute, not an instance one: SimpleHTTPRequestHandler reads
        # `extensions_map` through the instance but the mapping is shared, and
        # copying the module's dict rather than mutating it keeps this process's
        # fix out of anything else that imports mimetypes.
        extensions_map = {
            **http.server.SimpleHTTPRequestHandler.extensions_map,
            ".js": "text/javascript", ".mjs": "text/javascript",
            ".json": "application/json", ".css": "text/css",
            ".svg": "image/svg+xml", ".glb": "model/gltf-binary",
            ".gltf": "model/gltf+json", ".wasm": "application/wasm",
        }

        def __init__(self, *a: Any, **kw: Any) -> None:
            super().__init__(*a, directory=site_dir, **kw)

        def end_headers(self) -> None:
            self.send_header("Cache-Control", "no-store, max-age=0")
            super().end_headers()

        def log_message(self, fmt: str, *a: Any) -> None:
            # stderr, so `--json` output and a piped stdout stay clean. Kept
            # rather than silenced: a 404 on an asset is the single most common
            # thing to debug here and the log line names it.
            _warn(f"  {self.address_string()} {fmt % a}")

    try:
        server = http.server.ThreadingHTTPServer((args.host, int(args.port)), Handler)
    except OSError as exc:
        if exc.errno == errno.EADDRINUSE:
            raise AtompipeError(
                f"port {args.port} on {args.host} is already in use — something else is "
                f"listening there (another `atompipe site serve`, or any other server). "
                f"Use `-p <other port>`, or `-p 0` to let the OS pick a free one."
            ) from exc
        if exc.errno in (errno.EACCES, errno.EPERM):
            raise AtompipeError(
                f"not allowed to bind {args.host}:{args.port} — ports below 1024 need "
                f"root on most systems. Use `-p 8000` or any port above 1024."
            ) from exc
        raise AtompipeError(
            f"cannot serve on {args.host}:{args.port}: {exc.strerror or exc}") from exc

    host, port = server.server_address[0], server.server_address[1]
    # The bound port, not the requested one: `-p 0` means "any free port", and
    # printing the 0 back would be a URL that goes nowhere.
    url = f"http://{host}:{port}/"

    if args.json:
        _dump({"url": url, "host": host, "port": port, "root": root,
               "serving": rel(site_dir, root), "built": built})
    else:
        _say(f"serving {rel(site_dir, root)} at {url}")
        if not built:
            _say(f"{_tag('warn')} {rel(state_path, root)} does not exist yet — the page "
                 f"will load with no data. Run `atompipe site build`.")
        _say("Ctrl-C to stop")

    if not args.no_browser:
        # Safe to open now: the socket is bound and listening from the
        # constructor, so a request that arrives before `serve_forever` queues
        # rather than being refused.
        try:
            webbrowser.open(url)
        except Exception as exc:        # noqa: BLE001 - a headless box is not an error
            _warn(f"  could not open a browser ({exc}); open {url} yourself")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        # Exit 0: Ctrl-C is how a server is meant to be stopped, and a non-zero
        # code here would fail every script that starts one and stops it.
        if not args.json:
            _say("")
            _say("stopped")
    finally:
        server.server_close()
    return 0


def cmd_site_vendor(args: argparse.Namespace) -> int:
    """Download three.js into `site/vendor/` so the site works offline.

    **All or nothing.** Every file lands in a staging directory first and moves
    into place only once all of them have arrived. A half-written `vendor/`
    shadows the CDN through the import map and then 404s on whatever is missing,
    so an interrupted download would turn a working online site into a broken
    one — and the breakage shows up as "three.js is broken", offline, which is
    the one situation vendoring exists for and the one nobody tests before
    archiving a project.

    A network failure therefore writes nothing, says so, and leaves the CDN path
    exactly as it was. There is no partial success worth keeping.
    """
    root = _root(args)
    site_dir = os.path.join(root, site.SITE_DIR)
    if not os.path.isdir(site_dir):
        raise AtompipeError(
            f"no {site.SITE_DIR}/ directory at {root} — `atompipe site init` first")

    urls = site.vendor_urls()
    staging = tempfile.mkdtemp(dir=site_dir, prefix=".atompipe-vendor-")
    fetched: list[dict] = []
    try:
        for relative, url in sorted(urls.items()):
            if not url.startswith("https://"):
                # Guards a future edit to `vendor_urls`, not today's list: these
                # files are executed by the page, and a plaintext fetch of code
                # the site will run is a rewrite waiting for a coffee shop.
                raise AtompipeError(f"refusing to vendor {relative} over a non-https URL: {url}")
            payload = _fetch_vendor_file(url)
            target = os.path.join(staging, relative.replace("/", os.sep))
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "wb") as fh:
                fh.write(payload)
            fetched.append({"path": posixpath.join(site.SITE_DIR, relative),
                            "url": url, "bytes": len(payload)})

        moved: list[str] = []
        for relative in sorted(urls):
            source = os.path.join(staging, relative.replace("/", os.sep))
            target = os.path.join(site_dir, relative.replace("/", os.sep))
            os.makedirs(os.path.dirname(target), exist_ok=True)
            os.replace(source, target)
            moved.append(posixpath.join(site.SITE_DIR, relative))
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    total = sum(row["bytes"] for row in fetched)
    if args.json:
        _dump({"version": site.THREE_VERSION, "vendor": site.VENDOR_DIR,
               "files": fetched, "bytes": total, "wrote": moved})
        return 0
    _say(f"vendored three.js {site.THREE_VERSION} ({human_bytes(total)})")
    for row in fetched:
        _say(f"  {row['path']:<44} {human_bytes(row['bytes']):>10}")
    _say(f"the import map in {site.SITE_DIR}/index.html now resolves against these "
         f"files instead of the CDN; delete {site.SITE_DIR}/{site.VENDOR_DIR}/ to go back")
    return 0


def _fetch_vendor_file(url: str) -> bytes:
    """One vendored file's bytes, or an AtompipeError that says what to do instead.

    The content sniff is not paranoia. A captive portal or a corporate proxy
    answers 200 with an HTML login page, and an HTML file saved as
    `three.module.js` vendors cleanly, shadows the CDN and then fails at parse
    time in the browser with a syntax error on line 1 of a file the user never
    wrote. Refusing here costs one comparison and turns that into a sentence.
    """
    # Deferred for the same reason as `http.server` in `serve`: no other command
    # opens a socket, and none of them should pay urllib's import to not.
    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen(url, timeout=30) as response:
            status = getattr(response, "status", 200)
            content_type = (response.headers.get("Content-Type") or "").lower()
            payload = response.read()
    except urllib.error.HTTPError as exc:
        raise AtompipeError(
            f"{url} returned HTTP {exc.code} ({exc.reason}). Nothing was written; the "
            f"site still loads three.js from the CDN."
        ) from exc
    except (urllib.error.URLError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        raise AtompipeError(
            f"cannot reach {url}: {reason}. Nothing was written — the site keeps loading "
            f"three.js from the CDN, which is the working path. Re-run `atompipe site "
            f"vendor` when the network is back."
        ) from exc

    if status != 200 or not payload:
        raise AtompipeError(
            f"{url} returned HTTP {status} and {len(payload)} bytes. Nothing was written; "
            f"the CDN path still works.")
    head = payload.lstrip()[:14].lower()
    if "text/html" in content_type or head.startswith((b"<!doctype", b"<html")):
        raise AtompipeError(
            f"{url} answered with HTML, not JavaScript — that is a captive portal or a "
            f"proxy interception page, not three.js. Nothing was written; vendoring it "
            f"would have shadowed the working CDN copy with a login screen.")
    return payload


def cmd_site_status(args: argparse.Namespace) -> int:
    """What is built, how stale it is, and which locators point at nothing.

    Reads `state.json` and the filesystem; it never runs a viewgen and never
    writes. It does load the viewgen modules, with `strict=False`, because the
    question this command exists to answer is usually "why is there no assembly
    view in my site?" and the answer is usually a dependency that is named in
    the registration and missing on this machine.
    """
    root = _root(args)
    ledger = store.load(root)
    info = _site_state(root)
    view_registry, problems = _view_registry(root, ledger, strict=False)

    builtin = {row["id"] for row in info["views"]}
    viewgens = []
    for spec in view_registry.specs():
        ok, reason = site.availability(spec)
        viewgens.append({"id": spec.id, "kind": str(spec.kind), "pack": spec.pack,
                         "available": ok, "reason": "" if ok else reason,
                         "in_site": spec.id in builtin})

    if args.json:
        _dump({**info, "root": root, "viewgens": viewgens, "problems": problems,
               "has_dangling_locators": bool(info["locator_problems"])})
        return 0

    if not info["present"]:
        _say(f"no {site.SITE_DIR}/ directory — `atompipe site init`")
        return 0
    if info["error"]:
        _say(f"{_tag('FAIL')} {info['state']}: {info['error']}")
        return 0
    if not info["built"]:
        _say(f"{site.SITE_DIR}/ is scaffolded but never built — `atompipe site build`")
    else:
        _say(f"built {info['built_at'] or '(no timestamp)'}"
             f"{' (' + info['age'] + ')' if info['age'] else ''} — "
             f"{info.get('claims', 0)} claim(s), {info.get('verdicts', 0)} verdict(s), "
             f"{len(info['views'])} view(s)")
        for row in info["views"]:
            _say(f"  {row['id']:<20} {row['kind']:<9} {row['src'] or '(inline)'}")

    for row in viewgens:
        if row["in_site"]:
            continue
        # A viewgen that registered and is not in the built site: either the
        # site predates it, or its dependency is missing here. Both are worth a
        # line, because a view that is absent because trimesh is not installed
        # looks exactly like a view the project never had.
        why = row["reason"] or "registered, but produced nothing in the last build"
        _say(f"{_tag('warn')} viewgen {row['id']}: {why}")

    _say(f"{'STALE' if info['stale'] else 'current'}: {info['stale_reason']}")
    if info["verdicts_stale"]:
        _say(f"{_tag('warn')} the verdicts on the page are stale: "
             f"{info['verdicts_stale_reason']} — `atompipe check`, then rebuild")
    if info["locator_problems"]:
        _say(f"{_tag('warn')} {len(info['locator_problems'])} dangling locator(s):")
        for problem in info["locator_problems"][:6]:
            target = f"/{problem.get('target')}" if problem.get("target") else ""
            _say(f"    {problem.get('gate', '?')} -> {problem.get('view', '')}{target}: "
                 f"{problem.get('problem', '')}")
        if len(info["locator_problems"]) > 6:
            _say(f"    (+{len(info['locator_problems']) - 6} more — `--json` for all)")
    _say(f"assets: {info['assets']} file(s), {human_bytes(info['asset_bytes'])}; "
         f"three.js {site.THREE_VERSION} "
         f"{'vendored' if info['vendored'] else 'from the CDN (`atompipe site vendor`)'}")
    for problem in problems:
        _say(f"{_tag('FAIL')} {problem}")
    return 0


# --------------------------------------------------------------------------- #
# doctor
# --------------------------------------------------------------------------- #
def _first_sentence(text: str | None, limit: int = 100) -> str:
    """The first sentence of a pack's own note about a key, for a doctor row.

    A doctor row is one line and the whole point of this one is the CONTRAST
    between two definitions, which is carried by their first sentences. Cutting at
    a fixed character count instead put "Measured of" at the end of a line and made
    a reader distrust the rest of it.
    """
    flat = " ".join(str(text or "").split())
    if not flat:
        return "(the pack wrote no note for this key)"
    head = flat.split(". ", 1)[0].rstrip(".")
    return head if len(head) <= limit else head[:limit - 3].rstrip() + "..."


def _check(results: list[dict], name: str, status: str, detail: str) -> None:
    """Append one doctor row. `status` is one of ok / warn / FAIL."""
    results.append({"check": name, "status": status, "detail": detail})


def _ledger_problems(root: str, ledger: Ledger, registry: gates.Registry) -> list[str]:
    """Everything structurally wrong with this ledger, as sentences.

    Integrity here means "the records still refer to things that exist". Every
    one of these is a way the project can look fine and be wrong: a duplicate
    claim id means one of two claims is invisible to every lookup; a verdict for
    a gate nobody can find is a green tick with no instrument behind it; a
    missing artifact file is provenance that no longer resolves.

    `ledger` is `_resolved`'s view: its verdicts are the resolver's, so a gate
    that is not registered here shows up by its cache entries or its remembered
    outcome, not only by a row the ledger file happened to keep.
    """
    problems: list[str] = []

    def duplicates(values: Iterable[str], what: str) -> None:
        seen: set[str] = set()
        for value in values:
            if value in seen:
                problems.append(f"duplicate {what} {value!r} — one of them is unreachable")
            seen.add(value)

    duplicates([c.id for c in ledger.claims], "claim id")
    duplicates([p.name for p in ledger.params], "param name")
    duplicates([a.id for a in ledger.inputs], "artifact id")
    duplicates([n.id for n in ledger.needs], "need id")
    duplicates([d.id for d in ledger.decisions], "decision id")

    known_gates = set(registry.ids())
    orphaned = sorted({v.gate for v in ledger.verdicts
                       if not v.gate.endswith("#selftest") and v.gate not in known_gates})
    if orphaned and known_gates:
        # The resolver reads these stale — "gate not registered in this project"
        # — so they never count (tests:H2); they still reach the page and the
        # report as rows nothing here can re-run, which is a readiness problem,
        # not a tidiness one: a removed pack's passes, standing unexplained.
        problems.append(
            f"{len(orphaned)} verdict(s) from gates that are not registered here "
            f"({', '.join(orphaned[:4])}{'...' if len(orphaned) > 4 else ''}) — they read "
            f"stale and never count, and nothing here can re-run them; reinstall the "
            f"pack or remove their entries")
    for claim in ledger.claims:
        for gate_id in claim.gates or ():
            if known_gates and gate_id not in known_gates:
                problems.append(f"claim {claim.id} names gate {gate_id!r}, not registered here")
    for artifact in ledger.inputs:
        if artifact.path and not os.path.exists(os.path.join(root, artifact.path)):
            problems.append(f"artifact {artifact.id}: {artifact.path} is gone from disk")
    claim_ids = {c.id for c in ledger.claims}
    for need in ledger.needs:
        for cid in need.claim_ids or ():
            if cid not in claim_ids:
                problems.append(f"need {need.id} refers to claim {cid!r}, which does not exist")
    for record in store.load_runs(root, limit=50):
        if record.get("error"):
            problems.append(f"run {record.get('path')}: {record['error']}")
    return problems


def cmd_doctor(args: argparse.Namespace) -> int:
    """Everything that could be wrong with this environment, in one pass.

    This is the first thing anyone runs when confused, so it is diagnostic rather
    than decorative: every row names what was checked, what was found, and — when
    it is wrong — what to do. It survives every failure it reports (a broken
    pack, a model that will not import, a corrupt run file), because a doctor that
    dies on the first problem cannot tell you about the second.

    Exit 1 on any FAIL so it is usable in CI as an environment gate. Warnings do
    not fail: a solver that is not installed here is a real fact about the
    machine, and it is already visible as BLOCKED in the readiness report.
    """
    results: list[dict] = []

    version = ".".join(str(part) for part in sys.version_info[:3])
    _check(results, "python",
           "ok" if sys.version_info >= (3, 10) else "FAIL",
           f"{version} at {sys.executable} ({platform.system()}) — needs >= 3.10")
    _check(results, "spine", "ok",
           f"atompipe {__version__} from {os.path.dirname(os.path.abspath(__file__))}")

    # `require_root`'s own message, not a second spelling of it. What slipped
    # through (S-64): this line said "no .atompipe/ in <dir> or any parent" after
    # the marker became a FILE and the walk began stopping at `.git`, so doctor
    # told a user standing beside a bare `.atompipe/`, or in a worktree whose
    # trunk is a project, that the directory it could see was not there.
    try:
        root = store.require_root(getattr(args, "dir", None))
    except AtompipeError as exc:
        _check(results, "project", "FAIL", str(exc))
        return _doctor_finish(args, results)
    _check(results, "project", "ok", root)

    try:
        ledger = store.load(root)
    except AtompipeError as exc:
        _check(results, "ledger", "FAIL", str(exc))
        return _doctor_finish(args, results)
    _check(results, "ledger", "ok",
           f"{len(ledger.claims)} claims, {len(ledger.params)} params, "
           f"{len(ledger.inputs)} artifacts, {len(ledger.needs)} needs, "
           f"{len(ledger.decisions)} decisions, {len(ledger.verdicts)} verdicts")

    paths = store.project_paths(root)
    missing = [key for key in ("runs", "out", "inputs", "docs", "model")
               if not os.path.isdir(paths[key])]
    _check(results, "layout", "warn" if missing else "ok",
           f"missing: {', '.join(missing)} (recreated on demand)" if missing
           else "all well-known directories present")

    registry, pack_problems = _registry(root, ledger, strict=False)
    for problem in pack_problems:
        _check(results, "gate-loading", "FAIL", problem)

    installed = packs.installed(root, ledger=ledger)
    available = packs.available(root)
    unfound = [name for name in installed if not packs.find(name, root)]
    # Say WHERE the installed packs resolved from. A stale copy is indistinguishable
    # from a current one until something prints the path: a tester pulled a pack fix,
    # the new gate did not appear, and `doctor` reported the pack as present and fine
    # because the live copy was the one inside the installed wheel.
    origins: dict[str, list[str]] = {}
    for name in installed:
        directory = packs.find(name, root)
        if directory:
            origins.setdefault(packs.origin_of(directory, root), []).append(name)
    where = "; ".join(f"{origin}: {', '.join(sorted(names))}"
                      for origin, names in sorted(origins.items()))
    _check(results, "packs", "FAIL" if unfound else "ok",
           f"{len(installed)} installed ({', '.join(installed) or 'none'}), "
           f"{len(available)} discoverable"
           + (f" — from {where}" if where else "")
           + (f" — NOT FOUND: {', '.join(unfound)}" if unfound else ""))
    if origins.get("bundled"):
        _check(results, "pack-source", "ok",
               f"bundled packs load from {packs.BUNDLED_PACKS} — a checkout edited "
               f"elsewhere does not take effect until it is reinstalled")
    if not pack_problems and not available:
        _check(results, "pack-search", "warn",
               "no packs on any search path: "
               + ", ".join(packs.search_paths(root, existing_only=False)))

    summary = gates.registry_summary(registry)
    uncontrolled = summary["without_negative_control"]
    gate_status = "FAIL" if uncontrolled else ("warn" if not summary["gates"] else "ok")
    _check(results, "gates", gate_status,
           f"{summary['gates']} registered, {summary['tier0']} at tier 0, "
           f"{summary['available']} runnable here"
           + (f" — NO NEGATIVE CONTROL: {', '.join(uncontrolled)}" if uncontrolled
              else "" if summary["gates"] else " — nothing can be checked yet"))
    for row in summary["unavailable"]:
        _check(results, "gate-tool", "warn", f"{row['gate']}: {row['reason']}")
    for tool in summary["requires_tools"]:
        found = shutil.which(tool)
        _check(results, "tool", "ok" if found else "warn",
               f"{tool}: {found or 'not on PATH — gates needing it will SKIP, not fail'}")

    model = None
    projection = None
    model_error = ""
    if not (ledger.meta.model_entry or "").strip():
        _check(results, "model", "warn",
               "no model entry recorded — `atompipe model --set-entry model/<thing>.py`")
    else:
        try:
            model, projection = _projection(root, ledger)
        except AtompipeError as exc:
            model_error = str(exc)
            _check(results, "model", "FAIL", str(exc).replace("\n", " "))
        else:
            _check(results, "model", "ok",
                   f"{model.entry} loads, hash {modelio.model_hash(projection)}, "
                   f"{len(model.params)} params")

    if model is not None:
        try:
            deterministic, detail = modelio.check_determinism(model, 2)
        except AtompipeError as exc:
            _check(results, "model-determinism", "FAIL", str(exc).replace("\n", " "))
        else:
            _check(results, "model-determinism", "ok" if deterministic else "FAIL", detail)
        undocumented = modelio.undocumented_params(model)
        _check(results, "model-provenance", "warn" if undocumented else "ok",
               f"{len(undocumented)} param(s) with no rationale: "
               f"{', '.join(undocumented[:6])}" if undocumented
               else "every param carries a rationale")
        # Checked against a COPY: doctor never writes, and a diagnostic that
        # silently repaired what it was diagnosing would hide the problem from
        # the next run.
        declared = {param.name for param in (model.params or [])}
        orphans = [param.name for param in ledger.params if param.name not in declared]
        _check(results, "model-params", "warn" if orphans else "ok",
               f"{len(orphans)} ledger param(s) the model no longer declares: "
               f"{', '.join(orphans[:6])} — renamed, or removed and still grounded"
               if orphans else f"{len(declared)} param(s), all still in the model")

    # Two packs, one word, two meanings. The spine knows which packs are installed
    # and what each of them reads, so a collision between their key vocabularies is
    # something it can DIFF rather than something a user discovers from a verdict
    # about the wrong object. The case this exists for: `cad-solid` reads `bbox_mm`
    # as the assembly envelope and `fdm-print` reads it as one part in print
    # orientation, so a project with both — every mechanical project — got a 480 mm
    # boat measured against a 220 mm printer bed, or an envelope claim that
    # silently went unheld. A warning, not a failure: the collision is latent until
    # the project publishes the bare key, and `live` says when it has.
    flat_keys, _conflicts = modelio.flat_params(projection)
    for collision in packs.key_collisions(installed, root, projection_keys=flat_keys):
        meanings = "; ".join(
            f"{name}: {_first_sentence(collision.meanings.get(name))}"
            for name in collision.packs)
        _check(results, "pack-keys", "warn",
               f"{collision.key!r} is read by {' and '.join(collision.packs)} with "
               f"different meanings"
               + (" AND THIS PROJECT PUBLISHES IT" if collision.live else
                  " (this project does not publish it yet)")
               + f" — {collision.fix()}. {meanings}")

    # No global staleness row: one hash of the projection against the last
    # sweep's said THAT something moved, never which check it touched, and a
    # model that did not load compared equal (S-21). Which gates are current is
    # `status`'s `stale:` block now, per gate, from the resolver.
    view, _resolution = _resolved(root, ledger, registry, projection, model_error,
                                  now=utcnow_iso(), model=model)
    problems = _ledger_problems(root, view, registry)
    _check(results, "ledger-integrity", "FAIL" if problems else "ok",
           "; ".join(problems[:4]) + (f" (+{len(problems) - 4} more)" if len(problems) > 4 else "")
           if problems else "records all resolve")

    site_info = _site_state(root)
    if site_info["present"]:
        # Only when there is a site. A doctor row about a surface the project
        # never opted into is a row that is always there and never actionable,
        # and the ones that are actionable get read less for it.
        if site_info["error"]:
            _check(results, "site", "FAIL",
                   f"{site_info['state']}: {site_info['error']} — "
                   f"`atompipe site build` rewrites it")
        elif not site_info["built"]:
            _check(results, "site", "warn",
                   "scaffolded but never built — `atompipe site build`")
        else:
            dangling = len(site_info["locator_problems"])
            # Dangling locators are a WARNING, not a failure: the verdicts and
            # claims on the page are still true, and the overlay is the part
            # that is wrong. But never silent — a gate that believes it is
            # drawing and is not looks exactly like a gate that found nothing.
            status = "warn" if (site_info["stale"] or dangling) else "ok"
            detail = (f"{len(site_info['views'])} view(s), built {site_info['built_at']}"
                      f"{' (' + site_info['age'] + ')' if site_info['age'] else ''}, "
                      f"{site_info['stale_reason']}")
            if dangling:
                detail += (f" — {dangling} locator(s) point at a view or node that does "
                           f"not exist (`atompipe site status`)")
            _check(results, "site", status, detail)

    lock = _lock(root)
    age = lock.age()
    if age is not None:
        holder = lock.holder()
        _check(results, "lock", "warn",
               f"{LOCK_NAME} held by pid {holder.get('pid', '?')} for "
               f"{human_duration(age)} — another run is in progress, or crashed")

    return _doctor_finish(args, results)


def _doctor_finish(args: argparse.Namespace, results: list[dict]) -> int:
    """Render the doctor rows and return the exit code."""
    failures = [row for row in results if row["status"] == "FAIL"]
    if args.json:
        _dump({"checks": results, "ok": not failures,
               "failures": [row["check"] for row in failures]})
        return 1 if failures else 0
    for row in results:
        _say(f"{_tag(row['status'])} {row['check']:<18} {row['detail']}")
    warnings = [row for row in results if row["status"] == "warn"]
    _say(f"{len(results)} checks — {len(failures)} failing, {len(warnings)} warning(s)")
    return 1 if failures else 0


# --------------------------------------------------------------------------- #
# argument parsing
# --------------------------------------------------------------------------- #
def _common() -> argparse.ArgumentParser:
    """Flags every subcommand carries: `--json` and a repeat of `-C/--dir`.

    `-C` is repeated on the subparsers so both `atompipe -C proj check` and
    `atompipe check -C proj` work — people type both. `default=SUPPRESS` is what
    makes that safe: without it the subparser would write its own `None` over the
    value the top-level parser already stored, and `atompipe -C proj check` would
    silently run against the current directory instead.
    """
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("-C", "--dir", metavar="DIR", default=argparse.SUPPRESS,
                        help="run against this project directory instead of the cwd")
    common.add_argument("--json", action="store_true",
                        help="machine-readable output; nothing else goes to stdout")
    return common


def _junit_flag(parser: argparse.ArgumentParser, what: str) -> None:
    """`--junit [PATH]`, the same on `check` and `gate selftest`.

    `nargs="?"` with the default as `const`, so a bare `--junit` writes
    `report.JUNIT_DEFAULT` under the project. A PATH must end in `.xml`
    (`_junit_arg`), which is what stops the optional value swallowing a gate id.
    """
    parser.add_argument("--junit", nargs="?", const=_JUNIT_DEFAULT, default=None,
                        metavar="PATH",
                        help=f"also write {what} as JUnit XML (default "
                             f"{report.JUNIT_DEFAULT}; a PATH must end in .xml)")


def build_parser() -> argparse.ArgumentParser:
    """The whole command surface.

    Every help string is written for someone who has not read the docs, because
    `--help` is where most people meet this tool. Where a command's *point* is
    non-obvious — `ask`, `gap`, `gate selftest` — the help says what it is for
    rather than what it does.
    """
    common = _common()
    parser = argparse.ArgumentParser(
        prog="atompipe",
        description="Claims, gates, packs and an honest readiness report.",
        epilog="`atompipe doctor` is the first thing to run when something is confusing.",
    )
    parser.add_argument("--version", action="version", version=f"atompipe {__version__}")
    parser.add_argument("-C", "--dir", metavar="DIR", default=None,
                        help="run against this project directory instead of the cwd")
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    # -- init ------------------------------------------------------------- #
    p = sub.add_parser("init", parents=[common], help="create the project layout")
    p.add_argument("--name", default="", help="project name (default: directory name)")
    p.add_argument("--summary", default="", help="one line: what is being built")
    p.add_argument("--revision", default="v0.1")
    p.add_argument("--model", default="", help="model entry, e.g. model/bracket.py")
    p.add_argument("--pack", action="append", default=[], help="pack to install (repeatable)")
    p.set_defaults(func=cmd_init)

    # -- status ----------------------------------------------------------- #
    p = sub.add_parser("status", parents=[common],
                       help="where this project is, in one screen")
    p.set_defaults(func=cmd_status)

    # -- check ------------------------------------------------------------ #
    p = sub.add_parser("check", parents=[common],
                       help="run the gates; exits 1 while anything critical is unproven")
    p.add_argument("--tier", type=int, default=0,
                   help="cost ceiling: 0 instant (default), 1 build, 2 solve, 3 external")
    p.add_argument("--only", action="append", metavar="GATE",
                   help="gate id, pack name or glob (repeatable); runs it above its tier too")
    p.add_argument("--force", action="store_true",
                   help="re-run every selected gate and its control, ignoring the "
                        "verdict cache (what CI runs: a cache is re-proven, not trusted)")
    p.add_argument("--no-record", action="store_true",
                   help="a dry sweep: write nothing under .atompipe/ except gate scratch "
                        "in out/ — no cache or control entry, obs, last_check.json or "
                        "ledger")
    _junit_flag(p, "this run")
    p.set_defaults(func=cmd_check)

    # -- ask -------------------------------------------------------------- #
    p = sub.add_parser("ask", parents=[common],
                       help="what evidence to request from the human, in priority order")
    p.add_argument("--kind", choices=[k.value for k in ArtifactKind],
                   help="all prompts for one artifact kind instead of the priority list")
    p.add_argument("--about", default="",
                   help="what the project is, to bias the ordering (default: meta.summary)")
    p.add_argument("--limit", type=int, default=6,
                   help="how many to ask for at once (default 6: what a person answers)")
    p.set_defaults(func=cmd_ask)

    # -- ingest / inputs / extract ---------------------------------------- #
    p = sub.add_parser("ingest", parents=[common],
                       help="take files (or URLs) into the project as evidence")
    p.add_argument("paths", nargs="+")
    p.add_argument("--kind", choices=[k.value for k in ArtifactKind],
                   help="override the guess made from extension and directory")
    p.add_argument("--desc", default="", help="what it shows — worth the sentence")
    p.add_argument("--licence", default="", help="provenance: ingested media gets redistributed")
    p.add_argument("--note", default="")
    p.add_argument("--no-copy", action="store_true",
                   help="register in place instead of copying into inputs/")
    p.set_defaults(func=cmd_ingest)

    p = sub.add_parser("inputs", parents=[common], help="list ingested evidence")
    p.add_argument("--unextracted", action="store_true",
                   help="only the artifacts nobody has read anything out of")
    p.add_argument("--kind", choices=[k.value for k in ArtifactKind])
    p.set_defaults(func=cmd_inputs)

    p = sub.add_parser("extract", parents=[common],
                       help="record what was read out of an artifact, and what it grounds")
    p.add_argument("artifact", help="artifact id from `atompipe inputs`")
    p.add_argument("--what", required=True, help='e.g. "hull beam at midship reads 148 mm"')
    p.add_argument("--grounds", action="append", default=[],
                   help="param names / claim ids this supports (comma-separated or repeated)")
    p.add_argument("--confidence", default="stated",
                   choices=["measured", "scaled", "stated", "inferred"])
    p.add_argument("--note", default="")
    p.set_defaults(func=cmd_extract)

    # -- claim ------------------------------------------------------------ #
    claim = sub.add_parser("claim", help="add, list, show, edit and settle claims")
    claim_sub = claim.add_subparsers(dest="claim_command", metavar="<sub>")
    claim.set_defaults(func=lambda args: _needs_subcommand(claim))

    p = claim_sub.add_parser("add", parents=[common], help="add a claim")
    p.add_argument("--statement", required=True, help="what must be true for this to work")
    p.add_argument("--kind", default=ClaimKind.MEASURABLE.value,
                   choices=[k.value for k in ClaimKind])
    p.add_argument("--id", default="", help="explicit id (default: next free C<n>)")
    p.add_argument("--prefix", default="C", help="id prefix when generating (default C)")
    p.add_argument("--quantity", default=None, help='what is measured, e.g. "tip deflection"')
    p.add_argument("--cmp", default=None, choices=[c.value for c in Comparator],
                   help="comparator for the threshold")
    p.add_argument("--limit", type=float, default=None)
    p.add_argument("--limit-hi", type=float, default=None, help="upper bound for `between`")
    p.add_argument("--units", default=None)
    p.add_argument("--rationale", default="", help="why this matters; what breaks if false")
    p.add_argument("--source", default="")
    p.add_argument("--tags", action="append", default=[],
                   help="tags packs bind gates to (comma-separated or repeated)")
    p.add_argument("--gates", action="append", default=[], help="gate ids that cover it")
    p.add_argument("--grounds", action="append", default=[], help="artifact ids behind it")
    p.add_argument("--note", default="")
    p.add_argument("--nice-to-have", action="store_true",
                   help="not critical: never blocks a spend")
    p.set_defaults(func=cmd_claim_add)

    p = claim_sub.add_parser("list", parents=[common], help="one line per claim")
    p.add_argument("--status", choices=[s.value for s in ClaimStatus])
    p.add_argument("--kind", choices=[k.value for k in ClaimKind])
    p.add_argument("--tag", default="")
    p.set_defaults(func=cmd_claim_list)

    p = claim_sub.add_parser("show", parents=[common], help="one claim's whole history")
    p.add_argument("id")
    p.set_defaults(func=cmd_claim_show)

    p = claim_sub.add_parser("edit", parents=[common],
                             help="change a claim; unmentioned fields are untouched")
    p.add_argument("id")
    p.add_argument("--statement", default=None)
    p.add_argument("--kind", default=None, choices=[k.value for k in ClaimKind])
    p.add_argument("--quantity", default=None)
    p.add_argument("--cmp", default=None, choices=[c.value for c in Comparator])
    p.add_argument("--limit", type=float, default=None)
    p.add_argument("--limit-hi", type=float, default=None)
    p.add_argument("--units", default=None)
    p.add_argument("--rationale", default=None)
    p.add_argument("--source", default=None)
    p.add_argument("--note", default=None)
    p.add_argument("--tags", action="append", default=None)
    p.add_argument("--gates", action="append", default=None)
    p.add_argument("--grounds", action="append", default=None)
    p.add_argument("--critical", action="store_true", help="mark critical (blocks a spend)")
    p.add_argument("--nice-to-have", action="store_true", help="mark non-critical")
    p.set_defaults(func=cmd_claim_edit)

    p = claim_sub.add_parser("physical", parents=[common],
                             help="record a real-world result on a physical claim")
    p.add_argument("id")
    p.add_argument("result", nargs="?", choices=["pass", "fail"],
                   help="what happened in the real world")
    outcome = p.add_mutually_exclusive_group()
    outcome.add_argument("--pass", dest="passed", action="store_const", const=True,
                         help="same as the positional `pass` (what the report prints)")
    outcome.add_argument("--fail", dest="passed", action="store_const", const=False,
                         help="same as the positional `fail`")
    p.set_defaults(passed=None)
    p.add_argument("--when", default="", help="ISO date (default: now)")
    p.add_argument("--who", default="")
    p.add_argument("--detail", default="", help="what was actually observed")
    p.add_argument("--evidence", action="append", default=[], help="photo / log paths")
    p.set_defaults(func=cmd_claim_physical)

    # -- gap -------------------------------------------------------------- #
    p = sub.add_parser("gap", parents=[common],
                       help="measurable claims no gate covers — how the system grows")
    p.add_argument("--propose", action="store_true",
                   help="also list installed packs whose vocabulary matches")
    p.set_defaults(func=cmd_gap)

    # -- gate ------------------------------------------------------------- #
    gate = sub.add_parser("gate", help="list, inspect and falsify the gates")
    gate_sub = gate.add_subparsers(dest="gate_command", metavar="<sub>")
    gate.set_defaults(func=lambda args: _needs_subcommand(gate))

    p = gate_sub.add_parser("list", parents=[common], help="every registered gate")
    p.add_argument("--tier", type=int, default=None, help="only gates at or below this tier")
    p.add_argument("--full", action="store_true",
                   help="--json: include every gate's description (~4x the output)")
    p.set_defaults(func=cmd_gate_list)

    p = gate_sub.add_parser("show", parents=[common], help="one gate in full")
    p.add_argument("id")
    p.set_defaults(func=cmd_gate_show)

    p = gate_sub.add_parser("selftest", parents=[common],
                            help="run every negative control; fails any gate that cannot fail")
    # The positional form exists because `gates.py` prints `atompipe gate
    # selftest <id>` when it refuses a control-less gate, and a command the tool
    # tells you to run has to work as printed.
    p.add_argument("gates", nargs="*", metavar="GATE",
                   help="gate ids, pack names or globs; default is every control")
    p.add_argument("--only", action="append", metavar="GATE",
                   help="same as the positional form (repeatable)")
    p.add_argument("--tier", type=int, default=None,
                   help="cap the cost; the default runs every tier's control")
    p.add_argument("--no-record", action="store_true",
                   help="run every control but file nothing: no control entry, obs or "
                        "cache under .atompipe/")
    p.add_argument("--pack", action="append", metavar="NAME|DIR",
                   help="pack mode: demonstrate this pack, or every pack in this "
                        "directory (repeatable). Without a project, pack mode runs "
                        "every bundled pack")
    p.add_argument("--user-packs", action="store_true",
                   help="pack mode: also search $ATOMPIPE_PACK_PATH and "
                        "~/.atompipe/packs (off, so the machine cannot choose the pack)")
    p.add_argument("--allow-empty", action="store_true",
                   help="exit 0 when no control ran (otherwise that is a failure)")
    _junit_flag(p, "the controls (and, in pack mode, the baselines)")
    p.set_defaults(func=cmd_gate_selftest)

    # -- report / why / decide -------------------------------------------- #
    p = sub.add_parser("report", parents=[common], help="the readiness report")
    p.add_argument("--write", action="store_true", help="write docs/readiness.md")
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("why", parents=[common],
                       help="one param or claim's full history, instead of the whole log")
    p.add_argument("name")
    p.set_defaults(func=cmd_why)

    p = sub.add_parser("decide", parents=[common],
                       help="record a decision, including what LOST and why")
    p.add_argument("--title", required=True)
    p.add_argument("--summary", required=True)
    p.add_argument("--when", default="", help="ISO timestamp (default: now)")
    p.add_argument("--rejected", action="append", default=[], metavar="VALUE|WHY",
                   help='what lost and the concrete reason: "0.5 mm|the router could not close"')
    p.add_argument("--param", action="append", default=[], help="param names this moved")
    p.add_argument("--claim", action="append", default=[], help="claim ids this moved")
    p.add_argument("--evidence", action="append", default=[])
    p.add_argument("--body", default="", help="long-form markdown")
    p.set_defaults(func=cmd_decide)

    # -- packs ------------------------------------------------------------ #
    # `pack` is an alias because the extension protocol and `packs.py`'s own
    # messages say `atompipe pack validate`; a documented command must run.
    pack = sub.add_parser("packs", aliases=["pack"],
                          help="discover, read, validate and install packs")
    pack_sub = pack.add_subparsers(dest="packs_command", metavar="<sub>")
    pack.set_defaults(func=cmd_packs_list, json=False, verbose=False)

    p = pack_sub.add_parser("list", parents=[common], help="every discoverable pack")
    p.add_argument("-v", "--verbose", action="store_true", help="also show directory and settles")
    p.set_defaults(func=cmd_packs_list)

    p = pack_sub.add_parser("show", parents=[common], help="a pack's PACK.md (tier 2)")
    p.add_argument("name")
    p.add_argument("--ref", default="", help="a tier-3 reference instead of PACK.md")
    p.set_defaults(func=cmd_packs_show)

    p = pack_sub.add_parser("validate", parents=[common],
                            help="the checks CI runs; exit 1 on any problem")
    p.add_argument("name", help="pack name or directory")
    p.set_defaults(func=cmd_packs_validate)

    p = pack_sub.add_parser("add", parents=[common], help="opt this project into a pack")
    p.add_argument("names", nargs="+")
    p.set_defaults(func=cmd_packs_add)

    p = pack_sub.add_parser("remove", parents=[common], help="drop a pack from this project")
    p.add_argument("names", nargs="+")
    p.set_defaults(func=cmd_packs_remove)

    # -- model / doctor --------------------------------------------------- #
    p = sub.add_parser("model", parents=[common], help="the model, projected")
    p.add_argument("--write", action="store_true", help="write .atompipe/model.json")
    p.add_argument("--entry", default=None, help="project this file instead of the recorded one")
    p.add_argument("--set-entry", default="", metavar="PATH",
                   help="record this file as the project's model")
    p.set_defaults(func=cmd_model)

    # -- site ------------------------------------------------------------- #
    # The site has two jobs and the second one is why it is worth building: it
    # explains the project to someone who did not build it, AND it is a
    # debugging tool — spin the thing, pull it apart, and see the latest gate
    # results anchored to the geometry they are about.
    site_p = sub.add_parser("site", help="build, serve and inspect the project site")
    site_sub = site_p.add_subparsers(dest="site_command", metavar="<sub>")
    site_p.set_defaults(func=lambda args: _needs_subcommand(site_p))

    p = site_sub.add_parser("init", parents=[common],
                            help="scaffold site/ (refuses to clobber index.html)")
    p.add_argument("--force", action="store_true",
                   help="replace index.html too — your edits to it are not recoverable")
    p.set_defaults(func=cmd_site_init)

    p = site_sub.add_parser("build", parents=[common],
                            help="run the viewgens and write data/ and assets/; runs no gates")
    p.set_defaults(func=cmd_site_build)

    p = site_sub.add_parser("serve", parents=[common],
                            help="serve site/ from the standard library, on loopback")
    p.add_argument("-p", "--port", type=int, default=SERVE_PORT,
                   help=f"port to listen on (default {SERVE_PORT}; 0 picks a free one)")
    p.add_argument("--host", default=SERVE_HOST,
                   help=f"bind address (default {SERVE_HOST} — loopback on purpose)")
    p.add_argument("--no-browser", action="store_true",
                   help="do not open a browser window")
    p.set_defaults(func=cmd_site_serve)

    p = site_sub.add_parser("vendor", parents=[common],
                            help="pull three.js into site/vendor/ so the site works offline")
    p.set_defaults(func=cmd_site_vendor)

    p = site_sub.add_parser("status", parents=[common],
                            help="what is built, how stale it is, what locators dangle")
    p.set_defaults(func=cmd_site_status)

    p = sub.add_parser("doctor", parents=[common],
                       help="environment, model, packs, gates and ledger integrity")
    p.set_defaults(func=cmd_doctor)

    _tag_subparsers(parser)
    return parser


def _tag_subparsers(parser: argparse.ArgumentParser) -> None:
    """Give every subparser a `_parser` default pointing at itself.

    So that `main` can hand a bad flag back to the parser the user was actually
    using. `atompipe check --tierr 1` printed the two-line TOP-LEVEL usage —
    `usage: atompipe [-h] [--version] [-C DIR] <command> ...` — which lists the
    subcommands and not one of `check`'s own flags, so the reader learns nothing
    about the flag they got wrong and has to go and type `--help` separately.

    argparse fills defaults from the innermost parser last (each subparser parses
    into a fresh namespace that is then copied outward), so `args._parser` ends
    up as the deepest one that matched: `claim add`, not `claim`.

    `_actions` is private, but the alternative is repeating `set_defaults` on
    thirty subparsers, where the thirty-first would be added without it and
    nobody would notice until someone typoed a flag on exactly that command.
    Detection is by duck type — a `choices` that is a dict is a subparsers action
    — so it does not name a private argparse CLASS as well as a private field.
    """
    for action in parser._actions:
        choices = getattr(action, "choices", None)
        if not isinstance(choices, dict):
            continue
        for child in choices.values():
            child.set_defaults(_parser=child)
            _tag_subparsers(child)


def _needs_subcommand(parser: argparse.ArgumentParser) -> int:
    """`atompipe claim` with no subcommand: print its help, exit 2.

    Exit 2 rather than 0 because an incomplete command line is a user error, and
    a script that typoed its way here must not read the help text as success.
    """
    parser.print_help(sys.stderr)
    return 2


# --------------------------------------------------------------------------- #
# entry point
# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    """Parse, dispatch, and turn exceptions into exit codes.

    `AtompipeError` is the user's problem and prints as one sentence with no
    traceback; everything else is the spine's problem and keeps its traceback,
    because a bug that prints like a user error gets reported as a user error and
    never gets fixed.

    `BrokenPipeError` is handled rather than raised: `atompipe gate list | head`
    is a normal thing to type, and Python's default behaviour there is a
    confusing "Exception ignored" block at interpreter shutdown. stdout is
    redirected to devnull so the flush at exit has somewhere harmless to go.
    """
    parser = build_parser()
    try:
        # `parse_known_args`, then complain via the subparser the user reached.
        # `parse_args` reports an unrecognized flag from the TOP-LEVEL parser, so
        # `atompipe check --tierr 1` printed the list of subcommands instead of
        # the list of `check`'s flags — the one thing the reader needed.
        args, unknown = parser.parse_known_args(
            list(sys.argv[1:] if argv is None else argv))
        if unknown:
            chosen = getattr(args, "_parser", None) or parser
            chosen.error(f"unrecognized arguments: {' '.join(unknown)}")
    except SystemExit as exc:                # --help, --version, or a bad flag
        return int(exc.code or 0)

    handler: Callable[[argparse.Namespace], int] | None = getattr(args, "func", None)
    if handler is None:
        parser.print_help(sys.stderr)
        return 2

    try:
        return int(handler(args) or 0)
    except AtompipeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except BrokenPipeError:
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, sys.stdout.fileno())
        return 141
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":              # pragma: no cover - `python -m atompipe.cli`
    raise SystemExit(main())
